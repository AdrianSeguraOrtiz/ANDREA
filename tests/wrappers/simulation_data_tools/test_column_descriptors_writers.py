from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator

from andrea.core.shared.bulk_temporal import validate_bulk_temporal_inputs

ROOT = Path(__file__).resolve().parents[3]


def load_wrapper(name):
    module_name = f"normalized_columns_{name}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / f"wrappers/simulation_data_tools/simulators/{name}/run_simulator.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ColumnDescriptorWriterTests(unittest.TestCase):
    def test_bulk_time_series_writers_emit_one_metadata_file_with_model_units(self):
        for name in ("genenetweaver", "genespider2"):
            with self.subTest(simulator=name), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                (output / "extras").mkdir()
                wrapper = load_wrapper(name)
                columns = ["t0", "t1", "t2"]
                metadata = [
                    wrapper.ColumnMeta(
                        column=column,
                        condition="knockdown_G1",
                        perturbation="knockdown",
                        target="G1",
                        dose=1.0,
                        timepoint=float(index),
                        replicate="r1",
                        control=False,
                    )
                    for index, column in enumerate(columns)
                ]
                descriptor = output / "extras/column_descriptors.tsv"
                if name == "genespider2":
                    wrapper.write_column_descriptors(
                        descriptor, metadata, mode="bulk_time_series"
                    )
                else:
                    wrapper.write_column_descriptors(descriptor, metadata)
                expression = output / "expression.tsv"
                expression.write_text("gene\tt0\tt1\tt2\nG1\t1\t2\t3\nG2\t2\t3\t4\n")
                request = {
                    "data_axes": {
                        "measurement": "rna_expression",
                        "resolution": "bulk",
                        "column_kind": "timepoints",
                        "experimental_design": "time_series",
                    },
                    "truth_requirements": {"contexts": ["global"]},
                    "seed": 1,
                    "params": {"expression_variant": "normalized"},
                }
                extras = {
                    "tf_list": "extras/tf_list.txt",
                    "column_descriptors": "extras/column_descriptors.tsv",
                }
                if name == "genespider2":
                    wrapper.write_manifest(
                        output,
                        request,
                        wrapper.np.array([[1, 2, 3], [2, 3, 4]]),
                        "timepoints",
                        extras,
                        {},
                    )
                else:
                    table = wrapper.ExpressionTable(
                        genes=["G1", "G2"],
                        columns=columns,
                        matrix=[[1, 2, 3], [2, 3, 4]],
                        metadata=metadata,
                    )
                    wrapper.write_manifest(output, request, table, extras, {})
                manifest = json.loads(
                    (output / "simulator-output-manifest.json").read_text()
                )
                schema = json.loads(
                    (
                        ROOT
                        / "andrea/catalog_simulation_data_tools/schemas/simulator-output-manifest.schema.json"
                    ).read_text()
                )
                self.assertEqual(
                    list(Draft202012Validator(schema).iter_errors(manifest)), []
                )
                self.assertTrue(
                    set(manifest["extras"]).isdisjoint(
                        {"timepoints", "replicates", "steady_state_expression"}
                    )
                )
                summary = validate_bulk_temporal_inputs(
                    expression_path=expression,
                    column_descriptors_path=descriptor,
                )
                self.assertNotIn("temporal", manifest)
                self.assertEqual(summary["columns"], 3)
                self.assertEqual(summary["time_series_columns"], columns)
                self.assertEqual(summary["steady_state_columns"], [])
                with descriptor.open() as handle:
                    rows = list(csv.DictReader(handle, delimiter="\t"))
                self.assertEqual({row["time_unit"] for row in rows}, {"model_time"})
                self.assertEqual({row["sampling_design"] for row in rows}, {"longitudinal"})
                self.assertEqual({row["condition"] for row in rows}, {"knockdown_G1"})
                self.assertEqual(len({row["series_id"] for row in rows}), 1)
                self.assertEqual(
                    sorted(path.name for path in (output / "extras").iterdir()),
                    ["column_descriptors.tsv"],
                )

    def test_sampled_cells_keep_generic_metadata_without_a_longitudinal_claim(self):
        wrapper = load_wrapper("genespider2")
        metadata = [
            wrapper.ColumnMeta(
                column="cell_1",
                condition="knockdown_G1",
                perturbation="knockdown",
                target="G1",
                dose=1.0,
                timepoint=0.0,
                replicate="r1",
                control=False,
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "column_descriptors.tsv"
            wrapper.write_column_descriptors(
                path, metadata, mode="single_cell_perturbational"
            )
            with path.open() as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(
                rows,
                [{"column": "cell_1", "replicate": "r1", "condition": "knockdown_G1"}],
            )
            self.assertNotIn("series_id", rows[0])
            self.assertNotIn("observation_type", rows[0])

    @unittest.skipIf(shutil.which("Rscript") is None, "Rscript unavailable")
    def test_dyngen_time_group_writer_preserves_cell_labels_without_fabricated_series(
        self,
    ):
        wrapper = (
            ROOT / "wrappers/simulation_data_tools/simulators/dyngen/run_simulator.R"
        )
        code = r"""
args <- commandArgs(trailingOnly=TRUE)
expressions <- parse(file=args[[1]])
selected <- Filter(function(x) is.call(x) && identical(x[[1]], as.name("<-")) && identical(x[[2]], as.name("write_column_descriptors")), expressions)
stopifnot(length(selected) == 1L)
environment <- new.env(parent=globalenv())
eval(selected[[1]], envir=environment)
environment$write_column_descriptors(data.frame(cell_id=c("c2","c1"),timepoint_group=c(2,1)),c("c1","c2"),args[[2]])
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "column_descriptors.tsv"
            result = subprocess.run(
                ["Rscript", "--vanilla", "-e", code, str(wrapper), str(path)],
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with path.open() as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(
                rows,
                [
                    {
                        "column": "c1",
                        "timepoint_group": "1",
                        "time_unit": "model_time_group",
                    },
                    {
                        "column": "c2",
                        "timepoint_group": "2",
                        "time_unit": "model_time_group",
                    },
                ],
            )
            self.assertTrue(
                all(
                    "series_id" not in row and "observation_type" not in row
                    for row in rows
                )
            )


if __name__ == "__main__":
    unittest.main()
