"""Bundle contracts for infer-network outputs."""

from __future__ import annotations

import json
from pathlib import Path

from andrea.core.commands.evaluate_inference import validate_inference_analysis_inputs
from andrea.core.shared.bundles import (
    BundleResolution,
    BundleSource,
    BundleSpec,
    all_files,
    append_exact,
    append_glob,
    bundle_spec_by_id,
    exact_file,
    supported_bundle_ids,
    unique_sources,
)
from andrea.core.shared.output_capabilities import validate_frozen_output_capabilities

BUNDLE_SPECS: tuple[BundleSpec, ...] = (
    BundleSpec(
        id="full",
        label="Full Archive",
        purpose="Complete inference archive for inspection, debugging and storage.",
        contents_summary=(
            "Frozen inputs, shared inputs and per-tool workspaces.",
            "Merged networks, graph exports, runtime state, logs and final reports.",
        ),
    ),
    BundleSpec(
        id="analysis",
        label="Analysis Bundle",
        purpose="Minimal inferred-network handoff for evaluation and comparison.",
        intended_downstream_commands=("evaluate-inference", "compare-networks"),
        contents_summary=(
            "run_report.json.",
            "merged_network_raw.csv and merged_network_normalized.csv.",
        ),
    ),
    BundleSpec(
        id="report",
        label="Report Bundle",
        purpose="Compact run status, plan and resolved configuration summary.",
        contents_summary=(
            "run_report.json, plan.json, preflight_report.json and runtime state.",
            "Resolved per-tool parameter and execution files.",
        ),
    ),
    BundleSpec(
        id="graphs",
        label="Graph Exports",
        purpose="External network visualization files.",
        contents_summary=(
            "Merged raw and normalized GEXF/GraphML files.",
            "Normalized Cytoscape helper script.",
        ),
    ),
)

GRAPH_FILES = (
    "merged_network_raw.gexf",
    "merged_network_raw.graphml",
    "merged_network_normalized.gexf",
    "merged_network_normalized.graphml",
    "merged_network_normalized_cytoscape.py",
)

FULL_ALWAYS_REQUIRED_FILES = ("run_report.json",)


def bundle_specs() -> tuple[BundleSpec, ...]:
    return BUNDLE_SPECS


def supported_bundles() -> tuple[str, ...]:
    return supported_bundle_ids(BUNDLE_SPECS)


def resolve_bundle(*, bundle_id: str, run_dir: Path) -> BundleResolution:
    spec = bundle_spec_by_id(BUNDLE_SPECS, bundle_id)
    root = run_dir.resolve()
    if bundle_id == "full":
        return _resolve_full(spec=spec, root=root)
    if bundle_id == "analysis":
        return _resolve_analysis(spec=spec, root=root)
    if bundle_id == "report":
        return _resolve_report(spec=spec, root=root)
    if bundle_id == "graphs":
        return _resolve_graphs(spec=spec, root=root)
    raise AssertionError(f"Unhandled bundle_id: {bundle_id}")


def _resolve_full(*, spec: BundleSpec, root: Path) -> BundleResolution:
    missing_required: list[str] = [
        rel for rel in FULL_ALWAYS_REQUIRED_FILES if not (root / rel).is_file()
    ]
    if (root / "merged_network_raw.csv").is_file():
        for rel in ("merged_network_raw.gexf", "merged_network_raw.graphml"):
            if not (root / rel).is_file():
                missing_required.append(rel)
    if (root / "merged_network_normalized.csv").is_file():
        for rel in (
            "merged_network_normalized.gexf",
            "merged_network_normalized.graphml",
            "merged_network_normalized_cytoscape.py",
        ):
            if not (root / rel).is_file():
                missing_required.append(rel)
    return BundleResolution(
        spec=spec,
        root=root,
        sources=all_files(root),
        missing_required=tuple(missing_required),
    )


def _resolve_analysis(*, spec: BundleSpec, root: Path) -> BundleResolution:
    sources: list[BundleSource] = []
    missing_required: list[str] = []
    skipped_optional: list[str] = []
    for rel in (
        "run_report.json",
        "merged_network_raw.csv",
        "merged_network_normalized.csv",
    ):
        append_exact(
            root=root,
            relative_path=rel,
            sources=sources,
            missing_required=missing_required,
            skipped_optional=skipped_optional,
            required=True,
        )
    run_report_path = root / "run_report.json"
    if exact_file(root, "run_report.json") is not None:
        try:
            run_report = json.loads(run_report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            run_report = None
        try:
            validate_frozen_output_capabilities(
                run_report.get("tools") if isinstance(run_report, dict) else None,
                label="run_report.json tools",
            )
        except ValueError:
            missing_required.append("run_report.json:strict_output_capabilities")
        if exact_file(root, "merged_network_raw.csv") is not None:
            try:
                validate_inference_analysis_inputs(
                    run_report_path=run_report_path,
                )
            except (OSError, ValueError):
                missing_required.append("run_report.json:strict_evaluation_contract")
        elif not isinstance(run_report, dict):
            missing_required.append("run_report.json:strict_evaluation_contract")
        expected_outputs = {
            "merged_network_raw": "merged_network_raw.csv",
            "merged_network_normalized": "merged_network_normalized.csv",
        }
        outputs = run_report.get("outputs") if isinstance(run_report, dict) else None
        if not isinstance(outputs, dict) or any(
            outputs.get(key) != value for key, value in expected_outputs.items()
        ):
            missing_required.append("run_report.json:canonical_outputs")
    return BundleResolution(
        spec=spec,
        root=root,
        sources=unique_sources(sources),
        missing_required=tuple(missing_required),
        skipped_optional=tuple(skipped_optional),
    )


def _resolve_report(*, spec: BundleSpec, root: Path) -> BundleResolution:
    sources: list[BundleSource] = []
    missing_required: list[str] = []
    skipped_optional: list[str] = []
    append_exact(
        root=root,
        relative_path="run_report.json",
        sources=sources,
        missing_required=missing_required,
        skipped_optional=skipped_optional,
        required=True,
    )
    for rel in (
        "plan.json",
        "preflight_report.json",
        "runtime/execution_state.json",
    ):
        append_exact(
            root=root,
            relative_path=rel,
            sources=sources,
            missing_required=missing_required,
            skipped_optional=skipped_optional,
            required=False,
        )
    for pattern in (
        "tools/*/resolved_params.json",
        "tools/*/resolved_execution.json",
    ):
        append_glob(
            root=root,
            pattern=pattern,
            sources=sources,
            missing_required=missing_required,
            skipped_optional=skipped_optional,
            required=False,
        )
    return BundleResolution(
        spec=spec,
        root=root,
        sources=unique_sources(sources),
        missing_required=tuple(missing_required),
        skipped_optional=tuple(skipped_optional),
    )


def _resolve_graphs(*, spec: BundleSpec, root: Path) -> BundleResolution:
    sources: list[BundleSource] = []
    missing_required: list[str] = []
    skipped_optional: list[str] = []
    for rel in GRAPH_FILES:
        append_exact(
            root=root,
            relative_path=rel,
            sources=sources,
            missing_required=missing_required,
            skipped_optional=skipped_optional,
            required=False,
        )
    if not sources:
        missing_required.append("one or more graph export files")
    return BundleResolution(
        spec=spec,
        root=root,
        sources=unique_sources(sources),
        missing_required=tuple(missing_required),
        skipped_optional=tuple(skipped_optional),
    )


__all__ = ["BUNDLE_SPECS", "bundle_specs", "resolve_bundle", "supported_bundles"]
