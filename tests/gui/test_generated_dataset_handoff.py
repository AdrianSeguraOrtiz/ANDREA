"""Exercise generated inputs through GUI inference and evaluation contracts.

Only simulator/container execution is replaced by small deterministic outputs.
Generation packaging, GUI uploads, inference planning/fingerprints, result
assembly, analysis bundles, and evaluation use the production implementations.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from andrea.core.commands.generate_data import pipeline as generation
from andrea.core.commands.generate_data.bundles import resolve_bundle
from andrea.core.commands.generate_data.catalog import _load_simulator_catalog
from andrea.core.commands.generate_data.shared import ResolvedSimulatorRun
from andrea.core.commands.infer_network.commons.shared import ToolExecutionResult
from tests.gui.helpers import start_immediate_background_thread

try:
    from fastapi.testclient import TestClient

    from andrea.gui.evaluate_inference import server as evaluation_gui
    from andrea.gui.infer_network import server as inference_gui
except ImportError:
    TestClient = None


def _simulator_output(*, stage_dir: Path, request, seed: int, **_kwargs) -> Path:
    files = {
        "expression.tsv": "gene\tC1\tC2\tC3\nG1\t1\t2\t3\nG2\t3\t1\t2\nG3\t2\t3\t1\n",
        "extras/tf_list.txt": "G1\nG2\nG3\n",
        "truth/gene_universe.txt": "G1\nG2\nG3\n",
        "truth/networks.csv": (
            "source,target,score,sign,evidence,context\n"
            "G1,G2,1,+,simulated_truth,global\n"
        ),
    }
    for relative, content in files.items():
        target = stage_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "simulator_id": request.simulator_id,
        "data_axes": request.data_axes,
        "truth_requirements": request.truth_requirements,
        "seed": seed,
        "expression": {
            "path": "expression.tsv",
            "genes": 3,
            "columns": 3,
            "column_kind": "cells",
        },
        "extras": {"tf_list": "extras/tf_list.txt"},
        "native_outputs": {},
        "truth": {
            "gene_universe": "truth/gene_universe.txt",
            "networks": "truth/networks.csv",
        },
        "provenance": {"raw_dir": "provenance/raw"},
    }
    path = stage_dir / "simulator-output-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _inference_output(*, wave, runtime_io_by_tool, **_kwargs):
    results = {}
    for task in wave.tasks:
        network = runtime_io_by_tool[task.tool_id].out_dir / "network.csv"
        network.write_text(
            "source,target,score,sign,evidence,context\nG1,G2,1,?,inferred,global\n",
            encoding="utf-8",
        )
        results[task.tool_id] = ToolExecutionResult(
            tool_id=task.tool_id,
            status="completed",
            exit_code=0,
            measurement={"wall_time_seconds": 0.01},
            network_path=str(network),
            progress_path=None,
            logs_path=None,
            error=None,
        )
    return results


@unittest.skipIf(TestClient is None, "GUI test dependencies are not installed")
class GeneratedDatasetHandoffTests(unittest.TestCase):
    def test_uploaded_generated_inputs_match_by_content_not_gui_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            benchmark = root / "generated"
            dataset_id = "named_benchmark__dyngen_default__r01"
            schemas, catalog = _load_simulator_catalog()
            run = ResolvedSimulatorRun(
                request_id="named_benchmark",
                run_id="dyngen_default",
                simulator_id="dyngen",
                data_axes={
                    "measurement": "rna_expression",
                    "resolution": "single_cell",
                    "column_kind": "cells",
                    "experimental_design": "trajectory",
                },
                truth_requirements={"contexts": ["global"]},
                organism={"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                requested_extras=[],
                effective_extras=["tf_list"],
                inputs={},
                resolved_input_paths={},
                simulator_params={},
                runtime_resources={"threads": 1},
                native_outputs=[],
                replicates=1,
                base_seed=1,
                replicate_seeds=[1],
                notes=None,
                simulator_spec=catalog["dyngen"],
                ram_gb=1.0,
            )
            with patch.object(
                generation, "_run_simulator", side_effect=_simulator_output
            ):
                generation._execute_simulation_task(
                    task={
                        "run_id": run.run_id,
                        "task_id": "dyngen_default__r01",
                        "dataset_id": dataset_id,
                        "seed": 1,
                    },
                    resolved=SimpleNamespace(simulator_runs=[run]),
                    schemas=schemas,
                    staging_root=root / "stage",
                    datasets_root=benchmark / "datasets",
                    benchmark_root=benchmark,
                    progress_poll_seconds=0.1,
                    show_progress=False,
                    progress_callback=None,
                )
            dataset = benchmark / "datasets" / dataset_id
            truth = json.loads((dataset / "ground-truth-manifest.json").read_text())
            resolution = resolve_bundle(
                bundle_id="analysis",
                benchmark_root=benchmark,
                dataset_id=dataset_id,
            )
            self.assertTrue(resolution.available, msg=resolution.missing_required)
            truth_buffer = io.BytesIO()
            with zipfile.ZipFile(truth_buffer, "w") as archive:
                for name, source in resolution.source_tuples:
                    archive.write(source, name)

            for changed in (None, "expression", "tf_list"):
                with self.subTest(changed=changed):
                    case_root = root / (changed or "same_content")
                    case_root.mkdir()
                    expression = (dataset / "expression.tsv").read_bytes()
                    tf_list = (dataset / "extras/tf_list.txt").read_bytes()
                    if changed == "expression":
                        expression = expression.replace(b"G1\t1\t2\t3", b"G1\t9\t2\t3")
                    if changed == "tf_list":
                        tf_list = b"G1\nG2\n"
                    # Changed-content cases deliberately keep the original ID:
                    # equal labels must not override incompatible input bytes.
                    uploaded_id = dataset_id if changed else "gui_dataset"
                    with (
                        patch.object(
                            inference_gui, "GUI_TMP_ROOT", case_root / "infer_gui"
                        ),
                        patch.object(inference_gui, "STATE", inference_gui.GuiState()),
                        patch.object(
                            inference_gui,
                            "start_background_thread",
                            start_immediate_background_thread,
                        ),
                        patch(
                            "andrea.core.commands.infer_network.run._ensure_docker_cli"
                        ),
                        patch(
                            "andrea.core.commands.infer_network.run._run_wave",
                            side_effect=_inference_output,
                        ),
                    ):
                        client = TestClient(inference_gui.create_app())
                        response = client.post(
                            "/api/infer-network/preflight",
                            data={
                                "config": json.dumps(
                                    {
                                        "dataset": {
                                            "id": uploaded_id,
                                            "column_kind": "cells",
                                            "expression_profile": "scrna",
                                            "organism": run.organism,
                                        },
                                        "options": {
                                            "output_dir": str(case_root / "inference")
                                        },
                                    }
                                )
                            },
                            files={
                                "expression_file": (
                                    "renamed-expression.tsv",
                                    expression,
                                    "text/tab-separated-values",
                                ),
                                "extra__tf_list": (
                                    "renamed-regulators.txt",
                                    tf_list,
                                    "text/plain",
                                ),
                            },
                        )
                        self.assertEqual(response.status_code, 200, msg=response.text)
                        job_id = response.json()["job_id"]
                        job = client.get(f"/api/infer-network/jobs/{job_id}").json()
                        self.assertEqual(job["job"]["stage"], "preflight_ok", msg=job)
                        response = client.post(
                            "/api/infer-network/plan",
                            json={
                                "job_id": job_id,
                                "runs": [
                                    {
                                        "run_id": "genie3_01",
                                        "tool_id": "genie3",
                                        "params": {},
                                    }
                                ],
                                "options": {
                                    "planner": "heuristic",
                                    "max_cores": 1,
                                    "max_ram_gb": 1.0,
                                    "output_profile": "canonical",
                                },
                            },
                        )
                        self.assertEqual(response.status_code, 200, msg=response.text)
                        job = client.get(f"/api/infer-network/jobs/{job_id}").json()
                        self.assertEqual(job["job"]["stage"], "planned", msg=job)
                        response = client.post(
                            "/api/infer-network/run", json={"job_id": job_id}
                        )
                        self.assertEqual(response.status_code, 200, msg=response.text)
                        job = client.get(f"/api/infer-network/jobs/{job_id}").json()
                        self.assertEqual(job["job"]["stage"], "executed", msg=job)
                        inferred = job["run_report"]["dataset"]
                        self.assertEqual(inferred["id"], uploaded_id)
                        if changed:
                            self.assertNotEqual(
                                inferred["fingerprint"], truth["dataset_fingerprint"]
                            )
                        else:
                            self.assertEqual(
                                inferred["fingerprint"], truth["dataset_fingerprint"]
                            )
                        response = client.get(
                            f"/api/infer-network/jobs/{job_id}/bundle",
                            params={"bundle_id": "analysis"},
                        )
                        self.assertEqual(response.status_code, 200, msg=response.text)
                        inference_zip = response.content

                    with (
                        patch.object(
                            evaluation_gui, "GUI_TMP_ROOT", case_root / "eval_gui"
                        ),
                        patch.object(
                            evaluation_gui, "STATE", evaluation_gui.GuiState()
                        ),
                        patch.object(
                            evaluation_gui,
                            "start_background_thread",
                            start_immediate_background_thread,
                        ),
                    ):
                        client = TestClient(evaluation_gui.create_app())
                        response = client.post(
                            "/api/evaluate-inference/run",
                            data={"output_dir": str(case_root / "evaluation")},
                            files={
                                "inference_zip": (
                                    "inference.zip",
                                    inference_zip,
                                    "application/zip",
                                ),
                                "truth_zip": (
                                    "truth.zip",
                                    truth_buffer.getvalue(),
                                    "application/zip",
                                ),
                            },
                        )
                        self.assertEqual(response.status_code, 200, msg=response.text)
                        result = response.json()
                        if changed:
                            self.assertEqual(
                                result["job"]["status"], "failed", msg=result
                            )
                            self.assertIn(
                                "dataset fingerprints must match exactly",
                                result["job"]["error"],
                            )
                            self.assertFalse((case_root / "evaluation").exists())
                        else:
                            self.assertEqual(
                                result["job"]["status"], "completed", msg=result
                            )
                            report = result["evaluation_report"]
                            self.assertEqual(
                                report["inputs"]["inference_dataset_id"], "gui_dataset"
                            )
                            self.assertEqual(
                                report["inputs"]["ground_truth_dataset_id"], dataset_id
                            )
                            self.assertEqual(
                                report["inference_run"]["dataset"]["id"], "gui_dataset"
                            )
                            self.assertEqual(
                                report["ground_truth"]["dataset_id"], dataset_id
                            )
                            self.assertEqual(
                                report["pairings"][0]["status"], "evaluated"
                            )
                            metric = next(
                                row
                                for row in report["metrics"]
                                if row["level"] == "directed"
                            )
                            self.assertEqual(metric["auroc"], 1.0)


if __name__ == "__main__":
    unittest.main()
