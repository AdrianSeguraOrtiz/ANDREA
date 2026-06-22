from __future__ import annotations

from typing import Any


def start_immediate_background_thread(
    *, target: Any, kwargs: dict[str, Any] | None = None, daemon: bool = True
) -> None:
    del daemon
    target(**(kwargs or {}))
