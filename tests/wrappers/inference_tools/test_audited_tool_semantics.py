"""Regressions for scientific catalog audit: identifiers and valid model domains."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "wrappers/inference_tools/tools"
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts/templates/python"))


def load_wrapper(tool: str):
    name = f"audit_semantics_{tool}"
    spec = importlib.util.spec_from_file_location(name, TOOLS / tool / "run_tool.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with mock.patch.dict(sys.modules, {"anndata": types.ModuleType("anndata")}):
        spec.loader.exec_module(module)
    module.np = np
    module.pd = pd
    return module


def test_simic_preserves_text_ids_and_rejects_empty_variable_selection(tmp_path):
    wrapper = load_wrapper("simic")
    expression = tmp_path / "expression.tsv"
    expression.write_text("gene\tC1\tC2\n001\t1\t2\nNA\t3\t4\n")
    assert wrapper._read_expression_tsv(expression).index.tolist() == ["001", "NA"]
    spec = json.loads(
        (ROOT / "andrea/catalog_inference_tools/tools/simic/toolspec.json").read_text()
    )
    params = {key: value["default"] for key, value in spec["params"].items()}
    for key in ("num_TFs", "num_target_genes"):
        with pytest.raises(ValueError, match="positive integer"):
            wrapper._resolve_params({**params, key: 0})
    for seed in (-1, 2**32):
        with pytest.raises(ValueError):
            wrapper._resolve_random_seed(seed)


def test_pyscenic_keeps_na_gene_and_positive_raw_importance(tmp_path):
    wrapper = load_wrapper("pyscenic")
    source = tmp_path / "raw.tsv"
    source.write_text("TF\ttarget\timportance\nNA\t001\t3.2\nNA\tNA\t9\n001\tNA\t0\n")
    output = tmp_path / "network.csv"
    assert wrapper._convert_adjacencies(source, output) == 1
    result = pd.read_csv(output, dtype=str, keep_default_na=False)
    assert result.iloc[0].to_dict() == {
        "source": "NA",
        "target": "001",
        "score": "3.2",
        "sign": "?",
        "evidence": "association",
        "context": "global",
    }


def test_scgenerai_keeps_numeric_gene_ids_in_native_cell_network(tmp_path):
    wrapper = load_wrapper("scgenerai")
    (tmp_path / "results").mkdir()
    (tmp_path / "results/LRP_0_0.csv").write_text(
        "LRP,source_gene,target_gene\n0.5,001,NA\n0.5,NA,001\n"
    )
    result = wrapper.convert_raw_results(tmp_path, ["cell-A"])
    assert len(result) == 1
    assert result.iloc[0][["source", "target", "context"]].tolist() == [
        "001",
        "NA",
        "column:cell-A",
    ]


def test_scing_retains_na_gene_in_merged_network(tmp_path):
    wrapper = load_wrapper("scing")
    source = tmp_path / "raw.csv"
    source.write_text("source,target,importance\nNA,001,0.3\n")
    expression = wrapper.ExpressionInput(pd.DataFrame(), ["NA", "001"], [])
    output = tmp_path / "network.csv"
    assert wrapper._convert_network(source, output, expression) == 1


def test_scsgl_rejects_impossible_overlapping_sign_densities():
    wrapper = load_wrapper("scsgl")
    with pytest.raises(ValueError, match="disjoint signed edges"):
        wrapper._resolve_params({"pos_density": 0.7, "neg_density": 0.6})
    assert (
        wrapper._resolve_params({"pos_density": 0.4, "neg_density": 0.4}).pos_density
        == 0.4
    )


def test_scmtni_preserves_signed_conditional_coefficients(tmp_path):
    wrapper = load_wrapper("scmtni")
    raw = tmp_path / "A" / "fold0"
    raw.mkdir(parents=True)
    (raw / "var_mb_pw_k50.txt").write_text(
        "TF_A\tG_A\t-2.5\nTF_A\tH_A\t0.4\nTF_A\tZ_A\t0\n"
    )
    rows = wrapper._collect_network_rows(
        raw_output_root=tmp_path,
        clusters=["A"],
        max_regulators=50,
        execution_mode="global",
    )
    assert [(row["target"], row["score"], row["sign"]) for row in rows] == [
        ("G", 2.5, "-"),
        ("H", 0.4, "+"),
    ]


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript is unavailable")
@pytest.mark.parametrize("tool", ["ppcor", "tigress", "scminer"])
def test_r_expression_readers_preserve_public_text_gene_ids(tool, tmp_path):
    expression = tmp_path / "expression.tsv"
    expression.write_text("gene\tC1\tC2\n001\t1\t2\nNA\t3\t4\n")
    # Load only top-level helper definitions, avoiding installed upstream packages
    # and main(). This executes each real reader with the same public input.
    script = """
    args <- commandArgs(trailingOnly=TRUE)
    for (entry in parse(args[[1]])) {
      if (is.call(entry) && identical(entry[[1]], as.name('<-'))) eval(entry)
    }
    x <- read_expression_tsv(args[[2]])
    ids <- if (args[[3]] == 'tigress') colnames(x) else rownames(x)
    stopifnot(identical(ids, c('001', 'NA')))
    """
    result = subprocess.run(
        [
            "Rscript",
            "-e",
            script,
            str(TOOLS / tool / "run_tool.R"),
            str(expression),
            tool,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
