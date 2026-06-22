"""Bootstrap payload construction for the generate-data GUI."""

from __future__ import annotations

import os
from typing import Any

from andrea.core.shared.catalog_contracts import SIMULATION_EXTRA_IDS
from andrea.core.shared.input_specs import load_input_specs as load_inference_input_specs

from .catalog import _load_simulator_catalog, load_simulation_input_specs
from .cost_planner import detect_host_ram_gb
from .semantic import (
    COLUMN_KIND_VALUES,
    EXPERIMENTAL_DESIGN_VALUES,
    RESOLUTION_VALUES,
    SIMULATOR_TRUTH_CONTEXT_FAMILIES,
    context_prefix_for_family,
    expression_profile_for_axes,
    parse_data_axes,
    parse_truth_requirements,
    required_extras_for_request,
)


def load_generate_bootstrap() -> dict[str, Any]:
    """Build the catalog-backed bootstrap payload used by the local GUI."""
    _schemas, catalog = _load_simulator_catalog()
    inference_input_specs = load_inference_input_specs()
    simulation_input_specs = load_simulation_input_specs()
    extras_by_template: dict[str, set[str]] = {}
    templates_by_id: dict[str, dict[str, Any]] = {}
    capabilities_by_simulator: dict[str, dict[str, dict[str, Any]]] = {}
    simulation_inputs: dict[str, dict[str, Any]] = {}

    def _merge_simulation_input(
        item: dict[str, Any],
        *,
        simulator_id: str,
        simulator_name: str,
        relation: str,
    ) -> None:
        input_id = str(item.get("input") or "").strip()
        if not input_id:
            return
        input_spec = simulation_input_specs.get(input_id, {})
        input_format = str(input_spec.get("format") or "").strip()
        formats = [input_format] if input_format else []
        accepted_extensions = (
            [
                str(x).strip()
                for x in input_spec.get("accepted_extensions", [])
                if str(x).strip()
            ]
            if isinstance(input_spec.get("accepted_extensions", []), list)
            else []
        )
        existing = simulation_inputs.setdefault(
            input_id,
            {
                "id": input_id,
                "label": str(input_spec.get("name") or input_id),
                "description": str(input_spec.get("description") or ""),
                "example": str(input_spec.get("example") or ""),
                "formats": [],
                "accepted_extensions": [],
                "accept": "",
                "supported_by": [],
                "used_by": {
                    "required": [],
                    "optional": [],
                    "conditional": [],
                },
            },
        )
        existing.setdefault("supported_by", [])
        if simulator_id not in existing["supported_by"]:
            existing["supported_by"].append(simulator_id)
            existing["supported_by"].sort()
        existing["formats"] = sorted(set(existing.get("formats", [])).union(formats))
        existing["accepted_extensions"] = sorted(
            set(existing.get("accepted_extensions", [])).union(accepted_extensions)
        )
        existing["accept"] = _accept_from_extensions(existing["accepted_extensions"])
        if not existing["accept"]:
            existing["accept"] = _accept_from_formats(existing["formats"])
        entry: dict[str, Any] = {
            "simulator_id": simulator_id,
            "name": simulator_name,
            "usage": str(item.get("usage") or "").strip(),
        }
        if relation == "conditional":
            entry["conditions"] = item.get("conditions", [])
            entry["message"] = str(item.get("message") or "").strip()
        used_by = existing.setdefault(
            "used_by", {"required": [], "optional": [], "conditional": []}
        )
        relation_entries = used_by.setdefault(relation, [])
        if not any(
            str(existing_entry.get("simulator_id")) == simulator_id
            for existing_entry in relation_entries
        ):
            relation_entries.append(entry)
            relation_entries.sort(key=lambda value: str(value.get("simulator_id", "")))

    for simulator_id, spec in sorted(catalog.items()):
        simulator_name = str(spec.get("name") or simulator_id)
        capabilities_by_id: dict[str, dict[str, Any]] = {}
        for capability in spec.get("capabilities", []):
            if not isinstance(capability, dict):
                continue
            data_axes = capability.get("data_axes", {})
            truth_requirements = capability.get("truth_requirements", {})
            template_id = _semantic_template_id(
                data_axes=data_axes,
                truth_requirements=truth_requirements,
            )
            capabilities_by_id[template_id] = capability
            templates_by_id.setdefault(
                template_id,
                {
                    "id": template_id,
                    "data_axes": dict(data_axes),
                    "truth_requirements": dict(truth_requirements),
                    "column_kind": str(data_axes.get("column_kind", "")),
                    "expression_profile": expression_profile_for_axes(data_axes),
                    "required_truth_outputs": list(
                        truth_requirements.get("contexts", [])
                    ),
                    "required_truth_contexts": _truth_context_prefixes(
                        truth_requirements
                    ),
                    "required_extras": sorted(
                        required_extras_for_request(data_axes, truth_requirements)
                    ),
                    "available_extras": [],
                },
            )
            extras_by_template.setdefault(template_id, set()).update(
                required_extras_for_request(data_axes, truth_requirements)
            )
            extras_by_template[template_id].update(
                str(x)
                for x in capability.get("native_extras", [])
                if isinstance(x, str) and x in SIMULATION_EXTRA_IDS
            )
            extras_by_template[template_id].update(
                str(x)
                for x in capability.get("derivable_extras", [])
                if isinstance(x, str) and x in SIMULATION_EXTRA_IDS
            )
        capabilities_by_simulator[simulator_id] = capabilities_by_id
        raw_inputs = spec.get("extra_inputs", {})
        if isinstance(raw_inputs, dict):
            for group_key in ("required", "optional"):
                for item in raw_inputs.get(group_key, []):
                    if isinstance(item, dict):
                        _merge_simulation_input(
                            item,
                            simulator_id=simulator_id,
                            simulator_name=simulator_name,
                            relation=group_key,
                        )
            for item in raw_inputs.get("conditional_required", []):
                if isinstance(item, dict):
                    _merge_simulation_input(
                        item,
                        simulator_id=simulator_id,
                        simulator_name=simulator_name,
                        relation="conditional",
                    )

    scenario_templates = []
    for template_id, template in sorted(templates_by_id.items()):
        item = dict(template)
        item["available_extras"] = sorted(extras_by_template.get(template_id, set()))
        scenario_templates.append(item)

    extras = []
    all_extras = set().union(*extras_by_template.values()) if extras_by_template else set()
    for key in sorted(all_extras):
        spec = inference_input_specs.get(key, {})
        default_suffix = ".txt" if spec.get("file_kind") == "txt_list" else ".tsv"
        extras.append(
            {
                "key": key,
                "label": str(spec.get("label", f"{key}{default_suffix}")),
                "description": str(
                    spec.get("description", f"Optional generated extra '{key}'.")
                ),
                "file_kind": str(spec.get("file_kind", "tsv")),
                "example": str(spec.get("example", "")),
            }
        )

    simulators = []
    for simulator_id, spec in sorted(catalog.items()):
        simulators.append(
            {
                "simulator_id": simulator_id,
                "id": simulator_id,
                "schema_version": spec.get("schema_version"),
                "name": spec["name"],
                "publication": spec.get("publication", []),
                "first_author": spec.get("first_author"),
                "year": spec.get("year"),
                "simulation_summary": spec.get("simulation_summary"),
                "simulation_keywords": spec.get("simulation_keywords", []),
                "implementation_url": spec.get("implementation_url"),
                "docker_image": spec.get("docker_image"),
                "extra_inputs": spec.get("extra_inputs", {}),
                "runtime_resources": spec.get("runtime_resources", {}),
                "semantic_capabilities": capabilities_by_simulator.get(simulator_id, {}),
                "capabilities": spec.get("capabilities", []),
                "params_schema": spec.get("params", {}),
                "spec": spec,
            }
        )

    cpu_count = max(1, int(os.cpu_count() or 1))
    return {
        "semantic_options": {
            "axes": {
                "resolution": [
                    value for value in RESOLUTION_VALUES if value != "unknown"
                ],
                "column_kind": list(COLUMN_KIND_VALUES),
                "experimental_design": list(EXPERIMENTAL_DESIGN_VALUES),
            },
            "truth_context_families": list(SIMULATOR_TRUTH_CONTEXT_FAMILIES),
        },
        "scenario_templates": scenario_templates,
        "extras": extras,
        "planning_defaults": {
            "max_parallel_tasks": cpu_count,
            "max_cores": cpu_count,
            "max_ram_gb": round(detect_host_ram_gb(), 3),
        },
        "simulation_inputs": sorted(
            simulation_inputs.values(),
            key=lambda item: (
                -sum(
                    len(item.get("used_by", {}).get(relation, []))
                    for relation in ("required", "optional", "conditional")
                ),
                str(item.get("id", "")),
            ),
        ),
        "simulators": simulators,
    }


