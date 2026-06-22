from __future__ import annotations

import tempfile
from pathlib import Path

from andrea.core.commands.infer_network.commons.execution_state import (
    ExecutionStateWriter,
    EXECUTION_STATE_RELATIVE_PATH,
    build_initial_execution_state,
    execution_state_path,
    read_execution_state,
    read_execution_state_if_exists,
    validate_execution_state,
    write_execution_state,
)
from andrea.core.commands.infer_network.commons.shared import PlanWave, ToolPlanItem

from ._helpers import InferNetworkCoreTestCase


class InferNetworkExecutionStateTests(InferNetworkCoreTestCase):
    def _waves(self) -> list[PlanWave]:
        return [
            PlanWave(
                index=1,
                threads_used=3,
                ram_gb_used=6.0,
                eta_seconds=20.0,
                tasks=[
                    ToolPlanItem(
                        tool_id="genie3_01",
                        run_id="genie3_01",
                        image="andrea/genie3:test",
                        threads=1,
                        ram_gb=2.0,
                        eta_seconds=10.0,
                        eta_source="cost_profile",
                        output_dir="tools/genie3_01",
                    ),
                    ToolPlanItem(
                        tool_id="lioness_01__column_native",
                        run_id="lioness_01",
                        image="andrea/lioness:test",
                        threads=2,
                        ram_gb=4.0,
                        eta_seconds=20.0,
                        eta_source="cost_profile",
                        output_dir="tools/lioness_01__column_native",
                    ),
                ],
            ),
            PlanWave(
                index=2,
                threads_used=1,
                ram_gb_used=2.0,
                eta_seconds=8.0,
                tasks=[
                    ToolPlanItem(
                        tool_id="clr_01",
                        run_id="clr_01",
                        image="andrea/clr:test",
                        threads=1,
                        ram_gb=2.0,
                        eta_seconds=8.0,
                        eta_source="fallback_no_cost",
                        output_dir="tools/clr_01",
                    )
                ],
            ),
        ]

    def test_initial_execution_state_has_strict_shape(self) -> None:
        payload = build_initial_execution_state(
            run_id="gui_dataset_20260519T000000Z",
            waves=self._waves(),
        )

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["phase"], "planned")
        self.assertEqual(payload["percent"], 0)
        self.assertIsNone(payload["current_wave"])
        self.assertEqual(payload["waves_total"], 2)
        self.assertEqual(
            payload["summary"],
            {
                "total": 3,
                "queued": 3,
                "running": 0,
                "completed": 0,
                "failed": 0,
                "warnings": 0,
            },
        )
        self.assertEqual(
            payload["unit_summaries"],
            {
                "waves": {
                    "total": 2,
                    "queued": 2,
                    "running": 0,
                    "completed": 0,
                    "failed": 0,
                    "warnings": 0,
                    "errors": 0,
                },
                "configurations": {
                    "total": 3,
                    "queued": 3,
                    "running": 0,
                    "completed": 0,
                    "failed": 0,
                    "warnings": 0,
                    "errors": 0,
                },
                "executions": {
                    "total": 3,
                    "queued": 3,
                    "running": 0,
                    "completed": 0,
                    "failed": 0,
                    "warnings": 0,
                    "errors": 0,
                },
            },
        )
        self.assertEqual(payload["waves"][0]["tools"], ["genie3_01", "lioness_01__column_native"])
        self.assertEqual(payload["tools"]["lioness_01__column_native"]["run_id"], "lioness_01")
        self.assertEqual(payload["tools"]["lioness_01__column_native"]["wave"], 1)
        self.assertEqual(
            [
                event["phase"]
                for event in payload["phase_history"]
            ],
            ["planned"],
        )
        validate_execution_state(payload)

    def test_execution_state_path_uses_runtime_directory(self) -> None:
        run_dir = Path("/tmp/run")
        self.assertEqual(execution_state_path(run_dir), run_dir / EXECUTION_STATE_RELATIVE_PATH)

    def test_atomic_write_and_read_round_trip(self) -> None:
        payload = build_initial_execution_state(run_id="run_a", waves=self._waves())

        with tempfile.TemporaryDirectory() as tmp:
            path = execution_state_path(Path(tmp))
            self.assertIsNone(read_execution_state_if_exists(path))

            write_execution_state(path, payload)
            loaded = read_execution_state(path)

            self.assertEqual(loaded, payload)
            self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_validation_rejects_wrong_schema_version(self) -> None:
        payload = build_initial_execution_state(run_id="run_a", waves=self._waves())
        payload["schema_version"] = "2.0"

        with self.assertRaisesRegex(ValueError, "schema_version"):
            validate_execution_state(payload)

    def test_validation_rejects_unknown_status(self) -> None:
        payload = build_initial_execution_state(run_id="run_a", waves=self._waves())
        payload["tools"]["genie3_01"]["status"] = "unknown"

        with self.assertRaisesRegex(ValueError, "tools.genie3_01.status"):
            write_execution_state(Path(tempfile.gettempdir()) / "execution_state.json", payload)

    def test_writer_updates_wave_tool_and_logical_summary(self) -> None:
        logical_runs = {
            "genie3_01": {
                "run_id": "genie3_01",
                "tool_id": "genie3",
                "execution": {"mode": "global"},
                "physical_tasks": [{"task_id": "genie3_01"}],
            },
            "lioness_01": {
                "run_id": "lioness_01",
                "tool_id": "lioness",
                "execution": {"mode": "group_aggregated"},
                "physical_tasks": [{"task_id": "lioness_01__column_native"}],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            writer = ExecutionStateWriter.initialize(
                run_dir=Path(tmp),
                run_id="run_a",
                waves=self._waves(),
                logical_runs=logical_runs,
            )

            writer.start_wave(1)
            writer.update_tool(
                "genie3_01",
                status="completed",
                phase="done",
                percent=100,
                message="Finished",
            )
            writer.record_warning_message("[genie3_01] docker image was pulled")
            writer.update_tool(
                "lioness_01__column_native",
                status="failed",
                phase="failed",
                percent=100,
                message="synthetic failure",
                error="synthetic failure",
            )
            writer.complete_wave(1)
            loaded = read_execution_state(execution_state_path(Path(tmp)))

        self.assertEqual(loaded["waves"][0]["status"], "completed_with_failures")
        self.assertEqual(loaded["summary"]["completed"], 1)
        self.assertEqual(loaded["summary"]["failed"], 1)
        self.assertEqual(loaded["summary"]["warnings"], 1)
        self.assertEqual(
            loaded["unit_summaries"]["waves"],
            {
                "total": 2,
                "queued": 1,
                "running": 0,
                "completed": 1,
                "failed": 0,
                "warnings": 0,
                "errors": 1,
            },
        )
        self.assertEqual(loaded["unit_summaries"]["configurations"]["total"], 2)
        self.assertEqual(loaded["unit_summaries"]["configurations"]["completed"], 1)
        self.assertEqual(loaded["unit_summaries"]["configurations"]["failed"], 1)
        self.assertEqual(loaded["unit_summaries"]["configurations"]["warnings"], 1)
        self.assertEqual(loaded["unit_summaries"]["executions"]["total"], 3)
        self.assertEqual(loaded["unit_summaries"]["executions"]["completed"], 1)
        self.assertEqual(loaded["unit_summaries"]["executions"]["failed"], 1)
        self.assertEqual(loaded["unit_summaries"]["executions"]["warnings"], 1)
        self.assertEqual(loaded["unit_summaries"]["executions"]["errors"], 1)
        self.assertEqual(
            loaded["logical_runs"]["genie3_01"]["status"],
            "completed_with_warnings",
        )
        self.assertEqual(
            loaded["tools"]["genie3_01"]["status"],
            "completed_with_warnings",
        )
        self.assertEqual(
            loaded["logical_runs"]["genie3_01"]["warnings"],
            ["[genie3_01] docker image was pulled"],
        )
        self.assertEqual(
            loaded["tools"]["genie3_01"]["warnings"],
            ["[genie3_01] docker image was pulled"],
        )
        self.assertEqual(loaded["logical_runs"]["lioness_01"]["status"], "failed")
        self.assertEqual(
            loaded["tools"]["lioness_01__column_native"]["errors"],
            ["synthetic failure"],
        )
        self.assertEqual(
            [
                event["message"]
                for event in loaded["phase_history"]
            ],
            [
                "Execution is queued.",
                "Running wave 1 of 2.",
                "Completed wave 1 of 2.",
            ],
        )

    def test_wave_completed_with_warnings_is_distinct_from_clean_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ExecutionStateWriter.initialize(
                run_dir=Path(tmp),
                run_id="run_a",
                waves=self._waves(),
            )

            writer.start_wave(2)
            writer.update_tool(
                "clr_01",
                status="completed",
                phase="done",
                percent=100,
                message="Finished",
            )
            writer.record_warning_message("[clr_01] output network is empty")
            writer.complete_wave(2)
            loaded = read_execution_state(execution_state_path(Path(tmp)))

        self.assertEqual(loaded["waves"][1]["status"], "completed_with_warnings")
        self.assertEqual(
            loaded["tools"]["clr_01"]["status"],
            "completed_with_warnings",
        )

    def test_wave_fails_only_when_all_physical_tasks_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ExecutionStateWriter.initialize(
                run_dir=Path(tmp),
                run_id="run_a",
                waves=self._waves(),
            )

            writer.start_wave(1)
            writer.update_tool(
                "genie3_01",
                status="failed",
                phase="failed",
                percent=100,
                message="failed",
                error="failed",
            )
            running_state = read_execution_state(execution_state_path(Path(tmp)))
            self.assertEqual(running_state["waves"][0]["status"], "running")

            writer.update_tool(
                "lioness_01__column_native",
                status="failed",
                phase="failed",
                percent=100,
                message="failed",
                error="failed",
            )
            writer.complete_wave(1)
            loaded = read_execution_state(execution_state_path(Path(tmp)))

        self.assertEqual(loaded["waves"][0]["status"], "failed")

    def test_wave_with_mixed_success_and_failure_is_completed_with_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ExecutionStateWriter.initialize(
                run_dir=Path(tmp),
                run_id="run_a",
                waves=self._waves(),
            )

            writer.start_wave(1)
            writer.update_tool(
                "genie3_01",
                status="completed",
                phase="done",
                percent=100,
                message="Finished",
            )
            writer.update_tool(
                "lioness_01__column_native",
                status="failed",
                phase="failed",
                percent=100,
                message="failed",
                error="failed",
            )
            writer.complete_wave(1)
            loaded = read_execution_state(execution_state_path(Path(tmp)))

        self.assertEqual(loaded["waves"][0]["status"], "completed_with_failures")
