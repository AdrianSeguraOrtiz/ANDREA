"""Synthetic unit records exercise generic coverage; they are never measurements."""

from __future__ import annotations

import copy
from itertools import product
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts"))
from shared.cost_resume import measurement_context
from shared.temporal_cost_coverage import temporal_calibration_coverage


def panel_fixture():
    """Small local design, independent of any tool recipe or campaign cache."""
    temporal = {
        "schema_version": "bulk-temporal/1",
        "time_basis": "observed",
        "time_unit": "hour",
        "sampling_design": "longitudinal",
    }
    defaults = {"method": "example", "iterations": 4}
    profiles = []
    for points, series in product((2, 3, 4), (1, 2, 3)):
        profiles.append(
            {
                "id": f"unit_t{points}_s{series}",
                "temporal_fixture": {
                    "series_count": series,
                    "timepoints_per_series": points,
                    "steady_state_samples": 0,
                    "sampling": "regular",
                },
                "sizes": [f"{genes}x{points * series}" for genes in (2, 3, 4)],
            }
        )
    profiles.append(
        {
            "id": "unit_mixed",
            "temporal_fixture": {
                "series_count": 2,
                "timepoints_per_series": 3,
                "steady_state_samples": 2,
                "sampling": "regular",
            },
            "sizes": [f"{genes}x8" for genes in (2, 3, 4)],
            "param_overrides": {"method": "alternative"},
        }
    )
    families = [
        {
            "id": "default",
            "profile_ids": [row["id"] for row in profiles[:-1]],
            "expected_params": defaults,
            "optional_inputs": [],
            "coordinate_axes": {
                "genes": [2, 3, 4],
                "timepoints_per_series": [2, 3, 4],
                "independent_series": [1, 2, 3],
                "steady_state_samples": [0],
                "sampling": ["regular"],
            },
        },
        {
            "id": "mixed",
            "profile_ids": ["unit_mixed"],
            "expected_params": {**defaults, "method": "alternative"},
            "optional_inputs": [],
            "coordinate_axes": {
                "genes": [2, 3, 4],
                "timepoints_per_series": [3],
                "independent_series": [2],
                "steady_state_samples": [2],
                "sampling": ["regular"],
            },
        },
    ]
    recipe = {
        "temporal": temporal,
        "profiles": profiles,
        "calibration_design": {
            "kind": "independent_temporal_grid_with_branch_growth",
            "scientific_defaults": defaults,
            "default_input": {"steady_state_samples": 0, "sampling": "regular"},
            "resources": {"threads": [1, 2], "ram_gb": [1], "strata_count": 2},
            "repetitions": 3,
            "gene_levels": [2, 3, 4],
            "timepoints_per_series_levels": [2, 3, 4],
            "independent_series_levels": [1, 2, 3],
            "total_points_per_resource_stratum": 30,
            "planned_calibration_runs": 180,
            "families": families,
        },
    }
    instance = {"profiles": []}
    for raw in profiles:
        geometry = raw["temporal_fixture"]
        config = {
            "input_profile": {
                "temporal": temporal,
                "temporal_fixture": geometry,
                "observation_types": ["time_series", "steady_state"],
            },
            "params_profile": {
                "resolved_params": {**defaults, **raw.get("param_overrides", {})}
            },
            "execution_profile": {"mode": "global"},
            "provenance": {"image_id": "sha256:" + "a" * 64},
        }
        measured = {
            "profile_id": raw["id"],
            "benchmark_config": config,
            "runtime_points": [],
        }
        for size, threads in product(raw["sizes"], (1, 2)):
            genes, columns = map(int, size.split("x"))
            hashes = {
                name: "b" * 64
                for name in (
                    "expression.tsv",
                    "params.json",
                    "execution.json",
                    "extra/column_descriptors.tsv",
                )
            }
            observations = [
                {
                    "status": "ok",
                    "seconds": seconds,
                    "genes": genes,
                    "columns": columns,
                    "threads": threads,
                    "ram_gb": 1,
                    "run_id": f"unit-{raw['id']}-{genes}-{threads}-{repeat}",
                    "input_sha256": hashes,
                    "measurement_provenance": config["provenance"],
                    "dimensions": {
                        "genes": genes,
                        "columns": columns,
                        "timepoints_per_series": geometry["timepoints_per_series"],
                        "independent_series": geometry["series_count"],
                        "steady_state_samples": geometry["steady_state_samples"],
                    },
                }
                for repeat, seconds in enumerate((1.0, 2.0, 3.0))
            ]
            measured["runtime_points"].append(
                {
                    "genes": genes,
                    "columns": columns,
                    "threads": threads,
                    "ram_gb": 1,
                    "status": "ok",
                    "repeats_ok": 3,
                    "seconds_p50": 2.0,
                    "measurement_evidence": {
                        "input_sha256": hashes,
                        "measurement_context": measurement_context(config),
                        "observations": observations,
                    },
                }
            )
        instance["profiles"].append(measured)
    return recipe, instance


