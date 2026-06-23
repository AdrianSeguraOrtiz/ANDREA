# SCING Integration Decisions

Phase 2 implementation completed. The wrapper, Dockerfile, template-map entry and smoketest config now implement the Phase 1 contract.

## Sources Reviewed

- Primary paper text: `wrappers/inference_tools/tools/scing/papers/SCING.txt`
- Primary paper PDF: `wrappers/inference_tools/tools/scing/papers/SCING.pdf`
- Upstream README: `wrappers/inference_tools/tools/scing/repo/SCING/README.md`
- Upstream tutorials: `wrappers/inference_tools/tools/scing/repo/SCING/tutorials/*.ipynb`
- Upstream package/source: `wrappers/inference_tools/tools/scing/repo/SCING/setup.py`, `src/scing/supercellHelpers.py`, `src/scing/buildGRNHelpers.py`, `src/scing/MergeNetworksHelpers.py`
- Upstream git remote and commit: `https://github.com/XiaYangLabOrg/SCING.git`, `fcea8c5c9a806ee3dbc8123c2d13d1d357137f1d`
- Package audit: `pip index versions SCING` and PyPI JSON for `scing==0.8.2`

## Paper Preparation

- PDF inputs found: `SCING.pdf`
- Extracted text used: `SCING.txt`
- Extraction quality: sufficient for title, authors, DOI, year, method summary, defaults and limitations. DOI is line-wrapped near the article header but appears fully later as `https://doi.org/10.1016/j.isci.2023.107124`.

## Method Summary

SCING infers GRNs from scRNA-seq, snRNA-seq and spatial transcriptomics. The paper describes clustering cells/spots into supercells, subsampling supercells to build many GRNs, limiting candidate regulators by gene-neighbor search in PC space, fitting gradient boosting regressors to produce directed upstream-to-target edges, keeping consensus edges that appear in 20% of networks by default, and pruning reversed edges, cycles and conditionally redundant edges with conditional mutual information.

## Selected Contract

The wrapper contract mirrors the upstream public `BuildNetwork.ipynb` workflow:

1. Build supercells with `supercells.supercell_pipeline(adata, ngenes, npcs, ncell)`.
2. Build repeated subsampled networks with `build.grnBuilder(...).pipeline()`.
3. Merge/prune with `merge.NetworkMerger(...).pipeline()`.

The wrapper produces a single directed GRN per input expression matrix. ANDREA `group_emulated` partitions the input by group and runs the same public global workflow per group; when the wrapper is invoked directly with `execution.mode=group_emulated`, it validates `groups.tsv` but still emits raw `context=global`, matching the existing group-emulated wrapper contract where the ANDREA runner assigns `group:<id>` in the logical parent result.

## Public Execution Modes / Entrypoints

- `BuildNetwork.ipynb`: selected. It is the public end-to-end GRN tutorial and uses `supercell_pipeline`, `grnBuilder`, and `NetworkMerger`.
- `supercells.pseudobulk_pipeline()`: not exposed. It writes pseudobulk/supercell data by strata but does not define the complete public GRN output contract.
- `ModuleBasedDimensionalityReduction.ipynb`: not exposed. It consumes a merged network for module/AUCell analysis, not GRN inference.
- `PathwayEnrichmentOfModules.ipynb`: not exposed. It is pathway enrichment of modules, not network inference.
- `FindSignificantlyPerturbedGenesFromPerturbseqData.ipynb`: not exposed. It is validation/application logic, not the selected public inference interface.
- README documented cluster splitting into three scripts: not exposed as a separate ANDREA mode. The selected wrapper should use the public `ncore` controls in `grnBuilder` and `NetworkMerger`; wrapper-owned cluster sharding can be reconsidered only if runtime evidence shows local `ncore` is inadequate.

## ToolSpec Evidence Ledger

### `schema_version`

- Chosen value: `1.0`
- Evidence: existing ANDREA ToolSpecs and `toolspec.schema.json`.
- Rationale: current catalog schema version.
- Uncertainty: none.

