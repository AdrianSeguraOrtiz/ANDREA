# Autonomous integration workflow

This is the shared procedure for inference tools and simulators. The
[inference playbook](../wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md)
and [simulator playbook](../wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md)
define their respective runtime contracts and field evidence rules. Follow the
whole procedure after one request; phase boundaries are internal quality gates.
This document is the authority for integration procedure and scope. User
instructions take precedence; current executable schemas define actual file
formats. Keep playbooks and scaffold templates aligned with these rules. Do not change
a schema or runtime to make an obsolete example valid.
Within the authorized scope, ask about material scientific ambiguity,
inaccessible requirements or a decision that primary evidence cannot resolve.
Shared behavior changes require the consultation described below.
Record a precise pending state
when a gate cannot pass. Never invent capabilities, defaults or measurements.

## Scope of an integration

Integrating a method does not authorize changing how ANDREA generally filters
its catalog, validates unrelated datasets, interprets existing inputs or runs
other methods. Keep the established procedure and one stable schema for every
normalized input. Before implementing a change to shared architecture or public
behavior, describe the concrete need and proposed effect and consult the user.
Evidence alone is not approval for that wider change. Continue ordinary wrapper,
fixture and catalog work within the authorized integration without phase prompts.

Inference catalog eligibility uses declared dataset metadata, dimensions,
available input files and the existing parameter/execution rules. Validate scientific file
contents for a requested method before fitting; do not use them to infer hidden
catalog categories. Method-specific requirements belong to that method's
ToolSpec, wrapper and requested-run checks. Do not change another wrapper's
scientific input semantics to accommodate a new integration.

### Stable normalized inputs and GUI responsibility

Before adding an input type, inventory the existing catalog and record its
semantic match or precise information gap in `integration_decisions.md`.
Different upstream filenames, languages or table layouts are not new semantics.
Propose a reusable normalized type only for a real gap and obtain approval for
the shared contract change; do not create a tool-specific duplicate.

Each normalized input has one stable meaning and InputSpec. Dataset selectors
must not switch its required columns or create hidden schema profiles. Scientific
metadata belongs in the files. GUI fields are justified by existing interface
or compatibility decisions, not by duplicating metadata already supplied in an
input or derivable from other selectors. Selectors may contextualize help; they
must not reinterpret file contents to classify tools or reread files merely to
activate another input contract. Existing format, dimension and identifier checks
remain valid; method-specific scientific checks belong to the requested run.

For observed bulk data, use the current `expression.tsv` and
`column_descriptors.tsv` contract. Compatible temporal and stationary observations
share the matrix; the descriptor records their roles, series, identities,
sampling design, observed times and units. Do not restore separate timepoint,
replicate or stationary-expression inputs or an extra temporal form/declaration.
`accepts` describes expression column kinds, such as `timepoints`;
`column_descriptors` is an input key declared through `extra_inputs`.

An approved normalized-format change is incomplete until all affected writers
and consumers agree: inference wrappers, simulators, manifests/specs, fixtures,
examples, CLI/GUI handling and cost generators. Verify a generated dataset
through `infer-network` before declaring the handoff complete. Emit canonical
`network.csv` at the established output path and retain native auxiliary outputs
as declared artifacts; do not invent shared output types for informative
tool-specific artifacts without a semantic need.

## Minimal user input

Supply exactly these evidence files and one short text instruction:

| Kind | Primary article | Local upstream clone |
| --- | --- | --- |
| Inference | `wrappers/inference_tools/tools/<id>/papers/article.txt` | `wrappers/inference_tools/tools/<id>/repo/` including its `.git/` metadata |
| Simulation | `wrappers/simulation_data_tools/simulators/<id>/papers/article.txt` | `wrappers/simulation_data_tools/simulators/<id>/repo/` including its `.git/` metadata |

