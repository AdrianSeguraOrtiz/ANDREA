"""Benchmark inference tools and write per-tool cost profiles (`cost.json`).

This script runs each tool wrapper in Docker for multiple input sizes and
resource configurations (threads + memory limit), measures runtime, and writes:
1) `andrea/catalog_inference_tools/tools/<tool_id>/cost.json` files.

Usage examples:
1) Benchmark all tools with defaults (up to host cap, max 8 CPU / 64 GB):
   python benchmark_costs.py

2) Benchmark selected tools:
   python benchmark_costs.py --tool genie3 --tool grnboost2

3) Customize matrix:
   python benchmark_costs.py \
     --size 50x20 --size 100x40 --size 200x80 \
     --threads 1,2,4,8 \
     --ram-gb 8,16,32,64

4) Run selected cost profiles:
   python benchmark_costs.py \
     --tool genie3 \
     --profile global_tf_list \
     --profile group_emulated_groups_2_tf_list

Exit codes:
- 0: script completed (even if some runs failed; inspect report summary)
- 2: usage/runtime error (invalid args, missing paths, etc.)

Cost model written to cost.json:
- schema_version: always "1.0".
- profiles: empirical runtime profiles keyed by execution/input/parameter profile.
- runtime_points: per profile, aggregated per (genes, columns, threads, ram_gb),
  including status/failure information, ok_rate, failure_breakdown, and p50/p90
  runtime estimates.

For `execution.mode=group_emulated`, each runtime point measures one physical
wrapper task. The logical group count is stored in `execution_profile`; planner
ETA code applies the group/task multiplier later. For `group_aggregated`, the
benchmarkable upstream execution is still `cell_native`; ANDREA adds a
deterministic aggregation overhead during planning.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from shared.benchmark_inputs import (
    BenchmarkInputProfile,
    BenchmarkInputSize,
    write_benchmark_io_dir,
)
from shared.benchmark_profiles import (
    DEFAULT_GROUP_COUNT,
    DEFAULT_PRIOR_DENSITY,
    DEFAULT_COST_PROFILES_DIR,
    BenchmarkProfile,
    resolve_benchmark_profiles,
)
from shared.param_profiles import DEFAULT_PARAM_OVERRIDES_DIR

INFERENCE_TOOLS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_ROOT = REPO_ROOT / "andrea" / "catalog_inference_tools"
DEFAULT_CATALOG_TOOLS_ROOT = CATALOG_ROOT / "tools"
DEFAULT_TOOL_SOURCES_ROOT = INFERENCE_TOOLS_ROOT / "tools"
DEFAULT_SIZES = ("50x20", "100x40", "200x80")
DEFAULT_THREADS = "1,2,4,8"
DEFAULT_RAM_GB = "8,16,32,64"


@dataclass(frozen=True)
class SizePoint:
    genes: int
    columns: int


@dataclass(frozen=True)
class RunPlanItem:
    size: SizePoint
    threads: int
    ram_gb: int
    repeat: int


@dataclass(frozen=True)
class ToolBenchmarkTarget:
    tool_id: str
    catalog_tool_dir: Path
    toolspec: dict[str, Any]
    profiles: tuple[BenchmarkProfile, ...]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark tool source wrappers (wrappers/inference_tools/tools/*) "
            "under different sizes/resources and "
            "write cost.json profiles in the runtime catalog."
        )
    )
    parser.add_argument(
        "--catalog-tools-root",
        type=Path,
        default=DEFAULT_CATALOG_TOOLS_ROOT,
        help=(
            "Path to catalog tools directory containing toolspec.json files. "
            f"Default: {DEFAULT_CATALOG_TOOLS_ROOT}"
        ),
    )
    parser.add_argument(
        "--tool-sources-root",
        type=Path,
        default=DEFAULT_TOOL_SOURCES_ROOT,
        help=(
            "Path to tool source directories containing Dockerfile/wrappers. "
            f"Default: {DEFAULT_TOOL_SOURCES_ROOT}"
        ),
    )
    parser.add_argument(
        "--param-overrides-dir",
        type=Path,
        default=DEFAULT_PARAM_OVERRIDES_DIR,
        help=(
            "Path to optional per-tool dev parameter overrides merged onto ToolSpec defaults. "
            f"Default: {DEFAULT_PARAM_OVERRIDES_DIR}"
        ),
    )
    parser.add_argument(
        "--cost-profiles-dir",
        type=Path,
        default=DEFAULT_COST_PROFILES_DIR,
        help=(
            "Path to per-tool benchmark profile configs. "
            f"Default: {DEFAULT_COST_PROFILES_DIR}"
        ),
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        help="Tool id to benchmark (repeatable). If omitted, benchmarks all discovered tools.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        help=(
            "Benchmark profile id to run (repeatable). Accepts PROFILE_ID or "
            "TOOL_ID:PROFILE_ID. If omitted, runs all resolved profiles."
        ),
    )
    parser.add_argument(
        "--size",
        action="append",
        default=[],
        help=(
            "Physical wrapper benchmark size point as GENESxCOLUMNS (repeatable). "
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
        help=(
            "Default group count used when a grouped profile omits group_count. "
            f"Default: {DEFAULT_GROUP_COUNT}"
        ),
    )
    parser.add_argument(
        "--prior-density",
        type=float,
        default=DEFAULT_PRIOR_DENSITY,
        help=(
            "Default synthetic prior density when a profile uses prior-like inputs "
            f"but omits prior_density. Default: {DEFAULT_PRIOR_DENSITY}"
        ),
    )
    parser.add_argument(
        "--optional-input",
        action="append",
        default=[],
        help=(
            "Optional input id to provide in implicit/default profiles when the "
            "ToolSpec declares it optional (repeatable). Explicit cost profile "
            "configs still control their own optional_inputs."
        ),
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
        help="Number of repeats per (size, threads, ram) run. Default: 1",
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
        help="Keep temporary benchmark IO directories for debugging.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=12345,
        help="Random seed used for synthetic data generation. Default: 12345",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after first failed run.",
    )
    return parser.parse_args(argv)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{label} is malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload


def parse_size(value: str) -> SizePoint:
    token = value.strip().lower()
    if "x" not in token:
        raise ValueError(f"Invalid size '{value}'. Expected format: GENESxCOLUMNS")
    left, right = token.split("x", 1)
    genes = int(left)
    columns = int(right)
    if genes < 1 or columns < 1:
        raise ValueError(f"Invalid size '{value}'. Both dimensions must be >= 1.")
    return SizePoint(genes=genes, columns=columns)


def parse_int_csv(value: str, label: str) -> list[int]:
    items = []
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
                    kib = int(parts[1])
                    return kib / (1024 * 1024)
    # Fallback: conservative default if /proc/meminfo is unavailable.
    return 8.0


def discover_catalog_tools(catalog_tools_root: Path) -> list[tuple[str, Path]]:
    if not catalog_tools_root.exists() or not catalog_tools_root.is_dir():
        raise RuntimeError(f"Invalid catalog tools root: {catalog_tools_root}")
    out: list[tuple[str, Path]] = []
    for tool_dir in sorted(
        path for path in catalog_tools_root.iterdir() if path.is_dir()
    ):
        if (tool_dir / "toolspec.json").exists():
            out.append((tool_dir.name, tool_dir))
    if not out:
        raise RuntimeError(f"No toolspec.json found under: {catalog_tools_root}")
    return out


def select_tools(
    discovered: list[tuple[str, Path]], filters: list[str]
) -> list[tuple[str, Path]]:
    by_id = {tool_id: path for tool_id, path in discovered}
    if not filters:
        return discovered
    unknown = sorted(tool_id for tool_id in filters if tool_id not in by_id)
    if unknown:
        raise RuntimeError(f"Unknown tool id(s): {unknown}")
    return [(tool_id, by_id[tool_id]) for tool_id in filters]


def profile_filter_keys(profile_filters: Sequence[str]) -> set[str]:
    return {token.strip() for token in profile_filters if token.strip()}


def profile_matches_filter(
    *,
    tool_id: str,
    profile: BenchmarkProfile,
    filters: set[str],
) -> bool:
    if not filters:
        return True
    return profile.profile_id in filters or f"{tool_id}:{profile.profile_id}" in filters


def resolve_tool_targets(
    *,
    selected_tools: list[tuple[str, Path]],
    catalog_tools_root: Path,
    param_overrides_dir: Path,
    cost_profiles_dir: Path,
    default_group_count: int,
    default_prior_density: float,
    default_optional_inputs: set[str] | None,
    profile_filters: Sequence[str],
) -> list[ToolBenchmarkTarget]:
    filters = profile_filter_keys(profile_filters)
    matched_filters: set[str] = set()
    targets: list[ToolBenchmarkTarget] = []

    for tool_id, catalog_tool_dir in selected_tools:
        toolspec = load_json_object(
            catalog_tool_dir / "toolspec.json",
            f"toolspec[{tool_id}]",
        )
        resolved_profiles = resolve_benchmark_profiles(
            tool_id=tool_id,
            catalog_tools_root=catalog_tools_root,
            param_overrides_dir=param_overrides_dir,
            cost_profiles_dir=cost_profiles_dir,
            default_group_count=default_group_count,
            default_prior_density=default_prior_density,
            default_optional_inputs=default_optional_inputs,
        )
        selected_profiles: list[BenchmarkProfile] = []
        for profile in resolved_profiles:
            qualified_id = f"{tool_id}:{profile.profile_id}"
            if profile_matches_filter(
                tool_id=tool_id,
                profile=profile,
                filters=filters,
            ):
                selected_profiles.append(profile)
                if profile.profile_id in filters:
                    matched_filters.add(profile.profile_id)
                if qualified_id in filters:
                    matched_filters.add(qualified_id)

        if selected_profiles:
            targets.append(
                ToolBenchmarkTarget(
                    tool_id=tool_id,
                    catalog_tool_dir=catalog_tool_dir,
                    toolspec=toolspec,
                    profiles=tuple(selected_profiles),
                )
            )
        elif filters:
            print(f"[{tool_id}] no benchmark profiles matched --profile filters")

    unknown_filters = sorted(filters.difference(matched_filters))
    if unknown_filters:
        raise RuntimeError(
            f"Unknown benchmark profile filter(s) for selected tools: {unknown_filters}"
        )
    if not targets:
        raise RuntimeError("No benchmark profiles selected.")
    return targets


def docker_image_tag(tool_id: str) -> str:
    return f"inference-tools-{tool_id}:benchmark-local"


def safe_path_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def run_cmd(
    cmd: Sequence[str],
    *,
    cwd: Path,
    timeout_s: int,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd),
        text=True,
        check=False,
        capture_output=capture_output,
        timeout=None if timeout_s <= 0 else timeout_s,
    )


def build_image(
    tool_id: str,
    *,
    catalog_tools_root: Path,
    tool_sources_root: Path,
    image_tag: str,
) -> None:
    print(f"[{tool_id}] building image {image_tag}")
    build_script = INFERENCE_TOOLS_ROOT / "scripts" / "build_tool_images.py"
    result = run_cmd(
        [
            sys.executable,
            str(build_script),
            "--catalog-tools-root",
            str(catalog_tools_root),
            "--tool-sources-root",
            str(tool_sources_root),
            "--tool",
            tool_id,
            "--image-tag",
            f"{tool_id}={image_tag}",
        ],
        cwd=REPO_ROOT,
        timeout_s=0,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Docker build failed for {tool_id} (exit {result.returncode}).\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )


def prepare_io_dir(
    io_dir: Path,
    size: SizePoint,
    *,
    resolved_params: dict[str, Any],
    execution: dict[str, Any],
    seed: int,
    input_profile: dict[str, Any],
) -> None:
    write_benchmark_io_dir(
        io_dir,
        BenchmarkInputSize(genes=size.genes, columns=size.columns),
        BenchmarkInputProfile.from_cost_input_profile(input_profile, seed=seed),
    )
    save_json(io_dir / "params.json", resolved_params)
    save_json(io_dir / "execution.json", execution)


def run_container_once(
    *,
    image_tag: str,
    io_dir: Path,
    threads: int,
    ram_gb: int,
    timeout_s: int,
) -> tuple[str, float, str]:
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
        f"{io_dir}:/io",
        image_tag,
        "--input",
        "/io/expression.tsv",
        "--params",
        "/io/params.json",
        "--extra",
        "/io/extra",
        "--output-dir",
        "/io/out",
        "--threads",
        str(threads),
    ]

    started = time.perf_counter()
    try:
        result = run_cmd(cmd, cwd=REPO_ROOT, timeout_s=timeout_s, capture_output=True)
        elapsed = time.perf_counter() - started
        logs = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        if result.returncode != 0:
            return (classify_failure(logs), elapsed, logs.strip())
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - started
        return ("timeout", elapsed, f"Run exceeded timeout of {timeout_s} seconds.")

    network_path = io_dir / "out" / "network.csv"
    deadline = time.perf_counter() + 2.0
    while time.perf_counter() < deadline:
        if network_path.exists() and network_path.stat().st_size > 0:
            break
        time.sleep(0.1)

    if not network_path.exists() or network_path.stat().st_size <= 0:
        return (
            "error",
            elapsed,
            "Container finished but /io/out/network.csv is missing or empty.",
        )
    return ("ok", elapsed, "")


def classify_failure(logs: str) -> str:
    text = logs.lower()
    oom_markers = [
        "oom",
        "out of memory",
        "cannot allocate memory",
        "oomkilled",
        "killed process",
    ]
    if any(marker in text for marker in oom_markers):
        return "oom"
    return "error"


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("Cannot compute percentile of empty sequence.")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]

    position = (q / 100.0) * (len(ordered) - 1)
    lower_idx = int(math.floor(position))
    upper_idx = int(math.ceil(position))
    if lower_idx == upper_idx:
        return ordered[lower_idx]

    weight = position - lower_idx
    return ordered[lower_idx] * (1.0 - weight) + ordered[upper_idx] * weight


def aggregate_runtime_points(
    all_runs: list[dict[str, Any]],
    *,
    execution_profile: dict[str, Any],
    input_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for run in all_runs:
        key = (
            int(run["genes"]),
            int(run["columns"]),
            int(run["threads"]),
            int(run["ram_gb"]),
        )
        grouped[key].append(run)

    points: list[dict[str, Any]] = []
    for (genes, columns, threads, ram_gb), runs in sorted(grouped.items()):
        total = len(runs)
        ok_runs = [r for r in runs if r["status"] == "ok"]
        ok_count = len(ok_runs)
        ok_secs = [float(r["seconds"]) for r in ok_runs]
        failed_count = total - ok_count

        run_status_counts: dict[str, int] = defaultdict(int)
        for run in runs:
            run_status_counts[str(run.get("status", "error"))] += 1

        # Keep a minimal, fixed failure taxonomy for planner use.
        oom_failures = int(run_status_counts.get("oom", 0))
        timeout_failures = int(run_status_counts.get("timeout", 0))
        error_failures = int(run_status_counts.get("error", 0))
        unknown_failures = int(
            sum(
                count
                for status, count in run_status_counts.items()
                if status not in {"ok", "oom", "timeout", "error"}
            )
        )
        error_failures += unknown_failures

        failure_breakdown = {
            "oom": oom_failures,
            "timeout": timeout_failures,
            "error": error_failures,
        }

        if ok_count == total:
            status = "ok"
        elif ok_count > 0:
            status = "partial"
        else:
            if oom_failures > 0:
                status = "oom"
            elif timeout_failures > 0:
                status = "timeout"
            else:
                status = "error"

        point = {
            "genes": genes,
            "columns": columns,
            "threads": threads,
            "ram_gb": ram_gb,
            "status": status,
            "repeats_total": total,
            "repeats_ok": ok_count,
            "repeats_failed": failed_count,
            "ok_rate": round((ok_count / total), 6),
            "failure_breakdown": failure_breakdown,
            "seconds_p50": None,
            "seconds_p90": None,
            "feature_vector": build_feature_vector(
                execution_profile=execution_profile,
                input_profile=input_profile,
                genes=genes,
                columns=columns,
                threads=threads,
                ram_gb=ram_gb,
            ),
        }

        if ok_secs:
            point["seconds_p50"] = round(percentile(ok_secs, 50), 6)
            point["seconds_p90"] = round(percentile(ok_secs, 90), 6)

        points.append(point)
    return points


def build_feature_vector(
    *,
    execution_profile: dict[str, Any],
    input_profile: dict[str, Any],
    genes: int,
    columns: int,
    threads: int,
    ram_gb: int,
) -> dict[str, Any]:
    mode = str(execution_profile.get("mode") or "")
    group_count = int(execution_profile.get("group_count") or 0)
    n_cells = int(columns) if input_profile.get("column_kind") == "cells" else 0
    if mode == "cell_native":
        expected_contexts = max(1, n_cells)
    elif mode in {"group_native", "group_emulated", "group_aggregated"}:
        expected_contexts = max(1, group_count)
    else:
        expected_contexts = 1
    aggregation_step = str(
        execution_profile.get("aggregation_step")
        or input_profile.get("aggregation_step")
        or ("cell_to_group" if mode == "group_aggregated" else "none")
    )
    return {
        "execution_mode": mode,
        "n_cells": n_cells,
        "n_genes": int(genes),
        "n_groups": group_count,
        "expected_contexts": int(expected_contexts),
        "expected_dense_edges": int(max(0, n_cells) * max(0, genes) * max(0, genes - 1)),
        "has_tf_list": "tf_list" in set(input_profile.get("extras_provided", [])),
        "has_chromatin_accessibility_matrix": (
            "chromatin_accessibility_matrix"
            in set(input_profile.get("extras_provided", []))
        ),
        "output_density_class": str(
            input_profile.get("output_density_class")
            or ("dense" if mode in {"cell_native", "group_aggregated"} else "sparse")
        ),
        "aggregation_step": aggregation_step,
        "threads": int(threads),
        "ram_gb": float(ram_gb),
    }


def write_tool_cost_profile(
    *,
    cost_path: Path,
    cost_payload: dict[str, Any],
) -> None:
    save_json(cost_path, cost_payload)


def validate_runtime_args(args: argparse.Namespace) -> None:
    """Validate global CLI limits before running any benchmark."""
    if args.repeats < 1:
        raise RuntimeError("--repeats must be >= 1.")
    if args.group_count < 1:
        raise RuntimeError("--group-count must be >= 1.")
    if args.prior_density < 0 or args.prior_density > 1:
        raise RuntimeError("--prior-density must be between 0 and 1.")
    if args.max_cpu < 1:
        raise RuntimeError("--max-cpu must be >= 1.")
    if args.max_ram_gb < 1:
        raise RuntimeError("--max-ram-gb must be >= 1.")


def resolve_benchmark_dimensions(
    args: argparse.Namespace,
) -> tuple[list[SizePoint], list[int], list[int], int, float]:
    """Resolve and cap benchmark sizes/resources against host limits."""
    sizes_raw = args.size or list(DEFAULT_SIZES)
    sizes = [parse_size(token) for token in sizes_raw]
    threads_requested = parse_int_csv(args.threads, "threads")
    ram_requested = parse_int_csv(args.ram_gb, "ram-gb")

    host_cores = os.cpu_count() or 1
    host_ram_gb = detect_host_ram_gb()
    effective_cores = min(host_cores, args.max_cpu)
    effective_ram_gb = min(host_ram_gb, float(args.max_ram_gb))

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

    return sizes, threads, ram_gb, int(effective_cores), float(effective_ram_gb)


def _threading_config(toolspec: dict[str, Any]) -> dict[str, Any]:
    runtime_resources = toolspec.get("runtime_resources")
    if not isinstance(runtime_resources, dict):
        raise RuntimeError("toolspec.runtime_resources.threading is required.")
    threading = runtime_resources.get("threading")
    if not isinstance(threading, dict):
        raise RuntimeError("toolspec.runtime_resources.threading is required.")
    supported = threading.get("supported")
    default_threads = threading.get("default_threads")
    max_threads = threading.get("max_threads")
    upstream_mapping = str(threading.get("upstream_mapping", "")).strip()
    if (
        not isinstance(supported, bool)
        or isinstance(default_threads, bool)
        or not isinstance(default_threads, int)
        or isinstance(max_threads, bool)
        or not isinstance(max_threads, int)
        or default_threads < 1
        or max_threads < 1
        or default_threads > max_threads
        or not upstream_mapping
    ):
        raise RuntimeError("toolspec.runtime_resources.threading is invalid.")
    if not supported and (default_threads != 1 or max_threads != 1):
        raise RuntimeError(
            "toolspec.runtime_resources.threading with supported=false must use one thread."
        )
    return threading


def filter_threads_for_tool(
    *, tool_id: str, toolspec: dict[str, Any], requested_threads: list[int]
) -> list[int]:
    threading = _threading_config(toolspec)
    supported = bool(threading["supported"])
    max_threads = int(threading["max_threads"])
    if supported:
        allowed = [value for value in requested_threads if 1 <= value <= max_threads]
    else:
        allowed = [value for value in requested_threads if value == 1]
    ignored = sorted(set(requested_threads).difference(allowed))
    if ignored:
        print(
            f"[{tool_id}] ignoring thread value(s) incompatible with "
            f"toolspec.runtime_resources.threading: {ignored}"
        )
    if not allowed:
        raise RuntimeError(
            f"[{tool_id}] no requested thread values are compatible with "
            "toolspec.runtime_resources.threading."
        )
    return allowed


def build_benchmark_config(
    *,
    sizes: list[SizePoint],
    threads: list[int],
    ram_gb: list[int],
    params_profile: dict[str, Any],
    execution_profile: dict[str, Any],
    input_profile: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Build the benchmark_config block stored in cost.json."""
    return {
        "sizes": [{"genes": s.genes, "columns": s.columns} for s in sizes],
        "threads_tested": [int(t) for t in threads],
        "ram_gb_tested": [float(r) for r in ram_gb],
        "repeats": int(args.repeats),
        "timeout_seconds": int(args.timeout),
        "execution_profile": execution_profile,
        "input_profile": input_profile,
        "params_profile": params_profile,
    }


