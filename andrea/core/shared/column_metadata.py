"""Scientific metadata checks for the requested temporal inference methods."""

from __future__ import annotations

import csv
import math
from pathlib import Path

TEMPORAL_SUMMARY_SCHEMA_VERSION = "bulk-temporal/1"
TEMPORAL_COLUMNS = (
    "column",
    "observation_type",
    "series_id",
    "condition",
    "replicate",
    "observation_id",
    "technical_replicate",
    "timepoint",
    "time_unit",
    "sampling_design",
)
OBSERVATION_TYPES = ("time_series", "steady_state")


def load_column_descriptors(path: Path, columns: list[str]) -> dict[str, dict]:
    """Join exact expression IDs and validate TS/SS roles without averaging.

    Extra columns are retained as supplied for the requested method.
    """
    if len(set(columns)) != len(columns) or any(
        not x or x != x.strip() for x in columns
    ):
        raise ValueError("Expression columns must be unique exact nonempty identifiers")
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headings = reader.fieldnames or []
        if (
            len(set(headings)) != len(headings)
            or any(not name or name != name.strip() for name in headings)
            or not set(TEMPORAL_COLUMNS) <= set(headings)
            or headings[0] != "column"
        ):
            raise ValueError(
                "column_descriptors requires unique canonical metadata headings"
            )
        result = {}
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"column_descriptors: malformed row {number}")
            for name in TEMPORAL_COLUMNS:
                if row[name] != row[name].strip():
                    raise ValueError(
                        f"column_descriptors: {name} must use exact values at row {number}"
                    )
            role = row["observation_type"]
            if role not in OBSERVATION_TYPES:
                raise ValueError("observation_type must be time_series or steady_state")
            required = {
                "column",
                "condition",
                "replicate",
                "observation_id",
                "technical_replicate",
            }
            if role == "time_series":
                required.update(
                    {"series_id", "timepoint", "time_unit", "sampling_design"}
                )
            if any(not row[name] for name in required):
                raise ValueError(
                    f"column_descriptors: missing required {role} metadata at row {number}"
                )
            if role == "steady_state":
                if any(
                    row[name]
                    for name in (
                        "series_id",
                        "timepoint",
                        "time_unit",
                        "sampling_design",
                    )
                ):
                    raise ValueError(
                        "steady_state rows require empty series_id, timepoint, time_unit and sampling_design"
                    )
                row["timepoint"] = None
            else:
                if row["sampling_design"] not in {
                    "longitudinal",
                    "independent_samples",
                }:
                    raise ValueError(
                        "sampling_design must be longitudinal or independent_samples"
                    )
                try:
                    value = float(row["timepoint"])
                except ValueError as exc:
                    raise ValueError(
                        "time_series timepoint must be a finite number"
                    ) from exc
                if not math.isfinite(value):
                    raise ValueError("time_series timepoint must be a finite number")
                row["timepoint"] = value
            column = row["column"]
            if column in result:
                raise ValueError("column_descriptors: duplicate expression-column ID")
            result[column] = row
    if set(result) != set(columns):
        raise ValueError("column_descriptors must match expression columns exactly")
    observations, technical = {}, set()
    for column in columns:
        row = result[column]
        signature = tuple(
            row[name]
            for name in (
                "observation_type",
                "series_id",
                "condition",
                "replicate",
                "timepoint",
                "time_unit",
                "sampling_design",
            )
        )
        observation = row["observation_id"]
        if observation in observations and observations[observation] != signature:
            raise ValueError(
                "observation_id cannot cross roles, series, conditions, times or biological replicates"
            )
        observations[observation] = signature
        measurement = (observation, row["technical_replicate"])
        if measurement in technical:
            raise ValueError("duplicate technical_replicate within observation_id")
        technical.add(measurement)
    return {column: result[column] for column in columns}


def summarize_temporal_metadata(descriptors: dict[str, dict]) -> dict[str, str]:
    """Derive dataset-level temporal facts from the normalized metadata table."""
    rows = [
        row for row in descriptors.values() if row["observation_type"] == "time_series"
    ]
    if not rows:
        raise ValueError("Bulk timepoints require time_series observations")
    units = {row["time_unit"] for row in rows}
    designs = {row["sampling_design"] for row in rows}
    if len(units) != 1:
        raise ValueError("mixed or inconsistent time_unit")
    if len(designs) != 1:
        raise ValueError(
            "mixed sampling_design is not supported in one temporal dataset"
        )
    return {
        "schema_version": TEMPORAL_SUMMARY_SCHEMA_VERSION,
        "time_basis": "observed",
        "time_unit": next(iter(units)),
        "sampling_design": next(iter(designs)),
    }
