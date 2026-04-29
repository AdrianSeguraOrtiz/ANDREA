from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATE_TOOLSPECS_SCRIPT = (
    REPO_ROOT / "wrappers" / "inference_tools" / "scripts" / "validate_toolspecs.py"
)


def _python_executable() -> str:
    return str(
        Path(".venv/bin/python") if Path(".venv/bin/python").exists() else "python"
    )


class ToolSpecCatalogTest(unittest.TestCase):
    def test_all_tool_specs_validate_with_wrapper_script(self) -> None:
        completed = subprocess.run(
            [
                _python_executable(),
                str(VALIDATE_TOOLSPECS_SCRIPT),
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
