# Bulk time-series contract audit and compatible extension proposal

The historical audit below targets ANDREA 0.2.1
(`75424b6cad5748ba3f818e133412d21388700e52`). The Osaka branch now adopts the
optional `bulk-temporal/1` runtime object described under runtime adoption.
Legacy manifests without that object retain their behavior. The companion
`bulk-temporal-audit/1` checker and fixtures remain an offline quality check;
they do not establish simulator capabilities or measured cost coverage.

## Historical base audit (0.2.1)

| Contract or path | Implemented behavior | Remaining limitation |
| --- | --- | --- |
| [Expression input spec](../andrea/catalog_inference_tools/input_specs/expression_matrix.json) and [shared TSV validator](../andrea/core/shared/input_validation.py) | Gene rows, observation columns; numeric finite expression data when this spec is applied; unique nonempty gene identifiers. | No normalization/transformation provenance, temporal axis, or missing-visit semantics. The shared TSV validator alone does not reject duplicate observation headers; callers or the offline checker must check them. |
| [Timepoints spec](../andrea/catalog_inference_tools/input_specs/timepoints.json) | Unique expression-column IDs, required `timepoint` header, finite numeric values when nonempty. Subset membership plus row-count equality gives an exact set mapping when validation succeeds. | Blank `timepoint` cells currently pass the optional named-cell coercion. No units, series boundaries, sampling design, interval constraints, or required condition labels. No metadata row-order requirement. |
| [Replicates spec](../andrea/catalog_inference_tools/input_specs/replicates.json) | Unique expression-column IDs; required `replicate` header; optional `batch` and `condition` strings; exact set mapping via the same checks. | Empty labels are not rejected by named string coercion. A label does not say whether the unit is biological, technical, longitudinal, or independently sampled. No relationship with the timepoint table is checked. |
| [Pseudotime spec](../andrea/catalog_inference_tools/input_specs/pseudotime.json) | Separate `pseudotime` artifact and numeric field. | Numeric pseudotime is not evidence of observed time, a physical time unit, or equal sampling intervals. |
| [Simulation request semantics](../andrea/core/commands/generate_data/semantic.py), `required_extras_for_request` | `experimental_design=time_series` requires `timepoints` (and all simulations require `tf_list`). | Does not require series/replicate labels or units; time series may mean cells, samples, or timepoints. `replicates` in a simulation plan counts independent wrapper executions/seeds, not biological replicates within an expression matrix. |
| [Simulator output validator](../andrea/core/commands/generate_data/output_validation.py) | `_validate_expression` checks IDs, widths and declared dimensions. `_validate_extras` reuses the canonical inference input specs through [the common loader](../andrea/core/shared/input_specs.py). | `_validate_expression` does not itself inspect numeric expression values or reject NaN/missing data. Extra validation does not add temporal semantics. Inference [dataset validation](../andrea/core/commands/infer_network/commons/dataset.py) applies the expression spec separately. |
| [Generated dataset manifest](../andrea/core/commands/generate_data/pipeline.py), `_dataset_manifest_payload` | Carries recognized standardized extra file references into the inference manifest. | The strict [dataset schema](../andrea/catalog_inference_tools/schemas/dataset-manifest.schema.json) and [simulator output schema](../andrea/catalog_simulation_data_tools/schemas/simulator-output-manifest.schema.json) have no temporal object. A sidecar is not automatically copied into runtime or frozen inference bundles. |
| [Inference input resolution](../andrea/core/commands/infer_network/commons/tools.py) and [runtime preparation](../andrea/core/commands/infer_network/commons/runtime_helpers.py) | Mounts only active standardized extras declared by the ToolSpec. Extra TSV columns survive copying. | Being syntactically accepted does not mean a wrapper consumes `timepoints` or `replicates`, understands the proposed columns, or receives an arbitrary JSON sidecar. |
| [Network normalization and merge](../andrea/core/commands/infer_network/commons/merge.py) | Preserves source, target, positive finite score magnitude, separate sign, evidence and normalized context. Direction/sign capability is declared separately. | `_iter_network_rows` reconstructs the six standard fields, discarding extra CSV fields such as a native edge lag. Context is not a standardized lag or time-window axis. |

