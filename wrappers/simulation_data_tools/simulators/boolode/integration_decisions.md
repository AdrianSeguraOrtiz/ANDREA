# BoolODE Integration Decisions

Phase: 3 verified end-to-end.

## Upstream Target

- Simulator id: `boolode`
- Canonical project name: `BoolODE`
- Public implementation URL: `https://github.com/Murali-group/BoolODE`
- Public installation route: clone the public GitHub repository at a pinned commit; no PyPI package is declared by the reviewed README.
- Pinned runtime commit: `ba8884af40f98fc648b3f36f0b81a5a8cf22c9b9`
- Upstream tag present locally: `v0.1`
- Docker image: `adriansegura99/simulator_boolode:1.0.0`
- Public entrypoint used by the wrapper: BoolODE YAML parsing through `BoolODE.ConfigParser.parse()` followed by `BoolODE.execute_jobs()`, matching `python boolode.py --config <yaml>`.
- Runtime does not depend on `wrappers/simulation_data_tools/simulators/boolode/repo/`; the Dockerfile installs from `https://github.com/Murali-group/BoolODE.git` pinned to the commit above.

The local clone HEAD is `ba8884af40f98fc648b3f36f0b81a5a8cf22c9b9`. The upstream README says to use the `v0.1` release for the BEELINE publication code; the user requested freezing the latest available commit.

## Phase 3 Verification

- `make validate-simulatorspecs ARGS="--simulator boolode"`: passed.
- `make validate-simulator-smoketest-configs ARGS="--simulator boolode"`: passed.
- `make run-simulator-smoketests ARGS="--simulator boolode --skip-build"`: passed.
- `.venv/bin/python -m pytest tests/wrappers/simulation_data_tools/test_simulatorspecs.py tests/wrappers/simulation_data_tools/test_input_specs.py tests/core/commands/generate_data/test_generate_data.py tests/cli/test_generate_data_cli.py -q`: 77 passed.
- `.venv/bin/python -m pytest tests/gui/test_generate_data_server.py -q`: 3 passed.
- Generated temporary `scrna_global` and `scrna_grouped` benchmark packages with BoolODE under `/tmp/andrea_boolode_phase3/`; each generated `dataset-manifest.json` passed `infer-network` preflight via `preflight_infer_network()` with no dataset issues.
- GUI follow-up on `benchmarks/gui_generate_benchmark_20260618T120323Z`: failure was traced to pinned upstream BoolODE using `range(1, tmax*100)` when the internal full trajectory matrix has at least 1000 columns. The wrapper now serializes integral `simulation_time` values as YAML integers and rejects non-integral values when that large-output branch would be reached. Added `boolode_grouped_large_sampling_branch` to cover this path.

## Evidence Used

- Upstream README: [README.md](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/README.md)
- CLI entrypoint: [boolode.py](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/boolode.py)
- Config parsing and job defaults: [BoolODE/__init__.py](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/BoolODE/__init__.py)
- Simulation and trajectory clustering: [run_experiment.py](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/BoolODE/run_experiment.py)
- Reference network and pseudotime generation: [utils.py](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/BoolODE/utils.py)
- Boolean-to-ODE conversion and parameter surface: [model_generator.py](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/BoolODE/model_generator.py)
- Post-processing: [post_processing.py](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/BoolODE/post_processing.py)
- Config examples: [example-config.yaml](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/config-files/example-config.yaml), [beeline-inputs-synthetic.yaml](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/config-files/beeline-inputs-synthetic.yaml), [beeline-inputs-boolean.yaml](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/config-files/beeline-inputs-boolean.yaml)
- Bundled model files: [data/](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/repo/BoolODE/data)
- Primary paper text: [nihms-1544277.txt](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/boolode/papers/nihms-1544277.txt)
- Current simulator schema: [simulatorspec.schema.json](/home/adrian/Grecia/separation/ANDREA/andrea/catalog_simulation_data_tools/schemas/simulatorspec.schema.json)

## Scientific Contract

Claimed canonical profiles:

- `scrna_global`
  - BoolODE simulates single-cell expression snapshots from a Boolean GRN converted to stochastic ODEs.
  - Public global truth comes from native `refNetwork.csv`, which BoolODE derives from the same Boolean rules used to generate the ODE model.
  - Native `PseudoTime.csv` supports the `pseudotime` extra; BoolODE explicitly uses simulation time as pseudotime.
