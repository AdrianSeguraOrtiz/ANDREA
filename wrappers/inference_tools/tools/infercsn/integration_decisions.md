# infercsn Integration Decisions

## Sources Reviewed

- Local upstream repo: `wrappers/inference_tools/tools/infercsn/repo/inferCSN/`
- Local paper: `wrappers/inference_tools/tools/infercsn/papers/s41540-025-00564-4.pdf`
- Extracted paper text: `wrappers/inference_tools/tools/infercsn/papers/s41540-025-00564-4.txt`
- Official CRAN package page: `https://CRAN.R-project.org/package=inferCSN`
- Official CRAN source tarball inspected during Phase 1: `https://cran.r-project.org/src/contrib/inferCSN_1.2.0.tar.gz`

## Paper Preparation

- PDF inputs found:
  - `wrappers/inference_tools/tools/infercsn/papers/s41540-025-00564-4.pdf`
- Extracted text files used for analysis:
  - `wrappers/inference_tools/tools/infercsn/papers/s41540-025-00564-4.txt`
- Extraction quality / problems:
  - Usable for DOI, title, authors, abstract and methods. Some two-column method text is interleaved, so key claims were checked against nearby context rather than relying on a single extracted paragraph.

## Method Summary

inferCSN is described as a cell type and cell state specific GRN method for scRNA-seq data. The paper describes pseudotime/state windowing followed by sparse L0/L2 regression of target genes on TFs and optional reference-network calibration. The CRAN package interface selected for this integration exposes the sparse-regression network inference core as `inferCSN::inferCSN(object, ...)`, returning a regulator-target-weight table, and also exposes `network_sift()` as a public post-inference filtering mode using either reciprocal maximum weight or pseudotime transfer entropy.

## Upstream Interface Boundary

- Chosen public entrypoints:
  - Required inference step: `inferCSN::inferCSN()` for `matrix` / `sparseMatrix` inputs.
  - Optional post-inference filtering step: `inferCSN::network_sift()`.
- Evidence:
  - README shows installation from CRAN and basic usage `network <- inferCSN(example_matrix)`: `repo/inferCSN/README.md`.
  - CRAN 1.2.0 `R/inferCSN.R` defines the exported S4 generic and methods for `matrix`, `sparseMatrix` and `data.frame`.
  - `man/inferCSN.Rd` documents the return value as columns `regulator`, `target` and `weight`.
  - CRAN 1.2.0 `R/network_sift.R` / `man/network_sift.Rd` documents `network_sift(network_table, matrix, meta_data, pseudotime_column, method = c("entropy", "max"), ...)`.
- Rationale:
  - `inferCSN()` is the narrowest public upstream function that maps an expression matrix to a GRN table and preserves method scores.
  - `network_sift()` is an exported public mode of the package and should be exposed so the wrapper is not artificially limited to the raw `inferCSN()` table.
  - `single_network()` and `fit_srm()` are lower-level pieces of the same algorithm. They are useful evidence but are not the primary integration boundary.
  - Visualization functions such as `plot_dynamic_networks()` are not inference modes. Their examples manually add a `celltype` column to an already inferred network table, so they do not provide native grouped inference.
- Confidence:
  - High for `inferCSN()` plus optional `network_sift()` as the current CRAN public inference/filtering contract.
  - High that the package does not expose a public one-call native grouped GRN mode in CRAN 1.2.0.

## ToolSpec Evidence Ledger

### `schema_version`

- Chosen value: `1.0`
- Evidence: `andrea/catalog_inference_tools/schemas/toolspec.schema.json`
- Rationale: Project ToolSpec schema constant.
- Confidence / ambiguity: High / none.

### `id`

- Chosen value: `infercsn`
- Evidence: local scaffold directories `wrappers/inference_tools/tools/infercsn/` and `andrea/catalog_inference_tools/tools/infercsn/`
- Rationale: Stable lowercase tool id from scaffold.
- Confidence / ambiguity: High / none.

### `name`

- Chosen value: `inferCSN`
- Evidence:
  - README title and package links use `inferCSN`.
  - CRAN page title: `inferCSN: Inferring Cell-Specific Gene Regulatory Network`.
  - Paper abstract names the method `inferCSN`: `papers/s41540-025-00564-4.txt:27-35`.
