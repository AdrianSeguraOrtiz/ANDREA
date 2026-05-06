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
    _metric_value,
    _plot_scale_max,
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

    def test_zero_truth_scores_do_not_create_positive_edges(self) -> None:
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

    def test_epr_plot_values_are_not_clamped_to_unit_interval(self) -> None:
        self.assertEqual(
            _metric_value({"epr_at_truth_count": 6.0}, "epr_at_truth_count"),
            6.0,
        )
        self.assertEqual(
            _plot_scale_max([0.5, 6.0], metric="epr_at_truth_count"),
            6.0,
        )

    def test_evaluates_topology_directed_and_signed_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            truth_dir = base / "truth"
            truth_dir.mkdir(parents=True)
            self._write_csv(
                truth_dir / "global_network.csv",
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
                        "score": "0",
                        "sign": "+",
                        "evidence": "simulated_truth",
                        "context": "global",
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
                            "global_network": "truth/global_network.csv",
                            "group_networks": [],
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
            plot_paths = [
                output_root / entry["path"] for entry in report["outputs"]["plots"]
            ]
            plot_paths_exist = all(path.exists() for path in plot_paths)

        metrics = {(row["tool_id"], row["level"]): row for row in report["metrics"]}
        self.assertEqual(metrics[("genie3__01", "topology")]["status"], "ok")
        self.assertAlmostEqual(
            metrics[("genie3__01", "topology")]["f1_at_truth_count"], 1.0
        )
        self.assertAlmostEqual(
            metrics[("genie3__01", "topology")]["epr_at_truth_count"], 1.5
        )
        self.assertEqual(metrics[("genie3__01", "directed")]["status"], "ok")
        self.assertAlmostEqual(
            metrics[("genie3__01", "directed")]["f1_at_truth_count"], 0.5
        )
        self.assertAlmostEqual(
            metrics[("genie3__01", "directed")]["epr_at_truth_count"], 1.5
        )
        self.assertEqual(metrics[("genie3__01", "signed")]["status"], "not_applicable")
        self.assertEqual(metrics[("signed_tool", "signed")]["status"], "ok")
        self.assertAlmostEqual(
            metrics[("signed_tool", "signed")]["f1_at_truth_count"], 1.0
        )
        self.assertAlmostEqual(
            metrics[("signed_tool", "signed")]["epr_at_truth_count"], 6.0
        )
        self.assertEqual(Path(report["outputs"]["output_root"]), Path("."))
        self.assertEqual(evaluation_dir.parent, output_root)
        self.assertTrue(evaluation_dir.name.startswith("evaluation_"))
        self.assertTrue(metrics_csv_exists)
        self.assertTrue(pairings_csv_exists)
        self.assertTrue(report_json_exists)
        self.assertTrue(plot_paths)
        self.assertTrue(plot_paths_exist)
        self.assertEqual(report["inputs"]["inference_dataset_id"], None)
        self.assertEqual(report["inputs"]["ground_truth_dataset_id"], "toy")
        self.assertEqual(report["inputs"]["ground_truth_simulator_id"], "toy_sim")
        self.assertEqual(report["inputs"]["merged_network"], "merged_network_raw")
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
            self._write_csv(
                truth_dir / "global_network.csv",
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
                            "global_network": "truth/global_network.csv",
                            "group_networks": [],
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
