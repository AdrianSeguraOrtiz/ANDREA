"""Check held-out timing predictions against retained evidence, without running tools.

The frozen plan contains schema_version=1, cost and estimator artifact references,
training_observations, and cases. Each case has id, kind (interpolation or
extrapolation), identity, dimensions, inputs, predicted_typical_seconds,
predicted_planning_seconds, max_relative_error, and min_planning_coverage.
Identity contains profile_id, image_id, params_sha256, source_sha256, hardware_id,
threads and ram_gb. params_sha256 identifies the resolved recipe with its
dimension mappings (point coordinates are recorded separately). An observation has id, receipt, identity, dimensions, inputs
and outputs. Artifacts are {path, sha256}; inputs additionally require role
primary (workload-defining matrix/configuration) or shared (annotation/native
resource). Receipt may also have an RFC6901 pointer to one JSON row. Receipt rows
must contain status, seconds, unique run_id, all identity/dimension fields (flat
or nested) and input_sha256 inventory (list of hashes or name-to-hash mapping).
An adapted receipt may add raw_evidence artifact references to original rows and
configuration/provenance; retain its explicit reviewed mapping without inventing
measurements or execution IDs. The observations file contains plan_sha256 and cases, each with an id
and observations (at least three successful, distinct receipts).

All artifact paths are relative to the JSON file that references them. Inventory
hashes, dimensions, strata, receipt disjointness and errors are checked; actual
experimental independence and the chronology of freezing require provenance
review. Never promote these hold-outs into fitting data while retaining this
validation claim. Failures remain in the evidence and fail this check.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys

_COMPATIBILITY_CACHE = {}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def referenced_paths(value, base):
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and "sha256" in value:
            yield (base / value["path"]).resolve()
        for item in value.values():
            yield from referenced_paths(item, base)
    elif isinstance(value, list):
        for item in value:
            yield from referenced_paths(item, base)


def artifact(ref, base):
    path = (base / ref["path"]).resolve()
    if not path.is_file() or digest(path) != ref["sha256"]:
        raise ValueError(f"Missing or changed evidence: {path}")
    return path


def inventory(refs, base):
    if not refs:
        raise ValueError("Input/output inventory must be nonempty")
    # File names may differ after evidence is copied; content establishes reuse.
    return tuple(sorted(digest(artifact(ref, base)) for ref in refs))


def input_inventory(refs, base):
    all_inputs = inventory(refs, base)
    if any(ref.get("role") not in {"primary", "shared"} for ref in refs):
        raise ValueError("Every input needs explicit primary/shared role")
    primary = tuple(sorted(ref["sha256"] for ref in refs if ref["role"] == "primary"))
    if not primary:
        raise ValueError("At least one primary workload input is required")
    return all_inputs, primary


def receipt_data(ref, base):
    path = artifact(ref, base)
    row = read(path)
    pointer = ref.get("pointer", "")
    if pointer and not pointer.startswith("/"):
        raise ValueError("Receipt pointer must be an RFC6901 JSON pointer")
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        row = row[int(token)] if isinstance(row, list) else row[token]
    return row, path.parent


def contexts(value):
    """Inspect every supported representation, including conflicting duplicates."""
    if isinstance(value, dict):
        yield value
        for key in (
            "identity",
            "provenance",
            "measurement_provenance",
            "runtime_resources",
            "resources",
            "dimensions",
            "context",
        ):
            if key in value:
                yield from contexts(value[key])


def check_observed_fields(sources, expected, *, dimension=False):
    for key, wanted in expected.items():
        aliases = (
            {key, "cells"}
            if key == "columns"
            else {key, "columns"} if key == "cells" else {key}
        )
        observed = [
            source[alias] for source in sources for alias in aliases if alias in source
        ]
        if not observed:
            raise ValueError(
                f"Receipt is missing observed {'dimension ' if dimension else ''}{key}"
            )
        if any(item != wanted for item in observed):
            raise ValueError(
                f"Receipt contradicts declared {'dimension ' if dimension else ''}{key}"
            )


def positive(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def identity(value):
    required = (
        "profile_id",
        "image_id",
        "params_sha256",
        "source_sha256",
        "hardware_id",
        "threads",
        "ram_gb",
    )
    if set(value) != set(required) or not all(value[key] for key in required):
        raise ValueError(f"Identity must contain exactly {required}")
    if (
        not value["image_id"].startswith("sha256:")
        or len(value["image_id"]) != 71
        or any(char not in "0123456789abcdef" for char in value["image_id"][7:])
    ):
        raise ValueError("Prediction validation requires an immutable Docker image ID")
    for key in ("params_sha256", "source_sha256"):
        if len(value[key]) != 64 or any(
            char not in "0123456789abcdef" for char in value[key]
        ):
            raise ValueError(f"Invalid {key}")
    if (
        not positive(value["threads"])
        or int(value["threads"]) != value["threads"]
        or not positive(value["ram_gb"])
    ):
        raise ValueError("Invalid resource assignment")
    return tuple(value[key] for key in required)


def dimensions(value):
    if not value or not all(
        isinstance(key, str) and positive(item) for key, item in value.items()
    ):
        raise ValueError("Dimensions must be explicit positive numeric coordinates")
    return tuple(sorted(value.items()))


def observation(value, base):
    row, receipt_base = receipt_data(value["receipt"], base)
    if not isinstance(row, dict):
        raise ValueError("Receipt must select a JSON object")
    if row.get("status") != "ok" or not positive(row.get("seconds")):
        raise ValueError(
            "Every held-out/training receipt must retain a successful positive timing; failures cannot be omitted as successful evidence"
        )
    stratum = identity(value["identity"])
    coords = dimensions(value["dimensions"])
    run_id = row.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError(
            "Receipt requires a stable nonempty run_id for the actual execution"
        )
    sources = list(contexts(row))
    for ref in row.get("raw_evidence", []):
        original, _ = receipt_data(ref, receipt_base)
        sources.extend(contexts(original))
    check_observed_fields(sources, {"status": row["status"], "seconds": row["seconds"]})
    check_observed_fields(sources, {"run_id": run_id})
    compatible_sources = None
    if "source_compatibility" in row:
        compatibility_path = artifact(row["source_compatibility"], receipt_base)
        compatibility_spec = importlib.util.spec_from_file_location(
            "_andrea_checker_calibration_compatibility",
            Path(__file__).resolve().parent / "calibration_compatibility.py",
        )
        compatibility_module = importlib.util.module_from_spec(compatibility_spec)
        compatibility_spec.loader.exec_module(compatibility_module)
        cache_key = (
            str(compatibility_path),
            row["source_compatibility"]["sha256"],
            value["identity"]["image_id"],
            value["identity"]["source_sha256"],
        )
        if cache_key not in _COMPATIBILITY_CACHE:
            _COMPATIBILITY_CACHE[cache_key] = compatibility_module.verify(
                compatibility_path,
                image_id=value["identity"]["image_id"],
                current_source_sha256=value["identity"]["source_sha256"],
            )
        proof = _COMPATIBILITY_CACHE[cache_key]
        original = row.get("original_identity", {})
        capture = proof["executions"].get(run_id)
        if (
            capture is None
            or original.get("source_sha256") != capture["original_source_sha256"]
            or {key: item for key, item in original.items() if key != "source_sha256"}
            != {
                key: item
                for key, item in value["identity"].items()
                if key != "source_sha256"
            }
        ):
            raise ValueError(
                "Source compatibility cannot change observed image/hardware/recipe/resources."
            )
        captured_path = (
            compatibility_path.parent / capture["raw_receipt"]["path"]
        ).resolve()
        if not any(
            (receipt_base / ref["path"]).resolve() == captured_path
            and ref.get("sha256") == capture["raw_receipt"]["sha256"]
            and ref.get("pointer", "") == capture["raw_receipt"].get("pointer", "")
            for ref in row.get("raw_evidence", [])
        ):
            raise ValueError(
                "Source compatibility requires the captured original raw execution reference."
            )
        compatible_sources = {
            original["source_sha256"],
            value["identity"]["source_sha256"],
        }
    check_observed_fields(
        sources,
        {
            key: item
            for key, item in value["identity"].items()
            if compatible_sources is None or key != "source_sha256"
        },
    )
    if compatible_sources is not None:
        observed_sources = {
            source["source_sha256"] for source in sources if "source_sha256" in source
        }
        if (
            not observed_sources
            or not observed_sources <= compatible_sources
            or row["original_identity"]["source_sha256"] not in observed_sources
        ):
            raise ValueError(
                "Receipt source differs from its explicit compatibility capture."
            )
    check_observed_fields(sources, dict(coords), dimension=True)
    inputs, primary = input_inventory(value["inputs"], base)
    raw_inputs = row.get("input_sha256")
    raw_hashes = (
        list(raw_inputs.values()) if isinstance(raw_inputs, dict) else raw_inputs
    )
    if not isinstance(raw_hashes, list) or tuple(sorted(raw_hashes)) != inputs:
        raise ValueError(
            "Receipt input_sha256 contradicts the verified input inventory"
        )
    inventory(value["outputs"], base)
    # Canonical row content catches a copied observation even after it moves
    # between files or array positions. File hashes still seal the raw artifact.
    receipt = hashlib.sha256(
        json.dumps(row, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    source_receipts = {
        hashlib.sha256(
            json.dumps(source, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        for source in sources
        if "status" in source and "seconds" in source
    }
    return {
        "id": value["id"],
        "receipt": receipt,
        "run_id": run_id,
        "source_receipts": source_receipts,
        "role": (
            "calibration"
            if any(source.get("role") == "calibration" for source in sources)
            else row.get("role")
        ),
        "stratum": stratum,
        "dimensions": coords,
        "inputs": inputs,
        "primary_inputs": primary,
        "seconds": row["seconds"],
    }


def validate(plan_path, observations_path):
    _COMPATIBILITY_CACHE.clear()
    plan, measurements = read(plan_path), read(observations_path)
    if not isinstance(plan, dict) or not isinstance(measurements, dict):
        raise ValueError("Plan and observations must be JSON objects")
    if plan.get("schema_version") != 1 or measurements.get("plan_sha256") != digest(
        plan_path
    ):
        raise ValueError("Measurements must reference the unchanged frozen plan SHA256")
    artifact(plan["cost"], plan_path.parent)
    artifact(plan["estimator"], plan_path.parent)
    training = [
        observation(value, plan_path.parent) for value in plan["training_observations"]
    ]
    if not training:
        raise ValueError(
            "Training evidence must identify the observations used by the estimator"
        )
    if any(
        len({row[key] for row in training}) != len(training)
        for key in ("id", "receipt", "run_id")
    ):
        raise ValueError("Training evidence contains duplicate IDs or receipts")
    cases = {case["id"]: case for case in plan["cases"]}
    measured = {case["id"]: case for case in measurements["cases"]}
    if (
        not cases
        or len(cases) != len(plan["cases"])
        or len(measured) != len(measurements["cases"])
        or set(cases) != set(measured)
    ):
        raise ValueError(
            "Planned and measured case IDs must match exactly without duplicates"
        )
    seen_ids = {row["id"] for row in training}
    seen_receipts = {row["receipt"] for row in training}
    seen_runs = {row["run_id"] for row in training}
    seen_source_receipts = {sha for row in training for sha in row["source_receipts"]}
    if sum(len(row["source_receipts"]) for row in training) != len(
        seen_source_receipts
    ):
        raise ValueError(
            "Training evidence contains duplicate source execution receipts"
        )
    training_inputs = {sha for row in training for sha in row["primary_inputs"]}
    all_training_inputs = {sha for row in training for sha in row["inputs"]}
    reports = []
    for name, case in cases.items():
        stratum, coords = identity(case["identity"]), dimensions(case["dimensions"])
        inputs, primary = input_inventory(case["inputs"], plan_path.parent)
        rows = [
            observation(value, observations_path.parent)
            for value in measured[name]["observations"]
        ]
        if len(rows) < 3:
            raise ValueError(
                f"{name}: at least three successful held-out repeats are required"
            )
        for row in rows:
            if row["role"] == "calibration":
                raise ValueError(
                    f"{name}: calibration receipts cannot be relabelled as independent validation"
                )
            if (
                row["id"] in seen_ids
                or row["receipt"] in seen_receipts
                or row["run_id"] in seen_runs
                or row["source_receipts"] & seen_source_receipts
            ):
                raise ValueError(
                    f"{name}: training/validation or repeat receipt overlap"
                )
            seen_ids.add(row["id"])
            seen_receipts.add(row["receipt"])
            seen_runs.add(row["run_id"])
            seen_source_receipts.update(row["source_receipts"])
            if (
                row["stratum"],
                row["dimensions"],
                row["inputs"],
                row["primary_inputs"],
            ) != (
                stratum,
                coords,
                inputs,
                primary,
            ):
                raise ValueError(
                    f"{name}: observation differs from the planned workload/resource stratum"
                )
        calibration = [
            dict(row["dimensions"]) for row in training if row["stratum"] == stratum
        ]
        if not calibration or any(
            set(point) != set(dict(coords)) for point in calibration
        ):
            raise ValueError(
                f"{name}: matching training resource/profile stratum and dimensions are required"
            )
        if (
            dict(coords) in calibration
            or set(inputs) & training_inputs
            or set(primary) & all_training_inputs
        ):
            raise ValueError(
                f"{name}: held-out size or primary input inventory was used for calibration"
            )
        outside = any(
            value < min(point[key] for point in calibration)
            or value > max(point[key] for point in calibration)
            for key, value in coords
        )
        if case["kind"] not in {"interpolation", "extrapolation"} or outside != (
            case["kind"] == "extrapolation"
        ):
            raise ValueError(
                f"{name}: declared interpolation/extrapolation disagrees with training dimensions"
            )
        typical, planning = (
            case["predicted_typical_seconds"],
            case["predicted_planning_seconds"],
        )
        threshold, coverage_threshold = (
            case["max_relative_error"],
            case["min_planning_coverage"],
        )
        if not positive(typical) or not positive(planning) or planning < typical:
            raise ValueError(
                f"{name}: predictions require positive typical and conservative planning durations"
            )
        if (
            not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not math.isfinite(threshold)
            or threshold < 0
            or not isinstance(coverage_threshold, (int, float))
            or isinstance(coverage_threshold, bool)
            or not math.isfinite(coverage_threshold)
            or not 0 <= coverage_threshold <= 1
        ):
            raise ValueError(
                f"{name}: explicit finite error and coverage thresholds are required"
            )
        seconds = [row["seconds"] for row in rows]
        median = statistics.median(seconds)
        error = abs(typical - median) / median
        coverage = sum(value <= planning for value in seconds) / len(seconds)
        reports.append(
            {
                "id": name,
                "kind": case["kind"],
                "identity": case["identity"],
                "dimensions": case["dimensions"],
                "seconds": seconds,
                "observed_median_seconds": median,
                "relative_error": error,
                "max_relative_error": threshold,
                "observed_planning_coverage": coverage,
                "min_planning_coverage": coverage_threshold,
                "passed": error <= threshold and coverage >= coverage_threshold,
            }
        )
    return {
        "schema_version": 1,
        "plan_sha256": digest(plan_path),
        "cases": reports,
        "passed": all(case["passed"] for case in reports),
        "scope": "Only the frozen cases and their declared profile/resource strata; no claim about untested sizes or prediction intervals.",
        "independence_limit": "Hashes and disjoint declared executions/primary inputs are checked. Provenance review must establish experimental independence, complete training membership and complete validation attempt/failure inventory, input-role and receipt-adaptation mappings, and that predictions/thresholds were frozen before measurements. Review must also establish that predictions were computed from the hashed estimator/cost snapshots: this checker does not execute that estimator, establish chronology or authenticate measurements.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    protected = {args.plan.resolve(), args.observations.resolve()}
    pending = [args.plan.resolve(), args.observations.resolve()]
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        try:
            linked = set(referenced_paths(read(path), path.parent))
            protected.update(linked)
            pending.extend(
                item for item in linked - visited if item.suffix.lower() == ".json"
            )
        except (OSError, ValueError):
            pass  # Invalid sources produce a failing report below.
    if args.report.resolve() in protected:
        parser.error(
            "Report must not overwrite plans, costs, observations or their evidence"
        )
    try:
        result = validate(args.plan, args.observations)
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        IndexError,
        AttributeError,
    ) as exc:
        result = {"passed": False, "error": str(exc)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(
        "Prediction evidence passed"
        if result["passed"]
        else f"Prediction evidence failed: {result.get('error', 'thresholds exceeded')}"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
