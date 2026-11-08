"""Executor reviews must be explicit and cannot bypass scientific identity."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "resource_provenance_resume",
    ROOT / "wrappers/inference_tools/scripts/shared/cost_resume.py",
)
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)


def test_review_preserves_original_identity_and_checks_other_context(tmp_path):
    old_kernel, new_kernel = "a" * 64, "b" * 64
    proof = resume.VerifiedSourceKernels()
    proof["source-digest"] = old_kernel
    evidence = tmp_path / "review.json"
    evidence.write_text(
        json.dumps(
            {
                "from_kernel_sha256": old_kernel,
                "to_kernel_sha256": new_kernel,
                "conclusion": "timing_protocol_preserved",
            }
        )
    )
    manifest = {
        "measurement_kernel_reviews": [
            {
                "from_kernel_sha256": old_kernel,
                "to_kernel_sha256": new_kernel,
                "scope": "cost_executor_resource_orchestration",
                "evidence_path": str(evidence),
                "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            }
        ]
    }
    proof.load_reviews(manifest, new_kernel)
    old = {
        "provenance": {
            "measurement_kernel_sha256": old_kernel,
            "cpu_model": "same-cpu",
            "image_id": "sha256:same-image",
            "timing_scope": "docker-wallclock",
            "seed": 12345,
        }
    }
    new = copy.deepcopy(old)
    new["provenance"]["measurement_kernel_sha256"] = new_kernel
    assert not resume.same_measurement_context(old, new)
    review = proof.reviews[(old_kernel, new_kernel)]
    assert resume.same_measurement_context(old, new, kernel_review=review)
    assert old["provenance"]["measurement_kernel_sha256"] == old_kernel
    for field in ("cpu_model", "image_id", "timing_scope", "seed"):
        changed = copy.deepcopy(new)
        changed["provenance"][field] = "different"
        assert not resume.same_measurement_context(old, changed, kernel_review=review)
    evidence.write_text("{}")
    with pytest.raises(ValueError, match="missing or changed"):
        proof.load_reviews(manifest, new_kernel)


def test_review_requires_verified_original_source_and_exact_current_kernel(tmp_path):
    proof = resume.VerifiedSourceKernels()
    manifest = {
        "measurement_kernel_reviews": [
            {
                "from_kernel_sha256": "a" * 64,
                "to_kernel_sha256": "b" * 64,
                "scope": "cost_executor_resource_orchestration",
            }
        ]
    }
    with pytest.raises(ValueError, match="verified source"):
        proof.load_reviews(manifest, "b" * 64)
    proof["source-digest"] = "a" * 64
    with pytest.raises(ValueError, match="current kernel"):
        proof.load_reviews(manifest, "c" * 64)


def test_reviewed_import_keeps_native_kernel_provenance_and_receipt_bytes(tmp_path):
    old_kernel, new_kernel = "a" * 64, "b" * 64
    config = {
        "timeout_seconds": 1800,
        "provenance": {
            "measurement_kernel_sha256": old_kernel,
            "cpu_model": "same-cpu",
            "image_id": "same-image",
            "timing_scope": "docker-wallclock",
        },
    }
    (tmp_path / "benchmark_config.json").write_text(json.dumps(config))
    io = tmp_path / "native" / "io"
    io.mkdir(parents=True)
    (io / "expression.tsv").write_text("gene\tsample\nG1\t1\n")
    hashes = resume.input_manifest(io)
    row = {
        "run_id": "original-id",
        "genes": 1,
        "columns": 1,
        "threads": 1,
        "ram_gb": 8,
        "repeat": 1,
        "status": "ok",
        "seconds": 3.125,
        "io_dir": str(io),
        "input_sha256": hashes,
    }
    raw = tmp_path / "measurements.json"
    raw.write_text(json.dumps([row]))
    original = raw.read_bytes()
    requested = copy.deepcopy(config)
    requested["provenance"]["measurement_kernel_sha256"] = new_kernel
    kwargs = dict(
        directories=[tmp_path],
        config=requested,
        key=(1, 1, 1, 8.0),
        hashes=hashes,
        repeats=1,
    )
    assert resume.retained_observations(**kwargs) == []
    proof = resume.VerifiedSourceKernels()
    proof["verified-source"] = old_kernel
    proof.reviews[(old_kernel, new_kernel)] = {
        "from_kernel_sha256": old_kernel,
        "to_kernel_sha256": new_kernel,
        "path": "/verified/review.json",
        "sha256": "c" * 64,
    }
    result = resume.retained_observations(**kwargs, source_kernels=proof)
    assert len(result) == 1
    assert result[0]["run_id"] == "original-id"
    assert result[0]["seconds"] == 3.125
    assert result[0]["measurement_kernel_sha256"] == old_kernel
    assert result[0]["measurement_provenance"] == config["provenance"]
    assert (
        result[0]["measurement_kernel_review"]
        == proof.reviews[(old_kernel, new_kernel)]
    )
    assert raw.read_bytes() == original
