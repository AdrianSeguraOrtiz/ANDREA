from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator

from andrea.core.commands.generate_data.output_validation import (
    validate_simulator_output_package,
)
from andrea.core.commands.generate_data.pipeline import _dataset_manifest_payload
from andrea.core.commands.infer_network.commons.catalog import (
    _load_schema_constraints,
    _resolve_catalog_paths,
)
from andrea.core.commands.infer_network.commons.dataset import (
    _parse_dataset_context,
    _validate_dataset_inputs_by_specs,
)
from andrea.core.shared.input_specs import load_input_specs

ROOT = Path(__file__).resolve().parents[4]
SCHEMAS = ROOT / "andrea/catalog_simulation_data_tools/schemas"


class CombinedObservationHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        (self.directory / "extras").mkdir()
        (self.directory / "truth").mkdir()
        (self.directory / "extras/tf_list.txt").write_text("G1\nG2\nG3\n")
        self.expression = self.directory / "expression.tsv"
        self.expression_text = "gene\tt0\tt1\tt2\tss1\tss2\nG1\t1\t2\t3\t2\t3\nG2\t2\t3\t4\t3\t4\nG3\t3\t4\t5\t4\t5\n"
        self.expression.write_text(self.expression_text)
        self.descriptors = self.directory / "extras/column_descriptors.tsv"
        self.metadata_text = (
            "column\tobservation_type\tsampling_design\tseries_id\tcondition\treplicate\tobservation_id\ttechnical_replicate\ttimepoint\ttime_unit\n"
            "t0\ttime_series\tlongitudinal\tseries_1\tcontrol\tr1\tvisit0\t1\t0\tmodel_time\n"
            "t1\ttime_series\tlongitudinal\tseries_1\tcontrol\tr1\tvisit1\t1\t1\tmodel_time\n"
            "t2\ttime_series\tlongitudinal\tseries_1\tcontrol\tr1\tvisit2\t1\t2\tmodel_time\n"
            "ss1\tsteady_state\t\t\tcontrol\tss_r1\tsteady1\t1\t\t\n"
            "ss2\tsteady_state\t\t\tcontrol\tss_r2\tsteady2\t1\t\t\n"
        )
        self.descriptors.write_text(self.metadata_text)
        (self.directory / "truth/gene_universe.txt").write_text("G1\nG2\nG3\n")
        (self.directory / "truth/networks.csv").write_text(
            "source,target,score,sign,evidence,context\nG1,G2,1,?,simulated_truth,global\n"
        )
        self.axes = {
            "measurement": "rna_expression",
            "resolution": "bulk",
            "column_kind": "timepoints",
            "experimental_design": "time_series",
        }
        self.truth = {"contexts": ["global"]}
        self.manifest = {
            "schema_version": "1.0",
            "simulator_id": "unit_fixture",
            "seed": 1,
            "data_axes": self.axes,
            "truth_requirements": self.truth,
            "expression": {
                "path": "expression.tsv",
                "genes": 3,
                "columns": 5,
                "column_kind": "timepoints",
            },
            "extras": {
                "tf_list": "extras/tf_list.txt",
                "column_descriptors": "extras/column_descriptors.tsv",
            },
            "truth": {
                "gene_universe": "truth/gene_universe.txt",
                "networks": "truth/networks.csv",
            },
            "native_outputs": {},
            "provenance": {"raw_dir": "raw"},
        }

    def validate(self):
        return validate_simulator_output_package(
            stage_dir=self.directory,
            dataset_id="unit_fixture",
            data_axes=self.axes,
            truth_requirements=self.truth,
            simulator_manifest=self.manifest,
        )

    def test_shared_expression_and_descriptors_survive_generation_to_inference(self):
        schema = json.loads(
            (SCHEMAS / "simulator-output-manifest.schema.json").read_text()
        )
        self.assertEqual(
            list(Draft202012Validator(schema).iter_errors(self.manifest)), []
        )
        self.assertEqual(
            set(self.validate()["extras"]), {"tf_list", "column_descriptors"}
        )
        payload = _dataset_manifest_payload(
            dataset_id="unit_fixture",
            request=SimpleNamespace(
                data_axes=self.axes,
                organism={"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                notes=None,
            ),
            simulator_manifest=self.manifest,
        )
        self.assertEqual(payload["extras"], self.manifest["extras"])
        self.assertNotIn("temporal", self.manifest)
        self.assertNotIn("temporal", payload)
        path = self.directory / "generated-manifest.json"
        path.write_text(json.dumps(payload))
        _, schemas = _resolve_catalog_paths()
        dataset = _parse_dataset_context(
            dataset_manifest_path=path, constraints=_load_schema_constraints(schemas)
        )
        report, errors, _ = _validate_dataset_inputs_by_specs(
            dataset=dataset, input_specs=load_input_specs()
        )
        self.assertEqual(errors, [])
        self.assertEqual(report["extras"]["column_descriptors"]["status"], "ok")
        self.assertEqual(payload["dataset"]["spec"]["expression"]["columns"], 5)

    def test_scientific_temporal_checks_are_local_to_requested_tools(self):
        from andrea.core.shared.bulk_temporal import validate_bulk_temporal_inputs

        self.descriptors.write_text(
            self.metadata_text.replace(
                "ss1\tsteady_state\t\t\tcontrol\tss_r1\tsteady1\t1\t\t",
                "ss1\tsteady_state\t\t\tcontrol\tss_r1\tsteady1\t1\t0\tmodel_time",
            )
        )
        self.validate()
        with self.assertRaisesRegex(ValueError, "steady_state|timepoint"):
            validate_bulk_temporal_inputs(
                expression_path=self.expression,
                column_descriptors_path=self.descriptors,
            )

    def test_generic_descriptor_contract_does_not_require_temporal_columns(self):
        self.descriptors.write_text(
            "column\tbatch\nt0\tb1\nt1\tb1\nt2\tb1\nss1\tb2\nss2\tb2\n"
        )
        self.assertEqual(
            set(self.validate()["extras"]), {"tf_list", "column_descriptors"}
        )

    def test_bulk_timepoints_do_not_add_global_metadata_requirements(self):
        self.manifest["extras"].pop("column_descriptors")
        self.assertEqual(set(self.validate()["extras"]), {"tf_list"})

    def test_old_temporal_manifest_property_is_not_a_public_input(self):
        schema = json.loads(
            (SCHEMAS / "simulator-output-manifest.schema.json").read_text()
        )
        self.manifest["temporal"] = {"sampling_design": "longitudinal"}
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(self.manifest)))

    def test_public_schemas_expose_only_the_common_metadata_extra(self):
        for filename, keys in (
            (
                "scenario-request.schema.json",
                ("properties", "requested_extras", "items"),
            ),
            (
                "simulation-plan.schema.json",
                ("properties", "effective_extras", "items"),
            ),
            ("simulatorspec.schema.json", ("$defs", "extra_key")),
        ):
            schema = json.loads((SCHEMAS / filename).read_text())
            for key in keys:
                schema = schema[key]
            validator = Draft202012Validator(schema)
            self.assertEqual(list(validator.iter_errors("column_descriptors")), [])
            for old in ("timepoints", "replicates", "steady_state_expression", "perturbation_design"):
                self.assertTrue(list(validator.iter_errors(old)), (filename, old))
        for filename, keys in (
            (
                "simulator-output-manifest.schema.json",
                ("properties", "extras", "properties"),
            ),
            (
                "benchmark-manifest.schema.json",
                ("properties", "artifacts", "items", "properties"),
            ),
        ):
            schema = json.loads((SCHEMAS / filename).read_text())
            for key in keys:
                schema = schema[key]
            self.assertIn("column_descriptors", schema)
            self.assertTrue(
                set(schema).isdisjoint(
                    {"timepoints", "replicates", "steady_state_expression", "perturbation_design"}
                )
            )


if __name__ == "__main__":
    unittest.main()
