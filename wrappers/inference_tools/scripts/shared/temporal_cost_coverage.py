"""Resource-specific coverage of a declared finite bulk temporal design."""

from __future__ import annotations

from itertools import product
import math
import statistics
from typing import Any

from .benchmark_inputs import validate_temporal_fixture
from .cost_resume import measurement_context

SUPPORTED_DESIGNS = {
    "independent_temporal_grid_with_branch_growth",
}
COORDINATE_AXES = (
    "genes",
    "timepoints_per_series",
    "independent_series",
    "steady_state_samples",
    "sampling",
)


def _scientific_defaults(design: dict) -> dict:
    """Require explicit method defaults rather than guessing a tool or ID."""

    def json_value(value: Any) -> bool:
        if value is None or type(value) in {str, bool, int}:
            return True
        if type(value) is float:
            return math.isfinite(value)
        if isinstance(value, list):
            return all(json_value(item) for item in value)
        if isinstance(value, dict):
            return all(
                isinstance(key, str) and json_value(item) for key, item in value.items()
            )
        return False

    defaults = design.get("scientific_defaults")
    if (
        not isinstance(defaults, dict)
        or not defaults
        or any(
            not isinstance(key, str)
            or not key.isascii()
            or not key.isidentifier()
            or not json_value(value)
            for key, value in defaults.items()
        )
    ):
        raise ValueError(
            "scientific_defaults must be a nonempty parameter object with valid names and finite JSON values."
        )
    return defaults


def _coordinate(genes: int, geometry: dict) -> tuple:
    return (
        genes,
        geometry["timepoints_per_series"],
        geometry["series_count"],
        geometry.get("steady_state_samples", 0),
        geometry.get("sampling", "regular"),
    )


