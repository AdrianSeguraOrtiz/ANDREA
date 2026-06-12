"""SQLite store for scalable network-comparison exploration."""

from __future__ import annotations

import math
import sqlite3
import heapq
import time
from collections import OrderedDict, defaultdict
from copy import deepcopy
from itertools import islice
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from andrea.core.commands.compare_networks.distances import classical_mds_coordinates
from andrea.core.commands.compare_networks.models import (
    COMPARISON_LEVELS,
    COORDINATE_COLUMNS,
    DISTANCE_COLUMNS,
    DISTANCE_METRICS,
    EDGE_SCORES_COLUMNS,
    EVALUATION_METRIC_COLUMNS,
    NETWORK_INDEX_COLUMNS,
)
from andrea.core.commands.compare_networks.utils import format_float
from andrea.core.shared.network_context import network_context_family

SQLITE_TABLE_COLUMNS = {
    "network_index": NETWORK_INDEX_COLUMNS,
    "edge_scores": [
        "network_pk",
        "edge_key",
        "source",
        "target",
        "sign",
        "score",
    ],
    "distances": DISTANCE_COLUMNS,
    "distance_coordinates": COORDINATE_COLUMNS,
    "evaluation_metrics": [
        "source_id",
        "tool_id",
        "context",
        "level",
        "status",
        "n_truth_edges",
        *EVALUATION_METRIC_COLUMNS,
    ],
    "context_summary": [
        "context",
        "context_family",
        "network_instances",
    ],
}

ANCHOR_LAMBDA = 0.15
ANCHORED_MDS_ITERATIONS = 120
ANCHORED_MDS_INITIAL_STEP = 0.18
ELLIPSE_KEEP_RATIO = 0.95
ELLIPSE_95_SCALE = math.sqrt(5.991464547107979)
ELLIPSE_MIN_AXIS = 1e-9
MAX_SELECTED_CONTEXTS = 5
DISTANCE_VIEW_CACHE_VERSION = "distance-view-v2"
DISTANCE_VIEW_CACHE_SIZE = 24
_DISTANCE_VIEW_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()


