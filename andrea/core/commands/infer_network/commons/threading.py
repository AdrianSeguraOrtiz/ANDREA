"""Threading contract helpers for inference ToolSpecs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolThreading:
    supported: bool
    default_threads: int
    max_threads: int | None
    upstream_mapping: str


def resolve_tool_threading(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
) -> ToolThreading:
    runtime_resources = toolspec.get("runtime_resources")
    if not isinstance(runtime_resources, dict):
        raise ValueError(f"[{tool_id}] toolspec.runtime_resources must be an object")

    threading = runtime_resources.get("threading")
    if not isinstance(threading, dict):
        raise ValueError(
            f"[{tool_id}] toolspec.runtime_resources.threading must be an object"
        )

    supported = threading.get("supported")
    default_threads = threading.get("default_threads")
    max_threads = threading.get("max_threads")
    upstream_mapping_raw = threading.get("upstream_mapping")
    upstream_mapping = (
        upstream_mapping_raw.strip()
        if isinstance(upstream_mapping_raw, str)
        else ""
    )
    if (
        not isinstance(supported, bool)
        or isinstance(default_threads, bool)
        or not isinstance(default_threads, int)
        or default_threads < 1
        or (
            max_threads is not None
            and (
                isinstance(max_threads, bool)
                or not isinstance(max_threads, int)
                or max_threads < 1
                or default_threads > max_threads
            )
        )
        or not upstream_mapping
        or upstream_mapping_raw != upstream_mapping
        or set(threading)
        != {"supported", "default_threads", "max_threads", "upstream_mapping"}
        or (not supported and (default_threads != 1 or max_threads != 1))
    ):
        raise ValueError(
            f"[{tool_id}] toolspec.runtime_resources.threading is invalid"
        )

    if not supported:
        return ToolThreading(
            supported=False,
            default_threads=1,
            max_threads=1,
            upstream_mapping=upstream_mapping,
        )

    return ToolThreading(
        supported=True,
        default_threads=default_threads,
        max_threads=max_threads,
        upstream_mapping=upstream_mapping,
    )


def thread_count_allowed_by_tool(threading: ToolThreading, threads: int) -> bool:
    if threads < 1:
        return False
    if not threading.supported:
        return threads == 1
    return threading.max_threads is None or threads <= threading.max_threads


def default_threads_for_limits(threading: ToolThreading, *, max_cores: int) -> int:
    return max(1, min(int(max_cores), threading.default_threads))