The instruction gives the installation preference, for example “prefer the
official R package”. No prewritten spec, parameter file, fixture, cost file or
phase prompt is required. Other TXT filenames and multiple TXT articles are
accepted. A PDF is optional when the TXT supports methodological review. Record
title, authors, year, canonical DOI when identifiable, text hash, missing figures,
supplements, formulas and extraction limitations. Seek missing evidence only if
it changes a scientific decision. Do not fabricate bibliographic fields.

The clone is read-only evidence, never a runtime dependency. Record remote URL,
commit, dirty state and relevant file/line locations. Inspect the exact public
version selected for installation if it differs from that clone. Verify the
user's installation preference against the official distribution, availability,
licence, runtime and method version. Pin the public version/commit, dependency
versions and base image digest; preserve dependency checksums, build receipts
and necessary upstream patches with their scientific effect. Never copy `repo/`
or `papers/` into an image. These caches and editorial materials stay untracked.

## 1. Establish evidence and the scientific contract

Create the scaffold without overwriting evidence or existing work:

Use the existing Osaka worktree and current branch. Do not create a separate
branch or worktree per method unless the user explicitly requests one.

```sh
make scaffold-tool TOOL=<id> WRAPPER=python
# Or:
make scaffold-simulator SIMULATOR=<id> WRAPPER=python
```

A scaffold leaves the same essentials as the existing wrappers: `Dockerfile`,
`run_tool.*` or `run_simulator.*`, and `integration_decisions.md`, plus the user's
local `papers/` and `repo/`. Keep dependency pins/checksums in the Dockerfile and
necessary compatibility patches in the wrapper. Do not generate a separate
lock file, per-tool ignore file, duplicate README, `integration.json` or a
per-method directory under `wrappers/integration/`.

Unverified ToolSpec/SimulatorSpec and smoke candidates are staged outside the
wrapper and active catalogs, by default in `release-logs/inference-<id>/` or
`release-logs/simulation-<id>/`. Pass `--results-dir` to use another untracked
campaign directory. Scaffolding does not activate a placeholder contract.
The generated decision log uses the
[shared scientific template](../wrappers/integration/decisions.template.md).
Record rationale and parameter traceability there; keep execution logs and
machine-generated reports in the campaign results, without duplicating a
versioned dossier.

After reviewing the candidates, use `--activate-catalog` and
`--activate-smoketest`. Promotion validates all selected candidates before
writing active files. It removes only drafts whose bytes match their promoted
copies, and an inference candidate template map only when its selected entry
and bundle definitions match the central map. A conflicting or uncopied draft
is retained. New integrations use staged drafts only, outside the wrapper.
Do not introduce legacy draft paths, aliases, readers or fallback formats for
unpublished work. Existing generic legacy helpers do not authorize using or
expanding those paths in a new integration.
Ordinary scaffolding after promotion does not recreate placeholder drafts.
The active catalog, canonical smoke configuration, central inference template
map and cost recipe remain the single current sources.

Keep the current version tag while correcting an unpublished integration.
Increment it after stable publication when behavior or dependencies change;
never replace a published historical image. Always record the immutable image
ID actually tested and measured.

Unpublished contracts are changed directly, without version bumps, parallel
legacy APIs or migrations invented for development iterations. A stable tag
does not make old timings valid: a changed immutable image or measured input
contract still needs an applicability decision and affected recalibration.

Audit all public functions, CLIs, pipelines, modes and preprocessing/export
paths relevant to the method. Classify the implementation actually installed,
including model family, score meaning and deviations from the article. For each
entrypoint record inputs, outputs, exposure/exclusion, rationale, evidence and
ANDREA mapping. A callable matrix interface alone does not prove biological
compatibility. Distinguish native contexts, core emulation and derived truth.

Before implementation, complete the parameter traceability matrix:

| Upstream | ANDREA | Type/domain | Default/rule | Dependencies | Actual argument | Exposed/excluded/resource and reason | Test | Cost relevance | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

