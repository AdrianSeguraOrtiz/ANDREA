from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from ._helpers import InferNetworkCoreTestCase


class InferNetworkPlanTests(InferNetworkCoreTestCase):
    def _cell_aggregated_toolspec(self) -> dict:
        return {
            "id": "fakecell",
            "docker_image": "fake/cell:latest",
            "execution_capabilities": ["column_native", "group_aggregated"],
            "runtime_resources": {
                "threading": {
                    "supported": False,
                    "default_threads": 1,
                    "max_threads": 1,
                    "upstream_mapping": "No upstream parallel runtime control.",
                }
            },
            "params": {},
            "outputs": {
                "directed": True,
                "sign": "mixed",
            },
            "extra_inputs": {
                "required": [],
                "optional": [],
                "conditional_required": [
                    {
                        "input": "groups",
                        "execution": "mode",
                        "op": "eq",
                        "value": "group_aggregated",
                        "usage": "Used by ANDREA to aggregate column-native network rows by group.",
                        "message": "groups is required when execution.mode=group_aggregated.",
                    }
                ],
            },
        }

    def _cell_aggregated_preflight(
        self,
        *,
        expression_path: Path,
        groups_path: Path,
    ) -> dict:
        return {
            "dataset": {
                "dataset_id": "toy_ds",
                "column_kind": "cells",
                "expression_profile": "scrna",
                "organism": {"taxonomic_group": "animal", "ncbi_taxon_id": 9606},
                "genes": 2,
                "columns": 3,
                "expression_matrix_path": str(expression_path),
                "extras": {"groups": str(groups_path)},
            },
            "runs": {
                "selected": ["cellrun"],
                "catalog_tool_ids": {"cellrun": "fakecell"},
                "tool_origins": {"cellrun": "catalog"},
                "resolved_params": {"cellrun": {}},
                "resolved_execution": {"cellrun": {"mode": "group_aggregated"}},
                "issues": {"cellrun": []},
                "skipped": {},
            },
        }

    def test_group_aggregated_plan_uses_column_native_physical_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            expression_path = self._write_expression_matrix(
                base,
                lines=[
                    "gene\tC1\tC2\tC3",
                    "G1\t1\t2\t3",
                    "G2\t4\t5\t6",
                ],
            )
            groups_path = base / "groups.tsv"
            groups_path.write_text(
                "cell\tcluster\nC1\tA\nC2\tA\nC3\tB\n",
                encoding="utf-8",
            )
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                genes=2,
                columns=3,
                column_kind="cells",
                expression_profile="scrna",
                extras={"groups": "groups.tsv"},
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "cellrun",
                        "tool_id": "fakecell",
                        "execution": {"mode": "group_aggregated"},
                        "params": {},
                    }
                ],
            )
            preflight = self._cell_aggregated_preflight(
                expression_path=expression_path,
                groups_path=groups_path,
            )

            with patch(
                "andrea.core.commands.infer_network.plan._load_toolspec",
                return_value=self._cell_aggregated_toolspec(),
            ):
                run_dir = self.mod.plan_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                    output_dir=output_dir,
                    planner="heuristic",
                    preflight_report=preflight,
                )

            plan_payload = json.loads(
                (run_dir / "plan.json").read_text(encoding="utf-8")
            )

        logical_run = plan_payload["runs"][0]
        self.assertEqual(logical_run["execution"]["mode"], "group_aggregated")
        physical = logical_run["physical_tasks"][0]
        self.assertEqual(physical["task_id"], "cellrun__column_native")
        self.assertEqual(physical["postprocess"], "group_aggregated_mean_signed_effect")
        self.assertIn("upstream_column_native", physical["output_dir"])
        wave_task = plan_payload["waves"][0]["tasks"][0]
        self.assertEqual(wave_task["tool_id"], "cellrun__column_native")
        self.assertEqual(wave_task["run_id"], "cellrun")
        self.assertEqual(
            wave_task["eta_provenance"]["group_aggregation"]["rule"],
            "mean_signed_effect_by_group",
        )
        self.assertEqual(
            wave_task["eta_provenance"]["cost_features"]["execution_mode"],
            "group_aggregated",
        )
        self.assertEqual(
            wave_task["eta_provenance"]["cost_features"]["aggregation_step"],
            "column_to_group",
        )
        self.assertEqual(
            wave_task["eta_provenance"]["cost_features"][
                "upstream_cost_execution_mode"
            ],
            "column_native",
        )

    def test_group_aggregated_plan_rejects_toolspec_without_column_native(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            expression_path = self._write_expression_matrix(
                base,
                lines=[
                    "gene\tC1\tC2\tC3",
                    "G1\t1\t2\t3",
                    "G2\t4\t5\t6",
                ],
            )
            groups_path = base / "groups.tsv"
            groups_path.write_text(
                "cell\tcluster\nC1\tA\nC2\tA\nC3\tB\n",
                encoding="utf-8",
            )
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                genes=2,
                columns=3,
                column_kind="cells",
                expression_profile="scrna",
                extras={"groups": "groups.tsv"},
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "cellrun",
                        "tool_id": "fakecell",
                        "execution": {"mode": "group_aggregated"},
                        "params": {},
                    }
                ],
            )
            preflight = self._cell_aggregated_preflight(
                expression_path=expression_path,
                groups_path=groups_path,
            )
            malformed = self._cell_aggregated_toolspec()
            malformed["execution_capabilities"] = ["group_aggregated"]

            with (
                patch(
                    "andrea.core.commands.infer_network.plan._load_toolspec",
                    return_value=malformed,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "execution_capabilities includes 'group_aggregated'.*'column_native'",
                ),
            ):
                self.mod.plan_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                    output_dir=output_dir,
                    planner="heuristic",
                    preflight_report=preflight,
                )

    def test_plan_rejects_preflight_from_a_different_dataset_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "first").mkdir()
            first_manifest, tools_params = self._write_dataset_bundle(
                base / "first",
                tf_values=["G1"],
            )
            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=first_manifest,
                tools_params_path=tools_params,
            )

            (base / "second").mkdir()
            second_manifest, _unused_tools = self._write_dataset_bundle(
                base / "second",
                tf_values=["G2"],
            )
            second_payload = json.loads(second_manifest.read_text(encoding="utf-8"))
            second_payload["dataset"]["spec"]["id"] = "other_ds"
            second_payload["dataset"]["spec"]["name"] = "other_ds"
            second_manifest.write_text(
                json.dumps(second_payload),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "preflight_report.dataset does not match dataset_manifest_path",
            ):
                self.mod.plan_infer_network(
                    dataset_manifest_path=second_manifest,
                    tools_params_path=tools_params,
                    output_dir=base / "out",
                    planner="heuristic",
                    preflight_report=preflight,
                )

    def test_plan_requires_exact_preflight_tool_identity_maps(self) -> None:
        for case in ("missing_origin", "extra_origin", "extra_catalog_id"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                manifest_path, tools_params_path = self._write_dataset_bundle(
                    base,
                    tf_values=["G1", "G2"],
                )
                preflight = self.mod.preflight_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                )
                if case == "missing_origin":
                    del preflight["runs"]["tool_origins"]["aracne__01"]
                    field = "tool_origins"
                elif case == "extra_origin":
                    preflight["runs"]["tool_origins"]["ghost"] = "custom"
                    field = "tool_origins"
                else:
                    preflight["runs"]["catalog_tool_ids"]["ghost"] = "genie3"
                    field = "catalog_tool_ids"

                with self.assertRaisesRegex(
                    ValueError,
                    rf"preflight_report runs\.{field} must be an object with exactly",
                ):
                    self.mod.plan_infer_network(
                        dataset_manifest_path=manifest_path,
                        tools_params_path=tools_params_path,
                        output_dir=base / "out",
                        planner="heuristic",
                        preflight_report=preflight,
                    )

    def test_preflight_and_plan_generate_frozen_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )

            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            self.assertIn("catalog", preflight)
            self.assertIn("runs", preflight)
            self.assertEqual(preflight["runs"]["selected"], ["aracne__01"])
            self.assertEqual(preflight["inputs"]["dataset_id"], "toy_ds")
            self.assertEqual(preflight["inputs"]["tools_params"], "provided")
            self.assertNotIn("tools_root", preflight["inputs"])
            self.assertNotIn("schemas_dir", preflight["inputs"])

            run_dir = self.mod.plan_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                output_dir=output_dir,
                planner="heuristic",
                preflight_report=preflight,
            )
            self.assertTrue((run_dir / "plan.json").exists())
            self.assertTrue((run_dir / "preflight_report.json").exists())
            self.assertTrue((run_dir / "run_report.json").exists())
            self.assertTrue((run_dir / "input" / "dataset-manifest.json").exists())
            self.assertTrue((run_dir / "input" / "tools_params.json").exists())
            self.assertTrue((run_dir / "input" / "expression.tsv").exists())
            self.assertTrue((run_dir / "input" / "extra" / "tf_list.txt").exists())
            self.assertTrue(
                (run_dir / "tools" / "aracne__01" / "resolved_params.json").exists()
            )

            plan_payload = json.loads(
                (run_dir / "plan.json").read_text(encoding="utf-8")
            )
            self.assertIn("input_fingerprints", plan_payload)
            self.assertTrue(plan_payload["input_fingerprints"])
            first_wave_task = plan_payload["waves"][0]["tasks"][0]
            self.assertEqual(first_wave_task["eta_source"], "cost_profile")
            self.assertIn("eta_provenance", first_wave_task)
            self.assertIn(
                "profile_id",
                first_wave_task["eta_provenance"]["cost_profile"],
            )
            first_physical_task = plan_payload["runs"][0]["physical_tasks"][0]
            self.assertEqual(first_physical_task["eta_source"], "cost_profile")
            self.assertIn("eta_provenance", first_physical_task)

            report_payload = json.loads(
                (run_dir / "run_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report_payload["status"], "planned")
            self.assertEqual(
                report_payload["inputs"]["dataset_manifest_path"],
                "input/dataset-manifest.json",
            )
            self.assertEqual(
                report_payload["inputs"]["tools_params_path"],
                "input/tools_params.json",
            )
            self.assertNotIn("tools_root", report_payload["inputs"])
            self.assertNotIn("schemas_dir", report_payload["inputs"])

    def test_plan_freezes_custom_tool_registry_and_marks_custom_tasks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "demo_tool_01",
                        "tool_id": "custom_demo_tool_01",
                        "execution": {"mode": "global"},
                        "params": {"threshold": 0.25},
                    }
                ],
            )
            custom_tools_path = self._write_custom_tools(
                base,
                tools=[
                    {
                        "run_id": "demo_tool_01",
                        "name": "Demo Tool",
                        "docker_image": "example/demo-tool:1.0",
                        "execution_mode": "global",
                        "extra_inputs": [],
                        "outputs": {"directed": True, "sign": "mixed"},
                    }
                ],
            )
            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
            )

            run_dir = self.mod.plan_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
                output_dir=output_dir,
                planner="heuristic",
                preflight_report=preflight,
            )

            frozen_custom_tools = json.loads(
                (run_dir / "input" / "custom_tools.json").read_text(encoding="utf-8")
            )
            plan_payload = json.loads(
                (run_dir / "plan.json").read_text(encoding="utf-8")
            )
            report_payload = json.loads(
                (run_dir / "run_report.json").read_text(encoding="utf-8")
            )

        self.assertEqual(frozen_custom_tools["tools"][0]["run_id"], "demo_tool_01")
        self.assertEqual(
            report_payload["inputs"]["custom_tools_path"],
            "input/custom_tools.json",
        )
        self.assertEqual(
            report_payload["tools"]["tool_origins"]["demo_tool_01"],
            "custom",
        )
        self.assertEqual(
            report_payload["tools"]["output_capabilities"]["demo_tool_01"],
            {
                "tool_origin": "custom",
                "catalog_tool_id": "custom_demo_tool_01",
                "directed": True,
                "sign": "mixed",
            },
        )
        logical_run = plan_payload["runs"][0]
        self.assertEqual(logical_run["tool_id"], "custom_demo_tool_01")
        self.assertEqual(logical_run["tool_origin"], "custom")
        first_task = plan_payload["waves"][0]["tasks"][0]
        self.assertTrue(first_task["network_disabled"])
        self.assertEqual(first_task["eta_source"], "fallback_no_cost")
        self.assertTrue(
            any(
                "external Docker tool" in warning
                or "no cost.json for external Docker tool" in warning
                for warning in plan_payload["warnings"]
            )
        )

    def test_plan_accepts_minimal_custom_tool_schema_with_run_id(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, _default_tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "my_method_01",
                        "tool_id": "custom_my_method_01",
                        "execution": {"mode": "global"},
                        "params": {"alpha": 0.1, "use_prior": True},
                    }
                ],
            )
            custom_tools_path = self._write_custom_tools(
                base,
                tools=[
                    {
                        "run_id": "my_method_01",
                        "name": "My GRN method",
                        "docker_image": "registry.example.org/user/tool:1.0.0",
                        "execution_mode": "global",
                        "extra_inputs": ["tf_list"],
                        "outputs": {"directed": True, "sign": "signed"},
                    }
                ],
            )
            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
            )

            run_dir = self.mod.plan_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
                output_dir=output_dir,
                planner="heuristic",
                preflight_report=preflight,
            )

            frozen_custom_tools = json.loads(
                (run_dir / "input" / "custom_tools.json").read_text(encoding="utf-8")
            )
            frozen_tools_params = json.loads(
                (run_dir / "input" / "tools_params.json").read_text(encoding="utf-8")
            )
            plan_payload = json.loads(
                (run_dir / "plan.json").read_text(encoding="utf-8")
            )

        frozen_tool = frozen_custom_tools["tools"][0]
        self.assertEqual(frozen_tool["run_id"], "my_method_01")
        self.assertEqual(frozen_tool["name"], "My GRN method")
        self.assertEqual(frozen_tool["extra_inputs"], ["tf_list"])
        self.assertEqual(
            frozen_tools_params["runs"][0]["params"],
            {"alpha": 0.1, "use_prior": True},
        )
        self.assertEqual(plan_payload["runs"][0]["tool_id"], "custom_my_method_01")

    def test_cost_profile_warnings_are_planning_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            warning = "[aracne3] no cost.json found; using fallback estimation."
            with patch(
                "andrea.core.commands.infer_network.plan._load_tool_cost_profile",
                return_value=(None, [warning]),
            ):
                run_dir = self.mod.plan_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                    output_dir=output_dir,
                    planner="heuristic",
                    preflight_report=preflight,
                )

            plan_payload = json.loads(
                (run_dir / "plan.json").read_text(encoding="utf-8")
            )
            report_payload = json.loads(
                (run_dir / "run_report.json").read_text(encoding="utf-8")
            )

        self.assertIn(warning, plan_payload["warnings"])
        self.assertEqual(report_payload["issues"], [])

    def test_preflight_warnings_are_not_planning_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            preflight.setdefault("runs", {}).setdefault("issues", {}).setdefault(
                "aracne__01", []
            ).append(
                {
                    "severity": "warn",
                    "code": "optional_extra_missing",
                    "message": "optional extra not provided: tf_list",
                }
            )

            run_dir = self.mod.plan_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                output_dir=output_dir,
                planner="heuristic",
                preflight_report=preflight,
            )

            plan_payload = json.loads(
                (run_dir / "plan.json").read_text(encoding="utf-8")
            )
            report_payload = json.loads(
                (run_dir / "run_report.json").read_text(encoding="utf-8")
            )

        self.assertFalse(
            any("optional extra not provided" in item for item in plan_payload["warnings"])
        )
        self.assertFalse(
            any(
                "optional extra not provided" in issue.get("message", "")
                for issue in report_payload["issues"]
            )
        )

    def test_plan_rejects_invalid_planner_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output_dir = base / "out"
            manifest_path, tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

            with self.assertRaisesRegex(ValueError, "max_cores must be >= 1"):
                self.mod.plan_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                    output_dir=output_dir,
                    max_cores=0,
                    planner="heuristic",
                    preflight_report=preflight,
                )

            with self.assertRaisesRegex(ValueError, "planner must be one of"):
                self.mod.plan_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=tools_params_path,
                    output_dir=output_dir,
                    planner="unknown_planner",
                    preflight_report=preflight,
                )
