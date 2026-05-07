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
        "cell_phenotypes",
        "cluster_identities",
        "enrichment_background",
        "lineage_tree",
        "pseudotime",
        "prior_grn",
        "tf_list",
        "prior_grn_by_group",
        "group_networks",
    }
)
