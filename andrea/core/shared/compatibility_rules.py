"""Shared primitives for command-specific compatibility rule evaluators."""

from __future__ import annotations

from typing import Any, Callable

COMPATIBILITY_ACTIONS = {"block", "warn"}
COMPATIBILITY_OPS = {"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte"}


def compare_compatibility_values(
    *,
    actual: Any,
    op: str,
    expected: Any,
    coerce_numeric: bool,
    allow_bool_numeric: bool,
) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return isinstance(expected, list) and actual in expected
    if op == "not_in":
        return isinstance(expected, list) and actual not in expected
    if op not in {"gt", "gte", "lt", "lte"}:
        return False
    if not allow_bool_numeric and (isinstance(actual, bool) or isinstance(expected, bool)):
        return False
    if coerce_numeric:
        try:
            actual_num = float(actual)
            expected_num = float(expected)
        except (TypeError, ValueError):
            return False
    else:
        if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
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


def condition_expected_value(
    *,
    condition: dict[str, Any],
    condition_label: str,
    value_from_resolver: Callable[[str], Any],
    attribute_separator: str = ".",
) -> tuple[str, str, Any]:
    field = str(condition.get("field", "")).strip()
    op = str(condition.get("op", "")).strip()
    if not field:
        raise ValueError(f"{condition_label}{attribute_separator}field is required")
    if op not in COMPATIBILITY_OPS:
        raise ValueError(f"{condition_label}{attribute_separator}op is invalid")
    has_value = "value" in condition
    has_value_from = "value_from" in condition
    if has_value == has_value_from:
        raise ValueError(
            f"{condition_label} must define exactly one of value or value_from"
        )
    expected = (
        value_from_resolver(str(condition.get("value_from", "")).strip())
        if has_value_from
        else condition.get("value")
    )
    return field, op, expected


def match_compatibility_conditions(
    *,
    rule: dict[str, Any],
    condition_matcher: Callable[[dict[str, Any], int], bool],
    empty_conditions_message: str,
    non_object_condition_message: Callable[[int], str],
) -> bool:
    conditions = rule.get("conditions", [])
    if not isinstance(conditions, list) or not conditions:
        raise ValueError(empty_conditions_message)
    for index, condition in enumerate(conditions, start=1):
        if not isinstance(condition, dict):
            raise ValueError(non_object_condition_message(index))
        if not condition_matcher(condition, index):
            return False
    return True
