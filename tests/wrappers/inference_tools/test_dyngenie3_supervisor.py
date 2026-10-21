from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "wrappers/inference_tools/tools/dyngenie3/continue_integration.py"
spec = importlib.util.spec_from_file_location("dyngenie3_supervisor", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
IMAGE = "sha256:" + "a" * 64
OTHER_IMAGE = "sha256:" + "b" * 64
compat_spec = importlib.util.spec_from_file_location(
    "supervisor_calibration_compatibility",
    ROOT / "wrappers/calibration_compatibility.py",
)
compatibility = importlib.util.module_from_spec(compat_spec)
compat_spec.loader.exec_module(compatibility)


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
        self.supervisor.functional_plan = lambda: {
            "run_count": 26,
            "execution_timeout_cap_seconds": 93600,
            "cases": [{"timeout_seconds": 3600}],
            "diagnostic_probe_count": 5,
            "diagnostic_probe_timeout_seconds": 120,
        }

    def initialize(self) -> None:
        self.evidence.mkdir(parents=True)
        self.supervisor.initialize()

    def cleanup_subject(self):
        self.supervisor.state["image_id"] = IMAGE
        io = self.evidence / "calibration/dyngenie3-test/profile/run/io"
        io.mkdir(parents=True)
        container_id = "c" * 64
        (io / ".docker-container.cid").write_text(container_id)
        info = {
            "Id": container_id,
            "Image": IMAGE,
            "Name": "/andrea_tool_cost_dyngenie3_test",
            "State": {"Running": True},
            "Mounts": [{"Type": "bind", "Source": str(io), "Destination": "/io"}],
        }
        return container_id, info

    def test_interrupted_calibration_cleans_only_owned_container_and_records_130(self):
        self.initialize()
        container_id, info = self.cleanup_subject()
        process = Mock(
            pid=123,
            wait=Mock(side_effect=[KeyboardInterrupt(), 0]),
            poll=Mock(return_value=None),
        )
        results = [
            subprocess.CompletedProcess([], 0, container_id, ""),
            subprocess.CompletedProcess([], 0, json.dumps([info]), ""),
            subprocess.CompletedProcess([], 0, container_id, ""),
        ]
        with (
            patch.object(module.subprocess, "Popen", return_value=process),
            patch.object(module.subprocess, "run", side_effect=results) as run,
            patch.object(module.os, "killpg") as kill,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.supervisor.command(["mock-benchmark"], "calibration")
        kill.assert_called_once_with(123, module.signal.SIGTERM)
        self.assertEqual(
            run.call_args_list[-1].args[0], ["docker", "rm", "--force", container_id]
        )
        self.assertEqual(self.supervisor.state["commands"][-1]["exit_code"], 130)
        self.assertEqual(
            self.supervisor.state["commands"][-1]["container_cleanup"][0]["status"],
            "removed",
        )
        self.assertEqual(self.supervisor.state["calibration"]["status"], "interrupted")

    def test_cleanup_refuses_wrong_mount_name_image_or_id(self):
        self.initialize()
        container_id, original = self.cleanup_subject()
        for key in ("Mounts", "Name", "Image", "Id"):
            with self.subTest(key=key):
                info = {**original, key: [] if key == "Mounts" else "unrelated"}
                results = [
                    subprocess.CompletedProcess([], 0, container_id, ""),
                    subprocess.CompletedProcess([], 0, json.dumps([info]), ""),
                ]
                with patch.object(module.subprocess, "run", side_effect=results) as run:
                    report = self.supervisor.cleanup_calibration_containers()
                self.assertEqual(run.call_count, 2)
                self.assertEqual(report[0]["status"], "ownership_unverified")

    def test_other_phase_interruption_never_scans_calibration_containers(self):
        self.initialize()
        process = Mock(
            pid=123,
            wait=Mock(side_effect=[KeyboardInterrupt(), 0]),
            poll=Mock(return_value=None),
        )
        with (
            patch.object(module.subprocess, "Popen", return_value=process),
            patch.object(module.os, "killpg"),
            patch.object(self.supervisor, "cleanup_calibration_containers") as cleanup,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.supervisor.command(["mock-verification"], "functional")
        cleanup.assert_not_called()

    def previous_build(self):
        for name in module.IMAGE_SUBJECTS:
            path = self.root / name
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("installed source\n")
        self.initialize()
        previous = self.directory / "previous"
        previous.mkdir()
        module.write_json(previous / "freeze.json", self.supervisor.freeze)
        log = previous / "build.log"
        log.write_text("Successful original build output\n")
        old = {
            "source_sha256": self.supervisor.freeze["source_sha256"],
            "image_id": IMAGE,
            "build": {"status": "passed"},
            "commands": [
                {
                    "status": "passed",
                    "exit_code": 0,
                    "command": [str(self.scripts / "build_tool_images.py")],
                    "log": str(log),
                    "log_sha256": module.sha(log),
                }
            ],
        }
        module.write_json(previous / "state.json", old)
        return previous

    def previous_calibration(self):
        for name in compatibility.MEASUREMENT_SUBJECTS:
            path = self.root / name
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic unchanged measured implementation")
        previous = self.previous_build()
        directory = previous / "calibration/dyngenie3-original/profile"
        io = directory / "run/io"
        (io / "extra").mkdir(parents=True)
        (io / "out").mkdir()
        for name in ("expression.tsv", "params.json", "execution.json"):
            (io / name).write_text("synthetic unit-test input")
        (io / "out/network.csv").write_text("source,target,score\nG1,G2,1\n")
        provenance = {
            "image_id": IMAGE,
            "source_sha256": "b" * 64,
            "cpu_model": "unit-test-cpu",
            "platform": "unit-test",
            "logical_cpus": 8,
            "host_memory_bytes": 16 * 1024**3,
            "docker_server": "unit-test-version",
            "timing_scope": "unit-test-time",
            "seed": 42,
        }
        config = {
            "provenance": provenance,
            "params_profile": {"resolved_params": {"ntrees": 1000}},
            "execution_profile": {"mode": "global"},
            "input_profile": {
                "temporal": {"schema_version": "unit-test"},
                "temporal_fixture": {"series_count": 1, "timepoints_per_series": 2},
            },
        }
        row = {
            "run_id": "c" * 32,
            "status": "ok",
            "seconds": 1.5,
            "genes": 1,
            "columns": 2,
            "threads": 1,
            "ram_gb": 4,
            "repeat": 1,
            "dimensions": {"genes": 1, "columns": 2},
            "input_sha256": compatibility.inputs(io),
            "io_dir": str(io),
        }
        module.write_json(directory / "benchmark_config.json", config)
        module.write_json(directory / "measurements.json", [row])
        cost = previous / "calibration-catalog/dyngenie3/cost.json"
        module.write_json(
            cost,
            {
                "profiles": [
                    {
                        "profile_id": "profile",
                        "benchmark_config": config,
                        "runtime_points": [
                            {"measurement_evidence": {"observations": [row]}}
                        ],
                    }
                ]
            },
        )
        state = module.read_json(previous / "state.json")
        state.update(
            status="interrupted",
            calibration={"status": "interrupted", "cost_sha256": module.sha(cost)},
        )
        module.write_json(previous / "state.json", state)
        current = {**provenance, "source_sha256": "d" * 64}
        profile = SimpleNamespace(
            profile_id="profile",
            params_profile=config["params_profile"],
            execution_profile=config["execution_profile"],
            input_profile=config["input_profile"],
            params={"ntrees": 1000},
            execution={"mode": "global"},
        )

        def prepare(target, *_args, **_kwargs):
            import shutil

            shutil.copytree(io, target)

        backend = SimpleNamespace(
            collect_provenance=Mock(return_value=current),
            resolve_benchmark_profiles=Mock(return_value=[profile]),
            prepare_io_dir=prepare,
            SizePoint=lambda genes, columns: (genes, columns),
        )
        self.supervisor.state["image_id"] = IMAGE
        self.completed()
        return previous, backend, directory / "measurements.json"

    def test_calibration_migration_seals_original_rows_and_passes_reuse_paths(self):
        previous, backend, raw = self.previous_calibration()
        original = raw.read_bytes()
        support = SimpleNamespace(_support=lambda: (None, backend, None))
        with (
            patch.object(self.supervisor, "tool_module", return_value=support),
            patch.object(
                self.supervisor, "compatibility_module", return_value=compatibility
            ),
        ):
            self.supervisor.reuse_calibration(previous)
        state = self.supervisor.state
        self.assertEqual(state["calibration"]["status"], "imported_checkpoint")
        self.assertEqual(raw.read_bytes(), original)
        copied = self.evidence / "calibration-catalog/dyngenie3/cost.json"
        self.assertEqual(
            copied.read_bytes(),
            (previous / "calibration-catalog/dyngenie3/cost.json").read_bytes(),
        )
        proof = Path(state["calibration_compatibility"]["receipt"])
        verified = compatibility.verify(
            proof, image_id=IMAGE, current_source_sha256="d" * 64
        )
        self.assertEqual(len(verified["executions"]), 1)
        self.assertEqual(
            verified["executions"]["c" * 32]["original_source_sha256"], "b" * 64
        )
        self.assertIn(
            str(previous / "calibration"), self.supervisor.benchmark_command()
        )
        self.assertIn(str(proof), self.supervisor.prediction_command(IMAGE))

    def test_running_original_campaign_cannot_authorize_migration(self):
        previous, backend, raw = self.previous_calibration()
        state = module.read_json(previous / "state.json")
        state["status"] = "running"
        module.write_json(previous / "state.json", state)
        with patch.object(self.supervisor, "tool_module") as support:
            with self.assertRaisesRegex(module.IntegrationError, "Stop and retain"):
                self.supervisor.reuse_calibration(previous)
            support.assert_not_called()

    def test_reviewed_recovery_reuses_build_but_never_imports_acceptance(self):
        previous = self.previous_build()
        self.completed()
        before = (previous / "state.json").read_bytes()
        with patch.object(self.supervisor, "image", return_value=IMAGE):
            self.supervisor.reuse_image(previous)
        self.assertEqual(self.supervisor.state["image_id"], IMAGE)
        self.assertEqual(
            self.supervisor.state["build"]["status"], "reused_immutable_image"
        )
        self.assertEqual(self.supervisor.state["functional"]["status"], "pending")
        self.assertEqual(self.supervisor.state["calibration"]["status"], "pending")
        self.assertEqual((previous / "state.json").read_bytes(), before)

    def test_recovery_refuses_changed_installed_source_or_tampered_build_receipt(self):
        previous = self.previous_build()
        self.completed()
        frozen = module.read_json(previous / "freeze.json")
        name = module.IMAGE_SUBJECTS[0]
        frozen["identity"]["sources"][name] = "different installed Dockerfile"
        frozen["source_sha256"] = module.digest(frozen["identity"]["sources"])
        old = module.read_json(previous / "state.json")
        old["source_sha256"] = frozen["source_sha256"]
        module.write_json(previous / "freeze.json", frozen)
        module.write_json(previous / "state.json", old)
        with patch.object(self.supervisor, "image") as inspect:
            with self.assertRaisesRegex(module.IntegrationError, "subjects changed"):
                self.supervisor.reuse_image(previous)
            inspect.assert_not_called()
        module.write_json(previous / "freeze.json", self.supervisor.freeze)
        old["source_sha256"] = self.supervisor.freeze["source_sha256"]
        module.write_json(previous / "state.json", old)
        (previous / "build.log").write_text("Changed build evidence")
        with patch.object(self.supervisor, "image") as inspect:
            with self.assertRaisesRegex(
                module.IntegrationError, "build output evidence"
            ):
                self.supervisor.reuse_image(previous)
            inspect.assert_not_called()

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
        self.assertEqual(plan["planned_calibration_runs"], 1944)
        self.assertEqual(plan["execution_budget"]["functional"]["runs"], 26)
        self.assertEqual(plan["execution_budget"]["total_container_runs"], 2283)
        self.assertEqual(
            plan["execution_budget"]["science_timeout_cap_seconds"], 8218800
        )
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

    def test_unclosed_acceptance_gate_is_not_a_successful_process_exit(self):
        self.completed()

        def pending():
            self.supervisor.save(status="pending_prediction_review")

        with (
            patch.object(self.supervisor, "build"),
            patch.object(self.supervisor, "verify"),
            patch.object(self.supervisor, "promote"),
            patch.object(self.supervisor, "calibrate"),
            patch.object(self.supervisor, "gates", side_effect=pending),
        ):
            self.assertEqual(self.supervisor.run(), 2)
        self.assertEqual(self.supervisor.state["status"], "pending_prediction_review")

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
                    {"status": "passed", "image_id": IMAGE, "observed_run_count": 312},
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
