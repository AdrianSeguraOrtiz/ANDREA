"""Recovery must target only the campaign's original worker process group."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "cost_campaign_under_test",
    ROOT / "wrappers/inference_tools/scripts/run_cost_coverage.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_recovery_terminates_verified_worker_only():
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    worker = subprocess.Popen(command, start_new_session=True)
    try:
        record = module.process_record(worker.pid, command)
        changed_identity = {
            **record,
            "start_ticks": str(int(record["start_ticks"]) + 1),
        }
        assert not module.terminate_recorded_child(changed_identity)
        assert worker.poll() is None
        assert module.terminate_recorded_child(record)
        assert worker.wait(timeout=3) != 0
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()


def test_recovery_rejects_different_command():
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    worker = subprocess.Popen(command, start_new_session=True)
    try:
        record = module.process_record(worker.pid, command)
        assert not module.terminate_recorded_child({**record, "command": ["unrelated"]})
        assert worker.poll() is None
    finally:
        worker.kill()
        worker.wait()


def test_verified_worker_gets_time_to_drain_before_escalation(tmp_path):
    ready, drained = tmp_path / "ready", tmp_path / "drained"
    program = (
        "import signal,time,sys; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, lambda *_: (time.sleep(.2), Path(sys.argv[2]).write_text('released'), sys.exit(0))); "
        "Path(sys.argv[1]).write_text('ready'); signal.pause()"
    )
    command = [sys.executable, "-c", program, str(ready), str(drained)]
    worker = subprocess.Popen(command, start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        assert module.terminate_recorded_child(
            module.process_record(worker.pid, command), grace_seconds=2
        )
        assert worker.wait(timeout=2) == 0
        assert drained.read_text() == "released"
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()


def test_static_campaign_plan_counts_runs_and_budget_without_docker():
    catalog = ROOT / "andrea/catalog_inference_tools/tools"
    with patch.object(
        module.subprocess,
        "check_output",
        side_effect=AssertionError("Docker must not be inspected"),
    ):
        plan = module.execution_plan([("genie3", catalog / "genie3")], timeout=100)
    assert plan["total_runs"] == sum(row["runs"] for row in plan["profiles"])
    assert plan["timeout_budget_seconds"] == sum(
        row["runs"] * row["timeout_seconds_per_run"] for row in plan["profiles"]
    )
    assert {row["phase"] for row in plan["profiles"]} == {"baseline", "representative"}
    assert all(row["repeats"] >= 3 for row in plan["profiles"])


def test_campaign_threads_filter_full_panel_by_tool_support():
    assert module.resource_threads(
        {"supported": True, "max_threads": None}, [1, 2, 4]
    ) == [1, 2, 4]
    assert module.resource_threads({"supported": True}, [1, 2, 4]) == [1, 2, 4]
    assert module.resource_threads(
        {"supported": True, "max_threads": 1}, [1, 2, 4]
    ) == [1]
    assert module.resource_threads(
        {"supported": False, "max_threads": None}, [1, 2, 4]
    ) == [1]
    with pytest.raises(ValueError, match="No requested CPU"):
        module.resource_threads({"supported": False}, [2, 4])
    assert module.resource_threads({"supported": True}, None) == [2]
    assert module.resource_threads({"supported": False}, None) == [1]
    assert module.campaign_threads({"supported": True, "max_threads": 1}) == 1


def test_swing_baseline_panel_uses_all_six_resources_without_docker():
    catalog = ROOT / "andrea/catalog_inference_tools/tools"
    with patch.object(
        module.subprocess,
        "check_output",
        side_effect=AssertionError("Offline plan inspected Docker"),
    ):
        plan = module.execution_plan(
            [("swing", catalog / "swing")],
            1800,
            include_representative=False,
        )
    assert len(plan["profiles"]) == 77
    assert plan["total_runs"] == 4158
    assert {row["phase"] for row in plan["profiles"]} == {"baseline"}
    assert all(
        row["threads"] == [1, 2, 4] and row["ram_gb"] == [8, 32]
        for row in plan["profiles"]
    )
    assert plan["timeout_budget_seconds"] == 4158 * 1800


def test_declarative_panel_is_independent_of_host_and_debug_selection():
    catalog = ROOT / "andrea/catalog_inference_tools/tools"
    with patch.object(module.os, "cpu_count", return_value=1):
        plan = module.execution_plan(
            [("swing", catalog / "swing")],
            1800,
            threads=[1],
            ram_gb=[8],
            include_representative=False,
        )
    assert plan["total_runs"] == 4158
    assert plan["debug_subset"]
    assert sum(row["selected_runs"] for row in plan["profiles"]) == 693
    assert all(
        row["threads"] == [1, 2, 4] and row["selected_threads"] == [1]
        for row in plan["profiles"]
    )


def test_declared_repetitions_and_legacy_default_resource_contract():
    threading = {"supported": True, "max_threads": None}
    declared = module.baseline_panel(
        {
            "calibration_design": {
                "resources": {"threads": [1, 4], "ram_gb": [16]},
                "repetitions": 5,
            }
        },
        "one",
        threading,
    )
    assert declared["threads"] == [1, 4]
    assert declared["ram_gb"] == [16]
    assert declared["repeats"] == 5
    fallback = module.baseline_panel({}, "one", threading)
    assert fallback["threads"] == [2] and fallback["ram_gb"] == [8]
    assert fallback["repeats"] == 3 and not fallback["declarative_resources"]
    with pytest.raises(ValueError, match="subsets"):
        module.baseline_panel(
            {"calibration_design": {"resources": {"threads": [1, 4], "ram_gb": [16]}}},
            "one",
            threading,
            threads=[8],
        )


def test_uncalibrated_tool_can_be_planned_and_filtered_without_cost_or_docker():
    catalog = ROOT / "andrea/catalog_inference_tools/tools"
    with patch.object(
        module.subprocess,
        "check_output",
        side_effect=AssertionError("Offline plan inspected Docker"),
    ):
        plan = module.execution_plan(
            [("bixgboost", catalog / "bixgboost")],
            1800,
            profile_filters=["bixgboost:global_defaults_t4_s1"],
            include_representative=False,
        )
    assert len(plan["profiles"]) == 1
    assert plan["total_runs"] == 54


def test_baseline_command_freezes_explicit_panel_budget_and_profile_selection(tmp_path):
    plan = {
        "profiles": [
            {
                "phase": "baseline",
                "tool_id": "swing",
                "profile_id": "one",
                "threads": [1, 2, 4],
                "ram_gb": [8, 32],
                "selected_threads": [1, 2, 4],
                "selected_ram_gb": [8, 32],
                "repeats": 3,
            }
        ]
    }
    args = SimpleNamespace(
        timeout=1800,
        seed=12345,
        min_free_gb=20,
        reuse_results_dir=[tmp_path / "old"],
        reuse_source_root=tmp_path / "snapshot",
        reuse_source_manifest=tmp_path / "seal.json",
        remeasure_incompatible_history=False,
        concurrency=2,
        resource_state_dir=tmp_path / "global-resources",
        max_cpu=None,
        max_ram_gb=None,
        cpu_headroom=0,
        ram_headroom_gb=1,
        ram_headroom_fraction=0.1,
    )
    command = module.baseline_command("swing", tmp_path, plan, args)
    for flag, value in {
        "--threads": "1,2,4",
        "--ram-gb": "8,32",
        "--concurrency": "2",
        "--timeout": "1800",
        "--seed": "12345",
        "--min-free-gb": "20",
        "--profile": "one",
    }.items():
        assert command[command.index(flag) + 1] == value
    assert "--merge-existing" in command
    assert "--fail-fast" not in command
    assert "--force-rerun" not in command
    assert "--max-cpu" not in command
    assert "--max-ram-gb" not in command
    assert command[command.index("--reuse-results-dir") + 1] == str(
        (tmp_path / "old").resolve()
    )
    assert command[command.index("--reuse-source-root") + 1] == str(
        (tmp_path / "snapshot").resolve()
    )
    assert command[command.index("--reuse-source-manifest") + 1] == str(
        (tmp_path / "seal.json").resolve()
    )


@pytest.mark.parametrize("timeout_seconds, accepted", [(1800.1, True), (1799.9, False)])
def test_terminal_ledger_counts_timeout_and_enforces_budget_without_counting_summary_times(
    tmp_path, monkeypatch, timeout_seconds, accepted
):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    directory = tmp_path / "andrea/catalog_inference_tools/tools/swing"
    directory.mkdir(parents=True)
    observations = [
        {
            "genes": 50,
            "columns": 16,
            "threads": 1,
            "ram_gb": 8,
            "repeat": 1,
            "status": "ok",
            "seconds": 300,
            "measurement_timeout_seconds": 7200,
        },
        {
            "genes": 50,
            "columns": 16,
            "threads": 1,
            "ram_gb": 8,
            "repeat": 2,
            "status": "timeout",
            "seconds": timeout_seconds,
            "measurement_timeout_seconds": 1800,
        },
        {
            "genes": 50,
            "columns": 16,
            "threads": 1,
            "ram_gb": 8,
            "repeat": 3,
            "status": "timeout",
            "seconds": 7200.1,
            "measurement_timeout_seconds": 7200,
        },
    ]
    (directory / "cost.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "one",
                        "benchmark_config": {"timeout_seconds": 1800},
                        "runtime_points": [
                            {"measurement_evidence": {"observations": observations}}
                        ],
                    }
                ]
            }
        )
    )
    plan = {
        "profiles": [
            {
                "phase": "baseline",
                "tool_id": "swing",
                "profile_id": "one",
                "sizes": [{"genes": 50, "columns": 16}],
                "threads": [1],
                "ram_gb": [8],
                "repeats": 3,
                "historical_points_retained": [],
                "timeout_seconds_per_run": 1800,
            }
        ]
    }
    ledger = module.recorded_slot_ledger(plan)
    assert ledger["recorded_terminal_repetitions"] == 1 + int(accepted)
    assert ledger["recorded_statuses"] == {
        "ok": 1,
        "timeout": int(accepted),
        "oom": 0,
        "error": 0,
    }
    assert ledger["pending_repetitions"] == 2 - int(accepted)
    # A later interruption cannot make the frozen geometry change.
    assert plan["profiles"][0]["repeats"] == 3


@pytest.fixture
def fake_campaign(tmp_path, monkeypatch):
    """Exercise coordinator state transitions without creating workers or containers."""
    monkeypatch.setattr(module, "ROOT", tmp_path)
    catalog = tmp_path / "andrea/catalog_inference_tools/tools/newtool"
    catalog.mkdir(parents=True)
    (catalog / "toolspec.json").write_text(
        json.dumps(
            {
                "docker_image": "newtool:1.0.0",
                "runtime_resources": {
                    "threading": {"supported": True, "max_threads": None}
                },
            }
        )
    )

    def plan(selected, timeout, **kwargs):
        return {
            "profiles": [
                {
                    "phase": "baseline",
                    "tool_id": "newtool",
                    "profile_id": "one",
                    "sizes": [{"genes": 50, "columns": 16}],
                    "threads": [1, 2, 4],
                    "ram_gb": [8, 32],
                    "selected_threads": [1, 2, 4],
                    "selected_ram_gb": [8, 32],
                    "repeats": 3,
                    "runs": 18,
                    "historical_points_retained": [],
                    "timeout_seconds_per_run": timeout,
                }
            ],
            "total_runs": 18,
            "timeout_budget_seconds": 18 * timeout,
        }

    monkeypatch.setattr(module, "execution_plan", plan)
    monkeypatch.setattr(module, "frozen_sources", lambda: {})
    monkeypatch.setattr(
        module,
        "runtime_resource_snapshot",
        lambda args: {"cpu_budget": 8, "ram_budget_bytes": 50 * 1024**3},
    )
    monkeypatch.setattr(module, "build_context_fingerprint", lambda **kwargs: "context")
    monkeypatch.setattr(module, "owned_container_cleanup", lambda evidence: None)
    monkeypatch.setattr(
        module,
        "process_record",
        lambda pid, command: {"pid": pid, "start_ticks": "fixed", "command": command},
    )
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    calls = []

    def check_output(command, **kwargs):
        calls.append(command)
        return "sha256:image" if "--format" in command else '[{"Id":"sha256:image"}]'

    monkeypatch.setattr(module.subprocess, "check_output", check_output)

    class Worker:
        pid = 123456
        stdout = ["one real worker output line\n"]

        def wait(self, **kwargs):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: Worker())
    validations = []

    def run(command, **kwargs):
        validations.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    return tmp_path, calls, validations


def test_bootstrap_missing_cost_and_validation_scope(fake_campaign):
    root, calls, validations = fake_campaign
    evidence = root / "evidence"
    assert (
        module.main(
            ["--tool", "newtool", "--evidence", str(evidence), "--skip-representative"]
        )
        == 0
    )
    definition = json.loads((evidence / "campaign.json").read_text())
    assert definition["tools"][0]["ordering_weight"] == 0
    assert len(definition["jobs"]) == 1
    command = definition["jobs"][0]["command"]
    assert command[command.index("--timeout") + 1] == "1800"
    assert command[command.index("--concurrency") + 1] == "0"
    assert validations[0].count("--tool") == 1
    assert validations[0][validations[0].index("--tool") + 1] == "newtool"
    assert "--require-attempts" in validations[0]
    assert "--require-coverage" not in validations[0]
    state = json.loads((evidence / "status.json").read_text())
    assert state["status"] == "completed"
    assert state["initial_recorded_slot_ledger"]["pending_repetitions"] == 18


def test_resume_rejects_changed_timeout_before_inspecting_images(fake_campaign):
    root, calls, _ = fake_campaign
    evidence = root / "evidence"
    args = ["--tool", "newtool", "--evidence", str(evidence), "--skip-representative"]
    assert module.main(args) == 0
    calls.clear()
    with pytest.raises(ValueError, match="Execution plan changed"):
        module.main(args + ["--timeout", "7200"])
    assert not calls


def test_space_reserve_stops_before_any_worker(fake_campaign, monkeypatch):
    root, _, _ = fake_campaign
    monkeypatch.setattr(
        module.shutil, "disk_usage", lambda path: SimpleNamespace(free=0)
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Disk reserve must stop before measurement")

    monkeypatch.setattr(module.subprocess, "Popen", forbidden)
    with pytest.raises(RuntimeError, match="free disk"):
        module.main(
            [
                "--tool",
                "newtool",
                "--evidence",
                str(root / "evidence"),
                "--skip-representative",
            ]
        )


def test_resume_keeps_dynamic_ledger_outside_frozen_plan(fake_campaign):
    root, _, _ = fake_campaign
    evidence = root / "evidence"
    args = ["--tool", "newtool", "--evidence", str(evidence), "--skip-representative"]
    assert module.main(args) == 0
    before = (evidence / "execution-plan.json").read_bytes()
    (root / "andrea/catalog_inference_tools/tools/newtool/cost.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "one",
                        "benchmark_config": {"timeout_seconds": 1800},
                        "runtime_points": [
                            {
                                "measurement_evidence": {
                                    "observations": [
                                        {
                                            "genes": 50,
                                            "columns": 16,
                                            "threads": 1,
                                            "ram_gb": 8,
                                            "repeat": 1,
                                            "status": "timeout",
                                            "seconds": 1800.1,
                                        }
                                    ]
                                }
                            }
                        ],
                    }
                ]
            }
        )
    )
    assert module.main(args) == 0
    assert (evidence / "execution-plan.json").read_bytes() == before
    ledger = json.loads((evidence / "slot-ledger.json").read_text())
    assert ledger["recorded_terminal_repetitions"] == 1
    assert ledger["pending_repetitions"] == 17


def test_resume_rejects_changed_historical_source_proof(fake_campaign):
    root, calls, _ = fake_campaign
    snapshot = root / "snapshot"
    snapshot.mkdir()
    seal = root / "seal.json"
    seal.write_text('{"source_files_sha256": {}}')
    args = [
        "--tool",
        "newtool",
        "--evidence",
        str(root / "evidence"),
        "--skip-representative",
        "--reuse-source-root",
        str(snapshot),
        "--reuse-source-manifest",
        str(seal),
    ]
    assert module.main(args) == 0
    seal.write_text('{"source_files_sha256": {}, "changed": true}')
    calls.clear()
    with pytest.raises(ValueError, match="Execution plan changed"):
        module.main(args)
    assert not calls


def test_unavailable_resources_keep_operational_wait_state_without_final_claim(
    fake_campaign, monkeypatch
):
    root, _, validations = fake_campaign

    class WaitingWorker:
        pid = 123456
        stdout = ["WAIT: a declared 32 GiB slot does not fit this host capacity\n"]

        def wait(self, **kwargs):
            return 75

        def poll(self):
            return 75

    monkeypatch.setattr(
        module.subprocess, "Popen", lambda *args, **kwargs: WaitingWorker()
    )
    evidence = root / "evidence"
    assert (
        module.main(
            ["--tool", "newtool", "--evidence", str(evidence), "--skip-representative"]
        )
        == 75
    )
    state = json.loads((evidence / "status.json").read_text())
    assert state["status"] == "waiting_resources"
    assert state["jobs"][0]["status"] == "waiting_resources"
    assert state["failed_jobs"] == 0
    assert not validations
    assert (
        json.loads((evidence / "slot-ledger.json").read_text())["pending_repetitions"]
        == 18
    )
