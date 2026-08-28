from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator

from andrea.core.commands.generate_data.pipeline import (
    _copy_dataset_from_stage,
    _validate_selected_native_outputs,
    _validate_truth_outputs,
    execute_generate_data,
    run_generate_data,
)
from andrea.core.commands.generate_data.backends import docker_runner
from andrea.core.commands.generate_data.plan import plan_generate_data_request
from andrea.core.commands.generate_data.request import (
    validate_simulation_plan,
    validate_simulator_inputs,
)
from andrea.core.commands.generate_data.selection import (
    evaluate_simulator_for_scenario,
    preflight_generate_data_scenario,
)
from andrea.core.commands.generate_data.semantic import required_extras_for_request
from andrea.core.commands.generate_data.shared import (
    ResolvedScenarioRequest,
    ResolvedSimulatorRun,
)
from andrea.core.commands.infer_network.plan import plan_infer_network
from andrea.core.commands.infer_network.preflight import preflight_infer_network


def _has_docker_runtime() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def _issue_messages(entry: dict[str, object], severity: str | None = None) -> list[str]:
    issues = entry.get("issues", [])
    if not isinstance(issues, list):
        return []
    messages: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if severity is not None and issue.get("severity") != severity:
            continue
        message = issue.get("message")
        if isinstance(message, str):
            messages.append(message)
    return messages


def _entry_by_id(entries: list[dict[str, object]], simulator_id: str) -> dict[str, object]:
    for entry in entries:
        if entry.get("simulator_id") == simulator_id:
            return entry
    raise AssertionError(f"Missing simulator entry: {simulator_id}")


TRAJECTORY_AXES = {
    "measurement": "rna_expression",
    "resolution": "single_cell",
    "column_kind": "cells",
    "experimental_design": "trajectory",
}
DIFFERENTIATION_AXES = {
    "measurement": "rna_expression",
    "resolution": "single_cell",
    "column_kind": "cells",
    "experimental_design": "differentiation",
}
STEADY_STATE_AXES = {
    "measurement": "rna_expression",
    "resolution": "single_cell",
    "column_kind": "cells",
    "experimental_design": "steady_state",
}
BULK_TIME_SERIES_AXES = {
    "measurement": "rna_expression",
    "resolution": "bulk",
    "column_kind": "timepoints",
    "experimental_design": "time_series",
}
SINGLE_CELL_TIME_SERIES_AXES = {
    "measurement": "rna_expression",
    "resolution": "single_cell",
    "column_kind": "cells",
    "experimental_design": "time_series",
}
SINGLE_CELL_PERTURBATIONAL_AXES = {
    "measurement": "rna_expression",
    "resolution": "single_cell",
    "column_kind": "cells",
    "experimental_design": "perturbational",
}
GLOBAL_TRUTH = {"contexts": ["global"]}
GROUP_TRUTH = {"contexts": ["global", "group"]}
COLUMN_TRUTH = {"contexts": ["global", "group", "column"]}


