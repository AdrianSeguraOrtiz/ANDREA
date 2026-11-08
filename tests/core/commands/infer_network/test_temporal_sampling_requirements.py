from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from andrea.core.commands.infer_network.commons.catalog import (
    _load_schema_constraints,
    _resolve_catalog_paths,
)
from andrea.core.commands.infer_network.commons.dataset import _parse_dataset_context
from andrea.core.commands.infer_network.commons.tools import (
    _build_runtime_input_snapshot,
    _check_tool_compatibility,
    _load_toolspec,
    _validate_requested_temporal_inputs,
)
from andrea.core.commands.infer_network.preflight import preflight_infer_network
from andrea.core.shared.bulk_temporal import validate_temporal_requirements


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "tests/fixtures/bulk_time_series/regular_longitudinal"


class TemporalSamplingRequirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        shutil.copytree(FIXTURE, self.directory, dirs_exist_ok=True)
        self.manifest = self.directory / "dataset-manifest.json"
        payload = json.loads(self.manifest.read_text())
        path = self.directory / "column_descriptors.tsv"
        with path.open() as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            headings = reader.fieldnames
            rows = [row for row in reader if row["column"].endswith("tech1")]
        with path.open("w") as handle:
            writer = csv.DictWriter(handle, fieldnames=headings, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        expression = self.directory / "expression.tsv"
        with expression.open() as handle:
            rows = list(csv.reader(handle, delimiter="\t"))
        indexes = [0] + [
            index for index, column in enumerate(rows[0]) if column.endswith("tech1")
        ]
        with expression.open("w") as handle:
            csv.writer(handle, delimiter="\t").writerows(
                [[row[index] for index in indexes] for row in rows]
            )
        payload["dataset"]["spec"]["expression"]["columns"] = len(indexes) - 1
        self.manifest.write_text(json.dumps(payload))
        self.tools_root, schemas = _resolve_catalog_paths()
        self.constraints = _load_schema_constraints(schemas)

    def change_coordinates(self, transform):
        path = self.directory / "column_descriptors.tsv"
        with path.open() as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            headings = reader.fieldnames
            rows = list(reader)
        for row in rows:
            row["timepoint"] = str(transform(row, float(row["timepoint"])))
        with path.open("w") as handle:
            writer = csv.DictWriter(handle, fieldnames=headings, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

    def preflight(self, **overrides):
        requirements = {
            "required": True,
            "sampling_designs": ["longitudinal"],
            "observation_types": ["time_series"],
            "technical_replicates": "reject",
            "min_timepoints_per_series": 3,
            "require_regular_sampling": True,
            "require_aligned_timepoints": True,
            **overrides,
        }
        tools_root = self.directory / "catalog"
        tool_directory = tools_root / "sampling_test"
        tool_directory.mkdir(parents=True, exist_ok=True)
        toolspec = _load_toolspec(self.tools_root, "dyngenie3")
        toolspec["id"] = "sampling_test"
        toolspec["temporal_requirements"] = requirements
        (tool_directory / "toolspec.json").write_text(json.dumps(toolspec))
        params = self.directory / "tools_params.json"
        params.write_text(
            json.dumps(
                {
                    "runs": [
                        {
                            "run_id": "sampling_test",
                            "tool_id": "sampling_test",
                            "params": {},
                            "execution": {"mode": "global"},
                        }
                    ]
                }
            )
        )
        _, schemas = _resolve_catalog_paths()
        with patch(
            "andrea.core.commands.infer_network.preflight._resolve_catalog_paths",
            return_value=(tools_root, schemas),
        ):
            return preflight_infer_network(
                dataset_manifest_path=self.manifest,
                tools_params_path=params,
            )

    def test_preflight_accepts_regular_aligned_observed_courses(self):
        self.assertEqual(self.preflight()["runs"]["selected"], ["sampling_test"])

    def test_preflight_blocks_irregular_sampling_instead_of_only_warning(self):
        self.change_coordinates(lambda row, time: 5 if time == 4 else time)
        report = self.preflight()
        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("regular sampling", report["runs"]["skipped"]["sampling_test"])
        self.assertEqual(
            self.preflight(require_regular_sampling=False)["runs"]["selected"],
            ["sampling_test"],
        )

    def test_preflight_blocks_shifted_regular_grid_between_series(self):
        self.change_coordinates(
            lambda row, time: time + 1 if row["series_id"] == "treated_r2" else time
        )
        report = self.preflight()
        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn(
            "identical observed timepoints", report["runs"]["skipped"]["sampling_test"]
        )
        self.assertIn("treated_r2", report["runs"]["skipped"]["sampling_test"])
        self.assertEqual(
            self.preflight(require_aligned_timepoints=False)["runs"]["selected"],
            ["sampling_test"],
        )

    def test_catalog_never_uses_temporal_requirements_to_filter_tools(self):
        dataset = _parse_dataset_context(
            dataset_manifest_path=self.manifest, constraints=self.constraints
        )
        toolspec = _load_toolspec(self.tools_root, "dyngenie3")
        toolspec["temporal_requirements"]["require_regular_sampling"] = True
        self.change_coordinates(lambda row, time: 5 if time == 4 else time)
        with patch(
            "andrea.core.shared.column_metadata.load_column_descriptors",
            side_effect=AssertionError("catalog must not inspect metadata"),
        ):
            self.assertTrue(
                _check_tool_compatibility(
                    tool_id="sampling_test",
                    toolspec=toolspec,
                    dataset=dataset,
                    constraints=self.constraints,
                    warnings=[],
                )[0]
            )
        summary, errors, _ = _validate_requested_temporal_inputs(
            toolspec=toolspec, dataset=dataset
        )
        self.assertIsNotNone(summary)
        self.assertTrue(any("regular sampling" in error for error in errors))

    def test_changing_sampling_contents_keeps_catalog_results_identical(self):
        before = self.preflight()
        self.change_coordinates(lambda row, time: 5 if time == 4 else time)
        after = self.preflight()
        self.assertEqual(before["catalog"], after["catalog"])
        self.assertEqual(before["runs"]["selected"], ["sampling_test"])
        self.assertEqual(after["runs"]["selected"], [])
        self.assertNotIn("temporal", before["dataset"])
        self.assertNotIn("temporal", before["input_validation"])
        self.assertIn("sampling_test", before["runs"]["temporal_validation"])

    def test_invalid_metadata_affects_only_requested_temporal_run(self):
        descriptor = self.directory / "column_descriptors.tsv"
        with descriptor.open() as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            headings, rows = reader.fieldnames, list(reader)
        rows[0]["sampling_design"] = "invalid_design"
        with descriptor.open("w") as handle:
            writer = csv.DictWriter(handle, fieldnames=headings, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        report = self.preflight()
        self.assertEqual(report["runs"]["selected"], [])
        self.assertEqual(report["catalog"]["blocked"], [])
        self.assertIn("sampling_design", report["runs"]["skipped"]["sampling_test"])
        catalog_only = preflight_infer_network(dataset_manifest_path=self.manifest)
        self.assertNotIn("temporal", catalog_only["input_validation"])

    def test_bulk_timepoints_without_metadata_can_use_generic_tool(self):
        payload = json.loads(self.manifest.read_text())
        payload["extras"] = {}
        self.manifest.write_text(json.dumps(payload))
        params = self.directory / "generic_params.json"
        params.write_text(
            json.dumps(
                {
                    "runs": [
                        {
                            "run_id": "generic",
                            "tool_id": "genie3",
                            "params": {},
                            "execution": {"mode": "global"},
                        }
                    ]
                }
            )
        )
        report = preflight_infer_network(
            dataset_manifest_path=self.manifest, tools_params_path=params
        )
        self.assertEqual(report["runs"]["selected"], ["generic"])
        self.assertEqual(report["runs"]["temporal_validation"], {})

    def test_unknown_extras_do_not_change_catalog_or_add_input_readers(self):
        before = preflight_infer_network(dataset_manifest_path=self.manifest)
        payload = json.loads(self.manifest.read_text())
        payload["extras"]["unregistered_input"] = "missing-file.tsv"
        self.manifest.write_text(json.dumps(payload))
        after = preflight_infer_network(dataset_manifest_path=self.manifest)
        self.assertEqual(before["catalog"], after["catalog"])
        self.assertNotIn("unregistered_input", after["dataset"]["extras"])

    def test_schema_rejects_nonboolean_flags_and_freezes_validated_requirements(self):
        requirements = {
            "required": True,
            "sampling_designs": ["longitudinal"],
            "observation_types": ["time_series"],
            "require_regular_sampling": True,
            "require_aligned_timepoints": True,
        }
        for field in ("require_regular_sampling", "require_aligned_timepoints"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "boolean"),
            ):
                validate_temporal_requirements({**requirements, field: "true"})
        toolspec = _load_toolspec(self.tools_root, "dyngenie3")
        toolspec["temporal_requirements"] = requirements
        frozen = _build_runtime_input_snapshot(
            run_id="sampling_test",
            catalog_tool_id="sampling_test",
            tool_origin="catalog",
            toolspec=toolspec,
            resolved_params={},
            resolved_execution={"mode": "global"},
            available_extra_inputs={"column_descriptors"},
        )
        requirements["require_regular_sampling"] = False
        self.assertTrue(frozen["temporal_requirements"]["require_regular_sampling"])


if __name__ == "__main__":
    unittest.main()
