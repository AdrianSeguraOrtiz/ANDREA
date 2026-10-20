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

`steady_state_expression.tsv` supplies three independent steady-state samples
with reversed gene row order, requiring exact ID alignment. `tf_list.txt`
restricts sources in the optional-input branch. The manifest declares their
origin and observed-time semantics. `tools-params.json` retains real scientific
defaults; reduced-tree functional variants are declared separately in the draft
smoketest configuration.
