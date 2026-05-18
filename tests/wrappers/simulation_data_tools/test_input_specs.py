from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "wrappers" / "simulation_data_tools" / "scripts"
SCHEMA_PATH = (
    REPO_ROOT
    / "andrea"
    / "catalog_simulation_data_tools"
    / "schemas"
    / "input-spec.schema.json"
)
INPUT_SPECS_ROOT = REPO_ROOT / "andrea" / "catalog_simulation_data_tools" / "input_specs"


def _load_script(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


validate_input_specs = _load_script(
    "simulation_validate_input_specs",
    SCRIPTS_ROOT / "validate_input_specs.py",
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class SimulationInputSpecCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = _load_json(SCHEMA_PATH)
        cls.validator = Draft202012Validator(cls.schema)
        cls.spec_paths = sorted(INPUT_SPECS_ROOT.glob("*.json"))
        cls.specs = {path.stem: _load_json(path) for path in cls.spec_paths}

    def test_catalog_contains_current_simulator_inputs(self) -> None:
        self.assertEqual(set(self.specs), {"regulatory_network", "tree_newick"})

    def test_all_simulation_input_specs_validate_against_schema(self) -> None:
        for input_id, spec in self.specs.items():
            errors = sorted(
                self.validator.iter_errors(spec),
                key=lambda err: list(err.path),
            )
            self.assertFalse(
                errors,
                msg=f"{input_id} InputSpec validation failed: "
                + "; ".join(f"{list(err.path)} -> {err.message}" for err in errors),
            )

    def test_input_spec_ids_match_filenames(self) -> None:
        for input_id, spec in self.specs.items():
            self.assertEqual(spec["id"], input_id)

    def test_tsv_input_specs_type_every_required_column(self) -> None:
        for input_id, spec in self.specs.items():
            if spec["format"] != "tsv":
                continue
            required = set(spec["required_columns"])
            typed = set(spec["column_types"])
            self.assertLessEqual(required, typed, msg=input_id)

    def test_validator_rejects_mismatched_filename_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            bad = dict(self.specs["tree_newick"])
            bad["id"] = "different_id"
            (tmp_root / "tree_newick.json").write_text(
                json.dumps(bad, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            exit_code = validate_input_specs.run(
                schema_path=SCHEMA_PATH,
                input_specs_root=tmp_root,
                spec_filters=[],
                fail_fast=False,
            )

        self.assertEqual(exit_code, 1)

    def test_validator_rejects_tsv_missing_required_column_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            bad = dict(self.specs["regulatory_network"])
            bad["column_types"] = {"target": "string", "regulator": "string"}
            (tmp_root / "regulatory_network.json").write_text(
                json.dumps(bad, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            exit_code = validate_input_specs.run(
                schema_path=SCHEMA_PATH,
                input_specs_root=tmp_root,
                spec_filters=[],
                fail_fast=False,
            )

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
