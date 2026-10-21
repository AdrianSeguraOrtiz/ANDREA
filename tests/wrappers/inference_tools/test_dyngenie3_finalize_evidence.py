"""Acceptance failures must stop before Docker; these are synthetic receipts only."""

from __future__ import annotations

import importlib.util
import csv
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "wrappers/inference_tools/tools/dyngenie3"
spec = importlib.util.spec_from_file_location(
    "dyngenie3_finalizer", SOURCE / "finalize_evidence.py"
)
finalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finalizer)
IMAGE = "sha256:" + "a" * 64
SOURCE_SHA = "b" * 64


def test_finalizer_rejects_rounded_xml_weights_without_running_inference(tmp_path):
    from andrea.core.commands.infer_network.commons.network_exports import (
        MERGED_NETWORK_REQUIRED_COLUMNS,
        export_network_graphml,
        export_network_gexf,
        GEXF_NS,
    )

    source = tmp_path / "merged_network_raw.csv"
    with source.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=MERGED_NETWORK_REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "source": "0002",
                "target": "TF:01",
                "score": "0.12345678912345678",
                "sign": "?",
                "evidence": "association",
                "context": "global",
                "tool_id": "dyngenie3__01",
            }
        )
    export_network_graphml(source, tmp_path / "merged_network_raw.graphml")
    target = tmp_path / "merged_network_raw.gexf"
    export_network_gexf(source, target)
    assert finalizer.verify_raw_graph_exports(tmp_path)["status"] == "passed"
    document = ET.parse(target)
    document.find(f".//{{{GEXF_NS}}}edge").set("weight", "0.123456789123")
    document.write(target, encoding="utf-8", xml_declaration=True)
    with pytest.raises(ValueError, match="precision changed"):
        finalizer.verify_raw_graph_exports(tmp_path)


def json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def review_fixture(directory):
    outputs = []
    for role in ("lightweight_tests", "gui_browser", "scientific_audit"):
        path = json_file(
            directory / (role + ".json"),
            {"status": "passed", "synthetic_test_evidence": True},
        )
        outputs.append({"role": role, "path": str(path), "sha256": finalizer.sha(path)})
    path = json_file(
        directory / "review.json",
        {
            "status": "passed",
            "exit_code": 0,
            "source_sha256": SOURCE_SHA,
            "outputs": outputs,
        },
    )
    return path


