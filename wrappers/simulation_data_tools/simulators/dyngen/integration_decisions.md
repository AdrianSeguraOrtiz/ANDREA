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

## Phase 1 Upstream Truth-Context Audit

Evidence reviewed for the truth-context redesign:

- `repo/dyngen/R/3_feature_network.R`
- `repo/dyngen/R/6_simulation.R`
- `repo/dyngen/R/7_experiment.R`
- `repo/dyngen/R/8_convert.R`
- `repo/dyngen/man/generate_dataset.Rd`
- `repo/dyngen/man/generate_cells.Rd`
- `papers/41467_2021_Article_24152.txt`

Upstream regulatory truth:

- `model$feature_network` is the simulator-level regulatory graph. Upstream `generate_feature_network()` builds it from the target-gene regulatory network plus housekeeping-gene network, and the paper describes the input GRN as the regulatory model that drives the simulated dynamic process.
- `generate_cells()` has `compute_cellwise_grn=FALSE` by default. When enabled, dyngen computes a per-cell regulatory effect matrix by re-evaluating propensities after perturbing each regulator state for each simulated cell/state. The resulting matrix columns are named as `from->to` feature-network edges.
- `generate_experiment()` subsets `model$simulations$cellwise_grn` to the profiled cells and assigns row names from `cell_info$cell_id`.
- `generate_dataset(format="list", store_cellwise_grn=...)` exports both a static `dataset$regulatory_network` and, when stored, a long `dataset$regulatory_network_sc`. The documented default for `store_cellwise_grn` follows `model$simulation_params$compute_cellwise_grn`, so cellwise truth requires the simulation flag and the export/storage flag to be active.

Truth-context implications:

- Global truth should come from `model$feature_network` / `dataset$regulatory_network`, including for cell-specific runs. This is the native static regulatory graph used to create the simulation; aggregating cellwise activity would instead summarize state-specific regulatory effects and would not be the same upstream object.
- Cell truth is native only when cellwise GRN computation/storage is enabled. `dataset$regulatory_network_sc` represents per-cell regulatory effects for edges in the feature network.
- dyngen does not expose a native group-level GRN object. Group truth must be derived by ANDREA from cellwise GRN rows and exported group assignments.

Profile-relevant upstream switches:

- `simulation_params.compute_cellwise_grn` must be enabled whenever group or cell truth is needed.
- `generate_dataset(..., store_cellwise_grn=TRUE)` must store the computed cellwise GRN in the exported dataset.
- Group labels are not a native GRN context; they come from trajectory/milestone metadata and are therefore an ANDREA grouping policy over cells.

Uncertainty and policy decisions to validate in later phases:

- The group aggregation rule and active-edge threshold are ANDREA conventions, not dyngen upstream semantics.
- dyngen provides both a static simulator graph and state-specific cellwise effects. For cumulative profile truth, the cleanest contract is `global` from the static graph, `group` derived from cellwise effects, and `cell` from native cellwise effects.

## Phase 2 Wrapper Behavior Audit

Wrapper source audited:

- `run_simulator.R`
- Current catalog entry: `andrea/catalog_simulation_data_tools/simulators/dyngen/simulatorspec.json`
- Current core truth validation: `andrea/core/commands/generate_data/shared.py` and `pipeline.py`

Behavior matrix:

| Profile | Upstream options activated by wrapper | Native truth artifacts available | Public truth contexts currently emitted | Score/sign transformation | Missing or questionable behavior |
| --- | --- | --- | --- | --- | --- |
| `scrna_global` | `compute_cellwise_grn=false` and `store_cellwise_grn=false` unless a selected extra/native output needs cellwise GRN. | `model$feature_network`, `dataset$regulatory_network`. | `global`. | `score=abs(model$feature_network$strength)`; `sign` from `model$feature_network$effect`. | Matches the upstream audit and cumulative contract for this profile. |
| `scrna_grouped` | `compute_cellwise_grn=true` and `store_cellwise_grn=true` because `need_public_group_truth=true`. | Static `model$feature_network` plus cellwise `dataset$regulatory_network_sc`. | `global` and `group:<group_id>`. | Global as above. Group truth uses `mean(abs(strength))`, sign from mean signed strength, and active-edge threshold `mean(abs(strength)) >= 0.1`. | Group truth is correctly derived, but the aggregation threshold is an ANDREA policy rather than upstream dyngen semantics. |
| `scrna_cell_specific` | `compute_cellwise_grn=true` and `store_cellwise_grn=true` because `need_public_cell_truth=true`. | Static `model$feature_network` plus cellwise `dataset$regulatory_network_sc`. | `global` and `cell:<cell_id>`. | Global as above. Cell truth uses `score=abs(strength)` and sign from each cell-specific strength. | Missing `group:<group_id>` contexts for the new cumulative profile contract. The wrapper only derives groups for this profile if group-related extras are requested, and even then it does not append derived group truth rows because `need_public_group_truth=false`. |

