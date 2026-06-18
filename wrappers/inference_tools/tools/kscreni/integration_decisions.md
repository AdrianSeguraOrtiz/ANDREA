# kscreni Integration Decisions

## Integration Status

- Phase executed: Phase 3 complete.
- Wrapper implementation: `wrappers/inference_tools/tools/kscreni/run_tool.R`.
- Dockerfile: `wrappers/inference_tools/tools/kscreni/Dockerfile`.
- Smoketest config: `wrappers/inference_tools/tests/smoketest_configs/kscreni.json`.
- Smoketest fixtures: `wrappers/inference_tools/tests/fixtures/kscreni/expression.tsv`, `wrappers/inference_tools/tests/fixtures/kscreni/groups.tsv`.
- Selected upstream entrypoint: `ScReNI::Infer_kScReNI_scNetworks()`.
- Implemented entrypoint behavior: the wrapper mirrors the selected kScReNI public function but inlines that function body to pass a data-dependent safe `npcs` to `Seurat::RunPCA()`.
- Selected ANDREA capabilities: `cell_native` and `group_aggregated`.
- New normalized input specs: none.
- Final Phase 3 validation:
  - `make validate-toolspecs ARGS="--tool kscreni"` passed.
  - `make validate-input-specs` passed for all 16 inference input specs, including relevant `expression_matrix` and `groups`.
  - `make validate-smoketest-configs ARGS="--tool kscreni"` passed.
- Final Phase 3 smoke command: `make run-tool-smoketests ARGS="--tool kscreni --threads 2 --timeout 2400 --show-output-lines 20"`.
- Final Phase 3 smoke result: passed for `cell_native` and `group_aggregated`; each variant wrote 3081 positive non-self rows and validated 2 auxiliary artifacts.

## Sources Reviewed

- Playbook: `wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md`
- Upstream repo: `wrappers/inference_tools/tools/kscreni/repo/ScReNI`
- Local paper PDF: `wrappers/inference_tools/tools/kscreni/papers/ScReNI.pdf`
- Extracted paper text: `wrappers/inference_tools/tools/kscreni/papers/ScReNI.txt`
- Official repo page: `https://github.com/Xuxl2020/ScReNI`
- GENIE3 output-orientation evidence: `https://bioc.r-universe.dev/GENIE3/doc/GENIE3.html`

Paper extraction quality is adequate for method, DOI, author, year, and output-semantics evidence.

## Method Summary

ScReNI is a single-cell regulatory network inference method for scRNA-seq plus scATAC-seq data. The paper states that ScReNI identifies neighboring cells with nearest-neighbor methods and estimates nonlinear regulatory relationships with a modified random forest (`papers/ScReNI.txt:42-54`, `papers/ScReNI.txt:136-158`). The selected wrapper contract mirrors kScReNI, the transcriptome-only variant that uses scRNA-seq alone for cell-specific network inference (`papers/ScReNI.txt:149-177`; `repo/ScReNI/docs/ScReNI_tutorial.Rmd:135-168`).

## Upstream Interface Audit

