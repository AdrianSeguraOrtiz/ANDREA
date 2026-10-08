"""Public seed bounds and backward-compatible defaults for stochastic wrappers."""
import json

import pytest

from .test_audited_tool_semantics import ROOT, load_wrapper


@pytest.mark.parametrize("tool", ["dignet", "planet", "scgenerai"])
@pytest.mark.parametrize("seed", [True, None, -1, 2**32, 1.5, "7"])
def test_reject_invalid_seed(tool, seed):
    wrapper = load_wrapper(tool)
    spec = json.loads((ROOT / "andrea/catalog_inference_tools/tools" / tool / "toolspec.json").read_text())
    params = {key: value["default"] for key, value in spec["params"].items() if "default" in value}
    params["seed"] = seed
    resolve = getattr(wrapper, "_resolve_params", None) or wrapper.resolve_params
    with pytest.raises(ValueError, match="seed"):
        resolve(params)


@pytest.mark.parametrize("tool", ["dignet", "planet", "scgenerai"])
def test_old_parameter_files_use_explicit_catalog_seed_default(tool):
    wrapper = load_wrapper(tool)
    spec = json.loads((ROOT / "andrea/catalog_inference_tools/tools" / tool / "toolspec.json").read_text())
    params = {key: value["default"] for key, value in spec["params"].items() if "default" in value}
    resolve = getattr(wrapper, "_resolve_params", None) or wrapper.resolve_params
    params.pop("seed")
    if "gene_set" in params:
        params["gene_set"] = "all_expression_genes"
    assert resolve(params).seed == spec["params"]["seed"]["default"] == 0
    assert resolve({**params, "seed": 2**32 - 1}).seed == 2**32 - 1
