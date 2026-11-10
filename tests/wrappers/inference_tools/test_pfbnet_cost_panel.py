"""Validate the complete declared PFBNet panel without fitting or timing models."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "wrappers/inference_tools/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "templates/python"))

from shared.benchmark_inputs import (
    BenchmarkInputProfile,
    BenchmarkInputSize,
    write_benchmark_io_dir,
)
from shared.benchmark_profiles import resolve_benchmark_profiles
from shared.temporal_cost_coverage import temporal_calibration_coverage

metadata_spec = importlib.util.spec_from_file_location(
    "_column_metadata", ROOT / "andrea/core/shared/column_metadata.py"
)
metadata = importlib.util.module_from_spec(metadata_spec)
sys.modules["_column_metadata"] = metadata
metadata_spec.loader.exec_module(metadata)
spec = importlib.util.spec_from_file_location(
    "pfbnet_panel_wrapper", ROOT / "wrappers/inference_tools/tools/pfbnet/run_tool.py"
)
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


def test_panel_is_complete_declarative_and_has_no_measurements():
    recipe = json.loads(
        (ROOT / "wrappers/inference_tools/cost_profiles/pfbnet.json").read_text()
    )
    design = recipe["calibration_design"]
    assert len(recipe["profiles"]) == 40
    assert sum(len(row["sizes"]) for row in recipe["profiles"]) == 120
    assert design["planned_calibration_runs"] == 2160
    assert design["timeout_seconds"] == 1800
    assert design["repetitions"] == 3
    assert set(recipe["cost_relevant_params"]) == {"k", "alpha", "iter_num"}
    # The same coverage validator used by the catalog must accept the design,
    # while correctly refusing to certify an unmeasured model as complete.
    coverage = temporal_calibration_coverage({"profiles": []}, recipe)
    assert coverage["calibration_adequate_for_planned_range"] is False
    assert not (ROOT / "andrea/catalog_inference_tools/tools/pfbnet/cost.json").exists()


def test_every_profile_materializes_valid_native_expression_inputs(tmp_path):
    profiles = resolve_benchmark_profiles(
        tool_id="pfbnet",
        catalog_tools_root=ROOT / "andrea/catalog_inference_tools/tools",
    )
    assert len(profiles) == 40
    for profile in profiles:
        # The largest registered gene size catches insufficient TF counts,
        # array geometry, native float domains and every scientific branch.
        genes, columns = map(int, profile.sizes[-1].split("x"))
        folder = tmp_path / profile.profile_id
        bundle = write_benchmark_io_dir(
            folder,
            BenchmarkInputSize(genes=genes, columns=columns),
            BenchmarkInputProfile.from_cost_input_profile(
                profile.input_profile, seed=123
            ),
        )
        data = wrapper.prepare_data(
            folder / "expression.tsv", folder / "extra", profile.params
        )
        geometry = profile.input_profile["temporal_fixture"]
        series = geometry["series_count"]
        times = geometry["timepoints_per_series"]
        assert data["effective_training_rows"] == series * (times - profile.params["k"])
        assert data["expression_only_data"].shape == (series, genes, times)
        assert not data["unused_steady_state_columns"]
        assert set(bundle.extras) == {"column_descriptors", *profile.optional_inputs}
        expected_candidates = genes // 5 if profile.optional_inputs else genes
        assert len(data["regulators"]) == expected_candidates
