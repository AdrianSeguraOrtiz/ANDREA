# miniex3 Integration Decisions

Phase: Phase 3 complete; wrapper, ToolSpec, input specs and smoketest validated.

## Sources Reviewed

- Upstream repo snapshot: `wrappers/inference_tools/tools/miniex3/repo/MINI-EX/`
- Local upstream commit: `3f220a68e8057fe4d33665e956a3f3bbe41ff4c8`
- Upstream remote: `https://github.com/VIB-PSB/MINI-EX.git`
- Upstream entrypoint/config/docs:
  - `repo/MINI-EX/README.md`
  - `repo/MINI-EX/miniex.nf`
  - `repo/MINI-EX/miniex.config`
  - `repo/MINI-EX/docs/data_preparation.md`
  - `repo/MINI-EX/docs/configuration.md`
  - `repo/MINI-EX/example/README.md`
- Upstream implementation scripts:
  - `repo/MINI-EX/bin/MINIEX_checkUserInput.py`
  - `repo/MINI-EX/bin/MINIEX_scoreEdges.py`
  - `repo/MINI-EX/bin/MINIEX_makeBorda.py`
  - `repo/MINI-EX/bin/MINIEX_selectBordaProcedure.py`
- Local papers:
  - `papers/1-s2.0-S1674205222003689-main.pdf`
  - `papers/1-s2.0-S1674205222003689-main.txt`
  - `papers/978-1-0716-4972-5_12.pdf`
  - `papers/978-1-0716-4972-5_12.txt`

## Paper Preparation

- Text extraction already exists for both PDFs and is sufficient for title, authors, DOI, abstract, method steps, input files, parameters and output semantics.
- Primary method paper used for `year`, `first_author`, `method_summary` and `method_keywords`: Ferrari et al. 2022, Molecular Plant, DOI `https://doi.org/10.1016/j.molp.2022.10.016`.
- Secondary/version paper used for input/parameter clarification and version-2/late-version behavior: Staut et al. 2026, Methods in Molecular Biology, DOI `https://doi.org/10.1007/978-1-0716-4972-5_12`.

## Upstream Interface Audit

### Public Entrypoint Chosen

- Chosen upstream public entrypoint: `nextflow -C <generated config> run miniex.nf`.
- Evidence: `README.md` describes MINI-EX as a Nextflow DSL2 pipeline and documents `nextflow -C miniex.config run miniex.nf`; the primary paper states the pipeline is implemented in Nextflow DSL2 with a Singularity container; `miniex.nf` contains the full public workflow.
- Rationale: the Python scripts in `bin/` are internal process implementations. Calling them directly would bypass upstream validation, process ordering, Borda procedure selection and output layout.
- Wrapper language decision for Phase 2: keep the scaffold as Python for ANDREA IO/config generation and call the upstream Nextflow workflow as a subprocess. The method runtime is Nextflow plus Python dependencies.

### Execution Capabilities

- ToolSpec value: `execution_capabilities = ["group_native"]`.
- Evidence:
  - `README.md` says MINI-EX infers cell-type-specific GRNs from scRNA-seq data in plants.
  - `docs/data_preparation.md` requires a cell-to-cluster file and a cluster-identity file.
  - The 2026 paper states steps 1 and 2 create a precursor full-dataset GRN, while step 3 operates in a cluster-specific manner and yields a unique GRN for each cluster.
  - `miniex.nf` consumes `cellsToClusters` and `clustersToIdentities` in one workflow run and produces cluster-labeled regulons/edge tables.
- Rationale: MINI-EX natively consumes group metadata and produces group-specific networks in one upstream run. The full-dataset GRNBoost2 network is an intermediate, not MINI-EX's final inference target, so the wrapper should not advertise `global`. `group_emulated` is also not appropriate because the upstream method uses the full expression-derived network before cluster-specific filtering; splitting first would change the method.

### Public Modes Exposed as Parameters or Inputs

- Motif analysis on/off: exposed as `doMotifAnalysis`.
- Built-in species annotation set: exposed as `reference_species`.
- Motif-filter strictness: exposed as `motifFilter`.
- Optional GO/term-guided ranking: exposed by optional `terms_of_interest`; upstream chooses standard/reference Borda at runtime.
- Optional custom enrichment universe: exposed by optional `enrichment_background`.
- Optional precomputed GRNBoost2 cache: exposed by optional `grnboost_network`.
- GRNBoost2 subjob count and visualization threshold: exposed as `grnboostSubjobs` and `topRegulons`.

