# ppcor Integration Decisions

## Sources Reviewed

- Upstream/package mirror: `wrappers/inference_tools/tools/ppcor/repo/ppcor/`
- CRAN manual supplied by integrator:
  `wrappers/inference_tools/tools/ppcor/additional_files/ppcor.txt`
- Local paper:
  - `wrappers/inference_tools/tools/ppcor/papers/PPCOR.pdf`
  - extracted helper text:
    `wrappers/inference_tools/tools/ppcor/papers/PPCOR.txt`
- Installation/version check:
  - `https://cran.r-project.org/web/packages/ppcor/index.html`

## Paper Preparation

- PDF inputs found: `PPCOR.pdf`.
- Extracted text used for analysis: `PPCOR.txt`.
- Extraction quality: usable for method, DOI, authorship, examples,
  entrypoint semantics and output fields. Mathematical formulae are partially
  mangled by PDF extraction, but the prose and R examples are clear.

## Selected Contract

The wrapper mirrors this public R entrypoint:

```r
ppcor::pcor(x, method = c("pearson", "kendall", "spearman"))
```

The input `x` is the normalized ANDREA expression matrix transposed to rows as
expression columns/observations and columns as expression genes/variables.
`pcor()` returns full pairwise partial-correlation, p-value and statistic
matrices. The public ANDREA network uses the signed partial-correlation
coefficient matrix.

`spcor()` is not included in the first wrapper contract because it returns a
semi-partial matrix that is not generally symmetric: the effect of the
controlled variables is removed from one side only. Mixing it with `pcor()` in
one ToolSpec would make `outputs.directed` parameter-dependent. It can be added
later as a separate contract if ANDREA needs semi-partial directed/asymmetric
association output.

## Upstream Interface Audit

### Public execution modes / entrypoints

- `ppcor::pcor(x, method=...)`
  - Evidence:
    - `repo/ppcor/man/pcor.Rd` says `pcor` calculates pairwise partial
      correlations for each pair of variables given others and returns
      `estimate`, `p.value`, `statistic`, `n`, `gn` and `method`.
    - `repo/ppcor/R/ppcor_v1.01.R` implements `pcor()` by computing a
      covariance matrix, inverting it or using `MASS::ginv()` when singular,
      then deriving `-cov2cor(icvx)` and p-values/statistics.
    - `papers/PPCOR.txt` describes `pcor()` as calculating all pairwise
      partial correlations of a matrix/data frame and reporting p-values and
      statistics.
  - ANDREA mapping:
    - `global`: one `pcor()` run over all expression columns.
    - `group_emulated`: ANDREA partitions expression columns by `groups` and
      runs the same `pcor()` contract once per group.
  - Not `group_native`: `pcor()` accepts only one matrix/data frame and no
    group-label argument.
  - Not `column_native`: `pcor()` produces one aggregate gene-gene matrix, not
    one network per input column.
  - Not `group_aggregated`: no native per-column network is available to
    aggregate.

- `ppcor::spcor(x, method=...)`
  - Evidence:
    - `repo/ppcor/man/spcor.Rd` says `spcor` calculates pairwise semi-partial
      correlations for each pair of variables given others.
    - `papers/PPCOR.txt` states semi-partial correlation removes the effect of
      other variables from one of the two variables, and the printed example
      matrix is asymmetric.
  - Decision: excluded from this wrapper contract because it has asymmetric
    score semantics while the selected `pcor()` contract is undirected and
    symmetric.

- `ppcor::pcor.test(x, y, z, method=...)`
  - Evidence:
    - `repo/ppcor/man/pcor.test.Rd` documents a pairwise partial-correlation
      test for two vectors and one or more control variables.
    - `papers/PPCOR.txt` presents `pcor.test()` for a single selected pair.
  - Decision: excluded. It is a pair helper for one edge at a time; ANDREA
    needs a full network, and `pcor()` is the documented matrix entrypoint.

