from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts"))
from shared.cost_resume import measurement_context
from shared.temporal_cost_coverage import temporal_calibration_coverage
from shared.benchmark_profiles import resolve_benchmark_profiles
from shared.benchmark_inputs import BenchmarkInputProfile, temporal_timepoint


def panel_fixture(legacy=False):
    """Synthetic unit-test records only; these are never calibration evidence."""
    recipe = json.loads(
        (ROOT / "wrappers/inference_tools/cost_profiles/dyngenie3.json").read_text()
    )
    if legacy:
        recipe["profiles"] = recipe["profiles"][:16]
        for raw in recipe["profiles"][9:]:
            raw["sizes"] = ["100x16"]
        recipe["calibration_design"].update(
            {
                "kind": "independent_temporal_default_grid_with_branch_centers",
                "resources": {"threads": [1, 2], "ram_gb": [4, 8], "strata_count": 4},
                "total_points_per_resource_stratum": 34,
                "planned_calibration_runs": 408,
            }
        )
    spec = json.loads(
        (
            ROOT / "andrea/catalog_inference_tools/tools/dyngenie3/toolspec.json"
        ).read_text()
    )
    defaults = {name: value["default"] for name, value in spec["params"].items()}
    profiles = []
    for raw in recipe["profiles"]:
        config = {
            "input_profile": {
                "temporal": recipe["temporal"],
                "temporal_fixture": raw["temporal_fixture"],
            },
            "params_profile": {
                "resolved_params": {**defaults, **raw.get("param_overrides", {})}
            },
            "execution_profile": {"mode": "global"},
            "provenance": {"image_id": "sha256:" + "a" * 64},
        }
        points = []
        for size in raw["sizes"]:
            genes, columns = map(int, size.split("x"))
            for threads in recipe["calibration_design"]["resources"]["threads"]:
                for ram in recipe["calibration_design"]["resources"]["ram_gb"]:
                    hashes = {
                        name: "b" * 64
                        for name in (
                            "expression.tsv",
                            "params.json",
                            "execution.json",
                            "extra/timepoints.tsv",
                            "extra/replicates.tsv",
                        )
                    }
                    if "tf_list" in raw.get("optional_inputs", []):
                        hashes["extra/tf_list.txt"] = "b" * 64
                    if "steady_state_expression" in raw.get("optional_inputs", []):
                        hashes["extra/steady_state_expression.tsv"] = "b" * 64
                    geometry = raw["temporal_fixture"]
                    observations = [
                        {
                            "status": "ok",
                            "seconds": seconds,
                            "genes": genes,
                            "columns": columns,
                            "threads": threads,
                            "ram_gb": ram,
                            "run_id": f"unit-test-{raw['id']}-{genes}-{threads}-{ram}-{index}",
                            "input_sha256": hashes,
                            "measurement_provenance": config["provenance"],
                            "dimensions": {
                                "genes": genes,
                                "columns": columns,
                                "timepoints_per_series": geometry[
                                    "timepoints_per_series"
                                ],
                                "independent_series": geometry["series_count"],
                                "steady_state_samples": geometry.get(
                                    "steady_state_samples", 0
                                ),
                            },
                        }
                        for index, seconds in enumerate((10.0, 11.0, 12.0), 1)
                    ]
                    points.append(
                        {
                            "genes": genes,
                            "columns": columns,
                            "threads": threads,
                            "ram_gb": ram,
                            "status": "ok",
                            "repeats_ok": 3,
                            "seconds_p50": 11.0,
                            "measurement_evidence": {
                                "input_sha256": hashes,
                                "measurement_context": measurement_context(config),
                                "observations": observations,
                            },
                        }
                    )
        profiles.append(
            {
                "profile_id": raw["id"],
                "benchmark_config": config,
                "runtime_points": points,
            }
        )
    return recipe, {"profiles": profiles}


