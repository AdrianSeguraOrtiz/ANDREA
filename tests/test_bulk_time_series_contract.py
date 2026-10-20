from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable

import jsonschema

from andrea.core.shared.input_specs import load_input_specs
from andrea.core.shared.input_validation import validate_tsv_file_with_spec

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_bulk_time_series import validate_bulk_temporal_profile  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "bulk_time_series"


class BulkTemporalContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "fixture"
        self.copy_fixture("regular_longitudinal")

    def copy_fixture(self, name: str) -> None:
        shutil.copytree(FIXTURES / name, self.directory, dirs_exist_ok=True)
        self.profile = self.directory / "temporal-contract.json"

    def change_profile(self, **updates: object) -> None:
        profile = json.loads(self.profile.read_text())
        profile.update(updates)
        self.profile.write_text(json.dumps(profile), encoding="utf-8")

    def edit_rows(
        self, name: str, edit: Callable[[list[dict[str, str]]], object]
    ) -> None:
        path = self.directory / name
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            header = reader.fieldnames
            rows = list(reader)
        edit(rows)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=header, delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)

    def test_longitudinal_adjacency_stays_inside_series_and_deduplicates_technical_replicates(
        self,
    ) -> None:
        report = validate_bulk_temporal_profile(self.profile)
        self.assertEqual(
            (report["genes"], report["columns"], report["observations"]), (3, 24, 12)
        )
        self.assertEqual(len(report["series"]), 4)
        self.assertEqual(len(report["longitudinal_transitions"]), 8)
        self.assertFalse(report["runtime_support"])
        for transition in report["longitudinal_transitions"]:
            self.assertTrue(
                transition["from"].startswith(transition["series_id"] + "_t")
            )
            self.assertTrue(transition["to"].startswith(transition["series_id"] + "_t"))
            self.assertEqual(transition["elapsed_time"], 2)
        self.assertTrue(all(series["regular"] for series in report["series"]))

    def test_irregular_independent_samples_join_by_identifier_without_longitudinal_transitions(
        self,
    ) -> None:
        self.copy_fixture("irregular_independent")
        report = validate_bulk_temporal_profile(self.profile)
        self.assertEqual((report["columns"], report["observations"]), (12, 12))
        self.assertFalse(report["metadata_order_matches_expression"])
        self.assertEqual(report["longitudinal_transitions"], [])
        self.assertEqual(
            [row["intervals"] for row in report["series"]], [[1, 3], [2, 3]]
        )
        self.assertTrue(all(not row["regular"] for row in report["series"]))
        self.assertEqual(report, validate_bulk_temporal_profile(self.profile))

    def test_companion_runtime_manifests_match_current_schema(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "andrea/catalog_inference_tools/schemas/dataset-manifest.schema.json"
            ).read_text()
        )
        for name in ("regular_longitudinal", "irregular_independent"):
            with self.subTest(name=name):
                manifest = json.loads(
                    (FIXTURES / name / "dataset-manifest.json").read_text()
                )
                jsonschema.Draft202012Validator(schema).validate(manifest)
                self.assertNotIn("temporal-contract", manifest["extras"])

    def test_current_timepoint_contract_accepts_blank_coordinate_but_temporal_audit_rejects_it(
        self,
    ) -> None:
        self.edit_rows("timepoints.tsv", lambda rows: rows[0].update(timepoint=""))
        with (self.directory / "timepoints.tsv").open() as handle:
            columns = {row["column"] for row in csv.DictReader(handle, delimiter="\t")}
        result = validate_tsv_file_with_spec(
            key="timepoints",
            path=self.directory / "timepoints.tsv",
            spec=load_input_specs()["timepoints"],
            expression_genes={"G1", "G2", "G3"},
            expression_columns=columns,
            expression_columns_count=len(columns),
            unknown_cross_check="error",
        )
        self.assertEqual(result.status, "ok")
        with self.assertRaisesRegex(ValueError, "missing temporal metadata"):
            validate_bulk_temporal_profile(self.profile)

    def test_metadata_must_cover_expression_columns_exactly(self) -> None:
        for mutation in (
            lambda rows: rows.pop(),
            lambda rows: rows[0].update(column="unmapped"),
            lambda rows: rows[0].update(column=rows[1]["column"]),
        ):
            with self.subTest(mutation=mutation):
                self.copy_fixture("regular_longitudinal")
                self.edit_rows("timepoints.tsv", mutation)
                with self.assertRaises(ValueError):
                    validate_bulk_temporal_profile(self.profile)

    def test_nonfinite_or_missing_expression_is_rejected(self) -> None:
        for value in ("nan", "inf", ""):
            with self.subTest(value=value):
                self.copy_fixture("regular_longitudinal")
                self.edit_rows(
                    "expression.tsv",
                    lambda rows: rows[0].update({"control_r1_t0_tech1": value}),
                )
                with self.assertRaisesRegex(ValueError, "numeric"):
                    validate_bulk_temporal_profile(self.profile)

    def test_duplicate_expression_header_is_rejected(self) -> None:
        path = self.directory / "expression.tsv"
        path.write_text(
            path.read_text().replace("control_r1_t0_tech2", "control_r1_t0_tech1"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "duplicate header"):
            validate_bulk_temporal_profile(self.profile)

    def test_condition_changes_cannot_be_hidden_inside_series(self) -> None:
        self.edit_rows(
            "timepoints.tsv",
            lambda rows: [row.update(condition="treated") for row in rows[2:4]],
        )
        with self.assertRaisesRegex(ValueError, "condition must be constant"):
            validate_bulk_temporal_profile(self.profile)

    def test_units_must_match_declaration(self) -> None:
        self.edit_rows(
            "timepoints.tsv", lambda rows: rows[0].update(time_unit="minute")
        )
        with self.assertRaisesRegex(ValueError, "time_unit"):
            validate_bulk_temporal_profile(self.profile)

    def test_pseudotime_cannot_replace_observed_time(self) -> None:
        self.change_profile(time_basis="pseudotime")
        with self.assertRaisesRegex(ValueError, "pseudotime is distinct"):
            validate_bulk_temporal_profile(self.profile)

    def test_reusing_observation_across_times_is_rejected(self) -> None:
        self.edit_rows(
            "timepoints.tsv",
            lambda rows: rows[2].update(observation_id=rows[0]["observation_id"]),
        )
        with self.assertRaisesRegex(ValueError, "observation_id cannot cross"):
            validate_bulk_temporal_profile(self.profile)

    def test_duplicate_technical_label_cannot_inflate_observations(self) -> None:
        self.edit_rows(
            "replicates.tsv",
            lambda rows: rows[1].update(
                technical_replicate=rows[0]["technical_replicate"]
            ),
        )
        with self.assertRaisesRegex(ValueError, "duplicate technical_replicate"):
            validate_bulk_temporal_profile(self.profile)

    def test_independent_samples_cannot_be_reclassified_as_longitudinal(self) -> None:
        self.copy_fixture("irregular_independent")
        self.change_profile(sampling_design="longitudinal")
        with self.assertRaisesRegex(
            ValueError, "longitudinal series must identify one biological replicate"
        ):
            validate_bulk_temporal_profile(self.profile)

    def test_independent_biological_replicate_cannot_span_timepoints(self) -> None:
        self.copy_fixture("irregular_independent")
        self.edit_rows(
            "replicates.tsv",
            lambda rows: rows[2].update(replicate=rows[0]["replicate"]),
        )
        with self.assertRaisesRegex(ValueError, "cannot span timepoints"):
            validate_bulk_temporal_profile(self.profile)

    def test_longitudinal_series_requires_stable_biological_replicate(self) -> None:
        self.edit_rows(
            "replicates.tsv",
            lambda rows: [row.update(replicate="different") for row in rows[2:4]],
        )
        with self.assertRaisesRegex(ValueError, "one biological replicate"):
            validate_bulk_temporal_profile(self.profile)

    def test_elapsed_time_lag_requires_same_units(self) -> None:
        profile = json.loads(self.profile.read_text())
        profile["network_semantics"]["lag"] = {
            "kind": "elapsed_time",
            "values": [1],
            "unit": "minute",
        }
        self.change_profile(network_semantics=profile["network_semantics"])
        with self.assertRaisesRegex(ValueError, "lag unit must match"):
            validate_bulk_temporal_profile(self.profile)

    def test_nonfinite_intervals_are_rejected_even_when_coordinates_are_finite(
        self,
    ) -> None:
        self.edit_rows(
            "timepoints.tsv",
            lambda rows: [
                row.update(timepoint="-1e308" if row["timepoint"] == "0" else "1e308")
                for row in rows
            ],
        )
        with self.assertRaisesRegex(
            ValueError,
            "intervals must be finite",
        ):
            validate_bulk_temporal_profile(self.profile)

    def test_cli_returns_machine_readable_failure_and_nonzero_exit(self) -> None:
        self.change_profile(time_basis="pseudotime")
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/validate_bulk_time_series.py"),
                str(self.profile),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 1)
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "error")
        self.assertIn("pseudotime is distinct", report["errors"][0])


if __name__ == "__main__":
    unittest.main()
