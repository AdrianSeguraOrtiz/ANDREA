"""Regression cases for simulator sizes encoded in calibrated cost profiles."""

from unittest.mock import patch

import pytest

from andrea.core.commands.generate_data.cost_planner import (
    _estimate_dimensions,
    _estimate_run_modes,
)


@pytest.mark.parametrize(
    "params,genes_rule,cells_rule,expected",
    [
        ({}, {"fixed": 10}, {"fixed": 10}, (10, 10)),
        (
            {"generation": {"num_cells": 8}},
            {"fixed": 4},
            "generation.num_cells",
            (4, 8),
        ),
        (
            {"number_genes": 3, "number_sc": 2, "number_bins": 2},
            "number_genes",
            {"param": "number_sc", "multiplier_param": "number_bins"},
            (3, 4),
        ),
        (
            {"num_genes": 6, "perturbation": {"replicates_per_gene": 2}},
            "num_genes",
            {
                "param": "perturbation.replicates_per_gene",
                "multiplier_param": "num_genes",
            },
            (6, 12),
        ),
        (
            {"num_genes": 6, "time_series": {"time_points": 9}},
            "num_genes",
            {"param": "time_series.time_points", "offset": 1},
            (6, 10),
        ),
        (
            {"time_series": {"num_time_series": 2}},
            {"fixed": 10},
            {"param": "time_series.num_time_series", "multiplier": 3},
            (10, 6),
        ),
        (
            {"n": 3, "replicates": 2},
            {"fixed": 5},
            {"param": "n", "multiplier_param": "replicates", "offset": 1},
            (5, 7),
        ),
        (
            {"num_tfs": 2, "num_targets": 6, "num_hks": 2, "num_cells": 12},
            {"num_tfs": {}, "num_targets": {}, "num_hks": {}},
            "num_cells",
            (10, 12),
        ),
    ],
    ids=[
        "gnw-fixed",
        "groundgan",
        "sergio",
        "genespider-perturbation",
        "genespider-time",
        "gnw-series",
        "multiply-before-offset",
        "dyngen-components",
    ],
)
def test_estimates_dimensions_declared_by_cost_profile(
    params, genes_rule, cells_rule, expected
):
    profile = {
        "benchmark_config": {
            "dimension_profile": {"genes_param": genes_rule, "cells_param": cells_rule}
        }
    }
    assert _estimate_dimensions(params=params, selected_profile=profile)[:2] == expected


@pytest.mark.parametrize("multiplier", [None, True, "2", float("nan")])
def test_unresolved_multiplier_uses_existing_dimension_fallback(multiplier):
    profile = {
        "benchmark_config": {
            "dimension_profile": {
                "genes_param": "num_genes",
                "cells_param": {"param": "samples", "multiplier_param": "replicates"},
            }
        }
    }
    params = {"num_genes": 5, "num_cells": 12, "samples": 3, "replicates": multiplier}
    assert _estimate_dimensions(params=params, selected_profile=profile)[:2] == (5, 12)


def test_exact_fixed_size_uses_measured_eta_without_artificial_scaling():
    axes = {
        "measurement": "rna_expression",
        "resolution": "bulk",
        "column_kind": "perturbations",
        "experimental_design": "perturbational",
    }
    truth = {"contexts": ["global"]}
    profile = {
        "profile_id": "fixed_network",
        "benchmark_config": {
            "data_axes": axes,
            "truth_requirements": truth,
            "dimension_profile": {
                "genes_param": {"fixed": 10},
                "cells_param": {"fixed": 10},
                "group_count": 0,
                "population_count": 0,
            },
            "input_profile": {"requested_extras": [], "effective_extras": ["tf_list"]},
            "params_profile": {"cost_relevant_params": [], "cost_relevant_values": {}},
        },
        "runtime_points": [
            {
                "genes": 10,
                "cells": 10,
                "groups": 0,
                "threads": 1,
                "ram_gb": 8,
                "status": "ok",
                "seconds_p50": 2,
                "seconds_p90": 3,
                "repeats_total": 3,
                "repeats_ok": 3,
                "ok_rate": 1,
                "feature_vector": {"effective_extras": ["tf_list"]},
            }
        ],
    }
    spec = {
        "runtime_resources": {
            "threading": {"supported": False, "default_threads": 1, "max_threads": 1}
        }
    }
    with patch(
        "andrea.core.commands.generate_data.cost_planner._load_simulator_cost_payload",
        return_value=({"profiles": [profile]}, []),
    ):
        modes, _ = _estimate_run_modes(
            run={
                "simulator_id": "genenetweaver",
                "run_id": "test",
                "simulator_params": {},
            },
            spec=spec,
            data_axes=axes,
            truth_requirements=truth,
            requested_extras=[],
            effective_extras=["tf_list"],
            input_ids=set(),
            max_cores=1,
            max_ram_gb=8,
        )
    assert len(modes) == 1
    assert modes[0].eta_source == "cost_profile"
    assert modes[0].eta_seconds == 3
    assert modes[0].eta_provenance["cost_profile"]["size_scale"] == 1