| Upstream public entrypoint | Required inputs | Output | ANDREA mapping | Exposed | Rationale |
|---|---|---|---|---|---|
| `Infer_kScReNI_scNetworks(exprMatrix, nfeatures=4000, knn=20, nthread=20, nTrees=100)` | Gene x cell expression matrix | Named list of one directed weight matrix per expression cell | `cell_native`; `group_aggregated` is ANDREA aggregation over cell contexts | Yes | Cleanly matches normalized expression input. The tutorial says kScReNI uses transcriptomes alone and calls this function for single-cell networks (`docs/ScReNI_tutorial.Rmd:135-168`). |
| `Infer_wScReNI_scNetworks(exprMatrix, gene_peak_overlap_matrix, gene_peak_overlap_labs, nearest.neighbors.idx, network.path, data.name, ...)` | Expression, peak-associated matrix, gene/peak/TF labels, nearest-neighbor index, output path/name | One network per cell, written in batches and returned as a list | Could be `cell_native` in principle | No | Requires intermediate gene-peak labels and neighbor indices not represented by current ANDREA inputs (`R/Infer_wScReNI_scNetworks.R:19-24`, `docs/ScReNI_tutorial.Rmd:171-205`). |
| `Infer_gene_peak_relationships(gtf_data, scrna, scatac, motif_database, motif_pwm, genome_database, ...)` | GTF, scRNA, scATAC, motif database, motif PWM, genome database | Gene-peak-TF relationship object | Preprocessing for wScReNI | No | It is an upstream preprocessing step, not a direct GRN output, and its genomic resources are outside current normalized input specs (`R/Infer_gene_peak_relationships.R:21-52`). |
| `Integrate_scRNA_scATAC(...)` | Seurat scRNA/scATAC objects, integration dims, KNN, data type, species | Integrated Seurat object / weighted neighbors | Preprocessing for wScReNI | No | Integration step only; it does not produce ANDREA `network.csv` edges (`docs/ScReNI_tutorial.Rmd:101-133`). |
| `Get_scRNA_scATAC_neighbors(...)` | Integrated Seurat object | RNA/ATAC neighbor mapping | Preprocessing for wScReNI | No | Auxiliary neighbor extraction, not network inference. |
| `Infer_CSN_scNetworks(...)`, `Infer_LIONESS_scNetworks(...)` | Expression matrix and method-specific args | Benchmark-method cell networks | Separate tools / excluded benchmark modes | No | These are bundled baseline implementations, not the ScReNI method target (`docs/ScReNI_tutorial.Rmd:224-228`). |
| Degree, precision/recall, plotting, enriched-regulator functions | Existing networks and annotations | Downstream analysis outputs | Excluded | No | These consume inferred networks rather than producing the primary raw GRN. |

## Execution Capability Decision

- `cell_native`: selected because `Infer_kScReNI_scNetworks()` runs one logical ScReNI call and returns one network per cell (`R/Infer_kScReNI_scNetworks.R:32-48`).
- `group_aggregated`: selected because ANDREA can aggregate native `cell:<cell_id>` rows into group-level rows when `groups.tsv` is provided. The wrapper must still emit only native cell contexts; ANDREA owns the aggregation step.
- `global`, `group_native`, `group_emulated`: not exposed. kScReNI is defined as a cell-specific method; the selected public entrypoint does not natively consume group metadata or return one whole-dataset/global network.

## Input Contract

- Always required: normalized expression matrix, genes x cells, with non-negative numeric values. Evidence: the kScReNI entrypoint accepts `exprMatrix`, constructs a Seurat object from `counts = exprMatrix`, and names outputs with `colnames(exprMatrix)` (`R/Infer_kScReNI_scNetworks.R:17-30`, `R/Infer_kScReNI_scNetworks.R:46`). The wrapper validates non-negative counts because the upstream call passes the matrix as Seurat counts and runs `NormalizeData()`.
- Conditional required:
  - `groups` only when `execution.mode=group_aggregated`. ANDREA uses it after wrapper completion to aggregate cell-native edges. The upstream function does not consume group metadata.
- Optional inputs: none for the selected kScReNI contract.
- Not reused for the selected contract:
  - `chromatin_accessibility_matrix` is not enough for wScReNI. The public wScReNI path also needs gene-peak labels, motif/PWM/genome/GTF resources, and nearest-neighbor indices (`R/Infer_gene_peak_relationships.R:21-52`, `docs/ScReNI_tutorial.Rmd:171-205`). No new input spec is proposed because wScReNI is intentionally excluded from this phase.

## Runtime Resource Audit

- ToolSpec value: `runtime_resources.threading.supported=true`, `default_threads=1`, `max_threads=8`.
- ANDREA mapping: wrapper `--threads` maps to the `Infer_kScReNI_scNetworks(nthread=threads)` worker pattern.
- Evidence:
  - The public signature exposes `nthread=20` (`R/Infer_kScReNI_scNetworks.R:17`).
  - The implementation creates `parallel::makeCluster(nthread)`, registers `doParallel`, and runs `foreach(i = 1:ncell) %dopar%` over cells (`R/Infer_kScReNI_scNetworks.R:34-44`).
  - The inner `GENIE3::GENIE3()` call is fixed to `nCores=1`, so assigned threads control the outer cell-level worker pool, not nested GENIE3 threads (`R/Infer_kScReNI_scNetworks.R:40`).
