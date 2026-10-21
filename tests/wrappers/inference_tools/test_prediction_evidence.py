from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts"))
from shared.cost_resume import input_manifest
from shared.prediction_evidence import (
    adapt_training_observations,
    load_training_observations,
)


class PredictionEvidenceAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile_id = "unit-test-training-profile"
        directory = self.root / "calibration" / "dyngenie3-test" / self.profile_id
        directory.mkdir(parents=True)
        self.io = directory / "run" / "io"
        (self.io / "extra").mkdir(parents=True)
        (self.io / "out").mkdir()
        for name, content in (
            ("expression.tsv", "gene\tS1\tS2\nG1\t1\t2\n"),
            ("params.json", "{}"),
            ("execution.json", "{}"),
        ):
            (self.io / name).write_text(content)
        (self.io / "out/network.csv").write_text("source,target,score\nG1,G2,1\n")
        self.config = {
            "provenance": {
                "image_id": "sha256:" + "a" * 64,
                "source_sha256": "b" * 64,
                "cpu_model": "synthetic-test-cpu",
                "platform": "test",
                "logical_cpus": 2,
                "host_memory_bytes": 8 * 1024**3,
                "docker_server": "synthetic-test-version",
            },
            "params_profile": {"resolved_params": {"ntrees": 1000}},
            "execution_profile": {"mode": "global"},
            "input_profile": {
                "temporal": {"schema_version": "unit-test-only"},
                "temporal_fixture": {"series_count": 1, "timepoints_per_series": 2},
            },
        }
        self.row = {
            "run_id": "unit-test-actual-capture-id",
            "status": "ok",
            "seconds": 3.5,
            "repeat": 1,
            "genes": 1,
            "columns": 2,
            "threads": 1,
            "ram_gb": 4,
            "dimensions": {
                "genes": 1,
                "columns": 2,
                "independent_series": 1,
                "timepoints_per_series": 2,
                "steady_state_samples": 0,
            },
            "input_sha256": input_manifest(self.io),
            "io_dir": str(self.io),
        }
        self.config_path = directory / "benchmark_config.json"
        self.config_path.write_text(json.dumps(self.config))
        self.raw = directory / "measurements.json"
        self.raw.write_text(json.dumps([self.row]))
        self.profile = {
            "profile_id": self.profile_id,
            "benchmark_config": self.config,
            "runtime_points": [
                {"measurement_evidence": {"observations": [copy.deepcopy(self.row)]}}
            ],
        }

    def test_adapter_preserves_original_identity_timing_and_receipt_references(self):
        entries = load_training_observations(self.root / "calibration", self.profile)
        output = self.root / "prediction"
        observations = adapt_training_observations(entries, output, self.profile)
        self.assertEqual(len(observations), 1)
        self.assertNotIn("steady_state_samples", observations[0]["dimensions"])
        receipt = json.loads((output / observations[0]["receipt"]["path"]).read_text())
        self.assertEqual(receipt["run_id"], self.row["run_id"])
        self.assertEqual(receipt["seconds"], self.row["seconds"])
        self.assertEqual(receipt["raw_evidence"][0]["pointer"], "/0")
        self.assertEqual(
            receipt["identity"]["image_id"], self.config["provenance"]["image_id"]
        )
        self.assertEqual(
            sum(ref["role"] == "primary" for ref in observations[0]["inputs"]), 1
        )

    def test_changed_inputs_or_timing_are_rejected(self):
        (self.io / "expression.tsv").write_text("changed matrix")
        with self.assertRaisesRegex(ValueError, "input inventory changed"):
            load_training_observations(self.root / "calibration", self.profile)
        self.row["seconds"] = 999
        self.raw.write_text(json.dumps([self.row]))
        with self.assertRaisesRegex(ValueError, "contradicts its embedded"):
            load_training_observations(self.root / "calibration", self.profile)

    def test_failure_is_retained_and_blocks_accuracy_claim(self):
        self.row["status"] = "error"
        self.raw.write_text(json.dumps([self.row]))
        with self.assertRaisesRegex(ValueError, "Failed calibration attempt"):
            load_training_observations(self.root / "calibration", self.profile)

    def resumed_copy(self, *, relocated=False):
        directory = self.root / "calibration" / "dyngenie3-resumed" / self.profile_id
        directory.mkdir(parents=True)
        (directory / "benchmark_config.json").write_text(json.dumps(self.config))
        row = {
            **copy.deepcopy(self.row),
            "reused_from": str(self.raw),
            "measurement_provenance": self.config["provenance"],
        }
        if relocated:
            io = directory / "copied-run" / "io"
            shutil.copytree(self.io, io)
            row["io_dir"] = str(io)
        path = directory / "measurements.json"
        path.write_text(json.dumps([row]))
        return path, row

    def test_identical_reused_execution_is_counted_once_with_original_reference(self):
        path, _ = self.resumed_copy()
        entries = load_training_observations(self.root / "calibration", self.profile)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["raw_receipt"], self.raw)
        self.assertEqual(entries[0]["equivalent_raw_receipts"][0]["raw_receipt"], path)
        output = self.root / "prediction"
        observations = adapt_training_observations(entries, output, self.profile)
        self.assertEqual(len(observations), 1)
        adapter = json.loads((output / observations[0]["receipt"]["path"]).read_text())
        self.assertEqual(len(adapter["equivalent_retained_copies"]), 1)

    def test_relocated_reuse_requires_identical_retained_outputs(self):
        path, row = self.resumed_copy(relocated=True)
        self.assertEqual(
            len(load_training_observations(self.root / "calibration", self.profile)), 1
        )
        (Path(row["io_dir"]) / "out/network.csv").write_text("changed output")
        with self.assertRaisesRegex(ValueError, "Conflicting copies"):
            load_training_observations(self.root / "calibration", self.profile)

    def test_conflicting_duplicate_or_unresolvable_reused_from_is_rejected(self):
        path, row = self.resumed_copy()
        row["seconds"] += 1
        path.write_text(json.dumps([row]))
        with self.assertRaisesRegex(ValueError, "contradicts its embedded"):
            load_training_observations(self.root / "calibration", self.profile)
        row["seconds"] = self.row["seconds"]
        row["reused_from"] = str(path.parent / "missing.json")
        path.write_text(json.dumps([row]))
        with self.assertRaisesRegex(ValueError, "source is missing or ambiguous"):
            load_training_observations(self.root / "calibration", self.profile)

    def test_reused_execution_cannot_change_hardware_or_source_provenance(self):
        path, row = self.resumed_copy()
        for key in ("cpu_model", "source_sha256", "image_id"):
            with self.subTest(key=key):
                row["measurement_provenance"] = {
                    **self.config["provenance"],
                    key: "changed",
                }
                path.write_text(json.dumps([row]))
                with self.assertRaisesRegex(ValueError, "provenance contradicts"):
                    load_training_observations(self.root / "calibration", self.profile)

    def test_repeated_execution_within_one_raw_receipt_is_not_a_repeat(self):
        self.raw.write_text(json.dumps([self.row, self.row]))
        with self.assertRaisesRegex(ValueError, "Repeated execution ID within one raw"):
            load_training_observations(self.root / "calibration", self.profile)


if __name__ == "__main__":
    unittest.main()
