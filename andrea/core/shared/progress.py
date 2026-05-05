"""Shared progress event normalization helpers."""

from __future__ import annotations

from typing import Any


def progress_snapshot(
    payload: dict[str, Any],
    *,
    phase_percent: dict[str, int],
) -> tuple[int, str, str, str]:
    status = str(payload.get("status", "running"))
    phase = str(payload.get("phase", "unknown"))
    message = str(payload.get("message", ""))
    raw_percent = payload.get("percent")
    if raw_percent is None:
        percent = phase_percent.get(phase, 0)
    else:
        percent = int(raw_percent)
    if status == "completed":
        percent = 100
        status = "completed"
    elif status == "done":
        status = "running"
    elif status == "failed":
        percent = 100
    return max(0, min(100, percent)), status, phase, message