- Rationale: this is a safe public in-process worker mapping. `nthread` is runtime resource control, not a user-facing method parameter.
- Uncertainty: scaling is workload-dependent and no `cost.json` exists yet, so `max_threads=8` is a conservative planner cap until benchmarking.
- Excluded runtime controls:
  - wScReNI also has `nthread` and an internal `nthread2 <- min(nthread, floor(length(ncell2)*1.5))`, but wScReNI is not exposed (`R/Infer_wScReNI_scNetworks.R:167-175`).
  - `Integrate_scRNA_scATAC()` uses `future::plan("multisession", workers = 4)` in the unpaired path, but that entrypoint is excluded (`R/Integrate_scRNA_scATAC.R:17-24`).

## Parameters and Defaults

Exposed params:

- `nfeatures`
  - Chosen value/default: integer, optional, default `4000`, min `1`.
  - Evidence: function signature default and use in `FindVariableFeatures(..., nfeatures = nfeatures)` (`R/Infer_kScReNI_scNetworks.R:17`, `R/Infer_kScReNI_scNetworks.R:27`).
  - Rationale: behavior-changing feature-selection setting in the selected public entrypoint.
  - Uncertainty: Seurat behavior when requested features exceed available genes is upstream-defined. The wrapper preserves that upstream behavior; the smoke fixture sets `nfeatures` equal to available genes and Seurat reports this as expected.
- `knn`
  - Chosen value/default: integer, optional, default `20`, min `1`.
  - Evidence: function signature default and use in `FindNeighbors(k.param = knn)` plus `1:(knn+1)` cell-neighborhood extraction (`R/Infer_kScReNI_scNetworks.R:17`, `R/Infer_kScReNI_scNetworks.R:29`, `R/Infer_kScReNI_scNetworks.R:38`).
  - Rationale: biologically meaningful neighborhood-size parameter.
  - Uncertainty: none for wrapper validation; the wrapper rejects `knn + 1 > number_of_cells` before upstream fails.

Fixed implementation choices not exposed:

- `nthread`: runtime resource mapped from `--threads`.
- `RunPCA` `npcs`: upstream does not expose it and relies on Seurat's default. The wrapper sets a data-dependent safe value `min(50, length(VariableFeatures(pbmc)), number_of_cells - 1, number_of_genes - 1)` to preserve the upstream PCA step while avoiding the `irlba` requirement that requested PCs be strictly below the matrix rank bounds. This is an implementation guard, not a user-facing method parameter.
- `nTrees`: appears in the public signature but is not honored. The implementation always calls `GENIE3(..., nTrees = 100)` (`R/Infer_kScReNI_scNetworks.R:17`, `R/Infer_kScReNI_scNetworks.R:40`), so exposing `nTrees` would be misleading.
- GENIE3 `nCores`: fixed to `1` inside kScReNI (`R/Infer_kScReNI_scNetworks.R:40`).
- GENIE3 `K`: not passed by ScReNI, so GENIE3 uses its data-dependent default `K="sqrt"` over candidate regulators; preserving the public entrypoint preserves this upstream rule. GENIE3 documentation defines `K="sqrt"` as the default candidate-regulator sampling rule.
- Random seed: fixed upstream as `set.seed(100)` inside each cell task (`R/Infer_kScReNI_scNetworks.R:39`).

## Output Mapping to `network.csv`

