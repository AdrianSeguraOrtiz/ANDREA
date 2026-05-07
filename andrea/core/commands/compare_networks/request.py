"""Comparison request loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from andrea.core.commands.compare_networks.models import ComparisonSource
from andrea.core.shared.json_io import load_json_object


def load_comparison_request(path: Path) -> dict[str, Any]:
    request = load_json_object(path, "Comparison request")
    schema_version = request.get("schema_version")
    if schema_version != "1.0":
        raise ValueError("Comparison request schema_version must be '1.0'")
    request_id = str(request.get("id", "")).strip()
    if not request_id:
        raise ValueError("Comparison request id is required")
    sources = request.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Comparison request sources must be a non-empty array")
    return request


def parse_sources(
    *,
    request: dict[str, Any],
    request_path: Path,
) -> list[ComparisonSource]:
    base_dir = request_path.parent
    parsed: list[ComparisonSource] = []
    seen_ids: set[str] = set()
    for idx, raw in enumerate(request["sources"], start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Comparison request sources[{idx}] must be an object")
        source_id = str(raw.get("source_id", "")).strip()
        if not source_id:
            raise ValueError(f"Comparison request sources[{idx}].source_id is required")
        if source_id in seen_ids:
            raise ValueError(f"Duplicate comparison source_id: {source_id}")
        seen_ids.add(source_id)

        run_report_raw = raw.get("run_report")
        if not isinstance(run_report_raw, str) or not run_report_raw.strip():
            raise ValueError(
                f"Comparison request sources[{idx}].run_report is required"
            )
        evaluation_report_raw = raw.get("evaluation_report")
        if evaluation_report_raw is not None and (
            not isinstance(evaluation_report_raw, str)
            or not evaluation_report_raw.strip()
        ):
            raise ValueError(
                f"Comparison request sources[{idx}].evaluation_report must be a non-empty string when provided"
            )

        parsed.append(
            ComparisonSource(
                source_id=source_id,
                label=str(raw.get("label") or source_id),
                run_report_path=resolve_request_path(base_dir, run_report_raw),
                evaluation_report_path=(
                    resolve_request_path(base_dir, evaluation_report_raw)
                    if isinstance(evaluation_report_raw, str)
                    else None
                ),
                request_run_report=run_report_raw,
                request_evaluation_report=evaluation_report_raw,
            )
        )
    return parsed


def resolve_request_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path
