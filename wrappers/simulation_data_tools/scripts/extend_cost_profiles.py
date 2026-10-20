"""Measure selected simulator profiles with verified, resumable observations.

Existing complete profiles are reused only when their recipe and image identities
match. An incomplete profile is published atomically only after all repetitions
pass output and dimension validation. This companion leaves the historical
benchmark recipe unchanged, so unrelated measurements retain their provenance.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wrappers"))
sys.path.insert(0, str(Path(__file__).parent))
from benchmark_support import atomic_json, collect_provenance, load_script, profile_fingerprint

B = load_script(Path(__file__).with_name("benchmark_costs.py"), "simulator_extension_benchmark")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runner_fingerprint():
    return hashlib.sha256(Path(__file__).read_bytes() + b"\0" +
                          Path(__file__).with_name("verify_dyngen_sampling.R").read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def output_hashes(folder):
    output = (folder / "out").resolve()
    hashes = {}
    for path in sorted(output.rglob("*")):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(output):
            raise ValueError(f"Observation output escapes its directory: {path}")
        hashes[path.relative_to(output).as_posix()] = digest(path)
    if not hashes:
        raise ValueError(f"Observation output inventory is empty: {folder}")
    return hashes


def verify_observation(folder, identity):
    """Do not silently retry failed runs or reuse outputs from another recipe."""
    record = read(folder / "observation.json")
    if record.get("identity") != identity:
        raise ValueError(f"Observation identity changed: {folder}")
    if record.get("status") != "ok":
        raise ValueError(f"Previous unsuccessful observation retained: {folder}")
    if not record.get("output_sha256"):
        raise ValueError(f"Observation output inventory is empty: {folder}")
    for name, expected in record["output_sha256"].items():
        path = (folder / "out" / name).resolve()
        if (Path(name).is_absolute() or not path.is_relative_to((folder / "out").resolve())
                or not path.is_file() or digest(path) != expected):
            raise ValueError(f"Observation output changed: {path}")
    if output_hashes(folder) != record["output_sha256"]:
        raise ValueError(f"Observation output inventory changed: {folder}")
    return record


def reuse_observation(folder, identity, profile, size, revalidate_from=()):
    """Explicit validation-only migration; execution identity must not change."""
    previous = read(folder / "observation.json")
    old_identity = previous.get("identity", {})
    if old_identity == identity:
        record = verify_observation(folder, identity)
        validate_dimensions(folder, profile, size, image_id=identity["image_id"])
        return record
    if (old_identity.get("runner_sha256") not in revalidate_from
            or {**old_identity, "runner_sha256": identity["runner_sha256"]} != identity):
        raise ValueError(f"Observation identity changed: {folder}")
    verify_observation(folder, old_identity)
    validation = validate_dimensions(folder, profile, size, image_id=identity["image_id"])
    # A validator must not modify evidence. Retain the original receipt and
    # every measured duration rather than pretending to have executed again.
    verify_observation(folder, old_identity)
    archive = folder / f"observation.validation-{old_identity['runner_sha256']}.json"
    if archive.exists() and read(archive) != previous:
        raise ValueError(f"Conflicting original validation receipt: {archive}")
    atomic_json(archive, previous)
    record = {**previous, "identity": identity, "validation": validation,
              "revalidated_from": {"receipt": archive.name, "sha256": digest(archive)}}
    atomic_json(folder / "observation.json", record)
    return record


def complete_profile(previous, profile, identity):
    """A count alone cannot prove that every requested geometry was measured."""
    if not previous or previous.get("benchmark_config", {}).get("extension_identity") != identity:
        return False
    dimensions = profile.dimension_profile
    expected = {(size.genes, size.cells, dimensions["group_count"], dimensions["population_count"],
                 identity["threads"], float(identity["ram_gb"])) for size in profile.sizes}
    if len(expected) != len(profile.sizes):
        raise ValueError("Benchmark size grid contains duplicate points.")
    points = previous.get("runtime_points", [])
    actual = {(point["genes"], point["cells"], point["groups"], point["population_count"],
               point["threads"], float(point["ram_gb"])) for point in points}
    repeats = identity["repeats"]
    return (actual == expected and len(points) == len(expected) and all(
        point["status"] == "ok" and point["repeats_ok"] == point["repeats_total"] == repeats
        and point["repeats_failed"] == 0 and point["ok_rate"] == 1
        and not any(point["failure_breakdown"].values())
        and all(isinstance(point.get(key), (int, float)) and math.isfinite(point[key]) and point[key] > 0
                for key in ["seconds_p50", "seconds_p90"])
        for point in points))


def publish_profile(cost_path, new_profile, previous):
    """Merge under a short, path-specific lock without losing other profiles."""
    from jsonschema import Draft202012Validator
    lock_root = Path(tempfile.gettempdir()) / "andrea-simulator-cost-locks"
    lock_root.mkdir(exist_ok=True)
    lock_name = hashlib.sha256(str(cost_path.resolve()).encode()).hexdigest() + ".lock"
    with (lock_root / lock_name).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read(cost_path)
        matches = [item for item in current["profiles"] if item["profile_id"] == new_profile["profile_id"]]
        if len(matches) > 1 or (matches[0] if matches else None) != previous:
            raise ValueError("The measured cost profile changed concurrently; refusing to overwrite it.")
        current["profiles"] = [new_profile if item["profile_id"] == new_profile["profile_id"] else item
                               for item in current["profiles"]]
        if not matches:
            current["profiles"].append(new_profile)
        Draft202012Validator(read(ROOT / "andrea/catalog_simulation_data_tools/schemas/simulatorcost.schema.json")).validate(current)
        atomic_json(cost_path, current)
    return current


def verify_dyngen_sampling(folder, image_id):
    if not image_id or not image_id.startswith("sha256:"):
        raise ValueError("Native dyngen validation requires the measured immutable image ID")
    script = Path(__file__).with_name("verify_dyngen_sampling.R").resolve()
    result = subprocess.run([
        "docker", "run", "--rm", "--network", "none", "--cpus", "1", "--memory", "8g",
        "-v", f"{(folder / 'out').resolve()}:/evidence:ro",
        "-v", f"{script}:/verify.R:ro", "--entrypoint", "Rscript", image_id,
        "/verify.R", "/evidence",
    ], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(f"Native dyngen sampling verification failed: {result.stderr[-3000:]}")
    return json.loads(result.stdout)


def validate_dimensions(folder, profile, size, *, image_id=None):
    from andrea.core.commands.generate_data.output_validation import validate_simulator_output_package
    from jsonschema import Draft202012Validator
    manifest = read(folder / "out/simulator-output-manifest.json")
    schema = read(ROOT / "andrea/catalog_simulation_data_tools/schemas/simulator-output-manifest.schema.json")
    Draft202012Validator(schema).validate(manifest)
    validation = validate_simulator_output_package(
        stage_dir=folder / "out", dataset_id=profile.profile_id,
        data_axes=profile.data_axes, truth_requirements=profile.truth_requirements,
        simulator_manifest=manifest,
    )
    actual = validation["expression"]
    gene_semantics = "exported_genes"
    cell_semantics = "exported_columns"
    if getattr(profile, "simulator_id", None) == "dyngen":
        # dyngen allocates at least one TF to each backbone module. A requested
        # TF budget below that minimum is expanded by the native simulator;
        # even the number of modules can depend on the seed. The calibration
        # coordinate is the requested budget, never a false output shape.
        params = B.apply_dimensions_to_params(base_params=profile.params,
                                              dimension_profile=profile.dimension_profile, size=size)
        tf_ids = (folder / "out" / manifest["extras"]["tf_list"]).read_text().splitlines()
        requested_tfs = params["num_tfs"]
        if (requested_tfs + params["num_targets"] + params["num_hks"] != size.genes
                or len(set(tf_ids)) != len(tf_ids) or len(tf_ids) < requested_tfs
                or actual["genes"] != len(tf_ids) + params["num_targets"] + params["num_hks"]):
            raise ValueError("Dyngen exported gene count violates its TF/target/housekeeping allocation")
        gene_semantics = "requested_gene_budget"
        if params.get("experiment_params", {}).get("kind") == "snapshot":
            native = verify_dyngen_sampling(folder, image_id)
            if (native["kind"] != "snapshot" or native["requested_cells"] != size.cells
                    or native["observed_cells"] != actual["columns"]
                    or native["expected_cells"] != actual["columns"]
                    or not native["expression_matches_native"]
                    or native["dropped_cells"] != size.cells - actual["columns"]
                    or not 0 < actual["columns"] <= size.cells):
                raise ValueError("Dyngen exported cells violate its verified native allocation")
            cell_semantics = "requested_cell_budget"
            validation["native_sampling"] = native
            validation["warnings"] = []
            if native["missing_segments"]:
                validation["warnings"].append("Native trajectories do not cover every backbone segment.")
            if native["dropped_cells"]:
                validation["warnings"].append(
                    f"Native snapshot sampling returned {actual['columns']} of {size.cells} requested cells; "
                    "singleton branches were omitted. This is not full requested cell coverage.")
    if ((cell_semantics == "exported_columns" and actual["columns"] != size.cells)
            or (gene_semantics == "exported_genes" and actual["genes"] != size.genes)):
        raise ValueError(f"Requested {size.genes}x{size.cells}, produced {actual['genes']}x{actual['columns']}")
    validation["dimension_semantics"] = {"genes": gene_semantics, "cells": cell_semantics}
    validation["requested_dimensions"] = {"genes": size.genes, "columns": size.cells}
    missing = [name for name in getattr(profile, "effective_extras", ())
               if not manifest.get("extras", {}).get(name)]
    if missing:
        raise ValueError(f"Requested extras were not generated: {missing}")
    expected_groups = getattr(profile, "dimension_profile", {}).get("group_count", 0)
    groups_path = manifest.get("extras", {}).get("groups")
    if groups_path:
        with (folder / "out" / groups_path).open(newline="") as stream:
            groups = {row["cluster"] for row in csv.DictReader(stream, delimiter="\t")}
        if expected_groups and len(groups) != expected_groups:
            raise ValueError(f"Requested {expected_groups} groups, produced {len(groups)}")
        validation["observed_groups"] = len(groups)
    return validation


def run_measurement(**kwargs):
    # Match the public generate backend's BoolODE hash-seed control. Changing
    # the legacy benchmark script would invalidate unrelated recipe identities.
    run_command = B.run_cmd
    def command(args, **options):
        if kwargs["simulator_id"] == "boolode" and args[:2] == ["docker", "run"]:
            args = [*args[:2], "-e", f"PYTHONHASHSEED={kwargs['seed']}", *args[2:]]
        return run_command(args, **options)
    with patch.object(B, "run_cmd", command):
        return B.run_container_once(**kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append", required=True, help="SIMULATOR:PROFILE_ID")
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--cost-profiles-dir", type=Path, default=B.DEFAULT_COST_PROFILES_DIR)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--ram-gb", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--revalidate-from-runner", action="append", default=[],
                        help="Explicitly revalidate successful receipts from this reviewed runner hash; "
                             "all execution identity fields and output hashes must still match.")
    args = parser.parse_args(argv)
    if min(args.threads, args.ram_gb, args.timeout) < 1 or args.repeats < 3:
        raise ValueError("Positive resources/deadline and at least three repetitions are required.")
    targets = B.resolve_simulator_targets(
        selected_simulators=B.discover_catalog_simulator_dirs(B.DEFAULT_CATALOG_SIMULATORS_ROOT),
        catalog_simulators_root=B.DEFAULT_CATALOG_SIMULATORS_ROOT,
        cost_profiles_dir=args.cost_profiles_dir, param_overrides_dir=B.DEFAULT_PARAM_OVERRIDES_DIR,
        default_group_count=2, profile_filters=args.profile,
    )
    planned = [{"simulator_id": t.simulator_id, "profile_id": p.profile_id,
                "sizes": [vars(s) for s in p.sizes], "runs": len(p.sizes) * args.repeats}
               for t in targets for p in t.profiles]
    for target in targets:
        for profile in target.profiles:
            geometries = {(size.genes, size.cells) for size in profile.sizes}
            if not geometries or len(geometries) != len(profile.sizes):
                raise ValueError("Benchmark size grid must be nonempty and contain no duplicate points.")
    if args.plan_only:
        print(json.dumps(planned, indent=2)); return 0
    args.results_dir.mkdir(parents=True, exist_ok=True)
    with (args.results_dir / "campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = {"status": "running", "profiles": {}, "planned": planned}
        atomic_json(args.results_dir / "status.json", state)
        try:
            for target in targets:
                spec = target.spec
                provenance = collect_provenance(
                    root=ROOT, spec_path=target.catalog_simulator_dir / "simulatorspec.json",
                    image=spec["docker_image"], seed=args.seed,
                    source_dir=B.DEFAULT_WRAPPERS_ROOT / target.simulator_id,
                )
                B.filter_threads_for_simulator(simulator_id=target.simulator_id, spec=spec,
                                              requested_threads=[args.threads])
                cost_path = target.catalog_simulator_dir / "cost.json"
                existing = read(cost_path)
                for profile in target.profiles:
                    key = f"{target.simulator_id}:{profile.profile_id}"
                    profile_hash = profile_fingerprint(profile, root=ROOT, kind="simulation")
                    previous = next((p for p in existing["profiles"] if p["profile_id"] == profile.profile_id), None)
                    identity = {"profile_sha256": profile_hash, "image_id": provenance["image_id"],
                                "spec_sha256": provenance["spec_sha256"], "seed": args.seed,
                                "threads": args.threads, "ram_gb": args.ram_gb, "repeats": args.repeats,
                                "timeout": args.timeout, "runner_sha256": runner_fingerprint(),
                                "support_sha256": digest(ROOT / "wrappers/benchmark_support.py"),
                                "validation_sha256": hashlib.sha256(b"\0".join(
                                    path.read_bytes() for path in [
                                        ROOT / "andrea/core/commands/generate_data/output_validation.py",
                                        ROOT / "andrea/catalog_simulation_data_tools/schemas/simulator-output-manifest.schema.json",
                                    ])).hexdigest(),
                                "build_context_sha256": provenance["build_context_sha256"]}
                    if complete_profile(previous, profile, identity):
                        state["profiles"][key] = "reused_complete"
                        atomic_json(args.results_dir / "status.json", state)
                        continue
                    records = []
                    folder = args.results_dir / target.simulator_id / profile.profile_id
                    for size in profile.sizes:
                        params = B.apply_dimensions_to_params(base_params=profile.params,
                                                             dimension_profile=profile.dimension_profile, size=size)
                        B._resolve_simulator_params(simulator_id=target.simulator_id, user_params=params,
                                                    spec_params=spec["params"])
                        for repeat in range(1, args.repeats + 1):
                            work = folder / f"g{size.genes}-c{size.cells}-r{repeat}"
                            run_identity = {**identity, "genes": size.genes, "cells": size.cells, "repeat": repeat}
                            if (work / "observation.json").exists():
                                record = reuse_observation(work, run_identity, profile, size,
                                                           args.revalidate_from_runner)
                            else:
                                if work.exists() and any(work.iterdir()):
                                    raise RuntimeError(f"Interrupted observation retained; inspect before retry: {work}")
                                work.mkdir(parents=True, exist_ok=True)
                                record = {"identity": run_identity, "status": "running", "genes": size.genes,
                                          "cells": size.cells, "repeat": repeat, "threads": args.threads,
                                          "ram_gb": args.ram_gb, "groups": profile.dimension_profile["group_count"],
                                          "population_count": profile.dimension_profile["population_count"]}
                                atomic_json(work / "observation.json", record)
                                print(key, size, repeat, flush=True)
                                status, seconds, byte_count, memory, error = run_measurement(
                                    image_tag=provenance["image_id"], workdir=work, simulator_id=target.simulator_id,
                                    profile=profile, params=params, threads=args.threads, ram_gb=args.ram_gb,
                                    seed=args.seed, timeout_s=args.timeout,
                                )
                                record.update(status=status, seconds=seconds, output_bytes=byte_count,
                                              peak_memory_mb=memory, error=error)
                                try:
                                    if status != "ok": raise RuntimeError(error)
                                    record["validation"] = validate_dimensions(work, profile, size,
                                                                                image_id=provenance["image_id"])
                                    record["output_sha256"] = output_hashes(work)
                                except Exception as exc:
                                    record.update(status="validation_failed", error=f"{type(exc).__name__}: {exc}")
                                    raise
                                finally:
                                    atomic_json(work / "observation.json", record)
                            records.append(record)
                            state.update(current=key, completed_observations=len(records))
                            atomic_json(args.results_dir / "status.json", state)
                    config = B.build_benchmark_config(simulator_id=target.simulator_id, profile=profile,
                        sizes=list(profile.sizes), threads=[args.threads], ram_gb=[args.ram_gb], args=args)
                    config.update(provenance=provenance, profile_sha256=profile_hash, extension_identity=identity)
                    config["dimension_semantics"] = records[0]["validation"]["dimension_semantics"]
                    points = B.aggregate_runtime_points(profile=profile, all_runs=records)
                    for point in points:
                        observations = [{key: r["validation"]["expression"][key] for key in ("genes", "columns")} for r in records
                                        if (r["genes"], r["cells"]) == (point["genes"], point["cells"])]
                        point["feature_vector"]["dimension_semantics"] = config["dimension_semantics"]
                        point["feature_vector"]["observed_dimensions"] = observations
                        matching = [r for r in records if (r["genes"], r["cells"]) == (point["genes"], point["cells"])]
                        if config["dimension_semantics"]["cells"] == "requested_cell_budget":
                            point["feature_vector"]["native_sampling"] = [r["validation"]["native_sampling"] for r in matching]
                        point["feature_vector"]["validation_warnings"] = sorted({
                            warning for r in matching for warning in r["validation"].get("warnings", [])})
                    new_profile = B.make_cost_profile_entry(profile_id=profile.profile_id, benchmark_config=config,
                        runtime_points=points)
                    existing = publish_profile(cost_path, new_profile, previous)
                    state["profiles"][key] = "completed"
                    atomic_json(args.results_dir / "status.json", state)
            state["status"] = "completed"
        except BaseException as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            atomic_json(args.results_dir / "status.json", state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
