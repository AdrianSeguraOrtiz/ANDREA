"""Source loading and input freezing for network comparison."""

from __future__ import annotations

import csv
import math
import shutil
from pathlib import Path
from typing import Any, Optional

from andrea.core.commands.compare_networks.models import (
    MERGED_NETWORK_REQUIRED_COLUMNS,
    ComparisonSource,
    NetworkRow,
    SourceData,
)
from andrea.core.commands.compare_networks.utils import slugify
from andrea.core.shared.json_io import load_json_object, write_json
from andrea.core.shared.network_context import (
    normalize_network_context,
    normalize_network_sign,
)


def load_source_data(source: ComparisonSource) -> SourceData:
    run_report = load_json_object(
        source.run_report_path, f"Run report {source.source_id}"
    )
    normalized_network_path = resolve_normalized_network_path(
        run_report_path=source.run_report_path,
        run_report=run_report,
        source_id=source.source_id,
    )
    rows = load_normalized_network_rows(
        path=normalized_network_path,
        source_id=source.source_id,
    )
    evaluation_report: Optional[dict[str, Any]] = None
    if source.evaluation_report_path is not None:
        evaluation_report = load_json_object(
            source.evaluation_report_path,
            f"Evaluation report {source.source_id}",
        )
        if not isinstance(evaluation_report.get("metrics"), list):
            raise ValueError(
                f"[{source.source_id}] evaluation_report must contain a metrics array"
            )
    return SourceData(
        source=source,
        run_report=run_report,
        evaluation_report=evaluation_report,
        normalized_network_path=normalized_network_path,
        rows=rows,
    )


def freeze_comparison_inputs(
    *,
    request: dict[str, Any],
    source_data: list[SourceData],
    comparison_dir: Path,
) -> tuple[dict[str, Any], list[SourceData]]:
    frozen_sources: list[dict[str, Any]] = []
    frozen_data: list[SourceData] = []
    used_source_dirs: set[str] = set()
    input_dir = comparison_dir / "input" / "sources"

    for idx, data in enumerate(source_data, start=1):
        source_dirname = unique_source_dirname(
            source_id=data.source.source_id,
            idx=idx,
            used=used_source_dirs,
        )
        source_dir = input_dir / source_dirname
        source_dir.mkdir(parents=True, exist_ok=True)
        frozen_network_path = source_dir / "merged_network_normalized.csv"
        copy_file_unless_same(data.normalized_network_path, frozen_network_path)

        frozen_run_report = dict(data.run_report)
        outputs = frozen_run_report.setdefault("outputs", {})
        if not isinstance(outputs, dict):
            outputs = {}
            frozen_run_report["outputs"] = outputs
        outputs["merged_network_normalized"] = frozen_network_path.name
        frozen_run_report_path = source_dir / "run_report.json"
        write_json(frozen_run_report_path, frozen_run_report)

        frozen_eval_path: Optional[Path] = None
        if data.source.evaluation_report_path is not None:
            frozen_eval_path = source_dir / "evaluation_report.json"
            copy_file_unless_same(data.source.evaluation_report_path, frozen_eval_path)

        run_report_request_path = frozen_run_report_path.relative_to(
            comparison_dir
        ).as_posix()
        evaluation_request_path = (
            frozen_eval_path.relative_to(comparison_dir).as_posix()
            if frozen_eval_path is not None
            else None
        )
        frozen_source = ComparisonSource(
            source_id=data.source.source_id,
            label=data.source.label,
            run_report_path=frozen_run_report_path,
            evaluation_report_path=frozen_eval_path,
            request_run_report=run_report_request_path,
            request_evaluation_report=evaluation_request_path,
        )
        frozen_data.append(
            SourceData(
                source=frozen_source,
                run_report=frozen_run_report,
                evaluation_report=data.evaluation_report,
                normalized_network_path=frozen_network_path,
                rows=data.rows,
            )
        )
        source_entry: dict[str, Any] = {
            "source_id": data.source.source_id,
            "label": data.source.label,
            "run_report": run_report_request_path,
        }
        if evaluation_request_path is not None:
            source_entry["evaluation_report"] = evaluation_request_path
        frozen_sources.append(source_entry)

    frozen_request = dict(request)
    frozen_request["sources"] = frozen_sources
    return frozen_request, frozen_data


def unique_source_dirname(*, source_id: str, idx: int, used: set[str]) -> str:
    base = slugify(source_id)
    candidate = base
    if candidate in used:
        candidate = f"{idx:02d}_{base}"
    suffix = 2
    original = candidate
    while candidate in used:
        candidate = f"{original}_{suffix:02d}"
        suffix += 1
    used.add(candidate)
    return candidate


def copy_file_unless_same(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination.resolve():
        return
    shutil.copy2(source, destination)


def resolve_normalized_network_path(
    *,
    run_report_path: Path,
    run_report: dict[str, Any],
    source_id: str,
) -> Path:
    outputs = run_report.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError(f"[{source_id}] run_report outputs must be an object")
    raw_path = outputs.get("merged_network_normalized")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(
            f"[{source_id}] run_report outputs.merged_network_normalized is required"
        )
    path = Path(raw_path)
    if not path.is_absolute():
        path = run_report_path.parent / path
    if not path.exists() or not path.is_file():
        raise ValueError(
            f"[{source_id}] merged_network_normalized.csv not found: {path}"
        )
    return path


def load_normalized_network_rows(*, path: Path, source_id: str) -> list[NetworkRow]:
    rows: list[NetworkRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        missing = [col for col in MERGED_NETWORK_REQUIRED_COLUMNS if col not in headers]
        if missing:
            raise ValueError(
                f"[{source_id}] merged_network_normalized.csv is missing required columns: {missing}"
            )
        for line_no, raw in enumerate(reader, start=2):
            score = parse_normalized_score(
                raw.get("score"),
                source_id=source_id,
                line_no=line_no,
            )
            source = str(raw.get("source", "")).strip()
            target = str(raw.get("target", "")).strip()
            context = normalize_network_context(
                raw.get("context", ""),
                source=f"[{source_id}] merged_network_normalized.csv line {line_no}",
            )
            tool_id = str(raw.get("tool_id", "")).strip()
            if not source or not target or not context or not tool_id:
                raise ValueError(
                    f"[{source_id}] merged_network_normalized.csv line {line_no} has empty source, target, context or tool_id"
                )
            sign = normalize_network_sign(
                raw.get("sign", ""),
                source=f"[{source_id}] merged_network_normalized.csv line {line_no}",
            )
            rows.append(
                NetworkRow(
                    source=source,
                    target=target,
                    score=score,
                    sign=sign,
                    evidence=str(raw.get("evidence", "")),
                    context=context,
                    tool_id=tool_id,
                )
            )
    return rows


def parse_normalized_score(value: Any, *, source_id: str, line_no: int) -> float:
    try:
        score = float(value)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"[{source_id}] invalid normalized score at merged_network_normalized.csv line {line_no}: {value!r}"
        ) from exc
    if not math.isfinite(score):
        raise ValueError(
            f"[{source_id}] non-finite normalized score at merged_network_normalized.csv line {line_no}: {value!r}"
        )
    if score < 0.0 or score > 1.0:
        raise ValueError(
            f"[{source_id}] normalized score at merged_network_normalized.csv line {line_no} is outside [0, 1]: {value!r}"
        )
    return score
