"""Registered, checksum-pinned fixtures for supplementary cost measurements.

These profiles complement the small synthetic matrices. Keeping a separate
recipe preserves the fingerprints and measurements of those existing matrices.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


DEFAULT_REPRESENTATIVE_DIR = Path(__file__).resolve().parents[2] / 'cost_profiles' / 'representative'


def load_representative_profiles(tool_id: str, directory: Path = DEFAULT_REPRESENTATIVE_DIR) -> list[dict]:
    path = directory / f'{tool_id}.json'
    if not path.exists():
        return []
    profiles = json.loads(path.read_text())['profiles']
    ids = [p['profile_id'] for p in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError(f'Duplicate representative profile for {tool_id}')
    return profiles


def representative_fingerprint(recipe: dict) -> str:
    scripts = Path(__file__).resolve().parents[1]
    files = [Path(__file__), scripts / 'benchmark_representative_costs.py']
    payload = {'recipe': recipe, 'implementation': {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def representative_profile_errors(payload: dict, recipes: list[dict]) -> list[str]:
    errors = []
    expected = {r['profile_id']: r for r in recipes}
    for profile in payload.get('profiles', []):
        recipe = expected.get(profile.get('profile_id'))
        if recipe is None:
            continue
        config = profile.get('benchmark_config', {})
        for key, value in recipe['benchmark_config'].items():
            if config.get(key) != value:
                errors.append(f"{profile['profile_id']}: measured {key} differs from the registered fixture recipe.")
        evidence = config.get('calibration_evidence', [])
        seen = set()
        for point in recipe['fixtures']:
            samples = [e for e in evidence if e.get('fixture_key') == point['fixture_key']]
            if len(samples) != config.get('repeats'):
                errors.append(f"{profile['profile_id']}: incomplete calibration evidence for {point['fixture_key']}.")
            for sample in samples:
                identity = (sample.get('report_sha256'), sample.get('task_id'))
                if identity in seen or not all(identity):
                    errors.append(f"{profile['profile_id']}: duplicate or unidentified calibration observation.")
                seen.add(identity)
                if sample.get('input_sha256') != point['input_sha256']:
                    errors.append(f"{profile['profile_id']}: calibration input hashes differ from the registered fixture.")
                if sample.get('role') != 'calibration' or sample.get('status') != 'completed':
                    errors.append(f"{profile['profile_id']}: invalid calibration role/outcome.")
    return errors
