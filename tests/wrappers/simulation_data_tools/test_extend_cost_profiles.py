"""Resumption must not hide changed, failed, or dimensionally false evidence."""
import importlib.util
import importlib.machinery
import copy
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _extension_namespace(name):
    return (name == "shared" or name.startswith("shared.")
            or name in {"benchmark_support", "simulator_extension_benchmark", "simcost_extension_tests"})


@contextmanager
def _isolated_extension():
    """Standalone maintenance scripts must not share imports with other tests."""
    path = Path(__file__).resolve().parents[3] / "wrappers/simulation_data_tools/scripts/extend_cost_profiles.py"
    previous_path = list(sys.path)
    previous_modules = {name: module for name, module in sys.modules.items()
                        if _extension_namespace(name)}
    try:
        for name in previous_modules:
            del sys.modules[name]
        spec = importlib.util.spec_from_file_location("simcost_extension_tests", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = previous_path
        for name in list(sys.modules):
            if _extension_namespace(name):
                del sys.modules[name]
        sys.modules.update(previous_modules)


@pytest.fixture
def extension():
    with _isolated_extension() as module:
        yield module


@pytest.mark.parametrize("fail_import", [False, True])
def test_extension_import_isolates_and_restores_foreign_script_modules(monkeypatch, fail_import):
    foreign = ModuleType("shared")
    foreign.__path__ = [str(Path(__file__).resolve().parents[3]
                           / "wrappers/inference_tools/scripts/shared")]
    child = ModuleType("shared.catalog")
    partial = ModuleType("simulator_extension_benchmark")
    monkeypatch.setitem(sys.modules, "shared", foreign)
    monkeypatch.setitem(sys.modules, "shared.catalog", child)
    monkeypatch.setitem(sys.modules, "simulator_extension_benchmark", partial)
    before_path = list(sys.path)
    before_modules = {name: module for name, module in sys.modules.items()
                      if _extension_namespace(name)}
    if fail_import:
        execute = importlib.machinery.SourceFileLoader.exec_module

        def interrupted_import(loader, module):
            if module.__name__ == "simulator_extension_benchmark":
                sys.path.insert(0, "partial-import-path")
                raise ImportError("Intentional interrupted benchmark import")
            return execute(loader, module)

        monkeypatch.setattr(importlib.machinery.SourceFileLoader, "exec_module", interrupted_import)
        with pytest.raises(ImportError, match="Intentional interrupted"):
            with _isolated_extension():
                pytest.fail("The benchmark import should have failed")
    else:
        with _isolated_extension() as module:
            assert module.B is not partial
            assert callable(module.B.resolve_simulator_targets)
            assert "simulation_data_tools/scripts/shared" in sys.modules["shared"].__file__
            assert "shared.catalog_simulators" in sys.modules
    assert sys.path == before_path
    after_modules = {name: module for name, module in sys.modules.items()
                     if _extension_namespace(name)}
    assert after_modules == before_modules


def test_reuse_requires_unchanged_successful_outputs(extension, tmp_path):
    (tmp_path / "out").mkdir()
    data = tmp_path / "out/expression.tsv"
    data.write_text("gene\tc1\nA\t1\n")
    identity = {"seed": 123, "image_id": "frozen"}
    record = {"identity": identity, "status": "ok", "output_sha256": {"expression.tsv": extension.digest(data)}}
    (tmp_path / "observation.json").write_text(json.dumps(record))
    assert extension.verify_observation(tmp_path, identity) == record
    with pytest.raises(ValueError, match="identity changed"):
        extension.verify_observation(tmp_path, {**identity, "seed": 124})
    data.write_text("gene\tc1\nA\t2\n")
    with pytest.raises(ValueError, match="output changed"):
        extension.verify_observation(tmp_path, identity)
    record["status"] = "error"
    (tmp_path / "observation.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="unsuccessful"):
        extension.verify_observation(tmp_path, identity)


def test_dimension_mismatch_cannot_be_published(extension, monkeypatch, tmp_path):
    import jsonschema
    from andrea.core.commands.generate_data import output_validation
    (tmp_path / "out").mkdir()
    (tmp_path / "out/simulator-output-manifest.json").write_text("{}")
    monkeypatch.setattr(jsonschema.Draft202012Validator, "validate", lambda *args, **kwargs: None)
    monkeypatch.setattr(output_validation, "validate_simulator_output_package", lambda **kwargs:
                        {"expression": {"genes": 3, "columns": 28}})
    profile = SimpleNamespace(profile_id="boolode", data_axes={}, truth_requirements={})
    with pytest.raises(ValueError, match="Requested 3x4, produced 3x28"):
        extension.validate_dimensions(tmp_path, profile, SimpleNamespace(genes=3, cells=4))
    assert extension.validate_dimensions(tmp_path, profile, SimpleNamespace(genes=3, cells=28))["expression"]["columns"] == 28


def test_dyngen_budget_requires_verified_actual_tf_and_gene_counts(extension, monkeypatch, tmp_path):
    import jsonschema
    from andrea.core.commands.generate_data import output_validation
    (tmp_path / 'out/extras').mkdir(parents=True)
    (tmp_path / 'out/simulator-output-manifest.json').write_text(json.dumps({'extras': {'tf_list': 'extras/tf_list.txt'}}))
    tf_path = tmp_path / 'out/extras/tf_list.txt'
    tf_path.write_text('TF1\nTF2\nTF3\nTF4\n')
    monkeypatch.setattr(jsonschema.Draft202012Validator, 'validate', lambda *args, **kwargs: None)
    monkeypatch.setattr(output_validation, 'validate_simulator_output_package', lambda **kwargs:
                        {'expression': {'genes': 9, 'columns': 20}})
    monkeypatch.setattr(extension.B, 'apply_dimensions_to_params', lambda **kwargs:
                        {'num_tfs': 2, 'num_targets': 3, 'num_hks': 2})
    profile = SimpleNamespace(simulator_id='dyngen', profile_id='budget', data_axes={}, truth_requirements={},
                              dimension_profile={}, params={})
    result = extension.validate_dimensions(tmp_path, profile, SimpleNamespace(genes=7, cells=20))
    assert result['dimension_semantics']['genes'] == 'requested_gene_budget'
    assert result['requested_dimensions']['genes'] == 7
    assert result['expression']['genes'] == 9
    with pytest.raises(ValueError, match='produced 9x20'):
        extension.validate_dimensions(tmp_path, profile, SimpleNamespace(genes=7, cells=30))
    tf_path.write_text('TF1\nTF2\nTF3\n')
    with pytest.raises(ValueError, match='TF/target/housekeeping'):
        extension.validate_dimensions(tmp_path, profile, SimpleNamespace(genes=7, cells=20))


def test_dyngen_cell_budget_requires_native_proof_and_keeps_coverage_warning(extension, monkeypatch, tmp_path):
    import jsonschema
    from andrea.core.commands.generate_data import output_validation
    (tmp_path / 'out').mkdir()
    (tmp_path / 'out/tfs.txt').write_text('TF1\nTF2\n')
    (tmp_path / 'out/simulator-output-manifest.json').write_text(json.dumps({'extras': {'tf_list': 'tfs.txt'}}))
    monkeypatch.setattr(jsonschema.Draft202012Validator, 'validate', lambda *args, **kwargs: None)
    monkeypatch.setattr(output_validation, 'validate_simulator_output_package', lambda **kwargs:
                        {'expression': {'genes': 7, 'columns': 32}})
    monkeypatch.setattr(extension.B, 'apply_dimensions_to_params', lambda **kwargs:
                        {'num_tfs': 2, 'num_targets': 3, 'num_hks': 2, 'experiment_params': {'kind': 'snapshot'}})
    proof = dict(kind='snapshot', requested_cells=40, observed_cells=32, expected_cells=32,
                 dropped_cells=8, missing_segments=1, expression_matches_native=True)
    monkeypatch.setattr(extension, 'verify_dyngen_sampling', lambda *args: copy.deepcopy(proof))
    profile = SimpleNamespace(simulator_id='dyngen', profile_id='budget', data_axes={}, truth_requirements={},
                              dimension_profile={}, params={})
    size = SimpleNamespace(genes=7, cells=40)
    result = extension.validate_dimensions(tmp_path, profile, size, image_id='sha256:measured')
    assert result['dimension_semantics']['cells'] == 'requested_cell_budget'
    assert result['native_sampling'] == proof
    assert result['requested_dimensions']['columns'] == 40
    assert result['expression']['columns'] == 32
    assert len(result['warnings']) == 2
    for key, value in [('expected_cells', 40), ('observed_cells', 40), ('dropped_cells', 0),
                       ('requested_cells', 32), ('expression_matches_native', False), ('kind', 'synchronised')]:
        previous = proof[key]
        proof[key] = value
        with pytest.raises(ValueError, match='verified native allocation'):
            extension.validate_dimensions(tmp_path, profile, size, image_id='sha256:measured')
        proof[key] = previous


def test_explicit_revalidation_keeps_measurement_and_old_receipt(extension, monkeypatch, tmp_path):
    (tmp_path / 'out').mkdir()
    (tmp_path / 'out/expression.tsv').write_text('gene\tc1\nTF1\t1\n')
    old = {'runner_sha256': 'old', 'image_id': 'sha256:fixed', 'seed': 123}
    new = {**old, 'runner_sha256': 'new'}
    record = dict(identity=old, status='ok', seconds=17.25, validation={'old': True},
                  output_sha256=extension.output_hashes(tmp_path))
    receipt = tmp_path / 'observation.json'
    receipt.write_text(json.dumps(record))
    monkeypatch.setattr(extension, 'validate_dimensions', lambda *args, **kwargs: {'verified': True})
    with pytest.raises(ValueError, match='identity changed'):
        extension.reuse_observation(tmp_path, new, None, None)
    with pytest.raises(ValueError, match='identity changed'):
        extension.reuse_observation(tmp_path, {**new, 'seed': 124}, None, None, ['old'])
    changed = extension.reuse_observation(tmp_path, new, None, None, ['old'])
    assert changed['seconds'] == record['seconds']
    assert changed['output_sha256'] == record['output_sha256']
    assert changed['validation'] == {'verified': True}
    archive = tmp_path / changed['revalidated_from']['receipt']
    assert json.loads(archive.read_text()) == record
    assert extension.digest(archive) == changed['revalidated_from']['sha256']
    assert extension.reuse_observation(tmp_path, new, None, None) == changed
    receipt.write_text(json.dumps({**record, 'status': 'validation_failed'}))
    with pytest.raises(ValueError, match='unsuccessful'):
        extension.reuse_observation(tmp_path, new, None, None, ['old'])


def test_reuse_rejects_empty_or_expanded_output_inventory(extension, tmp_path):
    (tmp_path / "out").mkdir()
    data = tmp_path / "out/expression.tsv"
    data.write_text("gene\tc1\nA\t1\n")
    identity = {"seed": 1}
    record = {"identity": identity, "status": "ok", "output_sha256": {}}
    (tmp_path / "observation.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="inventory is empty"):
        extension.verify_observation(tmp_path, identity)
    record["output_sha256"] = {"expression.tsv": extension.digest(data)}
    (tmp_path / "observation.json").write_text(json.dumps(record))
    (tmp_path / "out/extra.tsv").write_text("new scientific output")
    with pytest.raises(ValueError, match="inventory changed"):
        extension.verify_observation(tmp_path, identity)


def test_complete_profile_requires_every_geometry_and_repetition(extension):
    identity = {"threads": 1, "ram_gb": 8, "repeats": 3}
    profile = SimpleNamespace(sizes=[SimpleNamespace(genes=3, cells=20), SimpleNamespace(genes=3, cells=40)],
                              dimension_profile={"group_count": 0, "population_count": 1})
    points = [{"genes": 3, "cells": cells, "groups": 0, "population_count": 1,
               "threads": 1, "ram_gb": 8, "status": "ok", "repeats_ok": 3, "repeats_total": 3,
               "repeats_failed": 0, "ok_rate": 1, "failure_breakdown": {"oom": 0, "error": 0, "timeout": 0},
               "seconds_p50": 10.0, "seconds_p90": 12.0} for cells in [20, 40]]
    complete = {"benchmark_config": {"extension_identity": identity}, "runtime_points": points}
    assert extension.complete_profile(complete, profile, identity)
    duplicated = copy.deepcopy(complete)
    duplicated["runtime_points"][1] = copy.deepcopy(duplicated["runtime_points"][0])
    assert not extension.complete_profile(duplicated, profile, identity)
    incomplete = copy.deepcopy(complete)
    incomplete["runtime_points"][1]["repeats_total"] = 4
    assert not extension.complete_profile(incomplete, profile, identity)
    assert not extension.complete_profile(complete, profile, {**identity, "image_id": "another"})


def test_atomic_publication_preserves_unrelated_concurrent_profiles(extension, tmp_path):
    source = extension.ROOT / "andrea/catalog_simulation_data_tools/simulators/boolode/cost.json"
    payload = json.loads(source.read_text())
    previous = copy.deepcopy(payload["profiles"][0])
    updated = copy.deepcopy(previous)
    updated["runtime_points"][0]["seconds_p50"] += 0.01
    payload["profiles"][1]["runtime_points"][0]["seconds_p50"] += 0.02
    unrelated = copy.deepcopy(payload["profiles"][1])
    path = tmp_path / "cost.json"
    path.write_text(json.dumps(payload))
    extension.publish_profile(path, updated, previous)
    actual = json.loads(path.read_text())
    assert actual["profiles"][0] == updated
    assert actual["profiles"][1] == unrelated
    before = path.read_bytes()
    with pytest.raises(ValueError, match="changed concurrently"):
        extension.publish_profile(path, updated, previous)
    assert path.read_bytes() == before


def test_invalid_new_profile_never_replaces_valid_cost_file(extension, tmp_path):
    from jsonschema import ValidationError
    source = extension.ROOT / "andrea/catalog_simulation_data_tools/simulators/boolode/cost.json"
    path = tmp_path / "cost.json"
    path.write_bytes(source.read_bytes())
    previous = json.loads(path.read_text())["profiles"][0]
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        extension.publish_profile(path, {"profile_id": previous["profile_id"]}, previous)
    assert path.read_bytes() == before


def test_requested_extras_and_group_count_are_measured_not_assumed(extension, monkeypatch, tmp_path):
    import jsonschema
    from andrea.core.commands.generate_data import output_validation
    (tmp_path / "out").mkdir()
    manifest = tmp_path / "out/simulator-output-manifest.json"
    manifest.write_text(json.dumps({"extras": {}}))
    monkeypatch.setattr(jsonschema.Draft202012Validator, "validate", lambda *args, **kwargs: None)
    monkeypatch.setattr(output_validation, "validate_simulator_output_package", lambda **kwargs:
                        {"expression": {"genes": 3, "columns": 4}})
    profile = SimpleNamespace(profile_id="example", data_axes={}, truth_requirements={},
                              effective_extras=["groups"], dimension_profile={"group_count": 2})
    size = SimpleNamespace(genes=3, cells=4)
    with pytest.raises(ValueError, match="extras were not generated"):
        extension.validate_dimensions(tmp_path, profile, size)
    manifest.write_text(json.dumps({"extras": {"groups": "groups.tsv"}}))
    (tmp_path / "out/groups.tsv").write_text("column\tcluster\nc1\tone\nc2\tone\nc3\tone\nc4\tone\n")
    with pytest.raises(ValueError, match="Requested 2 groups, produced 1"):
        extension.validate_dimensions(tmp_path, profile, size)
    profile.dimension_profile['group_count'] = 0
    assert extension.validate_dimensions(tmp_path, profile, size)['observed_groups'] == 1
