#!/usr/bin/env python3
"""Freeze and check finite held-out dynGENIE3 timing predictions.

--plan-only writes an unmeasured protocol without fitting or starting Docker.
Actual work requires an explicitly completed external experiment, the retained
calibration executions, and a full immutable image ID. Nothing runs on import.
The estimator varies genes within explicitly reserved fixed recipe/temporal/resource strata.
"""

from __future__ import annotations

import argparse
import builtins
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
from typing import Any
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SCRIPTS = HERE.parents[1] / "scripts"
PROFILE_ID = "global_rf_from_data_ts_t8_s2_spec_defaults"
TRAINING_GENES = (50, 100, 200)
RESOURCES = tuple((cpu, ram) for cpu in (1, 2, 4, 8) for ram in (4, 8))
RESERVATION_PATH = ROOT / "wrappers/inference_tools/cost_profiles/dyngenie3.json"
REPEATS = 3
TIMEOUT = 3600
ERROR_LIMIT = 0.35
MIN_COVERAGE = 1.0
BUDGET_RESERVE = 1.25
SCOPE = (
    "Independent gene-axis checks at reserved 75/250-gene cases, each within its "
    "exact measured recipe, longitudinal t8/s2 temporal geometry, observed hours, "
    "sampling pattern, steady-state count, immutable image and hardware, across "
    "eight CPU/RAM strata. Only named held-out branches are validated. "
    "No temporal interpolation, unmeasured branch interaction, statistical "
    "prediction interval or general guarantee."
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _support():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    package = "_dyngenie3_inference_support"
    if package not in sys.modules:
        _module(package, SCRIPTS / "shared/__init__.py")
    prediction_evidence = importlib.import_module(f"{package}.prediction_evidence")
    backend_name = "_dyngenie3_prediction_benchmark"
    if backend_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            backend_name, SCRIPTS / "benchmark_costs.py"
        )
        backend = importlib.util.module_from_spec(spec)

        def private_import(name, globals=None, locals=None, fromlist=(), level=0):
            # The script's historical absolute imports must resolve to inference
            # helpers even when a simulation library has already loaded shared.
            # This override belongs only to this backend module's builtins.
            if level == 0 and (name == "shared" or name.startswith("shared.")):
                name = package + name[len("shared") :]
            return builtins.__import__(name, globals, locals, fromlist, level)

        backend.__dict__["__builtins__"] = {
            **vars(builtins),
            "__import__": private_import,
        }
        sys.modules[backend_name] = backend
        try:
            spec.loader.exec_module(backend)
        except BaseException:
            sys.modules.pop(backend_name, None)
            raise
    benchmark_costs = sys.modules[backend_name]

    validate_cost_predictions = _module(
        "dyngenie3_generic_prediction_checks",
        ROOT / "wrappers/validate_cost_predictions.py",
    )
    return prediction_evidence, benchmark_costs, validate_cost_predictions


def _runtime():
    return _module("dyngenie3_prediction_runtime", HERE / "verify_runtime.py")


def reserved_cases() -> list[dict[str, Any]]:
    """Read predeclared independent cases, never infer them from measured errors."""
    manifest = _read(RESERVATION_PATH)
    cases = manifest["holdout_reservation"]["cases"]
    declared_profiles = {profile["id"] for profile in manifest["profiles"]}
    _require(isinstance(cases, list) and bool(cases), "No reserved hold-out cases")
    seen = set()
    for case in cases:
        _require(
            isinstance(case.get("id"), str)
            and case["id"] not in seen
            and re.fullmatch(r"[a-zA-Z0-9_-]+", case["id"]),
            "Reserved hold-out IDs must be distinct safe path names",
        )
        seen.add(case["id"])
        _require(
            case.get("profile_id") in declared_profiles,
            "Reserved profile is undeclared",
        )
        _require(
            case.get("genes") in (75, 250)
            and case.get("columns") == 16
            and case.get("timepoints_per_series") == 8
            and case.get("independent_series") == 2
            and type(case.get("seed")) is int
            and case.get("kind")
            == ("interpolation" if case["genes"] == 75 else "extrapolation"),
            "Hold-outs require reserved 75/250 genes and fixed t8/s2 geometry",
        )
    return cases


