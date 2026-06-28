"""Validate simulator cost.json files against the simulator cost schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from jsonschema import Draft202012Validator

_REPO_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_IMPORTS))

from andrea.core.commands.generate_data.catalog import get_semantic_capability
from andrea.core.commands.generate_data.request import validate_simulator_inputs

from shared.catalog_simulators import (
    CATALOG_ROOT,
    DEFAULT_CATALOG_SIMULATORS_ROOT,
    discover_catalog_simulator_dirs,
    load_json,
    load_simulatorspec,
    select_simulators,
)

DEFAULT_SCHEMA_PATH = CATALOG_ROOT / "schemas" / "simulatorcost.schema.json"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate every simulator cost.json against simulatorcost.schema.json."
    )
    parser.add_argument(
        "--catalog-simulators-root",
        type=Path,
        default=DEFAULT_CATALOG_SIMULATORS_ROOT,
        help=f"Path to catalog simulators directory. Default: {DEFAULT_CATALOG_SIMULATORS_ROOT}",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to simulator cost schema. Default: {DEFAULT_SCHEMA_PATH}",
    )
    parser.add_argument(
        "--simulator",
        action="append",
        default=[],
        help="Simulator id to validate (repeatable). If omitted, validates all discovered cost files.",
    )
    parser.add_argument(
        "--require",
        action="store_true",
        help="Fail when a selected simulator has no cost.json.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first invalid cost profile.",
    )
    return parser.parse_args(argv)


def build_validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema)


def validate_instance(
    validator: Draft202012Validator, instance: object
) -> list[str]:
    errors = sorted(validator.iter_errors(instance), key=lambda err: list(err.path))
    messages: list[str] = []
    for error in errors:
        path = ".".join(str(part) for part in error.absolute_path)
        location = path if path else "(root)"
        messages.append(f"{location}: {error.message}")
    return messages


def discover_cost_files(
    catalog_simulators_root: Path, *, simulator_filters: list[str], require: bool
) -> list[tuple[str, Path]]:
    discovered = discover_catalog_simulator_dirs(catalog_simulators_root)
    selected = select_simulators(discovered, simulator_filters)
    cost_files: list[tuple[str, Path]] = []
    missing: list[str] = []
    for simulator_id, simulator_dir in selected:
        path = simulator_dir / "cost.json"
        if path.exists():
            cost_files.append((simulator_id, path))
        elif require:
            missing.append(simulator_id)
    if missing:
        raise RuntimeError(f"Missing simulator cost.json for: {', '.join(missing)}")
    return cost_files


def semantic_errors_for_cost(
    *,
    simulator_id: str,
    instance: dict[str, Any],
    catalog_simulators_root: Path,
) -> list[str]:
    spec = load_simulatorspec(catalog_simulators_root, simulator_id)
    errors: list[str] = []
    for idx, profile in enumerate(instance.get("profiles", []), start=1):
        if not isinstance(profile, dict):
            continue
        prefix = f"profiles[{idx}]"
        config = profile.get("benchmark_config", {})
        if not isinstance(config, dict):
            continue
        if config.get("simulator_id") != simulator_id:
            errors.append(
                f"{prefix}.benchmark_config.simulator_id must be '{simulator_id}'."
            )
            continue
        data_axes = config.get("data_axes", {})
        truth_requirements = config.get("truth_requirements", {})
        capability = get_semantic_capability(
            spec,
            data_axes=data_axes,
            truth_requirements=truth_requirements,
        )
        if capability is None:
            errors.append(
                f"{prefix}.benchmark_config data_axes/truth_requirements are not declared by this SimulatorSpec."
            )
            continue
        input_profile = config.get("input_profile", {})
        params_profile = config.get("params_profile", {})
        runtime_profile = config.get("runtime_resources_profile", {})
        dimension_profile = config.get("dimension_profile", {})
        if isinstance(input_profile, dict):
            errors.extend(
                semantic_input_errors(
                    simulator_id=simulator_id,
                    spec=spec,
                    data_axes=data_axes,
                    truth_requirements=truth_requirements,
                    capability=capability,
                    input_profile=input_profile,
                    params_profile=params_profile,
                    prefix=prefix,
                )
            )
        if isinstance(params_profile, dict):
            errors.extend(
                semantic_param_errors(
                    spec=spec,
                    params_profile=params_profile,
                    prefix=prefix,
                )
            )
        if isinstance(dimension_profile, dict):
            errors.extend(
                semantic_dimension_errors(
                    spec=spec,
                    dimension_profile=dimension_profile,
                    prefix=prefix,
                )
            )
        if isinstance(runtime_profile, dict):
            errors.extend(
                semantic_runtime_errors(
                    spec=spec,
                    runtime_profile=runtime_profile,
                    profile=profile,
                    prefix=prefix,
                )
            )
        errors.extend(
            semantic_feature_vector_errors(
                profile=profile,
                config=config,
                input_profile=input_profile,
                prefix=prefix,
            )
        )
    return errors


def coverage_errors_for_cost(
    *,
    simulator_id: str,
    instance: dict[str, Any],
    catalog_simulators_root: Path,
) -> list[str]:
    spec = load_simulatorspec(catalog_simulators_root, simulator_id)
    covered_capabilities: set[tuple[str, tuple[str, ...]]] = set()
    covered_extras: set[str] = set()
    for profile in instance.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        config = profile.get("benchmark_config", {})
        if not isinstance(config, dict):
            continue
        covered_capabilities.add(_capability_key(config))
        input_profile = config.get("input_profile", {})
        if isinstance(input_profile, dict):
            covered_extras.update(
                str(item) for item in input_profile.get("effective_extras", [])
            )

    errors: list[str] = []
    supported_extras: set[str] = set()
    for capability in spec.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        supported_extras.update(str(item) for item in capability.get("native_extras", []))
        supported_extras.update(
            str(item) for item in capability.get("derivable_extras", [])
        )
        if _capability_key(capability) not in covered_capabilities:
            errors.append(
                f"cost.json missing profile for capability {simulator_id}:{_capability_label(capability)}"
            )
    missing_extras = sorted(supported_extras.difference(covered_extras))
    if missing_extras:
        errors.append(f"cost.json missing cost coverage for supported extras: {missing_extras}")
    return errors


def _capability_key(payload: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    data_axes = payload.get("data_axes", {})
    truth_requirements = payload.get("truth_requirements", {})
    return (
        repr(sorted(data_axes.items())) if isinstance(data_axes, dict) else repr(data_axes),
        tuple(truth_requirements.get("contexts", []))
        if isinstance(truth_requirements, dict)
        else (),
    )


def _capability_label(capability: dict[str, Any]) -> str:
    data_axes = capability.get("data_axes", {})
    truth_requirements = capability.get("truth_requirements", {})
    contexts = (
        ",".join(truth_requirements.get("contexts", []))
        if isinstance(truth_requirements, dict)
        else "unknown"
    )
    if not isinstance(data_axes, dict):
        return f"unknown axes truth={contexts}"
    return (
        f"{data_axes.get('resolution')}/"
        f"{data_axes.get('column_kind')}/"
        f"{data_axes.get('experimental_design')} truth={contexts}"
    )


def semantic_input_errors(
    *,
    simulator_id: str,
    spec: dict[str, Any],
    data_axes: dict[str, Any],
    truth_requirements: dict[str, Any],
    capability: dict[str, Any],
    input_profile: dict[str, Any],
    params_profile: Any,
    prefix: str,
) -> list[str]:
    errors: list[str] = []
    effective = set(input_profile.get("effective_extras", []))
    supported = set(capability.get("native_extras", []))
    supported.update(capability.get("derivable_extras", []))
    unsupported = sorted(effective.difference(supported))
    if unsupported:
        errors.append(f"{prefix}.input_profile.effective_extras unsupported: {unsupported}")

    requested = list(input_profile.get("requested_extras", []))
    resolved_params = (
        params_profile.get("resolved_base_params", {})
        if isinstance(params_profile, dict)
        else {}
    )
    input_ids = set(input_profile.get("required_inputs_satisfied", []))
    input_ids.update(input_profile.get("optional_inputs_provided", []))
    input_ids.update(input_profile.get("conditional_inputs_satisfied", []))
    errors.extend(
        f"{prefix}.input_profile: {message}"
        for message in validate_simulator_inputs(
            simulator_id=simulator_id,
            simulator_spec=spec,
            data_axes=data_axes,
            truth_requirements=truth_requirements,
            requested_extras=requested,
            simulator_params=resolved_params,
            input_ids=input_ids,
        )
    )
    return errors


def semantic_param_errors(
    *,
    spec: dict[str, Any],
    params_profile: dict[str, Any],
    prefix: str,
) -> list[str]:
    params_schema = spec.get("params", {})
    resolved = params_profile.get("resolved_base_params", {})
    cost_relevant = params_profile.get("cost_relevant_params", [])
    values = params_profile.get("cost_relevant_values", {})
    errors: list[str] = []
    if isinstance(cost_relevant, list):
        for path in cost_relevant:
            if not isinstance(path, str):
                continue
            errors.extend(
                validate_param_path(
                    path=path,
                    params_schema=params_schema,
                    prefix=f"{prefix}.params_profile.cost_relevant_params",
                )
            )
            actual = value_at_param_path(resolved, path)
            if isinstance(values, dict) and values.get(path) != actual:
                errors.append(
                    f"{prefix}.params_profile.cost_relevant_values['{path}'] does not match resolved_base_params."
                )
    return errors


def semantic_dimension_errors(
    *,
    spec: dict[str, Any],
    dimension_profile: dict[str, Any],
    prefix: str,
) -> list[str]:
    errors: list[str] = []
    params_schema = spec.get("params", {})
    cells_param = dimension_profile.get("cells_param")
    if isinstance(cells_param, str):
        errors.extend(
            validate_param_path(
                path=cells_param,
                params_schema=params_schema,
                prefix=f"{prefix}.dimension_profile.cells_param",
            )
        )
    elif isinstance(cells_param, dict):
        param = cells_param.get("param")
        if isinstance(param, str):
            errors.extend(
                validate_param_path(
                    path=param,
                    params_schema=params_schema,
                    prefix=f"{prefix}.dimension_profile.cells_param.param",
                )
            )
        multiplier_param = cells_param.get("multiplier_param")
        if isinstance(multiplier_param, str):
            errors.extend(
                validate_param_path(
                    path=multiplier_param,
                    params_schema=params_schema,
                    prefix=f"{prefix}.dimension_profile.cells_param.multiplier_param",
                )
            )
    genes_param = dimension_profile.get("genes_param")
    if isinstance(genes_param, str):
        errors.extend(
            validate_param_path(
                path=genes_param,
                params_schema=params_schema,
                prefix=f"{prefix}.dimension_profile.genes_param",
            )
        )
    elif isinstance(genes_param, dict):
        if "fixed" not in genes_param:
            for path in genes_param:
                errors.extend(
                    validate_param_path(
                        path=str(path),
                        params_schema=params_schema,
                        prefix=f"{prefix}.dimension_profile.genes_param",
                    )
                )
    return errors


def semantic_runtime_errors(
    *,
    spec: dict[str, Any],
    runtime_profile: dict[str, Any],
    profile: dict[str, Any],
    prefix: str,
) -> list[str]:
    threading = spec.get("runtime_resources", {}).get("threading", {})
    errors: list[str] = []
    if runtime_profile.get("threading_supported") != bool(threading.get("supported")):
        errors.append(f"{prefix}.runtime_resources_profile.threading_supported mismatch.")
    if runtime_profile.get("max_threads") != int(threading.get("max_threads", 1)):
        errors.append(f"{prefix}.runtime_resources_profile.max_threads mismatch.")
    max_threads = int(threading.get("max_threads", 1))
    supported = bool(threading.get("supported", False))
    benchmark_config = profile.get("benchmark_config", {})
    threads_tested = (
        benchmark_config.get("threads_tested")
        if isinstance(benchmark_config, dict)
        else None
    )
    if isinstance(threads_tested, list):
        for thread_idx, thread_value in enumerate(threads_tested, start=1):
            if not isinstance(thread_value, int) or isinstance(thread_value, bool):
                continue
            if thread_value > max_threads or (not supported and thread_value != 1):
                errors.append(
                    f"{prefix}.benchmark_config.threads_tested[{thread_idx}] "
                    "is incompatible with simulatorspec.runtime_resources.threading."
                )
    for point_idx, point in enumerate(profile.get("runtime_points", []), start=1):
        if not isinstance(point, dict):
            continue
        threads = point.get("threads")
        if (
            isinstance(threads, int)
            and not isinstance(threads, bool)
            and (threads > max_threads or (not supported and threads != 1))
        ):
            errors.append(
                f"{prefix}.runtime_points[{point_idx}].threads is incompatible "
                "with simulatorspec.runtime_resources.threading."
            )
    return errors


def semantic_feature_vector_errors(
    *,
    profile: dict[str, Any],
    config: dict[str, Any],
    input_profile: Any,
    prefix: str,
) -> list[str]:
    data_axes = config.get("data_axes", {})
    truth_requirements = config.get("truth_requirements", {})
    contexts = (
        truth_requirements.get("contexts", [])
        if isinstance(truth_requirements, dict)
        else []
    )
    requested_extras = (
        input_profile.get("requested_extras", [])
        if isinstance(input_profile, dict)
        else []
    )
    effective_extras = (
        input_profile.get("effective_extras", [])
        if isinstance(input_profile, dict)
        else []
    )
    expected_values = {
        "simulator_id": config.get("simulator_id"),
        "data_axes": data_axes,
        "truth_requirements": truth_requirements,
        "benchmark_profile_id": profile.get("profile_id"),
        "expression_profile": data_axes.get("resolution")
        if isinstance(data_axes, dict)
        else None,
        "column_kind": data_axes.get("column_kind")
        if isinstance(data_axes, dict)
        else None,
        "experimental_design": data_axes.get("experimental_design")
        if isinstance(data_axes, dict)
        else None,
        "truth_context_families": contexts,
        "truth_context_count": len(contexts) if isinstance(contexts, list) else None,
        "extras": effective_extras,
        "requested_extras": requested_extras,
        "effective_extras": effective_extras,
        "column_truth_requested": "column" in contexts
        if isinstance(contexts, list)
        else None,
    }
    point_fields = ("genes", "cells", "groups", "population_count", "threads", "ram_gb")
    errors: list[str] = []
    for point_idx, point in enumerate(profile.get("runtime_points", []), start=1):
        if not isinstance(point, dict):
            continue
        feature_vector = point.get("feature_vector")
        if not isinstance(feature_vector, dict):
            continue
        point_prefix = f"{prefix}.runtime_points[{point_idx}].feature_vector"
        for key, expected in expected_values.items():
            if feature_vector.get(key) != expected:
                errors.append(f"{point_prefix}.{key} does not match benchmark_config.")
        for key in point_fields:
            if feature_vector.get(key) != point.get(key):
                errors.append(f"{point_prefix}.{key} does not match runtime point.")
    return errors


def validate_param_path(
    *, path: str, params_schema: dict[str, Any], prefix: str
) -> list[str]:
    errors: list[str] = []
    current = params_schema
    consumed: list[str] = []
    for idx, part in enumerate(path.split(".")):
        if part not in current:
            location = ".".join(consumed) if consumed else "(root)"
            errors.append(f"{prefix} references unknown parameter path '{path}' at {location}/{part}.")
            return errors
        param_def = current[part]
        consumed.append(part)
        if idx == len(path.split(".")) - 1:
            return errors
        if not isinstance(param_def, dict) or param_def.get("type") != "object":
            errors.append(
                f"{prefix} references nested parameter path '{path}', but {'.'.join(consumed)} is not an object."
            )
            return errors
        properties = param_def.get("properties", {})
        if not isinstance(properties, dict):
            errors.append(
                f"{prefix} references nested parameter path '{path}', but {'.'.join(consumed)} has no properties."
            )
            return errors
        current = properties
    return errors


def value_at_param_path(params: Any, path: str) -> Any:
    current = params
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    schema = load_json(args.schema)
    if not isinstance(schema, dict):
        raise RuntimeError(f"Schema must be an object: {args.schema}")
    validator = build_validator(schema)
    cost_files = discover_cost_files(
        args.catalog_simulators_root,
        simulator_filters=args.simulator,
        require=args.require,
    )
    if not cost_files:
        print("No simulator cost.json files found; nothing to validate.")
        return 0

    invalid = 0
    for simulator_id, cost_path in cost_files:
        print(f"[{simulator_id}] validating {cost_path}")
        try:
            payload = load_json(cost_path)
            schema_errors = validate_instance(validator, payload)
            semantic_errors = (
                semantic_errors_for_cost(
                    simulator_id=simulator_id,
                    instance=payload,
                    catalog_simulators_root=args.catalog_simulators_root,
                )
                if isinstance(payload, dict)
                else ["cost.json must be an object"]
            )
            coverage_errors = (
                coverage_errors_for_cost(
                    simulator_id=simulator_id,
                    instance=payload,
                    catalog_simulators_root=args.catalog_simulators_root,
                )
                if isinstance(payload, dict)
                else []
            )
            errors = [*schema_errors, *semantic_errors, *coverage_errors]
            if errors:
                invalid += 1
                print(f"[{simulator_id}] invalid:")
                for error in errors:
                    print(f"  - {error}")
                if args.fail_fast:
                    break
            else:
                print(f"[{simulator_id}] valid")
        except Exception as exc:  # noqa: BLE001
            invalid += 1
            print(f"[{simulator_id}] invalid: {exc}")
            if args.fail_fast:
                break

    if invalid:
        print(f"Invalid simulator cost files: {invalid}", file=sys.stderr)
        return 1
    print(f"Checked {len(cost_files)} simulator cost file(s): all valid")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
