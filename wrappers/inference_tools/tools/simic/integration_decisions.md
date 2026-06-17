# simic Integration Decisions

## Sources Reviewed

- Upstream repo: `wrappers/inference_tools/tools/simic/repo/SimiC/`
- Upstream commit in local clone: `fa47e69e5c793bce1500dc35283e112cd8f51992`
- Local paper PDF: `wrappers/inference_tools/tools/simic/papers/s42003-022-03319-7.pdf`
- Extracted paper text: `wrappers/inference_tools/tools/simic/papers/s42003-022-03319-7.txt`
- Key repo evidence:
  - `README.md`
  - `Tutorial/README.md`
  - `example/SimiC_example.py`
  - `code/simiclasso/clus_regression.py`
  - `code/simiclasso/common_io.py`
  - `code/simiclasso/weighted_AUC_mat.py`
  - `setup.py`, `requirements.txt`, upstream `Dockerfile`
  - Catalog schema/input changes for this integration:
    - `andrea/catalog_inference_tools/input_specs/cell_phenotypes.json`
    - `andrea/catalog_inference_tools/schemas/toolspec.schema.json`
    - `andrea/catalog_inference_tools/schemas/dataset-manifest.schema.json`

## Paper Preparation

- PDF inputs found:
  - `wrappers/inference_tools/tools/simic/papers/s42003-022-03319-7.pdf`
- Extracted text files used for analysis:
  - `wrappers/inference_tools/tools/simic/papers/s42003-022-03319-7.txt`
- Extraction quality / problems:
  - Usable. The extracted text preserves title, DOI, abstract and methods, but two-column sections are interleaved, so line-level evidence was cross-checked against repo examples and source code.

## Method Summary

SimiC is a single-cell GRN inference framework for ordered cell phenotypes. It jointly fits one driver-gene-to-target-gene incidence matrix per phenotype using a fused LASSO objective with an L1 sparsity term and a similarity penalty between consecutive phenotype networks, then computes regulon activity / weighted AUC matrices from the inferred weights.

## Upstream Interface

### Chosen public entrypoint

- Primary inference entrypoint: `simiclasso.clus_regression.simicLASSO_op`.
- Auxiliary activity-score entrypoint: `simiclasso.weighted_AUC_mat.main_fn`.
- Evidence:
  - `README.md:23-30` directs Python users to the example workflow and says the default output contains 3 GRNs.
  - `README.md:35-36` says the test run outputs `incident_matrices` and `wAUC_matrices`.
  - `Tutorial/README.md:34-48` and `Tutorial/README.md:86-110` import and call `simicLASSO_op` followed by `main_fn`.
  - `example/SimiC_example.py:13-40` uses the same two functions.
  - `clus_regression.py:410-433` documents `simicLASSO_op` as the GRN inference algorithm and says it saves the weight dictionary and gene list.
  - `weighted_AUC_mat.py:156-173` defines `main_fn` for weighted AUC matrix output.
- Rationale:
  - This is the documented Python package workflow and is narrower than notebooks/R post-analysis scripts.
  - `direct_regression.py` contains an undocumented global MultiTaskLasso script with hard-coded data paths; it is not the SimiC public workflow and is not exposed as an ANDREA execution capability.
- Confidence:
  - High for `simicLASSO_op` + `main_fn`.
  - Medium that the undocumented clustering fallback in `simicLASSO_op` should remain out of the wrapper contract; the tutorial says the correct run requires an assignment file.

### Runtime resources

- ToolSpec value: `runtime_resources.threading.supported=true`,
  `default_threads=1`, `max_threads=8`.
- Evidence:
  - The selected public Python workflow uses NumPy/SciPy/scikit-learn
    operations in `simiclasso.clus_regression`, including matrix products
    (`X_i @ W_i`, `X_i.T @ X_i`), norms and `scipy.linalg.eigh`.
  - `weighted_AUC_mat.main_fn` also performs NumPy/Pandas post-processing over
    inferred weight matrices.
  - Upstream does not expose `n_jobs`, worker, process or explicit thread
    parameters in `simicLASSO_op` or `main_fn`.
- Wrapper mapping:
  - ANDREA `--threads` is applied before importing NumPy, pandas, SciPy,
    scikit-learn or `simiclasso`.
  - The wrapper sets `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`,
    `MKL_NUM_THREADS`, `NUMEXPR_NUM_THREADS`, `BLIS_NUM_THREADS` and
    `VECLIB_MAXIMUM_THREADS` to the assigned thread count.
  - No thread-like control is exposed as a normal ToolSpec parameter.
