# scMultiSim Integration Decisions

Phase: 3 complete. This file is the working contract for the wrapper and Docker implementation.

## Upstream Target

- Simulator id: `scmultisim`
- Canonical project name: `scMultiSim`
- Public implementation URL: `https://github.com/ZhangLabGT/scMultiSim`
- Public installation route: Bioconductor package `scMultiSim`
- Pinned runtime version: `1.8.0`
- Docker image: `adriansegura99/simulator_scmultisim:1.0.0`
- Public entrypoint mirrored by the wrapper: `sim_true_counts(options)`, optionally followed by public post-processing functions `add_expr_noise()` and `divide_batches()`.
- Runtime must not depend on `wrappers/simulation_data_tools/simulators/scmultisim/repo/`.

## Evidence Used

- Local Bioconductor manual: [scMultiSim.txt](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/scMultiSim.txt)
- Local Bioconductor package page: [Bioconductor - scMultiSim.html](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/Bioconductor%20-%20scMultiSim.html)
- Upstream README: [README.md](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/README.md)
- Package DESCRIPTION from local repo snapshot: [DESCRIPTION](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/DESCRIPTION)
- Main public API implementation: [R/1_main.R](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/R/1_main.R)
- Option definitions: [R/0_opts.R](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/R/0_opts.R)
- Dynamic GRN implementation: [R/3.2_dyngrn.R](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/R/3.2_dyngrn.R)
- Technical-noise implementation: [R/6_technoise.R](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/R/6_technoise.R)
- Vignettes: [workflow.Rmd](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/vignettes/workflow.Rmd), [basics.Rmd](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/vignettes/basics.Rmd), [options.Rmd](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/vignettes/options.Rmd), [spatialCCI.Rmd](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/vignettes/spatialCCI.Rmd)
- Primary paper text: [nihms-2090221.txt](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/papers/nihms-2090221.txt)

## Phase 1 Upstream Truth-Context Audit

Evidence reviewed for the truth-context redesign:

- `repo/scMultiSim/R/0_opts.R`
- `repo/scMultiSim/R/1_main.R`
- `repo/scMultiSim/R/2_sim.R`
- `repo/scMultiSim/R/3.2_dyngrn.R`
- `repo/scMultiSim/R/9_meta.R`
- `repo/scMultiSim/vignettes/basics.Rmd`
- `repo/scMultiSim/vignettes/options.Rmd`
- `repo/scMultiSim/man/sim_true_counts.Rd`

Upstream regulatory truth:

- `sim_true_counts(options)` returns `.grn`, and upstream docs define `.grn$geff` as the gene-by-TF matrix representing the GRN used during simulation. Rows are target genes and columns are regulators.
- The `GRN` option accepts a three-column data frame `(target, regulator, effect)` or `NA` to disable GRN effects. With a GRN, scMultiSim normalizes names, builds `GRN$geff`, and uses the matrix in the expression model.
- `dynamic.GRN` defaults to `NA` and is described as enabling dynamic cell-specific GRN. Internally, dynamic GRN wraps the static GRN in a mutable `dynGRN` object, repeatedly deletes/adds edges and stores a matrix history.
- When dynamic GRN is enabled, `sim_true_counts()` returns `cell_specific_grn`, a list with one gene-by-TF matrix per cell. The vignette states that each element indicates that cell's GRN and that adjacent cells can share a GRN according to `cell.per.step`.

Population, group and pseudotime evidence:

- The `tree` option is a phylo object defining relationships between populations. The basics vignette states that continuous populations use the tree as a differentiation trajectory, while discrete populations use tree tips as clusters/cell types.
- `cell_meta` is returned by `sim_true_counts()` and contains cell type labels and pseudotime information. In source, non-spatial results combine `cell_id` with CIF metadata; discrete CIF metadata contains `pop`, `cell.type` and `cell.type.idx`, while continuous CIF metadata contains trajectory-derived `pop` and `depth`.
- `cell_time` is only returned when `do.velocity=TRUE`; source initializes and fills it inside the kinetic model branch. Without velocity, pseudotime must come from `cell_meta$depth` or a deterministic fallback.

Truth-context implications:

