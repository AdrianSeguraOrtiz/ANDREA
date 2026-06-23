"""Planet wrapper for the inference_tools execution contract."""

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


PLANET_HOME = Path(os.environ.get("PLANET_HOME", "/opt/project-Planet"))
PLANET_REF = os.environ.get(
    "PLANET_REF", "3d8be3ca788dc436e8d2c888facdeffe49553c61"
)
MODEL_PATH = (
    PLANET_HOME / "pre-train" / "Pre-training_weights_on_simulated_datasets.pth"
)
KEGG_PATH = PLANET_HOME / "pathway" / "kegg" / "KEGG_all_pathway.pkl"
REGNETWORK_PATH = PLANET_HOME / "pathway" / "Regnetwork" / "2022.human.source"
TF_PATH = PLANET_HOME / "GRN" / "TF.txt"

NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
SUPPORTED_MODES = {"global", "group_emulated"}
_TORCH_THREADS_CONFIGURED = False


@dataclass(frozen=True)
class ResolvedParams:
    gene_set: str
    metacell: bool
    knn: int
    metacell_count: int
    ensemble: int
    diffusion_timesteps: int
    sampling_timesteps: int


@dataclass(frozen=True)
class ExpressionInput:
    values: pd.DataFrame
    gene_ids: list[str]
    cell_ids: list[str]


@dataclass(frozen=True)
class CheckpointMetadata:
    input_feature_count: int | None
    max_nodes: int | None
    edge_percent: float | None


@dataclass(frozen=True)
class PreparedInputs:
    upstream_expression: Path
    upstream_gene_set: str
    selected_genes: list[str]
    effective_feature_count: int
    checkpoint_input_feature_count: int | None
    checkpoint_max_nodes: int | None
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
        "sampling_timesteps",
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

    diffusion_timesteps = _as_int(
        "diffusion_timesteps", raw_params["diffusion_timesteps"], min_value=1
    )
    sampling_timesteps = _as_int(
        "sampling_timesteps", raw_params["sampling_timesteps"], min_value=1
    )
    if sampling_timesteps > diffusion_timesteps:
        raise ValueError("sampling_timesteps must be <= diffusion_timesteps.")

    return ResolvedParams(
        gene_set=gene_set,
        metacell=_as_bool("metacell", raw_params["metacell"]),
        knn=_as_int("knn", raw_params["knn"], min_value=1),
        metacell_count=_as_int(
            "metacell_count", raw_params["metacell_count"], min_value=1
        ),
        ensemble=_as_int("ensemble", raw_params["ensemble"], min_value=1),
        diffusion_timesteps=diffusion_timesteps,
        sampling_timesteps=sampling_timesteps,
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
        raise ValueError("Planet supports only execution.mode=global or group_emulated.")
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
            "expression.tsv contains negative values. Planet's selected public "
            "CSV path applies non-negative expression preprocessing."
        )

    expression = pd.DataFrame(values, index=gene_ids, columns=cell_ids)
    return ExpressionInput(values=expression, gene_ids=gene_ids, cell_ids=cell_ids)


def _load_kegg(path: Path) -> dict[str, list[str]]:
    with path.open("rb") as fh:
        data = pickle.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Planet KEGG resource is not a dictionary.")
    return {str(key): [str(value) for value in values] for key, values in data.items()}


def _count_reference_edges(selected_genes: list[str]) -> int:
    selected = set(selected_genes)
    human_network = pd.read_csv(REGNETWORK_PATH, sep="\t", header=None, dtype=str)
    mask = human_network.iloc[:, 0].isin(selected) & human_network.iloc[:, 2].isin(
        selected
    )
    return int(mask.sum())


def _load_checkpoint_metadata(path: Path) -> CheckpointMetadata:
    import torch

    checkpoint = torch.load(str(path), map_location="cpu")
    if not isinstance(checkpoint, dict):
        raise ValueError("Planet checkpoint is not a dictionary.")

    input_feature_count: int | None = None
    input_dims = checkpoint.get("input_dims")
    if isinstance(input_dims, dict):
        raw_x = input_dims.get("X")
        if isinstance(raw_x, int):
            input_feature_count = raw_x

    max_nodes: int | None = None
    raw_max_nodes = checkpoint.get("max_nodes")
    if isinstance(raw_max_nodes, int):
        max_nodes = raw_max_nodes

    edge_percent: float | None = None
    raw_edge_percent = checkpoint.get("edge_percent")
    if isinstance(raw_edge_percent, (int, float)):
        edge_percent = float(raw_edge_percent)

    return CheckpointMetadata(
        input_feature_count=input_feature_count,
        max_nodes=max_nodes,
        edge_percent=edge_percent,
    )


def _write_user_gene_set(path: Path, genes: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["gene"])
        for gene in genes:
            writer.writerow([gene])


