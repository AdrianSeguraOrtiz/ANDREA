"""Regression checks for the unsupported transfers exposed by the full pilot."""
from dataclasses import replace

import pytest

from andrea.core.commands.infer_network.commons.planner import (
    _measured_size_scale, _select_cost_profile,
)
from andrea.core.commands.infer_network.commons.resource_decisions import planned_task_resource_decision
from andrea.core.commands.infer_network.commons.shared import ToolPlanItem


def point(genes, columns, seconds):
    return dict(genes=genes, columns=columns, threads=2, ram_gb=8.0,
                seconds_p50=seconds, seconds_p90=seconds, status='ok',
                repeats_total=3, repeats_ok=3, ok_rate=1.0)


def profile(name, points, ensemble=30, steps=1000):
    return {'profile_id': name, 'runtime_points': points, 'benchmark_config': {
        'execution_profile': {'mode': 'global', 'group_count': 0},
        'input_profile': {}, 'params_profile': {
            'cost_relevant_params': ['ensemble', 'diffusion_timesteps'],
            'cost_relevant_values': {'ensemble': ensemble, 'diffusion_timesteps': steps}}}}


def select(profiles):
    return _select_cost_profile(tool_id='dignet', toolspec={'extra_inputs': {}},
        cost_payload={'profiles': profiles}, execution_mode='global', extras_present=set(),
        resolved_params={'ensemble': 30, 'diffusion_timesteps': 1000}, logical_group_count=0,
        genes=80, columns=100, max_cores=2, requested_threads=2, requested_ram_gb=8)


def test_full_diffusion_never_claims_calibration_from_fast_profile():
    selected, match, warnings = select([profile('fast', [point(100, 100, 53)], 3, 25)])
    assert selected is None
    assert match['param_difference_count'] == 2
    assert any('Cross-parameter scaling is unsupported' in w for w in warnings)


def test_random_seed_does_not_require_a_separate_cost_calibration():
    p = profile('full', [point(80, 100, 5000)])
    pp = p['benchmark_config']['params_profile']
    pp['cost_relevant_params'].append('seed')
    pp['cost_relevant_values']['seed'] = 123
    selected, match, _ = select([p])
    assert selected is not None
    assert match['ignored_randomness_params'] == ['seed']


def test_exact_representative_point_wins_over_first_profile_with_same_parameters():
    selected, _, _ = select([profile('small', [point(10, 10, 4)]),
                             profile('representative', [point(80, 100, 5000)])])
    assert selected['profile_id'] == 'representative'


def test_interpolation_respects_measured_cell_effect_not_sqrt_area():
    points = [point(100, 100, 10), point(100, 400, 160)]
    scale, model = _measured_size_scale(points=points, nearest=points[0],
        genes=100, columns=200, threads=2, ram_gb=8)
    assert scale == pytest.approx(4)
    assert model['calibrated'] is True
    assert model['method'] == 'measured_axis_interpolation'


def test_diagonal_grid_does_not_establish_independent_gene_or_cell_scaling():
    points = [point(50, 20, 1), point(100, 40, 4), point(200, 80, 16)]
    _, model = _measured_size_scale(points=points, nearest=points[1],
        genes=100, columns=200, threads=2, ram_gb=8)
    assert model['method'] == 'uncalibrated_size_heuristic'
    assert model['calibrated'] is False


def test_same_aspect_ratio_has_identifiable_joint_scaling():
    points = [point(50, 20, 1), point(200, 80, 16)]
    scale, model = _measured_size_scale(points=points, nearest=points[0],
        genes=100, columns=40, threads=2, ram_gb=8)
    assert scale == pytest.approx(4)
    assert model['calibrated'] is True


def test_extrapolation_and_resource_mismatch_remain_explicitly_uncalibrated():
    points = [point(100, 100, 10), point(100, 200, 20)]
    _, model = _measured_size_scale(points=points, nearest=points[1],
        genes=100, columns=400, threads=2, ram_gb=8)
    assert model['method'] == 'measured_axis_extrapolation'
    assert model['calibrated'] is False
    _, model = _measured_size_scale(points=points, nearest=points[0],
        genes=100, columns=100, threads=4, ram_gb=8)
    assert model['calibrated'] is False


def test_extrapolation_cannot_certify_timeout_feasibility_or_exclude_a_tool():
    task = ToolPlanItem(tool_id='x', run_id='x', image='x', threads=2, ram_gb=8,
                       eta_seconds=100, eta_source='cost_profile_extrapolated', output_dir='x', timeout_seconds=1000)
    decision = planned_task_resource_decision(task)
    assert decision['reason'] == 'runtime_unknown_without_matching_calibration'
    assert decision['evidence'] == 'uncalibrated_extrapolation'
    assert planned_task_resource_decision(replace(task, timeout_seconds=10))['status'] == 'eligible'