- Static/global GRN truth can be normalized from `.grn$geff` when dynamic GRN is disabled.
- Under dynamic GRN, `.grn` is a mutable dynamic-GRN object and its final `geff` is not a stable native global summary of all cell states. The native per-cell truth is `cell_specific_grn`; a global dynamic truth, if needed, should be an ANDREA aggregate over the cell-specific matrices.
- scMultiSim does not expose a native group-level GRN object. Group truth must be derived by ANDREA by aggregating `cell_specific_grn` over exported group labels, usually from `cell_meta$pop`.
- Cell truth is native only when `dynamic.GRN` is enabled and `cell_specific_grn` is returned.

Profile-relevant upstream switches:

- `dynamic.GRN` must be enabled for native cell-specific GRNs and for any group truth derived from changing per-cell GRNs.
- `tree`, `discrete.cif`, `discrete.pop.size`, `discrete.min.pop.size` and `discrete.min.pop.index` control population labels and therefore exported group contexts.
- `do.velocity` controls whether upstream `cell_time` exists. Otherwise, the wrapper must use `cell_meta$depth` or an ANDREA fallback for pseudotime-like extras.

Uncertainty and policy decisions to validate in later phases:

- A global summary for dynamic runs is not a single native upstream artifact. Aggregating `cell_specific_grn` is an ANDREA policy and must be documented as derivable, not native.
- Group-level truth is also an ANDREA policy over native per-cell matrices and group labels.
- The current wrapper/spec must be audited separately to ensure `scrna_cell_specific` emits cumulative `global`, `group` and `cell` truth contexts, not only `cell`.

## Phase 2 Wrapper Behavior Audit

Wrapper source audited:

- `run_simulator.R`
- Current catalog entry: `andrea/catalog_simulation_data_tools/simulators/scmultisim/simulatorspec.json`
- Current core truth validation: `andrea/core/commands/generate_data/shared.py` and `pipeline.py`

Behavior matrix:

| Profile | Upstream options activated by wrapper | Native truth artifacts available | Public truth contexts currently emitted | Score/sign transformation | Missing or questionable behavior |
| --- | --- | --- | --- | --- | --- |
| `scrna_global` | `tree_preset=auto` resolves to `Phyla1()`. `dynamic_grn.enabled` defaults to true unless overridden, so the wrapper usually passes `dynamic.GRN` to `sim_true_counts()`. | `.grn$geff`; when dynamic GRN is enabled, native `results$cell_specific_grn`. | `global`. | Static runs use nonzero `.grn$geff` entries with `score=abs(effect)` and sign from effect. Dynamic runs aggregate all `cell_specific_grn` matrices using `mean(abs(effect))` and sign from mean signed effect. | The dynamic global truth is derivable ANDREA policy, not a single native scMultiSim global artifact. This must be explicit in the final spec. |
| `scrna_grouped` | `tree_preset=auto` resolves to `Phyla5()`. `needs_group_specific=true`; wrapper requires `dynamic_grn.enabled=true`. | `.grn$geff`, native `results$cell_specific_grn`, `cell_meta$pop`, optional `cell_time` when velocity is enabled. | `global` and `group:<group_id>`. | Global as above. Group truth aggregates per-cell GRN matrices by `cell_meta$pop`, with `score=mean(abs(effect))` and sign from mean signed effect. | Correct for the current grouped profile; group truth is derived, not native. |
| `scrna_cell_specific` | Wrapper requires `dynamic_grn.enabled=true` only for cell truth. It does not currently treat the profile itself as group-specific. | `.grn$geff`, native `results$cell_specific_grn`, and group labels available from `cell_meta$pop`. | `global` and `cell:<cell_id>`. | Global as above. Cell truth normalizes each native per-cell matrix with `score=abs(effect)` and sign from effect. | Missing `group:<group_id>` contexts for the new cumulative profile contract. The wrapper only derives groups for this profile when group-related extras are requested, and even then it does not append derived group truth rows because `need_public_group_truth=false`. |

Branch verification:

- `build_sim_options()` always passes `dynamic.GRN = build_dynamic_grn_option(params, grn)`, and `build_dynamic_grn_option()` returns `NA` when `dynamic_grn.enabled=false`.
- `derive_global_truth()` uses an aggregate over `results$cell_specific_grn` when dynamic GRN is enabled; otherwise it uses `.grn$geff`.
- `derive_group_truth()` already aggregates `results$cell_specific_grn` by exported groups and is suitable for `scrna_cell_specific` once the caller requests group truth.
- `derive_cell_truth()` is called only when `request$profile == "scrna_cell_specific"`.
- `needs_group_specific` is currently true for `scrna_grouped`, `prior_grn_by_group` or `lineage_tree`, not for `scrna_cell_specific` by profile alone.
- `need_public_group_truth` is currently `request$profile == "scrna_grouped"`.
- `need_groups` is currently true for public group truth or selected group-related extras, not for `scrna_cell_specific` by profile alone.
- `write_truth_networks()` can already combine global, group and cell rows if the caller supplies them.

Phase 2 mismatches fixed in later phases:

- The wrapper could derive the required group truth for `scrna_cell_specific` from `results$cell_specific_grn` and `cell_meta$pop`, but did not yet request or append it.
- The scMultiSim SimulatorSpec did not yet claim derivable group truth for `scrna_cell_specific`.
- Core treated `scrna_cell_specific` as requiring only `cell`, so a missing `group:*` context was not blocked.
- Smoke tests did not yet require `global`, `group:` and `cell:` prefixes for scMultiSim cell-specific output.

Concrete wrapper fix list:

- Set `needs_group_specific` for `scrna_grouped` and `scrna_cell_specific` so the dynamic-GRN requirement also covers cumulative cell-specific group truth.
- Set `need_public_group_truth` for `scrna_grouped` and `scrna_cell_specific`.
- Set `need_groups` for `scrna_cell_specific` even when no group extra was explicitly requested, because group truth needs deterministic cell-to-group labels.
- Reuse the existing `derive_group_truth()` path for cell-specific runs and append its `group_truth_rows` to `truth/networks.csv`.
- Keep static global truth from `.grn$geff` when dynamic GRN is disabled; keep dynamic global truth as an explicit aggregate over `cell_specific_grn`.
- Preserve current cell truth score/sign semantics.

## Phase 3 Wrapper Corrections

- `scrna_cell_specific` now participates in `needs_group_specific`, so disabling `dynamic_grn.enabled` blocks both grouped and cell-specific profiles when their group/cell truth depends on `cell_specific_grn`.
- `scrna_cell_specific` now sets `need_public_group_truth=true` in addition to `need_public_cell_truth=true`.
- Cell-specific runs now derive and write groups even when no group extra was explicitly requested, because `group:<group_id>` truth contexts require deterministic cell-to-group assignments.
- The existing `derive_group_truth()` path is reused for cell-specific runs, so `truth/networks.csv` now includes `global`, `group:<group_id>` and `cell:<cell_id>` contexts for `scrna_cell_specific`.
- Static global truth still uses `.grn$geff` when dynamic GRN is disabled. Dynamic global truth remains an explicit aggregate over `cell_specific_grn`.
- The existing cell truth source remains native `results$cell_specific_grn`.
- The `scmultisim_cell_specific` smoke config now requires `global`, `group:` and `cell:` truth contexts and checks `provenance/raw/group_networks_index.tsv`.
- Focused smoke outcome: `python wrappers/simulation_data_tools/scripts/run_smoketests.py --configs-root /tmp/andrea_phase3_smoke --simulator scmultisim` passed after rebuilding the scMultiSim image.

## Field-By-Field Contract