def _write_upstream_expression(path: Path, expression: pd.DataFrame) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["gene", "__andrea_planet_drop__"] + list(expression.columns))
        for gene, row in expression.iterrows():
            writer.writerow([gene, 0.0] + [float(value) for value in row.tolist()])


def _prepare_inputs(
    *,
    expression: ExpressionInput,
    params: ResolvedParams,
    checkpoint: CheckpointMetadata,
    raw_dir: Path,
) -> PreparedInputs:
    kegg = _load_kegg(KEGG_PATH)
    expression_genes = set(expression.gene_ids)

    if params.gene_set == "all_expression_genes":
        selected_genes = list(expression.gene_ids)
    elif params.gene_set.startswith("hsa"):
        if params.gene_set not in kegg:
            raise ValueError(
                f"gene_set={params.gene_set!r} is not present in Planet's bundled "
                "human KEGG resource."
            )
        kegg_genes = set(kegg[params.gene_set])
        selected_genes = [gene for gene in expression.gene_ids if gene in kegg_genes]
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

    validation_errors: list[str] = []
    selected_max_genes = checkpoint.max_nodes if checkpoint.max_nodes is not None else 200
    if len(selected_genes) < 10 or len(selected_genes) > selected_max_genes:
        validation_errors.append(
            "Planet's selected pretrained CSV path requires 10 to "
            f"{selected_max_genes} selected genes; "
            f"gene_set={params.gene_set!r} retained {len(selected_genes)} expression genes."
        )

    if params.metacell and params.knn > len(expression.cell_ids):
        validation_errors.append(
            f"knn={params.knn} must be <= number of input cells "
            f"({len(expression.cell_ids)}) when metacell=true."
        )

    effective_feature_count = len(expression.cell_ids)
    if params.metacell and len(expression.cell_ids) > params.metacell_count:
        effective_feature_count = params.metacell_count

    if (
        checkpoint.input_feature_count is not None
        and effective_feature_count != checkpoint.input_feature_count
    ):
        validation_errors.append(
            "The selected Planet checkpoint requires "
            f"{checkpoint.input_feature_count} expression features after metacell "
            f"preprocessing, but this run would produce {effective_feature_count}. "
            f"Use exactly {checkpoint.input_feature_count} cells with metacell=false, "
            "or configure metacell=true with metacell_count="
            f"{checkpoint.input_feature_count} and more than "
            f"{checkpoint.input_feature_count} input cells."
        )

    n_reference_edges = _count_reference_edges(selected_genes)
    if n_reference_edges <= 0:
        if params.gene_set == "all_expression_genes":
            validation_errors.append(
                "all_expression_genes retained "
                f"{len(selected_genes)} expression genes, but none form a bundled "
                "human RegNetwork source-target edge. This option creates a "
                "temporary gene-list CSV; it does not bypass Planet's requirement "
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
            "Planet input is incompatible with the selected pretrained CSV model: "
            + " ".join(validation_errors)
        )

    gene_set_path = raw_dir / "andrea_selected_gene_set.csv"
    _write_user_gene_set(gene_set_path, selected_genes)
    upstream_expression = raw_dir / "upstream_expression.csv"
    _write_upstream_expression(upstream_expression, expression.values)

    return PreparedInputs(
        upstream_expression=upstream_expression,
        upstream_gene_set=str(gene_set_path),
        selected_genes=selected_genes,
        effective_feature_count=effective_feature_count,
        checkpoint_input_feature_count=checkpoint.input_feature_count,
        checkpoint_max_nodes=checkpoint.max_nodes,
        n_reference_edges=n_reference_edges,
    )


def _ensure_writable_upstream_work(work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)

    pathway_link = work_dir / "pathway"
    if not pathway_link.exists() and not pathway_link.is_symlink():
        try:
            pathway_link.symlink_to(PLANET_HOME / "pathway", target_is_directory=True)
        except OSError:
            shutil.copytree(PLANET_HOME / "pathway", pathway_link, dirs_exist_ok=True)

    # make_final_net.cal_final_net() calls cal_identify_TF_gene() without passing
    # args.TF_file, so the upstream default path is GRN/mouse_TF.txt. In the
    # pinned Planet repo that file is corrupt HTML; use the valid bundled human
    # TF table under that relative path inside the per-run work directory.
    grn_dir = work_dir / "GRN"
    grn_dir.mkdir(exist_ok=True)
    human_tf_as_default = grn_dir / "mouse_TF.txt"
    if not human_tf_as_default.exists():
        shutil.copy2(TF_PATH, human_tf_as_default)


def _write_selected_genes(path: Path, genes: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["gene"])
        for gene in genes:
            writer.writerow([gene])