- `ppcor::spcor.test(x, y, z, method=...)`
  - Evidence:
    - `repo/ppcor/man/spcor.test.Rd` documents a pairwise semi-partial
      correlation test for two vectors and one or more control variables.
  - Decision: excluded for the same reasons as `spcor()` and `pcor.test()`.

## Required Inputs

- Expression matrix: implicit ANDREA normalized expression matrix.
  - Evidence:
    - `pcor.Rd` requires `x` to be a matrix or data frame.
    - `repo/ppcor/R/ppcor_v1.01.R` coerces data frames to matrices and requires
      numeric or logical atomic data.
    - The paper examples use rows as samples and columns as variables.
  - Implemented wrapper rule: read ANDREA `expression.tsv` with genes as rows
    and expression columns as observations, transpose it, and call `pcor()` so
    genes become ppcor variables.

## Optional or Conditional Inputs

- `groups`: conditional for `execution.mode=group_emulated`.
  - Evidence:
    - Existing normalized input `groups` maps expression column ids to group
      labels.
    - ppcor has no native group argument, so group-emulated execution is owned
      by ANDREA orchestration.
  - Decision: declare `groups` as `conditional_required` only for
    `group_emulated`.

- No optional upstream inputs are declared for the selected `pcor()` contract.
- No new input specs are required.

## Parameters and Defaults

- `method`
  - Chosen value: enum `["pearson", "kendall", "spearman"]`, default
    `pearson`.
  - Evidence:
    - `pcor.Rd` usage is `pcor(x, method = c("pearson", "kendall",
      "spearman"))`.
    - `repo/ppcor/R/ppcor_v1.01.R` uses `method <- match.arg(method)`, which
      selects the first value, `pearson`, when omitted.
    - `papers/PPCOR.txt` states the package provides Pearson, Kendall and
      Spearman correlation methods.
  - Rationale: this is the only selected public `pcor()` argument that changes
    the statistical coefficient.
  - Uncertainty: none.

- Not exposed as params:
  - `x`: mapped from normalized expression.
  - Any threshold or top-k limit: not an upstream `pcor()` parameter and would
    be an ANDREA export decision.
  - `threads`, workers, cores or BLAS controls: runtime resources, not
    user-facing method parameters.

## Data-Dependent Runtime Rules

- Singular covariance handling:
  - Evidence:
    - `pcor.Rd` says when the determinant of the variance-covariance matrix is
      numerically zero, Moore-Penrose generalized inverse is used, and no
      p-value/statistic is provided if the number of variables is greater than
      or equal to sample size.
    - `repo/ppcor/R/ppcor_v1.01.R` checks `det(cvx) < .Machine$double.eps` and
      calls `MASS::ginv(cvx)`.
  - Preservation rule: the wrapper calls `ppcor::pcor()` directly and does not
    reimplement or precondition the covariance inversion. The raw p-value and
    statistic matrices are preserved as auxiliary artifacts even when
    they contain missing values.

- Missing values:
  - Evidence: `pcor.Rd` note says missing values are not allowed.
  - Preservation rule: wrapper fails clearly on missing/non-finite
    expression values rather than imputing.

## Output Mapping to `network.csv`

- Upstream raw output:
  - `pcor()` returns a signed symmetric `estimate` matrix of partial
    correlation coefficients, plus p-value and statistic matrices.
  - Coefficients are correlation values; their scale is already defined by the
    upstream method.
- ANDREA mapping:
  - `source`, `target`: exact ANDREA expression gene ids.
  - Edge convention: one row per unordered gene pair; exclude self-loops.
  - `score`: `abs(partial_correlation)` as the raw positive magnitude.
  - `sign`: `+` when the coefficient is positive, `-` when negative.
  - `evidence`: `association`.
  - `context`: `global` for direct wrapper output; `group:<group_id>` after
    ANDREA group-emulated orchestration.
- Filtering:
  - Do not write rows with `score <= 0` or non-finite coefficients.
  - No ANDREA-specific score normalization is applied in the wrapper.