### Upstream Modes Not Exposed

- Multi-dataset glob batching is not exposed. ANDREA's inference run contract is one dataset expression matrix per run; batching multiple datasets through one Nextflow invocation would complicate `network.csv` provenance and is better handled by ANDREA scheduling separate runs.
- Custom full species-annotation bundles are not exposed in Phase 1. Upstream can use user-generated motif mapping, TF-family/motif info, GO annotation and aliases; however, ANDREA currently lacks a normalized annotation-bundle input with compressed motif-map support and enough conditional rules to validate the bundle cleanly. The wrapper should support built-in upstream species data and the documented no-motif mode for unsupported species. This limitation should be revisited if users need full custom species annotation.
- Direct execution of individual `bin/MINIEX_*.py` scripts is not exposed because these are Nextflow internals, not the documented public entrypoint.

## Normalized Input Mapping

### Reused Input Specs

- `expression_matrix`: reused. Upstream requires a gene-to-cell count matrix with genes as rows and cells as columns; this matches ANDREA's expression TSV orientation.
- `groups`: reused. Upstream `cellsToClusters` maps cell barcodes to cluster IDs; ANDREA `groups.tsv` maps expression column IDs to a cluster/group label.
- `tf_list`: reused. Upstream requires a list of TFs for GRNBoost2.

### New Input Specs Added

- `cluster_markers`: required for MINI-EX `markersOut`. Evidence: `README.md` and `docs/data_preparation.md` require Seurat `FindAllMarkers` output; `MINIEX_checkUserInput.py` requires at least `p_val_adj`, `cluster` and `gene`.
- `cluster_identities`: required for MINI-EX `clustersToIdentities`. Evidence: `docs/data_preparation.md` describes cluster ID to annotation, with optional third ordering column; `MINIEX_checkUserInput.py` accepts two or three columns. Phase 2 validates the optional normalized `order` column but writes only the two upstream columns because `MINIEX_makeInfoFile.py` and `MINIEX_makeRankingDataframe.py` read this file as two columns in the pinned v3.2 workflow.
- `terms_of_interest`: optional list for GO/keyword-guided ranking. Evidence: `README.md`, `docs/data_preparation.md` and `docs/configuration.md`.
- `enrichment_background`: optional gene list used as enrichment background. Evidence: MINI-EX v3 README lists this as a new parameter, and `docs/configuration.md` states that `null` defaults to expression genes.
- `grnboost_network`: optional precomputed GRNBoost2 network cache. Evidence: `docs/data_preparation.md`, `docs/configuration.md` and `miniex.nf` skip de novo GRNBoost2 when `params.grnboostOut != null`.

Schema changes made:

- Added the five new inputs to `andrea/catalog_inference_tools/schemas/toolspec.schema.json`.
- Added the five new extras to `andrea/catalog_inference_tools/schemas/dataset-manifest.schema.json`.

## ToolSpec Evidence Ledger

### `schema_version`

- Chosen value: `1.0`
- Evidence: existing ANDREA ToolSpec schema requires `schema_version = "1.0"`.
- Rationale: catalog compatibility.
- Uncertainty: none.

### `id`

- Chosen value: `miniex3`
- Evidence: user scaffold and requested tool ID.
- Rationale: stable ANDREA identifier distinguishes current v3 integration from v1/v2 papers.
- Uncertainty: upstream product name is MINI-EX, not `miniex3`; ID remains user-specified.

### `name`

- Chosen value: `MINI-EX v3`
- Evidence: `README.md` product name is MINI-EX and states MINI-EX v3.* is released; `miniex.nf`/`miniex.config` snapshot use v3.2 artifacts.
- Rationale: display the official name while making clear this integration targets the v3 repository state.
- Uncertainty: local commit is after tag `v3.2`; no separate v3 paper was found.

### `publication`

- Chosen value:
  - `https://doi.org/10.1016/j.molp.2022.10.016`
  - `https://doi.org/10.1007/978-1-0716-4972-5_12`
- Evidence: DOI lines in both local paper text files and citation block in `README.md`.
- Rationale: primary method paper first, version/tutorial paper second.
- Uncertainty: no separate v3 publication exists in the local material.

### `first_author`

