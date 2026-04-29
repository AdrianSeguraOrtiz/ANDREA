from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

CLI_MODULE = importlib.import_module("andrea.cli.app")
app = CLI_MODULE.app


class EvaluateInferenceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_evaluate_inference_command_calls_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            truth_manifest = base / "ground-truth-manifest.json"
            run_report = base / "run_report.json"
            output_dir = base / "evaluation"
            truth_manifest.write_text("{}", encoding="utf-8")
            run_report.write_text("{}", encoding="utf-8")
            fake_report = {
                "metrics": [
                    {"status": "ok"},
                    {"status": "not_applicable"},
                ],
                "outputs": {
                    "evaluation_dir": str(output_dir / "evaluation_toy"),
                    "evaluation_report": str(output_dir / "evaluation_report.json"),
                    "metrics_csv": str(output_dir / "metrics.csv"),
                },
            }
            with patch.object(
                CLI_MODULE,
                "core_evaluate_inference",
                return_value=fake_report,
            ) as mock_fn:
                result = self.runner.invoke(
                    app,
                    [
                        "evaluate-inference",
                        "--run-report",
                        str(run_report),
                        "--ground-truth-manifest",
                        str(truth_manifest),
                        "--output-dir",
                        str(output_dir),
                        "--no-plots",
                    ],
                )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        mock_fn.assert_called_once()
        kwargs = mock_fn.call_args.kwargs
        self.assertEqual(kwargs["ground_truth_manifest_path"], truth_manifest)
        self.assertEqual(kwargs["run_report_path"], run_report)
        self.assertEqual(kwargs["output_dir"], output_dir)
        self.assertFalse(kwargs["generate_plots"])
        self.assertIn("inference evaluation completed", result.output)


if __name__ == "__main__":
    unittest.main()
