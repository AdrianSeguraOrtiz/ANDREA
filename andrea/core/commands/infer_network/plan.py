"""Phase B: Planning for infer-network.

Phase dependencies:
1. Preflight report (or run preflight inline if not provided).
2. Cost profile loading + mode estimation per requested run.
3. Planner selection (auto/cp_sat/heuristic) and wave construction.
4. Frozen run_dir artifact and metadata persistence.
"""

from __future__ import annotations

import multiprocessing
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rich import print

from andrea.core.shared.paths import report_path

from .commons.artifacts import (
    _build_input_fingerprints,
    _deserialize_dataset_context,
    _materialize_frozen_inputs,
)
from .commons.catalog import _load_schema_constraints, _resolve_catalog_paths
from .commons.custom_tools import load_custom_tool_registry, serialize_custom_tools
from .commons.dataset import _load_groups_by_column, _read_expression_axes
from .commons.planner import (
    _estimate_tool_mode_options,
    _load_tool_cost_profile,
    _optimize_mode_selection,
    _optimize_mode_selection_cp_sat,
)
from .commons.shared import (
    DEFAULT_OUTPUT_DIR,
    DatasetContext,
    _detect_host_ram_gb,
    _slugify_token,
    _task_eta_note,
    _write_json,
)
from .commons.tools import (
    _default_execution_mode,
    _load_toolspec,
    _parse_execution_capabilities,
)
from .preflight import preflight_infer_network

PLAN_SCHEMA_VERSION = "1.3"
_CELL_NATIVE_DENSE_EDGE_WARNING_THRESHOLD = 10_000_000


def _estimated_dense_cell_edges(*, dataset: DatasetContext) -> int:
    return max(0, int(dataset.genes) * (int(dataset.genes) - 1)) * max(
        0, int(dataset.columns)
    )


def _group_aggregation_eta_seconds(
    *,
    dataset: DatasetContext,
    group_count: int,
) -> float:
    dense_cell_edges = _estimated_dense_cell_edges(dataset=dataset)
    return round(max(1.0, (0.0000001 * dense_cell_edges) + (0.05 * group_count)), 3)


def _with_group_aggregation_eta(
    *,
    item: Any,
    dataset: DatasetContext,
    group_count: int,
) -> Any:
    aggregation_eta = _group_aggregation_eta_seconds(
        dataset=dataset,
        group_count=group_count,
    )
    eta_provenance = dict(item.eta_provenance or {"eta_source": item.eta_source})
    eta_provenance["group_aggregation"] = {
        "rule": "mean_signed_effect_by_group",
        "eta_seconds": aggregation_eta,
        "group_count": int(group_count),
        "estimated_dense_cell_edges": _estimated_dense_cell_edges(dataset=dataset),
    }
    cost_features = dict(eta_provenance.get("cost_features") or {})
    if cost_features:
        cost_features["upstream_cost_execution_mode"] = cost_features.get(
            "execution_mode", "cell_native"
        )
        cost_features["execution_mode"] = "group_aggregated"
        cost_features["n_groups"] = int(group_count)
        cost_features["expected_contexts"] = max(1, int(group_count))
        cost_features["aggregation_step"] = "cell_to_group"
        cost_features["output_density_class"] = "dense"
        eta_provenance["cost_features"] = cost_features
        if isinstance(eta_provenance.get("cost_profile"), dict):
            eta_provenance["cost_profile"]["cost_features"] = dict(cost_features)
    return replace(
        item,
        eta_seconds=round(float(item.eta_seconds) + aggregation_eta, 3),
        eta_provenance=eta_provenance,
    )


def _append_cell_native_output_warning(
    *,
    warnings: list[str],
    run_id: str,
    execution_mode: str,
    dataset: DatasetContext,
) -> None:
    if execution_mode not in {"cell_native", "group_aggregated"}:
        return
    dense_cell_edges = _estimated_dense_cell_edges(dataset=dataset)
    if dense_cell_edges < _CELL_NATIVE_DENSE_EDGE_WARNING_THRESHOLD:
        return
    warnings.append(
        f"[{run_id}] execution.mode={execution_mode} may emit a large per-cell "
        f"network ({dataset.columns} cells, {dataset.genes} genes; dense upper "
        f"bound {dense_cell_edges} cell-edge rows)."
    )


