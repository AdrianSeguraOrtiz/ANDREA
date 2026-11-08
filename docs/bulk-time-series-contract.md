# Observed bulk expression and column metadata

All observations use one canonical `expression.tsv`: genes in rows and
observations in columns. Time courses and stationary samples may share this
matrix only when their gene identifiers, measurement scale and preprocessing
are scientifically compatible. Shape validation cannot establish that biological
compatibility.

The single standardized extra `column_descriptors.tsv` describes every
expression column exactly once. Row order is irrelevant; `column` joins by the
exact expression-column identifier. No names are decoded to infer series,
replicate identity or time. `tf_list.txt` remains an optional regulator list.
There is no separate stationary-expression matrix or separate time/replicate
metadata input.

## Stable input format and method requirements

`column_descriptors` has one generic InputSpec for every dataset: an identifier
column followed by one or more metadata columns. Dataset selectors do not
change its required headers or validation profile. The normalized temporal
field names below retain the same meanings and types wherever they appear;
their presence is not globally required.

Requesting dynGENIE3 or SWING additionally requires the metadata needed to
interpret that method's observations:

| Field | Meaning |
| --- | --- |
| `column` | Exact expression-column identifier. |
| `observation_type` | `time_series` or `steady_state`. |
| `series_id` | Independent longitudinal course; required for temporal rows and empty for stationary rows. |
| `condition` | Experimental condition. |
| `replicate` | Biological unit identity, distinct from series and technical measurement. |
| `observation_id` | Biological observation identity. |
| `technical_replicate` | Technical measurement identity for that observation. |
| `timepoint` | Finite observed sampling coordinate for temporal rows; empty for stationary rows. |
| `time_unit` | Coordinate unit for temporal rows; empty for stationary rows. |
| `sampling_design` | `longitudinal` or `independent_samples` for temporal rows; empty for stationary rows. |

All applicable identities must be nonempty. Stationary samples have no invented
zero time or artificial series. Technical measurements of one observation are
not additional biological visits. Current dynGENIE3 and SWING integrations
reject technical duplicate observations and independent destructive sampling.
No automatic averaging, imputation, interpolation or transformation occurs.

Other tools retain their existing descriptor behavior. In particular,
scGeneRAI aligns all supplied descriptor columns with expression columns and
passes them as categorical model inputs during fitting and prediction. There
is no new naming prefix or automatic removal of organizational fields. A table
prepared for a temporal method is not necessarily a scientifically appropriate
set of covariates for scGeneRAI.

## Manifest and runtime

No separate temporal declaration or GUI toggle is needed. The GUI may offer
temporal guidance for `bulk + timepoints`, but those axes do not change the
InputSpec or trigger scientific interpretation of every descriptor table.
When a temporal method is requested, its scientific facts come from
`column_descriptors.tsv`. Its time-series rows must state one consistent unit
and sampling design. The axes cannot establish longitudinal pairing:
independent samples must be labelled explicitly. Stationary rows leave their
series, time, unit and sampling design empty.

`column_metadata.py` supplies the temporal parser and summary used for the
requested run and by both wrappers. `bulk_temporal.py` checks exact joins,
finite expression values, identities, series boundaries and sampling geometry
for that request. Transitions exist only within longitudinal temporal series,
never between courses or stationary observations. Text describing origin,
normalization, transformation or source is not an additional required inference
input. General dataset notes may document scientific preparation when useful.

A ToolSpec declares its accepted sampling designs, technical-measurement policy,
minimum visits and selected `observation_types` in `temporal_requirements`.
These requirements are checked for the requested run, separately from catalog
filtering. Role selection determines effective input dimensions, provenance
and the wrapper's model data. dynGENIE3 selects both roles and maps the temporal
blocks to native `TS_data`/`time_points`, and stationary columns to `SS_data`.
SWING selects temporal columns before its optional z-score; it requires aligned,
complete regular grids and at least `window_width + max_lag` visits per course.
Stationary columns never enter its fitting or scaling.

Catalog filtering uses declared dataset metadata, available input keys and
the established parameter/execution compatibility rules. It does not inspect
descriptor contents to decide which tools are compatible. Ordinary input
format and identifier validation remains separate from that filtering.

When dynGENIE3 or SWING is requested, preflight, planning and execution validate
its temporal data from the same normalized files. A catalog-compatible tool
can therefore fail its requested-run validation when required scientific
metadata is missing or unsuitable. Dataset fingerprints hash the files without
a second temporal declaration. Docker receives the matrix at
`/io/expression.tsv` and descriptors at `/io/extra/column_descriptors.tsv`;
`execution.json` selects the execution mode. The GUI displays validated design,
units, series and observation selection for the requested temporal run. It
never asks the user to repeat those scientific facts in form fields.

Simulation writers produce this same table. A time-series simulation request
requires `column_descriptors` in place of the retired timepoint extra; other
simulation scenarios retain their existing requirements. Single-cell trajectory
metadata does not imply a bulk longitudinal course.

## Complete example

The directly usable [dynGENIE3 fixture](../tests/fixtures/bulk_time_series/dyngenie3_complete/dataset-manifest.json)
contains temporal and stationary columns together, their descriptor table,
optional regulator list and ordinary dataset manifest. Its synthetic values
demonstrate the contract; they are not biological evidence or cost measurements. Required
algorithm inputs are `expression.tsv` and `column_descriptors.tsv`. The optional
`tf_list.txt` narrows regulators;
stationary observations are optional rows/columns in the same required inputs.

## Outputs and calibration

Both tools export the catalog's six-field canonical `network.csv` in global
mode. Native window/lag tables, degradation rates, diagnostics and saved models
remain tool-specific auxiliary artifacts under `provenance`, following the
other catalog wrappers. They do not need separate normalized output types.

The existing `wrappers/inference_tools/cost_profiles/` directory contains the
complete declarative panels for dynGENIE3 (68 profiles, 3,348 executions), SWING
(77 profiles, 4,158 executions) and BiXGBoost (27 profiles, 1,458 executions).
Each crosses CPU allocations 1/2/4 and RAM limits 8/32 GiB with three repetitions.
dynGENIE3 has its complete measured `cost.json` with strict provenance, attempt
and coverage checks passing. SWING and BiXGBoost have no `cost.json`; their full
calibrations are deferred. No campaign is running or queued.

Independent prediction-accuracy checks remain deferred. Measurements are bound
to their actual input contract and immutable image; invalidated timings are not
reused. The estimator requires matching measured temporal geometry. Arbitrary
parameters, TF counts, temporal shapes and extrapolation remain unvalidated.
Accuracy checks use frozen cost and estimator snapshots with predefined
error/budget criteria. Full measurement coverage alone does not prove
prediction accuracy.
