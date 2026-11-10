"""PFBNet uses the established metadata catalog and requested-run contracts.

These tests never fit a model or start a container. Scientific file contents
are validated only for an explicitly requested temporal run; catalog selection
continues to use declared metadata, input availability and existing rules.
"""

from __future__ import annotations
import csv
from dataclasses import replace
import json
from pathlib import Path
import shutil
from unittest.mock import patch
import pytest
from andrea.core.commands.infer_network.commons.catalog import (
    _load_schema_constraints,
    _resolve_catalog_paths,
)
from andrea.core.commands.infer_network.commons.planner import _load_tool_cost_profile
from andrea.core.commands.infer_network.commons.shared import DatasetContext
from andrea.core.commands.infer_network.commons.tools import (
    _build_tool_compatibility_entry,
    _load_toolspec,
)
from andrea.core.commands.infer_network.plan import plan_infer_network
from andrea.core.commands.infer_network.preflight import preflight_infer_network

ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "wrappers/inference_tools/tests/fixtures/pfbnet"
TOOLS, SCHEMAS = _resolve_catalog_paths()
CONSTRAINTS = _load_schema_constraints(SCHEMAS)
SPEC = _load_toolspec(TOOLS, "pfbnet")


def catalog_entry(tmp_path, **overrides):
    dataset = DatasetContext(
        dataset_id="pfbnet_contract",
        column_kind="timepoints",
        expression_profile="bulk",
        taxonomic_group="synthetic",
        ncbi_taxon_id=None,
        genes=6,
        columns=69,
        expression_matrix_path=tmp_path / "unreadable-expression.tsv",
        extras={
            "column_descriptors": tmp_path / "unreadable-descriptors.tsv",
            "tf_list": tmp_path / "unreadable-tfs.txt",
        },
    )
    dataset = replace(dataset, **overrides)
    with patch(
        "andrea.core.shared.column_metadata.load_column_descriptors",
        side_effect=AssertionError("catalog must not inspect temporal metadata"),
    ):
        return _build_tool_compatibility_entry(
            tool_id="pfbnet",
            toolspec=SPEC,
            dataset=dataset,
            constraints=CONSTRAINTS,
            tool_origin="catalog",
            warning_code="catalog_warning",
        )


@pytest.mark.parametrize(
    ("kind", "profile", "expected"),
    [
        ("timepoints", "bulk", "eligible"),
        ("timepoints", "mixed", "eligible"),
        ("timepoints", "scrna", "blocked"),
        ("timepoints", "spatial", "blocked"),
        ("timepoints", "unknown", "blocked"),
        ("samples", "bulk", "blocked"),
        ("cells", "scrna", "blocked"),
        ("perturbations", "bulk", "blocked"),
        ("conditions", "bulk", "blocked"),
        ("spots", "spatial", "blocked"),
        ("metacells", "scrna", "blocked"),
    ],
)
def test_declared_expression_axes_use_existing_catalog_rules(
    tmp_path, kind, profile, expected
):
    entry = catalog_entry(tmp_path, column_kind=kind, expression_profile=profile)
    assert entry["status"] == expected
    assert bool(
        [issue for issue in entry["issues"] if issue["severity"] == "block"]
    ) == (expected == "blocked")


@pytest.mark.parametrize("group", sorted(CONSTRAINTS.taxonomic_groups))
def test_general_gene_algorithm_does_not_invent_species_restrictions(tmp_path, group):
    entry = catalog_entry(
        tmp_path,
        taxonomic_group=group,
        ncbi_taxon_id=None if group in {"synthetic", "unknown"} else 1,
    )
    assert entry["status"] == "eligible"


@pytest.mark.parametrize(
    ("present", "status", "code"),
    [
        ([], "blocked", "compatibility"),
        (["tf_list"], "blocked", "compatibility"),
        (["column_descriptors"], "warning", "catalog_warning"),
        (["column_descriptors", "tf_list"], "eligible", None),
    ],
)
def test_input_availability_uses_standard_required_optional_behavior(
    tmp_path, present, status, code
):
    entry = catalog_entry(tmp_path, extras={key: tmp_path / key for key in present})
    assert entry["status"] == status
    if code:
        assert any(issue["code"] == code for issue in entry["issues"])
    else:
        assert not entry["issues"]


def test_gene_minimum_is_a_declared_dimension_block(tmp_path):
    entry = catalog_entry(tmp_path, genes=1)
    assert entry["status"] == "blocked"
    assert any(("at least two" in issue["message"] for issue in entry["issues"]))


@pytest.fixture
def dataset(tmp_path):
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    return tmp_path


def request(dataset, *, params=None, mode="global", remove_extras=(), add_extras=()):
    manifest_path = dataset / "dataset-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for key in remove_extras:
        manifest["extras"].pop(key, None)
    for key, filename in add_extras:
        manifest["extras"][key] = filename
    manifest_path.write_text(json.dumps(manifest))
    params_path = dataset / "contract-tools-params.json"
    params_path.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "run_id": "pfbnet_review",
                        "tool_id": "pfbnet",
                        "params": params or {},
                        "execution": {"mode": mode},
                        "resources": {"threads": 1, "ram_gb": 2},
                    }
                ]
            }
        )
    )
    report = preflight_infer_network(
        dataset_manifest_path=manifest_path, tools_params_path=params_path
    )
    return (manifest_path, params_path, report)


def run_issues(report, *, severity=None):
    issues = report["runs"]["issues"].get("pfbnet_review", [])
    return [
        issue for issue in issues if severity is None or issue["severity"] == severity
    ]


def test_expression_workflow_uses_existing_requested_run_path(dataset):
    _, _, report = request(dataset, params={})
    assert report["runs"]["selected"] == ["pfbnet_review"]
    assert not run_issues(report, severity="block")
    assert report["runs"]["resolved_execution"]["pfbnet_review"] == {"mode": "global"}


