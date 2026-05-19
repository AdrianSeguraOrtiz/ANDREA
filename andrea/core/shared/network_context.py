"""Internal helpers for normalized network context and sign values."""

from __future__ import annotations

from typing import Any

GLOBAL_CONTEXT = "global"
GROUP_CONTEXT_PREFIX = "group:"
CELL_CONTEXT_PREFIX = "cell:"
VALID_NETWORK_SIGNS = {"+", "-", "?"}


def normalize_network_context(value: Any, *, source: str = "network row") -> str:
    context = str(value).strip()
    if not context:
        raise ValueError(f"{source} has empty context")
    return context


def network_context_family(context: str) -> str:
    text = str(context).strip()
    if text == GLOBAL_CONTEXT:
        return "global"
    if text.startswith(GROUP_CONTEXT_PREFIX):
        return "group"
    if text.startswith(CELL_CONTEXT_PREFIX):
        return "cell"
    return "other"


def network_context_label(context: str) -> str:
    text = str(context).strip()
    family = network_context_family(text)
    if family == "group":
        value = text.removeprefix(GROUP_CONTEXT_PREFIX)
        return f"group {value}" if value else "group"
    if family == "cell":
        value = text.removeprefix(CELL_CONTEXT_PREFIX)
        return f"cell {value}" if value else "cell"
    return text


def network_context_sort_key(context: str) -> tuple[int, str]:
    text = str(context).strip()
    order = {
        "global": 0,
        "group": 1,
        "cell": 2,
        "other": 3,
    }
    family = network_context_family(text)
    return (order[family], network_context_label(text))


def normalize_network_sign(value: Any, *, source: str = "network row") -> str:
    sign = str(value).strip()
    if sign not in VALID_NETWORK_SIGNS:
        raise ValueError(
            f"{source} has invalid sign {sign!r}; expected one of '+', '-' or '?'"
        )
    return sign