- Rationale: Official package/method spelling preserves case.
- Confidence / ambiguity: High / none.

### `publication`

- Chosen value: `["https://doi.org/10.1038/s41540-025-00564-4"]`
- Evidence: DOI in primary paper text: `papers/s41540-025-00564-4.txt:7`.
- Rationale: This is the primary method paper provided locally. The CRAN package DOI is a software-package DOI, not the primary method publication.
- Confidence / ambiguity: High / none.

### `first_author`

- Chosen value: `Xiong Li`
- Evidence: primary paper author line starts with Xiong Li: `papers/s41540-025-00564-4.txt:14-16`.
- Rationale: ToolSpec requires the full first author name for the primary publication.
- Confidence / ambiguity: High / none.

### `year`

- Chosen value: `2025`
- Evidence:
  - Primary paper DOI page text and footer show `npj Systems Biology and Applications | (2025)11:94`: `papers/s41540-025-00564-4.txt:66`.
  - CRAN page lists package publication in 2025, but the ToolSpec year is based on the method paper.
- Rationale: Year of the primary method paper.
- Confidence / ambiguity: High / none.

### `method_summary`

- Chosen value: summary of scRNA-seq cell type/state GRN inference using pseudotime/state information, sparse regression, and optional public `network_sift()` filtering.
- Evidence:
  - Abstract: scRNA-seq, pseudotime ordering, windows and sparse regression with reference network information: `papers/s41540-025-00564-4.txt:27-36`.
  - Workflow: pseudotime/GAM, windows, sparse L0/L2 regression and reference network calibration: `papers/s41540-025-00564-4.txt:142-145`.
  - CRAN DESCRIPTION: package infers cell-type specific GRNs from single-cell RNA-seq data.
  - CRAN 1.2.0 `R/inferCSN.R`: `inferCSN()` exposes sparse-regression penalties and returns `regulator,target,weight`.
  - CRAN 1.2.0 `R/network_sift.R`: `network_sift()` exposes `method = "max"` and `method = "entropy"` post-inference filtering.
- Rationale: Describes the method from the paper while clarifying the public CRAN interfaces that the wrapper will mirror.
- Confidence / ambiguity: Medium. The paper describes a broader pseudotime/window/reference-network pipeline than the current CRAN `inferCSN()` function exposes directly.

### `method_keywords`

- Chosen value: `single_cell`, `sparse_regression`, `l0_regularization`, `pseudotime`, `transfer_entropy`, `cell_state`, `directed`
- Evidence:
  - Single-cell and cell state specificity: `papers/s41540-025-00564-4.txt:27-35`.
  - Sparse regression and L0/L2 model: `papers/s41540-025-00564-4.txt:142-145`, `papers/s41540-025-00564-4.txt:617-640`.
  - Directed signed examples: `papers/s41540-025-00564-4.txt:209-212`, `papers/s41540-025-00564-4.txt:461-463`.
  - Transfer entropy mode: CRAN 1.2.0 `network_sift()` calls `RTransferEntropy::transfer_entropy` when `method="entropy"`.
- Rationale: Captures the main method family and the public pseudotime filtering mode.
- Confidence / ambiguity: High.

### `implementation_url`

- Chosen value: `https://CRAN.R-project.org/package=inferCSN`
- Evidence:
  - User clarification requests official CRAN package installation.
  - CRAN page lists `inferCSN` 1.2.0 and R `>= 4.1.0`.
  - README documents `install.packages("inferCSN")`.
- Rationale: Official package source is preferred by the playbook. Runtime should install CRAN `inferCSN` pinned to 1.2.0.
- Confidence / ambiguity: High. The local repo currently reports 1.2.3 in `DESCRIPTION`, but that is not the CRAN release selected for runtime.

### `docker_image`

- Chosen value: `adriansegura99/inference-tools_infercsn:1.0.0`
- Evidence: Existing wrapper catalog naming convention.
- Rationale: Matches the Docker image convention used by other inference-tool wrappers.
- Confidence / ambiguity: High / none.

### `execution_capabilities`

