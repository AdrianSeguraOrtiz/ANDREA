"""Shared parameter validation helpers reused by inference and generation."""

from __future__ import annotations

import copy
from typing import Any


class ParamValidationError(ValueError):
    """Raised when a parameter value does not match its catalog definition."""


def _validate_numeric_range(
    value: float,
    param_def: dict[str, Any],
    path: str,
) -> None:
    min_value = param_def.get("min")
    max_value = param_def.get("max")
    if isinstance(min_value, (int, float)):
        min_bound = float(min_value)
        if bool(param_def.get("exclusive_min")):
            if value <= min_bound:
                raise ParamValidationError(f"{path} must be > {min_value}")
        elif value < min_bound:
            raise ParamValidationError(f"{path} must be >= {min_value}")
    if isinstance(max_value, (int, float)):
        max_bound = float(max_value)
        if bool(param_def.get("exclusive_max")):
            if value >= max_bound:
                raise ParamValidationError(f"{path} must be < {max_value}")
        elif value > max_bound:
            raise ParamValidationError(f"{path} must be <= {max_value}")


def validate_param_value(
    *,
    value: Any,
    param_def: dict[str, Any],
    path: str,
    warnings: list[str],
) -> Any:
    param_type = param_def.get("type")

    if param_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ParamValidationError(f"{path} must be int")
        _validate_numeric_range(float(value), param_def, path)
        return int(value)

    if param_type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParamValidationError(f"{path} must be float")
        out = float(value)
        _validate_numeric_range(out, param_def, path)
        return out

    if param_type == "bool":
        if not isinstance(value, bool):
            raise ParamValidationError(f"{path} must be bool")
        return value

    if param_type == "string":
        if not isinstance(value, str):
            raise ParamValidationError(f"{path} must be string")
        return value

    if param_type == "enum":
        if not isinstance(value, str):
            raise ParamValidationError(f"{path} must be enum string")
        choices = param_def.get("enum", [])
        if value not in choices:
            raise ParamValidationError(f"{path} must be one of {choices}")
        return value

    if param_type == "object":
        if not isinstance(value, dict):
            raise ParamValidationError(f"{path} must be object")
        properties = param_def.get("properties", {})
        if not isinstance(properties, dict):
            return value

        unknown = sorted(set(value.keys()).difference(properties.keys()))
        for key in unknown:
            warnings.append(
                f"{path}.{key} is not defined in toolspec and will be ignored"
            )

        resolved: dict[str, Any] = {}
        for key, sub_def in properties.items():
            if not isinstance(sub_def, dict):
                continue
            sub_path = f"{path}.{key}"
            if key in value:
                raw_sub = value[key]
            else:
                raw_sub = copy.deepcopy(sub_def.get("default"))

            if raw_sub is None:
                if bool(sub_def.get("required")) and sub_def.get("default") is None:
                    raise ParamValidationError(
                        f"missing required parameter: {sub_path}"
                    )
                resolved[key] = None
                continue

            resolved[key] = validate_param_value(
                value=raw_sub,
                param_def=sub_def,
                path=sub_path,
                warnings=warnings,
            )
        return resolved

    if param_type == "array":
        if not isinstance(value, list):
            raise ParamValidationError(f"{path} must be array")
        item_def = param_def.get("items")
        if not isinstance(item_def, dict):
            return value
        out = []
        for idx, item in enumerate(value):
            out.append(
                validate_param_value(
                    value=item,
                    param_def=item_def,
                    path=f"{path}[{idx}]",
                    warnings=warnings,
                )
            )
        return out

    if param_type == "union":
        options = param_def.get("oneOf", [])
        if not isinstance(options, list) or not options:
            raise ParamValidationError(f"{path} has invalid union definition")
        errors: list[str] = []
        for option in options:
            if not isinstance(option, dict):
                continue
            try:
                return validate_param_value(
                    value=value,
                    param_def=option,
                    path=path,
                    warnings=warnings,
                )
            except ParamValidationError as exc:
                errors.append(str(exc))
        raise ParamValidationError(f"{path} does not match union options: {errors}")

    raise ParamValidationError(
        f"{path} has unsupported param type in toolspec: {param_type!r}"
    )


__all__ = ["ParamValidationError", "validate_param_value"]
