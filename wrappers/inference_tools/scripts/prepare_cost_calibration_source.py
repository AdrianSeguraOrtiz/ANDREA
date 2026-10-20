"""Generate independent scMultiSim inputs for inference runtime calibration.

This maintenance dataset is separate from scientific validation inputs. It is
not an accuracy benchmark. The source and its checksums are kept in the chosen
output directory, which should be outside version control.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wrappers"))
from benchmark_support import atomic_json


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(output: Path, genes: int = 200, cells: int = 400,
            seed: int = 202610091) -> Path:
    import pandas as pd
    from andrea.core.commands.generate_data import (
        plan_generate_data_request, run_generate_data,
    )

    output = output.resolve()
    result_path = output / "source.json"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        if result["configuration"] != {"genes": genes, "cells": cells, "seed": seed}:
            raise ValueError("Existing calibration source has a different configuration")
        for name, expected in result["files"].items():
            if digest(output / name) != expected:
                raise ValueError(f"Existing calibration source changed: {name}")
        print(f"Reusing verified calibration source: {output}", flush=True)
        return output
    if genes < 50 or genes % 10 or cells < 100 or cells % 2:
        raise ValueError("Use >=50 genes divisible by ten and >=100 even cells")
    output.mkdir(parents=True, exist_ok=True)
    recipe = output / "recipe"
    recipe.mkdir(exist_ok=True)
    rng = random.Random(seed)
    names = [f"G{i:03d}" for i in range(1, genes + 1)]
    n = genes // 10
    masters, intermediate = names[:n], names[n:2*n]
    edges = []
    for target in intermediate:
        for source, sign in zip(rng.sample(masters, 2), [1, -1]):
            edges.append({"target": target, "regulator": source,
                          "effect": sign * rng.uniform(1, 3)})
    for target in names[2*n:]:
        for source, sign in [(rng.choice(masters), 1), (rng.choice(intermediate), -1)]:
            edges.append({"target": target, "regulator": source,
                          "effect": sign * rng.uniform(1, 3)})
    graph = pd.DataFrame(edges).sort_values(["target", "regulator"])
    graph.to_csv(recipe / "regulatory_network.tsv", sep="\t", index=False,
                 float_format="%.17g")
    (recipe / "tree.nwk").write_text("((Tip:1)Mid:1)Root;\n")
    scenario = {
        "schema_version": "1.0", "id": "independent_cost_calibration",
        "data_axes": {"measurement": "rna_expression", "resolution": "single_cell",
                      "column_kind": "cells", "experimental_design": "differentiation"},
        "truth_requirements": {"contexts": ["global", "group", "column"]},
        "organism": {"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
        "requested_extras": ["groups", "column_phenotypes", "pseudotime"],
        "inputs": {"regulatory_network": {"path": str(recipe / "regulatory_network.tsv")},
                   "tree_newick": {"path": str(recipe / "tree.nwk")}},
        "base_seed": seed,
    }
    runs = {"schema_version": "1.0", "runs": [{
        "run_id": "cost_calibration", "simulator_id": "scmultisim",
        "replicates": 1, "base_seed": seed,
        "native_outputs": ["true_counts", "cell_meta"],
        "params": {"grn_source": "input_tsv", "num_genes": genes + 1,
                   "num_cells": cells * 2, "unregulated_gene_ratio": 0.0,
                   "tree_preset": "input_newick", "population_mode": "continuous",
                   "num_cifs": 20, "technical_noise": {"enabled": False},
                   "batch_effect": {"enabled": False}, "velocity": {"enabled": False},
                   "dynamic_grn": {"enabled": True}},
    }]}
    atomic_json(recipe / "scenario.json", scenario)
    atomic_json(recipe / "runs.json", runs)
    generated_receipt = output / "generation.json"
    if generated_receipt.exists():
        receipt = json.loads(generated_receipt.read_text())
        if (receipt["scenario_sha256"] != digest(recipe / "scenario.json")
                or receipt["runs_sha256"] != digest(recipe / "runs.json")):
            raise ValueError("Existing raw generation used a different recipe")
        manifest = Path(receipt["manifest"])
    else:
        plan = plan_generate_data_request(
            scenario_request_path=recipe / "scenario.json",
            simulator_runs_path=recipe / "runs.json",
            output_path=recipe / "plan.json", max_parallel_tasks=1,
            max_cores=2, max_ram_gb=8,
        )
        generated = run_generate_data(plan_path=plan, output_dir=output / "generation",
                                      show_progress=True)
        manifests = list((generated / "datasets").glob("*/dataset-manifest.json"))
        if len(manifests) != 1:
            raise RuntimeError("Expected exactly one generated calibration dataset")
        manifest = manifests[0]
        atomic_json(generated_receipt, {"manifest": str(manifest),
                                      "scenario_sha256": digest(recipe / "scenario.json"),
                                      "runs_sha256": digest(recipe / "runs.json")})
    source = manifest.parent
    expression = pd.read_csv(source / "expression.tsv", sep="\t", index_col=0)
    groups = pd.read_csv(source / "extras/groups.tsv", sep="\t", dtype=str)
    counts = groups.groupby("cluster").size().to_dict()
    ordered = ["pop_2_3", "pop_3_1"]
    if any(counts.get(group, 0) < cells // 2 for group in ordered):
        raise ValueError(f"Insufficient native cells in the two lineage segments: {counts}")
    selected = []
    for group in ordered:
        candidates = sorted(groups.loc[groups.cluster == group, "column"])
        selected.extend(sorted(rng.sample(candidates, cells // 2)))
    expression = expression.loc[names, selected]
    if expression.shape != (genes, cells) or not (expression.var(axis=1) > 0).all():
        raise ValueError("Invalid prepared expression geometry or constant genes")
    extra = output / "extra"
    extra.mkdir(exist_ok=True)
    expression.to_csv(output / "expression.tsv", sep="\t", float_format="%.17g")
    groups = groups.set_index("column").loc[selected].reset_index()
    groups.to_csv(extra / "groups.tsv", sep="\t", index=False)
    phenotypes = groups.rename(columns={"cluster": "phenotype"})
    phenotypes["order"] = phenotypes.phenotype.map(dict(zip(ordered, [0, 1])))
    phenotypes.to_csv(extra / "column_phenotypes.tsv", sep="\t", index=False)
    tfs = sorted(set(graph.regulator))
    (extra / "tf_list.txt").write_text("\n".join(tfs) + "\n")
    prior = graph.rename(columns={"regulator": "source", "effect": "score"})
    prior[["source", "target", "score"]].to_csv(extra / "prior_grn.tsv", sep="\t",
                                                index=False, float_format="%.17g")
    files = [output / "expression.tsv", *sorted(extra.iterdir()),
             recipe / "regulatory_network.tsv", recipe / "tree.nwk",
             recipe / "scenario.json", recipe / "runs.json", manifest]
    atomic_json(result_path, {
        "configuration": {"genes": genes, "cells": cells, "seed": seed},
        "role": "calibration", "independent_seed": seed,
        "purpose": "Independent runtime calibration only; never imported from validation results",
        "raw_manifest": str(manifest), "groups": ordered, "group_counts": counts,
        "tf_count": len(tfs), "generator_sha256": digest(Path(__file__)),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                              text=True).strip(),
        "files": {str(p.relative_to(output)): digest(p) for p in files},
    })
    print(f"Prepared independent {genes}x{cells} calibration source: {output}", flush=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--genes", type=int, default=200)
    parser.add_argument("--cells", type=int, default=400)
    parser.add_argument("--seed", type=int, default=202610091)
    args = parser.parse_args()
    prepare(args.output, args.genes, args.cells, args.seed)
