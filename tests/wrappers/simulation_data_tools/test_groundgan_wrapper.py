"""Regressions for scientifically faithful GRouNdGAN perturbation metadata."""

import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


class GroundganWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[3] / "wrappers/simulation_data_tools/simulators/groundgan/run_simulator.py"
        spec = importlib.util.spec_from_file_location("groundgan_wrapper_audit", path)
        cls.wrapper = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.wrapper
        spec.loader.exec_module(cls.wrapper)

    def test_multiple_targets_cannot_be_silently_reduced_to_first(self):
        with self.assertRaisesRegex(ValueError, "exactly one TF"):
            self.wrapper.normalize_params({"perturbation": {"target_source": "explicit_list", "tf_targets": ["A", "B"]}})
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, "exactly one TF"):
            self.wrapper.write_perturbation_extras(output_dir=Path(temporary), columns=["pair_001_control", "pair_001_perturbed"], perturbations=[("A", 0), ("B", 0)])

    def test_expression_replacement_requires_one_nonnegative_value(self):
        for values in [[], [-1], [0, 1]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.wrapper.normalize_params({"perturbation": {"perturbation_values": values}})

    def test_positive_replacement_does_not_claim_overexpression(self):
        for value, effect, sign in [(0, "knockout", "-1"), (2.5, "set_expression", "0")]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.wrapper.write_perturbation_extras(output_dir=root, columns=["pair_001_control", "pair_001_perturbed"], perturbations=[("A", value)])
                with (root / "extras/interventions.tsv").open() as handle:
                    rows = list(csv.DictReader(handle, delimiter="\t"))
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]["effect"], rows[0]["sign"]), (effect, sign))
                with (root / "extras/perturbation_design.tsv").open() as handle:
                    design = list(csv.DictReader(handle, delimiter="\t"))
                self.assertEqual([row["target"] for row in design], ["", "A"])
                self.assertEqual(design[0]["matched_pair_id"], design[1]["matched_pair_id"])


if __name__ == "__main__":
    unittest.main()