def _copy_json(value: dict[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(value))


def _truth_for_level(truth_level: str | None) -> dict[str, object]:
    if truth_level == "column":
        return _copy_json(COLUMN_TRUTH)
    if truth_level == "group":
        return _copy_json(GROUP_TRUTH)
    return _copy_json(GLOBAL_TRUTH)


def _axes_for_simulator(
    *,
    simulator_id: str | None = None,
    simulator_params: dict[str, object] | None = None,
) -> dict[str, object]:
    if simulator_id == "scmultisim":
        return _copy_json(DIFFERENTIATION_AXES)
    if simulator_id == "sergio":
        mode = str((simulator_params or {}).get("simulation_mode", "steady_state"))
        if mode == "differentiation":
            return _copy_json(DIFFERENTIATION_AXES)
        return _copy_json(STEADY_STATE_AXES)
    return _copy_json(TRAJECTORY_AXES)


def _axes_for_request_id(request_id: str) -> dict[str, object]:
    if request_id.startswith("scmultisim_"):
        return _copy_json(DIFFERENTIATION_AXES)
    if request_id.startswith("sergio_"):
        return _copy_json(DIFFERENTIATION_AXES)
    return _copy_json(TRAJECTORY_AXES)


def _request_stub(
    *,
    truth_level: str | None = None,
    data_axes: dict[str, object] | None = None,
    truth_requirements: dict[str, object] | None = None,
    **kwargs: object,
) -> SimpleNamespace:
    return SimpleNamespace(
        data_axes=data_axes or _copy_json(TRAJECTORY_AXES),
        truth_requirements=truth_requirements or _truth_for_level(truth_level),
        **kwargs,
    )


class GenerateDataDyngenTests(unittest.TestCase):
    def _write_scenario_request(
        self,
        base: Path,
        *,
        request_id: str,
        truth_level: str | None = None,
        data_axes: dict[str, object] | None = None,
        truth_requirements: dict[str, object] | None = None,
        requested_extras: list[str],
        inputs: dict[str, object] | None = None,
        organism: dict[str, object] | None = None,
    ) -> Path:
        payload = {
            "schema_version": "1.0",
            "id": request_id,
            "data_axes": data_axes or _axes_for_request_id(request_id),
            "truth_requirements": truth_requirements or _truth_for_level(truth_level),
            "organism": organism
            or {"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
            "requested_extras": requested_extras,
            "inputs": inputs or {},
        }
        request_path = base / "scenario.json"
        request_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        return request_path

    def _write_plan(
        self,
        base: Path,
        *,
        request_id: str,
        truth_level: str | None = None,
        data_axes: dict[str, object] | None = None,
        truth_requirements: dict[str, object] | None = None,
        simulator_id: str,
        requested_extras: list[str],
        simulator_params: dict[str, object],
        run_id: str = "dyngen_default",
        organism: dict[str, object] | None = None,
        replicates: int = 1,
        base_seed: int = 100,
        native_outputs: list[str] | None = None,
        inputs: dict[str, object] | None = None,
    ) -> Path:
        seeds = [base_seed + idx for idx in range(replicates)]
        runtime_resources = {"threads": 1}
        ram_gb = 4.0
        eta_seconds = 60.0
        eta_provenance = {
            "eta_source": "test_fixture",
            "warnings": [],
        }
        resolved_data_axes = data_axes or _axes_for_simulator(
            simulator_id=simulator_id,
            simulator_params=simulator_params,
        )
        resolved_truth_requirements = truth_requirements or _truth_for_level(truth_level)
        required_extras = set(
            required_extras_for_request(
                resolved_data_axes,
                resolved_truth_requirements,
            )
        )
        task_payloads = []
        waves = []
        for idx, seed in enumerate(seeds, start=1):
            task_id = f"{run_id}__r{idx:02d}"
            start = float((idx - 1) * eta_seconds)
            end = float(idx * eta_seconds)
            task_payload = {
                "task_id": task_id,
                "run_id": run_id,
                "simulator_id": simulator_id,
                "replicate_index": idx,
                "seed": seed,
                "dataset_id": f"{request_id}__{task_id}",
                "runtime_resources": runtime_resources,
                "ram_gb": ram_gb,
                "eta_seconds": eta_seconds,
                "eta_source": "test_fixture",
                "eta_start_seconds": start,
                "eta_end_seconds": end,
                "eta_wave": idx,
                "eta_provenance": eta_provenance,
            }
            task_payloads.append(task_payload)
            waves.append(
                {
                    "index": idx,
                    "threads_used": 1,
                    "ram_gb_used": ram_gb,
                    "eta_seconds": eta_seconds,
                    "eta_start_seconds": start,
                    "eta_end_seconds": end,
                    "tasks": [
                        {
                            "task_id": task_id,
                            "run_id": run_id,
                            "simulator_id": simulator_id,
                            "threads": 1,
                            "ram_gb": ram_gb,
                            "eta_seconds": eta_seconds,
                            "eta_source": "test_fixture",
                            "eta_start_seconds": start,
                            "eta_end_seconds": end,
                        }
                    ],
                }
            )
        payload = {
            "schema_version": "1.0",
            "id": request_id,
            "data_axes": resolved_data_axes,
            "truth_requirements": resolved_truth_requirements,
            "organism": organism
            or {"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
            "requested_extras": requested_extras,
            "effective_extras": sorted(set(requested_extras).union(required_extras)),
            "inputs": inputs or {},
            "base_seed": base_seed,
            "runs": [
                {
                    "run_id": run_id,
                    "simulator_id": simulator_id,
                    "simulator_params": simulator_params,
                    "runtime_resources": runtime_resources,
                    "ram_gb": ram_gb,
                    "eta_seconds": float(replicates * eta_seconds),
                    "eta_source": "test_fixture",
                    "eta_start_seconds": 0.0,
                    "eta_end_seconds": float(replicates * eta_seconds),
                    "eta_provenance": eta_provenance,
                    "replicates": replicates,
                    "native_outputs": native_outputs or [],
                    "base_seed": base_seed,
                    "replicate_seeds": seeds,
                }
            ],
            "tasks": task_payloads,
            "execution": {
                "max_parallel_tasks": 1,
                "max_cores": 1,
                "max_ram_gb": ram_gb,
                "eta_total_seconds": float(replicates * eta_seconds),
                "waves": waves,
                "warnings": [],
            },
        }
        plan_path = base / "simulation-plan.json"
        plan_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        return plan_path

    def _write_simulator_runs(
        self,
        base: Path,
        runs: list[dict[str, object]],
    ) -> Path:
        simulator_runs_path = base / "simulator-runs.json"
        simulator_runs_path.write_text(
            json.dumps(
                {"schema_version": "1.0", "runs": runs},
                indent=2,
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return simulator_runs_path

    def _write_tools_params(self, base: Path, runs: list[dict[str, object]]) -> Path:
        tools_params_path = base / "tools_params.json"
        tools_params_path.write_text(
            json.dumps({"runs": runs}, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        return tools_params_path

    def test_dataset_manifest_schema_requires_strict_taxonomic_organism(self) -> None:
        schema_path = (
            Path(__file__).resolve().parents[4]
            / "andrea"
            / "catalog_inference_tools"
            / "schemas"
            / "dataset-manifest.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)

        biological_manifest = {
            "schema_version": "1.0",
            "id": "bio_manifest",
            "dataset": {
                "spec": {
                    "schema_version": "1.0",
                    "id": "bio_ds",
                    "name": "bio_ds",
                    "expression": {
                        "genes": 2,
                        "columns": 2,
                        "column_kind": "samples",
                        "expression_profile": "bulk",
                    },
                    "organism": {"taxonomic_group": "animal", "ncbi_taxon_id": 9606},
                },
                "expression_matrix": "expression.tsv",
            },
        }
        synthetic_manifest = {
            "schema_version": "1.0",
            "id": "syn_manifest",
            "dataset": {
                "spec": {
                    "schema_version": "1.0",
                    "id": "syn_ds",
                    "name": "syn_ds",
                    "expression": {
                        "genes": 2,
                        "columns": 2,
                        "column_kind": "cells",
                        "expression_profile": "scrna",
                    },
                    "organism": {"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                },
                "expression_matrix": "expression.tsv",
            },
        }
        self.assertEqual(list(validator.iter_errors(biological_manifest)), [])
        self.assertEqual(list(validator.iter_errors(synthetic_manifest)), [])

    def test_preflight_classifies_dyngen_for_grouped_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scenario_path = self._write_scenario_request(
                Path(tmp),
                request_id="trajectory_grouped_lineage",
                truth_level="group",
                requested_extras=["lineage_tree"],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                report = preflight_generate_data_scenario(scenario_path)

        self.assertGreaterEqual(report["catalog_summary"]["total"], 1)
        self.assertGreaterEqual(report["catalog_summary"]["warning"], 1)
        dyngen_entry = _entry_by_id(report["warning"], "dyngen")
        self.assertEqual(
            dyngen_entry["truth_outputs"],
            [
                {"context": "global", "status": "native"},
                {"context": "group", "status": "derivable"},
            ],
        )
        self.assertIn("groups", dyngen_entry["derived_extras_used"])
        self.assertIn("lineage_tree", dyngen_entry["derived_extras_used"])
        self.assertTrue(
            any(
                "canonical truth context 'group:'" in message
                for message in _issue_messages(dyngen_entry, "warn")
            )
        )

    def test_plan_blocks_sergio_lineage_tree_without_differentiation_mode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target_path = (
                Path(__file__).resolve().parents[4]
                / "wrappers"
                / "simulation_data_tools"
                / "tests"
                / "fixtures"
                / "sergio"
                / "target_interactions.csv"
            )
            master_path = target_path.with_name("master_regulators.csv")
            scenario_path = self._write_scenario_request(
                base,
                request_id="sergio_lineage_without_differentiation",
                truth_level="group",
                requested_extras=["lineage_tree"],
                inputs={
                    "sergio_target_interactions": {"path": str(target_path)},
                    "sergio_master_regulators": {"path": str(master_path)},
                },
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "sergio_steady_lineage",
                        "simulator_id": "sergio",
                        "replicates": 1,
                        "params": {
                            "input_preset": "custom_files",
                            "simulation_mode": "steady_state",
                            "number_genes": 3,
                            "number_bins": 2,
                            "number_sc": 2,
                        },
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "simulation_mode.*controlled by the selected scenario",
                ),
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_sergio_differentiation_preflight_uses_bound_default_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="sergio_bound_preflight",
                truth_level="group",
                requested_extras=[],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                report = preflight_generate_data_scenario(scenario_path)
            sergio = _entry_by_id(
                report["warning"] + report["eligible"] + report["blocked"],
                "sergio",
            )
            self.assertNotEqual(sergio["status"], "blocked")
            self.assertNotIn(
                "differentiation data axes require simulation_mode=differentiation",
                "\n".join(_issue_messages(sergio, "block")),
            )

    def test_sergio_differentiation_plan_binds_design_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="sergio_bound_plan",
                truth_level="group",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "sergio_diff",
                        "simulator_id": "sergio",
                        "replicates": 1,
                        "params": {},
                    }
                ],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                planned_path = plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )
            plan = json.loads(planned_path.read_text(encoding="utf-8"))
            params = plan["runs"][0]["simulator_params"]
            self.assertEqual(params["simulation_mode"], "differentiation")
            self.assertEqual(params["input_preset"], "demo_differentiation")
            self.assertEqual(params["number_bins"], 3)

    def test_dyngen_trajectory_plan_rejects_synchronised_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="dyngen_trajectory_kind_binding",
                data_axes=_copy_json(TRAJECTORY_AXES),
                truth_requirements=_copy_json(GLOBAL_TRUTH),
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "dyngen_bad_kind",
                        "simulator_id": "dyngen",
                        "replicates": 1,
                        "params": {
                            "experiment_params": {
                                "kind": "synchronised"
                            }
                        },
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "experiment_params.kind.*controlled by the selected scenario",
                ),
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_dyngen_trajectory_plan_rejects_knockdown_simulations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="dyngen_trajectory_knockdown_binding",
                data_axes=_copy_json(TRAJECTORY_AXES),
                truth_requirements=_copy_json(GLOBAL_TRUTH),
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "dyngen_bad_knockdowns",
                        "simulator_id": "dyngen",
                        "replicates": 1,
                        "params": {
                            "simulation_params": {
                                "num_knockdown_simulations": 1
                            }
                        },
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "simulation_params.num_knockdown_simulations.*controlled by the selected scenario",
                ),
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_dyngen_time_series_plan_adds_required_timepoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="dyngen_time_series_extras",
                data_axes=_copy_json(SINGLE_CELL_TIME_SERIES_AXES),
                truth_requirements=_copy_json(GROUP_TRUTH),
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "dyngen_time_series",
                        "simulator_id": "dyngen",
                        "replicates": 1,
                        "params": {},
                    }
                ],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                planned_path = plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )
            plan = json.loads(planned_path.read_text(encoding="utf-8"))
            self.assertEqual(
                plan["effective_extras"], ["groups", "tf_list", "timepoints"]
            )
            params = plan["runs"][0]["simulator_params"]
            self.assertEqual(params["experiment_params"]["kind"], "synchronised")
            self.assertEqual(params["simulation_params"]["num_knockdown_simulations"], 0)

    def test_dyngen_perturbational_plan_adds_required_design_extras(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="dyngen_perturbational_extras",
                data_axes=_copy_json(SINGLE_CELL_PERTURBATIONAL_AXES),
                truth_requirements=_copy_json(GLOBAL_TRUTH),
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "dyngen_perturbational",
                        "simulator_id": "dyngen",
                        "replicates": 1,
                        "params": {},
                    }
                ],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                planned_path = plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )
            plan = json.loads(planned_path.read_text(encoding="utf-8"))
            self.assertEqual(
                plan["effective_extras"],
                ["interventions", "perturbation_design", "tf_list"],
            )
            params = plan["runs"][0]["simulator_params"]
            self.assertEqual(params["experiment_params"]["kind"], "snapshot")
            self.assertEqual(params["simulation_params"]["num_knockdown_simulations"], 4)
            self.assertEqual(params["knockdown_params"]["num_genes"], 1)

    def test_dyngen_perturbational_plan_rejects_multi_gene_knockdowns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="dyngen_perturbational_knockdown_binding",
                data_axes=_copy_json(SINGLE_CELL_PERTURBATIONAL_AXES),
                truth_requirements=_copy_json(GLOBAL_TRUTH),
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "dyngen_bad_knockdown_genes",
                        "simulator_id": "dyngen",
                        "replicates": 1,
                        "params": {
                            "knockdown_params": {
                                "num_genes": 2
                            }
                        },
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "knockdown_params.num_genes.*controlled by the selected scenario",
                ),
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_preflight_blocks_dyngen_when_docker_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scenario_path = self._write_scenario_request(
                Path(tmp),
                request_id="runtime_blocked",
                truth_level="group",
                requested_extras=["lineage_tree"],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                side_effect=RuntimeError(
                    "docker daemon is not available: Cannot connect to the Docker daemon"
                ),
            ):
                report = preflight_generate_data_scenario(scenario_path)

        self.assertGreaterEqual(report["catalog_summary"]["blocked"], 1)
        dyngen_entry = _entry_by_id(report["blocked"], "dyngen")
        self.assertTrue(
            any(
                "docker daemon is not available" in reason
                for reason in _issue_messages(dyngen_entry, "block")
            )
        )

    def test_preflight_rejects_unknown_simulator_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            custom_input = base / "custom.tsv"
            custom_input.write_text("id\tvalue\nx\t1\n", encoding="utf-8")
            scenario_path = self._write_scenario_request(
                base,
                request_id="unknown_input",
                truth_level="group",
                requested_extras=[],
                inputs={"custom_backbone": {"path": "custom.tsv"}},
            )
            with self.assertRaisesRegex(
                ValueError,
                "unknown simulator input id: custom_backbone",
            ):
                preflight_generate_data_scenario(scenario_path)

    def test_preflight_warns_when_scmultisim_global_truth_is_derived(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scenario_path = self._write_scenario_request(
                Path(tmp),
                request_id="scmultisim_default_inputs",
                truth_level="global",
                requested_extras=[],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                report = preflight_generate_data_scenario(scenario_path)

        scmultisim_entry = _entry_by_id(report["warning"], "scmultisim")
        self.assertTrue(
            any(
                "canonical truth context 'global'" in message
                for message in _issue_messages(scmultisim_entry, "warn")
            )
        )
        self.assertEqual(scmultisim_entry["inputs_used"], [])

    def test_validate_simulator_inputs_supports_native_output_conditions(
        self,
    ) -> None:
        simulator_spec = {
            "extra_inputs": {
                "required": [],
                "optional": [],
                "conditional_required": [
                    {
                        "input": "tree_newick",
                        "usage": "Used when a selected native output needs a user tree.",
                        "conditions": [
                            {
                                "field": "native_output",
                                "op": "eq",
                                "value": "cell_meta",
                            }
                        ],
                        "message": "tree_newick is required when native_output=cell_meta.",
                    }
                ],
            }
        }

        inactive = validate_simulator_inputs(
            simulator_id="toy",
            simulator_spec=simulator_spec,
            data_axes=_copy_json(TRAJECTORY_AXES),
            truth_requirements=_copy_json(GROUP_TRUTH),
            requested_extras=[],
            simulator_params={},
            native_outputs=[],
            input_ids=set(),
        )
        missing = validate_simulator_inputs(
            simulator_id="toy",
            simulator_spec=simulator_spec,
            data_axes=_copy_json(TRAJECTORY_AXES),
            truth_requirements=_copy_json(GROUP_TRUTH),
            requested_extras=[],
            simulator_params={},
            native_outputs=["cell_meta"],
            input_ids=set(),
        )
        satisfied = validate_simulator_inputs(
            simulator_id="toy",
            simulator_spec=simulator_spec,
            data_axes=_copy_json(TRAJECTORY_AXES),
            truth_requirements=_copy_json(GROUP_TRUTH),
            requested_extras=[],
            simulator_params={},
            native_outputs=["cell_meta"],
            input_ids={"tree_newick"},
        )

        self.assertEqual(inactive, [])
        self.assertEqual(
            missing,
            ["tree_newick is required when native_output=cell_meta."],
        )
        self.assertEqual(satisfied, [])

    def test_preflight_accepts_column_cumulative_truth(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scenario_path = self._write_scenario_request(
                Path(tmp),
                request_id="column_truth_contract",
                truth_level="column",
                requested_extras=[],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                report = preflight_generate_data_scenario(scenario_path)

        self.assertEqual(report["scenario"]["data_axes"], TRAJECTORY_AXES)
        self.assertEqual(report["scenario"]["truth_requirements"], COLUMN_TRUTH)
        self.assertEqual(_entry_by_id(report["eligible"], "dyngen")["status"], "eligible")

    def test_preflight_missing_truth_issue_names_required_contexts(self) -> None:
        scenario = ResolvedScenarioRequest(
            request_id="column_truth_context_issue",
            data_axes=_copy_json(TRAJECTORY_AXES),
            truth_requirements=_copy_json(COLUMN_TRUTH),
            organism={"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
            requested_extras=[],
            effective_extras=[],
            inputs={},
            resolved_input_paths={},
            base_seed=None,
            notes=None,
            request_payload={},
        )
        entry = evaluate_simulator_for_scenario(
            simulator_id="toy",
            spec={
                "name": "Toy",
                "params": {},
                "extra_inputs": {
                    "required": [],
                    "optional": [],
                    "conditional_required": [],
                },
                "compatibility_rules": [],
                "capabilities": [
                    {
                        "data_axes": _copy_json(TRAJECTORY_AXES),
                        "truth_requirements": _copy_json(COLUMN_TRUTH),
                        "native_extras": [],
                        "derivable_extras": [],
                        "truth_outputs": [
                            {"context": "global", "status": "native"},
                            {"context": "column", "status": "native"},
                        ],
                    }
                ],
            },
            scenario=scenario,
        )

        messages = _issue_messages(entry, "block")
        self.assertTrue(any("truth context" in message for message in messages))
        self.assertTrue(any("group:" in message for message in messages))

    def test_preflight_does_not_block_simulators_that_ignore_provided_inputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "regulatory_network.tsv").write_text(
                "target\tregulator\teffect\nG2\tG1\t1.0\n",
                encoding="utf-8",
            )
            scenario_path = self._write_scenario_request(
                base,
                request_id="unused_input_is_allowed",
                truth_level="global",
                requested_extras=[],
                inputs={"regulatory_network": {"path": "regulatory_network.tsv"}},
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                report = preflight_generate_data_scenario(scenario_path)

        self.assertEqual(_entry_by_id(report["eligible"], "dyngen")["status"], "eligible")
        self.assertTrue(
            any(
                "requested data_axes/truth_requirements are not supported" in message
                for message in _issue_messages(
                    _entry_by_id(report["blocked"], "scmultisim"),
                    "block",
                )
            )
        )

    def test_plan_rejects_scmultisim_input_grn_source_without_regulatory_network(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_missing_regulatory_network",
                truth_level="global",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_grn_input",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {"grn_source": "input_tsv"},
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                self.assertRaisesRegex(ValueError, "regulatory_network is required"),
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_plan_rejects_scmultisim_column_truth_when_dynamic_grn_disabled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_column_truth_dynamic_required",
                truth_level="column",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_static",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {
                            "dynamic_grn": {
                                "enabled": False,
                            }
                        },
                    }
                ],
            )
            with self.assertRaisesRegex(
                ValueError,
                "Column truth requires dynamic_grn.enabled=true",
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_plan_rejects_scmultisim_grouped_when_dynamic_grn_disabled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_grouped_dynamic_required",
                truth_level="group",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_static",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {
                            "dynamic_grn": {
                                "enabled": False,
                            }
                        },
                    }
                ],
            )
            with self.assertRaisesRegex(
                ValueError,
                "Group truth requires dynamic_grn.enabled=true",
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_plan_accepts_scmultisim_input_grn_source_with_regulatory_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            grn_path = base / "regulatory_network.tsv"
            grn_path.write_text(
                "target\tregulator\teffect\nG2\tG1\t1.0\nG3\tG1\t-0.5\n",
                encoding="utf-8",
            )
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_with_regulatory_network",
                truth_level="global",
                requested_extras=[],
                inputs={"regulatory_network": {"path": "regulatory_network.tsv"}},
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_grn_input",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {"grn_source": "input_tsv"},
                    }
                ],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                planned_path = plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )
            payload = json.loads(planned_path.read_text(encoding="utf-8"))
            resolved = validate_simulation_plan(planned_path)

        self.assertEqual(payload["inputs"]["regulatory_network"]["path"], str(grn_path))
        self.assertEqual(
            resolved.simulator_runs[0].simulator_params["grn_source"],
            "input_tsv",
        )

    def test_plan_rejects_scmultisim_builtin_100_with_too_few_genes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_builtin_100_too_few",
                truth_level="global",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_builtin_100",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {"grn_source": "builtin_100", "num_genes": 100},
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                self.assertRaisesRegex(ValueError, "greater than 100"),
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_plan_rejects_scmultisim_builtin_1139_with_too_few_genes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_builtin_1139_too_few",
                truth_level="global",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_builtin_1139",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {"grn_source": "builtin_1139", "num_genes": 1136},
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                self.assertRaisesRegex(ValueError, "greater than 1136"),
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_plan_rejects_scmultisim_input_grn_when_num_genes_is_not_larger(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            grn_path = base / "regulatory_network.tsv"
            grn_path.write_text(
                "target\tregulator\teffect\nG2\tG1\t1.0\nG3\tG1\t-0.5\n",
                encoding="utf-8",
            )
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_input_grn_too_few",
                truth_level="global",
                requested_extras=[],
                inputs={"regulatory_network": {"path": "regulatory_network.tsv"}},
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_input_grn",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {"grn_source": "input_tsv", "num_genes": 3},
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "greater than the number of unique genes",
                ),
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_plan_rejects_scmultisim_input_tree_preset_without_tree_newick(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_missing_tree_newick",
                truth_level="group",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_tree_input",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {"tree_preset": "input_newick"},
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                self.assertRaisesRegex(ValueError, "tree_newick is required"),
            ):
                plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )

    def test_plan_accepts_scmultisim_input_tree_preset_with_tree_newick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            tree_path = base / "tree_newick.txt"
            tree_path.write_text("(pop1:1,pop2:1);\n", encoding="utf-8")
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_with_tree_newick",
                truth_level="group",
                requested_extras=[],
                inputs={"tree_newick": {"path": "tree_newick.txt"}},
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_tree_input",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {"tree_preset": "input_newick"},
                    }
                ],
            )
            with patch(
                "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                return_value=None,
            ):
                planned_path = plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                )
            payload = json.loads(planned_path.read_text(encoding="utf-8"))
            resolved = validate_simulation_plan(planned_path)

        self.assertEqual(payload["inputs"]["tree_newick"]["path"], str(tree_path))
        self.assertEqual(
            resolved.simulator_runs[0].simulator_params["tree_preset"],
            "input_newick",
        )

    def test_execute_rejects_scmultisim_conditional_input_before_running(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="scmultisim_execute_missing_grn",
                truth_level="global",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "scmultisim_grn_input",
                        "simulator_id": "scmultisim",
                        "replicates": 1,
                        "params": {"grn_source": "input_tsv"},
                    }
                ],
            )
            with (
                patch(
                    "andrea.core.commands.generate_data.selection.ensure_docker_cli",
                    return_value=None,
                ),
                patch(
                    "andrea.core.commands.generate_data.pipeline.run_generate_data"
                ) as run_mock,
                self.assertRaisesRegex(ValueError, "regulatory_network is required"),
            ):
                execute_generate_data(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_dir=base / "out",
                    max_parallel_tasks=1,
                    show_progress=False,
                )
            run_mock.assert_not_called()

    def test_validate_plan_accepts_dyngen_grouped_lineage_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._write_plan(
                Path(tmp),
                request_id="dyngen_lineage_ok",
                truth_level="group",
                simulator_id="dyngen",
                requested_extras=["lineage_tree"],
                simulator_params={"num_cells": 10},
            )
            resolved = validate_simulation_plan(plan_path)

        self.assertEqual(resolved.data_axes, TRAJECTORY_AXES)
        self.assertEqual(resolved.truth_requirements, GROUP_TRUTH)
        self.assertEqual(len(resolved.simulator_runs), 1)
        self.assertEqual(resolved.simulator_runs[0].simulator_id, "dyngen")
        self.assertEqual(
            resolved.effective_extras, ["groups", "lineage_tree", "tf_list"]
        )

    def test_validate_plan_accepts_dyngen_native_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._write_plan(
                Path(tmp),
                request_id="dyngen_native_outputs_ok",
                truth_level="global",
                simulator_id="dyngen",
                requested_extras=[],
                simulator_params={"num_cells": 10},
                native_outputs=["milestone_network", "rna_velocity"],
            )
            resolved = validate_simulation_plan(plan_path)

        self.assertEqual(
            resolved.simulator_runs[0].native_outputs,
            ["milestone_network", "rna_velocity"],
        )

    def test_validate_plan_rejects_unknown_native_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._write_plan(
                Path(tmp),
                request_id="dyngen_native_outputs_bad",
                truth_level="global",
                simulator_id="dyngen",
                requested_extras=[],
                simulator_params={"num_cells": 10},
                native_outputs=["not_a_real_output"],
            )
            with self.assertRaisesRegex(
                ValueError,
                "does not support selected native outputs",
            ):
                validate_simulation_plan(plan_path)

    def test_validate_plan_rejects_unavailable_native_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._write_plan(
                Path(tmp),
                request_id="scmultisim_observed_counts_bad",
                truth_level="global",
                simulator_id="scmultisim",
                requested_extras=[],
                simulator_params={"num_cells": 10},
                run_id="scmultisim_default",
                native_outputs=["observed_counts"],
            )
            with self.assertRaisesRegex(
                ValueError,
                "observed_counts.*technical_noise.enabled",
            ):
                validate_simulation_plan(plan_path)

    def test_validate_plan_rejects_scmultisim_compatibility_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._write_plan(
                Path(tmp),
                request_id="scmultisim_builtin_100_bad_plan",
                truth_level="global",
                simulator_id="scmultisim",
                requested_extras=[],
                simulator_params={"grn_source": "builtin_100", "num_genes": 100},
                run_id="scmultisim_builtin_100",
            )
            with self.assertRaisesRegex(ValueError, "greater than 100"):
                validate_simulation_plan(plan_path)

    def test_validate_plan_accepts_conditional_native_output_when_available(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._write_plan(
                Path(tmp),
                request_id="scmultisim_observed_counts_ok",
                truth_level="global",
                simulator_id="scmultisim",
                requested_extras=[],
                simulator_params={
                    "num_cells": 10,
                    "technical_noise": {"enabled": True},
                },
                run_id="scmultisim_noise",
                native_outputs=["observed_counts"],
            )
            resolved = validate_simulation_plan(plan_path)

        self.assertEqual(
            resolved.simulator_runs[0].native_outputs,
            ["observed_counts"],
        )

    def test_validate_plan_rejects_unsupported_semantic_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._write_plan(
                Path(tmp),
                request_id="bulk_bad",
                truth_level="global",
                data_axes=_copy_json(BULK_TIME_SERIES_AXES),
                simulator_id="dyngen",
                requested_extras=[],
                simulator_params={},
            )
            with self.assertRaisesRegex(
                ValueError,
                "does not support requested data_axes/truth_requirements",
            ):
                validate_simulation_plan(plan_path)

    def test_validate_plan_accepts_same_simulator_with_distinct_run_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            payload = {
                "schema_version": "1.0",
                "id": "multi_sim",
                "data_axes": _copy_json(TRAJECTORY_AXES),
                "truth_requirements": _copy_json(GLOBAL_TRUTH),
                "organism": {"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                "requested_extras": [],
                "effective_extras": ["tf_list"],
                "inputs": {},
                "base_seed": 100,
                "runs": [
                    {
                        "run_id": "dyngen_a",
                        "simulator_id": "dyngen",
                        "simulator_params": {},
                        "runtime_resources": {"threads": 1},
                        "replicates": 2,
                        "base_seed": 100,
                        "replicate_seeds": [100, 101],
                    },
                    {
                        "run_id": "dyngen_b",
                        "simulator_id": "dyngen",
                        "simulator_params": {},
                        "runtime_resources": {"threads": 1},
                        "replicates": 2,
                        "base_seed": 102,
                        "replicate_seeds": [102, 103],
                    },
                ],
                "tasks": [
                    {
                        "task_id": "dyngen_a__r01",
                        "run_id": "dyngen_a",
                        "simulator_id": "dyngen",
                        "replicate_index": 1,
                        "seed": 100,
                        "dataset_id": "multi_sim__dyngen_a__r01",
                        "runtime_resources": {"threads": 1},
                    },
                    {
                        "task_id": "dyngen_a__r02",
                        "run_id": "dyngen_a",
                        "simulator_id": "dyngen",
                        "replicate_index": 2,
                        "seed": 101,
                        "dataset_id": "multi_sim__dyngen_a__r02",
                        "runtime_resources": {"threads": 1},
                    },
                    {
                        "task_id": "dyngen_b__r01",
                        "run_id": "dyngen_b",
                        "simulator_id": "dyngen",
                        "replicate_index": 1,
                        "seed": 102,
                        "dataset_id": "multi_sim__dyngen_b__r01",
                        "runtime_resources": {"threads": 1},
                    },
                    {
                        "task_id": "dyngen_b__r02",
                        "run_id": "dyngen_b",
                        "simulator_id": "dyngen",
                        "replicate_index": 2,
                        "seed": 103,
                        "dataset_id": "multi_sim__dyngen_b__r02",
                        "runtime_resources": {"threads": 1},
                    },
                ],
                "execution": {},
            }
            eta_provenance = {"eta_source": "test_fixture", "warnings": []}
            for run in payload["runs"]:
                run["ram_gb"] = 4.0
                run["eta_seconds"] = 120.0
                run["eta_source"] = "test_fixture"
                run["eta_start_seconds"] = 0.0
                run["eta_end_seconds"] = 120.0
                run["eta_provenance"] = eta_provenance
            waves = []
            for wave_idx, wave_tasks in enumerate(
                [payload["tasks"][:2], payload["tasks"][2:]], start=1
            ):
                start = float((wave_idx - 1) * 60)
                end = float(wave_idx * 60)
                for task in wave_tasks:
                    task["ram_gb"] = 4.0
                    task["eta_seconds"] = 60.0
                    task["eta_source"] = "test_fixture"
                    task["eta_start_seconds"] = start
                    task["eta_end_seconds"] = end
                    task["eta_wave"] = wave_idx
                    task["eta_provenance"] = eta_provenance
                waves.append(
                    {
                        "index": wave_idx,
                        "threads_used": 2,
                        "ram_gb_used": 8.0,
                        "eta_seconds": 60.0,
                        "eta_start_seconds": start,
                        "eta_end_seconds": end,
                        "tasks": [
                            {
                                "task_id": str(task["task_id"]),
                                "run_id": str(task["run_id"]),
                                "simulator_id": str(task["simulator_id"]),
                                "threads": 1,
                                "ram_gb": 4.0,
                                "eta_seconds": 60.0,
                                "eta_source": "test_fixture",
                                "eta_start_seconds": start,
                                "eta_end_seconds": end,
                            }
                            for task in wave_tasks
                        ],
                    }
                )
            payload["execution"] = {
                "max_parallel_tasks": 2,
                "max_cores": 2,
                "max_ram_gb": 8.0,
                "eta_total_seconds": 120.0,
                "waves": waves,
                "warnings": [],
            }
            plan_path = base / "simulation-plan.json"
            plan_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            resolved = validate_simulation_plan(plan_path)

        self.assertEqual(
            [run.run_id for run in resolved.simulator_runs],
            ["dyngen_a", "dyngen_b"],
        )
        self.assertEqual(resolved.simulator_runs[0].replicate_seeds, [100, 101])
        self.assertEqual(resolved.simulator_runs[1].replicate_seeds, [102, 103])
        self.assertEqual(resolved.execution["max_parallel_tasks"], 2)

    def test_plan_generates_valid_dyngen_simulation_plan_from_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="plan_dyngen",
                truth_level="group",
                requested_extras=["lineage_tree", "tf_list"],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "dyngen_small",
                        "simulator_id": "dyngen",
                        "replicates": 2,
                        "native_outputs": ["milestone_network", "progressions"],
                        "params": {"num_cells": 25},
                    },
                    {
                        "run_id": "dyngen_linear",
                        "simulator_id": "dyngen",
                        "replicates": 2,
                        "params": {"num_cells": 30, "backbone_template": "linear"},
                    },
                ],
            )
            output_path = base / "simulation-plan.json"
            planned_path = plan_generate_data_request(
                scenario_request_path=scenario_path,
                simulator_runs_path=simulator_runs_path,
                output_path=output_path,
                max_parallel_tasks=2,
            )
            payload = json.loads(planned_path.read_text(encoding="utf-8"))
            resolved = validate_simulation_plan(planned_path)

        self.assertEqual(planned_path, output_path)
        self.assertEqual(
            [run["run_id"] for run in payload["runs"]],
            ["dyngen_small", "dyngen_linear"],
        )
        self.assertEqual(payload["data_axes"], TRAJECTORY_AXES)
        self.assertEqual(payload["truth_requirements"], GROUP_TRUTH)
        self.assertEqual(payload["requested_extras"], ["lineage_tree", "tf_list"])
        simulator_params = payload["runs"][0]["simulator_params"]
        self.assertEqual(simulator_params["num_cells"], 25)
        self.assertEqual(
            payload["runs"][0]["native_outputs"],
            ["milestone_network", "progressions"],
        )
        self.assertIn("simulation_params", simulator_params)
        self.assertEqual(resolved.simulator_runs[0].run_id, "dyngen_small")
        self.assertEqual(
            resolved.simulator_runs[0].native_outputs,
            ["milestone_network", "progressions"],
        )
        self.assertEqual(len(resolved.tasks), 4)
        self.assertEqual(resolved.execution["max_parallel_tasks"], 2)
        self.assertEqual(
            resolved.effective_extras, ["groups", "lineage_tree", "tf_list"]
        )
        self.assertIn("eta_total_seconds", resolved.execution)
        self.assertGreaterEqual(resolved.execution["eta_total_seconds"], 0)

    def test_plan_uses_simulator_cost_profile_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="plan_cost_profile",
                truth_level="global",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "dyngen_costed",
                        "simulator_id": "dyngen",
                        "replicates": 1,
                        "params": {
                            "num_cells": 20,
                            "num_tfs": 2,
                            "num_targets": 6,
                            "num_hks": 2,
                        },
                    }
                ],
            )
            cost_payload = {
                "schema_version": "1.0",
                "profiles": [
                    {
                        "profile_id": "single_cell_cells_trajectory_global_test",
                        "benchmark_config": {
                            "simulator_id": "dyngen",
                            "data_axes": _copy_json(TRAJECTORY_AXES),
                            "truth_requirements": _copy_json(GLOBAL_TRUTH),
                            "sizes": [{"genes": 10, "cells": 20}],
                            "threads_tested": [2],
                            "ram_gb_tested": [4.0],
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
                                "upstream_mapping": "test",
                            },
                        },
                        "runtime_points": [
                            {
                                "genes": 10,
                                "cells": 20,
                                "groups": 0,
                                "population_count": 0,
                                "threads": 2,
                                "ram_gb": 4.0,
                                "status": "ok",
                                "repeats_total": 1,
                                "repeats_ok": 1,
                                "repeats_failed": 0,
                                "ok_rate": 1.0,
                                "failure_breakdown": {"oom": 0, "timeout": 0, "error": 0},
                                "seconds_p50": 5.0,
                                "seconds_p90": 7.0,
                                "output_bytes_p50": 1000,
                                "output_bytes_p90": 1000,
                                "peak_memory_mb_p50": None,
                                "peak_memory_mb_p90": None,
                                "feature_vector": {},
                            }
                        ],
                    }
                ],
            }
            with patch(
                "andrea.core.commands.generate_data.cost_planner._load_simulator_cost_payload",
                return_value=(cost_payload, []),
            ):
                planned_path = plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                    max_cores=2,
                    max_ram_gb=4.0,
                )
            payload = json.loads(planned_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["runs"][0]["runtime_resources"], {"threads": 2})
        self.assertEqual(payload["runs"][0]["ram_gb"], 4.0)
        self.assertEqual(payload["runs"][0]["eta_source"], "cost_profile")
        self.assertEqual(payload["tasks"][0]["eta_source"], "cost_profile")
        self.assertEqual(payload["execution"]["eta_total_seconds"], 7.0)
        self.assertEqual(len(payload["execution"]["waves"]), 1)
        features = payload["runs"][0]["eta_provenance"]["cost_profile"]["features"]
        self.assertEqual(features["data_axes"], TRAJECTORY_AXES)
        self.assertEqual(features["truth_requirements"], GLOBAL_TRUTH)
        self.assertEqual(features["expression_profile"], "single_cell")
        self.assertEqual(features["column_kind"], "cells")
        self.assertEqual(features["experimental_design"], "trajectory")
        self.assertEqual(features["truth_context_families"], ["global"])
        self.assertEqual(features["requested_extras"], [])
        self.assertEqual(features["effective_extras"], ["tf_list"])
        self.assertEqual(features["extras"], ["tf_list"])
        self.assertEqual(features["n_cells"], 20)
        self.assertEqual(features["n_genes"], 10)
        self.assertFalse(features["column_truth_requested"])

    def test_plan_uses_conservative_eta_fallback_without_cost_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scenario_path = self._write_scenario_request(
                base,
                request_id="plan_cost_fallback",
                truth_level="global",
                requested_extras=[],
            )
            simulator_runs_path = self._write_simulator_runs(
                base,
                [
                    {
                        "run_id": "dyngen_fallback",
                        "simulator_id": "dyngen",
                        "replicates": 1,
                        "params": {"num_cells": 12},
                    }
                ],
            )
            with patch(
                "andrea.core.commands.generate_data.cost_planner._load_simulator_cost_payload",
                return_value=(
                    None,
                    ["[dyngen] no cost.json found; using conservative fallback ETA."],
                ),
            ):
                planned_path = plan_generate_data_request(
                    scenario_request_path=scenario_path,
                    simulator_runs_path=simulator_runs_path,
                    output_path=base / "simulation-plan.json",
                    max_parallel_tasks=1,
                    max_cores=4,
                    max_ram_gb=8.0,
                )
            payload = json.loads(planned_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["runs"][0]["eta_source"], "fallback_no_cost")
        self.assertEqual(payload["tasks"][0]["eta_source"], "fallback_no_cost")
        self.assertEqual(payload["tasks"][0]["eta_wave"], 1)
        self.assertEqual(payload["execution"]["max_cores"], 4)
        self.assertEqual(payload["execution"]["max_ram_gb"], 8.0)
        self.assertEqual(payload["execution"]["waves"][0]["tasks"][0]["threads"], 1)
        self.assertIn("no cost.json found", payload["execution"]["warnings"][0])

    def test_simulation_plan_schema_requires_resource_waves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan_path = self._write_plan(
                base,
                request_id="missing_waves",
                truth_level="global",
                simulator_id="dyngen",
                requested_extras=[],
                simulator_params={"num_cells": 10},
            )
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            del payload["execution"]["waves"]
            plan_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "waves"):
                validate_simulation_plan(plan_path)

    def test_package_copy_preserves_unified_truth_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            stage_dir = base / "stage"
            dataset_dir = base / "dataset"
            (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
            (stage_dir / "provenance").mkdir(parents=True, exist_ok=True)
            (stage_dir / "expression.tsv").write_text(
                "gene\tC1\nG1\t1\nG2\t2\n", encoding="utf-8"
            )
            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\nG1,G2,1,+,simulated_truth,group:A\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "gene_universe.txt").write_text(
                "G1\nG2\n", encoding="utf-8"
            )
            (stage_dir / "simulator-output-manifest.json").write_text(
                '{"schema_version":"1.0"}\n',
                encoding="utf-8",
            )

            _copy_dataset_from_stage(
                stage_dir=stage_dir,
                dataset_dir=dataset_dir,
                dataset_manifest_payload={"schema_version": "1.0"},
                ground_truth_manifest_payload={"schema_version": "1.0"},
                simulator_run_payload={"schema_version": "1.0"},
            )

            self.assertTrue((dataset_dir / "truth" / "networks.csv").exists())
            self.assertTrue((dataset_dir / "truth" / "gene_universe.txt").exists())

    def test_validate_truth_outputs_requires_group_context_for_column_truth(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage_dir = Path(tmp)
            (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
            (stage_dir / "expression.tsv").write_text(
                "gene\tC1\nG1\t1\nG2\t2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "gene_universe.txt").write_text(
                "G1\nG2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\nG1,G2,1,+,simulated_truth,global\n",
                encoding="utf-8",
            )
            request = _request_stub(
                truth_level="column",
                simulator_spec={
                    "capabilities": [
                        {
                            "data_axes": _copy_json(TRAJECTORY_AXES),
                            "truth_requirements": _copy_json(COLUMN_TRUTH),
                            "truth_outputs": [
                                {"context": "column", "status": "native"}
                            ],
                        }
                    ]
                },
            )
            manifest = {
                "data_axes": _copy_json(TRAJECTORY_AXES),
                "truth_requirements": _copy_json(COLUMN_TRUTH),
                "expression": {
                    "path": "expression.tsv",
                    "genes": 2,
                    "columns": 1,
                    "column_kind": "cells",
                    "expression_profile": "scrna",
                },
                "truth": {
                    "gene_universe": "truth/gene_universe.txt",
                    "networks": "truth/networks.csv",
                },
            }

            with self.assertRaisesRegex(ValueError, "required context prefix: group:"):
                _validate_truth_outputs(
                    stage_dir=stage_dir,
                    dataset_id="cell_missing",
                    request=request,
                    simulator_manifest=manifest,
                )

    def test_validate_truth_outputs_requires_column_context_for_column_truth(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage_dir = Path(tmp)
            (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
            (stage_dir / "expression.tsv").write_text(
                "gene\tC1\nG1\t1\nG2\t2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "gene_universe.txt").write_text(
                "G1\nG2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\n"
                "G1,G2,1,+,simulated_truth,global\n"
                "G1,G2,1,+,simulated_truth,group:A\n",
                encoding="utf-8",
            )
            request = _request_stub(truth_level="column")
            manifest = {
                "data_axes": _copy_json(TRAJECTORY_AXES),
                "truth_requirements": _copy_json(COLUMN_TRUTH),
                "expression": {
                    "path": "expression.tsv",
                    "genes": 2,
                    "columns": 1,
                    "column_kind": "cells",
                    "expression_profile": "scrna",
                },
                "truth": {
                    "gene_universe": "truth/gene_universe.txt",
                    "networks": "truth/networks.csv",
                },
            }

            with self.assertRaisesRegex(ValueError, "required context prefix: column:"):
                _validate_truth_outputs(
                    stage_dir=stage_dir,
                    dataset_id="cell_missing",
                    request=request,
                    simulator_manifest=manifest,
                )

    def test_validate_truth_outputs_accepts_column_specific_contexts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage_dir = Path(tmp)
            (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
            (stage_dir / "extras").mkdir(parents=True, exist_ok=True)
            (stage_dir / "expression.tsv").write_text(
                "gene\tC1\tC2\nG1\t1\t0\nG2\t2\t3\n",
                encoding="utf-8",
            )
            (stage_dir / "extras" / "groups.tsv").write_text(
                "cell\tcluster\nC1\tA\nC2\tA\n",
                encoding="utf-8",
            )
            (stage_dir / "extras" / "tf_list.txt").write_text(
                "G1\n", encoding="utf-8"
            )
            (stage_dir / "truth" / "gene_universe.txt").write_text(
                "G1\nG2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\n"
                "G1,G2,1,+,simulated_truth,global\n"
                "G1,G2,1,+,simulated_truth,group:A\n"
                "G1,G2,1,+,simulated_truth,column:C1\n"
                "G1,G2,1,+,simulated_truth,column:C2\n",
                encoding="utf-8",
            )
            request = _request_stub(truth_level="column")
            manifest = {
                "data_axes": _copy_json(TRAJECTORY_AXES),
                "truth_requirements": _copy_json(COLUMN_TRUTH),
                "expression": {
                    "path": "expression.tsv",
                    "genes": 2,
                    "columns": 2,
                    "column_kind": "cells",
                    "expression_profile": "scrna",
                },
                "extras": {
                    "groups": "extras/groups.tsv",
                    "tf_list": "extras/tf_list.txt",
                },
                "truth": {
                    "gene_universe": "truth/gene_universe.txt",
                    "networks": "truth/networks.csv",
                },
            }

            paths = _validate_truth_outputs(
                stage_dir=stage_dir,
                dataset_id="cell_ok",
                request=request,
                simulator_manifest=manifest,
            )

        self.assertEqual(paths["networks"], "truth/networks.csv")

    def test_validate_truth_outputs_checks_column_context_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage_dir = Path(tmp)
            (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
            (stage_dir / "extras").mkdir(parents=True, exist_ok=True)
            (stage_dir / "extras" / "tf_list.txt").write_text(
                "G1\n", encoding="utf-8"
            )
            (stage_dir / "expression.tsv").write_text(
                "gene\tC1\tC2\nG1\t1\t0\nG2\t2\t3\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "gene_universe.txt").write_text(
                "G1\nG2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\n"
                "G1,G2,1,+,simulated_truth,global\n"
                "G1,G2,1,+,simulated_truth,column:C1\n",
                encoding="utf-8",
            )
            request = _request_stub(truth_level="global")
            manifest = {
                "data_axes": _copy_json(TRAJECTORY_AXES),
                "truth_requirements": _copy_json(GLOBAL_TRUTH),
                "expression": {
                    "path": "expression.tsv",
                    "genes": 2,
                    "columns": 2,
                    "column_kind": "cells",
                    "expression_profile": "scrna",
                },
                "extras": {"tf_list": "extras/tf_list.txt"},
                "truth": {
                    "gene_universe": "truth/gene_universe.txt",
                    "networks": "truth/networks.csv",
                },
            }

            paths = _validate_truth_outputs(
                stage_dir=stage_dir,
                dataset_id="column_ok",
                request=request,
                simulator_manifest=manifest,
            )

            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\n"
                "G1,G2,1,+,simulated_truth,global\n"
                "G1,G2,1,+,simulated_truth,column:C3\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "column contexts not present"):
                _validate_truth_outputs(
                    stage_dir=stage_dir,
                    dataset_id="column_bad",
                    request=request,
                    simulator_manifest=manifest,
                )

        self.assertEqual(paths["networks"], "truth/networks.csv")

    def test_validate_truth_outputs_accepts_lineage_tree_with_root_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage_dir = Path(tmp)
            (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
            (stage_dir / "extras").mkdir(parents=True, exist_ok=True)
            (stage_dir / "expression.tsv").write_text(
                "gene\tC1\tC2\nG1\t1\t0\nG2\t2\t3\n",
                encoding="utf-8",
            )
            (stage_dir / "extras" / "groups.tsv").write_text(
                "cell\tcluster\nC1\tA\nC2\tB\n",
                encoding="utf-8",
            )
            (stage_dir / "extras" / "lineage_tree.tsv").write_text(
                "child\tparent\tgain_rate\tloss_rate\n"
                "A\t__root__\t0\t0\n"
                "B\tA\t0.2\t0.1\n",
                encoding="utf-8",
            )
            (stage_dir / "extras" / "tf_list.txt").write_text(
                "G1\n", encoding="utf-8"
            )
            (stage_dir / "truth" / "gene_universe.txt").write_text(
                "G1\nG2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\n"
                "G1,G2,1,+,simulated_truth,global\n"
                "G1,G2,1,+,simulated_truth,group:A\n"
                "G1,G2,1,+,simulated_truth,group:B\n",
                encoding="utf-8",
            )
            request = _request_stub(truth_level="group")
            manifest = {
                "data_axes": _copy_json(TRAJECTORY_AXES),
                "truth_requirements": _copy_json(GROUP_TRUTH),
                "expression": {
                    "path": "expression.tsv",
                    "genes": 2,
                    "columns": 2,
                    "column_kind": "cells",
                    "expression_profile": "scrna",
                },
                "extras": {
                    "groups": "extras/groups.tsv",
                    "lineage_tree": "extras/lineage_tree.tsv",
                    "tf_list": "extras/tf_list.txt",
                },
                "truth": {
                    "gene_universe": "truth/gene_universe.txt",
                    "networks": "truth/networks.csv",
                },
            }

            paths = _validate_truth_outputs(
                stage_dir=stage_dir,
                dataset_id="lineage_ok",
                request=request,
                simulator_manifest=manifest,
            )

        self.assertEqual(paths["gene_universe"], "truth/gene_universe.txt")

    def test_validate_truth_outputs_rejects_lineage_tree_missing_group_child(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage_dir = Path(tmp)
            (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
            (stage_dir / "extras").mkdir(parents=True, exist_ok=True)
            (stage_dir / "expression.tsv").write_text(
                "gene\tC1\tC2\nG1\t1\t0\nG2\t2\t3\n",
                encoding="utf-8",
            )
            (stage_dir / "extras" / "groups.tsv").write_text(
                "cell\tcluster\nC1\tA\nC2\tB\n",
                encoding="utf-8",
            )
            (stage_dir / "extras" / "lineage_tree.tsv").write_text(
                "child\tparent\tgain_rate\tloss_rate\n"
                "B\tA\t0.2\t0.1\n",
                encoding="utf-8",
            )
            (stage_dir / "extras" / "tf_list.txt").write_text(
                "G1\n", encoding="utf-8"
            )
            (stage_dir / "truth" / "gene_universe.txt").write_text(
                "G1\nG2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\n"
                "G1,G2,1,+,simulated_truth,global\n"
                "G1,G2,1,+,simulated_truth,group:A\n"
                "G1,G2,1,+,simulated_truth,group:B\n",
                encoding="utf-8",
            )
            request = _request_stub(truth_level="group")
            manifest = {
                "data_axes": _copy_json(TRAJECTORY_AXES),
                "truth_requirements": _copy_json(GROUP_TRUTH),
                "expression": {
                    "path": "expression.tsv",
                    "genes": 2,
                    "columns": 2,
                    "column_kind": "cells",
                    "expression_profile": "scrna",
                },
                "extras": {
                    "groups": "extras/groups.tsv",
                    "lineage_tree": "extras/lineage_tree.tsv",
                    "tf_list": "extras/tf_list.txt",
                },
                "truth": {
                    "gene_universe": "truth/gene_universe.txt",
                    "networks": "truth/networks.csv",
                },
            }

            with self.assertRaisesRegex(
                ValueError, "must include every groups.tsv label as child"
            ):
                _validate_truth_outputs(
                    stage_dir=stage_dir,
                    dataset_id="lineage_bad",
                    request=request,
                    simulator_manifest=manifest,
                )

    def test_validate_truth_outputs_rejects_column_context_outside_expression_columns(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage_dir = Path(tmp)
            (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
            (stage_dir / "expression.tsv").write_text(
                "gene\tC1\nG1\t1\nG2\t2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "gene_universe.txt").write_text(
                "G1\nG2\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\n"
                "G1,G2,1,+,simulated_truth,global\n"
                "G1,G2,1,+,simulated_truth,group:A\n"
                "G1,G2,1,+,simulated_truth,column:C2\n",
                encoding="utf-8",
            )
            request = _request_stub(truth_level="column")
            manifest = {
                "data_axes": _copy_json(TRAJECTORY_AXES),
                "truth_requirements": _copy_json(COLUMN_TRUTH),
                "expression": {
                    "path": "expression.tsv",
                    "genes": 2,
                    "columns": 1,
                    "column_kind": "cells",
                    "expression_profile": "scrna",
                },
                "truth": {
                    "gene_universe": "truth/gene_universe.txt",
                    "networks": "truth/networks.csv",
                },
            }

            with self.assertRaisesRegex(ValueError, "not present in expression columns"):
                _validate_truth_outputs(
                    stage_dir=stage_dir,
                    dataset_id="cell_bad",
                    request=request,
                    simulator_manifest=manifest,
                )

    def test_package_copy_preserves_native_outputs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            stage_dir = base / "stage"
            dataset_dir = base / "dataset"
            (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
            (stage_dir / "native").mkdir(parents=True, exist_ok=True)
            (stage_dir / "provenance").mkdir(parents=True, exist_ok=True)
            (stage_dir / "expression.tsv").write_text(
                "gene\tC1\nG1\t1\nG2\t2\n", encoding="utf-8"
            )
            (stage_dir / "truth" / "networks.csv").write_text(
                "source,target,score,sign,evidence,context\nG1,G2,1,+,simulated_truth,global\n",
                encoding="utf-8",
            )
            (stage_dir / "truth" / "gene_universe.txt").write_text(
                "G1\nG2\n", encoding="utf-8"
            )
            (stage_dir / "native" / "rna_velocity.tsv").write_text(
                "gene\tC1\nG1\t0.1\n",
                encoding="utf-8",
            )
            (stage_dir / "simulator-output-manifest.json").write_text(
                '{"schema_version":"1.0"}\n',
                encoding="utf-8",
            )

            _copy_dataset_from_stage(
                stage_dir=stage_dir,
                dataset_dir=dataset_dir,
                dataset_manifest_payload={"schema_version": "1.0"},
                ground_truth_manifest_payload={"schema_version": "1.0"},
                simulator_run_payload={"schema_version": "1.0"},
            )

            self.assertTrue((dataset_dir / "native" / "rna_velocity.tsv").exists())
            self.assertTrue((dataset_dir / "truth" / "gene_universe.txt").exists())

    def test_selected_native_outputs_must_live_under_native_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage_dir = Path(tmp)
            (stage_dir / "provenance" / "raw").mkdir(parents=True)
            (stage_dir / "provenance" / "raw" / "rna_velocity.tsv").write_text(
                "gene\tC1\nG1\t0.1\n",
                encoding="utf-8",
            )
            request = ResolvedSimulatorRun(
                request_id="native_bad_path",
                data_axes=_copy_json(TRAJECTORY_AXES),
                truth_requirements=_copy_json(GLOBAL_TRUTH),
                run_id="dyngen_native_bad_path",
                simulator_id="dyngen",
                organism={"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                requested_extras=[],
                effective_extras=[],
                inputs={},
                resolved_input_paths={},
                simulator_params={},
                runtime_resources={"threads": 1},
                native_outputs=["rna_velocity"],
                replicates=1,
                base_seed=1,
                replicate_seeds=[1],
                notes=None,
                simulator_spec={},
            )
            manifest = {
                "native_outputs": {
                    "rna_velocity": "provenance/raw/rna_velocity.tsv",
                },
            }

            with self.assertRaisesRegex(ValueError, "must be under native/"):
                _validate_selected_native_outputs(
                    stage_dir=stage_dir,
                    dataset_id="native_bad_path",
                    request=request,
                    simulator_manifest=manifest,
                )

    def test_run_generate_data_freezes_reproducibility_assets_in_benchmark_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan_path = self._write_plan(
                base,
                request_id="dyngen_repro",
                truth_level="global",
                simulator_id="dyngen",
                run_id="dyngen_small",
                requested_extras=[],
                simulator_params={"num_cells": 10},
                native_outputs=["rna_velocity"],
            )

            def fake_run_simulator(**kwargs):  # noqa: ANN003
                stage_dir = kwargs["stage_dir"]
                stage_dir.mkdir(parents=True, exist_ok=True)
                (stage_dir / "truth").mkdir(parents=True, exist_ok=True)
                (stage_dir / "extras").mkdir(parents=True, exist_ok=True)
                (stage_dir / "native").mkdir(parents=True, exist_ok=True)
                (stage_dir / "provenance" / "raw").mkdir(parents=True, exist_ok=True)
                (stage_dir / "expression.tsv").write_text(
                    "gene\tC1\nG1\t1\nG2\t2\n",
                    encoding="utf-8",
                )
                (stage_dir / "native" / "rna_velocity.tsv").write_text(
                    "gene\tC1\nG1\t0.1\n",
                    encoding="utf-8",
                )
                (stage_dir / "truth" / "networks.csv").write_text(
                    "source,target,score,sign,evidence,context\nG1,G2,1,+,simulated_truth,global\n",
                    encoding="utf-8",
                )
                (stage_dir / "truth" / "gene_universe.txt").write_text(
                    "G1\nG2\n",
                    encoding="utf-8",
                )
                (stage_dir / "extras" / "tf_list.txt").write_text(
                    "G1\n", encoding="utf-8"
                )
                manifest_path = stage_dir / "simulator-output-manifest.json"
                manifest_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "simulator_id": "dyngen",
                            "data_axes": _copy_json(TRAJECTORY_AXES),
                            "truth_requirements": _copy_json(GLOBAL_TRUTH),
                            "seed": 100,
                            "expression": {
                                "path": "expression.tsv",
                                "genes": 2,
                                "columns": 1,
                                "column_kind": "cells",
                            },
                            "extras": {"tf_list": "extras/tf_list.txt"},
                            "native_outputs": {
                                "rna_velocity": "native/rna_velocity.tsv",
                            },
                            "truth": {
                                "gene_universe": "truth/gene_universe.txt",
                                "networks": "truth/networks.csv",
                            },
                            "provenance": {"raw_dir": "provenance/raw"},
                        },
                        indent=2,
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return manifest_path

            fake_preflight = {
                "schema_version": "1.0",
                "scenario": {
                    "id": "dyngen_repro",
                    "data_axes": _copy_json(TRAJECTORY_AXES),
                    "truth_requirements": _copy_json(GLOBAL_TRUTH),
                    "organism": {"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                    "requested_extras": [],
                    "effective_extras": ["tf_list"],
                    "inputs": {},
                    "base_seed": 100,
                },
                "catalog_summary": {
                    "total": 1,
                    "eligible": 1,
                    "warning": 0,
                    "blocked": 0,
                },
                "eligible": [],
                "warning": [],
                "blocked": [],
            }

            with (
                patch(
                    "andrea.core.commands.generate_data.pipeline._run_simulator",
                    side_effect=fake_run_simulator,
                ),
                patch(
                    "andrea.core.commands.generate_data.pipeline.preflight_generate_data_scenario",
                    return_value=fake_preflight,
                ),
            ):
                benchmark_root = run_generate_data(
                    plan_path=plan_path,
                    output_dir=base / "out",
                    show_progress=False,
                )

            self.assertTrue(
                (benchmark_root / "input" / "scenario-request.json").exists()
            )
            self.assertTrue((benchmark_root / "input" / "simulator-runs.json").exists())
            self.assertTrue((benchmark_root / "simulation-plan.json").exists())
            self.assertTrue((benchmark_root / "preflight-report.json").exists())
            self.assertTrue(
                (
                    benchmark_root
                    / "datasets"
                    / "dyngen_repro__dyngen_small__r01"
                    / "truth"
                    / "gene_universe.txt"
                ).exists()
            )

            frozen_plan = json.loads(
                (benchmark_root / "simulation-plan.json").read_text(encoding="utf-8")
            )
            frozen_runs = json.loads(
                (benchmark_root / "input" / "simulator-runs.json").read_text(
                    encoding="utf-8"
                )
            )
            benchmark_manifest = json.loads(
                (benchmark_root / "benchmark-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(frozen_plan["execution"]["max_parallel_tasks"], 1)
            self.assertEqual(
                frozen_runs["runs"][0]["native_outputs"],
                ["rna_velocity"],
            )
            self.assertEqual(
                benchmark_manifest["runs"][0]["runtime_resources"],
                {"threads": 1},
            )
            self.assertEqual(benchmark_manifest["runs"][0]["eta_source"], "test_fixture")
            self.assertEqual(benchmark_manifest["tasks"][0]["eta_wave"], 1)
            self.assertEqual(
                benchmark_manifest["execution"]["waves"][0]["tasks"][0]["task_id"],
                "dyngen_small__r01",
            )
            self.assertEqual(benchmark_manifest["inputs"], {})
            simulator_run = json.loads(
                (
                    benchmark_root
                    / "datasets"
                    / "dyngen_repro__dyngen_small__r01"
                    / "provenance"
                    / "simulator-run.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                simulator_run["runtime_resources"],
                {"threads": 1},
            )

            dataset_dir = (
                benchmark_root
                / "datasets"
                / "dyngen_repro__dyngen_small__r01"
            )
            ground_truth_manifest = json.loads(
                (dataset_dir / "ground-truth-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            tools_params_path = self._write_tools_params(
                base,
                [
                    {
                        "run_id": "identity_check",
                        "tool_id": "custom_identity_check",
                        "execution": {"mode": "global"},
                        "params": {},
                    }
                ],
            )
            custom_tools_path = base / "custom_tools.json"
            custom_tools_path.write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "run_id": "identity_check",
                                "name": "Identity check",
                                "docker_image": "example/identity-check:1.0",
                                "execution_mode": "global",
                                "extra_inputs": ["tf_list"],
                                "outputs": {"directed": True, "sign": "none"},
                            }
                        ]
                    },
                    indent=2,
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            dataset_manifest_path = dataset_dir / "dataset-manifest.json"
            inference_preflight = preflight_infer_network(
                dataset_manifest_path=dataset_manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
            )
            inference_run_dir = plan_infer_network(
                dataset_manifest_path=dataset_manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
                output_dir=base / "inference",
                planner="heuristic",
                preflight_report=inference_preflight,
            )
            inference_report = json.loads(
                (inference_run_dir / "run_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                inference_report["dataset"]["fingerprint"],
                ground_truth_manifest["dataset_fingerprint"],
            )

    def test_run_generate_data_preserves_failed_simulator_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan_path = self._write_plan(
                base,
                request_id="dyngen_fail",
                truth_level="global",
                simulator_id="dyngen",
                run_id="dyngen_small",
                requested_extras=[],
                simulator_params={"num_cells": 10},
            )

            def fake_run_simulator(**kwargs):  # noqa: ANN003
                stage_dir = kwargs["stage_dir"]
                raw_dir = stage_dir / "provenance" / "raw"
                raw_dir.mkdir(parents=True, exist_ok=True)
                (stage_dir / "progress.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "status": "failed",
                            "phase": "failed",
                            "message": "simulated docker failure",
                        },
                        indent=2,
                        ensure_ascii=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (raw_dir / "docker_wrapper.stderr.log").write_text(
                    "container killed\n",
                    encoding="utf-8",
                )
                raise RuntimeError(
                    "Docker simulator 'dyngen' failed with exit code 137"
                )

            with (
                patch(
                    "andrea.core.commands.generate_data.pipeline._run_simulator",
                    side_effect=fake_run_simulator,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "Failed run artifacts preserved under",
                ),
            ):
                run_generate_data(
                    plan_path=plan_path,
                    output_dir=base / "out",
                    show_progress=False,
                )

            benchmark_root = next((base / "out").glob("dyngen_fail_*"))
            self.assertTrue((benchmark_root / "simulation-plan.json").exists())
            failed_dir = (
                benchmark_root
                / "failed_runs"
                / "dyngen_fail__dyngen_small__r01"
            )
            self.assertTrue((failed_dir / "progress.json").exists())
            self.assertTrue(
                (
                    failed_dir
                    / "provenance"
                    / "raw"
                    / "docker_wrapper.stderr.log"
                ).exists()
            )
            failure = json.loads(
                (failed_dir / "failure.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failure["dataset_id"], "dyngen_fail__dyngen_small__r01")
            self.assertIn("exit code 137", failure["message"])

    def test_sergio_demo_differentiation_rejects_unsafe_sampling_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan_path = self._write_plan(
                base,
                request_id="sergio_unsafe_diff",
                truth_level="group",
                simulator_id="sergio",
                run_id="sergio_diff",
                requested_extras=[],
                simulator_params={
                    "input_preset": "demo_differentiation",
                    "simulation_mode": "differentiation",
                    "number_genes": 100,
                    "number_bins": 3,
                    "sampling_state": 15,
                },
                native_outputs=[
                    "clean_expression",
                    "unspliced_expression",
                    "spliced_expression",
                ],
            )

            with self.assertRaisesRegex(ValueError, "sampling_state=1"):
                validate_simulation_plan(plan_path)

    def test_sergio_demo_differentiation_rejects_unsafe_cell_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan_path = self._write_plan(
                base,
                request_id="sergio_unsafe_cells",
                truth_level="group",
                simulator_id="sergio",
                run_id="sergio_diff",
                requested_extras=[],
                simulator_params={
                    "input_preset": "demo_differentiation",
                    "simulation_mode": "differentiation",
                    "number_genes": 100,
                    "number_bins": 3,
                    "number_sc": 300,
                    "sampling_state": 1,
                    "noise_params": 0.3,
                    "noise_params_splice": 0.1,
                    "splice_ratio": 1.5,
                },
                native_outputs=[
                    "clean_expression",
                    "unspliced_expression",
                    "spliced_expression",
                ],
            )

            with self.assertRaisesRegex(ValueError, "number_sc <= 100"):
                validate_simulation_plan(plan_path)

    def test_docker_image_resolution_uses_local_before_pull(self) -> None:
        docker_runner._PULLED_IMAGES.clear()
        with (
            patch(
                "andrea.core.commands.generate_data.backends.docker_runner._docker_image_exists",
                return_value=True,
            ) as exists_mock,
            patch(
                "andrea.core.commands.generate_data.backends.docker_runner.pull_docker_image"
            ) as pull_mock,
        ):
            origin = docker_runner._ensure_docker_image(
                simulator_id="dyngen",
                image="example/dyngen:1.0.0",
            )

        self.assertEqual(origin, "local")
        exists_mock.assert_called_once_with("example/dyngen:1.0.0")
        pull_mock.assert_not_called()

    def test_docker_image_resolution_pulls_when_missing_locally(self) -> None:
        docker_runner._PULLED_IMAGES.clear()
        seen_after_pull = False

        def fake_exists(_image: str) -> bool:
            return seen_after_pull

        def fake_pull(_image: str) -> None:
            nonlocal seen_after_pull
            seen_after_pull = True

        with (
            patch(
                "andrea.core.commands.generate_data.backends.docker_runner._docker_image_exists",
                side_effect=fake_exists,
            ),
            patch(
                "andrea.core.commands.generate_data.backends.docker_runner.pull_docker_image",
                side_effect=fake_pull,
            ) as pull_mock,
        ):
            origin = docker_runner._ensure_docker_image(
                simulator_id="dyngen",
                image="example/dyngen:1.0.0",
            )

        self.assertEqual(origin, "pulled")
        pull_mock.assert_called_once_with("example/dyngen:1.0.0")

    def test_docker_runner_stages_declared_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            input_path = base / "regulatory_network.tsv"
            input_path.write_text(
                "target\tregulator\teffect\nG2\tG1\t1\n",
                encoding="utf-8",
            )
            stage_dir = base / "stage"

            request = ResolvedSimulatorRun(
                request_id="scenario",
                data_axes=_copy_json(DIFFERENTIATION_AXES),
                truth_requirements=_copy_json(GROUP_TRUTH),
                run_id="scmultisim_01",
                simulator_id="scmultisim",
                organism={"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
                requested_extras=[],
                effective_extras=[],
                inputs={
                    "regulatory_network": {
                        "path": str(input_path),
                    }
                },
                resolved_input_paths={
                    "regulatory_network": input_path,
                },
                simulator_params={},
                runtime_resources={"threads": 1},
                native_outputs=[],
                replicates=1,
                base_seed=1,
                replicate_seeds=[1],
                notes=None,
                simulator_spec={
                    "docker_image": "example/scmultisim:1.0.0",
                },
            )

            class FakeProcess:
                returncode = 0

                def __init__(self, cmd: list[str], **_kwargs: object) -> None:
                    out_mount = next(
                        entry for entry in cmd if entry.endswith(":/work/out")
                    )
                    host_out = Path(out_mount.rsplit(":", 1)[0])
                    (host_out / "simulator-output-manifest.json").write_text(
                        json.dumps(
                            {
                                "schema_version": "1.0",
                                "simulator_id": "scmultisim",
                                "seed": 1,
                                "data_axes": _copy_json(DIFFERENTIATION_AXES),
                                "truth_requirements": _copy_json(GROUP_TRUTH),
                                "expression": {
                                    "path": "expression.tsv",
                                    "genes": 2,
                                    "columns": 2,
                                    "column_kind": "cells",
                                },
                                "extras": {},
                                "native_outputs": {},
                                "truth": {
                                    "gene_universe": "truth/gene_universe.txt",
                                    "networks": "truth/networks.csv",
                                },
                                "provenance": {
                                    "raw_dir": "provenance/raw",
                                },
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )

                def poll(self) -> int:
                    return 0

                def wait(self) -> int:
                    return 0

            with (
                patch(
                    "andrea.core.commands.generate_data.backends.docker_runner._ensure_docker_cli"
                ),
                patch(
                    "andrea.core.commands.generate_data.backends.docker_runner._ensure_docker_image",
                    return_value="local",
                ),
                patch(
                    "andrea.core.commands.generate_data.backends.docker_runner.subprocess.Popen",
                    side_effect=FakeProcess,
                ),
            ):
                manifest_path = docker_runner.run_docker_simulator(
                    request=request,
                    seed=1,
                    stage_dir=stage_dir,
                    task_label="scmultisim_01__r01",
                    progress_poll_seconds=0.01,
                    show_progress=False,
                )

            self.assertEqual(
                manifest_path,
                stage_dir / "simulator-output-manifest.json",
            )
            frozen_request = json.loads(
                (
                    stage_dir
                    / "provenance"
                    / "raw"
                    / "docker_wrapper.request.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                frozen_request["mounted_inputs"],
                {"regulatory_network": "/work/inputs/regulatory_network"},
            )

    @unittest.skipUnless(
        _has_docker_runtime(), "docker runtime is required for dyngen tests"
    )
    def test_run_generate_data_dyngen_grouped_is_consumable_by_infer_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan_path = self._write_plan(
                base,
                request_id="dyngen_stage1",
                truth_level="group",
                simulator_id="dyngen",
                run_id="dyngen_small",
                requested_extras=["tf_list"],
                simulator_params={
                    "num_cells": 12,
                    "simulation_params": {
                        "num_simulations": 4,
                        "compute_dimred": False,
                    },
                    "experiment_params": {
                        "map_reference_cpm": False,
                        "map_reference_ls": False,
                    },
                },
            )
            benchmark_root = run_generate_data(
                plan_path=plan_path,
                output_dir=base / "out",
            )
            dataset_dir = (
                benchmark_root / "datasets" / "dyngen_stage1__dyngen_small__r01"
            )
            manifest_path = dataset_dir / "dataset-manifest.json"

            self.assertTrue((dataset_dir / "extras" / "groups.tsv").exists())
            self.assertTrue((dataset_dir / "extras" / "tf_list.txt").exists())
            self.assertTrue((dataset_dir / "truth" / "gene_universe.txt").exists())
            self.assertTrue((dataset_dir / "truth" / "networks.csv").exists())

            report = preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )
            self.assertEqual(
                report["dataset"]["dataset_id"],
                "dyngen_stage1__dyngen_small__r01",
            )

    @unittest.skipUnless(
        _has_docker_runtime(), "docker runtime is required for dyngen tests"
    )
    def test_run_generate_data_dyngen_grouped_lineage_tree_is_consumable_by_scmtni(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan_path = self._write_plan(
                base,
                request_id="dyngen_stage2",
                truth_level="group",
                simulator_id="dyngen",
                run_id="dyngen_lineage",
                requested_extras=["lineage_tree", "tf_list"],
                simulator_params={
                    "num_cells": 10,
                    "simulation_params": {
                        "num_simulations": 4,
                        "compute_dimred": False,
                    },
                    "experiment_params": {
                        "map_reference_cpm": False,
                        "map_reference_ls": False,
                    },
                },
            )
            benchmark_root = run_generate_data(
                plan_path=plan_path,
                output_dir=base / "out",
            )
            dataset_dir = (
                benchmark_root / "datasets" / "dyngen_stage2__dyngen_lineage__r01"
            )
            manifest_path = dataset_dir / "dataset-manifest.json"
            tools_params_path = self._write_tools_params(
                base,
                [
                    {
                        "run_id": "scmtni__01",
                        "tool_id": "scmtni",
                        "params": {"indep": False, "q": 0},
                    }
                ],
            )

            self.assertTrue((dataset_dir / "extras" / "groups.tsv").exists())
            self.assertTrue((dataset_dir / "extras" / "lineage_tree.tsv").exists())
            self.assertTrue((dataset_dir / "extras" / "tf_list.txt").exists())
            self.assertTrue((dataset_dir / "truth" / "gene_universe.txt").exists())
            self.assertTrue((dataset_dir / "truth" / "networks.csv").exists())
            self.assertTrue(
                (
                    dataset_dir / "provenance" / "raw" / "group_edge_activity.tsv"
                ).exists()
            )
            self.assertTrue(
                (
                    dataset_dir / "provenance" / "raw" / "group_active_networks.tsv"
                ).exists()
            )

            dataset_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                dataset_manifest["extras"]["lineage_tree"],
                "extras/lineage_tree.tsv",
            )
            simulator_run = json.loads(
                (dataset_dir / "provenance" / "simulator-run.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("lineage_tree", simulator_run["effective_extras"])
            self.assertEqual(
                simulator_run["simulator_output"]["extras"]["lineage_tree"],
                "extras/lineage_tree.tsv",
            )

            report = preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            self.assertEqual(
                report["dataset"]["dataset_id"],
                "dyngen_stage2__dyngen_lineage__r01",
            )
            self.assertIn("scmtni__01", report["runs"]["selected"])
            scmtni_issues = report["runs"].get("issues", {}).get("scmtni__01", [])
            self.assertFalse(
                any(
                    isinstance(issue, dict)
                    and issue.get("code") == "conditional_required"
                    for issue in scmtni_issues
                )
            )


if __name__ == "__main__":
    unittest.main()
