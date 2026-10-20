# Autonomous integration workflow

This is the shared procedure for inference tools and simulators. The
[inference playbook](../wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md)
and [simulator playbook](../wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md)
define their respective runtime contracts and field evidence rules. Follow the
whole procedure after one request; phase boundaries are internal quality gates.
Ask only about material scientific ambiguity, inaccessible requirements or a
decision that primary evidence cannot resolve. Record a precise pending state
when a gate cannot pass. Never invent capabilities, defaults or measurements.

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
versions and base image digest; preserve lock/checksum/build receipts and
necessary upstream patches with their scientific effect. Never copy `repo/`
or `papers/` into an image. These caches and editorial materials stay untracked.

## 1. Establish evidence and the scientific contract

Create the scaffold without overwriting evidence or existing work:

```sh
make scaffold-tool TOOL=<id> WRAPPER=python
# Or:
make scaffold-simulator SIMULATOR=<id> WRAPPER=python
```

Scaffolds are drafts, not executable catalog entries. Inspect existing wrappers
before changing their contract. The generated `integration_decisions.md` uses
the [shared evidence template](../wrappers/integration/decisions.template.md).
The generated `integration.json` uses the
[acceptance template](../wrappers/integration/integration.template.json).
The integrating agent fills both from evidence and test receipts; the user
does not supply them. Keep prose for rationale and the JSON for checkable
file identities and receipts. Avoid repeating claims in additional checklists.

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

Record evidence for each nontrivial spec claim: inputs and conditional delivery,
capabilities, parameters, assumptions, compatibility restrictions, outputs,
progress, resources, artifacts and methodological grouping. Reuse normalized
inputs only when information and semantics match. For bulk observed time read
[the temporal contract audit and extension proposal](bulk-time-series-contract.md)
before choosing an input shape. An unresolved contract loss blocks the affected
claim; preserve the information and implement a compatible extension through
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

Generate and save the resolved `--plan-only` output, run count (including at
least three successful repetitions per new point), resource limits, concurrency,
timeouts, expected duration and conservative upper budget. Include holdout runs,
startup/validation overhead and retry allowance; an unknown ETA is explicitly
unknown, while the timeout sum is only an execution cap. A plan is not a receipt
of measurements. Inspect active experiments before launching it.

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

Run the shared evidence gate:

```sh
python wrappers/integration/validate_integration.py <wrapper>/integration.json \
  --stage functional --report release-logs/<id>-functional.json
# After calibration and independent prediction checks:
python wrappers/integration/validate_integration.py <wrapper>/integration.json \
  --stage calibrated --report release-logs/<id>-calibrated.json
```

Fill `artifacts` with `{role,path,sha256}` for the `spec`, `wrapper`,
`dockerfile` and other build inputs. The calibrated gate additionally requires
`cost` (a file named `cost.json`), `calibration_plan`, `prediction_plan`,
`prediction_report` and `estimator` artifacts. Evidence rows add `id`, `role` (`paper` or
`upstream`), `citation` (bibliography or source file/line) and `limitations`.
`installation` records preference, public HTTPS source, verified pin/lock,
immutable `image`, separate optional `registry_digest` and evidence IDs. Entrypoint and parameter rows use the
matrix fields described above; each exposed top-level ANDREA param must match
its spec default. Each parameter JSON row uses `upstream`, `andrea`, `type`, `domain`,
`upstream_default`, `default` (the ANDREA spec value), `default_rule`,
`dependencies`, `argument`, `disposition` (`exposed`, `excluded` or `resource`),
`reason`, `test`, `cost` and `evidence` IDs. The rule describes literal forwarding
or exact dynamic-default/sentinel/argument-omission behavior. Nested leaves are
audited in the prose matrix and referenced tests. Excluded/resource rows retain their rationale and upstream evidence.

Checks are named in `validate_integration.py:FUNCTIONAL`; `functional_modes.covers`
lists included entrypoint names and `parameter_defaults.covers` lists public
top-level ANDREA params. Retained reports must explain their actual test cases.
Calibration adds
`cost_structure`, `cost_domain`, `cost_predictions`. Each check has
`{name,status,receipt:{path,sha256}}`. Receipt JSON has `kind`, `id`, `status`,
`exit_code`, `command`, immutable `image`, `check`, `covers`, `context_sha256`,
`subjects` (artifact role to SHA-256), and `outputs` (retained report/log refs `{path,sha256}`, relative to the dossier).
Run the gate with `--context-only` to obtain canonical context and subject
hashes for receipts; this computes hashes without certifying a check. Scientific
evidence, installation and entrypoint/parameter claims are sealed by that context.
Functional receipts bind functional build subjects; cost receipts also bind the
calibration artifacts, so changing costs invalidates calibration evidence without
invalidating unchanged functional tests. The check name and covered items must
match the hashed receipt. A registry receipt also records `registry_digest`.
Only `conditional_inputs` or `repeatability` can be `not_applicable`, with
reason and evidence IDs. A receipt may represent a documented scientific review;
it must retain the reviewed findings, rather than an invented execution result.
Changing an artifact invalidates its receipts until applicability is reviewed
and checks rerun or honestly reissued with identified prior evidence. The gate
checks evidence integrity and required gates, not scientific truth or complete
upstream API discovery. Keep raw paper/clone/runs out of git; commit the compact
dossier, decisions and portable references, recording unavailable local evidence
as pending on another machine.

Report three distinct deliverables: **functional integration**, **calibration
complete within the declared domain with observed predictive limits**, and
**image publication**. Publishing requires explicit task authorization; it is
not implied by local integration. A published state additionally requires
`--stage published`, `installation.registry_digest` (`repository@sha256:…`)
and a `registry_image` receipt verifying that exact digest and its relationship
to the measured `image`. Publication depends on functional checks; it does not
imply that the independent `calibrated` gate has passed. Do not rebuild a measured image merely to push it. Package
release/PyPI and merging to main follow [release.md](release.md) only when requested.

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
