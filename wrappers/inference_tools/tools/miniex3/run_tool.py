"""MINI-EX v3 wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from _run_tool_common import (
    load_params,
    optional_extra_file,
    require_extra_file,
    require_param_keys,
    tail_text,
    validate_runtime_inputs,
    warn_unknown_params,
    write_progress,
)

MINIEX_DIR = Path("/opt/MINI-EX")
MINIEX_NF = MINIEX_DIR / "miniex.nf"
DATASET_ID = "andrea"


@dataclass(frozen=True)
class ResolvedParams:
    reference_species: str
    do_motif_analysis: bool
    motif_filter: str
    top_markers: int
    expression_filter: int
    top_regulons: int
    grnboost_subjobs: int


@dataclass(frozen=True)
class PreparedInputs:
    expression_path: Path
    markers_path: Path
    cells_to_clusters_path: Path
    cluster_identities_path: Path
    tf_list_path: Path
    terms_of_interest_path: Optional[Path]
    enrichment_background_path: Optional[Path]
    grnboost_path: Optional[Path]
    cluster_id_to_original: dict[str, str]


def _resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    expected = {
        "reference_species",
        "doMotifAnalysis",
        "motifFilter",
        "topMarkers",
        "expressionFilter",
        "topRegulons",
        "grnboostSubjobs",
    }
    require_param_keys(raw_params, expected)
    warn_unknown_params(raw_params, expected)

    reference_species = raw_params["reference_species"]
    if reference_species not in {"ath", "osa", "sly", "zma_v4", "zma_v5", "none"}:
        raise ValueError("reference_species must be one of: ath, osa, sly, zma_v4, zma_v5, none.")

    do_motif_analysis = raw_params["doMotifAnalysis"]
    if not isinstance(do_motif_analysis, bool):
        raise ValueError("doMotifAnalysis must be a boolean.")

    motif_filter = raw_params["motifFilter"]
    if motif_filter not in {"TF-F_motifs", "TF_motifs"}:
        raise ValueError("motifFilter must be one of: TF-F_motifs, TF_motifs.")

    top_markers = _as_int(raw_params["topMarkers"], "topMarkers", minimum=0)
    expression_filter = _as_int(raw_params["expressionFilter"], "expressionFilter", minimum=0, maximum=100)
    top_regulons = _as_int(raw_params["topRegulons"], "topRegulons", minimum=0)
    grnboost_subjobs = _as_int(raw_params["grnboostSubjobs"], "grnboostSubjobs", minimum=1)

    if reference_species == "none" and do_motif_analysis:
        raise ValueError("reference_species=none requires doMotifAnalysis=false.")

    return ResolvedParams(
        reference_species=str(reference_species),
        do_motif_analysis=do_motif_analysis,
        motif_filter=str(motif_filter),
        top_markers=top_markers,
        expression_filter=expression_filter,
        top_regulons=top_regulons,
        grnboost_subjobs=grnboost_subjobs,
    )


def _as_int(value: Any, name: str, *, minimum: int, maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}.")
    return int(value)


def _load_execution_mode(params_path: Path) -> str:
    execution_path = params_path.parent / "execution.json"
    if not execution_path.exists():
        return "group_native"
    with execution_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("execution.json must be a JSON object.")
    mode = data.get("mode", "group_native")
    if mode != "group_native":
        raise ValueError("MINI-EX v3 supports only execution.mode=group_native.")
    return "group_native"


def _read_expression(input_path: Path) -> tuple[str, list[str], list[str]]:
    with input_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("expression.tsv is empty.") from exc

        if len(header) < 2:
            raise ValueError("expression.tsv must contain a gene column and at least one cell column.")
        gene_header = header[0]
        cells = [str(cell).strip() for cell in header[1:]]
        if any(not cell for cell in cells):
            raise ValueError("expression.tsv contains an empty cell column name.")
        if len(set(cells)) != len(cells):
            raise ValueError("expression.tsv cell column names must be unique.")

        genes: list[str] = []
        seen_genes: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != len(header):
                raise ValueError(
                    f"expression.tsv line {line_number} has {len(row)} columns; expected {len(header)}."
                )
            gene = row[0].strip()
            if not gene:
                raise ValueError(f"expression.tsv line {line_number} has an empty gene id.")
            if gene in seen_genes:
                raise ValueError(f"expression.tsv contains duplicated gene id: {gene}")
            seen_genes.add(gene)
            genes.append(gene)
    return gene_header, cells, genes


def _copy_expression(input_path: Path, output_path: Path) -> None:
    shutil.copy2(input_path, output_path)


def _read_groups(groups_path: Path, cells: list[str]) -> tuple[dict[str, str], list[str]]:
    with groups_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("groups.tsv is empty or missing a header.")
        first_col = reader.fieldnames[0]
        if "cluster" not in reader.fieldnames:
            raise ValueError("groups.tsv is missing required column: cluster.")

        cell_to_group: dict[str, str] = {}
        group_order: list[str] = []
        for line_number, row in enumerate(reader, start=2):
            cell = (row.get(first_col) or "").strip()
            group = (row.get("cluster") or "").strip()
            if not cell or not group:
                raise ValueError(f"groups.tsv line {line_number} has an empty cell or cluster value.")
            if cell in cell_to_group:
                raise ValueError(f"groups.tsv contains duplicated cell id: {cell}")
            cell_to_group[cell] = group
            if group not in group_order:
                group_order.append(group)

    missing = [cell for cell in cells if cell not in cell_to_group]
    extra = sorted(set(cell_to_group).difference(cells))
    if missing:
        raise ValueError(f"groups.tsv is missing expression cells: {missing[:8]}")
    if extra:
        raise ValueError(f"groups.tsv contains cells not present in expression.tsv: {extra[:8]}")
    if len(group_order) < 1:
        raise ValueError("groups.tsv must define at least one group.")
    return cell_to_group, group_order


def _read_cluster_identities(path: Path, groups: list[str]) -> dict[str, tuple[str, Optional[str]]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("cluster_identities.tsv is empty or missing a header.")
        if "cluster" not in reader.fieldnames or "annotation" not in reader.fieldnames:
            raise ValueError("cluster_identities.tsv must contain cluster and annotation columns.")

        identities: dict[str, tuple[str, Optional[str]]] = {}
        for line_number, row in enumerate(reader, start=2):
            group = (row.get("cluster") or "").strip()
            annotation = (row.get("annotation") or "").strip()
            order = (row.get("order") or "").strip() if "order" in reader.fieldnames else ""
            if not group or not annotation:
                raise ValueError(
                    f"cluster_identities.tsv line {line_number} has an empty cluster or annotation."
                )
            if "_" in annotation:
                raise ValueError(
                    "MINI-EX does not allow underscores in cluster annotations; "
                    f"found {annotation!r}."
                )
            if group in identities:
                raise ValueError(f"cluster_identities.tsv contains duplicated cluster: {group}")
            if order:
                try:
                    int(order)
                except ValueError as exc:
                    raise ValueError(
                        f"cluster_identities.tsv line {line_number} has a non-integer order: {order!r}"
                    ) from exc
            identities[group] = (annotation, order or None)

    missing = [group for group in groups if group not in identities]
    extra = sorted(set(identities).difference(groups))
    if missing:
        raise ValueError(f"cluster_identities.tsv is missing groups from groups.tsv: {missing}")
    if extra:
        raise ValueError(f"cluster_identities.tsv contains groups not present in groups.tsv: {extra}")
    return identities


def _make_cluster_maps(groups: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    original_to_internal: dict[str, str] = {}
    internal_to_original: dict[str, str] = {}
    for idx, group in enumerate(groups, start=1):
        internal = f"C{idx}"
        original_to_internal[group] = internal
        internal_to_original[internal] = group
    return original_to_internal, internal_to_original


def _write_cells_to_clusters(
    path: Path,
    *,
    cells: list[str],
    cell_to_group: dict[str, str],
    original_to_internal: dict[str, str],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        for cell in cells:
            writer.writerow([cell, original_to_internal[cell_to_group[cell]]])


def _write_cluster_identities(
    path: Path,
    *,
    groups: list[str],
    identities: dict[str, tuple[str, Optional[str]]],
    original_to_internal: dict[str, str],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        for group in groups:
            annotation, _order = identities[group]
            writer.writerow([original_to_internal[group], annotation])


def _write_markers(
    *,
    input_path: Path,
    output_path: Path,
    genes: set[str],
    original_to_internal: dict[str, str],
) -> None:
    standard_columns = ["geneID", "p_val", "avg_logFC", "pct.1", "pct.2", "p_val_adj", "cluster", "gene"]
    with input_path.open("r", encoding="utf-8", newline="") as in_fh, output_path.open(
        "w", encoding="utf-8", newline=""
    ) as out_fh:
        reader = csv.DictReader(in_fh, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("cluster_markers.tsv is empty or missing a header.")
        for required in ("cluster", "gene", "p_val_adj"):
            if required not in reader.fieldnames:
                raise ValueError(f"cluster_markers.tsv is missing required column: {required}")

        writer = csv.DictWriter(out_fh, fieldnames=standard_columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        rows_written = 0
        for line_number, row in enumerate(reader, start=2):
            group = (row.get("cluster") or "").strip()
            gene = (row.get("gene") or "").strip()
            if group not in original_to_internal:
                raise ValueError(
                    f"cluster_markers.tsv line {line_number} references unknown group: {group!r}"
                )
            if gene not in genes:
                raise ValueError(
                    f"cluster_markers.tsv line {line_number} references gene not present in expression.tsv: {gene!r}"
                )
            try:
                p_val_adj = float((row.get("p_val_adj") or "").strip())
            except ValueError as exc:
                raise ValueError(
                    f"cluster_markers.tsv line {line_number} has invalid p_val_adj: {row.get('p_val_adj')!r}"
                ) from exc
            avg_logfc = row.get("avg_logFC", "1")
            try:
                if float(avg_logfc) < 0:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(
                    f"cluster_markers.tsv line {line_number} must contain only up-regulated markers."
                ) from exc

            writer.writerow(
                {
                    "geneID": row.get("geneID") or gene,
                    "p_val": row.get("p_val") or p_val_adj,
                    "avg_logFC": avg_logfc or "1",
                    "pct.1": row.get("pct.1") or "1",
                    "pct.2": row.get("pct.2") or "0",
                    "p_val_adj": p_val_adj,
                    "cluster": original_to_internal[group],
                    "gene": gene,
                }
            )
            rows_written += 1
    if rows_written == 0:
        raise ValueError("cluster_markers.tsv contains no marker rows.")


def _write_tf_list(input_path: Path, output_path: Path, genes: set[str]) -> None:
    tfs: list[str] = []
    for raw in input_path.read_text(encoding="utf-8").splitlines():
        token = raw.strip()
        if token and not token.startswith("#"):
            if token not in genes:
                raise ValueError(f"tf_list.txt contains TF not present in expression.tsv: {token}")
            tfs.append(token)
    if not tfs:
        raise ValueError("tf_list.txt contains no TFs.")
    output_path.write_text("\n".join(dict.fromkeys(tfs)) + "\n", encoding="utf-8")


def _copy_optional_list(input_path: Optional[Path], output_path: Path) -> Optional[Path]:
    if input_path is None:
        return None
    lines = [
        line.strip()
        for line in input_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        raise ValueError(f"{input_path.name} contains no usable rows.")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _write_grnboost_network(
    input_path: Optional[Path],
    output_path: Path,
    *,
    genes: set[str],
) -> Optional[Path]:
    if input_path is None:
        return None
    with input_path.open("r", encoding="utf-8", newline="") as in_fh, output_path.open(
        "w", encoding="utf-8", newline=""
    ) as out_fh:
        reader = csv.DictReader(in_fh, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("grnboost_network.tsv is empty or missing a header.")
        for required in ("source", "target", "score"):
            if required not in reader.fieldnames:
                raise ValueError(f"grnboost_network.tsv is missing required column: {required}")
        writer = csv.writer(out_fh, delimiter="\t", lineterminator="\n")
        rows_written = 0
        for line_number, row in enumerate(reader, start=2):
            source = (row.get("source") or "").strip()
            target = (row.get("target") or "").strip()
            if source not in genes or target not in genes:
                raise ValueError(
                    f"grnboost_network.tsv line {line_number} references genes not present in expression.tsv."
                )
            try:
                score = float((row.get("score") or "").strip())
            except ValueError as exc:
                raise ValueError(
                    f"grnboost_network.tsv line {line_number} has invalid score: {row.get('score')!r}"
                ) from exc
            if score == 0.0:
                continue
            writer.writerow([source, target, f"{score:.12g}"])
            rows_written += 1
    if rows_written == 0:
        raise ValueError("grnboost_network.tsv contains no non-zero edges.")
    return output_path


def _prepare_inputs(
    *,
    input_path: Path,
    extra_dir: Path,
    runtime_input_dir: Path,
    raw_dir: Path,
) -> PreparedInputs:
    _gene_header, cells, genes_list = _read_expression(input_path)
    genes = set(genes_list)

    groups_path = require_extra_file(extra_dir, "groups.tsv", "groups")
    markers_path = require_extra_file(extra_dir, "cluster_markers.tsv", "cluster_markers")
    identities_path = require_extra_file(extra_dir, "cluster_identities.tsv", "cluster_identities")
    tf_list_path = require_extra_file(extra_dir, "tf_list.txt", "tf_list")

    cell_to_group, group_order = _read_groups(groups_path, cells)
    identities = _read_cluster_identities(identities_path, group_order)
    original_to_internal, internal_to_original = _make_cluster_maps(group_order)

    expression_out = runtime_input_dir / f"{DATASET_ID}_matrix.tsv"
    cells_to_clusters_out = runtime_input_dir / f"{DATASET_ID}_cells2clusters.tsv"
    markers_out = runtime_input_dir / f"{DATASET_ID}_allMarkers.tsv"
    identities_out = runtime_input_dir / f"{DATASET_ID}_identities.tsv"
    tf_list_out = runtime_input_dir / "tf_list.tsv"
    terms_out = runtime_input_dir / "terms_of_interest.txt"
    background_out = runtime_input_dir / "enrichment_background.txt"
    grnboost_out = runtime_input_dir / f"{DATASET_ID}_grnboost2.tsv"

    _copy_expression(input_path, expression_out)
    _write_cells_to_clusters(
        cells_to_clusters_out,
        cells=cells,
        cell_to_group=cell_to_group,
        original_to_internal=original_to_internal,
    )
    _write_cluster_identities(
        identities_out,
        groups=group_order,
        identities=identities,
        original_to_internal=original_to_internal,
    )
    _write_markers(
        input_path=markers_path,
        output_path=markers_out,
        genes=genes,
        original_to_internal=original_to_internal,
    )
    _write_tf_list(tf_list_path, tf_list_out, genes)
    terms_path = _copy_optional_list(optional_extra_file(extra_dir, "terms_of_interest.txt"), terms_out)
    background_path = _copy_optional_list(
        optional_extra_file(extra_dir, "enrichment_background.txt"),
        background_out,
    )
    grnboost_path = _write_grnboost_network(
        optional_extra_file(extra_dir, "grnboost_network.tsv"),
        grnboost_out,
        genes=genes,
    )
    if grnboost_path is not None:
        raw_grnboost_dir = raw_dir / "grnboost2"
        raw_grnboost_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(grnboost_path, raw_grnboost_dir / grnboost_path.name)

    return PreparedInputs(
        expression_path=expression_out,
        markers_path=markers_out,
        cells_to_clusters_path=cells_to_clusters_out,
        cluster_identities_path=identities_out,
        tf_list_path=tf_list_out,
        terms_of_interest_path=terms_path,
        enrichment_background_path=background_path,
        grnboost_path=grnboost_path,
        cluster_id_to_original=internal_to_original,
    )


def _species_paths(reference_species: str, *, use_motif: bool, use_terms: bool) -> dict[str, Optional[Path]]:
    if reference_species == "none":
        return {
            "geneAliases": None,
            "infoTf": None,
            "featureFileMotifs": None,
            "goFile": None,
        }
    mapping = {
        "ath": {
            "dir": MINIEX_DIR / "data" / "ath",
            "alias": "ath_gene_aliases.tsv",
            "info": "ath_TF2fam2mot.tsv",
            "motif": "ath_2021.1_motifMapping.out.gz",
            "go": "ath_full_BP_expcur_ext_names.tsv",
        },
        "osa": {
            "dir": MINIEX_DIR / "data" / "osa",
            "alias": "osa_gene_aliases.tsv",
            "info": "osa_TF2fam2mot.tsv",
            "motif": "osa_2021.1_motifMapping.out.gz",
            "go": "osa_full_BP_ext_names.tsv",
        },
        "sly": {
            "dir": MINIEX_DIR / "data" / "sly",
            "alias": "sly_gene_aliases.tsv",
            "info": "sly_TF2fam2mot.tsv",
            "motif": "sly_2023.1_motifMapping.out.gz",
            "go": "sly_full_BP_ext_names.tsv",
        },
        "zma_v4": {
            "dir": MINIEX_DIR / "data" / "zma",
            "alias": "zma_v4_gene_aliases.tsv",
            "info": "zma_v4_TF2fam2mot.tsv",
            "motif": "zma_v4_2021.1_motifMapping.out.gz",
            "go": "zma_v4_full_BP_ext_names.tsv",
        },
        "zma_v5": {
            "dir": MINIEX_DIR / "data" / "zma",
            "alias": "zma_v5_gene_aliases.tsv",
            "info": "zma_v5_TF2fam2mot.tsv",
            "motif": "zma_v5_2023.1_motifMapping.out.gz",
            "go": "zma_v5_full_BP_ext_names.tsv",
        },
    }
    item = mapping[reference_species]
    base = item["dir"]
    out = {
        "geneAliases": base / item["alias"],
        "infoTf": base / item["info"] if use_motif else None,
        "featureFileMotifs": base / item["motif"] if use_motif else None,
        "goFile": base / item["go"] if use_terms else None,
    }
    for key, path in out.items():
        if path is not None and not path.exists():
            raise FileNotFoundError(f"MINI-EX built-in {key} file not found: {path}")
    return out


def _groovy_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_config(
    *,
    config_path: Path,
    params: ResolvedParams,
    prepared: PreparedInputs,
    raw_dir: Path,
    runtime_dir: Path,
    threads: int,
) -> None:
    if prepared.terms_of_interest_path is not None and params.reference_species == "none":
        raise ValueError("terms_of_interest requires reference_species other than none because GO annotations are unavailable.")

    species = _species_paths(
        params.reference_species,
        use_motif=params.do_motif_analysis,
        use_terms=prepared.terms_of_interest_path is not None,
    )
    config_path.write_text(
        "\n".join(
            [
                "executor {",
                "  name = 'local'",
                f"  queueSize = {max(1, int(threads))}",
                "}",
                "",
                "process.container = null",
                "docker.enabled = false",
                "singularity.enabled = false",
                f"workDir = {_groovy_value(runtime_dir / 'work')}",
                "",
                "params {",
                f"  expressionMatrix = {_groovy_value(prepared.expression_path)}",
                f"  markersOut = {_groovy_value(prepared.markers_path)}",
                f"  cellsToClusters = {_groovy_value(prepared.cells_to_clusters_path)}",
                f"  clustersToIdentities = {_groovy_value(prepared.cluster_identities_path)}",
                f"  grnboostOut = {_groovy_value(prepared.grnboost_path)}",
                f"  tfList = {_groovy_value(prepared.tf_list_path)}",
                f"  geneAliases = {_groovy_value(species['geneAliases'])}",
                f"  infoTf = {_groovy_value(species['infoTf'])}",
                f"  featureFileMotifs = {_groovy_value(species['featureFileMotifs'])}",
                f"  goFile = {_groovy_value(species['goFile'])}",
                f"  doMotifAnalysis = {_groovy_value(params.do_motif_analysis)}",
                f"  termsOfInterest = {_groovy_value(prepared.terms_of_interest_path)}",
                f"  topMarkers = {_groovy_value(params.top_markers)}",
                f"  expressionFilter = {_groovy_value(params.expression_filter)}",
                f"  motifFilter = {_groovy_value(params.motif_filter)}",
                f"  enrichmentBackground = {_groovy_value(prepared.enrichment_background_path)}",
                f"  topRegulons = {_groovy_value(params.top_regulons)}",
                f"  grnboostSubjobs = {_groovy_value(params.grnboost_subjobs)}",
                f"  outputDir = {_groovy_value(raw_dir)}",
                "}",
                "",
                "process {",
                "  executor = 'local'",
                "  errorStrategy = 'terminate'",
                "  withName: run_grnboost {",
                f"    cpus = {max(1, int(threads))}",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _run_nextflow(*, config_path: Path, runtime_dir: Path, log_path: Path, progress_path: Path) -> None:
    env = os.environ.copy()
    env["NXF_HOME"] = str(runtime_dir / ".nextflow")
    env["NXF_WORK"] = str(runtime_dir / "work")
    env["MPLCONFIGDIR"] = str(runtime_dir / "matplotlib")
    env["HOME"] = str(runtime_dir / "home")
    for key in ("NXF_HOME", "NXF_WORK", "MPLCONFIGDIR", "HOME"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)

    cmd = [
        "nextflow",
        "-C",
        str(config_path),
        "run",
        str(MINIEX_NF),
        "-ansi-log",
        "false",
    ]
    with log_path.open("a", encoding="utf-8") as log_fh:
        log_fh.write("$ " + " ".join(cmd) + "\n")
        log_fh.flush()
        process = subprocess.Popen(
            cmd,
            cwd=str(runtime_dir),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        last_update = 0.0
        tick = 0
        while process.poll() is None:
            now = time.monotonic()
            if now - last_update >= 5:
                percent = min(90, 20 + tick * 5)
                write_progress(
                    progress_path,
                    status="running",
                    percent=percent,
                    phase="nextflow",
                    message="Running MINI-EX Nextflow workflow",
                )
                tick += 1
                last_update = now
            time.sleep(0.5)

        if process.returncode != 0:
            raise RuntimeError(
                f"MINI-EX Nextflow workflow failed with exit code {process.returncode}.\n"
                f"{tail_text(log_path, max_lines=80)}"
            )


def _convert_network(raw_dir: Path, network_path: Path, cluster_id_to_original: dict[str, str]) -> int:
    edge_tables = sorted((raw_dir / "regulons").glob("*_edgeTable.tsv"))
    if not edge_tables:
        raise FileNotFoundError(f"No MINI-EX edge table found under {raw_dir / 'regulons'}")

    rows_written = 0
    with network_path.open("w", encoding="utf-8", newline="") as out_fh:
        writer = csv.DictWriter(
            out_fh,
            fieldnames=["source", "target", "score", "sign", "evidence", "context"],
        )
        writer.writeheader()
        for edge_table in edge_tables:
            with edge_table.open("r", encoding="utf-8", newline="") as in_fh:
                reader = csv.DictReader(in_fh, delimiter="\t")
                if reader.fieldnames is None:
                    continue
                missing = {"TF", "TG", "cluster", "weight"}.difference(reader.fieldnames)
                if missing:
                    raise ValueError(
                        f"Unexpected MINI-EX edge table columns in {edge_table}: missing {sorted(missing)}"
                    )
                for line_number, row in enumerate(reader, start=2):
                    source = (row.get("TF") or "").strip()
                    target = (row.get("TG") or "").strip()
                    cluster = (row.get("cluster") or "").strip()
                    if not source or not target or source == target:
                        continue
                    try:
                        score = float((row.get("weight") or "").strip())
                    except ValueError as exc:
                        raise ValueError(
                            f"Invalid MINI-EX weight in {edge_table.name} line {line_number}: {row.get('weight')!r}"
                        ) from exc
                    if score == 0.0:
                        continue
                    cluster_id = cluster.removeprefix("Cluster_")
                    original_group = cluster_id_to_original.get(cluster_id, cluster_id)
                    writer.writerow(
                        {
                            "source": source,
                            "target": target,
                            "score": f"{score:.12g}",
                            "sign": "?",
                            "evidence": "association",
                            "context": f"group:{original_group}",
                        }
                    )
                    rows_written += 1
    if rows_written == 0:
        raise ValueError("MINI-EX produced no non-zero non-self-loop edges.")
    return rows_written


def _ensure_aux_dirs(raw_dir: Path) -> None:
    for name in ("grnboost2", "regulons", "figures", "go_enrichment"):
        (raw_dir / name).mkdir(parents=True, exist_ok=True)


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
        required_paths=[MINIEX_NF],
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = args.output_dir / "progress.json"
    log_path = args.output_dir / "miniex.log"

    write_progress(progress_path, status="running", percent=0, phase="init", message="Initializing")

    try:
        execution_mode = _load_execution_mode(args.params)
        raw_params = load_params(args.params)
        params = _resolve_params(raw_params)

        raw_dir = args.output_dir / "raw"
        runtime_dir = args.output_dir / "runtime"
        runtime_input_dir = runtime_dir / "input"
        raw_dir.mkdir(parents=True, exist_ok=True)
        runtime_input_dir.mkdir(parents=True, exist_ok=True)
        _ensure_aux_dirs(raw_dir)

        log_path.write_text(
            f"MINI-EX wrapper execution.mode={execution_mode}\n",
            encoding="utf-8",
        )

        write_progress(
            progress_path,
            status="running",
            percent=5,
            phase="load_input",
            message="Preparing expression, groups and MINI-EX inputs",
        )
        prepared = _prepare_inputs(
            input_path=args.input,
            extra_dir=args.extra,
            runtime_input_dir=runtime_input_dir,
            raw_dir=raw_dir,
        )

        config_path = raw_dir / "miniex.config"
        _write_config(
            config_path=config_path,
            params=params,
            prepared=prepared,
            raw_dir=raw_dir,
            runtime_dir=runtime_dir,
            threads=args.threads,
        )

        write_progress(
            progress_path,
            status="running",
            percent=15,
            phase="inference",
            message="Starting MINI-EX Nextflow workflow",
        )
        _run_nextflow(
            config_path=config_path,
            runtime_dir=runtime_dir,
            log_path=log_path,
            progress_path=progress_path,
        )

        _ensure_aux_dirs(raw_dir)
        write_progress(
            progress_path,
            status="running",
            percent=95,
            phase="write_output",
            message="Converting MINI-EX edge table to network.csv",
        )
        rows = _convert_network(
            raw_dir=raw_dir,
            network_path=args.output_dir / "network.csv",
            cluster_id_to_original=prepared.cluster_id_to_original,
        )

        write_progress(
            progress_path,
            status="completed",
            percent=100,
            phase="done",
            message="Inference finished",
            completed=rows,
            total=rows,
        )
    except Exception as exc:
        write_progress(
            progress_path,
            status="failed",
            percent=100,
            phase="failed",
            message="Inference failed",
            error=str(exc),
        )
        print(str(exc), file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
