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
