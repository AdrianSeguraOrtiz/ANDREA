"""Scenario-first request parsing and validation for generate-data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from andrea.core.shared.catalog_contracts import SIMULATION_EXTRA_IDS

from .catalog import _load_simulator_catalog, load_simulation_input_specs
from .request import _resolve_inputs, _validate_organism
from .semantic import (
    parse_data_axes,
    parse_truth_requirements,
    required_extras_for_request,
    semantic_key,
)
from .shared import (
    ResolvedScenarioRequest,
    _load_json_object,
    _stable_seed_base,
    _validate_json_instance,
)


def validate_scenario_request_payload(
    payload: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> ResolvedScenarioRequest:
    schemas, _catalog = _load_simulator_catalog()
    _validate_json_instance(
        instance=payload,
        schema=schemas["scenario_request"],
        label="scenario-request",
    )

    data_axes = parse_data_axes(payload.get("data_axes"), label="scenario-request.data_axes")
    truth_requirements = parse_truth_requirements(
        payload.get("truth_requirements"),
        label="scenario-request.truth_requirements",
    )
    scenario_key = semantic_key(data_axes=data_axes, truth=truth_requirements)

    requested_extras = list(payload.get("requested_extras", []))
    if any(extra not in SIMULATION_EXTRA_IDS for extra in requested_extras):
        unsupported = sorted(set(requested_extras).difference(SIMULATION_EXTRA_IDS))
        raise ValueError(f"Unknown requested_extras: {unsupported}")

    organism = payload.get("organism")
    if not isinstance(organism, dict):
        raise ValueError("scenario-request.organism must be an object")
    _validate_organism(organism)

    base_seed = payload.get("base_seed")
    if base_seed is not None and not isinstance(base_seed, int):
        raise ValueError("scenario-request.base_seed must be integer when provided")
    if base_seed is None:
        base_seed = _stable_seed_base(
            request_id=str(payload["id"]),
            semantic_key=scenario_key,
            simulator_id="scenario",
        )
    if int(base_seed) < 1:
        raise ValueError("scenario-request.base_seed must be >= 1")

    effective_extras = sorted(
        set(requested_extras).union(
            required_extras_for_request(data_axes, truth_requirements)
        )
    )
    raw_inputs = payload.get("inputs", {})
    input_specs = load_simulation_input_specs()
    inputs, resolved_input_paths = _resolve_inputs(
        raw_inputs,
        base_dir=base_dir or Path.cwd(),
        known_input_ids=set(input_specs),
    )

    return ResolvedScenarioRequest(
        request_id=str(payload["id"]),
        data_axes=data_axes.to_json(),
        truth_requirements=truth_requirements.to_json(),
        organism=organism,
        requested_extras=requested_extras,
        effective_extras=effective_extras,
        inputs=inputs,
        resolved_input_paths=resolved_input_paths,
        base_seed=int(base_seed),
        notes=payload.get("notes"),
        request_payload=payload,
    )


def validate_scenario_request(scenario_path: Path) -> ResolvedScenarioRequest:
    payload = _load_json_object(scenario_path, "scenario-request")
    return validate_scenario_request_payload(
        payload,
        base_dir=scenario_path.resolve().parent,
    )
