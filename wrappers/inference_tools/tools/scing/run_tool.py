"""SCING wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import traceback
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Iterator

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

from _run_tool_common import (
    load_params,
    require_extra_file,
    require_param_keys,
    validate_runtime_inputs,
    warn_unknown_params,
    write_progress,
)


SCING_REF = os.environ.get("SCING_REF", "fcea8c5c9a806ee3dbc8123c2d13d1d357137f1d")
NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
SUPPORTED_MODES = {"global", "group_emulated"}
MEM_PER_CORE_BYTES = int(os.environ.get("SCING_MEM_PER_CORE", "2000000000"))


@dataclass(frozen=True)
class ResolvedParams:
    n_supercells: int
    supercell_hvgs: int
    supercell_pcs: int
    n_subsample_networks: int
    network_hvgs: int
    gene_neighbors: int
    gene_pcs: int
    subsample_fraction: float
    edge_consensus_threshold: float
    remove_cycles: bool
    random_seed: int


@dataclass(frozen=True)
class ExpressionInput:
    values: pd.DataFrame
    gene_ids: list[str]
    column_ids: list[str]


@dataclass(frozen=True)
class GroupInfo:
    groups: dict[str, str]


def _as_int(name: str, value: Any, *, min_value: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}.")
    return int(value)


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


def _as_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return bool(value)


def _resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    expected = {
        "n_supercells",
        "supercell_hvgs",
        "supercell_pcs",
        "n_subsample_networks",
        "network_hvgs",
        "gene_neighbors",
        "gene_pcs",
        "subsample_fraction",
        "edge_consensus_threshold",
        "remove_cycles",
        "random_seed",
    }
    require_param_keys(raw_params, expected)
    warn_unknown_params(raw_params, expected)

    network_hvgs = _as_int("network_hvgs", raw_params["network_hvgs"], min_value=-1)
    if network_hvgs in {0, 1}:
        raise ValueError("network_hvgs must be -1 or an integer >= 2.")

    return ResolvedParams(
        n_supercells=_as_int("n_supercells", raw_params["n_supercells"], min_value=2),
        supercell_hvgs=_as_int("supercell_hvgs", raw_params["supercell_hvgs"], min_value=1),
        supercell_pcs=_as_int("supercell_pcs", raw_params["supercell_pcs"], min_value=1),
        n_subsample_networks=_as_int(
            "n_subsample_networks", raw_params["n_subsample_networks"], min_value=1
        ),
        network_hvgs=network_hvgs,
        gene_neighbors=_as_int("gene_neighbors", raw_params["gene_neighbors"], min_value=1),
        gene_pcs=_as_int("gene_pcs", raw_params["gene_pcs"], min_value=1),
        subsample_fraction=_as_float(
            "subsample_fraction",
            raw_params["subsample_fraction"],
            min_value=0.0,
            exclusive_min=True,
            max_value=1.0,
        ),
        edge_consensus_threshold=_as_float(
            "edge_consensus_threshold",
            raw_params["edge_consensus_threshold"],
            min_value=0.0,
            max_value=1.0,
            exclusive_max=True,
        ),
        remove_cycles=_as_bool("remove_cycles", raw_params["remove_cycles"]),
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
        raise ValueError("SCING supports only execution.mode=global or group_emulated.")
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
        raise ValueError("expression.tsv must contain a gene column and at least two expression columns.")

    gene_ids = raw.iloc[:, 0].astype(str).tolist()
    column_ids = [str(column) for column in raw.columns[1:]]
    if any(not value for value in gene_ids):
        raise ValueError("expression.tsv contains an empty gene identifier.")
    if any(not value for value in column_ids):
        raise ValueError("expression.tsv contains an empty expression column identifier.")

    duplicated_genes = _find_duplicates(gene_ids)
    if duplicated_genes:
        raise ValueError(
            "expression.tsv contains duplicated gene identifiers: "
            + ", ".join(duplicated_genes)
        )
    duplicated_columns = _find_duplicates(column_ids)
    if duplicated_columns:
        raise ValueError(
            "expression.tsv contains duplicated expression column identifiers: "
            + ", ".join(duplicated_columns)
        )

    numeric = raw.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError("expression.tsv contains non-numeric expression values.")
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("expression.tsv contains non-finite expression values.")
    if np.any(values < 0):
        raise ValueError("SCING's selected preprocessing path requires non-negative expression values.")

    expression = pd.DataFrame(values, index=gene_ids, columns=column_ids)
    if expression.shape[0] < 2:
        raise ValueError("SCING requires at least two expression genes.")
    if expression.shape[1] < 2:
        raise ValueError("SCING requires at least two expression columns.")
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
            + ", ".join(duplicated)
        )

    groups_by_column = dict(zip(column_ids, clusters, strict=True))
    missing = sorted(set(expression.column_ids).difference(groups_by_column))
    if missing:
        raise ValueError(
            "groups.tsv is missing expression columns: " + ", ".join(missing[:8])
        )

    return GroupInfo(
        groups={column_id: groups_by_column[column_id] for column_id in expression.column_ids}
    )


def _validate_static_dimensions(params: ResolvedParams, expression: ExpressionInput) -> None:
    gene_count = len(expression.gene_ids)
    column_count = len(expression.column_ids)
    if params.gene_neighbors >= gene_count:
        raise ValueError("gene_neighbors must be smaller than the number of expression genes.")
    if params.gene_pcs >= gene_count:
        raise ValueError("gene_pcs must be smaller than the number of expression genes.")
    if params.gene_pcs >= column_count:
        raise ValueError("gene_pcs must be smaller than the number of expression columns.")
    if params.network_hvgs != -1 and params.gene_neighbors >= params.network_hvgs:
        raise ValueError("gene_neighbors must be smaller than network_hvgs when network_hvgs is positive.")
    if params.network_hvgs != -1 and params.gene_pcs >= params.network_hvgs:
        raise ValueError("gene_pcs must be smaller than network_hvgs when network_hvgs is positive.")


def _to_anndata(expression: ExpressionInput) -> ad.AnnData:
    data = expression.values.transpose().to_numpy(dtype=float, copy=True)
    adata = ad.AnnData(data)
    adata.obs_names = list(expression.column_ids)
    adata.var_names = list(expression.gene_ids)
    return adata


def _version_or_unknown(package_name: str) -> str:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return "unknown"


def _write_config(
    path: Path,
    *,
    params: ResolvedParams,
    expression: ExpressionInput,
    execution_mode: str,
    group_info: GroupInfo | None,
    threads: int,
) -> None:
    payload = {
        "tool": "scing",
        "upstream_repo": "https://github.com/XiaYangLabOrg/SCING.git",
        "upstream_ref": SCING_REF,
        "entrypoint": (
            "supercells.supercell_pipeline + build.grnBuilder + "
            "merge.NetworkMerger"
        ),
        "execution_mode": execution_mode,
        "gene_count": len(expression.gene_ids),
        "expression_column_count": len(expression.column_ids),
        "requested_threads": threads,
        "upstream_ncore": threads,
        "mem_per_core_bytes": MEM_PER_CORE_BYTES,
        "params": asdict(params),
        "group_count": (
            len(set(group_info.groups.values())) if group_info is not None else None
        ),
        "runtime_versions": {
            "scanpy": _version_or_unknown("scanpy"),
            "anndata": _version_or_unknown("anndata"),
            "numpy": _version_or_unknown("numpy"),
            "pandas": _version_or_unknown("pandas"),
            "scikit_learn": _version_or_unknown("scikit-learn"),
            "dask": _version_or_unknown("dask"),
            "distributed": _version_or_unknown("distributed"),
            "pyitlib": _version_or_unknown("pyitlib"),
        },
        "random_seed_rule": "Each grnBuilder run receives random_seed + network_index.",
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


@contextlib.contextmanager
def _pushd(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _build_intermediate_networks(
    *,
    adata_merged: ad.AnnData,
    params: ResolvedParams,
    intermediate_dir: Path,
    threads: int,
    progress_path: Path,
) -> list[pd.DataFrame]:
    from scing import build

    networks: list[pd.DataFrame] = []
    for index in range(params.n_subsample_networks):
        percent = 35 + int((index / max(1, params.n_subsample_networks)) * 40)
        write_progress(
            progress_path,
            status="running",
            percent=percent,
            phase="build_networks",
            message=f"Building SCING subsampled network {index + 1}/{params.n_subsample_networks}",
            completed=index,
            total=params.n_subsample_networks,
        )
        grn = build.grnBuilder(
            adata=adata_merged,
            ngenes=params.network_hvgs,
            nneighbors=params.gene_neighbors,
            npcs=params.gene_pcs,
            subsample_perc=params.subsample_fraction,
            prefix=f"net.{index:03d}",
            outdir=str(intermediate_dir),
            ncore=threads,
            mem_per_core=MEM_PER_CORE_BYTES,
            verbose=True,
            random_state=params.random_seed + index,
        )
        grn.subsample_cells()
        grn.filter_genes()

        filtered_gene_count = int(grn.adata.n_vars)
        filtered_column_count = int(grn.adata.n_obs)
        if filtered_gene_count < 2:
            raise RuntimeError(
                "SCING retained fewer than two genes after empty-gene/HVG filtering."
            )
        if filtered_column_count < 2:
            raise RuntimeError(
                "SCING retained fewer than two supercells after subsampling."
            )
        if params.gene_neighbors >= filtered_gene_count:
            raise RuntimeError(
                "gene_neighbors is too large after SCING gene filtering: "
                f"{params.gene_neighbors} >= {filtered_gene_count}."
            )
        if params.gene_pcs >= min(filtered_gene_count, filtered_column_count):
            raise RuntimeError(
                "gene_pcs is too large after SCING filtering/subsampling: "
                f"{params.gene_pcs} >= min({filtered_gene_count}, {filtered_column_count})."
            )

        grn.filter_gene_connectivities()
        grn.build_grn()
        if not hasattr(grn, "edges") or grn.edges.empty:
            raise RuntimeError(f"SCING subsampled network {index + 1} produced no edges.")
        grn.save_edges()
        networks.append(grn.edges.copy())
    return networks


def _run_scing(
    *,
    expression: ExpressionInput,
    params: ResolvedParams,
    raw_dir: Path,
    work_dir: Path,
    progress_path: Path,
    threads: int,
) -> Path:
    from scing import merge, supercells

    adata = _to_anndata(expression)
    intermediate_dir = raw_dir / "intermediate_networks"
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    write_progress(
        progress_path,
        status="running",
        percent=20,
        phase="supercells",
        message="Constructing SCING supercells",
    )
    adata_merged = supercells.supercell_pipeline(
        adata,
        ngenes=params.supercell_hvgs,
        npcs=params.supercell_pcs,
        ncell=params.n_supercells,
        verbose=True,
    )
    if int(adata_merged.n_obs) < 2:
        raise RuntimeError("SCING supercell construction produced fewer than two supercells.")

    networks = _build_intermediate_networks(
        adata_merged=adata_merged,
        params=params,
        intermediate_dir=intermediate_dir,
        threads=threads,
        progress_path=progress_path,
    )

    write_progress(
        progress_path,
        status="running",
        percent=80,
        phase="merge",
        message="Merging and pruning SCING subsampled networks",
    )
    merger = merge.NetworkMerger(
        adata=adata_merged,
        networks=networks,
        minimum_edge_appearance_threshold=params.edge_consensus_threshold,
        cycles=params.remove_cycles,
        prefix="final",
        outdir=str(raw_dir),
        ncore=threads,
        mem_per_core=MEM_PER_CORE_BYTES,
        verbose=True,
    )
    with _pushd(work_dir):
        merger.pipeline()

    final_path = raw_dir / "final.network.merged.csv"
    if not final_path.exists() or final_path.stat().st_size <= 0:
        raise RuntimeError("SCING did not produce a non-empty final merged network.")
    return final_path


def _convert_network(raw_path: Path, output_path: Path, expression: ExpressionInput) -> int:
    raw = pd.read_csv(raw_path, dtype={"source": str, "target": str})
    required = {"source", "target", "importance"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"SCING merged network is missing columns: {missing}")

    score = pd.to_numeric(raw["importance"], errors="coerce")
    source = raw["source"].astype(str)
    target = raw["target"].astype(str)
    keep = score.notna() & np.isfinite(score.to_numpy(dtype=float)) & (score > 0)
    keep &= source != target
    genes = set(expression.gene_ids)
    keep &= source.isin(genes) & target.isin(genes)

    filtered = raw.loc[keep].copy()
    if filtered.empty:
        raise RuntimeError("SCING produced no positive non-self edges.")

    filtered["score"] = pd.to_numeric(filtered["importance"], errors="raise").astype(float)
    out = pd.DataFrame(
        {
            "source": filtered["source"].astype(str),
            "target": filtered["target"].astype(str),
            "score": filtered["score"],
            "sign": "?",
            "evidence": "association",
            "context": "global",
        }
    )
    out = out.sort_values(["score", "source", "target"], ascending=[False, True, True])
    out.to_csv(output_path, index=False, columns=NETWORK_COLUMNS)
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
    work_dir = args.output_dir / "work"
    raw_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    progress_path = args.output_dir / "progress.json"
    log_path = args.output_dir / "scing.log"
    network_path = args.output_dir / "network.csv"
    config_path = raw_dir / "scing_config.json"

    write_progress(
        progress_path,
        status="running",
        percent=0,
        phase="init",
        message="Initializing SCING wrapper",
    )

    try:
        raw_params = load_params(args.params)
        params = _resolve_params(raw_params)
        execution_mode = _load_execution_mode(args.params)

        write_progress(
            progress_path,
            status="running",
            percent=10,
            phase="load_input",
            message="Loading expression matrix",
        )
        expression = _read_expression_tsv(args.input)
        _validate_static_dimensions(params, expression)
        group_info = (
            _load_groups(args.extra, expression)
            if execution_mode == "group_emulated"
            else None
        )
        _write_config(
            config_path,
            params=params,
            expression=expression,
            execution_mode=execution_mode,
            group_info=group_info,
            threads=args.threads,
        )

        with log_path.open("w", encoding="utf-8") as log_fh:
            log_fh.write("SCING wrapper starting\n")
            log_fh.write(f"upstream_ref={SCING_REF}\n")
            log_fh.write(f"execution_mode={execution_mode}\n")
            log_fh.write(f"genes={len(expression.gene_ids)} columns={len(expression.column_ids)}\n")
            log_fh.write(f"threads={args.threads} mem_per_core={MEM_PER_CORE_BYTES}\n")
            if group_info is not None:
                log_fh.write(f"group_count={len(set(group_info.groups.values()))}\n")
            log_fh.write("\n")
            log_fh.flush()

            with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
                raw_network_path = _run_scing(
                    expression=expression,
                    params=params,
                    raw_dir=raw_dir,
                    work_dir=work_dir,
                    progress_path=progress_path,
                    threads=args.threads,
                )

        write_progress(
            progress_path,
            status="running",
            percent=92,
            phase="write_output",
            message="Writing network.csv",
        )
        edge_count = _convert_network(raw_network_path, network_path, expression)
        write_progress(
            progress_path,
            status="completed",
            percent=100,
            phase="done",
            message="SCING inference finished",
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
            message="Inference failed",
            error=str(exc),
        )
        raise


if __name__ == "__main__":
    main()
