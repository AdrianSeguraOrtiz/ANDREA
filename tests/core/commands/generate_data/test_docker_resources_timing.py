"""The simulator Docker invocation must honor and measure the frozen plan."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from andrea.core.commands.generate_data.backends import docker_runner


def request(**changes):
    return SimpleNamespace(**{
        "simulator_id": "example", "simulator_spec": {"docker_image": "example:1"},
        "resolved_input_paths": {}, "run_id": "test", "data_axes": {},
        "truth_requirements": {}, "effective_extras": [], "native_outputs": [],
        "inputs": {}, "simulator_params": {}, "runtime_resources": {"threads": 2},
        "ram_gb": 1.5, **changes,
    })


def test_frozen_resources_are_enforced_and_timing_excludes_progress_callbacks(tmp_path):
    finished_timing = threading.Event()
    clock_calls = []

    def clock():
        clock_calls.append(True)
        if len(clock_calls) == 2:
            finished_timing.set()
        return 100.0 if len(clock_calls) == 1 else 101.25

    def process(command, **kwargs):
        assert len(clock_calls) == 1, "Clock starts before Popen"
        (tmp_path / "simulator-output-manifest.json").write_text("{}")
        return SimpleNamespace(wait=lambda: 0)

    def callback(progress):
        if progress["phase"] == "container_started":
            # Completion measurement must happen while the callback is active.
            assert finished_timing.wait(2)

    with (
        patch.object(docker_runner, "_ensure_docker_cli"),
        patch.object(docker_runner, "_ensure_docker_image", return_value="local"),
        patch.object(docker_runner.subprocess, "Popen", side_effect=process) as popen,
        patch.object(docker_runner.time, "perf_counter", side_effect=clock),
    ):
        docker_runner.run_docker_simulator(
            request=request(), seed=1, stage_dir=tmp_path, task_label="test",
            progress_poll_seconds=60, show_progress=False, progress_callback=callback,
        )
    command = popen.call_args.args[0]
    assert command[command.index("--cpus") + 1] == "2"
    assert command[command.index("--memory") + 1] == str(1610612736)
    timing = json.loads((tmp_path / "provenance/raw/docker_wrapper.timing.json").read_text())
    assert timing["seconds"] == 1.25
    assert timing["threads"] == 2
    assert timing["ram_gb"] == 1.5
    assert timing["memory_limit_bytes"] == 1610612736
    assert timing["returncode"] == 0


def test_failed_container_still_preserves_measured_duration(tmp_path):
    with (
        patch.object(docker_runner, "_ensure_docker_cli"),
        patch.object(docker_runner, "_ensure_docker_image", return_value="local"),
        patch.object(docker_runner.subprocess, "Popen", return_value=SimpleNamespace(wait=lambda: 137)),
        pytest.raises(RuntimeError, match="exit code 137"),
    ):
        docker_runner.run_docker_simulator(
            request=request(), seed=1, stage_dir=tmp_path, task_label="test",
            progress_poll_seconds=0.1, show_progress=False,
        )
    timing = json.loads((tmp_path / "provenance/raw/docker_wrapper.timing.json").read_text())
    assert timing["returncode"] == 137
    assert timing["seconds"] >= 0


@pytest.mark.parametrize("write_cidfile", [True, False])
def test_interrupt_removes_only_the_owned_container(tmp_path, write_cidfile):
    container_id = "a" * 64

    def process(command, **kwargs):
        if write_cidfile:
            Path(command[command.index("--cidfile") + 1]).write_text(container_id)
        return SimpleNamespace(wait=lambda: 0)

    def callback(progress):
        if progress["phase"] == "container_started":
            raise KeyboardInterrupt("cancel this run")

    with (
        patch.object(docker_runner, "_ensure_docker_cli"),
        patch.object(docker_runner, "_ensure_docker_image", return_value="local"),
        patch.object(docker_runner.subprocess, "Popen", side_effect=process) as popen,
        patch.object(docker_runner.subprocess, "run",
                     return_value=SimpleNamespace(returncode=0, stderr="")) as remove,
        pytest.raises(KeyboardInterrupt, match="cancel this run"),
    ):
        docker_runner.run_docker_simulator(
            request=request(), seed=1, stage_dir=tmp_path, task_label="test",
            progress_poll_seconds=0.1, show_progress=False, progress_callback=callback,
        )
    command = popen.call_args.args[0]
    owned_identifier = container_id if write_cidfile else command[command.index("--name") + 1]
    assert remove.call_args.args[0] == ["docker", "rm", "--force", owned_identifier]
    assert owned_identifier == container_id or owned_identifier.startswith("andrea_simulator_")
    assert json.loads((tmp_path / "provenance/raw/docker_wrapper.cleanup.json").read_text())["cancelled"]


@pytest.mark.parametrize("ram", [0, -1, True, float("inf"), float("nan")])
def test_invalid_memory_limit_cannot_start_container(tmp_path, ram):
    with (
        patch.object(docker_runner, "_ensure_docker_cli"),
        patch.object(docker_runner, "_ensure_docker_image", return_value="local"),
        patch.object(docker_runner.subprocess, "Popen") as popen,
        pytest.raises(ValueError, match="ram_gb"),
    ):
        docker_runner.run_docker_simulator(
            request=request(ram_gb=ram), seed=1, stage_dir=tmp_path, task_label="test",
            progress_poll_seconds=0.1, show_progress=False,
        )
    popen.assert_not_called()
