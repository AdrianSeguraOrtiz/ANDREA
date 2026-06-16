#!/usr/bin/env python3
"""ANDREA wrapper for SERGIO."""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if "int" not in np.__dict__:
    np.int = int  # type: ignore[attr-defined]
if "float" not in np.__dict__:
    np.float = float  # type: ignore[attr-defined]

import networkx as nx
import scipy

SERGIO_HOME = Path(os.environ.get("SERGIO_HOME", "/opt/SERGIO"))
if SERGIO_HOME.exists():
    sys.path.insert(0, str(SERGIO_HOME))

from SERGIO.sergio import sergio as Sergio  # noqa: E402


DEFAULT_PARAMS: dict[str, Any] = {
    "input_preset": "demo_steady_state",
    "simulation_mode": "steady_state",
    "number_genes": 100,
    "number_bins": 9,
    "number_sc": 300,
    "noise_params": 1.0,
    "noise_type": "dpd",
    "decays": 0.8,
    "sampling_state": 15,
    "tol": 0.001,
    "window_length": 100,
    "dt": 0.01,
    "optimize_sampling": False,
    "shared_coop_state": 2.0,
    "noise_params_splice": None,
    "noise_type_splice": None,
    "splice_ratio": 4.0,
    "dt_splice": 0.01,
    "differentiation_expression": "total",
    "technical_noise": {
        "outlier_enabled": False,
        "outlier_prob": 0.01,
        "outlier_mean": 0.8,
        "outlier_scale": 1.0,
        "library_size_enabled": False,
        "library_size_mean": 4.6,
        "library_size_scale": 0.4,
        "dropout_enabled": False,
        "dropout_shape": 6.5,
        "dropout_percentile": 82.0,
        "convert_to_umi": False,
    },
}

EXTRA_KEYS = [
    "groups",
    "cell_phenotypes",
    "cluster_identities",
    "enrichment_background",
    "lineage_tree",
    "pseudotime",
    "prior_grn",
    "tf_list",
    "prior_grn_by_group",
]


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
    if percent is not None:
        payload["percent"] = max(0, min(100, int(percent)))
    if message:
        payload["message"] = message
    if details:
        payload["details"] = details
    write_json(output_dir / "progress.json", payload)


