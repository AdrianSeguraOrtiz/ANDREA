"""Measured support must govern simulator interpolation and its provenance."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from andrea.core.commands.generate_data.cost_planner import (
    _estimate_run_modes,
    _estimate_supported_runtime,
    _nearest_point,
)


AXES = {"measurement": "rna_expression", "resolution": "single_cell",
        "column_kind": "cells", "experimental_design": "observational"}
TRUTH = {"contexts": ["global"]}
SPEC = {"runtime_resources": {
    "threading": {"supported": False, "default_threads": 1, "max_threads": 1}}}


def point(genes, cells, seconds, *, threads=1, ram=8, groups=0):
    return {"genes": genes, "cells": cells, "threads": threads, "ram_gb": ram,
            "groups": groups, "population_count": groups, "status": "ok",
            "seconds_p50": seconds, "seconds_p90": seconds,
            "repeats_total": 3, "repeats_ok": 3, "ok_rate": 1,
            "feature_vector": {"effective_extras": ["tf_list"]}}


def estimate(points, genes, cells, *, groups=0, configuration_matches=True):
    nearest = _nearest_point(points=points, genes=genes, cells=cells, groups=groups,
                             threads=1, ram_gb=8)
    return _estimate_supported_runtime(
        points=points, nearest=nearest, genes=genes, cells=cells, groups=groups,
        population_count=groups, threads=1, ram_gb=8,
        configuration_matches=configuration_matches, dimensions_resolved=True,
    )


def profile(points, *, name="measured", cost_params=None):
    return {
        "profile_id": name,
        "benchmark_config": {
            "data_axes": AXES, "truth_requirements": TRUTH,
            "dimension_profile": {"genes_param": "num_genes", "cells_param": "num_cells",
                                  "group_count": 0, "population_count": 0},
            "input_profile": {"requested_extras": [], "effective_extras": ["tf_list"]},
            "params_profile": {"cost_relevant_params": list(cost_params or {}),
                               "cost_relevant_values": cost_params or {}},
        },
        "runtime_points": points,
    }


def run_modes(profiles, params):
    with patch(
        "andrea.core.commands.generate_data.cost_planner._load_simulator_cost_payload",
        return_value=({"profiles": profiles}, []),
    ):
        return _estimate_run_modes(
            run={"simulator_id": "example", "run_id": "test", "simulator_params": params},
            spec=SPEC, data_axes=AXES, truth_requirements=TRUTH,
            requested_extras=[], effective_extras=["tf_list"], input_ids=set(),
            max_cores=1, max_ram_gb=8,
        )


def test_fixed_gene_interpolation_preserves_constant_startup_overhead():
    points = [point(50, 100, 10), point(50, 200, 10)]
    seconds, model = estimate(points, 50, 150)
    assert seconds == 10  # Old sqrt(size) scaling changed a constant runtime.
    assert model["estimation_method"] == "interpolated"
    assert model["calibrated"] is True
    assert model["geometry"] == "segment"
    assert [a["weight"] for a in model["anchors"]] == [0.5, 0.5]


def test_triangle_interpolation_recovers_affine_runtime_inside_convex_hull():
    # f(g,c) = 2 + g/10 + c/20, including a nonzero startup term.
    points = [point(10, 20, 4), point(30, 20, 6), point(10, 60, 6)]
    seconds, model = estimate(points, 15, 30)
    assert seconds == pytest.approx(5)
    assert model["geometry"] == "triangle"
    assert sum(a["weight"] for a in model["anchors"]) == pytest.approx(1)
    # A query inside the bounding rectangle but outside the triangle is NOT interpolation.
    _, outside = estimate(points, 25, 50)
    assert outside["estimation_method"] == "extrapolated"
    assert outside["calibrated"] is False


def test_diagonal_support_does_not_claim_independent_gene_cell_coverage():
    points = [point(50, 20, 10), point(100, 40, 20), point(200, 80, 40)]
    seconds, diagonal = estimate(points, 150, 60)
    assert seconds == pytest.approx(30)
    assert diagonal["estimation_method"] == "interpolated"
    _, off_diagonal = estimate(points, 100, 60)
    assert off_diagonal["estimation_method"] == "extrapolated"
    assert off_diagonal["calibrated"] is False


@pytest.mark.parametrize("foreign", [
    point(50, 200, 99, threads=2),
    point(50, 200, 99, ram=32),
    point(50, 200, 99, groups=2),
])
def test_interpolation_never_combines_resource_or_context_strata(foreign):
    _, model = estimate([point(50, 100, 10), foreign], 50, 150)
    assert model["estimation_method"] == "extrapolated"
    assert model["same_resource_points"] == 1


def test_exact_point_has_explicit_metadata_and_preserves_public_eta_source():
    modes, warnings = run_modes([profile([point(50, 100, 10)])],
                                {"num_genes": 50, "num_cells": 100})
    assert not warnings
    assert modes[0].eta_source == "cost_profile"
    assert modes[0].eta_seconds == 10
    assert modes[0].eta_provenance["estimation_method"] == "exact"
    assert modes[0].eta_provenance["cost_profile"]["size_scale"] == 1


def test_only_declared_dimension_parameters_are_ignored_in_configuration_matching():
    prof = profile([point(50, 100, 10), point(50, 200, 20)],
                   cost_params={"num_cells": 100, "noise": 0})
    modes, warnings = run_modes([prof], {"num_genes": 50, "num_cells": 150, "noise": 0})
    assert not warnings
    assert modes[0].eta_provenance["estimation_method"] == "interpolated"
    assert modes[0].eta_seconds == 15
    modes, warnings = run_modes([prof], {"num_genes": 50, "num_cells": 150, "noise": 1})
    assert warnings
    assert modes[0].eta_provenance["estimation_method"] == "fallback"
    assert modes[0].eta_provenance["calibrated"] is False


def test_contextual_multiplier_remains_a_configuration_parameter():
    prof = profile([point(50, 100, 10), point(50, 200, 20)],
                   cost_params={"number_sc": 50, "number_bins": 2})
    prof["benchmark_config"]["dimension_profile"]["cells_param"] = {
        "param": "number_sc", "multiplier_param": "number_bins"}
    modes, _ = run_modes([prof], {"num_genes": 50, "number_sc": 75, "number_bins": 2})
    assert modes[0].eta_provenance["estimation_method"] == "interpolated"
    modes, _ = run_modes([prof], {"num_genes": 50, "number_sc": 50, "number_bins": 3})
    assert modes[0].eta_provenance["estimation_method"] == "fallback"


def test_unlisted_simulation_control_still_uses_recorded_complete_recipe():
    prof = profile([point(50, 100, 10)], cost_params={"noise": 0})
    prof["benchmark_config"]["params_profile"]["resolved_base_params"] = {
        "num_genes": 50, "num_cells": 100, "noise": 0,
        "simulation_params": {"num_simulations": 4, "census_interval": 4.0},
    }
    modes, warnings = run_modes([prof], {
        "num_genes": 50, "num_cells": 100, "noise": 0,
        "simulation_params": {"num_simulations": 100, "census_interval": 4.0},
    })
    provenance = modes[0].eta_provenance
    assert provenance["estimation_method"] == "fallback"
    assert provenance["calibrated"] is False
    assert provenance["cost_profile"]["match"]["parameter_differences"] == {
        "simulation_params.num_simulations": {"calibrated": 4, "requested": 100},
    }
    assert warnings


def test_selector_prefers_actual_measured_support_over_legacy_profile_order():
    legacy = profile([point(50, 100, 10)], name="legacy")
    rich = profile([point(50, 100, 10), point(50, 200, 20)], name="coverage")
    modes, warnings = run_modes([legacy, rich], {"num_genes": 50, "num_cells": 150})
    assert not warnings
    assert modes[0].eta_provenance["cost_profile"]["profile_id"] == "coverage"
    assert modes[0].eta_provenance["estimation_method"] == "interpolated"
    exact = profile([point(50, 150, 17)], name="exact")
    modes, _ = run_modes([legacy, rich, exact], {"num_genes": 50, "num_cells": 150})
    assert modes[0].eta_provenance["cost_profile"]["profile_id"] == "exact"
    assert modes[0].eta_seconds == 17


def test_profile_selection_respects_available_resources():
    unsupported = profile([point(50, 150, 5, threads=2)], name="unavailable")
    supported = profile([point(50, 100, 10), point(50, 200, 20)], name="available")
    modes, _ = run_modes([unsupported, supported], {"num_genes": 50, "num_cells": 150})
    assert modes[0].eta_provenance["cost_profile"]["profile_id"] == "available"
    assert modes[0].eta_provenance["estimation_method"] == "interpolated"


def test_equivalent_profile_prefers_recorded_output_validation_over_catalog_order():
    legacy = profile([point(50, 100, 10)], name="legacy")
    audited = profile([point(50, 100, 12)], name="audited")
    audited["benchmark_config"]["extension_identity"] = {
        "validation_sha256": "a" * 64, "runner_sha256": "b" * 64,
    }
    modes, warnings = run_modes([legacy, audited], {"num_genes": 50, "num_cells": 100})
    assert not warnings
    provenance = modes[0].eta_provenance["cost_profile"]
    assert provenance["profile_id"] == "audited"
    assert provenance["match"]["output_validation_recorded"] is True
    assert modes[0].eta_seconds == 12


@pytest.mark.parametrize("identity", [
    None, [], {}, {"validation_sha256": "a" * 64}, {"runner_sha256": "b" * 64},
    {"validation_sha256": "a" * 63, "runner_sha256": "b" * 64},
    {"validation_sha256": "g" * 64, "runner_sha256": "b" * 64},
    {"validation_sha256": "a" * 64, "runner_sha256": True},
])
def test_incomplete_validation_identity_does_not_win_a_profile_tie(identity):
    legacy = profile([point(50, 100, 10)], name="legacy")
    incomplete = profile([point(50, 100, 12)], name="incomplete")
    incomplete["benchmark_config"]["extension_identity"] = identity
    modes, _ = run_modes([legacy, incomplete], {"num_genes": 50, "num_cells": 100})
    provenance = modes[0].eta_provenance["cost_profile"]
    assert provenance["profile_id"] == "legacy"
    assert provenance["match"]["output_validation_recorded"] is False
    assert modes[0].eta_seconds == 10


@pytest.mark.parametrize("difference", ["geometry", "configuration", "size_coverage"])
def test_recorded_validation_never_overrides_better_profile_support(difference):
    legacy = profile([point(50, 100, 10), point(50, 200, 20)], name="legacy",
                     cost_params={"noise": 0})
    audited = deepcopy(legacy)
    audited["profile_id"] = "audited"
    audited["benchmark_config"]["extension_identity"] = {
        "validation_sha256": "a" * 64, "runner_sha256": "b" * 64,
    }
    if difference == "geometry":
        audited["runtime_points"] = [point(50, 50, 5), point(50, 150, 15)]
    elif difference == "configuration":
        audited["benchmark_config"]["params_profile"]["cost_relevant_values"]["noise"] = 1
    else:
        audited["runtime_points"] = [point(50, 100, 10)]
    modes, _ = run_modes([audited, legacy], {"num_genes": 50, "num_cells": 100, "noise": 0})
    provenance = modes[0].eta_provenance["cost_profile"]
    assert provenance["profile_id"] == "legacy"
    assert provenance["match"]["output_validation_recorded"] is False
    assert modes[0].eta_provenance["estimation_method"] == "exact"


def test_invalid_dimension_resolution_is_labeled_fallback_not_exact():
    prof = profile([point(50, 100, 10)])
    prof["benchmark_config"]["dimension_profile"]["cells_param"] = {
        "param": "number_sc", "multiplier_param": "missing_bins"}
    modes, warnings = run_modes([prof], {"num_genes": 50, "num_cells": 100, "number_sc": 10})
    assert modes[0].eta_provenance["estimation_method"] == "fallback"
    assert warnings


def test_input_order_does_not_change_interpolation():
    points = [point(10, 20, 4), point(30, 20, 6), point(10, 60, 6)]
    assert estimate(points, 15, 30) == estimate(list(reversed(points)), 15, 30)


def test_partial_points_are_not_calibrated_support():
    partial = deepcopy(point(50, 200, 20))
    partial.update(status="partial", repeats_ok=1, ok_rate=1/3)
    _, model = estimate([point(50, 100, 10), partial], 50, 150)
    assert model["estimation_method"] == "extrapolated"
    assert model["calibrated"] is False


def test_bounded_axis_extrapolation_preserves_measured_startup_cost():
    seconds, model = estimate([point(50, 100, 10), point(50, 200, 10)], 50, 300)
    assert seconds == 10
    assert model["geometry"] == "measured_axis_affine_extrapolation"
    assert model["calibrated"] is False
    seconds, model = estimate([point(50, 100, 12), point(50, 200, 14)], 50, 300)
    assert seconds == 16  # ten seconds startup plus 0.02 per column
    assert model["startup_seconds"] == 10


@pytest.mark.parametrize("points,genes,cells", [
    ([point(50, 100, 12), point(50, 200, 10)], 50, 300),  # negative slope
    ([point(50, 100, 10), point(50, 200, 30)], 50, 300),  # negative intercept
    ([point(50, 100, 10), point(50, 200, 10)], 50, 500),  # too far
    ([point(50, 100, 10), point(50, 200, 10)], 60, 300),  # unmeasured gene axis
])
def test_unsupported_axis_extrapolation_remains_a_heuristic(points, genes, cells):
    _, model = estimate(points, genes, cells)
    assert model["geometry"] == "square_root_size_heuristic"
    assert model["calibrated"] is False


def test_different_configuration_cannot_use_measured_extrapolation():
    _, model = estimate([point(50, 100, 10), point(50, 200, 10)], 50, 300,
                        configuration_matches=False)
    assert model["estimation_method"] == "fallback"
    assert model["geometry"] == "square_root_size_heuristic"


def test_explicit_and_implicit_mandatory_extras_have_the_same_runtime():
    prof = profile([point(50, 100, 10)])
    prof['benchmark_config']['input_profile']['requested_extras'] = ['tf_list']
    modes, warnings = run_modes([prof], {'num_genes': 50, 'num_cells': 100})
    assert not warnings
    assert modes[0].eta_provenance['estimation_method'] == 'exact'
    assert modes[0].eta_provenance['cost_profile']['match']['requested_extra_differences'] == ['tf_list']
    # An additional effective artifact still changes the measured workload.
    prof['benchmark_config']['input_profile']['effective_extras'].append('prior_grn')
    prof['benchmark_config']['input_profile']['requested_extras'].append('prior_grn')
    modes, warnings = run_modes([prof], {'num_genes': 50, 'num_cells': 100})
    assert warnings
    assert modes[0].eta_provenance['estimation_method'] == 'fallback'


def test_central_forecast_is_separate_from_conservative_scheduling_budget():
    measured = point(50, 100, 10)
    measured['seconds_p90'] = 16
    modes, _ = run_modes([profile([measured])], {'num_genes': 50, 'num_cells': 100})
    mode = modes[0]
    estimates = mode.eta_provenance['runtime_estimates']
    assert mode.eta_seconds == estimates['planning_seconds'] == 16
    assert estimates['typical_seconds'] == 10
    assert estimates['prediction_interval_calibrated'] is False


def test_cell_budget_forecast_exposes_observed_shape_and_coverage_warning():
    measured = point(100, 40, 120)
    measured['feature_vector']['observed_dimensions'] = [{'genes': 116, 'columns': 32}]
    measured['feature_vector']['validation_warnings'] = ['Native snapshot returned 32 of 40 cells.']
    prof = profile([measured])
    prof['benchmark_config']['dimension_semantics'] = {
        'genes': 'requested_gene_budget', 'cells': 'requested_cell_budget'}
    modes, warnings = run_modes([prof], {'num_genes': 100, 'num_cells': 40})
    provenance = modes[0].eta_provenance['cost_profile']
    assert any('requested cell budgets' in message for message in warnings)
    assert 'Native snapshot returned 32 of 40 cells.' in provenance['warnings']
    assert provenance['nearest_runtime_point']['cells'] == 40
    assert provenance['nearest_runtime_point']['observed_dimensions'] == [{'genes': 116, 'columns': 32}]


def test_both_forecasts_interpolate_from_their_own_measured_statistics():
    points = [point(50, 100, 10), point(50, 200, 20)]
    points[0]['seconds_p90'], points[1]['seconds_p90'] = 14, 30
    modes, _ = run_modes([profile(points)], {'num_genes': 50, 'num_cells': 150})
    estimates = modes[0].eta_provenance['runtime_estimates']
    assert estimates['typical_seconds'] == 15
    assert estimates['planning_seconds'] == 22
