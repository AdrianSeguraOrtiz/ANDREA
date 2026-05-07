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
    from andrea.gui.compare_networks import server as gui_server
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


def _run_report(run_id: str) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "completed",
            "dataset": {"id": "dataset_a"},
            "outputs": {"merged_network_normalized": "merged_network_normalized.csv"},
        }
    )


def _normalized_csv() -> str:
    return (
        "source,target,score,sign,evidence,context,tool_id\n"
        "G1,G2,0.5,+,test,global,genie3_01\n"
    )


def _fake_compare_report(output_dir: Path) -> dict[str, object]:
    comparison_dir = output_dir / "comparison_fake"
    comparison_dir.mkdir(parents=True)
    report = {
        "schema_version": "1.0",
        "outputs": {
            "comparison_dir": "comparison_fake",
            "comparison_report": "comparison_fake/comparison_report.json",
            "comparison_request": "comparison_fake/comparison-request.json",
            "network_index_csv": "comparison_fake/network_index.csv",
            "edge_scores_csv": "comparison_fake/edge_scores.csv",
            "distances_csv": "comparison_fake/distances.csv",
            "distance_coordinates_csv": "comparison_fake/distance_coordinates.csv",
            "comparison_view": "comparison_fake/comparison_view.html",
        },
        "summary": {
            "sources": 1,
            "network_instances": 3,
            "distance_rows": 0,
        },
        "sources": [],
        "network_index": [],
        "distances": [],
        "distance_coordinates": [],
    }
    (comparison_dir / "comparison_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    for name in [
        "comparison-request.json",
        "network_index.csv",
        "edge_scores.csv",
        "distances.csv",
        "distance_coordinates.csv",
    ]:
        (comparison_dir / name).write_text("", encoding="utf-8")
    (comparison_dir / "comparison_view.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    return report


@unittest.skipIf(
    TestClient is None or gui_server is None,
    "GUI test dependencies are not installed",
)
class CompareNetworksGuiServerTests(unittest.TestCase):
    def test_uploads_auto_detect_single_source_and_runs_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            calls: list[dict[str, Path]] = []

            def fake_compare_networks(**kwargs):  # noqa: ANN003
                calls.append(dict(kwargs))
                return _fake_compare_report(Path(kwargs["output_dir"]))

            inference_zip = _zip_bytes(
                {
                    "run/run_report.json": _run_report("run_001"),
                    "run/merged_network_normalized.csv": _normalized_csv(),
                }
            )

            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(gui_server, "STATE", gui_server.GuiState()),
                patch.object(gui_server.threading, "Thread", _ImmediateThread),
                patch.object(
                    gui_server,
                    "compare_networks",
                    side_effect=fake_compare_networks,
                ),
            ):
                client = TestClient(gui_server.create_app())
                response = client.post(
                    "/api/compare-networks/run",
                    data={
                        "source_count": "1",
                        "source_0_label": "Dataset A",
                        "output_dir": str(tmp_root / "comparisons"),
                    },
                    files={
                        "source_0_inference_zip": (
                            "inference.zip",
                            inference_zip,
                            "application/zip",
                        )
                    },
                )

            self.assertEqual(response.status_code, 200, msg=response.text)
            payload = response.json()
            self.assertEqual(payload["job"]["status"], "completed")
            self.assertEqual(payload["comparison_report"]["summary"]["sources"], 1)
            self.assertTrue(payload["reproducibility"]["available"])
            self.assertIn(
                "andrea compare-networks",
                payload["reproducibility"]["cli"]["primary_code"],
            )
            self.assertNotIn(
                "/gui_tmp/",
                payload["reproducibility"]["cli"]["primary_code"],
            )
            self.assertIn(
                "compare_networks(",
                payload["reproducibility"]["python"]["primary_code"],
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["output_dir"], tmp_root / "comparisons")
            self.assertTrue(
                Path(payload["job"]["frozen_comparison_request_path"]).is_relative_to(
                    tmp_root / "comparisons"
                )
            )
            self.assertEqual(
                calls[0]["request_path"],
                Path(payload["job"]["frozen_comparison_request_path"]),
            )
            self.assertEqual(
                calls[0]["comparison_dir"],
                Path(payload["job"]["frozen_comparison_request_path"]).parent,
            )
            request_payload = json.loads(
                calls[0]["request_path"].read_text(encoding="utf-8")
            )
            self.assertEqual(request_payload["sources"][0]["label"], "Dataset A")
            self.assertTrue(
                request_payload["sources"][0]["run_report"].startswith(
                    "input/sources/source_1/"
                )
            )
            self.assertNotIn("evaluation_report", request_payload["sources"][0])

    def test_multiple_run_report_candidates_require_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)

            def fake_compare_networks(**kwargs):  # noqa: ANN003
                return _fake_compare_report(Path(kwargs["output_dir"]))

            inference_zip = _zip_bytes(
                {
                    "run_a/run_report.json": _run_report("run_a"),
                    "run_a/merged_network_normalized.csv": _normalized_csv(),
                    "run_b/run_report.json": _run_report("run_b"),
                    "run_b/merged_network_normalized.csv": _normalized_csv(),
                }
            )

            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(gui_server, "STATE", gui_server.GuiState()),
                patch.object(gui_server.threading, "Thread", _ImmediateThread),
                patch.object(
                    gui_server,
                    "compare_networks",
                    side_effect=fake_compare_networks,
                ),
            ):
                client = TestClient(gui_server.create_app())
                response = client.post(
                    "/api/compare-networks/run",
                    data={
                        "source_count": "1",
                        "source_0_label": "Dataset A",
                        "output_dir": str(tmp_root / "comparisons"),
                    },
                    files={
                        "source_0_inference_zip": (
                            "inference.zip",
                            inference_zip,
                            "application/zip",
                        )
                    },
                )
                self.assertEqual(response.status_code, 200, msg=response.text)
                payload = response.json()
                self.assertEqual(payload["job"]["status"], "needs_selection")
                self.assertEqual(len(payload["job"]["sources"][0]["run_candidates"]), 2)

                selected = payload["job"]["sources"][0]["run_candidates"][1]["path"]
                run_response = client.post(
                    f"/api/compare-networks/jobs/{payload['job']['job_id']}/run",
                    json={
                        "sources": [
                            {
                                "source_id": "source_1",
                                "run_report": selected,
                                "evaluation_report": None,
                            }
                        ]
                    },
                )

            self.assertEqual(run_response.status_code, 200, msg=run_response.text)
            self.assertEqual(run_response.json()["job"]["status"], "completed")
            self.assertTrue(run_response.json()["reproducibility"]["available"])
            self.assertNotIn(
                "/gui_tmp/",
                run_response.json()["reproducibility"]["cli"]["primary_code"],
            )

    def test_static_gui_contains_tabbed_source_and_ordered_tool_controls(self) -> None:
        index = (Path(gui_server.STATIC_DIR) / "index.html").read_text(encoding="utf-8")
        script = (Path(gui_server.STATIC_DIR) / "app" / "main.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("distance-tab", index)
        self.assertIn("edges-tab", index)
        self.assertIn("distance-source-cards", index)
        self.assertIn("edge-source-cards", index)
        self.assertIn("Reproduce This Comparison", index)
        self.assertIn("reproducibility-grid", index)
        self.assertIn("repro-steps-modal", index)
        self.assertIn("source-map-radio", script)
        self.assertIn("selectedNetworks", script)
        self.assertIn("renderReproducibility", script)
        self.assertIn("initReproducibility", script)
        self.assertIn("selection-index", script)
        self.assertIn("renderDistanceMaps", script)
        self.assertIn("renderEdgeDifferenceView", script)
        self.assertIn("updateSelectedNetworks", script)
        self.assertNotIn("Tool selections must use one context.", script)
        self.assertIn("Comparisons use only genes common to all selected networks.", index)

    def test_shared_component_contains_edge_difference_logic(self) -> None:
        view_script = (
            Path(gui_server.COMPARISON_VIEW_ASSETS_DIR) / "view.js"
        ).read_text(encoding="utf-8")
        view_style = (
            Path(gui_server.COMPARISON_VIEW_ASSETS_DIR) / "view.css"
        ).read_text(encoding="utf-8")

        self.assertIn("Ordered Edge Differences", view_script)
        self.assertIn("renderDistanceMaps", view_script)
        self.assertIn("renderEdgeDifferenceView", view_script)
        self.assertIn("renderEdgeDifferences", view_script)
        self.assertIn("using only common genes", view_script)
        self.assertIn("weightedJaccardForMaps", view_script)
        self.assertIn("rankOverlapForMaps", view_script)
        self.assertIn("sign-change", view_script)
        self.assertIn("edge-diff", view_style)


if __name__ == "__main__":
    unittest.main()
