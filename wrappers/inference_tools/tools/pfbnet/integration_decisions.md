# PFBNet integration decisions

ANDREA deliberately integrates the **official expression-only E. coli workflow**.
The separate DREAM4 workflow that fuses a complete stationary knockout panel is
outside the admitted catalog scope. There is one implementation and no `variant`
parameter, implicit fallback, compatibility alias, inactive KO adapter or KO
calibration branch. This scope was explicitly selected by the user: support
observed bulk temporal expression without requiring a specialized complete-KO
experiment or extending the common calibration generator for it.

The standard tool files are `Dockerfile`, `run_tool.py` and this decision log.
The user-supplied `papers/` and `repo/` remain ignored source evidence, excluded
from temporary Docker build contexts and unused at runtime. Dependency versions,
hashes and source patches reside in the existing Dockerfile/wrapper. No extra
lock, patch, README, executor or resume script is introduced. Follow the
[common workflow](../../../../docs/INTEGRATION_WORKFLOW.md).

## Scientific identity and the deliberate exclusion

Dandan Che, Shun Guo, Qingshan Jiang and Lifei Chen, **PFBNet: a priori-fused
boosting method for gene regulatory network inference**, BMC Bioinformatics 21,
308 (2020), DOI [10.1186/s12859-020-03639-7](https://doi.org/10.1186/s12859-020-03639-7).
The supplied `papers/article.txt` has SHA-256
`31268eeec5be7f36edbe67b09c21079043c3246380e47897349c9cd11b5c8ce7`.
The article evaluates inference without prior information as well as fusion.
Its official repository is [ddche/PFBNet](https://github.com/ddche/PFBNet), pinned
at the user's commit `86ec8eb37aa8834645600eaaee51012342ac6324`.

The admitted source is `Ecoil/PFBNet_Ecoil.py`, SHA-256
`e6ee28b3114c7ef6a0933fb497232026547b6729378ce245967f6f33659b194e`.
`ecoil_main(data, sample_num, k, alpha, feature_num, iter_num)` accumulates
expression including each current endpoint, fits one boosted model per target,
then weights the regulator rows by their sample variance. It does not perform
DREAM4 knockout fusion or DREAM4 row normalization. Its published example values
are `k=2`, `alpha=0.45`, `iter_num=1000`; the function itself has no argument
defaults. E. coli dataset names are examples, not a taxonomic restriction.

`dream4/PFBNet_dream4.py` was reviewed to distinguish the methods. Its fusion
requires exactly one identified stationary KO per gene. Omitting it is a
scientific scope decision, not a claim that KO lacks value, that other methods
cannot use interventions, or that PFBNet upstream lacks the feature. ANDREA does
not replace it with fabricated KO data or claim the two implementations are
numerically equivalent. The DREAM module is neither patched nor installed in
the integrated image.

The upstream repository has no installable distribution or packaging metadata.
Source installation is therefore pinned by commit and exact file SHA-256. The
original dependency recommendation is XGBoost 0.6/Python 3.7. Public availability
and the open-access paper do not themselves supply a separate source-code
license grant; the supplied repository has no standalone license file.

## Normalized inputs and selection

Required inputs are the canonical genes-by-observations `expression.tsv` and
`column_descriptors.tsv`. Optional `tf_list.txt` supplies exact candidate gene
identifiers; absent means all genes. Every expression gene remains a target.
The wrapper orders candidates first for the native prefix API and restores
original identifiers on output. At least two distinct candidates are required,
since a candidate target excludes itself from its predictors. Whitespace and
blank lines follow the established text-list convention; gene strings retain
leading zeros, punctuation and Unicode.

Only descriptor rows with `observation_type=time_series` enter inference.
Stationary rows remain valid shared dataset observations but are excluded and
listed in `unused_steady_state_columns`. Their intervention metadata, if present
in shared descriptors, does not enter this expression-only algorithm. No
separate perturbation file, stationary matrix, timepoint file or replica file is
accepted. Every expression column still needs exactly one descriptor.

Each independent course identifies one condition and biological replicate.
Courses require equal length `T > k`, strictly increasing regular observed times
and a common positive sampling interval in a common unit. Origins may differ.
Courses are sorted and accumulated separately before pooling, so no window
crosses a biological series boundary. There are `S*(T-k)` effective training
rows; predictors are accumulated candidate genes, not `G*k` lag features.
The transform sums `k+1` values with weights `alpha**k` through `alpha**0`,
including the current endpoint. Reported offsets `0..k` describe preprocessing,
not inferred edge-specific delays or causal direction.

Technical duplicates of used temporal observations, irregular/unequal courses,
independent destructive samples and pseudotime substitutes are unsupported.
Unused stationary technical measurements do not block a run. No averaging,
interpolation, truncation, centering, rescaling, imputation or silent expression
transformation occurs. Input and accumulated arrays must remain finite at the
native XGBoost float32 precision. Constant targets are not rejected by an
invented pre-fit variance rule; native scores and the canonical output must be
finite and nonnegative.

Catalog compatibility uses existing declarations and input availability,
without reading file contents to select tools. Bulk observed `timepoints` are
admitted; incompatible expression axes, fewer than two genes or missing required
descriptors block through established rules. Missing optional TF input yields
the standard warning and uses all genes. Detailed geometry is validated for an
explicitly requested run. Execution is global only, without invented group,
condition-specific or native per-column modes.

## Parameters, resources and exact native behavior

| Public parameter | Behavior |
| --- | --- |
| `k` | Positive integer accumulation order; each course has more than `k` points. Default 2. |
| `alpha` | Finite temporal decay in `[0,1]`, including endpoints. Default 0.45. |
| `iter_num` | Positive boosting rounds for every target. Default 1000. |
| `seed` | ANDREA reproducibility control, integer 0 through 2147483647. Default 0 preserves the native dependency default. |

Native `sample_num` is derived from timepoints per course. Candidate prefix size
is derived from the TF list or gene count. They are not independent user knobs.
The learner keeps upstream `booster=gbtree`, depth 4, minimum child weight 4,
`lambda=0`, row/column subsampling 0.7/0.9, `eta=0.0008`, and native zero
gamma/L1 defaults. Importance remains `get_fscore()` split counts. No modern
XGBoost engine, alternative importance, objective, tuner or model setting is
substituted. The original regulator-row variance weighting uses `ddof=1`.

Inline guarded source patches generalize the original fixed three courses and
1484 genes, repair the feature-index scalar conversion for current NumPy, and
add explicit seed/thread controls plus callbacks after actual native fits and
before aggregation. Original bytes are preserved inside the image. The
accumulation order and original arithmetic remain unchanged.

The image compiles official XGBoost 0.60, commit
`4a8d63b6c8711fb839c71e26c659936252df1eb5`, with pinned dmlc-core/rabit archives
and SHA-256 verification. Its inline mechanical Python repairs cover the moved
scikit-learn import, `collections.Mapping` and wheel library path. Native C++
learner code is unchanged. Python and scientific dependencies are pinned in the
Dockerfile. Source and patched-source receipts are stored in the image.

ANDREA thread allocation maps to native `nthread` for each sequential target
fit and OpenMP limits; unrelated BLAS pools stay serial. RAM limits remain
container budgets, not an algorithm tuning knob. Completed-fit receipts record
passed parameters, actual tree counts, pools, affinity and cgroup facts. These
observations do not promise CPU saturation or speedup. The image remains
`adriansegura99/inference-tools_pfbnet:1.0.0`; development repairs do not increment
an unpublished version.

## Outputs and progress

`network.csv` is canonical `source,target,score,sign,evidence,context`, directed,
unsigned (`sign=?`), `evidence=association`, `context=global`. Self loops and zero
weights are omitted; native positive scores retain their scale. Empty valid
networks are allowed. Scores are neither calibrated probabilities nor validated
causal effects. Auxiliary native matrices are method-specific provenance, not
new public normalized output types.

`provenance/` records selected series and unused stationary columns, candidate
order, parameters, exact input hashes, source/session identities, native and
accumulated arrays, score matrix, completed-model resource receipts and progress
journal. The public progress file follows the common protocol. Completed work
increments only after `xgb.train` returns; aggregation follows all `G` target
fits, then export and successful completion. Percentages are milestones, not
elapsed fractions. A fitting or export failure retains its completed count,
marks failure and never reports successful completion.

## Full finite executable cost panel; no calibration launched

The executable declarative recipe is
[`cost_profiles/pfbnet.json`](../../cost_profiles/pfbnet.json). It uses the
existing profile resolver, generator, coverage validator and common executor;
no core, generator, provider, KO experiment or custom script is required.

The entire admitted panel has genes `G=50,100,200`; defaults independently cross
`T=21,42,84` timepoints and `S=1,2,4` courses. Every fixture is regular, has zero
stationary columns and total columns `S*T`. Scientific defaults are `k=2`,
`alpha=0.45`, `iter_num=1000`, `seed=0`. Every row below additionally crosses all
three gene sizes; unmentioned parameters stay at defaults.

| Family | Overrides | T | S | Profiles | Size points |
| --- | --- | --- | --- | ---: | ---: |
| defaults | None | 21,42,84 | 1,2,4 | 9 | 27 |
| accumulation order | `k=1,4,8` separately | 21,42,84 | 2 | 9 | 27 |
| boosting rounds | `iter_num=200,500,2000` separately | 21,42,84 | 2 | 9 | 27 |
| decay | `alpha=0,0.2,0.8,1` separately | 42 | 2 | 4 | 12 |
| combined | `k=4,alpha=1,iter_num=2000` | 21,42,84 | 2 | 3 | 9 |
| optional TF restriction | First `max(3,floor(G/5))` genes | 21,42,84 | 2 | 3 | 9 |
| TF restriction plus combined | Same TF policy and combined overrides | 21,42,84 | 2 | 3 | 9 |
| **Total** | | | | **40** | **120** |

The machine-independent allocation panel is threads `1,2,4` crossed with RAM
`8,32 GiB`, six strata, three repetitions: **2160 future container attempts**.
The per-attempt maximum remains **1800 seconds**. Standard `ok/timeout/oom/error`
outcomes apply. This is a recipe, not `cost.json`, a schedule or a measurement;
no campaign has been started. Machine capacity affects common-executor
concurrency, never which scientific profiles constitute the panel.

This is a complete finite panel for its declared domain, not a claim to estimate
any possible run precisely. All public scientific knobs and optional TF
presence are represented by selected values. Other TF fractions, explicit
all-gene TF files, arbitrary parameter interactions, irregular courses,
stationary fitting, KO fusion, different expression distributions, unmeasured
hardware and extrapolated sizes are outside the calibrated domain. The common
generator currently supports only its established 20-percent TF policy; the
recipe does not pretend to materialize other fractions. Seed is controlled for
reproducibility rather than fitted as an independent cost axis.

Future timing validation must use independently seeded held-out matrices and
intermediate gene sizes (for example 75 and 150), separate from calibration or
reuse sources, with source/image/input identities and estimator/criteria frozen
before measurement. Complete outcome coverage is separate from prediction
accuracy. Timeouts are attempted terminal outcomes, not successful numerical
support, and should never be relabeled as measured finite runtimes.

## Current verification and limits

The dedicated tests exercise parameter domains, exact ID joins and TF ordering,
series boundaries, geometry rejection, unused stationary/technical observations,
float32 arithmetic domains, native source guards, original 3-course/1484-gene
accumulation equivalence, generalized geometry, exact predictor/label values,
resource mappings, fit counts, progress failure paths and canonical export.
The cost-panel tests resolve all 40 profiles through existing machinery and
materialize the largest gene size in each branch without fitting any model.
The 113 dedicated wrapper/core/panel tests pass; the selected shared catalog,
profile, smoke-configuration, thread and seed regression suite passes 67 tests
with one optional-dependency skip.
The normal catalog/preflight/plan/GUI-bootstrap tests exercise standard
eligibility, warning/block, runtime mounts and missing-cost behavior.

The current local image passes all seven common-runner smoke cases: published
defaults, k=1, k=4, alpha=0, alpha=1, seed=17 and optional TF restriction. Two
additional bounded native comparisons (all genes/seed0/thread1 and TF prefix/
seed17/threads2) produce canonical network bytes exactly equal to the original
E. coli implementation, with only its fixed gene count, NumPy scalar API and
explicit reproducibility/resource controls adapted in the independent harness.
All six target fits complete and all six stationary fixture columns remain
unused in both comparisons. The local 1.0.0 image embeds the current wrapper and
shared metadata module; it is not published by these checks.
The current ordinary browser workflow also reaches READY/100 percent and Results
Explorer with no variant selector, using reduced functional rounds and no KO
input. This verifies interaction and output handoff, not runtime prediction.

Historical acceptance evidence for the earlier broader scope is not evidence
that the removed KO workflow remains supported. Functional smoke durations are
not calibration measurements or runtime prediction validation. No full cost or
universal predictive-accuracy claim is made while the panel remains unexecuted.
