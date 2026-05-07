from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from andrea.core.commands.compare_networks import compare_networks
from andrea.core.commands.compare_networks.comparison import _build_distance_coordinates


class CompareNetworksCoreTests(unittest.TestCase):
    def test_builds_phase_one_comparison_tables_from_normalized_networks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_a = base / "run_a"
            run_b = base / "run_b"
            run_a.mkdir()
            run_b.mkdir()
            self._write_normalized_network(
                run_a / "merged_network_normalized.csv",
                [
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.5",
                        "sign": "+",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "genie3_01",
                    },
                    {
                        "source": "G2",
                        "target": "G1",
                        "score": "0.9",
                        "sign": "-",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "genie3_01",
                    },
                    {
                        "source": "G1",
                        "target": "G3",
                        "score": "0.4",
                        "sign": "?",
                        "evidence": "a",
                        "context": "group:sA",
                        "tool_id": "clr_01",
                    },
                ],
            )
            self._write_normalized_network(
                run_b / "merged_network_normalized.csv",
                [
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "1",
                        "sign": "+",
                        "evidence": "b",
                        "context": "global",
                        "tool_id": "grnboost2_01",
                    }
                ],
            )
            self._write_run_report(
                run_a / "run_report.json",
                run_id="run_a",
                catalog_ids={"genie3_01": "genie3", "clr_01": "clr"},
            )
            self._write_run_report(
                run_b / "run_report.json",
                run_id="run_b",
                catalog_ids={"grnboost2_01": "grnboost2"},
            )
            evaluation_report = base / "evaluation_report.json"
            evaluation_report.write_text(
                json.dumps({"schema_version": "1.0", "metrics": []}) + "\n",
                encoding="utf-8",
            )
            request = base / "comparison-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "toy_compare",
                        "sources": [
                            {
                                "source_id": "source_a",
                                "label": "Source A",
                                "run_report": "run_a/run_report.json",
                                "evaluation_report": "evaluation_report.json",
                            },
                            {
                                "source_id": "source_b",
                                "run_report": "run_b/run_report.json",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = compare_networks(request_path=request, output_dir=base / "out")
            output_root = base / "out"
            comparison_dir = output_root / report["outputs"]["comparison_dir"]
            network_index = self._read_csv(
                output_root / report["outputs"]["network_index_csv"]
            )
            edge_scores = self._read_csv(
                output_root / report["outputs"]["edge_scores_csv"]
            )
            distance_coordinates_csv_exists = (
                output_root / report["outputs"]["distance_coordinates_csv"]
            ).exists()
            view_path = output_root / report["outputs"]["comparison_view"]
            view_exists = view_path.exists()
            view_html = view_path.read_text(encoding="utf-8") if view_exists else ""
            copied_request = json.loads(
                (output_root / report["outputs"]["comparison_request"]).read_text(
                    encoding="utf-8"
                )
            )
            copied_source = copied_request["sources"][0]
            copied_run_report = json.loads(
                (
                    comparison_dir / copied_source["run_report"]
                ).read_text(encoding="utf-8")
            )
            frozen_network_exists = (
                comparison_dir
                / "input"
                / "sources"
                / "source_a"
                / "merged_network_normalized.csv"
            ).exists()

        self.assertTrue(comparison_dir.name.startswith("comparison_toy_compare_"))
        self.assertEqual(report["summary"]["sources"], 2)
        self.assertEqual(report["summary"]["network_instances"], 9)
        self.assertEqual(report["contexts"], ["global", "group:sA"])
        self.assertEqual(report["distances_available"], [])
        self.assertEqual(report["metrics_available"], [])
        self.assertEqual(report["summary"]["coordinate_rows"], 0)
        self.assertTrue(distance_coordinates_csv_exists)
        self.assertTrue(view_exists)
        self.assertIn("Network Comparison", view_html)
        self.assertIn("Ordered Edge Differences", view_html)
        self.assertIn('id="comparison-view-root"', view_html)
        self.assertIn("AndreaComparisonView.render", view_html)
        self.assertIn("updateSelectedNetworks", view_html)
        self.assertEqual(len(report["edge_scores"]), len(edge_scores))
        for value in report["outputs"].values():
            if value is not None:
                self.assertFalse(Path(value).is_absolute())
        self.assertEqual(
            copied_source["run_report"],
            "input/sources/source_a/run_report.json",
        )
        self.assertEqual(
            copied_source["evaluation_report"],
            "input/sources/source_a/evaluation_report.json",
        )
        self.assertEqual(
            copied_run_report["outputs"]["merged_network_normalized"],
            "merged_network_normalized.csv",
        )
        self.assertTrue(frozen_network_exists)
        self.assertEqual(
            {
                (row["source_id"], row["tool_id"], row["context"], row["level"])
                for row in network_index
            },
            {
                ("source_a", "genie3_01", "global", "topology"),
                ("source_a", "genie3_01", "global", "directed"),
                ("source_a", "genie3_01", "global", "signed"),
                ("source_a", "clr_01", "group:sA", "topology"),
                ("source_a", "clr_01", "group:sA", "directed"),
                ("source_a", "clr_01", "group:sA", "signed"),
                ("source_b", "grnboost2_01", "global", "topology"),
                ("source_b", "grnboost2_01", "global", "directed"),
                ("source_b", "grnboost2_01", "global", "signed"),
            },
        )
        topology_edge = next(
            row
            for row in edge_scores
            if row["source_id"] == "source_a"
            and row["tool_id"] == "genie3_01"
            and row["level"] == "topology"
        )
        self.assertEqual(topology_edge["edge_key"], "G1|G2")
        self.assertEqual(topology_edge["score"], "0.9")
        signed_edges = [
            row
            for row in edge_scores
            if row["source_id"] == "source_a"
            and row["tool_id"] == "clr_01"
            and row["level"] == "signed"
        ]
        self.assertEqual(signed_edges, [])

    def test_calculates_weighted_jaccard_and_rank_overlap_distances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_a = base / "run_a"
            run_b = base / "run_b"
            run_a.mkdir()
            run_b.mkdir()
            self._write_normalized_network(
                run_a / "merged_network_normalized.csv",
                [
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.8",
                        "sign": "+",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_a",
                    },
                    {
                        "source": "G2",
                        "target": "G3",
                        "score": "0.4",
                        "sign": "+",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_a",
                    },
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.2",
                        "sign": "+",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_b",
                    },
                    {
                        "source": "G1",
                        "target": "G3",
                        "score": "0.6",
                        "sign": "+",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_b",
                    },
                ],
            )
            self._write_normalized_network(
                run_b / "merged_network_normalized.csv",
                [
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "1",
                        "sign": "+",
                        "evidence": "b",
                        "context": "global",
                        "tool_id": "tool_c",
                    },
                    {
                        "source": "G1",
                        "target": "G3",
                        "score": "0.5",
                        "sign": "+",
                        "evidence": "b",
                        "context": "global",
                        "tool_id": "tool_d",
                    },
                ],
            )
            self._write_run_report(
                run_a / "run_report.json",
                run_id="run_a",
                catalog_ids={"tool_a": "genie3", "tool_b": "clr"},
            )
            self._write_run_report(
                run_b / "run_report.json",
                run_id="run_b",
                catalog_ids={"tool_c": "aracne3", "tool_d": "grnboost2"},
            )
            eval_a = base / "evaluation_a.json"
            eval_a.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "metrics": [
                            {
                                "tool_id": "tool_a",
                                "context": "global",
                                "level": "topology",
                                "status": "ok",
                                "n_truth_edges": 2,
                                "aupr": 0.7,
                            },
                            {
                                "tool_id": "tool_b",
                                "context": "global",
                                "level": "topology",
                                "status": "ok",
                                "n_truth_edges": 2,
                                "aupr": 0.6,
                            },
                            {
                                "tool_id": "tool_a",
                                "context": "global",
                                "level": "directed",
                                "status": "ok",
                                "n_truth_edges": 2,
                                "aupr": 0.65,
                            },
                            {
                                "tool_id": "tool_b",
                                "context": "global",
                                "level": "directed",
                                "status": "not_applicable",
                                "n_truth_edges": 0,
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            eval_b = base / "evaluation_b.json"
            eval_b.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "metrics": [
                            {
                                "tool_id": "tool_c",
                                "context": "global",
                                "level": "topology",
                                "status": "ok",
                                "n_truth_edges": 3,
                                "aupr": 0.5,
                            },
                            {
                                "tool_id": "tool_d",
                                "context": "global",
                                "level": "topology",
                                "status": "ok",
                                "n_truth_edges": 3,
                                "aupr": 0.4,
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            request = base / "comparison-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "distance_compare",
                        "sources": [
                            {
                                "source_id": "source_a",
                                "run_report": "run_a/run_report.json",
                                "evaluation_report": "evaluation_a.json",
                            },
                            {
                                "source_id": "source_b",
                                "run_report": "run_b/run_report.json",
                                "evaluation_report": "evaluation_b.json",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = compare_networks(request_path=request, output_dir=base / "out")
            output_root = base / "out"
            distances = self._read_csv(output_root / report["outputs"]["distances_csv"])
            coordinates = self._read_csv(
                output_root / report["outputs"]["distance_coordinates_csv"]
            )

        self.assertEqual(
            report["distances_available"],
            ["weighted_jaccard_distance", "rank_overlap_distance_at_truth_count"],
        )
        self.assertEqual(report["metrics_available"], ["aupr"])
        self.assertTrue(
            any(
                "truth_count differs across sources" in item
                for item in report["warnings"]
            )
        )
        weighted = next(
            row
            for row in distances
            if row["source_id"] == "source_a"
            and row["level"] == "topology"
            and row["distance_metric"] == "weighted_jaccard_distance"
        )
        self.assertEqual(weighted["status"], "ok")
        self.assertEqual(weighted["n_common_genes"], "3")
        self.assertEqual(weighted["n_edges_considered"], "3")
        self.assertAlmostEqual(float(weighted["distance"]), 1.0 - (0.2 / 1.8))

        rank = next(
            row
            for row in distances
            if row["source_id"] == "source_a"
            and row["level"] == "topology"
            and row["distance_metric"] == "rank_overlap_distance_at_truth_count"
        )
        self.assertEqual(rank["status"], "ok")
        self.assertEqual(rank["n_edges_considered"], "2")
        self.assertAlmostEqual(float(rank["distance"]), 0.5)
        directed_rank = next(
            row
            for row in distances
            if row["source_id"] == "source_a"
            and row["level"] == "directed"
            and row["distance_metric"] == "rank_overlap_distance_at_truth_count"
        )
        self.assertEqual(directed_rank["status"], "ok")
        self.assertEqual(directed_rank["n_edges_considered"], "2")
        self.assertFalse(
            any(
                "using minimum value 0" in item
                for item in report["warnings"]
            )
        )
        self.assertTrue(
            any(
                row["source_id"] == "source_a"
                and row["context"] == "global"
                and row["level"] == "topology"
                and row["distance_metric"] == "weighted_jaccard_distance"
                and row["status"] == "ok"
                for row in coordinates
            )
        )
        self.assertEqual(
            sorted(report["outputs"]),
            [
                "comparison_dir",
                "comparison_report",
                "comparison_request",
                "comparison_view",
                "distance_coordinates_csv",
                "distances_csv",
                "edge_scores_csv",
                "network_index_csv",
                "output_root",
            ],
        )

    def test_rank_overlap_is_unavailable_without_evaluation_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "run"
            run_dir.mkdir()
            self._write_normalized_network(
                run_dir / "merged_network_normalized.csv",
                [
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.9",
                        "sign": "+",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_a",
                    },
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.7",
                        "sign": "+",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_b",
                    },
                ],
            )
            self._write_run_report(
                run_dir / "run_report.json",
                run_id="run",
                catalog_ids={"tool_a": "genie3", "tool_b": "clr"},
            )
            request = base / "comparison-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "no_eval",
                        "sources": [
                            {
                                "source_id": "source_a",
                                "run_report": "run/run_report.json",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = compare_networks(request_path=request, output_dir=base / "out")
            output_root = base / "out"
            distances = self._read_csv(output_root / report["outputs"]["distances_csv"])

        self.assertEqual(report["distances_available"], ["weighted_jaccard_distance"])
        rank = next(
            row
            for row in distances
            if row["distance_metric"] == "rank_overlap_distance_at_truth_count"
        )
        self.assertEqual(rank["status"], "not_available")
        self.assertIn("truth_count is unavailable", rank["warning"])

    def test_can_write_into_prepared_comparison_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            comparison_dir = output_dir / "comparison_prepared"
            source_dir = comparison_dir / "input" / "sources" / "source_a"
            source_dir.mkdir(parents=True)
            self._write_normalized_network(
                source_dir / "merged_network_normalized.csv",
                [
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.9",
                        "sign": "+",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_a",
                    }
                ],
            )
            self._write_run_report(
                source_dir / "run_report.json",
                run_id="prepared",
                catalog_ids={"tool_a": "genie3"},
            )
            request = comparison_dir / "comparison-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "prepared",
                        "sources": [
                            {
                                "source_id": "source_a",
                                "run_report": "input/sources/source_a/run_report.json",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = compare_networks(
                request_path=request,
                output_dir=output_dir,
                comparison_dir=comparison_dir,
            )

        self.assertEqual(report["outputs"]["comparison_dir"], "comparison_prepared")
        self.assertEqual(
            report["outputs"]["comparison_request"],
            "comparison_prepared/comparison-request.json",
        )
        self.assertEqual(report["summary"]["sources"], 1)

    def test_requires_merged_network_normalized_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "run"
            run_dir.mkdir()
            (run_dir / "run_report.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": "run_without_normalized",
                        "outputs": {"merged_network_raw": "merged_network_raw.csv"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            request = base / "comparison-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "missing_norm",
                        "sources": [
                            {
                                "source_id": "source_a",
                                "run_report": "run/run_report.json",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "merged_network_normalized"):
                compare_networks(request_path=request, output_dir=base / "out")

    def test_rejects_duplicate_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            request = base / "comparison-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "duplicates",
                        "sources": [
                            {"source_id": "same", "run_report": "a.json"},
                            {"source_id": "same", "run_report": "b.json"},
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Duplicate comparison source_id"):
                compare_networks(request_path=request, output_dir=base / "out")

    def test_incomplete_distance_matrix_does_not_break_coordinates(self) -> None:
        distances = [
            {
                "source_id": "source_a",
                "context": "global",
                "level": "topology",
                "distance_metric": "weighted_jaccard_distance",
                "network_a": "network_a",
                "network_b": "network_b",
                "distance": "0.25",
                "status": "ok",
            },
            {
                "source_id": "source_a",
                "context": "global",
                "level": "topology",
                "distance_metric": "weighted_jaccard_distance",
                "network_a": "network_a",
                "network_b": "network_c",
                "distance": "0.75",
                "status": "ok",
            },
        ]

        coordinates, warnings = _build_distance_coordinates(distances)

        self.assertEqual(len(coordinates), 3)
        self.assertTrue(all(row["status"] == "not_available" for row in coordinates))
        self.assertTrue(any("incomplete distance matrix" in item for item in warnings))

    def test_shared_view_groups_signed_edge_differences_by_directed_pair(self) -> None:
        view_script = (
            Path("andrea/core/commands/compare_networks/view_assets/view.js")
        ).read_text(encoding="utf-8")

        self.assertIn("function edgeDifferenceKey", view_script)
        self.assertIn('if (level === "signed") return `${row.source}|${row.target}`;', view_script)
        self.assertIn("function keepEdgeDifferenceRow", view_script)
        self.assertIn("const representative = raw.find(Boolean);", view_script)

    def _write_run_report(
        self,
        path: Path,
        *,
        run_id: str,
        catalog_ids: dict[str, str],
    ) -> None:
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "run_id": run_id,
                    "status": "executed",
                    "dataset": {"id": f"dataset_{run_id}"},
                    "tools": {"catalog_tool_ids": catalog_ids},
                    "outputs": {
                        "merged_network_normalized": "merged_network_normalized.csv"
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_normalized_network(
        self,
        path: Path,
        rows: list[dict[str, str]],
    ) -> None:
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "source",
                    "target",
                    "score",
                    "sign",
                    "evidence",
                    "context",
                    "tool_id",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))


if __name__ == "__main__":
    unittest.main()