Branch verification:

- Global truth derivation is centralized in `derive_global_truth(model)` and always contributes rows to `truth/networks.csv`.
- Group truth derivation exists in `derive_group_truth(dataset, groups_df, raw_dir, active_threshold=0.1)` and is already suitable for both grouped and cell-specific profiles, because it only needs `dataset$regulatory_network_sc` and group assignments.
- Cell truth derivation exists in `derive_cell_truth(dataset, cell_ids, genes, raw_dir)` and is only called for `scrna_cell_specific`.
- `need_public_group_truth` is currently `request$profile == "scrna_grouped"`.
- `need_groups` is currently true for `scrna_grouped` or selected group-related extras, not for `scrna_cell_specific` by profile alone.
- `write_truth_networks()` can already combine global, group and cell rows if the caller supplies them.

Current mismatches to fix before final spec wording:

- The wrapper can derive the required group truth for `scrna_cell_specific`, but it does not currently request or append it.
- The current dyngen SimulatorSpec declares `scrna_cell_specific.truth_outputs.group = "none"`.
- Core currently treats `scrna_cell_specific` as requiring only `cell`, so a missing `group:*` context is not blocked.
- Smoke tests should require `global`, `group:` and `cell:` prefixes for dyngen cell-specific output after the wrapper is corrected.

Concrete wrapper fix list:

- Set `need_public_group_truth` for `scrna_grouped` and `scrna_cell_specific`.
- Set `need_groups` for `scrna_cell_specific` even when no group extra was explicitly requested, because group truth needs deterministic cell-to-group labels.
- Reuse the existing `derive_group_truth()` path for cell-specific runs and append its `group_truth_rows` to `truth/networks.csv`.
- Keep global truth from `model$feature_network`; do not replace it with a cellwise aggregate.
- Preserve current cell truth score/sign semantics.

## Phase 3 Wrapper Corrections

- `scrna_cell_specific` now sets `need_public_group_truth=true` in addition to `need_public_cell_truth=true`.
- Cell-specific runs now derive and write groups even when no group extra was explicitly requested, because `group:<group_id>` truth contexts require deterministic cell-to-group assignments.
- The existing `derive_group_truth()` path is reused for cell-specific runs, so `truth/networks.csv` now includes `global`, `group:<group_id>` and `cell:<cell_id>` contexts for `scrna_cell_specific`.
- The global truth source remains `model$feature_network`.
- The existing cell truth source remains native `dataset$regulatory_network_sc`.
- The `dyngen_cell_specific` smoke config now requires `global`, `group:` and `cell:` truth contexts and checks `provenance/raw/group_networks_index.tsv`.
- Focused smoke outcome: `python wrappers/simulation_data_tools/scripts/run_smoketests.py --configs-root /tmp/andrea_phase3_smoke_dyngen --simulator dyngen` passed after rebuilding the dyngen image.

## Scientific decisions

- Canonical profiles covered:
  - `scrna_global`
  - `scrna_grouped`
  - `scrna_cell_specific`
