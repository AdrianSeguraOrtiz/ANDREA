from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

CLI_MODULE = importlib.import_module("andrea.cli.app")
app = CLI_MODULE.app


class CompareNetworksCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_compare_networks_command_calls_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            request = base / "comparison-request.json"
            output_dir = base / "comparisons"
            request.write_text("{}", encoding="utf-8")
            fake_report = {
                "summary": {
                    "sources": 2,
                    "network_instances": 6,
                    "edge_score_rows": 12,
                },
                "outputs": {
                    "comparison_dir": "comparison_fake",
                    "comparison_report": "comparison_fake/comparison_report.json",
                    "network_index_csv": "comparison_fake/network_index.csv",
                    "edge_scores_csv": "comparison_fake/edge_scores.csv",
                    "distances_csv": "comparison_fake/distances.csv",
                    "distance_coordinates_csv": "comparison_fake/distance_coordinates.csv",
                    "comparison_view": "comparison_fake/comparison_view.html",
                },
            }
            with patch.object(
                CLI_MODULE,
                "core_compare_networks",
                return_value=fake_report,
            ) as mock_fn:
                result = self.runner.invoke(
                    app,
                    [
                        "compare-networks",
                        "--request",
                        str(request),
                        "--output-dir",
                        str(output_dir),
                    ],
                )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        mock_fn.assert_called_once()
        kwargs = mock_fn.call_args.kwargs
        self.assertEqual(kwargs["request_path"], request)
        self.assertEqual(kwargs["output_dir"], output_dir)
        self.assertIn("network comparison completed", result.output)


if __name__ == "__main__":
    unittest.main()
