"""Incrementally measure checksum-pinned representative calibration fixtures.

Successful observations are reused only when their input bytes, immutable image,
parameters, execution mode, resources and hardware agree. Expanding a recipe or
editing the orchestration script does not invalidate identical measurements.
The results directory is resumable; completed receipts survive interruption.
Validation observations must never be imported as calibration evidence.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'wrappers'))
from benchmark_support import atomic_json, collect_provenance, merge_profiles
from benchmark_costs import acquire_cost_lock, percentile, run_container_once
from shared.representative_profiles import (
    DEFAULT_REPRESENTATIVE_DIR, load_representative_profiles, representative_fingerprint,
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_fixture(directory: Path, expected: dict[str, str]) -> None:
    for name, digest in expected.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or file_hash(path) != digest:
            raise ValueError(f'Missing, changed or unsafe fixture input: {name}')


def timing_scope(provenance: dict) -> str | None:
    value = provenance.get('timing_scope')
    known_container_wall = {
        'docker run wall time including container startup and output writing',
        'Physical container launch through exit collection; measurements made with frozen ANDREA 0.2.0rc1 runtime and unchanged Docker image. Imported by the current source snapshot; host evaluation/postprocessing excluded.',
    }
    return 'container_launch_to_exit_wall' if value in known_container_wall else value


def measurement_context(config: dict) -> dict:
    """Scientific/timing identity; recipe bookkeeping is deliberately excluded."""
    provenance = config.get('provenance', {})
    inputs = {key: value for key, value in config.get('input_profile', {}).items() if key != 'notes'}
    return {
        'execution': config.get('execution_profile'),
        'params': config.get('params_profile', {}).get('resolved_params'),
        'inputs': inputs,
        'timing_scope': timing_scope(provenance),
        'environment': {key: provenance.get(key) for key in (
            'image_id', 'build_context_sha256', 'cpu_model', 'logical_cpus',
            'host_memory_bytes', 'platform', 'docker_server',
        )},
    }


def fixture_identity(config: dict, fixture: dict, threads: int, ram_gb: float) -> str:
    context = measurement_context(config)
    # Preserve the original on-disk identity format. Timing scope is checked
    # explicitly against the receipt's provenance before reusing any sample.
    context.pop('timing_scope')
    payload = {'context': context, 'input_sha256': fixture['input_sha256'],
               'genes': fixture['genes'], 'columns': fixture['columns'],
               'threads': threads, 'ram_gb': float(ram_gb)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def point_key(point: dict) -> tuple:
    return point['genes'], point['columns'], point['threads'], float(point['ram_gb'])


def valid_seconds(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def reusable_observations(old: dict | None, config: dict, fixture: dict) -> tuple[list[dict], dict | None]:
    """Import measured observations, never substitute a percentile for a repeat."""
    if old is None or measurement_context(old.get('benchmark_config', {})) != measurement_context(config):
        return [], None
    old_config = old['benchmark_config']
    # Legacy evidence has no resources per observation, so its profile must be
    # unambiguous. Newly generated representative profiles retain this contract.
    if (old_config.get('threads_tested') != config['threads_tested'] or
            old_config.get('ram_gb_tested') != config['ram_gb_tested']):
        return [], None
    key = fixture['genes'], fixture['columns'], config['threads_tested'][0], float(config['ram_gb_tested'][0])
    points = [p for p in old.get('runtime_points', []) if point_key(p) == key]
    if len(points) > 1:
        raise ValueError(f'Duplicate measured point for {fixture["fixture_key"]}')
    samples, seen = [], set()
    for sample in old_config.get('calibration_evidence', []):
        if sample.get('fixture_key') != fixture['fixture_key']:
            continue
        identity = sample.get('report_sha256'), sample.get('task_id')
        if (sample.get('role') != 'calibration' or sample.get('status') != 'completed'
                or sample.get('input_sha256') != fixture['input_sha256']
                or not all(identity) or identity in seen or not valid_seconds(sample.get('seconds'))):
            raise ValueError(f'Unverifiable calibration evidence for {fixture["fixture_key"]}')
        seen.add(identity)
        report = sample.get('report_path')
        if report and Path(report).exists() and file_hash(Path(report)) != sample['report_sha256']:
            raise ValueError(f'Changed retained measurement receipt: {report}')
        samples.append(copy.deepcopy(sample))
    point = points[0] if points else None
    if point is not None and samples:
        seconds = [sample['seconds'] for sample in samples]
        if (point.get('repeats_ok') != len(samples) or point.get('status') != 'ok'
                or not math.isclose(point.get('seconds_p50', -1), percentile(seconds, 50), rel_tol=1e-6, abs_tol=1e-6)
                or not math.isclose(point.get('seconds_p90', -1), percentile(seconds, 90), rel_tol=1e-6, abs_tol=1e-6)):
            raise ValueError(f'Cost summary disagrees with its observations: {fixture["fixture_key"]}')
    return samples, point


def sample_from_receipt(path: Path, fixture: dict, identity: str,
                        expected_scope: str | None = None) -> dict | None:
    if not path.is_file():
        return None
    receipt = json.loads(path.read_text())
    if (receipt.get('measurement_identity') != identity or receipt.get('status') != 'ok'
            or receipt.get('input_sha256') != fixture['input_sha256']
            or receipt.get('role') != 'calibration' or not valid_seconds(receipt.get('seconds'))):
        return None
    if timing_scope(receipt.get('provenance', {})) != expected_scope:
        return None
    # Reuse must be backed by the actual retained inputs, not only hashes claimed
    # by the receipt. Changed/deleted evidence must be measured again.
    try:
        verify_fixture(path.parent / 'io', fixture['input_sha256'])
    except ValueError:
        return None
    return {'fixture_key': fixture['fixture_key'], 'role': 'calibration', 'status': 'completed',
            'report_path': str(path.resolve()), 'report_sha256': file_hash(path),
            'task_id': receipt['task_id'], 'seconds': receipt['seconds'],
            'input_sha256': fixture['input_sha256']}


def make_point(fixture: dict, config: dict, samples: list[dict]) -> dict:
    seconds = [sample['seconds'] for sample in samples]
    return {'genes': fixture['genes'], 'columns': fixture['columns'],
            'threads': config['threads_tested'][0], 'ram_gb': config['ram_gb_tested'][0],
            'status': 'ok', 'repeats_total': len(seconds), 'repeats_ok': len(seconds),
            'repeats_failed': 0, 'ok_rate': 1.0,
            'failure_breakdown': {'oom': 0, 'timeout': 0, 'error': 0},
            'seconds_p50': percentile(seconds, 50), 'seconds_p90': percentile(seconds, 90)}


def measure_profile(*, recipe: dict, old: dict | None, provenance: dict, fixture_root: Path,
                    results_dir: Path, plan_only: bool = False, run_once=None,
                    reuse_results_dirs: Sequence[Path] = (), on_progress=None) -> tuple[dict, dict]:
    run_once = run_once or run_container_once
    config = copy.deepcopy(recipe['benchmark_config'])
    config.update(provenance=provenance, profile_sha256=representative_fingerprint(recipe), calibration_evidence=[])
    if len(config['threads_tested']) != 1 or len(config['ram_gb_tested']) != 1:
        raise ValueError('Representative fixtures require one explicit resource assignment per profile.')
    if config['repeats'] < 3:
        raise ValueError('Representative calibration requires at least three repeats.')
    threads, ram = config['threads_tested'][0], config['ram_gb_tested'][0]
    old_keys = {point_key(p) for p in (old or {}).get('runtime_points', [])}
    requested = {(f['genes'], f['columns'], threads, float(ram)) for f in recipe['fixtures']}
    if len(requested) != len(recipe['fixtures']):
        raise ValueError('A representative profile must have one fixture per size/resource point.')
    if old_keys - requested:
        raise ValueError('The expanded recipe would discard historical points; keep their fixtures in the recipe.')
    points = []
    failures = []
    old_fixtures = {}
    retained_points, retained_samples = {}, {}
    for fixture in recipe['fixtures']:
        verify_fixture(fixture_root / fixture['fixture_key'], fixture['input_sha256'])
        samples, point = reusable_observations(old, config, fixture)
        old_fixtures[fixture['fixture_key']] = samples, point
        if len(samples) == config['repeats'] and point is not None:
            retained_points[fixture['fixture_key']] = copy.deepcopy(point)
            retained_samples[fixture['fixture_key']] = copy.deepcopy(samples)
    summary = {'profile_id': recipe['profile_id'], 'planned_repeats': 0, 'reused_repeats': 0,
               'new_repeats': 0, 'pending_repeats': 0, 'reused_points': 0, 'new_points': 0}
    for fixture in recipe['fixtures']:
        source = fixture_root / fixture['fixture_key']
        verify_fixture(source, fixture['input_sha256'])
        samples, original_point = copy.deepcopy(old_fixtures[fixture['fixture_key']])
        if len(samples) > config['repeats']:
            raise ValueError('Recipe repeats cannot discard existing calibration observations.')
        originally_complete = len(samples) == config['repeats'] and original_point is not None
        summary['planned_repeats'] += config['repeats']
        summary['reused_repeats'] += len(samples)
        identity = fixture_identity(config, fixture, threads, ram)
        for repeat in range(len(samples), config['repeats']):
            work = results_dir / recipe['profile_id'] / fixture['fixture_key'] / identity / f'repeat{repeat + 1}'
            receipt_path = work / 'measurement.json'
            sample = sample_from_receipt(receipt_path, fixture, identity, timing_scope(provenance))
            if sample is None:
                relative = Path(recipe['profile_id']) / fixture['fixture_key'] / identity / f'repeat{repeat + 1}' / 'measurement.json'
                for previous in reuse_results_dirs:
                    # Accept either a representative phase root or one tool root.
                    for base in (previous, previous / provenance.get('tool_id', results_dir.name)):
                        sample = sample_from_receipt(base / relative, fixture, identity, timing_scope(provenance))
                        if sample is not None:
                            break
                    if sample is not None:
                        break
            if sample is not None:
                samples.append(sample)
                summary['reused_repeats'] += 1
                continue
            if plan_only:
                summary['pending_repeats'] += 1
                continue
            if receipt_path.exists():
                attempt = 1
                while receipt_path.with_name(f'measurement.failed-{attempt}.json').exists():
                    attempt += 1
                receipt_path.rename(receipt_path.with_name(f'measurement.failed-{attempt}.json'))
                if (work / 'io').exists():
                    (work / 'io').rename(work / f'io.failed-{attempt}')
            io = work / 'io'
            if (io / 'out').exists():
                shutil.rmtree(io / 'out')
            (io / 'out').mkdir(parents=True, exist_ok=True)
            # The inference container contract always mounts/passes /io/extra,
            # including expression-only fixtures with no supplementary files.
            (io / 'extra').mkdir(parents=True, exist_ok=True)
            for name in fixture['input_sha256']:
                target = io / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((source / name).read_bytes())
            print(f'[{recipe["profile_id"]}/{fixture["fixture_key"]}] repeat {repeat + 1}/{config["repeats"]}', flush=True)
            status, elapsed, error = run_once(image_tag=provenance['image_id'], io_dir=io,
                                            threads=threads, ram_gb=ram, timeout_s=config['timeout_seconds'])
            receipt = {'status': status, 'seconds': elapsed, 'error': error, 'role': 'calibration',
                       'task_id': f'{identity}/repeat{repeat + 1}', 'measurement_identity': identity,
                       'image_id': provenance['image_id'], 'input_sha256': fixture['input_sha256'],
                       'provenance': provenance, 'threads': threads, 'ram_gb': ram}
            atomic_json(receipt_path, receipt)
            if status != 'ok' or not valid_seconds(elapsed):
                failures.append(str(receipt_path))
                print(f'Calibration point failed; evidence retained: {receipt_path}', flush=True)
                break
            samples.append(sample_from_receipt(receipt_path, fixture, identity, timing_scope(provenance)))
            summary['new_repeats'] += 1
        config['calibration_evidence'].extend(samples)
        if len(samples) == config['repeats']:
            points.append(copy.deepcopy(original_point) if originally_complete else make_point(fixture, config, samples))
            summary['reused_points' if originally_complete else 'new_points'] += 1
            if on_progress is not None and not plan_only:
                retained_points[fixture['fixture_key']] = copy.deepcopy(points[-1])
                retained_samples[fixture['fixture_key']] = copy.deepcopy(samples)
                checkpoint_config = copy.deepcopy(config)
                checkpoint_config['calibration_evidence'] = [sample
                    for f in recipe['fixtures'] for sample in retained_samples.get(f['fixture_key'], [])]
                on_progress({'profile_id': recipe['profile_id'], 'benchmark_config': checkpoint_config,
                             'runtime_points': [retained_points[f['fixture_key']]
                                for f in recipe['fixtures'] if f['fixture_key'] in retained_points]})
    if failures:
        summary['failed_fixtures'] = failures
        atomic_json(results_dir / recipe['profile_id'] / 'summary.json', summary)
        raise RuntimeError(f'Calibration failed at {len(failures)} fixture(s); completed points checkpointed; evidence: {failures}')
    return {'profile_id': recipe['profile_id'], 'benchmark_config': config, 'runtime_points': points}, summary


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', required=True)
    parser.add_argument('--fixture-root', type=Path, required=True)
    parser.add_argument('--results-dir', type=Path, required=True)
    parser.add_argument('--reuse-results-dir', type=Path, action='append', default=[],
                        help='Reuse verified successful receipts from a previous representative phase/tool directory.')
    parser.add_argument('--recipes-dir', type=Path, default=DEFAULT_REPRESENTATIVE_DIR)
    parser.add_argument('--profile', action='append', default=[])
    parser.add_argument('--plan-only', action='store_true', help='Verify inputs and report missing observations without executing or changing costs.')
    args = parser.parse_args(argv)
    recipes = load_representative_profiles(args.tool, args.recipes_dir)
    if args.profile:
        unknown = set(args.profile) - {r['profile_id'] for r in recipes}
        if unknown:
            parser.error(f'Unknown profiles: {sorted(unknown)}')
        recipes = [r for r in recipes if r['profile_id'] in args.profile]
    if not recipes:
        parser.error('No registered representative fixtures for this tool.')
    args.results_dir.mkdir(parents=True, exist_ok=True)
    lock = (args.results_dir / f'{args.tool}.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('Another representative calibration is using this results directory.')
    spec_path = ROOT / 'andrea/catalog_inference_tools/tools' / args.tool / 'toolspec.json'
    spec = json.loads(spec_path.read_text())
    target = spec_path.with_name('cost.json')
    cost_lock = acquire_cost_lock(target) if not args.plan_only else None
    old_payload = json.loads(target.read_text()) if target.exists() else {'profiles': []}
    snapshot = args.results_dir / f'{args.tool}-cost-before-{file_hash(target) if target.exists() else "none"}.json'
    if not snapshot.exists():
        atomic_json(snapshot, old_payload)
    provenance = collect_provenance(root=ROOT, spec_path=spec_path, image=spec['docker_image'], seed=1729)
    atomic_json(args.results_dir / f'{args.tool}-provenance.json', provenance)
    existing = {p['profile_id']: p for p in old_payload['profiles']}
    summaries = []
    errors = []
    for recipe in recipes:
        def checkpoint(profile):
            atomic_json(target, merge_profiles(target, {'schema_version': '1.0', 'profiles': [profile]}))
        try:
            measured, summary = measure_profile(recipe=recipe, old=existing.get(recipe['profile_id']), provenance=provenance,
                                                fixture_root=args.fixture_root, results_dir=args.results_dir, plan_only=args.plan_only,
                                                reuse_results_dirs=args.reuse_results_dir, on_progress=checkpoint)
        except RuntimeError as exc:
            errors.append({'profile_id': recipe['profile_id'], 'error': str(exc)})
            print(str(exc), file=sys.stderr, flush=True)
            continue
        summaries.append(summary)
        if not args.plan_only:
            atomic_json(target, merge_profiles(target, {'schema_version': '1.0', 'profiles': [measured]}))
        print(json.dumps(summary, sort_keys=True), flush=True)
        atomic_json(args.results_dir / f'{args.tool}-summary.json', {'plan_only': args.plan_only, 'profiles': summaries, 'errors': errors})
    atomic_json(args.results_dir / f'{args.tool}-summary.json', {'plan_only': args.plan_only, 'profiles': summaries, 'errors': errors})
    if cost_lock is not None:
        cost_lock.close()
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
