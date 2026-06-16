"""Run-local external Docker tool definitions for infer-network."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from andrea.core.shared.issues import make_issue

from .shared import SchemaConstraints, _load_json_object, _slugify_token
from .tools import EXECUTION_CAPABILITIES, _validate_execution_capability_contract

CUSTOM_TOOL_PREFIX = "custom_"

_DEFAULT_CUSTOM_TOOL_OUTPUTS = {
    "directed": True,
    "sign": "mixed",
    "evidence": "external_tool_output",
}
_CUSTOM_TOOL_SCHEMA_KEYS = {
    "run_id",
    "name",
    "docker_image",
    "execution_mode",
    "extra_inputs",
}


def _catalog_tool_ids(tools_root: Path) -> set[str]:
    return {
        path.parent.name
        for path in tools_root.glob("*/toolspec.json")
        if path.parent.name
    }


def normalize_custom_tool_id(raw_id: Any) -> str:
    slug = _slugify_token(str(raw_id or "").strip())
    if not slug:
        raise ValueError("custom run_id is required")
    if not slug.startswith(CUSTOM_TOOL_PREFIX):
        slug = f"{CUSTOM_TOOL_PREFIX}{slug}"
    return slug


def _raw_custom_tool_entries(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(raw.get("tools"), list):
        entries = raw["tools"]
        if not all(isinstance(item, dict) for item in entries):
            raise ValueError("custom_tools.tools entries must be objects")
        return entries
    raise ValueError("custom_tools JSON must contain a tools array")


def _unexpected_custom_tool_keys(raw_tool: dict[str, Any]) -> list[str]:
    return sorted(set(raw_tool) - _CUSTOM_TOOL_SCHEMA_KEYS)


def _normalize_execution_mode(raw_tool: dict[str, Any]) -> str:
    return str(raw_tool.get("execution_mode", "")).strip()


def _capabilities_for_execution_mode(execution_mode: str) -> list[str]:
    if execution_mode == "group_aggregated":
        return ["cell_native", "group_aggregated"]
    return [execution_mode]


def _normalize_extra_usage_items(raw_items: Any) -> list[dict[str, str]]:
    if not isinstance(raw_items, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        input_key = str(item or "").strip()
        usage = "External Docker tool declares this standardized input as needed for execution."
        if not input_key or input_key in seen:
            continue
        seen.add(input_key)
        out.append({"input": input_key, "usage": usage})
    return out


def _normalize_extra_inputs(raw_extra_inputs: Any) -> dict[str, Any]:
    return {
        "required": _normalize_extra_usage_items(raw_extra_inputs),
        "optional": [],
    }


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
    return bool(toolspec.get("_andrea_custom_tool"))


def normalize_custom_tools_payload(
    *,
    payload: dict[str, Any],
    tools_root: Path,
    constraints: SchemaConstraints,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    catalog_ids = _catalog_tool_ids(tools_root)
    specs: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    blocked_entries: list[dict[str, Any]] = []

    for idx, raw_tool in enumerate(_raw_custom_tool_entries(payload), start=1):
        unexpected = _unexpected_custom_tool_keys(raw_tool)
        run_id = str(raw_tool.get("run_id", "")).strip()
        display_id = run_id or f"custom_tool_{idx}"
        try:
            tool_id = normalize_custom_tool_id(display_id)
        except ValueError as exc:
            blocked_entries.append(
                _blocked_custom_entry(display_id, f"invalid run_id: {exc}")
            )
            continue

        errors: list[str] = []
        if unexpected:
            errors.append(f"unsupported keys: {', '.join(unexpected)}")
        if not run_id:
            errors.append("run_id is required")
        if tool_id in catalog_ids:
            errors.append(
                f"derived custom tool_id collides with catalog tool id: {tool_id}"
            )
        if tool_id in specs:
            errors.append(f"duplicate custom run_id derives tool_id: {tool_id}")

        name = str(raw_tool.get("name", "")).strip() or tool_id
        docker_image = str(raw_tool.get("docker_image", "")).strip()
        if not docker_image:
            errors.append("docker_image is required")

        execution_mode = _normalize_execution_mode(raw_tool)
        if not execution_mode:
            errors.append("execution_mode is required")
            capabilities: list[str] = []
        else:
            if execution_mode not in EXECUTION_CAPABILITIES:
                errors.append(f"unsupported execution_mode: {execution_mode}")
                capabilities = []
            else:
                capabilities = _capabilities_for_execution_mode(execution_mode)
            try:
                _validate_execution_capability_contract(
                    tool_id=tool_id,
                    capabilities=capabilities,
                )
            except ValueError as exc:
                errors.append(str(exc))

        raw_extra_inputs = raw_tool.get("extra_inputs", [])
        if raw_extra_inputs is None:
            raw_extra_inputs = []
        if not isinstance(raw_extra_inputs, list) or not all(
            isinstance(item, str) and item.strip() for item in raw_extra_inputs
        ):
            errors.append("extra_inputs must be an array of non-empty strings")

        if errors:
            blocked_entries.append(_blocked_custom_entry(tool_id, "; ".join(errors)))
            continue

        spec = {
            "schema_version": "custom-1.0",
            "id": tool_id,
            "name": name,
            "publication": "external_docker_image",
            "first_author": "User-provided",
            "year": datetime.now(timezone.utc).year,
            "method_summary": "User-provided external Docker inference tool.",
            "method_keywords": ["custom", "docker"],
            "implementation_url": "",
            "docker_image": docker_image,
            "execution_capabilities": capabilities,
            "taxonomic_scope": {
                "allowed_groups": sorted(constraints.taxonomic_groups),
                "supported_species": [],
            },
            "compatibility_rules": [],
            "accepts": sorted(constraints.column_kinds),
            "assumes": "generic",
            "extra_inputs": _normalize_extra_inputs(raw_extra_inputs),
            "artifacts_aux": [],
            # Internal ToolSpec-shaped metadata only. The external image is
            # responsible for writing sign/evidence columns in network.csv.
            "outputs": dict(_DEFAULT_CUSTOM_TOOL_OUTPUTS),
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
        aliases[tool_id] = tool_id

    return specs, aliases, blocked_entries


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
) -> tuple[dict[str, dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    if custom_tools_path is None:
        return {}, {}, []
    raw = _load_json_object(custom_tools_path, "custom_tools")
    return normalize_custom_tools_payload(
        payload=raw,
        tools_root=tools_root,
        constraints=constraints,
    )


def serialize_custom_tools(
    custom_specs: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    tools: list[dict[str, Any]] = []
    for tool_id in sorted(custom_specs):
        source = custom_specs[tool_id]
        required_extras = source.get("extra_inputs", {}).get("required", [])
        spec = {
            "run_id": source.get("_andrea_run_id", tool_id.removeprefix(CUSTOM_TOOL_PREFIX)),
            "name": source.get("name", tool_id),
            "docker_image": source.get("docker_image", ""),
            "execution_mode": source.get("_andrea_execution_mode", "global"),
            "extra_inputs": [
                str(item.get("input", "")).strip()
                for item in required_extras
                if isinstance(item, dict) and str(item.get("input", "")).strip()
            ],
        }
        tools.append(spec)
    return {"tools": tools}
