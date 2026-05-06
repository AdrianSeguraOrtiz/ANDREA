from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # noqa: BLE001
    TestClient = None

try:
    from andrea.gui.evaluate_inference import server as gui_server
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
    def test_uploads_auto_detect_single_pair_and_runs_evaluation(self) -> None:
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
                    "run/run_report.json": json.dumps(
                        {
                            "run_id": "run_001",
                            "status": "completed",
                            "dataset": {"id": "dataset_a"},
                            "outputs": {"merged_network_raw": "merged_network_raw.csv"},
                        }
                    ),
                    "run/merged_network_raw.csv": "source,target,score,sign,evidence,context,tool_id\n",
                }
            )
            truth_zip = _zip_bytes(
                {
                    "benchmark/datasets/dataset_a/ground-truth-manifest.json": json.dumps(
                        {
                            "schema_version": "1.0",
                            "dataset_id": "dataset_a",
                            "simulator_id": "dyngen",
                            "profile": "scrna_grouped",
                            "outputs": {"global_network": "truth/global_network.csv"},
                        }
                    ),
                    "benchmark/datasets/dataset_a/truth/global_network.csv": "source,target,score,sign\n",
                }
            )

            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(gui_server, "STATE", gui_server.GuiState()),
                patch.object(gui_server.threading, "Thread", _ImmediateThread),
                patch.object(
                    gui_server,
                    "evaluate_inference",
                    side_effect=fake_evaluate_inference,
                ),
            ):
                client = TestClient(gui_server.create_app())
                response = client.post(
                    "/api/evaluate-inference/run",
                    files={
                        "inference_zip": (
                            "inference.zip",
                            inference_zip,
                            "application/zip",
                        ),
                        "truth_zip": ("truth.zip", truth_zip, "application/zip"),
                    },
                )

            self.assertEqual(response.status_code, 200, msg=response.text)
            payload = response.json()
            self.assertEqual(payload["job"]["status"], "completed")
            self.assertEqual(payload["evaluation_report"]["metrics"][0]["tool_id"], "genie3")
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0]["run_report_path"].exists())
            self.assertTrue(calls[0]["ground_truth_manifest_path"].exists())
            self.assertTrue(calls[0]["generate_view"])

    def test_multiple_candidates_require_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)

            def fake_evaluate_inference(**kwargs):  # noqa: ANN003
                output_dir = Path(kwargs["output_dir"])
                evaluation_dir = output_dir / "evaluation_selected"
                evaluation_dir.mkdir(parents=True)
                report = {
                    "schema_version": "1.0",
                    "outputs": {
                        "evaluation_dir": "evaluation_selected",
                        "evaluation_report": "evaluation_selected/evaluation_report.json",
                    },
                    "metrics": [{"tool_id": "clr", "status": "ok"}],
                }
                (evaluation_dir / "evaluation_report.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                return report

            inference_zip = _zip_bytes(
                {
                    "run/run_report.json": json.dumps(
                        {
                            "run_id": "run_001",
                            "status": "completed",
                            "dataset": {"id": "dataset_a"},
                            "outputs": {"merged_network_raw": "merged_network_raw.csv"},
                        }
                    ),
                    "run/merged_network_raw.csv": "source,target,score,sign,evidence,context,tool_id\n",
                }
            )
            truth_zip = _zip_bytes(
                {
                    "datasets/a/ground-truth-manifest.json": json.dumps(
                        {
                            "dataset_id": "dataset_x",
                            "outputs": {"global_network": "truth/global_network.csv"},
                        }
                    ),
                    "datasets/a/truth/global_network.csv": "source,target\n",
                    "datasets/b/ground-truth-manifest.json": json.dumps(
                        {
                            "dataset_id": "dataset_y",
                            "outputs": {"global_network": "truth/global_network.csv"},
                        }
                    ),
                    "datasets/b/truth/global_network.csv": "source,target\n",
                }
            )

            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(gui_server, "STATE", gui_server.GuiState()),
                patch.object(gui_server.threading, "Thread", _ImmediateThread),
                patch.object(
                    gui_server,
                    "evaluate_inference",
                    side_effect=fake_evaluate_inference,
                ),
            ):
                client = TestClient(gui_server.create_app())
                response = client.post(
                    "/api/evaluate-inference/run",
                    files={
                        "inference_zip": (
                            "inference.zip",
                            inference_zip,
                            "application/zip",
                        ),
                        "truth_zip": ("truth.zip", truth_zip, "application/zip"),
                    },
                )
                self.assertEqual(response.status_code, 200, msg=response.text)
                payload = response.json()
                self.assertEqual(payload["job"]["status"], "needs_selection")
                self.assertEqual(len(payload["job"]["truth_candidates"]), 2)

                selected_truth = payload["job"]["truth_candidates"][1]["path"]
                selected_run = payload["job"]["run_candidates"][0]["path"]
                run_response = client.post(
                    f"/api/evaluate-inference/jobs/{payload['job']['job_id']}/run",
                    json={
                        "run_report": selected_run,
                        "ground_truth_manifest": selected_truth,
                    },
                )

            self.assertEqual(run_response.status_code, 200, msg=run_response.text)
            self.assertEqual(run_response.json()["job"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
