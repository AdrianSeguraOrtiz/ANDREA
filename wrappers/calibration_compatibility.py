"""Verify explicit reuse of unchanged measured tasks across source reviews.

Original observations and their provenance are never rewritten. This proof
allows only a source-inventory alias; image, hardware, timing, parameters and
retained/generated input bytes must still agree exactly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


MEASUREMENT_SUBJECTS = (
    "wrappers/benchmark_support.py",
    "wrappers/inference_tools/scripts/benchmark_costs.py",
    "wrappers/inference_tools/scripts/shared/benchmark_inputs.py",
    "wrappers/inference_tools/scripts/shared/benchmark_profiles.py",
    "wrappers/inference_tools/scripts/shared/cost_resume.py",
    "wrappers/inference_tools/scripts/shared/param_profiles.py",
    "wrappers/inference_tools/scripts/shared/catalog_tools.py",
    "wrappers/inference_tools/scripts/build_tool_images.py",
    "wrappers/inference_tools/scripts/templates/python/_run_tool_common.py",
    "wrappers/inference_tools/tools/dyngenie3/Dockerfile",
    "wrappers/inference_tools/tools/dyngenie3/.dockerignore",
    "wrappers/inference_tools/tools/dyngenie3/requirements.lock",
    "wrappers/inference_tools/tools/dyngenie3/patch_upstream.py",
    "wrappers/inference_tools/tools/dyngenie3/run_tool.py",
    "wrappers/inference_tools/tools/dyngenie3/template_map.json",
    "wrappers/inference_tools/tools/dyngenie3/toolspec.draft.json",
)
HARDWARE_FIELDS = (
    "cpu_model",
    "platform",
    "logical_cpus",
    "host_memory_bytes",
    "docker_server",
    "timing_scope",
    "seed",
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execution_digest(row: dict) -> str:
    return digest(
        {
            key: value
            for key, value in row.items()
            if key not in {"reused_from", "measurement_provenance", "io_dir"}
        }
    )


def artifact(ref: dict, base: Path) -> Path:
    path = (base / ref["path"]).resolve()
    if not path.is_file() or sha(path) != ref.get("sha256"):
        raise ValueError(f"Calibration compatibility artifact changed: {path}")
    return path


def inputs(io: Path) -> dict[str, str]:
    paths = [io / name for name in ("expression.tsv", "params.json", "execution.json")]
    paths.extend(sorted((io / "extra").rglob("*")))
    return {p.relative_to(io).as_posix(): sha(p) for p in paths if p.is_file()}


def context(config: dict) -> dict:
    provenance = config.get("provenance", {})
    return {
        "image_id": provenance.get("image_id"),
        **{key: provenance.get(key) for key in HARDWARE_FIELDS},
        "params": config.get("params_profile", {}).get("resolved_params"),
        "execution": config.get("execution_profile"),
        "input": {
            key: value
            for key, value in config.get("input_profile", {}).items()
            if key != "notes"
        },
    }


def verify(path: Path, *, image_id: str, current_source_sha256: str) -> dict:
    path = path.resolve()
    proof = json.loads(path.read_text())
    if (
        proof.get("schema_version"),
        proof.get("kind"),
        proof.get("status"),
        proof.get("image_id"),
        proof.get("current_source_sha256"),
    ) != (
        1,
        "dyngenie3_calibration_compatibility",
        "passed",
        image_id,
        current_source_sha256,
    ):
        raise ValueError(
            "Calibration compatibility receipt does not authorize this source/image."
        )
    base = path.parent
    original_freeze = json.loads(artifact(proof["original_freeze"], base).read_text())
    original_state = json.loads(artifact(proof["original_state"], base).read_text())
    artifact(proof["original_cost"], base)
    if original_state.get("image_id") != image_id or original_state.get(
        "source_sha256"
    ) != original_freeze.get("source_sha256"):
        raise ValueError(
            "Calibration compatibility original campaign identity differs."
        )
    if original_state.get("status") in {"running", "waiting_external", "planned"}:
        raise ValueError(
            "Calibration compatibility requires a stopped original campaign."
        )
    old_subjects = original_freeze["identity"]["sources"]
    if digest(old_subjects) != original_freeze.get("source_sha256") or proof[
        "original_cost"
    ]["sha256"] != original_state.get("calibration", {}).get("cost_sha256"):
        raise ValueError(
            "Calibration compatibility original source/checkpoint seal differs."
        )
    subjects = proof.get("measurement_subjects", {})
    if set(subjects) != set(MEASUREMENT_SUBJECTS):
        raise ValueError("Calibration compatibility measurement closure is incomplete.")
    for name, ref in subjects.items():
        if ref.get("sha256") != old_subjects.get(name):
            raise ValueError(
                f"Measured implementation changed across source reviews: {name}"
            )
        artifact(ref, base)
    observed = json.loads(artifact(proof["observed_provenance"], base).read_text())
    if (
        observed.get("image_id") != image_id
        or observed.get("source_sha256") != current_source_sha256
    ):
        raise ValueError(
            "Calibration compatibility current observed provenance differs."
        )
    originals = proof.get("original_measurements", [])
    if not originals:
        raise ValueError(
            "Calibration compatibility has no original measurement captures."
        )
    original_pairs = set()
    original_root = (Path(proof["original_campaign"]) / "calibration").resolve()
    for original in originals:
        raw = artifact(original["measurements"], base)
        config_path = artifact(original["config"], base)
        if not raw.is_relative_to(original_root) or config_path != raw.with_name(
            "benchmark_config.json"
        ):
            raise ValueError(
                "Calibration compatibility original capture escapes its campaign."
            )
        original_pairs.add((raw, config_path))
    captures = proof.get("executions", {})
    if not captures:
        raise ValueError("Calibration compatibility has no captured execution IDs.")
    for run_id, capture in captures.items():
        raw_path = artifact(capture["raw_receipt"], base)
        rows = json.loads(raw_path.read_text())
        index = capture["raw_receipt"].get("pointer", "")
        if (
            not isinstance(index, str)
            or not index.startswith("/")
            or not index[1:].isdigit()
        ):
            raise ValueError(
                "Calibration compatibility requires an explicit raw-row pointer."
            )
        row = rows[int(index[1:])]
        config_path = artifact(capture["config"], base)
        if (raw_path, config_path) not in original_pairs:
            raise ValueError(
                "Calibration compatibility execution is outside its original captures."
            )
        config = json.loads(config_path.read_text())
        provenance = row.get("measurement_provenance", config["provenance"])
        if (
            row.get("run_id") != run_id
            or row.get("status") != "ok"
            or digest(row) != capture.get("raw_row_sha256")
            or execution_digest(row) != capture.get("execution_sha256")
        ):
            raise ValueError("Original captured calibration execution changed.")
        if (
            provenance.get("source_sha256") != capture.get("original_source_sha256")
            or provenance.get("source_sha256")
            != config["provenance"].get("source_sha256")
            or provenance.get("image_id") != image_id
        ):
            raise ValueError("Original captured calibration provenance differs.")
        if any(provenance.get(key) != observed.get(key) for key in HARDWARE_FIELDS):
            raise ValueError(
                "Calibration compatibility hardware/timing context differs."
            )
        hashes = row.get("input_sha256")
        if (
            inputs(Path(row["io_dir"])) != hashes
            or inputs(Path(capture["generated_io"])) != hashes
        ):
            raise ValueError(
                "Calibration compatibility retained/generated inputs differ."
            )
        if capture.get("context_sha256") != digest(context(config)):
            raise ValueError("Calibration compatibility task context differs.")
    return proof


def authorize_run(proof: dict, row: dict, config: dict, current_config: dict) -> None:
    capture = proof["executions"].get(row.get("run_id"))
    provenance = row.get("measurement_provenance", config["provenance"])
    if (
        capture is None
        or capture["original_source_sha256"] != provenance.get("source_sha256")
        or capture.get("execution_sha256") != execution_digest(row)
        or capture["context_sha256"] != digest(context(config))
        or context(config) != context(current_config)
    ):
        raise ValueError(
            "Calibration compatibility does not authorize this original execution/context."
        )