def write_comparison_store(
    path: Path,
    *,
    network_index: list[dict[str, Any]],
    edge_scores: Iterable[dict[str, Any]],
    distances: list[dict[str, Any]],
    distance_coordinates: list[dict[str, Any]],
    evaluation_metrics: list[dict[str, Any]],
) -> None:
    """Write the query store used by the compare-networks GUI."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        _create_tables(conn)
        _insert_rows(conn, "network_index", network_index)
        _insert_edge_score_rows(conn, edge_scores)
        _insert_rows(conn, "distances", distances)
        _insert_rows(conn, "distance_coordinates", distance_coordinates)
        _insert_rows(conn, "evaluation_metrics", evaluation_metrics)
        _insert_rows(conn, "context_summary", _context_summary_rows(network_index))
        _create_indices(conn)
        conn.commit()


def export_edge_scores_csv_from_sqlite(*, sqlite_path: Path, output_path: Path) -> int:
    """Reconstruct the public edge_scores.csv artifact from comparison.sqlite."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    count = 0
    import csv

    try:
        with _connect(sqlite_path) as conn, tmp_path.open(
            "w", encoding="utf-8", newline=""
        ) as fh:
            writer = csv.DictWriter(fh, fieldnames=EDGE_SCORES_COLUMNS)
            writer.writeheader()
            for row in conn.execute(
                """
                SELECT
                    ni.network_id,
                    ni.source_id,
                    ni.run_id,
                    ni.tool_id,
                    ni.catalog_tool_id,
                    ni.context,
                    ni.level,
                    es.edge_key,
                    es.source,
                    es.target,
                    es.sign,
                    es.score
                FROM edge_scores es
                JOIN network_index ni ON ni.network_pk = es.network_pk
                ORDER BY ni.network_pk, es.edge_key
                """
            ):
                item = dict(row)
                item["sign"] = item.get("sign") or ""
                item["score"] = format_float(float(item.get("score") or 0.0))
                writer.writerow(
                    {column: item.get(column, "") for column in EDGE_SCORES_COLUMNS}
                )
                count += 1
        tmp_path.replace(output_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return count


def list_contexts(
    path: Path,
    *,
    source_id: str,
    family: str,
    query: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    family = _normalize_family(family)
    safe_limit = max(1, min(int(limit or 100), 1000))
    terms: list[Any] = [source_id, family]
    where = [
        "ni.source_id = ?",
        "cs.context_family = ?",
    ]
    if query:
        where.append("ni.context LIKE ?")
        terms.append(f"%{query}%")
    sql = f"""
        SELECT ni.context, COUNT(*) AS network_instances
        FROM network_index ni
        JOIN context_summary cs ON cs.context = ni.context
        WHERE {' AND '.join(where)}
        GROUP BY ni.context
        ORDER BY ni.context
        LIMIT ?
    """
    terms.append(safe_limit)
    with _connect(path) as conn:
        rows = [dict(row) for row in conn.execute(sql, terms)]
        total = conn.execute(
            """
            SELECT COUNT(DISTINCT ni.context)
            FROM network_index ni
            JOIN context_summary cs ON cs.context = ni.context
            WHERE ni.source_id = ? AND cs.context_family = ?
            """,
            (source_id, family),
        ).fetchone()[0]
    rows.sort(key=lambda row: _context_sort_key(str(row["context"])))
    return {
        "source_id": source_id,
        "family": family,
        "query": query,
        "limit": safe_limit,
        "total": int(total),
        "contexts": rows,
    }


def distance_view(
    path: Path,
    *,
    source_id: str,
    context_family: str,
    distance_metric: str,
    evaluation_metric: str | None = None,
    contexts: list[str] | None = None,
) -> dict[str, Any]:
    family = _normalize_family(context_family)
    metric = str(distance_metric or "").strip()
    if metric not in DISTANCE_METRICS:
        raise ValueError(f"distance_metric must be one of {', '.join(DISTANCE_METRICS)}")
    eval_metric = _normalize_evaluation_metric(evaluation_metric)
    selected_context_key = tuple(
        part.strip()
        for item in contexts or []
        for part in str(item).split(",")
        if part.strip()
    )
    cache_key = _distance_view_cache_key(
        path=path,
        source_id=source_id,
        family=family,
        distance_metric=metric,
        evaluation_metric=eval_metric,
        selected_contexts=selected_context_key,
    )
    cached = _distance_view_cache_get(cache_key)
    if cached is not None:
        cached["query_profile"] = {
            **dict(cached.get("query_profile") or {}),
            "cache_hit": True,
            "elapsed_s": 0.0,
        }
        return cached
    started = time.perf_counter()
    with _connect(path) as conn:
        available_contexts = _contexts_for_family(conn, source_id=source_id, family=family)
        if not available_contexts:
            raise ValueError(
                f"No contexts found for source_id={source_id!r}, context_family={family!r}"
            )
        selected_contexts = _normalize_selected_contexts(
            available_contexts=available_contexts,
            selected_contexts=contexts,
        )
        levels = [
            _distance_level_view(
                conn,
                source_id=source_id,
                family=family,
                contexts=available_contexts,
                level=level,
                distance_metric=metric,
                evaluation_metric=eval_metric,
                selected_contexts=selected_contexts,
            )
            for level in COMPARISON_LEVELS
        ]
    payload = {
        "source_id": source_id,
        "context_family": family,
        "distance_metric": metric,
        "evaluation_metric": eval_metric,
        "selected_contexts": selected_contexts,
        "context_count": len(available_contexts),
        "levels": levels,
        "query_profile": {
            "cache_hit": False,
            "elapsed_s": round(max(0.0, time.perf_counter() - started), 6),
            "level_count": len(levels),
            "selected_context_count": len(selected_contexts),
        },
    }
    _distance_view_cache_put(cache_key, payload)
    return deepcopy(payload)


def _distance_view_cache_key(
    *,
    path: Path,
    source_id: str,
    family: str,
    distance_metric: str,
    evaluation_metric: str | None,
    selected_contexts: tuple[str, ...],
) -> tuple[Any, ...]:
    stat = path.stat()
    return (
        DISTANCE_VIEW_CACHE_VERSION,
        str(path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        source_id,
        family,
        distance_metric,
        evaluation_metric or "",
        selected_contexts,
    )


def _distance_view_cache_get(cache_key: tuple[Any, ...]) -> dict[str, Any] | None:
    cached = _DISTANCE_VIEW_CACHE.get(cache_key)
    if cached is None:
        return None
    _DISTANCE_VIEW_CACHE.move_to_end(cache_key)
    return deepcopy(cached)


def _distance_view_cache_put(cache_key: tuple[Any, ...], payload: dict[str, Any]) -> None:
    _DISTANCE_VIEW_CACHE[cache_key] = deepcopy(payload)
    _DISTANCE_VIEW_CACHE.move_to_end(cache_key)
    while len(_DISTANCE_VIEW_CACHE) > DISTANCE_VIEW_CACHE_SIZE:
        _DISTANCE_VIEW_CACHE.popitem(last=False)


def _distance_level_view(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    family: str,
    contexts: list[str],
    level: str,
    distance_metric: str,
    evaluation_metric: str | None,
    selected_contexts: list[str],
) -> dict[str, Any]:
    tools = _tools_for_level(conn, source_id=source_id, family=family, level=level)
    aggregate_rows = _aggregate_distance_rows(
        conn,
        source_id=source_id,
        family=family,
        contexts=contexts,
        level=level,
        distance_metric=distance_metric,
        tools=tools,
    )
    aggregate_coordinates, coordinate_warning = _aggregate_coordinates(
        tools=tools,
        aggregate_rows=aggregate_rows,
    )
    anchored_by_context, context_diagnostics = _anchored_context_coordinates(
        conn,
        source_id=source_id,
        family=family,
        contexts=contexts,
        level=level,
        distance_metric=distance_metric,
        aggregate_coordinates=aggregate_coordinates,
    )
    selected = None
    if selected_contexts:
        selected_metric_values = (
            _evaluation_metric_value_lookup(
                conn,
                source_id=source_id,
                contexts=selected_contexts,
                level=level,
                evaluation_metric=evaluation_metric,
            )
            if evaluation_metric
            else {}
        )
        selected_coordinates = []
        for idx, context in enumerate(selected_contexts):
            for row in anchored_by_context.get(context, []):
                coordinate = {
                    **row,
                    "context": context,
                    "selection_index": idx,
                }
                metric_value = selected_metric_values.get(
                    (context, str(row.get("tool_id") or ""))
                )
                if metric_value is not None:
                    coordinate["metric_value"] = _format_float(metric_value)
                selected_coordinates.append(coordinate)
        selected = {
            "contexts": selected_contexts,
            "distances": _aggregate_distance_rows(
                conn,
                source_id=source_id,
                family=family,
                contexts=selected_contexts,
                level=level,
                distance_metric=distance_metric,
                tools=tools,
            ),
            "coordinates": selected_coordinates,
        }
    evaluation = (
        _evaluation_metric_view(
            conn,
            source_id=source_id,
            family=family,
            contexts=contexts,
            selected_contexts=selected_contexts,
            level=level,
            evaluation_metric=evaluation_metric,
            tools=tools,
        )
        if evaluation_metric
        else None
    )
    warnings = []
    if coordinate_warning:
        warnings.append(coordinate_warning)
    diagnostics = {
        "aggregate_stress": _format_float(
            _stress_for_coordinate_rows(
                _pair_distances_from_aggregate_rows(aggregate_rows),
                aggregate_coordinates,
            )
        ),
        **context_diagnostics,
    }
    return {
        "level": level,
        "tools": tools,
        "aggregate": {
            "distances": aggregate_rows,
            "coordinates": aggregate_coordinates,
            "ellipses": _ellipses_for_context_coordinates(
                aggregate_coordinates=aggregate_coordinates,
                coordinates_by_context=anchored_by_context,
            ),
        },
        "selected": selected,
        "evaluation": evaluation,
        "diagnostics": diagnostics,
        "warnings": warnings,
    }


def _contexts_for_family(
    conn: sqlite3.Connection, *, source_id: str, family: str
) -> list[str]:
    rows = [
        str(row["context"])
        for row in conn.execute(
            """
            SELECT DISTINCT ni.context
            FROM network_index ni
            JOIN context_summary cs ON cs.context = ni.context
            WHERE ni.source_id = ? AND cs.context_family = ?
            """,
            (source_id, family),
        )
    ]
    return sorted(rows, key=_context_sort_key)


def _normalize_selected_contexts(
    *,
    available_contexts: list[str],
    selected_contexts: list[str] | None,
) -> list[str]:
    requested = []
    for item in selected_contexts or []:
        requested.extend(
            part.strip()
            for part in str(item).split(",")
            if part.strip()
        )
    unique_requested = list(dict.fromkeys(requested))
    if len(unique_requested) > MAX_SELECTED_CONTEXTS:
        raise ValueError(
            f"At most {MAX_SELECTED_CONTEXTS} specific contexts can be selected."
        )
    available = set(available_contexts)
    unknown = [item for item in unique_requested if item not in available]
    if unknown:
        raise ValueError(f"Unknown context(s): {', '.join(unknown)}")
    return unique_requested


def _normalize_evaluation_metric(metric: str | None) -> str | None:
    normalized = str(metric or "").strip()
    if not normalized:
        return None
    if normalized not in EVALUATION_METRIC_COLUMNS:
        raise ValueError(
            "evaluation_metric must be one of "
            f"{', '.join(EVALUATION_METRIC_COLUMNS)}"
        )
    return normalized


def _tools_for_level(
    conn: sqlite3.Connection, *, source_id: str, family: str, level: str
) -> list[dict[str, str]]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT ni.tool_id, MIN(ni.catalog_tool_id) AS catalog_tool_id
            FROM network_index ni
            JOIN context_summary cs ON cs.context = ni.context
            WHERE ni.source_id = ? AND cs.context_family = ? AND ni.level = ?
            GROUP BY ni.tool_id
            ORDER BY ni.tool_id
            """,
            (source_id, family, level),
        )
    ]
    return [
        {
            "tool_id": str(row["tool_id"]),
            "catalog_tool_id": str(row["catalog_tool_id"] or row["tool_id"]),
        }
        for row in rows
    ]


def _aggregate_distance_rows(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    family: str,
    contexts: list[str],
    level: str,
    distance_metric: str,
    tools: list[dict[str, str]],
) -> list[dict[str, Any]]:
    raw_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                d.context,
                d.distance,
                d.status,
                d.warning,
                a.tool_id AS tool_a,
                b.tool_id AS tool_b
            FROM distances d
            JOIN context_summary cs ON cs.context = d.context
            JOIN network_index a ON a.network_id = d.network_a
            JOIN network_index b ON b.network_id = d.network_b
            WHERE d.source_id = ?
              AND cs.context_family = ?
              AND d.level = ?
              AND d.distance_metric = ?
            """,
            (source_id, family, level, distance_metric),
        )
    ]
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    context_set = set(contexts)
    for row in raw_rows:
        if str(row["context"]) not in context_set:
            continue
        by_pair[_tool_pair_key(str(row["tool_a"]), str(row["tool_b"]))].append(row)
    tool_ids = [tool["tool_id"] for tool in tools]
    rows = []
    for left_idx, tool_a in enumerate(tool_ids):
        for tool_b in tool_ids[left_idx + 1 :]:
            items = by_pair.get(_tool_pair_key(tool_a, tool_b), [])
            values = [
                value
                for item in items
                for value in [_safe_float(item.get("distance"))]
                if item.get("status") == "ok" and value is not None
            ]
            stats = _summary_stats(values)
            rows.append(
                {
                    "tool_a": tool_a,
                    "tool_b": tool_b,
                    "n": len(values),
                    "unavailable": max(0, len(contexts) - len(values)),
                    "context_count": len(contexts),
                    **stats,
                }
            )
    return rows


def _evaluation_metric_view(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    family: str,
    contexts: list[str],
    selected_contexts: list[str],
    level: str,
    evaluation_metric: str,
    tools: list[dict[str, str]],
) -> dict[str, Any]:
    aggregate = _aggregate_evaluation_metric_rows(
        conn,
        source_id=source_id,
        family=family,
        contexts=contexts,
        level=level,
        evaluation_metric=evaluation_metric,
        tools=tools,
    )
    selected = (
        _aggregate_evaluation_metric_rows(
            conn,
            source_id=source_id,
            family=family,
            contexts=selected_contexts,
            level=level,
            evaluation_metric=evaluation_metric,
            tools=tools,
        )
        if selected_contexts
        else None
    )
    return {
        "metric": evaluation_metric,
        "aggregate": aggregate,
        "selected": selected,
    }


def _aggregate_evaluation_metric_rows(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    family: str,
    contexts: list[str],
    level: str,
    evaluation_metric: str,
    tools: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if not contexts:
        return []
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT
                em.context,
                em.tool_id,
                em.status,
                em.{evaluation_metric} AS value
            FROM evaluation_metrics em
            JOIN context_summary cs ON cs.context = em.context
            WHERE em.source_id = ?
              AND cs.context_family = ?
              AND em.level = ?
            """,
            (source_id, family, level),
        )
    ]
    context_set = set(contexts)
    values_by_tool: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        context = str(row.get("context") or "")
        tool_id = str(row.get("tool_id") or "")
        if context not in context_set:
            continue
        if not _evaluation_status_is_usable(row.get("status")):
            continue
        value = _safe_float(row.get("value"))
        if value is None:
            continue
        values_by_tool[tool_id].append(value)
    return [
        {
            "tool_id": tool["tool_id"],
            "n": len(values_by_tool.get(tool["tool_id"], [])),
            "unavailable": max(
                0,
                len(contexts) - len(values_by_tool.get(tool["tool_id"], [])),
            ),
            "context_count": len(contexts),
            **_summary_stats(values_by_tool.get(tool["tool_id"], [])),
        }
        for tool in tools
    ]


def _evaluation_metric_value_lookup(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    contexts: list[str],
    level: str,
    evaluation_metric: str | None,
) -> dict[tuple[str, str], float]:
    if not evaluation_metric or not contexts:
        return {}
    context_set = set(contexts)
    lookup: dict[tuple[str, str], float] = {}
    rows = conn.execute(
        f"""
        SELECT context, tool_id, status, {evaluation_metric} AS value
        FROM evaluation_metrics
        WHERE source_id = ? AND level = ?
        """,
        (source_id, level),
    )
    for row in rows:
        context = str(row["context"] or "")
        if context not in context_set:
            continue
        if not _evaluation_status_is_usable(row["status"]):
            continue
        value = _safe_float(row["value"])
        if value is not None:
            lookup[(context, str(row["tool_id"] or ""))] = value
    return lookup


def _evaluation_status_is_usable(status: Any) -> bool:
    return str(status or "").strip() in {"", "ok", "partial"}


def _aggregate_coordinates(
    *,
    tools: list[dict[str, str]],
    aggregate_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    tool_ids = [tool["tool_id"] for tool in tools]
    if len(tool_ids) < 2:
        return [], "At least two tools are required for a distance map."
    distances = {
        _tool_pair_key(str(row["tool_a"]), str(row["tool_b"])): _safe_float(row.get("median"))
        for row in aggregate_rows
        if int(row.get("n") or 0) > 0
    }
    expected_pairs = len(tool_ids) * (len(tool_ids) - 1) // 2
    if len(distances) < expected_pairs:
        return [], "Aggregate distance matrix is incomplete; distance map is not available."
    matrix = [[0.0 for _col in tool_ids] for _row in tool_ids]
    for left_idx, tool_a in enumerate(tool_ids):
        for right_idx in range(left_idx + 1, len(tool_ids)):
            tool_b = tool_ids[right_idx]
            value = distances.get(_tool_pair_key(tool_a, tool_b))
            if value is None:
                return [], "Aggregate distance matrix is incomplete; distance map is not available."
            matrix[left_idx][right_idx] = value
            matrix[right_idx][left_idx] = value
    coordinates = classical_mds_coordinates(matrix)
    return [
        {
            "tool_id": tool_id,
            "x": _format_float(x),
            "y": _format_float(y),
            "status": "ok",
        }
        for tool_id, (x, y) in zip(tool_ids, coordinates)
    ], ""


def _anchored_context_coordinates(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    family: str,
    contexts: list[str],
    level: str,
    distance_metric: str,
    aggregate_coordinates: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    reference = {
        str(row["tool_id"]): (
            float(row["x"]),
            float(row["y"]),
        )
        for row in aggregate_coordinates
        if row.get("status") == "ok" and row.get("x") is not None and row.get("y") is not None
    }
    if len(reference) < 2:
        return {}, {
            "context_embeddings": 0,
            "mean_context_stress": None,
            "mean_anchor_displacement": None,
            "anchor_lambda": _format_float(ANCHOR_LAMBDA),
        }
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                d.context,
                d.distance,
                d.status,
                a.tool_id AS tool_a,
                b.tool_id AS tool_b
            FROM distances d
            JOIN context_summary cs ON cs.context = d.context
            JOIN network_index a ON a.network_id = d.network_a
            JOIN network_index b ON b.network_id = d.network_b
            WHERE d.source_id = ?
              AND cs.context_family = ?
              AND d.level = ?
              AND d.distance_metric = ?
            """,
            (source_id, family, level, distance_metric),
        )
    ]
    pair_distances_by_context: dict[str, dict[tuple[str, str], float]] = defaultdict(dict)
    participating_tools_by_context: dict[str, set[str]] = defaultdict(set)
    support_contexts_by_tool: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("status") != "ok":
            continue
        context = str(row.get("context") or "")
        value = _safe_float(row.get("distance"))
        tool_a = str(row.get("tool_a") or "")
        tool_b = str(row.get("tool_b") or "")
        if (
            not context
            or value is None
            or tool_a not in reference
            or tool_b not in reference
            or tool_a == tool_b
        ):
            continue
        pair_distances_by_context[context][_tool_pair_key(tool_a, tool_b)] = value
        participating_tools_by_context[context].update([tool_a, tool_b])
        support_contexts_by_tool[tool_a].add(context)
        support_contexts_by_tool[tool_b].add(context)
    anchored = {}
    stresses = []
    displacements = []
    support_counts = {
        tool_id: len(contexts_for_tool)
        for tool_id, contexts_for_tool in support_contexts_by_tool.items()
    }
    for context in contexts:
        pair_distances = pair_distances_by_context.get(context, {})
        tool_ids = sorted(participating_tools_by_context.get(context, set()))
        if not pair_distances or len(tool_ids) < 2:
            continue
        coordinates, stress, anchor_displacement = _anchored_mds_coordinates(
            tool_ids=tool_ids,
            pair_distances=pair_distances,
            reference=reference,
            lambda_anchor=ANCHOR_LAMBDA,
        )
        if coordinates:
            anchored[context] = [
                {
                    "tool_id": tool_id,
                    "x": _format_float(
                        reference[tool_id][0]
                        if support_counts.get(tool_id, 0) == 1
                        else x
                    ),
                    "y": _format_float(
                        reference[tool_id][1]
                        if support_counts.get(tool_id, 0) == 1
                        else y
                    ),
                    "status": "ok",
                }
                for tool_id, (x, y) in sorted(coordinates.items())
            ]
            stresses.append(stress)
            displacements.append(anchor_displacement)
    return anchored, {
        "context_embeddings": len(anchored),
        "mean_context_stress": _format_float(sum(stresses) / len(stresses)) if stresses else None,
        "mean_anchor_displacement": _format_float(sum(displacements) / len(displacements)) if displacements else None,
        "anchor_lambda": _format_float(ANCHOR_LAMBDA),
    }


