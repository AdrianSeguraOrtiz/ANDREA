"""Content identity for datasets shared by inference and ground truth."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

DATASET_FINGERPRINT_ALGORITHM = "sha256"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_dataset_content(
    *,
    expression_path: Path,
    extras: Mapping[str, Path | None],
) -> dict[str, str]:
    """Hash standardized dataset inputs independently of their host paths."""
    components = {"expression": _sha256_file(expression_path)}
    for key, path in sorted(extras.items()):
        if path is not None:
            components[f"extra:{key}"] = _sha256_file(path)
    canonical = json.dumps(
        components,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return {
        "algorithm": DATASET_FINGERPRINT_ALGORITHM,
        "value": hashlib.sha256(canonical).hexdigest(),
    }


def validate_dataset_fingerprint(value: Any, *, label: str) -> dict[str, str]:
    """Validate the exact serialized fingerprint contract."""
    if not isinstance(value, dict) or set(value) != {"algorithm", "value"}:
        raise ValueError(f"{label} must contain exactly algorithm and value")
    if value.get("algorithm") != DATASET_FINGERPRINT_ALGORITHM:
        raise ValueError(f"{label}.algorithm must be 'sha256'")
    digest = value.get("value")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{label}.value must be 64 lowercase hexadecimal characters")
    return {"algorithm": DATASET_FINGERPRINT_ALGORITHM, "value": digest}


__all__ = [
    "DATASET_FINGERPRINT_ALGORITHM",
    "fingerprint_dataset_content",
    "validate_dataset_fingerprint",
]
