"""Calibration grids need independent axes and scientifically aligned extras."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "prepare_cost_coverage", ROOT / "wrappers/inference_tools/scripts/prepare_cost_coverage.py")
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)


def test_every_baseline_profile_has_independent_gene_and_cell_axes():
    recipes = ROOT / "wrappers/inference_tools/cost_profiles"
    tools = ROOT / "andrea/catalog_inference_tools/tools"
    assert {path.stem for path in recipes.glob("*.json")} == {
        path.parent.name for path in tools.glob("*/toolspec.json")}
    for path in recipes.glob("*.json"):
        config = json.loads(path.read_text())
        for profile in config["profiles"]:
            design = profile.get("calibration_design", config["calibration_design"])
            required = {(gene, column) for gene in design["gene_levels"]
                        for column in design["column_levels"]}
            if design.get("kind") == "fixed_column_gene_grid":
                assert path.stem in {"dignet", "planet"}
                assert design["fixed_axes"] == {"columns": 100}
                assert design["column_levels"] == [100]
                assert profile["param_overrides"]["metacell"] is False
                assert len(required) >= 3
            else:
                assert len(required) >= 9, path.name
            sizes = {tuple(map(int, size.split("x")))
                     for size in profile.get("sizes", config["sizes"])}
            assert required.issubset(sizes), (path.name, profile["id"])


def test_submatrix_keeps_group_balance_prior_endpoints_and_numeric_tokens(tmp_path):
    source = tmp_path / "source"
    template = tmp_path / "template"
    destination = tmp_path / "derived"
    (source / "extra").mkdir(parents=True)
    template.mkdir()
    coverage.write_table(source / "expression.tsv", ["gene", "a1", "a2", "b1", "b2"],
                         [[f"G{i}", "0.0010", "2.0000", "3.0", "4"] for i in range(1, 9)])
    coverage.write_table(source / "extra/groups.tsv", ["column", "cluster"],
                         [["a1", "a"], ["a2", "a"], ["b1", "b"], ["b2", "b"]])
    coverage.write_table(source / "extra/column_phenotypes.tsv", ["column", "phenotype", "order"],
                         [["a1", "a", "0"], ["a2", "a", "0"], ["b1", "b", "1"], ["b2", "b", "1"]])
    (source / "extra/tf_list.txt").write_text("G1\nG2\n")
    coverage.write_table(source / "extra/prior_grn.tsv", ["source", "target", "score"],
                         [["G1", "G3", "1.00"], ["G1", "G4", "0.500"], ["G2", "G8", "1.0"]])
    (template / "params.json").write_text('{"seed": 13}\n')
    (template / "execution.json").write_text('{"mode": "group_native"}\n')
    names = ["expression.tsv", "params.json", "execution.json", "extra/groups.tsv",
             "extra/column_phenotypes.tsv", "extra/tf_list.txt", "extra/prior_grn.tsv"]
    info = coverage.subset_fixture(source, template, destination, genes=4, columns=2,
                                   input_names=names, params={"min_targets": 2})
    assert info["group_counts"] == {"a": 1, "b": 1}
    header, expression = coverage.read_table(destination / "expression.tsv")
    assert header == ["gene", "a1", "b1"]
    assert expression[0][1:] == ["0.0010", "3.0"]
    gene_ids = {row[0] for row in expression}
    _, prior = coverage.read_table(destination / "extra/prior_grn.tsv")
    assert prior == [["G1", "G3", "1.00"], ["G1", "G4", "0.500"]]
    assert all(row[0] in gene_ids and row[1] in gene_ids for row in prior)
    assert (destination / "params.json").read_bytes() == (template / "params.json").read_bytes()
    assert (destination / "extra/tf_list.txt").read_text() == "G1\n"


def test_gene_selection_preserves_marker_and_regulator_universe():
    assert coverage.select_genes(["TF", "A", "B", "C", "D"], 3, ["TF"], {"C"}, []) == ["TF", "A", "C"]
    with pytest.raises(ValueError, match="discard required markers"):
        coverage.select_genes(["TF", "A", "B"], 1, ["TF"], {"A"}, [])


def test_subsetting_never_invents_observed_rows_or_cells():
    with pytest.raises(ValueError, match="additional genes"):
        coverage.select_genes(["G1"], 2, [], set(), [])
    with pytest.raises(ValueError, match="additional cells"):
        coverage.select_columns(["c1"], 2, {})


def test_validation_source_cannot_be_imported_as_calibration(tmp_path):
    source = tmp_path / "validation"
    source.mkdir()
    (source / "source.json").write_text(json.dumps({"role": "validation", "independent_seed": 42}))
    with pytest.raises(ValueError, match="independent calibration"):
        coverage.prepare(roots=[], scmultisim_source=source, output=tmp_path / "out")


def test_pretrained_direct_and_metacell_sizes_follow_checkpoint_contract():
    for tool in ("dignet", "planet"):
        baseline = json.loads((ROOT / f"wrappers/inference_tools/cost_profiles/{tool}.json").read_text())
        assert len(baseline["profiles"]) == 2
        for profile in baseline["profiles"]:
            params = profile["param_overrides"]
            for size in profile.get("sizes", baseline["sizes"]):
                _, cells = map(int, size.split("x"))
                effective = min(cells, params.get("metacell_count", 100)) if params["metacell"] else cells
                assert effective == 100
                if params["metacell"]:
                    assert cells > 100
        representative = json.loads((ROOT / f"wrappers/inference_tools/cost_profiles/representative/{tool}.json").read_text())
        for profile in representative["profiles"]:
            assert profile["calibration_design"]["fixed_axes"] == {"columns": 100}
            assert {fixture["columns"] for fixture in profile["fixtures"]} == {100}
            assert {fixture["genes"] for fixture in profile["fixtures"]} == {40, 60, 80}


def test_binary_prior_policy_changes_only_weights_and_preserves_legacy_default(tmp_path):
    import importlib
    import sys
    import types
    from dataclasses import replace
    from unittest.mock import patch
    # Both simulator helpers and collected tests use generic package names.
    # Load these helpers in a private namespace and restore the import cache.
    namespace = "_andrea_cost_coverage_test_helpers"
    package = types.ModuleType(namespace)
    package.__path__ = [str(ROOT / "wrappers/inference_tools/scripts/shared")]
    with patch.dict(sys.modules, {namespace: package}):
        inputs = importlib.import_module(namespace + ".benchmark_inputs")
        resolver = importlib.import_module(namespace + ".benchmark_profiles")
    BenchmarkInputProfile = inputs.BenchmarkInputProfile
    BenchmarkInputSize = inputs.BenchmarkInputSize
    write_benchmark_io_dir = inputs.write_benchmark_io_dir
    resolve_benchmark_profiles = resolver.resolve_benchmark_profiles
    profiles = resolve_benchmark_profiles(tool_id="scregulate", catalog_tools_root=ROOT / "andrea/catalog_inference_tools/tools")
    selected = next(profile for profile in profiles if profile.profile_id.endswith("binary_prior_v2"))
    assert selected.input_profile["prior_score_policy"] == "binary"
    assert all("prior_score_policy" not in profile.input_profile
               for profile in profiles if profile is not selected)
    binary = BenchmarkInputProfile.from_cost_input_profile(selected.input_profile, seed=12345)
    for genes in (50, 100, 200):
        for cells in (20, 40, 80):
            size = BenchmarkInputSize(genes, cells)
            weighted_root, binary_root = tmp_path / f"weighted-{genes}-{cells}", tmp_path / f"binary-{genes}-{cells}"
            write_benchmark_io_dir(weighted_root, size, replace(binary, prior_score_policy="uniform_positive"))
            write_benchmark_io_dir(binary_root, size, binary)
            assert (weighted_root / "expression.tsv").read_bytes() == (binary_root / "expression.tsv").read_bytes()
            _, weighted = coverage.read_table(weighted_root / "extra/prior_grn.tsv")
            _, unit = coverage.read_table(binary_root / "extra/prior_grn.tsv")
            assert [row[:2] for row in weighted] == [row[:2] for row in unit]
            assert unit and {float(row[2]) for row in unit} == {1.0}
