"""DigNet wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import os
import pickle
import shutil
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
for _thread_env in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_thread_env, "1")

import pandas as pd

from _run_tool_common import (
    load_params,
    require_param_keys,
    validate_runtime_inputs,
    warn_unknown_params,
    write_progress,
)


DIGNET_HOME = Path(os.environ.get("DIGNET_HOME", "/opt/DigNet"))
DIGNET_REF = os.environ.get(
    "DIGNET_REF", "5109401ac242d2b671156eab4b4a5fabd808b612"
)
MODEL_PATH = (
    DIGNET_HOME
    / "pre_train"
    / "S33_Cancer_cell_checkpoint_pre_train_20240326.pth"
)
PCA_PATH = DIGNET_HOME / "result" / "S33_Cancer_cell_pca_model.pkl"
KEGG_PATH = DIGNET_HOME / "pathway" / "kegg" / "KEGG_all_pathway.pkl"
REGNETWORK_PATH = DIGNET_HOME / "pathway" / "Regnetwork" / "2022.human.source"
TF_PATH = DIGNET_HOME / "GRN" / "TF.txt"

NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
SUPPORTED_MODES = {"global", "group_emulated"}


@dataclass(frozen=True)
class ResolvedParams:
    gene_set: str
    metacell: bool
    knn: int
    metacell_count: int
    ensemble: int
    diffusion_timesteps: int


@dataclass(frozen=True)
class ExpressionInput:
    values: pd.DataFrame
    gene_ids: list[str]
    cell_ids: list[str]


@dataclass(frozen=True)
class PreparedInputs:
    upstream_expression: Path
    upstream_gene_set: str
    selected_genes: list[str]
    effective_feature_count: int
    pca_feature_count: int | None
    n_reference_edges: int


def _as_int(name: str, value: Any, *, min_value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value < min_value:
        raise ValueError(f"{name} must be >= {min_value}.")
    return int(value)


def _as_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return bool(value)


def _resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    expected = {
        "gene_set",
        "metacell",
        "knn",
        "metacell_count",
        "ensemble",
        "diffusion_timesteps",
    }
    require_param_keys(raw_params, expected)
    warn_unknown_params(raw_params, expected)

    gene_set = raw_params["gene_set"]
    if not isinstance(gene_set, str) or not gene_set.strip():
        raise ValueError(
            "gene_set is required. Use a human KEGG id such as hsa05224, "
            "or all_expression_genes."
        )
    gene_set = gene_set.strip()

    return ResolvedParams(
        gene_set=gene_set,
        metacell=_as_bool("metacell", raw_params["metacell"]),
        knn=_as_int("knn", raw_params["knn"], min_value=1),
        metacell_count=_as_int(
            "metacell_count", raw_params["metacell_count"], min_value=1
        ),
        ensemble=_as_int("ensemble", raw_params["ensemble"], min_value=1),
        diffusion_timesteps=_as_int(
            "diffusion_timesteps", raw_params["diffusion_timesteps"], min_value=1
        ),
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
        raise ValueError("DigNet supports only execution.mode=global or group_emulated.")
    return mode


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        line = fh.readline()
    if not line:
        raise ValueError(f"{path.name} is empty.")
    return line.rstrip("\n\r").split("\t")


def _find_duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicated: set[str] = set()
    for value in values:
        if value in seen:
            duplicated.add(value)
        seen.add(value)
    return sorted(duplicated)


def _read_expression_tsv(path: Path) -> ExpressionInput:
    header = _read_header(path)
    if len(header) < 2:
        raise ValueError("expression.tsv must contain a gene column and at least one cell.")

    cell_ids = [str(value) for value in header[1:]]
    if any(not cell_id for cell_id in cell_ids):
        raise ValueError("expression.tsv contains an empty cell identifier.")
    duplicated_cells = _find_duplicates(cell_ids)
    if duplicated_cells:
        raise ValueError(
            "expression.tsv contains duplicated cell identifiers: "
            + ", ".join(duplicated_cells)
        )

    raw = pd.read_csv(path, sep="\t", header=0, dtype=str, keep_default_na=False)
    if raw.shape[1] != len(header):
        raise ValueError("expression.tsv rows do not match the header width.")

    gene_ids = raw.iloc[:, 0].astype(str).tolist()
    if any(not gene_id for gene_id in gene_ids):
        raise ValueError("expression.tsv contains an empty gene identifier.")
    duplicated_genes = _find_duplicates(gene_ids)
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
    if (values < 0).any():
        raise ValueError(
            "expression.tsv contains negative values. DigNet's selected public "
            "CSV path applies log1p/min-max expression preprocessing and expects "
            "non-negative expression values."
        )

    expression = pd.DataFrame(values, index=gene_ids, columns=cell_ids)
    return ExpressionInput(values=expression, gene_ids=gene_ids, cell_ids=cell_ids)


def _load_kegg(path: Path) -> dict[str, list[str]]:
    with path.open("rb") as fh:
        data = pickle.load(fh)
    if not isinstance(data, dict):
        raise ValueError("DigNet KEGG resource is not a dictionary.")
    return {str(key): [str(value) for value in values] for key, values in data.items()}


def _count_reference_edges(selected_genes: list[str]) -> int:
    selected = set(selected_genes)
    human_network = pd.read_csv(REGNETWORK_PATH, sep="\t", header=None, dtype=str)
    mask = human_network.iloc[:, 0].isin(selected) & human_network.iloc[:, 2].isin(
        selected
    )
    return int(mask.sum())


def _load_pca_feature_count(path: Path) -> int | None:
    with path.open("rb") as fh:
        pca = pickle.load(fh)
    raw_value = getattr(pca, "n_features_in_", None)
    if raw_value is None:
        return None
    return int(raw_value)


def _write_user_gene_set(path: Path, genes: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["gene"])
        for gene in genes:
            writer.writerow([gene])


def _write_upstream_expression(path: Path, expression: pd.DataFrame) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["gene", "__andrea_dignet_drop__"] + list(expression.columns))
        for gene, row in expression.iterrows():
            writer.writerow([gene, 0.0] + [float(value) for value in row.tolist()])


def _prepare_inputs(
    *,
    expression: ExpressionInput,
    params: ResolvedParams,
    raw_dir: Path,
) -> PreparedInputs:
    kegg = _load_kegg(KEGG_PATH)
    expression_genes = set(expression.gene_ids)

    if params.gene_set == "all_expression_genes":
        selected_genes = list(expression.gene_ids)
        gene_set_path = raw_dir / "andrea_all_expression_genes.csv"
        _write_user_gene_set(gene_set_path, selected_genes)
        upstream_gene_set = str(gene_set_path)
    elif params.gene_set.startswith("hsa"):
        if params.gene_set not in kegg:
            raise ValueError(
                f"gene_set={params.gene_set!r} is not present in DigNet's bundled "
                "human KEGG resource."
            )
        selected_genes = [
            gene for gene in expression.gene_ids if gene in set(kegg[params.gene_set])
        ]
        upstream_gene_set = params.gene_set
    else:
        raise ValueError(
            "gene_set must be a human KEGG id beginning with 'hsa' or the "
            "wrapper sentinel all_expression_genes."
        )

    missing = sorted(set(selected_genes).difference(expression_genes))
    if missing:
        raise ValueError(
            "Internal gene selection error; selected genes not present in expression: "
            + ", ".join(missing[:8])
        )

    if len(selected_genes) < 10 or len(selected_genes) > 200:
        raise ValueError(
            "DigNet's selected public CSV path accepts 10 to 200 selected genes; "
            f"gene_set={params.gene_set!r} retained {len(selected_genes)} expression genes."
        )

    validation_errors: list[str] = []
    if params.metacell and params.knn > len(expression.cell_ids):
        validation_errors.append(
            f"knn={params.knn} must be <= number of input cells "
            f"({len(expression.cell_ids)}) when metacell=true."
        )

    effective_feature_count = len(expression.cell_ids)
    if params.metacell and len(expression.cell_ids) > params.metacell_count:
        effective_feature_count = params.metacell_count

    pca_feature_count = _load_pca_feature_count(PCA_PATH)
    if pca_feature_count is not None and effective_feature_count != pca_feature_count:
        validation_errors.append(
            "The selected DigNet pretrained CSV model requires "
            f"{pca_feature_count} expression features after metacell preprocessing, "
            f"but this run would produce {effective_feature_count}. Use exactly "
            f"{pca_feature_count} cells with metacell=false, or configure "
            f"metacell=true with metacell_count={pca_feature_count} and more than "
            f"{pca_feature_count} input cells."
        )

    n_reference_edges = _count_reference_edges(selected_genes)
    if n_reference_edges <= 0:
        if params.gene_set == "all_expression_genes":
            validation_errors.append(
                "all_expression_genes retained "
                f"{len(selected_genes)} expression genes, but none form a bundled "
                "human RegNetwork source-target edge. This option creates a "
                "temporary gene-list CSV; it does not bypass DigNet's requirement "
                "that expression gene ids match its bundled human gene symbols."
            )
        else:
            validation_errors.append(
                "The selected public CSV path found no bundled RegNetwork reference "
                f"edges among the {len(selected_genes)} genes retained by "
                f"gene_set={params.gene_set!r}."
            )

    if validation_errors:
        raise ValueError(
            "DigNet input is incompatible with the selected pretrained CSV model: "
            + " ".join(validation_errors)
        )

    upstream_expression = raw_dir / "upstream_expression.csv"
    _write_upstream_expression(upstream_expression, expression.values)

    return PreparedInputs(
        upstream_expression=upstream_expression,
        upstream_gene_set=upstream_gene_set,
        selected_genes=selected_genes,
        effective_feature_count=effective_feature_count,
        pca_feature_count=pca_feature_count,
        n_reference_edges=n_reference_edges,
    )


def _ensure_writable_upstream_work(work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    for name in ("GRN", "pathway"):
        target = DIGNET_HOME / name
        link = work_dir / name
        if link.exists() or link.is_symlink():
            continue
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            shutil.copytree(target, link, dirs_exist_ok=True)


def _write_selected_genes(path: Path, genes: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["gene"])
        for gene in genes:
            writer.writerow([gene])


def _write_model_config(
    path: Path,
    *,
    params: ResolvedParams,
    prepared: PreparedInputs,
    mode: str,
    threads: int,
    n_job: int,
) -> None:
    payload = {
        "upstream_repo": "https://github.com/zpliulab/DigNet",
        "upstream_ref": DIGNET_REF,
        "entrypoint": "Config() -> DigNet(args) -> load_test_data() -> DigNet.test()",
        "execution_mode": mode,
        "model_path": str(MODEL_PATH),
        "pca_path": str(PCA_PATH),
        "gene_set": params.gene_set,
        "upstream_gene_set": prepared.upstream_gene_set,
        "selected_gene_count": len(prepared.selected_genes),
        "reference_edge_count": prepared.n_reference_edges,
        "metacell": params.metacell,
        "knn": params.knn,
        "metacell_count": params.metacell_count,
        "effective_feature_count": prepared.effective_feature_count,
        "pca_feature_count": prepared.pca_feature_count,
        "ensemble": params.ensemble,
        "diffusion_timesteps": params.diffusion_timesteps,
        "requested_threads": threads,
        "upstream_n_job": n_job,
        "device_policy": "cpu_only",
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def _configure_torch_threads() -> None:
    import torch

    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


def _run_dignet(
    *,
    prepared: PreparedInputs,
    params: ResolvedParams,
    raw_dir: Path,
    log_path: Path,
    threads: int,
) -> pd.DataFrame:
    n_job = max(1, min(int(threads), int(params.ensemble)))
    work_dir = raw_dir / "upstream_work"
    _ensure_writable_upstream_work(work_dir)

    previous_cwd = Path.cwd()
    if str(DIGNET_HOME) not in sys.path:
        sys.path.insert(0, str(DIGNET_HOME))

    with log_path.open("a", encoding="utf-8") as log_fh:
        log_fh.write("Starting DigNet public testing flow.\n")
        log_fh.write(f"DIGNET_HOME={DIGNET_HOME}\n")
        log_fh.write(f"MODEL_PATH={MODEL_PATH}\n")
        log_fh.write(f"PCA_PATH={PCA_PATH}\n")
        log_fh.flush()
        with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
            os.chdir(work_dir)
            try:
                _configure_torch_threads()
                import torch
                from config import Config
                from DigNet import DigNet

                args = Config()
                args.pca_file = str(PCA_PATH)
                args.save_dir = str(raw_dir)
                args.save_label = "andrea_dignet"
                args.test_pathway = prepared.upstream_gene_set
                args.diffusion_timesteps = params.diffusion_timesteps
                args.metacell = params.metacell
                args.KNN = params.knn
                args.Cnum = params.metacell_count
                args.use_pca = "30"
                args.ensemble = params.ensemble
                args.max_nodes = None
                args.show = False
                args.n_job = n_job

                trainer = DigNet(args)
                diffusion_pre = torch.load(str(MODEL_PATH), map_location=trainer.device)
                testdata, truelabel = trainer.load_test_data(
                    str(prepared.upstream_expression),
                    num=0,
                    diffusion_pre=diffusion_pre,
                )
                adj_final = trainer.test(diffusion_pre, testdata, truelabel)
            finally:
                os.chdir(previous_cwd)

    if not isinstance(adj_final, pd.DataFrame):
        adj_final = pd.DataFrame(adj_final)
    adj_final.to_csv(raw_dir / "adj_final.csv")
    return adj_final


def _network_from_adjacency(adj: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for source in adj.index.astype(str).tolist():
        for target in adj.columns.astype(str).tolist():
            if source == target:
                continue
            value = adj.loc[source, target]
            try:
                score = float(value)
            except Exception:  # noqa: BLE001
                continue
            if not math.isfinite(score) or score <= 0.0:
                continue
            rows.append(
                {
                    "source": source,
                    "target": target,
                    "score": score,
                    "sign": "?",
                    "evidence": "association",
                    "context": "global",
                }
            )

    out = pd.DataFrame(rows, columns=NETWORK_COLUMNS)
    if not out.empty:
        out = out.sort_values(
            by=["score", "source", "target"],
            ascending=[False, True, True],
            kind="mergesort",
        ).reset_index(drop=True)
    return out


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
        required_paths=[MODEL_PATH, PCA_PATH, KEGG_PATH, REGNETWORK_PATH, TF_PATH],
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = args.output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.output_dir / "dignet.log"
    progress_path = args.output_dir / "progress.json"

    write_progress(
        progress_path,
        status="running",
        percent=0,
        phase="init",
        message="Initializing DigNet",
    )

    try:
        raw_params = load_params(args.params)
        params = _resolve_params(raw_params)
        mode = _load_execution_mode(args.params)

        write_progress(
            progress_path,
            status="running",
            percent=10,
            phase="load_input",
            message="Loading expression matrix",
        )
        expression = _read_expression_tsv(args.input)

        write_progress(
            progress_path,
            status="running",
            percent=25,
            phase="prepare",
            message="Preparing DigNet CSV and gene set",
        )
        prepared = _prepare_inputs(
            expression=expression,
            params=params,
            raw_dir=raw_dir,
        )
        _write_selected_genes(raw_dir / "selected_genes.tsv", prepared.selected_genes)
        n_job = max(1, min(args.threads, params.ensemble))
        _write_model_config(
            raw_dir / "model_config.json",
            params=params,
            prepared=prepared,
            mode=mode,
            threads=args.threads,
            n_job=n_job,
        )

        write_progress(
            progress_path,
            status="running",
            percent=40,
            phase="inference",
            message="Running DigNet diffusion generation",
            completed=0,
            total=params.ensemble,
        )
        adj_final = _run_dignet(
            prepared=prepared,
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
            message="Writing network.csv",
        )
        network = _network_from_adjacency(adj_final)
        if network.empty:
            raise RuntimeError("DigNet produced no positive non-self-loop edges.")
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
