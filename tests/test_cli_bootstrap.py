from __future__ import annotations

import importlib
import unittest

from typer.testing import CliRunner

CLI_MODULE = importlib.import_module("andrea.cli.app")
app = CLI_MODULE.app


class AndreaCliBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_root_help_lists_public_namespaces(self) -> None:
        result = self.runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("infer-network", result.output)
        self.assertIn("generate-data", result.output)
        self.assertIn("evaluate-inference", result.output)
        self.assertIn("compare-networks", result.output)
        self.assertIn("gui", result.output)

    def test_version_flag_prints_package_version(self) -> None:
        result = self.runner.invoke(app, ["--version"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("ANDREA 0.1.0", result.output)

    def test_infer_network_namespace_is_bootstrapped(self) -> None:
        result = self.runner.invoke(app, ["infer-network", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("preflight", result.output)
        self.assertIn("plan", result.output)
        self.assertIn("run", result.output)
        self.assertIn("execute", result.output)

    def test_generate_data_namespace_is_bootstrapped(self) -> None:
        result = self.runner.invoke(app, ["generate-data", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("preflight", result.output)
        self.assertIn("plan", result.output)
        self.assertIn("run", result.output)
        self.assertIn("execute", result.output)

    def test_gui_namespace_is_bootstrapped(self) -> None:
        result = self.runner.invoke(app, ["gui", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Graphical interfaces", result.output)
        self.assertIn("evaluate-inference", result.output)
        self.assertIn("compare-networks", result.output)

    def test_evaluate_inference_gui_command_is_bootstrapped(self) -> None:
        result = self.runner.invoke(app, ["gui", "evaluate-inference", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Launch the local graphical interface", result.output)

    def test_compare_networks_gui_command_is_bootstrapped(self) -> None:
        result = self.runner.invoke(app, ["gui", "compare-networks", "--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Launch the local graphical interface", result.output)


if __name__ == "__main__":
    unittest.main()
