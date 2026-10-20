"""Inspect effective native pools through the real Docker entrypoint.

The diagnostic replaces only the application script with a read-only probe,
loads the original wrapper from the image, and never performs scientific fits.
No packages are installed. Test both unrestricted and pinned CPU affinity.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from build_tool_images import DEFAULT_TEMPLATE_MAP, load_json, validate_template_map

ROOT = Path(__file__).resolve().parents[3]
ENV_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")

PYTHON_PROBE = r'''
import ctypes, importlib.util, json, os, sys

def pools():
    found = []
    paths = {line.split()[-1] for line in open('/proc/self/maps') if '/' in line}
    for path in sorted(paths):
        base = os.path.basename(path)
        if 'openblas' in base:
            symbols = ('openblas_get_num_threads64_', 'openblas_get_num_threads',
                       'scipy_openblas_get_num_threads', 'scipy_openblas_get_num_threads64_')
        elif any(name in base for name in ('libgomp', 'libomp', 'libiomp')):
            symbols = ('omp_get_max_threads',)
        else:
            continue
        lib = ctypes.CDLL(path)
        for symbol in symbols:
            try:
                getter = getattr(lib, symbol)
            except AttributeError:
                continue
            getter.restype = ctypes.c_int
            found.append({'library': base, 'num_threads': getter()})
            break
        else:
            raise RuntimeError('Uninspectable native pool: ' + path)
    return found

def main():
    threads = int(sys.argv[sys.argv.index('--threads') + 1])
    tool = os.environ['ANDREA_AUDIT_TOOL']
    spec = importlib.util.spec_from_file_location('original_wrapper', '/probe/original.py')
    wrapper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = wrapper
    spec.loader.exec_module(wrapper)
    if tool in ('metasem', 'scregulate'):
        wrapper._configure_threads(threads)
    elif tool == 'cespgrn':
        from sklearn.decomposition import PCA
        import torch, g_admm
        torch.set_num_threads(threads)
    elif tool == 'scgenerai':
        import scGeneRAI, torch
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
    result = {'parent': pools(), 'affinity': sorted(os.sched_getaffinity(0))}
    if tool in ('genie3', 'grnboost2'):
        from distributed import Client, LocalCluster
        with LocalCluster(n_workers=threads, threads_per_worker=1,
                          processes=True, dashboard_address=None) as cluster:
            with Client(cluster) as client:
                result['workers'] = list(client.run(pools).values())
    print('ANDREA_THREAD_AUDIT=' + json.dumps(result), flush=True)

if __name__ == '__main__':
    main()
'''

R_NATIVE_PROBE = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <Rinternals.h>
SEXP audit_blas_threads(void) {
    int (*f)(void) = (int (*)(void))dlsym(RTLD_DEFAULT, "openblas_get_num_threads");
    return Rf_ScalarInteger(f ? f() : -1);
}
'''

R_PROBE = r'''
expressions <- parse('/probe/original.R')
# Every supported R wrapper ends with main() or its sys.nframe() guard.
stopifnot(grepl('main', paste(deparse(expressions[[length(expressions)]]), collapse=' ')))
for (e in expressions[-length(expressions)]) eval(e, envir=.GlobalEnv)
args <- commandArgs(trailingOnly=TRUE)
threads <- as.integer(args[match('--threads',args)+1L])
if (Sys.getenv('ANDREA_AUDIT_TOOL') == 'tigress') configure_threads(threads)
file.copy('/probe/probe.c', '/tmp/probe.c')
status <- system2('R', c('CMD','SHLIB','-o','/tmp/probe.so','/tmp/probe.c'),
                  stdout='/tmp/compile.log', stderr='/tmp/compile.log')
if (status != 0) stop(paste(readLines('/tmp/compile.log'),collapse='\n'))
dyn.load('/tmp/probe.so')
n <- .Call('audit_blas_threads')
stopifnot(n > 0L)
result <- list(parent=list(list(library='OpenBLAS',num_threads=n)))
if (Sys.getenv('ANDREA_AUDIT_TOOL') == 'tigress' && threads > 1L) {
    counts <- foreach::`%dopar%`(foreach::foreach(i=seq_len(threads)), .Call('audit_blas_threads'))
    result$workers <- lapply(counts,function(x) list(list(library='OpenBLAS',num_threads=x)))
    doParallel::stopImplicitCluster()
}
cat('ANDREA_THREAD_AUDIT=', jsonlite::toJSON(result, auto_unbox=TRUE), '\n', sep='')
'''


def check_report(report: dict, expected: int, *, workers: int | None = None) -> None:
    if not report.get("parent"):
        raise ValueError("No native pools were inspected")
    for pool in report["parent"]:
        if pool["num_threads"] != expected:
            raise ValueError(f"Parent pool violates thread policy: {pool}; expected {expected}")
    if workers is not None:
        if len(report.get("workers", [])) != workers:
            raise ValueError("Worker probes did not cover the requested worker count")
        for pools in report["workers"]:
            if not pools or any(pool["num_threads"] != 1 for pool in pools):
                raise ValueError(f"Nested worker pools exceed one thread: {pools}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", action="append", default=[])
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    mapping = load_json(DEFAULT_TEMPLATE_MAP)
    configs = validate_template_map(mapping, {name: Path(name) for name in mapping["tools"]})
    names = args.tool or [name for name, cfg in configs.items() if cfg.native_threads]
    available = sorted(os.sched_getaffinity(0))
    results = []
    for name in sorted(names):
        config = configs[name]
        if config.native_threads is None:
            raise ValueError(f"No entrypoint thread policy for {name}")
        spec = load_json(ROOT / "andrea/catalog_inference_tools/tools" / name / "toolspec.json")
        maximum = spec["runtime_resources"]["threading"].get("max_threads")
        image = spec["docker_image"]
        image_id = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True).strip()
        suffix = ".py" if config.runtime == "python" else ".R"
        app = "/app/run_tool" + suffix
        with tempfile.TemporaryDirectory(prefix="andrea-thread-audit-") as tmp:
            folder = Path(tmp)
            folder.chmod(0o755)
            original = subprocess.check_output(["docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", image_id, app])
            (folder / ("original" + suffix)).write_bytes(original)
            (folder / ("probe" + suffix)).write_text(PYTHON_PROBE if config.runtime == "python" else R_PROBE)
            (folder / "probe.c").write_text(R_NATIVE_PROBE)
            for threads in args.threads:
                if threads < 1 or threads > len(available) or (maximum is not None and threads > maximum):
                    continue
                for pinned in (False, True):
                    container = "andrea-thread-audit-" + uuid.uuid4().hex[:12]
                    command = ["docker", "run", "--rm", "--name", container, "--network", "none",
                               "--cpus", str(threads), "--memory", "4g",
                               "-v", f"{folder}:/probe:ro", "-v", f"{folder / ('probe' + suffix)}:{app}:ro",
                               "-e", f"ANDREA_AUDIT_TOOL={name}"]
                    # Deliberately conflicting inherited settings must be overridden.
                    for key in ENV_KEYS:
                        command += ["-e", f"{key}=7"]
                    if pinned:
                        command += ["--cpuset-cpus", ",".join(map(str, available[:threads]))]
                    command += [image_id, "--input", "/unused", "--params", "/unused", "--extra", "/unused",
                                "--output-dir", "/tmp/out", "--threads", str(threads)]
                    row = {"tool": name, "image": image, "image_id": image_id, "threads": threads, "pinned": pinned}
                    try:
                        completed = subprocess.run(command, text=True, capture_output=True, timeout=180)
                        row.update(exit_code=completed.returncode, stderr=completed.stderr[-3000:])
                        if completed.returncode:
                            raise ValueError(completed.stdout[-3000:] + completed.stderr[-3000:])
                        line = next(line for line in completed.stdout.splitlines() if line.startswith("ANDREA_THREAD_AUDIT="))
                        report = json.loads(line.split("=", 1)[1])
                        row["observed"] = report
                        workers = threads if name in ("genie3", "grnboost2") or (name == "tigress" and threads > 1) else None
                        check_report(report, threads if config.native_threads == "assigned" else 1, workers=workers)
                        row["status"] = "passed"
                    except Exception as exc:
                        row.update(status="failed", error=str(exc))
                    finally:
                        subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    results.append(row)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps({"status": "running", "results": results}, indent=2) + "\n")
                    print(name, threads, "pinned" if pinned else "unrestricted", row["status"], flush=True)
    passed = bool(results) and all(row["status"] == "passed" for row in results)
    args.output.write_text(json.dumps({"status": "passed" if passed else "failed", "results": results}, indent=2) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