| Field | Chosen value | Evidence | Rationale | Uncertainty |
| --- | --- | --- | --- | --- |
| `schema_version` | `1.0` | Current simulator schema | The platform is still in development and all current simulator specs use schema `1.0`. | None. |
| `id` | `scmultisim` | User request | Stable lowercase catalog id. | None. |
| `name` | `scMultiSim` | README, paper, Bioconductor page | Matches upstream project/package name. | None. |
| `publication` | `https://doi.org/10.1038/s41592-025-02651-0`, `https://doi.org/10.18129/B9.bioc.scMultiSim` | Paper first page; Bioconductor package page/manual | Primary paper plus software package DOI as canonical URLs. | The paper also cites Zenodo software/manuscript DOIs, but those are provenance snapshots rather than the primary paper/package citation. |
| `first_author` | `Hechen Li` | Paper first page; DESCRIPTION authors | Full first author name. | None. |
| `year` | `2025` | Paper final edited form, Nat Methods 2025 | Publication year from primary paper. | None. |
| `simulation_summary` | Multi-modal single-cell simulator modeling expression, chromatin accessibility, RNA velocity, spatial locations, cell identity, GRN, CCI, chromatin and technical noise. | Paper abstract; README; DESCRIPTION | Uses upstream scientific wording rather than wrapper wording. | None. |
| `simulation_keywords` | `scrna`, `single_cell`, `multi_omics`, `dynamic_grn`, `rna_velocity`, `cell_lineage` | Paper abstract and README | Captures the upstream biological capabilities relevant to ANDREA. | Spatial/CCI keywords are omitted because the initial wrapper does not claim a spatial profile. |
| `implementation_url` | `https://github.com/ZhangLabGT/scMultiSim` | README and paper code availability | Public upstream implementation. | None. |
| `docker_image` | `adriansegura99/simulator_scmultisim:1.0.0` | User requirement and playbook naming rule | Required catalog image name. | None. |
| `extra_inputs.required` | `[]` | Vignettes show built-in `GRN_params_100` and `Phyla*()` trees | The wrapper can run with public built-in GRN and tree presets. | None. |
| `extra_inputs.optional` | `[]` | Vignettes describe user-supplied GRN and tree inputs only as alternatives selected through simulator options | There is no simulator input that is consumed merely because it is present; both supported uploaded files are activated by explicit params. | None. |
| `extra_inputs.conditional_required` | `regulatory_network` when `param.grn_source eq input_tsv`; `tree_newick` when `param.tree_preset eq input_newick` | Vignettes describe GRN data frame and tree via `ape::read.tree()`; `sim_true_counts` options include `GRN` and `tree` | Prevents selecting an input-based preset without the corresponding file, matching the strict simulator-input contract. | None. |
| Simulation input specs | `regulatory_network`, `tree_newick` | `andrea/catalog_simulation_data_tools/input_specs/` | Reusable format/example/validation details live in input specs; `extra_inputs` only records scMultiSim-specific usage and conditional rules. | None. |
| `profile_capabilities.scrna_global` | Supported | scMultiSim simulates single-cell count matrices; `Phyla1()` can avoid specific trajectory | The whole expression matrix can be used as a global scRNA benchmark without exporting groups. | The simulator is still single-cell, not bulk. |
| `profile_capabilities.scrna_grouped` | Supported | `cell_meta$pop`; differentiation tree; discrete and continuous population docs | scMultiSim natively generates population/trajectory labels and can support grouped scRNA benchmarks. | Continuous populations produce trajectory segment labels; discrete populations produce terminal cell-type labels. |
| `profile_capabilities.scrna_cell_specific` | Supported when `dynamic_grn.enabled=true` | Dynamic GRN returns one gene-by-TF matrix per cell as `cell_specific_grn` | The wrapper can normalize native per-cell GRN matrices into public `cell:<cell_id>` truth rows. | Static GRN simulations do not provide distinct cell-level truth and are rejected for this profile. |
| Bulk profiles | Not claimed | README/DESCRIPTION describe single-cell data | Aggregating single cells into bulk would be wrapper invention, not a native profile. | None. |
| `truth_outputs.global` | `derivable` | `.grn$geff`, `regulatory_network`, dynamic `cell_specific_grn` | The wrapper converts matrices into ANDREA `source,target,score,sign,evidence,context` rows and aggregates dynamic GRNs when enabled. | Dynamic global truth is a summary over changing per-cell networks. |
| `truth_outputs.group` | `derivable` for `scrna_grouped` and `scrna_cell_specific`, `none` for `scrna_global` | Dynamic GRN returns one gene-by-TF matrix per cell and the wrapper exports group labels from `cell_meta$pop` | Group truth is derived by aggregating cell-specific GRN matrices over group labels and writing `context=group:<group_id>` rows in `truth/networks.csv`. | Requires `dynamic_grn.enabled=true`; wrapper rejects grouped/cell-specific group truth or group-specific extras when disabled. |
| `truth_outputs.cell` | `native` for `scrna_cell_specific` | `results$cell_specific_grn` | Per-cell matrices are emitted directly by upstream scMultiSim when dynamic GRN is enabled; the wrapper only normalizes them into the public edge table. | Requires `dynamic_grn.enabled=true`. |
| `derivable_extras` | Global: enrichment background, pseudotime, prior GRN, TF list. Grouped and cell-specific: all global extras plus groups, cell phenotypes, cluster identities, lineage tree and prior GRN by group. | `sim_true_counts` return docs; vignettes; dynamic GRN implementation | Cell-specific is cumulative, so group-layer extras are derivable from the same `cell_specific_grn` aggregation used for public group truth. | `lineage_tree` for discrete terminal clusters is less direct than for continuous trajectory segments. |
| `native_outputs` | true counts, observed counts, ATAC counts, cell metadata, velocity, cell-specific GRN | `sim_true_counts` value docs; `add_expr_noise`; `divide_batches` | These are upstream-native artifacts worth preserving under provenance/native outputs. | Phase 2 may restrict user-requestable native outputs if runtime size is high. |
| `params` | Serializable subset of `sim_true_counts`, `dynamic.GRN`, `add_expr_noise`, `divide_batches`; function hooks as presets | `R/0_opts.R`, `options.Rmd`, `R/6_technoise.R` | Exposes the supportable public surface while preventing arbitrary R callbacks through JSON. | Spatial/CCI params are intentionally excluded in this first contract. |