These guarantees are shared where the actual code calls the validators. Do not
infer that inference input validation, simulator output validation, and a
smoketest perform identical checks.

## Contrasted integrations and fixtures

[GeneNetWeaver](../wrappers/simulation_data_tools/simulators/genenetweaver/run_simulator.py)
parses blank-line-delimited native time-course blocks in
`parse_time_series_file`, retaining a numeric `series_id` internally.
`read_time_series_expression` encodes the family and series in public column
IDs and builds per-column perturbation metadata. However, `write_timepoints`
actually emits only `column`, `timepoint`, and `timepoint_label`.
The catalog's [timepoint derivation](../andrea/catalog_simulation_data_tools/simulators/genenetweaver/simulatorspec.json)
describes a `series_id` column that this writer does not emit. The required
`timepoints` artifact therefore cannot, by itself, carry machine-readable series
boundaries. Its [bulk temporal smoketest](../wrappers/simulation_data_tools/tests/smoketest_configs/genenetweaver_bulk_time_series_global.json)
also requests `perturbation_design`, which carries condition and replicate
annotations; that optional richer table does not repair the minimal public
timepoints contract for every consumer. A parser must not reverse-engineer
experimental structure from column-name substrings.

[GeneSPIDER2](../wrappers/simulation_data_tools/simulators/genespider2/run_simulator.py)
exports real modeled time coordinates for its single time course, using the
upstream timestep calculation described in its
[integration decisions](../wrappers/simulation_data_tools/simulators/genespider2/integration_decisions.md).
This is useful evidence for that mode, not evidence of support for multiple
independent trajectories, physical units, or arbitrary irregular sampling.
Similarly, dyngen's synchronized single-cell time groups are not bulk
biological replicates. Neither pseudotime nor a concentration/perturbation
series becomes observed bulk time merely by changing an axis label.

The generic inference [benchmark input writer](../wrappers/inference_tools/scripts/shared/benchmark_inputs.py)
currently writes time as `idx % 4` and replica labels as `idx % 3`. Those helpers
are syntax fixtures. They do not encode a controlled number of independent
time courses, biologically paired units, units of elapsed time, or a coherent
bulk sampling design, and must not establish Osaka temporal cost coverage.

The new [bulk fixtures](../tests/fixtures/bulk_time_series/README.md) contain
explicit population mean trajectories, regular and irregular grids, separate
conditions, biological series/samples, and technical measurements. Their origin
and deterministic formulas are documented. They validate contracts; they supply
neither measurements of runtime nor a causal truth network.

## Proposed extension, checked offline

Use the existing standardized extra IDs. Enrich `timepoints.tsv` with:

```text
column  timepoint  time_unit  series_id  condition  observation_id
```

Enrich `replicates.tsv` with:

```text
column  replicate  replicate_kind  technical_replicate
```

These headings are tab-separated in actual files. All fields are required and
nonempty for this proposal. `replicate_kind=biological` identifies the primary
biological unit; technical labels always occupy their own column. Use a label
such as `tech1` for a single measurement rather than an empty value. This
deliberately narrow profile avoids overloading the existing `replicate` field.

- `timepoint` is a finite observed coordinate, relative to an explicitly
  documented origin. Negative pre-treatment times are valid. `time_unit` must
  match the sidecar declaration in every row. Explicit model units may be used
  when an upstream simulator has no physical time calibration; never invent a
  conversion to hours.
- `series_id` identifies an independent longitudinal course or an explicitly
  designed cohort time course; `condition` is constant within that series.
  Different conditions, perturbations, and independent courses retain distinct
  IDs. A changing treatment protocol needs an explicit intervention annotation,
  not an unexplained change of condition within a series.
- In `sampling_design=longitudinal`, one series identifies one biological unit.
  Each observed time has one `observation_id`; multiple expression columns for
  it must have different technical labels. Sorting times produces adjacency
  only inside this series. Technical measurements are not extra timepoints.
- In `sampling_design=independent_samples`, a series groups a cohort design.
  Every biological sample/replicate identifies one observation at one time.
  Samples at different times have no asserted biological pairing. The audit
  reports time groups and no longitudinal transitions. A method may operate on
  explicitly aggregated group means only when its scientific contract supports
  that design and the transformation is documented and tested.
