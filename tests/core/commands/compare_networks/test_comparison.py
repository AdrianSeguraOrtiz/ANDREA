from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from andrea.core.commands.compare_networks import (
    compare_networks,
    export_edge_scores_csv_from_sqlite,
)
from andrea.core.commands.compare_networks.distances import build_distance_coordinates
from andrea.core.commands.compare_networks.store import (
    _anchored_mds_coordinates,
    _covariance_ellipse_axes,
    _trim_displacements,
    distance_view,
    edge_variability,
    write_comparison_store,
)


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
                    {
                        "source": "G2",
                        "target": "G3",
                        "score": "0.6",
                        "sign": "?",
                        "evidence": "a",
                        "context": "cell:c1",
                        "tool_id": "genie3_01",
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
            sqlite_path = output_root / report["outputs"]["comparison_sqlite"]
            sqlite_exists = sqlite_path.exists()
            with sqlite3.connect(sqlite_path) as conn:
                edge_score_rows = conn.execute(
                    "SELECT COUNT(*) FROM edge_scores"
                ).fetchone()[0]
                context_rows = conn.execute(
                    "SELECT COUNT(*) FROM context_summary"
                ).fetchone()[0]

        self.assertTrue(comparison_dir.name.startswith("comparison_toy_compare_"))
        self.assertEqual(report["summary"]["sources"], 2)
        self.assertEqual(report["summary"]["network_instances"], 12)
        self.assertEqual(report["contexts"], ["global", "group:sA", "cell:c1"])
        self.assertEqual(
            report["context_counts_by_family"],
            {"global": 1, "group": 1, "cell": 1, "other": 0},
        )
        self.assertEqual(report["distances_available"], [])
        self.assertEqual(report["metrics_available"], [])
        self.assertEqual(report["summary"]["coordinate_rows"], 0)
        self.assertIn("runtime_profile", report)
        runtime_stages = [entry["stage"] for entry in report["runtime_profile"]]
        self.assertEqual(
            runtime_stages[:5],
            [
                "loading_request",
                "loading_source_networks",
                "freezing_inputs",
                "building_network_tables",
                "computing_distances",
            ],
        )
        self.assertIn("writing_edge_scores_csv", runtime_stages)
        self.assertIn("writing_comparison_sqlite", runtime_stages)
        self.assertTrue(distance_coordinates_csv_exists)
        self.assertTrue(view_exists)
        self.assertIn("Network Comparison Static Report", view_html)
        self.assertIn("Interactive exploration lives in the local GUI", view_html)
        self.assertIn('id="comparison-view-root"', view_html)
        self.assertNotIn("updateSelectedNetworks", view_html)
        self.assertNotIn("renderDistanceMaps", view_html)
        self.assertNotIn("edge_scores", report)
        self.assertTrue(sqlite_exists)
        self.assertEqual(edge_score_rows, len(edge_scores))
        self.assertEqual(context_rows, len(report["contexts"]))
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
                ("source_a", "genie3_01", "cell:c1", "topology"),
                ("source_a", "genie3_01", "cell:c1", "directed"),
                ("source_a", "genie3_01", "cell:c1", "signed"),
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
            and row["context"] == "global"
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

    def test_edge_scores_are_never_embedded_in_report(self) -> None:
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
                        "score": "0.8",
                        "sign": "+",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_01",
                    },
                    {
                        "source": "G2",
                        "target": "G3",
                        "score": "0.6",
                        "sign": "-",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_01",
                    },
                ],
            )
            self._write_run_report(
                run_dir / "run_report.json",
                run_id="run_a",
                catalog_ids={"tool_01": "tool"},
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
                                "run_report": "run/run_report.json",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = compare_networks(request_path=request, output_dir=base / "out")

            output_root = base / "out"
            edge_scores = self._read_csv(output_root / report["outputs"]["edge_scores_csv"])
            sqlite_path = output_root / report["outputs"]["comparison_sqlite"]
            sqlite_exists = sqlite_path.exists()
            with sqlite3.connect(sqlite_path) as conn:
                sqlite_edge_score_rows = conn.execute("SELECT COUNT(*) FROM edge_scores").fetchone()[0]

        self.assertGreater(len(edge_scores), 1)
        self.assertEqual(report["summary"]["edge_score_rows"], len(edge_scores))
        self.assertNotIn("edge_scores", report)
        self.assertTrue(sqlite_exists)
        self.assertEqual(sqlite_edge_score_rows, len(edge_scores))

    def test_deferred_edge_score_csv_export_round_trips_from_sqlite(self) -> None:
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
                        "tool_id": "tool_a",
                    },
                    {
                        "source": "G2",
                        "target": "G3",
                        "score": "0.7",
                        "sign": "-",
                        "evidence": "a",
                        "context": "global",
                        "tool_id": "tool_a",
                    },
                ],
            )
            self._write_normalized_network(
                run_b / "merged_network_normalized.csv",
                [
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.9",
                        "sign": "+",
                        "evidence": "b",
                        "context": "global",
                        "tool_id": "tool_b",
                    }
                ],
            )
            self._write_run_report(
                run_a / "run_report.json",
                run_id="run_a",
                catalog_ids={"tool_a": "tool_a"},
            )
            self._write_run_report(
                run_b / "run_report.json",
                run_id="run_b",
                catalog_ids={"tool_b": "tool_b"},
            )
            request = base / "comparison-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "deferred_export",
                        "sources": [
                            {
                                "source_id": "source_a",
                                "run_report": "run_a/run_report.json",
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

            full_report = compare_networks(
                request_path=request,
                output_dir=base / "out_full",
            )
            deferred_report = compare_networks(
                request_path=request,
                output_dir=base / "out_deferred",
                write_edge_scores_csv=False,
            )
            full_edges_path = (
                base / "out_full" / full_report["outputs"]["edge_scores_csv"]
            )
            deferred_root = base / "out_deferred"
            deferred_edges_path = (
                deferred_root / deferred_report["outputs"]["edge_scores_csv"]
            )
            sqlite_path = deferred_root / deferred_report["outputs"]["comparison_sqlite"]
            deferred_runtime_stages = [
                item["stage"] for item in deferred_report["runtime_profile"]
            ]

            self.assertTrue(full_edges_path.exists())
            self.assertFalse(deferred_edges_path.exists())
            self.assertNotIn("writing_edge_scores_csv", deferred_runtime_stages)
            self.assertTrue(sqlite_path.exists())
            with sqlite3.connect(sqlite_path) as conn:
                edge_columns = [
                    row[1] for row in conn.execute("PRAGMA table_info(edge_scores)")
                ]
                edge_count = conn.execute(
                    "SELECT COUNT(*) FROM edge_scores"
                ).fetchone()[0]
                network_pk_type = conn.execute(
                    "SELECT typeof(network_pk) FROM edge_scores LIMIT 1"
                ).fetchone()[0]

            exported_count = export_edge_scores_csv_from_sqlite(
                sqlite_path=sqlite_path,
                output_path=deferred_edges_path,
            )

            self.assertIn("network_pk", edge_columns)
            self.assertNotIn("network_id", edge_columns)
            self.assertEqual(network_pk_type, "integer")
            self.assertEqual(exported_count, edge_count)
            self.assertEqual(
                self._read_csv(deferred_edges_path),
                self._read_csv(full_edges_path),
            )

    def test_cell_contexts_are_indexed_and_compared(self) -> None:
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
                        "context": "cell:c1",
                        "tool_id": "cell_tool_a",
                    },
                    {
                        "source": "G1",
                        "target": "G3",
                        "score": "0.5",
                        "sign": "+",
                        "evidence": "a",
                        "context": "cell:c1",
                        "tool_id": "cell_tool_a",
                    },
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.2",
                        "sign": "+",
                        "evidence": "a",
                        "context": "cell:c1",
                        "tool_id": "cell_tool_b",
                    },
                    {
                        "source": "G2",
                        "target": "G3",
                        "score": "0.7",
                        "sign": "+",
                        "evidence": "a",
                        "context": "cell:c1",
                        "tool_id": "cell_tool_b",
                    },
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.4",
                        "sign": "+",
                        "evidence": "a",
                        "context": "cell:c2",
                        "tool_id": "cell_tool_a",
                    },
                ],
            )
            self._write_run_report(
                run_dir / "run_report.json",
                run_id="run",
                catalog_ids={
                    "cell_tool_a": "lioness",
                    "cell_tool_b": "screni",
                },
            )
            request = base / "comparison-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "cell_compare",
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

        self.assertEqual(report["contexts"], ["cell:c1", "cell:c2"])
        self.assertEqual(
            report["context_counts_by_family"],
            {"global": 0, "group": 0, "cell": 2, "other": 0},
        )
        self.assertTrue(
            any(
                row["source_id"] == "source_a"
                and row["context"] == "cell:c1"
                and row["level"] == "topology"
                and row["distance_metric"] == "weighted_jaccard_distance"
                and row["status"] == "ok"
                for row in distances
            )
        )
        self.assertFalse(
            any(
                row["context"] == "cell:c2"
                for row in distances
            )
        )
        self.assertFalse(any("context_type" in row for row in report["network_index"]))
        self.assertFalse(any("context_family" in row for row in report["network_index"]))

    def test_group_aggregated_context_compares_with_group_level_network(self) -> None:
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
                        "score": "0.8",
                        "sign": "+",
                        "evidence": "andrea_group_agg_mean_effect",
                        "context": "group:sA",
                        "tool_id": "cellrun_01",
                    },
                    {
                        "source": "G2",
                        "target": "G3",
                        "score": "0.4",
                        "sign": "-",
                        "evidence": "andrea_group_agg_mean_effect",
                        "context": "group:sA",
                        "tool_id": "cellrun_01",
                    },
                    {
                        "source": "G1",
                        "target": "G2",
                        "score": "0.5",
                        "sign": "+",
                        "evidence": "native_group_network",
                        "context": "group:sA",
                        "tool_id": "scmtni_01",
                    },
                    {
                        "source": "G1",
                        "target": "G3",
                        "score": "0.2",
                        "sign": "+",
                        "evidence": "native_group_network",
                        "context": "group:sA",
                        "tool_id": "scmtni_01",
                    },
                ],
            )
            self._write_run_report(
                run_dir / "run_report.json",
                run_id="group_compare",
                catalog_ids={
                    "cellrun_01": "lioness",
                    "scmtni_01": "scmtni",
                },
            )
            evaluation_report = base / "evaluation_report.json"
            evaluation_report.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "metrics": [
                            {
                                "tool_id": "cellrun_01",
                                "context": "group:sA",
                                "level": "topology",
                                "status": "ok",
                                "n_truth_edges": 2,
                                "aupr": 0.8,
                            },
                            {
                                "tool_id": "scmtni_01",
                                "context": "group:sA",
                                "level": "topology",
                                "status": "ok",
                                "n_truth_edges": 2,
                                "aupr": 0.7,
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
                        "id": "group_aggregated_compare",
                        "sources": [
                            {
                                "source_id": "source_a",
                                "run_report": "run/run_report.json",
                                "evaluation_report": "evaluation_report.json",
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

        self.assertEqual(report["contexts"], ["group:sA"])
        self.assertEqual(
            report["context_counts_by_family"],
            {"global": 0, "group": 1, "cell": 0, "other": 0},
        )
        self.assertIn("rank_overlap_distance_at_truth_count", report["distances_available"])
        self.assertTrue(
            any(
                row["context"] == "group:sA"
                and row["level"] == "topology"
                and row["distance_metric"] == "weighted_jaccard_distance"
                and row["status"] == "ok"
                for row in distances
            )
        )
        self.assertTrue(
            any(
                row["context"] == "group:sA"
                and row["level"] == "topology"
                and row["distance_metric"] == "rank_overlap_distance_at_truth_count"
                and row["status"] == "ok"
                for row in distances
            )
        )

    def test_other_context_is_indexed_without_special_public_fields(self) -> None:
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
                        "evidence": "association",
                        "context": "condition:stim",
                        "tool_id": "tool_a",
                    }
                ],
            )
            self._write_run_report(
                run_dir / "run_report.json",
                run_id="other_context",
                catalog_ids={"tool_a": "genie3"},
            )
            request = base / "comparison-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "other_context_compare",
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
            network_index = self._read_csv(
                output_root / report["outputs"]["network_index_csv"]
            )
            edge_scores = self._read_csv(
                output_root / report["outputs"]["edge_scores_csv"]
            )

        self.assertEqual(report["contexts"], ["condition:stim"])
        self.assertEqual(
            report["context_counts_by_family"],
            {"global": 0, "group": 0, "cell": 0, "other": 1},
        )
        self.assertTrue(all(row["context"] == "condition:stim" for row in network_index))
        self.assertTrue(all(row["context"] == "condition:stim" for row in edge_scores))
        self.assertFalse(any("context_type" in row for row in network_index))
        self.assertFalse(any("context_family" in row for row in network_index))

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
                "comparison_sqlite",
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

    def test_rank_overlap_uses_truth_count_from_not_applicable_metrics(self) -> None:
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
                        "source": "G2",
                        "target": "G3",
                        "score": "0.8",
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
            self._write_run_report(
                run_dir / "run_report.json",
                run_id="run",
                catalog_ids={"tool_a": "lioness", "tool_b": "lioness"},
            )
            evaluation = base / "evaluation_report.json"
            evaluation.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "metrics": [
                            {
                                "tool_id": "tool_a",
                                "context": "global",
                                "level": "directed",
                                "status": "not_applicable",
                                "n_truth_edges": 2,
                            },
                            {
                                "tool_id": "tool_b",
                                "context": "global",
                                "level": "directed",
                                "status": "not_applicable",
                                "n_truth_edges": 2,
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
                        "id": "not_applicable_truth_count",
                        "sources": [
                            {
                                "source_id": "source_a",
                                "run_report": "run/run_report.json",
                                "evaluation_report": "evaluation_report.json",
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

        self.assertIn(
            "rank_overlap_distance_at_truth_count",
            report["distances_available"],
        )
        rank = next(
            row
            for row in distances
            if row["level"] == "directed"
            and row["distance_metric"] == "rank_overlap_distance_at_truth_count"
        )
        self.assertEqual(rank["status"], "ok")
        self.assertEqual(rank["n_edges_considered"], "2")

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

        coordinates, warnings = build_distance_coordinates(distances)

        self.assertEqual(len(coordinates), 3)
        self.assertTrue(all(row["status"] == "not_available" for row in coordinates))
        self.assertTrue(any("incomplete distance matrix" in item for item in warnings))

    def test_distance_view_returns_aggregate_and_selected_context_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = Path(tmp) / "comparison.sqlite"
            contexts = ["group:g1", "group:g2", "group:g3", "group:g4"]
            tools = ["tool_a", "tool_b", "tool_c"]
            network_index = [
                {
                    "network_id": f"{tool}_{context.replace(':', '_')}_topology",
                    "source_id": "source_a",
                    "run_id": "run",
                    "tool_id": tool,
                    "catalog_tool_id": tool,
                    "context": context,
                    "level": "topology",
                    "n_genes": "3",
                    "n_edges": "2",
                }
                for context in contexts
                for tool in tools
            ]
            distances = []
            values_by_context = {
                "group:g1": {"tool_a|tool_b": 0.2, "tool_a|tool_c": 0.4, "tool_b|tool_c": 0.6},
                "group:g2": {"tool_a|tool_b": 0.4, "tool_a|tool_c": 0.6, "tool_b|tool_c": 0.8},
                "group:g3": {"tool_a|tool_b": 0.6, "tool_a|tool_c": 0.8, "tool_b|tool_c": 0.9},
                "group:g4": {"tool_a|tool_c": 0.7, "tool_b|tool_c": 0.5},
            }
            for context, pairs in values_by_context.items():
                for pair, value in pairs.items():
                    left, right = pair.split("|")
                    distances.append(
                        {
                            "source_id": "source_a",
                            "context": context,
                            "level": "topology",
                            "distance_metric": "weighted_jaccard_distance",
                            "network_a": f"{left}_{context.replace(':', '_')}_topology",
                            "network_b": f"{right}_{context.replace(':', '_')}_topology",
                            "distance": str(value),
                            "n_common_genes": "3",
                            "n_edges_considered": "3",
                            "status": "ok",
                            "warning": "",
                        }
                    )
            distance_coordinates = []
            base_points = {
                "tool_a": (0.0, 0.0),
                "tool_b": (1.0, 0.0),
                "tool_c": (0.0, 1.0),
            }
            for idx, context in enumerate(contexts):
                for tool, (x, y) in base_points.items():
                    distance_coordinates.append(
                        {
                            "source_id": "source_a",
                            "context": context,
                            "level": "topology",
                            "distance_metric": "weighted_jaccard_distance",
                            "network_id": f"{tool}_{context.replace(':', '_')}_topology",
                            "x": str((x * (idx + 1)) + idx),
                            "y": str((y * (idx + 1)) - idx),
                            "status": "ok",
                            "warning": "",
                        }
                    )
            evaluation_metrics = [
                {
                    "source_id": "source_a",
                    "tool_id": tool,
                    "context": context,
                    "level": "topology",
                    "status": "ok",
                    "n_truth_edges": "2",
                    "auroc": "",
                    "aupr": str(0.5 + (0.1 * tool_idx) + (0.01 * context_idx)),
                    "f1_at_truth_count": "",
                    "epr_at_truth_count": "",
                }
                for context_idx, context in enumerate(contexts)
                for tool_idx, tool in enumerate(tools)
            ]
            write_comparison_store(
                sqlite_path,
                network_index=network_index,
                edge_scores=[],
                distances=distances,
                distance_coordinates=distance_coordinates,
                evaluation_metrics=evaluation_metrics,
            )

            payload = distance_view(
                sqlite_path,
                source_id="source_a",
                context_family="group",
                distance_metric="weighted_jaccard_distance",
                evaluation_metric="aupr",
                contexts=["group:g1", "group:g2"],
            )
            cached_payload = distance_view(
                sqlite_path,
                source_id="source_a",
                context_family="group",
                distance_metric="weighted_jaccard_distance",
                evaluation_metric="aupr",
                contexts=["group:g1", "group:g2"],
            )

        self.assertEqual(payload["context_family"], "group")
        self.assertEqual(payload["query_profile"]["cache_hit"], False)
        self.assertGreaterEqual(payload["query_profile"]["elapsed_s"], 0.0)
        self.assertEqual(payload["evaluation_metric"], "aupr")
        self.assertEqual(payload["selected_contexts"], ["group:g1", "group:g2"])
        topology = next(item for item in payload["levels"] if item["level"] == "topology")
        self.assertEqual([tool["tool_id"] for tool in topology["tools"]], tools)
        aggregate_ab = next(
            row
            for row in topology["aggregate"]["distances"]
            if {row["tool_a"], row["tool_b"]} == {"tool_a", "tool_b"}
        )
        self.assertEqual(aggregate_ab["median"], "0.4")
        self.assertEqual(aggregate_ab["q1"], "0.3")
        self.assertEqual(aggregate_ab["q3"], "0.5")
        self.assertEqual(aggregate_ab["min"], "0.2")
        self.assertEqual(aggregate_ab["max"], "0.6")
        self.assertEqual(aggregate_ab["n"], 3)
        self.assertEqual(aggregate_ab["unavailable"], 1)
        self.assertEqual(len(topology["aggregate"]["coordinates"]), 3)
        self.assertEqual(len(topology["aggregate"]["ellipses"]), 3)
        tool_a_metric = next(
            row for row in topology["evaluation"]["aggregate"] if row["tool_id"] == "tool_a"
        )
        self.assertEqual(tool_a_metric["median"], "0.515")
        self.assertEqual(tool_a_metric["n"], 4)
        selected_tool_a_metric = next(
            row for row in topology["evaluation"]["selected"] if row["tool_id"] == "tool_a"
        )
        self.assertEqual(selected_tool_a_metric["median"], "0.505")
        self.assertEqual(selected_tool_a_metric["n"], 2)
        selected_ab = next(
            row
            for row in topology["selected"]["distances"]
            if {row["tool_a"], row["tool_b"]} == {"tool_a", "tool_b"}
        )
        self.assertEqual(selected_ab["median"], "0.3")
        self.assertEqual(selected_ab["n"], 2)
        self.assertEqual(topology["selected"]["contexts"], ["group:g1", "group:g2"])
        self.assertEqual(len(topology["selected"]["distances"]), 3)
        self.assertEqual(len(topology["selected"]["coordinates"]), 6)
        self.assertEqual(
            {row["context"] for row in topology["selected"]["coordinates"]},
            {"group:g1", "group:g2"},
        )
        self.assertTrue(
            all("metric_value" in row for row in topology["selected"]["coordinates"])
        )
        self.assertEqual(
            {row["selection_index"] for row in topology["selected"]["coordinates"]},
            {0, 1},
        )
        self.assertEqual(topology["diagnostics"]["context_embeddings"], 4)
        self.assertEqual(topology["diagnostics"]["anchor_lambda"], "0.15")
        directed = next(item for item in payload["levels"] if item["level"] == "directed")
        self.assertEqual(directed["tools"], [])
        self.assertIsNotNone(directed["selected"])

        self.assertEqual(cached_payload["query_profile"]["cache_hit"], True)
        self.assertEqual(cached_payload["query_profile"]["elapsed_s"], 0.0)

    def test_distance_view_does_not_fabricate_context_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = Path(tmp) / "comparison.sqlite"
            network_index = [
                {
                    "network_id": f"{tool}_{context.replace(':', '_')}_topology",
                    "source_id": "source_a",
                    "run_id": "run",
                    "tool_id": tool,
                    "catalog_tool_id": tool,
                    "context": context,
                    "level": "topology",
                    "n_genes": "3",
                    "n_edges": "2",
                }
                for context, tools in {
                    "group:g1": ["tool_a", "tool_b", "tool_c"],
                    "group:g2": ["tool_a", "tool_b"],
                }.items()
                for tool in tools
            ]
            distances = [
                {
                    "source_id": "source_a",
                    "context": "group:g1",
                    "level": "topology",
                    "distance_metric": "weighted_jaccard_distance",
                    "network_a": "tool_a_group_g1_topology",
                    "network_b": "tool_b_group_g1_topology",
                    "distance": "0.2",
                    "n_common_genes": "3",
                    "n_edges_considered": "3",
                    "status": "ok",
                    "warning": "",
                },
                {
                    "source_id": "source_a",
                    "context": "group:g1",
                    "level": "topology",
                    "distance_metric": "weighted_jaccard_distance",
                    "network_a": "tool_a_group_g1_topology",
                    "network_b": "tool_c_group_g1_topology",
                    "distance": "0.4",
                    "n_common_genes": "3",
                    "n_edges_considered": "3",
                    "status": "ok",
                    "warning": "",
                },
                {
                    "source_id": "source_a",
                    "context": "group:g1",
                    "level": "topology",
                    "distance_metric": "weighted_jaccard_distance",
                    "network_a": "tool_b_group_g1_topology",
                    "network_b": "tool_c_group_g1_topology",
                    "distance": "0.6",
                    "n_common_genes": "3",
                    "n_edges_considered": "3",
                    "status": "ok",
                    "warning": "",
                },
                {
                    "source_id": "source_a",
                    "context": "group:g2",
                    "level": "topology",
                    "distance_metric": "weighted_jaccard_distance",
                    "network_a": "tool_a_group_g2_topology",
                    "network_b": "tool_b_group_g2_topology",
                    "distance": "0.8",
                    "n_common_genes": "3",
                    "n_edges_considered": "3",
                    "status": "ok",
                    "warning": "",
                },
            ]
            write_comparison_store(
                sqlite_path,
                network_index=network_index,
                edge_scores=[],
                distances=distances,
                distance_coordinates=[],
                evaluation_metrics=[],
            )

            payload = distance_view(
                sqlite_path,
                source_id="source_a",
                context_family="group",
                distance_metric="weighted_jaccard_distance",
                contexts=["group:g1", "group:g2"],
            )

        topology = next(item for item in payload["levels"] if item["level"] == "topology")
        aggregate_tool_c = next(
            row
            for row in topology["aggregate"]["coordinates"]
            if row["tool_id"] == "tool_c"
        )
        selected_tool_c = [
            row
            for row in topology["selected"]["coordinates"]
            if row["tool_id"] == "tool_c"
        ]
        self.assertEqual(len(selected_tool_c), 1)
        self.assertEqual(selected_tool_c[0]["context"], "group:g1")
        self.assertEqual(selected_tool_c[0]["x"], aggregate_tool_c["x"])
        self.assertEqual(selected_tool_c[0]["y"], aggregate_tool_c["y"])
        self.assertNotIn(
            "tool_c",
            {
                row["tool_id"]
                for row in topology["selected"]["coordinates"]
                if row["context"] == "group:g2"
            },
        )
        self.assertNotIn(
            "tool_c",
            {row["tool_id"] for row in topology["aggregate"]["ellipses"]},
        )

    def test_distance_view_rejects_more_than_five_selected_contexts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = Path(tmp) / "comparison.sqlite"
            contexts = [f"group:g{idx}" for idx in range(6)]
            write_comparison_store(
                sqlite_path,
                network_index=[
                    {
                        "network_id": f"tool_a_group_g{idx}_topology",
                        "source_id": "source_a",
                        "run_id": "run",
                        "tool_id": "tool_a",
                        "catalog_tool_id": "tool_a",
                        "context": context,
                        "level": "topology",
                        "n_genes": "3",
                        "n_edges": "1",
                    }
                    for idx, context in enumerate(contexts)
                ],
                edge_scores=[],
                distances=[],
                distance_coordinates=[],
                evaluation_metrics=[],
            )

            with self.assertRaisesRegex(ValueError, "At most 5"):
                distance_view(
                    sqlite_path,
                    source_id="source_a",
                    context_family="group",
                    distance_metric="weighted_jaccard_distance",
                    contexts=contexts,
                )

    def test_edge_variability_attaches_usable_evaluation_metric_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = Path(tmp) / "comparison.sqlite"
            network_index = [
                {
                    "network_id": "net_a",
                    "source_id": "source_a",
                    "run_id": "run_a",
                    "tool_id": "tool_a",
                    "catalog_tool_id": "tool_a",
                    "context": "group:g1",
                    "level": "topology",
                    "n_genes": "2",
                    "n_edges": "1",
                },
                {
                    "network_id": "net_b",
                    "source_id": "source_a",
                    "run_id": "run_b",
                    "tool_id": "tool_b",
                    "catalog_tool_id": "tool_b",
                    "context": "group:g1",
                    "level": "topology",
                    "n_genes": "2",
                    "n_edges": "1",
                },
            ]
            edge_scores = [
                {
                    "network_id": "net_a",
                    "edge_key": "g1|g2",
                    "source": "g1",
                    "target": "g2",
                    "sign": "+",
                    "score": "0.8",
                },
                {
                    "network_id": "net_b",
                    "edge_key": "g1|g2",
                    "source": "g1",
                    "target": "g2",
                    "sign": "+",
                    "score": "0.2",
                },
            ]
            evaluation_metrics = [
                {
                    "source_id": "source_a",
                    "tool_id": "tool_a",
                    "context": "group:g1",
                    "level": "topology",
                    "status": "ok",
                    "n_truth_edges": "1",
                    "auroc": "0.81",
                    "aupr": "",
                    "f1_at_truth_count": "",
                    "epr_at_truth_count": "",
                },
                {
                    "source_id": "source_a",
                    "tool_id": "tool_b",
                    "context": "group:g1",
                    "level": "topology",
                    "status": "failed",
                    "n_truth_edges": "1",
                    "auroc": "0.92",
                    "aupr": "",
                    "f1_at_truth_count": "",
                    "epr_at_truth_count": "",
                },
            ]
            write_comparison_store(
                sqlite_path,
                network_index=network_index,
                edge_scores=edge_scores,
                distances=[],
                distance_coordinates=[],
                evaluation_metrics=evaluation_metrics,
            )

            payload = edge_variability(
                sqlite_path,
                selected_networks=[
                    {"source_id": "source_a", "tool_id": "tool_a", "context": "group:g1"},
                    {"source_id": "source_a", "tool_id": "tool_b", "context": "group:g1"},
                ],
                limit=100,
                evaluation_metric="auroc",
            )

            topology = next(item for item in payload["levels"] if item["level"] == "topology")
            self.assertEqual(topology["status"], "ok")
            metrics = {row["tool_id"]: row["metric_value"] for row in topology["networks"]}
            self.assertEqual(metrics["tool_a"], "0.81")
            self.assertIsNone(metrics["tool_b"])

    def test_anchored_mds_and_ellipse_helpers(self) -> None:
        reference = {
            "a": (0.0, 0.0),
            "b": (1.0, 0.0),
            "c": (0.0, 1.0),
        }
        pair_distances = {
            ("a", "b"): 1.0,
            ("a", "c"): 1.0,
            ("b", "c"): 1.41421356237,
        }

        coordinates, stress, displacement = _anchored_mds_coordinates(
            tool_ids=["a", "b", "c"],
            pair_distances=pair_distances,
            reference=reference,
            lambda_anchor=0.15,
        )
        trimmed = _trim_displacements(
            [(0.0, 0.0), (0.2, 0.0), (0.0, 0.2), (0.2, 0.2), (100.0, 100.0)],
            keep_ratio=0.8,
        )
        rx, ry, angle = _covariance_ellipse_axes(trimmed)

        self.assertEqual(set(coordinates), {"a", "b", "c"})
        self.assertLess(stress, 1e-6)
        self.assertLess(displacement, 1e-6)
        self.assertNotIn((100.0, 100.0), trimmed)
        self.assertGreater(rx, 0)
        self.assertGreaterEqual(ry, 0)
        self.assertTrue(-180 <= angle <= 180)

    def test_shared_view_keeps_edge_differences_out_of_static_report(self) -> None:
        view_script = (
            Path("andrea/core/commands/compare_networks/view_assets/view.js")
        ).read_text(encoding="utf-8")

        self.assertIn("comparison.sqlite", view_script)
        self.assertIn("edge_scores.csv", view_script)
        self.assertIn("Static Report", view_script)
        self.assertNotIn("renderDistanceMaps", view_script)
        self.assertNotIn("renderEdgeDifferenceView", view_script)
        self.assertNotIn("function edgeDifferenceKey", view_script)
        self.assertNotIn("weightedJaccardForMaps", view_script)
        self.assertNotIn("rankOverlapForMaps", view_script)

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
