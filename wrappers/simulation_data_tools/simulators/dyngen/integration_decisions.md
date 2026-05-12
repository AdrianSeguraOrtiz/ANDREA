# dyngen Integration Decisions

## Upstream target

- Package: `dyngen`
- Public repo: `https://github.com/dynverse/dyngen`
- Public installation route: CRAN package `dyngen`
- Pinned package version: `1.1.1`

## Evidence used

- Paper: [41467_2021_Article_24152.txt](/home/adrian/Grecia/ANDREA/wrappers/simulation_data_tools/simulators/dyngen/papers/41467_2021_Article_24152.txt)
- Package DESCRIPTION: [DESCRIPTION](/home/adrian/Grecia/ANDREA/wrappers/simulation_data_tools/simulators/dyngen/repo/dyngen/DESCRIPTION)
- Installation vignette: [installation.md](/home/adrian/Grecia/ANDREA/wrappers/simulation_data_tools/simulators/dyngen/repo/dyngen/vignettes/installation.md)
- Getting started vignette: [getting_started.md](/home/adrian/Grecia/ANDREA/wrappers/simulation_data_tools/simulators/dyngen/repo/dyngen/vignettes/getting_started.md)
- Public tests: [test-generate_dataset.R](/home/adrian/Grecia/ANDREA/wrappers/simulation_data_tools/simulators/dyngen/repo/dyngen/tests/testthat/test-generate_dataset.R)

## Scientific decisions

- Canonical profiles covered:
  - `scrna_global`
  - `scrna_grouped`
- `dyngen` does not define a bulk RNA-seq simulator, so it is not mapped to bulk profiles.
- Native global truth comes from the package-generated regulatory network.
- Public truth export filters simulator self-loops because ANDREA truth networks exclude self-regulatory edges unless a simulator-specific contract explicitly requires them.
- `groups.tsv` is derived from dyngen `milestone_percentages` by assigning each cell to the milestone with maximum percentage.
- Group truth rows in public `truth/networks.csv` are derived from dyngen `regulatory_network_sc` by aggregating cell-specific regulatory strengths within each exported group. Missing cell-edge values are treated as zero; active edges use `mean(abs(strength)) >= 0.1` and use `context=group:<group_id>`.
- `lineage_tree.tsv` is derived from dyngen `milestone_network` and the public group truth active-edge sets.
- `tf_list.txt` is derived from `feature_info$is_tf`.
- `pseudotime.tsv` is derived from dyngen `milestone_percentages` and a deterministic order over `milestone_network`; branching/disconnected/cyclic topologies are projected to a single wrapper-defined order.
- `cell_phenotypes.tsv` reuses the hard milestone-derived group assignment as an ordered phenotype/state assignment.
- `cluster_identities.tsv` maps each exported group to its dyngen milestone identifier and the same deterministic milestone order.
- `enrichment_background.txt` is the complete set of generated expression genes.
- `prior_grn.tsv` is an oracle global prior from dyngen `feature_network`, with signed scores from `strength` and `effect`.
- `prior_grn_by_group.tsv` is an oracle group-specific prior from aggregated `regulatory_network_sc`; active edges use the same `mean(abs(strength)) >= 0.1` threshold as public group truth, and scores preserve mean signed strength where possible.
- The current single dyngen spec is intentionally operational as well as scientific:
  - only inputs and parameters that ANDREA can actually execute today are declared
  - unsupported custom-backbone file inputs are not advertised yet

## Wrapper decisions

- The wrapper uses the public package API:
  - `initialise_model()`
  - `generate_dataset(format = "list")`
- The wrapper does not depend on the locally cloned repo at runtime.
- The image installs the CRAN package and predownloads dyngen cacheable data files so runtime stays self-contained.
- The ANDREA-facing parameter surface exposes the broad serializable subset of dyngen:
  - backbone selection
  - model sizes (`num_cells`, `num_tfs`, `num_targets`, `num_hks`)
  - distance metric
  - TF-network settings
  - feature-network settings
  - gold-standard settings
  - simulation settings
  - experiment sampling settings
- Runtime parallelism is not exposed as a simulator parameter. The simulator
  spec declares `runtime_resources.threading`; the wrapper maps assigned
  `threads` to R `options(Ncpus=...)` and `dyngen::initialise_model(num_cores=...)`.
- Function-valued hooks from the R package are still not exposed as free-form user callbacks:
  - for example arbitrary `sample_num_regulators` functions
  - custom kinetics sampler functions
  - custom SSA algorithm objects beyond the supported `ssa_etl` serializable path

## Runtime provenance

- The wrapper writes:
  - `progress.json`
  - `simulator-output-manifest.json`
  - `truth/networks.csv`
  - `truth/gene_universe.txt`
  - `provenance/raw/model.rds`
  - `provenance/raw/dataset.rds`
  - `provenance/raw/session_info.txt`

## Phase 4 Truth Unification Update

- Public truth now uses one edge table, `truth/networks.csv`.
- Global dyngen truth rows use `context=global`.
- `scrna_grouped` truth rows use `context=group:<group_id>` in the same table.
- The wrapper no longer writes the legacy split public truth files.
- `simulator-output-manifest.json` now reports `truth.gene_universe` and `truth.networks`.
- Group derivation debug files remain under `provenance/raw/`, including `group_edge_activity.tsv`, `group_active_networks.tsv` and `group_networks_index.tsv` when group truth is derived.
