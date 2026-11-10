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


def test_inapplicable_normalized_fields_are_omitted_without_fabricating_categories(tmp_path):
    (tmp_path / "column_descriptors.tsv").write_text(
        "column\tcondition\tperturbation\ttarget\tdose\ttimepoint\tcontrol\tbatch\n"
        "c2\ttreated\tknockdown\tG1\t0.5\t\tfalse\tb\n"
        "c1\tcontrol\tnone\t\t\t\ttrue\ta\n"
    )
    result = wrapper.load_column_descriptors(tmp_path, ["c1", "c2"])
    assert result.to_dict("list") == {
        "condition": ["control", "treated"],
        "perturbation": ["none", "knockdown"],
        "control": ["true", "false"],
        "batch": ["a", "b"],
    }
    assert result.attrs["descriptor_selection"] == {
        "supplied": ["condition", "perturbation", "target", "dose", "timepoint", "control", "batch"],
        "used": ["condition", "perturbation", "control", "batch"],
        "omitted_not_applicable": ["target", "dose", "timepoint"],
    }


def test_complete_normalized_fields_remain_model_descriptors(tmp_path):
    (tmp_path / "column_descriptors.tsv").write_text(
        "column\ttarget\tdose\n"
        "c1\tG1\t0.5\nc2\tG2\t0.75\n"
    )
    result = wrapper.load_column_descriptors(tmp_path, ["c1", "c2"])
    assert result.to_dict("list") == {"target": ["G1", "G2"], "dose": ["0.5", "0.75"]}
    assert result.attrs["descriptor_selection"]["omitted_not_applicable"] == []


def test_only_inapplicable_fields_do_not_silently_remove_all_conditioning(tmp_path):
    (tmp_path / "column_descriptors.tsv").write_text(
        "column\ttarget\nc1\t\nc2\tG1\n"
    )
    with pytest.raises(ValueError, match="no complete descriptors"):
        wrapper.load_column_descriptors(tmp_path, ["c1", "c2"])


def test_optional_intervention_onset_is_distinct_from_sampling_time(tmp_path):
    (tmp_path / "column_descriptors.tsv").write_text(
        "column\tcondition\ttimepoint\tintervention_timepoint\tintervention_time_unit\n"
        "c1\tcontrol\t24\t\t\n"
        "c2\ttreated\t24\t0.5\tsimulation_fraction\n"
    )
    result = wrapper.load_column_descriptors(tmp_path, ["c1", "c2"])
    assert result.to_dict("list") == {
        "condition": ["control", "treated"], "timepoint": ["24", "24"],
    }
    assert result.attrs["descriptor_selection"]["omitted_not_applicable"] == [
        "intervention_timepoint", "intervention_time_unit",
    ]
