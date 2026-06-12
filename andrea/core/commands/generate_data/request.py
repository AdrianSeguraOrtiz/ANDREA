"""Request parsing and validation for generate-data."""

from __future__ import annotations

import copy
import csv
from pathlib import Path
from typing import Any

from andrea.core.shared.param_validation import (
    ParamValidationError,
    validate_param_value,
)
from andrea.core.shared.catalog_contracts import SIMULATION_EXTRA_IDS
from andrea.core.shared.compatibility_rules import (
    COMPATIBILITY_ACTIONS,
    compare_compatibility_values,
    condition_expected_value,
    match_compatibility_conditions,
)

from .catalog import (
    _load_simulator_catalog,
    get_profile_capability,
    load_simulation_input_specs,
)
from .shared import (
    PROFILE_SPECS,
    ResolvedSimulationPlan,
    ResolvedSimulatorRun,
    TAXONOMIC_GROUPS,
    _load_json_object,
    _validate_json_instance,
    required_truth_outputs_for_profile,
)

def _supported_requested_artifacts(
    profile_capability: dict[str, Any],
) -> tuple[set[str], set[str]]:
    native = set(profile_capability.get("native_extras", []))
    derivable = set(profile_capability.get("derivable_extras", []))
    truth_outputs = profile_capability.get("truth_outputs", {})
    if isinstance(truth_outputs, dict):
        native.update(
            key
            for key, mode in truth_outputs.items()
            if key in SIMULATION_EXTRA_IDS and mode == "native"
        )
        derivable.update(
            key
            for key, mode in truth_outputs.items()
            if key in SIMULATION_EXTRA_IDS and mode == "derivable"
        )
    return native, derivable


def _supported_native_outputs(
    profile_capability: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    supported: dict[str, dict[str, Any]] = {}
    for item in profile_capability.get("native_outputs", []):
        if not isinstance(item, dict):
            continue
        output_id = str(item.get("id", "")).strip()
        if output_id:
            supported[output_id] = item
    return supported


def _resolve_native_outputs(
    *,
    simulator_id: str,
    profile: str,
    profile_capability: dict[str, Any],
    requested_extras: list[str],
    simulator_params: dict[str, Any],
    raw_native_outputs: Any,
    label: str,
) -> list[str]:
    supported = _supported_native_outputs(profile_capability)
    if raw_native_outputs is None:
        return []
    if not isinstance(raw_native_outputs, list):
        raise ValueError(f"{label}.native_outputs must be an array when provided")

    resolved: list[str] = []
    seen: set[str] = set()
    for item in raw_native_outputs:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"{label}.native_outputs must contain non-empty string identifiers"
            )
        output_id = item.strip()
        if output_id in seen:
            continue
        seen.add(output_id)
        resolved.append(output_id)

    unsupported = sorted(set(resolved).difference(supported))
    if unsupported:
        raise ValueError(
            f"Simulator '{simulator_id}' does not support native outputs for profile '{profile}': "
            f"{unsupported}"
        )
    requested_extra_set = set(requested_extras)
    unavailable = [
        supported[output_id]
        for output_id in resolved
        if not _conditional_item_matches(
            supported[output_id],
            default_if_no_conditions=True,
            profile=profile,
            requested_extras=requested_extra_set,
            simulator_params=simulator_params,
        )
    ]
    if unavailable:
        messages = []
        for output_def in unavailable:
            output_id = str(output_def.get("id", "")).strip()
            message = str(
                output_def.get(
                    "message",
                    f"native output '{output_id}' is not available with the current configuration",
                )
            ).strip()
            messages.append(f"{output_id}: {message}")
        raise ValueError(
            f"Simulator '{simulator_id}' cannot produce selected native output(s) "
            f"for profile '{profile}': {'; '.join(messages)}"
        )
    return resolved