- Cost status: existing `cost.json` already covers runtime points for
  `threads` 1, 2, 4 and 8, so `max_threads=8` matches the measured matrix.
- Limitation: this is backend-level CPU parallelism, not algorithm-level
  sharding. Some SimiC phases are Python loops and may not scale with threads.

### Execution capabilities

- Chosen value: `["group_native"]`.
- Evidence:
  - Paper abstract says SimiC jointly infers distinct but related gene regulatory dynamics per phenotype (`s42003-022-03319-7.txt:20-29`).
  - Paper introduction says SimiC takes driver genes, cell labels/phenotypes and ordering information and produces a GRN for each phenotype (`s42003-022-03319-7.txt:66-72`).
  - Figure text says the objective includes phenotype-indexed matrices and outputs one incidence matrix per phenotype (`s42003-022-03319-7.txt:125-131`).
  - Methods define `[W1, ..., WK]`, one incidence matrix per state, with shared driver/target nodes and state-specific edge weights (`s42003-022-03319-7.txt:2672-2690`, `2732-2738`).
  - README says the default test output contains 3 GRNs from a dataset with 3 states (`README.md:23-30`).
- Rationale:
  - Native grouped output is produced by one upstream SimiC run, not by ANDREA partitioning cells and running a global method independently.
  - `similarity=false`, `lambda2=0`, and `cross_val=true` are parameter choices inside the native grouped workflow, not separate execution capabilities.
  - No `global` capability is exposed because the documented SimiC method is built around per-phenotype networks.
  - No `group_emulated` capability is exposed because independently partitioning phenotypes would discard the cross-phenotype similarity model that defines SimiC.
- Uncertainty:
  - `simicLASSO_op` can generate assignments internally if no assignment file is provided and `k_cluster` is set (`clus_regression.py:459-473`), but the tutorial states a correct run provides an assignment file and that assignment order is meaningful (`Tutorial/README.md:6-27`). This integration does not expose the undocumented fallback in Phase 1.

## ToolSpec Evidence Ledger

### `schema_version`

- Chosen value: `"1.0"`.
- Evidence: `andrea/catalog_inference_tools/schemas/toolspec.schema.json`.
- Rationale: fixed project contract.
- Confidence: high.

### `id`

- Chosen value: `"simic"`.
- Evidence: folder names `wrappers/inference_tools/tools/simic/` and `andrea/catalog_inference_tools/tools/simic/`.
- Rationale: stable lowercase tool id.
- Confidence: high.

### `name`

- Chosen value: `"SimiC"`.
- Evidence: README title `README.md:1`; paper title `s42003-022-03319-7.txt:4-5`.
- Rationale: official method spelling.
- Confidence: high.

### `publication`

- Chosen value: `["https://doi.org/10.1038/s42003-022-03319-7"]`.
- Evidence: DOI printed in the paper header and footer (`s42003-022-03319-7.txt:2`, `45`).
- Rationale: primary peer-reviewed SimiC method paper.
- Confidence: high.

### `first_author`

- Chosen value: `"Jianhao Peng"`.
- Evidence: paper author list starts with Jianhao Peng (`s42003-022-03319-7.txt:4-9`).
- Rationale: full first author of the primary publication.
- Confidence: high.

### `year`

- Chosen value: `2022`.
- Evidence: Communications Biology citation footer and copyright year (`s42003-022-03319-7.txt:45`, `2994`).
- Rationale: publication year of the primary SimiC paper.
- Confidence: high.

### `method_summary`

- Chosen value: summary in `toolspec.json`.
- Evidence:
  - Abstract: jointly infers distinct but related regulatory dynamics per phenotype (`s42003-022-03319-7.txt:20-29`).
  - Methods: fused LASSO objective with L1 sparsity and similarity penalty (`s42003-022-03319-7.txt:2668-2715`).
  - Activity-score workflow: weighted regulon activity / AUC from inferred weights (`s42003-022-03319-7.txt:2740-2768`).
- Rationale: describes SimiC's method, not the ANDREA wrapper.
- Confidence: high.

### `method_keywords`