def _write_planet_config(
    path: Path,
    *,
    params: ResolvedParams,
    prepared: PreparedInputs,
    checkpoint: CheckpointMetadata,
    mode: str,
    threads: int,
    n_job: int,
) -> None:
    payload = {
        "upstream_repo": "https://github.com/wangchuanyuan1/project-Planet",
        "upstream_ref": PLANET_REF,
        "entrypoint": "Config() -> Planet(args) -> load_test_data() -> Planet.test()",
        "execution_mode": mode,
        "model_path": str(MODEL_PATH),
        "tf_path": str(TF_PATH),
        "kegg_path": str(KEGG_PATH),
        "regnetwork_path": str(REGNETWORK_PATH),
        "gene_set": params.gene_set,
        "upstream_gene_set": prepared.upstream_gene_set,
        "selected_gene_count": len(prepared.selected_genes),
        "reference_edge_count": prepared.n_reference_edges,
        "metacell": params.metacell,
        "knn": params.knn,
        "metacell_count": params.metacell_count,
        "effective_feature_count": prepared.effective_feature_count,
        "checkpoint_input_feature_count": checkpoint.input_feature_count,
        "checkpoint_max_nodes": checkpoint.max_nodes,
        "checkpoint_edge_percent": checkpoint.edge_percent,
        "ensemble": params.ensemble,
        "diffusion_timesteps": params.diffusion_timesteps,
        "sampling_timesteps": params.sampling_timesteps,
        "requested_threads": threads,
        "upstream_n_job": n_job,
        "device_policy": "cpu_only",
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def _configure_torch_threads() -> None:
    global _TORCH_THREADS_CONFIGURED
    if _TORCH_THREADS_CONFIGURED:
        return

    import torch

    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    _TORCH_THREADS_CONFIGURED = True


def _run_planet(
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
    if str(PLANET_HOME) not in sys.path:
        sys.path.insert(0, str(PLANET_HOME))

    with log_path.open("a", encoding="utf-8") as log_fh:
        log_fh.write("Starting Planet public testing flow.\n")
        log_fh.write(f"PLANET_HOME={PLANET_HOME}\n")
        log_fh.write(f"MODEL_PATH={MODEL_PATH}\n")
        log_fh.flush()
        with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
            os.chdir(work_dir)
            try:
                _configure_torch_threads()
                import torch
                from config import Config
                from Planet import Planet

                args = Config()
                args.TF_file = str(TF_PATH)
                args.kegg_file = str(KEGG_PATH)
                args.reg_file = str(REGNETWORK_PATH)
                args.save_dir = str(raw_dir)
                args.save_label = "andrea_planet"
                args.test_pathway = prepared.upstream_gene_set
                args.diffusion_timesteps = params.diffusion_timesteps
                args.sampling_timesteps = params.sampling_timesteps
                args.metacell = params.metacell
                args.KNN = params.knn
                args.Cnum = params.metacell_count
                args.use_pca = "false"
                args.ensemble = params.ensemble
                args.max_nodes = None
                args.show = False
                args.n_job = n_job
                args.Adddatabse = False
                args.net_key_par = {
                    "Flag_reg": False,
                    "Flag_llm": False,
                    "LLM_metric": "cos",
                }

                trainer = Planet(args)
                diffusion_pre = torch.load(str(MODEL_PATH), map_location=trainer.device)
                testdata, truelabel = trainer.load_test_data(
                    str(prepared.upstream_expression),
                    num=0,
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
        required_paths=[MODEL_PATH, KEGG_PATH, REGNETWORK_PATH, TF_PATH],
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = args.output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.output_dir / "planet.log"
    progress_path = args.output_dir / "progress.json"

    write_progress(
        progress_path,
        status="running",
        percent=0,
        phase="init",
        message="Initializing Planet",
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
            percent=20,
            phase="checkpoint",
            message="Inspecting Planet checkpoint metadata",
        )
        _configure_torch_threads()
        checkpoint = _load_checkpoint_metadata(MODEL_PATH)

        write_progress(
            progress_path,
            status="running",
            percent=30,
            phase="prepare",
            message="Preparing Planet CSV and gene set",
        )
        prepared = _prepare_inputs(
            expression=expression,
            params=params,
            checkpoint=checkpoint,
            raw_dir=raw_dir,
        )
        _write_selected_genes(raw_dir / "selected_genes.tsv", prepared.selected_genes)
        n_job = max(1, min(args.threads, params.ensemble))
        _write_planet_config(
            raw_dir / "planet_config.json",
            params=params,
            prepared=prepared,
            checkpoint=checkpoint,
            mode=mode,
            threads=args.threads,
            n_job=n_job,
        )

        write_progress(
            progress_path,
            status="running",
            percent=45,
            phase="inference",
            message="Running Planet diffusion generation",
            completed=0,
            total=params.ensemble,
        )
        adj_final = _run_planet(
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
            raise RuntimeError("Planet produced no positive non-self-loop edges.")
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
