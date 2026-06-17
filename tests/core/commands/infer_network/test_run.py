from __future__ import annotations

import csv
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from andrea.core.commands.infer_network.commons.execution_state import (
    execution_state_path,
    read_execution_state,
)
from andrea.core.commands.infer_network.commons.shared import ToolExecutionResult

from ._helpers import InferNetworkCoreTestCase


class InferNetworkRunTests(InferNetworkCoreTestCase):
    def _cell_aggregated_toolspec(self) -> dict:
        return {
            "id": "fakecell",
            "docker_image": "fake/cell:latest",
            "execution_capabilities": ["cell_native", "group_aggregated"],
            "runtime_resources": {
                "threading": {
                    "supported": False,
                    "default_threads": 1,
                    "max_threads": 1,
                    "upstream_mapping": "No upstream parallel runtime control.",
                }
            },
            "params": {},
            "extra_inputs": {
                "required": [],
                "optional": [],
                "conditional_required": [
                    {
                        "input": "groups",
                        "execution": "mode",
                        "op": "eq",
                        "value": "group_aggregated",
                        "usage": "Used by ANDREA to aggregate cell-native network rows by group.",
                        "message": "groups is required when execution.mode=group_aggregated.",
                    }
                ],
            },
        }

    def _write_cell_aggregated_bundle(self, base: Path) -> tuple[Path, Path, dict]:
        expression_path = self._write_expression_matrix(
            base,
            lines=[
                "gene\tC1\tC2\tC3",
                "G1\t1\t2\t3",
                "G2\t4\t5\t6",
            ],
        )
        groups_path = base / "groups.tsv"
        groups_path.write_text(
            "cell\tcluster\nC1\tA\nC2\tA\nC3\tB\n",
            encoding="utf-8",
        )
        manifest_path = self._write_manifest(
            base,
            expression_matrix="expression.tsv",
            genes=2,
            columns=3,
            column_kind="cells",
            expression_profile="scrna",
            extras={"groups": "groups.tsv"},
        )
        tools_params_path = self._write_tools_params(
            base,
            runs=[
                {
                    "run_id": "cellrun",
                    "tool_id": "fakecell",
                    "execution": {"mode": "group_aggregated"},
                    "params": {},
                }
            ],
        )
        preflight = {
            "dataset": {
                "dataset_id": "toy_cell_ds",
                "column_kind": "cells",
                "expression_profile": "scrna",
                "organism": {"taxonomic_group": "animal", "ncbi_taxon_id": 9606},
                "genes": 2,
                "columns": 3,
                "expression_matrix_path": str(expression_path),
                "extras": {"groups": str(groups_path)},
            },
            "runs": {
                "selected": ["cellrun"],
                "catalog_tool_ids": {"cellrun": "fakecell"},
                "resolved_params": {"cellrun": {}},
                "resolved_execution": {"cellrun": {"mode": "group_aggregated"}},
                "issues": {"cellrun": []},
                "skipped": {},
            },
        }
        return manifest_path, tools_params_path, preflight

    def _prepare_planned_run(
        self,
        base: Path,
        *,
        tools_params_payload: dict | None = None,
    ) -> Path:
        output_dir = base / "out"
        manifest_path, tools_params_path = self._write_dataset_bundle(
            base,
            tf_values=["G1", "G2"],
        )
        if tools_params_payload is not None:
            tools_params_path.write_text(
                json.dumps(tools_params_payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

        preflight = self.mod.preflight_infer_network(
            dataset_manifest_path=manifest_path,
            tools_params_path=tools_params_path,
        )
        return self.mod.plan_infer_network(
            dataset_manifest_path=manifest_path,
            tools_params_path=tools_params_path,
            output_dir=output_dir,
            planner="heuristic",
            preflight_report=preflight,
        )

    def test_docker_run_detached_disables_network_only_when_requested(self) -> None:
        from andrea.core.commands.infer_network.commons import runtime_helpers

        with tempfile.TemporaryDirectory() as tmp:
            io_dir = Path(tmp)
            with patch.object(
                runtime_helpers,
                "_run_cmd",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="container-123\n",
                    stderr="",
                ),
            ) as run_cmd:
                container_id = runtime_helpers._docker_run_detached(
                    image="example/custom:1.0",
                    io_dir=io_dir,
                    threads=2,
                    ram_gb=4.0,
                    network_disabled=True,
                )
                custom_cmd = run_cmd.call_args.args[0]

            with patch.object(
                runtime_helpers,
                "_run_cmd",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="container-456\n",
                    stderr="",
                ),
            ) as run_cmd:
                runtime_helpers._docker_run_detached(
                    image="example/catalog:1.0",
                    io_dir=io_dir,
                    threads=2,
                    ram_gb=4.0,
                    network_disabled=False,
                )
                catalog_cmd = run_cmd.call_args.args[0]

        self.assertEqual(container_id, "container-123")
        self.assertIn("--network", custom_cmd)
        self.assertIn("none", custom_cmd)
        self.assertNotIn("--network", catalog_cmd)

    def test_runtime_io_can_filter_extra_inputs_for_custom_tools(self) -> None:
        from andrea.core.commands.infer_network.commons import runtime_helpers

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            shared_expression = base / "expression.tsv"
            shared_expression.write_text("gene\tS1\nG1\t1\n", encoding="utf-8")
            tf_list = base / "tf_list.txt"
            tf_list.write_text("G1\n", encoding="utf-8")
            groups = base / "groups.tsv"
            groups.write_text("cell\tcluster\nS1\tA\n", encoding="utf-8")

            filtered = runtime_helpers._prepare_tool_runtime_io(
                run_dir=base / "filtered",
                tool_id="custom_tool",
                run_id="custom_tool",
                output_dir="tools/custom_tool",
                resolved_params={},
                resolved_execution={"mode": "global"},
                shared_expression=shared_expression,
                shared_extras={"tf_list": tf_list, "groups": groups},
                extra_input_keys={"tf_list"},
            )
            unfiltered = runtime_helpers._prepare_tool_runtime_io(
                run_dir=base / "unfiltered",
                tool_id="catalog_tool",
                run_id="catalog_tool",
                output_dir="tools/catalog_tool",
                resolved_params={},
                resolved_execution={"mode": "global"},
                shared_expression=shared_expression,
                shared_extras={"tf_list": tf_list, "groups": groups},
                extra_input_keys=None,
            )

            self.assertTrue((filtered.io_dir / "extra" / "tf_list.txt").exists())
            self.assertFalse((filtered.io_dir / "extra" / "groups.tsv").exists())
            self.assertTrue((unfiltered.io_dir / "extra" / "tf_list.txt").exists())
            self.assertTrue((unfiltered.io_dir / "extra" / "groups.tsv").exists())

    def test_group_aggregated_run_writes_group_rows_and_keeps_cell_auxiliary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, tools_params_path, preflight = (
                self._write_cell_aggregated_bundle(base)
            )
            with patch(
                "andrea.core.commands.infer_network.plan._load_toolspec",
                return_value=self._cell_aggregated_toolspec(),
            ):
                run_dir = self.mod.plan_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                    output_dir=output_dir,
                    planner="heuristic",
                    preflight_report=preflight,
                )

            def fake_run_wave(
                *,
                wave,
                runtime_io_by_tool,
                pulled_images,
                poll_interval_s,
                warnings,
                state_writer=None,
            ):
                out = {}
                for task in wave.tasks:
                    tool_io = runtime_io_by_tool[task.tool_id]
                    execution = json.loads(
                        (tool_io.io_dir / "execution.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertEqual(execution["mode"], "cell_native")
                    network_path = tool_io.out_dir / "network.csv"
                    network_path.write_text(
                        "\n".join(
                            [
                                "source,target,score,sign,evidence,context",
                                "G1,G2,1,+,test,cell:C1",
                                "G1,G2,0.5,-,test,cell:C2",
                                "G1,G2,2,?,test,cell:C3",
                                "G2,G1,1,+,test,cell:C3",
                            ]
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    out[task.tool_id] = ToolExecutionResult(
                        tool_id=task.tool_id,
                        status="completed",
                        exit_code=0,
                        duration_seconds=0.5,
                        network_path=str(network_path),
                        progress_path=None,
                        logs_path=str(tool_io.tool_dir / "container.log"),
                        error=None,
                    )
                return out

            with (
                patch("andrea.core.commands.infer_network.run._ensure_docker_cli"),
                patch(
                    "andrea.core.commands.infer_network.run._load_toolspec",
                    return_value=self._cell_aggregated_toolspec(),
                ),
                patch(
                    "andrea.core.commands.infer_network.run._run_wave",
                    side_effect=fake_run_wave,
                ),
            ):
                self.mod.run_infer_network_plan(
                    run_dir=run_dir,
                    progress_poll_seconds=0.1,
                )

            raw_path = run_dir / "merged_network_raw.csv"
            with raw_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            contexts = {row["context"] for row in rows}
            group_a = next(
                row
                for row in rows
                if row["context"] == "group:A"
                and row["source"] == "G1"
                and row["target"] == "G2"
            )
            group_b_unknown = next(
                row
                for row in rows
                if row["context"] == "group:B"
                and row["source"] == "G1"
                and row["target"] == "G2"
            )
            cell_network = (
                run_dir
                / "tools"
                / "cellrun"
                / "io"
                / "out"
                / "network.cell_native.csv"
            )
            cell_network_exists = cell_network.exists()
            with cell_network.open("r", encoding="utf-8", newline="") as handle:
                cell_rows = list(csv.DictReader(handle))
            cell_contexts = {row["context"] for row in cell_rows}
            state_payload = read_execution_state(execution_state_path(run_dir))

        self.assertEqual(contexts, {"group:A", "group:B"})
        self.assertNotIn("cell:C1", contexts)
        self.assertEqual(cell_contexts, {"cell:C1", "cell:C2", "cell:C3"})
        self.assertAlmostEqual(float(group_a["score"]), 0.25)
        self.assertEqual(group_a["sign"], "+")
        self.assertAlmostEqual(float(group_b_unknown["score"]), 2.0)
        self.assertEqual(group_b_unknown["sign"], "?")
        self.assertTrue(cell_network_exists)
        self.assertEqual(state_payload["status"], "completed")
        self.assertEqual(state_payload["logical_runs"]["cellrun"]["status"], "completed")
        self.assertEqual(
            state_payload["tools"]["cellrun__cell_native"]["status"],
            "completed",
        )

    def test_run_rejects_plan_threads_above_toolspec_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, tools_params_path, preflight = (
                self._write_cell_aggregated_bundle(base)
            )
            with patch(
                "andrea.core.commands.infer_network.plan._load_toolspec",
                return_value=self._cell_aggregated_toolspec(),
            ):
                run_dir = self.mod.plan_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                    output_dir=output_dir,
                    planner="heuristic",
                    preflight_report=preflight,
                )

            plan_path = run_dir / "plan.json"
            plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
            plan_payload["waves"][0]["threads_used"] = 2
            plan_payload["waves"][0]["tasks"][0]["threads"] = 2
            plan_path.write_text(
                json.dumps(plan_payload, indent=2) + "\n",
                encoding="utf-8",
            )

            with (
                patch("andrea.core.commands.infer_network.run._ensure_docker_cli"),
                patch(
                    "andrea.core.commands.infer_network.run._load_toolspec",
                    return_value=self._cell_aggregated_toolspec(),
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "planned runtime threads are incompatible",
                ),
            ):
                self.mod.run_infer_network_plan(
                    run_dir=run_dir,
                    progress_poll_seconds=0.1,
                )

    def test_run_plan_executes_from_frozen_dir_and_updates_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = self._prepare_planned_run(base)

            def fake_run_wave(
                *,
                wave,
                runtime_io_by_tool,
                pulled_images,
                poll_interval_s,
                warnings,
                state_writer=None,
            ):
                out = {}
                for task in wave.tasks:
                    out[task.tool_id] = ToolExecutionResult(
                        tool_id=task.tool_id,
                        status="completed",
                        exit_code=0,
                        duration_seconds=0.5,
                        network_path=str(
                            (
                                run_dir
                                / "tools"
                                / task.tool_id
                                / "io"
                                / "out"
                                / "network.csv"
                            )
                        ),
                        progress_path=None,
                        logs_path=str(
                            (run_dir / "tools" / task.tool_id / "container.log")
                        ),
                        error=None,
                    )
                return out

            def fake_merge(
                *, run_dir, execution_results, warnings, progress_callback=None
            ):
                if progress_callback is not None:
                    progress_callback(
                        "merging_raw_networks",
                        83,
                        "Writing merged_network_raw.csv.",
                    )
                    progress_callback(
                        "normalizing_scores",
                        89,
                        "Writing merged_network_normalized.csv.",
                    )
                raw = run_dir / "merged_network_raw.csv"
                norm = run_dir / "merged_network_normalized.csv"
                raw.write_text(
                    "source,target,score,sign,evidence,context,tool_id\n",
                    encoding="utf-8",
                )
                norm.write_text(
                    "source,target,score,sign,evidence,context,tool_id\n",
                    encoding="utf-8",
                )
                rows = {
                    tool_id: 1
                    for tool_id, result in execution_results.items()
                    if result.status == "completed"
                }
                return execution_results, rows, raw, norm

            with (
                patch("andrea.core.commands.infer_network.run._ensure_docker_cli"),
                patch(
                    "andrea.core.commands.infer_network.run._run_wave",
                    side_effect=fake_run_wave,
                ),
                patch(
                    "andrea.core.commands.infer_network.run._merge_network_outputs",
                    side_effect=fake_merge,
                ),
            ):
                executed_run_dir = self.mod.run_infer_network_plan(
                    run_dir=run_dir,
                    progress_poll_seconds=0.1,
                )

            self.assertEqual(executed_run_dir, run_dir)
            report_payload = json.loads(
                (run_dir / "run_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report_payload["status"], "executed")
            self.assertEqual(report_payload["execution"]["tools_completed"], 1)
            self.assertEqual(report_payload["execution"]["tools_failed"], 0)
            raw_output = Path(report_payload["outputs"]["merged_network_raw"])
            self.assertFalse(raw_output.is_absolute())
            self.assertTrue((run_dir / raw_output).exists())
            for result in report_payload["tools"]["results"].values():
                for key in ("network_path", "progress_path", "logs_path"):
                    value = result.get(key)
                    if value is not None:
                        self.assertFalse(Path(value).is_absolute())
            state_payload = read_execution_state(execution_state_path(run_dir))
            self.assertEqual(state_payload["status"], "completed")
            self.assertEqual(state_payload["phase"], "completed")
            self.assertEqual(state_payload["percent"], 100)
            self.assertEqual(state_payload["summary"]["completed"], 1)
            self.assertEqual(state_payload["summary"]["failed"], 0)
            phase_history = [
                event["phase"] for event in state_payload["phase_history"]
            ]
            for expected_phase in (
                "collecting_results",
                "merging_raw_networks",
                "normalizing_scores",
                "exporting_artifacts",
                "writing_report",
                "completed",
            ):
                self.assertIn(expected_phase, phase_history)

    def test_run_plan_rejects_input_fingerprint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = self._prepare_planned_run(base)

            expression_path = run_dir / "input" / "expression.tsv"
            expression_path.write_text(
                expression_path.read_text(encoding="utf-8") + "#changed\n",
                encoding="utf-8",
            )

            with patch(
                "andrea.core.commands.infer_network.run._ensure_docker_cli"
            ) as ensure_docker:
                with self.assertRaisesRegex(
                    ValueError, "Input file changed since planning"
                ):
                    self.mod.run_infer_network_plan(run_dir=run_dir)
            ensure_docker.assert_not_called()

    def test_run_plan_records_partial_failures_without_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = self._prepare_planned_run(
                base,
                tools_params_payload={
                    "runs": [
                        {
                            "run_id": "aracne__01",
                            "tool_id": "aracne3",
                            "params": {"seed": 1},
                        },
                        {
                            "run_id": "aracne__02",
                            "tool_id": "aracne3",
                            "params": {"seed": 2},
                        },
                    ]
                },
            )

            def fake_run_wave(
                *,
                wave,
                runtime_io_by_tool,
                pulled_images,
                poll_interval_s,
                warnings,
                state_writer=None,
            ):
                out = {}
                for task in wave.tasks:
                    if task.tool_id.endswith("__01"):
                        status = "completed"
                        error = None
                        exit_code = 0
                    else:
                        status = "failed"
                        error = "synthetic test failure"
                        exit_code = 1
                    out[task.tool_id] = ToolExecutionResult(
                        tool_id=task.tool_id,
                        status=status,
                        exit_code=exit_code,
                        duration_seconds=0.2,
                        network_path=str(
                            (
                                run_dir
                                / "tools"
                                / task.tool_id
                                / "io"
                                / "out"
                                / "network.csv"
                            )
                        ),
                        progress_path=None,
                        logs_path=str(
                            (run_dir / "tools" / task.tool_id / "container.log")
                        ),
                        error=error,
                    )
                return out

            def fake_merge(
                *, run_dir, execution_results, warnings, progress_callback=None
            ):
                if progress_callback is not None:
                    progress_callback(
                        "merging_raw_networks",
                        83,
                        "Writing merged_network_raw.csv.",
                    )
                    progress_callback(
                        "normalizing_scores",
                        89,
                        "Writing merged_network_normalized.csv.",
                    )
                raw = run_dir / "merged_network_raw.csv"
                norm = run_dir / "merged_network_normalized.csv"
                raw.write_text(
                    "source,target,score,sign,evidence,context,tool_id\n",
                    encoding="utf-8",
                )
                norm.write_text(
                    "source,target,score,sign,evidence,context,tool_id\n",
                    encoding="utf-8",
                )
                rows = {
                    tool_id: 1
                    for tool_id, result in execution_results.items()
                    if result.status == "completed"
                }
                return execution_results, rows, raw, norm

            with (
                patch("andrea.core.commands.infer_network.run._ensure_docker_cli"),
                patch(
                    "andrea.core.commands.infer_network.run._run_wave",
                    side_effect=fake_run_wave,
                ),
                patch(
                    "andrea.core.commands.infer_network.run._merge_network_outputs",
                    side_effect=fake_merge,
                ),
            ):
                executed = self.mod.run_infer_network_plan(
                    run_dir=run_dir,
                    progress_poll_seconds=0.1,
                )

            report_payload = json.loads(
                (run_dir / "run_report.json").read_text(encoding="utf-8")
            )
            state_payload = read_execution_state(execution_state_path(run_dir))

        self.assertEqual(executed, run_dir)
        self.assertEqual(report_payload["execution"]["tools_completed"], 1)
        self.assertEqual(report_payload["execution"]["tools_failed"], 1)
        self.assertEqual(
            report_payload["tools"]["failed"]["aracne__02"],
            "synthetic test failure",
        )
        self.assertEqual(state_payload["status"], "completed_with_failures")
        self.assertEqual(state_payload["summary"]["completed"], 1)
        self.assertEqual(state_payload["summary"]["failed"], 1)
        self.assertEqual(
            state_payload["logical_runs"]["aracne__02"]["status"],
            "failed",
        )
