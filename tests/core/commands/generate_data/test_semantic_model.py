from __future__ import annotations

import unittest

from andrea.core.commands.generate_data.catalog import get_semantic_capability
from andrea.core.commands.generate_data.semantic import (
    DataAxes,
    SIMULATOR_TRUTH_CONTEXT_FAMILIES,
    SIMULATOR_TRUTH_CONTEXT_PREFIXES,
    TruthRequirements,
    parse_data_axes,
    parse_semantic_scenario,
    parse_truth_requirements,
    primary_truth_context,
    required_extras_for_request,
    required_truth_context_prefixes,
    semantic_key,
)
from andrea.core.shared.network_context import (
    NETWORK_CONTEXT_FAMILIES,
    NETWORK_CONTEXT_PREFIXES,
    network_context_family,
    network_context_label,
    network_context_sort_key,
)


class GenerateDataSemanticModelTests(unittest.TestCase):
    def test_simulator_truth_contexts_are_normalized_network_context_subset(self) -> None:
        self.assertEqual(SIMULATOR_TRUTH_CONTEXT_FAMILIES, ("global", "group", "column"))
        self.assertTrue(set(SIMULATOR_TRUTH_CONTEXT_FAMILIES).issubset(NETWORK_CONTEXT_FAMILIES))
        self.assertEqual(SIMULATOR_TRUTH_CONTEXT_PREFIXES["global"], "global")
        self.assertEqual(SIMULATOR_TRUTH_CONTEXT_PREFIXES["group"], NETWORK_CONTEXT_PREFIXES["group"])
        self.assertEqual(SIMULATOR_TRUTH_CONTEXT_PREFIXES["column"], NETWORK_CONTEXT_PREFIXES["column"])
        self.assertNotIn("timepoint", SIMULATOR_TRUTH_CONTEXT_FAMILIES)

    def test_parse_bulk_perturbational_axes(self) -> None:
        axes = parse_data_axes(
            {
                "measurement": "rna_expression",
                "resolution": "bulk",
                "column_kind": "perturbations",
                "experimental_design": "perturbational",
            }
        )

        self.assertEqual(axes.resolution, "bulk")
        self.assertEqual(axes.column_kind, "perturbations")

    def test_parse_single_cell_grouped_truth_requirements(self) -> None:
        truth = parse_truth_requirements({"contexts": ["group", "global"]})
        axes = parse_data_axes(
            {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "trajectory",
            }
        )

        self.assertEqual(truth.contexts, ("global", "group"))
        self.assertEqual(required_extras_for_request(axes, truth), frozenset({"groups"}))
        self.assertEqual(required_truth_context_prefixes(truth), ("global", "group:"))
        self.assertEqual(primary_truth_context(truth), "group")

    def test_column_truth_uses_generic_column_context(self) -> None:
        truth = parse_truth_requirements({"contexts": ["global", "group", "column"]})

        self.assertEqual(
            required_truth_context_prefixes(truth),
            ("global", "group:", "column:"),
        )
        self.assertEqual(primary_truth_context(truth), "column")
        self.assertEqual(network_context_family("column:C1"), "column")
        self.assertEqual(network_context_label("column:C1"), "column C1")
        self.assertEqual(network_context_family("cell:C1"), "other")
        self.assertLess(
            network_context_sort_key("column:C1"),
            network_context_sort_key("cell:C1"),
        )

    def test_truth_requirements_must_include_global(self) -> None:
        with self.assertRaisesRegex(ValueError, "must include global"):
            parse_truth_requirements({"contexts": ["group"]})

    def test_data_axes_reject_cell_columns_outside_single_cell(self) -> None:
        with self.assertRaisesRegex(ValueError, "column_kind=cells"):
            parse_data_axes(
                {
                    "measurement": "rna_expression",
                    "resolution": "bulk",
                    "column_kind": "cells",
                    "experimental_design": "observational",
                }
            )

    def test_single_cell_time_series_can_use_cell_columns(self) -> None:
        axes = parse_data_axes(
            {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "time_series",
            }
        )

        self.assertEqual(axes.column_kind, "cells")
        self.assertEqual(axes.experimental_design, "time_series")

    def test_required_extras_for_request_combines_truth_and_data_axes(self) -> None:
        grouped_truth = parse_truth_requirements({"contexts": ["global", "group"]})
        time_series_axes = parse_data_axes(
            {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "time_series",
            }
        )
        perturbational_axes = parse_data_axes(
            {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "perturbational",
            }
        )
        spatial_axes = parse_data_axes(
            {
                "measurement": "rna_expression",
                "resolution": "spatial",
                "column_kind": "spots",
                "experimental_design": "differentiation",
            }
        )

        self.assertEqual(
            required_extras_for_request(time_series_axes, grouped_truth),
            frozenset({"groups", "timepoints"}),
        )
        self.assertEqual(
            required_extras_for_request(perturbational_axes, parse_truth_requirements({"contexts": ["global"]})),
            frozenset({"perturbation_design", "interventions"}),
        )
        self.assertEqual(
            required_extras_for_request(spatial_axes, parse_truth_requirements({"contexts": ["global"]})),
            frozenset({"spatial_coordinates"}),
        )

    def test_parse_semantic_scenario_and_stable_key(self) -> None:
        scenario = parse_semantic_scenario(
            {
                "data_axes": {
                    "measurement": "rna_expression",
                    "resolution": "single_cell",
                    "column_kind": "cells",
                    "experimental_design": "differentiation",
                },
                "truth_requirements": {
                    "contexts": ["global", "group", "column"],
                },
            }
        )

        self.assertEqual(scenario.data_axes.resolution, "single_cell")
        self.assertEqual(scenario.truth.contexts, ("global", "group", "column"))
        self.assertEqual(
            semantic_key(data_axes=scenario.data_axes, truth=scenario.truth),
            "rna_expression|single_cell|cells|differentiation|global,group,column",
        )

    def test_to_json_round_trip_shape(self) -> None:
        axes = DataAxes(
            measurement="rna_expression",
            resolution="single_cell",
            column_kind="cells",
            experimental_design="trajectory",
        )
        truth = TruthRequirements(contexts=("global", "group"))

        self.assertEqual(
            axes.to_json(),
            {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "trajectory",
            },
        )
        self.assertEqual(truth.to_json(), {"contexts": ["global", "group"]})

    def test_catalog_capability_lookup_uses_axes_and_truth_requirements(self) -> None:
        capability = {
            "data_axes": {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "trajectory",
            },
            "truth_requirements": {"contexts": ["global", "group"]},
            "truth_outputs": [
                {"context": "global", "status": "native"},
                {"context": "group", "status": "derivable"},
            ],
        }
        spec = {"capabilities": [capability]}

        self.assertIs(
            get_semantic_capability(
                spec,
                data_axes=capability["data_axes"],
                truth_requirements=capability["truth_requirements"],
            ),
            capability,
        )
        self.assertIsNone(
            get_semantic_capability(
                spec,
                data_axes={
                    **capability["data_axes"],
                    "experimental_design": "steady_state",
                },
                truth_requirements=capability["truth_requirements"],
            )
        )


if __name__ == "__main__":
    unittest.main()
