# dynGENIE3 integration decisions

## Implementation and installation

The authors' Python implementation at commit
`27eddd960d50e82791a8e0e5f8fcf6d679fe3eac` is selected. The supplied paper
(Huynh-Thu and Geurts, Scientific Reports 8, 3384, 2018,
DOI 10.1038/s41598-018-21715-0) used Python and the implementation exposes
multiple time courses, stationary samples, RF/ExtraTrees and optional diagnostics.
No cross-language speed comparison has been performed.

`Dockerfile` pins Python 3.11 and the complete numerical dependency closure by
version and wheel hashes. `run_tool.py --prepare-upstream` verifies the pinned
source hash and applies the necessary sklearn/API and deterministic seed
repairs inline during image construction. Inference performs no downloads or
patching. The supplied `repo/` and `papers/` remain unmodified and are excluded
from the temporary build context. The unpublished image remains `1.0.0`.

The pinned upstream tree has no project-level LICENSE/COPYING file; the paper's
CC-BY license does not establish a software redistribution license.

## Inputs

Use the shared [bulk expression contract](../../../../docs/bulk-time-series-contract.md):
one genes-by-observations `expression.tsv` and one exact-join
`column_descriptors.tsv`. The descriptor InputSpec is generic and does not
change with dataset selectors. Requesting this method requires its normalized
temporal fields; observed coordinates, time units and sampling design are read
from those fields during run validation and by the wrapper. The execution
envelope carries only the selected mode. Both `time_series` and `steady_state`
observation roles are selected in the ToolSpec. Catalog filtering uses declared
dataset metadata, available files and existing compatibility rules, without
interpreting descriptor contents.

Each longitudinal course becomes a separate native `TS_data` array with its own
observed `time_points`. Irregular sampling is allowed; points must remain
strictly increasing at the native float precision. The wrapper checks the
native derivative/extrema separations before fitting. Transitions never cross
course boundaries. Stationary columns in the same expression matrix become
native `SS_data`, without artificial times or series. All observations must use
compatible gene IDs, scales and preprocessing. Optional `tf_list.txt` restricts
candidate regulators to genes in the matrix.

Only longitudinal bulk designs are enabled. Technical duplicate observations,
independent destructive samples, nonfinite values and ambiguous metadata fail
explicitly. There is no inferred series identity from column names, implicit
averaging, interpolation, imputation or normalization.

## Parameters, resources and progress

Native defaults are preserved: data-derived decay (`alpha="from_data"`),
RandomForest, `K="sqrt"`, 1000 trees, diagnostics and saved models disabled.
Scalar/vector decay, ExtraTrees, K variants, TF subsets, diagnostics and model
serialization are exposed in the canonical ToolSpec. ANDREA adds a seed
(default 0) and export limit (`maxcount="all"`). These additions do not change
the scientific score definition.

`--threads` bounds a pool of target-gene workers by the number of targets.
Every forest uses `n_jobs=1`; native numerical libraries use one thread.
Per-target random seeds are deterministic independently of worker scheduling.
Progress reports validation (0%), inference (10%) and completion (100%) through
the shared wrapper contract. Individual completed target fits are not exposed
as incremental progress; `progress.kind="none"` describes that limitation.
Upstream target messages remain in the execution log. Assigned resources and
actual execution details are recorded separately in provenance.

## Outputs

The global directed network uses canonical six-field `network.csv`. Importance
scores are unsigned association weights, not activation/repression, calibrated
probabilities or experimentally demonstrated causality. Gene identifiers are
preserved. Self edges and zero scores are omitted; deterministic export ordering
and optional truncation do not change fitted importances.

VIM, decay rates, diagnostic values and optional serialized models remain
method-specific auxiliary outputs. Undefined optional diagnostics are null
with an explanation rather than substituted scores. The derivative approximation
does not establish an edge-specific lag and is not exported as one.

The upstream `dynGENIE3_predict_doubleKO` helper predicts expression trajectories
under a double knockout from already fitted models. It is not a network-inference
mode and is outside `infer-network`; saved models and decay rates remain available
for a separate downstream prediction workflow. The upstream source-printing
`get_link_list` helper is represented by canonical network export, preserving
full-precision positive scores instead of six-decimal text and randomly ordered
zero-weight rows.

## Validation and costs

Canonical fixtures and smoke configurations live beside the other catalog
wrapper tests. They cover the unified input contract and method branches.
Static/unit checks and small functional smoke executions are distinct from
biological validation and the user's GUI acceptance review.

The final functional review on 2026-10-23 passed all 44 wrapper tests and all
seven canonical Docker smoke variants, including the unmodified 1000-tree
default. The reviewed image is
`sha256:b6d951073becfc8e78a2a3626c6b94fb296b95d17eb14f1e7b31099132025e8a`;
its wrapper and column parser hashes match the current sources. Four additional
bounded RF fits (six genes, 32 trees) verified model export and a saturated
integer `K` with a TF subset. Seed 17 produced identical VIM, network and quality
outputs with one worker and two workers, including a repeated two-worker run;
seed 18 changed the network. Actual worker reports showed one native-library
thread per worker and the requested one/two-CPU and 2-GiB cgroup limits. These
checks establish the tested runtime behavior, not determinism on every platform.

Cost calibration uses `cost_profiles/dyngenie3.json`: 68 profiles in 20 branch
families, 186 size points at each of six CPU/RAM assignments and three repeats
(3,348 planned calibration executions). The unified-input campaign uses fresh
measurements; invalidated evidence is not reused. Independent gene-interpolation
checks are reserved separately. Accuracy remains pending those checks and is
limited to the declared parameters and temporal geometry. No image publication
is implied.

On 2026-11-08 the shared driver's existing `--reuse-only` mode reconciled all
68 profile fingerprints after the common cost executor's concurrency change.
It used the existing sealed original sources and explicit executor review;
neither the validator nor its identity checks were relaxed. Only the 68
`profile_sha256` fields changed. All 3,348 complete observations, their original
provenance, 1,116 aggregate points and 408 protected receipts remained identical.
Strict provenance, full-attempt and calibration-coverage validation now passes
68/68. No inference, image rebuild or new calibration was performed. Evidence
is under `release-logs/dyngenie3-fingerprint-20261108/`. SWING and BiXGBoost
retain their full declared calibration panels without an active cost model or
running calibration.
