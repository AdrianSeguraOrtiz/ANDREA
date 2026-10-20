"""Verifier control-flow tests: all subprocess calls are mocked; no fits or Docker."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

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
    assert plan["run_count"] == 12
    assert (
        plan["cases"][0]["id"] == "smoke_real_defaults"
        and plan["cases"][0]["params"] == {}
    )
    assert plan["cases"][0]["resolved_params"]["ntrees"] == 1000
    assert [row["runs"] for row in plan["resource_strata"]] == [10, 2]
    assert (
        plan["execution_timeout_cap_seconds"] == 43200
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
    assert result["receipt_count"] == 1 and len(result["not_executed"]) == 11
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
    assert verifier.compare_reproducibility(receipts)["status"] == "passed"
    receipts[2]["validation"]["vim_sha256"] = "changed_workers"
    assert verifier.compare_reproducibility(receipts)["status"] == "failed"
    receipts[2]["validation"]["vim_sha256"] = "same"
    receipts[-1]["validation"]["vim_sha256"] = "same"
    assert verifier.compare_reproducibility(receipts)["status"] == "failed"
    assert verifier.compare_reproducibility(receipts[:-1])["status"] == "failed"


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
