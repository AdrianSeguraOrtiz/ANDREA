"""Phase C: Execution for infer-network.

Phase dependencies:
1. Planned run_dir integrity checks.
2. Runtime IO prep + Docker wave execution.
3. Merge and normalization of logical-run outputs.
4. Final run_report persistence.
"""

from __future__ import annotations

import csv
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rich import print

from andrea.core.shared.issues import issue_messages, make_issue
from andrea.core.shared.paths import report_path as _report_path

from .commons.artifacts import _load_plan_waves, _verify_input_fingerprints
from .commons.catalog import _load_schema_constraints, _resolve_catalog_paths
from .commons.dataset import (
    _load_groups_by_column,
    _parse_dataset_context,
    _read_expression_axes,
)
from .commons.merge import (
    _merge_network_outputs,
    _read_network_rows,
    _write_network_rows,
)
from .commons.network_exports import (
    export_cytoscape_style_script,
    export_network_gexf,
    export_network_graphml,
)
from .commons.runtime_helpers import (
    _ensure_docker_cli,
    _prepare_shared_inputs,
    _prepare_tool_runtime_io,
    _run_wave,
)
from .commons.shared import (
    ToolExecutionResult,
    _load_json_object,
    _slugify_token,
    _write_json,
)
from .commons.tools import (
    _collect_compatibility_rule_issues,
    _collect_conditional_input_issues,
    _load_toolspec,
)

_REPORT_PATH_KEYS = {"network_path", "progress_path", "logs_path"}


def _relativize_result_payload(payload: Any, *, base_dir: Path) -> Any:
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if key in _REPORT_PATH_KEYS and value is not None:
                out[key] = _report_path(Path(str(value)), base_dir=base_dir)
            else:
                out[key] = _relativize_result_payload(value, base_dir=base_dir)
        return out
    if isinstance(payload, list):
        return [_relativize_result_payload(item, base_dir=base_dir) for item in payload]
    return payload


def _load_logical_runs_from_plan(
    plan_payload: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    raw_runs = plan_payload.get("runs", [])
    if not isinstance(raw_runs, list):
        raise ValueError("plan.json.runs must be an array")

    logical_runs: dict[str, dict[str, Any]] = {}
    for idx, raw_run in enumerate(raw_runs, start=1):
        if not isinstance(raw_run, dict):
            raise ValueError(f"plan.json.runs[{idx}] must be an object")
        run_id = str(raw_run.get("run_id", "")).strip()
        tool_id = str(raw_run.get("tool_id", "")).strip()
        execution = raw_run.get("execution", {})
        execution_mode = (
            str(execution.get("mode", "")).strip()
            if isinstance(execution, dict)
            else ""
        )
        physical_tasks = raw_run.get("physical_tasks", [])
        if (
            not run_id
            or not tool_id
            or execution_mode not in {"global", "group_native", "group_emulated"}
            or not isinstance(execution, dict)
            or not isinstance(physical_tasks, list)
            or not physical_tasks
        ):
            raise ValueError(f"plan.json.runs[{idx}] is invalid")
        logical_runs[run_id] = {
            "run_id": run_id,
            "tool_id": tool_id,
            "execution": execution,
            "physical_tasks": physical_tasks,
        }
    return logical_runs


def _write_expression_subset(
    *,
    source_path: Path,
    output_path: Path,
    selected_columns: list[str],
) -> None:
    selected = set(selected_columns)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        source_path.open("r", encoding="utf-8", newline="") as src,
        output_path.open("w", encoding="utf-8", newline="") as dst,
    ):
        reader = csv.reader(src, delimiter="\t")
        writer = csv.writer(dst, delimiter="\t", lineterminator="\n")
        header = next(reader, None)
        if header is None or len(header) < 2:
            raise ValueError(
                f"Expression matrix must have header with at least 2 columns: {source_path}"
            )
        keep_indices = [0] + [
            idx
            for idx, value in enumerate(header[1:], start=1)
            if str(value).strip() in selected
        ]
        if len(keep_indices) <= 1:
            raise ValueError(
                f"No expression columns matched selected group subset for {output_path}"
            )
        writer.writerow([header[idx] for idx in keep_indices])
        for row in reader:
            if not row:
                continue
            writer.writerow([row[idx] for idx in keep_indices])