Include nested public options, conditional options, seeds, presets and omitted
options. Expose the entire serializable surface compatible with ANDREA; justify
exclusions individually. Preserve upstream defaults. A dynamic default needs its
exact upstream rule, sentinel/omission behavior, resolved value in receipts and
a regression test; `null` must not become an arbitrary constant. Separate CPU,
RAM, workers and native thread controls from scientific parameters. Inventory
unsupported function hooks explicitly and use documented presets when feasible.
Audit inventory completeness against source signatures/config/help; matching the
catalog's current params alone cannot establish upstream completeness.
Do not arbitrarily narrow a native parameter domain because one example uses
a convenient value. Check boundary cases, conditional backends, native automatic
searches and data-dependent defaults. Optional diagnostics must use the same
resolved model settings as fitting, rather than silently reverting to helper
defaults. Unsupported paths need individual scientific or implementation reasons.

Record evidence for each nontrivial spec claim: inputs and conditional delivery,
capabilities, parameters, assumptions, compatibility restrictions, outputs,
progress, resources, artifacts and methodological grouping. Reuse normalized
inputs only when information and semantics match. For bulk observed time read
[the temporal input contract](bulk-time-series-contract.md)
before choosing an input shape. An unresolved contract loss blocks the affected
claim; preserve the information and propose an extension through
core, CLI and GUI before activating that capability.

## 2. Implement and prove functional behavior

Implement the wrapper, pinned public installation and image, then update the
spec from verified behavior. Activate the draft only after removing placeholders
and aligning inputs, outputs and tests. The detailed playbooks define the
physical container contract. Preserve original public identifiers, score meaning,
direction, sign and contexts, and retain native outputs, resolved config, logs,
session information and patch provenance. Let ANDREA core own orchestration,
normalization and bundles. A successful container exit is insufficient.

Design a finite functional matrix covering every physical mode/capability,
conditional input branch, optional input that changes behavior, public output
and representative parameter groups. Include defaults and distinguish reduced
smoke settings. Test missing/invalid metadata, conditionally required inputs and
unsupported combinations, plus method-specific scientific invariants. Check
identifier round trips, finite positive score magnitudes, sign/direction,
context membership, dimensions and semantic input/output consistency. Validate
simulator dataset/truth manifests and the inference handoff, including native
versus derived truth. Test core-managed emulation/aggregation through core.

Check observable progress during execution and failure, not just the existence
of a final `progress.json`. Match the spec to actual granularity: phase updates
with `progress.kind=none` are valid when no native work counter is observable.
Do not fabricate target counters or smooth percentages, or report completion
on failure. Verify resource mappings separately for each backend; an extra CPU
allocation does not imply acceleration in a serial backend.

Express catalog restrictions only through existing metadata and rule syntax.
Data-dependent numerical failures stay in requested-run/wrapper validation;
they do not justify a new shared rule language or content-driven tool filtering.

Run schema/config checks before image builds. Build only the affected image,
retain its immutable ID, then run the physical smoke matrix and core/CLI/GUI
checks. Browser evidence is required for new/changed GUI workflows; API tests
alone do not establish browser behavior. An unchanged GUI path can retain
identified prior evidence with its applicability review. Regenerate catalog
tables/maps with `scripts/render_doc_assets.py --catalogs-only`, and run
`tests/test_catalog_figures.py` to check reviewed grouping.

Test repeated seeds in fresh runs with fixed image, inputs, resolved parameters
and resources, using declared numerical tolerances. Exercise changed seeds when
the stochastic contract warrants it; deterministic methods need no changed-seed
difference. Record failures and environment/hardware limits. The existing
`validate_seed_reproducibility.py` only covers DigNet, PLaNet and scGeneRAI; extend
or write a method-specific check for other methods and simulators. Verify actual
native pools and child workers, not just environment variables or Docker quota.
The inference thread validator is also image-specific; simulator enforcement
requires its own evidence. Never promise hardware-independent determinism.

## 3. Plan calibration before measuring

Use the filename `cost.json`. Follow the kind-specific
[inference](../wrappers/inference_tools/cost_profiles/README.md) or
[simulation](../wrappers/simulation_data_tools/cost_profiles/README.md)
generator documentation. Existing validators have different coverage checks;
invoke the explicit strict options for the kind under review.

