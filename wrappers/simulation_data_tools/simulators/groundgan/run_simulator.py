#!/usr/bin/env python3
"""ANDREA wrapper for GRouNdGAN.

The wrapper executes the public GRouNdGAN causal GAN Python API from the pinned
upstream repository and normalizes the generated expression matrix, imposed
causal graph and optional perturbation metadata into ANDREA's simulator output
contract.
"""

from __future__ import annotations

import csv
import importlib.metadata
import json
import os
import pickle
import platform
import shutil
import sys
import traceback
import types
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GROUNDGAN_SRC = Path(os.environ.get("GROUNDGAN_SRC", "/opt/groundgan/src"))
GROUNDGAN_COMMIT = "2df087f9144081c46eb6ce0a1daadd273adcc50a"
BUNDLE_ROOT = Path("/opt/andrea/bundles/groundgan")
EMBEDDED_BUNDLES = {"toy_4gene", "toy_20gene", "toy_50gene"}

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
    "input_bundle": "toy_4gene",
    "run_command": "generate",
    "generation": {"num_cells": 100},
    "model": {
        "latent_dim": 2,
        "noise_per_gene": 1,
        "depth_per_gene": 1,
        "width_per_gene": 2,
        "critic_layers": [8],
        "labeler_layers": [8],
    },
    "causal_controller": {
        "latent_dim": 2,
        "generator_layers": [8],
        "critic_layers": [8],
        "checkpoint_step": 0,
    },
    "training": {"batch_size": 8},
    "preprocessing": {"library_size": 1000},
    "perturbation": {
        "target_source": "first_regulators",
        "tf_targets": [],
        "perturbation_values": [0.0],
    },
}


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    score: float = 1.0
    sign: str = "?"


@dataclass(frozen=True)
class GeneratedData:
    genes: list[str]
    columns: list[str]
    matrix_by_gene: list[list[float]]


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
    *,
    percent: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "status": status,
        "phase": phase,
        "updated_at": utc_now(),
    }
    if message:
        payload["message"] = message
    if percent is not None:
        payload["percent"] = max(0, min(100, int(percent)))
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
        return list(base) if override == {} else override
    return base if override == {} else override