def _resolve_simulator_params(
    *,
    simulator_id: str,
    user_params: dict[str, Any],
    spec_params: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    resolved: dict[str, Any] = {}
    errors: list[str] = []

    unknown_keys = sorted(set(user_params.keys()).difference(spec_params.keys()))
    if unknown_keys:
        raise ValueError(
            f"[{simulator_id}] unknown simulator_params keys: {', '.join(unknown_keys)}"
        )

    for param_name, param_def in spec_params.items():
        if not isinstance(param_def, dict):
            errors.append(f"invalid param definition for '{param_name}'")
            continue
        if param_name in user_params:
            raw_value = user_params[param_name]
        else:
            raw_value = copy.deepcopy(param_def.get("default"))

        if raw_value is None:
            if bool(param_def.get("required")) and param_def.get("default") is None:
                errors.append(f"missing required simulator param: {param_name}")
            resolved[param_name] = None
            continue
        try:
            resolved[param_name] = validate_param_value(
                value=raw_value,
                param_def=param_def,
                path=f"{simulator_id}.{param_name}",
                warnings=warnings,
            )
        except ParamValidationError as exc:
            errors.append(str(exc))

    if errors:
        raise ValueError(
            f"[{simulator_id}] invalid simulator_params: {'; '.join(errors)}"
        )
    return resolved


def _resolve_inputs(
    raw_inputs: Any,
    *,
    base_dir: Path,
    known_input_ids: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    if raw_inputs is None:
        return {}, {}
    if not isinstance(raw_inputs, dict):
        raise ValueError("inputs must be an object mapping input id to input metadata")

    inputs: dict[str, dict[str, Any]] = {}
    resolved: dict[str, Path] = {}
    for input_id, raw_input in raw_inputs.items():
        if not isinstance(input_id, str) or not input_id:
            raise ValueError("inputs keys must be non-empty strings")
        if known_input_ids is not None and input_id not in known_input_ids:
            raise ValueError(f"unknown simulator input id: {input_id}")
        if isinstance(raw_input, dict):
            input_payload = dict(raw_input)
        else:
            raise ValueError(f"inputs.{input_id} must be an object with a path")
        raw_path = input_payload.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"inputs.{input_id}.path must be a non-empty path string")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        else:
            path = path.resolve()
        if not path.exists():
            raise ValueError(f"inputs.{input_id}.path does not exist: {path}")
        input_payload["path"] = raw_path
        inputs[input_id] = input_payload
        resolved[input_id] = path
    return inputs, resolved


def _param_lookup(params: dict[str, Any], path: str) -> Any:
    current: Any = params
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _compare_condition_value(actual: Any, op: str, expected: Any) -> bool:
    return compare_compatibility_values(
        actual=actual,
        op=op,
        expected=expected,
        coerce_numeric=True,
        allow_bool_numeric=True,
    )


def _condition_actual_value(
    field: str,
    *,
    profile: str,
    requested_extras: set[str],
    native_outputs: set[str],
    simulator_params: dict[str, Any],
) -> Any:
    if field == "profile":
        return profile
    if field == "requested_extra":
        return sorted(requested_extras)
    if field == "native_output":
        return sorted(native_outputs)
    if field.startswith("param."):
        return _param_lookup(simulator_params, field.removeprefix("param."))
    return None


def _conditional_item_matches(
    item: dict[str, Any],
    *,
    default_if_no_conditions: bool,
    profile: str,
    requested_extras: set[str],
    native_outputs: set[str] | None = None,
    simulator_params: dict[str, Any],
) -> bool:
    native_outputs = native_outputs or set()
    conditions = item.get("conditions", [])
    if not isinstance(conditions, list) or not conditions:
        return default_if_no_conditions
    for condition in conditions:
        if not isinstance(condition, dict):
            return False
        field = str(condition.get("field", "")).strip()
        op = str(condition.get("op", "")).strip()
        expected = condition.get("value")
        actual = _condition_actual_value(
            field,
            profile=profile,
            requested_extras=requested_extras,
            native_outputs=native_outputs,
            simulator_params=simulator_params,
        )
        if field == "requested_extra" and op in {"eq", "ne"}:
            matches = expected in requested_extras
            if op == "ne":
                matches = not matches
        elif field == "requested_extra" and op == "in":
            expected_values = set(expected if isinstance(expected, list) else [])
            matches = bool(expected_values.intersection(requested_extras))
        elif field == "requested_extra" and op == "not_in":
            expected_values = set(expected if isinstance(expected, list) else [])
            matches = not bool(expected_values.intersection(requested_extras))
        elif field == "native_output" and op in {"eq", "ne"}:
            matches = expected in native_outputs
            if op == "ne":
                matches = not matches
        elif field == "native_output" and op == "in":
            expected_values = set(expected if isinstance(expected, list) else [])
            matches = bool(expected_values.intersection(native_outputs))
        elif field == "native_output" and op == "not_in":
            expected_values = set(expected if isinstance(expected, list) else [])
            matches = not bool(expected_values.intersection(native_outputs))
        else:
            matches = _compare_condition_value(actual, op, expected)
        if not matches:
            return False
    return True


def _conditional_input_matches(
    requirement: dict[str, Any],
    *,
    profile: str,
    requested_extras: set[str],
    native_outputs: set[str] | None = None,
    simulator_params: dict[str, Any],
) -> bool:
    return _conditional_item_matches(
        requirement,
        default_if_no_conditions=False,
        profile=profile,
        requested_extras=requested_extras,
        native_outputs=native_outputs or set(),
        simulator_params=simulator_params,
    )


def validate_truth_parameter_requirements(
    *,
    profile_capability: dict[str, Any],
    profile: str,
    requested_extras: list[str],
    native_outputs: list[str] | None = None,
    simulator_params: dict[str, Any],
) -> list[str]:
    required_truth_outputs = set(required_truth_outputs_for_profile(profile))
    requested_extra_set = set(requested_extras)
    native_output_set = set(native_outputs or [])
    errors: list[str] = []
    for requirement in profile_capability.get("truth_parameter_requirements", []):
        if not isinstance(requirement, dict):
            continue
        truth_output = str(requirement.get("truth_output", "")).strip()
        if truth_output not in required_truth_outputs:
            continue
        if not _conditional_item_matches(
            requirement,
            default_if_no_conditions=True,
            profile=profile,
            requested_extras=requested_extra_set,
            native_outputs=native_output_set,
            simulator_params=simulator_params,
        ):
            errors.append(
                str(
                    requirement.get(
                        "message",
                        f"truth output '{truth_output}' is unavailable with the current simulator parameters",
                    )
                )
            )
    return errors


def _read_unique_gene_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"{path.name} must be a tabular file with a header")
        missing = {"target", "regulator"}.difference(reader.fieldnames)
        if missing:
            raise ValueError(
                f"{path.name} is missing required gene columns: "
                + ", ".join(sorted(missing))
            )
        genes: set[str] = set()
        for row in reader:
            for column in ("target", "regulator"):
                value = str(row.get(column, "")).strip()
                if value:
                    genes.add(value)
    if not genes:
        raise ValueError(f"{path.name} contains no target/regulator gene identifiers")
    return len(genes)


