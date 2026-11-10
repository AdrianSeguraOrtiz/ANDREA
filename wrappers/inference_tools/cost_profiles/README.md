# Cost Benchmark Profiles

Optional per-tool profile configs for `wrappers/inference_tools/scripts/benchmark_costs.py`.

Each file is named `<tool_id>.json` and contains a `profiles` array. If a tool
has no config file, the resolver uses a conservative default profile:

- `global` when the tool supports global execution.
- Otherwise `group_native` when available.
- Otherwise `column_native`.
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

Profiles may also pin size and fixture semantics when the generic matrix is not
valid or would be wasteful:

- top-level `sizes` or per-profile `sizes`: list of `GENESxCOLUMNS` size points.
  Explicit CLI `--size` values override these hints for debug runs.
- top-level or per-profile `column_kind` and `expression_profile`: semantic
  labels stored in `cost.json` and used by the fixture generator.
- top-level or per-profile `gene_id_source`: gene-id source used by the fixture
  generator. `synthetic` emits `G1`, `G2`, ...; specialized tools such as DigNet
  and Planet can request `human_breast_cancer_pathway` so the benchmark genes
  overlap their bundled KEGG/RegNetwork resources.
- top-level or per-profile `temporal`: internal generator facts (`schema_version`,
  `time_basis`, `time_unit`, `sampling_design`) written into `column_descriptors.tsv`.
  `temporal_fixture` declares positive `series_count`, at least two
  `timepoints_per_series`, optional `steady_state_samples`, and regular or
  irregular sampling. All temporal profiles provide the single descriptor.
  Each size satisfies `COLUMNS = series_count * timepoints_per_series + steady_state_samples`.
  The wrapper reads its scientific facts from that file; execution selects only
  the mode. Stationary columns have no fabricated coordinates or transitions.

Profiles describe only executable container contracts: `global`,
`group_native`, or `column_native`. Logical `group_emulated` runs select the
corresponding `global` profile for every child task. Logical
`group_aggregated` runs select `column_native` and plan aggregation separately.

Useful script options:

- `--cost-profiles-dir PATH`: use another profile config directory.
- `--profile PROFILE_ID`: run only matching profile ids.
- `--profile TOOL_ID:PROFILE_ID`: run one profile for one tool.
- `--plan-only`: print the fully resolved benchmark matrix without building or
  running containers.
- `--group-count N`: fallback group count for native-grouped profiles that omit it.
- `--prior-density FLOAT`: fallback density for generated prior-like inputs.
- `--optional-input INPUT_ID`: optional input to include in implicit profiles.

## Resource-aware common cost executor

`run_cost_coverage.py` reads each recipe's `calibration_design.resources` and
`repetitions`. Sizes, variants and all declared resource assignments form the
panel independently of the host. Operational CPU/RAM caps never remove planned
points. The existing fallback applies to recipes without a declared design.
The normal `infer-network` executor and planner are outside this mechanism.

The common coordinator runs one measurement driver at a time. Within each
profile, the driver admits independent fits concurrently through
`shared/cost_resources.py`. The coordinator defaults to `--concurrency 0`
(automatic); a positive value limits concurrent fits. Standalone
`benchmark_costs.py` defaults to one fit; pass `--concurrency 0` for automatic
admission. Profiles remain sequential, so unused capacity need not always be
filled from a different profile.

The broker discovers effective affinity, inherited cgroup CPU/RAM limits,
visible SMT siblings and live available memory. It atomically reserves the
unchanged requests across cooperating campaigns sharing `--resource-state-dir`.
Docker enforces the selected CPU set, CPU quota and RAM limit. Default RAM
headroom is the greater of 1 GiB and 10% of effective total memory. Full active
reservations are also subtracted from live availability; this is deliberately
conservative. `--cpu-headroom`, `--ram-headroom-gb`,
`--ram-headroom-fraction`, `--max-cpu` and `--max-ram-gb` are operational controls,
not panel definitions. Reservations coordinate participating cost workers;
they do not isolate external processes or the hypervisor, nor reserve physical
RAM in the kernel.

