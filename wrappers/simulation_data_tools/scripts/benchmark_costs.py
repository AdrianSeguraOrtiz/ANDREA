"""Benchmark simulator wrappers and write per-simulator cost profiles."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

_REPO_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_IMPORTS))

from andrea.core.commands.generate_data.request import (
    _resolve_simulator_params,
    resolve_simulator_runtime_resources,
    validate_simulator_inputs,
)
from andrea.core.commands.generate_data.catalog import get_semantic_capability
from andrea.core.commands.generate_data.semantic import (
    parse_truth_requirements,
    required_extras_for_request,
    semantic_key_from_json,
)

from shared.catalog_simulators import (
    DEFAULT_CATALOG_SIMULATORS_ROOT,
    DEFAULT_WRAPPERS_ROOT,
    REPO_ROOT,
    discover_catalog_simulator_dirs,
    load_json,
    load_simulatorspec,
    select_simulators,
)
from shared.param_profiles import DEFAULT_PARAM_OVERRIDES_DIR

SIMULATION_TOOLS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COST_PROFILES_DIR = SIMULATION_TOOLS_ROOT / "cost_profiles"
DEFAULT_SIZES = ("50x20", "100x40", "200x80")
DEFAULT_THREADS = "1,2,4,8"
DEFAULT_RAM_GB = "8,16,32,64"
DEFAULT_GROUP_COUNT = 2

CONDITIONAL_OPS = {"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte"}


@dataclass(frozen=True)
class SizePoint:
    genes: int
    cells: int


@dataclass(frozen=True)
class RunPlanItem:
    size: SizePoint
    threads: int
    ram_gb: int
    repeat: int


@dataclass(frozen=True)
class SimulatorBenchmarkProfile:
    simulator_id: str
    profile_id: str
    profile: str
    data_axes: dict[str, Any]
    truth_requirements: dict[str, Any]
    sizes: tuple[SizePoint, ...] | None
    requested_extras: tuple[str, ...]
    effective_extras: tuple[str, ...]
    params: dict[str, Any]
    params_profile: dict[str, Any]
    runtime_resources_profile: dict[str, Any]
    dimension_profile: dict[str, Any]
    input_profile: dict[str, Any]
    input_paths: dict[str, Path]


@dataclass(frozen=True)
class SimulatorBenchmarkTarget:
    simulator_id: str
    catalog_simulator_dir: Path
    spec: dict[str, Any]
    profiles: tuple[SimulatorBenchmarkProfile, ...]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark simulator wrappers under different sizes/resources and "
            "write catalog cost.json profiles."
        )
    )
    parser.add_argument(
        "--catalog-simulators-root",
        type=Path,
        default=DEFAULT_CATALOG_SIMULATORS_ROOT,
        help=f"Path to catalog simulators directory. Default: {DEFAULT_CATALOG_SIMULATORS_ROOT}",
    )
    parser.add_argument(
        "--wrappers-root",
        type=Path,
        default=DEFAULT_WRAPPERS_ROOT,
        help=f"Path to simulator wrapper directories. Default: {DEFAULT_WRAPPERS_ROOT}",
    )
    parser.add_argument(
        "--param-overrides-dir",
        type=Path,
        default=DEFAULT_PARAM_OVERRIDES_DIR,
        help=f"Path to optional per-simulator parameter overrides. Default: {DEFAULT_PARAM_OVERRIDES_DIR}",
    )
    parser.add_argument(
        "--cost-profiles-dir",
        type=Path,
        default=DEFAULT_COST_PROFILES_DIR,
        help=f"Path to per-simulator benchmark profile configs. Default: {DEFAULT_COST_PROFILES_DIR}",
    )
    parser.add_argument(
        "--simulator",
        action="append",
        default=[],
        help="Simulator id to benchmark (repeatable). If omitted, benchmarks all catalog simulators.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        help=(
            "Benchmark profile id to run (repeatable). Accepts PROFILE_ID or "
            "SIMULATOR_ID:PROFILE_ID."
        ),
    )
    parser.add_argument(
        "--size",
        action="append",
        default=[],
        help=(
            "Benchmark size point as GENESxCELLS (repeatable). "
            f"Default: {', '.join(DEFAULT_SIZES)}"
        ),
    )
    parser.add_argument(
        "--threads",
        default=DEFAULT_THREADS,
        help=f"Comma-separated thread counts to test. Default: {DEFAULT_THREADS}",
    )
    parser.add_argument(
        "--ram-gb",
        default=DEFAULT_RAM_GB,
        help=f"Comma-separated memory limits (GB) to test. Default: {DEFAULT_RAM_GB}",
    )
    parser.add_argument(
        "--group-count",
        type=int,
        default=DEFAULT_GROUP_COUNT,
        help=f"Default group/population count for grouped profiles. Default: {DEFAULT_GROUP_COUNT}",
    )
    parser.add_argument(
        "--max-cpu",
        type=int,
        default=8,
        help="Upper CPU cap for this machine. Default: 8",
    )
    parser.add_argument(
        "--max-ram-gb",
        type=int,
        default=64,
        help="Upper RAM cap (GB) for this machine. Default: 64",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of repeats per benchmark point. Default: 1",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout in seconds per run (0 = no timeout). Default: 1800",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip Docker image build step and assume images already exist.",
    )
    parser.add_argument(
        "--no-write-cost",
        action="store_true",
        help="Do not write cost.json; run benchmarks without persisting cost profiles.",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Keep temporary benchmark directories for debugging.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=12345,
        help="Random seed passed to simulator wrappers. Default: 12345",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed run.",
    )
    return parser.parse_args(argv)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def parse_size(value: str) -> SizePoint:
    token = value.strip().lower()
    if "x" not in token:
        raise ValueError(f"Invalid size '{value}'. Expected GENESxCELLS.")
    genes_raw, cells_raw = token.split("x", 1)
    genes = int(genes_raw)
    cells = int(cells_raw)
    if genes < 1 or cells < 1:
        raise ValueError(f"Invalid size '{value}'. Both dimensions must be >= 1.")
    return SizePoint(genes=genes, cells=cells)


def parse_int_csv(value: str, label: str) -> list[int]:
    items: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        parsed = int(token)
        if parsed < 1:
            raise ValueError(f"{label} values must be >= 1. Got: {parsed}")
        items.append(parsed)
    if not items:
        raise ValueError(f"{label} list is empty.")
    return sorted(set(items))


def detect_host_ram_gb() -> float:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) / (1024 * 1024)
    return 8.0


def validate_runtime_args(args: argparse.Namespace) -> None:
    if args.repeats < 1:
        raise RuntimeError("--repeats must be >= 1.")
    if args.group_count < 1:
        raise RuntimeError("--group-count must be >= 1.")
    if args.max_cpu < 1:
        raise RuntimeError("--max-cpu must be >= 1.")
    if args.max_ram_gb < 1:
        raise RuntimeError("--max-ram-gb must be >= 1.")


def resolve_benchmark_dimensions(
    args: argparse.Namespace,
) -> tuple[list[SizePoint], list[int], list[int]]:
    sizes = [parse_size(token) for token in (args.size or list(DEFAULT_SIZES))]
    threads_requested = parse_int_csv(args.threads, "threads")
    ram_requested = parse_int_csv(args.ram_gb, "ram-gb")

    effective_cores = min(os.cpu_count() or 1, int(args.max_cpu))
    effective_ram_gb = min(detect_host_ram_gb(), float(args.max_ram_gb))
    threads = [value for value in threads_requested if value <= effective_cores]
    ram_gb = [value for value in ram_requested if value <= int(effective_ram_gb)]
    if not threads:
        raise RuntimeError(
            f"No thread values <= effective core cap ({effective_cores}). Requested: {threads_requested}"
        )
    if not ram_gb:
        raise RuntimeError(
            f"No RAM values <= effective RAM cap ({int(effective_ram_gb)} GB). Requested: {ram_requested}"
        )
    return sizes, threads, ram_gb


def run_cmd(
    cmd: Sequence[str],
    *,
    timeout_s: int,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=str(REPO_ROOT),
        text=True,
        check=False,
        capture_output=capture_output,
        timeout=None if timeout_s <= 0 else timeout_s,
    )


def docker_image_tag(simulator_id: str) -> str:
    return f"simulator-{simulator_id}:benchmark-local"


def safe_path_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def build_image(
    *,
    simulator_id: str,
    catalog_simulators_root: Path,
    wrappers_root: Path,
    image_tag: str,
) -> None:
    print(f"[{simulator_id}] building image {image_tag}")
    build_script = SIMULATION_TOOLS_ROOT / "scripts" / "build_simulator_images.py"
    result = run_cmd(
        [
            sys.executable,
            str(build_script),
            "--catalog-simulators-root",
            str(catalog_simulators_root),
            "--wrappers-root",
            str(wrappers_root),
            "--simulator",
            simulator_id,
            "--image-tag",
            f"{simulator_id}={image_tag}",
        ],
        timeout_s=0,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Docker build failed for {simulator_id} (exit {result.returncode}).\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )


def load_profile_config(
    *,
    cost_profiles_dir: Path,
    simulator_id: str,
) -> dict[str, Any] | None:
    path = cost_profiles_dir / f"{simulator_id}.json"
    if not path.exists():
        return None
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"cost_profile_config[{simulator_id}] must be an object.")
    return payload


def profile_filter_keys(profile_filters: Sequence[str]) -> set[str]:
    return {token.strip() for token in profile_filters if token.strip()}


def profile_matches_filter(
    *,
    simulator_id: str,
    profile: SimulatorBenchmarkProfile,
    filters: set[str],
) -> bool:
    if not filters:
        return True
    return (
        profile.profile_id in filters
        or f"{simulator_id}:{profile.profile_id}" in filters
    )


def resolve_simulator_targets(
    *,
    selected_simulators: list[tuple[str, Path]],
    catalog_simulators_root: Path,
    cost_profiles_dir: Path,
    param_overrides_dir: Path,
    default_group_count: int,
    profile_filters: Sequence[str],
) -> list[SimulatorBenchmarkTarget]:
    filters = profile_filter_keys(profile_filters)
    matched_filters: set[str] = set()
    targets: list[SimulatorBenchmarkTarget] = []
    for simulator_id, catalog_simulator_dir in selected_simulators:
        spec = load_simulatorspec(catalog_simulators_root, simulator_id)
        profiles = resolve_benchmark_profiles(
            simulator_id=simulator_id,
            spec=spec,
            cost_profiles_dir=cost_profiles_dir,
            param_overrides_dir=param_overrides_dir,
            default_group_count=default_group_count,
        )
        selected_profiles: list[SimulatorBenchmarkProfile] = []
        for profile in profiles:
            qualified = f"{simulator_id}:{profile.profile_id}"
            if profile_matches_filter(
                simulator_id=simulator_id,
                profile=profile,
                filters=filters,
            ):
                selected_profiles.append(profile)
                if profile.profile_id in filters:
                    matched_filters.add(profile.profile_id)
                if qualified in filters:
                    matched_filters.add(qualified)
        if selected_profiles:
            targets.append(
                SimulatorBenchmarkTarget(
                    simulator_id=simulator_id,
                    catalog_simulator_dir=catalog_simulator_dir,
                    spec=spec,
                    profiles=tuple(selected_profiles),
                )
            )
    unknown_filters = sorted(filters.difference(matched_filters))
    if unknown_filters:
        raise RuntimeError(
            f"Unknown benchmark profile filter(s) for selected simulators: {unknown_filters}"
        )
    if not targets:
        raise RuntimeError("No benchmark profiles selected.")
    return targets


def resolve_benchmark_profiles(
    *,
    simulator_id: str,
    spec: dict[str, Any],
    cost_profiles_dir: Path,
    param_overrides_dir: Path,
    default_group_count: int,
) -> list[SimulatorBenchmarkProfile]:
    profile_config = load_profile_config(
        cost_profiles_dir=cost_profiles_dir,
        simulator_id=simulator_id,
    )
    raw_profiles = configured_profiles(
        profile_config=profile_config,
        simulator_id=simulator_id,
    )
    config_path = cost_profiles_dir / f"{simulator_id}.json" if profile_config else None
    return [
        resolve_one_profile(
            simulator_id=simulator_id,
            spec=spec,
            raw_profile=raw_profile,
            raw_profile_index=idx,
            profile_config=profile_config,
            profile_config_path=config_path,
            param_overrides_dir=param_overrides_dir,
            default_group_count=default_group_count,
        )
        for idx, raw_profile in enumerate(raw_profiles, start=1)
    ]


def configured_profiles(
    *,
    profile_config: dict[str, Any] | None,
    simulator_id: str,
) -> list[dict[str, Any]]:
    if profile_config is None:
        raise RuntimeError(
            f"Missing simulator cost profile config for {simulator_id}. "
            "Add wrappers/simulation_data_tools/cost_profiles/<simulator_id>.json."
        )
    inherited_cost_relevant = profile_config.get("cost_relevant_params")
    if inherited_cost_relevant is not None and not isinstance(
        inherited_cost_relevant, list
    ):
        raise RuntimeError("cost_relevant_params must be an array.")
    inherited_dimensions = profile_config.get("dimension_params")
    if inherited_dimensions is not None and not isinstance(inherited_dimensions, dict):
        raise RuntimeError("dimension_params must be an object.")
    inherited_input_source_params = profile_config.get("input_source_params")
    if inherited_input_source_params is not None and not isinstance(
        inherited_input_source_params, list
    ):
        raise RuntimeError("input_source_params must be an array.")
    inherited_sizes = profile_config.get("sizes")
    if inherited_sizes is not None and not isinstance(inherited_sizes, list):
        raise RuntimeError("sizes must be an array.")
    raw_profiles = profile_config.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise RuntimeError("cost profile config must include profiles.")
    out: list[dict[str, Any]] = []
    for idx, raw_profile in enumerate(raw_profiles, start=1):
        if not isinstance(raw_profile, dict):
            raise RuntimeError(f"profiles[{idx}] must be an object.")
        profile = copy.deepcopy(raw_profile)
        if "cost_relevant_params" not in profile and inherited_cost_relevant is not None:
            profile["cost_relevant_params"] = copy.deepcopy(inherited_cost_relevant)
        if "dimension_params" not in profile and inherited_dimensions is not None:
            profile["dimension_params"] = copy.deepcopy(inherited_dimensions)
        if (
            "input_source_params" not in profile
            and inherited_input_source_params is not None
        ):
            profile["input_source_params"] = copy.deepcopy(inherited_input_source_params)
        if "sizes" not in profile and inherited_sizes is not None:
            profile["sizes"] = copy.deepcopy(inherited_sizes)
        out.append(profile)
    return out


def resolve_one_profile(
    *,
    simulator_id: str,
    spec: dict[str, Any],
    raw_profile: dict[str, Any],
    raw_profile_index: int,
    profile_config: dict[str, Any] | None,
    profile_config_path: Path | None,
    param_overrides_dir: Path,
    default_group_count: int,
) -> SimulatorBenchmarkProfile:
    profile_id = profile_id_from(raw_profile, raw_profile_index)
    data_axes = raw_profile.get("data_axes", {})
    truth_requirements = raw_profile.get("truth_requirements", {})
    if not isinstance(data_axes, dict) or not isinstance(truth_requirements, dict):
        raise RuntimeError(
            f"[{simulator_id}:{profile_id}] data_axes and truth_requirements are required."
        )
    capability = get_semantic_capability(
        spec,
        data_axes=data_axes,
        truth_requirements=truth_requirements,
    )
    if capability is None:
        raise RuntimeError(
            f"[{simulator_id}:{profile_id}] data_axes/truth_requirements are not supported."
        )
    profile = semantic_key_from_json(
        data_axes=data_axes,
        truth_requirements=truth_requirements,
    )

    requested_extras = sorted_unique_strings(raw_profile.get("requested_extras", []))
    profile_sizes = resolve_profile_sizes(raw_profile=raw_profile, profile_id=profile_id)
    effective_extras = sorted(
        set(requested_extras).union(
            required_extras_for_request(data_axes, truth_requirements)
        )
    )
    validate_supported_extras(
        simulator_id=simulator_id,
        profile_id=profile_id,
        capability=capability,
        effective_extras=effective_extras,
    )

    base_params, params_profile = resolve_profile_params(
        simulator_id=simulator_id,
        spec=spec,
        raw_profile=raw_profile,
        profile_id=profile_id,
        profile_config_path=profile_config_path,
        param_overrides_dir=param_overrides_dir,
    )
    dimension_profile = resolve_dimension_profile(
        simulator_id=simulator_id,
        spec=spec,
        raw_profile=raw_profile,
        truth_requirements=truth_requirements,
        default_group_count=default_group_count,
    )
    inputs = resolve_profile_input_paths(
        raw_profile=raw_profile,
        profile_config_path=profile_config_path,
    )
    input_profile = build_input_profile(
        simulator_id=simulator_id,
        spec=spec,
        data_axes=data_axes,
        truth_requirements=truth_requirements,
        requested_extras=requested_extras,
        effective_extras=effective_extras,
        params=base_params,
        input_paths=inputs,
        input_source_params=sorted_unique_strings(
            raw_profile.get("input_source_params", [])
        ),
    )
    input_errors = validate_simulator_inputs(
        simulator_id=simulator_id,
        simulator_spec=spec,
        data_axes=data_axes,
        truth_requirements=truth_requirements,
        requested_extras=requested_extras,
        simulator_params=base_params,
        input_ids=set(inputs),
    )
    if input_errors:
        raise RuntimeError(
            f"[{simulator_id}:{profile_id}] invalid input profile: "
            + "; ".join(input_errors)
        )
    runtime_resources_profile = runtime_resources_profile_from_spec(spec)

    return SimulatorBenchmarkProfile(
        simulator_id=simulator_id,
        profile_id=profile_id,
        profile=profile,
        data_axes=data_axes,
        truth_requirements=truth_requirements,
        sizes=profile_sizes,
        requested_extras=tuple(requested_extras),
        effective_extras=tuple(effective_extras),
        params=base_params,
        params_profile=params_profile,
        runtime_resources_profile=runtime_resources_profile,
        dimension_profile=dimension_profile,
        input_profile=input_profile,
        input_paths=inputs,
    )


def profile_id_from(raw_profile: dict[str, Any], idx: int) -> str:
    raw = raw_profile.get("id", raw_profile.get("profile_id"))
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return f"profile_{idx}"


def sorted_unique_strings(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RuntimeError("Expected an array of strings.")
    return sorted(
        dict.fromkeys(str(item).strip() for item in raw if str(item).strip())
    )


def resolve_profile_sizes(
    *,
    raw_profile: dict[str, Any],
    profile_id: str,
) -> tuple[SizePoint, ...] | None:
    raw_sizes = raw_profile.get("sizes")
    if raw_sizes is None:
        return None
    if not isinstance(raw_sizes, list) or not raw_sizes:
        raise RuntimeError(f"profile {profile_id}.sizes must be a non-empty array.")
    sizes: list[SizePoint] = []
    for idx, raw_size in enumerate(raw_sizes, start=1):
        if isinstance(raw_size, str):
            sizes.append(parse_size(raw_size))
            continue
        if isinstance(raw_size, dict):
            genes = raw_size.get("genes")
            cells = raw_size.get("cells")
            if (
                isinstance(genes, bool)
                or isinstance(cells, bool)
                or not isinstance(genes, int)
                or not isinstance(cells, int)
                or genes < 1
                or cells < 1
            ):
                raise RuntimeError(
                    f"profile {profile_id}.sizes[{idx}] must contain integer genes/cells >= 1."
                )
            sizes.append(SizePoint(genes=genes, cells=cells))
            continue
        raise RuntimeError(
            f"profile {profile_id}.sizes[{idx}] must be GENESxCELLS or an object."
        )
    unique = {(item.genes, item.cells): item for item in sizes}
    return tuple(unique[key] for key in sorted(unique))


def validate_supported_extras(
    *,
    simulator_id: str,
    profile_id: str,
    capability: dict[str, Any],
    effective_extras: list[str],
) -> None:
    supported = set(capability.get("native_extras", []))
    supported.update(capability.get("derivable_extras", []))
    unsupported = sorted(set(effective_extras).difference(supported))
    if unsupported:
        raise RuntimeError(
            f"[{simulator_id}:{profile_id}] unsupported extras: {unsupported}"
        )


def resolve_profile_params(
    *,
    simulator_id: str,
    spec: dict[str, Any],
    raw_profile: dict[str, Any],
    profile_id: str,
    profile_config_path: Path | None,
    param_overrides_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    spec_params = spec.get("params", {})
    if not isinstance(spec_params, dict):
        spec_params = {}

    merged_user_params: dict[str, Any] = {}
    override_refs: list[str] = []
    base_override_path = param_overrides_dir / f"{simulator_id}.json"
    if base_override_path.exists():
        base_override = load_json(base_override_path)
        if not isinstance(base_override, dict):
            raise RuntimeError(f"param_override[{simulator_id}] must be an object.")
        validate_param_override_keys(
            override=base_override,
            params_schema=spec_params,
            label=base_override_path.name,
        )
        merged_user_params = deep_merge(merged_user_params, base_override)
        override_refs.append(base_override_path.name)

    file_override = load_profile_param_override(
        raw_profile=raw_profile,
        profile_id=profile_id,
        profile_config_path=profile_config_path,
    )
    if file_override:
        validate_param_override_keys(
            override=file_override,
            params_schema=spec_params,
            label=f"profile {profile_id} param_override_file",
        )
        merged_user_params = deep_merge(merged_user_params, file_override)
        override_refs.append(str(raw_profile["param_override_file"]).strip())

    inline_override = raw_profile.get("param_overrides", {})
    if inline_override is None:
        inline_override = {}
    if not isinstance(inline_override, dict):
        raise RuntimeError(f"profile {profile_id}.param_overrides must be an object.")
    if inline_override:
        validate_param_override_keys(
            override=inline_override,
            params_schema=spec_params,
            label=f"profile {profile_id} param_overrides",
        )
        merged_user_params = deep_merge(merged_user_params, inline_override)
        override_refs.append(profile_ref(profile_id, profile_config_path))

    resolved = _resolve_simulator_params(
        simulator_id=simulator_id,
        user_params=merged_user_params,
        spec_params=spec_params,
    )
    cost_relevant_params = resolve_cost_relevant_params(
        raw_profile=raw_profile,
        params_schema=spec_params,
        profile_id=profile_id,
    )
    return resolved, {
        "source": (
            "simulatorspec_defaults_plus_override"
            if override_refs
            else "simulatorspec_defaults"
        ),
        "override_file": ";".join(override_refs) if override_refs else None,
        "resolved_base_params": resolved,
        "cost_relevant_params": cost_relevant_params,
        "cost_relevant_values": {
            path: value_at_param_path(resolved, path) for path in cost_relevant_params
        },
    }


def resolve_dimension_profile(
    *,
    simulator_id: str,
    spec: dict[str, Any],
    raw_profile: dict[str, Any],
    truth_requirements: dict[str, Any],
    default_group_count: int,
) -> dict[str, Any]:
    raw_dimensions = raw_profile.get("dimension_params")
    if not isinstance(raw_dimensions, dict):
        raise RuntimeError(
            f"[{simulator_id}] benchmark profiles require dimension_params."
        )
    cells_param = normalize_cells_dimension(
        raw_dimensions.get("cells"), params_schema=spec.get("params", {})
    )
    genes_param = raw_dimensions.get("genes")
    if isinstance(genes_param, str):
        validate_param_path(
            path=genes_param,
            params_schema=spec.get("params", {}),
            label="dimension_params.genes",
        )
        normalized_genes: str | dict[str, Any] = genes_param
    elif isinstance(genes_param, dict):
        normalized_genes = normalize_genes_dimension(
            genes_param, params_schema=spec.get("params", {})
        )
    else:
        raise RuntimeError("dimension_params.genes must be a path or weighted object.")

    contexts = set(parse_truth_requirements(truth_requirements).contexts)
    requested_extras = set(sorted_unique_strings(raw_profile.get("requested_extras", [])))
    uses_group_dimension = bool(
        {"group", "column"}.intersection(contexts)
        or requested_extras.intersection(
            {
                "groups",
                "column_phenotypes",
                "cluster_identities",
                "lineage_tree",
                "prior_grn_by_group",
                "cell_cell_interactions",
            }
        )
    )
    group_count = raw_profile.get(
        "group_count",
        default_group_count if uses_group_dimension else 0,
    )
    population_count = raw_profile.get("population_count", group_count)
    if not isinstance(group_count, int) or isinstance(group_count, bool) or group_count < 0:
        raise RuntimeError("profile.group_count must be an integer >= 0.")
    if (
        not isinstance(population_count, int)
        or isinstance(population_count, bool)
        or population_count < 0
    ):
        raise RuntimeError("profile.population_count must be an integer >= 0.")
    if not uses_group_dimension and group_count != 0:
        raise RuntimeError(
            "group_count must be 0 unless group or column truth is requested."
        )
    return {
        "cells_param": cells_param,
        "genes_param": normalized_genes,
        "group_count": group_count,
        "population_count": population_count,
    }


def normalize_cells_dimension(
    raw: Any, *, params_schema: dict[str, Any]
) -> str | dict[str, Any]:
    if isinstance(raw, str):
        cells_param = raw.strip()
        if not cells_param:
            raise RuntimeError("dimension_params.cells must be a parameter path.")
        validate_param_path(
            path=cells_param,
            params_schema=params_schema,
            label="dimension_params.cells",
        )
        return cells_param
    if not isinstance(raw, dict):
        raise RuntimeError("dimension_params.cells must be a parameter path or object.")
    param = str(raw.get("param") or "").strip()
    if not param:
        raise RuntimeError("dimension_params.cells.param must be a parameter path.")
    validate_param_path(
        path=param,
        params_schema=params_schema,
        label="dimension_params.cells.param",
    )
    normalized: dict[str, Any] = {"param": param}
    if "multiplier_param" in raw:
        multiplier_param = str(raw.get("multiplier_param") or "").strip()
        if not multiplier_param:
            raise RuntimeError("dimension_params.cells.multiplier_param must be a parameter path.")
        validate_param_path(
            path=multiplier_param,
            params_schema=params_schema,
            label="dimension_params.cells.multiplier_param",
        )
        normalized["multiplier_param"] = multiplier_param
    if "multiplier" in raw:
        multiplier = raw.get("multiplier")
        if not isinstance(multiplier, int) or isinstance(multiplier, bool) or multiplier < 1:
            raise RuntimeError("dimension_params.cells.multiplier must be an integer >= 1.")
        normalized["multiplier"] = multiplier
    if "offset" in raw:
        offset = raw.get("offset")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise RuntimeError("dimension_params.cells.offset must be an integer >= 0.")
        normalized["offset"] = offset
    unknown = sorted(set(raw).difference({"param", "multiplier_param", "multiplier", "offset"}))
    if unknown:
        raise RuntimeError(f"dimension_params.cells has unknown key(s): {unknown}")
    if "multiplier_param" in normalized and "multiplier" in normalized:
        raise RuntimeError("dimension_params.cells can use multiplier or multiplier_param, not both.")
    return normalized


def normalize_genes_dimension(
    raw: dict[str, Any], *, params_schema: dict[str, Any]
) -> dict[str, Any]:
    if set(raw) == {"fixed"}:
        fixed = raw.get("fixed")
        if not isinstance(fixed, int) or isinstance(fixed, bool) or fixed < 1:
            raise RuntimeError("dimension_params.genes.fixed must be an integer >= 1.")
        return {"fixed": fixed}
    normalized: dict[str, dict[str, Any]] = {}
    for path, rule in raw.items():
        if not isinstance(rule, dict):
            raise RuntimeError("dimension_params.genes entries must be objects.")
        validate_param_path(
            path=str(path),
            params_schema=params_schema,
            label="dimension_params.genes",
        )
        normalized[str(path)] = {
            "fraction": float(rule.get("fraction", 0)),
            "min": int(rule.get("min", 0)),
        }
    return normalized


def build_input_profile(
    *,
    simulator_id: str,
    spec: dict[str, Any],
    data_axes: dict[str, Any],
    truth_requirements: dict[str, Any],
    requested_extras: list[str],
    effective_extras: list[str],
    params: dict[str, Any],
    input_paths: dict[str, Path],
    input_source_params: list[str],
) -> dict[str, Any]:
    extra_inputs = spec.get("extra_inputs", {})
    required_declared = set(input_ids_for(extra_inputs, "required"))
    optional_declared = set(input_ids_for(extra_inputs, "optional"))
    conditional = active_conditional_inputs(
        spec=spec,
        data_axes=data_axes,
        truth_requirements=truth_requirements,
        requested_extras=requested_extras,
        params=params,
    )
    provided = set(input_paths)
    required = sorted(provided.intersection(required_declared))
    optional = sorted(provided.intersection(optional_declared))
    input_source_modes = input_source_modes_from_params(params, input_source_params)
    return {
        "requested_extras": requested_extras,
        "effective_extras": effective_extras,
        "required_inputs_satisfied": required,
        "optional_inputs_provided": optional,
        "conditional_inputs_satisfied": conditional,
        "input_source_modes": input_source_modes,
        "notes": [],
    }


def input_ids_for(extra_inputs: Any, field: str) -> list[str]:
    if not isinstance(extra_inputs, dict):
        return []
    raw = extra_inputs.get(field, [])
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            input_id = str(item.get("input") or "").strip()
            if input_id:
                out.append(input_id)
    return sorted(dict.fromkeys(out))


def active_conditional_inputs(
    *,
    spec: dict[str, Any],
    data_axes: dict[str, Any],
    truth_requirements: dict[str, Any],
    requested_extras: list[str],
    params: dict[str, Any],
) -> list[str]:
    extra_inputs = spec.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return []
    requested = set(requested_extras)
    out: list[str] = []
    for requirement in extra_inputs.get("conditional_required", []):
        if not isinstance(requirement, dict):
            continue
        if conditional_requirement_matches(
            requirement=requirement,
            data_axes=data_axes,
            truth_requirements=truth_requirements,
            requested_extras=requested,
            params=params,
        ):
            input_id = str(requirement.get("input") or "").strip()
            if input_id:
                out.append(input_id)
    return sorted(dict.fromkeys(out))


def conditional_requirement_matches(
    *,
    requirement: dict[str, Any],
    data_axes: dict[str, Any],
    truth_requirements: dict[str, Any],
    requested_extras: set[str],
    params: dict[str, Any],
) -> bool:
    conditions = requirement.get("conditions", [])
    if not isinstance(conditions, list) or not conditions:
        return False
    for condition in conditions:
        if not isinstance(condition, dict):
            return False
        field = str(condition.get("field") or "").strip()
        op = str(condition.get("op") or "").strip()
        if op not in CONDITIONAL_OPS:
            return False
        expected = condition.get("value")
        if field.startswith("data_axes."):
            actual = data_axes.get(field.removeprefix("data_axes."))
            matches = compare_condition_value(actual, op, expected)
        elif field == "truth_requirement":
            matches = compare_requested_extra(
                set(parse_truth_requirements(truth_requirements).contexts),
                op,
                expected,
            )
        elif field == "requested_extra":
            matches = compare_requested_extra(requested_extras, op, expected)
        elif field.startswith("param."):
            actual = value_at_param_path(params, field.removeprefix("param."))
            matches = compare_condition_value(actual, op, expected)
        else:
            matches = False
        if not matches:
            return False
    return True


def compare_requested_extra(
    requested_extras: set[str], op: str, expected: Any
) -> bool:
    if op in {"eq", "ne"}:
        matches = expected in requested_extras
        return not matches if op == "ne" else matches
    if op in {"in", "not_in"}:
        expected_values = set(expected if isinstance(expected, list) else [])
        matches = bool(expected_values.intersection(requested_extras))
        return not matches if op == "not_in" else matches
    return False


def compare_condition_value(actual: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return isinstance(expected, list) and actual in expected
    if op == "not_in":
        return isinstance(expected, list) and actual not in expected
    if op in {"gt", "gte", "lt", "lte"}:
        try:
            actual_num = float(actual)
            expected_num = float(expected)
        except (TypeError, ValueError):
            return False
        if op == "gt":
            return actual_num > expected_num
        if op == "gte":
            return actual_num >= expected_num
        if op == "lt":
            return actual_num < expected_num
        if op == "lte":
            return actual_num <= expected_num
    return False


def input_source_modes_from_params(
    params: dict[str, Any], input_source_params: list[str]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if input_source_params:
        for path in input_source_params:
            out[path] = value_at_param_path(params, path)
        return out
    for key, value in params.items():
        if key.endswith("_source") or key.endswith("_mode"):
            out[key] = copy.deepcopy(value)
    return out


def resolve_profile_input_paths(
    *,
    raw_profile: dict[str, Any],
    profile_config_path: Path | None,
) -> dict[str, Path]:
    raw_inputs = raw_profile.get("inputs", {})
    if raw_inputs is None:
        raw_inputs = {}
    if not isinstance(raw_inputs, dict):
        raise RuntimeError("profile.inputs must be an object.")
    out: dict[str, Path] = {}
    for input_id, raw_path in raw_inputs.items():
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise RuntimeError(f"profile.inputs[{input_id}] must be a non-empty path.")
        path = Path(raw_path.strip())
        if not path.is_absolute():
            if profile_config_path is None:
                raise RuntimeError(
                    f"Relative input path requires a profile config path: {raw_path}"
                )
            path = (profile_config_path.parent / path).resolve()
        if not path.exists():
            raise RuntimeError(f"profile input file not found: {path}")
        out[str(input_id)] = path
    return out


def runtime_resources_profile_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    threading = spec.get("runtime_resources", {}).get("threading", {})
    return {
        "threading_supported": bool(threading.get("supported", False)),
        "default_threads": int(threading.get("default_threads", 1)),
        "max_threads": int(threading.get("max_threads", 1)),
        "upstream_mapping": str(threading.get("upstream_mapping", "")),
    }


def apply_dimensions_to_params(
    *,
    base_params: dict[str, Any],
    dimension_profile: dict[str, Any],
    size: SizePoint,
) -> dict[str, Any]:
    params = copy.deepcopy(base_params)
    genes_param = dimension_profile["genes_param"]
    if isinstance(genes_param, str):
        set_param_path(params, genes_param, size.genes)
    elif isinstance(genes_param, dict):
        if "fixed" in genes_param:
            fixed = int(genes_param["fixed"])
            if size.genes != fixed:
                raise RuntimeError(
                    f"Benchmark size requests {size.genes} genes, but this profile has fixed gene count {fixed}."
                )
        else:
            allocations = allocate_weighted_counts(size.genes, genes_param)
            for path, value in allocations.items():
                set_param_path(params, path, value)
    else:
        raise RuntimeError("Invalid dimension_profile.genes_param.")
    apply_cells_dimension(params, dimension_profile["cells_param"], size)
    return params


def apply_cells_dimension(
    params: dict[str, Any],
    cells_param: str | dict[str, Any],
    size: SizePoint,
) -> None:
    if isinstance(cells_param, str):
        set_param_path(params, cells_param, size.cells)
        return
    if not isinstance(cells_param, dict):
        raise RuntimeError("Invalid dimension_profile.cells_param.")
    offset = int(cells_param.get("offset", 0))
    adjusted_cells = size.cells - offset
    if adjusted_cells < 1:
        raise RuntimeError(
            f"Benchmark size requests {size.cells} columns, but cell offset {offset} leaves no positive parameter value."
        )
    multiplier = 1
    if "multiplier" in cells_param:
        multiplier = int(cells_param["multiplier"])
    elif "multiplier_param" in cells_param:
        raw_multiplier = value_at_param_path(params, str(cells_param["multiplier_param"]))
        if not isinstance(raw_multiplier, (int, float)) or isinstance(raw_multiplier, bool):
            raise RuntimeError(
                f"Cannot resolve cell multiplier parameter: {cells_param['multiplier_param']}"
            )
        multiplier = int(raw_multiplier)
    if multiplier < 1:
        raise RuntimeError("Cell dimension multiplier must be >= 1.")
    if adjusted_cells % multiplier != 0:
        raise RuntimeError(
            f"Benchmark size requests {size.cells} columns with offset {offset}, "
            f"which is not divisible by cell multiplier {multiplier}."
        )
    set_param_path(params, str(cells_param["param"]), int(adjusted_cells / multiplier))


def allocate_weighted_counts(
    total: int, rules: dict[str, dict[str, Any]]
) -> dict[str, int]:
    min_total = sum(int(rule.get("min", 0)) for rule in rules.values())
    if min_total > total:
        raise RuntimeError(
            f"Cannot allocate {total} genes; minimum configured total is {min_total}."
        )
    remaining = total - min_total
    raw_shares: list[tuple[str, float]] = []
    for path, rule in rules.items():
        raw_shares.append((path, float(rule.get("fraction", 0)) * remaining))
    base = {path: int(rules[path].get("min", 0)) + int(math.floor(share)) for path, share in raw_shares}
    assigned = sum(base.values())
    fractional_order = sorted(
        raw_shares,
        key=lambda item: (item[1] - math.floor(item[1]), item[0]),
        reverse=True,
    )
    idx = 0
    while assigned < total and fractional_order:
        path = fractional_order[idx % len(fractional_order)][0]
        base[path] += 1
        assigned += 1
        idx += 1
    return base


def iter_run_plan(
    *, sizes: list[SizePoint], threads: list[int], ram_gb: list[int], repeats: int
) -> Iterator[RunPlanItem]:
    for size in sizes:
        for thread_count in threads:
            for ram_limit in ram_gb:
                for repeat_idx in range(1, repeats + 1):
                    yield RunPlanItem(
                        size=size,
                        threads=thread_count,
                        ram_gb=ram_limit,
                        repeat=repeat_idx,
                    )


def run_container_once(
    *,
    image_tag: str,
    workdir: Path,
    simulator_id: str,
    profile: SimulatorBenchmarkProfile,
    params: dict[str, Any],
    threads: int,
    ram_gb: int,
    seed: int,
    timeout_s: int,
) -> tuple[str, float, int | None, float | None, str]:
    request_dir = workdir / "request"
    inputs_dir = workdir / "inputs"
    out_dir = workdir / "out"
    request_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    mounted_inputs = stage_inputs(profile.input_paths, inputs_dir)
    request_payload = {
        "schema_version": "1.0",
        "simulator_id": simulator_id,
        "profile": profile.profile_id,
        "data_axes": profile.data_axes,
        "truth_requirements": profile.truth_requirements,
        "seed": int(seed),
        "effective_extras": list(profile.effective_extras),
        "mounted_inputs": mounted_inputs,
        "params": params,
        "runtime_resources": {"threads": int(threads)},
        "output_dir_in_container": "/work/out",
    }
    save_json(request_dir / "simulator-run-request.json", request_payload)

    uid_gid = f"{os.getuid()}:{os.getgid()}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--user",
        uid_gid,
        "--cpus",
        str(threads),
        "--memory",
        f"{ram_gb}g",
        "-v",
        f"{request_dir}:/work/request:ro",
        "-v",
        f"{inputs_dir}:/work/inputs:ro",
        "-v",
        f"{out_dir}:/work/out",
        image_tag,
    ]
    started = time.perf_counter()
    try:
        result = run_cmd(cmd, timeout_s=timeout_s, capture_output=True)
        elapsed = time.perf_counter() - started
        logs = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        if result.returncode != 0:
            return (classify_failure(logs), elapsed, None, None, logs.strip())
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - started
        return ("timeout", elapsed, None, None, f"Run exceeded timeout {timeout_s}s.")

    manifest_path = out_dir / "simulator-output-manifest.json"
    expression_path = out_dir / "expression.tsv"
    truth_path = out_dir / "truth" / "networks.csv"
    if not manifest_path.exists() or not expression_path.exists() or not truth_path.exists():
        return (
            "error",
            elapsed,
            output_tree_size_bytes(out_dir),
            None,
            "Container finished but normalized output files are missing.",
        )
    return ("ok", elapsed, output_tree_size_bytes(out_dir), None, "")


def stage_inputs(input_paths: dict[str, Path], inputs_dir: Path) -> dict[str, str]:
    mounted: dict[str, str] = {}
    for input_id, source_path in input_paths.items():
        target = inputs_dir / input_id
        if source_path.is_dir():
            shutil.copytree(source_path, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)
        mounted[input_id] = f"/work/inputs/{input_id}"
    return mounted


def output_tree_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def classify_failure(logs: str) -> str:
    text = logs.lower()
    if any(
        marker in text
        for marker in ("oom", "out of memory", "cannot allocate memory", "oomkilled")
    ):
        return "oom"
    return "error"


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("Cannot compute percentile of empty sequence.")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (q / 100.0) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def percentile_int(values: Sequence[int], q: float) -> int:
    return int(round(percentile([float(value) for value in values], q)))


def aggregate_runtime_points(
    *,
    profile: SimulatorBenchmarkProfile,
    all_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int, int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for run in all_runs:
        key = (
            int(run["genes"]),
            int(run["cells"]),
            int(run["groups"]),
            int(run["population_count"]),
            int(run["threads"]),
            int(run["ram_gb"]),
        )
        grouped[key].append(run)

    points: list[dict[str, Any]] = []
    for (genes, cells, groups, population_count, threads, ram_gb), runs in sorted(grouped.items()):
        total = len(runs)
        ok_runs = [item for item in runs if item["status"] == "ok"]
        ok_secs = [float(item["seconds"]) for item in ok_runs]
        ok_output_bytes = [
            int(item["output_bytes"]) for item in ok_runs if item.get("output_bytes") is not None
        ]
        ok_peak_memory = [
            float(item["peak_memory_mb"])
            for item in ok_runs
            if item.get("peak_memory_mb") is not None
        ]
        status_counts: dict[str, int] = defaultdict(int)
        for run in runs:
            status_counts[str(run.get("status", "error"))] += 1

        failure_breakdown = {
            "oom": int(status_counts.get("oom", 0)),
            "timeout": int(status_counts.get("timeout", 0)),
            "error": int(
                sum(
                    count
                    for status, count in status_counts.items()
                    if status not in {"ok", "oom", "timeout"}
                )
            ),
        }
        if len(ok_runs) == total:
            status = "ok"
        elif ok_runs:
            status = "partial"
        elif failure_breakdown["oom"]:
            status = "oom"
        elif failure_breakdown["timeout"]:
            status = "timeout"
        else:
            status = "error"

        point = {
            "genes": genes,
            "cells": cells,
            "groups": groups,
            "population_count": population_count,
            "threads": threads,
            "ram_gb": float(ram_gb),
            "status": status,
            "repeats_total": total,
            "repeats_ok": len(ok_runs),
            "repeats_failed": total - len(ok_runs),
            "ok_rate": round(len(ok_runs) / total, 6),
            "failure_breakdown": failure_breakdown,
            "seconds_p50": round(percentile(ok_secs, 50), 6) if ok_secs else None,
            "seconds_p90": round(percentile(ok_secs, 90), 6) if ok_secs else None,
            "output_bytes_p50": percentile_int(ok_output_bytes, 50) if ok_output_bytes else None,
            "output_bytes_p90": percentile_int(ok_output_bytes, 90) if ok_output_bytes else None,
            "peak_memory_mb_p50": round(percentile(ok_peak_memory, 50), 6) if ok_peak_memory else None,
            "peak_memory_mb_p90": round(percentile(ok_peak_memory, 90), 6) if ok_peak_memory else None,
            "feature_vector": build_feature_vector(
                profile=profile,
                genes=genes,
                cells=cells,
                groups=groups,
                population_count=population_count,
                threads=threads,
                ram_gb=ram_gb,
            ),
        }
        points.append(point)
    return points


def build_feature_vector(
    *,
    profile: SimulatorBenchmarkProfile,
    genes: int,
    cells: int,
    groups: int,
    population_count: int,
    threads: int,
    ram_gb: int,
) -> dict[str, Any]:
    n_tfs = infer_tf_count(profile=profile, genes=genes)
    dynamic_flags = dynamic_grn_flags(profile.params)
    truth_contexts = list(parse_truth_requirements(profile.truth_requirements).contexts)
    requested_extras = list(profile.input_profile["requested_extras"])
    effective_extras = list(profile.input_profile["effective_extras"])
    return {
        "simulator_id": profile.simulator_id,
        "data_axes": profile.data_axes,
        "truth_requirements": profile.truth_requirements,
        "benchmark_profile_id": profile.profile_id,
        "profile_id": profile.profile_id,
        "expression_profile": profile.data_axes["resolution"],
        "column_kind": profile.data_axes["column_kind"],
        "experimental_design": profile.data_axes["experimental_design"],
        "truth_context_families": truth_contexts,
        "truth_context_count": len(truth_contexts),
        "extras": effective_extras,
        "requested_extras": requested_extras,
        "effective_extras": effective_extras,
        "genes": genes,
        "cells": cells,
        "groups": groups,
        "population_count": population_count,
        "n_genes": genes,
        "n_cells": cells,
        "n_tfs": n_tfs,
        "column_truth_requested": "column" in set(truth_contexts),
        "dynamic_grn_flags": dynamic_flags,
        "requested_extras_count": len(requested_extras),
        "effective_extras_count": len(effective_extras),
        "required_inputs_count": len(profile.input_profile["required_inputs_satisfied"]),
        "optional_inputs_count": len(profile.input_profile["optional_inputs_provided"]),
        "conditional_inputs_count": len(profile.input_profile["conditional_inputs_satisfied"]),
        "threads": threads,
        "ram_gb": float(ram_gb),
        "cost_relevant_values": copy.deepcopy(
            profile.params_profile["cost_relevant_values"]
        ),
        "input_source_modes": copy.deepcopy(profile.input_profile["input_source_modes"]),
    }


def infer_tf_count(*, profile: SimulatorBenchmarkProfile, genes: int) -> int | None:
    value = value_at_param_path(profile.params, "num_tfs")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    genes_param = profile.dimension_profile.get("genes_param")
    if isinstance(genes_param, dict) and "num_tfs" in genes_param:
        try:
            allocated = allocate_weighted_counts(genes, genes_param)
        except RuntimeError:
            return None
        value = allocated.get("num_tfs")
        if isinstance(value, int):
            return max(0, value)
    return None


def dynamic_grn_flags(params: dict[str, Any]) -> dict[str, Any]:
    keywords = ("dynamic", "grn", "backbone")
    out: dict[str, Any] = {}
    for path, value in flatten_values(params).items():
        lowered = path.lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[path] = copy.deepcopy(value)
    return out


def flatten_values(payload: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {prefix: payload} if prefix else {}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        child = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(flatten_values(value, child))
        else:
            out[child] = value
    return out


def build_benchmark_config(
    *,
    simulator_id: str,
    profile: SimulatorBenchmarkProfile,
    sizes: list[SizePoint],
    threads: list[int],
    ram_gb: list[int],
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "simulator_id": simulator_id,
        "data_axes": profile.data_axes,
        "truth_requirements": profile.truth_requirements,
        "sizes": [{"genes": s.genes, "cells": s.cells} for s in sizes],
        "threads_tested": [int(item) for item in threads],
        "ram_gb_tested": [float(item) for item in ram_gb],
        "repeats": int(args.repeats),
        "timeout_seconds": int(args.timeout),
        "dimension_profile": profile.dimension_profile,
        "input_profile": profile.input_profile,
        "params_profile": profile.params_profile,
        "runtime_resources_profile": profile.runtime_resources_profile,
    }


def make_cost_profile_entry(
    *,
    profile_id: str,
    benchmark_config: dict[str, Any],
    runtime_points: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "benchmark_config": benchmark_config,
        "runtime_points": runtime_points,
    }


def make_cost_payload(*, profile_entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not profile_entries:
        raise ValueError("Cannot build simulator cost payload without profiles.")
    return {"schema_version": "1.0", "profiles": profile_entries}


def allocate_simulator_workdir(
    simulator_id: str, keep_workdir: bool
) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    if keep_workdir:
        preserved = tempfile.mkdtemp(prefix=f"benchmark_simulator_{simulator_id}_")
        print(f"[{simulator_id}] keeping benchmark workdir: {preserved}")
        return Path(preserved), None
    context = tempfile.TemporaryDirectory(prefix=f"benchmark_simulator_{simulator_id}_")
    return Path(context.name), context


def execute_profile_benchmarks(
    *,
    simulator_id: str,
    spec: dict[str, Any],
    image_tag: str,
    workdir: Path,
    profile: SimulatorBenchmarkProfile,
    sizes: list[SizePoint],
    threads: list[int],
    ram_gb: list[int],
    repeats: int,
    seed: int,
    timeout: int,
    fail_fast: bool,
    run_index: int,
    total_runs: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    profile_runs: list[dict[str, Any]] = []
    for plan in iter_run_plan(
        sizes=sizes, threads=threads, ram_gb=ram_gb, repeats=repeats
    ):
        run_index += 1
        print(
            f"[{run_index}/{total_runs}] {simulator_id}/{profile.profile_id} "
            f"{plan.size.genes}x{plan.size.cells} "
            f"threads={plan.threads} ram={plan.ram_gb}GB repeat={plan.repeat}"
        )
        params = apply_dimensions_to_params(
            base_params=profile.params,
            dimension_profile=profile.dimension_profile,
            size=plan.size,
        )
        # Validate after dimensions are applied, so range checks remain strict.
        _resolve_simulator_params(
            simulator_id=simulator_id,
            user_params=params,
            spec_params=spec.get("params", {}),
        )
        resolve_simulator_runtime_resources(
            simulator_id=simulator_id,
            simulator_spec=spec,
            raw_resources={"threads": plan.threads},
        )
        run_key = (
            f"{safe_path_token(profile.profile_id)}_g{plan.size.genes}_c{plan.size.cells}"
            f"_t{plan.threads}_m{plan.ram_gb}_r{plan.repeat}"
        )
        run_workdir = workdir / run_key
        status, elapsed, output_bytes, peak_memory_mb, error = run_container_once(
            image_tag=image_tag,
            workdir=run_workdir,
            simulator_id=simulator_id,
            profile=profile,
            params=params,
            threads=plan.threads,
            ram_gb=plan.ram_gb,
            seed=seed,
            timeout_s=timeout,
        )
        payload: dict[str, Any] = {
            "genes": plan.size.genes,
            "cells": plan.size.cells,
            "groups": int(profile.dimension_profile["group_count"]),
            "population_count": int(profile.dimension_profile["population_count"]),
            "threads": plan.threads,
            "ram_gb": plan.ram_gb,
            "repeat": plan.repeat,
            "seconds": round(elapsed, 6),
            "status": status,
            "output_bytes": output_bytes,
            "peak_memory_mb": peak_memory_mb,
        }
        if error:
            payload["error"] = error
        profile_runs.append(payload)
        print(f"  -> {status} ({elapsed:.3f}s)")
        if status != "ok" and error:
            compact_error = " ".join(str(error).split())
            print(f"     error: {compact_error[:2000]}")
        if status != "ok" and fail_fast:
            return profile_runs, run_index, True
    return profile_runs, run_index, False


def sizes_for_profile(
    *,
    profile: SimulatorBenchmarkProfile,
    cli_sizes: list[SizePoint],
    cli_sizes_were_explicit: bool,
) -> list[SizePoint]:
    if cli_sizes_were_explicit or profile.sizes is None:
        return cli_sizes
    return list(profile.sizes)


def write_simulator_cost_profile(*, cost_path: Path, payload: dict[str, Any]) -> None:
    save_json(cost_path, payload)


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [run for run in runs if run["status"] == "ok"]
    failed = [run for run in runs if run["status"] != "ok"]
    return {
        "total_runs": len(runs),
        "successful_runs": len(successful),
        "failed_runs": len(failed),
    }


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    validate_runtime_args(args)
    sizes, threads, ram_gb = resolve_benchmark_dimensions(args)
    cli_sizes_were_explicit = bool(args.size)

    discovered = discover_catalog_simulator_dirs(args.catalog_simulators_root)
    selected = select_simulators(discovered, args.simulator)
    targets = resolve_simulator_targets(
        selected_simulators=selected,
        catalog_simulators_root=args.catalog_simulators_root,
        cost_profiles_dir=args.cost_profiles_dir,
        param_overrides_dir=args.param_overrides_dir,
        default_group_count=args.group_count,
        profile_filters=args.profile,
    )

    total_runs = sum(
        len(
            sizes_for_profile(
                profile=profile,
                cli_sizes=sizes,
                cli_sizes_were_explicit=cli_sizes_were_explicit,
            )
        )
        * len(threads)
        * len(ram_gb)
        * args.repeats
        for target in targets
        for profile in target.profiles
    )
    run_index = 0
    global_success = 0
    global_fail = 0
    fail_fast_triggered = False

    for target in targets:
        simulator_id = target.simulator_id
        image_tag = docker_image_tag(simulator_id)
        wrapper_dir = args.wrappers_root / simulator_id
        if not wrapper_dir.is_dir():
            print(f"[{simulator_id}] ERROR: missing wrapper dir: {wrapper_dir}", file=sys.stderr)
            if args.fail_fast:
                break
            continue

        workdir_context: tempfile.TemporaryDirectory[str] | None = None
        cost_entries: list[dict[str, Any]] = []
        simulator_total = 0
        simulator_ok = 0
        simulator_failed = 0
        wrote_cost = False
        try:
            if not args.skip_build:
                build_image(
                    simulator_id=simulator_id,
                    catalog_simulators_root=args.catalog_simulators_root,
                    wrappers_root=args.wrappers_root,
                    image_tag=image_tag,
                )
            workdir, workdir_context = allocate_simulator_workdir(
                simulator_id, args.keep_workdir
            )
            for profile in target.profiles:
                profile_sizes = sizes_for_profile(
                    profile=profile,
                    cli_sizes=sizes,
                    cli_sizes_were_explicit=cli_sizes_were_explicit,
                )
                print(f"[{simulator_id}] profile: {profile.profile_id}")
                profile_workdir = workdir / safe_path_token(profile.profile_id)
                profile_runs, run_index, fail_fast_triggered = execute_profile_benchmarks(
                    simulator_id=simulator_id,
                    spec=target.spec,
                    image_tag=image_tag,
                    workdir=profile_workdir,
                    profile=profile,
                    sizes=profile_sizes,
                    threads=threads,
                    ram_gb=ram_gb,
                    repeats=args.repeats,
                    seed=args.seed,
                    timeout=args.timeout,
                    fail_fast=args.fail_fast,
                    run_index=run_index,
                    total_runs=total_runs,
                )
                summary = summarize_runs(profile_runs)
                simulator_total += summary["total_runs"]
                simulator_ok += summary["successful_runs"]
                simulator_failed += summary["failed_runs"]
                global_success += summary["successful_runs"]
                global_fail += summary["failed_runs"]
                runtime_points = aggregate_runtime_points(
                    profile=profile,
                    all_runs=profile_runs,
                )
                if runtime_points:
                    cost_entries.append(
                        make_cost_profile_entry(
                            profile_id=profile.profile_id,
                            benchmark_config=build_benchmark_config(
                                simulator_id=simulator_id,
                                profile=profile,
                                sizes=profile_sizes,
                                threads=threads,
                                ram_gb=ram_gb,
                                args=args,
                            ),
                            runtime_points=runtime_points,
                        )
                    )
                print(
                    f"[{simulator_id}/{profile.profile_id}] summary: "
                    f"total={summary['total_runs']} ok={summary['successful_runs']} "
                    f"failed={summary['failed_runs']}"
                )
                if fail_fast_triggered:
                    break
            if cost_entries and not args.no_write_cost:
                write_simulator_cost_profile(
                    cost_path=target.catalog_simulator_dir / "cost.json",
                    payload=make_cost_payload(profile_entries=cost_entries),
                )
                wrote_cost = True
            elif not cost_entries:
                print(f"[{simulator_id}] warning: no profile runs were observed.")
            print(
                f"[{simulator_id}] summary: profiles={len(cost_entries)}/{len(target.profiles)} "
                f"total={simulator_total} ok={simulator_ok} failed={simulator_failed} "
                f"wrote_cost={wrote_cost}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[{simulator_id}] ERROR: {exc}", file=sys.stderr)
            if args.fail_fast:
                break
        finally:
            if workdir_context is not None:
                workdir_context.cleanup()
        if fail_fast_triggered:
            break

    print()
    print(
        f"Benchmark finished. successful_runs={global_success} failed_runs={global_fail}"
    )
    return 0


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = {key: copy.deepcopy(value) for key, value in base.items()}
        for key, value in override.items():
            merged[key] = deep_merge(merged.get(key), value) if key in merged else copy.deepcopy(value)
        return merged
    return copy.deepcopy(override)


def validate_param_override_keys(
    *,
    override: dict[str, Any],
    params_schema: dict[str, Any],
    label: str,
) -> None:
    unknown = sorted(set(override).difference(params_schema))
    if unknown:
        raise RuntimeError(f"{label} contains unknown parameter keys: {unknown}")
    for key, value in override.items():
        param_def = params_schema.get(key)
        if not isinstance(param_def, dict) or param_def.get("type") != "object":
            continue
        if not isinstance(value, dict):
            continue
        properties = param_def.get("properties", {})
        if isinstance(properties, dict):
            validate_param_override_keys(
                override=value,
                params_schema=properties,
                label=f"{label}.{key}",
            )


def resolve_cost_relevant_params(
    *,
    raw_profile: dict[str, Any],
    params_schema: dict[str, Any],
    profile_id: str,
) -> list[str]:
    raw_paths = raw_profile.get("cost_relevant_params", [])
    if raw_paths is None:
        raw_paths = []
    if not isinstance(raw_paths, list):
        raise RuntimeError(f"profile {profile_id}.cost_relevant_params must be an array.")
    paths = [
        str(path).strip()
        for path in raw_paths
        if isinstance(path, str) and str(path).strip()
    ]
    for path in paths:
        validate_param_path(
            path=path,
            params_schema=params_schema,
            label=f"profile {profile_id}.cost_relevant_params",
        )
    return list(dict.fromkeys(paths))


def validate_param_path(*, path: str, params_schema: dict[str, Any], label: str) -> None:
    current = params_schema
    consumed: list[str] = []
    for idx, part in enumerate(path.split(".")):
        if not part:
            raise RuntimeError(f"{label} contains empty path segment: {path}")
        if part not in current:
            prefix = ".".join(consumed) if consumed else "(root)"
            raise RuntimeError(f"{label} references unknown path '{path}' at {prefix}/{part}.")
        param_def = current[part]
        consumed.append(part)
        if idx == len(path.split(".")) - 1:
            return
        if not isinstance(param_def, dict) or param_def.get("type") != "object":
            raise RuntimeError(
                f"{label} references nested path '{path}', but {'.'.join(consumed)} is not an object."
            )
        properties = param_def.get("properties", {})
        if not isinstance(properties, dict):
            raise RuntimeError(
                f"{label} references nested path '{path}', but {'.'.join(consumed)} has no properties."
            )
        current = properties


def value_at_param_path(params: dict[str, Any], path: str) -> Any:
    current: Any = params
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return copy.deepcopy(current)


def set_param_path(params: dict[str, Any], path: str, value: Any) -> None:
    current: Any = params
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise RuntimeError(f"Cannot set missing nested param path: {path}")
        current = current[part]
    if not isinstance(current, dict):
        raise RuntimeError(f"Cannot set non-object nested param path: {path}")
    current[parts[-1]] = value


def load_profile_param_override(
    *,
    raw_profile: dict[str, Any],
    profile_id: str,
    profile_config_path: Path | None,
) -> dict[str, Any]:
    raw_file = raw_profile.get("param_override_file")
    if raw_file is None:
        return {}
    if not isinstance(raw_file, str) or not raw_file.strip():
        raise RuntimeError(f"profile {profile_id}.param_override_file must be a string.")
    if profile_config_path is None:
        raise RuntimeError("param_override_file requires a profile config path.")
    override_path = profile_config_path.parent / raw_file.strip()
    payload = load_json(override_path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"profile_param_override[{profile_id}] must be an object.")
    return payload


def profile_ref(profile_id: str, profile_config_path: Path | None) -> str:
    if profile_config_path is None:
        return f"profile:{profile_id}:inline"
    return f"{profile_config_path.name}:{profile_id}:inline"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