## Canonical Profile Decisions

- `scrna_global`
  - Supported as a full single-cell expression matrix without exporting group labels.
  - `tree_preset=auto` resolves to `Phyla1()` for this profile.
  - `population_mode=auto` resolves to continuous.
- `scrna_grouped`
  - Supported through `cell_meta$pop`.
  - `tree_preset=auto` resolves to `Phyla5()` for this profile.
  - `population_mode=auto` resolves to continuous so exported groups represent trajectory branches/segments by default. Users may choose `discrete`, in which case groups represent terminal populations.
- `scrna_cell_specific`
  - Supported through native `cell_specific_grn`.
  - Requires `dynamic_grn.enabled=true`.
  - `tree_preset=auto` resolves to `Phyla5()` for this profile.
  - `population_mode=auto` resolves to continuous.
  - Public global truth is an aggregate over the native per-cell GRN matrices.
  - Public group truth is a cumulative ANDREA summary over the native per-cell GRN matrices using `cell_meta$pop`.
  - Public cell truth uses `context=cell:<cell_id>` in `truth/networks.csv`.
- Bulk profiles are not claimed.

## Output Normalization Decisions

- `expression.tsv`
  - If `batch_effect.enabled=true`, use `counts_with_batches`.
  - Else if `technical_noise.enabled=true`, use `counts_obs`.
  - Else use true `counts`.
  - Rows are genes and columns are cells.
- `truth/networks.csv`
  - Static global GRN: convert non-zero `.grn$geff` entries with `context=global`.
  - Dynamic global GRN: aggregate all `cell_specific_grn` matrices with `score=mean(abs(effect))` and sign from the mean signed effect.
  - Group GRN rows for `scrna_grouped`: aggregate `cell_specific_grn` by exported group and write `context=group:<group_id>`.
  - Group GRN rows for `scrna_cell_specific`: aggregate the same native per-cell matrices by exported group and write cumulative `context=group:<group_id>` rows.
  - Cell GRN rows for `scrna_cell_specific`: convert each native `cell_specific_grn` matrix into nonzero `context=cell:<cell_id>` rows with `score=abs(effect)` and sign from the effect sign.
  - Score stores magnitude only; sign stores direction of the mean signed effect.
- `truth/gene_universe.txt`
  - All genes present in the exported expression matrix.
- `extras/groups.tsv`
  - From `cell_meta$pop`.
- `extras/cell_phenotypes.tsv`
  - Cell-level phenotype equals group; order from mean pseudotime/tree-depth.
- `extras/cluster_identities.tsv`
  - One row per group; annotation equals group; order from mean pseudotime/tree-depth.
