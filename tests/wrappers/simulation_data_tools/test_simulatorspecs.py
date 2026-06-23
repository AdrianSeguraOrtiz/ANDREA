from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = (
    REPO_ROOT
    / "andrea"
    / "catalog_simulation_data_tools"
    / "schemas"
    / "simulatorspec.schema.json"
)
SIMULATORS_DIR = REPO_ROOT / "andrea" / "catalog_simulation_data_tools" / "simulators"
INPUT_SPECS_DIR = REPO_ROOT / "andrea" / "catalog_simulation_data_tools" / "input_specs"
VALIDATOR_SCRIPT = (
    REPO_ROOT
    / "wrappers"
    / "simulation_data_tools"
    / "scripts"
    / "validate_simulatorspecs.py"
)
DRAFT_SIMULATORS_TO_SKIP = {"genespider2"}


def _load_validator_module():
    scripts_dir = str(VALIDATOR_SCRIPT.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(
        "validate_simulatorspecs_for_tests",
        VALIDATOR_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import validator script: {VALIDATOR_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_capability(
    spec: dict,
    *,
    experimental_design: str,
    contexts: tuple[str, ...],
) -> dict:
    expected_axes = {
        "measurement": "rna_expression",
        "resolution": "single_cell",
        "column_kind": "cells",
        "experimental_design": experimental_design,
    }
    for capability in spec["capabilities"]:
        if (
            capability["data_axes"] == expected_axes
            and tuple(capability["truth_requirements"]["contexts"]) == contexts
        ):
            return capability
    raise AssertionError(
        f"Missing capability for {experimental_design=} and {contexts=}"
    )


def _truth_output_statuses(capability: dict) -> dict[str, str]:
    return {
        item["context"]: item["status"]
        for item in capability.get("truth_outputs", [])
        if isinstance(item, dict)
    }


def _valid_semantic_spec() -> dict:
    evidence_context = {
        "context": "global",
        "status": "native",
        "source_artifacts": ["native_global_grn"],
        "upstream_configuration": ["simulator(..., dynamic=false)"],
        "generation": "Rows with context=global are copied from the native simulator GRN.",
        "score_semantics": "score=abs(effect); sign is sign(effect).",
        "limitations": ["Synthetic test contract."],
    }
    return {
        "schema_version": "1.0",
        "id": "dyngen",
        "name": "dyngen test",
        "docker_image": "adriansegura99/simulator_dyngen:1.0.0",
        "publication": ["https://doi.org/10.1000/example"],
        "first_author": "Test Author",
        "year": 2024,
        "simulation_summary": "Synthetic test spec for validator semantics.",
        "simulation_keywords": ["scrna"],
        "implementation_url": "https://example.org/dyngen",
        "runtime_resources": {
            "threading": {
                "supported": True,
                "default_threads": 1,
                "max_threads": 4,
                "upstream_mapping": "threads",
            }
        },
        "extra_inputs": {
            "required": [],
            "optional": [],
            "conditional_required": [],
        },
        "compatibility_rules": [],
        "params": {},
        "capabilities": [
            {
                "data_axes": {
                    "measurement": "rna_expression",
                    "resolution": "single_cell",
                    "column_kind": "cells",
                    "experimental_design": "trajectory",
                },
                "truth_requirements": {
                    "contexts": ["global", "group", "column"],
                },
                "native_extras": [],
                "derivable_extras": [],
                "truth_outputs": [
                    {"context": "global", "status": "native"},
                    {"context": "group", "status": "derivable"},
                    {"context": "column", "status": "native"},
                ],
                "truth_contexts": [
                    evidence_context,
                    {
                        **evidence_context,
                        "context": "group",
                        "status": "derivable",
                    },
                    {
                        **evidence_context,
                        "context": "column",
                        "status": "native",
                    },
                ],
                "derivations": [
                    {
                        "artifact": "group",
                        "source_artifacts": ["column_networks"],
                        "method": "Aggregate column networks by group.",
                        "assumptions": ["Groups exist."],
                        "limitations": ["Synthetic test contract."],
                    }
                ],
                "native_outputs": [],
                "artifacts_aux": [],
            }
        ],
    }


class SimulatorSpecCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = _load_json(SCHEMA_PATH)
        cls.validator = Draft202012Validator(cls.schema)
        cls.spec_paths = sorted(
            path
            for path in SIMULATORS_DIR.glob("*/simulatorspec.json")
            if path.parent.name not in DRAFT_SIMULATORS_TO_SKIP
        )
        cls.specs = {path.parent.name: _load_json(path) for path in cls.spec_paths}

    def test_catalog_contains_dyngen_spec(self) -> None:
        self.assertIn("dyngen", self.specs)

    def test_all_simulator_specs_validate(self) -> None:
        for simulator_id, spec in self.specs.items():
            errors = sorted(
                self.validator.iter_errors(spec),
                key=lambda err: list(err.path),
            )
            self.assertFalse(
                errors,
                msg=f"{simulator_id} spec validation failed: "
                + "; ".join(f"{list(err.path)} -> {err.message}" for err in errors),
            )

    def test_legacy_profile_capabilities_shape_is_rejected(self) -> None:
        legacy_spec = copy.deepcopy(_valid_semantic_spec())
        legacy_spec["profile_capabilities"] = {
            "scrna_global": legacy_spec.pop("capabilities")[0]
        }

        errors = sorted(
            self.validator.iter_errors(legacy_spec),
            key=lambda err: list(err.path),
        )

        self.assertTrue(errors)
        messages = "; ".join(error.message for error in errors)
        self.assertIn("'capabilities' is a required property", messages)
        self.assertIn(
            "Additional properties are not allowed ('profile_capabilities' was unexpected)",
            messages,
        )

    def test_all_simulator_specs_declare_compatibility_rules(self) -> None:
        for simulator_id, spec in self.specs.items():
            self.assertIn("compatibility_rules", spec, msg=simulator_id)
            self.assertIsInstance(spec["compatibility_rules"], list, msg=simulator_id)

    def test_runtime_parallelism_is_declared_as_resources(self) -> None:
        for simulator_id, spec in self.specs.items():
            threading = spec["runtime_resources"]["threading"]
            self.assertIn("supported", threading)
            self.assertIn("default_threads", threading)
            self.assertIn("max_threads", threading)
            self.assertIn("upstream_mapping", threading)
            self.assertLessEqual(threading["default_threads"], threading["max_threads"])
            self.assertNotIn("threads", spec["params"], msg=simulator_id)
            self.assertNotIn("num_cores", spec["params"], msg=simulator_id)

    def test_dyngen_spec_uses_full_publication_url_and_full_first_author(self) -> None:
        dyngen = self.specs["dyngen"]
        self.assertEqual(
            dyngen["publication"],
            ["https://doi.org/10.1038/s41467-021-24152-2"],
        )
        self.assertEqual(dyngen["first_author"], "Robrecht Cannoodt")
        self.assertEqual(
            dyngen["docker_image"],
            "adriansegura99/simulator_dyngen:1.0.0",
        )

    def test_dyngen_capabilities_are_single_cell_only(self) -> None:
        dyngen = self.specs["dyngen"]
        self.assertEqual(
            {
                (
                    capability["data_axes"]["measurement"],
                    capability["data_axes"]["resolution"],
                    capability["data_axes"]["column_kind"],
                    capability["data_axes"]["experimental_design"],
                    tuple(capability["truth_requirements"]["contexts"]),
                )
                for capability in dyngen["capabilities"]
            },
            {
                (
                    "rna_expression",
                    "single_cell",
                    "cells",
                    design,
                    contexts,
                )
                for design in ("trajectory", "time_series", "perturbational")
                for contexts in (
                    ("global",),
                    ("global", "group"),
                    ("global", "group", "column"),
                )
            },
        )
        global_capability = _find_capability(
            dyngen,
            experimental_design="trajectory",
            contexts=("global",),
        )
        grouped = _find_capability(
            dyngen,
            experimental_design="trajectory",
            contexts=("global", "group"),
        )
        column_truth = _find_capability(
            dyngen,
            experimental_design="trajectory",
            contexts=("global", "group", "column"),
        )
        self.assertEqual(
            {item["id"] for item in global_capability["native_outputs"]},
            {
                "milestone_network",
                "milestone_percentages",
                "progressions",
                "rna_velocity",
                "regulatory_network_sc",
            },
        )
        self.assertEqual(
            set(grouped["derivable_extras"]),
            {
                "groups",
                "column_phenotypes",
                "cluster_identities",
                "enrichment_background",
                "lineage_tree",
                "pseudotime",
                "prior_grn",
                "tf_list",
                "prior_grn_by_group",
            },
        )
        self.assertEqual(
            _truth_output_statuses(grouped),
            {"global": "native", "group": "derivable"},
        )
        self.assertEqual(
            _truth_output_statuses(column_truth),
            {"global": "native", "group": "derivable", "column": "native"},
        )
        self.assertEqual(
            {item["context"]: item["status"] for item in grouped["truth_contexts"]},
            {"global": "native", "group": "derivable", "column": "none"},
        )
        self.assertEqual(
            {
                item["context"]: item["status"]
                for item in column_truth["truth_contexts"]
            },
            {"global": "native", "group": "derivable", "column": "native"},
        )
        self.assertIn(
            "group",
            {item["artifact"] for item in column_truth["derivations"]},
        )
        self.assertEqual(
            set(column_truth["derivable_extras"]),
            {
                "groups",
                "column_phenotypes",
                "cluster_identities",
                "enrichment_background",
                "lineage_tree",
                "pseudotime",
                "prior_grn",
                "tf_list",
                "prior_grn_by_group",
            },
        )

    def test_dyngen_documents_every_derivation(self) -> None:
        dyngen = self.specs["dyngen"]
        for capability in dyngen["capabilities"]:
            capability_id = (
                f"{capability['data_axes']['experimental_design']}:"
                f"{','.join(capability['truth_requirements']['contexts'])}"
            )
            expected = set(capability["derivable_extras"])
            expected.update(
                key
                for key, mode in _truth_output_statuses(capability).items()
                if mode == "derivable"
            )
            documented = {item["artifact"] for item in capability["derivations"]}
            self.assertEqual(
                documented,
                expected,
                msg=f"{capability_id} derivation documentation mismatch",
            )
            for derivation in capability["derivations"]:
                self.assertTrue(derivation["source_artifacts"])
                self.assertTrue(derivation["method"])
                self.assertTrue(derivation["assumptions"])
                self.assertTrue(derivation["limitations"])

    def test_dyngen_declares_no_external_required_inputs(self) -> None:
        dyngen = self.specs["dyngen"]
        self.assertEqual(dyngen["extra_inputs"]["required"], [])
        self.assertEqual(dyngen["extra_inputs"]["optional"], [])

    def test_extra_inputs_reference_shared_simulation_input_specs(self) -> None:
        known_inputs = {path.stem for path in INPUT_SPECS_DIR.glob("*.json")}
        forbidden_inline_fields = {
            "format",
            "formats",
            "accepted_extensions",
            "example",
            "required_columns",
            "column_types",
        }
        referenced: set[str] = set()
        for simulator_id, spec in self.specs.items():
            extra_inputs = spec["extra_inputs"]
            for bucket in ("required", "optional", "conditional_required"):
                for item in extra_inputs[bucket]:
                    input_id = item["input"]
                    referenced.add(input_id)
                    self.assertIn(input_id, known_inputs, msg=simulator_id)
                    self.assertFalse(
                        forbidden_inline_fields.intersection(item),
                        msg=f"{simulator_id} extra_inputs.{bucket}.{input_id}",
                    )

        self.assertEqual(
            referenced,
            {
                "boolode_boolean_model",
                "boolode_initial_conditions",
                "boolode_interaction_strengths",
                "gnw_dynamical_network",
                "regulatory_network",
                "sergio_bifurcation_matrix",
                "sergio_master_regulators",
                "sergio_target_interactions",
                "tree_newick",
            },
        )

    def test_dyngen_exposes_broad_serializable_parameter_surface(self) -> None:
        dyngen = self.specs["dyngen"]
        for param_name in (
            "backbone_template",
            "num_cells",
            "num_tfs",
            "num_targets",
            "num_hks",
            "distance_metric",
            "tf_network_params",
            "feature_network_params",
            "gold_standard_params",
            "simulation_params",
            "experiment_params",
        ):
            self.assertIn(param_name, dyngen["params"])
        self.assertFalse(
            dyngen["params"]["simulation_params"]["properties"]["compute_dimred"][
                "default"
            ]
        )
        self.assertFalse(
            dyngen["params"]["experiment_params"]["properties"]["map_reference_cpm"][
                "default"
            ]
        )

    def test_scmultisim_declares_column_truth_capability(self) -> None:
        scmultisim = self.specs["scmultisim"]
        column_truth = _find_capability(
            scmultisim,
            experimental_design="differentiation",
            contexts=("global", "group", "column"),
        )
        self.assertEqual(
            _truth_output_statuses(column_truth),
            {"global": "derivable", "group": "derivable", "column": "native"},
        )
        self.assertEqual(
            {
                item["context"]: item["status"]
                for item in column_truth["truth_contexts"]
            },
            {"global": "derivable", "group": "derivable", "column": "native"},
        )
        self.assertIn(
            "group",
            {item["artifact"] for item in column_truth["derivations"]},
        )
        self.assertEqual(
            set(column_truth["derivable_extras"]),
            {
                "groups",
                "column_phenotypes",
                "cluster_identities",
                "enrichment_background",
                "lineage_tree",
                "pseudotime",
                "prior_grn",
                "tf_list",
                "prior_grn_by_group",
                "chromatin_accessibility",
                "chromatin_regions",
            },
        )
        requirements = {
            requirement["truth_output"]: requirement
            for requirement in column_truth["truth_parameter_requirements"]
        }
        self.assertEqual(
            set(requirements),
            {"group", "column"},
        )
        for truth_output in ("group", "column"):
            self.assertEqual(
                requirements[truth_output],
                {
                    "truth_output": truth_output,
                    "conditions": [
                        {
                            "field": "param.dynamic_grn.enabled",
                            "op": "eq",
                            "value": True,
                        }
                    ],
                    "message": (
                        "Column truth requires dynamic_grn.enabled=true."
                        if truth_output == "column"
                        else "Group truth requires dynamic_grn.enabled=true."
                    ),
                },
            )

    def test_truth_context_schema_requires_evidence_for_non_none_contexts(self) -> None:
        broken_spec = _valid_semantic_spec()
        del broken_spec["capabilities"][0]["truth_contexts"][0]["generation"]

        errors = sorted(
            self.validator.iter_errors(broken_spec),
            key=lambda err: list(err.path),
        )

        self.assertTrue(errors)
        self.assertTrue(
            any("generation" in error.message for error in errors),
            msg="; ".join(error.message for error in errors),
        )

    def test_truth_context_semantics_accept_valid_contract(self) -> None:
        validator = _load_validator_module()
        errors = validator.semantic_errors(
            simulator_id="dyngen",
            spec=_valid_semantic_spec(),
            wrappers_root=REPO_ROOT / "wrappers" / "simulation_data_tools" / "simulators",
            known_input_ids=set(),
        )

        self.assertEqual(errors, [])

    def test_truth_context_semantics_reject_missing_truth_contexts(self) -> None:
        validator = _load_validator_module()
        spec = _valid_semantic_spec()
        del spec["capabilities"][0]["truth_contexts"]

        errors = validator.semantic_errors(
            simulator_id="dyngen",
            spec=spec,
            wrappers_root=REPO_ROOT / "wrappers" / "simulation_data_tools" / "simulators",
            known_input_ids=set(),
        )

        self.assertIn(
            "capabilities[0]: missing truth_contexts array",
            errors,
        )

    def test_truth_context_semantics_reject_status_mismatch(self) -> None:
        validator = _load_validator_module()
        spec = _valid_semantic_spec()
        spec["capabilities"][0]["truth_contexts"][1]["status"] = "native"

        errors = validator.semantic_errors(
            simulator_id="dyngen",
            spec=spec,
            wrappers_root=REPO_ROOT / "wrappers" / "simulation_data_tools" / "simulators",
            known_input_ids=set(),
        )

        self.assertTrue(
            any(
                "status must match truth_outputs context 'group'" in error
                for error in errors
            ),
            msg=errors,
        )

    def test_truth_context_semantics_reject_unknown_context(self) -> None:
        validator = _load_validator_module()
        spec = _valid_semantic_spec()
        spec["capabilities"][0]["truth_contexts"][1]["context"] = "branch"

        errors = validator.semantic_errors(
            simulator_id="dyngen",
            spec=spec,
            wrappers_root=REPO_ROOT / "wrappers" / "simulation_data_tools" / "simulators",
            known_input_ids=set(),
        )

        self.assertTrue(
            any("unknown truth context 'branch'" in error for error in errors),
            msg=errors,
        )

    def test_legacy_profile_condition_field_is_rejected(self) -> None:
        validator = _load_validator_module()
        spec = _valid_semantic_spec()
        spec["compatibility_rules"] = [
            {
                "conditions": [
                    {
                        "field": "profile",
                        "op": "eq",
                        "value": "scrna_grouped",
                    }
                ],
                "action": "warn",
                "message": "Legacy profile condition.",
            }
        ]

        errors = validator.semantic_errors(
            simulator_id="dyngen",
            spec=spec,
            wrappers_root=REPO_ROOT / "wrappers" / "simulation_data_tools" / "simulators",
            known_input_ids=set(),
        )

        self.assertTrue(
            any("condition field 'profile' is legacy" in error for error in errors),
            msg=errors,
        )

    def test_truth_context_semantics_reject_required_context_with_none_status(
        self,
    ) -> None:
        validator = _load_validator_module()
        spec = _valid_semantic_spec()
        capability = spec["capabilities"][0]
        capability["truth_outputs"] = [
            item for item in capability["truth_outputs"] if item["context"] != "group"
        ]
        capability["truth_contexts"][1] = {
            "context": "group",
            "status": "none",
            "explanation": "No group truth is exported.",
        }
        capability["derivations"] = []

        errors = validator.semantic_errors(
            simulator_id="dyngen",
            spec=spec,
            wrappers_root=REPO_ROOT / "wrappers" / "simulation_data_tools" / "simulators",
            known_input_ids=set(),
        )

        self.assertIn(
            "capabilities[0].truth_outputs: "
            "required truth context(s) are not supported: group",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
