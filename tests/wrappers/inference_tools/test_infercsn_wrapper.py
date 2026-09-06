from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_TOOL = (
    REPO_ROOT / "wrappers" / "inference_tools" / "tools" / "infercsn" / "run_tool.R"
)
TOOLSPEC = (
    REPO_ROOT
    / "andrea"
    / "catalog_inference_tools"
    / "tools"
    / "infercsn"
    / "toolspec.json"
)


class InfercsnToolSpecContractTests(unittest.TestCase):
    def test_catalog_exposes_global_core_and_group_orchestration(self) -> None:
        toolspec = json.loads(TOOLSPEC.read_text(encoding="utf-8"))

        self.assertEqual(
            toolspec["execution_capabilities"],
            ["global", "group_emulated"],
        )
        groups = next(
            rule
            for rule in toolspec["extra_inputs"]["conditional_required"]
            if rule["input"] == "groups"
        )
        self.assertEqual(groups["execution"], "mode")
        self.assertEqual(groups["value"], "group_emulated")
        self.assertEqual(groups["delivery"], "orchestration_only")

    def test_params_match_the_public_cran_core_boundary(self) -> None:
        toolspec = json.loads(TOOLSPEC.read_text(encoding="utf-8"))

        self.assertEqual(
            set(toolspec["params"]),
            {
                "penalty",
                "cross_validation",
                "seed",
                "n_folds",
                "subsampling_method",
                "subsampling_ratio",
                "r_squared_threshold",
                "sift_method",
            },
        )
        self.assertEqual(toolspec["params"]["sift_method"]["enum"], ["none", "max"])
        seed = toolspec["params"]["seed"]
        self.assertEqual((seed["min"], seed["max"]), (-2147483647, 2147483647))


@unittest.skipUnless(
    shutil.which("Rscript"), "Rscript is required for inferCSN wrapper tests"
)
class InfercsnWrapperContractTests(unittest.TestCase):
    def _run_r(self, body: str) -> None:
        completed = subprocess.run(
            ["Rscript", "-e", f"source({json.dumps(str(RUN_TOOL))})\n{body}"],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"R test failed:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}",
        )

    def test_parameter_and_thread_boundaries(self) -> None:
        self._run_r(
            r'''
            valid_params <- list(
              penalty = "L0",
              cross_validation = FALSE,
              seed = 1,
              n_folds = 5,
              subsampling_method = "sample",
              subsampling_ratio = 1,
              r_squared_threshold = 0,
              sift_method = "none"
            )
            expect_error <- function(expression, expected) {
              observed <- tryCatch(
                { force(expression); NA_character_ },
                error = function(condition) conditionMessage(condition)
              )
              stopifnot(!is.na(observed), identical(observed, expected))
            }

            stopifnot(resolve_params(valid_params)$seed == 1L)
            valid_params$seed <- R_INTEGER_MIN
            stopifnot(resolve_params(valid_params)$seed == R_INTEGER_MIN)
            valid_params$seed <- R_INTEGER_MAX
            stopifnot(resolve_params(valid_params)$seed == R_INTEGER_MAX)
            stopifnot(parse_int_argument("--threads", "1", min_value = 1L) == 1L)
            expect_error(
              parse_int_argument("--threads", "1.5", min_value = 1L),
              "--threads must be an integer."
            )
            '''
        )

    def test_physical_execution_requires_explicit_global_mode(self) -> None:
        self._run_r(
            r'''
            expect_error <- function(expression, expected) {
              observed <- tryCatch(
                { force(expression); NA_character_ },
                error = function(condition) conditionMessage(condition)
              )
              stopifnot(!is.na(observed), identical(observed, expected))
            }
            work <- tempfile("infercsn_execution_")
            dir.create(work)
            params_path <- file.path(work, "params.json")
            writeLines("{}", params_path)

            expect_error(load_execution_mode(params_path), "execution.json is required.")
            writeLines('{"mode":"global"}', file.path(work, "execution.json"))
            stopifnot(identical(load_execution_mode(params_path), "global"))
            writeLines('{"mode":"group_emulated"}', file.path(work, "execution.json"))
            expect_error(
              load_execution_mode(params_path),
              "inferCSN supports only physical execution.mode=global."
            )
            '''
        )

    def test_empty_upstream_table_converts_to_canonical_empty_network(self) -> None:
        self._run_r(
            r'''
            upstream <- data.frame(
              regulator = character(),
              target = character(),
              weight = numeric()
            )
            observed <- network_to_andrea(upstream)
            stopifnot(nrow(observed) == 0L)
            stopifnot(identical(
              names(observed),
              c("source", "target", "score", "sign", "evidence", "context")
            ))
            '''
        )


if __name__ == "__main__":
    unittest.main()
