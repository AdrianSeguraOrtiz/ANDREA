"""scSGL wrapper for the inference_tools execution contract."""

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
from typing import Any

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
for _thread_env in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMBA_NUM_THREADS",
    "RCPP_PARALLEL_NUM_THREADS",
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


SCSGL_REF = os.environ.get("SCSGL_REF", "7fb2a011f6e1061daf4c976225027e76f4e0e4ea")
NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
SUPPORTED_MODES = {"global", "group_emulated"}
SUPPORTED_KERNELS = {"dotprod", "correlation", "proprho", "zikendall"}
MAX_UPPER_BOUND_STEPS = int(os.environ.get("SCSGL_MAX_UPPER_BOUND_STEPS", "12"))


@dataclass(frozen=True)
class ResolvedParams:
    pos_density: float
    neg_density: float
    association_kernel: str


@dataclass(frozen=True)
class ExpressionInput:
    values: pd.DataFrame
    gene_ids: list[str]
    column_ids: list[str]


@dataclass(frozen=True)
class GroupInfo:
    groups: dict[str, str]


@dataclass(frozen=True)
class ContextRun:
    context: str
    values: pd.DataFrame


@dataclass(frozen=True)
class ContextResult:
    raw_edges: pd.DataFrame
    density_rows: list[dict[str, Any]]
    warnings: list[str]


def _as_density(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric.")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite.")
    if out <= 0.0 or out >= 1.0:
        raise ValueError(f"{name} must satisfy 0 < {name} < 1.")
    return out


def _resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    required = {"pos_density", "neg_density"}
    expected = required | {"association_kernel"}
    require_param_keys(raw_params, required)
    warn_unknown_params(raw_params, expected)

    association_kernel = raw_params.get("association_kernel", "dotprod")
    if association_kernel not in SUPPORTED_KERNELS:
        raise ValueError(
            "association_kernel must be one of: "
            + ", ".join(sorted(SUPPORTED_KERNELS))
        )

    return ResolvedParams(
        pos_density=_as_density("pos_density", raw_params["pos_density"]),
        neg_density=_as_density("neg_density", raw_params["neg_density"]),
        association_kernel=str(association_kernel),
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
        raise ValueError("scSGL supports only execution.mode=global or group_emulated.")
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

    expression = pd.DataFrame(values, index=gene_ids, columns=column_ids)
    if expression.shape[0] < 2:
        raise ValueError("scSGL requires at least two expression genes.")
    if expression.shape[1] < 2:
        raise ValueError("scSGL requires at least two expression columns.")
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

    groups_by_column = dict(zip(column_ids, clusters))
    missing = sorted(set(expression.column_ids).difference(groups_by_column))
    if missing:
        raise ValueError(
            "groups.tsv is missing expression columns: " + ", ".join(missing[:8])
        )
    return GroupInfo(
        groups={column_id: groups_by_column[column_id] for column_id in expression.column_ids}
    )


def _context_runs(expression: ExpressionInput, group_info: GroupInfo | None) -> list[ContextRun]:
    if group_info is None:
        return [ContextRun(context="global", values=expression.values)]

    runs: list[ContextRun] = []
    groups = sorted(set(group_info.groups.values()))
    for group_id in groups:
        columns = [
            column_id
            for column_id in expression.column_ids
            if group_info.groups[column_id] == group_id
        ]
        if len(columns) < 2:
            raise ValueError(
                f"group {group_id!r} has fewer than two expression columns; scSGL cannot run."
            )
        runs.append(
            ContextRun(
                context=f"group:{group_id}",
                values=expression.values.loc[:, columns],
            )
        )
    if not runs:
        raise ValueError("groups.tsv did not define any non-empty groups.")
    return runs


def _configure_threads(threads: int) -> None:
    if threads != 1:
        raise ValueError("scSGL does not expose safe threading controls; --threads must be 1.")
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMBA_NUM_THREADS",
        "RCPP_PARALLEL_NUM_THREADS",
    ):
        os.environ[key] = "1"


def _version_or_unknown(package_name: str) -> str:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return "unknown"


