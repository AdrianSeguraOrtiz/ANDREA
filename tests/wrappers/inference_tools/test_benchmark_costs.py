from __future__ import annotations

import copy
import json
import hashlib
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
                        "gene_id_source": "synthetic",
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


class BenchmarkCostsContractTest(unittest.TestCase):
    def setUp(self) -> None:
        def evidence(**kwargs):
            return {
                "recorded_at_utc": "2026-09-29T00:00:00+00:00",
                "git_commit": "f" * 40, "git_dirty": False,
                "source_sha256": "a" * 64,
                "spec_sha256": hashlib.sha256(kwargs["spec_path"].read_bytes()).hexdigest(),
                "image_reference": kwargs["image"], "image_id": "sha256:" + "b" * 64,
                "image_repo_digests": [], "platform": "test", "cpu_model": "test",
                "logical_cpus": 1, "host_memory_bytes": 1024, "docker_server": "test",
                "seed": kwargs["seed"], "timing_scope": "test",
            }
        evidence_patch = patch.object(benchmark_costs, "collect_provenance", side_effect=evidence)
        evidence_patch.start()
        self.addCleanup(evidence_patch.stop)

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

    def test_toolcost_semantics_rejects_unaccepted_column_kind(self) -> None:
        payload = copy.deepcopy(_valid_cost_payload())

        errors = _semantic_errors(payload, tool_id="infercsn")

        self.assertTrue(
            any("column_kind must be accepted by the ToolSpec" in error for error in errors),
            errors,
        )

    def test_toolcost_semantics_rejects_logical_orchestration_mode(self) -> None:
        payload = copy.deepcopy(_valid_cost_payload())
        benchmark_config = payload["profiles"][0]["benchmark_config"]
        benchmark_config["execution_profile"] = {
            "mode": "group_emulated",
            "physical_task_policy": "andrea_group_emulated",
            "group_count": 2,
            "aggregation_step": "none",
        }
        errors = _semantic_errors(payload)

        self.assertTrue(
            any("must describe a physical wrapper task" in error for error in errors),
            errors,
        )

    def test_physical_benchmark_io_writes_physical_mode(self) -> None:
        cases = [
            ("genie3", "global_default", "global"),
            ("kscreni", "column_native_default", "column_native"),
        ]

        for tool_id, profile_id, expected_mode in cases:
            with self.subTest(tool_id=tool_id, profile_id=profile_id):
                target = resolve_tool_targets(
                    selected_tools=[(tool_id, CATALOG_TOOLS_ROOT / tool_id)],
                    catalog_tools_root=CATALOG_TOOLS_ROOT,
                    param_overrides_dir=PARAM_OVERRIDES_DIR,
                    cost_profiles_dir=COST_PROFILES_DIR,
                    default_group_count=2,
                    default_prior_density=0.05,
                    default_optional_inputs=None,
                    profile_filters=[profile_id],
                )[0]
                profile = target.profiles[0]

                with tempfile.TemporaryDirectory() as tmp:
                    io_dir = Path(tmp)
                    benchmark_costs.prepare_io_dir(
                        io_dir,
                        benchmark_costs.SizePoint(genes=24, columns=16),
                        resolved_params=profile.params,
                        execution=profile.execution,
                        seed=123,
                        input_profile=profile.input_profile,
                    )
                    execution = json.loads(
                        (io_dir / "execution.json").read_text(encoding="utf-8")
                    )
                    extras = sorted(path.name for path in (io_dir / "extra").iterdir())

                self.assertEqual(execution, {"mode": expected_mode})
                self.assertNotIn("groups.tsv", extras)

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

    def test_unbounded_threading_contract_accepts_any_positive_benchmark_count(
        self,
    ) -> None:
        unbounded = _catalog_toolspec("genie3", supported=True)
        unbounded["runtime_resources"]["threading"]["max_threads"] = None

        self.assertEqual(
            benchmark_costs.filter_threads_for_tool(
                tool_id="unbounded_tool",
                toolspec=unbounded,
                requested_threads=[1, 8, 64],
            ),
            [1, 8, 64],
        )

        with tempfile.TemporaryDirectory() as tmp:
            catalog_root = Path(tmp) / "tools"
            (catalog_root / "unbounded_tool").mkdir(parents=True)
            unbounded["id"] = "unbounded_tool"
            (catalog_root / "unbounded_tool" / "toolspec.json").write_text(
                json.dumps(unbounded, indent=2) + "\n",
                encoding="utf-8",
            )
            payload = copy.deepcopy(_valid_cost_payload())
            profile = payload["profiles"][0]
            profile["benchmark_config"]["threads_tested"] = [64]
            profile["runtime_points"][0]["threads"] = 64

            errors = validate_tool_costs.semantic_errors_for_cost(
                tool_id="unbounded_tool",
                instance=payload,
                catalog_tools_root=catalog_root,
                known_input_keys=validate_tool_costs.discover_input_keys(
                    CATALOG_TOOLS_ROOT.parent / "input_specs"
                ),
            )

        self.assertFalse(
            any("runtime_resources.threading" in error for error in errors),
            errors,
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
                "global_q0_independent",
            ],
        )

        self.assertEqual([target.tool_id for target in targets], ["genie3", "scmtni"])
        self.assertEqual(
            [profile.profile_id for profile in targets[0].profiles],
            ["global_tf_list"],
        )
        self.assertEqual(
            [profile.profile_id for profile in targets[1].profiles],
            ["global_q0_independent"],
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
            profile_id="global_tf_list",
            benchmark_config={"profile": "b"},
            runtime_points=[{"status": "partial"}],
        )

        payload = make_cost_payload(profile_entries=[first, second])

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(
            [profile["profile_id"] for profile in payload["profiles"]],
            ["global_default", "global_tf_list"],
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

    def test_failed_matrix_returns_failure_and_preserves_existing_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog"
            sources = root / "sources"
            (catalog / "genie3").mkdir(parents=True)
            (sources / "genie3").mkdir(parents=True)
            _write_catalog_toolspec(catalog / "genie3/toolspec.json", "genie3")
            cost_path = catalog / "genie3/cost.json"
            original = '{"sentinel": true}\n'
            cost_path.write_text(original)
            with patch.object(benchmark_costs, "run_container_once", return_value=("error", 1.0, "failed")):
                result = benchmark_costs.run([
                    "--catalog-tools-root", str(catalog), "--tool-sources-root", str(sources),
                    "--tool", "genie3", "--size", "8x4", "--threads", "1", "--ram-gb", "1",
                    "--skip-build", "--fail-fast", "--results-dir", str(root / "evidence"),
                ])
            self.assertEqual(result, 1)
            self.assertEqual(cost_path.read_text(), original)
            measurements = list((root / "evidence").glob("**/measurements.json"))
            self.assertEqual(len(measurements), 1)
            self.assertEqual(json.loads(measurements[0].read_text())[0]["status"], "error")

    def test_missing_source_and_failed_build_return_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog"
            (catalog / "genie3").mkdir(parents=True)
            _write_catalog_toolspec(catalog / "genie3/toolspec.json", "genie3")
            argv = ["--catalog-tools-root", str(catalog), "--tool-sources-root", str(root / "sources"),
                    "--tool", "genie3", "--size", "8x4", "--threads", "1", "--ram-gb", "1"]
            self.assertEqual(benchmark_costs.run(argv), 1)
            (root / "sources/genie3").mkdir(parents=True)
            with patch.object(benchmark_costs, "build_image", side_effect=RuntimeError("build failed")):
                self.assertEqual(benchmark_costs.run(argv), 1)
            self.assertFalse((catalog / "genie3/cost.json").exists())

    def test_resume_discovers_nested_tool_campaign_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "dignet" / "dignet-verified" / "global_default"
            directory.mkdir(parents=True)
            (directory / "benchmark_config.json").write_text("{}")
            found = benchmark_costs.find_evidence_profiles(
                [root], "dignet", "global_default"
            )
            self.assertEqual(found, [directory.resolve()])

    def _resume_fixture(self, root: Path) -> tuple[list[str], Path]:
        catalog, sources = root / "catalog", root / "sources"
        (catalog / "genie3").mkdir(parents=True)
        (sources / "genie3").mkdir(parents=True)
        _write_catalog_toolspec(catalog / "genie3/toolspec.json", "genie3")
        argv = ["--catalog-tools-root", str(catalog), "--tool-sources-root", str(sources),
                "--tool", "genie3", "--profile", "global_default", "--merge-existing",
                "--size", "8x4", "--threads", "1", "--ram-gb", "1", "--repeats", "3",
                "--skip-build", "--results-dir", str(root / "evidence")]
        return argv, catalog / "genie3/cost.json"

    def test_resume_skips_valid_points_and_preserves_unrelated_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv, path = self._resume_fixture(Path(tmp))
            with patch.object(benchmark_costs, "run_container_once", return_value=("ok", 1.25, "")) as run:
                self.assertEqual(benchmark_costs.run(argv), 0)
                self.assertEqual(run.call_count, 3)
                first = json.loads(path.read_text())
                unrelated = copy.deepcopy(first["profiles"][0])
                unrelated["profile_id"] = "unrelated_historical_profile"
                first["profiles"].append(unrelated)
                path.write_text(json.dumps(first))
                run.reset_mock()
                self.assertEqual(benchmark_costs.run(argv + ["--size", "12x4"]), 0)
                self.assertEqual(run.call_count, 3)
            second = json.loads(path.read_text())
            self.assertEqual(second["profiles"][1], unrelated)
            self.assertEqual(second["profiles"][0]["runtime_points"][0]["seconds_p50"], 1.25)
            self.assertEqual(len(second["profiles"][0]["runtime_points"]), 2)
            self.assertFalse(_schema_error_messages(second), _schema_error_messages(second))

    def test_resume_uses_old_raw_input_evidence_after_recipe_script_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv, path = self._resume_fixture(Path(tmp))
            with patch.object(benchmark_costs, "run_container_once", return_value=("ok", 1.25, "")) as run:
                self.assertEqual(benchmark_costs.run(argv), 0)
                payload = json.loads(path.read_text())
                old_profile = payload["profiles"][0]
                old_profile["benchmark_config"]["profile_sha256"] = "1" * 64
                del old_profile["runtime_points"][0]["measurement_evidence"]
                path.write_text(json.dumps(payload))
                run.reset_mock()
                self.assertEqual(benchmark_costs.run(argv), 0)
                self.assertEqual(run.call_count, 0)

    def test_resume_rejects_changed_image_seed_parameters_and_actual_input_bytes(self) -> None:
        for mutation in ("image", "seed", "params", "input_bytes"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                argv, path = self._resume_fixture(Path(tmp))
                with patch.object(benchmark_costs, "run_container_once", return_value=("ok", 1.25, "")) as run:
                    self.assertEqual(benchmark_costs.run(argv), 0)
                    run.reset_mock()
                    if mutation == "image":
                        provenance = json.loads(path.read_text())["profiles"][0]["benchmark_config"]["provenance"]
                        provenance["image_id"] = "sha256:" + "c" * 64
                        with patch.object(benchmark_costs, "collect_provenance", return_value=provenance):
                            self.assertEqual(benchmark_costs.run(argv), 0)
                    elif mutation == "seed":
                        self.assertEqual(benchmark_costs.run(argv + ["--seed", "5678"]), 0)
                    elif mutation == "params":
                        overrides = Path(tmp) / "overrides"
                        overrides.mkdir()
                        (overrides / "genie3.json").write_text(json.dumps({"limit": 99}))
                        self.assertEqual(benchmark_costs.run(argv + ["--param-overrides-dir", str(overrides)]), 0)
                    else:
                        prepare = benchmark_costs.prepare_io_dir
                        def changed_inputs(io_dir, *args, **kwargs):
                            prepare(io_dir, *args, **kwargs)
                            with (io_dir / "expression.tsv").open("a") as output:
                                output.write("\n")
                        with patch.object(benchmark_costs, "prepare_io_dir", side_effect=changed_inputs):
                            self.assertEqual(benchmark_costs.run(argv), 0)
                    self.assertEqual(run.call_count, 3)

    def test_resume_retries_failed_repeat_without_repeating_successes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            argv, path = self._resume_fixture(root)
            with patch.object(benchmark_costs, "run_container_once", side_effect=[("ok", 1.25, ""), ("error", 2.0, "failure")]):
                self.assertEqual(benchmark_costs.run(argv + ["--fail-fast"]), 1)
            failed = next((root / "evidence").glob("*/global_default/measurements.json"))
            failed_bytes = failed.read_bytes()
            self.assertFalse(path.exists())
            with patch.object(benchmark_costs, "run_container_once", return_value=("ok", 1.5, "")) as run:
                self.assertEqual(benchmark_costs.run(argv), 0)
                self.assertEqual(run.call_count, 2)
            self.assertEqual(failed.read_bytes(), failed_bytes)
            point = json.loads(path.read_text())["profiles"][0]["runtime_points"][0]
            self.assertEqual(point["repeats_ok"], 3)
            self.assertEqual([r["seconds"] for r in point["measurement_evidence"]["observations"]], [1.25, 1.5, 1.5])

    def test_partial_matrix_checkpoints_completed_points_then_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv, path = self._resume_fixture(Path(tmp))
            argv += ["--size", "12x4"]
            with patch.object(benchmark_costs, "run_container_once", side_effect=[("ok", 1.25, "")] * 3 + [("timeout", 2.0, "timeout")]):
                self.assertEqual(benchmark_costs.run(argv + ["--fail-fast"]), 1)
            payload = json.loads(path.read_text())["profiles"][0]
            self.assertEqual(len(payload["runtime_points"]), 1)
            self.assertEqual(len(payload["benchmark_config"]["planned_points"]), 2)
            with patch.object(benchmark_costs, "run_container_once", return_value=("ok", 1.5, "")) as run:
                self.assertEqual(benchmark_costs.run(argv), 0)
                self.assertEqual(run.call_count, 3)
            self.assertEqual(len(json.loads(path.read_text())["profiles"][0]["runtime_points"]), 2)

    def test_resume_retains_historical_resources_without_expanding_cartesian_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv, path = self._resume_fixture(Path(tmp))
            with patch.object(benchmark_costs, "run_container_once", return_value=("ok", 1.25, "")):
                self.assertEqual(benchmark_costs.run(argv + ["--threads", "1,2"]), 0)
            argv[argv.index("8x4")] = "12x4"
            with patch.object(benchmark_costs, "run_container_once", return_value=("ok", 1.5, "")) as run:
                self.assertEqual(benchmark_costs.run(argv), 0)
                self.assertEqual(run.call_count, 3)
            profile = json.loads(path.read_text())["profiles"][0]
            self.assertEqual(len(profile["runtime_points"]), 3)
            self.assertEqual(len(profile["benchmark_config"]["planned_points"]), 3)
            self.assertEqual(profile["benchmark_config"]["threads_tested"], [1, 2])

    def test_required_cost_coverage_rejects_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp)
            (catalog / "genie3").mkdir()
            _write_catalog_toolspec(catalog / "genie3/toolspec.json", "genie3")
            with self.assertRaisesRegex(RuntimeError, "Missing tool cost.json for: genie3"):
                validate_tool_costs.run(TOOL_COST_SCHEMA, catalog, [], False, require=True)

    def test_changed_image_remeasures_only_explicit_and_historical_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv, path = self._resume_fixture(Path(tmp))
            with patch.object(benchmark_costs, "run_container_once", return_value=("ok", 1.25, "")):
                self.assertEqual(benchmark_costs.run(argv + ["--threads", "1,2"]), 0)
            before = json.loads(path.read_text())
            provenance = copy.deepcopy(before['profiles'][0]['benchmark_config']['provenance'])
            provenance['image_id'] = 'sha256:' + 'e' * 64
            argv[argv.index('8x4')] = '12x4'
            with patch.object(benchmark_costs, 'collect_provenance', return_value=provenance):
                with patch.object(benchmark_costs, 'run_container_once', return_value=('ok', 2., '')) as run:
                    self.assertEqual(benchmark_costs.run(argv), 1)
                    self.assertEqual(run.call_count, 0)
                    self.assertEqual(benchmark_costs.run(argv + ['--remeasure-incompatible-history']), 0)
                    self.assertEqual(run.call_count, 9)
            profile = json.loads(path.read_text())['profiles'][0]
            self.assertEqual({(p['genes'],p['columns'],p['threads']) for p in profile['runtime_points']},
                             {(8,4,1),(8,4,2),(12,4,1)})
            self.assertTrue(all(p['seconds_p50']==2. for p in profile['runtime_points']))

    def test_interrupted_image_recalibration_retains_pending_historical_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv, path = self._resume_fixture(Path(tmp))
            with patch.object(benchmark_costs, 'run_container_once', return_value=('ok',1.,'')):
                self.assertEqual(benchmark_costs.run(argv+['--threads','1,2']),0)
            provenance=copy.deepcopy(json.loads(path.read_text())['profiles'][0]['benchmark_config']['provenance'])
            provenance['image_id']='sha256:'+'f'*64
            argv[argv.index('8x4')]='12x4'
            argv+=['--remeasure-incompatible-history']
            with patch.object(benchmark_costs, 'collect_provenance', return_value=provenance):
                with patch.object(benchmark_costs, 'run_container_once', side_effect=[('ok',2.,'')]*3+[KeyboardInterrupt()]):
                    with self.assertRaises(KeyboardInterrupt):benchmark_costs.run(argv)
                partial=json.loads(path.read_text())['profiles'][0]
                self.assertEqual(len(partial['runtime_points']),1)
                self.assertEqual(len(partial['benchmark_config']['planned_points']),3)
                with patch.object(benchmark_costs, 'run_container_once',return_value=('ok',2.,'')) as run:
                    self.assertEqual(benchmark_costs.run(argv),0)
                    self.assertEqual(run.call_count,6)
            points=json.loads(path.read_text())['profiles'][0]['runtime_points']
            self.assertEqual({(p['genes'],p['threads']) for p in points},{(8,1),(8,2),(12,1)})

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
                        "--merge-existing",
                        "--profile",
                        "global_default",
                        "--merge-existing",
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
                        "--merge-existing",
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
                        "--merge-existing",
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

    def test_run_container_retry_removes_only_stale_cidfiles(self) -> None:
        for state in ("missing", "stopped"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                io_dir = Path(tmp)
                cidfile = io_dir / ".docker-container.cid"
                cidfile.write_text("a" * 64)
                (io_dir / "out").mkdir()
                calls = []
                def fake_run_cmd(cmd, **_kwargs):
                    calls.append(list(cmd))
                    if cmd[:2] == ["docker", "inspect"]:
                        return subprocess.CompletedProcess(cmd, 1 if state == "missing" else 0,
                            stdout="" if state == "missing" else "false\n",
                            stderr="Error: No such object: " + "a" * 64 if state == "missing" else "")
                    self.assertEqual(cmd[:2], ["docker", "run"])
                    self.assertFalse(cidfile.exists())
                    (io_dir / "out/network.csv").write_text("source,target,score\nG1,G2,1\n")
                    return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
                with patch.object(benchmark_costs, "run_cmd", side_effect=fake_run_cmd):
                    result = benchmark_costs.run_container_once(image_tag="test-image", io_dir=io_dir,
                                                               threads=1, ram_gb=1, timeout_s=5)
                self.assertEqual(result[0], "ok")
                self.assertEqual([cmd[:2] for cmd in calls], [["docker", "inspect"], ["docker", "run"]])

    def test_run_container_retry_preserves_active_or_unverifiable_cidfiles(self) -> None:
        for state in ("active", "daemon_error"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                io_dir = Path(tmp)
                cidfile = io_dir / ".docker-container.cid"
                cidfile.write_text("b" * 64)
                response = subprocess.CompletedProcess(["docker", "inspect"], 0 if state == "active" else 1,
                    stdout="true\n" if state == "active" else "",
                    stderr="" if state == "active" else "Cannot connect to the Docker daemon")
                with patch.object(benchmark_costs, "run_cmd", return_value=response) as run_cmd:
                    with self.assertRaisesRegex(RuntimeError, "still running|Cannot verify"):
                        benchmark_costs.run_container_once(image_tag="test-image", io_dir=io_dir,
                                                          threads=1, ram_gb=1, timeout_s=5)
                self.assertEqual(cidfile.read_text(), "b" * 64)
                self.assertEqual(run_cmd.call_count, 1)
                self.assertEqual(run_cmd.call_args.args[0][:2], ["docker", "inspect"])

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
        docker_run = next(
            command for command in calls if command[:2] == ["docker", "run"]
        )
        self.assertIn(f"{io_dir.resolve()}:/io:ro", docker_run)
        self.assertIn(f"{(io_dir / 'out').resolve()}:/io/out:rw", docker_run)
        self.assertTrue(
            any(command[:4] == ["docker", "rm", "-f", "andrea_timeout_test"] for command in calls),
            calls,
        )


if __name__ == "__main__":
    unittest.main()
