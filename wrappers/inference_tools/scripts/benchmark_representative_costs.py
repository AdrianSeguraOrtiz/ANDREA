"""Re-measure registered representative fixtures without regenerating random data.

Example: python benchmark_representative_costs.py --tool dignet
    --fixture-root /path/to/fixtures --results-dir /path/to/new-evidence

The fixture bundle contains input files only, in the recipe's fixture_key
directories. Every input byte is checked before Docker runs. Existing baseline
profiles are retained. A held-out validation run must use a separate directory
and must never be imported as calibration evidence.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'wrappers'))
from benchmark_support import atomic_json, collect_provenance, merge_profiles
from benchmark_costs import percentile, run_container_once
from shared.representative_profiles import load_representative_profiles, representative_fingerprint


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_fixture(directory: Path, expected: dict[str, str]) -> None:
    for name, digest in expected.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or file_hash(path) != digest:
            raise ValueError(f'Missing, changed or unsafe fixture input: {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', required=True)
    parser.add_argument('--fixture-root', type=Path, required=True)
    parser.add_argument('--results-dir', type=Path, required=True)
    args = parser.parse_args()
    recipes = load_representative_profiles(args.tool)
    if not recipes:
        parser.error('No registered representative fixtures for this tool.')
    args.results_dir.mkdir(parents=True, exist_ok=False)
    spec_path = ROOT / 'andrea/catalog_inference_tools/tools' / args.tool / 'toolspec.json'
    spec = json.loads(spec_path.read_text())
    provenance = collect_provenance(root=ROOT, spec_path=spec_path, image=spec['docker_image'], seed=1729)
    measured = []
    for recipe in recipes:
        config = copy.deepcopy(recipe['benchmark_config'])
        config.update(provenance=provenance, profile_sha256=representative_fingerprint(recipe), calibration_evidence=[])
        points = []
        for fixture in recipe['fixtures']:
            source = args.fixture_root / fixture['fixture_key']
            verify_fixture(source, fixture['input_sha256'])
            seconds = []
            for repeat in range(config['repeats']):
                work = args.results_dir / recipe['profile_id'] / fixture['fixture_key'] / f'repeat{repeat + 1}'
                io = work / 'io'
                (io / 'out').mkdir(parents=True)
                for name in fixture['input_sha256']:
                    target = io / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((source / name).read_bytes())
                status, elapsed, error = run_container_once(
                    image_tag=provenance['image_id'], io_dir=io,
                    threads=config['threads_tested'][0], ram_gb=config['ram_gb_tested'][0],
                    timeout_s=config['timeout_seconds'])
                receipt = {'status': status, 'seconds': elapsed, 'error': error,
                           'image_id': provenance['image_id'], 'input_sha256': fixture['input_sha256']}
                report_path = work / 'measurement.json'
                atomic_json(report_path, receipt)
                if status != 'ok':
                    raise RuntimeError(f'Calibration failed; evidence retained and costs unchanged: {report_path}')
                seconds.append(elapsed)
                config['calibration_evidence'].append({
                    'fixture_key': fixture['fixture_key'], 'role': 'calibration', 'status': 'completed',
                    'report_path': str(report_path.resolve()), 'report_sha256': file_hash(report_path),
                    'task_id': f'repeat{repeat + 1}', 'seconds': elapsed,
                    'input_sha256': fixture['input_sha256']})
            points.append({
                'genes': fixture['genes'], 'columns': fixture['columns'],
                'threads': config['threads_tested'][0], 'ram_gb': config['ram_gb_tested'][0],
                'status': 'ok', 'repeats_total': len(seconds), 'repeats_ok': len(seconds),
                'repeats_failed': 0, 'ok_rate': 1.0,
                'failure_breakdown': {'oom': 0, 'timeout': 0, 'error': 0},
                'seconds_p50': percentile(seconds, 50), 'seconds_p90': percentile(seconds, 90)})
        measured.append({'profile_id': recipe['profile_id'], 'benchmark_config': config, 'runtime_points': points})
    target = spec_path.with_name('cost.json')
    atomic_json(target, merge_profiles(target, {'schema_version': '1.0', 'profiles': measured}))


if __name__ == '__main__':
    main()