def iter_run_plan(
    *, sizes: list[SizePoint], threads: list[int], ram_gb: list[int], repeats: int
) -> Iterator[RunPlanItem]:
    """Yield the cartesian run plan in a deterministic order."""
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


def allocate_tool_workdir(
    tool_id: str, keep_workdir: bool
) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    """Create a per-tool working directory (ephemeral by default)."""
    if keep_workdir:
        preserved = tempfile.mkdtemp(prefix=f"benchmark_{tool_id}_")
        print(f"[{tool_id}] keeping benchmark workdir: {preserved}")
        return Path(preserved), None
    context = tempfile.TemporaryDirectory(prefix=f"benchmark_{tool_id}_")
    return Path(context.name), context


def summarize_tool_runs(tool_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate run-level status counters for tool-level reporting."""
    successful = [r for r in tool_runs if r["status"] == "ok"]
    failed = [r for r in tool_runs if r["status"] != "ok"]
    status_counts: dict[str, int] = defaultdict(int)
    for run_item in tool_runs:
        status_counts[str(run_item["status"])] += 1

    return {
        "total_runs": len(tool_runs),
        "successful_runs": len(successful),
        "failed_runs": len(failed),
        "status_counts": dict(sorted(status_counts.items())),
    }


def make_cost_profile_entry(
    *,
    profile_id: str,
    runtime_points: list[dict[str, Any]],
    benchmark_config: dict[str, Any],
) -> dict[str, Any]:
    """Build one profile entry for cost.json."""
    return {
        "profile_id": profile_id,
        "benchmark_config": benchmark_config,
        "runtime_points": runtime_points,
    }


def make_cost_payload(
    *,
    profile_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the final cost payload written into cost.json."""
    if not profile_entries:
        raise ValueError("Cannot build cost payload without profiles.")
    return {
        "schema_version": "1.0",
        "profiles": profile_entries,
    }


def execute_tool_benchmarks(
    *,
    tool_id: str,
    profile_id: str,
    image_tag: str,
    workdir: Path,
    sizes: list[SizePoint],
    threads: list[int],
    ram_gb: list[int],
    repeats: int,
    resolved_params: dict[str, Any],
    execution: dict[str, Any],
    input_profile: dict[str, Any],
    seed: int,
    timeout: int,
    fail_fast: bool,
    run_index: int,
    total_runs: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Execute all planned runs for one tool; returns runs, updated index, and fail-fast flag."""
    tool_runs: list[dict[str, Any]] = []

    for plan in iter_run_plan(
        sizes=sizes, threads=threads, ram_gb=ram_gb, repeats=repeats
    ):
        run_index += 1
        print(
            f"[{run_index}/{total_runs}] {tool_id}/{profile_id} "
            f"{plan.size.genes}x{plan.size.columns} "
            f"threads={plan.threads} ram={plan.ram_gb}GB repeat={plan.repeat}"
        )

        run_key = (
            f"{safe_path_token(profile_id)}_g{plan.size.genes}_c{plan.size.columns}_t{plan.threads}"
            f"_m{plan.ram_gb}_r{plan.repeat}"
        )
        io_dir = workdir / run_key / "io"
        prepare_io_dir(
            io_dir,
            plan.size,
            resolved_params=resolved_params,
            execution=execution,
            seed=seed,
            input_profile=input_profile,
        )

        status, elapsed, error_or_empty = run_container_once(
            image_tag=image_tag,
            io_dir=io_dir,
            threads=plan.threads,
            ram_gb=plan.ram_gb,
            timeout_s=timeout,
        )

        run_payload: dict[str, Any] = {
            "genes": plan.size.genes,
            "columns": plan.size.columns,
            "threads": plan.threads,
            "ram_gb": plan.ram_gb,
            "repeat": plan.repeat,
            "seconds": round(elapsed, 6),
            "status": status,
        }
        if error_or_empty:
            run_payload["error"] = error_or_empty
        tool_runs.append(run_payload)

        if status != "ok":
            print(f"  -> {status} ({elapsed:.3f}s)")
            if fail_fast:
                return tool_runs, run_index, True
        else:
            print(f"  -> ok ({elapsed:.3f}s)")

    return tool_runs, run_index, False


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    validate_runtime_args(args)
    sizes, threads, ram_gb, _effective_cores, _effective_ram_gb = (
        resolve_benchmark_dimensions(args)
    )

    discovered = discover_catalog_tools(args.catalog_tools_root)
    selected = select_tools(discovered, args.tool)
    targets = resolve_tool_targets(
        selected_tools=selected,
        catalog_tools_root=args.catalog_tools_root,
        param_overrides_dir=args.param_overrides_dir,
        cost_profiles_dir=args.cost_profiles_dir,
        default_group_count=args.group_count,
        default_prior_density=args.prior_density,
        default_optional_inputs=(
            set(args.optional_input) if args.optional_input else None
        ),
        profile_filters=args.profile,
    )

    threads_by_tool = {
        target.tool_id: filter_threads_for_tool(
            tool_id=target.tool_id,
            toolspec=target.toolspec,
            requested_threads=threads,
        )
        for target in targets
    }
    total_runs = sum(
        len(target.profiles)
        * len(sizes)
        * len(threads_by_tool[target.tool_id])
        * len(ram_gb)
        * args.repeats
        for target in targets
    )
    run_index = 0
    fail_fast_triggered = False
    global_success = 0
    global_fail = 0

    for target in targets:
        tool_id = target.tool_id
        tool_threads = threads_by_tool[tool_id]
        image_tag = docker_image_tag(tool_id)
        tool_source_dir = args.tool_sources_root / tool_id
        if not tool_source_dir.exists() or not tool_source_dir.is_dir():
            print(
                f"[{tool_id}] ERROR: missing tool source directory: {tool_source_dir}",
                file=sys.stderr,
            )
            if args.fail_fast:
                break
            continue

        cost_path = target.catalog_tool_dir / "cost.json"
        wrote_cost = False
        workdir_context: tempfile.TemporaryDirectory[str] | None = None

        try:
            if not args.skip_build:
                build_image(
                    tool_id,
                    catalog_tools_root=args.catalog_tools_root,
                    tool_sources_root=args.tool_sources_root,
                    image_tag=image_tag,
                )

            workdir, workdir_context = allocate_tool_workdir(
                tool_id=tool_id,
                keep_workdir=args.keep_workdir,
            )
            cost_profile_entries: list[dict[str, Any]] = []
            tool_total_runs = 0
            tool_success_runs = 0
            tool_failed_runs = 0
            for profile in target.profiles:
                print(f"[{tool_id}] profile: {profile.profile_id}")
                benchmark_config = build_benchmark_config(
                    sizes=sizes,
                    threads=tool_threads,
                    ram_gb=ram_gb,
                    params_profile=profile.params_profile,
                    execution_profile=profile.execution_profile,
                    input_profile=profile.input_profile,
                    args=args,
                )
                profile_workdir = workdir / safe_path_token(profile.profile_id)
                profile_runs, run_index, fail_fast_triggered = execute_tool_benchmarks(
                    tool_id=tool_id,
                    profile_id=profile.profile_id,
                    image_tag=image_tag,
                    workdir=profile_workdir,
                    sizes=sizes,
                    threads=tool_threads,
                    ram_gb=ram_gb,
                    repeats=args.repeats,
                    resolved_params=profile.params,
                    execution=profile.execution,
                    input_profile=profile.input_profile,
                    seed=args.seed,
                    timeout=args.timeout,
                    fail_fast=args.fail_fast,
                    run_index=run_index,
                    total_runs=total_runs,
                )

                profile_summary = summarize_tool_runs(profile_runs)
                tool_total_runs += profile_summary["total_runs"]
                tool_success_runs += profile_summary["successful_runs"]
                tool_failed_runs += profile_summary["failed_runs"]
                global_success += profile_summary["successful_runs"]
                global_fail += profile_summary["failed_runs"]

                runtime_points = aggregate_runtime_points(
                    profile_runs,
                    execution_profile=profile.execution_profile,
                    input_profile=profile.input_profile,
                )
                if runtime_points:
                    cost_profile_entries.append(
                        make_cost_profile_entry(
                            profile_id=profile.profile_id,
                            runtime_points=runtime_points,
                            benchmark_config=benchmark_config,
                        )
                    )

                print(
                    f"[{tool_id}/{profile.profile_id}] summary: "
                    f"total={profile_summary['total_runs']} "
                    f"ok={profile_summary['successful_runs']} "
                    f"failed={profile_summary['failed_runs']}"
                )
                if fail_fast_triggered:
                    break

            if cost_profile_entries and not args.no_write_cost:
                write_tool_cost_profile(
                    cost_path=cost_path,
                    cost_payload=make_cost_payload(
                        profile_entries=cost_profile_entries,
                    ),
                )
                wrote_cost = True
            elif not cost_profile_entries:
                print(f"[{tool_id}] warning: no profile runs were observed.")

            print(
                f"[{tool_id}] summary: profiles={len(cost_profile_entries)}/{len(target.profiles)} "
                f"total={tool_total_runs} ok={tool_success_runs} failed={tool_failed_runs} "
                f"wrote_cost={wrote_cost}"
            )

        except Exception as exc:  # noqa: BLE001
            print(f"[{tool_id}] ERROR: {exc}", file=sys.stderr)
            if args.fail_fast:
                break
        finally:
            if workdir_context is not None:
                workdir_context.cleanup()

        if fail_fast_triggered:
            break

    print()
    print(
        f"Benchmark finished. successful_runs={global_success} "
        f"failed_runs={global_fail}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
