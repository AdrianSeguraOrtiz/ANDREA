"""ToolSpec conditional and compatibility rule evaluation."""

from __future__ import annotations

from typing import Any

from andrea.core.shared.compatibility_rules import (
    COMPATIBILITY_ACTIONS,
    COMPATIBILITY_OPS,
    compare_compatibility_values,
    condition_expected_value,
    match_compatibility_conditions,
)

from .shared import DatasetContext


def _compare_values(*, actual: Any, op: str, expected: Any) -> bool:
    return compare_compatibility_values(
        actual=actual,
        op=op,
        expected=expected,
        coerce_numeric=False,
        allow_bool_numeric=False,
    )


def _condition_value_from(*, toolspec: dict[str, Any], value_from: str) -> Any:
    if value_from == "taxonomic_scope.supported_species":
        taxonomic_scope = toolspec.get("taxonomic_scope", {})
        if isinstance(taxonomic_scope, dict):
            supported = taxonomic_scope.get("supported_species", [])
            if isinstance(supported, list):
                return supported
    raise ValueError(f"unsupported compatibility value_from: {value_from}")


def _condition_actual_value(
    *,
    field: str,
    dataset: DatasetContext,
    resolved_params: dict[str, Any],
    resolved_execution: dict[str, Any],
) -> Any:
    if field == "dataset.organism.taxonomic_group":
        return dataset.taxonomic_group
    if field == "dataset.organism.ncbi_taxon_id":
        return dataset.ncbi_taxon_id
    if field == "execution.mode":
        return resolved_execution.get("mode")
    if field.startswith("param."):
        param_name = field.removeprefix("param.")
        if param_name not in resolved_params:
            raise ValueError(
                f"compatibility rule references unknown parameter '{param_name}'"
            )
        return resolved_params.get(param_name)
    raise ValueError(f"unsupported compatibility field: {field}")


def _compatibility_rule_matches(
    *,
    rule: dict[str, Any],
    toolspec: dict[str, Any],
    dataset: DatasetContext,
    resolved_params: dict[str, Any],
    resolved_execution: dict[str, Any],
) -> bool:
    def _condition_matches(condition: dict[str, Any], idx: int) -> bool:
        field, op, expected = condition_expected_value(
            condition=condition,
            condition_label=f"compatibility rule condition[{idx}]",
            value_from_resolver=lambda value_from: _condition_value_from(
                toolspec=toolspec,
                value_from=value_from,
            ),
        )
        actual = _condition_actual_value(
            field=field,
            dataset=dataset,
            resolved_params=resolved_params,
            resolved_execution=resolved_execution,
        )
        return compare_compatibility_values(
            actual=actual,
            op=op,
            expected=expected,
            coerce_numeric=False,
            allow_bool_numeric=False,
        )

    return match_compatibility_conditions(
        rule=rule,
        condition_matcher=_condition_matches,
        empty_conditions_message="compatibility rule must include non-empty conditions",
        non_object_condition_message=lambda idx: (
            f"compatibility rule condition[{idx}] must be an object"
        ),
    )


def _collect_compatibility_rule_issues(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    dataset: DatasetContext,
    resolved_params: dict[str, Any],
    resolved_execution: dict[str, Any],
    catalog_scan: bool = False,
    warning_prefix: str | None = None,
) -> tuple[list[str], list[str], list[str]]:
    raw_rules = toolspec.get("compatibility_rules", [])
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        return ["toolspec.compatibility_rules must be an array"], [], []

    blocking: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    for idx, rule in enumerate(raw_rules, start=1):
        if not isinstance(rule, dict):
            errors.append(f"compatibility_rules[{idx}] must be an object")
            continue
        action = str(rule.get("action", "")).strip()
        message = str(rule.get("message", "")).strip()
        if action not in COMPATIBILITY_ACTIONS:
            errors.append(f"compatibility_rules[{idx}].action is invalid")
            continue
        if not message:
            errors.append(f"compatibility_rules[{idx}].message is required")
            continue
        if catalog_scan and _compatibility_rule_has_mutable_conditions(rule):
            try:
                matches = _compatibility_rule_matches(
                    rule=rule,
                    toolspec=toolspec,
                    dataset=dataset,
                    resolved_params=resolved_params,
                    resolved_execution=resolved_execution,
                )
            except ValueError as exc:
                errors.append(f"compatibility_rules[{idx}]: {exc}")
                continue
            if matches:
                warnings.append(
                    f"[{warning_prefix}] {message}" if warning_prefix else message
                )
            continue
        try:
            matches = _compatibility_rule_matches(
                rule=rule,
                toolspec=toolspec,
                dataset=dataset,
                resolved_params=resolved_params,
                resolved_execution=resolved_execution,
            )
        except ValueError as exc:
            errors.append(f"compatibility_rules[{idx}]: {exc}")
            continue
        if not matches:
            continue
        if action == "block":
            blocking.append(message)
        else:
            warnings.append(
                f"[{warning_prefix}] {message}" if warning_prefix else message
            )

    return (
        list(dict.fromkeys(blocking)),
        list(dict.fromkeys(warnings)),
        errors,
    )


def _compatibility_rule_has_mutable_conditions(rule: dict[str, Any]) -> bool:
    conditions = rule.get("conditions", [])
    if not isinstance(conditions, list):
        return False
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        field = str(condition.get("field", "")).strip()
        if field == "execution.mode" or field.startswith("param."):
            return True
    return False
