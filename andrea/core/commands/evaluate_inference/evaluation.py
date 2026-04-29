"""Evaluate inferred GRNs against generated benchmark truth networks."""

from __future__ import annotations

import csv
import html
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

MERGED_NETWORK_REQUIRED_COLUMNS = [
    "source",
    "target",
    "score",
    "sign",
    "evidence",
    "context",
    "tool_id",
]
TRUTH_NETWORK_REQUIRED_COLUMNS = ["source", "target"]
EVALUATION_LEVELS = ["topology", "directed", "signed"]
METRIC_COLUMNS = ["auroc", "aupr", "f1_score"]
VALID_SIGNS = {"+", "-"}
CATALOG_TOOLS_ROOT = (
    Path(__file__).resolve().parents[3] / "catalog_inference_tools" / "tools"
)


@dataclass(frozen=True)
class NetworkRow:
    source: str
    target: str
    score: float
    sign: str
    context: str
    tool_id: Optional[str] = None


@dataclass(frozen=True)
class TruthNetwork:
    context: str
    path: Path
    rows: list[NetworkRow]
    directed: bool
    signed: bool


@dataclass(frozen=True)
class ToolCapabilities:
    catalog_tool_id: str
    directed: bool
    signed: bool


def evaluate_inference(
    *,
    run_report_path: Path,
    ground_truth_manifest_path: Path,
    output_dir: Path,
    generate_plots: bool = True,
) -> dict[str, Any]:
    """Evaluate merged inferred networks against a ground-truth manifest."""
    run_report_path = run_report_path.resolve()
    ground_truth_manifest_path = ground_truth_manifest_path.resolve()
    output_root = output_dir.resolve()
    created_at = datetime.now(timezone.utc)

    run_report = _load_json_object(run_report_path, "Run report")
    merged_network_path = _resolve_merged_network_path_from_run_report(
        run_report_path=run_report_path,
        run_report=run_report,
    )
    inferred_rows = _load_inferred_rows(merged_network_path)
    truth_networks, manifest = _load_truth_networks(ground_truth_manifest_path)
    tool_capabilities = _resolve_tool_capabilities(
        inferred_rows=inferred_rows,
        run_report=run_report,
    )
    evaluation_dir = _create_evaluation_dir(
        output_root=output_root,
        run_report_path=run_report_path,
        run_report=run_report,
        truth_manifest=manifest,
        created_at=created_at,
    )

    metrics: list[dict[str, Any]] = []
    pairings: list[dict[str, Any]] = []
    grouped_predictions = _group_inferred_rows(inferred_rows)

    for (tool_id, context), prediction_rows in sorted(grouped_predictions.items()):
        truth = truth_networks.get(context)
        if truth is None:
            reason = f"no ground-truth network for context {context!r}"
            pairings.append(
                {
                    "tool_id": tool_id,
                    "catalog_tool_id": tool_capabilities[tool_id].catalog_tool_id,
                    "context": context,
                    "status": "skipped",
                    "reason": reason,
                }
            )
            for level in EVALUATION_LEVELS:
                metrics.append(
                    _empty_metric_row(
                        tool_id=tool_id,
                        capabilities=tool_capabilities[tool_id],
                        context=context,
                        truth_context=context,
                        level=level,
                        status="not_applicable",
                        reason=reason,
                    )
                )
            continue

        pairings.append(
            {
                "tool_id": tool_id,
                "catalog_tool_id": tool_capabilities[tool_id].catalog_tool_id,
                "context": context,
                "truth_context": truth.context,
                "truth_path": str(truth.path),
                "status": "evaluated",
                "reason": None,
            }
        )
        for level in EVALUATION_LEVELS:
            metrics.append(
                _evaluate_pairing(
                    tool_id=tool_id,
                    capabilities=tool_capabilities[tool_id],
                    prediction_rows=prediction_rows,
                    truth=truth,
                    level=level,
                )
            )

    metrics_csv_path = evaluation_dir / "metrics.csv"
    pairings_csv_path = evaluation_dir / "pairings.csv"
    report_json_path = evaluation_dir / "evaluation_report.json"

    _write_csv(metrics_csv_path, metrics)
    _write_csv(pairings_csv_path, pairings)
    plot_outputs = (
        _write_plots(evaluation_dir / "plots", metrics) if generate_plots else []
    )

    report = {
        "schema_version": "1.0",
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {
            "run_report": str(run_report_path),
            "ground_truth_manifest": str(ground_truth_manifest_path),
        },
        "derived_inputs": {
            "merged_network_normalized": str(merged_network_path),
        },
        "inference_run": {
            "run_id": run_report.get("run_id"),
            "status": run_report.get("status"),
            "dataset": run_report.get("dataset"),
            "execution": run_report.get("execution"),
            "warnings": run_report.get("warnings", []),
        },
        "ground_truth": {
            "dataset_id": manifest.get("dataset_id"),
            "simulator_id": manifest.get("simulator_id"),
            "profile": manifest.get("profile"),
            "contexts": sorted(truth_networks.keys()),
        },
        "outputs": {
            "output_root": str(output_root),
            "evaluation_dir": str(evaluation_dir),
            "metrics_csv": str(metrics_csv_path),
            "pairings_csv": str(pairings_csv_path),
            "evaluation_report": str(report_json_path),
            "plots_dir": str(evaluation_dir / "plots") if generate_plots else None,
            "plots": plot_outputs,
        },
        "pairings": pairings,
        "metrics": metrics,
    }
    report_json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return report


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} is malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _resolve_merged_network_path_from_run_report(
    *,
    run_report_path: Path,
    run_report: dict[str, Any],
) -> Path:
    outputs = run_report.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("Run report must contain an object at outputs")
    raw_path = outputs.get("merged_network_normalized")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(
            "Run report outputs.merged_network_normalized is required for evaluation"
        )
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return run_report_path.parent / path


