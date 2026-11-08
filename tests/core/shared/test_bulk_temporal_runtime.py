from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil

import pytest

from andrea.core.commands.infer_network.commons.artifacts import (
    _deserialize_dataset_context,
    _serialize_dataset_context,
)
from andrea.core.commands.infer_network.commons.catalog import (
    _load_schema_constraints,
    _resolve_catalog_paths,
)
from andrea.core.commands.infer_network.commons.dataset import _parse_dataset_context
from andrea.core.commands.infer_network.preflight import preflight_infer_network
from andrea.core.commands.infer_network.plan import plan_infer_network
from andrea.core.shared.bulk_temporal import (
    validate_bulk_temporal_inputs,
    select_temporal_observations,
)
from andrea.core.shared.column_metadata import load_column_descriptors
from andrea.core.shared.dataset_identity import fingerprint_dataset_content

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/bulk_time_series/dyngenie3_complete"


@pytest.fixture
def bundle(tmp_path):
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    return tmp_path


def manifest(directory):
    return json.loads((directory / "dataset-manifest.json").read_text())


def validate(directory):
    return validate_bulk_temporal_inputs(
        expression_path=directory / "expression.tsv",
        column_descriptors_path=directory / "column_descriptors.tsv",
    )


def rows(directory):
    with (directory / "column_descriptors.tsv").open() as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_rows(directory, values):
    with (directory / "column_descriptors.tsv").open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=values[0], delimiter="\t")
        writer.writeheader()
        writer.writerows(values)


def request(directory, tool):
    path = directory / "tools-params.json"
    path.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "run_id": "chosen",
                        "tool_id": tool,
                        "execution": {"mode": "global"},
                        "params": {},
                    }
                ]
            }
        )
    )
    return path


def test_one_matrix_summary_separates_roles_without_creating_ss_times(bundle):
    report = validate(bundle)
    assert report["columns"] == 8 and report["observations"] == 8
    assert (
        len(report["time_series_columns"]) == 6
        and len(report["steady_state_columns"]) == 2
    )
    assert len(report["series"]) == 2 and len(report["longitudinal_transitions"]) == 4
    assert report["observation_counts"] == {"time_series": 6, "steady_state": 2}
    for column in report["steady_state_columns"]:
        row = report["observations_by_column"][column]
        assert row["timepoint"] is None and row["series_id"] == row["time_unit"] == ""
    assert set(report["sha256"]) == {"expression_matrix", "column_descriptors"}


def test_tool_selection_is_explicit_in_preflight_plan_and_frozen_contract(
    bundle, monkeypatch
):
    import importlib

    runtime = importlib.import_module("andrea.core.commands.infer_network.run")

    def stop_before_docker():
        raise RuntimeError("validated frozen role selection; stop before Docker")

    monkeypatch.setattr(runtime, "_ensure_docker_cli", stop_before_docker)
    for tool, expected in [("dyngenie3", 8)]:
        params = request(bundle, tool)
        preflight = preflight_infer_network(
            dataset_manifest_path=bundle / "dataset-manifest.json",
            tools_params_path=params,
        )
        assert preflight["runs"]["selected"] == ["chosen"], preflight["runs"]
        selection = preflight["runs"]["observation_selection"]["chosen"]
        assert selection["columns"] == expected
        assert len(selection["ignored_columns"]) == 8 - expected
        directory = plan_infer_network(
            dataset_manifest_path=bundle / "dataset-manifest.json",
            tools_params_path=params,
            output_dir=bundle / tool,
            max_cores=1,
            max_ram_gb=4,
            planner="heuristic",
            preflight_report=preflight,
        )
        contract = json.loads(
            (directory / "input/runtime-input-contract.json").read_text()
        )
        assert contract["runs"]["chosen"]["observation_selection"] == selection
        plan = json.loads((directory / "plan.json").read_text())
        assert plan["runs"][0]["physical_tasks"][0]["columns"] == expected
        with pytest.raises(RuntimeError, match="validated frozen role selection"):
            runtime.run_infer_network_plan(run_dir=directory)


