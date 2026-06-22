"""Internal helpers for normalized network context and sign values."""

from __future__ import annotations

from typing import Any

GLOBAL_CONTEXT = "global"
GROUP_CONTEXT_PREFIX = "group:"
COLUMN_CONTEXT_PREFIX = "column:"
SAMPLE_CONTEXT_PREFIX = "sample:"
TIMEPOINT_CONTEXT_PREFIX = "timepoint:"
PERTURBATION_CONTEXT_PREFIX = "perturbation:"
NETWORK_CONTEXT_FAMILIES = (
    "global",
    "group",
    "column",
    "sample",
    "timepoint",
    "perturbation",
    "other",
)
SPECIFIC_NETWORK_CONTEXT_FAMILIES = (
    "column",
    "sample",
    "timepoint",
    "perturbation",
)
NETWORK_CONTEXT_FAMILY_ORDER = {
    family: idx for idx, family in enumerate(NETWORK_CONTEXT_FAMILIES)
}
NETWORK_CONTEXT_PREFIXES = {
    "group": GROUP_CONTEXT_PREFIX,
    "column": COLUMN_CONTEXT_PREFIX,
    "sample": SAMPLE_CONTEXT_PREFIX,
    "timepoint": TIMEPOINT_CONTEXT_PREFIX,
    "perturbation": PERTURBATION_CONTEXT_PREFIX,
}
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
    for family, prefix in NETWORK_CONTEXT_PREFIXES.items():
        if text.startswith(prefix):
            return family
    return "other"


def network_context_label(context: str) -> str:
    text = str(context).strip()
    family = network_context_family(text)
    prefix = NETWORK_CONTEXT_PREFIXES.get(family)
    if prefix:
        value = text.removeprefix(prefix)
        return f"{family} {value}" if value else family
    return text


def network_context_sort_key(context: str) -> tuple[int, str]:
    text = str(context).strip()
    family = network_context_family(text)
    return (
        NETWORK_CONTEXT_FAMILY_ORDER.get(family, NETWORK_CONTEXT_FAMILY_ORDER["other"]),
        network_context_label(text),
    )


def normalize_network_context_family(value: Any) -> str:
    family = str(value or "").strip().removesuffix("s")
    if family in NETWORK_CONTEXT_FAMILIES:
        return family
    valid = ", ".join(NETWORK_CONTEXT_FAMILIES)
    raise ValueError(f"family must be one of {valid}")


def network_context_counts_by_family(contexts: Any) -> dict[str, int]:
    counts = {family: 0 for family in NETWORK_CONTEXT_FAMILIES}
    for context in contexts:
        family = network_context_family(str(context))
        counts[family] = counts.get(family, 0) + 1
    return counts


def normalize_network_sign(value: Any, *, source: str = "network row") -> str:
    sign = str(value).strip()
    if sign not in VALID_NETWORK_SIGNS:
        raise ValueError(
            f"{source} has invalid sign {sign!r}; expected one of '+', '-' or '?'"
        )
    return sign