def _anchored_mds_coordinates(
    *,
    tool_ids: list[str],
    pair_distances: dict[tuple[str, str], float],
    reference: dict[str, tuple[float, float]],
    lambda_anchor: float,
) -> tuple[dict[str, tuple[float, float]], float, float]:
    positions = {tool: reference[tool] for tool in tool_ids if tool in reference}
    if len(positions) < 2:
        return {}, math.nan, math.nan
    pairs = [
        (tool_a, tool_b, value)
        for (tool_a, tool_b), value in sorted(pair_distances.items())
        if tool_a in positions and tool_b in positions and value is not None
    ]
    if not pairs:
        return {}, math.nan, math.nan
    best_loss, _gradients = _anchored_mds_loss_and_gradient(
        positions=positions,
        pairs=pairs,
        reference=reference,
        lambda_anchor=lambda_anchor,
    )
    step = ANCHORED_MDS_INITIAL_STEP
    for _iteration in range(ANCHORED_MDS_ITERATIONS):
        _loss, gradients = _anchored_mds_loss_and_gradient(
            positions=positions,
            pairs=pairs,
            reference=reference,
            lambda_anchor=lambda_anchor,
        )
        candidate = {
            tool: (
                positions[tool][0] - (step * gradients[tool][0]),
                positions[tool][1] - (step * gradients[tool][1]),
            )
            for tool in positions
        }
        candidate_loss, _candidate_gradients = _anchored_mds_loss_and_gradient(
            positions=candidate,
            pairs=pairs,
            reference=reference,
            lambda_anchor=lambda_anchor,
        )
        if candidate_loss <= best_loss:
            positions = candidate
            if abs(best_loss - candidate_loss) <= 1e-10:
                best_loss = candidate_loss
                break
            best_loss = candidate_loss
            step = min(step * 1.05, 1.0)
        else:
            step *= 0.5
            if step < 1e-7:
                break
    stress = _normalized_stress(pairs, positions)
    anchor_displacement = sum(
        math.sqrt(
            (positions[tool][0] - reference[tool][0]) ** 2
            + (positions[tool][1] - reference[tool][1]) ** 2
        )
        for tool in positions
    ) / len(positions)
    return positions, stress, anchor_displacement


