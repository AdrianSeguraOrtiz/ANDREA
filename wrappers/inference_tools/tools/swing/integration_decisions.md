# SWING integration decisions

This document records the scientific contract and coverage of the pinned SWING
integration. Installation and compatibility preparation live in `Dockerfile`
and `run_tool.py`. The canonical ToolSpec and smoketest remain in their shared catalog/test
locations. Cost calibration uses the shared cost-profile pipeline. Papers and the supplied upstream
clone are read-only evidence; measurements and temporary preparation belong to
the existing ignored `release-logs/` output. No per-tool integration dossier,
requirements lock, Docker ignore file or duplicate ToolSpec is needed here.

## Scientific evidence and implementation identity

The supplied article is **Justin D. Finkle, Jia J. Wu and Neda Bagheri (2018),
“Windowed Granger causal inference strategy improves discovery of gene
regulatory networks”, PNAS 115(9), 2252–2257**, DOI
[10.1073/pnas.1710936115](https://doi.org/10.1073/pnas.1710936115).
Local references below use `papers/article.txt` and the unmodified source at
`repo/SWING`.

The supplied extracted text has SHA-256
`f120ff613951408acaa6ec7c77ebec3ee16736a94d2aa9eabb0f2f53084ead16`;
the optional original PDF has SHA-256
`f74cb27a23bfd9c5e2eb87b2e6c7bf24f2fe1564f44e756ab24cc3a7a0c9c2ee`.

The article describes RandomForest, LASSO and PLSR variants at lines84–124,
window/lag construction at98–112, and data-dependent parameter selection at
508–527. Its detailed supplementary methods are referenced at559–563 but were
not provided locally. Attempts to retrieve the supplement returned a browser
check or HTTP403; no supplement-dependent implementation claims are made.

The selected implementation is the authors' Python repository
[bagherilab/SWING](https://github.com/bagherilab/SWING), fixed at
`591efc7c7c8e6bae2a610a833ec727047f7f1673`. The public master reference matched
this pin when inspected. The only published release is prerelease `v0.1.0`,
without binary assets; its code matches this commit, whose later change is
README-only. The exact commit therefore provides an appropriate reproducible
source installation, without adopting an unrelated package that currently
occupies the PyPI name `swing`.

The verified public archive has SHA-256
`f281aa5fe8959bb60c891b7c96ce73063d618c2765daee8f4b430cb0395b64e8`.
All nine required Python modules, including `Swing.util`, are checked before
copying or patching. Upstream `setup.py:45` declares only `packages=['Swing']`
and can omit that required subpackage; the build prepares the complete verified
module set instead. The original requirements target an old numerical stack;
they are evidence of the original environment, not a verified modern install.
Upstream setup metadata declares “Apache Software License” but its tree has no
standalone LICENSE/COPYING document; do not invent a more specific license
statement from the paper's availability.

## Observed temporal input

Reuse the shared [observed bulk temporal contract](../../../../docs/bulk-time-series-contract.md).
The only enabled execution mode is `global`, over bulk longitudinal data with
an observed time basis. One `expression.tsv` holds all observations and required
`column_descriptors.tsv` assigns their time_series or steady_state roles, series,
biological units, observations, technical measurements, times, time units and
sampling design. These fields are requirements of a requested SWING run, not a
conditional InputSpec selected by the dataset axes. Catalog filtering uses
declared metadata, available input keys and existing compatibility rules;
descriptor contents are interpreted only for the requested method. No duplicate
declaration is requested in the GUI or execution envelope. SWING selects only
time_series columns before scaling or fitting. Optional `tf_list` restricts
explanatory genes for RF/PLSR. A restricted
Lasso TF subset is rejected because its multi-lag self-predictor removal assumes
the matched complete gene universe. All-gene Lasso remains available.

Every independent series must contain the same complete ordered observed time
grid, at least two distinct coordinates, and a regular sampling interval.
The selected width and maximum lag must also satisfy
`timepoints_per_series >= window_width + max_lag`; constructor defaults
therefore still require at least three timepoints. Both core and wrapper use
the shared regularity policy (`math.isclose`, relative 1e-9, absolute 1e-12).
Validate actual coordinates, units, sampling design and observation/series joins before
calling SWING. Reject incomplete or unequal grids, irregular intervals,
nonfinite expression, technical duplicate observations, and independent
destructive samples. No interpolation, imputation, implicit averaging,
time-origin shifting or normalization beyond the selected z-score occurs.

The native input is a Time+genes table with each complete independent course in
a separate contiguous block. SWING selects a window using `Time.isin(...)`
(`Swing.py:125–129`) and horizontally stacks lagged windows (`280–317`).
Complete aligned courses preserve the same observation-row pairing across
every explanatory/response window. A window never crosses a biological series
boundary. Gene IDs are opaque strings: invertible internal aliases prevent
collisions with the time column, numeric-ID conversion and Unicode loss; the
final network restores original IDs and provenance records the mapping.

Lags are integer window displacements, not arbitrary physical durations
(`Swing.py:650–664`). With the supported stride1 and interval `dt`, lag `k`
means `k*dt` in the declared time unit. For `T` timepoints and width `w`, there
are `T-w+1` candidate positions and `T-w+1-max_lag` fitted response windows.
Parameters must leave at least one fitted window. Each fit pools
`w * n_series` observations and `n_candidates * (max_lag-min_lag+1)` predictor
columns before contemporaneous self-prediction removal.

## Parameter surface and defaults

The integration exposes all three native window families, with the exact
constructor defaults **RandomForest, width3, min_lag0, max_lag0, stride1**
(`Swing.py:21–22`). The default is therefore contemporaneous window regression;
it does not establish strictly time-delayed causality. The paper and official
lagged demonstration instead use width10 and lags1–3. Those are explicit
data-specific example values, not an automatically optimal default or a reason
to silently adapt parameters to a short input.

| Public configuration | Native mapping and selected default |
| --- | --- |
| `window_type`, `window_width`, `min_lag`, `max_lag`, `step_size` | Constructor names; defaults RF/3/0/0/1. Width>=1, nonnegative ordered finite lags, stride1 only. Width1 retains one observation per independent course in each window; backend row/predictor constraints still apply. |
| `zscore` | True matches the distributed pipeline; `zscore_all_data()` uses global per-gene sample standard deviation, ddof1 (`Swing.py:446–463`). |
| `n_trees` | RF `initialize_params(n_trees=...)`, default500. The executable example uses10 and notebook100 as overrides, not the native default (`RFRWindow.py:105–119`). |
| `alpha` | Lasso regularization; default null invokes original per-window CV, explicit alpha>=0 skips alpha selection. |
| `alpha_cv_folds`, `alpha_cv_method`, `alpha_grid` | Original `cv_select_alpha`; defaults10, modelQ2, null. Null grid means50 evenly spaced values from0 to null-alpha. `max_posQ2` and an explicit increasing nonnegative grid are also available (`LassoWindow.py:253–298`). |
| `null_alpha`, `null_alpha_max`, `null_alpha_min_step` | Original null-alpha search or positive explicit override; defaults null,1e4,1e-9 (`177–251`). No replacement analytic estimator. |
| `n_bootstraps`, `n_alphas`, `noise` | Original individual `run_bootstrap` defaults10,20,0.2 (`75–93`), preserving column-wise resampling and multiplicative Uniform(1-noise,1+noise). The high-level `rank_edges` convenience method instead defaults to1000 repeats; that is not the selected entrypoint. |
| `pcs` | PLSR/Dionesus component count; null preserves native PCA/elbow selection, an explicit positive count bypasses that selection (`DionesusWindow.py:132–188`). Auxiliary fallback values2/3 in other methods are not the selected automatic rule. |
| `n_permutations` | Optional native diagnostic, wrapper default0(disabled); enabled counts must be>=2. Native individual defaults differ by backend. Every diagnostic refit uses the selected forest tree/worker counts, Lasso alpha or PLS component count. p-values remain auxiliary and never replace inference scores. |
| `lag_method`, `sort_by` | Pipeline defaults mean_mean/mean; helper default max_median remains selectable. Reductions and score meanings are described below. |
| `seed` | ANDREA reproducibility extension, default0, with deterministic per-window/per-phase Python and NumPy streams. It is not an argument published by upstream. |
| `maxcount` | ANDREA export limit, default all; zero intentionally writes an empty network. It does not alter model fitting. |

Parameters belonging to an inactive backend cannot be changed from their
declared defaults. RF `max_features="sqrt"`, ordinary estimator defaults and
native PLS `scale=False` are retained rather than inventing additional model
options absent from SWING's API. Resource `--threads` controls RF forest
`n_jobs`; targets and windows remain sequential. Lasso/PLSR are serial in these
APIs, and native numerical pools remain at one thread for every backend.
The established compatibility rules cover restrictions expressible through
declared dimensions, parameters and execution mode. Requested-run temporal
validation checks metadata joins and sampling geometry. The wrapper checks
actual window dimensions, candidate counts and backend-specific CV/component
constraints before fitting; there is no additional numeric-expression language
in the shared rule evaluator. Scientific degeneracy detected from fitted values
also fails explicitly inside the wrapper.

Runtime progress reports initialization, inference before the first fit,
completed-window counts, aggregation, export and completion. Its
`task_partitions` unit is completed windows; percentages are phase milestones,
not predictions of elapsed or remaining time. A compact auxiliary event journal
records the actual emitted snapshots and monotonic observation times. The
physical gate verifies the full lifecycle and window counters, rather than
accepting a final100 snapshot as evidence of intermediate progress.
Resource evidence distinguishes assigned/requested limits from actual estimator
constructor/fitted `n_jobs` and tree counts. Normal inference observes each
forest without intercepting individual trees. Explicit diagnostic environment
`ANDREA_SWING_TRACE_TREES=1` additionally records identities entering tree
construction and restores the original hook afterward. This diagnostic has a
measurable overhead to assess separately and is disabled for normal inference
and calibration. Neither receipt claims full CPU utilization. Observation does
not change model arguments, outputs or random streams.

Lasso automatic alpha selection requires at least two test observations in
each fold: its Q2 denominator centers within each test fold, so leave-one-out
has zero denominator (`LassoWindow.py:327–337`). It retains native prediction
and Q2 definitions, including the original dot-product prediction. Null-alpha
search retains its density/convergence requirements. Unsupported or nonfinite
results fail explicitly instead of replacing the search, choosing fewer folds
or imputing a score. PLS automatic selection requires sufficient dimensions;
an explicit component count must fit centered observations and predictors.
Degenerate normalization, VIP or coefficient values are rejected; undefined
optional diagnostic p-values are retained in raw evidence with a warning.
In particular, ten-fold default alpha selection requires
`n_series * window_width >= 20`; a small input does not silently reduce that
default. Width1 is a valid upstream window size when the selected backend has
enough pooled observations. The previous wrapper minimum of2 had no upstream
justification and has been removed; the default width remains3.

## Direction, sign, scores and lag evidence

Native coefficient/importance matrices have rows=Child and columns=Parent;
the exported direction is **Parent -> Child**. RF uses feature importance,
PLSR uses VIP and retains signed Beta separately, and Lasso uses coefficients
with bootstrap stability for adjusted importance. The canonical output is
directed, **unsigned association**, with global context. It does not assert
activation/repression, calibrated probability or experimentally established
causality. Window-specific lag and coefficient evidence stays in native tables;
it is not fabricated into a network context or a single inferred lag per edge.

The official static aggregation first retains only rows with
`Importance>0` (`Swing.py:546–547`), including Lasso. Thus negatively signed
Lasso coefficients are omitted by the selected pipeline. This asymmetry is
preserved and disclosed; taking absolute coefficients would be a deliberate
scientific change, not a compatibility repair.

For `lag_method=outer_inner`, inner reduces the values within each lag and
outer reduces those lag summaries. Whitelisted reductions are mean, median,
min and max; arbitrary text is never passed to upstream's eval. `sort_by`
selects the final field:

- `mean` averages all retained native Importance rows and ignores lag_method.
- `max` takes maximum native Importance and also ignores lag_method.
- `lag` applies the two-stage reduction to Importance.
- `adj` applies it to adjusted importance, using Lasso stability.
- `rank` applies it to within-window ranks, where smaller is better. Export
  uses the declared monotonic transform `1/(1+native_rank)` so larger remains
  better; absent interactions receive confidence0 and are omitted.

The paper describes rank-based aggregation (`article.txt:98–112`), whereas
the distributed pipeline/notebook call `make_sort_df()` with default mean.
Both paths are configurable, but reproducing the distributed code is not a
claim of reproducing every unpublished detail of the paper's benchmark.
Only positive non-self export scores are retained; ties use deterministic
gene-ID ordering without changing scores.

## Checked repairs and exclusions

Build preparation checks the original source archive and individual file
hashes before applying exact replacements inside `run_tool.py`. The supplied
clone is never patched. Normal inference does not download or modify source.

| Repair | Scope |
| --- | --- |
| String identity comparisons -> equality | Select the intended Lasso/Dionesus branches reliably. |
| pandas append -> concat | Preserve concatenation under pinned modern pandas. |
| Static-edge positional index | Group already-filtered edge row positions once instead of repeatedly scanning the full table. Preserve original row order/index, missing edges, positive-Importance/self/TF filters, idxmax ties and every native reduction. |
| Native input parser float_precision=round_trip | Preserve provided float64 times/expression across CSV parsing, checked before fitting. |
| PLS keyword scale=False and flattened coef_ orientation | Adapt current sklearn API without changing predictor order or scaling. |
| PLS permutation component count | Refit with the selected `num_pcs` instead of silently using `get_coeffs`' helper default2. This corrects the optional diagnostic and leaves primary inference scores unchanged. |
| Lasso/RF helper consumers of get_coeffs | Match their actual DataFrame return instead of destructuring column labels. |
| Lasso linspace integer count and Series.idxmax() | Repair removed/invalid numeric and pandas API uses. |
| Single-repeat bootstrap dimension | Preserve an explicit bootstrap axis when repeats=1. |
| TF filtering across every lag | Remove all forbidden candidate columns consistently, rather than only their first occurrence. |

Excluded behavior is explicit: stride!=1 is inconsistent in upstream window
construction; max_lag=null/append mode compares None with integers; randomized
time controls select inconsistent raw versus explanatory windows; restricted
Lasso TF subsets assume invalid index alignment. Gold-standard evaluation,
plotting, artificial subnetwork selection and manual data-cleaning utilities
are outside inference. `get_null_alpha(s_edges=False)` remains the selected
internal counting policy, not a new public self-edge mode. The paper's
SWING-Community is a combination of methods, not a fourth dispatcher mode in
this repository. Unsupported controls are rejected explicitly.

## Installation and validation

The Dockerfile pins the Linux/amd64 Python3.11 base by digest, and pins the
complete numerical dependency closure with wheel hashes directly in the same
file: NumPy1.26.4, SciPy1.12.0, sklearn1.4.2, pandas2.2.2, joblib1.4.2,
threadpoolctl3.5.0 and pandas' required support packages. The verified source
archive is prepared by `run_tool.py --prepare-upstream`, including `Swing.util`.
The unpublished image remains **1.0.0** throughout corrections; source/image
identities in actual verification use immutable hashes.

The canonical fixture contains four opaque gene IDs, including `Time`, `0002`
and Unicode, two independent biological courses, and eight aligned hourly
visits per course. It is deterministic synthetic contract data, not biological
truth or calibration evidence. The manifest, tool parameters, temporal extras
and optional TF list use shared dataset/input schemas. The canonical smoketest
configuration covers real constructor defaults, lagged RF, TF restrictions,
normalization and score branches, permutation diagnostics, empty exports,
automatic/fixed PLS components, Lasso fixed/CV alpha and stability branches.
It also covers width1 and the original automatic null-alpha search with both
fixed alpha and the default fifty-value CV grid. Direct regression tests check
that PLS permutation fits retain selected component counts1 and3. A physical
two-gene/one-predictor PLS smoke confirms that `pcs=1` permutations work rather
than failing by trying to refit two components.
The secondary `tests/fixtures/swing/alpha_cv_default` fixture has four courses
with the same eight-point grid. Width6 gives24 observations per window,
supporting the actual10-fold alpha-selection default without changing its
value. Its separate manifest and parameters distinguish that shape from the
small canonical smoketest.

The initial integration review passed 80 focused wrapper regressions and all 22 canonical
Docker smoke variants under one assigned CPU and 2 GB RAM. Additional small
checks exercised the original ten-fold/fifty-alpha CV selection with automatic
null-alpha search, the one-predictor PLS permutation case, and RF seed/resource
reproducibility. The same seed produced byte-identical canonical networks for
repeated runs and one/two assigned threads; a different seed changed the result.
The actual two-worker forest configuration and overlapping tree tasks were
observed separately from the requested allocation. These checks do not measure
CPU saturation, validate cost predictions or establish biological accuracy.

Cost calibration is defined separately in the established shared
`cost_profiles/swing.json` location and its measurements belong to ignored
release outputs. A `cost.json` can only claim accuracy within its measured and
independently validated parameter/input/resource domain; arbitrary inputs,
unmeasured parameter combinations and data-dependent numerical behavior are
not covered merely because the method accepts them.

Native provenance retains resolved parameters, observed grids, selected and
excluded observation roles, aliases, per-window edges/CV results, raw lag tables,
ranked native edges, phase seeds, input/source hashes, software versions and
actual resource observations. The only wrapper source files are Dockerfile,
run_tool.py and this document, beside the supplied papers and clone.


## Performance correction, 2026-11-08

The long calibration was stopped before changing its image. All 3,538 real
observations (3,423 OK and 115 timeout), raw outputs and provenance were checked
before the correction; two interrupted fits produced no terminal result. Its
old model was withdrawn because timings from that image cannot describe the
corrected implementation. Obsolete model copies were subsequently removed;
historical raw data remains diagnostic evidence outside the catalog.

Most newly observed timeouts occurred during aggregation after all windows had
been fitted. The pinned upstream static-edge routine filtered the complete edge
table for each possible edge. The wrapper's hash-guarded preparation now builds
one positional group index and leaves all score expressions untouched. Exact
unit/replay comparisons cover 16 lag reductions, five sorting policies,
nonpositive/missing/self/TF edges, ordering, indices, ties and canonical CSV.
Normal forest fitting also avoids per-tree tracing; optional diagnostics remain
available without changing public scientific parameters or defaults.

The unpublished image keeps tag `1.0.0` and the upstream commit. Its immutable
image ID and build context identify the corrected native runtime; historical
wall times are not imported as its new calibration. Focused verification passes
290 tests, including 194 SWING wrapper regressions. The performance experiment
and the calibration withdrawal are recorded under
`release-logs/swing-performance-20261108/`; pilot measurements do not count as
catalog calibration and do not certify prediction accuracy.

The reduced reference calibration and its partial model were stopped and
removed at the user's direction. SWING has no active `cost.json`, running
calibration or automatic restart. A future campaign must cover the unchanged
full 77-profile/4,158-slot panel with the original 1,800-second native timeout;
calibration is deferred until then.