A single writer checkpoints raw terminal observations and the cost file.
Resource/queue waits are outside the measured Docker wall time and the fixed
1,800-second execution timeout. Temporarily busy requests wait; requests that
cannot fit effective capacity remain pending, with operational state
`waiting_resources` and exit code 75. This creates no scientific observation.
There is no timeout-based pruning of larger sizes. A controlled stop cancels
owned work and drains workers; an interrupted fit remains pending. CPU/RAM is
released only after container exit is positively verified. Uncertain cleanup or
orphaned reservations block admission until verified cleanup.

Each new observation records its CPU set, budget, resource policy and worker
intervals. Historical observation IDs, times, source/kernel identities and
provenance stay intact. Different measurement kernels are rejected by default.
An explicit review in the existing source-proof manifest can attest a change
to cost-executor resource orchestration, bound to exact old/new kernel hashes
and hashed review evidence. It does not bypass input, image, hardware,
parameters, resources or timing-scope checks, and does not establish universal
performance equivalence between allocation policies.

## Release calibration

Profiles ending in `_spec_defaults` use `parameter_base: "spec_defaults"`:
they start from the ToolSpec defaults, bypassing the development/smoke overrides.
Explicit per-profile overrides still select the intended execution variant.
The original bounded profiles are retained as separate configurations. Profiles
named `fast` remain reduced training configurations; their timings do not certify
full-training defaults. Extrapolation to unmeasured sizes or parameters remains
an estimate and must be reported as such.

Use `--catalog-images` to measure the already built/published catalog images.
The benchmark resolves each image to its immutable local ID before running it;
it does not rebuild or pull a potentially different image during the campaign.
Use `--results-dir PATH` to retain raw measurements, configs, inputs, outputs,
stdout/stderr and hardware/source/image provenance in per-tool evidence
directories. Compatible raw observations are reused from the current results
directory and any `--reuse-results-dir` directories.
Input hashes, immutable images, parameters, execution modes and resource
assignments must match before an observation can be reused. The input/timing
kernel has its own fingerprint. Pure queue/resume edits do not change it;
changes to native process supervision or CPU placement require an explicit
review before historical kernels can be reused across that transition.
Importing a historical source without that fingerprint requires its retained
source root and manifest, verified against the original global source digest.
Original measurement IDs, durations and provenance are never relabelled.

Every actual terminal outcome (`ok`, `timeout`, `oom`, `error`) is checkpointed
atomically, including partially attempted points. Missing repetitions remain
pending; a timeout is not permission to retry or increase the budget. Successful
observations can be reused under a smaller cap only when their actual duration
fits it; failed outcomes require the same cap. Percentiles use successful
observations only and remain null when there are none. A worker succeeds after
all requested slots have terminal results, even if some timed out. This does
not certify sufficient successful timings or predictive accuracy.

Use `--reuse-only` to verify retained evidence and rebuild derived cost metadata
without building images or running inference. Where needed, pass
`--reuse-source-root PATH --reuse-source-manifest PATH`; the manifest must contain
the existing `source_files_sha256` map. This operation reports pending slots and
does not fabricate observations for them.

Filtered writes require `--profile ... --merge-existing`; unselected profiles
and compatible historical points are preserved. Use `--no-write-cost` for
exploratory runs. Unfiltered campaigns also preserve unrelated profiles.
`make validate-release-costs` requires every cost file,
current catalog-image/spec provenance and at least three timing repetitions.
Three repetitions provide a basic variability check, not a precise tail estimate.

## Independent gene and cell calibration

The recipes define three-by-three grids where both dimensions can vary, and
three gene counts at a fixed cell count where the pretrained model requires it.
The 24 tools in the 0.2.1 catalog have **57 baseline profiles and 29 representative profiles**:
**86 profiles and 750 planned size points** (501 baseline, 249 representative),
before multiplying by repetitions and resource assignments. Each parameter/input
profile has its own grid; timings from different parameter settings or resource
assignments are not pooled.

dynGENIE3 and SWING have observed-time recipes for the unified input contract.
dynGENIE3 registers 68 profiles in 20 families and 186 size points per resource
assignment; SWING registers 77 profiles in 29 families and 231 points. Each uses
CPU allocations 1, 2 and 4 with RAM limits 8 and 32 GiB, and three repetitions:
3,348 and 4,158 calibration executions respectively. The six assignments are
measured separately. This exceeds the resource coverage of a typical catalog
profile; it does not establish universal prediction accuracy.

