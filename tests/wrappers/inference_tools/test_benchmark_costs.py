from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "wrappers" / "inference_tools" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
for module_name in list(sys.modules):
    if module_name == "shared" or module_name.startswith("shared."):
        del sys.modules[module_name]

import benchmark_costs  # noqa: E402
import validate_tool_costs  # noqa: E402
from benchmark_costs import (  # noqa: E402
    make_cost_payload,
    make_cost_profile_entry,
    resolve_tool_targets,
)

CATALOG_TOOLS_ROOT = REPO_ROOT / "andrea" / "catalog_inference_tools" / "tools"
PARAM_OVERRIDES_DIR = REPO_ROOT / "wrappers" / "inference_tools" / "param_overrides"
COST_PROFILES_DIR = REPO_ROOT / "wrappers" / "inference_tools" / "cost_profiles"
TOOL_COST_SCHEMA = (
    REPO_ROOT
    / "andrea"
    / "catalog_inference_tools"
    / "schemas"
    / "toolcost.schema.json"
)


def _runtime_point() -> dict[str, object]:
    return {
        "genes": 10,
        "columns": 5,
        "threads": 1,
        "ram_gb": 1.0,
        "status": "ok",
        "repeats_total": 1,
        "repeats_ok": 1,
        "repeats_failed": 0,
        "ok_rate": 1.0,
        "failure_breakdown": {"oom": 0, "timeout": 0, "error": 0},
        "seconds_p50": 1.0,
        "seconds_p90": 1.0,
    }


def _catalog_toolspec(tool_id: str, *, supported: bool = True) -> dict[str, object]:
    payload = json.loads((CATALOG_TOOLS_ROOT / tool_id / "toolspec.json").read_text())
    payload["runtime_resources"] = {
        "threading": {
            "supported": supported,
            "default_threads": 1,
            "max_threads": 8 if supported else 1,
            "upstream_mapping": (
                "Test fixture maps --threads to upstream runtime controls."
                if supported
                else "Test fixture exposes no upstream parallel runtime control."
            ),
        }
    }
    return payload


def _write_catalog_toolspec(
    target_path: Path,
    tool_id: str,
    *,
    supported: bool = True,
) -> None:
    target_path.write_text(
        json.dumps(_catalog_toolspec(tool_id, supported=supported), indent=2) + "\n",
        encoding="utf-8",
    )


def _valid_cost_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "profiles": [
            {
                "profile_id": "global_default",
                "benchmark_config": {
                    "sizes": [{"genes": 10, "columns": 5}],
                    "threads_tested": [1],
                    "ram_gb_tested": [1.0],
                    "repeats": 1,
                    "timeout_seconds": 60,
                    "execution_profile": {
                        "mode": "global",
                        "physical_task_policy": "single",
                        "group_count": 0,
                    },
                    "input_profile": {
                        "column_kind": "samples",
                        "expression_profile": "synthetic_benchmark",
                        "extras_provided": [],
                        "required_inputs_satisfied": [],
                        "optional_inputs_provided": [],
                        "conditional_inputs_satisfied": [],
                        "tf_count_policy": None,
                        "prior_density": None,
                        "group_count": 0,
                        "notes": [],
                    },
                    "params_profile": {
                        "source": "toolspec_defaults",
                        "override_file": None,
                        "resolved_params": {"limit": 50},
                        "cost_relevant_params": ["limit"],
                        "cost_relevant_values": {"limit": 50},
                    },
                },
                "runtime_points": [_runtime_point()],
            }
        ],
    }


def _schema_error_messages(payload: object) -> list[str]:
    schema = validate_tool_costs.load_json(TOOL_COST_SCHEMA)
    validator = validate_tool_costs.build_validator(schema)
    return [
        error.message
        for error in validate_tool_costs.validate_instance(validator, payload)
    ]


