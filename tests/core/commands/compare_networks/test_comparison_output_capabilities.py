"""Comparison must not infer direction or sign from CSV row orientation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from andrea.core.commands.compare_networks import compare_networks


def write_source(tmp_path: Path, *, frozen: bool = True) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    definitions = {
        "undirected_unsigned": (False, "none", "scgenerai", "catalog"),
        "undirected_signed": (False, "signed", "ppcor", "catalog"),
        "directed_unsigned": (True, "none", "genie3", "catalog"),
        "directed_mixed": (True, "mixed", "scmtni", "catalog"),
        "external": (True, "none", "custom_external", "custom"),
    }
    tools = {
        "selected": list(definitions),
        "catalog_tool_ids": {key: value[2] for key, value in definitions.items()},
        "tool_origins": {key: value[3] for key, value in definitions.items()},
    }
    if frozen:
        tools["output_capabilities"] = {
            key: {
                "directed": directed,
                "sign": sign,
                "catalog_tool_id": catalog_id,
                "tool_origin": origin,
            }
            for key, (directed, sign, catalog_id, origin) in definitions.items()
        }
    report = {
        "run_id": "analysis",
        "status": "executed",
        "tools": tools,
        "outputs": {"merged_network_normalized": "merged_network_normalized.csv"},
    }
    (source / "run_report.json").write_text(json.dumps(report))
    with (source / "merged_network_normalized.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source",
                "target",
                "score",
                "sign",
                "evidence",
                "context",
                "tool_id",
            ],
        )
        writer.writeheader()
        for tool, (_, sign, _, _) in definitions.items():
            writer.writerow(
                {
                    "source": "G1",
                    "target": "G2",
                    "score": 0.8,
                    "sign": "+" if sign != "none" else "?",
                    "evidence": "association",
                    "context": "global",
                    "tool_id": tool,
                }
            )
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "capabilities",
                "sources": [
                    {"source_id": "sample", "run_report": "source/run_report.json"}
                ],
            }
        )
    )
    return request


def test_comparison_levels_respect_frozen_catalog_and_external_capabilities(tmp_path):
    request = write_source(tmp_path)
    report = compare_networks(request_path=request, output_dir=tmp_path / "out")
    levels = {}
    for row in report["network_index"]:
        levels.setdefault(row["tool_id"], set()).add(row["level"])
    assert levels == {
        "undirected_unsigned": {"topology"},
        "undirected_signed": {"topology"},
        "directed_unsigned": {"topology", "directed"},
        "directed_mixed": {"topology", "directed", "signed"},
        "external": {"topology", "directed"},
    }
    index = {row["network_id"]: row for row in report["network_index"]}
    for row in report["distances"]:
        assert row["level"] in levels[index[row["network_a"]]["tool_id"]]
        assert row["level"] in levels[index[row["network_b"]]["tool_id"]]


def test_legacy_comparison_retains_projection_with_explicit_warning(tmp_path):
    request = write_source(tmp_path, frozen=False)
    report = compare_networks(request_path=request, output_dir=tmp_path / "out")
    assert len(report["network_index"]) == 15
    assert any(
        "missing frozen output_capabilities" in item for item in report["warnings"]
    )


def test_invalid_present_capabilities_do_not_fall_back_to_legacy(tmp_path):
    request = write_source(tmp_path)
    report_path = tmp_path / "source/run_report.json"
    report = json.loads(report_path.read_text())
    del report["tools"]["output_capabilities"]["external"]
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="output_capabilities"):
        compare_networks(request_path=request, output_dir=tmp_path / "out")
