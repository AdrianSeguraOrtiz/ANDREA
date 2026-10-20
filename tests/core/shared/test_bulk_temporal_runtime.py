from __future__ import annotations

import hashlib
import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from andrea.core.commands.infer_network.commons.artifacts import (
    _deserialize_dataset_context,
    _materialize_frozen_inputs,
    _serialize_dataset_context,
)
from andrea.core.commands.infer_network.commons.catalog import (
    _load_schema_constraints,
    _resolve_catalog_paths,
)
from andrea.core.commands.infer_network.commons.dataset import (
    _parse_dataset_context,
    _validate_dataset_inputs_by_specs,
)
from andrea.core.commands.infer_network.commons.tools import (
    _check_tool_compatibility,
    _load_toolspec,
    _runtime_execution_for_logical_run,
    _build_runtime_input_snapshot,
    _resolve_run_execution,
)
from andrea.core.shared.bulk_temporal import (
    validate_bulk_temporal_inputs,
    validate_temporal_declaration,
)
from andrea.core.shared.dataset_identity import fingerprint_dataset_content
from andrea.core.shared.input_specs import load_input_specs
from andrea.core.commands.infer_network.preflight import preflight_infer_network
from andrea.core.commands.generate_data.pipeline import _dataset_manifest_payload

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "bulk_time_series" / "regular_longitudinal"


class BulkTemporalRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        shutil.copytree(FIXTURE, self.directory, dirs_exist_ok=True)
        audit = json.loads((self.directory / "temporal-contract.json").read_text())
        self.temporal = {
            key: value
            for key, value in audit.items()
            if key
            not in {
                "status",
                "expression_matrix",
                "timepoints",
                "replicates",
                "network_semantics",
            }
        }
        self.temporal["schema_version"] = "bulk-temporal/1"
        self.manifest = self.directory / "dataset-manifest.json"
        self.tools_root, schemas = _resolve_catalog_paths()
        self.constraints = _load_schema_constraints(schemas)

    def write_runtime_manifest(self) -> None:
        payload = json.loads(self.manifest.read_text())
        payload["temporal"] = self.temporal
        self.manifest.write_text(json.dumps(payload))

    def parse(self):
        return _parse_dataset_context(
            dataset_manifest_path=self.manifest,
            constraints=self.constraints,
        )

    def test_manifest_semantics_serialize_and_frozen_inputs_preserve_temporal(
        self,
    ) -> None:
        self.write_runtime_manifest()
        dataset = self.parse()
        report, errors, _ = _validate_dataset_inputs_by_specs(
            dataset=dataset,
            input_specs=load_input_specs(),
        )
        self.assertEqual(errors, [])
        self.assertTrue(report["temporal"]["runtime_support"])
        self.assertEqual(len(report["temporal"]["series"]), 4)
        self.assertEqual(
            _deserialize_dataset_context(_serialize_dataset_context(dataset)), dataset
        )

        params = self.directory / "params.json"
        params.write_text('{"runs": []}')
        frozen_manifest, _, _, _, frozen_extras = _materialize_frozen_inputs(
            run_dir=self.directory / "run",
            dataset_manifest_path=self.manifest,
            tools_params_path=params,
            dataset=dataset,
            constraints=self.constraints,
        )
        frozen = self.parse_from(frozen_manifest)
        self.assertEqual(frozen.temporal, self.temporal)
        for key in ("timepoints", "replicates"):
            self.assertEqual(
                frozen_extras[key].read_bytes(), dataset.extras[key].read_bytes()
            )

    def parse_from(self, manifest: Path):
        return _parse_dataset_context(
            dataset_manifest_path=manifest, constraints=self.constraints
        )

    def test_temporal_requires_enriched_exact_metadata_and_declared_unit(self) -> None:
        self.write_runtime_manifest()
        timepoints = self.directory / "timepoints.tsv"
        timepoints.write_text(timepoints.read_text().replace("hour", "minute"))
        _, errors, _ = _validate_dataset_inputs_by_specs(
            dataset=self.parse(), input_specs=load_input_specs()
        )
        self.assertTrue(any("time_unit" in error for error in errors))
        with self.assertRaisesRegex(ValueError, "extras.replicates"):
            validate_bulk_temporal_inputs(
                temporal=self.temporal,
                expression_path=self.directory / "expression.tsv",
                timepoints_path=timepoints,
                replicates_path=None,
            )

    def test_missing_object_preserves_legacy_parse_and_fingerprint(self) -> None:
        dataset = self.parse()
        self.assertIsNone(dataset.temporal)
        self.assertNotIn("temporal", _serialize_dataset_context(dataset))
        components = {
            "expression": hashlib.sha256(
                dataset.expression_matrix_path.read_bytes()
            ).hexdigest()
        }
        for key, path in sorted(dataset.extras.items()):
            if path is not None:
                components[f"extra:{key}"] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        expected = hashlib.sha256(
            json.dumps(
                components, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("ascii")
        ).hexdigest()
        self.assertEqual(
            fingerprint_dataset_content(
                expression_path=dataset.expression_matrix_path, extras=dataset.extras
            )["value"],
            expected,
        )

    def test_identity_covers_declarations_and_ignores_json_object_key_order(
        self,
    ) -> None:
        dataset = self.parse()

        def identity(temporal):
            return fingerprint_dataset_content(
                expression_path=dataset.expression_matrix_path,
                extras=dataset.extras,
                temporal=temporal,
            )

        original = identity(self.temporal)
        self.assertEqual(
            original, identity(dict(reversed(list(self.temporal.items()))))
        )
        self.assertNotEqual(
            original, identity({**self.temporal, "time_unit": "minute"})
        )
        self.assertNotEqual(original, identity(None))

    def test_declarations_reject_null_unknown_pseudotime_and_missing_provenance(
        self,
    ) -> None:
        for invalid in (
            None,
            {**self.temporal, "time_basis": "pseudotime"},
            {**self.temporal, "hidden_sidecar": "other.json"},
            {**self.temporal, "provenance": "  "},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_temporal_declaration(invalid)

    def test_generic_tool_requirement_blocks_legacy_and_other_sampling_design(
        self,
    ) -> None:
        toolspec = _load_toolspec(self.tools_root, "genie3")
        toolspec["temporal_requirements"] = {
            "required": True,
            "sampling_designs": ["longitudinal"],
        }

        def compatible(dataset):
            return _check_tool_compatibility(
                tool_id="example",
                toolspec=toolspec,
                dataset=dataset,
                constraints=self.constraints,
                warnings=[],
            )

        _, reasons = compatible(self.parse())
        self.assertTrue(
            any("temporal metadata missing" in reason for reason in reasons)
        )
        self.write_runtime_manifest()
        self.assertTrue(compatible(self.parse())[0])
        self.temporal["sampling_design"] = "independent_samples"
        self.write_runtime_manifest()
        _, reasons = compatible(self.parse())
        self.assertTrue(any("sampling_design" in reason for reason in reasons))
        toolspec["temporal_requirements"]["sampling_designs"] = [{}]
        self.assertFalse(compatible(self.parse())[0])

    def test_physical_execution_receives_exact_temporal_object_and_legacy_stays_same(
        self,
    ) -> None:
        self.assertEqual(
            _runtime_execution_for_logical_run({"mode": "global"}), {"mode": "global"}
        )
        self.assertEqual(
            _runtime_execution_for_logical_run(
                {"mode": "global"}, temporal=self.temporal
            ),
            {"mode": "global", "temporal": self.temporal},
        )

    def test_frozen_runtime_contract_detects_temporal_requirement_drift(self) -> None:
        toolspec = _load_toolspec(self.tools_root, "genie3")

        def snapshot():
            return _build_runtime_input_snapshot(
                run_id="example",
                catalog_tool_id="example",
                tool_origin="catalog",
                toolspec=toolspec,
                resolved_params={},
                resolved_execution={"mode": "global"},
                available_extra_inputs=set(),
            )

        self.assertNotIn("temporal_requirements", snapshot())
        toolspec["temporal_requirements"] = {
            "required": True,
            "sampling_designs": ["longitudinal"],
            "min_timepoints_per_series": 2,
        }
        frozen = snapshot()
        toolspec["temporal_requirements"]["min_timepoints_per_series"] = 3
        self.assertEqual(
            frozen["temporal_requirements"]["min_timepoints_per_series"], 2
        )
        self.assertNotEqual(frozen, snapshot())

    def test_temporal_tools_block_group_emulation_until_metadata_slicing_exists(
        self,
    ) -> None:
        toolspec = _load_toolspec(self.tools_root, "genie3")
        toolspec["execution_capabilities"] = ["global", "group_emulated"]

        def execution():
            return _resolve_run_execution(
                run_id="example",
                toolspec=toolspec,
                user_execution={"mode": "group_emulated"},
                warnings=[],
            )

        self.assertTrue(execution()[0])
        toolspec["temporal_requirements"] = {
            "required": True,
            "sampling_designs": ["longitudinal"],
        }
        ok, _, errors = execution()
        self.assertFalse(ok)
        self.assertTrue(any("metadata slicing" in error for error in errors))

    def preflight_temporal_tool(self, **overrides):
        requirements = {
            "required": True,
            "sampling_designs": ["longitudinal"],
            "technical_replicates": "reject",
            "min_timepoints_per_series": 2,
            **overrides,
        }
        custom_path = self.directory / "custom_tools.json"
        custom_path.write_text(
            json.dumps(
                {
                    "tools": [
                        {
                            "run_id": "temporal_test",
                            "name": "Temporal test",
                            "docker_image": "example/temporal:1",
                            "execution_mode": "global",
                            "extra_inputs": ["timepoints", "replicates"],
                            "outputs": {"directed": True, "sign": "none"},
                            "runtime_resources": {
                                "threading": {
                                    "supported": False,
                                    "default_threads": 1,
                                    "max_threads": 1,
                                    "upstream_mapping": "single thread",
                                }
                            },
                            "temporal_requirements": requirements,
                        }
                    ]
                }
            )
        )
        params_path = self.directory / "tools_params.json"
        params_path.write_text(
            json.dumps(
                {
                    "runs": [
                        {
                            "run_id": "temporal_test",
                            "tool_id": "custom_temporal_test",
                            "params": {},
                            "execution": {"mode": "global"},
                        }
                    ]
                }
            )
        )
        return preflight_infer_network(
            dataset_manifest_path=self.manifest,
            tools_params_path=params_path,
            custom_tools_path=custom_path,
        )

    def test_preflight_rejects_technical_measurements_for_restrictive_tools(
        self,
    ) -> None:
        self.write_runtime_manifest()
        report = self.preflight_temporal_tool()
        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn(
            "technical replicates", report["runs"]["skipped"]["temporal_test"]
        )
        entry = next(
            item
            for item in report["catalog"]["blocked"]
            if item["tool_id"] == "custom_temporal_test"
        )
        self.assertTrue(
            any("technical replicates" in issue["message"] for issue in entry["issues"])
        )
        self.assertEqual(
            self.preflight_temporal_tool(technical_replicates="allow")["runs"][
                "selected"
            ],
            ["temporal_test"],
        )

    def test_preflight_accepts_two_timepoints_and_enforces_larger_declared_minimum(
        self,
    ) -> None:
        self.write_runtime_manifest()
        with (self.directory / "timepoints.tsv").open() as handle:
            times = list(csv.DictReader(handle, delimiter="\t"))
        keep = {
            row["column"]
            for row in times
            if row["timepoint"] in {"0", "2"} and row["column"].endswith("tech1")
        }
        for filename in ("timepoints.tsv", "replicates.tsv"):
            path = self.directory / filename
            with path.open() as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                headings = reader.fieldnames
                rows = [row for row in reader if row["column"] in keep]
            with path.open("w") as handle:
                writer = csv.DictWriter(handle, fieldnames=headings, delimiter="\t")
                writer.writeheader()
                writer.writerows(rows)
        expression = self.directory / "expression.tsv"
        with expression.open() as handle:
            rows = list(csv.reader(handle, delimiter="\t"))
        indexes = [0] + [
            index for index, column in enumerate(rows[0]) if column in keep
        ]
        with expression.open("w") as handle:
            csv.writer(handle, delimiter="\t").writerows(
                [[row[index] for index in indexes] for row in rows]
            )
        payload = json.loads(self.manifest.read_text())
        payload["dataset"]["spec"]["expression"]["columns"] = len(keep)
        self.manifest.write_text(json.dumps(payload))
        self.assertEqual(
            self.preflight_temporal_tool()["runs"]["selected"], ["temporal_test"]
        )
        report = self.preflight_temporal_tool(min_timepoints_per_series=3)
        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn(
            "at least 3 distinct timepoints", report["runs"]["skipped"]["temporal_test"]
        )

    def test_simulation_to_inference_handoff_preserves_optional_temporal_object(
        self,
    ) -> None:
        request = SimpleNamespace(
            data_axes={
                "measurement": "rna_expression",
                "resolution": "bulk",
                "column_kind": "timepoints",
                "experimental_design": "time_series",
            },
            organism={"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
            notes=None,
        )
        simulator_manifest = {
            "expression": {"genes": 3, "columns": 24},
            "extras": {
                "tf_list": "extras/tf_list.txt",
                "timepoints": "extras/timepoints.tsv",
                "replicates": "extras/replicates.tsv",
            },
            "temporal": self.temporal,
        }
        payload = _dataset_manifest_payload(
            dataset_id="example", request=request, simulator_manifest=simulator_manifest
        )
        self.assertEqual(payload["temporal"], self.temporal)
        simulator_manifest.pop("temporal")
        self.assertNotIn(
            "temporal",
            _dataset_manifest_payload(
                dataset_id="example",
                request=request,
                simulator_manifest=simulator_manifest,
            ),
        )

    def test_manifest_and_smoketest_schemas_share_canonical_temporal_shape(
        self,
    ) -> None:
        inference = json.loads(
            (
                ROOT
                / "andrea/catalog_inference_tools/schemas/dataset-manifest.schema.json"
            ).read_text()
        )["properties"]["temporal"]
        simulator = json.loads(
            (
                ROOT
                / "andrea/catalog_simulation_data_tools/schemas/simulator-output-manifest.schema.json"
            ).read_text()
        )["properties"]["temporal"]
        smoke = json.loads(
            (
                ROOT
                / "wrappers/inference_tools/tests/schemas/smoketest.config.schema.json"
            ).read_text()
        )
        self.assertEqual(inference, simulator)
        self.assertEqual(
            inference, smoke["properties"]["execution"]["properties"]["temporal"]
        )
        self.assertEqual(
            inference,
            smoke["properties"]["variants"]["items"]["properties"]["execution"][
                "properties"
            ]["temporal"],
        )


if __name__ == "__main__":
    unittest.main()