- Identifier preservation:
  - R matrix column names preserve exact expression gene ids for the selected
    `pcor()` runtime path. No upstream aliases are needed for the implemented
    wrapper.

## Runtime Resource Mapping

- ToolSpec: `runtime_resources.threading.supported=false`,
  `default_threads=1`, `max_threads=1`.
- Evidence:
  - Public functions in `NAMESPACE` are exported R functions, with no CLI or
    scheduler.
  - `pcor.Rd`, `spcor.Rd`, `pcor.test.Rd` and `spcor.test.Rd` expose no
    workers, cores, jobs or thread controls.
  - `repo/ppcor/R/ppcor_v1.01.R` uses base matrix operations plus
    `MASS::ginv()` and no foreach, parallel, future, scheduler, process pool
    or independent work-unit sharding.
- Decision:
  - ANDREA plans ppcor as single-threaded.
  - The wrapper requires `--threads=1` and pins common
    BLAS/OpenMP environment variables to 1 to avoid hidden oversubscription.
- Uncertainty:
  - R's BLAS implementation may internally use threads in some environments,
    but ppcor exposes no safe package-level control to map ANDREA threads.

## Installation Strategy

- Preferred public installation source:
  - CRAN package `ppcor` version `1.1`.
- Evidence:
  - CRAN page reports Version `1.1`, Depends `R (>= 2.6.0), MASS`,
    NeedsCompilation `no`, and package source `ppcor_1.1.tar.gz`.
  - `repo/ppcor/DESCRIPTION` also records Version `1.1`, Repository `CRAN`,
    Date/Publication `2015-12-03`.
  - Manual clarification identifies the CRAN package and version 1.1.
- Implemented install:
  - Dockerfile installs from the pinned CRAN source tarball
    `https://cran.r-project.org/src/contrib/ppcor_1.1.tar.gz` and verifies
    `packageVersion("ppcor") == "1.1"`. It does not depend on local `repo/`.
- Fallback:
  - CRAN mirror source at `wrappers/inference_tools/tools/ppcor/repo/` is
    evidence only and must not be used at runtime.

## ToolSpec Evidence Ledger

### `schema_version`

- Chosen value: `1.0`.
- Evidence: current catalog ToolSpecs use schema version `1.0`.
- Rationale: standard ANDREA ToolSpec version.
- Uncertainty: none.

### `id`

- Chosen value: `ppcor`.
- Evidence: scaffold path and CRAN package name.
- Rationale: stable lowercase tool id matching upstream package.
- Uncertainty: none.

### `name`

- Chosen value: `ppcor`.
- Evidence: CRAN package title and package name.
- Rationale: preserves public package name.
- Uncertainty: none.

### `publication`, `first_author`, `year`

- Chosen values:
  - `publication`:
    - `https://doi.org/10.5351/CSAM.2015.22.6.665`
    - `https://doi.org/10.32614/CRAN.package.ppcor`
  - `first_author`: `Seongho Kim`
  - `year`: `2015`
- Evidence:
  - `papers/PPCOR.txt` first page lists DOI `10.5351/CSAM.2015.22.6.665`,
    title, first author Seongho Kim and final publication in 2015.
  - CRAN page lists DOI `10.32614/CRAN.package.ppcor`.
- Rationale: record both the method/package paper and the package DOI.
- Uncertainty: none.

### `method_summary` and `method_keywords`

- Chosen value: summary and keywords in ToolSpec.
- Evidence:
  - Paper abstract says ppcor derives a general matrix formula for
    semi-partial correlation and implements semi-partial correlation along
    with partial correlation, with coefficients, p-values and statistics.
  - `pcor.Rd` documents the selected `pcor()` entrypoint as computing pairwise
    partial correlations for every pair of variables given all others.
- Rationale: summary distinguishes the broader package from the selected
  wrapper contract.
- Uncertainty: none.