- Chosen value: `single_cell`, `fused_lasso`, `regularized_regression`, `cell_phenotype`, `regulon_activity`, `directed`.
- Evidence:
  - Single-cell and phenotype-specific framing (`s42003-022-03319-7.txt:13-29`, `66-72`).
  - Fused LASSO / regularization (`s42003-022-03319-7.txt:141-147`, `2668-2715`).
  - Regulon activity score (`s42003-022-03319-7.txt:79-84`, `2740-2768`).
  - Directed TF/driver to target incidence matrix (`s42003-022-03319-7.txt:129-145`, `2700-2715`).
- Rationale: reusable conceptual tags for catalog filtering.
- Confidence: high.

### `implementation_url`

- Chosen value: `https://github.com/jianhao2016/SimiC/tree/fa47e69e5c793bce1500dc35283e112cd8f51992`.
- Evidence:
  - Paper code availability lists `https://github.com/jianhao2016/SimiC` (`s42003-022-03319-7.txt:2844-2851`).
  - Local repo remote is `https://github.com/jianhao2016/SimiC.git`; local HEAD is `fa47e69e5c793bce1500dc35283e112cd8f51992`.
- Rationale: official public source pinned to the integrator-provided commit.
- Confidence: high.

### `docker_image`

- Chosen value: `adriansegura99/inference-tools_simic:1.0.0`.
- Evidence: project naming convention used by other inference tool ToolSpecs.
- Rationale: expected final image name for ANDREA packaged wrappers.
- Confidence: medium until Phase 2 builds the image.

### `runtime_resources.threading`

- Chosen value: supported, default 1, max 8.
- Evidence: see Runtime resources above.
- Rationale: SimiC's public API lacks an algorithmic worker parameter, but its
  CPU-heavy numerical kernels can be controlled through BLAS/OpenMP-style
  backend thread variables under the agreed ToolSpec semantics.
- Confidence: medium. The mapping is real for numeric kernels, but not every
  phase is backend-bound.

### `accepts`

- Chosen value: `["cells"]`.
- Evidence:
  - Paper says SimiC is a GRN inference algorithm for scRNA-seq data and consumes cell labels (`s42003-022-03319-7.txt:66-72`).
  - Tutorial says the expression rows are different cells (`Tutorial/README.md:6-8`).
- Rationale: SimiC is semantically single-cell, not a generic bulk/sample method.
- Confidence: high.

### `assumes`

- Chosen value: `"scrna_specific"`.
- Evidence:
  - Paper states SimiC is for scRNA-seq, uses imputed scRNA-seq expression and cell state labels (`s42003-022-03319-7.txt:66-72`, `2655-2666`).
  - README test data is single-cell RNA-seq (`README.md:23-24`).
- Rationale: method assumptions depend on single-cell phenotypes and dropout/imputation context.
- Confidence: high.

### `extra_inputs`

- Chosen value:
  - `required`: `["cell_phenotypes", "tf_list"]`
  - `optional`: `[]`
  - `conditional_required`: `[]`
- Evidence:
  - Tutorial says a correct SimiC run requires 3 files: expression matrix, selected TF list, and cell-to-cluster assignment (`Tutorial/README.md:4-27`).
  - `simicLASSO_op` requires `p2assignment` and `p2tf` paths in the documented call (`Tutorial/README.md:38-48`, `90-107`).
  - Source reads `p2assignment` as integer labels (`clus_regression.py:459-467`) and reads `p2tf` as a pickle list (`clus_regression.py:508-521`).
- Rationale:
  - Create and use normalized `cell_phenotypes` for SimiC's ordered cell-to-phenotype assignment because existing `groups` only represents an unordered grouping/cluster label.
  - Reuse existing normalized `tf_list` for driver genes.
  - No prior GRN or lineage tree is part of the upstream SimiC entrypoint.
  - `cell_phenotypes` is always required because only `group_native` execution is exposed and SimiC's native grouped mode requires ordered phenotypes.
- Uncertainty:
  - Low. The new input spec deliberately encodes the missing ordering semantics instead of using an artificial run parameter.

### `outputs`

- Chosen value:
  - `directed`: `true`
  - `sign`: `"signed"`
  - `evidence`: `"association"`
