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
from andrea.core.shared.network_context import (
    network_context_counts_by_family,
    network_context_sort_key,
    normalize_network_context,
    normalize_network_sign,
)
from andrea.core.shared.paths import report_path
from andrea.core.shared.runtime_profile import ProgressCallback, RuntimeProfile

MERGED_NETWORK_REQUIRED_COLUMNS = [
    "source",
    "target",
    "score",
    "sign",
    "evidence",
    "context",
    "tool_id",
]
TRUTH_NETWORK_REQUIRED_COLUMNS = [
    "source",
    "target",
    "score",
    "sign",
    "evidence",
    "context",
]
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
    candidate_genes: set[str]
    directed: bool
    signed: bool


@dataclass(frozen=True)
class TruthLevelCache:
    truth_scores: dict[tuple[str, ...], float]
    truth_keys: set[tuple[str, ...]]
    candidate_genes: set[str]
    n_candidates: int


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
    progress_callback: Optional[ProgressCallback] = None,
) -> dict[str, Any]:
    """Evaluate merged inferred networks against a ground-truth manifest."""
    runtime_profile = RuntimeProfile(progress_callback)
    run_report_path = run_report_path.resolve()
    ground_truth_manifest_path = ground_truth_manifest_path.resolve()
    output_root = output_dir.resolve()
    created_at = datetime.now(timezone.utc)

    with runtime_profile.stage(
        "loading_run_report",
        label="Loading run report",
        detail="Reading infer-network run_report.json.",
    ):
        run_report = _load_json_object(run_report_path, "Run report")
        merged_network_path = _resolve_merged_network_path_from_run_report(
            run_report_path=run_report_path,
            run_report=run_report,
        )

    with runtime_profile.stage(
        "loading_inferred_network",
        label="Loading inferred network",
        detail="Reading merged inferred network CSV.",
    ):
        inferred_rows = _load_inferred_rows(merged_network_path)

    with runtime_profile.stage(
        "loading_truth_networks",
        label="Loading ground truth",
        detail="Reading ground-truth manifest and truth networks.",
    ):
        truth_networks, manifest = _load_truth_networks(ground_truth_manifest_path)

    with runtime_profile.stage(
        "preparing_evaluation_inputs",
        label="Preparing evaluation inputs",
        detail="Resolving tool capabilities and output directory.",
    ):
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
    with runtime_profile.stage(
        "computing",
        label="Evaluating metrics",
        detail="Scoring inferred networks against matching truth contexts.",
    ):
        grouped_predictions = _group_inferred_rows(inferred_rows)
        truth_level_cache: dict[tuple[str, str], TruthLevelCache] = {}

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
                        truth_cache=_truth_level_cache(
                            truth=truth,
                            level=level,
                            cache=truth_level_cache,
                        ),
                    )
                )

        context_matching = _context_matching_summary(
            truth_networks=truth_networks,
            grouped_predictions=grouped_predictions,
            tool_capabilities=tool_capabilities,
            pairings=pairings,
        )
    metrics_csv_path = evaluation_dir / "metrics.csv"
    pairings_csv_path = evaluation_dir / "pairings.csv"
    report_json_path = evaluation_dir / "evaluation_report.json"
    view_html_path = evaluation_dir / "evaluation_view.html"

    with runtime_profile.stage(
        "writing_outputs",
        label="Writing outputs",
        detail="Writing metrics, report and HTML view.",
    ):
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
            "data_axes": manifest.get("data_axes"),
            "truth_requirements": manifest.get("truth_requirements"),
            "contexts": sorted(truth_networks.keys(), key=network_context_sort_key),
            "context_counts_by_family": _context_counts_by_family(
                truth_networks.keys()
            ),
            "gene_universe_size": (
                len(next(iter(truth_networks.values())).candidate_genes)
                if truth_networks
                else 0
            ),
        },
        "context_matching": context_matching,
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
        "runtime_profile": runtime_profile.timings(),
        "pairings": pairings,
        "metrics": metrics,
    }
    report_json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    if generate_view:
        _write_evaluation_view(view_html_path, report)
    return report


