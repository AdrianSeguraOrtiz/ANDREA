"""Canonical resource decisions for inference planning and execution.

The planner already estimates runtime and the runner already enforces Docker
resource limits.  This module turns those measurements into a small, stable
vocabulary that external orchestrators can consume without parsing warning or
error strings.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from .shared import ToolExecutionResult, ToolPlanItem

RESOURCE_DECISION_SCHEMA_VERSION = "1.0"
PLANNING_DECISIONS = {"eligible", "estimated_infeasible_time"}


def _container_eta_seconds(task: ToolPlanItem) -> float:
    """Return the ETA governed by the physical container timeout.

    ``group_aggregated`` planning adds host-side aggregation time to the task
    ETA.  The timeout applies only to the upstream container, so that addition
    must not make an otherwise feasible container look infeasible.
    """

    eta = max(0.0, float(task.eta_seconds))
    provenance = task.eta_provenance
    if not isinstance(provenance, dict):
        return eta
    aggregation = provenance.get("group_aggregation")
    if not isinstance(aggregation, dict):
        return eta
    aggregation_eta = aggregation.get("eta_seconds")
    if isinstance(aggregation_eta, bool) or not isinstance(
        aggregation_eta, (int, float)
    ):
        return eta
    return max(0.0, eta - float(aggregation_eta))


def planned_task_resource_decision(task: ToolPlanItem) -> dict[str, Any]:
    """Classify one frozen physical task against its wall-clock budget."""

    estimated_seconds = round(_container_eta_seconds(task), 3)
    timeout_seconds = (
        round(float(task.timeout_seconds), 3)
        if task.timeout_seconds is not None
        else None
    )
    evidence = {
        "cost_profile": "empirical_cost_profile",
        "cost_profile_extrapolated": "uncalibrated_extrapolation",
    }.get(task.eta_source, "uncalibrated_heuristic")
    status = "eligible"
    reason = "no_timeout_requested"
    if timeout_seconds is not None:
        if estimated_seconds > timeout_seconds and evidence == "empirical_cost_profile":
            status = "estimated_infeasible_time"
            reason = "estimated_runtime_exceeds_timeout"
        elif estimated_seconds > timeout_seconds:
            reason = "fallback_estimate_exceeds_timeout_without_empirical_evidence"
        elif evidence == "empirical_cost_profile":
            reason = "estimated_runtime_within_timeout"
        else:
            reason = "runtime_unknown_without_matching_calibration"

    decision = {
        "schema_version": RESOURCE_DECISION_SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "estimated_seconds": estimated_seconds,
        "timeout_seconds": timeout_seconds,
        "eta_source": str(task.eta_source),
        "evidence": evidence,
        "advisory": True,
    }
    if timeout_seconds is not None:
        decision["estimated_timeout_ratio"] = round(
            estimated_seconds / timeout_seconds, 6
        )
    return decision


def planned_logical_resource_decision(
    tasks: Iterable[ToolPlanItem],
) -> dict[str, Any]:
    """Aggregate physical planning decisions for one logical tool run."""

    physical = [
        {
            "task_id": task.tool_id,
            **planned_task_resource_decision(task),
        }
        for task in tasks
    ]
    infeasible = [
        item["task_id"]
        for item in physical
        if item["status"] == "estimated_infeasible_time"
    ]
    return {
        "schema_version": RESOURCE_DECISION_SCHEMA_VERSION,
        "status": ("estimated_infeasible_time" if infeasible else "eligible"),
        "reason": (
            "one_or_more_physical_tasks_exceed_timeout"
            if infeasible
            else "all_physical_tasks_eligible"
        ),
        "infeasible_physical_tasks": infeasible,
        "physical_tasks": physical,
        "advisory": True,
    }


def execution_resource_outcome(result: ToolExecutionResult) -> dict[str, Any]:
    """Classify a finished physical or logical execution from typed evidence."""

    if result.status == "completed_with_warnings":
        status = "completed_with_warnings"
        reason = "execution_completed_with_warnings"
    elif result.status == "completed":
        status = "completed"
        reason = "execution_completed"
    elif int(result.exit_code) == 124:
        status = "timeout"
        reason = "andrea_wall_clock_timeout"
    else:
        measurement = result.measurement
        telemetry = (
            measurement.get("telemetry", {}) if isinstance(measurement, dict) else {}
        )
        oom_killed = (
            bool(telemetry.get("oom_killed")) if isinstance(telemetry, dict) else False
        )
        oom_kill_events = (
            telemetry.get("oom_kill_events") if isinstance(telemetry, dict) else None
        )
        if oom_killed or (
            isinstance(oom_kill_events, int)
            and not isinstance(oom_kill_events, bool)
            and oom_kill_events > 0
        ):
            status = "memory_limit"
            reason = "container_cgroup_oom_kill"
        else:
            status = "runtime_failure"
            reason = "execution_failed_without_timeout_or_oom_evidence"

    return {
        "schema_version": RESOURCE_DECISION_SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "exit_code": int(result.exit_code),
    }


def result_payload_with_resource_outcome(
    result: ToolExecutionResult,
) -> dict[str, Any]:
    """Serialize a result with its canonical resource outcome."""

    return {
        **asdict(result),
        "resource_outcome": execution_resource_outcome(result),
    }
