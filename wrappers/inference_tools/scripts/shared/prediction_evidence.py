"""Verified adaptation of retained benchmark executions for prediction checks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from .cost_resume import (
    input_manifest,
    same_measurement_context,
    successful_observation,
)


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


def _collect_calibration_entries(roots: list[Path], cost_profile: dict) -> list[dict]:
    """Inventory every attempt, following selected executions to their originals."""
    name = cost_profile["profile_id"]
    selected_ids = {
        row.get("run_id")
        for point in cost_profile["runtime_points"]
        for row in point["measurement_evidence"]["observations"]
    }
    paths = {
        path.resolve()
        for root in roots
        for path in root.rglob("measurements.json")
        if path.parent.name == name
    }
    entries = []
    visited = set()
    pending = sorted(paths)
    while pending:
        path = pending.pop(0)
        if path in visited:
            continue
        visited.add(path)
        config_path = path.with_name("benchmark_config.json")
        try:
            rows = json.loads(path.read_text())
            if not isinstance(rows, list):
                raise ValueError("Raw measurement document is not an array.")
        except (OSError, ValueError) as exc:
            entries.append(
                {
                    "raw_receipt": path,
                    "pointer": "",
                    "config_path": config_path,
                    "raw_row": None,
                    "classification": "malformed_receipt",
                    "diagnostic": str(exc),
                }
            )
            continue
        for index, row in enumerate(rows):
            run_id = row.get("run_id") if isinstance(row, dict) else None
            status = row.get("status") if isinstance(row, dict) else None
            if isinstance(run_id, str) and run_id in selected_ids:
                classification = "selected_training"
                reused = row.get("reused_from")
                if isinstance(reused, str) and reused:
                    source = Path(reused)
                    if not source.is_absolute():
                        source = path.parent / source
                    if source.is_file() and source.resolve() not in visited:
                        pending.append(source.resolve())
            elif status == "ok":
                classification = "unselected_success"
            elif isinstance(row, dict):
                classification = "retained_failure"
            else:
                classification = "malformed_row"
            entries.append(
                {
                    "raw_receipt": path,
                    "pointer": f"/{index}",
                    "config_path": config_path,
                    "raw_row": row,
                    "classification": classification,
                }
            )
    return entries


def _write_calibration_review(
    path: Path,
    cost_profile: dict,
    inventory: list[dict],
    *,
    verification_status: str,
    error: str | None = None,
) -> None:
    """Write review references separately; excluded attempts never become timings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    counts: dict[str, int] = {}
    for entry in inventory:
        classification = entry["classification"]
        counts[classification] = counts.get(classification, 0) + 1
        row = entry["raw_row"]
        record = {
            "classification": classification,
            "run_id": row.get("run_id") if isinstance(row, dict) else None,
            "status": row.get("status") if isinstance(row, dict) else None,
            "raw_evidence": artifact(
                entry["raw_receipt"], path.parent, pointer=entry["pointer"]
            ),
        }
        if entry["config_path"].is_file():
            record["config"] = artifact(entry["config_path"], path.parent)
        if entry.get("diagnostic"):
            record["diagnostic"] = entry["diagnostic"]
        rows.append(record)
    report = {
        "profile_id": cost_profile["profile_id"],
        "expected_run_ids": sorted(
            {
                row.get("run_id")
                for point in cost_profile["runtime_points"]
                for row in point["measurement_evidence"]["observations"]
                if isinstance(row.get("run_id"), str)
            }
        ),
        "verification_status": verification_status,
        "row_counts": counts,
        "rows": rows,
        "selection_policy": "Only successful execution IDs explicitly embedded in the frozen cost profile are training. Other retained attempts are inventoried without supplying training timings.",
    }
    if error is not None:
        report["error"] = error
    path.write_text(json.dumps(report, indent=2) + "\n")


