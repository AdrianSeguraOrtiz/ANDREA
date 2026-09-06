"""Tool request parsing, compatibility checks, and parameter resolution."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from andrea.core.shared.issues import make_issue
from andrea.core.shared.output_capabilities import OUTPUT_SIGN_SEMANTICS
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
    "column_native",
    "group_aggregated",
}
EXECUTION_CAPABILITY_ORDER = (
    "global",
    "group_native",
    "group_emulated",
    "column_native",
    "group_aggregated",
)
RUNTIME_INPUT_CONTRACT_SCHEMA_VERSION = "1.0"


def _execution_capability_choices() -> str:
    return ", ".join(EXECUTION_CAPABILITY_ORDER)


def _build_output_capability_snapshot(
    *,
    run_id: str,
    catalog_tool_id: str,
    tool_origin: str,
    toolspec: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the output semantics needed by downstream evaluators."""
    if tool_origin not in {"catalog", "custom"}:
        raise ValueError(f"[{run_id}] invalid tool_origin: {tool_origin!r}")
    if not isinstance(catalog_tool_id, str) or not catalog_tool_id.strip():
        raise ValueError(f"[{run_id}] catalog_tool_id is required")

    outputs = toolspec.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError(f"[{run_id}] toolspec.outputs must be an object")
    directed = outputs.get("directed")
    if not isinstance(directed, bool):
        raise ValueError(f"[{run_id}] toolspec.outputs.directed must be a boolean")
    sign = outputs.get("sign")
    if sign not in OUTPUT_SIGN_SEMANTICS:
        allowed = ", ".join(sorted(OUTPUT_SIGN_SEMANTICS))
        raise ValueError(f"[{run_id}] toolspec.outputs.sign must be one of: {allowed}")
    return {
        "tool_origin": tool_origin,
        "catalog_tool_id": catalog_tool_id,
        "directed": directed,
        "sign": sign,
    }


def _validate_execution_capability_contract(
    *, tool_id: str, capabilities: list[str]
) -> None:
    if "group_emulated" in capabilities and "global" not in capabilities:
        raise ValueError(
            f"[{tool_id}] toolspec.execution_capabilities includes 'group_emulated' "
            "but does not include required companion mode 'global'"
        )
    if "group_aggregated" in capabilities and "column_native" not in capabilities:
        raise ValueError(
            f"[{tool_id}] toolspec.execution_capabilities includes 'group_aggregated' "
            "but does not include required companion mode 'column_native'"
        )


