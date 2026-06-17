"""Threading contract helpers for inference ToolSpecs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolThreading:
    supported: bool
    default_threads: int
    max_threads: int
    upstream_mapping: str


def _fallback_threading() -> ToolThreading:
    return ToolThreading(
        supported=False,
        default_threads=1,
        max_threads=1,
        upstream_mapping="invalid_or_missing_toolspec_runtime_resources",
    )


def resolve_tool_threading(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
) -> tuple[ToolThreading, list[str]]:
    runtime_resources = toolspec.get("runtime_resources")
    if not isinstance(runtime_resources, dict):
        return _fallback_threading(), [
            f"[{tool_id}] toolspec.runtime_resources.threading is missing; using threads=1 fallback."
        ]

    threading = runtime_resources.get("threading")
    if not isinstance(threading, dict):
        return _fallback_threading(), [
            f"[{tool_id}] toolspec.runtime_resources.threading is invalid; using threads=1 fallback."
        ]

    supported = threading.get("supported")
    default_threads = threading.get("default_threads")
    max_threads = threading.get("max_threads")
    upstream_mapping = str(threading.get("upstream_mapping", "")).strip()
    if (
        not isinstance(supported, bool)
        or isinstance(default_threads, bool)
        or not isinstance(default_threads, int)
        or isinstance(max_threads, bool)
        or not isinstance(max_threads, int)
        or default_threads < 1
        or max_threads < 1
        or default_threads > max_threads
        or not upstream_mapping
    ):
        return _fallback_threading(), [
            f"[{tool_id}] toolspec.runtime_resources.threading is invalid; using threads=1 fallback."
        ]

    if not supported:
        return (
            ToolThreading(
                supported=False,
                default_threads=1,
                max_threads=1,
                upstream_mapping=upstream_mapping,
            ),
            [],
        )

    return (
        ToolThreading(
            supported=True,
            default_threads=default_threads,
            max_threads=max_threads,
            upstream_mapping=upstream_mapping,
        ),
        [],
    )


def thread_count_allowed_by_tool(threading: ToolThreading, threads: int) -> bool:
    if threads < 1:
        return False
    if not threading.supported:
        return threads == 1
    return threads <= threading.max_threads


def default_threads_for_limits(threading: ToolThreading, *, max_cores: int) -> int:
    return max(1, min(int(max_cores), threading.default_threads, threading.max_threads))
