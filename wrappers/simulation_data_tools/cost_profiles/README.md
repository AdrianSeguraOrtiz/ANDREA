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

Before running a full benchmark, inspect the resolved matrix with:

```bash
make benchmark-simulator-costs ARGS="--plan-only"
```

To refresh selected profiles without replacing other measurements, repeat
`--profile` for the profiles to run and pass `--merge-existing`. This mode
requires at least one explicit profile. An incomplete matrix is not persisted,
any existing entry for that profile is preserved, and the command exits
non-zero.

For resumable measurements with normalized-output and actual-dimension checks,
use the companion runner to extend an existing `cost.json`. For example:

```bash
python wrappers/simulation_data_tools/scripts/extend_cost_profiles.py \
  --profile boolode:representative_size_grid \
  --threads 1 --ram-gb 8 --repeats 3 \
  --results-dir release-logs/simulator-size-calibration
```

Add `--plan-only` to inspect the matrix first. Repeating the command reuses
successful observations only when their recipes, images, inputs, resources and
output hashes still match. Failed or interrupted attempts remain available for
inspection and are not silently retried. A selected profile is published only
after its complete matrix succeeds; unrelated profiles remain unchanged. To
retain an older resource matrix, add a distinct profile rather than replacing
its identity with a different matrix.

The extension plan is JSON with semantic modes, resolved base parameters,
dimension mappings, profile hashes, resources, total run allocations and a
sequential timeout budget. `--plan-only` requires no Docker and performs no
measurement or evidence write. Execution freezes `plan.json` before measuring;
changed plans require a new results directory. The timeout allocation counts all
repetitions before verified reuse and excludes setup/output/native validation;
it is not an observed ETA. Complete aggregate profiles are reused only after
their retained individual receipts and output inventories are verified again.
Missing evidence does not authorize reuse of an aggregate timing.

For a new simulator with no `cost.json`, bootstrap with
`benchmark_costs.py --simulator SIMULATOR_ID --plan-only`, inspect the matrix,
then execute that bounded benchmark when resources are available. The extension
runner reads and merges an existing cost file; it does not create a first one.

The `representative_size_grid` profiles hold out an intermediate sample count
for independent validation. Model or input networks with a fixed gene universe
vary only their supported column dimension. Unless explicitly declared as a
requested budget (see dyngen below), `cells` means actual exported expression
columns, including samples, perturbations or timepoints. In the
short BoolODE recipes, one unsampled trajectory produces seven columns; its
mapping therefore uses a multiplier of seven. Checkpoint demonstrations in
GRouNdGAN measure generation from the embedded model, not model training or
biological realism.

The additional mode-specific `*_size_grid` profiles cover dropout, technical
noise, differentiation, perturbations and spatial outputs independently. Their
gene and column axes vary independently where the native simulator permits it;
a fixed network or perturbation table retains its actual supported dimensions.
These grids supplement existing measurements instead of relabelling them.

The extension runner records `benchmark_config.dimension_semantics` and the
verified output dimensions of each repeat in the runtime point's feature vector.
For dyngen, the gene coordinate is the **requested gene budget**: the native
simulator may allocate additional TFs to populate its backbone modules. The
runner checks the actual TF count plus the requested targets and housekeeping
genes against the exported expression matrix. It never asserts that a budget
of 50 produced exactly 50 genes. A group count of zero in these dynamic profiles
means unspecified; observed groups are retained in each observation's validation.

Dyngen snapshot profiles also declare `cells: requested_cell_budget`. In native
dyngen 1.1.1, a branch with only one available simulation step contributes no
sampled cells, without reallocating its share. The runner reconstructs this
allocation from the saved native model in the measured immutable image and
checks the exported expression against `dataset.rds`, outside the timed run.
Every repeat records the requested, expected and observed cell counts, omitted
cells and uncovered branches. Undersampling warnings are retained in costs and
plans. A valid cost measurement of that request does **not** establish full
requested cell coverage or biological adequacy. No cells are fabricated, no
seed is retried until a preferred result appears, and unexplained dimension
mismatches still fail validation. Synchronised dyngen experiments and all other
simulators retain exact exported-column validation.

After a reviewed validation-only change to the extension runner, explicit
`--revalidate-from-runner SHA256` can preserve successful observations. It
requires unchanged execution identities and output inventories, reruns current
validation and archives each original receipt. Failed measurements cannot be
reused through this option. The runner fingerprint includes its native R
validation script.

