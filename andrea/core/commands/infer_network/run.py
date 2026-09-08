"""Phase C: Execution for infer-network.

Phase dependencies:
1. Planned run_dir integrity checks.
2. Runtime IO prep + Docker wave execution.
3. Merge and normalization of logical-run outputs.
4. Final run_report persistence.
"""

from __future__ import annotations

import csv
import math
import os
import shutil
import tempfile
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from rich import print

from andrea.core.shared.dataset_identity import (
    fingerprint_dataset_content,
    validate_dataset_fingerprint,
)
from andrea.core.shared.issues import issue_messages, make_issue
from andrea.core.shared.json_io import (
    load_json_object as _load_json_object,
)
from andrea.core.shared.json_io import (
    write_json as _write_json,
)
from andrea.core.shared.output_capabilities import (
    validate_frozen_output_capabilities,
    validate_selected_tool_identity_maps,
)
from andrea.core.shared.paths import report_path as _report_path

from .commons.artifacts import _load_plan_waves, _verify_input_fingerprints
from .commons.catalog import _load_schema_constraints, _resolve_catalog_paths
from .commons.custom_tools import load_custom_tool_registry
from .commons.dataset import (
    _load_groups_by_column,
    _parse_dataset_context,
    _read_column_phenotype_labels,
    _read_expression_columns,
)
from .commons.execution_state import ExecutionStateWriter
from .commons.merge import (
    _iter_network_rows,
    _merge_network_outputs,
    _read_network_rows,
    _write_network_rows,
)
from .commons.network_exports import (
    export_cytoscape_style_script,
    export_network_gexf,
    export_network_graphml,
)
from .commons.resources import normalize_cpuset_cpus, validate_cpuset_available
from .commons.runtime_helpers import (
    _ensure_docker_cli,
    _prepare_shared_inputs,
    _prepare_tool_runtime_io,
    _run_wave,
)
from .commons.shared import (
    PlanWave,
    ToolExecutionResult,
    ToolPlanItem,
    _slugify_token,
)
from .commons.telemetry import directory_size_bytes, not_started_measurement, utc_now
from .commons.threading import resolve_tool_threading, thread_count_allowed_by_tool
from .commons.tools import (
    EXECUTION_CAPABILITIES,
    RUNTIME_INPUT_CONTRACT_SCHEMA_VERSION,
    _build_output_capability_snapshot,
    _build_runtime_input_snapshot,
    _collect_compatibility_rule_issues,
    _collect_conditional_input_issues,
    _load_toolspec,
    _parse_execution_capabilities,
    _runtime_execution_for_logical_run,
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
        tool_origin = raw_run.get("tool_origin")
        execution = raw_run.get("execution", {})
        resources = raw_run.get("resources")
        execution_mode = (
            str(execution.get("mode", "")).strip()
            if isinstance(execution, dict)
            else ""
        )
        physical_tasks = raw_run.get("physical_tasks", [])
        if (
            not run_id
            or Path(run_id).name != run_id
            or run_id in {".", ".."}
            or not tool_id
            or tool_origin not in {"catalog", "custom"}
            or execution_mode not in EXECUTION_CAPABILITIES
            or not isinstance(execution, dict)
            or set(execution) != {"mode"}
            or not isinstance(physical_tasks, list)
            or not physical_tasks
            or "resources" not in raw_run
            or not isinstance(resources, dict)
            or bool(set(resources) - {"threads", "ram_gb", "cpuset_cpus"})
        ):
            raise ValueError(f"plan.json.runs[{idx}] is invalid")
        requested_threads = resources.get("threads")
        if requested_threads is not None and (
            isinstance(requested_threads, bool)
            or not isinstance(requested_threads, int)
            or requested_threads < 1
        ):
            raise ValueError(f"plan.json.runs[{idx}].resources is invalid")
        requested_ram_gb = resources.get("ram_gb")
        if requested_ram_gb is not None and (
            isinstance(requested_ram_gb, bool)
            or not isinstance(requested_ram_gb, (int, float))
            or not math.isfinite(float(requested_ram_gb))
            or requested_ram_gb <= 0
        ):
            raise ValueError(f"plan.json.runs[{idx}].resources is invalid")
        requested_cpuset = resources.get("cpuset_cpus")
        if requested_cpuset is not None:
            cpuset = normalize_cpuset_cpus(
                requested_cpuset,
                source=f"plan.json.runs[{idx}].resources.cpuset_cpus",
            )
            if requested_threads is None or len(cpuset) < requested_threads:
                raise ValueError(f"plan.json.runs[{idx}].resources is invalid")
        if run_id in logical_runs:
            raise ValueError(f"plan.json contains duplicate run_id: {run_id!r}")
        logical_runs[run_id] = {
            "run_id": run_id,
            "tool_id": tool_id,
            "tool_origin": tool_origin,
            "execution": execution,
            "resources": resources,
            "physical_tasks": physical_tasks,
        }
    return logical_runs


def _validate_physical_task_plan(
    *,
    logical_runs: dict[str, dict[str, Any]],
    waves: list[PlanWave],
) -> dict[str, ToolPlanItem]:
    """Validate the one-to-one mapping between logical runs and wave tasks."""

    expected: dict[str, tuple[str, dict[str, Any]]] = {}
    for run_id, logical_spec in logical_runs.items():
        mode = str(logical_spec["execution"]["mode"])
        physical_tasks = logical_spec["physical_tasks"]
        if mode in {"global", "group_native", "column_native"} and len(
            physical_tasks
        ) != 1:
            raise ValueError(
                f"[{run_id}] execution.mode={mode} requires exactly one physical task"
            )
        if mode == "group_aggregated" and len(physical_tasks) != 1:
            raise ValueError(
                f"[{run_id}] group_aggregated requires exactly one column-native task"
            )

        group_labels: set[str] = set()
        for index, physical in enumerate(physical_tasks, start=1):
            if not isinstance(physical, dict):
                raise ValueError(
                    f"[{run_id}] physical_tasks[{index}] must be an object"
                )
            task_id = str(physical.get("task_id", "")).strip()
            output_dir = str(physical.get("output_dir", "")).strip()
            columns = physical.get("columns")
            threads = physical.get("threads")
            ram_gb = physical.get("ram_gb")
            group_label_raw = physical.get("group_label")
            group_label = (
                str(group_label_raw).strip()
                if group_label_raw is not None
                else None
            )
            if (
                not task_id
                or not output_dir
                or not isinstance(columns, int)
                or isinstance(columns, bool)
                or columns < 1
                or isinstance(threads, bool)
                or not isinstance(threads, int)
                or threads < 1
                or isinstance(ram_gb, bool)
                or not isinstance(ram_gb, (int, float))
                or ram_gb <= 0
            ):
                raise ValueError(
                    f"[{run_id}] physical_tasks[{index}] has an invalid task_id, "
                    "output_dir, columns, threads, or ram_gb value"
                )
            if task_id in expected:
                raise ValueError(f"plan.json contains duplicate physical task: {task_id}")

            if mode in {"global", "group_native", "column_native"}:
                if task_id != run_id or group_label is not None:
                    raise ValueError(
                        f"[{run_id}] execution.mode={mode} requires task_id={run_id!r} "
                        "and group_label=null"
                    )
            elif mode == "group_emulated":
                if task_id == run_id or not group_label:
                    raise ValueError(
                        f"[{run_id}] each group_emulated child requires a distinct "
                        "task_id and non-empty group_label"
                    )
                if group_label in group_labels:
                    raise ValueError(
                        f"[{run_id}] duplicate group_emulated label: {group_label!r}"
                    )
                group_labels.add(group_label)
            elif mode == "group_aggregated":
                if (
                    task_id != f"{run_id}__column_native"
                    or group_label is not None
                    or physical.get("postprocess")
                    != "group_aggregated_mean_signed_effect"
                ):
                    raise ValueError(
                        f"[{run_id}] group_aggregated requires the canonical "
                        "column-native child and aggregation task"
                    )
            expected[task_id] = (run_id, physical)

    observed: dict[str, ToolPlanItem] = {}
    for wave in waves:
        for task in wave.tasks:
            if task.tool_id in observed:
                raise ValueError(
                    f"plan.json waves contain duplicate task: {task.tool_id}"
                )
            observed[task.tool_id] = task

    if set(observed) != set(expected):
        missing = sorted(set(expected).difference(observed))
        extra = sorted(set(observed).difference(expected))
        raise ValueError(
            "plan.json physical_tasks and waves must form an exact bijection; "
            f"missing={missing}, extra={extra}"
        )

    for task_id, task in observed.items():
        run_id, physical = expected[task_id]
        expected_group = physical.get("group_label")
        if (
            task.run_id != run_id
            or task.output_dir != str(physical["output_dir"])
            or task.group_label != expected_group
            or task.threads != int(physical["threads"])
            or round(task.ram_gb, 3) != round(float(physical["ram_gb"]), 3)
        ):
            raise ValueError(
                f"[{task_id}] wave task does not match its physical task declaration"
            )
    return observed


def _validate_wave_resource_schedule(
    *,
    plan_payload: dict[str, Any],
    waves: list[PlanWave],
) -> None:
    """Verify that the frozen wave schedule obeys its declared resource budget."""

    limits = plan_payload.get("resource_limits")
    if not isinstance(limits, dict) or set(limits) != {"max_cores", "max_ram_gb"}:
        raise ValueError("plan.json resource_limits is invalid")
    max_cores = limits.get("max_cores")
    max_ram_gb = limits.get("max_ram_gb")
    if (
        isinstance(max_cores, bool)
        or not isinstance(max_cores, int)
        or max_cores < 1
        or isinstance(max_ram_gb, bool)
        or not isinstance(max_ram_gb, (int, float))
        or max_ram_gb <= 0
    ):
        raise ValueError("plan.json resource_limits is invalid")

    expected_indices = list(range(1, len(waves) + 1))
    if [wave.index for wave in waves] != expected_indices:
        raise ValueError("plan.json wave indices must be contiguous and start at 1")
    for wave in waves:
        threads_used = sum(task.threads for task in wave.tasks)
        ram_gb_used = round(sum(task.ram_gb for task in wave.tasks), 3)
        if (
            wave.threads_used != threads_used
            or round(wave.ram_gb_used, 3) != ram_gb_used
        ):
            raise ValueError(
                f"plan.json wave {wave.index} resource totals do not match its tasks"
            )
        if threads_used > max_cores or ram_gb_used > float(max_ram_gb):
            raise ValueError(
                f"plan.json wave {wave.index} exceeds the declared resource_limits"
            )
        for left_index, left in enumerate(wave.tasks):
            for right in wave.tasks[left_index + 1 :]:
                if left.cpuset_cpus is None and right.cpuset_cpus is None:
                    continue
                if left.cpuset_cpus is None or right.cpuset_cpus is None:
                    raise ValueError(
                        f"plan.json wave {wave.index} mixes pinned and unpinned tasks"
                    )
                if set(left.cpuset_cpus).intersection(right.cpuset_cpus):
                    raise ValueError(
                        f"plan.json wave {wave.index} contains overlapping CPU affinities"
                    )


def _remove_runtime_artifact(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _clear_previous_execution_artifacts(
    *,
    run_dir: Path,
    logical_runs: dict[str, dict[str, Any]],
) -> None:
    """Remove outputs from an earlier execution of the same immutable plan."""

    tools_dir = run_dir / "tools"
    for run_id, logical_spec in logical_runs.items():
        tool_dir = tools_dir / run_id
        if tool_dir.is_symlink():
            raise ValueError(f"[{run_id}] tool directory must not be a symlink")
        _remove_runtime_artifact(tool_dir / "io")
        _remove_runtime_artifact(tool_dir / "group_execution.log")
        _remove_runtime_artifact(tool_dir / "group_aggregation.log")
        mode = str(logical_spec["execution"]["mode"])
        if mode == "group_emulated":
            _remove_runtime_artifact(tool_dir / "subruns")
        elif mode == "group_aggregated":
            _remove_runtime_artifact(tool_dir / "upstream_column_native")

    for filename in (
        "merged_network_raw.csv",
        "merged_network_normalized.csv",
        "merged_network_raw.gexf",
        "merged_network_raw.graphml",
        "merged_network_normalized.gexf",
        "merged_network_normalized.graphml",
        "merged_network_normalized_cytoscape.py",
    ):
        artifact = run_dir / filename
        if artifact.exists() and artifact.is_dir() and not artifact.is_symlink():
            raise ValueError(f"Expected execution artifact is a directory: {artifact}")
        _remove_runtime_artifact(artifact)


def _planned_contexts_by_run(
    *,
    logical_runs: dict[str, dict[str, Any]],
    dataset: Any,
    expression_columns: list[str],
    active_extra_input_keys_by_run: dict[str, set[str]],
    group_to_columns: dict[str, list[str]],
) -> dict[str, set[str]]:
    """Resolve the exact contexts each logical execution mode may emit."""
    contexts_by_run: dict[str, set[str]] = {}
    standard_group_labels: list[str] | None = None

    for run_id, logical_spec in logical_runs.items():
        mode = str(logical_spec["execution"].get("mode", "")).strip()
        if mode == "global":
            contexts = {"global"}
        elif mode == "column_native":
            contexts = {f"column:{column_id}" for column_id in expression_columns}
        elif mode == "group_emulated":
            contexts = {
                f"group:{str(physical.get('group_label', '')).strip()}"
                for physical in logical_spec["physical_tasks"]
                if str(physical.get("group_label", "")).strip()
            }
        elif mode == "group_aggregated":
            contexts = {f"group:{group_label}" for group_label in group_to_columns}
        elif mode == "group_native":
            active_inputs = active_extra_input_keys_by_run.get(run_id, set())
            groups_path = dataset.extras.get("groups")
            phenotypes_path = dataset.extras.get("column_phenotypes")
            if "groups" in active_inputs and groups_path is not None:
                if standard_group_labels is None:
                    standard_group_labels, _mapping = _load_groups_by_column(
                        groups_path=groups_path,
                        expression_columns=expression_columns,
                    )
                group_labels = standard_group_labels
            elif "column_phenotypes" in active_inputs and phenotypes_path is not None:
                group_labels = _read_column_phenotype_labels(phenotypes_path)
            else:
                raise ValueError(
                    f"[{run_id}] group_native cannot freeze output contexts: "
                    "the tool must declare groups or column_phenotypes and the "
                    "dataset must provide that input"
                )
            contexts = {f"group:{group_label}" for group_label in group_labels}
        else:  # guarded by _load_logical_runs_from_plan
            raise ValueError(f"[{run_id}] unsupported execution mode: {mode!r}")

        if not contexts:
            raise ValueError(
                f"[{run_id}] execution mode {mode!r} resolved no output contexts"
            )
        contexts_by_run[run_id] = contexts
    return contexts_by_run


def _completed_contexts_for_run(
    *,
    run_id: str,
    logical_spec: dict[str, Any],
    logical_payload: dict[str, Any],
    planned_contexts: set[str],
) -> list[str]:
    """Return contexts completed successfully, excluding failed emulated children."""
    mode = str(logical_spec["execution"].get("mode", "")).strip()
    if mode != "group_emulated":
        return sorted(planned_contexts)

    child_results = logical_payload.get("child_results")
    if not isinstance(child_results, dict):
        raise ValueError(f"[{run_id}] grouped result is missing child_results")
    completed_contexts: list[str] = []
    for physical in logical_spec["physical_tasks"]:
        task_id = str(physical.get("task_id", "")).strip()
        group_label = str(physical.get("group_label", "")).strip()
        child = child_results.get(task_id)
        if not isinstance(child, dict):
            continue
        if child.get("status") in {"completed", "completed_with_warnings"}:
            completed_contexts.append(f"group:{group_label}")
    if not completed_contexts:
        raise ValueError(
            f"[{run_id}] completed group_emulated run has no successful child context"
        )
    return completed_contexts


def _sync_warning_messages_to_state(
    *,
    state_writer: ExecutionStateWriter,
    warnings: list[str],
    cursor: int,
) -> int:
    for message in warnings[cursor:]:
        state_writer.record_warning_message(message)
    return len(warnings)


def _append_warning_once(warnings: list[str], message: str) -> None:
    text = str(message).strip()
    if text and text not in warnings:
        warnings.append(text)


def _prepare_group_expression_sources(
    *,
    run_dir: Path,
    shared_expression: Path,
    groups_path: Path,
    required_group_labels: set[str],
) -> dict[str, Path]:
    expression_columns = _read_expression_columns(shared_expression)
    group_order, group_to_columns = _load_groups_by_column(
        groups_path=groups_path,
        expression_columns=expression_columns,
    )
    missing_groups = sorted(required_group_labels.difference(group_to_columns))
    if missing_groups:
        raise ValueError(
            "Planned group labels are absent from groups.tsv: "
            + ", ".join(missing_groups)
        )

    shared_group_dir = run_dir / "shared" / "groups"
    shared_group_dir.parent.mkdir(parents=True, exist_ok=True)
    prepared_relpaths: dict[str, Path] = {}

    with tempfile.TemporaryDirectory(
        prefix=".groups-",
        dir=shared_group_dir.parent,
    ) as staging_raw:
        staging_dir = Path(staging_raw)
        with (
            shared_expression.open("r", encoding="utf-8", newline="") as src,
            ExitStack() as stack,
        ):
            reader = csv.reader(src, delimiter="\t")
            header = next(reader, None)
            if header is None or len(header) < 2:
                raise ValueError(
                    "Expression matrix must have header with at least 2 columns: "
                    f"{shared_expression}"
                )
            column_indices = {
                str(column).strip(): idx
                for idx, column in enumerate(header[1:], start=1)
            }
            outputs: dict[str, tuple[Any, list[int]]] = {}
            for idx, group_label in enumerate(group_order, start=1):
                if group_label not in required_group_labels:
                    continue
                relpath = (
                    Path(f"{idx:02d}_{_slugify_token(group_label)}")
                    / "expression.tsv"
                )
                output_path = staging_dir / relpath
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_handle = stack.enter_context(
                    output_path.open("w", encoding="utf-8", newline="")
                )
                writer = csv.writer(
                    output_handle,
                    delimiter="\t",
                    lineterminator="\n",
                )
                keep_indices = [0] + [
                    column_indices[column]
                    for column in group_to_columns[group_label]
                ]
                writer.writerow([header[index] for index in keep_indices])
                outputs[group_label] = (writer, keep_indices)
                prepared_relpaths[group_label] = relpath

            expected_columns = len(header)
            for line_idx, row in enumerate(reader, start=2):
                if not row:
                    continue
                if len(row) != expected_columns:
                    raise ValueError(
                        "Expression matrix has inconsistent number of columns at "
                        f"line {line_idx}: expected {expected_columns}, got "
                        f"{len(row)} ({shared_expression})"
                    )
                for writer, keep_indices in outputs.values():
                    writer.writerow([row[index] for index in keep_indices])

        if shared_group_dir.is_symlink() or shared_group_dir.is_file():
            shared_group_dir.unlink()
        elif shared_group_dir.exists():
            shutil.rmtree(shared_group_dir)
        staging_dir.replace(shared_group_dir)

    return {
        group_label: shared_group_dir / relpath
        for group_label, relpath in prepared_relpaths.items()
    }


def _column_context_id(context: str) -> str | None:
    prefix = "column:"
    if not context.startswith(prefix):
        return None
    column_id = context.removeprefix(prefix).strip()
    return column_id or None


def _aggregate_column_rows_by_group(
    *,
    rows: Iterable[dict[str, Any]],
    group_to_columns: dict[str, list[str]],
) -> Iterable[dict[str, Any]]:
    column_to_group: dict[str, str] = {}
    for group_label, columns in group_to_columns.items():
        for column in columns:
            column_to_group[str(column)] = group_label

    aggregate: dict[tuple[str, str, str], list[Any]] = {}
    for row in rows:
        column_id = _column_context_id(str(row.get("context", "")))
        if column_id is None:
            raise ValueError(
                "group_aggregated requires every upstream network row to use a "
                "column:<id> context"
            )
        group_label = column_to_group.get(column_id)
        if group_label is None:
            raise ValueError(
                f"column context '{column_id}' is not present in groups.tsv"
            )
        key = (group_label, str(row["source"]), str(row["target"]))
        state = aggregate.setdefault(key, [0.0, False, 0.0])
        score = float(row["score"])
        sign = str(row["sign"])
        if sign == "+":
            state[0] = float(state[0]) + score
            state[1] = True
        elif sign == "-":
            state[0] = float(state[0]) - score
            state[1] = True
        else:
            state[2] = float(state[2]) + score

    for (group_label, source, target), state in aggregate.items():
        denominator = max(1, len(group_to_columns[group_label]))
        signed_sum = float(state[0])
        unknown_sum = float(state[2])
        if bool(state[1]):
            mean_signed = signed_sum / denominator
            if mean_signed > 0:
                score = mean_signed
                sign = "+"
            elif mean_signed < 0:
                score = abs(mean_signed)
                sign = "-"
            elif unknown_sum > 0:
                score = unknown_sum / denominator
                sign = "?"
            else:
                continue
        else:
            score = unknown_sum / denominator
            sign = "?"
        if score <= 0:
            continue
        yield {
            "source": source,
            "target": target,
            "score": score,
            "sign": sign,
            "evidence": "andrea_group_agg_mean_effect",
            "context": f"group:{group_label}",
        }


def _link_or_copy_output(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or destination.exists():
        destination.unlink()
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _logical_measurement(
    *,
    child_results: Iterable[ToolExecutionResult],
    postprocess_started_monotonic_ns: int,
    finished_monotonic_ns: int,
    finished_at_utc: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Aggregate physical measurements without calling CPU time wall time.

    Child cgroup peaks are not timestamped.  For overlapping child intervals we
    therefore report a conservative upper bound, and name that semantic in the
    result instead of presenting it as a directly observed concurrent peak.
    """

    measurements = [result.measurement for result in child_results]
    intervals: list[tuple[int, int, dict[str, Any]]] = []
    for measurement in measurements:
        started = measurement.get("task_started_monotonic_ns")
        finished = measurement.get("task_finished_monotonic_ns")
        if (
            isinstance(started, int)
            and not isinstance(started, bool)
            and isinstance(finished, int)
            and not isinstance(finished, bool)
            and finished >= started
        ):
            intervals.append((started, finished, measurement))

    logical_started_ns = (
        min(start for start, _end, _measurement in intervals)
        if intervals
        else postprocess_started_monotonic_ns
    )
    logical_started_at_utc = None
    if intervals:
        earliest = min(intervals, key=lambda item: item[0])[2]
        raw_started_at = earliest.get("task_started_at_utc")
        if isinstance(raw_started_at, str):
            logical_started_at_utc = raw_started_at

    cpu_values = [measurement.get("cpu_time_seconds") for measurement in measurements]
    cpu_complete = bool(cpu_values) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in cpu_values
    )
    cpu_time = (
        round(sum(float(value) for value in cpu_values), 6)
        if cpu_complete
        else None
    )
    child_wall_sum = round(
        sum(
            float(value)
            for measurement in measurements
            if isinstance((value := measurement.get("wall_time_seconds")), (int, float))
            and not isinstance(value, bool)
        ),
        6,
    )

    candidate_times = sorted(
        {time_ns for start, end, _measurement in intervals for time_ns in (start, end)}
    )
    memory_complete = (
        bool(intervals)
        and len(intervals) == len(measurements)
        and all(
            isinstance(measurement.get("peak_memory_bytes"), int)
            and not isinstance(measurement.get("peak_memory_bytes"), bool)
            for _start, _end, measurement in intervals
        )
    )
    peak_memory: int | None = 0 if memory_complete else None
    threads_peak = 0
    ram_limit_peak = 0
    for time_ns in candidate_times:
        active = [
            measurement
            for start, end, measurement in intervals
            if start <= time_ns < end
        ]
        memory_values = [measurement.get("peak_memory_bytes") for measurement in active]
        if memory_complete and memory_values:
            observed_upper_bound = sum(int(value) for value in memory_values)
            peak_memory = max(peak_memory or 0, observed_upper_bound)
        threads_peak = max(
            threads_peak,
            sum(
                int(resources.get("threads", 0))
                for measurement in active
                if isinstance(
                    (resources := measurement.get("assigned_resources")), dict
                )
            ),
        )
        ram_limit_peak = max(
            ram_limit_peak,
            sum(
                int(resources.get("ram_limit_bytes", 0))
                for measurement in active
                if isinstance(
                    (resources := measurement.get("assigned_resources")), dict
                )
            ),
        )

    statuses = [
        telemetry.get("status")
        for measurement in measurements
        if isinstance((telemetry := measurement.get("telemetry")), dict)
    ]
    if statuses and all(status == "complete" for status in statuses):
        telemetry_status = "complete"
    elif any(status in {"complete", "partial"} for status in statuses):
        telemetry_status = "partial"
    else:
        telemetry_status = "unavailable"
    requested_cpusets = sorted(
        {
            tuple(cpuset)
            for measurement in measurements
            if isinstance(
                (resources := measurement.get("assigned_resources")), dict
            )
            and isinstance(
                (cpuset := resources.get("cpuset_cpus_requested")), list
            )
        }
    )
    effective_cpusets = sorted(
        {
            tuple(cpuset)
            for measurement in measurements
            if isinstance(
                (resources := measurement.get("assigned_resources")), dict
            )
            and isinstance(
                (cpuset := resources.get("cpuset_cpus_effective")), list
            )
        }
    )
    return {
        "schema_version": "1.0",
        "scope": "logical_run",
        "wall_time_seconds": round(
            max(0, finished_monotonic_ns - logical_started_ns) / 1_000_000_000.0,
            6,
        ),
        "child_wall_time_sum_seconds": child_wall_sum,
        "postprocess_wall_time_seconds": round(
            max(0, finished_monotonic_ns - postprocess_started_monotonic_ns)
            / 1_000_000_000.0,
            6,
        ),
        "cpu_time_seconds": cpu_time,
        "peak_memory_bytes": peak_memory,
        "output_bytes": directory_size_bytes(output_dir),
        "io_read_bytes": None,
        "io_write_bytes": None,
        "task_started_at_utc": logical_started_at_utc,
        "task_finished_at_utc": finished_at_utc,
        "task_started_monotonic_ns": logical_started_ns,
        "task_finished_monotonic_ns": int(finished_monotonic_ns),
        "assigned_resources": {
            "threads_peak": threads_peak,
            "ram_limit_peak_bytes": ram_limit_peak,
            "cpuset_cpus_requested_sets": [list(value) for value in requested_cpusets],
            "cpuset_cpus_effective_sets": [list(value) for value in effective_cpusets],
        },
        "telemetry": {
            "status": telemetry_status,
            "wall_source": "andrea_monotonic_clock",
            "wall_semantics": (
                "logical_run_from_first_physical_task_start_through_logical_"
                "postprocess; input_preparation_is_excluded"
            ),
            "cpu_source": "sum_of_child_container_cpu",
            "cpu_semantics": "sum_excludes_andrea_host_postprocess",
            "memory_source": (
                "child_container_cgroup_peaks" if memory_complete else None
            ),
            "memory_semantics": (
                "overlap_upper_bound_from_child_peaks_excludes_andrea_host_postprocess"
                if memory_complete
                else "unavailable_when_any_child_peak_or_interval_is_unavailable"
            ),
            "io_source": None,
            "io_semantics": "unavailable",
        },
    }


def _finalize_group_aggregated_logical_run(
    *,
    run_dir: Path,
    run_id: str,
    logical_spec: dict[str, Any],
    child_results: dict[str, ToolExecutionResult],
    group_to_columns: dict[str, list[str]],
    warnings: list[str],
) -> tuple[ToolExecutionResult, dict[str, Any]]:
    postprocess_started_monotonic_ns = time.perf_counter_ns()
    tool_dir = run_dir / "tools" / run_id
    out_dir = tool_dir / "io" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out_dir / "progress.json"
    logs_path = tool_dir / "group_aggregation.log"

    physical_tasks = logical_spec["physical_tasks"]
    child_task_id = str(physical_tasks[0].get("task_id", "")).strip()
    child_result = child_results.get(child_task_id)
    child_payload: dict[str, Any] = {}
    network_path: str | None = None
    status = "failed"
    error: str | None = None
    result_warnings: list[str] = []
    column_row_count = 0
    aggregated_row_count = 0

    if child_result is None:
        child_result = ToolExecutionResult(
            tool_id=child_task_id or run_id,
            status="failed",
            exit_code=127,
            measurement=not_started_measurement(
                threads=int(physical_tasks[0].get("threads", 1)),
                ram_gb=float(physical_tasks[0].get("ram_gb", 1.0)),
                requested_cpuset_cpus=(
                    tuple(logical_spec["resources"]["cpuset_cpus"])
                    if "cpuset_cpus" in logical_spec["resources"]
                    else None
                ),
            ),
            network_path=None,
            progress_path=None,
            logs_path=None,
            error="Internal column-native task result is missing.",
        )
    child_payload[child_task_id] = {
        **asdict(child_result),
        "output_dir": str(physical_tasks[0].get("output_dir", "")),
    }

    result_warnings.extend(str(item) for item in child_result.warnings)

    if (
        child_result.status not in {"completed", "completed_with_warnings"}
        or not child_result.network_path
    ):
        error = child_result.error or "Column-native upstream execution failed."
    else:
        column_network_path = out_dir / "network.column_native.csv"
        parent_network = out_dir / "network.csv"
        try:
            def count_column_rows() -> Iterable[dict[str, Any]]:
                nonlocal column_row_count
                for row in _iter_network_rows(
                    Path(child_result.network_path),
                    tool_id=child_task_id,
                ):
                    column_row_count += 1
                    yield row

            def count_aggregated_rows() -> Iterable[dict[str, Any]]:
                nonlocal aggregated_row_count
                for row in _aggregate_column_rows_by_group(
                    rows=count_column_rows(),
                    group_to_columns=group_to_columns,
                ):
                    aggregated_row_count += 1
                    yield row

            _write_network_rows(
                path=parent_network,
                rows=count_aggregated_rows(),
                include_tool_id=False,
            )
            _link_or_copy_output(
                Path(child_result.network_path),
                column_network_path,
            )
            if aggregated_row_count == 0:
                _append_warning_once(
                    warnings,
                    f"[{run_id}] group_aggregated produced no non-zero aggregated group edges.",
                )
            network_path = str(parent_network.resolve())
            status = "completed"
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            for output_path in (column_network_path, parent_network):
                if output_path.is_symlink() or output_path.is_file():
                    output_path.unlink()

    if status == "completed" and result_warnings:
        status = "completed_with_warnings"

    for warning in result_warnings:
        _append_warning_once(warnings, f"[{run_id}] {warning}")

    progress_payload = {
        "percent": 100,
        "status": status,
        "phase": (
            "done" if status in {"completed", "completed_with_warnings"} else "failed"
        ),
        "message": (
            f"Aggregated {column_row_count} column-context row(s) into "
            f"{aggregated_row_count} group-context row(s)"
            if status in {"completed", "completed_with_warnings"}
            else error or "Aggregation failed"
        ),
        "warnings": result_warnings,
    }
    _write_json(progress_path, progress_payload)

    log_lines = [
        f"run_id={run_id}",
        f"status={status}",
        f"upstream_task={child_task_id}",
        f"column_rows={column_row_count}",
        f"aggregated_group_rows={aggregated_row_count}",
    ]
    if error:
        log_lines.append(f"error={error}")
    logs_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    finished_monotonic_ns = time.perf_counter_ns()
    finished_at_utc = utc_now()
    measurement = _logical_measurement(
        child_results=[child_result],
        postprocess_started_monotonic_ns=postprocess_started_monotonic_ns,
        finished_monotonic_ns=finished_monotonic_ns,
        finished_at_utc=finished_at_utc,
        output_dir=out_dir,
    )

    logical_result = ToolExecutionResult(
        tool_id=run_id,
        status=status,
        exit_code=0 if status in {"completed", "completed_with_warnings"} else 1,
        measurement=measurement,
        network_path=network_path,
        progress_path=str(progress_path.resolve()),
        logs_path=str(logs_path.resolve()),
        error=error,
        warnings=tuple(result_warnings),
    )
    logical_payload = {
        **asdict(logical_result),
        "execution": logical_spec["execution"],
        "physical_tasks_total": len(logical_spec["physical_tasks"]),
        "child_results": child_payload,
        "aggregation": {
            "rule": "mean_signed_effect_by_group",
            "column_rows": column_row_count,
            "aggregated_group_rows": aggregated_row_count,
        },
    }
    return logical_result, logical_payload


def _finalize_grouped_logical_run(
    *,
    run_dir: Path,
    run_id: str,
    logical_spec: dict[str, Any],
    child_results: dict[str, ToolExecutionResult],
    warnings: list[str],
) -> tuple[ToolExecutionResult, dict[str, Any]]:
    postprocess_started_monotonic_ns = time.perf_counter_ns()
    tool_dir = run_dir / "tools" / run_id
    out_dir = tool_dir / "io" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out_dir / "progress.json"
    logs_path = tool_dir / "group_execution.log"

    child_payload: dict[str, Any] = {}
    child_failures: list[tuple[str, ToolExecutionResult]] = []
    child_rows: list[dict[str, Any]] = []
    successful_groups: list[str] = []
    result_warnings: list[str] = []
    measured_children: list[ToolExecutionResult] = []

    for physical in logical_spec["physical_tasks"]:
        task_id = str(physical.get("task_id", "")).strip()
        group_label = str(physical.get("group_label", "")).strip()
        result = child_results.get(task_id)
        if result is None:
            result = ToolExecutionResult(
                tool_id=task_id,
                status="failed",
                exit_code=127,
                measurement=not_started_measurement(
                    threads=int(physical.get("threads", 1)),
                    ram_gb=float(physical.get("ram_gb", 1.0)),
                    requested_cpuset_cpus=(
                        tuple(logical_spec["resources"]["cpuset_cpus"])
                        if "cpuset_cpus" in logical_spec["resources"]
                        else None
                    ),
                ),
                network_path=None,
                progress_path=None,
                logs_path=None,
                error="Internal grouped task result is missing.",
            )
        measured_children.append(result)
        child_payload[task_id] = {
            **asdict(result),
            "group_label": group_label,
            "output_dir": str(physical.get("output_dir", "")),
        }
        for warning in result.warnings:
            text = f"[group:{group_label}] {warning}"
            if text not in result_warnings:
                result_warnings.append(text)

        if (
            result.status in {"completed", "completed_with_warnings"}
            and result.network_path
        ):
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
                            measurement=result.measurement,
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
            unexpected_contexts = sorted(
                {
                    str(row["context"])
                    for row in rows
                    if str(row["context"]) != "global"
                }
            )
            if unexpected_contexts:
                error = (
                    "group_emulated child output must use only the physical "
                    f"global context; found {unexpected_contexts}"
                )
                child_failures.append(
                    (
                        group_label,
                        ToolExecutionResult(
                            tool_id=task_id,
                            status="failed",
                            exit_code=result.exit_code,
                            measurement=result.measurement,
                            network_path=result.network_path,
                            progress_path=result.progress_path,
                            logs_path=result.logs_path,
                            error=error,
                        ),
                    )
                )
                child_payload[task_id]["status"] = "failed"
                child_payload[task_id]["error"] = error
                continue
            for row in rows:
                row["context"] = f"group:{group_label}"
            child_rows.extend(rows)
            successful_groups.append(group_label)
            continue
        child_failures.append((group_label, result))

    network_path: str | None = None
    parent_network = out_dir / "network.csv"
    if successful_groups:
        _write_network_rows(path=parent_network, rows=child_rows, include_tool_id=False)
        network_path = str(parent_network.resolve())

    failed_groups = [label for label, _result in child_failures]
    if child_failures and successful_groups:
        failure_summary = f"{len(child_failures)}/{len(logical_spec['physical_tasks'])} grouped executions failed"
        if failed_groups:
            failure_summary += f" ({', '.join(failed_groups)})"
        _append_warning_once(warnings, f"[{run_id}] {failure_summary}.")
        result_warnings.append(failure_summary + ".")
        status = "completed"
        error = None
    elif child_failures:
        failure_summary = "All grouped executions failed"
        if failed_groups:
            failure_summary += f" ({', '.join(failed_groups)})"
        status = "failed"
        error = failure_summary
    else:
        status = "completed"
        error = None
        _write_network_rows(path=parent_network, rows=child_rows, include_tool_id=False)
        network_path = str(parent_network.resolve())

    if status == "completed" and result_warnings:
        status = "completed_with_warnings"

    for warning in result_warnings:
        _append_warning_once(warnings, f"[{run_id}] {warning}")

    progress_payload = {
        "percent": 100,
        "status": status,
        "phase": (
            "done" if status in {"completed", "completed_with_warnings"} else "failed"
        ),
        "message": (
            f"{len(successful_groups)}/{len(logical_spec['physical_tasks'])} grouped executions completed"
        ),
        "warnings": result_warnings,
    }
    _write_json(progress_path, progress_payload)

    log_lines = [
        f"run_id={run_id}",
        f"status={status}",
        f"successful_groups={len(successful_groups)}",
        f"failed_groups={len(child_failures)}",
    ]
    for group_label, result in child_failures:
        reason = str(result.error or f"exit_code={result.exit_code}").strip()
        log_lines.append(f"[group:{group_label}] {reason}")
    for warning in result_warnings:
        log_lines.append(f"warning={warning}")
    logs_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    finished_monotonic_ns = time.perf_counter_ns()
    finished_at_utc = utc_now()
    measurement = _logical_measurement(
        child_results=measured_children,
        postprocess_started_monotonic_ns=postprocess_started_monotonic_ns,
        finished_monotonic_ns=finished_monotonic_ns,
        finished_at_utc=finished_at_utc,
        output_dir=out_dir,
    )

    logical_result = ToolExecutionResult(
        tool_id=run_id,
        status=status,
        exit_code=0 if status in {"completed", "completed_with_warnings"} else 1,
        measurement=measurement,
        network_path=network_path,
        progress_path=str(progress_path.resolve()),
        logs_path=str(logs_path.resolve()),
        error=error,
        warnings=tuple(result_warnings),
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
) -> Path:
    run_started_monotonic_ns = time.perf_counter_ns()
    run_started_at_utc = utc_now()
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
    output_profile = plan_payload.get("output_profile")
    if output_profile not in {"canonical", "full"}:
        raise ValueError("plan.json output_profile must be canonical or full")
    if run_report.get("output_profile") != output_profile:
        raise ValueError("run_report output_profile does not match plan.json")

    output_capabilities_by_run = validate_frozen_output_capabilities(
        run_report.get("tools"),
        label="run_report tools",
    )

    _selected_modes, waves, _total_eta = _load_plan_waves(plan_payload)
    _validate_wave_resource_schedule(plan_payload=plan_payload, waves=waves)
    logical_runs = _load_logical_runs_from_plan(plan_payload)
    planned_tasks_by_id = _validate_physical_task_plan(
        logical_runs=logical_runs,
        waves=waves,
    )
    state_writer = ExecutionStateWriter.initialize(
        run_dir=run_dir,
        run_id=run_dir.name,
        waves=waves,
        logical_runs=logical_runs,
        message="Execution plan loaded.",
    )
    state_writer.update_global(
        status="running",
        phase="verifying_inputs",
        percent=1,
        message="Verifying frozen run inputs.",
    )

    fingerprints = plan_payload.get("input_fingerprints", {})
    if not isinstance(fingerprints, dict) or not fingerprints:
        raise ValueError("plan.json missing input_fingerprints")
    runtime_contract_relpath = "input/runtime-input-contract.json"
    if runtime_contract_relpath not in fingerprints:
        raise ValueError(
            "plan.json input_fingerprints is missing the frozen runtime input contract"
        )
    _verify_input_fingerprints(run_dir=run_dir, fingerprints=fingerprints)
    runtime_input_contract_path = run_dir / "input" / "runtime-input-contract.json"
    if not runtime_input_contract_path.exists():
        raise ValueError(
            f"Missing frozen runtime input contract: {runtime_input_contract_path}"
        )
    frozen_runtime_input_contract = _load_json_object(
        runtime_input_contract_path,
        "runtime-input-contract",
    )
    state_writer.update_global(
        status="running",
        phase="preparing_runtime",
        percent=3,
        message="Preparing runtime inputs.",
    )

    runs_payload = preflight_report.get("runs", {})
    if not isinstance(runs_payload, dict):
        raise ValueError("Invalid preflight_report.runs")
    (
        selected_tools,
        selected_tool_catalog_ids,
        selected_tool_origins,
    ) = validate_selected_tool_identity_maps(
        runs_payload,
        label="preflight_report runs",
    )
    resolved_execution_by_tool = {
        str(k): v
        for k, v in runs_payload.get("resolved_execution", {}).items()
        if isinstance(k, str) and isinstance(v, dict)
    }
    resolved_resources_by_tool = {
        str(k): v
        for k, v in runs_payload.get("resolved_resources", {}).items()
        if isinstance(k, str) and isinstance(v, dict)
    }
    skipped_tools = {
        str(k): str(v)
        for k, v in runs_payload.get("skipped", {}).items()
        if isinstance(k, str)
    }

    tools_root, schemas_dir = _resolve_catalog_paths()
    constraints = _load_schema_constraints(schemas_dir)
    frozen_custom_tools = run_dir / "input" / "custom_tools.json"
    custom_tools, _custom_blocked_entries = load_custom_tool_registry(
        custom_tools_path=frozen_custom_tools if frozen_custom_tools.exists() else None,
        tools_root=tools_root,
        constraints=constraints,
    )
    if set(output_capabilities_by_run) != set(selected_tools):
        raise ValueError(
            "run_report tools.output_capabilities must match preflight selected runs"
        )
    if set(logical_runs) != set(selected_tools):
        raise ValueError("plan.json runs must match preflight selected runs exactly")
    if set(resolved_resources_by_tool) != set(selected_tools):
        raise ValueError(
            "preflight resolved_resources must match selected runs exactly"
        )
    frozen_manifest = run_dir / "input" / "dataset-manifest.json"
    dataset = _parse_dataset_context(
        dataset_manifest_path=frozen_manifest,
        constraints=constraints,
    )
    report_dataset = run_report.get("dataset")
    if not isinstance(report_dataset, dict):
        raise ValueError("run_report dataset must be an object")
    if report_dataset.get("id") != dataset.dataset_id:
        raise ValueError(
            "run_report dataset.id does not match the frozen dataset manifest"
        )
    reported_dataset_fingerprint = validate_dataset_fingerprint(
        report_dataset.get("fingerprint"),
        label="run_report dataset.fingerprint",
    )
    observed_dataset_fingerprint = fingerprint_dataset_content(
        expression_path=dataset.expression_matrix_path,
        extras=dataset.extras,
    )
    if reported_dataset_fingerprint != observed_dataset_fingerprint:
        raise ValueError(
            "run_report dataset.fingerprint does not match the frozen dataset inputs"
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
    active_extra_input_keys_by_run: dict[str, set[str]] = {}
    runtime_extra_input_keys_by_run: dict[str, set[str]] = {}
    expected_runtime_input_contract_by_run: dict[str, dict[str, Any]] = {}
    available_extra_inputs = {
        input_key for input_key, path in dataset.extras.items() if path is not None
    }
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
        expected_origin = "custom" if catalog_tool_id in custom_tools else "catalog"
        if selected_tool_origins.get(run_id) != expected_origin:
            raise ValueError(
                f"preflight report tool_origin for {run_id!r} must be "
                f"{expected_origin!r}"
            )
        logical_spec = logical_runs[run_id]
        if logical_spec["tool_id"] != catalog_tool_id:
            raise ValueError(
                f"plan.json tool_id for {run_id!r} must be {catalog_tool_id!r}"
            )
        if logical_spec["tool_origin"] != expected_origin:
            raise ValueError(
                f"plan.json tool_origin for {run_id!r} must be {expected_origin!r}"
            )
        resolved_execution = resolved_execution_by_tool.get(run_id, {})
        if logical_spec["execution"] != resolved_execution:
            raise ValueError(
                f"plan.json execution for {run_id!r} does not match the frozen "
                "preflight execution"
            )
        resolved_resources = resolved_resources_by_tool.get(run_id, {})
        if logical_spec["resources"] != resolved_resources:
            raise ValueError(
                f"plan.json resources for {run_id!r} do not match the frozen "
                "preflight resource request"
            )
        requested_cpuset_raw = resolved_resources.get("cpuset_cpus")
        requested_cpuset = (
            normalize_cpuset_cpus(
                requested_cpuset_raw,
                source=f"[{run_id}] resources.cpuset_cpus",
            )
            if requested_cpuset_raw is not None
            else None
        )
        if requested_cpuset is not None:
            validate_cpuset_available(
                requested_cpuset,
                source=f"[{run_id}] resources.cpuset_cpus",
            )
        if expected_origin == "custom":
            if toolspec.get("_andrea_run_id") != run_id:
                raise ValueError(
                    f"Custom tool {catalog_tool_id!r} is not bound to run_id "
                    f"{run_id!r}"
                )
            required_execution = {"mode": toolspec.get("_andrea_execution_mode")}
            if resolved_execution_by_tool.get(run_id) != required_execution:
                raise ValueError(
                    f"Custom tool {catalog_tool_id!r} requires execution "
                    f"{required_execution!r}"
                )
        expected_capability = _build_output_capability_snapshot(
            run_id=run_id,
            catalog_tool_id=catalog_tool_id,
            tool_origin=expected_origin,
            toolspec=toolspec,
        )
        if output_capabilities_by_run[run_id] != expected_capability:
            raise ValueError(
                f"run_report output capability for {run_id!r} does not match "
                "the frozen tool definition"
            )
        runtime_input_snapshot = _build_runtime_input_snapshot(
            run_id=run_id,
            catalog_tool_id=catalog_tool_id,
            tool_origin=expected_origin,
            toolspec=toolspec,
            resolved_params=resolved_params_by_tool[run_id],
            resolved_execution=resolved_execution,
            available_extra_inputs=available_extra_inputs,
        )
        expected_runtime_input_contract_by_run[run_id] = runtime_input_snapshot
        active_extra_input_keys_by_run[run_id] = set(
            runtime_input_snapshot["active_extra_inputs"]
        )
        runtime_extra_input_keys_by_run[run_id] = set(
            runtime_input_snapshot["mounted_extra_inputs"]
        )
        _parse_execution_capabilities(tool_id=run_id, toolspec=toolspec)
        threading = resolve_tool_threading(
            tool_id=run_id,
            toolspec=toolspec,
        )
        expected_image = str(toolspec.get("docker_image", "")).strip()
        for physical in logical_spec["physical_tasks"]:
            if not isinstance(physical, dict):
                continue
            task_id = str(physical.get("task_id", "")).strip()
            if not task_id:
                continue
            planned_task = planned_tasks_by_id[task_id]
            if planned_task.image != expected_image:
                compatibility_blocks.setdefault(run_id, []).append(
                    f"task '{task_id}' image does not match toolspec.docker_image."
                )
            expected_network_disabled = expected_origin == "custom"
            if planned_task.network_disabled != expected_network_disabled:
                compatibility_blocks.setdefault(run_id, []).append(
                    f"task '{task_id}' network isolation does not match its tool origin."
                )
            if not thread_count_allowed_by_tool(threading, int(planned_task.threads)):
                compatibility_blocks.setdefault(run_id, []).append(
                    "planned runtime threads are incompatible with "
                    "toolspec.runtime_resources.threading: "
                    f"task '{task_id}' has threads={planned_task.threads}, "
                    f"supported={threading.supported}, max_threads={threading.max_threads}."
                )
            requested_threads = resolved_resources.get("threads")
            if (
                requested_threads is not None
                and int(planned_task.threads) != int(requested_threads)
            ):
                compatibility_blocks.setdefault(run_id, []).append(
                    f"task '{task_id}' has threads={planned_task.threads}, but the "
                    f"frozen per-run request requires threads={requested_threads}."
                )
            requested_ram_gb = resolved_resources.get("ram_gb")
            if requested_ram_gb is not None and round(
                float(planned_task.ram_gb), 3
            ) != round(float(requested_ram_gb), 3):
                compatibility_blocks.setdefault(run_id, []).append(
                    f"task '{task_id}' has ram_gb={planned_task.ram_gb}, but the "
                    f"frozen per-run request requires ram_gb={requested_ram_gb}."
                )
            if planned_task.cpuset_cpus != requested_cpuset:
                compatibility_blocks.setdefault(run_id, []).append(
                    f"task '{task_id}' CPU affinity does not exactly match the "
                    "frozen per-run resources.cpuset_cpus request."
                )
        rule_blocks, rule_warnings, rule_errors = _collect_compatibility_rule_issues(
            tool_id=run_id,
            toolspec=toolspec,
            dataset=dataset,
            resolved_params=resolved_params_by_tool[run_id],
            resolved_execution=resolved_execution,
            warning_prefix=run_id,
        )
        if rule_errors:
            compatibility_blocks.setdefault(run_id, []).extend(
                f"invalid compatibility rule: {message}" for message in rule_errors
            )
        elif rule_blocks:
            compatibility_blocks.setdefault(run_id, []).extend(rule_blocks)
        compatibility_warnings.extend(rule_warnings)
        issues = _collect_conditional_input_issues(
            tool_id=run_id,
            toolspec=toolspec,
            dataset=dataset,
            resolved_params=resolved_params_by_tool[run_id],
            resolved_execution=resolved_execution,
        )
        if issues:
            conditional_input_errors[run_id] = issues

    expected_runtime_input_contract = {
        "schema_version": RUNTIME_INPUT_CONTRACT_SCHEMA_VERSION,
        "runs": expected_runtime_input_contract_by_run,
    }
    if frozen_runtime_input_contract != expected_runtime_input_contract:
        state_writer.update_global(
            status="failed",
            phase="failed",
            percent=100,
            message="Frozen runtime input contract does not match tool definitions.",
        )
        raise ValueError(
            "Frozen runtime input contract does not match the selected tools, "
            "resolved parameters, and execution modes. Re-run planning."
        )

    if compatibility_blocks:
        error_lines: list[str] = []
        for run_id in sorted(compatibility_blocks):
            for message in compatibility_blocks[run_id]:
                error_lines.append(f"[{run_id}] {message}")
        state_writer.update_global(
            status="failed",
            phase="failed",
            percent=100,
            message="Execution blocked by tool compatibility rules.",
        )
        raise ValueError(
            "Execution blocked by tool compatibility rules:\n" + "\n".join(error_lines)
        )

    if conditional_input_errors:
        error_lines: list[str] = []
        for run_id in sorted(conditional_input_errors):
            for message in conditional_input_errors[run_id]:
                error_lines.append(f"[{run_id}] {message}")
        state_writer.update_global(
            status="failed",
            phase="failed",
            percent=100,
            message="Execution blocked by missing conditional inputs.",
        )
        raise ValueError(
            "Execution blocked by missing conditional inputs:\n"
            + "\n".join(error_lines)
        )

    report_issues = [
        issue
        for issue in run_report.get("issues", [])
        if isinstance(issue, dict) and issue.get("code") != "planning_warning"
    ]
    warnings = list(compatibility_warnings)
    warning_state_cursor = _sync_warning_messages_to_state(
        state_writer=state_writer,
        warnings=warnings,
        cursor=0,
    )
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

    has_group_aggregated = any(
        str(logical_spec["execution"].get("mode", "")).strip() == "group_aggregated"
        for logical_spec in logical_runs.values()
    )
    required_group_labels = {
        str(physical.get("group_label", "")).strip()
        for logical_spec in logical_runs.values()
        if str(logical_spec["execution"].get("mode", "")).strip() == "group_emulated"
        for physical in logical_spec["physical_tasks"]
        if physical.get("group_label") is not None
    }
    group_expression_sources: dict[str, Path] = {}
    group_to_columns: dict[str, list[str]] = {}
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
    if has_group_aggregated:
        groups_path = dataset.extras.get("groups")
        if groups_path is None:
            raise ValueError(
                "Execution requires groups.tsv because at least one run uses execution.mode=group_aggregated."
            )
        expression_columns = _read_expression_columns(shared_expression)
        _group_order, group_to_columns = _load_groups_by_column(
            groups_path=groups_path,
            expression_columns=expression_columns,
        )

    expression_columns = _read_expression_columns(shared_expression)
    allowed_contexts_by_run = _planned_contexts_by_run(
        logical_runs=logical_runs,
        dataset=dataset,
        expression_columns=expression_columns,
        active_extra_input_keys_by_run=active_extra_input_keys_by_run,
        group_to_columns=group_to_columns,
    )

    # Keep a valid previous execution intact until every environment and input
    # precheck that precedes runtime materialization has succeeded.
    _clear_previous_execution_artifacts(
        run_dir=run_dir,
        logical_runs=logical_runs,
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
            physical_execution = _runtime_execution_for_logical_run(
                logical_spec["execution"]
            )
            runtime_io_by_tool[task_id] = _prepare_tool_runtime_io(
                run_dir=run_dir,
                tool_id=task_id,
                run_id=logical_run_id,
                output_dir=str(physical.get("output_dir", "")),
                resolved_params=resolved_params,
                resolved_execution=physical_execution,
                shared_expression=shared_expression,
                shared_extras=shared_extras,
                extra_input_keys=runtime_extra_input_keys_by_run.get(logical_run_id),
                expression_source=expression_source,
            )

    pulled_images = set()
    for wave in waves:
        state_writer.start_wave(wave.index)
        wave_results = _run_wave(
            wave=wave,
            runtime_io_by_tool=runtime_io_by_tool,
            pulled_images=pulled_images,
            poll_interval_s=progress_poll_seconds,
            warnings=runtime_warnings,
            state_writer=state_writer,
        )
        for result in wave_results.values():
            state_writer.mark_tool_result(result)
        physical_results.update(wave_results)
        state_writer.complete_wave(wave.index)

    state_writer.update_global(
        status="running",
        phase="collecting_results",
        percent=70,
        message="Collecting tool execution results.",
    )

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
    warning_state_cursor = _sync_warning_messages_to_state(
        state_writer=state_writer,
        warnings=warnings,
        cursor=warning_state_cursor,
    )

    logical_results: dict[str, ToolExecutionResult] = {}
    logical_results_payload: dict[str, Any] = {}
    status_by_tool: dict[str, str] = {}
    for logical_run_id in selected_tools:
        logical_spec = logical_runs[logical_run_id]
        physical_tasks = logical_spec["physical_tasks"]
        execution_mode = str(logical_spec["execution"].get("mode", "")).strip()
        if execution_mode == "group_aggregated":
            state_writer.update_global(
                status="running",
                phase="finalizing_group_aggregated",
                percent=72,
                message=f"Aggregating column-native output for {logical_run_id}.",
            )
            upstream_children = {
                str(physical.get("task_id", "")).strip(): physical_results.get(
                    str(physical.get("task_id", "")).strip()
                )
                for physical in physical_tasks
            }
            logical_result, logical_payload = _finalize_group_aggregated_logical_run(
                run_dir=run_dir,
                run_id=logical_run_id,
                logical_spec=logical_spec,
                child_results={
                    task_id: result
                    for task_id, result in upstream_children.items()
                    if result is not None
                },
                group_to_columns=group_to_columns,
                warnings=warnings,
            )
            logical_results[logical_run_id] = logical_result
            logical_results_payload[logical_run_id] = logical_payload
            status_by_tool[logical_run_id] = logical_result.status
            state_writer.mark_logical_result(logical_result)
            warning_state_cursor = _sync_warning_messages_to_state(
                state_writer=state_writer,
                warnings=warnings,
                cursor=warning_state_cursor,
            )
            continue
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
                    measurement=not_started_measurement(
                        threads=int(physical_tasks[0].get("threads", 1)),
                        ram_gb=float(physical_tasks[0].get("ram_gb", 1.0)),
                        requested_cpuset_cpus=(
                            tuple(logical_spec["resources"]["cpuset_cpus"])
                            if "cpuset_cpus" in logical_spec["resources"]
                            else None
                        ),
                    ),
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
            state_writer.mark_logical_result(result)
            continue

        state_writer.update_global(
            status="running",
            phase="finalizing_grouped",
            percent=72,
            message=f"Combining grouped outputs for {logical_run_id}.",
        )
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
            warnings=warnings,
        )
        logical_results[logical_run_id] = logical_result
        logical_results_payload[logical_run_id] = logical_payload
        status_by_tool[logical_run_id] = logical_result.status
        state_writer.mark_logical_result(logical_result)
        warning_state_cursor = _sync_warning_messages_to_state(
            state_writer=state_writer,
            warnings=warnings,
            cursor=warning_state_cursor,
        )

    state_writer.update_global(
        status="running",
        phase="merging_raw_networks",
        percent=78,
        message="Merging logical network outputs.",
    )

    def update_postprocessing_progress(
        phase: str,
        percent: int,
        message: str,
    ) -> None:
        state_writer.update_global(
            status="running",
            phase=phase,
            percent=percent,
            message=message,
        )

    (
        execution_results,
        per_tool_rows,
        merged_raw_path,
        merged_norm_path,
    ) = _merge_network_outputs(
        run_dir=run_dir,
        execution_results=logical_results,
        output_capabilities=output_capabilities_by_run,
        warnings=warnings,
        allowed_contexts=allowed_contexts_by_run,
        progress_callback=update_postprocessing_progress,
    )
    for result in execution_results.values():
        state_writer.mark_logical_result(result)
    warning_state_cursor = _sync_warning_messages_to_state(
        state_writer=state_writer,
        warnings=warnings,
        cursor=warning_state_cursor,
    )
    state_writer.update_global(
        status="running",
        phase="normalizing_scores",
        percent=90,
        message="Merged raw outputs and normalized tool scores.",
    )

    merged_raw_gexf_path: Path | None = None
    merged_raw_graphml_path: Path | None = None
    merged_norm_gexf_path: Path | None = None
    merged_norm_graphml_path: Path | None = None
    merged_norm_cytoscape_script_path: Path | None = None

    if output_profile == "full" and merged_raw_path is not None:
        merged_raw_gexf_path = run_dir / "merged_network_raw.gexf"
        state_writer.update_global(
            status="running",
            phase="exporting_artifacts",
            percent=90,
            message=f"Exporting {merged_raw_gexf_path.name}.",
        )
        export_network_gexf(merged_raw_path, merged_raw_gexf_path)
        merged_raw_graphml_path = run_dir / "merged_network_raw.graphml"
        state_writer.update_global(
            status="running",
            phase="exporting_artifacts",
            percent=91,
            message=f"Exporting {merged_raw_graphml_path.name}.",
        )
        export_network_graphml(merged_raw_path, merged_raw_graphml_path)

    if output_profile == "full" and merged_norm_path is not None:
        merged_norm_gexf_path = run_dir / "merged_network_normalized.gexf"
        state_writer.update_global(
            status="running",
            phase="exporting_artifacts",
            percent=93,
            message=f"Exporting {merged_norm_gexf_path.name}.",
        )
        export_network_gexf(merged_norm_path, merged_norm_gexf_path)
        merged_norm_graphml_path = run_dir / "merged_network_normalized.graphml"
        state_writer.update_global(
            status="running",
            phase="exporting_artifacts",
            percent=94,
            message=f"Exporting {merged_norm_graphml_path.name}.",
        )
        export_network_graphml(merged_norm_path, merged_norm_graphml_path)
        merged_norm_cytoscape_script_path = (
            run_dir / "merged_network_normalized_cytoscape.py"
        )
        state_writer.update_global(
            status="running",
            phase="exporting_artifacts",
            percent=96,
            message=f"Exporting {merged_norm_cytoscape_script_path.name}.",
        )
        export_cytoscape_style_script(
            csv_path=merged_norm_path,
            graphml_path=merged_norm_graphml_path,
            out_path=merged_norm_cytoscape_script_path,
        )

    state_writer.update_global(
        status="running",
        phase="writing_report",
        percent=97,
        message="Writing final run_report.json.",
    )
    completed_tools = sorted(
        tool_id
        for tool_id, result in execution_results.items()
        if result.status in {"completed", "completed_with_warnings"}
    )
    failed_tools = {
        tool_id: (result.error or "unknown error")
        for tool_id, result in execution_results.items()
        if result.status not in {"completed", "completed_with_warnings"}
    }
    completed_contexts = {
        run_id: _completed_contexts_for_run(
            run_id=run_id,
            logical_spec=logical_runs[run_id],
            logical_payload=logical_results_payload[run_id],
            planned_contexts=allowed_contexts_by_run[run_id],
        )
        for run_id in completed_tools
    }
    for logical_run_id, result in execution_results.items():
        status_by_tool[logical_run_id] = result.status
        if logical_run_id in logical_results_payload:
            logical_results_payload[logical_run_id].update(asdict(result))
    logical_results_payload = _relativize_result_payload(
        logical_results_payload,
        base_dir=run_dir,
    )

    run_finished_monotonic_ns = time.perf_counter_ns()
    run_finished_at_utc = utc_now()
    physical_finish_times = [
        value
        for result in physical_results.values()
        if isinstance(
            (value := result.measurement.get("task_finished_monotonic_ns")), int
        )
        and not isinstance(value, bool)
    ]
    final_postprocess_started_ns = max(
        physical_finish_times,
        default=run_started_monotonic_ns,
    )
    execution_measurement = _logical_measurement(
        child_results=physical_results.values(),
        postprocess_started_monotonic_ns=final_postprocess_started_ns,
        finished_monotonic_ns=run_finished_monotonic_ns,
        finished_at_utc=run_finished_at_utc,
        output_dir=run_dir,
    )
    execution_measurement.update(
        {
            "scope": "andrea_execution",
            "wall_time_seconds": round(
                (run_finished_monotonic_ns - run_started_monotonic_ns)
                / 1_000_000_000.0,
                6,
            ),
            "task_started_at_utc": run_started_at_utc,
            "task_started_monotonic_ns": run_started_monotonic_ns,
        }
    )
    execution_measurement["telemetry"]["cpu_semantics"] = (
        "sum_of_physical_container_cpu_excludes_andrea_host_process"
    )
    execution_measurement["telemetry"]["wall_semantics"] = (
        "andrea_execution_end_to_end_including_input_verification_preparation_"
        "scheduling_merging_and_requested_output_materialization"
    )
    execution_measurement["telemetry"]["memory_semantics"] = (
        "overlap_upper_bound_from_physical_container_peaks_excludes_andrea_host_process"
    )
    run_report["status"] = "executed"
    run_report["tools"] = {
        "selected": selected_tools,
        "catalog_tool_ids": selected_tool_catalog_ids,
        "tool_origins": selected_tool_origins,
        "output_capabilities": output_capabilities_by_run,
        "skipped": skipped_tools,
        "status_by_tool": status_by_tool,
        "completed": completed_tools,
        "completed_contexts": completed_contexts,
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
            "measurement": execution_measurement,
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
    state_writer.update_global(
        status="completed" if not failed_tools else "completed_with_failures",
        phase="completed" if not failed_tools else "completed_with_failures",
        percent=100,
        message=(
            "Execution completed."
            if not failed_tools
            else f"Execution completed with {len(failed_tools)} failed run(s)."
        ),
    )

    print(f"infer-network execution completed: {run_dir}")
    print(f"  selected tools: {len(selected_tools)}")
    print(f"  skipped tools: {len(skipped_tools)}")
    print(f"  completed tools: {len(completed_tools)}")
    print(f"  failed tools: {len(failed_tools)}")
    print(
        "  elapsed time: "
        f"{float(execution_measurement['wall_time_seconds']):.2f}s"
    )
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
    warning_count = len(
        issue_messages(
            [
                issue
                for issue in report_issues
                if issue.get("code") != "planning_warning"
            ],
            severity="warn",
        )
    )
    if warning_count:
        print(f"  warnings: {warning_count} (see run_report.json)")

    if not completed_tools:
        state_writer.update_global(
            status="failed",
            phase="failed",
            percent=100,
            message="All tool executions failed.",
        )
        raise ValueError(
            "All tool executions failed. See run_report.json and per-tool container.log files."
        )
    return run_dir
