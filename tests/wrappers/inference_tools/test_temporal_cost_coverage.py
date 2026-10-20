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


def panel_fixture():
    """Synthetic unit-test records only; these are never calibration evidence."""
    recipe = json.loads(
        (ROOT / "wrappers/inference_tools/cost_profiles/dyngenie3.json").read_text()
    )
    spec = json.loads(
        (
            ROOT / "wrappers/inference_tools/tools/dyngenie3/toolspec.draft.json"
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
            for threads in (1, 2):
                for ram in (4, 8):
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
    def test_complete_finite_panel_covers_all_resources_without_branch_growth_claim(
        self,
    ):
        recipe, instance = panel_fixture()
        report = temporal_calibration_coverage(instance, recipe)
        self.assertTrue(report["calibration_adequate_for_planned_range"])
        self.assertEqual(report["required_successful_repetitions"], 408)
        self.assertEqual(report["retained_successful_repetitions"], 408)
        self.assertEqual(
            [
                row["successful_points"]
                for row in report["aggregate_default_temporal_domain"]
            ],
            [27] * 4,
        )
        branch = report["profiles"][-1]
        self.assertTrue(
            all(
                row["scope"] == "registered_point_only_no_growth_claim"
                for row in branch["resources"]
            )
        )
        self.assertFalse(report["coverage_is_prediction_validation"])

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
