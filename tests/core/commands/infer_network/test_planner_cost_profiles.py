from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from andrea.core.commands.infer_network.commons.planner import (
    _estimate_tool_mode_options,
)
from andrea.core.commands.infer_network.commons.shared import DatasetContext


def _runtime_point(seconds: float = 2.0, *, threads: int = 1) -> dict[str, Any]:
    return {
        "genes": 10,
        "columns": 5,
        "threads": threads,
        "ram_gb": 1.0,
        "status": "ok",
        "repeats_total": 1,
        "repeats_ok": 1,
        "repeats_failed": 0,
        "ok_rate": 1.0,
        "failure_breakdown": {"oom": 0, "timeout": 0, "error": 0},
        "seconds_p50": seconds,
        "seconds_p90": seconds,
    }


def _value_at_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _profile(
    profile_id: str,
    *,
    mode: str = "global",
    extras: list[str] | None = None,
    required: list[str] | None = None,
    optional: list[str] | None = None,
    conditional: list[str] | None = None,
    group_count: int = 0,
    seconds: float = 2.0,
    resolved_params: dict[str, Any] | None = None,
    cost_relevant_params: list[str] | None = None,
    cost_relevant_values: dict[str, Any] | None = None,
    runtime_points: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    extras = extras or []
    resolved_params = resolved_params or {"limit": 50}
    cost_relevant_params = cost_relevant_params or []
    cost_relevant_values = cost_relevant_values or {
        path: _value_at_path(resolved_params, path) for path in cost_relevant_params
    }
    return {
        "profile_id": profile_id,
        "benchmark_config": {
            "execution_profile": {
                "mode": mode,
                "physical_task_policy": {
                    "global": "single",
                    "group_native": "native_grouped",
                    "group_emulated": "andrea_group_emulated",
                }.get(mode, "single"),
                "group_count": group_count,
            },
            "input_profile": {
                "column_kind": "samples",
                "expression_profile": "synthetic_benchmark",
                "extras_provided": extras,
                "required_inputs_satisfied": required or [],
                "optional_inputs_provided": optional or [],
                "conditional_inputs_satisfied": conditional or [],
                "tf_count_policy": "max(3, genes/5)" if "tf_list" in extras else None,
                "prior_density": None,
                "group_count": group_count,
                "notes": [],
            },
            "params_profile": {
                "source": "toolspec_defaults",
                "override_file": None,
                "resolved_params": resolved_params,
                "cost_relevant_params": cost_relevant_params,
                "cost_relevant_values": cost_relevant_values,
            },
        },
        "runtime_points": runtime_points or [_runtime_point(seconds)],
    }


class PlannerCostProfileSelectionTest(unittest.TestCase):
    def _dataset(self, tmp: Path, *, extras: dict[str, Path | None]) -> DatasetContext:
        expression = tmp / "expression.tsv"
        expression.write_text("gene\tS1\tS2\nG1\t1\t2\n", encoding="utf-8")
        return DatasetContext(
            dataset_id="toy",
            column_kind="samples",
            expression_profile="mixed",
            taxonomic_group="animal",
            ncbi_taxon_id=9606,
            genes=10,
            columns=5,
            expression_matrix_path=expression,
            extras=extras,
        )

    def _toolspec(self) -> dict[str, Any]:
        return {
            "id": "genie3",
            "docker_image": "example/genie3:latest",
            "runtime_resources": {
                "threading": {
                    "supported": True,
                    "default_threads": 2,
                    "max_threads": 4,
                    "upstream_mapping": "Wrapper maps --threads to upstream n_jobs.",
                }
            },
            "extra_inputs": {
                "required": [],
                "optional": [{"input": "tf_list", "usage": "restrict TFs"}],
                "conditional_required": [
                    {
                        "input": "groups",
                        "execution": "mode",
                        "op": "eq",
                        "value": "group_emulated",
                        "usage": "split by groups",
                    }
                ],
            },
        }

    def test_fallback_without_cost_uses_toolspec_default_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            modes, warnings = _estimate_tool_mode_options(
                tool_id="genie3_01",
                run_id="genie3_01",
                toolspec=self._toolspec(),
                cost_profile=None,
                execution_mode="global",
                resolved_params={"limit": 50},
                extras_present=set(),
                logical_group_count=0,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={}),
                max_cores=8,
                max_ram_gb=4.0,
                output_dir="tools/genie3_01",
            )

        self.assertEqual(warnings, [])
        self.assertEqual(modes[0].eta_source, "fallback_no_cost")
        self.assertEqual(modes[0].threads, 2)

    def test_cost_points_above_toolspec_max_threads_are_ignored(self) -> None:
        cost_payload = {
            "profiles": [
                _profile(
                    "global_default",
                    runtime_points=[
                        _runtime_point(seconds=1.0, threads=8),
                        _runtime_point(seconds=5.0, threads=2),
                    ],
                )
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            modes, warnings = _estimate_tool_mode_options(
                tool_id="genie3_01",
                run_id="genie3_01",
                toolspec=self._toolspec(),
                cost_profile=cost_payload,
                execution_mode="global",
                resolved_params={"limit": 50},
                extras_present=set(),
                logical_group_count=0,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={}),
                max_cores=8,
                max_ram_gb=4.0,
                output_dir="tools/genie3_01",
            )

        self.assertTrue(any("incompatible" in warning for warning in warnings))
        self.assertEqual(modes[0].eta_source, "cost_profile")
        self.assertEqual(modes[0].threads, 2)

    def test_unsupported_threading_never_plans_more_than_one_thread(self) -> None:
        toolspec = self._toolspec()
        toolspec["runtime_resources"]["threading"] = {
            "supported": False,
            "default_threads": 1,
            "max_threads": 1,
            "upstream_mapping": "No upstream parallel runtime control.",
        }
        cost_payload = {
            "profiles": [
                _profile(
                    "global_default",
                    runtime_points=[_runtime_point(seconds=1.0, threads=2)],
                )
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            modes, warnings = _estimate_tool_mode_options(
                tool_id="serial_tool_01",
                run_id="serial_tool_01",
                toolspec=toolspec,
                cost_profile=cost_payload,
                execution_mode="global",
                resolved_params={"limit": 50},
                extras_present=set(),
                logical_group_count=0,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={}),
                max_cores=8,
                max_ram_gb=4.0,
                output_dir="tools/serial_tool_01",
            )

        self.assertTrue(any("incompatible" in warning for warning in warnings))
        self.assertEqual(modes[0].eta_source, "fallback_no_usable_runtime_point")
        self.assertEqual(modes[0].threads, 1)

    def test_selects_profile_without_optional_inputs_when_manifest_lacks_them(
        self,
    ) -> None:
        cost_payload = {
            "profiles": [
                _profile("global_default", extras=[], seconds=20.0),
                _profile(
                    "global_tf_list",
                    extras=["tf_list"],
                    optional=["tf_list"],
                    seconds=1.0,
                ),
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            modes, warnings = _estimate_tool_mode_options(
                tool_id="genie3_01",
                run_id="genie3_01",
                toolspec=self._toolspec(),
                cost_profile=cost_payload,
                execution_mode="global",
                resolved_params={"limit": 50},
                extras_present=set(),
                logical_group_count=0,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={}),
                max_cores=2,
                max_ram_gb=4.0,
                output_dir="tools/genie3_01",
            )

        self.assertEqual(warnings, [])
        self.assertEqual(
            modes[0].eta_provenance["cost_profile"]["profile_id"],
            "global_default",
        )
        self.assertEqual(modes[0].eta_seconds, 24.0)
        self.assertEqual(
            modes[0].eta_provenance["cost_profile"]["estimation_policy"],
            "cost_profile_v2",
        )
        self.assertEqual(
            modes[0].eta_provenance["cost_profile"]["uncertainty_components"][
                "sample_count_penalty"
            ],
            0.2,
        )

    def test_selects_exact_optional_input_profile_when_manifest_provides_it(
        self,
    ) -> None:
        cost_payload = {
            "profiles": [
                _profile("global_default", extras=[], seconds=20.0),
                _profile(
                    "global_tf_list",
                    extras=["tf_list"],
                    optional=["tf_list"],
                    seconds=1.0,
                ),
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tf_path = Path(tmp) / "tf_list.txt"
            tf_path.write_text("G1\n", encoding="utf-8")
            modes, warnings = _estimate_tool_mode_options(
                tool_id="genie3_01",
                run_id="genie3_01",
                toolspec=self._toolspec(),
                cost_profile=cost_payload,
                execution_mode="global",
                resolved_params={"limit": 50},
                extras_present={"tf_list"},
                logical_group_count=0,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={"tf_list": tf_path}),
                max_cores=2,
                max_ram_gb=4.0,
                output_dir="tools/genie3_01",
            )

        self.assertEqual(warnings, [])
        self.assertEqual(
            modes[0].eta_provenance["cost_profile"]["profile_id"],
            "global_tf_list",
        )

    def test_selects_group_native_profile_for_native_grouped_mode(self) -> None:
        cost_payload = {
            "profiles": [
                _profile("global_default", mode="global", seconds=20.0),
                _profile(
                    "group_native_groups_2",
                    mode="group_native",
                    group_count=2,
                    seconds=3.0,
                ),
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            modes, warnings = _estimate_tool_mode_options(
                tool_id="native_tool_01",
                run_id="native_tool_01",
                toolspec=self._toolspec(),
                cost_profile=cost_payload,
                execution_mode="group_native",
                resolved_params={"limit": 50},
                extras_present=set(),
                logical_group_count=2,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={}),
                max_cores=2,
                max_ram_gb=4.0,
                output_dir="tools/native_tool_01",
            )

        self.assertEqual(warnings, [])
        cost_profile = modes[0].eta_provenance["cost_profile"]
        self.assertEqual(cost_profile["profile_id"], "group_native_groups_2")
        self.assertEqual(cost_profile["profile_execution_mode"], "group_native")
        self.assertEqual(modes[0].eta_seconds, 3.6)

    def test_group_emulated_profile_records_group_multiplier_provenance(self) -> None:
        cost_payload = {
            "profiles": [
                _profile(
                    "group_emulated_groups_2",
                    mode="group_emulated",
                    extras=["groups"],
                    conditional=["groups"],
                    group_count=2,
                    seconds=3.0,
                )
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            groups_path = Path(tmp) / "groups.tsv"
            groups_path.write_text("sample\tcluster\nS1\ts1\n", encoding="utf-8")
            modes, warnings = _estimate_tool_mode_options(
                tool_id="genie3_01__group_01_s1",
                run_id="genie3_01",
                toolspec=self._toolspec(),
                cost_profile=cost_payload,
                execution_mode="group_emulated",
                resolved_params={"limit": 50},
                extras_present={"groups"},
                logical_group_count=5,
                physical_tasks_total=5,
                dataset=self._dataset(Path(tmp), extras={"groups": groups_path}),
                max_cores=2,
                max_ram_gb=4.0,
                output_dir="tools/genie3_01/subruns/01_s1",
                group_label="s1",
            )

        self.assertTrue(any("group count differs" in warning for warning in warnings))
        cost_profile = modes[0].eta_provenance["cost_profile"]
        self.assertEqual(cost_profile["profile_id"], "group_emulated_groups_2")
        self.assertEqual(
            cost_profile["multipliers"],
            {"physical_tasks": 5, "group_count": 5},
        )
        self.assertEqual(cost_profile["raw_size_scale"], 1.0)
        self.assertEqual(cost_profile["size_scale_floor"], 1.0)
        self.assertGreater(cost_profile["uncertainty_penalty"], 1.0)

    def test_approximate_profile_does_not_downscale_from_larger_runtime_point(
        self,
    ) -> None:
        larger_point = _runtime_point(seconds=2.0, threads=1)
        larger_point["genes"] = 20
        larger_point["columns"] = 10
        cost_payload = {
            "profiles": [
                _profile(
                    "global_limit_10",
                    seconds=2.0,
                    resolved_params={"limit": 10},
                    cost_relevant_params=["limit"],
                    runtime_points=[larger_point],
                )
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            modes, warnings = _estimate_tool_mode_options(
                tool_id="genie3_01",
                run_id="genie3_01",
                toolspec=self._toolspec(),
                cost_profile=cost_payload,
                execution_mode="global",
                resolved_params={"limit": 50},
                extras_present=set(),
                logical_group_count=0,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={}),
                max_cores=2,
                max_ram_gb=4.0,
                output_dir="tools/genie3_01",
            )

        self.assertTrue(any("cost-relevant parameter" in w for w in warnings))
        cost_profile = modes[0].eta_provenance["cost_profile"]
        self.assertEqual(cost_profile["raw_size_scale"], 0.5)
        self.assertEqual(cost_profile["size_scale"], 1.0)
        self.assertEqual(cost_profile["size_scale_floor"], 1.0)
        self.assertGreater(cost_profile["uncertainty_penalty"], 1.0)

    def test_only_cost_relevant_params_affect_profile_matching(self) -> None:
        cost_payload = {
            "profiles": [
                _profile(
                    "limit_10",
                    seconds=1.0,
                    resolved_params={"limit": 10, "seed": 111},
                    cost_relevant_params=["limit"],
                ),
                _profile(
                    "limit_50",
                    seconds=5.0,
                    resolved_params={"limit": 50, "seed": 111},
                    cost_relevant_params=["limit"],
                ),
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            modes, warnings = _estimate_tool_mode_options(
                tool_id="genie3_01",
                run_id="genie3_01",
                toolspec=self._toolspec(),
                cost_profile=cost_payload,
                execution_mode="global",
                resolved_params={"limit": 50, "seed": 999},
                extras_present=set(),
                logical_group_count=0,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={}),
                max_cores=2,
                max_ram_gb=4.0,
                output_dir="tools/genie3_01",
            )

        self.assertFalse(
            any("cost-relevant parameter" in warning for warning in warnings)
        )
        cost_profile = modes[0].eta_provenance["cost_profile"]
        self.assertEqual(cost_profile["profile_id"], "limit_50")
        self.assertEqual(cost_profile["cost_relevant_params"], ["limit"])
        self.assertEqual(cost_profile["cost_relevant_values"], {"limit": 50})
        self.assertEqual(
            cost_profile["planned_cost_relevant_values"],
            {"limit": 50},
        )

    def test_fallback_records_warning_when_no_execution_mode_profile_matches(
        self,
    ) -> None:
        cost_payload = {"profiles": [_profile("global_default", mode="global")]}

        with tempfile.TemporaryDirectory() as tmp:
            modes, warnings = _estimate_tool_mode_options(
                tool_id="native_tool_01",
                run_id="native_tool_01",
                toolspec=self._toolspec(),
                cost_profile=cost_payload,
                execution_mode="group_native",
                resolved_params={"limit": 50},
                extras_present=set(),
                logical_group_count=2,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={}),
                max_cores=2,
                max_ram_gb=4.0,
                output_dir="tools/native_tool_01",
            )

        self.assertEqual(modes[0].eta_source, "fallback_no_matching_cost_profile")
        self.assertTrue(any("no profile for execution.mode" in w for w in warnings))
        self.assertIn(
            "no profile for execution.mode",
            modes[0].eta_provenance["warnings"][0],
        )

    def test_profile_requiring_absent_optional_input_is_not_used(self) -> None:
        cost_payload = {
            "profiles": [
                _profile(
                    "global_tf_list",
                    extras=["tf_list"],
                    optional=["tf_list"],
                    seconds=1.0,
                )
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            modes, warnings = _estimate_tool_mode_options(
                tool_id="genie3_01",
                run_id="genie3_01",
                toolspec=self._toolspec(),
                cost_profile=cost_payload,
                execution_mode="global",
                resolved_params={"limit": 50},
                extras_present=set(),
                logical_group_count=0,
                physical_tasks_total=1,
                dataset=self._dataset(Path(tmp), extras={}),
                max_cores=2,
                max_ram_gb=4.0,
                output_dir="tools/genie3_01",
            )

        self.assertEqual(modes[0].eta_source, "fallback_no_matching_cost_profile")
        self.assertTrue(any("none are compatible" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