- `extras/enrichment_background.txt`
  - All generated expression genes.
- `extras/lineage_tree.tsv`
  - Derived from the selected tree plus group active-edge summaries.
- `extras/pseudotime.tsv`
  - Prefer `cell_time`; else `cell_meta$depth`; else deterministic tree-depth fallback.
- `extras/prior_grn.tsv`
  - Oracle global prior from `.grn$geff`.
- `extras/tf_list.txt`
  - From `.grn$regulators`.
- `extras/prior_grn_by_group.tsv`
  - Oracle group prior from group-aggregated `cell_specific_grn`.
- Provenance/raw should include at least:
  - serialized scMultiSim result object
  - normalized/scMultiSim options snapshot
  - raw counts and metadata exports
  - group edge activity table when group GRNs are requested
  - session info
  - wrapper request JSON

## Parameter Surface

Included serializable public options:

- General: `speed_up`
- GRN: `grn_source`, `num_genes`, `unregulated_gene_ratio`, `grn_effect`, `giv`, `high_expression`
- Tree and cell population: `tree_preset`, `population_mode`, `num_cells`, `num_cifs`, `diff_cif_fraction`, `cif_center`, `cif_sigma`, `use_impulse`, `discrete_population`
- ATAC-related RNA influence: `atac`
- RNA model: `rna_model`
- RNA velocity: `velocity`
- Dynamic GRN: `dynamic_grn`
- Technical noise: `technical_noise`
- Batch effects: `batch_effect`
- Function-valued callbacks represented as presets:
  - `mod_cif_giv_preset = "none"`
  - `ext_cif_giv_preset = "none"`

Not exposed as free-form JSON:

- `mod.cif.giv`: upstream expects an R function.
- `ext.cif.giv`: upstream expects an R function.
- `atac.density`: upstream expects an R density object.
- Spatial/CCI `cci`: upstream supports it, but ANDREA does not yet have a canonical spatial simulation profile or normalized CCI artifacts.
- `GRN=NA`: upstream supports disabling GRN effects, but the ANDREA simulator contract focuses on benchmarkable regulatory truth, so the initial wrapper requires a built-in or input GRN.
- `rand.seed`: supplied by ANDREA's run seed and mapped internally to the upstream `rand.seed`.

Data-dependent/runtime-dependent defaults:

- `tree_preset=auto`
  - `scrna_global` -> `Phyla1()`
  - `scrna_grouped` -> `Phyla5()`
- `population_mode=auto`
  - `scrna_global` -> continuous
  - `scrna_grouped` -> continuous
- `num_genes=null`
  - Defer to scMultiSim: derived from GRN size and `unregulated_gene_ratio`.
- `num_genes` with fixed or input GRNs
  - scMultiSim 1.8.0 does not reliably honor `num_genes` when it is smaller than the fixed/input GRN gene universe, and it fails when it equals that universe.
  - ANDREA therefore blocks `grn_source=builtin_100` with `num_genes<=100`, blocks `grn_source=builtin_1139` with `num_genes<=1136`, and blocks `grn_source=input_tsv` when `num_genes` is not greater than the unique target/regulator gene count in `regulatory_network.tsv`.
  - `num_genes=null` remains valid because it delegates the final gene count to scMultiSim.
- `dynamic_grn.weight_mean=null`
  - Defer to scMultiSim: use the mean effect of the input GRN.
- `dynamic_grn.involved_genes=[]`
  - Preserve the upstream default semantic of using all existing GRN genes. The wrapper passes that all-gene set explicitly in scMultiSim's `geff` row order because scMultiSim 1.8.0 otherwise sorts remapped numeric gene IDs differently from the `geff` row positions inside `dynamic.GRN`.

Runtime resources:

- `threads` is not exposed as a scientific simulator parameter. It is declared
  under `runtime_resources.threading` and mapped by the wrapper to
  `scMultiSim::sim_true_counts(threads=...)`.

## Capabilities Not Claimed Yet

- Spatial locations and cell-cell interaction truth:
  - Evidence: README, paper, `spatialCCI.Rmd`, and `cci` option docs.
  - Reason for exclusion: ANDREA currently lacks a canonical spatial profile and normalized CCI truth/input contracts.