def _input_metric_value(
    *,
    field: str,
    resolved_input_paths: dict[str, Path] | None,
) -> Any:
    parts = field.split(".")
    if len(parts) != 3 or parts[0] != "input":
        raise ValueError(f"unsupported input compatibility field: {field}")
    input_id = parts[1]
    metric = parts[2]
    if metric != "unique_gene_count":
        raise ValueError(f"unsupported input compatibility metric: {field}")
    input_path = (resolved_input_paths or {}).get(input_id)
    if input_path is None:
        return None
    return _read_unique_gene_count(input_path)


def _compatibility_condition_value(
    *,
    field: str,
    profile: str,
    requested_extras: set[str],
    native_outputs: set[str],
    simulator_params: dict[str, Any],
    resolved_input_paths: dict[str, Path] | None,
) -> Any:
    if field.startswith("input."):
        return _input_metric_value(
            field=field,
            resolved_input_paths=resolved_input_paths,
        )
    return _condition_actual_value(
        field,
        profile=profile,
        requested_extras=requested_extras,
        native_outputs=native_outputs,
        simulator_params=simulator_params,
    )


def _compatibility_condition_matches(
    *,
    condition: dict[str, Any],
    profile: str,
    requested_extras: set[str],
    native_outputs: set[str],
    simulator_params: dict[str, Any],
    resolved_input_paths: dict[str, Path] | None,
) -> bool:
    field, op, expected = condition_expected_value(
        condition=condition,
        condition_label="compatibility condition",
        attribute_separator=" ",
        value_from_resolver=lambda value_from: _compatibility_condition_value(
            field=value_from,
            profile=profile,
            requested_extras=requested_extras,
            native_outputs=native_outputs,
            simulator_params=simulator_params,
            resolved_input_paths=resolved_input_paths,
        ),
    )
    actual = _compatibility_condition_value(
        field=field,
        profile=profile,
        requested_extras=requested_extras,
        native_outputs=native_outputs,
        simulator_params=simulator_params,
        resolved_input_paths=resolved_input_paths,
    )
    if field == "requested_extra" and op in {"eq", "ne"}:
        matches = expected in requested_extras
        return not matches if op == "ne" else matches
    if field == "requested_extra" and op in {"in", "not_in"}:
        expected_values = set(expected if isinstance(expected, list) else [])
        matches = bool(expected_values.intersection(requested_extras))
        return not matches if op == "not_in" else matches
    if field == "native_output" and op in {"eq", "ne"}:
        matches = expected in native_outputs
        return not matches if op == "ne" else matches
    if field == "native_output" and op in {"in", "not_in"}:
        expected_values = set(expected if isinstance(expected, list) else [])
        matches = bool(expected_values.intersection(native_outputs))
        return not matches if op == "not_in" else matches
    return _compare_condition_value(actual, op, expected)


