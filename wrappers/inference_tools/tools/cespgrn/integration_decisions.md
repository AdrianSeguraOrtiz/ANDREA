# cespgrn Integration Decisions

## Phase 3 Status

- Scope: Phase 3 completed. Wrapper, Dockerfile, ToolSpec, input specs,
  template-map registration, fixtures and smoketest config are aligned. Cost
  profiling is not part of this phase.
- Tool id: `cespgrn`.
- Upstream snapshot reviewed: `wrappers/inference_tools/tools/cespgrn/repo/CeSpGRN`
  at commit `2fb222f8a26edf1bdae14a95b1491543f0aaa4e8` (`v1.0.0` tag).
- Runtime installation: no PyPI distribution was found with
  `.venv/bin/python -m pip index versions cespgrn`; Docker clones the official
  GitHub repository pinned to `2fb222f8a26edf1bdae14a95b1491543f0aaa4e8` and
  runs the wrapper against `/opt/CeSpGRN/src`.
- Final validation on 2026-06-17:
  - `.venv/bin/python wrappers/inference_tools/scripts/validate_toolspecs.py --tool cespgrn`
    passed.
  - `.venv/bin/python wrappers/inference_tools/scripts/validate_input_specs.py`
    passed with 16 valid input specs.
  - `.venv/bin/python wrappers/inference_tools/scripts/validate_smoketest_configs.py --tool cespgrn`
    passed.
  - `.venv/bin/python -m py_compile wrappers/inference_tools/tools/cespgrn/run_tool.py wrappers/inference_tools/tools/cespgrn/torch_sqrtm.py`
    passed.

## Sources Reviewed

- Local repo README: `repo/CeSpGRN/README.md`.
- Local repo demo: `repo/CeSpGRN/demo.py`.
- Core source: `repo/CeSpGRN/src/g_admm.py`,
  `repo/CeSpGRN/src/kernel.py`, `repo/CeSpGRN/src/genie3.py`.
- Test workflows:
  - `repo/CeSpGRN/test/scripts_mESC/benchmark.py`
  - `repo/CeSpGRN/test/scripts_drosophila_embryo/test_drosophila_embryo.py`
  - `repo/CeSpGRN/test/scripts_nmp/test_nmp.py`
  - `repo/CeSpGRN/test/scripts_scmultisim/test_scmultisim.py`
- Local paper text: `papers/CeSpGRN.txt`; extraction quality is good enough for
  title, DOI, abstract, methods, limitations and supplement hyperparameters.
- Current publication metadata checked from Oxford Academic/PubMed search:
  Bioinformatics, Volume 42, Issue 6, June 2026, DOI
  `https://doi.org/10.1093/bioinformatics/btag324`.

## Method Summary

CeSpGRN infers cell-specific GRNs from single-cell expression, multi-omics or
spatial transcriptomics data. The core method assumes neighboring cells in
expression or spatial space have smoothly changing GRNs, constructs a Gaussian
cell-cell kernel, fits a weighted Gaussian Copula Graphical Model precision
matrix per cell with ADMM, and transforms each precision matrix to a signed
partial-correlation network.

## Upstream Interface Audit

