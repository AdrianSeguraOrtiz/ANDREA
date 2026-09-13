from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from andrea.core.commands.generate_data.backends import docker_runner


class DockerFailureDiagnosticsTests(unittest.TestCase):
    def test_error_uses_preserved_traceback_when_container_streams_are_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary) / "stage"
            request = SimpleNamespace(
                simulator_id="boolode",
                simulator_spec={"docker_image": "example/boolode:1"},
                resolved_input_paths={},
                run_id="boolode",
                data_axes={},
                truth_requirements={},
                effective_extras=[],
                native_outputs=[],
                inputs={},
                simulator_params={},
                runtime_resources={},
            )

            def process(*_args, **_kwargs):
                (stage / "provenance/raw/wrapper_error.log").write_text(
                    "KeyError: 'E0_708'\n"
                )
                return SimpleNamespace(poll=lambda: 1, wait=lambda: 1)

            with patch.object(docker_runner, "_ensure_docker_cli"), patch.object(
                docker_runner, "_ensure_docker_image", return_value="local"
            ), patch.object(
                docker_runner.subprocess, "Popen", side_effect=process
            ) as popen:
                for simulator_id, environment in (
                    ("boolode", {"PYTHONHASHSEED": "1"}),
                    ("scmultisim", {}),
                ):
                    request.simulator_id = simulator_id
                    with self.subTest(
                        simulator_id=simulator_id
                    ), self.assertRaisesRegex(RuntimeError, "KeyError: 'E0_708'"):
                        docker_runner.run_docker_simulator(
                            request=request,
                            seed=1,
                            stage_dir=stage,
                            task_label=simulator_id,
                            progress_poll_seconds=0.1,
                            show_progress=False,
                        )
                    argv = popen.call_args.args[0]
                    self.assertEqual("-e" in argv, bool(environment))
                    if environment:
                        self.assertEqual(argv[argv.index("-e") + 1], "PYTHONHASHSEED=1")
                    self.assertEqual(
                        json.loads(
                            (
                                stage / "provenance/raw/docker_wrapper.environment.json"
                            ).read_text()
                        ),
                        environment,
                    )
            self.assertTrue(
                (stage / "provenance/raw/docker_wrapper.request.json").is_file()
            )

    def test_failure_details_bound_stderr_and_prefer_explicit_traceback(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            raw = stage / "provenance/raw"
            raw.mkdir(parents=True)
            (raw / "docker_wrapper.stderr.log").write_text(
                "x" * 20000 + "\nCause on stderr"
            )
            details = docker_runner._failure_details(stage)
            self.assertEqual(len(details.encode()), 8192)
            self.assertTrue(details.endswith("Cause on stderr"))
            (raw / "wrapper_error.log").write_text("Explicit traceback")
            self.assertEqual(
                docker_runner._failure_details(stage), "Explicit traceback"
            )

    def test_progress_and_explicit_empty_diagnostic_fallbacks(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            (stage / "progress.json").write_text(
                json.dumps({"status": "failed", "message": "Upstream error"})
            )
            self.assertEqual(docker_runner._failure_details(stage), "Upstream error")
            (stage / "progress.json").write_text("[]")
            self.assertIn(
                "No diagnostic message", docker_runner._failure_details(stage)
            )


if __name__ == "__main__":
    unittest.main()