### `implementation_url`

- Chosen value: `https://CRAN.R-project.org/package=ppcor`.
- Evidence: CRAN package page and paper state ppcor is publicly available from
  CRAN.
- Rationale: official package manager source is preferred.
- Uncertainty: none.

### `docker_image`

- Chosen value: `adriansegura99/inference-tools_ppcor:1.0.0`.
- Evidence: existing inference tool image naming convention.
- Rationale: matches catalog convention.
- Uncertainty: local Phase 2/3 builds passed; external image publication is
  outside this phase.

### `execution_capabilities`

- Chosen value: `["global", "group_emulated"]`.
- Evidence and rationale: see "Upstream Interface Audit".
- Uncertainty: none.

### `runtime_resources`

- Chosen value: no supported threading, default/max 1.
- Evidence and rationale: see "Runtime Resource Mapping".
- Uncertainty: possible BLAS threads are environment-level, not ppcor API.

### `taxonomic_scope` and `compatibility_rules`

- Chosen value: all broad groups, no supported species list, no compatibility
  rules.
- Evidence:
  - ppcor operates on numeric matrices/data frames and does not consume
    species-specific annotations or prior biology.
- Rationale: generic statistical association method.
- Uncertainty: none.

### `accepts` and `assumes`

- Chosen values:
  - `accepts`: `["samples", "cells", "timepoints", "perturbations"]`
  - `assumes`: `generic`
- Evidence:
  - ppcor docs require only a matrix/data frame of numeric observations by
    variables.
  - Paper examples describe generic samples and variables, including omics
    use cases.
- Rationale: any expression column type can serve as an observation for a
  generic partial-correlation matrix, provided enough observations exist.
- Uncertainty: high-dimensional low-sample runs may use generalized inverse
  and lose p-value/statistic support; this is an upstream runtime rule.

### `extra_inputs`

- Chosen values: no required or optional inputs; `groups` conditional for
  `group_emulated`.
- Evidence and rationale: see "Optional or Conditional Inputs".
- Uncertainty: none.

### `outputs`

- Chosen value: undirected, signed, evidence `association`.
- Evidence:
  - `pcor()` returns a symmetric partial-correlation coefficient matrix.
  - Coefficients are signed correlation values.
- Rationale: one unordered edge per gene pair; `score=abs(coefficient)` and
  coefficient direction stored only in `sign`.
- Uncertainty: none for `pcor()`.

### `progress`

- Chosen value: `kind="none"`.
- Evidence:
  - `pcor()` is a synchronous R function returning a list and exposes no
    callback, progress counter or stable incremental output.
- Rationale: wrapper can report coarse lifecycle states only.
- Uncertainty: none.

### `params`

- Chosen value: `method` enum with default `pearson`.
- Evidence and rationale: see "Parameters and Defaults".
- Uncertainty: none.

### `artifacts_aux`

- Chosen values:
  - `ppcor.log`
  - `raw/estimate.tsv`
  - `raw/p.value.tsv`
  - `raw/statistic.tsv`
  - `raw/ppcor_config.json`
- Evidence:
  - `pcor()` returns estimate, p-value, statistic, n, gp and method.
  - The selected public `network.csv` uses only estimate magnitudes/signs, so
    the remaining raw outputs should be preserved for audit.
- Rationale: retain raw upstream matrices and resolved runtime configuration.
- Uncertainty: p-value/statistic matrices may contain missing values when the
  covariance matrix is singular and variables are at least sample size; files
  are still written.

## Normalized Input Mapping

### Reused input specs

- `groups`
  - Semantics match ANDREA grouping for group-emulated execution.

### New input specs required

- None. The selected method consumes only the normalized expression matrix and
  conditional group labels.

## Smoketest Plan

- Use the shared expression fixture if it has enough expression columns for a
  non-singular or generalized-inverse-safe smoke run.
- Cover both declared execution capabilities:
  - `global`
  - `group_emulated` with `groups.tsv`
