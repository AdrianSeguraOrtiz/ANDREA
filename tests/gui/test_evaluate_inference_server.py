from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tests.gui.helpers import start_immediate_background_thread

try:
    from fastapi.testclient import TestClient
except Exception:  # noqa: BLE001
    TestClient = None

try:
    from andrea.gui.evaluate_inference import server as gui_server
except Exception:  # noqa: BLE001
    gui_server = None


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


@unittest.skipIf(
    TestClient is None or gui_server is None,
    "GUI test dependencies are not installed",
)
class EvaluateInferenceGuiServerTests(unittest.TestCase):
    def test_static_gui_recommends_analysis_bundles(self) -> None:
        index = (Path(gui_server.STATIC_DIR) / "index.html").read_text(encoding="utf-8")
        style = (Path(gui_server.STATIC_DIR) / "styles.css").read_text(encoding="utf-8")
        repro_style = (
            Path(gui_server.COMMON_STATIC_DIR) / "app" / "repro" / "styles.css"
        ).read_text(encoding="utf-8")
        toast_style = (
            Path(gui_server.COMMON_STATIC_DIR) / "app" / "ui" / "toasts.css"
        ).read_text(encoding="utf-8")
        popover_style = (
            Path(gui_server.COMMON_STATIC_DIR) / "app" / "ui" / "popovers.css"
        ).read_text(encoding="utf-8")
        uploads_style = (
            Path(gui_server.COMMON_STATIC_DIR) / "app" / "uploads" / "styles.css"
        ).read_text(encoding="utf-8")
        script = (Path(gui_server.STATIC_DIR) / "app" / "main.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("Required Analysis ZIPs", index)
        self.assertIn("Infer-network analysis ZIP", index)
        self.assertIn("Generate-data analysis ZIP", index)
        self.assertIn("Full archives and nested benchmark/run ZIPs are rejected", index)
        self.assertIn("run_report.json", index)
        self.assertIn("merged_network_raw.csv", index)
        self.assertIn("truth/networks.csv", index)
        self.assertIn("CLI and Python users do not need ZIP handoff", index)
        self.assertIn("/static-common/app/bundles/styles.css", index)
        self.assertIn("/static-common/app/uploads/styles.css", index)
        self.assertIn("/static-common/app/repro/styles.css", index)
        self.assertIn("/static-common/app/ui/popovers.css", index)
        self.assertIn("/static-common/app/ui/toasts.css", index)
        self.assertIn(".repro-card {", repro_style)
        self.assertNotIn(".repro-card {", style)
        self.assertIn(".toast {", toast_style)
        self.assertIn(".info-popover {", popover_style)
        self.assertIn(".handoff-card {", uploads_style)
        self.assertIn(".file-card {", uploads_style)
        self.assertNotIn(".toast {", style)
        self.assertNotIn(".info-popover {", style)
        self.assertNotIn(".handoff-card {", style)
        self.assertNotIn(".file-card {", style)
        self.assertIn("bundle-modal", index)
        self.assertIn("upload-progress-panel", index)
        self.assertIn("openBundleDownloadModal", script)
        self.assertIn("/bundles", script)
        self.assertIn("bundle_id=", script)
        self.assertIn("uploadFormDataWithProgress", script)
        legacy_bundle_name = "light " + "bundle"
        legacy_bundle_adjective = "light" + "weight"
        self.assertNotIn(legacy_bundle_name, index.lower())
        self.assertNotIn(legacy_bundle_adjective, index.lower())
        self.assertNotIn(legacy_bundle_adjective, script.lower())
        self.assertIn("XMLHttpRequest", (Path(gui_server.COMMON_STATIC_DIR) / "app" / "uploads" / "progress.js").read_text(encoding="utf-8"))
        self.assertNotIn("readAs", script)

    def test_strict_analysis_uploads_run_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            calls: list[dict[str, Path]] = []

            def fake_evaluate_inference(**kwargs):  # noqa: ANN003
                calls.append(dict(kwargs))
                output_dir = Path(kwargs["output_dir"])
                evaluation_dir = output_dir / "evaluation_fake"
                evaluation_dir.mkdir(parents=True)
                report = {
                    "schema_version": "1.0",
                    "outputs": {
                        "evaluation_dir": "evaluation_fake",
                        "evaluation_report": "evaluation_fake/evaluation_report.json",
                        "metrics_csv": "evaluation_fake/metrics.csv",
                        "pairings_csv": "evaluation_fake/pairings.csv",
                        "evaluation_view": "evaluation_fake/evaluation_view.html",
                    },
                    "metrics": [{"tool_id": "genie3", "status": "ok"}],
                }
                (evaluation_dir / "evaluation_report.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                (evaluation_dir / "metrics.csv").write_text(
                    "tool_id,status\ngenie3,ok\n", encoding="utf-8"
                )
                (evaluation_dir / "pairings.csv").write_text(
                    "tool_id,status\ngenie3,evaluated\n", encoding="utf-8"
                )
                (evaluation_dir / "evaluation_view.html").write_text(
                    "<html></html>", encoding="utf-8"
                )
                return report

            inference_zip = _zip_bytes(
                {
                    "run_report.json": json.dumps(
                        {
                            "run_id": "run_001",
                            "status": "completed",
                            "dataset": {"id": "dataset_a"},
                            "outputs": {"merged_network_raw": "merged_network_raw.csv"},
                        }
                    ),
                    "merged_network_raw.csv": "source,target,score,sign,evidence,context,tool_id\n",
                }
            )
            truth_zip = _zip_bytes(
                {
                    "ground-truth-manifest.json": json.dumps(
                        {
                            "schema_version": "1.0",
                            "dataset_id": "dataset_a",
                            "simulator_id": "dyngen",
                            "data_axes": {
                                "measurement": "rna_expression",
                                "resolution": "single_cell",
                                "column_kind": "cells",
                                "experimental_design": "trajectory",
                            },
                            "truth_requirements": {
                                "contexts": ["global", "group"],
                            },
                            "outputs": {
                                "gene_universe": "truth/gene_universe.txt",
                                "networks": "truth/networks.csv",
                            },
                        }
                    ),
                    "truth/gene_universe.txt": "G1\nG2\n",
                    "truth/networks.csv": "source,target,score,sign,evidence,context\n",
                }
            )

            state = gui_server.GuiState()
            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(gui_server, "STATE", state),
                patch.object(
                    gui_server,
                    "start_background_thread",
                    start_immediate_background_thread,
                ),
                patch.object(
                    gui_server,
                    "evaluate_inference",
                    side_effect=fake_evaluate_inference,
                ),
            ):
                client = TestClient(gui_server.create_app())
                response = client.post(
                    "/api/evaluate-inference/run",
                    data={"output_dir": str(tmp_root / "evaluations")},
                    files={
                        "inference_zip": (
                            "inference.zip",
                            inference_zip,
                            "application/zip",
                        ),
                        "truth_zip": ("truth.zip", truth_zip, "application/zip"),
                    },
                )
                bundles_response = client.get(
                    f"/api/evaluate-inference/jobs/{response.json()['job']['job_id']}/bundles"
                )
                analysis_bundle_response = client.get(
                    f"/api/evaluate-inference/jobs/{response.json()['job']['job_id']}/bundle",
                    params={"bundle_id": "analysis"},
                )
                invalid_bundle_response = client.get(
                    f"/api/evaluate-inference/jobs/{response.json()['job']['job_id']}/bundle",
                    params={"bundle_id": "not_a_bundle"},
                )

            self.assertEqual(response.status_code, 200, msg=response.text)
            payload = response.json()
            self.assertEqual(payload["job"]["status"], "completed")
            self.assertEqual(payload["job"]["progress_percent"], 100)
            self.assertGreaterEqual(len(payload["job"]["timings"]), 3)
            self.assertEqual(payload["evaluation_report"]["metrics"][0]["tool_id"], "genie3")
            self.assertTrue(payload["reproducibility"]["available"])
            self.assertIn(
                "andrea evaluate-inference",
                payload["reproducibility"]["cli"]["primary_code"],
            )
            self.assertNotIn(
                "/gui_tmp/",
                payload["reproducibility"]["cli"]["primary_code"],
            )
            self.assertIn(
                "evaluate_inference(",
                payload["reproducibility"]["python"]["primary_code"],
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["output_dir"], tmp_root / "evaluations")
            self.assertTrue(calls[0]["run_report_path"].exists())
            self.assertTrue(calls[0]["ground_truth_manifest_path"].exists())
            self.assertTrue(calls[0]["generate_view"])
            self.assertTrue(
                Path(payload["job"]["frozen_run_report_path"]).is_relative_to(
                    tmp_root / "evaluations"
                )
            )
            self.assertEqual(
                bundles_response.status_code, 200, msg=bundles_response.text
            )
            bundles_by_id = {
                item["id"]: item for item in bundles_response.json()["bundles"]
            }
            self.assertEqual(sorted(bundles_by_id), ["analysis", "full", "report"])
            self.assertTrue(bundles_by_id["analysis"]["available"])
            self.assertEqual(bundles_by_id["analysis"]["file_count"], 1)
            self.assertIn(
                "compare-networks",
                bundles_by_id["analysis"]["intended_downstream_commands"],
            )
            self.assertEqual(
                analysis_bundle_response.status_code,
                200,
                msg=analysis_bundle_response.text,
            )
            with zipfile.ZipFile(io.BytesIO(analysis_bundle_response.content)) as zf:
                self.assertEqual(zf.namelist(), ["evaluation_report.json"])
            self.assertEqual(invalid_bundle_response.status_code, 400)

    def test_nested_full_zip_layout_upload_is_rejected_server_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            calls: list[dict[str, Path]] = []

            def fake_evaluate_inference(**kwargs):  # noqa: ANN003
                calls.append(dict(kwargs))
                evaluation_dir = Path(kwargs["output_dir"]) / "evaluation_full_zip"
                evaluation_dir.mkdir(parents=True)
                report = {
                    "schema_version": "1.0",
                    "outputs": {
                        "evaluation_dir": "evaluation_full_zip",
                        "evaluation_report": "evaluation_full_zip/evaluation_report.json",
                    },
                    "metrics": [],
                }
                (evaluation_dir / "evaluation_report.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                return report

            inference_zip = _zip_bytes(
                {
                    "inferred/run_001/run_report.json": json.dumps(
                        {
                            "run_id": "run_001",
                            "status": "completed",
                            "dataset": {"id": "dataset_a"},
                            "outputs": {"merged_network_raw": "merged_network_raw.csv"},
                        }
                    ),
                    "inferred/run_001/merged_network_raw.csv": "source,target,score,sign,evidence,context,tool_id\n",
                    "inferred/run_001/provenance/raw/native.log": "large raw log\n",
                }
            )
            truth_zip = _zip_bytes(
                {
                    "benchmark-manifest.json": "{}",
                    "datasets/dataset_a/ground-truth-manifest.json": json.dumps(
                        {
                            "dataset_id": "dataset_a",
                            "outputs": {
                                "gene_universe": "truth/gene_universe.txt",
                                "networks": "truth/networks.csv",
                            },
                        }
                    ),
                    "datasets/dataset_a/expression.tsv": "gene\tc1\nG1\t1\n",
                    "datasets/dataset_a/truth/gene_universe.txt": "G1\nG2\n",
                    "datasets/dataset_a/truth/networks.csv": "source,target,score,sign,evidence,context\n",
                    "datasets/dataset_a/provenance/raw/native.tsv": "raw\n",
                }
            )

            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(gui_server, "STATE", gui_server.GuiState()),
                patch.object(
                    gui_server,
                    "start_background_thread",
                    start_immediate_background_thread,
                ),
                patch.object(
                    gui_server,
                    "evaluate_inference",
                    side_effect=fake_evaluate_inference,
                ),
            ):
                client = TestClient(gui_server.create_app())
                response = client.post(
                    "/api/evaluate-inference/run",
                    data={"output_dir": str(tmp_root / "evaluations")},
                    files={
                        "inference_zip": (
                            "inference_full.zip",
                            inference_zip,
                            "application/zip",
                        ),
                        "truth_zip": ("truth_full.zip", truth_zip, "application/zip"),
                    },
                )

            self.assertEqual(response.status_code, 200, msg=response.text)
            payload = response.json()
            self.assertEqual(payload["job"]["status"], "failed")
            self.assertEqual(payload["job"]["progress_percent"], 100)
            self.assertIn(
                "missing required root file run_report.json",
                payload["job"]["error"],
            )
            self.assertEqual(calls, [])

    def test_missing_analysis_files_return_specific_upload_error(self) -> None:
        inference_zip = _zip_bytes(
            {
                "run_report.json": json.dumps(
                    {
                        "run_id": "run_001",
                        "dataset": {"id": "dataset_a"},
                        "outputs": {"merged_network_raw": "merged_network_raw.csv"},
                    }
                )
            }
        )
        truth_zip = _zip_bytes(
            {
                "ground-truth-manifest.json": json.dumps(
                    {
                        "dataset_id": "dataset_a",
                        "outputs": {
                            "gene_universe": "truth/gene_universe.txt",
                            "networks": "truth/networks.csv",
                        },
                    }
                ),
                "truth/gene_universe.txt": "G1\nG2\n",
                "truth/networks.csv": "source,target,score,sign,evidence,context\n",
            }
        )

        with (
            patch.object(gui_server, "STATE", gui_server.GuiState()),
            patch.object(
                gui_server,
                "start_background_thread",
                start_immediate_background_thread,
            ),
        ):
            client = TestClient(gui_server.create_app())
            response = client.post(
                "/api/evaluate-inference/run",
                data={"output_dir": "./evaluations"},
                files={
                    "inference_zip": ("inference.zip", inference_zip, "application/zip"),
                    "truth_zip": ("truth.zip", truth_zip, "application/zip"),
                },
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["job"]["status"], "failed")
        self.assertIn(
            "missing required root file merged_network_raw.csv",
            payload["job"]["error"],
        )

    def test_static_gui_contains_reproducibility_section(self) -> None:
        index = (Path(gui_server.STATIC_DIR) / "index.html").read_text(encoding="utf-8")
        script = (Path(gui_server.STATIC_DIR) / "app" / "main.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("Reproduce This Evaluation", index)
        self.assertIn("reproducibility-grid", index)
        self.assertIn("repro-steps-modal", index)
        self.assertIn("renderReproducibility", script)
        self.assertIn("initReproducibility", script)


if __name__ == "__main__":
    unittest.main()
