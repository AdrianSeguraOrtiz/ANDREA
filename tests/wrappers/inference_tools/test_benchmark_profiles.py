from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "wrappers" / "inference_tools" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from shared.benchmark_inputs import (  # noqa: E402
    BenchmarkInputProfile,
    BenchmarkInputSize,
    write_benchmark_io_dir,
)
from shared.benchmark_profiles import resolve_benchmark_profiles  # noqa: E402

CATALOG_TOOLS_ROOT = REPO_ROOT / "andrea" / "catalog_inference_tools" / "tools"
PARAM_OVERRIDES_DIR = REPO_ROOT / "wrappers" / "inference_tools" / "param_overrides"
COST_PROFILES_DIR = REPO_ROOT / "wrappers" / "inference_tools" / "cost_profiles"


class BenchmarkProfileResolverTest(unittest.TestCase):
    def test_scmtni_profiles_satisfy_param_conditionals(self) -> None:
        profiles = resolve_benchmark_profiles(
            tool_id="scmtni",
            catalog_tools_root=CATALOG_TOOLS_ROOT,
            param_overrides_dir=PARAM_OVERRIDES_DIR,
        )

        self.assertEqual(
            [profile.profile_id for profile in profiles],
            [
                "group_native_groups_2_q2_with_prior",
                "group_native_groups_1_q0_independent",
            ],
        )
        q2_profile = profiles[0]
        self.assertEqual(q2_profile.execution_profile["mode"], "group_native")
        self.assertEqual(q2_profile.execution_profile["group_count"], 2)
        self.assertEqual(
            set(q2_profile.input_profile["required_inputs_satisfied"]),
            {"groups", "tf_list"},
        )
        self.assertEqual(
            set(q2_profile.input_profile["conditional_inputs_satisfied"]),
            {"lineage_tree", "prior_grn_by_group"},
        )
        self.assertEqual(
            set(q2_profile.input_profile["extras_provided"]),
            {"groups", "lineage_tree", "prior_grn_by_group", "tf_list"},
        )
        q0_profile = profiles[1]
        self.assertEqual(q0_profile.params["q"], 0)
        self.assertIs(q0_profile.params["indep"], True)
        self.assertEqual(q0_profile.input_profile["conditional_inputs_satisfied"], [])
        self.assertEqual(
            set(q0_profile.input_profile["extras_provided"]),
            {"groups", "tf_list"},
        )

    def test_repository_profiles_capture_tool_specific_input_contracts(self) -> None:
        expected = {
            ("genie3", "global_default"): {
                "mode": "global",
                "required": set(),
                "optional": set(),
                "conditional": set(),
            },
            ("genie3", "group_emulated_groups_2_tf_list"): {
                "mode": "group_emulated",
                "required": set(),
                "optional": {"tf_list"},
                "conditional": {"groups"},
            },
            ("inferelator3", "global_prior_sparse"): {
                "mode": "global",
                "required": {"prior_grn", "tf_list"},
                "optional": set(),
                "conditional": set(),
            },
            ("inferelator3", "group_native_groups_2_prior_sparse"): {
                "mode": "group_native",
                "required": {"prior_grn", "tf_list"},
                "optional": set(),
                "conditional": {"groups"},
            },
            ("scmtni", "group_native_groups_2_q2_with_prior"): {
                "mode": "group_native",
                "required": {"groups", "tf_list"},
                "optional": set(),
                "conditional": {"lineage_tree", "prior_grn_by_group"},
            },
            ("simic", "group_native_phenotypes_2_default"): {
                "mode": "group_native",
                "required": {"cell_phenotypes", "tf_list"},
                "optional": set(),
                "conditional": set(),
            },
            ("miniex3", "group_native_motif_off_grnboost_background"): {
                "mode": "group_native",
                "required": {
                    "cluster_identities",
                    "cluster_markers",
                    "groups",
                    "tf_list",
                },
                "optional": {"enrichment_background", "grnboost_network"},
                "conditional": set(),
            },
            ("infercsn", "group_emulated_groups_2_no_pseudotime"): {
                "mode": "group_emulated",
                "required": {"groups"},
                "optional": {"tf_list"},
                "conditional": set(),
            },
            ("infercsn", "group_emulated_groups_2_entropy_pseudotime"): {
                "mode": "group_emulated",
                "required": {"groups"},
                "optional": {"tf_list"},
                "conditional": {"pseudotime"},
            },
        }
        resolved = {}
        for tool_id in sorted({tool_id for tool_id, _profile_id in expected}):
            for profile in resolve_benchmark_profiles(
                tool_id=tool_id,
                catalog_tools_root=CATALOG_TOOLS_ROOT,
                param_overrides_dir=PARAM_OVERRIDES_DIR,
                cost_profiles_dir=COST_PROFILES_DIR,
            ):
                resolved[(tool_id, profile.profile_id)] = profile

        for key, expectation in expected.items():
            with self.subTest(tool_id=key[0], profile_id=key[1]):
                profile = resolved[key]
                self.assertEqual(profile.execution_profile["mode"], expectation["mode"])
                self.assertEqual(
                    set(profile.input_profile["required_inputs_satisfied"]),
                    expectation["required"],
                )
                self.assertEqual(
                    set(profile.input_profile["optional_inputs_provided"]),
                    expectation["optional"],
                )
                self.assertEqual(
                    set(profile.input_profile["conditional_inputs_satisfied"]),
                    expectation["conditional"],
                )
                self.assertEqual(
                    set(profile.input_profile["extras_provided"]),
                    expectation["required"]
                    | expectation["optional"]
                    | expectation["conditional"],
                )

    def test_configured_profiles_resolve_execution_inputs_and_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "genie3.json"
            config_path.write_text(
                json.dumps(
                    {
                        "cost_relevant_params": ["limit"],
                        "profiles": [
                            {
                                "id": "global_no_tf",
                                "execution": {"mode": "global"},
                                "optional_inputs": [],
                            },
                            {
                                "id": "group_emulated_tf_limit_25",
                                "execution": {"mode": "group_emulated"},
                                "group_count": 3,
                                "optional_inputs": ["tf_list"],
                                "param_overrides": {"limit": 25},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            profiles = resolve_benchmark_profiles(
                tool_id="genie3",
                catalog_tools_root=CATALOG_TOOLS_ROOT,
                param_overrides_dir=PARAM_OVERRIDES_DIR,
                cost_profiles_dir=Path(tmp),
            )

        self.assertEqual(
            [profile.profile_id for profile in profiles],
            ["global_no_tf", "group_emulated_tf_limit_25"],
        )
        self.assertEqual(profiles[0].input_profile["extras_provided"], [])
        self.assertEqual(profiles[0].params_profile["cost_relevant_params"], ["limit"])
        self.assertEqual(
            profiles[0].params_profile["cost_relevant_values"],
            {"limit": 50},
        )
        grouped = profiles[1]
        self.assertEqual(grouped.execution, {"mode": "group_emulated"})
        self.assertEqual(
            grouped.execution_profile["physical_task_policy"], "andrea_group_emulated"
        )
        self.assertEqual(grouped.execution_profile["group_count"], 3)
        self.assertEqual(grouped.params["limit"], 25)
        self.assertEqual(grouped.params_profile["cost_relevant_params"], ["limit"])
        self.assertEqual(
            grouped.params_profile["cost_relevant_values"],
            {"limit": 25},
        )
        self.assertEqual(
            set(grouped.input_profile["extras_provided"]),
            {"groups", "tf_list"},
        )
        self.assertEqual(
            set(grouped.input_profile["conditional_inputs_satisfied"]),
            {"groups"},
        )
        self.assertEqual(
            set(grouped.input_profile["optional_inputs_provided"]),
            {"tf_list"},
        )

    def test_cell_native_and_group_aggregated_cost_profiles_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            catalog_root = base / "catalog"
            tool_root = catalog_root / "fakecell"
            tool_root.mkdir(parents=True)
            tool_root.joinpath("toolspec.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "fakecell",
                        "execution_capabilities": [
                            "cell_native",
                            "group_aggregated",
                        ],
                        "params": {},
                        "extra_inputs": {
                            "required": [],
                            "optional": [
                                {
                                    "input": "tf_list",
                                    "usage": "Restricts candidate regulators.",
                                },
                                {
                                    "input": "chromatin_accessibility_matrix",
                                    "usage": "Provides paired accessibility features.",
                                },
                            ],
                            "conditional_required": [
                                {
                                    "input": "groups",
                                    "execution": "mode",
                                    "op": "eq",
                                    "value": "group_aggregated",
                                    "usage": "Maps cell-native outputs to groups.",
                                }
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            cost_dir = base / "cost_profiles"
            cost_dir.mkdir()
            cost_dir.joinpath("fakecell.json").write_text(
                json.dumps(
                    {
                        "profiles": [
                            {
                                "id": "cell_native_atac",
                                "execution": {"mode": "cell_native"},
                                "optional_inputs": [
                                    "tf_list",
                                    "chromatin_accessibility_matrix",
                                ],
                            },
                            {
                                "id": "group_aggregated_groups_2",
                                "execution": {"mode": "group_aggregated"},
                                "group_count": 2,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            profiles = resolve_benchmark_profiles(
                tool_id="fakecell",
                catalog_tools_root=catalog_root,
                param_overrides_dir=base / "param_overrides",
                cost_profiles_dir=cost_dir,
            )

        cell_native = profiles[0]
        self.assertEqual(cell_native.execution_profile["mode"], "cell_native")
        self.assertEqual(
            cell_native.execution_profile["physical_task_policy"], "cell_native"
        )
        self.assertEqual(cell_native.execution_profile["group_count"], 0)
        self.assertEqual(cell_native.execution_profile["aggregation_step"], "none")
        self.assertEqual(cell_native.input_profile["output_density_class"], "dense")
        self.assertTrue(cell_native.input_profile["has_tf_list"])
        self.assertTrue(
            cell_native.input_profile["has_chromatin_accessibility_matrix"]
        )

        group_aggregated = profiles[1]
        self.assertEqual(group_aggregated.execution_profile["mode"], "group_aggregated")
        self.assertEqual(
            group_aggregated.execution_profile["physical_task_policy"],
            "andrea_group_aggregated",
        )
        self.assertEqual(group_aggregated.execution_profile["group_count"], 2)
        self.assertEqual(
            group_aggregated.execution_profile["aggregation_step"], "cell_to_group"
        )
        self.assertEqual(
            group_aggregated.input_profile["conditional_inputs_satisfied"], ["groups"]
        )

    def test_profile_rejects_non_optional_input_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "clr.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": [
                            {
                                "id": "bad_optional",
                                "execution": {"mode": "global"},
                                "optional_inputs": ["tf_list"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "not declared as optional"):
                resolve_benchmark_profiles(
                    tool_id="clr",
                    catalog_tools_root=CATALOG_TOOLS_ROOT,
                    param_overrides_dir=PARAM_OVERRIDES_DIR,
                    cost_profiles_dir=Path(tmp),
                )

    def test_repository_profile_configs_resolve_and_generate_inputs(self) -> None:
        configured_tool_ids = sorted(
            path.stem for path in COST_PROFILES_DIR.glob("*.json")
        )
        self.assertTrue(configured_tool_ids)

        for tool_id in configured_tool_ids:
            with self.subTest(tool_id=tool_id):
                profiles = resolve_benchmark_profiles(
                    tool_id=tool_id,
                    catalog_tools_root=CATALOG_TOOLS_ROOT,
                    param_overrides_dir=PARAM_OVERRIDES_DIR,
                    cost_profiles_dir=COST_PROFILES_DIR,
                )
                self.assertTrue(profiles)
                for profile in profiles:
                    with self.subTest(tool_id=tool_id, profile=profile.profile_id):
                        with tempfile.TemporaryDirectory() as tmp:
                            group_count = int(
                                profile.input_profile.get("group_count") or 0
                            )
                            columns = max(16, group_count * 4)
                            bundle = write_benchmark_io_dir(
                                Path(tmp),
                                BenchmarkInputSize(genes=24, columns=columns),
                                BenchmarkInputProfile.from_cost_input_profile(
                                    profile.input_profile,
                                    seed=123,
                                ),
                            )
                        self.assertEqual(bundle.expression_path.name, "expression.tsv")
                        self.assertEqual(
                            set(bundle.extras),
                            set(profile.input_profile["extras_provided"]),
                        )


if __name__ == "__main__":
    unittest.main()
