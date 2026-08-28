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

DATASET_FINGERPRINT = {"algorithm": "sha256", "value": "a" * 64}


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _strict_run_report(
    *,
    run_id: str = "run_001",
    tool_id: str = "genie3_01",
    catalog_tool_id: str = "genie3",
    tool_origin: str = "catalog",
    sign: str = "none",
    rows: int = 1,
) -> dict[str, object]:
    inputs = {
        "dataset_manifest_path": "input/dataset-manifest.json",
        "tools_params_path": "input/tools_params.json",
    }
    if tool_origin == "custom":
        inputs["custom_tools_path"] = "input/custom_tools.json"
    return {
        "run_id": run_id,
        "status": "executed",
        "inputs": inputs,
        "dataset": {
            "id": "dataset_a",
            "fingerprint": DATASET_FINGERPRINT,
            "column_kind": "cells",
            "expression_profile": "single_cell",
            "genes": 2,
            "columns": 2,
            "expression_matrix_path": "input/expression.tsv",
        },
        "tools": {
            "selected": [tool_id],
            "catalog_tool_ids": {tool_id: catalog_tool_id},
            "tool_origins": {tool_id: tool_origin},
            "output_capabilities": {
                tool_id: {
                    "tool_origin": tool_origin,
                    "catalog_tool_id": catalog_tool_id,
                    "directed": True,
                    "sign": sign,
                }
            },
            "skipped": {},
            "status_by_tool": {tool_id: "completed"},
            "completed": [tool_id],
            "completed_contexts": {tool_id: ["global"]},
            "failed": {},
            "results": {tool_id: {"execution": {"mode": "global"}}},
        },
        "outputs": {
            "merged_network_raw": "merged_network_raw.csv",
            "merged_network_raw_gexf": "merged_network_raw.gexf",
            "merged_network_raw_graphml": "merged_network_raw.graphml",
            "merged_network_normalized": "merged_network_normalized.csv",
            "merged_network_normalized_gexf": "merged_network_normalized.gexf",
            "merged_network_normalized_graphml": "merged_network_normalized.graphml",
            "merged_network_normalized_cytoscape_script": (
                "merged_network_normalized_cytoscape.py"
            ),
            "rows_per_tool": {tool_id: rows},
        },
        "issues": [],
        "execution": {
            "elapsed_seconds": 1.0,
            "planner_requested": "heuristic",
            "planner_used": "heuristic",
            "planner_time_limit_seconds": 100.0,
            "waves_total": 1,
            "tools_selected": 1,
            "physical_tasks_total": 1,
            "tools_completed": 1,
            "tools_failed": 0,
        },
        "plan_file": "plan.json",
        "notes": [
            "Run directory is frozen at planning time.",
            "Use run_infer_network_plan(run_dir=...) to execute this plan.",
        ],
    }


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
        self.assertIn("frozen output capabilities for every run", index)
        self.assertNotIn("custom_tools.json", index)
        self.assertIn("truth/networks.csv", index)
        self.assertIn("candidate source/target files", index)
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
        self.assertIn(
            "XMLHttpRequest",
            (
                Path(gui_server.COMMON_STATIC_DIR) / "app" / "uploads" / "progress.js"
            ).read_text(encoding="utf-8"),
        )
        self.assertNotIn("readAs", script)

    def test_strict_analysis_uploads_run_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            calls: list[dict[str, Path]] = []

            def fake_evaluate_inference(**kwargs):
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
                        _strict_run_report(
                            tool_id="spathi_01",
                            catalog_tool_id="custom_spathi_01",
                            tool_origin="custom",
                        )
                    ),
                    "merged_network_raw.csv": (
                        "source,target,score,sign,evidence,context,tool_id\n"
                        "G1,G2,1,?,inferred,global,spathi_01\n"
                    ),
                }
            )
            truth_zip = _zip_bytes(
                {
                    "ground-truth-manifest.json": json.dumps(
                        {
                            "schema_version": "1.0",
                            "dataset_id": "dataset_a",
                            "dataset_fingerprint": DATASET_FINGERPRINT,
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
                            "candidate_space": {
                                "sources": "extras/tf_list.txt",
                                "targets": "truth/gene_universe.txt",
                                "allow_self_edges": False,
                            },
                        }
                    ),
                    "truth/gene_universe.txt": "G1\nG2\n",
                    "truth/networks.csv": "source,target,score,sign,evidence,context\n",
                    "extras/tf_list.txt": "G1\n",
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
            self.assertEqual(
                payload["evaluation_report"]["metrics"][0]["tool_id"], "genie3"
            )
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
            frozen_run_report_path = Path(payload["job"]["frozen_run_report_path"])
            frozen_run_report = json.loads(
                frozen_run_report_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                frozen_run_report["inputs"]["custom_tools_path"],
                "input/custom_tools.json",
            )
            self.assertEqual(
                frozen_run_report["tools"]["output_capabilities"]["spathi_01"],
                {
                    "tool_origin": "custom",
                    "catalog_tool_id": "custom_spathi_01",
                    "directed": True,
                    "sign": "none",
                },
            )
            self.assertFalse(
                (frozen_run_report_path.parent / "custom_tools.json").exists()
            )
            frozen_truth_manifest_path = Path(
                payload["job"]["frozen_ground_truth_manifest_path"]
            )
            frozen_truth_manifest = json.loads(
                frozen_truth_manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                frozen_truth_manifest["candidate_space"]["sources"],
                "candidate_space/sources.txt",
            )
            self.assertEqual(
                frozen_truth_manifest["candidate_space"]["targets"],
                "truth/gene_universe.txt",
            )
            self.assertFalse(
                frozen_truth_manifest["candidate_space"]["allow_self_edges"]
            )
            self.assertEqual(
                (
                    frozen_truth_manifest_path.parent
                    / frozen_truth_manifest["candidate_space"]["sources"]
                ).read_text(encoding="utf-8"),
                "G1\n",
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

    def test_strict_bundle_preparation_rejects_noncanonical_output_references(
        self,
    ) -> None:
        frozen_tools = {
            "selected": ["genie3__01"],
            "catalog_tool_ids": {"genie3__01": "genie3"},
            "tool_origins": {"genie3__01": "catalog"},
            "output_capabilities": {
                "genie3__01": {
                    "tool_origin": "catalog",
                    "catalog_tool_id": "genie3",
                    "directed": True,
                    "sign": "none",
                }
            },
        }
        cases = [
            (
                "inference",
                {"merged_network_raw": "nested/merged_network_raw.csv"},
                "outputs.merged_network_raw must be exactly 'merged_network_raw.csv'",
            ),
            (
                "truth_networks",
                {
                    "networks": "./truth/networks.csv",
                    "gene_universe": "truth/gene_universe.txt",
                },
                "outputs.networks must be exactly 'truth/networks.csv'",
            ),
            (
                "truth_gene_universe",
                {
                    "networks": "truth/networks.csv",
                    "gene_universe": "truth/../truth/gene_universe.txt",
                },
                "outputs.gene_universe must be exactly 'truth/gene_universe.txt'",
            ),
        ]

        for bundle_kind, outputs, expected_error in cases:
            with (
                self.subTest(bundle_kind=bundle_kind),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                if bundle_kind == "inference":
                    report = {
                        "run_id": "run_001",
                        "inputs": {
                            "custom_tools_path": "input/custom_tools.json",
                        },
                        "tools": frozen_tools,
                        "outputs": outputs,
                    }
                    report_text = json.dumps(report)
                    (root / "run_report.json").write_text(
                        report_text,
                        encoding="utf-8",
                    )
                    (root / "merged_network_raw.csv").write_text(
                        "source,target,score,sign,evidence,context,tool_id\n",
                        encoding="utf-8",
                    )
                    prepare = gui_server._prepare_strict_inference_bundle
                    manifest_path = root / "run_report.json"
                else:
                    manifest = {
                        "dataset_id": "dataset_a",
                        "dataset_fingerprint": DATASET_FINGERPRINT,
                        "outputs": outputs,
                        "candidate_space": {
                            "sources": "extras/tf_list.txt",
                            "targets": "truth/gene_universe.txt",
                            "allow_self_edges": False,
                        },
                    }
                    manifest_text = json.dumps(manifest)
                    manifest_path = root / "ground-truth-manifest.json"
                    manifest_path.write_text(manifest_text, encoding="utf-8")
                    (root / "truth").mkdir()
                    (root / "truth" / "networks.csv").write_text(
                        "source,target,score,sign,evidence,context\n",
                        encoding="utf-8",
                    )
                    (root / "truth" / "gene_universe.txt").write_text(
                        "G1\nG2\n",
                        encoding="utf-8",
                    )
                    (root / "extras").mkdir()
                    (root / "extras" / "tf_list.txt").write_text(
                        "G1\n",
                        encoding="utf-8",
                    )
                    prepare = gui_server._prepare_strict_truth_bundle

                original_text = manifest_path.read_text(encoding="utf-8")
                with self.assertRaisesRegex(ValueError, expected_error):
                    prepare(root)
                self.assertEqual(
                    manifest_path.read_text(encoding="utf-8"),
                    original_text,
                )

    def test_strict_inference_bundle_requires_terminal_report_and_exact_counts(
        self,
    ) -> None:
        base_report = _strict_run_report()
        cases = {
            "non-terminal status": (
                lambda report: report.__setitem__("status", "completed"),
                "status must be exactly 'executed'",
            ),
            "wrong row count": (
                lambda report: report["outputs"]["rows_per_tool"].__setitem__(
                    "genie3_01", 2
                ),
                "row counts must exactly match",
            ),
        }

        for case, (mutate, expected_error) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report = json.loads(json.dumps(base_report))
                mutate(report)
                (root / "run_report.json").write_text(
                    json.dumps(report), encoding="utf-8"
                )
                (root / "merged_network_raw.csv").write_text(
                    "source,target,score,sign,evidence,context,tool_id\n"
                    "G1,G2,1,?,inferred,global,genie3_01\n",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(ValueError, expected_error):
                    gui_server._prepare_strict_inference_bundle(root)

    def test_strict_truth_bundle_requires_complete_manifest_schema(self) -> None:
        base_manifest = {
            "schema_version": "1.0",
            "dataset_id": "dataset_a",
            "dataset_fingerprint": DATASET_FINGERPRINT,
            "simulator_id": "dyngen",
            "data_axes": {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "trajectory",
            },
            "truth_requirements": {"contexts": ["global"]},
            "outputs": {
                "networks": "truth/networks.csv",
                "gene_universe": "truth/gene_universe.txt",
            },
            "candidate_space": {
                "sources": "extras/tf_list.txt",
                "targets": "truth/gene_universe.txt",
                "allow_self_edges": False,
            },
        }
        cases = {
            "missing required field": (
                lambda manifest: manifest.pop("data_axes"),
                "data_axes",
            ),
            "unexpected legacy field": (
                lambda manifest: manifest.__setitem__(
                    "legacy_candidate_fallback", True
                ),
                "legacy_candidate_fallback",
            ),
        }

        for case, (mutate, expected_error) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manifest = json.loads(json.dumps(base_manifest))
                mutate(manifest)
                manifest_path = root / "ground-truth-manifest.json"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                (root / "truth").mkdir()
                (root / "truth" / "networks.csv").write_text(
                    "source,target,score,sign,evidence,context\n",
                    encoding="utf-8",
                )
                (root / "truth" / "gene_universe.txt").write_text(
                    "G1\nG2\n",
                    encoding="utf-8",
                )
                (root / "extras").mkdir()
                (root / "extras" / "tf_list.txt").write_text(
                    "G1\n",
                    encoding="utf-8",
                )
                original_text = manifest_path.read_text(encoding="utf-8")

                with self.assertRaisesRegex(
                    ValueError,
                    rf"failed schema validation.*{expected_error}",
                ):
                    gui_server._prepare_strict_truth_bundle(root)

                self.assertEqual(
                    manifest_path.read_text(encoding="utf-8"),
                    original_text,
                )

    def test_upload_rejects_missing_frozen_capabilities_even_with_custom_snapshot(
        self,
    ) -> None:
        inference_zip = _zip_bytes(
            {
                "run_report.json": json.dumps(
                    {
                        "run_id": "run_001",
                        "inputs": {
                            "custom_tools_path": "input/custom_tools.json",
                        },
                        "tools": {
                            "selected": ["external_01"],
                            "catalog_tool_ids": {
                                "external_01": "custom_external_01",
                            },
                            "tool_origins": {"external_01": "custom"},
                        },
                        "outputs": {
                            "merged_network_raw": "merged_network_raw.csv",
                        },
                    }
                ),
                "merged_network_raw.csv": (
                    "source,target,score,sign,evidence,context,tool_id\n"
                ),
                "input/custom_tools.json": json.dumps(
                    {
                        "tools": [
                            {
                                "run_id": "external_01",
                                "outputs": {"directed": True, "sign": "signed"},
                            }
                        ]
                    }
                ),
            }
        )
        truth_zip = _zip_bytes(
            {
                "ground-truth-manifest.json": json.dumps(
                    {
                        "dataset_id": "dataset_a",
                        "dataset_fingerprint": DATASET_FINGERPRINT,
                        "outputs": {
                            "gene_universe": "truth/gene_universe.txt",
                            "networks": "truth/networks.csv",
                        },
                        "candidate_space": {
                            "sources": "extras/tf_list.txt",
                            "targets": "truth/gene_universe.txt",
                            "allow_self_edges": False,
                        },
                    }
                ),
                "truth/gene_universe.txt": "G1\nG2\n",
                "truth/networks.csv": ("source,target,score,sign,evidence,context\n"),
                "extras/tf_list.txt": "G1\n",
            }
        )

        with (
            patch.object(gui_server, "STATE", gui_server.GuiState()),
            patch.object(
                gui_server,
                "start_background_thread",
                start_immediate_background_thread,
            ),
            patch.object(gui_server, "evaluate_inference") as evaluate_mock,
        ):
            client = TestClient(gui_server.create_app())
            response = client.post(
                "/api/evaluate-inference/run",
                data={"output_dir": "./evaluations"},
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
        self.assertEqual(payload["job"]["status"], "failed")
        self.assertIn(
            "tools.output_capabilities must be an object", payload["job"]["error"]
        )
        evaluate_mock.assert_not_called()

    def test_upload_rejects_missing_or_self_edge_candidate_space_early(self) -> None:
        inference_zip = _zip_bytes(
            {
                "run_report.json": json.dumps(
                    _strict_run_report(tool_id="genie3__01")
                ),
                "merged_network_raw.csv": (
                    "source,target,score,sign,evidence,context,tool_id\n"
                    "G1,G2,1,?,inferred,global,genie3__01\n"
                ),
            }
        )
        cases = [
            (None, "candidate_space must contain exactly"),
            (
                {
                    "sources": "extras/tf_list.txt",
                    "targets": "truth/gene_universe.txt",
                    "allow_self_edges": True,
                },
                "candidate_space.allow_self_edges must be false",
            ),
        ]
        for candidate_space, expected in cases:
            with self.subTest(expected=expected):
                manifest = {
                    "dataset_id": "dataset_a",
                    "dataset_fingerprint": DATASET_FINGERPRINT,
                    "outputs": {
                        "gene_universe": "truth/gene_universe.txt",
                        "networks": "truth/networks.csv",
                    },
                }
                if candidate_space is not None:
                    manifest["candidate_space"] = candidate_space
                truth_zip = _zip_bytes(
                    {
                        "ground-truth-manifest.json": json.dumps(manifest),
                        "truth/gene_universe.txt": "G1\nG2\n",
                        "truth/networks.csv": (
                            "source,target,score,sign,evidence,context\n"
                        ),
                        "extras/tf_list.txt": "G1\n",
                    }
                )

                with (
                    patch.object(gui_server, "STATE", gui_server.GuiState()),
                    patch.object(
                        gui_server,
                        "start_background_thread",
                        start_immediate_background_thread,
                    ),
                    patch.object(gui_server, "evaluate_inference") as evaluate_mock,
                ):
                    client = TestClient(gui_server.create_app())
                    response = client.post(
                        "/api/evaluate-inference/run",
                        data={"output_dir": "./evaluations"},
                        files={
                            "inference_zip": (
                                "inference.zip",
                                inference_zip,
                                "application/zip",
                            ),
                            "truth_zip": (
                                "truth.zip",
                                truth_zip,
                                "application/zip",
                            ),
                        },
                    )

                self.assertEqual(response.status_code, 200, msg=response.text)
                payload = response.json()
                self.assertEqual(payload["job"]["status"], "failed")
                self.assertIn(expected, payload["job"]["error"])
                evaluate_mock.assert_not_called()

    def test_nested_full_zip_layout_upload_is_rejected_server_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            calls: list[dict[str, Path]] = []

            def fake_evaluate_inference(**kwargs):
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
                            "dataset_fingerprint": DATASET_FINGERPRINT,
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
                        "dataset_fingerprint": DATASET_FINGERPRINT,
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
