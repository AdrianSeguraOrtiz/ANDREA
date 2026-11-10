"""PFBNet expression-only workflow, scientific adapter and runtime observations.

The native booster is replaced by a recorder: these tests do not fit models or
generate cost measurements. Optional clone-based checks skip when the supplied
read-only source cache is unavailable.
"""

from __future__ import annotations
import ast
from contextlib import nullcontext
import csv
import importlib.util
import json
import math
import operator
from pathlib import Path
import re
import sys
import time
from types import ModuleType, SimpleNamespace
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "wrappers/inference_tools/tools/pfbnet/run_tool.py"
UPSTREAM = ROOT / "wrappers/inference_tools/tools/pfbnet/repo/PFBNet"
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts/templates/python"))
metadata_spec = importlib.util.spec_from_file_location(
    "_column_metadata", ROOT / "andrea/core/shared/column_metadata.py"
)
metadata = importlib.util.module_from_spec(metadata_spec)
sys.modules["_column_metadata"] = metadata
metadata_spec.loader.exec_module(metadata)
spec = importlib.util.spec_from_file_location("pfbnet_wrapper", SOURCE)
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


def write_tsv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(header)
        writer.writerows(rows)


def read_tsv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return (reader.fieldnames, list(reader))


def mutate_table(path, change):
    header, rows = read_tsv(path)
    change(rows)
    write_tsv(path, header, [[row[name] for name in header] for row in rows])


@pytest.fixture
def inputs(tmp_path):
    extra = tmp_path / "extra"
    extra.mkdir()
    genes = ["0001", "TF_alpha", 'β,quote"']
    time_columns = [
        f"{series}{point}" for series in ("b", "a") for point in (3, 0, 4, 1, 5, 2)
    ]
    stationary = ["stationary-1", "stationary-2", "stationary-3", "stationary"]
    columns = [*time_columns, *stationary]
    values = []
    for index, gene in enumerate(genes):
        row = [
            (
                (100 if col[0] == "b" else 0) + (index + 1) * int(col[1:])
                if col in time_columns
                else 9000 + index
            )
            for col in columns
        ]
        values.append([gene, *row])
    write_tsv(tmp_path / "expression.tsv", ["gene", *columns], values)
    descriptors = []
    for column in reversed(columns):
        if column in time_columns:
            descriptor = [
                column,
                "time_series",
                column[0],
                "condition-a",
                column[0],
                f"obs:{column}",
                "technical-1",
                int(column[1:]) + (20 if column[0] == "b" else 0),
                "hour",
                "longitudinal",
            ]
        else:
            descriptor = [
                column,
                "steady_state",
                "",
                "baseline",
                f"unit:{column}",
                f"obs:{column}",
                "technical-1",
                "",
                "",
                "",
            ]
        descriptors.append(descriptor)
    write_tsv(extra / "column_descriptors.tsv", metadata.TEMPORAL_COLUMNS, descriptors)
    (tmp_path / "params.json").write_text("{}")
    (tmp_path / "execution.json").write_text('{"mode":"global"}')
    return (tmp_path, extra, genes)


def prepared(inputs, **raw):
    root, extra, _ = inputs
    return wrapper.prepare_data(
        root / "expression.tsv", extra, wrapper.parse_params(raw)
    )


def upstream_raw():
    path = UPSTREAM / wrapper.UPSTREAM_SOURCE["path"]
    if not path.is_file():
        pytest.skip("Optional user-provided PFBNet upstream clone is unavailable")
    return path.read_bytes()


def function_namespace(source):
    """Load actual function bodies without importing an installed XGBoost."""
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    namespace = {"np": np, "math": math, "operator": operator, "re": re, "time": time}
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), "PFBNet.py", "exec"),
        namespace,
    )
    return namespace


def test_native_surface_and_published_example_defaults():
    assert wrapper.parse_params({}) == {
        "k": 2,
        "alpha": 0.45,
        "iter_num": 1000,
        "seed": 0,
    }


@pytest.mark.parametrize("alpha", [0, 0.45, 1])
def test_decay_domain_includes_both_paper_endpoints(alpha):
    params = wrapper.parse_params({"alpha": alpha})
    assert params["alpha"] == alpha


