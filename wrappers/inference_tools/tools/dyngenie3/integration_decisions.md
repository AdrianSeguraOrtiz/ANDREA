# dynGENIE3 integration decisions

This document records the scientific contract, upstream surface and integration
choices. Runtime installation is defined by `Dockerfile` and `run_tool.py`;
parameters, input capabilities and image reference use the canonical catalog
ToolSpec. The shared [observed bulk temporal contract](../../../../docs/bulk-time-series-contract.md)
applies. Measurements and verification receipts belong to the existing ignored
`release-logs/` output, outside the wrapper source directory.

## Evidence identities and limitations

The supplied article is **Vân Anh Huynh-Thu and Pierre Geurts (2018),
“dynGENIE3: dynamical GENIE3 for the inference of gene networks from time series
expression data”, Scientific Reports 8, 3384**,
DOI [10.1038/s41598-018-21715-0](https://doi.org/10.1038/s41598-018-21715-0).
The local TXT contains the title/authors at lines 6–10, journal/DOI at lines
210 and 290, and paper availability at lines 276–277. The placeholder
“Published: xx xx xxxx” at line 11 does not establish a publication day.

| Evidence ID | Local evidence, relative to this directory | Identity and limitations |
| --- | --- | --- |
| P1 | `papers/article.txt` | SHA-256 `04567262b74bff36b28f35a64c4f381d8363fe796db7d4b98addd2a290e2262e`. Extracted text preserves prose and equation fragments, but figure content and formula layout are incomplete. Supplementary information is referenced at line 783 and was not supplied. |
| P2 | `papers/article.pdf` | SHA-256 `4d8cadb378ade00872bdfcd95646170da55d763c96982df3377b9a7728336a77`. Optional original PDF retained as read-only evidence. No supplement-dependent capabilities are asserted. |
| U1 | `repo/dynGENIE3/README.md` | Lines 11–15 identify three implementations, Python use in all paper results, and sklearn versus the C backends. |
| U2 | `repo/dynGENIE3/dynGENIE3_python/dynGENIE3.py` | SHA-256 `8108e4e8d15809e88ca1eb3c51dd2b260303b00b108e20822f0af84131855e8f`; line references below refer to this pinned, unmodified source. |
| U3 | `repo/dynGENIE3/dynGENIE3_R_C_wrapper/dynGENIE3.R` | R signature at line 45; concatenated training matrices at 260–293; serial/parallel C inference at 303–395. |
| U4 | `repo/dynGENIE3/dynGENIE3_MATLAB/` | Main signature in `dynGENIE3.m:1`, serial loop at 214–230; training and normalized scores in `dynGENIE3_single.m:171–251`. |
| U5 | `repo/dynGENIE3/dynGENIE3_python/dynGENIE3_python_doc.pdf` | Extracted, without modifying the original, to `release-logs/dyngenie3/audit/python-doc.txt`. Tutorial calls the code a research prototype, requires sklearn ≥0.16, and warns that weights have no statistical interpretation. No claim that every newer sklearn version works follows from this old lower bound. |

The supplied clone's origin is `https://github.com/vahuynh/dynGENIE3`; its HEAD
is the user-selected `27eddd960d50e82791a8e0e5f8fcf6d679fe3eac`, with no dirty
or untracked files reported by `git status --porcelain=v1` at audit time. The
commit was authored/committed on 2025-06-23 and merges the NumPy namespace
refactoring. The source/entrypoint inventory is retained in
`release-logs/dyngenie3/audit/source-audit.json`. This is verification of the
supplied pin, not a claim that the GitHub branch can never advance.

The pinned Git tree has no project-level LICENSE/COPYING file. The Python
source/README do not state an explicit redistribution license. C code has an
author notice asking that it not be distributed without permission
(`dynGENIE3_R_C_wrapper/dynGENIE3.c:33–37` and MATLAB
`RT/rtree-c/tree-model.h:2–6`, `tree-multiregr.c:2–6`,
`tree-model.c:2–6`, `rtenslearn_c.c:2–6`). The paper's CC-BY license
(`article.txt:787–796`) is not evidence of a software license. Record software
licensing as unresolved; do not invent MIT/GPL status or a publication receipt.

## Implementation choice and efficiency evidence

Select **Python**, as requested. It exposes the broadest method-level surface,
matches the implementation used for the paper, and permits a directly pinned
Python/sklearn runtime without MATLAB or an R/C compilation layer. This is a
coverage and packaging decision. No cross-language runtime benchmark was run
or supplied, so there is no evidence that Python is the fastest implementation.

| Capability / implementation detail | Python | R/C | MATLAB/C |
| --- | --- | --- | --- |
| Multi-series TS, finite differences and gene-specific decay | Yes: U2:164–209, 395–408, 500–535 | Yes: U3:183–208, 260–283, 540–591 | Yes: `dynGENIE3.m:195–203`; `dynGENIE3_single.m:171–195` |
| Joint TS + steady-state fitting | Yes: U2:537–555 | Yes: U3:285–293 | Yes: `dynGENIE3_single.m:198–205` |
| RF / ExtraTrees, K and tree count | Both: U2:569–581 | Both: U3:240–249, 320–338 | Both: `dynGENIE3_single.m:209–242` |
| Candidate regulators | Gene names; U2:419–423 | Names or indices; U3:216–235 | Indices; main signature |
| Ranking quality: OOB prediction and tree stability | Both; U2:599–648; OOB only RF | No public return for these; U3:398 | No public return for these; main signature |
| Return trained target models / double-KO prediction | `save_models`; U2:655–658; double KO at 667–798 | Not exposed by dynGENIE3 | Not exposed by dynGENIE3 |
| Seed in method-level signature | No; RNG implicit in sklearn / NumPy | `seed=NULL`, `set.seed`; U3:45,176–179 | No seed argument in main API |
| Target-level parallelism | `multiprocessing.Pool`; U2:444–450; processes despite name `nthreads` | foreach/doRNG; U3:352–395 | Serial loop; main:214–230 |
| Tree backend | sklearn compiled trees, U1:15 | Compiled C, U1:15; `.C` U3:333 | Compiled MEX C, `dynGENIE3_single.m:242` |
| Training-matrix preparation | Repeated inside every target call; U2:500–555 | Prepared once for all targets; U3:260–301 | Repeated inside every target call; single:171–205 |
| Constant gene decay handling | Explicit zero if min=max; U2:194–207 | Explicit zero if min=max; U3:573–589 | No equal-extrema branch; `estimate_decay_rates.m:32–41`, may form 0/0 |

Repeated preparation in Python can add allocations and multiprocessing can copy
data; compiled trees and target parallelism can dominate other costs. These
are source-based considerations, not measured speed rankings. R/C and MATLAB
also differ in numerical details and tree defaults: R rounds sqrt(K)
(`dynGENIE3.R:323–324`), while sklearn resolves its own `max_features='sqrt'`.
They are not interchangeable bitwise implementations. The paper's complexity
discussion (`article.txt:294–324`) compares methods, not the three languages;
it does not calibrate this ANDREA runtime.

## Scientific contract and output meaning

dynGENIE3 is a hybrid semi-parametric ODE/tree-ensemble method. For target j,
the implementation uses adjacent observations within each supplied series:

`target = (x_j(t[k+1]) - x_j(t[k])) / (t[k+1] - t[k]) + alpha_j*x_j(t[k])`.

The regression inputs are candidate regulators measured at `t[k]`. Training
samples from independent series are concatenated only after their individual
differences are constructed (`U2:500–535`; paper equations 7–9 and discussion
`article.txt:152–183`). h is fixed to one observed step (`U2:502`), with actual
elapsed intervals in the denominator. There is no public lag search, delay
estimation, interpolation, Gaussian-process derivative or ODE solver option.
An irregular grid is supported mathematically by these explicit intervals;
one observed step is not a fixed elapsed-time lag. The output is one pooled,
directed network, without a native edge-specific lag.

If SS_data is supplied, its target is `alpha_j*x_j(SS)` and its rows precede
the TS-derived samples (`U2:537–555`; paper `article.txt:195–226`). This is
native joint TS/SS fitting, not treating an arbitrary unordered sample as
observed time. TS-only and mixed TS/SS must have separate functional branches.
An SS-only run is not the documented method-level input contract: TS_data is
always required and indexed at `U2:301`; use a separate GENIE3 integration for
a scientifically supported purely steady-state algorithm.

The returned VIM has **source/regulator rows and target columns** after the
transpose (`U2:482`; docstring 277–287). Scores are averaged, unnormalized
within-tree impurity decreases (`U2:9–20,583–586`), then the self score is
zeroed and positive non-self importance sums are normalized **per target**
(`U2:587–592`; paper equation 16, `article.txt:237–266`). Preserve this native
normalization. They are ranking weights, not probabilities, p-values, signed
coefficients, or proof of causal regulation. Direction is supported; sign is
unknown. Do not derive activation/repression from a positive importance.
ANDREA's positive-score edge projection should omit zero scores, preserving
every positive finite magnitude and retaining the full VIM as a native output.
No positive threshold is selected automatically by the article
(`article.txt:115–118`, tutorial extract lines 248–251).

Non-regulators are zero as sources; all genes remain targets. The target's
own expression is allowed as a training feature when it is a candidate, but
its reported self edge is subsequently removed (`U2:527–528,585–592`).
Do not drop self features before fitting: that would change the method.

## Entrypoint and mode audit

All seven top-level Python functions were inventoried from the pinned AST.
The package exposes functions, with no upstream CLI or installable package
metadata in the Git tree. Python names below are exact.

| Public entrypoint / mode | Inputs and output | ANDREA disposition and rationale | Evidence / required verification |
| --- | --- | --- | --- |
| `dynGENIE3`, TS-only | List of matrices [timepoints,genes], matching time vectors; tuple VIM, alphas, quality outputs, models | Main network API; global pooled time-course network. Restrict to declared observed bulk series and preserve boundaries. | U2:216–287,500–535; pending physical TS/RF/ET and temporal-delivery tests |
| `dynGENIE3`, mixed TS+SS | Above plus SS matrix [samples,genes] | Native branch mapped to the optional `steady_state_expression` extra; align the gene axis by ID and fail explicitly on invalid/missing requested input. | U2:236–238,339–347,537–545; pending conditional-input and output tests |
| `estimate_degradation_rates` | TS_data,time_points -> per-gene rates | Invoked by exact `alpha='from_data'` sentinel; persist rates. Separate estimation-only job is not a network output. | U2:164–209,403–404; pending closed-form/constant-gene checks |
| `dynGENIE3_single` | One target plus native TS/SS, alpha, regulator indices and forest options -> target importances/quality/model | Internal target worker called by main API; direct arbitrary target/subset execution excluded because ANDREA requests a whole network. Surface same scientific controls through main API. | U2:500–658; pending forest argument/target orientation tests |
| `wr_dynGENIE3_single` | Positional worker argument list -> target index and single-target result | Internal multiprocessing adapter; no independent scientific workflow. | U2:495–496 |
| `compute_feature_importances` | Trained tree or ensemble -> unnormalized averaged importances | Internal score-preserving helper; supplied estimator objects are not serializable network inputs. Do not replace with sklearn's differently normalized convenience attribute. | U2:9–20,583–584; pending synthetic-estimator score check |
| `get_link_list` | VIM, names, regulator names, optional count/path; writes/prints ranked edges, returns None | Export implemented directly from VIM, retaining native magnitudes; expose `maxcount`. Deterministic ties and omission of zeros replace utility formatting/random zero-tail behavior in the positive-edge projection. | U2:24–161; pending source/target/magnitude/ID round trip |
| `dynGENIE3_predict_doubleKO` | WT vector, retained models, aligned rates/names/regulators, two KO genes, nTimePoints, deltaT -> simulated [nTimePoints+1,genes] | Excluded from network inference runtime: predicts expression, requires interventions/WT/model inputs and a distinct output contract. Saving models preserves future use. No simulation or perturbation-prediction capability claimed. | U2:667–798; paper `article.txt:480–490,641–644` |

Native group/task networks, per-column networks, signed edges, multiple network
contexts, and perturbation-aware causal fitting are absent. Multiple series
share a fitted target model; series labels are boundaries, not native tasks.
`group_emulated` is excluded initially: upstream accepts no groups argument,
and splitting columns without a reviewed whole-series design can change the
scientific interpretation. An all-column matrix by itself is insufficient to
establish biological compatibility.

## Complete parameter traceability

The following is the implementation target; acceptance receipts must verify
the final wrapper names/arguments. Source inputs are mapped inputs rather than
JSON scientific parameters. Tests named below are required evidence categories,
not a statement that an executable test has already passed. Preserve exact
defaults, even if a smoke test uses fewer than 1000 trees.

| Upstream / entrypoint | ANDREA mapping | Type and domain | Default / exact rule | Dependencies | Actual argument / conversion | Disposition | Verification required | Cost relevance | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `TS_data` / main | expression + temporal metadata | Nonempty list of finite matrices; ≥2 distinct times per series; aligned gene axis | Required | Observed-time declarations, boundaries and exact ID join | Transpose gene-row expression into independent [time,genes] arrays; copy because upstream sorts lists in place | Input | Shuffled metadata, unequal grids, boundaries, ID round trip, malformed data | Genes, points per series, series count | U2:223–227,293–321,395–401 |
| `time_points` / main | standardized timepoints + temporal object | One finite numeric vector per TS; unique strictly increasing after sort | Required | Same series/row correspondence as TS_data | Native time vectors for each series; verify float32 conversion remains strictly ordered | Input | Negative/irregular times; duplicate/precision-collapsed times rejected | Transitions and grid structure | U2:226–227,309–321,395–401,526–528 |
| `alpha` / main | `alpha` | `'from_data'`, finite scalar ≥0, or gene-aligned vector ≥0 | `'from_data'` | Explicit vector length = genes; units inverse input time unit | Pass sentinel unchanged; scalar unchanged; JSON list in gene order. No arbitrary fallback | Exposed | Sentinel forwarding, exact exponential estimate, constant gene, vector length/domain | Target preparation/quality branch; scientific fit | U2:229–234,323–337,403–408 |
| `SS_data` / main | `steady_state_expression` extra | Finite [samples,genes] matrix or None | None | Same ordered genes and compatible expression scale as TS | Transpose standardized SS matrix, align by full gene ID, pass None only when absent | Conditional input | Mixed branch, permuted genes, missing/extra genes, invalid matrix | Increases training observations | U2:236–238,339–347,537–545 |
| `gene_names` / main | expression row IDs | Unique nonempty string list of gene length | Upstream None; ANDREA always supplies original IDs | Required for named candidate regulators | Exact input IDs, no renaming | Input | Unusual Unicode/punctuation/numeric-looking IDs | Negligible | U2:240–242,350–354 |
| `regulators` / main | optional `tf_list`; absent -> `'all'` | `'all'` or nonempty matching gene-name list | `'all'` | Auto-deliver TF list when provided; gene names; at least one expressed regulator; record absent TFs | Pass list with expression-order correspondence; no fabricated TF list | Input | Absent/list branches, no intersection, source restriction, retain all targets | Candidate features | U2:244–246,356–365,419–423 |
| `tree_method` / main | `tree_method` | `'RF'` or `'ET'` | `'RF'` | OOB prediction only RF | Literal forwarding | Exposed | Both forest constructors; ET prediction marked unavailable | RF vs ET fit cost | U2:248–250,367–368,575–578 |
| `K` / main | `K` | `'sqrt'`, `'all'`, positive integer | `'sqrt'` resolves using sklearn per split; `'all'` or integer ≥candidate count means all candidates | Candidate count | Native K; compatibility patch maps historical `max_features='auto'` to 1.0 only in all-feature branch | Exposed | sqrt, all, small integer, saturated integer; resolved candidate count | Split search/features | U2:252–254,370–374,569–578 |
| `ntrees` / main | `ntrees` | Positive integer | 1000 | Quality stability needs ≥2 for a defined pairwise score | Literal forwarding; do not silently raise 1 to 2 | Exposed | Default and reduced smoke value; quality undefined case | Linear ensemble growth; quality quadratic pairs | U2:256–258,376–379,648 |
| `compute_quality_scores` / main | `compute_quality_scores` | Boolean | False | RF OOB; both RF/ET stability; degenerate outputs possible | Literal forwarding; persist metric status/reason when undefined | Exposed | RF/ET, SS/TS, constant predictions, ntrees=1, α=0+SS | OOB plus O(ntrees²) stability per target | U2:260–264,429–437,599–648 |
| `save_models` / main | `save_models` | Boolean | False (signature) | Trained models are version-coupled serialized artifacts | Literal forwarding; retain model artifact only when requested | Exposed | False empty result; True serialized count/order metadata | Memory and output volume | U2:216,266–267,439–442,655–658 |
| `nthreads` / main | runtime CPU/worker allocation | Positive integer | Native 1 | Process pool, not an OpenMP thread count | Forward min(assigned threads,target genes), retaining requested/effective allocations; native sklearn workers separately constrained to 1 | Resource | Effective child process count and native pool receipt | Resource stratum | U2:269–271,387–390,444–450 |
| implicit sklearn / NumPy RNG | `seed` | Nonnegative integer seed plus recorded target mapping | Upstream no seed parameter; ANDREA extension default 0 | Forest randomness, stability jitter, pool process start method | Added optional native `random_state=None`; explicit seed derives per-target state using `SeedSequence([seed,target_index])` and seeds forest plus quality jitter | Exposed ANDREA reproducibility extension, not an upstream default | Fresh repeated runs, changed seed, multiworker behavior; None preserves unseeded native behavior | No cost-domain substitution | U2:576–578,639; exact versioned patch to be retained |
| `get_link_list.maxcount` | `maxcount` | `'all'` or integer ≥0 | `'all'`; 0 selects zero positive edges | Applies after positive-edge extraction and deterministic ranking; VIM remains complete | Export from VIM without six-decimal rounding; preserve score precision | Exposed export parameter; negative upstream quirk excluded as invalid contract | all/0/finite count, tied scores, zeros, full native matrix | Export volume; does not reduce fit cost | U2:42–44,85–86,124–126 |
| implicit sklearn constructor defaults | resolved runtime metadata | sklearn defaults for every omitted keyword | Pinned sklearn version governs them | Includes criterion, bootstrap, max_depth, min_samples*, random_state, n_jobs, etc. | Forward no extra science keyword; record estimator.get_params() per variant | Excluded as new parameters: not in public dynGENIE3 signature | Assert only intended kwargs supplied; retain resolved forest config | Important backend/version dependence | U2:575–578 |

The docstrings say positive alpha, but guards reject only values <0; the
data-derived estimator returns zero for constant genes (`U2:181–207`).
The faithful admissible numerical domain includes zero. Do not replace zero
by epsilon. The default rate estimator uses global TS-only `C_min`, clips
`xmin-C_min` and `xmax-C_min` at 1e-6, computes their log difference over the
absolute time separation of first extrema, and takes the maximum across series.
SS_data does not contribute to rate estimation. This is a heuristic, not a
consistent estimate of true biological decay: paper `article.txt:464–474`.

The remaining public utility parameters are individually audited:

| Entrypoint parameters (each exact upstream name) | Default/domain | Disposition and rationale | Evidence |
| --- | --- | --- | --- |
| `compute_feature_importances.estimator` | Required trained sklearn tree/forest object | Internal trained object; no additional external estimator input or hyperparameter hook | U2:9–20 |
| `get_link_list.VIM` | Required square ndarray | Native output matrix, not a second inference input | U2:24,60–66 |
| `get_link_list.gene_names` | None or names | IDs supplied from expression; no rename parameter | U2:34–36,68–72 |
| `get_link_list.regulators` | `'all'` or named subset | Same selected candidates; no independent export-only candidate set | U2:38–40,74–83 |
| `get_link_list.maxcount` | `'all'` or integer; implementation treats 0 as zero and negative as all | Exposed as `maxcount` as specified in the main matrix; ANDREA rejects negatives and limits the positive-edge projection, retaining the complete raw native VIM | U2:42–44,85–86,124–126 |
| `get_link_list.file_name` | None or path | Artifact destination managed by runtime; user paths cannot redirect container output | U2:46–48,128–146 |
| `estimate_degradation_rates.TS_data` | Required matrices | Same validated temporal expression input as main; helper retained for exact default | U2:164–209 |
| `estimate_degradation_rates.time_points` | Required time vectors | Same validated times as main; helper retained for exact default | U2:164–209 |
| `wr_dynGENIE3_single.args` | Required positional worker list | Internal adapter only | U2:495–496 |
| `dynGENIE3_single.TS_data` | Required matrices | Main's validated temporal expression input; duplicate control excluded | U2:500–555 |
| `dynGENIE3_single.time_points` | Required time vectors | Main's validated times; duplicate control excluded | U2:500–535 |
| `dynGENIE3_single.SS_data` | Required positional; may None | Main's validated optional SS input; duplicate control excluded | U2:500,537–555 |
| `dynGENIE3_single.output_idx` | Required target index | Iterate every target in expression order; arbitrary single-target workflow excluded | U2:500,528,587 |
| `dynGENIE3_single.alpha` | Required target rate | Derived from main alpha; no conflicting second value | U2:500,528,541 |
| `dynGENIE3_single.input_idx` | Required candidate indices | Derived from main regulators; no conflicting second list | U2:500,527,540 |
| `dynGENIE3_single.tree_method` | Required, no local default | Main's exposed `tree_method` | U2:500,575–578 |
| `dynGENIE3_single.K` | Required, no local default | Main's exposed `K` | U2:500,569–573 |
| `dynGENIE3_single.ntrees` | Required, no local default | Main's exposed `ntrees` | U2:500,576–578,648 |
| `dynGENIE3_single.compute_quality_scores` | Required Boolean | Main's exposed quality flag | U2:500,564–567,599–648 |
| `dynGENIE3_single.save_models` | Required Boolean | Main's exposed model flag | U2:500,655–658 |
| `dynGENIE3_predict_doubleKO.expr_WT` | Required ndarray vector | Excluded KO prediction workflow needs a WT state input | U2:674,715–718 |
| `dynGENIE3_predict_doubleKO.treeEstimators` | Required list, one model per gene | Excluded KO workflow; future caller may use retained models | U2:676–677,720–721 |
| `dynGENIE3_predict_doubleKO.alpha` | Required scalar/vector ≥0 | Excluded KO workflow; use fitted rates for any future prediction | U2:679–682,723–736 |
| `dynGENIE3_predict_doubleKO.gene_names` | Required aligned names | Excluded KO workflow; retain names alongside models | U2:684–685,738–741 |
| `dynGENIE3_predict_doubleKO.regulators` | Required list or `'all'`, same as training | Excluded KO workflow; model feature contract must match | U2:687–689,775–779 |
| `dynGENIE3_predict_doubleKO.KO1_gene` | Required gene name | Excluded intervention workflow | U2:691,751–752 |
| `dynGENIE3_predict_doubleKO.KO2_gene` | Required distinct gene name | Excluded intervention workflow; same gene twice passes membership guards but remove() then fails | U2:693,754–755,766–768 |
| `dynGENIE3_predict_doubleKO.nTimePoints` | Required integer ≥1 | Excluded KO workflow; returns nTimePoints+1 rows including initial state | U2:695–696,757–758,786–793 |
| `dynGENIE3_predict_doubleKO.deltaT` | Required number; guard permits 0 although docs say positive | Excluded KO workflow; constant Euler prediction interval, not inference lag | U2:698–699,760–761,791–793 |

No upstream function-valued hooks or nested serializable configuration objects
are present in these signatures. The MATLAB RT package additionally contains
generic learners, cross-validation and prediction helpers; they are backend
utilities, not additional public dynGENIE3 network workflows. R's
`get.link.list(weight.matrix,report.max=NULL,threshold=0)` and
`read.expr.matrix(filename,form='',sep='',default.gene.label='gene_',
default.sample.label='sample_')` are export/import utilities of the unselected
R implementation (`U3:448–469,475–536`), not hidden Python options. Its `seed`,
`verbose` and `ncores` are R API differences, not absent Python scientific
parameters. Runtime logging/resource/seed behavior is specified independently.

## Compatibility patches, numerical edge cases and repeatability

The exact patch and its hash must be retained alongside the public-source
receipt. Never modify the supplied clone. Review the installed patched copy
against the pin before functional acceptance.

| Finding in pinned Python | Required behavior / impact | Source evidence / regression required |
| --- | --- | --- |
| `from sklearn.tree import BaseDecisionTree` uses an export unavailable in modern sklearn | Import the pinned modern base class via its versioned location, or avoid the fragile type check with equivalent tree detection; no change to importance formula | U2:1,14–20; import smoke + synthetic estimator importance check |
| Historical `max_features='auto'` on all-candidate branch | Replace with 1.0 for regressor all-feature semantics; retain `'sqrt'` and smaller integer K | U2:569–578; inspect constructors for all/saturated K |
| Latest refactor changed former scipy `pearsonr` calls to nonexistent `np.pearsonr` | Restore `scipy.stats.pearsonr` for the optional RF prediction score. Core forest/VIM path is unaffected when quality=False | U2:620,626; pinned git history `56ac69c`; controlled Pearson regression |
| ndarray alpha compared with string sentinel | Use explicit string detection in copied implementation, or pass JSON list so comparisons are scalar. Preserve vector values and estimation branch | U2:323,403; scalar/list/ndarray audit |
| Upstream sorts times to float32 and mutates input lists | Copy inputs, sort within series, preserve original metadata; reject any strict time order that collapses in native dtype unless a separately justified float64 patch is chosen | U2:395–401; negative/large/close times and mutated-input regression |
| Implicit RNG + multiprocessing fork copies process state; no `random_state` argument | Record the adapter strategy and per-target seeds, use fresh worker-independent seed streams if parallel, retain actual pools/start method; do not claim unmeasured cross-hardware reproducibility | U2:444–450,575–578,639; fixed-image/seed/resources repeated runs pending |
| Pool is not explicitly closed/joined in main | Adapter may use a managed pool/worker lifecycle; verify no surviving workers. Scientific formula unaffected | U2:449–450; process cleanup check |
| ntrees=1 yields no tree pairs; constant OOB predictions give undefined Pearson; alpha=0 in SS OOB inverse divides by zero | Preserve primary network, persist unavailable diagnostic with reason; avoid silent scientific substitutions or relabeling undefined as zero | U2:611,620,626,648; edge-case metric tests |
| Stability adds random jitter before zero-importance check; always divides pairwise intersection by fixed 5 | Preserve metric's actual semantics. With <5 nonself candidates its maximum is <1, and zero-importance trees can still get an arbitrary jitter ranking | U2:503,632–653; document candidate count, do not advertise calibrated confidence |
| No finite/unique/time-unit checks and weak rank/shape guards | Validate finite expression/time/rates, unique IDs, nonempty series and positive intervals before native call | U2:293–390,526–528; malformed input matrix required |

Quality scores are exploratory diagnostics, not independent network validation
or an automatic alpha tuning procedure. Both the paper
(`article.txt:654–660`) and tutorial (extract lines 155–159) explain that high
prediction/stability does not reliably select the best biological network on
real data. Retain unavailable values as JSON null with reasons, never NaN in a
strict JSON artifact. Changes to sklearn defaults/version can change the
forest and weights even when these compatibility fixes preserve the intended
formula; the dependency pin is part of method identity.

## Observed bulk time-series mapping

Require explicit observed time, one declared unit/origin, sampling design,
normalization/transformation provenance and exact joins from metadata IDs to
expression columns. Accept regular and irregular intervals inside a series;
sort only inside each series. Distinct conditions/perturbation experiments
retain distinct series identifiers. Pooling their valid samples fits one
common model; it does not claim condition-specific networks. Alpha values
must use inverse declared time units. Changing time units while keeping an
explicit numeric alpha unchanged changes the model.

The bulk audit's longitudinal metadata directly identifies permissible
adjacency. Independent destructive/cohort samples across visits are not
biologically paired trajectories; their measurements must not be linked by
file order or guessed replicate names. Any mean-trajectory transformation
requires an explicit, tested policy and provenance. Technical replicate
averaging likewise must be declared; technical columns are not extra time
points. This integration must reject unsupported sampling/replicate branches
until that preprocessing contract exists. No automatic interpolation,
imputation, scale normalization or expression transformation is native here.
Missing visits use actual observed intervals; missing expression values are
rejected. Do not infer units from numeric time or series structure from ID
substrings. Single-cell pseudotime is discussed as future work in the paper
(`article.txt:669–674`), so it is not established bulk observed-time evidence.

The selected declaration is `dataset.temporal` with
`schema_version='bulk-temporal/1'`; its network request requires
`temporal_requirements={required:true,sampling_designs:['longitudinal'],technical_replicates:'reject',min_timepoints_per_series:2}`.
The wrapper receives the same declaration in `/io/execution.json.temporal`.
Only longitudinal designs are initially enabled, with missing values rejected,
an observed time basis, declared units/origin, preprocessing declaration and
provenance. Technical duplicate visits are rejected rather than averaged.

The versioned temporal object is implemented through dataset parsing, core mounts,
frozen bundles, generation/inference handoff, CLI and GUI. Targeted tests and
the browser/preflight evidence cover the new contract, while physical
dynGENIE3 execution remains a separate pending gate. Passing the original
offline checker alone would be insufficient. `timepoints` and `replicates`
remain the shared extra IDs;
`SS_data` maps to the optional gene-aligned `steady_state_expression` extra.
No lag-output extension is required for this method because it outputs no
estimated edge-specific lag. The one-step derivative approximation must remain
described in native config/provenance rather than encoded as a fabricated
edge lag or network context.

## Installation and compatibility

The Dockerfile pins the Python 3.11 Linux/amd64 base by digest and installs
NumPy 1.26.4, SciPy 1.12.0, scikit-learn 1.4.2, joblib 1.4.2 and
threadpoolctl 3.5.0 with exact binary-wheel hashes. Those pins live directly in
the Dockerfile; no separate requirements file is needed.

During image construction, `run_tool.py --prepare-upstream` downloads the
public Python source at commit `27eddd960d50e82791a8e0e5f8fcf6d679fe3eac`,
checks its SHA-256 and applies the reviewed compatibility, diagnostic and
per-target seed patches defined in the same file. Normal inference performs
no download or patching. The patched native source is byte-identical to the
previous reviewed patch output. The supplied clone and papers are analysis
evidence and are excluded by the common temporary-context image builder.

`--threads` controls the outer target-worker pool, bounded by target count.
Each forest and native math library uses one thread. The seed extension
assigns a deterministic seed to each target independently of scheduling.
Undefined optional diagnostic scores remain null with an explanation; they
are not replaced with zero or allowed to alter network importance values.

The first unpublished image uses version `1.0.0`. Corrections before its stable
release retain that version. Actual verification and cost measurements always
identify the immutable image, regardless of its mutable local tag.

## Verification and finite cost coverage

The canonical smoketest configuration is
`../../tests/smoketest_configs/dyngenie3.json`. Method-contract and patch tests
live with the other inference wrapper tests. Physical verification additionally
covers TS-only/mixed input, RF/ET, scalar/vector/data-derived decay, K variants,
TF restrictions, quality diagnostics, saved models, empty networks, metadata
validation, fixed-seed repeats, changed seeds and actual 1/2/4/8-worker limits.

The canonical cost recipe is `../../cost_profiles/dyngenie3.json`. Its finite
panel contains 81 scientific coordinates, each measured with 1/2/4/8 CPUs,
4/8 GiB RAM and three repeats: 1,944 calibration runs. Genes, timepoints per
series and independent series have crossed default coverage; additional
panels cover ET, candidate TFs, scalar/vector decay, quality, models, K, tree
counts, steady-state counts and irregular sampling. This does not establish
coverage of every combination of inputs and parameters.

Thirteen reserved cases at gene sizes 75/250 provide 312 independent runtime
observations across those resource strata. Predictions are frozen calls to
the real planner before held-out execution. Held-out observations must remain
separate from model fitting. Temporal-shape interpolation, unmeasured TF
densities and unreserved parameter combinations remain outside the predictive
claim. The complete campaign also includes 26 functional image runs and one
real core/CLI execution, totaling 2,283 planned executions.

Previous candidate-image measurements remain historical evidence. Moving the
patch and dependency pins into their final files changes the build context
and image identity, so the final image is verified and calibrated afresh.
Do not label costs complete before structural validation, declared coverage
and independent prediction accuracy all pass. Registry publication remains a
separate action; no image has been published by this integration workflow.
