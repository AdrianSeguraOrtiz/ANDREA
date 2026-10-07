from __future__ import annotations

from dataclasses import replace

from andrea.core.commands.infer_network.commons.resource_decisions import (
    execution_resource_outcome,
    planned_logical_resource_decision,
    planned_task_resource_decision,
)
from andrea.core.commands.infer_network.commons.shared import (
    ToolExecutionResult,
    ToolPlanItem,
)


def _task(*, eta: float, timeout: float | None) -> ToolPlanItem:
    return ToolPlanItem(
        tool_id="tool_01",
        run_id="tool_01",
        image="example/tool:1.0",
        threads=4,
        ram_gb=8.0,
        eta_seconds=eta,
        eta_source="cost_profile",
        output_dir="tools/tool_01",
        timeout_seconds=timeout,
    )


def _result(
    *,
    status: str = "failed",
    exit_code: int = 1,
    telemetry: dict | None = None,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_id="tool_01",
        status=status,
        exit_code=exit_code,
        measurement={"telemetry": telemetry or {}},
        network_path=None,
        progress_path=None,
        logs_path=None,
        error=None if status.startswith("completed") else "failed",
    )


def test_planning_decision_compares_container_eta_with_timeout() -> None:
    eligible = planned_task_resource_decision(_task(eta=119.0, timeout=120.0))
    infeasible = planned_task_resource_decision(_task(eta=121.0, timeout=120.0))

    assert eligible["status"] == "eligible"
    assert eligible["estimated_timeout_ratio"] == 0.991667
    assert infeasible["status"] == "estimated_infeasible_time"
    assert infeasible["reason"] == "estimated_runtime_exceeds_timeout"


def test_fallback_eta_cannot_exclude_a_tool_without_empirical_evidence() -> None:
    fallback_sources = (
        "fallback_no_cost",
        "fallback_no_matching_cost_profile",
        "fallback_requested_threads_without_cost_point",
        "fallback_no_usable_runtime_point",
    )
    for eta_source in fallback_sources:
        task = replace(
            _task(eta=10_000.0, timeout=120.0),
            eta_source=eta_source,
        )

        decision = planned_task_resource_decision(task)

        assert decision["status"] == "eligible"
        assert decision["eta_source"] == eta_source
        assert decision["evidence"] == "uncalibrated_heuristic"
        assert decision["reason"] == (
            "fallback_estimate_exceeds_timeout_without_empirical_evidence"
        )


def test_planning_decision_excludes_host_aggregation_from_container_budget() -> None:
    task = replace(
        _task(eta=130.0, timeout=120.0),
        eta_provenance={"group_aggregation": {"eta_seconds": 15.0}},
    )

    decision = planned_task_resource_decision(task)

    assert decision["status"] == "eligible"
    assert decision["estimated_seconds"] == 115.0


def test_logical_planning_decision_lists_infeasible_children() -> None:
    first = _task(eta=30.0, timeout=60.0)
    second = replace(
        _task(eta=90.0, timeout=60.0),
        tool_id="tool_01__group_02",
    )

    decision = planned_logical_resource_decision([first, second])

    assert decision["status"] == "estimated_infeasible_time"
    assert decision["infeasible_physical_tasks"] == ["tool_01__group_02"]


def test_execution_outcome_uses_typed_timeout_and_oom_evidence() -> None:
    assert (
        execution_resource_outcome(_result(status="completed", exit_code=0))["status"]
        == "completed"
    )
    assert execution_resource_outcome(_result(exit_code=124))["status"] == "timeout"
    assert (
        execution_resource_outcome(
            _result(exit_code=137, telemetry={"oom_killed": True})
        )["status"]
        == "memory_limit"
    )
    assert execution_resource_outcome(_result(exit_code=2))["status"] == (
        "runtime_failure"
    )
