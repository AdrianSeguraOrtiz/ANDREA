"""GUI reproducibility snippet helpers."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any


def unavailable_reproducibility(message: str) -> dict[str, Any]:
    return {"available": False, "message": message}


def shell_join_pretty(args: list[str]) -> str:
    if len(args) <= 3:
        return " ".join(shlex.quote(str(item)) for item in args)
    head = " ".join(shlex.quote(str(item)) for item in args[:3])
    groups: list[str] = []
    idx = 3
    while idx < len(args):
        token = str(args[idx])
        if token.startswith("--") and idx + 1 < len(args):
            next_token = str(args[idx + 1])
            if not next_token.startswith("--"):
                groups.append(f"{shlex.quote(token)} {shlex.quote(next_token)}")
                idx += 2
                continue
        groups.append(shlex.quote(token))
        idx += 1
    return head + "".join(f" \\\n  {group}" for group in groups)


def python_path_expr(path: Path | str | None) -> str:
    return f"Path({str(path or '')!r})"


def python_literal(value: Any) -> str:
    return repr(value)


def append_cli_option(args: list[str], name: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        if value:
            args.append(name)
        return
    text = str(value).strip()
    if text:
        args.extend([name, text])


def build_single_step_reproducibility_payload(
    *,
    cli_summary: str,
    cli_args: list[str],
    python_summary: str,
    python_code: str,
) -> dict[str, Any]:
    return {
        "available": True,
        "cli": {
            "title": "CLI",
            "summary": cli_summary,
            "primary_label": "Unified command",
            "primary_language": "bash",
            "primary_code": shell_join_pretty(cli_args),
        },
        "python": {
            "title": "Python",
            "summary": python_summary,
            "primary_label": "Unified code",
            "primary_language": "python",
            "primary_code": python_code,
        },
    }