- Chosen value: `["group_emulated"]`
- Evidence:
  - CRAN 1.2.0 `inferCSN()` accepts one expression matrix and returns one network table.
  - No reviewed public CRAN entrypoint natively consumes group/task metadata and returns one network per group in a single run.
  - ANDREA can emulate grouped execution by partitioning expression columns by `groups.tsv` before invoking the wrapper independently for each group.
  - Paper motivation emphasizes avoiding GRNs built from mixed cell types/states: `papers/s41540-025-00564-4.txt:19-35`.
- Rationale:
  - `group_emulated`: the correct ANDREA execution mode for inferCSN because the package API operates on one already selected cell population, while the method is designed for cell type/state specific networks.
  - `global` is intentionally not exposed. Under the ANDREA catalog semantics, `global` means one network over the full expression dataset; for heterogeneous scRNA-seq inputs this conflicts with inferCSN's method motivation.
  - A homogeneous one-population dataset remains representable by providing `groups.tsv` with all cells assigned to one group; the orchestrator will produce one group-emulated run.
  - Not `group_native`: the current public package API does not natively consume `groups.tsv` or equivalent cell group metadata for multitask/group network inference.
- Confidence / ambiguity: High for CRAN 1.2.0; medium relative to the broader paper workflow, which describes state/window-specific networks but does not expose that as a public package function that takes grouped metadata and returns multiple networks.

### `accepts`

- Chosen value: `["cells"]`
- Evidence:
  - README: package for cell-specific GRNs from single-cell RNA data.
  - CRAN DESCRIPTION: cell-type specific GRNs from single-cell RNA-seq data.
  - Paper abstract: method based on scRNA-seq data.
- Rationale: ANDREA expression columns should represent cells for this tool. Although the R function uses the generic word "samples", method assumptions are scRNA-specific.
- Confidence / ambiguity: High.

### `assumes`

- Chosen value: `scrna_specific`
- Evidence:
  - README and CRAN DESCRIPTION explicitly identify single-cell RNA data.
  - Paper abstract and introduction motivate the method around scRNA-seq heterogeneity, cell type and cell state.
- Rationale: The method materially depends on single-cell context rather than being a generic bulk/cohort regression wrapper.
- Confidence / ambiguity: High.

### `extra_inputs`

- Chosen value:
  - `required`: `["groups"]`
  - `optional`: `["pseudotime", "tf_list"]`
  - `conditional_required`: require `pseudotime` when `sift_method == "entropy"`
- Evidence:
  - CRAN 1.2.0 `inferCSN()` has optional `regulators` and `targets`; when omitted it uses matrix gene names.
  - Paper states inferCSN supports specifying TF lists and this reduces computational complexity: `papers/s41540-025-00564-4.txt:221`.
  - CRAN 1.2.0 `network_sift()` requires `matrix`, `meta_data` and `pseudotime_column` for entropy mode; if any are missing it warns and falls back to `method="max"`.
  - `group_emulated` execution requires `groups.tsv` so ANDREA can partition the full expression dataset before invoking inferCSN.
- Rationale:
  - `groups` is required because inferCSN is exposed only as `group_emulated`; the wrapper itself does not pass groups to inferCSN, but ANDREA needs them to create per-group expression matrices.
  - `tf_list` maps to upstream `regulators`; if omitted, the wrapper omits `regulators` and preserves the upstream data-dependent all-gene regulator default.
  - `pseudotime` maps to `network_sift(meta_data=..., pseudotime_column="pseudotime")` and is required only for the `entropy` post-filtering mode.
  - No `prior_grn` is included because CRAN `inferCSN()` does not expose a reference-network file argument despite the broader paper workflow.
- Confidence / ambiguity: High for CRAN API. Medium for excluding reference network because the paper includes it, but the selected public package entrypoints do not.

### `outputs`

- Chosen value: `{"directed": true, "sign": "signed", "evidence": "association"}`
- Evidence:
  - `inferCSN()` returns `regulator`, `target`, `weight`.
  - `single_network()` fits each target gene from candidate regulators and returns regulator-target rows.
  - `network_format(abs_weight = FALSE)` preserves signed weights and filters zeros.
  - `network_sift()` returns the same three-column regulator-target-weight shape after filtering.
  - Paper examples distinguish negative and positive regulation: `papers/s41540-025-00564-4.txt:209-212`, `papers/s41540-025-00564-4.txt:461-463`.
