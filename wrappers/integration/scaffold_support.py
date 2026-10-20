"""Promote reviewed scaffold drafts without recreating placeholder files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|PLACEHOLDER|PENDING)(?:\b|_)", re.IGNORECASE)


def reviewed_draft(
    path: Path,
    *,
    schema_path: Path,
    expected_id: str | None = None,
    id_field: str = "id",
) -> str:
    """Check promotion eligibility, not functional/scientific acceptance."""
    if not path.is_file():
        raise RuntimeError(
            f"Cannot activate: reviewed draft missing: {path}. "
            "Create the scaffold, complete and review the draft first."
        )
    try:
        content = path.read_text(encoding="utf-8")
        payload = json.loads(content)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read draft/schema for {path}: {exc}") from exc
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            payload
        )
    )
    if errors:
        detail = "; ".join(
            f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors[:5]
        )
        raise RuntimeError(f"Cannot activate invalid draft {path}: {detail}")
    if expected_id is not None and payload.get(id_field) != expected_id:
        raise RuntimeError(
            f"Cannot activate {path}: {id_field} must equal {expected_id!r}."
        )

    def placeholders(value, location="<root>"):
        if isinstance(value, str) and PLACEHOLDER.search(value):
            yield location
        elif isinstance(value, dict):
            for key, child in value.items():
                yield from placeholders(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from placeholders(child, f"{location}[{index}]")

    unresolved = list(placeholders(payload))
    if unresolved:
        raise RuntimeError(
            f"Cannot activate {path}: unresolved placeholders at {', '.join(unresolved[:5])}."
        )
    return content
