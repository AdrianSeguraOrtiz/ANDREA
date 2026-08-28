from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ._helpers import InferNetworkCoreTestCase


class InferNetworkPreflightTests(InferNetworkCoreTestCase):
    def _issue_messages(
        self,
        payload: dict[str, object],
        severity: str | None = None,
    ) -> list[str]:
        issues = payload.get("issues", [])
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

    def _write_cell_expression_dataset(
        self,
        base: Path,
        *,
        genes: int,
        columns: int,
    ) -> Path:
        lines = ["gene\t" + "\t".join(f"C{i}" for i in range(1, columns + 1))]
        for gene_idx in range(1, genes + 1):
            values = "\t".join(
                str(gene_idx + column_idx) for column_idx in range(1, columns + 1)
            )
            lines.append(f"G{gene_idx}\t{values}")
        self._write_expression_matrix(base, lines=lines)
        return self._write_manifest(
            base,
            expression_matrix="expression.tsv",
            genes=genes,
            columns=columns,
            column_kind="cells",
            expression_profile="scrna",
            organism={"taxonomic_group": "animal", "ncbi_taxon_id": 9606},
        )

    def test_preflight_fails_when_input_spec_cross_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path, _tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "UNKNOWN_TF"],
            )

            with self.assertRaisesRegex(ValueError, "Input validation failed"):
                self.mod.preflight_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=None,
                )

    def test_preflight_invalid_tool_params_are_blocked_and_skipped(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path, tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            tools_payload = json.loads(tools_params_path.read_text(encoding="utf-8"))
            tools_payload["runs"][0]["params"] = {"alpha": "NOT_A_FLOAT"}
            tools_params_path.write_text(
                json.dumps(tools_payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            preflight_report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            self.assertEqual(preflight_report["runs"]["selected"], [])
            self.assertIn("aracne__01", preflight_report["runs"]["skipped"])
            issues = preflight_report["runs"]["issues"]["aracne__01"]
            self.assertTrue(
                any(issue.get("code") == "invalid_params" for issue in issues)
            )

    def test_catalog_does_not_block_configurable_required_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "gene\tC1\tC2",
                    "G1\t1\t2",
                    "G2\t3\t4",
                ],
            )
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                column_kind="cells",
                expression_profile="scrna",
                organism={"taxonomic_group": "animal", "ncbi_taxon_id": 9606},
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )

        blocked_ids = {item["tool_id"] for item in report["catalog"]["blocked"]}
        visible_ids = {
            item["tool_id"]
            for bucket in ("eligible", "warning")
            for item in report["catalog"][bucket]
        }
        self.assertNotIn("dignet", blocked_ids)
        self.assertIn("dignet", visible_ids)

    def test_selected_run_still_blocks_missing_required_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "gene\tC1\tC2",
                    "G1\t1\t2",
                    "G2\t3\t4",
                ],
            )
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                column_kind="cells",
                expression_profile="scrna",
                organism={"taxonomic_group": "animal", "ncbi_taxon_id": 9606},
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "dignet__01",
                        "tool_id": "dignet",
                        "execution": {"mode": "global"},
                        "params": {},
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("dignet__01", report["runs"]["skipped"])
        issues = report["runs"]["issues"]["dignet__01"]
        self.assertTrue(any(issue.get("code") == "invalid_params" for issue in issues))
        self.assertTrue(
            any(
                "missing required parameter: gene_set" in issue.get("message", "")
                for issue in issues
            )
        )

    def test_catalog_blocks_dataset_only_dimension_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=1,
                columns=5,
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )

        blocked = {item["tool_id"]: item for item in report["catalog"]["blocked"]}
        self.assertIn("cespgrn", blocked)
        self.assertIn("metasem", blocked)
        self.assertTrue(
            any(
                "requires at least 6" in message
                for message in self._issue_messages(blocked["cespgrn"], "block")
            )
        )
        self.assertTrue(
            any(
                "at least two expression genes" in message
                for message in self._issue_messages(blocked["metasem"], "block")
            )
        )

    def test_catalog_does_not_block_parameter_dependent_dimension_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=10,
                columns=10,
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )

        blocked_ids = {item["tool_id"] for item in report["catalog"]["blocked"]}
        visible_ids = {
            item["tool_id"]
            for bucket in ("eligible", "warning")
            for item in report["catalog"][bucket]
        }
        self.assertNotIn("kscreni", blocked_ids)
        self.assertIn("kscreni", visible_ids)

    def test_preflight_blocks_dataset_expression_gene_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lines = ["gene\tC1\tC2"]
            lines.extend(f"G{i}\t1\t2" for i in range(1, 101))
            self._write_expression_matrix(base, lines=lines)
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                genes=100,
                columns=2,
                column_kind="cells",
                expression_profile="scrna",
                organism={"taxonomic_group": "animal", "ncbi_taxon_id": 9606},
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "planet__01",
                        "tool_id": "planet",
                        "execution": {"mode": "global"},
                        "params": {"gene_set": "all_expression_genes"},
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("planet__01", report["runs"]["skipped"])
        issues = report["runs"]["issues"]["planet__01"]
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "supports at most 99 selected genes" in issue.get("message", "")
                for issue in issues
            )
        )

    def test_preflight_blocks_dignet_all_expression_gene_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lines = ["gene\tC1\tC2"]
            lines.extend(f"G{i}\t1\t2" for i in range(1, 202))
            self._write_expression_matrix(base, lines=lines)
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                genes=201,
                columns=2,
                column_kind="cells",
                expression_profile="scrna",
                organism={"taxonomic_group": "animal", "ncbi_taxon_id": 9606},
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "dignet__01",
                        "tool_id": "dignet",
                        "execution": {"mode": "global"},
                        "params": {"gene_set": "all_expression_genes"},
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("dignet__01", report["runs"]["skipped"])
        issues = report["runs"]["issues"]["dignet__01"]
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "accepts at most 200 selected genes" in issue.get("message", "")
                for issue in issues
            )
        )

    def test_preflight_all_expression_genes_limits_are_tool_specific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=120,
                columns=2,
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "dignet__01",
                        "tool_id": "dignet",
                        "execution": {"mode": "global"},
                        "params": {"gene_set": "all_expression_genes"},
                    },
                    {
                        "run_id": "planet__01",
                        "tool_id": "planet",
                        "execution": {"mode": "global"},
                        "params": {"gene_set": "all_expression_genes"},
                    },
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], ["dignet__01"])
        self.assertIn("planet__01", report["runs"]["skipped"])
        issues = report["runs"]["issues"]["planet__01"]
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "supports at most 99 selected genes" in issue.get("message", "")
                for issue in issues
            )
        )

    def test_preflight_blocks_cespgrn_too_few_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=20,
                columns=5,
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "cespgrn__01",
                        "tool_id": "cespgrn",
                        "execution": {"mode": "column_native"},
                        "params": {
                            "batch_size": 1,
                            "n_neigh": 2,
                            "pca_components": 2,
                        },
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("cespgrn__01", report["runs"]["skipped"])
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "requires at least 6" in issue.get("message", "")
                for issue in report["runs"]["issues"]["cespgrn__01"]
            )
        )

    def test_preflight_blocks_cespgrn_null_batch_size_small_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=20,
                columns=9,
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "cespgrn__01",
                        "tool_id": "cespgrn",
                        "execution": {"mode": "column_native"},
                        "params": {
                            "batch_size": None,
                            "n_neigh": 2,
                            "pca_components": 2,
                        },
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("cespgrn__01", report["runs"]["skipped"])
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "batch_size=null" in issue.get("message", "")
                for issue in report["runs"]["issues"]["cespgrn__01"]
            )
        )

    def test_preflight_blocks_cespgrn_n_neigh_over_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=20,
                columns=10,
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "cespgrn__01",
                        "tool_id": "cespgrn",
                        "execution": {"mode": "column_native"},
                        "params": {
                            "batch_size": 1,
                            "n_neigh": 11,
                            "pca_components": 2,
                        },
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("cespgrn__01", report["runs"]["skipped"])
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "n_neigh" in issue.get("message", "")
                for issue in report["runs"]["issues"]["cespgrn__01"]
            )
        )

    def test_preflight_blocks_cespgrn_pca_components_over_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=20,
                columns=10,
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "cespgrn__01",
                        "tool_id": "cespgrn",
                        "execution": {"mode": "column_native"},
                        "params": {
                            "batch_size": 1,
                            "n_neigh": 2,
                            "pca_components": 11,
                        },
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("cespgrn__01", report["runs"]["skipped"])
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "number of expression columns" in issue.get("message", "")
                for issue in report["runs"]["issues"]["cespgrn__01"]
            )
        )

    def test_preflight_blocks_cespgrn_pca_components_over_genes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=5,
                columns=20,
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "cespgrn__01",
                        "tool_id": "cespgrn",
                        "execution": {"mode": "column_native"},
                        "params": {
                            "batch_size": 1,
                            "n_neigh": 2,
                            "pca_components": 6,
                        },
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("cespgrn__01", report["runs"]["skipped"])
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "number of expression genes" in issue.get("message", "")
                for issue in report["runs"]["issues"]["cespgrn__01"]
            )
        )

    def test_preflight_blocks_kscreni_knn_at_cell_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=10,
                columns=10,
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "kscreni__01",
                        "tool_id": "kscreni",
                        "execution": {"mode": "column_native"},
                        "params": {"nfeatures": 10, "knn": 10},
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("kscreni__01", report["runs"]["skipped"])
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "knn must be smaller" in issue.get("message", "")
                for issue in report["runs"]["issues"]["kscreni__01"]
            )
        )

    def test_preflight_blocks_metasem_single_gene(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._write_cell_expression_dataset(
                base,
                genes=1,
                columns=2,
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "metasem__01",
                        "tool_id": "metasem",
                        "execution": {"mode": "global"},
                        "params": {},
                    }
                ],
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("metasem__01", report["runs"]["skipped"])
        self.assertTrue(
            any(
                issue.get("code") == "compatibility_rule"
                and "at least two expression genes" in issue.get("message", "")
                for issue in report["runs"]["issues"]["metasem__01"]
            )
        )

    def test_preflight_accepts_custom_docker_tool_and_passes_free_params(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
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
                        "params": {"alpha": 0.7, "nested": {"flag": True}},
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

            preflight_report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
            )

        self.assertEqual(preflight_report["inputs"]["custom_tools"], "provided")
        self.assertEqual(preflight_report["runs"]["selected"], ["demo_tool_01"])
        self.assertEqual(
            preflight_report["runs"]["catalog_tool_ids"]["demo_tool_01"],
            "custom_demo_tool_01",
        )
        self.assertEqual(
            preflight_report["runs"]["tool_origins"]["demo_tool_01"],
            "custom",
        )
        self.assertEqual(
            preflight_report["runs"]["resolved_params"]["demo_tool_01"],
            {"alpha": 0.7, "nested": {"flag": True}},
        )
        warning_entry = next(
            item
            for item in preflight_report["catalog"]["warning"]
            if item["tool_id"] == "custom_demo_tool_01"
        )
        self.assertEqual(warning_entry["tool_origin"], "custom")
        self.assertTrue(
            any(
                issue.get("code") == "custom_tool_warning"
                and "external Docker tool" in issue.get("message", "")
                for issue in warning_entry["issues"]
            )
        )

    def test_preflight_blocks_invalid_custom_tool_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path, _tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            custom_tools_path = self._write_custom_tools(
                base,
                tools=[
                    {
                        "run_id": "missing_image",
                        "name": "Broken",
                        "docker_image": "",
                        "execution_mode": "global",
                        "extra_inputs": [],
                        "outputs": {"directed": True, "sign": "mixed"},
                    },
                    {
                        "run_id": "bad_mode",
                        "name": "Bad Mode",
                        "docker_image": "example/bad-mode:1.0",
                        "execution_mode": "unsupported_mode",
                        "extra_inputs": [],
                        "outputs": {"directed": True, "sign": "mixed"},
                    },
                ],
            )

            preflight_report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
                custom_tools_path=custom_tools_path,
            )

        blocked = preflight_report["catalog"]["blocked"]
        self.assertTrue(
            any(
                item["tool_id"] == "custom_missing_image"
                and "docker_image must be a non-empty string"
                in item["issues"][0]["message"]
                for item in blocked
            )
        )
        self.assertTrue(
            any(
                item["tool_id"] == "custom_bad_mode"
                and "unsupported execution_mode" in item["issues"][0]["message"]
                for item in blocked
            )
        )

    def test_preflight_rejects_custom_tool_without_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path, _tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            custom_tools_path = self._write_custom_tools(
                base,
                tools=[
                    {
                        "name": "Missing Run ID",
                        "docker_image": "example/missing-run-id:1.0",
                        "execution_mode": "global",
                        "extra_inputs": [],
                        "outputs": {"directed": True, "sign": "mixed"},
                    }
                ],
            )

            with self.assertRaisesRegex(ValueError, "run_id is required"):
                self.mod.preflight_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=None,
                    custom_tools_path=custom_tools_path,
                )

    def test_preflight_rejects_custom_tool_run_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path, _tools_params_path = self._write_dataset_bundle(
                base,
                tf_values=["G1", "G2"],
            )
            tools_params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "alias_run",
                        "tool_id": "custom_demo_tool_01",
                        "execution": {"mode": "global"},
                        "params": {},
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

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
                custom_tools_path=custom_tools_path,
            )

        self.assertEqual(report["runs"]["selected"], [])
        self.assertIn("alias_run", report["runs"]["skipped"])
        self.assertTrue(
            any(
                issue.get("code") == "invalid_request"
                and "must exactly match" in issue.get("message", "")
                for issue in report["runs"]["issues"]["alias_run"]
            )
        )

    def test_preflight_fails_when_groups_file_has_wrong_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "gene\tS1\tS2",
                    "G1\t1\t2",
                    "G2\t3\t4",
                ],
            )
            (base / "groups.tsv").write_text(
                "\n".join(
                    [
                        "cell\tpseudotime",
                        "S1\t0.10",
                        "S2\t0.20",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                extras={"groups": "groups.tsv"},
            )

            with self.assertRaisesRegex(ValueError, "Input validation failed"):
                self.mod.preflight_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=None,
                )

    def test_preflight_accepts_groups_with_sample_column_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "gene\tS1\tS2",
                    "G1\t1\t2",
                    "G2\t3\t4",
                ],
            )
            (base / "groups.tsv").write_text(
                "\n".join(
                    [
                        "sample\tcluster",
                        "S1\tA",
                        "S2\tB",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                column_kind="samples",
                extras={"groups": "groups.tsv"},
            )

            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )
            extras_validation = preflight["input_validation"]["extras"]["groups"]
            self.assertEqual(extras_validation["status"], "ok")
            self.assertFalse(extras_validation["errors"])

    def test_preflight_accepts_column_native_extra_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "gene\tC1\tC2",
                    "G1\t1\t2",
                    "G2\t3\t4",
                ],
            )
            (base / "column_descriptors.tsv").write_text(
                "cell\tbatch\tcell_type\nC1\tbatch_a\troot\nC2\tbatch_b\tleaf\n",
                encoding="utf-8",
            )
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                column_kind="cells",
                expression_profile="scrna",
                extras={
                    "column_descriptors": "column_descriptors.tsv",
                },
            )

            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )
            extras_validation = preflight["input_validation"]["extras"]
            self.assertEqual(extras_validation["column_descriptors"]["status"], "ok")

    def test_preflight_supports_non_cell_column_kinds_for_generic_tools(
        self,
    ) -> None:
        cases = [
            (
                "timepoints",
                "bulk",
                ["T0", "T1"],
                {
                    "timepoints": "timepoints.tsv",
                },
                {
                    "timepoints.tsv": "column\ttimepoint\nT0\t0\nT1\t1\n",
                },
            ),
            (
                "perturbations",
                "bulk",
                ["P0", "P1"],
                {
                    "perturbation_design": "perturbation_design.tsv",
                    "interventions": "interventions.tsv",
                },
                {
                    "perturbation_design.tsv": (
                        "column\tcondition\tperturbation\ttarget\n"
                        "P0\tcontrol\tnone\t\n"
                        "P1\tknockdown_G1\tknockdown\tG1\n"
                    ),
                    "interventions.tsv": (
                        "intervention\ttarget\teffect\tsign\n"
                        "knockdown_G1\tG1\tknockdown\t-1\n"
                    ),
                },
            ),
        ]

        for column_kind, expression_profile, columns, extras, extra_files in cases:
            with self.subTest(column_kind=column_kind):
                with tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    self._write_expression_matrix(
                        base,
                        lines=[
                            "gene\t" + "\t".join(columns),
                            "G1\t1\t2",
                            "G2\t3\t4",
                        ],
                    )
                    for filename, content in extra_files.items():
                        (base / filename).write_text(content, encoding="utf-8")
                    manifest_path = self._write_manifest(
                        base,
                        expression_matrix="expression.tsv",
                        column_kind=column_kind,
                        expression_profile=expression_profile,
                        extras=extras,
                    )
                    tools_params_path = self._write_tools_params(
                        base,
                        runs=[
                            {
                                "run_id": "genie3__01",
                                "tool_id": "genie3",
                                "execution": {"mode": "global"},
                                "params": {},
                            },
                            {
                                "run_id": "lioness__01",
                                "tool_id": "lioness",
                                "execution": {"mode": "column_native"},
                                "params": {},
                            },
                        ],
                    )

                    preflight = self.mod.preflight_infer_network(
                        dataset_manifest_path=manifest_path,
                        tools_params_path=tools_params_path,
                    )

                self.assertEqual(preflight["runs"]["selected"], ["genie3__01"])
                self.assertIn("lioness__01", preflight["runs"]["skipped"])
                self.assertIn(
                    f"dataset column_kind '{column_kind}' is not accepted",
                    preflight["runs"]["skipped"]["lioness__01"],
                )
                extras_validation = preflight["input_validation"]["extras"]
                for extra_key in extras:
                    self.assertEqual(extras_validation[extra_key]["status"], "ok")

    def test_preflight_fails_when_expression_first_header_is_not_gene_like(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "cell\tpseudotime",
                    "C1\t0.10",
                    "C2\t0.20",
                ],
            )
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                columns=1,
            )

            with self.assertRaisesRegex(ValueError, "Input validation failed"):
                self.mod.preflight_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=None,
                )

    def test_preflight_fails_when_expression_has_duplicated_gene_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "gene\tS1\tS2",
                    "G1\t1\t2",
                    "G1\t3\t4",
                ],
            )
            manifest_path = self._write_manifest(
                base, expression_matrix="expression.tsv"
            )

            with self.assertRaisesRegex(ValueError, "Input validation failed"):
                self.mod.preflight_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=None,
                )

    def test_preflight_fails_when_expression_has_non_numeric_data_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "gene\tS1\tS2",
                    "G1\t1\tNOT_A_NUMBER",
                    "G2\t3\t4",
                ],
            )
            manifest_path = self._write_manifest(
                base, expression_matrix="expression.tsv"
            )

            with self.assertRaisesRegex(ValueError, "Input validation failed"):
                self.mod.preflight_infer_network(
                    dataset_manifest_path=manifest_path,
                    tools_params_path=None,
                )

    def test_preflight_accepts_valid_expression_matrix_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "gene\tsample_A\tsample_B",
                    "G1\t1.0\t2.5",
                    "G2\t3.2\t4.8",
                ],
            )
            manifest_path = self._write_manifest(
                base, expression_matrix="expression.tsv"
            )

            preflight = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )
            expr_validation = preflight["input_validation"]["expression_matrix"]
            self.assertEqual(expr_validation["status"], "ok")
            self.assertFalse(expr_validation["errors"])

    def _write_miniex_bundle(
        self,
        base: Path,
        *,
        taxonomic_group: str,
        ncbi_taxon_id: int,
        params: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
        self._write_expression_matrix(
            base,
            lines=[
                "gene\tC1\tC2\tC3",
                "G1\t1\t2\t3",
                "G2\t3\t4\t5",
            ],
        )
        (base / "groups.tsv").write_text(
            "cell\tcluster\nC1\tA\nC2\tA\nC3\tB\n",
            encoding="utf-8",
        )
        (base / "tf_list.txt").write_text("G1\nG2\n", encoding="utf-8")
        (base / "cluster_markers.tsv").write_text(
            "geneID\tp_val\tavg_logFC\tpct.1\tpct.2\tp_val_adj\tcluster\tgene\n"
            "G1\t0.001\t0.8\t0.7\t0.2\t0.01\tA\tG1\n",
            encoding="utf-8",
        )
        (base / "cluster_identities.tsv").write_text(
            "cluster\tannotation\torder\nA\troot\t1\nB\tleaf\t2\n",
            encoding="utf-8",
        )
        manifest_path = self._write_manifest(
            base,
            expression_matrix="expression.tsv",
            genes=2,
            columns=3,
            column_kind="cells",
            expression_profile="scrna",
            organism={
                "taxonomic_group": taxonomic_group,
                "ncbi_taxon_id": ncbi_taxon_id,
            },
            extras={
                "groups": "groups.tsv",
                "tf_list": "tf_list.txt",
                "cluster_markers": "cluster_markers.tsv",
                "cluster_identities": "cluster_identities.tsv",
            },
        )
        tools_params_path = self._write_tools_params(
            base,
            runs=[
                {
                    "run_id": "miniex3__01",
                    "tool_id": "miniex3",
                    "params": params or {},
                    "execution": {"mode": "group_native"},
                }
            ],
        )
        return manifest_path, tools_params_path

    def test_preflight_blocks_miniex3_for_non_plant_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_expression_matrix(
                base,
                lines=[
                    "gene\tC1\tC2",
                    "G1\t1\t2",
                    "G2\t3\t4",
                ],
            )
            manifest_path = self._write_manifest(
                base,
                expression_matrix="expression.tsv",
                column_kind="cells",
                expression_profile="scrna",
                organism={"taxonomic_group": "animal", "ncbi_taxon_id": 9606},
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )
            blocked = {
                item["tool_id"]: item
                for item in report["catalog"]["blocked"]
                if item["tool_id"] == "miniex3"
            }
            self.assertIn("miniex3", blocked)
            self.assertTrue(
                any(
                    "taxonomic_group" in reason
                    for reason in self._issue_messages(blocked["miniex3"], "block")
                )
            )

    def test_preflight_allows_miniex3_supported_plant_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, tools_params_path = self._write_miniex_bundle(
                Path(tmp),
                taxonomic_group="plant",
                ncbi_taxon_id=3702,
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            self.assertEqual(report["runs"]["selected"], ["miniex3__01"])
            self.assertEqual(report["runs"]["skipped"], {})

    def test_preflight_blocks_miniex3_unsupported_plant_with_motifs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, tools_params_path = self._write_miniex_bundle(
                Path(tmp),
                taxonomic_group="plant",
                ncbi_taxon_id=999999,
                params={"doMotifAnalysis": True, "reference_species": "ath"},
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            self.assertEqual(report["runs"]["selected"], [])
            self.assertIn("miniex3__01", report["runs"]["skipped"])
            self.assertIn("motif analysis", report["runs"]["skipped"]["miniex3__01"])

    def test_catalog_warns_miniex3_supported_non_default_plant_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, _tools_params_path = self._write_miniex_bundle(
                Path(tmp),
                taxonomic_group="plant",
                ncbi_taxon_id=4530,
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )
            self.assertFalse(
                any(
                    item["tool_id"] == "miniex3"
                    for item in report["catalog"]["blocked"]
                )
            )
            warning = {
                item["tool_id"]: item
                for item in report["catalog"]["warning"]
                if item["tool_id"] == "miniex3"
            }
            self.assertIn("miniex3", warning)
            self.assertTrue(
                any(
                    "reference_species=ath" in message
                    for message in self._issue_messages(warning["miniex3"], "warn")
                )
            )

    def test_preflight_blocks_miniex3_supported_non_default_plant_default_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, tools_params_path = self._write_miniex_bundle(
                Path(tmp),
                taxonomic_group="plant",
                ncbi_taxon_id=4530,
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            self.assertEqual(report["runs"]["selected"], [])
            self.assertIn("reference_species=ath", report["runs"]["skipped"]["miniex3__01"])

    def test_catalog_warns_miniex3_unsupported_plant_is_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, _tools_params_path = self._write_miniex_bundle(
                Path(tmp),
                taxonomic_group="plant",
                ncbi_taxon_id=999999,
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=None,
            )
            self.assertFalse(
                any(
                    item["tool_id"] == "miniex3"
                    for item in report["catalog"]["blocked"]
                )
            )
            warning = {
                item["tool_id"]: item
                for item in report["catalog"]["warning"]
                if item["tool_id"] == "miniex3"
            }
            self.assertIn("miniex3", warning)
            self.assertTrue(
                any(
                    "motif analysis" in message
                    for message in self._issue_messages(warning["miniex3"], "warn")
                )
            )

    def test_preflight_warns_miniex3_unsupported_plant_without_motifs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, tools_params_path = self._write_miniex_bundle(
                Path(tmp),
                taxonomic_group="plant",
                ncbi_taxon_id=999999,
                params={"doMotifAnalysis": False, "reference_species": "none"},
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            self.assertEqual(report["runs"]["selected"], ["miniex3__01"])
            run_issues = {"issues": report["runs"]["issues"]["miniex3__01"]}
            self.assertTrue(
                any(
                    "unsupported plant taxon" in warning
                    for warning in self._issue_messages(run_issues, "warn")
                )
            )

    def test_preflight_blocks_miniex3_reference_species_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path, tools_params_path = self._write_miniex_bundle(
                Path(tmp),
                taxonomic_group="plant",
                ncbi_taxon_id=3702,
                params={"reference_species": "osa"},
            )

            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=tools_params_path,
            )
            self.assertEqual(report["runs"]["selected"], [])
            self.assertIn(
                "reference_species=osa", report["runs"]["skipped"]["miniex3__01"]
            )