### `id`

- Chosen value: `scing`
- Evidence: scaffold path `andrea/catalog_inference_tools/tools/scing/`.
- Rationale: ANDREA tool id and wrapper folder id.
- Uncertainty: none.

### `name`

- Chosen value: `SCING`
- Evidence: README title and paper title.
- Rationale: official method name capitalization.
- Uncertainty: none.

### `publication`

- Chosen value: `https://doi.org/10.1016/j.isci.2023.107124`, `https://doi.org/10.1101/2022.09.07.506959`
- Evidence: `SCING.txt` article header and supplemental DOI line; README overview preprint DOI.
- Rationale: final iScience DOI first, preprint DOI second, both as canonical DOI URLs.
- Uncertainty: none.

### `first_author`

- Chosen value: `Russell Littman`
- Evidence: `SCING.txt` author line.
- Rationale: full first author name, not surname-only.
- Uncertainty: none.

### `year`

- Chosen value: `2023`
- Evidence: `SCING.txt` article header: `iScience 26, 107124, July 21, 2023`.
- Rationale: final publication year.
- Uncertainty: none.

### `method_summary`

- Chosen value: summary of supercells, bagging, gradient boosting regression, consensus filtering and conditional mutual-information pruning.
- Evidence: `SCING.txt` summary and method overview; README overview.
- Rationale: describes the method, not wrapper mechanics.
- Uncertainty: none.

### `method_keywords`

- Chosen value: `single_cell`, `spatial_transcriptomics`, `supercells`, `bagging`, `gradient_boosting_regression`, `conditional_mutual_information`, `directed`
- Evidence: paper summary/methods and README overview.
- Rationale: captures declared input domain and main algorithmic pieces.
- Uncertainty: none.

### `implementation_url`

- Chosen value: `https://github.com/XiaYangLabOrg/SCING/tree/fcea8c5c9a806ee3dbc8123c2d13d1d357137f1d`
- Evidence: local repo remote and `git rev-parse HEAD`; user-provided preferred commit.
- Rationale: official upstream source pinned to the requested commit.
- Uncertainty: none.

### `docker_image`

- Chosen value: `adriansegura99/inference-tools_scing:1.0.0`
- Evidence: ANDREA wrapper image naming convention for recent integrations.
- Rationale: image tag used by the implemented Dockerfile/ToolSpec contract.
- Uncertainty: local smoketest build used `scing-smoketest:local`; publishing the registry tag is a release step.

### `execution_capabilities`

- Chosen value: `global`, `group_emulated`
- Evidence: README says SCING selects a cell type or uses whole Visium data before building a network; `BuildNetwork.ipynb` takes one AnnData and returns one merged network.
- Rationale: one upstream invocation produces one network for one expression matrix. Grouped outputs require ANDREA to partition groups and run the same global workflow independently.
- Uncertainty: users should prefer biologically coherent groups/cell types; whole-dataset global mode is valid for a homogeneous dataset or whole spatial tissue but may be less interpretable for mixed cell types.

### `runtime_resources`

- Chosen value: threading supported, default 1, max 8, `--threads` maps to `grnBuilder.ncore` and `NetworkMerger.ncore`.
- Evidence: `buildGRNHelpers.py` uses `NearestNeighbors(n_jobs=self.ncores)` and Dask `LocalCluster(n_workers=self.ncores)`; `MergeNetworksHelpers.py` uses Dask `LocalCluster(n_workers=self.ncores)`; tutorial sets thread-related environment variables.
- Rationale: upstream exposes real worker controls; do not expose thread/core controls as normal params.
- Implemented behavior: wrapper passes `--threads` to both `grnBuilder(ncore=threads)` and `NetworkMerger(ncore=threads)`, pins common BLAS/OpenMP/NUMEXPR env vars to 1, and uses fixed `SCING_MEM_PER_CORE=2000000000` bytes unless overridden by environment.
- Uncertainty: `mem_per_core` remains a runtime policy, not a user-facing parameter.

