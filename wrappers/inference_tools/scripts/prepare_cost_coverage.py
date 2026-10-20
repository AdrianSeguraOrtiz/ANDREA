"""Register independent-axis representative calibration fixtures without Docker.

Old fixture bytes and their measured points are retained. New scMultiSim inputs
must come from an independently generated calibration source, not a validation
campaign. Observed human and Arabidopsis fixtures are only subsetted: expression
values, marker statistics, identities and prior edges are never synthesized.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import shutil

DEFAULT_RECIPES = Path(__file__).resolve().parents[1] / "cost_profiles" / "representative"
DERIVATION_VERSION = 1


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_table(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="") as stream:
        rows = list(csv.reader(stream, delimiter="\t"))
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError(f"Empty or inconsistent table: {path}")
    return rows[0], rows[1:]


def write_table(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def locate_fixture(roots: list[Path], fixture: dict) -> Path:
    for root in roots:
        candidate = root / fixture["fixture_key"]
        if all((candidate / name).is_file() and sha256(candidate / name) == digest
               for name, digest in fixture["input_sha256"].items()):
            return candidate
    raise ValueError(f"No checksum-matching source for {fixture['fixture_key']}")


def select_columns(columns: list[str], count: int, groups: dict[str, str]) -> list[str]:
    """Round-robin selection preserves every group; output keeps source order."""
    if count > len(columns):
        raise ValueError("Cannot invent additional cells from an observed fixture")
    if not groups:
        return columns[:count]
    if set(groups) != set(columns):
        raise ValueError("Group annotations must cover exactly the expression columns")
    levels = list(dict.fromkeys(groups[column] for column in columns))
    if count < len(levels):
        raise ValueError("Requested size cannot preserve all source groups")
    buckets = [[column for column in columns if groups[column] == level] for level in levels]
    chosen = []
    for index in range(max(map(len, buckets))):
        for bucket in buckets:
            if index < len(bucket) and len(chosen) < count:
                chosen.append(bucket[index])
    chosen_set = set(chosen)
    return [column for column in columns if column in chosen_set]


def select_genes(genes: list[str], count: int, tfs: list[str], markers: set[str],
                 prior: list[tuple[str, str]], min_targets: int = 0) -> list[str]:
    if count > len(genes):
        raise ValueError("Cannot invent additional genes from a source fixture")
    gene_set = set(genes)
    selected: set[str] = set()
    if markers:
        # MINI-EX requires the published marker and regulator universe. Keep it
        # fixed while the background-gene dimension changes.
        selected.update(markers.intersection(gene_set))
        selected.update(set(tfs).intersection(gene_set))
        if len(selected) > count:
            raise ValueError("Requested gene count would discard required markers/TFs")
    elif tfs:
        valid_tfs = [gene for gene in tfs if gene in gene_set]
        target_tf_count = max(1, round(count * len(valid_tfs) / len(genes)))
        # Spread the retained regulators across source order so a source sorted
        # by master/intermediate regulator class retains both classes.
        selected.update(valid_tfs[index * len(valid_tfs) // target_tf_count]
                        for index in range(target_tf_count))
    if min_targets:
        targets: dict[str, list[str]] = {}
        for regulator, target in prior:
            if regulator in gene_set and target in gene_set and regulator != target:
                targets.setdefault(regulator, []).append(target)
        suitable = [regulator for regulator in targets
                    if len(set(targets[regulator])) >= min_targets]
        if not suitable:
            raise ValueError("Source prior cannot satisfy the method's minimum targets")
        regulator = next((gene for gene in suitable if gene in selected), suitable[0])
        selected.add(regulator)
        selected.update(list(dict.fromkeys(targets[regulator]))[:min_targets])
    if len(selected) > count:
        raise ValueError("Requested gene count cannot preserve required prior endpoints")
    non_tfs = [gene for gene in genes if gene not in set(tfs)]
    for gene in non_tfs + genes:
        if len(selected) == count:
            break
        selected.add(gene)
    return [gene for gene in genes if gene in selected]


def subset_fixture(source: Path, template: Path, destination: Path, *, genes: int,
                   columns: int, input_names: list[str], params: dict) -> dict:
    """Subset expression and corresponding extras, preserving numeric tokens."""
    header, rows = read_table(source / "expression.tsv")
    source_genes = [row[0] for row in rows]
    source_columns = header[1:]
    if len(set(source_genes)) != len(source_genes) or len(set(source_columns)) != len(source_columns):
        raise ValueError("Source matrix has duplicate gene/cell identities")
    extra_dir = source / ("extra" if (source / "extra").is_dir() else "extras")
    tables = {}
    for name in input_names:
        if name.startswith("extra/") and name.endswith(".tsv"):
            tables[Path(name).name] = read_table(extra_dir / Path(name).name)
    group_path = extra_dir / "groups.tsv"
    phenotype_path = extra_dir / "column_phenotypes.tsv"
    groups = {}
    # Use group labels for selection even when the tool itself does not receive
    # them: all derived sizes retain the same mixture of source populations.
    if group_path.exists():
        _, group_rows = read_table(group_path)
        groups = {row[0]: row[1] for row in group_rows}
    elif phenotype_path.exists():
        _, phenotype_rows = read_table(phenotype_path)
        groups = {row[0]: row[1] for row in phenotype_rows}
    tf_path = extra_dir / "tf_list.txt"
    tfs = tf_path.read_text().splitlines() if tf_path.exists() else []
    marker_table = tables.get("cluster_markers.tsv")
    markers = ({row[marker_table[0].index("gene")] for row in marker_table[1]}
               if marker_table else set())
    prior_table = tables.get("prior_grn.tsv")
    prior = [(row[0], row[1]) for row in prior_table[1]] if prior_table else []
    selected_genes = select_genes(source_genes, genes, tfs, markers, prior,
                                 int(params.get("min_targets", 0)))
    selected_columns = select_columns(source_columns, columns, groups)
    gene_set, column_set = set(selected_genes), set(selected_columns)
    indices = [header.index(column) for column in selected_columns]
    destination.mkdir(parents=True, exist_ok=True)
    write_table(destination / "expression.tsv", [header[0]] + selected_columns,
                [[row[0]] + [row[index] for index in indices]
                 for row in rows if row[0] in gene_set])
    for name in input_names:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        filename = Path(name).name
        if name == "expression.tsv":
            continue
        if not name.startswith("extra/"):
            shutil.copyfile(template / name, target)
        elif filename == "tf_list.txt":
            selected = [gene for gene in tfs if gene in gene_set]
            if not selected:
                raise ValueError("Derived TF list would be empty")
            target.write_text("".join(gene + "\n" for gene in selected))
        elif filename in {"column_phenotypes.tsv", "groups.tsv"}:
            fields, entries = tables[filename]
            selected = [row for row in entries if row[0] in column_set]
            if {row[0] for row in selected} != column_set:
                raise ValueError(f"Incomplete derived cell annotations: {filename}")
            write_table(target, fields, selected)
        elif filename == "cluster_identities.tsv":
            fields, entries = tables[filename]
            selected_groups = {groups[column] for column in selected_columns}
            write_table(target, fields, [row for row in entries if row[0] in selected_groups])
        elif filename == "cluster_markers.tsv":
            fields, entries = tables[filename]
            gene_index = fields.index("gene")
            write_table(target, fields, [row for row in entries if row[gene_index] in gene_set])
        elif filename == "prior_grn.tsv":
            fields, entries = tables[filename]
            selected = [row for row in entries if row[0] in gene_set and row[1] in gene_set]
            if not selected:
                raise ValueError("Derived prior would be empty")
            write_table(target, fields, selected)
        else:
            raise ValueError(f"No scientifically defined subset operation for {name}")
    return {
        "version": DERIVATION_VERSION,
        "operation": "expression_submatrix_and_consistent_extra_filtering",
        "source_expression_sha256": sha256(source / "expression.tsv"),
        "source_extras_sha256": {path.name: sha256(path) for path in sorted(extra_dir.glob("*")) if path.is_file()},
        "selected_gene_ids": selected_genes,
        "selected_column_ids": selected_columns,
        "preserved_all_marker_and_tf_genes": bool(markers),
        "group_counts": {level: sum(groups.get(column) == level for column in selected_columns)
                         for level in sorted(set(groups.values()))},
    }


def prepare(*, roots: list[Path], scmultisim_source: Path, output: Path,
            recipes_dir: Path = DEFAULT_RECIPES, tools: set[str] | None = None) -> dict:
    if not (scmultisim_source / "source.json").is_file():
        raise ValueError("Independent source needs source.json provenance")
    provenance = json.loads((scmultisim_source / "source.json").read_text())
    if provenance.get("role") != "calibration" or not provenance.get("independent_seed"):
        raise ValueError("Source must explicitly identify an independent calibration seed")
    for name, digest in provenance.get("files", {}).items():
        candidate = (scmultisim_source / name).resolve()
        if not candidate.is_relative_to(scmultisim_source.resolve()) or not candidate.is_file() or sha256(candidate) != digest:
            raise ValueError(f"Independent calibration source checksum mismatch: {name}")
    output.mkdir(parents=True, exist_ok=True)
    summary = {"schema_version": "1.0", "source": provenance, "profiles": [], "new_fixtures": 0}
    updates = []
    for recipe_path in sorted(recipes_dir.glob("*.json")):
        if tools and recipe_path.stem not in tools:
            continue
        payload = json.loads(recipe_path.read_text())
        tool = recipe_path.stem
        for recipe in payload["profiles"]:
            originals = [fixture for fixture in recipe["fixtures"] if "derivation" not in fixture]
            if not originals:
                raise ValueError(f"Missing historical fixture for {tool}")
            sources = {}
            for fixture in originals:
                sources[fixture["fixture_key"]] = locate_fixture(roots + [output], fixture)
                destination = output / fixture["fixture_key"]
                for name in fixture["input_sha256"]:
                    target = destination / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if sources[fixture["fixture_key"]].resolve() != destination.resolve():
                        shutil.copyfile(sources[fixture["fixture_key"]] / name, target)
            template_fixture = originals[0]
            template = sources[template_fixture["fixture_key"]]
            if tool == "miniex3":
                gene_levels, column_levels = [4000, 8000, 12179], [40, 70, 100]
                source = template
            elif tool in {"dignet", "planet"}:
                # Both selected pretrained models require 100 features. This
                # observed-source profile has metacell=false: fewer cells are
                # incompatible, and additional observed cells cannot be invented.
                gene_levels, column_levels = [40, 60, 80], [100]
                source = template
            else:
                gene_levels, column_levels = [50, 100, 200], [100, 200, 400]
                source = scmultisim_source
            fixtures = copy.deepcopy(originals)
            existing_sizes = {(fixture["genes"], fixture["columns"]) for fixture in fixtures}
            params = recipe["benchmark_config"]["params_profile"]["resolved_params"]
            for genes in gene_levels:
                for columns in column_levels:
                    if (genes, columns) in existing_sizes:
                        continue
                    key = f"coverage_v1_{tool}_{recipe['profile_id']}_g{genes}_c{columns}"
                    destination = output / key
                    derivation = subset_fixture(source, template, destination, genes=genes, columns=columns,
                                                input_names=list(template_fixture["input_sha256"]), params=params)
                    (destination / "derivation.json").write_text(json.dumps(derivation, indent=2) + "\n")
                    fixture = {"fixture_key": key, "genes": genes, "columns": columns,
                               "input_sha256": {name: sha256(destination / name)
                                                for name in template_fixture["input_sha256"]},
                               "physical_index": 0,
                               "derivation": {"version": DERIVATION_VERSION,
                                              "source_expression_sha256": derivation["source_expression_sha256"],
                                              "record_sha256": sha256(destination / "derivation.json"),
                                              "role": "calibration"}}
                    fixtures.append(fixture)
                    summary["new_fixtures"] += 1
            recipe["fixtures"] = fixtures
            recipe["benchmark_config"]["sizes"] = [{"genes": fixture["genes"], "columns": fixture["columns"]}
                                                     for fixture in fixtures]
            recipe["calibration_design"] = {
                "version": 1, "kind": "independent_gene_column_grid", "gene_levels": gene_levels,
                "column_levels": column_levels,
                "scope": "Registered data family, method parameters, extras and resource assignment; no claim beyond measured dimensions.",
                "historical_fixtures_preserved": [fixture["fixture_key"] for fixture in originals],
                "source_selection": ("Published observed matrix; fixed complete marker/TF universe" if tool == "miniex3"
                                     else "Published observed matrix; no values synthesized" if tool in {"dignet", "planet"}
                                     else "Independent scMultiSim calibration seed; group-balanced submatrices"),
                "validation_policy": "Intermediate sizes are reserved for separate validation, not imported here.",
            }
            if tool in {"dignet", "planet"}:
                recipe["calibration_design"].update(
                    version=2, kind="fixed_column_gene_grid", fixed_axes={"columns": 100},
                    constraint_reason="Selected pretrained checkpoint requires exactly 100 expression features with metacell=false.",
                    scope="Observed human data; gene-size calibration at the fixed compatible cell count. No extrapolation over input cell count.")
            summary["profiles"].append({"tool": tool, "profile_id": recipe["profile_id"],
                                        "sizes": recipe["benchmark_config"]["sizes"],
                                        "historical_fixtures": len(originals), "new_fixtures": len(fixtures)-len(originals)})
        updates.append((recipe_path, payload))
    # Register only after every fixture has passed integrity/semantic checks.
    for path, payload in updates:
        path.write_text(json.dumps(payload, indent=2) + "\n")
    (output / "coverage-preparation.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, action="append", required=True)
    parser.add_argument("--scmultisim-source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--recipes-dir", type=Path, default=DEFAULT_RECIPES)
    parser.add_argument("--tool", action="append", help="Prepare only this tool's recipes (repeatable).")
    args = parser.parse_args()
    summary = prepare(roots=args.fixture_root, scmultisim_source=args.scmultisim_source,
                      output=args.output_root, recipes_dir=args.recipes_dir, tools=set(args.tool or []))
    print(json.dumps({"profiles": len(summary["profiles"]), "new_fixtures": summary["new_fixtures"]}))


if __name__ == "__main__":
    main()
