from __future__ import annotations

import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from andrea.core.commands.infer_network.commons.planner import (
    _build_parallel_waves,
    _optimize_mode_selection_cp_sat,
)
from andrea.core.commands.infer_network.commons.resources import (
    normalize_cpuset_cpus,
    parse_linux_cpuset,
    validate_cpuset_available,
)
from andrea.core.commands.infer_network.commons.shared import (
    ToolExecutionResult,
    ToolPlanItem,
)
from andrea.core.commands.infer_network.commons.telemetry import (
    ContainerTelemetrySampler,
)
from andrea.core.commands.infer_network.run import _logical_measurement


def _result(
    tool_id: str,
    *,
    started: int,
    finished: int,
    wall: float,
    cpu: float,
    memory: int,
    cpuset: list[int],
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_id=tool_id,
        status="completed",
        exit_code=0,
        measurement={
            "wall_time_seconds": wall,
            "cpu_time_seconds": cpu,
            "peak_memory_bytes": memory,
            "task_started_at_utc": "2026-01-01T00:00:00Z",
            "task_started_monotonic_ns": started,
            "task_finished_monotonic_ns": finished,
            "assigned_resources": {
                "threads": len(cpuset),
                "ram_limit_bytes": 1024,
                "cpuset_cpus_requested": cpuset,
                "cpuset_cpus_effective": cpuset,
            },
            "telemetry": {"status": "complete"},
        },
        network_path=None,
        progress_path=None,
        logs_path=None,
        error=None,
    )


def _task(tool_id: str, cpuset: tuple[int, ...] | None) -> ToolPlanItem:
    return ToolPlanItem(
        tool_id=tool_id,
        run_id=tool_id,
        image="example/tool:1.0",
        threads=2,
        ram_gb=1.0,
        eta_seconds=1.0,
        eta_source="test",
        output_dir=f"tools/{tool_id}",
        cpuset_cpus=cpuset,
    )


def test_cpuset_contract_is_canonical_and_available() -> None:
    assert normalize_cpuset_cpus([0, 2, 5], source="test") == (0, 2, 5)
    assert parse_linux_cpuset("0-2,5,7-8") == (0, 1, 2, 5, 7, 8)
    assert validate_cpuset_available((2, 5), source="test", available=(0, 2, 5)) == (
        2,
        5,
    )
    for invalid in ([1, 1], [2, 1], [True], [], "0,1"):
        with pytest.raises(ValueError):
            normalize_cpuset_cpus(invalid, source="test")
    with pytest.raises(ValueError, match="outside the effective process affinity"):
        validate_cpuset_available((3,), source="test", available=(0, 1, 2))


def test_docker_launch_applies_exact_threads_and_cpuset() -> None:
    from andrea.core.commands.infer_network.commons import runtime_helpers

    with (
        tempfile.TemporaryDirectory() as tmp,
        patch.object(
            runtime_helpers,
            "_run_cmd",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="container-id\n", stderr=""
            ),
        ) as run_cmd,
    ):
        runtime_helpers._docker_run_detached(
            image="example/tool:1.0",
            io_dir=Path(tmp),
            threads=4,
            ram_gb=1024.0,
            cpuset_cpus=(2, 3, 6, 7),
        )

    command = run_cmd.call_args.args[0]
    assert command[command.index("--cpus") + 1] == "4"
    assert command[command.index("--memory") + 1] == "1024g"
    assert command[command.index("--cpuset-cpus") + 1] == "2,3,6,7"
    assert command[command.index("--threads") + 1] == "4"