| Upstream public entrypoint / workflow | Inputs | Output | ANDREA mapping | Exposed | Rationale |
| --- | --- | --- | --- | --- | --- |
| README/demo pipeline: normalize counts, PCA, `kernel.calc_kernel_neigh`, `g_admm.est_cov`, `G_admm_minibatch(...).train(...)` | cells x genes expression matrix | tensor `(cells, genes, genes)` of signed partial correlations | `cell_native` | Yes | This is the narrowest public path that produces native one-network-per-cell outputs from expression only. |
| Same cell-native path plus ANDREA `groups.tsv` | expression matrix plus groups after wrapper output | derived group networks | `group_aggregated` | Yes | CeSpGRN itself does not consume groups; ANDREA can aggregate `cell:<id>` rows with the fixed signed-effect mean rule. |
| Spatial workflow in paper and `test/scripts_drosophila_embryo/test_drosophila_embryo.py` | expression matrix plus cell spatial coordinates | cell-specific signed partial-correlation tensor | parameter choice `kernel_source=spatial` within `cell_native` / `group_aggregated` | Yes | It uses the same CeSpGRN estimator and only changes the cell-cell kernel coordinates, so it is a parameter/input choice, not a new execution mode. |
| TF-prior workflow from paper and `G_admm_minibatch(TF=...)` | expression matrix plus TF list | cell-specific signed partial-correlation tensor with target-target mask penalty when beta > 0 | parameter choice `prior_mode=tf_list` | Yes | Existing `tf_list` semantics match the paper's TF information prior. |
| ATAC/cell-specific prior workflows in paper and `G_admm_mask(mask=...)` test scripts | paired scRNA/scATAC plus region-target and region-TF mappings or precomputed cell-specific masks | cell-specific signed partial-correlation tensor | parameter/input choice if represented | No | A standalone accessibility matrix is insufficient; the workflow requires cell-specific prior masks or genomic region-to-gene and motif/TF mapping inputs not currently normalized in the inference catalog. |
| `src/genie3.py` | expression matrix, optional regulators | global directed tree-importance matrix | separate global method, not CeSpGRN | No | It is bundled as a baseline implementation, not the CeSpGRN method. Existing GENIE3 integration should own this behavior. |
| `src/de_analysis.py` | pseudotime and expression | differential expression outputs | not network inference | No | Auxiliary downstream analysis, not a GRN inference output contract. |
| Published/supplement scripts that scan bandwidth and lambda grids and average GRNs | expression/spatial plus parameter grid | averaged cell-specific GRNs | possible parameter preset | No | Implementing the paper grid would multiply runtime substantially and is present as ad-hoc scripts, not a reusable public API. The current contract exposes the single-run API; users can run parameter sweeps externally or a later wrapper can add an explicit preset. |

## Execution Contract

- Declared capabilities: `cell_native`, `group_aggregated`.
- Not declared:
  - `global`: CeSpGRN's method target is cell-specific GRNs, not one
    population-level network.
  - `group_native`: no public CeSpGRN entrypoint consumes groups and returns
    group networks from one run.
  - `group_emulated`: running cell-native CeSpGRN separately per group would
    still produce per-cell rows and ANDREA's `group_emulated` finalizer would
    rewrite them to duplicate group contexts rather than applying the intended
    signed aggregation rule.
- `group_aggregated` is ANDREA-managed. The physical wrapper output remains
  `cell:<cell_id>` rows, and ANDREA core writes only derived `group:<id>` rows
  to the logical `group_aggregated` `network.csv`.

## Input Contract

Always required:
- `expression_matrix`: genes x cells in ANDREA normalized form. The wrapper
  will transpose to the upstream cells x genes matrix.

Conditional required:
- `groups` when `execution.mode=group_aggregated`; consumed by ANDREA after
  the physical cell-native run.
- `spatial_coordinates` when `kernel_source=spatial`; new input spec added at
  `andrea/catalog_inference_tools/input_specs/spatial_coordinates.json`. It maps
  expression cell ids to numeric `x`, `y` and optional `z` coordinates, matching
  the paper's spatial kernel source and the Drosophila script's `spaLoc.values`.
  The input key is also registered in the ToolSpec schema and synthetic
  benchmark input generator.
- `tf_list` when `prior_mode=tf_list`; existing input spec matches the paper's
  TF information prior and maps TF gene ids to expression genes.

Optional inputs:
- None. `tf_list` and `spatial_coordinates` are not optional enrichments because
  they are required exactly when their activating parameter values are selected.

Inputs intentionally not exposed:
- A standalone accessibility matrix is not enough for CeSpGRN-ATAC because the
  paper constructs cell-specific priors from accessible regions, region to
  target-gene proximity and motif/TF information. Existing `prior_grn` and
  `prior_grn_by_group` are global/group-level, not cell-specific masks.
- Pseudotime is not consumed by the selected CeSpGRN estimator; the paper
  explicitly notes that CeSpGRN does not require pseudotime ordering.

## Parameter Contract

Exposed method parameters:
- `kernel_source`: `expression` or `spatial`. Evidence: paper Section 2.2 and
  Drosophila spatial script.
- `prior_mode`: `none` or `tf_list`. Evidence: paper Section 2.3 and
  `G_admm_minibatch(TF=...)`.
- `expression_preprocessing`: `library_size_log1p` or `none`. Evidence:
  `demo.py` and test scripts scale by median library size and apply `log1p`.
- `pca_components`: default `20`. Evidence: `demo.py` uses 20; README uses 10,
  so this value has medium uncertainty and is chosen to match the runnable demo.