The default panels vary genes, visits and independent courses separately.
Other families cover expensive parameter branches, TF inputs, dynGENIE3
stationary samples and RF/ET quality, and all three SWING backends. Lasso native
CV/stability defaults use a smaller 10/25/50-gene domain with enough courses for
ten folds; a separately labelled reduced-stability branch covers 50/100/200
genes. Vector alpha fixes its gene axis. Functional checks and timing coverage
remain separate.

The independent validation design reserves 2,124 dynGENIE3 and 2,772 SWING
held-out timings: two intermediate gene counts and three repeats per
interpolatable profile/resource stratum. Execution of that validation is
deferred; it is separate from calibration. Costs and estimator sources are
frozen before those measurements.
Predefined criteria are relative median error at most 30% and all three held-out
times within the planning ETA. These are tests to pass, not precision claims or
statistical guarantees. The existing planner exposes one scheduling ETA, so that
actual value is tested against both criteria. Fixed-vector profiles have no
independent interpolation claim. Unregistered temporal geometry, parameters,
TF cardinalities and extrapolation remain unvalidated.

Invalidated measurements from older input contracts or native implementations
are not reused. Byte-identical retained files may share storage through hard
links without changing paths, hashes or distinct execution IDs. Keep sufficient
disk reserve and retain plans, state, logs and raw measurements in ignored
campaign storage, outside wrapper directories. Do not publish an unfinished
working model as complete calibration.

| Fixture family | Gene levels | Column levels |
| --- | --- | --- |
| Standard synthetic baseline | 50, 100, 200 | 20, 40, 80 |
| DigNet synthetic human pathway, direct input | 50, 80, 100 | 100, fixed by checkpoint |
| DigNet synthetic human pathway, 100 metacells | 50, 80, 100 | 200, 400, 800 |
| PLaNet synthetic human pathway, direct input | 50, 80, 99 | 100, fixed by checkpoint |
| PLaNet synthetic human pathway, 100 metacells | 50, 80, 99 | 200, 400, 800 |
| SCMiner built-in human regulators | 50, 80, 100 | 20, 40, 80 |
| Representative independent scMultiSim | 50, 100, 200 | 100, 200, 400 |
| Representative observed human pathway, direct input | 40, 60, 80 | 100, fixed by checkpoint |
| Representative observed Arabidopsis | 4000, 8000, 12179 | 40, 70, 100 |

These are bounded calibration ranges, not a claim that all possible sizes,
parameters, group counts, prior densities, or training settings are covered.
Three points on each independently variable axis support inspecting curvature;
extrapolation beyond it still requires independent validation. The representative
scMultiSim grid deliberately leaves intermediate dimensions, such as 75 genes
and 150 cells, available for subsequent held-out checks.

DigNet and PLaNet's selected pretrained checkpoints require 100 expression
features. With `metacell=false`, 50, 75 or 200 input cells are incompatible, so
the direct-input profiles measure only gene-size variation at 100 cells. The
separate `metacell=true`, `metacell_count=100` profiles measure the preprocessing
and inference cost for larger inputs; they do not justify cell-count
extrapolation for the direct-input profiles. The coverage validator recognizes
this fixed axis only for these integrations with metacell explicitly disabled.

scRegulate's `global_provided_prior_fast_binary_prior_v2` uses the explicit
`prior_score_policy: "binary"` input policy. Unit prior weights satisfy the
upstream weighted-sum filters for the configured minimum target/TF counts.
The historical sparse weighted-prior profile is archived separately, rather
than relabeling its timings as binary-prior measurements. Omitting the policy
preserves the previous random positive weights and deterministic input bytes;
the grouped, dense-prior and representative profiles retain their inputs.
Similarly, PIDC's two baseline discretization profiles retain four bins: their
size coverage does not establish calibration for arbitrary bin counts.