def _parse_extra_inputs_spec(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    known_params: set[str] | None = None,
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, Any]],
    list[str],
]:
    extra_inputs = toolspec.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return [], [], [], ["invalid toolspec.extra_inputs"]

    errors: list[str] = []
    required_extras: list[dict[str, str]] = []
    optional_extras: list[dict[str, str]] = []
    conditional_required: list[dict[str, Any]] = []

    req = extra_inputs.get("required", [])
    opt = extra_inputs.get("optional", [])
    cond = extra_inputs.get("conditional_required", [])

    def parse_delivery(raw: Any, *, path: str) -> str | None:
        if raw is None:
            errors.append(f"{path}.delivery is required")
            return None
        if not isinstance(raw, str) or raw not in {
            "runtime",
            "orchestration_only",
        }:
            errors.append(f"{path}.delivery must be 'runtime' or 'orchestration_only'")
            return None
        return raw

    def parse_usage_entries(raw: Any, field: str) -> list[dict[str, str]]:
        parsed: list[dict[str, str]] = []
        if not isinstance(raw, list):
            errors.append(f"toolspec.extra_inputs.{field} must be an array")
            return parsed
        seen: set[str] = set()
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                errors.append(
                    f"toolspec.extra_inputs.{field}[{idx}] must be an object "
                    "with input, usage, and delivery"
                )
                continue
            input_key = str(item.get("input", "")).strip()
            usage = str(item.get("usage", "")).strip()
            delivery = parse_delivery(
                item.get("delivery"),
                path=f"toolspec.extra_inputs.{field}[{idx}]",
            )
            if not input_key:
                errors.append(f"toolspec.extra_inputs.{field}[{idx}].input is required")
                continue
            if not usage:
                errors.append(f"toolspec.extra_inputs.{field}[{idx}].usage is required")
                continue
            if delivery is None:
                continue
            if delivery == "orchestration_only":
                errors.append(
                    f"toolspec.extra_inputs.{field}[{idx}].delivery may be "
                    "orchestration_only only for a conditional groups rule"
                )
                continue
            if input_key in seen:
                errors.append(
                    f"toolspec.extra_inputs.{field} contains duplicate input: {input_key}"
                )
                continue
            seen.add(input_key)
            parsed.append({"input": input_key, "delivery": delivery})
        return parsed

    required_extras = parse_usage_entries(req, "required")
    optional_extras = parse_usage_entries(opt, "optional")

    required_keys = {entry["input"] for entry in required_extras}
    optional_keys = {entry["input"] for entry in optional_extras}
    overlap = sorted(required_keys.intersection(optional_keys))
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
        delivery = parse_delivery(
            raw_rule.get("delivery"),
            path=f"toolspec.extra_inputs.conditional_required[{idx}]",
        )

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
        if delivery is None:
            continue
        if delivery == "orchestration_only" and not (
            input_key == "groups"
            and execution_name == "mode"
            and op == "eq"
            and value in {"group_emulated", "group_aggregated"}
        ):
            errors.append(
                "toolspec.extra_inputs.conditional_required"
                f"[{idx}].delivery=orchestration_only is reserved for groups "
                "when execution.mode is group_emulated or group_aggregated"
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
            "delivery": delivery,
        }
        if param_name:
            parsed_rule["param"] = param_name
        else:
            parsed_rule["execution"] = execution_name
        conditional_required.append(parsed_rule)

    return required_extras, optional_extras, conditional_required, errors


def _normalize_tool_request_identity(
    *,
    tool_id_raw: Any,
    run_id_raw: Any,
    request_index: int,
    custom_run_ids_by_tool_id: Mapping[str, str],
    source: str,
    strict_custom_identity: bool = False,
) -> tuple[str, str]:
    if not isinstance(tool_id_raw, str) or not tool_id_raw.strip():
        raise ValueError(
            f"{source}[{request_index}].tool_id must be a non-empty string"
        )
    if run_id_raw is not None and not isinstance(run_id_raw, str):
        raise ValueError(
            f"{source}[{request_index}].run_id must be string when provided"
        )

    tool_id_candidate = tool_id_raw.strip()
    run_id_candidate = run_id_raw.strip() if isinstance(run_id_raw, str) else None
    custom_tool_id_from_run = next(
        (
            tool_id
            for tool_id, custom_run_id in custom_run_ids_by_tool_id.items()
            if custom_run_id == run_id_candidate
        ),
        None,
    )
    expected_custom_run_id = custom_run_ids_by_tool_id.get(tool_id_candidate)
    if expected_custom_run_id is not None or custom_tool_id_from_run is not None:
        expected_tool_id = (
            tool_id_candidate
            if expected_custom_run_id is not None
            else custom_tool_id_from_run
        )
        if expected_tool_id is None:  # pragma: no cover - guarded above
            raise AssertionError("custom tool identity resolution failed")
        expected_run_id = custom_run_ids_by_tool_id[expected_tool_id]
        custom_identity_is_canonical = (
            tool_id_raw == tool_id_candidate
            and isinstance(run_id_raw, str)
            and bool(run_id_raw)
            and run_id_raw == run_id_candidate
        )
        custom_identity_matches = (
            tool_id_raw == expected_tool_id and run_id_raw == expected_run_id
        )
        if not custom_identity_is_canonical or (
            strict_custom_identity and not custom_identity_matches
        ):
            raise ValueError(
                f"{source}[{request_index}] external identity must be exactly "
                f"run_id={expected_run_id!r}, tool_id={expected_tool_id!r}"
            )
        return tool_id_raw, run_id_raw

    tool_id = tool_id_candidate
    if run_id_raw is None or (
        isinstance(run_id_raw, str) and not run_id_raw.strip()
    ):
        run_id = f"{tool_id}__{request_index:02d}"
    else:
        run_id = run_id_raw.strip()
    return tool_id, run_id