def _anchored_mds_loss_and_gradient(
    *,
    positions: dict[str, tuple[float, float]],
    pairs: list[tuple[str, str, float]],
    reference: dict[str, tuple[float, float]],
    lambda_anchor: float,
) -> tuple[float, dict[str, tuple[float, float]]]:
    gradients = {tool: [0.0, 0.0] for tool in positions}
    loss = 0.0
    pair_weight = 1.0 / max(1, len(pairs))
    for tool_a, tool_b, target in pairs:
        ax, ay = positions[tool_a]
        bx, by = positions[tool_b]
        dx = ax - bx
        dy = ay - by
        observed = math.sqrt((dx * dx) + (dy * dy))
        if observed <= 1e-12:
            observed = 1e-12
        diff = observed - target
        loss += pair_weight * diff * diff
        factor = 2.0 * pair_weight * diff / observed
        gx = factor * dx
        gy = factor * dy
        gradients[tool_a][0] += gx
        gradients[tool_a][1] += gy
        gradients[tool_b][0] -= gx
        gradients[tool_b][1] -= gy
    anchor_weight = lambda_anchor / max(1, len(positions))
    for tool, (x, y) in positions.items():
        ref_x, ref_y = reference[tool]
        dx = x - ref_x
        dy = y - ref_y
        loss += anchor_weight * ((dx * dx) + (dy * dy))
        gradients[tool][0] += 2.0 * anchor_weight * dx
        gradients[tool][1] += 2.0 * anchor_weight * dy
    return loss, {
        tool: (gradient[0], gradient[1])
        for tool, gradient in gradients.items()
    }


