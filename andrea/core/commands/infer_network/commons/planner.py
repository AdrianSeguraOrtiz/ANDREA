"""Resource estimation and wave planning helpers."""

from __future__ import annotations

import math
import multiprocessing
from pathlib import Path
from typing import Any, Optional

from .shared import DatasetContext, PlanWave, ToolPlanItem, _load_json_object
from .threading import (
    default_threads_for_limits,
    resolve_tool_threading,
    thread_count_allowed_by_tool,
)


def _load_tool_cost_profile(
    *,
    tools_root: Path,
    tool_id: str,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Load optional per-tool cost profile from cost.json used for planning."""
    warnings: list[str] = []
    cost_path = tools_root / tool_id / "cost.json"
    if not cost_path.exists():
        warnings.append(
            f"[{tool_id}] no cost.json found in catalog; cost is unknown and fallback estimation will be used."
        )
        return None, warnings
    if not cost_path.is_file():
        warnings.append(
            f"[{tool_id}] cost.json path is not a file and will be ignored: {cost_path}; "
            "fallback estimation will be used."
        )
        return None, warnings

    try:
        return _load_json_object(cost_path, f"cost[{tool_id}]"), warnings
    except ValueError as exc:
        warnings.append(
            f"[{tool_id}] invalid cost.json ignored ({exc}); fallback estimation will be used."
        )
        return None, warnings


def _nearest_runtime_point(
    *,
    points: list[dict[str, Any]],
    genes: int,
    columns: int,
    threads: int,
    ram_gb: float,
) -> Optional[dict[str, Any]]:
    same_resource = [
        p
        for p in points
        if int(p.get("threads", -1)) == int(threads)
        and float(p.get("ram_gb", -1.0)) == float(ram_gb)
    ]
    if not same_resource:
        same_resource = points
    if not same_resource:
        return None

    def score(point: dict[str, Any]) -> float:
        pg = max(1, int(point.get("genes", 1)))
        pc = max(1, int(point.get("columns", 1)))
        # Log-distance is more stable across scale differences.
        return abs(math.log(genes / pg)) + abs(math.log(columns / pc))

    return min(same_resource, key=score)


def _flatten_param_values(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value} if prefix else {}
    out: dict[str, Any] = {}
    for key, child in value.items():
        child_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, dict):
            out.update(_flatten_param_values(child, child_key))
        else:
            out[child_key] = child
    return out


def _extra_input_entries(toolspec: dict[str, Any], field: str) -> set[str]:
    extra_inputs = toolspec.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return set()
    raw_entries = extra_inputs.get(field, [])
    if not isinstance(raw_entries, list):
        return set()
    out: set[str] = set()
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        input_key = str(entry.get("input", "")).strip()
        if input_key:
            out.add(input_key)
    return out


def _compare_rule_values(*, actual: Any, op: str, expected: Any) -> bool:
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


def _active_conditional_inputs(
    *,
    toolspec: dict[str, Any],
    execution_mode: str,
    resolved_params: dict[str, Any],
) -> set[str]:
    extra_inputs = toolspec.get("extra_inputs", {})
    if not isinstance(extra_inputs, dict):
        return set()
    raw_rules = extra_inputs.get("conditional_required", [])
    if not isinstance(raw_rules, list):
        return set()
    out: set[str] = set()
    for rule in raw_rules:
        if not isinstance(rule, dict):
            continue
        input_key = str(rule.get("input", "")).strip()
        if not input_key:
            continue
        param_name = str(rule.get("param", "")).strip()
        execution_name = str(rule.get("execution", "")).strip()
        op = str(rule.get("op", "")).strip()
        if param_name:
            actual = resolved_params.get(param_name)
        elif execution_name == "mode":
            actual = execution_mode
        else:
            continue
        if _compare_rule_values(actual=actual, op=op, expected=rule.get("value")):
            out.add(input_key)
    return out


def _relevant_extra_inputs(
    *,
    toolspec: dict[str, Any],
    execution_mode: str,
    resolved_params: dict[str, Any],
) -> set[str]:
    return (
        _extra_input_entries(toolspec, "required")
        | _extra_input_entries(toolspec, "optional")
        | _active_conditional_inputs(
            toolspec=toolspec,
            execution_mode=execution_mode,
            resolved_params=resolved_params,
        )
    )


def _profile_config(profile: dict[str, Any]) -> dict[str, Any]:
    benchmark_config = profile.get("benchmark_config", {})
    return benchmark_config if isinstance(benchmark_config, dict) else {}


def _profile_execution(profile: dict[str, Any]) -> dict[str, Any]:
    execution_profile = _profile_config(profile).get("execution_profile", {})
    return execution_profile if isinstance(execution_profile, dict) else {}


def _profile_inputs(profile: dict[str, Any]) -> dict[str, Any]:
    input_profile = _profile_config(profile).get("input_profile", {})
    return input_profile if isinstance(input_profile, dict) else {}


def _profile_params_profile(profile: dict[str, Any]) -> dict[str, Any]:
    params_profile = _profile_config(profile).get("params_profile", {})
    return params_profile if isinstance(params_profile, dict) else {}


def _profile_cost_relevant_params(profile: dict[str, Any]) -> list[str]:
    raw = _profile_params_profile(profile).get("cost_relevant_params", [])
    if not isinstance(raw, list):
        return []
    return [
        str(item).strip() for item in raw if isinstance(item, str) and str(item).strip()
    ]


def _profile_cost_relevant_values(profile: dict[str, Any]) -> dict[str, Any]:
    raw = _profile_params_profile(profile).get("cost_relevant_values", {})
    return raw if isinstance(raw, dict) else {}


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        str(item).strip() for item in value if isinstance(item, str) and item.strip()
    }


def _runtime_points(profile: dict[str, Any]) -> list[dict[str, Any]]:
    raw_points = profile.get("runtime_points", [])
    if not isinstance(raw_points, list):
        return []
    return [point for point in raw_points if isinstance(point, dict)]


def _inference_cost_features(
    *,
    execution_mode: str,
    dataset: DatasetContext,
    extras_present: set[str],
    logical_group_count: Optional[int],
) -> dict[str, Any]:
    n_cells = int(dataset.columns) if dataset.column_kind == "cells" else 0
    n_genes = int(dataset.genes)
    n_groups = int(logical_group_count or 0)
    if execution_mode == "cell_native":
        expected_contexts = max(1, n_cells)
    elif execution_mode in {"group_native", "group_emulated", "group_aggregated"}:
        expected_contexts = max(1, n_groups)
    else:
        expected_contexts = 1
    aggregation_step = (
        "cell_to_group" if execution_mode == "group_aggregated" else "none"
    )
    return {
        "execution_mode": execution_mode,
        "n_cells": n_cells,
        "n_genes": n_genes,
        "n_groups": n_groups,
        "expected_contexts": int(expected_contexts),
        "expected_dense_edges": int(n_cells * n_genes * max(0, n_genes - 1)),
        "has_tf_list": "tf_list" in extras_present,
        "has_chromatin_accessibility_matrix": (
            "chromatin_accessibility_matrix" in extras_present
        ),
        "output_density_class": (
            "dense"
            if execution_mode in {"cell_native", "group_aggregated"}
            else "sparse"
        ),
        "aggregation_step": aggregation_step,
    }


def _valid_runtime_points(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        p
        for p in _runtime_points(profile)
        if p.get("status") in {"ok", "partial"}
        and isinstance(p.get("seconds_p50"), (int, float))
        and isinstance(p.get("seconds_p90"), (int, float))
        and isinstance(p.get("threads"), int)
        and isinstance(p.get("ram_gb"), (int, float))
        and int(p.get("threads")) >= 1
        and float(p.get("ram_gb")) > 0
    ]


def _param_difference_count(
    *,
    planned_params: dict[str, Any],
    profile_cost_relevant_values: dict[str, Any],
    cost_relevant_params: list[str],
) -> int:
    if not cost_relevant_params:
        return 0
    planned_flat = _flatten_param_values(planned_params)
    return sum(
        1
        for key in cost_relevant_params
        if planned_flat.get(key) != profile_cost_relevant_values.get(key)
    )


def _cost_profile_match_quality(
    *,
    extra_inputs_missing_from_profile: set[str],
    param_difference_count: int,
    group_distance: int,
) -> str:
    input_quality = (
        "exact_inputs" if not extra_inputs_missing_from_profile else "approx_inputs"
    )
    params_quality = "exact_params" if param_difference_count == 0 else "approx_params"
    group_quality = "exact_groups" if group_distance == 0 else "approx_groups"
    return f"exact_mode_{input_quality}_{params_quality}_{group_quality}"


def _select_cost_profile(
    *,
    tool_id: str,
    toolspec: dict[str, Any],
    cost_payload: dict[str, Any],
    execution_mode: str,
    extras_present: set[str],
    resolved_params: dict[str, Any],
    logical_group_count: Optional[int],
) -> tuple[Optional[dict[str, Any]], dict[str, Any], list[str]]:
    """Select the most compatible cost profile for a planned run."""
    profiles = cost_payload.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        return (
            None,
            {},
            [
                f"[{tool_id}] cost.json has no profiles array; fallback estimation will be used."
            ],
        )

    relevant_inputs = _relevant_extra_inputs(
        toolspec=toolspec,
        execution_mode=execution_mode,
        resolved_params=resolved_params,
    )
    relevant_extras_present = extras_present.intersection(relevant_inputs)
    candidates: list[
        tuple[tuple[int, int, int, int, int], dict[str, Any], dict[str, Any]]
    ] = []
    mode_matches = 0
    for profile_idx, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            continue
        execution_profile = _profile_execution(profile)
        if str(execution_profile.get("mode", "")).strip() == execution_mode:
            mode_matches += 1
        else:
            continue

        input_profile = _profile_inputs(profile)
        profile_required = _string_set(input_profile.get("required_inputs_satisfied"))
        profile_conditional = _string_set(
            input_profile.get("conditional_inputs_satisfied")
        )
        profile_optional = _string_set(input_profile.get("optional_inputs_provided"))
        profile_contract = profile_required.union(profile_conditional)
        missing_contract = profile_contract.difference(extras_present)
        missing_profile_optional = profile_optional.difference(extras_present)

        # Do not estimate with a profile that benchmarked extra method inputs absent
        # from the planned run. Missing optional inputs can change upstream work.
        if missing_contract or missing_profile_optional:
            continue

        profile_extras = _string_set(input_profile.get("extras_provided"))
        extra_inputs_missing_from_profile = relevant_extras_present.difference(
            profile_extras
        )
        profile_group_count = int(execution_profile.get("group_count") or 0)
        if logical_group_count is None:
            group_distance = 0
        else:
            group_distance = abs(int(logical_group_count) - profile_group_count)

        cost_relevant_params = _profile_cost_relevant_params(profile)
        cost_relevant_values = _profile_cost_relevant_values(profile)
        param_diffs = _param_difference_count(
            planned_params=resolved_params,
            profile_cost_relevant_values=cost_relevant_values,
            cost_relevant_params=cost_relevant_params,
        )
        valid_points = _valid_runtime_points(profile)
        no_runtime_penalty = 1 if not valid_points else 0
        profile_id = str(profile.get("profile_id", "")).strip()
        metadata = {
            "profile_id": profile_id,
            "match_quality": _cost_profile_match_quality(
                extra_inputs_missing_from_profile=extra_inputs_missing_from_profile,
                param_difference_count=param_diffs,
                group_distance=group_distance,
            ),
            "extra_inputs_missing_from_profile": sorted(
                extra_inputs_missing_from_profile
            ),
            "param_difference_count": int(param_diffs),
            "cost_relevant_params": cost_relevant_params,
            "cost_relevant_values": cost_relevant_values,
            "group_distance": int(group_distance),
            "profile_execution_mode": execution_mode,
            "profile_group_count": profile_group_count,
        }
        score = (
            no_runtime_penalty,
            len(extra_inputs_missing_from_profile),
            param_diffs,
            group_distance,
            profile_idx,
        )
        candidates.append((score, profile, metadata))

    if mode_matches == 0:
        return (
            None,
            {},
            [
                f"[{tool_id}] cost.json has no profile for execution.mode={execution_mode}; fallback estimation will be used."
            ],
        )

    if not candidates:
        return (
            None,
            {},
            [
                f"[{tool_id}] cost.json has profiles for execution.mode={execution_mode}, "
                "but none are compatible with the planned required/optional inputs; "
                "fallback estimation will be used."
            ],
        )

    _score, selected, metadata = min(candidates, key=lambda item: item[0])
    warnings: list[str] = []
    if metadata["extra_inputs_missing_from_profile"]:
        warnings.append(
            f"[{tool_id}] selected approximate cost profile {metadata['profile_id']}: "
            "planned run has extra input(s) absent from benchmark profile "
            f"{metadata['extra_inputs_missing_from_profile']}."
        )
    if metadata["param_difference_count"] > 0:
        warnings.append(
            f"[{tool_id}] selected approximate cost profile {metadata['profile_id']}: "
            f"{metadata['param_difference_count']} cost-relevant parameter value(s) differ."
        )
    if metadata["group_distance"] > 0:
        warnings.append(
            f"[{tool_id}] selected approximate cost profile {metadata['profile_id']}: "
            f"group count differs by {metadata['group_distance']}."
        )

    return selected, metadata, warnings


def _fallback_plan_item(
    *,
    tool_id: str,
    run_id: str,
    image: str,
    dataset: DatasetContext,
    execution_mode: str,
    max_cores: int,
    max_ram_gb: float,
    eta_source: str,
    output_dir: str,
    threads: int = 1,
    group_label: Optional[str] = None,
    eta_provenance: Optional[dict[str, Any]] = None,
) -> ToolPlanItem:
    fallback_eta = max(10.0, 0.02 * dataset.genes * dataset.columns)
    if execution_mode == "cell_native":
        dense_cell_edges = max(0, dataset.genes * (dataset.genes - 1)) * dataset.columns
        fallback_eta = max(fallback_eta, 10.0 + (0.0000001 * dense_cell_edges))
    return ToolPlanItem(
        tool_id=tool_id,
        run_id=run_id,
        image=image,
        threads=max(1, min(max_cores, int(threads))),
        ram_gb=max(1.0, min(max_ram_gb, 4.0)),
        eta_seconds=round(fallback_eta, 3),
        eta_source=eta_source,
        output_dir=output_dir,
        group_label=group_label,
        eta_provenance=eta_provenance,
    )


def _mode_risk_penalty(point: dict[str, Any]) -> float:
    repeats_total = int(point.get("repeats_total", 0))
    if repeats_total <= 0:
        return 2.0

    ok_rate_raw = point.get("ok_rate")
    if isinstance(ok_rate_raw, (int, float)):
        ok_rate = min(1.0, max(0.0, float(ok_rate_raw)))
    else:
        repeats_ok = int(point.get("repeats_ok", 0))
        ok_rate = min(1.0, max(0.0, repeats_ok / repeats_total))

    failure_breakdown = point.get("failure_breakdown", {})
    oom = timeout = error = 0
    if isinstance(failure_breakdown, dict):
        oom = int(failure_breakdown.get("oom", 0) or 0)
        timeout = int(failure_breakdown.get("timeout", 0) or 0)
        error = int(failure_breakdown.get("error", 0) or 0)

    fail_rate = 1.0 - ok_rate
    oom_rate = max(0.0, float(oom) / repeats_total)
    timeout_rate = max(0.0, float(timeout) / repeats_total)
    error_rate = max(0.0, float(error) / repeats_total)

    # Conservative risk model: unstable points are penalized to reduce planner optimism.
    penalty = (
        1.0
        + (0.50 * fail_rate)
        + (0.90 * oom_rate)
        + (0.70 * timeout_rate)
        + (0.40 * error_rate)
    )
    return max(1.0, penalty)


def _prune_modes_by_pareto(modes: list[ToolPlanItem]) -> list[ToolPlanItem]:
    if not modes:
        return []
    kept: list[ToolPlanItem] = []
    for mode in sorted(modes, key=lambda x: (x.eta_seconds, x.threads, x.ram_gb)):
        dominated = False
        for other in kept:
            better_or_equal = (
                other.eta_seconds <= mode.eta_seconds
                and other.threads <= mode.threads
                and other.ram_gb <= mode.ram_gb
            )
            strictly_better = (
                other.eta_seconds < mode.eta_seconds
                or other.threads < mode.threads
                or other.ram_gb < mode.ram_gb
            )
            if better_or_equal and strictly_better:
                dominated = True
                break
        if not dominated:
            kept.append(mode)
    return kept


def _estimate_tool_mode_options(
    *,
    tool_id: str,
    run_id: str,
    toolspec: dict[str, Any],
    cost_profile: Optional[dict[str, Any]],
    execution_mode: str,
    resolved_params: dict[str, Any],
    extras_present: set[str],
    logical_group_count: Optional[int],
    physical_tasks_total: int,
    dataset: DatasetContext,
    max_cores: int,
    max_ram_gb: float,
    output_dir: str,
    group_label: Optional[str] = None,
) -> tuple[list[ToolPlanItem], list[str]]:
    warnings: list[str] = []
    modes: list[ToolPlanItem] = []
    image = str(toolspec.get("docker_image", "")).strip()
    if not image:
        raise ValueError(f"[{tool_id}] toolspec.docker_image is missing")
    threading, threading_warnings = resolve_tool_threading(
        tool_id=tool_id,
        toolspec=toolspec,
    )
    warnings.extend(threading_warnings)
    fallback_threads = default_threads_for_limits(threading, max_cores=max_cores)

    cost = cost_profile
    cost_features = _inference_cost_features(
        execution_mode=execution_mode,
        dataset=dataset,
        extras_present=extras_present,
        logical_group_count=logical_group_count,
    )
    if not isinstance(cost, dict):
        return (
            [
                _fallback_plan_item(
                    tool_id=tool_id,
                    run_id=run_id,
                    image=image,
                    dataset=dataset,
                    execution_mode=execution_mode,
                    max_cores=max_cores,
                    max_ram_gb=max_ram_gb,
                    eta_source="fallback_no_cost",
                    output_dir=output_dir,
                    threads=fallback_threads,
                    group_label=group_label,
                    eta_provenance={
                        "eta_source": "fallback",
                        "warnings": [
                            "no cost.json payload available",
                            *threading_warnings,
                        ],
                        "cost_features": cost_features,
                    },
                )
            ],
            warnings,
        )

    selected_profile, profile_match, profile_warnings = _select_cost_profile(
        tool_id=tool_id,
        toolspec=toolspec,
        cost_payload=cost,
        execution_mode=execution_mode,
        extras_present=extras_present,
        resolved_params=resolved_params,
        logical_group_count=logical_group_count,
    )
    warnings.extend(profile_warnings)
    if selected_profile is None:
        return (
            [
                _fallback_plan_item(
                    tool_id=tool_id,
                    run_id=run_id,
                    image=image,
                    dataset=dataset,
                    execution_mode=execution_mode,
                    max_cores=max_cores,
                    max_ram_gb=max_ram_gb,
                    eta_source="fallback_no_matching_cost_profile",
                    output_dir=output_dir,
                    threads=fallback_threads,
                    group_label=group_label,
                    eta_provenance={
                        "eta_source": "fallback",
                        "warnings": [*profile_warnings, *threading_warnings],
                        "execution_mode": execution_mode,
                        "cost_features": cost_features,
                    },
                )
            ],
            warnings,
        )

    raw_valid_points = _valid_runtime_points(selected_profile)
    incompatible_thread_values = sorted(
        {
            int(point["threads"])
            for point in raw_valid_points
            if not thread_count_allowed_by_tool(threading, int(point["threads"]))
        }
    )
    cost_point_warnings: list[str] = []
    if incompatible_thread_values:
        cost_point_warnings.append(
            f"[{tool_id}] cost.json contains runtime point thread value(s) incompatible "
            "with toolspec.runtime_resources.threading and they were ignored: "
            f"{incompatible_thread_values}."
        )
        warnings.extend(cost_point_warnings)
    valid_points = [
        point
        for point in raw_valid_points
        if thread_count_allowed_by_tool(threading, int(point["threads"]))
    ]

    candidate_resources = sorted(
        {
            (int(p["threads"]), round(float(p["ram_gb"]), 3))
            for p in valid_points
            if int(p["threads"]) <= max_cores and float(p["ram_gb"]) <= max_ram_gb
        }
    )

    for threads, ram in candidate_resources:
        nearest = _nearest_runtime_point(
            points=valid_points,
            genes=dataset.genes,
            columns=dataset.columns,
            threads=threads,
            ram_gb=ram,
        )
        if nearest is None:
            continue

        p50 = float(nearest["seconds_p50"])
        p90 = float(nearest["seconds_p90"])
        pg = max(1, int(nearest.get("genes", dataset.genes)))
        pc = max(1, int(nearest.get("columns", dataset.columns)))
        size_scale = math.sqrt((dataset.genes / pg) * (dataset.columns / pc))
        robust_base = max(p90, (0.70 * p90 + 0.30 * p50))
        eta = max(0.1, robust_base * size_scale * _mode_risk_penalty(nearest))
        profile_id = str(selected_profile.get("profile_id", "")).strip()
        nearest_point = {
            "genes": int(nearest.get("genes", pg)),
            "columns": int(nearest.get("columns", pc)),
            "threads": int(nearest.get("threads", threads)),
            "ram_gb": round(float(nearest.get("ram_gb", ram)), 3),
            "status": str(nearest.get("status", "")),
            "seconds_p50": round(float(p50), 6),
            "seconds_p90": round(float(p90), 6),
            "ok_rate": nearest.get("ok_rate"),
        }
        size_exact = pg == dataset.genes and pc == dataset.columns
        resource_exact = int(nearest.get("threads", threads)) == int(threads) and round(
            float(nearest.get("ram_gb", ram)), 3
        ) == round(float(ram), 3)
        point_quality = ("exact_size" if size_exact else "nearest_size") + (
            "_exact_resources" if resource_exact else "_nearest_resources"
        )
        provenance_warnings = [
            *profile_warnings,
            *threading_warnings,
            *cost_point_warnings,
        ]
        if nearest.get("status") == "partial":
            provenance_warnings.append(
                "nearest benchmark point has partial success; ETA includes risk penalty"
            )

        modes.append(
            ToolPlanItem(
                tool_id=tool_id,
                run_id=run_id,
                image=image,
                threads=int(threads),
                ram_gb=round(float(ram), 3),
                eta_seconds=round(float(eta), 3),
                eta_source="cost_profile",
                output_dir=output_dir,
                group_label=group_label,
                eta_provenance={
                    "eta_source": "cost_profile",
                    "cost_features": cost_features,
                    "cost_profile": {
                        "tool_id": str(toolspec.get("id", tool_id)),
                        "profile_id": profile_id,
                        "match_quality": (
                            f"{profile_match.get('match_quality', 'unknown')}_{point_quality}"
                        ),
                        "profile_execution_mode": execution_mode,
                        "profile_group_count": profile_match.get("profile_group_count"),
                        "cost_relevant_params": profile_match.get(
                            "cost_relevant_params", []
                        ),
                        "cost_relevant_values": profile_match.get(
                            "cost_relevant_values", {}
                        ),
                        "nearest_runtime_point": nearest_point,
                        "size_scale": round(float(size_scale), 6),
                        "risk_penalty": round(float(_mode_risk_penalty(nearest)), 6),
                        "multipliers": {
                            "physical_tasks": int(max(1, physical_tasks_total)),
                            "group_count": (
                                int(logical_group_count)
                                if logical_group_count is not None
                                else None
                            ),
                        },
                        "cost_features": cost_features,
                        "warnings": provenance_warnings,
                    },
                },
            )
        )

    if modes:
        pruned = _prune_modes_by_pareto(modes)
        # Keep search tractable while preserving best alternatives.
        return (
            sorted(pruned, key=lambda x: (x.eta_seconds, x.threads, x.ram_gb))[:8],
            warnings,
        )

    warnings.append(
        f"[{tool_id}] cost.json exists but has no usable runtime_points; using fallback estimation."
    )
    return (
        [
            _fallback_plan_item(
                tool_id=tool_id,
                run_id=run_id,
                image=image,
                dataset=dataset,
                execution_mode=execution_mode,
                max_cores=max_cores,
                max_ram_gb=max_ram_gb,
                eta_source="fallback_no_usable_runtime_point",
                output_dir=output_dir,
                threads=fallback_threads,
                group_label=group_label,
                eta_provenance={
                    "eta_source": "fallback",
                    "cost_features": cost_features,
                    "cost_profile": {
                        "profile_id": profile_match.get("profile_id"),
                        "match_quality": profile_match.get("match_quality"),
                        "warnings": [
                            "selected profile has no runtime point compatible with resource limits",
                            *threading_warnings,
                            *cost_point_warnings,
                        ],
                    },
                },
            )
        ],
        warnings,
    )


def _build_parallel_waves(
    *,
    items: list[ToolPlanItem],
    max_cores: int,
    max_ram_gb: float,
) -> tuple[list[PlanWave], float]:
    waves: list[PlanWave] = []

    # Longest tasks first + best-fit placement to minimize ETA increase and resource waste.
    sorted_items = sorted(items, key=lambda x: x.eta_seconds, reverse=True)

    for item in sorted_items:
        best_idx: Optional[int] = None
        best_score: Optional[tuple[float, float]] = None
        for idx, wave in enumerate(waves):
            next_cores = wave.threads_used + item.threads
            next_ram = wave.ram_gb_used + item.ram_gb
            if next_cores <= max_cores and next_ram <= max_ram_gb:
                eta_increase = max(0.0, item.eta_seconds - wave.eta_seconds)
                residual_cores = float(max_cores - next_cores)
                residual_ram = float(max_ram_gb - next_ram)
                residual_score = residual_cores + residual_ram
                score = (eta_increase, residual_score)
                if best_score is None or score < best_score:
                    best_score = score
                    best_idx = idx

        if best_idx is not None:
            wave = waves[best_idx]
            next_tasks = wave.tasks + [item]
            waves[best_idx] = PlanWave(
                index=wave.index,
                threads_used=wave.threads_used + item.threads,
                ram_gb_used=round(wave.ram_gb_used + item.ram_gb, 3),
                eta_seconds=max(wave.eta_seconds, item.eta_seconds),
                tasks=next_tasks,
            )
            continue

        waves.append(
            PlanWave(
                index=len(waves) + 1,
                threads_used=item.threads,
                ram_gb_used=round(item.ram_gb, 3),
                eta_seconds=item.eta_seconds,
                tasks=[item],
            )
        )

    total_eta = round(sum(w.eta_seconds for w in waves), 3)
    return waves, total_eta


def _optimize_mode_selection(
    *,
    mode_options_by_tool: dict[str, list[ToolPlanItem]],
    max_cores: int,
    max_ram_gb: float,
) -> tuple[list[ToolPlanItem], list[PlanWave], float]:
    tool_ids = sorted(mode_options_by_tool.keys())
    if not tool_ids:
        return [], [], 0.0

    selected_index: dict[str, int] = {}
    for tool_id in tool_ids:
        options = mode_options_by_tool.get(tool_id, [])
        if not options:
            raise ValueError(f"[{tool_id}] no executable mode available for planning")
        selected_index[tool_id] = 0

    def build_items(idx_map: dict[str, int]) -> list[ToolPlanItem]:
        return [mode_options_by_tool[t][idx_map[t]] for t in tool_ids]

    current_items = build_items(selected_index)
    current_waves, current_total = _build_parallel_waves(
        items=current_items,
        max_cores=max_cores,
        max_ram_gb=max_ram_gb,
    )

    # Iterative single-tool mode swaps (steepest descent) for global ETA minimization.
    max_iters = max(2, len(tool_ids) * 2)
    for _ in range(max_iters):
        best_move: Optional[tuple[str, int, list[PlanWave], float]] = None
        for tool_id in tool_ids:
            current_idx = selected_index[tool_id]
            options = mode_options_by_tool[tool_id]
            for candidate_idx, _candidate in enumerate(options):
                if candidate_idx == current_idx:
                    continue
                trial_index = dict(selected_index)
                trial_index[tool_id] = candidate_idx
                trial_items = build_items(trial_index)
                trial_waves, trial_total = _build_parallel_waves(
                    items=trial_items,
                    max_cores=max_cores,
                    max_ram_gb=max_ram_gb,
                )
                better = (trial_total + 1e-9) < current_total or (
                    abs(trial_total - current_total) <= 1e-9
                    and len(trial_waves) < len(current_waves)
                )
                if not better:
                    continue
                if best_move is None or (trial_total, len(trial_waves)) < (
                    best_move[3],
                    len(best_move[2]),
                ):
                    best_move = (tool_id, candidate_idx, trial_waves, trial_total)

        if best_move is None:
            break

        tool_id, candidate_idx, best_waves, best_total = best_move
        selected_index[tool_id] = candidate_idx
        current_waves = best_waves
        current_total = best_total

    selected_items = build_items(selected_index)
    return selected_items, current_waves, round(current_total, 3)


def _optimize_mode_selection_cp_sat(
    *,
    mode_options_by_tool: dict[str, list[ToolPlanItem]],
    max_cores: int,
    max_ram_gb: float,
    time_limit_seconds: float,
    warnings: list[str],
) -> Optional[tuple[list[ToolPlanItem], list[PlanWave], float]]:
    from ortools.sat.python import cp_model  # type: ignore

    tool_ids = sorted(mode_options_by_tool.keys())
    if not tool_ids:
        return [], [], 0.0

    for tool_id in tool_ids:
        if not mode_options_by_tool.get(tool_id):
            warnings.append(
                f"[planner=cp_sat] tool '{tool_id}' has no candidate modes; falling back to heuristic planner."
            )
            return None

    num_tools = len(tool_ids)
    max_waves = num_tools
    ram_scale = 1000
    eta_scale = 1000
    max_ram_units = max(1, int(round(max_ram_gb * ram_scale)))

    horizon = 0
    for tool_id in tool_ids:
        max_eta_tool = max(mode.eta_seconds for mode in mode_options_by_tool[tool_id])
        horizon += int(round(max_eta_tool * eta_scale))
    horizon = max(1, horizon)

    model = cp_model.CpModel()

    x: dict[tuple[int, int, int], Any] = {}
    wave_eta: list[Any] = [
        model.NewIntVar(0, horizon, f"wave_eta_{w}") for w in range(max_waves)
    ]
    wave_used: list[Any] = [
        model.NewBoolVar(f"wave_used_{w}") for w in range(max_waves)
    ]

    # x[i,m,w] == 1 iff tool i uses mode m and is assigned to wave w.
    for i, tool_id in enumerate(tool_ids):
        modes = mode_options_by_tool[tool_id]
        for m, _mode in enumerate(modes):
            for w in range(max_waves):
                x[(i, m, w)] = model.NewBoolVar(f"x_{i}_{m}_{w}")

    # Every tool must select exactly one (mode, wave) assignment.
    for i, tool_id in enumerate(tool_ids):
        modes = mode_options_by_tool[tool_id]
        model.Add(
            sum(x[(i, m, w)] for m in range(len(modes)) for w in range(max_waves)) == 1
        )

    for w in range(max_waves):
        cores_terms = []
        ram_terms = []
        assignment_terms = []
        for i, tool_id in enumerate(tool_ids):
            modes = mode_options_by_tool[tool_id]
            for m, mode in enumerate(modes):
                var = x[(i, m, w)]
                cores_terms.append(var * int(mode.threads))
                ram_terms.append(var * int(round(mode.ram_gb * ram_scale)))
                assignment_terms.append(var)

                eta_units = int(round(mode.eta_seconds * eta_scale))
                model.Add(wave_eta[w] >= eta_units).OnlyEnforceIf(var)
                model.Add(var <= wave_used[w])

        model.Add(sum(cores_terms) <= int(max_cores))
        model.Add(sum(ram_terms) <= max_ram_units)
        model.Add(wave_eta[w] <= horizon * wave_used[w])
        # Prevent empty "used" waves: wave_used[w] == 1 implies at least one task in wave w.
        model.Add(sum(assignment_terms) >= wave_used[w])

    # Force contiguous usage of waves to reduce symmetry.
    for w in range(max_waves - 1):
        model.Add(wave_used[w] >= wave_used[w + 1])

    # Primary objective: total sequential wave ETA. Secondary: sum of selected ETA.
    total_wave_eta = sum(wave_eta)
    total_selected_eta = []
    for i, tool_id in enumerate(tool_ids):
        modes = mode_options_by_tool[tool_id]
        for m, mode in enumerate(modes):
            eta_units = int(round(mode.eta_seconds * eta_scale))
            for w in range(max_waves):
                total_selected_eta.append(x[(i, m, w)] * eta_units)
    model.Minimize(total_wave_eta * 1000 + sum(total_selected_eta))

    solver = cp_model.CpSolver()
    if time_limit_seconds > 0:
        solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.num_search_workers = max(1, min(8, multiprocessing.cpu_count()))

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        warnings.append(
            f"[planner=cp_sat] solver failed with status={solver.StatusName(status)}; "
            "falling back to heuristic planner."
        )
        return None

    waves: list[PlanWave] = []
    selected_items: list[ToolPlanItem] = []

    for w in range(max_waves):
        if solver.Value(wave_used[w]) != 1:
            continue

        tasks: list[ToolPlanItem] = []
        threads_used = 0
        ram_used = 0.0
        for i, tool_id in enumerate(tool_ids):
            modes = mode_options_by_tool[tool_id]
            chosen_mode: Optional[ToolPlanItem] = None
            for m, mode in enumerate(modes):
                if solver.Value(x[(i, m, w)]) == 1:
                    chosen_mode = mode
                    break
            if chosen_mode is None:
                continue
            tasks.append(chosen_mode)
            selected_items.append(chosen_mode)
            threads_used += chosen_mode.threads
            ram_used += chosen_mode.ram_gb

        # Defensive guard: should be unreachable with model constraints above.
        if not tasks:
            continue

        wave_eta_seconds = round(float(solver.Value(wave_eta[w])) / eta_scale, 3)
        waves.append(
            PlanWave(
                index=len(waves) + 1,
                threads_used=int(threads_used),
                ram_gb_used=round(float(ram_used), 3),
                eta_seconds=wave_eta_seconds,
                tasks=tasks,
            )
        )

    total_eta = round(sum(w.eta_seconds for w in waves), 3)
    return selected_items, waves, total_eta
