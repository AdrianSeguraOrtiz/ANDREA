"""Shared container runtime helpers."""

from __future__ import annotations

import shutil
import subprocess


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=True,
    )


def ensure_docker_cli(*, check_daemon: bool) -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker CLI is not available in PATH")
    probe = ["docker", "info"] if check_daemon else ["docker", "--version"]
    result = run_cmd(probe)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        if check_daemon:
            raise RuntimeError(f"Docker daemon is not available. Details: {details}")
        raise RuntimeError(f"Docker CLI is not available. Details: {details}")


def docker_image_exists(image: str) -> bool:
    result = run_cmd(["docker", "image", "inspect", image])
    return result.returncode == 0


def pull_docker_image(image: str) -> None:
    result = run_cmd(["docker", "pull", image])
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Failed to pull docker image '{image}': {details}")
