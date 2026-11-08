"""SWING Python: aligned observed bulk time courses, preserving replicate boundaries."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

UPSTREAM_COMMIT = "591efc7c7c8e6bae2a610a833ec727047f7f1673"
UPSTREAM_SHA256 = "f281aa5fe8959bb60c891b7c96ce73063d618c2765daee8f4b430cb0395b64e8"
UPSTREAM_URL = f"https://codeload.github.com/bagherilab/SWING/tar.gz/{UPSTREAM_COMMIT}"
UPSTREAM_FILES = {
    "Swing/DionesusWindow.py": "39484659d26ba9872410530d6b9c55008cde56830ef86ef82faee7088ced0bbe",
    "Swing/LassoWindow.py": "e39d479b0cb69ad3e404a7f9711ad09f2d7743e27010cd68dd9fb95058633c7c",
    "Swing/RFRWindow.py": "dfce63195484860858dd89bb93800c00d99103aaf2db63e0f81fbe38170a7d55",
    "Swing/Swing.py": "09b4d66e66f6d28b783a39b449e4370c23c57afe738549787505416207a85e2a",
    "Swing/Window.py": "02555415e20749cd4b9177b4063b19b31fc1c72a4df9950572b0dd4d635e23d9",
    "Swing/__init__.py": "c942e96516c556bacad471323fd568fab63afc8147f3821b449023198a16f35a",
    "Swing/util/Evaluator.py": "eaad2bfba88f07385e9a742feb4361e9e13160f1db876990cc537d89e4d2c227",
    "Swing/util/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "Swing/util/utility_module.py": "1488c4ed3de70abc0d0c3e3f314161a06054f2f79c3ba93f7ab63feb59282e2c",
}


def verify_upstream_sources(source_dir: Path) -> None:
    """Reject any source drift before reviewing or applying compatibility edits."""
    for relative, expected in UPSTREAM_FILES.items():
        observed = hashlib.sha256((source_dir / relative).read_bytes()).hexdigest()
        if observed != expected:
            raise ValueError(f"Unexpected SWING source SHA-256 for {relative}")


def prepare_upstream(*, output_dir: Path, source: Path | None = None) -> dict:
    """Copy the verified complete package without running broken upstream setup.py."""
    if source is None:
        import urllib.request

        with urllib.request.urlopen(UPSTREAM_URL, timeout=60) as response:
            raw = response.read(2_000_001)
    else:
        raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("Unexpected SWING source archive SHA-256")

    prefix = f"SWING-{UPSTREAM_COMMIT}/"
    selected = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for relative, expected in UPSTREAM_FILES.items():
            member = archive.getmember(prefix + relative)
            if not member.isfile() or member.size > 1_000_000:
                raise ValueError(f"Unexpected SWING archive member {relative}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"SWING archive member is unreadable: {relative}")
            with stream:
                content = stream.read()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"Unexpected SWING source SHA-256 for {relative}")
            selected[relative] = content

    # No tar extraction: only fixed, reviewed relative paths may be written.
    for relative, content in selected.items():
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    verify_upstream_sources(output_dir)
    patch_upstream_sources(output_dir)
    patched = {
        relative: hashlib.sha256((output_dir / relative).read_bytes()).hexdigest()
        for relative in UPSTREAM_FILES
    }
    return {
        "upstream_commit": UPSTREAM_COMMIT,
        "archive_sha256": UPSTREAM_SHA256,
        "original_files": dict(UPSTREAM_FILES),
        "patched_files": patched,
    }


def _prepare_upstream_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description="Prepare verified pinned SWING sources for image construction; no model is fitted."
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--source",
        type=Path,
        help="Optional local source archive; the pinned archive and module SHA-256 values are always required.",
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            prepare_upstream(output_dir=args.output_dir, source=args.source),
            sort_keys=True,
        )
    )


def patch_upstream_sources(source_dir: Path) -> None:
    """Apply exact, hash-guarded compatibility edits; preserve native scores."""
    verify_upstream_sources(source_dir)
    texts = {
        name: (source_dir / name).read_text(encoding="utf-8") for name in UPSTREAM_FILES
    }

    def replace(name, old, new, count=1):
        if texts[name].count(old) != count:
            raise ValueError(f"Unexpected SWING patch context in {name}: {old!r}")
        texts[name] = texts[name].replace(old, new)

    swing = "Swing/Swing.py"
    # Preserve the exact float64 values supplied by the canonical TSV contract.
    # Pandas' default C-parser precision may otherwise merge adjacent times.
    replace(
        swing,
        "pd.read_csv(file_path, sep=separator)",
        'pd.read_csv(file_path, sep=separator, float_precision="round_trip")',
    )
    # Literal identity is not string equality; callers use JSON-derived values.
    replace(swing, 'self.window_type is "Lasso"', 'self.window_type == "Lasso"', 2)
    replace(swing, 'self.window_type is "Dionesus"', 'self.window_type == "Dionesus"')
    replace(
        swing,
        "df = df.append(current_df.copy(), ignore_index=True)",
        "df = pd.concat([df, current_df.copy()], ignore_index=True)",
    )
    # Index the already-filtered edge rows once. Positional selections retain
    # the native row/index order, so every reduction, idxmax tie and returned
    # per-edge DataFrame is unchanged; only repeated full-table scans disappear.
    replace(
        swing,
        "        edge_diff = full_edge_set.difference(edge_set)\n",
        "        edge_diff = full_edge_set.difference(edge_set)\n"
        "        edge_positions = df.groupby('Edge', sort=False).indices\n",
    )
    replace(
        swing,
        "            current_df = df[df['Edge'] == edge]\n",
        "            current_df = df.iloc[edge_positions[edge]]\n",
    )
    old = """                # remove information from explanatory window
                to_remove = list(set(explanatory_dict['explanatory_labels'])-set(tf_list))
                for removed_tf in to_remove:
                    # remove from explanatory_labels
                    removed_index = np.where(explanatory_dict['explanatory_labels'] == removed_tf)[0][0]
                    explanatory_dict['explanatory_labels'] = np.delete(explanatory_dict['explanatory_labels'],
                                                                       removed_index)

                    # explanatory_window
                    explanatory_dict['explanatory_window'] = np.delete(explanatory_dict['explanatory_window'],
                                                                       removed_index)

                    # explanatory_data
                    explanatory_dict['explanatory_data'] = np.delete(explanatory_dict['explanatory_data'],
                                                                     removed_index, axis=1)
                    # not explanatory_times
