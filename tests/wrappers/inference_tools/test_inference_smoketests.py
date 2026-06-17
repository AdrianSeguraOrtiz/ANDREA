from __future__ import annotations

import os
import importlib.util
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "wrappers" / "inference_tools" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
SMOKETEST_SCRIPT = (
    SCRIPTS_ROOT / "run_smoketests.py"
)
BUILD_IMAGES_SCRIPT = (
    SCRIPTS_ROOT / "build_tool_images.py"
)
CATALOG_TOOLS_ROOT = REPO_ROOT / "andrea" / "catalog_inference_tools" / "tools"


def _load_run_smoketests_module():
    spec = importlib.util.spec_from_file_location("run_smoketests", SMOKETEST_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


class InferenceToolSmoketestScripts(unittest.TestCase):
    def test_smoketest_threads_are_capped_for_serial_tools(self) -> None:
        module = _load_run_smoketests_module()

        self.assertEqual(
            module.resolve_smoketest_threads(
                tool_id="clr",
                catalog_tool_dir=CATALOG_TOOLS_ROOT / "clr",
                requested_threads=2,
            ),
            1,
        )

    def test_smoketest_threads_keep_supported_requested_value(self) -> None:
        module = _load_run_smoketests_module()

        self.assertEqual(
            module.resolve_smoketest_threads(
                tool_id="grnboost2",
                catalog_tool_dir=CATALOG_TOOLS_ROOT / "grnboost2",
                requested_threads=2,
            ),
            2,
        )

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
        self.assertIn("genie3", completed.stdout)

    def test_build_tool_images_list_mode(self) -> None:
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
        self.assertIn("genie3", completed.stdout)

    @unittest.skipUnless(
        _has_docker_runtime(),
        "set ANDREA_RUN_DOCKER_SMOKETESTS=1 to run inference Docker smoketests",
    )
    def test_genie3_smoketest(self) -> None:
        completed = subprocess.run(
            [
                _python_executable(),
                str(SMOKETEST_SCRIPT),
                "--tool",
                "genie3",
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            self.fail(
                "genie3 inference smoketest failed:\n"
                f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )
