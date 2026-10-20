"""Method-contract tests use a recording fake; no scientific fit or Docker runs."""

from __future__ import annotations

import ast
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "wrappers/inference_tools/tools/dyngenie3"
FIXTURES = ROOT / "wrappers/inference_tools/tests/fixtures/dyngenie3"
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts/templates/python"))
spec = importlib.util.spec_from_file_location(
    "dyngenie3_wrapper", SOURCE / "run_tool.py"
)
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)
spec = importlib.util.spec_from_file_location(
    "dyngenie3_patches", SOURCE / "patch_upstream.py"
)
patches = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patches)


@pytest.fixture
def inputs(tmp_path):
    extra = tmp_path / "extra"
    extra.mkdir()
    for name in ("timepoints.tsv", "replicates.tsv"):
        shutil.copy2(FIXTURES / name, extra / name)
    shutil.copy2(FIXTURES / "expression.tsv", tmp_path / "expression.tsv")
    temporal = json.loads((FIXTURES / "temporal.json").read_text())
    (tmp_path / "params.json").write_text("{}")
    (tmp_path / "execution.json").write_text(
        json.dumps({"mode": "global", "temporal": temporal})
    )
    return tmp_path, extra, temporal


def change_table(path, mutate):
    with path.open() as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headings, rows = reader.fieldnames, list(reader)
    mutate(rows)
    with path.open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=headings, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def test_defaults_equal_upstream_contract_and_reviewed_spec():
    spec = json.loads((SOURCE / "toolspec.draft.json").read_text())
    assert wrapper.DEFAULTS == {
        name: data["default"] for name, data in spec["params"].items()
    }
    assert wrapper.resolve_params({}, 6) == wrapper.DEFAULTS


@pytest.mark.parametrize(
    "override",
    [
        {"alpha": float("nan")},
        {"alpha": [0.1]},
        {"alpha": [-0.1] * 6},
        {"alpha": True},
        {"ntrees": 0},
        {"ntrees": True},
        {"K": False},
        {"K": 0},
        {"K": "auto"},
        {"seed": -1},
        {"seed": 2**32},
        {"seed": True},
        {"maxcount": -1},
        {"save_models": 1},
        {"nthreads": 4},
        {"tree_method": "random_forest"},
    ],
)
def test_invalid_or_unexposed_options_are_rejected(override):
    with pytest.raises(ValueError):
        wrapper.resolve_params(override, 6)


def test_series_are_joined_by_id_sorted_independently_and_never_concatenated(inputs):
    root, extra, temporal = inputs
    genes, columns, values = wrapper.read_matrix(root / "expression.tsv")
    series, coordinates, resolved = wrapper.prepare_series(
        values, columns, extra, temporal
    )
    assert genes[:2] == ["TF:01", "0002"]
    assert len(series) == 3 and all(value.shape == (5, 6) for value in series)
    assert all(value.tolist() == [0, 1, 3, 6, 10] for value in coordinates)
    assert sum(len(value) - 1 for value in coordinates) == 12
    assert all(
        len({column.split(".")[0] for column in row["columns"]}) == 1
        for row in resolved
    )
    with (extra / "timepoints.tsv").open() as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows[0]["column"] == columns[-1]  # Deliberately reversed metadata rows.


@pytest.mark.parametrize(
    "change,match",
    [
        ("missing", "exactly"),
        ("duplicate_time", "strictly increasing"),
        ("unit", "units"),
        ("technical", "Technical replicate"),
        ("float32_collapse", "float32"),
        ("overflow_interval", "float32"),
        ("independent", "longitudinal"),
    ],
)
def test_invalid_time_structures_are_rejected_without_fit(inputs, change, match):
    root, extra, temporal = inputs
    _, columns, values = wrapper.read_matrix(root / "expression.tsv")
    path = extra / "timepoints.tsv"
    if change == "missing":
        change_table(path, lambda rows: rows.pop())
    elif change == "unit":
        change_table(path, lambda rows: rows[0].update(time_unit="minute"))
    elif change == "technical":
        change_table(
            path, lambda rows: rows[0].update(observation_id=rows[1]["observation_id"])
        )
    elif change == "duplicate_time":
        change_table(path, lambda rows: rows[0].update(timepoint=rows[1]["timepoint"]))
    elif change == "float32_collapse":
        change_table(
            path,
            lambda rows: [
                row.update(timepoint=str(1e12 + i)) for i, row in enumerate(rows)
            ],
        )
    elif change == "overflow_interval":

        def overflow(rows):
            for row in rows:
                if row["series_id"] == "control_1":
                    row["timepoint"] = {
                        "control_1.t0": "-3e38",
                        "control_1.t1": "3e38",
                        "control_1.t2": "3.1e38",
                        "control_1.t3": "3.2e38",
                        "control_1.t4": "3.3e38",
                    }[row["column"]]

        change_table(path, overflow)
    else:
        temporal["sampling_design"] = "independent_samples"
    with pytest.raises(ValueError, match=match):
        wrapper.prepare_series(values, columns, extra, temporal)


