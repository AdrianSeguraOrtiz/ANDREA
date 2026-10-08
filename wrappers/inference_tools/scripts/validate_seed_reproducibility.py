"""Check seeded wrappers in fresh containers using the catalog smoke fixtures.

Each tool is tested twice with one worker, twice with two workers, and once
with a different seed. Evidence is retained in a new output directory. This
bounded regression does not establish determinism on other hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ("dignet", "planet", "scgenerai")


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def validate(tool, output, timeout):
    spec = read(ROOT / "andrea/catalog_inference_tools/tools" / tool / "toolspec.json")
    fixtures = ROOT / "wrappers/inference_tools/tests/fixtures"
    config = read(ROOT / "wrappers/inference_tools/tests/smoketest_configs" / f"{tool}.json")
    params = {key: value["default"] for key, value in spec["params"].items() if "default" in value}
    params.update(config.get("param_overrides", {}))
    if tool != "scgenerai":
        params["ensemble"] = 3  # Exercise multiple members even with one worker.
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", spec["docker_image"]], text=True
    ).strip()
    cases = [(1729, 1, 1), (1729, 1, 2), (1729, 2, 1), (1729, 2, 2), (1730, 2, 1)]
    results = []
    for seed, threads, repeat in cases:
        work = output / tool / f"seed{seed}-threads{threads}-repeat{repeat}"
        (work / "out").mkdir(parents=True)
        (work / "extra").mkdir()
        for name in ["expression.tsv", *config.get("extra_files", [])]:
            source = fixtures / tool / name
            if not source.exists():
                source = fixtures / name
            target = work / name if name == "expression.tsv" else work / "extra" / name
            shutil.copy2(source, target)
        write(work / "params.json", {**params, "seed": seed})
        write(work / "execution.json", config["execution"])
        name = f"andrea-seed-{uuid.uuid4().hex}"
        command = [
            "docker", "run", "--rm", "--name", name, "--network", "none",
            "--cpus", str(threads), "--memory", "8g", "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{work.resolve()}:/io:ro", "-v", f"{(work / 'out').resolve()}:/io/out:rw",
            image, "--input", "/io/expression.tsv", "--params", "/io/params.json",
            "--extra", "/io/extra", "--output-dir", "/io/out", "--threads", str(threads),
        ]
        started = time.monotonic()
        try:
            with (work / "container.log").open("w") as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True)
        finally:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        network = work / "out/network.csv"
        if len(network.read_text().splitlines()) < 2:
            raise ValueError(f"Empty regression output: {network}")
        row = {"seed": seed, "threads": threads, "repeat": repeat, "image_id": image,
               "seconds": time.monotonic() - started,
               "network_sha256": hashlib.sha256(network.read_bytes()).hexdigest()}
        write(work / "receipt.json", row)
        results.append(row)
    hashes = [row["network_sha256"] for row in results]
    checks = {"repeat_one_worker_exact": hashes[0] == hashes[1],
              "repeat_two_workers_exact": hashes[2] == hashes[3],
              "different_seed_changes_output": hashes[3] != hashes[4]}
    # Diffusion members use one math-library thread irrespective of n_jobs.
    if tool != "scgenerai":
        checks["independent_of_worker_count"] = hashes[0] == hashes[2]
    result = {"tool": tool, "checks": checks, "runs": results,
              "status": "passed" if all(checks.values()) else "failed"}
    write(output / tool / "summary.json", result)
    if not all(checks.values()):
        raise ValueError(f"Seed regression failed for {tool}: {checks}")
    print(f"{tool}: {checks}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", choices=TOOLS, action="append")
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    args.results_dir.mkdir(parents=True, exist_ok=False)
    results = [validate(tool, args.results_dir, args.timeout) for tool in (args.tool or TOOLS)]
    write(args.results_dir / "summary.json", {"status": "passed", "tools": results})


if __name__ == "__main__":
    main()