def as_int(value: Any, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, not boolean.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if isinstance(value, float) and value != number:
        raise ValueError(f"{name} must be an integer.")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return number


def as_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric.") from exc
    if not (number == number and abs(number) != float("inf")):
        raise ValueError(f"{name} must be finite.")
    return number


def as_float_list(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty array of numbers.")
    return [as_float(item, f"{name}[{index}]") for index, item in enumerate(value)]


def as_str_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array of strings.")
    result: list[str] = []
    for index, item in enumerate(value):
        text = str(item).strip()
        if not text:
            raise ValueError(f"{name}[{index}] must be a non-empty string.")
        result.append(text)
    return result


def as_int_list(value: Any, name: str, *, minimum: int | None = None) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty array of integers.")
    return [as_int(item, f"{name}[{index}]", minimum=minimum) for index, item in enumerate(value)]


def normalize_params(raw: dict[str, Any]) -> dict[str, Any]:
    params = deep_merge(DEFAULT_PARAMS, raw)

    if params["input_bundle"] not in EMBEDDED_BUNDLES.union({"custom_files"}):
        allowed = ", ".join(sorted(EMBEDDED_BUNDLES.union({"custom_files"})))
        raise ValueError(f"input_bundle must be one of: {allowed}.")
    if params["run_command"] not in {"generate", "perturb"}:
        raise ValueError("run_command must be generate or perturb.")

    params["generation"]["num_cells"] = as_int(
        params["generation"]["num_cells"], "generation.num_cells", minimum=1
    )
    params["training"]["batch_size"] = as_int(
        params["training"]["batch_size"], "training.batch_size", minimum=2
    )
    if params["generation"]["num_cells"] < params["training"]["batch_size"]:
        params["training"]["batch_size"] = max(2, params["generation"]["num_cells"])

    model = params["model"]
    model["latent_dim"] = as_int(model["latent_dim"], "model.latent_dim", minimum=1)
    model["noise_per_gene"] = as_int(model["noise_per_gene"], "model.noise_per_gene", minimum=1)
    model["depth_per_gene"] = as_int(model["depth_per_gene"], "model.depth_per_gene", minimum=0)
    model["width_per_gene"] = as_int(model["width_per_gene"], "model.width_per_gene", minimum=1)
    model["critic_layers"] = as_int_list(model["critic_layers"], "model.critic_layers", minimum=1)
    model["labeler_layers"] = as_int_list(model["labeler_layers"], "model.labeler_layers", minimum=1)

    cc = params["causal_controller"]
    cc["latent_dim"] = as_int(cc["latent_dim"], "causal_controller.latent_dim", minimum=1)
    cc["generator_layers"] = as_int_list(
        cc["generator_layers"], "causal_controller.generator_layers", minimum=1
    )
    cc["critic_layers"] = as_int_list(
        cc["critic_layers"], "causal_controller.critic_layers", minimum=1
    )
    cc["checkpoint_step"] = as_int(
        cc["checkpoint_step"], "causal_controller.checkpoint_step", minimum=0
    )

    params["preprocessing"]["library_size"] = as_int(
        params["preprocessing"]["library_size"], "preprocessing.library_size", minimum=1
    )

    perturb = params["perturbation"]
    if perturb["target_source"] not in {"first_regulators", "explicit_list"}:
        raise ValueError("perturbation.target_source must be first_regulators or explicit_list.")
    perturb["tf_targets"] = as_str_list(perturb["tf_targets"], "perturbation.tf_targets")
    perturb["perturbation_values"] = as_float_list(
        perturb["perturbation_values"], "perturbation.perturbation_values"
    )
    if perturb["target_source"] == "explicit_list" and not perturb["tf_targets"]:
        raise ValueError("perturbation.tf_targets is required when target_source=explicit_list.")

    return params


def set_runtime_threads(request: dict[str, Any]) -> int:
    resources = request.get("runtime_resources", {})
    threads = as_int(resources.get("threads", 1), "runtime_resources.threads", minimum=1)
    for key in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]:
        os.environ[key] = str(threads)
    return threads


def import_upstream_modules(threads: int) -> tuple[Any, Any, Any, Any]:
    if str(GROUNDGAN_SRC) not in sys.path:
        sys.path.insert(0, str(GROUNDGAN_SRC))
    import anndata as ad  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    import torch  # noqa: PLC0415

    # GRouNdGAN's inference path imports sc_dataset through gans.gan, and
    # sc_dataset imports scanpy for training loaders. The wrapper does not train,
    # so provide the only function that loader code would need without importing
    # scanpy/numba during generation.
    scanpy_stub = types.ModuleType("scanpy")
    scanpy_stub.read_h5ad = ad.read_h5ad
    sys.modules.setdefault("scanpy", scanpy_stub)

    from factory import get_factory  # noqa: PLC0415

    torch.set_num_threads(threads)
    return ad, np, pd, torch, get_factory


def require_input(request: dict[str, Any], input_id: str) -> Path:
    mounted = request.get("mounted_inputs", {})
    raw = mounted.get(input_id)
    if not raw:
        raise ValueError(f"Missing required mounted input: {input_id}.")
    path = Path(str(raw))
    if not path.exists():
        raise ValueError(f"Mounted input does not exist for {input_id}: {path}")
    return path


def copy_input(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


def bundled_input_path(bundle_id: str, filename: str) -> Path:
    path = BUNDLE_ROOT / bundle_id / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing embedded GroundGAN bundle file: {path}")
    return path


def resolve_bundle_input_paths(request: dict[str, Any], params: dict[str, Any]) -> dict[str, Path]:
    bundle_id = str(params["input_bundle"])
    if bundle_id in EMBEDDED_BUNDLES:
        return {
            "reference": bundled_input_path(bundle_id, "reference.h5ad"),
            "causal_graph": bundled_input_path(bundle_id, "causal_graph.pkl"),
            "model_checkpoint": bundled_input_path(bundle_id, "model_checkpoint.pth"),
            "causal_controller_checkpoint": bundled_input_path(
                bundle_id,
                "causal_controller_checkpoint.pth",
            ),
        }
    return {
        "reference": require_input(request, "groundgan_reference_h5ad"),
        "causal_graph": require_input(request, "groundgan_causal_graph_pickle"),
        "model_checkpoint": require_input(request, "groundgan_model_checkpoint"),
        "causal_controller_checkpoint": require_input(
            request,
            "groundgan_causal_controller_checkpoint",
        ),
    }


def read_reference_genes(ad: Any, reference_path: Path) -> list[str]:
    reference = ad.read_h5ad(reference_path)
    genes = [str(gene).strip() for gene in list(reference.var_names)]
    if not genes or any(not gene for gene in genes):
        raise ValueError("groundgan_reference_h5ad must contain non-empty var_names.")
    if len(set(genes)) != len(genes):
        raise ValueError("groundgan_reference_h5ad var_names must be unique.")
    return genes


def load_causal_graph(path: Path, genes: list[str]) -> dict[int, set[int]]:
    with path.open("rb") as handle:
        raw = pickle.load(handle)
    if not isinstance(raw, dict) or not raw:
        raise ValueError("groundgan_causal_graph_pickle must contain a non-empty dict.")
    graph: dict[int, set[int]] = {}
    gene_count = len(genes)
    for raw_target, raw_regs in raw.items():
        target = as_int(raw_target, "causal_graph target")
        if target < 0 or target >= gene_count:
            raise ValueError(f"causal graph target index out of range: {target}")
        if not isinstance(raw_regs, (set, list, tuple)):
            raise ValueError(f"causal graph regulators for target {target} must be a collection.")
        regs: set[int] = set()
        for raw_reg in raw_regs:
            reg = as_int(raw_reg, f"causal_graph[{target}] regulator")
            if reg < 0 or reg >= gene_count:
                raise ValueError(f"causal graph regulator index out of range: {reg}")
            if reg == target:
                raise ValueError(f"causal graph contains self-loop at gene index {target}")
            regs.add(reg)
        if not regs:
            raise ValueError(f"causal graph target {target} has no regulators.")
        graph[target] = regs
    return graph


def graph_to_edges(graph: dict[int, set[int]], genes: list[str]) -> list[Edge]:
    edges = [
        Edge(source=genes[reg], target=genes[target])
        for target in sorted(graph)
        for reg in sorted(graph[target])
    ]
    if not edges:
        raise ValueError("causal graph contains no edges.")
    return edges


def _load_checkpoint(torch: Any, path: Path, label: str) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu")
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a torch checkpoint dictionary.")
    return payload


def _state_dict(payload: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    state = payload.get(key)
    if not isinstance(state, dict) or not state:
        raise ValueError(f"{label} is missing non-empty {key}.")
    return state


def _layer_weight_shapes(state: dict[str, Any], prefix: str) -> list[tuple[int, tuple[int, int]]]:
    shapes: list[tuple[int, tuple[int, int]]] = []
    marker = f"{prefix}."
    for key, tensor in state.items():
        if not isinstance(key, str) or not key.startswith(marker) or not key.endswith(".0.weight"):
            continue
        parts = key.split(".")
        try:
            index = int(parts[len(prefix.split("."))])
        except (IndexError, ValueError):
            continue
        shape = getattr(tensor, "shape", None)
        if shape is None or len(shape) != 2:
            continue
        shapes.append((index, (int(shape[0]), int(shape[1]))))
    return sorted(shapes)


def _infer_mlp_layers(
    state: dict[str, Any],
    *,
    prefix: str,
    expected_output: int,
    label: str,
) -> tuple[int, list[int]]:
    shapes = _layer_weight_shapes(state, prefix)
    if len(shapes) < 1:
        raise ValueError(f"Cannot infer {label}: no linear layers found.")
    final_output = shapes[-1][1][0]
    if final_output != expected_output:
        raise ValueError(
            f"Cannot infer {label}: final output dimension {final_output} does not "
            f"match expected {expected_output}."
        )
    latent_dim = shapes[0][1][1]
    hidden_layers = [shape[0] for _index, shape in shapes[:-1]]
    return latent_dim, hidden_layers


def _infer_causal_generator_architecture(
    state: dict[str, Any],
    graph: dict[int, set[int]],
) -> tuple[int, int, int]:
    mask_shapes: list[tuple[int, tuple[int, int]]] = []
    for key, tensor in state.items():
        if not isinstance(key, str) or not key.startswith("_generator.") or not key.endswith(".0.mask"):
            continue
        parts = key.split(".")
        try:
            index = int(parts[1])
        except (IndexError, ValueError):
            continue
        shape = getattr(tensor, "shape", None)
        if shape is None or len(shape) != 2:
            continue
        mask_shapes.append((index, (int(shape[0]), int(shape[1]))))
    mask_shapes = sorted(mask_shapes)
    if len(mask_shapes) < 2:
        raise ValueError("Cannot infer causal generator architecture: expected at least input and output masks.")

    target_count = len(graph)
    regulators = sorted({reg for regs in graph.values() for reg in regs})
    tf_count = len(regulators)
    input_features = mask_shapes[0][1][1]
    hidden_dim = mask_shapes[0][1][0]
    output_features = mask_shapes[-1][1][0]
    if output_features != target_count:
        raise ValueError(
            "Cannot infer causal generator architecture: output mask dimension "
            f"{output_features} does not match causal graph target count {target_count}."
        )
    if input_features <= tf_count or (input_features - tf_count) % target_count != 0:
        raise ValueError(
            "Cannot infer noise_per_gene from causal generator input mask and causal graph."
        )
    noise_per_gene = (input_features - tf_count) // target_count
    expected_hidden_base = sum(len(regs) + noise_per_gene for regs in graph.values())
    if expected_hidden_base <= 0 or hidden_dim % expected_hidden_base != 0:
        raise ValueError(
            "Cannot infer width_per_gene from causal generator hidden mask and causal graph."
        )
    width_per_gene = hidden_dim // expected_hidden_base
    for index, shape in mask_shapes[1:-1]:
        if shape != (hidden_dim, hidden_dim):
            raise ValueError(
                f"Cannot infer causal generator architecture: hidden mask {index} "
                f"has shape {shape}, expected {(hidden_dim, hidden_dim)}."
            )
    if mask_shapes[-1][1][1] != hidden_dim:
        raise ValueError("Cannot infer causal generator architecture: output mask width mismatch.")
    depth_per_gene = len(mask_shapes) - 2
    return noise_per_gene, depth_per_gene, width_per_gene


def _infer_labeler_layers(state: dict[str, Any]) -> list[int]:
    shapes = _layer_weight_shapes(state, "_labeler")
    return [shape[0] for _index, shape in shapes[:-1]] or list(DEFAULT_PARAMS["model"]["labeler_layers"])


def infer_checkpoint_architecture(
    *,
    torch: Any,
    model_checkpoint: Path,
    causal_controller_checkpoint: Path,
    graph: dict[int, set[int]],
    genes: list[str],
) -> dict[str, Any]:
    model_payload = _load_checkpoint(torch, model_checkpoint, "groundgan_model_checkpoint")
    cc_payload = _load_checkpoint(
        torch,
        causal_controller_checkpoint,
        "groundgan_causal_controller_checkpoint",
    )
    model_generator = _state_dict(
        model_payload,
        "generator_state_dict",
        "groundgan_model_checkpoint",
    )
    model_critic = _state_dict(
        model_payload,
        "critic_state_dict",
        "groundgan_model_checkpoint",
    )
    cc_generator = _state_dict(
        cc_payload,
        "generator_state_dict",
        "groundgan_causal_controller_checkpoint",
    )

    cc_latent_dim, cc_generator_layers = _infer_mlp_layers(
        cc_generator,
        prefix="_generator",
        expected_output=len(genes),
        label="causal-controller generator layers",
    )
    if any(key.startswith("_causal_controller._generator.") for key in model_generator):
        model_cc_latent_dim, model_cc_layers = _infer_mlp_layers(
            model_generator,
            prefix="_causal_controller._generator",
            expected_output=len(genes),
            label="embedded causal-controller generator layers",
        )
        if model_cc_latent_dim != cc_latent_dim or model_cc_layers != cc_generator_layers:
            raise ValueError(
                "groundgan_model_checkpoint and groundgan_causal_controller_checkpoint "
                "encode different causal-controller architectures."
            )

    _critic_latent, critic_layers = _infer_mlp_layers(
        model_critic,
        prefix="_critic",
        expected_output=1,
        label="critic layers",
    )
    first_critic = _layer_weight_shapes(model_critic, "_critic")[0][1][1]
    if first_critic != len(genes):
        raise ValueError(
            f"groundgan_model_checkpoint critic input dimension {first_critic} does not "
            f"match reference gene count {len(genes)}."
        )
    noise_per_gene, depth_per_gene, width_per_gene = _infer_causal_generator_architecture(
        model_generator,
        graph,
    )
    return {
        "model": {
            "latent_dim": cc_latent_dim,
            "noise_per_gene": noise_per_gene,
            "depth_per_gene": depth_per_gene,
            "width_per_gene": width_per_gene,
            "critic_layers": critic_layers,
            "labeler_layers": _infer_labeler_layers(model_generator),
        },
        "causal_controller": {
            "latent_dim": cc_latent_dim,
            "generator_layers": cc_generator_layers,
            "critic_layers": list(DEFAULT_PARAMS["causal_controller"]["critic_layers"]),
            "checkpoint_step": 0,
        },
    }


def resolve_perturbation_targets(params: dict[str, Any], graph: dict[int, set[int]], genes: list[str]) -> list[str]:
    perturb = params["perturbation"]
    if perturb["target_source"] == "explicit_list":
        targets = list(perturb["tf_targets"])
    else:
        regulators = sorted({reg for regs in graph.values() for reg in regs})
        if not regulators:
            raise ValueError("causal graph has no regulators to perturb.")
        targets = [genes[regulators[0]]]
    gene_set = set(genes)
    regulator_set = {genes[reg] for regs in graph.values() for reg in regs}
    unknown = sorted(set(targets).difference(gene_set))
    if unknown:
        raise ValueError("perturbation targets are not present in reference genes: " + ", ".join(unknown))
    non_regulators = sorted(set(targets).difference(regulator_set))
    if non_regulators:
        raise ValueError("perturbation targets must be regulators in the causal graph: " + ", ".join(non_regulators))
    values = params["perturbation"]["perturbation_values"]
    if len(values) not in {1, len(targets)}:
        raise ValueError("perturbation.perturbation_values must contain one value or one value per target.")
    return targets


def build_upstream_config(
    *,
    params: dict[str, Any],
    genes: list[str],
    causal_graph_path: Path,
    model_checkpoint: Path,
    output_stem: Path,
) -> ConfigParser:
    cfg = ConfigParser()
    cfg["Data"] = {
        "number of genes": str(len(genes)),
        "causal graph": str(causal_graph_path),
        "train": "",
        "validation": "",
        "test": "",
        "number of classes": "1",
        "label ratios": "1.0",
    }
    cfg["Preprocessing"] = {
        "library size": str(params["preprocessing"]["library_size"]),
        "test set size": str(params["generation"]["num_cells"]),
    }
    cfg["Training"] = {
        "batch size": str(params["training"]["batch_size"]),
        "critic iterations": "1",
        "maximum steps": "0",
        "labeler and antilabeler training intervals": "1",
    }
    cfg["CC Training"] = {
        "batch size": str(params["training"]["batch_size"]),
        "critic iterations": "1",
        "maximum steps": str(params["causal_controller"]["checkpoint_step"]),
    }
    cfg["Model"] = {
        "type": "causal GAN",
        "latent dim": str(params["model"]["latent_dim"]),
        "noise per gene": str(params["model"]["noise_per_gene"]),
        "depth per gene": str(params["model"]["depth_per_gene"]),
        "width per gene": str(params["model"]["width_per_gene"]),
        "critic layers": " ".join(str(item) for item in params["model"]["critic_layers"]),
        "labeler layers": " ".join(str(item) for item in params["model"]["labeler_layers"]),
        "lambda": "10.0",
    }
    cfg["CC Model"] = {
        "latent dim": str(params["causal_controller"]["latent_dim"]),
        "generator layers": " ".join(str(item) for item in params["causal_controller"]["generator_layers"]),
        "critic layers": " ".join(str(item) for item in params["causal_controller"]["critic_layers"]),
        "lambda": "10.0",
    }
    cfg["Optimizer"] = {"beta1": "0.5", "beta2": "0.9"}
    cfg["CC Optimizer"] = {"beta1": "0.5", "beta2": "0.9"}
    cfg["Learning Rate"] = {
        "generator initial": "0.0001",
        "generator final": "0.0001",
        "critic initial": "0.0001",
        "critic final": "0.0001",
        "labeler": "0.0001",
        "antilabeler": "0.0001",
    }
    cfg["CC Learning Rate"] = {
        "generator initial": "0.0001",
        "generator final": "0.0001",
        "critic initial": "0.0001",
        "critic final": "0.0001",
    }
    cfg["Logging"] = {"summary frequency": "0", "plot frequency": "0", "save frequency": "0"}
    cfg["CC Logging"] = {"summary frequency": "0", "plot frequency": "0", "save frequency": "0"}
    cfg["EXPERIMENT"] = {
        "device": "cpu",
        "checkpoint": str(model_checkpoint),
        "output directory": str(output_stem),
    }
    cfg["Perturbation"] = {
        "tfs to perturb": " ".join(params["perturbation"]["tf_targets"]),
        "perturbation values": " ".join(str(item) for item in params["perturbation"]["perturbation_values"]),
        "save dir": str(output_stem.parent) + "/",
    }
    return cfg


def write_config(path: Path, cfg: ConfigParser) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        cfg.write(handle)


def instantiate_gan(get_factory: Any, cfg: ConfigParser, checkpoint: Path) -> Any:
    gan = get_factory(cfg).get_gan()
    gan.generate_cells(2, checkpoint)
    return gan


def generate_observational(gan: Any, cells_no: int, checkpoint: Path, np: Any) -> Any:
    matrix = gan.generate_cells(cells_no, checkpoint)
    return np.asarray(matrix, dtype=float)


def require_generated_shape(matrix: Any, *, genes: list[str], label: str) -> None:
    shape = getattr(matrix, "shape", None)
    if not isinstance(shape, tuple) or len(shape) != 2:
        raise ValueError(f"{label} must be a two-dimensional generated matrix.")
    if int(shape[1]) != len(genes):
        raise ValueError(
            f"{label} gene dimension does not match groundgan_reference_h5ad.var_names: "
            f"matrix has {int(shape[1])} genes, reference has {len(genes)} genes. "
            "Use a reference, causal graph and checkpoint bundle trained for the same gene universe."
        )


def generate_perturbational(
    gan: Any,
    *,
    cells_no: int,
    checkpoint: Path,
    genes: list[str],
    targets: list[str],
    values: list[float],
    torch: Any,
    np: Any,
) -> tuple[Any, Any, list[tuple[str, float]]]:
    control = gan.generate_cells(cells_no, checkpoint)
    gan.gen.tf_expressions = None
    gan.gen.noise = None
    gan.gen.pert_mode = True
    control = gan.generate_cells(cells_no, checkpoint)

    native_target_indices = [genes.index(target) for target in targets]
    tf_positions = [gan.gen.tfs.index(native_idx) for native_idx in native_target_indices]
    value_by_target = values if len(values) == len(targets) else [values[0] for _ in targets]

    unperturbed_tfs = gan.gen.tf_expressions.clone()
    pert_tensor = torch.tensor(
        value_by_target,
        device=gan.gen.tf_expressions.device,
        dtype=gan.gen.tf_expressions.dtype,
    )
    gan.gen.tf_expressions[:, tf_positions] = pert_tensor.unsqueeze(0)
    perturbed = gan.generate_cells(cells_no, checkpoint)
    gan.gen.tf_expressions = unperturbed_tfs
    gan.gen.pert_mode = False
    return np.asarray(control, dtype=float), np.asarray(perturbed, dtype=float), list(zip(targets, value_by_target))


def generated_data_from_matrix(genes: list[str], matrix: Any, prefix: str = "cell") -> GeneratedData:
    require_generated_shape(matrix, genes=genes, label="generated matrix")
    columns = [f"{prefix}_{index:03d}" for index in range(1, matrix.shape[0] + 1)]
    by_gene = matrix.T.tolist()
    return GeneratedData(genes=genes, columns=columns, matrix_by_gene=by_gene)


def generated_data_from_perturbation(genes: list[str], control: Any, perturbed: Any) -> GeneratedData:
    require_generated_shape(control, genes=genes, label="control perturbation matrix")
    require_generated_shape(perturbed, genes=genes, label="perturbed perturbation matrix")
    if int(control.shape[0]) != int(perturbed.shape[0]):
        raise ValueError(
            "control and perturbed matrices must contain the same number of generated cells."
        )
    columns: list[str] = []
    combined_rows: list[Any] = []
    for index in range(control.shape[0]):
        pair_id = f"pair_{index + 1:03d}"
        columns.append(f"{pair_id}_control")
        combined_rows.append(control[index, :])
        columns.append(f"{pair_id}_perturbed")
        combined_rows.append(perturbed[index, :])
    import numpy as np  # noqa: PLC0415

    matrix = np.vstack(combined_rows)
    return GeneratedData(genes=genes, columns=columns, matrix_by_gene=matrix.T.tolist())


def write_expression(path: Path, data: GeneratedData) -> None:
    if len(data.genes) != len(data.matrix_by_gene):
        raise ValueError(
            "expression matrix row count does not match the normalized gene universe."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["gene", *data.columns])
        for gene, values in zip(data.genes, data.matrix_by_gene):
            writer.writerow([gene, *[f"{float(value):.10g}" for value in values]])


def write_gene_list(path: Path, genes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(genes) + "\n", encoding="utf-8")


def write_truth_networks(path: Path, edges: list[Edge]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source", "target", "score", "sign", "evidence", "context"],
        )
        writer.writeheader()
        for edge in edges:
            writer.writerow(
                {
                    "source": edge.source,
                    "target": edge.target,
                    "score": f"{edge.score:.10g}",
                    "sign": edge.sign,
                    "evidence": "simulated_truth",
                    "context": "global",
                }
            )


def write_prior_grn(path: Path, edges: list[Edge]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "target", "score"], delimiter="\t")
        writer.writeheader()
        for edge in edges:
            writer.writerow({"source": edge.source, "target": edge.target, "score": f"{edge.score:.10g}"})


def write_perturbation_extras(
    *,
    output_dir: Path,
    columns: list[str],
    perturbations: list[tuple[str, float]],
) -> dict[str, str]:
    if not perturbations:
        raise ValueError("At least one perturbation is required for perturbational runs.")
    target, value = perturbations[0]
    effect = "knockout" if value == 0.0 else "set_expression"
    sign = -1 if value == 0.0 else 1
    intervention = f"{effect}_{target}"
    extras: dict[str, str] = {}

    design_path = output_dir / "extras" / "perturbation_design.tsv"
    design_path.parent.mkdir(parents=True, exist_ok=True)
    with design_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "column",
                "condition",
                "perturbation",
                "target",
                "dose",
                "timepoint",
                "replicate",
                "control",
                "matched_pair_id",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        for column in columns:
            pair_id = column.rsplit("_", 1)[0]
            is_control = column.endswith("_control")
            writer.writerow(
                {
                    "column": column,
                    "condition": "control" if is_control else intervention,
                    "perturbation": "none" if is_control else effect,
                    "target": "" if is_control else target,
                    "dose": "0" if is_control else f"{value:.10g}",
                    "timepoint": "0",
                    "replicate": pair_id,
                    "control": "true" if is_control else "false",
                    "matched_pair_id": pair_id,
                }
            )
    extras["perturbation_design"] = "extras/perturbation_design.tsv"

    interventions_path = output_dir / "extras" / "interventions.tsv"
    with interventions_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["intervention", "target", "effect", "sign", "dose", "timepoint"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow(
            {
                "intervention": intervention,
                "target": target,
                "effect": effect,
                "sign": str(sign),
                "dose": f"{value:.10g}",
                "timepoint": "0",
            }
        )
    extras["interventions"] = "extras/interventions.tsv"
    return extras


def write_requested_extras(
    *,
    output_dir: Path,
    requested: set[str],
    genes: list[str],
    edges: list[Edge],
    columns: list[str],
    perturbations: list[tuple[str, float]] | None,
) -> dict[str, str | None]:
    extras: dict[str, str | None] = {key: None for key in EXTRA_KEYS}
    if "enrichment_background" in requested:
        write_gene_list(output_dir / "extras" / "enrichment_background.txt", genes)
        extras["enrichment_background"] = "extras/enrichment_background.txt"
    if "prior_grn" in requested:
        write_prior_grn(output_dir / "extras" / "prior_grn.tsv", edges)
        extras["prior_grn"] = "extras/prior_grn.tsv"
    regulators = sorted({edge.source for edge in edges})
    write_gene_list(output_dir / "extras" / "tf_list.txt", regulators)
    extras["tf_list"] = "extras/tf_list.txt"
    if {"perturbation_design", "interventions"}.intersection(requested):
        if perturbations is None:
            raise ValueError("Perturbation extras were requested for a non-perturbational run.")
        extras.update(write_perturbation_extras(output_dir=output_dir, columns=columns, perturbations=perturbations))
    unsupported = sorted(requested.difference({key for key, value in extras.items() if value is not None}).difference(
        {"enrichment_background", "prior_grn", "tf_list", "perturbation_design", "interventions"}
    ))
    if unsupported:
        raise ValueError("Unsupported standardized extras requested for GRouNdGAN: " + ", ".join(unsupported))
    return extras


def copy_native_outputs(
    *,
    output_dir: Path,
    requested: set[str],
    raw_paths: dict[str, Path],
) -> dict[str, str]:
    mapping = {
        "simulated_h5ad": "simulated.h5ad",
        "before_perturbation_h5ad": "before_perturbation.h5ad",
        "after_perturbation_h5ad": "after_perturbation.h5ad",
        "causal_graph_pickle": "causal_graph.pkl",
    }
    native_outputs: dict[str, str] = {}
    native_dir = output_dir / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    for native_id in sorted(requested):
        source = raw_paths.get(native_id)
        if native_id not in mapping or source is None or not source.exists():
            raise ValueError(f"Requested native output is unavailable for this run: {native_id}")
        dest = native_dir / mapping[native_id]
        shutil.copy2(source, dest)
        native_outputs[native_id] = f"native/{dest.name}"
    return native_outputs


def write_raw_h5ad(ad: Any, pd: Any, path: Path, matrix: Any, genes: list[str], obs_names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    adata = ad.AnnData(
        matrix,
        obs=pd.DataFrame(index=obs_names),
        var=pd.DataFrame(index=genes),
    )
    adata.write_h5ad(path)


def write_manifest(
    *,
    output_dir: Path,
    request: dict[str, Any],
    data: GeneratedData,
    extras: dict[str, str | None],
    native_outputs: dict[str, str],
) -> None:
    manifest = {
        "schema_version": "1.0",
        "simulator_id": "groundgan",
        "data_axes": request["data_axes"],
        "truth_requirements": request["truth_requirements"],
        "seed": int(request.get("seed", 1)),
        "expression": {
            "path": "expression.tsv",
            "genes": len(data.genes),
            "columns": len(data.columns),
            "column_kind": request["data_axes"]["column_kind"],
        },
        "extras": {key: extras.get(key) for key in EXTRA_KEYS},
        "native_outputs": native_outputs,
        "truth": {
            "gene_universe": "truth/gene_universe.txt",
            "networks": "truth/networks.csv",
        },
        "provenance": {
            "raw_dir": "provenance/raw",
            "notes": "GRouNdGAN causal GAN instantiated through the pinned upstream Python API; normalized truth is the imposed unsigned causal graph.",
        },
    }
    write_json(output_dir / "simulator-output-manifest.json", manifest)


def write_session_info(raw_dir: Path, threads: int) -> None:
    packages = {}
    for name in ["torch", "numpy", "pandas", "anndata", "scanpy", "scipy", "scikit-learn"]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    payload = {
        "timestamp_utc": utc_now(),
        "python": sys.version,
        "platform": platform.platform(),
        "groundgan_src": str(GROUNDGAN_SRC),
        "groundgan_commit": GROUNDGAN_COMMIT,
        "threads": threads,
        "thread_env": {
            key: os.environ.get(key)
            for key in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]
        },
        "packages": packages,
    }
    write_json(raw_dir / "session_info.json", payload)


def enforce_request_contract(request: dict[str, Any], params: dict[str, Any]) -> None:
    effective_extras = {
        str(item) for item in request.get("effective_extras", [])
    }
    if "tf_list" not in effective_extras:
        raise ValueError("effective_extras must include required extra tf_list.")
    axes = request.get("data_axes", {})
    truth = request.get("truth_requirements", {})
    if axes.get("measurement") != "rna_expression" or axes.get("resolution") != "single_cell" or axes.get("column_kind") != "cells":
        raise ValueError("GRouNdGAN wrapper only supports single-cell RNA expression with cell columns.")
    if truth.get("contexts") != ["global"]:
        raise ValueError("GRouNdGAN wrapper only supports global truth in the executable contract.")
    expected_command = "perturb" if axes.get("experimental_design") == "perturbational" else "generate"
    if axes.get("experimental_design") not in {"observational", "perturbational"}:
        raise ValueError("GRouNdGAN wrapper only supports observational and perturbational designs.")
    if params["run_command"] != expected_command:
        raise ValueError("run_command is controlled by the selected scenario and must be " + expected_command + ".")


def run(request_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "provenance" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    write_progress(output_dir, "running", "start", "Starting GRouNdGAN wrapper.", percent=1)

    request = json.loads(request_path.read_text(encoding="utf-8"))
    threads = set_runtime_threads(request)
    ad, np, pd, torch, get_factory = import_upstream_modules(threads)
    seed = int(request.get("seed", 1))
    np.random.seed(seed)
    torch.manual_seed(seed)
    params = normalize_params(dict(request.get("params", {})))
    enforce_request_contract(request, params)

    write_json(raw_dir / "request_snapshot.json", request)
    write_session_info(raw_dir, threads)

    write_progress(output_dir, "running", "stage_inputs", "Staging GroundGAN inputs.", percent=10)
    bundle_inputs = resolve_bundle_input_paths(request, params)
    reference_path = copy_input(bundle_inputs["reference"], raw_dir / "input_reference.h5ad")
    graph_path = copy_input(bundle_inputs["causal_graph"], raw_dir / "causal_graph.pkl")
    model_checkpoint = copy_input(bundle_inputs["model_checkpoint"], raw_dir / "model_checkpoint.pth")
    cc_checkpoint_source = bundle_inputs["causal_controller_checkpoint"]

    output_stem = raw_dir / "groundgan_run"
    cc_checkpoint_dir = Path(f"{output_stem}_CC") / "checkpoints"
    cc_checkpoint = copy_input(
        cc_checkpoint_source,
        cc_checkpoint_dir / f"step_{params['causal_controller']['checkpoint_step']}.pth",
    )

    genes = read_reference_genes(ad, reference_path)
    graph = load_causal_graph(graph_path, genes)
    edges = graph_to_edges(graph, genes)
    inferred_architecture = infer_checkpoint_architecture(
        torch=torch,
        model_checkpoint=model_checkpoint,
        causal_controller_checkpoint=cc_checkpoint,
        graph=graph,
        genes=genes,
    )
    params["model"] = inferred_architecture["model"]
    params["causal_controller"] = inferred_architecture["causal_controller"]
    write_json(
        raw_dir / "public_id_maps.json",
        {
            "genes": [{"native_index": index, "public_id": gene} for index, gene in enumerate(genes)],
            "graph_edges": [
                {"source_index": reg, "source": genes[reg], "target_index": target, "target": genes[target]}
                for target in sorted(graph)
                for reg in sorted(graph[target])
            ],
        },
    )

    params["perturbation"]["tf_targets"] = resolve_perturbation_targets(params, graph, genes)
    write_json(raw_dir / "resolved_params.json", params)
    cfg = build_upstream_config(
        params=params,
        genes=genes,
        causal_graph_path=graph_path,
        model_checkpoint=model_checkpoint,
        output_stem=output_stem,
    )
    write_config(raw_dir / "resolved_groundgan.cfg", cfg)
    (raw_dir / "run_log.txt").write_text(
        "Loaded GRouNdGAN via factory.CausalGANFactory and generated cells through CausalGAN.generate_cells().\n"
        f"Causal-controller checkpoint staged at {cc_checkpoint}.\n",
        encoding="utf-8",
    )

    write_progress(output_dir, "running", "simulate", "Executing upstream GRouNdGAN model.", percent=35)
    gan = instantiate_gan(get_factory, cfg, model_checkpoint)
    raw_h5ad_paths: dict[str, Path] = {"causal_graph_pickle": graph_path}
    perturbations: list[tuple[str, float]] | None = None
    if params["run_command"] == "generate":
        matrix = generate_observational(gan, params["generation"]["num_cells"], model_checkpoint, np)
        data = generated_data_from_matrix(genes, matrix)
        simulated_path = raw_dir / "simulated.h5ad"
        write_raw_h5ad(ad, pd, simulated_path, matrix, genes, data.columns)
        raw_h5ad_paths["simulated_h5ad"] = simulated_path
    else:
        control, perturbed, perturbations = generate_perturbational(
            gan,
            cells_no=params["generation"]["num_cells"],
            checkpoint=model_checkpoint,
            genes=genes,
            targets=params["perturbation"]["tf_targets"],
            values=params["perturbation"]["perturbation_values"],
            torch=torch,
            np=np,
        )
        data = generated_data_from_perturbation(genes, control, perturbed)
        before_names = [column for column in data.columns if column.endswith("_control")]
        after_names = [column for column in data.columns if column.endswith("_perturbed")]
        before_path = raw_dir / "before_perturbation.h5ad"
        after_path = raw_dir / "after_perturbation.h5ad"
        write_raw_h5ad(ad, pd, before_path, control, genes, before_names)
        write_raw_h5ad(ad, pd, after_path, perturbed, genes, after_names)
        raw_h5ad_paths["before_perturbation_h5ad"] = before_path
        raw_h5ad_paths["after_perturbation_h5ad"] = after_path

    write_progress(output_dir, "running", "normalize", "Writing normalized outputs.", percent=75)
    write_expression(output_dir / "expression.tsv", data)
    write_truth_networks(output_dir / "truth" / "networks.csv", edges)
    write_gene_list(output_dir / "truth" / "gene_universe.txt", genes)

    extras = write_requested_extras(
        output_dir=output_dir,
        requested={str(item) for item in request.get("effective_extras", [])},
        genes=genes,
        edges=edges,
        columns=data.columns,
        perturbations=perturbations,
    )
    native_outputs = copy_native_outputs(
        output_dir=output_dir,
        requested={str(item) for item in request.get("native_outputs", [])},
        raw_paths=raw_h5ad_paths,
    )
    write_manifest(
        output_dir=output_dir,
        request=request,
        data=data,
        extras=extras,
        native_outputs=native_outputs,
    )
    write_progress(output_dir, "complete", "done", "GRouNdGAN wrapper completed.", percent=100)


def main() -> int:
    request_path = Path("/work/request/simulator-run-request.json")
    output_dir = Path("/work/out")
    try:
        run(request_path, output_dir)
    except Exception as exc:  # noqa: BLE001
        raw_dir = output_dir / "provenance" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "error.log").write_text(
            "".join(traceback.format_exception(exc)),
            encoding="utf-8",
        )
        write_progress(output_dir, "failed", "error", str(exc), percent=100)
        print(f"GRouNdGAN wrapper failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
