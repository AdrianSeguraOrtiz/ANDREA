# Osaka integration status

This summarizes the current integration scope and remaining work. The shared
[integration workflow](INTEGRATION_WORKFLOW.md) defines the procedure; each
method's `integration_decisions.md` records its scientific choices and exclusions.

## Catalog integrations

| Tool | Functional integration | Calibration |
| --- | --- | --- |
| [dynGENIE3](../wrappers/inference_tools/tools/dyngenie3/integration_decisions.md) | Complete for the declared inference scope. | Full measured panel: 68 profiles, 1,116 resource/size points and 3,348 successful observations. Strict provenance, terminal-attempt and numerical coverage checks pass. |
| [SWING](../wrappers/inference_tools/tools/swing/integration_decisions.md) | Complete for the declared inference scope, including equivalent aggregation optimization. | No `cost.json`. The complete 77-profile/4,158-execution panel is designed; calibration is deferred. |
| [BiXGBoost](../wrappers/inference_tools/tools/bixgboost/integration_decisions.md) | Complete for the declared inference scope. | No `cost.json`. The complete 27-profile/1,458-execution panel is designed; calibration is deferred. |
| [PFBNet](../wrappers/inference_tools/tools/pfbnet/integration_decisions.md) | Official expression-only workflow; KO fusion deliberately excluded. Native, CLI and browser checks cover this scope. | Executable complete finite panel: 40 profiles, 120 size points per resource stratum and 2,160 future executions. No `cost.json`; calibration deferred. |

The four tools use the standard Dockerfile, runtime wrapper and scientific
decision log, canonical catalog specification and shared smoke/fixture
locations. PFBNet's executable recipe reuses the existing synthetic generator
and coverage validator; no KO-specific generator is needed.
Pinned upstream sources and dependency hashes define installation. Local image
tags remain `1.0.0`; no image publication is implied. No calibration is running
or queued. Independent prediction-accuracy checks remain deferred for all four.
Complete calibration coverage does not establish accuracy for arbitrary inputs,
parameters or hardware.

## Normalized inputs and simulator handoff

Observed bulk data uses one genes-by-observations `expression.tsv` and one
`column_descriptors.tsv` table joining every expression column exactly once.
Compatible temporal and stationary observations share the matrix. Roles,
series, biological/technical identities, sampling design, observed time and unit
belong in the descriptor. The [input contract](bulk-time-series-contract.md)
contains field definitions and usable examples. Separate timepoint, replicate
and stationary-expression inputs and the public temporal declaration were
removed rather than retained as development compatibility paths.

The descriptor InputSpec remains generic and stable across dataset selectors.
Catalog eligibility uses declared dataset metadata, available input keys and
established parameter/execution rules. Scientific interpretation and a method's
temporal requirements are checked for the requested run, not while scanning the
catalog. GUI help supplies context without duplicating file metadata or changing
the input schema. Other tools retain their established descriptor semantics,
including scGeneRAI's complete categorical covariates. Normalized fields with
non-applicable empty values are explicitly omitted from its conditioning table
and recorded, while arbitrary incomplete categorical fields still fail.

Temporal writers in dyngen, GeneNetWeaver and GeneSPIDER2 emit the same normalized
files; simulator specifications, manifests, fixtures and generated-data handoff
checks use that contract. dynGENIE3 consumes temporal and stationary observations;
SWING, BiXGBoost and PFBNet consume their declared temporal roles and record
stationary rows as unused. Optional intervention annotations now share
`column_descriptors` with the observation metadata. The separate normalized
perturbation-design input is removed; intervention definitions remain in
`interventions.tsv`. All five affected simulator writers/specs agree with this
contract. Intervention onset is distinct from observed sampling time.
Those intervention fields are optional simulator annotations, not requirements
of any of the four temporal inference tools. The shared runnable example contains
only the ten temporal descriptor fields, 126 temporal observations and six
independent stationary controls (8 genes by 132 columns); its seven tool/backend
requests complete through the ordinary CLI. No KO measurements or intervention
annotations are needed in this example. All four development image tags remain
`1.0.0`, and this cleanup changes no runtime wrapper or calibrated timing.
Wrappers do not invent
transitions across series, average technical duplicates or infer identities from
column names. Each tool exports the canonical six-field `network.csv`; native
diagnostics and models remain declared auxiliary artifacts.

## Method scope and verification

- **dynGENIE3:** RF/ExtraTrees, native parameter defaults, data-derived/scalar/vector
  degradation, regulator subsets, quality diagnostics and saved models. The
  double-knockout trajectory predictor is a separate downstream task. Progress
  exposes phases, without a completed-target counter.
- **SWING:** RF, Lasso and PLSR with native defaults and documented resources,
  completed-window progress, aggregation and export. Inconsistent upstream
  window/random-time controls and unsafe TF-restricted Lasso are excluded.
  Positive-importance filtering and native lag/score definitions are preserved.
- **BiXGBoost:** native `p_lambda`, `p_alpha` and `timelag`, derived series count,
  bidirectional split-count scores and completed-model progress. Native fixed
  model settings remain fixed. Equal-length regular longitudinal series are
  required; regulator restriction and stationary fitting are unsupported.