Simulator ETA remains a conservative scheduling budget based on measured p90
durations. Plans additionally expose `eta_provenance.runtime_estimates` with
`typical_seconds` (measured medians) and `planning_seconds`. Three repetitions
do not establish a calibrated 90% prediction interval. Mandatory extras count
as the same workload whether requested explicitly or supplied automatically.
Interpolation requires matching recipes/resources and measured geometric
support. Bounded affine extrapolation may use two nearby points on a measured
axis when startup and marginal costs are nonnegative; it remains explicitly
uncalibrated. Otherwise the planner reports a scheduling heuristic.

By default the benchmarker requests sizes `50x20`, `100x40`, `200x80`, threads
`1,2,4,8`, RAM limits `8,16,32,64` GB, one repeat and a per-run timeout of
1800 seconds. Thread values are filtered against each simulator's
`runtime_resources.threading` contract, so serial simulators are benchmarked
only with `threads=1`. RAM values and CPU counts are also capped by host-level
`--max-ram-gb` and `--max-cpu`.

If a run exceeds `--timeout`, the benchmarker classifies it as `timeout` and
explicitly removes the Docker container before continuing.

The size matrix uses `GENESxCELLS`. `dimension_params` tells the benchmarker how
to map those dimensions to simulator params. Use a string when one param controls
the dimension, a weighted object when total genes are split across multiple
params, or `{"fixed": N}` when an upstream bundle/model has a fixed public gene
or column count and no simulator parameter can change it.

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

Every benchmarked profile must include `tf_list` in `effective_extras`, and the
container must produce `extras/tf_list.txt`. Measurements made without that
candidate-regulator artifact must be rerun; they must not be relabelled.

`validate_simulator_costs.py` checks that those fields stay synchronized with
the benchmark config and measured runtime point.

For release calibration, use `--catalog-images --repeats 3 --results-dir PATH`.
This measures the current catalog image by immutable local ID without rebuilding.
Per-simulator evidence directories retain inputs, outputs, stdout/stderr, raw
measurements, resolved configuration and hardware/source/image provenance.
Filtered cost writes require `--merge-existing`; unselected profiles are preserved.
The provenance validator determines whether retained historical measurements
match the current specs/images; do not assume they all require remeasurement.
Timings from tiny or demonstration input bundles characterize those bundles only;
particularly, GRouNdGAN toy bundles are untrained smoke fixtures, not biological
validation or a calibrated model of training on real expression data.

## Separate coverage and prediction acceptance

Follow the shared [integration workflow](../../../docs/INTEGRATION_WORKFLOW.md).
The simulator validator's original capability/extras coverage check does not
test size geometry. The additional opt-in check reports every profile and
resource assignment separately:

```bash
python wrappers/simulation_data_tools/scripts/validate_simulator_costs.py \
  --simulator SIMULATOR_ID --require --require-provenance --require-coverage \
  --coverage-report /path/to/simulator-coverage.json
```

It requires at least three successful repetitions, complete declared point
matrices, three levels on each free size axis with independent crosses, and the
registered recipe sizes. Explicitly fixed dimensions in native model/network
mappings remain fixed. A recipe that delegates its sizes to the benchmark CLI
is assessed against the historical campaign's declared sizes. The report retains
requested-budget semantics; this gate does not claim actual exported dimensions
equal budgets or certify biological adequacy. Current readers cover genes and
total columns only; bulk time series also require evidence for timepoints per
series, independent series/replicates and dynamic parameters.

Geometric support can retain partial points with at least the declared number
of successful repetitions (minimum three); failures remain counted separately
as `failed_repetitions` per resource for reliability review. The benchmark and
extension runners retain their own conservative publication/reuse policies.

Legacy single-size and diagonal profiles can pass schema, semantics and
provenance while failing this stricter geometric acceptance. At the audited
0.2.1 baseline, all eight simulator files retain at least one such incomplete
profile; this is a calibration coverage pending state, not a finding that their
stored measurements or JSON are invalid. Some custom-network profiles also
need explicit fixed-axis metadata review. Do not relabel or overwrite them to
make the check pass. A justified exception needs scientific evidence and a
reviewed contract, followed by a suitable domain-specific coverage check.

Independent prediction validation is a third outcome. Reserve new dimensions
and workloads, freeze predictions/thresholds and the exact estimator/cost
snapshot, retain at least three validation-only timings and do not import them
into calibration. Use the shared
[`validate_cost_predictions.py`](../../validate_cost_predictions.py); its
authoritative manifest contract is available through `--help`, and the
[inference cost guide](../../inference_tools/cost_profiles/README.md#acceptance-of-new-integrations)
describes evidence preparation and limits. The checker verifies artifacts,
disjoint declared receipts/inputs and observed error/budget thresholds by
profile/resource stratum. Provenance review must still establish independence,
complete training membership and the chronology of freezing. Neither a full
grid nor three repeats establishes an extrapolation guarantee or a calibrated
prediction interval.
