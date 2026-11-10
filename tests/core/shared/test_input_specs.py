from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from andrea.core.shared.input_specs import load_input_specs
from andrea.core.shared.input_validation import validate_tsv_file_with_spec

ROOT = Path(__file__).resolve().parents[3]


def test_input_specs_have_one_stable_contract():
    schema = json.loads(
        (ROOT / "andrea/catalog_inference_tools/schemas/input-spec.schema.json").read_text()
    )
    specs = load_input_specs()
    for spec in specs.values():
        assert not list(Draft202012Validator(schema).iter_errors(spec))
        assert "profiles" not in spec
    assert not {"timepoints", "replicates", "steady_state_expression"} & set(specs)
    spec = specs["column_descriptors"]
    assert spec["required_columns"] == []
    assert spec["min_columns"] == 2
    assert spec["column_types"]["timepoint"] == "float"
    assert "covariate_" not in spec["description"]


def test_optional_temporal_columns_have_fixed_types_without_requiring_them(tmp_path):
    spec = load_input_specs()["column_descriptors"]
    path = tmp_path / "column_descriptors.tsv"

    def validate(text):
        path.write_text(text)
        return validate_tsv_file_with_spec(
            key="column_descriptors", path=path, spec=spec,
            expression_genes={"G1"}, expression_columns={"S1", "S2"},
            expression_columns_count=2,
        )

    assert validate("column\tcondition\nS1\tcontrol\nS2\ttreatment\n").status == "ok"
    assert validate("column\ttimepoint\nS1\t0\nS2\t1.5\n").status == "ok"
    assert validate("column\ttimepoint\nS1\tbad\nS2\t1.5\n").status == "error"
    assert validate("column\ttimepoint\nS1\t\nS2\t1.5\n").status == "ok"


def test_interventions_share_the_column_descriptor_contract(tmp_path):
    specs = load_input_specs()
    assert "perturbation_design" not in specs
    spec = specs["column_descriptors"]
    path = tmp_path / "column_descriptors.tsv"

    def validate(text):
        path.write_text(text)
        return validate_tsv_file_with_spec(
            key="column_descriptors", path=path, spec=spec,
            expression_genes={"G1"}, expression_columns={"S1", "S2"},
            expression_columns_count=2,
        )

    header = "column\tcondition\tperturbation\ttarget\tdose\tcontrol\n"
    rows = "S1\tcontrol\tnone\t\t\ttrue\nS2\ttreated\tknockdown\tG1\t0.5\tfalse\n"
    assert validate(header + rows).status == "ok"
    assert validate(header + rows.replace("\tG1\t", "\tunknown\t")).status == "error"
    assert validate(header + rows.replace("0.5", "high")).status == "error"
    assert validate(header + rows.replace("false", "maybe")).status == "error"


def test_intervention_onset_has_its_own_optional_coordinate_and_unit(tmp_path):
    spec = load_input_specs()["column_descriptors"]
    path = tmp_path / "column_descriptors.tsv"
    path.write_text(
        "column\ttimepoint\tintervention_timepoint\tintervention_time_unit\n"
        "S1\t24\t\t\n"
        "S2\t24\t0.5\tsimulation_fraction\n"
    )
    result = validate_tsv_file_with_spec(
        key="column_descriptors", path=path, spec=spec,
        expression_genes={"G1"}, expression_columns={"S1", "S2"},
        expression_columns_count=2,
    )
    assert result.status == "ok"
    assert spec["column_types"]["timepoint"] == "float"
    assert spec["column_types"]["intervention_timepoint"] == "float"
    assert spec["column_types"]["intervention_time_unit"] == "string"
