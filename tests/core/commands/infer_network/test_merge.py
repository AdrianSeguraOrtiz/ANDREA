from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from andrea.core.commands.infer_network.commons.merge import _merge_network_outputs
from andrea.core.commands.infer_network.commons.shared import ToolExecutionResult


class InferNetworkMergeTests(unittest.TestCase):
    def test_normalizes_nonnegative_scores_preserving_sign_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            tool_dir = run_dir / "tools" / "signed_tool" / "io" / "out"
            network_path = tool_dir / "network.csv"
            self._write_network(
                network_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "5",
                        "sign": "-",
                        "evidence": "association",
                        "context": "global",
                    },
                    {
                        "source": "C",
                        "target": "D",
                        "score": "2",
                        "sign": "+",
                        "evidence": "association",
                        "context": "global",
                    },
                    {
                        "source": "E",
                        "target": "F",
                        "score": "3.5",
                        "sign": "-",
                        "evidence": "association",
                        "context": "global",
                    },
                ],
            )
            warnings: list[str] = []

            _, per_tool_rows, merged_raw, merged_norm = _merge_network_outputs(
                run_dir=run_dir,
                execution_results={
                    "signed_tool": ToolExecutionResult(
                        tool_id="signed_tool",
                        status="completed",
                        exit_code=0,
                        duration_seconds=1.0,
                        network_path=str(network_path),
                        progress_path=None,
                        logs_path=None,
                        error=None,
                    )
                },
                warnings=warnings,
            )

            raw_rows = self._read_network(merged_raw)
            norm_rows = self._read_network(merged_norm)
            tool_norm_rows = self._read_network(tool_dir / "network.normalized.csv")

        self.assertEqual(warnings, [])
        self.assertEqual(per_tool_rows, {"signed_tool": 3})
        self.assertEqual([row["score"] for row in raw_rows], ["5", "2", "3.5"])
        self.assertEqual([row["sign"] for row in raw_rows], ["-", "+", "-"])
        self.assertEqual([row["score"] for row in norm_rows], ["1", "0", "0.5"])
        self.assertEqual([row["score"] for row in tool_norm_rows], ["1", "0", "0.5"])
        self.assertEqual([row["sign"] for row in norm_rows], ["-", "+", "-"])

    def test_non_positive_scores_mark_tool_output_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            tool_dir = run_dir / "tools" / "signed_tool" / "io" / "out"
            network_path = tool_dir / "network.csv"
            self._write_network(
                network_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "-5",
                        "sign": "-",
                        "evidence": "association",
                        "context": "global",
                    }
                ],
            )
            warnings: list[str] = []

            updated, per_tool_rows, merged_raw, merged_norm = _merge_network_outputs(
                run_dir=run_dir,
                execution_results={
                    "signed_tool": ToolExecutionResult(
                        tool_id="signed_tool",
                        status="completed",
                        exit_code=0,
                        duration_seconds=1.0,
                        network_path=str(network_path),
                        progress_path=None,
                        logs_path=None,
                        error=None,
                    )
                },
                warnings=warnings,
            )
            raw_rows = self._read_network(merged_raw)
            norm_rows = self._read_network(merged_norm)

        self.assertEqual(per_tool_rows, {})
        self.assertIsNotNone(merged_raw)
        self.assertIsNotNone(merged_norm)
        self.assertEqual(raw_rows, [])
        self.assertEqual(norm_rows, [])
        self.assertEqual(updated["signed_tool"].status, "failed")
        self.assertIn("non-positive score", updated["signed_tool"].error or "")
        self.assertTrue(any("non-positive score" in warning for warning in warnings))

    def test_preserves_cell_contexts_in_raw_and_normalized_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            tool_dir = run_dir / "tools" / "cell_tool" / "io" / "out"
            network_path = tool_dir / "network.csv"
            self._write_network(
                network_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "4",
                        "sign": "+",
                        "evidence": "association",
                        "context": "cell:C1",
                    },
                    {
                        "source": "A",
                        "target": "C",
                        "score": "2",
                        "sign": "?",
                        "evidence": "association",
                        "context": "cell:C2",
                    },
                ],
            )
            warnings: list[str] = []

            _updated, _per_tool_rows, merged_raw, merged_norm = _merge_network_outputs(
                run_dir=run_dir,
                execution_results={
                    "cell_tool": ToolExecutionResult(
                        tool_id="cell_tool",
                        status="completed",
                        exit_code=0,
                        duration_seconds=1.0,
                        network_path=str(network_path),
                        progress_path=None,
                        logs_path=None,
                        error=None,
                    )
                },
                warnings=warnings,
            )
            raw_rows = self._read_network(merged_raw)
            norm_rows = self._read_network(merged_norm)

        self.assertEqual(warnings, [])
        self.assertEqual([row["context"] for row in raw_rows], ["cell:C1", "cell:C2"])
        self.assertEqual([row["context"] for row in norm_rows], ["cell:C1", "cell:C2"])

    def test_invalid_context_or_sign_marks_tool_output_invalid(self) -> None:
        cases = [
            ("", "+", "empty context"),
            ("global", "activation", "invalid sign"),
        ]
        for context, sign, expected in cases:
            with self.subTest(expected=expected):
                with tempfile.TemporaryDirectory() as tmp:
                    run_dir = Path(tmp)
                    tool_dir = run_dir / "tools" / "bad_tool" / "io" / "out"
                    network_path = tool_dir / "network.csv"
                    self._write_network(
                        network_path,
                        [
                            {
                                "source": "A",
                                "target": "B",
                                "score": "1",
                                "sign": sign,
                                "evidence": "association",
                                "context": context,
                            }
                        ],
                    )
                    warnings: list[str] = []

                    updated, _per_tool_rows, _merged_raw, _merged_norm = (
                        _merge_network_outputs(
                            run_dir=run_dir,
                            execution_results={
                                "bad_tool": ToolExecutionResult(
                                    tool_id="bad_tool",
                                    status="completed",
                                    exit_code=0,
                                    duration_seconds=1.0,
                                    network_path=str(network_path),
                                    progress_path=None,
                                    logs_path=None,
                                    error=None,
                                )
                            },
                            warnings=warnings,
                        )
                    )

                self.assertEqual(updated["bad_tool"].status, "failed")
                self.assertIn(expected, updated["bad_tool"].error or "")
                self.assertTrue(any(expected in warning for warning in warnings))

    def _write_network(self, path: Path, rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["source", "target", "score", "sign", "evidence", "context"],
            )
            writer.writeheader()
            writer.writerows(rows)

    def _read_network(self, path: Path | None) -> list[dict[str, str]]:
        assert path is not None
        with path.open("r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))


if __name__ == "__main__":
    unittest.main()