def _accept_from_formats(formats: list[str]) -> str:
    extensions_by_format = {
        "csv": [".csv"],
        "newick": [".nwk", ".newick", ".txt"],
        "rds": [".rds"],
        "tsv": [".tsv", ".txt"],
        "txt": [".txt"],
    }
    extensions: set[str] = set()
    for value in formats:
        normalized = str(value or "").strip().lower()
        extensions.update(extensions_by_format.get(normalized, []))
    return ",".join(sorted(extensions))


def _accept_from_extensions(extensions: list[str]) -> str:
    normalized = {
        value if value.startswith(".") else f".{value}"
        for value in (str(item or "").strip().lower() for item in extensions)
        if value
    }
    return ",".join(sorted(normalized))


def _semantic_template_id(
    *,
    data_axes: dict[str, Any],
    truth_requirements: dict[str, Any],
) -> str:
    axes = parse_data_axes(data_axes)
    truth = parse_truth_requirements(truth_requirements)
    return "-".join(
        [
            axes.resolution,
            axes.column_kind,
            axes.experimental_design,
            *truth.contexts,
        ]
    )


def _truth_context_prefixes(truth_requirements: dict[str, Any]) -> list[str]:
    truth = parse_truth_requirements(truth_requirements)
    return [context_prefix_for_family(context) for context in truth.contexts]


__all__ = ["load_generate_bootstrap"]
