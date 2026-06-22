# dyngen Integration Decisions

Status: migrated to the semantic simulator contract.

## Upstream Target

- Simulator id: `dyngen`
- Public project: `https://github.com/dynverse/dyngen`
- Installation route: CRAN package `dyngen`
- Pinned package version: `1.1.1`
- Docker image: `adriansegura99/simulator_dyngen:1.0.0`
- Wrapper entrypoint: public R API `dyngen::initialise_model()` followed by `dyngen::generate_dataset(format="list")`
- Runtime does not depend on the local `repo/` checkout.

## Evidence Used

- Upstream source: `wrappers/simulation_data_tools/simulators/dyngen/repo/dyngen/`
- Paper text: `wrappers/simulation_data_tools/simulators/dyngen/papers/41467_2021_Article_24152.txt`
- Wrapper: `wrappers/simulation_data_tools/simulators/dyngen/run_simulator.R`
- Catalog spec: `andrea/catalog_simulation_data_tools/simulators/dyngen/simulatorspec.json`

## Claimed Semantic Capabilities

All claimed capabilities use:

- `data_axes.measurement=rna_expression`
- `data_axes.resolution=single_cell`
- `data_axes.column_kind=cells`

| Experimental design | Truth requirements | Public contexts emitted | Required extras |
| --- | --- | --- | --- |
| `trajectory` | `["global"]` | `global` | none |
| `trajectory` | `["global", "group"]` | `global`, `group:<id>` | `groups` |
| `trajectory` | `["global", "group", "column"]` | `global`, `group:<id>`, `column:<id>` | `groups` |
| `time_series` | `["global"]` | `global` | `timepoints` |
| `time_series` | `["global", "group"]` | `global`, `group:<id>` | `groups`, `timepoints` |
| `time_series` | `["global", "group", "column"]` | `global`, `group:<id>`, `column:<id>` | `groups`, `timepoints` |
| `perturbational` | `["global"]` | `global` | `perturbation_design`, `interventions` |
| `perturbational` | `["global", "group"]` | `global`, `group:<id>` | `groups`, `perturbation_design`, `interventions` |
| `perturbational` | `["global", "group", "column"]` | `global`, `group:<id>`, `column:<id>` | `groups`, `perturbation_design`, `interventions` |

Unclaimed capabilities:

- Bulk, pseudo-bulk and spatial axes are not claimed because the wrapper emits single-cell RNA expression columns only.
- Column truth without cumulative group truth is not claimed. ANDREA treats the `global+group+column` capability as cumulative.
- Custom backbone/input-file simulation is not exposed yet; only the serializable parameter surface implemented by the wrapper is claimed.

## Truth Context Decisions

- `global` is native. It comes from `model$feature_network` / `dataset$regulatory_network`, which is the simulator regulatory graph used to generate the dataset.
- `group` is derivable. The wrapper derives `groups.tsv` from `milestone_percentages`, then aggregates `dataset$regulatory_network_sc` within each exported group. Active group edges require `mean(abs(strength)) >= 0.1`; `score` is mean absolute strength and `sign` comes from the mean signed strength.
- `column` is native upstream state normalized by ANDREA. dyngen emits `dataset$regulatory_network_sc` when cellwise GRN computation/storage is enabled; the wrapper writes one public `context=column:<expression_column_id>` network per expression column.

Required upstream switches:

- `trajectory` capabilities bind `experiment_params.kind=snapshot` and `simulation_params.num_knockdown_simulations=0`.
- `time_series` capabilities bind `experiment_params.kind=synchronised` and `simulation_params.num_knockdown_simulations=0`; the wrapper exports `extras/timepoints.tsv` from `dataset$cell_info$timepoint_group`.
- `perturbational` capabilities bind `experiment_params.kind=snapshot`, `simulation_params.num_knockdown_simulations=4`, `knockdown_params.num_genes=1`, `knockdown_params.multiplier=0.0` and `knockdown_params.timepoint=0.5`; the wrapper exports `extras/perturbation_design.tsv` and `extras/interventions.tsv` from `dataset$cell_info$simulation_i` and `model$simulations$kd_multiplier`.
- `simulation_params.compute_cellwise_grn=true` whenever `group` or `column` truth, `lineage_tree`, `prior_grn_by_group` or `regulatory_network_sc` is requested.
- `generate_dataset(..., store_cellwise_grn=TRUE)` under the same conditions.
- `store_rna_velocity=TRUE` only when the native output `rna_velocity` is requested.

Score/sign semantics:

- `global`: `score=abs(strength)`, `sign` from `effect`.
- `group`: `score=mean(abs(strength))`, `sign` from mean signed strength.
- `column`: `score=abs(strength)`, `sign` from per-column signed strength.
- Self-loops and zero-strength edges are excluded from public truth.

## Extras And Native Outputs

Derivable extras:

- `groups`: hard milestone assignment from maximum `milestone_percentages`.
- `column_phenotypes`: group label plus deterministic milestone order.
- `cluster_identities`: one row per public group.
- `lineage_tree`: derived from `milestone_network` plus group active-edge summaries; root groups are emitted with `parent=__root__`.
- `pseudotime`: deterministic projection of `milestone_percentages` onto an ordered trajectory scale.
- `prior_grn`: oracle global prior from `feature_network`.
- `prior_grn_by_group`: oracle group prior from aggregated cellwise regulatory activity.
- `tf_list`: `feature_info$is_tf`.
- `enrichment_background`: all exported expression genes.
- `timepoints`: synchronised experiment `timepoint_group` per expression column.
- `perturbation_design`: one row per expression column mapping wild-type controls and one-target knockdown simulations to condition metadata.
- `interventions`: one row per dyngen knockdown intervention target.

Native outputs are preserved only when requested; currently this includes `rna_velocity` and `regulatory_network_sc`.

## Inputs, Params And Resources

- No simulator input file is required for the current claimed contract.
- Runtime threading is supported. ANDREA maps assigned `runtime_resources.threads` to `options(Ncpus=threads)` and `dyngen::initialise_model(num_cores=threads)`.
- Thread controls are not exposed as simulator parameters.
- Design-changing controls are bound at capability level with `parameter_bindings`: users cannot turn a trajectory run into a synchronised time-series or perturbational run by editing params.
- Function-valued dyngen hooks are intentionally represented as presets or omitted; arbitrary R callbacks are not accepted through JSON.
- The 12 trajectory, time-series and perturbational cost-profile entries have measured `cost.json` points from the wrapper benchmark runner (`20x10`, `threads=1`, `ram_gb=8`, one repeat per profile).

## Normalized Output Contract

- `expression.tsv`: genes in rows, expression columns in columns.
- `truth/networks.csv`: one table with `source,target,score,sign,evidence,context`.
- `truth/gene_universe.txt`: exact public expression gene universe.
- `extras/`: requested standardized extras only.
- `provenance/raw/`: request snapshot, parameter snapshots, `model.rds`, `dataset.rds`, session info, public ID maps, group derivation tables and `column_networks_index.tsv` for column truth.

The smoke matrix covers trajectory, time-series and perturbational runs across global, group and cumulative column truth, including public ID consistency across expression, truth, gene universe and extras.
