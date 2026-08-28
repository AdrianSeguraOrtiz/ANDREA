from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from andrea.core.commands.evaluate_inference import evaluate_inference
from andrea.core.commands.infer_network.commons.shared import ToolExecutionResult

from ._helpers import InferNetworkCoreTestCase


class ExternalInferenceEndToEndTests(InferNetworkCoreTestCase):
    def test_custom_tool_empty_network_can_be_planned_run_and_evaluated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path, _default_tools_params = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "external_01",
                        "tool_id": "custom_external_01",
                        "execution": {"mode": "global"},
                        "params": {"threshold": 0.25},
                    }
                ],
            )
            custom_tools_path = self._write_custom_tools(
                base,
                tools=[
                    {
                        "run_id": "external_01",
                        "name": "External GRN method",
                        "docker_image": "example/external-grn:1.0",
                        "execution_mode": "global",
                        "extra_inputs": ["tf_list"],
                        "outputs": {
                            "directed": True,
                            "sign": "signed",
                        },
                    }
                ],
            )

            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
            )
            self.assertEqual(preflight["runs"]["selected"], ["external_01"])
            self.assertEqual(
                preflight["runs"]["catalog_tool_ids"],
                {"external_01": "custom_external_01"},
            )

            run_dir = self.mod.plan_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
                output_dir=base / "inference",
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
                results = {}
                for task in wave.tasks:
                    runtime_io = runtime_io_by_tool[task.tool_id]
                    self.assertTrue(
                        (runtime_io.io_dir / "extra" / "tf_list.txt").exists()
                    )
                    network_path = runtime_io.out_dir / "network.csv"
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
                self.mod.run_infer_network_plan(
                    run_dir=run_dir,
                    progress_poll_seconds=0.1,
                )

            run_report_path = run_dir / "run_report.json"
            run_report = json.loads(run_report_path.read_text(encoding="utf-8"))
            self.assertEqual(run_report["status"], "executed")
            self.assertEqual(run_report["tools"]["completed"], ["external_01"])
            self.assertEqual(
                run_report["tools"]["output_capabilities"]["external_01"],
                {
                    "tool_origin": "custom",
                    "catalog_tool_id": "custom_external_01",
                    "directed": True,
                    "sign": "signed",
                },
            )
            self.assertEqual(
                run_report["outputs"]["rows_per_tool"],
                {"external_01": 0},
            )

            truth_dir = base / "truth"
            truth_dir.mkdir()
            (truth_dir / "gene_universe.txt").write_text(
                "G1\nG2\n",
                encoding="utf-8",
            )
            (truth_dir / "tf_list.txt").write_text("G1\nG2\n", encoding="utf-8")
            (truth_dir / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\n"
                "G1,G2,1,+,simulated_truth,global\n",
                encoding="utf-8",
            )
            truth_manifest_path = base / "ground-truth-manifest.json"
            truth_manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "dataset_id": "toy_ds",
                        "dataset_fingerprint": run_report["dataset"]["fingerprint"],
                        "simulator_id": "test_simulator",
                        "data_axes": {
                            "measurement": "rna_expression",
                            "resolution": "bulk",
                            "column_kind": "samples",
                            "experimental_design": "steady_state",
                        },
                        "truth_requirements": {"contexts": ["global"]},
                        "outputs": {
                            "gene_universe": "truth/gene_universe.txt",
                            "networks": "truth/networks.csv",
                        },
                        "candidate_space": {
                            "sources": "truth/tf_list.txt",
                            "targets": "truth/gene_universe.txt",
                            "allow_self_edges": False,
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            evaluation = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=truth_manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        self.assertEqual(evaluation["inputs"]["inference_dataset_id"], "toy_ds")
        self.assertEqual(
            evaluation["inputs"]["ground_truth_dataset_id"],
            "toy_ds",
        )
        self.assertEqual(len(evaluation["pairings"]), 1)
        pairing = evaluation["pairings"][0]
        self.assertEqual(pairing["tool_id"], "external_01")
        self.assertEqual(pairing["catalog_tool_id"], "custom_external_01")
        self.assertEqual(pairing["tool_origin"], "custom")
        self.assertEqual(pairing["context"], "global")
        self.assertEqual(pairing["status"], "evaluated")
        self.assertEqual(pairing["n_prediction_rows"], 0)
        self.assertEqual(
            {metric["level"] for metric in evaluation["metrics"]},
            {"topology", "directed", "signed"},
        )
        self.assertEqual(
            {metric["level"]: metric["status"] for metric in evaluation["metrics"]},
            {"topology": "partial", "directed": "ok", "signed": "ok"},
        )
        self.assertTrue(
            all(metric["n_prediction_rows"] == 0 for metric in evaluation["metrics"])
        )