- Rationale:
  - Directed because each row is regulator -> target.
  - Signed because the selected CRAN path preserves coefficient sign.
  - Association because the exported score remains the sparse-regression `weight` magnitude. Entropy mode uses pseudotime transfer entropy as an optional edge filter but does not replace the exported score with an entropy score.
- Confidence / ambiguity: Medium-high. If the schema later supports parameter-dependent evidence, `sift_method="entropy"` could be annotated as adding pseudotime-filtered evidence.

### `progress`

- Chosen value: `kind = "none"` with coarse lifecycle note.
- Evidence:
  - CRAN 1.2.0 `inferCSN()` internally maps over target genes, but the public entrypoint returns only after the run and does not expose a stable callback.
  - `network_sift(method="entropy")` internally maps over gene pairs, but it also lacks a documented progress callback.
  - Logging is handled through `thisutils::log_message()` / `parallelize_fun()`, but no documented progress counter is guaranteed.
- Rationale: The wrapper should still write `progress.json`, but only coarse states are reliable while mirroring the selected public entrypoints.
- Confidence / ambiguity: Medium.

### `runtime_resources.threading`

- Chosen value:
  - `supported`: `true`
  - `default_threads`: `1`
  - `max_threads`: `8`
  - `upstream_mapping`: wrapper maps ANDREA `--threads` to
    `inferCSN::inferCSN(cores=threads)`, `inferCSN::network_sift(cores=threads)`
    for `sift_method="max"`, and `parallel::mclapply(mc.cores=threads)` in the
    entropy compatibility path.
- Evidence:
  - `wrappers/inference_tools/tools/infercsn/run_tool.R` passes `threads` to
    `inferCSN()` as `cores`, to `network_sift()` as `cores`, and to entropy
    sifting as `mc.cores`.
  - `wrappers/inference_tools/tools/infercsn/repo/inferCSN/man/inferCSN.Rd`
    documents `cores` as the number of cores used for parallelization with
    `foreach::foreach`.
  - `wrappers/inference_tools/tools/infercsn/repo/inferCSN/R/inferCSN.R`
    passes validated `cores` into `thisutils::parallelize_fun()`.
  - `andrea/catalog_inference_tools/tools/infercsn/cost.json` benchmarks
    thread values `1`, `2`, `4` and `8`.
- Rationale:
  - This is a real upstream CPU control, so it belongs in
    `runtime_resources.threading` rather than `params`.
  - The wrapper pins common BLAS/OpenMP environment variables to one thread
    before loading R packages to avoid nested parallelism outside the
    planner-assigned `cores`.
- Confidence / ambiguity: High for `inferCSN(cores=...)` and
  `network_sift(cores=...)`; medium for the exact scaling benefit because it
  depends on the number of target genes or entropy gene pairs available in a
  physical group run.
- Uncertainty: upstream does not publish a fixed hard maximum core count;
  `max_threads=8` is the current ANDREA planning cap because it is the largest
  value covered by the checked-in cost profile.

### `params`

- Chosen values:
  - `penalty`: enum `L0`, `L0L1`, `L0L2`; default `L0`.
  - `cross_validation`: bool; default `false`.
  - `seed`: int; default `1`.
  - `n_folds`: int; default `5`; min `2`.
  - `subsampling_method`: enum `sample`, `meta_cells`, `pseudobulk`; default `sample`.
  - `subsampling_ratio`: float `(0, 1]`; default `1.0`.
  - `r_squared_threshold`: float `[0, 1]`; default `0.0`.
  - `sift_method`: enum `none`, `max`, `entropy`; default `none`.
  - `entropy_method`: enum `Shannon`, `Renyi`; default `Shannon`.
  - `effective_entropy`: bool; default `false`.
  - `shuffles`: int; default `100`; min `0`.
  - `entropy_nboot`: int; default `300`; min `0`.
  - `lag_value`: int; default `1`; min `1`.
  - `entropy_p_value`: float `[0, 1]`; default `0.05`.
