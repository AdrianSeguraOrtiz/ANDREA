from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # noqa: BLE001
    TestClient = None

try:
    from andrea.gui.generate_data import server as gui_server
except Exception:  # noqa: BLE001
    gui_server = None


class _ImmediateThread:
    def __init__(
        self, *, target=None, kwargs=None, daemon=None
    ):  # noqa: ANN001, ANN204
        self._target = target
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self) -> None:
        if self._target is not None:
            self._target(**self._kwargs)


def _add_eta_contract(plan_payload: dict[str, object], *, max_parallel_tasks: int) -> None:
    eta_provenance = {"eta_source": "test_fixture", "warnings": []}
    tasks = plan_payload["tasks"]
    assert isinstance(tasks, list)
    waves = []
    for idx, task in enumerate(tasks, start=1):
        assert isinstance(task, dict)
        start = float((idx - 1) * 60)
        end = float(idx * 60)
        task["ram_gb"] = 4.0
        task["eta_seconds"] = 60.0
        task["eta_source"] = "test_fixture"
        task["eta_start_seconds"] = start
        task["eta_end_seconds"] = end
        task["eta_wave"] = idx
        task["eta_provenance"] = eta_provenance
        waves.append(
            {
                "index": idx,
                "threads_used": 1,
                "ram_gb_used": 4.0,
                "eta_seconds": 60.0,
                "eta_start_seconds": start,
                "eta_end_seconds": end,
                "tasks": [
                    {
                        "task_id": task["task_id"],
                        "run_id": task["run_id"],
                        "simulator_id": task["simulator_id"],
                        "threads": 1,
                        "ram_gb": 4.0,
                        "eta_seconds": 60.0,
                        "eta_source": "test_fixture",
                        "eta_start_seconds": start,
                        "eta_end_seconds": end,
                    }
                ],
            }
        )
    runs = plan_payload["runs"]
    assert isinstance(runs, list)
    for run in runs:
        assert isinstance(run, dict)
        run["ram_gb"] = 4.0
        run["eta_seconds"] = 60.0 * len(tasks)
        run["eta_source"] = "test_fixture"
        run["eta_start_seconds"] = 0.0
        run["eta_end_seconds"] = 60.0 * len(tasks)
        run["eta_provenance"] = eta_provenance
    plan_payload["execution"] = {
        "max_parallel_tasks": max_parallel_tasks,
        "max_cores": max_parallel_tasks,
        "max_ram_gb": 8.0,
        "eta_total_seconds": 60.0 * len(tasks),
        "waves": waves,
        "warnings": [],
    }