- Evidence:
  - Paper describes a driver/TF by target incidence matrix; `Wi,j` is the influence of driver i on target j (`s42003-022-03319-7.txt:129-145`, `2700-2715`).
  - Paper says weights indicate strength and direction, including promotion or repression (`s42003-022-03319-7.txt:141-145`, `2636-2644`, `2811-2813`).
  - Source saves signed `weight_dic` matrices (`clus_regression.py:623-632`); weighted AUC uses absolute weights for activity scoring (`weighted_AUC_mat.py:117-118`) but this is post-processing, not the raw network score source.
- Rationale:
  - Edges are directed from TF/driver source to target gene.
  - The raw coefficient magnitude should become `score` in `network.csv`; the coefficient sign should become `+` or `-`.
  - Evidence is model-based association/regression, not perturbational causality.
- Confidence: high.

### `progress`

- Chosen value: `{"kind": "none", ...}`.
- Evidence:
  - The optimization has a bounded random coordinate descent loop (`clus_regression.py:581-588`) and `max_rcd_iter` parameter (`clus_regression.py:410-414`).
  - The public `simicLASSO_op` calls the RCD function with `slience=True`, so per-iteration progress is not emitted in the documented workflow (`clus_regression.py:586-588`).
- Rationale:
  - The wrapper can write coarse `progress.json` phase updates, but the ToolSpec should not claim a stable target-gene/iteration progress unit from the selected public interface.
- Confidence: medium.

### `params`

- Chosen values: see `toolspec.json`.
- Evidence and rationale:
  - `similarity`: exposed from `simicLASSO_op`; examples set it to `True` and the paper defines it as the similarity constraint between ordered states (`example/SimiC_example.py:19`, `Tutorial/README.md:42`, `s42003-022-03319-7.txt:2692-2707`).
  - `lambda1`, `lambda2`: exposed from `simicLASSO_op` defaults (`clus_regression.py:410-414`); paper defines lambda1 as sparsity and lambda2 as inter-matrix dependency/similarity (`s42003-022-03319-7.txt:2723-2731`).
  - `cross_val`: exposed from `simicLASSO_op`; if true, source searches the grid `[1e-1, 1e-2, 1e-3, 1e-4, 1e-5]` for both lambda values and then fits final weights (`clus_regression.py:562-578`).
  - `num_TFs`: exposed from `simicLASSO_op`; source selects top-MAD TFs unless `-1`, which keeps all available TFs (`clus_regression.py:330-338`, `536-542`).
  - `num_target_genes`: exposed from `simicLASSO_op`; source selects top-variance non-TF targets unless `-1`, which keeps all targets (`clus_regression.py:315-328`, `544-547`).
  - `normalization_factor`: maps to upstream `_NF`; source normalizes expression and multiplies by `_NF` (`clus_regression.py:449-451`).
  - `max_rcd_iter`: exposed from `simicLASSO_op` and passed into random coordinate descent (`clus_regression.py:410-414`, `586-588`).
  - `num_rep`: exposed from `simicLASSO_op`; source repeats final fitting and saves the last trained weight dictionary while averaging diagnostics (`clus_regression.py:586-632`).
  - `random_seed`: wrapper parameter. Upstream uses `np.random.permutation` and `random.choice` without a seed (`common_io.py:141-154`, `clus_regression.py:265-266`); null preserves upstream runtime-dependent randomness, while an integer lets Phase 2 seed Python and NumPy before calling SimiC.
  - `wauc_percent_of_target`, `wauc_sort_by`, `wauc_adj_r2_threshold`: exposed from `weighted_AUC_mat.main_fn` defaults (`weighted_AUC_mat.py:156-170`) for the auxiliary weighted AUC artifact.
- Defaults:
  - Signature defaults win when examples differ, except `similarity=true`, which is the documented example and paper default behavior even though `similarity` is a required positional argument.
  - `lambda1=0.01` and `lambda2=0.00001` are from the function signature.
  - `cross_val=false` means user-provided/default lambda values are used. `cross_val=true` makes lambda selection data-dependent on five-fold adjusted R2 over the upstream grid.
- Confidence:
  - High for upstream parameters.
  - Medium for wrapper-only `random_seed`; it is added to preserve upstream default randomness while allowing reproducible wrapper runs.

### `artifacts_aux`

- Chosen values:
  - `simic.log`
  - `raw/simic_weights.pickle`
  - `raw/simic_wauc_matrices.pickle`