def _create_evaluation_dir(
    *,
    output_root: Path,
    run_report_path: Path,
    run_report: dict[str, Any],
    truth_manifest: dict[str, Any],
    created_at: datetime,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    inference_id = _slugify(
        run_report_path.parent.name or str(run_report.get("run_id") or "inference")
    )
    truth_id = _slugify(str(truth_manifest.get("dataset_id") or "truth"))
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    dirname = f"evaluation_{inference_id}__{truth_id}_{timestamp}"
    candidate = output_root / dirname
    suffix = 2
    while candidate.exists():
        candidate = output_root / f"{dirname}_{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return slug or "unknown"


def _load_inferred_rows(path: Path) -> list[NetworkRow]:
    if not path.exists() or not path.is_file():
        raise ValueError(f"Inferred network CSV not found: {path}")

    rows: list[NetworkRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        missing = [col for col in MERGED_NETWORK_REQUIRED_COLUMNS if col not in headers]
        if missing:
            raise ValueError(
                f"Inferred network CSV is missing required columns {missing}: {path}"
            )
        for line_number, row in enumerate(reader, start=2):
            try:
                score = float(row["score"])
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    f"Inferred network CSV has invalid score at line {line_number}: "
                    f"{row.get('score')!r}"
                ) from exc
            if not math.isfinite(score):
                raise ValueError(
                    f"Inferred network CSV has non-finite score at line {line_number}: "
                    f"{row.get('score')!r}"
                )
            rows.append(
                NetworkRow(
                    source=str(row["source"]).strip(),
                    target=str(row["target"]).strip(),
                    score=score,
                    sign=_normalize_sign(str(row["sign"])),
                    context=str(row["context"]).strip(),
                    tool_id=str(row["tool_id"]).strip(),
                )
            )
    if not rows:
        raise ValueError(f"Inferred network CSV contains no rows: {path}")
    return rows


def _load_truth_networks(
    manifest_path: Path,
) -> tuple[dict[str, TruthNetwork], dict[str, Any]]:
    manifest = _load_json_object(manifest_path, "Ground-truth manifest")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("Ground-truth manifest must contain an object at outputs")

    base_dir = manifest_path.parent
    networks: dict[str, TruthNetwork] = {}

    global_path = outputs.get("global_network")
    if isinstance(global_path, str) and global_path.strip():
        networks["global"] = _load_truth_network(
            path=_resolve_manifest_path(base_dir, global_path),
            context="global",
        )

    group_entries = outputs.get("group_networks", [])
    if group_entries is None:
        group_entries = []
    if not isinstance(group_entries, list):
        raise ValueError(
            "Ground-truth manifest outputs.group_networks must be an array"
        )
    for entry in group_entries:
        if not isinstance(entry, dict):
            raise ValueError("Every group_networks entry must be an object")
        group = str(entry.get("group", "")).strip()
        rel_path = entry.get("path")
        if not group or not isinstance(rel_path, str) or not rel_path.strip():
            raise ValueError(
                "Every group_networks entry must include non-empty group and path"
            )
        context = f"group:{group}"
        networks[context] = _load_truth_network(
            path=_resolve_manifest_path(base_dir, rel_path),
            context=context,
        )

    if not networks:
        raise ValueError(
            f"Ground-truth manifest references no truth networks: {manifest_path}"
        )
    return networks, manifest


def _load_truth_network(*, path: Path, context: str) -> TruthNetwork:
    if not path.exists() or not path.is_file():
        raise ValueError(f"Ground-truth network CSV not found: {path}")

    rows: list[NetworkRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        missing = [col for col in TRUTH_NETWORK_REQUIRED_COLUMNS if col not in headers]
        if missing:
            raise ValueError(
                f"Ground-truth network CSV is missing required columns {missing}: {path}"
            )
        has_score = "score" in headers
        has_sign = "sign" in headers
        for line_number, row in enumerate(reader, start=2):
            raw_score = row["score"] if has_score else "1"
            try:
                score = float(raw_score)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    f"Ground-truth network CSV has invalid score at line {line_number}: "
                    f"{raw_score!r}"
                ) from exc
            rows.append(
                NetworkRow(
                    source=str(row["source"]).strip(),
                    target=str(row["target"]).strip(),
                    score=score,
                    sign=_normalize_sign(str(row["sign"])) if has_sign else "?",
                    context=context,
                )
            )
    if not rows:
        raise ValueError(f"Ground-truth network CSV contains no rows: {path}")
    signed = any(row.sign in VALID_SIGNS for row in rows)
    return TruthNetwork(
        context=context,
        path=path,
        rows=rows,
        directed=True,
        signed=signed,
    )


def _resolve_manifest_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def _group_inferred_rows(
    rows: Iterable[NetworkRow],
) -> dict[tuple[str, str], list[NetworkRow]]:
    grouped: dict[tuple[str, str], list[NetworkRow]] = defaultdict(list)
    for row in rows:
        if row.tool_id is None:
            raise ValueError("Inferred network row is missing tool_id")
        grouped[(row.tool_id, row.context)].append(row)
    return dict(grouped)


def _resolve_tool_capabilities(
    *,
    inferred_rows: list[NetworkRow],
    run_report: dict[str, Any],
) -> dict[str, ToolCapabilities]:
    catalog_ids = _catalog_ids_from_run_report(run_report)
    tool_ids = sorted({str(row.tool_id) for row in inferred_rows if row.tool_id})
    missing_catalog_ids = [
        tool_id for tool_id in tool_ids if tool_id not in catalog_ids
    ]
    if missing_catalog_ids:
        raise ValueError(
            "Run report tools.catalog_tool_ids is missing entries for inferred tools: "
            + ", ".join(missing_catalog_ids)
        )

    capabilities: dict[str, ToolCapabilities] = {}
    for tool_id in tool_ids:
        catalog_tool_id = catalog_ids[tool_id]
        spec_outputs = _load_tool_outputs(catalog_tool_id)
        if spec_outputs is None:
            raise ValueError(
                f"ToolSpec outputs could not be resolved for catalog tool {catalog_tool_id!r}"
            )
        capabilities[tool_id] = ToolCapabilities(
            catalog_tool_id=catalog_tool_id,
            directed=bool(spec_outputs.get("directed")),
            signed=str(spec_outputs.get("sign", "none")).strip().lower() != "none",
        )
    return capabilities


def _catalog_ids_from_run_report(run_report: dict[str, Any]) -> dict[str, str]:
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


def _load_tool_outputs(catalog_tool_id: str) -> Optional[dict[str, Any]]:
    spec_path = CATALOG_TOOLS_ROOT / catalog_tool_id / "toolspec.json"
    if not spec_path.exists():
        return None
    spec = _load_json_object(spec_path, f"ToolSpec {catalog_tool_id}")
    outputs = spec.get("outputs")
    if not isinstance(outputs, dict):
        return None
    return outputs


def _evaluate_pairing(
    *,
    tool_id: str,
    capabilities: ToolCapabilities,
    prediction_rows: list[NetworkRow],
    truth: TruthNetwork,
    level: str,
) -> dict[str, Any]:
    not_applicable_reason = _not_applicable_reason(
        level=level,
        truth=truth,
        capabilities=capabilities,
    )
    if not_applicable_reason is not None:
        return _empty_metric_row(
            tool_id=tool_id,
            capabilities=capabilities,
            context=prediction_rows[0].context,
            truth_context=truth.context,
            level=level,
            status="not_applicable",
            reason=not_applicable_reason,
        )

    truth_scores = _aggregate_rows(truth.rows, level=level)
    prediction_scores = _aggregate_rows(prediction_rows, level=level)
    truth_keys = set(truth_scores)
    predicted_keys = set(prediction_scores)
    candidates = _candidate_keys(
        nodes=_collect_nodes(truth.rows, prediction_rows),
        level=level,
    )
    candidates.update(truth_keys)
    candidates.update(predicted_keys)

    y_true = [1 if key in truth_keys else 0 for key in candidates]
    y_score = [float(prediction_scores.get(key, 0.0)) for key in candidates]
    tp = len(truth_keys & predicted_keys)
    fp = len(predicted_keys - truth_keys)
    fn = len(truth_keys - predicted_keys)
    f1_score = _f1_score(tp=tp, fp=fp, fn=fn)

    auroc = _auroc(y_true, y_score)
    aupr = _average_precision(y_true, y_score)
    status = "ok"
    reason = None
    if auroc is None or aupr is None:
        status = "partial"
        reason = "AUROC/AUPR require at least one positive and one negative candidate"

    return {
        "tool_id": tool_id,
        "catalog_tool_id": capabilities.catalog_tool_id,
        "context": prediction_rows[0].context,
        "truth_context": truth.context,
        "level": level,
        "status": status,
        "reason": reason,
        "auroc": auroc,
        "aupr": aupr,
        "f1_score": f1_score,
        "n_candidates": len(candidates),
        "n_truth_edges": len(truth_keys),
        "n_predicted_edges": len(predicted_keys),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "truth_directed": truth.directed,
        "truth_signed": truth.signed,
        "prediction_directed": capabilities.directed,
        "prediction_signed": capabilities.signed,
    }


def _not_applicable_reason(
    *,
    level: str,
    truth: TruthNetwork,
    capabilities: ToolCapabilities,
) -> Optional[str]:
    if level == "topology":
        return None
    if level == "directed":
        if not truth.directed:
            return "ground-truth network is not directed"
        if not capabilities.directed:
            return "inferred network is not directed"
        return None
    if level == "signed":
        if not truth.directed:
            return "ground-truth network is not directed"
        if not truth.signed:
            return "ground-truth network is not signed"
        if not capabilities.directed:
            return "inferred network is not directed"
        if not capabilities.signed:
            return "inferred network is not signed"
        return None
    raise ValueError(f"Unknown evaluation level: {level}")


def _empty_metric_row(
    *,
    tool_id: str,
    capabilities: ToolCapabilities,
    context: str,
    truth_context: Optional[str],
    level: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "tool_id": tool_id,
        "catalog_tool_id": capabilities.catalog_tool_id,
        "context": context,
        "truth_context": truth_context,
        "level": level,
        "status": status,
        "reason": reason,
        "auroc": None,
        "aupr": None,
        "f1_score": None,
        "n_candidates": 0,
        "n_truth_edges": 0,
        "n_predicted_edges": 0,
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "truth_directed": None,
        "truth_signed": None,
        "prediction_directed": capabilities.directed,
        "prediction_signed": capabilities.signed,
    }


def _aggregate_rows(
    rows: Iterable[NetworkRow], *, level: str
) -> dict[tuple[str, ...], float]:
    scores: dict[tuple[str, ...], float] = {}
    for row in rows:
        if level == "signed" and row.sign not in VALID_SIGNS:
            continue
        key = _edge_key(row.source, row.target, row.sign, level=level)
        score = abs(float(row.score))
        if key not in scores or score > scores[key]:
            scores[key] = score
    return scores


def _edge_key(source: str, target: str, sign: str, *, level: str) -> tuple[str, ...]:
    if level == "topology":
        if source <= target:
            return (source, target)
        return (target, source)
    if level == "directed":
        return (source, target)
    if level == "signed":
        return (source, target, sign)
    raise ValueError(f"Unknown evaluation level: {level}")


def _candidate_keys(
    *,
    nodes: set[str],
    level: str,
) -> set[tuple[str, ...]]:
    candidates: set[tuple[str, ...]] = set()
    sorted_nodes = sorted(nodes)
    if level == "topology":
        for idx, source in enumerate(sorted_nodes):
            for target in sorted_nodes[idx:]:
                candidates.add((source, target))
        return candidates
    if level == "directed":
        for source in sorted_nodes:
            for target in sorted_nodes:
                candidates.add((source, target))
        return candidates
    if level == "signed":
        for source in sorted_nodes:
            for target in sorted_nodes:
                for sign in sorted(VALID_SIGNS):
                    candidates.add((source, target, sign))
        return candidates
    raise ValueError(f"Unknown evaluation level: {level}")


def _collect_nodes(*row_groups: Iterable[NetworkRow]) -> set[str]:
    nodes: set[str] = set()
    for rows in row_groups:
        for row in rows:
            if row.source:
                nodes.add(row.source)
            if row.target:
                nodes.add(row.target)
    return nodes


def _f1_score(*, tp: int, fp: int, fn: int) -> Optional[float]:
    denominator = (2 * tp) + fp + fn
    if denominator == 0:
        return None
    return (2 * tp) / denominator


def _auroc(y_true: list[int], y_score: list[float]) -> Optional[float]:
    positives = sum(y_true)
    negatives = len(y_true) - positives
    if positives == 0 or negatives == 0:
        return None

    order = sorted(range(len(y_score)), key=lambda idx: y_score[idx])
    ranks = [0.0] * len(y_score)
    idx = 0
    while idx < len(order):
        end = idx + 1
        while end < len(order) and y_score[order[end]] == y_score[order[idx]]:
            end += 1
        average_rank = (idx + 1 + end) / 2.0
        for order_idx in range(idx, end):
            ranks[order[order_idx]] = average_rank
        idx = end

    positive_rank_sum = sum(rank for rank, label in zip(ranks, y_true) if label == 1)
    return (positive_rank_sum - (positives * (positives + 1) / 2.0)) / (
        positives * negatives
    )


def _average_precision(y_true: list[int], y_score: list[float]) -> Optional[float]:
    positives = sum(y_true)
    negatives = len(y_true) - positives
    if positives == 0 or negatives == 0:
        return None

    pairs = sorted(zip(y_score, y_true), key=lambda item: item[0], reverse=True)
    tp = 0
    fp = 0
    previous_recall = 0.0
    ap = 0.0
    idx = 0
    while idx < len(pairs):
        score = pairs[idx][0]
        group_tp = 0
        group_fp = 0
        while idx < len(pairs) and pairs[idx][0] == score:
            if pairs[idx][1] == 1:
                group_tp += 1
            else:
                group_fp += 1
            idx += 1
        tp += group_tp
        fp += group_fp
        recall = tp / positives
        precision = tp / (tp + fp)
        ap += (recall - previous_recall) * precision
        previous_recall = recall
    return ap


def _write_plots(
    plots_dir: Path, metrics: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    plots_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[dict[str, Any]] = []
    for metric in METRIC_COLUMNS:
        for level in EVALUATION_LEVELS:
            rows = [row for row in metrics if row.get("level") == level]
            if not rows:
                continue

            heatmap_path = plots_dir / f"{metric}_{level}_heatmap.svg"
            _write_heatmap_svg(path=heatmap_path, rows=rows, metric=metric, level=level)
            outputs.append(
                {
                    "kind": "heatmap",
                    "metric": metric,
                    "level": level,
                    "path": str(heatmap_path),
                }
            )

            valid_rows = [row for row in rows if _metric_value(row, metric) is not None]
            contexts = _sorted_unique(row.get("context") for row in valid_rows)
            tools = _sorted_unique(row.get("tool_id") for row in valid_rows)
            if valid_rows and len(contexts) <= 16 and len(tools) <= 12:
                bars_path = plots_dir / f"{metric}_{level}_grouped_bars.svg"
                _write_grouped_bars_svg(
                    path=bars_path,
                    rows=valid_rows,
                    metric=metric,
                    level=level,
                )
                outputs.append(
                    {
                        "kind": "grouped_bars",
                        "metric": metric,
                        "level": level,
                        "path": str(bars_path),
                    }
                )
    return outputs


def _write_heatmap_svg(
    *,
    path: Path,
    rows: list[dict[str, Any]],
    metric: str,
    level: str,
) -> None:
    contexts = _sorted_unique(row.get("context") for row in rows)
    tools = _sorted_unique(row.get("tool_id") for row in rows)
    values = {
        (str(row.get("tool_id")), str(row.get("context"))): _metric_value(row, metric)
        for row in rows
    }
    statuses = {
        (str(row.get("tool_id")), str(row.get("context"))): str(row.get("status", ""))
        for row in rows
    }
    reasons = {
        (str(row.get("tool_id")), str(row.get("context"))): str(row.get("reason") or "")
        for row in rows
    }

    cell_w = 74
    cell_h = 34
    left = max(150, min(320, 18 + max((len(tool) for tool in tools), default=0) * 8))
    top = 96
    right = 34
    bottom = 82
    width = left + (cell_w * len(contexts)) + right
    height = top + (cell_h * len(tools)) + bottom

    parts = [_svg_header(width, height)]
    parts.append(
        _svg_text(24, 34, f"{_metric_label(metric)} - {level}", size=18, weight="700")
    )
    parts.append(
        _svg_text(
            24,
            58,
            "Grey cells are not applicable or unavailable.",
            size=12,
            fill="#475569",
        )
    )
    for col_idx, context in enumerate(contexts):
        x = left + (col_idx * cell_w) + (cell_w / 2)
        parts.append(
            _svg_text(
                x,
                top - 16,
                _short_label(context, 13),
                size=11,
                anchor="middle",
                fill="#334155",
            )
        )
    for row_idx, tool in enumerate(tools):
        y = top + (row_idx * cell_h)
        parts.append(
            _svg_text(
                left - 10,
                y + 22,
                _short_label(tool, 32),
                size=12,
                anchor="end",
                fill="#0f172a",
            )
        )
        for col_idx, context in enumerate(contexts):
            x = left + (col_idx * cell_w)
            value = values.get((tool, context))
            status = statuses.get((tool, context), "")
            reason = reasons.get((tool, context), "")
            fill = _heat_color(value) if value is not None else "#e5e7eb"
            title = f"{tool} | {context} | {metric}/{level}"
            if value is not None:
                title += f" = {value:.4f}"
            elif reason:
                title += f" | {reason}"
            elif status:
                title += f" | {status}"
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w - 2}" height="{cell_h - 2}" '
                f'rx="2" fill="{fill}" stroke="#ffffff" stroke-width="1">'
                f"<title>{html.escape(title)}</title></rect>"
            )
            if value is not None:
                parts.append(
                    _svg_text(
                        x + (cell_w / 2) - 1,
                        y + 21,
                        f"{value:.2f}",
                        size=11,
                        anchor="middle",
                        fill=_contrast_color(value),
                    )
                )
            else:
                parts.append(
                    _svg_text(
                        x + (cell_w / 2) - 1,
                        y + 21,
                        "NA",
                        size=10,
                        anchor="middle",
                        fill="#64748b",
                    )
                )

    legend_x = left
    legend_y = height - 42
    parts.append(_svg_text(legend_x, legend_y - 10, "0", size=11, fill="#475569"))
    for idx in range(80):
        value = idx / 79
        parts.append(
            f'<rect x="{legend_x + 18 + idx * 2:.1f}" y="{legend_y - 20}" '
            f'width="2" height="12" fill="{_heat_color(value)}" />'
        )
    parts.append(_svg_text(legend_x + 184, legend_y - 10, "1", size=11, fill="#475569"))
    parts.append(
        f'<rect x="{legend_x + 218}" y="{legend_y - 20}" width="18" height="12" '
        'fill="#e5e7eb" stroke="#cbd5e1" />'
    )
    parts.append(
        _svg_text(legend_x + 242, legend_y - 10, "NA", size=11, fill="#475569")
    )
    parts.append("</svg>\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_grouped_bars_svg(
    *,
    path: Path,
    rows: list[dict[str, Any]],
    metric: str,
    level: str,
) -> None:
    contexts = _sorted_unique(row.get("context") for row in rows)
    tools = _sorted_unique(row.get("tool_id") for row in rows)
    values = {
        (str(row.get("tool_id")), str(row.get("context"))): _metric_value(row, metric)
        for row in rows
    }
    palette = _tool_palette(tools)
    left = 76
    top = 76
    plot_w = max(520, 92 * len(contexts))
    plot_h = 320
    legend_h = 28 * math.ceil(max(1, len(tools)) / 3)
    bottom = 108 + legend_h
    width = left + plot_w + 34
    height = top + plot_h + bottom
    baseline = top + plot_h
    group_w = plot_w / max(1, len(contexts))
    slot_w = min(22, (group_w * 0.76) / max(1, len(tools)))

    parts = [_svg_header(width, height)]
    parts.append(
        _svg_text(24, 34, f"{_metric_label(metric)} - {level}", size=18, weight="700")
    )
    parts.append(
        _svg_text(24, 58, "Grouped by evaluation context.", size=12, fill="#475569")
    )

    for tick in range(6):
        value = tick / 5
        y = baseline - (value * plot_h)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
            'stroke="#e2e8f0" stroke-width="1" />'
        )
        parts.append(
            _svg_text(
                left - 10, y + 4, f"{value:.1f}", size=11, anchor="end", fill="#64748b"
            )
        )
    parts.append(
        f'<line x1="{left}" y1="{baseline}" x2="{left + plot_w}" y2="{baseline}" '
        'stroke="#334155" stroke-width="1" />'
    )
    parts.append(
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{baseline}" '
        'stroke="#334155" stroke-width="1" />'
    )

    for context_idx, context in enumerate(contexts):
        group_x = left + (context_idx * group_w)
        bars_w = slot_w * len(tools)
        start_x = group_x + ((group_w - bars_w) / 2)
        for tool_idx, tool in enumerate(tools):
            value = values.get((tool, context))
            if value is None:
                continue
            bar_h = max(0.0, min(1.0, value)) * plot_h
            x = start_x + (tool_idx * slot_w)
            y = baseline - bar_h
            title = f"{tool} | {context} | {metric}/{level} = {value:.4f}"
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(2, slot_w - 2):.1f}" '
                f'height="{bar_h:.1f}" fill="{palette[tool]}" rx="2">'
                f"<title>{html.escape(title)}</title></rect>"
            )
        parts.append(
            _svg_text(
                group_x + (group_w / 2),
                baseline + 22,
                _short_label(context, 12),
                size=11,
                anchor="middle",
                fill="#334155",
            )
        )

    legend_x = left
    legend_y = baseline + 56
    for idx, tool in enumerate(tools):
        col = idx % 3
        row = idx // 3
        x = legend_x + (col * 220)
        y = legend_y + (row * 24)
        parts.append(
            f'<rect x="{x}" y="{y - 11}" width="12" height="12" fill="{palette[tool]}" rx="2" />'
        )
        parts.append(
            _svg_text(x + 18, y, _short_label(tool, 24), size=12, fill="#334155")
        )
    parts.append("</svg>\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def _metric_value(row: dict[str, Any], metric: str) -> Optional[float]:
    value = row.get(metric)
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return max(0.0, min(1.0, numeric))


def _sorted_unique(values: Iterable[Any]) -> list[str]:
    return sorted(
        {str(value) for value in values if value not in (None, "")},
        key=_plot_sort_key,
    )


def _plot_sort_key(value: str) -> tuple[int, str]:
    if value == "global":
        return (0, value)
    if value.startswith("group:"):
        return (1, value.removeprefix("group:").lower())
    return (2, value.lower())


def _heat_color(value: Optional[float]) -> str:
    if value is None:
        return "#e5e7eb"
    stops = [
        (0.0, (247, 251, 255)),
        (0.35, (198, 219, 239)),
        (0.7, (107, 174, 214)),
        (1.0, (8, 81, 156)),
    ]
    return _interpolate_stops(max(0.0, min(1.0, value)), stops)


def _interpolate_stops(
    value: float, stops: list[tuple[float, tuple[int, int, int]]]
) -> str:
    for idx in range(len(stops) - 1):
        left_value, left_color = stops[idx]
        right_value, right_color = stops[idx + 1]
        if value <= right_value:
            span = right_value - left_value
            ratio = 0.0 if span <= 0 else (value - left_value) / span
            rgb = tuple(
                int(
                    round(
                        left_color[channel]
                        + ((right_color[channel] - left_color[channel]) * ratio)
                    )
                )
                for channel in range(3)
            )
            return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    rgb = stops[-1][1]
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _contrast_color(value: float) -> str:
    return "#ffffff" if value >= 0.62 else "#0f172a"


def _tool_palette(tools: list[str]) -> dict[str, str]:
    colors = [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#9467bd",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
        "#bcbd22",
        "#17becf",
        "#4c78a8",
        "#f58518",
    ]
    return {tool: colors[idx % len(colors)] for idx, tool in enumerate(tools)}


def _svg_header(width: float, height: float) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img">'
        '<rect width="100%" height="100%" fill="#ffffff" />'
    )


def _svg_text(
    x: float,
    y: float,
    text: str,
    *,
    size: int,
    fill: str = "#0f172a",
    weight: str = "400",
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-family="Inter, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">'
        f"{html.escape(text)}</text>"
    )


def _metric_label(metric: str) -> str:
    return {
        "auroc": "AUROC",
        "aupr": "AUPR",
        "f1_score": "F1-score",
    }.get(metric, metric)


def _short_label(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _normalize_sign(value: str) -> str:
    normalized = value.strip()
    if normalized in {"+", "1", "activation", "positive"}:
        return "+"
    if normalized in {"-", "-1", "repression", "negative"}:
        return "-"
    return "?"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = _fieldnames(rows)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".12g")
    return value
