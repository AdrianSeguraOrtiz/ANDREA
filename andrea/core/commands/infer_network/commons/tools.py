"""Tool request parsing, compatibility checks, and parameter resolution."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from andrea.core.shared.issues import make_issue
from andrea.core.shared.param_validation import ParamValidationError
from andrea.core.shared.param_validation import (
    validate_param_value as _validate_param_value,
)

from .shared import DatasetContext, SchemaConstraints, _load_json_object
from .tool_rule_eval import (
    COMPATIBILITY_OPS,
    _collect_compatibility_rule_issues,
    _compare_values,
)

EXECUTION_CAPABILITIES = {
    "global",
    "group_native",
    "group_emulated",
    "cell_native",
    "group_aggregated",
}
EXECUTION_CAPABILITY_ORDER = (
    "global",
    "group_native",
    "group_emulated",
    "cell_native",
    "group_aggregated",
)


def _execution_capability_choices() -> str:
    return ", ".join(EXECUTION_CAPABILITY_ORDER)


def _validate_execution_capability_contract(
    *, tool_id: str, capabilities: list[str]
) -> None:
    if "group_aggregated" in capabilities and "cell_native" not in capabilities:
        raise ValueError(
            f"[{tool_id}] toolspec.execution_capabilities includes 'group_aggregated' "
            "but does not include required companion mode 'cell_native'"
        )


def _parse_extra_inputs_spec(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    known_params: set[str] | None = None,
) -> tuple[list[str], list[str], list[dict[str, Any]], list[str]]:
    extra_inputs = toolspec.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return [], [], [], ["invalid toolspec.extra_inputs"]

    errors: list[str] = []
    required_extras: list[str] = []
    optional_extras: list[str] = []
    conditional_required: list[dict[str, Any]] = []

    req = extra_inputs.get("required", [])
    opt = extra_inputs.get("optional", [])
    cond = extra_inputs.get("conditional_required", [])

    def parse_usage_entries(raw: Any, field: str) -> list[str]:
        parsed: list[str] = []
        if not isinstance(raw, list):
            errors.append(f"toolspec.extra_inputs.{field} must be an array")
            return parsed
        seen: set[str] = set()
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                errors.append(
                    f"toolspec.extra_inputs.{field}[{idx}] must be an object with input and usage"
                )
                continue
            input_key = str(item.get("input", "")).strip()
            usage = str(item.get("usage", "")).strip()
            if not input_key:
                errors.append(f"toolspec.extra_inputs.{field}[{idx}].input is required")
                continue
            if not usage:
                errors.append(f"toolspec.extra_inputs.{field}[{idx}].usage is required")
                continue
            if input_key in seen:
                errors.append(
                    f"toolspec.extra_inputs.{field} contains duplicate input: {input_key}"
                )
                continue
            seen.add(input_key)
            parsed.append(input_key)
        return parsed

    required_extras = parse_usage_entries(req, "required")
    optional_extras = parse_usage_entries(opt, "optional")

    overlap = sorted(set(required_extras).intersection(optional_extras))
    if overlap:
        errors.append(f"toolspec.extra_inputs.required/optional overlap: {overlap}")

    if cond is None:
        cond = []
    if not isinstance(cond, list):
        errors.append("toolspec.extra_inputs.conditional_required must be an array")
        cond = []

    for idx, raw_rule in enumerate(cond, start=1):
        if not isinstance(raw_rule, dict):
            errors.append(
                f"toolspec.extra_inputs.conditional_required[{idx}] must be an object"
            )
            continue

        input_key = str(raw_rule.get("input", "")).strip()
        param_name = str(raw_rule.get("param", "")).strip()
        execution_name = str(raw_rule.get("execution", "")).strip()
        op = str(raw_rule.get("op", "")).strip()
        usage = str(raw_rule.get("usage", "")).strip()
        message = str(raw_rule.get("message", "")).strip()
        value = raw_rule.get("value")

        if not input_key:
            errors.append(
                f"toolspec.extra_inputs.conditional_required[{idx}].input is required"
            )
            continue
        if bool(param_name) == bool(execution_name):
            errors.append(
                f"toolspec.extra_inputs.conditional_required[{idx}] must define exactly one of param or execution"
            )
            continue
        if execution_name and execution_name != "mode":
            errors.append(
                f"toolspec.extra_inputs.conditional_required[{idx}].execution must be 'mode'"
            )
            continue
        if op not in COMPATIBILITY_OPS:
            errors.append(
                f"toolspec.extra_inputs.conditional_required[{idx}].op is invalid"
            )
            continue
        if not usage:
            errors.append(
                f"toolspec.extra_inputs.conditional_required[{idx}].usage is required"
            )
            continue
        if not message:
            errors.append(
                f"toolspec.extra_inputs.conditional_required[{idx}].message is required"
            )
            continue
        if param_name and known_params is not None and param_name not in known_params:
            errors.append(
                f"toolspec.extra_inputs.conditional_required[{idx}] references unknown parameter '{param_name}'"
            )
            continue

        parsed_rule = {
            "input": input_key,
            "op": op,
            "value": value,
            "usage": usage,
            "message": message,
        }
        if param_name:
            parsed_rule["param"] = param_name
        else:
            parsed_rule["execution"] = execution_name
        conditional_required.append(parsed_rule)

    return required_extras, optional_extras, conditional_required, errors


def _load_tools_params(tools_params_path: Path) -> dict[str, dict[str, Any]]:
    raw = _load_json_object(tools_params_path, "tools-params")
    if not raw:
        raise ValueError("tools-params JSON must include at least one tool request")

    parsed: dict[str, dict[str, Any]] = {}
    # Required format:
    # {"runs": [{"run_id": "...", "tool_id": "...", "params": {...}, "execution": {...}}, ...]}
    runs = raw.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError(
            "tools-params must be an object with non-empty array field: runs"
        )
    extra_keys = sorted(k for k in raw.keys() if k != "runs")
    if extra_keys:
        raise ValueError(
            "tools-params with 'runs' format must not include extra top-level keys: "
            f"{extra_keys}"
        )

    for idx, item in enumerate(runs, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"tools-params.runs[{idx}] must be an object")

        tool_id_raw = item.get("tool_id")
        if not isinstance(tool_id_raw, str) or not tool_id_raw.strip():
            raise ValueError(
                f"tools-params.runs[{idx}].tool_id must be a non-empty string"
            )
        tool_id = tool_id_raw.strip()

        params = item.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"tools-params.runs[{idx}].params must be an object")

        execution = item.get("execution", {})
        if execution is None:
            execution = {}
        if not isinstance(execution, dict):
            raise ValueError(
                f"tools-params.runs[{idx}].execution must be an object when provided"
            )

        run_id_raw = item.get("run_id")
        if run_id_raw is None or (
            isinstance(run_id_raw, str) and not run_id_raw.strip()
        ):
            run_id = f"{tool_id}__{idx:02d}"
        elif isinstance(run_id_raw, str):
            run_id = run_id_raw.strip()
        else:
            raise ValueError(
                f"tools-params.runs[{idx}].run_id must be string when provided"
            )

        if run_id in parsed:
            raise ValueError(f"Duplicate run_id in tools-params: {run_id}")
        parsed[run_id] = {
            "tool_id": tool_id,
            "params": params,
            "execution": execution,
        }
    return parsed


def _load_toolspec(tools_root: Path, tool_id: str) -> dict[str, Any]:
    toolspec_path = tools_root / tool_id / "toolspec.json"
    if not toolspec_path.exists():
        raise ValueError(
            f"Tool '{tool_id}' requested in tools-params but toolspec not found: {toolspec_path}"
        )
    return _load_json_object(toolspec_path, f"toolspec[{tool_id}]")


def _parse_execution_capabilities(
    *, tool_id: str, toolspec: dict[str, Any]
) -> list[str]:
    raw = toolspec.get("execution_capabilities")
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            f"[{tool_id}] toolspec.execution_capabilities must be a non-empty array"
        )
    capabilities: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(
                f"[{tool_id}] toolspec.execution_capabilities entries must be strings"
            )
        mode = item.strip()
        if mode not in EXECUTION_CAPABILITIES:
            raise ValueError(
                f"[{tool_id}] toolspec.execution_capabilities contains unsupported mode: {mode!r}"
            )
        if mode not in capabilities:
            capabilities.append(mode)
    _validate_execution_capability_contract(
        tool_id=tool_id,
        capabilities=capabilities,
    )
    return capabilities


def _default_execution_mode(capabilities: list[str]) -> str:
    for mode in EXECUTION_CAPABILITY_ORDER:
        if mode in capabilities:
            return mode
    return capabilities[0]


def _resolve_run_execution(
    *,
    run_id: str,
    toolspec: dict[str, Any],
    user_execution: dict[str, Any],
    warnings: list[str],
) -> tuple[bool, dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        capabilities = _parse_execution_capabilities(tool_id=run_id, toolspec=toolspec)
    except ValueError as exc:
        errors.append(str(exc))
        capabilities = ["global"]

    unknown_keys = sorted(set(user_execution.keys()).difference({"mode"}))
    for key in unknown_keys:
        errors.append(f"unknown execution key: {key}")

    mode_raw = user_execution.get("mode")
    if mode_raw is not None:
        if not isinstance(mode_raw, str):
            errors.append("execution.mode must be string when provided")
            mode = _default_execution_mode(capabilities)
        else:
            mode = mode_raw.strip()
            if mode not in EXECUTION_CAPABILITIES:
                errors.append(
                    "execution.mode must be one of: "
                    f"{_execution_capability_choices()}"
                )
    else:
        mode = _default_execution_mode(capabilities)

    if mode and mode not in capabilities:
        errors.append(
            f"execution.mode={mode!r} is not supported by this tool; supported modes: {capabilities}"
        )

    if errors:
        warnings.append(
            f"[{run_id}] skipped due to invalid execution config: {'; '.join(errors)}"
        )
        return False, {}, errors

    return True, {"mode": mode}, []


def _check_tool_compatibility(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    dataset: DatasetContext,
    constraints: SchemaConstraints,
    warnings: list[str],
    warning_prefix: str | None = None,
) -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    conditional_messages: list[str] = []

    try:
        _parse_execution_capabilities(tool_id=tool_id, toolspec=toolspec)
    except ValueError as exc:
        errors.append(str(exc))

    accepts = toolspec.get("accepts")
    if not isinstance(accepts, list) or not all(isinstance(x, str) for x in accepts):
        errors.append("invalid toolspec.accepts")
    elif dataset.column_kind not in set(accepts):
        errors.append(
            f"dataset column_kind '{dataset.column_kind}' is not accepted by tool ({accepts})"
        )

    assumes = str(toolspec.get("assumes", "")).strip()
    if assumes not in constraints.assumptions:
        errors.append("invalid toolspec.assumes")
    else:
        if assumes == "scrna_specific" and dataset.expression_profile not in {
            "scrna",
            "mixed",
        }:
            errors.append(
                f"tool assumes scrna_specific but dataset expression_profile is '{dataset.expression_profile}'"
            )
        if assumes == "bulk_specific" and dataset.expression_profile not in {
            "bulk",
            "mixed",
        }:
            errors.append(
                f"tool assumes bulk_specific but dataset expression_profile is '{dataset.expression_profile}'"
            )

    taxonomic_scope = toolspec.get("taxonomic_scope")
    if not isinstance(taxonomic_scope, dict):
        errors.append("invalid toolspec.taxonomic_scope")
    else:
        allowed_groups = taxonomic_scope.get("allowed_groups")
        if (
            not isinstance(allowed_groups, list)
            or not allowed_groups
            or not all(isinstance(x, str) for x in allowed_groups)
        ):
            errors.append("invalid toolspec.taxonomic_scope.allowed_groups")
        else:
            unknown_groups = sorted(
                set(allowed_groups).difference(constraints.taxonomic_groups)
            )
            if unknown_groups:
                errors.append(
                    "toolspec.taxonomic_scope.allowed_groups contains unsupported values: "
                    f"{unknown_groups}"
                )
            elif dataset.taxonomic_group not in set(allowed_groups):
                allowed_label = ", ".join(allowed_groups)
                errors.append(
                    f"dataset taxonomic_group '{dataset.taxonomic_group}' is not accepted by tool; accepted groups: {allowed_label}"
                )

    toolspec_params = toolspec.get("params", {})
    known_params = (
        set(toolspec_params.keys()) if isinstance(toolspec_params, dict) else None
    )
    required_extras, optional_extras, conditional_required, extra_errors = (
        _parse_extra_inputs_spec(
            tool_id=tool_id,
            toolspec=toolspec,
            known_params=known_params,
        )
    )
    errors.extend(extra_errors)

    for extra_key in required_extras:
        if dataset.extras.get(extra_key) is None:
            errors.append(f"required extra input missing in manifest: {extra_key}")

    conditional_inputs = {
        str(rule.get("input", "")).strip()
        for rule in conditional_required
        if str(rule.get("input", "")).strip()
    }
    for extra_key in optional_extras:
        if extra_key in conditional_inputs:
            continue
        if dataset.extras.get(extra_key) is None:
            message = f"optional extra not provided: {extra_key}"
            warnings.append(
                f"[{warning_prefix}] {message}" if warning_prefix else message
            )

    for rule in conditional_required:
        input_key = str(rule.get("input", "")).strip()
        message = str(rule.get("message", "")).strip()
        if input_key and dataset.extras.get(input_key) is None and message:
            conditional_messages.append(message)

    if errors:
        return False, errors, []

    return True, [], conditional_messages


def _resolve_tool_params(
    *,
    tool_id: str,
    user_params: dict[str, Any],
    toolspec_params: dict[str, Any],
    warnings: list[str],
    passthrough_unknown_params: bool = False,
) -> tuple[bool, dict[str, Any], list[str]]:
    errors: list[str] = []
    if passthrough_unknown_params and not toolspec_params:
        return True, copy.deepcopy(user_params), []

    unknown_keys = sorted(set(user_params.keys()).difference(toolspec_params.keys()))
    for key in unknown_keys:
        warnings.append(f"[{tool_id}] unknown parameter key ignored: {key}")

    resolved: dict[str, Any] = {}
    for param_name, param_def_any in toolspec_params.items():
        if not isinstance(param_def_any, dict):
            errors.append(f"invalid toolspec.params definition for '{param_name}'")
            continue

        if param_name in user_params:
            raw_value = user_params[param_name]
        else:
            raw_value = copy.deepcopy(param_def_any.get("default"))

        if raw_value is None:
            if (
                bool(param_def_any.get("required"))
                and param_def_any.get("default") is None
            ):
                errors.append(f"missing required parameter: {param_name}")
            resolved[param_name] = None
            continue

        try:
            resolved[param_name] = _validate_param_value(
                value=raw_value,
                param_def=param_def_any,
                path=f"{tool_id}.{param_name}",
                warnings=warnings,
            )
        except ParamValidationError as exc:
            errors.append(str(exc))

    if errors:
        warnings.append(
            f"[{tool_id}] skipped due to invalid params: {'; '.join(errors)}"
        )
        return False, {}, errors

    return True, resolved, []


def _conditional_rule_matches(
    *,
    resolved_params: dict[str, Any],
    resolved_execution: dict[str, Any],
    rule: dict[str, Any],
) -> bool:
    param_name = str(rule.get("param", "")).strip()
    execution_name = str(rule.get("execution", "")).strip()
    op = str(rule.get("op", "")).strip()
    expected = rule.get("value")

    if param_name:
        if param_name not in resolved_params:
            return False
        actual = resolved_params.get(param_name)
    elif execution_name:
        if execution_name not in resolved_execution:
            return False
        actual = resolved_execution.get(execution_name)
    else:
        return False

    return _compare_values(actual=actual, op=op, expected=expected)


def _collect_conditional_input_issues(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    dataset: DatasetContext,
    resolved_params: dict[str, Any],
    resolved_execution: dict[str, Any],
) -> list[str]:
    toolspec_params = toolspec.get("params", {})
    known_params = (
        set(toolspec_params.keys()) if isinstance(toolspec_params, dict) else None
    )
    _required, _optional, conditional_required, extra_errors = _parse_extra_inputs_spec(
        tool_id=tool_id,
        toolspec=toolspec,
        known_params=known_params,
    )
    if extra_errors:
        return [f"invalid toolspec extra-input rules: {msg}" for msg in extra_errors]

    issues: list[str] = []
    for rule in conditional_required:
        input_key = str(rule.get("input", "")).strip()
        message = str(rule.get("message", "")).strip()
        if dataset.extras.get(input_key) is not None:
            continue
        if _conditional_rule_matches(
            resolved_params=resolved_params,
            resolved_execution=resolved_execution,
            rule=rule,
        ):
            issues.append(message)
    return list(dict.fromkeys(issues))


def _build_tool_compatibility_entry(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    dataset: DatasetContext,
    constraints: SchemaConstraints,
    tool_origin: str,
    warning_code: str,
    post_compatibility_warnings: list[str] | None = None,
) -> dict[str, Any]:
    local_warnings: list[str] = []
    compatible, block_messages, _conditional_messages = _check_tool_compatibility(
        tool_id=tool_id,
        toolspec=toolspec,
        dataset=dataset,
        constraints=constraints,
        warnings=local_warnings,
    )
    local_warnings.extend(post_compatibility_warnings or [])

    conditional_messages: list[str] = []
    if compatible:
        toolspec_params = toolspec.get("params", {})
        if not isinstance(toolspec_params, dict):
            compatible = False
            block_messages = ["toolspec.params must be an object"]
        else:
            params_ok, resolved_params, param_errors = _resolve_tool_params(
                tool_id=tool_id,
                user_params={},
                toolspec_params=toolspec_params,
                warnings=local_warnings,
            )
            execution_ok, resolved_execution, execution_errors = (
                _resolve_run_execution(
                    run_id=tool_id,
                    toolspec=toolspec,
                    user_execution={},
                    warnings=local_warnings,
                )
            )
            if not params_ok or not execution_ok:
                compatible = False
                block_messages = param_errors + execution_errors
            else:
                rule_blocks, rule_warnings, rule_errors = (
                    _collect_compatibility_rule_issues(
                        tool_id=tool_id,
                        toolspec=toolspec,
                        dataset=dataset,
                        resolved_params=resolved_params,
                        resolved_execution=resolved_execution,
                        catalog_scan=True,
                    )
                )
                if rule_errors:
                    compatible = False
                    block_messages = [
                        f"invalid compatibility rule: {msg}" for msg in rule_errors
                    ]
                elif rule_blocks:
                    compatible = False
                    block_messages = rule_blocks
                else:
                    local_warnings.extend(rule_warnings)
                    conditional_messages = _collect_conditional_input_issues(
                        tool_id=tool_id,
                        toolspec=toolspec,
                        dataset=dataset,
                        resolved_params=resolved_params,
                        resolved_execution=resolved_execution,
                    )

    status = "eligible"
    if not compatible:
        status = "blocked"
    elif local_warnings or conditional_messages:
        status = "warning"

    return {
        "tool_id": tool_id,
        "status": status,
        "tool_origin": tool_origin,
        "issues": [
            *[
                make_issue(
                    severity="block",
                    code="compatibility",
                    message=message,
                    tool_id=tool_id,
                )
                for message in block_messages
            ],
            *[
                make_issue(
                    severity="warn",
                    code=warning_code,
                    message=message,
                    tool_id=tool_id,
                )
                for message in local_warnings
            ],
            *[
                make_issue(
                    severity="warn",
                    code="conditional_required",
                    message=message,
                    tool_id=tool_id,
                )
                for message in conditional_messages
            ],
        ],
    }


def _scan_catalog_compatibility(
    *,
    tools_root: Path,
    dataset: DatasetContext,
    constraints: SchemaConstraints,
    custom_tools: dict[str, dict[str, Any]] | None = None,
    custom_blocked_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for toolspec_path in sorted(tools_root.glob("*/toolspec.json")):
        tool_id = toolspec_path.parent.name
        try:
            toolspec = _load_json_object(toolspec_path, f"toolspec[{tool_id}]")
        except ValueError as exc:
            entries.append(
                {
                    "tool_id": tool_id,
                    "status": "blocked",
                    "issues": [
                        make_issue(
                            severity="block",
                            code="invalid_toolspec",
                            message=f"invalid toolspec: {exc}",
                            tool_id=tool_id,
                        )
                    ],
                }
            )
            continue

        entries.append(
            _build_tool_compatibility_entry(
                tool_id=tool_id,
                tool_origin="catalog",
                toolspec=toolspec,
                dataset=dataset,
                constraints=constraints,
                warning_code="catalog_warning",
            )
        )

    for tool_id, toolspec in sorted((custom_tools or {}).items()):
        post_compatibility_warnings: list[str] = []
        if toolspec.get("_andrea_custom_tool"):
            from .custom_tools import custom_tool_warnings

            post_compatibility_warnings = custom_tool_warnings(tool_id, toolspec)
        entries.append(
            _build_tool_compatibility_entry(
                tool_id=tool_id,
                tool_origin="custom",
                toolspec=toolspec,
                dataset=dataset,
                constraints=constraints,
                warning_code="custom_tool_warning",
                post_compatibility_warnings=post_compatibility_warnings,
            )
        )

    entries.extend(custom_blocked_entries or [])

    eligible = [item for item in entries if item["status"] == "eligible"]
    warning = [item for item in entries if item["status"] == "warning"]
    blocked = [item for item in entries if item["status"] == "blocked"]
    return {
        "tools_total": len(entries),
        "eligible": eligible,
        "warning": warning,
        "blocked": blocked,
    }
