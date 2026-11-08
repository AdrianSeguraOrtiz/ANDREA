from __future__ import annotations

import copy
import hashlib
import importlib.util
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
                "measurement_kernel_sha256": "c" * 64,
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
        before = self.raw.read_bytes()
        review_path = self.root / "review/rejected.json"
        with self.assertRaisesRegex(ValueError, "Failed calibration attempt"):
            load_training_observations(
                self.root / "calibration", self.profile, review_path=review_path
            )
        self.assertEqual(self.raw.read_bytes(), before)
        self.assertEqual(
            json.loads(review_path.read_text())["verification_status"], "rejected"
        )

    def test_timeout_followed_by_selected_retry_is_retained_in_separate_review(self):
        timeout = {
            **self.row,
            "run_id": "failed-original-attempt",
            "status": "timeout",
            "seconds": 1800.0,
        }
        self.raw.write_text(json.dumps([timeout, self.row]))
        before = self.raw.read_bytes()
        output = self.root / "prediction"
        review_path = output / "review.json"
        entries = load_training_observations(
            [self.root / "calibration"], self.profile, review_path=review_path
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["raw_row"]["run_id"], self.row["run_id"])
        observations = adapt_training_observations(entries, output, self.profile)
        self.assertEqual(len(observations), 1)
        review = json.loads(review_path.read_text())
        self.assertEqual(review["verification_status"], "verified")
        self.assertEqual(
            review["row_counts"], {"retained_failure": 1, "selected_training": 1}
        )
        excluded = review["rows"][0]
        self.assertEqual(excluded["run_id"], timeout["run_id"])
        self.assertEqual(excluded["raw_evidence"]["pointer"], "/0")
        self.assertEqual(
            excluded["raw_evidence"]["sha256"], hashlib.sha256(before).hexdigest()
        )
        self.assertEqual(
            (review_path.parent / excluded["raw_evidence"]["path"]).resolve(), self.raw
        )
        adapter = json.loads((output / observations[0]["receipt"]["path"]).read_text())
        self.assertEqual(adapter["raw_evidence"][0]["pointer"], "/1")
        self.assertEqual(adapter["seconds"], self.row["seconds"])
        self.assertEqual(
            adapter["calibration_review"]["sha256"],
            hashlib.sha256(review_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(self.raw.read_bytes(), before)

    def test_extra_success_is_inventoried_without_becoming_training(self):
        extra = {**self.row, "run_id": "not-selected-for-cost", "seconds": 100.0}
        self.raw.write_text(json.dumps([self.row, extra]))
        before = self.raw.read_bytes()
        entries = load_training_observations(self.root / "calibration", self.profile)
        output = self.root / "prediction"
        observations = adapt_training_observations(entries, output, self.profile)
        self.assertEqual([row["id"] for row in observations], [self.row["run_id"]])
        adapter = json.loads((output / observations[0]["receipt"]["path"]).read_text())
        review = json.loads(
            (output / adapter["calibration_review"]["path"]).read_text()
        )
        self.assertEqual(
            review["row_counts"], {"selected_training": 1, "unselected_success": 1}
        )
        self.assertEqual(review["rows"][1]["raw_evidence"]["pointer"], "/1")
        self.assertEqual(review["rows"][1]["run_id"], extra["run_id"])
        self.assertEqual(self.raw.read_bytes(), before)

    def test_unselected_different_context_is_reviewed_without_affecting_training(self):
        path, row = self.resumed_copy()
        row["run_id"] = "unselected-different-context"
        row.pop("reused_from")
        path.write_text(json.dumps([row]))
        config_path = path.with_name("benchmark_config.json")
        changed = copy.deepcopy(self.config)
        changed["provenance"]["source_sha256"] = "c" * 64
        config_path.write_text(json.dumps(changed))
        review_path = self.root / "review.json"
        entries = load_training_observations(
            self.root / "calibration", self.profile, review_path=review_path
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            json.loads(review_path.read_text())["row_counts"]["unselected_success"], 1
        )

    def test_missing_selected_id_cannot_be_replaced_by_unselected_success(self):
        self.raw.write_text(json.dumps([{**self.row, "run_id": "unselected-success"}]))
        with self.assertRaisesRegex(ValueError, "do not completely match"):
            load_training_observations(self.root / "calibration", self.profile)

    def test_embedded_duplicate_or_changed_provenance_is_rejected(self):
        embedded = self.profile["runtime_points"][0]["measurement_evidence"][
            "observations"
        ]
        embedded.append(copy.deepcopy(embedded[0]))
        with self.assertRaisesRegex(ValueError, "repeated execution ID"):
            load_training_observations(self.root / "calibration", self.profile)
        embedded.pop()
        embedded[0]["measurement_provenance"] = {
            **self.config["provenance"],
            "source_sha256": "changed",
        }
        with self.assertRaisesRegex(ValueError, "provenance contradicts"):
            load_training_observations(self.root / "calibration", self.profile)

    def test_selected_config_source_change_remains_rejected(self):
        changed = copy.deepcopy(self.config)
        changed["provenance"]["source_sha256"] = "c" * 64
        self.config_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "source provenance contradicts"):
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

    def test_reused_source_outside_requested_root_is_followed_and_verified(self):
        path, _ = self.resumed_copy()
        entries = load_training_observations(path.parent.parent, self.profile)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["raw_receipt"], self.raw)
        self.assertEqual(len(entries[0]["equivalent_raw_receipts"]), 1)

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
