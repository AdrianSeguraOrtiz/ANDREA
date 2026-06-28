"""ToolSpec-aware benchmark profile resolution for cost benchmarks."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .benchmark_inputs import GENERATED_EXTRA_INPUTS
from .param_profiles import DEFAULT_PARAM_OVERRIDES_DIR, resolve_dev_params

INFERENCE_TOOLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COST_PROFILES_DIR = INFERENCE_TOOLS_ROOT / "cost_profiles"
DEFAULT_GROUP_COUNT = 2
DEFAULT_PRIOR_DENSITY = 0.05
DEFAULT_OPTIONAL_BENCHMARK_INPUTS = {"tf_list"}
EXECUTION_POLICY_BY_MODE = {
    "global": "single",
    "group_native": "native_grouped",
    "group_emulated": "andrea_group_emulated",
    "column_native": "column_native",
    "group_aggregated": "andrea_group_aggregated",
}
CONDITIONAL_OPS = {"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte"}
PRIOR_LIKE_INPUTS = {"grnboost_network", "prior_grn", "prior_grn_by_group"}


@dataclass(frozen=True)
class BenchmarkProfile:
    tool_id: str
    profile_id: str
    sizes: tuple[str, ...] | None
    execution: dict[str, Any]
    execution_profile: dict[str, Any]
    params: dict[str, Any]
    params_profile: dict[str, Any]
    input_profile: dict[str, Any]
    required_inputs: tuple[str, ...]
    optional_inputs: tuple[str, ...]
    conditional_inputs: tuple[str, ...]


def resolve_benchmark_profiles(
    *,
    tool_id: str,
    catalog_tools_root: Path,
    param_overrides_dir: Path = DEFAULT_PARAM_OVERRIDES_DIR,
    cost_profiles_dir: Path = DEFAULT_COST_PROFILES_DIR,
    default_group_count: int = DEFAULT_GROUP_COUNT,
    default_prior_density: float = DEFAULT_PRIOR_DENSITY,
    default_optional_inputs: set[str] | None = None,
) -> list[BenchmarkProfile]:
    """Resolve concrete benchmark profiles for one catalog tool."""
    toolspec = _load_json_object(
        catalog_tools_root / tool_id / "toolspec.json",
        f"toolspec[{tool_id}]",
    )
    capabilities = _execution_capabilities(toolspec)
    params_schema = _params_schema(toolspec)
    base_params, base_params_profile = resolve_dev_params(
        tool_id=tool_id,
        catalog_tools_root=catalog_tools_root,
        param_overrides_dir=param_overrides_dir,
    )
    profile_config = load_profile_config(
        cost_profiles_dir=cost_profiles_dir,
        tool_id=tool_id,
    )
    optional_defaults = (
        DEFAULT_OPTIONAL_BENCHMARK_INPUTS
        if default_optional_inputs is None
        else default_optional_inputs
    )

    raw_profiles = _configured_profiles(
        profile_config=profile_config,
        capabilities=capabilities,
        default_group_count=default_group_count,
    )
    return [
        _resolve_one_profile(
            tool_id=tool_id,
            toolspec=toolspec,
            capabilities=capabilities,
            params_schema=params_schema,
            base_params=base_params,
            base_params_profile=base_params_profile,
            raw_profile=raw_profile,
            raw_profile_index=idx,
            default_group_count=default_group_count,
            default_prior_density=default_prior_density,
            default_optional_inputs=optional_defaults,
            profile_config_path=(
                cost_profiles_dir / f"{tool_id}.json"
                if profile_config is not None
                else None
            ),
        )
        for idx, raw_profile in enumerate(raw_profiles, start=1)
    ]


def load_profile_config(
    *,
    cost_profiles_dir: Path,
    tool_id: str,
) -> dict[str, Any] | None:
    config_path = cost_profiles_dir / f"{tool_id}.json"
    if not config_path.exists():
        return None
    return _load_json_object(config_path, f"cost_profile_config[{tool_id}]")


def _configured_profiles(
    *,
    profile_config: dict[str, Any] | None,
    capabilities: Sequence[str],
    default_group_count: int,
) -> list[dict[str, Any]]:
    if profile_config is not None:
        inherited_cost_relevant = profile_config.get("cost_relevant_params")
        if inherited_cost_relevant is not None and not isinstance(
            inherited_cost_relevant, list
        ):
            raise ValueError(
                "cost profile config cost_relevant_params must be an array."
            )
        raw_profiles = profile_config.get("profiles", [])
        if not isinstance(raw_profiles, list) or not raw_profiles:
            raise ValueError(
                "cost profile config must include a non-empty profiles array."
            )
        inherited_profile_fields = {
            "sizes": profile_config.get("sizes"),
            "column_kind": profile_config.get("column_kind"),
            "expression_profile": profile_config.get("expression_profile"),
            "gene_id_source": profile_config.get("gene_id_source"),
            "tf_count_policy": profile_config.get("tf_count_policy"),
            "prior_density": profile_config.get("prior_density"),
            "marker_count_per_group": profile_config.get("marker_count_per_group"),
        }
        inherited_sizes = inherited_profile_fields["sizes"]
        if inherited_sizes is not None and not isinstance(inherited_sizes, list):
            raise ValueError("cost profile config sizes must be an array.")
        out: list[dict[str, Any]] = []
        for idx, profile in enumerate(raw_profiles, start=1):
            if not isinstance(profile, dict):
                raise ValueError(
                    f"cost profile config profiles[{idx}] must be an object."
                )
            profile_copy = copy.deepcopy(profile)
            if (
                "cost_relevant_params" not in profile_copy
                and inherited_cost_relevant is not None
            ):
                profile_copy["cost_relevant_params"] = copy.deepcopy(
                    inherited_cost_relevant
                )
            for key, value in inherited_profile_fields.items():
                if key not in profile_copy and value is not None:
                    profile_copy[key] = copy.deepcopy(value)
            out.append(profile_copy)
        return out

    mode = _default_execution_mode(capabilities)
    return [
        {
            "id": f"{mode}_default",
            "execution": {"mode": mode},
            "group_count": 0 if mode == "global" else default_group_count,
        }
    ]


def _resolve_one_profile(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    capabilities: Sequence[str],
    params_schema: dict[str, Any],
    base_params: dict[str, Any],
    base_params_profile: dict[str, Any],
    raw_profile: dict[str, Any],
    raw_profile_index: int,
    default_group_count: int,
    default_prior_density: float,
    default_optional_inputs: set[str],
    profile_config_path: Path | None,
) -> BenchmarkProfile:
    profile_id = _profile_id(raw_profile, raw_profile_index)
    execution = _execution_payload(raw_profile, capabilities)
    mode = str(execution["mode"])
    group_count = _profile_group_count(
        raw_profile=raw_profile,
        mode=mode,
        default_group_count=default_group_count,
    )
    execution_profile = {
        "mode": mode,
        "physical_task_policy": EXECUTION_POLICY_BY_MODE[mode],
        "group_count": group_count,
        "aggregation_step": "column_to_group" if mode == "group_aggregated" else "none",
    }
    params, params_profile = _resolve_profile_params(
        params_schema=params_schema,
        base_params=base_params,
        base_params_profile=base_params_profile,
        raw_profile=raw_profile,
        profile_id=profile_id,
        profile_config_path=profile_config_path,
    )
    required_inputs = _extra_input_entries(toolspec, "required")
    selected_optional = _selected_optional_inputs(
        toolspec=toolspec,
        raw_profile=raw_profile,
        default_optional_inputs=default_optional_inputs,
    )
    conditional_inputs = _active_conditional_inputs(
        toolspec=toolspec,
        resolved_params=params,
        execution=execution,
    )
    _validate_generated_inputs(
        profile_id=profile_id,
        required_inputs=required_inputs,
        optional_inputs=selected_optional,
        conditional_inputs=conditional_inputs,
    )
    input_profile = _build_input_profile(
        raw_profile=raw_profile,
        mode=mode,
        group_count=group_count,
        required_inputs=required_inputs,
        optional_inputs=selected_optional,
        conditional_inputs=conditional_inputs,
        default_prior_density=default_prior_density,
    )
    return BenchmarkProfile(
        tool_id=tool_id,
        profile_id=profile_id,
        sizes=_profile_sizes(raw_profile=raw_profile, profile_id=profile_id),
        execution=execution,
        execution_profile=execution_profile,
        params=params,
        params_profile=params_profile,
        input_profile=input_profile,
        required_inputs=tuple(required_inputs),
        optional_inputs=tuple(selected_optional),
        conditional_inputs=tuple(conditional_inputs),
    )


def _profile_sizes(
    *,
    raw_profile: dict[str, Any],
    profile_id: str,
) -> tuple[str, ...] | None:
    raw_sizes = raw_profile.get("sizes")
    if raw_sizes is None:
        return None
    if not isinstance(raw_sizes, list) or not raw_sizes:
        raise ValueError(f"profile {profile_id}: sizes must be a non-empty array.")
    sizes: list[str] = []
    for raw_size in raw_sizes:
        if not isinstance(raw_size, str) or not raw_size.strip():
            raise ValueError(
                f"profile {profile_id}: sizes entries must be non-empty strings."
            )
        token = raw_size.strip()
        if "x" not in token.lower():
            raise ValueError(
                f"profile {profile_id}: invalid size {token!r}; expected GENESxCOLUMNS."
            )
        sizes.append(token)
    return tuple(dict.fromkeys(sizes))


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{label} file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} is malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _execution_capabilities(toolspec: dict[str, Any]) -> list[str]:
    raw = toolspec.get("execution_capabilities", [])
    modes = [
        str(item).strip() for item in raw if isinstance(item, str) and item.strip()
    ]
    if not modes:
        raise ValueError("toolspec.execution_capabilities must not be empty.")
    unknown = sorted(set(modes).difference(EXECUTION_POLICY_BY_MODE))
    if unknown:
        raise ValueError(f"Unsupported execution_capabilities: {unknown}")
    return list(dict.fromkeys(modes))


def _params_schema(toolspec: dict[str, Any]) -> dict[str, Any]:
    raw_params = toolspec.get("params", {})
    return raw_params if isinstance(raw_params, dict) else {}


def _default_execution_mode(capabilities: Sequence[str]) -> str:
    if "global" in capabilities:
        return "global"
    if "group_native" in capabilities:
        return "group_native"
    if "group_emulated" in capabilities:
        return "group_emulated"
    if "column_native" in capabilities:
        return "column_native"
    if "group_aggregated" in capabilities:
        return "group_aggregated"
    raise ValueError(f"No benchmarkable execution mode in: {list(capabilities)}")


def _profile_id(raw_profile: dict[str, Any], raw_profile_index: int) -> str:
    raw_id = raw_profile.get("id", raw_profile.get("profile_id"))
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()
    return f"profile_{raw_profile_index}"


def _execution_payload(
    raw_profile: dict[str, Any],
    capabilities: Sequence[str],
) -> dict[str, Any]:
    raw_execution = raw_profile.get("execution", {})
    if raw_execution is None:
        raw_execution = {}
    if not isinstance(raw_execution, dict):
        raise ValueError("profile.execution must be an object when provided.")
    execution = copy.deepcopy(raw_execution)
    mode = str(execution.get("mode") or _default_execution_mode(capabilities)).strip()
    if mode not in capabilities:
        raise ValueError(
            f"profile.execution.mode={mode!r} is not in execution_capabilities: {list(capabilities)}"
        )
    execution["mode"] = mode
    return execution


def _profile_group_count(
    *,
    raw_profile: dict[str, Any],
    mode: str,
    default_group_count: int,
) -> int:
    raw_group_count = raw_profile.get("group_count")
    if raw_group_count is None:
        group_count = 0 if mode in {"global", "column_native"} else default_group_count
    elif isinstance(raw_group_count, bool) or not isinstance(raw_group_count, int):
        raise ValueError("profile.group_count must be an integer.")
    else:
        group_count = raw_group_count

    if mode in {"global", "column_native"} and group_count != 0:
        raise ValueError(
            f"profile.group_count must be 0 for execution.mode={mode}."
        )
    if mode in {"group_native", "group_emulated", "group_aggregated"} and group_count < 1:
        raise ValueError(f"profile.group_count must be >= 1 for execution.mode={mode}.")
    return group_count


def _resolve_profile_params(
    *,
    params_schema: dict[str, Any],
    base_params: dict[str, Any],
    base_params_profile: dict[str, Any],
    raw_profile: dict[str, Any],
    profile_id: str,
    profile_config_path: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = copy.deepcopy(base_params)
    inline_override = raw_profile.get("param_overrides", {})
    if inline_override is None:
        inline_override = {}
    if not isinstance(inline_override, dict):
        raise ValueError(f"profile {profile_id}: param_overrides must be an object.")

    file_override = _load_profile_param_override(
        raw_profile=raw_profile,
        profile_id=profile_id,
        profile_config_path=profile_config_path,
    )
    for label, override in (
        ("param_override_file", file_override),
        ("param_overrides", inline_override),
    ):
        if not override:
            continue
        _validate_override_keys(
            override=override,
            params_schema=params_schema,
            label=f"profile {profile_id} {label}",
        )
        params = _deep_merge(params, override)

    profile = copy.deepcopy(base_params_profile)
    override_refs: list[str] = []
    base_override = profile.get("override_file")
    if isinstance(base_override, str) and base_override.strip():
        override_refs.append(base_override.strip())
    if file_override:
        override_refs.append(str(raw_profile.get("param_override_file")).strip())
    if inline_override:
        override_refs.append(
            _profile_ref(profile_id=profile_id, profile_config_path=profile_config_path)
        )
    if override_refs:
        profile["source"] = "toolspec_defaults_plus_override"
        profile["override_file"] = ";".join(override_refs)
    profile["resolved_params"] = params
    cost_relevant_params = _resolve_cost_relevant_params(
        raw_profile=raw_profile,
        params_schema=params_schema,
        profile_id=profile_id,
    )
    profile["cost_relevant_params"] = cost_relevant_params
    profile["cost_relevant_values"] = _cost_relevant_values(
        params=params,
        cost_relevant_params=cost_relevant_params,
    )
    return params, profile


def _resolve_cost_relevant_params(
    *,
    raw_profile: dict[str, Any],
    params_schema: dict[str, Any],
    profile_id: str,
) -> list[str]:
    raw_paths = raw_profile.get("cost_relevant_params", [])
    if raw_paths is None:
        raw_paths = []
    if not isinstance(raw_paths, list):
        raise ValueError(
            f"profile {profile_id}: cost_relevant_params must be an array."
        )
    paths = [
        str(item).strip()
        for item in raw_paths
        if isinstance(item, str) and str(item).strip()
    ]
    for path in paths:
        _validate_param_path(
            path=path,
            params_schema=params_schema,
            label=f"profile {profile_id}.cost_relevant_params",
        )
    return list(dict.fromkeys(paths))


def _validate_param_path(
    *,
    path: str,
    params_schema: dict[str, Any],
    label: str,
) -> None:
    current = params_schema
    consumed: list[str] = []
    parts = path.split(".")
    for idx, part in enumerate(parts):
        if not part:
            raise ValueError(f"{label} contains invalid empty path segment: {path}")
        if part not in current:
            prefix = ".".join(consumed) if consumed else "(root)"
            raise ValueError(
                f"{label} references unknown parameter path '{path}' at {prefix}/{part}."
            )
        param_def = current[part]
        consumed.append(part)
        if idx == len(parts) - 1:
            return
        if not isinstance(param_def, dict) or param_def.get("type") != "object":
            raise ValueError(
                f"{label} references nested path '{path}', but {'.'.join(consumed)} is not an object parameter."
            )
        properties = param_def.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError(
                f"{label} references nested path '{path}', but {'.'.join(consumed)} has no object properties."
            )
        current = properties


def _value_at_param_path(params: dict[str, Any], path: str) -> Any:
    current: Any = params
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return copy.deepcopy(current)


def _cost_relevant_values(
    *,
    params: dict[str, Any],
    cost_relevant_params: Sequence[str],
) -> dict[str, Any]:
    return {path: _value_at_param_path(params, path) for path in cost_relevant_params}


def _load_profile_param_override(
    *,
    raw_profile: dict[str, Any],
    profile_id: str,
    profile_config_path: Path | None,
) -> dict[str, Any]:
    raw_file = raw_profile.get("param_override_file")
    if raw_file is None:
        return {}
    if not isinstance(raw_file, str) or not raw_file.strip():
        raise ValueError(f"profile {profile_id}: param_override_file must be a string.")
    if profile_config_path is None:
        raise ValueError(
            f"profile {profile_id}: param_override_file requires a config file path."
        )
    override_path = profile_config_path.parent / raw_file.strip()
    return _load_json_object(override_path, f"profile_param_override[{profile_id}]")


def _profile_ref(*, profile_id: str, profile_config_path: Path | None) -> str:
    if profile_config_path is None:
        return f"profile:{profile_id}:inline"
    return f"{profile_config_path.name}:{profile_id}:inline"


def _extra_input_entries(toolspec: dict[str, Any], field: str) -> list[str]:
    extra_inputs = toolspec.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return []
    raw_entries = extra_inputs.get(field, [])
    if not isinstance(raw_entries, list):
        return []
    out: list[str] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        input_key = str(entry.get("input", "")).strip()
        if input_key:
            out.append(input_key)
    return list(dict.fromkeys(out))


def _selected_optional_inputs(
    *,
    toolspec: dict[str, Any],
    raw_profile: dict[str, Any],
    default_optional_inputs: set[str],
) -> list[str]:
    declared_optional = set(_extra_input_entries(toolspec, "optional"))
    if "optional_inputs" in raw_profile:
        raw_optional = raw_profile.get("optional_inputs")
        if not isinstance(raw_optional, list):
            raise ValueError("profile.optional_inputs must be an array.")
        selected = [
            str(item).strip()
            for item in raw_optional
            if isinstance(item, str) and str(item).strip()
        ]
    else:
        selected = sorted(declared_optional.intersection(default_optional_inputs))

    unknown = sorted(set(selected).difference(declared_optional))
    if unknown:
        raise ValueError(
            f"profile optional_inputs are not declared as optional by ToolSpec: {unknown}"
        )
    return list(dict.fromkeys(selected))


def _active_conditional_inputs(
    *,
    toolspec: dict[str, Any],
    resolved_params: dict[str, Any],
    execution: dict[str, Any],
) -> list[str]:
    extra_inputs = toolspec.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return []
    raw_rules = extra_inputs.get("conditional_required", [])
    if not isinstance(raw_rules, list):
        return []

    out: list[str] = []
    for rule in raw_rules:
        if not isinstance(rule, dict):
            continue
        input_key = str(rule.get("input", "")).strip()
        if not input_key:
            continue
        if _conditional_rule_matches(
            rule=rule,
            resolved_params=resolved_params,
            execution=execution,
        ):
            out.append(input_key)
    return list(dict.fromkeys(out))


def _conditional_rule_matches(
    *,
    rule: dict[str, Any],
    resolved_params: dict[str, Any],
    execution: dict[str, Any],
) -> bool:
    param_name = str(rule.get("param", "")).strip()
    execution_name = str(rule.get("execution", "")).strip()
    op = str(rule.get("op", "")).strip()
    if op not in CONDITIONAL_OPS:
        raise ValueError(f"conditional input rule has unsupported op: {op}")
    if param_name and execution_name:
        raise ValueError(
            "conditional input rule must not define both param and execution."
        )
    if param_name:
        actual = resolved_params.get(param_name)
    elif execution_name:
        actual = execution.get(execution_name)
    else:
        raise ValueError("conditional input rule must define param or execution.")
    return _compare_values(actual=actual, op=op, expected=rule.get("value"))


def _compare_values(*, actual: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return isinstance(expected, list) and actual in expected
    if op == "not_in":
        return isinstance(expected, list) and actual not in expected
    if (
        isinstance(actual, bool)
        or isinstance(expected, bool)
        or not isinstance(actual, (int, float))
        or not isinstance(expected, (int, float))
    ):
        return False
    actual_num = float(actual)
    expected_num = float(expected)
    if op == "gt":
        return actual_num > expected_num
    if op == "gte":
        return actual_num >= expected_num
    if op == "lt":
        return actual_num < expected_num
    if op == "lte":
        return actual_num <= expected_num
    return False


def _validate_generated_inputs(
    *,
    profile_id: str,
    required_inputs: Sequence[str],
    optional_inputs: Sequence[str],
    conditional_inputs: Sequence[str],
) -> None:
    selected = set(required_inputs).union(optional_inputs).union(conditional_inputs)
    unsupported = sorted(selected.difference(GENERATED_EXTRA_INPUTS))
    if unsupported:
        raise ValueError(
            f"profile {profile_id}: no benchmark generator exists for input(s): {unsupported}"
        )


def _build_input_profile(
    *,
    raw_profile: dict[str, Any],
    mode: str,
    group_count: int,
    required_inputs: Sequence[str],
    optional_inputs: Sequence[str],
    conditional_inputs: Sequence[str],
    default_prior_density: float,
) -> dict[str, Any]:
    extras = sorted(
        set(required_inputs).union(optional_inputs).union(conditional_inputs)
    )
    column_kind = raw_profile.get("column_kind")
    if column_kind is None:
        column_kind = "samples" if mode == "global" else "cells"
    expression_profile = raw_profile.get("expression_profile")
    if expression_profile is None:
        expression_profile = (
            "synthetic_benchmark"
            if mode == "global"
            else "synthetic_single_cell_benchmark"
        )
    gene_id_source = raw_profile.get("gene_id_source", "synthetic")
    prior_density = raw_profile.get("prior_density")
    if prior_density is None and set(extras).intersection(PRIOR_LIKE_INPUTS):
        prior_density = default_prior_density
    marker_count_per_group = raw_profile.get("marker_count_per_group", 4)
    if isinstance(marker_count_per_group, bool) or not isinstance(marker_count_per_group, int):
        raise ValueError("profile.marker_count_per_group must be an integer.")
    tf_count_policy = raw_profile.get("tf_count_policy")
    if tf_count_policy is None and "tf_list" in extras:
        tf_count_policy = "max(3, genes/5)"
    notes = raw_profile.get("notes", [])
    if notes is None:
        notes = []
    if not isinstance(notes, list):
        raise ValueError("profile.notes must be an array when provided.")

    return {
        "column_kind": str(column_kind),
        "expression_profile": str(expression_profile),
        "gene_id_source": str(gene_id_source),
        "extras_provided": extras,
        "required_inputs_satisfied": sorted(set(required_inputs)),
        "optional_inputs_provided": sorted(set(optional_inputs)),
        "conditional_inputs_satisfied": sorted(set(conditional_inputs)),
        "tf_count_policy": (
            str(tf_count_policy) if tf_count_policy is not None else None
        ),
        "prior_density": (
            _validate_density(prior_density) if prior_density is not None else None
        ),
        "marker_count_per_group": marker_count_per_group,
        "group_count": group_count,
        "has_tf_list": "tf_list" in extras,
        "output_density_class": (
            "dense" if mode in {"column_native", "group_aggregated"} else "sparse"
        ),
        "aggregation_step": "column_to_group" if mode == "group_aggregated" else "none",
        "notes": [str(note) for note in notes]
        or ["Resolved by benchmark_profiles.py from ToolSpec defaults."],
    }


def _validate_density(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("profile.prior_density must be numeric.")
    density = float(value)
    if density < 0 or density > 1:
        raise ValueError("profile.prior_density must be between 0 and 1.")
    return density


def _validate_override_keys(
    *,
    override: dict[str, Any],
    params_schema: dict[str, Any],
    label: str,
) -> None:
    unknown = sorted(set(override.keys()).difference(params_schema.keys()))
    if unknown:
        raise ValueError(f"{label} contains unknown parameter keys: {unknown}")

    for key, value in override.items():
        param_def = params_schema.get(key)
        if not isinstance(param_def, dict):
            continue
        if param_def.get("type") != "object" or not isinstance(value, dict):
            continue
        properties = param_def.get("properties", {})
        if not isinstance(properties, dict):
            continue
        _validate_override_keys(
            override=value,
            params_schema=properties,
            label=f"{label}.{key}",
        )


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = {key: copy.deepcopy(value) for key, value in base.items()}
        for key, value in override.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    return copy.deepcopy(override)
