# Cost Benchmark Profiles

Optional per-tool profile configs for `wrappers/inference_tools/scripts/benchmark_costs.py`.

Each file is named `<tool_id>.json` and contains a `profiles` array. If a tool
has no config file, the resolver uses a conservative default profile:

- `global` when the tool supports global execution.
- Otherwise `group_native` when available.
- Otherwise `group_emulated`.
- Required and active conditional inputs are generated.
- Optional inputs are omitted by default except cheap/common `tf_list`.

Example:

```json
{
  "cost_relevant_params": ["limit"],
  "profiles": [
    {
      "id": "global_default",
      "execution": {"mode": "global"},
      "optional_inputs": ["tf_list"],
      "param_overrides": {"limit": 50}
    },
    {
      "id": "group_emulated_groups_2",
      "execution": {"mode": "group_emulated"},
      "group_count": 2,
      "optional_inputs": ["tf_list"]
    }
  ]
}
```

`cost_relevant_params` is the curated list of dotted ToolSpec parameter paths
that should affect ETA profile matching. It can be declared once at the top
level and inherited by every profile, or overridden per profile. Parameters not
listed here are still passed to the wrapper during benchmarking, but they do not
penalize planner profile selection later.

Manual profile configs are intentionally bounded. They define which combinations
are worth benchmarking; `benchmark_costs.py` expands each selected profile across
the requested size, thread and RAM matrix.

For `execution.mode=group_emulated`, each size point represents one physical
wrapper task. The profile still records the logical `group_count`, and planner
ETA code applies the grouped-task multiplier later.

Useful script options:

- `--cost-profiles-dir PATH`: use another profile config directory.
- `--profile PROFILE_ID`: run only matching profile ids.
- `--profile TOOL_ID:PROFILE_ID`: run one profile for one tool.
- `--group-count N`: fallback group count for grouped profiles that omit it.
- `--prior-density FLOAT`: fallback density for generated prior-like inputs.
- `--optional-input INPUT_ID`: optional input to include in implicit profiles.