def _compatibility_rule_matches(
    *,
    rule: dict[str, Any],
    profile: str,
    requested_extras: set[str],
    native_outputs: set[str],
    simulator_params: dict[str, Any],
    resolved_input_paths: dict[str, Path] | None,
) -> bool:
    def _condition_matches(condition: dict[str, Any], _index: int) -> bool:
        return _compatibility_condition_matches(
            condition=condition,
            profile=profile,
            requested_extras=requested_extras,
            native_outputs=native_outputs,
            simulator_params=simulator_params,
            resolved_input_paths=resolved_input_paths,
        )

    return match_compatibility_conditions(
        rule=rule,
        condition_matcher=_condition_matches,
        empty_conditions_message="compatibility rule must include non-empty conditions",
        non_object_condition_message=lambda _index: (
            "compatibility rule conditions must be objects"
        ),
    )


def collect_simulator_compatibility_rule_issues(
    *,
    simulator_id: str,
    simulator_spec: dict[str, Any],
    profile: str,
    requested_extras: list[str],
    simulator_params: dict[str, Any],
    native_outputs: list[str] | None = None,
    resolved_input_paths: dict[str, Path] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    raw_rules = simulator_spec.get("compatibility_rules", [])
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        return [], [], ["simulatorspec.compatibility_rules must be an array"]

    requested_extra_set = set(requested_extras)
    native_output_set = set(native_outputs or [])
    blocking: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    for index, rule in enumerate(raw_rules, start=1):
        if not isinstance(rule, dict):
            errors.append(f"compatibility_rules[{index}] must be an object")
            continue
        action = str(rule.get("action", "")).strip()
        message = str(rule.get("message", "")).strip()
        if action not in COMPATIBILITY_ACTIONS:
            errors.append(f"compatibility_rules[{index}].action is invalid")
            continue
        if not message:
            errors.append(f"compatibility_rules[{index}].message is required")
            continue
        try:
            matches = _compatibility_rule_matches(
                rule=rule,
                profile=profile,
                requested_extras=requested_extra_set,
                native_outputs=native_output_set,
                simulator_params=simulator_params,
                resolved_input_paths=resolved_input_paths,
            )
        except ValueError as exc:
            errors.append(f"compatibility_rules[{index}]: {exc}")
            continue
        if not matches:
            continue
        if action == "block":
            blocking.append(message)
        else:
            warnings.append(f"[{simulator_id}] {message}")
    return (
        list(dict.fromkeys(blocking)),
        list(dict.fromkeys(warnings)),
        list(dict.fromkeys(errors)),
    )


def validate_simulator_inputs(
    *,
    simulator_id: str,
    simulator_spec: dict[str, Any],
    profile: str,
    requested_extras: list[str],
    simulator_params: dict[str, Any],
    native_outputs: list[str] | None = None,
    input_ids: set[str],
) -> list[str]:
    extra_inputs = simulator_spec.get("extra_inputs", {})
    required = extra_inputs.get("required", [])
    conditional_required = extra_inputs.get("conditional_required", [])

    errors: list[str] = []
    for item in required:
        if isinstance(item, dict):
            input_id = str(item.get("input", ""))
            if input_id and input_id not in input_ids:
                errors.append(f"missing required input file '{input_id}'")

    requested_extra_set = set(requested_extras)
    native_output_set = set(native_outputs or [])
    for requirement in conditional_required:
        if not isinstance(requirement, dict):
            continue
        input_id = str(requirement.get("input", ""))
        if not input_id:
            continue
        if (
            _conditional_input_matches(
                requirement,
                profile=profile,
                requested_extras=requested_extra_set,
                native_outputs=native_output_set,
                simulator_params=simulator_params,
            )
            and input_id not in input_ids
        ):
            errors.append(
                str(
                    requirement.get(
                        "message",
                        f"missing conditionally required input file '{input_id}'",
                    )
                )
            )
    return errors


def simulator_input_warnings(
    *,
    simulator_spec: dict[str, Any],
    input_ids: set[str],
) -> list[str]:
    extra_inputs = simulator_spec.get("extra_inputs", {})
    optional = extra_inputs.get("optional", [])
    warnings: list[str] = []
    for item in optional:
        if not isinstance(item, dict):
            continue
        input_id = str(item.get("input", "")).strip()
        if input_id and input_id not in input_ids:
            warnings.append(f"optional input not provided: {input_id}")
    return warnings


def resolve_simulator_runtime_resources(
    *,
    simulator_id: str,
    simulator_spec: dict[str, Any],
    raw_resources: Any = None,
) -> dict[str, Any]:
    threading = (
        simulator_spec.get("runtime_resources", {})
        .get("threading", {})
        if isinstance(simulator_spec.get("runtime_resources"), dict)
        else {}
    )
    supported = bool(threading.get("supported", False))
    default_threads = int(threading.get("default_threads", 1))
    max_threads = int(threading.get("max_threads", default_threads))
    if default_threads < 1 or max_threads < 1 or default_threads > max_threads:
        raise ValueError(
            f"[{simulator_id}] invalid runtime_resources.threading defaults"
        )

    if raw_resources is None:
        threads = default_threads
    else:
        if not isinstance(raw_resources, dict):
            raise ValueError(
                f"[{simulator_id}] runtime_resources must be an object"
            )
        raw_threads = raw_resources.get("threads")
        if raw_threads is None:
            raise ValueError(f"[{simulator_id}] runtime_resources.threads is required")
        if isinstance(raw_threads, bool) or not isinstance(raw_threads, int):
            raise ValueError(
                f"[{simulator_id}] runtime_resources.threads must be an integer"
            )
        threads = int(raw_threads)

    if threads < 1:
        raise ValueError(f"[{simulator_id}] runtime_resources.threads must be >= 1")
    if not supported and threads != 1:
        raise ValueError(
            f"[{simulator_id}] does not support threaded execution; runtime_resources.threads must be 1"
        )
    if threads > max_threads:
        raise ValueError(
            f"[{simulator_id}] runtime_resources.threads must be <= {max_threads}"
        )
    return {"threads": threads}


def _validate_organism(payload: dict[str, Any]) -> None:
    organism_keys = set(payload)
    expected_organism_keys = {"taxonomic_group", "ncbi_taxon_id"}
    if organism_keys != expected_organism_keys:
        raise ValueError(
            "organism must contain exactly taxonomic_group and ncbi_taxon_id"
        )
    taxonomic_group = str(payload.get("taxonomic_group", "")).strip()
    if taxonomic_group not in TAXONOMIC_GROUPS:
        raise ValueError(
            "organism.taxonomic_group must be one of: "
            + ", ".join(sorted(TAXONOMIC_GROUPS))
        )
    ncbi_taxon_id = payload.get("ncbi_taxon_id")
    if ncbi_taxon_id is not None and (
        not isinstance(ncbi_taxon_id, int)
        or isinstance(ncbi_taxon_id, bool)
        or ncbi_taxon_id < 1
    ):
        raise ValueError("organism.ncbi_taxon_id must be null or integer >= 1")
    if taxonomic_group not in {"synthetic", "unknown"} and ncbi_taxon_id is None:
        raise ValueError(
            "organism.ncbi_taxon_id must be integer >= 1 for biological taxonomic groups"
        )


def _validate_common_plan_fields(
    payload: dict[str, Any],
    *,
    label: str,
) -> tuple[str, dict[str, Any], list[str], list[str], int | None]:
    profile = str(payload.get("profile", "")).strip()
    if profile not in PROFILE_SPECS:
        raise ValueError(f"Unknown benchmark profile: {profile}")

    requested_extras = list(payload.get("requested_extras", []))
    if any(extra not in SIMULATION_EXTRA_IDS for extra in requested_extras):
        unsupported = sorted(set(requested_extras).difference(SIMULATION_EXTRA_IDS))
        raise ValueError(f"Unknown requested_extras: {unsupported}")

    profile_required = set(PROFILE_SPECS[profile].required_extras)
    effective_extras = sorted(set(requested_extras).union(profile_required))

    organism = payload.get("organism")
    if not isinstance(organism, dict):
        raise ValueError(f"{label}.organism must be an object")
    _validate_organism(organism)

    base_seed = payload.get("base_seed")
    if base_seed is not None and (not isinstance(base_seed, int)):
        raise ValueError(f"{label}.base_seed must be integer when provided")
    if base_seed is not None and int(base_seed) < 1:
        raise ValueError(f"{label}.base_seed must be >= 1")

    return profile, organism, requested_extras, effective_extras, base_seed


def _resolve_simulator_run(
    *,
    request_id: str,
    profile: str,
    run_id: str,
    organism: dict[str, Any],
    requested_extras: list[str],
    effective_extras: list[str],
    inputs: dict[str, dict[str, Any]],
    resolved_input_paths: dict[str, Path],
    run_payload: dict[str, Any],
    notes: str | None,
    catalog: dict[str, dict[str, Any]],
) -> ResolvedSimulatorRun:
    simulator_id = str(run_payload.get("simulator_id", "")).strip()
    if simulator_id not in catalog:
        raise ValueError(f"Unknown simulator_id in simulation-plan: {simulator_id}")
    simulator_spec = catalog[simulator_id]

    profile_capability = get_profile_capability(simulator_spec, profile)
    if profile_capability is None:
        raise ValueError(
            f"Simulator '{simulator_id}' does not support profile '{profile}'"
        )
    truth_outputs = profile_capability.get("truth_outputs", {})
    missing_truth = [
        output_id
        for output_id in required_truth_outputs_for_profile(profile)
        if truth_outputs.get(output_id) not in {"native", "derivable"}
    ]
    if missing_truth:
        raise ValueError(
            f"Simulator '{simulator_id}' cannot satisfy required truth outputs for "
            f"profile '{profile}': {missing_truth}"
        )

    native, derivable = _supported_requested_artifacts(profile_capability)
    supported_extras = native.union(derivable)
    unsupported_requested = sorted(set(requested_extras).difference(supported_extras))
    if unsupported_requested:
        raise ValueError(
            f"Simulator '{simulator_id}' does not support requested extras for profile '{profile}': "
            f"{unsupported_requested}"
        )

    profile_required = set(PROFILE_SPECS[profile].required_extras)
    missing_profile_support = sorted(profile_required.difference(supported_extras))
    if missing_profile_support:
        raise ValueError(
            f"Simulator '{simulator_id}' cannot satisfy required extras for profile '{profile}': "
            f"{missing_profile_support}"
        )

    raw_simulator_params = run_payload.get("simulator_params", {})
    if not isinstance(raw_simulator_params, dict):
        raise ValueError(
            f"simulation-plan.runs[{run_id}].simulator_params must be an object"
        )
    resolved_params = _resolve_simulator_params(
        simulator_id=simulator_id,
        user_params=raw_simulator_params,
        spec_params=simulator_spec.get("params", {}),
    )
    truth_parameter_errors = validate_truth_parameter_requirements(
        profile_capability=profile_capability,
        profile=profile,
        requested_extras=requested_extras,
        simulator_params=resolved_params,
    )
    if truth_parameter_errors:
        raise ValueError(
            f"[{simulator_id}] invalid truth output parameters: "
            + "; ".join(truth_parameter_errors)
        )
    runtime_resources = resolve_simulator_runtime_resources(
        simulator_id=simulator_id,
        simulator_spec=simulator_spec,
        raw_resources=run_payload.get("runtime_resources"),
    )
    native_outputs = _resolve_native_outputs(
        simulator_id=simulator_id,
        profile=profile,
        profile_capability=profile_capability,
        requested_extras=requested_extras,
        simulator_params=resolved_params,
        raw_native_outputs=run_payload.get("native_outputs"),
        label=f"simulation-plan.runs[{run_id}]",
    )

    input_errors = validate_simulator_inputs(
        simulator_id=simulator_id,
        simulator_spec=simulator_spec,
        profile=profile,
        requested_extras=requested_extras,
        simulator_params=resolved_params,
        native_outputs=native_outputs,
        input_ids=set(inputs),
    )
    if input_errors:
        raise ValueError(
            f"[{simulator_id}] invalid inputs: {'; '.join(input_errors)}"
        )

    compatibility_blocks, _compatibility_warnings, compatibility_errors = (
        collect_simulator_compatibility_rule_issues(
            simulator_id=simulator_id,
            simulator_spec=simulator_spec,
            profile=profile,
            requested_extras=requested_extras,
            simulator_params=resolved_params,
            native_outputs=native_outputs,
            resolved_input_paths=resolved_input_paths,
        )
    )
    if compatibility_errors:
        raise ValueError(
            f"[{simulator_id}] invalid compatibility rules: "
            + "; ".join(compatibility_errors)
        )
    if compatibility_blocks:
        raise ValueError(
            f"[{simulator_id}] incompatible simulator parameters: "
            + "; ".join(compatibility_blocks)
        )

    simulator_seed_base = run_payload.get("base_seed")
    if simulator_seed_base is None:
        raise ValueError(f"simulation-plan.runs[{run_id}].base_seed is required")
    if simulator_seed_base is not None and not isinstance(simulator_seed_base, int):
        raise ValueError(
            f"simulation-plan.runs[{run_id}].base_seed must be integer when provided"
        )
    if int(simulator_seed_base) < 1:
        raise ValueError(f"simulation-plan.runs[{run_id}].base_seed must be >= 1")

    replicates = int(run_payload.get("replicates", 0))
    if replicates < 1:
        raise ValueError(f"simulation-plan.runs[{run_id}].replicates must be >= 1")

    replicate_seeds = run_payload.get("replicate_seeds", [])
    if not isinstance(replicate_seeds, list) or not replicate_seeds:
        raise ValueError(
            f"simulation-plan.runs[{run_id}].replicate_seeds must be a non-empty array"
        )
    if any(not isinstance(seed, int) or seed < 1 for seed in replicate_seeds):
        raise ValueError(
            f"simulation-plan.runs[{run_id}].replicate_seeds must contain integers >= 1"
        )
    if len(replicate_seeds) != replicates:
        raise ValueError(
            f"simulation-plan.runs[{run_id}].replicate_seeds must contain "
            f"{replicates} seed(s)"
        )

    return ResolvedSimulatorRun(
        request_id=request_id,
        profile=profile,
        run_id=run_id,
        simulator_id=simulator_id,
        organism=organism,
        requested_extras=requested_extras,
        effective_extras=effective_extras,
        inputs=inputs,
        resolved_input_paths=resolved_input_paths,
        simulator_params=resolved_params,
        runtime_resources=runtime_resources,
        native_outputs=native_outputs,
        replicates=replicates,
        base_seed=int(simulator_seed_base),
        replicate_seeds=[int(seed) for seed in replicate_seeds],
        notes=run_payload.get("notes") or notes,
        simulator_spec=simulator_spec,
    )


def validate_simulation_plan_payload(
    plan_payload: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> ResolvedSimulationPlan:
    schemas, catalog = _load_simulator_catalog()
    _validate_json_instance(
        instance=plan_payload,
        schema=schemas["simulation_plan"],
        label="simulation-plan",
    )

    profile, organism, requested_extras, effective_extras, base_seed = (
        _validate_common_plan_fields(plan_payload, label="simulation-plan")
    )
    raw_inputs = plan_payload.get("inputs", {})
    input_specs = load_simulation_input_specs()
    inputs, resolved_input_paths = _resolve_inputs(
        raw_inputs,
        base_dir=base_dir or Path.cwd(),
        known_input_ids=set(input_specs),
    )

    run_payloads = plan_payload.get("runs", [])
    simulator_runs = [
        _resolve_simulator_run(
            request_id=str(plan_payload["id"]),
            profile=profile,
            run_id=str(run_payload["run_id"]),
            organism=organism,
            requested_extras=requested_extras,
            effective_extras=effective_extras,
            inputs=inputs,
            resolved_input_paths=resolved_input_paths,
            run_payload=run_payload,
            notes=plan_payload.get("notes"),
            catalog=catalog,
        )
        for run_payload in run_payloads
    ]

    seen_runs: set[str] = set()
    duplicate_runs: set[str] = set()
    for run in simulator_runs:
        if run.run_id in seen_runs:
            duplicate_runs.add(run.run_id)
        seen_runs.add(run.run_id)
    if duplicate_runs:
        raise ValueError(
            "simulation-plan.runs must not contain duplicate run_id values: "
            + ", ".join(sorted(duplicate_runs))
        )

    tasks = list(plan_payload.get("tasks", []))
    task_run_ids = {str(task.get("run_id")) for task in tasks if isinstance(task, dict)}
    unknown_task_runs = sorted(task_run_ids.difference(seen_runs))
    if unknown_task_runs:
        raise ValueError(
            "simulation-plan.tasks reference unknown run_id values: "
            + ", ".join(unknown_task_runs)
        )
    expected_task_count = sum(run.replicates for run in simulator_runs)
    if len(tasks) != expected_task_count:
        raise ValueError(
            f"simulation-plan.tasks must contain {expected_task_count} tasks "
            f"(sum of per-run replicates across {len(simulator_runs)} runs)"
        )
    task_ids = [str(task.get("task_id")) for task in tasks if isinstance(task, dict)]
    duplicate_tasks = sorted(
        {task_id for task_id in task_ids if task_ids.count(task_id) > 1}
    )
    if duplicate_tasks:
        raise ValueError(
            "simulation-plan.tasks must not contain duplicate task_id values: "
            + ", ".join(duplicate_tasks)
        )

    runs_by_id = {run.run_id: run for run in simulator_runs}
    seen_replicates_by_run: dict[str, set[int]] = {
        run.run_id: set() for run in simulator_runs
    }
    for task in tasks:
        run_id = str(task.get("run_id"))
        run = runs_by_id[run_id]
        replicate_index = int(task.get("replicate_index", 0))
        if replicate_index < 1 or replicate_index > run.replicates:
            raise ValueError(
                f"simulation-plan.tasks[{task.get('task_id')}].replicate_index "
                f"must be between 1 and {run.replicates}"
            )
        expected_seed = run.replicate_seeds[replicate_index - 1]
        if int(task.get("seed", 0)) != expected_seed:
            raise ValueError(
                f"simulation-plan.tasks[{task.get('task_id')}].seed must match "
                f"runs[{run_id}].replicate_seeds[{replicate_index - 1}]"
            )
        if dict(task.get("runtime_resources", {})) != run.runtime_resources:
            raise ValueError(
                f"simulation-plan.tasks[{task.get('task_id')}].runtime_resources "
                f"must match runs[{run_id}].runtime_resources"
            )
        seen_replicates_by_run[run_id].add(replicate_index)
    missing_replicates = {
        run_id: sorted(set(range(1, run.replicates + 1)).difference(indices))
        for run_id, indices in seen_replicates_by_run.items()
        for run in [runs_by_id[run_id]]
        if len(indices) != run.replicates
    }
    if missing_replicates:
        details = "; ".join(
            f"{run_id}: {indices}"
            for run_id, indices in sorted(missing_replicates.items())
        )
        raise ValueError(f"simulation-plan.tasks missing replicate indexes: {details}")

    execution = dict(plan_payload.get("execution", {}))

    return ResolvedSimulationPlan(
        request_id=str(plan_payload["id"]),
        profile=profile,
        organism=organism,
        requested_extras=requested_extras,
        effective_extras=effective_extras,
        inputs=inputs,
        resolved_input_paths=resolved_input_paths,
        base_seed=base_seed,
        notes=plan_payload.get("notes"),
        simulator_runs=simulator_runs,
        tasks=tasks,
        execution=execution,
        plan_payload=plan_payload,
    )


def validate_simulation_plan(plan_path: Path) -> ResolvedSimulationPlan:
    plan_payload = _load_json_object(plan_path, "simulation-plan")
    return validate_simulation_plan_payload(
        plan_payload,
        base_dir=plan_path.resolve().parent,
    )
