"""Shared constants, dataclasses, and IO helpers for generate-data."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from andrea.core.shared.catalog_contracts import TAXONOMIC_GROUPS
from andrea.core.shared.json_io import (
    load_json_object as _load_json_object,
    validate_json_instance as _validate_json_instance,
    write_json as _write_json,
)

DEFAULT_OUTPUT_DIR = Path("./benchmarks")
CATALOG_ROOT = Path(__file__).resolve().parents[3] / "catalog_simulation_data_tools"
SCHEMA_VERSION = "1.0"
MAX_SEED_32BIT = 2_147_483_646


@dataclass(frozen=True)
class ResolvedSimulatorRun:
    request_id: str
    data_axes: dict[str, Any]
    truth_requirements: dict[str, Any]
    run_id: str
    simulator_id: str
    organism: dict[str, Any]
    requested_extras: list[str]
    effective_extras: list[str]
    inputs: dict[str, dict[str, Any]]
    resolved_input_paths: dict[str, Path]
    simulator_params: dict[str, Any]
    runtime_resources: dict[str, Any]
    native_outputs: list[str]
    replicates: int
    base_seed: Optional[int]
    replicate_seeds: list[int]
    notes: Optional[str]
    simulator_spec: dict[str, Any]


@dataclass(frozen=True)
class ResolvedSimulationPlan:
    request_id: str
    data_axes: dict[str, Any]
    truth_requirements: dict[str, Any]
    organism: dict[str, Any]
    requested_extras: list[str]
    effective_extras: list[str]
    inputs: dict[str, dict[str, Any]]
    resolved_input_paths: dict[str, Path]
    base_seed: Optional[int]
    notes: Optional[str]
    simulator_runs: list[ResolvedSimulatorRun]
    tasks: list[dict[str, Any]]
    execution: dict[str, Any]
    plan_payload: dict[str, Any]


@dataclass(frozen=True)
class ResolvedScenarioRequest:
    request_id: str
    data_axes: dict[str, Any]
    truth_requirements: dict[str, Any]
    organism: dict[str, Any]
    requested_extras: list[str]
    effective_extras: list[str]
    inputs: dict[str, dict[str, Any]]
    resolved_input_paths: dict[str, Path]
    base_seed: Optional[int]
    notes: Optional[str]
    request_payload: dict[str, Any]


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    shutil.copytree(src, dst)


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _relative_posix(path: Path, start: Path) -> str:
    return path.relative_to(start).as_posix()


def _stable_seed_base(*, request_id: str, semantic_key: str, simulator_id: str) -> int:
    digest = hashlib.sha256(
        f"{request_id}|{semantic_key}|{simulator_id}".encode("utf-8")
    ).hexdigest()
    return (int(digest[:8], 16) % MAX_SEED_32BIT) + 1
