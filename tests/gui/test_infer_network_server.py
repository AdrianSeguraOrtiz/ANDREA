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
    from andrea.gui.infer_network import server as gui_server
except Exception:  # noqa: BLE001
    gui_server = None

from andrea.core.commands.infer_network.commons.execution_state import (
    build_initial_execution_state,
    execution_state_path,
    write_execution_state,
)
from andrea.core.commands.infer_network.commons.shared import PlanWave, ToolPlanItem


@unittest.skipIf(
    TestClient is None or gui_server is None,
    "GUI test dependencies are not installed",
)
class InferNetworkGuiServerTests(unittest.TestCase):
    def test_static_gui_uses_bundle_download_modal(self) -> None:
        index = (Path(gui_server.STATIC_DIR) / "index.html").read_text(encoding="utf-8")
        style = (Path(gui_server.STATIC_DIR) / "styles.css").read_text(encoding="utf-8")
        repro_style = (
            Path(gui_server.COMMON_STATIC_DIR) / "app" / "repro" / "styles.css"
        ).read_text(encoding="utf-8")
        params_style = (
            Path(gui_server.COMMON_STATIC_DIR) / "app" / "params" / "styles.css"
        ).read_text(encoding="utf-8")
        toast_style = (
            Path(gui_server.COMMON_STATIC_DIR) / "app" / "ui" / "toasts.css"
        ).read_text(encoding="utf-8")
        popover_style = (
            Path(gui_server.COMMON_STATIC_DIR) / "app" / "ui" / "popovers.css"
        ).read_text(encoding="utf-8")
        script = (Path(gui_server.STATIC_DIR) / "app" / "main.js").read_text(
            encoding="utf-8"
        )
        jobs_controller = (
            Path(gui_server.STATIC_DIR) / "app" / "jobs" / "controller.js"
        ).read_text(encoding="utf-8")
        run_cards = (
            Path(gui_server.STATIC_DIR) / "app" / "runs" / "cards.js"
        ).read_text(encoding="utf-8")

        self.assertIn("bundle-modal", index)
        self.assertIn("Explorer view: available output files", index)
        self.assertIn("external-tool-modal", index)
        self.assertIn("open-external-tool-modal-btn", index)
        self.assertIn("/static-common/app/params/styles.css", index)
        self.assertIn("/static-common/app/repro/styles.css", index)
        self.assertIn("/static-common/app/ui/popovers.css", index)
        self.assertIn("/static-common/app/ui/toasts.css", index)
        self.assertIn(".repro-card {", repro_style)
        self.assertIn(".param-field {", params_style)
        self.assertIn(".toast {", toast_style)
        self.assertIn(".info-popover {", popover_style)
        self.assertIn("openBundleDownloadModal", script)
        self.assertIn("/bundles", script)
        self.assertIn("bundle_id=", script)
        self.assertIn("available_outputs", jobs_controller)
        self.assertIn("customToolsPayload", script)
        self.assertIn("custom_tools", script)
        self.assertIn("renderPlanFailure", jobs_controller)
        self.assertIn("dataset.expression.genes", run_cards)
        self.assertIn("dataset.expression.columns", run_cards)
        self.assertIn(".tool-item-custom-badge", style)
        self.assertIn(".external-tool-callout", style)
        self.assertIn("Request New Tool", index)
        self.assertIn("Docker Image Name", index)
        self.assertIn("Docker Image Tag", index)
        self.assertIn("Run ID", index)
        self.assertIn("custom-tool-run-id", index)
        self.assertIn("custom-tool-image-help-btn", index)
        self.assertIn("custom-tool-needed-extras", index)
        self.assertIn("custom-tool-extra-options", index)
        self.assertIn("custom-tool-param-rows", index)
        self.assertIn("custom-tool-add-param-row", index)
        self.assertIn("Extra inputs needed by this image", index)
        self.assertIn("External Docker image contract", script)
        self.assertIn("--output-dir /io/out", script)
        self.assertIn("progress.json", script)
        self.assertIn("/io/expression.tsv", script)
        self.assertIn("/io/out/network.csv", script)
        self.assertIn("Validate image", index)
        self.assertIn("/api/infer-network/docker-image/check", script)

    def test_docker_image_check_endpoint_returns_helper_result(self) -> None:
        with patch.object(
            gui_server,
            "_check_docker_image_access",
            return_value={
                "available": True,
                "source": "local",
                "message": "Image is available locally.",
            },
        ) as check_mock:
            client = TestClient(gui_server.create_app())
            response = client.post(
                "/api/infer-network/docker-image/check",
                json={"image": "example/tool:1.0"},
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["source"], "local")
        check_mock.assert_called_once_with("example/tool:1.0")

    def _planned_wave(self) -> PlanWave:
        return PlanWave(
            index=1,
            threads_used=1,
            ram_gb_used=1.0,
            eta_seconds=1.0,
            tasks=[
                ToolPlanItem(
                    tool_id="tool_01",
                    run_id="run_01",
                    image="dummy/image:latest",
                    threads=1,
                    ram_gb=1.0,
                    eta_seconds=1.0,
                    eta_source="test",
                    output_dir="tools/tool_01",
                )
            ],
        )

    def _write_execution_state(
        self,
        run_dir: Path,
        *,
        status: str,
        phase: str,
        percent: int,
        message: str,
        tool_status: str,
    ) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = build_initial_execution_state(
            run_id=run_dir.name,
            waves=[self._planned_wave()],
            logical_runs={
                "run_01": {
                    "run_id": "run_01",
                    "tool_id": "dummy",
                    "execution": {"mode": "global"},
                    "physical_tasks": [{"task_id": "tool_01"}],
                }
            },
        )
        payload.update(
            {
                "status": status,
                "phase": phase,
                "percent": percent,
                "message": message,
                "current_wave": None,
            }
        )
        payload["summary"] = {
            "total": 1,
            "queued": 0,
            "running": 1 if tool_status == "running" else 0,
            "completed": 1 if tool_status == "completed" else 0,
            "failed": 1 if tool_status == "failed" else 0,
            "warnings": 0,
        }
        payload["waves"][0].update(
            {
                "status": (
                    "running"
                    if tool_status == "running"
                    else "failed"
                    if tool_status == "failed"
                    else "completed"
                ),
                "percent": percent if tool_status == "running" else 100,
            }
        )
        payload["tools"]["tool_01"].update(
            {
                "status": tool_status,
                "phase": phase,
                "percent": percent if tool_status == "running" else 100,
                "message": message,
                "errors": [message] if tool_status == "failed" else [],
            }
        )
        payload["logical_runs"]["run_01"].update(
            {
                "status": tool_status,
                "phase": phase,
                "percent": percent if tool_status == "running" else 100,
                "message": message,
                "errors": [message] if tool_status == "failed" else [],
            }
        )
        payload["phase_history"].append(
            {
                "status": status,
                "phase": phase,
                "percent": percent,
                "message": message,
                "current_wave": None,
                "updated_at": payload["updated_at"],
            }
        )
        write_execution_state(execution_state_path(run_dir), payload)

    def _register_job(
        self,
        *,
        job_id: str,
        run_dir: Path,
        status: str,
        stage: str,
        run_report_path: str | None = None,
        error: str | None = None,
    ) -> None:
        with gui_server.STATE.lock:
            gui_server.STATE.jobs.clear()
            gui_server.STATE.jobs[job_id] = gui_server.GuiJob(
                job_id=job_id,
                created_at="2026-05-19T00:00:00Z",
                status=status,
                request_dir=str(run_dir / "request"),
                output_dir=str(run_dir.parent),
                stage=stage,
                run_dir=str(run_dir),
                run_report_path=run_report_path,
                error=error,
            )

    def _write_merged_csvs(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        content = (
            "source,target,score,sign,evidence,context,tool_id\n"
            "G1,G2,0.8,+,test,global,run_01\n"
        )
        (run_dir / "merged_network_raw.csv").write_text(content, encoding="utf-8")
        (run_dir / "merged_network_normalized.csv").write_text(
            content, encoding="utf-8"
        )

    def test_preflight_plan_run_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            expression_content = "gene\tS1\tS2\nG1\t1\t2\nG2\t3\t4\n"
            run_dir = tmp_root / "planned_run"
            run_dir.mkdir(parents=True, exist_ok=True)

            def fake_preflight(  # noqa: ANN001
                *,
                dataset_manifest_path,
                tools_params_path,
                custom_tools_path=None,
            ):
                return {
                    "schema_version": "1.0",
                    "catalog": {
                        "eligible": [
                            {
                                "tool_id": "aracne3",
                                "status": "eligible",
                                "issues": [],
                            }
                        ],
                        "warning": [],
                        "blocked": [],
                    },
                    "runs": {
                        "requested_total": 0,
                        "selected": [],
                        "catalog_tool_ids": {},
                        "resolved_params": {},
                        "skipped": {},
                    },
                    "issues": [],
                    "inputs": {
                        "dataset_manifest_path": str(dataset_manifest_path),
                        "tools_params_path": (
                            str(tools_params_path) if tools_params_path else None
                        ),
                    },
                    "dataset": {},
                }

            def fake_plan(**kwargs):  # noqa: ANN003
                plan_payload = {
                    "run_id": "gui_run_001",
                    "waves": [
                        {
                            "index": 1,
                            "threads_used": 1,
                            "ram_gb_used": 1.0,
                            "eta_seconds": 1.0,
                            "tasks": [
                                {
                                    "tool_id": "aracne_run_01",
                                    "image": "dummy/image:latest",
                                    "threads": 1,
                                    "ram_gb": 1.0,
                                    "eta_seconds": 1.0,
                                    "eta_source": "test",
                                    "output_dir": "tools/aracne_run_01",
                                }
                            ],
                        }
                    ],
                    "eta_total_seconds": 1.0,
                    "input_fingerprints": {
                        "input/dataset-manifest.json": {"size_bytes": 10, "sha256": "x"}
                    },
                }
                (run_dir / "plan.json").write_text(
                    json.dumps(plan_payload, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                (run_dir / "run_report.json").write_text(
                    json.dumps(
                        {
                            "run_id": "gui_run_001",
                            "status": "planned",
                            "execution": {
                                "planner_used": "heuristic",
                                "waves_total": 1,
                                "tools_selected": 1,
                                "tools_completed": 0,
                                "tools_failed": 0,
                                "elapsed_seconds": 0.0,
                            },
                            "issues": [],
                            "outputs": {
                                "merged_network_raw": None,
                                "merged_network_normalized": None,
                                "rows_per_tool": {},
                            },
                        },
                        indent=2,
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return run_dir

            def fake_run(*, run_dir, progress_poll_seconds):  # noqa: ANN001
                report_path = Path(run_dir) / "run_report.json"
                payload = json.loads(report_path.read_text(encoding="utf-8"))
                payload["status"] = "executed"
                payload["execution"]["tools_completed"] = 1
                payload["execution"]["elapsed_seconds"] = 0.7
                report_path.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                return Path(run_dir)

            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(
                    gui_server,
                    "start_background_thread",
                    start_immediate_background_thread,
                ),
                patch.object(
                    gui_server,
                    "preflight_infer_network",
                    side_effect=fake_preflight,
                ),
                patch.object(gui_server, "plan_infer_network", side_effect=fake_plan),
                patch.object(
                    gui_server, "run_infer_network_plan", side_effect=fake_run
                ),
            ):
                client = TestClient(gui_server.create_app())

                preflight_response = client.post(
                    "/api/infer-network/preflight",
                    data={
                        "config": json.dumps(
                            {
                                "dataset": {
                                    "id": "gui_dataset",
                                    "column_kind": "samples",
                                    "expression_profile": "mixed",
                                    "organism": {
                                        "taxonomic_group": "animal",
                                        "ncbi_taxon_id": 9606,
                                    },
                                },
                                "options": {"output_dir": str(tmp_root / "out")},
                            }
                        )
                    },
                    files={
                        "expression_file": (
                            "expression.tsv",
                            expression_content,
                            "text/tab-separated-values",
                        ),
                    },
                )
                self.assertEqual(
                    preflight_response.status_code, 200, msg=preflight_response.text
                )
                job_id = preflight_response.json()["job_id"]

                job_payload = client.get(f"/api/infer-network/jobs/{job_id}").json()
                self.assertEqual(job_payload["job"]["status"], "completed")
                self.assertEqual(job_payload["job"]["stage"], "preflight_ok")

                plan_response = client.post(
                    "/api/infer-network/plan",
                    json={
                        "job_id": job_id,
                        "runs": [
                            {
                                "run_id": "aracne_run_01",
                                "tool_id": "aracne3",
                                "params": {},
                            }
                        ],
                        "options": {"planner": "heuristic"},
                    },
                )
                self.assertEqual(plan_response.status_code, 200, msg=plan_response.text)

                job_payload = client.get(f"/api/infer-network/jobs/{job_id}").json()
                self.assertEqual(job_payload["job"]["stage"], "planned")
                self.assertTrue(job_payload["job"]["plan_path"])

                plan_payload = client.get(
                    f"/api/infer-network/jobs/{job_id}/plan"
                ).json()
                self.assertIn("plan", plan_payload)
                self.assertIsNotNone(plan_payload["plan"])

                run_response = client.post(
                    "/api/infer-network/run",
                    json={"job_id": job_id, "options": {"progress_poll_seconds": 0.1}},
                )
                self.assertEqual(run_response.status_code, 200, msg=run_response.text)

                job_payload = client.get(f"/api/infer-network/jobs/{job_id}").json()
                self.assertEqual(job_payload["job"]["stage"], "executed")
                self.assertEqual(job_payload["run_report"]["status"], "executed")
                self.assertEqual(
                    job_payload["run_report"]["execution"]["tools_completed"], 1
                )

                files_response = client.get(
                    f"/api/infer-network/jobs/{job_id}/files?bundle_id=report"
                )
                self.assertEqual(
                    files_response.status_code, 200, msg=files_response.text
                )
                entries = files_response.json()["entries"]
                self.assertTrue(any(item["path"] == "plan.json" for item in entries))

                file_content = client.get(
                    f"/api/infer-network/jobs/{job_id}/file-content",
                    params={"bundle_id": "report", "path": "run_report.json"},
                )
                self.assertEqual(file_content.status_code, 200, msg=file_content.text)
                self.assertEqual(file_content.json()["viewer"], "json")

                file_missing = client.get(
                    f"/api/infer-network/jobs/{job_id}/file-content",
                    params={"bundle_id": "report", "path": "missing_file.txt"},
                )
                self.assertEqual(file_missing.status_code, 404)

                bundles_response = client.get(
                    f"/api/infer-network/jobs/{job_id}/bundles"
                )
                self.assertEqual(
                    bundles_response.status_code, 200, msg=bundles_response.text
                )
                bundles_payload = bundles_response.json()
                self.assertTrue(bundles_payload["output_ready"])
                bundles_by_id = {
                    item["id"]: item for item in bundles_payload["bundles"]
                }
                self.assertEqual(
                    sorted(bundles_by_id),
                    ["analysis", "full", "graphs", "report"],
                )
                self.assertTrue(bundles_by_id["report"]["available"])
                self.assertFalse(bundles_by_id["analysis"]["available"])
                self.assertEqual(
                    sorted(bundles_by_id["analysis"]["intended_downstream_commands"]),
                    ["compare-networks", "evaluate-inference"],
                )
                self.assertIn(
                    "merged_network_raw.csv",
                    bundles_by_id["analysis"]["missing_required"],
                )
                report_bundle = client.get(
                    f"/api/infer-network/jobs/{job_id}/bundle",
                    params={"bundle_id": "report"},
                )
                self.assertEqual(report_bundle.status_code, 200, msg=report_bundle.text)
                with zipfile.ZipFile(io.BytesIO(report_bundle.content)) as zf:
                    self.assertIn("run_report.json", zf.namelist())
                    self.assertIn("plan.json", zf.namelist())

                invalid_bundle = client.get(
                    f"/api/infer-network/jobs/{job_id}/bundle",
                    params={"bundle_id": "not_a_bundle"},
                )
                self.assertEqual(invalid_bundle.status_code, 400)

    def test_preflight_accepts_external_tool_payload_from_gui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            expression_content = "gene\tS1\tS2\nG1\t1\t2\nG2\t3\t4\n"
            seen_custom_tools: dict[str, object] = {}

            def fake_preflight(  # noqa: ANN001
                *,
                dataset_manifest_path,
                tools_params_path,
                custom_tools_path=None,
            ):
                self.assertIsNotNone(custom_tools_path)
                payload = json.loads(Path(custom_tools_path).read_text(encoding="utf-8"))
                seen_custom_tools.update(payload)
                return {
                    "schema_version": "1.0",
                    "catalog": {
                        "eligible": [],
                        "warning": [
                            {
                                "tool_id": "custom_demo_tool_01",
                                "tool_origin": "custom",
                                "status": "warning",
                                "issues": [],
                            }
                        ],
                        "blocked": [],
                    },
                    "runs": {
                        "requested_total": 0,
                        "selected": [],
                        "catalog_tool_ids": {},
                        "tool_origins": {},
                        "resolved_params": {},
                        "skipped": {},
                    },
                    "issues": [],
                    "inputs": {
                        "dataset_manifest_path": str(dataset_manifest_path),
                        "tools_params_path": (
                            str(tools_params_path) if tools_params_path else None
                        ),
                        "custom_tools": "provided",
                    },
                    "dataset": {},
                }

            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(
                    gui_server,
                    "start_background_thread",
                    start_immediate_background_thread,
                ),
                patch.object(
                    gui_server,
                    "preflight_infer_network",
                    side_effect=fake_preflight,
                ),
            ):
                client = TestClient(gui_server.create_app())
                response = client.post(
                    "/api/infer-network/preflight",
                    data={
                        "config": json.dumps(
                            {
                                "dataset": {
                                    "id": "gui_dataset",
                                    "column_kind": "samples",
                                    "expression_profile": "mixed",
                                    "organism": {
                                        "taxonomic_group": "animal",
                                        "ncbi_taxon_id": 9606,
                                    },
                                },
                                "options": {"output_dir": str(tmp_root / "out")},
                            }
                        ),
                        "custom_tools": json.dumps(
                            {
                                "tools": [
                                    {
                                        "run_id": "demo_tool_01",
                                        "name": "Demo Tool",
                                        "docker_image": "example/demo:1.0",
                                        "execution_mode": "global",
                                        "extra_inputs": ["tf_list"],
                                    }
                                ]
                            }
                        ),
                    },
                    files={
                        "expression_file": (
                            "expression.tsv",
                            expression_content,
                            "text/tab-separated-values",
                        ),
                    },
                )

                self.assertEqual(response.status_code, 200, msg=response.text)
                job_id = response.json()["job_id"]
                job_payload = client.get(f"/api/infer-network/jobs/{job_id}").json()

        self.assertEqual(seen_custom_tools["tools"][0]["run_id"], "demo_tool_01")
        self.assertEqual(seen_custom_tools["tools"][0]["extra_inputs"], ["tf_list"])
        self.assertTrue(job_payload["job"]["custom_tools_path"])

    def test_job_payload_includes_running_execution_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self._write_execution_state(
                run_dir,
                status="running",
                phase="merging_raw_networks",
                percent=82,
                message="Writing merged_network_raw.csv.",
                tool_status="running",
            )
            self._register_job(
                job_id="job_running_state",
                run_dir=run_dir,
                status="running",
                stage="planned",
            )

            client = TestClient(gui_server.create_app())
            payload = client.get(
                "/api/infer-network/jobs/job_running_state"
            ).json()

        self.assertEqual(payload["execution_state"]["phase"], "merging_raw_networks")
        self.assertEqual(payload["runtime_progress"]["summary"]["running"], 1)
        self.assertEqual(payload["runtime_progress"]["tools"][0]["run_id"], "run_01")
        self.assertEqual(
            payload["runtime_progress"]["tools"][0]["message"],
            "Writing merged_network_raw.csv.",
        )

    def test_bundle_metadata_blocks_report_dependent_bundles_during_finalization(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self._write_merged_csvs(run_dir)
            (run_dir / "run_report.json").write_text(
                json.dumps(
                    {
                        "run_id": "run",
                        "status": "planned",
                        "tools": {
                            "catalog_tool_ids": {"run_01": "dummy"},
                        },
                        "outputs": {
                            "merged_network_raw": None,
                            "merged_network_normalized": None,
                        },
                    },
                    indent=2,
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            for rel in (
                "merged_network_raw.gexf",
                "merged_network_raw.graphml",
                "merged_network_normalized.gexf",
                "merged_network_normalized.graphml",
                "merged_network_normalized_cytoscape.py",
            ):
                (run_dir / rel).write_text("graph\n", encoding="utf-8")
            logs_dir = run_dir / "tools" / "dummy_tool"
            logs_dir.mkdir(parents=True, exist_ok=True)
            (logs_dir / "stderr.log").write_text("log\n", encoding="utf-8")
            self._register_job(
                job_id="job_finalizing_bundles",
                run_dir=run_dir,
                status="running",
                stage="executing",
            )

            client = TestClient(gui_server.create_app())
            payload = client.get(
                "/api/infer-network/jobs/job_finalizing_bundles/bundles"
            ).json()
            full_bundle = next(item for item in payload["bundles"] if item["id"] == "full")
            analysis_bundle = next(
                item for item in payload["bundles"] if item["id"] == "analysis"
            )
            report_bundle = next(
                item for item in payload["bundles"] if item["id"] == "report"
            )
            graphs_bundle = next(
                item for item in payload["bundles"] if item["id"] == "graphs"
            )
            analysis_download = client.get(
                "/api/infer-network/jobs/job_finalizing_bundles/bundle",
                params={"bundle_id": "analysis"},
            )
            explorer_files = client.get(
                "/api/infer-network/jobs/job_finalizing_bundles/files",
                params={"bundle_id": "available_outputs"},
            )

        self.assertTrue(payload["output_ready"])
        self.assertTrue(payload["output_readiness"]["finalizing_artifacts"])
        self.assertTrue(payload["output_readiness"]["run_report_file_ready"])
        self.assertFalse(full_bundle["available"])
        self.assertTrue(analysis_bundle["available"])
        self.assertFalse(report_bundle["available"])
        self.assertTrue(graphs_bundle["available"])
        self.assertIn(
            "run_report.json final report is not complete",
            full_bundle["missing_required"],
        )
        self.assertIn(
            "run_report.json final report is not complete",
            report_bundle["missing_required"],
        )
        self.assertEqual(analysis_download.status_code, 200)
        self.assertEqual(explorer_files.status_code, 200, msg=explorer_files.text)
        explorer_payload = explorer_files.json()
        self.assertEqual(explorer_payload["bundle_id"], "available_outputs")
        explorer_paths = {item["path"] for item in explorer_payload["entries"]}
        self.assertIn("merged_network_raw.csv", explorer_paths)
        self.assertIn("tools", explorer_paths)
        self.assertIn("tools/dummy_tool/stderr.log", explorer_paths)
        self.assertEqual(
            full_bundle["readiness"],
            [
                {"label": "Merged CSVs", "status": "ready"},
                {"label": "Run report", "status": "pending"},
                {"label": "Graph exports", "status": "ready"},
            ],
        )
        self.assertEqual(
            analysis_bundle["readiness"],
            [
                {"label": "Merged CSVs", "status": "ready"},
                {"label": "Run report snapshot", "status": "ready"},
            ],
        )

    def test_file_preview_adds_artifact_guide_without_changing_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self._write_merged_csvs(run_dir)
            self._register_job(
                job_id="job_file_guide",
                run_dir=run_dir,
                status="running",
                stage="executing",
            )

            client = TestClient(gui_server.create_app())
            response = client.get(
                "/api/infer-network/jobs/job_file_guide/file-content",
                params={
                    "bundle_id": "available_outputs",
                    "path": "merged_network_normalized.csv",
                },
            )
            csv_text = (run_dir / "merged_network_normalized.csv").read_text(
                encoding="utf-8"
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["viewer"], "table_csv")
        self.assertEqual(payload["guide"]["title"], "Normalized merged network")
        self.assertEqual(payload["headers"][0], "source")
        self.assertTrue(csv_text.startswith("source,target,score"))

    def test_job_payload_keeps_final_report_with_execution_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            report_path = run_dir / "run_report.json"
            run_dir.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run",
                        "status": "executed",
                        "execution": {"tools_completed": 1, "tools_failed": 0},
                    },
                    indent=2,
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_execution_state(
                run_dir,
                status="completed",
                phase="completed",
                percent=100,
                message="Execution completed.",
                tool_status="completed",
            )
            self._register_job(
                job_id="job_final_state",
                run_dir=run_dir,
                status="completed",
                stage="executed",
                run_report_path=str(report_path),
            )

            client = TestClient(gui_server.create_app())
            payload = client.get("/api/infer-network/jobs/job_final_state").json()

        self.assertEqual(payload["execution_state"]["status"], "completed")
        self.assertEqual(payload["run_report"]["status"], "executed")
        self.assertEqual(payload["runtime_progress"]["summary"]["completed"], 1)

    def test_failed_job_payload_can_use_state_without_final_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self._write_execution_state(
                run_dir,
                status="failed",
                phase="failed",
                percent=100,
                message="Execution halted.",
                tool_status="failed",
            )
            self._register_job(
                job_id="job_failed_state",
                run_dir=run_dir,
                status="failed",
                stage="planned",
                error="Execution halted.",
            )

            client = TestClient(gui_server.create_app())
            payload = client.get("/api/infer-network/jobs/job_failed_state").json()

        self.assertIsNone(payload["run_report"])
        self.assertEqual(payload["execution_state"]["status"], "failed")
        self.assertEqual(payload["runtime_progress"]["summary"]["failed"], 1)
        self.assertEqual(payload["runtime_progress"]["tools"][0]["status"], "failed")
        self.assertFalse(payload["output_readiness"]["explorer_available"])

    def test_output_readiness_exposes_csvs_during_artifact_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self._write_merged_csvs(run_dir)
            report_path = run_dir / "run_report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run",
                        "status": "planned",
                        "execution": {"tools_completed": 1, "tools_failed": 0},
                    },
                    indent=2,
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_execution_state(
                run_dir,
                status="running",
                phase="exporting_artifacts",
                percent=93,
                message="Exporting merged_network_normalized.gexf.",
                tool_status="completed",
            )
            self._register_job(
                job_id="job_finalizing_outputs",
                run_dir=run_dir,
                status="running",
                stage="planned",
                run_report_path=str(report_path),
            )

            client = TestClient(gui_server.create_app())
            payload = client.get(
                "/api/infer-network/jobs/job_finalizing_outputs"
            ).json()

        readiness = payload["output_readiness"]
        self.assertTrue(readiness["explorer_available"])
        self.assertTrue(readiness["csv_ready"])
        self.assertFalse(readiness["final_report_ready"])
        self.assertFalse(readiness["graph_exports_ready"])
        self.assertTrue(readiness["finalizing_artifacts"])
        self.assertIn("Merged CSV outputs are available", readiness["message"])
        self.assertIsNotNone(readiness["paths"]["merged_network_raw"])
        self.assertIsNotNone(readiness["paths"]["merged_network_normalized"])

    def test_output_readiness_marks_partial_final_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self._write_merged_csvs(run_dir)
            report_path = run_dir / "run_report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run",
                        "status": "executed",
                        "execution": {"tools_completed": 1, "tools_failed": 1},
                        "tools": {"failed": {"run_02": "test failure"}},
                    },
                    indent=2,
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_execution_state(
                run_dir,
                status="completed_with_failures",
                phase="completed_with_failures",
                percent=100,
                message="Execution completed with failures.",
                tool_status="failed",
            )
            self._register_job(
                job_id="job_partial_outputs",
                run_dir=run_dir,
                status="completed",
                stage="executed",
                run_report_path=str(report_path),
            )

            client = TestClient(gui_server.create_app())
            payload = client.get("/api/infer-network/jobs/job_partial_outputs").json()

        readiness = payload["output_readiness"]
        self.assertTrue(readiness["explorer_available"])
        self.assertTrue(readiness["csv_ready"])
        self.assertTrue(readiness["final_report_ready"])
        self.assertTrue(readiness["partial"])
        self.assertEqual(readiness["failed_runs"], 1)

    def test_run_endpoint_requires_planned_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            expression_content = "gene\tS1\tS2\nG1\t1\t2\nG2\t3\t4\n"

            def fake_preflight(  # noqa: ANN001
                *,
                dataset_manifest_path,
                tools_params_path,
                custom_tools_path=None,
            ):
                return {
                    "schema_version": "1.0",
                    "catalog": {"eligible": [], "warning": [], "blocked": []},
                    "runs": {
                        "requested_total": 0,
                        "selected": [],
                        "catalog_tool_ids": {},
                        "resolved_params": {},
                        "skipped": {},
                    },
                    "issues": [],
                    "inputs": {
                        "dataset_manifest_path": str(dataset_manifest_path),
                        "tools_params_path": (
                            str(tools_params_path) if tools_params_path else None
                        ),
                    },
                    "dataset": {},
                }

            with (
                patch.object(gui_server, "GUI_TMP_ROOT", tmp_root / "gui_tmp"),
                patch.object(
                    gui_server,
                    "start_background_thread",
                    start_immediate_background_thread,
                ),
                patch.object(
                    gui_server,
                    "preflight_infer_network",
                    side_effect=fake_preflight,
                ),
            ):
                client = TestClient(gui_server.create_app())

                preflight_response = client.post(
                    "/api/infer-network/preflight",
                    data={
                        "config": json.dumps(
                            {
                                "dataset": {
                                    "id": "gui_dataset",
                                    "column_kind": "samples",
                                    "expression_profile": "mixed",
                                    "organism": {
                                        "taxonomic_group": "animal",
                                        "ncbi_taxon_id": 9606,
                                    },
                                },
                                "options": {"output_dir": str(tmp_root / "out")},
                            }
                        )
                    },
                    files={
                        "expression_file": (
                            "expression.tsv",
                            expression_content,
                            "text/tab-separated-values",
                        ),
                    },
                )
                self.assertEqual(
                    preflight_response.status_code, 200, msg=preflight_response.text
                )
                job_id = preflight_response.json()["job_id"]

                run_response = client.post(
                    "/api/infer-network/run",
                    json={"job_id": job_id, "options": {"progress_poll_seconds": 0.1}},
                )
                self.assertEqual(run_response.status_code, 400)
                self.assertIn("No planned run found", run_response.text)


if __name__ == "__main__":
    unittest.main()