def _install_bounded_density_search(
    graphlearning: Any,
    *,
    context: str,
    density_rows: list[dict[str, Any]],
) -> None:
    if MAX_UPPER_BOUND_STEPS < 1:
        raise ValueError("SCSGL_MAX_UPPER_BOUND_STEPS must be a positive integer.")

    unsigned = graphlearning.unsigned
    call_index = {"value": 0}

    def _bounded_find_bs_upper_bound(k: np.ndarray, d: np.ndarray, density: float) -> float:
        label = "positive" if call_index["value"] == 0 else "negative"
        call_index["value"] += 1
        alpha = 5.0
        best_density = -1.0
        best_alpha = alpha
        for step in range(MAX_UPPER_BOUND_STEPS):
            w = unsigned.learn_ladmm(k, d, alpha, alpha)
            density_est = float(np.count_nonzero(w) / len(w))
            print(
                "ANDREA bounded density search: "
                f"context={context} sign={label} target={density:.6g} "
                f"step={step + 1}/{MAX_UPPER_BOUND_STEPS} "
                f"alpha={alpha:.6g} density={density_est:.6g}",
                flush=True,
            )
            if density_est > best_density:
                best_density = density_est
                best_alpha = alpha
            if density_est > density:
                density_rows.append(
                    {
                        "context": context,
                        "sign": label,
                        "requested_density": float(density),
                        "bracketed": True,
                        "best_upper_bound_density": float(density_est),
                        "best_alpha": float(alpha),
                        "steps": step + 1,
                        "max_steps": MAX_UPPER_BOUND_STEPS,
                    }
                )
                return alpha
            alpha *= 1.5
        density_rows.append(
            {
                "context": context,
                "sign": label,
                "requested_density": float(density),
                "bracketed": False,
                "best_upper_bound_density": float(max(0.0, best_density)),
                "best_alpha": float(best_alpha),
                "steps": MAX_UPPER_BOUND_STEPS,
                "max_steps": MAX_UPPER_BOUND_STEPS,
            }
        )
        return best_alpha

    graphlearning._find_bs_upper_bound = _bounded_find_bs_upper_bound
    graphlearning._andrea_bounded_density_search = True


def _actual_density_by_sign(raw_edges: pd.DataFrame, retained_gene_count: int) -> dict[str, float]:
    total_pairs = retained_gene_count * (retained_gene_count - 1) / 2
    if total_pairs <= 0:
        return {"positive": 0.0, "negative": 0.0}
    positives: set[tuple[str, str]] = set()
    negatives: set[tuple[str, str]] = set()
    for row in raw_edges.itertuples(index=False):
        gene1 = str(getattr(row, "Gene1"))
        gene2 = str(getattr(row, "Gene2"))
        if gene1 == gene2:
            continue
        pair = tuple(sorted((gene1, gene2)))
        weight = float(getattr(row, "EdgeWeight"))
        if weight > 0:
            positives.add(pair)
        elif weight < 0:
            negatives.add(pair)
    return {
        "positive": len(positives) / total_pairs,
        "negative": len(negatives) / total_pairs,
    }


def _retention_rows(run: ContextRun) -> tuple[pd.DataFrame, int]:
    retained = np.count_nonzero(run.values.to_numpy(dtype=float), axis=1) != 0
    rows = pd.DataFrame(
        {
            "context": run.context,
            "gene": run.values.index.astype(str),
            "status": np.where(retained, "retained", "dropped_all_zero"),
        }
    )
    retained_count = int(np.count_nonzero(retained))
    return rows, retained_count


def _validate_context_matrix(run: ContextRun, params: ResolvedParams) -> None:
    if run.values.shape[1] < 2:
        raise ValueError(f"{run.context} has fewer than two expression columns.")

    retention, retained_count = _retention_rows(run)
    if retained_count < 2:
        raise ValueError(
            f"{run.context} has fewer than two nonzero genes after scSGL's all-zero filter."
        )

    retained_values = run.values.loc[
        retention.loc[retention["status"] == "retained", "gene"].tolist(), :
    ].to_numpy(dtype=float)
    if params.association_kernel in {"proprho", "zikendall"} and np.any(retained_values < 0):
        raise ValueError(
            f"{params.association_kernel} requires non-negative expression values."
        )
    if not np.any(np.std(retained_values, axis=1) > 0):
        raise ValueError(
            f"{run.context} has no retained gene with expression variation across columns."
        )