- Chosen value: `Camilla Ferrari`
- Evidence: primary paper title page.
- Rationale: first author of the primary method paper listed first in `publication`.
- Uncertainty: none.

### `year`

- Chosen value: `2022`
- Evidence: primary paper citation and title page.
- Rationale: year of the primary method paper.
- Uncertainty: v3 code is newer, but ToolSpec field is publication year.

### `method_summary`

- Chosen value: scRNA-seq plant cell-type GRN inference using GRNBoost2, motif enrichment/expression filtering and Borda prioritization.
- Evidence: primary paper abstract; `README.md` pipeline summary; `docs/configuration.md` parameter descriptions.
- Rationale: describes method core, not wrapper behavior.
- Uncertainty: no v3-specific paper, so v3 additions are supported by repo README rather than a paper.

### `method_keywords`

- Chosen value: `single_cell`, `cell_type_specific`, `grnboost2`, `motif_enrichment`, `borda_ranking`, `plant`
- Evidence: primary paper keywords and abstract; `README.md` pipeline summary.
- Rationale: captures input domain and algorithmic stages.
- Uncertainty: none material.

### `implementation_url`

- Chosen value: `https://github.com/VIB-PSB/MINI-EX/tree/3f220a68e8057fe4d33665e956a3f3bbe41ff4c8`
- Evidence: local repo remote and requested/latest known commit.
- Rationale: no official language package was found in the repo; use pinned public source rather than local clone.
- Uncertainty: upstream also publishes a container image `psbdock/mini-ex:v3.2`, but the wrapper should not rely on the local repo folder at runtime.

### `docker_image`

- Chosen value: `adriansegura99/inference-tools_miniex3:1.0.0`
- Evidence: existing ANDREA inference tool image naming convention.
- Rationale: placeholder for Phase 2 wrapper image.
- Uncertainty: exact image can be adjusted if the project changes registry naming.

### `execution_capabilities`

- Chosen value: `["group_native"]`
- Evidence: see Execution Capabilities above.
- Rationale: native grouped inference is the only semantically correct final-output mode.
- Uncertainty: none for final MINI-EX output; GRNBoost2 intermediate could be global, but that would not be MINI-EX.

### `accepts`

- Chosen value: `["cells"]`
- Evidence: README and papers repeatedly specify scRNA-seq cells as columns of the expression matrix.
- Rationale: MINI-EX is single-cell specific.
- Uncertainty: none.

### `assumes`

- Chosen value: `scrna_specific`
- Evidence: README purpose and paper abstracts.
- Rationale: method requires scRNA-seq cluster metadata and marker genes.
- Uncertainty: none for scRNA specificity; plant restriction is represented separately in `taxonomic_scope`.

### `taxonomic_scope`

- Chosen value: `allowed_groups=["plant"]`, `supported_species=[3702, 4530, 4081, 4577]`.
- Evidence: README and built-in species resources expose Arabidopsis thaliana, Oryza sativa, Solanum lycopersicum and Zea mays references.
- Rationale: MINI-EX is plant-focused and motif/GO resources are species-specific.
- Uncertainty: custom species annotation bundles are excluded from Phase 1.

### `compatibility_rules`

- Chosen value: block non-supported plant taxa when `doMotifAnalysis=true`; warn for non-supported plant taxa when `doMotifAnalysis=false`; block `reference_species=none` with motif analysis; block mismatches between `reference_species` and dataset NCBI taxonomy ID.
- Evidence: README/configuration docs describe supported species folders and `doMotifAnalysis` toggling; `reference_species` maps to built-in annotation folders.
- Rationale: preserve the degraded no-motif mode while blocking invalid motif/reference combinations. During catalog scanning, parameter-dependent compatibility rules are surfaced as warnings instead of hard blocks so users can still add the tool and fix the run configuration.
- Uncertainty: Zea mays v4/v5 share the same species taxon ID, so both reference values map to 4577.

### `extra_inputs`

- Chosen value:
  - Required: `groups`, `cluster_markers`, `cluster_identities`, `tf_list`
  - Optional: `terms_of_interest`, `enrichment_background`, `grnboost_network`
  - Conditional: none
- Evidence: `README.md` inputs; `docs/data_preparation.md`; `MINIEX_checkUserInput.py`; `miniex.nf`.
- Rationale: these match upstream required/optional user inputs while using normalized semantics. `groups` is required because this tool's only advertised execution capability is native grouped inference and upstream always needs cell-to-cluster mapping.
- Uncertainty: custom species annotation inputs are not exposed in Phase 1, as documented above.

