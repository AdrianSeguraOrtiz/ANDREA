#!/usr/bin/env python3
"""ANDREA wrapper for GeneNetWeaver.

The wrapper executes the official GeneNetWeaver Java CLI and normalizes its
benchmark files into ANDREA's simulator output contract.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENENETWEAVER_COMMIT = "c5310349f5d5723306585c2bb62aedbdeb70db46"
GNW_HOME = Path(os.environ.get("GNW_HOME", "/opt/genenetweaver"))
GNW_JAR = Path(os.environ.get("GNW_JAR", str(GNW_HOME / "gnw-3.1.2b.jar")))
DEFAULT_XML = GNW_HOME / "sandbox" / "InSilicoSize10-Yeast1.xml"

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
    "network_preset": "in_silico_size10_yeast1",
    "network_name": "andrea_gnw",
    "simulation_design": "perturbational_steady_state",
    "solver": "ode",
    "expression_variant": "normalized",
    "steady_state_experiment": "knockouts",
    "time_series_experiment": "dream4_timeseries",
    "steady_state": {
        "maxt_ode": 2000,
        "maxt_sde": -1,
        "mint_sde": 100,
    },
    "time_series": {
        "num_time_series": 10,
        "maxt": 1000,
        "dt": 50,
    },
    "perturbations": {
        "multifactorial_stdev": 0.25,
        "perturbation_probability": 0.33,
        "min_gene_deletion_effect": 1.0,
        "max_gene_deletion_effect": 1.0,
        "min_gene_overexpression_effect": 1.0,
        "max_gene_overexpression_effect": 1.0,
        "min_fraction_direct_targets": 0.05,
        "max_fraction_direct_targets": 0.3,
    },
    "ode": {
        "absolute_precision": 0.00001,
        "relative_precision": 0.001,
    },
    "sde": {
        "time_step": 1.0,
        "noise_coefficient": 0.05,
    },
    "experimental_noise": {
        "normal": False,
        "lognormal": False,
        "microarray": True,
        "normal_stdev": 0.025,
        "lognormal_stdev": 0.075,
        "normalize_after_adding_noise": True,
    },
    "goldstandard": {
        "ignore_autoregulatory_interactions": True,
        "append_zero_interactions": True,
    },
}

STEADY_LABELS = {
    "knockouts": "knockouts",
    "knockdowns": "knockdowns",
    "multifactorial": "multifactorial",
    "dual_knockouts": "dualknockouts",
    "dream4_time_series_perturbations": "dream4_timeseries",
}

TIME_SERIES_LABELS = {
    "dream4_timeseries": "dream4_timeseries",
    "knockouts": "knockout_timeseries",
    "knockdowns": "knockdown_timeseries",
    "multifactorial": "multifactorial_timeseries",
    "dual_knockouts": "dualknockout_timeseries",
}


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    sign: str
    score: float = 1.0


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


@dataclass(frozen=True)
class ExpressionTable:
    genes: list[str]
    columns: list[str]
    matrix: list[list[float]]
    metadata: list[ColumnMeta]


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


def as_int(value: Any, name: str, *, minimum: int | None = None) -> int:
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
    if not (number == number and abs(number) != float("inf")):
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
    enum_fields = {
        "network_preset": {"in_silico_size10_yeast1", "custom_xml"},
        "simulation_design": {"perturbational_steady_state", "time_series"},
        "solver": {"ode", "sde"},
        "expression_variant": {"normalized", "nonoise", "noexpnoise"},
        "steady_state_experiment": set(STEADY_LABELS).union({"all"}),
        "time_series_experiment": set(TIME_SERIES_LABELS).union({"all"}),
    }
    for field, allowed in enum_fields.items():
        if params[field] not in allowed:
            raise ValueError(f"{field} must be one of {sorted(allowed)}.")

    params["network_name"] = str(params.get("network_name") or "andrea_gnw").strip() or "andrea_gnw"

    steady = params["steady_state"]
    steady["maxt_ode"] = as_int(steady["maxt_ode"], "steady_state.maxt_ode", minimum=1)
    steady["maxt_sde"] = as_int(steady["maxt_sde"], "steady_state.maxt_sde")
    steady["mint_sde"] = as_int(steady["mint_sde"], "steady_state.mint_sde", minimum=0)

    time_series = params["time_series"]
    time_series["num_time_series"] = as_int(time_series["num_time_series"], "time_series.num_time_series", minimum=1)
    time_series["maxt"] = as_int(time_series["maxt"], "time_series.maxt", minimum=1)
    time_series["dt"] = as_int(time_series["dt"], "time_series.dt", minimum=1)
    if time_series["maxt"] % time_series["dt"] != 0:
        raise ValueError("time_series.maxt must be an exact multiple of time_series.dt.")

    perturbations = params["perturbations"]
    for name in (
        "multifactorial_stdev",
        "perturbation_probability",
        "min_gene_deletion_effect",
        "max_gene_deletion_effect",
        "min_gene_overexpression_effect",
        "max_gene_overexpression_effect",
        "min_fraction_direct_targets",
        "max_fraction_direct_targets",
    ):
        maximum = 1.0 if name in {"perturbation_probability", "min_fraction_direct_targets", "max_fraction_direct_targets"} else None
        perturbations[name] = as_float(perturbations[name], f"perturbations.{name}", minimum=0.0, maximum=maximum)
    if perturbations["min_gene_deletion_effect"] > perturbations["max_gene_deletion_effect"]:
        raise ValueError("perturbations.min_gene_deletion_effect must be <= max_gene_deletion_effect.")
    if perturbations["min_gene_overexpression_effect"] > perturbations["max_gene_overexpression_effect"]:
        raise ValueError("perturbations.min_gene_overexpression_effect must be <= max_gene_overexpression_effect.")
    if perturbations["min_fraction_direct_targets"] > perturbations["max_fraction_direct_targets"]:
        raise ValueError("perturbations.min_fraction_direct_targets must be <= max_fraction_direct_targets.")

    ode = params["ode"]
    ode["absolute_precision"] = as_float(ode["absolute_precision"], "ode.absolute_precision", minimum=0.0, exclusive_min=True)
    ode["relative_precision"] = as_float(ode["relative_precision"], "ode.relative_precision", minimum=0.0, exclusive_min=True)

    sde = params["sde"]
    sde["time_step"] = as_float(sde["time_step"], "sde.time_step", minimum=0.0, exclusive_min=True)
    sde["noise_coefficient"] = as_float(sde["noise_coefficient"], "sde.noise_coefficient", minimum=0.0)

    noise = params["experimental_noise"]
    for name in ("normal", "lognormal", "microarray", "normalize_after_adding_noise"):
        noise[name] = as_bool(noise[name], f"experimental_noise.{name}")
    noise["normal_stdev"] = as_float(noise["normal_stdev"], "experimental_noise.normal_stdev", minimum=0.0)
    noise["lognormal_stdev"] = as_float(noise["lognormal_stdev"], "experimental_noise.lognormal_stdev", minimum=0.0)

    gold = params["goldstandard"]
    gold["ignore_autoregulatory_interactions"] = as_bool(
        gold["ignore_autoregulatory_interactions"],
        "goldstandard.ignore_autoregulatory_interactions",
    )
    gold["append_zero_interactions"] = as_bool(gold["append_zero_interactions"], "goldstandard.append_zero_interactions")
    return params


def request_contexts(request: dict[str, Any]) -> set[str]:
    truth = request.get("truth_requirements", {})
    return {str(item) for item in truth.get("contexts", [])}


def validate_request(request: dict[str, Any], params: dict[str, Any]) -> str:
    if request.get("simulator_id") not in {None, "genenetweaver"}:
        raise ValueError("simulator_id must be genenetweaver.")
    resources = request.get("runtime_resources", {})
    threads = as_int(resources.get("threads", 1), "runtime_resources.threads", minimum=1)
    if threads != 1:
        raise ValueError("GeneNetWeaver does not expose upstream threading; runtime_resources.threads must be 1.")
    axes = request.get("data_axes", {})
    if axes.get("measurement") != "rna_expression" or axes.get("resolution") != "bulk":
        raise ValueError("GeneNetWeaver wrapper supports only bulk RNA expression capabilities.")
    contexts = request_contexts(request)
    if contexts != {"global"}:
        raise ValueError("GeneNetWeaver wrapper supports only global truth requirements.")
    if axes.get("column_kind") == "perturbations" and axes.get("experimental_design") == "perturbational":
        if params["simulation_design"] != "perturbational_steady_state":
            raise ValueError("simulation_design is controlled by the selected scenario and must be 'perturbational_steady_state'.")
        return "bulk_perturbational"
    if axes.get("column_kind") == "timepoints" and axes.get("experimental_design") == "time_series":
        if params["simulation_design"] != "time_series":
            raise ValueError("simulation_design is controlled by the selected scenario and must be 'time_series'.")
        return "bulk_time_series"
    raise ValueError("Unsupported GeneNetWeaver semantic capability.")


def bool01(value: bool) -> str:
    return "1" if value else "0"


def selected_labels(selection: str, labels: dict[str, str]) -> list[str]:
    if selection == "all":
        return list(labels.values())
    return [labels[selection]]


def build_settings(params: dict[str, Any], seed: int, output_dir: Path, mode: str) -> str:
    steady_labels = set(selected_labels(params["steady_state_experiment"], STEADY_LABELS)) if mode == "bulk_perturbational" else set()
    ts_labels = set(selected_labels(params["time_series_experiment"], TIME_SERIES_LABELS)) if mode == "bulk_time_series" else set()
    steady = params["steady_state"]
    time_series = params["time_series"]
    perturb = params["perturbations"]
    ode = params["ode"]
    sde = params["sde"]
    noise = params["experimental_noise"]
    gold = params["goldstandard"]

    simulate_ode = params["solver"] == "ode"
    simulate_sde = params["solver"] == "sde"
    if simulate_sde:
        # GNW always computes ODE wild-type/steady states internally for SDEs.
        # Keep simulateODE false so only SDE tables are printed.
        simulate_ode = False

    output_text = output_dir.as_posix()
    if not output_text.endswith("/"):
        output_text += "/"

    lines = [
        "randomSeed = " + str(int(seed)),
        "outputDirectory = " + output_text,
        "modelTranslation = 1",
        "ignoreAutoregulatoryInteractionsInEvaluation = " + bool01(gold["ignore_autoregulatory_interactions"]),
        "appendZeroInteractionsInGoldStandardFiles = " + bool01(gold["append_zero_interactions"]),
        "outputGenesInRows = 1",
        "numRegulators = -1",
        "truncatedSelectionFraction = 0.1",
        "numSeedsFromStronglyConnectedComponents = 0",
        "ssKnockouts = " + bool01("knockouts" in steady_labels),
        "ssKnockdowns = " + bool01("knockdowns" in steady_labels),
        "ssMultifactorial = " + bool01("multifactorial" in steady_labels),
        "ssDREAM4TimeSeries = " + bool01("dream4_timeseries" in steady_labels),
        "ssDualKnockouts = " + bool01("dualknockouts" in steady_labels),
        "maxtSteadyStateODE = " + str(steady["maxt_ode"]),
        "maxtSteadyStateSDE = " + str(steady["maxt_sde"]),
        "mintSDE = " + str(steady["mint_sde"]),
        "tsKnockouts = " + bool01("knockout_timeseries" in ts_labels),
        "tsKnockdowns = " + bool01("knockdown_timeseries" in ts_labels),
        "tsMultifactorial = " + bool01("multifactorial_timeseries" in ts_labels),
        "tsDREAM4TimeSeries = " + bool01("dream4_timeseries" in ts_labels),
        "tsDualKnockouts = " + bool01("dualknockout_timeseries" in ts_labels),
        "numTimeSeries = " + str(time_series["num_time_series"]),
        "maxtTimeSeries = " + str(time_series["maxt"]),
        "dt = " + str(time_series["dt"]),
        "multifactorialStdev = " + str(perturb["multifactorial_stdev"]),
        "perturbationProbability = " + str(perturb["perturbation_probability"]),
        "loadPerturbations = 0",
        "minGeneDeletionEffect = " + str(perturb["min_gene_deletion_effect"]),
        "maxGeneDeletionEffect = " + str(perturb["max_gene_deletion_effect"]),
        "minGeneOverexpressionEffect = " + str(perturb["min_gene_overexpression_effect"]),
        "maxGeneOverexpressionEffect = " + str(perturb["max_gene_overexpression_effect"]),
        "minFractionDirectTargets = " + str(perturb["min_fraction_direct_targets"]),
        "maxFractionDirectTargets = " + str(perturb["max_fraction_direct_targets"]),
        "simulateODE = " + bool01(simulate_ode),
        "absolutePrecision = " + str(ode["absolute_precision"]),
        "relativePrecision = " + str(ode["relative_precision"]),
        "simulateSDE = " + bool01(simulate_sde),
        "timeStepSDE = " + str(sde["time_step"]),
        "noiseCoefficientSDE = " + str(sde["noise_coefficient"]),
        "addNormalNoise = " + bool01(noise["normal"]),
        "addLognormalNoise = " + bool01(noise["lognormal"]),
        "addMicroarrayNoise = " + bool01(noise["microarray"]),
        "normalStdev = " + str(noise["normal_stdev"]),
        "lognormalStdev = " + str(noise["lognormal_stdev"]),
        "normalizeAfterAddingNoise = " + bool01(noise["normalize_after_adding_noise"]),
    ]
    return "\n".join(lines) + "\n"


def resolve_network_xml(request: dict[str, Any], params: dict[str, Any], raw_dir: Path) -> Path:
    if params["network_preset"] == "custom_xml":
        mounted = request.get("mounted_inputs", {})
        source = mounted.get("gnw_dynamical_network")
        if not source:
            raise ValueError("gnw_dynamical_network mounted input is required when network_preset=custom_xml.")
        source_path = Path(str(source))
    else:
        source_path = DEFAULT_XML
    if not source_path.exists():
        raise ValueError(f"GeneNetWeaver network XML not found: {source_path}")
    dest = raw_dir / "input_network.xml"
    shutil.copy2(source_path, dest)
    return dest


def run_gnw(settings_path: Path, network_xml: Path, gnw_output: Path, raw_dir: Path) -> None:
    if not GNW_JAR.exists():
        raise RuntimeError(f"GeneNetWeaver JAR not found: {GNW_JAR}")
    cmd = [
        "java",
        "-jar",
        str(GNW_JAR),
        "--simulate",
        "-c",
        str(settings_path),
        "--input-net",
        str(network_xml),
        "--output-path",
        str(gnw_output),
    ]
    write_json(raw_dir / "gnw_command.json", cmd)
    proc = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=False,
        cwd=gnw_output,
    )
    (raw_dir / "upstream_stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (raw_dir / "upstream_stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"GeneNetWeaver CLI failed with exit code {proc.returncode}.")


def find_prefix(gnw_output: Path) -> str:
    signed = sorted(gnw_output.glob("*_goldstandard_signed.tsv"))
    if len(signed) != 1:
        raise ValueError(f"Expected exactly one GNW signed gold-standard file, found {len(signed)}.")
    return signed[0].name.removesuffix("_goldstandard_signed.tsv")


def variant_filename(prefix: str, label: str, variant: str) -> str:
    if variant == "normalized":
        return f"{prefix}_{label}.tsv"
    return f"{prefix}_{variant}_{label}.tsv"


def parse_numeric_tsv(path: Path) -> tuple[list[str], list[list[float]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if not header:
            raise ValueError(f"{path} is missing a header row.")
        genes = [clean_token(value) for value in header]
        rows: list[list[float]] = []
        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(genes):
                raise ValueError(f"{path} has inconsistent width at line {line_no}.")
            rows.append([float(value) for value in row])
    if not rows:
        raise ValueError(f"{path} contains no data rows.")
    return genes, rows


def transpose(rows: list[list[float]]) -> list[list[float]]:
    return [list(values) for values in zip(*rows, strict=True)]


def clean_token(value: str) -> str:
    return str(value).strip().strip('"')


def sanitize_value_for_id(value: float | str) -> str:
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        pass
    else:
        if number.is_integer():
            text = str(int(number))
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "0"


def load_perturbation_matrix(path: Path) -> tuple[list[str], list[list[float]]]:
    if not path.exists():
        return [], []
    return parse_numeric_tsv(path)


def metadata_for_single_gene(
    *,
    columns: list[str],
    genes: list[str],
    family_label: str,
    timepoints: list[float] | None = None,
    series_ids: list[int] | None = None,
    series_offset: int = 0,
) -> list[ColumnMeta]:
    if "knockout" in family_label and "knockdown" not in family_label:
        perturbation = "knockout"
        dose = 1.0
    else:
        perturbation = "knockdown"
        dose = 0.5
    metadata: list[ColumnMeta] = []
    for idx, column in enumerate(columns):
        series_idx = series_ids[idx] - 1 if series_ids is not None else idx
        gene = genes[(series_offset + series_idx) % len(genes)]
        timepoint = timepoints[idx] if timepoints is not None else 0.0
        metadata.append(
            ColumnMeta(
                column=column,
                condition=f"{perturbation}_{gene}",
                perturbation=perturbation,
                target=gene,
                dose=dose,
                timepoint=timepoint,
                replicate=f"series_{series_idx + 1:03d}" if series_ids is not None else "r1",
                control=False,
            )
        )
    return metadata


def metadata_for_matrix(
    *,
    columns: list[str],
    genes: list[str],
    matrix_rows: list[list[float]],
    label: str,
    timepoints: list[float] | None = None,
    series_ids: list[int] | None = None,
) -> list[ColumnMeta]:
    metadata: list[ColumnMeta] = []
    for idx, column in enumerate(columns):
        series_idx = series_ids[idx] - 1 if series_ids is not None else idx
        row = matrix_rows[series_idx] if 0 <= series_idx < len(matrix_rows) else []
        nonzero = [(genes[j], value) for j, value in enumerate(row) if j < len(genes) and float(value) != 0.0]
        condition = f"{label}_{series_idx + 1:03d}"
        target = nonzero[0][0] if len(nonzero) == 1 else ""
        dose = abs(float(nonzero[0][1])) if len(nonzero) == 1 else 0.0
        metadata.append(
            ColumnMeta(
                column=column,
                condition=condition,
                perturbation="multifactorial",
                target=target,
                dose=dose,
                timepoint=timepoints[idx] if timepoints is not None else 0.0,
                replicate=f"series_{series_idx + 1:03d}",
                control=False,
            )
        )
    return metadata


def metadata_for_dual(
    *,
    columns: list[str],
    genes: list[str],
    index_path: Path,
    timepoints: list[float] | None = None,
    series_ids: list[int] | None = None,
) -> list[ColumnMeta]:
    pairs: list[tuple[str, str]] = []
    if index_path.exists():
        with index_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            next(reader, None)
            for row in reader:
                if len(row) < 2:
                    continue
                i = int(float(row[0])) - 1
                j = int(float(row[1])) - 1
                if 0 <= i < len(genes) and 0 <= j < len(genes):
                    pairs.append((genes[i], genes[j]))
    metadata: list[ColumnMeta] = []
    for idx, column in enumerate(columns):
        series_idx = series_ids[idx] - 1 if series_ids is not None else idx
        pair = pairs[series_idx] if 0 <= series_idx < len(pairs) else ("", "")
        condition = "dual_knockout_" + "_".join(g for g in pair if g)
        metadata.append(
            ColumnMeta(
                column=column,
                condition=condition or f"dual_knockout_{series_idx + 1:03d}",
                perturbation="dual_knockout",
                target="",
                dose=1.0,
                timepoint=timepoints[idx] if timepoints is not None else 0.0,
                replicate=f"series_{series_idx + 1:03d}",
                control=False,
            )
        )
    return metadata


def read_steady_expression(gnw_output: Path, prefix: str, params: dict[str, Any]) -> ExpressionTable:
    labels = selected_labels(params["steady_state_experiment"], STEADY_LABELS)
    all_genes: list[str] | None = None
    all_columns: list[str] = []
    all_rows_by_column: list[list[float]] = []
    all_metadata: list[ColumnMeta] = []
    for label in labels:
        path = gnw_output / variant_filename(prefix, label, params["expression_variant"])
        if not path.exists():
            raise ValueError(f"Expected GNW steady-state output not found: {path.name}")
        genes, rows = parse_numeric_tsv(path)
        if all_genes is None:
            all_genes = genes
        elif all_genes != genes:
            raise ValueError(f"Gene order mismatch in {path.name}.")
        columns = [f"{label}_{idx:03d}" for idx in range(1, len(rows) + 1)]
        if label == "knockouts":
            columns = [f"knockout_{gene}" for gene in genes[: len(rows)]]
            metadata = metadata_for_single_gene(columns=columns, genes=genes, family_label=label)
        elif label == "knockdowns":
            columns = [f"knockdown_{gene}" for gene in genes[: len(rows)]]
            metadata = metadata_for_single_gene(columns=columns, genes=genes, family_label=label)
        elif label == "dualknockouts":
            metadata = metadata_for_dual(
                columns=columns,
                genes=genes,
                index_path=gnw_output / f"{prefix}_dualknockouts_indexes.tsv",
            )
        else:
            p_genes, p_rows = load_perturbation_matrix(gnw_output / f"{prefix}_{label}_perturbations.tsv")
            if p_genes and p_genes != genes:
                raise ValueError(f"Perturbation matrix gene order mismatch for {label}.")
            metadata = metadata_for_matrix(columns=columns, genes=genes, matrix_rows=p_rows, label=label)
        all_columns.extend(columns)
        all_rows_by_column.extend(rows)
        all_metadata.extend(metadata)
    if all_genes is None:
        raise ValueError("No steady-state expression labels selected.")
    return ExpressionTable(
        genes=all_genes,
        columns=all_columns,
        matrix=transpose(all_rows_by_column),
        metadata=all_metadata,
    )


def parse_time_series_file(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header: list[str] | None = None
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for raw_line in lines:
        if not raw_line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        row = [clean_token(value) for value in raw_line.split("\t")]
        if header is None:
            header = row
            continue
        current.append(row)
    if current:
        blocks.append(current)
    if not header or len(header) < 2 or header[0].lower() != "time":
        raise ValueError(f"{path} does not look like a GNW time-series table.")
    genes = header[1:]
    observations: list[dict[str, Any]] = []
    for series_id, block in enumerate(blocks, start=1):
        for row in block:
            if len(row) != len(header):
                raise ValueError(f"{path} has inconsistent time-series row width.")
            timepoint = float(row[0])
            values = [float(value) for value in row[1:]]
            observations.append(
                {
                    "series_id": series_id,
                    "timepoint": timepoint,
                    "values": values,
                }
            )
    if not observations:
        raise ValueError(f"{path} contains no time-series observations.")
    return genes, observations


def read_time_series_expression(gnw_output: Path, prefix: str, params: dict[str, Any]) -> ExpressionTable:
    labels = selected_labels(params["time_series_experiment"], TIME_SERIES_LABELS)
    all_genes: list[str] | None = None
    all_columns: list[str] = []
    all_rows_by_column: list[list[float]] = []
    all_metadata: list[ColumnMeta] = []
    for label in labels:
        path = gnw_output / variant_filename(prefix, label, params["expression_variant"])
        if not path.exists():
            raise ValueError(f"Expected GNW time-series output not found: {path.name}")
        genes, observations = parse_time_series_file(path)
        if all_genes is None:
            all_genes = genes
        elif all_genes != genes:
            raise ValueError(f"Gene order mismatch in {path.name}.")
        columns = [
            f"{label}_series_{obs['series_id']:03d}_t_{sanitize_value_for_id(obs['timepoint'])}"
            for obs in observations
        ]
        values_by_column = [obs["values"] for obs in observations]
        timepoints = [float(obs["timepoint"]) for obs in observations]
        series_ids = [int(obs["series_id"]) for obs in observations]
        if label in {"knockout_timeseries", "knockdown_timeseries"}:
            metadata = metadata_for_single_gene(
                columns=columns,
                genes=genes,
                family_label=label,
                timepoints=timepoints,
                series_ids=series_ids,
                series_offset=0,
            )
        elif label == "dualknockout_timeseries":
            metadata = metadata_for_dual(
                columns=columns,
                genes=genes,
                index_path=gnw_output / f"{prefix}_dualknockouts_indexes.tsv",
                timepoints=timepoints,
                series_ids=series_ids,
            )
        else:
            perturb_label = label.removesuffix("_timeseries")
            p_genes, p_rows = load_perturbation_matrix(gnw_output / f"{prefix}_{label}_perturbations.tsv")
            if not p_rows:
                p_genes, p_rows = load_perturbation_matrix(gnw_output / f"{prefix}_{perturb_label}_perturbations.tsv")
            if p_genes and p_genes != genes:
                raise ValueError(f"Perturbation matrix gene order mismatch for {label}.")
            metadata = metadata_for_matrix(
                columns=columns,
                genes=genes,
                matrix_rows=p_rows,
                label=label,
                timepoints=timepoints,
                series_ids=series_ids,
            )
        all_columns.extend(columns)
        all_rows_by_column.extend(values_by_column)
        all_metadata.extend(metadata)
    if all_genes is None:
        raise ValueError("No time-series expression labels selected.")
    return ExpressionTable(
        genes=all_genes,
        columns=all_columns,
        matrix=transpose(all_rows_by_column),
        metadata=all_metadata,
    )


def parse_truth_edges(path: Path) -> list[Edge]:
    edges: list[Edge] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for row in reader:
            if len(row) < 3:
                continue
            source = clean_token(row[0])
            target = clean_token(row[1])
            sign = clean_token(row[2])
            if not source or not target or source == target:
                continue
            if sign not in {"+", "-"}:
                continue
            edges.append(Edge(source=source, target=target, sign=sign))
    if not edges:
        raise ValueError(f"GNW signed gold-standard contains no usable edges: {path}")
    return edges


def write_expression(path: Path, table: ExpressionTable) -> None:
    if len(table.matrix) != len(table.genes):
        raise ValueError("Expression matrix row count does not match gene count.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["gene", *table.columns])
        for gene, row in zip(table.genes, table.matrix, strict=True):
            if len(row) != len(table.columns):
                raise ValueError("Expression matrix column count mismatch.")
            writer.writerow([gene, *[f"{float(value):.12g}" for value in row]])


def write_gene_universe(path: Path, genes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(genes) + "\n", encoding="utf-8")


def write_truth_networks(path: Path, edges: list[Edge]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target", "score", "sign", "evidence", "context"])
        for edge in edges:
            writer.writerow([edge.source, edge.target, f"{edge.score:.12g}", edge.sign, "simulated_truth", "global"])


def write_text_list(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(values) + "\n", encoding="utf-8")


def write_prior_grn(path: Path, edges: list[Edge]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["source", "target", "score"])
        for edge in edges:
            signed_score = edge.score if edge.sign == "+" else -edge.score
            writer.writerow([edge.source, edge.target, f"{signed_score:.12g}"])


def write_perturbation_design(path: Path, metadata: list[ColumnMeta]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
                    "true" if item.control else "false",
                ]
            )


def intervention_rows(metadata: list[ColumnMeta], genes: list[str], gnw_output: Path, prefix: str) -> list[tuple[str, str, str, int, float, float]]:
    rows: dict[tuple[str, str], tuple[str, str, str, int, float, float]] = {}
    for item in metadata:
        if item.target:
            sign = -1 if item.perturbation in {"knockout", "knockdown", "dual_knockout"} else 1
            rows[(item.condition, item.target)] = (
                f"{item.condition}_{item.target}",
                item.target,
                item.perturbation,
                sign,
                item.dose,
                item.timepoint,
            )
    # For multifactorial rows with blank target, recover individual targets from
    # the sidecar matrices preserved by GNW.
    sidecars = list(gnw_output.glob(f"{prefix}_*_perturbations.tsv"))
    by_condition = {item.condition: item for item in metadata}
    for sidecar in sidecars:
        try:
            p_genes, p_rows = parse_numeric_tsv(sidecar)
        except Exception:  # noqa: BLE001
            continue
        if p_genes != genes:
            continue
        label = sidecar.name.removeprefix(f"{prefix}_").removesuffix("_perturbations.tsv")
        matching = [item for item in metadata if item.condition.startswith(label)]
        for item in matching:
            match = re.search(r"_(\d{3})$", item.condition)
            if not match:
                continue
            row_idx = int(match.group(1)) - 1
            if not (0 <= row_idx < len(p_rows)):
                continue
            for gene, value in zip(genes, p_rows[row_idx], strict=True):
                if value == 0:
                    continue
                rows[(item.condition, gene)] = (
                    f"{item.condition}_{gene}",
                    gene,
                    item.perturbation,
                    1 if value > 0 else -1,
                    abs(float(value)),
                    item.timepoint,
                )
    return list(rows.values())


def write_interventions(path: Path, metadata: list[ColumnMeta], genes: list[str], gnw_output: Path, prefix: str) -> None:
    rows = intervention_rows(metadata, genes, gnw_output, prefix)
    if not rows:
        rows = [("unperturbed", genes[0], "none", 0, 0.0, 0.0)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["intervention", "target", "effect", "sign", "dose", "timepoint"])
        for intervention, target, effect, sign, dose, timepoint in rows:
            writer.writerow([intervention, target, effect, sign, f"{dose:.12g}", f"{timepoint:.12g}"])


def write_timepoints(path: Path, metadata: list[ColumnMeta]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["column", "timepoint", "timepoint_label"])
        for item in metadata:
            writer.writerow([item.column, f"{item.timepoint:.12g}", f"t_{sanitize_value_for_id(item.timepoint)}"])


def write_public_id_maps(raw_dir: Path, genes: list[str], columns: list[str]) -> None:
    with (raw_dir / "public_gene_id_map.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["native_gene_id", "public_gene_id"])
        for gene in genes:
            writer.writerow([gene, gene])
    with (raw_dir / "public_column_id_map.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["native_column_id", "public_column_id"])
        for column in columns:
            writer.writerow([column, column])


def copy_path(src: Path, dst: Path) -> None:
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def copy_native_outputs(gnw_output: Path, prefix: str, native_dir: Path, requested: set[str]) -> dict[str, str]:
    native_outputs: dict[str, str] = {}
    native_map: dict[str, Path] = {
        "gnw_goldstandard": gnw_output / f"{prefix}_goldstandard.tsv",
        "gnw_goldstandard_signed": gnw_output / f"{prefix}_goldstandard_signed.tsv",
        "gnw_sbml_model": gnw_output / f"{prefix}.xml",
        "gnw_normalization_constant": gnw_output / f"{prefix}_normalization_constant.tsv",
    }
    for native_id, source in native_map.items():
        if native_id not in requested or not source.exists():
            continue
        suffix = source.suffix
        dest = native_dir / f"{native_id}{suffix}"
        copy_path(source, dest)
        native_outputs[native_id] = f"native/{dest.name}"

    if "gnw_expression_tables" in requested:
        dest = native_dir / "gnw_expression_tables"
        dest.mkdir(parents=True, exist_ok=True)
        for source in sorted(gnw_output.glob(f"{prefix}_*.tsv")):
            if any(token in source.name for token in ("goldstandard", "perturbations", "indexes", "normalization_constant")):
                continue
            shutil.copy2(source, dest / source.name)
        native_outputs["gnw_expression_tables"] = "native/gnw_expression_tables"

    if "gnw_perturbation_tables" in requested:
        dest = native_dir / "gnw_perturbation_tables"
        dest.mkdir(parents=True, exist_ok=True)
        for source in sorted(gnw_output.glob(f"{prefix}_*.tsv")):
            if any(token in source.name for token in ("perturbations", "indexes")):
                shutil.copy2(source, dest / source.name)
        native_outputs["gnw_perturbation_tables"] = "native/gnw_perturbation_tables"
    return native_outputs


def write_session_info(raw_dir: Path) -> None:
    proc = subprocess.run(["java", "-version"], text=True, capture_output=True, check=False)
    lines = [
        "simulator=genenetweaver",
        f"pinned_commit={GENENETWEAVER_COMMIT}",
        "jar_version=3.1.2 Beta",
        f"gnw_home={GNW_HOME}",
        f"gnw_jar={GNW_JAR}",
        f"python={sys.version.split()[0]}",
        f"platform={platform.platform()}",
        "java_version_output=" + (proc.stderr or proc.stdout or "").replace("\n", " | ").strip(),
    ]
    (raw_dir / "session_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest(
    output_dir: Path,
    request: dict[str, Any],
    table: ExpressionTable,
    extras_paths: dict[str, str | None],
    native_outputs: dict[str, str],
) -> None:
    manifest = {
        "schema_version": "1.0",
        "simulator_id": "genenetweaver",
        "data_axes": request["data_axes"],
        "truth_requirements": request["truth_requirements"],
        "seed": int(request["seed"]),
        "expression": {
            "path": "expression.tsv",
            "genes": len(table.genes),
            "columns": len(table.columns),
            "column_kind": request["data_axes"]["column_kind"],
        },
        "extras": {key: extras_paths.get(key) for key in EXTRA_KEYS},
        "native_outputs": native_outputs,
        "truth": {
            "gene_universe": "truth/gene_universe.txt",
            "networks": "truth/networks.csv",
        },
        "provenance": {
            "raw_dir": "provenance/raw",
            "notes": (
                "GeneNetWeaver executed through the official Java CLI pinned to commit "
                f"{GENENETWEAVER_COMMIT}; ANDREA wrapper normalized expression, truth and extras."
            ),
        },
    }
    write_json(output_dir / "simulator-output-manifest.json", manifest)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GeneNetWeaver and emit an ANDREA normalized simulator package.")
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
    gnw_output = raw_dir / "gnw_output"
    gnw_output.mkdir(parents=True, exist_ok=True)
    native_dir = output_dir / "native"

    try:
        write_progress(output_dir, "running", "validate_request", "Reading simulator-run-request.json.", percent=5)
        request = json.loads(args.request.read_text(encoding="utf-8"))
        write_json(raw_dir / "simulator-run-request.json", request)
        params = normalize_params(dict(request.get("params", {})))
        mode = validate_request(request, params)
        write_json(raw_dir / "resolved_params.json", params)

        write_progress(output_dir, "running", "prepare_gnw", "Preparing GeneNetWeaver settings.", percent=15)
        network_xml = resolve_network_xml(request, params, raw_dir)
        settings_path = raw_dir / "settings.txt"
        settings_path.write_text(
            build_settings(params, int(request.get("seed", 1)), gnw_output, mode),
            encoding="utf-8",
        )
        write_session_info(raw_dir)

        write_progress(output_dir, "running", "run_simulator", "Running GeneNetWeaver Java CLI.", percent=30)
        run_gnw(settings_path, network_xml, gnw_output, raw_dir)
        prefix = find_prefix(gnw_output)

        write_progress(output_dir, "running", "normalize_outputs", "Normalizing GeneNetWeaver outputs.", percent=75)
        if mode == "bulk_perturbational":
            table = read_steady_expression(gnw_output, prefix, params)
        else:
            table = read_time_series_expression(gnw_output, prefix, params)
        signed_truth = gnw_output / f"{prefix}_goldstandard_signed.tsv"
        edges = parse_truth_edges(signed_truth)
        gene_set = set(table.genes)
        missing_truth_genes = sorted({edge.source for edge in edges}.union(edge.target for edge in edges).difference(gene_set))
        if missing_truth_genes:
            raise ValueError(f"Truth edges reference genes absent from expression.tsv: {missing_truth_genes[:8]}")

        write_expression(output_dir / "expression.tsv", table)
        write_gene_universe(output_dir / "truth" / "gene_universe.txt", table.genes)
        write_truth_networks(output_dir / "truth" / "networks.csv", edges)
        write_public_id_maps(raw_dir, table.genes, table.columns)

        extras = {str(item) for item in request.get("effective_extras", [])}
        extras_paths: dict[str, str | None] = {key: None for key in EXTRA_KEYS}
        if "perturbation_design" in extras:
            write_perturbation_design(output_dir / "extras" / "perturbation_design.tsv", table.metadata)
            extras_paths["perturbation_design"] = "extras/perturbation_design.tsv"
        if "interventions" in extras:
            write_interventions(output_dir / "extras" / "interventions.tsv", table.metadata, table.genes, gnw_output, prefix)
            extras_paths["interventions"] = "extras/interventions.tsv"
        if "timepoints" in extras:
            write_timepoints(output_dir / "extras" / "timepoints.tsv", table.metadata)
            extras_paths["timepoints"] = "extras/timepoints.tsv"
        if "enrichment_background" in extras:
            write_text_list(output_dir / "extras" / "enrichment_background.txt", table.genes)
            extras_paths["enrichment_background"] = "extras/enrichment_background.txt"
        if "prior_grn" in extras:
            write_prior_grn(output_dir / "extras" / "prior_grn.tsv", edges)
            extras_paths["prior_grn"] = "extras/prior_grn.tsv"
        if "tf_list" in extras:
            write_text_list(output_dir / "extras" / "tf_list.txt", sorted({edge.source for edge in edges}))
            extras_paths["tf_list"] = "extras/tf_list.txt"

        requested_native = {str(item) for item in request.get("native_outputs", [])}
        native_outputs = copy_native_outputs(gnw_output, prefix, native_dir, requested_native)

        write_progress(output_dir, "running", "write_manifest", "Writing simulator-output-manifest.json.", percent=95)
        write_manifest(output_dir, request, table, extras_paths, native_outputs)
        write_progress(output_dir, "completed", "done", "GeneNetWeaver simulation package completed.", percent=100)
        return 0
    except BaseException as exc:  # noqa: BLE001
        try:
            write_session_info(raw_dir)
            (raw_dir / "wrapper_error.log").write_text(traceback.format_exc(), encoding="utf-8")
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