`prepare_cost_calibration_source.py` creates an independently seeded scMultiSim
source for cost calibration. `prepare_cost_coverage.py` checks its provenance and
file hashes, retains every registered historical fixture unchanged, and expands
the representative recipes using deterministic submatrices. It preserves the
source regulator fraction, balances cell groups, filters TF lists and prior
edges to retained genes, and preserves ordered phenotype labels. Observed human
inputs are only subsetted; additional expression values are not invented.
Arabidopsis subsets retain the complete published marker/TF universe while
varying background genes and cells. Derived inputs and complete selection
receipts belong in ignored evidence storage, not the source repository.

For example, after creating an independent source:

```bash
python wrappers/inference_tools/scripts/prepare_cost_coverage.py \
  --fixture-root /path/to/original-fixtures \
  --fixture-root /path/to/corrected-seed-fixtures \
  --scmultisim-source /path/to/calibration-source \
  --output-root /path/to/expanded-fixtures
```

The preparation command updates the registered representative recipe hashes
only after all fixture derivations succeed. Repeating it with the same inputs
produces the same derived files and recipes. It performs no inference and never
imports validation timings as calibration evidence. Existing dimensions keep
their historical fixtures, rather than replacing their measurements with a new
input matrix of the same dimensions.

Structural validity and calibration coverage are distinct checks. A well-formed
single-size cost profile can support that size, but does not establish a growth
model. Coverage must be evaluated separately for every parameter/input profile
and every thread/RAM assignment, and a measured grid is not automatically a
validated predictive model. Three repetitions are the minimum for each newly
measured point; intermediate sizes remain separate validation data.

## Incremental campaign and monitoring

`run_cost_coverage.py` is the common coordinator for selected catalog tools,
including tools without a previous `cost.json`. Recipes with a
`calibration_design` supply their declared resource assignments and repetitions;
other recipes retain the fallback of two threads (one for serial tools), 8 GiB
and three repetitions. `--threads` and `--ram-gb` select an explicit resource
panel when requested. Admission follows the resource-aware executor described
above, with automatic concurrency by default. The per-run cap defaults to
1,800 seconds and is frozen in the plan and worker command. Retaining historical
thread/RAM measurements does not imply coverage at other assignments.

```bash
python wrappers/inference_tools/scripts/run_cost_coverage.py \
  --evidence /path/to/campaign \
  --fixture-root /path/to/expanded-fixtures \
  --reuse-results-dir /path/to/old-baseline-evidence \
  --reuse-results-dir /path/to/corrected-image-evidence \
  --reuse-representative-results-dir /path/to/old-representative-evidence \
  --plan-only
```

For a tool with a declared synthetic panel, inspect
`--tool TOOL_ID --skip-representative --plan-only` without a representative
fixture root. The recipe already declares its complete resource panel. Supply
compatible retained evidence with `--reuse-results-dir` and source proof with
`--reuse-source-root`/`--reuse-source-manifest` when needed. An unchanged version
tag does not permit reusing timings from a different immutable native image.
The coordinator streams the existing worker's logs directly; no separate
campaign runner or progress-observer process is required.

Remove `--plan-only` to execute. The campaign freezes source hashes, tool specs,
immutable Docker image IDs and commands before measurement. `status.json`
records the active job and terminal outcomes; `logs/` retains each worker log.
Reusing the same command and evidence directory resumes verified observations.
Changed sources or images require a new campaign directory, which can still
reuse scientifically compatible point receipts from `cost.json`.
When a corrected image invalidates historical measurements, explicitly use
`--remeasure-incompatible-history` to retain and remeasure those original
size/resource points. This preserves their explicit point list; it does not
expand every historical size across every historical resource combination.

For PM2, use `autorestart: false`, one instance, and `kill_timeout: 60000`.
Successful completion must not restart the entire campaign. On an intentional
restart, the supervisor cleans only its verified old worker process group and
containers mounting its own evidence directory before continuing. Worker output
is also streamed to PM2 logs. It preserves failed receipts and reports failures
explicitly; a job failure does not prevent the other tools from being measured.

The campaign finishes with provenance and complete-attempt validation. The
manual equivalent for the selected tool is:

```bash
python wrappers/inference_tools/scripts/validate_tool_costs.py \
  --tool TOOL_ID --require --require-provenance --require-attempts \
  --coverage-report /path/to/coverage.json
```

`--require-attempts` requires a real terminal result for every planned repetition,
including timeouts. `--require-coverage` separately requires sufficient successful
timings over the numerical domain. The report retains that numerical coverage
assessment even when only the attempt gate is required. A completed campaign
with timeouts must not be described as complete successful timing coverage.

Costs updated incrementally during an unfinished campaign are working artifacts,
not completed release calibration. When the agreed deliverable is the complete
panel, retain resumable working output outside the active catalog until all
planned attempts and applicable validation gates are satisfied. Diagnostic
pilots do not authorize replacing the agreed panel with a smaller one. If
calibration is deferred, leave the tool without `cost.json`. Native image or
input changes invalidate affected timings even if an unpublished image tag
stays unchanged; withdraw obsolete models rather than adjusting old times.

## Acceptance of new integrations

Use the shared [integration workflow](../../../docs/INTEGRATION_WORKFLOW.md)
for the scientific and functional gates. For costs, record three separate
outcomes: schema/semantic validity, coverage of the declared calibration domain,
and observed accuracy on independent predictions. Neither coverage nor the
release provenance gate establishes the third outcome.

For a new tool without `cost.json`, inspect its common-coordinator plan with
`--tool TOOL_ID --skip-representative --plan-only`, then execute the authorized
panel. No alternate bootstrap runner is needed. Representative fixtures are
required only when that phase is selected.

The campaign's `--plan-only` is a CPU-only static inspection: it does not need
Docker, resolve image IDs, verify fixtures, create evidence directories or run
measurements. Its JSON lists profiles, sizes, actual resource assignments,
repetitions, retained historical points, total run allocations and a sequential
timeout budget. This budget counts all planned repetitions before verified
reuse; it is not a measured ETA and excludes setup and validation. Inspect
fixture provenance and host capacity before executing. Real execution freezes
this plan in `execution-plan.json`, then immutable images and commands in
`campaign.json`. A changed plan requires a new evidence directory.

Reserve hold-out dimensions and datasets before measurement. Freeze the exact
`cost.json` snapshot, estimator source/model, predictions, scope and thresholds
before collecting those timings. Keep the hold-outs outside calibration recipes
and `--reuse-results-dir`; if subsequently fitted, retire their independent
validation claim and reserve fresh observations. Test interpolation inside the
supported geometry and extrapolation separately when that behavior is claimed.
Include every resource/profile stratum claimed by the new integration.

The shared checker reads retained evidence and never runs containers or updates
costs:

```bash
python wrappers/validate_cost_predictions.py \
  --plan /path/to/frozen-prediction-plan.json \
  --observations /path/to/validation-observations.json \
  --report /path/to/prediction-validation.json
```

Its authoritative input contract is in
[`validate_cost_predictions.py`](../../validate_cost_predictions.py) and
`python wrappers/validate_cost_predictions.py --help`. Prepare the two JSON
manifests autonomously from the retained benchmark evidence:

| Manifest | Required evidence |
| --- | --- |
| Plan | `schema_version: 1`; `cost` and `estimator` artifact references; `training_observations`; `cases` |
| Each planned case | Unique `id`; `kind: interpolation` or `extrapolation`; `identity`; positive numeric `dimensions`; `inputs`; `predicted_typical_seconds`; `predicted_planning_seconds`; explicit finite `max_relative_error` and `min_planning_coverage` |
| Observations | Exact `plan_sha256`; `cases` with identical case IDs, each holding `observations` with at least three successful distinct raw receipts |
| Each training/validation observation | Unique `id`; `receipt`; `identity`; `dimensions`; nonempty `inputs` and `outputs` artifact inventories |
| Artifact reference | `path` and SHA256 `sha256`, relative to its enclosing manifest; a receipt can add RFC6901 `pointer`, e.g. `/0`, to select a raw JSON measurement row. Each input additionally needs `role: primary` (workload matrix/configuration) or `role: shared` (annotation/native resource); at least one primary input is required |
| Identity | `profile_id`, immutable `image_id`, `params_sha256`, `source_sha256`, `hardware_id`, `threads`, `ram_gb` |