### `outputs`

- Chosen value: directed `true`, sign `none`, evidence `association`.
- Evidence: `example/README.md` edge table columns are TF, TG, cluster, Borda ranks and GRNBoost2 weight; no sign column is documented.
- Rationale: edges are TF to target gene. MINI-EX uses expression association plus motif/expression filtering and ranks, but does not output activation/repression sign.
- Uncertainty: evidence enum lacks a motif-supported association subtype, so `association` is the closest schema value.

### `progress`

- Chosen value: `task_partitions`.
- Evidence: public interface is a Nextflow workflow with named processes, and `grnboostSubjobs` splits GRNBoost2 into independent subjobs.
- Rationale: progress can be reported as coarse Nextflow process/task progress rather than target gene loops.
- Uncertainty: exact percentages will be wrapper-defined and approximate; upstream does not expose stable per-edge callbacks.

### `params`

- `reference_species = "ath"`: wrapper-level selection of built-in upstream data directories. Evidence: `miniex.config` defaults to `data/ath`; `docs/configuration.md` lists supported species; local data includes `ath`, `osa`, `sly`, `zma_v4`, `zma_v5`.
- `doMotifAnalysis = true`: upstream default. Evidence: `miniex.config` and `docs/configuration.md`.
- `motifFilter = "TF-F_motifs"`: upstream default and allowed enum. Evidence: `miniex.config`, `docs/configuration.md`, `MINIEX_checkUserInput.py`.
- `topMarkers = 700`: upstream default. Evidence: `miniex.config`, `docs/configuration.md`, primary paper parameter optimization.
- `expressionFilter = 10`: upstream default and valid range 0-100. Evidence: `miniex.config`, `docs/configuration.md`, `MINIEX_checkUserInput.py`.
- `topRegulons = 150`: upstream default and visualization-only. Evidence: `miniex.config` and `docs/configuration.md`.
- `grnboostSubjobs = 20`: upstream default, technical partition count. Evidence: `miniex.config`, `docs/configuration.md`, `MINIEX_checkUserInput.py`.
- Rationale: preserve upstream defaults and expose documented behavior-changing settings; avoid exposing executor/memory because those are runtime infrastructure, not method parameters.
- Uncertainty: upstream validation function accepts integer `0` for some "positive" parameters, but `grnboostSubjobs=0` would make splitting invalid. ToolSpec sets `grnboostSubjobs` minimum to 1 and leaves `topMarkers`/`topRegulons` minimum at 0 to match upstream validation.

### `artifacts_aux`

- Chosen value:
  - `miniex.log`
  - `raw/miniex.config`
  - `raw/grnboost2`
  - `raw/regulons`
  - `raw/figures`
  - `raw/go_enrichment`
- Evidence: `README.md` and `example/README.md` list output directories and log; wrapper generates the config at runtime.
- Rationale: preserve upstream raw outputs and the exact config used for reproducibility.
- Uncertainty: `figures` and `go_enrichment` may be empty or absent depending on data/terms; wrapper should create/copy stable directories and mark them not requiring non-empty content.

## Upstream Defaults and Runtime-Dependent Rules

- `termsOfInterest = null`: upstream uses standard ranking. If a terms file is supplied, `MINIEX_selectBordaProcedure.py` uses the reference procedure only when at least two relevant TFs are found in GO annotations; otherwise it prints `std`. ToolSpec preserves this by making `terms_of_interest` optional and not adding a separate Borda procedure parameter.
- `enrichmentBackground = null`: `miniex.nf` uses expressed genes from the expression matrix as the enrichment background. ToolSpec preserves this with optional `enrichment_background`; the wrapper should pass `null` when absent.
- `grnboostOut = null`: `miniex.nf` runs GRNBoost2 de novo; if provided, it skips GRNBoost2 and reuses the file. ToolSpec preserves this with optional `grnboost_network`; the wrapper should pass `null` when absent.
- `clustersToIdentities` optional third column: upstream docs/checks allow it, but the pinned v3.2 downstream scripts used by the public Nextflow workflow read the file as two columns in `MINIEX_makeInfoFile.py` and `MINIEX_makeRankingDataframe.py`. The wrapper validates the normalized optional `order` field but omits it from the generated upstream file so the documented public workflow runs consistently. This affects visualization ordering only, not `network.csv` edge scores.
- `doMotifAnalysis=true` requires non-null motif mapping and TF-family/motif info; `MINIEX_checkUserInput.py` rejects motif analysis when those files are null. The wrapper must set built-in files from `reference_species` or reject `reference_species=none` with `doMotifAnalysis=true`.
- `goFile=null` requires `termsOfInterest=null`; the wrapper must reject `terms_of_interest` when `reference_species=none`.