- Multi-modal ATAC benchmark profile:
  - Evidence: `atac_counts`, `atacseq_data`, `region_to_gene`, `region_to_tf`.
  - Reason for exclusion: current generate-data profiles are expression-centric; ATAC is preserved as provenance/native output but not normalized as a benchmark profile.
- Shiny app:
  - Evidence: README and `run_shiny()`.
  - Reason for exclusion: interactive upstream UI is not an executable wrapper contract.

## Phase 2 Implementation Notes

- Wrapper path: [run_simulator.R](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/run_simulator.R)
- Dockerfile path: [Dockerfile](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/scmultisim/Dockerfile)
- Runtime installation:
  - Base image: `bioconductor/bioconductor_docker:RELEASE_3_23`
  - Package install: `BiocManager::install("scMultiSim", version="3.23", update=FALSE)`
  - Version assertion: `packageVersion("scMultiSim") == "1.8.0"`
- The container reads `/work/request/simulator-run-request.json` and writes the normalized output tree directly under `/work/out/`.
- The wrapper writes `progress.json`, `expression.tsv`, `truth/networks.csv`, `truth/gene_universe.txt`, optional `extras/`, `simulator-output-manifest.json`, and provenance under `provenance/raw/`.
- Provenance currently includes the wrapper request, resolved wrapper params, scMultiSim options RDS, result RDS, true counts, cell metadata, session info and group derivation tables when requested.
- The wrapper requires `runtime_resources.threads` in `/work/request/simulator-run-request.json` and records it in `provenance/raw/wrapper_environment.json`.
- `batch_effect.enabled=true` hard-errors unless `technical_noise.enabled=true`, because upstream `divide_batches()` operates on observed counts produced by `add_expr_noise()`.
- `scrna_grouped`, `prior_grn_by_group` and `lineage_tree` hard-error unless `dynamic_grn.enabled=true`, because group truth and those extras depend on native `cell_specific_grn`.
- `scrna_cell_specific` hard-errors unless `dynamic_grn.enabled=true`, because cell truth depends on native `cell_specific_grn`.
- `atac.region_distrib` is validated by the wrapper and passed with a small R class workaround. scMultiSim 1.8.0 validates this length-3 vector with `x > 0 && length(x) == 3`, which fails under R 4.6 unless the vector comparison returns a scalar. The wrapper keeps the public value and uses `sim_true_counts()` unchanged.
- `dynamic_grn.num_changing_edges` hard-errors when it resolves to fewer than two changed edges because scMultiSim 1.8.0 drops matrix dimensions when sampling a single edge in `dynamic.GRN$restructure()`. The ToolSpec default remains upstream-compatible at `2.0`.

## Phase 2 Smoke-Test Matrix

Implemented smoke tests:

1. `scmultisim_global_basic`
   - profile: `scrna_global`
   - extras: none
   - small `num_cells`, `grn_source=builtin_100`, `tree_preset=phyla1`
   - validates `expression.tsv`, `truth/networks.csv`, `truth/gene_universe.txt`, manifest, progress, provenance
2. `scmultisim_global_extras`
   - profile: `scrna_global`
   - extras: `enrichment_background`, `pseudotime`, `prior_grn`, `tf_list`
   - validates all global normalized extras
3. `scmultisim_grouped_full`
   - profile: `scrna_grouped`
   - extras: `groups`, `cell_phenotypes`, `cluster_identities`, `enrichment_background`, `lineage_tree`, `pseudotime`, `prior_grn`, `tf_list`, `prior_grn_by_group`
   - params: `dynamic_grn.enabled=true`
   - validates `group:<id>` rows in the unified truth network and group-specific prior
4. `scmultisim_grouped_custom_inputs`
   - profile: `scrna_grouped`
   - use `grn_source=input_tsv` and `tree_preset=input_newick`
   - covers conditional simulator inputs
5. `scmultisim_grouped_noise_batch`
   - profile: `scrna_grouped`
   - params: `technical_noise.enabled=true`, `batch_effect.enabled=true`
   - validates that `expression.tsv` comes from batch-adjusted observed counts and batch metadata is preserved
