#!/usr/bin/env python3
"""ANDREA wrapper for SynTReN.

The wrapper executes the public SynTReN Java CLI and normalizes the generated
microarray-like expression data and generated topology into ANDREA's simulator
output contract.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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

SYN_TREN_HOME = Path(os.environ.get("SYN_TREN_HOME", "/opt/syntren"))
SYN_TREN_JAR = SYN_TREN_HOME / "SynTReN.jar"
PATCHED_LIB = Path(os.environ.get("SYN_TREN_PATCHED_LIB", "/opt/syntren-patched-lib"))
JAVA_BIN = Path(os.environ.get("JAVA_BIN", "java"))
SYN_TREN_VERSION = "1.2"
LOCAL_JAR_SHA256 = "1830a5d10c909b135040181b22470aac84ffb6f1ffc085f91089c92f728b8f5a"
XSTREAM_SHA256 = "7f8039c0ee7284f9c2a9554b5e2bc20bf26b74b37f690633a75ff1993136f364"

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
    "source_network": {"preset": "ecoli_full"},
    "subnetwork": {
        "enabled": True,
        "method": "cluster_addition",
        "num_nodes": 100,
        "num_background_nodes": 100,
    },
    "interactions": {
        "use_edge_types_from_sif": True,
        "percent_activators": 0.2,
        "interaction_category": "SIGMOIDAL",
        "higher_order_probability": 0.0,
    },
    "external_inputs": {
        "mode": "randomized",
        "fixed_source": "builtin_concentration_series",
        "num_externals": -1,
        "num_correlated_externals": 0,
        "correlation_noise": 0.1,
    },
    "sampling": {
        "burn_in": 1000,
        "num_experiments": 10,
        "samples_per_experiment": 1,
    },
    "noise": {
        "biological": 0.1,
        "input": 0.1,
        "experimental": 0.1,
    },
    "expression_variant": "unnormalized",
}

SOURCE_PRESETS = {
    "ecoli_full": "EColi_full.sif",
    "ecoli_hongwu_ma": "EColi_full_HongWu_Ma_NAR2004.sif",
    "yeast_full": "Yeast_full.sif",
    "yeast_neighbor_sample": "Yeast_full_nn.sif",
    "dag1_clean": "DAG1_clean.sif",
}


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    sign: str = "?"
    score: float = 1.0


@dataclass(frozen=True)
class ExpressionTable:
    genes: list[str]
    columns: list[str]
    values: list[list[float]]


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
        return list(base)
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


def as_float(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric.") from exc
    if not (number == number and abs(number) != float("inf")):
        raise ValueError(f"{name} must be finite.")
    if minimum is not None and number < minimum:
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

    source = params["source_network"]
    if source["preset"] not in set(SOURCE_PRESETS).union({"custom_sif"}):
        raise ValueError("source_network.preset is not supported.")

    sub = params["subnetwork"]
    sub["enabled"] = as_bool(sub["enabled"], "subnetwork.enabled")
    if sub["method"] not in {"cluster_addition", "neighbor_addition"}:
        raise ValueError("subnetwork.method must be cluster_addition or neighbor_addition.")
    sub["num_nodes"] = as_int(sub["num_nodes"], "subnetwork.num_nodes", minimum=2)
    sub["num_background_nodes"] = as_int(sub["num_background_nodes"], "subnetwork.num_background_nodes", minimum=0)

    interactions = params["interactions"]
    interactions["use_edge_types_from_sif"] = as_bool(
        interactions["use_edge_types_from_sif"],
        "interactions.use_edge_types_from_sif",
    )
    interactions["percent_activators"] = as_float(
        interactions["percent_activators"],
        "interactions.percent_activators",
        minimum=0.0,
        maximum=1.0,
    )
    if interactions["interaction_category"] not in {
        "LINEARLIKE",
        "SIGMOIDAL",
        "STEP",
        "STEEP",
        "LINEAR",
        "MIXED",
        "DEFAULT",
        "RANDOM",
    }:
        raise ValueError("interactions.interaction_category is not supported.")
    interactions["higher_order_probability"] = as_float(
        interactions["higher_order_probability"],
        "interactions.higher_order_probability",
        minimum=0.0,
        maximum=1.0,
    )

    external = params["external_inputs"]
    if external["mode"] not in {"randomized", "from_file", "fixed"}:
        raise ValueError("external_inputs.mode is not supported.")
    if external["fixed_source"] not in {"builtin_concentration_series", "custom_table"}:
        raise ValueError("external_inputs.fixed_source is not supported.")
    external["num_externals"] = as_int(external["num_externals"], "external_inputs.num_externals", minimum=-1)
    external["num_correlated_externals"] = as_int(
        external["num_correlated_externals"],
        "external_inputs.num_correlated_externals",
        minimum=-1,
    )
    if external["num_externals"] >= 0 and external["num_correlated_externals"] >= external["num_externals"]:
        raise ValueError("external_inputs.num_correlated_externals must be smaller than num_externals.")
    external["correlation_noise"] = as_float(external["correlation_noise"], "external_inputs.correlation_noise", minimum=0.0)

    sampling = params["sampling"]
    sampling["burn_in"] = as_int(sampling["burn_in"], "sampling.burn_in", minimum=0)
    sampling["num_experiments"] = as_int(sampling["num_experiments"], "sampling.num_experiments", minimum=1)
    sampling["samples_per_experiment"] = as_int(
        sampling["samples_per_experiment"],
        "sampling.samples_per_experiment",
        minimum=1,
    )

    noise = params["noise"]
    noise["biological"] = as_float(noise["biological"], "noise.biological", minimum=0.0)
    noise["input"] = as_float(noise["input"], "noise.input", minimum=0.0)
    noise["experimental"] = as_float(noise["experimental"], "noise.experimental", minimum=0.0)

    if params["expression_variant"] not in {"unnormalized", "max_expr_1"}:
        raise ValueError("expression_variant must be unnormalized or max_expr_1.")
    return params


def enforce_semantic_bindings(params: dict[str, Any], axes: dict[str, Any]) -> None:
    design = axes.get("experimental_design")
    column_kind = axes.get("column_kind")
    if axes.get("measurement") != "rna_expression" or axes.get("resolution") != "bulk":
        raise ValueError("SynTReN only supports bulk RNA expression capabilities.")
    if design == "observational" and column_kind == "samples":
        if params["external_inputs"]["mode"] != "randomized":
            raise ValueError("external_inputs.mode is controlled by the selected scenario and must be randomized.")
        return
    if design == "perturbational" and column_kind == "perturbations":
        if params["external_inputs"]["mode"] != "from_file":
            raise ValueError("external_inputs.mode is controlled by the selected scenario and must be from_file.")
        if params["subnetwork"]["enabled"]:
            raise ValueError("subnetwork.enabled is controlled by the selected scenario and must be false for SynTReN perturbational runs.")
        return
    raise ValueError("Requested data axes are not supported by SynTReN.")


def classpath() -> str:
    entries = [
        PATCHED_LIB / "xstream-1.4.7.jar",
        PATCHED_LIB / "xmlpull-1.1.3.1.jar",
        PATCHED_LIB / "xpp3_min-1.1.4c.jar",
        SYN_TREN_JAR,
        SYN_TREN_HOME / "lib" / "cglib-nodep-2.1_3.jar",
        SYN_TREN_HOME / "lib" / "colt.jar",
        SYN_TREN_HOME / "lib" / "commons-math-1.1.jar",
        SYN_TREN_HOME / "lib" / "xercesImpl.jar",
        SYN_TREN_HOME / "lib" / "xom-1.0a5.jar",
        Path("/app"),
    ]
    missing = [str(path) for path in entries if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing SynTReN runtime classpath entries: " + ", ".join(missing))
    return ":".join(str(path) for path in entries)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_source_sif(params: dict[str, Any], mounted_inputs: dict[str, str], raw_dir: Path) -> Path:
    preset = params["source_network"]["preset"]
    if preset == "custom_sif":
        raw_path = mounted_inputs.get("syntren_source_network_sif")
        if not raw_path:
            raise ValueError("syntren_source_network_sif is required when source_network.preset=custom_sif.")
        source = Path(raw_path)
    else:
        source = SYN_TREN_HOME / "data" / "sourceNetworks" / SOURCE_PRESETS[preset]
    if not source.exists():
        raise FileNotFoundError(f"SynTReN source network not found: {source}")
    dest = raw_dir / "input_source_network.sif"
    shutil.copy2(source, dest)
    return dest


def resolve_externals(params: dict[str, Any], mounted_inputs: dict[str, str], raw_dir: Path) -> Path:
    external = params["external_inputs"]
    if external["fixed_source"] == "custom_table":
        raw_path = mounted_inputs.get("syntren_externals_table")
        if not raw_path:
            raise ValueError("syntren_externals_table is required when external_inputs.fixed_source=custom_table.")
        source = Path(raw_path)
    else:
        source = SYN_TREN_HOME / "data" / "samples" / "externalsFile.txt"
    if not source.exists():
        raise FileNotFoundError(f"SynTReN externals table not found: {source}")
    dest = raw_dir / "externalsFile.txt"
    shutil.copy2(source, dest)
    return dest


def syntren_bool(value: bool) -> str:
    return "true" if value else "false"


def write_ini(
    path: Path,
    *,
    params: dict[str, Any],
    seed: int,
    source_sif: Path,
    externals_file: Path,
    raw_output: Path,
    xml_file: Path,
) -> None:
    sub = params["subnetwork"]
    interactions = params["interactions"]
    external = params["external_inputs"]
    sampling = params["sampling"]
    noise = params["noise"]
    subnetwork_method = {
        "cluster_addition": "clusterAddition",
        "neighbor_addition": "neighborAddition",
    }[sub["method"]]
    external_mode = {
        "randomized": "RANDOMIZED",
        "from_file": "FROM_EXTERNALS_FILE",
        "fixed": "FIXED",
    }[external["mode"]]
    lines = [
        "createGeneNetwork=true",
        f"selectSubnetwork={syntren_bool(sub['enabled'])}",
        f"fixedExternals={syntren_bool(external['mode'] == 'from_file')}",
        "generateExpressionData=true",
        f"randomSeed={seed}",
        f"subnetworkSelection={subnetwork_method}",
        f"nrNodes={sub['num_nodes']}",
        f"nrBackgroundNodes={sub['num_background_nodes']}",
        f"useEdgeTypesFromSIF={syntren_bool(interactions['use_edge_types_from_sif'])}",
        f"percentActivators={interactions['percent_activators']}",
        f"interactionCategory={interactions['interaction_category']}",
        f"nrExternals={external['num_externals']}",
        f"nrCorrelatedExternals={external['num_correlated_externals']}",
        f"correlationNoise={external['correlation_noise']}",
        f"higherOrderProbability={interactions['higher_order_probability']}",
        f"externalInputValues={external_mode}",
        f"bioNoise={noise['biological']}",
        f"inputNoise={noise['input']}",
        f"expNoise={noise['experimental']}",
        f"burnIn={sampling['burn_in']}",
        f"nrExperiments={sampling['num_experiments']}",
        f"nrSamplesPerExp={sampling['samples_per_experiment']}",
        f"NetworkSIFFile={source_sif}",
        f"externalsFile={externals_file}",
        f"outputdir={raw_output}",
        f"GeneNetworkXMLFile={xml_file}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_java_command(cmd: list[str], raw_dir: Path, *, stdout_name: str, stderr_name: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=raw_dir, text=True, capture_output=True, check=False)
    (raw_dir / stdout_name).write_text(result.stdout or "", encoding="utf-8")
    (raw_dir / stderr_name).write_text(result.stderr or "", encoding="utf-8")
    return result


def run_syntren(ini_path: Path, raw_dir: Path) -> None:
    cmd = [
        str(JAVA_BIN),
        "-Xmx1024m",
        "-cp",
        classpath(),
        "islab.bayesian.genenetwork.generation.NetworkGeneratorCLI",
        str(ini_path),
    ]
    write_json(raw_dir / "syntren_command.json", cmd)
    result = run_java_command(cmd, raw_dir, stdout_name="upstream_stdout.log", stderr_name="upstream_stderr.log")
    if result.returncode != 0:
        raise RuntimeError(
            f"SynTReN CLI failed with exit code {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()[:2000]}"
        )


def export_sif(xml_path: Path, sif_path: Path, raw_dir: Path) -> None:
    cmd = [
        str(JAVA_BIN),
        "-cp",
        classpath(),
        "SyntrenExportSif",
        str(xml_path),
        str(sif_path),
    ]
    result = run_java_command(cmd, raw_dir, stdout_name="export_sif_stdout.log", stderr_name="export_sif_stderr.log")
    if result.returncode != 0:
        raise RuntimeError(
            f"SynTReN SIF export failed with exit code {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()[:2000]}"
        )
    if not sif_path.exists() or sif_path.stat().st_size == 0:
        raise RuntimeError("SynTReN SIF export produced no network edges.")


def find_expression_file(raw_output: Path, variant: str) -> Path:
    suffixes = {
        "unnormalized": ["_unnormalized_dataset.txt"],
        "max_expr_1": ["_maxExpr1_dataset.txt", "_normalized_dataset.txt"],
    }[variant]
    matches: list[Path] = []
    for suffix in suffixes:
        matches.extend(sorted(raw_output.glob(f"*{suffix}")))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one SynTReN {variant} dataset, found {len(matches)}.")
    return matches[0]


def read_expression(path: Path) -> ExpressionTable:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if not header or len(header) < 2 or header[0].strip().upper() != "GENE":
            raise ValueError(f"SynTReN dataset has invalid header: {path}")
        columns = [clean_id(value, "column") for value in header[1:]]
        genes: list[str] = []
        values: list[list[float]] = []
        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(f"SynTReN dataset has inconsistent width at line {line_no}.")
            gene = clean_id(row[0], "gene")
            genes.append(gene)
            try:
                values.append([float(value) for value in row[1:]])
            except ValueError as exc:
                raise ValueError(f"SynTReN dataset has non-numeric value at line {line_no}.") from exc
    if not genes or not columns:
        raise ValueError("SynTReN dataset is empty.")
    if len(set(genes)) != len(genes):
        raise ValueError("SynTReN dataset contains duplicate gene identifiers.")
    if len(set(columns)) != len(columns):
        raise ValueError("SynTReN dataset contains duplicate column identifiers.")
    return ExpressionTable(genes=genes, columns=columns, values=values)


def clean_id(value: Any, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"Empty {label} identifier.")
    return text


def write_expression(path: Path, table: ExpressionTable) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["gene", *table.columns])
        for gene, row in zip(table.genes, table.values, strict=True):
            writer.writerow([gene, *[f"{value:.12g}" for value in row]])


def parse_sif_edges(path: Path, gene_universe: set[str]) -> list[Edge]:
    edges: list[Edge] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                raise ValueError(f"Generated SIF has invalid row at line {line_no}: {line}")
            source, token, target = parts[0], parts[1].lower(), parts[2]
            if source == target:
                continue
            if source not in gene_universe or target not in gene_universe:
                continue
            sign = "+" if token == "ac" else "-" if token == "re" else "?"
            edges.append(Edge(source=source, target=target, sign=sign))
    unique = sorted({(edge.source, edge.target, edge.sign): edge for edge in edges}.values(), key=lambda e: (e.source, e.target, e.sign))
    if not unique:
        raise ValueError("Generated SynTReN SIF contains no usable non-self edges within expression genes.")
    return unique


def write_truth_networks(path: Path, edges: list[Edge]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target", "score", "sign", "evidence", "context"])
        for edge in edges:
            writer.writerow([edge.source, edge.target, f"{edge.score:.12g}", edge.sign, "simulated_truth", "global"])


def write_gene_universe(path: Path, genes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{gene}\n" for gene in genes), encoding="utf-8")


def write_enrichment_background(path: Path, genes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{gene}\n" for gene in genes), encoding="utf-8")


def write_prior_grn(path: Path, edges: list[Edge]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["source", "target", "score"])
        for edge in edges:
            signed_score = edge.score if edge.sign == "+" else -edge.score if edge.sign == "-" else edge.score
            writer.writerow([edge.source, edge.target, f"{signed_score:.12g}"])


def write_tf_list(path: Path, edges: list[Edge]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    regulators = sorted({edge.source for edge in edges})
    path.write_text("".join(f"{gene}\n" for gene in regulators), encoding="utf-8")


def read_externals_table(path: Path) -> tuple[list[str], dict[str, list[float]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if not header or len(header) < 2 or header[0].strip() != "Regulators":
            raise ValueError("SynTReN externals table must have Regulators plus at least one condition column.")
        columns = [clean_id(value, "external condition") for value in header[1:]]
        values: dict[str, list[float]] = {}
        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(f"SynTReN externals table has inconsistent width at line {line_no}.")
            regulator = clean_id(row[0], "external regulator")
            try:
                values[regulator] = [float(value) for value in row[1:]]
            except ValueError as exc:
                raise ValueError(f"SynTReN externals table has non-numeric value at line {line_no}.") from exc
    if not values:
        raise ValueError("SynTReN externals table has no regulator rows.")
    return columns, values


def write_perturbation_extras(
    *,
    design_path: Path,
    interventions_path: Path,
    expression_columns: list[str],
    expression_genes: set[str],
    externals_file: Path,
) -> None:
    external_columns, external_values = read_externals_table(externals_file)
    if len(external_columns) != len(expression_columns):
        raise ValueError(
            "SynTReN externals table column count does not match expression columns: "
            f"{len(external_columns)} != {len(expression_columns)}"
        )
    baseline = {regulator: values[0] for regulator, values in external_values.items()}
    design_path.parent.mkdir(parents=True, exist_ok=True)
    with design_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["column", "condition", "perturbation", "target", "dose", "timepoint", "replicate", "control"])
        for idx, column in enumerate(expression_columns):
            delta = sum(abs(values[idx] - baseline[regulator]) for regulator, values in external_values.items())
            control = idx == 0 or delta == 0.0
            writer.writerow([column, external_columns[idx], "external_input_vector", "", f"{delta:.12g}", "0", f"r{idx + 1}", str(control).lower()])

    rows: list[tuple[str, str, str, int, float, float]] = []
    for idx, column in enumerate(expression_columns):
        for regulator, values in external_values.items():
            if regulator not in expression_genes:
                continue
            delta = values[idx] - baseline[regulator]
            sign = 1 if delta > 0 else -1 if delta < 0 else 0
            rows.append((f"{column}__{regulator}", regulator, "external_input", sign, abs(delta), 0.0))
    if not rows:
        raise ValueError("Could not derive interventions.tsv because no external regulator is present in expression genes.")
    interventions_path.parent.mkdir(parents=True, exist_ok=True)
    with interventions_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["intervention", "target", "effect", "sign", "dose", "timepoint"])
        for intervention, target, effect, sign, dose, timepoint in rows:
            writer.writerow([intervention, target, effect, sign, f"{dose:.12g}", f"{timepoint:.12g}"])


def copytree_replace(source: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)


def copy_native_outputs(
    *,
    requested: set[str],
    native_dir: Path,
    raw_output: Path,
    generated_xml: Path,
    generated_sif: Path,
    externals_file: Path,
    ini_path: Path,
) -> dict[str, str]:
    native_outputs: dict[str, str] = {}
    native_dir.mkdir(parents=True, exist_ok=True)
    unnormalized = sorted(raw_output.glob("*_unnormalized_dataset.txt"))
    maxexpr = sorted(raw_output.glob("*_maxExpr1_dataset.txt")) or sorted(raw_output.glob("*_normalized_dataset.txt"))
    mapping = {
        "syntren_unnormalized_dataset": (unnormalized[0] if len(unnormalized) == 1 else None, "syntren_unnormalized_dataset.txt"),
        "syntren_maxexpr_dataset": (maxexpr[0] if len(maxexpr) == 1 else None, "syntren_maxexpr_dataset.txt"),
        "syntren_network_sif": (generated_sif, "syntren_network_sif.sif"),
        "syntren_network_xml": (generated_xml, "syntren_network_xml.xml"),
        "syntren_external_inputs": (externals_file, "syntren_external_inputs.tsv"),
        "syntren_ini_file": (ini_path, "syntren.ini"),
    }
    for native_id, (source, dest_name) in mapping.items():
        if native_id not in requested or source is None or not source.exists():
            continue
        dest = native_dir / dest_name
        shutil.copy2(source, dest)
        native_outputs[native_id] = f"native/{dest.name}"
    return native_outputs


def write_manifest(
    output_dir: Path,
    request: dict[str, Any],
    table: ExpressionTable,
    extras: dict[str, str | None],
    native_outputs: dict[str, str],
) -> None:
    manifest = {
        "schema_version": "1.0",
        "simulator_id": "syntren",
        "data_axes": request["data_axes"],
        "truth_requirements": request["truth_requirements"],
        "seed": int(request.get("seed", 1)),
        "expression": {
            "path": "expression.tsv",
            "genes": len(table.genes),
            "columns": len(table.columns),
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
            "notes": "SynTReN 1.2 Java CLI executed with XStream 1.4.7 compatibility dependency; generated XML exported to SIF through GeneNetwork.toSIF().",
        },
    }
    write_json(output_dir / "simulator-output-manifest.json", manifest)


def write_session_info(raw_dir: Path) -> None:
    lines = [
        f"timestamp_utc={utc_now()}",
        f"python={sys.version.split()[0]}",
        f"platform={platform.platform()}",
        f"java_bin={JAVA_BIN}",
        f"syntren_version={SYN_TREN_VERSION}",
        f"syntren_jar={SYN_TREN_JAR}",
    ]
    if SYN_TREN_JAR.exists():
        lines.append(f"syntren_jar_sha256={sha256(SYN_TREN_JAR)}")
    for jar in ["xstream-1.4.7.jar", "xmlpull-1.1.3.1.jar", "xpp3_min-1.1.4c.jar"]:
        path = PATCHED_LIB / jar
        if path.exists():
            lines.append(f"{jar}_sha256={sha256(path)}")
    result = subprocess.run([str(JAVA_BIN), "-version"], text=True, capture_output=True, check=False)
    lines.append("java_version_output=" + ((result.stderr or result.stdout or "").strip().replace("\n", " | ")))
    (raw_dir / "session_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(request_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "provenance" / "raw"
    raw_output = raw_dir / "syntren_output"
    native_dir = output_dir / "native"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_output.mkdir(parents=True, exist_ok=True)

    try:
        write_progress(output_dir, "running", "initializing", "Reading simulator request.", percent=2)
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if request.get("simulator_id") != "syntren":
            raise ValueError("Request simulator_id must be syntren.")
        runtime = request.get("runtime_resources", {})
        threads = int(runtime.get("threads", 1))
        if threads != 1:
            raise ValueError("SynTReN does not expose thread controls; runtime_resources.threads must be 1.")
        params = normalize_params(dict(request.get("params", {})))
        enforce_semantic_bindings(params, dict(request.get("data_axes", {})))
        mounted_inputs = {str(k): str(v) for k, v in dict(request.get("mounted_inputs", {})).items()}
        write_json(raw_dir / "request_snapshot.json", request)
        write_json(raw_dir / "resolved_params.json", params)

        seed = as_int(request.get("seed", 1), "seed")
        source_sif = resolve_source_sif(params, mounted_inputs, raw_dir)
        externals_file = resolve_externals(params, mounted_inputs, raw_dir)
        ini_path = raw_dir / "syntren.ini"
        generated_xml = raw_output / "generated.xml"

        write_progress(output_dir, "running", "configuring", "Writing SynTReN ini.", percent=15)
        write_ini(
            ini_path,
            params=params,
            seed=seed,
            source_sif=source_sif,
            externals_file=externals_file,
            raw_output=raw_output,
            xml_file=generated_xml,
        )
        write_session_info(raw_dir)

        write_progress(output_dir, "running", "simulating", "Executing SynTReN CLI.", percent=30)
        run_syntren(ini_path, raw_dir)

        write_progress(output_dir, "running", "normalizing", "Exporting and parsing SynTReN outputs.", percent=65)
        generated_sif = raw_dir / "generated_network.sif"
        export_sif(generated_xml, generated_sif, raw_dir)

        expression_file = find_expression_file(raw_output, params["expression_variant"])
        table = read_expression(expression_file)
        edges = parse_sif_edges(generated_sif, set(table.genes))

        write_expression(output_dir / "expression.tsv", table)
        write_gene_universe(output_dir / "truth" / "gene_universe.txt", table.genes)
        write_truth_networks(output_dir / "truth" / "networks.csv", edges)

        extras_requested = {str(item) for item in request.get("effective_extras", [])}
        extras_paths: dict[str, str | None] = {}
        if "enrichment_background" in extras_requested:
            write_enrichment_background(output_dir / "extras" / "enrichment_background.txt", table.genes)
            extras_paths["enrichment_background"] = "extras/enrichment_background.txt"
        if "prior_grn" in extras_requested:
            write_prior_grn(output_dir / "extras" / "prior_grn.tsv", edges)
            extras_paths["prior_grn"] = "extras/prior_grn.tsv"
        if "tf_list" in extras_requested:
            write_tf_list(output_dir / "extras" / "tf_list.txt", edges)
            extras_paths["tf_list"] = "extras/tf_list.txt"
        if request["data_axes"]["experimental_design"] == "perturbational":
            missing = sorted({"perturbation_design", "interventions"}.difference(extras_requested))
            if missing:
                raise ValueError(f"Perturbational SynTReN runs require generated extras: {missing}")
            write_perturbation_extras(
                design_path=output_dir / "extras" / "perturbation_design.tsv",
                interventions_path=output_dir / "extras" / "interventions.tsv",
                expression_columns=table.columns,
                expression_genes=set(table.genes),
                externals_file=externals_file,
            )
            extras_paths["perturbation_design"] = "extras/perturbation_design.tsv"
            extras_paths["interventions"] = "extras/interventions.tsv"

        copytree_replace(raw_output, raw_dir / "syntren_output_snapshot")
        requested_native = {str(item) for item in request.get("native_outputs", [])}
        native_outputs = copy_native_outputs(
            requested=requested_native,
            native_dir=native_dir,
            raw_output=raw_output,
            generated_xml=generated_xml,
            generated_sif=generated_sif,
            externals_file=externals_file,
            ini_path=ini_path,
        )
        write_manifest(output_dir, request, table, extras_paths, native_outputs)
        write_progress(output_dir, "complete", "done", "SynTReN output normalized.", percent=100)
    except Exception as exc:
        (raw_dir / "wrapper_error.txt").write_text(
            "".join(traceback.format_exception(exc)),
            encoding="utf-8",
        )
        write_progress(output_dir, "failed", "error", str(exc), percent=100)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, default=Path("/work/request/simulator-run-request.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("/work/out"))
    args = parser.parse_args()
    run(args.request, args.output_dir)


if __name__ == "__main__":
    main()