- Metadata joins use the full expression-column identifier, with exact set
  equality and uniqueness. Row order may differ from expression order. Wrappers
  must join by ID and then construct each supported series explicitly.
- This initial profile rejects absent expression values, NaN and infinity.
  Irregular intervals and unscheduled/missing visits are represented by the
  actual finite observed coordinates. The checker cannot distinguish an
  intentional irregular schedule from a missing scheduled visit without the
  study protocol. No interpolation, imputation, averaging, reordering of the
  files, normalization or transformation is performed by the checker.

The separate `temporal-contract.json` is an integration audit sidecar with
`schema_version=bulk-temporal-audit/1` and `status=proposal`. The
[fixtures](../tests/fixtures/bulk_time_series/regular_longitudinal/temporal-contract.json)
are the executable examples. It references the three input files, declares
`time_basis=observed`, one explicit time unit and origin, sampling design, missing-value
policy, normalization/transformation, provenance, and intended network semantics.
The checker validates declarations and input consistency; it cannot establish
that a declared preprocessing step was actually performed or that upstream
supports the intended output.

Network semantics distinguish global, condition and series scopes; direction;
sign; score meaning; and no lag, sampling-step lag, or elapsed-time lag. Step
lags are positive integers in `step` units. Elapsed lags use the observed unit.
On irregular grids, one step has no fixed elapsed duration. Record the upstream
method's matching/interpolation policy, window boundaries and lag resolution in
its scientific audit; the checker does not implement those policies or verify
an inferred network.

Run:

```bash
.venv/bin/python scripts/validate_bulk_time_series.py tests/fixtures/bulk_time_series/regular_longitudinal/temporal-contract.json
.venv/bin/python scripts/validate_bulk_time_series.py tests/fixtures/bulk_time_series/irregular_independent/temporal-contract.json
.venv/bin/python -m pytest tests/test_bulk_time_series_contract.py -q
```

The command returns JSON, exits nonzero on invalid input, and includes input
SHA-256 hashes, genes, total columns, biological observations, per-series time
coordinates/intervals, and descriptive within-series longitudinal adjacency.
`runtime_support=false` is always explicit. A two-timepoint series has one
interval; reporting it as regular makes no claim about an unobserved schedule.

## Runtime adoption: `bulk-temporal/1`

The enriched TSV columns use the existing `timepoints` and `replicates` extra
IDs. The audit JSON sidecar remains an offline input. Runtime manifests instead
carry this optional top-level object:

```json
{
  "temporal": {
    "schema_version": "bulk-temporal/1",
    "time_basis": "observed",
    "time_unit": "hour",
    "time_origin": "Treatment start",
    "sampling_design": "longitudinal",
    "missing_values": "reject",
    "preprocessing": {
      "normalization": "Describe the actual normalization, or none",
      "transformation": "Describe the actual transformation, or none"
    },
    "provenance": "Data source and preparation"
  }
}
```

The canonical declaration schema lives in `dataset-manifest.schema.json`;
simulator-output and smoketest schema projections are checked for exact equality.
`andrea/core/shared/bulk_temporal.py` supplies both the runtime and offline TSV
semantics. Runtime activation requires a bulk expression profile and both
enriched metadata tables. It validates exact column joins, observed units,
biological observations, technical measurement labels and series boundaries.
It rejects missing or nonfinite expression/time values. No normalization,
aggregation, interpolation or transformation is performed.

The optional object travels through inference preflight, planning, frozen
manifests, dataset content fingerprints, run reports and downloadable bundles.
The wrapper receives the same declaration as `temporal` in `/io/execution.json`,
beside the existing `mode`, with the declared TSV extras in `/io/extra`.
Simulator manifests may opt into the same object; generation validates it and
preserves it in the inference manifest and dataset fingerprint. This does not
assert that any current simulator emits the enriched profile.

