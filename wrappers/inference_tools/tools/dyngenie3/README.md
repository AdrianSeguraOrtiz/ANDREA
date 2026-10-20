# dynGENIE3 for Osaka

The candidate integrates the official **Python** implementation at
`27eddd960d50e82791a8e0e5f8fcf6d679fe3eac`. Python exposes the broadest method
surface and was used for the article's results. No comparative language timing
was supplied or measured. The source audit, exclusions, parameter matrix and
compatibility patches are in [integration_decisions.md](integration_decisions.md).

The wrapper and shared temporal delivery are implemented. Physical image,
repeatability, resource and cost acceptance require actual execution receipts;
draft files alone do not activate a catalog tool. The authoritative campaign
state is `release-logs/dyngenie3/campaign/state.json`, when the supervisor is
started. Registry publication is separate and is never performed by it.

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

The cost recipe allocates **408 calibration runs**: 27 default points crossing
genes 50/100/200, points per series 4/8/16 and series 1/2/4, plus seven explicitly
limited branch centers; each uses three repeats in four thread/RAM strata.
Another **24 runs** are reserved for independent gene-axis interpolation and
extrapolation at the exact default t8/s2 shape. Branch centers do not establish
growth curves. Predictions across temporal shapes remain unsupported.
Timeout sums are execution caps, not measured ETAs. Structural validity,
declared-domain coverage and observed predictive accuracy remain separate gates.
Including 12 functional cases and one core/CLI execution gives 445 allocated
containers and a summed scientific timeout cap of 1,602,000 seconds. Build and
validation overhead are additional and unknown. The campaign runs one container
at a time with one or two assigned CPUs and 4 or 8 GiB RAM.

Independent predictions freeze the actual ANDREA planner calls and cost/spec
snapshots before measuring the reserved inputs. The planning prediction is the
exact ETA consumed by core; a p50-derived typical-time diagnostic is identified
separately. An additional operational reserve does not help a failed predictive
coverage gate pass. Successful validation at t8/s2 does not validate other
temporal shapes or every point in the measured panel.

The pinned source has no project-level software license. This remains an
unresolved redistribution question; neither the article license nor local
verification is a registry publication receipt.
