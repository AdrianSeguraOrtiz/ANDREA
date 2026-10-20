from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "wrappers/inference_tools/tools/dyngenie3/continue_integration.py"
spec = importlib.util.spec_from_file_location("dyngenie3_supervisor", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
IMAGE = "sha256:" + "a" * 64
OTHER_IMAGE = "sha256:" + "b" * 64


class DynGenie3SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "repo"
        self.tool = self.root / "wrappers/inference_tools/tools/dyngenie3"
        self.tool.mkdir(parents=True)
        for name in (
            "continue_integration.py",
            "verify_runtime.py",
            "run_tool.py",
            "patch_upstream.py",
            "Dockerfile",
            "requirements.lock",
        ):
            (self.tool / name).write_text("reviewed source\n")
        module.write_json(
            self.tool / "toolspec.draft.json",
            {"docker_image": "local/dyngenie3:reviewed"},
        )
        module.write_json(self.tool / "smoketest.config.draft.json", {"reviewed": True})
        self.map = {
            "bundle_templates": {"runtime": ["python/common.py"]},
            "tools": {
                "dyngenie3": {
                    "runtime": "python",
                    "bundles": ["runtime"],
                    "native_threads": "single",
                }
            },
        }
        module.write_json(self.tool / "template_map.json", self.map)
        self.scripts = self.root / "wrappers/inference_tools/scripts"
        self.scripts.mkdir(parents=True)
        for name in (
            "build_tool_images.py",
            "benchmark_costs.py",
            "validate_tool_costs.py",
        ):
            (self.scripts / name).write_text("# reviewed shared source\n")
        self.central = self.scripts / "template_map.json"
        module.write_json(
            self.central,
            {
                "bundle_templates": self.map["bundle_templates"],
                "tools": {"existing": {"runtime": "python", "bundles": ["runtime"]}},
            },
        )
        config = json.loads(
            (ROOT / "wrappers/inference_tools/cost_profiles/dyngenie3.json").read_text()
        )
        module.write_json(
            self.root / "wrappers/inference_tools/cost_profiles/dyngenie3.json", config
        )
        self.external = self.directory / "external.json"
        module.write_json(self.external, {"status": "running", "active": True})
        self.evidence = self.directory / "evidence"
        self.supervisor = module.Supervisor(
            root=self.root, evidence=self.evidence, external_state=self.external
        )

    def initialize(self) -> None:
        self.evidence.mkdir(parents=True)
        self.supervisor.initialize()

    def completed(self) -> None:
        module.write_json(self.external, {"status": "completed", "active": False})

    def test_plan_only_freezes_sources_without_subprocess_or_external_success(
        self,
    ) -> None:
        with patch.object(
            module.subprocess,
            "Popen",
            side_effect=AssertionError("No subprocess in plan-only"),
        ):
            self.assertEqual(self.supervisor.run(plan_only=True), 0)
        plan = module.read_json(self.evidence / "plan.json")
        self.assertEqual(plan["planned_calibration_runs"], 408)
        self.assertEqual(plan["pm2"]["autorestart"], False)
        self.assertEqual(plan["calibration"].count("--tool"), 1)
        self.assertIn("--catalog-images", plan["calibration"])
        self.assertNotIn("publish", plan["phase_order"])
        self.assertFalse(module.read_json(self.evidence / "state.json")["published"])

    def test_waits_thirty_seconds_until_explicit_external_completion(self) -> None:
        self.initialize()
        sleeps = []

        def release(seconds):
            sleeps.append(seconds)
            self.completed()

        self.supervisor.sleep = release
        with patch.object(
            module.subprocess,
            "Popen",
            side_effect=AssertionError("Wait must not invoke Docker"),
        ):
            self.supervisor.guard("build_candidate")
        self.assertEqual(sleeps, [30])
        self.assertEqual(
            self.supervisor.state["external_state_receipt"]["status"], "completed"
        )

    def test_external_missing_malformed_failed_and_active_states_do_not_authorize(
        self,
    ) -> None:
        for value in (
            None,
            [],
            {"status": []},
            {"status": "failed"},
            {"status": "completed", "active": True},
            {"status": "completed", "active": "false"},
        ):
            self.external.write_text(json.dumps(value))
            self.assertIsNone(module.external_receipt(self.external))
        for status in ("completed", "complete", "succeeded"):
            module.write_json(self.external, {"status": status})
            self.assertEqual(module.external_receipt(self.external)["status"], status)

    def test_changed_source_blocks_before_next_subprocess(self) -> None:
        self.initialize()
        self.completed()
        (self.tool / "run_tool.py").write_text("changed after review\n")
        with patch.object(
            self.supervisor,
            "command",
            side_effect=AssertionError("Changed source must not run"),
        ):
            with self.assertRaisesRegex(module.IntegrationError, "changed"):
                self.supervisor.build()

    def test_lock_excludes_second_supervisor(self) -> None:
        other = module.Supervisor(
            root=self.root, evidence=self.evidence, external_state=self.external
        )
        with self.supervisor.locked():
            with self.assertRaisesRegex(module.IntegrationError, "Another supervisor"):
                with other.locked():
                    self.fail("Second lock unexpectedly acquired")

    def test_build_refuses_existing_unknown_tag_and_never_overwrites_it(self) -> None:
        self.initialize()
        self.completed()
        with (
            patch.object(self.supervisor, "image", return_value=OTHER_IMAGE),
            patch.object(self.supervisor, "command") as command,
        ):
            with self.assertRaisesRegex(
                module.IntegrationError, "unrecognized image tag"
            ):
                self.supervisor.build()
            command.assert_not_called()

    def test_failed_verification_stops_before_promotion_or_fits(self) -> None:
        def built():
            self.supervisor.state["image_id"] = IMAGE

        def result(args, label, **_kwargs):
            out = Path(args[args.index("--out") + 1])
            module.write_json(
                out / "verification.json", {"status": "failed", "image_id": IMAGE}
            )
            return subprocess.CompletedProcess(args, 2, "failed")

        self.completed()
        with (
            patch.object(self.supervisor, "build", side_effect=built),
            patch.object(self.supervisor, "command", side_effect=result),
            patch.object(self.supervisor, "promote") as promote,
            patch.object(self.supervisor, "calibrate") as calibrate,
        ):
            self.assertEqual(self.supervisor.run(), 2)
            promote.assert_not_called()
            calibrate.assert_not_called()
        self.assertEqual(self.supervisor.state["functional"]["status"], "failed")

    def test_functional_receipt_must_identify_the_exact_verified_image(self) -> None:
        self.initialize()
        self.completed()
        self.supervisor.state["image_id"] = IMAGE

        def result(args, _label, **_kwargs):
            out = Path(args[args.index("--out") + 1])
            module.write_json(
                out / "verification.json", {"status": "passed", "image_id": OTHER_IMAGE}
            )
            return subprocess.CompletedProcess(args, 0, "")

        with patch.object(self.supervisor, "command", side_effect=result):
            with self.assertRaisesRegex(module.IntegrationError, "did not pass"):
                self.supervisor.verify()

    def test_promotion_preserves_other_template_entries_and_rechecks_external_state(
        self,
    ) -> None:
        self.initialize()
        self.completed()
        self.supervisor.state.update(image_id=IMAGE, functional={"status": "passed"})
        before = module.read_json(self.central)
        calls = []

        def result(args, label, **_kwargs):
            calls.append(label)
            return subprocess.CompletedProcess(args, 0, "")

        with (
            patch.object(self.supervisor, "image", return_value=IMAGE),
            patch.object(self.supervisor, "command", side_effect=result),
        ):
            self.supervisor.promote()
        after = module.read_json(self.central)
        self.assertEqual(after["tools"]["existing"], before["tools"]["existing"])
        self.assertEqual(after["bundle_templates"], before["bundle_templates"])
        self.assertEqual(after["tools"]["dyngenie3"], self.map["tools"]["dyngenie3"])
        self.assertEqual(calls, ["render_catalogs"])
        self.supervisor.check_sources()
        activated = (
            self.root / "andrea/catalog_inference_tools/tools/dyngenie3/toolspec.json"
        )
        activated.write_text("different spec")
        with self.assertRaisesRegex(
            module.IntegrationError, "Activated artifact changed"
        ):
            self.supervisor.check_sources()

    def test_gates_keep_independent_validation_pending_even_when_cost_checks_pass(
        self,
    ) -> None:
        self.initialize()
        self.completed()
        self.supervisor.state["image_id"] = IMAGE
        with patch.object(
            self.supervisor,
            "command",
            return_value=subprocess.CompletedProcess([], 0, ""),
        ):
            self.supervisor.gates()
        self.assertEqual(self.supervisor.state["status"], "pending_prediction_review")
        self.assertEqual(
            self.supervisor.state["independent_predictions"]["measured"], False
        )
        self.assertFalse(self.supervisor.state["published"])

    def test_unknown_cost_file_is_never_replaced(self) -> None:
        self.initialize()
        self.completed()
        self.supervisor.state["image_id"] = IMAGE
        module.write_json(
            self.evidence / "calibration-catalog/dyngenie3/cost.json",
            {"unrecognized": True},
        )
        with (
            patch.object(self.supervisor, "image", return_value=IMAGE),
            patch.object(self.supervisor, "command") as command,
        ):
            with self.assertRaisesRegex(
                module.IntegrationError, "unrecognized or changed cost"
            ):
                self.supervisor.calibrate()
            command.assert_not_called()

    def test_passed_predictions_register_only_verified_staging_cost_and_still_need_acceptance(
        self,
    ):
        self.initialize()
        self.completed()
        self.supervisor.state["image_id"] = IMAGE
        stage = self.evidence / "calibration-catalog/dyngenie3/cost.json"
        module.write_json(stage, {"synthetic_unit_test_only": True})

        def result(args, label, **_kwargs):
            if label == "independent_predictions":
                module.write_json(
                    self.evidence / "predictions/prediction-report.json",
                    {"status": "passed", "image_id": IMAGE, "observed_run_count": 24},
                )
            return subprocess.CompletedProcess(args, 0, "")

        with patch.object(self.supervisor, "command", side_effect=result):
            self.supervisor.gates()
        target = self.root / "andrea/catalog_inference_tools/tools/dyngenie3/cost.json"
        self.assertEqual(target.read_bytes(), stage.read_bytes())
        self.assertEqual(self.supervisor.state["status"], "pending_acceptance_dossier")
        self.assertEqual(
            self.supervisor.state["independent_predictions"]["status"], "passed"
        )
        self.assertFalse(self.supervisor.state["published"])


if __name__ == "__main__":
    unittest.main()
