"""Prediction checks verify retained artifacts without running scientific tools."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "cost_predictions", ROOT / "wrappers/validate_cost_predictions.py"
)
predictions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(predictions)


@pytest.fixture
def evidence(tmp_path):
    def file(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return {"path": name, "sha256": predictions.digest(path)}

    identity = {
        "profile_id": "global_default",
        "image_id": "sha256:" + "a" * 64,
        "params_sha256": "b" * 64,
        "source_sha256": "c" * 64,
        "hardware_id": "test-host",
        "threads": 1,
        "ram_gb": 8,
    }

    def observation(name, genes, repeat=1):
        inputs = [{**file(f"input-{genes}.json", {"genes": genes}), "role": "primary"}]
        return {
            "id": name,
            "receipt": file(
                name + ".json",
                {
                    "seconds": 10.0,
                    "status": "ok",
                    "genes": genes,
                    "threads": 1,
                    "ram_gb": 8,
                    "repeat": repeat,
                    "case": name,
                    "run_id": name,
                    "identity": copy.deepcopy(identity),
                    "dimensions": {"genes": genes, "columns": 5},
                    "input_sha256": [item["sha256"] for item in inputs],
                },
            ),
            "identity": copy.deepcopy(identity),
            "dimensions": {"genes": genes, "columns": 5},
            "inputs": inputs,
            "outputs": [file(f"output-{name}.json", {"name": name})],
        }

    training = [observation(f"training-{genes}", genes) for genes in [10, 20, 30]]
    heldout = [observation(f"validation-{repeat}", 15, repeat) for repeat in [1, 2, 3]]
    case = {
        "id": "heldout-interpolation",
        "kind": "interpolation",
        "identity": identity,
        "dimensions": {"genes": 15, "columns": 5},
        "inputs": heldout[0]["inputs"],
        "predicted_typical_seconds": 10,
        "predicted_planning_seconds": 12,
        "max_relative_error": 0.2,
        "min_planning_coverage": 1.0,
    }
    plan = {
        "schema_version": 1,
        "cost": file("cost.json", {"profiles": []}),
        "estimator": file("estimator.json", {"model": "fixture"}),
        "training_observations": training,
        "cases": [case],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    measurements = {
        "plan_sha256": predictions.digest(plan_path),
        "cases": [{"id": case["id"], "observations": heldout}],
    }
    measurements_path = tmp_path / "observations.json"
    measurements_path.write_text(json.dumps(measurements))
    return plan_path, measurements_path, plan, measurements


def save(evidence):
    plan_path, measurements_path, plan, measurements = evidence
    plan_path.write_text(json.dumps(plan))
    measurements["plan_sha256"] = predictions.digest(plan_path)
    measurements_path.write_text(json.dumps(measurements))


def test_distinct_heldout_evidence_passes_and_explains_independence_limit(evidence):
    report = predictions.validate(*evidence[:2])
    assert report["passed"] and report["cases"][0]["seconds"] == [10, 10, 10]
    assert "chronology" in report["independence_limit"]


@pytest.mark.parametrize(
    "change,match",
    [
        ("receipt", "receipt overlap"),
        ("id", "receipt overlap"),
        ("input", "inventory was used"),
        ("size", "size or primary"),
        ("resource", "stratum"),
        ("kind", "interpolation/extrapolation"),
        ("repeats", "three successful"),
        ("calibration_role", "cannot be relabelled"),
    ],
)
def test_contamination_and_incompatible_strata_are_rejected(evidence, change, match):
    plan, measurements = evidence[2:]
    rows = measurements["cases"][0]["observations"]
    case = plan["cases"][0]
    if change == "receipt":
        rows[1]["receipt"] = rows[0]["receipt"]
    elif change == "id":
        rows[0]["id"] = plan["training_observations"][0]["id"]
    elif change == "input":
        case["inputs"] = plan["training_observations"][0]["inputs"]
        for row in rows:
            row["inputs"] = case["inputs"]
            mutate_receipt(
                evidence,
                row,
                lambda raw: raw.update(
                    input_sha256=[item["sha256"] for item in row["inputs"]]
                ),
            )
    elif change == "size":
        for row in plan["training_observations"]:
            if row["dimensions"]["genes"] == 10:
                row["dimensions"]["genes"] = 15
                path = evidence[0].parent / row["receipt"]["path"]
                raw = json.loads(path.read_text())
                raw["genes"] = 15
                raw["dimensions"]["genes"] = 15
                path.write_text(json.dumps(raw))
                row["receipt"]["sha256"] = predictions.digest(path)
    elif change == "resource":
        case["identity"]["threads"] = 2
    elif change == "kind":
        case["kind"] = "extrapolation"
    elif change == "repeats":
        rows.pop()
    else:
        path = evidence[0].parent / rows[0]["receipt"]["path"]
        raw = json.loads(path.read_text())
        raw["role"] = "calibration"
        path.write_text(json.dumps(raw))
        rows[0]["receipt"]["sha256"] = predictions.digest(path)
    save(evidence)
    with pytest.raises(ValueError, match=match):
        predictions.validate(*evidence[:2])


def test_hash_mutations_and_unfrozen_plan_fail(evidence):
    path = evidence[0].parent / evidence[2]["estimator"]["path"]
    path.write_text("changed")
    with pytest.raises(ValueError, match="changed evidence"):
        predictions.validate(*evidence[:2])


def test_accuracy_and_budget_are_separate_observed_gates(evidence):
    case = evidence[2]["cases"][0]
    case.update(predicted_typical_seconds=5, predicted_planning_seconds=6)
    save(evidence)
    report = predictions.validate(*evidence[:2])
    assert not report["passed"]
    assert report["cases"][0]["relative_error"] == 0.5
    assert report["cases"][0]["observed_planning_coverage"] == 0


def test_changed_frozen_plan_fails_even_with_unchanged_artifacts(evidence):
    evidence[0].write_text(evidence[0].read_text() + " ")
    with pytest.raises(ValueError, match="unchanged frozen plan"):
        predictions.validate(*evidence[:2])


def test_report_cannot_overwrite_calibration_artifact(evidence):
    target = evidence[0].parent / evidence[2]["cost"]["path"]
    original = target.read_bytes()
    with pytest.raises(SystemExit):
        predictions.main(
            [
                "--plan",
                str(evidence[0]),
                "--observations",
                str(evidence[1]),
                "--report",
                str(target),
            ]
        )
    assert target.read_bytes() == original


def test_copied_receipt_at_different_json_pointer_is_still_duplicate(evidence):
    rows = evidence[3]["cases"][0]["observations"]
    source = evidence[0].parent / rows[0]["receipt"]["path"]
    copied = evidence[0].parent / "copied.json"
    copied.write_text(json.dumps([json.loads(source.read_text())]))
    rows[1]["receipt"] = {
        "path": copied.name,
        "sha256": predictions.digest(copied),
        "pointer": "/0",
    }
    save(evidence)
    with pytest.raises(ValueError, match="receipt overlap"):
        predictions.validate(*evidence[:2])


def mutate_receipt(evidence, row, change):
    path = evidence[0].parent / row["receipt"]["path"]
    raw = json.loads(path.read_text())
    change(raw)
    path.write_text(json.dumps(raw))
    row["receipt"]["sha256"] = predictions.digest(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile_id", "wrong-profile"),
        ("params_sha256", "d" * 64),
        ("source_sha256", "e" * 64),
        ("hardware_id", "wrong-host"),
    ],
)
def test_receipt_cannot_contradict_any_identity_field(evidence, field, value):
    row = evidence[3]["cases"][0]["observations"][0]
    mutate_receipt(evidence, row, lambda raw: raw.update({field: value}))
    save(evidence)
    with pytest.raises(ValueError, match=f"contradicts declared {field}"):
        predictions.validate(*evidence[:2])


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_resources", {"threads": 99, "ram_gb": 999}),
        ("dimensions", {"genes": 999, "columns": 999}),
    ],
)
def test_nested_receipt_contradictions_fail_even_when_flat_fields_match(
    evidence, field, value
):
    row = evidence[3]["cases"][0]["observations"][0]
    mutate_receipt(evidence, row, lambda raw: raw.update({field: value}))
    save(evidence)
    with pytest.raises(ValueError, match="Receipt contradicts declared"):
        predictions.validate(*evidence[:2])


def test_adding_annotation_cannot_hide_reuse_of_training_primary_input(evidence):
    plan, measurements = evidence[2:]
    annotation = evidence[0].parent / "annotation.json"
    annotation.write_text("{}")
    plan["cases"][0]["inputs"] = plan["training_observations"][0]["inputs"] + [
        {
            "path": annotation.name,
            "sha256": predictions.digest(annotation),
            "role": "shared",
        }
    ]
    for row in measurements["cases"][0]["observations"]:
        row["inputs"] = plan["cases"][0]["inputs"]
        mutate_receipt(
            evidence,
            row,
            lambda raw: raw.update(
                input_sha256=[item["sha256"] for item in row["inputs"]]
            ),
        )
    save(evidence)
    with pytest.raises(ValueError, match="primary input inventory was used"):
        predictions.validate(*evidence[:2])


def test_distinct_receipt_rows_cannot_reuse_one_execution(evidence):
    for row in evidence[3]["cases"][0]["observations"]:
        mutate_receipt(
            evidence, row, lambda raw: raw.update(run_id="same-one-execution")
        )
    save(evidence)
    with pytest.raises(ValueError, match="receipt overlap"):
        predictions.validate(*evidence[:2])


@pytest.mark.parametrize(
    "missing", ["run_id", "identity", "dimensions", "input_sha256"]
)
def test_receipt_requires_observed_execution_identity_and_dimensions(evidence, missing):
    row = evidence[3]["cases"][0]["observations"][0]
    mutate_receipt(evidence, row, lambda raw: raw.pop(missing))
    save(evidence)
    with pytest.raises(ValueError, match="requires|missing|inventory"):
        predictions.validate(*evidence[:2])


def test_shared_annotations_can_recur_with_distinct_primary_workloads(evidence):
    annotation = evidence[0].parent / "shared-annotation.json"
    annotation.write_text("{}")
    ref = {
        "path": annotation.name,
        "sha256": predictions.digest(annotation),
        "role": "shared",
    }
    evidence[2]["cases"][0]["inputs"].append(ref)
    for row in (
        evidence[2]["training_observations"] + evidence[3]["cases"][0]["observations"]
    ):
        if ref not in row["inputs"]:
            row["inputs"].append(ref)
        mutate_receipt(
            evidence,
            row,
            lambda raw: raw.update(
                input_sha256=[item["sha256"] for item in row["inputs"]]
            ),
        )
    save(evidence)
    assert predictions.validate(*evidence[:2])["passed"]


def test_adapted_receipt_cannot_contradict_original_timing(evidence):
    original = evidence[0].parent / "original-timing.json"
    original.write_text(json.dumps({"status": "ok", "seconds": 999}))
    row = evidence[3]["cases"][0]["observations"][0]
    mutate_receipt(
        evidence,
        row,
        lambda raw: raw.update(
            raw_evidence=[
                {"path": original.name, "sha256": predictions.digest(original)}
            ]
        ),
    )
    save(evidence)
    with pytest.raises(ValueError, match="contradicts declared seconds"):
        predictions.validate(*evidence[:2])


def test_malformed_json_object_contract_still_writes_failing_report(evidence):
    evidence[0].write_text("[]")
    report = evidence[0].parent / "invalid-report.json"
    assert (
        predictions.main(
            [
                "--plan",
                str(evidence[0]),
                "--observations",
                str(evidence[1]),
                "--report",
                str(report),
            ]
        )
        == 1
    )
    assert json.loads(report.read_text())["passed"] is False