def _family_domains(
    design: dict, wanted: dict[str, dict], scientific_defaults: dict
) -> list[dict]:
    """Check the declared independent branch axes against registered slices."""
    if design["kind"] != "independent_temporal_grid_with_branch_growth":
        return []
    families = design.get("families")
    if not isinstance(families, list) or not families:
        raise ValueError("Growth panels require explicit branch families.")
    seen_names, seen_profiles = set(), set()
    domains = []
    for family in families:
        name, members = family.get("id"), family.get("profile_ids")
        if (
            not isinstance(name, str)
            or not name
            or name in seen_names
            or not isinstance(members, list)
            or not members
            or any(
                member not in wanted or member in seen_profiles for member in members
            )
            or len(set(members)) != len(members)
        ):
            raise ValueError(
                "Branch families need unique names and disjoint registered profile IDs."
            )
        axes = family.get("coordinate_axes", {})
        if set(axes) != set(COORDINATE_AXES):
            raise ValueError(
                f"{name}: branch family must declare every temporal coordinate axis."
            )
        for axis in COORDINATE_AXES:
            levels = axes[axis]
            if (
                not isinstance(levels, list)
                or not levels
                or len(set(levels)) != len(levels)
            ):
                raise ValueError(f"{name}: branch axes need distinct nonempty levels.")
            if axis == "sampling":
                valid = all(value in {"regular", "irregular"} for value in levels)
            else:
                valid = all(
                    type(value) is int
                    and value >= (0 if axis == "steady_state_samples" else 1)
                    for value in levels
                )
            if not valid:
                raise ValueError(f"{name}: invalid branch coordinate axis {axis}.")
        expected = set(product(*(axes[axis] for axis in COORDINATE_AXES)))
        actual = set()
        count = 0
        for member in members:
            raw = wanted[member]
            if family.get("expected_params") != {
                **scientific_defaults,
                **raw.get("param_overrides", {}),
            } or sorted(family.get("optional_inputs", [])) != sorted(
                raw.get("optional_inputs", [])
            ):
                raise ValueError(
                    f"{name}: branch members differ from their declared parameters/optional inputs."
                )
            for size in raw["sizes"]:
                genes, _ = map(int, size.lower().split("x"))
                actual.add(_coordinate(genes, raw["temporal_fixture"]))
                count += 1
        if actual != expected or len(actual) != count:
            raise ValueError(
                f"{name}: registered slices must exactly cross the declared branch axes."
            )
        domains.append(
            {
                "id": name,
                "profile_ids": members,
                "coordinates": expected,
                "scope": family.get("scope", "Finite registered coordinates only."),
            }
        )
        seen_names.add(name)
        seen_profiles.update(members)
    if seen_profiles != set(wanted):
        raise ValueError(
            "Every registered profile must belong to exactly one branch family."
        )
    return domains


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
    Profiles retain their exact temporal shapes. Explicit families certify
    their finite independent axes, without establishing temporal interpolation.
    """
    design = recipe["calibration_design"]
    scientific_defaults = _scientific_defaults(design)
    default_input = design.get("default_input")
    if (
        not isinstance(default_input, dict)
        or set(default_input) != {"steady_state_samples", "sampling"}
        or type(default_input["steady_state_samples"]) is not int
        or default_input["steady_state_samples"] < 0
        or default_input["sampling"] not in {"regular", "irregular"}
    ):
        raise ValueError(
            "default_input must explicitly declare steady_state_samples and sampling."
        )
    if design.get("kind") not in SUPPORTED_DESIGNS:
        raise ValueError("Unsupported temporal calibration design.")
    allocation = design["resources"]
    if (
        any(type(value) is not int or value < 1 for value in allocation["threads"])
        or any(not _positive(value) for value in allocation["ram_gb"])
        or type(design["repetitions"]) is not int
        or design["repetitions"] < 3
    ):
        raise ValueError(
            "Temporal resources must be positive and at least three repetitions are required."
        )
    resources = list(product(allocation["threads"], allocation["ram_gb"]))
    repeats = design["repetitions"]
    wanted = {row["id"]: row for row in recipe["profiles"]}
    if (
        len(wanted) != len(recipe["profiles"])
        or not resources
        or len(set(resources)) != len(resources)
        or allocation.get("strata_count", len(resources)) != len(resources)
    ):
        raise ValueError(
            "Temporal panel needs distinct registered profiles and the declared resource strata."
        )
    families = _family_domains(design, wanted, scientific_defaults)
    point_count = sum(
        len(row.get("sizes", recipe.get("sizes", []))) for row in wanted.values()
    )
    if (
        design.get("total_points_per_resource_stratum") != point_count
        or design.get("planned_calibration_runs")
        != point_count * len(resources) * repeats
    ):
        raise ValueError(
            "Temporal point/run budget differs from registered sizes and resource strata."
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
    family_measured = {
        name: {tuple(resource): set() for resource in resources} for name in wanted
    }
    for name, raw in wanted.items():
        geometry = raw.get("temporal_fixture", recipe.get("temporal_fixture"))
        temporal = raw.get("temporal", recipe.get("temporal"))
        validate_temporal_fixture(temporal, geometry)
        expected_columns = geometry["series_count"] * geometry[
            "timepoints_per_series"
        ] + geometry.get("steady_state_samples", 0)
        sizes = [
            tuple(map(int, value.lower().split("x")))
            for value in raw.get("sizes", recipe.get("sizes", []))
        ]
        if not sizes or any(columns != expected_columns for _, columns in sizes):
            raise ValueError(
                f"{name}: registered temporal geometry does not match sizes."
            )
        overrides = raw.get("param_overrides", {})
        default = (
            not raw.get("optional_inputs")
            and geometry.get("steady_state_samples", 0)
            == default_input["steady_state_samples"]
            and geometry.get("sampling", "regular") == default_input["sampling"]
            and set(overrides).issubset(scientific_defaults)
            and all(
                overrides.get(key, value) == value
                for key, value in scientific_defaults.items()
            )
        )
        if default and sorted({genes for genes, _ in sizes}) != sorted(
            design["gene_levels"]
        ):
            raise ValueError(
                f"{name}: default temporal slice needs the registered three gene levels."
            )
        alpha = raw.get("param_overrides", {}).get("alpha")
        if isinstance(alpha, list) and any(genes != len(alpha) for genes, _ in sizes):
            raise ValueError(
                f"{name}: vector alpha fixes the gene axis to its exact length."
            )
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
        if profile:
            params = config.get("params_profile", {}).get("resolved_params", {})
            expected_params = {
                **scientific_defaults,
                **raw.get("param_overrides", {}),
            }
            if any(
                key not in params or params[key] != value
                for key, value in expected_params.items()
            ):
                errors.append(
                    "Measured method parameters differ from the registered defaults/branch overrides."
                )
        measured = set()
        seen_points = set()
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
            if key in seen_points:
                errors.append(f"Duplicated temporal size/resource point: {key}.")
                continue
            seen_points.add(key)
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
            required_hashes = {
                "expression.tsv",
                "params.json",
                "execution.json",
                "extra/column_descriptors.tsv",
            }
            if "tf_list" in raw.get("optional_inputs", []):
                required_hashes.add("extra/tf_list.txt")
            if not required_hashes.issubset(inputs):
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
                family_measured[name][(key[2], key[3])].add(
                    _coordinate(key[0], geometry)
                )
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
                        if len(sizes) > 1
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
                    if len(sizes) == 1
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
    family_reports = []
    for family in families:
        resource_reports = []
        for threads, ram in resources:
            observed = set().union(
                *(
                    family_measured[name][(threads, ram)]
                    for name in family["profile_ids"]
                )
            )
            missing = family["coordinates"] - observed
            resource_reports.append(
                {
                    "threads": threads,
                    "ram_gb": ram,
                    "expected_points": len(family["coordinates"]),
                    "successful_points": len(observed),
                    "missing_coordinates": [
                        dict(zip(COORDINATE_AXES, point)) for point in sorted(missing)
                    ],
                    "covered": not missing,
                }
            )
        family_reports.append(
            {
                "id": family["id"],
                "profile_ids": family["profile_ids"],
                "scope": family["scope"],
                "resources": resource_reports,
                "covered": all(row["covered"] for row in resource_reports),
            }
        )
    return {
        "profiles": reports,
        "missing_profiles": sorted(set(wanted) - set(present)),
        "duplicate_profiles": duplicates,
        "unplanned_profiles": sorted(set(present) - set(wanted)),
        "aggregate_default_temporal_domain": aggregate,
        "branch_families": family_reports,
        "resource_strata": [{"threads": t, "ram_gb": r} for t, r in resources],
        "registered_points_per_resource": point_count,
        "required_successful_repetitions": len(resources)
        * sum(len(row.get("sizes", recipe.get("sizes", []))) for row in wanted.values())
        * repeats,
        "retained_successful_repetitions": successful_repetitions,
        "calibration_adequate_for_planned_range": not duplicates
        and not (set(present) - set(wanted))
        and all(row["calibration_adequate_for_planned_range"] for row in reports)
        and all(row["covered"] for row in aggregate)
        and all(row["covered"] for row in family_reports),
        "coverage_is_prediction_validation": False,
        "interpretation": "Finite temporal/resource measurement coverage. Exact profile shapes and declared branch axes do not establish temporal, SS-count or parameter interpolation. Independent prediction validation remains separate.",
    }