def test_steady_state_aligns_by_gene_id_and_keeps_distinct_sample_axis(inputs):
    root, _, _ = inputs
    genes, _, _ = wrapper.read_matrix(root / "expression.tsv")
    steady, samples = wrapper.load_steady_state(
        FIXTURES / "steady_state_expression.tsv", genes
    )
    assert steady.shape == (3, 6)
    assert samples == ["SS.control", "SS.treated", "SS.other"]
    assert steady[0].tolist() == [10, 11, 12, 13, 14, 15]
    with pytest.raises(ValueError, match="universes"):
        wrapper.load_steady_state(FIXTURES / "steady_state_expression.tsv", genes[:-1])


def test_complete_wrapper_forwards_public_api_and_preserves_raw_scores(inputs):
    root, extra, temporal = inputs
    for name in ("steady_state_expression.tsv", "tf_list.txt"):
        shutil.copy2(FIXTURES / name, extra / name)
    params = {
        "alpha": [0.1] * 6,
        "K": "all",
        "ntrees": 37,
        "tree_method": "ET",
        "compute_quality_scores": True,
        "seed": 17,
        "maxcount": 1,
    }
    (root / "params.json").write_text(json.dumps(params))
    calls = []

    def infer(**kwargs):
        calls.append(kwargs)
        vim = np.zeros((6, 6))
        vim[0, 1] = 0.12345678912345678
        return vim, np.full(6, 0.1), [], 0.2, []

    resolved = wrapper.run(
        input_path=root / "expression.tsv",
        params_path=root / "params.json",
        extra_dir=extra,
        output_dir=root / "out",
        threads=2,
        upstream=SimpleNamespace(dynGENIE3=infer),
    )
    assert len(calls) == 1
    call = calls[0]
    assert call["random_state"] == 17 and call["nthreads"] == 2
    assert call["alpha"] == [0.1] * 6 and call["K"] == "all" and call["ntrees"] == 37
    assert call["SS_data"].shape == (3, 6) and call["regulators"] == [
        "TF:01",
        "0002",
        "gene-A",
    ]
    assert len(call["TS_data"]) == 3 and call["tree_method"] == "ET"
    with (root / "out/network.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert row == {
        "source": "TF:01",
        "target": "0002",
        "score": "0.12345678912345678",
        "sign": "?",
        "evidence": "association",
        "context": "global",
    }
    assert resolved["target_seeds"][0] != resolved["target_seeds"][1]
    assert (
        json.loads((root / "out/provenance/resolved.json").read_text())["temporal"]
        == temporal
    )
    assert json.loads((root / "out/progress.json").read_text())["status"] == "completed"


def test_empty_export_and_undefined_diagnostics_are_truthful(inputs):
    root, extra, _ = inputs
    (root / "params.json").write_text(
        json.dumps({"maxcount": 0, "compute_quality_scores": True})
    )
    fake = SimpleNamespace(
        dynGENIE3=lambda **kwargs: (
            np.ones((6, 6)),
            np.zeros(6),
            float("nan"),
            float("nan"),
            [],
        )
    )
    wrapper.run(
        input_path=root / "expression.tsv",
        params_path=root / "params.json",
        extra_dir=extra,
        output_dir=root / "out",
        threads=1,
        upstream=fake,
    )
    assert len((root / "out/network.csv").read_text().splitlines()) == 1
    progress = json.loads((root / "out/progress.json").read_text())
    assert len(progress["warnings"]) == 3
    quality = json.loads((root / "out/provenance/quality_scores.json").read_text())
    assert quality["prediction_score"] is None and quality["stability_score"] is None


def test_model_export_and_effective_feature_count_are_recorded(inputs, monkeypatch):
    root, extra, _ = inputs
    (root / "params.json").write_text(json.dumps({"K": 1, "save_models": True}))
    models = [f"model-{i}" for i in range(6)]
    saved = []

    def dump(value, path):
        saved.append(value)
        path.write_text("synthetic model receipt")

    monkeypatch.setitem(sys.modules, "joblib", SimpleNamespace(dump=dump))
    calls = []

    def infer(**kwargs):
        calls.append(kwargs)
        return np.ones((6, 6)), np.ones(6), [], [], models

    result = wrapper.run(
        input_path=root / "expression.tsv",
        params_path=root / "params.json",
        extra_dir=extra,
        output_dir=root / "out",
        threads=12,
        upstream=SimpleNamespace(dynGENIE3=infer),
    )
    assert saved == [models]
    assert calls[0]["save_models"] and calls[0]["nthreads"] == 6
    assert result["max_features_count"] == 1 and result["max_features_argument"] == 1
    assert result["effective_workers"] == 6 and result["threads"] == 12


def test_patches_reject_unverified_sources():
    with pytest.raises(ValueError, match="SHA-256"):
        patches.patched_source(b"not the official source")


