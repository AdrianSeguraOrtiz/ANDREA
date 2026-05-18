"""Runtime cost estimation and wave planning for generate-data."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .request import resolve_simulator_runtime_resources
from .shared import CATALOG_ROOT, _load_json_object


@dataclass(frozen=True)
class SimulatorRunMode:
    run_id: str
    simulator_id: str
    threads: int
    ram_gb: float
    eta_seconds: float
    eta_source: str
    eta_provenance: dict[str, Any]


@dataclass(frozen=True)
class SimulatorTaskItem:
    task_id: str
    run_id: str
    simulator_id: str
    threads: int
    ram_gb: float
    eta_seconds: float
    eta_source: str
    eta_provenance: dict[str, Any]


@dataclass(frozen=True)
class SimulatorWave:
    index: int
    threads_used: int
    ram_gb_used: float
    eta_seconds: float
    tasks: list[SimulatorTaskItem]


def detect_host_ram_gb() -> float:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) / (1024 * 1024)
    return 8.0


def _load_simulator_cost_payload(simulator_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    cost_path = CATALOG_ROOT / "simulators" / simulator_id / "cost.json"
    if not cost_path.exists():
        return None, [
            f"[{simulator_id}] no cost.json found; using conservative fallback ETA."
        ]
    try:
        return _load_json_object(cost_path, f"simulator-cost[{simulator_id}]"), []
    except ValueError as exc:
        return None, [
            f"[{simulator_id}] invalid cost.json ignored ({exc}); using conservative fallback ETA."
        ]


def _value_at_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _flatten_values(payload: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {prefix: payload} if prefix else {}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        child = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten_values(value, child))
        else:
            out[child] = value
    return out


def _string_set(raw: Any) -> set[str]:
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if isinstance(item, str) and item.strip()}


def _profile_config(profile: dict[str, Any]) -> dict[str, Any]:
    raw = profile.get("benchmark_config", {})
    return raw if isinstance(raw, dict) else {}


def _profile_input_profile(profile: dict[str, Any]) -> dict[str, Any]:
    raw = _profile_config(profile).get("input_profile", {})
    return raw if isinstance(raw, dict) else {}


def _profile_params_profile(profile: dict[str, Any]) -> dict[str, Any]:
    raw = _profile_config(profile).get("params_profile", {})
    return raw if isinstance(raw, dict) else {}


def _runtime_points(profile: dict[str, Any]) -> list[dict[str, Any]]:
    points = profile.get("runtime_points", [])
    if not isinstance(points, list):
        return []
    return [
        point
        for point in points
        if isinstance(point, dict)
        and point.get("status") in {"ok", "partial"}
        and isinstance(point.get("seconds_p50"), (int, float))
        and isinstance(point.get("seconds_p90"), (int, float))
        and isinstance(point.get("threads"), int)
        and isinstance(point.get("ram_gb"), (int, float))
    ]


def _runtime_penalty(point: dict[str, Any]) -> float:
    repeats_total = max(1, int(point.get("repeats_total", 1) or 1))
    ok_rate_raw = point.get("ok_rate")
    if isinstance(ok_rate_raw, (int, float)):
        ok_rate = min(1.0, max(0.0, float(ok_rate_raw)))
    else:
        ok_rate = min(1.0, max(0.0, float(point.get("repeats_ok", 0) or 0) / repeats_total))
    breakdown = point.get("failure_breakdown", {})
    oom = timeout = error = 0
    if isinstance(breakdown, dict):
        oom = int(breakdown.get("oom", 0) or 0)
        timeout = int(breakdown.get("timeout", 0) or 0)
        error = int(breakdown.get("error", 0) or 0)
    return max(
        1.0,
        1.0
        + (0.5 * (1.0 - ok_rate))
        + (0.9 * oom / repeats_total)
        + (0.7 * timeout / repeats_total)
        + (0.4 * error / repeats_total),
    )


def _estimate_dimensions(
    *, params: dict[str, Any], selected_profile: dict[str, Any] | None
) -> tuple[int, int, int, int, dict[str, Any]]:
    dimension_profile: dict[str, Any] = {}
    if isinstance(selected_profile, dict):
        raw_dimension = _profile_config(selected_profile).get("dimension_profile", {})
        if isinstance(raw_dimension, dict):
            dimension_profile = raw_dimension

    cells = None
    genes = None
    if dimension_profile:
        cells_param = str(dimension_profile.get("cells_param") or "").strip()
        if cells_param:
            cells = _value_at_path(params, cells_param)
        genes_param = dimension_profile.get("genes_param")
        if isinstance(genes_param, str):
            genes = _value_at_path(params, genes_param)
        elif isinstance(genes_param, dict):
            total = 0
            found = False
            for path in genes_param:
                value = _value_at_path(params, str(path))
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    total += int(value)
                    found = True
            if found:
                genes = total

    if not isinstance(cells, (int, float)) or isinstance(cells, bool):
        cells = _value_at_path(params, "num_cells")
    if not isinstance(genes, (int, float)) or isinstance(genes, bool):
        num_genes = _value_at_path(params, "num_genes")
        if isinstance(num_genes, (int, float)) and not isinstance(num_genes, bool):
            genes = int(num_genes)
        else:
            genes = sum(
                int(value)
                for value in (
                    _value_at_path(params, "num_tfs"),
                    _value_at_path(params, "num_targets"),
                    _value_at_path(params, "num_hks"),
                )
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            )

    resolved_genes = max(1, int(genes or 100))
    resolved_cells = max(1, int(cells or 100))
    groups = max(0, int(dimension_profile.get("group_count", 0) or 0))
    population_count = max(0, int(dimension_profile.get("population_count", groups) or 0))
    return resolved_genes, resolved_cells, groups, population_count, dimension_profile


def _infer_tf_count(
    *,
    params: dict[str, Any],
    dimension_profile: dict[str, Any],
    genes: int,
) -> int | None:
    value = _value_at_path(params, "num_tfs")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    genes_param = dimension_profile.get("genes_param")
    if not isinstance(genes_param, dict) or "num_tfs" not in genes_param:
        return None
    min_total = 0
    rules: dict[str, dict[str, Any]] = {}
    for path, raw_rule in genes_param.items():
        if not isinstance(raw_rule, dict):
            continue
        rule = {
            "fraction": float(raw_rule.get("fraction", 0) or 0),
            "min": int(raw_rule.get("min", 0) or 0),
        }
        rules[str(path)] = rule
        min_total += rule["min"]
    if not rules or min_total > genes:
        return None
    remaining = genes - min_total
    allocations = {
        path: int(rule["min"]) + int(math.floor(float(rule["fraction"]) * remaining))
        for path, rule in rules.items()
    }
    assigned = sum(allocations.values())
    order = sorted(
        rules,
        key=lambda path: (
            (float(rules[path]["fraction"]) * remaining)
            - math.floor(float(rules[path]["fraction"]) * remaining),
            path,
        ),
        reverse=True,
    )
    idx = 0
    while assigned < genes and order:
        allocations[order[idx % len(order)]] += 1
        assigned += 1
        idx += 1
    value = allocations.get("num_tfs")
    return max(0, int(value)) if isinstance(value, int) else None


def _dynamic_grn_flags(params: dict[str, Any]) -> dict[str, Any]:
    keywords = ("dynamic", "grn", "backbone")
    out: dict[str, Any] = {}
    for path, value in _flatten_values(params).items():
        lowered = path.lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[path] = value
    return out


def _simulation_cost_features(
    *,
    params: dict[str, Any],
    scenario_profile: str,
    genes: int,
    cells: int,
    groups: int,
    population_count: int,
    dimension_profile: dict[str, Any],
) -> dict[str, Any]:
    return {
        "profile": scenario_profile,
        "n_cells": int(cells),
        "n_genes": int(genes),
        "n_tfs": _infer_tf_count(
            params=params,
            dimension_profile=dimension_profile,
            genes=genes,
        ),
        "n_groups": int(groups),
        "population_count": int(population_count),
        "native_cell_truth_enabled": scenario_profile == "scrna_cell_specific",
        "dynamic_grn_flags": _dynamic_grn_flags(params),
    }


def _select_cost_profile(
    *,
    simulator_id: str,
    cost_payload: dict[str, Any],
    scenario_profile: str,
    requested_extras: list[str],
    effective_extras: list[str],
    input_ids: set[str],
    params: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str]]:
    profiles = cost_payload.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        return None, {}, [f"[{simulator_id}] cost.json contains no profiles."]

    requested = set(requested_extras)
    effective = set(effective_extras)
    params_flat = _flatten_values(params)
    candidates: list[tuple[tuple[int, int, int, int, int], dict[str, Any], dict[str, Any]]] = []
    profile_matches = 0
    for idx, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            continue
        config = _profile_config(profile)
        if str(config.get("profile") or "") != scenario_profile:
            continue
        profile_matches += 1
        input_profile = _profile_input_profile(profile)
        profile_requested = _string_set(input_profile.get("requested_extras"))
        profile_effective = _string_set(input_profile.get("effective_extras"))
        profile_required = _string_set(input_profile.get("required_inputs_satisfied"))
        profile_optional = _string_set(input_profile.get("optional_inputs_provided"))
        profile_conditional = _string_set(input_profile.get("conditional_inputs_satisfied"))
        profile_inputs = profile_required | profile_optional | profile_conditional

        extra_over = profile_requested.difference(requested)
        extra_under = requested.difference(profile_requested)
        effective_delta = profile_effective.symmetric_difference(effective)
        input_over = profile_inputs.difference(input_ids)
        input_under = input_ids.difference(profile_inputs)

        params_profile = _profile_params_profile(profile)
        cost_params = [
            str(item)
            for item in params_profile.get("cost_relevant_params", [])
            if isinstance(item, str) and item
        ]
        cost_values = params_profile.get("cost_relevant_values", {})
        if not isinstance(cost_values, dict):
            cost_values = {}
        param_diffs = sum(
            1 for path in cost_params if params_flat.get(path) != cost_values.get(path)
        )

        source_modes = input_profile.get("input_source_modes", {})
        if not isinstance(source_modes, dict):
            source_modes = {}
        source_diffs = sum(
            1
            for path, value in source_modes.items()
            if _value_at_path(params, str(path)) != value
        )

        valid_points = _runtime_points(profile)
        metadata = {
            "profile_id": str(profile.get("profile_id") or ""),
            "extra_over": sorted(extra_over),
            "extra_under": sorted(extra_under),
            "effective_delta": sorted(effective_delta),
            "input_over": sorted(input_over),
            "input_under": sorted(input_under),
            "param_difference_count": int(param_diffs),
            "source_mode_difference_count": int(source_diffs),
            "cost_relevant_params": cost_params,
            "cost_relevant_values": cost_values,
            "input_source_modes": source_modes,
        }
        score = (
            1 if not valid_points else 0,
            len(extra_over) * 3 + len(extra_under),
            len(input_over) * 3 + len(input_under),
            len(effective_delta),
            param_diffs + source_diffs,
            idx,
        )
        candidates.append((score, profile, metadata))

    if profile_matches == 0:
        return None, {}, [
            f"[{simulator_id}] cost.json has no profile for scenario profile={scenario_profile}."
        ]
    if not candidates:
        return None, {}, [
            f"[{simulator_id}] cost.json has no compatible benchmark profile."
        ]

    _score, selected, metadata = min(candidates, key=lambda item: item[0])
    warnings: list[str] = []
    approximate_bits = [
        metadata["extra_over"],
        metadata["extra_under"],
        metadata["input_over"],
        metadata["input_under"],
        metadata["effective_delta"],
    ]
    if any(approximate_bits) or metadata["param_difference_count"] or metadata["source_mode_difference_count"]:
        warnings.append(
            f"[{simulator_id}] selected approximate cost profile {metadata['profile_id']}."
        )
    return selected, metadata, warnings


def _nearest_point(
    *, points: list[dict[str, Any]], genes: int, cells: int, groups: int, threads: int, ram_gb: float
) -> dict[str, Any] | None:
    same_resource = [
        point
        for point in points
        if int(point.get("threads", -1)) == int(threads)
        and round(float(point.get("ram_gb", -1.0)), 3) == round(float(ram_gb), 3)
    ]
    candidates = same_resource or points
    if not candidates:
        return None

    def score(point: dict[str, Any]) -> float:
        pg = max(1, int(point.get("genes", 1) or 1))
        pc = max(1, int(point.get("cells", 1) or 1))
        pgroups = max(1, int(point.get("groups", 0) or 1))
        group_term = 0.0 if groups <= 0 else abs(math.log(max(1, groups) / pgroups))
        return abs(math.log(genes / pg)) + abs(math.log(cells / pc)) + group_term

    return min(candidates, key=score)


def _fallback_mode(
    *,
    simulator_id: str,
    run_id: str,
    spec: dict[str, Any],
    params: dict[str, Any],
    scenario_profile: str,
    max_cores: int,
    max_ram_gb: float,
    eta_source: str,
    warnings: list[str],
) -> SimulatorRunMode:
    genes, cells, groups, population_count, dimension_profile = _estimate_dimensions(
        params=params, selected_profile=None
    )
    extras_multiplier = (
        2.0
        if scenario_profile == "scrna_cell_specific"
        else (1.4 if scenario_profile == "scrna_grouped" else 1.0)
    )
    eta = max(60.0, 0.04 * genes * cells * extras_multiplier)
    resources = resolve_simulator_runtime_resources(
        simulator_id=simulator_id,
        simulator_spec=spec,
        raw_resources={"threads": 1},
    )
    return SimulatorRunMode(
        run_id=run_id,
        simulator_id=simulator_id,
        threads=int(resources["threads"]),
        ram_gb=round(max(1.0, min(float(max_ram_gb), 4.0)), 3),
        eta_seconds=round(float(eta), 3),
        eta_source=eta_source,
        eta_provenance={
            "eta_source": "fallback",
            "warnings": warnings,
            "features": _simulation_cost_features(
                params=params,
                scenario_profile=scenario_profile,
                genes=genes,
                cells=cells,
                groups=groups,
                population_count=population_count,
                dimension_profile=dimension_profile,
            ),
            "limits": {"max_cores": int(max_cores), "max_ram_gb": float(max_ram_gb)},
        },
    )


def _estimate_run_modes(
    *,
    run: dict[str, Any],
    spec: dict[str, Any],
    scenario_profile: str,
    requested_extras: list[str],
    effective_extras: list[str],
    input_ids: set[str],
    max_cores: int,
    max_ram_gb: float,
) -> tuple[list[SimulatorRunMode], list[str]]:
    simulator_id = str(run["simulator_id"])
    run_id = str(run["run_id"])
    params = dict(run.get("simulator_params", {}))
    cost_payload, load_warnings = _load_simulator_cost_payload(simulator_id)
    if cost_payload is None:
        return [
            _fallback_mode(
                simulator_id=simulator_id,
                run_id=run_id,
                spec=spec,
                params=params,
                scenario_profile=scenario_profile,
                max_cores=max_cores,
                max_ram_gb=max_ram_gb,
                eta_source="fallback_no_cost",
                warnings=load_warnings,
            )
        ], load_warnings

    selected_profile, profile_match, profile_warnings = _select_cost_profile(
        simulator_id=simulator_id,
        cost_payload=cost_payload,
        scenario_profile=scenario_profile,
        requested_extras=requested_extras,
        effective_extras=effective_extras,
        input_ids=input_ids,
        params=params,
    )
    warnings = [*load_warnings, *profile_warnings]
    if selected_profile is None:
        return [
            _fallback_mode(
                simulator_id=simulator_id,
                run_id=run_id,
                spec=spec,
                params=params,
                scenario_profile=scenario_profile,
                max_cores=max_cores,
                max_ram_gb=max_ram_gb,
                eta_source="fallback_no_matching_cost_profile",
                warnings=warnings,
            )
        ], warnings

    points = _runtime_points(selected_profile)
    genes, cells, groups, population_count, dimension_profile = _estimate_dimensions(
        params=params, selected_profile=selected_profile
    )
    cost_features = _simulation_cost_features(
        params=params,
        scenario_profile=scenario_profile,
        genes=genes,
        cells=cells,
        groups=groups,
        population_count=population_count,
        dimension_profile=dimension_profile,
    )
    candidate_resources = sorted(
        {
            (int(point["threads"]), round(float(point["ram_gb"]), 3))
            for point in points
            if int(point["threads"]) <= max_cores and float(point["ram_gb"]) <= max_ram_gb
        }
    )
    modes: list[SimulatorRunMode] = []
    for threads, ram_gb in candidate_resources:
        try:
            resolved_resources = resolve_simulator_runtime_resources(
                simulator_id=simulator_id,
                simulator_spec=spec,
                raw_resources={"threads": int(threads)},
            )
        except ValueError:
            continue
        nearest = _nearest_point(
            points=points,
            genes=genes,
            cells=cells,
            groups=groups,
            threads=threads,
            ram_gb=ram_gb,
        )
        if nearest is None:
            continue
        nearest_genes = max(1, int(nearest.get("genes", genes) or genes))
        nearest_cells = max(1, int(nearest.get("cells", cells) or cells))
        nearest_groups = max(1, int(nearest.get("groups", groups or 1) or 1))
        group_scale = 1.0 if groups <= 0 else math.sqrt(max(1, groups) / nearest_groups)
        size_scale = math.sqrt((genes / nearest_genes) * (cells / nearest_cells)) * group_scale
        p50 = float(nearest["seconds_p50"])
        p90 = float(nearest["seconds_p90"])
        robust_base = max(p90, 0.7 * p90 + 0.3 * p50)
        eta = max(0.1, robust_base * size_scale * _runtime_penalty(nearest))
        profile_id = str(selected_profile.get("profile_id") or "")
        provenance_warnings = list(warnings)
        if nearest.get("status") == "partial":
            provenance_warnings.append(
                "nearest benchmark point has partial success; ETA includes risk penalty"
            )
        modes.append(
            SimulatorRunMode(
                run_id=run_id,
                simulator_id=simulator_id,
                threads=int(resolved_resources["threads"]),
                ram_gb=round(float(ram_gb), 3),
                eta_seconds=round(float(eta), 3),
                eta_source="cost_profile",
                eta_provenance={
                    "eta_source": "cost_profile",
                    "cost_profile": {
                        "simulator_id": simulator_id,
                        "profile_id": profile_id,
                        "match": profile_match,
                        "dimension_profile": dimension_profile,
                        "features": cost_features,
                        "nearest_runtime_point": {
                            "genes": nearest_genes,
                            "cells": nearest_cells,
                            "groups": int(nearest.get("groups", 0) or 0),
                            "population_count": int(nearest.get("population_count", 0) or 0),
                            "threads": int(nearest.get("threads", threads)),
                            "ram_gb": round(float(nearest.get("ram_gb", ram_gb)), 3),
                            "status": str(nearest.get("status") or ""),
                            "seconds_p50": round(float(p50), 6),
                            "seconds_p90": round(float(p90), 6),
                            "ok_rate": nearest.get("ok_rate"),
                        },
                        "size_scale": round(float(size_scale), 6),
                        "risk_penalty": round(float(_runtime_penalty(nearest)), 6),
                        "warnings": provenance_warnings,
                    },
                },
            )
        )
    if not modes:
        warnings.append(
            f"[{simulator_id}] cost.json has no usable runtime point under resource limits."
        )
        return [
            _fallback_mode(
                simulator_id=simulator_id,
                run_id=run_id,
                spec=spec,
                params=params,
                scenario_profile=scenario_profile,
                max_cores=max_cores,
                max_ram_gb=max_ram_gb,
                eta_source="fallback_no_usable_runtime_point",
                warnings=warnings,
            )
        ], warnings
    return _prune_modes(modes)[:8], warnings


def _prune_modes(modes: list[SimulatorRunMode]) -> list[SimulatorRunMode]:
    kept: list[SimulatorRunMode] = []
    for mode in sorted(modes, key=lambda item: (item.eta_seconds, item.threads, item.ram_gb)):
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


def _task_items_for_modes(
    *, tasks: list[dict[str, Any]], modes_by_run_id: dict[str, SimulatorRunMode]
) -> list[SimulatorTaskItem]:
    items: list[SimulatorTaskItem] = []
    for task in tasks:
        mode = modes_by_run_id[str(task["run_id"])]
        items.append(
            SimulatorTaskItem(
                task_id=str(task["task_id"]),
                run_id=str(task["run_id"]),
                simulator_id=str(task["simulator_id"]),
                threads=mode.threads,
                ram_gb=mode.ram_gb,
                eta_seconds=mode.eta_seconds,
                eta_source=mode.eta_source,
                eta_provenance=mode.eta_provenance,
            )
        )
    return items


def _build_waves(
    *,
    items: list[SimulatorTaskItem],
    max_parallel_tasks: int,
    max_cores: int,
    max_ram_gb: float,
) -> tuple[list[SimulatorWave], float]:
    waves: list[SimulatorWave] = []
    for item in sorted(items, key=lambda task: task.eta_seconds, reverse=True):
        best_idx = None
        best_score = None
        for idx, wave in enumerate(waves):
            if len(wave.tasks) >= max_parallel_tasks:
                continue
            next_threads = wave.threads_used + item.threads
            next_ram = wave.ram_gb_used + item.ram_gb
            if next_threads > max_cores or next_ram > max_ram_gb:
                continue
            eta_increase = max(0.0, item.eta_seconds - wave.eta_seconds)
            residual = (max_cores - next_threads) + (max_ram_gb - next_ram)
            score = (eta_increase, residual)
            if best_score is None or score < best_score:
                best_score = score
                best_idx = idx
        if best_idx is None:
            waves.append(
                SimulatorWave(
                    index=len(waves) + 1,
                    threads_used=item.threads,
                    ram_gb_used=round(item.ram_gb, 3),
                    eta_seconds=item.eta_seconds,
                    tasks=[item],
                )
            )
        else:
            wave = waves[best_idx]
            waves[best_idx] = SimulatorWave(
                index=wave.index,
                threads_used=wave.threads_used + item.threads,
                ram_gb_used=round(wave.ram_gb_used + item.ram_gb, 3),
                eta_seconds=max(wave.eta_seconds, item.eta_seconds),
                tasks=wave.tasks + [item],
            )
    total = round(sum(wave.eta_seconds for wave in waves), 3)
    return waves, total


def _choose_modes_and_waves(
    *,
    run_ids: list[str],
    mode_options_by_run: dict[str, list[SimulatorRunMode]],
    tasks: list[dict[str, Any]],
    max_parallel_tasks: int,
    max_cores: int,
    max_ram_gb: float,
) -> tuple[dict[str, SimulatorRunMode], list[SimulatorWave], float]:
    option_lists = [mode_options_by_run[run_id] for run_id in run_ids]
    total_combinations = 1
    for options in option_lists:
        total_combinations *= max(1, len(options))
    if total_combinations > 4096:
        option_lists = [options[:4] for options in option_lists]

    best: tuple[float, int, float, dict[str, SimulatorRunMode], list[SimulatorWave]] | None = None
    for combo in itertools.product(*option_lists):
        selected = {mode.run_id: mode for mode in combo}
        items = _task_items_for_modes(tasks=tasks, modes_by_run_id=selected)
        waves, total_eta = _build_waves(
            items=items,
            max_parallel_tasks=max_parallel_tasks,
            max_cores=max_cores,
            max_ram_gb=max_ram_gb,
        )
        resource_sum = sum(item.threads + item.ram_gb for item in items)
        score = (total_eta, len(waves), resource_sum, selected, waves)
        if best is None or score[:3] < best[:3]:
            best = score
    if best is None:
        raise ValueError("No simulator run mode combination could be planned")
    return best[3], best[4], round(best[0], 3)


def _wave_payloads(waves: list[SimulatorWave]) -> tuple[list[dict[str, Any]], dict[str, tuple[float, float, int]]]:
    payloads: list[dict[str, Any]] = []
    task_windows: dict[str, tuple[float, float, int]] = {}
    cursor = 0.0
    for idx, wave in enumerate(waves, start=1):
        start = round(cursor, 3)
        end = round(cursor + wave.eta_seconds, 3)
        cursor = end
        tasks_payload = []
        for task in sorted(wave.tasks, key=lambda item: item.task_id):
            task_windows[task.task_id] = (start, round(start + task.eta_seconds, 3), idx)
            tasks_payload.append(
                {
                    "task_id": task.task_id,
                    "run_id": task.run_id,
                    "simulator_id": task.simulator_id,
                    "threads": task.threads,
                    "ram_gb": task.ram_gb,
                    "eta_seconds": task.eta_seconds,
                    "eta_source": task.eta_source,
                    "eta_start_seconds": start,
                    "eta_end_seconds": round(start + task.eta_seconds, 3),
                }
            )
        payloads.append(
            {
                "index": idx,
                "threads_used": wave.threads_used,
                "ram_gb_used": wave.ram_gb_used,
                "eta_seconds": round(wave.eta_seconds, 3),
                "eta_start_seconds": start,
                "eta_end_seconds": end,
                "tasks": tasks_payload,
            }
        )
    return payloads, task_windows


def apply_simulator_cost_plan(
    *,
    runs: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    scenario_profile: str,
    requested_extras: list[str],
    effective_extras: list[str],
    input_ids: set[str],
    max_parallel_tasks: int,
    max_cores: int,
    max_ram_gb: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not runs or not tasks:
        return runs, tasks, {
            "max_parallel_tasks": max_parallel_tasks,
            "max_cores": max_cores,
            "max_ram_gb": float(max_ram_gb),
            "eta_total_seconds": 0.0,
            "waves": [],
            "warnings": [],
        }

    mode_options_by_run: dict[str, list[SimulatorRunMode]] = {}
    warnings: list[str] = []
    run_ids = [str(run["run_id"]) for run in runs]
    for run in runs:
        simulator_id = str(run["simulator_id"])
        modes, run_warnings = _estimate_run_modes(
            run=run,
            spec=catalog[simulator_id],
            scenario_profile=scenario_profile,
            requested_extras=requested_extras,
            effective_extras=effective_extras,
            input_ids=input_ids,
            max_cores=max_cores,
            max_ram_gb=max_ram_gb,
        )
        mode_options_by_run[str(run["run_id"])] = modes
        warnings.extend(run_warnings)

    selected_modes, waves, total_eta = _choose_modes_and_waves(
        run_ids=run_ids,
        mode_options_by_run=mode_options_by_run,
        tasks=tasks,
        max_parallel_tasks=max_parallel_tasks,
        max_cores=max_cores,
        max_ram_gb=max_ram_gb,
    )
    wave_payloads, task_windows = _wave_payloads(waves)

    updated_runs: list[dict[str, Any]] = []
    for run in runs:
        mode = selected_modes[str(run["run_id"])]
        run_task_windows = [
            task_windows[str(task["task_id"])]
            for task in tasks
            if str(task["run_id"]) == str(run["run_id"])
        ]
        run_start = min((item[0] for item in run_task_windows), default=0.0)
        run_end = max((item[1] for item in run_task_windows), default=0.0)
        payload = dict(run)
        payload["runtime_resources"] = {"threads": mode.threads}
        payload["ram_gb"] = mode.ram_gb
        payload["eta_seconds"] = round(run_end - run_start, 3)
        payload["eta_source"] = mode.eta_source
        payload["eta_start_seconds"] = round(run_start, 3)
        payload["eta_end_seconds"] = round(run_end, 3)
        payload["eta_provenance"] = mode.eta_provenance
        updated_runs.append(payload)

    updated_tasks: list[dict[str, Any]] = []
    for task in tasks:
        mode = selected_modes[str(task["run_id"])]
        start, end, wave_idx = task_windows[str(task["task_id"])]
        payload = dict(task)
        payload["runtime_resources"] = {"threads": mode.threads}
        payload["ram_gb"] = mode.ram_gb
        payload["eta_seconds"] = mode.eta_seconds
        payload["eta_source"] = mode.eta_source
        payload["eta_start_seconds"] = round(start, 3)
        payload["eta_end_seconds"] = round(end, 3)
        payload["eta_wave"] = wave_idx
        payload["eta_provenance"] = mode.eta_provenance
        updated_tasks.append(payload)

    execution = {
        "max_parallel_tasks": int(max_parallel_tasks),
        "max_cores": int(max_cores),
        "max_ram_gb": round(float(max_ram_gb), 3),
        "eta_total_seconds": round(total_eta, 3),
        "waves": wave_payloads,
        "warnings": sorted(dict.fromkeys(warnings)),
    }
    return updated_runs, updated_tasks, execution
