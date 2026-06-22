# BoolODE Integration Decisions

Status: migrated to the semantic simulator contract.

## Upstream Target

- Simulator id: `boolode`
- Public project: `https://github.com/Murali-group/BoolODE`
- Installation route: pinned public GitHub commit
- Pinned commit: `ba8884af40f98fc648b3f36f0b81a5a8cf22c9b9`
- Docker image: `adriansegura99/simulator_boolode:1.0.0`
- Wrapper entrypoint: public BoolODE API through `BoolODE.ConfigParser.parse()` and `BoolODE.execute_jobs()`, equivalent to `python boolode.py --config <yaml>`.
- Runtime does not depend on the local `repo/` checkout.

## Evidence Used

- Upstream source: `wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/`
- Upstream README and config examples under the pinned repo snapshot.
- Paper text: `wrappers/simulation_data_tools/simulators/boolode/papers/nihms-1544277.txt`
- Wrapper: `wrappers/simulation_data_tools/simulators/boolode/run_simulator.py`
- Catalog spec: `andrea/catalog_simulation_data_tools/simulators/boolode/simulatorspec.json`

## Claimed Semantic Capabilities

All claimed capabilities use:

- `data_axes.measurement=rna_expression`
- `data_axes.resolution=single_cell`
- `data_axes.column_kind=cells`
- `data_axes.experimental_design=trajectory`

| Truth requirements | Public contexts emitted | Status |
| --- | --- | --- |
| `["global"]` | `global` | Supported |
| `["global", "group"]` | `global`, `group:<id>` | Supported |

Unclaimed capabilities:

- `column` truth is not claimed. BoolODE uses a fixed Boolean-model GRN and does not emit per-expression-column GRNs; duplicating the fixed network into `column:<id>` contexts would misrepresent the contract.
- Bulk, pseudo-bulk, spatial, perturbational and time-series axes were re-reviewed during the expanded semantic-contract migration and are not claimed. BoolODE simulates trajectories internally, but the public wrapper emits sampled single-cell columns with fixed regulatory truth; treating trajectory timepoints as a time-series benchmark would require a new normalized sampling contract and would still not provide time-varying regulatory truth.
- `lineage_tree` is not claimed because the native output gives trajectory clusters, not a stable parent-child graph between public groups.

## Truth Context Decisions

- `global` is native. BoolODE writes `refNetwork.csv` from the Boolean model used to generate the ODE system; the wrapper normalizes it to public truth rows.
- `group` is derivable. BoolODE can cluster full simulated trajectories and writes `ClusterIds.csv`; the wrapper maps expression columns to trajectory clusters and duplicates the fixed global GRN into one `context=group:<id>` network per observed group.
- `column` is unavailable for the public contract.

Required upstream switches:

- `global_settings.do_simulations=true`.
- `global_settings.modeltype=hill|heaviside`.
- `jobs[].model_definition` from a bundled model or `boolode_boolean_model`.
- `jobs[].nClusters >= 2` and `jobs[].sample_cells=false` when `group` truth or group-derived extras are requested.

Score/sign semantics:

- `score=1.0` for every non-self-loop native `refNetwork.csv` edge.
- `sign` is BoolODE `Type`, where `+` is activation and `-` is repression.
- Complex Boolean logic and ODE thresholds are not emitted as public truth scores.

## Extras

Native extras:

- `pseudotime`: normalized from BoolODE `PseudoTime.csv`.

Derivable extras:

- `groups`: expression-column-to-cluster assignment from `ClusterIds.csv`.
- `column_phenotypes`: group label plus order by mean native simulation time.
- `cluster_identities`: one row per public group.
- `prior_grn`: oracle prior from native `refNetwork.csv`.
- `prior_grn_by_group`: fixed GRN duplicated per group.
- `tf_list`: regulators from native `refNetwork.csv`.
- `enrichment_background`: all exported expression genes.

Not claimed as extras:

- `lineage_tree`, dimensionality reductions, plots, Slingshot outputs and column-level truth.

## Inputs, Params And Resources

- Required simulator inputs: none for bundled presets.
- Optional inputs:
  - `boolode_initial_conditions`
  - `boolode_interaction_strengths`
- Conditional input:
  - `boolode_boolean_model` when `model_preset=custom_files`.
- Runtime threading is unsupported. The wrapper rejects `runtime_resources.threads != 1`, sets BoolODE `do_parallel=false`, and patches the imported public BoolODE KMeans constructor to avoid the pinned upstream hard-coded `n_jobs=8`.
- Thread controls are not exposed as simulator parameters.
- No function-valued callbacks are exposed by the reviewed public BoolODE API.

## Normalized Output Contract

- `expression.tsv`: public gene IDs in rows and public expression-column IDs in columns, from BoolODE `ExpressionData.csv` after optional dropout.
- `truth/networks.csv`: one table with `source,target,score,sign,evidence,context`.
- `truth/gene_universe.txt`: exact public expression gene universe.
- `extras/`: requested standardized extras only.
- `native/`: selected native BoolODE files/directories copied from raw upstream outputs and listed in `simulator-output-manifest.json`.
- `provenance/raw/`: request snapshot, resolved YAML/config, copied model/input files, public ID maps, raw BoolODE outputs and runtime package information.

The smoke matrix covers global and group truth, custom dropout, built-in models, the large-output sampling branch and public ID consistency.
