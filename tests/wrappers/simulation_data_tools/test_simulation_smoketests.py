from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKETEST_SCRIPT = (
    REPO_ROOT / "wrappers" / "simulation_data_tools" / "scripts" / "run_smoketests.py"
)
VALIDATE_SMOKETEST_SCRIPT = (
    REPO_ROOT
    / "wrappers"
    / "simulation_data_tools"
    / "scripts"
    / "validate_smoketest_configs.py"
)
SMOKETEST_CONFIGS_ROOT = (
    REPO_ROOT / "wrappers" / "simulation_data_tools" / "tests" / "smoketest_configs"
)
FIXTURES_ROOT = REPO_ROOT / "wrappers" / "simulation_data_tools" / "tests" / "fixtures"
INPUT_SPECS_ROOT = REPO_ROOT / "andrea" / "catalog_simulation_data_tools" / "input_specs"
BUILD_IMAGES_SCRIPT = (
    REPO_ROOT
    / "wrappers"
    / "simulation_data_tools"
    / "scripts"
    / "build_simulator_images.py"
)


def _python_executable() -> str:
    return str(
        Path(".venv/bin/python") if Path(".venv/bin/python").exists() else "python"
    )


def _has_docker_runtime() -> bool:
    if os.environ.get("ANDREA_RUN_DOCKER_SMOKETESTS") != "1":
        return False
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


class SimulatorSmoketestScripts(unittest.TestCase):
    def test_all_smoketests_require_tf_list_output(self) -> None:
        config_paths = sorted(SMOKETEST_CONFIGS_ROOT.glob("*.json"))
        self.assertTrue(config_paths)
        for config_path in config_paths:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            with self.subTest(config=config_path.name):
                self.assertIn("tf_list", payload["request"]["effective_extras"])
                self.assertIn("extras/tf_list.txt", payload["required_files"])

    def test_column_truth_smoketests_require_cumulative_truth_contexts(self) -> None:
        config_paths = sorted(SMOKETEST_CONFIGS_ROOT.glob("*column_truth*.json"))
        self.assertTrue(config_paths)
        for config_path in config_paths:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(payload.get("required_truth_context_prefixes", [])),
                {"global", "group:", "column:"},
                msg=config_path.name,
            )

    def test_fixture_names_match_simulator_input_spec_ids(self) -> None:
        input_ids = {path.stem for path in INPUT_SPECS_ROOT.glob("*.json")}
        fixture_ids = {path.stem for path in FIXTURES_ROOT.iterdir() if path.is_file()}
        self.assertEqual(fixture_ids - input_ids, set())

    def test_run_smoketests_list_mode(self) -> None:
        completed = subprocess.run(
            [
                _python_executable(),
                str(SMOKETEST_SCRIPT),
                "--list",
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("dyngen", completed.stdout)

    def test_build_simulator_images_list_mode(self) -> None:
        completed = subprocess.run(
            [
                _python_executable(),
                str(BUILD_IMAGES_SCRIPT),
                "--list",
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("dyngen", completed.stdout)

    def test_validate_scmultisim_smoketest_configs_with_conditional_inputs(self) -> None:
        completed = subprocess.run(
            [
                _python_executable(),
                str(VALIDATE_SMOKETEST_SCRIPT),
                "--simulator",
                "scmultisim",
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("scmultisim_grouped_custom_inputs.json", completed.stdout)

    def test_smoketest_configs_cover_supported_capabilities_and_extras(self) -> None:
        completed = subprocess.run(
            [
                _python_executable(),
                str(VALIDATE_SMOKETEST_SCRIPT),
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(
        _has_docker_runtime(),
        "set ANDREA_RUN_DOCKER_SMOKETESTS=1 to run simulator Docker smoketests",
    )
    def test_dyngen_simulator_smoketest(self) -> None:
        completed = subprocess.run(
            [
                _python_executable(),
                str(SMOKETEST_SCRIPT),
                "--simulator",
                "dyngen",
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            self.fail(
                "dyngen simulator smoketest failed:\n"
                f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )
