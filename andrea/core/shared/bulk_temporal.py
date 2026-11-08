"""Validate temporal inputs only for a requested inference execution."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from andrea.core.shared.column_metadata import (
    load_column_descriptors,
    summarize_temporal_metadata,
)
from andrea.core.shared.input_specs import load_input_specs
from andrea.core.shared.input_validation import validate_tsv_file_with_spec


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _expression_axes(path: Path) -> tuple[list[str], set[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, [])
        _require(
            len(header) >= 2 and all(header),
            "expression requires exact nonempty gene and sample headings",
        )
        _require(len(set(header)) == len(header), "expression has duplicate header")
        genes = set()
        for number, row in enumerate(reader, 2):
            if not row:
                continue
            _require(len(row) == len(header), f"expression: malformed row {number}")
            _require(
                bool(row[0]) and row[0] == row[0].strip() and row[0] not in genes,
                "expression genes must be unique exact nonempty identifiers",
            )
            genes.add(row[0])
    _require(bool(genes), "expression requires genes")
    return header[1:], genes


def _validate_schema_property(value: Any, filename: str, key: str) -> Any:
    from jsonschema import Draft202012Validator

    schema_path = (
        Path(__file__).resolve().parents[2]
        / "catalog_inference_tools"
        / "schemas"
        / filename
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))["properties"][key]
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: str(error.path),
    )
    if errors:
        raise ValueError(key + ": " + "; ".join(error.message for error in errors))
    return value


def validate_temporal_requirements(requirements: Any) -> dict[str, Any]:
    """Validate the declarative requirements shared by catalog and custom tools."""
    return _validate_schema_property(
        requirements, "toolspec.schema.json", "temporal_requirements"
    )


def validate_bulk_temporal_inputs(
    *,
    expression_path: Path,
    column_descriptors_path: Path | None,
) -> dict[str, Any]:
    """Validate TS and SS columns without inventing times or averaging replicas."""
    _require(
        column_descriptors_path is not None,
        "temporal requires extras.column_descriptors",
    )
    columns, genes = _expression_axes(expression_path)
    specs = load_input_specs()
    for key, path in (
        ("expression_matrix", expression_path),
        ("column_descriptors", column_descriptors_path),
    ):
        spec = specs[key]
        result = validate_tsv_file_with_spec(
            key=key,
            path=path,
            spec=spec,
            expression_genes=genes,
            expression_columns=set(columns),
            expression_columns_count=len(columns),
            unknown_cross_check="error",
        )
        _require(not result.errors, "\n".join(result.errors))
    metadata = load_column_descriptors(column_descriptors_path, columns)
    temporal = summarize_temporal_metadata(metadata)
    by_series = defaultdict(list)
    role_columns = {role: [] for role in ("time_series", "steady_state")}
    for column, row in metadata.items():
        role_columns[row["observation_type"]].append(column)
        if row["observation_type"] == "time_series":
            _require(
                row["time_unit"] == temporal["time_unit"],
                "mixed or inconsistent time_unit",
            )
            by_series[row["series_id"]].append(row)
    summaries, transitions = [], []
    for series_id, rows in sorted(by_series.items()):
        conditions = {row["condition"] for row in rows}
        _require(
            len(conditions) == 1, f"series {series_id}: condition must be constant"
        )
        coordinates = sorted({row["timepoint"] for row in rows})
        _require(
            len(coordinates) >= 2,
            f"series {series_id}: requires at least two distinct timepoints",
        )
        by_time = {
            time: {row["observation_id"] for row in rows if row["timepoint"] == time}
            for time in coordinates
        }
        deltas = [right - left for left, right in zip(coordinates, coordinates[1:])]
        _require(
            all(math.isfinite(delta) and delta > 0 for delta in deltas),
            f"series {series_id}: intervals must be finite and positive",
        )
        regular = all(
            math.isclose(delta, deltas[0], rel_tol=1e-9, abs_tol=1e-12)
            for delta in deltas
        )
        if temporal["sampling_design"] == "longitudinal":
            _require(
                len({row["replicate"] for row in rows}) == 1,
                f"series {series_id}: longitudinal series must identify one biological replicate",
            )
            _require(
                all(len(obs) == 1 for obs in by_time.values()),
                f"series {series_id}: multiple independent observations at one time cannot be longitudinal replicas",
            )
            for left, right in zip(coordinates, coordinates[1:]):
                transitions.append(
                    {
                        "series_id": series_id,
                        "from": next(iter(by_time[left])),
                        "to": next(iter(by_time[right])),
                        "elapsed_time": right - left,
                    }
                )
        else:
            biological = defaultdict(list)
            for row in rows:
                biological[row["replicate"]].append(row)
            _require(
                all(
                    len({row["timepoint"] for row in values}) == 1
                    for values in biological.values()
                ),
                f"series {series_id}: independent biological replicate cannot span timepoints",
            )
            _require(
                all(
                    len({row["observation_id"] for row in values}) == 1
                    for values in biological.values()
                ),
                f"series {series_id}: independent biological replicate must identify one observation",
            )
        summaries.append(
            {
                "series_id": series_id,
                "condition": next(iter(conditions)),
                "timepoints": coordinates,
                "intervals": deltas,
                "regular": regular,
                "observations_per_timepoint": [
                    len(by_time[time]) for time in coordinates
                ],
                "columns": len(rows),
            }
        )
    counts = {
        role: len({metadata[column]["observation_id"] for column in ids})
        for role, ids in role_columns.items()
    }
    return {
        "status": "ok",
        "runtime_support": True,
        **temporal,
        "genes": len(genes),
        "columns": len(columns),
        "observations": len({row["observation_id"] for row in metadata.values()}),
        "observation_counts": counts,
        "time_series_columns": role_columns["time_series"],
        "steady_state_columns": role_columns["steady_state"],
        "observations_by_column": metadata,
        "series": summaries,
        "longitudinal_transitions": transitions,
        "sha256": {
            "expression_matrix": _sha256_file(expression_path),
            "column_descriptors": _sha256_file(column_descriptors_path),
        },
    }


def select_temporal_observations(
    summary: dict[str, Any], observation_types: list[str]
) -> dict[str, Any]:
    """Describe the exact role selection before planning or executing a tool."""
    _require(
        summary.get("status") == "ok", "validated temporal input summary is required"
    )
    _require(
        bool(observation_types)
        and len(set(observation_types)) == len(observation_types)
        and set(observation_types) <= {"time_series", "steady_state"},
        "invalid observation_types selection",
    )
    metadata = summary["observations_by_column"]
    selected = [
        column
        for column, row in metadata.items()
        if row["observation_type"] in observation_types
    ]
    selected_set = set(selected)
    ignored = [column for column in metadata if column not in selected_set]
    return {
        "observation_types": list(observation_types),
        "selected_columns": selected,
        "ignored_columns": ignored,
        "columns": len(selected),
        "time_series_columns": [
            column
            for column in selected
            if metadata[column]["observation_type"] == "time_series"
        ],
        "steady_state_columns": [
            column
            for column in selected
            if metadata[column]["observation_type"] == "steady_state"
        ],
        "observations": len(
            {metadata[column]["observation_id"] for column in selected}
        ),
    }
