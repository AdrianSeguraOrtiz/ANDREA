"""Resource-specific coverage of the reviewed finite bulk temporal panel."""

from __future__ import annotations

from itertools import product
import math
import statistics
from typing import Any

from .benchmark_inputs import validate_temporal_fixture
from .cost_resume import measurement_context


def _positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def temporal_calibration_coverage(instance: dict, recipe: dict) -> dict:
    """Require the actual registered shapes/resources and three distinct receipts.

    This certifies the finite measurement domain, never predictive accuracy.
    Default profiles are gene-axis slices of a combined three-axis grid. Named
    branch profiles certify only their single registered point, with no curve.
    """
    design = recipe["calibration_design"]
    if design.get("kind") != "independent_temporal_default_grid_with_branch_centers":
        raise ValueError("Unsupported temporal calibration design.")
    resources = list(
        product(design["resources"]["threads"], design["resources"]["ram_gb"])
    )
    repeats = max(3, design["repetitions"])
    wanted = {row["id"]: row for row in recipe["profiles"]}
    if len(wanted) != len(recipe["profiles"]) or len(set(resources)) != 4:
        raise ValueError(
            "Temporal panel needs distinct registered profiles and four resource strata."
        )
    if any(
        len(set(design[key])) != 3
        for key in (
            "gene_levels",
            "timepoints_per_series_levels",
            "independent_series_levels",
        )
    ):
        raise ValueError(
            "The default temporal panel requires three independent levels on each axis."
        )
    present: dict[str, dict] = {}
    duplicates = []
    for profile in instance.get("profiles", []):
        name = profile.get("profile_id")
        if name in present:
            duplicates.append(name)
        present[name] = profile
    seen_runs: set[str] = set()
    reports = []
    default_coordinates: dict[tuple[int, float], set[tuple[int, int, int]]] = {
        tuple(r): set() for r in resources
    }
    successful_repetitions = 0
    for name, raw in wanted.items():
        geometry = raw.get("temporal_fixture", recipe.get("temporal_fixture"))
        temporal = raw.get("temporal", recipe.get("temporal"))
        validate_temporal_fixture(temporal, geometry)
        expected_columns = geometry["series_count"] * geometry["timepoints_per_series"]
        sizes = [
            tuple(map(int, value.lower().split("x")))
            for value in raw.get("sizes", recipe.get("sizes", []))
        ]
        if not sizes or any(columns != expected_columns for _, columns in sizes):
            raise ValueError(
                f"{name}: registered temporal geometry does not match sizes."
            )
        default = name.startswith("global_rf_from_data_ts_")
        if default and sorted({genes for genes, _ in sizes}) != sorted(
            design["gene_levels"]
        ):
            raise ValueError(
                f"{name}: default temporal slice needs the registered three gene levels."
            )
        if not default and len(sizes) != 1:
            raise ValueError(f"{name}: branch scope must be an explicit single point.")
        expected = {(g, c, t, float(r)) for (g, c), (t, r) in product(sizes, resources)}
        profile = present.get(name, {})
        config = profile.get("benchmark_config", {})
        input_profile = config.get("input_profile", {})
        errors = []
        if profile and (
            input_profile.get("temporal") != temporal
            or input_profile.get("temporal_fixture") != geometry
        ):
            errors.append(
                "Measured temporal contract/geometry differs from registered recipe."
            )
        if default and profile:
            params = config.get("params_profile", {}).get("resolved_params", {})
            defaults = {
                "tree_method": "RF",
                "alpha": "from_data",
                "K": "sqrt",
                "ntrees": 1000,
                "compute_quality_scores": False,
                "save_models": False,
            }
            if any(params.get(key) != value for key, value in defaults.items()):
                errors.append(
                    "Default grid does not retain actual registered method defaults."
                )
        measured = set()
        failed_repetitions = 0
        for point in profile.get("runtime_points", []):
            key = (
                point.get("genes"),
                point.get("columns"),
                point.get("threads"),
                float(point.get("ram_gb", 0)),
            )
            if key not in expected:
                errors.append(f"Unregistered temporal size/resource point: {key}.")
                continue
            evidence = point.get("measurement_evidence", {})
            observations = evidence.get("observations", [])
            failed_repetitions += sum(row.get("status") != "ok" for row in observations)
            successes = [
                row
                for row in observations
                if row.get("status") == "ok" and _positive(row.get("seconds"))
            ]
            point_errors = []
            if len(successes) < repeats or point.get("repeats_ok") != len(successes):
                point_errors.append(
                    "At least three retained successful repeats and accurate counts are required."
                )
            if evidence.get("measurement_context") != measurement_context(config):
                point_errors.append(
                    "Raw evidence measurement context differs from the measured profile."
                )
            inputs = evidence.get("input_sha256", {})
            if not {
                "expression.tsv",
                "params.json",
                "execution.json",
                "extra/timepoints.tsv",
                "extra/replicates.tsv",
            }.issubset(inputs):
                point_errors.append(
                    "Raw evidence must identify the matrices, resolved configuration and temporal metadata."
                )
            for row in observations:
                run_id = row.get("run_id")
                if not isinstance(run_id, str) or not run_id or run_id in seen_runs:
                    point_errors.append("Missing or reused actual execution run_id.")
                else:
                    seen_runs.add(run_id)
                observed_key = (
                    row.get("genes"),
                    row.get("columns"),
                    row.get("threads"),
                    float(row.get("ram_gb", 0)),
                )
                if observed_key != key or row.get("input_sha256") != inputs:
                    point_errors.append(
                        "Raw observation contradicts point dimensions/resources or input hashes."
                    )
                dimensions = row.get("dimensions", {})
                expected_dimensions = {
                    "genes": key[0],
                    "columns": key[1],
                    "timepoints_per_series": geometry["timepoints_per_series"],
                    "independent_series": geometry["series_count"],
                    "steady_state_samples": geometry.get("steady_state_samples", 0),
                }
                if any(
                    dimensions.get(field) != value
                    for field, value in expected_dimensions.items()
                ):
                    point_errors.append(
                        "Raw observation contradicts the explicit temporal dimensions."
                    )
                provenance = row.get("measurement_provenance", {})
                if provenance.get("image_id") != config.get("provenance", {}).get(
                    "image_id"
                ):
                    point_errors.append(
                        "Raw observation image differs from the profile image."
                    )
            if successes:
                median = statistics.median(row["seconds"] for row in successes)
                if not _positive(point.get("seconds_p50")) or not math.isclose(
                    point["seconds_p50"], median, rel_tol=1e-9, abs_tol=1e-6
                ):
                    point_errors.append(
                        "Reported median differs from retained observations."
                    )
            if point_errors:
                errors.extend(
                    f"{key}: {message}" for message in dict.fromkeys(point_errors)
                )
            else:
                measured.add(key)
                successful_repetitions += len(successes)
                if default:
                    default_coordinates[(key[2], key[3])].add(
                        (
                            key[0],
                            geometry["timepoints_per_series"],
                            geometry["series_count"],
                        )
                    )
        resource_reports = []
        for threads, ram in resources:
            expected_slice = {
                (g, c) for g, c, t, r in expected if (t, r) == (threads, float(ram))
            }
            measured_slice = {
                (g, c) for g, c, t, r in measured if (t, r) == (threads, float(ram))
            }
            missing = expected_slice - measured_slice
            resource_reports.append(
                {
                    "threads": threads,
                    "ram_gb": ram,
                    "fixed_columns_contract": expected_columns,
                    "temporal_fixture": geometry,
                    "gene_levels": sorted(g for g, _ in measured_slice),
                    "scope": (
                        "gene_axis_only"
                        if default
                        else "registered_point_only_no_growth_claim"
                    ),
                    "planned_sizes_missing": [
                        {"genes": g, "columns": c} for g, c in sorted(missing)
                    ],
                    "calibration_adequate_for_planned_range": not missing,
                }
            )
        missing_points = expected - measured
        reports.append(
            {
                "profile_id": name,
                "resources": resource_reports,
                "errors": errors,
                "declared_points": len(expected),
                "successful_points": len(measured),
                "failed_repetitions": failed_repetitions,
                "declared_matrix_complete": not missing_points,
                "declared_points_missing": [
                    dict(zip(("genes", "columns", "threads", "ram_gb"), point))
                    for point in sorted(missing_points)
                ],
                "calibration_adequate_for_planned_range": not errors
                and not missing_points,
                "interpretation": (
                    "Exact registered branch point only; no size growth claim."
                    if not default
                    else "Gene-axis coverage at one fixed temporal shape."
                ),
            }
        )
    expected_default = set(
        product(
            design["gene_levels"],
            design["timepoints_per_series_levels"],
            design["independent_series_levels"],
        )
    )
    aggregate = [
        {
            "threads": t,
            "ram_gb": r,
            "expected_points": len(expected_default),
            "successful_points": len(default_coordinates[(t, r)]),
            "missing_coordinates": [
                dict(
                    zip(("genes", "timepoints_per_series", "independent_series"), point)
                )
                for point in sorted(expected_default - default_coordinates[(t, r)])
            ],
            "covered": expected_default <= default_coordinates[(t, r)],
        }
        for t, r in resources
    ]
    return {
        "profiles": reports,
        "missing_profiles": sorted(set(wanted) - set(present)),
        "duplicate_profiles": duplicates,
        "unplanned_profiles": sorted(set(present) - set(wanted)),
        "aggregate_default_temporal_domain": aggregate,
        "registered_points_per_resource": sum(
            len(row.get("sizes", recipe.get("sizes", []))) for row in wanted.values()
        ),
        "required_successful_repetitions": len(resources)
        * sum(len(row.get("sizes", recipe.get("sizes", []))) for row in wanted.values())
        * repeats,
        "retained_successful_repetitions": successful_repetitions,
        "calibration_adequate_for_planned_range": not duplicates
        and all(row["calibration_adequate_for_planned_range"] for row in reports)
        and all(row["covered"] for row in aggregate),
        "coverage_is_prediction_validation": False,
        "interpretation": "Finite temporal/resource measurement coverage. The default 3-axis grid does not establish a temporal growth estimator; branch centers have only point support. Independent prediction validation remains separate.",
    }
