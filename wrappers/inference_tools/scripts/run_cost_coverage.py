"""Run declarative inference cost panels with a shared resource executor.

Tools and profiles retain a single cost writer. Their fit pool admits only
available, reserved CPU/RAM allocations; the scientific panel is never reduced
to fit the current machine. No images are rebuilt.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import io
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import importlib.util

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "wrappers"))
from benchmark_support import (
    atomic_json,
    build_context_fingerprint,
    profile_fingerprint,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def campaign_threads(threading):
    maximum = threading.get("max_threads")
    return (
        min(2, maximum)
        if threading["supported"] and maximum is not None
        else 2 if threading["supported"] else 1
    )


def integer_panel(value):
    try:
        values = sorted({int(token.strip()) for token in value.split(",")})
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use comma-separated positive integers"
        ) from exc
    if not values or any(number < 1 for number in values):
        raise argparse.ArgumentTypeError("Use comma-separated positive integers")
    return values


def resource_threads(threading, requested):
    if requested is None:
        return [campaign_threads(threading)]
    maximum = threading.get("max_threads")
    values = [
        value
        for value in requested
        if (threading["supported"] or value == 1)
        and (maximum is None or value <= maximum)
    ]
    if not values:
        raise ValueError("No requested CPU assignment is supported by the tool")
    return values


def baseline_panel(config, profile_id, threading, threads=None, ram_gb=None):
    config = config or {}
    raw = next(
        (row for row in config.get("profiles", []) if row.get("id") == profile_id), {}
    )
    design = {
        **config.get("calibration_design", {}),
        **raw.get("calibration_design", {}),
    }
    resources = design.get("resources", {})

    def panel(values, name):
        if (
            not isinstance(values, (tuple, list))
            or not values
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 1
                for value in values
            )
        ):
            raise ValueError(f"Invalid declarative {name} panel for {profile_id}")
        return sorted(set(values))

    declared_threads = panel(
        resources.get(
            "threads",
            raw.get("threads_tested", threads or [campaign_threads(threading)]),
        ),
        "CPU",
    )
    declared_ram = panel(
        resources.get("ram_gb", raw.get("ram_gb_tested", ram_gb or [8])), "RAM"
    )
    if resource_threads(threading, declared_threads) != declared_threads:
        raise ValueError(
            f"Declarative CPU panel is incompatible with ToolSpec for {profile_id}"
        )
    selected_threads = panel(
        threads if threads is not None else declared_threads, "selected CPU"
    )
    selected_ram = panel(ram_gb if ram_gb is not None else declared_ram, "selected RAM")
    if not set(selected_threads) <= set(declared_threads) or not set(
        selected_ram
    ) <= set(declared_ram):
        raise ValueError(
            "CLI resource selections must be subsets of the declarative panel"
        )
    repeats = design.get("repetitions", raw.get("repeats", config.get("repeats", 3)))
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 3:
        raise ValueError(
            f"Declarative repetitions must be at least three for {profile_id}"
        )
    return {
        "threads": declared_threads,
        "ram_gb": declared_ram,
        "repeats": repeats,
        "selected_threads": selected_threads,
        "selected_ram_gb": selected_ram,
        "declarative_resources": bool(resources),
        "debug_resource_subset": selected_threads != declared_threads
        or selected_ram != declared_ram,
    }


def execution_plan(
    selected,
    timeout,
    *,
    threads=None,
    ram_gb=None,
    profile_filters=(),
    include_representative=True,
):
    """Resolve the real recipes without inspecting or running Docker images."""
    old_path = sys.path[:]
    saved = {
        key: value
        for key, value in sys.modules.items()
        if key == "shared" or key.startswith("shared.")
    }
    try:
        for key in saved:
            del sys.modules[key]
        sys.path.insert(0, str(SCRIPTS))
        spec = importlib.util.spec_from_file_location(
            "andrea_campaign_plan_benchmark", SCRIPTS / "benchmark_costs.py"
        )
        benchmark = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = benchmark
        spec.loader.exec_module(benchmark)
        from shared.representative_profiles import load_representative_profiles
        from shared.benchmark_profiles import load_profile_config

        # Non-matching tools are routine when a qualified profile is selected;
        # keep their informational prints out of the machine-readable plan.
        with redirect_stdout(io.StringIO()):
            targets = benchmark.resolve_tool_targets(
                selected_tools=selected,
                catalog_tools_root=ROOT / "andrea/catalog_inference_tools/tools",
                param_overrides_dir=benchmark.DEFAULT_PARAM_OVERRIDES_DIR,
                cost_profiles_dir=benchmark.DEFAULT_COST_PROFILES_DIR,
                default_group_count=benchmark.DEFAULT_GROUP_COUNT,
                default_prior_density=benchmark.DEFAULT_PRIOR_DENSITY,
                default_optional_inputs=None,
                profile_filters=profile_filters,
            )
        rows = []
        for target in targets:
            threading = target.toolspec["runtime_resources"]["threading"]
            config = load_profile_config(
                cost_profiles_dir=benchmark.DEFAULT_COST_PROFILES_DIR,
                tool_id=target.tool_id,
            )
            cost_path = (
                ROOT
                / "andrea/catalog_inference_tools/tools"
                / target.tool_id
                / "cost.json"
            )
            previous = (
                {
                    profile["profile_id"]: profile
                    for profile in json.loads(cost_path.read_text()).get("profiles", [])
                }
                if cost_path.exists()
                else {}
            )
            for profile in target.profiles:
                panel = baseline_panel(
                    config, profile.profile_id, threading, threads, ram_gb
                )
                sizes = benchmark.sizes_for_profile(
                    profile=profile,
                    default_sizes=[
                        benchmark.parse_size(value) for value in benchmark.DEFAULT_SIZES
                    ],
                    cli_sizes_explicit=False,
                )
                requested = {
                    (size.genes, size.columns, thread, float(ram))
                    for size in sizes
                    for thread in panel["threads"]
                    for ram in panel["ram_gb"]
                }
                old = previous.get(profile.profile_id, {})
                historical = {
                    (
                        point["genes"],
                        point["columns"],
                        point["threads"],
                        float(point["ram_gb"]),
                    )
                    for point in old.get("runtime_points", [])
                }
                old_config = old.get("benchmark_config", {})
                if old_config.get("profile_sha256") == profile_fingerprint(
                    profile, root=ROOT, kind="inference"
                ):
                    historical.update(
                        (
                            point["genes"],
                            point["columns"],
                            point["threads"],
                            float(point["ram_gb"]),
                        )
                        for point in old_config.get("planned_points", [])
                    )
                rows.append(
                    {
                        "phase": "baseline",
                        "tool_id": target.tool_id,
                        "profile_id": profile.profile_id,
                        "sizes": [vars(size) for size in sizes],
                        **panel,
                        "historical_points_retained": [
                            dict(zip(("genes", "columns", "threads", "ram_gb"), point))
                            for point in sorted(historical - requested)
                        ],
                        "runs": len(requested | historical) * panel["repeats"],
                        "selected_runs": len(sizes)
                        * len(panel["selected_threads"])
                        * len(panel["selected_ram_gb"])
                        * panel["repeats"],
                        "timeout_seconds_per_run": timeout,
                    }
                )
            for recipe in (
                load_representative_profiles(target.tool_id)
                if include_representative
                else []
            ):
                config = recipe["benchmark_config"]
                rows.append(
                    {
                        "phase": "representative",
                        "tool_id": target.tool_id,
                        "profile_id": recipe["profile_id"],
                        "sizes": config["sizes"],
                        "threads": config["threads_tested"][0],
                        "ram_gb": config["ram_gb_tested"][0],
                        "repeats": config["repeats"],
                        "runs": len(recipe["fixtures"]) * config["repeats"],
                        "timeout_seconds_per_run": config["timeout_seconds"],
                    }
                )
        return {
            "schema_version": 1,
            "profiles": rows,
            "total_runs": sum(row["runs"] for row in rows),
            "baseline_timeout_seconds": timeout,
            "baseline_threads_requested": (
                list(threads) if threads is not None else None
            ),
            "baseline_ram_gb_requested": list(ram_gb) if ram_gb is not None else None,
            "debug_subset": bool(profile_filters)
            or any(row.get("debug_resource_subset") for row in rows),
            "representative_enabled": include_representative,
            "timeout_budget_seconds": sum(
                row["runs"] * row["timeout_seconds_per_run"] for row in rows
            ),
            "budget_scope": "Sequential timed runs, including explicit historical points, before verified reuse. Historical compatibility must still be checked; incompatible points require explicit remeasurement authorization. Excludes setup/validation. Timeout allocation is not a measured ETA.",
            "image_resolution": "Immutable image IDs are frozen in campaign.json before execution.",
        }
    finally:
        sys.path[:] = old_path
        for key in list(sys.modules):
            if key == "shared" or key.startswith("shared."):
                del sys.modules[key]
        sys.modules.update(saved)
        sys.modules.pop("andrea_campaign_plan_benchmark", None)


def recorded_slot_ledger(plan):
    """Report retained candidates without claiming offline image/input verification."""
    result = {
        "planned_repetitions": 0,
        "recorded_terminal_repetitions": 0,
        "recorded_statuses": {
            status: 0 for status in ("ok", "timeout", "oom", "error")
        },
        "pending_repetitions": 0,
        "verification": "Embedded records only; the measurement driver rechecks image, hardware, input bytes and timeout compatibility before reuse.",
        "profiles": [],
    }
    cached = {}
    for row in plan["profiles"]:
        if row["phase"] != "baseline":
            continue
        tool = row["tool_id"]
        if tool not in cached:
            path = ROOT / "andrea/catalog_inference_tools/tools" / tool / "cost.json"
            cached[tool] = {
                profile["profile_id"]: profile
                for profile in (
                    json.loads(path.read_text()).get("profiles", [])
                    if path.exists()
                    else []
                )
            }
        profile = cached[tool].get(row["profile_id"], {})
        config = profile.get("benchmark_config", {})
        expected = {
            (size["genes"], size["columns"], thread, float(ram), repeat)
            for size in row["sizes"]
            for thread in row["threads"]
            for ram in row["ram_gb"]
            for repeat in range(1, row["repeats"] + 1)
        }
        for point in row["historical_points_retained"]:
            expected.update(
                (
                    point["genes"],
                    point["columns"],
                    point["threads"],
                    float(point["ram_gb"]),
                    repeat,
                )
                for repeat in range(1, row["repeats"] + 1)
            )
        observed = {}
        for point in profile.get("runtime_points", []):
            for observation in point.get("measurement_evidence", {}).get(
                "observations", []
            ):
                status, seconds = observation.get("status"), observation.get("seconds")
                if (
                    status not in result["recorded_statuses"]
                    or not isinstance(seconds, (int, float))
                    or isinstance(seconds, bool)
                    or not math.isfinite(seconds)
                    or seconds <= 0
                ):
                    continue
                cap = row["timeout_seconds_per_run"]
                original_cap = observation.get(
                    "measurement_timeout_seconds", config.get("timeout_seconds")
                )
                if (
                    (status == "ok" and seconds > cap)
                    or (status != "ok" and original_cap != cap)
                    or (status == "timeout" and seconds < cap)
                ):
                    continue
                try:
                    key = (
                        observation["genes"],
                        observation["columns"],
                        observation["threads"],
                        float(observation["ram_gb"]),
                        observation["repeat"],
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if key in expected:
                    observed.setdefault(key, status)
        statuses = {
            status: sum(value == status for value in observed.values())
            for status in result["recorded_statuses"]
        }
        summary = {
            "tool_id": tool,
            "profile_id": row["profile_id"],
            "planned_repetitions": len(expected),
            "recorded_terminal_repetitions": len(observed),
            "recorded_statuses": statuses,
            "pending_repetitions": len(expected) - len(observed),
        }
        result["profiles"].append(summary)
        for key in (
            "planned_repetitions",
            "recorded_terminal_repetitions",
            "pending_repetitions",
        ):
            result[key] += summary[key]
        for status, count in statuses.items():
            result["recorded_statuses"][status] += count
    return result


def baseline_groups(tool, plan):
    groups = {}
    for row in plan["profiles"]:
        if row["phase"] == "baseline" and row["tool_id"] == tool:
            key = (
                tuple(row["selected_threads"]),
                tuple(row["selected_ram_gb"]),
                row["repeats"],
            )
            groups.setdefault(key, []).append(row)
    return list(groups.values())


def baseline_command(tool, evidence, plan, args, rows=None):
    rows = (
        rows
        if rows is not None
        else [
            row
            for row in plan["profiles"]
            if row["phase"] == "baseline" and row["tool_id"] == tool
        ]
    )
    panels = {
        (tuple(row["selected_threads"]), tuple(row["selected_ram_gb"]), row["repeats"])
        for row in rows
    }
    if len(panels) != 1:
        raise ValueError(
            "One measurement command requires a single resolved resource/repetition panel"
        )
    selected_threads, selected_ram, repeats = next(iter(panels))
    command = [
        sys.executable,
        "-u",
        str(SCRIPTS / "benchmark_costs.py"),
        "--tool",
        tool,
        "--catalog-images",
        "--skip-build",
        "--threads",
        ",".join(map(str, selected_threads)),
        "--ram-gb",
        ",".join(map(str, selected_ram)),
        "--repeats",
        str(repeats),
        "--timeout",
        str(args.timeout),
        "--seed",
        str(args.seed),
        "--min-free-gb",
        str(args.min_free_gb),
        "--results-dir",
        str(evidence / "measurements/baseline" / tool),
        "--merge-existing",
        "--concurrency",
        str(args.concurrency),
        "--ram-headroom-gb",
        str(args.ram_headroom_gb),
        "--ram-headroom-fraction",
        str(args.ram_headroom_fraction),
        "--cpu-headroom",
        str(args.cpu_headroom),
    ]
    for flag, value in (
        ("--max-cpu", args.max_cpu),
        ("--max-ram-gb", args.max_ram_gb),
        (
            "--resource-state-dir",
            (
                args.resource_state_dir.resolve()
                if args.resource_state_dir is not None
                else None
            ),
        ),
    ):
        if value is not None:
            command += [flag, str(value)]
    for row in rows:
        command += ["--profile", row["profile_id"]]
    for previous in args.reuse_results_dir:
        command += ["--reuse-results-dir", str(previous.resolve())]
    if args.reuse_source_root is not None:
        command += [
            "--reuse-source-root",
            str(args.reuse_source_root.resolve()),
            "--reuse-source-manifest",
            str(args.reuse_source_manifest.resolve()),
        ]
    if args.remeasure_incompatible_history:
        command += ["--remeasure-incompatible-history"]
    return command


def runtime_resource_snapshot(args):
    name = "_andrea_cost_campaign_resources"
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS / "shared/cost_resources.py"
    )
    resources = importlib.util.module_from_spec(spec)
    sys.modules[name] = resources
    spec.loader.exec_module(resources)
    state_dir = (
        args.resource_state_dir.resolve()
        if args.resource_state_dir is not None
        else resources.default_state_dir()
    )
    pool = resources.ResourcePool(
        state_dir,
        ram_headroom_gb=args.ram_headroom_gb,
        ram_headroom_fraction=args.ram_headroom_fraction,
        cpu_headroom=args.cpu_headroom,
        max_threads=args.max_cpu,
        max_ram_gb=args.max_ram_gb,
    )
    snapshot = pool.snapshot()
    snapshot["resource_state_dir"] = str(state_dir)
    snapshot["fit_concurrency"] = args.concurrency or max(
        1, int(snapshot["cpu_budget"])
    )
    if snapshot.get("orphaned_leases"):
        raise RuntimeError(
            "Orphaned cost resource reservations require verified cleanup before resuming"
        )
    return snapshot


def validation_command(tools, evidence):
    command = [
        sys.executable,
        str(SCRIPTS / "validate_tool_costs.py"),
        "--require",
        "--require-provenance",
        "--require-attempts",
        "--coverage-report",
        str(evidence / "coverage-final.json"),
    ]
    for tool in tools:
        command += ["--tool", tool]
    return command


def frozen_sources():
    paths = [
        *SCRIPTS.rglob("*.py"),
        ROOT / "wrappers/benchmark_support.py",
        *SCRIPTS.parent.joinpath("cost_profiles").rglob("*.json"),
        ROOT / "andrea/catalog_inference_tools/schemas/toolcost.schema.json",
    ]
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def owned_container_cleanup(evidence: Path):
    ids = subprocess.check_output(["docker", "ps", "-q"], text=True).split()
    if not ids:
        return
    containers = json.loads(
        subprocess.check_output(["docker", "inspect", *ids], text=True)
    )
    for container in containers:
        if any(
            Path(m.get("Source", "/")).is_relative_to(evidence)
            for m in container.get("Mounts", [])
        ):
            subprocess.run(["docker", "rm", "-f", container["Id"]], check=False)


def process_record(pid: int, command: list[str]):
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "start_ticks": stat[19], "command": command}


def terminate_recorded_child(record: dict | None, *, grace_seconds: float = 60):
    """Stop only a verified worker, never a subsequently recycled PID."""
    if not record:
        return False
    pid = record["pid"]
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        command = Path(f"/proc/{pid}/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        if (
            stat[19] != record["start_ticks"]
            or command != [os.fsencode(arg) for arg in record["command"]]
            or os.getpgid(pid) != pid
        ):
            return False
        os.killpg(pid, signal.SIGTERM)
        # The worker owns fit cancellation, container verification, checkpoints
        # and resource release. Let it drain before the coordinator escalates.
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            try:
                current = (
                    Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                )
                if current[0] == "Z" or current[19] != record["start_ticks"]:
                    return True
            except FileNotFoundError:
                return True
            time.sleep(0.1)
        os.killpg(pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return False
    except FileNotFoundError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        help="Measure and validate only these tool ids (repeatable).",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        help="Limit baseline measurements to these profile ids or tool:profile values (repeatable); final validation still checks every registered profile of each selected tool.",
    )
    parser.add_argument(
        "--threads",
        type=integer_panel,
        help="Select a debug subset of the declared CPU panel; otherwise use its full declaration.",
    )
    parser.add_argument(
        "--ram-gb",
        type=integer_panel,
        help="Select a debug subset of the declared RAM panel.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=0,
        help="Maximum concurrent native fits (0 = automatically use the detected CPU budget); the shared broker also limits CPU/RAM admission.",
    )
    parser.add_argument(
        "--resource-state-dir",
        type=Path,
        help="Shared cost resource registry; defaults to the same per-user/per-boot registry for all cost executors.",
    )
    parser.add_argument(
        "--max-cpu",
        type=int,
        help="Optional operational CPU limit; it never removes panel slots.",
    )
    parser.add_argument(
        "--max-ram-gb",
        type=float,
        help="Optional operational RAM limit; it never removes panel slots.",
    )
    parser.add_argument("--ram-headroom-gb", type=float, default=1)
    parser.add_argument("--ram-headroom-fraction", type=float, default=0.1)
    parser.add_argument("--cpu-headroom", type=int, default=0)
    parser.add_argument(
        "--skip-representative",
        action="store_true",
        help="Run only the synthetic baseline recipes.",
    )
    parser.add_argument("--reuse-results-dir", type=Path, action="append", default=[])
    parser.add_argument(
        "--reuse-source-root",
        type=Path,
        help="Read-only source snapshot proving the original measurement identity.",
    )
    parser.add_argument(
        "--reuse-source-manifest",
        type=Path,
        help="Original source-file seal for --reuse-source-root.",
    )
    parser.add_argument(
        "--reuse-representative-results-dir", type=Path, action="append", default=[]
    )
    parser.add_argument(
        "--remeasure-incompatible-history",
        action="store_true",
        help="Retain historical points and remeasure them when an image/input correction invalidates their evidence.",
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--min-free-gb", type=float, default=20)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error(
            "A positive per-run timeout is required for a finite campaign budget"
        )
    if args.seed < 0:
        parser.error("--seed must be nonnegative")
    if not math.isfinite(args.min_free_gb) or args.min_free_gb < 0:
        parser.error("--min-free-gb must be finite and nonnegative")
    if (
        args.concurrency < 0
        or args.cpu_headroom < 0
        or (args.max_cpu is not None and args.max_cpu < 1)
    ):
        parser.error(
            "Concurrency and CPU headroom must be nonnegative; CPU limits must be positive"
        )
    if (
        not math.isfinite(args.ram_headroom_gb)
        or args.ram_headroom_gb < 0
        or not math.isfinite(args.ram_headroom_fraction)
        or not 0 <= args.ram_headroom_fraction < 1
        or (
            args.max_ram_gb is not None
            and (not math.isfinite(args.max_ram_gb) or args.max_ram_gb <= 0)
        )
    ):
        parser.error(
            "RAM limits/headroom must be finite and within their valid positive ranges"
        )
    if not args.skip_representative and args.fixture_root is None:
        parser.error(
            "--fixture-root is required unless --skip-representative is selected"
        )
    if bool(args.reuse_source_root) != bool(args.reuse_source_manifest):
        parser.error(
            "--reuse-source-root and --reuse-source-manifest must be provided together"
        )
    proof = None
    if args.reuse_source_root is not None:
        if (
            not args.reuse_source_root.is_dir()
            or not args.reuse_source_manifest.is_file()
        ):
            parser.error("The reused source snapshot and original manifest must exist")
        proof = {
            "root": str(args.reuse_source_root.resolve()),
            "manifest": str(args.reuse_source_manifest.resolve()),
            "manifest_sha256": hashlib.sha256(
                args.reuse_source_manifest.read_bytes()
            ).hexdigest(),
        }
    catalog = ROOT / "andrea/catalog_inference_tools/tools"
    available_tools = {path.parent.name for path in catalog.glob("*/toolspec.json")}
    unknown = set(args.tool) - available_tools
    if unknown:
        parser.error(f"Unknown tool ids: {sorted(unknown)}")
    selected = [
        (path.parent.name, path.parent)
        for path in sorted(catalog.glob("*/toolspec.json"))
        if not args.tool or path.parent.name in args.tool
    ]
    plan = execution_plan(
        selected,
        args.timeout,
        threads=args.threads,
        ram_gb=args.ram_gb,
        profile_filters=args.profile,
        include_representative=not args.skip_representative,
    )
    plan["reuse_source_proof"] = proof
    plan["measurement_seed"] = args.seed
    plan["executor_policy"] = {
        "max_concurrent_fits": args.concurrency or "auto",
        "max_cpu": args.max_cpu,
        "max_ram_gb": args.max_ram_gb,
        "ram_headroom_gb": args.ram_headroom_gb,
        "ram_headroom_fraction": args.ram_headroom_fraction,
        "cpu_headroom": args.cpu_headroom,
        "resource_state_dir": (
            str(args.resource_state_dir.resolve())
            if args.resource_state_dir is not None
            else None
        ),
    }
    ledger = recorded_slot_ledger(plan)
    if args.plan_only:
        print(json.dumps({**plan, "recorded_slot_ledger": ledger}, indent=2))
        return 0
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    lock = (evidence / "campaign.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("Another process is already running this campaign")
    plan_path = evidence / "execution-plan.json"
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise ValueError(
            "Execution plan changed; retain evidence and use a new campaign directory"
        )
    atomic_json(plan_path, plan)
    atomic_json(evidence / "slot-ledger.json", ledger)
    capacity = runtime_resource_snapshot(args)
    atomic_json(evidence / "resource-capacity.json", capacity)
    print(
        f"[{now()}] RESOURCE CAPACITY {json.dumps(capacity, sort_keys=True)}",
        flush=True,
    )
    print(
        f"[{now()}] BASELINE SLOTS planned={ledger['planned_repetitions']} "
        f"recorded_terminal={ledger['recorded_terminal_repetitions']} "
        f"pending={ledger['pending_repetitions']} statuses={ledger['recorded_statuses']}",
        flush=True,
    )
    tools = []
    selected_names = {row["tool_id"] for row in plan["profiles"]}
    for path in sorted(catalog.glob("*/toolspec.json")):
        if path.parent.name not in selected_names:
            continue
        spec = json.loads(path.read_text())
        cost_path = path.with_name("cost.json")
        cost = json.loads(cost_path.read_text()) if cost_path.exists() else {}
        weight = max(
            (
                point["seconds_p90"]
                for profile in cost.get("profiles", [])
                for point in profile.get("runtime_points", [])
                if isinstance(point.get("seconds_p90"), (int, float))
                and math.isfinite(point["seconds_p90"])
            ),
            default=0,
        )
        image = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", spec["docker_image"]], text=True
            )
        )[0]
        tools.append(
            {
                "tool": path.parent.name,
                "threads": sorted(
                    {
                        value
                        for row in plan["profiles"]
                        if row["phase"] == "baseline"
                        and row["tool_id"] == path.parent.name
                        for value in row["threads"]
                    }
                ),
                "image": spec["docker_image"],
                "image_id": image["Id"],
                "spec_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "build_context_sha256": build_context_fingerprint(
                    root=ROOT, kind="inference", identifier=path.parent.name
                ),
                "ordering_weight": weight,
            }
        )
    jobs = []
    for phase in (
        ["baseline"] if args.skip_representative else ["baseline", "representative"]
    ):
        for tool in sorted(tools, key=lambda t: (t["ordering_weight"], t["tool"])):
            name = tool["tool"]
            if not any(
                row["phase"] == phase and row["tool_id"] == name
                for row in plan["profiles"]
            ):
                continue
            results = evidence / "measurements" / phase / name
            if phase == "baseline":
                groups = baseline_groups(name, plan)
                for index, rows in enumerate(groups, start=1):
                    suffix = f"-panel-{index}" if len(groups) > 1 else ""
                    jobs.append(
                        {
                            "id": f"baseline/{name}{suffix}",
                            "tool_id": name,
                            "command": baseline_command(
                                name, evidence, plan, args, rows
                            ),
                            "log": str(
                                evidence / "logs" / f"baseline-{name}{suffix}.log"
                            ),
                            "status": "pending",
                        }
                    )
                continue
            else:
                command = [
                    sys.executable,
                    "-u",
                    str(SCRIPTS / "benchmark_representative_costs.py"),
                    "--tool",
                    name,
                    "--fixture-root",
                    str(args.fixture_root.resolve()),
                    "--results-dir",
                    str(results),
                ]
                for previous in args.reuse_representative_results_dir:
                    command += ["--reuse-results-dir", str(previous.resolve())]
            jobs.append(
                {
                    "id": f"{phase}/{name}",
                    "tool_id": name,
                    "command": command,
                    "log": str(evidence / "logs" / f"{phase}-{name}.log"),
                    "status": "pending",
                }
            )
    definition = {
        "schema_version": 1,
        "created_at": now(),
        "scope": f"{len(tools)} inference tools",
        "resources": {
            "maximum_parallel_measurements": args.concurrency or "auto",
            "panel": "Declared in execution-plan.json independently of host capacity",
            "execution_policy": plan["executor_policy"],
            "baseline_timeout_seconds": args.timeout,
            "minimum_free_disk_gb": args.min_free_gb,
        },
        "tools": tools,
        "source_sha256": frozen_sources(),
        "jobs": jobs,
    }
    frozen = evidence / "campaign.json"
    if frozen.exists():
        old = json.loads(frozen.read_text())
        if old["source_sha256"] != definition["source_sha256"]:
            raise ValueError(
                "Campaign source changed; retain this evidence and start a new campaign directory"
            )
        if {
            t["tool"]: (t["image_id"], t["spec_sha256"], t.get("build_context_sha256"))
            for t in old["tools"]
        } != {
            t["tool"]: (t["image_id"], t["spec_sha256"], t.get("build_context_sha256"))
            for t in tools
        }:
            raise ValueError("Campaign images/specifications changed")
        if {j["id"]: j["command"] for j in old["jobs"]} != {
            j["id"]: j["command"] for j in jobs
        }:
            raise ValueError("Campaign commands changed")
        definition = old
        jobs = definition["jobs"]
    else:
        atomic_json(frozen, definition)
    state_path = evidence / "status.json"
    state = {
        "status": "running",
        "started_at": now(),
        "jobs": jobs,
        "completed_jobs": 0,
        "failed_jobs": 0,
        "active_job": None,
        "initial_recorded_slot_ledger": ledger,
        "resource_capacity": capacity,
    }
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if {job["id"]: job["command"] for job in state["jobs"]} != {
            job["id"]: job["command"] for job in jobs
        }:
            raise ValueError(
                "Campaign state commands differ from the frozen definition"
            )
        terminate_recorded_child(state.get("child"))
        owned_container_cleanup(evidence)
        state["child"] = None
        state.update(status="running", resumed_at=now(), resource_capacity=capacity)
    child = None

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        for job in state["jobs"]:
            if job["status"] == "completed":
                continue
            if frozen_sources() != definition["source_sha256"]:
                raise ValueError(
                    "Calibration source changed while the campaign was running"
                )
            if (
                proof is not None
                and hashlib.sha256(Path(proof["manifest"]).read_bytes()).hexdigest()
                != proof["manifest_sha256"]
            ):
                raise ValueError("The original measurement source manifest changed")
            free = shutil.disk_usage(evidence).free
            if free < args.min_free_gb * 1024**3:
                raise RuntimeError(
                    "Insufficient free disk for the campaign reserve; all evidence is retained"
                )
            state["free_disk_bytes"] = free
            tool_name = job["tool_id"]
            frozen_tool = next(t for t in definition["tools"] if t["tool"] == tool_name)
            spec_path = catalog / tool_name / "toolspec.json"
            current_image = subprocess.check_output(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    frozen_tool["image"],
                ],
                text=True,
            ).strip()
            if (
                current_image != frozen_tool["image_id"]
                or hashlib.sha256(spec_path.read_bytes()).hexdigest()
                != frozen_tool["spec_sha256"]
                or build_context_fingerprint(
                    root=ROOT, kind="inference", identifier=tool_name
                )
                != frozen_tool["build_context_sha256"]
            ):
                raise ValueError(
                    f"Frozen image or specification changed for {tool_name}"
                )
            log = Path(job["log"])
            log.parent.mkdir(parents=True, exist_ok=True)
            job.update(status="running", started_at=now())
            state.update(active_job=job["id"], updated_at=now())
            atomic_json(state_path, state)
            print(f"[{now()}] START {job['id']} -> {log}", flush=True)
            with log.open("a") as stream:
                child = subprocess.Popen(
                    job["command"],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    text=True,
                    bufsize=1,
                )
                state["child"] = process_record(child.pid, job["command"])
                atomic_json(state_path, state)
                for line in child.stdout:
                    stream.write(line)
                    stream.flush()
                    print(line, end="", flush=True)
                code = child.wait()
            child = None
            state["child"] = None
            job.update(
                status=(
                    "completed"
                    if code == 0
                    else "waiting_resources" if code == 75 else "failed"
                ),
                exit_code=code,
                finished_at=now(),
            )
            state.update(
                completed_jobs=sum(j["status"] == "completed" for j in state["jobs"]),
                failed_jobs=sum(j["status"] == "failed" for j in state["jobs"]),
                updated_at=now(),
                active_job=None,
            )
            atomic_json(state_path, state)
            print(
                f"[{now()}] {job['status'].upper()} {job['id']} exit={code}", flush=True
            )
            if code == 75:
                atomic_json(evidence / "slot-ledger.json", recorded_slot_ledger(plan))
                state.update(
                    status="waiting_resources", active_job=None, updated_at=now()
                )
                atomic_json(state_path, state)
                print(
                    f"[{now()}] CAMPAIGN WAITING_RESOURCES; the declared panel and unfinished slots are retained",
                    flush=True,
                )
                return 75
        ledger = recorded_slot_ledger(plan)
        atomic_json(evidence / "slot-ledger.json", ledger)
        validation = validation_command([tool["tool"] for tool in tools], evidence)
        with (evidence / "validation-final.log").open("w") as stream:
            result = subprocess.run(
                validation, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT
            )
        state.update(
            status=(
                "completed"
                if not state["failed_jobs"] and result.returncode == 0
                else "failed"
            ),
            validation_exit_code=result.returncode,
            finished_at=now(),
        )
        atomic_json(state_path, state)
        print(f"[{now()}] CAMPAIGN {state['status'].upper()}", flush=True)
        return 0 if state["status"] == "completed" else 1
    except BaseException as exc:
        if child is not None and child.poll() is None:
            terminate_recorded_child(state.get("child"))
            child.wait(timeout=15)
        owned_container_cleanup(evidence)
        state.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            error=type(exc).__name__ + ": " + str(exc),
            updated_at=now(),
        )
        atomic_json(state_path, state)
        raise


if __name__ == "__main__":
    sys.exit(main())
