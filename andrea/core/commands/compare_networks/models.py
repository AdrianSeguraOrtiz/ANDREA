"""Shared models and constants for network comparison."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

MERGED_NETWORK_REQUIRED_COLUMNS = [
    "source",
    "target",
    "score",
    "sign",
    "evidence",
    "context",
    "tool_id",
]
COMPARISON_LEVELS = ["topology", "directed", "signed"]
VALID_SIGNS = {"+", "-"}
NETWORK_INDEX_COLUMNS = [
    "network_id",
    "source_id",
    "run_id",
    "tool_id",
    "catalog_tool_id",
    "context",
    "level",
    "n_genes",
    "n_edges",
]
EDGE_SCORES_COLUMNS = [
    "network_id",
    "source_id",
    "run_id",
    "tool_id",
    "catalog_tool_id",
    "context",
    "level",
    "edge_key",
    "source",
    "target",
    "sign",
    "score",
]
DISTANCE_COLUMNS = [
    "source_id",
    "context",
    "level",
    "distance_metric",
    "network_a",
    "network_b",
    "distance",
    "n_common_genes",
    "n_edges_considered",
    "status",
    "warning",
]
COORDINATE_COLUMNS = [
    "source_id",
    "context",
    "level",
    "distance_metric",
    "network_id",
    "x",
    "y",
    "status",
    "warning",
]
EVALUATION_METRIC_COLUMNS = [
    "auroc",
    "aupr",
    "f1_at_truth_count",
    "epr_at_truth_count",
]
DISTANCE_METRICS = [
    "weighted_jaccard_distance",
    "rank_overlap_distance_at_truth_count",
]
VIEW_ASSETS_PACKAGE = "andrea.core.commands.compare_networks.view_assets"


@dataclass(frozen=True)
class ComparisonSource:
    source_id: str
    label: str
    run_report_path: Path
    evaluation_report_path: Optional[Path]
    request_run_report: str
    request_evaluation_report: Optional[str]


@dataclass(frozen=True)
class NetworkRow:
    source: str
    target: str
    score: float
    sign: str
    evidence: str
    context: str
    tool_id: str


@dataclass(frozen=True)
class SourceData:
    source: ComparisonSource
    run_report: dict[str, Any]
    evaluation_report: Optional[dict[str, Any]]
    normalized_network_path: Path
    rows: list[NetworkRow]


@dataclass(frozen=True)
class EvaluationMetric:
    source_id: str
    tool_id: str
    context: str
    level: str
    status: str
    n_truth_edges: Optional[int]
    values: dict[str, Optional[float]]


@dataclass(frozen=True)
class NetworkInstance:
    network_id: str
    source_id: str
    run_id: str
    tool_id: str
    catalog_tool_id: str
    context: str
    level: str
    scores: dict[tuple[str, ...], float]
    nodes: set[str]
