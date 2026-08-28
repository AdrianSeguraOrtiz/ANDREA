"""Phase A: Input preflight for infer-network.

Phase dependencies:
1. Catalog + schema constraints resolution.
2. Dataset manifest parsing + declarative input validation.
3. Tool request compatibility + parameter validation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from andrea.core.shared.issues import make_issue

from .commons.artifacts import _serialize_dataset_context
from .commons.catalog import _load_schema_constraints, _resolve_catalog_paths
from .commons.custom_tools import custom_tool_warnings, load_custom_tool_registry
from .commons.dataset import (
    _load_input_specs,
    _parse_dataset_context,
    _validate_dataset_inputs_by_specs,
)
from .commons.shared import INPUT_SPECS_DIR, PREFLIGHT_SCHEMA_VERSION
from .commons.tools import (
    _check_tool_compatibility,
    _collect_compatibility_rule_issues,
    _collect_conditional_input_issues,
    _load_tools_params,
    _load_toolspec,
    _resolve_run_execution,
    _resolve_tool_params,
    _scan_catalog_compatibility,
)


def preflight_infer_network(
    *,
    dataset_manifest_path: Path,
    tools_params_path: Optional[Path] = None,
    custom_tools_path: Optional[Path] = None,
) -> dict[str, Any]:
    tools_root, schemas_dir = _resolve_catalog_paths()
    constraints = _load_schema_constraints(schemas_dir)
    custom_tools, custom_blocked_entries = load_custom_tool_registry(
        custom_tools_path=custom_tools_path,
        tools_root=tools_root,
        constraints=constraints,
    )
    dataset = _parse_dataset_context(
        dataset_manifest_path=dataset_manifest_path,
        constraints=constraints,
    )
    input_specs = _load_input_specs()
    input_validation, validation_errors, validation_warnings = (
        _validate_dataset_inputs_by_specs(
            dataset=dataset,
            input_specs=input_specs,
        )
    )
    if validation_errors:
        raise ValueError("Input validation failed: " + "; ".join(validation_errors))

    issues: list[dict[str, Any]] = [
        make_issue(severity="warn", code="input_validation", message=message)
        for message in validation_warnings
    ]
    catalog = _scan_catalog_compatibility(
        tools_root=tools_root,
        dataset=dataset,
        constraints=constraints,
        custom_tools=custom_tools,
        custom_blocked_entries=custom_blocked_entries,
    )

    selected_tools: list[str] = []
    selected_tool_catalog_ids: dict[str, str] = {}
    selected_tool_origins: dict[str, str] = {}
    skipped_tools: dict[str, str] = {}
    resolved_params_by_tool: dict[str, dict[str, Any]] = {}
    resolved_execution_by_tool: dict[str, dict[str, Any]] = {}
    run_issues: dict[str, list[dict[str, Any]]] = {}
    requested_total = 0

    def add_run_issue(
        run_id: str,
        *,
        severity: str,
        code: str,
        message: str,
    ) -> None:
        run_issues.setdefault(run_id, []).append(
            make_issue(
                severity=severity,
                code=code,
                message=message,
                run_id=run_id,
            )
        )

    def add_run_warnings(run_id: str, code: str, messages: list[str]) -> None:
        for message in messages:
            add_run_issue(run_id, severity="warn", code=code, message=message)

    if tools_params_path is not None:
        custom_run_ids_by_tool_id = {
            tool_id: toolspec["_andrea_run_id"]
            for tool_id, toolspec in custom_tools.items()
        }
        tools_params = _load_tools_params(
            tools_params_path,
            custom_run_ids_by_tool_id=custom_run_ids_by_tool_id,
        )
        requested_total = len(tools_params)
        for run_id, run_spec in tools_params.items():
            requested_tool_id = str(run_spec.get("tool_id", "")).strip()
            catalog_tool_id = requested_tool_id
            user_params = run_spec.get("params", {})
            user_execution = run_spec.get("execution", {})
            if not catalog_tool_id:
                add_run_issue(
                    run_id,
                    severity="block",
                    code="invalid_request",
                    message="invalid tool request (missing tool_id)",
                )
                skipped_tools[run_id] = "invalid tool request: missing tool_id"
                continue
            if not isinstance(user_params, dict):
                add_run_issue(
                    run_id,
                    severity="block",
                    code="invalid_request",
                    message="invalid tool request (params must be object)",
                )
                skipped_tools[run_id] = "invalid tool request: params must be object"
                continue
            if not isinstance(user_execution, dict):
                add_run_issue(
                    run_id,
                    severity="block",
                    code="invalid_request",
                    message="invalid tool request (execution must be object)",
                )
                skipped_tools[run_id] = "invalid tool request: execution must be object"
                continue

            tool_origin = "custom" if catalog_tool_id in custom_tools else "catalog"
            try:
                toolspec = (
                    custom_tools[catalog_tool_id]
                    if tool_origin == "custom"
                    else _load_toolspec(tools_root, catalog_tool_id)
                )
            except ValueError as exc:
                add_run_issue(
                    run_id,
                    severity="block",
                    code="unknown_tool",
                    message=str(exc),
                )
                skipped_tools[run_id] = str(exc)
                continue
            if tool_origin == "custom":
                custom_run_id = toolspec.get("_andrea_run_id")
                if run_id != custom_run_id:
                    message = (
                        f"Custom tool {catalog_tool_id!r} is bound to run_id "
                        f"{custom_run_id!r}; tools_params run_id must exactly match "
                        "custom_tools.tools[].run_id"
                    )
                    add_run_issue(
                        run_id,
                        severity="block",
                        code="invalid_request",
                        message=message,
                    )
                    skipped_tools[run_id] = message
                    continue
                required_execution = {
                    "mode": toolspec.get("_andrea_execution_mode")
                }
                if user_execution != required_execution:
                    message = (
                        f"Custom tool {catalog_tool_id!r} requires tools_params "
                        f"execution to be exactly {required_execution!r}; its "
                        "definition execution_mode is fixed"
                    )
                    add_run_issue(
                        run_id,
                        severity="block",
                        code="invalid_execution",
                        message=message,
                    )
                    skipped_tools[run_id] = message
                    continue
            compatibility_warnings: list[str] = []
            compatible, compat_errors, _conditional_messages = (
                _check_tool_compatibility(
                    tool_id=run_id,
                    toolspec=toolspec,
                    dataset=dataset,
                    constraints=constraints,
                    warnings=compatibility_warnings,
                )
            )
            compatibility_warnings.extend(
                custom_tool_warnings(catalog_tool_id, toolspec)
            )
            add_run_warnings(run_id, "compatibility_warning", compatibility_warnings)
            if not compatible:
                skipped_tools[run_id] = "; ".join(compat_errors)
                for message in compat_errors:
                    add_run_issue(
                        run_id,
                        severity="block",
                        code="compatibility",
                        message=message,
                    )
                continue

            toolspec_params = toolspec.get("params", {})
            if not isinstance(toolspec_params, dict):
                add_run_issue(
                    run_id,
                    severity="block",
                    code="invalid_toolspec",
                    message="toolspec.params is not an object",
                )
                skipped_tools[run_id] = "invalid toolspec.params"
                continue

            param_warnings: list[str] = []
            valid_params, resolved_params, param_errors = _resolve_tool_params(
                tool_id=run_id,
                user_params=user_params,
                toolspec_params=toolspec_params,
                warnings=param_warnings,
                passthrough_unknown_params=bool(
                    toolspec.get("_andrea_custom_tool") and not toolspec_params
                ),
            )
            add_run_warnings(run_id, "param_warning", param_warnings)
            if not valid_params:
                skipped_tools[run_id] = "; ".join(param_errors)
                for message in param_errors:
                    add_run_issue(
                        run_id,
                        severity="block",
                        code="invalid_params",
                        message=message,
                    )
                continue

            execution_warnings: list[str] = []
            valid_execution, resolved_execution, execution_errors = (
                _resolve_run_execution(
                    run_id=run_id,
                    toolspec=toolspec,
                    user_execution=user_execution,
                    warnings=execution_warnings,
                )
            )
            add_run_warnings(run_id, "execution_warning", execution_warnings)
            if not valid_execution:
                skipped_tools[run_id] = "; ".join(execution_errors)
                for message in execution_errors:
                    add_run_issue(
                        run_id,
                        severity="block",
                        code="invalid_execution",
                        message=message,
                    )
                continue

            rule_blocks, rule_warnings, rule_errors = (
                _collect_compatibility_rule_issues(
                    tool_id=run_id,
                    toolspec=toolspec,
                    dataset=dataset,
                    resolved_params=resolved_params,
                    resolved_execution=resolved_execution,
                )
            )
            if rule_errors:
                message = "; ".join(
                    f"invalid compatibility rule: {item}" for item in rule_errors
                )
                add_run_issue(
                    run_id,
                    severity="block",
                    code="invalid_compatibility_rule",
                    message=message,
                )
                skipped_tools[run_id] = message
                continue
            if rule_blocks:
                message = "; ".join(rule_blocks)
                add_run_issue(
                    run_id,
                    severity="block",
                    code="compatibility_rule",
                    message=message,
                )
                skipped_tools[run_id] = message
                continue
            add_run_warnings(run_id, "compatibility_rule", rule_warnings)

            selected_tools.append(run_id)
            selected_tool_catalog_ids[run_id] = catalog_tool_id
            selected_tool_origins[run_id] = tool_origin
            resolved_params_by_tool[run_id] = resolved_params
            resolved_execution_by_tool[run_id] = resolved_execution
            conditional_input_messages = _collect_conditional_input_issues(
                tool_id=run_id,
                toolspec=toolspec,
                dataset=dataset,
                resolved_params=resolved_params,
                resolved_execution=resolved_execution,
            )
            for message in conditional_input_messages:
                add_run_issue(
                    run_id,
                    severity="block",
                    code="conditional_required",
                    message=message,
                )

    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "inputs": {
            "dataset_id": dataset.dataset_id,
            "tools_params": "provided" if tools_params_path else "catalog_defaults",
            "custom_tools": "provided" if custom_tools_path else "none",
        },
        "dataset": _serialize_dataset_context(dataset),
        "input_validation": input_validation,
        "catalog": catalog,
        "runs": {
            "requested_total": requested_total,
            "selected": selected_tools,
            "catalog_tool_ids": selected_tool_catalog_ids,
            "tool_origins": selected_tool_origins,
            "resolved_params": resolved_params_by_tool,
            "resolved_execution": resolved_execution_by_tool,
            "issues": run_issues,
            "skipped": skipped_tools,
        },
        "issues": issues,
    }
