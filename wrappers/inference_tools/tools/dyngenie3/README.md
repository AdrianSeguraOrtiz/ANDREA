# dynGENIE3 for Osaka

The candidate integrates the official **Python** implementation at
`27eddd960d50e82791a8e0e5f8fcf6d679fe3eac`. Python exposes the broadest method
surface and was used for the article's results. No comparative language timing
was supplied or measured. The source audit, exclusions, parameter matrix and
compatibility patches are in [integration_decisions.md](integration_decisions.md).

The wrapper and shared temporal delivery are implemented. Physical image,
repeatability, resource and cost acceptance require actual execution receipts;
draft files alone do not activate a catalog tool. The authoritative campaign
state is `release-logs/dyngenie3/campaign-expanded-20261021/state.json` for the
expanded campaign. `campaign/` retains the first failure;
`campaign-recovery-20261021/` retains the successful 17-case image verification
and every original calibration observation collected before expansion. See the [dated integration review](integration_review_20261021.md).
Registry publication is separate and is never performed by the supervisor.

## Scientific input and output

Supply a gene-by-observation expression TSV, enriched `timepoints` and
`replicates` tables, and a `bulk-temporal/1` declaration in the dataset manifest.
See the [shared contract](../../../../docs/bulk-time-series-contract.md) and
[fixture manifest](../../tests/fixtures/dyngenie3/dataset-manifest.json).

Each longitudinal biological series must have at least two distinct observed
times. Unequal intervals are supported. Metadata are joined by exact column ID
and sorted within each series; no transition crosses series boundaries.
Technical duplicates, independently sampled cohorts, missing/nonfinite values,
and times whose order or interval overflows/collapses in upstream float32 are
rejected. There is no implicit averaging, normalization or imputation.
For `alpha="from_data"`, the time separation between each varying gene's
expression minimum and maximum must also stay finite at native float32
precision. Adjacent intervals can be valid while that longer subtraction
overflows. Constant genes remain permitted with rate zero, and explicit
scalar/vector alpha does not impose this additional estimation-only check.

An optional `tf_list` restricts candidate sources. Optional
`steady_state_expression` is a separate gene-by-sample matrix; it must contain
the exact expression gene universe and use a compatible expression scale.
Its sample identifiers are independent of the time-course columns. Explicit
alpha values have units inverse to the declared observed time unit.

The result is one directed, unsigned, pooled network. Positive target-normalized
native importances are retained at full precision; they are ranking weights,
not probabilities or signed effects. The method estimates no edge-specific
lag. Native VIM, resolved alphas, parameters, target seeds, input hashes,
quality diagnostics, runtime versions and observed worker pools are retained
under `provenance/`. Models are saved only when requested. Undefined optional
quality diagnostics are recorded as null with an explanation.
In particular, zero alpha can make the steady-state inverse OOB prediction
nonfinite. The compatibility guard marks its Pearson diagnostic undefined
before SciPy can reject those values; it preserves the inferred VIM and never
substitutes a zero correlation or changes the fitted rate.

## Reproducible runtime

The Docker build downloads only the public, checksum-verified Python source.
It does not copy the evidence clone or paper. The base image is pinned by
digest; Python 3.11, NumPy, SciPy, scikit-learn, joblib and threadpoolctl use
hashed pins in [requirements.lock](requirements.lock). The patch restores
compatibility with the pinned dependencies, manages worker cleanup and adds
explicit per-target seeds. It preserves the upstream scientific defaults,
including `alpha="from_data"`, RF, `K="sqrt"` and 1000 trees.

ANDREA's resource allocation controls the outer target-worker pool. Each forest
and native math pool use one worker/thread. `seed=0` is an explicit ANDREA
extension because upstream's method-level Python API has no seed argument.
Reproducibility claims are limited to the image/environment actually tested.

## Deferred verification and costs

`continue_integration.py` freezes relevant source hashes and commands, then
waits for an explicitly successful terminal state in the external experiment's
state file. A running, failed, missing or malformed state does not authorize
heavy work. Run it with the worktree's `.venv/bin/python`, one dedicated PM2
process and autorestart disabled. Its required arguments are `--external-state`
and `--evidence`; `--plan-only` writes the frozen plan without Docker or fits.
Keep evidence outside this source directory. Source changes require a newly
reviewed evidence directory.
`--reuse-image-from` can retain a proven prior image only when all installed
source subjects and the scientific spec remain byte-identical; it imports no
acceptance status. A wrapper correction requires a new image version. Recovery
therefore uses `1.0.1`, retaining the original `1.0.0` image and receipts.

