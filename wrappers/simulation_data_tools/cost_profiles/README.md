# Simulator Cost Benchmark Profiles

Optional per-simulator profile configs for
`wrappers/simulation_data_tools/scripts/benchmark_costs.py`.

Each file is named `<simulator_id>.json`. A profile config declares which
scientific/runtime combinations are worth benchmarking. In this directory,
“profile” means a benchmark combination, not an old canonical simulator profile.
Each combination is defined by semantic axes (`data_axes`), requested truth
context families (`truth_requirements`), requested extras, inputs, params and
runtime resources. The script expands each selected benchmark profile across the
requested size, thread and RAM matrix.

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
      "id": "single_cell_cells_trajectory_global_default",
      "data_axes": {
        "measurement": "rna_expression",
        "resolution": "single_cell",
        "column_kind": "cells",
        "experimental_design": "trajectory"
      },
      "truth_requirements": {
        "contexts": ["global"]
      },
      "requested_extras": ["tf_list"],
      "param_overrides": {"tree_preset": "phyla1"}
    }
  ]
}
```

Conditional simulator inputs are explicit. If a profile sets a parameter value
that activates `simulatorspec.extra_inputs.conditional_required`, the profile must provide
that input in its `inputs` object.

Use `input_source_params` for params that define which upstream data source is
used. Those values are copied into `benchmark_config.input_profile.input_source_modes`.

The reusable input-file contract itself lives in
`andrea/catalog_simulation_data_tools/input_specs/`; cost profiles only decide
which declared simulator inputs are present for a benchmarked configuration.

Generated `cost.json` runtime points include a `feature_vector` with flat
semantic fields intended for planner models:

- `expression_profile`: copied from `data_axes.resolution`.
- `column_kind`: copied from `data_axes.column_kind`.
- `experimental_design`: copied from `data_axes.experimental_design`.
- `truth_context_families`: copied from `truth_requirements.contexts`.
- `extras`, `requested_extras` and `effective_extras`.

`validate_simulator_costs.py` checks that those fields stay synchronized with
the benchmark config and measured runtime point.
