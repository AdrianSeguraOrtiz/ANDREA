"""dynGENIE3 Python: observed bulk time courses, preserving independent series."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

# Build preparation is a standard-library-only CLI of this wrapper. The pinned
# public source is downloaded and checked before applying the same reviewed
# compatibility/resource/seed patch used by the current integration.
UPSTREAM_COMMIT = "27eddd960d50e82791a8e0e5f8fcf6d679fe3eac"
UPSTREAM_SHA256 = "8108e4e8d15809e88ca1eb3c51dd2b260303b00b108e20822f0af84131855e8f"
UPSTREAM_URL = f"https://raw.githubusercontent.com/vahuynh/dynGENIE3/{UPSTREAM_COMMIT}/dynGENIE3_python/dynGENIE3.py"


def patched_source(raw: bytes) -> str:
    if hashlib.sha256(raw).hexdigest() != UPSTREAM_SHA256:
        raise ValueError(
            "Unexpected dynGENIE3 source; patches require the verified upstream SHA-256"
        )
    text = raw.decode("utf-8")

    def replace(old: str, new: str, count: int = 1):
        nonlocal text
        if text.count(old) != count:
            raise ValueError(f"Patch context changed: {old!r}")
        text = text.replace(old, new)

    replace(
        "from sklearn.tree import BaseDecisionTree",
        "from sklearn.tree._classes import BaseDecisionTree\nfrom scipy.stats import pearsonr",
    )
    replace("np.pearsonr(", "_andrea_pearsonr(", 2)
    replace('max_features = "auto"', "max_features = 1.0")
    replace(
        "save_models=False,nthreads=1):",
        "save_models=False,nthreads=1,random_state=None):",
    )
    replace(
        "compute_quality_scores,save_models):\n",
        "compute_quality_scores,save_models,random_state=None):\n",
    )
    replace(
        "if alpha != 'from_data':",
        "if not (isinstance(alpha, str) and alpha == 'from_data'):",
    )
    replace(
        "if alpha == 'from_data':",
        "if isinstance(alpha, str) and alpha == 'from_data':",
    )
    replace(
        "input_data = [[TS_data,time_points,SS_data,i,alphas[i],input_idx,tree_method,K,ntrees,compute_quality_scores,save_models] for i in range(ngenes)]",
        "input_data = [[TS_data,time_points,SS_data,i,alphas[i],input_idx,tree_method,K,ntrees,compute_quality_scores,save_models,_andrea_target_seed(random_state,i)] for i in range(ngenes)]",
    )
    replace(
        "        pool = Pool(nthreads)\n        alloutput = pool.map(wr_dynGENIE3_single, input_data)",
        "        with Pool(nthreads) as pool:\n            alloutput = pool.map(wr_dynGENIE3_single, input_data)",
    )
    replace(
        "dynGENIE3_single(TS_data,time_points,SS_data,i,alphas[i],input_idx,tree_method,K,ntrees,compute_quality_scores,save_models)",
        "dynGENIE3_single(TS_data,time_points,SS_data,i,alphas[i],input_idx,tree_method,K,ntrees,compute_quality_scores,save_models,_andrea_target_seed(random_state,i))",
    )
    replace(
        "max_features=max_features,oob_score=oob_score)",
        "max_features=max_features,oob_score=oob_score,random_state=random_state,n_jobs=1)",
        2,
    )
    replace("args[8], args[9], args[10])", "args[8], args[9], args[10], args[11])")
    replace("    h = 1 # lag", "    _andrea_record_resources()\n    h = 1 # lag")
    replace(
        "np.random.uniform(low=1e-12,high=1e-11,size=importances_by_tree.shape)",
        "(np.random if random_state is None else np.random.RandomState(random_state)).uniform(low=1e-12,high=1e-11,size=importances_by_tree.shape)",
    )
    text += '''\n\ndef _andrea_target_seed(seed, target_index):
    """Stable across worker schedules; None preserves upstream unseeded behavior."""
    if seed is None:
        return None
    return int(np.random.SeedSequence([int(seed), int(target_index)]).generate_state(1)[0])


def _andrea_pearsonr(prediction, observed):
    """Undefined inverse predictions have no correlation; retain the inferred network."""
    if (len(prediction) < 2 or len(observed) < 2
            or not np.isfinite(prediction).all() or not np.isfinite(observed).all()):
        return float('nan'), float('nan')
    return pearsonr(prediction, observed)


def _andrea_record_resources():
    """Retain actual native pool observations for each target worker."""
    import json
    import os
    from pathlib import Path
    from threadpoolctl import threadpool_info
    directory = os.environ.get('ANDREA_RESOURCE_REPORT_DIR')
    if not directory:
        return
    path = Path(directory) / ('resource-worker-%d.json' % os.getpid())
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'pid': os.getpid(), 'native_pools': threadpool_info(),
        'cgroups': _andrea_cgroups(),
        'cpu_affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None}, indent=2) + '\\n')
    temporary.replace(path)


def _andrea_cgroups(root='/sys/fs/cgroup'):
    """Read the effective container limits; retain raw values, never infer quotas from affinity."""
    from pathlib import Path
    root = Path(root)
    try:
        if (root / 'cpu.max').is_file() and (root / 'memory.max').is_file():
            version = 2
            cpu_path, memory_path = root / 'cpu.max', root / 'memory.max'
            cpu_raw, memory_raw = cpu_path.read_text().strip(), memory_path.read_text().strip()
            quota_text, period_text = cpu_raw.split()
            quota, period = None if quota_text == 'max' else int(quota_text), int(period_text)
            memory = None if memory_raw == 'max' else int(memory_raw)
        else:
            version = 1
            cpu_dir = next((path for path in (root / 'cpu', root / 'cpu,cpuacct', root)
                            if (path / 'cpu.cfs_quota_us').is_file() and (path / 'cpu.cfs_period_us').is_file()), None)
            memory_path = next((path for path in (root / 'memory/memory.limit_in_bytes', root / 'memory.limit_in_bytes') if path.is_file()), None)
            if cpu_dir is None or memory_path is None:
                return {'version': None, 'error': 'Effective CPU and memory cgroup limits are unavailable'}
            cpu_path = cpu_dir / 'cpu.cfs_quota_us'
            quota_text = cpu_path.read_text().strip()
            period_text = (cpu_dir / 'cpu.cfs_period_us').read_text().strip()
            cpu_raw, memory_raw = quota_text + ' ' + period_text, memory_path.read_text().strip()
            quota, period = int(quota_text), int(period_text)
            quota = None if quota < 0 else quota
            memory = int(memory_raw)
        if period <= 0 or quota is not None and quota <= 0 or memory is not None and memory <= 0:
            raise ValueError('Cgroup limits must be positive or explicitly unlimited')
        return {'version': version,
                'cpu': {'path': str(cpu_path), 'raw': cpu_raw, 'quota_us': quota, 'period_us': period,
                        'limit_cpus': None if quota is None else quota / period},
                'memory': {'path': str(memory_path), 'raw': memory_raw, 'limit_bytes': memory}}
    except (OSError, ValueError) as error:
        return {'version': None, 'error': str(error)}
'''
    compile(text, "dynGENIE3.py", "exec")
    return text


def prepare_upstream(*, output_dir: Path, source: Path | None = None) -> dict[str, str]:
    """Install only the exact pinned public source, without importing the method."""
    if source is None:
        import urllib.request

        raw = urllib.request.urlopen(UPSTREAM_URL, timeout=60).read()
    else:
        raw = source.read_bytes()
    patched = patched_source(raw).encode("utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)
    original_path = output_dir / "dynGENIE3.original.py"
    patched_path = output_dir / "dynGENIE3.py"
    original_path.write_bytes(raw)
    patched_path.write_bytes(patched)
    return {
        "upstream_commit": UPSTREAM_COMMIT,
        "original_source_sha256": hashlib.sha256(raw).hexdigest(),
        "patched_source_sha256": hashlib.sha256(patched).hexdigest(),
    }


def _prepare_upstream_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description="Prepare verified pinned dynGENIE3 source for image construction; no model is fitted."
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--source",
        type=Path,
        help="Optional local source for offline verification; the pinned SHA-256 is always required.",
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            prepare_upstream(output_dir=args.output_dir, source=args.source),
            sort_keys=True,
        )
    )


# Preparation runs before scientific/shared-runtime imports so it also works
# during image construction and offline checks with Python's standard library.
if __name__ == "__main__" and sys.argv[1:2] == ["--prepare-upstream"]:
    _prepare_upstream_cli(sys.argv[2:])
    raise SystemExit(0)


import numpy as np

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
    "alpha": "from_data",
    "tree_method": "RF",
    "K": "sqrt",
    "ntrees": 1000,
    "compute_quality_scores": False,
    "save_models": False,
    "maxcount": "all",
    "seed": 0,
}


def _finite_number(value: Any, label: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{label} must be finite and >= {minimum}")
    return result


def resolve_params(raw: dict[str, Any], ngenes: int) -> dict[str, Any]:
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown method parameters: {sorted(unknown)}")
    params = {**DEFAULTS, **raw}
    alpha = params["alpha"]
    if isinstance(alpha, str):
        if alpha != "from_data":
            raise ValueError(
                "alpha must be from_data, a nonnegative number or a per-gene vector"
            )
    elif isinstance(alpha, list):
        if len(alpha) != ngenes:
            raise ValueError(
                "alpha vector must have one value per expression gene in expression row order"
            )
        params["alpha"] = [_finite_number(value, "alpha") for value in alpha]
    else:
        params["alpha"] = _finite_number(alpha, "alpha")
    if params["tree_method"] not in {"RF", "ET"}:
        raise ValueError("tree_method must be RF or ET")
    features = params["K"]
    if isinstance(features, str):
        if features not in {"sqrt", "all"}:
            raise ValueError("K must be sqrt, all or a positive integer")
    elif isinstance(features, bool) or not isinstance(features, int) or features < 1:
        raise ValueError("K must be sqrt, all or a positive integer")
    for key, minimum, maximum in (("ntrees", 1, None), ("seed", 0, 2**32 - 1)):
        value = params[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < minimum
            or (maximum is not None and value > maximum)
        ):
            raise ValueError(f"{key} must be an integer in the supported domain")
    for key in ("compute_quality_scores", "save_models"):
        if not isinstance(params[key], bool):
            raise ValueError(f"{key} must be boolean")
    maximum = params["maxcount"]
    if maximum != "all" and (
        isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0
    ):
        raise ValueError("maxcount must be all or a nonnegative integer")
    return params


def read_matrix(path: Path) -> tuple[list[str], list[str], np.ndarray]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows or len(rows[0]) < 2 or len(rows) < 2:
        raise ValueError(f"{path.name}: expected genes by samples TSV")
    header = rows[0]
    columns = header[1:]
    if any(not value.strip() or value != value.strip() for value in header) or len(
        set(header)
    ) != len(header):
        raise ValueError(
            f"{path.name}: empty, duplicate or surrounding-whitespace header"
        )
    genes, values = [], []
    for index, row in enumerate(rows[1:], 2):
        if not row:
            continue
        if len(row) != len(header):
            raise ValueError(f"{path.name}: malformed row {index}")
        gene = row[0]
        if not gene.strip() or gene != gene.strip() or gene in genes:
            raise ValueError(
                f"{path.name}: empty, duplicate or surrounding-whitespace gene ID"
            )
        genes.append(gene)
        values.append([float(value) for value in row[1:]])
    matrix = np.asarray(values, dtype=np.float64)
    if not genes or not np.isfinite(matrix).all():
        raise ValueError(f"{path.name}: missing or nonfinite expression value")
    return genes, columns, matrix


def prepare_series(matrix: np.ndarray, columns: list[str], extra: Path):
    """Read observed coordinates and sampling design from canonical metadata."""
    descriptors = load_bulk_column_descriptors(
        extra / "column_descriptors.tsv", columns
    )
    temporal = summarize_temporal_metadata(descriptors)
    if temporal["sampling_design"] != "longitudinal":
        raise ValueError("dynGENIE3 requires longitudinal observed bulk time series")
    observation_ids = [row["observation_id"] for row in descriptors.values()]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError(
            "Technical replicate columns require explicit preprocessing; dynGENIE3 does not average them implicitly"
        )
    times = {
        column: row
        for column, row in descriptors.items()
        if row["observation_type"] == "time_series"
    }
    index = {column: position for position, column in enumerate(columns)}
    grouped: dict[str, list[tuple[float, str]]] = {}
    observations = set()
    for column in columns:
        if column not in times:
            continue
        time_row = times[column]
        observation = time_row["observation_id"]
        if observation in observations:
            raise ValueError(
                "Technical replicate columns require explicit preprocessing; dynGENIE3 does not average them implicitly"
            )
        observations.add(observation)
        time = float(time_row["timepoint"])
        if not math.isfinite(time):
            raise ValueError("Observed times must be finite")
        grouped.setdefault(time_row["series_id"], []).append((time, column))
    series_data, time_points, resolved = [], [], []
    for series, entries in sorted(grouped.items()):
        entries.sort()
        if len(entries) < 2:
            raise ValueError(
                "Each longitudinal series needs at least two distinct observed times"
            )
        ids = [column for _, column in entries]
        biological_units = {times[column]["replicate"] for column in ids}
        conditions = {times[column]["condition"] for column in ids}
        if len(biological_units) != 1 or len(conditions) != 1:
            raise ValueError(
                "One longitudinal series must identify one biological unit and one condition"
            )
        coordinates = np.asarray([time for time, _ in entries], dtype=np.float64)
        # Preserve the selected upstream float32 time conversion, rejecting loss.
        with np.errstate(over="ignore", invalid="ignore"):
            upstream_times = coordinates.astype(np.float32)
        with np.errstate(over="ignore", invalid="ignore"):
            upstream_intervals = np.diff(upstream_times)
        if (
            not np.isfinite(upstream_times).all()
            or not np.isfinite(upstream_intervals).all()
            or not np.all(upstream_intervals > 0)
        ):
            raise ValueError(
                "Observed times must remain strictly increasing at upstream float32 precision"
            )
        series_data.append(matrix[:, [index[column] for column in ids]].T.copy())
        time_points.append(coordinates)
        resolved.append(
            {
                "series_id": series,
                "condition": next(iter(conditions)),
                "replicate": next(iter(biological_units)),
                "columns": ids,
                "timepoints": coordinates.tolist(),
                "upstream_timepoints": upstream_times.tolist(),
            }
        )
    if not series_data:
        raise ValueError("At least one time series is required")
    return series_data, time_points, resolved


def validate_decay_time_separations(series_data, time_points) -> None:
    """The native rate estimator subtracts the times of each gene's extrema.

    Finite adjacent intervals alone do not prevent that longer float32
    subtraction from overflowing. Constant genes remain valid with rate zero.
    """
    for series_index, (values, coordinates) in enumerate(zip(series_data, time_points)):
        low, high = np.argmin(values, axis=0), np.argmax(values, axis=0)
        gene_indices = np.arange(values.shape[1])
        varying = values[low, gene_indices] != values[high, gene_indices]
        native_time = np.asarray(coordinates, dtype=np.float32)
        with np.errstate(over="ignore", invalid="ignore"):
            separations = np.abs(native_time[low] - native_time[high])
        if not np.all(np.isfinite(separations[varying]) & (separations[varying] > 0)):
            raise ValueError(
                f"Series {series_index}: data-derived degradation extrema must have finite positive time separation at upstream float32 precision"
            )


def export_network(
    path: Path, vim: np.ndarray, genes: list[str], maxcount: str | int
) -> int:
    vim = np.asarray(vim)
    if (
        vim.shape != (len(genes), len(genes))
        or not np.isfinite(vim).all()
        or np.any(vim < 0)
    ):
        raise ValueError(
            "Upstream VIM must be a finite nonnegative gene-by-gene matrix"
        )
    rows = [
        (genes[source], genes[target], float(vim[source, target]))
        for source, target in np.ndindex(vim.shape)
        if source != target and vim[source, target] > 0
    ]
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


def _write_json(path: Path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


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
    write_progress(
        progress,
        status="running",
        percent=0,
        phase="validate",
        message="Validating observed bulk time series",
    )
    try:
        load_execution_mode(params_path, supported_modes={"global"})
        genes, columns, expression = read_matrix(input_path)
        params = resolve_params(load_params(params_path), len(genes))
        ts_data, time_points, series = prepare_series(expression, columns, extra_dir)
        if isinstance(params["alpha"], str) and params["alpha"] == "from_data":
            validate_decay_time_separations(ts_data, time_points)
        descriptors = load_bulk_column_descriptors(
            extra_dir / "column_descriptors.tsv", columns
        )
        ss_columns = [
            column
            for column in columns
            if descriptors[column]["observation_type"] == "steady_state"
        ]
        ss_positions = [columns.index(column) for column in ss_columns]
        ss_data = expression[:, ss_positions].T.copy() if ss_columns else None
        tf_path = extra_dir / "tf_list.txt"
        regulators = "all"
        if tf_path.is_file():
            names = tf_path.read_text(encoding="utf-8").splitlines()
            names = [name for name in names if name.strip()]
            if (
                not names
                or len(names) != len(set(names))
                or not set(names) <= set(genes)
            ):
                raise ValueError(
                    "tf_list must contain unique, exact expression gene IDs"
                )
            regulators = [gene for gene in genes if gene in set(names)]
        raw = output_dir / "provenance"
        raw.mkdir(exist_ok=True)
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
            "steady_state_columns": ss_columns,
            "threads": threads,
            "effective_workers": min(threads, len(genes)),
            "target_seeds": [
                int(
                    np.random.SeedSequence([params["seed"], target]).generate_state(1)[
                        0
                    ]
                )
                for target in range(len(genes))
            ],
            "input_sha256": {
                str(path.name): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [input_path, *sorted(extra_dir.iterdir())]
                if path.is_file()
            },
        }
        _write_json(raw / "resolved.json", resolved)
        write_progress(
            progress,
            status="running",
            percent=10,
            phase="inference",
            message="Running pinned dynGENIE3 Python",
        )
        implementation = (
            upstream if upstream is not None else importlib.import_module("dynGENIE3")
        )
        candidate_count = len(genes) if regulators == "all" else len(regulators)
        features = params["K"]
        max_features = (
            1.0
            if features == "all"
            or isinstance(features, int)
            and features >= candidate_count
            else features
        )
        resolved["max_features_argument"] = max_features
        resolved["max_features_count"] = (
            max(1, int(math.sqrt(candidate_count)))
            if features == "sqrt"
            else (
                candidate_count
                if features == "all"
                or isinstance(features, int)
                and features >= candidate_count
                else features
            )
        )
        resolved["effective_training_rows"] = sum(
            len(value) - 1 for value in time_points
        ) + len(ss_columns)
        forest_class = getattr(
            implementation,
            (
                "RandomForestRegressor"
                if params["tree_method"] == "RF"
                else "ExtraTreesRegressor"
            ),
            None,
        )
        if forest_class is not None:
            forest = forest_class(
                n_estimators=params["ntrees"],
                max_features=max_features,
                oob_score=params["compute_quality_scores"]
                and params["tree_method"] == "RF",
                random_state=resolved["target_seeds"][0],
                n_jobs=1,
            )
            resolved["forest_constructor_params"] = forest.get_params(deep=False)
            resolved["forest_random_states"] = resolved["target_seeds"]
        _write_json(raw / "resolved.json", resolved)
        previous_resource_dir = os.environ.get("ANDREA_RESOURCE_REPORT_DIR")
        os.environ["ANDREA_RESOURCE_REPORT_DIR"] = str(raw.resolve())
        try:
            vim, alphas, prediction, stability, models = implementation.dynGENIE3(
                TS_data=ts_data,
                time_points=time_points,
                alpha=params["alpha"],
                SS_data=ss_data,
                gene_names=genes,
                regulators=regulators,
                tree_method=params["tree_method"],
                K=params["K"],
                ntrees=params["ntrees"],
                compute_quality_scores=params["compute_quality_scores"],
                save_models=params["save_models"],
                nthreads=resolved["effective_workers"],
                random_state=params["seed"],
            )
        finally:
            if previous_resource_dir is None:
                os.environ.pop("ANDREA_RESOURCE_REPORT_DIR", None)
            else:
                os.environ["ANDREA_RESOURCE_REPORT_DIR"] = previous_resource_dir
        alphas = np.asarray(alphas, dtype=float)
        if (
            alphas.shape != (len(genes),)
            or not np.isfinite(alphas).all()
            or np.any(alphas < 0)
        ):
            raise ValueError(
                "Upstream degradation rates must be finite, nonnegative and aligned to genes"
            )
        resolved["alpha_resolved"] = alphas.tolist()
        _write_json(raw / "resolved.json", resolved)
        np.save(raw / "VIM.npy", vim, allow_pickle=False)
        with (raw / "degradation_rates.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["gene", "alpha", "inverse_time_unit"])
            writer.writerows(
                (gene, repr(float(alpha)), f"1/{resolved['temporal']['time_unit']}")
                for gene, alpha in zip(genes, alphas)
            )
        messages = []
        quality = {"requested": params["compute_quality_scores"]}
        for name, value in (
            ("prediction_score", prediction),
            ("stability_score", stability),
        ):
            if isinstance(value, (list, tuple)) and not value:
                quality[name] = None
            elif not np.isfinite(float(value)):
                quality[name] = None
                messages.append(
                    f"Upstream {name} is undefined for this configuration; retained as null, without inventing a score."
                )
            else:
                quality[name] = float(value)
        _write_json(raw / "quality_scores.json", quality)
        if params["save_models"]:
            if not isinstance(models, (list, tuple)) or len(models) != len(genes):
                raise ValueError(
                    "save_models requires one upstream fitted model per target gene"
                )
            import joblib

            joblib.dump(models, raw / "tree_models.joblib")
        count = export_network(
            output_dir / "network.csv", vim, genes, params["maxcount"]
        )
        if not count:
            messages.append(
                "Upstream retained no positive non-self interactions under the requested export policy."
            )
        session = {
            "upstream_commit": UPSTREAM_COMMIT,
            "packages": {},
            "notes": "Pinned implementation with reviewed compatibility and per-target random-state patches.",
        }
        for package in ("numpy", "scipy", "scikit-learn", "joblib", "threadpoolctl"):
            try:
                session["packages"][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                session["packages"][package] = "not installed in this test environment"
        if getattr(implementation, "__file__", None):
            session["patched_source_sha256"] = hashlib.sha256(
                Path(implementation.__file__).read_bytes()
            ).hexdigest()
        _write_json(raw / "session.json", session)
        write_progress(
            progress,
            status="completed",
            percent=100,
            phase="done",
            message="dynGENIE3 network exported",
            warnings=messages,
        )
        return resolved
    except Exception as exc:
        write_progress(
            progress,
            status="failed",
            percent=100,
            phase="failed",
            message="dynGENIE3 failed",
            error=str(exc),
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