@pytest.mark.parametrize(
    "raw",
    [
        {"k": 0},
        {"k": -1},
        {"k": True},
        {"k": 1.5},
        {"alpha": -0.1},
        {"alpha": 1.1},
        {"alpha": True},
        {"alpha": float("nan")},
        {"alpha": float("inf")},
        {"iter_num": 0},
        {"iter_num": True},
        {"iter_num": 1.2},
        {"seed": -1},
        {"seed": 2**31},
        {"seed": True},
        {"variant": "expression_only"},
        {"variant": "knockout"},
        {"variant": "dream4"},
        {"variant": "auto"},
        {"samples": 2},
        {"sample_num": 2},
        {"feature_num": 2},
        {"gamma": 0},
        {"max_depth": 2},
        {"n_estimators": 5},
        {"nthread": 4},
    ],
)
def test_invalid_or_nonpublic_parameters_are_rejected(raw):
    with pytest.raises(ValueError):
        wrapper.parse_params(raw)


def test_expression_only_needs_no_perturbation_table_and_ignores_stationary_roles(
    inputs,
):
    _, extra, _ = inputs
    data = prepared(inputs)
    assert data["expression_only_data"].shape == (2, 3, 6)
    assert data["accumulation_offsets"] == [0, 1, 2]
    assert data["accumulation_durations"] == [0, 1, 2]
    np.testing.assert_array_equal(data["expression_only_data"][0, 0], range(6))
    np.testing.assert_array_equal(data["expression_only_data"][1, 0], range(100, 106))
    assert set(data["unused_steady_state_columns"]) == {
        "stationary-1",
        "stationary-2",
        "stationary-3",
        "stationary",
    }


def test_expression_only_regulators_are_reordered_to_native_prefix(inputs):
    _, extra, genes = inputs
    (extra / "tf_list.txt").write_text(f"{genes[2]}\n{genes[0]}\n", encoding="utf-8")
    data = prepared(inputs)
    assert set(data["regulators"]) == {genes[2], genes[0]}
    assert data["native_genes"][:2] == data["regulators"]
    assert data["native_genes"][2] == genes[1]
    for position, gene in enumerate(data["native_genes"]):
        np.testing.assert_array_equal(
            data["time_series"][:6, position], np.arange(6) * (genes.index(gene) + 1)
        )


def test_tf_list_uses_existing_txt_list_whitespace_and_blank_line_semantics(inputs):
    _, extra, genes = inputs
    (extra / "tf_list.txt").write_text(
        f"\n  {genes[2]}  \n\n{genes[0]}\n\n", encoding="utf-8"
    )
    data = prepared(inputs)
    assert data["regulators"] == [genes[2], genes[0]]
    assert data["native_genes"] == [genes[2], genes[0], genes[1]]


@pytest.mark.parametrize("contents", ["absent\n0001\n", "0001\n0001\n", "0001\n"])
def test_invalid_native_regulator_prefix_rejects(inputs, contents):
    (inputs[1] / "tf_list.txt").write_text(contents)
    with pytest.raises(ValueError):
        prepared(inputs)


def test_technical_measurements_in_unused_stationary_columns_do_not_block(inputs):
    _, extra, _ = inputs
    header, rows = read_tsv(extra / "column_descriptors.tsv")
    prior = next((row for row in rows if row["column"] == "stationary-1"))
    unused = next((row for row in rows if row["column"] == "stationary"))
    unused.update(prior, column="stationary", technical_replicate="technical-2")
    write_tsv(
        extra / "column_descriptors.tsv",
        header,
        [[row[key] for key in header] for row in rows],
    )
    data = prepared(inputs)
    assert "stationary" in data["unused_steady_state_columns"]


@pytest.mark.parametrize(
    "change",
    [
        lambda rows: [
            row.update(timepoint="2.2") for row in rows if row["column"] == "a2"
        ],
        lambda rows: [
            row.update(timepoint=str(float(row["timepoint"]) * 2))
            for row in rows
            if row["series_id"] == "b"
        ],
        lambda rows: [
            row.update(sampling_design="independent_samples")
            for row in rows
            if row["observation_type"] == "time_series"
        ],
        lambda rows: [
            row.update(replicate="different") for row in rows if row["column"] == "a1"
        ],
        lambda rows: [
            row.update(
                observation_id="obs:a1",
                technical_replicate="technical-2",
                timepoint="1",
            )
            for row in rows
            if row["column"] == "a2"
        ],
        lambda rows: [
            row.update(time_unit="second") for row in rows if row["column"] == "a1"
        ],
        lambda rows: [
            row.update(
                observation_type="steady_state",
                series_id="",
                timepoint="",
                time_unit="",
                sampling_design="",
            )
            for row in rows
            if row["column"] == "b5"
        ],
    ],
)
def test_invalid_temporal_geometry_is_rejected_by_requested_run(inputs, change):
    mutate_table(inputs[1] / "column_descriptors.tsv", change)
    with pytest.raises(ValueError):
        prepared(inputs)


