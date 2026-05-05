"""Shared input-spec loading helpers reused by inference and generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from andrea.core.shared.json_io import load_json_object

DEFAULT_INPUT_SPECS_DIR = (
    Path(__file__).resolve().parents[2] / "catalog_inference_tools" / "input_specs"
)


def load_input_specs(
    input_specs_dir: Path = DEFAULT_INPUT_SPECS_DIR,
) -> dict[str, dict[str, Any]]:
    input_specs_dir = input_specs_dir.resolve()
    if not input_specs_dir.exists() or not input_specs_dir.is_dir():
        raise ValueError(f"Input specs directory not found: {input_specs_dir}")

    specs: dict[str, dict[str, Any]] = {}
    for spec_path in sorted(input_specs_dir.glob("*.json")):
        spec = load_json_object(spec_path, f"input-spec[{spec_path.name}]")
        key = str(spec.get("key", "")).strip()
        if not key:
            raise ValueError(f"input spec is missing key: {spec_path}")
        if key in specs:
            raise ValueError(f"Duplicate input spec key '{key}': {spec_path}")
        specs[key] = spec

    if "expression_matrix" not in specs:
        raise ValueError(
            f"Missing required input spec 'expression_matrix' in {input_specs_dir}"
        )
    return specs


__all__ = ["DEFAULT_INPUT_SPECS_DIR", "load_input_specs"]