def progress_heartbeat(output_dir: Path, stop_event: threading.Event, started_at: float) -> None:
    while not stop_event.wait(10.0):
        elapsed_seconds = int(time.monotonic() - started_at)
        percent = min(70, 45 + elapsed_seconds // 30)
        try:
            write_progress(
                output_dir,
                "running",
                "run_simulator",
                "SERGIO simulation is still running; upstream API has no finer progress callback.",
                {
                    "elapsed_seconds": elapsed_seconds,
                    "progress_source": "wrapper_heartbeat",
                },
                percent=percent,
            )
        except Exception:  # noqa: BLE001
            return


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = {key: deep_merge(value, {}) for key, value in base.items()}
        for key, value in override.items():
            merged[key] = deep_merge(merged[key], value) if key in merged else value
        return merged
    if isinstance(base, list):
        return list(base)
    return base if override == {} else override


def as_int(value: Any, name: str, *, minimum: int | None = None) -> int:
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
    return number


def as_float(value: Any, name: str, *, minimum: float | None = None, strict_min: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, not boolean.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not np.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if minimum is not None:
        if strict_min and not number > minimum:
            raise ValueError(f"{name} must be > {minimum}.")
        if not strict_min and number < minimum:
            raise ValueError(f"{name} must be >= {minimum}.")
    return number


def as_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean.")
    return value


def as_scalar_or_array(
    value: Any,
    name: str,
    number_genes: int,
    *,
    minimum: float | None = None,
    strict_min: bool = False,
    allow_none: bool = False,
) -> float | list[float] | None:
    if value is None and allow_none:
        return None
    if isinstance(value, list):
        if len(value) != number_genes:
            raise ValueError(f"{name} must have length number_genes ({number_genes}) when provided as an array.")
        return [
            as_float(item, f"{name}[{idx}]", minimum=minimum, strict_min=strict_min)
            for idx, item in enumerate(value)
        ]
    return as_float(value, name, minimum=minimum, strict_min=strict_min)


def normalize_params(raw_params: dict[str, Any]) -> dict[str, Any]:
    params = deep_merge(DEFAULT_PARAMS, raw_params)
    number_genes = as_int(params["number_genes"], "number_genes", minimum=2)
    params["number_genes"] = number_genes
    params["number_bins"] = as_int(params["number_bins"], "number_bins", minimum=1)
    params["number_sc"] = as_int(params["number_sc"], "number_sc", minimum=1)
    params["sampling_state"] = as_int(params["sampling_state"], "sampling_state", minimum=1)
    params["window_length"] = as_int(params["window_length"], "window_length", minimum=1)
    params["noise_params"] = as_scalar_or_array(params["noise_params"], "noise_params", number_genes, minimum=0.0)
    params["decays"] = as_scalar_or_array(params["decays"], "decays", number_genes, minimum=0.0, strict_min=True)
    params["tol"] = as_float(params["tol"], "tol", minimum=0.0, strict_min=True)
    params["dt"] = as_float(params["dt"], "dt", minimum=0.0, strict_min=True)
    params["dt_splice"] = as_float(params["dt_splice"], "dt_splice", minimum=0.0, strict_min=True)
    params["shared_coop_state"] = as_float(params["shared_coop_state"], "shared_coop_state")
    params["optimize_sampling"] = as_bool(params["optimize_sampling"], "optimize_sampling")
    params["noise_params_splice"] = as_scalar_or_array(
        params["noise_params_splice"],
        "noise_params_splice",
        number_genes,
        minimum=0.0,
        allow_none=True,
    )
    params["splice_ratio"] = as_scalar_or_array(
        params["splice_ratio"],
        "splice_ratio",
        number_genes,
        minimum=0.0,
        strict_min=True,
    )
    if params["input_preset"] not in {"demo_steady_state", "demo_differentiation", "custom_files"}:
        raise ValueError("input_preset must be demo_steady_state, demo_differentiation or custom_files.")
    if params["simulation_mode"] not in {"steady_state", "differentiation"}:
        raise ValueError("simulation_mode must be steady_state or differentiation.")
    if params["noise_type"] not in {"dpd", "sp", "spd"}:
        raise ValueError("noise_type must be dpd, sp or spd.")
    if params["noise_type_splice"] is not None and params["noise_type_splice"] not in {"dpd", "sp", "spd"}:
        raise ValueError("noise_type_splice must be null, dpd, sp or spd.")
    if params["differentiation_expression"] not in {"total", "spliced", "unspliced"}:
        raise ValueError("differentiation_expression must be total, spliced or unspliced.")

    noise = params["technical_noise"]
    if not isinstance(noise, dict):
        raise ValueError("technical_noise must be an object.")
    noise["outlier_enabled"] = as_bool(noise["outlier_enabled"], "technical_noise.outlier_enabled")
    noise["outlier_prob"] = as_float(noise["outlier_prob"], "technical_noise.outlier_prob", minimum=0.0)
    if noise["outlier_prob"] > 1.0:
        raise ValueError("technical_noise.outlier_prob must be <= 1.")
    noise["outlier_mean"] = as_float(noise["outlier_mean"], "technical_noise.outlier_mean")
    noise["outlier_scale"] = as_float(noise["outlier_scale"], "technical_noise.outlier_scale", minimum=0.0)
    noise["library_size_enabled"] = as_bool(noise["library_size_enabled"], "technical_noise.library_size_enabled")
    noise["library_size_mean"] = as_float(noise["library_size_mean"], "technical_noise.library_size_mean")
    noise["library_size_scale"] = as_float(noise["library_size_scale"], "technical_noise.library_size_scale", minimum=0.0)
    noise["dropout_enabled"] = as_bool(noise["dropout_enabled"], "technical_noise.dropout_enabled")
    noise["dropout_shape"] = as_float(noise["dropout_shape"], "technical_noise.dropout_shape")
    noise["dropout_percentile"] = as_float(noise["dropout_percentile"], "technical_noise.dropout_percentile", minimum=0.0)
    if noise["dropout_percentile"] > 100.0:
        raise ValueError("technical_noise.dropout_percentile must be <= 100.")
    noise["convert_to_umi"] = as_bool(noise["convert_to_umi"], "technical_noise.convert_to_umi")
    return params


def validate_profile_and_params(profile: str, extras: set[str], params: dict[str, Any]) -> None:
    if profile not in {"scrna_global", "scrna_grouped"}:
        raise ValueError("SERGIO supports only scrna_global and scrna_grouped in this wrapper.")
    if profile == "scrna_grouped" and params["number_bins"] < 2:
        raise ValueError("scrna_grouped requires number_bins >= 2.")
    if params["input_preset"] == "demo_steady_state":
        if params["simulation_mode"] != "steady_state":
            raise ValueError("input_preset=demo_steady_state must use simulation_mode=steady_state.")
        if params["number_genes"] != 100 or params["number_bins"] != 9:
            raise ValueError("input_preset=demo_steady_state requires number_genes=100 and number_bins=9.")
    if params["input_preset"] == "demo_differentiation":
        if params["simulation_mode"] != "differentiation":
            raise ValueError("input_preset=demo_differentiation must use simulation_mode=differentiation.")
        if params["number_genes"] != 100 or params["number_bins"] != 3:
            raise ValueError("input_preset=demo_differentiation requires number_genes=100 and number_bins=3.")
    if "lineage_tree" in extras and params["simulation_mode"] != "differentiation":
        raise ValueError("lineage_tree requires simulation_mode=differentiation.")
    if "pseudotime" in extras:
        raise ValueError("SERGIO does not claim the pseudotime extra.")


def resolve_inputs(params: dict[str, Any], mounted_inputs: dict[str, str]) -> dict[str, Path | None]:
    preset = params["input_preset"]
    demo_dir = SERGIO_HOME / "Demo"
    if preset == "demo_steady_state":
        paths = {
            "target": demo_dir / "steady-state_input_GRN.txt",
            "master_regulators": demo_dir / "steady-state_input_MRs.txt",
            "bifurcation": None,
        }
    elif preset == "demo_differentiation":
        paths = {
            "target": demo_dir / "differentiation_input_GRN.txt",
            "master_regulators": demo_dir / "differentiation_input_MRs.txt",
            "bifurcation": demo_dir / "differentiation_graph.tab",
        }
    else:
        missing = [
            input_id
            for input_id in ("sergio_target_interactions", "sergio_master_regulators")
            if input_id not in mounted_inputs
        ]
        if params["simulation_mode"] == "differentiation" and "sergio_bifurcation_matrix" not in mounted_inputs:
            missing.append("sergio_bifurcation_matrix")
        if missing:
            raise ValueError("Missing required mounted input(s): " + ", ".join(sorted(missing)))
        paths = {
            "target": Path(mounted_inputs["sergio_target_interactions"]),
            "master_regulators": Path(mounted_inputs["sergio_master_regulators"]),
            "bifurcation": (
                Path(mounted_inputs["sergio_bifurcation_matrix"])
                if params["simulation_mode"] == "differentiation"
                else None
            ),
        }
    for key, path in paths.items():
        if path is not None and not path.exists():
            raise ValueError(f"Resolved SERGIO {key} input does not exist: {path}")
    return paths


def parse_target_interactions(
    path: Path,
    *,
    number_genes: int,
    shared_coop_state: float,
) -> tuple[list[dict[str, Any]], set[int]]:
    edges: list[dict[str, Any]] = []
    targets: set[int] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=",")
        for line_no, raw_row in enumerate(reader, start=1):
            row = [value.strip() for value in raw_row if value.strip()]
            if not row:
                continue
            if len(row) < 2:
                raise ValueError(f"{path} line {line_no} must contain target ID and regulator count.")
            target = int(float(row[0]))
            n_regs = int(float(row[1]))
            if target in targets:
                raise ValueError(f"{path} line {line_no} duplicates target gene {target}.")
            if not 0 <= target < number_genes:
                raise ValueError(f"{path} line {line_no} target gene {target} is outside number_genes.")
            if n_regs <= 0:
                raise ValueError(f"{path} line {line_no} has n_regulators <= 0; master regulators belong in the MR file.")
            min_width = 2 + 2 * n_regs
            if shared_coop_state <= 0:
                min_width = 2 + 3 * n_regs
            if len(row) < min_width:
                raise ValueError(f"{path} line {line_no} expected at least {min_width} values, got {len(row)}.")
            targets.add(target)
            reg_values = row[2 : 2 + n_regs]
            k_values = row[2 + n_regs : 2 + 2 * n_regs]
            hill_values = (
                row[2 + 2 * n_regs : 2 + 3 * n_regs]
                if len(row) >= 2 + 3 * n_regs
                else [shared_coop_state] * n_regs
            )
            for idx, (reg_raw, k_raw, hill_raw) in enumerate(zip(reg_values, k_values, hill_values), start=1):
                regulator = int(float(reg_raw))
                if not 0 <= regulator < number_genes:
                    raise ValueError(f"{path} line {line_no} regulator {regulator} is outside number_genes.")
                k_value = float(k_raw)
                hill = float(hill_raw) if shared_coop_state <= 0 else shared_coop_state
                if not np.isfinite(k_value) or not np.isfinite(hill):
                    raise ValueError(f"{path} line {line_no} regulator entry {idx} contains a non-finite value.")
                edges.append(
                    {
                        "target": target,
                        "regulator": regulator,
                        "k": k_value,
                        "hill": hill,
                        "line_no": line_no,
                    }
                )
    if not edges:
        raise ValueError(f"{path} contains no target interactions.")
    return edges, targets


def parse_master_regulators(path: Path, *, number_bins: int, number_genes: int) -> set[int]:
    master_regulators: set[int] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=",")
        for line_no, raw_row in enumerate(reader, start=1):
            row = [value.strip() for value in raw_row if value.strip()]
            if not row:
                continue
            if len(row) != number_bins + 1:
                raise ValueError(
                    f"{path} line {line_no} expected {number_bins + 1} values for number_bins={number_bins}, got {len(row)}."
                )
            gene_id = int(float(row[0]))
            if gene_id in master_regulators:
                raise ValueError(f"{path} line {line_no} duplicates master regulator {gene_id}.")
            if not 0 <= gene_id < number_genes:
                raise ValueError(f"{path} line {line_no} master regulator {gene_id} is outside number_genes.")
            rates = [float(value) for value in row[1:]]
            if any(not np.isfinite(rate) for rate in rates):
                raise ValueError(f"{path} line {line_no} contains a non-finite production rate.")
            master_regulators.add(gene_id)
    if not master_regulators:
        raise ValueError(f"{path} contains no master regulators.")
    return master_regulators


