"""Evidence gates must reject placeholders, stale receipts and missing coverage."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "wrappers/integration/validate_integration.py"
spec = importlib.util.spec_from_file_location("integration_acceptance", MODULE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def write(path, data):
    path.write_text(json.dumps(data))
    return {"path": path.name, "sha256": gate.sha256(path)}


def dossier(tmp_path):
    artifacts = []
    for role in ("spec", "wrapper", "dockerfile", *sorted(gate.CALIBRATION_ROLES)):
        value = (
            {"id": "demo", "params": {"seed": {"default": 0}}}
            if role == "spec"
            else {"fixture": role}
        )
        artifacts.append({"role": role, **write(tmp_path / f"{role}.json", value)})
    evidence = []
    for role in ("paper", "upstream"):
        evidence.append(
            {
                "id": role,
                "role": role,
                "citation": "Synthetic test evidence; no scientific measurement",
                "limitations": "Test fixture only",
                **write(tmp_path / f"{role}.json", {"fixture": role}),
            }
        )
    image = "example.invalid/tool@sha256:" + "a" * 64
    output = write(
        tmp_path / "check-output.json", {"scope": "synthetic gate unit test"}
    )
    data = {
        "schema_version": "1.0",
        "kind": "inference",
        "id": "demo",
        "artifacts": artifacts,
        "evidence": evidence,
        "installation": {
            "preference": "official package",
            "public_source": "https://example.invalid/tool",
            "pin": "fixture version",
            "image": image,
            "registry_digest": image,
            "evidence": ["upstream"],
        },
        "entrypoints": [
            {
                "name": "fit",
                "disposition": "included",
                "mapping": "global",
                "reason": "test fixture",
                "evidence": ["upstream"],
            }
        ],
        "parameters": [
            {
                "upstream": "seed",
                "andrea": "seed",
                "type": "integer",
                "domain": "nonnegative",
                "default": 0,
                "upstream_default": 0,
                "default_rule": "Literal default; forwarded unchanged",
                "dependencies": "none",
                "argument": "fit(seed=seed)",
                "disposition": "exposed",
                "reason": "public argument",
                "test": "default fixture",
                "cost": "none identified",
                "evidence": ["upstream"],
            }
        ],
        "limitations": ["Synthetic evidence only"],
        "checks": [],
    }
    for name in sorted(gate.STAGES["published"] | gate.CALIBRATED):
        receipt = write(
            tmp_path / f"receipt-{name}.json",
            {
                "kind": "inference",
                "id": "demo",
                "status": "passed",
                "check": name,
                "exit_code": 0,
                "command": "synthetic test",
                "image": image,
                "registry_digest": image,
                "covers": ["fit", "seed"],
                "context_sha256": gate.context_sha256(data),
                "subjects": gate.subject_sets(data)[
                    (
                        "calibration_subjects"
                        if name.startswith("cost_")
                        else "functional_subjects"
                    )
                ],
                "outputs": [output],
            },
        )
        data["checks"].append(
            {
                "name": name,
                "status": "passed",
                "receipt": receipt,
                "covers": ["fit", "seed"],
            }
        )
    path = tmp_path / "integration.json"
    write(path, data)
    return path, data


def test_scaffold_cannot_be_mistaken_for_functional_integration():
    result = gate.validate(ROOT / "wrappers/integration/integration.template.json")
    assert result["status"] == "failed"
    assert any("immutable image" in error for error in result["errors"])


def test_dossier_gate_distinguishes_all_three_stages(tmp_path):
    path, data = dossier(tmp_path)
    assert gate.validate(path, "published")["status"] == "passed"
    assert gate.validate(path, "calibrated")["status"] == "passed"
    data["checks"] = [
        row
        for row in data["checks"]
        if row["name"] not in {"cost_structure", "cost_domain", "cost_predictions"}
    ]
    write(path, data)
    assert gate.validate(path, "published")["status"] == "passed"
    assert gate.validate(path, "calibrated")["status"] == "failed"
    data["checks"] = [row for row in data["checks"] if row["name"] in gate.FUNCTIONAL]
    write(path, data)
    assert gate.validate(path)["status"] == "passed"
    assert gate.validate(path, "calibrated")["status"] == "failed"


@pytest.mark.parametrize(
    "change,expected",
    [
        ("default", "default mismatch"),
        ("default_type", "default mismatch"),
        ("coverage", "entrypoint coverage"),
        ("stale", "stale artifact subjects"),
        ("output", "SHA-256 mismatch"),
        ("parameter", "public params missing"),
        ("local_image", "registry digest"),
        ("evidence", "stale scientific context"),
        ("entrypoint", "stale scientific context"),
        ("invented_coverage", "receipt check/coverage differs"),
    ],
)
def test_rejects_incomplete_or_stale_evidence(tmp_path, change, expected):
    path, data = dossier(tmp_path)
    if change == "default":
        data["parameters"][0]["default"] = 3
    elif change == "default_type":
        data["parameters"][0]["default"] = False
    elif change == "coverage":
        next(row for row in data["checks"] if row["name"] == "functional_modes")[
            "covers"
        ] = []
    elif change == "stale":
        ref = data["artifacts"][1]
        ref.update(write(tmp_path / ref["path"], {"updated": True}))
    elif change == "output":
        write(tmp_path / "check-output.json", {"changed": True})
    elif change == "parameter":
        data["parameters"] = []
    elif change == "local_image":
        data["installation"]["registry_digest"] = "pending"
    elif change == "evidence":
        data["evidence"][0].update(write(tmp_path / "paper.json", {"changed": True}))
    elif change == "entrypoint":
        data["entrypoints"][0]["mapping"] = "changed scientific mode"
    elif change == "invented_coverage":
        next(row for row in data["checks"] if row["name"] == "functional_modes")[
            "covers"
        ].append("invented")
    write(path, data)
    result = gate.validate(path, "published")
    assert result["status"] == "failed"
    assert any(expected in error for error in result["errors"])


def test_cost_changes_do_not_invalidate_functional_evidence_but_block_calibration(
    tmp_path,
):
    path, data = dossier(tmp_path)
    ref = next(row for row in data["artifacts"] if row["role"] == "cost")
    ref.update(write(tmp_path / "cost.json", {"changed": True}))
    write(path, data)
    assert gate.validate(path)["status"] == "passed"
    result = gate.validate(path, "calibrated")
    assert result["status"] == "failed"
    assert any("stale artifact subjects" in error for error in result["errors"])


def test_calibrated_requires_actual_calibration_artifacts(tmp_path):
    path, data = dossier(tmp_path)
    data["artifacts"] = [
        ref for ref in data["artifacts"] if ref["role"] not in gate.CALIBRATION_ROLES
    ]
    write(path, data)
    result = gate.validate(path, "calibrated")
    assert any("requires cost/" in error for error in result["errors"])


@pytest.mark.parametrize(
    "target", ["integration.json", "cost.json", "check-output.json"]
)
def test_report_never_overwrites_evidence(tmp_path, target):
    path, _ = dossier(tmp_path)
    protected = tmp_path / target
    before = protected.read_bytes()
    result = subprocess.run(
        [sys.executable, str(MODULE), str(path), "--report", str(protected)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "must not overwrite" in result.stderr
    assert protected.read_bytes() == before
