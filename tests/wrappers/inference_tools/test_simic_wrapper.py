from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
SIMIC_TOOL_ROOT = REPO_ROOT / "wrappers" / "inference_tools" / "tools" / "simic"
PYTHON_TEMPLATE_ROOT = REPO_ROOT / "wrappers" / "inference_tools" / "scripts" / "templates" / "python"

if str(PYTHON_TEMPLATE_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_TEMPLATE_ROOT))

_spec = importlib.util.spec_from_file_location(
    "simic_run_tool", SIMIC_TOOL_ROOT / "run_tool.py"
)
assert _spec is not None and _spec.loader is not None
simic_run_tool = importlib.util.module_from_spec(_spec)
sys.modules["simic_run_tool"] = simic_run_tool
_spec.loader.exec_module(simic_run_tool)


class SimicWrapperInputTests(unittest.TestCase):
    def test_cell_phenotypes_rejects_groups_without_train_test_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cell_phenotypes.tsv"
            path.write_text(
                "cell\tphenotype\torder\n"
                "C1\tA\t0\n"
                "C2\tA\t0\n"
                "C3\tA\t0\n"
                "C4\tB\t1\n"
                "C5\tB\t1\n"
                "C6\tB\t1\n"
                "C7\tB\t1\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "at least four cells"):
                simic_run_tool._read_cell_phenotypes(
                    path,
                    cells=[f"C{i}" for i in range(1, 8)],
                )

    def test_cell_phenotypes_rejects_too_many_phenotypes_for_upstream_split(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cell_phenotypes.tsv"
            rows = ["cell\tphenotype\torder"]
            cells: list[str] = []
            for label_idx, label in enumerate(["A", "B", "C"]):
                for offset in range(4):
                    cell = f"C{label_idx}_{offset}"
                    cells.append(cell)
                    rows.append(f"{cell}\t{label}\t{label_idx}")
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "20% test split is too small"):
                simic_run_tool._read_cell_phenotypes(path, cells=cells)

    def test_stratified_split_preserves_per_phenotype_train_and_test_cells(
        self,
    ) -> None:
        counts = [27, 13, 13, 13, 13, 13, 4, 4]
        assignments = np.array(
            [label for label, count in enumerate(counts) for _ in range(count)]
        )
        df = pd.DataFrame(
            {
                "G1": np.arange(len(assignments), dtype=float),
                "G2": np.arange(len(assignments), dtype=float) + 1,
            }
        )
        np.random.seed(123)

        train_df, test_df, train_assignment, test_assignment = (
            simic_run_tool._stratified_split_df_and_assignment(df, assignments)
        )

        self.assertEqual(len(test_df), 20)
        self.assertEqual(len(train_df), 80)
        for label in sorted(set(assignments)):
            self.assertGreaterEqual(int(np.sum(train_assignment == label)), 2)
            self.assertGreaterEqual(int(np.sum(test_assignment == label)), 2)


if __name__ == "__main__":
    unittest.main()
