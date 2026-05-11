# Simulator Cost Benchmark Profiles

Optional per-simulator profile configs for
`wrappers/simulation_data_tools/scripts/benchmark_costs.py`.

Each file is named `<simulator_id>.json`. A profile config declares which
scientific/runtime combinations are worth benchmarking; the script expands each
selected profile across the requested size, thread and RAM matrix.

The size matrix uses `GENESxCELLS`. `dimension_params` tells the benchmarker how
to map those dimensions to simulator params. Use a string when one param controls
the dimension, or a weighted object when total genes are split across multiple
params.

Example:

```json
{
  "cost_relevant_params": ["num_cells", "tree_preset"],
  "dimension_params": {
    "cells": "num_cells",
    "genes": "num_genes"
  },
  "input_source_params": ["grn_source", "tree_preset"],
  "profiles": [
    {
      "id": "scrna_global_default",
      "profile": "scrna_global",
      "requested_extras": ["tf_list"],
      "param_overrides": {"tree_preset": "phyla1"}
    }
  ]
}
```

Conditional simulator inputs are explicit. If a profile sets a parameter value
that activates `simulator_inputs.conditional_required`, the profile must provide
that input in its `inputs` object.

Use `input_source_params` for params that define which upstream data source is
used. Those values are copied into `benchmark_config.input_profile.input_source_modes`.
