#!/usr/bin/env python3
"""ANDREA wrapper for BoolODE."""

from __future__ import annotations

import argparse
import ast
import contextlib
import csv
import io
import json
import math
import os
import platform
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

for _var in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_var, "1")

import numpy as np
import pandas as pd
import yaml
from sklearn.cluster import KMeans as SklearnKMeans

BOOLODE_HOME = Path(os.environ.get("BOOLODE_HOME", "/opt/BoolODE"))
if BOOLODE_HOME.exists():
    sys.path.insert(0, str(BOOLODE_HOME))

import BoolODE as bo  # noqa: E402
from BoolODE import post_processing as boolode_post_processing  # noqa: E402


PINNED_COMMIT = "ba8884af40f98fc648b3f36f0b81a5a8cf22c9b9"
JOB_NAME = "andrea_boolode"

EXTRA_KEYS = [
    "groups",
    "column_phenotypes",
    "cluster_identities",
    "enrichment_background",
    "lineage_tree",
    "pseudotime",
    "prior_grn",
    "tf_list",
    "prior_grn_by_group",
]

GLOBAL_EXTRAS = {"pseudotime", "enrichment_background", "prior_grn", "tf_list"}
GROUP_EXTRAS = {
    "groups",
    "column_phenotypes",
    "cluster_identities",
    "enrichment_background",
    "pseudotime",
    "prior_grn",
    "tf_list",
    "prior_grn_by_group",
}

DEFAULT_PARAMS: dict[str, Any] = {
    "model_preset": "dyn_bifurcating",
    "model_type": "hill",
    "simulation_time": 8.0,
    "num_cells": 100,
    "n_clusters": 2,
    "sample_cells": False,
    "sample_parameters": False,
    "sample_std": 0.1,
    "identical_parameters": False,
    "integration_step_size": 0.01,
    "dropout": {
        "enabled": False,
        "drop_cutoff": 0.5,
        "drop_prob": 0.5,
    },
}