- **PFBNet:** pin the official source to
  `86ec8eb37aa8834645600eaaee51012342ac6324` and expose its E.coli
  accumulated-expression workflow. It accepts regulator candidates and omits KO
  fusion and DREAM4 normalization. KO fusion is deliberately outside catalog
  scope. Fixed learner settings and native score formulas are preserved.
  Expression, column descriptors and optional TF lists cover all admitted inputs;
  there is no `variant` parameter or hidden KO fallback.

Physical smoke matrices and bounded CLI runs cover the implemented branches,
normalized handoff, canonical outputs, progress and actual resource settings.
They do not establish biological validity or user acceptance of every GUI
workflow. BiXGBoost's recorded GUI check covers candidate display; its active
catalog inference was verified through the ordinary CLI.
PFBNet passes seven native smoke settings and exact canonical-output comparisons
against the original E.coli implementation with all genes and restricted TFs,
changed seeds and one/two-CPU controls. Its current browser workflow is checked
through Results Explorer. Its complete finite cost recipe is executable, with
no calibration started and no fitted cost model.

SWING's aggregation indexes filtered edge rows once instead of rescanning the
whole table per edge. Equivalence checks cover all 16 lag reductions, five sort
policies, row/index/tie cases and canonical CSV bytes. RF tree tracing is an
explicit diagnostic mode; normal runs retain actual model metadata without
per-tree hooks. A controlled PLSR example that exhausted the original 1,800-second
budget finishes in 70.9 seconds after correction; an RF example improves from
118.5 to 113.6 seconds. These individual examples establish neither a universal
speedup nor a general runtime estimate. Old-image timings are not a current
cost model and are not imported into a future corrected-image calibration.

## Common cost executor

The complete panel resides in declarative recipes, independently of machine
capacity. The existing coordinator and measurement driver share atomic CPU/RAM
reservations and admit concurrent fits within each profile. Docker enforces
selected CPU sets, quotas and RAM limits; a single writer records real terminal
observations. Effective affinity/cgroup limits, live availability and conservative
headroom bound admission. This coordinates participating campaigns; it cannot
isolate unrelated host workloads or the hypervisor. Ordinary `infer-network`
execution is outside this cost-only mechanism.

Every native fit retains the agreed 1,800-second cap. Busy or unsupported
resource requests and interrupted work remain pending. Timeouts are real
attempted outcomes, not successful elapsed times or a basis for pruning larger
unattempted inputs. Complete attempts, sufficient successful timing coverage and
independent prediction accuracy are distinct checks.

dynGENIE3's derived profile fingerprints were reconciled through the existing
shared driver's offline reuse path after a verified executor-only migration.
Original observation IDs, elapsed times, inputs, image identity, aggregate values
and provenance were preserved. Native image or input changes do not qualify for
that reconciliation. When such a change invalidates timings, withdraw the old
model; defer calibration or attempt the complete agreed panel. Diagnostic pilots
must not substitute a reduced published model. Retained raw evidence belongs in
ignored campaign storage, not wrapper directories or catalog models.

## Existing simulator calibration limitations

The initial audit of the 0.2.1 catalog found structural/provenance-valid costs
but incomplete domains under the stronger simulator geometry gate:

| Simulator | Profiles passing geometry |
| --- | ---: |
| BoolODE | 2 / 5 |
| dyngen | 0 / 27 |
| GeneNetWeaver | 5 / 6 |
| GeneSPIDER2 | 0 / 8 |
| GRouNdGAN | 1 / 9 |
| scMultiSim | 0 / 29 |
| SERGIO | 3 / 10 |
| SynTReN | 4 / 6 |

These historical diagnostics do not invalidate the original measurements or
certify prediction accuracy. A separate profile's wider grid does not cover a
single-size configuration automatically. Fixed axes need native justification;
SERGIO's custom-gene recipe metadata still needs review. Simulator recalibration
and independent validation remain outside the current integration scope.

## Descriptor unification and cost applicability

The five changed simulators (GeneNetWeaver, GeneSPIDER2, dyngen, GRouNdGAN and
SynTReN) and scGeneRAI require fresh cost applicability for their changed wrappers
and images. Their previous cost files were preserved byte-for-byte outside the
active catalog and withdrawn from planning. No timings were rewritten or new
calibrations launched. Their ordinary existing fallback estimates apply until
complete recalibration; native GeneSPIDER2 perturbation matrices remain native
artifacts, not public normalized extras.

dynGENIE3's wrapper, image, parameters, measured inputs and timing component
remain unchanged. The cleanup of the unused synthetic perturbation writer changes
the generator's whole-file hash. An exhaustive offline applicability review
verified all 68 profiles, 1,116 resource/size points, 3,348 observations and 14,094
retained file bytes/hashes. Only the 68 derived profile fingerprints changed;
measurement identities, times, aggregates, evidence and original provenance remain
identical. Strict provenance, attempt and coverage validation passes. This review
adds no exception to the shared resumption policy across different kernels.

Final unification verification: 1,463 repository tests pass (13 optional checks
skipped), eight native simulator scenarios and seven PFBNet smoke settings pass,
and scGeneRAI's declared descriptor-selection artifact is exported. Nine ordinary
CLI inference runs pass, including a fresh GeneNetWeaver dataset handed unchanged
to PFBNet and dynGENIE3. The PFBNet browser run reaches READY/100 percent and
Results Explorer. The common driver's plan-only check resolves all 40 PFBNet
profiles without starting a calibration.