- `dyngen` does not define a bulk RNA-seq simulator, so it is not mapped to bulk profiles.
- Native global truth comes from the package-generated regulatory network.
- Public truth export filters simulator self-loops because ANDREA truth networks exclude self-regulatory edges unless a simulator-specific contract explicitly requires them.
- `groups.tsv` is derived from dyngen `milestone_percentages` by assigning each cell to the milestone with maximum percentage.
- Group truth rows in public `truth/networks.csv` are derived from dyngen `regulatory_network_sc` by aggregating cell-specific regulatory strengths within each exported group. Missing cell-edge values are treated as zero; active edges use `mean(abs(strength)) >= 0.1` and use `context=group:<group_id>`.
- Cell truth rows in public `truth/networks.csv` are normalized directly from dyngen `regulatory_network_sc`. The wrapper exports one row per nonzero non-self-loop cell/regulator/target strength with `context=cell:<cell_id>`, `score=abs(strength)` and `sign` derived from the strength sign. The group-level active-edge threshold is not applied.
- `lineage_tree.tsv` is derived from dyngen `milestone_network` and the public group truth active-edge sets for both grouped and cell-specific profiles. Root groups are emitted with `parent=__root__`, `gain_rate=0` and `loss_rate=0` so every group appears as a `child`.
- `tf_list.txt` is derived from `feature_info$is_tf`.
- `pseudotime.tsv` is derived from dyngen `milestone_percentages` and a deterministic order over `milestone_network`; branching/disconnected/cyclic topologies are projected to a single wrapper-defined order.
- `cell_phenotypes.tsv` reuses the hard milestone-derived group assignment as an ordered phenotype/state assignment.
- `cluster_identities.tsv` maps each exported group to its dyngen milestone identifier and the same deterministic milestone order.
- `enrichment_background.txt` is the complete set of generated expression genes.
- `prior_grn.tsv` is an oracle global prior from dyngen `feature_network`, with signed scores from `strength` and `effect`.
- `prior_grn_by_group.tsv` is an oracle group-specific prior from aggregated `regulatory_network_sc` for both grouped and cell-specific profiles; active edges use the same `mean(abs(strength)) >= 0.1` threshold as public group truth, and scores preserve mean signed strength where possible.
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
- `scrna_cell_specific` truth rows use `context=cell:<cell_id>` in the same table, generated from native `regulatory_network_sc`.
- The wrapper no longer writes the legacy split public truth files.
- `simulator-output-manifest.json` now reports `truth.gene_universe` and `truth.networks`.
- Group derivation debug files remain under `provenance/raw/`, including `group_edge_activity.tsv`, `group_active_networks.tsv` and `group_networks_index.tsv` when group truth is derived.
- Cell truth derivation writes `provenance/raw/cell_networks_index.tsv` when `scrna_cell_specific` is selected.

## Phase 6 SimulatorSpec Truth Contexts

- `profile_capabilities.scrna_cell_specific.truth_outputs.group` is now `derivable`, matching the Phase 3 wrapper behavior.
- Every dyngen profile now declares `truth_contexts` for `global`, `group` and `cell`.
- `scrna_global` documents native `global` truth from `model$feature_network` / `dataset$regulatory_network`; `group` and `cell` are explicitly `none`.
- `scrna_grouped` documents native `global` truth and ANDREA-derived `group:<group_id>` truth from `dataset$regulatory_network_sc` aggregated over wrapper-derived groups.
- `scrna_cell_specific` documents cumulative `global + group + cell` truth:
  - `global` remains native static dyngen feature-network truth.
  - `group` is derived from `dataset$regulatory_network_sc` using the same group aggregation rule as grouped runs.
  - `cell` is native dyngen `dataset$regulatory_network_sc` normalized to `context=cell:<cell_id>`.
- `python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py --simulator dyngen` passes with the new contract.

## Phase 11 Documentation Alignment

- The dyngen integration now follows the documented ordering required for future simulators:
  1. upstream source audit,
  2. wrapper behavior audit,
  3. wrapper correction,
  4. spec update from verified wrapper behavior,
  5. smoke proof.
- The decision log records, per profile, which upstream settings are activated and which public truth contexts are emitted.
- The spec is intentionally cumulative:
  - `scrna_global`: `global`.
  - `scrna_grouped`: `global` plus derived `group:<group_id>`.
  - `scrna_cell_specific`: `global` plus derived `group:<group_id>` plus native `cell:<cell_id>`.
- `truth_contexts` in the spec records the native upstream artifact or wrapper derivation rule, score/sign semantics and limitations for every `global`, `group` and `cell` entry.
- The cell-specific smoke config requires `global`, `group:` and `cell:` context prefixes, proving the cumulative contract.

## Failure Semantics Review

- The lineage-related inference failures traced back to dyngen outputs are classified as simulator standardized-output bugs, not dyngen scientific simulation failures.
- The affected `lineage_tree.tsv` files described only parent-child transitions and omitted root groups that were present in `groups.tsv`. Tools such as scMTNI need every exported group to appear as a `child` so the lineage file is a complete state table/tree representation.
- The wrapper now emits explicit root rows with `parent=__root__`, `gain_rate=0` and `loss_rate=0` for root groups.
- Shared generate-data output validation now checks `lineage_tree.tsv` coverage when lineage output is requested, so incomplete lineage extras fail during dataset generation instead of surfacing later as tool-specific inference errors.
