"""Benchmark inference tools and write per-tool cost profiles (`cost.json`).

This script runs each tool wrapper in Docker for multiple input sizes and
resource configurations (threads + memory limit), measures runtime, and writes:
1) `andrea/catalog_inference_tools/tools/<tool_id>/cost.json` files.

Usage examples:
1) Benchmark all tools with defaults (up to host cap, max 8 CPU / 64 GB):
   python benchmark_costs.py

2) Benchmark selected tools:
   python benchmark_costs.py --tool genie3 --tool grnboost2 --results-dir cost-evidence

3) Customize matrix:
   python benchmark_costs.py \
     --size 50x40 --size 100x40 --size 200x40 \
     --size 100x20 --size 100x80 \
     --threads 1,2,4,8 \
     --ram-gb 8,16,32,64

4) Run selected cost profiles:
   python benchmark_costs.py \
     --tool genie3 \
     --profile global_tf_list \
     --profile global_default --merge-existing --results-dir cost-evidence

The default fallback matrix independently varies three gene counts and three
column counts. Tool-local recipes can constrain these to compatible ranges.
Existing successful points are reused only after scientific input/image identity
verification. --results-dir preserves partial repetitions across restarts;
--reuse-results-dir imports retained evidence, and --force-rerun explicitly
disables reuse for the requested points.

Exit codes:
- 0: all requested slots have terminal outcomes, or a reuse-only checkpoint completed
- 1: a control/verification failure or requested slots remain unattempted
- 2: usage/runtime error (invalid args, missing paths, etc.)

Cost model written to cost.json:
- schema_version: always "1.0".
- profiles: empirical runtime profiles keyed by execution/input/parameter profile.
- runtime_points: per profile, aggregated per (genes, columns, threads, ram_gb),
  including status/failure information, ok_rate, failure_breakdown, and p50/p90
  runtime estimates.

Cost profiles describe physical wrapper tasks only. Logical `group_emulated`
runs reuse `global` profiles because ANDREA creates one global child task per
group. Logical `group_aggregated` runs reuse `column_native` profiles and add a
deterministic aggregation task. The planner obtains total ETA from the complete
task graph; it does not apply an implicit grouped multiplier.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import signal
import threading
import subprocess
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from shared.benchmark_inputs import (
    BenchmarkInputProfile,
    BenchmarkInputSize,
    write_benchmark_io_dir,
    validate_temporal_fixture,
)
from shared.benchmark_profiles import (
    DEFAULT_COST_PROFILES_DIR,
    DEFAULT_GROUP_COUNT,
    DEFAULT_PRIOR_DENSITY,
    BenchmarkProfile,
    resolve_benchmark_profiles,
)
from shared.param_profiles import DEFAULT_PARAM_OVERRIDES_DIR
from shared.cost_resume import (
    VerifiedSourceKernels,
    complete_point,
    embedded_observations,
    find_evidence_profiles,
    input_manifest,
    retained_observations,
    measurement_context,
    point_key,
    read_json,
    successful_observation,
    terminal_observation,
)

from shared.cost_resources import (
    ResourcePool,
    UnsatisfiableResources,
    default_state_dir,
)

_BENCHMARK_ROOT = Path(__file__).resolve().parents[3]
if str(_BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCHMARK_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmark_support import (
    atomic_json,
    collect_provenance,
    build_context_fingerprint,
    measurement_kernel_fingerprint,
    verify_measurement_source,
    merge_profiles,
    persistent_workdir,
    write_process_logs,
    profile_fingerprint,
)

INFERENCE_TOOLS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_ROOT = REPO_ROOT / "andrea" / "catalog_inference_tools"
DEFAULT_CATALOG_TOOLS_ROOT = CATALOG_ROOT / "tools"
DEFAULT_TOOL_SOURCES_ROOT = INFERENCE_TOOLS_ROOT / "tools"
DEFAULT_SIZES = tuple(
    f"{genes}x{columns}" for genes in (50, 100, 200) for columns in (20, 40, 80)
)
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
        default=None,
        help="Optional shared CPU allocation cap; requests remain in the declared panel.",
    )
    parser.add_argument(
        "--max-ram-gb",
        type=int,
        default=None,
        help="Optional shared RAM allocation cap in GiB; requests remain in the declared panel.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Number of repeats per (size, threads, ram) run. Default: 3",
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
        "--plan-only",
        action="store_true",
        help="Resolve and print the benchmark matrix without building images or running containers.",
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
    parser.add_argument(
        "--catalog-images",
        action="store_true",
        help="Use the image referenced by each catalog spec without rebuilding; run its immutable local image ID.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        help="Persist inputs, outputs, stdout/stderr, raw measurements and provenance below this directory.",
    )
    parser.add_argument(
        "--merge-existing",
        action="store_true",
        help="Allow updating selected profiles while preserving unrelated profiles and compatible historical points.",
    )
    parser.add_argument(
        "--reuse-results-dir",
        type=Path,
        action="append",
        default=[],
        help="Also search this previous campaign directory for verified raw observations (repeatable).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Reuse compatible successful measurements and continue missing repetitions (default).",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="Explicitly rerun requested points; preserve unrelated profiles and historical evidence.",
    )
    parser.add_argument(
        "--remeasure-incompatible-history",
        action="store_true",
        help="Remeasure historical size/resource points whose evidence is incompatible with the current image or inputs; retain compatible observations.",
    )
    parser.add_argument(
        "--reuse-only",
        action="store_true",
        help="Verify/import retained observations and checkpoint cost without building or running Docker; pending slots are reported.",
    )
    parser.add_argument(
        "--reuse-source-root",
        type=Path,
        help="Archived measurement source used to prove legacy receipts lacking a kernel digest.",
    )
    parser.add_argument(
        "--reuse-source-manifest",
        type=Path,
        help="Original seal containing source_files_sha256 for --reuse-source-root.",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=0,
        help="Require this much free disk space before each new fit (default: 0).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Maximum independent fits in flight (default: 1; 0: automatic); actual admission uses detected CPU/RAM.",
    )
    parser.add_argument(
        "--resource-state-dir",
        type=Path,
        default=default_state_dir(),
        help="Shared resource reservation registry across benchmark campaigns.",
    )
    parser.add_argument("--ram-headroom-gb", type=float, default=1.0)
    parser.add_argument("--ram-headroom-fraction", type=float, default=0.1)
    parser.add_argument("--cpu-headroom", type=int, default=0)
    return parser.parse_args(argv)


def save_json(path: Path, payload: Any) -> None:
    atomic_json(path, payload)


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


def docker_container_name(
    *,
    image_tag: str,
    io_dir: Path,
    threads: int,
    ram_gb: int,
) -> str:
    raw = f"{image_tag}|{io_dir}|{threads}|{ram_gb}|{time.time_ns()}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    image_token = safe_path_token(image_tag.lower())[:40].strip("._-") or "tool"
    return f"andrea_tool_cost_{image_token}_{digest}"


def cleanup_timed_out_container(*, container_name: str, cidfile: Path) -> str:
    """Best-effort cleanup for docker run timeouts."""
    targets: list[str] = []
    if cidfile.exists():
        cid = cidfile.read_text(encoding="utf-8", errors="ignore").strip()
        if cid:
            targets.append(cid)
    targets.append(container_name)

    messages: list[str] = []
    seen: set[str] = set()
    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        result = run_cmd(
            ["docker", "rm", "-f", target],
            cwd=REPO_ROOT,
            timeout_s=5,
            capture_output=True,
        )
        if result.returncode == 0:
            messages.append(f"Cleaned timed-out container {target}.")
        else:
            err = (result.stderr or result.stdout or "").strip()
            if err:
                messages.append(f"Could not cleanup container {target}: {err}")
    return "\n".join(messages)


class BenchmarkInterrupted(RuntimeError):
    """Operator cancellation is control state, never a scientific observation."""

    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout, self.stderr = stdout, stderr


class UnsafeContainerCleanup(RuntimeError):
    """Container stop could not be verified; its reservation must remain held."""

    observation: dict[str, Any] | None = None


class WaitingForResources(RuntimeError):
    """Unattempted slots remain because their declared resource request cannot fit."""


def run_cmd(
    cmd: Sequence[str],
    *,
    cwd: Path,
    timeout_s: int,
    capture_output: bool = True,
    cancel_event: threading.Event | None = None,
) -> subprocess.CompletedProcess[str]:
    if cancel_event is None:
        return subprocess.run(
            list(cmd),
            cwd=str(cwd),
            text=True,
            check=False,
            capture_output=capture_output,
            timeout=None if timeout_s <= 0 else timeout_s,
        )
    if cancel_event.is_set():
        raise BenchmarkInterrupted("Benchmark cancelled before native execution.")
    process = subprocess.Popen(
        list(cmd),
        cwd=str(cwd),
        text=True,
        start_new_session=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )
    deadline = time.monotonic() + timeout_s if timeout_s > 0 else None
    while True:
        cancelled = cancel_event.is_set()
        expired = deadline is not None and time.monotonic() >= deadline
        if cancelled or expired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
            if cancelled:
                raise BenchmarkInterrupted(
                    "Benchmark cancelled during native execution.",
                    stdout or "",
                    stderr or "",
                )
            raise subprocess.TimeoutExpired(
                list(cmd), timeout_s, output=stdout, stderr=stderr
            )
        interval = (
            0.2
            if deadline is None
            else max(0.001, min(0.2, deadline - time.monotonic()))
        )
        try:
            stdout, stderr = process.communicate(timeout=interval)
            return subprocess.CompletedProcess(
                list(cmd), process.returncode, stdout, stderr
            )
        except subprocess.TimeoutExpired:
            continue


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
    runtime_execution = dict(execution)
    save_json(io_dir / "execution.json", runtime_execution)


def prepare_container_cidfile(cidfile: Path) -> None:
    """Remove a stale Docker receipt without disrupting a still-running task."""
    if not cidfile.exists():
        return
    container_id = cidfile.read_text(encoding="utf-8").strip()
    if container_id:
        result = run_cmd(
            [
                "docker",
                "inspect",
                "--type",
                "container",
                "--format",
                "{{.State.Running}}",
                container_id,
            ],
            cwd=REPO_ROOT,
            timeout_s=30,
            capture_output=True,
        )
        state = (result.stdout or "").strip().lower()
        if result.returncode == 0 and state == "true":
            raise RuntimeError(
                f"Refusing to restart benchmark: container {container_id} recorded in {cidfile} is still running."
            )
        missing = any(
            token in (result.stderr or "").lower()
            for token in ("no such container", "no such object")
        )
        if not (result.returncode == 0 and state == "false") and not (
            result.returncode != 0 and missing
        ):
            raise RuntimeError(
                f"Cannot verify whether container {container_id} is stopped; preserving {cidfile}. "
                f"Docker inspect: {(result.stderr or result.stdout or '').strip()}"
            )
    cidfile.unlink()


def run_container_once(
    *,
    image_tag: str,
    io_dir: Path,
    threads: int,
    ram_gb: int,
    timeout_s: int,
    cpuset_cpus: Sequence[int] | None = None,
    cancel_event: threading.Event | None = None,
    container_name: str | None = None,
) -> tuple[str, float, str]:
    uid_gid = f"{os.getuid()}:{os.getgid()}"
    resolved_io_dir = io_dir.resolve()
    resolved_out_dir = (io_dir / "out").resolve()
    container_name = container_name or docker_container_name(
        image_tag=image_tag,
        io_dir=io_dir,
        threads=threads,
        ram_gb=ram_gb,
    )
    cidfile = io_dir / ".docker-container.cid"
    prepare_container_cidfile(cidfile)
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--cidfile",
        str(cidfile),
        "--user",
        uid_gid,
        "--cpus",
        str(threads),
        "--memory",
        f"{ram_gb}g",
        "-v",
        f"{resolved_io_dir}:/io:ro",
        "-v",
        f"{resolved_out_dir}:/io/out:rw",
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

    if cpuset_cpus is not None:
        if (
            len(cpuset_cpus) != threads
            or len(set(cpuset_cpus)) != threads
            or any(
                isinstance(cpu, bool) or not isinstance(cpu, int) or cpu < 0
                for cpu in cpuset_cpus
            )
        ):
            raise ValueError(
                "CPU affinity must identify exactly the requested number of distinct logical CPUs."
            )
        cmd[2:2] = ["--cpuset-cpus", ",".join(str(cpu) for cpu in cpuset_cpus)]
    started = time.perf_counter()
    try:
        result = run_cmd(
            cmd,
            cwd=REPO_ROOT,
            timeout_s=timeout_s,
            capture_output=True,
            cancel_event=cancel_event,
        )
        elapsed = time.perf_counter() - started
        write_process_logs(io_dir.parent, result)
        logs = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        if result.returncode != 0:
            return (classify_failure(logs), elapsed, logs.strip())
    except BenchmarkInterrupted as exc:
        write_process_logs(io_dir.parent, exc)
        cleanup_timed_out_container(container_name=container_name, cidfile=cidfile)
        raise
    except subprocess.TimeoutExpired as exc:
        write_process_logs(io_dir.parent, exc)
        elapsed = time.perf_counter() - started
        cleanup = cleanup_timed_out_container(
            container_name=container_name,
            cidfile=cidfile,
        )
        message = f"Run exceeded timeout of {timeout_s} seconds."
        if cleanup:
            message = f"{message}\n{cleanup}"
        return ("timeout", elapsed, message)

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


def verify_owned_container_cleanup(*, container_name: str, cidfile: Path) -> None:
    """Confirm owned containers stopped/missing before releasing their lease.

    The generated name belongs to this worker. A preexisting receipt is only
    inspected; it never authorizes killing some other live container.
    """
    targets = [container_name]
    if cidfile.exists():
        recorded = cidfile.read_text(encoding="utf-8").strip()
        if recorded and recorded not in targets:
            targets.append(recorded)
    for target in targets:

        def inspect():
            return run_cmd(
                [
                    "docker",
                    "inspect",
                    "--type",
                    "container",
                    "--format",
                    "{{.State.Running}}",
                    target,
                ],
                cwd=REPO_ROOT,
                timeout_s=5,
                capture_output=True,
            )

        try:
            result = inspect()
            if (
                result.returncode == 0
                and (result.stdout or "").strip().lower() == "true"
                and target == container_name
            ):
                run_cmd(
                    ["docker", "rm", "-f", container_name],
                    cwd=REPO_ROOT,
                    timeout_s=5,
                    capture_output=True,
                )
                result = inspect()
            stopped = (
                result.returncode == 0
                and (result.stdout or "").strip().lower() == "false"
            )
            missing = result.returncode != 0 and any(
                token in (result.stderr or "").lower()
                for token in ("no such container", "no such object")
            )
            if not (stopped or missing):
                raise UnsafeContainerCleanup(
                    f"Cannot verify container {target} stopped; reservation retained. "
                    f"{(result.stderr or result.stdout or '').strip()}"
                )
        except UnsafeContainerCleanup:
            raise
        except Exception as exc:
            raise UnsafeContainerCleanup(
                f"Cannot verify container {target} stopped; reservation retained: {exc}"
            ) from exc


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
    if mode == "column_native":
        expected_contexts = max(1, n_cells)
    elif mode == "group_native":
        expected_contexts = max(1, group_count)
    else:
        expected_contexts = 1
    aggregation_step = str(
        execution_profile.get("aggregation_step")
        or input_profile.get("aggregation_step")
        or "none"
    )
    result = {
        "execution_mode": mode,
        "n_cells": n_cells,
        "n_genes": int(genes),
        "n_groups": group_count,
        "expected_contexts": int(expected_contexts),
        "expected_dense_edges": int(
            max(0, n_cells) * max(0, genes) * max(0, genes - 1)
        ),
        "has_tf_list": "tf_list" in set(input_profile.get("extras_provided", [])),
        "output_density_class": str(
            input_profile.get("output_density_class")
            or ("dense" if mode == "column_native" else "sparse")
        ),
        "aggregation_step": aggregation_step,
        "threads": int(threads),
        "ram_gb": float(ram_gb),
    }
    geometry = input_profile.get("temporal_fixture")
    if isinstance(geometry, dict):
        series = int(geometry["series_count"])
        points = int(geometry["timepoints_per_series"])
        validate_temporal_fixture(input_profile.get("temporal"), geometry)
        steady = int(geometry.get("steady_state_samples", 0))
        kinds = input_profile.get("observation_types")
        if (
            not isinstance(kinds, list)
            or "time_series" not in kinds
            or (steady and "steady_state" not in kinds)
        ):
            raise ValueError(
                "Temporal cost features require only consumed observation types."
            )
        if int(columns) != series * points + steady:
            raise ValueError(
                "Temporal cost columns must match the unified TS+SS matrix."
            )
        result.update(
            {
                "n_timepoints_per_series": points,
                "n_independent_series": series,
                "n_steady_state_samples": int(geometry.get("steady_state_samples", 0)),
                "n_temporal_transitions": series * (points - 1),
                "temporal_sampling": geometry.get("sampling", "regular"),
            }
        )
    return result


def write_tool_cost_profile(
    *,
    cost_path: Path,
    cost_payload: dict[str, Any],
) -> None:
    save_json(cost_path, cost_payload)


def validate_runtime_args(args: argparse.Namespace) -> None:
    """Validate global CLI limits before running any benchmark."""
    if (
        args.profile
        and not args.merge_existing
        and not args.no_write_cost
        and not args.plan_only
    ):
        raise RuntimeError(
            "Filtered cost writes require --merge-existing; use --no-write-cost for experiments."
        )
    if args.merge_existing and not args.profile:
        raise RuntimeError("--merge-existing requires at least one --profile.")
    if bool(args.reuse_source_root) != bool(args.reuse_source_manifest):
        raise RuntimeError(
            "--reuse-source-root and --reuse-source-manifest must be supplied together."
        )
    if args.reuse_only and args.force_rerun:
        raise RuntimeError("--reuse-only cannot be combined with --force-rerun.")
    if not math.isfinite(args.min_free_gb) or args.min_free_gb < 0:
        raise RuntimeError("--min-free-gb must be finite and >= 0.")
    if args.timeout < 0:
        raise RuntimeError("--timeout must be >= 0.")
    if args.repeats < 1:
        raise RuntimeError("--repeats must be >= 1.")
    if args.group_count < 1:
        raise RuntimeError("--group-count must be >= 1.")
    if args.prior_density < 0 or args.prior_density > 1:
        raise RuntimeError("--prior-density must be between 0 and 1.")
    if args.concurrency < 0:
        raise RuntimeError("--concurrency must be >= 0 (0 selects automatic capacity).")
    if (
        args.cpu_headroom < 0
        or args.ram_headroom_gb < 0
        or not math.isfinite(args.ram_headroom_gb)
    ):
        raise RuntimeError("Resource headroom must be finite and nonnegative.")
    if (
        not math.isfinite(args.ram_headroom_fraction)
        or not 0 <= args.ram_headroom_fraction < 1
    ):
        raise RuntimeError("--ram-headroom-fraction must be >= 0 and < 1.")
    if args.max_cpu is not None and args.max_cpu < 1:
        raise RuntimeError("--max-cpu must be >= 1.")
    if args.max_ram_gb is not None and args.max_ram_gb < 1:
        raise RuntimeError("--max-ram-gb must be >= 1.")


def resolve_benchmark_dimensions(
    args: argparse.Namespace,
) -> tuple[list[SizePoint], list[int], list[int], int, float]:
    """Resolve the full declared panel independently of machine capacity.

    Capacity admission belongs to the resource pool. Neither host detection nor
    operational caps may silently remove a size/resource point from calibration.
    """
    sizes = [parse_size(token) for token in (args.size or list(DEFAULT_SIZES))]
    threads = parse_int_csv(args.threads, "threads")
    ram_gb = parse_int_csv(args.ram_gb, "ram-gb")
    return sizes, threads, ram_gb, os.cpu_count() or 1, detect_host_ram_gb()


def sizes_for_profile(
    *,
    profile: BenchmarkProfile,
    default_sizes: list[SizePoint],
    cli_sizes_explicit: bool,
) -> list[SizePoint]:
    """Resolve the size matrix for one profile.

    Explicit CLI --size values intentionally override profile-local size hints so
    a user can force a narrow debug run across any profile.
    """
    if cli_sizes_explicit or not profile.sizes:
        return list(default_sizes)
    return [parse_size(token) for token in profile.sizes]


def benchmark_plan_rows(
    *,
    targets: Sequence[ToolBenchmarkTarget],
    default_sizes: list[SizePoint],
    threads_by_tool: dict[str, list[int]],
    ram_gb: list[int],
    repeats: int,
    cli_sizes_explicit: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in targets:
        for profile in target.profiles:
            profile_sizes = sizes_for_profile(
                profile=profile,
                default_sizes=default_sizes,
                cli_sizes_explicit=cli_sizes_explicit,
            )
            run_count = (
                len(profile_sizes)
                * len(threads_by_tool[target.tool_id])
                * len(ram_gb)
                * repeats
            )
            rows.append(
                {
                    "tool_id": target.tool_id,
                    "profile_id": profile.profile_id,
                    "sizes": profile_sizes,
                    "threads": threads_by_tool[target.tool_id],
                    "ram_gb": ram_gb,
                    "runs": run_count,
                }
            )
    return rows


def print_benchmark_plan_summary(rows: Sequence[dict[str, Any]]) -> None:
    total_runs = sum(int(row["runs"]) for row in rows)
    tools = sorted({str(row["tool_id"]) for row in rows})
    print(
        f"Benchmark plan: tools={len(tools)} profiles={len(rows)} planned_runs={total_runs}"
    )
    for row in rows:
        size_tokens = [f"{size.genes}x{size.columns}" for size in row["sizes"]]
        print(
            f"- {row['tool_id']}/{row['profile_id']}: "
            f"sizes={','.join(size_tokens)} "
            f"threads={','.join(str(v) for v in row['threads'])} "
            f"ram_gb={','.join(str(v) for v in row['ram_gb'])} "
            f"runs={row['runs']}"
        )


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
        or default_threads < 1
        or (
            max_threads is not None
            and (
                isinstance(max_threads, bool)
                or not isinstance(max_threads, int)
                or max_threads < 1
                or default_threads > max_threads
            )
        )
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
    max_threads = threading["max_threads"]
    if supported:
        allowed = [
            value
            for value in requested_threads
            if value >= 1 and (max_threads is None or value <= max_threads)
        ]
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
    timed_out = [r for r in tool_runs if r["status"] == "timeout"]
    failed = [r for r in tool_runs if r["status"] not in {"ok", "timeout"}]
    status_counts: dict[str, int] = defaultdict(int)
    for run_item in tool_runs:
        status_counts[str(run_item["status"])] += 1

    return {
        "total_runs": len(tool_runs),
        "successful_runs": len(successful),
        "timeout_runs": len(timed_out),
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
    initial_runs: list[dict[str, Any]] | None = None,
    on_progress: Callable[[list[dict[str, Any]]], None] | None = None,
    point_plan: set[tuple[int, int, int, float]] | None = None,
    min_free_gb: float = 0,
    concurrency: int = 1,
    resource_pool: ResourcePool | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Admit bounded independent fits; only this coordinator writes receipts.

    Reservation and queue wait precede the existing native measurement timer.
    Workers own distinct IO directories. A lease is released only after its
    worker has completed or cancellation has cleaned its native process/container.
    """
    pool = (
        resource_pool
        if resource_pool is not None
        else ResourcePool(default_state_dir())
    )
    cancelled = cancel_event if cancel_event is not None else threading.Event()
    if concurrency == 0:
        concurrency = max(1, int(pool.snapshot()["cpu_budget"]))
    tool_runs = list(initial_runs or [])
    recovered = {
        (*point_key(row), row["repeat"])
        for row in tool_runs
        if terminal_observation(row)
    }
    workdir.mkdir(parents=True, exist_ok=True)
    if tool_runs:
        save_json(workdir / "measurements.json", tool_runs)
    plans = (
        list(
            iter_run_plan(sizes=sizes, threads=threads, ram_gb=ram_gb, repeats=repeats)
        )
        if point_plan is None
        else [
            RunPlanItem(SizePoint(g, c), t, m, repeat)
            for g, c, t, m in sorted(point_plan)
            for repeat in range(1, repeats + 1)
        ]
    )
    plans = list(dict.fromkeys(plans))
    pending = []
    for index, plan in enumerate(plans, start=1):
        if (
            *point_key(
                {
                    "genes": plan.size.genes,
                    "columns": plan.size.columns,
                    "threads": plan.threads,
                    "ram_gb": plan.ram_gb,
                }
            ),
            plan.repeat,
        ) not in recovered:
            pending.append((index, plan))
    run_index += len(plans) - len(pending)
    if recovered:
        print(
            f"[{tool_id}/{profile_id}] retained {len(plans) - len(pending)} verified terminal slots",
            flush=True,
        )

    def measure(plan: RunPlanItem, lease: Any) -> dict[str, Any]:
        worker_started = time.time()
        if cancelled.is_set():
            raise BenchmarkInterrupted("Benchmark cancelled before input preparation.")
        if shutil.disk_usage(workdir).free < min_free_gb * 1024**3:
            raise RuntimeError(
                f"Free disk space is below --min-free-gb={min_free_gb:g}; no new observation was created."
            )
        run_key = (
            f"{safe_path_token(profile_id)}_g{plan.size.genes}_c{plan.size.columns}_t{plan.threads}"
            f"_m{plan.ram_gb:g}_r{plan.repeat}"
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
        observed_inputs = input_manifest(io_dir)
        observed_run_id = uuid.uuid4().hex
        container_name = docker_container_name(
            image_tag=image_tag, io_dir=io_dir, threads=plan.threads, ram_gb=plan.ram_gb
        )
        row = None
        try:
            native_started = time.time()
            status, elapsed, error = run_container_once(
                image_tag=image_tag,
                io_dir=io_dir,
                threads=plan.threads,
                ram_gb=plan.ram_gb,
                timeout_s=timeout,
                cpuset_cpus=lease.cpus,
                cancel_event=cancelled,
                container_name=container_name,
            )
            native_finished = time.time()
            row = {
                "run_id": observed_run_id,
                "genes": plan.size.genes,
                "columns": plan.size.columns,
                "threads": plan.threads,
                "ram_gb": plan.ram_gb,
                "repeat": plan.repeat,
                "seconds": round(elapsed, 6),
                "status": status,
                "io_dir": str(io_dir.resolve()),
                "input_sha256": observed_inputs,
                "measurement_timeout_seconds": timeout,
                "resource_allocation": {
                    **lease.context,
                    "lease_id": lease.lease_id,
                    "cpus": list(lease.cpus),
                    "requested_threads": plan.threads,
                    "requested_ram_gb": plan.ram_gb,
                    "concurrency_limit": concurrency,
                    "worker_started_at_unix": worker_started,
                    "native_call_started_at_unix": native_started,
                    "native_call_finished_at_unix": native_finished,
                    "native_call_interval_scope": "Native function entry to return, including CID checks and post-command output/cleanup checks; seconds retains measured Docker wall time.",
                },
            }
            geometry = input_profile.get("temporal_fixture")
            if isinstance(geometry, dict):
                row["dimensions"] = {
                    "genes": plan.size.genes,
                    "columns": plan.size.columns,
                    "timepoints_per_series": geometry["timepoints_per_series"],
                    "independent_series": geometry["series_count"],
                    "steady_state_samples": geometry.get("steady_state_samples", 0),
                }
            if error:
                row["error"] = error
            return row
        finally:
            try:
                verify_owned_container_cleanup(
                    container_name=container_name,
                    cidfile=io_dir / ".docker-container.cid",
                )
            except UnsafeContainerCleanup as exc:
                exc.observation = row
                raise
            finally:
                if row is not None:
                    row["resource_allocation"]["worker_finished_at_unix"] = time.time()

    active: dict[Any, tuple[RunPlanItem, Any]] = {}
    blocked = []
    unsafe_reports = []
    errors: list[BaseException] = []
    stopped = False
    last_wait_report = 0.0
    with ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="cost-fit"
    ) as executor:
        while pending or active:
            if not cancelled.is_set() and not stopped and not errors:
                for index, plan in list(pending):
                    if len(active) >= concurrency:
                        break
                    try:
                        lease = pool.reserve(threads=plan.threads, ram_gb=plan.ram_gb)
                    except UnsatisfiableResources as exc:
                        blocked.append(
                            {
                                "genes": plan.size.genes,
                                "columns": plan.size.columns,
                                "threads": plan.threads,
                                "ram_gb": plan.ram_gb,
                                "repeat": plan.repeat,
                                "reason": str(exc),
                            }
                        )
                        pending.remove((index, plan))
                        continue
                    except Exception as exc:
                        errors.append(exc)
                        cancelled.set()
                        break
                    if lease is None:
                        continue
                    try:
                        future = executor.submit(measure, plan, lease)
                    except BaseException:
                        lease.release()
                        raise
                    active[future] = (plan, lease)
                    pending.remove((index, plan))
                    run_index += 1
                    print(
                        f"[{index}/{len(plans)}] {tool_id}/{profile_id} {plan.size.genes}x{plan.size.columns} "
                        f"threads={plan.threads} ram={plan.ram_gb}GB repeat={plan.repeat} "
                        f"cpus={','.join(map(str, lease.cpus))}",
                        flush=True,
                    )
            if not active:
                if cancelled.is_set() or stopped or errors or not pending:
                    break
                if time.monotonic() - last_wait_report >= 60:
                    print(
                        f"[{tool_id}/{profile_id}] waiting_resources: {len(pending)} unattempted slots; "
                        "fixed CPU/RAM requests remain unchanged",
                        flush=True,
                    )
                    save_json(workdir / "resource-wait.json", pool.snapshot())
                    last_wait_report = time.monotonic()
                cancelled.wait(0.25)
                continue
            done, _ = wait(active, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                plan, lease = active.pop(future)
                unsafe = False
                try:
                    row = future.result()
                    tool_runs.append(row)
                    tool_runs.sort(
                        key=lambda value: (*point_key(value), value["repeat"])
                    )
                    save_json(workdir / "measurements.json", tool_runs)
                    if on_progress is not None:
                        on_progress(tool_runs)
                    print(
                        f"  -> {row['status']} ({row['seconds']:.3f}s) repeat={plan.repeat} "
                        f"active={len(active)} pending={len(pending)}",
                        flush=True,
                    )
                    if row["status"] != "ok" and fail_fast:
                        stopped = True
                except UnsafeContainerCleanup as exc:
                    unsafe = True
                    errors.append(exc)
                    cancelled.set()
                    unsafe_reports.append(
                        {
                            "lease_id": lease.lease_id,
                            "resource_allocation": lease.context,
                            "error": str(exc),
                            "reservation_retained": True,
                        }
                    )
                    try:
                        if exc.observation is not None:
                            tool_runs.append(exc.observation)
                            tool_runs.sort(
                                key=lambda value: (*point_key(value), value["repeat"])
                            )
                            save_json(workdir / "measurements.json", tool_runs)
                            if on_progress is not None:
                                on_progress(tool_runs)
                        save_json(
                            workdir / "unsafe-cleanup.json",
                            {
                                "entries": unsafe_reports,
                                "reservation_retained": True,
                            },
                        )
                    except BaseException as write_error:
                        errors.append(write_error)
                except BenchmarkInterrupted:
                    cancelled.set()
                except BaseException as exc:
                    errors.append(exc)
                    cancelled.set()
                finally:
                    if not unsafe:
                        try:
                            lease.release()
                        except Exception as exc:
                            errors.append(exc)
                            cancelled.set()
    if blocked:
        save_json(
            workdir / "resource-blocked.json",
            {"slots": blocked, "resource_snapshot": pool.snapshot()},
        )
    if errors:
        raise errors[0]
    if cancelled.is_set():
        raise BenchmarkInterrupted(
            "Benchmark interrupted; completed receipts preserved, unattempted slots remain pending."
        )
    if blocked:
        raise WaitingForResources(
            f"{len(blocked)} fixed resource slots remain pending; no native observations were fabricated."
        )
    return tool_runs, run_index, stopped


def prepare_incremental_measurements(
    *,
    profile: BenchmarkProfile,
    config: dict[str, Any],
    existing: dict[str, Any] | None,
    evidence_roots: list[Path],
    workdir: Path,
    sizes: list[SizePoint],
    threads: list[int],
    ram_gb: list[int],
    repeats: int,
    seed: int,
    force_rerun: bool,
    remeasure_historical: bool = False,
    source_kernels: dict[str, str] | None = None,
) -> tuple[
    list[dict[str, Any]],
    dict[tuple[int, int, int, float], dict[str, str]],
    set[tuple[int, int, int, float]],
]:
    """Resolve evidence and the execution matrix without executing Docker.

    Historical aggregates are never expanded into invented raw timings. They are
    reusable only when their original observations and input identity survive.
    An unfinished planned point is retained in the profile plan, but does not
    prevent a later invocation from measuring a different requested resource.
    """
    requested = {
        (p.size.genes, p.size.columns, p.threads, float(p.ram_gb))
        for p in iter_run_plan(sizes=sizes, threads=threads, ram_gb=ram_gb, repeats=1)
    }
    old_points = {point_key(p): p for p in (existing or {}).get("runtime_points", [])}
    intended = requested | set(old_points)
    old_config = (existing or {}).get("benchmark_config", {})
    # A partial checkpoint can contain historical points still awaiting their
    # new-image measurement. Keep that frozen plan across interruptions, while
    # allowing genuinely superseded recipes to retire incompatible dimensions.
    if (
        config.get("profile_sha256")
        and old_config.get("profile_sha256") == config["profile_sha256"]
    ):
        intended.update(point_key(p) for p in old_config.get("planned_points", []))
    directories = find_evidence_profiles(
        evidence_roots, profile.tool_id, profile.profile_id
    )
    initial: list[dict[str, Any]] = []
    hashes_by_key: dict[tuple[int, int, int, float], dict[str, str]] = {}
    manifests: dict[tuple[int, int], dict[str, str]] = {}
    blocked: list[tuple[int, int, int, float]] = []
    deferred: set[tuple[int, int, int, float]] = set()
    for key in sorted(intended):
        shape = key[:2]
        if shape not in manifests:
            io_dir = workdir / "input-verification" / f"g{key[0]}_c{key[1]}" / "io"
            prepare_io_dir(
                io_dir,
                SizePoint(*shape),
                resolved_params=profile.params,
                execution=profile.execution,
                seed=seed,
                input_profile=profile.input_profile,
            )
            manifests[shape] = input_manifest(io_dir)
        hashes = manifests[shape]
        hashes_by_key[key] = hashes
        if force_rerun and key in requested:
            continue
        old_point = old_points.get(key, {})
        rows = embedded_observations(
            point=old_point,
            config=config,
            hashes=hashes,
            repeats=repeats,
            source_kernels=source_kernels,
        )
        if len(rows) < repeats:
            recovered = retained_observations(
                directories=directories,
                config=config,
                key=key,
                hashes=hashes,
                repeats=repeats,
                source_kernels=source_kernels,
            )
            by_repeat = {row["repeat"]: row for row in rows}
            for row in recovered:
                by_repeat.setdefault(row["repeat"], row)
            rows = [by_repeat[k] for k in sorted(by_repeat)]
        if old_point and len(rows) == old_point.get("repeats_total"):
            recomputed = aggregate_runtime_points(
                rows,
                execution_profile=profile.execution_profile,
                input_profile=profile.input_profile,
            )[0]
            if any(
                recomputed[field] != old_point[field]
                for field in (
                    "seconds_p50",
                    "seconds_p90",
                    "status",
                    "repeats_total",
                    "repeats_ok",
                    "repeats_failed",
                    "failure_breakdown",
                )
            ):
                # A changed/corrupted aggregate cannot authorize skipping work.
                rows = []
        if key not in requested and len(rows) < repeats:
            if old_point and len(rows) < old_point.get("repeats_total", repeats):
                # Published observations cannot disappear when another resource
                # is requested. A verified partial checkpoint may remain pending.
                blocked.append(key)
            elif not remeasure_historical:
                deferred.add(key)
        initial.extend(rows)
    if blocked and not remeasure_historical:
        raise RuntimeError(
            f"Cannot safely retain {len(blocked)} historical points outside the requested matrix: "
            "their matching raw input/measurement evidence is missing or incompatible. "
            "Supply --reuse-results-dir or explicitly include those points for remeasurement. "
            f"First points: {blocked[:3]}"
        )
    config["planned_points"] = [
        {"genes": g, "columns": c, "threads": t, "ram_gb": m}
        for g, c, t, m in sorted(intended)
    ]
    config["sizes"] = [
        {"genes": g, "columns": c} for g, c in sorted({k[:2] for k in intended})
    ]
    config["threads_tested"] = sorted({k[2] for k in intended})
    config["ram_gb_tested"] = sorted({k[3] for k in intended})
    requested_reused = sum(point_key(row) in requested for row in initial)
    report = {
        "profile_id": profile.profile_id,
        "requested_points": len(requested),
        "historical_points_retained": len(intended - requested),
        "requested_repetitions": len(requested) * repeats,
        "planned_repetitions": len(intended) * repeats,
        "verified_terminal_repetitions": len(initial),
        "successful_repetitions": sum(row["status"] == "ok" for row in initial),
        "timeout_repetitions": sum(row["status"] == "timeout" for row in initial),
        "oom_repetitions": sum(row["status"] == "oom" for row in initial),
        "error_repetitions": sum(row["status"] == "error" for row in initial),
        "reused_requested_repetitions": requested_reused,
        "pending_repetitions": len(requested) * repeats - requested_reused,
        "historical_points_requiring_remeasurement": [
            dict(zip(("genes", "columns", "threads", "ram_gb"), key)) for key in blocked
        ],
        "deferred_planned_points": [
            dict(zip(("genes", "columns", "threads", "ram_gb"), key))
            for key in sorted(deferred)
        ],
        "total_pending_repetitions": len(intended) * repeats - len(initial),
        "evidence_profiles_checked": [str(p) for p in directories],
    }
    save_json(workdir / "reuse-report.json", report)
    print(
        f"[{profile.tool_id}/{profile.profile_id}] reuse: "
        f"{requested_reused}/{len(requested) * repeats} requested repetitions; "
        f"{len(intended - requested)} historical resource points retained"
    )
    return initial, hashes_by_key, intended - deferred


def completed_incremental_points(
    runs: list[dict[str, Any]],
    *,
    profile: BenchmarkProfile,
    config: dict[str, Any],
    hashes: dict[tuple[int, int, int, float], dict[str, str]],
    repeats: int,
) -> list[dict[str, Any]]:
    points = aggregate_runtime_points(
        runs,
        execution_profile=profile.execution_profile,
        input_profile=profile.input_profile,
    )
    completed = []
    for point in points:
        key = point_key(point)
        observations = [
            {
                **row,
                "measurement_provenance": row.get(
                    "measurement_provenance", config.get("provenance", {})
                ),
            }
            for row in runs
            if point_key(row) == key
        ]
        contexts = {}
        for row in observations:
            provenance = row["measurement_provenance"]
            context = measurement_context(
                {
                    **config,
                    "provenance": {
                        **provenance,
                        "measurement_kernel_sha256": row.get(
                            "measurement_kernel_sha256",
                            provenance.get("measurement_kernel_sha256"),
                        ),
                    },
                }
            )
            contexts[json.dumps(context, sort_keys=True)] = context
        common_context = dict(next(iter(contexts.values())))
        for context in contexts.values():
            common_context = {
                name: value
                for name, value in common_context.items()
                if name in context and context[name] == value
            }
        point["measurement_evidence"] = {
            "version": 1,
            "input_sha256": hashes[key],
            "measurement_context": common_context,
            "measurement_contexts": list(contexts.values()),
            "observations": observations,
        }
        completed.append(point)
    return completed


def acquire_cost_lock(cost_path: Path):
    """Prevent simultaneous writers even when they use different evidence roots."""
    directory = Path(tempfile.gettempdir()) / "andrea-inference-cost-locks"
    directory.mkdir(exist_ok=True)
    key = hashlib.sha256(str(cost_path.resolve()).encode()).hexdigest()
    stream = (directory / f"{key}.lock").open("a")
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        stream.close()
        raise RuntimeError(
            f"Another benchmark is already updating {cost_path}"
        ) from exc
    return stream


def evidence_configs(
    target: ToolBenchmarkTarget, roots: list[Path], cost_path: Path
) -> list[dict[str, Any]]:
    configs = []
    for profile in target.profiles:
        for directory in find_evidence_profiles(
            roots, target.tool_id, profile.profile_id
        ):
            value = read_json(directory / "benchmark_config.json", {})
            if isinstance(value, dict):
                configs.append(value)
    payload = read_json(cost_path, {})
    configs.extend(
        p.get("benchmark_config", {})
        for p in payload.get("profiles", [])
        if isinstance(p, dict)
    )
    return configs


def verified_source_kernels(
    args: argparse.Namespace,
    target: ToolBenchmarkTarget,
    roots: list[Path],
    cost_path: Path,
) -> dict[str, str]:
    if not args.reuse_source_root:
        return {}
    manifest = load_json_object(
        args.reuse_source_manifest, "measurement source manifest"
    )
    files = manifest.get("source_files_sha256")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("Measurement source manifest requires source_files_sha256.")
    provenances = [
        config.get("provenance", {})
        for config in evidence_configs(target, roots, cost_path)
    ]
    # Reconciled configs carry the proven kernel, but every imported row still
    # carries its unchanged original provenance. Discover those original source
    # identities too: the derived row's kernel annotation is not source proof.
    for profile in target.profiles:
        for directory in find_evidence_profiles(
            roots, target.tool_id, profile.profile_id
        ):
            rows = read_json(directory / "measurements.json", [])
            if isinstance(rows, list):
                provenances.extend(
                    row.get("measurement_provenance", {})
                    for row in rows
                    if isinstance(row, dict)
                )
    payload = read_json(cost_path, {})
    for entry in payload.get("profiles", []):
        if not isinstance(entry, dict):
            continue
        for point in entry.get("runtime_points", []):
            if not isinstance(point, dict):
                continue
            rows = point.get("measurement_evidence", {}).get("observations", [])
            if isinstance(rows, list):
                provenances.extend(
                    row.get("measurement_provenance", {})
                    for row in rows
                    if isinstance(row, dict)
                )
    sources = {
        provenance.get("source_sha256")
        for provenance in provenances
        if isinstance(provenance, dict)
        and not provenance.get("measurement_kernel_sha256")
    } - {None}
    current_kernel = measurement_kernel_fingerprint(REPO_ROOT)
    verified = VerifiedSourceKernels()
    for source in sources:
        kernel = verify_measurement_source(args.reuse_source_root, source, files)
        verified[source] = kernel
    verified.load_reviews(manifest, current_kernel)
    if any(
        kernel != current_kernel and (kernel, current_kernel) not in verified.reviews
        for kernel in verified.values()
    ):
        raise RuntimeError(
            "Retained measurement kernel differs from the current scientific timing/input kernel without an explicit verified executor review."
        )
    return verified


def retained_provenance(
    target: ToolBenchmarkTarget,
    source_dir: Path,
    roots: list[Path],
    cost_path: Path,
    seed: int,
    source_kernels: dict[str, str],
) -> dict[str, Any]:
    """Reconcile retained facts without claiming a fresh image/hardware inspection.

    Native execution always collects live provenance. Offline reconciliation
    verifies the current spec/build inputs and anchors each observation to its
    original hardware/image receipt, rather than invoking Docker to create one.
    """
    spec_hash = hashlib.sha256(
        (target.catalog_tool_dir / "toolspec.json").read_bytes()
    ).hexdigest()
    context_hash = build_context_fingerprint(
        root=REPO_ROOT,
        kind="inference",
        identifier=target.tool_id,
        source_dir=source_dir,
    )
    kernel = measurement_kernel_fingerprint(REPO_ROOT)
    candidates = evidence_configs(target, roots, cost_path)
    # Prefer exact current-kernel evidence. A reviewed old anchor remains old:
    # offline reconciliation must not claim a new native timing inspection.
    candidates.sort(
        key=lambda c: c.get("provenance", {}).get("measurement_kernel_sha256") != kernel
    )
    for config in candidates:
        provenance = config.get("provenance", {})
        original_kernel = provenance.get(
            "measurement_kernel_sha256"
        ) or source_kernels.get(provenance.get("source_sha256"))
        reviewed = (original_kernel, kernel) in getattr(source_kernels, "reviews", {})
        if (
            (original_kernel == kernel or reviewed)
            and provenance.get("seed") == seed
            and provenance.get("spec_sha256") == spec_hash
            and provenance.get("build_context_sha256") == context_hash
            and provenance.get("image_id")
            and provenance.get("cpu_model")
        ):
            return {**provenance, "measurement_kernel_sha256": original_kernel}
    if candidates:
        raise RuntimeError(
            "No retained provenance matches the current spec/build context, source proof and seed."
        )
    # No receipt can be accepted without an immutable image/hardware anchor.
    # This still permits a no-data reuse-only invocation to report pending slots.
    return {
        "measurement_kernel_sha256": kernel,
        "spec_sha256": spec_hash,
        "build_context_sha256": context_hash,
        "seed": seed,
    }


def _run(argv: Sequence[str] | None = None, *, cancel_event: threading.Event) -> int:
    args = parse_args(argv)
    validate_runtime_args(args)
    default_sizes, threads, ram_gb, _effective_cores, _effective_ram_gb = (
        resolve_benchmark_dimensions(args)
    )
    cli_sizes_explicit = bool(args.size)

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
    plan_rows = benchmark_plan_rows(
        targets=targets,
        default_sizes=default_sizes,
        threads_by_tool=threads_by_tool,
        ram_gb=ram_gb,
        repeats=args.repeats,
        cli_sizes_explicit=cli_sizes_explicit,
    )
    total_runs = sum(int(row["runs"]) for row in plan_rows)
    if args.plan_only:
        print_benchmark_plan_summary(plan_rows)
        return 0

    resource_pool = (
        None
        if args.reuse_only
        else ResourcePool(
            args.resource_state_dir,
            ram_headroom_gb=args.ram_headroom_gb,
            ram_headroom_fraction=args.ram_headroom_fraction,
            cpu_headroom=args.cpu_headroom,
            max_threads=args.max_cpu,
            max_ram_gb=args.max_ram_gb,
        )
    )
    run_index = 0
    waiting_resources = False
    fail_fast_triggered = False
    global_success = 0
    global_timeout = 0
    global_fail = 0
    had_error = False

    for target in targets:
        tool_id = target.tool_id
        tool_threads = threads_by_tool[tool_id]
        image_tag = (
            target.toolspec["docker_image"]
            if args.catalog_images
            else docker_image_tag(tool_id)
        )
        tool_source_dir = args.tool_sources_root / tool_id
        if not tool_source_dir.exists() or not tool_source_dir.is_dir():
            print(
                f"[{tool_id}] ERROR: missing tool source directory: {tool_source_dir}",
                file=sys.stderr,
            )
            had_error = True
            if args.fail_fast:
                break
            continue

        cost_path = target.catalog_tool_dir / "cost.json"
        wrote_cost = False
        unpersistable_profile = False
        workdir_context: tempfile.TemporaryDirectory[str] | None = None
        cost_lock = None

        try:
            cost_lock = acquire_cost_lock(cost_path)
            if not args.reuse_only and not args.skip_build and not args.catalog_images:
                build_image(
                    tool_id,
                    catalog_tools_root=args.catalog_tools_root,
                    tool_sources_root=args.tool_sources_root,
                    image_tag=image_tag,
                )

            evidence_roots = list(args.reuse_results_dir)
            if args.results_dir:
                evidence_roots.append(args.results_dir)
            source_kernels = verified_source_kernels(
                args, target, evidence_roots, cost_path
            )
            if args.reuse_only:
                provenance = retained_provenance(
                    target,
                    tool_source_dir,
                    evidence_roots,
                    cost_path,
                    args.seed,
                    source_kernels,
                )
            else:
                provenance = collect_provenance(
                    root=REPO_ROOT,
                    spec_path=target.catalog_tool_dir / "toolspec.json",
                    image=image_tag,
                    seed=args.seed,
                    source_dir=tool_source_dir,
                )
                provenance["measurement_kernel_sha256"] = (
                    measurement_kernel_fingerprint(REPO_ROOT)
                )
            image_tag = provenance.get("image_id", image_tag)
            if args.results_dir:
                workdir = persistent_workdir(args.results_dir, tool_id)
                print(f"[{tool_id}] evidence: {workdir}")
            else:
                workdir, workdir_context = allocate_tool_workdir(
                    tool_id=tool_id,
                    keep_workdir=args.keep_workdir,
                )
            save_json(workdir / "provenance.json", provenance)
            previous_payload = read_json(cost_path, {})
            save_json(workdir / "cost-before.json", previous_payload)
            old_profiles = {
                p["profile_id"]: p
                for p in previous_payload.get("profiles", [])
                if isinstance(p, dict) and "profile_id" in p
            }
            cost_profile_entries: list[dict[str, Any]] = []
            tool_total_runs = 0
            tool_success_runs = 0
            tool_timeout_runs = 0
            tool_failed_runs = 0
            for profile in target.profiles:
                print(f"[{tool_id}] profile: {profile.profile_id}")
                profile_sizes = sizes_for_profile(
                    profile=profile,
                    default_sizes=default_sizes,
                    cli_sizes_explicit=cli_sizes_explicit,
                )
                benchmark_config = build_benchmark_config(
                    sizes=profile_sizes,
                    threads=tool_threads,
                    ram_gb=ram_gb,
                    params_profile=profile.params_profile,
                    execution_profile=profile.execution_profile,
                    input_profile=profile.input_profile,
                    args=args,
                )
                benchmark_config["provenance"] = provenance
                benchmark_config["profile_sha256"] = profile_fingerprint(
                    profile, root=REPO_ROOT, kind="inference"
                )
                profile_workdir = workdir / safe_path_token(profile.profile_id)
                evidence_roots = list(args.reuse_results_dir)
                if args.results_dir:
                    evidence_roots.append(args.results_dir)
                initial_runs, input_hashes, execution_points = (
                    prepare_incremental_measurements(
                        profile=profile,
                        config=benchmark_config,
                        existing=old_profiles.get(profile.profile_id),
                        evidence_roots=evidence_roots,
                        workdir=profile_workdir,
                        sizes=profile_sizes,
                        threads=tool_threads,
                        ram_gb=ram_gb,
                        repeats=args.repeats,
                        seed=args.seed,
                        force_rerun=args.force_rerun,
                        remeasure_historical=args.remeasure_incompatible_history,
                        source_kernels=source_kernels,
                    )
                )
                save_json(profile_workdir / "benchmark_config.json", benchmark_config)

                def checkpoint(rows: list[dict[str, Any]]) -> None:
                    nonlocal wrote_cost
                    points = completed_incremental_points(
                        rows,
                        profile=profile,
                        config=benchmark_config,
                        hashes=input_hashes,
                        repeats=args.repeats,
                    )
                    save_json(profile_workdir / "completed-points.json", points)
                    if points and not args.no_write_cost:
                        entry = make_cost_profile_entry(
                            profile_id=profile.profile_id,
                            runtime_points=points,
                            benchmark_config=benchmark_config,
                        )
                        payload = make_cost_payload(profile_entries=[entry])
                        # Preserve every unrelated profile, even for a full-catalog run.
                        if read_json(cost_path, {}).get("profiles"):
                            payload = merge_profiles(cost_path, payload)
                        write_tool_cost_profile(
                            cost_path=cost_path, cost_payload=payload
                        )
                        wrote_cost = True

                if initial_runs:
                    checkpoint(initial_runs)
                if args.reuse_only:
                    profile_runs = initial_runs
                    save_json(profile_workdir / "measurements.json", profile_runs)
                else:
                    profile_runs, run_index, fail_fast_triggered = (
                        execute_tool_benchmarks(
                            tool_id=tool_id,
                            profile_id=profile.profile_id,
                            image_tag=image_tag,
                            workdir=profile_workdir,
                            sizes=profile_sizes,
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
                            initial_runs=initial_runs,
                            on_progress=checkpoint,
                            point_plan=execution_points,
                            min_free_gb=args.min_free_gb,
                            concurrency=args.concurrency,
                            resource_pool=resource_pool,
                            cancel_event=cancel_event,
                        )
                    )
                profile_summary = summarize_tool_runs(profile_runs)
                tool_total_runs += profile_summary["total_runs"]
                tool_success_runs += profile_summary["successful_runs"]
                tool_timeout_runs += profile_summary["timeout_runs"]
                tool_failed_runs += profile_summary["failed_runs"]
                global_success += profile_summary["successful_runs"]
                global_timeout += profile_summary["timeout_runs"]
                global_fail += profile_summary["failed_runs"]

                runtime_points = completed_incremental_points(
                    profile_runs,
                    profile=profile,
                    config=benchmark_config,
                    hashes=input_hashes,
                    repeats=args.repeats,
                )
                profile_complete = {
                    point_key(point)
                    for point in runtime_points
                    if complete_point(point, args.repeats)
                } >= execution_points
                if not profile_complete:
                    had_error = had_error or not args.reuse_only
                    unpersistable_profile = True
                    print(
                        f"[{tool_id}/{profile.profile_id}] incomplete matrix; completed points checkpointed, failures retained in evidence."
                    )
                if runtime_points and (profile_complete or args.reuse_only):
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
                    f"timeout={profile_summary['timeout_runs']} "
                    f"failed={profile_summary['failed_runs']}"
                )
                if fail_fast_triggered:
                    break

            can_write = bool(cost_profile_entries) and (
                args.merge_existing or not unpersistable_profile
            )
            if can_write and not args.no_write_cost:
                payload = make_cost_payload(profile_entries=cost_profile_entries)
                if read_json(cost_path, {}).get("profiles"):
                    payload = merge_profiles(cost_path, payload)
                write_tool_cost_profile(cost_path=cost_path, cost_payload=payload)
                wrote_cost = True
            elif not cost_profile_entries:
                had_error = had_error or not args.reuse_only
                print(
                    f"[{tool_id}] no fully attempted profiles; available terminal observations were checkpointed."
                )

            print(
                f"[{tool_id}] summary: profiles={len(cost_profile_entries)}/{len(target.profiles)} "
                f"total={tool_total_runs} ok={tool_success_runs} "
                f"timeout={tool_timeout_runs} failed={tool_failed_runs} "
                f"wrote_cost={wrote_cost}"
            )

        except WaitingForResources as exc:
            waiting_resources = True
            print(f"[{tool_id}] waiting_resources: {exc}", file=sys.stderr, flush=True)
        except BenchmarkInterrupted as exc:
            cancel_event.set()
            print(f"[{tool_id}] interrupted: {exc}", file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001
            had_error = True
            print(f"[{tool_id}] ERROR: {exc}", file=sys.stderr)
            if args.fail_fast:
                break
        finally:
            if workdir_context is not None:
                workdir_context.cleanup()
            if cost_lock is not None:
                cost_lock.close()

        if fail_fast_triggered or cancel_event.is_set():
            break

    print()
    print(
        f"Benchmark finished. successful_runs={global_success} "
        f"timeout_runs={global_timeout} "
        f"failed_runs={global_fail}"
    )
    if had_error:
        return 1
    if cancel_event.is_set():
        return 130
    return 75 if waiting_resources else 0


def run(argv: Sequence[str] | None = None) -> int:
    cancelled = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():

        def stop(signum, _frame):
            cancelled.set()

        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, stop)
    try:
        return _run(argv, cancel_event=cancelled)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