- Checks:
  - `network.csv` has one row per unordered pair, no self-loops.
  - `score` is positive `abs(estimate)`.
  - `sign` is `+` or `-`.
  - raw matrices and config artifacts exist.

## Implemented Wrapper Notes

- Runtime source: Dockerfile installs `ppcor` from the pinned CRAN source
  tarball `https://cran.r-project.org/src/contrib/ppcor_1.1.tar.gz` and checks
  `packageVersion("ppcor") == "1.1"`. The local `repo/` folder is not copied
  into the image.
- Runtime dependencies: Dockerfile installs `jsonlite` with R's package
  manager in the same R runtime used by the wrapper. `MASS` is available from
  the R runtime/recommended package set and is loaded by `ppcor`.
- Runtime resources: wrapper requires `--threads=1` and pins common
  BLAS/OpenMP environment variables to 1.
- Params: wrapper validates `method`; if absent, it uses the upstream
  `pcor()` default `pearson`.
- Expression serialization: wrapper reads ANDREA `expression.tsv` as genes by
  expression columns and transposes it to observations by genes before calling
  `ppcor::pcor()`.
- Execution modes: wrapper accepts `global` and `group_emulated`. For
  `group_emulated`, it validates that all current expression columns appear in
  `groups.tsv` and allows `groups.tsv` to be a superset, because ANDREA core
  owns group-emulated partitioning.
- Raw artifacts: wrapper writes `raw/estimate.tsv`, `raw/p.value.tsv`,
  `raw/statistic.tsv` and `raw/ppcor_config.json`.
- Upstream singular covariance behavior: wrapper calls `ppcor::pcor()`
  directly and preserves its generalized-inverse warnings in `ppcor.log`.
  Because upstream can return matrices without dimnames after generalized
  inverse, the wrapper restores dimnames from exact ANDREA gene ids before
  writing raw matrices or `network.csv`.
- Output conversion: wrapper exports one unordered row per non-self gene pair
  with finite non-zero coefficient, `score=abs(coefficient)`, `sign` set from
  coefficient direction, `evidence=association`, `context=global`, and no
  ANDREA-specific normalization.

## Smoketest Outcome

- Final Phase 3 validation:
  - `Rscript -e 'invisible(parse(file="wrappers/inference_tools/tools/ppcor/run_tool.R"))'`
  - `make validate-toolspecs ARGS="--tool ppcor"`
  - `python wrappers/inference_tools/scripts/validate_input_specs.py --spec expression_matrix --spec groups`
  - `python wrappers/inference_tools/scripts/validate_smoketest_configs.py --tool ppcor`
  - `python wrappers/inference_tools/scripts/build_tool_images.py --tool ppcor --image-tag ppcor=ppcor-smoketest:local`
  - `python wrappers/inference_tools/scripts/run_smoketests.py --tool ppcor --skip-image-build --image-tag ppcor=ppcor-smoketest:local --timeout 300`
- Result: passed.
- Image build installed and verified CRAN `ppcor` version `1.1`.
- Variant `global_pearson`: produced 28 positive signed unordered edges and
  all five declared auxiliary artifacts.
- Variant `group_emulated_contract`: validated `groups.tsv`, recorded two
  groups in `raw/ppcor_config.json`, produced 28 positive signed unordered
  edges and all five declared auxiliary artifacts. Direct wrapper output
  retains `context=global`; ANDREA core is responsible for physical
  group-emulated partitioning and `group:<id>` public contexts.

## Known Limitations / Open Questions

- The selected contract exposes partial correlation only. Semi-partial
  correlation is intentionally excluded because it is asymmetric and would
  require a separate output-direction contract.
- ppcor does not perform feature selection, TF restriction or biological
  direction inference; it is a signed association method.
- For high-dimensional data where the covariance matrix is singular, upstream
  uses Moore-Penrose generalized inverse and may not provide p-values or
  statistics. The network edge coefficients remain the selected score source.
