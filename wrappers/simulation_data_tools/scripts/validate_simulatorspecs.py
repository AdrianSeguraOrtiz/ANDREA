"""Validate simulator SimulatorSpec files against the SimulatorSpec JSON Schema.

Usage examples:
1) Validate every simulator:
   python validate_simulatorspecs.py

2) Validate only selected simulators:
   python validate_simulatorspecs.py --simulator dyngen
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from shared.catalog_simulators import (
    DEFAULT_CATALOG_SIMULATORS_ROOT,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_WRAPPERS_ROOT,
    discover_catalog_simulator_dirs,
    expected_docker_image,
    load_json,
    select_simulators,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT_SPECS_ROOT = (
    REPO_ROOT / "andrea" / "catalog_simulation_data_tools" / "input_specs"
)
TRUTH_CONTEXT_IDS = {"global", "group", "cell"}
REQUIRED_TRUTH_OUTPUTS_BY_PROFILE = {
    "bulk_steady_state": {"global"},
    "bulk_time_series": {"global"},
    "bulk_perturbational": {"global"},
    "scrna_global": {"global"},
    "scrna_grouped": {"global", "group"},
    "scrna_cell_specific": {"global", "group", "cell"},
}
NON_EMPTY_TRUTH_CONTEXT_FIELDS = (
    "source_artifacts",
    "upstream_configuration",
    "generation",
    "score_semantics",
    "limitations",
)


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


def _param_path_exists(params_schema: dict[str, Any], path: str) -> bool:
    current = params_schema
    parts = path.split(".")
    for index, part in enumerate(parts):
        if not isinstance(current, dict) or part not in current:
            return False
        param_def = current[part]
        if not isinstance(param_def, dict):
            return False
        if index == len(parts) - 1:
            return True
        if param_def.get("type") != "object":
            return False
        current = param_def.get("properties", {})
    return False


def _condition_values(condition: dict[str, Any]) -> list[Any]:
    value = condition.get("value")
    if isinstance(value, list):
        return list(value)
    return [value]


def _all_extra_ids(spec: dict[str, Any]) -> set[str]:
    extras: set[str] = set()
    for capability in spec.get("profile_capabilities", {}).values():
        if not isinstance(capability, dict):
            continue
        extras.update(capability.get("native_extras", []))
        extras.update(capability.get("derivable_extras", []))
    return extras


def _all_native_output_ids(spec: dict[str, Any]) -> set[str]:
    outputs: set[str] = set()
    for capability in spec.get("profile_capabilities", {}).values():
        if not isinstance(capability, dict):
            continue
        for output in capability.get("native_outputs", []):
            if isinstance(output, dict) and output.get("id"):
                outputs.add(str(output["id"]))
    return outputs


def _validate_truth_contexts(
    *,
    profile: str,
    capability: dict[str, Any],
    location: str,
) -> list[str]:
    errors: list[str] = []
    truth_outputs = capability.get("truth_outputs", {})
    if not isinstance(truth_outputs, dict):
        return [f"{location}.truth_outputs must be an object"]

    required_outputs = REQUIRED_TRUTH_OUTPUTS_BY_PROFILE.get(profile, {"global"})
    unsupported_required = sorted(
        output_id
        for output_id in required_outputs
        if truth_outputs.get(output_id) not in {"native", "derivable"}
    )
    if unsupported_required:
        errors.append(
            f"{location}.truth_outputs: required profile context(s) cannot be none: "
            + ", ".join(unsupported_required)
        )

    truth_contexts = capability.get("truth_contexts")
    if not isinstance(truth_contexts, list):
        errors.append(f"{location}: missing truth_contexts array")
        return errors

    context_ids: list[str] = []
    context_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(truth_contexts):
        item_location = f"{location}.truth_contexts[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_location}: truth context must be an object")
            continue
        context_id = str(item.get("context", "")).strip()
        if context_id not in TRUTH_CONTEXT_IDS:
            errors.append(f"{item_location}: unknown truth context '{context_id}'")
            continue
        context_ids.append(context_id)
        if context_id in context_by_id:
            errors.append(f"{item_location}: duplicate truth context '{context_id}'")
        context_by_id[context_id] = item

        status = item.get("status")
        expected_status = truth_outputs.get(context_id)
        if status != expected_status:
            errors.append(
                f"{item_location}: status must match truth_outputs.{context_id} "
                f"({expected_status!r})"
            )
        if status in {"native", "derivable"}:
            missing_fields = [
                field for field in NON_EMPTY_TRUTH_CONTEXT_FIELDS if not item.get(field)
            ]
            if missing_fields:
                errors.append(
                    f"{item_location}: non-none truth context is missing evidence "
                    "field(s): "
                    + ", ".join(missing_fields)
                )

    duplicate_contexts = sorted(
        context_id for context_id in set(context_ids) if context_ids.count(context_id) > 1
    )
    if duplicate_contexts:
        errors.append(
            f"{location}.truth_contexts: duplicate context entries: {duplicate_contexts}"
        )

    expected_contexts = set(truth_outputs)
    documented_contexts = set(context_by_id)
    missing_contexts = sorted(expected_contexts.difference(documented_contexts))
    if missing_contexts:
        errors.append(
            f"{location}.truth_contexts: missing context entries for truth_outputs: "
            + ", ".join(missing_contexts)
        )
    extra_contexts = sorted(documented_contexts.difference(expected_contexts))
    if extra_contexts:
        errors.append(
            f"{location}.truth_contexts: context entries are not declared in "
            "truth_outputs: "
            + ", ".join(extra_contexts)
        )

    return errors


def _validate_condition_value_from(
    *,
    spec: dict[str, Any],
    known_input_ids: set[str],
    value_from: Any,
    location: str,
) -> list[str]:
    reference = str(value_from or "").strip()
    if reference.startswith("param."):
        param_path = reference.removeprefix("param.")
        if not _param_path_exists(spec.get("params", {}), param_path):
            return [f"{location}: unknown value_from parameter path '{reference}'"]
        return []
    if reference.startswith("input."):
        parts = reference.split(".")
        if len(parts) != 3 or parts[2] != "unique_gene_count":
            return [f"{location}: unsupported value_from input metric '{reference}'"]
        if parts[1] not in known_input_ids:
            return [f"{location}: value_from references unknown input '{parts[1]}'"]
        return []
    return [f"{location}: unsupported value_from reference '{reference}'"]


def _validate_condition_reference(
    *,
    spec: dict[str, Any],
    condition: dict[str, Any],
    location: str,
    known_input_ids: set[str] | None = None,
) -> list[str]:
    field = str(condition.get("field", "")).strip()
    known_input_ids = known_input_ids or set()
    errors: list[str] = []
    if field.startswith("param."):
        param_path = field.removeprefix("param.")
        if not _param_path_exists(spec.get("params", {}), param_path):
            errors.append(f"{location}: unknown parameter path '{field}'")
    elif field.startswith("input."):
        parts = field.split(".")
        if len(parts) != 3 or parts[2] != "unique_gene_count":
            errors.append(f"{location}: unsupported input metric field '{field}'")
        elif parts[1] not in known_input_ids:
            errors.append(f"{location}: condition references unknown input '{parts[1]}'")
    elif field == "profile":
        profile_values = {
            str(value) for value in _condition_values(condition) if value is not None
        }
        unknown_profiles = sorted(
            profile_values.difference(spec.get("profile_capabilities", {}))
        )
        if unknown_profiles:
            errors.append(
                f"{location}: profile condition references unsupported profile(s): "
                + ", ".join(unknown_profiles)
            )
    elif field == "requested_extra":
        all_extras = _all_extra_ids(spec)
        extra_values = {
            str(value) for value in _condition_values(condition) if value is not None
        }
        unknown_extras = sorted(extra_values.difference(all_extras))
        if unknown_extras:
            errors.append(
                f"{location}: requested_extra condition references unsupported extra(s): "
                + ", ".join(unknown_extras)
            )
    elif field == "native_output":
        all_outputs = _all_native_output_ids(spec)
        output_values = {
            str(value) for value in _condition_values(condition) if value is not None
        }
        unknown_outputs = sorted(output_values.difference(all_outputs))
        if unknown_outputs:
            errors.append(
                f"{location}: native_output condition references unsupported output(s): "
                + ", ".join(unknown_outputs)
            )
    else:
        errors.append(f"{location}: unsupported condition field '{field}'")
    if "value_from" in condition:
        errors.extend(
            _validate_condition_value_from(
                spec=spec,
                known_input_ids=known_input_ids,
                value_from=condition.get("value_from"),
                location=location,
            )
        )
    return errors


def discover_input_spec_ids(input_specs_root: Path) -> set[str]:
    if not input_specs_root.exists() or not input_specs_root.is_dir():
        raise RuntimeError(f"Invalid input_specs root: {input_specs_root}")

    input_ids: set[str] = set()
    for spec_path in sorted(input_specs_root.glob("*.json")):
        spec = load_json(spec_path)
        if not isinstance(spec, dict):
            raise RuntimeError(f"{spec_path}: expected JSON object")
        input_id = str(spec.get("id", "")).strip()
        if not input_id:
            raise RuntimeError(f"{spec_path}: missing input spec id")
        if input_id != spec_path.stem:
            raise RuntimeError(
                f"{spec_path}: input spec id must match filename stem "
                f"(expected '{spec_path.stem}', got '{input_id}')"
            )
        if input_id in input_ids:
            raise RuntimeError(f"Duplicate simulation input spec id: {input_id}")
        input_ids.add(input_id)
    return input_ids


def semantic_errors(
    *,
    simulator_id: str,
    spec: dict[str, Any],
    wrappers_root: Path,
    known_input_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    if spec.get("id") != simulator_id:
        errors.append(f"id must match catalog directory name '{simulator_id}'")

    expected_image = expected_docker_image(simulator_id)
    if spec.get("docker_image") != expected_image:
        errors.append(f"docker_image must be '{expected_image}'")

    publications = spec.get("publication", [])
    if not all(
        isinstance(item, str) and item.startswith(("http://", "https://"))
        for item in publications
    ):
        errors.append("publication entries must be complete http(s) URLs")

    first_author = str(spec.get("first_author", "")).strip()
    if len(first_author.split()) < 2:
        errors.append("first_author must include at least given name and surname")

    threading = spec.get("runtime_resources", {}).get("threading", {})
    default_threads = threading.get("default_threads")
    max_threads = threading.get("max_threads")
    if isinstance(default_threads, int) and isinstance(max_threads, int):
        if default_threads > max_threads:
            errors.append(
                "runtime_resources.threading.default_threads must be <= max_threads"
            )
    if any(key in spec.get("params", {}) for key in ("threads", "num_cores")):
        errors.append(
            "params must not expose runtime parallelism controls; use runtime_resources.threading"
        )

    wrapper_dir = wrappers_root / simulator_id
    if not wrapper_dir.exists():
        errors.append(f"missing wrapper directory: {wrapper_dir}")
    elif not (wrapper_dir / "Dockerfile").exists():
        errors.append(f"missing wrapper Dockerfile: {wrapper_dir / 'Dockerfile'}")

    extra_inputs = spec.get("extra_inputs", {})
    input_declarations: dict[str, list[str]] = {}
    for category in ("required", "optional"):
        for index, item in enumerate(extra_inputs.get(category, [])):
            if not isinstance(item, dict):
                continue
            input_id = str(item.get("input", "")).strip()
            if not input_id:
                continue
            input_declarations.setdefault(input_id, []).append(
                f"extra_inputs.{category}[{index}]"
            )
    for index, requirement in enumerate(extra_inputs.get("conditional_required", [])):
        if not isinstance(requirement, dict):
            continue
        input_id = str(requirement.get("input", "")).strip()
        if input_id:
            input_declarations.setdefault(input_id, []).append(
                f"extra_inputs.conditional_required[{index}]"
            )
        for condition_index, condition in enumerate(requirement.get("conditions", [])):
            if not isinstance(condition, dict):
                continue
            location = (
                "extra_inputs.conditional_required"
                f"[{index}].conditions[{condition_index}]"
            )
            errors.extend(
                _validate_condition_reference(
                    spec=spec,
                    condition=condition,
                    location=location,
                    known_input_ids=known_input_ids,
                )
            )

    for input_id, locations in sorted(input_declarations.items()):
        if input_id not in known_input_ids:
            errors.append(
                f"extra_inputs: input '{input_id}' is not declared in "
                "catalog_simulation_data_tools/input_specs"
            )
        required_locations = [
            location for location in locations if ".required[" in location
        ]
        optional_locations = [
            location for location in locations if ".optional[" in location
        ]
        if required_locations and optional_locations:
            errors.append(
                f"extra_inputs: input '{input_id}' cannot be both required and optional"
            )

    compatibility_rules = spec.get("compatibility_rules", [])
    if not isinstance(compatibility_rules, list):
        errors.append("compatibility_rules must be an array")
    else:
        for rule_index, rule in enumerate(compatibility_rules):
            location = f"compatibility_rules[{rule_index}]"
            if not isinstance(rule, dict):
                errors.append(f"{location}: rule must be an object")
                continue
            if rule.get("action") not in {"block", "warn"}:
                errors.append(f"{location}: action must be block or warn")
            if not str(rule.get("message", "")).strip():
                errors.append(f"{location}: message is required")
            conditions = rule.get("conditions", [])
            if not isinstance(conditions, list) or not conditions:
                errors.append(f"{location}: conditions must be a non-empty array")
                continue
            for condition_index, condition in enumerate(conditions):
                if not isinstance(condition, dict):
                    errors.append(
                        f"{location}.conditions[{condition_index}]: condition must be an object"
                    )
                    continue
                errors.extend(
                    _validate_condition_reference(
                        spec=spec,
                        condition=condition,
                        location=f"{location}.conditions[{condition_index}]",
                        known_input_ids=known_input_ids,
                    )
                )

    for profile, capability in spec.get("profile_capabilities", {}).items():
        location = f"profile_capabilities.{profile}"
        errors.extend(
            _validate_truth_contexts(
                profile=str(profile),
                capability=capability,
                location=location,
            )
        )
        native = set(capability.get("native_extras", []))
        derivable = set(capability.get("derivable_extras", []))
        overlap = sorted(native.intersection(derivable))
        if overlap:
            errors.append(
                f"profile_capabilities.{profile}: native_extras and derivable_extras overlap: {overlap}"
            )
        for output_index, output in enumerate(capability.get("native_outputs", [])):
            if not isinstance(output, dict):
                continue
            for condition_index, condition in enumerate(output.get("conditions", [])):
                if not isinstance(condition, dict):
                    continue
                errors.extend(
                    _validate_condition_reference(
                        spec=spec,
                        condition=condition,
                        location=(
                            f"profile_capabilities.{profile}.native_outputs"
                            f"[{output_index}].conditions[{condition_index}]"
                        ),
                        known_input_ids=known_input_ids,
                    )
                )
        truth_derivable = {
            key
            for key, value in capability.get("truth_outputs", {}).items()
            if value == "derivable"
        }
        for requirement_index, requirement in enumerate(
            capability.get("truth_parameter_requirements", [])
        ):
            if not isinstance(requirement, dict):
                continue
            for condition_index, condition in enumerate(
                requirement.get("conditions", [])
            ):
                if not isinstance(condition, dict):
                    continue
                errors.extend(
                    _validate_condition_reference(
                        spec=spec,
                        condition=condition,
                        location=(
                            f"profile_capabilities.{profile}.truth_parameter_requirements"
                            f"[{requirement_index}].conditions[{condition_index}]"
                        ),
                        known_input_ids=known_input_ids,
                    )
                )
        expected_derivations = derivable.union(truth_derivable)
        derivation_entries = capability.get("derivations", [])
        derivation_artifacts = [
            str(item.get("artifact"))
            for item in derivation_entries
            if isinstance(item, dict)
        ]
        duplicate_derivations = sorted(
            artifact
            for artifact in set(derivation_artifacts)
            if derivation_artifacts.count(artifact) > 1
        )
        if duplicate_derivations:
            errors.append(
                f"profile_capabilities.{profile}: duplicate derivation entries: {duplicate_derivations}"
            )
        documented_derivations = set(derivation_artifacts)
        missing_derivations = sorted(
            expected_derivations.difference(documented_derivations)
        )
        if missing_derivations:
            errors.append(
                f"profile_capabilities.{profile}: missing derivation explanations for: {missing_derivations}"
            )
        unexpected_derivations = sorted(
            documented_derivations.difference(expected_derivations)
        )
        if unexpected_derivations:
            errors.append(
                f"profile_capabilities.{profile}: derivation explanations declared for non-derived artifacts: {unexpected_derivations}"
            )
    return errors


def validate_one(
    *,
    simulator_id: str,
    simulator_dir: Path,
    validator: Draft202012Validator,
    wrappers_root: Path,
    known_input_ids: set[str],
) -> list[str]:
    spec_path = simulator_dir / "simulatorspec.json"
    spec = load_json(spec_path)
    if not isinstance(spec, dict):
        return [f"{spec_path}: expected JSON object"]

    errors = [
        f"{to_json_pointer(err)} -> {err.message}"
        for err in sorted(validator.iter_errors(spec), key=lambda err: list(err.path))
    ]
    errors.extend(
        semantic_errors(
            simulator_id=simulator_id,
            spec=spec,
            wrappers_root=wrappers_root,
            known_input_ids=known_input_ids,
        )
    )
    return errors


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate every catalog simulatorspec.json against simulatorspec.schema.json."
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to SimulatorSpec schema. Default: {DEFAULT_SCHEMA_PATH}",
    )
    parser.add_argument(
        "--catalog-simulators-root",
        type=Path,
        default=DEFAULT_CATALOG_SIMULATORS_ROOT,
        help=f"Path to catalog simulators directory. Default: {DEFAULT_CATALOG_SIMULATORS_ROOT}",
    )
    parser.add_argument(
        "--wrappers-root",
        type=Path,
        default=DEFAULT_WRAPPERS_ROOT,
        help=f"Path to simulator wrapper directories. Default: {DEFAULT_WRAPPERS_ROOT}",
    )
    parser.add_argument(
        "--input-specs-root",
        type=Path,
        default=DEFAULT_INPUT_SPECS_ROOT,
        help=f"Path to simulation input_specs directory. Default: {DEFAULT_INPUT_SPECS_ROOT}",
    )
    parser.add_argument(
        "--simulator",
        action="append",
        default=[],
        help="Simulator id to validate (repeatable). If omitted, validates every simulator.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first invalid simulator.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validator = build_validator(load_json(args.schema))
        discovered = discover_catalog_simulator_dirs(args.catalog_simulators_root)
        selected = select_simulators(discovered, args.simulator)
        known_input_ids = discover_input_spec_ids(args.input_specs_root)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    counters = ValidationCounters()
    for simulator_id, simulator_dir in selected:
        try:
            errors = validate_one(
                simulator_id=simulator_id,
                simulator_dir=simulator_dir,
                validator=validator,
                wrappers_root=args.wrappers_root,
                known_input_ids=known_input_ids,
            )
        except RuntimeError as exc:
            errors = [str(exc)]

        if errors:
            counters = ValidationCounters(counters.valid, counters.invalid + 1)
            print(f"[INVALID] {simulator_id}", file=sys.stderr)
            for message in errors:
                print(f"  - {message}", file=sys.stderr)
            if args.fail_fast:
                break
        else:
            counters = ValidationCounters(counters.valid + 1, counters.invalid)
            print(f"[valid] {simulator_id}")

    print(
        f"Checked {counters.checked} simulator spec(s): "
        f"{counters.valid} valid, {counters.invalid} invalid"
    )
    return 1 if counters.invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
