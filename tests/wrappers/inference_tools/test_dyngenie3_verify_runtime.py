"""Verifier control-flow tests: all subprocess calls are mocked; no fits or Docker."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np

import pytest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "wrappers/inference_tools/tools/dyngenie3"
spec = importlib.util.spec_from_file_location(
    "dyngenie3_runtime_verifier", SOURCE / "verify_runtime.py"
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
IMAGE = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "image",
    [
        "dynGENIE3:latest",
        "registry/tool@sha256:" + "a" * 64,
        "sha256:abc",
        "sha256:" + "A" * 64,
    ],
)
def test_only_full_immutable_image_ids_are_accepted(image):
    with pytest.raises(ValueError, match="immutable"):
        verifier.validate_image(image)


def test_plan_only_has_reviewed_matrix_defaults_first_and_never_invokes_docker(
    tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Plan-only invoked a subprocess")

    monkeypatch.setattr(verifier.subprocess, "run", forbidden)
    assert verifier.main(["--image", IMAGE, "--out", str(tmp_path), "--plan-only"]) == 0
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert plan["run_count"] == 26
    assert (
        plan["cases"][0]["id"] == "smoke_real_defaults"
        and plan["cases"][0]["params"] == {}
    )
    assert plan["cases"][0]["resolved_params"]["ntrees"] == 1000
    assert [row["runs"] for row in plan["resource_strata"]] == [15, 6, 2, 3]
    assert (
        plan["execution_timeout_cap_seconds"] == 93600
        and plan["estimated_duration_seconds"] is None
    )
    assert next(row for row in plan["cases"] if row["id"] == "smoke_rf_k1")[
        "params"
    ] == {"K": 1, "alpha": 0.2, "ntrees": 32}
    assert all(
        row["memory"] == "4g" and row["timeout_seconds"] == 3600
        for row in plan["cases"]
    )
    assert len(list(tmp_path.iterdir())) == 1
    assert plan["scientific_fit_count"] == plan["docker_container_count"] == 26
    assert (
        plan["diagnostic_probe_count"] == 5
        and plan["diagnostic_probe_timeout_seconds"] == 120
    )


def test_real_execution_requires_external_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Unexpected execution"),
    )
    with pytest.raises(SystemExit, match="2"):
        verifier.main(["--image", IMAGE, "--out", str(tmp_path)])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"status": "running"},
        {"status": "failed"},
        {"status": "completed", "active": True},
        {"status": {}},
        {"status": "completed", "active": "false"},
    ],
)
def test_external_state_fails_closed(payload, tmp_path):
    state = tmp_path / "external.json"
    if payload is not None:
        state.write_text(json.dumps(payload))
    with pytest.raises(verifier.ExternalExperimentActive):
        verifier.external_state_receipt(state)


def test_external_state_blocks_before_any_container_and_retains_blocked_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail(
            "Active external experiment reached Docker"
        ),
    )
    state = tmp_path / "external.json"
    state.write_text('{"status":"running","phase":"validating_from_scratch"}')
    plan = verifier.make_plan(IMAGE)
    out = tmp_path / "evidence"
    result = verifier.run_campaign(plan, out, state)
    assert result["status"] == "blocked" and result["completed_run_count"] == 0
    assert result["receipt_count"] == 1 and len(result["not_executed"]) == 25
    assert list(out.glob("runs/*/receipt.json"))


def test_state_is_rechecked_between_runs_and_failures_logs_are_retained(
    tmp_path, monkeypatch
):
    state = tmp_path / "external.json"
    state.write_text('{"status":"completed"}')
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write("retained mock stdout\n")
        kwargs["stderr"].write("retained mock failure\n")
        state.write_text('{"status":"running"}')
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    plan = verifier.make_plan(IMAGE)
    result = verifier.run_campaign(plan, tmp_path / "evidence", state)
    assert len(calls) == 1 and result["status"] == "blocked"
    first, second = result["runs"]
    assert (
        first["status"] == "failed"
        and first["exit_code"] == 7
        and second["status"] == "blocked"
    )
    assert first["logs_sha256"].keys() == {"stdout.log", "stderr.log"}
    assert first["input_sha256"] and first["elapsed_seconds"] >= 0
    command = calls[0]
    assert command[:3] == ["docker", "run", "--rm"] and IMAGE in command
    assert command[command.index("--cpus") + 1] == "1"
    assert command[command.index("--memory") + 1] == "4g"


def test_timeout_cleans_only_its_owned_container_and_retains_failure(
    tmp_path, monkeypatch
):
    state = tmp_path / "external.json"
    state.write_text('{"status":"completed"}')
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "run":
            assert kwargs["timeout"] == 3600
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        assert command[:3] == ["docker", "rm", "--force"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    plan = verifier.make_plan(IMAGE)
    receipt = verifier.run_case(plan["cases"][0], plan, tmp_path / "run", state)
    assert (
        receipt["status"] == "failed"
        and receipt["timed_out"]
        and receipt["cleanup"]["exit_code"] == 0
    )
    assert calls[1][-1] == calls[0][calls[0].index("--name") + 1]
    assert len(calls) == 2 and "cleanup.log" in receipt["logs_sha256"]


@pytest.mark.parametrize(
    "interruption",
    [KeyboardInterrupt(), verifier.RuntimeInterrupted("Interrupted by SIGTERM")],
)
def test_interruption_cleans_owned_container_stops_campaign_and_retains_failed_status(
    tmp_path, monkeypatch, interruption
):
    state = tmp_path / "external.json"
    state.write_text('{"status":"completed"}')
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "run":
            raise interruption
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    result = verifier.run_campaign(
        verifier.make_plan(IMAGE), tmp_path / "evidence", state
    )
    assert result["status"] == "failed" and result["receipt_count"] == 1
    receipt = result["runs"][0]
    assert (
        receipt["interrupted"]
        and not receipt["timed_out"]
        and receipt["cleanup"]["exit_code"] == 0
    )
    assert calls[1][-1] == calls[0][calls[0].index("--name") + 1] and len(calls) == 2
    assert (
        json.loads((tmp_path / "evidence/verification.json").read_text())["status"]
        == "failed"
    )


def test_reproducibility_requires_all_four_fresh_runs_cross_workers_and_changed_seed():
    receipts = [
        {
            "id": f"seed17_threads{threads}_repeat{repeat}",
            "status": "passed",
            "validation": {"vim_sha256": "same", "patched_source_sha256": "source"},
        }
        for threads in (1, 2)
        for repeat in (1, 2)
    ]
    receipts.append(
        {
            "id": "seed18_threads1",
            "status": "passed",
            "validation": {
                "vim_sha256": "different",
                "patched_source_sha256": "source",
            },
        }
    )
    assert (
        verifier.compare_reproducibility(receipts, expanded=False)["status"] == "passed"
    )
    receipts[2]["validation"]["vim_sha256"] = "changed_workers"
    assert (
        verifier.compare_reproducibility(receipts, expanded=False)["status"] == "failed"
    )
    receipts[2]["validation"]["vim_sha256"] = "same"
    receipts[-1]["validation"]["vim_sha256"] = "same"
    assert (
        verifier.compare_reproducibility(receipts, expanded=False)["status"] == "failed"
    )
    assert (
        verifier.compare_reproducibility(receipts[:-1], expanded=False)["status"]
        == "failed"
    )


def test_actual_worker_reports_must_show_effective_single_thread_native_pools(tmp_path):
    limits = {
        "version": 2,
        "cpu": {
            "raw": "100000 100000",
            "quota_us": 100000,
            "period_us": 100000,
            "limit_cpus": 1,
        },
        "memory": {"raw": "4294967296", "limit_bytes": 4294967296},
    }

    def observation(pid=100, threads=1):
        return {
            "pid": pid,
            "native_pools": [{"internal_api": "openblas", "num_threads": threads}],
            "cgroups": limits,
        }

    with pytest.raises(ValueError, match="missing"):
        verifier.validate_worker_resources(tmp_path, 1)
    first = tmp_path / "resource-worker-100.json"
    first.write_text(json.dumps(observation()))
    assert verifier.validate_worker_resources(tmp_path, 1)["worker_count"] == 1
    first.write_text(json.dumps(observation(threads=2)))
    with pytest.raises(ValueError, match="exceeded"):
        verifier.validate_worker_resources(tmp_path, 1)
    first.write_text(json.dumps({"pid": 100, "native_pools": []}))
    with pytest.raises(ValueError, match="no observed"):
        verifier.validate_worker_resources(tmp_path, 1)
    first.write_text(json.dumps(observation()))
    (tmp_path / "resource-worker-101.json").write_text(json.dumps(observation(pid=101)))
    with pytest.raises(ValueError, match="exceed"):
        verifier.validate_worker_resources(tmp_path, 1)
    limits["cpu"].update(raw="200000 100000", quota_us=200000, limit_cpus=2)
    first.write_text(json.dumps(observation()))
    (tmp_path / "resource-worker-101.json").write_text(json.dumps(observation(pid=101)))
    assert verifier.validate_worker_resources(tmp_path, 2)["worker_count"] == 2


@pytest.mark.parametrize(
    "limits,match",
    [
        ({}, "unavailable"),
        ({"version": 2, "cpu": {"limit_cpus": None}, "memory": {}}, "CPU quota"),
        (
            {
                "version": 1,
                "cpu": {
                    "raw": "200000 100000",
                    "quota_us": 200000,
                    "period_us": 100000,
                    "limit_cpus": 2,
                },
            },
            "CPU quota",
        ),
        (
            {
                "version": 2,
                "cpu": {
                    "raw": "100000 100000",
                    "quota_us": 100000,
                    "period_us": 100000,
                    "limit_cpus": 1,
                },
                "memory": {"raw": "max", "limit_bytes": None},
            },
            "memory limit",
        ),
    ],
)
def test_missing_unlimited_or_wrong_cgroup_limits_fail_resource_acceptance(
    limits, match, tmp_path
):
    (tmp_path / "resource-worker-100.json").write_text(
        json.dumps(
            {"pid": 100, "native_pools": [{"num_threads": 1}], "cgroups": limits}
        )
    )
    with pytest.raises(ValueError, match=match):
        verifier.validate_worker_resources(tmp_path, 1)


def test_diagnostic_cases_stage_distinct_large_inputs_and_probe_in_the_same_container(
    tmp_path,
):
    plan = verifier.make_plan(IMAGE)
    cases = {row["id"]: row for row in plan["cases"] if row.get("diagnostic_probe")}
    assert set(cases) == {
        "rf_quality_time_only",
        "et_quality_steady",
        "rf_quality_zero_decay",
        "rf_quality_one_tree",
        "rf_quality_one_sample",
    }
    case = cases["rf_quality_time_only"]
    io = verifier.stage_case(case, plan, tmp_path / "run")
    genes, columns, _ = verifier._matrix_axes(io / "expression.tsv")
    assert len(genes) == 8 and len(columns) == 21
    command = verifier.docker_command(IMAGE, case, io, "synthetic-container")
    assert command.count("docker") == 1 and command.count(IMAGE) == 1
    assert command[command.index("--entrypoint") + 1] == "/bin/sh"
    assert '/app/run_tool.sh "$@"' in command[command.index("-c") + 1]
    assert "--probe-diagnostics /io" in command[command.index("-c") + 1]
    assert (
        verifier._sha(io / "verify_runtime.py")
        == plan["source_sha256"]["verify_runtime.py"]
    )
    one_sample = verifier.stage_case(
        cases["rf_quality_one_sample"], plan, tmp_path / "one-sample"
    )
    assert len(verifier._matrix_axes(one_sample / "expression.tsv")[1]) == 2
    assert cases["rf_quality_one_sample"]["allow_empty_network"]


def test_data_derived_alpha_closed_form_uses_global_baseline_and_maximum_across_series():
    columns = ["a0", "a1", "b0", "b1"]
    values = [[4.0, 1.0, 9.0, 1.0], [3.0, 3.0, 0.0, 0.0]]
    series = [
        {"columns": ["a0", "a1"], "upstream_timepoints": [0.0, 2.0]},
        {"columns": ["b0", "b1"], "upstream_timepoints": [1.0, 5.0]},
    ]
    # Global C_min=0 comes from series b; gene B is constant within each
    # independent series despite changing between series and must retain zero.
    assert verifier.expected_degradation_rates(
        values, columns, series
    ).tolist() == pytest.approx([np.log(2), 0])
    series[1]["upstream_timepoints"] = [1.0, 2.0]
    assert verifier.expected_degradation_rates(
        values, columns, series
    ).tolist() == pytest.approx([np.log(9), 0])


def diagnostic_mock_fixture(tmp_path):
    """Alternating known tree rankings have analytic stability, without fitting."""
    plan = verifier.make_plan(IMAGE)
    case = next(row for row in plan["cases"] if row["id"] == "rf_quality_time_only")
    io = verifier.stage_case(case, plan, tmp_path / "run")
    raw = io / "out/provenance"
    raw.mkdir()
    genes, columns, values = verifier._matrix_axes(io / "expression.tsv")
    import csv

    with (io / "extra/timepoints.tsv").open() as stream:
        rows = {row["column"]: row for row in csv.DictReader(stream, delimiter="\t")}
    grouped = {}
    for column in columns:
        grouped.setdefault(rows[column]["series_id"], []).append(column)
    series = []
    for name, ids in sorted(grouped.items()):
        ids.sort(key=lambda column: float(rows[column]["timepoint"]))
        times = [float(rows[column]["timepoint"]) for column in ids]
        series.append(
            {
                "series_id": name,
                "columns": ids,
                "upstream_timepoints": np.asarray(times, dtype=np.float32).tolist(),
            }
        )
    params = case["resolved_params"]
    seeds = [
        int(np.random.SeedSequence([params["seed"], i]).generate_state(1)[0])
        for i in range(8)
    ]
    config = {
        "n_estimators": 32,
        "n_jobs": 1,
        "random_state": seeds[0],
        "max_features": "sqrt",
        "oob_score": True,
        "bootstrap": True,
    }
    resolved = {
        "params": params,
        "genes": genes,
        "regulators": "all",
        "series": series,
        "alpha_resolved": [0.2] * 8,
        "forest_constructor_params": config,
    }
    verifier._write(raw / "resolved.json", resolved)
    # For each target, 16 ascending and 16 descending feature rankings have
    # 240 same-order tree pairs (intersection 5) and 256 opposite pairs
    # (intersection 3), giving exactly 1968 / 2480 before numerical rounding.
    verifier._write(
        raw / "quality_scores.json",
        {"prediction_score": 1.0, "stability_score": 1968 / 2480},
    )
    vim = np.full((8, 8), 1 / 7)
    np.fill_diagonal(vim, 0)
    np.save(raw / "VIM.npy", vim)
    (raw / "tree_models.joblib").write_bytes(
        b"synthetic model objects supplied by loader; no serialized code"
    )
    matrix = np.asarray(values)
    index = {c: i for i, c in enumerate(columns)}
    models = []
    for target in range(8):
        oob = []
        for row in series:
            x = matrix[target, [index[c] for c in row["columns"]]]
            t = np.asarray(row["upstream_timepoints"], dtype=np.float32)
            oob.extend((np.diff(x) / np.diff(t) + 0.2 * x[:-1]).tolist())
        trees = []
        for tree_index in range(32):
            importance = (
                np.arange(1, 9, dtype=float)
                if tree_index % 2 == 0
                else np.arange(8, 0, -1, dtype=float)
            )
            trees.append(
                SimpleNamespace(
                    tree_=SimpleNamespace(
                        compute_feature_importances=lambda normalize=False, value=importance: value.copy()
                    )
                )
            )
        target_config = {**config, "random_state": seeds[target]}
        models.append(
            SimpleNamespace(
                n_features_in_=8,
                estimators_=trees,
                oob_prediction_=np.asarray(oob),
                get_params=lambda deep=False, value=target_config: value.copy(),
            )
        )

    def exact_correlation(predicted, observed):
        np.testing.assert_allclose(predicted, observed, rtol=1e-12, atol=1e-12)
        return 1.0, 0.0

    return io, models, exact_correlation


def test_saved_model_probe_recomputes_vim_inverse_dynamics_and_nontrivial_stability_without_fit(
    tmp_path,
):
    io, models, correlation = diagnostic_mock_fixture(tmp_path)
    result = verifier.verify_saved_diagnostics(
        io, model_loader=lambda _: models, correlation=correlation
    )
    assert result["status"] == "passed" and result["scientific_fits"] == 0
    assert result["model_count"] == 8 and result["candidate_count"] == 8
    assert result["recomputed_diagnostics"]["prediction_score"] == 1
    assert result["recomputed_diagnostics"]["stability_score"] == pytest.approx(
        1968 / 2480
    )


def test_zero_decay_ss_inverse_prediction_is_undefined_without_calling_pearson(
    tmp_path,
):
    io, models, _ = diagnostic_mock_fixture(tmp_path)
    raw = io / "out/provenance"
    import shutil

    shutil.copy2(
        verifier.FIXTURES / "quality/steady_state_expression.tsv",
        io / "extra/steady_state_expression.tsv",
    )
    resolved = verifier._json(raw / "resolved.json")
    resolved["params"]["alpha"] = 0.0
    resolved["alpha_resolved"] = [0.0] * 8
    verifier._write(raw / "resolved.json", resolved)
    verifier._write(
        raw / "quality_scores.json",
        {"prediction_score": None, "stability_score": 1968 / 2480},
    )
    verifier._write(
        io / "diagnostic_expectations.json",
        {"prediction_score": "null", "stability_score": "nontrivial"},
    )
    for model in models:
        model.oob_prediction_ = np.concatenate([np.zeros(4), model.oob_prediction_])

    def forbidden(*_):
        pytest.fail("Undefined SS inverse prediction reached Pearson")

    result = verifier.verify_saved_diagnostics(
        io, model_loader=lambda _: models, correlation=forbidden
    )
    assert (
        result["status"] == "passed"
        and result["recomputed_diagnostics"]["prediction_score"] is None
    )


@pytest.mark.parametrize(
    "tamper,match",
    [
        ("count", "model count"),
        ("seed", "configuration"),
        ("vim", "VIM differs"),
        ("quality", "diagnostic differs"),
    ],
)
def test_saved_model_probe_rejects_tampered_model_or_derived_outputs_without_fit(
    tamper, match, tmp_path
):
    io, models, correlation = diagnostic_mock_fixture(tmp_path)
    raw = io / "out/provenance"
    if tamper == "count":
        models.pop()
    elif tamper == "seed":
        models[2].get_params = lambda deep=False: {"random_state": 99}
    elif tamper == "vim":
        vim = np.load(raw / "VIM.npy")
        vim[0, 1] = 0.9
        np.save(raw / "VIM.npy", vim)
    else:
        verifier._write(
            raw / "quality_scores.json",
            {"prediction_score": 1.0, "stability_score": 0.1},
        )
    with pytest.raises(ValueError, match=match):
        verifier.verify_saved_diagnostics(
            io, model_loader=lambda _: models, correlation=correlation
        )


def test_expanded_reproducibility_checks_every_resource_and_both_fixture_families():
    receipts = []
    for prefix, threads_values, common_hash in (
        ("", (1, 2), "original"),
        ("resource_", (1, 2, 4, 8), "eight_genes"),
    ):
        for threads in threads_values:
            for repeat in (1, 2):
                receipts.append(
                    {
                        "id": f"{prefix}seed17_threads{threads}_repeat{repeat}",
                        "status": "passed",
                        "validation": {
                            "vim_sha256": common_hash,
                            "patched_source_sha256": "source",
                        },
                    }
                )
        receipts.append(
            {
                "id": f"{prefix}seed18_threads{threads_values[-1] if prefix else 1}",
                "status": "passed",
                "validation": {
                    "vim_sha256": "changed",
                    "patched_source_sha256": "source",
                },
            }
        )
    assert verifier.compare_reproducibility(receipts)["status"] == "passed"
    target = next(
        row for row in receipts if row["id"] == "resource_seed17_threads8_repeat2"
    )
    target["validation"]["vim_sha256"] = "diverged"
    assert verifier.compare_reproducibility(receipts)["status"] == "failed"
    target["validation"]["vim_sha256"] = "eight_genes"
    receipts.remove(target)
    assert verifier.compare_reproducibility(receipts)["status"] == "failed"


def test_expanded_worker_cases_use_enough_genes_for_eight_actual_workers():
    plan = verifier.make_plan(IMAGE)
    cases = [row for row in plan["cases"] if row["id"].startswith("resource_")]
    assert len(cases) == 9 and {row["threads"] for row in cases} == {1, 2, 4, 8}
    assert all(
        row["fixture_subdir"] == "quality" and row["resolved_params"]["ntrees"] == 256
        for row in cases
    )
    assert all(
        not row["resolved_params"]["compute_quality_scores"]
        and not row["resolved_params"]["save_models"]
        for row in cases
    )
