#!/usr/bin/env python3
"""Offline audit of a bulk temporal proposal, reusing the canonical validator."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from andrea.core.shared.bulk_temporal import (
    validate_bulk_temporal_profile,
)  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    args = parser.parse_args()
    try:
        report = validate_bulk_temporal_profile(args.profile)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)]}, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