- Evidence:
  - README says test output includes `incident_matrices` and `wAUC_matrices` (`README.md:35-36`).
  - Example names those outputs `test_incident_matrices` and `test_wAUC_matrices` (`example/SimiC_example.py:20-41`).
  - Source saves `weight_dic`, adjusted R2, standard error, TF IDs and query targets to the SimiC weight pickle (`clus_regression.py:623-632`).
  - `weighted_AUC_mat.main_fn` writes the weighted AUC dictionary pickle (`weighted_AUC_mat.py:156-173`).
- Rationale:
  - Preserve both documented raw outputs for debugging and downstream inspection while mapping the raw incidence matrices to `network.csv`.
- Confidence: high.

## Required inputs

- `expression_matrix`: normalized ANDREA expression matrix, genes in rows and cells in columns. The wrapper must transpose to SimiC's pandas DataFrame shape where rows are cells and columns are genes.
- `cell_phenotypes`: normalized `cell_phenotypes.tsv` mapping each expression column/cell to a phenotype label and integer phenotype order. Required for the exposed `group_native` mode.
- `tf_list`: normalized one-TF-per-line text file. The wrapper must convert it to the pickle list expected by `simicLASSO_op`.

## Optional or conditional inputs

- No optional extra input files.
- No ToolSpec conditional extra input rules in Phase 1 because the only exposed execution capability is `group_native` and `cell_phenotypes` is always required.
- Conditional behavior is represented in params:
  - `cross_val=true` makes `lambda1`/`lambda2` selected by upstream cross-validation rather than fixed defaults.

## Parameters and defaults

- `similarity=true`: use the SimiC similarity constraint.
- `lambda1=0.01`: upstream default sparsity weight.
- `lambda2=0.00001`: upstream default similarity weight.
- `cross_val=false`: upstream default; if true, choose lambda1/lambda2 by five-fold adjusted-R2 grid search over `{1e-1, 1e-2, 1e-3, 1e-4, 1e-5}`.
- `num_TFs=-1`: keep all provided TFs present in expression.
- `num_target_genes=-1`: keep all non-TF expression genes as targets.
- `normalization_factor=1.0`: maps to upstream `_NF`.
- `max_rcd_iter=500000`: upstream default final RCD iteration cap.
- `num_rep=1`: upstream default repeated final fits.
- `random_seed=null`: preserve upstream unseeded randomness.
- `wauc_percent_of_target=1.0`, `wauc_sort_by="expression"`, `wauc_adj_r2_threshold=0.7`: upstream weighted AUC defaults.

## Primary outputs

- Upstream primary output: SimiC weight dictionary pickle containing `weight_dic`, `adjusted_r_squared`, `standard_error`, `TF_ids`, and `query_targets`.
- Upstream auxiliary output: weighted AUC matrix pickle from `main_fn`.
- ANDREA primary output for Phase 2: `network.csv`.

## Normalized Input Mapping

### Reused input specs

- `expression_matrix`: semantic match after orientation conversion. ANDREA stores genes x cells; SimiC code/tutorial uses cells x genes DataFrames.
- `tf_list`: semantic match for SimiC driver genes / TF list.

### New input specs required

- Added `andrea/catalog_inference_tools/input_specs/cell_phenotypes.json`.
- Rationale:
  - SimiC requires an ordered cell phenotype assignment, not just arbitrary unordered cell groups.
  - Existing `groups.tsv` has only `sample`/`cluster` semantics and cannot represent phenotype order without an artificial tool parameter.
  - The new TSV requires first-column expression cell IDs plus `phenotype` and integer `order` columns.
  - Cross-checks require first-column values to be expression columns and row count to match the number of expression columns; with `unique_first_column=true`, this enforces one phenotype assignment per input cell.
- Catalog/GUI support:
  - Added `cell_phenotypes` to ToolSpec extra-input enums.
  - Added `cell_phenotypes` to dataset manifest extras.
  - Added GUI bootstrap example/help through the generic input-spec loading path.
  - Native grouped tools now rely on their ToolSpec-required and ToolSpec-conditional extras; `group_emulated` group requirements are declared as `conditional_required` rules on the tools that support that execution mode.

## Output Mapping to `network.csv`

- Use `raw/simic_weights.pickle` as the source of `network.csv`.
- For each group/state label:
  - read `weight_dic[state]`
  - drop the final bias row by keeping rows `0:len(TF_ids)` as upstream `weighted_AUC_mat.normalized_by_target_norm` does (`weighted_AUC_mat.py:19-42`)
  - map rows to `TF_ids` and columns to `query_targets`
  - write one directed row per nonzero coefficient:
    - `source`: TF/driver gene
    - `target`: target gene
    - `score`: absolute magnitude of the raw SimiC coefficient from the incidence matrix, without ANDREA-specific normalization
    - `sign`: `+` when the raw coefficient > 0, `-` when the raw coefficient < 0
    - `evidence`: `association`
    - `context`: `group:<original_group_label>`
