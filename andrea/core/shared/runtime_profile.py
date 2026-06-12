"""Small runtime profiling helper for command reports and GUI progress."""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

ProgressCallback = Callable[[dict[str, Any]], None]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RuntimeProfile:
    """Collect coarse command-stage timings without coupling to any UI."""

    def __init__(self, progress_callback: Optional[ProgressCallback] = None) -> None:
        self._progress_callback = progress_callback
        self._timings: list[dict[str, Any]] = []

    @contextmanager
    def stage(self, stage: str, *, label: str, detail: str = "") -> Iterator[None]:
        started_at = utc_now_iso()
        started = time.perf_counter()
        self._notify(
            {
                "stage": stage,
                "status": "started",
                "label": label,
                "detail": detail,
                "started_at": started_at,
            }
        )
        try:
            yield
        finally:
            finished_at = utc_now_iso()
            elapsed_s = max(0.0, time.perf_counter() - started)
            entry = {
                "stage": stage,
                "label": label,
                "detail": detail,
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_s": round(elapsed_s, 6),
            }
            self._timings.append(entry)
            self._notify({**entry, "status": "finished"})

    def timings(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._timings]

    def _notify(self, payload: dict[str, Any]) -> None:
        if self._progress_callback is None:
            return
        self._progress_callback(dict(payload))