def _ellipses_for_context_coordinates(
    *,
    aggregate_coordinates: list[dict[str, Any]],
    coordinates_by_context: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    aggregate = {
        str(row["tool_id"]): (
            _safe_float(row.get("x")),
            _safe_float(row.get("y")),
        )
        for row in aggregate_coordinates
    }
    displacements_by_tool: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for rows in coordinates_by_context.values():
        for row in rows:
            x = _safe_float(row.get("x"))
            y = _safe_float(row.get("y"))
            tool_id = str(row.get("tool_id") or "")
            center = aggregate.get(tool_id)
            if (
                x is not None
                and y is not None
                and center is not None
                and center[0] is not None
                and center[1] is not None
            ):
                displacements_by_tool[tool_id].append((x - center[0], y - center[1]))
    ellipses = []
    for tool_id, displacements in sorted(displacements_by_tool.items()):
        if len(displacements) < 2:
            continue
        center = aggregate.get(tool_id)
        if center is None or center[0] is None or center[1] is None:
            continue
        trimmed = _trim_displacements(displacements, keep_ratio=ELLIPSE_KEEP_RATIO)
        rx, ry, angle_deg = _covariance_ellipse_axes(trimmed)
        if rx <= ELLIPSE_MIN_AXIS or ry <= ELLIPSE_MIN_AXIS:
            continue
        ellipses.append(
            {
                "tool_id": tool_id,
                "point_count": len(displacements),
                "trimmed_count": len(trimmed),
                "center_x": _format_float(center[0]),
                "center_y": _format_float(center[1]),
                "rx": _format_float(rx),
                "ry": _format_float(ry),
                "angle_deg": _format_float(angle_deg),
                "scale": "covariance_95",
            }
        )
    return ellipses


def _tool_pair_key(tool_a: str, tool_b: str) -> tuple[str, str]:
    left, right = sorted((tool_a, tool_b))
    return left, right


def _trim_displacements(
    displacements: list[tuple[float, float]], *, keep_ratio: float
) -> list[tuple[float, float]]:
    if len(displacements) <= 3:
        return list(displacements)
    keep_count = max(3, math.ceil(len(displacements) * keep_ratio))
    ordered = sorted(
        displacements,
        key=lambda item: (item[0] * item[0]) + (item[1] * item[1]),
    )
    return ordered[:keep_count]


def _covariance_ellipse_axes(
    displacements: list[tuple[float, float]],
) -> tuple[float, float, float]:
    if not displacements:
        return 0.0, 0.0, 0.0
    cov_xx = sum(dx * dx for dx, _dy in displacements) / len(displacements)
    cov_xy = sum(dx * dy for dx, dy in displacements) / len(displacements)
    cov_yy = sum(dy * dy for _dx, dy in displacements) / len(displacements)
    trace = cov_xx + cov_yy
    delta = math.sqrt(max(0.0, ((cov_xx - cov_yy) * (cov_xx - cov_yy)) + (4.0 * cov_xy * cov_xy)))
    eigen_major = max(0.0, (trace + delta) / 2.0)
    eigen_minor = max(0.0, (trace - delta) / 2.0)
    if abs(cov_xy) > 1e-12 or abs(eigen_major - cov_xx) > 1e-12:
        angle = math.atan2(eigen_major - cov_xx, cov_xy)
    else:
        angle = 0.0 if cov_xx >= cov_yy else math.pi / 2.0
    return (
        math.sqrt(eigen_major) * ELLIPSE_95_SCALE,
        math.sqrt(eigen_minor) * ELLIPSE_95_SCALE,
        math.degrees(angle),
    )


def _pair_distances_from_aggregate_rows(
    aggregate_rows: list[dict[str, Any]],
) -> dict[tuple[str, str], float]:
    pair_distances = {}
    for row in aggregate_rows:
        value = _safe_float(row.get("median"))
        if int(row.get("n") or 0) > 0 and value is not None:
            pair_distances[_tool_pair_key(str(row["tool_a"]), str(row["tool_b"]))] = value
    return pair_distances


def _stress_for_coordinate_rows(
    pair_distances: dict[tuple[str, str], float],
    coordinates: list[dict[str, Any]],
) -> float | None:
    positions = {
        str(row["tool_id"]): (_safe_float(row.get("x")), _safe_float(row.get("y")))
        for row in coordinates
    }
    clean_positions = {
        tool: (x, y)
        for tool, (x, y) in positions.items()
        if x is not None and y is not None
    }
    pairs = [
        (tool_a, tool_b, value)
        for (tool_a, tool_b), value in pair_distances.items()
        if tool_a in clean_positions and tool_b in clean_positions
    ]
    if not pairs:
        return None
    return _normalized_stress(pairs, clean_positions)


def _normalized_stress(
    pairs: list[tuple[str, str, float]],
    positions: dict[str, tuple[float, float]],
) -> float:
    if not pairs:
        return math.nan
    numerator = 0.0
    denominator = 0.0
    for tool_a, tool_b, target in pairs:
        ax, ay = positions[tool_a]
        bx, by = positions[tool_b]
        observed = math.sqrt(((ax - bx) ** 2) + ((ay - by) ** 2))
        numerator += (observed - target) ** 2
        denominator += target * target
    if denominator <= 1e-12:
        return math.sqrt(numerator / len(pairs))
    return math.sqrt(numerator / denominator)


def edge_variability(
    path: Path,
    *,
    selected_networks: list[dict[str, Any]],
    limit: int,
    evaluation_metric: str | None = None,
) -> dict[str, Any]:
    safe_limit = _normalize_limit(limit)
    eval_metric = _normalize_evaluation_metric(evaluation_metric)
    results = []
    warnings = []
    for level in ("topology", "directed", "signed"):
        level_networks = _resolve_selected_networks(
            path, selected_networks=selected_networks, level=level
        )
        if eval_metric:
            _attach_network_evaluation_metrics(
                path,
                networks=level_networks,
                evaluation_metric=eval_metric,
            )
        if len(level_networks) != len(selected_networks):
            results.append(
                {
                    "level": level,
                    "status": "not_available",
                    "warning": f"One or more selected networks do not have a {level} network.",
                    "networks": level_networks,
                    "rows": [],
                    "common_genes": 0,
                    "comparable_edges": 0,
                    "limit": safe_limit,
                    "truncated": False,
                }
            )
            continue
        comparable = _comparable_edge_rows(
            path, networks=level_networks, level=level, limit=safe_limit
        )
        if comparable.get("warning"):
            warnings.append(comparable["warning"])
        results.append({"level": level, "networks": level_networks, **comparable})
    return {
        "limit": safe_limit,
        "evaluation_metric": eval_metric,
        "levels": results,
        "warnings": warnings,
    }


def _create_tables(conn: sqlite3.Connection) -> None:
    for table, columns in SQLITE_TABLE_COLUMNS.items():
        if table == "network_index":
            defs = "network_pk INTEGER PRIMARY KEY, " + ", ".join(
                f"{column} TEXT" for column in columns
            )
        elif table == "edge_scores":
            defs = (
                "network_pk INTEGER, "
                "edge_key TEXT, "
                "source TEXT, "
                "target TEXT, "
                "sign TEXT, "
                "score REAL"
            )
        else:
            defs = ", ".join(f"{column} TEXT" for column in columns)
        conn.execute(f"CREATE TABLE {table} ({defs})")


def _insert_rows(
    conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]
) -> None:
    columns = SQLITE_TABLE_COLUMNS[table]
    placeholders = ", ".join("?" for _column in columns)
    column_sql = ", ".join(columns)
    iterator = (
        [_sqlite_value(row.get(column)) for column in columns]
        for row in rows
    )
    while True:
        batch = list(islice(iterator, 5000))
        if not batch:
            break
        conn.executemany(
            f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
            batch,
        )