- Do not use weighted AUC values as edge scores. The paper defines SimiC's GRN output as incidence matrices and the weighted AUC as regulon activity post-processing.
- Do not write exact zero coefficients to `network.csv`.

## Installation Strategy

Preferred public installation source:

- No official usable PyPI package for this SimiC implementation was found.
- Evidence:
  - Upstream `setup.py` package name is `simiclasso` version `0.3.2` (`setup.py:16-24`).
  - `python -m pip index versions simiclasso` returned no matching distribution.
  - A PyPI package named `simic` exists, but inspecting `simic-1.5.4` shows it is a Selenium helper package with package `simic`, not the GRN package `simiclasso`.

Fallback pinned source:

- Install from the official GitHub repository pinned to `fa47e69e5c793bce1500dc35283e112cd8f51992`.
- Implemented in `Dockerfile` with `pip install git+https://github.com/jianhao2016/SimiC.git@fa47e69e5c793bce1500dc35283e112cd8f51992`; runtime does not depend on the local `repo/` folder.
- Runtime dependencies are installed with the same Python interpreter that executes the wrapper. The upstream pinned requirements are not used verbatim because they target older Python/scientific-stack versions; the wrapper image pins compatible Python 3.11 packages (`numpy`, `pandas`, `scipy`, `scikit-learn`, `matplotlib`, `requests`, `ipdb`).

Upstream Dockerfile decision:

- Do not use the upstream Dockerfile as the ANDREA wrapper.
- Evidence:
  - Upstream Dockerfile clones floating `master`, installs a broad Seurat/R environment, downloads tutorial data, and has no ANDREA-style entrypoint (`Dockerfile:1-37`).
  - README describes the Dockerfile as an environment fallback if package installation fails (`README.md:17-18`), not as a complete command-line wrapper.
- Implemented with an ANDREA Dockerfile that installs the pinned upstream source and exposes `run_tool.py` through the generated Python runtime `run_tool.sh`.

## Implemented Wrapper Behavior

- The wrapper is implemented in `wrappers/inference_tools/tools/simic/run_tool.py`.
- `template_map.json` registers `simic` as a Python wrapper with the shared `python_runtime_contract` bundle.
- Runtime input conversion:
  - expression TSV genes x cells -> pandas DataFrame cells x genes pickle
  - `tf_list.txt` -> pickle list expected by SimiC
  - `cell_phenotypes.tsv` -> contiguous integer assignment file ordered by the `order` column
- Runtime validation:
  - every expression cell must appear exactly once in `cell_phenotypes.tsv`
  - every phenotype has exactly one integer order
  - every order maps to exactly one phenotype
  - each phenotype must have enough cells for SimiC's train/test diagnostics after the upstream 20% split: at least two train cells and two test cells per phenotype
  - all TFs must exist in expression and at least one non-TF target gene must remain
- Upstream split handling:
  - SimiC's public `simicLASSO_op` calls `split_df_and_assignment()` with a fixed 20% test split before fitting and scoring (`clus_regression.py:500`, `common_io.py:141-153`).
  - The upstream split is random but not stratified, so small or imbalanced phenotype datasets can leave a phenotype with only one test cell; the upstream adjusted-R2 helper then asserts `num_sample > 1` (`evaluation_metric.py:42-43`).
  - The wrapper now patches the imported split helper to keep the same 20% test-size rule while selecting at least two train and two test cells per phenotype. If this is impossible, it fails early with a clear input-shape error instead of surfacing the upstream assertion.
- Default preservation:
  - `random_seed=null` does not seed `random` or `numpy.random`
  - integer `random_seed` seeds both Python and NumPy for reproducible wrapper runs
  - `cross_val=true` is passed through to upstream SimiC rather than precomputing or replacing lambda values