## Output Mapping to `network.csv`

- Upstream raw edge source: `raw/regulons/*_edgeTable.tsv`.
- Upstream columns: `TF`, `TG`, `cluster`, `borda_rank`, `borda_clusterRank`, `weight`.
- ANDREA mapping:
  - `source`: upstream `TF`
  - `target`: upstream `TG`
  - `score`: upstream `weight`
  - `sign`: `?`
  - `evidence`: `association`
  - `context`: `group:<cluster>`
- Zero scores: rows with `weight == 0` should be omitted.
- Score-scale decision: preserve raw `weight`, the GRNBoost2 edge importance reported by upstream. `borda_rank` and `borda_clusterRank` are regulon-level ranks, not direct edge scores, so they should remain in raw auxiliary artifacts rather than replacing the edge score.
- Evidence: `example/README.md` defines edge table columns and explicitly labels `weight` as GRNBoost2 edge weight; `MINIEX_scoreEdges.py` joins GRNBoost2 weights and Borda ranks into the edge table.

## Installation Strategy

- Preferred public installation source: pinned upstream GitHub repository at `3f220a68e8057fe4d33665e956a3f3bbe41ff4c8`.
- Evidence: local repo has no Python package metadata for a pip/conda package; README documents Nextflow plus Singularity, and `miniex.config` references upstream container `psbdock/mini-ex:v3.2`.
- Phase 2 implementation: the ANDREA image starts from the official upstream runtime image `psbdock/mini-ex:v3.2`, installs Java, Nextflow and a modern system Python for the ANDREA wrapper, clones `https://github.com/VIB-PSB/MINI-EX.git` at commit `3f220a68e8057fe4d33665e956a3f3bbe41ff4c8` into `/opt/MINI-EX`, and runs `nextflow -C <generated config> run /opt/MINI-EX/miniex.nf`. Runtime does not depend on `wrappers/inference_tools/tools/miniex3/repo/`.
- Fallback option: reuse upstream Dockerfile/runtime dependency set from the pinned public repo if rebuilding dependencies is more reliable than starting from a Python scaffold.

## Wrapper Notes for Phase 2

- Convert normalized `groups.tsv` with header to upstream headerless `*_cells2clusters.tsv`.
- Convert normalized `cluster_identities.tsv` with header to upstream headerless two-column `*_identities.tsv`; validate optional `order` but omit it for the pinned v3.2 workflow compatibility reason documented above.
- Convert normalized `cluster_markers.tsv` to upstream `*_allMarkers.tsv`, preserving Seurat-compatible columns.
- Convert normalized `tf_list.txt` to upstream TF list path.
- If `grnboost_network.tsv` is supplied, convert headered `source,target,score` to upstream headerless `TF,TG,weight`.
- If `terms_of_interest.txt` or `enrichment_background.txt` is absent, pass `null` in the generated config so upstream dynamic defaults are preserved.
- Generate stable dataset-prefixed filenames because upstream joins files by prefix before the first underscore.
- Validate and reject underscores in cluster annotations before invoking upstream, matching `docs/data_preparation.md`; original group IDs may contain underscores because the wrapper maps them to internal upstream-safe IDs and maps them back in `network.csv`.

## Smoketest Plan

- Use a small cell expression matrix with at least two clusters, a TF list, marker table, groups file and cluster identity file.
- Prefer `doMotifAnalysis=false` and `reference_species=none` for the first smoke test to avoid species annotation/motif dependencies and make the test independent of real plant IDs.
- Use a tiny provided `grnboost_network` in the smoke test if GRNBoost2 runtime is too heavy; otherwise run de novo with low `grnboostSubjobs`.
- Validate that `network.csv` contains nonzero directed TF-to-target rows with `context=group:<cluster>`, and that raw upstream artifacts are copied under `raw/`.

## Phase 2 Implementation Notes

