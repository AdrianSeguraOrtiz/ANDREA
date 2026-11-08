# BiXGBoost integration decisions

This integration mirrors the public `mainRun` workflow. Its standard wrapper
files are `Dockerfile`, `run_tool.py` and this decision log. The supplied
`papers/` and `repo/` remain read-only scientific evidence and are excluded from
build contexts. The integration is active in the ordinary catalog following
promotion on 2026-11-05. Candidate evidence, physical test reports and the
promotion receipt remain under `release-logs/inference-bixgboost/`; the active
catalog CLI run is retained under `release-logs/recovery-20261105/`.

As reviewed on 2026-11-06, no BiXGBoost cost calibration has executed and no
`cost.json` exists. Its proposed calibration queue was stopped before its first
BiXGBoost run. All related PM2 entries and launch configurations are retired;
independent prediction-accuracy checks are deferred. Functional fixture results
below do not establish cost coverage, estimation accuracy or full GUI acceptance.

## Evidence and implementation identity

The supplied article is Ruiqing Zheng, Min Li, Xiang Chen, Fang-Xiang Wu,
Yi Pan and Jianxin Wang, **“BiXGBoost: a scalable, flexible boosting-based
method for reconstructing gene regulatory networks”**, Bioinformatics
35(11), 1893–1900 (2019), DOI
[10.1093/bioinformatics/bty908](https://doi.org/10.1093/bioinformatics/bty908).
Its advance online date is 5 November 2018; the volume year is 2019. The
extracted article TXT has SHA-256
`bcd7316d05c09f8f7fec50b3cab71a9b4e72e5fab8c53e48c485c7651525ce5c`.
The paper's formula extraction is imperfect and the supplementary material was
not supplied; claims below follow inspected executable code where the paper and
implementation differ. Benchmark performance in the article is not evidence of
the predictive accuracy of this particular integrated runtime.

The upstream implementation is
[zrq0123/BiXGBoost](https://github.com/zrq0123/BiXGBoost), pinned to the user's
commit `e01c00dda5d447e68d476d82b51bccb54f2f3c23`. The supplied evidence clone
is clean at that commit. Its `BiXGBoost.py` has SHA-256
`722f62742d0536d6f57793b95c93d3e6f17c6036b2a68c93b52f7b7f669be25b`.
The wrapper downloads that exact public file, verifies its checksum, retains an
unaltered copy and applies guarded compatibility patches during construction.
It never executes code from the local evidence cache. The source repository
does not contain a standalone license file; repository availability must not be
described as a particular license grant.

README declares Python3.5 and **XGBoost0.6**. No installable BiXGBoost package
or independent package metadata is present in the supplied repository. The
selected installation therefore uses the exact BiXGBoost source and the
official XGBoost `v0.60` source commit
`4a8d63b6c8711fb839c71e26c659936252df1eb5`, including its fixed `dmlc-core` and
`rabit` submodule commits. Source/dependency hashes and the base image digest
belong in the Dockerfile, rather than an extra lock file. The image receipt must
record the immutable built image ID; the initial unpublished tag remains1.0.0.

The XGBoost version is scientific, not a replaceable installation detail:
`v0.60` `feature_importances_` uses normalized split counts (`get_fscore`,
**weight**) in float32. A modern XGBoost default may instead use gain and would
change the network scores. This integration retains the original0.6 native
kernel, default squared-error objective `reg:linear`, and default tree-method
selection. It does not silently substitute a modern estimator or force a
different large-data tree method.

## Public entrypoints and normalized input inventory

| Upstream surface | Exposure and mapping |
| --- | --- |
| `mainRun(expressionFile,samples,outputfile,p_lambda=0,p_alpha=1,timelag=2)` | The selected public global inference workflow, retaining two fitted models for every gene and the original score combination. |
| `single_col_timeseries`, `invert_expression_timeseries`, `getlinks`, `compute_feature_importances` | Internal pieces of the same workflow; called through the verified source, not exposed as independent inference modes. |
| `get_Goldset` | Benchmark evaluation helper; unused in network construction and excluded from inference inputs. |
| DREAM10/DREAM100/Ecoil data and `example.py` | Demonstration/evaluation material, not another inference backend or execution capability. |
| README `maxlag` argument | Documentation error: `mainRun` has only `timelag`; no invented `maxlag` parameter or alias is accepted. |

The semantic inventory finds a complete match with existing normalized inputs:
`expression.tsv` is genes by observations and `column_descriptors.tsv` describes
observation roles, independent courses, biological/technical identities,
conditions, observed times, units and sampling design. No input type, GUI form,
selector-dependent schema or separate replicate/timepoint/stationary matrix is
introduced. `samples` is the number of independent complete temporal series
derived from these descriptors; asking the user to duplicate it could silently
split the matrix at incorrect biological boundaries.

Only `global` execution is native. The method does not natively accept an
explicit TF candidate list, groups, multimodal observations, pseudotime or
steady-state-only data. All genes remain predictors and responses. In a mixed
normalized matrix, stationary columns remain in the supplied expression matrix;
their names are recorded as unused in resolved provenance, and their values are
excluded before target normalization or fitting. This
is the same role-based selection used for a temporal-only method, not a claim
that BiXGBoost incorporates stationary observations into its model. Native
grouped output and signed activation/repression are not claimed.

## Temporal geometry and scientific validation

Each independent course is longitudinal, has one condition and biological
replicate, and contains `T > timelag` unique strictly increasing observed times.
The native default lag2 therefore needs at least three timepoints per course;
the ToolSpec minimum2 permits lag1 when pooled rows have sufficient variance.
This parameter-dependent check occurs inside preparation for the requested
execution, not during catalog classification.
All courses must have equal `T` and one common positive regular sampling
interval in the declared common unit. Series may have different absolute time
origins: the native kernel uses integer positions, not absolute timestamps, so
the wrapper neither requires identical absolute grids nor shifts the supplied
coordinates. Regularity uses the existing policy `math.isclose` with relative
1e-9 and absolute1e-12 tolerances. Irregular or unequal courses are rejected;
there is no interpolation, truncation, imputation or implicit averaging.

Time-series columns are sorted by exact series ID and observed time into
separate complete contiguous blocks before calling upstream with
`samples=n_series`. Its `single_col_timeseries` slices inside each block and
concatenates the resulting rows, so no lag crosses a biological boundary.
For `G` genes, lag `p` and `R` series, each of the2G models receives
`R*(T-p)` rows and `G*p` predictors. At least two pooled lagged rows and finite
positive target variance are required independently for each gene's incoming
and outgoing targets. These checks preserve the original population-standard-
deviation normalization; a constant or numerically invalid target is rejected
instead of being assigned a fabricated score.

Technical measurements sharing an observation ID are rejected for the selected
temporal rows. The wrapper does not average them, infer longitudinal pairing
from independent destructive samples, or construct transitions between series.
The stable descriptor parser supplies exact joins and verifies the common time
unit and sampling design. All expression entries must be finite.

These requirements belong to the requested method's ToolSpec/wrapper. They do
not create file-content-based catalog filtering, new shared rule syntax or
global requirements for other ANDREA tools.

## Parameter traceability and fixed native model

| Upstream | ANDREA | Domain/default | Native argument or rule | Coverage/cost role |
| --- | --- | --- | --- | --- |
| `expressionFile` | `expression.tsv` + `column_descriptors.tsv` | Required normalized files | Exact float64 values transposed into alias-gene columns after temporal role selection; pandas `float_precision=round_trip` preserves written values | Sorting, mixed roles, opaque IDs, malformed metadata and dimensional checks |
| `samples` | Derived, not a user parameter | Number of complete series | Passed as native `samples`; equal course lengths checked before slicing | Independent series count changes pooled training rows |
| `outputfile` | Raw native network under provenance | Fixed output path | Upstream `to_csv` remains intact; returned DataFrame drives canonical export | Native and canonical scores compared |
| `p_lambda` | `p_lambda` | Finite number>=0; default0 | `XGBRegressor(reg_lambda=p_lambda)` for both directions; L2 regularization | Zero/default and positive values; affects tree structure/cost |
| `p_alpha` | `p_alpha` | Finite number>=0; default1 | `XGBRegressor(reg_alpha=p_alpha)` for both directions; L1 regularization | Default and zero/positive changes; affects tree structure/cost |
| `timelag` | `timelag` | Integer>=1; default2 | Maximum positive integer positional lag, preserving every lag1..p | Boundary lag1, multiple lags, invalid geometry; predictor dimension/cost |
| Internal `random.randint(1,100)` seed | `seed` | Integer0..2^31-1; default0 | ANDREA reproducibility extension: the selected value replaces the one random native seed and is passed unchanged to all2G estimators | Fresh-run repeated/changed seeds; no claim of hardware-independent determinism |
| Native XGB `nthread=-1` default | Runtime `--threads` | Positive resource budget | `nthread=threads` on both model constructors; targets/models remain sequential | Actual native pools, resolved model controls and cgroup receipts |

The complete public serializable scientific surface is the three `mainRun`
parameters. The supplied code fixes `max_depth=3`, `n_estimators=1000`,
`learning_rate=.0001`, `subsample=.8`, `colsample_bylevel=.6` and
`colsample_bytree=.6` in both constructors. These constants remain fixed and
are recorded in resolved provenance; they are not silently reduced for smoke
tests or converted into unsupported new method parameters. Remaining
XGBoost0.6 estimator defaults, including base_score=.5 and objective reg:linear,
are retained. Runtime resources are separate from scientific parameters.
Unknown keys, including README `maxlag`, redundant `samples` and modern
estimator options, fail explicitly.

## Score meaning, identifiers and native evidence

For each gene the native incoming model predicts its later expression from
earlier lagged expression of all genes. The outgoing model predicts its earlier
expression using later lagged expression. Each target vector is centered and
scaled by its population standard deviation (`ddof=0`) separately; predictors
remain unscaled. Self-gene predictors stay in both fitted models, as upstream
requires, while self interactions are removed by native `getlinks`.

Within each direction the maximum normalized weight importance across that
gene pair's lagged predictors is retained. Each direction's links are weighted
by `1 - gene_training_MSE / sum(all_gene_training_MSE)` using that direction's
own errors. Incoming links are weighted by the response/target model and
outgoing links by the source model. The final directed confidence is the
geometric mean `sqrt(weighted_in * weighted_out)`. These raw magnitudes are
preserved. The paper selects the bidirectional result when the correlation
between local-in and local-out scores is greater than0.5, and otherwise selects
the local-in result. This fallback is absent from the pinned implementation;
the wrapper always preserves its geometric-mean result. The paper does not
specify that fallback as a repair for zero split counts or undefined importance.

Native internal feature names append an underscore and lag, and `getlinks`
splits at the first underscore. Reversible internal aliases with no underscore
prevent gene IDs containing underscores, Unicode, spaces or numeric strings
from being truncated or interpreted. `resolved.json` records the alias-to-
original mapping; canonical `network.csv` restores the original IDs exactly.
The native output's tab-containing interaction index and its original escape
characters are retained as raw evidence; canonical export uses the returned
DataFrame directly, avoiding a lossy ad hoc split of the physical TSV.

Canonical output is `source,target,score,sign,evidence,context`, directed,
unsigned, `sign=?`, `evidence=association`, `context=global`. Only positive
finite non-self confidences are emitted, ordered by descending score and exact
IDs for deterministic ties; zero confidence remains in native auxiliary data.
No confidence is described as probability, sign, a chosen biological lag or
proven experimental causality. Nonfinite native scores, missing/duplicate
interactions or a degenerate error normalization fail explicitly. Strong L1
regularization can leave a fitted model with no splits; the historical
normalized split-count property then divides by zero. That undefined importance
is an execution failure, not a reason to fabricate zeros, substitute gain or
claim a successful empty network.

Auxiliary provenance contains the exact native expression, native network,
incoming/outgoing/final score columns, resolved input roles/series/times/
effective dimensions/aliases/parameters/input hashes, per-model training MSE,
actual fitted tree count and native pool/resource observations, package/source
identity, and the emitted progress event journal. Native future-feature name
suffixes are retained rather than relabeled as physical lag estimates: the
upstream outgoing `pan=1` naming convention can include suffix0 even though the
actual future offsets are1..p.

## Compatibility patches and runtime observation

All BiXGBoost compatibility changes are source-checksummed and embedded in
`run_tool.py`: replace removed pandas `Series.append` with the same ordered
concat; provide an explicit float Series dtype; use keyword `sep` and round-trip
float parsing; explicitly convert error lists to NumPy arrays before the same
elementwise weight formula; supply ANDREA seed and native thread budget; observe
each completed model fit and its MSE; return the unchanged final DataFrame for
lossless export. The untouched original source and patched source are both
retained in the image. No network-scoring formula or lag orientation changes.

XGBoost0.6 is compiled unchanged with the GCC forced include for the missing
standard C++ `<functional>` header. Its Python API requires modern import
adaptations for `sklearn.cross_validation` and `collections.Mapping`; exact
file hashes guard those small compatibility patches. They do not replace the
native booster or modify its importance calculation. A further upstream
inconsistency is explicit: the declared0.6 dependency defines
`feature_importances_` on `XGBClassifier` but not `XGBRegressor`, although
BiXGBoost reads it from the regressor. The compatibility patch shares the exact
original classifier property with the regressor, using the same native booster
and normalized float32 weight calculation. The unpatched declared combination
is not described as executable. Modern setuptools also requires a relative
native-library installation path; copying the compiled library into the package
and using that relative path changes packaging only. Original and patched
Python-source hashes plus the compiled library hash are retained in the image.

Progress counts **completed model fits**, total2G. It starts before the first
fit, increments only after the native fit and its training-error calculation,
then reports export and successful completion. Phase percentages are milestones
and observed work fractions, not elapsed-time estimates. A fit failure retains
the completed count and a partial percentage, never reports100 or success.
Native `nthread` receives the ANDREA budget; BLAS pools stay serial and no
target-level multiprocessing is introduced. Resource receipts distinguish the
configured native control, actual fitted tree counts and observed pool state
from assigned Docker quotas. They do not claim continuous use of every CPU.

## Acceptance and costs

Unit coverage exercises source guards, native aggregation/lag semantics,
parameter domains/defaults, mixed observation roles, series ordering and
boundaries, shifted origins, geometry/variance failures, identifier round trips,
resource mapping, intermediate progress and failure state. Docker functional,
fresh-seed repeatability and selected core/CLI checks use the candidate image
and the standard smoke matrix. Machine-generated receipts belong in the
existing ignored campaign area and determine the final verified state.

Functional verification on 2026-10-23 used immutable image
`sha256:9fa1185f2d73bb2ac0efa873b8b9a881cfda3fb99416a3389eef0f850639b9aa`:
six standard Docker variants, eleven physical acceptance cases and the complete
CLI pipeline passed. An independent execution of the original BiXGBoost source
with only the documented API repairs and matching seed/resources reproduced
all three score columns and every model's training error exactly on the tiny
fixture (maximum absolute score difference0). Repeated seeds and changed unused
stationary values preserved the network; changing the seed changed it. With
two allotted Docker CPUs, model `nthread` and the native XGBoost OpenMP pool
were both2 while BLAS pools stayed1. Docker inspection verified CPU/RAM limits;
the wrapper's optional cgroup-v2 observations were empty on this cgroup-v1 host.
An isolated candidate GUI showed the catalog entry, parameters and file contract
without running inference. This is not an active-catalog browser inference test.
The ordinary active-catalog CLI did complete a small fixture on 2026-11-05.
These checks establish the tested functional behavior and environment, not
biological accuracy, cost accuracy or acceptance of every GUI workflow.

Activation completed on 2026-11-05 after preserving the preceding campaign's
exact measured sources. Promotion checked source, image and destination hashes,
copied the standard integration files and added the central template-map entry.
The promotion receipt records those historical bytes. This 2026-11-06 editorial
status correction changes neither the native wrapper nor its Docker inputs.

Cost calibration is not scheduled or running after the 2026-11-06 cleanup.
There is no guessed `cost.json`. The registered recipe at
`wrappers/inference_tools/cost_profiles/bixgboost.json` is a prospective panel:
27 profiles in11 families,81 registered points per resource stratum, and
CPU counts1/2/4 crossed with RAM limits8/32GiB and three repetitions, giving
1,458 planned calibration executions. Defaults independently cross genes
50/100/200, timepoints4/8/16 and series1/2/4. Additional branches register
lags1/3/5, L2 values0.1/1/10, L1 values0/0.1/2, and the explicit combination
lambda1/alpha0.1/lag3. The native defaults remain lambda0/alpha1/lag2.

Every registered input is regular longitudinal bulk time series with no TF
restriction or stationary fitting. The fixed1000 trees, depth3, learning
rate0.0001 and native sampling fractions are part of every measurement.
The alpha2 branch replaces an analytically degenerate alpha10 proposal at
T8/S2/lag2: its12 standardized target rows cannot supply a gradient sum above
the native L1 threshold10, so that proposal could not produce valid normalized
split counts. Alpha2 is a selected prospective value, not a claim that all
expression distributions yield nondegenerate fits.

The existing plan-only command resolves the complete matrix without fitting,
and the temporal coverage validator correctly rejects zero retained timings.
Independent prediction validation remains pending. Its prospective reservation
uses genes75/150 at every profile shape/resource stratum with three repetitions
(972 runs); source/model/predictions, independent input seeds and acceptance
criteria must be frozen before collecting those measurements. Registered
parameter values and temporal coordinates define the intended finite domain;
they do not establish universal accuracy, arbitrary parameter interpolation or
coverage of unmeasured combinations. No cost calibration or held-out timing has
been executed for BiXGBoost; the planned 1,458-run calibration was stopped
before the first BiXGBoost fit.
