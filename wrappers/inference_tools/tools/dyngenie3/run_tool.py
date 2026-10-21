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
from pathlib import Path
from typing import Any

import numpy as np

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
UPSTREAM_COMMIT = "27eddd960d50e82791a8e0e5f8fcf6d679fe3eac"


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


def _metadata(
    path: Path, columns: list[str], required: set[str]
) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headers = reader.fieldnames or []
        if len(set(headers)) != len(headers) or not required <= set(headers):
            raise ValueError(f"{path.name}: missing or duplicate metadata headings")
        result = {}
        for row in reader:
            if None in row or any(
                not isinstance(row.get(key), str) or not row[key].strip()
                for key in required
            ):
                raise ValueError(f"{path.name}: malformed or empty metadata")
            column = row["column"]
            if column in result:
                raise ValueError(
                    f"{path.name}: duplicate expression-column ID {column}"
                )
            result[column] = row
    if set(result) != set(columns) or len(result) != len(columns):
        raise ValueError(f"{path.name}: metadata must match expression columns exactly")
    return result


def prepare_series(
    matrix: np.ndarray, columns: list[str], extra: Path, temporal: dict[str, Any]
):
    """Only method-specific preparation; core validates the canonical contract."""
    expected = {
        "schema_version",
        "time_basis",
        "time_unit",
        "time_origin",
        "sampling_design",
        "missing_values",
        "preprocessing",
        "provenance",
    }
    if not isinstance(temporal, dict) or set(temporal) != expected:
        raise ValueError(
            "execution.temporal must contain the canonical bulk-temporal/1 declaration"
        )
    if (
        temporal["schema_version"] != "bulk-temporal/1"
        or temporal["time_basis"] != "observed"
        or temporal["sampling_design"] != "longitudinal"
        or temporal["missing_values"] != "reject"
    ):
        raise ValueError(
            "dynGENIE3 requires observed longitudinal bulk time with missing_values=reject"
        )
    for field in ("time_unit", "time_origin", "provenance"):
        if not isinstance(temporal[field], str) or not temporal[field].strip():
            raise ValueError(f"temporal.{field} must be explicit")
    preprocessing = temporal["preprocessing"]
    if (
        not isinstance(preprocessing, dict)
        or set(preprocessing) != {"normalization", "transformation"}
        or any(
            not isinstance(value, str) or not value.strip()
            for value in preprocessing.values()
        )
    ):
        raise ValueError(
            "temporal.preprocessing must declare normalization and transformation"
        )
    times = _metadata(
        extra / "timepoints.tsv",
        columns,
        {
            "column",
            "timepoint",
            "time_unit",
            "series_id",
            "condition",
            "observation_id",
        },
    )
    replicates = _metadata(
        extra / "replicates.tsv",
        columns,
        {"column", "replicate", "replicate_kind", "technical_replicate"},
    )
    index = {column: position for position, column in enumerate(columns)}
    grouped: dict[str, list[tuple[float, str]]] = {}
    observations = set()
    for column in columns:
        time_row, replica = times[column], replicates[column]
        if time_row["time_unit"] != temporal["time_unit"]:
            raise ValueError("Observed time units must match the temporal declaration")
        if replica["replicate_kind"] != "biological":
            raise ValueError("replicate must identify a biological longitudinal unit")
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
        biological_units = {replicates[column]["replicate"] for column in ids}
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


def load_steady_state(path: Path, genes: list[str]) -> tuple[np.ndarray, list[str]]:
    source_genes, columns, matrix = read_matrix(path)
    if set(source_genes) != set(genes):
        raise ValueError(
            "Steady-state and time-series gene universes must match exactly"
        )
    positions = {gene: index for index, gene in enumerate(source_genes)}
    return matrix[[positions[gene] for gene in genes], :].T.copy(), columns


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
        execution = json.loads(params_path.with_name("execution.json").read_text())
        genes, columns, expression = read_matrix(input_path)
        params = resolve_params(load_params(params_path), len(genes))
        ts_data, time_points, series = prepare_series(
            expression, columns, extra_dir, execution.get("temporal")
        )
        if isinstance(params["alpha"], str) and params["alpha"] == "from_data":
            validate_decay_time_separations(ts_data, time_points)
        ss_path = extra_dir / "steady_state_expression.tsv"
        ss_data, ss_columns = (
            load_steady_state(ss_path, genes) if ss_path.is_file() else (None, [])
        )
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
            "temporal": execution["temporal"],
            "series": series,
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
                (gene, repr(float(alpha)), f"1/{execution['temporal']['time_unit']}")
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