def test_temporal_dataset_context_roundtrip_and_fingerprint_cover_unified_metadata(
    bundle,
):
    _, schemas = _resolve_catalog_paths()
    dataset = _parse_dataset_context(
        dataset_manifest_path=bundle / "dataset-manifest.json",
        constraints=_load_schema_constraints(schemas),
    )
    assert _deserialize_dataset_context(_serialize_dataset_context(dataset)) == dataset
    before = fingerprint_dataset_content(
        expression_path=dataset.expression_matrix_path,
        extras=dataset.extras,
    )
    records = rows(bundle)
    records[-1]["condition"] = "changed_condition"
    write_rows(bundle, records)
    after = fingerprint_dataset_content(
        expression_path=dataset.expression_matrix_path,
        extras=dataset.extras,
    )
    assert before != after


@pytest.mark.parametrize(
    "field,value,steady",
    [
        ("observation_type", "unknown", False),
        ("timepoint", "", False),
        ("timepoint", "nan", False),
        ("condition", "", False),
        ("replicate", "", False),
        ("technical_replicate", "", False),
        ("sampling_design", "", False),
        ("sampling_design", "guessed_longitudinal", False),
        ("sampling_design", "longitudinal", True),
        ("timepoint", "0", True),
        ("time_unit", "hour", True),
        ("series_id", "invented", True),
    ],
)
def test_invalid_role_specific_metadata_is_rejected(bundle, field, value, steady):
    records = rows(bundle)
    row = next(
        row for row in records if (row["observation_type"] == "steady_state") is steady
    )
    row[field] = value
    write_rows(bundle, records)
    with pytest.raises(ValueError):
        validate(bundle)


def test_exact_joins_and_observation_identity_prevent_silent_deduplication(bundle):
    records = rows(bundle)
    records[-1]["column"] = records[0]["column"]
    write_rows(bundle, records)
    with pytest.raises(ValueError, match="duplicat.*expression-column"):
        validate(bundle)


def test_observation_id_cannot_cross_ts_and_ss_roles(bundle):
    records = rows(bundle)
    records[-1]["observation_id"] = records[0]["observation_id"]
    write_rows(bundle, records)
    with pytest.raises(ValueError, match="observation_id cannot cross"):
        validate(bundle)


def test_technical_replicates_are_represented_without_averaging(tmp_path):
    source = ROOT / "tests/fixtures/bulk_time_series/regular_longitudinal"
    shutil.copytree(source, tmp_path, dirs_exist_ok=True)
    report = validate(tmp_path)
    assert report["columns"] == 24 and report["observations"] == 12
    assert len(report["longitudinal_transitions"]) == 8
    params = request(tmp_path, "dyngenie3")
    preflight = preflight_infer_network(
        dataset_manifest_path=tmp_path / "dataset-manifest.json",
        tools_params_path=params,
    )
    assert not preflight["runs"]["selected"]
    assert "technical replicates" in preflight["runs"]["skipped"]["chosen"]


def test_tool_technical_replication_policy_only_checks_selected_roles(bundle):
    records = rows(bundle)
    steady = [row for row in records if row["observation_type"] == "steady_state"]
    steady[1]["replicate"] = steady[0]["replicate"]
    steady[1]["condition"] = steady[0]["condition"]
    steady[1]["observation_id"] = steady[0]["observation_id"]
    steady[1]["technical_replicate"] = "2"
    write_rows(bundle, records)
    summary = validate(bundle)
    assert summary["observations"] == 7 and summary["columns"] == 8
    for tool, selected in [("dyngenie3", False)]:
        report = preflight_infer_network(
            dataset_manifest_path=bundle / "dataset-manifest.json",
            tools_params_path=request(bundle, tool),
        )
        assert bool(report["runs"]["selected"]) is selected, report["runs"]