def _run_scsgl_context(run: ContextRun, params: ResolvedParams) -> ContextResult:
    from pysrc import graphlearning

    _validate_context_matrix(run, params)
    density_rows: list[dict[str, Any]] = []
    _install_bounded_density_search(
        graphlearning,
        context=run.context,
        density_rows=density_rows,
    )
    try:
        result = graphlearning.learn_signed_graph(
            run.values.to_numpy(dtype=float),
            pos_density=params.pos_density,
            neg_density=params.neg_density,
            assoc=params.association_kernel,
            gene_names=np.array(run.values.index.astype(str)),
            return_run_time=False,
            verbose=False,
        )
    except Exception as exc:
        raise RuntimeError(f"{run.context}: {exc}") from exc
    if not isinstance(result, pd.DataFrame):
        raise RuntimeError("scSGL returned an unexpected non-DataFrame result.")
    required = {"Gene1", "Gene2", "EdgeWeight"}
    missing = sorted(required.difference(result.columns))
    if missing:
        raise RuntimeError(f"scSGL output is missing columns: {missing}")
    out = result.loc[:, ["Gene1", "Gene2", "EdgeWeight"]].copy()
    out.insert(0, "context", run.context)
    retained_count = _retention_rows(run)[1]
    actual_density = _actual_density_by_sign(out, retained_gene_count=retained_count)
    warnings: list[str] = []
    for row in density_rows:
        sign = str(row["sign"])
        row["actual_density"] = float(actual_density.get(sign, 0.0))
        if not bool(row["bracketed"]):
            warning = (
                f"{run.context}: requested {sign} density "
                f"{float(row['requested_density']):.6g} could not be bracketed after "
                f"{int(row['steps'])} scSGL search step(s); network.csv uses the "
                f"best-effort graph with actual {sign} density "
                f"{float(row['actual_density']):.6g}. The highest density observed "
                f"during the upstream upper-bound probe was "
                f"{float(row['best_upper_bound_density']):.6g}."
            )
            warnings.append(warning)
    return ContextResult(raw_edges=out, density_rows=density_rows, warnings=warnings)