def test_lag_requires_more_observed_timepoints_than_k(inputs):
    with pytest.raises(ValueError):
        prepared(inputs, k=6)


def test_constant_temporal_target_does_not_invent_a_prefit_variance_requirement(inputs):
    path = inputs[0] / "expression.tsv"
    header, rows = read_tsv(path)
    for column in header[1:]:
        if column.startswith(("a", "b")):
            rows[0][column] = "1"
    write_tsv(path, header, [[row[name] for name in header] for row in rows])
    data = prepared(inputs)
    np.testing.assert_array_equal(data["time_series"][:, 0], np.ones(12))


@pytest.mark.parametrize("invalid", ["nan", "inf", "not-a-number", "1e40"])
def test_invalid_or_float32_overflow_expression_rejects_before_booster(inputs, invalid):
    path = inputs[0] / "expression.tsv"
    header, rows = read_tsv(path)
    rows[0]["a0"] = invalid
    write_tsv(path, header, [[row[name] for name in header] for row in rows])
    with pytest.raises(ValueError):
        prepared(inputs)


@pytest.mark.parametrize("identifier", ["", "0001", " gene "])
def test_duplicate_or_nonexact_gene_identifiers_are_rejected(inputs, identifier):
    path = inputs[0] / "expression.tsv"
    header, rows = read_tsv(path)
    rows[1][header[0]] = identifier
    write_tsv(path, header, [[row[name] for name in header] for row in rows])
    with pytest.raises(ValueError):
        prepared(inputs)


def test_duplicate_expression_column_identifiers_are_rejected(inputs):
    path = inputs[0] / "expression.tsv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    rows[0][2] = rows[0][1]
    write_tsv(path, rows[0], rows[1:])
    with pytest.raises(ValueError):
        prepared(inputs)


def test_upstream_source_patches_are_guarded_by_exact_identity():
    raw = upstream_raw()
    source = wrapper.patched_source(raw)
    compile(source, "PFBNet.py", "exec")
    assert "get_fscore()" in source
    assert not any(
        (
            isinstance(node, ast.keyword) and node.arg == "importance_type"
            for node in ast.walk(ast.parse(source))
        )
    )
    with pytest.raises(ValueError, match="SHA-256"):
        wrapper.patched_source(raw + b"\n")


def test_preparation_retains_expression_source_and_excludes_other_workflows(tmp_path):
    raw = upstream_raw()
    receipt = wrapper.prepare_upstream(
        output_dir=tmp_path / "prepared", source_dir=UPSTREAM
    )
    assert receipt["workflow"] == "expression_only"
    files = list((tmp_path / "prepared").glob("*.py"))
    assert len(files) == 2
    assert (tmp_path / "prepared/PFBNet_Ecoil.original.py").read_bytes() == raw
    assert not list((tmp_path / "prepared").glob("*dream*"))


def test_expression_only_original_three_series_1484_gene_shape_is_preserved():
    raw = upstream_raw()
    original = function_namespace(raw.decode())
    patched = function_namespace(wrapper.patched_source(raw))
    data = np.random.default_rng(881).normal(size=(3, 1484, 5))
    native_scores = np.random.default_rng(822).uniform(0.1, 2, size=(1484, 4))
    observations = []

    def record_weights(matrix, feature_num, iter_num, **kwargs):
        observations.append(np.array(matrix, copy=True))
        assert matrix.shape == (9, 1484)
        assert feature_num == 4 and iter_num == 3
        return native_scores.copy()

    original["xgboost_weight"] = patched["xgboost_weight"] = record_weights
    expected = original["ecoil_main"](data, 5, 2, 0.45, 4, 3)
    actual = patched["ecoil_main"](data, 5, 2, 0.45, 4, 3)
    np.testing.assert_array_equal(observations[0], observations[1])
    np.testing.assert_array_equal(expected, actual)


