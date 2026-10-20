#!/usr/bin/env python3
"""Offline audit of the proposed bulk temporal extension; no runtime adoption.

Reuses ANDREA's canonical input specs, then checks information that those specs
cannot express. See docs/bulk-time-series-contract.md. No Docker or numerical
simulation is involved.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from andrea.core.shared.input_specs import load_input_specs  # noqa: E402
from andrea.core.shared.input_validation import (
    validate_tsv_file_with_spec,
)  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, [])
        _require(bool(header), f"{path}: missing header")
        _require(all(_text(x) for x in header), f"{path}: empty header")
        _require(len(set(header)) == len(header), f"{path}: duplicate header")
        rows = []
        for number, row in enumerate(reader, start=2):
            if not row:
                continue
            _require(len(row) == len(header), f"{path}: malformed row {number}")
            rows.append(dict(zip(header, row, strict=True)))
    return header, rows


def _declarations(profile: dict[str, Any]) -> None:
    expected = {
        "schema_version",
        "status",
        "expression_matrix",
        "timepoints",
        "replicates",
        "time_basis",
        "time_unit",
        "time_origin",
        "sampling_design",
        "missing_values",
        "preprocessing",
        "network_semantics",
        "provenance",
    }
    _require(set(profile) == expected, "profile has missing or unknown fields")
    _require(
        profile["schema_version"] == "bulk-temporal-audit/1",
        "unsupported audit schema_version",
    )
    _require(
        profile["status"] == "proposal",
        "this is an offline proposal, not a runtime contract",
    )
    _require(
        profile["time_basis"] == "observed",
        "time_basis must be observed; pseudotime is distinct",
    )
    _require(_text(profile["time_unit"]), "time_unit must be explicit")
    _require(_text(profile["time_origin"]), "time_origin must be explicit")
    _require(
        profile["sampling_design"] in {"longitudinal", "independent_samples"},
        "invalid sampling_design",
    )
    _require(
        profile["missing_values"] == "reject", "only missing_values=reject is audited"
    )
    preprocessing = profile["preprocessing"]
    _require(
        isinstance(preprocessing, dict)
        and set(preprocessing) == {"normalization", "transformation"},
        "preprocessing must declare normalization and transformation",
    )
    _require(
        all(_text(x) for x in preprocessing.values()),
        "preprocessing declarations cannot be empty",
    )
    _require(
        _text(profile["provenance"]), "provenance must describe the fixture/data origin"
    )
    semantics = profile["network_semantics"]
    _require(
        isinstance(semantics, dict)
        and set(semantics) == {"scope", "directed", "sign", "score", "lag"},
        "network_semantics fields are incomplete or unknown",
    )
    _require(
        semantics["scope"] in {"global", "condition", "series"}, "invalid network scope"
    )
    _require(type(semantics["directed"]) is bool, "network directed must be boolean")
    _require(
        semantics["sign"] in {"none", "signed", "mixed"},
        "invalid network sign semantics",
    )
    _require(_text(semantics["score"]), "network score semantics must be explicit")
    lag = semantics["lag"]
    _require(
        isinstance(lag, dict) and set(lag) == {"kind", "values", "unit"},
        "lag must declare kind, values and unit",
    )
    _require(
        lag["kind"] in {"none", "sampling_steps", "elapsed_time"}, "invalid lag kind"
    )
    values = lag["values"]
    _require(isinstance(values, list), "lag values must be an array")
    _require(
        all(type(x) in {int, float} and math.isfinite(x) and x > 0 for x in values),
        "lag values must be finite and positive",
    )
    _require(values == sorted(set(values)), "lag values must be unique and increasing")
    if lag["kind"] == "none":
        _require(
            not values and lag["unit"] is None,
            "lag none requires empty values and null unit",
        )
    else:
        _require(bool(values), "lag declaration requires values")
        if lag["kind"] == "sampling_steps":
            _require(
                all(float(x).is_integer() for x in values) and lag["unit"] == "step",
                "sampling_steps requires integer values and unit=step",
            )
        else:
            _require(
                lag["unit"] == profile["time_unit"],
                "elapsed_time lag unit must match observed time_unit",
            )


def validate_bulk_temporal_profile(profile_path: Path) -> dict[str, Any]:
    """Raise ValueError on invalid data, otherwise return a reproducible audit.

    Longitudinal adjacency is descriptive only: no expression preprocessing,
    technical-replicate aggregation, interpolation or inference is performed.
    Independent samples never acquire biological longitudinal transitions.
    """
    profile_path = profile_path.resolve()
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    _require(isinstance(profile, dict), "profile must be a JSON object")
    _declarations(profile)
    paths = {}
    for key in ("expression_matrix", "timepoints", "replicates"):
        _require(_text(profile[key]), f"{key} must reference a file")
        paths[key] = (profile_path.parent / profile[key]).resolve()

    tables = {key: _table(path) for key, path in paths.items()}
    expression_header, expression_rows = tables["expression_matrix"]
    columns = expression_header[1:]
    _require(
        bool(columns) and bool(expression_rows),
        "expression must have genes and observations",
    )
    genes = {row[expression_header[0]] for row in expression_rows}
    specs = load_input_specs()
    for key, path in paths.items():
        result = validate_tsv_file_with_spec(
            key=key,
            path=path,
            spec=specs[key],
            expression_genes=genes,
            expression_columns=set(columns),
            expression_columns_count=len(columns),
            unknown_cross_check="error",
        )
        _require(not result.errors, "\n".join(result.errors))

    time_header, time_rows = tables["timepoints"]
    replicate_header, replicate_rows = tables["replicates"]
    required_time = {
        "column",
        "timepoint",
        "time_unit",
        "series_id",
        "condition",
        "observation_id",
    }
    required_replicates = {
        "column",
        "replicate",
        "replicate_kind",
        "technical_replicate",
    }
    _require(
        required_time.issubset(time_header),
        f"timepoints requires {sorted(required_time)}",
    )
    _require(
        required_replicates.issubset(replicate_header),
        f"replicates requires {sorted(required_replicates)}",
    )
    _require(
        time_header[0] == replicate_header[0] == "column",
        "metadata first header must be column",
    )
    for key, rows, required in (
        ("timepoints", time_rows, required_time),
        ("replicates", replicate_rows, required_replicates),
    ):
        _require(
            all(_text(row[field]) for row in rows for field in required),
            f"{key} contains missing temporal metadata",
        )
    times = {row["column"]: row for row in time_rows}
    replicates = {row["column"]: row for row in replicate_rows}
    _require(
        set(times) == set(replicates) == set(columns),
        "metadata must match expression columns exactly",
    )

    by_series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observations: dict[str, tuple[Any, ...]] = {}
    technical_keys: set[tuple[str, str]] = set()
    for column in columns:
        row = times[column]
        replica = replicates[column]
        _require(
            row["time_unit"] == profile["time_unit"], "mixed or inconsistent time_unit"
        )
        _require(
            replica["replicate_kind"] == "biological",
            "replicate identifies a biological unit; technical replicas use technical_replicate",
        )
        time = float(row["timepoint"])
        _require(math.isfinite(time), "timepoint must be finite")
        obs = row["observation_id"]
        signature = (row["series_id"], row["condition"], time, replica["replicate"])
        _require(
            obs not in observations or observations[obs] == signature,
            "observation_id cannot cross series, conditions, times or biological replicates",
        )
        observations[obs] = signature
        technical_key = (obs, replica["technical_replicate"])
        _require(
            technical_key not in technical_keys,
            "duplicate technical_replicate within observation_id",
        )
        technical_keys.add(technical_key)
        by_series[row["series_id"]].append(
            {
                "column": column,
                "time": time,
                "condition": row["condition"],
                "observation_id": obs,
                "replicate": replica["replicate"],
            }
        )

    summaries = []
    longitudinal_transitions = []
    for series_id, rows in sorted(by_series.items()):
        conditions = {row["condition"] for row in rows}
        _require(
            len(conditions) == 1, f"series {series_id}: condition must be constant"
        )
        coordinates = sorted({row["time"] for row in rows})
        _require(
            len(coordinates) >= 2,
            f"series {series_id}: requires at least two distinct timepoints",
        )
        by_time = {
            time: {row["observation_id"] for row in rows if row["time"] == time}
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
        if profile["sampling_design"] == "longitudinal":
            _require(
                len({row["replicate"] for row in rows}) == 1,
                f"series {series_id}: longitudinal series must identify one biological replicate",
            )
            _require(
                all(len(obs) == 1 for obs in by_time.values()),
                f"series {series_id}: multiple independent observations at one time cannot be longitudinal replicas",
            )
            for left, right in zip(coordinates, coordinates[1:]):
                longitudinal_transitions.append(
                    {
                        "series_id": series_id,
                        "from": next(iter(by_time[left])),
                        "to": next(iter(by_time[right])),
                        "elapsed_time": right - left,
                    }
                )
        else:
            replicate_times: dict[str, set[float]] = defaultdict(set)
            replicate_observations: dict[str, set[str]] = defaultdict(set)
            for row in rows:
                replicate_times[row["replicate"]].add(row["time"])
                replicate_observations[row["replicate"]].add(row["observation_id"])
            _require(
                all(len(values) == 1 for values in replicate_times.values()),
                f"series {series_id}: independent biological replicate cannot span timepoints",
            )
            _require(
                all(len(values) == 1 for values in replicate_observations.values()),
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

    return {
        "status": "ok",
        "runtime_support": False,
        "schema_version": profile["schema_version"],
        "genes": len(genes),
        "columns": len(columns),
        "observations": len(observations),
        "sampling_design": profile["sampling_design"],
        "time_unit": profile["time_unit"],
        "metadata_order_matches_expression": [row["column"] for row in time_rows]
        == columns,
        "series": summaries,
        "longitudinal_transitions": longitudinal_transitions,
        "sha256": {
            key: hashlib.sha256(path.read_bytes()).hexdigest()
            for key, path in {"profile": profile_path, **paths}.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    args = parser.parse_args()
    try:
        report = validate_bulk_temporal_profile(args.profile)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)]}, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