def _load_tools_params(
    tools_params_path: Path,
    *,
    custom_run_ids_by_tool_id: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
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

        tool_id, run_id = _normalize_tool_request_identity(
            tool_id_raw=item.get("tool_id"),
            run_id_raw=item.get("run_id"),
            request_index=idx,
            custom_run_ids_by_tool_id=custom_run_ids_by_tool_id or {},
            source="tools-params.runs",
        )

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
    if "group_native" in capabilities:
        extra_inputs = toolspec.get("extra_inputs")
        required = (
            extra_inputs.get("required", [])
            if isinstance(extra_inputs, dict)
            else []
        )
        conditional = (
            extra_inputs.get("conditional_required", [])
            if isinstance(extra_inputs, dict)
            else []
        )
        context_inputs = {"groups", "column_phenotypes"}
        context_sources = {
            str(entry.get("input"))
            for entry in required
            if isinstance(entry, dict)
            and entry.get("input") in context_inputs
            and entry.get("delivery") == "runtime"
        }
        context_sources.update(
            str(rule.get("input"))
            for rule in conditional
            if isinstance(rule, dict)
            and rule.get("input") in context_inputs
            and rule.get("execution") == "mode"
            and rule.get("op") == "eq"
            and rule.get("value") == "group_native"
            and rule.get("delivery") == "runtime"
        )
        if len(context_sources) != 1:
            raise ValueError(
                f"[{tool_id}] group_native requires exactly one runtime-delivered "
                "context source: groups or column_phenotypes"
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
) -> tuple[bool, list[str]]:
    errors: list[str] = []

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
    (
        required_extras,
        optional_extras,
        conditional_required,
        extra_errors,
    ) = _parse_extra_inputs_spec(
        tool_id=tool_id,
        toolspec=toolspec,
        known_params=known_params,
    )
    errors.extend(extra_errors)

    for entry in required_extras:
        extra_key = entry["input"]
        if dataset.extras.get(extra_key) is None:
            errors.append(f"required extra input missing in manifest: {extra_key}")

    conditional_inputs = {
        str(rule.get("input", "")).strip()
        for rule in conditional_required
        if str(rule.get("input", "")).strip()
    }
    for entry in optional_extras:
        extra_key = entry["input"]
        if extra_key in conditional_inputs:
            continue
        if dataset.extras.get(extra_key) is None:
            message = f"optional extra not provided: {extra_key}"
            warnings.append(
                f"[{warning_prefix}] {message}" if warning_prefix else message
            )

    if errors:
        return False, errors

    return True, []


def _resolve_tool_params(
    *,
    tool_id: str,
    user_params: dict[str, Any],
    toolspec_params: dict[str, Any],
    warnings: list[str],
    passthrough_unknown_params: bool = False,
    allow_missing_required: bool = False,
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
                and not allow_missing_required
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
                allow_missing_required=allow_missing_required,
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


def _resolve_runtime_extra_input_keys(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    resolved_params: dict[str, Any],
    resolved_execution: dict[str, Any],
) -> set[str]:
    """Return the exact standardized extras that may be mounted in the tool runtime."""
    return _resolve_active_extra_input_keys(
        tool_id=tool_id,
        toolspec=toolspec,
        resolved_params=resolved_params,
        resolved_execution=resolved_execution,
        delivery="runtime",
    )


def _runtime_execution_for_logical_run(
    logical_execution: dict[str, Any],
) -> dict[str, Any]:
    """Return the execution contract seen by the physical child container."""
    runtime_execution = dict(logical_execution)
    logical_mode = str(runtime_execution.get("mode", "")).strip()
    if logical_mode == "group_emulated":
        runtime_execution["mode"] = "global"
    elif logical_mode == "group_aggregated":
        runtime_execution["mode"] = "column_native"
    return runtime_execution


def _resolve_active_extra_input_keys(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    resolved_params: dict[str, Any],
    resolved_execution: dict[str, Any],
    delivery: str | None = None,
) -> set[str]:
    """Resolve active extra inputs from the canonical ToolSpec contract.

    ``delivery=None`` returns every active input needed by inference or by
    ANDREA's orchestration. Passing ``delivery='runtime'`` returns only inputs
    that the child container is allowed to receive.
    """
    if delivery not in {None, "runtime", "orchestration_only"}:
        raise ValueError(f"invalid extra-input delivery filter: {delivery!r}")
    toolspec_params = toolspec.get("params", {})
    known_params = (
        set(toolspec_params.keys()) if isinstance(toolspec_params, dict) else None
    )
    required, optional, conditional_required, errors = _parse_extra_inputs_spec(
        tool_id=tool_id,
        toolspec=toolspec,
        known_params=known_params,
    )
    if errors:
        details = "; ".join(errors)
        raise ValueError(f"[{tool_id}] invalid toolspec extra-input rules: {details}")

    entries: list[dict[str, Any]] = [*required, *optional]
    entries.extend(
        rule
        for rule in conditional_required
        if _conditional_rule_matches(
            resolved_params=resolved_params,
            resolved_execution=resolved_execution,
            rule=rule,
        )
    )
    return {
        str(entry["input"])
        for entry in entries
        if delivery is None or entry.get("delivery") == delivery
    }


def _build_runtime_input_snapshot(
    *,
    run_id: str,
    catalog_tool_id: str,
    tool_origin: str,
    toolspec: dict[str, Any],
    resolved_params: dict[str, Any],
    resolved_execution: dict[str, Any],
    available_extra_inputs: set[str],
) -> dict[str, Any]:
    """Freeze logical active inputs and the physical child-container firewall."""
    if tool_origin not in {"catalog", "custom"}:
        raise ValueError(f"[{run_id}] invalid tool_origin: {tool_origin!r}")
    logical_active = _resolve_active_extra_input_keys(
        tool_id=run_id,
        toolspec=toolspec,
        resolved_params=resolved_params,
        resolved_execution=resolved_execution,
    )
    runtime_active = _resolve_runtime_extra_input_keys(
        tool_id=run_id,
        toolspec=toolspec,
        resolved_params=resolved_params,
        resolved_execution=_runtime_execution_for_logical_run(resolved_execution),
    )
    logical_active.update(runtime_active)
    logical_active.intersection_update(available_extra_inputs)
    runtime_active.intersection_update(available_extra_inputs)
    return {
        "tool_id": catalog_tool_id,
        "tool_origin": tool_origin,
        "active_extra_inputs": sorted(logical_active),
        "mounted_extra_inputs": sorted(runtime_active),
    }


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
    runtime_execution = _runtime_execution_for_logical_run(resolved_execution)
    for rule in conditional_required:
        input_key = str(rule.get("input", "")).strip()
        message = str(rule.get("message", "")).strip()
        if dataset.extras.get(input_key) is not None:
            continue
        if _conditional_rule_matches(
            resolved_params=resolved_params,
            resolved_execution=(
                runtime_execution
                if rule.get("delivery") == "runtime"
                else resolved_execution
            ),
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
    requested_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    local_warnings: list[str] = []
    compatible, block_messages = _check_tool_compatibility(
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
                allow_missing_required=True,
            )
            execution_ok, resolved_execution, execution_errors = (
                _resolve_run_execution(
                    run_id=tool_id,
                    toolspec=toolspec,
                    user_execution=requested_execution or {},
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
                requested_execution={
                    "mode": toolspec.get("_andrea_execution_mode")
                },
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
