from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKETEST_SCRIPT = (
    REPO_ROOT / "wrappers" / "inference_tools" / "scripts" / "run_smoketests.py"
)
BUILD_IMAGES_SCRIPT = (
    REPO_ROOT / "wrappers" / "inference_tools" / "scripts" / "build_tool_images.py"
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


class InferenceToolSmoketestScripts(unittest.TestCase):
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
