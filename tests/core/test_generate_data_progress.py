from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from andrea.core.commands.generate_data import pipeline
from andrea.core.commands.generate_data.shared import (
    ResolvedSimulationPlan,
    ResolvedSimulatorRun,
)


class GenerateDataProgressCallbackTests(unittest.TestCase):
    def test_run_generate_data_emits_normalized_progress_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            plan_path = tmp_root / "simulation-plan.json"
            plan_path.write_text("{}", encoding="utf-8")

            run = ResolvedSimulatorRun(
                request_id="bench",
                profile="scrna_global",
                run_id="dyngen_cfg",
                simulator_id="dyngen",
                organism={"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                requested_extras=[],
                effective_extras=[],
                inputs={},
                resolved_input_paths={},
                simulator_params={},
                runtime_resources={"threads": 1},
                native_outputs=[],
                replicates=1,
                base_seed=11,
                replicate_seeds=[11],
                notes=None,
                simulator_spec={"docker_image": "dummy"},
            )
            task = {
                "task_id": "dyngen_cfg__r01",
                "run_id": "dyngen_cfg",
                "simulator_id": "dyngen",
                "replicate_index": 1,
                "seed": 11,
                "dataset_id": "bench__dyngen_cfg__r01",
                "runtime_resources": {"threads": 1},
                "ram_gb": 4.0,
                "eta_seconds": 60.0,
                "eta_source": "test_fixture",
                "eta_start_seconds": 0.0,
                "eta_end_seconds": 60.0,
                "eta_wave": 1,
                "eta_provenance": {"eta_source": "test_fixture", "warnings": []},
            }
            resolved = ResolvedSimulationPlan(
                request_id="bench",
                profile="scrna_global",
                organism={"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                requested_extras=[],
                effective_extras=[],
                inputs={},
                resolved_input_paths={},
                base_seed=11,
                notes=None,
                simulator_runs=[run],
                tasks=[task],
                execution={
                    "max_parallel_tasks": 1,
                    "max_cores": 1,
                    "max_ram_gb": 4.0,
                    "eta_total_seconds": 60.0,
                    "warnings": [],
                    "waves": [
                        {
                            "index": 1,
                            "threads_used": 1,
                            "ram_gb_used": 4.0,
                            "eta_seconds": 60.0,
                            "eta_start_seconds": 0.0,
                            "eta_end_seconds": 60.0,
                            "tasks": [
                                {
                                    "task_id": "dyngen_cfg__r01",
                                    "run_id": "dyngen_cfg",
                                    "simulator_id": "dyngen",
                                    "threads": 1,
                                    "ram_gb": 4.0,
                                    "eta_seconds": 60.0,
                                    "eta_source": "test_fixture",
                                    "eta_start_seconds": 0.0,
                                    "eta_end_seconds": 60.0,
                                }
                            ],
                        }
                    ],
                },
                plan_payload={},
            )
            events: list[dict] = []

            def fake_execute_simulation_task(**kwargs):  # noqa: ANN003
                kwargs["progress_callback"](
                    {
                        "status": "running",
                        "phase": "run_simulator",
                        "percent": 42,
                        "message": "halfway",
                    }
                )
                return (
                    {
                        "dataset_id": "bench__dyngen_cfg__r01",
                        "run_id": "dyngen_cfg",
                        "simulator_id": "dyngen",
                        "seed": 11,
                        "path": "datasets/bench__dyngen_cfg__r01",
                        "dataset_manifest": "datasets/bench__dyngen_cfg__r01/dataset-manifest.json",
                        "ground_truth_manifest": "datasets/bench__dyngen_cfg__r01/ground-truth-manifest.json",
                    },
                    {
                        "dataset_id": "bench__dyngen_cfg__r01",
                        "run_id": "dyngen_cfg",
                        "simulator_id": "dyngen",
                        "expression_matrix": "datasets/bench__dyngen_cfg__r01/expression.tsv",
                        "groups": None,
                        "lineage_tree": None,
                        "tf_list": None,
                        "prior_grn_by_group": None,
                        "global_network": "datasets/bench__dyngen_cfg__r01/truth/global_network.csv",
                        "group_networks_dir": None,
                    },
                )

            with (
                patch.object(
                    pipeline, "validate_simulation_plan", return_value=resolved
                ),
                patch.object(
                    pipeline,
                    "_load_simulator_catalog",
                    return_value=(
                        {
                            "scenario_request": {"type": "object"},
                            "simulator_runs": {"type": "object"},
                            "simulation_plan": {"type": "object"},
                            "benchmark_manifest": {"type": "object"},
                        },
                        {},
                    ),
                ),
                patch.object(
                    pipeline,
                    "preflight_generate_data_scenario",
                    return_value={"status": "ok"},
                ),
                patch.object(
                    pipeline,
                    "_execute_simulation_task",
                    side_effect=fake_execute_simulation_task,
                ),
            ):
                benchmark_root = pipeline.run_generate_data(
                    plan_path=plan_path,
                    output_dir=tmp_root / "out",
                    show_progress=False,
                    progress_callback=events.append,
                )

            self.assertEqual(benchmark_root.parent, (tmp_root / "out").resolve())
            self.assertRegex(benchmark_root.name, r"^bench_\d{8}T\d{6}Z$")
            self.assertGreaterEqual(len(events), 2)
            running = [event for event in events if event["phase"] == "run_simulator"][
                0
            ]
            self.assertEqual(running["task_id"], "dyngen_cfg__r01")
            self.assertEqual(running["run_id"], "dyngen_cfg")
            self.assertEqual(running["simulator_id"], "dyngen")
            self.assertEqual(running["replicate_index"], 1)
            self.assertEqual(running["seed"], 11)
            self.assertEqual(running["percent"], 42)
            self.assertEqual(running["status"], "running")
            self.assertIn("updated_at", running)


if __name__ == "__main__":
    unittest.main()