def protocol(image: str) -> dict[str, Any]:
    _require(
        bool(re.fullmatch(r"sha256:[0-9a-f]{64}", image)),
        "--image requires a full immutable sha256 Docker image ID",
    )
    cases = reserved_cases()
    return {
        "schema_version": 1,
        "kind": "dyngenie3_timing_holdout_protocol",
        "image_id": image,
        "profile_ids": sorted({case["profile_id"] for case in cases}),
        "training_genes": list(TRAINING_GENES),
        "holdouts": [{**case, "generator_seed": case["seed"]} for case in cases],
        "resources": [
            {"threads": threads, "ram_gb": ram} for threads, ram in RESOURCES
        ],
        "repeats": REPEATS,
        "planned_run_count": len(cases) * len(RESOURCES) * REPEATS,
        "timeout_seconds_per_run": TIMEOUT,
        "max_relative_error": ERROR_LIMIT,
        "min_planning_coverage": MIN_COVERAGE,
        "planning_reserve": BUDGET_RESERVE,
        "estimator_recipe": "Frozen calls to ANDREA _estimate_tool_mode_options with exact CPU/RAM assignments and temporal context. Planning duration is the exact returned ETA; typical diagnostic is nearest-point p50 times the core size scale. A separate 25% operating reserve is not used to approve ETA coverage.",
        "scope": SCOPE,
        "automatic_retries": False,
    }


def validate_training_panel(
    entries: list[dict[str, Any]], *, config: dict | None = None
) -> None:
    """Require the declared panel without fitting a separate estimator."""
    grouped: dict[tuple[int, float], dict[int, list[float]]] = {}
    execution_ids = set()
    for entry in entries:
        row = entry["raw_row"]
        run_id = row.get("run_id")
        _require(
            isinstance(run_id, str) and run_id and run_id not in execution_ids,
            "Training executions require distinct captured run IDs",
        )
        execution_ids.add(run_id)
        dimensions = row["dimensions"]
        _require(
            row.get("status") == "ok"
            and row["genes"] in TRAINING_GENES
            and row["columns"] == 16
            and dimensions.get("timepoints_per_series") == 8
            and dimensions.get("independent_series") == 2
            and (
                config is None
                or dimensions.get("steady_state_samples", 0)
                == config["input_profile"]["temporal_fixture"]["steady_state_samples"]
            ),
            "Training observations must use only successful 50/100/200-gene t8/s2 cases",
        )
        seconds = row["seconds"]
        _require(
            type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0,
            "Training duration must be finite and positive",
        )
        key = (row["threads"], float(row["ram_gb"]))
        _require(
            key in RESOURCES, "Training resource allocation is outside the frozen panel"
        )
        grouped.setdefault(key, {}).setdefault(row["genes"], []).append(float(seconds))
    _require(
        len(entries) == len(TRAINING_GENES) * len(RESOURCES) * REPEATS
        and set(grouped) == set(RESOURCES),
        "Exactly three training repeats at each 50/100/200-gene point across eight resource allocations are required",
    )
    for points in grouped.values():
        _require(
            set(points) == set(TRAINING_GENES)
            and all(len(values) == REPEATS for values in points.values()),
            "Each training gene/resource point needs exactly three observations",
        )


