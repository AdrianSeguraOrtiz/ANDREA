"""Bundle contracts for compare-networks outputs."""

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
        purpose="Complete comparison archive for inspection, debugging and storage.",
        contents_summary=(
            "Frozen source inputs.",
            "Comparison request, complete tables, SQLite query store and static HTML summary.",
        ),
    ),
    BundleSpec(
        id="report",
        label="Report Bundle",
        purpose="Compact comparison tables, SQLite query store and static HTML summary.",
        contents_summary=(
            "Comparison request and comparison_report.json.",
            "Network index, distances, coordinates, SQLite query store and static HTML summary.",
        ),
    ),
)

FULL_REQUIRED_FILES = (
    "comparison-request.json",
    "comparison_report.json",
    "network_index.csv",
    "edge_scores.csv",
    "distances.csv",
    "distance_coordinates.csv",
    "comparison.sqlite",
    "comparison_view.html",
)

REPORT_REQUIRED_FILES = (
    "comparison-request.json",
    "comparison_report.json",
    "network_index.csv",
    "distances.csv",
    "distance_coordinates.csv",
    "comparison.sqlite",
    "comparison_view.html",
)


def bundle_specs() -> tuple[BundleSpec, ...]:
    return BUNDLE_SPECS


def supported_bundles() -> tuple[str, ...]:
    return supported_bundle_ids(BUNDLE_SPECS)


def resolve_bundle(*, bundle_id: str, comparison_dir: Path) -> BundleResolution:
    spec = bundle_spec_by_id(BUNDLE_SPECS, bundle_id)
    root = comparison_dir.resolve()
    if bundle_id == "full":
        return _resolve_full(spec=spec, root=root)
    if bundle_id == "report":
        return _resolve_report(spec=spec, root=root)
    raise AssertionError(f"Unhandled bundle_id: {bundle_id}")


def _resolve_full(*, spec: BundleSpec, root: Path) -> BundleResolution:
    missing_required = [
        relative_path for relative_path in FULL_REQUIRED_FILES if not (root / relative_path).is_file()
    ]
    return BundleResolution(
        spec=spec,
        root=root,
        sources=all_files(root),
        missing_required=tuple(missing_required),
    )


def _resolve_report(*, spec: BundleSpec, root: Path) -> BundleResolution:
    return exact_files_resolution(
        spec=spec,
        root=root,
        required=REPORT_REQUIRED_FILES,
    )


__all__ = ["BUNDLE_SPECS", "bundle_specs", "resolve_bundle", "supported_bundles"]