def test_independent_samples_never_acquire_longitudinal_transitions(tmp_path):
    source = ROOT / "tests/fixtures/bulk_time_series/irregular_independent"
    shutil.copytree(source, tmp_path, dirs_exist_ok=True)
    report = validate(tmp_path)
    assert report["sampling_design"] == "independent_samples"
    assert report["longitudinal_transitions"] == []
    assert any(not item["regular"] for item in report["series"])


@pytest.mark.parametrize("key", ["timepoints", "replicates", "steady_state_expression"])
def test_removed_inputs_are_ignored_without_aliases_or_readers(bundle, key):
    payload = manifest(bundle)
    payload["extras"] = {key: "missing-retired-file.tsv"}
    (bundle / "dataset-manifest.json").write_text(json.dumps(payload))
    report = preflight_infer_network(
        dataset_manifest_path=bundle / "dataset-manifest.json",
        tools_params_path=request(bundle, "dyngenie3"),
    )
    assert key not in report["dataset"]["extras"]
    assert not report["runs"]["selected"]
    assert "required extra input missing" in report["runs"]["skipped"]["chosen"]


def test_bulk_timepoints_do_not_activate_a_global_content_profile(bundle):
    payload = manifest(bundle)
    assert "temporal" not in payload
    _, schemas = _resolve_catalog_paths()
    dataset = _parse_dataset_context(
        dataset_manifest_path=bundle / "dataset-manifest.json",
        constraints=_load_schema_constraints(schemas),
    )
    assert not hasattr(dataset, "temporal")
    report = preflight_infer_network(
        dataset_manifest_path=bundle / "dataset-manifest.json",
        tools_params_path=request(bundle, "dyngenie3"),
    )
    assert "temporal" not in report["input_validation"]
    summary = report["runs"]["temporal_validation"]["chosen"]
    assert summary["sampling_design"] == "longitudinal"
    assert summary["time_unit"] == "hour"


def test_unrecognized_temporal_envelope_is_ignored_without_being_consumed(bundle):
    baseline = preflight_infer_network(
        dataset_manifest_path=bundle / "dataset-manifest.json"
    )
    payload = manifest(bundle)
    payload["temporal"] = {"sampling_design": "not-a-supported-design"}
    (bundle / "dataset-manifest.json").write_text(json.dumps(payload))
    report = preflight_infer_network(
        dataset_manifest_path=bundle / "dataset-manifest.json"
    )
    assert baseline["catalog"] == report["catalog"]
    assert baseline["dataset"] == report["dataset"]
    assert "temporal" not in report["input_validation"]


@pytest.mark.parametrize(
    "field,value", [("time_unit", "minute"), ("sampling_design", "independent_samples")]
)
def test_temporal_facts_must_be_consistent_across_series(bundle, field, value):
    records = rows(bundle)
    records[0][field] = value
    write_rows(bundle, records)
    with pytest.raises(ValueError, match="mixed|inconsistent"):
        validate(bundle)


def test_sampling_design_is_not_inferred_from_timepoints(bundle):
    records = rows(bundle)
    for row in records:
        if row["observation_type"] == "time_series":
            row["sampling_design"] = "independent_samples"
    write_rows(bundle, records)
    with pytest.raises(
        ValueError, match="independent biological replicate cannot span timepoints"
    ):
        validate(bundle)


def test_missing_descriptor_blocks_only_tools_that_require_it(bundle):
    payload = manifest(bundle)
    payload["extras"].pop("column_descriptors")
    (bundle / "dataset-manifest.json").write_text(json.dumps(payload))
    report = preflight_infer_network(
        dataset_manifest_path=bundle / "dataset-manifest.json",
        tools_params_path=request(bundle, "dyngenie3"),
    )
    assert not report["runs"]["selected"]
    assert "required extra input missing" in report["runs"]["skipped"]["chosen"]
    assert "temporal" not in report["input_validation"]
