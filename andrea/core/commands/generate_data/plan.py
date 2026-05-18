"""Scenario-first planning helpers for generate-data."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Any

from .catalog import _load_simulator_catalog, get_profile_capability
from .cost_planner import apply_simulator_cost_plan, detect_host_ram_gb
from .request import (
    _resolve_native_outputs,
    _resolve_simulator_params,
    collect_simulator_compatibility_rule_issues,
    resolve_simulator_runtime_resources,
    validate_simulator_inputs,
    validate_truth_parameter_requirements,
    validate_simulation_plan_payload,
)
from .scenario import validate_scenario_request
from .selection import evaluate_simulator_for_scenario
from .shared import (
    MAX_SEED_32BIT,
    _load_json_object,
    _stable_seed_base,
    _validate_json_instance,
    _write_json,
)


def _load_simulator_runs_payload(path: Path) -> dict[str, Any]:
    payload = _load_json_object(path, "simulator-runs")
    schemas, _catalog = _load_simulator_catalog()
    _validate_json_instance(
        instance=payload,
        schema=schemas["simulator_runs"],
        label="simulator-runs",
    )
    return payload


def _replicate_seeds(base_seed: int, replicates: int) -> list[int]:
    return [
        ((int(base_seed) - 1 + idx) % MAX_SEED_32BIT) + 1 for idx in range(replicates)
    ]


def _build_simulation_plan_payload(
    *,
    scenario_request_path: Path,
    simulator_runs_path: Path,
    max_parallel_tasks: int | None = None,
    max_cores: int | None = None,
    max_ram_gb: float | None = None,
) -> dict[str, Any]:
    scenario = validate_scenario_request(scenario_request_path)
    simulator_runs_payload = _load_simulator_runs_payload(simulator_runs_path)
    _schemas, catalog = _load_simulator_catalog()

    selected_runs = simulator_runs_payload.get("runs", [])
    run_ids = [str(item.get("run_id", "")) for item in selected_runs]
    duplicated = sorted({run_id for run_id in run_ids if run_ids.count(run_id) > 1})
    if duplicated:
        raise ValueError(
            "Duplicate run_id values are not allowed: " + ", ".join(duplicated)
        )

    resolved_runs: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    seed_offset = 0
    for run_config in selected_runs:
        run_id = str(run_config["run_id"])
        simulator_id = str(run_config["simulator_id"])
        replicates = int(run_config.get("replicates", 0))
        if replicates < 1:
            raise ValueError(f"simulator-runs.runs[{run_id}].replicates must be >= 1")
        if simulator_id not in catalog:
            raise ValueError(f"Unknown simulator_id: {simulator_id}")
        simulator_spec = catalog[simulator_id]
        entry = evaluate_simulator_for_scenario(
            simulator_id=simulator_id,
            spec=simulator_spec,
            scenario=scenario,
        )
        if entry["status"] == "blocked":
            block_messages = [
                str(issue.get("message", "")).strip()
                for issue in entry.get("issues", [])
                if issue.get("severity") == "block"
                and str(issue.get("message", "")).strip()
            ]
            raise ValueError(
                f"Simulator run '{run_id}' is blocked for scenario '{scenario.request_id}': "
                + "; ".join(block_messages)
            )
        resolved_params = _resolve_simulator_params(
            simulator_id=simulator_id,
            user_params=dict(run_config.get("params", {})),
            spec_params=simulator_spec.get("params", {}),
        )
        truth_parameter_errors = validate_truth_parameter_requirements(
            profile_capability=get_profile_capability(
                simulator_spec, scenario.profile
            )
            or {},
            profile=scenario.profile,
            requested_extras=scenario.requested_extras,
            simulator_params=resolved_params,
        )
        if truth_parameter_errors:
            raise ValueError(
                f"Simulator run '{run_id}' has invalid truth output parameters: "
                + "; ".join(truth_parameter_errors)
            )
        profile_capability = get_profile_capability(simulator_spec, scenario.profile)
        if profile_capability is None:
            raise ValueError(
                f"Simulator '{simulator_id}' does not support profile '{scenario.profile}'"
            )
        native_outputs = _resolve_native_outputs(
            simulator_id=simulator_id,
            profile=scenario.profile,
            profile_capability=profile_capability,
            requested_extras=scenario.requested_extras,
            simulator_params=resolved_params,
            raw_native_outputs=run_config.get("native_outputs"),
            label=f"simulator-runs.runs[{run_id}]",
        )
        compatibility_blocks, _compatibility_warnings, compatibility_errors = (
            collect_simulator_compatibility_rule_issues(
                simulator_id=simulator_id,
                simulator_spec=simulator_spec,
                profile=scenario.profile,
                requested_extras=scenario.requested_extras,
                simulator_params=resolved_params,
                native_outputs=native_outputs,
                resolved_input_paths=scenario.resolved_input_paths,
            )
        )
        if compatibility_errors:
            raise ValueError(
                f"Simulator run '{run_id}' has invalid compatibility rules: "
                + "; ".join(compatibility_errors)
            )
        if compatibility_blocks:
            raise ValueError(
                f"Simulator run '{run_id}' is blocked by compatibility rules: "
                + "; ".join(compatibility_blocks)
            )
        input_errors = validate_simulator_inputs(
            simulator_id=simulator_id,
            simulator_spec=simulator_spec,
            profile=scenario.profile,
            requested_extras=scenario.requested_extras,
            simulator_params=resolved_params,
            native_outputs=native_outputs,
            input_ids=set(scenario.inputs),
        )
        if input_errors:
            raise ValueError(
                f"Simulator run '{run_id}' has invalid inputs: "
                + "; ".join(input_errors)
            )
        runtime_resources = resolve_simulator_runtime_resources(
            simulator_id=simulator_id,
            simulator_spec=simulator_spec,
        )
        base_seed = run_config.get("base_seed")
        if base_seed is None:
            if scenario.base_seed is None:
                base_seed = _stable_seed_base(
                    request_id=scenario.request_id,
                    profile=scenario.profile,
                    simulator_id=f"{run_id}|{simulator_id}",
                )
            else:
                base_seed = (
                    (int(scenario.base_seed) - 1 + seed_offset) % MAX_SEED_32BIT
                ) + 1
        seeds = _replicate_seeds(int(base_seed), replicates)
        seed_offset += replicates
        resolved_runs.append(
            {
                "run_id": run_id,
                "simulator_id": simulator_id,
                "simulator_params": resolved_params,
                "runtime_resources": runtime_resources,
                "replicates": replicates,
                "native_outputs": native_outputs,
                "base_seed": int(base_seed),
                "replicate_seeds": seeds,
                **({"notes": run_config["notes"]} if run_config.get("notes") else {}),
            }
        )
        for replicate_index, seed in enumerate(seeds, start=1):
            task_id = f"{run_id}__r{replicate_index:02d}"
            tasks.append(
                {
                    "task_id": task_id,
                    "run_id": run_id,
                    "simulator_id": simulator_id,
                    "replicate_index": replicate_index,
                    "seed": seed,
                    "dataset_id": f"{scenario.request_id}__{task_id}",
                    "runtime_resources": runtime_resources,
                }
            )

    if max_parallel_tasks is None:
        max_parallel_tasks = multiprocessing.cpu_count()
    max_parallel_tasks = max(1, min(int(max_parallel_tasks), max(1, len(tasks))))
    if max_cores is None:
        max_cores = multiprocessing.cpu_count()
    max_cores = max(1, int(max_cores))
    if max_ram_gb is None:
        max_ram_gb = detect_host_ram_gb()
    max_ram_gb = max(1.0, float(max_ram_gb))

    resolved_runs, tasks, execution = apply_simulator_cost_plan(
        runs=resolved_runs,
        tasks=tasks,
        catalog=catalog,
        scenario_profile=scenario.profile,
        requested_extras=scenario.requested_extras,
        effective_extras=scenario.effective_extras,
        input_ids=set(scenario.inputs),
        max_parallel_tasks=max_parallel_tasks,
        max_cores=max_cores,
        max_ram_gb=max_ram_gb,
    )

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "id": scenario.request_id,
        "profile": scenario.profile,
        "organism": dict(scenario.organism),
        "requested_extras": list(scenario.requested_extras),
        "effective_extras": list(scenario.effective_extras),
        "inputs": {
            key: {**scenario.inputs[key], "path": str(path)}
            for key, path in sorted(scenario.resolved_input_paths.items())
        },
        "runs": resolved_runs,
        "tasks": tasks,
        "execution": execution,
        "base_seed": scenario.base_seed,
    }
    if scenario.notes:
        payload["notes"] = scenario.notes
    return payload


def plan_generate_data_request(
    *,
    scenario_request_path: Path,
    simulator_runs_path: Path,
    output_path: Path,
    max_parallel_tasks: int | None = None,
    max_cores: int | None = None,
    max_ram_gb: float | None = None,
) -> Path:
    payload = _build_simulation_plan_payload(
        scenario_request_path=scenario_request_path,
        simulator_runs_path=simulator_runs_path,
        max_parallel_tasks=max_parallel_tasks,
        max_cores=max_cores,
        max_ram_gb=max_ram_gb,
    )
    validate_simulation_plan_payload(payload, base_dir=output_path.resolve().parent)
    _write_json(output_path, payload)
    return output_path
