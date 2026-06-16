"""CeSpGRN wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from _run_tool_common import (
    load_params,
    require_extra_file,
    require_param_keys,
    validate_runtime_inputs,
    warn_unknown_params,
    write_progress,
)


NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
SUPPORTED_MODES = {"cell_native", "group_aggregated"}


@dataclass(frozen=True)
class ResolvedParams:
    kernel_source: str
    prior_mode: str
    expression_preprocessing: str
    pca_components: int
    bandwidth: float
    n_neigh: int
    lamb: float
    prior_beta: float
    max_iters: int
    n_intervals: int
    batch_size: int | None
    random_seed: int | None


@dataclass(frozen=True)
class ExpressionInput:
    counts: np.ndarray
    gene_ids: list[str]
    cell_ids: list[str]


def _as_enum(name: str, value: Any, allowed: set[str]) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    normalized = value.strip()
    if normalized not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}.")
    return normalized


def _as_int(name: str, value: Any, *, min_value: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}.")
    return int(value)


def _as_int_or_none(
    name: str,
    value: Any,
    *,
    min_value: int | None = None,
) -> int | None:
    if value is None:
        return None
    return _as_int(name, value, min_value=min_value)


def _as_float(
    name: str,
    value: Any,
    *,
    min_value: float | None = None,
    exclusive_min: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric.")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite.")
    if min_value is not None:
        if exclusive_min and out <= min_value:
            raise ValueError(f"{name} must be > {min_value}.")
        if not exclusive_min and out < min_value:
            raise ValueError(f"{name} must be >= {min_value}.")
    return out


def resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    expected = {
        "kernel_source",
        "prior_mode",
        "expression_preprocessing",
        "pca_components",
        "bandwidth",
        "n_neigh",
        "lamb",
        "prior_beta",
        "max_iters",
        "n_intervals",
        "batch_size",
        "random_seed",
    }
    require_param_keys(raw_params, expected)
    warn_unknown_params(raw_params, expected)

    return ResolvedParams(
        kernel_source=_as_enum(
            "kernel_source", raw_params["kernel_source"], {"expression", "spatial"}
        ),
        prior_mode=_as_enum("prior_mode", raw_params["prior_mode"], {"none", "tf_list"}),
        expression_preprocessing=_as_enum(
            "expression_preprocessing",
            raw_params["expression_preprocessing"],
            {"library_size_log1p", "none"},
        ),
        pca_components=_as_int("pca_components", raw_params["pca_components"], min_value=1),
        bandwidth=_as_float(
            "bandwidth", raw_params["bandwidth"], min_value=0.0, exclusive_min=True
        ),
        n_neigh=_as_int("n_neigh", raw_params["n_neigh"], min_value=1),
        lamb=_as_float("lamb", raw_params["lamb"], min_value=0.0, exclusive_min=True),
        prior_beta=_as_float("prior_beta", raw_params["prior_beta"], min_value=0.0),
        max_iters=_as_int("max_iters", raw_params["max_iters"], min_value=1),
        n_intervals=_as_int("n_intervals", raw_params["n_intervals"], min_value=1),
        batch_size=_as_int_or_none("batch_size", raw_params["batch_size"], min_value=1),
        random_seed=_as_int_or_none("random_seed", raw_params["random_seed"]),
    )


def load_execution_mode(params_path: Path) -> str:
    execution_path = params_path.parent / "execution.json"
    if not execution_path.exists():
        return "cell_native"
    with execution_path.open("r", encoding="utf-8") as fh:
        execution = json.load(fh)
    if not isinstance(execution, dict):
        raise ValueError("execution.json must be a JSON object.")
    mode = execution.get("mode", "cell_native")
    if not isinstance(mode, str):
        raise ValueError("execution.mode must be a string.")
    if mode not in SUPPORTED_MODES:
        raise ValueError(
            "CeSpGRN supports only execution.mode=cell_native or "
            "execution.mode=group_aggregated."
        )
    return mode


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        line = fh.readline()
    if not line:
        raise ValueError(f"{path.name} is empty.")
    return line.rstrip("\n\r").split("\t")


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def read_expression_tsv(path: Path) -> ExpressionInput:
    header = _read_header(path)
    if len(header) < 2:
        raise ValueError("expression.tsv must have a gene column and at least one cell.")

    cell_ids = [str(value) for value in header[1:]]
    if any(not value for value in cell_ids):
        raise ValueError("expression.tsv contains an empty cell identifier.")
    duplicated_cells = _duplicates(cell_ids)
    if duplicated_cells:
        raise ValueError(
            "expression.tsv contains duplicated cell identifiers: "
            + ", ".join(duplicated_cells)
        )

    raw = pd.read_csv(path, sep="\t", header=0, dtype=str, keep_default_na=False)
    if raw.shape[1] != len(header):
        raise ValueError("expression.tsv rows do not match the header width.")

    gene_ids = raw.iloc[:, 0].astype(str).tolist()
    if any(not value for value in gene_ids):
        raise ValueError("expression.tsv contains an empty gene identifier.")
    duplicated_genes = _duplicates(gene_ids)
    if duplicated_genes:
        raise ValueError(
            "expression.tsv contains duplicated gene identifiers: "
            + ", ".join(duplicated_genes)
        )

    numeric = raw.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError("expression.tsv contains non-numeric expression values.")

    genes_by_cells = numeric.to_numpy(dtype=float)
    if not np.isfinite(genes_by_cells).all():
        raise ValueError("expression.tsv contains non-finite expression values.")

    counts = genes_by_cells.T.copy()
    return ExpressionInput(counts=counts, gene_ids=gene_ids, cell_ids=cell_ids)


def preprocess_expression(counts: np.ndarray, mode: str) -> np.ndarray:
    if mode == "none":
        return counts.astype(float, copy=True)

    if np.any(counts < 0):
        raise ValueError(
            "expression_preprocessing=library_size_log1p requires non-negative values."
        )
    library_sizes = counts.sum(axis=1)
    if np.any(library_sizes <= 0):
        raise ValueError(
            "expression_preprocessing=library_size_log1p requires every cell to have positive library size."
        )
    median_library_size = float(np.median(library_sizes))
    if median_library_size <= 0 or not math.isfinite(median_library_size):
        raise ValueError("Median library size is not positive and finite.")
    normalized = counts / library_sizes[:, None] * median_library_size
    return np.log1p(normalized)


def validate_groups(extra_dir: Path, cell_ids: list[str]) -> None:
    path = require_extra_file(extra_dir, "groups.tsv", "groups")
    header = _read_header(path)
    if len(header) < 2 or "cluster" not in header[1:]:
        raise ValueError("groups.tsv must contain a first cell id column and a cluster column.")

    raw = pd.read_csv(path, sep="\t", header=0, dtype=str, keep_default_na=False)
    id_col = raw.columns[0]
    group_cell_ids = raw[id_col].astype(str).tolist()
    if any(not value for value in group_cell_ids):
        raise ValueError("groups.tsv contains an empty cell identifier.")
    duplicated = _duplicates(group_cell_ids)
    if duplicated:
        raise ValueError("groups.tsv contains duplicated cell identifiers: " + ", ".join(duplicated))
    missing = [cell_id for cell_id in cell_ids if cell_id not in group_cell_ids]
    extra = [cell_id for cell_id in group_cell_ids if cell_id not in cell_ids]
    if missing or extra:
        details = []
        if missing:
            details.append("missing cells: " + ", ".join(missing))
        if extra:
            details.append("unknown cells: " + ", ".join(extra))
        raise ValueError("groups.tsv must match expression cells exactly (" + "; ".join(details) + ").")
    clusters = raw.set_index(id_col).loc[cell_ids, "cluster"].astype(str)
    if (clusters == "").any():
        raise ValueError("groups.tsv contains empty cluster values.")


def load_tf_indices(extra_dir: Path, gene_ids: list[str]) -> list[int]:
    path = require_extra_file(extra_dir, "tf_list.txt", "tf_list")
    tfs = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    tfs = [tf for tf in tfs if tf]
    if not tfs:
        raise ValueError("tf_list.txt must contain at least one TF gene id.")
    duplicated = _duplicates(tfs)
    if duplicated:
        raise ValueError("tf_list.txt contains duplicated TF gene ids: " + ", ".join(duplicated))
    gene_index = {gene_id: idx for idx, gene_id in enumerate(gene_ids)}
    unknown = [tf for tf in tfs if tf not in gene_index]
    if unknown:
        raise ValueError("tf_list.txt contains TF ids absent from expression.tsv: " + ", ".join(unknown))
    return [gene_index[tf] for tf in tfs]


def load_spatial_coordinates(extra_dir: Path, cell_ids: list[str]) -> np.ndarray:
    path = require_extra_file(extra_dir, "spatial_coordinates.tsv", "spatial_coordinates")
    header = _read_header(path)
    if len(header) < 3 or "x" not in header[1:] or "y" not in header[1:]:
        raise ValueError(
            "spatial_coordinates.tsv must contain a first cell id column and x/y columns."
        )

    raw = pd.read_csv(path, sep="\t", header=0, dtype=str, keep_default_na=False)
    id_col = raw.columns[0]
    coord_cols = list(raw.columns[1:])
    spatial_cell_ids = raw[id_col].astype(str).tolist()
    if any(not value for value in spatial_cell_ids):
        raise ValueError("spatial_coordinates.tsv contains an empty cell identifier.")
    duplicated = _duplicates(spatial_cell_ids)
    if duplicated:
        raise ValueError(
            "spatial_coordinates.tsv contains duplicated cell identifiers: "
            + ", ".join(duplicated)
        )
    missing = [cell_id for cell_id in cell_ids if cell_id not in spatial_cell_ids]
    extra = [cell_id for cell_id in spatial_cell_ids if cell_id not in cell_ids]
    if missing or extra:
        details = []
        if missing:
            details.append("missing cells: " + ", ".join(missing))
        if extra:
            details.append("unknown cells: " + ", ".join(extra))
        raise ValueError(
            "spatial_coordinates.tsv must match expression cells exactly ("
            + "; ".join(details)
            + ")."
        )

    numeric = raw.set_index(id_col).loc[cell_ids, coord_cols].apply(
        pd.to_numeric,
        errors="coerce",
    )
    if numeric.isna().any().any():
        raise ValueError("spatial_coordinates.tsv contains non-numeric coordinates.")
    coords = numeric.to_numpy(dtype=float)
    if not np.isfinite(coords).all():
        raise ValueError("spatial_coordinates.tsv contains non-finite coordinates.")
    if np.allclose(coords, coords[0, :]):
        raise ValueError("spatial_coordinates.tsv coordinates are all identical.")
    return coords


def resolve_kernel_coordinates(
    *,
    expression: ExpressionInput,
    counts: np.ndarray,
    params: ResolvedParams,
    extra_dir: Path,
) -> np.ndarray:
    if params.kernel_source == "spatial":
        return load_spatial_coordinates(extra_dir, expression.cell_ids)

    max_components = min(counts.shape[0], counts.shape[1])
    if params.pca_components > max_components:
        raise ValueError(
            "pca_components must be <= min(number of cells, number of genes); "
            f"got {params.pca_components} > {max_components}."
        )
    from sklearn.decomposition import PCA  # noqa: PLC0415

    coords = PCA(n_components=params.pca_components).fit_transform(counts)
    if np.allclose(coords, coords[0, :]):
        raise ValueError("PCA coordinates are all identical.")
    return coords


def validate_upstream_shape(expression: ExpressionInput, params: ResolvedParams) -> None:
    ncells = len(expression.cell_ids)
    if ncells <= 5:
        raise ValueError(
            "CeSpGRN's public kernel path starts kNN search at k=5 and requires at least 6 cells."
        )
    if params.n_neigh > ncells:
        raise ValueError(f"n_neigh must be <= number of cells; got {params.n_neigh} > {ncells}.")
    if params.batch_size is None and ncells < 10:
        raise ValueError(
            "batch_size=null preserves CeSpGRN's upstream int(ncells/10) rule, "
            "which would be zero for fewer than 10 cells. Provide an explicit batch_size."
        )


def append_log(log_path: Path, message: str) -> None:
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(message.rstrip() + "\n")


def write_id_table(raw_dir: Path, expression: ExpressionInput) -> None:
    with (raw_dir / "ids.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["kind", "index", "id"])
        for idx, cell_id in enumerate(expression.cell_ids):
            writer.writerow(["cell", idx, cell_id])
        for idx, gene_id in enumerate(expression.gene_ids):
            writer.writerow(["gene", idx, gene_id])


def patch_torch_eig(torch_module: Any) -> None:
    def _eig_compat(input_tensor: Any, eigenvectors: bool = False) -> tuple[Any, Any]:
        if eigenvectors:
            eigenvalues, eigenvectors_tensor = torch_module.linalg.eig(input_tensor)
            eigenvalues_ri = torch_module.view_as_real(eigenvalues).to(input_tensor.dtype)
            return eigenvalues_ri, eigenvectors_tensor.real.to(input_tensor.dtype)

        eigenvalues = torch_module.linalg.eigvals(input_tensor)
        eigenvalues_ri = torch_module.view_as_real(eigenvalues).to(input_tensor.dtype)
        empty = torch_module.empty(
            0,
            dtype=input_tensor.dtype,
            device=input_tensor.device,
        )
        return eigenvalues_ri, empty

    torch_module.eig = _eig_compat


def run_cespgrn(
    *,
    expression: ExpressionInput,
    counts: np.ndarray,
    kernel_coordinates: np.ndarray,
    params: ResolvedParams,
    tf_indices: list[int] | None,
    raw_dir: Path,
    log_path: Path,
    threads: int,
) -> np.ndarray:
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    os.environ.setdefault("NUMEXPR_NUM_THREADS", str(threads))

    import torch  # noqa: PLC0415

    torch.set_num_threads(threads)
    patch_torch_eig(torch)

    import g_admm as CeSpGRN  # noqa: PLC0415
    import kernel  # noqa: PLC0415

    with log_path.open("a", encoding="utf-8") as log_fh:
        with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
            K, K_trun = kernel.calc_kernel_neigh(
                kernel_coordinates,
                k=5,
                bandwidth=params.bandwidth,
                truncate=True,
                truncate_param=params.n_neigh,
            )
            if not np.isfinite(K).all() or not np.isfinite(K_trun).all():
                raise RuntimeError("CeSpGRN kernel construction produced non-finite values.")
            np.save(raw_dir / "kernel.npy", K)
            np.save(raw_dir / "kernel_truncated.npy", K_trun)

            empir_cov = CeSpGRN.est_cov(X=counts, K_trun=K_trun, weighted_kt=True)
            if not np.isfinite(empir_cov).all():
                raise RuntimeError("CeSpGRN covariance estimation produced non-finite values.")

            model_kwargs: dict[str, Any] = {
                "X": counts[:, None, :],
                "K": K,
                "pre_cov": empir_cov,
                "seed": params.random_seed,
            }
            if params.batch_size is not None:
                model_kwargs["batchsize"] = params.batch_size
            if tf_indices is not None:
                model_kwargs["TF"] = tf_indices

            model = CeSpGRN.G_admm_minibatch(**model_kwargs)
            if model.batchsize <= 0:
                raise RuntimeError(
                    "CeSpGRN resolved a non-positive batch size from the upstream default."
                )

            beta = params.prior_beta if params.prior_mode == "tf_list" else 0.0
            partial_correlations = model.train(
                max_iters=params.max_iters,
                n_intervals=params.n_intervals,
                lamb=params.lamb,
                beta=beta,
                njobs=max(1, int(threads)),
            )
            np.save(raw_dir / "precision_matrices.npy", model.thetas)
            np.save(raw_dir / "partial_correlations.npy", partial_correlations)

    if partial_correlations.shape != (
        len(expression.cell_ids),
        len(expression.gene_ids),
        len(expression.gene_ids),
    ):
        raise RuntimeError(
            "CeSpGRN returned an unexpected partial-correlation tensor shape: "
            f"{partial_correlations.shape!r}."
        )
    if not np.isfinite(partial_correlations).all():
        raise RuntimeError("CeSpGRN partial correlations contain non-finite values.")
    return partial_correlations


def write_network_csv(
    *,
    path: Path,
    partial_correlations: np.ndarray,
    gene_ids: list[str],
    cell_ids: list[str],
) -> int:
    rows_written = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=NETWORK_COLUMNS)
        writer.writeheader()
        for cell_idx, cell_id in enumerate(cell_ids):
            context = f"cell:{cell_id}"
            matrix = partial_correlations[cell_idx]
            for source_idx in range(len(gene_ids) - 1):
                source = gene_ids[source_idx]
                for target_idx in range(source_idx + 1, len(gene_ids)):
                    value = float(matrix[source_idx, target_idx])
                    score = abs(value)
                    if score <= 0.0:
                        continue
                    writer.writerow(
                        {
                            "source": source,
                            "target": gene_ids[target_idx],
                            "score": f"{score:.17g}",
                            "sign": "+" if value > 0.0 else "-",
                            "evidence": "association",
                            "context": context,
                        }
                    )
                    rows_written += 1
    return rows_written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--extra", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--threads", required=True, type=int)
    args = parser.parse_args()

    validate_runtime_inputs(
        input_path=args.input,
        params_path=args.params,
        extra_dir=args.extra,
        threads=args.threads,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = args.output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    progress_path = args.output_dir / "progress.json"
    log_path = args.output_dir / "cespgrn.log"
    append_log(log_path, "Starting CeSpGRN wrapper.")
    write_progress(
        progress_path,
        status="running",
        percent=0,
        phase="init",
        message="Initializing",
    )

    try:
        raw_params = load_params(args.params)
        params = resolve_params(raw_params)
        mode = load_execution_mode(args.params)

        write_progress(
            progress_path,
            status="running",
            percent=5,
            phase="load_input",
            message="Loading expression and extra inputs",
        )
        expression = read_expression_tsv(args.input)
        validate_upstream_shape(expression, params)
        if mode == "group_aggregated":
            validate_groups(args.extra, expression.cell_ids)
        tf_indices = (
            load_tf_indices(args.extra, expression.gene_ids)
            if params.prior_mode == "tf_list"
            else None
        )
        write_id_table(raw_dir, expression)

        append_log(
            log_path,
            (
                f"Loaded {len(expression.cell_ids)} cells and "
                f"{len(expression.gene_ids)} genes; mode={mode}; "
                f"kernel_source={params.kernel_source}; prior_mode={params.prior_mode}."
            ),
        )

        write_progress(
            progress_path,
            status="running",
            percent=12,
            phase="preprocess",
            message="Preprocessing expression",
        )
        counts = preprocess_expression(expression.counts, params.expression_preprocessing)
        append_log(log_path, f"Expression preprocessing: {params.expression_preprocessing}.")

        write_progress(
            progress_path,
            status="running",
            percent=20,
            phase="kernel",
            message="Resolving kernel coordinates",
        )
        kernel_coordinates = resolve_kernel_coordinates(
            expression=expression,
            counts=counts,
            params=params,
            extra_dir=args.extra,
        )
        np.save(raw_dir / "kernel_coordinates.npy", kernel_coordinates)

        write_progress(
            progress_path,
            status="running",
            percent=35,
            phase="inference",
            message="Running CeSpGRN",
        )
        partial_correlations = run_cespgrn(
            expression=expression,
            counts=counts,
            kernel_coordinates=kernel_coordinates,
            params=params,
            tf_indices=tf_indices,
            raw_dir=raw_dir,
            log_path=log_path,
            threads=args.threads,
        )

        write_progress(
            progress_path,
            status="running",
            percent=92,
            phase="write_output",
            message="Writing network.csv",
        )
        row_count = write_network_csv(
            path=args.output_dir / "network.csv",
            partial_correlations=partial_correlations,
            gene_ids=expression.gene_ids,
            cell_ids=expression.cell_ids,
        )
        if row_count <= 0:
            raise RuntimeError("CeSpGRN produced no non-zero partial-correlation edges.")

        write_progress(
            progress_path,
            status="completed",
            percent=100,
            phase="done",
            message="Inference finished",
            completed=row_count,
            total=row_count,
        )
        append_log(log_path, f"Wrote {row_count} positive network rows.")
    except Exception as exc:
        append_log(log_path, "ERROR: " + str(exc))
        append_log(log_path, traceback.format_exc())
        write_progress(
            progress_path,
            status="failed",
            percent=100,
            phase="failed",
            message="Inference failed",
            error=str(exc),
        )
        raise


if __name__ == "__main__":
    main()