- Upstream score: nonnegative random-forest/GENIE3 importance weight. Preserve raw positive magnitudes directly as `score`.
- Sign handling: no signed coefficient is produced. Write `sign="?"` in `network.csv`; ToolSpec `outputs.sign="none"`.
- Direction: directed regulator-target edges. The ScReNI paper states that obtained cell-specific networks are directed from regulators to target genes and weights represent regulatory strengths (`papers/ScReNI.txt:230-238`). GENIE3 documentation states `weightMat[i,j]` is the weight of the link from gene `i` to gene `j`; the wrapper exports row gene as `source` and column gene as `target`.
- Evidence type: association. The method infers regulatory relationships from expression-neighborhood/random-forest importance rather than intervention or temporal causality.
- Filtering: omit self-loops and rows with `score <= 0`.
- Context: one row context per upstream cell network, `cell:<original_cell_id>`.
- Identifier preservation: use expression matrix row names as gene IDs and expression column names as cell IDs. `Infer_kScReNI_scNetworks()` uses the original `exprMatrix` columns to select neighborhoods and names output list entries with `colnames(exprMatrix)` (`R/Infer_kScReNI_scNetworks.R:38`, `R/Infer_kScReNI_scNetworks.R:46`). The wrapper introduces no aliases and errors if upstream cell names differ from expression column identifiers.
- Dense output warning: kScReNI returns one dense gene x gene matrix per cell. The wrapper filters positive edges during conversion; smoketests use small fixtures.

## Installation Strategy

- Preferred public installation source: upstream GitHub repo.
- Evidence:
  - README and tutorial instruct `devtools::install_github('Xuxl2020/ScReNI')` (`README.md:39-58`, `docs/ScReNI_tutorial.Rmd:38-48`).
  - The package DESCRIPTION reports `Package: ScReNI`, version `1.0.0`, and author Xueli Xu (`DESCRIPTION:1-6`).
  - Current CRAN package index check returned `CRAN_has_ScReNI= FALSE`.
  - GitHub page reports no releases published and no packages; local clone remote is `https://github.com/Xuxl2020/ScReNI.git`.
- Pinned source for Phase 2: install from `Xuxl2020/ScReNI` at commit `695772f8555f0b22df1c331e3c900075fee03b2c`.
- Implemented Docker route: base image `rocker/r2u:24.04`; install runtime packages with apt/R2U for the same R runtime (`r-cran-seurat`, `r-bioc-genie3`, `r-cran-doparallel`, `r-cran-foreach`, `r-cran-jsonlite`, `r-cran-remotes`); install ScReNI with `remotes::install_github('Xuxl2020/ScReNI', ref='695772f8555f0b22df1c331e3c900075fee03b2c', dependencies=FALSE, upgrade='never')`; assert `packageVersion('ScReNI') == '1.0.0'`.
- Rationale: package-manager/official library availability was not found for ScReNI; README recommends GitHub installation and the integrator supplied the same source plus commit. R2U binary packages avoid a long source build for large runtime dependencies while still installing them into the same R interpreter used by the wrapper.
- Uncertainty: a Bioconductor full-index query was attempted but did not complete promptly. There is no repo, paper, README, or search evidence of an official Bioconductor package; the implemented Dockerfile therefore uses the pinned upstream GitHub commit.

## ToolSpec Evidence Ledger