- `scrna_grouped`
  - BoolODE can cluster full simulated trajectories when `nClusters > 1` and writes `ClusterIds.csv`.
  - Public `groups.tsv` is derived by joining each expression cell's native `E<experiment>_<time>` identifier to the trajectory cluster for `E<experiment>`.
  - Public group truth duplicates the fixed global `refNetwork.csv` edge set into one `context=group:<group_id>` network per observed group. This documents the fixed-GRN design; it is not a claim of group-specific rewiring.

Not claimed:

- `scrna_cell_specific`: no native cell-specific GRN object or file exists. Duplicating the fixed GRN for every cell would satisfy file shape but would misrepresent the profile.
- Bulk profiles: BoolODE defines a single cell as one time point from one simulated trajectory; treating trajectories as bulk time series would be an ANDREA-side reinterpretation.
- Perturbational profile: fixed parameter inputs can model externally held nodes, but the reviewed upstream does not expose a canonical perturbation experiment contract with perturbation labels and matched truth contexts.
- `lineage_tree`: the paper describes biological and synthetic trajectory families, and Slingshot can infer lineages, but the native BoolODE output used here provides trajectory clusters rather than a stable parent-child graph between public groups.

## Truth-Context Audit

`scrna_global`:

| Context | Status | Evidence | Wrapper rule | Score/sign | Limitations |
| --- | --- | --- | --- | --- | --- |
| `global` | `native` | README documents `refNetwork.csv`; `utils.generateInputFiles()` writes it from the Boolean model. | Normalize non-self-loop `refNetwork.csv` rows to `truth/networks.csv` with `context=global`, `source=Gene1`, `target=Gene2`. | `score=1.0`; `sign=Type` where `+` is activation and `-` is repression. | Complex Boolean logic is reduced by upstream to pairwise signs; interaction strengths affect ODE thresholds but are not emitted as truth scores. |
| `group` | `none` | Global profile has no required groups. | No rows. | Not applicable. | None. |
| `cell` | `none` | No cell-specific GRN artifact in README or source. | No rows. | Not applicable. | Fixed GRN across all simulated cells. |

`scrna_grouped`:

| Context | Status | Evidence | Wrapper rule | Score/sign | Limitations |
| --- | --- | --- | --- | --- | --- |
| `global` | `native` | Same native `refNetwork.csv`. | Normalize global rows exactly as in `scrna_global`. | `score=1.0`; `sign=Type`. | Global truth is the fixed Boolean-model topology. |
| `group` | `derivable` | `run_experiment.py` writes `ClusterIds.csv` when `nClusters > 1`; the paper describes assigning sampled cells to the cluster of their full simulation. | Build `groups.tsv` from `ClusterIds.csv`, then duplicate global truth into `context=group:<group_id>` for every observed group. | Same as global. | Group networks are identical and wrapper-derived; cluster labels are k-means labels, not native group-specific GRNs. |
| `cell` | `none` | No cell-specific GRN artifact in README or source. | No rows. | Not applicable. | Cell-level regulatory truth is intentionally unclaimed. |

Required upstream switches:

- `global_settings.do_simulations: True`
- `global_settings.modeltype: hill|heaviside`
- `jobs[].model_definition`: bundled or mounted BoolODE Boolean model.
- `jobs[].model_initial_conditions`: optional bundled or mounted initial conditions.
- `jobs[].interaction_strengths`: optional ODE-threshold modifier.
- `jobs[].nClusters >= 2` and `jobs[].sample_cells: False` for `scrna_grouped` group truth.

## Extra Outputs

Claimed for `scrna_global`:

- `pseudotime`: native from `PseudoTime.csv`, normalized to `extras/pseudotime.tsv`.
- `enrichment_background`: derived from expression genes.
- `prior_grn`: oracle prior from native `refNetwork.csv`.
- `tf_list`: derived from `refNetwork.csv` regulators (`Gene1`).

Claimed for `scrna_grouped`:

- `pseudotime`: native from `PseudoTime.csv`.
- `groups`: derived from `ClusterIds.csv` and expression cell IDs.
- `cell_phenotypes`: derived from groups; phenotype order is by mean native simulation time per group.
- `cluster_identities`: derived from groups.
- `enrichment_background`: derived from expression genes.
- `prior_grn`: oracle prior from native `refNetwork.csv`.
- `tf_list`: derived from `refNetwork.csv` regulators.
- `prior_grn_by_group`: fixed global prior duplicated per group.

Not claimed as extras:

- `lineage_tree`: no native group hierarchy is emitted. Slingshot-based lineage inference is an additional downstream procedure, not part of the core BoolODE run contract.
- `cell-specific truth`: unavailable upstream.
- Dimensionality reductions, plots and Slingshot outputs: useful provenance/debug artifacts but not normalized ANDREA extras for this integration.

## Simulator-Side Inputs

Existing input specs were not reused:

- `regulatory_network` is an edge-list TSV; BoolODE requires a two-column Boolean-rule table (`Gene`, `Rule`).
- `tree_newick` is a differentiation tree; BoolODE does not consume Newick trees.

New shared input specs added:

- [boolode_boolean_model.json](/home/adrian/Grecia/separation/ANDREA/andrea/catalog_simulation_data_tools/input_specs/boolode_boolean_model.json)
- [boolode_initial_conditions.json](/home/adrian/Grecia/separation/ANDREA/andrea/catalog_simulation_data_tools/input_specs/boolode_initial_conditions.json)
- [boolode_interaction_strengths.json](/home/adrian/Grecia/separation/ANDREA/andrea/catalog_simulation_data_tools/input_specs/boolode_interaction_strengths.json)

Input requirements in the spec:

- Required: none, because bundled presets can run from files in the pinned public BoolODE repository.
- Optional:
  - `boolode_initial_conditions`
  - `boolode_interaction_strengths`
- Conditional:
  - `boolode_boolean_model` when `model_preset=custom_files`.

Wrapper validation checks that optional custom files reference genes present in the Boolean model.

## Parameter Surface

Exposed JSON-serializable upstream controls:

- `model_preset`: bundled model selection or `custom_files`.
- `model_type`: BoolODE `hill` or `heaviside`.
- `simulation_time`, `num_cells`, `n_clusters`, `sample_cells`.
- `sample_parameters`, `sample_std`, `identical_parameters`.
- `integration_step_size`.
- `dropout.enabled`, `dropout.drop_cutoff`, `dropout.drop_prob`.

Not exposed, with rationale:

- `do_parallel`: runtime resource control; not a simulator parameter.
- `do_simulations`, `do_post_processing`, `model_dir`, `output_dir`, job `name`: wrapper-owned execution plumbing.
- `write_protein`: would add protein rows that are not represented in `refNetwork.csv` gene truth and would break public gene-universe consistency.
- `normalize_trajectory`: accepted by the config parser but not used in the reviewed simulation path.
- `species_type`: documented in README but loaded into `speciesTypeDF` and not passed into `GenerateModel` in the pinned implementation.
- `parameter_inputs_path`: documented for fixed input nodes, but the pinned implementation checks membership against a pandas Series in a way that does not reliably detect the `Input` values; exposing it would risk leaking input nodes as expression/truth genes.
- `parameter_set`: the code path appears broken at the pinned commit (`pvalue` is referenced but not defined).
- `add_dummy` and `max_parents`: marked experimental and the implementation references undefined variables in the reviewed code.
- `GenSamples.nDatasets`: ANDREA run contract emits one normalized dataset per simulator run.
- Slingshot and t-SNE settings: depend on extra R/Docker tooling and are not required because BoolODE already emits simulation-time pseudotime.

No function-valued callbacks were found in the public BoolODE API. Therefore no callback presets are represented in the spec.

Wrapper type normalization:

- `simulation_time` remains a public numeric parameter. Integral values are written to BoolODE YAML as integers because the pinned upstream implementation uses `range(1, tmax*100)` in the large-output sampling branch. Non-integral values are accepted for small full outputs, but are rejected with a clear error when `num_cells * int(simulation_time / integration_step_size) >= 1000` and `sample_cells=false`.

## Runtime Resources