- Evidence:
  - CRAN 1.2.0 `R/inferCSN.R` signature and roxygen docs.
  - CRAN 1.2.0 `R/subsampling.R` validates `subsampling_ratio` and uses `match.arg()` for subsampling method.
  - CRAN 1.2.0 `R/single_network.R` applies `r_squared_threshold` and unit-vector coefficient normalization.
  - CRAN 1.2.0 `R/sparse_regression_model.R` passes `penalty`, `cross_validation`, `seed` and `n_folds` to `L0Learn`.
  - CRAN 1.2.0 `R/network_sift.R` defines `method`, `entropy_method`, `effective_entropy`, `shuffles`, `entropy_nboot`, `lag_value` and `entropy_p_value`.
- Rationale:
  - These are the stable, documented top-level method and public filtering parameters that affect inference or retained edges.
  - `sift_method="none"` is a wrapper-level option that preserves the raw `inferCSN()` result; `max` and `entropy` map to upstream `network_sift(method=...)`.
  - `cores` is not a ToolSpec param; the wrapper maps ANDREA runtime threads to upstream `cores`.
  - `verbose` is runtime logging, not biological/method behavior.
  - `targets` is not exposed because ANDREA currently has no normalized target-gene-list input and GUI array-param support is not established; the wrapper preserves the upstream default of all genes as targets.
  - Low-level `...` / `L0Learn` options are not exposed because they are not a stable documented infer-network contract.
- Confidence / ambiguity: High for listed defaults; medium for not exposing `targets`.

### `artifacts_aux`

- Chosen value:
  - `infercsn.log`
  - `raw/infercsn_inferred_network.tsv`
  - `raw/infercsn_network.tsv`
- Evidence:
  - CRAN `inferCSN()` returns a raw upstream regulator-target-weight table.
  - CRAN `network_sift()` returns a filtered regulator-target-weight table with the same score column.
  - Existing wrapper conventions preserve combined logs and raw upstream outputs for debugging.
- Rationale:
  - `raw/infercsn_inferred_network.tsv` preserves the direct `inferCSN()` output before optional sifting.
  - `raw/infercsn_network.tsv` preserves the final upstream table after `sift_method`.
  - `network.csv` is the standardized ANDREA output.
- Confidence / ambiguity: Medium. Exact filenames are wrapper-contract decisions, but they are consistent with existing integrations.

## Dynamic Defaults and Runtime-Dependent Rules

- `regulators`: upstream default is data-dependent. If `regulators` is omitted, inferCSN uses the expression matrix gene names as candidate regulators. ToolSpec preserves this by making `tf_list` optional; the wrapper omits the `regulators` argument when `tf_list.txt` is absent.
- `targets`: upstream default is data-dependent. If `targets` is omitted, inferCSN uses all expression matrix gene names as targets. ToolSpec preserves this by not exposing a target-list input; the wrapper omits `targets`.
- `subsampling_method`: upstream R signature lists multiple choices; `match.arg()` selects the first value, `sample`, when no value is provided. ToolSpec records default `sample`.
- `subsampling_ratio`: upstream returns the full matrix immediately when ratio is `>= 1`; otherwise it samples/aggregates approximately `round(nrow(matrix) * ratio)` cells. ToolSpec records default `1.0` to preserve no subsampling.
- `sift_method`: wrapper default `none` intentionally preserves raw `inferCSN()` output. If set to `max`, the wrapper calls `network_sift(method = "max")`; if set to `entropy`, the wrapper mirrors the documented transfer-entropy path with the compatibility fix described below.
- `network_sift(method="entropy")`: upstream requires `matrix`, `meta_data` and `pseudotime_column`; if they are missing, it warns and falls back to `method="max"`. ToolSpec prevents the silent fallback by making `pseudotime` conditionally required when `sift_method="entropy"`.
- `network_sift()` shuffles: if `effective_entropy=false` and `shuffles != 0`, upstream resets `shuffles` to `0`; if `effective_entropy=true` and `shuffles <= 10`, upstream resets it to `10`.
- `network_sift()` p-value filtering: upstream filters transfer-entropy rows by `entropy_p_value` only when `entropy_nboot > 1`.
- `cores`: upstream default is `1`, but the wrapper passes the runtime thread count to `cores` rather than exposing it as a biological method parameter.

## Normalized Input Mapping

### Reused input specs