def _insert_edge_score_rows(
    conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]
) -> None:
    network_pk_by_id = {
        str(row[1]): int(row[0])
        for row in conn.execute("SELECT network_pk, network_id FROM network_index")
    }
    columns = SQLITE_TABLE_COLUMNS["edge_scores"]
    placeholders = ", ".join("?" for _column in columns)
    column_sql = ", ".join(columns)

    def values() -> Iterable[list[Any]]:
        for row in rows:
            network_id = str(row.get("network_id") or "")
            try:
                network_pk = network_pk_by_id[network_id]
            except KeyError as exc:
                raise ValueError(
                    f"edge_scores references unknown network_id: {network_id}"
                ) from exc
            yield [
                network_pk,
                _sqlite_value(row.get("edge_key")),
                _sqlite_value(row.get("source")),
                _sqlite_value(row.get("target")),
                _sqlite_value(row.get("sign")),
                float(row.get("score") or 0.0),
            ]

    iterator = values()
    while True:
        batch = list(islice(iterator, 5000))
        if not batch:
            break
        conn.executemany(
            f"INSERT INTO edge_scores ({column_sql}) VALUES ({placeholders})",
            batch,
        )


def _create_indices(conn: sqlite3.Connection) -> None:
    index_statements = [
        "CREATE INDEX idx_network_index_source_context_level ON network_index(source_id, context, level)",
        "CREATE INDEX idx_network_index_network_id ON network_index(network_id)",
        "CREATE INDEX idx_network_index_tool ON network_index(source_id, tool_id, context, level)",
        "CREATE INDEX idx_edge_scores_network ON edge_scores(network_pk)",
        "CREATE INDEX idx_distances_context ON distances(source_id, context, level, distance_metric)",
        "CREATE INDEX idx_coordinates_context ON distance_coordinates(source_id, context, level, distance_metric)",
        "CREATE INDEX idx_eval_context ON evaluation_metrics(source_id, tool_id, context, level)",
        "CREATE INDEX idx_context_summary_family ON context_summary(context_family, context)",
    ]
    for statement in index_statements:
        conn.execute(statement)


