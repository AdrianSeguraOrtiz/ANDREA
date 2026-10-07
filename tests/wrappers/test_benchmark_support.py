import hashlib
import importlib.util
import json
import copy
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cost_evidence_support", ROOT / "wrappers/benchmark_support.py")
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


def test_atomic_save_does_not_damage_existing_measurements(tmp_path):
    path = tmp_path / "cost.json"
    path.write_text('{"old": true}\n')
    with pytest.raises(TypeError):
        support.atomic_json(path, {"unserializable": object()})
    assert path.read_text() == '{"old": true}\n'
    assert list(tmp_path.iterdir()) == [path]


def test_profile_merge_preserves_unmeasured_entries(tmp_path):
    path = tmp_path / "cost.json"
    old = {"schema_version": "1.0", "profiles": [
        {"profile_id": "a", "measured": 1}, {"profile_id": "b", "measured": 2},
    ]}
    path.write_text(json.dumps(old))
    merged = support.merge_profiles(path, {"schema_version": "1.0", "profiles": [
        {"profile_id": "b", "measured": 3}, {"profile_id": "c", "measured": 4},
    ]})
    assert merged["profiles"] == [old["profiles"][0], {"profile_id": "b", "measured": 3}, {"profile_id": "c", "measured": 4}]
    assert json.loads(path.read_text()) == old


def test_release_validation_rejects_legacy_and_stale_calibration(tmp_path):
    spec_path = tmp_path / "toolspec.json"
    spec_path.write_text('{"docker_image": "example/tool:1.1.0"}\n')
    payload = {"profiles": [{"profile_id": "global", "benchmark_config": {"repeats": 3}}]}
    assert support.provenance_errors(payload, spec_path) == []
    assert "missing benchmark provenance" in support.provenance_errors(payload, spec_path, required=True)[0]
    config = payload["profiles"][0]["benchmark_config"]
    config["provenance"] = {
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "image_reference": "example/tool:1.1.0",
    }
    assert support.provenance_errors(payload, spec_path, required=True) == []
    config["repeats"] = 1
    assert "three timing repetitions" in support.provenance_errors(payload, spec_path, required=True)[0]
    spec_path.write_text('{"docker_image": "example/tool:1.2.0"}\n')
    assert any("spec changed" in error for error in support.provenance_errors(payload, spec_path, required=True))


def test_fingerprint_tracks_shared_templates_and_wrappers_but_not_package_version(tmp_path):
    scripts = tmp_path / "wrappers/inference_tools/scripts"
    shutil.copytree(ROOT / "wrappers/inference_tools/scripts", scripts,
                    ignore=shutil.ignore_patterns("__pycache__"))
    sources = tmp_path / "wrappers/inference_tools/tools/genie3"
    sources.mkdir(parents=True)
    for name in ("Dockerfile", "run_tool.py"):
        shutil.copy2(ROOT / "wrappers/inference_tools/tools/genie3" / name, sources / name)
    def fingerprint():
        return support.build_context_fingerprint(root=tmp_path, kind="inference", identifier="genie3")
    initial = fingerprint()
    (tmp_path / "andrea").mkdir()
    (tmp_path / "andrea/config.py").write_text('__version__ = "0.2.0"\n')
    (sources / "integration_decisions.md").write_text("Updated documentation.\n")
    assert fingerprint() == initial
    template = scripts / "templates/python/_arboreto_common.py"
    template.write_text(template.read_text() + "\n# changed template\n")
    changed = fingerprint()
    assert changed != initial
    (sources / "run_tool.py").write_text("# replaced wrapper\n")
    assert fingerprint() != changed


def test_simulator_fingerprint_tracks_docker_copy_and_rejects_unknown_sources(tmp_path):
    directory = tmp_path / "wrappers/simulation_data_tools/simulators/sergio"
    directory.mkdir(parents=True)
    for name in ("Dockerfile", "run_simulator.py"):
        shutil.copy2(ROOT / "wrappers/simulation_data_tools/simulators/sergio" / name, directory / name)
    def fingerprint():
        return support.build_context_fingerprint(root=tmp_path, kind="simulation", identifier="sergio")
    initial = fingerprint()
    (directory / "run_simulator.py").write_text("# changed wrapper\n")
    assert fingerprint() != initial
    (directory / "Dockerfile").write_text("FROM scratch\nCOPY $UNKNOWN /app\n")
    with pytest.raises(RuntimeError, match="Cannot fingerprint"):
        fingerprint()


