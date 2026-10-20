# Bulk temporal contract fixtures

These are deterministic **synthetic bulk population mean abundances**, designed
to exercise time-series metadata. They are not measured data, single-cell
matrices, outputs of an upstream simulator, scientific ground truth, or
calibration observations. Their numeric trajectories do not establish a causal
GRN. No network is supplied.

| Fixture | Genes | Biological series | Distinct times per series | Biological observations | Expression columns |
| --- | ---: | ---: | ---: | ---: | ---: |
| `regular_longitudinal` | 3 | 4 | 3 (`0,2,4` hours) | 12 | 24 |
| `irregular_independent` | 3 | 2 experimental cohorts | 3 (`0,1,4` / `0,2,5` hours) | 12 | 12 |

In `regular_longitudinal`, `control_r1`, `control_r2`, `treated_r1`, and
`treated_r2` are separate longitudinal biological units. Each unit has three
observations with two technical measurements per observation. The technical
measurements share `observation_id`; their `technical_replicate` differs. The
eight within-series adjacent observation pairs must never become a single
concatenated trajectory or sixteen technical-measurement transitions.

In `irregular_independent`, each condition has an experimental cohort with two
independently sampled biological units at each time. Every physical sample has
its own `replicate` and `observation_id`; repeated sample numbers across time do
not establish pairing. `series_id` groups the cohort design, not a longitudinal
subject. The audit reports time groups and **zero biological longitudinal
transitions**. The timepoint metadata rows deliberately run in reverse expression
column order to verify joining by identifiers.

The values can be reproduced from time `t`, biological replicate/sample index
`r` in `{1,2}`, and technical index `k` in `{1,2}` (only `k=1` for independent
samples). With `b = r + (k-1)/10`, the three rows are:

```text
G1 = 10 + 2*t + b
G2 = 20 + a*t + b     a=1 for control, a=3 for treated
G3 = 30 - t + b
```

Values are arbitrary abundance units, with no normalization or transformation.
All expression values and times are finite and present. An absent scheduled
visit would be represented by the absence of that observation, never by silently
imputing an expression value or inserting a transition.

Each `dataset-manifest.json` conforms to the existing v1 runtime schema and
references the existing `timepoints` and `replicates` extra IDs. Extra metadata
columns are permitted by their current TSV specs. The separate
`temporal-contract.json` uses the **proposed offline audit format**, which core,
CLI, GUI, and container delivery do not discover or interpret.

Run from the repository root:

```bash
.venv/bin/python scripts/validate_bulk_time_series.py tests/fixtures/bulk_time_series/regular_longitudinal/temporal-contract.json
.venv/bin/python scripts/validate_bulk_time_series.py tests/fixtures/bulk_time_series/irregular_independent/temporal-contract.json
.venv/bin/python -m pytest tests/test_bulk_time_series_contract.py -q
```

See [the contract audit](../../../docs/bulk-time-series-contract.md) before using
these files to design a wrapper or cost campaign.
