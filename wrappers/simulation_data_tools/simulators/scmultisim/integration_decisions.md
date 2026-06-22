# scMultiSim Integration Decisions

Status: migrated to the semantic simulator contract.

## Upstream Target

- Simulator id: `scmultisim`
- Public project: `https://github.com/ZhangLabGT/scMultiSim`
- Installation route: Bioconductor package `scMultiSim`
- Pinned runtime version: `1.8.0`
- Docker image: `adriansegura99/simulator_scmultisim:1.0.0`
- Wrapper entrypoint: public R API `scMultiSim::sim_true_counts()`, optionally followed by `scMultiSim::add_expr_noise()` and `scMultiSim::divide_batches()`
- Runtime does not depend on the local `repo/` checkout.

## Evidence Used

- Upstream source: `wrappers/simulation_data_tools/simulators/scmultisim/repo/scMultiSim/`
- Local Bioconductor docs: `wrappers/simulation_data_tools/simulators/scmultisim/scMultiSim.txt`
- Paper text: `wrappers/simulation_data_tools/simulators/scmultisim/papers/nihms-2090221.txt`
- Wrapper: `wrappers/simulation_data_tools/simulators/scmultisim/run_simulator.R`
- Catalog spec: `andrea/catalog_simulation_data_tools/simulators/scmultisim/simulatorspec.json`

## Claimed Semantic Capabilities

Claimed single-cell RNA capabilities use:

- `data_axes.measurement=rna_expression`
- `data_axes.resolution=single_cell`
- `data_axes.column_kind=cells`
- `data_axes.experimental_design=differentiation`

Claimed spatial RNA capabilities use:

- `data_axes.measurement=rna_expression`
- `data_axes.resolution=spatial`
- `data_axes.column_kind=spots`
- `data_axes.experimental_design=differentiation`

| Truth requirements | Public contexts emitted | Status |
| --- | --- | --- |
| `["global"]` | `global` | Supported for single-cell RNA and spatial RNA |
| `["global", "group"]` | `global`, `group:<id>` | Supported for single-cell RNA and spatial RNA when dynamic GRN is enabled |
| `["global", "group", "column"]` | `global`, `group:<id>`, `column:<id>` | Supported for single-cell RNA and spatial RNA when dynamic GRN is enabled |

Unclaimed capabilities:

- Bulk and pseudo-bulk axes are not claimed; aggregating generated cells into bulk samples would be an ANDREA-side reinterpretation.
- ATAC is claimed as standardized extras only (`chromatin_accessibility.tsv` and `chromatin_regions.tsv`), not as the primary `expression.tsv` measurement. Primary expression remains RNA.
- CCI is claimed as standardized spatial extra `cell_cell_interactions.tsv`, not as `truth/networks.csv`; GRN truth remains the only public network truth.
- Column truth without cumulative group truth is not claimed. ANDREA treats the `global+group+column` capability as cumulative.

## Truth Context Decisions

- `global` is derivable. With static GRN, the wrapper normalizes nonzero `.grn$geff` entries. With dynamic GRN, the wrapper aggregates `results$cell_specific_grn` across all expression columns.
- `group` is derivable. The wrapper derives `groups.tsv` from `cell_meta$pop`, aggregates `results$cell_specific_grn` within each group, and writes one `context=group:<id>` network per observed group.
- `column` is native upstream state normalized by ANDREA. scMultiSim returns `results$cell_specific_grn` when `dynamic.GRN` is enabled; the wrapper maps each matrix to `context=column:<expression_column_id>`.

Required upstream switches:

- `dynamic_grn.enabled=true` for `group` truth, `column` truth, `lineage_tree` and `prior_grn_by_group`.
- `tree_preset` and `population_mode` determine exported group labels.
- `velocity.enabled=true` is optional and only needed when velocity-derived native outputs or upstream `cell_time` are requested.

Score/sign semantics:

