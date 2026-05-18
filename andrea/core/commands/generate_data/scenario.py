"""Scenario-first request parsing and validation for generate-data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from andrea.core.shared.catalog_contracts import SIMULATION_EXTRA_IDS

from .catalog import _load_simulator_catalog, load_simulation_input_specs
from .request import _resolve_inputs, _validate_organism
from .shared import (
    PROFILE_SPECS,
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

    profile = str(payload.get("profile", "")).strip()
    if profile not in PROFILE_SPECS:
        raise ValueError(f"Unknown benchmark profile: {profile}")

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
            profile=profile,
            simulator_id="scenario",
        )
    if int(base_seed) < 1:
        raise ValueError("scenario-request.base_seed must be >= 1")

    effective_extras = sorted(
        set(requested_extras).union(PROFILE_SPECS[profile].required_extras)
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
        profile=profile,
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
