"""Conservative, data-based reuse of inference benchmark observations.

Script digests are audit information, not scientific cache keys. Reuse instead
requires an immutable image, hardware/timing context, resolved parameters and
byte-identical generated inputs. Old runs can be imported from their retained
measurement directories; new points embed their receipts in cost.json.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


def point_key(value: dict[str, Any]) -> tuple[int, int, int, float]:
    return (int(value["genes"]), int(value["columns"]), int(value["threads"]), float(value["ram_gb"]))


def input_manifest(io_dir: Path) -> dict[str, str]:
    paths = [io_dir / name for name in ("expression.tsv", "params.json", "execution.json")]
    paths.extend(sorted((io_dir / "extra").rglob("*")))
    return {
        path.relative_to(io_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths if path.is_file()
    }


def measurement_context(config: dict[str, Any]) -> dict[str, Any]:
    provenance = config.get("provenance", {})
    # Notes and profile ids do not alter the task. Actual parameter/input bytes
    # are checked separately, including extras and execution.json.
    return {
        "image_id": provenance.get("image_id"),
        "cpu_model": provenance.get("cpu_model"),
        "logical_cpus": provenance.get("logical_cpus"),
        "host_memory_bytes": provenance.get("host_memory_bytes"),
        "platform": provenance.get("platform"),
        "docker_server": provenance.get("docker_server"),
        "timing_scope": provenance.get("timing_scope"),
        "seed": provenance.get("seed"),
        "params": config.get("params_profile", {}).get("resolved_params"),
        "execution": config.get("execution_profile"),
        "input": {k: v for k, v in config.get("input_profile", {}).items() if k != "notes"},
    }


def same_measurement_context(old: dict[str, Any], new: dict[str, Any]) -> bool:
    left, right = measurement_context(old), measurement_context(new)
    return bool(left.get("image_id") and left.get("cpu_model") and left == right)


def successful_observation(row: dict[str, Any]) -> bool:
    seconds = row.get("seconds")
    return (row.get("status") == "ok" and isinstance(row.get("repeat"), int)
            and row["repeat"] >= 1 and isinstance(seconds, (int, float))
            and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds > 0)


def complete_point(point: dict[str, Any], repeats: int) -> bool:
    return (
        point.get("status") == "ok" and point.get("repeats_total") == repeats
        and point.get("repeats_ok") == repeats and point.get("repeats_failed") == 0
        and point.get("ok_rate") == 1
        and not any(point.get("failure_breakdown", {}).values())
        and all(isinstance(point.get(key), (int, float)) and not isinstance(point[key], bool)
                and math.isfinite(point[key]) and point[key] > 0
                for key in ("seconds_p50", "seconds_p90"))
    )


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def find_evidence_profiles(roots: list[Path], tool_id: str, profile_id: str) -> list[Path]:
    found: set[Path] = set()
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in profile_id)
    for root in roots:
        # Accept the campaign root, one tool evidence directory, or one profile.
        candidates = [root / "benchmark_config.json", root / safe_id / "benchmark_config.json"]
        if root.is_dir():
            candidates.extend(root.glob(f"{tool_id}-*/{safe_id}/benchmark_config.json"))
            candidates.extend(root.glob(f"{tool_id}/{tool_id}-*/{safe_id}/benchmark_config.json"))
        for path in candidates:
            if path.is_file() and path.parent.name == safe_id:
                found.add(path.parent.resolve())
    return sorted(found)


def legacy_observations(
    *, directories: list[Path], config: dict[str, Any], key: tuple[int, int, int, float],
    hashes: dict[str, str], repeats: int,
) -> list[dict[str, Any]]:
    """Reuse only successful raw repeats whose retained input bytes still agree."""
    selected: dict[int, dict[str, Any]] = {}
    for directory in directories:
        old_config = read_json(directory / "benchmark_config.json", {})
        if not same_measurement_context(old_config, config):
            continue
        rows = read_json(directory / "measurements.json", [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not successful_observation(row):
                continue
            try:
                if point_key(row) != key or row["repeat"] > repeats or row["repeat"] in selected:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            # Newly imported receipts refer to their original IO directory.
            io_dir = Path(row["io_dir"]) if row.get("io_dir") else directory / (
                f"{directory.name}_g{key[0]}_c{key[1]}_t{key[2]}_m{key[3]:g}_r{row['repeat']}"
            ) / "io"
            if input_manifest(io_dir) != hashes or "expression.tsv" not in hashes:
                continue
            selected[row["repeat"]] = {
                **row, "io_dir": str(io_dir),
                "reused_from": str(directory / "measurements.json"),
                "measurement_provenance": row.get("measurement_provenance", old_config.get("provenance", {})),
            }
    return [selected[k] for k in sorted(selected)]


def embedded_observations(
    *, point: dict[str, Any], config: dict[str, Any], hashes: dict[str, str], repeats: int,
) -> list[dict[str, Any]]:
    receipt = point.get("measurement_evidence", {})
    if (receipt.get("input_sha256") != hashes
            or receipt.get("measurement_context") != measurement_context(config)):
        return []
    rows = receipt.get("observations", [])
    selected: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not successful_observation(row):
            continue
        try:
            if point_key(row) == point_key(point) and row["repeat"] <= repeats:
                selected.setdefault(row["repeat"], dict(row))
        except (KeyError, TypeError, ValueError):
            continue
    return [selected[k] for k in sorted(selected)]