def _context_summary_rows(network_index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    for row in network_index:
        counts[str(row.get("context") or "")] += 1
    return [
        {
            "context": context,
            "context_family": network_context_family(context),
            "network_instances": count,
        }
        for context, count in sorted(counts.items(), key=lambda item: _context_sort_key(item[0]))
    ]


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_value(value: Any) -> Any:
    if value is None:
        return None
    return str(value)


def _normalize_family(family: str) -> str:
    normalized = str(family or "").strip().removesuffix("s")
    if normalized not in {"global", "group", "cell", "other"}:
        raise ValueError("family must be one of global, group, cell or other")
    return normalized


def _normalize_limit(limit: int) -> int:
    value = int(limit or 100)
    if value not in {100, 500, 1000}:
        raise ValueError("limit must be one of 100, 500 or 1000")
    return value


def _context_sort_key(context: str) -> tuple[int, str]:
    family_order = {"global": 0, "group": 1, "cell": 2, "other": 3}
    family = network_context_family(context)
    return (family_order[family], context)


def _summary_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"median": None, "q1": None, "q3": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "median": _format_float(median(ordered)),
        "q1": _format_float(_quantile(ordered, 0.25)),
        "q3": _format_float(_quantile(ordered, 0.75)),
        "min": _format_float(ordered[0]),
        "max": _format_float(ordered[-1]),
    }


def _quantile(ordered: list[float], probability: float) -> float:
    if not ordered:
        return math.nan
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    ratio = position - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * ratio)


def _format_float(value: float | None) -> str | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return f"{float(value):.6g}"


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _resolve_selected_networks(
    path: Path,
    *,
    selected_networks: list[dict[str, Any]],
    level: str,
) -> list[dict[str, Any]]:
    resolved = []
    with _connect(path) as conn:
        for item in selected_networks:
            row = conn.execute(
                """
                SELECT *
                FROM network_index
                WHERE source_id = ? AND tool_id = ? AND context = ? AND level = ?
                """,
                (
                    str(item.get("source_id") or ""),
                    str(item.get("tool_id") or ""),
                    str(item.get("context") or ""),
                    level,
                ),
            ).fetchone()
            if row is not None:
                resolved.append(dict(row))
    return resolved


def _attach_network_evaluation_metrics(
    path: Path,
    *,
    networks: list[dict[str, Any]],
    evaluation_metric: str,
) -> None:
    if not networks:
        return
    with _connect(path) as conn:
        for network in networks:
            row = conn.execute(
                f"""
                SELECT status, {evaluation_metric} AS value
                FROM evaluation_metrics
                WHERE source_id = ? AND tool_id = ? AND context = ? AND level = ?
                """,
                (
                    str(network.get("source_id") or ""),
                    str(network.get("tool_id") or ""),
                    str(network.get("context") or ""),
                    str(network.get("level") or ""),
                ),
            ).fetchone()
            if row is None:
                network["metric_status"] = "missing"
                network["metric_value"] = None
                continue
            value = _safe_float(row["value"])
            network["metric_status"] = str(row["status"] or "")
            network["metric_value"] = (
                _format_float(value)
                if value is not None and _evaluation_status_is_usable(row["status"])
                else None
            )


def _comparable_edge_rows(
    path: Path,
    *,
    networks: list[dict[str, Any]],
    level: str,
    limit: int,
) -> dict[str, Any]:
    with _connect(path) as conn:
        common_nodes = _common_nodes_for_networks(conn, networks=networks)
        if not common_nodes:
            return {
                "status": "not_available",
                "warning": "No common genes across selected networks for this level.",
                "rows": [],
                "common_genes": 0,
                "comparable_edges": 0,
                "limit": limit,
                "truncated": False,
            }
        score_maps, row_maps = _score_maps_for_common_nodes(
            conn,
            networks=networks,
            common_nodes=common_nodes,
            level=level,
        )
    top_rows: list[tuple[float, tuple[int, ...], int, dict[str, Any]]] = []
    total = 0
    for edge_key in {key for score_map in score_maps.values() for key in score_map}:
        values = [score_maps[network["network_id"]].get(edge_key, 0.0) for network in networks]
        raw = [row_maps[network["network_id"]].get(edge_key) for network in networks]
        edge_label = _edge_label(next((row for row in raw if row), None), edge_key, level)
        row = {
            "edge_key": edge_key,
            "edge_label": edge_label,
            "values": [_format_float(value) for value in values],
            "raw": raw,
            "variance": _variance(values),
        }
        total += 1
        heap_key = (float(row["variance"]), _reverse_label_rank(str(edge_label)))
        if len(top_rows) < limit:
            heapq.heappush(top_rows, (heap_key[0], heap_key[1], total, row))
        elif heap_key > (top_rows[0][0], top_rows[0][1]):
            heapq.heapreplace(top_rows, (heap_key[0], heap_key[1], total, row))
    computed = [
        item[3]
        for item in sorted(
            top_rows,
            key=lambda item: (-item[3]["variance"], str(item[3]["edge_label"])),
        )
    ]
    return {
        "status": "ok",
        "warning": "",
        "rows": [
            {
                **row,
                "variance": _format_float(row["variance"]),
            }
            for row in computed
        ],
        "common_genes": len(common_nodes),
        "comparable_edges": total,
        "limit": limit,
        "truncated": total > limit,
    }


