import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('representative_profiles_under_test',
    ROOT / 'wrappers/inference_tools/scripts/shared/representative_profiles.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_validation_observations_cannot_be_imported_as_calibration():
    fixture = {'fixture_key': 'one', 'input_sha256': {'expression.tsv': 'a' * 64}}
    recipe = {'profile_id': 'new', 'benchmark_config': {'repeats': 3}, 'fixtures': [fixture]}
    samples = [{**fixture, 'report_sha256': str(i) * 64, 'task_id': 'x',
                'role': 'validation', 'status': 'completed'} for i in [1, 2, 3]]
    payload = {'profiles': [{'profile_id': 'new', 'benchmark_config': {
        'repeats': 3, 'calibration_evidence': samples}}]}
    assert any('invalid calibration role' in e for e in module.representative_profile_errors(payload, [recipe]))


def test_repeated_reference_is_not_three_measurements():
    fixture = {'fixture_key': 'one', 'input_sha256': {'expression.tsv': 'a' * 64}}
    recipe = {'profile_id': 'new', 'benchmark_config': {'repeats': 3}, 'fixtures': [fixture]}
    sample = {**fixture, 'report_sha256': 'b' * 64, 'task_id': 'x', 'role': 'calibration', 'status': 'completed'}
    payload = {'profiles': [{'profile_id': 'new', 'benchmark_config': {
        'repeats': 3, 'calibration_evidence': [sample] * 3}}]}
    assert any('duplicate' in e for e in module.representative_profile_errors(payload, [recipe]))
