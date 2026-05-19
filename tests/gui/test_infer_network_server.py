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
    from andrea.gui.infer_network import server as gui_server
except Exception:  # noqa: BLE001
    gui_server = None

from andrea.core.commands.infer_network.commons.execution_state import (
    build_initial_execution_state,
    execution_state_path,
    write_execution_state,
)
from andrea.core.commands.infer_network.commons.shared import PlanWave, ToolPlanItem


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


@unittest.skipIf(
    TestClient is None or gui_server is None,
    "GUI test dependencies are not installed",
)
class InferNetworkGuiServerTests(unittest.TestCase):
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

            def fake_preflight(*, dataset_manifest_path, tools_params_path):  # noqa: ANN001
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
                patch.object(gui_server.threading, "Thread", _ImmediateThread),
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
                    f"/api/infer-network/jobs/{job_id}/files?mode=light"
                )
                self.assertEqual(
                    files_response.status_code, 200, msg=files_response.text
                )
                entries = files_response.json()["entries"]
                self.assertTrue(
                    any(item["path"] == "run/plan.json" for item in entries)
                )

                file_content = client.get(
                    f"/api/infer-network/jobs/{job_id}/file-content",
                    params={"mode": "light", "path": "run/run_report.json"},
                )
                self.assertEqual(file_content.status_code, 200, msg=file_content.text)
                self.assertEqual(file_content.json()["viewer"], "json")

                file_missing = client.get(
                    f"/api/infer-network/jobs/{job_id}/file-content",
                    params={"mode": "light", "path": "run/missing_file.txt"},
                )
                self.assertEqual(file_missing.status_code, 404)

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

            def fake_preflight(*, dataset_manifest_path, tools_params_path):  # noqa: ANN001
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
                patch.object(gui_server.threading, "Thread", _ImmediateThread),
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
