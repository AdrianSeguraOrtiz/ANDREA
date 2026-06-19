"""MetaSEM wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import os
import random
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
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


METASEM_HOME = Path(os.environ.get("METASEM_HOME", "/opt/MetaSEM"))
METASEM_REF = os.environ.get(
    "METASEM_REF", "482987b360ca57172f0276fd64e27b2681223b00"
)
NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
SUPPORTED_MODES = {"global", "group_emulated"}


@dataclass(frozen=True)
class ResolvedParams:
    pseudo_grn_mode: str
    epochs: int
    batch_size: int
    alpha: float
    lr: float
    lr_meta: float
    gamma: float
    gamma_meta: float
    random_seed: int


@dataclass(frozen=True)
class ExpressionInput:
    values: pd.DataFrame
    gene_ids: list[str]
    cell_ids: list[str]


def _as_int(name: str, value: Any, *, min_value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value < min_value:
        raise ValueError(f"{name} must be >= {min_value}.")
    return int(value)


def _as_float(name: str, value: Any, *, min_value: float | None = None, max_value: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number.")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite.")
    if min_value is not None and out <= min_value:
        raise ValueError(f"{name} must be > {min_value}.")
    if max_value is not None and out > max_value:
        raise ValueError(f"{name} must be <= {max_value}.")
    return out


def _resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    expected = {
        "pseudo_grn_mode",
        "epochs",
        "batch_size",
        "alpha",
        "lr",
        "lr_meta",
        "gamma",
        "gamma_meta",
        "random_seed",
    }
    require_param_keys(raw_params, expected)
    warn_unknown_params(raw_params, expected)

    pseudo_grn_mode = raw_params["pseudo_grn_mode"]
    if pseudo_grn_mode not in {"unlabeled_all_genes", "provided_prior"}:
        raise ValueError(
            "pseudo_grn_mode must be one of: unlabeled_all_genes, provided_prior."
        )

    return ResolvedParams(
        pseudo_grn_mode=str(pseudo_grn_mode),
        epochs=_as_int("epochs", raw_params["epochs"], min_value=1),
        batch_size=_as_int("batch_size", raw_params["batch_size"], min_value=1),
        alpha=_as_float("alpha", raw_params["alpha"], min_value=0.0),
        lr=_as_float("lr", raw_params["lr"], min_value=0.0),
        lr_meta=_as_float("lr_meta", raw_params["lr_meta"], min_value=0.0),
        gamma=_as_float("gamma", raw_params["gamma"], min_value=0.0, max_value=1.0),
        gamma_meta=_as_float(
            "gamma_meta", raw_params["gamma_meta"], min_value=0.0, max_value=1.0
        ),
        random_seed=_as_int("random_seed", raw_params["random_seed"], min_value=0),
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
        raise ValueError("MetaSEM supports only execution.mode=global or group_emulated.")
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
    if raw.shape[1] < 2:
        raise ValueError("expression.tsv must contain a gene column and at least one cell.")

    gene_ids = raw.iloc[:, 0].astype(str).tolist()
    cell_ids = [str(col) for col in raw.columns[1:]]
    if any(not value for value in gene_ids):
        raise ValueError("expression.tsv contains an empty gene identifier.")
    if any(not value for value in cell_ids):
        raise ValueError("expression.tsv contains an empty cell identifier.")

    duplicated_genes = _find_duplicates(gene_ids)
    if duplicated_genes:
        raise ValueError(
            "expression.tsv contains duplicated gene identifiers: "
            + ", ".join(duplicated_genes)
        )
    duplicated_cells = _find_duplicates(cell_ids)
    if duplicated_cells:
        raise ValueError(
            "expression.tsv contains duplicated cell identifiers: "
            + ", ".join(duplicated_cells)
        )

    numeric = raw.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError("expression.tsv contains non-numeric expression values.")
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("expression.tsv contains non-finite expression values.")
    expression = pd.DataFrame(values, index=gene_ids, columns=cell_ids)
    return ExpressionInput(values=expression, gene_ids=gene_ids, cell_ids=cell_ids)


def _write_upstream_expression(path: Path, expression: ExpressionInput) -> None:
    sample_by_gene = expression.values.transpose()
    sample_by_gene.index.name = "sample"
    sample_by_gene.to_csv(path)


def _write_provided_prior(path: Path, prior_path: Path, expression: ExpressionInput) -> int:
    raw = pd.read_csv(prior_path, sep="\t", header=0, dtype=str, keep_default_na=False)
    required = {"source", "target", "score"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"prior_grn.tsv is missing required columns: {missing}")
    raw["score"] = pd.to_numeric(raw["score"], errors="coerce")
    raw = raw[np.isfinite(raw["score"].to_numpy(dtype=float)) & (raw["score"] != 0)]
    gene_set = set(expression.gene_ids)
    invalid_source = sorted(set(raw["source"].astype(str)).difference(gene_set))
    invalid_target = sorted(set(raw["target"].astype(str)).difference(gene_set))
    if invalid_source or invalid_target:
        raise ValueError(
            "prior_grn.tsv contains genes not present in expression.tsv. "
            f"Invalid sources: {invalid_source[:5]}; invalid targets: {invalid_target[:5]}"
        )
    rows = raw[["source", "target"]].drop_duplicates()
    rows = rows[rows["source"].astype(str) != rows["target"].astype(str)]
    if rows.empty:
        raise ValueError("prior_grn.tsv has no nonzero non-self prior edges after filtering.")
    upstream = pd.DataFrame(
        {
            "Gene1": rows["source"].astype(str),
            "Gene2": rows["target"].astype(str),
        }
    )
    upstream.to_csv(path, index=False)
    return int(upstream.shape[0])


def _write_unlabeled_pseudo_grn(path: Path, expression: ExpressionInput) -> int:
    genes = expression.gene_ids
    if len(genes) < 2:
        raise ValueError("MetaSEM requires at least two genes.")
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["Gene1", "Gene2"])
        for idx, source in enumerate(genes):
            writer.writerow([source, genes[(idx + 1) % len(genes)]])
    return len(genes)


def _prepare_net_path(
    *,
    params: ResolvedParams,
    extra_dir: Path,
    expression: ExpressionInput,
    work_dir: Path,
) -> tuple[Path, int, bool]:
    net_path = work_dir / "metasem_network_input.csv"
    if params.pseudo_grn_mode == "provided_prior":
        prior_path = require_extra_file(extra_dir, "prior_grn.tsv", "prior_grn")
        edge_count = _write_provided_prior(net_path, prior_path, expression)
        return net_path, edge_count, True
    edge_count = _write_unlabeled_pseudo_grn(net_path, expression)
    return net_path, edge_count, False


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

    import torch

    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed % (2**32))
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)


def _write_config(
    path: Path,
    *,
    params: ResolvedParams,
    execution_mode: str,
    expression: ExpressionInput,
    upstream_edge_count: int,
    is_label: bool,
    threads: int,
) -> None:
    payload = {
        "tool": "metasem",
        "upstream_ref": METASEM_REF,
        "upstream_home": str(METASEM_HOME),
        "execution_mode": execution_mode,
        "pseudo_grn_mode": params.pseudo_grn_mode,
        "is_label": is_label,
        "gene_count": len(expression.gene_ids),
        "cell_count": len(expression.cell_ids),
        "upstream_net_edges": upstream_edge_count,
        "threads": threads,
        "params": {
            "epochs": params.epochs,
            "batch_size": params.batch_size,
            "alpha": params.alpha,
            "lr": params.lr,
            "lr_meta": params.lr_meta,
            "gamma": params.gamma,
            "gamma_meta": params.gamma_meta,
            "random_seed": params.random_seed,
            "lr_step_size": 1,
            "lr_step_size_meta": 1,
            "hidden_size": 64,
        },
        "source_patch": [
            "patched_src/MetaSEM_Model.py",
            "patched_src/MetaSEM_tool.py",
            "patched_src/MetaSEM_Train_GRN_inference.py",
        ],
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def _run_metasem(
    *,
    params: ResolvedParams,
    input_csv: Path,
    net_csv: Path,
    raw_edges_path: Path,
    work_dir: Path,
    progress_path: Path,
) -> None:
    if str(METASEM_HOME) not in sys.path:
        sys.path.insert(0, str(METASEM_HOME))

    from SRC.MetaSEM_Train_GRN_inference import Train_inference

    def _on_epoch(completed: int, total: int) -> None:
        percent = 10 + int((completed / max(1, total)) * 80)
        write_progress(
            progress_path,
            status="running",
            percent=percent,
            phase="inference",
            message="Training MetaSEM",
            completed=completed,
            total=total,
        )

    opt = SimpleNamespace(
        alpha=params.alpha,
        gamma=params.gamma,
        gamma_meta=params.gamma_meta,
        lr=params.lr,
        lr_meta=params.lr_meta,
        lr_step_size=1,
        lr_step_size_meta=1,
        batch_size=params.batch_size,
        epochs=params.epochs,
        epoch=params.epochs,
        net_size=0,
        n_hidden=64,
        hidden_size=64,
        save_name=str(work_dir / "save"),
        tsv_path=str(raw_edges_path),
        net_path=str(net_csv),
        is_label=params.pseudo_grn_mode == "provided_prior",
        progress_callback=_on_epoch,
    )
    trainer = Train_inference(opt)
    trainer.train_model(str(input_csv), str(net_csv))


def _convert_network(raw_edges_path: Path, network_path: Path) -> int:
    raw = pd.read_csv(raw_edges_path, sep="\t", header=0, dtype={"TF": str, "Target": str})
    required = {"TF", "Target", "EdgeWeight"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"MetaSEM raw edge output is missing columns: {missing}")
    raw["EdgeWeight"] = pd.to_numeric(raw["EdgeWeight"], errors="coerce")
    raw = raw[np.isfinite(raw["EdgeWeight"].to_numpy(dtype=float))]
    raw = raw[raw["TF"].astype(str) != raw["Target"].astype(str)]
    raw["score"] = raw["EdgeWeight"].abs()
    raw = raw[raw["score"] > 0]
    if raw.empty:
        raise RuntimeError("MetaSEM produced no positive-magnitude edges.")
    out = pd.DataFrame(
        {
            "source": raw["TF"].astype(str),
            "target": raw["Target"].astype(str),
            "score": raw["score"].astype(float),
            "sign": np.where(raw["EdgeWeight"] > 0, "+", "-"),
            "evidence": "association",
            "context": "global",
        }
    )
    out = out.sort_values(["score", "source", "target"], ascending=[False, True, True])
    out.to_csv(network_path, index=False, columns=NETWORK_COLUMNS)
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
    work_dir = args.output_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    progress_path = args.output_dir / "progress.json"
    log_path = args.output_dir / "metasem.log"

    write_progress(
        progress_path,
        status="running",
        percent=0,
        phase="init",
        message="Initializing MetaSEM",
    )

    try:
        _configure_threads(args.threads)
        raw_params = load_params(args.params)
        params = _resolve_params(raw_params)
        _seed_everything(params.random_seed)
        execution_mode = _load_execution_mode(args.params)

        write_progress(
            progress_path,
            status="running",
            percent=5,
            phase="load_input",
            message="Loading expression and MetaSEM inputs",
        )
        expression = _read_expression_tsv(args.input)
        input_csv = work_dir / "metasem_expression.csv"
        _write_upstream_expression(input_csv, expression)
        net_csv, upstream_edge_count, is_label = _prepare_net_path(
            params=params,
            extra_dir=args.extra,
            expression=expression,
            work_dir=work_dir,
        )
        raw_edges_path = raw_dir / "metasem_edges.tsv"
        config_path = raw_dir / "metasem_config.json"
        _write_config(
            config_path,
            params=params,
            execution_mode=execution_mode,
            expression=expression,
            upstream_edge_count=upstream_edge_count,
            is_label=is_label,
            threads=args.threads,
        )

        write_progress(
            progress_path,
            status="running",
            percent=10,
            phase="inference",
            message="Starting MetaSEM training",
            completed=0,
            total=params.epochs,
        )
        with log_path.open("w", encoding="utf-8") as log_fh:
            with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
                print("MetaSEM wrapper starting")
                print(f"upstream_ref={METASEM_REF}")
                print(f"pseudo_grn_mode={params.pseudo_grn_mode} is_label={is_label}")
                _run_metasem(
                    params=params,
                    input_csv=input_csv,
                    net_csv=net_csv,
                    raw_edges_path=raw_edges_path,
                    work_dir=work_dir,
                    progress_path=progress_path,
                )

        write_progress(
            progress_path,
            status="running",
            percent=95,
            phase="write_output",
            message="Writing network.csv",
        )
        edge_count = _convert_network(raw_edges_path, args.output_dir / "network.csv")

        write_progress(
            progress_path,
            status="completed",
            percent=100,
            phase="done",
            message="Inference finished",
            completed=edge_count,
            total=edge_count,
        )
    except Exception as exc:
        if not log_path.exists() or log_path.stat().st_size == 0:
            with log_path.open("w", encoding="utf-8") as log_fh:
                log_fh.write("MetaSEM wrapper failed before upstream logging started.\n")
                log_fh.write(traceback.format_exc())
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