def cgroup_helper_without_importing_or_fitting_the_method():
    """Compile only the resource helper and supply synthetic filesystem roots."""
    outer = ast.parse((SOURCE / "patch_upstream.py").read_text())
    appended = next(
        node.value
        for node in ast.walk(outer)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "def _andrea_cgroups" in node.value
    )
    helper = next(
        node
        for node in ast.parse(appended).body
        if isinstance(node, ast.FunctionDef) and node.name == "_andrea_cgroups"
    )
    namespace = {}
    exec(
        compile(
            ast.Module(body=[helper], type_ignores=[]),
            "<synthetic-cgroup-helper>",
            "exec",
        ),
        namespace,
    )
    return namespace["_andrea_cgroups"]


def test_effective_cgroup_v2_limits_are_read_from_only_the_supplied_fake_root(tmp_path):
    inspect = cgroup_helper_without_importing_or_fitting_the_method()
    (tmp_path / "cpu.max").write_text("200000 100000\n")
    (tmp_path / "memory.max").write_text("4294967296\n")
    limits = inspect(tmp_path)
    assert limits["version"] == 2 and limits["cpu"]["limit_cpus"] == 2
    assert (
        limits["cpu"]["raw"] == "200000 100000"
        and limits["memory"]["limit_bytes"] == 4294967296
    )
    (tmp_path / "cpu.max").write_text("max 100000\n")
    (tmp_path / "memory.max").write_text("max\n")
    assert (
        inspect(tmp_path)["cpu"]["limit_cpus"] is None
        and inspect(tmp_path)["memory"]["limit_bytes"] is None
    )


def test_effective_cgroup_v1_fallback_and_unavailable_or_malformed_limits(tmp_path):
    inspect = cgroup_helper_without_importing_or_fitting_the_method()
    assert inspect(tmp_path)["version"] is None
    cpu, memory = tmp_path / "cpu,cpuacct", tmp_path / "memory"
    cpu.mkdir()
    memory.mkdir()
    (cpu / "cpu.cfs_quota_us").write_text("100000\n")
    (cpu / "cpu.cfs_period_us").write_text("100000\n")
    (memory / "memory.limit_in_bytes").write_text("4294967296\n")
    assert (
        inspect(tmp_path)["version"] == 1
        and inspect(tmp_path)["cpu"]["limit_cpus"] == 1
    )
    (cpu / "cpu.cfs_quota_us").write_text("-1\n")
    assert inspect(tmp_path)["cpu"]["limit_cpus"] is None
    (cpu / "cpu.cfs_period_us").write_text("0\n")
    assert inspect(tmp_path)["version"] is None


def test_steady_state_preflight_rejects_duplicate_sample_headers(tmp_path):
    from andrea.core.shared.input_validation import validate_tsv_file_with_spec

    spec = json.loads(
        (
            ROOT
            / "andrea/catalog_inference_tools/input_specs/steady_state_expression.json"
        ).read_text()
    )
    matrix = tmp_path / "steady.tsv"
    matrix.write_text("gene\tSS1\tSS1\nG1\t1\t2\nG2\t3\t4\n")
    result = validate_tsv_file_with_spec(
        key="steady_state_expression",
        path=matrix,
        spec=spec,
        expression_genes={"G1", "G2"},
        expression_columns={"t1", "t2"},
        expression_columns_count=2,
    )
    assert any("duplicate" in error for error in result.errors)


def test_exact_public_source_patch_ast_and_seed_forwarding_when_evidence_is_available():
    source = SOURCE / "repo/dynGENIE3/dynGENIE3_python/dynGENIE3.py"
    if not source.is_file():
        pytest.skip("Optional local upstream evidence clone is unavailable")
    tree = ast.parse(patches.patched_source(source.read_bytes()))
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert functions["dynGENIE3"].args.args[-1].arg == "random_state"
    assert functions["dynGENIE3_single"].args.args[-1].arg == "random_state"
    worker = ast.unparse(functions["wr_dynGENIE3_single"])
    assert "args[11]" in worker
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"RandomForestRegressor", "ExtraTreesRegressor"}
    ]
    assert len(calls) == 2 and all(
        {kw.arg for kw in node.keywords} >= {"random_state", "n_jobs"} for node in calls
    )
    seed_namespace = {"np": np}
    exec(
        compile(
            ast.Module(body=[functions["_andrea_target_seed"]], type_ignores=[]),
            "<seed>",
            "exec",
        ),
        seed_namespace,
    )
    assert seed_namespace["_andrea_target_seed"](19, 0) == seed_namespace[
        "_andrea_target_seed"
    ](19, 0)
    assert seed_namespace["_andrea_target_seed"](19, 0) != seed_namespace[
        "_andrea_target_seed"
    ](19, 1)
    quality_namespace = {"pearsonr": lambda a, b: (0.5, 0.1)}
    exec(
        compile(
            ast.Module(body=[functions["_andrea_pearsonr"]], type_ignores=[]),
            "<pearson>",
            "exec",
        ),
        quality_namespace,
    )
    assert np.isnan(quality_namespace["_andrea_pearsonr"]([1.0], [2.0])[0])
    assert quality_namespace["_andrea_pearsonr"]([1, 2], [2, 3]) == (0.5, 0.1)
