"""Network index, edge score and evaluation metric table builders."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from andrea.core.commands.compare_networks.models import (
    COMPARISON_LEVELS,
    EVALUATION_METRIC_COLUMNS,
    VALID_SIGNS,
    EvaluationMetric,
    NetworkInstance,
    NetworkRow,
    SourceData,
)
from andrea.core.commands.compare_networks.utils import (
    format_float,
    optional_float,
    optional_int,
    slugify,
    unique_preserve_order,
)


def build_network_tables(
    source_data: list[SourceData],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[NetworkInstance]]:
    network_index: list[dict[str, Any]] = []
    edge_scores: list[dict[str, Any]] = []
    instances: list[NetworkInstance] = []
    for data in source_data:
        catalog_ids = catalog_ids_from_run_report(data.run_report)
        grouped = group_rows(data.rows)
        for (tool_id, context), rows in sorted(grouped.items()):
            catalog_tool_id = catalog_ids.get(tool_id, tool_id)
            run_id = str(data.run_report.get("run_id") or "")
            for level in COMPARISON_LEVELS:
                aggregated = aggregate_rows(rows, level=level)
                nodes = collect_nodes_from_scores(aggregated, level=level)
                network_id = build_network_id(
                    source_id=data.source.source_id,
                    tool_id=tool_id,
                    context=context,
                    level=level,
                )
                network_index.append(
                    {
                        "network_id": network_id,
                        "source_id": data.source.source_id,
                        "run_id": run_id,
                        "tool_id": tool_id,
                        "catalog_tool_id": catalog_tool_id,
                        "context": context,
                        "level": level,
                        "n_genes": len(nodes),
                        "n_edges": len(aggregated),
                    }
                )
                instances.append(
                    NetworkInstance(
                        network_id=network_id,
                        source_id=data.source.source_id,
                        run_id=run_id,
                        tool_id=tool_id,
                        catalog_tool_id=catalog_tool_id,
                        context=context,
                        level=level,
                        scores=aggregated,
                        nodes=nodes,
                    )
                )
                for key, score in sorted(aggregated.items()):
                    edge_scores.append(
                        edge_score_row(
                            network_id=network_id,
                            source_id=data.source.source_id,
                            run_id=run_id,
                            tool_id=tool_id,
                            catalog_tool_id=catalog_tool_id,
                            context=context,
                            level=level,
                            key=key,
                            score=score,
                        )
                    )
    return network_index, edge_scores, instances


def build_evaluation_metrics(
    source_data: list[SourceData],
) -> tuple[dict[tuple[str, str, str, str], EvaluationMetric], list[str]]:
    metrics: dict[tuple[str, str, str, str], EvaluationMetric] = {}
    warnings: list[str] = []
    for data in source_data:
        if data.evaluation_report is None:
            continue
        for idx, raw in enumerate(data.evaluation_report.get("metrics", []), start=1):
            if not isinstance(raw, dict):
                warnings.append(
                    f"[{data.source.source_id}] ignored non-object evaluation metric row {idx}"
                )
                continue
            tool_id = str(raw.get("tool_id", "")).strip()
            context = str(raw.get("context", "")).strip()
            level = str(raw.get("level", "")).strip()
            if not tool_id or not context or level not in COMPARISON_LEVELS:
                warnings.append(
                    f"[{data.source.source_id}] ignored evaluation metric row {idx} with missing tool_id/context or unsupported level"
                )
                continue
            key = (data.source.source_id, tool_id, context, level)
            if key in metrics:
                warnings.append(
                    f"[{data.source.source_id}] duplicate evaluation metric for {tool_id}/{context}/{level}; last row kept"
                )
            metrics[key] = EvaluationMetric(
                source_id=data.source.source_id,
                tool_id=tool_id,
                context=context,
                level=level,
                status=str(raw.get("status") or "").strip(),
                n_truth_edges=optional_int(raw.get("n_truth_edges")),
                values={
                    metric: optional_float(raw.get(metric))
                    for metric in EVALUATION_METRIC_COLUMNS
                },
            )
    warnings.extend(truth_count_discordance_warnings(metrics))
    return metrics, unique_preserve_order(warnings)


def metrics_available(
    evaluation_metrics: dict[tuple[str, str, str, str], EvaluationMetric],
) -> list[str]:
    available: set[str] = set()
    for metric in evaluation_metrics.values():
        for key, value in metric.values.items():
            if value is not None:
                available.add(key)
    return sorted(available)


def has_usable_truth_count(metric: EvaluationMetric) -> bool:
    if metric.n_truth_edges is None or metric.n_truth_edges <= 0:
        return False
    if metric.status and metric.status not in {"ok", "partial"}:
        return False
    return True


def evaluation_metric_report_rows(
    evaluation_metrics: dict[tuple[str, str, str, str], EvaluationMetric],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(evaluation_metrics):
        metric = evaluation_metrics[key]
        row: dict[str, Any] = {
            "source_id": metric.source_id,
            "tool_id": metric.tool_id,
            "context": metric.context,
            "level": metric.level,
            "status": metric.status,
            "n_truth_edges": metric.n_truth_edges,
        }
        row.update(metric.values)
        rows.append(row)
    return rows


def truth_count_discordance_warnings(
    evaluation_metrics: dict[tuple[str, str, str, str], EvaluationMetric],
) -> list[str]:
    by_context_level: dict[tuple[str, str], dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for metric in evaluation_metrics.values():
        if not has_usable_truth_count(metric):
            continue
        by_context_level[(metric.context, metric.level)][metric.source_id].add(
            metric.n_truth_edges
        )

    warnings: list[str] = []
    for (context, level), by_source in sorted(by_context_level.items()):
        source_values = {
            source_id: sorted(values)
            for source_id, values in by_source.items()
            if values
        }
        flattened = {value for values in source_values.values() for value in values}
        if len(flattened) > 1 and len(source_values) > 1:
            details = ", ".join(
                f"{source_id}={values}"
                for source_id, values in sorted(source_values.items())
            )
            warnings.append(
                f"truth_count differs across sources for context={context!r}, level={level!r}: {details}"
            )
    return warnings


def catalog_ids_from_run_report(run_report: dict[str, Any]) -> dict[str, str]:
    tools = run_report.get("tools", {})
    if not isinstance(tools, dict):
        return {}
    catalog_ids = tools.get("catalog_tool_ids", {})
    if not isinstance(catalog_ids, dict):
        return {}
    return {
        str(run_id): str(catalog_id)
        for run_id, catalog_id in catalog_ids.items()
        if str(run_id).strip() and str(catalog_id).strip()
    }


def group_rows(rows: Iterable[NetworkRow]) -> dict[tuple[str, str], list[NetworkRow]]:
    grouped: dict[tuple[str, str], list[NetworkRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.tool_id, row.context)].append(row)
    return dict(grouped)


def aggregate_rows(
    rows: Iterable[NetworkRow], *, level: str
) -> dict[tuple[str, ...], float]:
    scores: dict[tuple[str, ...], float] = {}
    for row in rows:
        if level == "signed" and row.sign not in VALID_SIGNS:
            continue
        if row.score <= 0.0:
            continue
        key = edge_key(row.source, row.target, row.sign, level=level)
        if key not in scores or row.score > scores[key]:
            scores[key] = row.score
    return scores


def edge_key(source: str, target: str, sign: str, *, level: str) -> tuple[str, ...]:
    if level == "topology":
        return (source, target) if source <= target else (target, source)
    if level == "directed":
        return (source, target)
    if level == "signed":
        return (source, target, sign)
    raise ValueError(f"Unknown comparison level: {level}")


def collect_nodes_from_scores(
    scores: dict[tuple[str, ...], float],
    *,
    level: str,
) -> set[str]:
    nodes: set[str] = set()
    for key in scores:
        if level in {"topology", "directed"}:
            nodes.update([key[0], key[1]])
        elif level == "signed":
            nodes.update([key[0], key[1]])
    return nodes


def edge_score_row(
    *,
    network_id: str,
    source_id: str,
    run_id: str,
    tool_id: str,
    catalog_tool_id: str,
    context: str,
    level: str,
    key: tuple[str, ...],
    score: float,
) -> dict[str, Any]:
    if level == "signed":
        source, target, sign = key
    else:
        source, target = key
        sign = ""
    return {
        "network_id": network_id,
        "source_id": source_id,
        "run_id": run_id,
        "tool_id": tool_id,
        "catalog_tool_id": catalog_tool_id,
        "context": context,
        "level": level,
        "edge_key": edge_key_string(key),
        "source": source,
        "target": target,
        "sign": sign,
        "score": format_float(score),
    }


def build_network_id(*, source_id: str, tool_id: str, context: str, level: str) -> str:
    return "::".join(
        [
            slugify(source_id),
            slugify(tool_id),
            slugify(context),
            slugify(level),
        ]
    )


def edge_key_string(key: tuple[str, ...]) -> str:
    return "|".join(key)


def source_report_item(data: SourceData) -> dict[str, Any]:
    dataset = data.run_report.get("dataset")
    tools = data.run_report.get("tools")
    return {
        "source_id": data.source.source_id,
        "label": data.source.label,
        "run_id": data.run_report.get("run_id"),
        "run_status": data.run_report.get("status"),
        "dataset_id": (dataset.get("id") if isinstance(dataset, dict) else None),
        "tools_completed": (
            tools.get("completed") if isinstance(tools, dict) else None
        ),
        "run_report": data.source.request_run_report,
        "evaluation_report": data.source.request_evaluation_report,
        "merged_network": "merged_network_normalized",
        "rows": len(data.rows),
    }
