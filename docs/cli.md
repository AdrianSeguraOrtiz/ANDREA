# CLI Guide

ANDREA's CLI exposes the same four workflow stages as the local GUIs:

```text
generate-data -> infer-network -> evaluate-inference -> compare-networks
```

Use the step-by-step commands when you want to inspect validation reports,
review resource plans or reproduce a GUI run exactly. Use the `execute`
shortcuts for unattended runs once the inputs are already trusted.

## Common Help Commands

```sh
andrea --help
andrea generate-data --help
andrea infer-network --help
andrea evaluate-inference --help
andrea compare-networks --help
```

The GUI launchers are also regular CLI commands:

```sh
andrea gui generate-data --host 127.0.0.1 --port 8766
andrea gui infer-network --host 127.0.0.1 --port 8765
andrea gui evaluate-inference --host 127.0.0.1 --port 8767
andrea gui compare-networks --host 127.0.0.1 --port 8768
```

Add `--open-browser` when working on a local desktop and you want ANDREA to open
the browser automatically.

## `generate-data`

`generate-data` turns a scenario request plus selected simulator runs into a
benchmark package.

Step-by-step:

```sh
andrea generate-data preflight \
  --scenario path/to/scenario-request.json \
  --output-json path/to/preflight_report.json

andrea generate-data plan \
  --scenario path/to/scenario-request.json \
  --simulator-runs path/to/simulator-runs.json \
  --max-parallel-tasks 4 \
  --max-cores 8 \
  --max-ram-gb 32 \
  --out path/to/simulation-plan.json

andrea generate-data run \
  --plan path/to/simulation-plan.json \
  --output-dir benchmarks
```

Single command:

```sh
andrea generate-data execute \
  --scenario path/to/scenario-request.json \
  --simulator-runs path/to/simulator-runs.json \
  --output-dir benchmarks \
  --max-parallel-tasks 4 \
  --max-cores 8 \
  --max-ram-gb 32
```

Main outputs include `benchmark-manifest.json`, one dataset directory per
simulator replicate, standardized `expression.tsv`, `truth/networks.csv`,
ground-truth manifests, standardized extras and preserved native simulator
outputs. The analysis bundle can be used by `evaluate-inference`.

## `infer-network`

`infer-network` validates a dataset manifest, resolves selected catalog or
external Docker tools, creates an execution plan and runs the planned waves.

Step-by-step:

```sh
andrea infer-network preflight \
  --dataset-manifest path/to/dataset-manifest.json \
  --tools-params path/to/tools_params.json \
  --custom-tools path/to/custom_tools.json \
  --output-json path/to/preflight_report.json

andrea infer-network plan \
  --dataset-manifest path/to/dataset-manifest.json \
  --tools-params path/to/tools_params.json \
  --custom-tools path/to/custom_tools.json \
  --output-dir inferred_networks \
  --max-cores 8 \
  --max-ram-gb 32 \
  --planner auto \
  --planner-time-limit-seconds 100

andrea infer-network run \
  --run-dir inferred_networks/<run_dir>
```

Single command:

```sh
andrea infer-network execute \
  --dataset-manifest path/to/dataset-manifest.json \
  --tools-params path/to/tools_params.json \
  --output-dir inferred_networks \
  --max-cores 8 \
  --max-ram-gb 32
```

`--custom-tools` is optional and is only needed for temporary external Docker
tools. Every external definition must declare an `outputs` object containing
exactly `directed` (boolean) and `sign` (`none`, `signed` or `mixed`). Missing or
unknown output semantics block preflight. Its matching `tools_params.json` run
must use the same `run_id` and the derived tool ID, obtained by prefixing
the complete definition ID with literal `custom_`. Existing prefixes are not
collapsed, and custom definitions cannot be reused under aliases. Main outputs
include
`plan.json`, `preflight_report.json`, `run_report.json`, per-run workspaces,
merged raw and normalized networks, runtime state, logs and graph exports when
available. The analysis bundle can feed `evaluate-inference` and
`compare-networks`.

## `evaluate-inference`

`evaluate-inference` compares inferred networks against a ground-truth manifest.

```sh
andrea evaluate-inference \
  --run-report inferred_networks/<run_dir>/run_report.json \
  --ground-truth-manifest benchmarks/<benchmark>/datasets/<dataset>/ground-truth-manifest.json \
  --output-dir evaluations \
  --view
```

Main outputs include `evaluation_report.json`, `metrics.csv`, `pairings.csv`
and an optional HTML view. The analysis bundle can be passed to
`compare-networks` as an evaluation overlay.

## `compare-networks`

`compare-networks` compares one or more inferred-network sources. The request
JSON points to source run reports and can optionally include evaluation reports.

```sh
andrea compare-networks \
  --request path/to/comparison-request.json \
  --output-dir comparisons
```

Main outputs include `comparison_report.json`, `network_index.csv`,
`distances.csv`, `distance_coordinates.csv`, `edge_scores.csv`,
`comparison.sqlite` and a lightweight `comparison_view.html`.

The local GUI uses `comparison.sqlite` for scalable interactive exploration.
The CLI output remains portable and complete for storage, inspection and
downstream scripting.

## Stepwise Versus One-Shot Execution

Use `preflight -> plan -> run` when:

- you want to inspect `eligible`, `warning` and `blocked` entries before
  launching Docker jobs;
- you need to review the generated plan, waves or resource estimates;
- you are reproducing a GUI run from its snippets;
- you want to preserve intermediate validation reports.

Use `execute` when the same inputs are already validated and you want a compact
scriptable command.