class TemporalCostCoverageTests(unittest.TestCase):
    def test_complete_finite_panel_covers_declared_family_axes_in_all_resources(
        self,
    ):
        recipe, instance = panel_fixture()
        report = temporal_calibration_coverage(instance, recipe)
        self.assertTrue(report["calibration_adequate_for_planned_range"])
        self.assertEqual(report["required_successful_repetitions"], 1944)
        self.assertEqual(report["retained_successful_repetitions"], 1944)
        self.assertEqual(report["registered_points_per_resource"], 81)
        self.assertEqual(
            [
                row["successful_points"]
                for row in report["aggregate_default_temporal_domain"]
            ],
            [27] * 8,
        )
        branch = report["profiles"][-1]
        self.assertTrue(
            all(row["scope"] == "gene_axis_only" for row in branch["resources"])
        )
        self.assertEqual(len(report["branch_families"]), 13)
        self.assertTrue(all(row["covered"] for row in report["branch_families"]))
        self.assertFalse(report["coverage_is_prediction_validation"])

    def test_original_four_strata_center_panel_remains_supported(self):
        recipe, instance = panel_fixture(legacy=True)
        report = temporal_calibration_coverage(instance, recipe)
        self.assertTrue(report["calibration_adequate_for_planned_range"])
        self.assertEqual(report["required_successful_repetitions"], 408)
        self.assertEqual(report["branch_families"], [])

    def test_branch_gene_growth_does_not_certify_missing_four_or_eight_cpu_points(self):
        recipe, instance = panel_fixture()
        branch = next(
            p
            for p in instance["profiles"]
            if p["profile_id"] == "global_et_center_spec_defaults"
        )
        branch["runtime_points"] = [
            p for p in branch["runtime_points"] if p["threads"] < 4
        ]
        report = temporal_calibration_coverage(instance, recipe)
        self.assertFalse(report["calibration_adequate_for_planned_range"])
        family = next(
            f for f in report["branch_families"] if f["id"] == "et_gene_time_slices"
        )
        self.assertFalse(family["covered"])
        self.assertTrue(
            all(r["covered"] for r in family["resources"] if r["threads"] < 4)
        )

    def test_family_recipe_cannot_omit_a_cross_or_relabel_vector_gene_axis(self):
        for mutation in (
            "missing_cross",
            "vector_axis",
            "overlapping_family",
            "run_budget",
            "duplicate_resource",
            "family_parameter",
        ):
            with self.subTest(mutation=mutation):
                recipe, instance = panel_fixture()
                if mutation == "missing_cross":
                    recipe["profiles"][9]["sizes"] = ["100x16", "200x16"]
                elif mutation == "vector_axis":
                    family = next(
                        f
                        for f in recipe["calibration_design"]["families"]
                        if f["id"] == "vector_alpha_temporal_points"
                    )
                    family["coordinate_axes"]["genes"] = [50, 100, 200]
                elif mutation == "overlapping_family":
                    recipe["calibration_design"]["families"][1]["profile_ids"].append(
                        recipe["profiles"][0]["id"]
                    )
                elif mutation == "run_budget":
                    recipe["calibration_design"]["planned_calibration_runs"] = 408
                elif mutation == "duplicate_resource":
                    recipe["calibration_design"]["resources"]["threads"].append(8)
                else:
                    recipe["profiles"][9]["param_overrides"]["tree_method"] = "RF"
                with self.assertRaises(ValueError):
                    temporal_calibration_coverage(instance, recipe)

    def test_measured_branch_parameters_and_optional_inputs_cannot_be_relabelled(self):
        for mutation in (
            "parameters",
            "steady_hash",
            "duplicate_point",
            "unplanned_profile",
        ):
            with self.subTest(mutation=mutation):
                recipe, instance = panel_fixture()
                if mutation == "parameters":
                    branch = next(
                        p
                        for p in instance["profiles"]
                        if p["profile_id"]
                        == "global_quality_scores_center_spec_defaults"
                    )
                    branch["benchmark_config"]["params_profile"]["resolved_params"][
                        "compute_quality_scores"
                    ] = False
                elif mutation == "steady_hash":
                    branch = next(
                        p
                        for p in instance["profiles"]
                        if p["profile_id"] == "global_steady_state_center_spec_defaults"
                    )
                    branch["runtime_points"][0]["measurement_evidence"][
                        "input_sha256"
                    ].pop("extra/steady_state_expression.tsv")
                elif mutation == "duplicate_point":
                    instance["profiles"][0]["runtime_points"].append(
                        copy.deepcopy(instance["profiles"][0]["runtime_points"][0])
                    )
                else:
                    extra = copy.deepcopy(instance["profiles"][0])
                    extra["profile_id"] = "unplanned"
                    instance["profiles"].append(extra)
                report = temporal_calibration_coverage(instance, recipe)
                self.assertFalse(report["calibration_adequate_for_planned_range"])

    def test_actual_recipe_resolves_sampled_parameters_and_native_temporal_axes(self):
        profiles = resolve_benchmark_profiles(
            tool_id="dyngenie3",
            catalog_tools_root=ROOT / "andrea/catalog_inference_tools/tools",
        )
        by_id = {row.profile_id: row for row in profiles}
        self.assertEqual(len(profiles), 29)
        self.assertEqual(sum(len(row.sizes) for row in profiles), 81)
        self.assertEqual(by_id["global_rf_k1_t8_s2_spec_defaults"].params["K"], 1)
        self.assertEqual(by_id["global_rf_kall_t8_s2_spec_defaults"].params["K"], "all")
        self.assertEqual(
            by_id["global_rf_ntrees100_t8_s2_spec_defaults"].params["ntrees"], 100
        )
        quality = by_id["global_quality_scores_center_spec_defaults"]
        self.assertTrue(quality.params["compute_quality_scores"])
        self.assertEqual(quality.params["ntrees"], 1000)
        for profile in profiles:
            for size in profile.sizes:
                genes = int(size.split("x")[0])
                self.assertNotIn(genes, {75, 250})
                if isinstance(profile.params["alpha"], list):
                    self.assertEqual(genes, 100)
                    self.assertEqual(len(profile.params["alpha"]), genes)
        irregular = by_id["global_rf_irregular_t8_s2_spec_defaults"]
        self.assertEqual(
            irregular.input_profile["temporal_fixture"]["sampling"], "irregular"
        )
        # The existing input generator already implements the declared schedule.
        profile = BenchmarkInputProfile.from_cost_input_profile(
            irregular.input_profile, seed=12345
        )
        times = [temporal_timepoint(index, profile) for index in range(8)]
        intervals = [right - left for left, right in zip(times, times[1:])]
        self.assertTrue(all(value > 0 for value in intervals))
        self.assertGreater(len(set(intervals)), 1)

    def test_missing_resource_or_temporal_cross_is_incomplete(self):
        recipe, instance = panel_fixture()
        instance["profiles"][0]["runtime_points"] = [
            point
            for point in instance["profiles"][0]["runtime_points"]
            if point["threads"] != 2
        ]
        report = temporal_calibration_coverage(instance, recipe)
        self.assertFalse(report["calibration_adequate_for_planned_range"])
        self.assertTrue(
            any(
                row["missing_coordinates"]
                for row in report["aggregate_default_temporal_domain"]
            )
        )

    def test_equal_columns_cannot_relabel_temporal_shape(self):
        recipe, instance = panel_fixture()
        instance["profiles"][2]["benchmark_config"]["input_profile"][
            "temporal_fixture"
        ] = copy.deepcopy(
            instance["profiles"][4]["benchmark_config"]["input_profile"][
                "temporal_fixture"
            ]
        )
        report = temporal_calibration_coverage(instance, recipe)
        self.assertFalse(report["calibration_adequate_for_planned_range"])
        self.assertTrue(report["profiles"][2]["errors"])

    def test_reused_execution_ids_or_changed_raw_inputs_are_rejected(self):
        for mutation in ("run_id", "input_sha256", "dimensions"):
            with self.subTest(mutation=mutation):
                recipe, instance = panel_fixture()
                observations = instance["profiles"][0]["runtime_points"][0][
                    "measurement_evidence"
                ]["observations"]
                if mutation == "run_id":
                    observations[1]["run_id"] = observations[0]["run_id"]
                elif mutation == "input_sha256":
                    observations[1]["input_sha256"] = {"expression.tsv": "c" * 64}
                else:
                    observations[1]["dimensions"]["independent_series"] = 999
                report = temporal_calibration_coverage(instance, recipe)
                self.assertFalse(report["calibration_adequate_for_planned_range"])


if __name__ == "__main__":
    unittest.main()