6. `scmultisim_cell_specific`
   - profile: `scrna_cell_specific`
   - params: `dynamic_grn.enabled=true`
   - validates `global`, `group:<id>` and `cell:<id>` rows in the unified truth network plus the group/cell-network provenance indexes

## Final Validation Outcome

- `python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py --simulator scmultisim`: passed.
- `python wrappers/simulation_data_tools/scripts/validate_smoketest_configs.py --simulator scmultisim`: passed.
- `python wrappers/simulation_data_tools/scripts/run_smoketests.py --simulator scmultisim`: passed.
- `PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/core/commands/generate_data tests/core/test_generate_data_progress.py tests/cli/test_generate_data_cli.py tests/gui/test_generate_data_server.py tests/wrappers/simulation_data_tools`: passed, 33 passed and 3 skipped.
- Generated a temporary `scrna_grouped` benchmark with `scmultisim` and all declared grouped extras, then ran `infer-network preflight` on the generated `dataset-manifest.json`: passed. Expression and requested extras validated as `ok`; missing inputs were optional extras not requested by the benchmark.
- Docker build confirmed Bioconductor `scMultiSim` version `1.8.0`.

## Phase 4 Truth Unification Update

- Public truth now uses one edge table, `truth/networks.csv`.
- Global scMultiSim truth rows use `context=global`.
- `scrna_grouped` truth rows use `context=group:<group_id>` in the same table.
- `scrna_cell_specific` truth rows use `context=cell:<cell_id>` in the same table, generated from native `cell_specific_grn`.
- The wrapper no longer writes the legacy split public truth files.
- `simulator-output-manifest.json` now reports `truth.gene_universe` and `truth.networks`.
- Group derivation debug files remain under `provenance/raw/`, including `group_edge_activity.tsv`, `group_active_networks.tsv` and `group_networks_index.tsv` when group truth is derived.
- Cell truth derivation writes `provenance/raw/cell_networks_index.tsv` when `scrna_cell_specific` is selected.

## Phase 7 SimulatorSpec Truth Contexts

- `scrna_cell_specific.truth_outputs.group` is now `derivable`, matching the wrapper behavior introduced in Phase 3.
- Every scMultiSim profile declares `truth_contexts` for `global`, `group` and `cell`, with `none` used only for contexts not exported by that profile.
- `scrna_cell_specific` documents the cumulative truth contract explicitly: `global` and `group:<group_id>` are ANDREA-derived summaries over native `results$cell_specific_grn`, while `cell:<cell_id>` is normalized directly from native per-cell matrices.
- `truth_parameter_requirements` records that both `scrna_cell_specific` group truth and cell truth require `dynamic_grn.enabled=true`; grouped group truth keeps the same requirement.
- The `tree_preset=auto` rule is profile-specific in the spec: `Phyla1` for `scrna_global`, `Phyla5` for `scrna_grouped` and `scrna_cell_specific`.
- `population_mode=auto` is documented as continuous for every supported profile.

## Phase 11 Documentation Alignment

- The scMultiSim integration now follows the documented ordering required for future simulators:
  1. upstream source audit,
  2. wrapper behavior audit,
  3. wrapper correction,
  4. spec update from verified wrapper behavior,
  5. smoke proof.
- The decision log records, per profile, which upstream settings are activated and which public truth contexts are emitted.
- The spec is intentionally cumulative:
  - `scrna_global`: `global`.
  - `scrna_grouped`: `global` plus derived `group:<group_id>`.
  - `scrna_cell_specific`: derived `global`, derived `group:<group_id>` and native `cell:<cell_id>`, all from dynamic `cell_specific_grn`.
- `truth_contexts` in the spec records the native upstream artifact or wrapper derivation rule, required dynamic-GRN switches, score/sign semantics and limitations for every `global`, `group` and `cell` entry.
- `truth_parameter_requirements` documents that grouped and cell-specific truth requiring per-cell GRN state depends on `dynamic_grn.enabled=true`.
- The cell-specific smoke config requires `global`, `group:` and `cell:` context prefixes, proving the cumulative contract.

## Remaining Follow-Up

- Spatial/CCI and ATAC benchmark profiles remain intentionally unclaimed until ANDREA has normalized contracts for those modalities.
