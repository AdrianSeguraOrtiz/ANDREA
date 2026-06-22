from __future__ import annotations

import unittest

from andrea.core.commands.generate_data.bootstrap import load_generate_bootstrap


class GenerateDataBootstrapTests(unittest.TestCase):
    def test_load_generate_bootstrap_exposes_templates_and_inputs(self) -> None:
        payload = load_generate_bootstrap()
        templates = {item["id"]: item for item in payload["scenario_templates"]}
        grouped = templates["single_cell-cells-trajectory-global-group"]
        self.assertEqual(grouped["required_truth_outputs"], ["global", "group"])
        self.assertEqual(grouped["required_truth_contexts"], ["global", "group:"])
        self.assertIn("groups", grouped["available_extras"])

        inputs = {item["id"]: item for item in payload["simulation_inputs"]}
        self.assertIn("regulatory_network", inputs)
        self.assertIn(".tsv", inputs["regulatory_network"]["accept"])
        self.assertIn("scmultisim", inputs["regulatory_network"]["supported_by"])


if __name__ == "__main__":
    unittest.main()
