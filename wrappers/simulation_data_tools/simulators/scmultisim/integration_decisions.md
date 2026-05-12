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
| `simulator_inputs.required` | `[]` | Vignettes show built-in `GRN_params_100` and `Phyla*()` trees | The wrapper can run with public built-in GRN and tree presets. | None. |
| `simulator_inputs.optional` | `[]` | Vignettes describe user-supplied GRN and tree inputs only as alternatives selected through simulator options | There is no simulator input that is consumed merely because it is present; both supported uploaded files are activated by explicit params. | None. |
| `simulator_inputs.conditional_required` | `grn_params` when `param.grn_source eq input_tsv`; `tree_newick` when `param.tree_preset eq input_newick` | Vignettes describe GRN data frame and tree via `ape::read.tree()`; `sim_true_counts` options include `GRN` and `tree` | Prevents selecting an input-based preset without the corresponding file, matching the strict simulator-input contract. | None. |
| `profile_capabilities.scrna_global` | Supported | scMultiSim simulates single-cell count matrices; `Phyla1()` can avoid specific trajectory | The whole expression matrix can be used as a global scRNA benchmark without exporting groups. | The simulator is still single-cell, not bulk. |
| `profile_capabilities.scrna_grouped` | Supported | `cell_meta$pop`; differentiation tree; discrete and continuous population docs | scMultiSim natively generates population/trajectory labels and can support grouped scRNA benchmarks. | Continuous populations produce trajectory segment labels; discrete populations produce terminal cell-type labels. |
| Bulk profiles | Not claimed | README/DESCRIPTION describe single-cell data | Aggregating single cells into bulk would be wrapper invention, not a native profile. | None. |
| `truth_outputs.global` | `derivable` | `.grn$geff`, `grn_params`, dynamic `cell_specific_grn` | The wrapper converts matrices into ANDREA `source,target,score,sign,evidence,context` rows and aggregates dynamic GRNs when enabled. | Dynamic global truth is a summary over changing per-cell networks. |
| `truth_outputs.group` | `derivable` for `scrna_grouped`, `none` for `scrna_global` | Dynamic GRN returns one gene-by-TF matrix per cell | Group truth is derived by aggregating cell-specific GRN matrices over group labels and writing `context=group:<group_id>` rows in `truth/networks.csv`. | Requires `dynamic_grn.enabled=true`; wrapper rejects grouped profile or group-specific extras when disabled. |
| `derivable_extras` | Global: enrichment background, pseudotime, prior GRN, TF list. Grouped: all global extras plus groups, cell phenotypes, cluster identities, lineage tree and prior GRN by group. | `sim_true_counts` return docs; vignettes; dynamic GRN implementation | Every normalized artifact is exported by wrapper conversion from native result objects. Truth by group is profile output, not a selectable extra. | `lineage_tree` for discrete terminal clusters is less direct than for continuous trajectory segments. |
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
- The wrapper no longer writes the legacy split public truth files.
- `simulator-output-manifest.json` now reports `truth.gene_universe` and `truth.networks`.
- Group derivation debug files remain under `provenance/raw/`, including `group_edge_activity.tsv`, `group_active_networks.tsv` and `group_networks_index.tsv` when group truth is derived.

## Remaining Follow-Up

- Spatial/CCI and ATAC benchmark profiles remain intentionally unclaimed until ANDREA has normalized contracts for those modalities.