def _context_matching_summary(
    *,
    truth_networks: dict[str, TruthNetwork],
    grouped_predictions: dict[tuple[str, str], list[NetworkRow]],
    tool_capabilities: dict[str, ToolCapabilities],
    pairings: list[dict[str, Any]],
) -> dict[str, Any]:
    truth_contexts = set(truth_networks)
    predicted_contexts_by_tool: dict[str, set[str]] = defaultdict(set)
    for tool_id, context in grouped_predictions:
        predicted_contexts_by_tool[tool_id].add(context)
    all_predicted_contexts = {
        context
        for contexts in predicted_contexts_by_tool.values()
        for context in contexts
    }
    unmatched_prediction_contexts = [
        {
            "tool_id": str(pairing.get("tool_id")),
            "catalog_tool_id": str(pairing.get("catalog_tool_id")),
            "context": str(pairing.get("context")),
            "reason": str(pairing.get("reason") or ""),
        }
        for pairing in pairings
        if pairing.get("status") == "skipped"
    ]
    truth_without_any_prediction = sorted(
        truth_contexts - all_predicted_contexts,
        key=network_context_sort_key,
    )
    missing_by_tool = []
    for tool_id in sorted(predicted_contexts_by_tool):
        missing = sorted(
            truth_contexts - predicted_contexts_by_tool[tool_id],
            key=network_context_sort_key,
        )
        if not missing:
            continue
        capabilities = tool_capabilities[tool_id]
        missing_by_tool.append(
            {
                "tool_id": tool_id,
                "catalog_tool_id": capabilities.catalog_tool_id,
                "missing_context_count": len(missing),
                "missing_context_counts_by_family": _context_counts_by_family(missing),
                "sample_contexts": missing[:25],
            }
        )
    return {
        "truth_context_count": len(truth_contexts),
        "prediction_context_count": len(all_predicted_contexts),
        "truth_context_counts_by_family": _context_counts_by_family(truth_contexts),
        "prediction_context_counts_by_family": _context_counts_by_family(
            all_predicted_contexts
        ),
        "prediction_contexts_without_truth_count": len(unmatched_prediction_contexts),
        "prediction_contexts_without_truth": unmatched_prediction_contexts[:25],
        "truth_contexts_without_any_prediction_count": len(
            truth_without_any_prediction
        ),
        "truth_contexts_without_any_prediction_sample": truth_without_any_prediction[
            :25
        ],
        "missing_truth_contexts_by_tool": missing_by_tool,
    }