A user's stability review or explicit deferral controls whether calibration may
start. Preparing a plan does not revoke a request to defer measurement; do not
restart removed or queued campaigns automatically. Once launching is authorized,
complete the necessary preparation and launch without another phase approval.
If the user requests removing invalidated development evidence, stop only the
affected owned jobs and remove their identified invalid artifacts; preserve
unrelated experiments and costs. Retained history is not a compatibility layer.

Declare a bounded domain and justified panel of profiles per physical mode,
input source/branch and materially expensive scientific option. Include real
defaults separately from reduced smoke presets. Avoid the Cartesian product of
every parameter. Vary free dimensions independently: start with three levels
per axis and crosses that avoid a diagonal. State exceptions for fixed native
models, infeasible settings or one-dimensional contracts. Record requested and
actually exported dimensions separately. Bulk panels must vary genes, observed
timepoints per series, independent series/replicates and dynamic parameters,
with real temporal metadata and no transitions across independent series.

Separate strata for each measured thread/RAM/group allocation; measurement in
one stratum does not establish coverage in another. Reserve independent points
for interpolation and, where meaningful, extrapolation before fitting. State
what typical duration and conservative budget mean for the estimator in use.
Set accuracy thresholds and budget-coverage targets before seeing holdout results.
Use the estimator's actual outputs. `infer-network` currently exposes one
scheduling ETA, not an independent typical-time predictor. If that ETA is tested
against both a median-error and planning-coverage criterion, state that explicitly;
do not invent a second predictor, tail guarantee or universal precision claim.
Document estimator limits as well as measured shapes, parameter values, TF policy
and resource strata. Comparable measurement coverage does not imply accuracy
for every execution or permission to extend the common estimator.

Generate and save the resolved `--plan-only` output, run count (including at
least three successful repetitions per new point), resource limits, concurrency,
timeouts, expected duration and conservative upper budget. Include holdout runs,
startup/validation overhead and retry allowance; an unknown ETA is explicitly
unknown, while the timeout sum is only an execution cap. A plan is not a receipt
of measurements. Inspect active experiments before launching it.

Keep the panel in the declarative cost recipe, independent of the machine.
The common cost coordinator reads `calibration_design.resources` and
`repetitions`; its executor discovers effective CPU/RAM and admits unchanged
requests through shared atomic reservations. Automatic concurrency and optional
operational caps belong to that executor. They must not remove panel points or
change ordinary `infer-network` scheduling. Within each profile, independent
fits may run concurrently with exact Docker CPU sets/quotas/RAM limits and a
single result writer. Busy or impossible capacity remains pending, never an
invented measurement. Reserve headroom and verify container exit before
releasing a lease; uncertain/orphaned ownership blocks admission. Do not infer
timeouts for unattempted larger inputs. See
[`cost_profiles/README.md`](../wrappers/inference_tools/cost_profiles/README.md)
for the common executor contract and its limits of isolation.

The configured per-run timeout is part of the user's measurement budget. Do
not increase it, replace it with a different launcher default or retry until
success without the user's agreement. Retain budget-limited `timeout` outcomes
separately from successful timings and from work never attempted. Reaching the
budget does not itself invalidate the observation. Any requirement for three
successful timings must be distinguished from recording that a planned point
was attempted within budget; consult the user when those criteria conflict.
Complete attempts and sufficient successful timing coverage are separate
validation gates. Failed/partial observations retain their actual outcomes;
timeout caps never become successful elapsed times.

Probe expensive or data-dependent branches with small valid generated inputs
before a long queue. Budget the full calibration and validation workloads and
storage for matrices, models, native auxiliaries and logs. Set a free-space
reserve and explicit capacity/failure states. Sharing byte-identical retained
files may save storage, but paths, hashes and distinct execution receipts must
remain intact; copies or hard links never count as additional repetitions.