- `expression_matrix`: ANDREA expression TSV is genes x cells. CRAN inferCSN expects genes as matrix columns and cells/samples as rows, so the wrapper transposes before calling `inferCSN()`.
- `tf_list`: optional regulator list. Map to upstream `regulators` after intersecting/validating against expression genes.
- `groups`: required for ANDREA `group_emulated` orchestration. The wrapper does not pass groups to inferCSN; each physical subrun receives an expression matrix already subset to one group.

### New input specs required

- `pseudotime`: created at `andrea/catalog_inference_tools/input_specs/pseudotime.json`.
  - Semantics: first column maps expression cell identifiers to a numeric `pseudotime` value.
  - Required only when `sift_method="entropy"`.
  - Cross-checks require a one-to-one mapping to expression columns.

## Output Mapping to `network.csv`

- Upstream inference output: table columns `regulator`, `target`, `weight`.
- Optional upstream filtering:
  - `sift_method="none"`: final table is the raw `inferCSN()` output.
  - `sift_method="max"`: final table is `network_sift(method="max")`, which removes weaker reciprocal directions using absolute weight.
  - `sift_method="entropy"`: final table follows the documented `network_sift(method="entropy")` algorithm, filtering the inferred network using pseudotime transfer entropy while preserving the original `weight` column. The wrapper implements a compatibility fix for CRAN 1.2.0 because the public function builds an internal transfer-entropy table with column `entropy` and then calls `weight_sift()`, which expects column `weight`.
- ANDREA mapping:
  - `source` = upstream `regulator`
  - `target` = upstream `target`
  - `score` = absolute magnitude of final upstream `weight`
  - `sign` = derived from the sign of `weight`
  - execution context/group = provided by ANDREA orchestration for `group_emulated`.
- Score policy:
  - Preserve raw upstream final `weight` magnitude directly. Do not apply ANDREA-specific normalization.
  - Because the ANDREA `network.csv` contract stores direction only in `sign`, signed upstream coefficients are converted to `score=abs(weight)`.
  - The selected upstream interface defines the score scale: per-target sparse-regression coefficients normalized by `thisutils::normalization(method = "unit_vector")`, with optional post-filtering by `network_sift()`.
  - Zero-score edges are not written. Upstream `network_format()` already removes exact zero weights; the wrapper keeps the same invariant defensively.

## Installation Strategy

- Preferred public installation source:
  - CRAN package `inferCSN` pinned to version `1.2.0`.
  - Requires R `>= 4.1.0`.
  - Evidence: user clarification, CRAN package page and CRAN 1.2.0 `DESCRIPTION`.
- Fallback pinned source if needed:
  - CRAN source tarball `https://cran.r-project.org/src/contrib/inferCSN_1.2.0.tar.gz` while 1.2.0 is current.
  - If CRAN later moves 1.2.0 to Archive, the runtime should pin to the corresponding CRAN Archive tarball or use `remotes::install_version("inferCSN", version = "1.2.0", repos = "https://cloud.r-project.org")`.
- Local repo note:
  - The local cloned repo reports version `1.2.3`, but runtime must not depend on `repo/`, and CRAN/manual context identify 1.2.0 as the official package version for this integration phase.
  - The local repo NEWS says `network_sift()` and `weight_sift()` were removed in 1.2.2, which is another reason the runtime pins 1.2.0 while `sift_method` is part of the wrapper contract.

## Implemented Wrapper Behavior

- Runtime wrapper: `wrappers/inference_tools/tools/infercsn/run_tool.R`.
- Dockerfile: `wrappers/inference_tools/tools/infercsn/Dockerfile`.
- Runtime installation:
  - `rocker/r-ver:4.4.1`.
  - `remotes::install_version("inferCSN", version = "1.2.0", repos = "https://cloud.r-project.org", dependencies = c("Depends", "Imports", "LinkingTo"), upgrade = "never")`.
  - `RTransferEntropy` installed explicitly because CRAN `network_sift(method="entropy")` uses it but lists it under `Suggests`.
  - No runtime dependency on local `repo/`.
- Execution mode:
  - Wrapper accepts only `execution.mode="group_emulated"`; missing `execution.json` defaults to `group_emulated`.
  - Wrapper requires `/io/extra/groups.tsv` to reflect the catalog contract, but it does not pass groups to inferCSN. The ANDREA orchestrator partitions expression by group before each physical invocation and overwrites physical output context as `group:<label>` in the parent grouped network.
