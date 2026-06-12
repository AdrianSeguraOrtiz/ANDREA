#!/usr/bin/env python3
"""Summarize ANDREA runtime profiles from existing command reports.

The script is intentionally read-only: point it at report files or output
directories and it extracts public/additive ``runtime_profile`` entries into a
compact JSON or TSV table for before/after comparisons.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

REPORT_NAMES = (
    "run_report.json",
    "evaluation_report.json",
    "comparison_report.json",
    "benchmark-manifest.json",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize runtime_profile timings from ANDREA reports."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Report files or directories to scan recursively.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "tsv"),
        default="tsv",
        help="Output format. Default: tsv.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output file. Defaults to stdout.",
    )
    args = parser.parse_args()

    reports = []
    for path in args.paths:
        reports.extend(_iter_reports(path))
    rows = [_summarize_report(path) for path in sorted(set(reports))]
    if args.format == "json":
        text = json.dumps(rows, indent=2, sort_keys=True) + "\n"
    else:
        text = _format_tsv(rows)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


def _iter_reports(path: Path) -> Iterable[Path]:
    path = path.expanduser()
    if path.is_file():
        yield path.resolve()
        return
    if not path.is_dir():
        return
    for name in REPORT_NAMES:
        yield from (item.resolve() for item in path.rglob(name) if item.is_file())


def _summarize_report(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    timings = payload.get("runtime_profile")
    if not isinstance(timings, list):
        timings = []
    stage_rows = [
        {
            "stage": str(item.get("stage") or ""),
            "label": str(item.get("label") or ""),
            "elapsed_s": _safe_float(item.get("elapsed_s")),
        }
        for item in timings
        if isinstance(item, dict)
    ]
    total_s = sum(row["elapsed_s"] for row in stage_rows)
    slowest = sorted(stage_rows, key=lambda item: item["elapsed_s"], reverse=True)[:5]
    return {
        "command": _command_from_report(path, payload),
        "report": str(path),
        "created_at": str(payload.get("created_at") or ""),
        "id": _report_id(payload),
        "total_s": round(total_s, 6),
        "stage_count": len(stage_rows),
        "slowest_stages": slowest,
        "stages": stage_rows,
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Cannot read JSON report {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Report is not a JSON object: {path}")
    return payload


def _command_from_report(path: Path, payload: dict[str, Any]) -> str:
    name = path.name
    if name == "run_report.json":
        return "infer-network"
    if name == "evaluation_report.json":
        return "evaluate-inference"
    if name == "comparison_report.json":
        return "compare-networks"
    if name == "benchmark-manifest.json":
        return "generate-data"
    if "comparison" in payload:
        return "compare-networks"
    if "metrics" in payload and "pairings" in payload:
        return "evaluate-inference"
    if "datasets" in payload and "simulators" in payload:
        return "generate-data"
    return "unknown"


def _report_id(payload: dict[str, Any]) -> str:
    request = payload.get("request")
    if isinstance(request, dict) and request.get("id"):
        return str(request["id"])
    for key in ("run_id", "benchmark_id", "id"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _safe_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed >= 0.0 else 0.0


def _format_tsv(rows: list[dict[str, Any]]) -> str:
    headers = [
        "command",
        "id",
        "total_s",
        "stage_count",
        "slowest_stage",
        "slowest_stage_s",
        "report",
    ]
    lines = ["\t".join(headers)]
    for row in rows:
        slowest = row["slowest_stages"][0] if row["slowest_stages"] else {}
        values = [
            row["command"],
            row["id"],
            f"{row['total_s']:.6f}",
            str(row["stage_count"]),
            str(slowest.get("stage") or ""),
            f"{float(slowest.get('elapsed_s') or 0.0):.6f}",
            row["report"],
        ]
        lines.append("\t".join(_tsv_cell(value) for value in values))
    return "\n".join(lines) + "\n"


def _tsv_cell(value: Any) -> str:
    return str(value).replace("\t", " ").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