@pytest.mark.parametrize("series_count,gene_count", [(1, 3), (2, 5), (4, 3)])
def test_expression_only_generalized_accumulation_includes_current_value(
    series_count, gene_count
):
    ns = function_namespace(wrapper.patched_source(upstream_raw()))
    data = np.arange(series_count * gene_count * 6, dtype=float).reshape(
        series_count, gene_count, 6
    )
    actual = ns["time_series"](data, 2, 0.45)
    expected = np.concatenate(
        [
            (0.45**2 * series[:, :-2] + 0.45 * series[:, 1:-1] + series[:, 2:]).T
            for series in data
        ],
        axis=0,
    )
    np.testing.assert_array_equal(actual, expected)


class RecordingDMatrix:

    def __init__(self, data, label=None, **kwargs):
        self.data = np.array(data, copy=True)
        self.label = None if label is None else np.array(label, copy=True)


class RecordingBooster:

    def __init__(self, feature_count, round_count):
        self.feature_count, self.round_count = (feature_count, round_count)

    def get_fscore(self):
        return {f"f{index}": index + 1 for index in range(self.feature_count)}

    def get_dump(self, **kwargs):
        return ["tree"] * self.round_count


class RecordingXGBoost:

    def __init__(self):
        self.fitted = []
        self.fail_at = None
        self.DMatrix = RecordingDMatrix
        self.__version__ = "recording-no-fits"

    def train(self, params, data, num_boost_round=10, **kwargs):
        if len(self.fitted) == self.fail_at:
            raise RuntimeError("native fit failed")
        self.fitted.append(
            (data.data.copy(), data.label.copy(), dict(params), num_boost_round)
        )
        return RecordingBooster(data.data.shape[1], num_boost_round)


@pytest.fixture
def recording_module(monkeypatch):
    recorder = RecordingXGBoost()
    monkeypatch.setitem(sys.modules, "xgboost", recorder)
    monkeypatch.setitem(
        sys.modules, "sklearn", SimpleNamespace(preprocessing=SimpleNamespace())
    )
    monkeypatch.setitem(
        sys.modules,
        "threadpoolctl",
        SimpleNamespace(
            threadpool_info=lambda: [], threadpool_limits=lambda **kwargs: nullcontext()
        ),
    )

    def load():
        module = ModuleType("PFBNet_recording_expression_only")
        exec(
            compile(wrapper.patched_source(upstream_raw()), "PFBNet.py", "exec"),
            module.__dict__,
        )
        return (recorder, module)

    return load


def run_recording(inputs, implementation, raw):
    root, extra, _ = inputs
    (root / "params.json").write_text(json.dumps(raw))
    return wrapper.run(
        input_path=root / "expression.tsv",
        params_path=root / "params.json",
        extra_dir=extra,
        output_dir=root / "out",
        threads=2,
        upstream=implementation,
    )