The sequence builds only an isolated candidate tag, verifies the immutable
image, and activates the local spec, smoketest and template entry only after
functional acceptance. Calibration costs stay in a private staging catalog
until the structural, temporal-domain and independent prediction gates pass.
It preserves receipts, logs and failures, checks the
external state again before heavy phases, and never overwrites an unknown
image or cost artifact. It does not push, publish, commit or merge anything.
The finalizer additionally executes the fixture through the real core/CLI,
checks the activated GUI contract and catalog map, reruns the cost validators,
and writes `acceptance/integration.json` with hashed receipts. The source
`integration.json` remains the frozen preparation dossier. Failed attempts
remain available for review; they do not become acceptance evidence.

The verifier includes five saved-model diagnostic cases using an
[eight-gene fixture](../../tests/fixtures/dyngenie3/quality/README.md): RF quality
from time courses alone, ET with steady-state samples, and zero-decay,
one-tree and one-training-sample edge cases. It reconstructs native VIM, OOB
Pearson and seeded top-five stability from saved forests inside the pinned
image without another fit. Each probe is capped at 120 seconds within its
case's 3600-second limit. Nine additional eight-gene executions verify actual
1/2/4/8 target workers and exact repeated VIM across those allocations, with
changed-seed sensitivity. The matrix has **26 fits in 26 containers**, including
five internal probes; its timeout sum is 93,600 seconds. Earlier 12- and 17-case
receipts retain their original scope.

The expanded cost recipe allocates **1944 calibration runs**: 81 scientific
coordinates, eight CPU/RAM strata (1/2/4/8 CPUs × 4/8 GiB), three repetitions.
The default panel crosses genes 50/100/200, points per series 4/8/16 and series
1/2/4. ET and TF variants cross three gene/time levels at two series. Other
panels vary genes at fixed t8/s2: scalar alpha, quality, saved models, K=1/all,
and 100/300 trees; real 1000-tree defaults remain in their own profiles.
Steady-state counts 10/20/40 cross the three gene levels. An irregular t8/s2
panel also spans genes. The vector-alpha panel retains its exact 100-gene
vector and varies timepoints only. Unmeasured factor combinations and TF-list
densities are outside this finite panel.

Another **312 runs**, 13 cases across eight resource strata with three repeats,
check independent gene-axis predictions for representative registered branches.
The manifest declares every reserved case before measurement. Vector-alpha
size extrapolation and interpolation across temporal shapes remain unsupported.
These are runtime prediction checks, separate from biological accuracy.
Including 26 functional cases and one real core/CLI execution gives **2283**
allocated containers and a summed execution timeout cap of **8,218,800 seconds**.
This 95.125-day cap is not an ETA. The campaign runs one container at a time;
measured progress and a provisional duration range are retained in campaign
planning evidence. Structural validity, domain coverage and independent
prediction accuracy must pass separately before `cost.json` is registered.

Expansion preserves the previous campaign and uses `--reuse-calibration-from`
with `--reuse-image-from`. Reuse retains each original run ID, time and source
provenance. The benchmark regenerates inputs and checks exact bytes plus
image/hardware/parameter context before skipping an observation. A separate
hashed compatibility receipt proves that installed code and the input/timing
implementation did not change; the prediction adapter verifies it before
accepting a historical source identity. New gates are always run against the
expanded panel; old success statuses are never imported.

Independent predictions freeze the actual ANDREA planner calls and cost/spec
snapshots before measuring the reserved inputs. The planning prediction is the
exact ETA consumed by core; a p50-derived typical-time diagnostic is identified
separately. An additional operational reserve does not help a failed predictive
coverage gate pass. Successful validation at t8/s2 does not validate other
temporal shapes or every point in the measured panel.

The pinned source has no project-level software license. This remains an
unresolved redistribution question; neither the article license nor local
verification is a registry publication receipt.