- Output behavior:
  - `raw/simic_weights.pickle` is produced by `simicLASSO_op`
  - `raw/simic_wauc_matrices.pickle` is produced by `weighted_AUC_mat.main_fn`
  - `network.csv` uses raw incidence-matrix coefficient magnitudes from `weight_dic`, excludes exact zero coefficients, writes `+`/`-` sign from the raw coefficient direction, and sets `context=group:<phenotype>`
  - `progress.json` reports coarse lifecycle states because the selected upstream public function has no stable progress callback
- Runtime resources:
  - `--threads` sets BLAS/OpenMP-style thread environment variables before NumPy/Pandas/SciPy/scikit-learn/SimiC are imported
  - SimiC has no public `n_jobs`, worker or process-count parameter; backend CPU threads are not repeated as normal params
- Compatibility note:
  - The wrapper patches SimiC's imported `r2_score` call to pass `sample_weight` as a keyword, preserving upstream behavior on modern scikit-learn where that argument is keyword-only.

## Smoketest

- Config: `wrappers/inference_tools/tests/smoketest_configs/simic.json`.
- Fixtures: shared `wrappers/inference_tools/tests/fixtures/expression.tsv`, `cell_phenotypes.tsv`, `tf_list.txt`.
- Dev override: `wrappers/inference_tools/param_overrides/simic.json` uses a small deterministic run (`random_seed=1`, `num_TFs=3`, `num_target_genes=4`, `max_rcd_iter=2000`, `lambda1=0`, `lambda2=0`).
- Result:
  - Command: `python wrappers/inference_tools/scripts/run_smoketests.py --tool simic --timeout 900 --show-output --show-output-lines 80`
  - Passed.
  - `network.csv`: 24 non-zero directed rows with `group:state_0` and `group:state_1` contexts.
  - Auxiliary artifacts validated: `simic.log`, `raw/simic_weights.pickle`, `raw/simic_wauc_matrices.pickle`.

## Known Limitations / Open Questions

- SimiC requires meaningful ordering of phenotypes for the similarity constraint. Phase 1 resolves this with a dedicated `cell_phenotypes` input spec instead of overloading `groups.tsv`.
- The selected upstream public function silently runs RCD without a per-iteration callback, so progress is coarse.
- Upstream has runtime-dependent randomness from the train/test split and RCD label selection; `random_seed=null` preserves that default, while an integer seed is a wrapper-level reproducibility option.
- The wrapper preserves runtime-dependent randomness for the stratified 20% train/test split when `random_seed=null`; integer `random_seed` seeds the split and random coordinate descent.
- Datasets with too many ordered phenotypes for the fixed 20% test split, or with fewer than four cells in any phenotype, are unsupported by this wrapper contract because SimiC's adjusted-R2 diagnostics require at least two train and two test cells per phenotype.
- `simicLASSO_op` has an undocumented fallback that clusters cells when no assignment file is provided. This integration does not expose it because the tutorial says a correct run requires an assignment file and the fallback does not clearly preserve meaningful phenotype order.
- `--threads` controls numeric backend threads only; SimiC does not expose an upstream algorithm-level worker pool.

## Failure Semantics Review

- The `simic__01` failure observed in `inferred_networks/gui_dataset_20260519T003127Z` is classified as an unsupported dataset shape exposed through an upstream method limitation.
- The input files were syntactically valid, but the dataset contained several small phenotype groups. Upstream SimiC's fixed random 20% train/test split is not stratified, and the adjusted-R2 diagnostic asserts that each evaluated phenotype has more than one sample.
- The wrapper now preserves the upstream 20% split rule while making the split phenotype-aware. It fails early with a clear input-shape error when a phenotype cannot provide at least two train and two test cells.
- SimiC upstream failures are not broadly converted into successful empty networks. Empty output is not the intended behavior for this failure class.

## Validation

- `make validate-toolspecs`: passed for all inference ToolSpecs, including `simic`.
- `python wrappers/inference_tools/scripts/validate_input_specs.py`: passed for all input specs, including `cell_phenotypes`.
- JSON syntax validation passed for the new/modified catalog schema and SimiC ToolSpec files.
- `make validate-smoketest-configs ARGS="--tool simic"`: passed.
- Shared fixture compatibility check passed: `expression.tsv`, `groups.tsv` and `cell_phenotypes.tsv` use the same 30 cell IDs; `tf_list`, `prior_grn`, `prior_grn_by_group` and `lineage_tree` remain consistent with the shared expression and group fixtures.
- `python -m py_compile wrappers/inference_tools/tools/simic/run_tool.py`: passed.
- SimiC smoketest: passed.