- Upstream has a `do_parallel` boolean, but no safe worker-count setting in YAML.
- `BoolODE.execute_jobs(parallel=False, num_threads=1)` accepts a `num_threads` argument but does not pass it to the simulation worker pool.
- `run_experiment.Experiment()` calls `multiprocessing.Pool()` without a `processes` argument when `doParallel` is true.
- K-means clustering is hard-coded with `n_jobs=8` in the pinned source.

Contract:

- `runtime_resources.threading.supported=false`
- `default_threads=1`
- `max_threads=1`
- The wrapper sets BoolODE `do_parallel=false`, fails fast if `runtime_resources.threads != 1`, and patches the imported public BoolODE KMeans constructor to force `n_jobs=1` before calling the public API.

This avoids uncontrolled oversubscription while still executing the public BoolODE API rather than reimplementing simulator logic. A future integration could revisit multi-thread support only if the upstream interface exposes a bounded worker-count control.

## Output Normalization Contract

`expression.tsv`:

- Source is BoolODE `ExpressionData.csv`, or dropout-processed `ExpressionData.csv` when `dropout.enabled=true`.
- Rows are public gene IDs preserved from BoolODE where possible.
- Columns are public cell IDs derived from BoolODE cell column names such as `E0_10`.
- The wrapper must preserve or record any escaping in `provenance/raw/public_id_maps`.

`truth/networks.csv`:

- Required columns: `source,target,score,sign,evidence,context`.
- `source` and `target` use the same public gene IDs as expression rows and `truth/gene_universe.txt`.
- `global`: one row per non-self-loop native `refNetwork.csv` edge.
- `group:<group_id>`: for `scrna_grouped`, duplicate the fixed global edge set once per group.
- `score=1.0`, `sign=Type`, `evidence=boolode_refNetwork`.

`truth/gene_universe.txt`:

- Every public expression gene, one per line.
- Must exactly match expression rows.

`extras/`:

- `pseudotime.tsv`: cell-to-pseudotime from `PseudoTime.csv`, subset to expression columns.
- `groups.tsv`: cell-to-cluster label for `scrna_grouped`.
- `cell_phenotypes.tsv`: cell-to-phenotype/group label plus group order.
- `cluster_identities.tsv`: one row per public group.
- `enrichment_background.txt`: every public expression gene.
- `prior_grn.tsv`: fixed BoolODE GRN in oracle-prior form.
- `tf_list.txt`: native regulators from `refNetwork.csv`.
- `prior_grn_by_group.tsv`: fixed BoolODE GRN duplicated per group.

`provenance/raw/` must include:

- input request JSON
- resolved parameter JSON
- generated BoolODE YAML config
- copied custom input files or resolved bundled input file names
- raw BoolODE output directory, including `ExpressionData.csv`, `PseudoTime.csv`, `refNetwork.csv`, `ClusterIds.csv` when present, `model.py`, `parameters.txt` and `simulations/`
- clean expression before dropout if dropout is enabled
- stdout/stderr logs
- session info with Python, package versions and pinned BoolODE commit

Manifest dependencies:

- `dataset-manifest.json` for `infer-network` needs `expression.tsv` and any requested files under `extras/`.
- `ground-truth-manifest.json` for `evaluate-inference` needs `truth/networks.csv` and `truth/gene_universe.txt`.
- The wrapper writes `simulator-output-manifest.json`; generate-data core owns benchmark package manifests and GUI bundles.

## Field-By-Field Contract

