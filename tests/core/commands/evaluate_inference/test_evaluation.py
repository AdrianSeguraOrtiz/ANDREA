from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from andrea.core.commands.evaluate_inference import evaluate_inference
from andrea.core.commands.evaluate_inference.evaluation import (
    NetworkRow,
    _aggregate_rows,
    _auroc,
    _average_precision,
    _context_counts_by_family,
    _load_inferred_rows,
    _sparse_auroc,
    _sparse_average_precision,
    _top_truth_count_stats,
)


class EvaluateInferenceCoreTests(unittest.TestCase):
    @staticmethod
    def _manifest(
        *,
        dataset_id: str = "toy",
        simulator_id: str = "toy_sim",
        data_axes: dict[str, str] | None = None,
        truth_contexts: list[str] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "dataset_id": dataset_id,
            "simulator_id": simulator_id,
            "data_axes": data_axes
            or {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "steady_state",
            },
            "truth_requirements": {
                "contexts": truth_contexts or ["global"],
            },
            "outputs": outputs
            or {
                "gene_universe": "truth/gene_universe.txt",
                "networks": "truth/networks.csv",
            },
        }

    def test_context_family_counts_include_public_context_families(self) -> None:
        self.assertEqual(
            _context_counts_by_family(
                [
                    "global",
                    "group:a",
                    "column:c1",
                    "sample:s1",
                    "timepoint:t1",
                    "perturbation:ko",
                    "condition:stim",
                ]
            ),
            {
                "global": 1,
                "group": 1,
                "column": 1,
                "sample": 1,
                "timepoint": 1,
                "perturbation": 1,
                "other": 1,
            },
        )

    def test_aggregates_signed_prediction_scores_without_negating_by_sign(self) -> None:
        rows = [
            NetworkRow(
                source="A",
                target="B",
                score=0.9,
                sign="-",
                context="global",
                tool_id="signed_tool",
            ),
            NetworkRow(
                source="A",
                target="B",
                score=0.2,
                sign="+",
                context="global",
                tool_id="signed_tool",
            ),
        ]

        self.assertEqual(
            _aggregate_rows(rows, level="directed"),
            {("A", "B"): 0.9},
        )
        self.assertEqual(
            _aggregate_rows(rows, level="signed"),
            {("A", "B", "-"): 0.9, ("A", "B", "+"): 0.2},
        )

    def test_aggregate_rows_ignores_non_positive_scores(self) -> None:
        rows = [
            NetworkRow(
                source="A",
                target="B",
                score=1.0,
                sign="+",
                context="global",
            ),
            NetworkRow(
                source="A",
                target="C",
                score=0.0,
                sign="+",
                context="global",
            ),
        ]

        self.assertEqual(
            _aggregate_rows(rows, level="directed"),
            {("A", "B"): 1.0},
        )
        self.assertEqual(
            _aggregate_rows(rows, level="signed"),
            {("A", "B", "+"): 1.0},
        )

    def test_truth_count_metrics_use_top_ranked_predictions(self) -> None:
        stats = _top_truth_count_stats(
            prediction_scores={
                ("A", "B"): 0.9,
                ("C", "D"): 0.8,
                ("B", "C"): 0.7,
            },
            truth_keys={("A", "B"), ("B", "C")},
            n_candidates=4,
        )

        self.assertAlmostEqual(stats["f1_at_truth_count"] or 0.0, 0.5)
        self.assertAlmostEqual(stats["epr_at_truth_count"] or 0.0, 1.0)
        self.assertEqual(
            (
                stats["tp_at_truth_count"],
                stats["fp_at_truth_count"],
                stats["fn_at_truth_count"],
            ),
            (1, 1, 1),
        )

    def test_rank_metrics_handle_perfect_reversed_and_tied_rankings(self) -> None:
        self.assertEqual(
            _auroc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]),
            1.0,
        )
        self.assertEqual(
            _average_precision([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]),
            1.0,
        )
        self.assertEqual(
            _auroc([1, 1, 0, 0], [0.1, 0.2, 0.8, 0.9]),
            0.0,
        )
        self.assertAlmostEqual(
            _auroc([1, 0, 1, 0], [0.5, 0.5, 0.5, 0.5]),
            0.5,
        )
        self.assertAlmostEqual(
            _average_precision([1, 0, 1, 0], [0.5, 0.5, 0.5, 0.5]),
            0.5,
        )

    def test_rank_metrics_treat_unreported_candidate_edges_as_zero_scores(self) -> None:
        y_true = [1] + [0] * 99
        y_score = [1.0] + [0.0] * 99

        self.assertEqual(_auroc(y_true, y_score), 1.0)
        self.assertEqual(_average_precision(y_true, y_score), 1.0)

    def test_sparse_rank_metrics_match_dense_zero_filled_candidates(self) -> None:
        candidate_keys = [
            ("A", "B"),
            ("A", "C"),
            ("A", "D"),
            ("B", "C"),
            ("B", "D"),
            ("C", "D"),
        ]
        truth_keys = {("A", "B"), ("B", "D"), ("C", "D")}
        prediction_scores = {
            ("A", "B"): 0.9,
            ("A", "D"): 0.6,
            ("B", "C"): 0.6,
            ("B", "D"): 0.1,
        }
        y_true = [1 if key in truth_keys else 0 for key in candidate_keys]
        y_score = [prediction_scores.get(key, 0.0) for key in candidate_keys]

        self.assertAlmostEqual(
            _sparse_auroc(
                truth_keys=truth_keys,
                prediction_scores=prediction_scores,
                n_candidates=len(candidate_keys),
            ) or 0.0,
            _auroc(y_true, y_score) or 0.0,
        )
        self.assertAlmostEqual(
            _sparse_average_precision(
                truth_keys=truth_keys,
                prediction_scores=prediction_scores,
                n_candidates=len(candidate_keys),
            ) or 0.0,
            _average_precision(y_true, y_score) or 0.0,
        )

    def test_rejects_inferred_rows_with_empty_context_or_invalid_sign(self) -> None:
        cases = [
            ("", "+", "empty context"),
            ("global", "activation", "invalid sign"),
        ]
        for context, sign, expected in cases:
            with self.subTest(expected=expected):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "merged_network_raw.csv"
                    self._write_csv(
                        path,
                        [
                            {
                                "source": "A",
                                "target": "B",
                                "score": "1",
                                "sign": sign,
                                "evidence": "association",
                                "context": context,
                                "tool_id": "tool_01",
                            }
                        ],
                    )

                    with self.assertRaisesRegex(ValueError, expected):
                        _load_inferred_rows(path)

    def test_evaluates_cumulative_global_group_and_column_truth_contexts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            truth_dir = base / "truth"
            truth_dir.mkdir(parents=True)
            (truth_dir / "gene_universe.txt").write_text(
                "A\nB\nC\nD\n",
                encoding="utf-8",
            )
            self._write_csv(
                truth_dir / "networks.csv",
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    },
                    {
                        "source": "B",
                        "target": "C",
                        "score": "1",
                        "sign": "-",
                        "evidence": "simulated_truth",
                        "context": "global",
                    },
                    {
                        "source": "C",
                        "target": "D",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "group:sA",
                    },
                    {
                        "source": "A",
                        "target": "C",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "column:cell_a",
                    },
                ],
            )
            manifest_path = base / "ground-truth-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    self._manifest(truth_contexts=["global", "group", "column"])
                ),
                encoding="utf-8",
            )
            inferred_path = base / "merged_network_raw.csv"
            self._write_csv(
                inferred_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "genie3__01",
                    },
                    {
                        "source": "C",
                        "target": "D",
                        "score": "0.7",
                        "sign": "?",
                        "evidence": "association",
                        "context": "group:sA",
                        "tool_id": "genie3__01",
                    },
                    {
                        "source": "A",
                        "target": "C",
                        "score": "0.6",
                        "sign": "?",
                        "evidence": "association",
                        "context": "column:cell_a",
                        "tool_id": "genie3__01",
                    },
                    {
                        "source": "C",
                        "target": "B",
                        "score": "0.8",
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "genie3__01",
                    },
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "+",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "signed_tool",
                    },
                    {
                        "source": "B",
                        "target": "C",
                        "score": "0.8",
                        "sign": "-",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "signed_tool",
                    },
                    {
                        "source": "C",
                        "target": "D",
                        "score": "0.7",
                        "sign": "+",
                        "evidence": "association",
                        "context": "group:sA",
                        "tool_id": "signed_tool",
                    },
                    {
                        "source": "A",
                        "target": "C",
                        "score": "0.7",
                        "sign": "+",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "signed_tool",
                    },
                ],
            )
            run_report_path = base / "run_report.json"
            run_report_path.write_text(
                json.dumps(
                    {
                        "outputs": {
                            "merged_network_raw": inferred_path.name,
                        },
                        "tools": {
                            "catalog_tool_ids": {
                                "genie3__01": "genie3",
                                "signed_tool": "signed_tool",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            catalog_root = self._write_tool_catalog(base)
            with patch(
                "andrea.core.commands.evaluate_inference.evaluation.CATALOG_TOOLS_ROOT",
                catalog_root,
            ):
                report = evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                )
            output_root = base / "evaluation"
            evaluation_dir = output_root / report["outputs"]["evaluation_dir"]
            metrics_csv_exists = (
                output_root / report["outputs"]["metrics_csv"]
            ).exists()
            pairings_csv_exists = (
                output_root / report["outputs"]["pairings_csv"]
            ).exists()
            report_json_exists = (
                output_root / report["outputs"]["evaluation_report"]
            ).exists()
            view_path = output_root / report["outputs"]["evaluation_view"]
            view_exists = view_path.exists()
            view_html = view_path.read_text(encoding="utf-8") if view_exists else ""

        metrics = {
            (row["tool_id"], row["context"], row["level"]): row
            for row in report["metrics"]
        }
        self.assertEqual(metrics[("genie3__01", "global", "topology")]["status"], "ok")
        self.assertAlmostEqual(
            metrics[("genie3__01", "global", "topology")]["f1_at_truth_count"], 1.0
        )
        self.assertAlmostEqual(
            metrics[("genie3__01", "global", "topology")]["epr_at_truth_count"], 3.0
        )
        self.assertEqual(metrics[("genie3__01", "global", "directed")]["status"], "ok")
        self.assertAlmostEqual(
            metrics[("genie3__01", "global", "directed")]["f1_at_truth_count"], 0.5
        )
        self.assertAlmostEqual(
            metrics[("genie3__01", "global", "directed")]["epr_at_truth_count"], 3.0
        )
        self.assertEqual(metrics[("genie3__01", "global", "signed")]["status"], "not_applicable")
        self.assertEqual(metrics[("genie3__01", "global", "signed")]["n_truth_edges"], 2)
        self.assertEqual(metrics[("genie3__01", "global", "signed")]["truth_signed"], True)
        self.assertEqual(metrics[("genie3__01", "group:sA", "topology")]["status"], "ok")
        self.assertEqual(metrics[("genie3__01", "group:sA", "directed")]["status"], "ok")
        self.assertEqual(metrics[("genie3__01", "column:cell_a", "topology")]["status"], "ok")
        self.assertEqual(metrics[("signed_tool", "group:sA", "signed")]["status"], "ok")
        self.assertEqual(metrics[("signed_tool", "global", "signed")]["status"], "ok")
        self.assertAlmostEqual(
            metrics[("signed_tool", "global", "signed")]["f1_at_truth_count"], 1.0
        )
        self.assertAlmostEqual(
            metrics[("signed_tool", "global", "signed")]["epr_at_truth_count"], 12.0
        )
        self.assertEqual(Path(report["outputs"]["output_root"]), Path("."))
        self.assertEqual(evaluation_dir.parent, output_root)
        self.assertTrue(evaluation_dir.name.startswith("evaluation_"))
        self.assertTrue(metrics_csv_exists)
        self.assertTrue(pairings_csv_exists)
        self.assertTrue(report_json_exists)
        self.assertTrue(view_exists)
        self.assertIn("Inference Evaluation", view_html)
        self.assertIn("Metrics", view_html)
        self.assertIn('id="evaluation-view-root"', view_html)
        self.assertIn("AndreaEvaluationView.render", view_html)
        self.assertEqual(report["inputs"]["inference_dataset_id"], None)
        self.assertEqual(report["inputs"]["ground_truth_dataset_id"], "toy")
        self.assertEqual(report["inputs"]["ground_truth_simulator_id"], "toy_sim")
        self.assertEqual(report["inputs"]["merged_network"], "merged_network_raw")
        self.assertIn("runtime_profile", report)
        self.assertEqual(
            [entry["stage"] for entry in report["runtime_profile"]],
            [
                "loading_run_report",
                "loading_inferred_network",
                "loading_truth_networks",
                "preparing_evaluation_inputs",
                "computing",
                "writing_outputs",
            ],
        )
        self.assertEqual(report["ground_truth"]["gene_universe_size"], 4)
        self.assertEqual(
            report["ground_truth"]["data_axes"],
            {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "steady_state",
            },
        )
        self.assertEqual(
            report["ground_truth"]["truth_requirements"],
            {"contexts": ["global", "group", "column"]},
        )
        self.assertNotIn("profile", report["ground_truth"])
        self.assertEqual(
            report["ground_truth"]["context_counts_by_family"],
            {
                "global": 1,
                "group": 1,
                "column": 1,
                "sample": 0,
                "timepoint": 0,
                "perturbation": 0,
                "other": 0,
            },
        )
        self.assertEqual(metrics[("genie3__01", "global", "topology")]["n_candidate_genes"], 4)
        self.assertEqual(report["ground_truth"]["contexts"], ["global", "group:sA", "column:cell_a"])
        self.assertEqual(
            report["context_matching"]["truth_context_counts_by_family"]["group"],
            1,
        )
        self.assertIn("column:cell_a", report["ground_truth"]["contexts"])
        self.assertEqual(
            report["context_matching"]["truth_context_counts_by_family"]["column"],
            1,
        )
        missing_by_tool = {
            row["tool_id"]: row
            for row in report["context_matching"]["missing_truth_contexts_by_tool"]
        }
        self.assertEqual(
            missing_by_tool["signed_tool"]["missing_context_counts_by_family"]["column"],
            1,
        )
        self.assertIn("Global", view_html)
        self.assertIn("bars", view_html)
        self.assertIn("Groups", view_html)
        self.assertIn("heatmap", view_html)
        self.assertIn("Column Contexts", view_html)
        self.assertIn("distributions by tool", view_html)
        self.assertNotIn("run_report", report["inputs"])
        self.assertNotIn("ground_truth_manifest", report["inputs"])
        self.assertNotIn("derived_inputs", report)
        for pairing in report["pairings"]:
            self.assertNotIn("truth_path", pairing)

    def test_skips_prediction_context_without_matching_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            truth_dir = base / "truth"
            truth_dir.mkdir(parents=True)
            (truth_dir / "gene_universe.txt").write_text(
                "A\nB\n",
                encoding="utf-8",
            )
            self._write_csv(
                truth_dir / "networks.csv",
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
            )
            manifest_path = base / "ground-truth-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    self._manifest(truth_contexts=["global"])
                ),
                encoding="utf-8",
            )
            inferred_path = base / "merged_network_raw.csv"
            self._write_csv(
                inferred_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "?",
                        "evidence": "association",
                        "context": "group:sA",
                        "tool_id": "genie3__01",
                    }
                ],
            )
            run_report_path = base / "run_report.json"
            run_report_path.write_text(
                json.dumps(
                    {
                        "outputs": {
                            "merged_network_raw": inferred_path.name,
                        },
                        "tools": {
                            "catalog_tool_ids": {
                                "genie3__01": "genie3",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
            )

        self.assertEqual(report["pairings"][0]["status"], "skipped")
        self.assertIn("no ground-truth network", report["pairings"][0]["reason"])
        self.assertEqual(
            report["context_matching"]["prediction_contexts_without_truth_count"],
            1,
        )
        self.assertEqual(
            report["context_matching"]["truth_contexts_without_any_prediction_count"],
            1,
        )

    def test_evaluates_group_aggregated_rows_against_group_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            truth_dir = base / "truth"
            truth_dir.mkdir(parents=True)
            (truth_dir / "gene_universe.txt").write_text(
                "A\nB\nC\n",
                encoding="utf-8",
            )
            self._write_csv(
                truth_dir / "networks.csv",
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "group:sA",
                    },
                    {
                        "source": "B",
                        "target": "C",
                        "score": "1",
                        "sign": "-",
                        "evidence": "simulated_truth",
                        "context": "group:sA",
                    },
                ],
            )
            manifest_path = base / "ground-truth-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    self._manifest(
                        dataset_id="toy_grouped",
                        truth_contexts=["global", "group"],
                    )
                ),
                encoding="utf-8",
            )
            inferred_path = base / "merged_network_raw.csv"
            self._write_csv(
                inferred_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "+",
                        "evidence": "andrea_group_agg_mean_effect",
                        "context": "group:sA",
                        "tool_id": "group_tool_01",
                    },
                    {
                        "source": "B",
                        "target": "C",
                        "score": "0.7",
                        "sign": "-",
                        "evidence": "andrea_group_agg_mean_effect",
                        "context": "group:sA",
                        "tool_id": "group_tool_01",
                    },
                ],
            )
            run_report_path = base / "run_report.json"
            run_report_path.write_text(
                json.dumps(
                    {
                        "outputs": {"merged_network_raw": inferred_path.name},
                        "tools": {
                            "catalog_tool_ids": {
                                "group_tool_01": "signed_tool",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            catalog_root = self._write_tool_catalog(base)
            with patch(
                "andrea.core.commands.evaluate_inference.evaluation.CATALOG_TOOLS_ROOT",
                catalog_root,
            ):
                report = evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

        metrics = {
            (row["tool_id"], row["context"], row["level"]): row
            for row in report["metrics"]
        }
        self.assertEqual(report["pairings"][0]["status"], "evaluated")
        self.assertEqual(report["pairings"][0]["truth_context"], "group:sA")
        self.assertEqual(
            report["ground_truth"]["context_counts_by_family"],
            {
                "global": 0,
                "group": 1,
                "column": 0,
                "sample": 0,
                "timepoint": 0,
                "perturbation": 0,
                "other": 0,
            },
        )
        self.assertEqual(metrics[("group_tool_01", "group:sA", "topology")]["status"], "ok")
        self.assertEqual(metrics[("group_tool_01", "group:sA", "directed")]["status"], "ok")
        self.assertEqual(metrics[("group_tool_01", "group:sA", "signed")]["status"], "ok")
        self.assertAlmostEqual(
            metrics[("group_tool_01", "group:sA", "signed")]["f1_at_truth_count"],
            1.0,
        )

    def test_other_context_is_evaluated_and_kept_as_public_context_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            truth_dir = base / "truth"
            truth_dir.mkdir(parents=True)
            (truth_dir / "gene_universe.txt").write_text(
                "A\nB\n",
                encoding="utf-8",
            )
            self._write_csv(
                truth_dir / "networks.csv",
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "condition:stim",
                    }
                ],
            )
            manifest_path = base / "ground-truth-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    self._manifest(dataset_id="toy_other_context")
                ),
                encoding="utf-8",
            )
            inferred_path = base / "merged_network_raw.csv"
            self._write_csv(
                inferred_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "?",
                        "evidence": "association",
                        "context": "condition:stim",
                        "tool_id": "genie3__01",
                    }
                ],
            )
            run_report_path = base / "run_report.json"
            run_report_path.write_text(
                json.dumps(
                    {
                        "outputs": {"merged_network_raw": inferred_path.name},
                        "tools": {
                            "catalog_tool_ids": {
                                "genie3__01": "genie3",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        self.assertEqual(report["ground_truth"]["contexts"], ["condition:stim"])
        self.assertEqual(
            report["ground_truth"]["context_counts_by_family"],
            {
                "global": 0,
                "group": 0,
                "column": 0,
                "sample": 0,
                "timepoint": 0,
                "perturbation": 0,
                "other": 1,
            },
        )
        self.assertEqual(report["pairings"][0]["status"], "evaluated")
        self.assertEqual(report["metrics"][0]["context"], "condition:stim")
        self.assertNotIn("context_type", report["metrics"][0])
        self.assertNotIn("context_family", report["metrics"][0])

    def test_semantic_specific_contexts_are_evaluated_by_exact_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            truth_dir = base / "truth"
            truth_dir.mkdir(parents=True)
            (truth_dir / "gene_universe.txt").write_text(
                "A\nB\nC\n",
                encoding="utf-8",
            )
            self._write_csv(
                truth_dir / "networks.csv",
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "timepoint:t0",
                    },
                    {
                        "source": "B",
                        "target": "C",
                        "score": "1",
                        "sign": "-",
                        "evidence": "simulated_truth",
                        "context": "perturbation:ko_G1",
                    },
                ],
            )
            manifest_path = base / "ground-truth-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    self._manifest(
                        dataset_id="toy_semantic_contexts",
                        data_axes={
                            "measurement": "rna_expression",
                            "resolution": "bulk",
                            "column_kind": "timepoints",
                            "experimental_design": "time_series",
                        },
                        truth_contexts=["global", "column"],
                    )
                ),
                encoding="utf-8",
            )
            inferred_path = base / "merged_network_raw.csv"
            self._write_csv(
                inferred_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "?",
                        "evidence": "association",
                        "context": "timepoint:t0",
                        "tool_id": "genie3__01",
                    },
                    {
                        "source": "B",
                        "target": "C",
                        "score": "0.8",
                        "sign": "?",
                        "evidence": "association",
                        "context": "perturbation:ko_G1",
                        "tool_id": "genie3__01",
                    },
                ],
            )
            run_report_path = base / "run_report.json"
            run_report_path.write_text(
                json.dumps(
                    {
                        "outputs": {"merged_network_raw": inferred_path.name},
                        "tools": {
                            "catalog_tool_ids": {
                                "genie3__01": "genie3",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        metrics = {
            (row["context"], row["level"]): row
            for row in report["metrics"]
            if row["tool_id"] == "genie3__01"
        }
        self.assertEqual(
            report["ground_truth"]["contexts"],
            ["timepoint:t0", "perturbation:ko_G1"],
        )
        self.assertEqual(
            report["ground_truth"]["context_counts_by_family"]["timepoint"],
            1,
        )
        self.assertEqual(
            report["ground_truth"]["context_counts_by_family"]["perturbation"],
            1,
        )
        self.assertEqual(metrics[("timepoint:t0", "topology")]["status"], "ok")
        self.assertEqual(metrics[("perturbation:ko_G1", "topology")]["status"], "ok")

    def test_requires_explicit_ground_truth_gene_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            truth_dir = base / "truth"
            truth_dir.mkdir(parents=True)
            self._write_csv(
                truth_dir / "networks.csv",
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
            )
            manifest_path = base / "ground-truth-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    self._manifest(
                        outputs={
                            "networks": "truth/networks.csv",
                        }
                    )
                ),
                encoding="utf-8",
            )
            inferred_path = base / "merged_network_raw.csv"
            self._write_csv(
                inferred_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "genie3__01",
                    }
                ],
            )
            run_report_path = base / "run_report.json"
            run_report_path.write_text(
                json.dumps(
                    {
                        "outputs": {"merged_network_raw": inferred_path.name},
                        "tools": {
                            "catalog_tool_ids": {
                                "genie3__01": "genie3",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "outputs.gene_universe"):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                )

    def _write_csv(self, path: Path, rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def _write_tool_catalog(self, base: Path) -> Path:
        catalog_root = base / "catalog_tools"
        self._write_toolspec_outputs(
            catalog_root / "genie3" / "toolspec.json",
            directed=True,
            sign="none",
        )
        self._write_toolspec_outputs(
            catalog_root / "signed_tool" / "toolspec.json",
            directed=True,
            sign="signed",
        )
        return catalog_root

    def _write_toolspec_outputs(
        self,
        path: Path,
        *,
        directed: bool,
        sign: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "outputs": {
                        "directed": directed,
                        "sign": sign,
                    }
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
