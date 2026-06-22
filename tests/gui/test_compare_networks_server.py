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
    from andrea.gui.compare_networks import server as gui_server
    from andrea.core.commands.compare_networks.store import write_comparison_store
except Exception:  # noqa: BLE001
    gui_server = None
    write_comparison_store = None


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
            "comparison_sqlite": "comparison_fake/comparison.sqlite",
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
        "distances.csv",
        "distance_coordinates.csv",
    ]:
        (comparison_dir / name).write_text("", encoding="utf-8")
    (comparison_dir / "comparison_view.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    write_comparison_store(
        comparison_dir / "comparison.sqlite",
        network_index=[],
        edge_scores=[],
        distances=[],
        distance_coordinates=[],
        evaluation_metrics=[],
    )
    return report


@unittest.skipIf(
    TestClient is None or gui_server is None or write_comparison_store is None,
    "GUI test dependencies are not installed",
)
class CompareNetworksGuiServerTests(unittest.TestCase):
    def test_strict_analysis_upload_runs_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            calls: list[dict[str, Path]] = []

            def fake_compare_networks(**kwargs):  # noqa: ANN003
                calls.append(dict(kwargs))
                return _fake_compare_report(Path(kwargs["output_dir"]))

            inference_zip = _zip_bytes(
                {
                    "run_report.json": _run_report("run_001"),
                    "merged_network_normalized.csv": _normalized_csv(),
                }
            )
            evaluation_zip = _zip_bytes(
                {
                    "evaluation_report.json": json.dumps(
                        {
                            "schema_version": "1.0",
                            "inputs": {
                                "inference_run_id": "run_001",
                                "inference_dataset_id": "dataset_a",
                            },
                            "metrics": [
                                {
                                    "tool_id": "genie3_01",
                                    "metric": "aupr",
                                    "value": 0.5,
                                }
                            ],
                        }
                    )
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
                        ),
                        "source_0_evaluation_zip": (
                            "evaluation.zip",
                            evaluation_zip,
                            "application/zip",
                        ),
                    },
                )
                bundles_response = client.get(
                    f"/api/compare-networks/jobs/{response.json()['job']['job_id']}/bundles"
                )
                report_bundle_response = client.get(
                    f"/api/compare-networks/jobs/{response.json()['job']['job_id']}/bundle",
                    params={"bundle_id": "report"},
                )
                invalid_bundle_response = client.get(
                    f"/api/compare-networks/jobs/{response.json()['job']['job_id']}/bundle",
                    params={"bundle_id": "analysis"},
                )

            self.assertEqual(response.status_code, 200, msg=response.text)
            payload = response.json()
            self.assertEqual(payload["job"]["status"], "completed")
            self.assertEqual(payload["job"]["progress_percent"], 100)
            self.assertEqual(payload["job"]["artifact_status"]["edge_scores_csv"], "ready")
            self.assertEqual(payload["job"]["artifact_errors"], [])
            self.assertGreaterEqual(len(payload["job"]["timings"]), 3)
            self.assertEqual(payload["comparison_report"]["summary"]["sources"], 1)
            self.assertEqual(
                payload["comparison_report"]["artifact_status"]["edge_scores_csv"],
                "ready",
            )
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
            self.assertFalse(calls[0]["write_edge_scores_csv"])
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
            self.assertEqual(
                bundles_response.status_code, 200, msg=bundles_response.text
            )
            bundles_by_id = {
                item["id"]: item for item in bundles_response.json()["bundles"]
            }
            self.assertEqual(sorted(bundles_by_id), ["full", "report"])
            self.assertTrue(bundles_by_id["full"]["available"])
            self.assertTrue(bundles_by_id["report"]["available"])
            self.assertEqual(
                bundles_by_id["full"]["readiness"],
                [
                    {"label": "Explorer", "status": "ready"},
                    {"label": "edge_scores.csv", "status": "ready"},
                ],
            )
            self.assertEqual(
                bundles_by_id["report"]["readiness"],
                [
                    {"label": "Explorer", "status": "ready"},
                    {"label": "edge_scores.csv", "status": "not required"},
                ],
            )
            self.assertGreaterEqual(bundles_by_id["report"]["file_count"], 5)
            request_payload = json.loads(
                calls[0]["request_path"].read_text(encoding="utf-8")
            )
            self.assertEqual(request_payload["sources"][0]["label"], "Dataset A")
            self.assertTrue(
                request_payload["sources"][0]["run_report"].startswith(
                    "input/sources/source_1/"
                )
            )
            self.assertTrue(
                request_payload["sources"][0]["evaluation_report"].startswith(
                    "input/sources/source_1/"
                )
            )
            self.assertEqual(
                report_bundle_response.status_code,
                200,
                msg=report_bundle_response.text,
            )
            with zipfile.ZipFile(io.BytesIO(report_bundle_response.content)) as zf:
                names = set(zf.namelist())
            self.assertIn("comparison_report.json", names)
            self.assertIn("comparison-request.json", names)
            self.assertNotIn("input/sources/source_1/run_report.json", names)
            self.assertEqual(invalid_bundle_response.status_code, 400)

    def test_strict_analysis_upload_without_evaluation_runs_comparison(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            calls: list[dict[str, Path]] = []

            def fake_compare_networks(**kwargs):  # noqa: ANN003
                calls.append(dict(kwargs))
                return _fake_compare_report(Path(kwargs["output_dir"]))

            inference_zip = _zip_bytes(
                {
                    "run_report.json": _run_report("run_001"),
                    "merged_network_normalized.csv": _normalized_csv(),
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
            self.assertEqual(len(calls), 1)
            request_payload = json.loads(
                calls[0]["request_path"].read_text(encoding="utf-8")
            )
            self.assertNotIn("evaluation_report", request_payload["sources"][0])

    def test_deferred_edge_export_failure_keeps_report_bundle_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)

            def fake_compare_networks(**kwargs):  # noqa: ANN003
                return _fake_compare_report(Path(kwargs["output_dir"]))

            inference_zip = _zip_bytes(
                {
                    "run_report.json": _run_report("run_001"),
                    "merged_network_normalized.csv": _normalized_csv(),
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
                    "compare_networks",
                    side_effect=fake_compare_networks,
                ),
                patch.object(
                    gui_server,
                    "export_edge_scores_csv_from_sqlite",
                    side_effect=RuntimeError("export failed"),
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
                job_id = response.json()["job"]["job_id"]
                bundles_response = client.get(
                    f"/api/compare-networks/jobs/{job_id}/bundles"
                )

            self.assertEqual(response.status_code, 200, msg=response.text)
            payload = response.json()
            self.assertEqual(payload["job"]["status"], "completed")
            self.assertEqual(payload["job"]["artifact_status"]["edge_scores_csv"], "failed")
            self.assertIn("edge_scores.csv export failed", payload["job"]["artifact_errors"][0])
            self.assertEqual(
                payload["comparison_report"]["artifact_status"]["edge_scores_csv"],
                "failed",
            )
            bundles_by_id = {
                item["id"]: item for item in bundles_response.json()["bundles"]
            }
            self.assertTrue(bundles_by_id["report"]["available"])
            self.assertFalse(bundles_by_id["full"]["available"])
            self.assertIn("edge_scores.csv", bundles_by_id["full"]["missing_required"])
            self.assertEqual(
                bundles_by_id["full"]["readiness"][1],
                {"label": "edge_scores.csv", "status": "failed"},
            )

    def test_reproducibility_is_available_while_artifacts_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            comparison_dir = tmp_root / "comparisons" / "comparison_001"
            request_dir = tmp_root / "request"
            comparison_dir.mkdir(parents=True)
            request_dir.mkdir()
            frozen_request = comparison_dir / "comparison-request.json"
            frozen_request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "comparison_001",
                        "sources": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = gui_server.GuiState()
            state.jobs["job_finalizing"] = gui_server.GuiJob(
                job_id="job_finalizing",
                created_at="2026-05-22T00:00:00Z",
                status="finalizing_artifacts",
                stage="exporting_edge_scores_csv",
                request_dir=str(request_dir),
                output_dir=str(tmp_root / "comparisons"),
                comparison_request_path=str(frozen_request),
                frozen_comparison_request_path=str(frozen_request),
                comparison_dir=str(comparison_dir),
                artifact_status={"edge_scores_csv": "exporting"},
            )

            with patch.object(gui_server, "STATE", state):
                client = TestClient(gui_server.create_app())
                response = client.get("/api/compare-networks/jobs/job_finalizing")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertTrue(payload["reproducibility"]["available"])
        self.assertIn(
            "andrea compare-networks",
            payload["reproducibility"]["cli"]["primary_code"],
        )

    def test_completed_job_query_endpoints_use_sqlite_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            output_dir = tmp_root / "comparisons"
            comparison_dir = output_dir / "comparison_fake"
            comparison_dir.mkdir(parents=True)
            sqlite_path = comparison_dir / "comparison.sqlite"
            network_index = [
                {
                    "network_id": "a_global_topology",
                    "source_id": "source_1",
                    "run_id": "run",
                    "tool_id": "tool_a",
                    "catalog_tool_id": "tool_a",
                    "context": "global",
                    "level": "topology",
                    "n_genes": "3",
                    "n_edges": "2",
                },
                {
                    "network_id": "b_global_topology",
                    "source_id": "source_1",
                    "run_id": "run",
                    "tool_id": "tool_b",
                    "catalog_tool_id": "tool_b",
                    "context": "global",
                    "level": "topology",
                    "n_genes": "3",
                    "n_edges": "2",
                },
                {
                    "network_id": "a_group_topology",
                    "source_id": "source_1",
                    "run_id": "run",
                    "tool_id": "tool_a",
                    "catalog_tool_id": "tool_a",
                    "context": "group:sA",
                    "level": "topology",
                    "n_genes": "3",
                    "n_edges": "1",
                },
                {
                    "network_id": "b_group_topology",
                    "source_id": "source_1",
                    "run_id": "run",
                    "tool_id": "tool_b",
                    "catalog_tool_id": "tool_b",
                    "context": "group:sA",
                    "level": "topology",
                    "n_genes": "3",
                    "n_edges": "1",
                },
            ]
            edge_scores = [
                {
                    "network_id": "a_global_topology",
                    "source_id": "source_1",
                    "run_id": "run",
                    "tool_id": "tool_a",
                    "catalog_tool_id": "tool_a",
                    "context": "global",
                    "level": "topology",
                    "edge_key": "G1|G2",
                    "source": "G1",
                    "target": "G2",
                    "sign": "",
                    "score": "0.9",
                },
                {
                    "network_id": "b_global_topology",
                    "source_id": "source_1",
                    "run_id": "run",
                    "tool_id": "tool_b",
                    "catalog_tool_id": "tool_b",
                    "context": "global",
                    "level": "topology",
                    "edge_key": "G1|G2",
                    "source": "G1",
                    "target": "G2",
                    "sign": "",
                    "score": "0.2",
                },
            ]
            distances = [
                {
                    "source_id": "source_1",
                    "context": "group:sA",
                    "level": "topology",
                    "distance_metric": "weighted_jaccard_distance",
                    "network_a": "a_group_topology",
                    "network_b": "b_group_topology",
                    "distance": "0.4",
                    "n_common_genes": "3",
                    "n_edges_considered": "2",
                    "status": "ok",
                    "warning": "",
                }
            ]
            write_comparison_store(
                sqlite_path,
                network_index=network_index,
                edge_scores=edge_scores,
                distances=distances,
                distance_coordinates=[],
                evaluation_metrics=[],
            )
            report = {
                "schema_version": "1.0",
                "outputs": {
                    "comparison_sqlite": "comparison_fake/comparison.sqlite",
                },
            }
            report_path = comparison_dir / "comparison_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            state = gui_server.GuiState()
            state.jobs["job_1"] = gui_server.GuiJob(
                job_id="job_1",
                created_at="now",
                status="completed",
                stage="completed",
                request_dir=str(tmp_root),
                output_dir=str(output_dir),
                comparison_report_path=str(report_path),
                comparison_dir=str(comparison_dir),
            )
            with patch.object(gui_server, "STATE", state):
                client = TestClient(gui_server.create_app())
                contexts = client.get(
                    "/api/compare-networks/jobs/job_1/contexts",
                    params={"source_id": "source_1", "family": "group"},
                )
                distance_view_response = client.get(
                    "/api/compare-networks/jobs/job_1/distance-view",
                    params={
                        "source_id": "source_1",
                        "context_family": "group",
                        "distance_metric": "weighted_jaccard_distance",
                    },
                )
                selected_view_response = client.get(
                    "/api/compare-networks/jobs/job_1/distance-view",
                    params={
                        "source_id": "source_1",
                        "context_family": "group",
                        "distance_metric": "weighted_jaccard_distance",
                        "contexts": "group:sA",
                    },
                )
                invalid_metric_response = client.get(
                    "/api/compare-networks/jobs/job_1/distance-view",
                    params={
                        "source_id": "source_1",
                        "context_family": "group",
                        "distance_metric": "not_a_metric",
                    },
                )
                invalid_evaluation_metric_response = client.get(
                    "/api/compare-networks/jobs/job_1/distance-view",
                    params={
                        "source_id": "source_1",
                        "context_family": "group",
                        "distance_metric": "weighted_jaccard_distance",
                        "evaluation_metric": "not_a_metric",
                    },
                )
                variability = client.post(
                    "/api/compare-networks/jobs/job_1/edge-variability",
                    json={
                        "limit": 100,
                        "selected_networks": [
                            {
                                "source_id": "source_1",
                                "tool_id": "tool_a",
                                "context": "global",
                            },
                            {
                                "source_id": "source_1",
                                "tool_id": "tool_b",
                                "context": "global",
                            },
                        ],
                    },
                )

        self.assertEqual(contexts.status_code, 200, msg=contexts.text)
        self.assertEqual(contexts.json()["contexts"][0]["context"], "group:sA")
        self.assertEqual(
            distance_view_response.status_code,
            200,
            msg=distance_view_response.text,
        )
        distance_payload = distance_view_response.json()
        self.assertEqual(distance_payload["context_family"], "group")
        topology_view = next(item for item in distance_payload["levels"] if item["level"] == "topology")
        self.assertIsNone(topology_view["selected"])
        self.assertEqual(topology_view["aggregate"]["distances"][0]["median"], "0.4")
        self.assertEqual(topology_view["aggregate"]["ellipses"], [])
        self.assertEqual(
            selected_view_response.status_code,
            200,
            msg=selected_view_response.text,
        )
        context_payload = selected_view_response.json()
        context_topology = next(item for item in context_payload["levels"] if item["level"] == "topology")
        self.assertEqual(context_payload["selected_contexts"], ["group:sA"])
        self.assertEqual(context_topology["selected"]["contexts"], ["group:sA"])
        self.assertEqual(context_topology["selected"]["distances"][0]["median"], "0.4")
        self.assertEqual(invalid_metric_response.status_code, 400)
        self.assertEqual(invalid_evaluation_metric_response.status_code, 400)
        self.assertEqual(variability.status_code, 200, msg=variability.text)
        topology = next(item for item in variability.json()["levels"] if item["level"] == "topology")
        self.assertEqual(topology["status"], "ok")
        self.assertGreaterEqual(topology["comparable_edges"], 1)

    def test_nested_full_zip_layout_upload_is_rejected_server_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            calls: list[dict[str, Path]] = []

            def fake_compare_networks(**kwargs):  # noqa: ANN003
                calls.append(dict(kwargs))
                return _fake_compare_report(Path(kwargs["output_dir"]))

            inference_zip = _zip_bytes(
                {
                    "inferred/run_001/run_report.json": _run_report("run_001"),
                    "inferred/run_001/merged_network_normalized.csv": _normalized_csv(),
                    "inferred/run_001/runtime/execution_state.json": "{}",
                    "inferred/run_001/tools/genie3_01/work/native.tsv": "raw\n",
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
                            "inference_full.zip",
                            inference_zip,
                            "application/zip",
                        )
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

    def test_nested_multiple_run_reports_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            calls: list[dict[str, Path]] = []

            def fake_compare_networks(**kwargs):  # noqa: ANN003
                calls.append(dict(kwargs))
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
                patch.object(
                    gui_server,
                    "start_background_thread",
                    start_immediate_background_thread,
                ),
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
                self.assertEqual(payload["job"]["status"], "failed")
                self.assertIn(
                    "missing required root file run_report.json",
                    payload["job"]["error"],
                )

            self.assertEqual(calls, [])

    def test_static_gui_contains_tabbed_source_and_ordered_tool_controls(self) -> None:
        index = (Path(gui_server.STATIC_DIR) / "index.html").read_text(encoding="utf-8")
        script = (Path(gui_server.STATIC_DIR) / "app" / "main.js").read_text(
            encoding="utf-8"
        )
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

        self.assertIn("infer-network analysis bundle", index)
        self.assertIn("evaluate-inference analysis bundle", index)
        self.assertIn("Full archives and nested run folders are rejected", index)
        self.assertIn("run_report.json", index)
        self.assertIn("merged_network_normalized.csv", index)
        self.assertIn("evaluation_report.json", index)
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
        self.assertIn("Infer-network analysis ZIP", script)
        self.assertIn("Evaluate-inference analysis ZIP", script)
        self.assertIn("openBundleDownloadModal", script)
        self.assertIn("/bundles", script)
        self.assertIn("bundle_id=", script)
        self.assertIn("uploadFormDataWithProgress", script)
        self.assertIn(
            "XMLHttpRequest",
            (Path(gui_server.COMMON_STATIC_DIR) / "app" / "uploads" / "progress.js").read_text(
                encoding="utf-8"
            ),
        )
        self.assertIn("upload-progress-row overall", index)
        self.assertNotIn("readAs", script)
        legacy_bundle_name = "light " + "bundle"
        legacy_bundle_adjective = "light" + "weight"
        self.assertNotIn(legacy_bundle_name, index.lower())
        self.assertNotIn(legacy_bundle_adjective, index.lower())
        self.assertNotIn(legacy_bundle_adjective, script.lower())
        self.assertIn("distance-tab", index)
        self.assertIn("edges-tab", index)
        self.assertNotIn("distance-source-cards", index)
        self.assertIn("edge-source-cards", index)
        self.assertIn("Reproduce This Comparison", index)
        self.assertIn("reproducibility-grid", index)
        self.assertIn("repro-steps-modal", index)
        self.assertNotIn("source-map-radio", script)
        self.assertIn("edgeBuilderSelection", script)
        self.assertIn("edge-builder-add", script)
        self.assertIn("selectedNetworks", script)
        self.assertIn("distanceFamilies", script)
        self.assertIn("renderDistanceHeatmap", script)
        self.assertIn("renderDistanceMap", script)
        self.assertIn("renderDistanceMethodSummary", script)
        self.assertIn("distance-cell-meta", script)
        self.assertIn("source-add-row", index)
        self.assertIn("renderReproducibility", script)
        self.assertIn("initReproducibility", script)
        self.assertIn("selection-index", script)
        self.assertIn("distance-view", script)
        self.assertNotIn("distance-summary", script)
        self.assertIn("distanceSelectedContexts", script)
        self.assertIn("maxDistanceSelectedContexts", script)
        self.assertIn("Evaluation metric", script)
        self.assertIn("precision-chip", script)
        self.assertIn("evaluation_metric=", script)
        self.assertIn("edge-variability", script)
        self.assertIn("renderEdgeSparklineMatrix", script)
        self.assertIn("edgeSelectedEdges", script)
        self.assertIn("edgeVisualColumns", script)
        self.assertIn("edgeChartRange", script)
        self.assertIn("edgeMetricChip", script)
        self.assertIn("svgEdgeMetricChip", script)
        self.assertIn("edge-svg-metric-chip", style)
        self.assertNotIn("renderEdgeDifferenceTable", script)
        self.assertNotIn("contextTagsHtml", script)
        self.assertNotIn("context-tags", style)
        self.assertNotIn("source-card-grid.compact", style)
        self.assertNotIn("source-map-card", style)
        self.assertNotIn("section-actions", style)
        self.assertNotIn("edge-chart-guide", script)
        self.assertNotIn("Tool selections must use one context.", script)
        self.assertIn("Comparisons use only genes common to all selected networks.", index)

    def test_shared_component_keeps_static_edge_differences_lightweight(self) -> None:
        view_script = (
            Path(gui_server.COMPARISON_VIEW_ASSETS_DIR) / "view.js"
        ).read_text(encoding="utf-8")
        view_style = (
            Path(gui_server.COMPARISON_VIEW_ASSETS_DIR) / "view.css"
        ).read_text(encoding="utf-8")

        self.assertIn("Network Comparison Static Report", view_script)
        self.assertIn("Interactive exploration lives in the local GUI", view_script)
        self.assertIn("comparison.sqlite", view_script)
        self.assertIn("edge_scores.csv", view_script)
        self.assertIn("SQL inspection, scripts and GUI exploration", view_script)
        self.assertIn("CLI and Python users can use the CSV files", view_script)
        self.assertNotIn("renderDistanceMaps", view_script)
        self.assertNotIn("renderEdgeDifferenceView", view_script)
        self.assertNotIn("renderEdgeDifferences", view_script)
        self.assertNotIn("weightedJaccardForMaps", view_script)
        self.assertNotIn("rankOverlapForMaps", view_script)
        self.assertNotIn("context-filter", view_script)
        self.assertNotIn("maxContextOptions", view_script)
        self.assertNotIn("edge-diff", view_style)
        self.assertIn("artifact-card", view_style)
        self.assertIn("scrollbar-gutter", view_style)

    def test_missing_analysis_files_return_specific_upload_error(self) -> None:
        inference_zip = _zip_bytes(
            {
                "run_report.json": json.dumps(
                    {
                        "run_id": "run_001",
                        "outputs": {
                            "merged_network_normalized": "merged_network_normalized.csv"
                        },
                    }
                )
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
                "/api/compare-networks/run",
                data={
                    "source_count": "1",
                    "source_0_label": "Dataset A",
                    "output_dir": "./comparisons",
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
        self.assertEqual(payload["job"]["status"], "failed")
        self.assertIn(
            "missing required root file merged_network_normalized.csv",
            payload["job"]["error"],
        )


if __name__ == "__main__":
    unittest.main()
