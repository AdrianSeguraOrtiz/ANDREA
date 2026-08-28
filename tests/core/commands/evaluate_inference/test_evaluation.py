from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

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
    DATASET_FINGERPRINT = {"algorithm": "sha256", "value": "a" * 64}

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
            "dataset_fingerprint": dict(EvaluateInferenceCoreTests.DATASET_FINGERPRINT),
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
            "candidate_space": {
                "sources": "truth/gene_universe.txt",
                "targets": "truth/gene_universe.txt",
                "allow_self_edges": False,
            },
        }

    @staticmethod
    def _run_tools(
        *,
        run_id: str,
        catalog_tool_id: str,
        directed: bool,
        sign: str,
        tool_origin: str = "catalog",
        execution_mode: str = "global",
        completed_contexts: list[str] | None = None,
    ) -> dict[str, object]:
        if completed_contexts is None:
            if execution_mode == "global":
                completed_contexts = ["global"]
            elif execution_mode == "column_native":
                completed_contexts = ["column:C1"]
            else:
                completed_contexts = ["group:A"]
        return {
            "selected": [run_id],
            "catalog_tool_ids": {run_id: catalog_tool_id},
            "tool_origins": {run_id: tool_origin},
            "skipped": {},
            "status_by_tool": {run_id: "completed"},
            "completed": [run_id],
            "completed_contexts": {run_id: completed_contexts},
            "failed": {},
            "results": {
                run_id: {"execution": {"mode": execution_mode}},
            },
            "output_capabilities": {
                run_id: {
                    "tool_origin": tool_origin,
                    "catalog_tool_id": catalog_tool_id,
                    "directed": directed,
                    "sign": sign,
                }
            },
        }

    @staticmethod
    def _terminal_run_report(
        *,
        run_tools: dict[str, object],
        rows_per_tool: dict[str, int],
        run_inputs: dict[str, object] | None = None,
        gene_count: int = 3,
        dataset_id: str = "toy",
    ) -> dict[str, object]:
        selected = run_tools.get("selected")
        selected_count = len(selected) if isinstance(selected, list) else 0
        completed = run_tools.get("completed")
        completed_count = len(completed) if isinstance(completed, list) else 0
        failed = run_tools.get("failed")
        failed_count = len(failed) if isinstance(failed, dict) else 0
        return {
            "run_id": "inference_01",
            "status": "executed",
            "inputs": {
                "dataset_manifest_path": "input/dataset-manifest.json",
                "tools_params_path": "input/tools_params.json",
                **(run_inputs or {}),
            },
            "dataset": {
                "id": dataset_id,
                "fingerprint": dict(EvaluateInferenceCoreTests.DATASET_FINGERPRINT),
                "column_kind": "cells",
                "expression_profile": "scrna",
                "genes": gene_count,
                "columns": 2,
                "expression_matrix_path": "input/expression.tsv",
            },
            "outputs": {
                "merged_network_raw": "merged_network_raw.csv",
                "merged_network_raw_gexf": "merged_network_raw.gexf",
                "merged_network_raw_graphml": "merged_network_raw.graphml",
                "merged_network_normalized": "merged_network_normalized.csv",
                "merged_network_normalized_gexf": "merged_network_normalized.gexf",
                "merged_network_normalized_graphml": (
                    "merged_network_normalized.graphml"
                ),
                "merged_network_normalized_cytoscape_script": (
                    "merged_network_normalized_cytoscape.py"
                ),
                "rows_per_tool": rows_per_tool,
            },
            "tools": run_tools,
            "issues": [],
            "execution": {
                "elapsed_seconds": 1.0,
                "planner_requested": "heuristic",
                "planner_used": "heuristic",
                "planner_time_limit_seconds": 100.0,
                "waves_total": 1,
                "tools_selected": selected_count,
                "physical_tasks_total": max(1, selected_count),
                "tools_completed": completed_count,
                "tools_failed": failed_count,
            },
            "plan_file": "plan.json",
            "notes": [
                "Run directory is frozen at planning time.",
                "Use run_infer_network_plan(run_dir=...) to execute this plan.",
            ],
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
            )
            or 0.0,
            _auroc(y_true, y_score) or 0.0,
        )
        self.assertAlmostEqual(
            _sparse_average_precision(
                truth_keys=truth_keys,
                prediction_scores=prediction_scores,
                n_candidates=len(candidate_keys),
            )
            or 0.0,
            _average_precision(y_true, y_score) or 0.0,
        )

    def test_rejects_inferred_rows_with_empty_context_or_invalid_sign(self) -> None:
        cases = [
            ("", "+", "empty context"),
            ("global", "activation", "invalid sign"),
        ]
        for context, sign, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
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

    def test_accepts_finite_float_compatible_inferred_score_spellings(self) -> None:
        for score, expected_score in (
            ("01", 1.0),
            ("+1", 1.0),
            ("1_0", 10.0),
            (".5", 0.5),
            ("1.", 1.0),
            (" 1 ", 1.0),
        ):
            with self.subTest(score=score), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "merged_network_raw.csv"
                self._write_csv(
                    path,
                    [
                        {
                            "source": "A",
                            "target": "B",
                            "score": score,
                            "sign": "+",
                            "evidence": "association",
                            "context": "global",
                            "tool_id": "tool_01",
                        }
                    ],
                )
                rows = _load_inferred_rows(path)
                self.assertEqual(rows[0].score, expected_score)

    def test_rejects_non_finite_or_invalid_inferred_scores(self) -> None:
        for score in ("1e9999", "nan", "-Infinity", "not-a-number"):
            with self.subTest(score=score), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "merged_network_raw.csv"
                self._write_csv(
                    path,
                    [
                        {
                            "source": "A",
                            "target": "B",
                            "score": score,
                            "sign": "+",
                            "evidence": "association",
                            "context": "global",
                            "tool_id": "tool_01",
                        }
                    ],
                )
                with self.assertRaisesRegex(ValueError, "invalid score"):
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
                        "tool_id": "genie3_group",
                    },
                    {
                        "source": "A",
                        "target": "C",
                        "score": "0.6",
                        "sign": "?",
                        "evidence": "association",
                        "context": "column:cell_a",
                        "tool_id": "genie3_column",
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
                        "tool_id": "signed_group",
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
                    self._terminal_run_report(
                        rows_per_tool={
                            "genie3__01": 2,
                            "genie3_group": 1,
                            "genie3_column": 1,
                            "signed_tool": 3,
                            "signed_group": 1,
                        },
                        run_tools={
                            "selected": [
                                "genie3__01",
                                "genie3_group",
                                "genie3_column",
                                "signed_tool",
                                "signed_group",
                            ],
                            "catalog_tool_ids": {
                                "genie3__01": "genie3",
                                "genie3_group": "genie3",
                                "genie3_column": "genie3",
                                "signed_tool": "signed_tool",
                                "signed_group": "signed_tool",
                            },
                            "tool_origins": {
                                "genie3__01": "catalog",
                                "genie3_group": "catalog",
                                "genie3_column": "catalog",
                                "signed_tool": "catalog",
                                "signed_group": "catalog",
                            },
                            "skipped": {},
                            "status_by_tool": {
                                "genie3__01": "completed",
                                "genie3_group": "completed",
                                "genie3_column": "completed",
                                "signed_tool": "completed",
                                "signed_group": "completed",
                            },
                            "completed": [
                                "genie3__01",
                                "genie3_group",
                                "genie3_column",
                                "signed_tool",
                                "signed_group",
                            ],
                            "completed_contexts": {
                                "genie3__01": ["global"],
                                "genie3_group": ["group:sA"],
                                "genie3_column": ["column:cell_a"],
                                "signed_tool": ["global"],
                                "signed_group": ["group:sA"],
                            },
                            "failed": {},
                            "results": {
                                "genie3__01": {"execution": {"mode": "global"}},
                                "genie3_group": {"execution": {"mode": "group_native"}},
                                "genie3_column": {
                                    "execution": {"mode": "column_native"}
                                },
                                "signed_tool": {"execution": {"mode": "global"}},
                                "signed_group": {"execution": {"mode": "group_native"}},
                            },
                            "output_capabilities": {
                                "genie3__01": {
                                    "tool_origin": "catalog",
                                    "catalog_tool_id": "genie3",
                                    "directed": True,
                                    "sign": "none",
                                },
                                "genie3_group": {
                                    "tool_origin": "catalog",
                                    "catalog_tool_id": "genie3",
                                    "directed": True,
                                    "sign": "none",
                                },
                                "genie3_column": {
                                    "tool_origin": "catalog",
                                    "catalog_tool_id": "genie3",
                                    "directed": True,
                                    "sign": "none",
                                },
                                "signed_tool": {
                                    "tool_origin": "catalog",
                                    "catalog_tool_id": "signed_tool",
                                    "directed": True,
                                    "sign": "signed",
                                },
                                "signed_group": {
                                    "tool_origin": "catalog",
                                    "catalog_tool_id": "signed_tool",
                                    "directed": True,
                                    "sign": "signed",
                                },
                            },
                        },
                    )
                ),
                encoding="utf-8",
            )

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
        self.assertEqual(
            metrics[("genie3__01", "global", "signed")]["status"], "not_applicable"
        )
        self.assertEqual(
            metrics[("genie3__01", "global", "signed")]["n_truth_edges"], 2
        )
        self.assertEqual(
            metrics[("genie3__01", "global", "signed")]["truth_signed"], True
        )
        self.assertEqual(
            metrics[("genie3_group", "group:sA", "topology")]["status"], "ok"
        )
        self.assertEqual(
            metrics[("genie3_group", "group:sA", "directed")]["status"], "ok"
        )
        self.assertEqual(
            metrics[("genie3_column", "column:cell_a", "topology")]["status"], "ok"
        )
        self.assertEqual(
            metrics[("signed_group", "group:sA", "signed")]["status"], "ok"
        )
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
        self.assertIn("n_candidate_sources", view_html)
        self.assertIn("n_truth_rows_outside_candidate_space", view_html)
        self.assertIn("n_prediction_rows_outside_candidate_space", view_html)
        self.assertEqual(report["inputs"]["inference_dataset_id"], "toy")
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
        self.assertEqual(
            metrics[("genie3__01", "global", "topology")]["n_candidate_genes"], 4
        )
        self.assertEqual(
            report["ground_truth"]["contexts"], ["global", "group:sA", "column:cell_a"]
        )
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
            missing_by_tool["signed_tool"]["missing_context_counts_by_family"][
                "column"
            ],
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
            (base / "tf_list.txt").write_text("B\n", encoding="utf-8")
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
            manifest = self._manifest(truth_contexts=["global"])
            manifest["candidate_space"] = {
                "sources": "tf_list.txt",
                "targets": "truth/gene_universe.txt",
                "allow_self_edges": False,
            }
            manifest_path.write_text(
                json.dumps(manifest),
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
                    self._terminal_run_report(
                        rows_per_tool={"genie3__01": 1},
                        run_tools=self._run_tools(
                            run_id="genie3__01",
                            catalog_tool_id="genie3",
                            directed=True,
                            sign="none",
                            execution_mode="group_native",
                            completed_contexts=["group:sA"],
                        ),
                    )
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
        self.assertEqual(
            report["ground_truth"]["candidate_space"]["truth_rows_total"], 1
        )
        self.assertEqual(
            report["ground_truth"]["candidate_space"][
                "n_truth_rows_outside_candidate_space_by_level"
            ],
            {"topology": 0, "directed": 1, "signed": 1},
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
                        "target": "C",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    },
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
                    self._terminal_run_report(
                        rows_per_tool={"group_tool_01": 2},
                        dataset_id="toy_grouped",
                        run_tools=self._run_tools(
                            run_id="group_tool_01",
                            catalog_tool_id="signed_tool",
                            directed=True,
                            sign="signed",
                            execution_mode="group_aggregated",
                            completed_contexts=["group:sA"],
                        ),
                    )
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
            (row["tool_id"], row["context"], row["level"]): row
            for row in report["metrics"]
        }
        group_pairing = next(
            pairing
            for pairing in report["pairings"]
            if pairing["context"] == "group:sA"
        )
        self.assertEqual(group_pairing["status"], "evaluated")
        self.assertEqual(group_pairing["truth_context"], "group:sA")
        self.assertEqual(
            report["ground_truth"]["context_counts_by_family"],
            {
                "global": 1,
                "group": 1,
                "column": 0,
                "sample": 0,
                "timepoint": 0,
                "perturbation": 0,
                "other": 0,
            },
        )
        self.assertEqual(
            metrics[("group_tool_01", "group:sA", "topology")]["status"], "ok"
        )
        self.assertEqual(
            metrics[("group_tool_01", "group:sA", "directed")]["status"], "ok"
        )
        self.assertEqual(
            metrics[("group_tool_01", "group:sA", "signed")]["status"], "ok"
        )
        self.assertAlmostEqual(
            metrics[("group_tool_01", "group:sA", "signed")]["f1_at_truth_count"],
            1.0,
        )

    def test_rejects_other_context_not_declared_by_execution_contract(self) -> None:
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
                        "source": "B",
                        "target": "A",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    },
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "condition:stim",
                    },
                ],
            )
            manifest_path = base / "ground-truth-manifest.json"
            manifest_path.write_text(
                json.dumps(self._manifest(dataset_id="toy_other_context")),
                encoding="utf-8",
            )
            inferred_path = base / "merged_network_raw.csv"
            self._write_csv(
                inferred_path,
                [
                    {
                        "source": "B",
                        "target": "A",
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
                        "sign": "?",
                        "evidence": "association",
                        "context": "condition:stim",
                        "tool_id": "genie3__01",
                    },
                ],
            )
            run_report_path = base / "run_report.json"
            run_report_path.write_text(
                json.dumps(
                    self._terminal_run_report(
                        rows_per_tool={"genie3__01": 2},
                        dataset_id="toy_other_context",
                        run_tools=self._run_tools(
                            run_id="genie3__01",
                            catalog_tool_id="genie3",
                            directed=True,
                            sign="none",
                        ),
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "contexts not declared in Run report tools.completed_contexts",
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_rejects_semantic_contexts_outside_execution_contract(self) -> None:
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
                        "source": "C",
                        "target": "A",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    },
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
                        truth_contexts=["global"],
                    )
                ),
                encoding="utf-8",
            )
            inferred_path = base / "merged_network_raw.csv"
            self._write_csv(
                inferred_path,
                [
                    {
                        "source": "C",
                        "target": "A",
                        "score": "0.7",
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "genie3__01",
                    },
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
                    self._terminal_run_report(
                        rows_per_tool={"genie3__01": 3},
                        dataset_id="toy_semantic_contexts",
                        run_tools=self._run_tools(
                            run_id="genie3__01",
                            catalog_tool_id="genie3",
                            directed=True,
                            sign="none",
                        ),
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "contexts not declared in Run report tools.completed_contexts",
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_uses_frozen_output_capabilities_without_catalog_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = self._manifest()
            run_tools = self._run_tools(
                run_id="external_01",
                catalog_tool_id="custom_external_01",
                directed=True,
                sign="signed",
                tool_origin="custom",
            )
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=manifest,
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "+",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "external_01",
                    }
                ],
                run_tools=run_tools,
                genes="A\nB\nC\n",
            )

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        signed = next(row for row in report["metrics"] if row["level"] == "signed")
        self.assertEqual(signed["status"], "ok")
        self.assertEqual(signed["catalog_tool_id"], "custom_external_01")
        self.assertEqual(signed["tool_origin"], "custom")
        self.assertEqual(
            report["inference_run"]["output_capabilities"]["external_01"],
            {
                "tool_origin": "custom",
                "catalog_tool_id": "custom_external_01",
                "directed": True,
                "sign": "signed",
            },
        )

    def test_evaluates_completed_custom_run_with_header_only_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": ".5",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[],
                run_tools=self._run_tools(
                    run_id="external_01",
                    catalog_tool_id="custom_external_01",
                    directed=True,
                    sign="signed",
                    tool_origin="custom",
                    execution_mode="global",
                ),
                genes="A\nB\nC\n",
            )

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        self.assertEqual(report["pairings"][0]["tool_id"], "external_01")
        self.assertEqual(report["pairings"][0]["context"], "global")
        self.assertEqual(report["pairings"][0]["status"], "evaluated")
        self.assertEqual(report["pairings"][0]["n_prediction_rows"], 0)
        self.assertEqual(
            {metric["level"] for metric in report["metrics"]},
            {"topology", "directed", "signed"},
        )
        for metric in report["metrics"]:
            self.assertEqual(metric["status"], "ok")
            self.assertEqual(metric["n_prediction_rows"], 0)
            self.assertEqual(metric["n_predicted_edges"], 0)

    def test_materializes_completed_zero_edge_contexts_for_non_global_modes(
        self,
    ) -> None:
        cases = (
            ("group_native", "group:A"),
            ("group_aggregated", "group:A"),
            ("column_native", "column:C1"),
        )
        for execution_mode, context in cases:
            with self.subTest(
                execution_mode=execution_mode
            ), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                run_report_path, manifest_path = self._write_evaluation_fixture(
                    base=base,
                    manifest=self._manifest(
                        truth_contexts=[
                            "global",
                            "group" if context.startswith("group:") else "column",
                        ]
                    ),
                    truth_rows=[
                        {
                            "source": "A",
                            "target": "B",
                            "score": "1",
                            "sign": "+",
                            "evidence": "simulated_truth",
                            "context": truth_context,
                        }
                        for truth_context in ("global", context)
                    ],
                    prediction_rows=[],
                    run_tools=self._run_tools(
                        run_id="empty_run",
                        catalog_tool_id="tool",
                        directed=True,
                        sign="none",
                        execution_mode=execution_mode,
                        completed_contexts=[context],
                    ),
                    genes="A\nB\n",
                )

                report = evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

            self.assertEqual(
                [
                    (pairing["context"], pairing["n_prediction_rows"])
                    for pairing in report["pairings"]
                ],
                [(context, 0)],
            )

    def test_materializes_empty_group_alongside_nonempty_group_for_same_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            truth_rows = [
                {
                    "source": "A",
                    "target": "B",
                    "score": "1",
                    "sign": "+",
                    "evidence": "simulated_truth",
                    "context": context,
                }
                for context in ("global", "group:A", "group:B")
            ]
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(truth_contexts=["global", "group"]),
                truth_rows=truth_rows,
                prediction_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "?",
                        "evidence": "association",
                        "context": "group:A",
                        "tool_id": "grouped_run",
                    }
                ],
                run_tools=self._run_tools(
                    run_id="grouped_run",
                    catalog_tool_id="tool",
                    directed=True,
                    sign="none",
                    execution_mode="group_emulated",
                    completed_contexts=["group:A", "group:B"],
                ),
                genes="A\nB\n",
            )

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        pairings = {row["context"]: row for row in report["pairings"]}
        self.assertEqual(pairings["group:A"]["n_prediction_rows"], 1)
        self.assertEqual(pairings["group:B"]["n_prediction_rows"], 0)
        self.assertEqual(set(pairings), {"group:A", "group:B"})

    def test_does_not_materialize_failed_group_child_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(truth_contexts=["global", "group"]),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": context,
                    }
                    for context in ("global", "group:A", "group:B")
                ],
                prediction_rows=[],
                run_tools=self._run_tools(
                    run_id="partial_run",
                    catalog_tool_id="tool",
                    directed=True,
                    sign="none",
                    execution_mode="group_emulated",
                    completed_contexts=["group:A"],
                ),
                genes="A\nB\n",
            )

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        self.assertEqual(
            [row["context"] for row in report["pairings"]],
            ["group:A"],
        )

    def test_rejects_completed_context_incompatible_with_execution_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[],
                run_tools=self._run_tools(
                    run_id="bad_context",
                    catalog_tool_id="tool",
                    directed=True,
                    sign="none",
                    execution_mode="global",
                    completed_contexts=["group:A"],
                ),
                genes="A\nB\n",
            )

            with self.assertRaisesRegex(
                ValueError,
                "contradicts execution mode 'global'",
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_rejects_mismatched_inference_and_ground_truth_dataset_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "+1",
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "genie3__01",
                    }
                ],
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\nC\n",
                inference_dataset_id="different_dataset",
            )

            with self.assertRaisesRegex(
                ValueError,
                "run_report.dataset.id='different_dataset'.*"
                "ground_truth_manifest.dataset_id='toy'",
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

            self.assertFalse((base / "evaluation").exists())

    def test_rejects_same_dataset_id_with_different_content_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = self._manifest()
            manifest["dataset_fingerprint"] = {
                "algorithm": "sha256",
                "value": "b" * 64,
            }
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=manifest,
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[],
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\n",
            )

            with self.assertRaisesRegex(
                ValueError,
                "dataset fingerprints must match exactly",
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

            self.assertFalse((base / "evaluation").exists())

    def test_rejects_custom_catalog_identity_not_derived_from_complete_run_id(
        self,
    ) -> None:
        cases = (
            ("external_01", "custom_external", "custom_external_01"),
            (
                "custom_external_01",
                "custom_external_01",
                "custom_custom_external_01",
            ),
        )
        for run_id, invalid_catalog_id, expected_catalog_id in cases:
            with self.subTest(run_id=run_id), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                run_tools = self._run_tools(
                    run_id=run_id,
                    catalog_tool_id=expected_catalog_id,
                    directed=True,
                    sign="none",
                    tool_origin="custom",
                )
                run_tools["catalog_tool_ids"][run_id] = invalid_catalog_id
                run_tools["output_capabilities"][run_id][
                    "catalog_tool_id"
                ] = invalid_catalog_id
                run_report_path, manifest_path = self._write_evaluation_fixture(
                    base=base,
                    manifest=self._manifest(),
                    truth_rows=[
                        {
                            "source": "A",
                            "target": "B",
                            "score": "1",
                            "sign": "+",
                            "evidence": "simulated_truth",
                            "context": "global",
                        }
                    ],
                    prediction_rows=[
                        {
                            "source": "A",
                            "target": "B",
                            "score": "1",
                            "sign": "?",
                            "evidence": "association",
                            "context": "global",
                            "tool_id": run_id,
                        }
                    ],
                    run_tools=run_tools,
                    genes="A\nB\nC\n",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    f"must be exactly '{expected_catalog_id}' "
                    "when tool_origin is 'custom'",
                ):
                    evaluate_inference(
                        run_report_path=run_report_path,
                        ground_truth_manifest_path=manifest_path,
                        output_dir=base / "evaluation",
                        generate_view=False,
                    )

    def test_terminal_report_validation_ignores_unconsumed_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "?",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
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
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\n",
            )
            run_report = json.loads(run_report_path.read_text(encoding="utf-8"))
            run_report["future_field"] = {"allowed": True}
            run_report["dataset"]["genes"] = "3"
            run_report["execution"]["tools_completed"] = "1"
            run_report["tools"]["results"]["genie3__01"]["future_field"] = True
            run_report["outputs"].pop("merged_network_raw_graphml")
            run_report_path.write_text(json.dumps(run_report), encoding="utf-8")

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        self.assertEqual(report["inputs"]["inference_run_id"], "inference_01")

    def test_rejects_rows_inconsistent_with_frozen_sign_semantics(self) -> None:
        cases = [
            ("none", "+", "sign='none'.*emitted signed edge"),
            ("signed", "?", "sign='signed'.*emitted unsigned edge"),
        ]
        for declared_sign, emitted_sign, expected in cases:
            with (
                self.subTest(
                    declared_sign=declared_sign,
                    emitted_sign=emitted_sign,
                ),
                tempfile.TemporaryDirectory() as tmp,
            ):
                base = Path(tmp)
                run_report_path, manifest_path = self._write_evaluation_fixture(
                    base=base,
                    manifest=self._manifest(),
                    truth_rows=[
                        {
                            "source": "A",
                            "target": "B",
                            "score": "1",
                            "sign": "+",
                            "evidence": "simulated_truth",
                            "context": "global",
                        }
                    ],
                    prediction_rows=[
                        {
                            "source": "A",
                            "target": "B",
                            "score": "0.9",
                            "sign": emitted_sign,
                            "evidence": "association",
                            "context": "global",
                            "tool_id": "external_01",
                        }
                    ],
                    run_tools=self._run_tools(
                        run_id="external_01",
                        catalog_tool_id="custom_external_01",
                        directed=True,
                        sign=declared_sign,
                        tool_origin="custom",
                    ),
                    genes="A\nB\nC\n",
                )

                with self.assertRaisesRegex(ValueError, expected):
                    evaluate_inference(
                        run_report_path=run_report_path,
                        ground_truth_manifest_path=manifest_path,
                        output_dir=base / "evaluation",
                        generate_view=False,
                    )

    def test_rejects_missing_frozen_capabilities_even_with_custom_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            custom_tools = {
                "tools": [
                    {
                        "run_id": "external_01",
                        "name": "External",
                        "docker_image": "example/external:1.0",
                        "execution_mode": "global",
                        "extra_inputs": [],
                        "outputs": {"directed": True, "sign": "signed"},
                    }
                ]
            }
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "+",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "external_01",
                    }
                ],
                run_tools={
                    "selected": ["external_01"],
                    "catalog_tool_ids": {"external_01": "custom_external_01"},
                    "tool_origins": {"external_01": "custom"},
                },
                run_inputs={"custom_tools_path": "input/custom_tools.json"},
                genes="A\nB\nC\n",
                extra_files={
                    "input/custom_tools.json": json.dumps(custom_tools),
                },
            )

            with self.assertRaisesRegex(
                ValueError,
                "tools.output_capabilities must be an object with exactly",
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_rejects_missing_frozen_capabilities_without_catalog_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "0.9",
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "external_01",
                    }
                ],
                run_tools={
                    "selected": ["genie3__01"],
                    "catalog_tool_ids": {"genie3__01": "genie3"},
                    "tool_origins": {"genie3__01": "catalog"},
                },
                genes="A\nB\nC\n",
            )

            with self.assertRaisesRegex(
                ValueError,
                "tools.output_capabilities must be an object with exactly",
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_rejects_malformed_frozen_output_capabilities(self) -> None:
        invalid_values = [
            (None, "must contain exactly"),
            (
                {
                    "tool_origin": "custom",
                    "catalog_tool_id": "custom_external_01",
                    "directed": "true",
                    "sign": "none",
                },
                "directed must be a boolean",
            ),
            (
                {
                    "tool_origin": "custom",
                    "catalog_tool_id": "custom_external_01",
                    "directed": True,
                    "sign": "sometimes",
                },
                "sign must be one of",
            ),
            (
                {
                    "catalog_tool_id": "custom_external_01",
                    "directed": True,
                    "sign": "none",
                },
                "must contain exactly",
            ),
            (
                {
                    "tool_origin": "external",
                    "catalog_tool_id": "custom_external_01",
                    "directed": True,
                    "sign": "none",
                },
                "tool_origin must be one of",
            ),
            (
                {
                    "tool_origin": "custom",
                    "catalog_tool_id": "custom_external_01",
                    "directed": True,
                    "sign": "none",
                    "evidence": "association",
                },
                "must contain exactly",
            ),
        ]
        for frozen, expected in invalid_values:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                frozen_origin = (
                    frozen["tool_origin"]
                    if isinstance(frozen, dict)
                    and isinstance(frozen.get("tool_origin"), str)
                    else "custom"
                )
                run_report_path, manifest_path = self._write_evaluation_fixture(
                    base=base,
                    manifest=self._manifest(),
                    truth_rows=[
                        {
                            "source": "A",
                            "target": "B",
                            "score": "1",
                            "sign": "+",
                            "evidence": "simulated_truth",
                            "context": "global",
                        }
                    ],
                    prediction_rows=[
                        {
                            "source": "A",
                            "target": "B",
                            "score": "0.9",
                            "sign": "?",
                            "evidence": "association",
                            "context": "global",
                            "tool_id": "external_01",
                        }
                    ],
                    run_tools={
                        "selected": ["external_01"],
                        "catalog_tool_ids": {
                            "external_01": "custom_external_01",
                        },
                        "tool_origins": {"external_01": frozen_origin},
                        "output_capabilities": {"external_01": frozen},
                    },
                    genes="A\nB\nC\n",
                )

                with self.assertRaisesRegex(ValueError, expected):
                    evaluate_inference(
                        run_report_path=run_report_path,
                        ground_truth_manifest_path=manifest_path,
                        output_dir=base / "evaluation",
                        generate_view=False,
                    )

    def test_rejects_inference_reports_with_inconsistent_capability_maps(self) -> None:
        valid_capability = {
            "tool_origin": "catalog",
            "catalog_tool_id": "genie3",
            "directed": True,
            "sign": "none",
        }
        cases = [
            (
                {
                    "catalog_tool_ids": {"genie3__01": "genie3"},
                    "tool_origins": {"genie3__01": "catalog"},
                    "output_capabilities": {"genie3__01": valid_capability},
                },
                "tools.selected must be a non-empty array",
            ),
            (
                {
                    "selected": ["genie3__01"],
                    "catalog_tool_ids": {"genie3__01": "genie3"},
                    "tool_origins": {"genie3__01": "catalog"},
                    "output_capabilities": {
                        "genie3__01": valid_capability,
                        "extra_01": valid_capability,
                    },
                },
                "tools.output_capabilities must be an object with exactly",
            ),
            (
                {
                    "selected": ["genie3__01"],
                    "catalog_tool_ids": {},
                    "tool_origins": {"genie3__01": "catalog"},
                    "output_capabilities": {"genie3__01": valid_capability},
                },
                "tools.catalog_tool_ids must be an object with exactly",
            ),
            (
                {
                    "selected": ["genie3__01"],
                    "catalog_tool_ids": {"genie3__01": "different"},
                    "tool_origins": {"genie3__01": "catalog"},
                    "output_capabilities": {"genie3__01": valid_capability},
                },
                "catalog_tool_id must match .*tools.catalog_tool_ids",
            ),
        ]
        for run_tools, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                run_report_path, manifest_path = self._write_evaluation_fixture(
                    base=base,
                    manifest=self._manifest(),
                    truth_rows=[
                        {
                            "source": "A",
                            "target": "B",
                            "score": "1",
                            "sign": "+",
                            "evidence": "simulated_truth",
                            "context": "global",
                        }
                    ],
                    prediction_rows=[
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
                    run_tools=run_tools,
                    genes="A\nB\nC\n",
                )

                with self.assertRaisesRegex(ValueError, expected):
                    evaluate_inference(
                        run_report_path=run_report_path,
                        ground_truth_manifest_path=manifest_path,
                        output_dir=base / "evaluation",
                        generate_view=False,
                    )

    def test_requires_explicit_candidate_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = self._manifest()
            del manifest["candidate_space"]
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=manifest,
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
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
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\nC\n",
            )

            with self.assertRaisesRegex(ValueError, "candidate_space is required"):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_rejects_unsafe_candidate_space_references(self) -> None:
        cases = [
            ("", "non-empty relative POSIX path"),
            ("/tmp/candidate_genes.txt", "relative to its manifest"),
            ("C:/tmp/candidate_genes.txt", "relative to its manifest"),
            ("../truth/gene_universe.txt", r"must not contain.*'\.\.'"),
            ("./truth/gene_universe.txt", r"must not contain.*'\.'"),
            ("truth//gene_universe.txt", "must not contain empty"),
            ("truth\\gene_universe.txt", "portable POSIX path"),
            ("truth/gene_universe.txt\n", "surrounding whitespace"),
            ("truth/gene_universe.csv", r"must use the '\.txt' extension"),
        ]
        for key in ("sources", "targets"):
            for value, expected in cases:
                with (
                    self.subTest(key=key, value=value),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    base = Path(tmp)
                    manifest = self._manifest()
                    manifest["candidate_space"][key] = value
                    run_report_path, manifest_path = self._write_evaluation_fixture(
                        base=base,
                        manifest=manifest,
                        truth_rows=[
                            {
                                "source": "A",
                                "target": "B",
                                "score": "1",
                                "sign": "+",
                                "evidence": "simulated_truth",
                                "context": "global",
                            }
                        ],
                        prediction_rows=[
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
                        run_tools=self._run_tools(
                            run_id="genie3__01",
                            catalog_tool_id="genie3",
                            directed=True,
                            sign="none",
                        ),
                        genes="A\nB\nC\n",
                    )

                    with self.assertRaisesRegex(ValueError, expected):
                        evaluate_inference(
                            run_report_path=run_report_path,
                            ground_truth_manifest_path=manifest_path,
                            output_dir=base / "evaluation",
                            generate_view=False,
                        )

    def test_rejects_unsafe_ground_truth_output_references(self) -> None:
        cases_by_key = {
            "gene_universe": [
                ("", "non-empty relative POSIX path"),
                ("/tmp/gene_universe.txt", "relative to its manifest"),
                ("../truth/gene_universe.txt", r"must not contain.*'\.\.'"),
                ("truth\\gene_universe.txt", "portable POSIX path"),
                ("truth/gene_universe.csv", r"must use the '\.txt' extension"),
            ],
            "networks": [
                ("", "non-empty relative POSIX path"),
                ("C:/tmp/networks.csv", "relative to its manifest"),
                ("truth/../networks.csv", r"must not contain.*'\.\.'"),
                ("truth//networks.csv", "must not contain empty"),
                ("truth\\networks.csv", "portable POSIX path"),
                ("truth/networks.csv\r", "surrounding whitespace"),
                ("truth/networks.txt", r"must use the '\.csv' extension"),
            ],
        }
        for key, cases in cases_by_key.items():
            for value, expected in cases:
                with (
                    self.subTest(key=key, value=value),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    base = Path(tmp)
                    manifest = self._manifest()
                    manifest["outputs"][key] = value
                    run_report_path, manifest_path = self._write_evaluation_fixture(
                        base=base,
                        manifest=manifest,
                        truth_rows=[
                            {
                                "source": "A",
                                "target": "B",
                                "score": "1",
                                "sign": "+",
                                "evidence": "simulated_truth",
                                "context": "global",
                            }
                        ],
                        prediction_rows=[
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
                        run_tools=self._run_tools(
                            run_id="genie3__01",
                            catalog_tool_id="genie3",
                            directed=True,
                            sign="none",
                        ),
                        genes="A\nB\nC\n",
                    )

                    with self.assertRaisesRegex(ValueError, expected):
                        evaluate_inference(
                            run_report_path=run_report_path,
                            ground_truth_manifest_path=manifest_path,
                            output_dir=base / "evaluation",
                            generate_view=False,
                        )

    def test_rejects_candidate_space_with_self_edges_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = self._manifest()
            manifest["candidate_space"]["allow_self_edges"] = True
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=manifest,
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
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
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\nC\n",
            )

            with self.assertRaisesRegex(
                ValueError,
                "candidate_space.allow_self_edges must be false",
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_explicit_candidate_space_controls_each_evaluation_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = self._manifest()
            manifest["candidate_space"] = {
                "sources": "extras/tf_list.txt",
                "targets": "truth/targets.txt",
                "allow_self_edges": False,
            }
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=manifest,
                truth_rows=[
                    {
                        "source": "A",
                        "target": "C",
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
                ],
                prediction_rows=[
                    {
                        "source": source,
                        "target": target,
                        "score": score,
                        "sign": sign,
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "external_01",
                    }
                    for source, target, score, sign in [
                        ("A", "C", "0.9", "+"),
                        # Reverse direction remains a valid topology candidate,
                        # but is excluded from directed and signed metrics.
                        ("C", "A", "0.8", "+"),
                        ("D", "A", "0.7", "+"),
                        ("A", "D", "0.6", "+"),
                        ("A", "A", "0.5", "+"),
                    ]
                ],
                run_tools={
                    **self._run_tools(
                        run_id="external_01",
                        catalog_tool_id="custom_external_01",
                        directed=True,
                        sign="signed",
                        tool_origin="custom",
                    )
                },
                genes="A\nB\nC\nD\n",
                extra_files={
                    "extras/tf_list.txt": "A\nB\n",
                    "truth/targets.txt": "A\nB\nC\n",
                },
            )

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        metrics = {row["level"]: row for row in report["metrics"]}
        self.assertEqual(metrics["topology"]["n_candidates"], 3)
        self.assertEqual(metrics["directed"]["n_candidates"], 4)
        self.assertEqual(metrics["signed"]["n_candidates"], 8)
        self.assertEqual(metrics["topology"]["n_predicted_edges"], 1)
        self.assertEqual(metrics["directed"]["n_predicted_edges"], 1)
        self.assertEqual(
            metrics["topology"]["n_prediction_rows_outside_candidate_space"],
            3,
        )
        self.assertEqual(
            metrics["directed"]["n_prediction_rows_outside_candidate_space"],
            4,
        )
        self.assertEqual(metrics["directed"]["n_candidate_sources"], 2)
        self.assertEqual(metrics["directed"]["n_candidate_targets"], 3)
        self.assertEqual(
            report["ground_truth"]["candidate_space"],
            {
                "mode": "explicit",
                "sources": "extras/tf_list.txt",
                "targets": "truth/targets.txt",
                "allow_self_edges": False,
                "n_sources": 2,
                "n_targets": 3,
                "n_source_target_overlap": 2,
                "truth_rows_total": 2,
                "n_truth_rows_outside_candidate_space_by_level": {
                    "topology": 0,
                    "directed": 0,
                    "signed": 0,
                },
                "n_truth_edges_outside_candidate_space_by_level": {
                    "topology": 0,
                    "directed": 0,
                    "signed": 0,
                },
                "truth_filtering_by_context": [],
                "n_candidates_by_level": {
                    "topology": 3,
                    "directed": 4,
                    "signed": 8,
                },
            },
        )
        pairing = report["pairings"][0]
        self.assertEqual(
            pairing["n_prediction_rows_outside_candidate_space_topology"], 3
        )
        self.assertEqual(
            pairing["n_prediction_rows_outside_candidate_space_directed"], 4
        )

    def test_filters_and_reports_truth_outside_explicit_candidate_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = self._manifest()
            manifest["candidate_space"] = {
                "sources": "extras/tf_list.txt",
                "targets": "truth/targets.txt",
                "allow_self_edges": False,
            }
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=manifest,
                truth_rows=[
                    {
                        "source": "C",
                        "target": "A",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    },
                    {
                        "source": "A",
                        "target": "A",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    },
                ],
                prediction_rows=[
                    {
                        "source": "A",
                        "target": "C",
                        "score": "0.9",
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "genie3__01",
                    }
                ],
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\nC\n",
                extra_files={
                    "extras/tf_list.txt": "A\nB\n",
                    "truth/targets.txt": "A\nB\nC\n",
                },
            )

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        metrics = {row["level"]: row for row in report["metrics"]}
        self.assertEqual(report["ground_truth"]["contexts"], ["global"])
        self.assertEqual(metrics["topology"]["n_truth_edges"], 1)
        self.assertEqual(metrics["topology"]["n_truth_rows_outside_candidate_space"], 1)
        self.assertEqual(metrics["directed"]["n_truth_edges"], 0)
        self.assertEqual(metrics["directed"]["n_truth_rows_outside_candidate_space"], 2)
        self.assertEqual(
            metrics["directed"]["n_truth_edges_outside_candidate_space"], 2
        )
        self.assertEqual(
            metrics["directed"]["truth_outside_candidate_space_examples"],
            "A->A; C->A",
        )
        self.assertEqual(metrics["directed"]["status"], "partial")
        pairing = report["pairings"][0]
        self.assertEqual(pairing["n_truth_rows_outside_candidate_space_topology"], 1)
        self.assertEqual(pairing["n_truth_rows_outside_candidate_space_directed"], 2)
        candidate_report = report["ground_truth"]["candidate_space"]
        self.assertEqual(candidate_report["truth_rows_total"], 2)
        self.assertEqual(
            candidate_report["n_truth_rows_outside_candidate_space_by_level"],
            {"topology": 1, "directed": 2, "signed": 2},
        )
        self.assertEqual(
            candidate_report["truth_filtering_by_context"][0]["context"],
            "global",
        )

    def test_rejects_candidate_genes_outside_gene_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = self._manifest()
            manifest["candidate_space"] = {
                "sources": "extras/tf_list.txt",
                "targets": "truth/gene_universe.txt",
                "allow_self_edges": False,
            }
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=manifest,
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
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
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\nC\n",
                extra_files={"extras/tf_list.txt": "A\nUNKNOWN\n"},
            )

            with self.assertRaisesRegex(
                ValueError, "candidate_space.sources contains genes outside"
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_rejects_duplicate_genes_in_explicit_candidate_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = self._manifest()
            manifest["candidate_space"] = {
                "sources": "extras/tf_list.txt",
                "targets": "truth/gene_universe.txt",
                "allow_self_edges": False,
            }
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=manifest,
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
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
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\nC\n",
                extra_files={"extras/tf_list.txt": "A\nA\n"},
            )

            with self.assertRaisesRegex(ValueError, "contains duplicate gene 'A'"):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_rejects_duplicate_ground_truth_gene_universe_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
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
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nA\nB\n",
            )

            with self.assertRaisesRegex(
                ValueError,
                "Ground-truth gene universe file contains duplicate gene 'A'",
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

    def test_rejects_predictions_with_genes_outside_gene_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = self._manifest()
            manifest["candidate_space"] = {
                "sources": "extras/tf_list.txt",
                "targets": "truth/gene_universe.txt",
                "allow_self_edges": False,
            }
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=manifest,
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
                    {
                        "source": "UNKNOWN",
                        "target": "B",
                        "score": "0.9",
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "genie3__01",
                    }
                ],
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\nC\n",
                extra_files={"extras/tf_list.txt": "A\n"},
            )

            with self.assertRaisesRegex(
                ValueError, "Inferred network contains genes outside"
            ):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                    generate_view=False,
                )

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
                        "status": "executed",
                        "outputs": {
                            "merged_network_raw": inferred_path.name,
                            "rows_per_tool": {"genie3__01": 1},
                        },
                        "tools": self._run_tools(
                            run_id="genie3__01",
                            catalog_tool_id="genie3",
                            directed=True,
                            sign="none",
                        ),
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

    def test_accepts_safe_relative_merged_network_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "?",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
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
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\n",
            )
            nested = base / "networks" / "result.csv"
            nested.parent.mkdir()
            (base / "merged_network_raw.csv").replace(nested)
            run_report = json.loads(run_report_path.read_text(encoding="utf-8"))
            run_report["outputs"]["merged_network_raw"] = "networks/result.csv"
            run_report_path.write_text(json.dumps(run_report), encoding="utf-8")

            report = evaluate_inference(
                run_report_path=run_report_path,
                ground_truth_manifest_path=manifest_path,
                output_dir=base / "evaluation",
                generate_view=False,
            )

        self.assertEqual(len(report["pairings"]), 1)

    def test_rejects_merged_network_symlink_outside_analysis_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "bundle"
            run_report_path, manifest_path = self._write_evaluation_fixture(
                base=base,
                manifest=self._manifest(),
                truth_rows=[
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
                    }
                ],
                prediction_rows=[
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
                run_tools=self._run_tools(
                    run_id="genie3__01",
                    catalog_tool_id="genie3",
                    directed=True,
                    sign="none",
                ),
                genes="A\nB\n",
            )
            merged = base / "merged_network_raw.csv"
            outside = root / "outside.csv"
            merged.replace(outside)
            merged.symlink_to(outside)

            with self.assertRaisesRegex(ValueError, "resolves outside"):
                evaluate_inference(
                    run_report_path=run_report_path,
                    ground_truth_manifest_path=manifest_path,
                    output_dir=base / "evaluation",
                )

    def test_rejects_inconsistent_final_inference_report(self) -> None:
        cases = (
            (
                "missing_run_id",
                lambda report: report.pop("run_id"),
                "Run report run_id",
            ),
            (
                "status",
                lambda report: report.__setitem__("status", "planned"),
                "status must be exactly 'executed'",
            ),
            (
                "row_count",
                lambda report: report["outputs"]["rows_per_tool"].__setitem__(
                    "genie3__01", 2
                ),
                "row counts must exactly match",
            ),
            (
                "completed",
                lambda report: report["tools"].__setitem__("completed", []),
                "tools.completed must be a non-empty array",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                run_report_path, manifest_path = self._write_evaluation_fixture(
                    base=base,
                    manifest=self._manifest(),
                    truth_rows=[
                        {
                            "source": "A",
                            "target": "B",
                            "score": "1",
                            "sign": "+",
                            "evidence": "simulated_truth",
                            "context": "global",
                        }
                    ],
                    prediction_rows=[
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
                    run_tools=self._run_tools(
                        run_id="genie3__01",
                        catalog_tool_id="genie3",
                        directed=True,
                        sign="none",
                    ),
                    genes="A\nB\n",
                )
                report = json.loads(run_report_path.read_text(encoding="utf-8"))
                mutate(report)
                run_report_path.write_text(json.dumps(report), encoding="utf-8")

                with self.assertRaisesRegex(ValueError, expected):
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

    def _write_evaluation_fixture(
        self,
        *,
        base: Path,
        manifest: dict[str, object],
        truth_rows: list[dict[str, str]],
        prediction_rows: list[dict[str, str]],
        run_tools: dict[str, object],
        genes: str,
        extra_files: dict[str, str] | None = None,
        run_inputs: dict[str, object] | None = None,
        inference_dataset_id: str | None = None,
    ) -> tuple[Path, Path]:
        truth_dir = base / "truth"
        truth_dir.mkdir(parents=True)
        (truth_dir / "gene_universe.txt").write_text(genes, encoding="utf-8")
        self._write_csv(truth_dir / "networks.csv", truth_rows)
        for relative_path, content in (extra_files or {}).items():
            path = base / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        manifest_path = base / "ground-truth-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        inferred_path = base / "merged_network_raw.csv"
        if prediction_rows:
            self._write_csv(inferred_path, prediction_rows)
        else:
            inferred_path.write_text(
                "source,target,score,sign,evidence,context,tool_id\n",
                encoding="utf-8",
            )
        run_report_path = base / "run_report.json"
        rows_per_tool: dict[str, int] = {}
        for row in prediction_rows:
            tool_id = row["tool_id"]
            rows_per_tool[tool_id] = rows_per_tool.get(tool_id, 0) + 1
        completed = run_tools.get("completed")
        if isinstance(completed, list):
            for run_id in completed:
                if isinstance(run_id, str):
                    rows_per_tool.setdefault(run_id, 0)
        manifest_dataset_id = manifest.get("dataset_id")
        run_report = self._terminal_run_report(
            run_tools=run_tools,
            rows_per_tool=rows_per_tool,
            run_inputs=run_inputs,
            gene_count=max(1, len([line for line in genes.splitlines() if line])),
            dataset_id=(
                inference_dataset_id
                if inference_dataset_id is not None
                else str(manifest_dataset_id)
            ),
        )
        run_report_path.write_text(json.dumps(run_report), encoding="utf-8")
        return run_report_path, manifest_path


if __name__ == "__main__":
    unittest.main()
