"""Verify retained inference observations without manufacturing measurements.

Raw receipts and their original provenance remain immutable. Derived receipts
identify both the retained observation and its actual measurement budget.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = frozenset({"ok", "timeout", "oom", "error"})


class VerifiedSourceKernels(dict):
    """Source proofs plus explicit, evidence-backed executor reviews.

    A source change is incompatible by default. A reviewed change to resource
    orchestration may retain observations without relabelling their original
    kernel, timing, allocation or provenance. The review is part of the audit
    evidence, rather than an implicit exception for a particular tool.
    """

    def __init__(self):
        super().__init__()
        self.reviews: dict[tuple[str, str], dict[str, str]] = {}

    def load_reviews(self, manifest: dict[str, Any], current_kernel: str) -> None:
        for review in manifest.get("measurement_kernel_reviews", []):
            old = review.get("from_kernel_sha256")
            new = review.get("to_kernel_sha256")
            if (
                old not in self.values()
                or new != current_kernel
                or review.get("scope") != "cost_executor_resource_orchestration"
            ):
                raise ValueError(
                    "Executor review does not match the verified source and current kernel."
                )
            path = Path(review.get("evidence_path", ""))
            digest = review.get("evidence_sha256")
            if (
                not path.is_absolute()
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("Executor review evidence is missing or changed.")
            evidence = read_json(path, {})
            if (
                evidence.get("from_kernel_sha256") != old
                or evidence.get("to_kernel_sha256") != new
                or evidence.get("conclusion") != "timing_protocol_preserved"
            ):
                raise ValueError(
                    "Executor review does not establish the declared timing protocol."
                )
            self.reviews[(old, new)] = {
                "path": str(path),
                "sha256": digest,
                "from_kernel_sha256": old,
                "to_kernel_sha256": new,
            }


def point_key(value: dict[str, Any]) -> tuple[int, int, int, float]:
    return (
        int(value["genes"]),
        int(value["columns"]),
        int(value["threads"]),
        float(value["ram_gb"]),
    )


def input_manifest(io_dir: Path) -> dict[str, str]:
    paths = [
        io_dir / name for name in ("expression.tsv", "params.json", "execution.json")
    ]
    paths.extend(sorted((io_dir / "extra").rglob("*")))
    return {
        path.relative_to(io_dir)
        .as_posix(): hashlib.sha256(path.read_bytes())
        .hexdigest()
        for path in paths
        if path.is_file()
    }


def measurement_context(config: dict[str, Any]) -> dict[str, Any]:
    provenance = config.get("provenance", {})
    return {
        **{
            key: provenance.get(key)
            for key in (
                "image_id",
                "cpu_model",
                "logical_cpus",
                "host_memory_bytes",
                "platform",
                "docker_server",
                "timing_scope",
                "measurement_kernel_sha256",
                "spec_sha256",
                "build_context_sha256",
                "seed",
            )
        },
        "params": config.get("params_profile", {}).get("resolved_params"),
        "execution": config.get("execution_profile"),
        "input": {
            k: v for k, v in config.get("input_profile", {}).items() if k != "notes"
        },
    }


def same_measurement_context(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    kernel_review: dict[str, str] | None = None,
) -> bool:
    left, right = measurement_context(old), measurement_context(new)
    old_kernel, new_kernel = left.get("measurement_kernel_sha256"), right.get(
        "measurement_kernel_sha256"
    )
    if kernel_review is not None and (
        kernel_review.get("from_kernel_sha256") == old_kernel
        and kernel_review.get("to_kernel_sha256") == new_kernel
    ):
        # Only implementation identity is covered by this explicit review.
        # Image, input, hardware, parameters and wall-clock scope still match.
        left = {**left, "measurement_kernel_sha256": new_kernel}
    return bool(
        left.get("image_id")
        and left.get("cpu_model")
        and left.get("measurement_kernel_sha256")
        and left == right
    )


def terminal_observation(row: dict[str, Any]) -> bool:
    seconds = row.get("seconds")
    return (
        row.get("status") in TERMINAL_STATUSES
        and isinstance(row.get("repeat"), int)
        and not isinstance(row["repeat"], bool)
        and row["repeat"] >= 1
        and isinstance(seconds, (int, float))
        and not isinstance(seconds, bool)
        and math.isfinite(seconds)
        and seconds > 0
    )


def successful_observation(row: dict[str, Any]) -> bool:
    return row.get("status") == "ok" and terminal_observation(row)


def complete_point(point: dict[str, Any], repeats: int) -> bool:
    """Whether all requested slots have real terminal observations (not all OK)."""
    return (
        point.get("status") in TERMINAL_STATUSES | {"partial"}
        and point.get("repeats_total") == repeats
        and point.get("repeats_ok", -1) + point.get("repeats_failed", -1) == repeats
    )


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def find_evidence_profiles(
    roots: list[Path], tool_id: str, profile_id: str
) -> list[Path]:
    found: set[Path] = set()
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in profile_id)
    for root in roots:
        candidates = [
            root / "benchmark_config.json",
            root / safe_id / "benchmark_config.json",
        ]
        if root.is_dir():
            candidates.extend(root.glob(f"{tool_id}-*/{safe_id}/benchmark_config.json"))
            candidates.extend(
                root.glob(f"{tool_id}/{tool_id}-*/{safe_id}/benchmark_config.json")
            )
        for path in candidates:
            if path.is_file() and path.parent.name == safe_id:
                found.add(path.parent.resolve())
    return sorted(found)


def _identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("run_id"),
        point_key(row),
        row.get("repeat"),
        row.get("status"),
        row.get("seconds"),
        row.get("io_dir"),
        row.get("input_sha256"),
        row.get("dimensions"),
    )


def _original_receipt(
    row: dict[str, Any], path: Path, seen: frozenset[Path] = frozenset()
) -> tuple[dict[str, Any], dict[str, Any], Path, int] | None:
    """Resolve reused_from to the actual native observation; reject altered copies."""
    path = path.resolve()
    if path in seen or not row.get("run_id"):
        return None
    rows = read_json(path, [])
    if not isinstance(rows, list):
        return None
    matches = [
        (i, value)
        for i, value in enumerate(rows)
        if isinstance(value, dict) and value.get("run_id") == row["run_id"]
    ]
    if not matches:
        return None
    if any(_identity(value) != _identity(row) for _, value in matches):
        raise ValueError(
            f"Conflicting retained copies of run_id {row['run_id']} in {path}"
        )
    index, native = matches[0]
    reused = native.get("reused_from")
    if reused:
        target = Path(reused)
        if not target.is_absolute():
            return None
        if target.resolve() != path:
            return _original_receipt(native, target, seen | {path})
    config = read_json(path.parent / "benchmark_config.json", {})
    if not isinstance(config, dict):
        return None
    return native, config, path, index


def _verified_observation(
    *,
    row: dict[str, Any],
    path: Path,
    config: dict[str, Any],
    hashes: dict[str, str],
    repeats: int,
    source_kernels: dict[str, str],
) -> dict[str, Any] | None:
    if not terminal_observation(row) or row["repeat"] > repeats:
        return None
    original = _original_receipt(row, path)
    if original is None:
        return None
    native, old_config, origin, index = original
    provenance = native.get("measurement_provenance", old_config.get("provenance", {}))
    kernel = provenance.get("measurement_kernel_sha256") or source_kernels.get(
        provenance.get("source_sha256")
    )
    if not kernel or (row.get("measurement_kernel_sha256") not in (None, kernel)):
        return None
    current_kernel = config.get("provenance", {}).get("measurement_kernel_sha256")
    review = getattr(source_kernels, "reviews", {}).get((kernel, current_kernel))
    if not same_measurement_context(
        {
            **old_config,
            "provenance": {**provenance, "measurement_kernel_sha256": kernel},
        },
        config,
        kernel_review=review,
    ):
        return None
    budget = old_config.get("timeout_seconds")
    requested = config.get("timeout_seconds")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in (budget, requested)
    ):
        return None
    if native.get("measurement_timeout_seconds", budget) != budget:
        return None
    if row.get("measurement_timeout_seconds", budget) != budget:
        return None
    # Successful runs within the requested cap remain useful even if their
    # original cap was larger. Censored/failed outcomes require that exact cap.
    if native["status"] == "ok":
        if requested > 0 and native["seconds"] > requested:
            return None
    elif budget != requested:
        return None
    if native["status"] == "timeout":
        declared = re.search(
            r"Run exceeded timeout of ([0-9]+(?:\.[0-9]+)?) seconds",
            native.get("error", ""),
        )
        if (
            budget <= 0
            or native["seconds"] < budget
            or (declared and float(declared.group(1)) != budget)
            or ("measurement_timeout_seconds" not in native and declared is None)
        ):
            return None
    io_value = native.get("io_dir")
    if not isinstance(io_value, str) or not io_value or io_value != io_value.strip():
        return None
    io_dir = Path(io_value)
    if not io_dir.is_absolute() or not io_dir.is_dir():
        return None
    if (
        "expression.tsv" not in hashes
        or native.get("input_sha256") != hashes
        or input_manifest(io_dir) != hashes
    ):
        return None
    return {
        **native,
        **({"measurement_kernel_review": review} if review else {}),
        "io_dir": str(io_dir),
        "reused_from": str(origin),
        "measurement_provenance": provenance,
        "measurement_kernel_sha256": kernel,
        "measurement_timeout_seconds": budget,
        "retained_receipt": {
            "path": str(origin),
            "pointer": f"/{index}",
            "sha256": hashlib.sha256(origin.read_bytes()).hexdigest(),
        },
    }


def retained_observations(
    *,
    directories: list[Path],
    config: dict[str, Any],
    key: tuple[int, int, int, float],
    hashes: dict[str, str],
    repeats: int,
    source_kernels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    selected: dict[int, dict[str, Any]] = {}
    for directory in directories:
        path = directory / "measurements.json"
        rows = read_json(path, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                if point_key(row) != key:
                    continue
                verified = _verified_observation(
                    row=row,
                    path=path,
                    config=config,
                    hashes=hashes,
                    repeats=repeats,
                    source_kernels=source_kernels or {},
                )
            except (KeyError, TypeError):
                continue
            if verified is not None:
                selected.setdefault(row["repeat"], verified)
    return [selected[k] for k in sorted(selected)]


def embedded_observations(
    *,
    point: dict[str, Any],
    config: dict[str, Any],
    hashes: dict[str, str],
    repeats: int,
    source_kernels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Embedded metadata is an index, never sufficient proof without the raw file."""
    receipt = point.get("measurement_evidence", {})
    if receipt.get("input_sha256") != hashes:
        return []
    selected: dict[int, dict[str, Any]] = {}
    for row in receipt.get("observations", []):
        if not isinstance(row, dict):
            continue
        io = row.get("io_dir")
        raw = row.get("reused_from") or row.get("retained_receipt", {}).get("path")
        if raw is None and isinstance(io, str) and Path(io).is_absolute():
            raw = str(Path(io).parent.parent / "measurements.json")
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            continue
        try:
            if point_key(row) != point_key(point):
                continue
            verified = _verified_observation(
                row=row,
                path=Path(raw),
                config=config,
                hashes=hashes,
                repeats=repeats,
                source_kernels=source_kernels or {},
            )
        except (KeyError, TypeError):
            continue
        if verified is not None:
            selected.setdefault(row["repeat"], verified)
    return [selected[k] for k in sorted(selected)]