PRESETS: dict[str, dict[str, str | None]] = {
    "dyn_linear": {
        "model": "dyn-linear.txt",
        "initial_conditions": "dyn-linear_ics.txt",
        "interaction_strengths": "dyn-linear_strengths.txt",
    },
    "dyn_linear_long": {
        "model": "dyn-linear-long.txt",
        "initial_conditions": "dyn-linear-long_ics.txt",
        "interaction_strengths": "dyn-linear-long_strengths.txt",
    },
    "dyn_cycle": {
        "model": "dyn-cycle.txt",
        "initial_conditions": "dyn-cycle_ics.txt",
        "interaction_strengths": "dyn-cycle_strengths.txt",
    },
    "dyn_bifurcating": {
        "model": "dyn-bifurcating.txt",
        "initial_conditions": "dyn-bifurcating_ics.txt",
        "interaction_strengths": "dyn-bifurcating_strengths.txt",
    },
    "dyn_bifurcating_converging": {
        "model": "dyn-bifurcating-converging.txt",
        "initial_conditions": "dyn-bifurcating-converging_ics.txt",
        "interaction_strengths": "dyn-bifurcating-converging_strengths.txt",
    },
    "dyn_trifurcating": {
        "model": "dyn-trifurcating.txt",
        "initial_conditions": "dyn-trifurcating_ics.txt",
        "interaction_strengths": "dyn-trifurcating_strengths.txt",
    },
    "mCAD": {
        "model": "mCAD.txt",
        "initial_conditions": "mCAD_ics.txt",
        "interaction_strengths": None,
    },
    "VSC": {
        "model": "VSC.txt",
        "initial_conditions": None,
        "interaction_strengths": None,
    },
    "HSC": {
        "model": "HSC.txt",
        "initial_conditions": "HSC_ics.txt",
        "interaction_strengths": None,
    },
    "GSD": {
        "model": "GSD.txt",
        "initial_conditions": "GSD_ics.txt",
        "interaction_strengths": None,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_progress(
    output_dir: Path,
    status: str,
    phase: str,
    message: str | None = None,
    details: dict[str, Any] | None = None,
    percent: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "status": status,
        "phase": phase,
        "updated_at": utc_now(),
    }
    if message:
        payload["message"] = message
    if details:
        payload["details"] = details
    if percent is not None:
        payload["percent"] = max(0, min(100, int(percent)))
    write_json(output_dir / "progress.json", payload)


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = {key: deep_merge(value, {}) for key, value in base.items()}
        for key, value in override.items():
            merged[key] = deep_merge(merged[key], value) if key in merged else value
        return merged
    if isinstance(base, list):
        return list(base)
    return base if override == {} else override


def as_int(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, not boolean.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if number != value and not (isinstance(value, float) and number == value):
        raise ValueError(f"{name} must be an integer.")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be <= {maximum}.")
    return number


def as_float(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strict_min: bool = False,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, not boolean.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if minimum is not None:
        if strict_min and not number > minimum:
            raise ValueError(f"{name} must be > {minimum}.")
        if not strict_min and number < minimum:
            raise ValueError(f"{name} must be >= {minimum}.")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be <= {maximum}.")
    return number


def as_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean.")
    return value


def normalize_params(raw_params: dict[str, Any]) -> dict[str, Any]:
    params = deep_merge(DEFAULT_PARAMS, raw_params)
    if params["model_preset"] not in set(PRESETS).union({"custom_files"}):
        raise ValueError("model_preset is not a supported BoolODE preset.")
    if params["model_type"] not in {"hill", "heaviside"}:
        raise ValueError("model_type must be hill or heaviside.")
    params["simulation_time"] = as_float(params["simulation_time"], "simulation_time", minimum=0.0, strict_min=True)
    params["num_cells"] = as_int(params["num_cells"], "num_cells", minimum=1, maximum=5000)
    params["n_clusters"] = as_int(params["n_clusters"], "n_clusters", minimum=1)
    params["sample_cells"] = as_bool(params["sample_cells"], "sample_cells")
    params["sample_parameters"] = as_bool(params["sample_parameters"], "sample_parameters")
    params["sample_std"] = as_float(params["sample_std"], "sample_std", minimum=0.0)
    params["identical_parameters"] = as_bool(params["identical_parameters"], "identical_parameters")
    params["integration_step_size"] = as_float(
        params["integration_step_size"],
        "integration_step_size",
        minimum=0.0,
        strict_min=True,
    )
    if int(params["simulation_time"] / params["integration_step_size"]) < 3:
        raise ValueError("simulation_time / integration_step_size must produce at least three time points.")
    estimated_full_columns = params["num_cells"] * int(params["simulation_time"] / params["integration_step_size"])
    if (
        not params["sample_cells"]
        and estimated_full_columns >= 1000
        and not float(params["simulation_time"]).is_integer()
    ):
        raise ValueError(
            "The pinned BoolODE implementation requires simulation_time to be an integer "
            "when full trajectory output has at least 1000 columns; use an integer "
            "simulation_time or increase integration_step_size/reduce num_cells."
        )
    dropout = params["dropout"]
    if not isinstance(dropout, dict):
        raise ValueError("dropout must be an object.")
    dropout["enabled"] = as_bool(dropout["enabled"], "dropout.enabled")
    dropout["drop_cutoff"] = as_float(dropout["drop_cutoff"], "dropout.drop_cutoff", minimum=0.0, maximum=1.0)
    dropout["drop_prob"] = as_float(dropout["drop_prob"], "dropout.drop_prob", minimum=0.0, maximum=1.0)
    return params


def truth_contexts(request: dict[str, Any]) -> set[str]:
    raw = request.get("truth_requirements", {}).get("contexts", [])
    if not isinstance(raw, list):
        raise ValueError("truth_requirements.contexts must be an array.")
    contexts = {str(item) for item in raw}
    if "global" not in contexts:
        raise ValueError("truth_requirements.contexts must include global.")
    unsupported = sorted(contexts.difference({"global", "group"}))
    if unsupported:
        raise ValueError(
            "BoolODE wrapper supports only global and group truth contexts; unsupported: "
            + ", ".join(unsupported)
        )
    return contexts


def validate_semantic_request(request: dict[str, Any], extras: set[str], params: dict[str, Any]) -> None:
    axes = request.get("data_axes", {})
    if axes != {
        "measurement": "rna_expression",
        "resolution": "single_cell",
        "column_kind": "cells",
        "experimental_design": "trajectory",
    }:
        raise ValueError("BoolODE wrapper supports only single-cell RNA trajectory data_axes.")
    contexts = truth_contexts(request)
    supported = GROUP_EXTRAS if "group" in contexts else GLOBAL_EXTRAS
    unsupported = sorted(extras.difference(supported))
    if unsupported:
        raise ValueError(f"BoolODE does not support requested extra(s): {', '.join(unsupported)}")
    if "group" in contexts and params["n_clusters"] < 2:
        raise ValueError("group truth requires n_clusters >= 2.")
    if params["sample_cells"] and params["n_clusters"] > 1:
        raise ValueError("sample_cells=true is incompatible with n_clusters > 1 in the pinned BoolODE implementation.")


def read_boolean_model(path: Path) -> set[str]:
    try:
        df = pd.read_csv(path, sep="\t", dtype=str)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Failed to read BoolODE Boolean model: {path}") from exc
    required = {"Gene", "Rule"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"BoolODE Boolean model is missing column(s): {', '.join(sorted(missing))}")
    genes = {str(gene).strip() for gene in df["Gene"] if str(gene).strip()}
    if not genes:
        raise ValueError("BoolODE Boolean model contains no genes.")
    if len(genes) != len(df["Gene"]):
        raise ValueError("BoolODE Boolean model contains duplicate or empty Gene values.")
    return genes


def validate_initial_conditions(path: Path, genes: set[str]) -> None:
    df = pd.read_csv(path, sep="\t", dtype=str)
    if {"Genes", "Values"}.difference(df.columns):
        raise ValueError("BoolODE initial conditions must contain Genes and Values columns.")
    for line_no, row in enumerate(df.to_dict(orient="records"), start=2):
        try:
            row_genes = ast.literal_eval(str(row["Genes"]))
            values = ast.literal_eval(str(row["Values"]))
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"Initial conditions line {line_no} must contain Python-literal lists.") from exc
        if not isinstance(row_genes, list) or not isinstance(values, list) or len(row_genes) != len(values):
            raise ValueError(f"Initial conditions line {line_no} has mismatched Genes and Values lists.")
        unknown = sorted({str(gene) for gene in row_genes}.difference(genes))
        if unknown:
            raise ValueError(f"Initial conditions reference unknown gene(s): {', '.join(unknown[:8])}")
        for value in values:
            as_float(value, f"initial_conditions line {line_no}")


def validate_interaction_strengths(path: Path, genes: set[str]) -> None:
    df = pd.read_csv(path, sep="\t", dtype=str)
    if {"Gene1", "Gene2", "Strength"}.difference(df.columns):
        raise ValueError("BoolODE interaction strengths must contain Gene1, Gene2 and Strength columns.")
    for line_no, row in enumerate(df.to_dict(orient="records"), start=2):
        target = str(row["Gene1"]).strip()
        regulator = str(row["Gene2"]).strip()
        unknown = sorted({target, regulator}.difference(genes))
        if unknown:
            raise ValueError(f"Interaction strengths line {line_no} references unknown gene(s): {', '.join(unknown[:8])}")
        as_float(row["Strength"], f"interaction_strengths line {line_no}", minimum=0.0, strict_min=True)


def copy_input(src: Path, dest_dir: Path, dest_name: str) -> Path:
    if not src.exists() or not src.is_file():
        raise ValueError(f"BoolODE input file does not exist: {src}")
    dest = dest_dir / dest_name
    shutil.copy2(src, dest)
    return dest


def stage_inputs(
    *,
    params: dict[str, Any],
    mounted_inputs: dict[str, str],
    model_dir: Path,
    raw_dir: Path,
) -> dict[str, Path | None]:
    model_dir.mkdir(parents=True, exist_ok=True)
    data_dir = BOOLODE_HOME / "data"
    preset = params["model_preset"]

    if preset == "custom_files":
        if "boolode_boolean_model" not in mounted_inputs:
            raise ValueError("boolode_boolean_model is required when model_preset=custom_files.")
        model_path = copy_input(Path(mounted_inputs["boolode_boolean_model"]), model_dir, "model_definition.tsv")
        initial_path = (
            copy_input(Path(mounted_inputs["boolode_initial_conditions"]), model_dir, "initial_conditions.tsv")
            if "boolode_initial_conditions" in mounted_inputs
            else None
        )
        strengths_path = (
            copy_input(Path(mounted_inputs["boolode_interaction_strengths"]), model_dir, "interaction_strengths.tsv")
            if "boolode_interaction_strengths" in mounted_inputs
            else None
        )
    else:
        preset_info = PRESETS[preset]
        model_path = copy_input(data_dir / str(preset_info["model"]), model_dir, str(preset_info["model"]))
        initial_source = (
            Path(mounted_inputs["boolode_initial_conditions"])
            if "boolode_initial_conditions" in mounted_inputs
            else (data_dir / str(preset_info["initial_conditions"]) if preset_info["initial_conditions"] else None)
        )
        strengths_source = (
            Path(mounted_inputs["boolode_interaction_strengths"])
            if "boolode_interaction_strengths" in mounted_inputs
            else (data_dir / str(preset_info["interaction_strengths"]) if preset_info["interaction_strengths"] else None)
        )
        initial_path = (
            copy_input(initial_source, model_dir, Path(initial_source).name)
            if initial_source is not None and Path(initial_source).exists()
            else None
        )
        strengths_path = (
            copy_input(strengths_source, model_dir, Path(strengths_source).name)
            if strengths_source is not None and Path(strengths_source).exists()
            else None
        )

    genes = read_boolean_model(model_path)
    if initial_path is not None:
        validate_initial_conditions(initial_path, genes)
    if strengths_path is not None:
        validate_interaction_strengths(strengths_path, genes)

    input_summary = {
        "model_definition": model_path.relative_to(raw_dir).as_posix(),
        "model_initial_conditions": initial_path.relative_to(raw_dir).as_posix() if initial_path else None,
        "interaction_strengths": strengths_path.relative_to(raw_dir).as_posix() if strengths_path else None,
    }
    write_json(raw_dir / "input_files.json", input_summary)
    return {
        "model_definition": model_path,
        "model_initial_conditions": initial_path,
        "interaction_strengths": strengths_path,
    }


def build_config(params: dict[str, Any], model_dir: Path, boolode_output_dir: Path, inputs: dict[str, Path | None]) -> dict[str, Any]:
    simulation_time: int | float = params["simulation_time"]
    if float(simulation_time).is_integer():
        simulation_time = int(simulation_time)
    job: dict[str, Any] = {
        "name": JOB_NAME,
        "model_definition": Path(inputs["model_definition"]).name,
        "simulation_time": simulation_time,
        "num_cells": params["num_cells"],
        "nClusters": params["n_clusters"],
        "do_parallel": False,
        "sample_cells": params["sample_cells"],
        "sample_pars": params["sample_parameters"],
        "sample_std": params["sample_std"],
        "identical_pars": params["identical_parameters"],
        "integration_step_size": params["integration_step_size"],
    }
    if inputs.get("model_initial_conditions") is not None:
        job["model_initial_conditions"] = Path(inputs["model_initial_conditions"]).name
    if inputs.get("interaction_strengths") is not None:
        job["interaction_strengths"] = Path(inputs["interaction_strengths"]).name
    return {
        "global_settings": {
            "model_dir": str(model_dir),
            "output_dir": str(boolode_output_dir),
            "do_simulations": True,
            "do_post_processing": False,
            "modeltype": params["model_type"],
        },
        "jobs": [job],
        "post_processing": {},
    }


def patch_boolode_resource_use() -> None:
    def bounded_kmeans(*args: Any, **kwargs: Any) -> SklearnKMeans:
        kwargs["n_jobs"] = 1
        return SklearnKMeans(*args, **kwargs)

    if hasattr(bo, "runexp"):
        bo.runexp.KMeans = bounded_kmeans


def run_boolode(config_path: Path) -> None:
    patch_boolode_resource_use()
    with config_path.open("r", encoding="utf-8") as handle:
        jobs = bo.ConfigParser.parse(handle)
    jobs.execute_jobs()


def run_dropout_if_requested(params: dict[str, Any], upstream_dir: Path, raw_dir: Path) -> Path:
    if not params["dropout"]["enabled"]:
        return upstream_dir
    out_prefix = raw_dir / "dropout" / "dropout"
    opts = {
        "dropout": True,
        "drop_cutoff": params["dropout"]["drop_cutoff"],
        "drop_prob": params["dropout"]["drop_prob"],
        "expr": upstream_dir / "ExpressionData.csv",
        "pseudo": upstream_dir / "PseudoTime.csv",
        "refNet": upstream_dir / "refNetwork.csv",
        "outPrefix": str(out_prefix),
    }
    boolode_post_processing.genDropouts(opts)
    return Path(f"{out_prefix}-{int(100 * params['dropout']['drop_cutoff'])}-{params['dropout']['drop_prob']}")


def load_expression(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise ValueError(f"Missing BoolODE expression file: {path}")
    df = pd.read_csv(path, index_col=0)
    df.index = [str(value).strip() for value in df.index]
    df.columns = [str(value).strip() for value in df.columns]
    if df.empty:
        raise ValueError("BoolODE ExpressionData.csv is empty.")
    if len(set(df.index)) != len(df.index):
        raise ValueError("BoolODE ExpressionData.csv has duplicate gene IDs.")
    if len(set(df.columns)) != len(df.columns):
        raise ValueError("BoolODE ExpressionData.csv has duplicate cell IDs.")
    numeric = df.apply(pd.to_numeric, errors="raise")
    if not np.all(np.isfinite(numeric.values)):
        raise ValueError("BoolODE expression matrix contains non-finite values.")
    return numeric


def parse_ref_network(path: Path, expression_genes: set[str]) -> list[dict[str, Any]]:
    df = pd.read_csv(path, dtype=str)
    required = {"Gene1", "Gene2", "Type"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"refNetwork.csv is missing column(s): {', '.join(sorted(missing))}")
    rows: list[dict[str, Any]] = []
    for line_no, row in enumerate(df.to_dict(orient="records"), start=2):
        source = str(row["Gene1"]).strip()
        target = str(row["Gene2"]).strip()
        sign = str(row["Type"]).strip()
        if not source or not target or source == target:
            continue
        if sign not in {"+", "-"}:
            raise ValueError(f"refNetwork.csv line {line_no} has unsupported Type value {sign!r}.")
        unknown = sorted({source, target}.difference(expression_genes))
        if unknown:
            raise ValueError(f"refNetwork.csv line {line_no} references genes absent from expression.tsv: {unknown}")
        rows.append(
            {
                "source": source,
                "target": target,
                "score": 1.0,
                "sign": sign,
                "evidence": "simulated_truth",
                "context": "global",
                "line_no": line_no,
            }
        )
    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        dedup[(row["source"], row["target"], row["sign"])] = row
    out = sorted(dedup.values(), key=lambda item: (item["source"], item["target"], item["sign"]))
    if not out:
        raise ValueError("BoolODE refNetwork.csv produced no non-self-loop public truth edges.")
    return out


def load_pseudotime(path: Path, expression_columns: list[str]) -> dict[str, dict[str, float]]:
    df = pd.read_csv(path)
    cell_column = "Cell ID" if "Cell ID" in df.columns else df.columns[0]
    if "PseudoTime" not in df.columns:
        pt_columns = [column for column in df.columns if str(column).startswith("PseudoTime")]
        if not pt_columns:
            raise ValueError("PseudoTime.csv has no PseudoTime column.")
        df["PseudoTime"] = df[pt_columns].apply(lambda row: next((value for value in row if pd.notna(value)), np.nan), axis=1)
    if "Time" not in df.columns:
        df["Time"] = df["PseudoTime"]
    result: dict[str, dict[str, float]] = {}
    for row in df.to_dict(orient="records"):
        cell = str(row[cell_column]).strip()
        if not cell:
            continue
        result[cell] = {
            "pseudotime": float(row["PseudoTime"]),
            "time": float(row["Time"]),
        }
    missing = sorted(set(expression_columns).difference(result))
    if missing:
        raise ValueError("PseudoTime.csv is missing expression cell(s): " + ", ".join(missing[:8]))
    return result


def load_groups(cluster_path: Path, expression_columns: list[str]) -> dict[str, str]:
    if not cluster_path.exists():
        raise ValueError("group truth requires BoolODE ClusterIds.csv, but it was not generated.")
    df = pd.read_csv(cluster_path, index_col=0, dtype=str)
    if "cl" not in df.columns:
        raise ValueError("ClusterIds.csv is missing column 'cl'.")
    cluster_by_experiment = {str(index).strip(): str(value).strip() for index, value in df["cl"].items()}
    groups: dict[str, str] = {}
    for cell in expression_columns:
        experiment = str(cell).split("_", 1)[0]
        if experiment not in cluster_by_experiment:
            raise ValueError(f"ClusterIds.csv has no row for expression cell experiment {experiment!r}.")
        groups[cell] = f"cluster_{cluster_by_experiment[experiment]}"
    return groups


def write_expression(path: Path, df: pd.DataFrame) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["gene"] + list(df.columns))
        for gene, row in df.iterrows():
            writer.writerow([gene] + [f"{float(value):.12g}" for value in row])


def write_gene_universe(path: Path, genes: list[str]) -> None:
    path.write_text("".join(f"{gene}\n" for gene in genes), encoding="utf-8")


def write_truth_networks(path: Path, global_edges: list[dict[str, Any]], groups: list[str] | None = None) -> None:
    rows = list(global_edges)
    if groups is not None:
        for group in groups:
            for edge in global_edges:
                copied = dict(edge)
                copied["context"] = f"group:{group}"
                rows.append(copied)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "target", "score", "sign", "evidence", "context"], lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "source": row["source"],
                    "target": row["target"],
                    "score": f"{float(row['score']):.12g}",
                    "sign": row["sign"],
                    "evidence": row["evidence"],
                    "context": row["context"],
                }
            )


