"""Bundle contracts for generate-data outputs."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from andrea.core.shared.bundles import (
    BundleResolution,
    BundleSource,
    BundleSpec,
    all_files,
    append_exact,
    bundle_spec_by_id,
    exact_file,
    supported_bundle_ids,
    unique_sources,
)
from andrea.core.shared.paths import validate_safe_relative_posix_path

GROUND_TRUTH_MANIFEST_SCHEMA = (
    Path(__file__).resolve().parents[3]
    / "catalog_simulation_data_tools"
    / "schemas"
    / "ground-truth-manifest.schema.json"
)

BUNDLE_SPECS: tuple[BundleSpec, ...] = (
    BundleSpec(
        id="full",
        label="Full Archive",
        purpose="Complete benchmark archive for inspection, debugging and storage.",
        contents_summary=(
            "All benchmark manifests and request snapshots.",
            "All generated datasets, expression matrices, extras, truth files, native outputs and provenance.",
        ),
    ),
    BundleSpec(
        id="analysis",
        label="Analysis Bundle",
        purpose="Dataset-level ground-truth handoff for evaluate-inference.",
        intended_downstream_commands=("evaluate-inference",),
        contents_summary=(
            "Selected dataset ground-truth-manifest.json.",
            "Selected dataset truth/networks.csv and truth/gene_universe.txt.",
            "Required candidate source and target universes referenced by the manifest.",
        ),
    ),
    BundleSpec(
        id="report",
        label="Report Bundle",
        purpose="Compact benchmark summary for human inspection.",
        contents_summary=(
            "Benchmark manifest, preflight report and simulation plan.",
            "Dataset manifests and simulator output manifests.",
        ),
    ),
)


def bundle_specs() -> tuple[BundleSpec, ...]:
    return BUNDLE_SPECS


def supported_bundles() -> tuple[str, ...]:
    return supported_bundle_ids(BUNDLE_SPECS)


def resolve_bundle(
    *, bundle_id: str, benchmark_root: Path, dataset_id: str | None = None
) -> BundleResolution:
    spec = bundle_spec_by_id(BUNDLE_SPECS, bundle_id)
    root = benchmark_root.resolve()
    if bundle_id == "full":
        return BundleResolution(spec=spec, root=root, sources=all_files(root))
    if bundle_id == "analysis":
        return _resolve_analysis(spec=spec, root=root, dataset_id=dataset_id)
    if bundle_id == "report":
        return _resolve_report(spec=spec, root=root)
    raise AssertionError(f"Unhandled bundle_id: {bundle_id}")


def _dataset_dirs(root: Path) -> list[Path]:
    datasets_dir = root / "datasets"
    if not datasets_dir.is_dir():
        return []
    return [path for path in sorted(datasets_dir.iterdir()) if path.is_dir()]


def _ground_truth_schema_errors(manifest: dict[str, object]) -> list[str]:
    schema = json.loads(GROUND_TRUTH_MANIFEST_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(manifest),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    return [error.message for error in errors]


def analysis_dataset_ids(*, benchmark_root: Path) -> tuple[str, ...]:
    return tuple(path.name for path in _dataset_dirs(benchmark_root.resolve()))


def _resolve_analysis(
    *, spec: BundleSpec, root: Path, dataset_id: str | None
) -> BundleResolution:
    if not dataset_id:
        raise ValueError("dataset_id is required for generate-data analysis bundles")
    available_datasets = analysis_dataset_ids(benchmark_root=root)
    if dataset_id not in available_datasets:
        supported = ", ".join(available_datasets) or "none"
        raise ValueError(
            f"Unknown dataset_id {dataset_id!r}; available datasets: {supported}"
        )

    dataset_root = (root / "datasets" / dataset_id).resolve()
    sources = []
    missing_required: list[str] = []
    skipped_optional: list[str] = []
    append_exact(
        root=dataset_root,
        relative_path="ground-truth-manifest.json",
        sources=sources,
        missing_required=missing_required,
        skipped_optional=skipped_optional,
        required=True,
    )
    manifest_path = dataset_root / "ground-truth-manifest.json"
    if exact_file(dataset_root, "ground-truth-manifest.json") is not None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None
        if not isinstance(manifest, dict):
            missing_required.append("ground-truth-manifest.json:valid_json_object")
        else:
            if _ground_truth_schema_errors(manifest):
                missing_required.append("ground-truth-manifest.json:schema")
            outputs = manifest.get("outputs")
            candidate_space = manifest.get("candidate_space")
            references = {
                "outputs.networks": outputs.get("networks")
                if isinstance(outputs, dict)
                else None,
                "outputs.gene_universe": outputs.get("gene_universe")
                if isinstance(outputs, dict)
                else None,
                "candidate_space.sources": candidate_space.get("sources")
                if isinstance(candidate_space, dict)
                else None,
                "candidate_space.targets": candidate_space.get("targets")
                if isinstance(candidate_space, dict)
                else None,
            }
            for label, value in references.items():
                try:
                    rel = validate_safe_relative_posix_path(
                        value,
                        label=label,
                    )
                except ValueError:
                    missing_required.append(label)
                    continue
                append_exact(
                    root=dataset_root,
                    relative_path=rel,
                    sources=sources,
                    missing_required=missing_required,
                    skipped_optional=skipped_optional,
                    required=True,
                )
    return BundleResolution(
        spec=spec,
        root=dataset_root,
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
        relative_path="benchmark-manifest.json",
        sources=sources,
        missing_required=missing_required,
        skipped_optional=skipped_optional,
        required=True,
    )
    for rel in (
        "preflight-report.json",
        "simulation-plan.json",
        "input/scenario-request.json",
        "input/simulator-runs.json",
    ):
        append_exact(
            root=root,
            relative_path=rel,
            sources=sources,
            missing_required=missing_required,
            skipped_optional=skipped_optional,
            required=False,
        )
    for dataset_dir in _dataset_dirs(root):
        base = dataset_dir.relative_to(root).as_posix()
        for rel in (
            f"{base}/dataset-manifest.json",
            f"{base}/ground-truth-manifest.json",
            f"{base}/provenance/simulator-output-manifest.json",
        ):
            append_exact(
                root=root,
                relative_path=rel,
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


__all__ = [
    "BUNDLE_SPECS",
    "analysis_dataset_ids",
    "bundle_specs",
    "resolve_bundle",
    "supported_bundles",
]
