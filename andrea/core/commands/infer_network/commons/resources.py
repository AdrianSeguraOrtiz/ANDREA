"""Strict operational resource requests for inference runs."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any


def normalize_cpuset_cpus(raw: Any, *, source: str) -> tuple[int, ...]:
    """Validate the canonical JSON representation of a logical CPU set."""

    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{source} must be a non-empty array of logical CPU indices")
    cpus: list[int] = []
    for index, value in enumerate(raw):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(
                f"{source}[{index}] must be a non-negative integer logical CPU index"
            )
        cpus.append(value)
    if cpus != sorted(set(cpus)):
        raise ValueError(
            f"{source} must be strictly increasing and contain no duplicates"
        )
    return tuple(cpus)


def effective_process_cpuset() -> tuple[int, ...]:
    """Return the logical CPUs on which the current process may execute."""

    if not hasattr(os, "sched_getaffinity"):
        raise RuntimeError(
            "CPU affinity cannot be verified on this platform; omit cpuset_cpus"
        )
    cpus = tuple(sorted(int(cpu) for cpu in os.sched_getaffinity(0)))
    if not cpus:
        raise RuntimeError("The current process has an empty effective CPU affinity")
    return cpus


def validate_cpuset_available(
    requested: Iterable[int],
    *,
    source: str,
    available: Iterable[int] | None = None,
) -> tuple[int, ...]:
    cpus = tuple(int(cpu) for cpu in requested)
    allowed = tuple(available) if available is not None else effective_process_cpuset()
    unavailable = sorted(set(cpus) - set(allowed))
    if unavailable:
        raise ValueError(
            f"{source} contains CPU(s) outside the effective process affinity: "
            f"{unavailable}; available CPUs are {list(allowed)}"
        )
    return cpus


def docker_cpuset(cpus: Iterable[int]) -> str:
    """Render a canonical CPU list accepted by Docker's --cpuset-cpus flag."""

    return ",".join(str(int(cpu)) for cpu in cpus)


def parse_linux_cpuset(raw: str) -> tuple[int, ...]:
    """Parse Linux/Docker CPU-list syntax into a canonical tuple."""

    cpus: set[int] = set()
    text = str(raw).strip()
    if not text:
        return ()
    for token in text.split(","):
        if not token or token != token.strip():
            raise ValueError(f"Invalid Linux CPU-list token: {token!r}")
        if "-" not in token:
            value = int(token)
            if value < 0:
                raise ValueError("Logical CPU indices must be non-negative")
            cpus.add(value)
            continue
        parts = token.split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid Linux CPU range: {token!r}")
        start, end = (int(part) for part in parts)
        if start < 0 or end < start:
            raise ValueError(f"Invalid Linux CPU range: {token!r}")
        cpus.update(range(start, end + 1))
    return tuple(sorted(cpus))
