"""pySCENIC GRN wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import traceback
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

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
SUPPORTED_MODES = {"global", "group_emulated"}


@dataclass(frozen=True)
class ResolvedParams:
    method: str
    seed: int | None


@dataclass(frozen=True)
class ExpressionInput:
    values: pd.DataFrame
    gene_ids: list[str]
    column_ids: list[str]


@dataclass(frozen=True)
class GroupInfo:
    groups: dict[str, str]


def _configure_thread_environment() -> None:
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ.setdefault(key, "1")


def _as_int_or_none(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer or null.")
    return int(value)


def _resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    expected = {"method", "seed"}
    require_param_keys(raw_params, expected)
    warn_unknown_params(raw_params, expected)

    method = raw_params["method"]
    if not isinstance(method, str) or method not in {"grnboost2", "genie3"}:
        raise ValueError("method must be one of: grnboost2, genie3.")

    return ResolvedParams(
        method=method,
        seed=_as_int_or_none("seed", raw_params["seed"]),
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
        raise ValueError("pySCENIC supports only execution.mode=global or group_emulated.")
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
        raise ValueError("expression.tsv must contain a gene column and at least one expression column.")

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
    if not math.isfinite(float(values.sum())):
        raise ValueError("expression.tsv contains non-finite expression values.")

    expression = pd.DataFrame(values, index=gene_ids, columns=column_ids)
    return ExpressionInput(values=expression, gene_ids=gene_ids, column_ids=column_ids)


def _read_tf_list(extra_dir: Path, expression: ExpressionInput) -> list[str]:
    tf_path = require_extra_file(extra_dir, "tf_list.txt", "tf_list")
    tf_names: list[str] = []
    for line in tf_path.read_text(encoding="utf-8").splitlines():
        token = line.strip()
        if token and not token.startswith("#"):
            tf_names.append(token)

    if not tf_names:
        raise ValueError("tf_list.txt does not contain any transcription factors.")
    duplicated = _find_duplicates(tf_names)
    if duplicated:
        raise ValueError("tf_list.txt contains duplicated TF ids: " + ", ".join(duplicated))

    expression_genes = set(expression.gene_ids)
    missing = [tf for tf in tf_names if tf not in expression_genes]
    if missing:
        raise ValueError(
            "tf_list.txt contains TF ids absent from expression.tsv: "
            + ", ".join(missing)
        )
    return tf_names


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
        groups={
            column_id: groups_by_column[column_id]
            for column_id in expression.column_ids
        }
    )


def _write_gene_alias_map(path: Path, expression: ExpressionInput) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["gene_id", "upstream_gene_id"])
        for gene_id in expression.gene_ids:
            writer.writerow([gene_id, gene_id])


def _write_upstream_expression(path: Path, expression: ExpressionInput) -> None:
    cells_by_genes = expression.values.T
    cells_by_genes.to_csv(path, sep="\t", index=True, index_label="cell")


def _write_upstream_tf_list(path: Path, tf_names: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        for tf_name in tf_names:
            fh.write(f"{tf_name}\n")


def _pyscenic_version() -> str:
    try:
        return metadata.version("pyscenic")
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
    command: list[str],
) -> None:
    payload = {
        "tool": "pyscenic",
        "upstream_package": "pyscenic",
        "upstream_version": _pyscenic_version(),
        "entrypoint": "pyscenic grn",
        "execution_mode": execution_mode,
        "gene_count": len(expression.gene_ids),
        "expression_column_count": len(expression.column_ids),
        "requested_threads": threads,
        "upstream_num_workers": threads,
        "params": {
            "method": params.method,
            "seed": params.seed,
            "seed_rule": (
                "omitted; pySCENIC uses its random-seed default"
                if params.seed is None
                else "passed to pyscenic grn --seed"
            ),
        },
        "group_count": (
            len(set(group_info.groups.values())) if group_info is not None else None
        ),
        "command": command,
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def _build_command(
    *,
    expression_path: Path,
    tf_path: Path,
    adjacency_path: Path,
    params: ResolvedParams,
    threads: int,
) -> list[str]:
    command = [
        "pyscenic",
        "grn",
        str(expression_path),
        str(tf_path),
        "--method",
        params.method,
        "--num_workers",
        str(threads),
        "--client_or_address",
        "local",
        "-o",
        str(adjacency_path),
    ]
    if params.seed is not None:
        command.extend(["--seed", str(params.seed)])
    return command


def _run_pyscenic(command: list[str], log_path: Path) -> None:
    env = os.environ.copy()
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        env[key] = "1"

    with log_path.open("a", encoding="utf-8") as log_fh:
        log_fh.write("Running upstream command:\n")
        log_fh.write(" ".join(command) + "\n\n")
        log_fh.flush()
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=env,
        )
        log_fh.write(f"\npySCENIC exit_code={result.returncode}\n")
    if result.returncode != 0:
        raise RuntimeError(f"pyscenic grn failed with exit code {result.returncode}.")


def _convert_adjacencies(raw_path: Path, network_path: Path) -> int:
    if not raw_path.exists() or raw_path.stat().st_size <= 0:
        raise RuntimeError("pySCENIC did not produce a non-empty adjacency table.")

    raw = pd.read_csv(raw_path, sep="\t", header=0, dtype={"TF": str, "target": str})
    required = {"TF", "target", "importance"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"pySCENIC adjacency output is missing columns: {missing}")

    score = pd.to_numeric(raw["importance"], errors="coerce")
    keep = score.notna() & score.map(math.isfinite) & (score > 0)
    keep &= raw["TF"].astype(str) != raw["target"].astype(str)
    filtered = raw.loc[keep].copy()
    if filtered.empty:
        raise RuntimeError("pySCENIC produced no positive non-self edges.")

    filtered["score"] = pd.to_numeric(filtered["importance"], errors="raise")
    out = pd.DataFrame(
        {
            "source": filtered["TF"].astype(str),
            "target": filtered["target"].astype(str),
            "score": filtered["score"].astype(float),
            "sign": "?",
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
    work_dir = args.output_dir / "work"
    raw_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    progress_path = args.output_dir / "progress.json"
    log_path = args.output_dir / "pyscenic.log"
    network_path = args.output_dir / "network.csv"
    adjacency_path = raw_dir / "adjacencies.tsv"
    config_path = raw_dir / "pyscenic_config.json"
    alias_map_path = raw_dir / "gene_alias_map.tsv"
    upstream_expression_path = work_dir / "pyscenic_expression.tsv"
    upstream_tf_path = work_dir / "tf_list.txt"

    write_progress(
        progress_path,
        status="running",
        percent=0,
        phase="init",
        message="Initializing pySCENIC wrapper",
    )

    try:
        _configure_thread_environment()
        raw_params = load_params(args.params)
        params = _resolve_params(raw_params)
        execution_mode = _load_execution_mode(args.params)

        write_progress(
            progress_path,
            status="running",
            percent=10,
            phase="load_input",
            message="Loading expression matrix and TF list",
        )
        expression = _read_expression_tsv(args.input)
        tf_names = _read_tf_list(args.extra, expression)
        group_info = (
            _load_groups(args.extra, expression)
            if execution_mode == "group_emulated"
            else None
        )

        write_progress(
            progress_path,
            status="running",
            percent=25,
            phase="prepare",
            message="Preparing pySCENIC input files",
        )
        _write_gene_alias_map(alias_map_path, expression)
        _write_upstream_expression(upstream_expression_path, expression)
        _write_upstream_tf_list(upstream_tf_path, tf_names)
        command = _build_command(
            expression_path=upstream_expression_path,
            tf_path=upstream_tf_path,
            adjacency_path=adjacency_path,
            params=params,
            threads=args.threads,
        )
        _write_config(
            config_path,
            params=params,
            expression=expression,
            execution_mode=execution_mode,
            group_info=group_info,
            threads=args.threads,
            command=command,
        )

        with log_path.open("w", encoding="utf-8") as log_fh:
            log_fh.write("pySCENIC wrapper starting\n")
            log_fh.write(f"pyscenic_version={_pyscenic_version()}\n")
            log_fh.write(f"execution_mode={execution_mode}\n")
            log_fh.write(f"genes={len(expression.gene_ids)} columns={len(expression.column_ids)}\n")
            log_fh.write(f"tf_count={len(tf_names)} threads={args.threads}\n")
            if group_info is not None:
                log_fh.write(f"group_count={len(set(group_info.groups.values()))}\n")
            log_fh.write("\n")

        write_progress(
            progress_path,
            status="running",
            percent=35,
            phase="inference",
            message="Running pyscenic grn",
        )
        _run_pyscenic(command, log_path)

        write_progress(
            progress_path,
            status="running",
            percent=90,
            phase="write_output",
            message="Writing network.csv",
        )
        edge_count = _convert_adjacencies(adjacency_path, network_path)

        write_progress(
            progress_path,
            status="completed",
            percent=100,
            phase="done",
            message="pySCENIC inference finished",
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
