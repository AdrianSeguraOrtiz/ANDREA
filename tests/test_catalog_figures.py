"""Scientific classifications must not change when keywords are expanded."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "scripts/doc_assets"
sys.path.insert(0, str(FIGURES))
spec = importlib.util.spec_from_file_location(
    "inference_contract_figure", FIGURES / "build_inference_tool_contract_map.py"
)
figure = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = figure
spec.loader.exec_module(figure)


def test_reviewed_family_is_independent_of_context_keywords():
    assert (
        figure.classify_family(
            {
                "id": "tigress",
                "method_family": "regression",
                "method_keywords": [
                    "tree_ensemble",
                    "cell_specific_network",
                    "prior_knowledge",
                ],
            }
        )[0]
        == "regression"
    )
    with pytest.raises(ValueError, match="no reviewed method_family"):
        figure.classify_family({"id": "new_tool", "method_keywords": ["random_forest"]})


def test_catalog_figure_covers_each_tool_once_with_reviewed_classification():
    tools = figure.load_tools()
    families = {tool.tool_id: tool.family_id for tool in tools}
    catalog = {
        path.parent.name
        for path in (ROOT / "andrea/catalog_inference_tools/tools").glob(
            "*/toolspec.json"
        )
    }
    assert set(families) == catalog
    assert len(tools) == len(catalog)
    assert families["tigress"] == "regression"
    assert families["cespgrn"] == "graph"
    assert families["scing"] == "tree"
    assert families["ppcor"] == "correlation"
    assert (
        families["scregulate"] == families["planet"] == families["dignet"] == "neural"
    )


@pytest.mark.parametrize("limit", [None, 8])
def test_catalog_map_preserves_optional_thread_limit(limit, tmp_path, monkeypatch):
    source = ROOT / "andrea/catalog_inference_tools/tools/genie3/toolspec.json"
    tool = json.loads(source.read_text())
    tool["id"] = "thread_limit_fixture"
    tool["runtime_resources"]["threading"]["max_threads"] = limit
    target = tmp_path / tool["id"] / "toolspec.json"
    target.parent.mkdir()
    target.write_text(json.dumps(tool))
    monkeypatch.setattr(figure, "SPEC_ROOT", tmp_path)
    loaded = figure.load_tools()
    assert len(loaded) == 1
    assert loaded[0].max_threads == limit