@unittest.skipIf(
    TestClient is None or gui_server is None,
    "GUI test dependencies are not installed",
)
class GenerateDataV2GuiServerTests(unittest.TestCase):
    def test_bootstrap_exposes_profile_extras_and_simulator_inputs(self) -> None:
        client = TestClient(gui_server.create_app())
        payload = client.get("/api/generate-data/bootstrap").json()
        profiles = {item["id"]: item for item in payload["profiles"]}
        self.assertNotIn(
            "lineage_tree", profiles["bulk_steady_state"]["available_extras"]
        )
        self.assertIn("groups", profiles["scrna_grouped"]["available_extras"])
        self.assertIn("lineage_tree", profiles["scrna_grouped"]["available_extras"])
        self.assertIn("group_networks", profiles["scrna_grouped"]["available_extras"])
        inputs = {item["id"]: item for item in payload["simulator_inputs"]}
        self.assertIn("grn_params", inputs)
        self.assertIn("tree_newick", inputs)
        self.assertIn("target\tregulator\teffect", inputs["grn_params"]["example"])
        self.assertIn("pop_a", inputs["tree_newick"]["example"])
        self.assertIn("scmultisim", inputs["grn_params"]["supported_by"])
        conditional_usage = inputs["grn_params"]["used_by"]["conditional"]
        self.assertEqual(conditional_usage[0]["simulator_id"], "scmultisim")
        self.assertIn("grn_source=input_tsv", conditional_usage[0]["message"])
        planning_defaults = payload["planning_defaults"]
        self.assertGreaterEqual(planning_defaults["max_cores"], 1)
        self.assertGreaterEqual(planning_defaults["max_ram_gb"], 1.0)
        simulators = {item["simulator_id"]: item for item in payload["simulators"]}
        grouped_native_outputs = {
            item["id"]
            for item in simulators["dyngen"]["profile_capabilities"]["scrna_grouped"][
                "native_outputs"
            ]
        }
        self.assertIn("rna_velocity", grouped_native_outputs)
        self.assertIn("regulatory_network_sc", grouped_native_outputs)
        scmultisim_outputs = {
            item["id"]: item
            for item in simulators["scmultisim"]["profile_capabilities"]["scrna_global"][
                "native_outputs"
            ]
        }
        self.assertEqual(
            scmultisim_outputs["observed_counts"]["conditions"][0]["field"],
            "param.technical_noise.enabled",
        )

    def test_preflight_plan_run_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)

            def fake_preflight(scenario_path):  # noqa: ANN001
                payload = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
                return {
                    "schema_version": "1.0",
                    "scenario": {
                        "id": payload["id"],
                        "profile": payload["profile"],
                        "organism": payload["organism"],
                        "requested_extras": payload["requested_extras"],
                        "effective_extras": ["groups", "lineage_tree"],
                        "inputs": {},
                        "base_seed": payload.get("base_seed", 100),
                    },
                    "catalog_summary": {
                        "total": 1,
                        "eligible": 1,
                        "warning": 0,
                        "blocked": 0,
                    },
                    "eligible": [
                        {
                            "simulator_id": "dyngen",
                            "name": "dyngen",
                            "requested_profile": "scrna_grouped",
                            "requested_extras": ["lineage_tree"],
                            "effective_extras": ["groups", "lineage_tree"],
                            "inputs_used": [],
                            "native_extras_used": [],
                            "derived_extras_used": ["groups", "lineage_tree"],
                            "truth_outputs": {
                                "global_network": "native",
                                "group_networks": "derivable",
                            },
                            "status": "eligible",
                            "issues": [],
                        }
                    ],
                    "warning": [],
                    "blocked": [],
                }

            def fake_plan(**kwargs):  # noqa: ANN003
                simulator_runs = json.loads(
                    Path(kwargs["simulator_runs_path"]).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    simulator_runs["runs"][0]["native_outputs"],
                    ["rna_velocity"],
                )
                plan_payload = {
                    "schema_version": "1.0",
                    "id": "gui_generate_test",
                    "profile": "scrna_grouped",
                    "organism": {"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                    "requested_extras": ["lineage_tree"],
                    "effective_extras": ["groups", "lineage_tree"],
                    "inputs": {},
                    "runs": [
                        {
                            "run_id": "dyngen_a",
                            "simulator_id": "dyngen",
                            "simulator_params": {},
                            "runtime_resources": {"threads": 1},
                            "replicates": 2,
                            "native_outputs": ["rna_velocity"],
                            "base_seed": 100,
                            "replicate_seeds": [100, 101],
                        }
                    ],
                    "tasks": [
                        {
                            "task_id": "dyngen_a__r01",
                            "run_id": "dyngen_a",
                            "simulator_id": "dyngen",
                            "replicate_index": 1,
                            "seed": 100,
                            "dataset_id": "gui_generate_test__dyngen_a__r01",
                            "runtime_resources": {"threads": 1},
                        },
                        {
                            "task_id": "dyngen_a__r02",
                            "run_id": "dyngen_a",
                            "simulator_id": "dyngen",
                            "replicate_index": 2,
                            "seed": 101,
                            "dataset_id": "gui_generate_test__dyngen_a__r02",
                            "runtime_resources": {"threads": 1},
                        },
                    ],
                    "execution": {},
                    "base_seed": 100,
                }
                _add_eta_contract(
                    plan_payload,
                    max_parallel_tasks=kwargs["max_parallel_tasks"],
                )
                kwargs["output_path"].write_text(
                    json.dumps(plan_payload, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                return kwargs["output_path"]

            def fake_run(**kwargs):  # noqa: ANN003
                callback = kwargs["progress_callback"]
                callback(
                    {
                        "task_id": "dyngen_a__r01",
                        "run_id": "dyngen_a",
                        "simulator_id": "dyngen",
                        "replicate_index": 1,
                        "seed": 100,
                        "dataset_id": "gui_generate_test__dyngen_a__r01",
                        "percent": 100,
                        "status": "completed",
                        "phase": "done",
                        "message": "done",
                        "updated_at": "2026-04-22T00:00:00Z",
                    }
                )
                benchmark_root = kwargs["output_dir"] / "gui_generate_test"
                input_dir = benchmark_root / "input"
                dataset_dir = (
                    benchmark_root / "datasets" / "gui_generate_test__dyngen_a__r01"
                )
                input_dir.mkdir(parents=True, exist_ok=True)
                (dataset_dir / "truth").mkdir(parents=True, exist_ok=True)
                (dataset_dir / "extras").mkdir(parents=True, exist_ok=True)
                (dataset_dir / "provenance").mkdir(parents=True, exist_ok=True)
                (input_dir / "scenario-request.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "id": "gui_generate_test",
                            "profile": "scrna_grouped",
                            "requested_extras": ["lineage_tree"],
                            "organism": {
                                "taxonomic_group": "synthetic",
                                "ncbi_taxon_id": None,
                            },
                            "base_seed": 100,
                        },
                        indent=2,
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (input_dir / "simulator-runs.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "runs": [
                                {
                                    "run_id": "dyngen_a",
                                    "simulator_id": "dyngen",
                                    "replicates": 2,
                                    "native_outputs": ["rna_velocity"],
                                    "params": {},
                                }
                            ],
                        },
                        indent=2,
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (benchmark_root / "preflight-report.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "scenario": {
                                "id": "gui_generate_test",
                                "profile": "scrna_grouped",
                                "organism": {
                                    "taxonomic_group": "synthetic",
                                    "ncbi_taxon_id": None,
                                },
                                "requested_extras": ["lineage_tree"],
                                "effective_extras": ["groups", "lineage_tree"],
                                "inputs": {},
                                "base_seed": 100,
                            },
                            "catalog_summary": {
                                "total": 1,
                                "eligible": 1,
                                "warning": 0,
                                "blocked": 0,
                            },
                            "eligible": [],
                            "warning": [],
                            "blocked": [],
                        },
                        indent=2,
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                frozen_plan = {
                            "schema_version": "1.0",
                            "id": "gui_generate_test",
                            "profile": "scrna_grouped",
                            "organism": {
                                "taxonomic_group": "synthetic",
                                "ncbi_taxon_id": None,
                            },
                            "requested_extras": ["lineage_tree"],
                            "effective_extras": ["groups", "lineage_tree"],
                            "inputs": {},
                            "runs": [
                                {
                                    "run_id": "dyngen_a",
                                    "simulator_id": "dyngen",
                                    "simulator_params": {},
                                    "runtime_resources": {"threads": 1},
                                    "replicates": 2,
                                    "native_outputs": ["rna_velocity"],
                                    "base_seed": 100,
                                    "replicate_seeds": [100, 101],
                                }
                            ],
                            "tasks": [
                                {
                                    "task_id": "dyngen_a__r01",
                                    "run_id": "dyngen_a",
                                    "simulator_id": "dyngen",
                                    "replicate_index": 1,
                                    "seed": 100,
                                    "dataset_id": "gui_generate_test__dyngen_a__r01",
                                    "runtime_resources": {"threads": 1},
                                },
                                {
                                    "task_id": "dyngen_a__r02",
                                    "run_id": "dyngen_a",
                                    "simulator_id": "dyngen",
                                    "replicate_index": 2,
                                    "seed": 101,
                                    "dataset_id": "gui_generate_test__dyngen_a__r02",
                                    "runtime_resources": {"threads": 1},
                                },
                            ],
                            "execution": {},
                            "base_seed": 100,
                        }
                _add_eta_contract(frozen_plan, max_parallel_tasks=2)
                (benchmark_root / "simulation-plan.json").write_text(
                    json.dumps(frozen_plan, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                (benchmark_root / "benchmark-manifest.json").write_text(
                    '{"schema_version":"1.0","id":"gui_generate_test"}\n',
                    encoding="utf-8",
                )
                (dataset_dir / "dataset-manifest.json").write_text(
                    '{"schema_version":"1.0"}\n', encoding="utf-8"
                )
                (dataset_dir / "ground-truth-manifest.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "outputs": {"gene_universe": "truth/gene_universe.txt"},
                        },
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (dataset_dir / "expression.tsv").write_text(
                    "gene\tC1\nG1\t1\nG2\t2\n", encoding="utf-8"
                )
                (dataset_dir / "extras" / "groups.tsv").write_text(
                    "cell\tcluster\nC1\tA\n", encoding="utf-8"
                )
                (dataset_dir / "truth" / "global_network.csv").write_text(
                    "source,target,score,sign,evidence,context\nG1,G2,1,+,simulated_truth,global\n",
                    encoding="utf-8",
                )
                (dataset_dir / "truth" / "gene_universe.txt").write_text(
                    "G1\nG2\n",
                    encoding="utf-8",
                )
                (
                    dataset_dir / "provenance" / "simulator-output-manifest.json"
                ).write_text('{"schema_version":"1.0"}\n', encoding="utf-8")
                (dataset_dir / "provenance" / "simulator-run.json").write_text(
                    '{"schema_version":"1.0"}\n', encoding="utf-8"
                )
                (dataset_dir / "provenance" / "progress.json").write_text(
                    '{"status":"completed","percent":100}\n', encoding="utf-8"
                )
                return benchmark_root

            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(gui_server, "STATE", gui_server.GuiState()),
                patch.object(gui_server.threading, "Thread", _ImmediateThread),
                patch.object(
                    gui_server,
                    "preflight_generate_data_scenario",
                    side_effect=fake_preflight,
                ),
                patch.object(
                    gui_server,
                    "plan_generate_data_request",
                    side_effect=fake_plan,
                ),
                patch.object(gui_server, "run_generate_data", side_effect=fake_run),
            ):
                client = TestClient(gui_server.create_app())

                preflight_response = client.post(
                    "/api/generate-data/preflight",
                    data={
                        "config": json.dumps(
                            {
                                "scenario": {
                                    "id": "gui_generate_test",
                                    "profile": "scrna_grouped",
                                    "requested_extras": ["lineage_tree"],
                                    "organism": {
                                        "taxonomic_group": "synthetic",
                                        "ncbi_taxon_id": None,
                                    },
                                    "base_seed": 100,
                                },
                                "options": {"output_dir": str(tmp_root / "benchmarks")},
                            }
                        )
                    },
                )
                self.assertEqual(
                    preflight_response.status_code, 200, msg=preflight_response.text
                )
                job_id = preflight_response.json()["job_id"]

                job_payload = client.get(f"/api/generate-data/jobs/{job_id}").json()
                self.assertEqual(job_payload["job"]["stage"], "preflight_ok")
                self.assertEqual(
                    job_payload["preflight_report"]["catalog_summary"]["eligible"],
                    1,
                )

                plan_response = client.post(
                    "/api/generate-data/plan",
                    json={
                        "job_id": job_id,
                        "runs": [
                            {
                                "run_id": "dyngen_a",
                                "simulator_id": "dyngen",
                                "replicates": 2,
                                "native_outputs": ["rna_velocity"],
                                "params": {},
                            }
                        ],
                        "options": {
                            "max_parallel_tasks": 2,
                            "max_cores": 2,
                            "max_ram_gb": 8,
                        },
                    },
                )
                self.assertEqual(plan_response.status_code, 200, msg=plan_response.text)
                plan_payload = client.get(
                    f"/api/generate-data/jobs/{job_id}/plan"
                ).json()
                self.assertEqual(
                    plan_payload["plan"]["execution"]["max_parallel_tasks"], 2
                )
                self.assertEqual(len(plan_payload["plan"]["tasks"]), 2)

                run_response = client.post(
                    "/api/generate-data/run",
                    json={
                        "job_id": job_id,
                        "options": {
                            "progress_poll_seconds": 0.1,
                        },
                    },
                )
                self.assertEqual(run_response.status_code, 200, msg=run_response.text)

                job_payload = client.get(f"/api/generate-data/jobs/{job_id}").json()
                self.assertEqual(job_payload["job"]["stage"], "executed")
                self.assertEqual(
                    job_payload["runtime_progress"]["summary"]["completed"], 1
                )
                self.assertTrue(job_payload["reproducibility"]["available"])
                self.assertIn(
                    "/input/scenario-request.json",
                    job_payload["reproducibility"]["cli"]["primary_code"],
                )
                self.assertNotIn(
                    "/gui_tmp/",
                    job_payload["reproducibility"]["cli"]["primary_code"],
                )

                files_response = client.get(
                    f"/api/generate-data/jobs/{job_id}/files?mode=light"
                )
                self.assertEqual(
                    files_response.status_code, 200, msg=files_response.text
                )
                entries = files_response.json()["entries"]
                self.assertTrue(
                    any(
                        item["path"] == "benchmark/benchmark-manifest.json"
                        for item in entries
                    )
                )
                self.assertTrue(
                    any(
                        item["path"] == "benchmark/input/scenario-request.json"
                        for item in entries
                    )
                )

                content_response = client.get(
                    f"/api/generate-data/jobs/{job_id}/file-content",
                    params={
                        "mode": "light",
                        "path": "benchmark/benchmark-manifest.json",
                    },
                )
                self.assertEqual(
                    content_response.status_code, 200, msg=content_response.text
                )
                self.assertEqual(content_response.json()["viewer"], "json")
                self.assertIn("guide", content_response.json())

                bundle_response = client.get(
                    f"/api/generate-data/jobs/{job_id}/bundle?mode=light"
                )
                self.assertEqual(bundle_response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
