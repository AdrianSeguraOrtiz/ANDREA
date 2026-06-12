"""Network output merging and normalization helpers."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from andrea.core.shared.network_context import (
    normalize_network_context,
    normalize_network_sign,
)

from .shared import NETWORK_REQUIRED_COLUMNS, ToolExecutionResult


def _format_score(value: float) -> str:
    return format(float(value), ".12g")


def _iter_network_rows(path: Path, tool_id: str) -> Iterable[dict[str, Any]]:
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError(
            f"[{tool_id}] network.csv was not generated or is empty: {path}"
        )

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        missing = [col for col in NETWORK_REQUIRED_COLUMNS if col not in headers]
        if missing:
            raise ValueError(
                f"[{tool_id}] network.csv is missing required columns: {missing}"
            )

        for idx, row in enumerate(reader, start=2):
            try:
                score = float(row["score"])
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    f"[{tool_id}] invalid numeric score at network.csv line {idx}: {row.get('score')!r}"
                ) from exc
            if not math.isfinite(score):
                raise ValueError(
                    f"[{tool_id}] non-finite score at network.csv line {idx}: {row.get('score')!r}"
                )
            if score <= 0.0:
                raise ValueError(
                    f"[{tool_id}] non-positive score at network.csv line {idx}: {row.get('score')!r}; "
                    "network.csv score must be a positive magnitude and sign must be stored in the sign column"
                )

            source = str(row["source"]).strip()
            target = str(row["target"]).strip()
            evidence = str(row["evidence"]).strip()
            context = normalize_network_context(
                row["context"],
                source=f"[{tool_id}] network.csv line {idx}",
            )
            if not source or not target:
                raise ValueError(
                    f"[{tool_id}] network.csv has empty source or target at line {idx}"
                )
            if not evidence:
                raise ValueError(
                    f"[{tool_id}] network.csv has empty evidence at line {idx}"
                )
            sign = normalize_network_sign(
                row["sign"],
                source=f"[{tool_id}] network.csv line {idx}",
            )

            yield {
                "source": source,
                "target": target,
                "score": score,
                "sign": sign,
                "evidence": evidence,
                "context": context,
            }


def _read_network_rows(path: Path, tool_id: str) -> list[dict[str, Any]]:
    return list(_iter_network_rows(path, tool_id=tool_id))


def _write_network_rows(
    *,
    path: Path,
    rows: Iterable[dict[str, Any]],
    include_tool_id: bool,
) -> None:
    fieldnames = list(NETWORK_REQUIRED_COLUMNS)
    if include_tool_id:
        fieldnames.append("tool_id")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            out_row = {
                "source": row["source"],
                "target": row["target"],
                "score": _format_score(float(row["score"])),
                "sign": row["sign"],
                "evidence": row["evidence"],
                "context": row["context"],
            }
            if include_tool_id:
                out_row["tool_id"] = row["tool_id"]
            writer.writerow(out_row)


def _merge_network_outputs(
    *,
    run_dir: Path,
    execution_results: dict[str, ToolExecutionResult],
    warnings: list[str],
    progress_callback: Optional[Callable[[str, int, str], None]] = None,
) -> tuple[
    dict[str, ToolExecutionResult], dict[str, int], Optional[Path], Optional[Path]
]:
    updated = dict(execution_results)
    per_tool_rows: dict[str, int] = {}
    had_completed_network_output = False
    valid_stats_by_tool: dict[str, dict[str, Any]] = {}
    completed_results = [
        (tool_id, updated[tool_id])
        for tool_id in sorted(updated.keys())
        if updated[tool_id].status == "completed" and updated[tool_id].network_path
    ]
    total_completed = max(1, len(completed_results))

    for idx, (tool_id, result) in enumerate(completed_results, start=1):
        had_completed_network_output = True
        merge_percent = min(83, 78 + int(round((idx - 1) / total_completed * 5)))
        _notify_progress(
            progress_callback,
            "merging_raw_networks",
            merge_percent,
            f"Reading network.csv for {tool_id}.",
        )

        network_path = Path(result.network_path)
        try:
            stats = _network_row_stats(network_path, tool_id=tool_id)
        except Exception as exc:  # noqa: BLE001
            updated[tool_id] = ToolExecutionResult(
                tool_id=result.tool_id,
                status="failed",
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                network_path=result.network_path,
                progress_path=result.progress_path,
                logs_path=result.logs_path,
                error=str(exc),
            )
            continue

        if int(stats["rows"]) == 0:
            warnings.append(
                f"[{tool_id}] network output contains no non-zero edges; empty network kept as a valid result."
            )
            per_tool_rows[tool_id] = 0
            continue

        valid_stats_by_tool[tool_id] = {"network_path": network_path, **stats}

    merged_raw_path: Optional[Path] = None
    merged_norm_path: Optional[Path] = None

    if valid_stats_by_tool or had_completed_network_output:
        merged_raw_path = run_dir / "merged_network_raw.csv"
        _notify_progress(
            progress_callback,
            "merging_raw_networks",
            83,
            "Writing merged_network_raw.csv.",
        )
        _write_network_rows(
            path=merged_raw_path,
            rows=_iter_merged_raw_rows(valid_stats_by_tool),
            include_tool_id=True,
        )

    total_valid = max(1, len(valid_stats_by_tool))
    for idx, (tool_id, stats) in enumerate(valid_stats_by_tool.items(), start=1):
        normalize_percent = min(89, 84 + int(round((idx - 1) / total_valid * 5)))
        _notify_progress(
            progress_callback,
            "normalizing_scores",
            normalize_percent,
            f"Normalizing network scores for {tool_id}.",
        )
        network_path = Path(stats["network_path"])
        tool_norm_path = network_path.parent / "network.normalized.csv"
        _write_network_rows(
            path=tool_norm_path,
            rows=_iter_normalized_rows(
                path=network_path,
                tool_id=tool_id,
                min_score=float(stats["min_score"]),
                max_score=float(stats["max_score"]),
                include_tool_id=False,
            ),
            include_tool_id=False,
        )
        per_tool_rows[tool_id] = int(stats["rows"])

    if valid_stats_by_tool or had_completed_network_output:
        merged_norm_path = run_dir / "merged_network_normalized.csv"
        _notify_progress(
            progress_callback,
            "normalizing_scores",
            89,
            "Writing merged_network_normalized.csv.",
        )
        _write_network_rows(
            path=merged_norm_path,
            rows=_iter_merged_normalized_rows(valid_stats_by_tool),
            include_tool_id=True,
        )

    return updated, per_tool_rows, merged_raw_path, merged_norm_path


def _network_row_stats(path: Path, tool_id: str) -> dict[str, Any]:
    count = 0
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    for row in _iter_network_rows(path, tool_id=tool_id):
        score = float(row["score"])
        count += 1
        min_score = score if min_score is None else min(min_score, score)
        max_score = score if max_score is None else max(max_score, score)
    return {
        "rows": count,
        "min_score": min_score if min_score is not None else 0.0,
        "max_score": max_score if max_score is not None else 0.0,
    }


def _iter_merged_raw_rows(
    valid_stats_by_tool: dict[str, dict[str, Any]],
) -> Iterable[dict[str, Any]]:
    for tool_id, stats in valid_stats_by_tool.items():
        for row in _iter_network_rows(Path(stats["network_path"]), tool_id=tool_id):
            out_row = dict(row)
            out_row["tool_id"] = tool_id
            yield out_row


def _iter_normalized_rows(
    *,
    path: Path,
    tool_id: str,
    min_score: float,
    max_score: float,
    include_tool_id: bool,
) -> Iterable[dict[str, Any]]:
    for row in _iter_network_rows(path, tool_id=tool_id):
        out_row = dict(row)
        if max_score > min_score:
            out_row["score"] = (float(row["score"]) - min_score) / (
                max_score - min_score
            )
        else:
            out_row["score"] = 1.0
        if include_tool_id:
            out_row["tool_id"] = tool_id
        yield out_row


def _iter_merged_normalized_rows(
    valid_stats_by_tool: dict[str, dict[str, Any]],
) -> Iterable[dict[str, Any]]:
    for tool_id, stats in valid_stats_by_tool.items():
        yield from _iter_normalized_rows(
            path=Path(stats["network_path"]),
            tool_id=tool_id,
            min_score=float(stats["min_score"]),
            max_score=float(stats["max_score"]),
            include_tool_id=True,
        )


def _notify_progress(
    progress_callback: Optional[Callable[[str, int, str], None]],
    phase: str,
    percent: int,
    message: str,
) -> None:
    if progress_callback is None:
        return
    progress_callback(phase, percent, message)
