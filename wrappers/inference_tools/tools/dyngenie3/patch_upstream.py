"""Apply reviewed compatibility and deterministic-seeding patches to the pinned file.

No source is vendored: Docker downloads the official immutable source and checks
its hash before applying these exact replacements. Scientific transformations,
tree hyperparameters, feature importances and target normalization are unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

UPSTREAM_COMMIT = "27eddd960d50e82791a8e0e5f8fcf6d679fe3eac"
UPSTREAM_SHA256 = "8108e4e8d15809e88ca1eb3c51dd2b260303b00b108e20822f0af84131855e8f"
UPSTREAM_URL = f"https://raw.githubusercontent.com/vahuynh/dynGENIE3/{UPSTREAM_COMMIT}/dynGENIE3_python/dynGENIE3.py"


def patched_source(raw: bytes) -> str:
    if hashlib.sha256(raw).hexdigest() != UPSTREAM_SHA256:
        raise ValueError(
            "Unexpected dynGENIE3 source; patches require the verified upstream SHA-256"
        )
    text = raw.decode("utf-8")

    def replace(old: str, new: str, count: int = 1):
        nonlocal text
        if text.count(old) != count:
            raise ValueError(f"Patch context changed: {old!r}")
        text = text.replace(old, new)

    replace(
        "from sklearn.tree import BaseDecisionTree",
        "from sklearn.tree._classes import BaseDecisionTree\nfrom scipy.stats import pearsonr",
    )
    replace("np.pearsonr(", "_andrea_pearsonr(", 2)
    replace('max_features = "auto"', "max_features = 1.0")
    replace(
        "save_models=False,nthreads=1):",
        "save_models=False,nthreads=1,random_state=None):",
    )
    replace(
        "compute_quality_scores,save_models):\n",
        "compute_quality_scores,save_models,random_state=None):\n",
    )
    replace(
        "if alpha != 'from_data':",
        "if not (isinstance(alpha, str) and alpha == 'from_data'):",
    )
    replace(
        "if alpha == 'from_data':",
        "if isinstance(alpha, str) and alpha == 'from_data':",
    )
    replace(
        "input_data = [[TS_data,time_points,SS_data,i,alphas[i],input_idx,tree_method,K,ntrees,compute_quality_scores,save_models] for i in range(ngenes)]",
        "input_data = [[TS_data,time_points,SS_data,i,alphas[i],input_idx,tree_method,K,ntrees,compute_quality_scores,save_models,_andrea_target_seed(random_state,i)] for i in range(ngenes)]",
    )
    replace(
        "        pool = Pool(nthreads)\n        alloutput = pool.map(wr_dynGENIE3_single, input_data)",
        "        with Pool(nthreads) as pool:\n            alloutput = pool.map(wr_dynGENIE3_single, input_data)",
    )
    replace(
        "dynGENIE3_single(TS_data,time_points,SS_data,i,alphas[i],input_idx,tree_method,K,ntrees,compute_quality_scores,save_models)",
        "dynGENIE3_single(TS_data,time_points,SS_data,i,alphas[i],input_idx,tree_method,K,ntrees,compute_quality_scores,save_models,_andrea_target_seed(random_state,i))",
    )
    replace(
        "max_features=max_features,oob_score=oob_score)",
        "max_features=max_features,oob_score=oob_score,random_state=random_state,n_jobs=1)",
        2,
    )
    replace("args[8], args[9], args[10])", "args[8], args[9], args[10], args[11])")
    replace("    h = 1 # lag", "    _andrea_record_resources()\n    h = 1 # lag")
    replace(
        "np.random.uniform(low=1e-12,high=1e-11,size=importances_by_tree.shape)",
        "(np.random if random_state is None else np.random.RandomState(random_state)).uniform(low=1e-12,high=1e-11,size=importances_by_tree.shape)",
    )
    text += '''\n\ndef _andrea_target_seed(seed, target_index):
    """Stable across worker schedules; None preserves upstream unseeded behavior."""
    if seed is None:
        return None
    return int(np.random.SeedSequence([int(seed), int(target_index)]).generate_state(1)[0])


def _andrea_pearsonr(prediction, observed):
    """An insufficient sample has no correlation; retain the inferred network."""
    if len(prediction) < 2 or len(observed) < 2:
        return float('nan'), float('nan')
    return pearsonr(prediction, observed)


def _andrea_record_resources():
    """Retain actual native pool observations for each target worker."""
    import json
    import os
    from pathlib import Path
    from threadpoolctl import threadpool_info
    directory = os.environ.get('ANDREA_RESOURCE_REPORT_DIR')
    if not directory:
        return
    path = Path(directory) / ('resource-worker-%d.json' % os.getpid())
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'pid': os.getpid(), 'native_pools': threadpool_info(),
        'cgroups': _andrea_cgroups(),
        'cpu_affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None}, indent=2) + '\\n')
    temporary.replace(path)


def _andrea_cgroups(root='/sys/fs/cgroup'):
    """Read the effective container limits; retain raw values, never infer quotas from affinity."""
    from pathlib import Path
    root = Path(root)
    try:
        if (root / 'cpu.max').is_file() and (root / 'memory.max').is_file():
            version = 2
            cpu_path, memory_path = root / 'cpu.max', root / 'memory.max'
            cpu_raw, memory_raw = cpu_path.read_text().strip(), memory_path.read_text().strip()
            quota_text, period_text = cpu_raw.split()
            quota, period = None if quota_text == 'max' else int(quota_text), int(period_text)
            memory = None if memory_raw == 'max' else int(memory_raw)
        else:
            version = 1
            cpu_dir = next((path for path in (root / 'cpu', root / 'cpu,cpuacct', root)
                            if (path / 'cpu.cfs_quota_us').is_file() and (path / 'cpu.cfs_period_us').is_file()), None)
            memory_path = next((path for path in (root / 'memory/memory.limit_in_bytes', root / 'memory.limit_in_bytes') if path.is_file()), None)
            if cpu_dir is None or memory_path is None:
                return {'version': None, 'error': 'Effective CPU and memory cgroup limits are unavailable'}
            cpu_path = cpu_dir / 'cpu.cfs_quota_us'
            quota_text = cpu_path.read_text().strip()
            period_text = (cpu_dir / 'cpu.cfs_period_us').read_text().strip()
            cpu_raw, memory_raw = quota_text + ' ' + period_text, memory_path.read_text().strip()
            quota, period = int(quota_text), int(period_text)
            quota = None if quota < 0 else quota
            memory = int(memory_raw)
        if period <= 0 or quota is not None and quota <= 0 or memory is not None and memory <= 0:
            raise ValueError('Cgroup limits must be positive or explicitly unlimited')
        return {'version': version,
                'cpu': {'path': str(cpu_path), 'raw': cpu_raw, 'quota_us': quota, 'period_us': period,
                        'limit_cpus': None if quota is None else quota / period},
                'memory': {'path': str(memory_path), 'raw': memory_raw, 'limit_bytes': memory}}
    except (OSError, ValueError) as error:
        return {'version': None, 'error': str(error)}
'''
    compile(text, "dynGENIE3.py", "exec")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(patched_source(args.source.read_bytes()), encoding="utf-8")


if __name__ == "__main__":
    main()
