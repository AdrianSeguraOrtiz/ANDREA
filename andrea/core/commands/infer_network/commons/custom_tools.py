"""Run-local external Docker tool definitions for infer-network."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from andrea.core.shared.issues import make_issue
from andrea.core.shared.output_capabilities import OUTPUT_SIGN_SEMANTICS
from andrea.core.shared.paths import validate_portable_identifier

from .shared import SchemaConstraints, _load_json_object
from .tools import EXECUTION_CAPABILITIES, _validate_execution_capability_contract

CUSTOM_TOOL_PREFIX = "custom_"

_CUSTOM_TOOL_EVIDENCE = "external_tool_output"
_CUSTOM_TOOL_SCHEMA_KEYS = {
    "run_id",
    "name",
    "docker_image",
    "execution_mode",
    "extra_inputs",
    "outputs",
}
_CUSTOM_TOOL_OUTPUT_KEYS = {"directed", "sign"}


def _catalog_tool_ids(tools_root: Path) -> set[str]:
    return {
        path.parent.name
        for path in tools_root.glob("*/toolspec.json")
        if path.parent.name
    }


def normalize_custom_tool_id(raw_id: Any) -> str:
    run_id = validate_portable_identifier(raw_id, label="custom run_id")
    return f"{CUSTOM_TOOL_PREFIX}{run_id}"


def _raw_custom_tool_entries(raw: dict[str, Any]) -> list[dict[str, Any]]:
    unexpected = sorted(set(raw) - {"tools"})
    if unexpected:
        raise ValueError(
            "custom_tools JSON has unsupported top-level keys: " + ", ".join(unexpected)
        )
    if isinstance(raw.get("tools"), list):
        entries = raw["tools"]
        if not all(isinstance(item, dict) for item in entries):
            raise ValueError("custom_tools.tools entries must be objects")
        return entries
    raise ValueError("custom_tools JSON must contain a tools array")


def _unexpected_custom_tool_keys(raw_tool: dict[str, Any]) -> list[str]:
    return sorted(set(raw_tool) - _CUSTOM_TOOL_SCHEMA_KEYS)


def _capabilities_for_execution_mode(execution_mode: str) -> list[str]:
    if execution_mode == "group_aggregated":
        return ["column_native", "group_aggregated"]
    return [execution_mode]


def _normalize_extra_usage_items(raw_items: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in raw_items:
        usage = "External Docker tool declares this standardized input as needed for execution."
        out.append({"input": item, "usage": usage})
    return out


def _normalize_extra_inputs(raw_extra_inputs: list[str]) -> dict[str, Any]:
    return {
        "required": _normalize_extra_usage_items(raw_extra_inputs),
        "optional": [],
    }


def normalize_custom_tool_outputs(
    raw_outputs: Any,
) -> tuple[dict[str, Any], list[str]]:
    """Validate the explicit output semantics required for a custom tool."""
    outputs: dict[str, Any] = {"evidence": _CUSTOM_TOOL_EVIDENCE}
    if raw_outputs is None:
        return outputs, ["outputs is required"]
    if not isinstance(raw_outputs, dict):
        return outputs, ["outputs must be an object"]

    errors: list[str] = []
    unexpected = sorted(set(raw_outputs) - _CUSTOM_TOOL_OUTPUT_KEYS)
    if unexpected:
        errors.append(f"outputs has unsupported keys: {', '.join(unexpected)}")

    directed = raw_outputs.get("directed")
    if "directed" not in raw_outputs:
        errors.append("outputs.directed is required")
    elif not isinstance(directed, bool):
        errors.append("outputs.directed must be a boolean")
    else:
        outputs["directed"] = directed

    sign = raw_outputs.get("sign")
    if "sign" not in raw_outputs:
        errors.append("outputs.sign is required")
    elif not isinstance(sign, str) or sign not in OUTPUT_SIGN_SEMANTICS:
        errors.append("outputs.sign must be one of: none, signed, mixed")
    else:
        outputs["sign"] = sign

    return outputs, errors


def custom_tool_warnings(tool_id: str, toolspec: dict[str, Any]) -> list[str]:
    if not is_custom_toolspec(toolspec):
        return []
    warnings = [
        f"[{tool_id}] external Docker tool is user-provided and has no cost.json; fallback runtime estimation will be used.",
        f"[{tool_id}] external Docker images execute arbitrary code; only run trusted images.",
    ]
    docker_image = str(toolspec.get("docker_image", "")).strip()
    if docker_image.endswith(":latest") or ":" not in docker_image.split("/")[-1]:
        warnings.append(
            f"[{tool_id}] docker_image is not pinned to an explicit non-latest tag or digest."
        )
    return warnings


def is_custom_toolspec(toolspec: dict[str, Any]) -> bool:
    return toolspec.get("_andrea_custom_tool") is True


def normalize_custom_tools_payload(
    *,
    payload: dict[str, Any],
    tools_root: Path,
    constraints: SchemaConstraints,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    catalog_ids = _catalog_tool_ids(tools_root)
    specs: dict[str, dict[str, Any]] = {}
    blocked_entries: list[dict[str, Any]] = []
    seen_tool_ids: set[str] = set()

    for idx, raw_tool in enumerate(_raw_custom_tool_entries(payload), start=1):
        unexpected = _unexpected_custom_tool_keys(raw_tool)
        if "run_id" not in raw_tool:
            raise ValueError(f"custom_tools.tools[{idx}].run_id is required")
        try:
            tool_id = normalize_custom_tool_id(raw_tool["run_id"])
        except ValueError as exc:
            raise ValueError(f"custom_tools.tools[{idx}].run_id is invalid: {exc}") from exc
        run_id = raw_tool["run_id"]
        if tool_id in seen_tool_ids:
            raise ValueError(
                f"custom_tools.tools[{idx}].run_id duplicates derived tool_id: "
                f"{tool_id}"
            )
        seen_tool_ids.add(tool_id)

        errors: list[str] = []
        if unexpected:
            errors.append(f"unsupported keys: {', '.join(unexpected)}")
        if tool_id in catalog_ids:
            errors.append(
                f"derived custom tool_id collides with catalog tool id: {tool_id}"
            )

        name = raw_tool.get("name")
        if "name" not in raw_tool:
            errors.append("name is required")
        elif not isinstance(name, str) or not name or name != name.strip():
            errors.append(
                "name must be a non-empty string without surrounding whitespace"
            )

        docker_image = raw_tool.get("docker_image")
        if "docker_image" not in raw_tool:
            errors.append("docker_image is required")
        elif (
            not isinstance(docker_image, str)
            or not docker_image
            or docker_image != docker_image.strip()
        ):
            errors.append(
                "docker_image must be a non-empty string without surrounding whitespace"
            )

        execution_mode = raw_tool.get("execution_mode")
        if "execution_mode" not in raw_tool:
            errors.append("execution_mode is required")
            capabilities: list[str] = []
        elif not isinstance(execution_mode, str):
            errors.append("execution_mode must be a string")
            capabilities = []
        elif execution_mode not in EXECUTION_CAPABILITIES:
            errors.append(f"unsupported execution_mode: {execution_mode}")
            capabilities = []
        else:
            capabilities = _capabilities_for_execution_mode(execution_mode)
        if capabilities:
            try:
                _validate_execution_capability_contract(
                    tool_id=tool_id,
                    capabilities=capabilities,
                )
            except ValueError as exc:
                errors.append(str(exc))

        raw_extra_inputs = raw_tool.get("extra_inputs")
        if "extra_inputs" not in raw_tool:
            errors.append("extra_inputs is required")
            extra_inputs: list[str] = []
        elif not isinstance(raw_extra_inputs, list) or not all(
            isinstance(item, str) and item and item == item.strip()
            for item in raw_extra_inputs
        ):
            errors.append(
                "extra_inputs must be an array of non-empty strings without "
                "surrounding whitespace"
            )
            extra_inputs = []
        elif len(set(raw_extra_inputs)) != len(raw_extra_inputs):
            errors.append("extra_inputs must not contain duplicates")
            extra_inputs = []
        else:
            extra_inputs = list(raw_extra_inputs)
            unsupported_extras = sorted(
                set(extra_inputs).difference(constraints.extra_input_keys)
            )
            if unsupported_extras:
                errors.append(
                    "extra_inputs contains unsupported standardized inputs: "
                    + ", ".join(unsupported_extras)
                )

        outputs, output_errors = normalize_custom_tool_outputs(raw_tool.get("outputs"))
        errors.extend(output_errors)

        if errors:
            blocked_entries.append(_blocked_custom_entry(tool_id, "; ".join(errors)))
            continue

        spec = {
            "schema_version": "custom-1.0",
            "id": tool_id,
            "name": name,
            "publication": "external_docker_image",
            "first_author": "User-provided",
            "year": datetime.now(UTC).year,
            "method_summary": "User-provided external Docker inference tool.",
            "method_keywords": ["custom", "docker"],
            "implementation_url": "",
            "docker_image": docker_image,
            "execution_capabilities": capabilities,
            "runtime_resources": {
                "threading": {
                    "supported": False,
                    "default_threads": 1,
                    "max_threads": 1,
                    "upstream_mapping": (
                        "External Docker tool threading is not catalog-audited; "
                        "ANDREA assigns one thread by default."
                    ),
                }
            },
            "taxonomic_scope": {
                "allowed_groups": sorted(constraints.taxonomic_groups),
                "supported_species": [],
            },
            "compatibility_rules": [],
            "accepts": sorted(constraints.column_kinds),
            "assumes": "generic",
            "extra_inputs": _normalize_extra_inputs(extra_inputs),
            "artifacts_aux": [],
            # Internal ToolSpec-shaped metadata only. The external image is
            # responsible for writing sign/evidence columns in network.csv.
            "outputs": outputs,
            "progress": {
                "kind": "none",
                "note": "External tool progress is read from /io/out/progress.json when provided.",
            },
            "params": {},
            "_andrea_custom_tool": True,
            "_andrea_run_id": run_id,
            "_andrea_execution_mode": execution_mode,
        }
        specs[tool_id] = spec

    return specs, blocked_entries


def _blocked_custom_entry(tool_id: str, message: str) -> dict[str, Any]:
    return {
        "tool_id": tool_id,
        "status": "blocked",
        "tool_origin": "custom",
        "issues": [
            make_issue(
                severity="block",
                code="invalid_custom_tool",
                message=message,
                tool_id=tool_id,
            )
        ],
    }


def load_custom_tool_registry(
    *,
    custom_tools_path: Path | None,
    tools_root: Path,
    constraints: SchemaConstraints,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if custom_tools_path is None:
        return {}, []
    raw = _load_json_object(custom_tools_path, "custom_tools")
    return normalize_custom_tools_payload(
        payload=raw,
        tools_root=tools_root,
        constraints=constraints,
    )


def _serialization_error(tool_id: str, message: str) -> ValueError:
    return ValueError(f"Cannot serialize custom tool {tool_id!r}: {message}")


def _required_internal_string(
    *,
    source: dict[str, Any],
    key: str,
    tool_id: str,
) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise _serialization_error(
            tool_id,
            f"{key} must be a non-empty string without surrounding whitespace",
        )
    return value


def _serialize_custom_tool(tool_id: str, source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise _serialization_error(tool_id, "internal toolspec must be an object")
    if source.get("_andrea_custom_tool") is not True:
        raise _serialization_error(tool_id, "_andrea_custom_tool must be true")

    internal_id = _required_internal_string(
        source=source,
        key="id",
        tool_id=tool_id,
    )
    if internal_id != tool_id:
        raise _serialization_error(tool_id, "id must match the registry key")

    run_id = _required_internal_string(
        source=source,
        key="_andrea_run_id",
        tool_id=tool_id,
    )
    if normalize_custom_tool_id(run_id) != tool_id:
        raise _serialization_error(
            tool_id,
            "_andrea_run_id must derive the registry tool ID",
        )
    name = _required_internal_string(source=source, key="name", tool_id=tool_id)
    docker_image = _required_internal_string(
        source=source,
        key="docker_image",
        tool_id=tool_id,
    )
    execution_mode = _required_internal_string(
        source=source,
        key="_andrea_execution_mode",
        tool_id=tool_id,
    )
    if execution_mode not in EXECUTION_CAPABILITIES:
        raise _serialization_error(
            tool_id,
            f"unsupported _andrea_execution_mode: {execution_mode}",
        )
    expected_capabilities = _capabilities_for_execution_mode(execution_mode)
    if source.get("execution_capabilities") != expected_capabilities:
        raise _serialization_error(
            tool_id,
            "execution_capabilities must match _andrea_execution_mode",
        )

    raw_extra_inputs = source.get("extra_inputs")
    if (
        not isinstance(raw_extra_inputs, dict)
        or set(raw_extra_inputs) != {"required", "optional"}
        or raw_extra_inputs.get("optional") != []
        or not isinstance(raw_extra_inputs.get("required"), list)
    ):
        raise _serialization_error(
            tool_id,
            "extra_inputs must contain exactly required and empty optional arrays",
        )
    extra_inputs: list[str] = []
    for idx, item in enumerate(raw_extra_inputs["required"]):
        if not isinstance(item, dict) or set(item) != {"input", "usage"}:
            raise _serialization_error(
                tool_id,
                f"extra_inputs.required[{idx}] must contain exactly input and usage",
            )
        input_key = item["input"]
        usage = item["usage"]
        if (
            not isinstance(input_key, str)
            or not input_key
            or input_key != input_key.strip()
        ):
            raise _serialization_error(
                tool_id,
                f"extra_inputs.required[{idx}].input must be canonical",
            )
        if not isinstance(usage, str) or not usage.strip():
            raise _serialization_error(
                tool_id,
                f"extra_inputs.required[{idx}].usage is required",
            )
        extra_inputs.append(input_key)
    if len(set(extra_inputs)) != len(extra_inputs):
        raise _serialization_error(tool_id, "extra_inputs contains duplicates")

    source_outputs = source.get("outputs")
    if (
        not isinstance(source_outputs, dict)
        or set(source_outputs) != {"directed", "sign", "evidence"}
        or source_outputs.get("evidence") != _CUSTOM_TOOL_EVIDENCE
    ):
        raise _serialization_error(
            tool_id,
            "outputs must contain the canonical directed, sign and evidence fields",
        )
    outputs, output_errors = normalize_custom_tool_outputs(
        {
            "directed": source_outputs["directed"],
            "sign": source_outputs["sign"],
        }
    )
    if output_errors:
        raise _serialization_error(tool_id, "; ".join(output_errors))

    return {
        "run_id": run_id,
        "name": name,
        "docker_image": docker_image,
        "execution_mode": execution_mode,
        "extra_inputs": extra_inputs,
        "outputs": {
            "directed": outputs["directed"],
            "sign": outputs["sign"],
        },
    }


def serialize_custom_tools(
    custom_specs: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(custom_specs, dict):
        raise TypeError("custom_specs must be an object")
    if not all(
        isinstance(tool_id, str) and tool_id and tool_id == tool_id.strip()
        for tool_id in custom_specs
    ):
        raise ValueError(
            "custom_specs keys must be non-empty strings without surrounding whitespace"
        )
    tools = [
        _serialize_custom_tool(tool_id, custom_specs[tool_id])
        for tool_id in sorted(custom_specs)
    ]
    return {"tools": tools}
