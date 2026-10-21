"""Scaffolds stage candidates safely and leave ordinary runtime-only wrappers."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def command(tmp_path, kind, identity="audit_fixture"):
    if kind == "inference":
        script = "wrappers/inference_tools/scripts/scaffold_tool.py"
        flags = ("--tool", "--tool-sources-root", "--catalog-tools-root")
    else:
        script = "wrappers/simulation_data_tools/scripts/scaffold_simulator.py"
        flags = ("--simulator", "--wrappers-root", "--catalog-simulators-root")
    return [
        sys.executable,
        str(REPO_ROOT / script),
        flags[0],
        identity,
        flags[1],
        str(tmp_path / "wrappers"),
        flags[2],
        str(tmp_path / "catalog"),
        "--smoketest-configs-root",
        str(tmp_path / "smoke"),
        "--results-dir",
        str(tmp_path / "results"),
    ]


def run(args, *, check=True):
    return subprocess.run(
        args, cwd=REPO_ROOT, capture_output=True, text=True, check=check
    )


def names(kind, identity="audit_fixture"):
    return (
        ("toolspec.draft.json", "toolspec.json", f"{identity}.json", "run_tool.py")
        if kind == "inference"
        else (
            "simulatorspec.draft.json",
            "simulatorspec.json",
            f"{identity}_basic.json",
            "run_simulator.py",
        )
    )


def reviewed_spec(kind, identity="audit_fixture"):
    path = (
        REPO_ROOT / "andrea/catalog_inference_tools/tools/genie3/toolspec.json"
        if kind == "inference"
        else REPO_ROOT
        / "andrea/catalog_simulation_data_tools/simulators/dyngen/simulatorspec.json"
    )
    spec = json.loads(path.read_text())
    spec["id"] = identity
    return spec


def prepare(tmp_path, kind, identity="audit_fixture"):
    args = command(tmp_path, kind, identity)
    run(args)
    draft, _, _, _ = names(kind, identity)
    spec = reviewed_spec(kind, identity)
    (tmp_path / "results" / draft).write_text(json.dumps(spec) + "\n")
    return args, spec


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_scaffold_has_same_essentials_before_and_after_integration(tmp_path, kind):
    args = command(tmp_path, kind)
    wrapper = tmp_path / "wrappers/audit_fixture"
    (wrapper / "papers").mkdir(parents=True)
    (wrapper / "papers/article.txt").write_text("User supplied article.\n")
    (wrapper / "repo/.git").mkdir(parents=True)
    (wrapper / "repo/.git/config").write_text("User supplied clone.\n")
    run(args)
    draft, _, _, script = names(kind)
    assert {p.name for p in wrapper.iterdir() if p.is_file()} == {
        "Dockerfile",
        script,
        "integration_decisions.md",
    }
    assert {p.name for p in (tmp_path / "results").iterdir()} == {
        draft,
        "smoketest.config.draft.json",
    }
    assert json.loads((tmp_path / "results" / draft).read_text())["year"] is None
    assert not (tmp_path / "catalog").exists() and not (tmp_path / "smoke").exists()
    decisions = (wrapper / "integration_decisions.md").read_text()
    assert "{{" not in decisions and "Complete parameter traceability" in decisions
    assert "integration.json" not in decisions
    (tmp_path / "results" / draft).write_text('{"reviewed":true}\n')
    (wrapper / "integration_decisions.md").write_text("Reviewed scientific notes.\n")
    run(args)
    assert (tmp_path / "results" / draft).read_text() == '{"reviewed":true}\n'
    assert (
        wrapper / "integration_decisions.md"
    ).read_text() == "Reviewed scientific notes.\n"
    assert (wrapper / "papers/article.txt").read_text() == "User supplied article.\n"
    assert (wrapper / "repo/.git/config").read_text() == "User supplied clone.\n"


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_promotion_rejects_missing_invalid_placeholder_or_wrong_identity(
    tmp_path, kind
):
    args = command(tmp_path, kind)
    activation = [*args, "--activate-catalog", "--activate-smoketest", "--force"]
    assert "draft missing" in run(activation, check=False).stderr
    run(args)
    assert "invalid draft" in run(activation, check=False).stderr
    draft = tmp_path / "results" / names(kind)[0]
    spec = reviewed_spec(kind)
    for key, value, message in (
        ("first_author", "TODO: citation", "unresolved placeholders"),
        ("id", "different_identity", "must equal"),
    ):
        invalid = {**spec, key: value}
        draft.write_text(json.dumps(invalid))
        result = run(activation, check=False)
        assert result.returncode != 0 and message in result.stderr
        assert not (tmp_path / "catalog").exists()
        assert not (tmp_path / "smoke").exists()


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_promotion_validates_all_selected_drafts_before_writing(tmp_path, kind):
    args, _ = prepare(tmp_path, kind)
    (tmp_path / "results/smoketest.config.draft.json").write_text("invalid JSON")
    result = run(
        [*args, "--activate-catalog", "--activate-smoketest", "--force"], check=False
    )
    assert result.returncode != 0 and "Cannot read draft/schema" in result.stderr
    assert not (tmp_path / "catalog").exists() and not (tmp_path / "smoke").exists()


@pytest.mark.parametrize("kind", ["inference", "simulation"])
@pytest.mark.parametrize("legacy", [False, True])
def test_promotion_cleans_only_identical_copies_and_preserves_wrapper(
    tmp_path, kind, legacy
):
    args, spec = prepare(tmp_path, kind)
    draft, active_name, smoke_name, script = names(kind)
    wrapper = tmp_path / "wrappers/audit_fixture"
    (wrapper / script).write_text("# reviewed implementation\n")
    smoke = json.loads((tmp_path / "results/smoketest.config.draft.json").read_text())
    if legacy:
        for name in (draft, "smoketest.config.draft.json"):
            (tmp_path / "results" / name).rename(wrapper / name)
    else:
        # Equal legacy duplicates are also safe to retire after the exact copy.
        for name in (draft, "smoketest.config.draft.json"):
            (wrapper / name).write_bytes((tmp_path / "results" / name).read_bytes())
    run([*args, "--activate-catalog", "--activate-smoketest", "--force"])
    assert (
        json.loads((tmp_path / "catalog/audit_fixture" / active_name).read_text())
        == spec
    )
    assert json.loads((tmp_path / "smoke" / smoke_name).read_text()) == smoke
    assert {p.name for p in wrapper.iterdir() if p.is_file()} == {
        "Dockerfile",
        script,
        "integration_decisions.md",
    }
    assert not (tmp_path / "results" / draft).exists()
    assert not (tmp_path / "results/smoketest.config.draft.json").exists()
    assert (wrapper / script).read_text() == "# reviewed implementation\n"
    run(args)
    assert not (tmp_path / "results" / draft).exists()
    assert not (tmp_path / "results/smoketest.config.draft.json").exists()


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_uncopied_or_conflicting_draft_is_retained(tmp_path, kind):
    args, _ = prepare(tmp_path, kind)
    draft, active_name, _, _ = names(kind)
    staged = tmp_path / "results" / draft
    before = staged.read_bytes()
    active = tmp_path / "catalog/audit_fixture" / active_name
    active.parent.mkdir(parents=True)
    active.write_text('{"different":"preserve"}\n')
    run([*args, "--activate-catalog"])
    assert active.read_text() == '{"different":"preserve"}\n'
    assert staged.read_bytes() == before
    legacy = tmp_path / "wrappers/audit_fixture" / draft
    legacy.write_text('{"conflicting":"preserve"}\n')
    result = run([*args, "--activate-catalog", "--force"], check=False)
    assert result.returncode != 0 and "Conflicting candidate copies" in result.stderr
    assert staged.read_bytes() == before
    assert legacy.read_text() == '{"conflicting":"preserve"}\n'


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_nonidentical_newline_copy_is_not_removed(tmp_path, kind):
    args, _ = prepare(tmp_path, kind)
    draft = tmp_path / "results" / names(kind)[0]
    before = draft.read_bytes().replace(b"\n", b"\r\n")
    draft.write_bytes(before)
    run([*args, "--activate-catalog", "--force"])
    assert draft.read_bytes() == before


@pytest.mark.parametrize("kind", ["inference", "simulation"])
@pytest.mark.parametrize(
    "inside", ["wrappers/audit_fixture/results", "catalog/candidate", "smoke/candidate"]
)
def test_candidate_staging_cannot_be_inside_runtime_or_active_catalog(
    tmp_path, kind, inside
):
    result = run(
        [*command(tmp_path, kind), "--results-dir", str(tmp_path / inside)], check=False
    )
    assert result.returncode != 0 and "outside wrapper and active" in result.stderr
    assert not (tmp_path / "wrappers").exists()
    assert not (tmp_path / "catalog").exists()


@pytest.mark.parametrize("matches", [False, True])
def test_inference_local_map_is_removed_only_when_central_configuration_matches(
    tmp_path, matches
):
    args, _ = prepare(tmp_path, "inference", "genie3")
    central = json.loads(
        (REPO_ROOT / "wrappers/inference_tools/scripts/template_map.json").read_text()
    )
    candidate = {
        "tools": {"genie3": central["tools"]["genie3"]},
        "bundle_templates": central["bundle_templates"],
    }
    if not matches:
        candidate["tools"]["genie3"] = {
            **candidate["tools"]["genie3"],
            "native_threads": "assigned",
        }
    staged = tmp_path / "results/template_map.json"
    staged.write_text(json.dumps(candidate))
    run([*args, "--activate-catalog", "--activate-smoketest", "--force"])
    assert staged.exists() is not matches


def test_simulator_docker_entrypoint_matches_overridden_extension(tmp_path):
    run([*command(tmp_path, "simulation"), "--wrapper-ext", "custom"])
    wrapper = tmp_path / "wrappers/audit_fixture"
    assert (wrapper / "run_simulator.custom").is_file()
    assert (
        'ENTRYPOINT ["python3", "/app/run_simulator.custom"'
        in (wrapper / "Dockerfile").read_text()
    )
