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
- top-level or per-profile `temporal`: the exact `bulk-temporal/1` runtime
  contract copied into `execution.json`. `temporal_fixture` separately declares
  positive `series_count`, at least three `timepoints_per_series`, optional
  `steady_state_samples`, and `sampling: regular` or `irregular`. Temporal
  profiles require `timepoints` and `replicates` inputs. Each `GENESxCOLUMNS`
  point must satisfy `COLUMNS = series_count * timepoints_per_series`; the
  separate steady-state matrix contributes no temporal transitions. These
  fields are opt-in and preserve historical non-temporal fixture bytes.

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
assignments must match before a timing can be reused. Missing repetitions are
executed; completed successful points are checkpointed atomically into costs.
Failures return nonzero and retain evidence without discarding completed points
or unrelated historical profiles.

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

The Osaka generator extension preserves those measurements. Its finite
`shared/recipe_compatibility.json` review records 57 matching resolved profile
fingerprints and 171 byte-identical fixture comparisons against the preparation
baseline. It applies only to the exact reviewed source hashes and profiles
without temporal or steady-state inputs. Any further generator edit invalidates
that bridge and requires another explicit compatibility review or new evidence;
it never rewrites a measured `cost.json` or timing receipt.

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

`run_cost_coverage.py` coordinates the baseline and representative recipes for
all 24 inference tools. It measures one container at a time, using two threads
(one for single-thread tools), 8 GiB, and three repetitions. Other historical
thread/RAM measurements remain in the cost files; expanding the size grid at
one resource assignment does not imply equivalent coverage at every resource.

```bash
python wrappers/inference_tools/scripts/run_cost_coverage.py \
  --evidence /path/to/campaign \
  --fixture-root /path/to/expanded-fixtures \
  --reuse-results-dir /path/to/old-baseline-evidence \
  --reuse-results-dir /path/to/corrected-image-evidence \
  --reuse-representative-results-dir /path/to/old-representative-evidence \
  --plan-only
```

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

The campaign finishes with both provenance and coverage validation. The manual
equivalent is:

```bash
python wrappers/inference_tools/scripts/validate_tool_costs.py \
  --require --require-provenance --require-coverage \
  --coverage-report /path/to/coverage.json
```

Costs updated incrementally during an unfinished campaign are working artifacts,
not a completed release calibration. The final validation must pass before
publishing them as such. Keep campaign evidence in ignored storage.

## Acceptance of new integrations

Use the shared [integration workflow](../../../docs/INTEGRATION_WORKFLOW.md)
for the scientific and functional gates. For costs, record three separate
outcomes: schema/semantic validity, coverage of the declared calibration domain,
and observed accuracy on independent predictions. Neither coverage nor the
release provenance gate establishes the third outcome.

For a new tool without `cost.json`, bootstrap with its existing per-tool
`benchmark_costs.py --tool TOOL_ID --plan-only` and then the planned benchmark
after resource availability is confirmed. `run_cost_coverage.py` is the existing
catalog campaign coordinator: its execution ordering reads prior costs and
expects registered representative recipes/fixtures. It is not the bootstrap
path for an unmeasured integration.

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
separately for reliability review. Campaign publication and raw-receipt reuse
retain their own stricter success checks.

For bulk time series, `dimensions` must explicitly include timepoints per series,
independent series/replicates and relevant dynamic settings. Inputs must contain
the actual temporal metadata and matrices. Generic non-temporal coverage readers
analyze genes versus total columns (simulators call columns `cells`); passing
them alone does not certify temporal axes or fixture validity. The opt-in
temporal inference gate below verifies the separately declared geometry.

The initial [dynGENIE3 panel](dyngenie3.json) is **planned and unmeasured**. It
contains 27 default points crossing 50/100/200 genes, 4/8/16 observed timepoints
per series and 1/2/4 independent biological courses. Seven additional center
points isolate ET, scalar/vector decay, steady-state data, TF restriction,
quality scores and model serialization. Each branch is limited to its exact
100-gene, 8-timepoint, 2-series center; it provides no branch size-growth claim.
All profiles preserve upstream defaults except the named branch change.

The declared four resource strata (1/2 threads crossed with 4/8 GiB), three
repetitions and serial execution imply 408 calibration runs. Two separately
reserved default holdouts vary only genes (75 and 250) at the exact 8-timepoint,
2-series profile and add 24 runs. Their 3600-second per-run timeout sums
are execution caps of 1,468,800 and 86,400 seconds respectively; measured ETA,
startup overhead and validation overhead remain unknown. No automatic retries
are planned. Holdout artifact hashes, estimator snapshot and predictions must
be frozen before collecting validation timings. The config records these
reservations and thresholds; it is not a valid frozen prediction manifest.

These allocations do not close the integration even if all runs succeed.
The temporal coverage gate assesses genes within each fixed timepoint/series
shape, requires all four declared resource strata and three distinct captured
execution receipts per point, and reports the combined 27-point default domain
separately. It verifies temporal dimensions, input hashes, measurement context
and image identity. The seven branch centers remain evidence only at their
exact point. This finite coverage is separate from predictive accuracy.
The ETA selector now requires an exact validated temporal shape, time unit,
sampling design, regularity and steady-state sample count; unsupported shapes
fall back with a warning instead of pooling profiles with equal total columns.
That guard has no measured accuracy evidence. The autonomous continuation
supervisor keeps calibration in a private staging catalog, then checks domain
coverage and freezes the actual ANDREA planner, snapshot and eight predictions
before collecting the 24 independent holdout timings. The planning budget is
the core ETA itself; an operational reserve cannot help pass its coverage test.
It registers costs only after those gates pass and requires a
separate acceptance dossier. Predicting across timepoint/series shapes remains
unsupported; 432 allocations are not an acceptance receipt.

While the existing PM2 experimental validation is active, this integration
stays at static planning: no Docker build, model fit, calibration or holdout
measurement. After the experimental work finishes, inspect capacity and the
saved resolved plan before executing. Absence of `cost.json` is intentional.