def write_pseudotime(path: Path, expression_columns: list[str], pseudotime: dict[str, dict[str, float]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "pseudotime"])
        for cell in expression_columns:
            writer.writerow([cell, f"{float(pseudotime[cell]['pseudotime']):.12g}"])


def group_order(groups: dict[str, str], pseudotime: dict[str, dict[str, float]]) -> dict[str, int]:
    times: dict[str, list[float]] = {}
    for cell, group in groups.items():
        times.setdefault(group, []).append(float(pseudotime[cell]["time"]))
    ordered = sorted(times, key=lambda group: (sum(times[group]) / len(times[group]), group))
    return {group: index for index, group in enumerate(ordered)}


def write_groups(path: Path, groups: dict[str, str], expression_columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "cluster"])
        for cell in expression_columns:
            writer.writerow([cell, groups[cell]])


def write_column_phenotypes(path: Path, groups: dict[str, str], expression_columns: list[str], order: dict[str, int]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "phenotype", "order"])
        for cell in expression_columns:
            group = groups[cell]
            writer.writerow([cell, group, order[group]])


def write_cluster_identities(path: Path, order: dict[str, int]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["cluster", "annotation", "order"])
        for group, index in sorted(order.items(), key=lambda item: (item[1], item[0])):
            writer.writerow([group, group, index])


