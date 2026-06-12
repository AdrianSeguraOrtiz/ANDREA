"""Shared helpers for network comparison."""

from __future__ import annotations

import csv
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional


def create_comparison_dir(
    *,
    output_root: Path,
    request_id: str,
    created_at: datetime,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    dirname = f"comparison_{slugify(request_id)}_{timestamp}"
    candidate = output_root / dirname
    suffix = 2
    while candidate.exists():
        candidate = output_root / f"{dirname}_{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip()).strip("._-")
    return slug or "unknown"


def optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def write_csv(
    path: Path,
    rows: Iterable[dict[str, Any]],
    *,
    fieldnames: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def format_float(value: float) -> str:
    return format(float(value), ".12g")
