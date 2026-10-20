# Osaka integration-procedure audit

Baseline: ANDREA `v0.2.1`, commit
`75424b6cad5748ba3f818e133412d21388700e52`. Work is isolated in
`osaka/bulk-time-series`, worktree `/home/adrian/Salonica/ANDREA-Osaka`, with its
own `.venv`. The original checkout remains on `main` at the baseline, with no
working-file changes. No new method or simulator is integrated by this change.

## Findings against executable behavior

| Area | Already present in 0.2.1 | Gap and resulting change |
| --- | --- | --- |
| Scientific contract | Explicit inference `method_family`, execution capabilities, conditional input delivery, sign/score/context rules, simulator native/derived truth and semantic axes. Current scMTNI, TIGRESS, GeneNetWeaver and dyngen decisions provide concrete audited examples. | Guides required manual phase prompts and reviews; common evidence-to-catalog workflow now executes all authorized gates from one request. Field rules remain in the specific playbooks. |
| Evidence | Ignored local `repo/` and `papers/` caches; wrappers install separately. | Guides assumed PDF. Paper preparation resolved `pdftotext` even for TXT-only evidence. TXT is accepted directly; extraction is lazy and atomic, rejects empty text and preserves previous text on failure. Bibliography and evidence limitations are required. |
| Scaffolds | Simulator drafts avoided exposing incomplete implementations. | Inference scaffold activated placeholder specs/smoke configs. Both now start inactive, share one scientific decision template and acceptance dossier, and exclude evidence from build contexts. Unknown bibliography remains pending rather than invented. Simulator Python entrypoint and overridden extension are corrected. |
| Parameters and defaults | Schema validation, parameter bindings, overrides and dynamic-default guidance existed. | No common complete traceability/acceptance record. The shared template requires upstream/ANDREA names, domains, defaults/rules, dependencies, real call arguments, exclusions/resources, tests and cost relevance. Coverage of top-level public params/defaults is checked; completeness against upstream signatures remains a recorded scientific review. |
| Functional and repeatability checks | Physical inference smoke variants, simulator capability/extra matrix, identifier/score checks, core emulation/aggregation tests, image-specific thread/seed regressions. | No unified completion evidence across scientific review, defaults, conditional paths, core/CLI/GUI and maps. The shared gate checks hashes, scientific-context seals, check/coverage receipts and stage-specific artifacts. It cannot infer scientific truth or replace browser/method-specific checks. |
| Cost validity and provenance | `cost_profile_v3`, resolved recipes, input/build hashes, retained individual observations/failures, compatible reuse, interpolation/extrapolation provenance. | Validity and independent accuracy were easy to conflate. New workflow separates structural/semantic validity, declared-domain coverage and held-out prediction quality. No measurements or old cost files were changed. |
| Cost validator asymmetry | Inference already checked planned geometry by profile/resources; simulator validator checked schema/provenance and capability/extra coverage. | Simulator now has opt-in geometry reports/gate. Both geometry gates require independent free axes/resource strata and sufficient successful observations; they do not prove predictive accuracy. |
| Campaign plans and resume | Sequential inference campaign with state/logs/locks and simulator extension runner with retained observations. | Plans lacked sufficient execution/budget detail; inference plan-only could depend on Docker. Offline planning now reports resolved jobs, counts, resources and timeout budgets before measurement. Simulator complete-profile reuse no longer bypasses receipt/output verification. PM2 use requires no automatic restart. |
| Independent predictions | Planners exposed typical/conservative estimates and extrapolation provenance. | No shared held-out evidence checker. `wrappers/validate_cost_predictions.py` verifies frozen-plan references, estimator/cost snapshots, receipt and training disjointness, inputs, resource strata, individual repeats and predeclared error/budget thresholds. Chronology and experimental independence still require provenance review. |
| Observed bulk time | `timepoints`, `replicates`, `column_descriptors`, separate pseudotime and simulation time-series axes exist. | Units, longitudinal identity, series boundaries and lag-preserving outputs are incomplete. The [temporal audit](bulk-time-series-contract.md) gives exact code paths, a compatible proposal, meaningful bulk fixtures and an offline checker. No new runtime support is claimed. |

