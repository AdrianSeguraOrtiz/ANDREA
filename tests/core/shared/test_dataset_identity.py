from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from andrea.core.shared.dataset_identity import (
    fingerprint_dataset_content,
    validate_dataset_fingerprint,
)


class DatasetIdentityTests(unittest.TestCase):
    def test_fingerprint_is_path_independent_and_covers_expression_and_extras(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = Path(first_tmp)
            second = Path(second_tmp)
            for root in (first, second):
                (root / "expression.tsv").write_text(
                    "gene\tc1\nG1\t1\n", encoding="utf-8"
                )
                (root / "tf_list.txt").write_text("G1\n", encoding="utf-8")

            first_fingerprint = fingerprint_dataset_content(
                expression_path=first / "expression.tsv",
                extras={"tf_list": first / "tf_list.txt", "groups": None},
            )
            second_fingerprint = fingerprint_dataset_content(
                expression_path=second / "expression.tsv",
                extras={"tf_list": second / "tf_list.txt"},
            )
            self.assertEqual(first_fingerprint, second_fingerprint)

            (second / "tf_list.txt").write_text("G2\n", encoding="utf-8")
            changed = fingerprint_dataset_content(
                expression_path=second / "expression.tsv",
                extras={"tf_list": second / "tf_list.txt"},
            )
            self.assertNotEqual(first_fingerprint, changed)

    def test_validates_exact_sha256_contract(self) -> None:
        expected = {"algorithm": "sha256", "value": "a" * 64}
        self.assertEqual(
            validate_dataset_fingerprint(expected, label="fingerprint"),
            expected,
        )
        for invalid in (
            {"algorithm": "sha1", "value": "a" * 64},
            {"algorithm": "sha256", "value": "A" * 64},
            {"algorithm": "sha256", "value": "a" * 63},
            {**expected, "legacy": True},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_dataset_fingerprint(invalid, label="fingerprint")


if __name__ == "__main__":
    unittest.main()
