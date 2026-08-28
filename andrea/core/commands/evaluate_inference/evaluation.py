"""Evaluate inferred GRNs against generated benchmark truth networks."""

from __future__ import annotations

import csv
import html
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Optional

from andrea.core.shared.dataset_identity import validate_dataset_fingerprint
from andrea.core.shared.issues import issue_messages
from andrea.core.shared.json_io import (
    load_json_object as _load_json_object,
    validate_json_instance,
)
from andrea.core.shared.network_context import (
    network_context_counts_by_family,
    network_context_family,
    network_context_sort_key,
    normalize_network_context,
    normalize_network_sign,
)
from andrea.core.shared.output_capabilities import (
    validate_final_inference_report,
    validate_frozen_output_capabilities,
)
from andrea.core.shared.paths import (
    report_path,
    resolve_safe_manifest_path,
    validate_safe_relative_posix_path,
)
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
VIEW_ASSETS_PACKAGE = "andrea.core.commands.evaluate_inference.view_assets"
GROUND_TRUTH_SCHEMA_PACKAGE = "andrea.catalog_simulation_data_tools"
GROUND_TRUTH_SCHEMA_RESOURCE = "schemas/ground-truth-manifest.schema.json"


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
    candidate_space: CandidateSpace
    directed: bool
    signed: bool


@dataclass(frozen=True)
class CandidateSpace:
    gene_universe: set[str]
    sources: set[str]
    targets: set[str]
    allow_self_edges: bool
    mode: str
    sources_reference: str
    targets_reference: str


@dataclass(frozen=True)
class TruthLevelCache:
    truth_scores: dict[tuple[str, ...], float]
    truth_keys: set[tuple[str, ...]]
    excluded_truth_scores: dict[tuple[str, ...], float]
    included_rows: list[NetworkRow]
    excluded_rows: list[NetworkRow]
    candidate_space: CandidateSpace
    n_candidates: int


@dataclass(frozen=True)
class ToolCapabilities:
    catalog_tool_id: str
    tool_origin: str
    directed: bool
    signed: bool
    sign: str


@dataclass(frozen=True)
class CandidateFilter:
    included_rows: list[NetworkRow]
    excluded_rows: list[NetworkRow]


