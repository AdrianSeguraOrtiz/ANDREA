"""Shared background-job helpers for local GUI servers."""

from __future__ import annotations

import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def start_background_thread(
    *, target: Any, kwargs: dict[str, Any], daemon: bool = True
) -> None:
    """Start one background worker thread for GUI job execution."""
    threading.Thread(target=target, kwargs=kwargs, daemon=daemon).start()


def run_parallel(tasks: list[Any], *, max_workers: int) -> None:
    """Run callables in bounded thread batches and raise the first task error."""
    if len(tasks) <= 1 or max_workers <= 1:
        for task in tasks:
            task()
        return
    for start in range(0, len(tasks), max_workers):
        batch = tasks[start : start + max_workers]
        errors: list[BaseException | None] = [None for _item in batch]
        threads = []
        for idx, task in enumerate(batch):

            def _runner(task=task, idx=idx) -> None:  # noqa: B023
                try:
                    task()
                except BaseException as exc:  # noqa: BLE001
                    errors[idx] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()
        first_error = next((error for error in errors if error is not None), None)
        if first_error is not None:
            raise first_error


def set_job_progress(
    *,
    state: Any,
    job_id: str,
    stage: str,
    label: str,
    detail: str = "",
    percent: int,
) -> None:
    """Update the common progress fields on a GUI job under its state lock."""
    with state.lock:
        job = state.jobs[job_id]
        job.stage = stage
        job.progress_percent = max(0, min(100, int(percent)))
        job.progress_label = label
        job.progress_detail = detail


@contextmanager
def timed_job_stage(
    *,
    state: Any,
    job_id: str,
    stage: str,
    label: str,
    detail: str = "",
    percent: int,
):
    """Set GUI job progress and append a timing entry when the block exits."""
    started_at = utc_now()
    started = time.perf_counter()
    set_job_progress(
        state=state,
        job_id=job_id,
        stage=stage,
        label=label,
        detail=detail,
        percent=percent,
    )
    try:
        yield
    finally:
        finished_at = utc_now()
        elapsed_s = max(0.0, time.perf_counter() - started)
        with state.lock:
            job = state.jobs[job_id]
            job.timings.append(
                {
                    "stage": stage,
                    "label": label,
                    "detail": detail,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "elapsed_s": round(elapsed_s, 6),
                }
            )


def make_core_progress_callback(
    *,
    state: Any,
    job_id: str,
    stage_progress: dict[str, int],
    default_percent: int = 70,
):
    """Build a progress callback compatible with core RuntimeProfile events."""

    def _callback(payload: dict[str, Any]) -> None:
        if payload.get("status") == "finished":
            with state.lock:
                job = state.jobs[job_id]
                job.timings.append(
                    {
                        "stage": str(payload.get("stage") or ""),
                        "label": str(payload.get("label") or ""),
                        "detail": str(payload.get("detail") or ""),
                        "started_at": str(payload.get("started_at") or ""),
                        "finished_at": str(payload.get("finished_at") or ""),
                        "elapsed_s": payload.get("elapsed_s"),
                    }
                )
            return
        if payload.get("status") != "started":
            return
        stage = str(payload.get("stage") or "computing")
        set_job_progress(
            state=state,
            job_id=job_id,
            stage=stage,
            label=str(payload.get("label") or stage.replace("_", " ").title()),
            detail=str(payload.get("detail") or ""),
            percent=stage_progress.get(stage, default_percent),
        )

    return _callback