def validate_graph_universe(targets: set[int], masters: set[int], number_genes: int) -> None:
    overlap = sorted(targets.intersection(masters))
    if overlap:
        raise ValueError("SERGIO target and master-regulator files overlap for gene IDs: " + ", ".join(map(str, overlap[:8])))
    expected = set(range(number_genes))
    observed = targets.union(masters)
    if observed != expected:
        missing = sorted(expected.difference(observed))[:8]
        extra = sorted(observed.difference(expected))[:8]
        raise ValueError(
            "SERGIO input files must represent every gene exactly once as target or master regulator; "
            f"missing={missing}, extra={extra}."
        )


def load_bifurcation_matrix(path: Path, *, number_bins: int) -> np.ndarray:
    rows: list[list[float]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.replace(",", " ").split()
        try:
            rows.append([float(part) for part in parts])
        except ValueError as exc:
            raise ValueError(f"{path} line {line_no} contains a non-numeric bifurcation value.") from exc
    matrix = np.array(rows, dtype=float)
    if matrix.shape != (number_bins, number_bins):
        raise ValueError(f"{path} must be a {number_bins}x{number_bins} matrix; observed shape {matrix.shape}.")
    if not np.all(np.isfinite(matrix)):
        raise ValueError(f"{path} contains non-finite values.")
    if np.any(matrix < 0):
        raise ValueError(f"{path} contains negative transition rates.")
    if np.any(np.diag(matrix) != 0):
        raise ValueError(f"{path} must not contain self transitions on the diagonal.")
    graph = nx.DiGraph(matrix)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError(f"{path} must be acyclic.")
    if any(np.count_nonzero(matrix[:, col]) > 1 for col in range(number_bins)):
        raise ValueError(f"{path} must have at most one parent per child bin.")
    return matrix


def copy_input_files(paths: dict[str, Path | None], raw_dir: Path) -> dict[str, str]:
    provenance_names = {
        "target": "input_target.csv",
        "master_regulators": "input_master_regulators.csv",
        "bifurcation": "input_bifurcation.tsv",
    }
    copied: dict[str, str] = {}
    for key, path in paths.items():
        if path is None:
            continue
        dest = raw_dir / provenance_names.get(key, f"input_{key}.txt")
        shutil.copy2(path, dest)
        copied[key] = dest.relative_to(raw_dir.parent.parent).as_posix()
    return copied


def public_gene(gene_id: int) -> str:
    return f"gene_{gene_id}"


def public_group(bin_id: int) -> str:
    return f"bin_{bin_id}"


def public_cell(bin_id: int, cell_idx: int) -> str:
    return f"cell_bin{bin_id}_{cell_idx}"


def truth_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for edge in edges:
        regulator = int(edge["regulator"])
        target = int(edge["target"])
        k_value = float(edge["k"])
        if regulator == target or k_value == 0.0:
            continue
        out.append(
            {
                "source": public_gene(regulator),
                "target": public_gene(target),
                "score": abs(k_value),
                "sign": "+" if k_value > 0 else "-",
                "evidence": "sergio_input_grn",
                "context": "global",
                "k": k_value,
                "hill": float(edge["hill"]),
                "line_no": int(edge["line_no"]),
            }
        )
    if not out:
        raise ValueError("SERGIO input GRN produced no nonzero non-self-loop public truth edges.")
    out.sort(key=lambda row: (row["context"], row["source"], row["target"], row["sign"], row["score"]))
    return out


def flatten_cell_records(number_bins: int, number_sc: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for bin_id in range(number_bins):
        for cell_idx in range(number_sc):
            records.append(
                {
                    "cell_id": public_cell(bin_id, cell_idx),
                    "bin_id": bin_id,
                    "cell_idx": cell_idx,
                    "group": public_group(bin_id),
                }
            )
    return records


def write_expression(path: Path, expression: np.ndarray, cell_records: list[dict[str, Any]]) -> None:
    if expression.ndim != 3:
        raise ValueError(f"SERGIO expression must be 3-dimensional, got shape {expression.shape}.")
    if not np.all(np.isfinite(expression)):
        raise ValueError("SERGIO expression contains non-finite values.")
    _, number_genes, _ = expression.shape
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["gene"] + [record["cell_id"] for record in cell_records])
        for gene_id in range(number_genes):
            row = [public_gene(gene_id)]
            for record in cell_records:
                value = float(expression[int(record["bin_id"]), gene_id, int(record["cell_idx"])])
                row.append(f"{value:.12g}")
            writer.writerow(row)


def write_gene_universe(path: Path, number_genes: int) -> None:
    path.write_text("".join(f"{public_gene(gene_id)}\n" for gene_id in range(number_genes)), encoding="utf-8")


def write_truth_networks(path: Path, global_edges: list[dict[str, Any]], groups: list[str] | None) -> None:
    rows: list[dict[str, Any]] = []
    for edge in global_edges:
        rows.append(edge)
    if groups is not None:
        for group in groups:
            for edge in global_edges:
                copied = dict(edge)
                copied["context"] = f"group:{group}"
                rows.append(copied)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source", "target", "score", "sign", "evidence", "context"],
            lineterminator="\n",
        )
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


