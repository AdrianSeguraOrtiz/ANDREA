"""Semantic axes for generated simulation datasets.

This module is the single source of truth for the new generate-data data model.
The public contract is expressed as independent axes rather than one combined
profile string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from andrea.core.shared.network_context import (
    GLOBAL_CONTEXT,
    NETWORK_CONTEXT_FAMILIES,
    NETWORK_CONTEXT_PREFIXES,
)


MEASUREMENT_VALUES: tuple[str, ...] = (
    "rna_expression",
)

RESOLUTION_VALUES: tuple[str, ...] = (
    "bulk",
    "single_cell",
    "spatial",
    "pseudo_bulk",
    "mixed",
    "unknown",
)

COLUMN_KIND_VALUES: tuple[str, ...] = (
    "samples",
    "cells",
    "timepoints",
    "perturbations",
    "spots",
    "metacells",
    "conditions",
)

EXPERIMENTAL_DESIGN_VALUES: tuple[str, ...] = (
    "observational",
    "steady_state",
    "perturbational",
    "time_series",
    "trajectory",
    "differentiation",
)

# Simulator truth requirements are a deliberately smaller contract than the
# full normalized/evaluable network-context vocabulary. They are expressed as
# context families in simulator specs and scenario requests; wrappers expand
# them to concrete public contexts such as "group:<id>" or "column:<id>".
_SIMULATOR_TRUTH_CONTEXT_FAMILY_NAMES = (
    GLOBAL_CONTEXT,
    "group",
    "column",
)
SIMULATOR_TRUTH_CONTEXT_FAMILIES: tuple[str, ...] = tuple(
    family
    for family in NETWORK_CONTEXT_FAMILIES
    if family in _SIMULATOR_TRUTH_CONTEXT_FAMILY_NAMES
)
SIMULATOR_TRUTH_CONTEXT_PREFIXES: dict[str, str] = {
    family: (
        GLOBAL_CONTEXT
        if family == GLOBAL_CONTEXT
        else NETWORK_CONTEXT_PREFIXES[family]
    )
    for family in SIMULATOR_TRUTH_CONTEXT_FAMILIES
}
SIMULATOR_TRUTH_CONTEXT_PRIORITY: tuple[str, ...] = (
    "column",
    "group",
    GLOBAL_CONTEXT,
)

TRUTH_OUTPUT_STATUSES: tuple[str, ...] = (
    "none",
    "native",
    "derivable",
)


@dataclass(frozen=True)
class DataAxes:
    measurement: str
    resolution: str
    column_kind: str
    experimental_design: str

    def to_json(self) -> dict[str, str]:
        return {
            "measurement": self.measurement,
            "resolution": self.resolution,
            "column_kind": self.column_kind,
            "experimental_design": self.experimental_design,
        }


@dataclass(frozen=True)
class TruthRequirements:
    contexts: tuple[str, ...]

    def to_json(self) -> dict[str, list[str]]:
        return {"contexts": list(self.contexts)}


@dataclass(frozen=True)
class SemanticScenario:
    data_axes: DataAxes
    truth: TruthRequirements

    def to_json(self) -> dict[str, Any]:
        return {
            "data_axes": self.data_axes.to_json(),
            "truth_requirements": self.truth.to_json(),
        }


def parse_data_axes(raw: dict[str, Any], *, label: str = "data_axes") -> DataAxes:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    axes = DataAxes(
        measurement=_required_axis(raw, "measurement", MEASUREMENT_VALUES, label=label),
        resolution=_required_axis(raw, "resolution", RESOLUTION_VALUES, label=label),
        column_kind=_required_axis(raw, "column_kind", COLUMN_KIND_VALUES, label=label),
        experimental_design=_required_axis(
            raw,
            "experimental_design",
            EXPERIMENTAL_DESIGN_VALUES,
            label=label,
        ),
    )
    _validate_axis_relationships(axes, label=label)
    return axes


def parse_truth_requirements(
    raw: dict[str, Any],
    *,
    label: str = "truth_requirements",
) -> TruthRequirements:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    raw_contexts = raw.get("contexts")
    if not isinstance(raw_contexts, list) or not raw_contexts:
        raise ValueError(f"{label}.contexts must be a non-empty array")
    seen: set[str] = set()
    for index, item in enumerate(raw_contexts):
        context = str(item or "").strip()
        if context not in SIMULATOR_TRUTH_CONTEXT_FAMILIES:
            raise ValueError(
                f"{label}.contexts[{index}] must be one of "
                f"{list(SIMULATOR_TRUTH_CONTEXT_FAMILIES)}"
            )
        if context in seen:
            raise ValueError(f"{label}.contexts contains duplicate value: {context}")
        seen.add(context)
    if GLOBAL_CONTEXT not in seen:
        raise ValueError(f"{label}.contexts must include {GLOBAL_CONTEXT}")
    contexts = tuple(
        context for context in SIMULATOR_TRUTH_CONTEXT_FAMILIES if context in seen
    )
    return TruthRequirements(contexts=contexts)


def parse_semantic_scenario(
    raw: dict[str, Any],
    *,
    label: str = "scenario",
) -> SemanticScenario:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    return SemanticScenario(
        data_axes=parse_data_axes(raw.get("data_axes"), label=f"{label}.data_axes"),
        truth=parse_truth_requirements(
            raw.get("truth_requirements"),
            label=f"{label}.truth_requirements",
        ),
    )


def _required_extras_for_truth(requirements: TruthRequirements) -> frozenset[str]:
    extras: set[str] = set()
    if "group" in requirements.contexts:
        extras.add("groups")
    return frozenset(extras)


def required_extras_for_request(
    data_axes: DataAxes | dict[str, Any],
    requirements: TruthRequirements | dict[str, Any],
) -> frozenset[str]:
    axes = data_axes if isinstance(data_axes, DataAxes) else parse_data_axes(data_axes)
    truth = (
        requirements
        if isinstance(requirements, TruthRequirements)
        else parse_truth_requirements(requirements)
    )
    # Every generated benchmark must expose the regulator universe used by
    # inference and evaluation. All catalogued simulator capabilities provide
    # tf_list natively or derivably.
    extras = {"tf_list", *_required_extras_for_truth(truth)}
    if axes.experimental_design == "time_series":
        extras.add("timepoints")
    if axes.experimental_design == "perturbational":
        extras.update({"perturbation_design", "interventions"})
    if axes.resolution == "spatial" or axes.column_kind == "spots":
        extras.add("spatial_coordinates")
    return frozenset(extras)


def truth_output_statuses(capability: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    raw_outputs = capability.get("truth_outputs", [])
    if not isinstance(raw_outputs, list):
        return statuses
    for item in raw_outputs:
        if not isinstance(item, dict):
            continue
        context = str(item.get("context", "")).strip()
        status = str(item.get("status", "")).strip()
        if (
            context in SIMULATOR_TRUTH_CONTEXT_FAMILIES
            and status in TRUTH_OUTPUT_STATUSES
        ):
            statuses[context] = status
    return statuses


def supported_artifacts(capability: dict[str, Any]) -> tuple[set[str], set[str]]:
    native = set(_string_list(capability.get("native_extras", [])))
    derivable = set(_string_list(capability.get("derivable_extras", [])))
    return native, derivable


def context_prefix_for_family(family: str) -> str:
    normalized = str(family or "").strip()
    if normalized not in SIMULATOR_TRUTH_CONTEXT_PREFIXES:
        raise ValueError(
            f"Unknown truth context family {family!r}; expected one of "
            f"{list(SIMULATOR_TRUTH_CONTEXT_FAMILIES)}"
        )
    return SIMULATOR_TRUTH_CONTEXT_PREFIXES[normalized]


def required_truth_context_prefixes(
    requirements: TruthRequirements,
) -> tuple[str, ...]:
    return tuple(context_prefix_for_family(context) for context in requirements.contexts)


def primary_truth_context(requirements: TruthRequirements) -> str:
    return primary_truth_context_family(requirements)


def semantic_key(*, data_axes: DataAxes, truth: TruthRequirements) -> str:
    return "|".join(
        [
            data_axes.measurement,
            data_axes.resolution,
            data_axes.column_kind,
            data_axes.experimental_design,
            ",".join(truth.contexts),
        ]
    )


def semantic_key_from_json(
    *,
    data_axes: dict[str, Any],
    truth_requirements: dict[str, Any],
) -> str:
    return semantic_key(
        data_axes=parse_data_axes(data_axes),
        truth=parse_truth_requirements(truth_requirements),
    )


def expression_profile_for_axes(data_axes: DataAxes | dict[str, Any]) -> str:
    axes = data_axes if isinstance(data_axes, DataAxes) else parse_data_axes(data_axes)
    if axes.resolution in {"single_cell", "spatial", "pseudo_bulk"}:
        return "scrna"
    if axes.resolution == "bulk":
        return "bulk"
    if axes.resolution == "mixed":
        return "mixed"
    return "unknown"


def primary_truth_context_family(requirements: TruthRequirements) -> str:
    for family in SIMULATOR_TRUTH_CONTEXT_PRIORITY:
        if family in requirements.contexts:
            return family
    raise ValueError("truth_requirements.contexts must include at least one context")


def truth_context_label(requirements: TruthRequirements) -> str:
    return "+".join(requirements.contexts)


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]


def _required_axis(
    raw: dict[str, Any],
    key: str,
    allowed: tuple[str, ...],
    *,
    label: str,
) -> str:
    value = str(raw.get(key) or "").strip()
    if value not in allowed:
        raise ValueError(f"{label}.{key} must be one of {list(allowed)}")
    return value


def _validate_axis_relationships(axes: DataAxes, *, label: str) -> None:
    if axes.column_kind == "cells" and axes.resolution != "single_cell":
        raise ValueError(f"{label}.column_kind=cells requires resolution=single_cell")
    if axes.column_kind == "spots" and axes.resolution != "spatial":
        raise ValueError(f"{label}.column_kind=spots requires resolution=spatial")
    if axes.column_kind == "metacells" and axes.resolution not in {
        "single_cell",
        "pseudo_bulk",
    }:
        raise ValueError(
            f"{label}.column_kind=metacells requires resolution=single_cell "
            "or pseudo_bulk"
        )
    if axes.experimental_design == "time_series" and axes.column_kind not in {
        "timepoints",
        "cells",
        "samples",
    }:
        raise ValueError(
            f"{label}.experimental_design=time_series requires columns that "
            "represent timepoints, sampled cells or samples"
        )
    if axes.experimental_design == "perturbational" and axes.column_kind not in {
        "perturbations",
        "cells",
        "samples",
    }:
        raise ValueError(
            f"{label}.experimental_design=perturbational requires columns that "
            "represent perturbations, perturbed cells or perturbed samples"
        )
