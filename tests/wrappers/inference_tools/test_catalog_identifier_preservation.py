from __future__ import annotations

import ast
import importlib.util
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "wrappers/inference_tools/tools"
TEMPLATES = ROOT / "wrappers/inference_tools/scripts/templates/python"
if str(TEMPLATES) not in sys.path:
    sys.path.insert(0, str(TEMPLATES))


def load_wrapper(tool: str):
    name = f"audit_identifiers_{tool}"
    spec = importlib.util.spec_from_file_location(name, TOOLS / tool / "run_tool.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PythonIdentifierPreservationTests(unittest.TestCase):
    def test_inferelator_expression_prior_and_group_ids_match(self) -> None:
        # Test the actual readers without requiring the container's joblib
        # inference backend in the ANDREA package development environment.
        path = TOOLS / "inferelator3" / "run_tool.py"
        names = {"_read_expression_tsv", "_read_prior_grn", "_read_groups_tsv"}
        readers = [node for node in ast.parse(path.read_text()).body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in readers}, names)
        namespace = {"pd": pd, "Path": Path, "math": math}
        exec(compile(ast.Module(body=readers, type_ignores=[]), str(path), "exec"), namespace)
        wrapper = SimpleNamespace(**{name: namespace[name] for name in names})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expression = root / "expression.tsv"
            expression.write_text("gene\t01\t02\n001\t1\t2\nNA\t3\t4\n")
            self.assertEqual(wrapper._read_expression_tsv(expression).index.tolist(), ["001", "NA"])
            prior = root / "prior_grn.tsv"
            prior.write_text("source\ttarget\tscore\n001\tNA\t1\n")
            result = wrapper._read_prior_grn(prior, genes=["001", "NA"], tf_names=["001"])
            self.assertEqual(result.loc["NA", "001"], 1)
            groups = root / "groups.tsv"
            groups.write_text("sample\tcluster\n01\t001\n02\tNA\n")
            result = wrapper._read_groups_tsv(groups, ["01", "02"])
            self.assertEqual(result["condName"].tolist(), ["01", "02"])
            self.assertEqual(result["cluster"].tolist(), ["001", "NA"])

    def test_metasem_raw_edges_preserve_na_like_gene_names(self) -> None:
        wrapper = load_wrapper("metasem")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "edges.tsv"
            raw.write_text("TF\tTarget\tEdgeWeight\nNA\t001\t-0.25\n")
            output = root / "network.csv"
            wrapper._convert_network(raw, output)
            self.assertIn("NA,001,0.25,-,association,global", output.read_text())


@unittest.skipUnless(shutil.which("Rscript"), "Rscript is required for R reader tests")
class RIdentifierPreservationTests(unittest.TestCase):
    def test_expression_identifiers_are_not_coerced_to_numbers_or_missing(self) -> None:
        code = r'''
        namespace <- new.env(parent = globalenv())
        for (expression in as.list(parse(file = commandArgs(TRUE)[[1L]]))) {
          if (is.call(expression) && identical(expression[[1L]], as.name("<-")) &&
              is.call(expression[[3L]]) && identical(expression[[3L]][[1L]], as.name("function"))) {
            eval(expression, envir = namespace)
          }
        }
        for (ids in list(c("001", "002"), c("NA", "null"))) {
          path <- tempfile(fileext = ".tsv")
          writeLines(c("gene\t01\t02\t03\t04",
                       paste0(ids[[1L]], "\t1\t2\t3\t4"),
                       paste0(ids[[2L]], "\t5\t6\t7\t8")), path)
          value <- namespace$read_expression_tsv(path)
          stopifnot(identical(rownames(value), ids) || identical(colnames(value), ids))
          unlink(path)
        }
        '''
        for tool in ("clr", "infercsn", "kscreni", "lioness"):
            with self.subTest(tool=tool):
                result = subprocess.run(
                    ["Rscript", "-e", code, str(TOOLS / tool / "run_tool.R")],
                    capture_output=True, text=True, timeout=30, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
