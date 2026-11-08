# Bulk temporal input examples

These deterministic synthetic values exercise input contracts. They are not
measured biological data, ground truth or cost observations.

| Fixture | Genes | Series or cohorts | Times per series | Temporal columns | Stationary columns |
| --- | ---: | ---: | --- | ---: | ---: |
| `regular_longitudinal` | 3 | 4 longitudinal units | 0, 2, 4 hours | 24 | 0 |
| `irregular_independent` | 3 | 2 independently sampled cohorts | 0, 1, 4 / 0, 2, 5 hours | 12 | 0 |
| `dyngenie3_complete` | 3 | 2 longitudinal units | 0, 6, 12 hours | 6 | 2 |

Every fixture has one `expression.tsv`, one `column_descriptors.tsv` joined by
exact column IDs, and a normal `dataset-manifest.json`. The descriptor InputSpec
is the same for every dataset selection. A requested temporal method checks
the sampling design, time units and other scientific fields supplied in the
descriptor table; those contents do not filter the catalog. No separate time,
replicate or stationary-expression files are inputs.

`regular_longitudinal` includes two technical measurements per biological
observation. It validates the general data contract but both new inference tools
reject this sampling branch; they do not silently average it. There are eight
biological transitions, without crossing any course boundary.

`irregular_independent` uses distinct biological units at each time. Its cohort
identity does not establish longitudinal pairing and it has no longitudinal
transitions. This sampling branch is also rejected by both new tools.

`dyngenie3_complete` is a runnable example with two courses and two independent
stationary samples in the same matrix. The main manifest also includes the
optional `tf_list.txt`; `dataset-manifest-required-only.json` omits only that
optional regulator restriction. `tools-params.json` selects global dynGENIE3
with its native defaults. SWING can consume the same matrix using the temporal
columns only when its width/lag requirements fit the three-point grid.

See the [current shared contract](../../../docs/bulk-time-series-contract.md)
for field meanings and native tool mappings. CLI and GUI use these same files.
