from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from andrea.core.commands.infer_network.commons.merge import (
    _merge_network_outputs,
)
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
                output_capabilities={"signed_tool": {"sign": "signed"}},
                allowed_contexts={"signed_tool": {"global"}},
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
                output_capabilities={"signed_tool": {"sign": "signed"}},
                allowed_contexts={"signed_tool": {"global"}},
                warnings=warnings,
            )
            raw_exists = (run_dir / "merged_network_raw.csv").exists()
            normalized_exists = (run_dir / "merged_network_normalized.csv").exists()

        self.assertEqual(per_tool_rows, {})
        self.assertIsNone(merged_raw)
        self.assertIsNone(merged_norm)
        self.assertFalse(raw_exists)
        self.assertFalse(normalized_exists)
        self.assertEqual(updated["signed_tool"].status, "failed")
        self.assertIn("non-positive score", updated["signed_tool"].error or "")
        self.assertEqual(warnings, [])

    def test_empty_network_is_completed_and_produces_header_only_merged_outputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            network_path = (
                run_dir / "tools" / "empty_tool" / "io" / "out" / "network.csv"
            )
            self._write_network(network_path, [])
            warnings: list[str] = []

            updated, per_tool_rows, merged_raw, merged_norm = _merge_network_outputs(
                run_dir=run_dir,
                execution_results={
                    "empty_tool": ToolExecutionResult(
                        tool_id="empty_tool",
                        status="completed",
                        exit_code=0,
                        duration_seconds=1.0,
                        network_path=str(network_path),
                        progress_path=None,
                        logs_path=None,
                        error=None,
                    )
                },
                output_capabilities={"empty_tool": {"sign": "none"}},
                allowed_contexts={"empty_tool": {"global"}},
                warnings=warnings,
            )
            raw_exists = (run_dir / "merged_network_raw.csv").exists()
            normalized_exists = (run_dir / "merged_network_normalized.csv").exists()
            raw_content = (run_dir / "merged_network_raw.csv").read_text(
                encoding="utf-8"
            )
            normalized_content = (
                run_dir / "merged_network_normalized.csv"
            ).read_text(encoding="utf-8")

        self.assertEqual(per_tool_rows, {"empty_tool": 0})
        self.assertEqual(
            merged_raw.name if merged_raw else None,
            "merged_network_raw.csv",
        )
        self.assertEqual(
            merged_norm.name if merged_norm else None,
            "merged_network_normalized.csv",
        )
        self.assertTrue(raw_exists)
        self.assertTrue(normalized_exists)
        self.assertEqual(
            raw_content,
            "source,target,score,sign,evidence,context,tool_id\n",
        )
        self.assertEqual(raw_content, normalized_content)
        self.assertEqual(updated["empty_tool"].status, "completed")
        self.assertIsNone(updated["empty_tool"].error)
        self.assertTrue(any("empty network kept" in warning for warning in warnings))

    def test_preserves_column_contexts_in_raw_and_normalized_outputs(self) -> None:
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
                        "context": "column:C1",
                    },
                    {
                        "source": "A",
                        "target": "C",
                        "score": "2",
                        "sign": "?",
                        "evidence": "association",
                        "context": "column:C2",
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
                output_capabilities={"cell_tool": {"sign": "mixed"}},
                allowed_contexts={"cell_tool": {"column:C1", "column:C2"}},
                warnings=warnings,
            )
            raw_rows = self._read_network(merged_raw)
            norm_rows = self._read_network(merged_norm)

        self.assertEqual(warnings, [])
        self.assertEqual(
            [row["context"] for row in raw_rows], ["column:C1", "column:C2"]
        )
        self.assertEqual(
            [row["context"] for row in norm_rows], ["column:C1", "column:C2"]
        )

    def test_invalid_context_or_sign_marks_tool_output_invalid(self) -> None:
        cases = [
            ("", "+", "has empty context"),
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

                    (
                        updated,
                        _per_tool_rows,
                        _merged_raw,
                        _merged_norm,
                    ) = _merge_network_outputs(
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
                        output_capabilities={"bad_tool": {"sign": "mixed"}},
                        allowed_contexts={"bad_tool": {"global"}},
                        warnings=warnings,
                    )

                self.assertEqual(updated["bad_tool"].status, "failed")
                self.assertIn(expected, updated["bad_tool"].error or "")
                self.assertEqual(warnings, [])

    def test_rejects_rows_that_contradict_frozen_sign_semantics(self) -> None:
        cases = [
            ("none", "+", "capability is sign='none'"),
            ("signed", "?", "capability is sign='signed'"),
        ]
        for declared_sign, row_sign, expected in cases:
            with self.subTest(declared_sign=declared_sign, row_sign=row_sign):
                with tempfile.TemporaryDirectory() as tmp:
                    run_dir = Path(tmp)
                    network_path = (
                        run_dir / "tools" / "tool" / "io" / "out" / "network.csv"
                    )
                    self._write_network(
                        network_path,
                        [
                            {
                                "source": "A",
                                "target": "B",
                                "score": "1",
                                "sign": row_sign,
                                "evidence": "association",
                                "context": "global",
                            }
                        ],
                    )

                    updated, _rows, _raw, _normalized = _merge_network_outputs(
                        run_dir=run_dir,
                        execution_results={
                            "tool": ToolExecutionResult(
                                tool_id="tool",
                                status="completed",
                                exit_code=0,
                                duration_seconds=1.0,
                                network_path=str(network_path),
                                progress_path=None,
                                logs_path=None,
                                error=None,
                            )
                        },
                        output_capabilities={"tool": {"sign": declared_sign}},
                        allowed_contexts={"tool": {"global"}},
                        warnings=[],
                    )

                self.assertEqual(updated["tool"].status, "failed")
                self.assertIn(expected, updated["tool"].error or "")

    def test_rejects_completed_output_without_frozen_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            network_path = run_dir / "tools" / "tool" / "io" / "out" / "network.csv"
            self._write_network(
                network_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                    }
                ],
            )

            updated, _rows, _raw, _normalized = _merge_network_outputs(
                run_dir=run_dir,
                execution_results={
                    "tool": ToolExecutionResult(
                        tool_id="tool",
                        status="completed",
                        exit_code=0,
                        duration_seconds=1.0,
                        network_path=str(network_path),
                        progress_path=None,
                        logs_path=None,
                        error=None,
                    )
                },
                output_capabilities={},
                allowed_contexts={"tool": {"global"}},
                warnings=[],
            )

        self.assertEqual(updated["tool"].status, "failed")
        self.assertIn(
            "required frozen output capability is missing", updated["tool"].error or ""
        )

    def test_requires_non_empty_context_inventory_for_completed_output(self) -> None:
        for allowed_contexts, expected in (
            ({}, "allowed_contexts keys must exactly match execution_results"),
            ({"tool": set()}, "must be a non-empty set of contexts"),
            (
                {"tool": {"global"}, "ghost": {"global"}},
                "allowed_contexts keys must exactly match execution_results",
            ),
        ):
            with self.subTest(allowed_contexts=allowed_contexts):
                with tempfile.TemporaryDirectory() as tmp:
                    run_dir = Path(tmp)
                    network_path = (
                        run_dir / "tools" / "tool" / "io" / "out" / "network.csv"
                    )
                    self._write_network(network_path, [])

                    with self.assertRaisesRegex(ValueError, expected):
                        _merge_network_outputs(
                            run_dir=run_dir,
                            execution_results={
                                "tool": ToolExecutionResult(
                                    tool_id="tool",
                                    status="completed",
                                    exit_code=0,
                                    duration_seconds=1.0,
                                    network_path=str(network_path),
                                    progress_path=None,
                                    logs_path=None,
                                    error=None,
                                )
                            },
                            output_capabilities={"tool": {"sign": "none"}},
                            allowed_contexts=allowed_contexts,
                            warnings=[],
                        )

    def test_rejects_unplanned_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            network_path = run_dir / "tools" / "tool" / "io" / "out" / "network.csv"
            self._write_network(
                network_path,
                [
                    {
                        "source": "A",
                        "target": "B",
                        "score": "1",
                        "sign": "+",
                        "evidence": "association",
                        "context": "column:C1",
                    }
                ],
            )

            updated, _rows, _raw, _normalized = _merge_network_outputs(
                run_dir=run_dir,
                execution_results={
                    "tool": ToolExecutionResult(
                        tool_id="tool",
                        status="completed",
                        exit_code=0,
                        duration_seconds=1.0,
                        network_path=str(network_path),
                        progress_path=None,
                        logs_path=None,
                        error=None,
                    )
                },
                output_capabilities={"tool": {"sign": "mixed"}},
                warnings=[],
                allowed_contexts={"tool": {"global"}},
            )

        self.assertEqual(updated["tool"].status, "failed")
        self.assertIn(
            "contradicts the planned execution mode",
            updated["tool"].error or "",
        )

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