def plan_infer_network(
    *,
    dataset_manifest_path: Path,
    tools_params_path: Path,
    custom_tools_path: Optional[Path] = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    max_cores: int = multiprocessing.cpu_count(),
    max_ram_gb: Optional[float] = None,
    planner: str = "auto",
    planner_time_limit_seconds: float = 100.0,
    preflight_report: Optional[dict[str, Any]] = None,
) -> Path:
    if max_cores < 1:
        raise ValueError("max_cores must be >= 1")
    host_ram = _detect_host_ram_gb()
    effective_ram = host_ram if max_ram_gb is None else min(float(max_ram_gb), host_ram)
    if effective_ram <= 0:
        raise ValueError("max_ram_gb must be > 0")

    planner_mode = str(planner).strip().lower().replace("-", "_")
    if planner_mode not in {"auto", "heuristic", "cp_sat"}:
        raise ValueError("planner must be one of: auto, heuristic, cp_sat")
    if planner_time_limit_seconds <= 0:
        raise ValueError("planner_time_limit_seconds must be > 0")

    if preflight_report is None:
        preflight_report = preflight_infer_network(
            dataset_manifest_path=dataset_manifest_path,
            tools_params_path=tools_params_path,
            custom_tools_path=custom_tools_path,
        )

    dataset_payload = preflight_report.get("dataset", {})
    if not isinstance(dataset_payload, dict):
        raise ValueError("preflight_report.dataset is invalid")
    dataset = _deserialize_dataset_context(dataset_payload)

    tools_root, schemas_dir = _resolve_catalog_paths()
    constraints = _load_schema_constraints(schemas_dir)
    custom_tools, _custom_aliases, _custom_blocked_entries = load_custom_tool_registry(
        custom_tools_path=custom_tools_path,
        tools_root=tools_root,
        constraints=constraints,
    )

    warnings: list[str] = []
    runs_payload = preflight_report.get("runs", {})
    if not isinstance(runs_payload, dict):
        raise ValueError("preflight_report.runs is invalid")
    selected_tools = [x for x in runs_payload.get("selected", []) if isinstance(x, str)]
    selected_tool_catalog_ids = {
        str(k): str(v)
        for k, v in runs_payload.get("catalog_tool_ids", {}).items()
        if isinstance(k, str) and isinstance(v, str)
    }
    selected_tool_origins = {
        str(k): str(v)
        for k, v in runs_payload.get("tool_origins", {}).items()
        if isinstance(k, str) and isinstance(v, str)
    }
    resolved_params_by_tool = {
        str(k): v
        for k, v in runs_payload.get("resolved_params", {}).items()
        if isinstance(k, str) and isinstance(v, dict)
    }
    resolved_execution_by_tool = {
        str(k): v
        for k, v in runs_payload.get("resolved_execution", {}).items()
        if isinstance(k, str) and isinstance(v, dict)
    }
    run_issues = {
        str(k): [item for item in v if isinstance(item, dict)]
        for k, v in runs_payload.get("issues", {}).items()
        if isinstance(k, str) and isinstance(v, list)
    }
    skipped_tools = {
        str(k): str(v)
        for k, v in runs_payload.get("skipped", {}).items()
        if isinstance(k, str)
    }

    if not selected_tools:
        refreshed_preflight = preflight_infer_network(
            dataset_manifest_path=dataset_manifest_path,
            tools_params_path=tools_params_path,
            custom_tools_path=custom_tools_path,
        )
        preflight_report = refreshed_preflight
        dataset_payload = refreshed_preflight.get("dataset", {})
        if not isinstance(dataset_payload, dict):
            raise ValueError("refreshed preflight report has invalid dataset payload")
        dataset = _deserialize_dataset_context(dataset_payload)
        warnings = []
        runs_payload = refreshed_preflight.get("runs", {})
        if not isinstance(runs_payload, dict):
            raise ValueError("refreshed preflight report has invalid runs payload")
        selected_tools = [
            x for x in runs_payload.get("selected", []) if isinstance(x, str)
        ]
        selected_tool_catalog_ids = {
            str(k): str(v)
            for k, v in runs_payload.get("catalog_tool_ids", {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        selected_tool_origins = {
            str(k): str(v)
            for k, v in runs_payload.get("tool_origins", {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        resolved_params_by_tool = {
            str(k): v
            for k, v in runs_payload.get("resolved_params", {}).items()
            if isinstance(k, str) and isinstance(v, dict)
        }
        resolved_execution_by_tool = {
            str(k): v
            for k, v in runs_payload.get("resolved_execution", {}).items()
            if isinstance(k, str) and isinstance(v, dict)
        }
        run_issues = {
            str(k): [item for item in v if isinstance(item, dict)]
            for k, v in runs_payload.get("issues", {}).items()
            if isinstance(k, str) and isinstance(v, list)
        }
        skipped_tools = {
            str(k): str(v)
            for k, v in runs_payload.get("skipped", {}).items()
            if isinstance(k, str)
        }
        if not selected_tools:
            raise ValueError(
                "No compatible tools available after validation. "
                "Check tools_params.json and dataset/tool compatibility."
            )

    blocking_run_issues = {
        run_id: [
            str(issue.get("message", "")).strip()
            for issue in issues
            if issue.get("severity") == "block"
            and str(issue.get("message", "")).strip()
        ]
        for run_id, issues in run_issues.items()
    }
    blocking_run_issues = {
        run_id: messages for run_id, messages in blocking_run_issues.items() if messages
    }
    if blocking_run_issues:
        error_lines: list[str] = []
        for run_id in sorted(blocking_run_issues):
            for message in blocking_run_issues[run_id]:
                error_lines.append(f"[{run_id}] {message}")
        raise ValueError(
            "Planning blocked by preflight issues:\n" + "\n".join(error_lines)
        )

    catalog_toolspec_by_run: dict[str, dict[str, Any]] = {}
    for run_id in selected_tools:
        catalog_tool_id = selected_tool_catalog_ids.get(run_id, "").strip()
        if not catalog_tool_id:
            raise ValueError(
                f"preflight report is missing catalog mapping for run '{run_id}'"
            )
        toolspec = (
            custom_tools[catalog_tool_id]
            if catalog_tool_id in custom_tools
            else _load_toolspec(tools_root, catalog_tool_id)
        )
        catalog_toolspec_by_run[run_id] = toolspec

    group_order: list[str] = []
    group_to_columns: dict[str, list[str]] = {}
    execution_mode_by_run: dict[str, str] = {}
    for run_id in selected_tools:
        resolved_execution = resolved_execution_by_tool.get(run_id, {})
        execution_mode = str(resolved_execution.get("mode", "")).strip()
        if not execution_mode:
            capabilities = _parse_execution_capabilities(
                tool_id=run_id,
                toolspec=catalog_toolspec_by_run[run_id],
            )
            execution_mode = _default_execution_mode(capabilities)
        if execution_mode:
            execution_mode_by_run[run_id] = execution_mode

    needs_group_partition = any(
        execution_mode_by_run.get(run_id) == "group_emulated"
        for run_id in selected_tools
    )
    needs_group_assignment = any(
        execution_mode_by_run.get(run_id) in {"group_emulated", "group_aggregated"}
        for run_id in selected_tools
    )
    needs_group_count = any(
        execution_mode_by_run.get(run_id)
        in {"group_native", "group_emulated", "group_aggregated"}
        for run_id in selected_tools
    )
    groups_path = dataset.extras.get("groups")
    if needs_group_assignment and groups_path is None:
        raise ValueError(
            "Planning requires groups.tsv because at least one run uses execution.mode=group_emulated or execution.mode=group_aggregated."
        )
    if groups_path is not None and (needs_group_partition or needs_group_count):
        _expression_genes, expression_columns = _read_expression_axes(
            dataset.expression_matrix_path
        )
        group_order, group_to_columns = _load_groups_by_column(
            groups_path=groups_path,
            expression_columns=expression_columns,
        )

    mode_options_by_tool: dict[str, list[Any]] = {}
    logical_run_specs: dict[str, dict[str, Any]] = {}
    extras_present = {
        input_key for input_key, path in dataset.extras.items() if path is not None
    }
    for run_id in selected_tools:
        catalog_tool_id = selected_tool_catalog_ids[run_id]
        tool_origin = selected_tool_origins.get(
            run_id,
            "custom" if catalog_tool_id in custom_tools else "catalog",
        )
        toolspec = catalog_toolspec_by_run[run_id]
        execution_capabilities = _parse_execution_capabilities(
            tool_id=run_id,
            toolspec=toolspec,
        )
        resolved_execution = resolved_execution_by_tool.get(run_id, {})
        execution_mode = str(resolved_execution.get("mode", "")).strip()
        if not execution_mode:
            execution_mode = _default_execution_mode(execution_capabilities)

        if tool_origin == "custom":
            cost_profile = None
            cost_warnings = [
                f"[{catalog_tool_id}] no cost.json for external Docker tool; fallback estimation will be used."
            ]
        else:
            cost_profile, cost_warnings = _load_tool_cost_profile(
                tools_root=tools_root,
                tool_id=catalog_tool_id,
            )
        warnings.extend(cost_warnings)
        _append_cell_native_output_warning(
            warnings=warnings,
            run_id=run_id,
            execution_mode=execution_mode,
            dataset=dataset,
        )

        physical_tasks: list[dict[str, Any]] = []
        if execution_mode == "group_emulated":
            for idx, group_label in enumerate(group_order, start=1):
                group_slug = _slugify_token(group_label)
                physical_task_id = f"{run_id}__group_{idx:02d}_{group_slug}"
                task_output_dir = f"tools/{run_id}/subruns/{idx:02d}_{group_slug}"
                group_columns = group_to_columns[group_label]
                group_dataset = DatasetContext(
                    dataset_id=dataset.dataset_id,
                    column_kind=dataset.column_kind,
                    expression_profile=dataset.expression_profile,
                    taxonomic_group=dataset.taxonomic_group,
                    ncbi_taxon_id=dataset.ncbi_taxon_id,
                    genes=dataset.genes,
                    columns=len(group_columns),
                    expression_matrix_path=dataset.expression_matrix_path,
                    extras=dataset.extras,
                )
                mode_options, plan_warnings = _estimate_tool_mode_options(
                    tool_id=physical_task_id,
                    run_id=run_id,
                    toolspec=toolspec,
                    cost_profile=cost_profile,
                    execution_mode=execution_mode,
                    resolved_params=resolved_params_by_tool.get(run_id, {}),
                    extras_present=extras_present,
                    logical_group_count=len(group_order),
                    physical_tasks_total=len(group_order),
                    dataset=group_dataset,
                    max_cores=max_cores,
                    max_ram_gb=effective_ram,
                    output_dir=task_output_dir,
                    group_label=group_label,
                )
                warnings.extend(plan_warnings)
                if tool_origin == "custom":
                    mode_options = [
                        replace(item, network_disabled=True) for item in mode_options
                    ]
                mode_options_by_tool[physical_task_id] = mode_options
                physical_tasks.append(
                    {
                        "task_id": physical_task_id,
                        "group_label": group_label,
                        "columns": len(group_columns),
                        "output_dir": task_output_dir,
                    }
                )
        else:
            physical_task_id = (
                f"{run_id}__cell_native"
                if execution_mode == "group_aggregated"
                else run_id
            )
            task_output_dir = (
                f"tools/{run_id}/upstream_cell_native"
                if execution_mode == "group_aggregated"
                else f"tools/{run_id}"
            )
            cost_execution_mode = (
                "cell_native" if execution_mode == "group_aggregated" else execution_mode
            )
            mode_options, plan_warnings = _estimate_tool_mode_options(
                tool_id=physical_task_id,
                run_id=run_id,
                toolspec=toolspec,
                cost_profile=cost_profile,
                execution_mode=cost_execution_mode,
                resolved_params=resolved_params_by_tool.get(run_id, {}),
                extras_present=extras_present,
                logical_group_count=(
                    len(group_order)
                    if execution_mode == "group_native"
                    and group_order
                    else (0 if execution_mode == "global" else None)
                ),
                physical_tasks_total=1,
                dataset=dataset,
                max_cores=max_cores,
                max_ram_gb=effective_ram,
                output_dir=task_output_dir,
            )
            warnings.extend(plan_warnings)
            if tool_origin == "custom":
                mode_options = [
                    replace(item, network_disabled=True) for item in mode_options
                ]
            if execution_mode == "group_aggregated":
                group_count = len(group_order)
                mode_options = [
                    _with_group_aggregation_eta(
                        item=item,
                        dataset=dataset,
                        group_count=group_count,
                    )
                    for item in mode_options
                ]
            mode_options_by_tool[physical_task_id] = mode_options
            physical_tasks.append(
                {
                    "task_id": physical_task_id,
                    "group_label": None,
                    "columns": dataset.columns,
                    "output_dir": task_output_dir,
                    **(
                        {"postprocess": "group_aggregated_mean_signed_effect"}
                        if execution_mode == "group_aggregated"
                        else {}
                    ),
                }
            )

        logical_run_specs[run_id] = {
            "run_id": run_id,
            "tool_id": catalog_tool_id,
            "tool_origin": tool_origin,
            "execution": {**resolved_execution, "mode": execution_mode},
            "physical_tasks": physical_tasks,
        }

    planner_used = "heuristic"
    planning_result = None
    if planner_mode in {"auto", "cp_sat"}:
        planning_result = _optimize_mode_selection_cp_sat(
            mode_options_by_tool=mode_options_by_tool,
            max_cores=max_cores,
            max_ram_gb=effective_ram,
            time_limit_seconds=planner_time_limit_seconds,
            warnings=warnings,
        )
        if planning_result is not None:
            planner_used = "cp_sat"
    if planning_result is None:
        planning_result = _optimize_mode_selection(
            mode_options_by_tool=mode_options_by_tool,
            max_cores=max_cores,
            max_ram_gb=effective_ram,
        )
        if planner_mode == "cp_sat":
            warnings.append("[planner=cp_sat] fallback heuristic planner was used.")

    _selected_modes, waves, total_eta = planning_result
    if planner_mode == "auto":
        print(f"planner: auto -> {planner_used}")
    else:
        print(f"planner: requested={planner_mode}, used={planner_used}")

    run_id = (
        f"{dataset.dataset_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    run_dir = output_dir.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    tools_dir = run_dir / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)

    for tool_id, resolved in resolved_params_by_tool.items():
        tool_out = tools_dir / tool_id
        tool_out.mkdir(parents=True, exist_ok=True)
        _write_json(tool_out / "resolved_params.json", resolved)
        _write_json(
            tool_out / "resolved_execution.json",
            resolved_execution_by_tool.get(tool_id, {}),
        )

    custom_tools_payload = serialize_custom_tools(custom_tools) if custom_tools else None
    frozen_manifest, frozen_tools_params, frozen_custom_tools, frozen_expression, frozen_extras = (
        _materialize_frozen_inputs(
            run_dir=run_dir,
            dataset_manifest_path=dataset_manifest_path,
            tools_params_path=tools_params_path,
            custom_tools_payload=custom_tools_payload,
            dataset=dataset,
            constraints=constraints,
        )
    )
    input_fingerprints = _build_input_fingerprints(
        run_dir=run_dir,
        frozen_manifest=frozen_manifest,
        frozen_tools_params=frozen_tools_params,
        frozen_custom_tools=frozen_custom_tools,
        frozen_expression=frozen_expression,
        frozen_extras=frozen_extras,
    )

    plan_generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    plan_waves = []
    selected_tasks_by_id: dict[str, dict[str, Any]] = {}
    eta_cursor = 0.0
    for wave in waves:
        wave_start = round(eta_cursor, 3)
        wave_end = round(eta_cursor + wave.eta_seconds, 3)
        eta_cursor = wave_end
        tasks_payload = []
        for task in wave.tasks:
            task_payload = asdict(task)
            if task_payload.get("eta_provenance") is None:
                task_payload.pop("eta_provenance", None)
            note = _task_eta_note(task.eta_source)
            if note is not None:
                task_payload["note"] = note
            task_eta_start = wave_start
            task_eta_end = round(wave_start + task.eta_seconds, 3)
            task_payload["eta_start_seconds"] = task_eta_start
            task_payload["eta_end_seconds"] = task_eta_end
            tasks_payload.append(task_payload)
            selected_tasks_by_id[task.tool_id] = {
                "task": task,
                "eta_start_seconds": task_eta_start,
                "eta_end_seconds": task_eta_end,
            }
        plan_waves.append(
            {
                "index": wave.index,
                "threads_used": wave.threads_used,
                "ram_gb_used": wave.ram_gb_used,
                "eta_seconds": wave.eta_seconds,
                "eta_start_seconds": wave_start,
                "eta_end_seconds": wave_end,
                "tasks": tasks_payload,
            }
        )

    logical_runs_payload: list[dict[str, Any]] = []
    for run_id in selected_tools:
        logical_spec = logical_run_specs[run_id]
        physical_tasks_payload: list[dict[str, Any]] = []
        logical_starts: list[float] = []
        logical_ends: list[float] = []
        for raw_physical in logical_spec["physical_tasks"]:
            task_id = str(raw_physical["task_id"])
            scheduled = selected_tasks_by_id.get(task_id)
            if scheduled is None:
                raise ValueError(
                    f"Planner did not select a mode for physical task '{task_id}'"
                )
            task = scheduled["task"]
            task_start = float(scheduled["eta_start_seconds"])
            task_end = float(scheduled["eta_end_seconds"])
            logical_starts.append(task_start)
            logical_ends.append(task_end)
            task_payload = {
                "task_id": task_id,
                "group_label": raw_physical["group_label"],
                "columns": int(raw_physical["columns"]),
                "output_dir": str(raw_physical["output_dir"]),
                "threads": int(task.threads),
                "ram_gb": round(float(task.ram_gb), 3),
                "eta_seconds": round(float(task.eta_seconds), 3),
                "eta_source": str(task.eta_source),
                "eta_start_seconds": round(task_start, 3),
                "eta_end_seconds": round(task_end, 3),
            }
            if task.eta_provenance is not None:
                task_payload["eta_provenance"] = task.eta_provenance
            note = _task_eta_note(task.eta_source)
            if note is not None:
                task_payload["note"] = note
            if raw_physical.get("postprocess"):
                task_payload["postprocess"] = str(raw_physical["postprocess"])
            physical_tasks_payload.append(task_payload)

        logical_eta_start = round(min(logical_starts), 3) if logical_starts else 0.0
        logical_eta_end = round(max(logical_ends), 3) if logical_ends else 0.0
        logical_runs_payload.append(
            {
                "run_id": run_id,
                "tool_id": logical_spec["tool_id"],
                "tool_origin": logical_spec.get("tool_origin", "catalog"),
                "execution": logical_spec["execution"],
                "physical_tasks_total": len(physical_tasks_payload),
                "eta_start_seconds": logical_eta_start,
                "eta_end_seconds": logical_eta_end,
                "eta_seconds": round(logical_eta_end - logical_eta_start, 3),
                "physical_tasks": physical_tasks_payload,
            }
        )

    planning_warnings = list(dict.fromkeys(warnings))
    plan_payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "generated_at_utc": plan_generated_at,
        "run_id": run_id,
        "planner": {
            "requested": planner_mode,
            "used": planner_used,
            "cp_sat_time_limit_seconds": float(planner_time_limit_seconds),
        },
        "resource_limits": {
            "max_cores": int(max_cores),
            "max_ram_gb": round(float(effective_ram), 3),
        },
        "totals": {
            "logical_runs_total": int(len(selected_tools)),
            "physical_tasks_total": int(sum(len(w.tasks) for w in waves)),
            "tasks_total": int(sum(len(w.tasks) for w in waves)),
            "waves_total": int(len(waves)),
            "threads_peak": int(max((w.threads_used for w in waves), default=0)),
            "ram_peak_gb": round(
                float(max((w.ram_gb_used for w in waves), default=0.0)), 3
            ),
        },
        "runs": logical_runs_payload,
        "waves": plan_waves,
        "eta_total_seconds": total_eta,
        "warnings": planning_warnings,
        "input_fingerprints": input_fingerprints,
    }
    _write_json(run_dir / "plan.json", plan_payload)
    _write_json(run_dir / "preflight_report.json", preflight_report)

    planned_tool_origins = {
        str(run["run_id"]): str(run.get("tool_origin", "catalog"))
        for run in logical_runs_payload
        if isinstance(run, dict) and run.get("run_id")
    }
    report_payload = {
        "run_id": run_id,
        "status": "planned",
        "inputs": {
            "dataset_manifest_path": report_path(frozen_manifest, base_dir=run_dir),
            "tools_params_path": report_path(frozen_tools_params, base_dir=run_dir),
            **(
                {
                    "custom_tools_path": report_path(
                        frozen_custom_tools,
                        base_dir=run_dir,
                    )
                }
                if frozen_custom_tools is not None
                else {}
            ),
        },
        "dataset": {
            "id": dataset.dataset_id,
            "column_kind": dataset.column_kind,
            "expression_profile": dataset.expression_profile,
            "genes": dataset.genes,
            "columns": dataset.columns,
            "expression_matrix_path": report_path(frozen_expression, base_dir=run_dir),
        },
        "tools": {
            "selected": selected_tools,
            "catalog_tool_ids": selected_tool_catalog_ids,
            "tool_origins": planned_tool_origins,
            "skipped": skipped_tools,
            "status_by_tool": {tool_id: "pending" for tool_id in selected_tools},
            "completed": [],
            "failed": {},
            "results": {},
        },
        "outputs": {
            "merged_network_raw": None,
            "merged_network_normalized": None,
            "rows_per_tool": {},
        },
        "issues": [],
        "execution": {
            "elapsed_seconds": 0.0,
            "planner_requested": planner_mode,
            "planner_used": planner_used,
            "planner_time_limit_seconds": float(planner_time_limit_seconds),
            "waves_total": len(waves),
            "tools_selected": len(selected_tools),
            "physical_tasks_total": int(sum(len(w.tasks) for w in waves)),
            "tools_completed": 0,
            "tools_failed": 0,
        },
        "plan_file": report_path(run_dir / "plan.json", base_dir=run_dir),
        "notes": [
            "Run directory is frozen at planning time.",
            "Use run_infer_network_plan(run_dir=...) to execute this plan.",
        ],
    }
    _write_json(run_dir / "run_report.json", report_payload)

    print(f"infer-network planning completed: {run_dir}")
    print(f"  selected tools: {len(selected_tools)}")
    print(f"  skipped tools: {len(skipped_tools)}")
    print(f"  waves: {len(waves)}")
    print(f"  estimated total time: {total_eta:.2f}s")
    if warnings:
        print(f"  warnings: {len(warnings)} (see run_report.json)")

    return run_dir