def test_complete_generic_design_has_exact_finite_resource_coverage():
    recipe, instance = panel_fixture()
    report = temporal_calibration_coverage(instance, recipe)
    assert report["calibration_adequate_for_planned_range"]
    assert report["retained_successful_repetitions"] == 180
    assert report["required_successful_repetitions"] == 180
    assert report["registered_points_per_resource"] == 30
    assert all(row["covered"] for row in report["branch_families"])
    assert not report["coverage_is_prediction_validation"]


@pytest.mark.parametrize(
    "mutation",
    [
        "resource",
        "dimensions",
        "hash",
        "run_id",
        "parameters",
        "duplicate_point",
        "unknown_profile",
        "shape",
    ],
)
def test_missing_or_contradictory_measurements_cannot_certify_coverage(mutation):
    recipe, instance = panel_fixture()
    profile = instance["profiles"][0]
    point = profile["runtime_points"][0]
    observations = point["measurement_evidence"]["observations"]
    if mutation == "resource":
        profile["runtime_points"] = [
            row for row in profile["runtime_points"] if row["threads"] == 1
        ]
    elif mutation == "dimensions":
        observations[0]["dimensions"]["independent_series"] = 99
    elif mutation == "hash":
        point["measurement_evidence"]["input_sha256"].pop(
            "extra/column_descriptors.tsv"
        )
    elif mutation == "run_id":
        observations[1]["run_id"] = observations[0]["run_id"]
    elif mutation == "parameters":
        profile["benchmark_config"]["params_profile"]["resolved_params"][
            "iterations"
        ] = 1
    elif mutation == "duplicate_point":
        profile["runtime_points"].append(copy.deepcopy(point))
    elif mutation == "unknown_profile":
        extra = copy.deepcopy(profile)
        extra["profile_id"] = "unknown"
        instance["profiles"].append(extra)
    else:
        profile["benchmark_config"]["input_profile"]["temporal_fixture"] = (
            copy.deepcopy(recipe["profiles"][1]["temporal_fixture"])
        )
    assert not temporal_calibration_coverage(instance, recipe)[
        "calibration_adequate_for_planned_range"
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "budget",
        "family_cross",
        "resource",
        "column_sum",
        "missing_defaults",
        "missing_default_input",
    ],
)
def test_ambiguous_or_inconsistent_designs_fail_closed(mutation):
    recipe, instance = panel_fixture()
    design = recipe["calibration_design"]
    if mutation == "budget":
        design["planned_calibration_runs"] += 1
    elif mutation == "family_cross":
        design["families"][0]["coordinate_axes"]["genes"].append(5)
    elif mutation == "resource":
        design["resources"]["threads"].append(2)
    elif mutation == "column_sum":
        recipe["profiles"][-1]["sizes"] = ["2x6", "3x6", "4x6"]
    elif mutation == "missing_defaults":
        design.pop("scientific_defaults")
    else:
        design.pop("default_input")
    with pytest.raises(ValueError):
        temporal_calibration_coverage(instance, recipe)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        [],
        True,
        {"bad name": 1},
        {"iterations": float("inf")},
        {"weights": [float("nan")]},
    ],
)
def test_scientific_defaults_are_explicit_finite_json(invalid):
    recipe, instance = panel_fixture()
    recipe["calibration_design"]["scientific_defaults"] = invalid
    with pytest.raises(ValueError, match="scientific_defaults"):
        temporal_calibration_coverage(instance, recipe)
