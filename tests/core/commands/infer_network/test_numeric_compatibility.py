"""Catalog rules retain the established metadata/parameter-only language."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from andrea.core.commands.infer_network.commons.shared import DatasetContext
from andrea.core.commands.infer_network.commons.tool_rule_eval import (
    _collect_compatibility_rule_issues,
)

ROOT = Path(__file__).resolve().parents[4]


def collect(tmp_path, condition, *, catalog_scan=False):
    dataset = DatasetContext(
        "test",
        "timepoints",
        "bulk",
        "synthetic",
        None,
        4,
        16,
        tmp_path / "expression.tsv",
        {
            "tf_list": tmp_path / "unreadable-tfs.txt",
            "column_descriptors": tmp_path / "unreadable-metadata.tsv",
        },
    )
    return _collect_compatibility_rule_issues(
        tool_id="arbitrary_method",
        toolspec={
            "compatibility_rules": [
                {
                    "conditions": [condition],
                    "action": "block",
                    "message": "invalid dimension",
                }
            ]
        },
        dataset=dataset,
        resolved_params={"width": 3},
        resolved_execution={"mode": "global"},
        catalog_scan=catalog_scan,
    )


def test_declared_dimension_rule_does_not_open_input_files(tmp_path):
    assert collect(
        tmp_path,
        {"field": "dataset.expression.genes", "op": "lt", "value": 5},
        catalog_scan=True,
    ) == (["invalid dimension"], [], [])


def test_mutable_parameter_rule_warns_in_catalog_and_blocks_requested_run(tmp_path):
    condition = {"field": "param.width", "op": "lt", "value": 4}
    assert collect(tmp_path, condition, catalog_scan=True) == (
        [],
        ["invalid dimension"],
        [],
    )
    assert collect(tmp_path, condition) == (["invalid dimension"], [], [])


@pytest.mark.parametrize(
    "field",
    [
        "dataset.temporal.series_count",
        "dataset.temporal.min_timepoints_per_series",
        "input.tf_list.count",
    ],
)
def test_file_content_facts_are_not_supported_compatibility_fields(tmp_path, field):
    blocks, warnings, errors = collect(
        tmp_path, {"field": field, "op": "lt", "value": 2}, catalog_scan=True
    )
    assert not blocks and not warnings
    assert errors and "unsupported compatibility field" in errors[0]


@pytest.mark.parametrize(
    "condition",
    [
        {"field": {"op": "add", "args": [1, 2]}, "op": "gt", "value": 1},
        {"field": "param.width", "op": "gt", "value_from": "param.other"},
        {"field": "param.width", "op": "gt", "value_from": "input.tf_list.count"},
        {"field": "dataset.temporal.series_count", "op": "gt", "value": 1},
    ],
)
def test_toolspec_schema_rejects_new_arithmetic_and_content_language(condition):
    schema = json.loads(
        (
            ROOT / "andrea/catalog_inference_tools/schemas/toolspec.schema.json"
        ).read_text()
    )
    validator = Draft202012Validator(
        {"$defs": schema["$defs"], "$ref": "#/$defs/condition"}
    )
    assert list(validator.iter_errors(condition))


def test_swing_rules_use_only_the_established_schema():
    schema = json.loads(
        (
            ROOT / "andrea/catalog_inference_tools/schemas/toolspec.schema.json"
        ).read_text()
    )
    spec = json.loads(
        (ROOT / "andrea/catalog_inference_tools/tools/swing/toolspec.json").read_text()
    )
    assert not list(Draft202012Validator(schema).iter_errors(spec))
    rules = json.dumps(spec["compatibility_rules"])
    assert "dataset.temporal" not in rules
    assert "input.tf_list.count" not in rules
