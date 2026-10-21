"""Analytic receipt fixtures and mocked containers; no measured science or Docker."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import types
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "wrappers/inference_tools/tools/dyngenie3"
spec = importlib.util.spec_from_file_location(
    "dyngenie3_predictions", SOURCE / "validate_runtime_predictions.py"
)
predictions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(predictions)
IMAGE = "sha256:" + "a" * 64


READ_RESERVED_CASES = predictions.reserved_cases


@pytest.fixture(autouse=True)
def default_reserved_cases(monkeypatch):
    cases = [
        {
            "id": "default_gene_interpolation_t8_s2",
            "profile_id": predictions.PROFILE_ID,
            "genes": 75,
            "columns": 16,
            "timepoints_per_series": 8,
            "independent_series": 2,
            "seed": 765431,
            "kind": "interpolation",
        },
        {
            "id": "default_gene_extrapolation_t8_s2",
            "profile_id": predictions.PROFILE_ID,
            "genes": 250,
            "columns": 16,
            "timepoints_per_series": 8,
            "independent_series": 2,
            "seed": 765439,
            "kind": "extrapolation",
        },
    ]
    monkeypatch.setattr(predictions, "reserved_cases", lambda: copy.deepcopy(cases))
    return cases


def test_reviewed_expanded_reservation_is_finite_and_independent():
    cases = READ_RESERVED_CASES()
    assert len(cases) == 13
    assert len({row["id"] for row in cases}) == 13
    assert {row["genes"] for row in cases} == {75, 250}
    assert len(predictions.RESOURCES) == 8
    assert len(cases) * len(predictions.RESOURCES) * predictions.REPEATS == 312
    assert not any("vector" in row["profile_id"] for row in cases)
    assert any("irregular" in row["profile_id"] for row in cases)


def test_support_isolates_inference_helpers_from_foreign_script_imports(monkeypatch):
    shared = types.ModuleType("shared")
    shared.__path__ = [str(ROOT / "wrappers/simulation_data_tools/scripts/shared")]
    foreign_backend = types.ModuleType("benchmark_costs")
    monkeypatch.setitem(sys.modules, "shared", shared)
    monkeypatch.setitem(sys.modules, "benchmark_costs", foreign_backend)
    evidence, backend, _ = predictions._support()
    assert sys.modules["shared"] is shared
    assert sys.modules["benchmark_costs"] is foreign_backend
    assert evidence.__name__.startswith("_dyngenie3_inference_support.")
    assert (
        Path(backend.__file__).resolve()
        == ROOT / "wrappers/inference_tools/scripts/benchmark_costs.py"
    )
    assert backend.input_manifest.__module__.startswith("_dyngenie3_inference_support.")


def arguments(tmp_path, *, plan_only=False):
    return argparse.Namespace(
        calibration_root=tmp_path / "calibration",
        cost=tmp_path / "catalog/dyngenie3/cost.json",
        image=IMAGE,
        out=tmp_path / "predictions",
        external_state=tmp_path / "external.json",
        plan_only=plan_only,
    )


def test_plan_only_has_no_fit_import_or_subprocess_and_declares_scope(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        predictions,
        "_support",
        lambda: pytest.fail("Plan-only imported a scientific backend"),
    )
    monkeypatch.setattr(
        predictions,
        "planner_prediction",
        lambda **_: pytest.fail("Plan-only evaluated scientific predictions"),
    )
    monkeypatch.setattr(
        predictions.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("Plan-only started a subprocess"),
    )
    args = arguments(tmp_path, plan_only=True)
    report = predictions.campaign(args)
    protocol = json.loads((args.out / "protocol.json").read_text())
    assert (
        report["status"] == "planned_unmeasured" and report["observed_run_count"] == 0
    )
    assert protocol["planned_run_count"] == 48 and protocol["repeats"] == 3
    assert [case["genes"] for case in protocol["holdouts"]] == [75, 250]
    assert (
        protocol["planning_reserve"] == 1.25 and protocol["max_relative_error"] == 0.35
    )
    assert not (args.out / "estimator.json").exists()


@pytest.mark.parametrize(
    "state",
    [
        {"status": "running"},
        {"status": "failed"},
        {"status": "completed", "active": True},
        {"status": "completed", "active": "false"},
    ],
)
def test_external_state_blocks_before_training_fit_or_container(
    tmp_path, monkeypatch, state
):
    args = arguments(tmp_path)
    args.external_state.write_text(json.dumps(state))
    monkeypatch.setattr(
        predictions,
        "_support",
        lambda: pytest.fail("External active experiment reached training"),
    )
    report = predictions.campaign(args)
    assert report["status"] == "blocked" and report["observed_run_count"] == 0
    assert not (args.out / "estimator.json").exists()


@pytest.fixture
def retained_training(tmp_path):
    evidence, backend, checks = predictions._support()
    args = arguments(tmp_path)
    args.external_state.write_text('{"status":"completed","active":false}')
    recipe = json.loads(
        (ROOT / "wrappers/inference_tools/cost_profiles/dyngenie3.json").read_text()
    )
    params = {
        key: value["default"]
        for key, value in json.loads((SOURCE / "toolspec.draft.json").read_text())[
            "params"
        ].items()
    }
    profile_config = next(
        row for row in recipe["profiles"] if row["id"] == predictions.PROFILE_ID
    )
    input_profile = {
        "extras_provided": ["timepoints", "replicates"],
        "column_kind": "samples",
        "gene_id_source": "synthetic",
        "expression_profile": recipe["expression_profile"],
        "temporal": recipe["temporal"],
        "temporal_fixture": profile_config["temporal_fixture"],
    }
    provenance = {
        "image_id": IMAGE,
        "source_sha256": "b" * 64,
        "cpu_model": "analytic-fixture-cpu",
        "platform": "analytic-test",
        "logical_cpus": 8,
        "host_memory_bytes": 32 * 1024**3,
        "docker_server": "fixture",
        "seed": 12345,
        "timing_scope": "docker run wall time including container startup and output writing",
    }
    config = {
        "input_profile": input_profile,
        "params_profile": {"resolved_params": params},
        "provenance": provenance,
        "execution_profile": {
            "mode": "global",
            "physical_task_policy": "single",
            "group_count": 0,
        },
    }
    directory = args.calibration_root / "dyngenie3-fixture" / predictions.PROFILE_ID
    directory.mkdir(parents=True)
    config_path = directory / "benchmark_config.json"
    config_path.write_text(json.dumps(config))
    rows, points = [], []
    for genes in predictions.TRAINING_GENES:
        for threads, ram in predictions.RESOURCES:
            point_rows = []
            for repeat in range(1, 4):
                run_id = f"fixture-g{genes}-t{threads}-m{ram}-r{repeat}"
                io = directory / run_id / "io"
                backend.prepare_io_dir(
                    io,
                    backend.SizePoint(genes, 16),
                    resolved_params=params,
                    execution={"mode": "global"},
                    seed=12345,
                    input_profile=input_profile,
                )
                (io / "out/network.csv").write_text(
                    "source,target,score,sign,evidence,context\nG1,G2,1,?,association,global\n"
                )
                row = {
                    "run_id": run_id,
                    "status": "ok",
                    "seconds": genes / 10 * (1 + repeat / 100) / threads,
                    "genes": genes,
                    "columns": 16,
                    "threads": threads,
                    "ram_gb": ram,
                    "repeat": repeat,
                    "io_dir": str(io),
                    "dimensions": {
                        "genes": genes,
                        "columns": 16,
                        "timepoints_per_series": 8,
                        "independent_series": 2,
                    },
                    "input_sha256": backend.input_manifest(io),
                }
                rows.append(row)
                point_rows.append(row)
            points.append(
                {
                    "genes": genes,
                    "columns": 16,
                    "threads": threads,
                    "ram_gb": ram,
                    "status": "ok",
                    "repeats_total": 3,
                    "repeats_ok": 3,
                    "repeats_failed": 0,
                    "ok_rate": 1.0,
                    "failure_breakdown": {"oom": 0, "timeout": 0, "error": 0},
                    "seconds_p50": genes / 10 * 1.02 / threads,
                    "seconds_p90": genes / 10 * 1.038 / threads,
                    "measurement_evidence": {"observations": point_rows},
                }
            )
    (directory / "measurements.json").write_text(json.dumps(rows))
    args.cost.parent.mkdir(parents=True)
    shutil.copy2(SOURCE / "toolspec.draft.json", args.cost.parent / "toolspec.json")
    profile = {
        "profile_id": predictions.PROFILE_ID,
        "benchmark_config": config,
        "runtime_points": points,
    }
    args.cost.write_text(json.dumps({"schema_version": "1.0", "profiles": [profile]}))
    return SimpleNamespace(
        args=args,
        evidence=evidence,
        backend=backend,
        checks=checks,
        config=config,
        rows=rows,
        profile=profile,
    )


def mocked_execution(
    retained, monkeypatch, *, fail=False, change_state=False, mutate_plan=False
):
    calls = []
    backend, args = retained.backend, retained.args
    runtime = predictions._runtime()
    monkeypatch.setattr(
        runtime, "validate_outputs", lambda *_args: {"fixture_checks": "mocked"}
    )
    monkeypatch.setattr(predictions, "_runtime", lambda: runtime)
    monkeypatch.setattr(
        backend, "collect_provenance", lambda **_: retained.config["provenance"]
    )

    def run(**kwargs):
        assert (args.out / "prediction-plan.json").is_file()
        assert (args.out / "estimator.json").is_file()
        assert kwargs["image_tag"] == IMAGE
        calls.append(kwargs)
        genes = len((kwargs["io_dir"] / "expression.tsv").read_text().splitlines()) - 1
        (kwargs["io_dir"] / "out/network.csv").write_text(
            "source,target,score,sign,evidence,context\nG1,G2,1,?,association,global\n"
        )
        if change_state:
            args.external_state.write_text('{"status":"running"}')
        if mutate_plan:
            (args.out / "estimator.json").write_text('{"changed":true}')
        return (
            ("timeout", 1, "fixture timeout")
            if fail
            else ("ok", genes / 10 * 1.02 / kwargs["threads"], "")
        )

    monkeypatch.setattr(backend, "run_container_once", run)
    return calls


def test_full_mock_campaign_freezes_before_48_runs_and_generic_checker_passes(
    retained_training, monkeypatch
):
    retained = retained_training
    calls = mocked_execution(retained, monkeypatch)
    report = predictions.campaign(retained.args)
    assert report["status"] == "passed", report
    assert (
        len(calls)
        == report["successful_run_count"]
        == report["observed_run_count"]
        == 48
    )
    assert report["generic_validation"]["passed"]
    assert len(report["generic_validation"]["cases"]) == 16
    assert report["plan_sha256"] == predictions._sha(
        retained.args.out / "prediction-plan.json"
    )
    assert report["estimator_sha256"] == predictions._sha(
        retained.args.out / "estimator.json"
    )
    estimator = json.loads((retained.args.out / "estimator.json").read_text())
    plan = json.loads((retained.args.out / "prediction-plan.json").read_text())
    assert estimator["kind"] == "frozen_andrea_planner_calls"
    assert len(estimator["predictions"]) == 16
    for case in plan["cases"]:
        frozen = estimator["predictions"][case["id"]]
        assert (
            case["predicted_planning_seconds"] == frozen["predicted_planning_seconds"]
        )
        assert (
            case["operating_reserve_seconds"]
            == case["predicted_planning_seconds"] * 1.25
        )
        assert (
            frozen["core_provenance"]["cost_profile"]["profile_id"]
            == predictions.PROFILE_ID
        )
        assert frozen["core_provenance"]["cost_profile"]["size_model"]["method"] == (
            "measured_axis_interpolation"
            if case["kind"] == "interpolation"
            else "measured_axis_extrapolation"
        )


def test_failure_is_retained_and_never_retried(retained_training, monkeypatch):
    calls = mocked_execution(retained_training, monkeypatch, fail=True)
    report = predictions.campaign(retained_training.args)
    assert report["status"] == "failed" and len(calls) == 1
    receipt = json.loads(
        next((retained_training.args.out / "runs").rglob("receipt.json")).read_text()
    )
    assert receipt["status"] == "timeout" and receipt["error"] == "fixture timeout"
    with pytest.raises(ValueError, match="new directory"):
        predictions.campaign(retained_training.args)


def test_state_is_checked_between_runs_and_frozen_mutation_stops_next_run(
    retained_training, monkeypatch
):
    calls = mocked_execution(retained_training, monkeypatch, change_state=True)
    report = predictions.campaign(retained_training.args)
    assert report["status"] == "blocked" and len(calls) == 1
    assert len(list((retained_training.args.out / "runs").rglob("receipt.json"))) == 2


def test_frozen_estimator_change_stops_next_run(retained_training, monkeypatch):
    calls = mocked_execution(retained_training, monkeypatch, mutate_plan=True)
    report = predictions.campaign(retained_training.args)
    assert report["status"] == "failed" and len(calls) == 1
    assert "changed" in report["error"]


def test_training_receipts_cannot_be_missing_or_relabelled(
    retained_training, monkeypatch
):
    retained = retained_training
    monkeypatch.setattr(
        retained.backend,
        "run_container_once",
        lambda **_: pytest.fail("Invalid calibration reached held-outs"),
    )
    first_io = Path(retained.rows[0]["io_dir"])
    (first_io / "expression.tsv").write_text("gene\tc1\nG1\t1\n")
    report = predictions.campaign(retained.args)
    assert report["status"] == "failed" and report["observed_run_count"] == 0
    assert "input inventory" in report["error"]


def test_estimator_declines_heldout_genes_in_training(retained_training):
    entries = [{"raw_row": copy.deepcopy(row)} for row in retained_training.rows]
    entries[0]["raw_row"]["genes"] = 75
    with pytest.raises(ValueError, match="50/100/200"):
        predictions.validate_training_panel(entries)


def _add_retained_recipe(retained, definition):
    """Analytic raw receipts for one real declared recipe; never fit a model."""
    config = copy.deepcopy(retained.config)
    config["params_profile"]["resolved_params"].update(
        definition.get("param_overrides", {})
    )
    relevant = [
        "tree_method",
        "K",
        "ntrees",
        "alpha",
        "compute_quality_scores",
        "save_models",
    ]
    config["params_profile"]["cost_relevant_params"] = relevant
    config["params_profile"]["cost_relevant_values"] = {
        key: config["params_profile"]["resolved_params"][key] for key in relevant
    }
    config["input_profile"]["temporal_fixture"] = definition["temporal_fixture"]
    config["input_profile"]["extras_provided"] = [
        "timepoints",
        "replicates",
        *definition.get("optional_inputs", []),
    ]
    config["input_profile"]["optional_inputs_provided"] = definition.get(
        "optional_inputs", []
    )
    name = definition["id"]
    directory = retained.args.calibration_root / "dyngenie3-fixture" / name
    directory.mkdir(parents=True)
    (directory / "benchmark_config.json").write_text(json.dumps(config))
    rows, points = [], []
    for point in retained.profile["runtime_points"]:
        point = copy.deepcopy(point)
        observations = []
        for original in point["measurement_evidence"]["observations"]:
            row = copy.deepcopy(original)
            row["run_id"] += "-" + name
            io = directory / row["run_id"] / "io"
            retained.backend.prepare_io_dir(
                io,
                retained.backend.SizePoint(row["genes"], 16),
                resolved_params=config["params_profile"]["resolved_params"],
                execution={"mode": "global"},
                seed=12345,
                input_profile=config["input_profile"],
            )
            (io / "out/network.csv").write_text(
                "source,target,score,sign,evidence,context\nG1,G2,1,?,association,global\n"
            )
            row["io_dir"] = str(io)
            row["input_sha256"] = retained.backend.input_manifest(io)
            ss = definition["temporal_fixture"]["steady_state_samples"]
            if ss:
                row["dimensions"]["steady_state_samples"] = ss
            observations.append(row)
            rows.append(row)
        point["measurement_evidence"]["observations"] = observations
        points.append(point)
    (directory / "measurements.json").write_text(json.dumps(rows))
    return {"profile_id": name, "benchmark_config": config, "runtime_points": points}


def test_all13_reserved_recipes_freeze_actual_core_predictions_before312_mock_runs(
    retained_training, monkeypatch
):
    retained = retained_training
    declarations = READ_RESERVED_CASES()
    manifest = json.loads(predictions.RESERVATION_PATH.read_text())
    definitions = {row["id"]: row for row in manifest["profiles"]}
    relevant = [
        "tree_method",
        "K",
        "ntrees",
        "alpha",
        "compute_quality_scores",
        "save_models",
    ]
    retained.config["params_profile"]["cost_relevant_params"] = relevant
    retained.config["params_profile"]["cost_relevant_values"] = {
        key: retained.config["params_profile"]["resolved_params"][key]
        for key in relevant
    }
    first_config = (
        retained.args.calibration_root
        / "dyngenie3-fixture"
        / predictions.PROFILE_ID
        / "benchmark_config.json"
    )
    first_config.write_text(json.dumps(retained.config))
    profiles = [retained.profile]
    for name in sorted(
        {case["profile_id"] for case in declarations} - {predictions.PROFILE_ID}
    ):
        profiles.append(_add_retained_recipe(retained, definitions[name]))
    retained.args.cost.write_text(
        json.dumps({"schema_version": "1.0", "profiles": profiles})
    )
    monkeypatch.setattr(
        predictions, "reserved_cases", lambda: copy.deepcopy(declarations)
    )
    calls = mocked_execution(retained, monkeypatch)
    report = predictions.campaign(retained.args)
    assert report["status"] == "passed", report
    assert len(calls) == report["successful_run_count"] == 312
    plan = json.loads((retained.args.out / "prediction-plan.json").read_text())
    estimator = json.loads((retained.args.out / "estimator.json").read_text())
    assert len(plan["cases"]) == len(estimator["predictions"]) == 104
    assert len(plan["training_observations"]) == 12 * 72
    assert {case["reservation_id"] for case in plan["cases"]} == {
        case["id"] for case in declarations
    }
    for case in plan["cases"]:
        frozen = estimator["predictions"][case["id"]]
        assert (
            frozen["core_provenance"]["cost_profile"]["profile_id"]
            == case["identity"]["profile_id"]
        )
        assert frozen["arguments"]["temporal_context"]["steady_state_samples"] == case[
            "dimensions"
        ].get("steady_state_samples", 0)
        assert (
            frozen["predicted_planning_seconds"] == case["predicted_planning_seconds"]
        )
    assert report["generic_validation"]["passed"]
