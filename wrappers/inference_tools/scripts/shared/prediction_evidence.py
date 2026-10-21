"""Verified adaptation of retained benchmark executions for prediction checks."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

from .cost_resume import input_manifest, same_measurement_context

_compat_spec = importlib.util.spec_from_file_location(
    "_andrea_prediction_calibration_compatibility",
    Path(__file__).resolve().parents[3] / "calibration_compatibility.py",
)
_compatibility = importlib.util.module_from_spec(_compat_spec)
_compat_spec.loader.exec_module(_compatibility)
authorize_run = _compatibility.authorize_run
verify_compatibility = _compatibility.verify


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
    calibration_root: Path,
    cost_profile: dict,
    *,
    compatibility_receipt: Path | None = None,
) -> list[dict]:
    """Retain actual raw row references; reject missing IDs or changed inputs."""
    name = cost_profile["profile_id"]
    config = cost_profile["benchmark_config"]
    compatibility = (
        verify_compatibility(
            compatibility_receipt,
            image_id=config["provenance"]["image_id"],
            current_source_sha256=config["provenance"]["source_sha256"],
        )
        if compatibility_receipt is not None
        else None
    )
    expected_rows = {
        row["run_id"]: row
        for point in cost_profile["runtime_points"]
        for row in point["measurement_evidence"]["observations"]
        if row.get("status") == "ok"
    }
    candidates: dict[str, list[dict]] = {}
    paths = set(calibration_root.rglob("measurements.json"))
    if compatibility is not None:
        paths.update(
            (compatibility_receipt.parent / row["measurements"]["path"]).resolve()
            for row in compatibility["original_measurements"]
        )
    for path in sorted(paths):
        if path.parent.name != name:
            continue
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
        rows = json.loads(path.read_text())
        seen_in_file = set()
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
            if run_id in seen_in_file:
                raise ValueError(
                    f"Repeated execution ID within one raw receipt: {path}"
                )
            seen_in_file.add(run_id)
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
                if compatibility is None:
                    raise ValueError(
                        "Calibration source provenance contradicts the frozen cost snapshot without a compatibility receipt."
                    )
                authorize_run(compatibility, row, actual_config, config)
            candidates.setdefault(run_id, []).append(
                {
                    "raw_row": row,
                    "raw_receipt": path,
                    "pointer": f"/{index}",
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
    *,
    compatibility_receipt: Path | None = None,
) -> list[dict]:
    """Write explicit evidence adapters, without fabricating any timing or run ID."""
    output_dir.mkdir(parents=True, exist_ok=True)
    observations = []
    compatibility = (
        verify_compatibility(
            compatibility_receipt,
            image_id=profile["benchmark_config"]["provenance"]["image_id"],
            current_source_sha256=profile["benchmark_config"]["provenance"][
                "source_sha256"
            ],
        )
        if compatibility_receipt is not None
        else None
    )
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
            if compatibility is None:
                raise ValueError(
                    "Original source identity requires an explicit compatibility receipt."
                )
            authorize_run(
                compatibility, row, entry["config"], profile["benchmark_config"]
            )
            identity = identity_for_config(
                profile["benchmark_config"],
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
            "equivalent_retained_copies": [
                artifact(copy["raw_receipt"], output_dir, pointer=copy["pointer"])
                for copy in entry.get("equivalent_raw_receipts", [])
            ],
            "mapping": "Identity recipe/hardware hashes derive from retained configuration/provenance; seconds, execution ID, coordinates and inputs come from the original raw execution row.",
        }
        if identity != original_identity:
            adapter["original_identity"] = original_identity
            adapter["source_compatibility"] = artifact(
                compatibility_receipt, output_dir
            )
            adapter[
                "mapping"
            ] += " Source identity is an explicit verified compatibility alias; original identity and raw measurement provenance are retained unchanged."
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
