"""Fast wrapper regressions; also runnable with unittest inside its pinned image."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


HAS_RUNTIME = all(
    importlib.util.find_spec(name) for name in ("yaml", "numpy", "pandas", "sklearn")
)


@unittest.skipUnless(HAS_RUNTIME, "BoolODE wrapper scientific runtime is required")
class BoolodeWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep scientific modules in sys.modules when the upstream stub patch
        # exits; old NumPy/pandas cannot safely be imported a second time.
        for name in ("yaml", "numpy", "pandas", "sklearn.cluster"):
            importlib.import_module(name)
        path = (
            Path(__file__).resolve().parents[3]
            / "wrappers/simulation_data_tools/simulators/boolode/run_simulator.py"
        )
        module_spec = importlib.util.spec_from_file_location(
            "boolode_wrapper_test", path
        )
        cls.wrapper = importlib.util.module_from_spec(module_spec)
        upstream = ModuleType("BoolODE")
        upstream.post_processing = ModuleType("BoolODE.post_processing")
        with patch.dict(
            sys.modules,
            {"BoolODE": upstream, "BoolODE.post_processing": upstream.post_processing},
        ):
            module_spec.loader.exec_module(cls.wrapper)

    def test_time_grid_matches_native_sampling_branches(self):
        for params in [
            {"num_cells": 300, "integration_step_size": 0.1},
            {"num_cells": 500, "simulation_time": 3, "integration_step_size": 1},
            {"num_cells": 500, "simulation_time": 3.5, "integration_step_size": 1},
            {"sample_cells": True, "num_cells": 1000},
        ]:
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.wrapper.normalize_params(params)
        for params in [
            {},
            {"num_cells": 300, "integration_step_size": 0.001},
            {"num_cells": 499, "simulation_time": 3.5, "integration_step_size": 1},
            {"sample_cells": True, "num_cells": 999, "integration_step_size": 0.1},
        ]:
            with self.subTest(params=params):
                self.wrapper.normalize_params(params)

    def test_boolean_truth_respects_negation_scope_and_mixed_polarity(self):
        genes = {"A", "B", "C", "G-1"}
        cases = [
            ("not A and B", {"A": "-", "B": "+"}),
            ("not (A or B) and C", {"A": "-", "B": "-", "C": "+"}),
            ("not not A", {"A": "+"}),
            ("(A and B) or (not A and C)", {"A": "?", "B": "+", "C": "+"}),
            ("G-1 and not B", {"G-1": "+", "B": "-"}),
        ]
        for rule, expected in cases:
            with self.subTest(rule=rule):
                self.assertEqual(self.wrapper.boolean_rule_signs(rule, genes), expected)

    def test_native_first_not_bug_does_not_reach_public_truth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "refNetwork.csv"
            native.write_text("Gene1,Gene2,Type\nA,C,-\nB,C,-\n")
            model = root / "model.tsv"
            model.write_text("Gene\tRule\nA\tA\nB\tB\nC\tnot A and B\n")
            rows = self.wrapper.parse_ref_network(native, {"A", "B", "C"}, model)
            self.assertEqual({row["source"]: row["sign"] for row in rows}, {"A": "-", "B": "+"})
            self.assertEqual(native.read_text(), "Gene1,Gene2,Type\nA,C,-\nB,C,-\n")

    def test_custom_model_rejects_upstream_missing_value_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "model.tsv"
            for gene in ("NA", "NaN", "NULL", ""):
                with self.subTest(gene=gene):
                    model.write_text(f"Gene\tRule\n{gene}\tA\nA\tA\n")
                    with self.assertRaisesRegex(ValueError, "upstream parser"):
                        self.wrapper.read_boolean_model(model)
            model.write_text("Gene\tRule\nNAN1\tNAN1\nA\tNAN1\n")
            self.assertEqual(self.wrapper.read_boolean_model(model), {"NAN1", "A"})

    def test_upstream_failure_keeps_streams_and_exposes_cause(self):
        def fail(_config):
            print("Upstream stdout before failure")
            print("Upstream stderr before failure", file=sys.stderr)
            raise KeyError("E0_708")

        request = {
            "simulator_id": "boolode",
            "data_axes": {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "trajectory",
            },
            "truth_requirements": {"contexts": ["global"]},
            "seed": 1,
            "effective_extras": ["tf_list"],
            "params": {},
            "runtime_resources": {"threads": 1},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request))
            output = root / "output"
            with patch.object(
                self.wrapper, "stage_inputs", return_value={}
            ), patch.object(
                self.wrapper, "build_config", return_value={}
            ), patch.object(
                self.wrapper, "run_boolode", side_effect=fail
            ), patch(
                "sys.stderr"
            ) as stderr:
                code = self.wrapper.main(
                    ["--request", str(request_path), "--output-dir", str(output)]
                )
            self.assertEqual(code, 1)
            raw = output / "provenance/raw"
            self.assertIn(
                "Upstream stdout before failure",
                (raw / "upstream_stdout.log").read_text(),
            )
            self.assertIn(
                "Upstream stderr before failure",
                (raw / "upstream_stderr.log").read_text(),
            )
            self.assertIn("KeyError: 'E0_708'", (raw / "wrapper_error.log").read_text())
            self.assertIn(
                "KeyError", "".join(call[0][0] for call in stderr.write.call_args_list)
            )
            self.assertEqual(
                json.loads((output / "progress.json").read_text())["status"], "failed"
            )

    def test_session_info_reads_pinned_commit_without_changing_global_git_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(
                self.wrapper.subprocess,
                "run",
                return_value=SimpleNamespace(stdout=self.wrapper.PINNED_COMMIT + "\n"),
            ) as run:
                self.wrapper.write_session_info(root)
            self.assertIn(
                "boolode_commit=" + self.wrapper.PINNED_COMMIT,
                (root / "session_info.txt").read_text(),
            )
            command = run.call_args[0][0]
            self.assertEqual(
                command,
                ["git", "--git-dir=" + str(self.wrapper.BOOLODE_HOME / ".git"), "rev-parse", "HEAD"],
            )
            self.assertTrue(run.call_args[1]["capture_output"])


if __name__ == "__main__":
    unittest.main()