- Wrapper: `wrappers/inference_tools/tools/miniex3/run_tool.py`.
- Dockerfile: `wrappers/inference_tools/tools/miniex3/Dockerfile`.
- Build context handling: `wrappers/inference_tools/scripts/build_tool_images.py` now excludes local `repo/`, `papers/` and cache directories from temporary Docker build contexts, matching the no-local-repo runtime rule and avoiding a 1 GB local MINI-EX copy in the build context.
- Runtime inputs:
  - Required extras: `groups.tsv`, `cluster_markers.tsv`, `cluster_identities.tsv`, `tf_list.txt`.
  - Optional extras: `terms_of_interest.txt`, `enrichment_background.txt`, `grnboost_network.tsv`.
  - For `reference_species=none`, wrapper rejects `doMotifAnalysis=true` and rejects `terms_of_interest.txt` because GO annotations are unavailable.
- Output conversion:
  - Reads `raw/regulons/*_edgeTable.tsv`.
  - Writes `network.csv` rows as `source=TF`, `target=TG`, `score=weight`, `sign=?`, `evidence=association`, `context=group:<original group>`.
  - Filters self-loops and zero weights.
  - Preserves raw upstream artifacts under `raw/`.
- Smoketest fixture behavior:
  - Uses shared `expression.tsv`, `groups.tsv`, `tf_list.txt`, `cluster_markers.tsv`, `cluster_identities.tsv`, `grnboost_network.tsv` and `enrichment_background.txt`.
  - Uses `reference_species=none`, `doMotifAnalysis=false`, `grnboost_network.tsv` and `topRegulons=6`. `topRegulons` is visualization-only upstream; `6` avoids an upstream heatmap failure that can occur with too few unique TFs in tiny smoke data.
- Smoketest outcome: passed with 24 `network.csv` rows and 6 auxiliary artifact checks.

## Phase 3 Validation

- `python wrappers/inference_tools/scripts/validate_input_specs.py`: passed; 13 input specs valid.
- `python wrappers/inference_tools/scripts/validate_toolspecs.py`: passed; 10 ToolSpecs valid, including `miniex3`.
- `python wrappers/inference_tools/scripts/run_smoketests.py --tool miniex3 --timeout 1200`: passed after rebuilding `miniex3-smoketest:local`; output contained 24 `network.csv` rows and 6 validated auxiliary artifacts.
- No remaining inconsistency was found between the wrapper, ToolSpec, normalized inputs and smoketest configuration.

## Taxonomic Compatibility Refinement

- Supported species evidence remains the upstream v3 repository: `docs/configuration.md` lists built-in species directories for *Arabidopsis thaliana* (`ath`), *Oryza sativa* (`osa`), *Solanum lycopersicum* (`sly`) and *Zea mays* (`zma`), and `data/` contains the corresponding reference files.
- NCBI IDs represented in ToolSpec: `3702` for *Arabidopsis thaliana*, `4530` for *Oryza sativa*, `4081` for *Solanum lycopersicum* and `4577` for *Zea mays*. Both `zma_v4` and `zma_v5` map to `4577`.
- Defaults remain the upstream/example defaults: `reference_species=ath` and `doMotifAnalysis=true`.
- Catalog scanning now treats compatibility rules involving `param.*` or `execution.mode` as configurable warnings, not as hard blocks. Supported non-Arabidopsis datasets therefore remain selectable in the catalog; after adding a run, the run card/preflight still marks the default configuration invalid until the user selects the matching `reference_species` or disables motif analysis as appropriate.
- Validation after this refinement:
  - `python wrappers/inference_tools/scripts/validate_toolspecs.py`: passed.
  - `python wrappers/inference_tools/scripts/validate_input_specs.py`: passed.
  - `.venv/bin/python -m pytest tests/core/commands/infer_network/test_preflight.py`: passed.
  - `python wrappers/inference_tools/scripts/run_smoketests.py --tool miniex3 --timeout 1200`: passed.

## Known Limitations / Open Questions

- Full custom species annotation bundles are intentionally excluded in Phase 1. Supporting them properly likely requires new annotation input specs, compressed motif-map handling and stronger conditional validation.
- `reference_species` is wrapper-level because upstream represents species as file-path choices in `miniex.config`, not as one named parameter.
- MINI-EX is plant-focused and that restriction is now explicit in `taxonomic_scope` and `compatibility_rules`.