def write_groups(path: Path, cell_records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["cell", "cluster"])
        for record in cell_records:
            writer.writerow([record["cell_id"], record["group"]])


def topological_group_order(number_bins: int, bifurcation: np.ndarray | None) -> dict[str, int]:
    if bifurcation is None:
        return {public_group(bin_id): bin_id for bin_id in range(number_bins)}
    order = list(nx.topological_sort(nx.DiGraph(bifurcation)))
    return {public_group(int(bin_id)): idx for idx, bin_id in enumerate(order)}


def write_cell_phenotypes(path: Path, cell_records: list[dict[str, Any]], group_order: dict[str, int]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["cell", "phenotype", "order"])
        for record in cell_records:
            group = str(record["group"])
            writer.writerow([record["cell_id"], group, group_order[group]])


def write_cluster_identities(path: Path, groups: list[str], group_order: dict[str, int]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["cluster", "annotation", "order"])
        for group in groups:
            writer.writerow([group, group, group_order[group]])


def write_lineage_tree(path: Path, bifurcation: np.ndarray) -> None:
    number_bins = bifurcation.shape[0]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["child", "parent", "gain_rate", "loss_rate"])
        for child in range(number_bins):
            parents = [parent for parent in range(number_bins) if bifurcation[parent, child] > 0]
            if not parents:
                writer.writerow([public_group(child), "__root__", "0", "0"])
            else:
                writer.writerow([public_group(child), public_group(parents[0]), "0", "0"])


