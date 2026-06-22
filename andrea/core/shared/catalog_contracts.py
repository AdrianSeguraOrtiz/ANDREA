"""Shared catalog contract constants."""

from __future__ import annotations

TAXONOMIC_GROUPS = frozenset(
    {
        "animal",
        "plant",
        "fungi",
        "bacteria",
        "archaea",
        "protist",
        "viral",
        "synthetic",
        "unknown",
    }
)

SIMULATION_EXTRA_IDS = frozenset(
    {
        "groups",
        "column_descriptors",
        "column_phenotypes",
        "cluster_identities",
        "cell_cell_interactions",
        "chromatin_accessibility",
        "chromatin_regions",
        "enrichment_background",
        "interventions",
        "lineage_tree",
        "perturbation_design",
        "pseudotime",
        "prior_grn",
        "prior_grn_by_group",
        "replicates",
        "spatial_coordinates",
        "tf_list",
        "timepoints",
    }
)
