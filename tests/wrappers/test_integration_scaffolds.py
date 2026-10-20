from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_scaffold_keeps_unverified_contracts_out_of_active_catalog(
    tmp_path: Path, kind: str
) -> None:
    if kind == "inference":
        script = REPO_ROOT / "wrappers/inference_tools/scripts/scaffold_tool.py"
        flags = [
            "--tool",
            "audit_fixture",
            "--tool-sources-root",
            str(tmp_path / "wrappers"),
            "--catalog-tools-root",
            str(tmp_path / "catalog"),
        ]
        draft_name = "toolspec.draft.json"
        ignore_name = ".dockerignore"
    else:
        script = (
            REPO_ROOT / "wrappers/simulation_data_tools/scripts/scaffold_simulator.py"
        )
        flags = [
            "--simulator",
            "audit_fixture",
            "--wrappers-root",
            str(tmp_path / "wrappers"),
            "--catalog-simulators-root",
            str(tmp_path / "catalog"),
        ]
        draft_name = "simulatorspec.draft.json"
        ignore_name = "Dockerfile.dockerignore"
    command = [
        sys.executable,
        str(script),
        *flags,
        "--smoketest-configs-root",
        str(tmp_path / "smoke"),
    ]
    subprocess.run(command, cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    wrapper = tmp_path / "wrappers/audit_fixture"
    assert not (tmp_path / "catalog").exists()
    assert not (tmp_path / "smoke").exists()
    assert (wrapper / "repo").is_dir()
    assert (wrapper / "papers").is_dir()
    assert json.loads((wrapper / draft_name).read_text())["year"] is None
    record = json.loads((wrapper / "integration.json").read_text())
    assert record["kind"] == kind
    assert record["id"] == "audit_fixture"
    assert record["checks"] == []
    decisions = (wrapper / "integration_decisions.md").read_text()
    assert "{{" not in decisions
    assert "Complete parameter traceability" in decisions
    assert "User installation preference" in decisions
    assert "Image publication: pending" in decisions
    ignores = (wrapper / ignore_name).read_text().splitlines()
    assert "**/repo" in ignores
    assert "**/papers" in ignores

    # Scaffolding again preserves reviewed evidence and work by default.
    (wrapper / "integration.json").write_text('{"reviewed": true}\n')
    (wrapper / "papers/article.txt").write_text("User supplied article.\n")
    subprocess.run(command, cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    assert json.loads((wrapper / "integration.json").read_text()) == {"reviewed": True}
    assert (wrapper / "papers/article.txt").read_text() == "User supplied article.\n"


def test_simulator_docker_entrypoint_matches_interpreter_and_overridden_script(
    tmp_path: Path,
) -> None:
    script = REPO_ROOT / "wrappers/simulation_data_tools/scripts/scaffold_simulator.py"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--simulator",
            "audit_fixture",
            "--wrappers-root",
            str(tmp_path),
            "--wrapper-ext",
            "custom",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    dockerfile = (tmp_path / "audit_fixture/Dockerfile").read_text()
    assert 'ENTRYPOINT ["python3", "/app/run_simulator.custom"' in dockerfile
    assert (tmp_path / "audit_fixture/run_simulator.custom").is_file()


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_activation_promotes_reviewed_drafts_without_regenerating_wrapper(
    tmp_path: Path, kind: str
) -> None:
    if kind == "inference":
        script = REPO_ROOT / "wrappers/inference_tools/scripts/scaffold_tool.py"
        flags = [
            "--tool",
            "audit_fixture",
            "--tool-sources-root",
            str(tmp_path / "wrappers"),
            "--catalog-tools-root",
            str(tmp_path / "catalog"),
        ]
        draft_name, active_name = "toolspec.draft.json", "toolspec.json"
        smoke_name = "audit_fixture.json"
        spec_source = (
            REPO_ROOT / "andrea/catalog_inference_tools/tools/genie3/toolspec.json"
        )
    else:
        script = (
            REPO_ROOT / "wrappers/simulation_data_tools/scripts/scaffold_simulator.py"
        )
        flags = [
            "--simulator",
            "audit_fixture",
            "--wrappers-root",
            str(tmp_path / "wrappers"),
            "--catalog-simulators-root",
            str(tmp_path / "catalog"),
        ]
        draft_name, active_name = "simulatorspec.draft.json", "simulatorspec.json"
        smoke_name = "audit_fixture_basic.json"
        spec_source = (
            REPO_ROOT
            / "andrea/catalog_simulation_data_tools/simulators/dyngen/simulatorspec.json"
        )
    command = [
        sys.executable,
        str(script),
        *flags,
        "--smoketest-configs-root",
        str(tmp_path / "smoke"),
    ]
    activation = [*command, "--activate-catalog", "--activate-smoketest", "--force"]
    # Fresh activation must not create a plausible active placeholder.
    missing = subprocess.run(activation, cwd=REPO_ROOT, capture_output=True, text=True)
    assert missing.returncode != 0 and "draft missing" in missing.stderr
    assert not (tmp_path / "catalog").exists()
    subprocess.run(command, cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    wrapper = tmp_path / "wrappers/audit_fixture"
    # The unreviewed schema-invalid initial draft is still blocked.
    invalid = subprocess.run(activation, cwd=REPO_ROOT, capture_output=True, text=True)
    assert invalid.returncode != 0 and "invalid draft" in invalid.stderr
    assert not (tmp_path / "catalog").exists()
    reviewed = json.loads(spec_source.read_text())
    reviewed["id"] = "audit_fixture"
    (wrapper / draft_name).write_text(json.dumps(reviewed))
    wrapper_script = wrapper / (
        "run_tool.py" if kind == "inference" else "run_simulator.py"
    )
    wrapper_script.write_text("# reviewed wrapper, preserve this implementation\n")
    (wrapper / "integration.json").write_text('{"reviewed":true}\n')
    smoke = json.loads((wrapper / "smoketest.config.draft.json").read_text())
    subprocess.run(
        activation, cwd=REPO_ROOT, check=True, capture_output=True, text=True
    )
    active = tmp_path / "catalog/audit_fixture" / active_name
    assert json.loads(active.read_text()) == reviewed
    assert json.loads((tmp_path / "smoke" / smoke_name).read_text()) == smoke
    assert (
        wrapper_script.read_text()
        == "# reviewed wrapper, preserve this implementation\n"
    )
    assert (wrapper / "integration.json").read_text() == '{"reviewed":true}\n'
    # A valid schema does not excuse an unresolved review placeholder.
    reviewed["first_author"] = "TODO: verify citation"
    (wrapper / draft_name).write_text(json.dumps(reviewed))
    pending = subprocess.run(activation, cwd=REPO_ROOT, capture_output=True, text=True)
    assert pending.returncode != 0 and "unresolved placeholders" in pending.stderr
    assert json.loads(active.read_text())["first_author"] != reviewed["first_author"]
    reviewed["first_author"] = json.loads(spec_source.read_text())["first_author"]
    reviewed["id"] = "different_identity"
    (wrapper / draft_name).write_text(json.dumps(reviewed))
    mismatch = subprocess.run(activation, cwd=REPO_ROOT, capture_output=True, text=True)
    assert mismatch.returncode != 0 and "must equal" in mismatch.stderr
    assert json.loads(active.read_text())["id"] == "audit_fixture"


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_scaffold_record_cannot_pass_functional_gate(tmp_path: Path, kind: str) -> None:
    # The template itself must never be mistaken for integration evidence.
    payload = json.loads(
        (REPO_ROOT / "wrappers/integration/integration.template.json").read_text()
    )
    payload.update(kind=kind, id="audit_fixture")
    manifest = tmp_path / "integration.json"
    manifest.write_text(json.dumps(payload))
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "wrappers/integration/validate_integration.py"),
            str(manifest),
            "--stage",
            "functional",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