def write_prior_grn(path: Path, global_edges: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["source", "target", "score", "sign"])
        for edge in global_edges:
            writer.writerow([edge["source"], edge["target"], f"{float(edge['score']):.12g}", edge["sign"]])


def write_prior_grn_by_group(path: Path, global_edges: list[dict[str, Any]], groups: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["group", "source", "target", "score", "sign"])
        for group in groups:
            for edge in global_edges:
                writer.writerow([group, edge["source"], edge["target"], f"{float(edge['score']):.12g}", edge["sign"]])


def write_tf_list(path: Path, edges: list[dict[str, Any]], masters: set[int]) -> None:
    regulators = {int(edge["regulator"]) for edge in edges}.union(masters)
    path.write_text("".join(f"{public_gene(gene_id)}\n" for gene_id in sorted(regulators)), encoding="utf-8")


def write_text_gene_list(path: Path, number_genes: int) -> None:
    write_gene_universe(path, number_genes)


def write_maps(raw_dir: Path, number_genes: int, cell_records: list[dict[str, Any]]) -> None:
    with (raw_dir / "public_gene_id_map.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["sergio_gene_id", "public_gene_id"])
        for gene_id in range(number_genes):
            writer.writerow([gene_id, public_gene(gene_id)])
    with (raw_dir / "public_cell_id_map.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["bin_id", "within_bin_index", "public_cell_id", "public_group_id"])
        for record in cell_records:
            writer.writerow([record["bin_id"], record["cell_idx"], record["cell_id"], record["group"]])


def write_truth_source_edges(raw_dir: Path, edges: list[dict[str, Any]]) -> None:
    with (raw_dir / "truth_source_edges.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["line_no", "sergio_regulator_id", "sergio_target_id", "k", "hill", "source", "target", "score", "sign"])
        for edge in truth_edges(edges):
            writer.writerow(
                [
                    edge["line_no"],
                    edge["source"].removeprefix("gene_"),
                    edge["target"].removeprefix("gene_"),
                    f"{float(edge['k']):.12g}",
                    f"{float(edge['hill']):.12g}",
                    edge["source"],
                    edge["target"],
                    f"{float(edge['score']):.12g}",
                    edge["sign"],
                ]
            )


def write_matrix_tsv(path: Path, expression: np.ndarray, cell_records: list[dict[str, Any]]) -> None:
    write_expression(path, expression, cell_records)


def save_array(raw_dir: Path, name: str, value: np.ndarray, native_outputs: dict[str, str]) -> None:
    path = raw_dir / f"{name}.npy"
    np.save(path, np.asarray(value))
    native_outputs[name] = path.relative_to(raw_dir.parent.parent).as_posix()


def apply_technical_noise(
    sim: Sergio,
    expression: np.ndarray,
    params: dict[str, Any],
    raw_dir: Path,
    native_outputs: dict[str, str],
    *,
    unspliced: np.ndarray | None = None,
    spliced: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    noise = params["technical_noise"]
    if unspliced is None or spliced is None:
        current = np.asarray(expression)
        if noise["outlier_enabled"]:
            current = np.asarray(sim.outlier_effect(current, noise["outlier_prob"], noise["outlier_mean"], noise["outlier_scale"]))
            save_array(raw_dir, "outlier_expression", current, native_outputs)
        if noise["library_size_enabled"]:
            lib_factors, current = sim.lib_size_effect(current, noise["library_size_mean"], noise["library_size_scale"])
            current = np.asarray(current)
            save_array(raw_dir, "library_size_factors", np.asarray(lib_factors), native_outputs)
            save_array(raw_dir, "library_size_expression", current, native_outputs)
        if noise["dropout_enabled"]:
            mask = np.asarray(sim.dropout_indicator(current, noise["dropout_shape"], noise["dropout_percentile"]))
            current = np.multiply(mask, current)
            save_array(raw_dir, "dropout_indicator", mask, native_outputs)
            save_array(raw_dir, "dropout_expression", current, native_outputs)
        if noise["convert_to_umi"]:
            current = np.asarray(sim.convert_to_UMIcounts(current))
            save_array(raw_dir, "umi_counts", current, native_outputs)
        if not np.all(np.isfinite(current)):
            raise ValueError("Technical-noise output contains non-finite values.")
        return current, None, None

    current_u = np.asarray(unspliced)
    current_s = np.asarray(spliced)
    if noise["outlier_enabled"]:
        current_u, current_s = sim.outlier_effect_dynamics(
            current_u,
            current_s,
            noise["outlier_prob"],
            noise["outlier_mean"],
            noise["outlier_scale"],
        )
        current_u = np.asarray(current_u)
        current_s = np.asarray(current_s)
        save_array(raw_dir, "outlier_unspliced_expression", current_u, native_outputs)
        save_array(raw_dir, "outlier_spliced_expression", current_s, native_outputs)
    if noise["library_size_enabled"]:
        lib_factors, current_u, current_s = sim.lib_size_effect_dynamics(
            current_u,
            current_s,
            noise["library_size_mean"],
            noise["library_size_scale"],
        )
        current_u = np.asarray(current_u)
        current_s = np.asarray(current_s)
        save_array(raw_dir, "library_size_factors", np.asarray(lib_factors), native_outputs)
        save_array(raw_dir, "library_size_unspliced_expression", current_u, native_outputs)
        save_array(raw_dir, "library_size_spliced_expression", current_s, native_outputs)
    if noise["dropout_enabled"]:
        mask_u, mask_s = sim.dropout_indicator_dynamics(
            current_u,
            current_s,
            noise["dropout_shape"],
            noise["dropout_percentile"],
        )
        mask_u = np.asarray(mask_u)
        mask_s = np.asarray(mask_s)
        current_u = np.multiply(mask_u, current_u)
        current_s = np.multiply(mask_s, current_s)
        save_array(raw_dir, "dropout_indicator", np.stack([mask_u, mask_s]), native_outputs)
        save_array(raw_dir, "dropout_unspliced_expression", current_u, native_outputs)
        save_array(raw_dir, "dropout_spliced_expression", current_s, native_outputs)
    if noise["convert_to_umi"]:
        current_u, current_s = sim.convert_to_UMIcounts_dynamics(current_u, current_s)
        current_u = np.asarray(current_u)
        current_s = np.asarray(current_s)
        save_array(raw_dir, "umi_counts", select_differentiation_expression(current_u, current_s, params), native_outputs)
    if not np.all(np.isfinite(current_u)) or not np.all(np.isfinite(current_s)):
        raise ValueError("Technical-noise dynamics output contains non-finite values.")
    return select_differentiation_expression(current_u, current_s, params), current_u, current_s


def select_differentiation_expression(unspliced: np.ndarray, spliced: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    mode = params["differentiation_expression"]
    if mode == "unspliced":
        return np.asarray(unspliced)
    if mode == "spliced":
        return np.asarray(spliced)
    return np.asarray(unspliced) + np.asarray(spliced)


def run_sergio(
    params: dict[str, Any],
    input_paths: dict[str, Path | None],
    raw_dir: Path,
    native_outputs: dict[str, str],
) -> tuple[Sergio, np.ndarray, np.ndarray | None, np.ndarray | None]:
    bifurcation = None
    if params["simulation_mode"] == "differentiation":
        bifurcation_path = input_paths["bifurcation"]
        if bifurcation_path is None:
            raise ValueError("Differentiation mode requires a bifurcation matrix.")
        bifurcation = load_bifurcation_matrix(bifurcation_path, number_bins=params["number_bins"])
        np.save(raw_dir / "bifurcation_matrix.npy", bifurcation)

    sim = Sergio(
        number_genes=params["number_genes"],
        number_bins=params["number_bins"],
        number_sc=params["number_sc"],
        noise_params=params["noise_params"],
        noise_type=params["noise_type"],
        decays=params["decays"],
        dynamics=params["simulation_mode"] == "differentiation",
        sampling_state=params["sampling_state"],
        tol=params["tol"],
        window_length=params["window_length"],
        dt=params["dt"],
        optimize_sampling=params["optimize_sampling"],
        bifurcation_matrix=bifurcation,
        noise_params_splice=params["noise_params_splice"],
        noise_type_splice=params["noise_type_splice"],
        splice_ratio=params["splice_ratio"],
        dt_splice=params["dt_splice"],
    )
    sim.build_graph(str(input_paths["target"]), str(input_paths["master_regulators"]), params["shared_coop_state"])
    if params["simulation_mode"] == "differentiation":
        sim.simulate_dynamics()
        unspliced, spliced = sim.getExpressions_dynamics()
        unspliced = np.asarray(unspliced)
        spliced = np.asarray(spliced)
        clean = select_differentiation_expression(unspliced, spliced, params)
        save_array(raw_dir, "unspliced_expression", unspliced, native_outputs)
        save_array(raw_dir, "spliced_expression", spliced, native_outputs)
        save_array(raw_dir, "clean_expression", clean, native_outputs)
        return sim, clean, unspliced, spliced
    sim.simulate()
    clean = np.asarray(sim.getExpressions())
    save_array(raw_dir, "clean_expression", clean, native_outputs)
    return sim, clean, None, None


def write_session_info(raw_dir: Path) -> None:
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(SERGIO_HOME), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # noqa: BLE001
        commit = "unknown"
    lines = [
        f"python={platform.python_version()}",
        f"platform={platform.platform()}",
        f"numpy={np.__version__}",
        f"scipy={scipy.__version__}",
        f"networkx={nx.__version__}",
        f"sergio_home={SERGIO_HOME}",
        f"sergio_commit={commit}",
    ]
    (raw_dir / "session_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest(
    output_dir: Path,
    request: dict[str, Any],
    expression: np.ndarray,
    extras_paths: dict[str, str | None],
    native_outputs: dict[str, str],
) -> None:
    manifest = {
        "schema_version": "1.0",
        "simulator_id": "sergio",
        "profile": request["profile"],
        "seed": int(request["seed"]),
        "expression": {
            "path": "expression.tsv",
            "genes": int(expression.shape[1]),
            "columns": int(expression.shape[0] * expression.shape[2]),
            "column_kind": "cells",
            "expression_profile": "scrna",
        },
        "extras": {key: extras_paths.get(key) for key in EXTRA_KEYS},
        "native_outputs": native_outputs,
        "truth": {
            "gene_universe": "truth/gene_universe.txt",
            "networks": "truth/networks.csv",
        },
        "provenance": {
            "raw_dir": "provenance/raw",
            "notes": "SERGIO run from the public GitHub package API pinned to a6190b74425112834c8fa9b4b6157d9cb3d1ab88; normalized truth and extras are wrapper-derived from the run inputs and public output arrays.",
        },
    }
    write_json(output_dir / "simulator-output-manifest.json", manifest)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SERGIO and emit an ANDREA normalized simulator package.")
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
    raw_dir.mkdir(parents=True, exist_ok=True)

    try:
        write_progress(output_dir, "running", "validate_request", "Reading simulator-run-request.json.", percent=10)
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if request.get("simulator_id") != "sergio":
            raise ValueError("simulator-run-request.json must have simulator_id='sergio'.")
        for field in ("profile", "seed", "effective_extras", "params", "runtime_resources"):
            if field not in request:
                raise ValueError(f"simulator-run-request.json is missing required field {field!r}.")
        write_json(raw_dir / "request.json", request)

        if as_int(request.get("runtime_resources", {}).get("threads", 1), "runtime_resources.threads", minimum=1) != 1:
            raise ValueError("SERGIO exposes no public thread control; runtime_resources.threads must be 1.")

        params = normalize_params(dict(request.get("params", {})))
        extras = {str(item) for item in request.get("effective_extras", [])}
        validate_profile_and_params(str(request["profile"]), extras, params)
        write_json(raw_dir / "resolved_params.json", params)

        seed = as_int(request["seed"], "seed", minimum=1)
        np.random.seed(seed)

        write_progress(
            output_dir,
            "running",
            "initialise_model",
            "Resolving and validating SERGIO input files.",
            percent=20,
        )
        input_paths = resolve_inputs(params, {str(k): str(v) for k, v in request.get("mounted_inputs", {}).items()})
        edges, targets = parse_target_interactions(
            Path(input_paths["target"]),
            number_genes=params["number_genes"],
            shared_coop_state=params["shared_coop_state"],
        )
        masters = parse_master_regulators(
            Path(input_paths["master_regulators"]),
            number_bins=params["number_bins"],
            number_genes=params["number_genes"],
        )
        validate_graph_universe(targets, masters, params["number_genes"])
        copied_inputs = copy_input_files(input_paths, raw_dir)
        write_json(raw_dir / "input_files.json", copied_inputs)
        write_truth_source_edges(raw_dir, edges)

        bifurcation = None
        if input_paths["bifurcation"] is not None:
            bifurcation = load_bifurcation_matrix(Path(input_paths["bifurcation"]), number_bins=params["number_bins"])

        write_progress(output_dir, "running", "run_simulator", "Running SERGIO public Python API.", percent=45)
        native_outputs: dict[str, str] = {}
        stdout = io.StringIO()
        stderr = io.StringIO()
        heartbeat_stop = threading.Event()
        heartbeat_started_at = time.monotonic()
        heartbeat_thread = threading.Thread(
            target=progress_heartbeat,
            args=(output_dir, heartbeat_stop, heartbeat_started_at),
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                sim, clean_expression, unspliced, spliced = run_sergio(params, input_paths, raw_dir, native_outputs)
                expression, final_unspliced, final_spliced = apply_technical_noise(
                    sim,
                    clean_expression,
                    params,
                    raw_dir,
                    native_outputs,
                    unspliced=unspliced,
                    spliced=spliced,
                )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=5.0)
        (raw_dir / "upstream_stdout.log").write_text(stdout.getvalue(), encoding="utf-8")
        (raw_dir / "upstream_stderr.log").write_text(stderr.getvalue(), encoding="utf-8")
        if final_unspliced is not None:
            save_array(raw_dir, "final_unspliced_expression", final_unspliced, native_outputs)
        if final_spliced is not None:
            save_array(raw_dir, "final_spliced_expression", final_spliced, native_outputs)

        write_progress(
            output_dir,
            "running",
            "package_outputs",
            "Writing normalized expression, truth and extras.",
            percent=75,
        )
        cell_records = flatten_cell_records(params["number_bins"], params["number_sc"])
        group_ids = [public_group(bin_id) for bin_id in range(params["number_bins"])]
        group_order = topological_group_order(params["number_bins"], bifurcation)
        global_edges = truth_edges(edges)

        write_expression(output_dir / "expression.tsv", expression, cell_records)
        write_gene_universe(output_dir / "truth" / "gene_universe.txt", params["number_genes"])
        need_group_truth = request["profile"] == "scrna_grouped"
        write_truth_networks(output_dir / "truth" / "networks.csv", global_edges, group_ids if need_group_truth else None)
        write_maps(raw_dir, params["number_genes"], cell_records)
        write_matrix_tsv(raw_dir / "final_expression.tsv", expression, cell_records)

        extras_paths: dict[str, str | None] = {key: None for key in EXTRA_KEYS}
        need_groups = need_group_truth or bool(extras.intersection({"groups", "cell_phenotypes", "cluster_identities", "lineage_tree", "prior_grn_by_group"}))
        if need_groups:
            write_groups(output_dir / "extras" / "groups.tsv", cell_records)
            extras_paths["groups"] = "extras/groups.tsv"
        if "cell_phenotypes" in extras:
            write_cell_phenotypes(output_dir / "extras" / "cell_phenotypes.tsv", cell_records, group_order)
            extras_paths["cell_phenotypes"] = "extras/cell_phenotypes.tsv"
        if "cluster_identities" in extras:
            write_cluster_identities(output_dir / "extras" / "cluster_identities.tsv", group_ids, group_order)
            extras_paths["cluster_identities"] = "extras/cluster_identities.tsv"
        if "enrichment_background" in extras:
            write_text_gene_list(output_dir / "extras" / "enrichment_background.txt", params["number_genes"])
            extras_paths["enrichment_background"] = "extras/enrichment_background.txt"
        if "lineage_tree" in extras:
            if bifurcation is None:
                raise ValueError("lineage_tree requires a resolved bifurcation matrix.")
            write_lineage_tree(output_dir / "extras" / "lineage_tree.tsv", bifurcation)
            extras_paths["lineage_tree"] = "extras/lineage_tree.tsv"
        if "prior_grn" in extras:
            write_prior_grn(output_dir / "extras" / "prior_grn.tsv", global_edges)
            extras_paths["prior_grn"] = "extras/prior_grn.tsv"
        if "tf_list" in extras:
            write_tf_list(output_dir / "extras" / "tf_list.txt", edges, masters)
            extras_paths["tf_list"] = "extras/tf_list.txt"
        if "prior_grn_by_group" in extras:
            write_prior_grn_by_group(output_dir / "extras" / "prior_grn_by_group.tsv", global_edges, group_ids)
            extras_paths["prior_grn_by_group"] = "extras/prior_grn_by_group.tsv"

        write_session_info(raw_dir)
        write_progress(output_dir, "running", "write_manifest", "Writing simulator-output-manifest.json.", percent=95)
        write_manifest(output_dir, request, expression, extras_paths, native_outputs)
        write_progress(output_dir, "completed", "done", "SERGIO simulation package completed.", percent=100)
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