The temporal audit also contrasts a concrete GeneNetWeaver discrepancy: its spec
describes a `series_id` timepoint column while its writer emits only `column`,
`timepoint`, `timepoint_label`. It identifies blank temporal values accepted by
the shared coercion, numeric/finiteness checks absent from simulator expression
validation, discarded extra network-lag fields at merge, and generic benchmark
labels that do not encode coherent observed bulk time courses. These are
explicit adoption requirements, rather than silently corrected wrapper semantics
or retroactively certified capabilities.

## Calibration domain findings

The 32 historical `cost.json` files pass structural/provenance validation.
Inference's existing strict coverage check passes all 24 tools. The new
simulator geometry gate reports these adequate/total profile counts:

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

Every simulator has at least one pending profile under the stronger geometry
gate. Many legacy capability/default profiles measure a single size while a
separate representative profile carries a broader grid; that does not establish
independent coverage for each configuration/resource stratum. Native fixed axes
are respected only when supported by resolved recipe evidence. SERGIO's custom
gene universe is fixed in the implementation, while a legacy recipe maps a
free `number_genes` argument; this ambiguous metadata remains pending. A fixed
choice of 12 genes in GeneSPIDER2's scalable network is not a native fixed axis.

These results are conservative domain diagnostics. They do not invalidate the
historical measurements, establish predictive accuracy, or require replacing
images during the running experiment. Review and justify finite domain
exceptions or measure missing points later. None of the 32 files receives a new
independent accuracy claim from this audit.

## Verification and retained evidence

Lightweight checks use the worktree virtualenv, one native thread, Docker test
opt-in disabled and reduced CPU priority. Exact commands and results are kept
under ignored `release-logs/osaka/`; this document records their scope.

- Final targeted regression suite: **300 passed, 2 skipped in 10.00 seconds**.
  The two omitted tests require actual Docker execution.
- Both catalog validation targets and `validate-release-costs` pass: 24 tools,
  eight simulators, 22 inference input specs, 15 simulation input specs, 24
  inference smoke configs and 47 simulator smoke configs.
- Both cost coverage report commands were executed without containers.
  Inference passes; simulator domain deficits are the table above.
- Existing `scripts/render_doc_assets.py --catalogs-only` regenerates 24
  inference entries and 31 executable simulator capabilities. Grouping tests
  use reviewed `method_family`; generated tracked maps/tables have no diff.
- New tests exercise TXT-only preparation with no extractor, atomic extraction
  failure, scaffold draft preservation/activation, defaults and stale receipts,
  independent cost-prediction contamination/resource checks, offline plans,
  safe reuse and temporal identity/series/missing-value checks. All timing data
  in these tests are explicitly synthetic test fixtures.
- Both regular longitudinal and irregular independent bulk fixtures pass the
  offline temporal checker and explicitly return `runtime_support=false`.
- Offline campaign plan probes resolve 414 allocated runs for the existing
  GENIE3 campaign and three for one existing GeneNetWeaver profile, with explicit
  timeout budgets. These are static execution allocations, not completed runs
  or predicted durations; no measurement was launched.

The experiment state was read only. While
`andrea-replication-0.2.1` remained active, no image builds, pulls, replacement,
scientific container smoke runs, calibration or experimental tests were
launched, and no experiment files/processes were modified. PyPI publication,
image publication and merging to main are outside this task.

## Acceptance and pending states

The procedure and its lightweight automation are prepared and tested. The
[shared workflow](INTEGRATION_WORKFLOW.md) contains the exact two user-supplied
paths plus installation preference, two short reusable prompts and acceptance
receipt formats. A generated empty scaffold correctly fails the functional gate.

Future integrations report functional and calibrated-domain gates separately
from image publication; the publication gate applies when requested.
No concrete integration is complete by association with
this work. Runtime temporal metadata/lag adoption, new image functional/browser
checks, new calibration points and independently measured prediction accuracy
remain unperformed. They are required only for future claims that depend on
them, under an available resource budget. The temporal proposal must be adopted
through schemas, core, CLI, GUI and bundles before a wrapper requires it.
