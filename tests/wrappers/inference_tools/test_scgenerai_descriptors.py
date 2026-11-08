"""Keep scGeneRAI's established descriptor semantics during temporal integration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts/templates/python"))
spec = importlib.util.spec_from_file_location(
    "scgenerai_descriptor_wrapper",
    ROOT / "wrappers/inference_tools/tools/scgenerai/run_tool.py",
)
wrapper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = wrapper
spec.loader.exec_module(wrapper)


def test_all_categorical_descriptors_are_aligned_and_passed_without_prefix(tmp_path):
    (tmp_path / "column_descriptors.tsv").write_text(
        "column\tbatch\tdonor\tcondition\n"
        "c2\tbatch_b\tdonor_b\ttreated\n"
        "c1\tbatch_a\tdonor_a\tcontrol\n"
    )
    result = wrapper.load_column_descriptors(tmp_path, ["c1", "c2"])
    assert result.to_dict("list") == {
        "batch": ["batch_a", "batch_b"],
        "donor": ["donor_a", "donor_b"],
        "condition": ["control", "treated"],
    }


def test_absent_descriptors_preserve_upstream_default(tmp_path):
    assert wrapper.load_column_descriptors(tmp_path, ["c1", "c2"]) is None


@pytest.mark.parametrize(
    "text,match",
    [
        ("column\tbatch\nc1\ta\n", "match expression columns exactly"),
        ("column\tbatch\nc1\t\nc2\tb\n", "empty values"),
        ("column\tbatch\nc1\ta\nc1\tb\n", "duplicated expression-column"),
    ],
)
def test_existing_descriptor_alignment_and_nonempty_checks_remain(
    tmp_path, text, match
):
    (tmp_path / "column_descriptors.tsv").write_text(text)
    with pytest.raises(ValueError, match=match):
        wrapper.load_column_descriptors(tmp_path, ["c1", "c2"])
