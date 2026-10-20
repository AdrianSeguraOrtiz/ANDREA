"""Acceptance failures must stop before Docker; these are synthetic receipts only."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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