`params_sha256` identifies the resolved recipe with its dimensional mappings;
point coordinates are recorded separately. Include resolved parameter files and
scientific metadata in the retained input inventory. Receipt rows must retain
`status`, individual `seconds`, a stable unique `run_id`, all observed identity
fields and dimensions (flat or nested), and `input_sha256` matching the verified
inventory (hash list or name-to-hash mapping). Every representation of a field
must agree with the manifest. An adapted receipt can retain `raw_evidence`
artifact references (relative to the receipt, with optional pointers) to original
timing rows and configuration/provenance. The checker verifies those references
and rejects contradictory original fields or reused source execution rows.
Review and retain the mapping; do not invent missing execution identities or
measurements. Historical timings lacking sufficient evidence remain pending.
Preserve failed attempts separately and report
them; an unsuccessful receipt fails this gate. Calibration-labelled receipts
cannot be relabelled as validation.

The checker verifies file hashes, copied-receipt/ID disjointness, distinct
calibration and held-out dimensions/primary inputs, matching profile/resource
identities, median relative error and the observed fraction of repetitions under
the planning budget. These fractions from three repeats are not calibrated
prediction intervals. Its interpolation/extrapolation label checks axis ranges;
the separate coverage gate and scientific review must establish geometric
support. Shared annotations may recur; reusing a primary training input is
rejected even if another annotation is appended or the input is labelled shared.
Hashes and manifests cannot authenticate measurements, prove that the training
list or validation attempt/failure inventory is complete, establish chronology
or prove experimental independence. The checker also does not execute the
hashed estimator or prove predictions came from it. Retain review evidence for
those links, input roles and any receipt adaptation alongside the report.

Coverage counts at least the declared number of successful repeats (minimum
three) even when a point also retains failures; `failed_repetitions` is reported
separately for reliability review. This numerical-support check is distinct
from retaining failed outcomes and from completing all planned attempts.

For bulk time series, `dimensions` must explicitly include timepoints per series,
independent series/replicates and relevant dynamic settings. Inputs must contain
the actual temporal metadata and matrices. Generic non-temporal coverage readers
analyze genes versus total columns (simulators call columns `cells`); passing
them alone does not certify temporal axes or fixture validity. The inference
validator's temporal coverage check verifies the separately declared geometry.

Observed-time recipes use the single expression matrix and column-descriptor
table, declare consumed observation roles, and vary temporal dimensions and
resource assignments independently. A full declared panel is a finite domain,
not coverage of every parameter/input combination.

The ETA selector requires matching temporal geometry, units, sampling design,
regularity and stationary sample count. Unsupported or unmeasured shapes use
an explicit fallback estimate; this is not measured timing evidence. A complete
calibration and independent prediction review remain necessary before claiming
realistic measured costs for these tools.

## Current temporal integrations

| Tool | Profiles | Planned executions | Current cost state |
| --- | ---: | ---: | --- |
| dynGENIE3 | 68 | 3,348 | Complete measured panel: 1,116 points and 3,348 successful observations; strict provenance, attempt and coverage checks pass. |
| SWING | 77 | 4,158 | Full panel designed; calibration deferred, no `cost.json`. |
| BiXGBoost | 27 | 1,458 | Full panel designed; calibration deferred, no `cost.json`. |

Each panel crosses CPU allocations 1/2/4 with RAM limits 8/32 GiB and uses three
repetitions. No calibration is active or queued. Independent prediction accuracy
remains deferred for all three tools. dynGENIE3's verified executor-only
fingerprint reconciliation preserves original observations and provenance;
it does not authorize reuse across a native image or input change. SWING's
aggregation optimization changed native wall times, so previous-image timings
are historical diagnostic evidence and are not a current cost model.

After descriptor unification, scGeneRAI's previous model is archived outside the
active catalog pending complete recalibration of the changed wrapper. dynGENIE3
retains its complete measured panel after exhaustive byte-identity verification
of all measured inputs and the unchanged timing component. Only derived profile
fingerprints were refreshed; original observation/provenance bytes and times
were retained. This adds no kernel exception to automatic resumption.