def _prepare_group_expression_sources(
    *,
    run_dir: Path,
    shared_expression: Path,
    groups_path: Path,
    required_group_labels: set[str],
) -> dict[str, Path]:
    _genes, expression_columns = _read_expression_axes(shared_expression)
    group_order, group_to_columns = _load_groups_by_column(
        groups_path=groups_path,
        expression_columns=expression_columns,
    )

    shared_group_dir = run_dir / "shared" / "groups"
    shared_group_dir.mkdir(parents=True, exist_ok=True)
    prepared: dict[str, Path] = {}
    for idx, group_label in enumerate(group_order, start=1):
        if group_label not in required_group_labels:
            continue
        slug = _slugify_token(group_label)
        output_path = shared_group_dir / f"{idx:02d}_{slug}" / "expression.tsv"
        if not output_path.exists():
            _write_expression_subset(
                source_path=shared_expression,
                output_path=output_path,
                selected_columns=group_to_columns[group_label],
            )
        prepared[group_label] = output_path
    return prepared


def _finalize_grouped_logical_run(
    *,
    run_dir: Path,
    run_id: str,
    logical_spec: dict[str, Any],
    child_results: dict[str, ToolExecutionResult],
    strict: bool,
    warnings: list[str],
) -> tuple[ToolExecutionResult, dict[str, Any]]:
    tool_dir = run_dir / "tools" / run_id
    out_dir = tool_dir / "io" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out_dir / "progress.json"
    logs_path = tool_dir / "group_execution.log"

    child_payload: dict[str, Any] = {}
    child_failures: list[tuple[str, ToolExecutionResult]] = []
    child_rows: list[dict[str, Any]] = []
    durations: list[float] = []

    for physical in logical_spec["physical_tasks"]:
        task_id = str(physical.get("task_id", "")).strip()
        group_label = str(physical.get("group_label", "")).strip()
        result = child_results.get(task_id)
        if result is None:
            result = ToolExecutionResult(
                tool_id=task_id,
                status="failed",
                exit_code=127,
                duration_seconds=0.0,
                network_path=None,
                progress_path=None,
                logs_path=None,
                error="Internal grouped task result is missing.",
            )
        durations.append(float(result.duration_seconds))
        child_payload[task_id] = {
            **asdict(result),
            "group_label": group_label,
            "output_dir": str(physical.get("output_dir", "")),
        }
        if result.status == "completed" and result.network_path:
            try:
                rows = _read_network_rows(Path(result.network_path), tool_id=task_id)
            except Exception as exc:  # noqa: BLE001
                child_failures.append(
                    (
                        group_label,
                        ToolExecutionResult(
                            tool_id=task_id,
                            status="failed",
                            exit_code=result.exit_code,
                            duration_seconds=result.duration_seconds,
                            network_path=result.network_path,
                            progress_path=result.progress_path,
                            logs_path=result.logs_path,
                            error=str(exc),
                        ),
                    )
                )
                child_payload[task_id]["status"] = "failed"
                child_payload[task_id]["error"] = str(exc)
                continue
            for row in rows:
                row["context"] = f"group:{group_label}"
            child_rows.extend(rows)
            continue
        child_failures.append((group_label, result))

    network_path: str | None = None
    parent_network = out_dir / "network.csv"
    if child_rows:
        _write_network_rows(path=parent_network, rows=child_rows, include_tool_id=False)
        network_path = str(parent_network.resolve())

    failed_groups = [label for label, _result in child_failures]
    if child_failures and child_rows:
        failure_summary = f"{len(child_failures)}/{len(logical_spec['physical_tasks'])} grouped executions failed"
        if failed_groups:
            failure_summary += f" ({', '.join(failed_groups)})"
        warnings.append(f"[{run_id}] {failure_summary}.")
        status = "failed" if strict else "completed"
        error = failure_summary if strict else None
    elif child_failures:
        failure_summary = "All grouped executions failed"
        if failed_groups:
            failure_summary += f" ({', '.join(failed_groups)})"
        status = "failed"
        error = failure_summary
    else:
        status = "completed"
        error = None
        if child_rows:
            network_path = str(parent_network.resolve())
        else:
            _write_network_rows(path=parent_network, rows=[], include_tool_id=False)
            network_path = str(parent_network.resolve())

    progress_payload = {
        "percent": 100,
        "status": status,
        "phase": "done" if status == "completed" else "failed",
        "message": (
            f"{len(logical_spec['physical_tasks']) - len(child_failures)}/{len(logical_spec['physical_tasks'])} grouped executions completed"
        ),
    }
    _write_json(progress_path, progress_payload)

    log_lines = [
        f"run_id={run_id}",
        f"status={status}",
        f"successful_groups={len(logical_spec['physical_tasks']) - len(child_failures)}",
        f"failed_groups={len(child_failures)}",
    ]
    for group_label, result in child_failures:
        reason = str(result.error or f"exit_code={result.exit_code}").strip()
        log_lines.append(f"[group:{group_label}] {reason}")
    logs_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    logical_result = ToolExecutionResult(
        tool_id=run_id,
        status=status,
        exit_code=0 if status == "completed" else 1,
        duration_seconds=round(sum(durations), 3),
        network_path=network_path,
        progress_path=str(progress_path.resolve()),
        logs_path=str(logs_path.resolve()),
        error=error,
    )
    logical_payload = {
        **asdict(logical_result),
        "execution": logical_spec["execution"],
        "physical_tasks_total": len(logical_spec["physical_tasks"]),
        "child_results": child_payload,
    }
    return logical_result, logical_payload


