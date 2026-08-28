from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "wrappers" / "simulation_data_tools" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
for module_name in list(sys.modules):
    if module_name == "shared" or module_name.startswith("shared."):
        del sys.modules[module_name]

CATALOG_SIMULATORS_ROOT = (
    REPO_ROOT / "andrea" / "catalog_simulation_data_tools" / "simulators"
)
PARAM_OVERRIDES_DIR = REPO_ROOT / "wrappers" / "simulation_data_tools" / "param_overrides"
COST_PROFILES_DIR = REPO_ROOT / "wrappers" / "simulation_data_tools" / "cost_profiles"
SIMULATOR_COST_SCHEMA = (
    REPO_ROOT
    / "andrea"
    / "catalog_simulation_data_tools"
    / "schemas"
    / "simulatorcost.schema.json"
)
TRAJECTORY_AXES = {
    "measurement": "rna_expression",
    "resolution": "single_cell",
    "column_kind": "cells",
    "experimental_design": "trajectory",
}
GLOBAL_TRUTH = {"contexts": ["global"]}
ACTIVE_SIMULATORS = (
    "boolode",
    "dyngen",
    "genenetweaver",
    "genespider2",
    "groundgan",
    "scmultisim",
    "sergio",
    "syntren",
)


def _load_script(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


benchmark_costs = _load_script(
    "simulation_benchmark_costs", SCRIPTS_ROOT / "benchmark_costs.py"
)
validate_simulator_costs = _load_script(
    "simulation_validate_simulator_costs",
    SCRIPTS_ROOT / "validate_simulator_costs.py",
)


def _runtime_point() -> dict[str, object]:
    return {
        "genes": 10,
        "cells": 8,
        "groups": 0,
        "population_count": 0,
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
        "output_bytes_p50": 1000,
        "output_bytes_p90": 1000,
        "peak_memory_mb_p50": None,
        "peak_memory_mb_p90": None,
        "feature_vector": {
            "simulator_id": "dyngen",
            "data_axes": copy.deepcopy(TRAJECTORY_AXES),
            "truth_requirements": copy.deepcopy(GLOBAL_TRUTH),
            "benchmark_profile_id": "single_cell_cells_trajectory_global_linear_default",
            "profile_id": "single_cell_cells_trajectory_global_linear_default",
            "expression_profile": "single_cell",
            "column_kind": "cells",
            "experimental_design": "trajectory",
            "truth_context_families": ["global"],
            "truth_context_count": 1,
            "extras": ["tf_list"],
            "requested_extras": [],
            "effective_extras": ["tf_list"],
            "genes": 10,
            "cells": 8,
            "groups": 0,
            "population_count": 0,
            "threads": 1,
            "ram_gb": 1.0,
            "column_truth_requested": False,
            "cost_relevant_values": {},
        },
    }


def _valid_cost_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "profiles": [
            {
                "profile_id": "single_cell_cells_trajectory_global_linear_default",
                "benchmark_config": {
                    "simulator_id": "dyngen",
                    "data_axes": copy.deepcopy(TRAJECTORY_AXES),
                    "truth_requirements": copy.deepcopy(GLOBAL_TRUTH),
                    "sizes": [{"genes": 10, "cells": 8}],
                    "threads_tested": [1],
                    "ram_gb_tested": [1.0],
                    "repeats": 1,
                    "timeout_seconds": 60,
                    "dimension_profile": {
                        "cells_param": "num_cells",
                        "genes_param": {
                            "num_tfs": {"fraction": 0.2, "min": 2},
                            "num_targets": {"fraction": 0.6, "min": 1},
                            "num_hks": {"fraction": 0.2, "min": 0},
                        },
                        "group_count": 0,
                        "population_count": 0,
                    },
                    "input_profile": {
                        "requested_extras": [],
                        "effective_extras": ["tf_list"],
                        "required_inputs_satisfied": [],
                        "optional_inputs_provided": [],
                        "conditional_inputs_satisfied": [],
                        "input_source_modes": {},
                        "notes": [],
                    },
                    "params_profile": {
                        "source": "simulatorspec_defaults",
                        "override_file": None,
                        "resolved_base_params": {},
                        "cost_relevant_params": [],
                        "cost_relevant_values": {},
                    },
                    "runtime_resources_profile": {
                        "threading_supported": True,
                        "default_threads": 1,
                        "max_threads": 4096,
                        "upstream_mapping": "Mapped by wrapper.",
                    },
                },
                "runtime_points": [_runtime_point()],
            }
        ],
    }


