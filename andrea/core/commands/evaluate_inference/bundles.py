"""Bundle contracts for evaluate-inference outputs."""

from __future__ import annotations

from pathlib import Path

from andrea.core.shared.bundles import (
    BundleResolution,
    BundleSpec,
    all_files,
    bundle_spec_by_id,
    exact_files_resolution,
    supported_bundle_ids,
)

BUNDLE_SPECS: tuple[BundleSpec, ...] = (
    BundleSpec(
        id="full",
        label="Full Archive",
        purpose="Complete evaluation archive for inspection, debugging and storage.",
        contents_summary=(
            "Frozen inference and ground-truth inputs.",
            "Metrics, pairings, evaluation report and HTML view.",
        ),
    ),
    BundleSpec(
        id="analysis",
        label="Analysis Bundle",
        purpose="Minimal evaluation-metric handoff for compare-networks.",
        intended_downstream_commands=("compare-networks",),
        contents_summary=("evaluation_report.json.",),
    ),
    BundleSpec(
        id="report",
        label="Report Bundle",
        purpose="Compact evaluation tables and visual report for human inspection.",
        contents_summary=(
            "evaluation_report.json, metrics.csv and pairings.csv.",
            "evaluation_view.html when generated.",
        ),
    ),
)


def bundle_specs() -> tuple[BundleSpec, ...]:
    return BUNDLE_SPECS


def supported_bundles() -> tuple[str, ...]:
    return supported_bundle_ids(BUNDLE_SPECS)


def resolve_bundle(*, bundle_id: str, evaluation_dir: Path) -> BundleResolution:
    spec = bundle_spec_by_id(BUNDLE_SPECS, bundle_id)
    root = evaluation_dir.resolve()
    if bundle_id == "full":
        return BundleResolution(spec=spec, root=root, sources=all_files(root))
    if bundle_id == "analysis":
        return _resolve_analysis(spec=spec, root=root)
    if bundle_id == "report":
        return _resolve_report(spec=spec, root=root)
    raise AssertionError(f"Unhandled bundle_id: {bundle_id}")


def _resolve_analysis(*, spec: BundleSpec, root: Path) -> BundleResolution:
    return exact_files_resolution(
        spec=spec,
        root=root,
        required=("evaluation_report.json",),
    )


def _resolve_report(*, spec: BundleSpec, root: Path) -> BundleResolution:
    return exact_files_resolution(
        spec=spec,
        root=root,
        required=("evaluation_report.json",),
        optional=("metrics.csv", "pairings.csv", "evaluation_view.html"),
    )


__all__ = ["BUNDLE_SPECS", "bundle_specs", "resolve_bundle", "supported_bundles"]