def _semantic_errors(payload: object, *, tool_id: str = "genie3") -> list[str]:
    return validate_tool_costs.semantic_errors_for_cost(
        tool_id=tool_id,
        instance=payload,
        catalog_tools_root=CATALOG_TOOLS_ROOT,
        known_input_keys=validate_tool_costs.discover_input_keys(
            CATALOG_TOOLS_ROOT.parent / "input_specs"
        ),
    )


class BenchmarkCostsPhase4Test(unittest.TestCase):
    def test_toolcost_schema_rejects_old_non_profile_payloads(self) -> None:
        old_payload = {
            "benchmark_config": {},
            "runtime_points": [_runtime_point()],
        }

        messages = _schema_error_messages(old_payload)

        self.assertTrue(
            any("'schema_version' is a required property" in m for m in messages)
        )
        self.assertTrue(any("'profiles' is a required property" in m for m in messages))

    def test_toolcost_schema_rejects_missing_input_profile(self) -> None:
        payload = copy.deepcopy(_valid_cost_payload())
        del payload["profiles"][0]["benchmark_config"]["input_profile"]

        messages = _schema_error_messages(payload)

        self.assertTrue(
            any("'input_profile' is a required property" in m for m in messages)
        )

    def test_toolcost_semantics_reject_invalid_execution_mode(self) -> None:
        payload = copy.deepcopy(_valid_cost_payload())
        payload["profiles"][0]["benchmark_config"]["execution_profile"][
            "mode"
        ] = "group_native"
        payload["profiles"][0]["benchmark_config"]["execution_profile"][
            "physical_task_policy"
        ] = "native_grouped"
        payload["profiles"][0]["benchmark_config"]["execution_profile"][
            "group_count"
        ] = 2
        payload["profiles"][0]["benchmark_config"]["input_profile"]["group_count"] = 2

        errors = _semantic_errors(payload)

        self.assertTrue(any("execution_profile.mode" in error for error in errors))

    def test_toolcost_semantics_reject_unknown_or_undeclared_extras(self) -> None:
        payload = copy.deepcopy(_valid_cost_payload())
        input_profile = payload["profiles"][0]["benchmark_config"]["input_profile"]
        input_profile["extras_provided"] = ["prior_grn"]

        errors = _semantic_errors(payload)

        self.assertTrue(
            any("not declared by this ToolSpec" in error for error in errors)
        )

    def test_toolcost_semantics_reject_threads_incompatible_with_toolspec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_root = Path(tmp) / "tools"
            (catalog_root / "serial_tool").mkdir(parents=True)
            toolspec = _catalog_toolspec("genie3", supported=False)
            toolspec["id"] = "serial_tool"
            (catalog_root / "serial_tool" / "toolspec.json").write_text(
                json.dumps(toolspec, indent=2) + "\n",
                encoding="utf-8",
            )
            payload = copy.deepcopy(_valid_cost_payload())
            profile = payload["profiles"][0]
            profile["benchmark_config"]["threads_tested"] = [1, 2]
            profile["runtime_points"][0]["threads"] = 2

            errors = validate_tool_costs.semantic_errors_for_cost(
                tool_id="serial_tool",
                instance=payload,
                catalog_tools_root=catalog_root,
                known_input_keys=validate_tool_costs.discover_input_keys(
                    CATALOG_TOOLS_ROOT.parent / "input_specs"
                ),
            )

        self.assertTrue(any("threads_tested" in error for error in errors), errors)
        self.assertTrue(any("runtime_points[1].threads" in error for error in errors), errors)

    def test_benchmark_thread_filter_respects_toolspec_threading(self) -> None:
        supported = _catalog_toolspec("genie3", supported=True)
        supported["runtime_resources"]["threading"]["max_threads"] = 4
        serial = _catalog_toolspec("genie3", supported=False)

        self.assertEqual(
            benchmark_costs.filter_threads_for_tool(
                tool_id="parallel_tool",
                toolspec=supported,
                requested_threads=[1, 2, 4, 8],
            ),
            [1, 2, 4],
        )
        self.assertEqual(
            benchmark_costs.filter_threads_for_tool(
                tool_id="serial_tool",
                toolspec=serial,
                requested_threads=[1, 2, 4],
            ),
            [1],
        )

    def test_profile_filters_support_tool_qualified_and_unqualified_ids(self) -> None:
        targets = resolve_tool_targets(
            selected_tools=[
                ("genie3", CATALOG_TOOLS_ROOT / "genie3"),
                ("scmtni", CATALOG_TOOLS_ROOT / "scmtni"),
            ],
            catalog_tools_root=CATALOG_TOOLS_ROOT,
            param_overrides_dir=PARAM_OVERRIDES_DIR,
            cost_profiles_dir=COST_PROFILES_DIR,
            default_group_count=2,
            default_prior_density=0.05,
            default_optional_inputs=None,
            profile_filters=[
                "genie3:global_tf_list",
                "group_native_groups_1_q0_independent",
            ],
        )

        self.assertEqual([target.tool_id for target in targets], ["genie3", "scmtni"])
        self.assertEqual(
            [profile.profile_id for profile in targets[0].profiles],
            ["global_tf_list"],
        )
        self.assertEqual(
            [profile.profile_id for profile in targets[1].profiles],
            ["group_native_groups_1_q0_independent"],
        )

    def test_profile_filter_rejects_unknown_ids(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unknown benchmark profile"):
            resolve_tool_targets(
                selected_tools=[("genie3", CATALOG_TOOLS_ROOT / "genie3")],
                catalog_tools_root=CATALOG_TOOLS_ROOT,
                param_overrides_dir=PARAM_OVERRIDES_DIR,
                cost_profiles_dir=COST_PROFILES_DIR,
                default_group_count=2,
                default_prior_density=0.05,
                default_optional_inputs=None,
                profile_filters=["missing_profile"],
            )

    def test_cost_payload_preserves_multiple_profile_entries(self) -> None:
        first = make_cost_profile_entry(
            profile_id="global_default",
            benchmark_config={"profile": "a"},
            runtime_points=[{"status": "ok"}],
        )
        second = make_cost_profile_entry(
            profile_id="group_emulated_groups_2",
            benchmark_config={"profile": "b"},
            runtime_points=[{"status": "partial"}],
        )

        payload = make_cost_payload(profile_entries=[first, second])

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(
            [profile["profile_id"] for profile in payload["profiles"]],
            ["global_default", "group_emulated_groups_2"],
        )

    def test_tool_run_summary_reports_timeouts_separately_from_failures(self) -> None:
        summary = benchmark_costs.summarize_tool_runs(
            [
                {"status": "ok"},
                {"status": "timeout"},
                {"status": "error"},
            ]
        )

        self.assertEqual(summary["total_runs"], 3)
        self.assertEqual(summary["successful_runs"], 1)
        self.assertEqual(summary["timeout_runs"], 1)
        self.assertEqual(summary["failed_runs"], 1)
        self.assertEqual(
            summary["status_counts"],
            {"error": 1, "ok": 1, "timeout": 1},
        )

    def test_run_writes_selected_profiles_without_pooling_runtime_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            catalog_root = tmp_root / "catalog_tools"
            tool_sources_root = tmp_root / "tool_sources"
            (catalog_root / "genie3").mkdir(parents=True)
            (tool_sources_root / "genie3").mkdir(parents=True)
            _write_catalog_toolspec(
                catalog_root / "genie3" / "toolspec.json",
                "genie3",
            )

            with patch.object(
                benchmark_costs,
                "run_container_once",
                return_value=("ok", 1.25, ""),
            ):
                exit_code = benchmark_costs.run(
                    [
                        "--catalog-tools-root",
                        str(catalog_root),
                        "--tool-sources-root",
                        str(tool_sources_root),
                        "--param-overrides-dir",
                        str(PARAM_OVERRIDES_DIR),
                        "--cost-profiles-dir",
                        str(COST_PROFILES_DIR),
                        "--tool",
                        "genie3",
                        "--profile",
                        "global_default",
                        "--profile",
                        "global_tf_list",
                        "--size",
                        "8x4",
                        "--threads",
                        "1",
                        "--ram-gb",
                        "1",
                        "--skip-build",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads((catalog_root / "genie3" / "cost.json").read_text())
            self.assertEqual(
                [profile["profile_id"] for profile in payload["profiles"]],
                ["global_default", "global_tf_list"],
            )
            for profile in payload["profiles"]:
                self.assertEqual(len(profile["runtime_points"]), 1)
                self.assertEqual(profile["runtime_points"][0]["seconds_p50"], 1.25)
                feature_vector = profile["runtime_points"][0]["feature_vector"]
                self.assertEqual(feature_vector["n_genes"], 8)
                self.assertEqual(feature_vector["n_cells"], 0)
                self.assertEqual(feature_vector["aggregation_step"], "none")
                params_profile = profile["benchmark_config"]["params_profile"]
                self.assertEqual(
                    params_profile["cost_relevant_params"],
                    [
                        "regressor_type",
                        "regressor_kwargs.n_estimators",
                        "regressor_kwargs.max_features",
                    ],
                )
                self.assertEqual(
                    params_profile["cost_relevant_values"],
                    {
                        "regressor_type": "RF",
                        "regressor_kwargs.n_estimators": 100,
                        "regressor_kwargs.max_features": "sqrt",
                    },
                )

    def test_run_no_write_cost_keeps_catalog_cost_file_unchanged_and_generates_io(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            catalog_root = tmp_root / "catalog_tools"
            tool_sources_root = tmp_root / "tool_sources"
            workdir = tmp_root / "benchmark_work"
            (catalog_root / "clr").mkdir(parents=True)
            (tool_sources_root / "clr").mkdir(parents=True)
            _write_catalog_toolspec(
                catalog_root / "clr" / "toolspec.json",
                "clr",
                supported=False,
            )
            cost_path = catalog_root / "clr" / "cost.json"
            cost_path.write_text('{"sentinel": true}\n', encoding="utf-8")

            with (
                patch.object(
                    benchmark_costs,
                    "run_container_once",
                    return_value=("ok", 1.25, ""),
                ),
                patch.object(
                    benchmark_costs,
                    "allocate_tool_workdir",
                    return_value=(workdir, None),
                ),
            ):
                exit_code = benchmark_costs.run(
                    [
                        "--catalog-tools-root",
                        str(catalog_root),
                        "--tool-sources-root",
                        str(tool_sources_root),
                        "--param-overrides-dir",
                        str(PARAM_OVERRIDES_DIR),
                        "--cost-profiles-dir",
                        str(COST_PROFILES_DIR),
                        "--tool",
                        "clr",
                        "--profile",
                        "global_default",
                        "--size",
                        "8x4",
                        "--threads",
                        "1",
                        "--ram-gb",
                        "1",
                        "--skip-build",
                        "--no-write-cost",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                cost_path.read_text(encoding="utf-8"), '{"sentinel": true}\n'
            )
            io_dir = workdir / "global_default" / "global_default_g8_c4_t1_m1_r1" / "io"
            self.assertTrue((io_dir / "expression.tsv").is_file())
            self.assertTrue((io_dir / "params.json").is_file())
            self.assertTrue((io_dir / "execution.json").is_file())
            self.assertTrue((io_dir / "extra").is_dir())
            self.assertTrue((io_dir / "out").is_dir())

    def test_run_uses_profile_specific_sizes_when_cli_size_is_not_explicit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            catalog_root = tmp_root / "catalog_tools"
            tool_sources_root = tmp_root / "tool_sources"
            cost_profiles_root = tmp_root / "cost_profiles"
            (catalog_root / "genie3").mkdir(parents=True)
            (tool_sources_root / "genie3").mkdir(parents=True)
            cost_profiles_root.mkdir(parents=True)
            _write_catalog_toolspec(
                catalog_root / "genie3" / "toolspec.json",
                "genie3",
            )
            (cost_profiles_root / "genie3.json").write_text(
                json.dumps(
                    {
                        "sizes": ["9x5"],
                        "profiles": [
                            {
                                "id": "global_small",
                                "execution": {"mode": "global"},
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(
                benchmark_costs,
                "run_container_once",
                return_value=("ok", 1.25, ""),
            ):
                exit_code = benchmark_costs.run(
                    [
                        "--catalog-tools-root",
                        str(catalog_root),
                        "--tool-sources-root",
                        str(tool_sources_root),
                        "--param-overrides-dir",
                        str(PARAM_OVERRIDES_DIR),
                        "--cost-profiles-dir",
                        str(cost_profiles_root),
                        "--tool",
                        "genie3",
                        "--threads",
                        "1",
                        "--ram-gb",
                        "1",
                        "--skip-build",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads((catalog_root / "genie3" / "cost.json").read_text())
            profile = payload["profiles"][0]
            self.assertEqual(
                profile["benchmark_config"]["sizes"],
                [{"genes": 9, "columns": 5}],
            )
            self.assertEqual(profile["runtime_points"][0]["genes"], 9)
            self.assertEqual(profile["runtime_points"][0]["columns"], 5)

    def test_run_plan_only_does_not_build_run_or_write_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            catalog_root = tmp_root / "catalog_tools"
            tool_sources_root = tmp_root / "tool_sources"
            (catalog_root / "genie3").mkdir(parents=True)
            _write_catalog_toolspec(
                catalog_root / "genie3" / "toolspec.json",
                "genie3",
            )

            with (
                patch.object(benchmark_costs, "build_image") as build_image,
                patch.object(benchmark_costs, "run_container_once") as run_container,
            ):
                exit_code = benchmark_costs.run(
                    [
                        "--catalog-tools-root",
                        str(catalog_root),
                        "--tool-sources-root",
                        str(tool_sources_root),
                        "--param-overrides-dir",
                        str(PARAM_OVERRIDES_DIR),
                        "--cost-profiles-dir",
                        str(COST_PROFILES_DIR),
                        "--tool",
                        "genie3",
                        "--profile",
                        "global_default",
                        "--threads",
                        "1",
                        "--ram-gb",
                        "1",
                        "--plan-only",
                    ]
                )

            self.assertEqual(exit_code, 0)
            build_image.assert_not_called()
            run_container.assert_not_called()
            self.assertFalse((catalog_root / "genie3" / "cost.json").exists())

    def test_run_container_timeout_cleans_up_named_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            io_dir = Path(tmp)
            calls: list[list[str]] = []

            def fake_run_cmd(cmd, **_kwargs):
                command = list(cmd)
                calls.append(command)
                if command[:2] == ["docker", "run"]:
                    raise subprocess.TimeoutExpired(command, 1)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="removed",
                    stderr="",
                )

            with (
                patch.object(benchmark_costs, "run_cmd", side_effect=fake_run_cmd),
                patch.object(
                    benchmark_costs,
                    "docker_container_name",
                    return_value="andrea_timeout_test",
                ),
            ):
                status, _elapsed, message = benchmark_costs.run_container_once(
                    image_tag="test-image",
                    io_dir=io_dir,
                    threads=1,
                    ram_gb=1,
                    timeout_s=1,
                )

        self.assertEqual(status, "timeout")
        self.assertIn("Run exceeded timeout", message)
        self.assertTrue(
            any(command[:4] == ["docker", "rm", "-f", "andrea_timeout_test"] for command in calls),
            calls,
        )


if __name__ == "__main__":
    unittest.main()