def planner_prediction(
    *, cost, toolspec, config, io, genes, threads, ram_gb, profile_id=PROFILE_ID
) -> dict[str, Any]:
    """Call the estimator consumed by actual inference planning, unchanged."""
    from andrea.core.commands.infer_network.commons.planner import (
        _estimate_tool_mode_options,
        _temporal_cost_context,
    )
    from andrea.core.commands.infer_network.commons.shared import DatasetContext
    from andrea.core.shared.bulk_temporal import validate_bulk_temporal_inputs

    # File names follow the same public benchmark input contract as execution.
    extras = {
        path.name.split(".")[0]: path
        for path in (io / "extra").iterdir()
        if path.is_file()
    }
    _require(
        set(extras) == set(config["input_profile"]["extras_provided"]),
        "Generated extras differ from the frozen profile",
    )
    dataset = DatasetContext(
        dataset_id="dyngenie3_holdout",
        column_kind="samples",
        expression_profile="bulk",
        taxonomic_group="synthetic",
        ncbi_taxon_id=None,
        genes=genes,
        columns=16,
        expression_matrix_path=io / "expression.tsv",
        extras=extras,
        temporal=config["input_profile"]["temporal"],
    )
    summary = validate_bulk_temporal_inputs(
        temporal=dataset.temporal,
        expression_path=dataset.expression_matrix_path,
        timepoints_path=extras["timepoints"],
        replicates_path=extras["replicates"],
    )
    steady_samples = 0
    if "steady_state_expression" in extras:
        with extras["steady_state_expression"].open(
            encoding="utf-8", newline=""
        ) as stream:
            steady_samples = len(next(csv.reader(stream, delimiter="\t"))) - 1
    _require(
        steady_samples
        == config["input_profile"]["temporal_fixture"]["steady_state_samples"],
        "Steady-state matrix differs from the frozen recipe",
    )
    context = _temporal_cost_context(
        dataset=dataset, temporal_summary=summary, steady_state_samples=steady_samples
    )
    modes, warnings = _estimate_tool_mode_options(
        tool_id="dyngenie3",
        run_id="dyngenie3_holdout",
        toolspec=toolspec,
        cost_profile=cost,
        execution_mode="global",
        resolved_params=config["params_profile"]["resolved_params"],
        extras_present=set(extras),
        logical_group_count=0,
        dataset=dataset,
        max_cores=threads,
        max_ram_gb=ram_gb,
        output_dir="tools/dyngenie3_holdout",
        requested_threads=threads,
        requested_ram_gb=ram_gb,
        temporal_cost_context=context,
    )
    _require(
        len(modes) == 1 and modes[0].threads == threads and modes[0].ram_gb == ram_gb,
        "Planner did not return the exact requested resource allocation",
    )
    mode = modes[0]
    _require(
        mode.eta_source in {"cost_profile", "cost_profile_extrapolated"},
        f"Planner returned unprofiled {mode.eta_source} for reserved {profile_id}; cannot relabel it: {warnings}; temporal={context}",
    )
    detail = mode.eta_provenance["cost_profile"]
    _require(
        detail["profile_id"] == profile_id
        and detail["size_model"]["method"]
        in {"measured_axis_interpolation", "measured_axis_extrapolation"},
        f"Planner selected {detail['profile_id']}/{detail['size_model']['method']} instead of reserved {profile_id} measured gene axis",
    )
    typical = detail["nearest_runtime_point"]["seconds_p50"] * detail["size_scale"]
    _require(
        0 < typical <= mode.eta_seconds,
        "Core-derived typical/planning predictions are inconsistent",
    )
    return {
        "predicted_typical_seconds": typical,
        "predicted_planning_seconds": mode.eta_seconds,
        "operating_reserve_seconds": mode.eta_seconds * BUDGET_RESERVE,
        "eta_source": mode.eta_source,
        "core_provenance": mode.eta_provenance,
        "warnings": warnings,
        "arguments": {
            "genes": genes,
            "columns": 16,
            "threads": threads,
            "ram_gb": ram_gb,
            "temporal_context": context,
        },
        "typical_definition": "Diagnostic p50 projection using the unchanged core size_scale; the planning duration is the exact ANDREA ETA without an added validation reserve.",
    }


def _input_refs(io: Path, base: Path, evidence) -> list[dict[str, Any]]:
    paths = [
        io / "expression.tsv",
        io / "params.json",
        io / "execution.json",
        *sorted((io / "extra").rglob("*")),
    ]
    return [
        evidence.artifact(
            path,
            base,
            role=(
                "primary"
                if path.name == "expression.tsv" and path.parent == io
                else "shared"
            ),
        )
        for path in paths
        if path.is_file()
    ]


