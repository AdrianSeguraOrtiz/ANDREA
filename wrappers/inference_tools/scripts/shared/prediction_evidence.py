"""Verified adaptation of retained benchmark executions for prediction checks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .cost_resume import input_manifest, same_measurement_context


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def artifact(path: Path, base: Path, **fields: Any) -> dict:
    return {
        "path": os.path.relpath(path.resolve(), base.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        **fields,
    }


def identity_for_config(
    config: dict, *, threads: int, ram_gb: float, profile_id: str
) -> dict:
    provenance = config["provenance"]
    hardware = {
        key: provenance[key]
        for key in (
            "cpu_model",
            "platform",
            "logical_cpus",
            "host_memory_bytes",
            "docker_server",
        )
    }
    if any(value is None or value == "" for value in hardware.values()):
        raise ValueError("Actual hardware provenance is incomplete.")
    inputs = config["input_profile"]
    recipe = {
        "params": config["params_profile"]["resolved_params"],
        "execution": config["execution_profile"],
        "temporal": inputs["temporal"],
        "temporal_fixture": inputs["temporal_fixture"],
    }
    return {
        "profile_id": profile_id,
        "image_id": provenance["image_id"],
        "params_sha256": digest(recipe),
        "source_sha256": provenance["source_sha256"],
        "hardware_id": digest(hardware),
        "threads": threads,
        "ram_gb": float(ram_gb),
    }


def load_training_observations(
    calibration_root: Path, cost_profile: dict
) -> list[dict]:
    """Retain actual raw row references; reject missing IDs or changed inputs."""
    name = cost_profile["profile_id"]
    config = cost_profile["benchmark_config"]
    expected_rows = {
        row["run_id"]: row
        for point in cost_profile["runtime_points"]
        for row in point["measurement_evidence"]["observations"]
        if row.get("status") == "ok"
    }
    found: dict[str, dict] = {}
    for path in sorted(calibration_root.rglob("measurements.json")):
        if path.parent.name != name:
            continue
        config_path = path.with_name("benchmark_config.json")
        actual_config = json.loads(config_path.read_text())
        if not same_measurement_context(actual_config, config):
            raise ValueError(
                f"Incompatible retained calibration context: {config_path}"
            )
        if any(
            actual_config["provenance"].get(key) != config["provenance"].get(key)
            for key in ("image_id", "source_sha256")
        ):
            raise ValueError(
                "Calibration image/source differs from the frozen cost snapshot."
            )
        rows = json.loads(path.read_text())
        for index, row in enumerate(rows):
            if row.get("status") != "ok":
                raise ValueError(
                    f"Failed calibration attempt retained in {path}; review before claiming independent accuracy."
                )
            run_id = row.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise ValueError(
                    "Calibration receipt lacks an actual captured execution run_id."
                )
            if run_id not in expected_rows:
                raise ValueError(
                    "Raw calibration execution is absent from the frozen cost snapshot."
                )
            if any(
                row.get(key) != expected_rows[run_id].get(key)
                for key in (
                    "status",
                    "seconds",
                    "genes",
                    "columns",
                    "threads",
                    "ram_gb",
                    "repeat",
                    "input_sha256",
                    "dimensions",
                )
            ):
                raise ValueError(
                    "Original calibration row contradicts its embedded cost observation."
                )
            io_dir = Path(row["io_dir"])
            if input_manifest(io_dir) != row.get("input_sha256"):
                raise ValueError(f"Calibration input inventory changed: {io_dir}")
            if run_id in found:
                raise ValueError(
                    f"Duplicate raw execution ID in calibration evidence: {run_id}"
                )
            found[run_id] = {
                "raw_row": row,
                "raw_receipt": path,
                "pointer": f"/{index}",
                "config": actual_config,
                "config_path": config_path,
                "io_dir": io_dir,
            }
    if set(found) != set(expected_rows) or not found:
        raise ValueError(
            "Raw calibration receipts do not completely match the frozen training observations."
        )
    return list(found.values())


def adapt_training_observations(
    entries: list[dict], output_dir: Path, profile: dict
) -> list[dict]:
    """Write explicit evidence adapters, without fabricating any timing or run ID."""
    output_dir.mkdir(parents=True, exist_ok=True)
    observations = []
    for entry in entries:
        row, io = entry["raw_row"], entry["io_dir"]
        identity = identity_for_config(
            entry["config"],
            threads=row["threads"],
            ram_gb=row["ram_gb"],
            profile_id=profile["profile_id"],
        )
        coordinates = {
            key: value for key, value in row["dimensions"].items() if value > 0
        }
        paths = [
            io / "expression.tsv",
            io / "params.json",
            io / "execution.json",
            *sorted((io / "extra").rglob("*")),
        ]
        inputs = [
            artifact(
                path,
                output_dir,
                role="primary" if path == io / "expression.tsv" else "shared",
            )
            for path in paths
            if path.is_file()
        ]
        outputs = [
            artifact(path, output_dir)
            for path in sorted((io / "out").rglob("*"))
            if path.is_file()
        ]
        if not outputs:
            raise ValueError(f"Calibration output inventory is missing: {io / 'out'}")
        adapter = {
            "status": row["status"],
            "seconds": row["seconds"],
            "run_id": row["run_id"],
            "identity": identity,
            "dimensions": coordinates,
            "input_sha256": row["input_sha256"],
            "raw_evidence": [
                artifact(entry["raw_receipt"], output_dir, pointer=entry["pointer"]),
                artifact(entry["config_path"], output_dir),
            ],
            "mapping": "Identity recipe/hardware hashes derive from retained configuration/provenance; seconds, execution ID, coordinates and inputs come from the original raw execution row.",
        }
        receipt = output_dir / f"training-{row['run_id']}.json"
        receipt.write_text(json.dumps(adapter, indent=2) + "\n")
        observations.append(
            {
                "id": row["run_id"],
                "receipt": artifact(receipt, output_dir),
                "identity": identity,
                "dimensions": coordinates,
                "inputs": inputs,
                "outputs": outputs,
            }
        )
    return observations