def test_all_catalog_build_contexts_can_be_fingerprinted_without_docker():
    for kind, catalog, filename in (
        ("inference", "catalog_inference_tools/tools", "toolspec.json"),
        ("simulation", "catalog_simulation_data_tools/simulators", "simulatorspec.json"),
    ):
        for spec_path in (ROOT / "andrea" / catalog).glob(f"*/{filename}"):
            fingerprint = support.build_context_fingerprint(root=ROOT, kind=kind, identifier=spec_path.parent.name)
            assert len(fingerprint) == 64


def test_profile_fingerprint_tracks_preset_and_fixture_content(tmp_path):
    first = tmp_path / "first.tsv"
    second = tmp_path / "second.tsv"
    first.write_text("a\tb\n")
    second.write_bytes(first.read_bytes())
    profile = {"id": "global", "params": {"epochs": 20}, "inputs": {"data": first}}
    initial = support.profile_fingerprint(profile, root=ROOT, kind="simulation")
    profile["inputs"]["data"] = second
    assert support.profile_fingerprint(profile, root=ROOT, kind="simulation") == initial
    second.write_text("changed fixture\n")
    assert support.profile_fingerprint(profile, root=ROOT, kind="simulation") != initial
    profile["inputs"]["data"] = first
    profile["params"]["epochs"] = 100
    assert support.profile_fingerprint(profile, root=ROOT, kind="simulation") != initial


def _release_profile():
    return {
        "profile_id": "global", "benchmark_config": {
            "profile_sha256": "a" * 64, "repeats": 3,
            "sizes": [{"genes": 10, "columns": 5}], "threads_tested": [1], "ram_gb_tested": [1],
        }, "runtime_points": [{
            "genes": 10, "columns": 5, "threads": 1, "ram_gb": 1, "status": "ok",
            "repeats_total": 3, "repeats_ok": 3, "repeats_failed": 0, "ok_rate": 1,
            "failure_breakdown": {"oom": 0, "timeout": 0, "error": 0},
        }],
    }


def test_strict_release_requires_current_profile_set_and_recorded_repetitions():
    payload = {"profiles": [_release_profile()]}
    expected = {"global": "a" * 64}
    assert support.release_profile_errors(payload, expected) == []
    assert any("Missing current" in e for e in support.release_profile_errors(payload, {**expected, "new": "b" * 64}))
    assert any("Obsolete" in e for e in support.release_profile_errors(payload, {}))
    assert any("recipe changed" in e for e in support.release_profile_errors(payload, {"global": "b" * 64}))
    payload["profiles"][0]["runtime_points"][0]["repeats_ok"] = 1
    assert any("minimum three" in e for e in support.release_profile_errors(payload, expected))


def test_strict_release_rejects_missing_or_duplicate_matrix_points():
    payload = {"profiles": [_release_profile()]}
    profile = payload["profiles"][0]
    profile["benchmark_config"]["threads_tested"].append(2)
    assert any("matrix exactly" in e for e in support.release_profile_errors(payload, {"global": "a" * 64}))
    profile["runtime_points"].append(copy.deepcopy(profile["runtime_points"][0]))
    assert any("matrix exactly" in e for e in support.release_profile_errors(payload, {"global": "a" * 64}))


def test_strict_release_rejects_duplicate_profile_ids():
    payload = {"profiles": [_release_profile(), _release_profile()]}
    assert any("Duplicate" in e for e in support.release_profile_errors(payload, {"global": "a" * 64}))


def test_strict_provenance_rejects_changed_build_inputs(tmp_path):
    spec_path = tmp_path / "toolspec.json"
    spec_path.write_text('{"docker_image":"image:1.1.0"}')
    config = _release_profile()["benchmark_config"]
    config["provenance"] = {
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "image_reference": "image:1.1.0", "build_context_sha256": "b" * 64,
    }
    payload = {"profiles": [{"profile_id": "global", "benchmark_config": config}]}
    assert support.provenance_errors(payload, spec_path, required=True, build_context_sha256="b" * 64) == []
    assert any("build inputs changed" in e for e in support.provenance_errors(payload, spec_path, required=True, build_context_sha256="c" * 64))