def _convert_edges(
    *,
    raw_edges: pd.DataFrame,
    output_path: Path,
    gene_order: dict[str, int],
) -> int:
    records: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in raw_edges.itertuples(index=False):
        context = str(getattr(row, "context"))
        gene1 = str(getattr(row, "Gene1"))
        gene2 = str(getattr(row, "Gene2"))
        if gene1 == gene2:
            continue
        if gene1 not in gene_order or gene2 not in gene_order:
            continue
        try:
            weight = float(getattr(row, "EdgeWeight"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(weight) or weight == 0.0:
            continue

        if gene_order[gene1] <= gene_order[gene2]:
            source, target = gene1, gene2
        else:
            source, target = gene2, gene1
        key = (context, source, target)
        score = abs(weight)
        sign = "+" if weight > 0.0 else "-"
        existing = records.get(key)
        if existing is None or score > float(existing["score"]):
            records[key] = {
                "source": source,
                "target": target,
                "score": score,
                "sign": sign,
                "evidence": "association",
                "context": context,
            }

    if not records:
        raise RuntimeError("scSGL produced no positive-magnitude non-self edges.")

    out = pd.DataFrame(records.values(), columns=NETWORK_COLUMNS)
    out = out.sort_values(
        ["context", "score", "source", "target"],
        ascending=[True, False, True, True],
    )
    out.to_csv(output_path, index=False, columns=NETWORK_COLUMNS)
    return int(out.shape[0])


def _write_config(
    path: Path,
    *,
    params: ResolvedParams,
    expression: ExpressionInput,
    execution_mode: str,
    group_info: GroupInfo | None,
    contexts: list[ContextRun],
    threads: int,
) -> None:
    payload = {
        "tool": "scsgl",
        "upstream_repo": "https://github.com/Single-Cell-Graph-Learning/scSGL.git",
        "upstream_ref": SCSGL_REF,
        "entrypoint": "pysrc.graphlearning.learn_signed_graph",
        "execution_mode": execution_mode,
        "gene_count": len(expression.gene_ids),
        "expression_column_count": len(expression.column_ids),
        "context_count": len(contexts),
        "contexts": [
            {
                "context": run.context,
                "columns": list(run.values.columns.astype(str)),
                "column_count": int(run.values.shape[1]),
            }
            for run in contexts
        ],
        "requested_threads": threads,
        "upstream_threads": 1,
        "max_upper_bound_steps": MAX_UPPER_BOUND_STEPS,
        "params": asdict(params),
        "group_count": (
            len(set(group_info.groups.values())) if group_info is not None else None
        ),
        "runtime_versions": {
            "numpy": _version_or_unknown("numpy"),
            "pandas": _version_or_unknown("pandas"),
            "scipy": _version_or_unknown("scipy"),
            "scikit_learn": _version_or_unknown("scikit-learn"),
            "numba": _version_or_unknown("numba"),
            "rpy2": _version_or_unknown("rpy2"),
        },
        "score_mapping": "network.csv score=abs(upstream EdgeWeight); sign stores EdgeWeight direction.",
        "edge_convention": "One unordered pair per context; self-loops and zero/non-finite weights are omitted.",
        "thread_environment": {
            key: os.environ.get(key)
            for key in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "BLIS_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
                "NUMBA_NUM_THREADS",
                "RCPP_PARALLEL_NUM_THREADS",
            )
        },
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


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
    _configure_threads(args.threads)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = args.output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    progress_path = args.output_dir / "progress.json"
    log_path = args.output_dir / "scsgl.log"
    network_path = args.output_dir / "network.csv"
    raw_edges_path = raw_dir / "scsgl_edges.tsv"
    config_path = raw_dir / "scsgl_config.json"
    retained_genes_path = raw_dir / "retained_genes.tsv"
    density_search_path = raw_dir / "density_search.tsv"

    write_progress(
        progress_path,
        status="running",
        percent=0,
        phase="init",
        message="Initializing scSGL wrapper",
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
        group_info = (
            _load_groups(args.extra, expression)
            if execution_mode == "group_emulated"
            else None
        )
        contexts = _context_runs(expression, group_info)
        _write_config(
            config_path,
            params=params,
            expression=expression,
            execution_mode=execution_mode,
            group_info=group_info,
            contexts=contexts,
            threads=args.threads,
        )

        retention_tables: list[pd.DataFrame] = []
        raw_tables: list[pd.DataFrame] = []
        density_rows: list[dict[str, Any]] = []
        wrapper_warnings: list[str] = []
        with log_path.open("w", encoding="utf-8") as log_fh:
            log_fh.write("scSGL wrapper starting\n")
            log_fh.write(f"upstream_ref={SCSGL_REF}\n")
            log_fh.write(f"execution_mode={execution_mode}\n")
            log_fh.write(f"genes={len(expression.gene_ids)} columns={len(expression.column_ids)}\n")
            log_fh.write(f"params={asdict(params)}\n")
            log_fh.write(f"threads={args.threads}\n")
            log_fh.write("\n")
            log_fh.flush()

            with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
                for index, run in enumerate(contexts, start=1):
                    write_progress(
                        progress_path,
                        status="running",
                        percent=20 + int(((index - 1) / max(1, len(contexts))) * 60),
                        phase="run_scsgl",
                        message=f"Running scSGL for {run.context}",
                        completed=index - 1,
                        total=len(contexts),
                    )
                    retention, retained_count = _retention_rows(run)
                    retention_tables.append(retention)
                    log_fh.write(
                        f"context={run.context} columns={run.values.shape[1]} "
                        f"retained_genes={retained_count}\n"
                    )
                    log_fh.flush()
                    context_result = _run_scsgl_context(run, params)
                    raw_tables.append(context_result.raw_edges)
                    density_rows.extend(context_result.density_rows)
                    wrapper_warnings.extend(context_result.warnings)
                    for warning in context_result.warnings:
                        log_fh.write(f"warning={warning}\n")
                    log_fh.flush()

        retained = pd.concat(retention_tables, ignore_index=True)
        retained.to_csv(retained_genes_path, sep="\t", index=False)
        raw_edges = pd.concat(raw_tables, ignore_index=True)
        raw_edges.to_csv(raw_edges_path, sep="\t", index=False)
        density_table = pd.DataFrame(
            density_rows,
            columns=[
                "context",
                "sign",
                "requested_density",
                "bracketed",
                "best_upper_bound_density",
                "best_alpha",
                "steps",
                "max_steps",
                "actual_density",
            ],
        )
        density_table.to_csv(density_search_path, sep="\t", index=False)
        if raw_edges.empty:
            raise RuntimeError("scSGL produced an empty raw edge table.")

        write_progress(
            progress_path,
            status="running",
            percent=90,
            phase="write_output",
            message="Writing network.csv",
        )
        gene_order = {gene_id: idx for idx, gene_id in enumerate(expression.gene_ids)}
        edge_count = _convert_edges(
            raw_edges=raw_edges,
            output_path=network_path,
            gene_order=gene_order,
        )
        write_progress(
            progress_path,
            status="completed_with_warnings" if wrapper_warnings else "completed",
            percent=100,
            phase="done",
            message=(
                "scSGL inference finished with warning(s)"
                if wrapper_warnings
                else "scSGL inference finished"
            ),
            completed=edge_count,
            total=edge_count,
            warnings=wrapper_warnings,
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