def test_shared_tf_input_uses_existing_requested_run_path(dataset):
    _, _, report = request(dataset, params={}, add_extras=[("tf_list", "tf_list.txt")])
    assert report["runs"]["selected"] == ["pfbnet_review"]
    assert not run_issues(report, severity="block")
    entry = next(
        (item for item in report["catalog"]["eligible"] if item["tool_id"] == "pfbnet")
    )
    assert not entry["issues"]


@pytest.mark.parametrize(
    "params",
    [
        {"k": 0},
        {"k": 1.5},
        {"alpha": -0.1},
        {"alpha": 1.1},
        {"iter_num": 0},
        {"seed": -1},
        {"seed": 2147483648},
    ],
)
def test_invalid_public_parameter_values_block_requested_run(dataset, params):
    _, _, report = request(dataset, params=params)
    assert report["runs"]["selected"] == []
    assert any(
        (
            issue["code"] == "invalid_params"
            for issue in run_issues(report, severity="block")
        )
    )


@pytest.mark.parametrize("mode", ["group_native", "group_emulated"])
def test_unsupported_context_modes_block_without_invented_temporal_slicing(
    dataset, mode
):
    _, _, report = request(dataset, mode=mode)
    assert report["runs"]["selected"] == []
    assert any(
        (
            issue["code"] == "invalid_execution"
            for issue in run_issues(report, severity="block")
        )
    )


def test_requested_irregular_courses_block_without_affecting_catalog(dataset):
    _, _, before = request(dataset)
    path = dataset / "column_descriptors.tsv"
    with path.open() as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headings, rows = (reader.fieldnames, list(reader))
    temporal = next((row for row in rows if row["observation_type"] == "time_series"))
    temporal["timepoint"] = str(float(temporal["timepoint"]) + 0.1)
    with path.open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=headings, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    _, _, after = request(dataset)
    assert before["catalog"] == after["catalog"]
    assert before["runs"]["selected"] == ["pfbnet_review"]
    assert after["runs"]["selected"] == []
    assert any(
        (
            "regular sampling" in issue["message"]
            for issue in run_issues(after, severity="block")
        )
    )


def test_expression_only_ignores_technical_replicates_of_unused_stationary_observations(
    dataset,
):
    path = dataset / "column_descriptors.tsv"
    with path.open() as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headings, rows = (reader.fieldnames, list(reader))
    steady = [row for row in rows if row["observation_type"] == "steady_state"]
    for key in ("condition", "replicate", "observation_id"):
        steady[0][key] = steady[1][key]
    steady[0]["technical_replicate"] = "measurement-2"
    with path.open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=headings, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    _, _, report = request(dataset, params={})
    assert report["runs"]["selected"] == ["pfbnet_review"]
    assert not run_issues(report, severity="block")


def test_pseudotime_cannot_make_single_cells_compatible(dataset):
    manifest_path = dataset / "dataset-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["dataset"]["spec"]["expression"].update(
        column_kind="cells", expression_profile="scrna"
    )
    manifest["extras"]["pseudotime"] = "pseudotime.tsv"
    manifest_path.write_text(json.dumps(manifest))
    with (dataset / "expression.tsv").open() as handle:
        columns = next(csv.reader(handle, delimiter="\t"))[1:]
    (dataset / "pseudotime.tsv").write_text(
        "column\tpseudotime\n"
        + "".join((f"{column}\t{i}\n" for i, column in enumerate(columns)))
    )
    _, _, report = request(dataset)
    assert report["runs"]["selected"] == []
    assert any((item["tool_id"] == "pfbnet" for item in report["catalog"]["blocked"]))


def test_cost_deferral_uses_existing_unknown_cost_warning():
    profile, warnings = _load_tool_cost_profile(tools_root=TOOLS, tool_id="pfbnet")
    assert profile is None
    assert any(
        (
            "no cost.json" in warning and "fallback estimation" in warning
            for warning in warnings
        )
    )


def test_expression_only_plan_keeps_standard_runtime_firewall_and_missing_cost_warning(
    dataset,
):
    manifest, params, report = request(
        dataset, params={}, add_extras=[("tf_list", "tf_list.txt")]
    )
    run_dir = plan_infer_network(
        dataset_manifest_path=manifest,
        tools_params_path=params,
        output_dir=dataset / "plan-only",
        max_cores=1,
        max_ram_gb=2,
        planner="heuristic",
        preflight_report=report,
    )
    plan = json.loads((run_dir / "plan.json").read_text())
    contract = json.loads((run_dir / "input/runtime-input-contract.json").read_text())
    assert contract["runs"]["pfbnet_review"]["mounted_extra_inputs"] == [
        "column_descriptors",
        "tf_list",
    ]
    assert any(("no cost.json" in warning for warning in plan["warnings"]))
    assert plan["waves"][0]["tasks"][0]["eta_source"] == "fallback_no_cost"


def test_gui_bootstrap_exposes_same_spec_defaults_and_rules():
    from andrea.gui.infer_network.server import _load_tools_bootstrap

    bootstrap = _load_tools_bootstrap()
    tool = next((tool for tool in bootstrap["tools"] if tool["tool_id"] == "pfbnet"))
    assert tool["spec"] == SPEC
    assert tool["accepts"] == ["timepoints"]
    assert tool["required_extras"] == ["column_descriptors"]
    assert tool["optional_extras"] == ["tf_list"]
    assert tool["conditional_required_extras"] == []
    assert tool["compatibility_rules"] == SPEC["compatibility_rules"]
    assert tool["params_schema"] == SPEC["params"]
    assert tool["default_params"] == {
        name: definition["default"] for name, definition in SPEC["params"].items()
    }
