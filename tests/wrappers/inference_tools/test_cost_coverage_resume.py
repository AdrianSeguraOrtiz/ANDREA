"""Regression tests for safe incremental representative costs and coverage claims."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / 'wrappers/inference_tools/scripts'
sys.path.insert(0, str(SCRIPTS))
import benchmark_representative_costs as runner
import validate_tool_costs as validation
from benchmark_support import release_profile_errors


@pytest.fixture
def setup_fixture(tmp_path):
    fixtures = tmp_path / 'fixtures'
    directory = fixtures / 'one'
    directory.mkdir(parents=True)
    (directory / 'expression.tsv').write_text('gene\tcell\nG1\t1\n')
    fixture = {'fixture_key': 'one', 'genes': 100, 'columns': 200,
               'input_sha256': {'expression.tsv': runner.file_hash(directory / 'expression.tsv')}}
    config = {'sizes': [{'genes': 100, 'columns': 200}], 'threads_tested': [2], 'ram_gb_tested': [8.0],
              'repeats': 3, 'timeout_seconds': 100, 'execution_profile': {'mode': 'global'},
              'input_profile': {'column_kind': 'cells', 'notes': ['before']},
              'params_profile': {'resolved_params': {'trees': 100}}}
    provenance = {'image_id': 'sha256:' + 'a' * 64, 'cpu_model': 'fixture-cpu', 'logical_cpus': 8,
                  'host_memory_bytes': 100000, 'platform': 'fixture-os', 'docker_server': 'fixture-version',
                  'build_context_sha256': 'b' * 64}
    recipe = {'profile_id': 'representative', 'benchmark_config': config, 'fixtures': [fixture]}
    return recipe, provenance, fixtures, tmp_path / 'results'


def measured(recipe, provenance):
    config = copy.deepcopy(recipe['benchmark_config'])
    config['provenance'] = provenance
    fixture = recipe['fixtures'][0]
    samples = [{'fixture_key': fixture['fixture_key'], 'role': 'calibration', 'status': 'completed',
                'report_sha256': str(i) * 64, 'task_id': str(i), 'seconds': float(i),
                'input_sha256': fixture['input_sha256']} for i in (1, 2, 3)]
    config['calibration_evidence'] = samples
    return {'profile_id': recipe['profile_id'], 'benchmark_config': config,
            'runtime_points': [runner.make_point(fixture, config, samples)]}


def test_identical_point_is_reused_when_recipe_fingerprint_or_notes_change(setup_fixture):
    recipe, provenance, fixtures, results = setup_fixture
    old = measured(recipe, provenance)
    old['benchmark_config']['profile_sha256'] = '0' * 64
    recipe['benchmark_config']['input_profile']['notes'] = ['updated documentation']
    def forbidden(**kwargs):
        raise AssertionError('Already measured identical fixture must not run again')
    new, summary = runner.measure_profile(recipe=recipe, old=old, provenance=provenance,
                                         fixture_root=fixtures, results_dir=results, run_once=forbidden)
    assert new['runtime_points'] == old['runtime_points']
    assert summary['reused_repeats'] == 3
    assert summary['new_repeats'] == 0


def test_expanding_geometry_executes_only_new_points(setup_fixture):
    recipe, provenance, fixtures, results = setup_fixture
    old = measured(recipe, provenance)
    second = copy.deepcopy(recipe['fixtures'][0])
    second.update(fixture_key='two', genes=200)
    (fixtures / 'two').mkdir()
    (fixtures / 'two/expression.tsv').write_bytes((fixtures / 'one/expression.tsv').read_bytes())
    recipe['fixtures'].append(second)
    recipe['benchmark_config']['sizes'].append({'genes': 200, 'columns': 200})
    calls = []
    def run_once(**kwargs):
        calls.append(kwargs)
        return 'ok', 4.0, None
    new, summary = runner.measure_profile(recipe=recipe, old=old, provenance=provenance,
                                         fixture_root=fixtures, results_dir=results, run_once=run_once)
    assert len(calls) == 3
    assert len(new['runtime_points']) == 2
    assert new['runtime_points'][0] == old['runtime_points'][0]
    assert summary['reused_repeats'] == 3


def test_interruption_keeps_successful_repeat_and_resumes_only_missing(setup_fixture):
    recipe, provenance, fixtures, results = setup_fixture
    calls = []
    def interrupted(**kwargs):
        calls.append(kwargs)
        return ('ok', 1.0, None) if len(calls) == 1 else ('error', 1.0, 'failed')
    with pytest.raises(RuntimeError, match='Calibration failed'):
        runner.measure_profile(recipe=recipe, old=None, provenance=provenance,
                               fixture_root=fixtures, results_dir=results, run_once=interrupted)
    resumed = []
    def success(**kwargs):
        resumed.append(kwargs)
        return 'ok', 2.0, None
    new, summary = runner.measure_profile(recipe=recipe, old=None, provenance=provenance,
                                         fixture_root=fixtures, results_dir=results, run_once=success)
    assert len(resumed) == 2
    assert summary['reused_repeats'] == 1
    assert new['runtime_points'][0]['repeats_ok'] == 3


@pytest.mark.parametrize('change', ['image', 'params', 'resources', 'hardware'])
def test_changed_scientific_or_timing_context_is_not_reused(setup_fixture, change):
    recipe, provenance, fixtures, results = setup_fixture
    old = measured(recipe, copy.deepcopy(provenance))
    if change == 'image':
        provenance['image_id'] = 'sha256:' + 'c' * 64
    elif change == 'params':
        recipe['benchmark_config']['params_profile']['resolved_params']['trees'] = 200
    elif change == 'resources':
        recipe['benchmark_config']['threads_tested'] = [4]
    else:
        provenance['cpu_model'] = 'other-cpu'
    config = copy.deepcopy(recipe['benchmark_config'])
    config['provenance'] = provenance
    samples, _ = runner.reusable_observations(old, config, recipe['fixtures'][0])
    assert samples == []


def test_validation_evidence_cannot_be_reused(setup_fixture):
    recipe, provenance, fixtures, results = setup_fixture
    old = measured(recipe, provenance)
    old['benchmark_config']['calibration_evidence'][0]['role'] = 'validation'
    config = {**recipe['benchmark_config'], 'provenance': provenance}
    with pytest.raises(ValueError, match='Unverifiable'):
        runner.reusable_observations(old, config, recipe['fixtures'][0])


def test_changed_fixture_bytes_fail_before_execution(setup_fixture):
    recipe, provenance, fixtures, results = setup_fixture
    (fixtures / 'one/expression.tsv').write_text('changed')
    with pytest.raises(ValueError, match='fixture input'):
        runner.measure_profile(recipe=recipe, old=None, provenance=provenance,
                               fixture_root=fixtures, results_dir=results, plan_only=True)


def test_expression_only_fixture_still_provides_contract_extra_directory(setup_fixture):
    recipe, provenance, fixtures, results = setup_fixture
    calls = []
    def run_once(**kwargs):
        extra = kwargs['io_dir'] / 'extra'
        assert extra.is_dir()
        assert not list(extra.iterdir())
        calls.append(kwargs)
        return 'ok', 1.0, None
    _, summary = runner.measure_profile(recipe=recipe, old=None, provenance=provenance,
                                        fixture_root=fixtures, results_dir=results, run_once=run_once)
    assert len(calls) == 3
    assert summary['new_repeats'] == 3


def test_partial_receipts_reused_from_previous_campaign(setup_fixture):
    recipe, provenance, fixtures, results = setup_fixture
    calls = []
    def interrupted(**kwargs):
        calls.append(kwargs)
        return ('ok', 1., None) if len(calls)==1 else ('error', 1., 'failure')
    with pytest.raises(RuntimeError, match='Calibration failed'):
        runner.measure_profile(recipe=recipe, old=None, provenance=provenance,
                               fixture_root=fixtures, results_dir=results, run_once=interrupted)
    resumed = []
    def success(**kwargs):
        resumed.append(kwargs)
        return 'ok', 2., None
    profile, summary = runner.measure_profile(recipe=recipe, old=None, provenance=provenance,
        fixture_root=fixtures, results_dir=results.parent/'recovery', reuse_results_dirs=[results], run_once=success)
    assert len(resumed)==2
    assert summary['reused_repeats']==1
    assert [x['seconds'] for x in profile['benchmark_config']['calibration_evidence']]==[1.,2.,2.]


def test_modified_retained_input_receipt_is_not_reused(setup_fixture):
    recipe, provenance, fixtures, results = setup_fixture
    runner.measure_profile(recipe=recipe, old=None, provenance=provenance,
        fixture_root=fixtures, results_dir=results, run_once=lambda **kwargs: ('ok', 1., None))
    retained = next(results.rglob('repeat1/io/expression.tsv'))
    retained.write_text('corrupted input')
    calls=[]
    def success(**kwargs):
        calls.append(kwargs)
        return 'ok', 2., None
    _, summary=runner.measure_profile(recipe=recipe, old=None, provenance=provenance,
        fixture_root=fixtures, results_dir=results.parent/'recovery', reuse_results_dirs=[results], run_once=success)
    assert len(calls)==1
    assert summary['reused_repeats']==2


def test_failed_fixture_does_not_prevent_later_checkpoint(setup_fixture):
    recipe, provenance, fixtures, results = setup_fixture
    second=copy.deepcopy(recipe['fixtures'][0]);second.update(fixture_key='two',genes=200)
    (fixtures/'two').mkdir()
    (fixtures/'two/expression.tsv').write_bytes((fixtures/'one/expression.tsv').read_bytes())
    recipe['fixtures'].append(second)
    recipe['benchmark_config']['sizes'].append({'genes':200,'columns':200})
    checkpoints=[]
    def run_once(**kwargs):
        return ('error',1.,'failure') if 'one' in kwargs['io_dir'].parts else ('ok',2.,None)
    with pytest.raises(RuntimeError,match='Calibration failed at 1'):
        runner.measure_profile(recipe=recipe,old=None,provenance=provenance,fixture_root=fixtures,
            results_dir=results,run_once=run_once,on_progress=checkpoints.append)
    assert len(checkpoints)==1
    assert [p['genes'] for p in checkpoints[0]['runtime_points']]==[200]


def test_checkpoint_preserves_preexisting_points_not_yet_visited(setup_fixture):
    recipe, provenance, fixtures, results=setup_fixture
    second=copy.deepcopy(recipe['fixtures'][0]);second.update(fixture_key='two',genes=200)
    (fixtures/'two').mkdir()
    (fixtures/'two/expression.tsv').write_bytes((fixtures/'one/expression.tsv').read_bytes())
    recipe['fixtures'].append(second)
    recipe['benchmark_config']['sizes'].append({'genes':200,'columns':200})
    old,_=runner.measure_profile(recipe=recipe,old=None,provenance=provenance,
        fixture_root=fixtures,results_dir=results,run_once=lambda **kwargs:('ok',1.,None))
    checkpoints=[]
    def stop(profile):
        checkpoints.append(profile)
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        runner.measure_profile(recipe=recipe,old=old,provenance=provenance,
            fixture_root=fixtures,results_dir=results.parent/'recovery',on_progress=stop)
    assert len(checkpoints[0]['runtime_points'])==2
    assert len(checkpoints[0]['benchmark_config']['calibration_evidence'])==6
    _,summary=runner.measure_profile(recipe=recipe,old=checkpoints[0],provenance=provenance,
        fixture_root=fixtures,results_dir=results.parent/'other',plan_only=True)
    assert summary['pending_repeats']==0


def test_receipt_timing_scope_is_checked_independently_of_legacy_identity(setup_fixture):
    recipe, provenance, fixtures, results=setup_fixture
    provenance['timing_scope']='inference only, excluding startup'
    runner.measure_profile(recipe=recipe,old=None,provenance=provenance,
        fixture_root=fixtures,results_dir=results,run_once=lambda **kwargs:('ok',1.,None))
    provenance['timing_scope']='docker run wall time including container startup and output writing'
    _,summary=runner.measure_profile(recipe=recipe,old=None,provenance=provenance,
        fixture_root=fixtures,results_dir=results.parent/'other',reuse_results_dirs=[results],plan_only=True)
    assert summary['pending_repeats']==3


@pytest.mark.parametrize('tool', ['dignet','planet'])
def test_pretrained_direct_profile_has_valid_fixed_column_axis(tool):
    profile=make_coverage_profile([(40,100),(60,100),(80,100)])
    profile['benchmark_config']['params_profile']={'resolved_params':{'metacell':False}}
    report=validation.calibration_coverage({'profiles':[profile]},tool_id=tool)
    assert report['calibration_adequate_for_planned_range']
    resource=report['profiles'][0]['resources'][0]
    assert resource['model_constrained_axis_coverage']
    assert not resource['independent_axis_coverage']
    assert not validation.calibration_coverage({'profiles':[profile]},tool_id='ppcor')['calibration_adequate_for_planned_range']
    profile['benchmark_config']['params_profile']['resolved_params']['metacell']=True
    assert not validation.calibration_coverage({'profiles':[profile]},tool_id=tool)['calibration_adequate_for_planned_range']


def make_coverage_profile(sizes):
    config = {'sizes': [{'genes': g, 'columns': c} for g, c in sizes],
              'threads_tested': [2], 'ram_gb_tested': [8.0], 'repeats': 3}
    samples = [{'seconds': v} for v in (1, 2, 3)]
    return {'profile_id': 'one', 'benchmark_config': config,
            'runtime_points': [runner.make_point({'genes': g, 'columns': c}, config, samples) for g, c in sizes]}


def test_valid_diagonal_grid_is_not_independent_axis_coverage():
    profile = make_coverage_profile([(50, 20), (100, 40), (200, 80)])
    report = validation.coverage_for_profile(profile)
    assert report['declared_matrix_complete']
    assert not report['calibration_adequate_for_planned_range']
    assert report['resources'][0]['status'] == 'confounded_or_sparse_axes'
    assert len(report['resources'][0]['unmeasured_cross_combinations']) == 6


def test_rich_grid_is_reported_for_actual_resource_assignment_only():
    profile = make_coverage_profile([(g, c) for g in (50, 100, 200) for c in (100, 200, 400)])
    point = copy.deepcopy(profile['runtime_points'][0])
    point['threads'] = 4
    profile['runtime_points'].append(point)
    profile['benchmark_config']['planned_points'] = [
        {k: p[k] for k in ('genes', 'columns', 'threads', 'ram_gb')} for p in profile['runtime_points']]
    report = validation.coverage_for_profile(profile)
    assert report['calibration_adequate_for_planned_range']
    assert report['resources'][0]['calibration_adequate_for_planned_range']
    assert not report['resources'][1]['calibration_adequate_for_planned_range']
    assert report['resources'][1]['status'] == 'single_size'


def test_explicit_planned_points_do_not_require_unrequested_resource_cross_product():
    profile = make_coverage_profile([(50, 100), (100, 100)])
    profile['benchmark_config']['threads_tested'] = [2, 4]
    profile['benchmark_config']['planned_points'] = [
        {k: p[k] for k in ('genes', 'columns', 'threads', 'ram_gb')} for p in profile['runtime_points']]
    profile['benchmark_config']['profile_sha256'] = 'a' * 64
    assert release_profile_errors({'profiles': [profile]}, {'one': 'a' * 64}) == []
    profile['runtime_points'].pop()
    assert any('cover the declared matrix' in e for e in release_profile_errors({'profiles': [profile]}, {'one': 'a' * 64}))