| Field | Chosen value | Evidence | Rationale / uncertainty |
|---|---|---|---|
| `schema_version` | `1.0` | `toolspec.schema.json` requires constant `1.0` | Project contract; certain. |
| `id` | `kscreni` | Tool folder and catalog path use `kscreni` | Project scaffold; certain. |
| `name` | `kScReNI` | README title uses ScReNI; paper names kScReNI as scRNA-only variant (`README.md:4-13`, `papers/ScReNI.txt:149-177`) | Distinguishes selected public variant; certain. |
| `publication` | `https://doi.org/10.1093/gpbjnl/qzaf060` | Paper DOI (`papers/ScReNI.txt:1-3`) | Canonical DOI URL; certain. |
| `first_author` | `Xueli Xu` | Paper author list (`papers/ScReNI.txt:9-16`) | Full first author name; certain. |
| `year` | `2025` | Paper citation and publication details (`papers/ScReNI.txt:1-3`, `papers/ScReNI.txt:59-63`) | Primary method paper year; certain. |
| `method_summary` | ScReNI kNN plus random forest; kScReNI scRNA-only directed cell networks | Abstract/methods (`papers/ScReNI.txt:42-54`, `papers/ScReNI.txt:149-177`, `papers/ScReNI.txt:230-238`) | Method wording, not wrapper behavior; certain. |
| `method_keywords` | `single_cell`, `cell_specific_network`, `k_nearest_neighbors`, `random_forest`, `genie3`, `directed` | Paper keywords/method and code GENIE3 call (`papers/ScReNI.txt:54`, `papers/ScReNI.txt:165-168`, `R/Infer_kScReNI_scNetworks.R:40`) | Captures method family and selected implementation; certain. |
| `implementation_url` | `https://github.com/Xuxl2020/ScReNI` | Paper code availability and README (`papers/ScReNI.txt:700-706`, `README.md:41-46`) | Canonical install source; certain. |
| `docker_image` | `adriansegura99/inference-tools_kscreni:1.0.0` | Existing inference-tool image naming convention | Project convention; local smoketest image `kscreni-smoketest:local` built successfully in Phase 2. |
| `execution_capabilities` | `cell_native`, `group_aggregated` | kScReNI returns one network per cell (`R/Infer_kScReNI_scNetworks.R:32-48`) | Matches ANDREA cell-native plus ANDREA-owned aggregation; certain. |
| `runtime_resources` | Threading supported; default 1; max 8; `--threads -> kScReNI nthread worker pattern` | kScReNI `nthread` and foreach worker pool (`R/Infer_kScReNI_scNetworks.R:17`, `R/Infer_kScReNI_scNetworks.R:34-44`) | Safe resource mapping; max is conservative until cost profiling. |
| `taxonomic_scope` | All broad groups, no species IDs | Selected kScReNI uses expression matrix only; species-specific motif/genome resources occur only in excluded wScReNI tutorial (`docs/ScReNI_tutorial.Rmd:171-187`) | No species restriction for kScReNI; moderate confidence. |
| `compatibility_rules` | `[]` | No selected species- or parameter-specific hard block found | Wrapper validates matrix size and `knn + 1 <= number_of_cells`; schema cannot encode cell-count bounds. |
| `accepts` | `cells` | ScReNI is cell-specific and kScReNI names one result per expression column/cell (`papers/ScReNI.txt:42-54`, `R/Infer_kScReNI_scNetworks.R:46`) | Certain. |
| `assumes` | `scrna_specific` | kScReNI is for scRNA-seq alone and cell-specific network inference (`papers/ScReNI.txt:149-177`) | Certain. |
| `extra_inputs` | No required/optional; `groups` conditional for `group_aggregated` | Upstream does not consume groups; ANDREA aggregation does | Certain. |
| `artifacts_aux` | `kscreni.log`, `raw/kscreni_networks.rds` | kScReNI returns a raw per-cell matrix list; upstream prints Seurat/GENIE3 diagnostics | Implemented and validated by smoketest. |
| `outputs` | directed, sign none, association | Paper directed/regulatory-strength statement; GENIE3 weights are ranking weights (`papers/ScReNI.txt:230-238`, GENIE3 vignette) | Certain for selected output; no sign available. |
| `progress` | `none` with coarse lifecycle note | Public entrypoint hides foreach loop and exposes no callback (`R/Infer_kScReNI_scNetworks.R:34-44`) | Implemented coarse `progress.json` lifecycle states: init, load_input, inference, write_output, done/failed. |
| `params` | `nfeatures`, `knn` | Function signature and use sites (`R/Infer_kScReNI_scNetworks.R:17`, `R/Infer_kScReNI_scNetworks.R:27-29`, `R/Infer_kScReNI_scNetworks.R:38`) | Behavior-changing public params; `nthread` and fixed/hard-coded settings are excluded. |

## Implemented Wrapper Behavior

