from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_ROOT = REPO_ROOT / "wrappers" / "inference_tools" / "tools"
PYTHON_TEMPLATE_ROOT = (
    REPO_ROOT / "wrappers" / "inference_tools" / "scripts" / "templates" / "python"
)

if str(PYTHON_TEMPLATE_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_TEMPLATE_ROOT))


def _load_python_wrapper(tool_id: str) -> types.ModuleType:
    module_name = f"{tool_id}_execution_contract_run_tool"
    spec = importlib.util.spec_from_file_location(
        module_name,
        TOOLS_ROOT / tool_id / "run_tool.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _has_r_jsonlite() -> bool:
    if shutil.which("Rscript") is None:
        return False
    completed = subprocess.run(
        [
            "Rscript",
            "-e",
            'quit(status=if (requireNamespace("jsonlite", quietly=TRUE)) 0 else 1)',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


class PythonWrapperExecutionModeTests(unittest.TestCase):
    def test_wrappers_require_an_explicit_physical_execution_mode(self) -> None:
        cases = (
            ("cespgrn", "load_execution_mode", "column_native"),
            ("scgenerai", "load_execution_mode", "column_native"),
            ("simic", "_load_execution", "group_native"),
            ("miniex3", "_load_execution_mode", "group_native"),
            ("scmtni", "_load_execution", "group_native"),
        )

        for tool_id, loader_name, physical_mode in cases:
            with self.subTest(tool=tool_id):
                loader = getattr(_load_python_wrapper(tool_id), loader_name)
                with tempfile.TemporaryDirectory() as tmp:
                    params_path = Path(tmp) / "params.json"
                    params_path.write_text("{}\n", encoding="utf-8")

                    with self.assertRaisesRegex(
                        FileNotFoundError,
                        "execution.json is required",
                    ):
                        loader(params_path)

                    execution_path = params_path.parent / "execution.json"
                    execution_path.write_text("{}\n", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "non-empty string"):
                        loader(params_path)

                    execution_path.write_text(
                        json.dumps({"mode": physical_mode}),
                        encoding="utf-8",
                    )
                    self.assertEqual(loader(params_path), physical_mode)

                    execution_path.write_text(
                        json.dumps({"mode": "group_emulated"}),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, "physical execution.mode"):
                        loader(params_path)


@unittest.skipUnless(
    _has_r_jsonlite(),
    "Rscript and jsonlite are required for R wrapper execution-mode tests",
)
class RWrapperExecutionModeTests(unittest.TestCase):
    def test_column_native_wrappers_require_an_explicit_mode(self) -> None:
        r_test = r'''
        parsed <- as.list(parse(file = commandArgs(TRUE)[[1L]]))
        is_loader <- function(expression) {
          is.call(expression) &&
            identical(expression[[1L]], as.name("<-")) &&
            identical(as.character(expression[[2L]]), "load_execution_mode")
        }
        loader_expression <- Filter(is_loader, parsed)
        stopifnot(length(loader_expression) == 1L)
        environment <- new.env(parent = baseenv())
        environment$fromJSON <- jsonlite::fromJSON
        eval(loader_expression[[1L]], envir = environment)

        params_path <- commandArgs(TRUE)[[2L]]
        expect_error <- function(expression, pattern) {
          observed <- tryCatch(
            { force(expression); NA_character_ },
            error = function(condition) conditionMessage(condition)
          )
          stopifnot(!is.na(observed), grepl(pattern, observed, fixed = TRUE))
        }

        expect_error(
          environment$load_execution_mode(params_path),
          "execution.json is required."
        )
        writeLines("{}", file.path(dirname(params_path), "execution.json"))
        expect_error(
          environment$load_execution_mode(params_path),
          "execution.mode must be a non-empty string."
        )
        writeLines(
          '{"mode":"column_native"}',
          file.path(dirname(params_path), "execution.json")
        )
        stopifnot(identical(
          environment$load_execution_mode(params_path),
          "column_native"
        ))
        writeLines(
          '{"mode":"group_aggregated"}',
          file.path(dirname(params_path), "execution.json")
        )
        expect_error(
          environment$load_execution_mode(params_path),
          "physical execution.mode=column_native"
        )
        '''

        for tool_id in ("kscreni", "lioness"):
            with self.subTest(tool=tool_id), tempfile.TemporaryDirectory() as tmp:
                params_path = Path(tmp) / "params.json"
                params_path.write_text("{}\n", encoding="utf-8")
                completed = subprocess.run(
                    [
                        "Rscript",
                        "-e",
                        r_test,
                        str(TOOLS_ROOT / tool_id / "run_tool.R"),
                        str(params_path),
                    ],
                    cwd=REPO_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    "R execution-mode test failed:\n"
                    f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}",
                )


if __name__ == "__main__":
    unittest.main()
