from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_DIR = REPO_ROOT / "andrea" / "catalog_simulation_data_tools" / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def _valid_scenario_request() -> dict:
    return {
        "schema_version": "1.0",
        "id": "scenario_a",
        "data_axes": {
            "measurement": "rna_expression",
            "resolution": "single_cell",
            "column_kind": "cells",
            "experimental_design": "trajectory",
        },
        "truth_requirements": {
            "contexts": ["global", "group"],
        },
        "organism": {
            "taxonomic_group": "synthetic",
            "ncbi_taxon_id": None,
        },
        "requested_extras": ["groups"],
    }


def _valid_preflight_report() -> dict:
    data_axes = {
        "measurement": "rna_expression",
        "resolution": "single_cell",
        "column_kind": "cells",
        "experimental_design": "steady_state",
    }
    truth_requirements = {"contexts": ["global", "column"]}
    base_entry = {
        "simulator_id": "toy",
        "name": "Toy",
        "requested_data_axes": data_axes,
        "requested_truth_requirements": truth_requirements,
        "requested_extras": [],
        "effective_extras": [],
        "inputs_used": [],
        "native_extras_used": [],
        "derived_extras_used": [],
        "truth_outputs": [
            {"context": "global", "status": "native"},
            {"context": "column", "status": "derivable"},
        ],
        "status": "eligible",
        "issues": [],
    }
    blocked_entry = copy.deepcopy(base_entry)
    blocked_entry.update(
        {
            "simulator_id": "blocked_toy",
            "status": "blocked",
            "truth_outputs": [],
            "issues": [
                {
                    "severity": "block",
                    "code": "unsupported_profile",
                    "message": "unsupported",
                }
            ],
        }
    )
    return {
        "schema_version": "1.0",
        "scenario": {
            "id": "scenario_a",
            "data_axes": data_axes,
            "truth_requirements": truth_requirements,
            "organism": {
                "taxonomic_group": "synthetic",
                "ncbi_taxon_id": None,
            },
            "requested_extras": [],
            "effective_extras": [],
            "inputs": {},
            "base_seed": 1,
        },
        "catalog_summary": {"total": 2, "eligible": 1, "warning": 0, "blocked": 1},
        "eligible": [base_entry],
        "warning": [],
        "blocked": [blocked_entry],
    }


class GenerateDataSchemaContractTests(unittest.TestCase):
    def test_all_generate_data_schemas_are_valid_json_schemas(self) -> None:
        for schema_path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
            if schema_path.name == "simulatorcost.schema.json":
                continue
            Draft202012Validator.check_schema(_load_schema(schema_path.name))

    def test_scenario_request_accepts_semantic_contract(self) -> None:
        validator = Draft202012Validator(_load_schema("scenario-request.schema.json"))

        errors = list(validator.iter_errors(_valid_scenario_request()))

        self.assertEqual(errors, [])

    def test_scenario_request_rejects_legacy_profile_contract(self) -> None:
        validator = Draft202012Validator(_load_schema("scenario-request.schema.json"))
        request = _valid_scenario_request()
        del request["data_axes"]
        del request["truth_requirements"]
        request["profile"] = "scrna_grouped"

        messages = "; ".join(error.message for error in validator.iter_errors(request))

        self.assertIn("'data_axes' is a required property", messages)
        self.assertIn("'truth_requirements' is a required property", messages)
        self.assertIn(
            "Additional properties are not allowed ('profile' was unexpected)",
            messages,
        )

    def test_scenario_request_requires_global_truth_context(self) -> None:
        validator = Draft202012Validator(_load_schema("scenario-request.schema.json"))
        request = copy.deepcopy(_valid_scenario_request())
        request["truth_requirements"]["contexts"] = ["group"]

        messages = "; ".join(error.message for error in validator.iter_errors(request))

        self.assertIn("does not contain items matching the given schema", messages)

    def test_preflight_report_accepts_truth_output_lists(self) -> None:
        validator = Draft202012Validator(_load_schema("preflight-report.schema.json"))

        errors = list(validator.iter_errors(_valid_preflight_report()))

        self.assertEqual(errors, [])

    def test_preflight_report_rejects_legacy_truth_output_object(self) -> None:
        validator = Draft202012Validator(_load_schema("preflight-report.schema.json"))
        report = _valid_preflight_report()
        report["eligible"][0]["truth_outputs"] = {
            "global": "native",
            "group": "none",
            "column": "derivable",
        }

        messages = "; ".join(error.message for error in validator.iter_errors(report))

        self.assertIn("is not of type 'array'", messages)


if __name__ == "__main__":
    unittest.main()
