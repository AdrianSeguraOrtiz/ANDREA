from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "wrappers" / "inference_tools" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from shared.benchmark_inputs import (  # noqa: E402
    EXTRA_FILENAMES,
    GENERATED_EXTRA_INPUTS,
    BenchmarkInputProfile,
    BenchmarkInputSize,
    write_benchmark_io_dir,
)

INPUT_SPECS_ROOT = REPO_ROOT / "andrea" / "catalog_inference_tools" / "input_specs"


def _digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        rows = [{str(k): str(v) for k, v in row.items()} for row in reader]
    return list(reader.fieldnames or []), rows


def _read_expression(path: Path) -> tuple[set[str], set[str]]:
    header, rows = _read_tsv(path)
    return {row[header[0]] for row in rows}, set(header[1:])


def _assert_type(testcase: unittest.TestCase, value: str, expected: str) -> None:
    if value == "":
        return
    if expected == "string":
        return
    if expected == "int":
        int(value)
        return
    if expected == "float":
        float(value)
        return
    if expected == "bool":
        testcase.assertIn(value.lower(), {"true", "false", "1", "0"})
        return
    testcase.fail(f"Unsupported input spec column type in test: {expected}")


def _column_values(rows: list[dict[str, str]], column: str) -> set[str]:
    return {row[column] for row in rows if column in row and row[column] != ""}


def _load_specs() -> dict[str, dict[str, Any]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in INPUT_SPECS_ROOT.glob("*.json")
    }


def _assert_file_matches_input_spec(
    testcase: unittest.TestCase,
    *,
    input_key: str,
    path: Path,
    spec: dict[str, Any],
    expression_genes: set[str],
    expression_columns: set[str],
    generated_paths: dict[str, Path],
) -> None:
    file_kind = spec["file_kind"]
    if file_kind == "txt_list":
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        testcase.assertGreaterEqual(len(lines), int(spec.get("min_rows", 1)))
        for check in spec.get("cross_checks", []):
            if check.get("kind") == "line_subset_expression_genes":
                testcase.assertTrue(set(lines).issubset(expression_genes))
        return

    testcase.assertEqual(file_kind, "tsv")
    header, rows = _read_tsv(path)
    testcase.assertGreaterEqual(len(rows), int(spec.get("min_rows", 1)), input_key)
    testcase.assertGreaterEqual(
        len(header),
        int(spec.get("min_columns", len(spec.get("required_columns", [])) or 1)),
        input_key,
    )
    for column in spec.get("required_columns", []):
        testcase.assertIn(column, header, input_key)
    if spec.get("unique_first_column"):
        first_column = header[0]
        values = [row[first_column] for row in rows]
        testcase.assertEqual(len(values), len(set(values)), input_key)

    for column, expected_type in spec.get("column_types", {}).items():
        if column not in header:
            continue
        for row in rows:
            _assert_type(testcase, row[column], str(expected_type))
    if spec.get("data_columns_type") == "float":
        for row in rows:
            for column in header[1:]:
                float(row[column])

    first_column_values = {row[header[0]] for row in rows}
    first_column_role = spec.get("first_column_role")
    if first_column_role == "expression_column_id":
        testcase.assertTrue(first_column_values.issubset(expression_columns), input_key)
    elif first_column_role == "gene_id":
        testcase.assertTrue(first_column_values.issubset(expression_genes), input_key)

    parsed_extras: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
    for other_key, other_path in generated_paths.items():
        other_spec = _load_specs().get(other_key, {})
        if other_spec.get("file_kind") == "tsv":
            parsed_extras[other_key] = _read_tsv(other_path)

    for check in spec.get("cross_checks", []):
        kind = check.get("kind")
        if kind == "first_column_subset_expression_columns":
            testcase.assertTrue(first_column_values.issubset(expression_columns))
        elif kind == "data_columns_subset_expression_columns":
            testcase.assertTrue(set(header[1:]).issubset(expression_columns))
        elif kind == "data_columns_match_expression_columns":
            testcase.assertEqual(set(header[1:]), expression_columns)
        elif kind == "row_count_matches_expression_columns":
            testcase.assertEqual(len(rows), len(expression_columns), input_key)
        elif kind == "column_subset_expression_genes":
            values = _column_values(rows, str(check["column"]))
            testcase.assertTrue(values.issubset(expression_genes), input_key)
        elif kind == "column_subset_extra_column":
            other_key = str(check["other_input"])
            other_column = str(check["other_column"])
            _other_header, other_rows = parsed_extras[other_key]
            values = _column_values(rows, str(check["column"]))
            other_values = _column_values(other_rows, other_column)
            testcase.assertTrue(values.issubset(other_values), input_key)


class BenchmarkInputGeneratorTest(unittest.TestCase):
    def test_generates_all_known_inputs_deterministically(self) -> None:
        profile = BenchmarkInputProfile(
            seed=99,
            column_kind="cells",
            extras_provided=tuple(sorted(GENERATED_EXTRA_INPUTS)),
            group_count=3,
            prior_density=0.1,
        )
        size = BenchmarkInputSize(genes=16, columns=12)

        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            first_bundle = write_benchmark_io_dir(Path(first), size, profile)
            second_bundle = write_benchmark_io_dir(Path(second), size, profile)

            self.assertEqual(_digest_tree(Path(first)), _digest_tree(Path(second)))
            self.assertEqual(set(first_bundle.extras), GENERATED_EXTRA_INPUTS)
            self.assertEqual(set(second_bundle.extras), GENERATED_EXTRA_INPUTS)
            for input_key, filename in EXTRA_FILENAMES.items():
                self.assertTrue((Path(first) / "extra" / filename).is_file(), input_key)

    def test_generated_inputs_match_input_specs_and_expression_universe(self) -> None:
        specs = _load_specs()
        profile = BenchmarkInputProfile(
            seed=99,
            column_kind="cells",
            extras_provided=tuple(sorted(GENERATED_EXTRA_INPUTS)),
            group_count=3,
            prior_density=0.1,
        )

        with tempfile.TemporaryDirectory() as tmp:
            bundle = write_benchmark_io_dir(
                Path(tmp),
                BenchmarkInputSize(genes=16, columns=12),
                profile,
            )
            expression_genes, expression_columns = _read_expression(
                bundle.expression_path
            )
            _assert_file_matches_input_spec(
                self,
                input_key="expression_matrix",
                path=bundle.expression_path,
                spec=specs["expression_matrix"],
                expression_genes=expression_genes,
                expression_columns=expression_columns,
                generated_paths=bundle.extras,
            )
            for input_key, path in bundle.extras.items():
                with self.subTest(input_key=input_key):
                    _assert_file_matches_input_spec(
                        self,
                        input_key=input_key,
                        path=path,
                        spec=specs[input_key],
                        expression_genes=expression_genes,
                        expression_columns=expression_columns,
                        generated_paths=bundle.extras,
                    )

            self.assertEqual(
                expression_columns,
                {column for column, _ in assign_rows(bundle.extras["groups"])},
            )

    def test_rejects_grouped_inputs_without_groups(self) -> None:
        profile = BenchmarkInputProfile(
            extras_provided=("column_phenotypes",),
            group_count=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Grouped synthetic inputs"):
                write_benchmark_io_dir(
                    Path(tmp),
                    BenchmarkInputSize(genes=8, columns=4),
                    profile,
                )


def assign_rows(path: Path) -> list[tuple[str, str]]:
    header, rows = _read_tsv(path)
    first_col = header[0]
    return [(row[first_col], row["cluster"]) for row in rows]


if __name__ == "__main__":
    unittest.main()
