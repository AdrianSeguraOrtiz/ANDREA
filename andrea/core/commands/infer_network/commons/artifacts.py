"""Plan/run artifact and fingerprint helpers."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Optional

from andrea.core.shared.json_io import (
    load_json_object as _load_json_object,
)
from andrea.core.shared.json_io import (
    write_json as _write_json,
)

from .resources import normalize_cpuset_cpus
from .shared import (
    DatasetContext,
    PlanWave,
    SchemaConstraints,
    ToolPlanItem,
)


def _serialize_dataset_context(dataset: DatasetContext) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "column_kind": dataset.column_kind,
        "expression_profile": dataset.expression_profile,
        "organism": {
            "taxonomic_group": dataset.taxonomic_group,
            "ncbi_taxon_id": dataset.ncbi_taxon_id,
        },
        "genes": dataset.genes,
        "columns": dataset.columns,
        "expression_matrix_path": str(dataset.expression_matrix_path.resolve()),
        "extras": {
            key: (str(path.resolve()) if path is not None else None)
            for key, path in sorted(dataset.extras.items())
        },
    }


def _deserialize_dataset_context(payload: dict[str, Any]) -> DatasetContext:
    organism = payload.get("organism")
    if not isinstance(organism, dict) or set(organism) != {
        "taxonomic_group",
        "ncbi_taxon_id",
    }:
        raise ValueError(
            "preflight_report.dataset.organism must contain exactly taxonomic_group and ncbi_taxon_id"
        )

    extras_raw = payload.get("extras", {})
    extras: dict[str, Optional[Path]] = {}
    if isinstance(extras_raw, dict):
        for key, value in extras_raw.items():
            if value is None:
                extras[str(key)] = None
            elif isinstance(value, str):
                extras[str(key)] = Path(value).resolve()

    return DatasetContext(
        dataset_id=str(payload.get("dataset_id", "")),
        column_kind=str(payload.get("column_kind", "")),
        expression_profile=str(payload.get("expression_profile", "")),
        taxonomic_group=str(organism.get("taxonomic_group", "")),
        ncbi_taxon_id=(
            int(organism["ncbi_taxon_id"])
            if organism.get("ncbi_taxon_id") is not None
            else None
        ),
        genes=int(payload.get("genes", 0)),
        columns=int(payload.get("columns", 0)),
        expression_matrix_path=Path(
            str(payload.get("expression_matrix_path", ""))
        ).resolve(),
        extras=extras,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint_file(path: Path) -> dict[str, Any]:
    return {
        "size_bytes": int(path.stat().st_size),
        "sha256": _sha256_file(path),
    }


def _materialize_frozen_inputs(
    *,
    run_dir: Path,
    dataset_manifest_path: Path,
    tools_params_path: Path,
    custom_tools_payload: dict[str, Any] | None = None,
    dataset: DatasetContext,
    constraints: SchemaConstraints,
) -> tuple[Path, Path, Path | None, Path, dict[str, Path]]:
    input_dir = run_dir / "input"
    extra_dir = input_dir / "extra"
    input_dir.mkdir(parents=True, exist_ok=True)
    extra_dir.mkdir(parents=True, exist_ok=True)

    frozen_expression = input_dir / "expression.tsv"
    shutil.copy2(dataset.expression_matrix_path, frozen_expression)

    frozen_extras: dict[str, Path] = {}
    for key, source in sorted(dataset.extras.items()):
        if source is None:
            continue
        filename = constraints.extra_input_filenames.get(key, f"{key}.tsv")
        destination = extra_dir / filename
        shutil.copy2(source, destination)
        frozen_extras[key] = destination

    manifest_payload = _load_json_object(dataset_manifest_path, "dataset-manifest")
    manifest_payload.setdefault("dataset", {})
    if not isinstance(manifest_payload["dataset"], dict):
        manifest_payload["dataset"] = {}
    manifest_payload["dataset"]["expression_matrix"] = "expression.tsv"
    extras_payload = manifest_payload.get("extras")
    if not isinstance(extras_payload, dict):
        extras_payload = {}
    for key in sorted(constraints.extra_input_keys):
        if key in frozen_extras:
            extras_payload[key] = str(Path("extra") / frozen_extras[key].name)
        else:
            extras_payload[key] = None
    manifest_payload["extras"] = extras_payload

    frozen_manifest = input_dir / "dataset-manifest.json"
    _write_json(frozen_manifest, manifest_payload)

    frozen_tools_params = input_dir / "tools_params.json"
    tools_payload = _load_json_object(tools_params_path, "tools-params")
    _write_json(frozen_tools_params, tools_payload)

    frozen_custom_tools: Path | None = None
    if custom_tools_payload is not None:
        frozen_custom_tools = input_dir / "custom_tools.json"
        _write_json(frozen_custom_tools, custom_tools_payload)

    return (
        frozen_manifest,
        frozen_tools_params,
        frozen_custom_tools,
        frozen_expression,
        frozen_extras,
    )


def _build_input_fingerprints(
    *,
    run_dir: Path,
    frozen_manifest: Path,
    frozen_tools_params: Path,
    frozen_custom_tools: Path | None = None,
    frozen_runtime_input_contract: Path,
    frozen_expression: Path,
    frozen_extras: dict[str, Path],
) -> dict[str, dict[str, Any]]:
    run_dir = run_dir.resolve()
    fingerprints: dict[str, dict[str, Any]] = {}

    def add(path: Path) -> None:
        rel = str(path.resolve().relative_to(run_dir))
        fingerprints[rel] = _fingerprint_file(path)

    add(frozen_manifest)
    add(frozen_tools_params)
    if frozen_custom_tools is not None:
        add(frozen_custom_tools)
    add(frozen_runtime_input_contract)
    add(frozen_expression)
    for key in sorted(frozen_extras):
        add(frozen_extras[key])
    return fingerprints


def _verify_input_fingerprints(
    *,
    run_dir: Path,
    fingerprints: dict[str, Any],
) -> None:
    run_dir = run_dir.resolve()
    for rel_path, expected in sorted(fingerprints.items()):
        if not isinstance(rel_path, str) or not isinstance(expected, dict):
            raise ValueError("plan.json contains invalid input_fingerprints format")
        file_path = (run_dir / rel_path).resolve()
        if not file_path.exists():
            raise ValueError(f"Planned input file is missing: {file_path}")
        actual = _fingerprint_file(file_path)
        expected_size = int(expected.get("size_bytes", -1))
        expected_sha = str(expected.get("sha256", ""))
        if actual["size_bytes"] != expected_size or actual["sha256"] != expected_sha:
            raise ValueError(
                "Input file changed since planning: "
                f"{file_path} (expected size={expected_size}, sha256={expected_sha}; "
                f"got size={actual['size_bytes']}, sha256={actual['sha256']})"
            )


def _load_plan_waves(
    plan_payload: dict[str, Any]
) -> tuple[list[ToolPlanItem], list[PlanWave], float]:
    raw_waves = plan_payload.get("waves", [])
    if not isinstance(raw_waves, list) or not raw_waves:
        raise ValueError("plan.json has no waves to execute")

    selected_modes: list[ToolPlanItem] = []
    waves: list[PlanWave] = []
    for raw_wave in raw_waves:
        if not isinstance(raw_wave, dict):
            raise ValueError("plan.json contains invalid wave entry")
        raw_index = raw_wave.get("index")
        raw_threads_used = raw_wave.get("threads_used")
        raw_ram_used = raw_wave.get("ram_gb_used")
        raw_wave_eta = raw_wave.get("eta_seconds")
        if (
            isinstance(raw_index, bool)
            or not isinstance(raw_index, int)
            or raw_index < 1
            or isinstance(raw_threads_used, bool)
            or not isinstance(raw_threads_used, int)
            or raw_threads_used < 1
            or isinstance(raw_ram_used, bool)
            or not isinstance(raw_ram_used, (int, float))
            or raw_ram_used <= 0
            or isinstance(raw_wave_eta, bool)
            or not isinstance(raw_wave_eta, (int, float))
            or raw_wave_eta < 0
        ):
            raise ValueError("plan.json contains invalid wave resource metadata")
        tasks_raw = raw_wave.get("tasks", [])
        if not isinstance(tasks_raw, list) or not tasks_raw:
            raise ValueError("plan.json contains empty or invalid wave tasks")
        tasks: list[ToolPlanItem] = []
        for raw_task in tasks_raw:
            if not isinstance(raw_task, dict):
                raise ValueError("plan.json contains invalid task entry")
            raw_threads = raw_task.get("threads")
            raw_ram = raw_task.get("ram_gb")
            raw_eta = raw_task.get("eta_seconds")
            if (
                isinstance(raw_threads, bool)
                or not isinstance(raw_threads, int)
                or raw_threads < 1
                or isinstance(raw_ram, bool)
                or not isinstance(raw_ram, (int, float))
                or raw_ram <= 0
                or isinstance(raw_eta, bool)
                or not isinstance(raw_eta, (int, float))
                or raw_eta < 0
            ):
                raise ValueError("plan.json contains invalid task resource metadata")
            cpuset_raw = raw_task.get("cpuset_cpus")
            cpuset_cpus = (
                normalize_cpuset_cpus(
                    cpuset_raw,
                    source=f"plan.json task {raw_task.get('tool_id')!r}.cpuset_cpus",
                )
                if cpuset_raw is not None
                else None
            )
            task = ToolPlanItem(
                tool_id=str(raw_task.get("tool_id", "")),
                run_id=str(raw_task.get("run_id", "")),
                image=str(raw_task.get("image", "")),
                threads=raw_threads,
                ram_gb=float(raw_ram),
                eta_seconds=float(raw_eta),
                eta_source=str(raw_task.get("eta_source", "")),
                output_dir=str(raw_task.get("output_dir", "")),
                group_label=(
                    str(raw_task.get("group_label", ""))
                    if raw_task.get("group_label") is not None
                    else None
                ),
                eta_provenance=(
                    raw_task.get("eta_provenance")
                    if isinstance(raw_task.get("eta_provenance"), dict)
                    else None
                ),
                network_disabled=bool(raw_task.get("network_disabled")),
                cpuset_cpus=cpuset_cpus,
            )
            if (
                not task.tool_id
                or not task.run_id
                or not task.image
                or task.threads < 1
                or task.ram_gb <= 0
            ):
                raise ValueError(
                    f"plan.json contains invalid task configuration: {raw_task}"
                )
            tasks.append(task)
            selected_modes.append(task)
        wave = PlanWave(
            index=raw_index,
            threads_used=raw_threads_used,
            ram_gb_used=float(raw_ram_used),
            eta_seconds=float(raw_wave_eta),
            tasks=tasks,
        )
        waves.append(wave)

    total_eta_raw = plan_payload.get("eta_total_seconds")
    if (
        isinstance(total_eta_raw, bool)
        or not isinstance(total_eta_raw, (int, float))
        or total_eta_raw < 0
    ):
        raise ValueError("plan.json eta_total_seconds is invalid")
    total_eta = float(total_eta_raw)
    return selected_modes, waves, round(total_eta, 3)