def _common_nodes_for_networks(
    conn: sqlite3.Connection, *, networks: list[dict[str, Any]]
) -> set[str]:
    common: set[str] | None = None
    for network in networks:
        network_pk = int(network["network_pk"])
        nodes: set[str] = set()
        for row in conn.execute(
            """
            SELECT source, target
            FROM edge_scores
            WHERE network_pk = ?
            """,
            (network_pk,),
        ):
            if row["source"]:
                nodes.add(str(row["source"]))
            if row["target"]:
                nodes.add(str(row["target"]))
        if common is None:
            common = nodes
        else:
            common.intersection_update(nodes)
        if not common:
            return set()
    return common or set()


def _score_maps_for_common_nodes(
    conn: sqlite3.Connection,
    *,
    networks: list[dict[str, Any]],
    common_nodes: set[str],
    level: str,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, dict[str, Any]]]]:
    score_maps: dict[str, dict[str, float]] = {
        str(network["network_id"]): {} for network in networks
    }
    row_maps: dict[str, dict[str, dict[str, Any]]] = {
        str(network["network_id"]): {} for network in networks
    }
    pk_to_network_id = {
        int(network["network_pk"]): str(network["network_id"]) for network in networks
    }
    if not pk_to_network_id:
        return score_maps, row_maps

    conn.execute("DROP TABLE IF EXISTS temp_common_nodes")
    conn.execute("CREATE TEMP TABLE temp_common_nodes(value TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT OR IGNORE INTO temp_common_nodes(value) VALUES (?)",
        ((node,) for node in sorted(common_nodes)),
    )
    placeholders = ", ".join("?" for _item in pk_to_network_id)
    query = f"""
        SELECT es.network_pk, es.edge_key, es.source, es.target, es.sign, es.score
        FROM edge_scores es
        JOIN temp_common_nodes source_node ON source_node.value = es.source
        JOIN temp_common_nodes target_node ON target_node.value = es.target
        WHERE es.network_pk IN ({placeholders})
        ORDER BY es.network_pk, es.edge_key
    """
    for raw in conn.execute(query, tuple(pk_to_network_id)):
        network_id = pk_to_network_id[int(raw["network_pk"])]
        row = {
            "edge_key": raw["edge_key"],
            "source": raw["source"],
            "target": raw["target"],
            "sign": raw["sign"],
            "score": float(raw["score"] or 0.0),
            "network_id": network_id,
        }
        key = _edge_difference_key(row, level)
        value = _signed_value(row, level)
        current = score_maps[network_id].get(key)
        if current is None or _prefer_edge_value(
            current=current,
            candidate=value,
            current_row=row_maps[network_id].get(key),
            candidate_row=row,
        ):
            score_maps[network_id][key] = value
            row_maps[network_id][key] = row
    conn.execute("DROP TABLE IF EXISTS temp_common_nodes")
    return score_maps, row_maps


def _reverse_label_rank(label: str) -> tuple[int, ...]:
    return tuple(-ord(char) for char in label)


def _edge_rows_for_network(path: Path, network_id: str) -> list[dict[str, Any]]:
    with _connect(path) as conn:
        network = conn.execute(
            "SELECT network_pk FROM network_index WHERE network_id = ?",
            (network_id,),
        ).fetchone()
        if network is None:
            return []
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT edge_key, source, target, sign, score
                FROM edge_scores
                WHERE network_pk = ?
                """,
                (network["network_pk"],),
            )
        ]
    for row in rows:
        row["network_id"] = network_id
        row["score"] = float(row.get("score") or 0.0)
    return rows


def _nodes_for_rows(rows: Iterable[dict[str, Any]]) -> set[str]:
    nodes: set[str] = set()
    for row in rows:
        if row.get("source"):
            nodes.add(str(row["source"]))
        if row.get("target"):
            nodes.add(str(row["target"]))
    return nodes


def _intersect_sets(sets: list[set[str]]) -> set[str]:
    if not sets:
        return set()
    common = set(sets[0])
    for item in list(common):
        if not all(item in candidates for candidates in sets):
            common.remove(item)
    return common


def _edge_difference_key(row: dict[str, Any], level: str) -> str:
    if level == "signed":
        return f"{row.get('source')}|{row.get('target')}"
    return str(row.get("edge_key") or f"{row.get('source')}|{row.get('target')}")


def _signed_value(row: dict[str, Any], level: str) -> float:
    score = float(row.get("score") or 0.0)
    if level == "signed" and row.get("sign") == "-":
        return -score
    return score


def _prefer_edge_value(
    *,
    current: float,
    candidate: float,
    current_row: dict[str, Any] | None,
    candidate_row: dict[str, Any],
) -> bool:
    if abs(candidate) != abs(current):
        return abs(candidate) > abs(current)
    return str(candidate_row.get("sign") or "") < str((current_row or {}).get("sign") or "")


def _edge_label(row: dict[str, Any] | None, fallback: str, level: str) -> str:
    if row and row.get("source") and row.get("target"):
        if level == "topology":
            return f"{row['source']} - {row['target']}"
        return f"{row['source']} -> {row['target']}"
    return fallback


def _variance(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)