- `bandwidth`: default `0.1`. Evidence: `demo.py`; README example uses 1 and
  paper scans `[0.01, 0.1, 1, 10]`, so the default has medium uncertainty.
- `n_neigh`: default `30`. Evidence: README and `demo.py`; paper supplement
  scripts commonly use 100 for larger datasets.
- `lamb`: default `0.1`. Evidence: README and `demo.py`; low-level
  `train()` default is `2.1e-4`, so the ToolSpec intentionally follows the
  public runnable example rather than the bare optimizer default.
- `prior_beta`: default `1.0`, used only with `prior_mode=tf_list`; paper
  supplement fixes beta to 1 for prior-constrained tests. Wrapper should pass
  beta 0 when `prior_mode=none`.
- `max_iters`: default `1000`. Evidence: `demo.py`; test scripts use 100 for
  benchmarking speed.
- `n_intervals`: default `100`. Evidence: README and `demo.py`.
- `batch_size`: default `null`. Exact upstream data-dependent rule:
  `G_admm_minibatch.__init__` sets `self.batchsize = int(self.ntimes/10)` when
  `batchsize is None`; ToolSpec preserves this with null and the wrapper omits
  the `batchsize` constructor argument when null.
- `random_seed`: default `0`. Evidence: `G_admm_minibatch.__init__(seed=0)`;
  null should pass `seed=None` for unseeded torch behavior.

Fixed implementation choices, not exposed:
- `kernel.calc_kernel_neigh(k=5, truncate=True)`: the paper says k is the
  minimum number larger than 5 that makes the kNN graph connected; upstream
  code starts the search from `k=5`.
- `weighted_kt=True` for covariance estimation in the selected path: README and
  demo call `est_cov(..., weighted_kt=True)`.
- `alpha=2`, `rho=1.7`, `theta_init_offset=0.1`: ADMM implementation knobs used
  by examples/code, not primary method-level user inputs for this first
  contract.
- `n_jobs`/thread controls: runtime infrastructure represented in
  `runtime_resources.threading`, not a method parameter.

## Runtime Resources