def test_scheduler_never_overlaps_reserved_or_unpinned_cpu_sets() -> None:
    disjoint, _ = _build_parallel_waves(
        items=[_task("a", (0, 1)), _task("b", (2, 3))],
        max_cores=4,
        max_ram_gb=4.0,
    )
    overlapping, _ = _build_parallel_waves(
        items=[_task("a", (0, 1)), _task("b", (1, 2))],
        max_cores=4,
        max_ram_gb=4.0,
    )
    mixed, _ = _build_parallel_waves(
        items=[_task("a", (0, 1)), _task("b", None)],
        max_cores=4,
        max_ram_gb=4.0,
    )

    assert len(disjoint) == 1
    assert len(overlapping) == 2
    assert len(mixed) == 2

    cp_sat_warnings: list[str] = []
    cp_sat = _optimize_mode_selection_cp_sat(
        mode_options_by_tool={
            "a": [_task("a", (0, 1))],
            "b": [_task("b", (1, 2))],
        },
        max_cores=4,
        max_ram_gb=4.0,
        time_limit_seconds=5.0,
        warnings=cp_sat_warnings,
    )
    assert cp_sat is not None
    assert len(cp_sat[1]) == 2
    assert cp_sat_warnings == []


def test_scheduler_rejects_items_outside_resource_budget() -> None:
    with pytest.raises(ValueError, match="planned threads=2"):
        _build_parallel_waves(
            items=[_task("a", None)],
            max_cores=1,
            max_ram_gb=4.0,
        )
    oversized_ram = replace(_task("a", None), threads=1, ram_gb=2.0)
    with pytest.raises(ValueError, match="planned ram_gb=2.0"):
        _build_parallel_waves(
            items=[oversized_ram],
            max_cores=2,
            max_ram_gb=1.0,
        )


def test_container_sampler_reads_cgroup_cpu_memory_and_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "cpu.stat").write_text(
            "usage_usec 2500000\nthrottled_usec 125000\n", encoding="utf-8"
        )
        (root / "memory.peak").write_text("4096\n", encoding="utf-8")
        (root / "memory.current").write_text("1024\n", encoding="utf-8")
        (root / "memory.events").write_text("oom 0\noom_kill 0\n", encoding="utf-8")
        output = root / "out"
        output.mkdir()
        (output / "network.csv").write_bytes(b"12345")
        sampler = ContainerTelemetrySampler(
            container_id="container-id",
            assigned_threads=2,
            assigned_ram_gb=1.0,
            requested_cpuset_cpus=None,
            task_started_monotonic_ns=1_000_000_000,
            task_started_at_utc="2026-01-01T00:00:00Z",
            container_started_at_utc="2026-01-01T00:00:00.500000Z",
            _v2_path=root,
        )
        sampler.sample()
        with patch(
            "andrea.core.commands.infer_network.commons.telemetry._docker_inspect",
            return_value={"State": {"FinishedAt": "2026-01-01T00:00:03Z"}},
        ):
            measurement = sampler.finish(
                task_finished_monotonic_ns=4_000_000_000,
                task_finished_at_utc="2026-01-01T00:00:03Z",
                output_dir=output,
            )

    assert measurement["wall_time_seconds"] == 3.0
    assert measurement["scope"] == "physical_task"
    assert measurement["container_wall_time_seconds"] == 2.5
    assert measurement["cpu_time_seconds"] == 2.5
    assert measurement["peak_memory_bytes"] == 4096
    assert measurement["output_bytes"] == 5
    assert measurement["telemetry"]["status"] == "complete"
    assert measurement["telemetry"]["oom_events"] == 0
    assert measurement["telemetry"]["oom_kill_events"] == 0
    assert measurement["telemetry"]["oom_killed"] is False
    assert measurement["telemetry"]["wall_source"] == "andrea_monotonic_clock"
    assert (
        measurement["telemetry"]["container_wall_source"]
        == "docker_state_timestamps"
    )


def test_requested_cpuset_must_be_observable_and_exact() -> None:
    base_args = {
        "container_id": "container-id",
        "assigned_threads": 1,
        "assigned_ram_gb": 1.0,
        "requested_cpuset_cpus": (0,),
        "task_started_monotonic_ns": 1,
        "task_started_at_utc": "2026-01-01T00:00:00Z",
    }
    with (
        patch(
            "andrea.core.commands.infer_network.commons.telemetry._docker_inspect",
            return_value=None,
        ),
        pytest.raises(RuntimeError, match="cannot be verified"),
    ):
        ContainerTelemetrySampler.attach(**base_args)

    with (
        patch(
            "andrea.core.commands.infer_network.commons.telemetry._docker_inspect",
            return_value={
                "State": {"Pid": 0},
                "HostConfig": {"CpusetCpus": "1"},
            },
        ),
        pytest.raises(RuntimeError, match="did not preserve"),
    ):
        ContainerTelemetrySampler.attach(**base_args)


