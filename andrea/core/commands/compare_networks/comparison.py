"""Compare inferred networks across one or more infer-network runs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from andrea.core.commands.compare_networks.distances import (
    build_distance_coordinates,
    build_distance_rows,
    distances_available,
)
from andrea.core.commands.compare_networks.loading import (
    freeze_comparison_inputs,
    load_source_data,
)
from andrea.core.commands.compare_networks.models import (
    COMPARISON_LEVELS,
    COORDINATE_COLUMNS,
    DISTANCE_COLUMNS,
    EDGE_SCORES_COLUMNS,
    NETWORK_INDEX_COLUMNS,
)
from andrea.core.commands.compare_networks.request import (
    load_comparison_request,
    parse_sources,
)
from andrea.core.commands.compare_networks.store import (
    export_edge_scores_csv_from_sqlite,
    write_comparison_store,
)
from andrea.core.commands.compare_networks.tables import (
    build_evaluation_metrics,
    build_network_tables,
    edge_score_row_count,
    evaluation_metric_report_rows,
    iter_edge_score_rows,
    metrics_available,
    source_report_item,
)
from andrea.core.commands.compare_networks.utils import (
    create_comparison_dir,
    write_csv,
)
from andrea.core.commands.compare_networks.view import write_comparison_view
from andrea.core.shared.json_io import write_json
from andrea.core.shared.network_context import (
    network_context_family,
    network_context_sort_key,
)
from andrea.core.shared.paths import report_path
from andrea.core.shared.runtime_profile import ProgressCallback, RuntimeProfile

_LARGE_CELL_CONTEXT_WARNING_THRESHOLD = 250


def compare_networks(
    *,
    request_path: Path,
    output_dir: Path,
    comparison_dir: Optional[Path] = None,
    progress_callback: Optional[ProgressCallback] = None,
    write_edge_scores_csv: bool = True,
) -> dict[str, Any]:
    """Build a comparison package for normalized inferred networks."""
    runtime_profile = RuntimeProfile(progress_callback)
    request_path = request_path.resolve()
    output_root = output_dir.resolve()
    created_at = datetime.now(timezone.utc)
    with runtime_profile.stage(
        "loading_request",
        label="Loading request",
        detail="Reading comparison request and source definitions.",
    ):
        request = load_comparison_request(request_path)
        sources = parse_sources(request=request, request_path=request_path)
        if comparison_dir is None:
            comparison_dir = create_comparison_dir(
                output_root=output_root,
                request_id=str(request["id"]),
                created_at=created_at,
            )
        else:
            output_root.mkdir(parents=True, exist_ok=True)
            comparison_dir = comparison_dir.resolve()
            comparison_dir.mkdir(parents=True, exist_ok=True)
            if not comparison_dir.is_dir():
                raise ValueError(f"comparison_dir is not a directory: {comparison_dir}")

    with runtime_profile.stage(
        "loading_source_networks",
        label="Loading source networks",
        detail="Reading normalized network CSVs for each comparison source.",
    ):
        source_data = [load_source_data(source) for source in sources]

    with runtime_profile.stage(
        "freezing_inputs",
        label="Freezing inputs",
        detail="Copying validated source reports and networks into the comparison package.",
    ):
        request, source_data = freeze_comparison_inputs(
            request=request,
            source_data=source_data,
            comparison_dir=comparison_dir,
        )
    with runtime_profile.stage(
        "building_network_tables",
        label="Building network tables",
        detail="Building network index and internal edge-score maps.",
    ):
        network_index, network_instances = build_network_tables(source_data)
        edge_score_rows = edge_score_row_count(network_instances)
        evaluation_metrics, evaluation_warnings = build_evaluation_metrics(source_data)

    with runtime_profile.stage(
        "computing_distances",
        label="Computing distances",
        detail="Computing pairwise network distances.",
    ):
        distances, distance_warnings = build_distance_rows(
            network_instances=network_instances,
            evaluation_metrics=evaluation_metrics,
        )

    with runtime_profile.stage(
        "computing_coordinates",
        label="Computing coordinates",
        detail="Computing distance-map coordinates.",
    ):
        distance_coordinates, coordinate_warnings = build_distance_coordinates(distances)
        contexts = sorted(
            {row["context"] for row in network_index},
            key=network_context_sort_key,
        )
        context_counts_by_family = _context_counts_by_family(contexts)

    request_copy_path = comparison_dir / "comparison-request.json"
    network_index_path = comparison_dir / "network_index.csv"
    edge_scores_path = comparison_dir / "edge_scores.csv"
    distances_path = comparison_dir / "distances.csv"
    distance_coordinates_path = comparison_dir / "distance_coordinates.csv"
    sqlite_path = comparison_dir / "comparison.sqlite"
    report_path_json = comparison_dir / "comparison_report.json"
    view_html_path = comparison_dir / "comparison_view.html"
    evaluation_metric_rows = evaluation_metric_report_rows(evaluation_metrics)

    with runtime_profile.stage(
        "writing_request",
        label="Writing request snapshot",
        detail="Writing comparison-request.json.",
    ):
        write_json(request_copy_path, request)
    with runtime_profile.stage(
        "writing_network_index_csv",
        label="Writing network index",
        detail="Writing network_index.csv.",
    ):
        write_csv(network_index_path, network_index, fieldnames=NETWORK_INDEX_COLUMNS)
    if write_edge_scores_csv:
        with runtime_profile.stage(
            "writing_edge_scores_csv",
            label="Writing edge-score CSV",
            detail="Writing full edge_scores.csv artifact.",
        ):
            write_csv(
                edge_scores_path,
                iter_edge_score_rows(network_instances),
                fieldnames=EDGE_SCORES_COLUMNS,
            )
    with runtime_profile.stage(
        "writing_distances_csv",
        label="Writing distances",
        detail="Writing distances.csv.",
    ):
        write_csv(distances_path, distances, fieldnames=DISTANCE_COLUMNS)
    with runtime_profile.stage(
        "writing_distance_coordinates_csv",
        label="Writing distance coordinates",
        detail="Writing distance_coordinates.csv.",
    ):
        write_csv(
            distance_coordinates_path,
            distance_coordinates,
            fieldnames=COORDINATE_COLUMNS,
        )
    with runtime_profile.stage(
        "writing_comparison_sqlite",
        label="Writing comparison SQLite",
        detail="Writing comparison.sqlite query store.",
    ):
        write_comparison_store(
            sqlite_path,
            network_index=network_index,
            edge_scores=iter_edge_score_rows(network_instances),
            distances=distances,
            distance_coordinates=distance_coordinates,
            evaluation_metrics=evaluation_metric_rows,
        )
    warnings = (
        evaluation_warnings
        + distance_warnings
        + coordinate_warnings
        + _context_scale_warnings(context_counts_by_family)
    )

    report = {
        "schema_version": "1.0",
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request": {
            "id": request["id"],
            "source_count": len(sources),
        },
        "sources": [source_report_item(item) for item in source_data],
        "contexts": contexts,
        "context_counts_by_family": context_counts_by_family,
        "levels": list(COMPARISON_LEVELS),
        "metrics_available": metrics_available(evaluation_metrics),
        "distances_available": distances_available(distances),
        "warnings": warnings,
        "outputs": {
            "output_root": ".",
            "comparison_dir": report_path(comparison_dir, base_dir=output_root),
            "comparison_request": report_path(request_copy_path, base_dir=output_root),
            "comparison_report": report_path(report_path_json, base_dir=output_root),
            "network_index_csv": report_path(network_index_path, base_dir=output_root),
            "edge_scores_csv": report_path(edge_scores_path, base_dir=output_root),
            "distances_csv": report_path(distances_path, base_dir=output_root),
            "distance_coordinates_csv": report_path(
                distance_coordinates_path,
                base_dir=output_root,
            ),
            "comparison_sqlite": report_path(sqlite_path, base_dir=output_root),
            "comparison_view": report_path(view_html_path, base_dir=output_root),
        },
        "summary": {
            "sources": len(sources),
            "network_instances": len(network_index),
            "edge_score_rows": edge_score_rows,
            "distance_rows": len(distances),
            "coordinate_rows": len(distance_coordinates),
            "warnings": len(warnings),
        },
        "runtime_profile": runtime_profile.timings(),
        "network_index": network_index,
        "evaluation_metrics": evaluation_metric_rows,
        "distances": distances,
        "distance_coordinates": distance_coordinates,
    }
    with runtime_profile.stage(
        "writing_report",
        label="Writing report",
        detail="Writing comparison_report.json and static comparison_view.html.",
    ):
        write_comparison_view(view_html_path, report)
    report["runtime_profile"] = runtime_profile.timings()
    write_json(report_path_json, report)
    return report


def _context_counts_by_family(contexts: list[str]) -> dict[str, int]:
    counts = {
        "global": 0,
        "group": 0,
        "cell": 0,
        "other": 0,
    }
    for context in contexts:
        counts[network_context_family(context)] += 1
    return counts


def _context_scale_warnings(context_counts_by_family: dict[str, int]) -> list[str]:
    cell_count = context_counts_by_family.get("cell", 0)
    if cell_count <= _LARGE_CELL_CONTEXT_WARNING_THRESHOLD:
        return []
    return [
        "comparison contains "
        f"{cell_count} cell contexts; distance-map views expose context filtering "
        "to avoid rendering every cell context at once"
    ]