def validate_inference_analysis_inputs(*, run_report_path: Path) -> None:
    """Validate an infer-network analysis handoff without producing outputs."""
    resolved_report_path = run_report_path.resolve()
    run_report = _load_json_object(resolved_report_path, "Run report")
    merged_network_path = _resolve_merged_network_path_from_run_report(
        run_report_path=resolved_report_path,
        run_report=run_report,
    )
    inferred_rows = _load_inferred_rows(merged_network_path)
    _resolve_tool_capabilities(
        inferred_rows=inferred_rows,
        run_report=run_report,
    )


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
        _validate_dataset_identity(
            run_report=run_report,
            truth_manifest=manifest,
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
        _add_completed_contexts(
            grouped_predictions=grouped_predictions,
            run_report=run_report,
        )
        truth_level_cache: dict[tuple[str, str], TruthLevelCache] = {}

        for (tool_id, context), prediction_rows in sorted(grouped_predictions.items()):
            truth = truth_networks.get(context)
            if truth is None:
                reason = f"no ground-truth network for context {context!r}"
                pairings.append(
                    {
                        "tool_id": tool_id,
                        "catalog_tool_id": tool_capabilities[tool_id].catalog_tool_id,
                        "tool_origin": tool_capabilities[tool_id].tool_origin,
                        "context": context,
                        "status": "skipped",
                        "reason": reason,
                        "n_prediction_rows": len(prediction_rows),
                        **{
                            f"n_prediction_rows_outside_candidate_space_{level}": None
                            for level in EVALUATION_LEVELS
                        },
                        **{
                            f"outside_candidate_space_examples_{level}": None
                            for level in EVALUATION_LEVELS
                        },
                        **{
                            f"n_truth_rows_outside_candidate_space_{level}": None
                            for level in EVALUATION_LEVELS
                        },
                        **{
                            f"truth_outside_candidate_space_examples_{level}": None
                            for level in EVALUATION_LEVELS
                        },
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

            prediction_filters = {
                level: _filter_rows_by_candidate_space(
                    rows=prediction_rows,
                    truth=truth,
                    level=level,
                )
                for level in EVALUATION_LEVELS
            }
            truth_caches = {
                level: _truth_level_cache(
                    truth=truth,
                    level=level,
                    cache=truth_level_cache,
                )
                for level in EVALUATION_LEVELS
            }
            pairings.append(
                {
                    "tool_id": tool_id,
                    "catalog_tool_id": tool_capabilities[tool_id].catalog_tool_id,
                    "tool_origin": tool_capabilities[tool_id].tool_origin,
                    "context": context,
                    "truth_context": truth.context,
                    "status": "evaluated",
                    "reason": None,
                    "n_prediction_rows": len(prediction_rows),
                    **{
                        f"n_prediction_rows_outside_candidate_space_{level}": len(
                            prediction_filters[level].excluded_rows
                        )
                        for level in EVALUATION_LEVELS
                    },
                    **{
                        f"outside_candidate_space_examples_{level}": (
                            _format_edge_examples(
                                prediction_filters[level].excluded_rows
                            )
                        )
                        for level in EVALUATION_LEVELS
                    },
                    **{
                        f"n_truth_rows_outside_candidate_space_{level}": len(
                            truth_caches[level].excluded_rows
                        )
                        for level in EVALUATION_LEVELS
                    },
                    **{
                        f"truth_outside_candidate_space_examples_{level}": (
                            _format_edge_examples(truth_caches[level].excluded_rows)
                        )
                        for level in EVALUATION_LEVELS
                    },
                }
            )
            for level in EVALUATION_LEVELS:
                prediction_filter = prediction_filters[level]
                metrics.append(
                    _evaluate_pairing(
                        tool_id=tool_id,
                        capabilities=tool_capabilities[tool_id],
                        prediction_rows=prediction_filter.included_rows,
                        excluded_prediction_rows=prediction_filter.excluded_rows,
                        prediction_context=context,
                        truth=truth,
                        level=level,
                        truth_cache=truth_caches[level],
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
            "inference_run_id": run_report["run_id"],
            "inference_dataset_id": run_report["dataset"]["id"],
            "inference_dataset_fingerprint": run_report["dataset"]["fingerprint"],
            "ground_truth_dataset_id": manifest.get("dataset_id"),
            "ground_truth_dataset_fingerprint": manifest["dataset_fingerprint"],
            "ground_truth_simulator_id": manifest.get("simulator_id"),
            "merged_network": "merged_network_raw",
        },
        "inference_run": {
            "run_id": run_report["run_id"],
            "status": run_report["status"],
            "dataset": run_report["dataset"],
            "execution": run_report["execution"],
            "warnings": issue_messages(run_report["issues"], severity="warn"),
            "output_capabilities": {
                tool_id: {
                    "tool_origin": capabilities.tool_origin,
                    "catalog_tool_id": capabilities.catalog_tool_id,
                    "directed": capabilities.directed,
                    "sign": capabilities.sign,
                }
                for tool_id, capabilities in sorted(tool_capabilities.items())
            },
        },
        "ground_truth": {
            "dataset_id": manifest.get("dataset_id"),
            "dataset_fingerprint": manifest["dataset_fingerprint"],
            "simulator_id": manifest.get("simulator_id"),
            "data_axes": manifest.get("data_axes"),
            "truth_requirements": manifest.get("truth_requirements"),
            "contexts": sorted(truth_networks.keys(), key=network_context_sort_key),
            "context_counts_by_family": _context_counts_by_family(
                truth_networks.keys()
            ),
            "gene_universe_size": (
                len(next(iter(truth_networks.values())).candidate_space.gene_universe)
                if truth_networks
                else 0
            ),
            "candidate_space": _candidate_space_report(
                truth_networks,
                cache=truth_level_cache,
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


def _candidate_space_report(
    truth_networks: dict[str, TruthNetwork],
    *,
    cache: dict[tuple[str, str], TruthLevelCache],
) -> dict[str, Any]:
    if not truth_networks:
        return {
            "mode": "unknown",
            "sources": None,
            "targets": None,
            "allow_self_edges": False,
            "n_sources": 0,
            "n_targets": 0,
            "n_source_target_overlap": 0,
            "truth_rows_total": 0,
            "n_truth_rows_outside_candidate_space_by_level": {
                level: 0 for level in EVALUATION_LEVELS
            },
            "n_truth_edges_outside_candidate_space_by_level": {
                level: 0 for level in EVALUATION_LEVELS
            },
            "truth_filtering_by_context": [],
            "n_candidates_by_level": {level: 0 for level in EVALUATION_LEVELS},
        }
    candidate_space = next(iter(truth_networks.values())).candidate_space
    truth_filtering_by_context: list[dict[str, Any]] = []
    totals_by_level = {level: 0 for level in EVALUATION_LEVELS}
    edge_totals_by_level = {level: 0 for level in EVALUATION_LEVELS}
    for context, truth in sorted(
        truth_networks.items(), key=lambda item: network_context_sort_key(item[0])
    ):
        caches = {
            level: _truth_level_cache(truth=truth, level=level, cache=cache)
            for level in EVALUATION_LEVELS
        }
        outside_by_level = {
            level: len(caches[level].excluded_rows) for level in EVALUATION_LEVELS
        }
        outside_edges_by_level = {
            level: len(caches[level].excluded_truth_scores)
            for level in EVALUATION_LEVELS
        }
        for level in EVALUATION_LEVELS:
            totals_by_level[level] += outside_by_level[level]
            edge_totals_by_level[level] += outside_edges_by_level[level]
        if any(outside_by_level.values()):
            truth_filtering_by_context.append(
                {
                    "context": context,
                    "n_truth_rows": len(truth.rows),
                    "n_truth_rows_outside_candidate_space_by_level": (outside_by_level),
                    "n_truth_edges_outside_candidate_space_by_level": (
                        outside_edges_by_level
                    ),
                    "outside_candidate_space_examples_by_level": {
                        level: _format_edge_examples(caches[level].excluded_rows)
                        for level in EVALUATION_LEVELS
                    },
                }
            )
    return {
        "mode": candidate_space.mode,
        "sources": candidate_space.sources_reference,
        "targets": candidate_space.targets_reference,
        "allow_self_edges": candidate_space.allow_self_edges,
        "n_sources": len(candidate_space.sources),
        "n_targets": len(candidate_space.targets),
        "n_source_target_overlap": len(
            candidate_space.sources & candidate_space.targets
        ),
        "truth_rows_total": sum(len(truth.rows) for truth in truth_networks.values()),
        "n_truth_rows_outside_candidate_space_by_level": totals_by_level,
        "n_truth_edges_outside_candidate_space_by_level": edge_totals_by_level,
        "truth_filtering_by_context": truth_filtering_by_context,
        "n_candidates_by_level": {
            level: _candidate_count(
                candidate_space=candidate_space,
                level=level,
            )
            for level in EVALUATION_LEVELS
        },
    }


def _resolve_merged_network_path_from_run_report(
    *,
    run_report_path: Path,
    run_report: dict[str, Any],
) -> Path:
    outputs = run_report.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("Run report must contain an object at outputs")
    raw_path = outputs.get("merged_network_raw")
    return resolve_safe_manifest_path(
        base_dir=run_report_path.parent,
        value=raw_path,
        label="Run report outputs.merged_network_raw",
    )


def _create_evaluation_dir(
    *,
    output_root: Path,
    run_report_path: Path,
    run_report: dict[str, Any],
    truth_manifest: dict[str, Any],
    created_at: datetime,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    inference_id = _slugify(run_report_path.parent.name or run_report["run_id"])
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
            raw_score = row["score"]
            try:
                score = float(raw_score)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Inferred network CSV has invalid score at line {line_number}: "
                    f"{row.get('score')!r}"
                ) from exc
            if not math.isfinite(score):
                raise ValueError(
                    f"Inferred network CSV has invalid score at line {line_number}: "
                    f"{row.get('score')!r}; score must be finite"
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
    return rows


def _load_truth_networks(
    manifest_path: Path,
) -> tuple[dict[str, TruthNetwork], dict[str, Any]]:
    manifest = _load_json_object(manifest_path, "Ground-truth manifest")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("Ground-truth manifest must contain an object at outputs")

    base_dir = manifest_path.parent
    gene_universe_reference = _validate_truth_manifest_reference(
        outputs.get("gene_universe"),
        label="Ground-truth manifest outputs.gene_universe",
        suffix=".txt",
    )
    networks_reference = _validate_truth_manifest_reference(
        outputs.get("networks"),
        label="Ground-truth manifest outputs.networks",
        suffix=".csv",
    )
    gene_universe = _load_gene_universe(
        resolve_safe_manifest_path(
            base_dir=base_dir,
            value=gene_universe_reference,
            label="Ground-truth manifest outputs.gene_universe",
        )
    )
    candidate_space = _load_candidate_space(
        manifest=manifest,
        base_dir=base_dir,
        gene_universe=gene_universe,
    )
    schema = json.loads(
        resources.files(GROUND_TRUTH_SCHEMA_PACKAGE)
        .joinpath(GROUND_TRUTH_SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
    )
    validate_json_instance(
        instance=manifest,
        schema=schema,
        label="Ground-truth manifest",
    )
    networks = _load_truth_network_table(
        path=resolve_safe_manifest_path(
            base_dir=base_dir,
            value=networks_reference,
            label="Ground-truth manifest outputs.networks",
        ),
        candidate_space=candidate_space,
    )

    if not networks:
        raise ValueError(
            f"Ground-truth manifest references no truth networks: {manifest_path}"
        )
    _validate_required_truth_contexts(manifest=manifest, networks=networks)
    return networks, manifest


def _validate_required_truth_contexts(
    *, manifest: dict[str, Any], networks: dict[str, TruthNetwork]
) -> None:
    contexts = set(networks)
    required_families = manifest["truth_requirements"]["contexts"]
    missing: list[str] = []
    for family in required_families:
        if family == "global":
            present = "global" in contexts
        else:
            prefix = f"{family}:"
            present = any(context.startswith(prefix) for context in contexts)
        if not present:
            missing.append(family)
    if missing:
        raise ValueError(
            "Ground-truth network CSV does not satisfy "
            "truth_requirements.contexts; missing: " + ", ".join(missing)
        )


def _validate_truth_manifest_reference(
    value: Any,
    *,
    label: str,
    suffix: str,
) -> str:
    reference = validate_safe_relative_posix_path(value, label=label)
    if not reference.endswith(suffix):
        raise ValueError(f"{label} must use the {suffix!r} extension")
    return reference


def _load_gene_universe(path: Path) -> set[str]:
    return _load_gene_set(
        path,
        label="Ground-truth gene universe",
        reject_duplicates=True,
    )


def _load_gene_set(
    path: Path,
    *,
    label: str,
    reject_duplicates: bool = False,
) -> set[str]:
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} file not found: {path}")
    genes: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line or line != line.strip():
            raise ValueError(
                f"{label} file contains an empty or non-canonical identifier "
                f"at line {line_number}: {path}"
            )
        gene = line
        if gene in seen:
            if reject_duplicates:
                raise ValueError(
                    f"{label} file contains duplicate gene {gene!r} "
                    f"at line {line_number}: {path}"
                )
            continue
        seen.add(gene)
        genes.append(gene)
    if not genes:
        raise ValueError(f"{label} file contains no genes: {path}")
    return set(genes)


def _load_candidate_space(
    *,
    manifest: dict[str, Any],
    base_dir: Path,
    gene_universe: set[str],
) -> CandidateSpace:
    raw_candidate_space = manifest.get("candidate_space")
    if raw_candidate_space is None:
        raise ValueError(
            "Ground-truth manifest candidate_space is required; evaluation "
            "cannot infer the regulator universe"
        )
    if not isinstance(raw_candidate_space, dict):
        raise ValueError("Ground-truth manifest candidate_space must be an object")

    supported_keys = {"sources", "targets", "allow_self_edges"}
    unexpected_keys = sorted(set(raw_candidate_space) - supported_keys)
    if unexpected_keys:
        raise ValueError(
            "Ground-truth manifest candidate_space contains unsupported keys: "
            + ", ".join(unexpected_keys)
        )

    references: dict[str, str] = {}
    for key in ("sources", "targets"):
        references[key] = _validate_truth_manifest_reference(
            raw_candidate_space.get(key),
            label=f"Ground-truth manifest candidate_space.{key}",
            suffix=".txt",
        )
    allow_self_edges = raw_candidate_space.get("allow_self_edges")
    if allow_self_edges is not False:
        raise ValueError(
            "Ground-truth manifest candidate_space.allow_self_edges must be false"
        )

    sources = _load_gene_set(
        resolve_safe_manifest_path(
            base_dir=base_dir,
            value=references["sources"],
            label="Ground-truth manifest candidate_space.sources",
        ),
        label="Ground-truth candidate source universe",
        reject_duplicates=True,
    )
    targets = _load_gene_set(
        resolve_safe_manifest_path(
            base_dir=base_dir,
            value=references["targets"],
            label="Ground-truth manifest candidate_space.targets",
        ),
        label="Ground-truth candidate target universe",
        reject_duplicates=True,
    )
    for name, values in (("sources", sources), ("targets", targets)):
        outside = sorted(values - gene_universe)
        if outside:
            raise ValueError(
                f"Ground-truth manifest candidate_space.{name} contains genes outside "
                f"outputs.gene_universe; examples: {', '.join(outside[:8])}"
            )

    return CandidateSpace(
        gene_universe=set(gene_universe),
        sources=sources,
        targets=targets,
        allow_self_edges=allow_self_edges,
        mode="explicit",
        sources_reference=references["sources"],
        targets_reference=references["targets"],
    )


def _load_truth_network_table(
    *,
    path: Path,
    candidate_space: CandidateSpace,
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
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Ground-truth network CSV has invalid score at line {line_number}: "
                    f"{raw_score!r}"
                ) from exc
            if not math.isfinite(score):
                raise ValueError(
                    f"Ground-truth network CSV has invalid score at line {line_number}: "
                    f"{raw_score!r}; score must be finite"
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
            if (
                source not in candidate_space.gene_universe
                or target not in candidate_space.gene_universe
            ):
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
            candidate_space=candidate_space,
            directed=True,
            signed=any(row.sign in VALID_SIGNS for row in rows),
        )
        for context, rows in rows_by_context.items()
    }


def _group_inferred_rows(
    rows: Iterable[NetworkRow],
) -> dict[tuple[str, str], list[NetworkRow]]:
    grouped: dict[tuple[str, str], list[NetworkRow]] = defaultdict(list)
    for row in rows:
        if row.tool_id is None:
            raise ValueError("Inferred network row is missing tool_id")
        grouped[(row.tool_id, row.context)].append(row)
    return dict(grouped)


def _validate_dataset_identity(
    *,
    run_report: dict[str, Any],
    truth_manifest: dict[str, Any],
) -> None:
    inference_dataset_id = run_report["dataset"]["id"]
    truth_dataset_id = truth_manifest["dataset_id"]
    if inference_dataset_id != truth_dataset_id:
        raise ValueError(
            "Inference and ground truth dataset IDs must match exactly: "
            f"run_report.dataset.id={inference_dataset_id!r}, "
            f"ground_truth_manifest.dataset_id={truth_dataset_id!r}"
        )
    inference_fingerprint = validate_dataset_fingerprint(
        run_report["dataset"].get("fingerprint"),
        label="Run report dataset.fingerprint",
    )
    truth_fingerprint = validate_dataset_fingerprint(
        truth_manifest.get("dataset_fingerprint"),
        label="Ground-truth manifest dataset_fingerprint",
    )
    if inference_fingerprint != truth_fingerprint:
        raise ValueError(
            "Inference and ground truth dataset fingerprints must match exactly: "
            f"run_report.dataset.fingerprint={inference_fingerprint!r}, "
            f"ground_truth_manifest.dataset_fingerprint={truth_fingerprint!r}"
        )


def _validated_completed_contexts(
    *,
    run_report: dict[str, Any],
    observed_contexts: dict[str, set[str]],
) -> dict[str, list[str]]:
    tools = run_report["tools"]
    completed = tools["completed"]
    raw_inventory = tools.get("completed_contexts")
    if not isinstance(raw_inventory, dict) or set(raw_inventory) != set(completed):
        raise ValueError(
            "Run report tools.completed_contexts must be an object with exactly "
            "the tools.completed keys"
        )
    results = tools.get("results")
    if not isinstance(results, dict):
        raise ValueError("Run report tools.results must be an object")

    validated: dict[str, list[str]] = {}
    for run_id in completed:
        result = results.get(run_id)
        execution = result.get("execution") if isinstance(result, dict) else None
        mode = execution.get("mode") if isinstance(execution, dict) else None
        if mode not in {
            "global",
            "group_native",
            "group_emulated",
            "column_native",
            "group_aggregated",
        }:
            raise ValueError(
                f"Run report tools.results[{run_id!r}].execution.mode is required "
                "for every completed run"
            )

        raw_contexts = raw_inventory[run_id]
        if (
            not isinstance(raw_contexts, list)
            or not raw_contexts
            or not all(isinstance(context, str) for context in raw_contexts)
        ):
            raise ValueError(
                f"Run report tools.completed_contexts[{run_id!r}] must be a "
                "non-empty array of unique canonical contexts"
            )
        contexts = [
            normalize_network_context(
                context,
                source=f"Run report tools.completed_contexts[{run_id!r}]",
            )
            for context in raw_contexts
        ]
        if contexts != raw_contexts or len(set(contexts)) != len(contexts):
            raise ValueError(
                f"Run report tools.completed_contexts[{run_id!r}] must be a "
                "non-empty array of unique canonical contexts"
            )

        for context in contexts:
            family = network_context_family(context)
            compatible = (
                (mode == "global" and context == "global")
                or (
                    mode in {"group_native", "group_emulated", "group_aggregated"}
                    and family == "group"
                    and context != "group:"
                )
                or (
                    mode == "column_native"
                    and family == "column"
                    and context != "column:"
                )
            )
            if not compatible:
                raise ValueError(
                    f"Run report tools.completed_contexts[{run_id!r}] context "
                    f"{context!r} contradicts execution mode {mode!r}"
                )

        unexpected_observed = observed_contexts.get(run_id, set()) - set(contexts)
        if unexpected_observed:
            raise ValueError(
                f"Inferred run {run_id!r} contains contexts not declared in "
                "Run report tools.completed_contexts: "
                f"{sorted(unexpected_observed, key=network_context_sort_key)}"
            )
        validated[run_id] = contexts
    return validated


def _add_completed_contexts(
    *,
    grouped_predictions: dict[tuple[str, str], list[NetworkRow]],
    run_report: dict[str, Any],
) -> None:
    observed_contexts: dict[str, set[str]] = defaultdict(set)
    for run_id, context in grouped_predictions:
        observed_contexts[run_id].add(context)
    inventory = _validated_completed_contexts(
        run_report=run_report,
        observed_contexts=dict(observed_contexts),
    )
    for run_id, contexts in inventory.items():
        for context in contexts:
            grouped_predictions.setdefault((run_id, context), [])


def _resolve_tool_capabilities(
    *,
    inferred_rows: list[NetworkRow],
    run_report: dict[str, Any],
) -> dict[str, ToolCapabilities]:
    raw_capabilities = validate_frozen_output_capabilities(
        run_report.get("tools"),
        label="Run report tools",
    )
    validate_final_inference_report(
        run_report,
        observed_rows_per_tool=Counter(
            row.tool_id for row in inferred_rows if row.tool_id is not None
        ),
        selected=list(raw_capabilities),
    )
    observed_contexts: dict[str, set[str]] = defaultdict(set)
    for row in inferred_rows:
        if row.tool_id is not None:
            observed_contexts[row.tool_id].add(row.context)
    _validated_completed_contexts(
        run_report=run_report,
        observed_contexts=dict(observed_contexts),
    )
    frozen_capabilities = {
        run_id: _parse_frozen_output_capabilities(run_id=run_id, value=value)
        for run_id, value in raw_capabilities.items()
    }
    completed = run_report["tools"]["completed"]
    resolved = {tool_id: frozen_capabilities[tool_id] for tool_id in completed}
    _validate_inferred_sign_semantics(
        inferred_rows=inferred_rows,
        capabilities=resolved,
    )
    return resolved


def _validate_inferred_sign_semantics(
    *,
    inferred_rows: list[NetworkRow],
    capabilities: dict[str, ToolCapabilities],
) -> None:
    for row in inferred_rows:
        if row.tool_id is None:
            raise ValueError("Inferred network row is missing tool_id")
        capability = capabilities[row.tool_id]
        if capability.sign == "none" and row.sign != "?":
            raise ValueError(
                f"Inferred run {row.tool_id!r} declares sign='none' but emitted "
                f"signed edge {row.source!r} -> {row.target!r} with sign {row.sign!r}"
            )
        if capability.sign == "signed" and row.sign not in VALID_SIGNS:
            raise ValueError(
                f"Inferred run {row.tool_id!r} declares sign='signed' but emitted "
                f"unsigned edge {row.source!r} -> {row.target!r}"
            )


def _parse_frozen_output_capabilities(
    *,
    run_id: str,
    value: Any,
) -> ToolCapabilities:
    directed = value["directed"]
    sign = value["sign"]
    tool_origin = value["tool_origin"]
    catalog_tool_id = value["catalog_tool_id"]
    return ToolCapabilities(
        catalog_tool_id=catalog_tool_id,
        tool_origin=tool_origin,
        directed=directed,
        signed=sign != "none",
        sign=sign,
    )


def _evaluate_pairing(
    *,
    tool_id: str,
    capabilities: ToolCapabilities,
    prediction_rows: list[NetworkRow],
    excluded_prediction_rows: list[NetworkRow],
    prediction_context: str,
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
            context=prediction_context,
            truth_context=truth.context,
            level=level,
            status="not_applicable",
            reason=not_applicable_reason,
            truth=truth,
            truth_cache=truth_count_cache,
            prediction_rows=prediction_rows,
            excluded_prediction_rows=excluded_prediction_rows,
        )

    if truth_cache is None:
        truth_cache = _build_truth_level_cache(truth=truth, level=level)
    prediction_scores = _aggregate_rows(prediction_rows, level=level)
    truth_keys = truth_cache.truth_keys
    predicted_keys = set(prediction_scores)
    predicted_outside = [
        key
        for key in predicted_keys
        if not _is_candidate_key(
            key,
            candidate_space=truth_cache.candidate_space,
            level=level,
        )
    ]
    if predicted_outside:
        example = next(iter(sorted(predicted_outside)))
        raise ValueError(
            "Internal error: filtered inferred network contains edges outside "
            f"candidate_space for context {prediction_context!r}, level {level!r}; "
            f"example edge: {example}"
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
        "tool_origin": capabilities.tool_origin,
        "context": prediction_context,
        "truth_context": truth.context,
        "level": level,
        "status": status,
        "reason": reason,
        "auroc": auroc,
        "aupr": aupr,
        "f1_at_truth_count": top_k_stats["f1_at_truth_count"],
        "epr_at_truth_count": top_k_stats["epr_at_truth_count"],
        "n_candidates": truth_cache.n_candidates,
        "n_candidate_genes": len(truth.candidate_space.gene_universe),
        "n_candidate_sources": len(truth.candidate_space.sources),
        "n_candidate_targets": len(truth.candidate_space.targets),
        "n_truth_rows": len(truth_cache.included_rows) + len(truth_cache.excluded_rows),
        "n_truth_rows_outside_candidate_space": len(truth_cache.excluded_rows),
        "n_truth_edges": len(truth_keys),
        "n_truth_edges_outside_candidate_space": len(truth_cache.excluded_truth_scores),
        "truth_outside_candidate_space_examples": _format_edge_examples(
            truth_cache.excluded_rows
        ),
        "n_predicted_edges": len(predicted_keys),
        "n_predicted_edges_outside_candidate_space": len(
            _aggregate_rows(excluded_prediction_rows, level=level)
        ),
        "n_prediction_rows": len(prediction_rows) + len(excluded_prediction_rows),
        "n_prediction_rows_outside_candidate_space": len(excluded_prediction_rows),
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
    truth_filter = _filter_rows_by_candidate_space(
        rows=truth.rows,
        truth=truth,
        level=level,
    )
    truth_scores = _aggregate_rows(truth_filter.included_rows, level=level)
    excluded_truth_scores = _aggregate_rows(
        truth_filter.excluded_rows,
        level=level,
    )
    truth_keys = set(truth_scores)
    n_candidates = _candidate_count(
        candidate_space=truth.candidate_space,
        level=level,
    )
    return TruthLevelCache(
        truth_scores=truth_scores,
        truth_keys=truth_keys,
        excluded_truth_scores=excluded_truth_scores,
        included_rows=truth_filter.included_rows,
        excluded_rows=truth_filter.excluded_rows,
        candidate_space=truth.candidate_space,
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
    prediction_rows: Optional[list[NetworkRow]] = None,
    excluded_prediction_rows: Optional[list[NetworkRow]] = None,
) -> dict[str, Any]:
    included_rows = prediction_rows or []
    excluded_rows = excluded_prediction_rows or []
    return {
        "tool_id": tool_id,
        "catalog_tool_id": capabilities.catalog_tool_id,
        "tool_origin": capabilities.tool_origin,
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
        "n_candidate_genes": (len(truth.candidate_space.gene_universe) if truth else 0),
        "n_candidate_sources": (len(truth.candidate_space.sources) if truth else 0),
        "n_candidate_targets": (len(truth.candidate_space.targets) if truth else 0),
        "n_truth_rows": (
            len(truth_cache.included_rows) + len(truth_cache.excluded_rows)
            if truth_cache
            else 0
        ),
        "n_truth_rows_outside_candidate_space": (
            len(truth_cache.excluded_rows) if truth_cache else 0
        ),
        "n_truth_edges": len(truth_cache.truth_keys) if truth_cache else 0,
        "n_truth_edges_outside_candidate_space": (
            len(truth_cache.excluded_truth_scores) if truth_cache else 0
        ),
        "truth_outside_candidate_space_examples": (
            _format_edge_examples(truth_cache.excluded_rows) if truth_cache else None
        ),
        "n_predicted_edges": len(_aggregate_rows(included_rows, level=level)),
        "n_predicted_edges_outside_candidate_space": len(
            _aggregate_rows(excluded_rows, level=level)
        ),
        "n_prediction_rows": len(included_rows) + len(excluded_rows),
        "n_prediction_rows_outside_candidate_space": len(excluded_rows),
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


def _candidate_count(*, candidate_space: CandidateSpace, level: str) -> int:
    n_sources = len(candidate_space.sources)
    n_targets = len(candidate_space.targets)
    overlap = len(candidate_space.sources & candidate_space.targets)
    directed_count = n_sources * n_targets
    if not candidate_space.allow_self_edges:
        directed_count -= overlap
    if level == "topology":
        # Each pair entirely inside the source/target overlap is present in both
        # directions before topology collapses directionality.
        return directed_count - (overlap * (overlap - 1) // 2)
    if level == "directed":
        return directed_count
    if level == "signed":
        return directed_count * len(VALID_SIGNS)
    raise ValueError(f"Unknown evaluation level: {level}")


def _is_candidate_key(
    key: tuple[str, ...],
    *,
    candidate_space: CandidateSpace,
    level: str,
) -> bool:
    if level == "topology":
        return len(key) == 2 and (
            _is_candidate_edge(
                source=key[0],
                target=key[1],
                candidate_space=candidate_space,
            )
            or _is_candidate_edge(
                source=key[1],
                target=key[0],
                candidate_space=candidate_space,
            )
        )
    if level == "directed":
        return len(key) == 2 and _is_candidate_edge(
            source=key[0],
            target=key[1],
            candidate_space=candidate_space,
        )
    if level == "signed":
        return (
            len(key) == 3
            and _is_candidate_edge(
                source=key[0],
                target=key[1],
                candidate_space=candidate_space,
            )
            and key[2] in VALID_SIGNS
        )
    raise ValueError(f"Unknown evaluation level: {level}")


def _is_candidate_edge(
    *,
    source: str,
    target: str,
    candidate_space: CandidateSpace,
) -> bool:
    return (
        source in candidate_space.sources
        and target in candidate_space.targets
        and (candidate_space.allow_self_edges or source != target)
    )


def _filter_rows_by_candidate_space(
    *,
    rows: list[NetworkRow],
    truth: TruthNetwork,
    level: str,
) -> CandidateFilter:
    included: list[NetworkRow] = []
    excluded: list[NetworkRow] = []
    for row in rows:
        unknown_genes = sorted(
            {row.source, row.target} - truth.candidate_space.gene_universe
        )
        if unknown_genes:
            raise ValueError(
                "Inferred network contains genes outside outputs.gene_universe "
                f"for context {row.context!r}; edge {row.source!r} -> {row.target!r}; "
                f"unknown genes: {', '.join(unknown_genes)}"
            )
        if level == "topology":
            key = _edge_key(row.source, row.target, row.sign, level=level)
            is_candidate = _is_candidate_key(
                key,
                candidate_space=truth.candidate_space,
                level=level,
            )
        else:
            is_candidate = _is_candidate_edge(
                source=row.source,
                target=row.target,
                candidate_space=truth.candidate_space,
            )
        if is_candidate:
            included.append(row)
        else:
            excluded.append(row)
    return CandidateFilter(
        included_rows=included,
        excluded_rows=excluded,
    )


def _format_edge_examples(rows: list[NetworkRow], *, limit: int = 8) -> Optional[str]:
    examples = sorted({f"{row.source}->{row.target}" for row in rows})[:limit]
    return "; ".join(examples) if examples else None


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
        resources.files(VIEW_ASSETS_PACKAGE).joinpath(name).read_text(encoding="utf-8")
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
