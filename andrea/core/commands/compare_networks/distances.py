"""Distance calculation and distance-map coordinates."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

from andrea.core.commands.compare_networks.models import (
    DISTANCE_METRICS,
    EvaluationMetric,
    NetworkInstance,
)
from andrea.core.commands.compare_networks.tables import has_usable_truth_count
from andrea.core.commands.compare_networks.utils import (
    format_float,
    optional_float,
    unique_preserve_order,
)


def build_distance_rows(
    *,
    network_instances: list[NetworkInstance],
    evaluation_metrics: dict[tuple[str, str, str, str], EvaluationMetric],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    grouped: dict[tuple[str, str, str], list[NetworkInstance]] = defaultdict(list)
    for instance in network_instances:
        grouped[(instance.source_id, instance.context, instance.level)].append(instance)

    for (source_id, context, level), instances in sorted(grouped.items()):
        instances = sorted(instances, key=lambda item: item.network_id)
        if len(instances) < 2:
            continue
        common_nodes = common_nodes_for_instances(instances)
        if not common_nodes:
            warning = f"[{source_id}] no common genes for context={context!r}, level={level!r}"
            warnings.append(warning)
        truth_count, truth_warning = truth_count_for_group(
            source_id=source_id,
            context=context,
            level=level,
            evaluation_metrics=evaluation_metrics,
        )
        if truth_warning:
            warnings.append(truth_warning)

        filtered = {
            instance.network_id: filter_scores_by_nodes(instance.scores, common_nodes)
            for instance in instances
        }
        for left_idx, network_a in enumerate(instances):
            for network_b in instances[left_idx + 1 :]:
                scores_a = filtered[network_a.network_id]
                scores_b = filtered[network_b.network_id]
                rows.append(
                    weighted_jaccard_row(
                        source_id=source_id,
                        context=context,
                        level=level,
                        network_a=network_a.network_id,
                        network_b=network_b.network_id,
                        scores_a=scores_a,
                        scores_b=scores_b,
                        n_common_genes=len(common_nodes),
                    )
                )
                rows.append(
                    rank_overlap_row(
                        source_id=source_id,
                        context=context,
                        level=level,
                        network_a=network_a.network_id,
                        network_b=network_b.network_id,
                        scores_a=scores_a,
                        scores_b=scores_b,
                        n_common_genes=len(common_nodes),
                        truth_count=truth_count,
                    )
                )
    return rows, unique_preserve_order(warnings)


def build_distance_coordinates(
    distance_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in distance_rows:
        if row.get("status") != "ok":
            continue
        distance = optional_float(row.get("distance"))
        if distance is None:
            continue
        grouped[
            (
                str(row.get("source_id", "")),
                str(row.get("context", "")),
                str(row.get("level", "")),
                str(row.get("distance_metric", "")),
            )
        ].append(row)

    for (source_id, context, level, metric), group_rows in sorted(grouped.items()):
        pair_distances: dict[tuple[str, str], float] = {}
        network_ids: set[str] = set()
        for row in group_rows:
            network_a = str(row.get("network_a", ""))
            network_b = str(row.get("network_b", ""))
            distance = optional_float(row.get("distance"))
            if not network_a or not network_b or distance is None:
                continue
            network_ids.update([network_a, network_b])
            pair_distances[network_pair_key(network_a, network_b)] = distance

        ordered_networks = sorted(network_ids)
        expected_pairs = len(ordered_networks) * (len(ordered_networks) - 1) // 2
        if len(ordered_networks) < 2:
            continue
        if len(pair_distances) < expected_pairs:
            warning = (
                f"[{source_id}] incomplete distance matrix for context={context!r}, "
                f"level={level!r}, distance_metric={metric!r}; coordinates not available"
            )
            warnings.append(warning)
            for network_id in ordered_networks:
                rows.append(
                    coordinate_row(
                        source_id=source_id,
                        context=context,
                        level=level,
                        metric=metric,
                        network_id=network_id,
                        x=None,
                        y=None,
                        status="not_available",
                        warning=warning,
                    )
                )
            continue

        matrix = [[0.0 for _col in ordered_networks] for _row in ordered_networks]
        invalid_warning: Optional[str] = None
        for left_idx, network_a in enumerate(ordered_networks):
            for right_idx in range(left_idx + 1, len(ordered_networks)):
                network_b = ordered_networks[right_idx]
                distance = pair_distances.get(network_pair_key(network_a, network_b))
                if distance is None or distance < 0.0 or not math.isfinite(distance):
                    invalid_warning = (
                        f"[{source_id}] invalid distance matrix for context={context!r}, "
                        f"level={level!r}, distance_metric={metric!r}; coordinates not available"
                    )
                    break
                matrix[left_idx][right_idx] = distance
                matrix[right_idx][left_idx] = distance
            if invalid_warning:
                break
        if invalid_warning:
            warnings.append(invalid_warning)
            for network_id in ordered_networks:
                rows.append(
                    coordinate_row(
                        source_id=source_id,
                        context=context,
                        level=level,
                        metric=metric,
                        network_id=network_id,
                        x=None,
                        y=None,
                        status="not_available",
                        warning=invalid_warning,
                    )
                )
            continue

        coordinates = classical_mds_coordinates(matrix)
        for network_id, (x, y) in zip(ordered_networks, coordinates):
            rows.append(
                coordinate_row(
                    source_id=source_id,
                    context=context,
                    level=level,
                    metric=metric,
                    network_id=network_id,
                    x=x,
                    y=y,
                    status="ok",
                    warning="",
                )
            )
    return rows, unique_preserve_order(warnings)


def classical_mds_coordinates(matrix: list[list[float]]) -> list[tuple[float, float]]:
    n = len(matrix)
    if n == 0:
        return []
    if n == 1:
        return [(0.0, 0.0)]
    if n == 2:
        distance = matrix[0][1]
        return [(-distance / 2.0, 0.0), (distance / 2.0, 0.0)]

    squared = [[matrix[row][col] ** 2 for col in range(n)] for row in range(n)]
    row_means = [sum(row) / n for row in squared]
    col_means = [sum(squared[row][col] for row in range(n)) / n for col in range(n)]
    grand_mean = sum(row_means) / n
    centered = [
        [
            -0.5 * (squared[row][col] - row_means[row] - col_means[col] + grand_mean)
            for col in range(n)
        ]
        for row in range(n)
    ]

    eigenvalues, eigenvectors = jacobi_eigendecomposition(centered)
    axes: list[tuple[int, float]] = []
    for eigen_idx in sorted(range(n), key=lambda idx: eigenvalues[idx], reverse=True):
        eigenvalue = eigenvalues[eigen_idx]
        if eigenvalue <= 1e-10:
            continue
        axes.append((eigen_idx, math.sqrt(eigenvalue)))
        if len(axes) == 2:
            break

    coordinates: list[tuple[float, float]] = []
    for point_idx in range(n):
        x = eigenvectors[point_idx][axes[0][0]] * axes[0][1] if len(axes) >= 1 else 0.0
        y = eigenvectors[point_idx][axes[1][0]] * axes[1][1] if len(axes) >= 2 else 0.0
        coordinates.append((x, y))
    return coordinates


def jacobi_eigendecomposition(
    matrix: list[list[float]],
) -> tuple[list[float], list[list[float]]]:
    n = len(matrix)
    a = [row[:] for row in matrix]
    eigenvectors = [
        [1.0 if row == col else 0.0 for col in range(n)] for row in range(n)
    ]
    if n <= 1:
        return ([a[0][0]] if n == 1 else []), eigenvectors

    tolerance = 1e-12
    max_iterations = max(50, 100 * n * n)
    for _iteration in range(max_iterations):
        pivot_row = 0
        pivot_col = 1
        max_off_diagonal = abs(a[pivot_row][pivot_col])
        for row in range(n):
            for col in range(row + 1, n):
                value = abs(a[row][col])
                if value > max_off_diagonal:
                    max_off_diagonal = value
                    pivot_row = row
                    pivot_col = col
        if max_off_diagonal < tolerance:
            break

        app = a[pivot_row][pivot_row]
        aqq = a[pivot_col][pivot_col]
        apq = a[pivot_row][pivot_col]
        if abs(apq) < tolerance:
            continue
        tau = (aqq - app) / (2.0 * apq)
        sign = 1.0 if tau >= 0.0 else -1.0
        tangent = sign / (abs(tau) + math.sqrt(1.0 + tau * tau))
        cosine = 1.0 / math.sqrt(1.0 + tangent * tangent)
        sine = tangent * cosine

        for idx in range(n):
            if idx in {pivot_row, pivot_col}:
                continue
            aip = a[idx][pivot_row]
            aiq = a[idx][pivot_col]
            a[idx][pivot_row] = a[pivot_row][idx] = (cosine * aip) - (sine * aiq)
            a[idx][pivot_col] = a[pivot_col][idx] = (sine * aip) + (cosine * aiq)

        a[pivot_row][pivot_row] = (
            (cosine * cosine * app) - (2.0 * sine * cosine * apq) + (sine * sine * aqq)
        )
        a[pivot_col][pivot_col] = (
            (sine * sine * app) + (2.0 * sine * cosine * apq) + (cosine * cosine * aqq)
        )
        a[pivot_row][pivot_col] = 0.0
        a[pivot_col][pivot_row] = 0.0

        for idx in range(n):
            vip = eigenvectors[idx][pivot_row]
            viq = eigenvectors[idx][pivot_col]
            eigenvectors[idx][pivot_row] = (cosine * vip) - (sine * viq)
            eigenvectors[idx][pivot_col] = (sine * vip) + (cosine * viq)

    return [a[idx][idx] for idx in range(n)], eigenvectors


def coordinate_row(
    *,
    source_id: str,
    context: str,
    level: str,
    metric: str,
    network_id: str,
    x: Optional[float],
    y: Optional[float],
    status: str,
    warning: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "context": context,
        "level": level,
        "distance_metric": metric,
        "network_id": network_id,
        "x": format_float(x) if x is not None else "",
        "y": format_float(y) if y is not None else "",
        "status": status,
        "warning": warning,
    }


def network_pair_key(network_a: str, network_b: str) -> tuple[str, str]:
    return (network_a, network_b) if network_a <= network_b else (network_b, network_a)


def weighted_jaccard_row(
    *,
    source_id: str,
    context: str,
    level: str,
    network_a: str,
    network_b: str,
    scores_a: dict[tuple[str, ...], float],
    scores_b: dict[tuple[str, ...], float],
    n_common_genes: int,
) -> dict[str, Any]:
    keys = set(scores_a) | set(scores_b)
    if not keys:
        warning = "weighted jaccard is not available because both networks have no comparable edges"
        return distance_row(
            source_id=source_id,
            context=context,
            level=level,
            metric="weighted_jaccard_distance",
            network_a=network_a,
            network_b=network_b,
            distance=None,
            n_common_genes=n_common_genes,
            n_edges_considered=0,
            status="not_available",
            warning=warning,
        )
    numerator = sum(min(scores_a.get(key, 0.0), scores_b.get(key, 0.0)) for key in keys)
    denominator = sum(
        max(scores_a.get(key, 0.0), scores_b.get(key, 0.0)) for key in keys
    )
    if denominator <= 0.0:
        warning = "weighted jaccard is not available because comparable edge weights sum to zero"
        return distance_row(
            source_id=source_id,
            context=context,
            level=level,
            metric="weighted_jaccard_distance",
            network_a=network_a,
            network_b=network_b,
            distance=None,
            n_common_genes=n_common_genes,
            n_edges_considered=len(keys),
            status="not_available",
            warning=warning,
        )
    return distance_row(
        source_id=source_id,
        context=context,
        level=level,
        metric="weighted_jaccard_distance",
        network_a=network_a,
        network_b=network_b,
        distance=1.0 - (numerator / denominator),
        n_common_genes=n_common_genes,
        n_edges_considered=len(keys),
        status="ok",
        warning="",
    )


def rank_overlap_row(
    *,
    source_id: str,
    context: str,
    level: str,
    network_a: str,
    network_b: str,
    scores_a: dict[tuple[str, ...], float],
    scores_b: dict[tuple[str, ...], float],
    n_common_genes: int,
    truth_count: Optional[int],
) -> dict[str, Any]:
    if truth_count is None:
        warning = "rank overlap is not available because truth_count is unavailable"
        return distance_row(
            source_id=source_id,
            context=context,
            level=level,
            metric="rank_overlap_distance_at_truth_count",
            network_a=network_a,
            network_b=network_b,
            distance=None,
            n_common_genes=n_common_genes,
            n_edges_considered=0,
            status="not_available",
            warning=warning,
        )
    if truth_count <= 0:
        warning = "rank overlap is not available because truth_count is zero"
        return distance_row(
            source_id=source_id,
            context=context,
            level=level,
            metric="rank_overlap_distance_at_truth_count",
            network_a=network_a,
            network_b=network_b,
            distance=None,
            n_common_genes=n_common_genes,
            n_edges_considered=0,
            status="not_available",
            warning=warning,
        )
    top_a = top_k_edges(scores_a, truth_count)
    top_b = top_k_edges(scores_b, truth_count)
    overlap = len(top_a & top_b) / truth_count
    return distance_row(
        source_id=source_id,
        context=context,
        level=level,
        metric="rank_overlap_distance_at_truth_count",
        network_a=network_a,
        network_b=network_b,
        distance=1.0 - overlap,
        n_common_genes=n_common_genes,
        n_edges_considered=truth_count,
        status="ok",
        warning="",
    )


def distance_row(
    *,
    source_id: str,
    context: str,
    level: str,
    metric: str,
    network_a: str,
    network_b: str,
    distance: Optional[float],
    n_common_genes: int,
    n_edges_considered: int,
    status: str,
    warning: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "context": context,
        "level": level,
        "distance_metric": metric,
        "network_a": network_a,
        "network_b": network_b,
        "distance": format_float(distance) if distance is not None else "",
        "n_common_genes": n_common_genes,
        "n_edges_considered": n_edges_considered,
        "status": status,
        "warning": warning,
    }


def common_nodes_for_instances(instances: list[NetworkInstance]) -> set[str]:
    if not instances:
        return set()
    common = set(instances[0].nodes)
    for instance in instances[1:]:
        common &= instance.nodes
    return common


def filter_scores_by_nodes(
    scores: dict[tuple[str, ...], float],
    nodes: set[str],
) -> dict[tuple[str, ...], float]:
    if not nodes:
        return {}
    return {
        key: score
        for key, score in scores.items()
        if key[0] in nodes and key[1] in nodes
    }


def top_k_edges(scores: dict[tuple[str, ...], float], k: int) -> set[tuple[str, ...]]:
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return {key for key, _score in ranked[:k]}


def truth_count_for_group(
    *,
    source_id: str,
    context: str,
    level: str,
    evaluation_metrics: dict[tuple[str, str, str, str], EvaluationMetric],
) -> tuple[Optional[int], Optional[str]]:
    values = sorted(
        {
            metric.n_truth_edges
            for metric in evaluation_metrics.values()
            if metric.source_id == source_id
            and metric.context == context
            and metric.level == level
            and has_usable_truth_count(metric)
        }
    )
    if not values:
        return None, None
    if len(values) > 1:
        return values[0], (
            f"[{source_id}] discordant truth_count values for context={context!r}, "
            f"level={level!r}; using minimum value {values[0]}"
        )
    return values[0], None


def distances_available(rows: list[dict[str, Any]]) -> list[str]:
    available = {
        str(row["distance_metric"])
        for row in rows
        if row.get("status") == "ok" and str(row.get("distance_metric", "")).strip()
    }
    ordered = [metric for metric in DISTANCE_METRICS if metric in available]
    ordered.extend(sorted(available - set(ordered)))
    return ordered