def load_training_observations(
    calibration_root: Path | Sequence[Path],
    cost_profile: dict,
    *,
    review_path: Path | None = None,
) -> list[dict]:
    """Verify selected training and every retained copy, inventorying other attempts."""
    roots = (
        [calibration_root]
        if isinstance(calibration_root, Path)
        else list(calibration_root)
    )
    inventory = _collect_calibration_entries(roots, cost_profile)
    try:
        found = _verify_training_observations(inventory, cost_profile)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        if review_path is not None:
            _write_calibration_review(
                review_path,
                cost_profile,
                inventory,
                verification_status="rejected",
                error=str(exc),
            )
        raise
    if review_path is not None:
        _write_calibration_review(
            review_path, cost_profile, inventory, verification_status="verified"
        )
    for entry in found:
        entry["review_inventory"] = inventory
        entry["review_path"] = review_path
    return found


def _verify_training_observations(
    inventory: list[dict], cost_profile: dict
) -> list[dict]:
    config = cost_profile["benchmark_config"]
    expected_rows = {}
    for point in cost_profile["runtime_points"]:
        for row in point["measurement_evidence"]["observations"]:
            run_id = row.get("run_id")
            if (
                not isinstance(run_id, str)
                or not run_id
                or not successful_observation(row)
            ):
                raise ValueError(
                    "Frozen training observation lacks a successful execution ID/timing."
                )
            if run_id in expected_rows:
                raise ValueError(
                    "Frozen training observations contain a repeated execution ID."
                )
            embedded_provenance = row.get(
                "measurement_provenance", config["provenance"]
            )
            if any(
                embedded_provenance.get(key) != config["provenance"].get(key)
                for key in (
                    "image_id",
                    "source_sha256",
                    "cpu_model",
                    "platform",
                    "logical_cpus",
                    "host_memory_bytes",
                    "docker_server",
                )
            ):
                raise ValueError(
                    "Embedded training provenance contradicts the frozen cost snapshot."
                )
            expected_rows[run_id] = row
    candidates: dict[str, list[dict]] = {}
    selected_paths = sorted(
        {
            entry["raw_receipt"]
            for entry in inventory
            if entry["classification"] == "selected_training"
        }
    )
    for path in selected_paths:
        config_path = path.with_name("benchmark_config.json")
        actual_config = json.loads(config_path.read_text())
        if not same_measurement_context(actual_config, config):
            raise ValueError(
                f"Incompatible retained calibration context: {config_path}"
            )
        if actual_config["provenance"].get("image_id") != config["provenance"].get(
            "image_id"
        ):
            raise ValueError(
                "Calibration image/source differs from the frozen cost snapshot."
            )
        seen_in_file = set()
        for entry in inventory:
            if (
                entry["raw_receipt"] != path
                or entry["classification"] != "selected_training"
            ):
                continue
            row = entry["raw_row"]
            if not successful_observation(row):
                raise ValueError(
                    f"Failed calibration attempt selected by the frozen cost snapshot: {path}{entry['pointer']}"
                )
            run_id = row.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise ValueError(
                    "Calibration receipt lacks an actual captured execution run_id."
                )
            if run_id in seen_in_file:
                raise ValueError(
                    f"Repeated execution ID within one raw receipt: {path}"
                )
            seen_in_file.add(run_id)
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
            row_provenance = row.get(
                "measurement_provenance", actual_config["provenance"]
            )
            if any(
                row_provenance.get(key) != actual_config["provenance"].get(key)
                for key in (
                    "image_id",
                    "cpu_model",
                    "platform",
                    "logical_cpus",
                    "host_memory_bytes",
                    "docker_server",
                )
            ):
                raise ValueError(
                    "Reused execution provenance contradicts its retained measurement context."
                )
            if row_provenance.get("source_sha256") != config["provenance"].get(
                "source_sha256"
            ) or actual_config["provenance"].get("source_sha256") != config[
                "provenance"
            ].get(
                "source_sha256"
            ):
                raise ValueError(
                    "Calibration source provenance contradicts the frozen cost snapshot."
                )
            candidates.setdefault(run_id, []).append(
                {
                    "raw_row": row,
                    "raw_receipt": path,
                    "pointer": entry["pointer"],
                    "config": actual_config,
                    "config_path": config_path,
                    "io_dir": io_dir,
                }
            )
    if set(candidates) != set(expected_rows) or not candidates:
        raise ValueError(
            "Raw calibration receipts do not completely match the frozen training observations."
        )
    found = []
    for run_id, copies in candidates.items():

        def execution_signature(entry):
            # Reuse adds bookkeeping, never a new execution. Paths may move when
            # evidence is copied; retained input/output bytes establish equality.
            value = {
                key: item
                for key, item in entry["raw_row"].items()
                if key not in {"reused_from", "measurement_provenance", "io_dir"}
            }
            value["retained_outputs_sha256"] = {
                str(path.relative_to(entry["io_dir"] / "out")): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted((entry["io_dir"] / "out").rglob("*"))
                if path.is_file()
            }
            if not value["retained_outputs_sha256"]:
                raise ValueError(
                    "Copied calibration execution has no retained outputs."
                )
            return digest(value)

        expected_signature = execution_signature(copies[0])
        if any(
            execution_signature(entry) != expected_signature for entry in copies[1:]
        ):
            raise ValueError(
                f"Conflicting copies of calibration execution ID: {run_id}"
            )
        original_paths = set()
        for entry in copies:
            reused = entry["raw_row"].get("reused_from")
            if reused is None:
                continue
            source = Path(reused)
            if not source.is_absolute():
                source = entry["raw_receipt"].parent / source
            source = source.resolve()
            originals = [
                candidate
                for candidate in copies
                if candidate["raw_receipt"].resolve() == source
            ]
            if len(originals) != 1:
                raise ValueError(
                    f"Reused execution source is missing or ambiguous: {source}"
                )
            original_paths.add(source)
        selected_entry = min(
            copies,
            key=lambda entry: (
                bool(original_paths)
                and entry["raw_receipt"].resolve() not in original_paths,
                "reused_from" in entry["raw_row"],
                "measurement_provenance" in entry["raw_row"],
                str(entry["raw_receipt"].resolve()),
            ),
        )
        selected = dict(selected_entry)
        selected["equivalent_raw_receipts"] = [
            {"raw_receipt": entry["raw_receipt"], "pointer": entry["pointer"]}
            for entry in copies
            if entry is not selected_entry
        ]
        found.append(selected)
    return found


