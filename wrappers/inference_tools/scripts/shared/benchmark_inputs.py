"""Deterministic synthetic inputs for inference-tool cost benchmarks."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

GENERATED_EXTRA_INPUTS = {
    "column_phenotypes",
    "column_descriptors",
    "cluster_identities",
    "cluster_markers",
    "enrichment_background",
    "grnboost_network",
    "groups",
    "interventions",
    "lineage_tree",
    "perturbation_design",
    "prior_grn",
    "prior_grn_by_group",
    "pseudotime",
    "replicates",
    "spatial_coordinates",
    "terms_of_interest",
    "tf_list",
    "timepoints",
}

EXTRA_FILENAMES = {
    "column_phenotypes": "column_phenotypes.tsv",
    "column_descriptors": "column_descriptors.tsv",
    "cluster_identities": "cluster_identities.tsv",
    "cluster_markers": "cluster_markers.tsv",
    "enrichment_background": "enrichment_background.txt",
    "grnboost_network": "grnboost_network.tsv",
    "groups": "groups.tsv",
    "interventions": "interventions.tsv",
    "lineage_tree": "lineage_tree.tsv",
    "perturbation_design": "perturbation_design.tsv",
    "prior_grn": "prior_grn.tsv",
    "prior_grn_by_group": "prior_grn_by_group.tsv",
    "pseudotime": "pseudotime.tsv",
    "replicates": "replicates.tsv",
    "spatial_coordinates": "spatial_coordinates.tsv",
    "terms_of_interest": "terms_of_interest.txt",
    "tf_list": "tf_list.txt",
    "timepoints": "timepoints.tsv",
}

HUMAN_BREAST_CANCER_PATHWAY_GENES = (
    "ESR1",
    "ESR2",
    "NCOA1",
    "NCOA3",
    "FOS",
    "JUN",
    "SP1",
    "CCND1",
    "MYC",
    "PGR",
    "WNT1",
    "WNT4",
    "TNFSF11",
    "ERBB2",
    "FGF1",
    "FGF2",
    "FGF3",
    "FGF4",
    "FGF17",
    "FGF6",
    "FGF7",
    "FGF8",
    "FGF9",
    "FGF10",
    "FGF16",
    "FGF5",
    "FGF18",
    "FGF20",
    "FGF22",
    "FGF19",
    "FGF21",
    "FGF23",
    "FGFR1",
    "IGF1",
    "IGF1R",
    "EGF",
    "EGFR",
    "KIT",
    "SHC1",
    "SHC2",
    "SHC3",
    "SHC4",
    "GRB2",
    "SOS1",
    "SOS2",
    "HRAS",
    "KRAS",
    "NRAS",
    "ARAF",
    "BRAF",
    "RAF1",
    "MAP2K1",
    "MAP2K2",
    "MAPK1",
    "MAPK3",
    "PIK3CA",
    "PIK3CD",
    "PIK3CB",
    "PIK3R1",
    "PIK3R2",
    "PIK3R3",
    "PTEN",
    "AKT1",
    "AKT2",
    "AKT3",
    "MTOR",
    "RPS6KB1",
    "RPS6KB2",
    "JAG1",
    "JAG2",
    "DLL3",
    "DLL1",
    "DLL4",
    "NOTCH1",
    "NOTCH2",
    "NOTCH3",
    "NOTCH4",
    "HES1",
    "HES5",
    "HEYL",
    "HEY1",
    "HEY2",
    "FLT4",
    "CDKN1A",
    "NFKB2",
    "WNT2",
    "WNT2B",
    "WNT3",
    "WNT3A",
    "WNT5A",
    "WNT5B",
    "WNT6",
    "WNT7A",
    "WNT7B",
    "WNT8A",
    "WNT8B",
    "WNT9A",
    "WNT9B",
    "WNT10B",
    "WNT10A",
    "WNT11",
    "WNT16",
    "FZD1",
    "FZD7",
    "FZD2",
    "FZD3",
    "FZD4",
    "FZD5",
    "FZD8",
    "FZD6",
    "FZD10",
    "FZD9",
    "LRP5",
    "LRP6",
    "DVL3",
    "DVL2",
    "DVL1",
    "FRAT1",
    "FRAT2",
    "GSK3B",
    "AXIN1",
    "AXIN2",
    "APC",
    "APC2",
    "CTNNB1",
    "CSNK1A1L",
    "CSNK1A1",
    "TCF7",
    "TCF7L1",
    "TCF7L2",
    "LEF1",
    "TP53",
    "GADD45A",
    "GADD45B",
    "GADD45G",
    "BAX",
    "BAK1",
    "DDB2",
    "POLK",
    "CDK4",
    "CDK6",
    "RB1",
    "E2F1",
    "E2F2",
    "E2F3",
    "BRCA1",
    "BRCA2",
)


@dataclass(frozen=True)
class BenchmarkInputSize:
    genes: int
    columns: int


@dataclass(frozen=True)
class BenchmarkInputProfile:
    seed: int = 12345
    column_kind: str = "samples"
    expression_profile: str = "synthetic_benchmark"
    gene_id_source: str = "synthetic"
    extras_provided: tuple[str, ...] = ()
    group_count: int = 0
    tf_count_policy: str | None = "max(3, genes/5)"
    prior_density: float | None = 0.05
    marker_count_per_group: int = 4
    terms_of_interest: tuple[str, ...] = ("root", "development", "stress")

    @classmethod
    def from_cost_input_profile(
        cls,
        payload: dict[str, Any],
        *,
        seed: int,
    ) -> "BenchmarkInputProfile":
        extras = payload.get("extras_provided", [])
        if not isinstance(extras, list):
            extras = []
        return cls(
            seed=seed,
            column_kind=str(payload.get("column_kind", "samples") or "samples"),
            expression_profile=str(
                payload.get("expression_profile", "synthetic_benchmark")
                or "synthetic_benchmark"
            ),
            gene_id_source=str(
                payload.get("gene_id_source", "synthetic") or "synthetic"
            ),
            extras_provided=tuple(
                dict.fromkeys(str(item).strip() for item in extras if str(item).strip())
            ),
            group_count=_as_int(payload.get("group_count", 0), default=0),
            tf_count_policy=(
                str(payload["tf_count_policy"])
                if payload.get("tf_count_policy") is not None
                else None
            ),
            prior_density=_as_optional_density(payload.get("prior_density")),
            marker_count_per_group=_as_int(
                payload.get("marker_count_per_group", 4),
                default=4,
            ),
        )


@dataclass(frozen=True)
class BenchmarkInputBundle:
    expression_path: Path
    extra_dir: Path
    genes: tuple[str, ...]
    columns: tuple[str, ...]
    groups: tuple[str, ...]
    tfs: tuple[str, ...]
    extras: dict[str, Path]


def write_benchmark_io_dir(
    io_dir: Path,
    size: BenchmarkInputSize,
    profile: BenchmarkInputProfile,
) -> BenchmarkInputBundle:
    """Write expression.tsv and selected /extra inputs for one benchmark profile."""
    _validate_profile(size=size, profile=profile)

    io_dir.mkdir(parents=True, exist_ok=True)
    extra_dir = io_dir / "extra"
    out_dir = io_dir / "out"
    extra_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    expression_path = io_dir / "expression.tsv"
    genes, columns = write_expression_matrix(
        expression_path,
        size=size,
        profile=profile,
    )
    tfs = select_tfs(genes, profile.tf_count_policy)
    groups, assignments = assign_groups(columns, profile.group_count)

    extras: dict[str, Path] = {}
    for input_key in profile.extras_provided:
        if input_key not in GENERATED_EXTRA_INPUTS:
            raise ValueError(f"Unsupported synthetic extra input: {input_key}")
        path = extra_dir / EXTRA_FILENAMES[input_key]
        if input_key == "column_phenotypes":
            write_column_phenotypes(path, columns=columns, assignments=assignments)
        elif input_key == "column_descriptors":
            write_column_descriptors(path, columns=columns, assignments=assignments)
        elif input_key == "cluster_identities":
            write_cluster_identities(path, groups=groups)
        elif input_key == "cluster_markers":
            write_cluster_markers(
                path,
                genes=genes,
                groups=groups,
                marker_count_per_group=profile.marker_count_per_group,
            )
        elif input_key == "enrichment_background":
            write_gene_list(path, genes)
        elif input_key == "grnboost_network":
            write_network_edges(
                path,
                genes=genes,
                groups=groups,
                tfs=tfs,
                density=resolved_prior_density(profile),
                marker_count_per_group=profile.marker_count_per_group,
                seed=stable_seed(size=size, profile=profile, namespace=input_key),
            )
        elif input_key == "groups":
            write_groups(path, assignments=assignments)
        elif input_key == "interventions":
            write_interventions(path, genes=genes)
        elif input_key == "lineage_tree":
            write_lineage_tree(path, groups=groups)
        elif input_key == "perturbation_design":
            write_perturbation_design(path, columns=columns, genes=genes)
        elif input_key == "prior_grn":
            write_network_edges(
                path,
                genes=genes,
                groups=groups,
                tfs=tfs,
                density=resolved_prior_density(profile),
                marker_count_per_group=profile.marker_count_per_group,
                seed=stable_seed(size=size, profile=profile, namespace=input_key),
            )
        elif input_key == "prior_grn_by_group":
            write_prior_grn_by_group(
                path,
                genes=genes,
                groups=groups,
                tfs=tfs,
                density=resolved_prior_density(profile),
                seed=stable_seed(size=size, profile=profile, namespace=input_key),
            )
        elif input_key == "pseudotime":
            write_pseudotime(path, columns=columns, assignments=assignments)
        elif input_key == "replicates":
            write_replicates(path, columns=columns)
        elif input_key == "spatial_coordinates":
            write_spatial_coordinates(path, columns=columns)
        elif input_key == "terms_of_interest":
            write_terms_of_interest(path, profile.terms_of_interest)
        elif input_key == "tf_list":
            write_tf_list(path, tfs)
        elif input_key == "timepoints":
            write_timepoints(path, columns=columns)
        extras[input_key] = path

    return BenchmarkInputBundle(
        expression_path=expression_path,
        extra_dir=extra_dir,
        genes=tuple(genes),
        columns=tuple(columns),
        groups=tuple(groups),
        tfs=tuple(tfs),
        extras=extras,
    )


def write_expression_matrix(
    path: Path,
    *,
    size: BenchmarkInputSize,
    profile: BenchmarkInputProfile,
) -> tuple[list[str], list[str]]:
    genes = gene_names(size.genes, source=profile.gene_id_source)
    columns = column_names(size.columns, profile.column_kind)
    rng = random.Random(stable_seed(size=size, profile=profile, namespace="expression"))

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["gene"] + columns)
        for gene_idx, gene in enumerate(genes, start=1):
            row = []
            for col_idx in range(1, size.columns + 1):
                trend = (gene_idx * 0.17) + (col_idx * 0.031)
                seasonal = ((gene_idx + col_idx) % 7) * 0.013
                noise = rng.uniform(0.0, 0.02)
                row.append(f"{trend + seasonal + noise:.6f}")
            writer.writerow([gene] + row)

    return genes, columns


def write_tf_list(path: Path, tfs: Sequence[str]) -> None:
    path.write_text("".join(f"{tf}\n" for tf in tfs), encoding="utf-8")


def write_groups(
    path: Path,
    *,
    assignments: Sequence[tuple[str, str]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "cluster"])
        writer.writerows(assignments)


def write_lineage_tree(path: Path, groups: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["child", "parent", "gain_rate", "loss_rate"])
        if len(groups) == 1:
            writer.writerow([groups[0], groups[0], "0.2", "0.8"])
            return
        for idx in range(1, len(groups)):
            writer.writerow([groups[idx], groups[idx - 1], "0.2", "0.8"])


def write_network_edges(
    path: Path,
    *,
    genes: Sequence[str],
    groups: Sequence[str] = (),
    tfs: Sequence[str],
    density: float,
    marker_count_per_group: int = 4,
    seed: int,
) -> None:
    edges = select_directed_edges(
        genes=genes,
        groups=groups,
        tfs=tfs,
        density=density,
        marker_count_per_group=marker_count_per_group,
        seed=seed,
    )
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["source", "target", "score"])
        for source, target, score in edges:
            writer.writerow([source, target, f"{score:.6f}"])


def write_prior_grn_by_group(
    path: Path,
    *,
    genes: Sequence[str],
    groups: Sequence[str],
    tfs: Sequence[str],
    density: float,
    seed: int,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["group", "source", "target", "score"])
        for group_idx, group in enumerate(groups):
            group_edges = select_directed_edges(
                genes=genes,
                tfs=tfs,
                density=density,
                seed=seed + group_idx,
            )
            for source, target, score in group_edges:
                writer.writerow([group, source, target, f"{score:.6f}"])


def write_column_phenotypes(
    path: Path,
    *,
    columns: Sequence[str],
    assignments: Sequence[tuple[str, str]],
) -> None:
    group_order = _unique_in_order(group for _column, group in assignments)
    order_by_group = {group: idx for idx, group in enumerate(group_order)}
    label_by_group = {group: f"P{idx + 1}" for idx, group in enumerate(group_order)}
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "phenotype", "order"])
        group_by_column = dict(assignments)
        for column in columns:
            group = group_by_column[column]
            writer.writerow([column, label_by_group[group], order_by_group[group]])


def write_column_descriptors(
    path: Path,
    *,
    columns: Sequence[str],
    assignments: Sequence[tuple[str, str]],
) -> None:
    group_by_column = dict(assignments)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "batch", "condition", "cell_type"])
        for idx, column in enumerate(columns):
            writer.writerow(
                [
                    column,
                    f"batch_{(idx % 2) + 1}",
                    "stimulated" if idx % 3 == 0 else "baseline",
                    group_by_column.get(column, "cluster_1"),
                ]
            )


def write_timepoints(path: Path, *, columns: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "timepoint"])
        for idx, column in enumerate(columns):
            writer.writerow([column, f"{float(idx % 4):.1f}"])


def write_replicates(path: Path, *, columns: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "replicate", "batch"])
        for idx, column in enumerate(columns):
            writer.writerow([column, f"r{(idx % 3) + 1}", f"batch_{(idx % 2) + 1}"])


def write_perturbation_design(
    path: Path,
    *,
    columns: Sequence[str],
    genes: Sequence[str],
) -> None:
    targets = list(genes[: max(1, min(3, len(genes)))])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "column",
                "condition",
                "perturbation",
                "target",
                "dose",
                "timepoint",
                "replicate",
                "control",
            ]
        )
        for idx, column in enumerate(columns):
            if idx % 4 == 0:
                writer.writerow(
                    [column, "control", "none", "", "0", "0", f"r{(idx % 3) + 1}", "true"]
                )
            else:
                target = targets[idx % len(targets)]
                writer.writerow(
                    [
                        column,
                        f"knockdown_{target}",
                        "knockdown",
                        target,
                        "1",
                        "24",
                        f"r{(idx % 3) + 1}",
                        "false",
                    ]
                )


def write_interventions(path: Path, *, genes: Sequence[str]) -> None:
    targets = list(genes[: max(1, min(3, len(genes)))])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["intervention", "target", "effect", "sign", "dose"])
        for target in targets:
            writer.writerow([f"knockdown_{target}", target, "knockdown", "-1", "1.0"])


def write_pseudotime(
    path: Path,
    *,
    columns: Sequence[str],
    assignments: Sequence[tuple[str, str]],
) -> None:
    group_to_columns: dict[str, list[str]] = {}
    for column, group in assignments:
        group_to_columns.setdefault(group, []).append(column)

    pseudotime_by_column: dict[str, float] = {}
    for group_idx, group in enumerate(
        _unique_in_order(group for _c, group in assignments)
    ):
        group_columns = group_to_columns[group]
        denom = max(1, len(group_columns) - 1)
        for within_idx, column in enumerate(group_columns):
            pseudotime_by_column[column] = group_idx + (within_idx / denom)

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "pseudotime"])
        for column in columns:
            writer.writerow([column, f"{pseudotime_by_column[column]:.6f}"])


def write_spatial_coordinates(path: Path, *, columns: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["column", "x", "y"])
        for idx, column in enumerate(columns):
            row = idx // 8
            col = idx % 8
            writer.writerow([column, f"{col:.6f}", f"{row:.6f}"])


def write_cluster_identities(path: Path, groups: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["cluster", "annotation", "order"])
        for idx, group in enumerate(groups, start=1):
            writer.writerow([group, f"Type{idx}", idx])


def write_cluster_markers(
    path: Path,
    *,
    genes: Sequence[str],
    groups: Sequence[str],
    marker_count_per_group: int,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "geneID",
                "p_val",
                "avg_logFC",
                "pct.1",
                "pct.2",
                "p_val_adj",
                "cluster",
                "gene",
            ]
        )
        marker_count = max(1, min(len(genes), marker_count_per_group))
        for group_idx, group in enumerate(groups):
            offset = (group_idx * marker_count) % len(genes)
            for marker_idx in range(marker_count):
                gene = genes[(offset + marker_idx) % len(genes)]
                p_val = 0.0005 * (marker_idx + 1)
                p_adj = min(0.049, 0.0005 * (marker_idx + 1))
                avg_logfc = 0.5 + (0.1 * marker_idx)
                writer.writerow(
                    [
                        gene,
                        f"{p_val:.6g}",
                        f"{avg_logfc:.6g}",
                        "0.8",
                        "0.2",
                        f"{p_adj:.6g}",
                        group,
                        gene,
                    ]
                )


def write_terms_of_interest(path: Path, terms: Sequence[str]) -> None:
    clean_terms = [term.strip() for term in terms if term.strip()]
    if not clean_terms:
        clean_terms = ["root"]
    path.write_text("".join(f"{term}\n" for term in clean_terms), encoding="utf-8")


def write_gene_list(path: Path, genes: Sequence[str]) -> None:
    path.write_text("".join(f"{gene}\n" for gene in genes), encoding="utf-8")


def gene_names(count: int, *, source: str = "synthetic") -> list[str]:
    if source == "synthetic":
        return [f"G{idx + 1}" for idx in range(count)]
    if source == "human_breast_cancer_pathway":
        if count > len(HUMAN_BREAST_CANCER_PATHWAY_GENES):
            raise ValueError(
                "gene_id_source=human_breast_cancer_pathway supports at most "
                f"{len(HUMAN_BREAST_CANCER_PATHWAY_GENES)} genes."
            )
        return list(HUMAN_BREAST_CANCER_PATHWAY_GENES[:count])
    raise ValueError(f"Unsupported gene_id_source: {source}")


def column_names(count: int, column_kind: str) -> list[str]:
    prefix = "cell" if column_kind == "cells" else "S"
    if prefix == "cell":
        return [f"cell_{idx + 1}" for idx in range(count)]
    return [f"S{idx + 1}" for idx in range(count)]


def group_names(count: int) -> list[str]:
    return [f"cluster_{idx + 1}" for idx in range(count)]


def assign_groups(
    columns: Sequence[str],
    requested_group_count: int,
) -> tuple[list[str], list[tuple[str, str]]]:
    group_count = max(1, requested_group_count)
    if len(columns) < group_count:
        raise ValueError(
            f"Cannot assign {len(columns)} columns to {group_count} non-empty groups."
        )
    groups = group_names(group_count)
    assignments = [
        (column, groups[idx % group_count]) for idx, column in enumerate(columns)
    ]
    return groups, assignments


def select_tfs(genes: Sequence[str], policy: str | None) -> list[str]:
    if not genes:
        return []
    if policy is None:
        policy = "max(3, genes/5)"
    if policy != "max(3, genes/5)":
        raise ValueError(f"Unsupported TF count policy: {policy}")
    count = max(1, min(len(genes), max(3, len(genes) // 5)))
    return list(genes[:count])


def select_directed_edges(
    *,
    genes: Sequence[str],
    groups: Sequence[str] = (),
    tfs: Sequence[str],
    density: float,
    marker_count_per_group: int = 4,
    seed: int,
) -> list[tuple[str, str, float]]:
    candidates = [
        (source, target) for source in tfs for target in genes if source != target
    ]
    if not candidates:
        return []

    rng = random.Random(seed)
    seeded_edges = marker_coherent_edges(
        genes=genes,
        groups=groups,
        tfs=tfs,
        marker_count_per_group=marker_count_per_group,
        rng=rng,
    )
    seeded_pairs = {(source, target) for source, target, _score in seeded_edges}

    rng.shuffle(candidates)
    edge_count = max(1, min(len(candidates), round(len(candidates) * density)))
    edges = list(seeded_edges)
    for source, target in candidates:
        if len(edges) >= edge_count and len(edges) >= len(seeded_edges):
            break
        if (source, target) in seeded_pairs:
            continue
        score = rng.uniform(0.01, 1.0)
        edges.append((source, target, score))
    return edges


def marker_coherent_edges(
    *,
    genes: Sequence[str],
    groups: Sequence[str],
    tfs: Sequence[str],
    marker_count_per_group: int,
    rng: random.Random,
) -> list[tuple[str, str, float]]:
    """Create marker-focused prior edges for grouped synthetic benchmark inputs."""
    if not groups or not tfs:
        return []

    marker_count = max(1, min(len(genes), marker_count_per_group))
    edges: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for group_idx, _group in enumerate(groups):
        source = tfs[group_idx % len(tfs)]
        offset = (group_idx * marker_count) % len(genes)
        for marker_idx in range(marker_count):
            target = genes[(offset + marker_idx) % len(genes)]
            if source == target:
                continue
            pair = (source, target)
            if pair in seen:
                continue
            seen.add(pair)
            score = rng.uniform(0.75, 1.0)
            edges.append((source, target, score))
    return edges


def stable_seed(
    *,
    size: BenchmarkInputSize,
    profile: BenchmarkInputProfile,
    namespace: str,
) -> int:
    payload = {
        "seed": profile.seed,
        "size": {"genes": size.genes, "columns": size.columns},
        "profile": {
            "column_kind": profile.column_kind,
            "expression_profile": profile.expression_profile,
            "gene_id_source": profile.gene_id_source,
            "extras_provided": sorted(profile.extras_provided),
            "group_count": profile.group_count,
            "tf_count_policy": profile.tf_count_policy,
            "prior_density": profile.prior_density,
            "marker_count_per_group": profile.marker_count_per_group,
            "terms_of_interest": list(profile.terms_of_interest),
        },
        "namespace": namespace,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def resolved_prior_density(profile: BenchmarkInputProfile) -> float:
    if profile.prior_density is None:
        return 0.05
    density = float(profile.prior_density)
    if density < 0 or density > 1:
        raise ValueError("prior_density must be between 0 and 1.")
    return density


def _validate_profile(
    *,
    size: BenchmarkInputSize,
    profile: BenchmarkInputProfile,
) -> None:
    if size.genes < 1:
        raise ValueError("BenchmarkInputSize.genes must be >= 1.")
    if size.columns < 1:
        raise ValueError("BenchmarkInputSize.columns must be >= 1.")
    if profile.group_count < 0:
        raise ValueError("BenchmarkInputProfile.group_count must be >= 0.")
    gene_names(size.genes, source=profile.gene_id_source)
    unknown = sorted(set(profile.extras_provided).difference(GENERATED_EXTRA_INPUTS))
    if unknown:
        raise ValueError(f"Unknown synthetic extra input(s): {unknown}")

    needs_groups = any(
        input_key
        in {
            "column_phenotypes",
            "cluster_identities",
            "cluster_markers",
            "groups",
            "lineage_tree",
            "prior_grn_by_group",
            "pseudotime",
        }
        for input_key in profile.extras_provided
    )
    if needs_groups and profile.group_count < 1:
        raise ValueError("Grouped synthetic inputs require group_count >= 1.")
    if profile.group_count > size.columns:
        raise ValueError("group_count cannot exceed the number of expression columns.")
    if "column_phenotypes" in profile.extras_provided and (
        size.columns < profile.group_count * 2
    ):
        raise ValueError(
            "column_phenotypes generation requires at least two expression columns per phenotype."
        )
    if profile.marker_count_per_group < 1:
        raise ValueError("marker_count_per_group must be >= 1.")
    resolved_prior_density(profile)


def _as_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return int(value)


def _as_optional_density(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.05
    return max(0.0, min(1.0, float(value)))


def _unique_in_order(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))
