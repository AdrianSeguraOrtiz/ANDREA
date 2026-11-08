"""Terminal execution coverage and numerical support are independent gates."""

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def validator():
    scripts = ROOT / "wrappers/inference_tools/scripts"
    saved_path = sys.path[:]
    saved_modules = {
        name: value
        for name, value in sys.modules.items()
        if name == "shared" or name.startswith("shared.")
    }
    try:
        for name in saved_modules:
            del sys.modules[name]
        sys.path.insert(0, str(scripts))
        spec = importlib.util.spec_from_file_location(
            "outcome_validator", scripts / "validate_tool_costs.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = saved_path
        for name in list(sys.modules):
            if name == "shared" or name.startswith("shared."):
                del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.modules.pop("outcome_validator", None)


def profile(statuses=("timeout", "timeout", "timeout")):
    ok = statuses.count("ok")
    counts = {name: statuses.count(name) for name in ("oom", "timeout", "error")}
    point = {
        "genes": 10,
        "columns": 5,
        "threads": 1,
        "ram_gb": 8,
        "status": (
            "ok"
            if ok == len(statuses)
            else (
                "partial"
                if ok
                else (
                    "oom"
                    if counts["oom"]
                    else "timeout" if counts["timeout"] else "error"
                )
            )
        ),
        "repeats_total": len(statuses),
        "repeats_ok": ok,
        "repeats_failed": len(statuses) - ok,
        "ok_rate": round(ok / len(statuses), 6),
        "failure_breakdown": counts,
        "seconds_p50": 1.0 if ok else None,
        "seconds_p90": 1.0 if ok else None,
        "measurement_evidence": {
            "observations": [
                {
                    "genes": 10,
                    "columns": 5,
                    "threads": 1,
                    "ram_gb": 8,
                    "repeat": repeat,
                    "run_id": f"actual-{repeat}",
                    "status": status,
                    "seconds": 1.0 if status == "ok" else 1800.2,
                }
                for repeat, status in enumerate(statuses, 1)
            ]
        },
    }
    return {
        "profile_id": "one",
        "benchmark_config": {
            "profile_sha256": "a" * 64,
            "sizes": [{"genes": 10, "columns": 5}],
            "threads_tested": [1],
            "ram_gb_tested": [8],
            "repeats": 3,
        },
        "runtime_points": [point],
    }


def test_terminal_timeouts_finish_attempt_panel_without_becoming_numeric_support(
    validator,
):
    result = validator.calibration_coverage({"profiles": [profile()]})
    assert result["attempt_coverage"]["complete"]
    assert result["attempt_coverage"]["profiles"][0]["failed_attempts"] == 3
    assert not result["calibration_adequate_for_planned_range"]
    assert result["profiles"][0]["successful_points"] == 0


def test_partial_attempts_and_never_attempted_points_remain_pending(validator):
    measured = profile(("timeout",))
    measured["benchmark_config"]["sizes"].append({"genes": 20, "columns": 5})
    result = validator.attempt_coverage({"profiles": [measured]})
    assert not result["complete"]
    report = result["profiles"][0]
    assert report["actual_attempts"] == 1
    assert report["terminal_points"] == 0
    assert len(report["pending_points"]) == 2
    assert measured["runtime_points"][0]["repeats_total"] == 1


def test_attempt_gate_requires_retained_receipts_not_aggregate_counts(validator):
    measured = profile()
    measured["runtime_points"][0].pop("measurement_evidence")
    result = validator.attempt_coverage({"profiles": [measured]})
    assert not result["complete"]
    assert any("Retained raw terminal" in error for error in result["errors"])


def test_missing_registered_profile_is_pending_without_invented_observations(validator):
    result = validator.attempt_coverage(
        {"profiles": [profile()]}, {"one", "unmeasured"}
    )
    assert not result["complete"]
    assert any("Missing current" in error for error in result["errors"])


def test_attempt_cli_and_numeric_cli_remain_separate(validator):
    args = validator.parse_args(["--require-attempts"])
    assert args.require_attempts and not args.require_coverage
