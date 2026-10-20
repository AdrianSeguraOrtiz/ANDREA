#!/usr/bin/env python3
"""Run the finite dynGENIE3 image matrix after an external experiment completes.

Nothing runs on import. --plan-only writes a reproducible plan without Docker.
Actual execution requires a full immutable image ID and --external-state JSON;
the external state is re-read before every container is started. This helper
neither builds/publishes images nor activates a catalog entry or fits costs.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from typing import Any
import uuid


HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[1] / "tests" / "fixtures" / "dyngenie3"
TIMEOUT_SECONDS = 3600
COMPLETED = {"completed", "complete", "succeeded"}
COMMIT = "27eddd960d50e82791a8e0e5f8fcf6d679fe3eac"
NETWORK_FIELDS = ["source", "target", "score", "sign", "evidence", "context"]


class ExternalExperimentActive(RuntimeError):
    """The supplied external state does not authorize starting another run."""


class RuntimeInterrupted(KeyboardInterrupt):
    """An operator signal should stop the owned container and retain evidence."""


def _interrupt(signum, _frame):
    raise RuntimeInterrupted(f"Interrupted by {signal.Signals(signum).name}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_image(image: str) -> str:
    _require(
        bool(re.fullmatch(r"sha256:[0-9a-f]{64}", image)),
        "--image must be a full immutable Docker image ID: sha256:<64 lowercase hex digits>",
    )
    return image


def external_state_receipt(path: Path) -> dict[str, Any]:
    """Fail closed: only an explicit successfully completed state permits work."""
    try:
        raw = path.read_bytes()
        state = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalExperimentActive(
            f"Cannot read a valid --external-state: {path}: {exc}"
        ) from exc
    if (
        not isinstance(state, dict)
        or state.get("active") is True
        or not isinstance(state.get("status"), str)
        or state["status"] not in COMPLETED
    ):
        raise ExternalExperimentActive(
            f"External experiment is not complete: {path}; status={state.get('status') if isinstance(state, dict) else None!r}"
        )
    if "active" in state and not isinstance(state["active"], bool):
        raise ExternalExperimentActive(
            "external-state.active must be boolean when provided"
        )
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "status": state["status"],
        "checked_at": _utc(),
    }


def make_plan(
    image: str,
    *,
    config_path: Path = HERE / "smoketest.config.draft.json",
    fixtures: Path = FIXTURES,
) -> dict[str, Any]:
    validate_image(image)
    config = _json(config_path)
    variants = config.get("variants", [])
    _require(
        len(variants) == 7 and len({row.get("name") for row in variants}) == 7,
        "Expected the reviewed seven distinct smoke variants",
    )
    defaults = [row for row in variants if row.get("name") == "real_defaults"]
    _require(
        len(defaults) == 1 and not defaults[0].get("param_overrides"),
        "real_defaults must exist without parameter overrides",
    )
    variants = [defaults[0], *(row for row in variants if row is not defaults[0])]
    cases = []
    for row in variants:
        cases.append(
            {
                "id": f"smoke_{row['name']}",
                "purpose": "functional_variant",
                "variant": row["name"],
                "threads": 1,
                "params": {
                    **config.get("param_overrides", {}),
                    **row.get("param_overrides", {}),
                },
                "extra_files": row.get("extra_files", config.get("extra_files", [])),
            }
        )
    for threads in (1, 2):
        for repetition in (1, 2):
            cases.append(
                {
                    "id": f"seed17_threads{threads}_repeat{repetition}",
                    "purpose": "seed_repeat",
                    "variant": "default_RF",
                    "threads": threads,
                    "params": {"seed": 17},
                    "extra_files": config["extra_files"],
                }
            )
    cases.append(
        {
            "id": "seed18_threads1",
            "purpose": "changed_seed",
            "variant": "default_RF",
            "threads": 1,
            "params": {"seed": 18},
            "extra_files": config["extra_files"],
        }
    )
    names = {
        "expression.tsv",
        *(name for case in cases for name in case["extra_files"]),
    }
    for name in names:
        _require(
            Path(name).name == name and (fixtures / name).is_file(),
            f"Missing or invalid fixture {name}",
        )
    spec = _json(HERE / "toolspec.draft.json")
    default_params = {key: value["default"] for key, value in spec["params"].items()}
    for index, case in enumerate(cases, 1):
        case.update(
            {
                "index": index,
                "cpus": case["threads"],
                "memory": "4g",
                "memory_swap": "4g",
                "timeout_seconds": TIMEOUT_SECONDS,
                "resolved_params": {**default_params, **case["params"]},
            }
        )
    plan = {
        "kind": "dyngenie3_runtime_plan",
        "schema_version": 1,
        "image": image,
        "upstream_commit": COMMIT,
        "execution": config["execution"],
        "fixtures": str(fixtures.resolve()),
        "cases": cases,
        "fixture_sha256": {name: _sha(fixtures / name) for name in sorted(names)},
        "source_sha256": {
            name: _sha(HERE / name)
            for name in (
                "run_tool.py",
                "patch_upstream.py",
                "Dockerfile",
                "requirements.lock",
                "toolspec.draft.json",
                "verify_runtime.py",
            )
        },
        "smoke_config_sha256": _sha(config_path),
        "run_count": len(cases),
        "resource_strata": [
            {
                "threads": threads,
                "cpus": threads,
                "memory": "4g",
                "runs": sum(case["threads"] == threads for case in cases),
            }
            for threads in (1, 2)
        ],
        "execution_timeout_cap_seconds": len(cases) * TIMEOUT_SECONDS,
        "estimated_duration_seconds": None,
        "budget_note": "12 serial executions, maximum 3600 seconds each. The timeout sum is an execution cap, not a measured ETA; validation and cleanup add overhead.",
        "reproducibility": {
            "comparison": "Exact VIM.npy SHA-256 equality for all four seed-17 runs, including both worker allocations; seed 18 must change the VIM.",
            "limitations": "Valid only for this pinned image, fixtures, numerical runtime and host. Resource pools require separate effective-runtime evidence.",
        },
    }
    plan["plan_sha256"] = _digest(plan)
    return plan


def _hash_tree(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): _sha(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def stage_case(case: dict[str, Any], plan: dict[str, Any], directory: Path) -> Path:
    _require(not directory.exists(), f"Refusing to overwrite retained run: {directory}")
    io = directory / "io"
    (io / "extra").mkdir(parents=True)
    (io / "out").mkdir()
    fixtures = Path(plan["fixtures"])
    for name in ("expression.tsv", *case["extra_files"]):
        _require(
            _sha(fixtures / name) == plan["fixture_sha256"][name],
            f"Fixture changed since planning: {name}",
        )
        shutil.copy2(
            fixtures / name,
            io / name if name == "expression.tsv" else io / "extra" / name,
        )
    _write(io / "params.json", case["params"])
    _write(io / "execution.json", plan["execution"])
    return io


def docker_command(image: str, case: dict[str, Any], io: Path, name: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--network",
        "none",
        "--cpus",
        str(case["cpus"]),
        "--memory",
        case["memory"],
        "--memory-swap",
        case["memory_swap"],
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{io.resolve()}:/io:ro",
        "-v",
        f"{(io / 'out').resolve()}:/io/out:rw",
        image,
        "--input",
        "/io/expression.tsv",
        "--params",
        "/io/params.json",
        "--extra",
        "/io/extra",
        "--output-dir",
        "/io/out",
        "--threads",
        str(case["threads"]),
    ]


def _matrix_axes(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    _require(bool(rows), "Missing expression header")
    return (
        [row[0] for row in rows[1:] if row],
        rows[0][1:],
        [[float(value) for value in row[1:]] for row in rows[1:] if row],
    )


def validate_worker_resources(raw: Path, assigned_threads: int) -> dict[str, Any]:
    worker_reports = sorted(raw.glob("resource-worker-*.json"))
    _require(
        1 <= len(worker_reports) <= assigned_threads,
        "Actual worker observations are missing or exceed the assigned worker count",
    )
    observed_pools = []
    seen_pids = set()
    for path in worker_reports:
        observation = _json(path)
        pid = observation.get("pid")
        _require(
            isinstance(pid, int)
            and pid > 0
            and path.name == f"resource-worker-{pid}.json"
            and pid not in seen_pids,
            "Invalid or duplicated observed worker identity",
        )
        seen_pids.add(pid)
        pools = observation.get("native_pools")
        _require(
            isinstance(pools, list) and bool(pools),
            "Worker report has no observed native pools",
        )
        for pool in pools:
            workers = pool.get("num_threads")
            _require(
                isinstance(workers, int)
                and not isinstance(workers, bool)
                and workers == 1,
                "Native library exceeded one thread per target worker",
            )
            observed_pools.append(
                {
                    "pid": pid,
                    "internal_api": pool.get("internal_api"),
                    "num_threads": workers,
                }
            )
        cgroups = observation.get("cgroups", {})
        _require(
            cgroups.get("version") in (1, 2),
            "Effective container CPU/RAM cgroup limits are unavailable",
        )
        cpu, memory = cgroups.get("cpu", {}), cgroups.get("memory", {})
        _require(
            cpu.get("limit_cpus") == assigned_threads
            and cpu.get("quota_us") is not None
            and isinstance(cpu.get("period_us"), int)
            and cpu["period_us"] > 0
            and cpu["quota_us"] / cpu["period_us"] == assigned_threads
            and bool(cpu.get("raw")),
            "Effective container CPU quota differs from assigned CPUs",
        )
        _require(
            memory.get("limit_bytes") == 4 * 1024**3 and bool(memory.get("raw")),
            "Effective container memory limit differs from 4 GiB",
        )
    return {"worker_count": len(worker_reports), "native_pools": observed_pools}


def validate_outputs(
    case: dict[str, Any], plan: dict[str, Any], io: Path
) -> dict[str, Any]:
    import numpy as np

    out = io / "out"
    raw = out / "provenance"
    genes, columns, expression = _matrix_axes(io / "expression.tsv")
    vim = np.load(raw / "VIM.npy", allow_pickle=False)
    _require(
        vim.shape == (len(genes), len(genes))
        and np.isfinite(vim).all()
        and np.all(vim >= 0),
        "Invalid finite nonnegative VIM dimensions",
    )
    _require(np.all(np.diag(vim) == 0), "Native VIM contains a self interaction")
    sums = vim.sum(axis=0)
    _require(
        np.allclose(sums[sums > 0], 1.0, rtol=1e-10, atol=1e-12),
        "VIM does not preserve target normalization",
    )
    tf_path = io / "extra" / "tf_list.txt"
    candidates = (
        tf_path.read_text(encoding="utf-8").splitlines() if tf_path.is_file() else genes
    )
    _require(
        np.all(
            vim[
                [index for index, gene in enumerate(genes) if gene not in candidates], :
            ]
            == 0
        ),
        "VIM includes a noncandidate source",
    )
    expected = [
        (genes[source], genes[target], float(vim[source, target]))
        for source, target in np.ndindex(vim.shape)
        if source != target and vim[source, target] > 0
    ]
    expected.sort(key=lambda row: (-row[2], row[0], row[1]))
    maximum = case["resolved_params"]["maxcount"]
    if maximum != "all":
        expected = expected[:maximum]
    with (out / "network.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _require(
            reader.fieldnames == NETWORK_FIELDS,
            "Incorrect standardized network columns",
        )
        edges = []
        for row in reader:
            _require(
                row["source"] in candidates
                and row["target"] in genes
                and row["source"] != row["target"],
                "Invalid source/target identifiers or self edge",
            )
            score = float(row["score"])
            _require(math.isfinite(score) and score > 0, "Invalid edge magnitude")
            _require(
                (row["sign"], row["evidence"], row["context"])
                == ("?", "association", "global"),
                "Incorrect sign/evidence/context",
            )
            edges.append((row["source"], row["target"], score))
    _require(
        edges == expected,
        "network.csv differs from the deterministic full-precision positive VIM projection",
    )
    _require(
        bool(edges) or maximum == 0,
        "Unexpected empty network for the varied smoke fixture",
    )
    resolved = _json(raw / "resolved.json")
    _require(
        resolved["params"] == case["resolved_params"]
        and resolved["genes"] == genes
        and resolved["threads"] == case["threads"],
        "Resolved parameters/IDs/resources differ from request",
    )
    _require(
        resolved["temporal"] == plan["execution"]["temporal"],
        "Temporal declaration lost or altered",
    )
    expected_regulators = (
        [gene for gene in genes if gene in candidates] if tf_path.is_file() else "all"
    )
    _require(
        resolved["regulators"] == expected_regulators,
        "Candidate regulator mapping differs",
    )
    seeds = [
        int(
            np.random.SeedSequence(
                [case["resolved_params"]["seed"], index]
            ).generate_state(1)[0]
        )
        for index in range(len(genes))
    ]
    _require(
        resolved["target_seeds"] == seeds and resolved["forest_random_states"] == seeds,
        "Per-target seeds differ from declared propagation",
    )
    resource_observations = validate_worker_resources(raw, case["threads"])
    constructor = resolved["forest_constructor_params"]
    params = case["resolved_params"]
    _require(
        constructor["random_state"] == seeds[0]
        and constructor["n_jobs"] == 1
        and constructor["n_estimators"] == params["ntrees"],
        "Forest constructor seed/workers/tree count differ",
    )
    _require(
        constructor["oob_score"]
        == (params["compute_quality_scores"] and params["tree_method"] == "RF"),
        "Incorrect OOB mode",
    )
    _require(
        constructor["bootstrap"] == (params["tree_method"] == "RF"),
        "Incorrect RF/ET bootstrap default",
    )
    count = len(candidates)
    features = params["K"]
    all_features = features == "all" or isinstance(features, int) and features >= count
    expected_features = (
        count
        if all_features
        else max(1, int(math.sqrt(count))) if features == "sqrt" else features
    )
    expected_argument = 1.0 if all_features else features
    _require(
        resolved["max_features_argument"]
        == constructor["max_features"]
        == expected_argument
        and resolved["max_features_count"] == expected_features,
        "Incorrect resolved K/default feature count",
    )
    with (io / "extra" / "timepoints.tsv").open(encoding="utf-8", newline="") as handle:
        temporal_rows = {
            row["column"]: row for row in csv.DictReader(handle, delimiter="\t")
        }
    grouped: dict[str, list[str]] = {}
    for column in columns:
        grouped.setdefault(temporal_rows[column]["series_id"], []).append(column)
    _require(len(resolved["series"]) == len(grouped), "Lost independent series")
    for series in resolved["series"]:
        ids = sorted(
            grouped[series["series_id"]],
            key=lambda column: float(temporal_rows[column]["timepoint"]),
        )
        times = [float(temporal_rows[column]["timepoint"]) for column in ids]
        _require(
            series["columns"] == ids
            and series["timepoints"] == times
            and series["upstream_timepoints"]
            == np.asarray(times, dtype=np.float32).tolist(),
            "Series/time/column join changed",
        )
    ss_path = io / "extra" / "steady_state_expression.tsv"
    ss_columns = _matrix_axes(ss_path)[1] if ss_path.is_file() else []
    _require(
        resolved["steady_state_columns"] == ss_columns,
        "Steady-state sample axis changed",
    )
    _require(
        resolved["effective_training_rows"]
        == sum(len(ids) - 1 for ids in grouped.values()) + len(ss_columns),
        "Incorrect within-series training row count",
    )
    with (raw / "degradation_rates.tsv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        _require(
            reader.fieldnames == ["gene", "alpha", "inverse_time_unit"],
            "Missing degradation rate schema",
        )
        rates = list(reader)
    _require(
        [row["gene"] for row in rates] == genes, "Degradation rate gene order changed"
    )
    alphas = np.asarray([float(row["alpha"]) for row in rates])
    _require(
        np.isfinite(alphas).all()
        and np.all(alphas >= 0)
        and alphas.tolist() == resolved["alpha_resolved"],
        "Invalid/unaligned resolved alpha",
    )
    _require(
        all(
            row["inverse_time_unit"]
            == f"1/{plan['execution']['temporal']['time_unit']}"
            for row in rates
        ),
        "Lost alpha inverse-time units",
    )
    alpha = params["alpha"]
    if alpha != "from_data":
        expected_alpha = alpha if isinstance(alpha, list) else [alpha] * len(genes)
        _require(alphas.tolist() == expected_alpha, "Explicit alpha changed")
    else:
        _require(
            all(
                alphas[index] == 0
                for index, values in enumerate(expression)
                if len(set(values)) == 1
            ),
            "Constant gene received nonzero estimated degradation",
        )
    quality = _json(raw / "quality_scores.json")
    _require(
        quality["requested"] == params["compute_quality_scores"],
        "Quality request changed",
    )
    for field in ("prediction_score", "stability_score"):
        _require(
            quality[field] is None or math.isfinite(float(quality[field])),
            "Nonfinite quality score not represented as null",
        )
    if not params["compute_quality_scores"]:
        _require(
            quality["prediction_score"] is None and quality["stability_score"] is None,
            "Unexpected disabled diagnostics",
        )
    if params["tree_method"] == "ET":
        _require(
            quality["prediction_score"] is None, "ET incorrectly supplies RF OOB score"
        )
    if params["save_models"]:
        _require(
            (raw / "tree_models.joblib").is_file()
            and (raw / "tree_models.joblib").stat().st_size > 0,
            "Requested model artifact is missing/empty",
        )
    session = _json(raw / "session.json")
    _require(
        session["upstream_commit"] == COMMIT
        and bool(re.fullmatch(r"[0-9a-f]{64}", session["patched_source_sha256"])),
        "Missing pinned source/session identity",
    )
    for line in (HERE / "requirements.lock").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            package, version = line.split()[0].split("==", 1)
            _require(
                session["packages"][package] == version,
                f"Runtime package pin changed: {package}",
            )
    progress = _json(out / "progress.json")
    _require(
        progress["status"] == "completed" and progress["percent"] == 100,
        "Missing completed phase progress",
    )
    expected_input_hashes = {
        path.name: _sha(path)
        for path in [io / "expression.tsv", *sorted((io / "extra").iterdir())]
        if path.is_file()
    }
    _require(
        resolved["input_sha256"] == expected_input_hashes,
        "Wrapper input hash inventory differs",
    )
    return {
        "genes": len(genes),
        "columns": len(columns),
        "series": len(grouped),
        "training_rows": resolved["effective_training_rows"],
        "positive_edges": len(edges),
        "vim_sha256": _sha(raw / "VIM.npy"),
        "patched_source_sha256": session["patched_source_sha256"],
        "target_seeds": seeds,
        "resource_observations": resource_observations,
        "checks": [
            "network_projection",
            "finite_magnitudes",
            "source_target_identifiers",
            "direction_sign_context",
            "target_normalization",
            "series_boundaries",
            "alpha_shape_units",
            "parameter_defaults",
            "forest_configuration",
            "seed_propagation",
            "actual_worker_native_pools",
            "runtime_pins",
            "phase_progress",
        ],
    }


def run_case(
    case: dict[str, Any], plan: dict[str, Any], directory: Path, external_state: Path
) -> dict[str, Any]:
    io = stage_case(case, plan, directory)
    name = f"andrea-dyngenie3-verify-{uuid.uuid4().hex[:12]}-{case['index']}"
    command = docker_command(plan["image"], case, io, name)
    receipt = {
        "kind": "dyngenie3_runtime_run",
        "id": case["id"],
        "image": plan["image"],
        "plan_sha256": plan["plan_sha256"],
        "status": "planned",
        "resources": {
            key: case[key]
            for key in ("threads", "cpus", "memory", "memory_swap", "timeout_seconds")
        },
        "requested_params": case["params"],
        "resolved_params": case["resolved_params"],
        "command": command,
        "input_sha256": {
            name: digest
            for name, digest in _hash_tree(io).items()
            if not name.startswith("out/")
        },
        "outputs_sha256": {},
    }
    started = time.monotonic()
    try:
        receipt["external_state"] = external_state_receipt(external_state)
        receipt.update({"status": "running", "started_at": _utc()})
        _write(directory / "receipt.json", receipt)
        with (
            (directory / "stdout.log").open("w", encoding="utf-8") as stdout,
            (directory / "stderr.log").open("w", encoding="utf-8") as stderr,
        ):
            result = subprocess.run(
                command,
                stdout=stdout,
                stderr=stderr,
                timeout=case["timeout_seconds"],
                check=False,
            )
        receipt["exit_code"] = result.returncode
        _require(
            result.returncode == 0, f"Container exited with status {result.returncode}"
        )
        receipt["validation"] = validate_outputs(case, plan, io)
        receipt["status"] = "passed"
    except ExternalExperimentActive as exc:
        receipt.update({"status": "blocked", "error": str(exc), "exit_code": None})
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        interrupted = isinstance(exc, KeyboardInterrupt)
        receipt.update(
            {
                "status": "failed",
                "error": str(exc) or "Operator interrupted verification",
                "timed_out": not interrupted,
                "interrupted": interrupted,
                "exit_code": None,
            }
        )
        # A killed Docker client may leave its container alive. Remove only this
        # helper's uniquely named container, even if an external job became active.
        cleanup = ["docker", "rm", "--force", name]
        with (directory / "cleanup.log").open("w", encoding="utf-8") as output:
            try:
                result = subprocess.run(
                    cleanup,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                    check=False,
                )
                receipt["cleanup"] = {
                    "command": cleanup,
                    "exit_code": result.returncode,
                }
            except Exception as cleanup_error:
                receipt["cleanup"] = {"command": cleanup, "error": str(cleanup_error)}
    except Exception as exc:
        receipt.update({"status": "failed", "error": str(exc)})
    receipt.update(
        {
            "finished_at": _utc(),
            "elapsed_seconds": time.monotonic() - started,
            "outputs_sha256": _hash_tree(io / "out"),
            "logs_sha256": {
                path.name: _sha(path) for path in sorted(directory.glob("*.log"))
            },
        }
    )
    _write(directory / "receipt.json", receipt)
    return receipt


def compare_reproducibility(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {receipt["id"]: receipt for receipt in receipts}
    names = [
        f"seed17_threads{threads}_repeat{repeat}"
        for threads in (1, 2)
        for repeat in (1, 2)
    ]
    missing = [
        name
        for name in [*names, "seed18_threads1"]
        if by_id.get(name, {}).get("status") != "passed"
    ]
    if missing:
        return {
            "status": "failed",
            "reason": "Successful independent executions are missing",
            "missing": missing,
        }
    hashes = [by_id[name]["validation"]["vim_sha256"] for name in names]
    identical = len(set(hashes)) == 1
    changed = by_id["seed18_threads1"]["validation"]["vim_sha256"] != hashes[0]
    same_source = (
        len(
            {
                receipt["validation"]["patched_source_sha256"]
                for receipt in receipts
                if receipt["status"] == "passed"
            }
        )
        == 1
    )
    return {
        "status": "passed" if identical and changed and same_source else "failed",
        "seed17_same_within_and_across_workers": identical,
        "seed18_changed_vim": changed,
        "same_patched_source": same_source,
        "seed17_vim_sha256": hashes,
        "seed18_vim_sha256": by_id["seed18_threads1"]["validation"]["vim_sha256"],
        "tolerance": "Exact SHA-256 equality of native VIM.npy; no hardware-independent claim",
    }


def run_campaign(
    plan: dict[str, Any], out: Path, external_state: Path
) -> dict[str, Any]:
    receipts: list[dict[str, Any]] = []
    result = {
        "kind": "dyngenie3_runtime_verification",
        "image": plan["image"],
        "image_id": plan["image"],
        "plan_sha256": plan["plan_sha256"],
        "status": "running",
        "started_at": _utc(),
        "runs": receipts,
    }
    _write(out / "report.json", result)
    _write(out / "verification.json", result)
    for case in plan["cases"]:
        try:
            receipt = run_case(
                case,
                plan,
                out / "runs" / f"{case['index']:02d}_{case['id']}",
                external_state,
            )
        except Exception as exc:
            result.update(
                {"status": "failed", "reason": f"Could not stage {case['id']}: {exc}"}
            )
            break
        receipts.append(receipt)
        _write(out / "report.json", result)
        _write(out / "verification.json", result)
        if (
            receipt["status"] == "blocked"
            or receipt.get("interrupted")
            or receipt.get("cleanup", {}).get("error")
            or receipt.get("cleanup", {}).get("exit_code", 0) != 0
        ):
            result["status"] = "blocked" if receipt["status"] == "blocked" else "failed"
            result["reason"] = (
                receipt["error"]
                if receipt["status"] == "blocked" or receipt.get("interrupted")
                else "Timed-out container cleanup could not be verified; refusing to start another run"
            )
            break
    else:
        result["reproducibility"] = compare_reproducibility(receipts)
        result["status"] = (
            "passed"
            if all(receipt["status"] == "passed" for receipt in receipts)
            and result["reproducibility"]["status"] == "passed"
            else "failed"
        )
    result.update(
        {
            "finished_at": _utc(),
            "completed_run_count": sum(
                receipt["status"] != "blocked" for receipt in receipts
            ),
            "receipt_count": len(receipts),
            "planned_run_count": plan["run_count"],
            "not_executed": [case["id"] for case in plan["cases"][len(receipts) :]],
            "elapsed_seconds": sum(receipt["elapsed_seconds"] for receipt in receipts),
        }
    )
    _write(out / "report.json", result)
    _write(out / "verification.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="Full immutable local image ID, sha256:<64 hex>; not a mutable tag",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="New retained evidence directory; a matching plan-only directory may be reused",
    )
    parser.add_argument(
        "--external-state",
        type=Path,
        help="Required for execution: external experiment JSON with successfully completed status",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Write plan/counts/resources/budget without any Docker invocation",
    )
    args = parser.parse_args(argv)
    try:
        plan = make_plan(args.image)
        if not args.plan_only:
            _require(
                args.external_state is not None,
                "Real execution requires --external-state; --plan-only does not",
            )
        out = args.out.resolve()
        if out.exists():
            existing = list(out.iterdir())
            _require(
                not existing
                or len(existing) == 1
                and existing[0].name == "plan.json"
                and _json(existing[0]) == plan,
                "Refusing to overwrite prior evidence or an incompatible plan; use a new --out directory",
            )
        out.mkdir(parents=True, exist_ok=True)
        _write(out / "plan.json", plan)
        if args.plan_only:
            print(
                json.dumps(
                    {
                        "status": "planned_only",
                        "plan": str(out / "plan.json"),
                        "image": plan["image"],
                        "run_count": plan["run_count"],
                        "resource_strata": plan["resource_strata"],
                        "execution_timeout_cap_seconds": plan[
                            "execution_timeout_cap_seconds"
                        ],
                        "estimated_duration_seconds": None,
                    }
                )
            )
            return 0
        previous_handlers = {
            signum: signal.getsignal(signum)
            for signum in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            for signum in previous_handlers:
                signal.signal(signum, _interrupt)
            result = run_campaign(plan, out, args.external_state)
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "image_id": result["image_id"],
                    "report": str(out / "verification.json"),
                    "completed_run_count": result["completed_run_count"],
                }
            )
        )
        return 0 if result["status"] == "passed" else 2
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
