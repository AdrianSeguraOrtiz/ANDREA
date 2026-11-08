"""BiXGBoost source adapters and scientific routing, without a fitted booster."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
from contextlib import nullcontext
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "wrappers/inference_tools/tools/bixgboost/run_tool.py"
UPSTREAM = ROOT / "wrappers/inference_tools/tools/bixgboost/repo/BiXGBoost/BiXGBoost.py"
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts/templates/python"))
metadata_spec = importlib.util.spec_from_file_location(
    "_column_metadata", ROOT / "andrea/core/shared/column_metadata.py"
)
metadata = importlib.util.module_from_spec(metadata_spec)
sys.modules["_column_metadata"] = metadata
metadata_spec.loader.exec_module(metadata)
spec = importlib.util.spec_from_file_location("bixgboost_wrapper", SOURCE)
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


def write_tsv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(header)
        writer.writerows(rows)


@pytest.fixture
def inputs(tmp_path):
    extra = tmp_path / "extra"
    extra.mkdir()
    columns = [f"{unit}{point}" for unit in ("b", "a") for point in (2, 0, 3, 1)] + [
        "stationary"
    ]
    genes = ["0001", "TF_alpha", 'β,quote"']
    write_tsv(
        tmp_path / "expression.tsv",
        ["gene", *columns],
        [
            [
                gene,
                *[
                    (
                        (100 if column[0] == "b" else 0) + int(column[1]) * (index + 1)
                        if column != "stationary"
                        else 9000 + index
                    )
                    for column in columns
                ],
            ]
            for index, gene in enumerate(genes)
        ],
    )
    write_tsv(
        extra / "column_descriptors.tsv",
        metadata.TEMPORAL_COLUMNS,
        [
            (
                [
                    column,
                    "time_series",
                    column[0],
                    "condition-a",
                    column[0],
                    f"obs:{column}",
                    "technical-1",
                    int(column[1]) + (20 if column[0] == "b" else 0),
                    "hour",
                    "longitudinal",
                ]
                if column != "stationary"
                else [
                    column,
                    "steady_state",
                    "",
                    "condition-a",
                    "ss1",
                    f"obs:{column}",
                    "technical-1",
                    "",
                    "",
                    "",
                ]
            )
            for column in reversed(columns)
        ],
    )
    (tmp_path / "params.json").write_text("{}")
    (tmp_path / "execution.json").write_text('{"mode":"global"}')
    return tmp_path, extra, genes


def mutate_metadata(extra, change):
    path = extra / "column_descriptors.tsv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headers, rows = reader.fieldnames, list(reader)
    change(rows)
    write_tsv(path, headers, [[row[name] for name in headers] for row in rows])


def prepare(inputs, lag=2):
    root, extra, genes = inputs
    _, columns, expression = wrapper.read_matrix(root / "expression.tsv")
    return wrapper.prepare_series(expression, columns, extra, lag)


def test_native_parameter_surface_and_defaults_are_retained():
    assert wrapper.resolve_params({}) == {
        "p_lambda": 0,
        "p_alpha": 1,
        "timelag": 2,
        "seed": 0,
    }
    assert wrapper.FIXED_MODEL_PARAMS["n_estimators"] == 1000
    assert wrapper.FIXED_MODEL_PARAMS["importance_type"] == "weight"
    assert wrapper.FIXED_MODEL_PARAMS["learning_rate"] == 0.0001


@pytest.mark.parametrize(
    "params",
    [
        {"p_lambda": True},
        {"p_alpha": -1},
        {"p_lambda": float("nan")},
        {"timelag": 0},
        {"timelag": 1.2},
        {"seed": -1},
        {"seed": 2**31},
        {"samples": 2},
        {"maxlag": 2},
        {"n_estimators": 5},
        {"maxcount": "all"},
    ],
)
def test_invalid_or_nonpublic_parameters_reject(params):
    with pytest.raises(ValueError):
        wrapper.resolve_params(params)


def test_sort_series_without_crossing_boundaries_and_allow_shifted_origins(inputs):
    table, descriptors, resolved = prepare(inputs)
    assert table.shape == (8, 3)
    np.testing.assert_array_equal(table[:, 0], [0, 1, 2, 3, 100, 101, 102, 103])
    assert resolved["unused_steady_state_columns"] == ["stationary"]
    assert resolved["effective_training_rows"] == 4
    assert resolved["effective_predictor_columns"] == 6
    assert resolved["sampling_interval"] == 1
    assert [series["timepoints"] for series in resolved["series"]] == [
        [0, 1, 2, 3],
        [20, 21, 22, 23],
    ]


@pytest.mark.parametrize(
    "change,message",
    [
        (
            lambda rows: [
                row.update(timepoint="2.2") for row in rows if row["column"] == "a2"
            ],
            "regular",
        ),
        (
            lambda rows: [
                row.update(timepoint=str(float(row["timepoint"]) * 2))
                for row in rows
                if row["series_id"] == "b"
            ],
            "common sampling interval",
        ),
        (
            lambda rows: [
                row.update(sampling_design="independent_samples")
                for row in rows
                if row["observation_type"] == "time_series"
            ],
            "longitudinal",
        ),
        (
            lambda rows: [
                row.update(
                    observation_id="obs:a1",
                    technical_replicate="technical-2",
                    timepoint="1",
                )
                for row in rows
                if row["column"] == "a2"
            ],
            "technical replicates",
        ),
        (
            lambda rows: [
                row.update(replicate="different")
                for row in rows
                if row["column"] == "a1"
            ],
            "one condition and biological replicate",
        ),
    ],
)
def test_invalid_temporal_geometry_rejects_locally(inputs, change, message):
    mutate_metadata(inputs[1], change)
    with pytest.raises(ValueError, match=message):
        prepare(inputs)


def test_unequal_series_length_rejects(inputs):
    root, extra, _ = inputs
    mutate_metadata(
        extra,
        lambda rows: [
            row.update(
                observation_type="steady_state",
                series_id="",
                timepoint="",
                time_unit="",
                sampling_design="",
            )
            for row in rows
            if row["column"] == "b3"
        ],
    )
    with pytest.raises(ValueError, match="same number"):
        prepare(inputs)


def test_pooled_target_variance_and_lagged_rows_are_checked(inputs):
    root, extra, _ = inputs
    genes, columns, expression = wrapper.read_matrix(root / "expression.tsv")
    expression[0, :] = 1
    with pytest.raises(ValueError, match="positive pooled lagged variance"):
        wrapper.prepare_series(expression, columns, extra, 2)
    with pytest.raises(ValueError, match="more observed timepoints"):
        prepare(inputs, lag=4)


def test_native_float32_predictor_overflow_rejects_before_booster(inputs):
    root, extra, _ = inputs
    _, columns, expression = wrapper.read_matrix(root / "expression.tsv")
    expression[0, :] *= 1e40
    with pytest.raises(ValueError, match="native XGBoost float32 precision"):
        wrapper.prepare_series(expression, columns, extra, 2)


def test_patch_is_source_guarded_and_uses_native_importance(tmp_path):
    if not UPSTREAM.is_file():
        pytest.skip("Optional user-provided upstream clone is not available")
    raw = UPSTREAM.read_bytes()
    source = wrapper.patched_source(raw)
    assert source.count("nthread=andrea_threads") == 2
    assert source.count(".feature_importances_") == 2
    assert "importance_type=" not in source
    assert "res_col.append" not in source
    with pytest.raises(ValueError, match="pinned SHA-256"):
        wrapper.patched_source(raw + b"\n")
    receipt = wrapper.prepare_upstream(
        output_dir=tmp_path / "prepared", source=UPSTREAM
    )
    assert receipt["original_source_sha256"] == wrapper.UPSTREAM_SHA256
    assert (tmp_path / "prepared/BiXGBoost.original.py").read_bytes() == raw


def test_xgboost_preparation_keeps_original_engine_and_exact_weight_property(tmp_path):
    archives = ROOT / "release-logs/inference-bixgboost/xgboost060-proof"
    if not archives.is_dir():
        pytest.skip(
            "Downloaded original source archives belong to ignored build evidence"
        )
    receipt = wrapper.prepare_xgboost(output_dir=tmp_path, archives_dir=archives)
    original_sklearn = tmp_path / "andrea-original-python/sklearn.py"
    patched_sklearn = tmp_path / "python-package/xgboost/sklearn.py"
    expected_append = "\n# ANDREA: share the original v0.60 weight property with its regressor.\nXGBRegressor.feature_importances_ = XGBClassifier.feature_importances_\n"
    assert patched_sklearn.read_text() == original_sklearn.read_text() + expected_append
    assert (tmp_path / "python-package/xgboost/VERSION").read_text().strip() == "0.6"
    assert (tmp_path / "src/gbm/gbtree.cc").is_file()
    assert receipt["xgboost_commit"] == wrapper.XGBOOST_COMMIT
    assert (
        "sklearn.model_selection"
        in (tmp_path / "python-package/xgboost/compat.py").read_text()
    )
    assert (
        "collections.abc.Mapping"
        in (tmp_path / "python-package/xgboost/core.py").read_text()
    )


def test_xgboost_archive_guard_runs_before_extraction(tmp_path):
    archive_name = f"xgboost-{wrapper.XGBOOST_COMMIT}.tar.gz"
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    (archive_dir / archive_name).write_bytes(b"incorrect source archive")
    with pytest.raises(ValueError, match="pinned SHA-256"):
        wrapper.prepare_xgboost(
            output_dir=tmp_path / "source", archives_dir=archive_dir
        )
    assert not list((tmp_path / "source").rglob("*.py"))


def test_export_retains_scores_and_rejects_degenerate_native_outputs(tmp_path):
    native = pd.DataFrame({"total": [0.25, 0.0]}, index=["g0\tg1", "g1\tg0"])
    count = wrapper.export_network(
        tmp_path / "network.csv", native, ["A_with_underscore", "0002"], ["g0", "g1"]
    )
    assert count == 1
    with (tmp_path / "network.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0] == {
        "source": "A_with_underscore",
        "target": "0002",
        "score": "0.25",
        "sign": "?",
        "evidence": "association",
        "context": "global",
    }
    native.loc["g0\tg1", "total"] = float("nan")
    with pytest.raises(ValueError, match="finite and nonnegative"):
        wrapper.export_network(
            tmp_path / "network.csv", native, ["A", "B"], ["g0", "g1"]
        )


class RecordingRegressor:
    fitted = []
    fail_at = None

    def __init__(self, **kwargs):
        self.params = kwargs

    def fit(self, x, y):
        if len(self.fitted) == self.fail_at:
            raise RuntimeError("native fit failed")
        self.fitted.append((x.copy(), np.array(y), self.params.copy()))
        weights = np.arange(1, x.shape[1] + 1, dtype=np.float32)
        self.feature_importances_ = weights / weights.sum()
        return self

    def predict(self, x):
        return np.zeros(len(x))

    def booster(self):
        return SimpleNamespace(get_dump=lambda: ["tree"] * self.params["n_estimators"])

    def get_params(self, deep=False):
        return {**self.params, "missing": np.nan}


@pytest.fixture
def implementation(monkeypatch):
    if not UPSTREAM.is_file():
        pytest.skip("Optional user-provided upstream clone is not available")
    RecordingRegressor.fitted = []
    RecordingRegressor.fail_at = None
    module = ModuleType("BiXGBoost_recording")
    monkeypatch.setitem(
        sys.modules, "xgboost", SimpleNamespace(XGBRegressor=RecordingRegressor)
    )
    monkeypatch.setitem(sys.modules, "sklearn", ModuleType("sklearn"))
    monkeypatch.setitem(
        sys.modules,
        "sklearn.metrics",
        SimpleNamespace(
            mean_squared_error=lambda prediction, observed: float(
                np.mean((prediction - observed) ** 2)
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "threadpoolctl",
        SimpleNamespace(
            threadpool_info=lambda: [], threadpool_limits=lambda **kwargs: nullcontext()
        ),
    )
    exec(
        compile(wrapper.patched_source(UPSTREAM.read_bytes()), "BiXGBoost.py", "exec"),
        module.__dict__,
    )
    return module


def run_recording(inputs, implementation):
    root, extra, _ = inputs
    return wrapper.run(
        input_path=root / "expression.tsv",
        params_path=root / "params.json",
        extra_dir=extra,
        output_dir=root / "out",
        threads=2,
        upstream=implementation,
    )


def test_native_fits_progress_resources_scores_and_opaque_identifiers(
    inputs, implementation
):
    root, extra, genes = inputs
    resolved = run_recording(inputs, implementation)
    assert len(RecordingRegressor.fitted) == 2 * len(genes)
    assert all(x.shape == (4, 6) for x, _, _ in RecordingRegressor.fitted)
    for x, y, params in RecordingRegressor.fitted:
        np.testing.assert_allclose(np.mean(y), 0, atol=1e-12)
        np.testing.assert_allclose(np.std(y), 1, atol=1e-12)
        assert params == {
            "max_depth": 3,
            "n_estimators": 1000,
            "learning_rate": 0.0001,
            "subsample": 0.8,
            "reg_lambda": 0,
            "reg_alpha": 1,
            "colsample_bylevel": 0.6,
            "colsample_bytree": 0.6,
            "seed": 0,
            "nthread": 2,
        }
    assert list(RecordingRegressor.fitted[0][0].iloc[:, 0]) == [0, 1, 100, 101]
    assert list(RecordingRegressor.fitted[1][0].iloc[:, 0]) == [1, 2, 101, 102]
    events = [
        json.loads(line)
        for line in (root / "out/provenance/progress-events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [
        event["completed"] for event in events if event["phase"] == "inference"
    ] == list(range(7))
    assert events[-1]["status"] == "completed"
    network = pd.read_csv(
        root / "out/network.csv", dtype={"source": str, "target": str}
    )
    assert set(network["source"]) == set(genes)
    assert set(network["target"]) == set(genes)
    assert len(network) == 6
    assert (network["score"] > 0).all()
    scores = pd.read_csv(root / "out/provenance/bidirectional-scores.tsv", sep="\t")
    aliases = resolved["gene_aliases"]
    canonical = {(row.source, row.target): row.score for row in network.itertuples()}
    for row in scores.itertuples(index=False, name=None):
        source, target = row[0].split("\t")
        assert canonical[aliases[source], aliases[target]] == pytest.approx(
            row[-1], rel=1e-14
        )
    model = json.loads((root / "out/provenance/model-00000000-in.json").read_text())
    assert model["observed_tree_count"] == 1000
    assert model["model_params"]["nthread"] == 2
    assert model["model_params"]["missing"] == {"native_nonfinite_float": "NaN"}
    assert resolved["unused_steady_state_columns"] == ["stationary"]


def test_failure_never_marks_completion_and_keeps_observed_fit_count(
    inputs, implementation
):
    root, _, _ = inputs
    RecordingRegressor.fail_at = 2
    with pytest.raises(RuntimeError, match="native fit failed"):
        run_recording(inputs, implementation)
    progress = json.loads((root / "out/progress.json").read_text())
    assert progress["status"] == "failed"
    assert progress["completed"] == 2
    assert progress["total"] == 6
    assert progress["percent"] < 100
    events = [
        json.loads(line)
        for line in (root / "out/provenance/progress-events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert events[-1]["status"] == "failed"


def test_export_failure_keeps_last_observed_progress(
    inputs, implementation, monkeypatch
):
    root, _, _ = inputs

    def invalid_export(*args, **kwargs):
        raise ValueError("nonfinite native scores")

    monkeypatch.setattr(wrapper, "export_network", invalid_export)
    with pytest.raises(ValueError, match="nonfinite native scores"):
        run_recording(inputs, implementation)
    events = [
        json.loads(line)
        for line in (root / "out/provenance/progress-events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert events[-2]["phase"] == "export" and events[-2]["percent"] == 95
    assert events[-1]["status"] == "failed" and events[-1]["percent"] == 95
    assert [event["percent"] for event in events] == sorted(
        event["percent"] for event in events
    )


def test_native_bidirectional_weighting_and_max_lag_are_not_reinterpreted(
    implementation,
):
    incoming = [{"A\tB": 0.6}, {"B\tA": 0.9}, {"C\tA": 0.3}]
    outgoing = [{"A\tB": 0.5}, {"B\tA": 0.8}, {"C\tA": 0.2}]
    scored = implementation.compute_feature_importances(
        [1.0, 2.0, 3.0], [3.0, 2.0, 1.0], incoming, outgoing
    )
    expected = np.sqrt(0.6 * (1 - 1 / 6) * 0.5 * (1 - 3 / 6))
    assert scored.loc["A\tB", "total"] == pytest.approx(expected)
    linked = implementation.getlinks("A", ["B_2", "B_1", "A_1"], [0.2, 0.7, 0.9])
    assert linked == {"B\tA": 0.7}
