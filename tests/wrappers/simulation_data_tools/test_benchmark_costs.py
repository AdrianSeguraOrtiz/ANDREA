from __future__ import annotations

import copy
import importlib.util
import json
import shutil
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
            "profile": "scrna_global",
            "profile_id": "scrna_global_linear_default",
            "genes": 10,
            "cells": 8,
            "groups": 0,
            "population_count": 0,
            "threads": 1,
            "ram_gb": 1.0,
            "cost_relevant_values": {},
        },
    }


def _valid_cost_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "profiles": [
            {
                "profile_id": "scrna_global_linear_default",
                "benchmark_config": {
                    "simulator_id": "dyngen",
                    "profile": "scrna_global",
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
                        "effective_extras": [],
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
    def test_simulatorcost_schema_accepts_profile_payload(self) -> None:
        schema = json.loads(SIMULATOR_COST_SCHEMA.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)

        errors = list(validator.iter_errors(_valid_cost_payload()))

        self.assertEqual(errors, [])

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
            profile_filters=["scrna_grouped_custom_grn_tree"],
        )

        self.assertEqual(len(targets), 1)
        profile = targets[0].profiles[0]
        self.assertEqual(profile.profile_id, "scrna_grouped_custom_grn_tree")
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
                if profile["id"] == "scrna_grouped_custom_grn_tree"
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
                    profile_filters=["scrna_grouped_custom_grn_tree"],
                )

    def test_cost_profile_configs_cover_supported_profiles(self) -> None:
        for simulator_id in ("dyngen", "scmultisim"):
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
                supported = set(spec["profile_capabilities"])
                configured = {profile.profile for profile in targets[0].profiles}

                self.assertEqual(supported - configured, set())

    def test_cell_specific_cost_profiles_record_group_dimensions(self) -> None:
        for simulator_id in ("dyngen", "scmultisim"):
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
                cell_profiles = [
                    profile
                    for profile in targets[0].profiles
                    if profile.profile == "scrna_cell_specific"
                ]

                self.assertTrue(cell_profiles)
                self.assertTrue(
                    all(
                        profile.dimension_profile["group_count"] > 0
                        for profile in cell_profiles
                    )
                )
                self.assertTrue(
                    all(
                        profile.dimension_profile["population_count"] > 0
                        for profile in cell_profiles
                    )
                )

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
                        "scrna_global_linear_default",
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
                ["scrna_global_linear_default"],
            )
            point = payload["profiles"][0]["runtime_points"][0]
            self.assertEqual(point["seconds_p50"], 1.25)
            self.assertEqual(point["output_bytes_p50"], 4321)
            self.assertEqual(point["feature_vector"]["genes"], 10)
            self.assertEqual(point["feature_vector"]["cells"], 10)
            self.assertEqual(point["feature_vector"]["n_genes"], 10)
            self.assertEqual(point["feature_vector"]["n_cells"], 10)
            self.assertIsInstance(point["feature_vector"]["n_tfs"], int)
            self.assertFalse(point["feature_vector"]["native_cell_truth_enabled"])

    def test_profile_sizes_override_default_sizes_unless_cli_size_is_explicit(self) -> None:
        profile = benchmark_costs.SimulatorBenchmarkProfile(
            simulator_id="scmultisim",
            profile_id="profile",
            profile="scrna_global",
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


if __name__ == "__main__":
    unittest.main()
