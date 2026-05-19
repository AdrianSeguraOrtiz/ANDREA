"""Validate tool cost.json files against the tool cost JSON Schema.

Usage examples:
1) Validate every cost profile:
   python validate_tool_costs.py

2) Validate only selected tools:
   python validate_tool_costs.py --tool genie3 --tool scmtni

Exit codes:
- 0: all selected cost profiles are valid
- 1: one or more cost profiles are invalid / unreadable
- 2: usage/runtime error (missing schema, unknown tool ids, etc.)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from shared.catalog_tools import (
    CATALOG_ROOT,
    DEFAULT_CATALOG_TOOLS_ROOT,
    discover_catalog_tool_dirs,
    load_json,
    load_toolspec,
    select_tools,
)

DEFAULT_SCHEMA_PATH = CATALOG_ROOT / "schemas" / "toolcost.schema.json"


@dataclass(frozen=True)
class ValidationCounters:
    valid: int = 0
    invalid: int = 0

    @property
    def checked(self) -> int:
        return self.valid + self.invalid


def to_json_pointer(error: ValidationError) -> str:
    if not error.path:
        return "(root)"
    return "/" + "/".join(str(part) for part in error.path)


def build_validator(schema: Any) -> Draft202012Validator:
    try:
        validator = Draft202012Validator(schema)
        validator.check_schema(schema)
    except SchemaError as exc:
        raise RuntimeError(f"Invalid JSON Schema: {exc.message}") from exc
    return validator


def validate_instance(
    validator: Draft202012Validator,
    instance: Any,
) -> list[ValidationError]:
    return sorted(validator.iter_errors(instance), key=lambda err: list(err.path))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate every catalog cost.json against toolcost.schema.json."
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to tool cost schema. Default: {DEFAULT_SCHEMA_PATH}",
    )
    parser.add_argument(
        "--catalog-tools-root",
        type=Path,
        default=DEFAULT_CATALOG_TOOLS_ROOT,
        help=f"Path to catalog tools directory. Default: {DEFAULT_CATALOG_TOOLS_ROOT}",
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        help="Tool id to validate (repeatable). If omitted, validates all cost.json files.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first invalid cost profile.",
    )
    return parser.parse_args(argv)


def discover_cost_files(catalog_tools_root: Path) -> list[tuple[str, Path]]:
    return [
        (tool_id, tool_dir / "cost.json")
        for tool_id, tool_dir in discover_catalog_tool_dirs(
            catalog_tools_root, required_filename="cost.json"
        )
    ]


def discover_input_keys(input_specs_root: Path) -> set[str]:
    if not input_specs_root.exists() or not input_specs_root.is_dir():
        raise RuntimeError(f"Invalid input specs root: {input_specs_root}")
    return {
        path.stem
        for path in input_specs_root.iterdir()
        if path.is_file() and path.suffix == ".json"
    }


def _input_entries(toolspec: dict[str, Any], field: str) -> set[str]:
    extra_inputs = toolspec.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return set()
    raw_entries = extra_inputs.get(field, [])
    if not isinstance(raw_entries, list):
        return set()
    out: set[str] = set()
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        input_key = str(entry.get("input", "")).strip()
        if input_key:
            out.add(input_key)
    return out


def _conditional_rules(toolspec: dict[str, Any]) -> list[dict[str, Any]]:
    extra_inputs = toolspec.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return []
    raw_rules = extra_inputs.get("conditional_required", [])
    if not isinstance(raw_rules, list):
        return []
    return [rule for rule in raw_rules if isinstance(rule, dict)]


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        str(item).strip() for item in value if isinstance(item, str) and item.strip()
    }


def _compare_values(*, actual: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return isinstance(expected, list) and actual in expected
    if op == "not_in":
        return isinstance(expected, list) and actual not in expected

    if (
        isinstance(actual, bool)
        or isinstance(expected, bool)
        or not isinstance(actual, (int, float))
        or not isinstance(expected, (int, float))
    ):
        return False

    actual_num = float(actual)
    expected_num = float(expected)
    if op == "gt":
        return actual_num > expected_num
    if op == "gte":
        return actual_num >= expected_num
    if op == "lt":
        return actual_num < expected_num
    if op == "lte":
        return actual_num <= expected_num
    return False


def _resolved_param_value(
    *,
    param_name: str,
    resolved_params: dict[str, Any],
    toolspec: dict[str, Any],
) -> Any:
    if param_name in resolved_params:
        return resolved_params.get(param_name)
    params = toolspec.get("params", {})
    if isinstance(params, dict):
        param_def = params.get(param_name)
        if isinstance(param_def, dict):
            return param_def.get("default")
    return None


def _conditional_rule_matches(
    *,
    rule: dict[str, Any],
    toolspec: dict[str, Any],
    resolved_params: dict[str, Any],
    execution_profile: dict[str, Any],
) -> bool:
    param_name = str(rule.get("param", "")).strip()
    execution_name = str(rule.get("execution", "")).strip()
    op = str(rule.get("op", "")).strip()
    if param_name:
        actual = _resolved_param_value(
            param_name=param_name,
            resolved_params=resolved_params,
            toolspec=toolspec,
        )
    elif execution_name:
        actual = execution_profile.get(execution_name)
    else:
        return False
    return _compare_values(actual=actual, op=op, expected=rule.get("value"))


def _check_param_key_compatibility(
    *,
    payload: dict[str, Any],
    schema: dict[str, Any],
    path: str,
    prefix: str,
    errors: list[str],
) -> None:
    unknown = sorted(set(payload.keys()).difference(schema.keys()))
    for key in unknown:
        errors.append(
            f"{prefix}.params_profile.resolved_params{path}/{key} does not exist in toolspec.params."
        )

    for key, value in payload.items():
        param_def = schema.get(key)
        if not isinstance(param_def, dict):
            continue
        if param_def.get("type") != "object" or not isinstance(value, dict):
            continue
        properties = param_def.get("properties", {})
        if not isinstance(properties, dict):
            continue
        _check_param_key_compatibility(
            payload=value,
            schema=properties,
            path=f"{path}/{key}",
            prefix=prefix,
            errors=errors,
        )


def _validate_param_path(
    *,
    path: str,
    params_schema: dict[str, Any],
    prefix: str,
    errors: list[str],
) -> None:
    current = params_schema
    consumed: list[str] = []
    parts = path.split(".")
    for idx, part in enumerate(parts):
        if not part:
            errors.append(
                f"{prefix}.cost_relevant_params contains empty path segment: {path}"
            )
            return
        if part not in current:
            location = ".".join(consumed) if consumed else "(root)"
            errors.append(
                f"{prefix}.cost_relevant_params references unknown parameter path '{path}' at {location}/{part}."
            )
            return
        param_def = current[part]
        consumed.append(part)
        if idx == len(parts) - 1:
            return
        if not isinstance(param_def, dict) or param_def.get("type") != "object":
            errors.append(
                f"{prefix}.cost_relevant_params references nested path '{path}', but {'.'.join(consumed)} is not an object parameter."
            )
            return
        properties = param_def.get("properties", {})
        if not isinstance(properties, dict):
            errors.append(
                f"{prefix}.cost_relevant_params references nested path '{path}', but {'.'.join(consumed)} has no object properties."
            )
            return
        current = properties


def _value_at_param_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def semantic_errors_for_cost(
    *,
    tool_id: str,
    instance: Any,
    catalog_tools_root: Path,
    known_input_keys: set[str],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(instance, dict):
        errors.append("Cost profile root must be a JSON object.")
        return errors

    profiles = instance.get("profiles")
    if not isinstance(profiles, list):
        return errors

    toolspec = load_toolspec(catalog_tools_root, tool_id)
    execution_capabilities = toolspec.get("execution_capabilities", [])
    execution_modes = (
        set(execution_capabilities)
        if isinstance(execution_capabilities, list)
        else set()
    )
    required_inputs = _input_entries(toolspec, "required")
    optional_inputs = _input_entries(toolspec, "optional")
    conditional_rules = _conditional_rules(toolspec)
    conditional_inputs = {
        str(rule.get("input", "")).strip()
        for rule in conditional_rules
        if str(rule.get("input", "")).strip()
    }
    declared_inputs = required_inputs | optional_inputs | conditional_inputs

    profile_ids: set[str] = set()
    for idx, profile in enumerate(profiles, start=1):
        if not isinstance(profile, dict):
            continue
        profile_prefix = f"profiles[{idx}]"
        profile_id = str(profile.get("profile_id", "")).strip()
        if profile_id in profile_ids:
            errors.append(f"{profile_prefix}.profile_id is duplicated: {profile_id}")
        elif profile_id:
            profile_ids.add(profile_id)

        benchmark_config = profile.get("benchmark_config")
        if not isinstance(benchmark_config, dict):
            continue

        params_profile = benchmark_config.get("params_profile")
        if not isinstance(params_profile, dict):
            continue

        source = params_profile.get("source")
        override_file = params_profile.get("override_file")
        if source == "toolspec_defaults" and override_file is not None:
            errors.append(
                f"{profile_prefix}.benchmark_config.params_profile.override_file must be null when source='toolspec_defaults'."
            )
        if source == "toolspec_defaults_plus_override" and (
            not isinstance(override_file, str) or not override_file.strip()
        ):
            errors.append(
                f"{profile_prefix}.benchmark_config.params_profile.override_file must be a non-empty string when source='toolspec_defaults_plus_override'."
            )

        resolved_params = params_profile.get("resolved_params")
        if not isinstance(resolved_params, dict):
            continue

        raw_params = toolspec.get("params", {})
        params_schema = raw_params if isinstance(raw_params, dict) else {}
        _check_param_key_compatibility(
            payload=resolved_params,
            schema=params_schema,
            path="",
            prefix=f"{profile_prefix}.benchmark_config",
            errors=errors,
        )

        cost_relevant_params = params_profile.get("cost_relevant_params")
        if isinstance(cost_relevant_params, list):
            for param_path in cost_relevant_params:
                if not isinstance(param_path, str) or not param_path.strip():
                    continue
                _validate_param_path(
                    path=param_path.strip(),
                    params_schema=params_schema,
                    prefix=f"{profile_prefix}.benchmark_config.params_profile",
                    errors=errors,
                )
        cost_relevant_values = params_profile.get("cost_relevant_values")
        if isinstance(cost_relevant_params, list) and isinstance(
            cost_relevant_values, dict
        ):
            expected_paths = {
                str(item).strip()
                for item in cost_relevant_params
                if isinstance(item, str) and str(item).strip()
            }
            actual_paths = {
                str(key).strip() for key in cost_relevant_values if str(key).strip()
            }
            missing_values = sorted(expected_paths.difference(actual_paths))
            if missing_values:
                errors.append(
                    f"{profile_prefix}.benchmark_config.params_profile.cost_relevant_values is missing path(s): {missing_values}."
                )
            extra_values = sorted(actual_paths.difference(expected_paths))
            if extra_values:
                errors.append(
                    f"{profile_prefix}.benchmark_config.params_profile.cost_relevant_values contains path(s) not listed in cost_relevant_params: {extra_values}."
                )
            for param_path in sorted(expected_paths.intersection(actual_paths)):
                expected_value = _value_at_param_path(resolved_params, param_path)
                actual_value = cost_relevant_values.get(param_path)
                if actual_value != expected_value:
                    errors.append(
                        f"{profile_prefix}.benchmark_config.params_profile.cost_relevant_values['{param_path}'] does not match resolved_params."
                    )

        execution_profile = benchmark_config.get("execution_profile")
        if not isinstance(execution_profile, dict):
            continue
        mode = str(execution_profile.get("mode", "")).strip()
        if mode not in execution_modes:
            errors.append(
                f"{profile_prefix}.benchmark_config.execution_profile.mode must be one of this tool's execution_capabilities: {sorted(execution_modes)}."
            )

        physical_task_policy = str(
            execution_profile.get("physical_task_policy", "")
        ).strip()
        expected_policy = {
            "global": "single",
            "group_native": "native_grouped",
            "group_emulated": "andrea_group_emulated",
            "cell_native": "cell_native",
            "group_aggregated": "andrea_group_aggregated",
        }.get(mode)
        if expected_policy is not None and physical_task_policy != expected_policy:
            errors.append(
                f"{profile_prefix}.benchmark_config.execution_profile.physical_task_policy must be '{expected_policy}' when mode='{mode}'."
            )

        group_count = execution_profile.get("group_count")
        if mode in {"global", "cell_native"} and group_count != 0:
            errors.append(
                f"{profile_prefix}.benchmark_config.execution_profile.group_count must be 0 when mode='{mode}'."
            )
        if mode in {"group_native", "group_emulated", "group_aggregated"} and (
            not isinstance(group_count, int)
            or isinstance(group_count, bool)
            or group_count < 1
        ):
            errors.append(
                f"{profile_prefix}.benchmark_config.execution_profile.group_count must be >= 1 when mode='{mode}'."
            )

        input_profile = benchmark_config.get("input_profile")
        if not isinstance(input_profile, dict):
            continue
        input_group_count = input_profile.get("group_count")
        if input_group_count != group_count:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.group_count must match execution_profile.group_count."
            )

        extras_provided = _string_set(input_profile.get("extras_provided"))
        required_satisfied = _string_set(input_profile.get("required_inputs_satisfied"))
        optional_provided = _string_set(input_profile.get("optional_inputs_provided"))
        conditional_satisfied = _string_set(
            input_profile.get("conditional_inputs_satisfied")
        )
        profile_input_sets = {
            "extras_provided": extras_provided,
            "required_inputs_satisfied": required_satisfied,
            "optional_inputs_provided": optional_provided,
            "conditional_inputs_satisfied": conditional_satisfied,
        }
        for field_name, values in profile_input_sets.items():
            unknown = sorted(values.difference(known_input_keys))
            if unknown:
                errors.append(
                    f"{profile_prefix}.benchmark_config.input_profile.{field_name} contains unknown input spec ids: {unknown}."
                )

        unsupported_extras = sorted(extras_provided.difference(declared_inputs))
        if unsupported_extras:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.extras_provided lists input(s) not declared by this ToolSpec: {unsupported_extras}."
            )

        missing_required = sorted(required_inputs.difference(required_satisfied))
        if missing_required:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.required_inputs_satisfied is missing required input(s): {missing_required}."
            )
        missing_required_extras = sorted(required_satisfied.difference(extras_provided))
        if missing_required_extras:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.extras_provided must include required input(s): {missing_required_extras}."
            )
        unknown_required = sorted(required_satisfied.difference(required_inputs))
        if unknown_required:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.required_inputs_satisfied lists input(s) not required by this ToolSpec: {unknown_required}."
            )

        unknown_optional = sorted(optional_provided.difference(optional_inputs))
        if unknown_optional:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.optional_inputs_provided lists input(s) not optional in this ToolSpec: {unknown_optional}."
            )
        missing_optional_extras = sorted(optional_provided.difference(extras_provided))
        if missing_optional_extras:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.extras_provided must include optional input(s): {missing_optional_extras}."
            )

        active_conditional = {
            str(rule.get("input", "")).strip()
            for rule in conditional_rules
            if str(rule.get("input", "")).strip()
            and _conditional_rule_matches(
                rule=rule,
                toolspec=toolspec,
                resolved_params=resolved_params,
                execution_profile=execution_profile,
            )
        }
        missing_conditional = sorted(
            active_conditional.difference(conditional_satisfied)
        )
        if missing_conditional:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.conditional_inputs_satisfied is missing active conditional input(s): {missing_conditional}."
            )
        inactive_conditional = sorted(
            conditional_satisfied.difference(active_conditional)
        )
        if inactive_conditional:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.conditional_inputs_satisfied lists inactive conditional input(s): {inactive_conditional}."
            )
        missing_conditional_extras = sorted(
            conditional_satisfied.difference(extras_provided)
        )
        if missing_conditional_extras:
            errors.append(
                f"{profile_prefix}.benchmark_config.input_profile.extras_provided must include conditional input(s): {missing_conditional_extras}."
            )
    return errors


def run(
    schema_path: Path,
    catalog_tools_root: Path,
    tool_filters: list[str],
    fail_fast: bool,
) -> int:
    all_costs = discover_cost_files(catalog_tools_root)
    if not all_costs:
        raise RuntimeError(f"No cost.json files found under: {catalog_tools_root}")

    selected = select_tools(all_costs, tool_filters)
    schema = load_json(schema_path)
    validator = build_validator(schema)
    known_input_keys = discover_input_keys(catalog_tools_root.parent / "input_specs")

    counters = ValidationCounters()

    for tool_id, cost_path in selected:
        print(f"[{tool_id}] validating {cost_path}")
        try:
            instance = load_json(cost_path)
            schema_errors = validate_instance(validator, instance)
            semantic_errors = semantic_errors_for_cost(
                tool_id=tool_id,
                instance=instance,
                catalog_tools_root=catalog_tools_root,
                known_input_keys=known_input_keys,
            )
        except RuntimeError as exc:
            counters = ValidationCounters(
                valid=counters.valid, invalid=counters.invalid + 1
            )
            print(f"  ERROR: {exc}")
            if fail_fast:
                break
            continue

        if not schema_errors and not semantic_errors:
            counters = ValidationCounters(
                valid=counters.valid + 1, invalid=counters.invalid
            )
            print("  VALID")
            continue

        counters = ValidationCounters(
            valid=counters.valid, invalid=counters.invalid + 1
        )
        print(
            "  INVALID: {count} issue(s)".format(
                count=len(schema_errors) + len(semantic_errors)
            )
        )
        for idx, err in enumerate(schema_errors, start=1):
            print(f"    {idx}. {to_json_pointer(err)} -> {err.message}")
        base = len(schema_errors)
        for rel_idx, message in enumerate(semantic_errors, start=1):
            print(f"    {base + rel_idx}. (semantic) {message}")
        if fail_fast:
            break

    print()
    print(
        f"Summary: checked={counters.checked} valid={counters.valid} invalid={counters.invalid}"
    )
    return 0 if counters.invalid == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(
            schema_path=args.schema,
            catalog_tools_root=args.catalog_tools_root,
            tool_filters=args.tool,
            fail_fast=args.fail_fast,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
