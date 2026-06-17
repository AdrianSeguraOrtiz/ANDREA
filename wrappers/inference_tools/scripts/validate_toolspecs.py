"""Validate tool ToolSpec files against the ToolSpec JSON Schema.

Usage examples:
1) Validate every tool:
   python validate_toolspecs.py

2) Validate only selected tools:
   python validate_toolspecs.py --tool genie3 --tool scmtni

Exit codes:
- 0: all selected ToolSpecs are valid
- 1: one or more ToolSpecs are invalid / unreadable
- 2: usage/runtime error (missing schema, unknown tool ids, etc.)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

INFERENCE_TOOLS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_ROOT = REPO_ROOT / "andrea" / "catalog_inference_tools"
DEFAULT_SCHEMA_PATH = CATALOG_ROOT / "schemas" / "toolspec.schema.json"
DEFAULT_CATALOG_TOOLS_ROOT = CATALOG_ROOT / "tools"
TAXONOMIC_GROUPS = {
    "animal",
    "plant",
    "fungi",
    "bacteria",
    "archaea",
    "protist",
    "viral",
    "synthetic",
    "unknown",
}
COMPATIBILITY_FIELDS = {
    "dataset.organism.taxonomic_group",
    "dataset.organism.ncbi_taxon_id",
    "execution.mode",
}
COMPATIBILITY_OPS = {"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte"}
COMPATIBILITY_ACTIONS = {"block", "warn"}


@dataclass(frozen=True)
class ValidationCounters:
    valid: int = 0
    invalid: int = 0

    @property
    def checked(self) -> int:
        return self.valid + self.invalid


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"File not found: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not read file: {path} ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Malformed JSON in {path} (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc


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
        description="Validate every catalog toolspec.json against toolspec.schema.json."
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to ToolSpec schema. Default: {DEFAULT_SCHEMA_PATH}",
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
        help="Tool id to validate (repeatable). If omitted, validates all tools.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first invalid ToolSpec.",
    )
    return parser.parse_args(argv)


def discover_toolspecs(catalog_tools_root: Path) -> list[tuple[str, Path]]:
    if not catalog_tools_root.exists() or not catalog_tools_root.is_dir():
        raise RuntimeError(f"Invalid catalog tools root: {catalog_tools_root}")

    discovered: list[tuple[str, Path]] = []
    for tool_dir in sorted(
        path for path in catalog_tools_root.iterdir() if path.is_dir()
    ):
        spec_path = tool_dir / "toolspec.json"
        if spec_path.exists():
            discovered.append((tool_dir.name, spec_path))
    return discovered


def select_toolspecs(
    all_toolspecs: list[tuple[str, Path]],
    tool_filters: list[str],
) -> list[tuple[str, Path]]:
    by_tool_id = {tool_id: path for tool_id, path in all_toolspecs}
    if not tool_filters:
        return all_toolspecs

    unknown = sorted(tool_id for tool_id in tool_filters if tool_id not in by_tool_id)
    if unknown:
        raise RuntimeError(f"Unknown tool id(s): {unknown}")
    return [(tool_id, by_tool_id[tool_id]) for tool_id in tool_filters]


def _extra_input_set(*, raw_items: Any, field_name: str, errors: list[str]) -> set[str]:
    if not isinstance(raw_items, list):
        errors.append(f"extra_inputs.{field_name} must be an array.")
        return set()
    out: set[str] = set()
    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            errors.append(
                f"extra_inputs.{field_name}[{idx}] must be an object with input and usage; strings are not allowed."
            )
            continue
        input_name = item.get("input")
        usage = item.get("usage")
        if not isinstance(input_name, str) or not input_name.strip():
            errors.append(f"extra_inputs.{field_name}[{idx}].input is required.")
            continue
        if not isinstance(usage, str) or not usage.strip():
            errors.append(f"extra_inputs.{field_name}[{idx}].usage is required.")
        if input_name in out:
            errors.append(
                f"extra_inputs.{field_name} contains duplicate input '{input_name}'."
            )
        out.add(input_name)
    return out


def _condition_param_name(condition: dict[str, Any]) -> str | None:
    field = condition.get("field")
    if isinstance(field, str) and field.startswith("param."):
        return field.removeprefix("param.")
    return None


def _validate_runtime_resources(instance: dict[str, Any], errors: list[str]) -> None:
    runtime_resources = instance.get("runtime_resources")
    if not isinstance(runtime_resources, dict):
        errors.append("runtime_resources.threading is required and must be an object.")
        return
    threading = runtime_resources.get("threading")
    if not isinstance(threading, dict):
        errors.append("runtime_resources.threading is required and must be an object.")
        return

    supported = threading.get("supported")
    default_threads = threading.get("default_threads")
    max_threads = threading.get("max_threads")
    upstream_mapping = threading.get("upstream_mapping")
    if not isinstance(upstream_mapping, str) or not upstream_mapping.strip():
        errors.append("runtime_resources.threading.upstream_mapping is required.")
    if not isinstance(supported, bool):
        errors.append("runtime_resources.threading.supported must be boolean.")
    if (
        isinstance(default_threads, bool)
        or not isinstance(default_threads, int)
        or default_threads < 1
    ):
        errors.append("runtime_resources.threading.default_threads must be >= 1.")
    if (
        isinstance(max_threads, bool)
        or not isinstance(max_threads, int)
        or max_threads < 1
    ):
        errors.append("runtime_resources.threading.max_threads must be >= 1.")
    if (
        isinstance(default_threads, int)
        and not isinstance(default_threads, bool)
        and isinstance(max_threads, int)
        and not isinstance(max_threads, bool)
        and default_threads > max_threads
    ):
        errors.append(
            "runtime_resources.threading.default_threads must be <= max_threads."
        )
    if supported is False and (default_threads != 1 or max_threads != 1):
        errors.append(
            "runtime_resources.threading with supported=false must set default_threads=1 and max_threads=1."
        )


def semantic_errors_for_toolspec(*, tool_id: str, instance: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(instance, dict):
        return ["ToolSpec root must be a JSON object."]

    raw_id = instance.get("id")
    if not isinstance(raw_id, str) or raw_id.strip() != tool_id:
        errors.append(
            f"ToolSpec id must match directory name. expected='{tool_id}' got='{raw_id}'."
        )

    params = instance.get("params", {})
    if not isinstance(params, dict):
        params = {}

    _validate_runtime_resources(instance, errors)

    execution_capabilities = instance.get("execution_capabilities", [])
    execution_modes = {
        x for x in execution_capabilities if isinstance(x, str) and x.strip()
    }

    taxonomic_scope = instance.get("taxonomic_scope")
    if not isinstance(taxonomic_scope, dict):
        errors.append("taxonomic_scope is required and must be an object.")
    else:
        allowed_groups = taxonomic_scope.get("allowed_groups", [])
        if not isinstance(allowed_groups, list) or not allowed_groups:
            errors.append("taxonomic_scope.allowed_groups must be a non-empty array.")
        else:
            invalid_groups = sorted(
                str(item)
                for item in allowed_groups
                if not isinstance(item, str) or item not in TAXONOMIC_GROUPS
            )
            if invalid_groups:
                errors.append(
                    f"taxonomic_scope.allowed_groups contains unsupported values: {invalid_groups}."
                )
        supported_species = taxonomic_scope.get("supported_species", [])
        if not isinstance(supported_species, list) or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 1
            for item in supported_species
        ):
            errors.append(
                "taxonomic_scope.supported_species must contain only positive integer NCBI taxonomy ids."
            )

    extra_inputs = instance.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return errors

    required = extra_inputs.get("required", [])
    optional = extra_inputs.get("optional", [])
    conditional = extra_inputs.get("conditional_required", [])

    required_set = _extra_input_set(
        raw_items=required, field_name="required", errors=errors
    )
    optional_set = _extra_input_set(
        raw_items=optional, field_name="optional", errors=errors
    )
    overlap = sorted(required_set.intersection(optional_set))
    if overlap:
        errors.append(
            f"extra_inputs.required and extra_inputs.optional overlap: {overlap}"
        )

    if isinstance(conditional, list):
        for idx, rule in enumerate(conditional, start=1):
            if not isinstance(rule, dict):
                continue
            usage = rule.get("usage")
            if not isinstance(usage, str) or not usage.strip():
                errors.append(
                    "extra_inputs.conditional_required[{idx}].usage is required.".format(
                        idx=idx
                    )
                )
            param_name = rule.get("param")
            if isinstance(param_name, str) and param_name not in params:
                errors.append(
                    "extra_inputs.conditional_required[{idx}] references unknown parameter '{param}'.".format(
                        idx=idx, param=param_name
                    )
                )
            execution_name = rule.get("execution")
            if isinstance(execution_name, str) and execution_name != "mode":
                errors.append(
                    "extra_inputs.conditional_required[{idx}] references unknown execution field '{field}'.".format(
                        idx=idx, field=execution_name
                    )
                )
            if isinstance(execution_name, str) and execution_name == "mode":
                op = rule.get("op")
                if op not in {"eq", "ne"}:
                    errors.append(
                        "extra_inputs.conditional_required[{idx}] execution.mode rules must use op 'eq' or 'ne'.".format(
                            idx=idx
                        )
                    )
                value = rule.get("value")
                if not isinstance(value, str) or value not in execution_modes:
                    errors.append(
                        "extra_inputs.conditional_required[{idx}] execution.mode value must be one of this tool's execution_capabilities: {modes}.".format(
                            idx=idx, modes=sorted(execution_modes)
                        )
                    )

    compatibility_rules = instance.get("compatibility_rules", [])
    if not isinstance(compatibility_rules, list):
        errors.append("compatibility_rules must be an array.")
    else:
        for rule_idx, rule in enumerate(compatibility_rules, start=1):
            if not isinstance(rule, dict):
                errors.append(f"compatibility_rules[{rule_idx}] must be an object.")
                continue
            action = rule.get("action")
            if action not in COMPATIBILITY_ACTIONS:
                errors.append(
                    f"compatibility_rules[{rule_idx}].action must be one of {sorted(COMPATIBILITY_ACTIONS)}."
                )
            message = rule.get("message")
            if not isinstance(message, str) or not message.strip():
                errors.append(f"compatibility_rules[{rule_idx}].message is required.")
            conditions = rule.get("conditions", [])
            if not isinstance(conditions, list) or not conditions:
                errors.append(
                    f"compatibility_rules[{rule_idx}].conditions must be a non-empty array."
                )
                continue
            for cond_idx, condition in enumerate(conditions, start=1):
                if not isinstance(condition, dict):
                    errors.append(
                        f"compatibility_rules[{rule_idx}].conditions[{cond_idx}] must be an object."
                    )
                    continue
                field = condition.get("field")
                if not isinstance(field, str) or not field.strip():
                    errors.append(
                        f"compatibility_rules[{rule_idx}].conditions[{cond_idx}].field is required."
                    )
                elif field not in COMPATIBILITY_FIELDS and not field.startswith(
                    "param."
                ):
                    errors.append(
                        f"compatibility_rules[{rule_idx}].conditions[{cond_idx}] references unsupported field '{field}'."
                    )
                param_name = _condition_param_name(condition)
                if param_name is not None and param_name not in params:
                    errors.append(
                        f"compatibility_rules[{rule_idx}].conditions[{cond_idx}] references unknown parameter '{param_name}'."
                    )
                op = condition.get("op")
                if op not in COMPATIBILITY_OPS:
                    errors.append(
                        f"compatibility_rules[{rule_idx}].conditions[{cond_idx}].op is invalid."
                    )
                has_value = "value" in condition
                has_value_from = "value_from" in condition
                if has_value == has_value_from:
                    errors.append(
                        f"compatibility_rules[{rule_idx}].conditions[{cond_idx}] must define exactly one of value or value_from."
                    )
                if (
                    has_value_from
                    and condition.get("value_from")
                    != "taxonomic_scope.supported_species"
                ):
                    errors.append(
                        f"compatibility_rules[{rule_idx}].conditions[{cond_idx}].value_from is unsupported."
                    )
                if field == "execution.mode" and has_value:
                    value = condition.get("value")
                    values = value if isinstance(value, list) else [value]
                    invalid_modes = [
                        item
                        for item in values
                        if not isinstance(item, str) or item not in execution_modes
                    ]
                    if invalid_modes:
                        errors.append(
                            "compatibility_rules[{rule_idx}].conditions[{cond_idx}] execution.mode value(s) must be in this tool's execution_capabilities: {modes}.".format(
                                rule_idx=rule_idx,
                                cond_idx=cond_idx,
                                modes=sorted(execution_modes),
                            )
                        )

    if "group_emulated" in execution_modes:
        has_group_emulated_requirement = "groups" in required_set
        if isinstance(conditional, list):
            for rule in conditional:
                if not isinstance(rule, dict):
                    continue
                if (
                    rule.get("input") == "groups"
                    and rule.get("execution") == "mode"
                    and rule.get("op") == "eq"
                    and rule.get("value") == "group_emulated"
                ):
                    has_group_emulated_requirement = True
                    break
        if not has_group_emulated_requirement:
            errors.append(
                "tools with execution_capabilities including 'group_emulated' must either require groups directly or declare extra_inputs.conditional_required for groups when execution.mode == 'group_emulated'."
            )

    if "group_aggregated" in execution_modes:
        if "cell_native" not in execution_modes:
            errors.append(
                "tools with execution_capabilities including 'group_aggregated' must also declare 'cell_native'."
            )
        has_group_aggregated_requirement = False
        if isinstance(conditional, list):
            for rule in conditional:
                if not isinstance(rule, dict):
                    continue
                if (
                    rule.get("input") == "groups"
                    and rule.get("execution") == "mode"
                    and rule.get("op") == "eq"
                    and rule.get("value") == "group_aggregated"
                ):
                    has_group_aggregated_requirement = True
                    break
        if not has_group_aggregated_requirement:
            errors.append(
                "tools with execution_capabilities including 'group_aggregated' must declare extra_inputs.conditional_required for groups when execution.mode == 'group_aggregated'."
            )

    return errors


def run(
    schema_path: Path,
    catalog_tools_root: Path,
    tool_filters: list[str],
    fail_fast: bool,
) -> int:
    all_toolspecs = discover_toolspecs(catalog_tools_root)
    if not all_toolspecs:
        raise RuntimeError(f"No toolspec.json files found under: {catalog_tools_root}")

    selected = select_toolspecs(all_toolspecs, tool_filters)

    schema = load_json(schema_path)
    validator = build_validator(schema)

    counters = ValidationCounters()

    for tool_id, spec_path in selected:
        print(f"[{tool_id}] validating {spec_path}")
        try:
            instance = load_json(spec_path)
            errors = validate_instance(
                validator,
                instance,
            )
            semantic_errors = semantic_errors_for_toolspec(
                tool_id=tool_id,
                instance=instance,
            )
        except RuntimeError as exc:
            counters = ValidationCounters(
                valid=counters.valid, invalid=counters.invalid + 1
            )
            print(f"  ERROR: {exc}")
            if fail_fast:
                break
            continue

        if not errors and not semantic_errors:
            counters = ValidationCounters(
                valid=counters.valid + 1, invalid=counters.invalid
            )
            print("  VALID")
            continue

        counters = ValidationCounters(
            valid=counters.valid, invalid=counters.invalid + 1
        )
        print(
            "  INVALID: {count} error(s)".format(
                count=len(errors) + len(semantic_errors)
            )
        )
        for idx, err in enumerate(errors, start=1):
            print(f"    {idx}. {to_json_pointer(err)} -> {err.message}")
        base = len(errors)
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
