from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from andrea.core.commands.infer_network.commons.shared import ToolExecutionResult

from ._helpers import InferNetworkCoreTestCase


class InferNetworkRunTests(InferNetworkCoreTestCase):
    def _cell_aggregated_toolspec(self) -> dict:
        return {
            "id": "fakecell",
            "docker_image": "fake/cell:latest",
            "execution_capabilities": ["cell_native", "group_aggregated"],
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

    def test_group_aggregated_run_preserves_cell_rows_and_adds_group_rows(self) -> None:
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
                *, wave, runtime_io_by_tool, pulled_images, poll_interval_s, warnings
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

        self.assertIn("cell:C1", contexts)
        self.assertIn("group:A", contexts)
        self.assertAlmostEqual(float(group_a["score"]), 0.25)
        self.assertEqual(group_a["sign"], "+")
        self.assertAlmostEqual(float(group_b_unknown["score"]), 2.0)
        self.assertEqual(group_b_unknown["sign"], "?")
        self.assertTrue(cell_network_exists)

    def test_run_plan_executes_from_frozen_dir_and_updates_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = self._prepare_planned_run(base)

            def fake_run_wave(
                *, wave, runtime_io_by_tool, pulled_images, poll_interval_s, warnings
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

            def fake_merge(*, run_dir, execution_results, warnings):
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
                *, wave, runtime_io_by_tool, pulled_images, poll_interval_s, warnings
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

            def fake_merge(*, run_dir, execution_results, warnings):
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

        self.assertEqual(executed, run_dir)
        self.assertEqual(report_payload["execution"]["tools_completed"], 1)
        self.assertEqual(report_payload["execution"]["tools_failed"], 1)
        self.assertEqual(
            report_payload["tools"]["failed"]["aracne__02"],
            "synthetic test failure",
        )
