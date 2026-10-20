"""Run the complete inference cost coverage campaign sequentially (PM2 friendly).

The campaign retains logs/state and runs only missing compatible measurements.
No images are rebuilt, and measurement workers never run concurrently.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
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


def execution_plan(selected, timeout):
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

        targets = benchmark.resolve_tool_targets(
            selected_tools=selected,
            catalog_tools_root=ROOT / "andrea/catalog_inference_tools/tools",
            param_overrides_dir=benchmark.DEFAULT_PARAM_OVERRIDES_DIR,
            cost_profiles_dir=benchmark.DEFAULT_COST_PROFILES_DIR,
            default_group_count=benchmark.DEFAULT_GROUP_COUNT,
            default_prior_density=benchmark.DEFAULT_PRIOR_DENSITY,
            default_optional_inputs=None,
            profile_filters=[],
        )
        rows = []
        for target in targets:
            threading = target.toolspec["runtime_resources"]["threading"]
            threads = campaign_threads(threading)
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
                sizes = benchmark.sizes_for_profile(
                    profile=profile,
                    default_sizes=[
                        benchmark.parse_size(value) for value in benchmark.DEFAULT_SIZES
                    ],
                    cli_sizes_explicit=False,
                )
                requested = {(size.genes, size.columns, threads, 8.0) for size in sizes}
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
                        "threads": threads,
                        "ram_gb": 8,
                        "historical_points_retained": [
                            dict(zip(("genes", "columns", "threads", "ram_gb"), point))
                            for point in sorted(historical - requested)
                        ],
                        "repeats": 3,
                        "runs": len(requested | historical) * 3,
                        "timeout_seconds_per_run": timeout,
                    }
                )
            for recipe in load_representative_profiles(target.tool_id):
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
            "parallel_measurements": 1,
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


def terminate_recorded_child(record: dict | None):
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
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                state = (
                    Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
                )
                if state == "Z":
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        help="Recalibrate only these tool ids (repeatable); final validation still covers the full catalog.",
    )
    parser.add_argument("--reuse-results-dir", type=Path, action="append", default=[])
    parser.add_argument(
        "--reuse-representative-results-dir", type=Path, action="append", default=[]
    )
    parser.add_argument(
        "--remeasure-incompatible-history",
        action="store_true",
        help="Retain historical points and remeasure them when an image/input correction invalidates their evidence.",
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=10800)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error(
            "A positive per-run timeout is required for a finite campaign budget"
        )
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
    plan = execution_plan(selected, args.timeout)
    if args.plan_only:
        print(json.dumps(plan, indent=2))
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
    tools = []
    for path in sorted(catalog.glob("*/toolspec.json")):
        if args.tool and path.parent.name not in args.tool:
            continue
        spec = json.loads(path.read_text())
        threads = campaign_threads(spec["runtime_resources"]["threading"])
        cost = json.loads(path.with_name("cost.json").read_text())
        weight = max(
            p["seconds_p90"]
            for profile in cost["profiles"]
            for p in profile["runtime_points"]
        )
        image = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", spec["docker_image"]], text=True
            )
        )[0]
        tools.append(
            {
                "tool": path.parent.name,
                "threads": threads,
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
    for phase in ["baseline", "representative"]:
        for tool in sorted(tools, key=lambda t: (t["ordering_weight"], t["tool"])):
            name = tool["tool"]
            results = evidence / "measurements" / phase / name
            if phase == "baseline":
                command = [
                    sys.executable,
                    "-u",
                    str(SCRIPTS / "benchmark_costs.py"),
                    "--tool",
                    name,
                    "--catalog-images",
                    "--skip-build",
                    "--threads",
                    str(tool["threads"]),
                    "--ram-gb",
                    "8",
                    "--repeats",
                    "3",
                    "--timeout",
                    str(args.timeout),
                    "--results-dir",
                    str(results),
                ]
                for previous in args.reuse_results_dir:
                    command += ["--reuse-results-dir", str(previous.resolve())]
                if args.remeasure_incompatible_history:
                    command += ["--remeasure-incompatible-history"]
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
            "parallel_measurements": 1,
            "threads": "2 or 1 for single-thread tools",
            "ram_gb": 8,
            "repeats": 3,
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
    }
    if state_path.exists():
        state = json.loads(state_path.read_text())
        terminate_recorded_child(state.get("child"))
        owned_container_cleanup(evidence)
        state["child"] = None
        state.update(status="running", resumed_at=now())
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
            tool_name = job["id"].split("/", 1)[1]
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
                status="completed" if code == 0 else "failed",
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
        validation = [
            sys.executable,
            str(SCRIPTS / "validate_tool_costs.py"),
            "--require",
            "--require-provenance",
            "--require-coverage",
            "--coverage-report",
            str(evidence / "coverage-final.json"),
        ]
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