### `accepts`

- Chosen value: `cells`, `spots`
- Evidence: paper states scRNA-seq, snRNA-seq and spatial transcriptomics; Figure 1 describes cells/spatial spots.
- Rationale: selected interface consumes expression columns that are single-cell/nucleus observations or spatial spots.
- Uncertainty: ANDREA has no explicit `nuclei` column kind; `cells` covers sc/snRNA observations.

### `taxonomic_scope`

- Chosen value: all broad groups, no species restriction.
- Evidence: selected implementation uses only expression matrices and no bundled species-specific prior in the selected GRN path; applications include human and mouse.
- Rationale: no primary evidence of a species/taxon hard dependency.
- Uncertainty: biological interpretation is strongest for transcriptomic datasets with meaningful gene identifiers.

### `compatibility_rules`

- Chosen values:
  - block if expression genes < 2.
  - block if expression columns < 2.
  - block if `network_hvgs` is 0 or 1.
  - block if `gene_neighbors >= dataset.expression.genes`.
  - block if `gene_pcs >= dataset.expression.genes`.
  - block if `gene_pcs >= dataset.expression.columns`.
- Evidence: `grnBuilder` predicts each target from candidate upstream genes, calls `NearestNeighbors(n_neighbors=nneighbors + 1)` over genes, and calls gene-level PCA with `n_comps=npcs`; the selected workflow clusters cells/spots into supercells.
- Rationale: these are dataset/parameter dimensional impossibilities that can be expressed declaratively. The wrapper also validates after SCING's HVG, empty-gene, duplicate-gene, supercell and subsampling steps.
- Uncertainty: exact post-filter dimensions are runtime-dependent, so not every possible invalid setting can be preflighted statically.

### `assumes`

- Chosen value: `scrna_specific`
- Evidence: paper and README position SCING for scRNA-seq, snRNA-seq and spatial transcriptomics.
- Rationale: not a generic bulk-expression method.
- Uncertainty: schema lacks a separate spatial-specific assumption value.

### `extra_inputs`

- Chosen value: no required or optional extras; `groups` conditional for `group_emulated`.
- Evidence: selected public workflow consumes only AnnData expression; groups are an ANDREA partitioning mechanism, not a SCING input.
- Rationale: no TF list, prior network or spatial coordinate input is used by the selected GRN path. The paper explicitly notes SCING does not currently utilize spatial information during spatial transcriptomics GRN construction.
- Implemented behavior: direct wrapper invocation validates `groups.tsv` when `execution.mode=group_emulated`; SCING itself receives only the expression matrix.
- Uncertainty: none for selected contract.

### `outputs`

- Chosen value: directed, `sign=none`, `evidence=association`.
- Evidence: `grnBuilder` writes `source`, `target`, `importance`; paper describes directed upstream-to-downstream edges but also cautions observational links are not necessarily causal or directional.
- Rationale: output orientation is directed by the model, scores are non-negative feature-importance/consensus weights, and no activation/repression sign is produced.
- Uncertainty: none.

### `progress`

- Chosen value: `kind=none` with coarse wrapper lifecycle reporting.
- Evidence: selected public Python APIs print diagnostics but expose no stable progress callback.
- Rationale: wrapper can update `progress.json` for phases but should not claim fine-grained upstream progress.
- Implemented behavior: wrapper reports coarse phases `init`, `load_input`, `supercells`, `build_networks`, `merge`, `write_output`, and `done`/`failed`.
- Uncertainty: none.

### `params`