- Threading support: `supported=true`.
- Default threads: `1`.
- Maximum planned threads: `8`.
- ANDREA mapping: wrapper argument `--threads` is mapped to
  `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
  `NUMEXPR_NUM_THREADS`, `torch.set_num_threads(threads)`, and
  `G_admm_minibatch.train(njobs=threads)`.
- Evidence:
  - `wrappers/inference_tools/tools/cespgrn/run_tool.py` sets the environment
    variables, calls `torch.set_num_threads()`, and passes `njobs` to upstream
    `train()`.
  - `wrappers/inference_tools/tools/cespgrn/repo/CeSpGRN/src/g_admm.py`
    exposes `train(..., njobs=1)` and forwards `njobs` into
    `construct_weighted_G()`, whose docstring says `njobs` is the number of
    CPUs and whose implementation creates `Pool(njobs)`.
  - Upstream test scripts set `OMP_NUM_THREADS` and `torch.set_num_threads()`;
    several benchmark/test paths use `njobs=8` or higher.
- Rationale: this is real runtime parallelism through PyTorch, BLAS/OpenMP and
  upstream multiprocessing, so it belongs under `runtime_resources.threading`
  and should not be exposed as a normal method parameter.
- Uncertainty: no catalog `cost.json` exists for CeSpGRN yet, so
  `max_threads=8` is a conservative ANDREA planning and benchmarking cap rather
  than an upstream hard limit. The planner will use `default_threads=1` until
  empirical cost points are generated.

## Output Mapping to `network.csv`

- Upstream selected score tensor: signed partial-correlation matrices returned
  by `G_admm_minibatch.train()` after `construct_weighted_G()`.
- Directionality: undirected. The paper discussion states CeSpGRN infers
  undirected graphs; repo benchmarks assert CeSpGRN matrices are symmetric.
- Sign: signed. Positive and negative partial correlations are meaningful in
  repo benchmark code; ANDREA rows should write `score=abs(partial_correlation)`
  and `sign` as `+` or `-`.
- Evidence: `association`, because partial correlation/conditional dependence
  does not establish directed causality.
- Row convention: one row per unordered gene pair per cell, no self-loops,
  omit exact zero magnitudes.
- Context: `cell:<original_expression_column_id>` for `cell_native`. For
  `group_aggregated`, ANDREA will aggregate these rows to `group:<group_id>`.
- Public ids: preserve expression gene ids and cell ids exactly. No upstream
  aliasing is required; the wrapper runs in memory and writes raw id indices to
  `raw/ids.tsv` for auditability.
- Dense-output warning: cell-native output can be very large
  (`cells * genes * (genes - 1) / 2` candidate rows before zero filtering).

## ToolSpec Evidence Ledger

| Field | Chosen value | Evidence and rationale | Uncertainty |
| --- | --- | --- | --- |
| `schema_version` | `1.0` | Fixed by `toolspec.schema.json`. | None |
| `id` | `cespgrn` | Scaffold/catalog directory names. | None |
| `name` | `CeSpGRN` | Repo README title and paper title. | None |
| `publication` | `https://doi.org/10.1093/bioinformatics/btag324`; preprint `https://doi.org/10.1101/2022.03.03.482887` | Published Bioinformatics page is primary current paper; local PDF is the bioRxiv preprint and remains reviewed evidence. | Low; local paper is preprint but current DOI is newer. |
| `first_author` | `Ziqi Zhang` | Local paper title page and Bioinformatics metadata. | None |
| `year` | `2026` | Bioinformatics page: Volume 42, Issue 6, June 2026; published 01 June 2026. | None |
| `method_summary` | Kernel-weighted GCGM cell-specific GRN with optional spatial/TF paths | Paper abstract and methods Sections 2.1-2.5; repo README. | None |
| `method_keywords` | `single_cell`, `cell_specific_network`, `gaussian_copula_graphical_model`, `kernel_weighting`, `partial_correlation`, `spatial_transcriptomics` | Paper title, abstract, Sections 2.1-2.5. | None |
| `implementation_url` | `https://github.com/PeterZZQ/CeSpGRN` | Repo README and published paper availability statement. | None |
| `docker_image` | `adriansegura99/inference-tools_cespgrn:1.0.0` | Project image naming convention. | None |
| `execution_capabilities` | `cell_native`, `group_aggregated` | Paper/repo produce one GRN per cell; ANDREA can aggregate cell rows by group. | None |
| `runtime_resources.threading` | `supported=true`, `default_threads=1`, `max_threads=8` | Wrapper maps `--threads` to Torch, BLAS/OpenMP environment variables and upstream `G_admm_minibatch.train(njobs=threads)`; upstream source documents `njobs` as CPU count for `construct_weighted_G()`. | Medium; no catalog `cost.json` exists yet, so `max_threads=8` is a conservative planning cap. |
| `accepts` | `cells` | README requires count matrix shape `(ncells, ngenes)` and method is single-cell specific. | None |
| `assumes` | `scrna_specific` | Paper targets scRNA-seq, single-cell multi-omics and spatial transcriptomics. | None |
| `taxonomic_scope` | all broad groups, no species IDs | Method is statistical and examples include human, mouse and Drosophila; no species-specific packaged database is required for selected paths. | Low |
| `compatibility_rules` | `[]` | No organism-specific hard blocks for selected expression/spatial/TF-list paths. | Low |
| `extra_inputs` | conditional `groups`, `spatial_coordinates`, `tf_list` | ANDREA group aggregation contract; paper spatial kernel; paper TF prior. | None for selected paths |
| `outputs` | undirected, signed, association | Paper Section 2.5 and discussion; repo benchmark symmetry checks and signed metrics. | None |
| `progress` | `iterations` | `train()` loops over minibatches and ADMM iterations and prints `n_iter` diagnostics. | Medium; wrapper may fall back to coarse phases if parsing is brittle. |
| `params` | listed in Parameter Contract | README/demo/source signatures/paper hyperparameter section. | Medium for defaults where README/demo/code differ. |
| `artifacts_aux` | log, raw partial correlations, precision matrices, kernel | Repo writes `.npy` tensors in demo/test workflows; these are useful audit artifacts before `network.csv` conversion. | Low |

## Installation Strategy

- Preferred package manager source: none found. `pip index versions cespgrn`
  returned no matching distribution, the local repo has no `setup.py`,
  `pyproject.toml` or `requirements.txt`, and GitHub reports no packages.
- Pinned source: clone `https://github.com/PeterZZQ/CeSpGRN.git`
  at `2fb222f8a26edf1bdae14a95b1491543f0aaa4e8` (`v1.0.0`).