- Static `global`: `score=abs(effect)`, `sign` from effect.
- Dynamic `global` and `group`: `score=mean(abs(effect))`, `sign` from mean signed effect, with ambiguous signs represented as `?`.
- `column`: `score=abs(effect)`, `sign` from the per-column GRN matrix.
- Self-loops, zero effects and missing scores are excluded from public truth.

## Extras And Native Outputs

Derivable extras:

- `groups`: public group labels from `cell_meta$pop`.
- `spatial_coordinates`: public spot coordinates from `cci_locs`; emitted for spatial capabilities with `column_kind=spots`.
- `chromatin_accessibility`: public region-by-column ATAC matrix from `atac_counts` or `atacseq_data`.
- `chromatin_regions`: public region metadata from `region_to_gene`, with stable `region_<index>` identifiers when upstream row names are absent.
- `cell_cell_interactions`: public directed LR interactions from `cci_gt[target, source, lr_pair]` when spatial `single.cell.gt=TRUE`.
- `column_phenotypes`: group label plus order from pseudotime/tree depth.
- `cluster_identities`: one row per public group.
- `lineage_tree`: selected tree plus group active-edge summaries; root groups are emitted with `parent=__root__`.
- `pseudotime`: prefer `cell_time`, then `cell_meta$depth`, then deterministic tree-depth fallback.
- `prior_grn`: oracle global prior from static or aggregated GRN state.
- `prior_grn_by_group`: oracle group prior from group-aggregated `cell_specific_grn`.
- `tf_list`: upstream regulators.
- `enrichment_background`: all exported expression genes.

Native outputs are preserved only when requested; examples include true counts, observed counts, ATAC counts, cell metadata, velocity and `cell_specific_grn`. `atac_counts` remains available as native output even though the standardized ATAC path writes the public `chromatin_accessibility.tsv` contract.

## Inputs, Params And Resources

- Required simulator inputs: none for built-in presets.
- Conditional inputs:
  - `regulatory_network` when `grn_source=input_tsv`.
  - `tree_newick` when `tree_preset=input_newick`.
- Runtime threading is supported. ANDREA maps assigned `runtime_resources.threads` to `scMultiSim::sim_true_counts(threads=threads)`.
- Thread controls are not exposed as simulator parameters.
- Function-valued upstream hooks are represented as presets; arbitrary R functions, density objects and spatial/CCI callbacks are not accepted through JSON.
- `parameter_bindings` lock `spatial.enabled=false` for single-cell capabilities and `spatial.enabled=true`, `spatial.single_cell_gt=true` for spatial capabilities. Spatial capabilities also default `num_cifs=20` and `speed_up=true`.
- The public spatial CCI preset uses fixed serializable ligand-receptor pairs (`target` genes 101/102, `regulator` genes 103/104) with configurable signed effects. This avoids exposing arbitrary R callback/layout objects while still executing the upstream `cci` API.
- Cost-profile source entries include spatial, ATAC and CCI paths. Existing `cost.json` measured points were not fabricated for the newly claimed profiles; they should be added only after running real benchmarks for those profiles.

## Normalized Output Contract

- `expression.tsv`: genes in rows, expression columns in columns; single-cell capabilities use public `cell` columns, spatial capabilities normalize columns to `spot_<n>`.
- `truth/networks.csv`: one table with `source,target,score,sign,evidence,context`.
- `truth/gene_universe.txt`: exact public expression gene universe.
- `extras/`: requested standardized extras plus dependencies needed to satisfy public contracts. Spatial/CCI outputs preserve public ID consistency across expression columns, `groups.tsv`, `spatial_coordinates.tsv`, `chromatin_accessibility.tsv` and `cell_cell_interactions.tsv`.
- `provenance/raw/`: request snapshot, resolved options, result RDS, raw counts/metadata, session info, group derivation tables and `column_networks_index.tsv` for column truth.

The smoke matrix covers single-cell and spatial global, group and cumulative column truth, ATAC standardized extras and spatial CCI standardized extras, including public ID consistency across expression, truth, gene universe and generated extras.