def freeze(args, out: Path, evidence, backend, checks):
    cost = _read(args.cost)
    declarations = reserved_cases()
    selected_ids = {case["profile_id"] for case in declarations}
    profiles = {
        row["profile_id"]: row
        for row in cost.get("profiles", [])
        if row.get("profile_id") in selected_ids
    }
    _require(
        set(profiles) == selected_ids,
        "A reserved hold-out profile is absent from retained costs",
    )
    configs, training, execution_ids = {}, [], set()
    compatibility = getattr(args, "compatibility_receipt", None)
    for profile_id in sorted(profiles):
        profile = profiles[profile_id]
        config = profile["benchmark_config"]
        geometry = config["input_profile"]["temporal_fixture"]
        _require(
            geometry["series_count"] == 2
            and geometry["timepoints_per_series"] == 8
            and geometry.get("sampling") in {"regular", "irregular"}
            and config["input_profile"]["temporal"]["time_unit"] == "hour"
            and config["input_profile"]["temporal"]["sampling_design"] == "longitudinal"
            and config["execution_profile"]["mode"] == "global",
            "Reserved profiles require exact observed-hour longitudinal t8/s2 geometry",
        )
        _require(
            config["provenance"]["image_id"] == args.image,
            "Training image differs from the immutable image",
        )
        kwargs = {"compatibility_receipt": compatibility} if compatibility else {}
        entries = evidence.load_training_observations(
            args.calibration_root, profile, **kwargs
        )
        validate_training_panel(entries, config=config)
        adapted = evidence.adapt_training_observations(entries, out, profile, **kwargs)
        for observation in adapted:
            checks.observation(observation, out)
            expected = evidence.identity_for_config(
                config,
                threads=observation["identity"]["threads"],
                ram_gb=observation["identity"]["ram_gb"],
                profile_id=profile_id,
            )
            _require(
                observation["identity"] == expected,
                "Training source/hardware/recipe strata differ; refusing held-outs",
            )
            _require(
                observation["id"] not in execution_ids,
                "A captured execution cannot train two profile strata",
            )
            execution_ids.add(observation["id"])
        training.extend(adapted)
        configs[profile_id] = config
    shutil.copy2(args.cost, out / "cost-snapshot.json")
    toolspec = _read(HERE / "toolspec.draft.json")
    _write(out / "toolspec-snapshot.json", toolspec)
    source_paths = [
        Path(__file__),
        HERE / "verify_runtime.py",
        HERE / "toolspec.draft.json",
        HERE / "requirements.lock",
        RESERVATION_PATH,
        *sorted((ROOT / "andrea/core/commands/infer_network/commons").glob("*.py")),
        ROOT / "andrea/core/shared/bulk_temporal.py",
        ROOT / "andrea/catalog_inference_tools/schemas/dataset-manifest.schema.json",
        ROOT / "wrappers/validate_cost_predictions.py",
        SCRIPTS / "benchmark_costs.py",
        *sorted((SCRIPTS / "shared").glob("*.py")),
        ROOT / "wrappers/calibration_compatibility.py",
    ]
    if compatibility:
        source_paths.append(compatibility)
    estimator = {
        "schema_version": 1,
        "kind": "frozen_andrea_planner_calls",
        "frozen_at": _utc(),
        "profile_ids": sorted(profiles),
        "implementation_artifacts": [
            evidence.artifact(path, out) for path in source_paths
        ],
        "scope": SCOPE,
        "training_execution_ids": sorted(execution_ids),
        "predictions": {},
        "cost": evidence.artifact(out / "cost-snapshot.json", out),
        "toolspec": evidence.artifact(out / "toolspec-snapshot.json", out),
    }
    cases = []
    for declaration in declarations:
        profile_id, genes, seed = (
            declaration["profile_id"],
            declaration["genes"],
            declaration["seed"],
        )
        config = configs[profile_id]
        geometry = config["input_profile"]["temporal_fixture"]
        for threads, ram in RESOURCES:
            case_id = f"{declaration['id']}_t{threads}_m{ram}"
            io = out / "cases" / case_id / "io"
            backend.prepare_io_dir(
                io,
                backend.SizePoint(genes, 16),
                resolved_params=config["params_profile"]["resolved_params"],
                execution={"mode": "global"},
                seed=seed,
                input_profile=config["input_profile"],
            )
            identity = evidence.identity_for_config(
                config, threads=threads, ram_gb=ram, profile_id=profile_id
            )
            prediction = planner_prediction(
                cost=cost,
                toolspec=toolspec,
                config=config,
                io=io,
                genes=genes,
                threads=threads,
                ram_gb=ram,
                profile_id=profile_id,
            )
            estimator["predictions"][case_id] = prediction
            dimensions = {
                "genes": genes,
                "columns": 16,
                "timepoints_per_series": 8,
                "independent_series": 2,
            }
            if geometry["steady_state_samples"]:
                dimensions["steady_state_samples"] = geometry["steady_state_samples"]
            cases.append(
                {
                    "id": case_id,
                    "reservation_id": declaration["id"],
                    "kind": declaration["kind"],
                    "identity": identity,
                    "dimensions": dimensions,
                    "inputs": _input_refs(io, out, evidence),
                    "template_io": os.path.relpath(io, out),
                    "generator_seed": seed,
                    "predicted_typical_seconds": prediction[
                        "predicted_typical_seconds"
                    ],
                    "predicted_planning_seconds": prediction[
                        "predicted_planning_seconds"
                    ],
                    "operating_reserve_seconds": prediction[
                        "operating_reserve_seconds"
                    ],
                    "max_relative_error": ERROR_LIMIT,
                    "min_planning_coverage": MIN_COVERAGE,
                }
            )
    _write(out / "estimator.json", estimator)
    plan = {
        "schema_version": 1,
        "kind": "dyngenie3_frozen_runtime_predictions",
        "frozen_at": _utc(),
        "image_id": args.image,
        "cost": evidence.artifact(out / "cost-snapshot.json", out),
        "estimator": evidence.artifact(out / "estimator.json", out),
        "training_observations": training,
        "cases": cases,
        "reserved_cases": declarations,
        "scope": SCOPE,
        "planned_run_count": len(cases) * REPEATS,
        "automatic_retries": False,
    }
    _write(out / "prediction-plan.json", plan)
    return plan, configs


