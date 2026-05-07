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
from andrea.core.commands.compare_networks.tables import (
    build_evaluation_metrics,
    build_network_tables,
    evaluation_metric_report_rows,
    metrics_available,
    source_report_item,
)
from andrea.core.commands.compare_networks.utils import (
    create_comparison_dir,
    write_csv,
)
from andrea.core.commands.compare_networks.view import write_comparison_view
from andrea.core.shared.json_io import write_json
from andrea.core.shared.paths import report_path

# Kept as a private compatibility alias for existing tests and callers that imported
# the helper before the module split.
_build_distance_coordinates = build_distance_coordinates


def compare_networks(
    *,
    request_path: Path,
    output_dir: Path,
    comparison_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Build a comparison package for normalized inferred networks."""
    request_path = request_path.resolve()
    output_root = output_dir.resolve()
    created_at = datetime.now(timezone.utc)
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

    source_data = [load_source_data(source) for source in sources]
    request, source_data = freeze_comparison_inputs(
        request=request,
        source_data=source_data,
        comparison_dir=comparison_dir,
    )
    network_index, edge_scores, network_instances = build_network_tables(source_data)
    evaluation_metrics, evaluation_warnings = build_evaluation_metrics(source_data)
    distances, distance_warnings = build_distance_rows(
        network_instances=network_instances,
        evaluation_metrics=evaluation_metrics,
    )
    distance_coordinates, coordinate_warnings = build_distance_coordinates(distances)

    request_copy_path = comparison_dir / "comparison-request.json"
    network_index_path = comparison_dir / "network_index.csv"
    edge_scores_path = comparison_dir / "edge_scores.csv"
    distances_path = comparison_dir / "distances.csv"
    distance_coordinates_path = comparison_dir / "distance_coordinates.csv"
    report_path_json = comparison_dir / "comparison_report.json"
    view_html_path = comparison_dir / "comparison_view.html"

    write_json(request_copy_path, request)
    write_csv(network_index_path, network_index, fieldnames=NETWORK_INDEX_COLUMNS)
    write_csv(edge_scores_path, edge_scores, fieldnames=EDGE_SCORES_COLUMNS)
    write_csv(distances_path, distances, fieldnames=DISTANCE_COLUMNS)
    write_csv(
        distance_coordinates_path,
        distance_coordinates,
        fieldnames=COORDINATE_COLUMNS,
    )
    warnings = evaluation_warnings + distance_warnings + coordinate_warnings

    report = {
        "schema_version": "1.0",
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request": {
            "id": request["id"],
            "source_count": len(sources),
        },
        "sources": [source_report_item(item) for item in source_data],
        "contexts": sorted({row["context"] for row in network_index}),
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
            "comparison_view": report_path(view_html_path, base_dir=output_root),
        },
        "summary": {
            "sources": len(sources),
            "network_instances": len(network_index),
            "edge_score_rows": len(edge_scores),
            "distance_rows": len(distances),
            "coordinate_rows": len(distance_coordinates),
            "warnings": len(warnings),
        },
        "network_index": network_index,
        "evaluation_metrics": evaluation_metric_report_rows(evaluation_metrics),
        "distances": distances,
        "distance_coordinates": distance_coordinates,
        "edge_scores": edge_scores,
    }
    write_json(report_path_json, report)
    write_comparison_view(view_html_path, report)
    return report
