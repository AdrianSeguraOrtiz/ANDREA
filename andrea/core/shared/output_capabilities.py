"""Frozen output-capability contracts shared by inference consumers."""

from __future__ import annotations

from typing import Any, Mapping

from andrea.core.shared.dataset_identity import validate_dataset_fingerprint
from andrea.core.shared.paths import validate_portable_identifier

OUTPUT_CAPABILITY_KEYS = frozenset(
    {"tool_origin", "catalog_tool_id", "directed", "sign"}
)
OUTPUT_SIGN_SEMANTICS = frozenset({"none", "signed", "mixed"})
TOOL_ORIGINS = frozenset({"catalog", "custom"})


def validate_selected_tool_identity_maps(
    tools: Any,
    *,
    label: str,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Validate the exact selected-run identity maps without coercion."""
    if not isinstance(tools, dict):
        raise ValueError(f"{label} must be an object")

    selected = tools.get("selected")
    if (
        not isinstance(selected, list)
        or not selected
        or not all(
            isinstance(run_id, str)
            and bool(run_id)
            and run_id == run_id.strip()
            for run_id in selected
        )
        or len(set(selected)) != len(selected)
    ):
        raise ValueError(
            f"{label}.selected must be a non-empty array of unique run IDs "
            "without surrounding whitespace"
        )

    selected_ids = set(selected)
    catalog_ids = tools.get("catalog_tool_ids")
    origins = tools.get("tool_origins")
    for field_name, mapping in (
        ("catalog_tool_ids", catalog_ids),
        ("tool_origins", origins),
    ):
        if not isinstance(mapping, dict) or set(mapping) != selected_ids:
            raise ValueError(
                f"{label}.{field_name} must be an object with exactly the "
                f"{label}.selected keys"
            )

    validated_catalog_ids: dict[str, str] = {}
    validated_origins: dict[str, str] = {}
    for run_id in selected:
        catalog_tool_id = validate_portable_identifier(
            catalog_ids[run_id],
            label=f"{label}.catalog_tool_ids[{run_id!r}]",
        )
        validated_catalog_ids[run_id] = catalog_tool_id

        tool_origin = origins[run_id]
        if not isinstance(tool_origin, str) or tool_origin not in TOOL_ORIGINS:
            allowed = ", ".join(sorted(TOOL_ORIGINS))
            raise ValueError(
                f"{label}.tool_origins[{run_id!r}]: tool_origin must be one "
                f"of: {allowed}"
            )
        validated_origins[run_id] = tool_origin
        if tool_origin == "custom":
            validate_portable_identifier(
                run_id,
                label=f"{label}.selected custom run ID",
            )
            expected_custom_tool_id = f"custom_{run_id}"
            if catalog_tool_id != expected_custom_tool_id:
                raise ValueError(
                    f"{label}.catalog_tool_ids[{run_id!r}] must be exactly "
                    f"{expected_custom_tool_id!r} when tool_origin is 'custom'"
                )

    return list(selected), validated_catalog_ids, validated_origins


def validate_frozen_output_capabilities(
    tools: Any,
    *,
    label: str,
) -> dict[str, dict[str, Any]]:
    """Validate and return the immutable per-run output declarations."""
    selected, catalog_ids, origins = validate_selected_tool_identity_maps(
        tools,
        label=label,
    )
    selected_ids = set(selected)
    capabilities = tools.get("output_capabilities")
    if not isinstance(capabilities, dict) or set(capabilities) != selected_ids:
        raise ValueError(
            f"{label}.output_capabilities must be an object with exactly the "
            f"{label}.selected keys"
        )

    validated: dict[str, dict[str, Any]] = {}
    for run_id in selected:
        capability = capabilities[run_id]
        capability_label = f"{label}.output_capabilities[{run_id!r}]"
        if (
            not isinstance(capability, dict)
            or set(capability) != OUTPUT_CAPABILITY_KEYS
        ):
            raise ValueError(
                f"{capability_label} must contain exactly tool_origin, "
                "catalog_tool_id, directed and sign"
            )

        tool_origin = capability["tool_origin"]
        if not isinstance(tool_origin, str) or tool_origin not in TOOL_ORIGINS:
            allowed = ", ".join(sorted(TOOL_ORIGINS))
            raise ValueError(
                f"{capability_label}.tool_origin must be one of: {allowed}"
            )
        if origins[run_id] != tool_origin:
            raise ValueError(
                f"{capability_label}.tool_origin must match {label}.tool_origins"
            )

        catalog_tool_id = capability["catalog_tool_id"]
        if catalog_ids[run_id] != catalog_tool_id:
            raise ValueError(
                f"{capability_label}.catalog_tool_id must match "
                f"{label}.catalog_tool_ids"
            )

        if not isinstance(capability["directed"], bool):
            raise ValueError(f"{capability_label}.directed must be a boolean")
        if (
            not isinstance(capability["sign"], str)
            or capability["sign"] not in OUTPUT_SIGN_SEMANTICS
        ):
            allowed = ", ".join(sorted(OUTPUT_SIGN_SEMANTICS))
            raise ValueError(f"{capability_label}.sign must be one of: {allowed}")

        validated[run_id] = dict(capability)
    return validated


def validate_selected_object_map(
    tools: Any,
    *,
    field: str,
    selected: list[str],
    label: str,
) -> dict[str, dict[str, Any]]:
    """Validate an object-valued map keyed by every selected run exactly."""
    if not isinstance(tools, dict):
        raise ValueError(f"{label} must be an object")
    raw_mapping = tools.get(field)
    selected_ids = set(selected)
    if not isinstance(raw_mapping, dict) or set(raw_mapping) != selected_ids:
        raise ValueError(
            f"{label}.{field} must be an object with exactly the "
            f"{label}.selected keys"
        )
    validated: dict[str, dict[str, Any]] = {}
    for run_id in selected:
        value = raw_mapping[run_id]
        if not isinstance(value, dict):
            raise ValueError(f"{label}.{field}[{run_id!r}] must be an object")
        validated[run_id] = dict(value)
    return validated


def validate_final_inference_report(
    run_report: Any,
    *,
    selected: list[str],
    observed_rows_per_tool: Mapping[str, int] | None = None,
    label: str = "Run report",
) -> dict[str, int]:
    """Validate only the terminal fields consumed by evaluation."""
    if not isinstance(run_report, dict):
        raise ValueError(f"{label} must be an object")
    report = run_report
    if report.get("status") != "executed":
        raise ValueError(f"{label} status must be exactly 'executed' for evaluation")
    report_run_id = report.get("run_id")
    if not isinstance(report_run_id, str) or not report_run_id.strip():
        raise ValueError(f"{label} run_id must be a non-empty string")
    if not isinstance(report.get("execution"), dict):
        raise ValueError(f"{label} execution must be an object")
    if not isinstance(report.get("issues"), list):
        raise ValueError(f"{label} issues must be an array")

    dataset = report.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError(f"{label} dataset must be an object")
    dataset_id = dataset.get("id")
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError(f"{label} dataset.id must be a non-empty string")
    validate_dataset_fingerprint(
        dataset.get("fingerprint"),
        label=f"{label} dataset.fingerprint",
    )

    tools = report.get("tools")
    if not isinstance(tools, dict):
        raise ValueError(f"{label} tools must be an object")

    completed = tools.get("completed")
    if (
        not isinstance(completed, list)
        or not completed
        or len(set(completed)) != len(completed)
        or not all(isinstance(run_id, str) for run_id in completed)
    ):
        raise ValueError(
            f"{label} tools.completed must be a non-empty array of unique run IDs"
        )

    selected_ids = set(selected)
    completed_ids = set(completed)
    if not completed_ids.issubset(selected_ids):
        raise ValueError(f"{label} tools.completed must be a subset of tools.selected")

    outputs = report.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError(f"{label} outputs must be an object")
    rows_per_tool = outputs.get("rows_per_tool")
    if not isinstance(rows_per_tool, dict) or set(rows_per_tool) != completed_ids:
        raise ValueError(
            f"{label} outputs.rows_per_tool keys must exactly match tools.completed"
        )
    for run_id, count in rows_per_tool.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(
                f"{label} outputs.rows_per_tool[{run_id!r}] must be a non-negative integer"
            )

    validated_counts = dict(rows_per_tool)
    if observed_rows_per_tool is not None:
        observed_counts = dict(observed_rows_per_tool)
        unexpected_observed = set(observed_counts) - completed_ids
        if unexpected_observed:
            raise ValueError(
                f"Inferred network tool IDs must be contained in {label} tools.completed"
            )
        observed_with_empty_runs = {
            run_id: observed_counts.get(run_id, 0) for run_id in completed
        }
        if observed_with_empty_runs != validated_counts:
            raise ValueError(
                f"Inferred network row counts must exactly match {label} "
                "outputs.rows_per_tool"
            )
    return validated_counts


__all__ = [
    "OUTPUT_CAPABILITY_KEYS",
    "OUTPUT_SIGN_SEMANTICS",
    "TOOL_ORIGINS",
    "validate_final_inference_report",
    "validate_frozen_output_capabilities",
    "validate_selected_object_map",
    "validate_selected_tool_identity_maps",
]