def _context_counts_by_family(contexts: Iterable[str]) -> dict[str, int]:
    return network_context_counts_by_family(contexts)


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
            source = str(row["source"]).strip()
            target = str(row["target"]).strip()
            evidence = str(row["evidence"]).strip()
            context = normalize_network_context(
                row["context"],
                source=f"Inferred network CSV line {line_number}",
            )
            tool_id = str(row["tool_id"]).strip()
            if not source or not target:
                raise ValueError(
                    f"Inferred network CSV has empty source or target at line {line_number}: {path}"
                )
            if not evidence:
                raise ValueError(
                    f"Inferred network CSV has empty evidence at line {line_number}: {path}"
                )
            if not tool_id:
                raise ValueError(
                    f"Inferred network CSV has empty tool_id at line {line_number}: {path}"
                )
            sign = normalize_network_sign(
                row["sign"],
                source=f"Inferred network CSV line {line_number}",
            )
            rows.append(
                NetworkRow(
                    source=source,
                    target=target,
                    score=score,
                    sign=sign,
                    context=context,
                    tool_id=tool_id,
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
    gene_universe_raw = outputs.get("gene_universe")
    if not isinstance(gene_universe_raw, str) or not gene_universe_raw.strip():
        raise ValueError("Ground-truth manifest outputs.gene_universe is required")
    networks_raw = outputs.get("networks")
    if not isinstance(networks_raw, str) or not networks_raw.strip():
        raise ValueError("Ground-truth manifest outputs.networks is required")
    candidate_genes = _load_gene_universe(
        _resolve_manifest_path(base_dir, gene_universe_raw)
    )
    networks = _load_truth_network_table(
        path=_resolve_manifest_path(base_dir, networks_raw),
        candidate_genes=candidate_genes,
    )

    if not networks:
        raise ValueError(
            f"Ground-truth manifest references no truth networks: {manifest_path}"
        )
    return networks, manifest


def _load_gene_universe(path: Path) -> set[str]:
    if not path.exists() or not path.is_file():
        raise ValueError(f"Ground-truth gene universe file not found: {path}")
    genes: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        gene = line.strip()
        if not gene:
            continue
        if gene in seen:
            continue
        seen.add(gene)
        genes.append(gene)
    if not genes:
        raise ValueError(f"Ground-truth gene universe file contains no genes: {path}")
    return set(genes)


def _load_truth_network_table(
    *,
    path: Path,
    candidate_genes: set[str],
) -> dict[str, TruthNetwork]:
    if not path.exists() or not path.is_file():
        raise ValueError(f"Ground-truth network CSV not found: {path}")

    rows_by_context: dict[str, list[NetworkRow]] = defaultdict(list)
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        missing = [col for col in TRUTH_NETWORK_REQUIRED_COLUMNS if col not in headers]
        if missing:
            raise ValueError(
                f"Ground-truth network CSV is missing required columns {missing}: {path}"
            )
        for line_number, row in enumerate(reader, start=2):
            raw_score = row["score"]
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
            if score <= 0.0:
                raise ValueError(
                    f"Ground-truth network CSV has non-positive score at line {line_number}: "
                    f"{raw_score!r}; score must be a positive truth label or magnitude"
                )
            source = str(row["source"]).strip()
            target = str(row["target"]).strip()
            context = normalize_network_context(
                row["context"],
                source=f"Ground-truth network CSV line {line_number}",
            )
            evidence = str(row["evidence"]).strip()
            if not source or not target:
                raise ValueError(
                    f"Ground-truth network CSV has empty source or target at line {line_number}: {path}"
                )
            if source == target:
                raise ValueError(
                    f"Ground-truth network CSV has a self-loop at line {line_number}: {source!r}"
                )
            if source not in candidate_genes or target not in candidate_genes:
                raise ValueError(
                    f"Ground-truth network CSV line {line_number} references genes outside outputs.gene_universe: "
                    f"{source!r}, {target!r}"
                )
            if not evidence:
                raise ValueError(
                    f"Ground-truth network CSV has empty evidence at line {line_number}: {path}"
                )
            sign = normalize_network_sign(
                row["sign"],
                source=f"Ground-truth network CSV line {line_number}",
            )
            rows_by_context[context].append(
                NetworkRow(
                    source=source,
                    target=target,
                    score=score,
                    sign=sign,
                    context=context,
                )
            )
    if not rows_by_context:
        raise ValueError(f"Ground-truth network CSV contains no rows: {path}")
    return {
        context: TruthNetwork(
            context=context,
            path=path,
            rows=rows,
            candidate_genes=set(candidate_genes),
            directed=True,
            signed=any(row.sign in VALID_SIGNS for row in rows),
        )
        for context, rows in rows_by_context.items()
    }


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
    truth_cache: Optional[TruthLevelCache] = None,
) -> dict[str, Any]:
    not_applicable_reason = _not_applicable_reason(
        level=level,
        truth=truth,
        capabilities=capabilities,
    )
    if not_applicable_reason is not None:
        truth_count_cache = None
        if _truth_supports_level(truth=truth, level=level):
            truth_count_cache = (
                truth_cache
                if truth_cache is not None
                else _build_truth_level_cache(truth=truth, level=level)
            )
        return _empty_metric_row(
            tool_id=tool_id,
            capabilities=capabilities,
            context=prediction_rows[0].context,
            truth_context=truth.context,
            level=level,
            status="not_applicable",
            reason=not_applicable_reason,
            truth=truth,
            truth_cache=truth_count_cache,
        )

    if truth_cache is None:
        truth_cache = _build_truth_level_cache(truth=truth, level=level)
    prediction_scores = _aggregate_rows(prediction_rows, level=level)
    truth_keys = truth_cache.truth_keys
    predicted_keys = set(prediction_scores)
    truth_outside = [
        key
        for key in truth_keys
        if not _is_candidate_key(key, candidate_genes=truth_cache.candidate_genes, level=level)
    ]
    if truth_outside:
        raise ValueError(
            "Ground-truth edges include genes outside outputs.gene_universe for "
            f"context {truth.context!r}, level {level!r}"
        )
    predicted_outside = [
        key
        for key in predicted_keys
        if not _is_candidate_key(key, candidate_genes=truth_cache.candidate_genes, level=level)
    ]
    if predicted_outside:
        example = next(iter(sorted(predicted_outside)))
        raise ValueError(
            "Inferred network contains edges outside the ground-truth gene universe "
            f"for context {prediction_rows[0].context!r}, level {level!r}; example edge: {example}"
        )

    top_k_stats = _top_truth_count_stats(
        prediction_scores=prediction_scores,
        truth_keys=truth_keys,
        n_candidates=truth_cache.n_candidates,
    )

    auroc = _sparse_auroc(
        truth_keys=truth_keys,
        prediction_scores=prediction_scores,
        n_candidates=truth_cache.n_candidates,
    )
    aupr = _sparse_average_precision(
        truth_keys=truth_keys,
        prediction_scores=prediction_scores,
        n_candidates=truth_cache.n_candidates,
    )
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
        "n_candidates": truth_cache.n_candidates,
        "n_candidate_genes": len(truth.candidate_genes),
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


def _truth_level_cache(
    *,
    truth: TruthNetwork,
    level: str,
    cache: dict[tuple[str, str], TruthLevelCache],
) -> TruthLevelCache:
    key = (truth.context, level)
    cached = cache.get(key)
    if cached is None:
        cached = _build_truth_level_cache(truth=truth, level=level)
        cache[key] = cached
    return cached


def _build_truth_level_cache(*, truth: TruthNetwork, level: str) -> TruthLevelCache:
    truth_scores = _aggregate_rows(truth.rows, level=level)
    truth_keys = set(truth_scores)
    n_candidates = _candidate_count(nodes=truth.candidate_genes, level=level)
    return TruthLevelCache(
        truth_scores=truth_scores,
        truth_keys=truth_keys,
        candidate_genes=set(truth.candidate_genes),
        n_candidates=n_candidates,
    )


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


def _truth_supports_level(*, truth: TruthNetwork, level: str) -> bool:
    if level == "topology":
        return True
    if level == "directed":
        return truth.directed
    if level == "signed":
        return truth.directed and truth.signed
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
    truth: Optional[TruthNetwork] = None,
    truth_cache: Optional[TruthLevelCache] = None,
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
        "n_candidates": truth_cache.n_candidates if truth_cache else 0,
        "n_candidate_genes": len(truth.candidate_genes) if truth else 0,
        "n_truth_edges": len(truth_cache.truth_keys) if truth_cache else 0,
        "n_predicted_edges": 0,
        "tp_at_truth_count": 0,
        "fp_at_truth_count": 0,
        "fn_at_truth_count": 0,
        "truth_directed": truth.directed if truth else None,
        "truth_signed": truth.signed if truth else None,
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


def _candidate_count(*, nodes: set[str], level: str) -> int:
    n = len(nodes)
    if level == "topology":
        return n * (n - 1) // 2
    if level == "directed":
        return n * (n - 1)
    if level == "signed":
        return n * (n - 1) * len(VALID_SIGNS)
    raise ValueError(f"Unknown evaluation level: {level}")


def _is_candidate_key(
    key: tuple[str, ...],
    *,
    candidate_genes: set[str],
    level: str,
) -> bool:
    if level == "topology":
        return (
            len(key) == 2
            and key[0] in candidate_genes
            and key[1] in candidate_genes
            and key[0] != key[1]
        )
    if level == "directed":
        return (
            len(key) == 2
            and key[0] in candidate_genes
            and key[1] in candidate_genes
            and key[0] != key[1]
        )
    if level == "signed":
        return (
            len(key) == 3
            and key[0] in candidate_genes
            and key[1] in candidate_genes
            and key[0] != key[1]
            and key[2] in VALID_SIGNS
        )
    raise ValueError(f"Unknown evaluation level: {level}")


def _score_groups(
    *,
    truth_keys: set[tuple[str, ...]],
    prediction_scores: dict[tuple[str, ...], float],
    n_candidates: int,
    ascending: bool,
) -> list[tuple[float, int, int]]:
    grouped: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    nonzero_tp = 0
    for key, score in prediction_scores.items():
        group = grouped[float(score)]
        group[1] += 1
        if key in truth_keys:
            group[0] += 1
            nonzero_tp += 1

    zero_total = n_candidates - len(prediction_scores)
    if zero_total > 0:
        zero_tp = len(truth_keys) - nonzero_tp
        group = grouped[0.0]
        group[0] += zero_tp
        group[1] += zero_total

    return [
        (score, counts[0], counts[1])
        for score, counts in sorted(grouped.items(), reverse=not ascending)
        if counts[1] > 0
    ]


def _sparse_auroc(
    *,
    truth_keys: set[tuple[str, ...]],
    prediction_scores: dict[tuple[str, ...], float],
    n_candidates: int,
) -> Optional[float]:
    positives = len(truth_keys)
    negatives = n_candidates - positives
    if positives == 0 or negatives == 0:
        return None

    positive_rank_sum = 0.0
    next_rank = 1
    for _score, group_tp, group_total in _score_groups(
        truth_keys=truth_keys,
        prediction_scores=prediction_scores,
        n_candidates=n_candidates,
        ascending=True,
    ):
        average_rank = (next_rank + next_rank + group_total - 1) / 2.0
        positive_rank_sum += group_tp * average_rank
        next_rank += group_total

    return (positive_rank_sum - (positives * (positives + 1) / 2.0)) / (
        positives * negatives
    )


def _sparse_average_precision(
    *,
    truth_keys: set[tuple[str, ...]],
    prediction_scores: dict[tuple[str, ...], float],
    n_candidates: int,
) -> Optional[float]:
    positives = len(truth_keys)
    negatives = n_candidates - positives
    if positives == 0 or negatives == 0:
        return None

    tp = 0
    fp = 0
    previous_recall = 0.0
    ap = 0.0
    for _score, group_tp, group_total in _score_groups(
        truth_keys=truth_keys,
        prediction_scores=prediction_scores,
        n_candidates=n_candidates,
        ascending=False,
    ):
        group_fp = group_total - group_tp
        tp += group_tp
        fp += group_fp
        recall = tp / positives
        precision = tp / (tp + fp)
        ap += (recall - previous_recall) * precision
        previous_recall = recall
    return ap


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
