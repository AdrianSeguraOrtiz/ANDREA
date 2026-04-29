"""Shared helpers for simulator smoketest parameter profiles."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from shared.catalog_simulators import DEFAULT_CATALOG_SIMULATORS_ROOT

SIMULATION_TOOLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARAM_OVERRIDES_DIR = SIMULATION_TOOLS_ROOT / "param_overrides"


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{label} file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} is malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return data


def _validate_override_keys(
    *,
    override: dict[str, Any],
    params_schema: dict[str, Any],
    label: str,
) -> None:
    unknown = sorted(set(override.keys()).difference(params_schema.keys()))
    if unknown:
        raise ValueError(f"{label} contains unknown parameter keys: {unknown}")

    for key, value in override.items():
        param_def = params_schema.get(key)
        if not isinstance(param_def, dict):
            continue
        if param_def.get("type") != "object" or not isinstance(value, dict):
            continue
        properties = param_def.get("properties", {})
        if not isinstance(properties, dict):
            continue
        _validate_override_keys(
            override=value,
            params_schema=properties,
            label=f"{label}.{key}",
        )


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = {key: copy.deepcopy(value) for key, value in base.items()}
        for key, value in override.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    return copy.deepcopy(override)


def resolve_smoketest_params(
    *,
    simulator_id: str,
    config_params: dict[str, Any],
    catalog_simulators_root: Path = DEFAULT_CATALOG_SIMULATORS_ROOT,
    param_overrides_dir: Path = DEFAULT_PARAM_OVERRIDES_DIR,
) -> tuple[dict[str, Any], dict[str, Any]]:
    spec_path = catalog_simulators_root / simulator_id / "simulatorspec.json"
    spec = _load_json_object(spec_path, f"simulatorspec[{simulator_id}]")
    raw_params = spec.get("params", {})
    params_schema = raw_params if isinstance(raw_params, dict) else {}

    override_path = param_overrides_dir / f"{simulator_id}.json"
    override_payload: dict[str, Any] = {}
    if override_path.exists():
        override_payload = _load_json_object(
            override_path, f"param_override[{simulator_id}]"
        )
        _validate_override_keys(
            override=override_payload,
            params_schema=params_schema,
            label=override_path.name,
        )

    _validate_override_keys(
        override=config_params,
        params_schema=params_schema,
        label=f"smoketest_config[{simulator_id}].request.params",
    )
    resolved = _deep_merge(override_payload, config_params)
    profile = {
        "source": (
            "smoketest_override_plus_config" if override_payload else "smoketest_config"
        ),
        "override_file": override_path.name if override_payload else None,
        "resolved_params": resolved,
    }
    return resolved, profile
