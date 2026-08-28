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
from andrea.core.commands.infer_network.commons.shared import (
    PlanWave,
    ToolExecutionResult,
    ToolPlanItem,
)
from andrea.core.commands.infer_network.run import (
    _completed_contexts_for_run,
    _finalize_group_aggregated_logical_run,
    _finalize_grouped_logical_run,
    _load_logical_runs_from_plan,
)

from ._helpers import InferNetworkCoreTestCase


class InferNetworkRunTests(InferNetworkCoreTestCase):
    def _cell_aggregated_toolspec(self) -> dict:
        return {
            "id": "fakecell",
            "docker_image": "fake/cell:latest",
            "execution_capabilities": ["column_native", "group_aggregated"],
            "runtime_resources": {
                "threading": {
                    "supported": False,
                    "default_threads": 1,
                    "max_threads": 1,
                    "upstream_mapping": "No upstream parallel runtime control.",
                }
            },
            "params": {},
            "accepts": ["cells"],
            "assumes": "generic",
            "taxonomic_scope": {
                "allowed_groups": ["animal"],
                "supported_species": [],
            },
            "outputs": {
                "directed": True,
                "sign": "mixed",
                "evidence": "association",
            },
            "extra_inputs": {
                "required": [],
                "optional": [],
                "conditional_required": [
                    {
                        "input": "groups",
                        "execution": "mode",
                        "op": "eq",
                        "value": "group_aggregated",
                        "usage": "Used by ANDREA to aggregate column-native network rows by group.",
                        "message": "groups is required when execution.mode=group_aggregated.",
                    }
                ],
            },
        }

    def _write_cell_aggregated_bundle(self, base: Path) -> tuple[Path, Path, dict]:
        self._write_expression_matrix(
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
        with patch(
            "andrea.core.commands.infer_network.preflight._load_toolspec",
            return_value=self._cell_aggregated_toolspec(),
        ):
            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
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

    def test_run_wave_promotes_wrapper_progress_warnings(self) -> None:
        from andrea.core.commands.infer_network.commons import runtime_helpers

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            shared_expression = base / "expression.tsv"
            shared_expression.write_text("gene\tS1\tS2\nG1\t1\t2\n", encoding="utf-8")
            tool_io = runtime_helpers._prepare_tool_runtime_io(
                run_dir=base,
                tool_id="warn_tool",
                run_id="warn_tool",
                output_dir="tools/warn_tool",
                resolved_params={},
                resolved_execution={"mode": "global"},
                shared_expression=shared_expression,
                shared_extras={},
            )
            wave = PlanWave(
                index=1,
                threads_used=1,
                ram_gb_used=1.0,
                eta_seconds=1.0,
                tasks=[
                    ToolPlanItem(
                        tool_id="warn_tool",
                        run_id="warn_tool",
                        image="example/warn:1.0",
                        threads=1,
                        ram_gb=1.0,
                        eta_seconds=1.0,
                        eta_source="test",
                        output_dir="tools/warn_tool",
                    )
                ],
            )

            def fake_docker_run_detached(**_kwargs):
                (tool_io.out_dir / "network.csv").write_text(
                    "source,target,score,sign,evidence,context\n"
                    "G1,G2,1,+,test,global\n",
                    encoding="utf-8",
                )
                (tool_io.out_dir / "progress.json").write_text(
                    json.dumps(
                        {
                            "status": "completed_with_warnings",
                            "phase": "done",
                            "percent": 100,
                            "message": "done",
                            "warnings": ["wrapper produced a best-effort result"],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return "container-1"

            runtime_warnings: list[str] = []
            with (
                patch.object(
                    runtime_helpers,
                    "_ensure_docker_image",
                    return_value="local",
                ),
                patch.object(
                    runtime_helpers,
                    "_docker_run_detached",
                    side_effect=fake_docker_run_detached,
                ),
                patch.object(
                    runtime_helpers,
                    "_docker_inspect_status",
                    return_value="exited",
                ),
                patch.object(
                    runtime_helpers,
                    "_docker_wait_exit_code",
                    return_value=0,
                ),
                patch.object(runtime_helpers, "_docker_logs", return_value=""),
                patch.object(runtime_helpers, "_docker_rm"),
            ):
                results = runtime_helpers._run_wave(
                    wave=wave,
                    runtime_io_by_tool={"warn_tool": tool_io},
                    pulled_images=set(),
                    poll_interval_s=0.01,
                    warnings=runtime_warnings,
                    state_writer=None,
                )

        result = results["warn_tool"]
        self.assertEqual(result.status, "completed_with_warnings")
        self.assertEqual(
            result.warnings,
            ("wrapper produced a best-effort result",),
        )
        self.assertEqual(
            runtime_warnings,
            ["[warn_tool] wrapper produced a best-effort result"],
        )

    def test_group_aggregated_run_writes_group_rows_and_keeps_column_auxiliary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            (
                manifest_path,
                tools_params_path,
                preflight,
            ) = self._write_cell_aggregated_bundle(base)
            with (
                patch(
                    "andrea.core.commands.infer_network.plan._load_toolspec",
                    return_value=self._cell_aggregated_toolspec(),
                ),
                patch(
                    "andrea.core.commands.infer_network.plan.preflight_infer_network",
                    return_value=preflight,
                ),
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
                        (tool_io.io_dir / "execution.json").read_text(encoding="utf-8")
                    )
                    self.assertEqual(execution["mode"], "column_native")
                    network_path = tool_io.out_dir / "network.csv"
                    network_path.write_text(
                        "\n".join(
                            [
                                "source,target,score,sign,evidence,context",
                                "G1,G2,1,+,test,column:C1",
                                "G1,G2,0.5,-,test,column:C2",
                                "G1,G2,2,?,test,column:C3",
                                "G2,G1,1,+,test,column:C3",
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
            column_network = (
                run_dir
                / "tools"
                / "cellrun"
                / "io"
                / "out"
                / "network.column_native.csv"
            )
            column_network_exists = column_network.exists()
            with column_network.open("r", encoding="utf-8", newline="") as handle:
                column_rows = list(csv.DictReader(handle))
            column_contexts = {row["context"] for row in column_rows}
            state_payload = read_execution_state(execution_state_path(run_dir))
            report_payload = json.loads(
                (run_dir / "run_report.json").read_text(encoding="utf-8")
            )

        self.assertEqual(contexts, {"group:A", "group:B"})
        self.assertNotIn("column:C1", contexts)
        self.assertEqual(column_contexts, {"column:C1", "column:C2", "column:C3"})
        self.assertAlmostEqual(float(group_a["score"]), 0.25)
        self.assertEqual(group_a["sign"], "+")
        self.assertAlmostEqual(float(group_b_unknown["score"]), 2.0)
        self.assertEqual(group_b_unknown["sign"], "?")
        self.assertTrue(column_network_exists)
        self.assertEqual(state_payload["status"], "completed")
        self.assertEqual(
            state_payload["logical_runs"]["cellrun"]["status"], "completed"
        )
        self.assertEqual(
            state_payload["tools"]["cellrun__column_native"]["status"],
            "completed",
        )
        self.assertEqual(
            report_payload["tools"]["completed_contexts"],
            {"cellrun": ["group:A", "group:B"]},
        )

    def test_group_aggregated_accepts_header_only_upstream_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            upstream = run_dir / "upstream" / "network.csv"
            upstream.parent.mkdir(parents=True)
            upstream.write_text(
                "source,target,score,sign,evidence,context\n",
                encoding="utf-8",
            )
            result, payload = _finalize_group_aggregated_logical_run(
                run_dir=run_dir,
                run_id="aggregate_run",
                logical_spec={
                    "execution": {"mode": "group_aggregated"},
                    "physical_tasks": [
                        {
                            "task_id": "aggregate_run__column_native",
                            "output_dir": "upstream",
                        }
                    ],
                },
                child_results={
                    "aggregate_run__column_native": ToolExecutionResult(
                        tool_id="aggregate_run__column_native",
                        status="completed",
                        exit_code=0,
                        duration_seconds=0.1,
                        network_path=str(upstream),
                        progress_path=None,
                        logs_path=None,
                        error=None,
                    )
                },
                group_to_columns={"A": ["C1"], "B": ["C2"]},
                warnings=[],
            )

            parent_network = Path(result.network_path or "")
            auxiliary_network = (
                run_dir
                / "tools"
                / "aggregate_run"
                / "io"
                / "out"
                / "network.column_native.csv"
            )
            parent_content = parent_network.read_text(encoding="utf-8")
            auxiliary_content = auxiliary_network.read_text(encoding="utf-8")

        self.assertEqual(result.status, "completed")
        self.assertEqual(payload["aggregation"]["column_rows"], 0)
        self.assertEqual(payload["aggregation"]["aggregated_group_rows"], 0)
        self.assertEqual(
            parent_content,
            "source,target,score,sign,evidence,context\n",
        )
        self.assertEqual(
            auxiliary_content,
            "source,target,score,sign,evidence,context\n",
        )

    def test_group_emulated_keeps_empty_success_and_excludes_failed_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            empty_network = run_dir / "group_a" / "network.csv"
            empty_network.parent.mkdir(parents=True)
            empty_network.write_text(
                "source,target,score,sign,evidence,context\n",
                encoding="utf-8",
            )
            logical_spec = {
                "execution": {"mode": "group_emulated"},
                "physical_tasks": [
                    {
                        "task_id": "grouped__a",
                        "group_label": "A",
                        "output_dir": "group_a",
                    },
                    {
                        "task_id": "grouped__b",
                        "group_label": "B",
                        "output_dir": "group_b",
                    },
                ],
            }
            result, payload = _finalize_grouped_logical_run(
                run_dir=run_dir,
                run_id="grouped",
                logical_spec=logical_spec,
                child_results={
                    "grouped__a": ToolExecutionResult(
                        tool_id="grouped__a",
                        status="completed",
                        exit_code=0,
                        duration_seconds=0.1,
                        network_path=str(empty_network),
                        progress_path=None,
                        logs_path=None,
                        error=None,
                    ),
                    "grouped__b": ToolExecutionResult(
                        tool_id="grouped__b",
                        status="failed",
                        exit_code=1,
                        duration_seconds=0.1,
                        network_path=None,
                        progress_path=None,
                        logs_path=None,
                        error="synthetic failure",
                    ),
                },
                warnings=[],
            )
            completed_contexts = _completed_contexts_for_run(
                run_id="grouped",
                logical_spec=logical_spec,
                logical_payload=payload,
                planned_contexts={"group:A", "group:B"},
            )
            parent_network = Path(result.network_path or "")
            parent_content = parent_network.read_text(encoding="utf-8")

        self.assertEqual(result.status, "completed_with_warnings")
        self.assertEqual(completed_contexts, ["group:A"])
        self.assertEqual(
            parent_content,
            "source,target,score,sign,evidence,context\n",
        )

    def test_run_rejects_plan_threads_above_toolspec_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            (
                manifest_path,
                tools_params_path,
                preflight,
            ) = self._write_cell_aggregated_bundle(base)
            with (
                patch(
                    "andrea.core.commands.infer_network.plan._load_toolspec",
                    return_value=self._cell_aggregated_toolspec(),
                ),
                patch(
                    "andrea.core.commands.infer_network.plan.preflight_infer_network",
                    return_value=preflight,
                ),
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
            plan_payload["runs"][0]["physical_tasks"][0]["threads"] = 2
            plan_payload["totals"]["threads_peak"] = 2
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

    def test_logical_plan_requires_explicit_canonical_tool_origin(self) -> None:
        base_run = {
            "run_id": "demo",
            "tool_id": "demo_tool",
            "execution": {"mode": "global"},
            "physical_tasks": [{}],
        }
        cases = [
            ({**base_run}, "missing"),
            ({**base_run, "tool_origin": "external"}, "invalid"),
        ]
        for raw_run, label in cases:
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(ValueError, r"plan\.json\.runs\[1\] is invalid"),
            ):
                _load_logical_runs_from_plan({"runs": [raw_run]})

    def test_run_rejects_plan_tool_identity_mismatch(self) -> None:
        cases = [
            (
                "tool_origin",
                "custom",
                "plan.json tool_origin for 'aracne__01' must be 'catalog'",
            ),
            (
                "tool_id",
                "genie3",
                "plan.json tool_id for 'aracne__01' must be 'aracne3'",
            ),
        ]
        for field, value, expected in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                run_dir = self._prepare_planned_run(Path(tmp))
                plan_path = run_dir / "plan.json"
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                plan["runs"][0][field] = value
                plan_path.write_text(
                    json.dumps(plan, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(ValueError, expected):
                    self.mod.run_infer_network_plan(run_dir=run_dir)

    def test_run_requires_exact_frozen_preflight_identity_maps(self) -> None:
        cases = [
            ("tool_origins", "custom"),
            ("catalog_tool_ids", "genie3"),
        ]
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                run_dir = self._prepare_planned_run(Path(tmp))
                preflight_path = run_dir / "preflight_report.json"
                preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
                preflight["runs"][field]["ghost"] = value
                preflight_path.write_text(
                    json.dumps(preflight, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    rf"preflight_report runs\.{field} must be an object with exactly",
                ):
                    self.mod.run_infer_network_plan(run_dir=run_dir)

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
                *,
                run_dir,
                execution_results,
                output_capabilities,
                warnings,
                allowed_contexts=None,
                progress_callback=None,
            ):
                self.assertEqual(output_capabilities["aracne__01"]["sign"], "none")
                self.assertEqual(
                    allowed_contexts,
                    {"aracne__01": {"global"}},
                )
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
            self.assertEqual(
                report_payload["tools"]["completed_contexts"],
                {"aracne__01": ["global"]},
            )
            self.assertEqual(
                report_payload["tools"]["output_capabilities"]["aracne__01"],
                {
                    "tool_origin": "catalog",
                    "catalog_tool_id": "aracne3",
                    "directed": False,
                    "sign": "none",
                },
            )
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
            phase_history = [event["phase"] for event in state_payload["phase_history"]]
            for expected_phase in (
                "collecting_results",
                "merging_raw_networks",
                "normalizing_scores",
                "exporting_artifacts",
                "writing_report",
                "completed",
            ):
                self.assertIn(expected_phase, phase_history)

    def test_run_plan_accepts_header_only_network_as_completed_zero_edge_run(
        self,
    ) -> None:
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
                results = {}
                for task in wave.tasks:
                    network_path = (
                        runtime_io_by_tool[task.tool_id].out_dir / "network.csv"
                    )
                    network_path.write_text(
                        "source,target,score,sign,evidence,context\n",
                        encoding="utf-8",
                    )
                    results[task.tool_id] = ToolExecutionResult(
                        tool_id=task.tool_id,
                        status="completed",
                        exit_code=0,
                        duration_seconds=0.1,
                        network_path=str(network_path),
                        progress_path=None,
                        logs_path=None,
                        error=None,
                    )
                return results

            with (
                patch("andrea.core.commands.infer_network.run._ensure_docker_cli"),
                patch(
                    "andrea.core.commands.infer_network.run._run_wave",
                    side_effect=fake_run_wave,
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
            state_payload = read_execution_state(execution_state_path(run_dir))

            self.assertEqual(report_payload["status"], "executed")
            self.assertEqual(report_payload["execution"]["tools_completed"], 1)
            self.assertEqual(report_payload["execution"]["tools_failed"], 0)
            self.assertEqual(report_payload["tools"]["completed"], ["aracne__01"])
            self.assertEqual(report_payload["tools"]["failed"], {})
            self.assertEqual(
                report_payload["outputs"]["rows_per_tool"],
                {"aracne__01": 0},
            )
            self.assertEqual(
                report_payload["outputs"]["merged_network_raw"],
                "merged_network_raw.csv",
            )
            self.assertEqual(
                report_payload["outputs"]["merged_network_normalized"],
                "merged_network_normalized.csv",
            )
            self.assertEqual(
                (run_dir / "merged_network_raw.csv").read_text(encoding="utf-8"),
                "source,target,score,sign,evidence,context,tool_id\n",
            )
            self.assertEqual(state_payload["status"], "completed")
            self.assertEqual(state_payload["summary"]["completed"], 1)
            self.assertEqual(state_payload["summary"]["failed"], 0)

    def test_run_plan_rejects_malformed_frozen_output_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._prepare_planned_run(Path(tmp))
            report_path = run_dir / "run_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            run_id = report["tools"]["selected"][0]
            report["tools"]["output_capabilities"][run_id] = "invalid"
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "must contain exactly tool_origin, catalog_tool_id, directed and sign",
            ):
                self.mod.run_infer_network_plan(run_dir=run_dir)

    def test_run_plan_requires_exact_frozen_maps_for_selected_runs(self) -> None:
        cases = [
            (
                "output_capabilities",
                "output_capabilities must be an object with exactly.*selected keys",
            ),
            (
                "catalog_tool_ids",
                "catalog_tool_ids must be an object with exactly.*selected keys",
            ),
            (
                "tool_origins",
                "tool_origins must be an object with exactly.*selected keys",
            ),
        ]
        for field_name, expected in cases:
            with (
                self.subTest(field_name=field_name),
                tempfile.TemporaryDirectory() as tmp,
            ):
                run_dir = self._prepare_planned_run(Path(tmp))
                report_path = run_dir / "run_report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                run_id = report["tools"]["selected"][0]
                report["tools"][field_name].pop(run_id)
                report_path.write_text(
                    json.dumps(report, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(ValueError, expected):
                    self.mod.run_infer_network_plan(run_dir=run_dir)

    def test_run_plan_counts_completed_with_warnings_as_completed(self) -> None:
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
                    warning = "wrapper produced a best-effort result"
                    warnings.append(f"[{task.tool_id}] {warning}")
                    out[task.tool_id] = ToolExecutionResult(
                        tool_id=task.tool_id,
                        status="completed_with_warnings",
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
                        warnings=(warning,),
                    )
                return out

            def fake_merge(
                *,
                run_dir,
                execution_results,
                output_capabilities,
                warnings,
                allowed_contexts=None,
                progress_callback=None,
            ):
                self.assertEqual(output_capabilities["aracne__01"]["sign"], "none")
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
                    if result.status in {"completed", "completed_with_warnings"}
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
                self.mod.run_infer_network_plan(
                    run_dir=run_dir,
                    progress_poll_seconds=0.1,
                )

            report_payload = json.loads(
                (run_dir / "run_report.json").read_text(encoding="utf-8")
            )
            state_payload = read_execution_state(execution_state_path(run_dir))

        self.assertEqual(report_payload["execution"]["tools_completed"], 1)
        self.assertEqual(report_payload["execution"]["tools_failed"], 0)
        self.assertEqual(
            report_payload["tools"]["status_by_tool"]["aracne__01"],
            "completed_with_warnings",
        )
        self.assertEqual(
            report_payload["tools"]["results"]["aracne__01"]["warnings"],
            ["wrapper produced a best-effort result"],
        )
        self.assertEqual(
            state_payload["logical_runs"]["aracne__01"]["status"],
            "completed_with_warnings",
        )

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

    def test_run_plan_rejects_report_dataset_fingerprint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._prepare_planned_run(Path(tmp))
            report_path = run_dir / "run_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["dataset"]["fingerprint"] = {
                "algorithm": "sha256",
                "value": "b" * 64,
            }
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with patch(
                "andrea.core.commands.infer_network.run._ensure_docker_cli"
            ) as ensure_docker:
                with self.assertRaisesRegex(
                    ValueError,
                    "dataset.fingerprint does not match the frozen dataset inputs",
                ):
                    self.mod.run_infer_network_plan(run_dir=run_dir)
            ensure_docker.assert_not_called()

    def test_run_plan_rejects_report_dataset_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._prepare_planned_run(Path(tmp))
            report_path = run_dir / "run_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["dataset"]["id"] = "other_ds"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with patch(
                "andrea.core.commands.infer_network.run._ensure_docker_cli"
            ) as ensure_docker:
                with self.assertRaisesRegex(
                    ValueError,
                    "dataset.id does not match the frozen dataset manifest",
                ):
                    self.mod.run_infer_network_plan(run_dir=run_dir)
            ensure_docker.assert_not_called()

    def test_run_plan_rejects_non_object_report_dataset_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._prepare_planned_run(Path(tmp))
            report_path = run_dir / "run_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["dataset"] = []
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with patch(
                "andrea.core.commands.infer_network.run._ensure_docker_cli"
            ) as ensure_docker:
                with self.assertRaisesRegex(
                    ValueError,
                    "run_report dataset must be an object",
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
                *,
                run_dir,
                execution_results,
                output_capabilities,
                warnings,
                allowed_contexts=None,
                progress_callback=None,
            ):
                self.assertEqual(set(output_capabilities), {"aracne__01", "aracne__02"})
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