- Input mapping:
  - Reads `/io/expression.tsv` as ANDREA genes x cells and transposes to inferCSN cells x genes.
  - Optional `/io/extra/tf_list.txt` is validated against expression genes and passed as upstream `regulators`.
  - `targets` is omitted to preserve the upstream all-gene target default.
  - `/io/extra/pseudotime.tsv` is required only for `sift_method="entropy"` and is subset/reordered to the current physical expression cells. The wrapper adds an inert metadata column before entropy sifting because CRAN 1.2.0 drops single-column metadata to a vector internally.
- Parameter mapping:
  - `penalty`, `cross_validation`, `seed`, `n_folds`, `subsampling_method`, `subsampling_ratio`, `r_squared_threshold` map directly to `inferCSN()`.
  - `sift_method="none"` skips post-filtering.
  - `sift_method="max"` calls `inferCSN::network_sift(method="max")`.
  - `sift_method="entropy"` mirrors the CRAN 1.2.0 transfer-entropy implementation with the minimal column-name compatibility fix described above, then preserves the original inferCSN regression `weight` in the final table.
  - Runtime `--threads` is passed to upstream `cores` where applicable and to
    `parallel::mclapply(mc.cores=threads)` in the entropy compatibility path.
  - Common BLAS/OpenMP environment variables are pinned to one thread before R
    packages load so the upstream `cores` control remains the only planned CPU
    parallelism.
- Output mapping:
  - Writes coarse lifecycle `progress.json`.
  - Writes `infercsn.log`.
  - Writes `raw/infercsn_inferred_network.tsv` before optional sifting.
  - Writes `raw/infercsn_network.tsv` after the selected `sift_method`.
  - Writes `network.csv` with `source=regulator`, `target=target`, raw magnitude `score=abs(weight)`, `sign` from weight sign, `evidence=association`, and physical-run `context=global`.
  - Filters exact zero scores and self-loops defensively.

## Smoketest

- Smoketest config: `wrappers/inference_tools/tests/smoketest_configs/infercsn.json`.
- Shared fixture added: `wrappers/inference_tools/tests/fixtures/pseudotime.tsv`.
- Smoketest uses only `execution.mode="group_emulated"`, because `infercsn` exposes no catalog `global` mode.
- Variants:
  - `raw`: `sift_method="none"` with `groups.tsv` and `tf_list.txt`.
  - `max_sift`: `sift_method="max"` with `groups.tsv` and `tf_list.txt`.
  - `entropy_sift`: `sift_method="entropy"` with `groups.tsv`, `tf_list.txt`, `pseudotime.tsv`, `shuffles=0` and `entropy_nboot=0` to keep the smoke run fast and deterministic.
- Result:
  - Command: `python wrappers/inference_tools/scripts/run_smoketests.py --tool infercsn --timeout 900`
  - Outcome: passed on 2026-05-01.
  - Rows validated: `raw` 15 rows, `max_sift` 12 rows, `entropy_sift` 15 rows.
  - Validated auxiliary artifacts: `infercsn.log`, `raw/infercsn_inferred_network.tsv`, `raw/infercsn_network.tsv`.

## Known Limitations

- The paper's full pseudotime/window/reference-network workflow is broader than CRAN `inferCSN()` 1.2.0. This integration mirrors the public CRAN inference and post-filtering functions rather than reconstructing unpublished glue around paper figures.
- `sift_method="entropy"` adds pseudotime-based filtering, but the static ToolSpec `outputs.evidence` field remains `association` because the exported score is still the sparse-regression `weight` magnitude.
- CRAN 1.2.0 `network_sift(method="entropy")` has a column-name bug in its internal call to `weight_sift()`. The wrapper keeps the documented transfer-entropy behavior with a local compatibility fix rather than silently disabling the entropy mode.
- `global` is intentionally not exposed. Users who already have a single homogeneous cell population should provide a `groups.tsv` with one group so the execution still follows the grouped contract.
- `targets` is not exposed. The wrapper will infer all target genes, matching upstream defaults.
- The local repo version is ahead of CRAN and removed `network_sift()` in 1.2.2. The runtime pins `inferCSN` 1.2.0 to keep this contract valid.
