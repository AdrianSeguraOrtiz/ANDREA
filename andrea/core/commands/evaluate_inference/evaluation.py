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
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Optional

from andrea.core.shared.json_io import load_json_object as _load_json_object
from andrea.core.shared.issues import issue_messages
from andrea.core.shared.paths import report_path

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
METRIC_COLUMNS = ["auroc", "aupr", "f1_at_truth_count", "epr_at_truth_count"]
VALID_SIGNS = {"+", "-"}
CATALOG_TOOLS_ROOT = (
    Path(__file__).resolve().parents[3] / "catalog_inference_tools" / "tools"
)
VIEW_ASSETS_PACKAGE = "andrea.core.commands.evaluate_inference.view_assets"


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
    generate_view: bool = True,
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
    view_html_path = evaluation_dir / "evaluation_view.html"

    _write_csv(metrics_csv_path, metrics)
    _write_csv(pairings_csv_path, pairings)

    report = {
        "schema_version": "1.0",
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {
            "inference_run_id": run_report.get("run_id"),
            "inference_dataset_id": (
                run_report.get("dataset", {}).get("id")
                if isinstance(run_report.get("dataset"), dict)
                else None
            ),
            "ground_truth_dataset_id": manifest.get("dataset_id"),
            "ground_truth_simulator_id": manifest.get("simulator_id"),
            "merged_network": "merged_network_raw",
        },
        "inference_run": {
            "run_id": run_report.get("run_id"),
            "status": run_report.get("status"),
            "dataset": run_report.get("dataset"),
            "execution": run_report.get("execution"),
            "warnings": issue_messages(run_report.get("issues", []), severity="warn"),
        },
        "ground_truth": {
            "dataset_id": manifest.get("dataset_id"),
            "simulator_id": manifest.get("simulator_id"),
            "profile": manifest.get("profile"),
            "contexts": sorted(truth_networks.keys()),
        },
        "outputs": {
            "output_root": ".",
            "evaluation_dir": report_path(evaluation_dir, base_dir=output_root),
            "metrics_csv": report_path(metrics_csv_path, base_dir=output_root),
            "pairings_csv": report_path(pairings_csv_path, base_dir=output_root),
            "evaluation_report": report_path(report_json_path, base_dir=output_root),
            "evaluation_view": (
                report_path(view_html_path, base_dir=output_root)
                if generate_view
                else None
            ),
        },
        "pairings": pairings,
        "metrics": metrics,
    }
    report_json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    if generate_view:
        _write_evaluation_view(view_html_path, report)
    return report


def _resolve_merged_network_path_from_run_report(
    *,
    run_report_path: Path,
    run_report: dict[str, Any],
) -> Path:
    outputs = run_report.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("Run report must contain an object at outputs")
    raw_path = outputs.get("merged_network_raw")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(
            "Run report outputs.merged_network_raw is required for evaluation"
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
            if score <= 0.0:
                raise ValueError(
                    f"Inferred network CSV has non-positive score at line {line_number}: "
                    f"{row.get('score')!r}; score must be a positive magnitude and sign must be stored in the sign column"
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
            if not math.isfinite(score):
                raise ValueError(
                    f"Ground-truth network CSV has non-finite score at line {line_number}: "
                    f"{raw_score!r}"
                )
            if score < 0.0:
                raise ValueError(
                    f"Ground-truth network CSV has negative score at line {line_number}: "
                    f"{raw_score!r}; score must be a non-negative truth label or magnitude"
                )
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
    top_k_stats = _top_truth_count_stats(
        prediction_scores=prediction_scores,
        truth_keys=truth_keys,
        n_candidates=len(candidates),
    )

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
        "f1_at_truth_count": top_k_stats["f1_at_truth_count"],
        "epr_at_truth_count": top_k_stats["epr_at_truth_count"],
        "n_candidates": len(candidates),
        "n_truth_edges": len(truth_keys),
        "n_predicted_edges": len(predicted_keys),
        "tp_at_truth_count": top_k_stats["tp_at_truth_count"],
        "fp_at_truth_count": top_k_stats["fp_at_truth_count"],
        "fn_at_truth_count": top_k_stats["fn_at_truth_count"],
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
        "f1_at_truth_count": None,
        "epr_at_truth_count": None,
        "n_candidates": 0,
        "n_truth_edges": 0,
        "n_predicted_edges": 0,
        "tp_at_truth_count": 0,
        "fp_at_truth_count": 0,
        "fn_at_truth_count": 0,
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
        score = float(row.score)
        if score <= 0.0:
            continue
        key = _edge_key(row.source, row.target, row.sign, level=level)
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
            for target in sorted_nodes[idx + 1 :]:
                candidates.add((source, target))
        return candidates
    if level == "directed":
        for source in sorted_nodes:
            for target in sorted_nodes:
                if source == target:
                    continue
                candidates.add((source, target))
        return candidates
    if level == "signed":
        for source in sorted_nodes:
            for target in sorted_nodes:
                if source == target:
                    continue
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


def _top_truth_count_stats(
    *,
    prediction_scores: dict[tuple[str, ...], float],
    truth_keys: set[tuple[str, ...]],
    n_candidates: int,
) -> dict[str, Any]:
    k = len(truth_keys)
    if k == 0:
        return {
            "f1_at_truth_count": None,
            "epr_at_truth_count": None,
            "tp_at_truth_count": 0,
            "fp_at_truth_count": 0,
            "fn_at_truth_count": 0,
        }

    ranked_predictions = sorted(
        prediction_scores,
        key=lambda key: (-float(prediction_scores[key]), key),
    )
    top_keys = set(ranked_predictions[:k])
    tp = len(top_keys & truth_keys)
    fp = k - tp
    fn = k - tp
    denominator = (2 * tp) + fp + fn
    f1 = None if denominator == 0 else (2 * tp) / denominator
    early_precision = tp / k
    random_precision = k / n_candidates if n_candidates > 0 else None
    epr = (
        None
        if random_precision is None or random_precision == 0
        else early_precision / random_precision
    )
    return {
        "f1_at_truth_count": f1,
        "epr_at_truth_count": epr,
        "tp_at_truth_count": tp,
        "fp_at_truth_count": fp,
        "fn_at_truth_count": fn,
    }


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


def _write_evaluation_view(path: Path, report: dict[str, Any]) -> None:
    path.write_text(_evaluation_view_html(report), encoding="utf-8")


def _evaluation_view_html(report: dict[str, Any]) -> str:
    title = "ANDREA Inference Evaluation"
    dataset = report.get("ground_truth", {}).get("dataset_id")
    if dataset:
        title = f"{title} - {dataset}"
    report_json = json.dumps(report, ensure_ascii=True).replace("</", "<\\/")
    return (
        _read_view_asset("template.html")
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace("__STYLE__", _read_view_asset("view.css"))
        .replace("__SCRIPT__", _read_view_asset("view.js"))
        .replace("__REPORT_JSON__", report_json)
    )


def _read_view_asset(name: str) -> str:
    return (
        resources.files(VIEW_ASSETS_PACKAGE)
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


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
