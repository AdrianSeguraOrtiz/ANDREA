from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ._helpers import InferNetworkCoreTestCase


class InferNetworkPlanTests(InferNetworkCoreTestCase):
    def test_preflight_and_plan_generate_frozen_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )

            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                strict=False,
            )
            self.assertIn("catalog", preflight)
            self.assertIn("runs", preflight)
            self.assertEqual(preflight["runs"]["selected"], ["aracne__01"])
            self.assertEqual(preflight["inputs"]["dataset_id"], "toy_ds")
            self.assertEqual(preflight["inputs"]["tools_params"], "provided")
            self.assertNotIn("tools_root", preflight["inputs"])
            self.assertNotIn("schemas_dir", preflight["inputs"])

            run_dir = self.mod.plan_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                output_dir=output_dir,
                planner="heuristic",
                strict=False,
                preflight_report=preflight,
            )
            self.assertTrue((run_dir / "plan.json").exists())
            self.assertTrue((run_dir / "preflight_report.json").exists())
            self.assertTrue((run_dir / "run_report.json").exists())
            self.assertTrue((run_dir / "input" / "dataset-manifest.json").exists())
            self.assertTrue((run_dir / "input" / "tools_params.json").exists())
            self.assertTrue((run_dir / "input" / "expression.tsv").exists())
            self.assertTrue((run_dir / "input" / "extra" / "tf_list.txt").exists())
            self.assertTrue(
                (run_dir / "tools" / "aracne__01" / "resolved_params.json").exists()
            )

            plan_payload = json.loads(
                (run_dir / "plan.json").read_text(encoding="utf-8")
            )
            self.assertIn("input_fingerprints", plan_payload)
            self.assertTrue(plan_payload["input_fingerprints"])
            first_wave_task = plan_payload["waves"][0]["tasks"][0]
            self.assertEqual(first_wave_task["eta_source"], "cost_profile")
            self.assertIn("eta_provenance", first_wave_task)
            self.assertIn(
                "profile_id",
                first_wave_task["eta_provenance"]["cost_profile"],
            )
            first_physical_task = plan_payload["runs"][0]["physical_tasks"][0]
            self.assertEqual(first_physical_task["eta_source"], "cost_profile")
            self.assertIn("eta_provenance", first_physical_task)

            report_payload = json.loads(
                (run_dir / "run_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report_payload["status"], "planned")
            self.assertEqual(
                report_payload["inputs"]["dataset_manifest_path"],
                "input/dataset-manifest.json",
            )
            self.assertEqual(
                report_payload["inputs"]["tools_params_path"],
                "input/tools_params.json",
            )
            self.assertNotIn("tools_root", report_payload["inputs"])
            self.assertNotIn("schemas_dir", report_payload["inputs"])

    def test_plan_rejects_invalid_planner_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                strict=False,
            )

            with self.assertRaisesRegex(ValueError, "max_cores must be >= 1"):
                self.mod.plan_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                    output_dir=output_dir,
                    max_cores=0,
                    planner="heuristic",
                    strict=False,
                    preflight_report=preflight,
                )

            with self.assertRaisesRegex(ValueError, "planner must be one of"):
                self.mod.plan_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                    output_dir=output_dir,
                    planner="unknown_planner",
                    strict=False,
                    preflight_report=preflight,
                )
