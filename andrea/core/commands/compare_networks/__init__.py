"""Public API for inferred network comparison."""

from .comparison import compare_networks, export_edge_scores_csv_from_sqlite

__all__ = ["compare_networks", "export_edge_scores_csv_from_sqlite"]