def test_logical_wall_is_makespan_and_child_wall_sum_is_separate(
    tmp_path: Path,
) -> None:
    children = [
        _result(
            "a",
            started=1_000_000_000,
            finished=5_000_000_000,
            wall=4.0,
            cpu=3.0,
            memory=100,
            cpuset=[0, 1],
        ),
        _result(
            "b",
            started=2_000_000_000,
            finished=6_000_000_000,
            wall=4.0,
            cpu=2.0,
            memory=200,
            cpuset=[2, 3],
        ),
    ]
    measurement = _logical_measurement(
        child_results=children,
        postprocess_started_monotonic_ns=6_000_000_000,
        finished_monotonic_ns=7_000_000_000,
        finished_at_utc="2026-01-01T00:00:06Z",
        output_dir=tmp_path,
    )

    assert measurement["wall_time_seconds"] == 6.0
    assert measurement["child_wall_time_sum_seconds"] == 8.0
    assert measurement["postprocess_wall_time_seconds"] == 1.0
    assert measurement["cpu_time_seconds"] == 5.0
    assert measurement["peak_memory_bytes"] == 300
    assert measurement["assigned_resources"]["threads_peak"] == 4
    assert measurement["assigned_resources"]["cpuset_cpus_effective_sets"] == [
        [0, 1],
        [2, 3],
    ]
    assert "input_preparation_is_excluded" in measurement["telemetry"][
        "wall_semantics"
    ]


def test_logical_peak_does_not_treat_touching_intervals_as_overlapping(
    tmp_path: Path,
) -> None:
    children = [
        _result(
            "a",
            started=1_000_000_000,
            finished=3_000_000_000,
            wall=2.0,
            cpu=1.0,
            memory=100,
            cpuset=[0],
        ),
        _result(
            "b",
            started=3_000_000_000,
            finished=5_000_000_000,
            wall=2.0,
            cpu=1.0,
            memory=200,
            cpuset=[1],
        ),
    ]

    measurement = _logical_measurement(
        child_results=children,
        postprocess_started_monotonic_ns=5_000_000_000,
        finished_monotonic_ns=5_000_000_000,
        finished_at_utc="2026-01-01T00:00:05Z",
        output_dir=tmp_path,
    )

    assert measurement["wall_time_seconds"] == 4.0
    assert measurement["child_wall_time_sum_seconds"] == 4.0
    assert measurement["peak_memory_bytes"] == 200
    assert measurement["assigned_resources"]["threads_peak"] == 1


def test_logical_peak_is_unavailable_when_any_child_peak_is_unknown(
    tmp_path: Path,
) -> None:
    known = _result(
        "known",
        started=1_000_000_000,
        finished=4_000_000_000,
        wall=3.0,
        cpu=1.0,
        memory=100,
        cpuset=[0],
    )
    unknown = _result(
        "unknown",
        started=2_000_000_000,
        finished=3_000_000_000,
        wall=1.0,
        cpu=0.5,
        memory=1,
        cpuset=[1],
    )
    unknown.measurement["peak_memory_bytes"] = None

    measurement = _logical_measurement(
        child_results=[known, unknown],
        postprocess_started_monotonic_ns=4_000_000_000,
        finished_monotonic_ns=4_000_000_000,
        finished_at_utc="2026-01-01T00:00:04Z",
        output_dir=tmp_path,
    )

    assert measurement["peak_memory_bytes"] is None
    assert measurement["telemetry"]["memory_source"] is None
    assert (
        measurement["telemetry"]["memory_semantics"]
        == "unavailable_when_any_child_peak_or_interval_is_unavailable"
    )
