"""scRegulate wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import os
import random
import traceback
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
for _thread_env in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_thread_env, "1")

import anndata as ad
import numpy as np
import pandas as pd
import scanpy as sc
import torch

from scregulate import train_model
from scregulate.fine_tuning import fine_tune_clusters

from _run_tool_common import (
    load_params,
    require_extra_file,
    require_param_keys,
    validate_runtime_inputs,
    warn_unknown_params,
    write_progress,
)


NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
SUPPORTED_MODES = {"global", "group_native", "group_emulated"}
SCREGULATE_PRIORS_DIR = Path(os.environ.get("SCREGULATE_PRIORS_DIR", "/opt/scregulate_priors"))


@dataclass(frozen=True)
class ResolvedParams:
    prior_source: str
    epochs: int
    freeze_epochs: int
    train_val_split_ratio: float
    batch_size: int | None
    learning_rate: float
    alpha_max: float
    alpha_scale: float
    beta_max: float
    gamma_max: float
    early_stopping_patience: int
    min_targets: int
    min_TFs: int
    fine_tune_epochs: int
    fine_tune_batch_size: int
    fine_tune_min_epochs: int
    fine_tune_beta_max: float
    fine_tune_max_weight_norm: float
    fine_tune_early_stopping_patience: int
    random_state: int


@dataclass(frozen=True)
class ExpressionInput:
    values: pd.DataFrame
    gene_ids: list[str]
    column_ids: list[str]


@dataclass(frozen=True)
class GroupInfo:
    groups: dict[str, str]

    @property
    def unique_groups(self) -> list[str]:
        return sorted(set(self.groups.values()))


def _as_int(name: str, value: Any, *, min_value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value < min_value:
        raise ValueError(f"{name} must be >= {min_value}.")
    return int(value)


def _as_optional_int(name: str, value: Any, *, min_value: int) -> int | None:
    if value is None:
        return None
    return _as_int(name, value, min_value=min_value)


def _as_float(
    name: str,
    value: Any,
    *,
    min_value: float | None = None,
    exclusive_min: bool = False,
    max_value: float | None = None,
    exclusive_max: bool = False,
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
    if max_value is not None:
        if exclusive_max and out >= max_value:
            raise ValueError(f"{name} must be < {max_value}.")
        if not exclusive_max and out > max_value:
            raise ValueError(f"{name} must be <= {max_value}.")
    return out


def _resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    expected = {
        "prior_source",
        "epochs",
        "freeze_epochs",
        "train_val_split_ratio",
        "batch_size",
        "learning_rate",
        "alpha_max",
        "alpha_scale",
        "beta_max",
        "gamma_max",
        "early_stopping_patience",
        "min_targets",
        "min_TFs",
        "fine_tune_epochs",
        "fine_tune_batch_size",
        "fine_tune_min_epochs",
        "fine_tune_beta_max",
        "fine_tune_max_weight_norm",
        "fine_tune_early_stopping_patience",
        "random_state",
    }
    require_param_keys(raw_params, expected)
    warn_unknown_params(raw_params, expected)

    prior_source = raw_params["prior_source"]
    if prior_source not in {"collectri_human", "collectri_mouse", "provided_prior"}:
        raise ValueError(
            "prior_source must be one of: collectri_human, collectri_mouse, provided_prior."
        )

    return ResolvedParams(
        prior_source=str(prior_source),
        epochs=_as_int("epochs", raw_params["epochs"], min_value=1),
        freeze_epochs=_as_int("freeze_epochs", raw_params["freeze_epochs"], min_value=20),
        train_val_split_ratio=_as_float(
            "train_val_split_ratio",
            raw_params["train_val_split_ratio"],
            min_value=0.0,
            exclusive_min=True,
            max_value=1.0,
            exclusive_max=True,
        ),
        batch_size=_as_optional_int("batch_size", raw_params["batch_size"], min_value=1),
        learning_rate=_as_float(
            "learning_rate", raw_params["learning_rate"], min_value=0.0, exclusive_min=True
        ),
        alpha_max=_as_float("alpha_max", raw_params["alpha_max"], min_value=0.0, max_value=1.0),
        alpha_scale=_as_float("alpha_scale", raw_params["alpha_scale"], min_value=0.0),
        beta_max=_as_float("beta_max", raw_params["beta_max"], min_value=0.0),
        gamma_max=_as_float("gamma_max", raw_params["gamma_max"], min_value=0.0),
        early_stopping_patience=_as_int(
            "early_stopping_patience", raw_params["early_stopping_patience"], min_value=1
        ),
        min_targets=_as_int("min_targets", raw_params["min_targets"], min_value=1),
        min_TFs=_as_int("min_TFs", raw_params["min_TFs"], min_value=1),
        fine_tune_epochs=_as_int(
            "fine_tune_epochs", raw_params["fine_tune_epochs"], min_value=1
        ),
        fine_tune_batch_size=_as_int(
            "fine_tune_batch_size", raw_params["fine_tune_batch_size"], min_value=1
        ),
        fine_tune_min_epochs=_as_int(
            "fine_tune_min_epochs", raw_params["fine_tune_min_epochs"], min_value=0
        ),
        fine_tune_beta_max=_as_float(
            "fine_tune_beta_max", raw_params["fine_tune_beta_max"], min_value=0.0
        ),
        fine_tune_max_weight_norm=_as_float(
            "fine_tune_max_weight_norm",
            raw_params["fine_tune_max_weight_norm"],
            min_value=0.0,
            exclusive_min=True,
        ),
        fine_tune_early_stopping_patience=_as_int(
            "fine_tune_early_stopping_patience",
            raw_params["fine_tune_early_stopping_patience"],
            min_value=1,
        ),
        random_state=_as_int("random_state", raw_params["random_state"], min_value=0),
    )


def _load_execution_mode(params_path: Path) -> str:
    execution_path = params_path.parent / "execution.json"
    if not execution_path.exists():
        return "global"
    with execution_path.open("r", encoding="utf-8") as fh:
        execution = json.load(fh)
    if not isinstance(execution, dict):
        raise ValueError("execution.json must be a JSON object.")
    mode = execution.get("mode", "global")
    if not isinstance(mode, str):
        raise ValueError("execution.mode must be a string.")
    if mode not in SUPPORTED_MODES:
        raise ValueError(
            "scRegulate supports only execution.mode=global, group_native or group_emulated."
        )
    return mode


def _find_duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicated: set[str] = set()
    for value in values:
        if value in seen:
            duplicated.add(value)
        seen.add(value)
    return sorted(duplicated)


def _read_expression_tsv(path: Path) -> ExpressionInput:
    raw = pd.read_csv(path, sep="\t", header=0, dtype=str, keep_default_na=False)
    if raw.shape[1] < 3:
        raise ValueError(
            "expression.tsv must contain a gene column and at least two expression columns."
        )

    gene_ids = raw.iloc[:, 0].astype(str).tolist()
    column_ids = [str(column) for column in raw.columns[1:]]
    if any(not value for value in gene_ids):
        raise ValueError("expression.tsv contains an empty gene identifier.")
    if any(not value for value in column_ids):
        raise ValueError("expression.tsv contains an empty expression-column identifier.")

    duplicated_genes = _find_duplicates(gene_ids)
    if duplicated_genes:
        raise ValueError(
            "expression.tsv contains duplicated gene identifiers: "
            + ", ".join(duplicated_genes[:8])
        )
    duplicated_columns = _find_duplicates(column_ids)
    if duplicated_columns:
        raise ValueError(
            "expression.tsv contains duplicated expression-column identifiers: "
            + ", ".join(duplicated_columns[:8])
        )

    numeric = raw.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError("expression.tsv contains non-numeric expression values.")
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("expression.tsv contains non-finite expression values.")
    if (values < 0).any():
        raise ValueError("scRegulate requires non-negative expression values.")

    expression = pd.DataFrame(values, index=gene_ids, columns=column_ids)
    return ExpressionInput(values=expression, gene_ids=gene_ids, column_ids=column_ids)


def _load_groups(extra_dir: Path, expression: ExpressionInput) -> GroupInfo:
    path = require_extra_file(extra_dir, "groups.tsv", "groups")
    raw = pd.read_csv(path, sep="\t", header=0, dtype=str, keep_default_na=False)
    if raw.shape[1] < 2:
        raise ValueError("groups.tsv must contain an expression-column id column and a cluster column.")
    if "cluster" not in raw.columns:
        raise ValueError("groups.tsv is missing required column: cluster.")

    column_col = raw.columns[0]
    column_ids = raw[column_col].astype(str).tolist()
    clusters = raw["cluster"].astype(str).tolist()
    if any(not value for value in column_ids):
        raise ValueError("groups.tsv contains an empty expression-column identifier.")
    if any(not value for value in clusters):
        raise ValueError("groups.tsv contains an empty cluster value.")
    duplicated = _find_duplicates(column_ids)
    if duplicated:
        raise ValueError(
            "groups.tsv contains duplicated expression-column identifiers: "
            + ", ".join(duplicated[:8])
        )

    groups_by_column = dict(zip(column_ids, clusters, strict=True))
    missing = sorted(set(expression.column_ids).difference(groups_by_column))
    if missing:
        raise ValueError("groups.tsv is missing expression columns: " + ", ".join(missing[:8]))

    return GroupInfo(
        groups={column_id: groups_by_column[column_id] for column_id in expression.column_ids}
    )


def _to_anndata(expression: ExpressionInput, group_info: GroupInfo | None) -> ad.AnnData:
    obs = pd.DataFrame(index=pd.Index(expression.column_ids, name="cell_id"))
    if group_info is not None:
        obs["andrea_group"] = [group_info.groups[column_id] for column_id in expression.column_ids]
    var = pd.DataFrame(index=pd.Index(expression.gene_ids, name="gene_id"))
    adata = ad.AnnData(
        X=expression.values.transpose().to_numpy(dtype=np.float32, copy=True),
        obs=obs,
        var=var,
    )
    library_sizes = np.asarray(adata.X.sum(axis=1)).reshape(-1)
    if (library_sizes <= 0).any():
        raise ValueError("scRegulate requires every expression column to have positive total counts.")
    sc.pp.normalize_total(adata, target_sum=1e6)
    sc.pp.log1p(adata)
    return adata


def _load_provided_prior(extra_dir: Path, expression: ExpressionInput) -> pd.DataFrame:
    path = require_extra_file(extra_dir, "prior_grn.tsv", "prior_grn")
    raw = pd.read_csv(path, sep="\t", header=0, dtype=str, keep_default_na=False)
    required = {"source", "target", "score"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"prior_grn.tsv is missing required columns: {missing}")

    prior = raw.loc[:, ["source", "target", "score"]].copy()
    prior["source"] = prior["source"].astype(str)
    prior["target"] = prior["target"].astype(str)
    if (prior["source"] == "").any() or (prior["target"] == "").any():
        raise ValueError("prior_grn.tsv contains empty source or target identifiers.")

    prior["weight"] = pd.to_numeric(prior["score"], errors="coerce")
    keep = prior["weight"].notna() & prior["weight"].map(math.isfinite) & (prior["weight"] != 0)
    prior = prior.loc[keep, ["source", "target", "weight"]].copy()
    if prior.empty:
        raise ValueError("prior_grn.tsv has no finite nonzero prior edges.")

    expression_genes = set(expression.gene_ids)
    invalid_source = sorted(set(prior["source"]).difference(expression_genes))
    invalid_target = sorted(set(prior["target"]).difference(expression_genes))
    if invalid_source or invalid_target:
        raise ValueError(
            "prior_grn.tsv contains genes not present in expression.tsv. "
            f"Invalid sources: {invalid_source[:8]}; invalid targets: {invalid_target[:8]}"
        )

    prior = prior.drop_duplicates(subset=["source", "target"], keep="first")
    return prior.reset_index(drop=True)


def _load_collectri_prior(species: str) -> pd.DataFrame:
    filename = f"collectri_{species}_net.csv"
    path = SCREGULATE_PRIORS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Bundled scRegulate CollecTRI prior not found: {path}. "
            "The Docker image should cache builtin priors during build."
        )
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {"source", "target", "weight"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"{filename} is missing required columns: {missing}")
    prior = raw.loc[:, ["source", "target", "weight"]].copy()
    prior["source"] = prior["source"].astype(str)
    prior["target"] = prior["target"].astype(str)
    prior["weight"] = pd.to_numeric(prior["weight"], errors="coerce")
    keep = prior["weight"].notna() & prior["weight"].map(math.isfinite) & (prior["weight"] != 0)
    prior = prior.loc[keep].drop_duplicates(subset=["source", "target"], keep="first")
    if prior.empty:
        raise ValueError(f"{filename} has no finite nonzero prior edges.")
    return prior.reset_index(drop=True)


def _load_prior(params: ResolvedParams, extra_dir: Path, expression: ExpressionInput) -> pd.DataFrame:
    if params.prior_source == "provided_prior":
        return _load_provided_prior(extra_dir, expression)
    if params.prior_source == "collectri_human":
        return _load_collectri_prior("human")
    if params.prior_source == "collectri_mouse":
        return _load_collectri_prior("mouse")
    raise ValueError(f"Unsupported prior_source: {params.prior_source!r}")


def _filter_prior_for_upstream(
    prior: pd.DataFrame,
    *,
    min_targets: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    target_counts = prior.groupby("source", sort=False)["target"].nunique()
    keep_sources = set(target_counts[target_counts >= min_targets].index.astype(str))
    dropped_sources = sorted(set(target_counts.index.astype(str)).difference(keep_sources))

    stats = {
        "original_edges": int(prior.shape[0]),
        "original_sources": int(target_counts.shape[0]),
        "min_targets": int(min_targets),
        "dropped_sources_below_min_targets": dropped_sources,
    }
    if not dropped_sources:
        stats["filtered_edges"] = int(prior.shape[0])
        stats["filtered_sources"] = int(target_counts.shape[0])
        return prior.reset_index(drop=True), stats

    filtered = prior.loc[prior["source"].isin(keep_sources)].copy()
    if filtered.empty:
        raise ValueError(
            "prior_grn contains no transcription factors with at least "
            f"{min_targets} target genes after expression-gene filtering. "
            "Lower min_targets or provide a denser prior_grn.tsv."
        )

    filtered_counts = filtered.groupby("source", sort=False)["target"].nunique()
    stats["filtered_edges"] = int(filtered.shape[0])
    stats["filtered_sources"] = int(filtered_counts.shape[0])
    return filtered.reset_index(drop=True), stats


def _configure_threads(threads: int) -> None:
    if threads <= 0:
        raise ValueError("--threads must be a positive integer.")
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ[key] = str(threads)
    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


def _set_random_seeds(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)


def _version_or_unknown(package_name: str) -> str:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return "unknown"


def _attach_upstream_file_logging(log_path: Path) -> None:
    formatter = logging.Formatter(
        "[%(levelname)s - %(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    for logger_name in ("train_model", "finetune"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def _write_config(
    path: Path,
    *,
    params: ResolvedParams,
    expression: ExpressionInput,
    execution_mode: str,
    group_info: GroupInfo | None,
    prior_edge_count: int,
    prior_filter: dict[str, Any],
    threads: int,
) -> None:
    payload = {
        "tool": "scregulate",
        "upstream_package": "scRegulate",
        "upstream_version": _version_or_unknown("scRegulate"),
        "entrypoint": "scregulate.train_model + scregulate.fine_tuning.fine_tune_clusters",
        "execution_mode": execution_mode,
        "gene_count": len(expression.gene_ids),
        "expression_column_count": len(expression.column_ids),
        "prior_edge_count": prior_edge_count,
        "prior_filter": prior_filter,
        "requested_threads": threads,
        "thread_mapping": {
            "torch_num_threads": threads,
            "torch_num_interop_threads": 1,
            "blas_openmp_env_threads": threads,
        },
        "params": asdict(params),
        "expression_preprocessing": {
            "non_negative_required": True,
            "positive_column_sums_required": True,
            "scanpy_normalize_total_target_sum": 1e6,
            "scanpy_log1p": True,
        },
        "batch_size_rule": (
            "omitted; scRegulate computes int(train_val_split_ratio * n_cells)"
            if params.batch_size is None
            else "explicit batch_size passed to train_model"
        ),
        "group_count": (
            len(set(group_info.groups.values())) if group_info is not None else None
        ),
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def _write_prior(path: Path, prior: pd.DataFrame) -> None:
    prior.loc[:, ["source", "target", "weight"]].to_csv(path, sep="\t", index=False)


def _run_scregulate(
    *,
    expression: ExpressionInput,
    prior: pd.DataFrame,
    params: ResolvedParams,
    execution_mode: str,
    group_info: GroupInfo | None,
) -> tuple[dict[str, np.ndarray], list[str], list[str], dict[str, Any]]:
    _set_random_seeds(params.random_state)
    adata = _to_anndata(
        expression,
        group_info if execution_mode == "group_native" else None,
    )

    model, processed_adata, _scaled_posterior = train_model(
        rna_data=adata,
        net=prior,
        train_val_split_ratio=params.train_val_split_ratio,
        random_state=params.random_state,
        batch_size=params.batch_size,
        epochs=params.epochs,
        freeze_epochs=params.freeze_epochs,
        learning_rate=params.learning_rate,
        alpha_max=params.alpha_max,
        alpha_scale=params.alpha_scale,
        beta_max=params.beta_max,
        gamma_max=params.gamma_max,
        log_interval=max(1, params.epochs),
        early_stopping_patience=params.early_stopping_patience,
        min_targets=params.min_targets,
        min_TFs=params.min_TFs,
        device=None,
        return_outputs=True,
        verbose=True,
    )

    cluster_key = "andrea_group" if execution_mode == "group_native" else None
    processed_adata, _tf_activities, final_model, _scaled_grns = fine_tune_clusters(
        processed_adata=processed_adata,
        model=model,
        cluster_key=cluster_key,
        epochs=params.fine_tune_epochs,
        batch_size=params.fine_tune_batch_size,
        device=None,
        max_weight_norm=params.fine_tune_max_weight_norm,
        log_interval=max(1, params.fine_tune_epochs),
        early_stopping_patience=params.fine_tune_early_stopping_patience,
        min_epochs=params.fine_tune_min_epochs,
        beta_max=params.fine_tune_beta_max,
        verbose=True,
    )

    raw_matrices = processed_adata.uns.get("W_posteriors_per_cluster")
    if not isinstance(raw_matrices, dict) or not raw_matrices:
        raise RuntimeError("scRegulate did not expose raw W_posteriors_per_cluster.")

    gene_names = list(processed_adata.uns["GRN_posterior"]["gene_names"])
    tf_names = list(processed_adata.uns["GRN_posterior"]["TF_names"])

    if execution_mode == "global":
        context_by_key = {next(iter(raw_matrices.keys())): "global"}
    elif execution_mode == "group_native":
        context_by_key = {cluster: f"group:{cluster}" for cluster in raw_matrices}
    else:
        context = "global"
        if group_info is not None and len(group_info.unique_groups) == 1:
            context = f"group:{group_info.unique_groups[0]}"
        context_by_key = {next(iter(raw_matrices.keys())): context}

    by_context: dict[str, np.ndarray] = {}
    for cluster, matrix in raw_matrices.items():
        context = context_by_key.get(cluster)
        if context is None:
            context = f"group:{cluster}"
        by_context[str(context)] = np.asarray(matrix, dtype=float)

    model_state = {
        "base_model_state_dict": model.state_dict(),
        "final_model_state_dict": final_model.state_dict(),
        "execution_mode": execution_mode,
        "contexts": sorted(by_context),
    }
    return by_context, gene_names, tf_names, model_state


def _matrix_to_frame(
    *,
    by_context: dict[str, np.ndarray],
    gene_names: list[str],
    tf_names: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    gene_count = len(gene_names)
    tf_count = len(tf_names)
    for context, matrix in by_context.items():
        if matrix.shape == (tf_count, gene_count):
            matrix = matrix.T
        if matrix.shape != (gene_count, tf_count):
            raise ValueError(
                "Raw scRegulate weight matrix has unexpected shape "
                f"{matrix.shape}; expected {(gene_count, tf_count)}."
            )
        for gene_idx, target in enumerate(gene_names):
            for tf_idx, source in enumerate(tf_names):
                weight = float(matrix[gene_idx, tf_idx])
                if not math.isfinite(weight) or weight == 0.0:
                    continue
                if str(source) == str(target):
                    continue
                rows.append(
                    {
                        "context": context,
                        "source": str(source),
                        "target": str(target),
                        "weight": weight,
                        "score": abs(weight),
                        "sign": "+" if weight > 0 else "-",
                    }
                )

    if not rows:
        raise RuntimeError("scRegulate produced no nonzero non-self raw GRN weights.")
    out = pd.DataFrame(rows)
    out = out.sort_values(
        ["context", "score", "source", "target"],
        ascending=[True, False, True, True],
    ).reset_index(drop=True)
    return out


def _write_network(raw_weights: pd.DataFrame, path: Path) -> int:
    out = pd.DataFrame(
        {
            "source": raw_weights["source"].astype(str),
            "target": raw_weights["target"].astype(str),
            "score": raw_weights["score"].astype(float),
            "sign": raw_weights["sign"].astype(str),
            "evidence": "association",
            "context": raw_weights["context"].astype(str),
        }
    )
    out.to_csv(path, index=False, columns=NETWORK_COLUMNS)
    return int(out.shape[0])


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
    log_path = args.output_dir / "scregulate.log"
    network_path = args.output_dir / "network.csv"
    config_path = raw_dir / "scregulate_config.json"
    prior_path = raw_dir / "prior_network.tsv"
    raw_weights_path = raw_dir / "grn_raw_weights.tsv"
    model_state_path = raw_dir / "model_state.pt"

    write_progress(
        progress_path,
        status="running",
        percent=0,
        phase="init",
        message="Initializing scRegulate wrapper",
    )

    try:
        _configure_threads(args.threads)
        raw_params = load_params(args.params)
        params = _resolve_params(raw_params)
        execution_mode = _load_execution_mode(args.params)

        write_progress(
            progress_path,
            status="running",
            percent=10,
            phase="load_input",
            message="Loading expression matrix and prior",
        )
        expression = _read_expression_tsv(args.input)
        group_info = (
            _load_groups(args.extra, expression)
            if execution_mode in {"group_native", "group_emulated"}
            else None
        )
        raw_prior = _load_prior(params, args.extra, expression)
        prior, prior_filter = _filter_prior_for_upstream(
            raw_prior,
            min_targets=params.min_targets,
        )
        _write_prior(prior_path, prior)
        _write_config(
            config_path,
            params=params,
            expression=expression,
            execution_mode=execution_mode,
            group_info=group_info,
            prior_edge_count=int(prior.shape[0]),
            prior_filter=prior_filter,
            threads=args.threads,
        )

        with log_path.open("w", encoding="utf-8") as log_fh:
            log_fh.write("scRegulate wrapper starting\n")
            log_fh.write(f"scRegulate_version={_version_or_unknown('scRegulate')}\n")
            log_fh.write(f"execution_mode={execution_mode}\n")
            log_fh.write(f"genes={len(expression.gene_ids)} columns={len(expression.column_ids)}\n")
            log_fh.write(
                f"prior_source={params.prior_source} "
                f"prior_edges={prior_filter['original_edges']} "
                f"filtered_prior_edges={prior.shape[0]}\n"
            )
            dropped_sources = prior_filter["dropped_sources_below_min_targets"]
            if dropped_sources:
                log_fh.write(
                    "prior_sources_dropped_below_min_targets="
                    + ",".join(dropped_sources[:50])
                    + ("\n" if len(dropped_sources) <= 50 else ",...\n")
                )
            log_fh.write(f"threads={args.threads}\n")
            if group_info is not None:
                log_fh.write(f"group_count={len(group_info.unique_groups)}\n")
            log_fh.write("\n")
            log_fh.flush()

        _attach_upstream_file_logging(log_path)
        write_progress(
            progress_path,
            status="running",
            percent=25,
            phase="training",
            message="Running scRegulate train_model",
            completed=0,
            total=params.epochs + params.fine_tune_epochs,
        )
        with log_path.open("a", encoding="utf-8") as log_fh:
            with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
                by_context, gene_names, tf_names, model_state = _run_scregulate(
                    expression=expression,
                    prior=prior,
                    params=params,
                    execution_mode=execution_mode,
                    group_info=group_info,
                )

        torch.save(model_state, model_state_path)
        write_progress(
            progress_path,
            status="running",
            percent=90,
            phase="write_output",
            message="Writing raw weights and network.csv",
        )
        raw_weights = _matrix_to_frame(
            by_context=by_context,
            gene_names=gene_names,
            tf_names=tf_names,
        )
        raw_weights.to_csv(raw_weights_path, sep="\t", index=False)
        edge_count = _write_network(raw_weights, network_path)

        write_progress(
            progress_path,
            status="completed",
            percent=100,
            phase="done",
            message="scRegulate inference finished",
            completed=edge_count,
            total=edge_count,
        )
    except Exception as exc:
        with log_path.open("a", encoding="utf-8") as log_fh:
            log_fh.write("\nWrapper failure:\n")
            log_fh.write(traceback.format_exc())
        write_progress(
            progress_path,
            status="failed",
            percent=100,
            phase="failed",
            message="scRegulate inference failed",
            error=str(exc),
        )
        raise


if __name__ == "__main__":
    main()
