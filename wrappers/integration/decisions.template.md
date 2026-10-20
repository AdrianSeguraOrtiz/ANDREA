# {{id}} integration decisions

Status: pending evidence review and implementation. Scaffolding is not a
functional integration, calibration or image publication.

Follow [the integration workflow](../../../../docs/INTEGRATION_WORKFLOW.md).
Keep executable evidence and verification receipts in `integration.json`; run
`python wrappers/integration/validate_integration.py {{evidence_path}}/integration.json --stage functional`
from the repository root when ready. Continue through all authorized stages;
ask only about material scientific ambiguity, inaccessible requirements or
decisions that evidence cannot resolve.

## Evidence and scientific identity

- Article TXT files under `{{evidence_path}}/papers/`: TODO. TXT is sufficient;
  PDF and `pdftotext` are optional.
- Bibliography (title, full first author, year, DOI/URL) and precise TXT
  sections/line references: TODO. Record incomplete metadata as pending.
- Evidence limitations (missing supplement, OCR quality, inaccessible source): TODO.
- Local upstream evidence: `{{evidence_path}}/repo/`; remote URL, exact commit,
  dirty status, license, reviewed paths/symbols: TODO.
- Paper method versus actual implementation, implementation variant and
  methodological family/keywords: TODO. Do not claim a paper-only feature that
  the selected public implementation does not provide.
- Required upstream patches, before/after behavior, scientific impact and
  regression evidence: TODO.

## Entrypoint and mode audit

Audit every relevant public API, CLI and mode before selecting the integration.
Do not treat one working entrypoint as complete coverage of the method.

| Public entrypoint / mode | Scientific assumptions and restrictions | Required / conditional inputs | Integrated or excluded, and why | Repository / paper evidence | Functional test |
| --- | --- | --- | --- | --- | --- |
| TODO | TODO | TODO | pending | TODO | pending |

## Complete parameter traceability

One row per public upstream parameter, across the audited entrypoints. Expose
all serializable parameters compatible with ANDREA. Justify exclusions such as
function-valued hooks or unsupported scientific modes; convenience is not a
reason to omit a parameter. Preserve upstream defaults and omission semantics.
For data-dependent defaults record the formula/condition and let upstream
resolve it; do not replace it with a fixed small smoke-test value. Record the
resolved value and request/configuration for each execution where possible.
Distinguish an unspecified value, explicit `null`, and zero when upstream does.

| Upstream name / entrypoint | ANDREA name | Type and domain | Upstream default or data-dependent rule | Dependencies / conditions | Actual implementation argument / conversion | Exposed or justified exclusion | Test / receipt | Material cost effect | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TODO | TODO | TODO | pending | TODO | TODO | pending | pending | unknown | TODO |

Resource controls (threads/workers/processes, RAM, GPU) belong to the runtime
contract, separate from scientific parameters. Record actual mappings and
limits, seed propagation, repeatability limitations and native-library thread
controls. Unsupported control must not be advertised as effective.

## Catalog contract evidence

For **every** populated field in `{{contract_name}}`, record chosen value,
source path/line or article section, rationale, ambiguity and test. Draft
values are illustrative and must not be activated as scientific claims.

{{specific_decisions}}

## Observed bulk time series, when applicable

Review the existing `timepoints` and `replicates` input specs before proposing
new contracts. Record observed time, units and ordering; regular/irregular
intervals; independent series, conditions and replicate meaning; exact
metadata-to-expression-column correspondence; missing values, transformations,
normalization, lags and interpretation of resulting network contexts/scores.
Do not substitute pseudotime for observed time or invent transitions between
independent series. If a property cannot be represented without information
loss, record the gap and a compatible contract extension before implementation.
Fixtures and cost panels must preserve the actual series structure, including
genes, timepoints per series, series/replicates and dynamic parameters.

## Installation preference and reproducible public source

- User installation preference: TODO; treat it as a hypothesis to verify.
- Verified official/public route, immutable version/commit, archive/package
  checksum and dependency/runtime pins: pending.
- Command or Dockerfile location and installation verification receipt: pending.
- Reason for any deviation from the preference and fallback source: TODO.
- Image identity/digest after build: pending. Registry publication: pending.

The local `repo/` is analysis evidence. The image must install from the pinned
public source and run without the clone or article. Exclude `repo/` and
`papers/` from Docker build contexts; never mount them at runtime.

## Functional evidence and catalog availability

Record actual commands, fixtures, expected scientific properties, results and
artifact paths/hashes. Cover modes, conditional inputs, parameter forwarding
and omission, invalid requests, seeds/repeated runs, effective resources,
identifiers, score meaning, direction/sign/context, empty networks and progress
where applicable. Exit code alone does not verify scientific output.

- Functional and repeatability status / receipts: pending.
- Core, CLI, GUI availability and documentation / regenerated grouped catalog
  maps: pending.

## Cost plan and completion evidence

- Profile panel and declared physical execution modes, inputs, cost-sensitive
  parameters and real defaults: pending. Distinguish smoke-test overrides.
- Independent free axes and crossed points: start from three levels per axis;
  justify exceptions and native/fixed dimensions. Record requested versus
  exported dimensions and series structure.
- Resource strata, minimum three successful repeats per new point, planned
  execution count, resources and time budget before measurement: pending.
- Individual times/failures, resolved params, input hashes, immutable images,
  resource/provenance receipts and compatible-only resume: pending.
- Independent interpolation/extrapolation holdouts (excluded from fitting),
  errors and typical versus conservative duration estimates: pending.
- `cost.json` structural/semantic validity: pending.
- Coverage of the declared calibration domain: pending.
- Predictive accuracy on independent executions: pending.

Use the existing cost campaign tooling. A valid JSON, one size or a diagonal
grid does not complete calibration. A complete panel does not establish reliable
extrapolation. Preserve unaffected profiles and failed observations. Long
authorized campaigns use PM2 with explicit state/logs, safe resume and no
automatic restart on completion. Defer measurement during resource conflicts.

## Remaining decisions and independently reported states

- Scientific ambiguities / inaccessible requirements / justified exclusions: TODO.
- Functional integration: pending.
- Calibration complete within declared domain: pending.
- Independent predictive validation: pending.
- Image publication: pending.

Do not convert pending work into a passed check or require approval at each
internal quality gate. Report limits and resolve evidence-backed choices
autonomously within the user's authorized scope.
