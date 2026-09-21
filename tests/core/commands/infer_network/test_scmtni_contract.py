from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ._helpers import InferNetworkCoreTestCase


class ScmtniExecutionContractTests(InferNetworkCoreTestCase):
    def _inputs(self, base: Path) -> Path:
        self._write_expression_matrix(
            base,
            lines=[
                "gene\tC1\tC2\tC3\tC4",
                "G1\t1\t2\t3\t4",
                "G2\t4\t3\t2\t1",
            ],
        )
        (base / "groups.tsv").write_text(
            "column\tcluster\nC1\tA\nC2\tA\nC3\tB\nC4\tB\n",
            encoding="utf-8",
        )
        (base / "tf_list.txt").write_text("G1\n", encoding="utf-8")
        (base / "lineage_tree.tsv").write_text(
            "child\tparent\tgain_rate\tloss_rate\n"
            "A\t__root__\t0\t0\n"
            "B\tA\t0.2\t0.1\n",
            encoding="utf-8",
        )
        return self._write_manifest(
            base,
            expression_matrix="expression.tsv",
            genes=2,
            columns=4,
            column_kind="cells",
            expression_profile="scrna",
            extras={
                "groups": "groups.tsv",
                "tf_list": "tf_list.txt",
                "lineage_tree": "lineage_tree.tsv",
            },
        )

    def _preflight(
        self,
        base: Path,
        *,
        mode: str,
        indep: bool,
        q: int = 0,
    ) -> tuple[Path, Path, dict]:
        manifest_path = self._inputs(base)
        params_path = self._write_tools_params(
            base,
            runs=[
                {
                    "run_id": "scmtni_test",
                    "tool_id": "scmtni",
                    "execution": {"mode": mode},
                    "params": {"indep": indep, "q": q},
                }
            ],
        )
        report = self.mod.preflight_infer_network(
            dataset_manifest_path=manifest_path,
            tools_params_path=params_path,
        )
        return manifest_path, params_path, report

    def test_independent_grouped_route_is_emulated_and_firewalls_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path, params_path, preflight = self._preflight(
                base,
                mode="group_emulated",
                indep=True,
            )
            self.assertEqual(preflight["runs"]["selected"], ["scmtni_test"])

            run_dir = self.mod.plan_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=params_path,
                output_dir=base / "out",
                planner="heuristic",
                preflight_report=preflight,
            )
            plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
            contract = json.loads(
                (run_dir / "input" / "runtime-input-contract.json").read_text(
                    encoding="utf-8"
                )
            )["runs"]["scmtni_test"]

        logical = plan["runs"][0]
        self.assertEqual(logical["execution"], {"mode": "group_emulated"})
        self.assertEqual(
            [task["group_label"] for task in logical["physical_tasks"]],
            ["A", "B"],
        )
        self.assertEqual(contract["active_extra_inputs"], ["groups", "tf_list"])
        self.assertEqual(contract["mounted_extra_inputs"], ["tf_list"])

    def test_joint_route_is_native_and_receives_groups_and_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path, params_path, preflight = self._preflight(
                base,
                mode="group_native",
                indep=False,
            )
            self.assertEqual(preflight["runs"]["selected"], ["scmtni_test"])
            run_dir = self.mod.plan_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=params_path,
                output_dir=base / "out",
                planner="heuristic",
                preflight_report=preflight,
            )
            contract = json.loads(
                (run_dir / "input" / "runtime-input-contract.json").read_text(
                    encoding="utf-8"
                )
            )["runs"]["scmtni_test"]

        self.assertEqual(
            contract["mounted_extra_inputs"],
            ["groups", "lineage_tree", "tf_list"],
        )

    def test_preflight_blocks_execution_and_indep_mismatches(self) -> None:
        cases = (
            ("group_native", True),
            ("group_emulated", False),
            ("global", False),
        )
        for mode, indep in cases:
            with self.subTest(mode=mode, indep=indep), tempfile.TemporaryDirectory() as tmp:
                _manifest, _params, report = self._preflight(
                    Path(tmp),
                    mode=mode,
                    indep=indep,
                )
                self.assertEqual(report["runs"]["selected"], [])
                issues = report["runs"]["issues"]["scmtni_test"]
                self.assertTrue(
                    any(issue.get("code") == "compatibility_rule" for issue in issues),
                    issues,
                )

    def test_omitted_mode_uses_scmtni_native_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = self._inputs(base)
            params_path = self._write_tools_params(
                base,
                runs=[
                    {
                        "run_id": "scmtni_test",
                        "tool_id": "scmtni",
                        "params": {"indep": False, "q": 0},
                    }
                ],
            )
            report = self.mod.preflight_infer_network(
                dataset_manifest_path=manifest_path,
                tools_params_path=params_path,
            )

        self.assertEqual(report["runs"]["selected"], ["scmtni_test"])
        self.assertEqual(
            report["runs"]["resolved_execution"]["scmtni_test"],
            {"mode": "group_native"},
        )
