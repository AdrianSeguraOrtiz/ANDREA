"""Path formatting helpers for portable workflow reports."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

_WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_PORTABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_portable_identifier(value: object, *, label: str) -> str:
    """Return a filesystem-safe workflow identifier without rewriting it."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _PORTABLE_IDENTIFIER.fullmatch(value) is None
    ):
        raise ValueError(f"{label} must match [A-Za-z0-9][A-Za-z0-9._-]* exactly")
    return value


def validate_safe_relative_posix_path(value: object, *, label: str) -> str:
    """Return a canonical, portable manifest-relative path or raise.

    Workflow manifests are portable package indexes, so their file references
    must not escape the directory containing the manifest or depend on the
    producer's absolute filesystem layout.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            f"{label} must be a non-empty relative POSIX path without "
            "surrounding whitespace"
        )
    if "\\" in value or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} must be a portable POSIX path")
    if value.startswith("/") or _WINDOWS_DRIVE_PREFIX.match(value):
        raise ValueError(f"{label} must be relative to its manifest")

    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(f"{label} must not contain empty, '.' or '..' path segments")
    normalized = PurePosixPath(value).as_posix()
    if normalized != value:
        raise ValueError(f"{label} must be a canonical relative POSIX path")
    return normalized


def resolve_safe_manifest_path(*, base_dir: Path, value: object, label: str) -> Path:
    """Resolve a manifest-relative path while enforcing package containment."""
    relative = validate_safe_relative_posix_path(value, label=label)
    root = base_dir.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside the manifest directory") from exc
    return resolved


def report_path(path: Path | str | None, *, base_dir: Path) -> str | None:
    """Return a POSIX relative path from base_dir when possible.

    Workflow reports are meant to travel with their output directories, so paths
    stored in JSON should not be absolute unless the platform cannot compute a
    relative path.
    """
    if path is None:
        return None
    raw = Path(path)
    try:
        rel = os.path.relpath(raw.resolve(), base_dir.resolve())
    except ValueError:
        return raw.as_posix()
    return Path(rel).as_posix()