Catalog and custom tools can declare
`temporal_requirements={"required":true,"sampling_designs":["longitudinal"]}`.
Compatibility checks enforce the declaration and permitted sampling design.
Optional `technical_replicates="reject"|"allow"` and
`min_timepoints_per_series` (integer, at least two) refine a tool's accepted
design. These checks reuse the validated temporal summary for both catalog
eligibility and requested runs. A rejected technical design is blocked before
execution; setting `reject` never averages measurements. Omitted restrictions
preserve the earlier compatibility behavior.
The inference GUI exposes the declaration fields, uploads the existing extras,
displays series counts and units, and preserves the declaration in submitted
manifests. CLI commands consume the same manifest without extra flags.

Existing `schema_version=1.0` manifests without `temporal` keep their previous
validation and content fingerprints. Enriched manifests require this Osaka
runtime extension; older strict schema readers reject the new field. The audit
sidecar is not mounted or hidden in `notes`, and it is not an extra input ID.

## Output preservation for future lag-exporting methods

An edge-lag method also needs a lossless output extension **before it can be
declared functionally integrated**. A compatible design can retain the current
six-field network projection for existing evaluation and introduce a versioned
edge-dynamics artifact keyed by source, target, context and lag (including lag
kind, units and any window bounds). Preserve and validate it through container
output, merge, export, bundle, CLI and GUI paths. Define whether evaluation
compares lag-specific edges or an explicitly documented collapsed projection;
never discard lags while presenting the result as the full method. Do not encode
lags in identifiers, sign, score, evidence or ad hoc context strings. This branch
documents the extension; it does not implement that output lifecycle. dynGENIE3
exports a global weighted network without edge lags, so its input finite
differences do not require an edge-lag artifact.

## Temporal cost and acceptance requirements

Record genes, distinct observed points **per series**, independent biological
series/cohorts per condition, independent samples per time, technical measurements,
the actual grids/intervals, and any dynamic parameters such as maximum lag,
window size, interpolation/ODE settings or solver tolerances. Total matrix
columns alone is insufficient: four series of three points and one series of
twelve points have different permissible transitions and effective lag windows.
Native/fixed model dimensions and requested versus exported dimensions remain
explicit. Never infer biological replication from a simulation plan's run count.

The existing estimators generally expose genes and total expression columns as
size axes. The Osaka inference planner now guards tools that declare
`temporal_requirements`: it admits a benchmark profile only when the validated
number of independent series, uniform timepoints per series, separate
steady-state sample count, observed unit, sampling design and regular/irregular
sampling class all match its declared temporal fixture. Unknown geometry,
unequal series lengths, technical measurements or mismatches produce an
explicit uncalibrated fallback ETA. The plan computes this geometry once from
current inputs and records it in estimation provenance. Profiles with the same
matrix-column count but different series boundaries cannot substitute for one
another. This guard does not interpolate across temporal structures or claim
coverage of exact elapsed-time grids, which current cost metadata does not
standardize.

Stratify supported temporal structures as separate justified finite cost
profiles (or implement a reviewed estimator extension) before merging their
observations. The benchmark input generator and coverage checker must actually
construct and verify each profile's declared structure; an arbitrary relabeled
matrix cannot close this gate. The guard supplies no timings or calibration.

For each freely variable supported axis, start with three levels and
independent crossings, such as genes, points per series and series count, then
justify a finite panel rather than every parameter's Cartesian product. Include
the method's actual defaults as well as explicitly reduced smoke settings.
Respect resource strata and at least three successful repeats per new point;
reserve independent interpolation/extrapolation cases. Preserve per-run timings,
failures, immutable image identity, input hashes, resolved parameters and actual
exported temporal structure. These fixtures are too small and unmeasured to
establish any calibration domain or predictive accuracy.

A future bulk temporal integration closes only after it has evidence for the
observed-time design and every supported/excluded mode; validated series-aware
input delivery; defaults and parameter traceability; tested identifier,
direction/sign/score/context/lag preservation; meaningful functional and
repeatability checks; calibrated cost coverage and independent prediction checks
within its declared temporal/resource domain; and verified core/CLI/GUI/catalog
paths. Image publication is a separate state. Runtime adoption alone does not
close scientific or cost-calibration requirements; unsupported designs and
unmeasured temporal/resource domains remain pending.
