"""Docker backend runner for generate-data."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from andrea.core.shared.container_runtime import (
    docker_image_exists as _docker_image_exists,
    ensure_docker_cli as _shared_ensure_docker_cli,
    pull_docker_image,
)

from ..shared import ResolvedSimulatorRun, _write_json

_PULLED_IMAGES: set[str] = set()
_IMAGE_LOCK = threading.Lock()


def _ensure_docker_cli() -> None:
    _shared_ensure_docker_cli(check_daemon=True)


def _pull_image(*, image: str) -> str:
    if image in _PULLED_IMAGES and _docker_image_exists(image):
        return "pulled"
    pull_docker_image(image)
    _PULLED_IMAGES.add(image)
    return "pulled"


def _ensure_docker_image(*, simulator_id: str, image: str) -> str:
    with _IMAGE_LOCK:
        if _docker_image_exists(image):
            return "local"

        try:
            return _pull_image(image=image)
        except RuntimeError as exc:
            pull_error = str(exc)

        message = [f"Could not prepare docker image '{image}' for '{simulator_id}'."]
        message.append(pull_error)
        raise RuntimeError(" ".join(message))


def _read_progress(progress_path: Path) -> dict[str, object] | None:
    try:
        return json.loads(progress_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def run_docker_simulator(
    *,
    request: ResolvedSimulatorRun,
    seed: int,
    stage_dir: Path,
    task_label: str,
    progress_poll_seconds: float,
    show_progress: bool,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    image = str(request.simulator_spec.get("docker_image", "")).strip()
    if not image:
        raise RuntimeError(f"Simulator '{request.simulator_id}' has no docker_image")

    if progress_callback is not None:
        progress_callback(
            {
                "status": "running",
                "phase": "prepare_image",
                "message": f"Preparing Docker image {image}",
            }
        )
    _ensure_docker_cli()
    image_origin = _ensure_docker_image(simulator_id=request.simulator_id, image=image)

    stage_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = stage_dir / "provenance" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=f"andrea_generate_data_{request.simulator_id}_"
    ) as tmp:
        request_dir = Path(tmp) / "request"
        inputs_dir = Path(tmp) / "inputs"
        request_dir.mkdir(parents=True, exist_ok=True)
        inputs_dir.mkdir(parents=True, exist_ok=True)
        mounted_inputs: dict[str, str] = {}
        for input_id, source_path in request.resolved_input_paths.items():
            staged_path = inputs_dir / input_id
            if source_path.is_dir():
                shutil.copytree(source_path, staged_path)
            else:
                shutil.copy2(source_path, staged_path)
            mounted_inputs[input_id] = f"/work/inputs/{input_id}"

        request_payload = {
            "schema_version": "1.0",
            "run_id": request.run_id,
            "simulator_id": request.simulator_id,
            "profile": request.profile,
            "seed": int(seed),
            "effective_extras": list(request.effective_extras),
            "native_outputs": list(request.native_outputs),
            "inputs": request.inputs,
            "mounted_inputs": mounted_inputs,
            "params": dict(request.simulator_params),
            "runtime_resources": dict(request.runtime_resources),
            "output_dir_in_container": "/work/out",
        }
        request_path = request_dir / "simulator-run-request.json"
        _write_json(request_path, request_payload)

        cmd = ["docker", "run", "--rm"]
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        cmd.extend(
            [
                "-v",
                f"{request_dir}:/work/request:ro",
                "-v",
                f"{inputs_dir}:/work/inputs:ro",
                "-v",
                f"{stage_dir}:/work/out",
                image,
            ]
        )

        stdout_path = raw_dir / "docker_wrapper.stdout.log"
        stderr_path = raw_dir / "docker_wrapper.stderr.log"
        progress_path = stage_dir / "progress.json"
        with (
            stdout_path.open("w", encoding="utf-8") as stdout_fh,
            stderr_path.open("w", encoding="utf-8") as stderr_fh,
        ):
            proc = subprocess.Popen(
                cmd,
                text=True,
                stdout=stdout_fh,
                stderr=stderr_fh,
            )
            last_progress: str | None = None
            if progress_callback is not None:
                progress_callback(
                    {
                        "status": "running",
                        "phase": "container_started",
                        "message": f"Container started for {task_label}",
                    }
                )
            while proc.poll() is None:
                if progress_callback is not None:
                    progress_payload = _read_progress(progress_path)
                    if progress_payload is not None:
                        rendered = json.dumps(progress_payload, sort_keys=True)
                        if rendered != last_progress:
                            progress_callback(progress_payload)
                            last_progress = rendered
                time.sleep(max(0.05, float(progress_poll_seconds)))
            returncode = proc.wait()
            if progress_callback is not None:
                progress_payload = _read_progress(progress_path)
                if progress_payload is not None:
                    rendered = json.dumps(progress_payload, sort_keys=True)
                    if rendered != last_progress:
                        progress_callback(progress_payload)
        (raw_dir / "docker_wrapper.request.json").write_text(
            request_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (raw_dir / "docker_wrapper.image.txt").write_text(
            image + "\n", encoding="utf-8"
        )
        (raw_dir / "docker_wrapper.image_origin.txt").write_text(
            image_origin + "\n", encoding="utf-8"
        )
        if returncode != 0:
            details = (
                stderr_path.read_text(encoding="utf-8")
                or stdout_path.read_text(encoding="utf-8")
                or ""
            ).strip()
            raise RuntimeError(
                f"Docker simulator '{request.simulator_id}' failed with exit code "
                f"{returncode}: {details}"
            )

    manifest_path = stage_dir / "simulator-output-manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(
            f"Docker simulator '{request.simulator_id}' did not produce simulator-output-manifest.json"
        )
    return manifest_path
