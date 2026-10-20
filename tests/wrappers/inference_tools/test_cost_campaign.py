"""Recovery must target only the campaign's original worker process group."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

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


def test_campaign_threads_honors_nullable_and_missing_maximum():
    assert module.campaign_threads({"supported": True, "max_threads": None}) == 2
    assert module.campaign_threads({"supported": True}) == 2
    assert module.campaign_threads({"supported": True, "max_threads": 1}) == 1
    assert module.campaign_threads({"supported": False, "max_threads": None}) == 1