def adapt_training_observations(
    entries: list[dict],
    output_dir: Path,
    profile: dict,
) -> list[dict]:
    """Write explicit evidence adapters, without fabricating any timing or run ID."""
    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = entries[0].get("review_path") if entries else None
    if entries and review_path is None:
        review_path = (
            output_dir / f"training-review-{digest(profile['profile_id'])[:16]}.json"
        )
        _write_calibration_review(
            review_path,
            profile,
            entries[0]["review_inventory"],
            verification_status="verified",
        )
    observations = []
    for entry in entries:
        row, io = entry["raw_row"], entry["io_dir"]
        original_config = {
            **entry["config"],
            "provenance": row.get(
                "measurement_provenance", entry["config"]["provenance"]
            ),
        }
        original_identity = identity_for_config(
            original_config,
            threads=row["threads"],
            ram_gb=row["ram_gb"],
            profile_id=profile["profile_id"],
        )
        identity = original_identity
        if (
            original_identity["source_sha256"]
            != profile["benchmark_config"]["provenance"]["source_sha256"]
        ):
            raise ValueError(
                "Calibration source identity differs from the requested profile."
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
            "equivalent_retained_copies": [
                artifact(copy["raw_receipt"], output_dir, pointer=copy["pointer"])
                for copy in entry.get("equivalent_raw_receipts", [])
            ],
            "calibration_review": artifact(review_path, output_dir),
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
