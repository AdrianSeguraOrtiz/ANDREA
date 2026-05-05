"""Small helpers for normalized issue objects."""

from __future__ import annotations

from typing import Any

ISSUE_SEVERITIES = {"block", "warn", "info"}


def make_issue(
    *,
    severity: str,
    message: str,
    code: str,
    **context: Any,
) -> dict[str, Any]:
    severity = str(severity).strip()
    if severity not in ISSUE_SEVERITIES:
        raise ValueError(f"invalid issue severity: {severity}")
    payload: dict[str, Any] = {
        "severity": severity,
        "code": str(code).strip() or "general",
        "message": str(message).strip(),
    }
    payload.update({key: value for key, value in context.items() if value is not None})
    return payload


def issue_messages(
    issues: list[dict[str, Any]],
    *,
    severity: str | None = None,
) -> list[str]:
    messages: list[str] = []
    for issue in issues:
        if severity is not None and issue.get("severity") != severity:
            continue
        message = str(issue.get("message", "")).strip()
        if message:
            messages.append(message)
    return messages