def test_retry_preserves_every_retained_acceptance_file(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    retained = json_file(
        campaign / "acceptance/acceptance.json",
        {"status": "failed", "original_attempt": True},
    )
    before = retained.read_bytes()
    monkeypatch.setattr(
        finalizer,
        "external_receipt",
        lambda *_: pytest.fail("Retry reached execution gates"),
    )
    assert (
        finalizer.main(
            [
                "--campaign",
                str(campaign),
                "--image",
                IMAGE,
                "--external-state",
                str(tmp_path / "external.json"),
            ]
        )
        == 2
    )
    assert retained.read_bytes() == before


def test_review_requires_the_exact_frozen_source_hash(tmp_path):
    review = review_fixture(tmp_path)
    with pytest.raises(ValueError, match="stale"):
        finalizer.verify_review(review, "c" * 64)


@pytest.mark.parametrize(
    "role", ["lightweight_tests", "gui_browser", "scientific_audit"]
)
def test_review_rejects_each_missing_required_evidence_role(role, tmp_path):
    path = review_fixture(tmp_path)
    review = json.loads(path.read_text())
    review["outputs"] = [row for row in review["outputs"] if row["role"] != role]
    json_file(path, review)
    with pytest.raises(ValueError, match="lacks"):
        finalizer.verify_review(path, SOURCE_SHA)


@pytest.mark.parametrize("change", ["tamper", "remove"])
def test_review_rejects_tampered_or_missing_output_files(change, tmp_path):
    review = review_fixture(tmp_path)
    output = tmp_path / "scientific_audit.json"
    if change == "tamper":
        output.write_text("modified evidence after source review")
    else:
        output.unlink()
    with pytest.raises(ValueError, match="changed"):
        finalizer.verify_review(review, SOURCE_SHA)


@pytest.mark.parametrize("field,value", [("status", "failed"), ("exit_code", 1)])
def test_review_rejects_failed_status_and_nonzero_exit(field, value, tmp_path):
    path = review_fixture(tmp_path)
    review = json.loads(path.read_text())
    review[field] = value
    json_file(path, review)
    with pytest.raises(ValueError):
        finalizer.verify_review(path, SOURCE_SHA)


@pytest.mark.parametrize(
    "failure,match",
    [
        ("external", "External experiment"),
        ("sources", "sources changed"),
        ("review", "stale"),
        ("functional_hash", "Functional receipt changed"),
        ("functional_image", "Evidence image differs"),
        ("coverage", "Cost domain"),
        ("structural", "Cost structure"),
        ("prediction", "Evidence has not passed"),
    ],
)
def test_main_early_failures_cannot_invoke_docker_or_inference(
    failure, match, tmp_path, monkeypatch
):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    external = json_file(tmp_path / "external.json", {"status": "completed"})
    sources = {"reviewed.py": "frozen-source-hash"}
    json_file(
        campaign / "freeze.json",
        {"identity": {"sources": sources}, "source_sha256": SOURCE_SHA},
    )
    review_path = review_fixture(campaign)
    functional = json_file(
        campaign / "functional/verification.json",
        {"status": "passed", "image_id": IMAGE},
    )
    json_file(
        campaign / "predictions/prediction-report.json",
        {"status": "passed", "image_id": IMAGE},
    )
    state = {
        "image_id": IMAGE,
        "functional": {"receipt": str(functional), "sha256": finalizer.sha(functional)},
        "coverage": {"status": "passed", "structural_provenance_exit_code": 0},
    }
    if failure == "external":
        json_file(external, {"status": "running", "active": True})
    elif failure == "sources":
        sources = {"reviewed.py": "changed-source-hash"}
    elif failure == "review":
        review = json.loads(review_path.read_text())
        review["source_sha256"] = "c" * 64
        json_file(review_path, review)
    elif failure == "functional_hash":
        state["functional"]["sha256"] = "c" * 64
    elif failure == "functional_image":
        json_file(functional, {"status": "passed", "image_id": "sha256:" + "c" * 64})
        state["functional"]["sha256"] = finalizer.sha(functional)
    elif failure == "coverage":
        state["coverage"]["status"] = "pending"
    elif failure == "structural":
        state["coverage"]["structural_provenance_exit_code"] = 1
    else:
        json_file(
            campaign / "predictions/prediction-report.json",
            {"status": "failed", "image_id": IMAGE},
        )
    json_file(campaign / "state.json", state)
    monkeypatch.setattr(finalizer, "ROOT", tmp_path / "repo")
    monkeypatch.setattr(
        finalizer,
        "Supervisor",
        lambda **kwargs: SimpleNamespace(source_inventory=lambda: sources),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Early failed acceptance gate reached subprocess/Docker")

    monkeypatch.setattr(finalizer, "command", forbidden)
    monkeypatch.setattr(finalizer, "inspect_image", forbidden)
    monkeypatch.setattr(finalizer.subprocess, "Popen", forbidden)
    monkeypatch.setattr(finalizer.subprocess, "check_output", forbidden)
    assert (
        finalizer.main(
            [
                "--campaign",
                str(campaign),
                "--image",
                IMAGE,
                "--external-state",
                str(external),
            ]
        )
        == 2
    )
    report = json.loads((campaign / "acceptance/acceptance.json").read_text())
    assert (
        report["status"] == "failed"
        and not report["published"]
        and match in report["error"]
    )


@pytest.mark.parametrize(
    "checker", ["validate_tool_costs.py", "validate_cost_predictions.py"]
)
def test_passed_summary_flags_do_not_bypass_real_cost_or_prediction_checker_failure(
    checker, tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    tool = root / "wrappers/inference_tools/tools/dyngenie3"
    catalog = root / "andrea/catalog_inference_tools/tools/dyngenie3"
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    external = json_file(tmp_path / "external.json", {"status": "completed"})
    sources = {"reviewed.py": "frozen-source-hash"}
    json_file(
        campaign / "freeze.json",
        {"identity": {"sources": sources}, "source_sha256": SOURCE_SHA},
    )
    review_fixture(campaign)
    functional = json_file(
        campaign / "functional/verification.json",
        {"status": "passed", "image_id": IMAGE},
    )
    json_file(
        campaign / "predictions/prediction-report.json",
        {"status": "passed", "image_id": IMAGE},
    )
    cost = json_file(catalog / "cost.json", {"synthetic_test_evidence": True})
    spec = {"docker_image": "local/dyngenie3:synthetic", "params": {}}
    json_file(catalog / "toolspec.json", spec)
    json_file(tool / "toolspec.draft.json", spec)
    json_file(
        campaign / "state.json",
        {
            "image_id": IMAGE,
            "functional": {
                "receipt": str(functional),
                "sha256": finalizer.sha(functional),
            },
            "coverage": {"status": "passed", "structural_provenance_exit_code": 0},
            "calibration": {"cost_sha256": finalizer.sha(cost)},
        },
    )
    monkeypatch.setattr(finalizer, "ROOT", root)
    monkeypatch.setattr(finalizer, "HERE", tool)
    monkeypatch.setattr(
        finalizer,
        "Supervisor",
        lambda **kwargs: SimpleNamespace(source_inventory=lambda: sources),
    )
    monkeypatch.setattr(finalizer, "inspect_image", lambda *args: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Synthetic acceptance test invoked a real subprocess")

    monkeypatch.setattr(finalizer.subprocess, "Popen", forbidden)
    monkeypatch.setattr(finalizer.subprocess, "check_output", forbidden)
    commands = []

    def mocked_checker(args, out):
        commands.append(args)
        if checker in args[1]:
            raise ValueError("Real checker failed: " + checker)
        if "-m" in args:
            pytest.fail("Cost/prediction checker failure reached CLI inference")

    monkeypatch.setattr(finalizer, "command", mocked_checker)
    assert (
        finalizer.main(
            [
                "--campaign",
                str(campaign),
                "--image",
                IMAGE,
                "--external-state",
                str(external),
            ]
        )
        == 2
    )
    report = json.loads((campaign / "acceptance/acceptance.json").read_text())
    assert (
        report["status"] == "failed"
        and "Real checker failed: " + checker == report["error"]
    )
    assert not (campaign / "acceptance/gate.json").exists()
    assert not (campaign / "acceptance/receipts").exists()


def predictive_panel_fixture(directory):
    declarations = [
        {
            "id": "default75",
            "profile_id": "rf-default",
            "genes": 75,
            "columns": 16,
            "timepoints_per_series": 8,
            "independent_series": 2,
            "seed": 123,
            "kind": "interpolation",
        },
        {
            "id": "quality250",
            "profile_id": "rf-quality",
            "genes": 250,
            "columns": 16,
            "timepoints_per_series": 8,
            "independent_series": 2,
            "seed": 456,
            "kind": "extrapolation",
        },
    ]
    resources = [{"threads": 1, "ram_gb": 4}, {"threads": 8, "ram_gb": 8}]
    specification = {
        "holdouts": declarations,
        "resources": resources,
        "planned_run_count": 12,
        "scope": "Synthetic finite test domain",
        "repeats": 3,
        "max_relative_error": 0.35,
        "min_planning_coverage": 1.0,
    }
    cases, predictions = [], {}
    for declaration in declarations:
        for resource in resources:
            name = declaration["id"] + str(resource["threads"])
            cases.append(
                {
                    "id": name,
                    "reservation_id": declaration["id"],
                    "identity": {
                        **resource,
                        "profile_id": declaration["profile_id"],
                        "image_id": IMAGE,
                    },
                    "kind": declaration["kind"],
                    "generator_seed": declaration["seed"],
                    "dimensions": {
                        key: declaration[key]
                        for key in (
                            "genes",
                            "columns",
                            "timepoints_per_series",
                            "independent_series",
                        )
                    },
                    "predicted_typical_seconds": 8,
                    "predicted_planning_seconds": 10,
                    "operating_reserve_seconds": 12.5,
                    "max_relative_error": 0.35,
                    "min_planning_coverage": 1.0,
                }
            )
            predictions[name] = {
                "predicted_typical_seconds": 8,
                "predicted_planning_seconds": 10,
                "operating_reserve_seconds": 12.5,
                "core_provenance": {
                    "cost_profile": {"profile_id": declaration["profile_id"]}
                },
            }
    plan = {"cases": cases, "planned_run_count": 12}
    estimator = {"kind": "frozen_andrea_planner_calls", "predictions": predictions}
    json_file(directory / "prediction-plan.json", plan)
    json_file(directory / "estimator.json", estimator)
    report = {
        "status": "passed",
        "image_id": IMAGE,
        "planned_run_count": 12,
        "observed_run_count": 12,
        "successful_run_count": 12,
    }
    return specification, plan, estimator, report


def write_predictive_receipts(directory, plan, estimator, report):
    json_file(directory / "prediction-plan.json", plan)
    json_file(directory / "estimator.json", estimator)
    report.update(
        plan_sha256=finalizer.sha(directory / "prediction-plan.json"),
        estimator_sha256=finalizer.sha(directory / "estimator.json"),
    )
    json_file(directory / "prediction-report.json", report)
    json_file(
        directory / "observations.json",
        {
            "plan_sha256": finalizer.sha(directory / "prediction-plan.json"),
            "cases": [
                {"id": case["id"], "observations": [{}, {}, {}]}
                for case in plan["cases"]
            ],
        },
    )


def test_finalizer_scope_checks_every_reserved_recipe_resource_case(tmp_path):
    specification, plan, estimator, report = predictive_panel_fixture(tmp_path)
    write_predictive_receipts(tmp_path, plan, estimator, report)
    checked = finalizer.verify_prediction_scope(
        tmp_path, IMAGE, specification=specification
    )
    assert checked["reserved_case_count"] == 2
    assert checked["observed_run_count"] == 12
    assert checked["profile_ids"] == ["rf-default", "rf-quality"]


@pytest.mark.parametrize(
    "change",
    [
        "missing_case",
        "duplicate_case",
        "seed",
        "profile",
        "partial_run_count",
        "core_profile",
        "stale_frozen_hash",
        "extra_observation",
    ],
)
def test_finalizer_rejects_a_passed_subset_or_relabelled_prediction_panel(
    change, tmp_path
):
    specification, plan, estimator, report = predictive_panel_fixture(tmp_path)
    if change == "missing_case":
        plan["cases"].pop()
    elif change == "duplicate_case":
        plan["cases"][-1] = plan["cases"][0]
    elif change == "seed":
        plan["cases"][0]["generator_seed"] += 1
    elif change == "profile":
        plan["cases"][0]["identity"]["profile_id"] = "unreserved"
    elif change == "partial_run_count":
        report["successful_run_count"] = 3
    elif change == "core_profile":
        estimator["predictions"][plan["cases"][0]["id"]]["core_provenance"][
            "cost_profile"
        ]["profile_id"] = "wrong-profile"
    write_predictive_receipts(tmp_path, plan, estimator, report)
    if change == "stale_frozen_hash":
        json_file(tmp_path / "estimator.json", {**estimator, "changed": True})
    elif change == "extra_observation":
        observed = json.loads((tmp_path / "observations.json").read_text())
        observed["cases"][0]["observations"].append({})
        json_file(tmp_path / "observations.json", observed)
    with pytest.raises(ValueError):
        finalizer.verify_prediction_scope(tmp_path, IMAGE, specification=specification)