- Runtime contract: generated `run_tool.sh` calls `Rscript /app/run_tool.R` with `--input`, `--params`, `--extra`, `--output-dir`, and `--threads`.
- Execution modes: accepts `cell_native` and `group_aggregated`; defaults to `cell_native` when `execution.json` is absent.
- Group handling: when `execution.mode=group_aggregated`, the wrapper requires and validates `groups.tsv` against expression columns, but still emits `cell:<cell_id>` contexts. ANDREA core owns the logical group aggregation.
- Parameter handling: requires resolved `nfeatures` and `knn`; rejects invalid integer values and rejects `knn + 1 > number_of_cells`.
- Input validation: requires unique non-empty gene and cell IDs, finite numeric non-negative expression values, at least 2 genes and 2 cells.
- Upstream behavior: mirrors `ScReNI::Infer_kScReNI_scNetworks(exprMatrix=expr, nfeatures=params$nfeatures, knn=params$knn, nthread=threads)` and preserves the same Seurat preprocessing, SNN neighbor extraction, outer foreach worker pool, `set.seed(100)`, and `GENIE3(..., nCores=1, nTrees=100)` calls. The function body is inlined only to pass a safe data-dependent `npcs` to `Seurat::RunPCA()`.
- PCA guard: uses `npcs = min(50, length(VariableFeatures(pbmc)), ncol(expr) - 1, nrow(expr) - 1)`. This fixes GUI datasets with exactly 50 cells where Seurat's default `npcs=50` reaches `irlba`'s strict bound and fails with `max(nu, nv) must be strictly less than min(nrow(A), ncol(A))`.
- Output conversion: writes `network.csv` with original gene IDs, `score` as raw positive upstream weights, `sign="?"`, `evidence="association"`, and `context="cell:<cell_id>"`; filters self-loops and non-positive/non-finite scores.
- Auxiliary artifacts: writes `kscreni.log` and `raw/kscreni_networks.rds`.
- Identifier aliases: no aliasing is introduced; wrapper errors if upstream per-cell list names do not match expression cell IDs exactly.

## Smoketest Outcome

- Fixture: `wrappers/inference_tools/tests/fixtures/kscreni/expression.tsv` has 15 cells and 15 variable non-negative genes so Seurat's default neighbor dimensions are available; `groups.tsv` maps the same 15 cells to two groups.
- Config: `nfeatures=15`, `knn=2`; variants cover `cell_native` and `group_aggregated`.
- Final command: `make run-tool-smoketests ARGS="--tool kscreni --threads 2 --timeout 2400 --show-output-lines 20"`.
- Result: passed.
- Output checks: each variant produced `network.csv` with 3081 positive non-self rows, final `progress.json` status `completed`, and required auxiliary artifacts `kscreni.log` plus `raw/kscreni_networks.rds`.
- Notes: Seurat emits expected small-fixture warnings from `FindVariableFeatures()`/feature counts; these do not affect smoke validation.

## GUI Regression 2026-06-17

- Failing GUI run inspected: `inferred_networks/gui_dataset_20260617T124221Z`.
- Failure: both `kscreni__01` and `kscreni__02/upstream_cell_native` failed in `Seurat::RunPCA()` with `max(nu, nv) must be strictly less than min(nrow(A), ncol(A))` on a 137 gene x 50 cell expression matrix.
- Cause: upstream kScReNI does not pass `npcs`, so Seurat's default `npcs=50` hits the exact cell-count boundary.
- Fix: wrapper now preserves the kScReNI public behavior but sets safe `RunPCA(npcs=49)` for that dataset via the data-dependent PCA guard above.
- Verification: rebuilt `adriansegura99/inference-tools_kscreni:1.0.0` and reran the GUI `kscreni__01` input directly in Docker; the run completed and wrote 610353 positive non-self cell-context edges. The same output aggregates with the GUI `groups.tsv` into 95328 group-context edges across 7 groups.

## Known Limitations / Open Questions

- wScReNI is not exposed because the current normalized input catalog cannot express its full gene-peak/motif/genome/neighbor-index contract.
- kScReNI can produce dense per-cell gene x gene outputs; large datasets may produce very large raw and `network.csv` artifacts.
- The upstream `nTrees` argument is present but ignored by the implementation; this should be revisited only if upstream fixes the function or a wrapper deliberately patches the public call.
- No package-manager release was found. The implementation uses the pinned GitHub commit.