"""
    new = """                # Retain every lagged occurrence of each requested TF.
                # Upstream deleted just the first occurrence of non-TF labels.
                keep = np.isin(explanatory_dict['explanatory_labels'], tf_list)
                explanatory_dict['explanatory_labels'] = explanatory_dict['explanatory_labels'][keep]
                explanatory_dict['explanatory_window'] = explanatory_dict['explanatory_window'][keep]
                explanatory_dict['explanatory_data'] = explanatory_dict['explanatory_data'][:, keep]
"""
    replace(swing, old, new)
    rfr = "Swing/RFRWindow.py"
    replace(
        rfr,
        "permuted_coeffs, _ = self.get_coeffs(",
        "permuted_coeffs = self.get_coeffs(",
    )
    replace(
        rfr,
        "        # Fit the model\n        rfr.fit(x_matrix, target_y)",
        """        # Observe the real estimator without changing its fit or RNG.
        observer = getattr(self, '_andrea_forest_observer', None)
        if observer is not None:
            observer('constructed', rfr, len(model_list))
        # Fit the model
        rfr.fit(x_matrix, target_y)
        if observer is not None:
            observer('fitted', rfr, len(model_list))""",
    )
    lasso = "Swing/LassoWindow.py"
    for variable in (
        "permuted_coeffs",
        "boot_matrix",
        "current_coeff",
        "zero_alpha_coeffs",
        "high_alpha_coeffs",
        "cur_coeffs",
        "current_coef",
    ):
        replace(
            lasso, f"{variable}, _ = self.get_coeffs(", f"{variable} = self.get_coeffs("
        )
    replace(
        lasso,
        "np.linspace(cur_min, cur_max, (cur_max - cur_min) / cur_step + 1)",
        "np.linspace(cur_min, cur_max, int(round((cur_max - cur_min) / cur_step)) + 1)",
    )
    replace(lasso, ".idxmax(1)", ".idxmax()", 2)
    # A single bootstrap still has a bootstrap axis, matching every other count.
    replace(
        lasso,
        "        return boot_matrix\n",
        "        return np.asarray(boot_matrix)[..., None] if resamples == 1 else boot_matrix\n",
    )
    dionesus = "Swing/DionesusWindow.py"
    # Permutations must refit the selected model rather than get_coeffs' unrelated
    # helper default of two components (which can exceed a valid pcs=1 fit).
    replace(
        dionesus,
        "self.get_coeffs(x_data=permuted_data)",
        "self.get_coeffs(self.num_pcs, x_data=permuted_data)",
    )
    replace(
        dionesus,
        "PLSRegression(n_pcs, False)",
        "PLSRegression(n_components=n_pcs, scale=False)",
    )
    replace(
        dionesus,
        "coeffs = np.reshape(coeffs, (len(coeffs),))",
        "coeffs = np.asarray(coeffs).reshape(-1)",
    )
    for relative, text in texts.items():
        compile(text, relative, "exec")
        (source_dir / relative).write_text(text, encoding="utf-8")


if __name__ == "__main__" and sys.argv[1:2] == ["--prepare-upstream"]:
    _prepare_upstream_cli(sys.argv[2:])
    raise SystemExit(0)


import csv
from contextlib import nullcontext
import functools
import importlib
import importlib.metadata
import math
import os
import random
import threading
import time
from typing import Any

import numpy as np
import pandas as pd

from _column_metadata import (
    load_column_descriptors as load_bulk_column_descriptors,
    summarize_temporal_metadata,
)

from _run_tool_common import (
    load_execution_mode,
    load_params,
    validate_runtime_inputs,
    write_progress,
)


DEFAULTS = {
    "window_type": "RandomForest",
    "window_width": 3,
    "min_lag": 0,
    "max_lag": 0,
    "step_size": 1,
    "zscore": True,
    "n_trees": 500,
    "alpha": None,
    "pcs": None,
    "n_bootstraps": 10,
    "n_alphas": 20,
    "noise": 0.2,
    "null_alpha": None,
    "null_alpha_max": 10000.0,
    "null_alpha_min_step": 1e-9,
    "alpha_cv_folds": 10,
    "alpha_cv_method": "modelQ2",
    "alpha_grid": None,
    "n_permutations": 0,
    "lag_method": "mean_mean",
    "sort_by": "mean",
    "seed": 0,
    "maxcount": "all",
}


def _integer(value, name: str, minimum: int, maximum: int | None = None) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or maximum is not None
        and value > maximum
    ):
        raise ValueError(f"{name} must be an integer in the supported domain")
    return value


def _number(value, name: str, minimum=0.0, *, strict=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or value < minimum or strict and value == minimum:
        raise ValueError(
            f"{name} must be finite and {'greater than' if strict else 'at least'} {minimum}"
        )
    return value


def resolve_params(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown method parameters: {sorted(unknown)}")
    result = {**DEFAULTS, **raw}
    if result["window_type"] not in {"RandomForest", "Lasso", "Dionesus"}:
        raise ValueError("window_type must be RandomForest, Lasso or Dionesus")
    for name, minimum in (
        ("window_width", 1),
        ("min_lag", 0),
        ("max_lag", 0),
        ("step_size", 1),
        ("n_trees", 1),
        ("n_bootstraps", 1),
        ("n_alphas", 2),
        ("alpha_cv_folds", 2),
        ("n_permutations", 0),
    ):
        _integer(result[name], name, minimum)
    _integer(result["seed"], "seed", 0, 2**32 - 1)
    if result["step_size"] != 1:
        raise ValueError(
            "step_size=1 is required: the pinned upstream does not apply non-unit strides consistently"
        )
    if result["min_lag"] > result["max_lag"]:
        raise ValueError("min_lag cannot exceed max_lag")
    if not isinstance(result["zscore"], bool):
        raise ValueError("zscore must be boolean")
    if result["alpha"] is not None:
        result["alpha"] = _number(result["alpha"], "alpha")
    if result["pcs"] is not None:
        _integer(result["pcs"], "pcs", 1)
    for name in ("null_alpha", "null_alpha_max", "null_alpha_min_step"):
        if name != "null_alpha" or result[name] is not None:
            result[name] = _number(result[name], name, strict=True)
    if result["null_alpha_min_step"] > result["null_alpha_max"]:
        raise ValueError("null_alpha_min_step cannot exceed null_alpha_max")
    result["noise"] = _number(result["noise"], "noise")
    if result["noise"] > 1:
        raise ValueError("noise must be between zero and one")
    if result["alpha_cv_method"] not in {"modelQ2", "max_posQ2"}:
        raise ValueError("alpha_cv_method must be modelQ2 or max_posQ2")
    grid = result["alpha_grid"]
    if grid is not None:
        if not isinstance(grid, list) or len(grid) < 2:
            raise ValueError(
                "alpha_grid must be null or a strictly increasing list of at least two nonnegative values"
            )
        result["alpha_grid"] = [_number(value, "alpha_grid") for value in grid]
        if any(a >= b for a, b in zip(result["alpha_grid"], result["alpha_grid"][1:])):
            raise ValueError("alpha_grid must be strictly increasing")
    if result["n_permutations"] == 1:
        raise ValueError(
            "n_permutations must be zero or at least two for a defined sample variance"
        )
    allowed = {
        f"{outer}_{inner}"
        for outer in ("mean", "median", "max", "min")
        for inner in ("mean", "median", "max", "min")
    }
    if result["lag_method"] not in allowed:
        raise ValueError(
            "lag_method must combine two allowed reductions: mean, median, max or min"
        )
    if result["sort_by"] not in {"mean", "max", "lag", "rank", "adj"}:
        raise ValueError("sort_by must be mean, max, lag, rank or adj")
    if result["maxcount"] != "all":
        _integer(result["maxcount"], "maxcount", 0)
    conditional = {
        "RandomForest": {"n_trees"},
        "Dionesus": {"pcs"},
        "Lasso": {
            "alpha",
            "n_bootstraps",
            "n_alphas",
            "noise",
            "null_alpha",
            "null_alpha_max",
            "null_alpha_min_step",
            "alpha_cv_folds",
            "alpha_cv_method",
            "alpha_grid",
        },
    }
    inactive = set.union(
        *(
            keys
            for method, keys in conditional.items()
            if method != result["window_type"]
        )
    )
    for name in inactive:
        if name in raw and result[name] != DEFAULTS[name]:
            raise ValueError(
                f"{name} is not active for window_type={result['window_type']}"
            )
    return result


def read_matrix(path: Path) -> tuple[list[str], list[str], np.ndarray]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if len(rows) < 3 or len(rows[0]) < 3:
        raise ValueError(
            "SWING needs at least two expression genes and two observed columns"
        )
    header, values, genes = rows[0], [], []
    if any(not value.strip() or value != value.strip() for value in header) or len(
        set(header)
    ) != len(header):
        raise ValueError("Expression headers must be unique exact nonempty identifiers")
    for number, row in enumerate(rows[1:], 2):
        if not row:
            continue
        if len(row) != len(header):
            raise ValueError(f"Malformed expression row {number}")
        gene = row[0]
        if not gene.strip() or gene != gene.strip() or gene in genes:
            raise ValueError(
                "Expression genes must be unique exact nonempty identifiers"
            )
        genes.append(gene)
        values.append([float(value) for value in row[1:]])
    matrix = np.asarray(values, dtype=np.float64)
    if len(genes) < 2 or not np.isfinite(matrix).all():
        raise ValueError(
            "SWING requires at least two genes and finite complete expression values"
        )
    return genes, header[1:], matrix


def prepare_series(matrix, columns: list[str], extra: Path):
    descriptors = load_bulk_column_descriptors(
        extra / "column_descriptors.tsv", columns
    )
    temporal = summarize_temporal_metadata(descriptors)
    if temporal["sampling_design"] != "longitudinal":
        raise ValueError("SWING requires longitudinal observed bulk time series")
    times = {
        column: row
        for column, row in descriptors.items()
        if row["observation_type"] == "time_series"
    }
    index, grouped, observations = (
        {column: i for i, column in enumerate(columns)},
        {},
        set(),
    )
    for column in columns:
        if column not in times:
            continue
        time_row = times[column]
        if time_row["observation_id"] in observations:
            raise ValueError(
                "Technical replicate columns require explicit preprocessing; SWING does not average them"
            )
        observations.add(time_row["observation_id"])
        time = float(time_row["timepoint"])
        if not math.isfinite(time):
            raise ValueError("Observed times must be finite")
        grouped.setdefault(time_row["series_id"], []).append((time, column))
    blocks, resolved, grid = [], [], None
    for series_id, entries in sorted(grouped.items()):
        entries.sort()
        ids = [column for _, column in entries]
        units = {times[column]["replicate"] for column in ids}
        conditions = {times[column]["condition"] for column in ids}
        coordinates = np.asarray([time for time, _ in entries], dtype=np.float64)
        with np.errstate(over="ignore", invalid="ignore"):
            intervals = np.diff(coordinates)
        if len(entries) < 2 or len(units) != 1 or len(conditions) != 1:
            raise ValueError(
                "Each series needs at least two observations from one biological unit and one condition"
            )
        if not np.isfinite(intervals).all() or not np.all(intervals > 0):
            raise ValueError(
                "Each series needs finite strictly increasing unique times"
            )
        # Lags are sample-index offsets, not arbitrary elapsed-time displacements.
        # Tolerate representation error only; never resample or impute observations.
        if not all(
            math.isclose(
                float(interval), float(intervals[0]), rel_tol=1e-9, abs_tol=1e-12
            )
            for interval in intervals
        ):
            raise ValueError(
                "SWING requires regular observed time spacing; no interpolation is performed"
            )
        if grid is not None and not np.array_equal(coordinates, grid):
            raise ValueError(
                "Independent SWING series must have identical complete observed time grids"
            )
        grid = coordinates
        blocks.append(matrix[:, [index[column] for column in ids]].T.copy())
        resolved.append(
            {
                "series_id": series_id,
                "condition": next(iter(conditions)),
                "replicate": next(iter(units)),
                "columns": ids,
                "timepoints": coordinates.tolist(),
            }
        )
    if not blocks:
        raise ValueError("At least one observed time series is required")
    return blocks, grid, resolved


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_progress_event(path: Path, **payload) -> None:
    """Retain the actual emitted events even when UI polling skips a short phase."""
    write_progress(path, **payload)
    event = json.loads(path.read_text(encoding="utf-8"))
    event["observed_monotonic_ns"] = time.monotonic_ns()
    journal = path.parent / "provenance/progress-events.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    initial = payload.get("phase") == "validate" and payload.get("status") == "running"
    with journal.open("w" if initial else "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")


def _seed(seed: int, window_index: int, phase: int) -> int:
    return int(np.random.SeedSequence([seed, window_index, phase]).generate_state(1)[0])


def _apply_seed(seed: int) -> None:
    np.random.seed(seed)
    random.seed(seed)


def _resource_observation(threads: int, method: str) -> dict:
    from threadpoolctl import threadpool_info

    root = Path("/sys/fs/cgroup")
    limits = {}
    for name in (
        "cpu.max",
        "memory.max",
        "cpuset.cpus.effective",
        "cpu/cpu.cfs_quota_us",
        "cpu/cpu.cfs_period_us",
        "memory/memory.limit_in_bytes",
    ):
        path = root / name
        if path.is_file():
            limits[name] = path.read_text().strip()
    return {
        "pid": os.getpid(),
        "assigned_threads": threads,
        "requested_forest_n_jobs": threads if method == "RandomForest" else None,
        "native_pools": threadpool_info(),
        "cgroup_values": limits,
        "cpu_affinity": (
            sorted(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else None
        ),
    }


class _ForestExecutionObserver:
    """Observe actual estimators, with optional expensive tree-worker tracing.

    Normal runs inspect constructed/fitted forests without intercepting trees.
    Explicit diagnostic tracing counts threads entering sklearn's tree builder;
    it does not measure CPU utilization or promise saturation. The original
    callable, arguments, return values and RNG states are preserved.
    """

    def __init__(
        self,
        threads: int,
        trees: int,
        *,
        inspect_backend=True,
        forest_module=None,
        output_path=None,
    ):
        self.requested = {"n_jobs": threads, "n_estimators": trees}
        self.inspect_backend = inspect_backend
        self.forest_module = forest_module
        self.configurations = {}
        self.models = []
        self.workers = {}
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0
        self.tree_tasks = 0
        self.window = None
        self.phase = "fit"
        self.current_model = None
        self.original = None
        self.output_path = output_path

    def __enter__(self):
        if not self.inspect_backend:
            return self
        if self.forest_module is None:
            self.forest_module = importlib.import_module("sklearn.ensemble._forest")
        self.original = self.forest_module._parallel_build_trees
        original = self.original

        @functools.wraps(original)
        def observed_tree(*args, **kwargs):
            thread = threading.current_thread()
            identity = (os.getpid(), threading.get_ident(), threading.get_native_id())
            with self.lock:
                self.workers[identity] = {
                    "pid": identity[0],
                    "thread_ident": identity[1],
                    "native_thread_id": identity[2],
                    "name": thread.name,
                }
                self.tree_tasks += 1
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                if self.current_model is not None:
                    self.current_model["observed_tree_tasks"] += 1
            try:
                return original(*args, **kwargs)
            finally:
                with self.lock:
                    self.active -= 1

        self.forest_module._parallel_build_trees = observed_tree
        return self

    def __exit__(self, *_exc):
        if self.original is not None:
            self.forest_module._parallel_build_trees = self.original
        if self.output_path is not None:
            _write_json(self.output_path, self.receipt())

    def set_phase(self, window_index: int, phase: str):
        self.window, self.phase = window_index, phase

    def estimator(self, stage: str, forest, target_index: int):
        params = forest.get_params(deep=False)
        canonical = json.dumps(
            params, sort_keys=True, allow_nan=False, separators=(",", ":")
        )
        identity = hashlib.sha256(canonical.encode()).hexdigest()
        self.configurations[identity] = params
        if stage == "constructed":
            if (
                params["n_jobs"] != self.requested["n_jobs"]
                or params["n_estimators"] != self.requested["n_estimators"]
            ):
                raise ValueError(
                    "Actual forest constructor differs from the requested resource/tree configuration"
                )
            model = {
                "window_index": self.window,
                "phase": self.phase,
                "target_index": target_index,
                "configuration_sha256": identity,
                "constructed_n_jobs": params["n_jobs"],
                "fitted_n_jobs": None,
                "fitted_tree_count": None,
                "observed_tree_tasks": 0,
            }
            self.models.append(model)
            self.current_model = model
        elif stage == "fitted":
            model = self.current_model
            if (
                model is None
                or model["target_index"] != target_index
                or model["configuration_sha256"] != identity
            ):
                raise ValueError("Forest identity/configuration changed during fit")
            model["fitted_n_jobs"] = forest.n_jobs
            model["fitted_tree_count"] = len(forest.estimators_)
            if (
                forest.n_jobs != params["n_jobs"]
                or len(forest.estimators_) != params["n_estimators"]
            ):
                raise ValueError("Fitted forest differs from its actual constructor")
            self.current_model = None
        else:
            raise ValueError("Unexpected forest observation stage")

    def receipt(self):
        return {
            "observer_pid": os.getpid(),
            "requested": self.requested,
            "configurations": self.configurations,
            "models": self.models,
            "observed_joblib": {
                "instrumented": self.inspect_backend,
                "tree_tasks": self.tree_tasks,
                "max_concurrent_tree_tasks": self.maximum_active,
                "worker_threads": [self.workers[key] for key in sorted(self.workers)],
                "semantics": "Threads which entered sklearn tree-build tasks; not CPU utilization, active cores or a saturation guarantee.",
            },
        }


def _validate_dimensions(
    params: dict, ngenes: int, nseries: int, ntime: int, ncandidates: int
) -> dict:
    n_windows = ntime - params["window_width"] + 1
    if n_windows <= params["max_lag"]:
        raise ValueError(
            "window_width and max_lag must leave at least one complete response window"
        )
    nrows = nseries * params["window_width"]
    nfeatures = ncandidates * (params["max_lag"] - params["min_lag"] + 1)
    minimum_features = nfeatures - (1 if params["min_lag"] == 0 else 0)
    if minimum_features < 1:
        raise ValueError(
            "At least one predictor must remain after removing contemporaneous self-prediction"
        )
    if params["window_type"] == "Lasso":
        if params["alpha"] is None and nrows // params["alpha_cv_folds"] < 2:
            raise ValueError(
                "Lasso alpha cross-validation requires at least two test observations per fold; choose fewer folds, a wider window or explicit alpha"
            )
    if params["window_type"] == "Dionesus":
        bound = min(nrows - 1, minimum_features)
        if params["pcs"] is not None and params["pcs"] > bound:
            raise ValueError(
                "pcs exceeds centered window observations or available predictor dimensions"
            )
        if params["pcs"] is None and bound < 2:
            raise ValueError(
                "Upstream automatic PLS selection requires at least two predictor and observation dimensions; use pcs=1 explicitly"
            )
    return {
        "candidate_windows": n_windows,
        "fitted_windows": n_windows - params["max_lag"],
        "rows_per_window": nrows,
        "predictors_per_window": nfeatures,
        "lags_in_time_unit": [
            float(lag) for lag in range(params["min_lag"], params["max_lag"] + 1)
        ],
    }


def export_network(
    path: Path, edge_dict: dict, alias_to_gene: dict[str, str], sort_by: str, maxcount
):
    rows = []
    field = sort_by + "_importance"
    for (source, target), stats in edge_dict.items():
        if source not in alias_to_gene or target not in alias_to_gene:
            raise ValueError("Upstream emitted an unknown gene identifier")
        if source == target:
            continue
        value = float(stats[field])
        if sort_by == "rank" and math.isnan(value) and stats.get("dataframe") is None:
            continue  # Native absent-edge sentinel is undefined, never a high-ranked edge.
        if not math.isfinite(value) or value < 0:
            raise ValueError("Upstream edge scores must be finite and nonnegative")
        score = 1.0 / (1.0 + value) if sort_by == "rank" else value
        if score > 0:
            rows.append((alias_to_gene[source], alias_to_gene[target], score))
    rows.sort(key=lambda row: (-row[2], row[0], row[1]))
    if maxcount != "all":
        rows = rows[:maxcount]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target", "score", "sign", "evidence", "context"])
        writer.writerows(
            (source, target, repr(score), "?", "association", "global")
            for source, target, score in rows
        )
    return len(rows)


def run(
    *,
    input_path: Path,
    params_path: Path,
    extra_dir: Path,
    output_dir: Path,
    threads: int,
    upstream=None,
):
    validate_runtime_inputs(
        input_path=input_path,
        params_path=params_path,
        extra_dir=extra_dir,
        threads=threads,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    progress = output_dir / "progress.json"
    _write_progress_event(
        progress,
        status="running",
        percent=0,
        phase="validate",
        message="Validating observed SWING time series",
    )
    try:
        load_execution_mode(params_path, supported_modes={"global"})
        genes, columns, expression = read_matrix(input_path)
        params = resolve_params(load_params(params_path))
        blocks, coordinates, series = prepare_series(expression, columns, extra_dir)
        descriptors = load_bulk_column_descriptors(
            extra_dir / "column_descriptors.tsv", columns
        )
        aliases = [f"__andrea_gene_{index:06d}" for index in range(len(genes))]
        gene_to_alias = dict(zip(genes, aliases))
        alias_to_gene = dict(zip(aliases, genes))
        time_label = "__andrea_time__"
        tf_path = extra_dir / "tf_list.txt"
        regulators = genes.copy()
        if tf_path.is_file():
            names = [
                name
                for name in tf_path.read_text(encoding="utf-8").splitlines()
                if name.strip()
            ]
            if (
                not names
                or len(names) != len(set(names))
                or not set(names) <= set(genes)
            ):
                raise ValueError(
                    "tf_list must contain unique exact expression gene IDs"
                )
            regulators = [gene for gene in genes if gene in set(names)]
        if params["window_type"] == "Lasso" and regulators != genes:
            raise ValueError(
                "Lasso with a restricted TF subset is excluded: upstream multi-lag self-predictor removal assumes the full matched gene universe"
            )
        dimensions = _validate_dimensions(
            params, len(genes), len(blocks), len(coordinates), len(regulators)
        )
        combined = np.vstack(blocks)
        if params["zscore"] or params["window_type"] in {"Lasso", "Dionesus"}:
            with np.errstate(over="ignore", invalid="ignore"):
                std = np.std(combined, axis=0, ddof=1)
            invalid = [
                gene
                for gene, value in zip(genes, std)
                if not math.isfinite(float(value)) or value == 0
            ]
            if invalid:
                raise ValueError(
                    f"Constant or numerically degenerate genes are incompatible with requested normalization/backend: {invalid}"
                )
        provenance = output_dir / "provenance"
        provenance.mkdir(exist_ok=True)
        native_input = provenance / "swing_expression.tsv"
        with native_input.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow([time_label, *aliases])
            # Complete repeated grids keep explanatory and response rows paired
            # within the same independent series; time never crosses a boundary.
            for block in blocks:
                writer.writerows(
                    (repr(float(time)), *[repr(float(value)) for value in row])
                    for time, row in zip(coordinates, block)
                )
        seeds = [
            {
                "window_index": index,
                "initialize": _seed(params["seed"], index, 0),
                "fit": _seed(params["seed"], index, 1),
                "bootstrap": _seed(params["seed"], index, 2),
                "permutation": _seed(params["seed"], index, 3),
            }
            for index in range(params["max_lag"], dimensions["candidate_windows"])
        ]
        dimensions["lags_in_time_unit"] = [
            float(lag * (coordinates[1] - coordinates[0]))
            for lag in range(params["min_lag"], params["max_lag"] + 1)
        ]
        resolved = {
            "params": params,
            "genes": genes,
            "regulators": regulators,
            "temporal": summarize_temporal_metadata(descriptors),
            "series": series,
            "time_series_columns": [
                column
                for column in columns
                if descriptors[column]["observation_type"] == "time_series"
            ],
            "unused_steady_state_columns": [
                column
                for column in columns
                if descriptors[column]["observation_type"] == "steady_state"
            ],
            "native_alias_to_gene": alias_to_gene,
            "native_time_label": time_label,
            "threads": threads,
            "dimensions": dimensions,
            "window_seeds": seeds,
            "score_semantics": (
                "1/(1+native_rank)"
                if params["sort_by"] == "rank"
                else params["sort_by"] + "_importance"
            ),
            "input_sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [
                    input_path,
                    params_path,
                    params_path.with_name("execution.json"),
                    *sorted(extra_dir.iterdir()),
                ]
                if path.is_file()
            },
        }
        _write_json(provenance / "resolved.json", resolved)
        _write_progress_event(
            progress,
            status="running",
            percent=5,
            phase="initializing",
            message="Initializing SWING data, normalization and windows",
        )
        factory = (
            upstream if upstream is not None else importlib.import_module("Swing").Swing
        )
        roller = factory(
            str(native_input),
            gene_start=1,
            gene_end=len(genes) + 1,
            time_label=time_label,
            separator="\t",
            window_type=params["window_type"],
            step_size=1,
            min_lag=params["min_lag"],
            max_lag=params["max_lag"],
            window_width=params["window_width"],
        )
        if not np.array_equal(
            roller.raw_data[time_label].to_numpy(dtype=float),
            np.tile(coordinates, len(blocks)),
        ) or not np.array_equal(
            roller.raw_data[aliases].to_numpy(dtype=float), combined
        ):
            raise ValueError(
                "Upstream input parsing changed observed time or expression precision"
            )
        if params["zscore"]:
            roller.zscore_all_data()
            if not np.isfinite(
                roller.norm_data.drop(columns=[time_label]).to_numpy(dtype=float)
            ).all():
                raise ValueError(
                    "Upstream z-score normalization produced nonfinite expression"
                )
        if regulators == genes:
            roller.create_windows()
        else:
            roller.create_custom_windows([gene_to_alias[gene] for gene in regulators])
        if len(roller.window_list) != dimensions["fitted_windows"]:
            raise ValueError(
                "Upstream created an unexpected number of complete windows"
            )
        from threadpoolctl import threadpool_limits

        messages, summaries = [], []
        _write_json(
            provenance / "resources-before.json",
            _resource_observation(threads, params["window_type"]),
        )
        forest_observer = (
            _ForestExecutionObserver(
                threads,
                params["n_trees"],
                inspect_backend=(
                    upstream is None
                    and os.environ.get("ANDREA_SWING_TRACE_TREES", "0") == "1"
                ),
                output_path=provenance / "forest-execution.json",
            )
            if params["window_type"] == "RandomForest"
            else None
        )
        _write_progress_event(
            progress,
            status="running",
            percent=10,
            phase="inference",
            message=f"Completed 0/{len(seeds)} SWING windows",
            completed=0,
            total=len(seeds),
        )
        with threadpool_limits(limits=1), forest_observer or nullcontext():
            for ordinal, (window, window_seeds) in enumerate(
                zip(roller.window_list, seeds)
            ):
                if window.nth_window != window_seeds["window_index"]:
                    raise ValueError("Upstream reordered windows unexpectedly")
                _apply_seed(window_seeds["initialize"])
                method = params["window_type"]
                if method == "RandomForest":
                    window.initialize_params(n_trees=params["n_trees"])
                    window.n_jobs = threads
                    window._andrea_forest_observer = forest_observer.estimator
                    forest_observer.set_phase(window.nth_window, "fit")
                elif method == "Dionesus":
                    if params["pcs"] is None:
                        window.initialize_params()
                    else:
                        window.num_pcs = params["pcs"]
                    if (
                        not isinstance(window.num_pcs, (int, np.integer))
                        or window.num_pcs < 1
                    ):
                        raise ValueError(
                            "Upstream PLS component selection returned an invalid dimension"
                        )
                else:
                    window.null_alpha = params["null_alpha"]
                    window.get_null_alpha = functools.partial(
                        window.get_null_alpha,
                        max_expected_alpha=params["null_alpha_max"],
                        min_step_size=params["null_alpha_min_step"],
                    )
                    if params["alpha"] is None:
                        window.alpha, window.cv_table = window.cv_select_alpha(
                            alpha_range=(
                                None
                                if params["alpha_grid"] is None
                                else np.asarray(params["alpha_grid"], dtype=float)
                            ),
                            method=params["alpha_cv_method"],
                            n_folds=params["alpha_cv_folds"],
                        )
                        if not math.isfinite(float(window.alpha)) or window.alpha < 0:
                            raise ValueError(
                                "Upstream Lasso cross-validation returned an invalid alpha"
                            )
                    else:
                        window.initialize_params(alpha=params["alpha"])
                _apply_seed(window_seeds["fit"])
                window.fit_window()
                if method == "Lasso":
                    _apply_seed(window_seeds["bootstrap"])
                    window.run_bootstrap(
                        n_bootstraps=params["n_bootstraps"],
                        n_alphas=params["n_alphas"],
                        noise=params["noise"],
                    )
                if params["n_permutations"]:
                    _apply_seed(window_seeds["permutation"])
                    arguments = {"n_permutations": params["n_permutations"]}
                    if method == "RandomForest":
                        arguments["n_jobs"] = threads
                        forest_observer.set_phase(window.nth_window, "permutation")
                    window.run_permutation_test(**arguments)
                table = window.make_edge_table()
                for name in ("Importance", "Stability", "Beta"):
                    if (
                        name in table
                        and not np.isfinite(table[name].to_numpy(dtype=float)).all()
                    ):
                        raise ValueError(
                            f"Upstream {method} produced nonfinite {name}; no scores were imputed"
                        )
                table.to_csv(
                    provenance / f"window-{window.nth_window:05d}-edges.tsv",
                    sep="\t",
                    index=False,
                )
                if (
                    params["n_permutations"]
                    and "p_value" in table
                    and not np.isfinite(table["p_value"].to_numpy(dtype=float)).all()
                ):
                    messages.append(
                        f"Window {window.nth_window}: some diagnostic permutation p-values are undefined; raw values retained without changing edge scores."
                    )
                if getattr(window, "cv_table", None) is not None:
                    window.cv_table.to_csv(
                        provenance / f"window-{window.nth_window:05d}-cv.tsv",
                        sep="\t",
                        index=False,
                    )
                summaries.append(
                    {
                        "window_index": window.nth_window,
                        "alpha": None if method != "Lasso" else float(window.alpha),
                        "null_alpha": (
                            None if method != "Lasso" else float(window.null_alpha)
                        ),
                        "pcs": None if method != "Dionesus" else int(window.num_pcs),
                        "n_trees": (
                            None if method != "RandomForest" else int(window.n_trees)
                        ),
                        "n_jobs": int(window.n_jobs) if method == "RandomForest" else 1,
                    }
                )
                _write_progress_event(
                    progress,
                    status="running",
                    percent=10 + int(70 * (ordinal + 1) / len(seeds)),
                    phase="inference",
                    message=f"Fitted SWING window {ordinal + 1}/{len(seeds)}",
                    completed=ordinal + 1,
                    total=len(seeds),
                )
            _write_json(
                provenance / "resources-inference.json",
                _resource_observation(threads, params["window_type"]),
            )
        _write_progress_event(
            progress,
            status="running",
            percent=85,
            phase="aggregation",
            message="Aggregating completed native window and lag scores",
            completed=len(seeds),
            total=len(seeds),
        )
        roller.compile_edges(self_edges=False)
        roller.full_edge_list.to_csv(
            provenance / "native-window-edges.tsv", sep="\t", index=False
        )
        roller.make_static_edge_dict(self_edges=False, lag_method=params["lag_method"])
        # Preserve the official positive-Importance filter, including Lasso's
        # asymmetric retention. Do not silently replace coefficients by abs().
        native_ranked = roller.make_sort_df(roller.edge_dict, sort_by=params["sort_by"])
        _write_progress_event(
            progress,
            status="running",
            percent=95,
            phase="export",
            message="Exporting the network and native provenance",
            completed=len(seeds),
            total=len(seeds),
        )
        native_ranked.to_csv(
            provenance / "native-ranked-edges.tsv", sep="\t", index=False
        )
        count = export_network(
            output_dir / "network.csv",
            roller.edge_dict,
            alias_to_gene,
            params["sort_by"],
            params["maxcount"],
        )
        if not count:
            messages.append(
                "Upstream retained no positive non-self interactions under the requested export policy."
            )
        resolved["windows"] = summaries
        _write_json(provenance / "resolved.json", resolved)
        packages = {}
        for package in (
            "numpy",
            "pandas",
            "scipy",
            "scikit-learn",
            "joblib",
            "threadpoolctl",
        ):
            try:
                packages[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                packages[package] = "not installed in this test environment"
        source_root = (
            Path(importlib.import_module("Swing").__file__).parent.parent
            if upstream is None
            else None
        )
        _write_json(
            provenance / "session.json",
            {
                "upstream_commit": UPSTREAM_COMMIT,
                "packages": packages,
                "source_sha256": (
                    {}
                    if source_root is None
                    else {
                        relative: hashlib.sha256(
                            (source_root / relative).read_bytes()
                        ).hexdigest()
                        for relative in UPSTREAM_FILES
                    }
                ),
                "native_rank_projection": "1/(1+native_rank); larger is better; not a probability",
                "lasso_filter": "Official positive Importance filter retained; negative beta associations are omitted",
            },
        )
        _write_progress_event(
            progress,
            status="completed",
            percent=100,
            phase="done",
            message="SWING network exported",
            warnings=messages,
            completed=len(seeds),
            total=len(seeds),
        )
        return resolved
    except Exception as error:
        _write_progress_event(
            progress,
            status="failed",
            percent=100,
            phase="failed",
            message="SWING failed",
            error=str(error),
        )
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Pinned SWING inference for complete observed bulk time grids"
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--extra", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--threads", required=True, type=int)
    args = parser.parse_args()
    run(
        input_path=args.input,
        params_path=args.params,
        extra_dir=args.extra,
        output_dir=args.output_dir,
        threads=args.threads,
    )


if __name__ == "__main__":
    main()