Include representative scaling checks before committing that budget: measure
fitting, aggregation and export separately at growing gene/window counts, not
only the smallest smoke fixture. Compare equivalent native/wrapper outputs and
check whether additional threads help. Detailed hooks on individual trees or
other inner-loop tasks belong to explicit diagnostic runs; keep ordinary
inference and cost measurements free of that tracing overhead. A bounded pilot
does not replace the declared panel or independent estimator validation.

When native code or its performance changes, keep any retained old observations
bound to their original immutable image, and remove obsolete model copies when
the user requests cleanup. Withdraw the old active cost until the corrected
image's full agreed panel has been attempted, even when its unpublished version
tag stays unchanged. Do not import old wall times or synthesize new timings by
combining old fitting time with new aggregation time. An executor-only kernel
review cannot bypass native image/build-context identity. Do not substitute a
reduced calibration or publish a partial cost model without the user's explicit
agreement. When full calibration is deferred, leave the tool without `cost.json`;
diagnostic pilots do not change that state. A full campaign may keep resumable
working checkpoints outside the catalog without presenting them as its final
cost model. Genuine timeouts count as attempted outcomes under the agreed cap.

A common cost-executor migration also requires strict validation of every
active cost model in the selected scope. When a verified executor review
preserves the measurement protocol, use the existing shared driver's
`--reuse-only` path to reconcile derived profile fingerprints. Verify unchanged
observation IDs, timings, inputs, original provenance, aggregate values and raw
receipts, then rerun provenance, attempt and coverage validation. Do not leave
other selected tools with stale fingerprints after migrating one campaign, or
relax the validator to accept an unverified identity change.

For a long campaign use a dedicated PM2 process, state and log directory and
`--no-autorestart`, for example:

```sh
pm2 start /absolute/campaign-command.sh --name andrea-cost-<id> \
  --interpreter bash --no-autorestart --output /absolute/campaign/stdout.log \
  --error /absolute/campaign/stderr.log
```

The command file must invoke the pinned worktree virtualenv, persist the resolved
plan hash and statuses (`planned/running/completed/failed/interrupted`), trap
interruptions and resume only compatible verified receipts. Use existing
inference coverage/campaign resume mechanisms and simulator
`extend_cost_profiles.py`; do not silently start a fresh overwrite. PM2 alone
does not implement scientific resume. Do not disturb other experiments or images.

Record worker identity and recover only verified owned process groups and
containers after interruption; a reused PID is not authorization to signal a
process. Do not clear another experiment's PM2 entries or use automatic restart
as a substitute for verified scientific resume.

While costs run, inspect the actual source/image seal before editing or starting
another integration. A seal can include Markdown playbooks and templates as well
as code. Preserve those bytes in an ordinary immutable source snapshot under
ignored release evidence before changing the live tree. Keep a private Git
index and explicit execution environment when needed to reproduce the original
source identity; this does not require another branch or registered worktree.
Verify source, image, build context, input bytes and hardware before reuse.
Run the measured driver and estimator from that snapshot, so later catalog
activation and documentation work do not change their identities. Record any
new post-measurement analyzer separately. Never relabel historical observations
with the new tree's source hash or rewrite the original seal.

Source inventories exclude generated `cost.json` and its atomic writer's
`.cost.json.<random>` files in catalog directories. Genuine new or modified
sources remain detectable. Queue measurement jobs with their resource budgets;
use dependencies within each tool, so another tool's failed calibration does
not suppress already available independent validation or catalog activation.

## 4. Measure, validate domain and test independent predictions

Retain individual times, successful repeats, all failures/timeouts, immutable
image/build identity, fully resolved params, fixture recipes and input hashes,
seeds, resources, hardware, software and source provenance. Reuse observations
only after exact compatibility checks. Preserve unaffected profiles and failed
observations; a point with fewer than three successes remains incomplete even when some
repetitions succeed. Failure evidence is retained independently of success counts.
Build/source changes require a recorded applicability decision and recalibration
of affected evidence, not blanket deletion or automatic reuse.

Evaluate three separate gates:

