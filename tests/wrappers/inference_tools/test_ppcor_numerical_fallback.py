"""Regression checks for the PPCOR singular-inverse recovery path."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "wrappers/inference_tools/tools/ppcor/run_tool.R"


@unittest.skipUnless(shutil.which("Rscript"), "Rscript is required")
class PPCORNumericalFallbackTests(unittest.TestCase):
    def run_r(self, checks: str) -> None:
        source = r'''
        parsed <- as.list(parse(file=commandArgs(TRUE)[[1L]]))
        for (expression in parsed) {
          if (is.call(expression) && identical(expression[[1L]], as.name("<-")) &&
              identical(as.character(expression[[2L]]), "pcor_with_inverse_fallback")) {
            eval(expression)
          }
        }
        stopifnot(requireNamespace("MASS", quietly=TRUE))
        '''
        result = subprocess.run(["Rscript", "--vanilla", "-", str(WRAPPER)],
                                input=source + checks, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_well_conditioned_result_is_returned_unchanged(self) -> None:
        self.run_r(r'''
        upstream <- function(x, method) list(estimate=solve(x), method=method, p.value=42)
        x <- matrix(c(2, 0.25, 0.25, 3), 2)
        expected <- upstream(x, "pearson")
        observed <- pcor_with_inverse_fallback(x, "pearson", upstream)
        stopifnot(identical(expected, observed))
        ''')

    def test_exactly_singular_matrix_uses_upstream_generalized_inverse(self) -> None:
        self.run_r(r'''
        upstream <- function(x, method) list(estimate=solve(x), method=method)
        x <- matrix(c(1,1,1,1), 2)
        messages <- character()
        result <- withCallingHandlers(pcor_with_inverse_fallback(x, "kendall", upstream),
          warning=function(w) {messages <<- c(messages, conditionMessage(w)); invokeRestart("muffleWarning")})
        stopifnot(isTRUE(all.equal(result$estimate, MASS::ginv(x))),
                  identical(result$method, "kendall"),
                  identical(result$numerical_inverse_fallback$method, "MASS::ginv"),
                  length(messages)==1L, grepl("Moore-Penrose",messages[[1L]]))
        ''')

    def test_ill_conditioned_matrix_recovers_and_reports_original_failure(self) -> None:
        self.run_r(r'''
        upstream <- function(x, method) list(estimate=solve(x))
        x <- diag(c(1, 1e-20))
        result <- suppressWarnings(pcor_with_inverse_fallback(x, "pearson", upstream))
        stopifnot(isTRUE(all.equal(result$estimate, MASS::ginv(x))),
                  grepl("computationally singular", result$numerical_inverse_fallback$original_error),
                  identical(result$numerical_inverse_fallback$tolerance, sqrt(.Machine$double.eps)))
        ''')

    def test_other_upstream_errors_are_not_hidden(self) -> None:
        self.run_r(r'''
        upstream <- function(x, method) stop("invalid method or input")
        message <- tryCatch(pcor_with_inverse_fallback(diag(2), "invalid", upstream),
                            error=function(e)conditionMessage(e))
        stopifnot(identical(message,"invalid method or input"))
        ''')


if __name__ == "__main__":
    unittest.main()
