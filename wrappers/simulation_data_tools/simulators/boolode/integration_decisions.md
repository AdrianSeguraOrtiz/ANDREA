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

- `global` is derivable. Native `refNetwork.csv` supplies topology; the wrapper derives signs from the original Boolean rule (see scientific audit below).
- `group` is derivable. BoolODE can cluster full simulated trajectories and writes `ClusterIds.csv`; the wrapper maps expression columns to trajectory clusters and duplicates the fixed global GRN into one `context=group:<id>` network per observed group.
- `column` is unavailable for the public contract.

Required upstream switches:

- `global_settings.do_simulations=true`.
- `global_settings.modeltype=hill|heaviside`.
- `jobs[].model_definition` from a bundled model or `boolode_boolean_model`.
- `jobs[].nClusters >= 2` and `jobs[].sample_cells=false` when `group` truth or group-derived extras are requested.

Score/sign semantics:

- `score=1.0` for every non-self-loop native `refNetwork.csv` edge.
- `sign` is derived from Boolean literal polarity; ambiguous mixed polarity is `?`. The native `Type` heuristic is preserved only in raw output.
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

### Pinned upstream sampling constraints

The time grid has `int(simulation_time / integration_step_size)` points. Full
trajectories omit the initial time point, so the actual output width is
`num_cells * (time_points - 1)`, or `num_cells` with `sample_cells=true`.
`BoolODE.utils.generateInputFiles` enters its resampling branch at 1000 columns.
That branch draws indices from `range(1, simulation_time * 100)` independently
of the chosen integration step; it requires integer `simulation_time`, full
trajectories (`sample_cells=false`) and enough time points to cover that range.
ANDREA checks these constraints during parameter resolution, before execution
through core, CLI or GUI; the wrapper validates them again for direct requests.
The default step `0.01` is recommended. Smaller steps remain supported but keep
upstream's fixed index range, so they sample an earlier part of each trajectory.
Coarser steps remain valid when the output stays below 1000 columns.

ANDREA starts BoolODE containers with `PYTHONHASHSEED` equal to the run seed and
records that environment in `provenance/raw/docker_wrapper.environment.json`.
The pinned model generator builds terms from sets; fixing NumPy's seed alone
does not fix term ordering between Python processes. This environment setting
is specific to BoolODE and leaves the other simulator containers unchanged.

Upstream stdout/stderr are streamed to `provenance/raw/upstream_*.log`, including
failed executions. The wrapper emits the exception type and message on stderr,
saves its traceback in `wrapper_error.log`, and marks `progress.json` as failed.

## Normalized Output Contract

- `expression.tsv`: public gene IDs in rows and public expression-column IDs in columns, from BoolODE `ExpressionData.csv` after optional dropout.
- `truth/networks.csv`: one table with `source,target,score,sign,evidence,context`.
- `truth/gene_universe.txt`: exact public expression gene universe.
- `extras/`: requested standardized extras only.
- `native/`: selected native BoolODE files/directories copied from raw upstream outputs and listed in `simulator-output-manifest.json`.
- `provenance/raw/`: request snapshot, resolved YAML/config, copied model/input files, public ID maps, raw BoolODE outputs and runtime package information.

The smoke matrix covers global and group truth, custom dropout, built-in models, the large-output sampling branch and public ID consistency.

## Scientific audit, 2026-09-29

Sources: [BEELINE/BoolODE paper](https://www.nature.com/articles/s41592-019-0690-6),
[pinned utils.py](https://github.com/Murali-group/BoolODE/blob/ba8884af40f98fc648b3f36f0b81a5a8cf22c9b9/BoolODE/utils.py),
[pinned simulation driver](https://github.com/Murali-group/BoolODE/blob/ba8884af40f98fc648b3f36f0b81a5a8cf22c9b9/BoolODE/run_experiment.py), and
[pinned SDE solver](https://github.com/Murali-group/BoolODE/blob/ba8884af40f98fc648b3f36f0b81a5a8cf22c9b9/BoolODE/simulator.py).
The installed pinned source was inspected from the existing image; no local
`repo/` checkout is required at runtime.

The native `generateInputFiles` sign exporter considers every regulator after
the first `not` token inhibitory. For `not A and B`, this incorrectly labels B
as inhibitory. ANDREA now retains native directed topology but derives polarity
from the original Boolean rule with proper nested negation. AND/OR retain
polarity and NOT reverses it. Mixed-polarity occurrences or syntax outside that
conservative grammar have sign `?`. Native `refNetwork.csv` stays unmodified in
provenance. Global truth is therefore declared **derivable**, as is the fixed
network repeated across trajectory groups. Priors use the corrected public
truth. No kinetic magnitudes or Boolean truth-table simplification are claimed.

The wrapper seeds NumPy and core pins PYTHONHASHSEED, but upstream assigns each
trajectory `seed=cellid` and resets NumPy in `deltaW`. Different run seeds do not
prove independent stochastic trajectories when inputs/parameters are unchanged.
This is recorded as a limitation rather than silently replacing upstream RNG.
Pseudotime comes from simulation time; trajectory clusters are not distinct
regulatory graphs. Sampling, dropout, custom inputs and serial resource mapping
retain the previously documented limits. Unit regressions cover mixed/nested
negation and preserve original native output.

Custom model `Gene`/`Rule` values may not be empty or pandas missing-value tokens
such as `NA`, `NaN` or `NULL`. The pinned upstream `GenerateModel` reader applies
pandas NA inference before generating its model. ANDREA now rejects these values
with a clear error during input validation; it does not claim identifier-alias
support that the simulator does not implement.

The clean build found that live bullseye-security indexes referenced unavailable
packages. OS dependencies now use the signed Debian snapshot dated
`20260601T000000Z`; historical metadata expiration is disabled for that snapshot,
while package signatures remain checked. Python 3.7 and the pinned upstream
scientific requirements are unchanged.

### Executed validation for the audited wrapper

On 2026-09-29 the repository Dockerfile built
`adriansegura99/simulator_boolode:1.1.0` successfully (local image
`sha256:5961de0161af7790d9b3873c39e2a05f10dca823c97a8b906bbe847de6d1d263`). The wrapper SHA-256 inside
that image matched the current repository file. All **3/3** simulator smoke
configurations passed on this final image, using the repository smoke runner
with at most 2 CPU threads, 8 GiB RAM and a 300-second timeout per fixture.
These checks cover executable contracts and fixture outputs; they do not
calibrate costs or independently validate biological realism.
The six BoolODE unit regressions also passed inside this final Python 3.7 image.