1. **Structural/semantic validity:** schema, identities, resolved parameters,
   actual run timings, repeated successes, input/build provenance and receipts.
2. **Declared domain coverage:** planned configurations, branches, dimensions
   and resource strata have satisfactory observations; fixed-axis exceptions
   and excluded configurations are explicit. A valid JSON or one size is insufficient.
3. **Independent predictive accuracy:** frozen estimator/cost snapshot and
   disjoint validation executions meet the predeclared error and budget criteria
   per stratum. A full grid does not establish extrapolation accuracy.

Use `wrappers/validate_cost_predictions.py` with its predeclared plan,
observations and report (format in its `--help` and the cost READMEs). Preserve
the plan seal, training inventory, held-out receipts and estimator snapshot.
Hashes and disjoint IDs support auditing; they cannot prove honest chronology
or experimental independence without provenance review. If holdout points are
subsequently fitted, retire that validation claim and reserve new independent
points. Report interpolation and extrapolation separately. If accuracy fails,
limit the declared domain, revise the model and use new holdouts, or leave the
calibration pending. Never relabel failed predictions as independent success.

## 5. Acceptance, image publication and handoff

Use the existing schema, runtime and calibration checks, retaining their real
commands, exit codes, reports, immutable image IDs, source/configuration hashes,
input/output hashes and limitations in `release-logs/` or `--results-dir`.
Scientific findings and justified exclusions belong in the existing decision
log. Do not create a new per-method dossier or a second set of current specs,
smoke configurations, template mappings or cost plans.
`wrappers/integration/integration.template.json` is an optional helper example,
not a required wrapper deliverable. If used, keep receipts in ignored campaign
storage. The Dockerfile's dependency pins/checksums satisfy the installation
record; a validator mentioning a dependency lock does not require a new lock file.

Functional acceptance requires the physical variants and core/CLI/GUI checks
from section 2. Calibration additionally requires all three independent gates:
structural/provenance validity, measured-domain coverage, and the frozen
independent prediction checks. Run the relevant cost validator with
`--require-provenance --require-coverage`, and use
`wrappers/validate_cost_predictions.py` on the retained prediction plan and
observations. Passing schema validation or a helper exit alone is insufficient.
Never replace actual failures or pending measurements with a prose success.

Retain historical logs and failed attempts unchanged. Before removing a staged
copy, check that its active counterpart is identical; never delete a changed or
uncopied candidate. Reorganizing sources changes their fingerprints, so a frozen
campaign must finish or be deliberately stopped before its sources are changed.
Keep raw paper/clone/runs out of Git; commit runtime code, canonical
configuration, tests and the scientific decision log.

Report three distinct deliverables: **functional integration**, **calibration
complete within the measured domain with independently observed predictive
limits**, and **image publication**. Publication requires explicit task
authorization and a registry digest tied to the measured immutable image;
local tests do not establish publication. Do not rebuild a measured image merely
to push it. Package release/PyPI and merging to main follow
[release.md](release.md) only when requested.

The final handoff lists changed files, scientific exclusions, parameter matrix,
commands/results, costs/domain/accuracy, image identity and publication state,
retained evidence, pending gates and limitations. Continue through every
authorized feasible gate without requesting approval per phase.

## Reusable prompts

**Inference:** “Integra `<id>` siguiendo
`wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md` y el flujo compartido
completo. Aporto `papers/article.txt`, el clon en `repo/` y prefiero instalar
mediante `<preferencia>`. Audita, implementa, verifica y calibra con evidencia;
avanza sin prompts por fase y declara los estados pendientes. Publicación de
imagen: `<no / sí, destino>`.”

**Simulation:** “Integra `<id>` siguiendo
`wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md` y el flujo
compartido completo. Aporto `papers/article.txt`, el clon en `repo/` y prefiero
instalar mediante `<preferencia>`. Audita contrato, capacidades y parámetros;
implementa, verifica y calibra con evidencia sin prompts por fase. Publicación
de imagen: `<no / sí, destino>`.”