class SimulatorBenchmarkCostsTests(unittest.TestCase):
    def test_merge_existing_requires_explicit_profile(self) -> None:
        args = benchmark_costs.parse_args(["--merge-existing"])

        with self.assertRaisesRegex(RuntimeError, "requires at least one"):
            benchmark_costs.validate_runtime_args(args)

    def test_simulatorcost_schema_accepts_profile_payload(self) -> None:
        schema = json.loads(SIMULATOR_COST_SCHEMA.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)

        errors = list(validator.iter_errors(_valid_cost_payload()))

        self.assertEqual(errors, [])

    def test_simulatorcost_schema_requires_tf_list_in_cost_profiles(self) -> None:
        schema = json.loads(SIMULATOR_COST_SCHEMA.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)

        for target in ("benchmark_config", "feature_vector"):
            with self.subTest(target=target):
                payload = copy.deepcopy(_valid_cost_payload())
                if target == "benchmark_config":
                    payload["profiles"][0]["benchmark_config"]["input_profile"][
                        "effective_extras"
                    ] = []
                else:
                    payload["profiles"][0]["runtime_points"][0]["feature_vector"][
                        "effective_extras"
                    ] = []

                self.assertNotEqual(list(validator.iter_errors(payload)), [])

    def test_simulatorcost_schema_rejects_missing_feature_vector(self) -> None:
        payload = copy.deepcopy(_valid_cost_payload())
        del payload["profiles"][0]["runtime_points"][0]["feature_vector"]
        schema = json.loads(SIMULATOR_COST_SCHEMA.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)

        messages = [error.message for error in validator.iter_errors(payload)]

        self.assertTrue(any("'feature_vector' is a required property" in m for m in messages))

    def test_resolver_preserves_conditional_input_profile(self) -> None:
        targets = benchmark_costs.resolve_simulator_targets(
            selected_simulators=[
                ("scmultisim", CATALOG_SIMULATORS_ROOT / "scmultisim")
            ],
            catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
            cost_profiles_dir=COST_PROFILES_DIR,
            param_overrides_dir=PARAM_OVERRIDES_DIR,
            default_group_count=2,
            profile_filters=["single_cell_cells_differentiation_global_group_custom_grn_tree"],
        )

        self.assertEqual(len(targets), 1)
        profile = targets[0].profiles[0]
        self.assertEqual(profile.profile_id, "single_cell_cells_differentiation_global_group_custom_grn_tree")
        self.assertEqual(
            [(size.genes, size.cells) for size in profile.sizes],
            [(50, 20), (100, 40), (200, 80)],
        )
        self.assertTrue(profile.params["dynamic_grn"]["enabled"])
        self.assertEqual(profile.dimension_profile["group_count"], 3)
        self.assertEqual(profile.dimension_profile["population_count"], 3)
        self.assertEqual(
            profile.input_profile["conditional_inputs_satisfied"],
            ["regulatory_network", "tree_newick"],
        )
        self.assertEqual(profile.input_profile["input_source_modes"]["grn_source"], "input_tsv")
        self.assertEqual(
            profile.input_profile["input_source_modes"]["tree_preset"],
            "input_newick",
        )

    def test_resolver_rejects_conditional_profile_without_required_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            shutil.copy2(COST_PROFILES_DIR / "scmultisim.json", config_dir / "scmultisim.json")
            payload = json.loads((config_dir / "scmultisim.json").read_text(encoding="utf-8"))
            custom = next(
                profile
                for profile in payload["profiles"]
                if profile["id"] == "single_cell_cells_differentiation_global_group_custom_grn_tree"
            )
            custom["inputs"] = {}
            (config_dir / "scmultisim.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "regulatory_network is required"):
                benchmark_costs.resolve_simulator_targets(
                    selected_simulators=[
                        ("scmultisim", CATALOG_SIMULATORS_ROOT / "scmultisim")
                    ],
                    catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
                    cost_profiles_dir=config_dir,
                    param_overrides_dir=PARAM_OVERRIDES_DIR,
                    default_group_count=2,
                    profile_filters=["single_cell_cells_differentiation_global_group_custom_grn_tree"],
                )

    def test_cost_profile_configs_cover_supported_profiles(self) -> None:
        for simulator_id in ACTIVE_SIMULATORS:
            with self.subTest(simulator_id=simulator_id):
                targets = benchmark_costs.resolve_simulator_targets(
                    selected_simulators=[
                        (simulator_id, CATALOG_SIMULATORS_ROOT / simulator_id)
                    ],
                    catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
                    cost_profiles_dir=COST_PROFILES_DIR,
                    param_overrides_dir=PARAM_OVERRIDES_DIR,
                    default_group_count=2,
                    profile_filters=[],
                )
                spec = targets[0].spec
                supported = {
                    benchmark_costs.semantic_key_from_json(
                        data_axes=capability["data_axes"],
                        truth_requirements=capability["truth_requirements"],
                    )
                    for capability in spec["capabilities"]
                }
                configured = {profile.profile for profile in targets[0].profiles}

                self.assertEqual(supported - configured, set())

    def test_cost_profile_configs_cover_supported_extras(self) -> None:
        for simulator_id in ACTIVE_SIMULATORS:
            with self.subTest(simulator_id=simulator_id):
                spec = json.loads(
                    (
                        CATALOG_SIMULATORS_ROOT
                        / simulator_id
                        / "simulatorspec.json"
                    ).read_text(encoding="utf-8")
                )
                profiles = json.loads(
                    (COST_PROFILES_DIR / f"{simulator_id}.json").read_text(
                        encoding="utf-8"
                    )
                )["profiles"]
                for capability in spec["capabilities"]:
                    data_axes = capability["data_axes"]
                    truth_requirements = capability["truth_requirements"]
                    required_extras = set(
                        benchmark_costs.required_extras_for_request(
                            data_axes,
                            truth_requirements,
                        )
                    )
                    supported_extras = (
                        set(capability.get("native_extras", []))
                        | set(capability.get("derivable_extras", []))
                        | required_extras
                    )
                    matched_profiles = [
                        profile
                        for profile in profiles
                        if profile["data_axes"] == data_axes
                        and profile["truth_requirements"] == truth_requirements
                    ]
                    covered_extras: set[str] = set()
                    for profile in matched_profiles:
                        covered_extras.update(profile.get("requested_extras", []))
                        covered_extras.update(required_extras)

                    self.assertEqual(
                        supported_extras - covered_extras,
                        set(),
                        msg=(
                            simulator_id,
                            data_axes,
                            truth_requirements,
                        ),
                    )

    def test_column_truth_cost_profiles_record_group_dimensions(self) -> None:
        for simulator_id in ACTIVE_SIMULATORS:
            with self.subTest(simulator_id=simulator_id):
                targets = benchmark_costs.resolve_simulator_targets(
                    selected_simulators=[
                        (simulator_id, CATALOG_SIMULATORS_ROOT / simulator_id)
                    ],
                    catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
                    cost_profiles_dir=COST_PROFILES_DIR,
                    param_overrides_dir=PARAM_OVERRIDES_DIR,
                    default_group_count=2,
                    profile_filters=[],
                )
                column_truth_profiles = [
                    profile
                    for profile in targets[0].profiles
                    if "column" in profile.truth_requirements["contexts"]
                ]
                spec_has_column_truth = any(
                    "column" in capability["truth_requirements"]["contexts"]
                    for capability in targets[0].spec["capabilities"]
                )
                if not spec_has_column_truth:
                    self.assertEqual(column_truth_profiles, [])
                    continue
                self.assertTrue(column_truth_profiles)
                self.assertTrue(
                    all(
                        profile.dimension_profile["group_count"] > 0
                        for profile in column_truth_profiles
                    )
                )
                self.assertTrue(
                    all(
                        profile.dimension_profile["population_count"] > 0
                        for profile in column_truth_profiles
                    )
                )

    def test_boolode_cost_profile_records_fixed_gene_dimension(self) -> None:
        targets = benchmark_costs.resolve_simulator_targets(
            selected_simulators=[("boolode", CATALOG_SIMULATORS_ROOT / "boolode")],
            catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
            cost_profiles_dir=COST_PROFILES_DIR,
            param_overrides_dir=PARAM_OVERRIDES_DIR,
            default_group_count=2,
            profile_filters=["single_cell_cells_trajectory_global_custom_default"],
        )

        profile = targets[0].profiles[0]
        params = benchmark_costs.apply_dimensions_to_params(
            base_params=profile.params,
            dimension_profile=profile.dimension_profile,
            size=benchmark_costs.SizePoint(genes=3, cells=5),
        )

        self.assertEqual(profile.dimension_profile["genes_param"], {"fixed": 3})
        self.assertEqual(params["num_cells"], 5)

    def test_sergio_cost_profile_maps_total_columns_to_cells_per_bin(self) -> None:
        targets = benchmark_costs.resolve_simulator_targets(
            selected_simulators=[("sergio", CATALOG_SIMULATORS_ROOT / "sergio")],
            catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
            cost_profiles_dir=COST_PROFILES_DIR,
            param_overrides_dir=PARAM_OVERRIDES_DIR,
            default_group_count=2,
            profile_filters=["single_cell_cells_steady_state_global_custom_default"],
        )

        profile = targets[0].profiles[0]
        params = benchmark_costs.apply_dimensions_to_params(
            base_params=profile.params,
            dimension_profile=profile.dimension_profile,
            size=benchmark_costs.SizePoint(genes=3, cells=4),
        )

        self.assertEqual(profile.dimension_profile["cells_param"]["param"], "number_sc")
        self.assertEqual(profile.dimension_profile["cells_param"]["multiplier_param"], "number_bins")
        self.assertEqual(params["number_sc"], 2)
        self.assertEqual(params["number_genes"], 3)

    def test_benchmark_thread_filter_respects_simulatorspec_threading(self) -> None:
        boolode_spec = json.loads(
            (CATALOG_SIMULATORS_ROOT / "boolode" / "simulatorspec.json").read_text(
                encoding="utf-8"
            )
        )
        dyngen_spec = json.loads(
            (CATALOG_SIMULATORS_ROOT / "dyngen" / "simulatorspec.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            benchmark_costs.filter_threads_for_simulator(
                simulator_id="boolode",
                spec=boolode_spec,
                requested_threads=[1, 2, 4, 8],
            ),
            [1],
        )
        self.assertEqual(
            benchmark_costs.filter_threads_for_simulator(
                simulator_id="dyngen",
                spec=dyngen_spec,
                requested_threads=[1, 2, 4, 8],
            ),
            [1, 2, 4, 8],
        )

    def test_run_filters_serial_simulator_threads_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            catalog_root = tmp_root / "catalog_simulators"
            wrappers_root = tmp_root / "wrappers"
            workdir = tmp_root / "benchmark_work"
            (catalog_root / "boolode").mkdir(parents=True)
            (wrappers_root / "boolode").mkdir(parents=True)
            shutil.copy2(
                CATALOG_SIMULATORS_ROOT / "boolode" / "simulatorspec.json",
                catalog_root / "boolode" / "simulatorspec.json",
            )

            with (
                patch.object(
                    benchmark_costs,
                    "run_container_once",
                    return_value=("ok", 1.25, 4321, None, ""),
                ) as run_container,
                patch.object(
                    benchmark_costs,
                    "allocate_simulator_workdir",
                    return_value=(workdir, None),
                ),
            ):
                exit_code = benchmark_costs.run(
                    [
                        "--catalog-simulators-root",
                        str(catalog_root),
                        "--wrappers-root",
                        str(wrappers_root),
                        "--param-overrides-dir",
                        str(PARAM_OVERRIDES_DIR),
                        "--cost-profiles-dir",
                        str(COST_PROFILES_DIR),
                        "--simulator",
                        "boolode",
                        "--profile",
                        "single_cell_cells_trajectory_global_custom_default",
                        "--threads",
                        "1,2,4",
                        "--ram-gb",
                        "1",
                        "--skip-build",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(run_container.call_count, 1)
            payload = json.loads(
                (catalog_root / "boolode" / "cost.json").read_text(encoding="utf-8")
            )
            profile = payload["profiles"][0]
            self.assertEqual(profile["benchmark_config"]["threads_tested"], [1])
            self.assertEqual(profile["runtime_points"][0]["threads"], 1)

    def test_run_plan_only_does_not_build_or_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            catalog_root = tmp_root / "catalog_simulators"
            wrappers_root = tmp_root / "wrappers"
            (catalog_root / "boolode").mkdir(parents=True)
            (wrappers_root / "boolode").mkdir(parents=True)
            shutil.copy2(
                CATALOG_SIMULATORS_ROOT / "boolode" / "simulatorspec.json",
                catalog_root / "boolode" / "simulatorspec.json",
            )

            with (
                patch.object(benchmark_costs, "build_image") as build_image,
                patch.object(benchmark_costs, "run_container_once") as run_container,
            ):
                exit_code = benchmark_costs.run(
                    [
                        "--catalog-simulators-root",
                        str(catalog_root),
                        "--wrappers-root",
                        str(wrappers_root),
                        "--param-overrides-dir",
                        str(PARAM_OVERRIDES_DIR),
                        "--cost-profiles-dir",
                        str(COST_PROFILES_DIR),
                        "--simulator",
                        "boolode",
                        "--profile",
                        "single_cell_cells_trajectory_global_custom_default",
                        "--threads",
                        "1,2",
                        "--ram-gb",
                        "1",
                        "--plan-only",
                    ]
                )

            self.assertEqual(exit_code, 0)
            build_image.assert_not_called()
            run_container.assert_not_called()
            self.assertFalse((catalog_root / "boolode" / "cost.json").exists())

    def test_run_container_timeout_removes_container(self) -> None:
        profile = benchmark_costs.SimulatorBenchmarkProfile(
            simulator_id="boolode",
            profile_id="profile",
            profile="single_cell_cells_trajectory_global",
            data_axes=copy.deepcopy(TRAJECTORY_AXES),
            truth_requirements=copy.deepcopy(GLOBAL_TRUTH),
            sizes=None,
            requested_extras=(),
            effective_extras=(),
            params={},
            params_profile={},
            runtime_resources_profile={},
            dimension_profile={},
            input_profile={},
            input_paths={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            docker_run_timeout = subprocess.TimeoutExpired(
                cmd=["docker", "run"],
                timeout=1,
            )
            cleanup_ok = subprocess.CompletedProcess(
                args=["docker", "rm", "-f", "container"],
                returncode=0,
                stdout="",
                stderr="",
            )
            with patch.object(
                benchmark_costs,
                "run_cmd",
                side_effect=[docker_run_timeout, cleanup_ok],
            ) as run_cmd:
                status, _elapsed, _bytes, _memory, error = benchmark_costs.run_container_once(
                    image_tag="image",
                    workdir=workdir,
                    simulator_id="boolode",
                    profile=profile,
                    params={},
                    threads=1,
                    ram_gb=1,
                    seed=1,
                    timeout_s=1,
                )

            self.assertEqual(status, "timeout")
            self.assertIn("Run exceeded timeout 1s", error)
            self.assertGreaterEqual(run_cmd.call_count, 2)
            cleanup_cmd = run_cmd.call_args_list[1].args[0]
            self.assertEqual(cleanup_cmd[:3], ["docker", "rm", "-f"])

    def test_run_container_requires_tf_list_artifact(self) -> None:
        profile = benchmark_costs.SimulatorBenchmarkProfile(
            simulator_id="boolode",
            profile_id="profile",
            profile="single_cell_cells_trajectory_global",
            data_axes=copy.deepcopy(TRAJECTORY_AXES),
            truth_requirements=copy.deepcopy(GLOBAL_TRUTH),
            sizes=None,
            requested_extras=(),
            effective_extras=("tf_list",),
            params={},
            params_profile={},
            runtime_resources_profile={},
            dimension_profile={},
            input_profile={},
            input_paths={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            out_dir = workdir / "out"

            def successful_container(*_args, **_kwargs):
                (out_dir / "truth").mkdir(parents=True, exist_ok=True)
                (out_dir / "simulator-output-manifest.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                (out_dir / "expression.tsv").write_text("gene\tc1\n", encoding="utf-8")
                (out_dir / "truth" / "networks.csv").write_text(
                    "source,target,weight\n", encoding="utf-8"
                )
                return subprocess.CompletedProcess(
                    args=["docker", "run"], returncode=0, stdout="", stderr=""
                )

            with patch.object(
                benchmark_costs, "run_cmd", side_effect=successful_container
            ):
                status, _elapsed, _bytes, _memory, error = (
                    benchmark_costs.run_container_once(
                        image_tag="image",
                        workdir=workdir,
                        simulator_id="boolode",
                        profile=profile,
                        params={},
                        threads=1,
                        ram_gb=1,
                        seed=1,
                        timeout_s=1,
                    )
                )

            self.assertEqual(status, "error")
            self.assertIn("extras/tf_list.txt", error)

    def test_run_writes_selected_simulator_cost_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            catalog_root = tmp_root / "catalog_simulators"
            wrappers_root = tmp_root / "wrappers"
            workdir = tmp_root / "benchmark_work"
            (catalog_root / "dyngen").mkdir(parents=True)
            (wrappers_root / "dyngen").mkdir(parents=True)
            shutil.copy2(
                CATALOG_SIMULATORS_ROOT / "dyngen" / "simulatorspec.json",
                catalog_root / "dyngen" / "simulatorspec.json",
            )

            with (
                patch.object(
                    benchmark_costs,
                    "run_container_once",
                    return_value=("ok", 1.25, 4321, None, ""),
                ),
                patch.object(
                    benchmark_costs,
                    "allocate_simulator_workdir",
                    return_value=(workdir, None),
                ),
            ):
                exit_code = benchmark_costs.run(
                    [
                        "--catalog-simulators-root",
                        str(catalog_root),
                        "--wrappers-root",
                        str(wrappers_root),
                        "--param-overrides-dir",
                        str(PARAM_OVERRIDES_DIR),
                        "--cost-profiles-dir",
                        str(COST_PROFILES_DIR),
                        "--simulator",
                        "dyngen",
                        "--profile",
                        "single_cell_cells_trajectory_global_linear_default",
                        "--size",
                        "10x10",
                        "--threads",
                        "1",
                        "--ram-gb",
                        "1",
                        "--skip-build",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads((catalog_root / "dyngen" / "cost.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "1.0")
            self.assertEqual(
                [profile["profile_id"] for profile in payload["profiles"]],
                ["single_cell_cells_trajectory_global_linear_default"],
            )
            point = payload["profiles"][0]["runtime_points"][0]
            self.assertEqual(point["seconds_p50"], 1.25)
            self.assertEqual(point["output_bytes_p50"], 4321)
            self.assertEqual(point["feature_vector"]["genes"], 10)
            self.assertEqual(point["feature_vector"]["cells"], 10)
            self.assertEqual(point["feature_vector"]["n_genes"], 10)
            self.assertEqual(point["feature_vector"]["n_cells"], 10)
            self.assertIsInstance(point["feature_vector"]["n_tfs"], int)
            self.assertEqual(
                point["feature_vector"]["benchmark_profile_id"],
                "single_cell_cells_trajectory_global_linear_default",
            )
            self.assertEqual(point["feature_vector"]["expression_profile"], "single_cell")
            self.assertEqual(point["feature_vector"]["column_kind"], "cells")
            self.assertEqual(point["feature_vector"]["experimental_design"], "trajectory")
            self.assertEqual(point["feature_vector"]["truth_context_families"], ["global"])
            self.assertEqual(point["feature_vector"]["extras"], ["tf_list"])
            self.assertFalse(point["feature_vector"]["column_truth_requested"])

    def test_merge_existing_cost_profiles_replaces_only_measured_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cost_path = Path(tmp) / "cost.json"
            benchmark_costs.save_json(
                cost_path,
                {
                    "schema_version": "1.0",
                    "profiles": [
                        {"profile_id": "kept", "marker": "old-kept"},
                        {"profile_id": "replaced", "marker": "old-replaced"},
                    ],
                },
            )
            measured = {
                "schema_version": "1.0",
                "profiles": [
                    {"profile_id": "replaced", "marker": "new-replaced"},
                    {"profile_id": "added", "marker": "new-added"},
                ],
            }

            merged = benchmark_costs.merge_existing_cost_profiles(
                cost_path=cost_path,
                measured_payload=measured,
            )

            self.assertEqual(
                [profile["profile_id"] for profile in merged["profiles"]],
                ["kept", "replaced", "added"],
            )
            self.assertEqual(merged["profiles"][0]["marker"], "old-kept")
            self.assertEqual(merged["profiles"][1]["marker"], "new-replaced")

    def test_unsuccessful_profile_preserves_existing_cost_and_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            catalog_root = tmp_root / "catalog_simulators"
            wrappers_root = tmp_root / "wrappers"
            workdir = tmp_root / "benchmark_work"
            simulator_dir = catalog_root / "dyngen"
            simulator_dir.mkdir(parents=True)
            (wrappers_root / "dyngen").mkdir(parents=True)
            shutil.copy2(
                CATALOG_SIMULATORS_ROOT / "dyngen" / "simulatorspec.json",
                simulator_dir / "simulatorspec.json",
            )
            cost_path = simulator_dir / "cost.json"
            benchmark_costs.save_json(cost_path, _valid_cost_payload())
            original_cost = cost_path.read_bytes()

            with (
                patch.object(
                    benchmark_costs,
                    "run_container_once",
                    side_effect=[
                        ("ok", 1.0, 100, None, ""),
                        ("error", 1.0, None, None, "simulated failure"),
                        ("ok", 1.0, 100, None, ""),
                    ],
                ),
                patch.object(
                    benchmark_costs,
                    "allocate_simulator_workdir",
                    return_value=(workdir, None),
                ),
            ):
                exit_code = benchmark_costs.run(
                    [
                        "--catalog-simulators-root",
                        str(catalog_root),
                        "--wrappers-root",
                        str(wrappers_root),
                        "--param-overrides-dir",
                        str(PARAM_OVERRIDES_DIR),
                        "--cost-profiles-dir",
                        str(COST_PROFILES_DIR),
                        "--simulator",
                        "dyngen",
                        "--profile",
                        "single_cell_cells_trajectory_global_linear_default",
                        "--size",
                        "10x10",
                        "--size",
                        "20x20",
                        "--size",
                        "30x30",
                        "--threads",
                        "1",
                        "--ram-gb",
                        "1",
                        "--skip-build",
                        "--merge-existing",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(cost_path.read_bytes(), original_cost)

    def test_partial_success_without_merge_does_not_replace_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            catalog_root = tmp_root / "catalog_simulators"
            wrappers_root = tmp_root / "wrappers"
            workdir = tmp_root / "benchmark_work"
            simulator_dir = catalog_root / "dyngen"
            simulator_dir.mkdir(parents=True)
            (wrappers_root / "dyngen").mkdir(parents=True)
            shutil.copy2(
                CATALOG_SIMULATORS_ROOT / "dyngen" / "simulatorspec.json",
                simulator_dir / "simulatorspec.json",
            )
            cost_path = simulator_dir / "cost.json"
            benchmark_costs.save_json(cost_path, _valid_cost_payload())
            original_cost = cost_path.read_bytes()

            with (
                patch.object(
                    benchmark_costs,
                    "run_container_once",
                    side_effect=[
                        ("error", 1.0, None, None, "simulated failure"),
                        ("ok", 1.0, 100, None, ""),
                    ],
                ),
                patch.object(
                    benchmark_costs,
                    "allocate_simulator_workdir",
                    return_value=(workdir, None),
                ),
            ):
                exit_code = benchmark_costs.run(
                    [
                        "--catalog-simulators-root",
                        str(catalog_root),
                        "--wrappers-root",
                        str(wrappers_root),
                        "--param-overrides-dir",
                        str(PARAM_OVERRIDES_DIR),
                        "--cost-profiles-dir",
                        str(COST_PROFILES_DIR),
                        "--simulator",
                        "dyngen",
                        "--profile",
                        "single_cell_cells_trajectory_global_linear_default",
                        "--profile",
                        "single_cell_cells_time_series_global_linear_default",
                        "--size",
                        "10x10",
                        "--threads",
                        "1",
                        "--ram-gb",
                        "1",
                        "--skip-build",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(cost_path.read_bytes(), original_cost)

    def test_profile_sizes_override_default_sizes_unless_cli_size_is_explicit(self) -> None:
        profile = benchmark_costs.SimulatorBenchmarkProfile(
            simulator_id="scmultisim",
            profile_id="profile",
            profile="single_cell_cells_trajectory_global",
            data_axes=copy.deepcopy(TRAJECTORY_AXES),
            truth_requirements=copy.deepcopy(GLOBAL_TRUTH),
            sizes=(
                benchmark_costs.SizePoint(genes=50, cells=20),
                benchmark_costs.SizePoint(genes=101, cells=40),
            ),
            requested_extras=(),
            effective_extras=(),
            params={},
            params_profile={},
            runtime_resources_profile={},
            dimension_profile={},
            input_profile={
                "requested_extras": [],
                "effective_extras": [],
                "required_inputs_satisfied": [],
                "optional_inputs_provided": [],
                "conditional_inputs_satisfied": [],
                "input_source_modes": {},
                "notes": [],
            },
            input_paths={},
        )
        defaults = [benchmark_costs.SizePoint(genes=100, cells=40)]

        implicit = benchmark_costs.sizes_for_profile(
            profile=profile,
            cli_sizes=defaults,
            cli_sizes_were_explicit=False,
        )
        explicit = benchmark_costs.sizes_for_profile(
            profile=profile,
            cli_sizes=defaults,
            cli_sizes_were_explicit=True,
        )

        self.assertEqual([(item.genes, item.cells) for item in implicit], [(50, 20), (101, 40)])
        self.assertEqual([(item.genes, item.cells) for item in explicit], [(100, 40)])

    def test_validator_accepts_generated_payload_semantics(self) -> None:
        payload = _valid_cost_payload()
        payload["profiles"][0]["benchmark_config"]["params_profile"][
            "resolved_base_params"
        ] = {
            "backbone_template": "linear",
            "distance_metric": "pearson",
        }

        errors = validate_simulator_costs.semantic_errors_for_cost(
            simulator_id="dyngen",
            instance=payload,
            catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
        )

        self.assertEqual(errors, [])

    def test_validator_rejects_profile_without_tf_list(self) -> None:
        payload = _valid_cost_payload()
        payload["profiles"][0]["benchmark_config"]["input_profile"][
            "effective_extras"
        ] = []

        errors = validate_simulator_costs.semantic_errors_for_cost(
            simulator_id="dyngen",
            instance=payload,
            catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
        )

        self.assertTrue(any("must include required tf_list" in item for item in errors))

    def test_validator_rejects_feature_vector_axis_mismatch(self) -> None:
        payload = _valid_cost_payload()
        payload["profiles"][0]["runtime_points"][0]["feature_vector"][
            "expression_profile"
        ] = "bulk"

        errors = validate_simulator_costs.semantic_errors_for_cost(
            simulator_id="dyngen",
            instance=payload,
            catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
        )

        self.assertTrue(
            any("feature_vector.expression_profile does not match" in item for item in errors),
            errors,
        )

    def test_validator_reports_incomplete_cost_coverage(self) -> None:
        payload = _valid_cost_payload()

        errors = validate_simulator_costs.coverage_errors_for_cost(
            simulator_id="dyngen",
            instance=payload,
            catalog_simulators_root=CATALOG_SIMULATORS_ROOT,
        )

        self.assertTrue(any("missing profile for capability" in item for item in errors))

    def test_validator_rejects_threads_tested_incompatible_with_simulatorspec(self) -> None:
        boolode_spec = json.loads(
            (CATALOG_SIMULATORS_ROOT / "boolode" / "simulatorspec.json").read_text(
                encoding="utf-8"
            )
        )
        profile = {
            "benchmark_config": {
                "threads_tested": [1, 2],
            },
            "runtime_points": [
                {"threads": 1},
                {"threads": 2},
            ],
        }

        errors = validate_simulator_costs.semantic_runtime_errors(
            spec=boolode_spec,
            runtime_profile={
                "threading_supported": False,
                "max_threads": 1,
            },
            profile=profile,
            prefix="profiles[1]",
        )

        self.assertTrue(any("threads_tested" in item for item in errors), errors)
        self.assertTrue(any("runtime_points[2].threads" in item for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
