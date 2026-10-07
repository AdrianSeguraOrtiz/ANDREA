from __future__ import annotations

import ast
import csv
import math
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "wrappers/inference_tools/scripts/templates/python/_arboreto_common.py"


class ArboretoExpressionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Exercise the actual reader without importing Docker-only Dask/Arboreto.
        tree = ast.parse(HELPER.read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "read_expression_tsv"
        )
        namespace = {"pd": pd, "Path": Path, "csv": csv, "math": math}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(HELPER), "exec"), namespace)
        cls.read_expression = staticmethod(namespace["read_expression_tsv"])

    def read(self, text: str) -> pd.DataFrame:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expression.tsv"
            path.write_text(text, encoding="utf-8")
            return self.read_expression(path)

    def test_numeric_and_na_like_identifiers_round_trip(self) -> None:
        for identifiers in (("001", "002"), ("NA", "null")):
            with self.subTest(identifiers=identifiers):
                result = self.read(
                    f"gene\t01\t02\n{identifiers[0]}\t1\t2\n{identifiers[1]}\t3\t4\n"
                )
                self.assertEqual(result.columns.tolist(), list(identifiers))
                self.assertEqual(result.index.tolist(), ["01", "02"])
                self.assertEqual(result.to_numpy().tolist(), [[1, 3], [2, 4]])

    def test_duplicates_are_rejected_instead_of_dropping_measurements(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicated gene"):
            self.read("gene\ta\tb\nG1\t1\t2\nG1\t3\t4\n")
        with self.assertRaisesRegex(ValueError, "observation identifiers"):
            self.read("gene\ta\ta\nG1\t1\t2\nG2\t3\t4\n")

    def test_nonfinite_values_are_rejected_before_regression(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-finite"):
            self.read("gene\ta\tb\nG1\tinf\t2\nG2\t3\t4\n")


if __name__ == "__main__":
    unittest.main()