| Field | Chosen value | Evidence | Rationale | Uncertainty |
| --- | --- | --- | --- | --- |
| `schema_version` | `1.0` | Current simulator schema | Matches existing catalog. | None. |
| `id` | `boolode` | User request and target paths | Stable lowercase id. | None. |
| `name` | `BoolODE` | README and paper methods section | Official upstream spelling. | None. |
| `publication` | `https://doi.org/10.1038/s41592-019-0690-6` | README citation and paper first page | Primary publication introducing BoolODE in BEELINE. | It is a benchmark paper, not a standalone software note. |
| `first_author` | `Aditya Pratapa` | Paper first page | Full first-author name. | None. |
| `year` | `2020` | Paper citation | Publication year of primary paper. | None. |
| `implementation_url` | `https://github.com/Murali-group/BoolODE` | README and local clone remote | Public source used for installation. | None. |
| `docker_image` | `adriansegura99/simulator_boolode:1.0.0` | Playbook and user requirement | Required image naming convention. | Built and used for smoke tests. |
| `extra_inputs.required` | `[]` | Bundled data/config examples | Presets can run without mounted user files. | Docker install preserves bundled data files. |
| `extra_inputs.optional` | initial conditions, interaction strengths | README/config parser/model generator | These files enrich or alter simulation but are not required for every run. | Generic preflight cannot fully validate Python-literal list cells. |
| `extra_inputs.conditional_required` | Boolean model when `model_preset=custom_files` | README input section | Custom runs cannot proceed without a model definition. | None. |
| `runtime_resources.threading` | unsupported, one thread | Source `do_parallel` uses unbounded `Pool()` and hard-coded KMeans `n_jobs=8` | Avoids pretending ANDREA can map assigned threads safely; wrapper forces serial simulation and KMeans jobs. | A future wrapper may revisit only if upstream exposes bounded worker control. |
| `profile_capabilities.scrna_global` | supported | README outputs; paper defines cells as trajectory time points | Exports expression, native global truth and native pseudotime. | None. |
| `profile_capabilities.scrna_grouped` | supported | `ClusterIds.csv` code path and paper trajectory-clustering procedure | Exports groups and required group truth contexts by fixed-GRN duplication. | Group truth is not group-varying. |
| `scrna_cell_specific` | not claimed | No cell-specific GRN artifact | Prevents misleading duplicated cell contexts. | None. |
| `truth_outputs.global` | `native` | `refNetwork.csv` | Direct BoolODE output. | Pairwise simplification of Boolean rules. |
| `truth_outputs.group` | `derivable` for `scrna_grouped` | `ClusterIds.csv` and fixed GRN | Duplicate global truth per public group. | All groups share topology. |
| `native_extras.pseudotime` | claimed for both profiles | README states BoolODE uses simulation time as pseudotime; `PseudoTime.csv` code | Direct native simulator output normalized to TSV. | Slingshot pseudotime is not claimed. |
| `params` | Serializable subset listed above | README, config examples and parser defaults | Exposes supportable public behavior while keeping resource and wrapper plumbing separate. | Some upstream documented options are intentionally excluded due ignored/broken code paths. |

## Smoke-Test Matrix

1. `boolode_global_custom_dropout_extras`
   - profile: `scrna_global`
   - inputs: custom `boolode_boolean_model`, `boolode_initial_conditions`, `boolode_interaction_strengths`
   - params: `model_preset=custom_files`, `dropout.enabled=true`
   - extras: `pseudotime`, `enrichment_background`, `prior_grn`, `tf_list`
   - proves `expression.tsv`, `truth/networks.csv` with `context=global`, `truth/gene_universe.txt`, native pseudotime, conditional custom model handling, optional input handling, dropout expression path, manifest, progress, provenance and public gene ID consistency.
2. `boolode_grouped_builtin_full`
   - profile: `scrna_grouped`
   - params: bundled `dyn_bifurcating`, `n_clusters=2`, `sample_cells=false`
   - extras: `pseudotime`, `groups`, `cell_phenotypes`, `cluster_identities`, `enrichment_background`, `prior_grn`, `tf_list`, `prior_grn_by_group`
   - proves `global` and `group:` truth contexts, group context coverage, group extras and public cell/group ID consistency.
3. `boolode_grouped_large_sampling_branch`
   - profile: `scrna_grouped`
   - params: bundled `dyn_bifurcating`, `simulation_time=1.0`, `integration_step_size=0.001`, `num_cells=2`, `n_clusters=2`, `sample_cells=false`
   - extras: `groups`, `prior_grn_by_group`
   - proves BoolODE's large-output sampling branch, where the upstream requires integer `simulation_time` typing, plus grouped truth context generation.

The smoke matrix intentionally has no `scrna_cell_specific` config because the spec does not claim that profile. It also intentionally excludes `lineage_tree` because no lineage extra is claimed. The implemented matrix passed with `make run-simulator-smoketests ARGS="--simulator boolode --skip-build"` after building `adriansegura99/simulator_boolode:1.0.0`.