- `n_supercells=500`: evidence paper default/benchmark and `supercell_pipeline(... ncell=500)`.
- `supercell_hvgs=2000`: evidence README/tutorial and `supercell_pipeline(... ngenes=2000)`.
- `supercell_pcs=20`: evidence README/tutorial and `supercell_pipeline(... npcs=20)`.
- `n_subsample_networks=100`: evidence paper default and README cluster note for 100 networks. Tutorial uses 10 only as a small example, not the method default.
- `network_hvgs=-1`: evidence `grnBuilder.filter_genes()` skips HVG filtering if `ngenes == -1` or `ngenes >= gene count`; tutorial uses `ngenes=-1`.
- `gene_neighbors=100`: evidence paper default and tutorial `nneighbors=100`.
- `gene_pcs=10`: evidence paper application and tutorial `npcs=10`.
- `subsample_fraction=0.7`: evidence paper application and tutorial `subsample_perc=0.7`.
- `edge_consensus_threshold=0.2`: evidence paper default 20%; source keeps edges with `FractionAppeared > threshold`.
- `remove_cycles=true`: evidence paper final-network pruning and `NetworkMerger` cycle-removal step.
- `random_seed=0`: evidence `grnBuilder(random_state=0)` default. Wrapper should pass `random_seed + network_index` so repeated bagged networks are reproducible and distinct.
- Not exposed: `ncore`, `mem_per_core`, Dask worker configuration, BLAS/OpenMP env vars, GBR estimator/depth/learning-rate/subsample/max-features, top 10% edge filter, top 3 parents per target, 25% reverse-edge dominance threshold and conditional-MI alpha. Evidence: hardcoded or resource-level implementation choices in source; exposing them would be wrapper-level surface area rather than the four main paper hyperparameters.
- Implemented validation: wrapper enforces static dimensional rules before running SCING, then checks post-filter dimensions after each `grnBuilder.filter_genes()` call because empty-gene/HVG filtering and supercell/subsampling counts are runtime-dependent.

### `artifacts_aux`

- Chosen values: `scing.log`, `raw/final.network.merged.csv`, `raw/intermediate_networks/*.csv.gz`, `raw/scing_config.json`.
- Evidence: `grnBuilder.save_edges()` writes per-subsample CSV.GZ files; `NetworkMerger.save_network()` writes `<prefix>.network.merged.csv`; selected APIs print diagnostics.
- Rationale: preserve upstream raw outputs/configuration needed to audit conversion to `network.csv`.
- Implemented behavior: all four declared artifact patterns are produced; the wildcard may match one file per subsampled network.
- Uncertainty: none.

## Normalized Input Mapping

- Reused input specs: expression matrix and conditional `groups` input already supported by ANDREA.
- New input specs required: none.
- ID preservation: wrapper creates AnnData with `var_names` exactly equal to ANDREA expression gene ids and `obs_names` exactly equal to expression column ids. SCING's public network output uses gene names in `source` and `target`, so no alias map is required.

## Output Mapping to `network.csv`

- Input SCING output: final merged CSV with `source`, `target`, `importance`.
- ANDREA mapping:
  - `regulator`: `source`
  - `target`: `target`
  - `score`: raw positive `importance`
  - `sign`: empty/none because no signed coefficient is produced
  - `evidence`: `association`
  - `context`: raw wrapper output is `global`; the ANDREA runner rewrites child-run rows to `group:<group_id>` for logical `group_emulated` outputs.
- Wrapper drops self-loops, rows with `score <= 0`, non-finite scores, and rows whose source/target are not original expression gene ids. No ANDREA-specific score normalization occurs in the wrapper.

## Runtime Resource Mapping

ANDREA `--threads` should map to public upstream worker controls:

- `build.grnBuilder(..., ncore=threads)`.
- `merge.NetworkMerger(..., ncore=threads)`.
- `grnBuilder` also passes `ncore` to sklearn `NearestNeighbors(n_jobs=ncore)`.
- `grnBuilder` and `NetworkMerger` use Dask `LocalCluster(n_workers=ncore, threads_per_worker=1)`.

The wrapper does not expose cores/workers as params. It sets common BLAS/OpenMP/NUMEXPR thread env vars to 1 under Dask worker execution to reduce nested oversubscription. `mem_per_core` is an implementation/runtime policy, not a user-facing method parameter.

Implemented behavior:

- `--threads` is passed to `grnBuilder` and `NetworkMerger` as `ncore`.
- Common BLAS/OpenMP/NUMEXPR env vars are set to 1 in Dockerfile and wrapper startup.
- `SCING_MEM_PER_CORE` defaults to `2000000000` bytes and is recorded in `raw/scing_config.json`.
- The wrapper runs upstream SCING from `output-dir/work` so SCING's fixed `merge_network_profile_stats` file is written to a writable location rather than `/app`.

## Installation Strategy

- Preferred public package: none. `pip index versions SCING` finds a PyPI package named `scing==0.8.2`, but PyPI metadata says it is `Single-Cell pIpeliNe Garden`, home page `https://github.com/hisplan/scing`, author `Jaeyoung Chun`; this is not the XiaYangLabOrg SCING GRN method.
- Selected installation source: clone official repo `https://github.com/XiaYangLabOrg/SCING.git` at commit `fcea8c5c9a806ee3dbc8123c2d13d1d357137f1d` during Docker build.
- Upstream install evidence: README instructs `git clone`, conda environment creation, `pip install pyitlib`, then `pip install -e .`.
- Implemented Dockerfile: installs Python runtime dependencies with `pip`, clones the pinned upstream repo into `/opt/SCING`, verifies the commit, and sets `PYTHONPATH=/opt/SCING/src`. This avoids the local `repo/` folder at runtime and works around upstream `setup.py` not declaring the `src/scing` package correctly for standard editable install.
- Dependency notes: `pyitlib==0.2.3` is installed with `--no-deps` because its stale `scikit-learn<=0.24` metadata conflicts with the Scanpy/scikit-learn runtime. `matplotlib==3.6.3`, `seaborn==0.12.2`, and `bokeh==2.4.3` are pinned to avoid known incompatibilities with `scanpy==1.9.1` and Dask's dashboard import path.

## Smoketest

Implemented smoketest config: `wrappers/inference_tools/tests/smoketest_configs/scing.json`.

- `global` mode with reduced parameters, e.g. small `n_supercells`, `n_subsample_networks`, `gene_neighbors`, `gene_pcs`, and `network_hvgs`.
- `group_emulated` mode with `groups.tsv`, checking the direct wrapper contract. The logical ANDREA runner is responsible for assigning group contexts after per-group child runs.
- Positive `score` values, directed rows, no self-loops, original gene ids preserved.
- `progress.json` exists and `scing.log` plus raw merged/intermediate artifacts are present.

Outcome on 2026-06-24:

- Command: `python wrappers/inference_tools/scripts/run_smoketests.py --tool scing --threads 2 --timeout 1800 --show-output --show-output-lines 80`
- Result: passed.
- Variants passed: `global`, `group_emulated_contract`.
- Build image used by smoketest: `scing-smoketest:local`.

Phase 3 validation on 2026-06-24:

- `make validate-toolspecs ARGS="--tool scing"`: passed.
- `make validate-input-specs ARGS="--spec expression_matrix --spec groups"`: passed.
- `python wrappers/inference_tools/scripts/validate_smoketest_configs.py --tool scing`: passed.
- `python wrappers/inference_tools/scripts/run_smoketests.py --tool scing --threads 2 --timeout 1800`: passed.

## Known Limitations

- SCING is computationally heavy at paper defaults (`500` supercells, `100` subsampled networks, `100` gene neighbors). Smoketests use reduced params while ToolSpec defaults remain aligned with the paper.
- The paper supports spatial transcriptomics but explicitly states spatial information is not used in GRN construction, so no spatial-coordinate extra input is declared.
- Some dimensional constraints depend on runtime filtering: empty genes, duplicate expression profiles, selected HVGs, actual supercell count and subsampled supercell count. ToolSpec preflight covers only static impossibilities; wrapper validation remains required.
- The public tutorial appears to instantiate repeated `grnBuilder` runs without varying `random_state`. Because the method relies on bagged subsamples and `grnBuilder` exposes `random_state`, the wrapper should use deterministic distinct seeds (`random_seed + network_index`) to preserve the intended bagging behavior.
