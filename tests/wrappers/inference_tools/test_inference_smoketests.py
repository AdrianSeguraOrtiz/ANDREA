from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
PYTHON_RUNTIME_HELPERS = (
    SCRIPTS_ROOT / "templates" / "python" / "_run_tool_common.py"
)


def _load_run_smoketests_module():
    spec = importlib.util.spec_from_file_location("run_smoketests", SMOKETEST_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_python_runtime_helpers():
    spec = importlib.util.spec_from_file_location(
        "inference_run_tool_common",
        PYTHON_RUNTIME_HELPERS,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
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
    def test_physical_execution_mode_is_required_and_strict(self) -> None:
        helpers = _load_python_runtime_helpers()
        with tempfile.TemporaryDirectory() as tmp:
            params_path = Path(tmp) / "params.json"
            params_path.write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(
                FileNotFoundError, "execution.json is required"
            ):
                helpers.load_execution_mode(
                    params_path,
                    supported_modes={"global"},
                )

            execution_path = params_path.parent / "execution.json"
            execution_path.write_text(
                json.dumps({"mode": "global"}),
                encoding="utf-8",
            )
            self.assertEqual(
                helpers.load_execution_mode(
                    params_path,
                    supported_modes={"global"},
                ),
                "global",
            )

            execution_path.write_text(
                json.dumps({"mode": "group_emulated"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "physical execution.mode"):
                helpers.load_execution_mode(
                    params_path,
                    supported_modes={"global"},
                )

    def test_smoketest_config_requires_a_physical_execution_mode(self) -> None:
        module = _load_run_smoketests_module()
        config_path = Path("smoke.json")

        for execution in ({}, {"mode": "group_emulated"}, {"mode": "group_aggregated"}):
            with self.subTest(execution=execution), self.assertRaisesRegex(
                ValueError,
                "physical execution.mode",
            ):
                module._parse_smoke_config_payload(
                    raw={"execution": execution},
                    config_path=config_path,
                    default_name="test",
                )

        for mode in ("global", "group_native", "column_native"):
            with self.subTest(mode=mode):
                config = module._parse_smoke_config_payload(
                    raw={"execution": {"mode": mode}},
                    config_path=config_path,
                    default_name="test",
                )
                self.assertEqual(config.execution, {"mode": mode})

    def test_smoketest_config_file_is_required(self) -> None:
        module = _load_run_smoketests_module()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(
            FileNotFoundError,
            "Smoketest config is required",
        ):
            module.load_configs(tool_id="missing", configs_dir=Path(tmp))

    def test_smoketest_container_uses_the_production_mount_boundary(self) -> None:
        module = _load_run_smoketests_module()
        with tempfile.TemporaryDirectory() as tmp:
            io_dir = Path(tmp) / "io"
            (io_dir / "out").mkdir(parents=True)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="container-id\n",
                stderr="",
            )

            with mock.patch.object(module, "run_cmd", return_value=completed) as run_cmd:
                container_id = module.start_container(
                    "andrea/test:latest",
                    io_dir,
                    3,
                )

            self.assertEqual(container_id, "container-id")
            command = run_cmd.call_args.args[0]
            self.assertEqual(
                command,
                [
                    "docker",
                    "run",
                    "-d",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "-v",
                    f"{io_dir.resolve()}:/io:ro",
                    "-v",
                    f"{(io_dir / 'out').resolve()}:/io/out:rw",
                    "andrea/test:latest",
                    "--input",
                    "/io/expression.tsv",
                    "--params",
                    "/io/params.json",
                    "--extra",
                    "/io/extra",
                    "--output-dir",
                    "/io/out",
                    "--threads",
                    "3",
                ],
            )

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