- Runtime dependencies from README: PyTorch, NumPy, SciPy, NetworkX and
  scikit-learn; Matplotlib/statsmodels are optional and not needed for the
  selected wrapper contract.
- Docker uses Python 3.10, NumPy 1.24.4, SciPy 1.10.1, scikit-learn 1.2.2,
  NetworkX 2.8.8, pandas 2.0.3, Matplotlib 3.7.3 and CPU PyTorch 1.13.1.
- CeSpGRN imports `torch_sqrtm.MatrixSquareRoot`, but no `torch-sqrtm` PyPI
  distribution was found. The wrapper image supplies a local `torch_sqrtm.py`
  compatibility module using SciPy `sqrtm`/`solve_sylvester`.
- PyTorch 1.13.1 keeps `torch.eig` as a removed-function stub. The wrapper
  installs a runtime compatibility adapter before importing `g_admm`, mapping
  CeSpGRN's expected `torch.eig(..., eigenvectors=...)` signature to
  `torch.linalg.eig/eigvals` without editing the pinned upstream checkout.

## Implemented Wrapper Notes

- Runtime does not depend on local `repo/`; Docker clones the pinned upstream
  repository.
- Dependencies are installed with the same Python interpreter that executes
  `run_tool.py`.
- The cloned repo's `src` directory and `/app` are on `PYTHONPATH`.
- The wrapper reads ANDREA expression as genes x cells, preserves gene/cell ids,
  and transposes to upstream cells x genes.
- `expression_preprocessing=library_size_log1p` rejects negative values and
  zero-library cells before division; `none` passes finite values through.
- `kernel_source=expression` computes PCA with `pca_components`;
  `kernel_source=spatial` aligns `spatial_coordinates.tsv` exactly to expression
  cell ids and passes numeric coordinates to `kernel.calc_kernel_neigh()`.
- `prior_mode=tf_list` validates TF ids against expression genes and passes TF
  indices; `prior_mode=none` omits TF and forces `beta=0`.
- `batch_size=null` omits `batchsize` so upstream uses `int(ncells/10)`. For
  fewer than 10 cells the wrapper raises a clear error because the preserved
  upstream rule would resolve to zero.
- `network.csv` contains one unordered, non-self edge per cell/gene pair with
  `score=abs(partial_correlation)`, `sign=+/-`, `evidence=association`, and
  `context=cell:<original_cell_id>`.
- Raw artifacts written: `cespgrn.log`, `raw/kernel.npy`,
  `raw/kernel_truncated.npy`, `raw/kernel_coordinates.npy`,
  `raw/precision_matrices.npy`, `raw/partial_correlations.npy`, and
  `raw/ids.tsv`.

## Smoketest Outcome

- Smoketest config: `wrappers/inference_tools/tests/smoketest_configs/cespgrn.json`.
- Fixtures: shared `expression.tsv`, `groups.tsv`, and new
  `spatial_coordinates.tsv`.
- Variants:
  - `cell_native`: expression kernel, one ADMM iteration, explicit
    `batch_size=30`.
  - `group_aggregated_spatial`: validates `groups.tsv`, uses
    `kernel_source=spatial`, and still emits physical `cell:<id>` rows for
    ANDREA core aggregation.
- Build command passed:
  `.venv/bin/python wrappers/inference_tools/scripts/build_tool_images.py --tool cespgrn --image-tag cespgrn=cespgrn-smoketest:local`.
- Smoketest command passed:
  `.venv/bin/python wrappers/inference_tools/scripts/run_smoketests.py --tool cespgrn --image-tag cespgrn=cespgrn-smoketest:local --timeout 240`.
- Outcome: `passed=1 failed=0`; `cell_native` wrote 831 rows and
  `group_aggregated_spatial` wrote 820 physical cell-native rows. All declared
  auxiliary artifacts were present in both variants.

## Known Limitations / Open Questions

- ATAC/cell-specific prior mode is excluded until ANDREA has normalized inputs
  for region-to-gene/motif relationships or cell-specific prior masks.
- Published paper-grid averaging is excluded from the first wrapper contract;
  it can be added later as a preset if runtime and UI semantics are acceptable.
- Defaults conflict across source layers: low-level code defaults, README
  examples, demo values and paper grid values differ. The ToolSpec follows the
  runnable demo/README for most user-facing defaults and records conflicts
  above.
- The runtime compatibility shims for `torch_sqrtm` and `torch.eig` are wrapper
  infrastructure, not exposed method parameters.
