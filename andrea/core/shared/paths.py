"""Path formatting helpers for portable workflow reports."""

from __future__ import annotations

import os
from pathlib import Path


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
