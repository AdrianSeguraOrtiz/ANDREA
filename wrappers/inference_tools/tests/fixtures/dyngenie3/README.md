# dynGENIE3 contract fixture

This is a deterministic synthetic fixture for interface and functional checks,
not measured biology, a truth network or a runtime calibration observation.
It contains six genes and three independent bulk trajectories with observed
times 0, 1, 3, 6 and 10 hours. Five genes follow smooth, distinct abundance
curves; `constant6` stays at 5 to exercise the native zero-decay branch.

The expression column order is independent of the reversed metadata row order.
One biological replicate belongs to each series, with one `tech1` measurement
per observation. Two series share the control condition and the third has a
different condition; the wrapper pools their within-series transitions only.
IDs include punctuation, Unicode and a numeric-looking string to detect
accidental identifier coercion.

The same `expression.tsv` also contains three independent steady-state samples
on the identical ordered gene axis. The single `column_descriptors.tsv` marks
all 18 columns as `time_series` or `steady_state`. Steady-state rows have empty
series, time, time-unit and sampling-design fields; no artificial times are assigned.
`tf_list.txt` restricts candidate sources in its optional branch. The descriptor
InputSpec is stable across dataset selections; this requested dynGENIE3 run
requires the observation roles, time units and longitudinal sampling declared
in the TSV. Descriptor contents do not filter the catalog. `tools-params.json`
retains real scientific defaults; smaller functional variants are declared
separately in the smoketest configuration.
