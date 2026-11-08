"""SWING contracts and compatibility edits, without Docker or scientific fitting."""

from __future__ import annotations

import ast
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import warnings

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "wrappers/inference_tools/tools/swing"
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts/templates/python"))
metadata_spec = importlib.util.spec_from_file_location(
    "_column_metadata", ROOT / "andrea/core/shared/column_metadata.py"
)
metadata = importlib.util.module_from_spec(metadata_spec)
sys.modules["_column_metadata"] = metadata
metadata_spec.loader.exec_module(metadata)
spec = importlib.util.spec_from_file_location("swing_wrapper", SOURCE / "run_tool.py")
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


@pytest.fixture(autouse=True)
def record_native_pool_contract(monkeypatch):
    # The host test environment deliberately lacks the scientific container's
    # dependencies. No model or native pool is created by these recording tests.
    monkeypatch.setitem(
        sys.modules,
        "threadpoolctl",
        SimpleNamespace(
            threadpool_limits=lambda **kwargs: nullcontext(), threadpool_info=lambda: []
        ),
    )


def write_tsv(path, headings, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(headings)
        writer.writerows(rows)


def mutate_tsv(path, change):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headings, rows = reader.fieldnames, list(reader)
    change(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headings, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def inputs(tmp_path):
    extra = tmp_path / "extra"
    extra.mkdir()
    # Expression and column descriptors use different orders. The biological
    # trajectories have deliberately separated ranges to reveal boundary errors.
    columns = [f"{unit}{time}" for unit in ("b", "a") for time in (2, 0, 4, 1, 3)]
    genes = ["Time", "0001", 'TF:β,quote"', "__andrea_time__"]
    rows = [
        [
            gene,
            *[
                (1000 if column[0] == "b" else 0) + int(column[1]) * (index + 1)
                for column in columns
            ],
        ]
        for index, gene in enumerate(genes)
    ]
    write_tsv(tmp_path / "expression.tsv", ["gene", *columns], rows)
    write_tsv(
        extra / "column_descriptors.tsv",
        [
            "column",
            "observation_type",
            "series_id",
            "condition",
            "replicate",
            "observation_id",
            "technical_replicate",
            "timepoint",
            "time_unit",
            "sampling_design",
        ],
        [
            [
                column,
                "time_series",
                column[0],
                "control",
                column[0],
                f"observation:{column}",
                "measurement-1",
                int(column[1]),
                "hour",
                "longitudinal",
            ]
            for column in reversed(columns)
        ],
    )
    temporal = wrapper.summarize_temporal_metadata(
        wrapper.load_bulk_column_descriptors(extra / "column_descriptors.tsv", columns)
    )
    (tmp_path / "params.json").write_text("{}")
    (tmp_path / "execution.json").write_text(json.dumps({"mode": "global"}))
    return tmp_path, extra, temporal


def test_defaults_are_constructor_and_pipeline_defaults_not_demo_speedups():
    params = wrapper.resolve_params({})
    assert params["window_type"] == "RandomForest"
    assert (
        params["window_width"],
        params["min_lag"],
        params["max_lag"],
        params["step_size"],
    ) == (3, 0, 0, 1)
    assert params["n_trees"] == 500
    assert params["zscore"] is True
    assert (params["n_bootstraps"], params["n_alphas"], params["noise"]) == (
        10,
        20,
        0.2,
    )
    assert params["n_permutations"] == 0
    catalog_path = ROOT / "andrea/catalog_inference_tools/tools/swing/toolspec.json"
    if catalog_path.is_file():
        catalog = json.loads(catalog_path.read_text())
        assert wrapper.DEFAULTS == {
            name: value["default"] for name, value in catalog["params"].items()
        }


def test_progress_reports_initialization_and_zero_completed_before_the_first_fit(
    inputs, monkeypatch
):
    root, _, _ = inputs
    events = []
    original_progress = wrapper.write_progress
    original_init = RecordingSwing.__init__
    original_fit = RecordingWindow.fit_window

    def record(path, **event):
        events.append(dict(event))
        return original_progress(path, **event)

    def initialize(self, *args, **kwargs):
        payload = json.loads((root / "out/progress.json").read_text())
        assert payload["phase"] == "initializing"
        original_init(self, *args, **kwargs)

    def fit(self):
        payload = json.loads((root / "out/progress.json").read_text())
        assert payload["phase"] == "inference"
        if self.nth_window == 0:
            assert payload["completed"] == 0 and payload["total"] == 3
        return original_fit(self)

    monkeypatch.setattr(wrapper, "write_progress", record)
    monkeypatch.setattr(RecordingSwing, "__init__", initialize)
    monkeypatch.setattr(RecordingWindow, "fit_window", fit)
    run_fake(inputs)
    assert [event["phase"] for event in events] == [
        "validate",
        "initializing",
        "inference",
        "inference",
        "inference",
        "inference",
        "aggregation",
        "export",
        "done",
    ]
    assert [
        event["completed"] for event in events if event["phase"] == "inference"
    ] == [0, 1, 2, 3]
    assert [event["percent"] for event in events] == sorted(
        event["percent"] for event in events
    )
    retained = [
        json.loads(line)
        for line in (root / "out/provenance/progress-events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [event["phase"] for event in retained] == [
        event["phase"] for event in events
    ]
    assert all(
        type(event["timestamp"]) in (int, float) and event["timestamp"] > 0
        for event in retained
    )
    assert all(
        a["observed_monotonic_ns"] < b["observed_monotonic_ns"]
        for a, b in zip(retained, retained[1:])
    )
    assert {
        key: value
        for key, value in retained[-1].items()
        if key != "observed_monotonic_ns"
    } == json.loads((root / "out/progress.json").read_text())


@pytest.mark.parametrize("epsilon,accepted", [(5e-10, True), (3e-9, False)])
def test_regularity_tolerance_matches_canonical_core_policy(inputs, epsilon, accepted):
    root, extra, temporal = inputs
    mutate_tsv(
        extra / "column_descriptors.tsv",
        lambda rows: [
            row.update(
                timepoint=str(
                    float(row["timepoint"])
                    + (epsilon if float(row["timepoint"]) >= 2 else 0)
                )
            )
            for row in rows
        ],
    )
    _, columns, values = wrapper.read_matrix(root / "expression.tsv")
    if accepted:
        wrapper.prepare_series(values, columns, extra)
    else:
        with pytest.raises(ValueError, match="regular observed"):
            wrapper.prepare_series(values, columns, extra)


def test_two_observed_times_with_width_two_match_the_declared_minimum(inputs):
    root, extra, temporal = inputs
    with (root / "expression.tsv").open() as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    selected = [
        index
        for index, column in enumerate(rows[0])
        if index == 0 or column in {"a0", "a1", "b0", "b1"}
    ]
    write_tsv(
        root / "expression.tsv",
        [rows[0][index] for index in selected],
        [[row[index] for index in selected] for row in rows[1:]],
    )
    for name in ("column_descriptors.tsv",):
        mutate_tsv(
            extra / name,
            lambda metadata: metadata.__setitem__(
                slice(None),
                [row for row in metadata if row["column"] in {"a0", "a1", "b0", "b1"}],
            ),
        )
    _, resolved, _ = run_fake(inputs, {"window_width": 2})
    assert resolved["dimensions"]["fitted_windows"] == 1
    toolspec = json.loads(
        (ROOT / "andrea/catalog_inference_tools/tools/swing/toolspec.json").read_text()
    )
    assert toolspec["temporal_requirements"]["min_timepoints_per_series"] == 2


def test_forest_observer_separates_requested_configured_and_executing_workers_without_fitting():
    barrier = threading.Barrier(2)
    calls = []

    def build_tree(index, payload):
        if index < 2:
            barrier.wait(timeout=5)
        calls.append((index, payload))
        return index * 2

    module = SimpleNamespace(_parallel_build_trees=build_tree)

    class Forest:
        n_jobs = 2
        estimators_ = [object(), object(), object()]

        def get_params(self, deep=False):
            return {"n_estimators": 3, "n_jobs": 2, "max_features": "sqrt"}

    observer = wrapper._ForestExecutionObserver(2, 3, forest_module=module)
    before = np.random.get_state()
    forest = Forest()
    payload = object()
    with observer:
        observer.set_phase(4, "fit")
        observer.estimator("constructed", forest, 0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(
                pool.map(
                    lambda index: module._parallel_build_trees(index, payload), range(3)
                )
            )
        observer.estimator("fitted", forest, 0)
    after = np.random.get_state()
    assert result == [0, 2, 4]
    assert {index for index, value in calls if value is payload} == {0, 1, 2}
    assert module._parallel_build_trees is build_tree
    assert before[0] == after[0] and before[2:] == after[2:]
    np.testing.assert_array_equal(before[1], after[1])
    receipt = observer.receipt()
    assert receipt["requested"] == {"n_jobs": 2, "n_estimators": 3}
    assert (
        receipt["models"][0]["constructed_n_jobs"]
        == receipt["models"][0]["fitted_n_jobs"]
        == 2
    )
    assert (
        receipt["models"][0]["fitted_tree_count"]
        == receipt["models"][0]["observed_tree_tasks"]
        == 3
    )
    assert receipt["observed_joblib"]["max_concurrent_tree_tasks"] == 2
    assert len(receipt["observed_joblib"]["worker_threads"]) == 2
    assert receipt["observed_joblib"]["tree_tasks"] == 3
    assert "not CPU utilization" in receipt["observed_joblib"]["semantics"]


def test_forest_observer_restores_callable_after_an_underlying_error():
    def fail(*args, **kwargs):
        raise RuntimeError("original tree failure")

    module = SimpleNamespace(_parallel_build_trees=fail)
    observer = wrapper._ForestExecutionObserver(1, 1, forest_module=module)
    with pytest.raises(RuntimeError, match="original tree failure"):
        with observer:
            module._parallel_build_trees("same argument", untouched=True)
    assert module._parallel_build_trees is fail
    assert observer.active == 0


def test_forest_observer_rejects_configuration_that_only_echoes_requested_jobs():
    observer = wrapper._ForestExecutionObserver(4, 3, inspect_backend=False)
    forest = SimpleNamespace(
        get_params=lambda deep=False: {"n_jobs": 1, "n_estimators": 3}
    )
    with pytest.raises(ValueError, match="Actual forest constructor"):
        observer.estimator("constructed", forest, 0)


@pytest.mark.parametrize(
    "raw",
    [
        {"window_width": 0},
        {"window_width": True},
        {"min_lag": -1},
        {"max_lag": None},
        {"min_lag": 2, "max_lag": 1},
        {"step_size": 2},
        {"zscore": 1},
        {"n_trees": 0},
        {"window_type": "PLSR"},
        {"n_permutations": 1},
        {"seed": -1},
        {"seed": 2**32},
        {"seed": True},
        {"lag_method": "sum_mean"},
        {"lag_method": "__import___mean"},
        {"sort_by": "p_value"},
        {"maxcount": -1},
        {"maxcount": True},
        {"unknown": 1},
        {"alpha": 1},
        {"pcs": 2},
        {"window_type": "Lasso", "alpha": -0.1},
        {"window_type": "Lasso", "alpha": float("nan")},
        {"window_type": "Lasso", "noise": 1.1},
        {"window_type": "Lasso", "n_bootstraps": 0},
        {"window_type": "Lasso", "n_alphas": 1},
        {"window_type": "Lasso", "null_alpha": 0},
        {"window_type": "Lasso", "alpha_grid": [1, 0]},
        {"window_type": "Lasso", "alpha_grid": [0, 0]},
        {"window_type": "Lasso", "alpha_grid": [0, True]},
        {"window_type": "Lasso", "alpha_cv_method": "random"},
        {"window_type": "Dionesus", "pcs": 0},
        {"window_type": "Lasso", "n_trees": 2},
    ],
)
def test_invalid_and_inactive_parameters_fail_before_upstream(raw):
    with pytest.raises(ValueError):
        wrapper.resolve_params(raw)


def test_exact_id_join_preserves_independent_complete_grids(inputs):
    root, extra, temporal = inputs
    genes, columns, matrix = wrapper.read_matrix(root / "expression.tsv")
    blocks, times, series = wrapper.prepare_series(matrix, columns, extra)
    assert genes == ["Time", "0001", 'TF:β,quote"', "__andrea_time__"]
    np.testing.assert_array_equal(times, [0, 1, 2, 3, 4])
    np.testing.assert_array_equal(blocks[0][:, 0], [0, 1, 2, 3, 4])
    np.testing.assert_array_equal(blocks[1][:, 0], [1000, 1001, 1002, 1003, 1004])
    assert [entry["columns"] for entry in series] == [
        [f"a{i}" for i in range(5)],
        [f"b{i}" for i in range(5)],
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda rows: rows[0].update(column="unknown"),
        lambda rows: rows[0].update(timepoint="nan"),
        lambda rows: rows[0].update(time_unit="minute"),
        lambda rows: rows[0].update(sampling_design=""),
        lambda rows: rows[0].update(sampling_design="independent_samples"),
        lambda rows: [
            row.update(sampling_design="independent_samples") for row in rows
        ],
        lambda rows: rows[0].update(timepoint="1.2"),
        lambda rows: rows[0].update(condition="another"),
        lambda rows: rows[0].update(observation_id=rows[1]["observation_id"]),
    ],
)
def test_bad_temporal_metadata_is_rejected(inputs, mutation):
    root, extra, temporal = inputs
    mutate_tsv(extra / "column_descriptors.tsv", mutation)
    _, columns, matrix = wrapper.read_matrix(root / "expression.tsv")
    with pytest.raises(ValueError):
        wrapper.prepare_series(matrix, columns, extra)


def test_regular_but_different_series_grids_are_rejected(inputs):
    root, extra, temporal = inputs
    mutate_tsv(
        extra / "column_descriptors.tsv",
        lambda rows: [
            row.update(timepoint=str(int(row["timepoint"]) * 2))
            for row in rows
            if row["series_id"] == "a"
        ],
    )
    _, columns, matrix = wrapper.read_matrix(root / "expression.tsv")
    with pytest.raises(ValueError, match="identical complete"):
        wrapper.prepare_series(matrix, columns, extra)


class RecordingWindow:
    def __init__(self, roller, index):
        self.roller, self.nth_window = roller, index
        self.cv_table = None
        self.alpha, self.null_alpha, self.num_pcs = None, None, None

    def initialize_params(self, **kwargs):
        self.roller.calls.append(("initialize", self.nth_window, kwargs))
        self.n_trees = kwargs.get("n_trees", 500)
        self.alpha = kwargs.get("alpha", 0.1)
        self.num_pcs = 2

    def get_null_alpha(self, **kwargs):
        self.roller.calls.append(("null_alpha", kwargs))
        return 1.0

    def cv_select_alpha(self, **kwargs):
        self.roller.calls.append(("cv", kwargs))
        if self.null_alpha is None:
            self.null_alpha = self.get_null_alpha()
        return 0.25, pd.DataFrame({"alpha": [0.25], "Model_Q^2": [0.5]})

    def fit_window(self):
        # Consumes randomness exactly to assert the wrapper seeds each phase.
        self.roller.calls.append(
            (
                "fit",
                self.nth_window,
                int(np.random.randint(2**31)),
                getattr(self, "n_jobs", None),
                self.alpha,
                self.num_pcs,
            )
        )

    def run_bootstrap(self, **kwargs):
        self.roller.calls.append(("bootstrap", self.nth_window, kwargs))
        if self.null_alpha is None:
            self.null_alpha = self.get_null_alpha()

    def run_permutation_test(self, **kwargs):
        self.roller.calls.append(("permutation", self.nth_window, kwargs))

    def make_edge_table(self):
        labels = self.roller.labels
        table = pd.DataFrame(
            {
                "Parent": [labels[0], labels[1]],
                "Child": [labels[1], labels[2]],
                "Importance": [0.7, 0.3],
                "P_window": [0, 0],
                "C_window": [self.nth_window, self.nth_window],
            }
        )
        if self.roller.kwargs["window_type"] == "Lasso":
            table["Stability"] = [0.2, 0.6]
        return table


class RecordingSwing:
    instances = []

    def __init__(self, path, **kwargs):
        self.path, self.kwargs, self.calls = Path(path), kwargs, []
        self.raw_data = pd.read_csv(path, sep="\t", float_precision="round_trip")
        self.norm_data = self.raw_data.copy()
        self.labels = self.raw_data.columns.tolist()[1:]
        self.tf_list = None
        self.instances.append(self)

    def zscore_all_data(self):
        self.calls.append(("zscore",))

    def create_windows(self):
        self.calls.append(("create",))
        ntime = self.raw_data[self.kwargs["time_label"]].nunique()
        self.window_list = [
            RecordingWindow(self, index)
            for index in range(
                self.kwargs["max_lag"], ntime - self.kwargs["window_width"] + 1
            )
        ]

    def create_custom_windows(self, tf_list):
        self.tf_list = tf_list
        self.calls.append(("custom", tf_list))
        self.create_windows()

    def compile_edges(self, **kwargs):
        self.calls.append(("compile", kwargs))
        self.full_edge_list = pd.concat(
            [window.make_edge_table() for window in self.window_list], ignore_index=True
        )

    def make_static_edge_dict(self, **kwargs):
        self.calls.append(("aggregate", kwargs))
        self.edge_dict = {
            (self.labels[0], self.labels[1]): {
                "mean_importance": 0.7,
                "max_importance": 0.7,
                "lag_importance": 0.7,
                "rank_importance": 0,
                "adj_importance": 0.2,
                "dataframe": self.full_edge_list,
            }
        }

    def make_sort_df(self, edge_dict, **kwargs):
        self.calls.append(("sort", kwargs))
        return pd.DataFrame(
            {"regulator-target": list(edge_dict), "mean_importance": [0.7]}
        )


def run_fake(inputs, params=None, threads=2, suffix="out"):
    root, extra, _ = inputs
    (root / "params.json").write_text(json.dumps(params or {}))
    resolved = wrapper.run(
        input_path=root / "expression.tsv",
        params_path=root / "params.json",
        extra_dir=extra,
        output_dir=root / suffix,
        threads=threads,
        upstream=RecordingSwing,
    )
    return root, resolved, RecordingSwing.instances[-1]


def test_width_one_pools_independent_trajectories_without_changing_defaults(inputs):
    _, resolved, upstream = run_fake(inputs, {"window_width": 1, "n_trees": 3})
    assert upstream.kwargs["window_width"] == 1
    assert resolved["dimensions"]["rows_per_window"] == 2
    assert resolved["dimensions"]["fitted_windows"] == 5
    assert wrapper.resolve_params({})["window_width"] == 3


@pytest.mark.parametrize(
    "raw,nseries,error",
    [
        ({"window_type": "Lasso", "alpha_cv_folds": 2}, 3, "two test observations"),
        ({"window_type": "Dionesus", "pcs": 1}, 1, "pcs exceeds"),
        ({"window_type": "Dionesus"}, 2, "automatic PLS"),
    ],
)
def test_width_one_keeps_backend_observation_constraints(raw, nseries, error):
    params = wrapper.resolve_params({**raw, "window_width": 1})
    with pytest.raises(ValueError, match=error):
        wrapper._validate_dimensions(params, 4, nseries, 5, 4)


def test_steady_state_columns_are_excluded_before_normalization_and_fitting(inputs):
    root, extra, _ = inputs
    with (root / "expression.tsv").open(newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    rows[0].append("steady:unrelated")
    for row in rows[1:]:
        row.append("1e308")
    write_tsv(root / "expression.tsv", rows[0], rows[1:])
    mutate_tsv(
        extra / "column_descriptors.tsv",
        lambda metadata: metadata.append(
            dict(
                column="steady:unrelated",
                observation_type="steady_state",
                series_id="",
                condition="other",
                replicate="ss1",
                observation_id="ss1",
                technical_replicate="tech1",
                timepoint="",
                time_unit="",
                sampling_design="",
            )
        ),
    )
    _, resolved, upstream = run_fake(inputs)
    assert resolved["unused_steady_state_columns"] == ["steady:unrelated"]
    assert len(resolved["time_series_columns"]) == 10
    assert len(upstream.raw_data) == 10
    assert np.isfinite(upstream.raw_data.to_numpy()).all()
    assert upstream.raw_data.iloc[:, 1:].to_numpy().max() < 1e4


def test_recording_forest_receives_options_ids_resources_and_replica_rows(inputs):
    root, resolved, upstream = run_fake(
        inputs,
        {
            "window_width": 3,
            "min_lag": 1,
            "max_lag": 1,
            "n_trees": 7,
            "seed": 91,
            "n_permutations": 2,
        },
    )
    assert upstream.kwargs == {
        "gene_start": 1,
        "gene_end": 5,
        "time_label": "__andrea_time__",
        "separator": "\t",
        "window_type": "RandomForest",
        "step_size": 1,
        "min_lag": 1,
        "max_lag": 1,
        "window_width": 3,
    }
    assert upstream.raw_data["__andrea_time__"].tolist() == [0, 1, 2, 3, 4] * 2
    assert upstream.raw_data["__andrea_gene_000000"].tolist() == [
        0,
        1,
        2,
        3,
        4,
        1000,
        1001,
        1002,
        1003,
        1004,
    ]
    assert ("initialize", 1, {"n_trees": 7}) in upstream.calls
    assert ("permutation", 1, {"n_permutations": 2, "n_jobs": 2}) in upstream.calls
    assert (
        "aggregate",
        {"self_edges": False, "lag_method": "mean_mean"},
    ) in upstream.calls
    assert resolved["dimensions"]["fitted_windows"] == 2
    with (root / "out/network.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {
            "source": "Time",
            "target": "0001",
            "score": "0.7",
            "sign": "?",
            "evidence": "association",
            "context": "global",
        }
    ]
    assert json.loads((root / "out/progress.json").read_text())["status"] == "completed"


def test_default_seed_is_reproducible_and_resources_do_not_change_it(inputs):
    _, _, first = run_fake(inputs, {"seed": 3}, threads=1)
    _, _, second = run_fake(inputs, {"seed": 3}, threads=8, suffix="out2")
    assert [(call[1], call[2]) for call in first.calls if call[0] == "fit"] == [
        (call[1], call[2]) for call in second.calls if call[0] == "fit"
    ]
    _, _, third = run_fake(inputs, {"seed": 4}, suffix="out3")
    assert [call[2] for call in first.calls if call[0] == "fit"] != [
        call[2] for call in third.calls if call[0] == "fit"
    ]


def test_lasso_fits_requested_alpha_and_runs_stability_with_exact_options(inputs):
    _, resolved, upstream = run_fake(
        inputs,
        {
            "window_type": "Lasso",
            "alpha": 0.12,
            "null_alpha": 2.0,
            "n_bootstraps": 1,
            "n_alphas": 3,
            "noise": 0.05,
        },
    )
    assert ("initialize", 0, {"alpha": 0.12}) in upstream.calls
    assert (
        "bootstrap",
        0,
        {"n_bootstraps": 1, "n_alphas": 3, "noise": 0.05},
    ) in upstream.calls
    assert all(
        item["alpha"] == 0.12 and item["null_alpha"] == 2
        for item in resolved["windows"]
    )


def test_lasso_auto_cv_passes_fold_grid_and_method_without_capping(inputs):
    _, resolved, upstream = run_fake(
        inputs,
        {
            "window_type": "Lasso",
            "alpha_cv_folds": 2,
            "alpha_cv_method": "max_posQ2",
            "alpha_grid": [0.01, 0.1, 1.0],
            "null_alpha": 2,
        },
    )
    cv = next(call for call in upstream.calls if call[0] == "cv")
    assert cv[1]["n_folds"] == 2
    assert cv[1]["method"] == "max_posQ2"
    np.testing.assert_array_equal(cv[1]["alpha_range"], [0.01, 0.1, 1.0])
    assert all(item["alpha"] == 0.25 for item in resolved["windows"])


def test_lasso_invalid_cv_and_subset_fail_with_failed_progress(inputs):
    root, extra, _ = inputs
    with pytest.raises(ValueError, match="two test observations"):
        run_fake(inputs, {"window_type": "Lasso"})
    assert json.loads((root / "out/progress.json").read_text())["status"] == "failed"
    (extra / "tf_list.txt").write_text("Time\n0001\n")
    with pytest.raises(ValueError, match="restricted TF subset"):
        run_fake(inputs, {"window_type": "Lasso", "alpha": 0.1})


def test_pls_pcs_and_tf_candidates_are_forwarded_as_inputs(inputs):
    root, extra, _ = inputs
    (extra / "tf_list.txt").write_text("Time\n0001\n")
    _, _, upstream = run_fake(inputs, {"window_type": "Dionesus", "pcs": 1})
    assert (
        "custom",
        ["__andrea_gene_000000", "__andrea_gene_000001"],
    ) in upstream.calls
    assert all(call[5] == 1 for call in upstream.calls if call[0] == "fit")


@pytest.mark.parametrize(
    "sort_by,expected",
    [
        ("mean", "0.75"),
        ("max", "0.75"),
        ("lag", "0.75"),
        ("adj", "0.75"),
        ("rank", "0.25"),
    ],
)
def test_scores_keep_native_magnitudes_and_rank_direction(tmp_path, sort_by, expected):
    stats = {name + "_importance": 0.75 for name in ("mean", "max", "lag", "adj")}
    stats.update(rank_importance=3, dataframe=object())
    destination = tmp_path / "network.csv"
    assert (
        wrapper.export_network(
            destination, {("a", "b"): stats}, {"a": "001", "b": "TF,β"}, sort_by, "all"
        )
        == 1
    )
    with destination.open() as handle:
        row = next(csv.DictReader(handle))
    assert (row["source"], row["target"], row["score"]) == ("001", "TF,β", expected)
    assert row["sign"] == "?"


def test_absent_rank_is_not_promoted_to_high_score_and_export_limit_is_postfit(
    tmp_path,
):
    path = tmp_path / "network.csv"
    assert (
        wrapper.export_network(
            path,
            {("a", "b"): {"rank_importance": float("nan"), "dataframe": None}},
            {"a": "a", "b": "b"},
            "rank",
            "all",
        )
        == 0
    )
    assert (
        wrapper.export_network(
            path,
            {("a", "b"): {"mean_importance": 0.3}},
            {"a": "a", "b": "b"},
            "mean",
            0,
        )
        == 0
    )


def _raw_source_dir():
    path = SOURCE / "repo/SWING"
    if not (path / "Swing/Swing.py").is_file():
        pytest.skip("Optional user-provided upstream clone is not available")
    return path


@pytest.fixture
def patched(tmp_path):
    source = _raw_source_dir()
    for relative, digest in wrapper.UPSTREAM_FILES.items():
        data = (source / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        destination = tmp_path / relative
        destination.parent.mkdir(exist_ok=True, parents=True)
        destination.write_bytes(data)
    wrapper.patch_upstream_sources(tmp_path)
    return tmp_path


def _class_from_source(path, name, namespace):
    parsed = ast.parse(path.read_text())
    cls = next(
        node
        for node in parsed.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def test_patch_preimages_and_changed_contexts_are_hard_guarded(patched):
    with pytest.raises(ValueError, match="SHA-256"):
        wrapper.patch_upstream_sources(patched)
    swing = (patched / "Swing/Swing.py").read_text()
    assert "np.isin(explanatory_dict['explanatory_labels'], tf_list)" in swing
    assert "df = df.append" not in swing
    assert 'float_precision="round_trip"' in swing
    # The scientifically controversial positive filter remains identical.
    assert "df = df[df['Importance'] > 0]" in swing


def test_real_window_preparation_preserves_series_boundaries_and_all_tf_lags(
    patched, inputs
):
    _, _, recording = run_fake(inputs, {"min_lag": 1, "max_lag": 2, "window_width": 2})
    ns = {"np": np, "pd": pd, "random": __import__("random")}

    def edges(parents, children, self_edges=True):
        return [
            (parent, child)
            for child in children
            for parent in parents
            if self_edges or parent != child
        ]

    ns["util"] = SimpleNamespace(make_possible_edge_list=edges)
    ns["Window"] = _class_from_source(patched / "Swing/Window.py", "Window", ns)
    ns["RandomForestRegressionWindow"] = _class_from_source(
        patched / "Swing/RFRWindow.py", "RandomForestRegressionWindow", ns
    )
    swing_cls = _class_from_source(patched / "Swing/Swing.py", "Swing", ns)
    roller = swing_cls(str(recording.path), **recording.kwargs)
    roller.create_custom_windows([recording.labels[0], recording.labels[1]])
    assert len(roller.window_list) == 2
    first = roller.window_list[0]
    assert first.response_data[:, 0].tolist() == [2, 3, 1002, 1003]
    assert first.explanatory_data[:, 0].tolist() == [0, 1, 1000, 1001]
    assert first.explanatory_data[:, 2].tolist() == [1, 2, 1001, 1002]
    assert (
        first.explanatory_labels.tolist()
        == [recording.labels[0], recording.labels[1]] * 2
    )
    assert first.explanatory_window.tolist() == [0, 0, 1, 1]


def test_patched_lasso_single_bootstrap_keeps_axis_without_fitting(patched):
    ns = {"np": np, "pd": pd, "Window": object}
    cls = _class_from_source(patched / "Swing/LassoWindow.py", "LassoWindow", ns)
    obj = cls.__new__(cls)
    obj.resample_window = lambda: np.ones((3, 2))
    obj.add_noise_to_values = lambda values, noise: values
    obj.get_coeffs = lambda alpha, x_data: pd.DataFrame(np.arange(6).reshape(2, 3))
    assert obj.bootstrap_alpha(0.1, 1, 0.2).shape == (2, 3, 1)
    assert obj.bootstrap_alpha(0.1, 3, 0.2).shape == (2, 3, 3)


def test_patched_pls_preserves_constructor_and_handles_new_coefficient_orientation(
    patched,
):
    observed = {}

    class RecordedPLS:
        def __init__(self, **kwargs):
            observed["constructor"] = kwargs

        def fit(self, predictors, response):
            observed["predictors"], observed["response"] = predictors, response
            self.coef_ = np.array([[0.1, -0.2, 0.3]])
            self.x_scores_ = self.x_weights_ = np.ones((3, 1))

    ns = {"np": np, "pd": pd, "Window": object, "PLSRegression": RecordedPLS}
    cls = _class_from_source(patched / "Swing/DionesusWindow.py", "DionesusWindow", ns)
    obj = cls.__new__(cls)
    obj._vipp = lambda *args: np.array([0.2, 0.3, 0.4])
    predictors, response = np.ones((5, 3)), np.arange(5)
    beta, vip, models = obj._fitstack_coeffs(
        1, np.empty((0, 3)), np.empty((0, 3)), [], predictors, response, 0
    )
    assert observed["constructor"] == {"n_components": 1, "scale": False}
    np.testing.assert_array_equal(beta, [[0.1, -0.2, 0.3]])
    assert beta.shape == vip.shape == (1, 3)
    assert len(models) == 1


def test_real_forest_arguments_keep_sqrt_and_assigned_parallelism_without_fitting(
    patched,
):
    observed = {}

    class RecordedForest:
        def __init__(self, **kwargs):
            observed["constructor"] = kwargs

        def fit(self, predictors, response):
            observed["predictors"], observed["response"] = predictors, response
            self.feature_importances_ = np.array([0.2, 0.8])

    ns = {"np": np, "pd": pd, "Window": object, "RandomForestRegressor": RecordedForest}
    cls = _class_from_source(
        patched / "Swing/RFRWindow.py", "RandomForestRegressionWindow", ns
    )
    obj = cls.__new__(cls)
    predictors, response = np.ones((3, 2)), np.arange(3)
    importance, models = obj._fitstack_coeffs(
        np.empty((0, 3)), [], predictors, response, 1, 7, 4
    )
    assert observed["constructor"] == {
        "n_estimators": 7,
        "n_jobs": 4,
        "max_features": "sqrt",
    }
    np.testing.assert_array_equal(importance, [[0.2, 0, 0.8]])
    assert len(models) == 1


@pytest.mark.parametrize("epsilon,accepted", [(5e-13, True), (3e-12, False)])
def test_regularity_absolute_tolerance_matches_core_for_small_time_units(
    inputs, epsilon, accepted
):
    root, extra, temporal = inputs
    mutate_tsv(
        extra / "column_descriptors.tsv",
        lambda rows: [
            row.update(
                timepoint=str(
                    float(row["timepoint"]) * 1e-7
                    + (epsilon if float(row["timepoint"]) >= 2 else 0)
                )
            )
            for row in rows
        ],
    )
    _, columns, values = wrapper.read_matrix(root / "expression.tsv")
    if accepted:
        wrapper.prepare_series(values, columns, extra)
    else:
        with pytest.raises(ValueError, match="regular observed"):
            wrapper.prepare_series(values, columns, extra)


def test_upstream_forest_permutations_forward_explicit_worker_limit_for_every_refit(
    patched,
):
    ns = {"np": np, "pd": pd, "Window": object}
    cls = _class_from_source(
        patched / "Swing/RFRWindow.py", "RandomForestRegressionWindow", ns
    )
    obj = cls.__new__(cls)
    obj.n_trees, obj.explanatory_data = 7, np.arange(12).reshape(4, 3)
    calls = []

    def coefficients(trees, *, x_data, n_jobs):
        calls.append((trees, n_jobs, x_data.shape))
        return pd.DataFrame(np.full((2, 3), len(calls), dtype=float))

    obj.get_coeffs = coefficients
    obj.permute_data = lambda values: values.copy()
    obj.update_variance_2D = lambda state, matrices: {
        "mean": np.asarray(matrices[-1]),
        "variance": np.ones((2, 3)),
    }
    obj._calc_p_value = lambda: np.ones((2, 3))
    obj._permute_coeffs(np.zeros((2, 3)), n_permutations=3, n_jobs=4)
    assert calls == [(7, 4, (4, 3))] * 3


@pytest.mark.parametrize("components", [1, 3])
def test_patched_pls_permutations_refit_the_selected_component_count(
    patched, components
):
    ns = {"np": np, "pd": pd, "Window": object}
    cls = _class_from_source(patched / "Swing/DionesusWindow.py", "DionesusWindow", ns)
    obj = cls.__new__(cls)
    obj.num_pcs, obj.n_genes = components, 2
    obj.explanatory_data = np.arange(20).reshape(5, 4)
    obj.beta_coefficients = np.ones((2, 4))
    calls = []

    def coefficients(num_pcs, *, x_data):
        calls.append((num_pcs, x_data.shape))
        return np.full((2, 4), len(calls), dtype=float), None

    obj.get_coeffs = coefficients
    obj.permute_data = lambda values: values.copy()
    obj.update_variance_2D = lambda state, matrices: {
        "mean": np.asarray(matrices[-1]),
        "variance": np.ones((2, 4)),
    }
    obj._calc_p_value = lambda: np.ones((2, 4))
    obj.run_permutation_test(n_permutations=3)
    assert calls == [(components, (5, 4))] * 3


def test_preparation_cli_is_standard_library_only_and_never_fits(tmp_path):
    # Build a small temporary archive from the immutable user-provided clone.
    # No test depends on integration campaign caches or release logs.
    import tarfile

    source = _raw_source_dir()
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        for relative in wrapper.UPSTREAM_FILES:
            handle.add(
                source / relative, arcname=f"SWING-{wrapper.UPSTREAM_COMMIT}/{relative}"
            )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    script = tmp_path / "run_tool.py"
    script.write_text(
        (SOURCE / "run_tool.py").read_text().replace(wrapper.UPSTREAM_SHA256, digest)
    )
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(script),
            "--prepare-upstream",
            "--source",
            str(archive),
            "--output-dir",
            str(tmp_path / "package"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    receipt = json.loads(result.stdout)
    assert receipt["upstream_commit"] == wrapper.UPSTREAM_COMMIT
    assert len(receipt["patched_files"]) == 9
    assert (
        receipt["original_files"]["Swing/Window.py"]
        == receipt["patched_files"]["Swing/Window.py"]
    )


def _aggregation_classes(patched):
    """Load actual original/patched class bodies without scientific imports."""
    native = _raw_source_dir()
    utility_tree = ast.parse((native / "Swing/util/utility_module.py").read_text())
    edge_function = next(
        node
        for node in utility_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "make_possible_edge_list"
    )
    utility_namespace = {"np": np}
    exec(
        compile(
            ast.Module(body=[edge_function], type_ignores=[]), "native_edges", "exec"
        ),
        utility_namespace,
    )

    def namespace():
        return {
            "np": np,
            "pd": pd,
            "warnings": warnings,
            "print": lambda *args, **kwargs: None,
            "util": SimpleNamespace(
                make_possible_edge_list=utility_namespace["make_possible_edge_list"]
            ),
        }

    # Unused original class methods contain legacy literal-identity checks.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return (
            _class_from_source(native / "Swing/Swing.py", "Swing", namespace()),
            _class_from_source(patched / "Swing/Swing.py", "Swing", namespace()),
        )


def _aggregation_input(case):
    genes = np.array(["A", "001", 'β,quote"', "TF with space", "missing"])
    rng = np.random.default_rng(1703)
    rows = []
    for window in range(4):
        for lag in range(3):
            for parent in genes[:-1]:
                for child in genes[:-1]:
                    importance = float(rng.uniform(0.001, 0.7))
                    if (parent, child) == ("001", "A"):
                        importance = -importance
                    elif (parent, child) == ('β,quote"', "001"):
                        importance = 0.0
                    elif (parent, child) == ("A", "001"):
                        importance = 0.875  # Repeated maximum tests first-tie identity.
                    rows.append(
                        {
                            "Parent": parent,
                            "Child": child,
                            "Importance": importance,
                            "P_window": window,
                            "C_window": window + lag,
                            "adj_imp": float(rng.uniform(0, 2)),
                            "Rank": len(rows) % 23,
                            "Edge": (parent, child),
                            "Lag": lag,
                        }
                    )
    frame = pd.DataFrame(rows)
    # A non-default, nonmonotonic unique index must survive each native subset.
    frame.index = pd.Index(
        np.arange(len(frame), dtype=np.int64)[::-1] * 7 + 101, name="native_row"
    )
    tf_list = None
    self_edges = case == "self_edges"
    if case == "restricted_tfs":
        tf_list = ["TF with space", "A"]
    elif case == "reordered":
        frame = frame.sample(frac=1, random_state=308)
    elif case == "categorical_edges":
        frame["Edge"] = frame["Edge"].astype("category")
    elif case == "empty":
        frame = frame.iloc[:0].copy()
    elif case == "nonpositive":
        frame["Importance"] = -np.abs(frame["Importance"])
    return frame, genes, tf_list, self_edges


def _aggregate(cls, frame, genes, tf_list, self_edges, lag_method):
    obj = cls.__new__(cls)
    obj.full_edge_list = frame.copy(deep=True)
    obj.gene_list = genes.copy()
    obj.tf_list = tf_list
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        obj.make_static_edge_dict(self_edges=self_edges, lag_method=lag_method)
    return obj


def _assert_edge_dictionaries_exact(actual, expected):
    assert list(actual) == list(expected)
    for edge, expected_stats in expected.items():
        assert list(actual[edge]) == list(expected_stats)
        for name, expected_value in expected_stats.items():
            observed = actual[edge][name]
            if name == "dataframe" and expected_value is not None:
                pd.testing.assert_frame_equal(
                    observed, expected_value, check_exact=True
                )
            elif expected_value is None:
                assert observed is None
            else:
                np.testing.assert_equal(observed, expected_value)


@pytest.mark.parametrize(
    "lag_method",
    [
        f"{outer}_{inner}"
        for outer in ("mean", "median", "max", "min")
        for inner in ("mean", "median", "max", "min")
    ],
)
@pytest.mark.parametrize(
    "case",
    [
        "full",
        "restricted_tfs",
        "self_edges",
        "reordered",
        "categorical_edges",
        "empty",
        "nonpositive",
    ],
)
def test_indexed_aggregation_exactly_matches_upstream_for_every_reduction(
    patched, tmp_path, lag_method, case
):
    native, optimized = _aggregation_classes(patched)
    frame, genes, tf_list, self_edges = _aggregation_input(case)
    before = frame.copy(deep=True)
    original = _aggregate(native, frame, genes, tf_list, self_edges, lag_method)
    result = _aggregate(optimized, frame, genes, tf_list, self_edges, lag_method)
    _assert_edge_dictionaries_exact(result.edge_dict, original.edge_dict)
    pd.testing.assert_frame_equal(frame, before, check_exact=True)
    aliases = {gene: gene for gene in genes}
    for sort_by in ("mean", "max", "lag", "rank", "adj"):
        pd.testing.assert_frame_equal(
            result.make_sort_df(result.edge_dict, sort_by=sort_by),
            original.make_sort_df(original.edge_dict, sort_by=sort_by),
            check_exact=True,
        )
        expected_path = tmp_path / "native-network.csv"
        actual_path = tmp_path / "indexed-network.csv"
        wrapper.export_network(
            expected_path, original.edge_dict, aliases, sort_by, "all"
        )
        wrapper.export_network(actual_path, result.edge_dict, aliases, sort_by, "all")
        assert actual_path.read_bytes() == expected_path.read_bytes()


def test_indexed_aggregation_eliminates_repeated_full_edge_column_comparisons(
    patched, monkeypatch
):
    native, optimized = _aggregation_classes(patched)
    frame, genes, tf_list, self_edges = _aggregation_input("full")
    compare = pd.Series.__eq__
    scans = []

    def count_edge_scans(series, other):
        if series.name == "Edge" and isinstance(other, tuple):
            scans.append(len(series))
        return compare(series, other)

    monkeypatch.setattr(pd.Series, "__eq__", count_edge_scans)
    original = _aggregate(native, frame, genes, tf_list, self_edges, "mean_mean")
    assert scans and len(scans) > 1
    scans.clear()
    result = _aggregate(optimized, frame, genes, tf_list, self_edges, "mean_mean")
    assert scans == []
    _assert_edge_dictionaries_exact(result.edge_dict, original.edge_dict)


def test_forest_resource_receipts_remain_available_without_tree_instrumentation():
    def native_tree(*args, **kwargs):
        return args, kwargs

    backend = SimpleNamespace(_parallel_build_trees=native_tree)
    forest = SimpleNamespace(
        n_jobs=2,
        estimators_=[object()] * 7,
        get_params=lambda deep=False: {
            "n_jobs": 2,
            "n_estimators": 7,
            "max_features": "sqrt",
        },
    )
    observer = wrapper._ForestExecutionObserver(
        2, 7, inspect_backend=False, forest_module=backend
    )
    with observer:
        assert backend._parallel_build_trees is native_tree
        observer.set_phase(3, "fit")
        observer.estimator("constructed", forest, 1)
        observer.estimator("fitted", forest, 1)
        assert backend._parallel_build_trees is native_tree
    assert backend._parallel_build_trees is native_tree
    receipt = observer.receipt()
    assert receipt["observed_joblib"]["instrumented"] is False
    assert receipt["observed_joblib"]["tree_tasks"] == 0
    assert receipt["observed_joblib"]["worker_threads"] == []
    assert receipt["models"][0]["constructed_n_jobs"] == 2
    assert receipt["models"][0]["fitted_n_jobs"] == 2
    assert receipt["models"][0]["fitted_tree_count"] == 7
