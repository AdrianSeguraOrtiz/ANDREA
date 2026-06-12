from __future__ import annotations

from pathlib import Path

import pytest

from andrea.core.commands.compare_networks import bundles as compare_bundles
from andrea.core.commands.evaluate_inference import bundles as evaluate_bundles
from andrea.core.commands.generate_data import bundles as generate_bundles
from andrea.core.commands.infer_network import bundles as infer_bundles


def touch(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def paths(resolution) -> set[str]:
    return {source.virtual_path for source in resolution.sources}


def test_no_command_exposes_legacy_light_bundle() -> None:
    assert "light" not in generate_bundles.supported_bundles()
    assert "light" not in infer_bundles.supported_bundles()
    assert "light" not in evaluate_bundles.supported_bundles()
    assert "light" not in compare_bundles.supported_bundles()


def test_generate_data_analysis_is_minimal_truth_bundle(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    dataset = root / "datasets" / "dataset_01"
    other_dataset = root / "datasets" / "dataset_02"
    touch(root / "benchmark-manifest.json", "{}\n")
    touch(root / "preflight-report.json", "{}\n")
    touch(dataset / "ground-truth-manifest.json", "{}\n")
    touch(dataset / "truth" / "networks.csv", "source,target\n")
    touch(dataset / "truth" / "gene_universe.txt", "g1\n")
    touch(other_dataset / "ground-truth-manifest.json", "{}\n")
    touch(other_dataset / "truth" / "networks.csv", "source,target\n")
    touch(other_dataset / "truth" / "gene_universe.txt", "g2\n")
    touch(dataset / "dataset-manifest.json", "{}\n")
    touch(dataset / "expression.tsv", "gene\tc1\n")
    touch(dataset / "extras" / "groups.tsv", "c1\tg\n")
    touch(dataset / "native" / "raw.tsv", "native\n")
    touch(dataset / "provenance" / "raw" / "large-native-object.rds", "native\n")

    assert generate_bundles.supported_bundles() == ("full", "analysis", "report")
    full = generate_bundles.resolve_bundle(bundle_id="full", benchmark_root=root)
    assert full.available
    assert {
        "benchmark-manifest.json",
        "datasets/dataset_01/expression.tsv",
        "datasets/dataset_01/native/raw.tsv",
        "datasets/dataset_01/provenance/raw/large-native-object.rds",
    }.issubset(paths(full))

    assert generate_bundles.analysis_dataset_ids(benchmark_root=root) == (
        "dataset_01",
        "dataset_02",
    )
    with pytest.raises(ValueError, match="dataset_id is required"):
        generate_bundles.resolve_bundle(bundle_id="analysis", benchmark_root=root)
    with pytest.raises(ValueError, match="Unknown dataset_id"):
        generate_bundles.resolve_bundle(
            bundle_id="analysis",
            benchmark_root=root,
            dataset_id="missing_dataset",
        )

    analysis = generate_bundles.resolve_bundle(
        bundle_id="analysis", benchmark_root=root, dataset_id="dataset_01"
    )
    assert analysis.available
    assert paths(analysis) == {
        "ground-truth-manifest.json",
        "truth/networks.csv",
        "truth/gene_universe.txt",
    }
    assert "expression.tsv" not in paths(analysis)
    assert "extras/groups.tsv" not in paths(analysis)
    assert "provenance/raw/large-native-object.rds" not in paths(analysis)
    assert "datasets/dataset_02/truth/networks.csv" not in paths(analysis)

    report = generate_bundles.resolve_bundle(bundle_id="report", benchmark_root=root)
    assert report.available
    assert "benchmark-manifest.json" in paths(report)
    assert "datasets/dataset_01/dataset-manifest.json" in paths(report)
    assert "datasets/dataset_01/expression.tsv" not in paths(report)
    assert "datasets/dataset_01/native/raw.tsv" not in paths(report)

    with pytest.raises(ValueError, match="Unsupported bundle_id"):
        generate_bundles.resolve_bundle(bundle_id="not_a_bundle", benchmark_root=root)


def test_generate_data_analysis_reports_missing_truth_files(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    dataset = root / "datasets" / "dataset_01"
    touch(dataset / "ground-truth-manifest.json", "{}\n")

    analysis = generate_bundles.resolve_bundle(
        bundle_id="analysis", benchmark_root=root, dataset_id="dataset_01"
    )

    assert not analysis.available
    assert paths(analysis) == {"ground-truth-manifest.json"}
    assert "truth/networks.csv" in analysis.missing_required
    assert "truth/gene_universe.txt" in analysis.missing_required


def test_infer_network_bundles_split_analysis_report_and_graphs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    for rel in (
        "run_report.json",
        "merged_network_raw.csv",
        "merged_network_normalized.csv",
        "plan.json",
        "preflight_report.json",
        "runtime/execution_state.json",
        "tools/tool_01/resolved_params.json",
        "tools/tool_01/resolved_execution.json",
        "tools/tool_01/io/expression.tsv",
        "tools/tool_01/work/native-output.tsv",
        "merged_network_raw.gexf",
        "merged_network_raw.graphml",
        "merged_network_normalized.gexf",
        "merged_network_normalized.graphml",
        "merged_network_normalized_cytoscape.py",
    ):
        touch(root / rel)

    assert infer_bundles.supported_bundles() == (
        "full",
        "analysis",
        "report",
        "graphs",
    )
    full = infer_bundles.resolve_bundle(bundle_id="full", run_dir=root)
    assert full.available
    assert {
        "tools/tool_01/io/expression.tsv",
        "tools/tool_01/work/native-output.tsv",
        "merged_network_raw.gexf",
    }.issubset(paths(full))

    analysis = infer_bundles.resolve_bundle(bundle_id="analysis", run_dir=root)
    assert analysis.available
    assert paths(analysis) == {
        "run_report.json",
        "merged_network_raw.csv",
        "merged_network_normalized.csv",
    }
    assert "merged_network_raw.gexf" not in paths(analysis)
    assert "runtime/execution_state.json" not in paths(analysis)
    assert "tools/tool_01/work/native-output.tsv" not in paths(analysis)

    report = infer_bundles.resolve_bundle(bundle_id="report", run_dir=root)
    assert report.available
    assert "tools/tool_01/resolved_params.json" in paths(report)
    assert "tools/tool_01/resolved_execution.json" in paths(report)
    assert "tools/tool_01/io/expression.tsv" not in paths(report)
    assert "tools/tool_01/work/native-output.tsv" not in paths(report)
    assert "merged_network_raw.csv" not in paths(report)

    graphs = infer_bundles.resolve_bundle(bundle_id="graphs", run_dir=root)
    assert graphs.available
    assert paths(graphs) == {
        "merged_network_raw.gexf",
        "merged_network_raw.graphml",
        "merged_network_normalized.gexf",
        "merged_network_normalized.graphml",
        "merged_network_normalized_cytoscape.py",
    }

    with pytest.raises(ValueError, match="Unsupported bundle_id"):
        infer_bundles.resolve_bundle(bundle_id="not_a_bundle", run_dir=root)


def test_infer_network_graph_bundle_requires_at_least_one_graph(tmp_path: Path) -> None:
    root = tmp_path / "run"
    touch(root / "run_report.json")

    graphs = infer_bundles.resolve_bundle(bundle_id="graphs", run_dir=root)

    assert not graphs.available
    assert paths(graphs) == set()
    assert graphs.missing_required == ("one or more graph export files",)


def test_infer_network_full_waits_for_graph_exports_but_report_does_not(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    for rel in (
        "run_report.json",
        "merged_network_raw.csv",
        "merged_network_normalized.csv",
    ):
        touch(root / rel)

    full = infer_bundles.resolve_bundle(bundle_id="full", run_dir=root)
    report = infer_bundles.resolve_bundle(bundle_id="report", run_dir=root)
    analysis = infer_bundles.resolve_bundle(bundle_id="analysis", run_dir=root)

    assert not full.available
    assert "merged_network_raw.gexf" in full.missing_required
    assert "merged_network_normalized_cytoscape.py" in full.missing_required
    assert report.available
    assert analysis.available


def test_evaluate_inference_analysis_is_evaluation_report_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evaluation"
    for rel in (
        "evaluation_report.json",
        "metrics.csv",
        "pairings.csv",
        "evaluation_view.html",
        "input/inference/run_report.json",
        "input/inference/merged_network_raw.csv",
        "input/truth/truth/networks.csv",
    ):
        touch(root / rel)

    assert evaluate_bundles.supported_bundles() == ("full", "analysis", "report")
    full = evaluate_bundles.resolve_bundle(bundle_id="full", evaluation_dir=root)
    assert full.available
    assert {
        "input/inference/run_report.json",
        "input/inference/merged_network_raw.csv",
        "input/truth/truth/networks.csv",
    }.issubset(paths(full))

    analysis = evaluate_bundles.resolve_bundle(
        bundle_id="analysis", evaluation_dir=root
    )
    assert analysis.available
    assert paths(analysis) == {"evaluation_report.json"}
    assert "metrics.csv" not in paths(analysis)
    assert "pairings.csv" not in paths(analysis)
    assert "input/inference/run_report.json" not in paths(analysis)

    report = evaluate_bundles.resolve_bundle(bundle_id="report", evaluation_dir=root)
    assert report.available
    assert paths(report) == {
        "evaluation_report.json",
        "metrics.csv",
        "pairings.csv",
        "evaluation_view.html",
    }
    assert "input/inference/run_report.json" not in paths(report)

    with pytest.raises(ValueError, match="Unsupported bundle_id"):
        evaluate_bundles.resolve_bundle(bundle_id="not_a_bundle", evaluation_dir=root)


def test_evaluate_inference_analysis_requires_evaluation_report(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evaluation"
    touch(root / "metrics.csv")

    analysis = evaluate_bundles.resolve_bundle(
        bundle_id="analysis", evaluation_dir=root
    )

    assert not analysis.available
    assert paths(analysis) == set()
    assert analysis.missing_required == ("evaluation_report.json",)


def test_compare_networks_report_excludes_frozen_inputs(tmp_path: Path) -> None:
    root = tmp_path / "comparison"
    for rel in (
        "comparison-request.json",
        "comparison_report.json",
        "network_index.csv",
        "edge_scores.csv",
        "distances.csv",
        "distance_coordinates.csv",
        "comparison.sqlite",
        "comparison_view.html",
        "input/sources/source_1/run_report.json",
        "input/sources/source_1/merged_network_normalized.csv",
        "input/sources/source_1/evaluation_report.json",
    ):
        touch(root / rel)

    assert compare_bundles.supported_bundles() == ("full", "report")
    full = compare_bundles.resolve_bundle(bundle_id="full", comparison_dir=root)
    assert full.available
    assert {
        "input/sources/source_1/run_report.json",
        "input/sources/source_1/merged_network_normalized.csv",
        "input/sources/source_1/evaluation_report.json",
    }.issubset(paths(full))

    report = compare_bundles.resolve_bundle(bundle_id="report", comparison_dir=root)
    assert report.available
    assert paths(report) == {
        "comparison-request.json",
        "comparison_report.json",
        "network_index.csv",
        "distances.csv",
        "distance_coordinates.csv",
        "comparison.sqlite",
        "comparison_view.html",
    }
    assert "input/sources/source_1/run_report.json" not in paths(report)
    assert "input/sources/source_1/merged_network_normalized.csv" not in paths(report)

    with pytest.raises(ValueError, match="Unsupported bundle_id"):
        compare_bundles.resolve_bundle(bundle_id="analysis", comparison_dir=root)
    with pytest.raises(ValueError, match="Unsupported bundle_id"):
        compare_bundles.resolve_bundle(bundle_id="not_a_bundle", comparison_dir=root)


def test_compare_networks_report_bundle_is_available_before_edge_score_csv(
    tmp_path: Path,
) -> None:
    root = tmp_path / "comparison"
    for rel in (
        "comparison-request.json",
        "comparison_report.json",
        "network_index.csv",
        "distances.csv",
        "distance_coordinates.csv",
        "comparison.sqlite",
        "comparison_view.html",
    ):
        touch(root / rel)

    report = compare_bundles.resolve_bundle(bundle_id="report", comparison_dir=root)
    full = compare_bundles.resolve_bundle(bundle_id="full", comparison_dir=root)

    assert report.available
    assert "edge_scores.csv" not in paths(report)
    assert not full.available
    assert full.missing_required == ("edge_scores.csv",)
