"""scGeneRAI wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from _run_tool_common import (
    load_params,
    optional_extra_file,
    require_extra_file,
    require_param_keys,
    validate_runtime_inputs,
    warn_unknown_params,
    write_progress,
)


NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
SUPPORTED_MODES = {"column_native", "group_aggregated"}


@dataclass(frozen=True)
class ResolvedParams:
    nepochs: int
    model_depth: int
    lr: float
    batch_size: int
    lr_decay: float
    early_stopping: bool


@dataclass(frozen=True)
class ExpressionInput:
    upstream_data: pd.DataFrame
    gene_ids: list[str]
    cell_ids: list[str]
    dropped_zero_variance_genes: list[str]


def _as_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def _as_int(name: str, value: Any, *, min_value: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}.")
    return value


def _as_float(
    name: str,
    value: Any,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
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
    if max_value is not None and out > max_value:
        raise ValueError(f"{name} must be <= {max_value}.")
    return out


def resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    expected = {
        "nepochs",
        "model_depth",
        "lr",
        "batch_size",
        "lr_decay",
        "early_stopping",
    }
    require_param_keys(raw_params, expected)
    warn_unknown_params(raw_params, expected)

    return ResolvedParams(
        nepochs=_as_int("nepochs", raw_params["nepochs"], min_value=1),
        model_depth=_as_int("model_depth", raw_params["model_depth"], min_value=0),
        lr=_as_float("lr", raw_params["lr"], min_value=0.0, exclusive_min=True),
        batch_size=_as_int("batch_size", raw_params["batch_size"], min_value=1),
        lr_decay=_as_float(
            "lr_decay",
            raw_params["lr_decay"],
            min_value=0.0,
            max_value=1.0,
            exclusive_min=True,
        ),
        early_stopping=_as_bool("early_stopping", raw_params["early_stopping"]),
    )


def load_execution_mode(params_path: Path) -> str:
    execution_path = params_path.parent / "execution.json"
    if not execution_path.exists():
        return "column_native"
    with execution_path.open("r", encoding="utf-8") as fh:
        execution = json.load(fh)
    if not isinstance(execution, dict):
        raise ValueError("execution.json must be a JSON object.")
    mode = execution.get("mode", "column_native")
    if not isinstance(mode, str):
        raise ValueError("execution.mode must be a string.")
    if mode not in SUPPORTED_MODES:
        raise ValueError(
            "scGeneRAI supports only execution.mode=column_native or "
            "execution.mode=group_aggregated."
        )
    return mode


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        line = fh.readline()
    if not line:
        raise ValueError(f"{path.name} is empty.")
    return line.rstrip("\n\r").split("\t")


def read_expression_tsv(path: Path) -> ExpressionInput:
    header = _read_header(path)
    if len(header) < 2:
        raise ValueError("expression.tsv must have a gene column and at least one cell.")

    cell_ids = [str(value) for value in header[1:]]
    if any(not value for value in cell_ids):
        raise ValueError("expression.tsv contains an empty cell identifier.")
    duplicated_cells = sorted({value for value in cell_ids if cell_ids.count(value) > 1})
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
    duplicated_genes = sorted({value for value in gene_ids if gene_ids.count(value) > 1})
    if duplicated_genes:
        raise ValueError(
            "expression.tsv contains duplicated gene identifiers: "
            + ", ".join(duplicated_genes)
        )

    numeric = raw.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError("expression.tsv contains non-numeric expression values.")

    values = numeric.to_numpy(dtype=float)
    if not math.isfinite(float(values.sum())):
        raise ValueError("expression.tsv contains non-finite expression values.")

    raw_cells_by_genes = pd.DataFrame(values.T, columns=gene_ids)
    means = raw_cells_by_genes.mean(axis=0)
    sds = raw_cells_by_genes.std(axis=0, ddof=1)
    keep = sds.notna() & (sds > 0)
    dropped_zero_variance_genes = [
        str(gene_id) for gene_id, keep_gene in zip(gene_ids, keep.tolist()) if not keep_gene
    ]
    retained_gene_ids = [
        str(gene_id) for gene_id, keep_gene in zip(gene_ids, keep.tolist()) if keep_gene
    ]
    if not retained_gene_ids:
        raise ValueError("expression.tsv contains no genes with non-zero variance.")

    upstream_data = (raw_cells_by_genes.loc[:, retained_gene_ids] - means.loc[retained_gene_ids]) / sds.loc[retained_gene_ids]
    upstream_data.index = pd.RangeIndex(start=0, stop=len(cell_ids), step=1)
    return ExpressionInput(
        upstream_data=upstream_data,
        gene_ids=retained_gene_ids,
        cell_ids=cell_ids,
        dropped_zero_variance_genes=dropped_zero_variance_genes,
    )


def load_column_descriptors(extra_dir: Path, cell_ids: list[str]) -> pd.DataFrame | None:
    path = optional_extra_file(extra_dir, "column_descriptors.tsv")
    if path is None:
        return None

    header = _read_header(path)
    if len(header) < 2:
        raise ValueError(
            "column_descriptors.tsv must have an expression-column id column and at least one descriptor."
        )

    raw = pd.read_csv(path, sep="\t", header=0, dtype=str, keep_default_na=False)
    id_col = raw.columns[0]
    descriptor_cols = list(raw.columns[1:])
    descriptor_cell_ids = raw[id_col].astype(str).tolist()
    if any(not value for value in descriptor_cell_ids):
        raise ValueError("column_descriptors.tsv contains an empty expression-column identifier.")
    duplicated = sorted(
        {value for value in descriptor_cell_ids if descriptor_cell_ids.count(value) > 1}
    )
    if duplicated:
        raise ValueError(
            "column_descriptors.tsv contains duplicated expression-column identifiers: "
            + ", ".join(duplicated)
        )
    missing = [cell_id for cell_id in cell_ids if cell_id not in descriptor_cell_ids]
    extra = [cell_id for cell_id in descriptor_cell_ids if cell_id not in cell_ids]
    if missing or extra:
        details = []
        if missing:
            details.append("missing expression columns: " + ", ".join(missing))
        if extra:
            details.append("unknown expression columns: " + ", ".join(extra))
        raise ValueError("column_descriptors.tsv must match expression columns exactly (" + "; ".join(details) + ").")

    aligned = raw.set_index(id_col).loc[cell_ids, descriptor_cols].reset_index(drop=True)
    for col in descriptor_cols:
        aligned[col] = aligned[col].astype(str)
        if (aligned[col] == "").any():
            raise ValueError(f"column_descriptors.tsv column {col!r} contains empty values.")
    return aligned


def validate_groups(extra_dir: Path, cell_ids: list[str]) -> None:
    path = require_extra_file(extra_dir, "groups.tsv", "groups")
    header = _read_header(path)
    if len(header) < 2 or "cluster" not in header[1:]:
        raise ValueError("groups.tsv must contain a first expression-column id column and a cluster column.")

    raw = pd.read_csv(path, sep="\t", header=0, dtype=str, keep_default_na=False)
    id_col = raw.columns[0]
    group_cell_ids = raw[id_col].astype(str).tolist()
    if any(not value for value in group_cell_ids):
        raise ValueError("groups.tsv contains an empty expression-column identifier.")
    duplicated = sorted({value for value in group_cell_ids if group_cell_ids.count(value) > 1})
    if duplicated:
        raise ValueError("groups.tsv contains duplicated expression-column identifiers: " + ", ".join(duplicated))
    missing = [cell_id for cell_id in cell_ids if cell_id not in group_cell_ids]
    if missing:
        raise ValueError("groups.tsv is missing expression columns: " + ", ".join(missing))
    clusters = raw.set_index(id_col).loc[cell_ids, "cluster"].astype(str)
    if (clusters == "").any():
        raise ValueError("groups.tsv contains empty cluster values.")


def write_alias_map(raw_dir: Path, cell_ids: list[str]) -> None:
    alias_path = raw_dir / "cell_alias_map.tsv"
    with alias_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("sample_index\tupstream_sample_name\tcell_id\n")
        for idx, cell_id in enumerate(cell_ids):
            fh.write(f"{idx}\t{idx}\t{cell_id}\n")


def append_log(log_path: Path, message: str) -> None:
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(message.rstrip() + "\n")


def find_raw_lrp_file(results_dir: Path, sample_index: int) -> Path:
    expected = results_dir / f"LRP_{sample_index}_{sample_index}.csv"
    if expected.exists():
        return expected

    matches = sorted(results_dir.glob(f"LRP_{sample_index}_*.csv"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"Missing scGeneRAI result CSV for sample index {sample_index}.")
    raise RuntimeError(
        f"Multiple scGeneRAI result CSVs matched sample index {sample_index}: "
        + ", ".join(path.name for path in matches)
    )


def convert_raw_results(raw_dir: Path, cell_ids: list[str]) -> pd.DataFrame:
    results_dir = raw_dir / "results"
    if not results_dir.exists():
        raise FileNotFoundError("scGeneRAI did not create raw/results/.")

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for sample_index, cell_id in enumerate(cell_ids):
        raw_path = find_raw_lrp_file(results_dir, sample_index)
        raw = pd.read_csv(raw_path)
        required = {"LRP", "source_gene", "target_gene"}
        missing = sorted(required.difference(raw.columns))
        if missing:
            raise ValueError(f"{raw_path.name} is missing required columns: {missing}")

        for record in raw[["LRP", "source_gene", "target_gene"]].to_dict("records"):
            source = str(record["source_gene"])
            target = str(record["target_gene"])
            if source == target:
                continue
            score = float(record["LRP"])
            if not math.isfinite(score) or score <= 0.0:
                continue
            context = f"column:{cell_id}"
            pair_key = tuple(sorted((source, target)))
            key = (context, pair_key[0], pair_key[1])
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "source": source,
                    "target": target,
                    "score": score,
                    "sign": "?",
                    "evidence": "association",
                    "context": context,
                }
            )

    return pd.DataFrame(rows, columns=NETWORK_COLUMNS)


def run_scgenerai(
    *,
    expression: ExpressionInput,
    descriptors: pd.DataFrame | None,
    params: ResolvedParams,
    raw_dir: Path,
    log_path: Path,
    threads: int,
) -> None:
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ[key] = str(threads)

    import scGeneRAI as scgenerai_module  # noqa: PLC0415

    try:
        import torch  # noqa: PLC0415

        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
    except Exception as exc:  # noqa: BLE001
        append_log(log_path, f"Warning: failed to set torch thread count: {exc}")

    model = scgenerai_module.scGeneRAI()
    with log_path.open("a", encoding="utf-8") as log_fh:
        with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
            model.fit(
                expression.upstream_data,
                nepochs=params.nepochs,
                model_depth=params.model_depth,
                lr=params.lr,
                batch_size=params.batch_size,
                lr_decay=params.lr_decay,
                descriptors=descriptors,
                early_stopping=params.early_stopping,
                device_name="cpu",
            )
            model.predict_networks(
                expression.upstream_data,
                descriptors=descriptors,
                LRPau=True,
                remove_descriptors=True,
                device_name="cpu",
                PATH=str(raw_dir),
            )


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
    log_path = args.output_dir / "scgenerai.log"
    append_log(log_path, "Starting scGeneRAI wrapper.")
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
        descriptors = load_column_descriptors(args.extra, expression.cell_ids)
        if mode == "group_aggregated":
            validate_groups(args.extra, expression.cell_ids)
        write_alias_map(raw_dir, expression.cell_ids)

        append_log(
            log_path,
            (
                f"Loaded {len(expression.cell_ids)} cells and "
                f"{len(expression.gene_ids)} genes; mode={mode}; "
                f"descriptors={'yes' if descriptors is not None else 'no'}."
            ),
        )
        append_log(
            log_path,
            "Applied per-gene z-score standardization before scGeneRAI fit().",
        )
        if expression.dropped_zero_variance_genes:
            append_log(
                log_path,
                (
                    "Dropped zero-variance genes before scGeneRAI z-score "
                    "standardization: "
                    + ", ".join(expression.dropped_zero_variance_genes)
                ),
            )

        write_progress(
            progress_path,
            status="running",
            percent=15,
            phase="fit",
            message="Training scGeneRAI model",
        )
        run_scgenerai(
            expression=expression,
            descriptors=descriptors,
            params=params,
            raw_dir=raw_dir,
            log_path=log_path,
            threads=args.threads,
        )

        write_progress(
            progress_path,
            status="running",
            percent=90,
            phase="write_output",
            message="Converting raw scGeneRAI results",
        )
        network = convert_raw_results(raw_dir, expression.cell_ids)
        if network.empty:
            raise RuntimeError("scGeneRAI produced no positive LRPau edges.")
        network.to_csv(args.output_dir / "network.csv", index=False)

        write_progress(
            progress_path,
            status="completed",
            percent=100,
            phase="done",
            message="Inference finished",
            completed=len(network),
            total=len(network),
        )
        append_log(log_path, f"Wrote {len(network)} positive network rows.")
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
