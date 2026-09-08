# Core Python Guide

The CLI is the recommended stable interface for most users. The Python core API
is useful when ANDREA is embedded in scripts, notebooks or larger workflows
that already manage paths and configuration files.

This page documents only the public functions exported by
`andrea.core.commands.*`. Helpers inside submodules are implementation details.

## Imports

```python
from pathlib import Path

from andrea.core.commands.generate_data import (
    execute_generate_data,
    plan_generate_data_request,
    preflight_generate_data_scenario,
    run_generate_data,
    validate_generate_data_plan,
)
from andrea.core.commands.infer_network import (
    infer_network,
    plan_infer_network,
    preflight_infer_network,
    run_infer_network_plan,
)
from andrea.core.commands.evaluate_inference import evaluate_inference
from andrea.core.commands.compare_networks import compare_networks
```

All functions use filesystem paths and write the same public artifacts as the
CLI.

## Generate Data

```python
scenario = Path("input/scenario-request.json")
runs = Path("input/simulator-runs.json")
plan = Path("work/simulation-plan.json")

preflight = preflight_generate_data_scenario(scenario)

plan_generate_data_request(
    scenario_request_path=scenario,
    simulator_runs_path=runs,
    output_path=plan,
    max_parallel_tasks=4,
    max_cores=8,
    max_ram_gb=32,
)

plan_summary = validate_generate_data_plan(plan)

benchmark_dir = run_generate_data(
    plan_path=plan,
    output_dir=Path("benchmarks"),
)
```

For a single call:

```python
benchmark_dir = execute_generate_data(
    scenario_request_path=scenario,
    simulator_runs_path=runs,
    output_dir=Path("benchmarks"),
    max_parallel_tasks=4,
    max_cores=8,
    max_ram_gb=32,
)
```

`preflight_generate_data_scenario` returns the simulator compatibility report.
Planning writes `simulation-plan.json`; execution returns the benchmark root
directory.

## Infer Network

```python
dataset_manifest = Path("dataset/dataset-manifest.json")
tools_params = Path("input/tools_params.json")
custom_tools = None  # or Path("input/custom_tools.json")

preflight = preflight_infer_network(
    dataset_manifest_path=dataset_manifest,
    tools_params_path=tools_params,
    custom_tools_path=custom_tools,
)

run_dir = plan_infer_network(
    dataset_manifest_path=dataset_manifest,
    tools_params_path=tools_params,
    custom_tools_path=custom_tools,
    output_dir=Path("inferred_networks"),
    max_cores=8,
    max_ram_gb=32,
    planner="auto",
    planner_time_limit_seconds=100,
    output_profile="full",
    preflight_report=preflight,
)

finished_run_dir = run_infer_network_plan(run_dir=run_dir)
```

For a single call:

```python
finished_run_dir = infer_network(
    dataset_manifest_path=dataset_manifest,
    tools_params_path=tools_params,
    custom_tools_path=custom_tools,
    output_dir=Path("inferred_networks"),
    max_cores=8,
    max_ram_gb=32,
    output_profile="canonical",
)
```

`tools_params.json` controls selected runs, scientific parameters, execution
modes and optional operational resources (`threads`, `ram_gb` and an exact
`cpuset_cpus`). Operational resources are frozen separately from parameters;
`ram_gb` is the exact Docker memory limit applied to every physical task in
that logical run.
`custom_tools.json` is optional and is used for temporary external Docker
images. Every external definition must explicitly declare an `outputs` object
with exactly `directed` and `sign`; no defaults are inferred. These capabilities
are frozen in the run report for downstream evaluation. It must also declare a
strict `runtime_resources.threading` capability. The `canonical` output profile
keeps CSV/report handoffs while skipping derivative graph exports; `full`
includes them.

Timing scopes are explicit. A physical result reports `scope: physical_task`:
its monotonic wall clock includes Docker launch and exit collection, while
`container_wall_time_seconds` is recorded separately from Docker's own state
timestamps when available. A grouped logical result measures from its first
physical child through ANDREA's logical postprocessing and therefore excludes
input preparation. The authoritative end-to-end latency is
`run_report.json.execution.measurement`; it includes input verification and
preparation, scheduling, merging, and the requested output materialization.

For multithreaded tools, `max_threads: null` declares that the wrapper has no
intrinsic thread ceiling. ANDREA still requires each operational allocation to
fit the run's effective CPU budget. Single-threaded tools must declare
`supported: false`, `default_threads: 1` and `max_threads: 1`.

## Evaluate Inference

```python
report = evaluate_inference(
    run_report_path=Path("inferred_networks/<run_dir>/run_report.json"),
    ground_truth_manifest_path=Path("dataset/ground-truth-manifest.json"),
    output_dir=Path("evaluations"),
    generate_view=True,
)

print(report["outputs"]["evaluation_report"])
```

The returned dictionary is the same report payload written to
`evaluation_report.json`. The ground-truth manifest must contain
`candidate_space`, whose source and target files define the evaluated edge
universe.

## Compare Networks

```python
report = compare_networks(
    request_path=Path("input/comparison-request.json"),
    output_dir=Path("comparisons"),
)

print(report["outputs"]["comparison_report"])
print(report["outputs"]["comparison_sqlite"])
```

By default the core writes the complete public CSV artifacts, including
`edge_scores.csv`. GUI code may call the same core with deferred export options,
but user scripts should normally keep the default complete output.

## Progress Callbacks

Long-running core functions accept progress callbacks where the corresponding
workflow supports them. A callback receives dictionaries with stage labels,
details or progress updates and can be used to integrate ANDREA into external
UIs or logs.

For general scripting, prefer the CLI snippets emitted by the GUIs because they
pin the exact files used in a run.
