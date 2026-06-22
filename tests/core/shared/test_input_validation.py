from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from andrea.core.shared.input_validation import validate_tsv_file_with_spec


class InputValidationTests(unittest.TestCase):
    def test_unique_first_column_applies_to_generic_ids(self) -> None:
        spec = {
            "file_kind": "tsv",
            "delimiter": "\t",
            "header": True,
            "first_column_role": "none",
            "unique_first_column": True,
            "min_rows": 1,
            "min_columns": 2,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "regions.tsv"
            path.write_text("region\tvalue\nregion_1\t1\nregion_1\t2\n", encoding="utf-8")

            result = validate_tsv_file_with_spec(
                key="chromatin_regions",
                path=path,
                spec=spec,
                expression_genes={"G1"},
                expression_columns={"cell_1"},
                expression_columns_count=1,
                unknown_cross_check="error",
            )

        self.assertEqual(result.status, "error")
        self.assertTrue(
            any("duplicated identifier 'region_1'" in error for error in result.errors),
            result.errors,
        )

    def test_data_columns_match_expression_columns_requires_exact_set(self) -> None:
        spec = {
            "file_kind": "tsv",
            "delimiter": "\t",
            "header": True,
            "first_column_role": "none",
            "min_rows": 1,
            "min_columns": 2,
            "data_columns_type": "float",
            "cross_checks": [{"kind": "data_columns_match_expression_columns"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chromatin_accessibility.tsv"
            path.write_text("region\tcell_1\tcell_extra\nregion_1\t1\t2\n", encoding="utf-8")

            result = validate_tsv_file_with_spec(
                key="chromatin_accessibility",
                path=path,
                spec=spec,
                expression_genes={"G1"},
                expression_columns={"cell_1", "cell_2"},
                expression_columns_count=2,
                unknown_cross_check="error",
            )

        self.assertEqual(result.status, "error")
        self.assertTrue(
            any("data column headers must match expression columns" in error for error in result.errors),
            result.errors,
        )


if __name__ == "__main__":
    unittest.main()