def run_infer_network_plan(
    *,
    run_dir: Path,
    progress_poll_seconds: float = 0.5,
    strict: bool = False,
) -> Path:
    started_at = time.perf_counter()
    if progress_poll_seconds <= 0:
        raise ValueError("progress_poll_seconds must be > 0")

    run_dir = run_dir.resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise ValueError(f"run_dir does not exist or is not a directory: {run_dir}")

    plan_path = run_dir / "plan.json"
    preflight_path = run_dir / "preflight_report.json"
    run_report_path = run_dir / "run_report.json"
    if not plan_path.exists():
        raise ValueError(f"Missing plan.json in run_dir: {plan_path}")
    if not preflight_path.exists():
        raise ValueError(f"Missing preflight_report.json in run_dir: {preflight_path}")
    if not run_report_path.exists():
        raise ValueError(f"Missing run_report.json in run_dir: {run_report_path}")

    plan_payload = _load_json_object(plan_path, "plan")
    preflight_report = _load_json_object(preflight_path, "preflight_report")
    run_report = _load_json_object(run_report_path, "run_report")

    fingerprints = plan_payload.get("input_fingerprints", {})
    if not isinstance(fingerprints, dict) or not fingerprints:
        raise ValueError("plan.json missing input_fingerprints")
    _verify_input_fingerprints(run_dir=run_dir, fingerprints=fingerprints)

    _selected_modes, waves, _total_eta = _load_plan_waves(plan_payload)
    logical_runs = _load_logical_runs_from_plan(plan_payload)

    runs_payload = preflight_report.get("runs", {})
    if not isinstance(runs_payload, dict):
        raise ValueError("Invalid preflight_report.runs")
    selected_tools = [x for x in runs_payload.get("selected", []) if isinstance(x, str)]
    selected_tool_catalog_ids = {
        str(k): str(v)
        for k, v in runs_payload.get("catalog_tool_ids", {}).items()
        if isinstance(k, str) and isinstance(v, str)
    }
    resolved_execution_by_tool = {
        str(k): v
        for k, v in runs_payload.get("resolved_execution", {}).items()
        if isinstance(k, str) and isinstance(v, dict)
    }
    skipped_tools = {
        str(k): str(v)
        for k, v in runs_payload.get("skipped", {}).items()
        if isinstance(k, str)
    }

    tools_root, schemas_dir = _resolve_catalog_paths()
    constraints = _load_schema_constraints(schemas_dir)
    frozen_manifest = run_dir / "input" / "dataset-manifest.json"
    dataset = _parse_dataset_context(
        dataset_manifest_path=frozen_manifest,
        constraints=constraints,
    )

    resolved_params_by_tool: dict[str, dict[str, Any]] = {}
    for run_id in selected_tools:
        params_path = run_dir / "tools" / run_id / "resolved_params.json"
        if not params_path.exists():
            raise ValueError(
                f"Missing resolved params for run '{run_id}': {params_path}"
            )
        resolved_params_by_tool[run_id] = _load_json_object(
            params_path,
            f"resolved_params[{run_id}]",
        )

    conditional_input_errors: dict[str, list[str]] = {}
    compatibility_blocks: dict[str, list[str]] = {}
    compatibility_warnings: list[str] = []
    for run_id in selected_tools:
        catalog_tool_id = selected_tool_catalog_ids.get(run_id, "").strip()
        if not catalog_tool_id:
            raise ValueError(
                f"preflight report is missing catalog mapping for run '{run_id}'"
            )
        toolspec = _load_toolspec(tools_root, catalog_tool_id)
        rule_blocks, rule_warnings, rule_errors = _collect_compatibility_rule_issues(
            tool_id=run_id,
            toolspec=toolspec,
            dataset=dataset,
            resolved_params=resolved_params_by_tool[run_id],
            resolved_execution=resolved_execution_by_tool.get(run_id, {}),
            warning_prefix=run_id,
        )
        if rule_errors:
            compatibility_blocks[run_id] = [
                f"invalid compatibility rule: {message}" for message in rule_errors
            ]
        elif rule_blocks:
            compatibility_blocks[run_id] = rule_blocks
        compatibility_warnings.extend(rule_warnings)
        issues = _collect_conditional_input_issues(
            tool_id=run_id,
            toolspec=toolspec,
            dataset=dataset,
            resolved_params=resolved_params_by_tool[run_id],
            resolved_execution=resolved_execution_by_tool.get(run_id, {}),
        )
        if issues:
            conditional_input_errors[run_id] = issues

    if compatibility_blocks:
        error_lines: list[str] = []
        for run_id in sorted(compatibility_blocks):
            for message in compatibility_blocks[run_id]:
                error_lines.append(f"[{run_id}] {message}")
        raise ValueError(
            "Execution blocked by tool compatibility rules:\n" + "\n".join(error_lines)
        )

    if conditional_input_errors:
        error_lines: list[str] = []
        for run_id in sorted(conditional_input_errors):
            for message in conditional_input_errors[run_id]:
                error_lines.append(f"[{run_id}] {message}")
        raise ValueError(
            "Execution blocked by missing conditional inputs:\n"
            + "\n".join(error_lines)
        )

    report_issues = [
        issue for issue in run_report.get("issues", []) if isinstance(issue, dict)
    ]
    warnings = issue_messages(report_issues, severity="warn")
    warnings.extend(compatibility_warnings)
    runtime_warnings: list[str] = []
    physical_results: dict[str, ToolExecutionResult] = {}
    merged_raw_path = None
    merged_norm_path = None
    merged_raw_gexf_path = None
    merged_norm_gexf_path = None
    merged_raw_graphml_path = None
    merged_norm_graphml_path = None
    per_tool_rows = {}

    _ensure_docker_cli()
    shared_expression, shared_extras = _prepare_shared_inputs(
        run_dir=run_dir,
        dataset=dataset,
        constraints=constraints,
    )

    required_group_labels = {
        str(physical.get("group_label", "")).strip()
        for logical_spec in logical_runs.values()
        if str(logical_spec["execution"].get("mode", "")).strip() == "group_emulated"
        for physical in logical_spec["physical_tasks"]
        if physical.get("group_label") is not None
    }
    group_expression_sources: dict[str, Path] = {}
    if required_group_labels:
        groups_path = dataset.extras.get("groups")
        if groups_path is None:
            raise ValueError(
                "Execution requires groups.tsv because at least one run uses execution.mode=group_emulated."
            )
        group_expression_sources = _prepare_group_expression_sources(
            run_dir=run_dir,
            shared_expression=shared_expression,
            groups_path=groups_path,
            required_group_labels=required_group_labels,
        )

    runtime_io_by_tool = {}
    for logical_run_id in selected_tools:
        logical_spec = logical_runs.get(logical_run_id)
        if logical_spec is None:
            raise ValueError(f"plan.json is missing logical run '{logical_run_id}'")
        resolved_params = resolved_params_by_tool[logical_run_id]
        for physical in logical_spec["physical_tasks"]:
            task_id = str(physical.get("task_id", "")).strip()
            if not task_id:
                raise ValueError(
                    f"plan.json logical run '{logical_run_id}' contains invalid physical task"
                )
            expression_source = shared_expression
            if (
                str(logical_spec["execution"].get("mode", "")).strip()
                == "group_emulated"
                and physical.get("group_label") is not None
            ):
                expression_source = group_expression_sources[
                    str(physical.get("group_label", "")).strip()
                ]
            runtime_io_by_tool[task_id] = _prepare_tool_runtime_io(
                run_dir=run_dir,
                tool_id=task_id,
                run_id=logical_run_id,
                output_dir=str(physical.get("output_dir", "")),
                resolved_params=resolved_params,
                resolved_execution=logical_spec["execution"],
                shared_expression=shared_expression,
                shared_extras=shared_extras,
                expression_source=expression_source,
            )

    pulled_images = set()
    for wave in waves:
        wave_results = _run_wave(
            wave=wave,
            runtime_io_by_tool=runtime_io_by_tool,
            pulled_images=pulled_images,
            poll_interval_s=progress_poll_seconds,
            warnings=runtime_warnings,
        )
        physical_results.update(wave_results)

    grouped_child_ids = {
        str(physical.get("task_id", "")).strip()
        for logical_spec in logical_runs.values()
        for physical in logical_spec["physical_tasks"]
        if str(physical.get("task_id", "")).strip()
        and str(physical.get("task_id", "")).strip() != logical_spec["run_id"]
    }
    for message in runtime_warnings:
        handled = False
        for task_id in grouped_child_ids:
            if message.startswith(f"[{task_id}] "):
                handled = True
                break
        if not handled:
            warnings.append(message)

    logical_results: dict[str, ToolExecutionResult] = {}
    logical_results_payload: dict[str, Any] = {}
    status_by_tool: dict[str, str] = {}
    for logical_run_id in selected_tools:
        logical_spec = logical_runs[logical_run_id]
        physical_tasks = logical_spec["physical_tasks"]
        if (
            len(physical_tasks) == 1
            and str(physical_tasks[0].get("task_id", "")).strip() == logical_run_id
        ):
            result = physical_results.get(logical_run_id)
            if result is None:
                result = ToolExecutionResult(
                    tool_id=logical_run_id,
                    status="failed",
                    exit_code=127,
                    duration_seconds=0.0,
                    network_path=None,
                    progress_path=None,
                    logs_path=None,
                    error="Planned execution result is missing.",
                )
            logical_results[logical_run_id] = result
            logical_results_payload[logical_run_id] = {
                **asdict(result),
                "execution": logical_spec["execution"],
                "physical_tasks_total": 1,
                "child_results": {},
            }
            status_by_tool[logical_run_id] = result.status
            continue

        grouped_children = {
            str(physical.get("task_id", "")).strip(): physical_results.get(
                str(physical.get("task_id", "")).strip()
            )
            for physical in physical_tasks
        }
        logical_result, logical_payload = _finalize_grouped_logical_run(
            run_dir=run_dir,
            run_id=logical_run_id,
            logical_spec=logical_spec,
            child_results={
                task_id: result
                for task_id, result in grouped_children.items()
                if result is not None
            },
            strict=strict,
            warnings=warnings,
        )
        logical_results[logical_run_id] = logical_result
        logical_results_payload[logical_run_id] = logical_payload
        status_by_tool[logical_run_id] = logical_result.status

    execution_results, per_tool_rows, merged_raw_path, merged_norm_path = (
        _merge_network_outputs(
            run_dir=run_dir,
            execution_results=logical_results,
            warnings=warnings,
        )
    )

    merged_raw_gexf_path: Path | None = None
    merged_raw_graphml_path: Path | None = None
    merged_norm_gexf_path: Path | None = None
    merged_norm_graphml_path: Path | None = None
    merged_norm_cytoscape_script_path: Path | None = None

    if merged_raw_path is not None:
        merged_raw_gexf_path = run_dir / "merged_network_raw.gexf"
        merged_raw_graphml_path = run_dir / "merged_network_raw.graphml"
        export_network_gexf(merged_raw_path, merged_raw_gexf_path)
        export_network_graphml(merged_raw_path, merged_raw_graphml_path)

    if merged_norm_path is not None:
        merged_norm_gexf_path = run_dir / "merged_network_normalized.gexf"
        merged_norm_graphml_path = run_dir / "merged_network_normalized.graphml"
        merged_norm_cytoscape_script_path = (
            run_dir / "merged_network_normalized_cytoscape.py"
        )
        export_network_gexf(merged_norm_path, merged_norm_gexf_path)
        export_network_graphml(merged_norm_path, merged_norm_graphml_path)
        export_cytoscape_style_script(
            csv_path=merged_norm_path,
            graphml_path=merged_norm_graphml_path,
            out_path=merged_norm_cytoscape_script_path,
        )

    completed_tools = sorted(
        tool_id
        for tool_id, result in execution_results.items()
        if result.status == "completed"
    )
    failed_tools = {
        tool_id: (result.error or "unknown error")
        for tool_id, result in execution_results.items()
        if result.status != "completed"
    }
    for logical_run_id, result in execution_results.items():
        status_by_tool[logical_run_id] = result.status
        if logical_run_id in logical_results_payload:
            logical_results_payload[logical_run_id].update(asdict(result))
    logical_results_payload = _relativize_result_payload(
        logical_results_payload,
        base_dir=run_dir,
    )

    elapsed_total = round(time.perf_counter() - started_at, 3)
    run_report["status"] = "executed"
    run_report["tools"] = {
        "selected": selected_tools,
        "catalog_tool_ids": selected_tool_catalog_ids,
        "skipped": skipped_tools,
        "status_by_tool": status_by_tool,
        "completed": completed_tools,
        "failed": failed_tools,
        "results": logical_results_payload,
    }
    run_report["outputs"] = {
        "merged_network_raw": _report_path(merged_raw_path, base_dir=run_dir),
        "merged_network_raw_gexf": (
            _report_path(merged_raw_gexf_path, base_dir=run_dir)
        ),
        "merged_network_raw_graphml": (
            _report_path(merged_raw_graphml_path, base_dir=run_dir)
        ),
        "merged_network_normalized": _report_path(merged_norm_path, base_dir=run_dir),
        "merged_network_normalized_gexf": (
            _report_path(merged_norm_gexf_path, base_dir=run_dir)
        ),
        "merged_network_normalized_graphml": (
            _report_path(merged_norm_graphml_path, base_dir=run_dir)
        ),
        "merged_network_normalized_cytoscape_script": (
            _report_path(merged_norm_cytoscape_script_path, base_dir=run_dir)
        ),
        "rows_per_tool": per_tool_rows,
    }
    seen_warning_messages = set(issue_messages(report_issues, severity="warn"))
    for message in warnings:
        if message in seen_warning_messages:
            continue
        seen_warning_messages.add(message)
        report_issues.append(
            make_issue(
                severity="warn",
                code="runtime_warning",
                message=message,
            )
        )
    run_report["issues"] = report_issues
    execution_info = run_report.get("execution", {})
    if not isinstance(execution_info, dict):
        execution_info = {}
    execution_info.update(
        {
            "elapsed_seconds": elapsed_total,
            "waves_total": len(waves),
            "tools_selected": len(selected_tools),
            "physical_tasks_total": int(
                sum(
                    len(logical_runs[run_id]["physical_tasks"])
                    for run_id in selected_tools
                    if run_id in logical_runs
                )
            ),
            "tools_completed": len(completed_tools),
            "tools_failed": len(failed_tools),
        }
    )
    run_report["execution"] = execution_info
    _write_json(run_report_path, run_report)

    print(f"infer-network execution completed: {run_dir}")
    print(f"  selected tools: {len(selected_tools)}")
    print(f"  skipped tools: {len(skipped_tools)}")
    print(f"  completed tools: {len(completed_tools)}")
    print(f"  failed tools: {len(failed_tools)}")
    print(f"  elapsed time: {elapsed_total:.2f}s")
    print(f"  waves: {len(waves)}")
    if merged_raw_path:
        print(f"  merged raw: {merged_raw_path}")
    if merged_raw_gexf_path:
        print(f"  merged raw gexf: {merged_raw_gexf_path}")
    if merged_raw_graphml_path:
        print(f"  merged raw graphml: {merged_raw_graphml_path}")
    if merged_norm_path:
        print(f"  merged normalized: {merged_norm_path}")
    if merged_norm_gexf_path:
        print(f"  merged normalized gexf: {merged_norm_gexf_path}")
    if merged_norm_graphml_path:
        print(f"  merged normalized graphml: {merged_norm_graphml_path}")
    if merged_norm_cytoscape_script_path:
        print(
            "  merged normalized cytoscape preset:"
            f" {merged_norm_cytoscape_script_path}"
        )
    warning_count = len(issue_messages(report_issues, severity="warn"))
    if warning_count:
        print(f"  warnings: {warning_count} (see run_report.json)")

    if not completed_tools:
        raise ValueError(
            "All tool executions failed. See run_report.json and per-tool container.log files."
        )
    if strict and failed_tools:
        raise ValueError("One or more tools failed during execution (strict mode).")
    return run_dir
