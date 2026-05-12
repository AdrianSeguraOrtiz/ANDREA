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
    _top_truth_count_stats,
)


class EvaluateInferenceCoreTests(unittest.TestCase):
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

    def test_evaluates_topology_directed_and_signed_levels(self) -> None:
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
                        "source": "A",
                        "target": "C",
                        "score": "1",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "cell:cell_a",
                    },
                ],
            )
            manifest_path = base / "ground-truth-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "dataset_id": "toy",
                        "simulator_id": "toy_sim",
                        "profile": "scrna_grouped",
                        "outputs": {
                            "gene_universe": "truth/gene_universe.txt",
                            "networks": "truth/networks.csv",
                        },
                    }
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
                        "source": "A",
                        "target": "C",
                        "score": "0.6",
                        "sign": "?",
                        "evidence": "association",
                        "context": "cell:cell_a",
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
        self.assertEqual(metrics[("genie3__01", "cell:cell_a", "topology")]["status"], "ok")
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
        self.assertEqual(report["ground_truth"]["gene_universe_size"], 4)
        self.assertEqual(metrics[("genie3__01", "global", "topology")]["n_candidate_genes"], 4)
        self.assertIn("cell:cell_a", report["ground_truth"]["contexts"])
        self.assertIn("Other Contexts", view_html)
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
                    {
                        "schema_version": "1.0",
                        "dataset_id": "toy",
                        "simulator_id": "toy_sim",
                        "profile": "scrna_grouped",
                        "outputs": {
                            "gene_universe": "truth/gene_universe.txt",
                            "networks": "truth/networks.csv",
                        },
                    }
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
                    {
                        "schema_version": "1.0",
                        "dataset_id": "toy",
                        "simulator_id": "toy_sim",
                        "profile": "scrna_grouped",
                        "outputs": {
                            "networks": "truth/networks.csv",
                        },
                    }
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
