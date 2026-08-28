#!/usr/bin/env python3
"""ANDREA wrapper for GeneSPIDER2.

This wrapper executes the public GeneSPIDER2 MATLAB API through
``run_genespider2.m``. Python owns ANDREA request validation, ID mapping,
normalized output writing and provenance packaging.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

GENESPIDER2_COMMIT = "0ac785abf89dbf65cb01132da703d0e75196abc2"
GENESPIDER2_HOME = Path(os.environ.get("GENESPIDER2_HOME", "/opt/genespider"))

EXTRA_KEYS = [
    "groups",
    "column_descriptors",
    "column_phenotypes",
    "cluster_identities",
    "enrichment_background",
    "interventions",
    "lineage_tree",
    "perturbation_design",
    "pseudotime",
    "prior_grn",
    "tf_list",
    "prior_grn_by_group",
    "replicates",
    "timepoints",
    "spatial_coordinates",
    "chromatin_accessibility",
    "chromatin_regions",
    "cell_cell_interactions",
]

DEFAULT_PARAMS: dict[str, Any] = {
    "network_source": "scalefree2",
    "num_genes": 50,
    "average_degree": 3.0,
    "network": {
        "scalefree2_alpha": 1.2,
        "activation_probability": 0.62,
    },
    "perturbation": {
        "replicates_per_gene": 2,
        "strength": 1.0,
    },
    "bulk": {
        "snr": 0.1,
        "snr_model": "SNR_L",
    },
    "single_cell": {
        "snr": 0.1,
        "control_snr": 0.1,
        "snr_model": "SNR_L",
        "raw_counts": True,
        "right_tail": 2.0,
        "negbin_prob": 0.5,
        "dispersion": 1.0,
        "n_clusts": 5,
        "logbase": 10.0,
        "ds_min": 0.3,
        "ds_max": 0.6,
    },
    "grouping": {
        "method": "kmeans_expression",
    },
    "time_series": {
        "time_points": 20,
        "perturbed_gene_index": 1,
        "perturbation_strength": -1.0,
        "input_noise_std": 0.0,
    },
}


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    score: float
    sign: str


@dataclass(frozen=True)
class ColumnMeta:
    column: str
    condition: str
    perturbation: str
    target: str
    dose: float
    timepoint: float
    replicate: str
    control: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    if isinstance(value, float) and number != value:
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
    exclusive_min: bool = False,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric.") from exc
    if not np.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if minimum is not None:
        if exclusive_min and not number > minimum:
            raise ValueError(f"{name} must be > {minimum}.")
        if not exclusive_min and number < minimum:
            raise ValueError(f"{name} must be >= {minimum}.")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be <= {maximum}.")
    return number


def as_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean.")
    return value


def normalize_params(raw: dict[str, Any]) -> dict[str, Any]:
    params = deep_merge(DEFAULT_PARAMS, raw)
    if params["network_source"] not in {"scalefree2", "input_tsv"}:
        raise ValueError("network_source must be scalefree2 or input_tsv.")
    params["num_genes"] = as_int(params["num_genes"], "num_genes", minimum=2)
    params["average_degree"] = as_float(params["average_degree"], "average_degree", minimum=1.0)
    if params["network_source"] == "scalefree2" and params["average_degree"] >= params["num_genes"]:
        raise ValueError("average_degree must be smaller than num_genes.")

    network = params["network"]
    network["scalefree2_alpha"] = as_float(network["scalefree2_alpha"], "network.scalefree2_alpha", minimum=0.0, exclusive_min=True)
    network["activation_probability"] = as_float(network["activation_probability"], "network.activation_probability", minimum=0.0, maximum=1.0)

    perturbation = params["perturbation"]
    perturbation["replicates_per_gene"] = as_int(perturbation["replicates_per_gene"], "perturbation.replicates_per_gene", minimum=1, maximum=100)
    perturbation["strength"] = as_float(perturbation["strength"], "perturbation.strength", minimum=0.0, exclusive_min=True)

    bulk = params["bulk"]
    bulk["snr"] = as_float(bulk["snr"], "bulk.snr", minimum=0.0, exclusive_min=True)
    if bulk["snr_model"] not in {"SNR_L", "SNR_vov", "SNR_movd", "SNR_movd2", "SNR_cov", "SNR_manual"}:
        raise ValueError("bulk.snr_model is not supported.")

    single = params["single_cell"]
    single["snr"] = as_float(single["snr"], "single_cell.snr", minimum=0.0, exclusive_min=True)
    single["control_snr"] = as_float(single["control_snr"], "single_cell.control_snr", minimum=0.0, exclusive_min=True)
    if single["snr_model"] not in {"SNR_L", "SNR_vov", "SNR_movd", "SNR_movd2", "SNR_cov", "SNR_manual"}:
        raise ValueError("single_cell.snr_model is not supported.")
    single["raw_counts"] = as_bool(single["raw_counts"], "single_cell.raw_counts")
    single["right_tail"] = as_float(single["right_tail"], "single_cell.right_tail", minimum=0.0, exclusive_min=True)
    single["negbin_prob"] = as_float(single["negbin_prob"], "single_cell.negbin_prob", minimum=0.0, maximum=1.0, exclusive_min=True)
    single["dispersion"] = as_float(single["dispersion"], "single_cell.dispersion", minimum=0.0, exclusive_min=True)
    single["n_clusts"] = as_int(single["n_clusts"], "single_cell.n_clusts", minimum=1)
    single["logbase"] = as_float(single["logbase"], "single_cell.logbase", minimum=0.0, exclusive_min=True)
    single["ds_min"] = as_float(single["ds_min"], "single_cell.ds_min", minimum=0.0, maximum=1.0)
    single["ds_max"] = as_float(single["ds_max"], "single_cell.ds_max", minimum=0.0, maximum=1.0)
    if single["ds_min"] > single["ds_max"]:
        raise ValueError("single_cell.ds_min must be <= single_cell.ds_max.")
    if params["grouping"]["method"] != "kmeans_expression":
        raise ValueError("grouping.method must be kmeans_expression.")

    time_series = params["time_series"]
    time_series["time_points"] = as_int(time_series["time_points"], "time_series.time_points", minimum=1)
    time_series["perturbed_gene_index"] = as_int(time_series["perturbed_gene_index"], "time_series.perturbed_gene_index", minimum=1)
    time_series["perturbation_strength"] = as_float(time_series["perturbation_strength"], "time_series.perturbation_strength")
    time_series["input_noise_std"] = as_float(time_series["input_noise_std"], "time_series.input_noise_std", minimum=0.0)
    if time_series["perturbed_gene_index"] > params["num_genes"]:
        raise ValueError("time_series.perturbed_gene_index must be <= num_genes.")
    return params


def request_contexts(request: dict[str, Any]) -> set[str]:
    contexts = request.get("truth_requirements", {}).get("contexts", [])
    return {str(item) for item in contexts}


def resolve_mode(request: dict[str, Any]) -> str:
    axes = request["data_axes"]
    contexts = request_contexts(request)
    if contexts - {"global", "group"}:
        raise ValueError("GeneSPIDER2 only supports global and fixed-GRN group truth.")
    if axes == {
        "measurement": "rna_expression",
        "resolution": "bulk",
        "column_kind": "perturbations",
        "experimental_design": "perturbational",
    } and contexts == {"global"}:
        return "bulk_perturbational"
    if axes == {
        "measurement": "rna_expression",
        "resolution": "bulk",
        "column_kind": "timepoints",
        "experimental_design": "time_series",
    } and contexts == {"global"}:
        return "bulk_time_series"
    if axes == {
        "measurement": "rna_expression",
        "resolution": "single_cell",
        "column_kind": "cells",
        "experimental_design": "perturbational",
    }:
        if contexts == {"global"} or contexts == {"global", "group"}:
            return "single_cell_perturbational"
    raise ValueError("Requested data_axes/truth_requirements are not supported by GeneSPIDER2.")


def validate_request(request: dict[str, Any], params: dict[str, Any], extras: set[str]) -> str:
    if request.get("simulator_id") != "genespider2":
        raise ValueError("simulator-run-request.json must have simulator_id='genespider2'.")
    for field in ("data_axes", "truth_requirements", "seed", "effective_extras", "params", "runtime_resources"):
        if field not in request:
            raise ValueError(f"simulator-run-request.json is missing required field {field!r}.")
    if "tf_list" not in extras:
        raise ValueError("effective_extras must include required extra tf_list.")
    mode = resolve_mode(request)
    if "group" in request_contexts(request) and params["single_cell"]["n_clusts"] < 2:
        raise ValueError("Single-cell group truth requires single_cell.n_clusts >= 2.")

    supported_by_mode = {
        "bulk_perturbational": {
            "perturbation_design",
            "enrichment_background",
            "interventions",
            "prior_grn",
            "replicates",
            "tf_list",
        },
        "bulk_time_series": {
            "perturbation_design",
            "enrichment_background",
            "interventions",
            "prior_grn",
            "tf_list",
            "timepoints",
        },
        "single_cell_perturbational": {
            "perturbation_design",
            "enrichment_background",
            "interventions",
            "prior_grn",
            "replicates",
            "tf_list",
        },
    }
    if "group" in request_contexts(request):
        supported_by_mode["single_cell_perturbational"] = supported_by_mode["single_cell_perturbational"].union(
            {"groups", "column_phenotypes", "cluster_identities", "prior_grn_by_group"}
        )
    unsupported = sorted(extras.difference(supported_by_mode[mode]))
    if unsupported:
        raise ValueError(f"Unsupported GeneSPIDER2 extras for selected capability: {unsupported}.")
    if mode == "bulk_time_series" and "timepoints" not in extras:
        raise ValueError("GeneSPIDER2 bulk time_series capability requires effective_extras to include timepoints.")
    if mode in {"bulk_perturbational", "single_cell_perturbational"}:
        missing = sorted({"perturbation_design", "interventions"}.difference(extras))
        if missing:
            raise ValueError(f"GeneSPIDER2 perturbational capabilities require extras: {missing}.")
    if "group" in request_contexts(request) and "groups" not in extras:
        raise ValueError("Group truth requires effective_extras to include groups.")
    return mode


def gene_ids(count: int) -> list[str]:
    width = max(3, len(str(count)))
    return [f"G{idx:0{width}d}" for idx in range(1, count + 1)]


def write_matrix(path: Path, matrix: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(path, matrix, delimiter="\t", fmt="%.12g")


def read_matrix(path: Path) -> np.ndarray:
    data = np.loadtxt(path, delimiter="\t")
    if data.ndim == 0:
        return np.asarray([[float(data)]])
    if data.ndim == 1:
        return data.reshape((-1, 1))
    return np.asarray(data, dtype=float)


def load_input_network(path: Path) -> tuple[list[str], np.ndarray]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        missing = {"target", "regulator", "effect"}.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"regulatory_network is missing required columns: {sorted(missing)}")
        rows = []
        genes: set[str] = set()
        for row in reader:
            target = str(row["target"]).strip()
            regulator = str(row["regulator"]).strip()
            if not target or not regulator:
                raise ValueError("regulatory_network contains empty target/regulator.")
            effect = float(str(row["effect"]).strip())
            if effect == 0:
                continue
            rows.append((target, regulator, effect))
            genes.add(target)
            genes.add(regulator)
    if not rows:
        raise ValueError("regulatory_network contains no nonzero edges.")
    ordered_genes = sorted(genes)
    index = {gene: idx for idx, gene in enumerate(ordered_genes)}
    matrix = np.zeros((len(ordered_genes), len(ordered_genes)), dtype=float)
    for target, regulator, effect in rows:
        matrix[index[target], index[regulator]] = effect
    np.fill_diagonal(matrix, -1.0)
    return ordered_genes, matrix


def build_perturbation_matrix(params: dict[str, Any], genes: list[str]) -> tuple[np.ndarray, list[ColumnMeta]]:
    if len(genes) != params["num_genes"]:
        raise ValueError("Perturbation gene IDs must match num_genes.")
    reps = params["perturbation"]["replicates_per_gene"]
    strength = params["perturbation"]["strength"]
    columns = len(genes) * reps
    matrix = np.zeros((len(genes), columns), dtype=float)
    metadata: list[ColumnMeta] = []
    col_idx = 0
    for gene_idx, gene in enumerate(genes):
        for rep in range(1, reps + 1):
            matrix[gene_idx, col_idx] = -strength
            metadata.append(
                ColumnMeta(
                    column=f"perturb_{gene}_r{rep}",
                    condition=f"knockdown_{gene}",
                    perturbation="knockdown",
                    target=gene,
                    dose=float(strength),
                    timepoint=0.0,
                    replicate=f"r{rep}",
                    control=False,
                )
            )
            col_idx += 1
    return matrix, metadata


def build_time_series_vector(params: dict[str, Any]) -> tuple[np.ndarray, list[str]]:
    vector = np.zeros((params["num_genes"], 1), dtype=float)
    vector[params["time_series"]["perturbed_gene_index"] - 1, 0] = params["time_series"]["perturbation_strength"]
    columns = [f"timepoint_{idx:03d}" for idx in range(0, params["time_series"]["time_points"] + 1)]
    return vector, columns


def write_expression(path: Path, matrix: np.ndarray, genes: list[str], columns: list[str]) -> None:
    if matrix.shape != (len(genes), len(columns)):
        raise ValueError(
            f"expression matrix has shape {matrix.shape}, expected {(len(genes), len(columns))}."
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["gene", *columns])
        for gene, values in zip(genes, matrix):
            writer.writerow([gene, *[f"{float(value):.12g}" for value in values]])


def write_gene_universe(path: Path, genes: list[str]) -> None:
    path.write_text("\n".join(genes) + "\n", encoding="utf-8")


def truth_edges(matrix: np.ndarray, genes: list[str]) -> list[Edge]:
    edges: list[Edge] = []
    for target_idx, target in enumerate(genes):
        for source_idx, source in enumerate(genes):
            if target_idx == source_idx:
                continue
            value = float(matrix[target_idx, source_idx])
            if value == 0.0:
                continue
            edges.append(
                Edge(
                    source=source,
                    target=target,
                    score=abs(value),
                    sign="+" if value > 0 else "-",
                )
            )
    return edges


def write_truth_networks(path: Path, edges: list[Edge], group_ids: list[str] | None) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target", "score", "sign", "evidence", "context"])
        for edge in edges:
            writer.writerow([edge.source, edge.target, f"{edge.score:.12g}", edge.sign, "simulated_truth", "global"])
        if group_ids:
            for group_id in group_ids:
                for edge in edges:
                    writer.writerow(
                        [
                            edge.source,
                            edge.target,
                            f"{edge.score:.12g}",
                            edge.sign,
                            "simulated_truth",
                            f"group:{group_id}",
                        ]
                    )


def write_text_list(path: Path, values: list[str]) -> None:
    path.write_text("\n".join(values) + "\n", encoding="utf-8")


def write_prior_grn(path: Path, edges: list[Edge]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["source", "target", "score"])
        for edge in edges:
            score = edge.score if edge.sign == "+" else -edge.score
            writer.writerow([edge.source, edge.target, f"{score:.12g}"])


def write_prior_grn_by_group(path: Path, edges: list[Edge], group_ids: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["group", "source", "target", "score"])
        for group_id in group_ids:
            for edge in edges:
                score = edge.score if edge.sign == "+" else -edge.score
                writer.writerow([group_id, edge.source, edge.target, f"{score:.12g}"])


def write_interventions(path: Path, metadata: list[ColumnMeta]) -> None:
    seen: dict[str, ColumnMeta] = {}
    for item in metadata:
        seen.setdefault(item.condition, item)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["intervention", "target", "effect", "sign", "dose", "timepoint"])
        for item in seen.values():
            writer.writerow([item.condition, item.target, item.perturbation, -1, f"{item.dose:.12g}", f"{item.timepoint:.12g}"])


def write_perturbation_design(path: Path, metadata: list[ColumnMeta]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["column", "condition", "perturbation", "target", "dose", "timepoint", "replicate", "control"])
        for item in metadata:
            writer.writerow(
                [
                    item.column,
                    item.condition,
                    item.perturbation,
                    item.target,
                    f"{item.dose:.12g}",
                    f"{item.timepoint:.12g}",
                    item.replicate,
                    "false" if not item.control else "true",
                ]
            )


def write_replicates(path: Path, metadata: list[ColumnMeta]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["column", "replicate", "condition"])
        for item in metadata:
            writer.writerow([item.column, item.replicate, item.condition])


def simts_time_grid(network_matrix: np.ndarray, n_columns: int) -> list[float]:
    eigenvalue_scale = float(np.min(np.abs(np.linalg.eigvals(network_matrix))))
    timestep = eigenvalue_scale * 0.1
    return [idx * timestep for idx in range(n_columns)]


def write_timepoints(path: Path, columns: list[str], timepoints: list[float] | None = None) -> None:
    values = timepoints if timepoints is not None else [float(idx) for idx, _column in enumerate(columns)]
    if len(values) != len(columns):
        raise ValueError("timepoints length must match expression columns.")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["column", "timepoint", "timepoint_label"])
        for column, timepoint in zip(columns, values, strict=True):
            writer.writerow([column, f"{timepoint:.12g}", column])


def write_time_grid(path: Path, columns: list[str], timepoints: list[float]) -> None:
    if len(timepoints) != len(columns):
        raise ValueError("time grid length must match expression columns.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["column", "timepoint"])
        for column, timepoint in zip(columns, timepoints, strict=True):
            writer.writerow([column, f"{timepoint:.12g}"])


def kmeans_groups(expression: np.ndarray, columns: list[str], k: int, seed: int) -> dict[str, str]:
    data = expression.T.astype(float)
    if k <= 1:
        return {column: "group_1" for column in columns}
    if data.shape[0] < k:
        raise ValueError("Cannot derive more groups than expression columns.")
    std = data.std(axis=0)
    std[std == 0] = 1.0
    data = (data - data.mean(axis=0)) / std
    rng = np.random.default_rng(seed)
    first = int(rng.integers(0, data.shape[0]))
    centers = [data[first]]
    while len(centers) < k:
        distances = np.min(
            np.stack([np.sum((data - center) ** 2, axis=1) for center in centers], axis=1),
            axis=1,
        )
        next_idx = int(np.argmax(distances))
        centers.append(data[next_idx])
    centers_arr = np.vstack(centers)
    labels = np.zeros(data.shape[0], dtype=int)
    for _ in range(100):
        dist = np.stack([np.sum((data - center) ** 2, axis=1) for center in centers_arr], axis=1)
        new_labels = np.argmin(dist, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for group_idx in range(k):
            mask = labels == group_idx
            if np.any(mask):
                centers_arr[group_idx] = data[mask].mean(axis=0)
    return {column: f"group_{labels[idx] + 1}" for idx, column in enumerate(columns)}


def write_groups(path: Path, assignments: dict[str, str]) -> list[str]:
    group_ids = sorted(set(assignments.values()))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["column", "cluster"])
        for column, group_id in assignments.items():
            writer.writerow([column, group_id])
    return group_ids


def write_column_phenotypes(path: Path, assignments: dict[str, str], group_ids: list[str]) -> None:
    order = {group_id: idx for idx, group_id in enumerate(group_ids)}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["column", "phenotype", "order"])
        for column, group_id in assignments.items():
            writer.writerow([column, group_id, order[group_id]])


def write_cluster_identities(path: Path, group_ids: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["cluster", "annotation", "order"])
        for idx, group_id in enumerate(group_ids):
            writer.writerow([group_id, f"GeneSPIDER2 derived group {idx + 1}", idx])


def write_session_info(raw_dir: Path, backend: str | None) -> None:
    lines = [
        f"python={platform.python_version()}",
        f"platform={platform.platform()}",
        f"numpy={np.__version__}",
        f"genespider_home={GENESPIDER2_HOME}",
        f"genespider_commit={GENESPIDER2_COMMIT}",
        f"backend={backend or 'not_found'}",
        f"matlab_runtime_root={os.environ.get('MATLAB_RUNTIME_ROOT', '')}",
    ]
    (raw_dir / "session_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def compiled_runner_command() -> list[str] | None:
    runtime_root = os.environ.get("MATLAB_RUNTIME_ROOT", "/opt/matlab_runtime/R2026a")
    configured = os.environ.get("GENESPIDER2_COMPILED_RUNNER")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path("/opt/genespider2/compiled/run_run_genespider2.sh"),
            Path(__file__).resolve().parent / "compiled" / "run_run_genespider2.sh",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate), runtime_root]
    return None


def find_matlab() -> str:
    configured = os.environ.get("MATLAB_BIN")
    if configured:
        return configured
    path = shutil.which("matlab")
    if path:
        return path
    raise RuntimeError(
        "MATLAB executable not found. GeneSPIDER2 uses MATLAB-only syntax and Statistics Toolbox functions; "
        "Octave is not compatible with the pinned upstream implementation."
    )


def run_matlab(matlab_bin: str, request_path: Path, matlab_out_dir: Path, raw_dir: Path) -> None:
    script_dir = Path(__file__).resolve().parent
    command = (
        f"addpath('{script_dir.as_posix()}'); "
        f"run_genespider2('{request_path.as_posix()}', '{matlab_out_dir.as_posix()}');"
    )
    proc = subprocess.run(
        [matlab_bin, "-batch", command],
        text=True,
        capture_output=True,
        check=False,
    )
    (raw_dir / "matlab_stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (raw_dir / "matlab_stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"GeneSPIDER2 MATLAB execution failed with exit code {proc.returncode}.")


def run_backend(request_path: Path, matlab_out_dir: Path, raw_dir: Path) -> str:
    compiled_command = compiled_runner_command()
    if compiled_command:
        proc = subprocess.run(
            [*compiled_command, request_path.as_posix(), matlab_out_dir.as_posix()],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "MCR_CACHE_ROOT": os.environ.get("MCR_CACHE_ROOT", "/tmp/mcr_cache")},
        )
        (raw_dir / "matlab_stdout.log").write_text(proc.stdout or "", encoding="utf-8")
        (raw_dir / "matlab_stderr.log").write_text(proc.stderr or "", encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"GeneSPIDER2 compiled MATLAB Runtime execution failed with exit code {proc.returncode}.")
        return "compiled:" + " ".join(compiled_command)

    matlab_bin = find_matlab()
    run_matlab(matlab_bin, request_path, matlab_out_dir, raw_dir)
    return f"matlab:{matlab_bin}"


def build_matlab_request(
    *,
    mode: str,
    request: dict[str, Any],
    params: dict[str, Any],
    raw_dir: Path,
    genes: list[str],
    input_gene_ids: list[str] | None,
) -> dict[str, Any]:
    network = params["network"]
    single = params["single_cell"]
    bulk = params["bulk"]
    time_series = params["time_series"]
    payload: dict[str, Any] = {
        "mode": mode,
        "seed": int(request["seed"]),
        "threads": as_int(request.get("runtime_resources", {}).get("threads", 1), "runtime_resources.threads", minimum=1),
        "network_source": params["network_source"],
        "num_genes": params["num_genes"],
        "average_degree": params["average_degree"],
        "scalefree2_alpha": network["scalefree2_alpha"],
        "activation_probability": network["activation_probability"],
        "bulk_snr": bulk["snr"],
        "bulk_snr_model": bulk["snr_model"],
        "single_cell_snr": single["snr"],
        "single_cell_control_snr": single["control_snr"],
        "single_cell_snr_model": single["snr_model"],
        "single_cell_raw_counts": single["raw_counts"],
        "single_cell_right_tail": single["right_tail"],
        "single_cell_negbin_prob": single["negbin_prob"],
        "single_cell_dispersion": single["dispersion"],
        "single_cell_n_clusts": single["n_clusts"],
        "single_cell_logbase": single["logbase"],
        "single_cell_ds_min": single["ds_min"],
        "single_cell_ds_max": single["ds_max"],
        "time_points": time_series["time_points"],
        "input_noise_std": time_series["input_noise_std"],
    }
    if input_gene_ids is not None:
        payload["input_gene_ids"] = input_gene_ids
    if mode in {"bulk_perturbational", "single_cell_perturbational"}:
        perturbation_matrix, _ = build_perturbation_matrix(params, genes)
        path = raw_dir / "matlab_input_perturbation_matrix.tsv"
        write_matrix(path, perturbation_matrix)
        payload["perturbation_matrix_path"] = path.as_posix()
    if mode == "bulk_time_series":
        vector, _ = build_time_series_vector(params)
        path = raw_dir / "matlab_input_time_series_perturbation_vector.tsv"
        write_matrix(path, vector)
        payload["time_series_perturbation_vector_path"] = path.as_posix()
    return payload


def copy_native(
    raw_dir: Path,
    native_dir: Path,
    requested_native_outputs: set[str],
    native_outputs: dict[str, str],
    source_name: str,
    native_id: str,
) -> None:
    if native_id not in requested_native_outputs:
        return
    path = raw_dir / "matlab_outputs" / source_name
    if path.exists():
        native_dir.mkdir(parents=True, exist_ok=True)
        native_path = native_dir / f"{native_id}{path.suffix}"
        shutil.copy2(path, native_path)
        native_outputs[native_id] = f"native/{native_path.name}"


def write_manifest(
    output_dir: Path,
    request: dict[str, Any],
    expression: np.ndarray,
    column_kind: str,
    extras_paths: dict[str, str | None],
    native_outputs: dict[str, str],
) -> None:
    manifest = {
        "schema_version": "1.0",
        "simulator_id": "genespider2",
        "data_axes": request["data_axes"],
        "truth_requirements": request["truth_requirements"],
        "seed": int(request["seed"]),
        "expression": {
            "path": "expression.tsv",
            "genes": int(expression.shape[0]),
            "columns": int(expression.shape[1]),
            "column_kind": column_kind,
        },
        "extras": {key: extras_paths.get(key) for key in EXTRA_KEYS},
        "native_outputs": native_outputs,
        "truth": {
            "gene_universe": "truth/gene_universe.txt",
            "networks": "truth/networks.csv",
        },
        "provenance": {
            "raw_dir": "provenance/raw",
            "notes": "GeneSPIDER2 run through the public MATLAB API pinned to commit "
            f"{GENESPIDER2_COMMIT}; ANDREA wrapper normalized public expression, truth and extras.",
        },
    }
    write_json(output_dir / "simulator-output-manifest.json", manifest)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GeneSPIDER2 and emit an ANDREA normalized simulator package.")
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
    matlab_out_dir = raw_dir / "matlab_outputs"
    matlab_out_dir.mkdir(parents=True, exist_ok=True)

    backend: str | None = None
    try:
        write_progress(output_dir, "running", "validate_request", "Reading simulator-run-request.json.", percent=5)
        request = json.loads(args.request.read_text(encoding="utf-8"))
        write_json(raw_dir / "simulator-run-request.json", request)
        params = normalize_params(dict(request.get("params", {})))
        extras = {str(item) for item in request.get("effective_extras", [])}
        requested_native_outputs = {str(item) for item in request.get("native_outputs", [])}
        mode = validate_request(request, params, extras)
        write_json(raw_dir / "resolved_params.json", params)

        input_gene_ids: list[str] | None = None
        if params["network_source"] == "input_tsv":
            mounted_inputs = request.get("mounted_inputs", {})
            input_path = mounted_inputs.get("regulatory_network")
            if not input_path:
                raise ValueError("regulatory_network mounted input is required when network_source=input_tsv.")
            input_gene_ids, input_matrix = load_input_network(Path(str(input_path)))
            if len(input_gene_ids) != params["num_genes"]:
                raise ValueError("num_genes must match regulatory_network unique gene count when network_source=input_tsv.")
            write_matrix(raw_dir / "matlab_input_network_matrix.tsv", input_matrix)
            params_gene_ids = input_gene_ids
        else:
            params_gene_ids = gene_ids(params["num_genes"])

        matlab_request = build_matlab_request(
            mode=mode,
            request=request,
            params=params,
            raw_dir=raw_dir,
            genes=params_gene_ids,
            input_gene_ids=input_gene_ids,
        )
        if params["network_source"] == "input_tsv":
            matlab_request["input_network_matrix_path"] = (raw_dir / "matlab_input_network_matrix.tsv").as_posix()
        matlab_request_path = raw_dir / "matlab_request.json"
        write_json(matlab_request_path, matlab_request)

        write_session_info(raw_dir, backend)
        write_progress(output_dir, "running", "run_simulator", "Running GeneSPIDER2 public MATLAB API.", percent=30)
        backend = run_backend(matlab_request_path, matlab_out_dir, raw_dir)
        write_session_info(raw_dir, backend)

        write_progress(output_dir, "running", "package_outputs", "Writing normalized outputs.", percent=75)
        expression = read_matrix(matlab_out_dir / "expression_matrix.tsv")
        network_matrix = read_matrix(matlab_out_dir / "network_matrix.tsv")
        genes = params_gene_ids
        if network_matrix.shape != (len(genes), len(genes)):
            raise ValueError("GeneSPIDER2 network matrix dimensions do not match public gene IDs.")

        if mode == "bulk_time_series":
            _vector, columns = build_time_series_vector(params)
            time_values = simts_time_grid(network_matrix, len(columns))
            write_time_grid(matlab_out_dir / "time_grid.tsv", columns, time_values)
            column_metadata = [
                ColumnMeta(
                    column=column,
                    condition=f"timepoint_{idx}",
                    perturbation="knockdown",
                    target=genes[params["time_series"]["perturbed_gene_index"] - 1],
                    dose=abs(float(params["time_series"]["perturbation_strength"])),
                    timepoint=time_values[idx],
                    replicate="r1",
                    control=idx == 0,
                )
                for idx, column in enumerate(columns)
            ]
            column_kind = "timepoints"
        else:
            _matrix, column_metadata = build_perturbation_matrix(params, genes)
            if mode == "single_cell_perturbational":
                for idx, item in enumerate(column_metadata, start=1):
                    column_metadata[idx - 1] = ColumnMeta(
                        column=f"cell_{idx:04d}",
                        condition=item.condition,
                        perturbation=item.perturbation,
                        target=item.target,
                        dose=item.dose,
                        timepoint=item.timepoint,
                        replicate=item.replicate,
                        control=item.control,
                    )
                column_kind = "cells"
            else:
                column_kind = "perturbations"
            columns = [item.column for item in column_metadata]

        write_expression(output_dir / "expression.tsv", expression, genes, columns)
        write_gene_universe(output_dir / "truth" / "gene_universe.txt", genes)
        edges = truth_edges(network_matrix, genes)
        native_outputs: dict[str, str] = {}
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "network_matrix.tsv", "network_matrix")

        extras_paths: dict[str, str | None] = {key: None for key in EXTRA_KEYS}
        group_ids: list[str] | None = None
        if "group" in request_contexts(request):
            assignments = kmeans_groups(expression, columns, params["single_cell"]["n_clusts"], int(request["seed"]))
            group_ids = write_groups(output_dir / "extras" / "groups.tsv", assignments)
            extras_paths["groups"] = "extras/groups.tsv"
        write_truth_networks(output_dir / "truth" / "networks.csv", edges, group_ids)

        if "perturbation_design" in extras:
            write_perturbation_design(output_dir / "extras" / "perturbation_design.tsv", column_metadata)
            extras_paths["perturbation_design"] = "extras/perturbation_design.tsv"
        if "interventions" in extras:
            write_interventions(output_dir / "extras" / "interventions.tsv", column_metadata)
            extras_paths["interventions"] = "extras/interventions.tsv"
        if "replicates" in extras:
            write_replicates(output_dir / "extras" / "replicates.tsv", column_metadata)
            extras_paths["replicates"] = "extras/replicates.tsv"
        if "timepoints" in extras:
            write_timepoints(output_dir / "extras" / "timepoints.tsv", columns, [item.timepoint for item in column_metadata])
            extras_paths["timepoints"] = "extras/timepoints.tsv"
        if "enrichment_background" in extras:
            write_text_list(output_dir / "extras" / "enrichment_background.txt", genes)
            extras_paths["enrichment_background"] = "extras/enrichment_background.txt"
        if "prior_grn" in extras:
            write_prior_grn(output_dir / "extras" / "prior_grn.tsv", edges)
            extras_paths["prior_grn"] = "extras/prior_grn.tsv"
        write_text_list(output_dir / "extras" / "tf_list.txt", sorted({edge.source for edge in edges}))
        extras_paths["tf_list"] = "extras/tf_list.txt"
        if group_ids is not None and "column_phenotypes" in extras:
            assignments = {row[0]: row[1] for row in csv.reader((output_dir / "extras" / "groups.tsv").open("r", encoding="utf-8"), delimiter="\t") if row and row[0] != "column"}
            write_column_phenotypes(output_dir / "extras" / "column_phenotypes.tsv", assignments, group_ids)
            extras_paths["column_phenotypes"] = "extras/column_phenotypes.tsv"
        if group_ids is not None and "cluster_identities" in extras:
            write_cluster_identities(output_dir / "extras" / "cluster_identities.tsv", group_ids)
            extras_paths["cluster_identities"] = "extras/cluster_identities.tsv"
        if group_ids is not None and "prior_grn_by_group" in extras:
            write_prior_grn_by_group(output_dir / "extras" / "prior_grn_by_group.tsv", edges, group_ids)
            extras_paths["prior_grn_by_group"] = "extras/prior_grn_by_group.tsv"

        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "perturbation_design_matrix.tsv", "perturbation_design")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "expression_matrix.tsv", "bulk_response")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "bulk_noise_matrix.tsv", "gaussian_noise")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "time_series_perturbation_vector.tsv", "perturbation_vector")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "expression_matrix.tsv", "time_series_expression")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "time_grid.tsv", "time_grid")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "expression_matrix.tsv", "single_cell_expression")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "single_cell_noise_free_fold_change.tsv", "noise_free_fold_change")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "single_cell_dropout_mask.tsv", "dropout_mask")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "single_cell_gaussian_noise.tsv", "gaussian_noise")
        copy_native(raw_dir, native_dir, requested_native_outputs, native_outputs, "single_cell_control_counts.tsv", "single_cell_control_counts")

        write_progress(output_dir, "running", "write_manifest", "Writing simulator-output-manifest.json.", percent=95)
        write_manifest(output_dir, request, expression, column_kind, extras_paths, native_outputs)
        write_progress(output_dir, "completed", "done", "GeneSPIDER2 simulation package completed.", percent=100)
        return 0
    except BaseException as exc:  # noqa: BLE001
        write_session_info(raw_dir, backend)
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