def run_observation(
    case, *, repeat, config, args, out, plan_sha, evidence, backend, runtime
):
    run_id = str(uuid.uuid4())
    directory = out / "runs" / case["id"] / f"repeat-{repeat}"
    _require(
        not directory.exists(), "Refusing to overwrite a retained validation attempt"
    )
    shutil.copytree(out / case["template_io"], directory / "io")
    io = directory / "io"
    row = {
        "run_id": run_id,
        "role": "independent_validation",
        "status": "planned",
        "seconds": None,
        "identity": case["identity"],
        "dimensions": case["dimensions"],
        "repeat": repeat,
        "plan_sha256": plan_sha,
        "generator_seed": case["generator_seed"],
        "input_sha256": backend.input_manifest(io),
        "started_at": _utc(),
    }
    receipt = directory / "receipt.json"
    _write(receipt, row)
    try:
        row["external_state"] = runtime.external_state_receipt(args.external_state)
        row["status"] = "running"
        _write(receipt, row)
        status, seconds, error = backend.run_container_once(
            image_tag=args.image,
            io_dir=io,
            threads=case["identity"]["threads"],
            ram_gb=int(case["identity"]["ram_gb"]),
            timeout_s=TIMEOUT,
        )
        row.update(status=status, seconds=seconds, error=error)
        if status == "ok":
            physical_case = {
                "threads": case["identity"]["threads"],
                "resolved_params": config["params_profile"]["resolved_params"],
            }
            row["validation"] = runtime.validate_outputs(
                physical_case, {"execution": _read(io / "execution.json")}, io
            )
    except runtime.ExternalExperimentActive as exc:
        row.update(status="blocked", error=str(exc))
        raise
    except KeyboardInterrupt:
        row.update(
            status="error", error="Operator interrupted validation", interrupted=True
        )
        cidfile = io / ".docker-container.cid"
        if cidfile.is_file():
            container_id = cidfile.read_text().strip()
            if re.fullmatch(r"[a-f0-9]{64}", container_id):
                result = subprocess.run(
                    ["docker", "rm", "--force", container_id],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                row["cleanup"] = {
                    "container_id": container_id,
                    "exit_code": result.returncode,
                }
        raise
    except Exception as exc:
        row.update(status="error", error=str(exc))
    finally:
        row["finished_at"] = _utc()
        _write(receipt, row)
    outputs = [
        evidence.artifact(path, out)
        for path in sorted((io / "out").rglob("*"))
        if path.is_file()
    ]
    return row, {
        "id": run_id,
        "receipt": evidence.artifact(receipt, out),
        "identity": case["identity"],
        "dimensions": case["dimensions"],
        "inputs": _input_refs(io, out, evidence),
        "outputs": outputs,
    }


def campaign(args) -> dict[str, Any]:
    specification = protocol(args.image)
    out = args.out.resolve()
    _require(
        not out.exists(),
        "--out must be a new directory; retained failures are never retried or overwritten automatically",
    )
    out.mkdir(parents=True)
    _write(out / "protocol.json", specification)
    report = {
        "schema_version": 1,
        "status": "planned_unmeasured",
        "image_id": args.image,
        "scope": SCOPE,
        "observed_run_count": 0,
        "successful_run_count": 0,
        "planned_run_count": specification["planned_run_count"],
    }
    _write(out / "prediction-report.json", report)
    if args.plan_only:
        return report
    runtime = _runtime()
    try:
        report["external_state"] = runtime.external_state_receipt(args.external_state)
        evidence, backend, checks = _support()
        plan, configs = freeze(args, out, evidence, backend, checks)
        config = next(iter(configs.values()))
        plan_path = out / "prediction-plan.json"
        plan_sha, estimator_sha = _sha(plan_path), _sha(out / "estimator.json")
        report.update(
            status="running",
            plan="prediction-plan.json",
            estimator="estimator.json",
            plan_sha256=plan_sha,
            estimator_sha256=estimator_sha,
        )
        _write(out / "prediction-report.json", report)
        # Inspect the current host and immutable image after the external gate;
        # source/hardware identity must equal calibration before any held-out run.
        observed_provenance = backend.collect_provenance(
            root=ROOT,
            spec_path=args.cost.parent / "toolspec.json",
            image=args.image,
            seed=config["provenance"]["seed"],
            source_dir=HERE,
        )
        _write(out / "observed-provenance.json", observed_provenance)
        for case in plan["cases"]:
            profile_id = case["identity"]["profile_id"]
            current_config = {**configs[profile_id], "provenance": observed_provenance}
            identity = evidence.identity_for_config(
                current_config,
                threads=case["identity"]["threads"],
                ram_gb=case["identity"]["ram_gb"],
                profile_id=profile_id,
            )
            _require(
                identity == case["identity"],
                "Current image/source/hardware differs from the frozen calibration stratum",
            )
        observations = {
            "plan_sha256": plan_sha,
            "cases": [{"id": case["id"], "observations": []} for case in plan["cases"]],
        }
        _write(out / "observations.json", observations)
        for case, measured in zip(plan["cases"], observations["cases"]):
            for repeat in range(1, REPEATS + 1):
                _require(
                    _sha(plan_path) == plan_sha
                    and _sha(out / "estimator.json") == estimator_sha,
                    "Frozen predictions or estimator changed; refusing another run",
                )
                frozen_estimator = _read(out / "estimator.json")
                for ref in [
                    *frozen_estimator["implementation_artifacts"],
                    frozen_estimator["cost"],
                    frozen_estimator["toolspec"],
                ]:
                    checks.artifact(ref, out)
                row, observation = run_observation(
                    case,
                    repeat=repeat,
                    config=configs[case["identity"]["profile_id"]],
                    args=args,
                    out=out,
                    plan_sha=plan_sha,
                    evidence=evidence,
                    backend=backend,
                    runtime=runtime,
                )
                measured["observations"].append(observation)
                report["observed_run_count"] += 1
                report["successful_run_count"] += int(row["status"] == "ok")
                _write(out / "observations.json", observations)
                _write(out / "prediction-report.json", report)
                _require(
                    row["status"] == "ok",
                    f"Held-out attempt failed; evidence retained without retry: {row.get('error')}",
                )
        validation = checks.validate(plan_path, out / "observations.json")
        _require(
            _sha(plan_path) == plan_sha
            and _sha(out / "estimator.json") == estimator_sha,
            "Frozen predictions or estimator changed during measurement",
        )
        report.update(
            status=(
                "passed"
                if validation["passed"]
                and report["successful_run_count"] == plan["planned_run_count"]
                else "failed"
            ),
            generic_validation=validation,
            limits="Only these frozen cases were checked. Failed thresholds remain failures; no refit or retry was performed.",
            finished_at=_utc(),
        )
        report["estimator_kind"] = "frozen_andrea_planner_calls"
        report["planning_prediction_source"] = (
            "Exact ANDREA ToolPlanItem.eta_seconds; the 25% operating reserve is excluded from acceptance."
        )
    except runtime.ExternalExperimentActive as exc:
        report.update(status="blocked", error=str(exc), finished_at=_utc())
    except (Exception, KeyboardInterrupt) as exc:
        report.update(
            status="failed",
            error=str(exc) or "Operator interrupted validation",
            finished_at=_utc(),
        )
    _write(out / "prediction-report.json", report)
    return report


def _interrupt(signum, _frame):
    raise KeyboardInterrupt(f"Interrupted by {signal.Signals(signum).name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-root", type=Path, required=True)
    parser.add_argument("--cost", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--external-state", type=Path, required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--compatibility-receipt", type=Path)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        report = campaign(args)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "image_id": args.image,
                "report": str(args.out / "prediction-report.json"),
                "observed_run_count": report["observed_run_count"],
            }
        )
    )
    return 0 if report["status"] in {"passed", "planned_unmeasured"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