def test_native_models_resources_progress_and_canonical_identifiers(
    inputs, recording_module
):
    recorder, module = recording_module()
    result = run_recording(inputs, module, {"iter_num": 5, "seed": 12})
    root, _, genes = inputs
    assert result
    assert result["samples_argument"] == 6
    assert result["independent_series_count"] == 2
    assert result["candidate_regulator_count"] == 3
    assert result["effective_predictor_columns"] == {gene: 2 for gene in genes}
    assert result["accumulation_offsets"] == [0, 1, 2]
    assert len(recorder.fitted) == len(genes)
    for x, y, params, rounds in recorder.fitted:
        assert x.shape == (8, 2) and y.size == 8
        assert rounds == 5
        assert params["nthread"] == 2 and params["seed"] == 12
        assert params["max_depth"] == 4 and params["min_child_weight"] == 4
        assert params["eta"] == 0.0008 and params["lambda"] == 0
        assert params["subsample"] == 0.7 and params["colsample_bytree"] == 0.9
        assert "gamma" not in params or params["gamma"] == 0
    with (root / "out/network.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 6
    assert {row["source"] for row in rows} == set(genes)
    assert {row["target"] for row in rows} == set(genes)
    assert all(
        (row["source"] != row["target"] and float(row["score"]) > 0 for row in rows)
    )
    assert all(
        (
            row["sign"] == "?"
            and row["evidence"] == "association"
            and (row["context"] == "global")
            for row in rows
        )
    )
    events = [
        json.loads(line)
        for line in (root / "out/provenance/progress-events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [
        event["completed"] for event in events if event["phase"] == "inference"
    ] == list(range(4))
    assert any((event["phase"] == "aggregation" for event in events))
    assert events[-1]["status"] == "completed" and events[-1]["percent"] == 100
    model = json.loads((root / "out/provenance/model-00000000.json").read_text())
    assert model
    assert model["effective_predictor_columns"] == 2
    assert (root / "out/provenance/native-score-matrix.tsv").is_file()


def test_fitted_predictors_and_labels_preserve_native_accumulation(
    inputs, recording_module
):
    recorder, module = recording_module()
    run_recording(inputs, module, {"iter_num": 5})
    data = prepared(inputs)
    blocks = data["time_series"].reshape(2, 6, 3)
    accumulated = np.concatenate(
        [0.45**2 * series[:-2] + 0.45 * series[1:-1] + series[2:] for series in blocks]
    )
    responses = accumulated
    for target, (x, y, _, _) in enumerate(recorder.fitted):
        np.testing.assert_array_equal(x, np.delete(accumulated, target, axis=1))
        np.testing.assert_array_equal(y.reshape(-1), responses[:, target])


def test_native_regulator_prefix_fits_only_selected_candidates_and_exports_original_ids(
    inputs, recording_module
):
    root, extra, genes = inputs
    (extra / "tf_list.txt").write_text(f"{genes[2]}\n{genes[0]}\n", encoding="utf-8")
    recorder, module = recording_module()
    resolved = run_recording(inputs, module, {"iter_num": 5})
    data = prepared(inputs)
    assert [x.shape for x, _, _, _ in recorder.fitted] == [(8, 1), (8, 1), (8, 2)]
    assert resolved["candidate_regulator_count"] == 2
    assert resolved["effective_predictor_columns"] == {
        gene: 1 if gene in data["regulators"] else 2 for gene in genes
    }
    for index, gene in enumerate(data["native_genes"]):
        model = json.loads(
            (root / f"out/provenance/model-{index:08d}.json").read_text()
        )
        assert (
            model["effective_predictor_columns"]
            == resolved["effective_predictor_columns"][gene]
        )
    with (root / "out/network.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["source"] for row in rows} == set(data["regulators"])
    assert {row["target"] for row in rows} == set(genes)
    assert len(rows) == 4


def test_failed_native_model_keeps_only_completed_fits(inputs, recording_module):
    recorder, module = recording_module()
    recorder.fail_at = 2
    with pytest.raises(RuntimeError, match="native fit failed"):
        run_recording(inputs, module, {"iter_num": 5})
    progress = json.loads((inputs[0] / "out/progress.json").read_text())
    assert progress["status"] == "failed"
    assert progress["completed"] == 2 and progress["total"] == 3
    assert progress["percent"] < 100
    events = [
        json.loads(line)
        for line in (inputs[0] / "out/provenance/progress-events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert not any((event["status"] == "completed" for event in events))


def test_export_failure_never_reports_success_or_loses_completed_fit_count(
    inputs, recording_module, monkeypatch
):
    recorder, module = recording_module()

    def fail_export(*args, **kwargs):
        raise ValueError("invalid native scores")

    monkeypatch.setattr(wrapper, "export_network", fail_export)
    with pytest.raises(ValueError, match="invalid native scores"):
        run_recording(inputs, module, {"iter_num": 5})
    assert len(recorder.fitted) == 3
    progress = json.loads((inputs[0] / "out/progress.json").read_text())
    assert progress["status"] == "failed" and progress["percent"] < 100
    assert progress["completed"] == 3 and progress["total"] == 3


def test_export_preserves_native_regulator_to_target_orientation_and_score(tmp_path):
    path = tmp_path / "network.csv"
    score = np.array([[0, 1.25, 3.0], [2.5, 0, 4.5]])
    sources = ['TF,"β', "0001"]
    targets = ["0001", 'TF,"β', "gene_three"]
    wrapper.export_network(path, score, sources, targets)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    actual = {(row["source"], row["target"]): float(row["score"]) for row in rows}
    assert actual == {(sources[0], targets[2]): 3.0, (sources[1], targets[2]): 4.5}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1])
def test_export_rejects_undefined_or_negative_native_scores(tmp_path, bad):
    score = np.array([[0, bad], [1, 0]], dtype=float)
    with pytest.raises(ValueError):
        wrapper.export_network(tmp_path / "network.csv", score, ["A", "B"], ["A", "B"])