def write_prior_grn(path: Path, edges: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["source", "target", "score", "sign"])
        for edge in edges:
            writer.writerow([edge["source"], edge["target"], f"{float(edge['score']):.12g}", edge["sign"]])


def write_prior_grn_by_group(path: Path, edges: list[dict[str, Any]], groups: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["group", "source", "target", "score", "sign"])
        for group in groups:
            for edge in edges:
                writer.writerow([group, edge["source"], edge["target"], f"{float(edge['score']):.12g}", edge["sign"]])


def write_tf_list(path: Path, edges: list[dict[str, Any]]) -> None:
    regulators = sorted({str(edge["source"]) for edge in edges})
    path.write_text("".join(f"{gene}\n" for gene in regulators), encoding="utf-8")


def write_truth_source_edges(path: Path, edges: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["line_no", "source", "target", "score", "sign"])
        for edge in edges:
            writer.writerow([edge["line_no"], edge["source"], edge["target"], f"{float(edge['score']):.12g}", edge["sign"]])


def write_public_maps(raw_dir: Path, genes: list[str], columns: list[str], groups: dict[str, str] | None) -> None:
    with (raw_dir / "public_gene_id_map.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["native_gene_id", "public_gene_id"])
        for gene in genes:
            writer.writerow([gene, gene])
    with (raw_dir / "public_cell_id_map.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["native_cell_id", "public_cell_id", "public_group_id"])
        for cell in columns:
            writer.writerow([cell, cell, groups[cell] if groups is not None else ""])


def write_session_info(raw_dir: Path) -> None:
    try:
        import scipy
        import sklearn

        scipy_version = scipy.__version__
        sklearn_version = sklearn.__version__
    except Exception:  # noqa: BLE001
        scipy_version = "unknown"
        sklearn_version = "unknown"
    try:
        commit = os.popen(f"git -C {BOOLODE_HOME} rev-parse HEAD").read().strip() or "unknown"
    except Exception:  # noqa: BLE001
        commit = "unknown"
    lines = [
        f"python={platform.python_version()}",
        f"platform={platform.platform()}",
        f"numpy={np.__version__}",
        f"pandas={pd.__version__}",
        f"pyyaml={yaml.__version__}",
        f"scipy={scipy_version}",
        f"scikit_learn={sklearn_version}",
        f"boolode_home={BOOLODE_HOME}",
        f"boolode_commit={commit}",
        f"expected_boolode_commit={PINNED_COMMIT}",
    ]
    (raw_dir / "session_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def rel(path: Path, output_dir: Path) -> str:
    return path.relative_to(output_dir).as_posix()


def copy_native_output(
    *,
    source_path: Path,
    native_dir: Path,
    native_outputs: dict[str, str],
    native_id: str,
) -> None:
    if not source_path.exists():
        return
    native_dir.mkdir(parents=True, exist_ok=True)
    if source_path.is_dir():
        destination = native_dir / native_id
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source_path, destination)
    else:
        destination = native_dir / f"{native_id}{source_path.suffix}"
        shutil.copy2(source_path, destination)
    native_outputs[native_id] = rel(destination, native_dir.parent)


def write_manifest(
    output_dir: Path,
    request: dict[str, Any],
    expression: pd.DataFrame,
    extras_paths: dict[str, str | None],
    native_outputs: dict[str, str],
) -> None:
    manifest = {
        "schema_version": "1.0",
        "simulator_id": "boolode",
        "data_axes": request["data_axes"],
        "truth_requirements": request["truth_requirements"],
        "seed": int(request["seed"]),
        "expression": {
            "path": "expression.tsv",
            "genes": int(expression.shape[0]),
            "columns": int(expression.shape[1]),
            "column_kind": "cells",
        },
        "extras": {key: extras_paths.get(key) for key in EXTRA_KEYS},
        "native_outputs": native_outputs,
        "truth": {
            "gene_universe": "truth/gene_universe.txt",
            "networks": "truth/networks.csv",
        },
        "provenance": {
            "raw_dir": "provenance/raw",
            "notes": "BoolODE run from the public GitHub repository API pinned to ba8884af40f98fc648b3f36f0b81a5a8cf22c9b9; normalized truth and extras are derived from native BoolODE outputs.",
        },
    }
    write_json(output_dir / "simulator-output-manifest.json", manifest)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BoolODE and emit an ANDREA normalized simulator package.")
    parser.add_argument("--request", type=Path, default=Path("/work/request/simulator-run-request.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("/work/out"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "extras").mkdir(parents=True, exist_ok=True)
    (output_dir / "truth").mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "provenance" / "raw"
    native_dir = output_dir / "native"
    raw_dir.mkdir(parents=True, exist_ok=True)

    try:
        write_progress(output_dir, "running", "validate_request", "Reading simulator-run-request.json.", percent=10)
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if request.get("simulator_id") != "boolode":
            raise ValueError("simulator-run-request.json must have simulator_id='boolode'.")
        for field in ("data_axes", "truth_requirements", "seed", "effective_extras", "params", "runtime_resources"):
            if field not in request:
                raise ValueError(f"simulator-run-request.json is missing required field {field!r}.")
        write_json(raw_dir / "request.json", request)

        if as_int(request.get("runtime_resources", {}).get("threads", 1), "runtime_resources.threads", minimum=1) != 1:
            raise ValueError("BoolODE exposes no bounded public thread control; runtime_resources.threads must be 1.")

        params = normalize_params(dict(request.get("params", {})))
        extras = {str(item) for item in request.get("effective_extras", [])}
        requested_native_outputs = {str(item) for item in request.get("native_outputs", [])}
        validate_semantic_request(request, extras, params)
        write_json(raw_dir / "resolved_params.json", params)
        np.random.seed(as_int(request["seed"], "seed", minimum=1))

        write_progress(output_dir, "running", "prepare_run", "Staging BoolODE inputs and configuration.", percent=25)
        model_dir = raw_dir / "model_inputs"
        boolode_output_dir = raw_dir / "boolode_output"
        staged_inputs = stage_inputs(
            params=params,
            mounted_inputs={str(k): str(v) for k, v in request.get("mounted_inputs", {}).items()},
            model_dir=model_dir,
            raw_dir=raw_dir,
        )
        config = build_config(params, model_dir, boolode_output_dir, staged_inputs)
        config_path = raw_dir / "boolode_config.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

        write_progress(output_dir, "running", "run_simulator", "Running BoolODE public Python API.", percent=45)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            run_boolode(config_path)
        (raw_dir / "upstream_stdout.log").write_text(stdout.getvalue(), encoding="utf-8")
        (raw_dir / "upstream_stderr.log").write_text(stderr.getvalue(), encoding="utf-8")

        upstream_dir = boolode_output_dir / JOB_NAME
        if not upstream_dir.exists():
            raise ValueError(f"BoolODE did not create expected output directory: {upstream_dir}")
        selected_dir = run_dropout_if_requested(params, upstream_dir, raw_dir)

        native_candidates = {
            "expression_data": upstream_dir / "ExpressionData.csv",
            "pseudo_time": upstream_dir / "PseudoTime.csv",
            "reference_network": upstream_dir / "refNetwork.csv",
            "simulation_trajectories": upstream_dir / "simulations",
            "generated_model": upstream_dir / "model.py",
            "kinetic_parameters": upstream_dir / "parameters.txt",
        }
        if (upstream_dir / "ClusterIds.csv").exists():
            native_candidates["cluster_ids"] = upstream_dir / "ClusterIds.csv"
        if selected_dir != upstream_dir:
            native_candidates["dropout_expression"] = selected_dir / "ExpressionData.csv"
        native_outputs: dict[str, str] = {}
        for native_id in sorted(requested_native_outputs):
            source_path = native_candidates.get(native_id)
            if source_path is not None:
                copy_native_output(
                    source_path=source_path,
                    native_dir=native_dir,
                    native_outputs=native_outputs,
                    native_id=native_id,
                )

        write_progress(output_dir, "running", "package_outputs", "Normalizing expression, truth and extras.", percent=75)
        expression = load_expression(selected_dir / "ExpressionData.csv")
        genes = list(expression.index)
        columns = list(expression.columns)
        expression_gene_set = set(genes)
        pseudotime = load_pseudotime(selected_dir / "PseudoTime.csv", columns)
        global_edges = parse_ref_network(selected_dir / "refNetwork.csv", expression_gene_set)

        groups = None
        observed_groups = None
        if "group" in truth_contexts(request):
            groups = load_groups(upstream_dir / "ClusterIds.csv", columns)
            observed_groups = sorted(set(groups.values()))

        write_expression(output_dir / "expression.tsv", expression)
        expression.to_csv(raw_dir / "final_expression.tsv", sep="\t")
        write_gene_universe(output_dir / "truth" / "gene_universe.txt", genes)
        write_truth_networks(output_dir / "truth" / "networks.csv", global_edges, observed_groups)
        write_truth_source_edges(raw_dir / "truth_source_edges.tsv", global_edges)
        write_public_maps(raw_dir, genes, columns, groups)

        extras_paths: dict[str, str | None] = {key: None for key in EXTRA_KEYS}
        if "pseudotime" in extras:
            write_pseudotime(output_dir / "extras" / "pseudotime.tsv", columns, pseudotime)
            extras_paths["pseudotime"] = "extras/pseudotime.tsv"
        if groups is not None:
            write_groups(output_dir / "extras" / "groups.tsv", groups, columns)
            extras_paths["groups"] = "extras/groups.tsv"
            order = group_order(groups, pseudotime)
        else:
            order = {}
        if "column_phenotypes" in extras:
            if groups is None:
                raise ValueError("column_phenotypes requires grouped BoolODE output.")
            write_column_phenotypes(output_dir / "extras" / "column_phenotypes.tsv", groups, columns, order)
            extras_paths["column_phenotypes"] = "extras/column_phenotypes.tsv"
        if "cluster_identities" in extras:
            if groups is None:
                raise ValueError("cluster_identities requires grouped BoolODE output.")
            write_cluster_identities(output_dir / "extras" / "cluster_identities.tsv", order)
            extras_paths["cluster_identities"] = "extras/cluster_identities.tsv"
        if "enrichment_background" in extras:
            write_gene_universe(output_dir / "extras" / "enrichment_background.txt", genes)
            extras_paths["enrichment_background"] = "extras/enrichment_background.txt"
        if "prior_grn" in extras:
            write_prior_grn(output_dir / "extras" / "prior_grn.tsv", global_edges)
            extras_paths["prior_grn"] = "extras/prior_grn.tsv"
        if "tf_list" in extras:
            write_tf_list(output_dir / "extras" / "tf_list.txt", global_edges)
            extras_paths["tf_list"] = "extras/tf_list.txt"
        if "prior_grn_by_group" in extras:
            if observed_groups is None:
                raise ValueError("prior_grn_by_group requires grouped BoolODE output.")
            write_prior_grn_by_group(output_dir / "extras" / "prior_grn_by_group.tsv", global_edges, observed_groups)
            extras_paths["prior_grn_by_group"] = "extras/prior_grn_by_group.tsv"

        write_session_info(raw_dir)
        write_progress(output_dir, "running", "write_manifest", "Writing simulator-output-manifest.json.", percent=95)
        write_manifest(output_dir, request, expression, extras_paths, native_outputs)
        write_progress(output_dir, "completed", "done", "BoolODE simulation package completed.", percent=100)
        return 0
    except BaseException as exc:  # noqa: BLE001
        (raw_dir / "wrapper_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        try:
            write_progress(
                output_dir,
                "failed",
                "failed",
                str(exc),
                {"error_type": exc.__class__.__name__},
                percent=100,
            )
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
