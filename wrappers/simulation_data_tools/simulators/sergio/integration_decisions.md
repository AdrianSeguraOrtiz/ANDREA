# SERGIO Integration Decisions

Phase: 3 verified and final-aligned.

## Upstream Target

- Simulator id: `sergio`
- Canonical project name: `SERGIO`
- Full name: `Single-cell ExpRession of Genes In silicO`
- Public implementation URL: `https://github.com/PayamDiba/SERGIO`
- Public installation route: clone the public GitHub repository at a pinned commit; no PyPI package is available in the reviewed upstream README.
- Pinned runtime commit: `a6190b74425112834c8fa9b4b6157d9cb3d1ab88`
- Docker image: `adriansegura99/simulator_sergio:1.0.0`
- Public entrypoint mirrored by the wrapper: Python module class `SERGIO.sergio.sergio`, with `build_graph()`, `simulate()` / `simulate_dynamics()`, `getExpressions()` / `getExpressions_dynamics()`, and optional technical-noise methods.
- Runtime does not depend on `wrappers/simulation_data_tools/simulators/sergio/repo/`; the Docker image installs from `https://github.com/PayamDiba/SERGIO.git` pinned to the commit above.

The local clone HEAD is `a6190b74425112834c8fa9b4b6157d9cb3d1ab88`. The GitHub commits page currently shows the latest visible `master` commit as `a6190b7` on July 30, 2020, matching the requested full commit prefix.

## Evidence Used

- Upstream README: [README.md](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/repo/SERGIO/README.md)
- Public API implementation: [SERGIO/sergio.py](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/repo/SERGIO/SERGIO/sergio.py)
- Gene state object: [SERGIO/gene.py](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/repo/SERGIO/SERGIO/gene.py)
- Demo notebook: [demo_run.ipynb](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/repo/SERGIO/Demo/demo_run.ipynb)
- Repository notebook: [run_sergio.ipynb](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/repo/SERGIO/run_sergio.ipynb)
- Demo steady-state target interactions: [steady-state_input_GRN.txt](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/repo/SERGIO/Demo/steady-state_input_GRN.txt)
- Demo steady-state master regulators: [steady-state_input_MRs.txt](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/repo/SERGIO/Demo/steady-state_input_MRs.txt)
- Demo differentiation bifurcation matrix: [differentiation_graph.tab](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/repo/SERGIO/Demo/differentiation_graph.tab)
- Primary paper text: [nihms-1625336.txt](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/papers/nihms-1625336.txt)
- Current simulator schema: [simulatorspec.schema.json](/home/adrian/Grecia/separation/ANDREA/andrea/catalog_simulation_data_tools/schemas/simulatorspec.schema.json)
- Current output validation contract: [output_validation.py](/home/adrian/Grecia/separation/ANDREA/andrea/core/commands/generate_data/output_validation.py)

## Scientific Contract

Claimed canonical profiles:

- `scrna_global`
  - SERGIO produces a single-cell expression matrix guided by a fixed GRN.
  - Public truth is `context=global` rows from the SERGIO target interaction file used by `build_graph()`.
  - No group or cell truth contexts are exported for this profile.
- `scrna_grouped`
  - SERGIO natively simulates `number_bins` cell types/bins and returns arrays in bin-major order.
  - `groups.tsv` is derived from the known bin membership of each expression column.
  - Public group truth is derived by duplicating the fixed SERGIO GRN into one `context=group:<group_id>` network per bin. This is explicit fixed-GRN truth, not a claim of group-specific topology.

Not claimed:

- `scrna_cell_specific`: SERGIO does not expose native cell-specific GRNs. Duplicating the fixed GRN for every cell would satisfy file shape but would misrepresent the profile.
- Bulk profiles: SERGIO is a single-cell simulator. Aggregating cells into bulk samples would be an ANDREA-side invention.
- Perturbational profile: the paper discusses in silico knockout analyses, but the reviewed public module has no first-class JSON-safe perturbational experiment API in the wrapper contract.

## Truth-Context Audit

`scrna_global`:

| Context | Status | Evidence | Wrapper rule | Score/sign | Limitations |
| --- | --- | --- | --- | --- | --- |
| `global` | `native` | `README.md` documents `build_graph(input_file_taregts, input_file_regs)` and signed K values; `sergio.py` stores interactions in `graph_`. | Parse the same target interaction rows passed to `build_graph()` and export non-self-loop edges as `context=global`. | `score=abs(K)`; public sign is `+` for K>0 and `-` for K<0. | SERGIO does not emit a separate truth file; truth is the simulator GRN input/state. Hill coefficients and half-response values are not public truth columns. |
| `group` | `none` | Global profile has no required groups. | No rows. | Not applicable. | None. |
| `cell` | `none` | No cell-specific GRN in source or README. | No rows. | Not applicable. | SERGIO's GRN is fixed across cells. |

`scrna_grouped`:

| Context | Status | Evidence | Wrapper rule | Score/sign | Limitations |
| --- | --- | --- | --- | --- | --- |
| `global` | `native` | Same fixed target interaction file and `graph_` state. | Export one global edge per SERGIO K value. | `score=abs(K)`; public sign is `+` for K>0 and `-` for K<0. | Global truth is the fixed simulator GRN. |
| `group` | `derivable` | README states concatenated expression columns are grouped by cell type/bin; source returns arrays shaped `#bins x #genes x #cells_per_type`. | Write `groups.tsv` from bin membership, then duplicate the fixed global GRN into one `group:<group_id>` context per exported bin. | Same as global. | Group networks are identical. They document fixed-GRN simulation rather than group-specific regulatory rewiring. |
| `cell` | `none` | No native cell-specific GRN output exists. | No rows. | Not applicable. | Cell-specific truth is intentionally unclaimed. |

Required upstream switches:

- `build_graph(input_file_taregts=..., input_file_regs=..., shared_coop_state=...)` is required for all claimed truth.
- `simulate()` is used when `simulation_mode=steady_state`.
- `simulate_dynamics()` is used when `simulation_mode=differentiation` and requires `bifurcation_matrix`.
- `number_bins >= 2` is required for `scrna_grouped`.

## Extra Outputs

Claimed for `scrna_global`:

- `enrichment_background`: derived from expression genes.
- `prior_grn`: oracle prior from the same SERGIO fixed GRN used for truth.
- `tf_list`: union of regulator IDs in the target interaction file and master-regulator IDs in the master-regulator file.

Claimed for `scrna_grouped`:

- `groups`: derived from bin membership.
- `cell_phenotypes`: same label as group; order from numeric bin order for steady-state or bifurcation topological order for differentiation.
- `cluster_identities`: one row per bin/group.
- `enrichment_background`: derived from expression genes.
- `lineage_tree`: derived only for `simulation_mode=differentiation` from the SERGIO bifurcation matrix. Root rows use `parent=__root__`; gain/loss rates are `0` because the GRN is fixed across bins.
- `prior_grn`: oracle global prior from the fixed GRN.
- `tf_list`: regulator/master-regulator IDs.
- `prior_grn_by_group`: fixed GRN duplicated per group as an oracle group prior.

Not claimed as extras:

- `pseudotime`: SERGIO differentiation has transient simulations, but the public `getExpressions_dynamics()` method samples random cells without returning the sampled time indices. A coarse bin-depth pseudotime would be available, but it would not represent the actual sampled transient positions, so it is not claimed.
- RNA velocity: SERGIO differentiation returns unspliced and spliced expression, but ANDREA has no current simulator extra for velocity and SERGIO does not emit a velocity matrix.
- Cell-specific regulatory truth: not present.

## Simulator-Side Inputs

Existing input specs were not reused:

- `regulatory_network` is a simple `target/regulator/effect` TSV. SERGIO needs a variable-width comma-separated target interaction parameter file with regulator counts, K values and Hill coefficients.
- `tree_newick` is a Newick tree. SERGIO uses a square numeric bifurcation/migration matrix.

New shared input specs added:

- [sergio_target_interactions.json](/home/adrian/Grecia/separation/ANDREA/andrea/catalog_simulation_data_tools/input_specs/sergio_target_interactions.json)
- [sergio_master_regulators.json](/home/adrian/Grecia/separation/ANDREA/andrea/catalog_simulation_data_tools/input_specs/sergio_master_regulators.json)
- [sergio_bifurcation_matrix.json](/home/adrian/Grecia/separation/ANDREA/andrea/catalog_simulation_data_tools/input_specs/sergio_bifurcation_matrix.json)

Input requirements in the spec:

- Required: none, because `input_preset=demo_steady_state` can run from files in the pinned public SERGIO repository.
- Optional: none.
- Conditional:
  - `sergio_target_interactions` when `input_preset=custom_files`.
  - `sergio_master_regulators` when `input_preset=custom_files`.
  - `sergio_bifurcation_matrix` when `input_preset=custom_files` and `simulation_mode=differentiation`.

Wrapper validation checks custom file consistency that the current generic catalog cannot express:

- `number_genes` matches all target and regulator IDs.
- `number_bins` matches master-regulator rate columns.
- Differentiation matrix is square, non-negative, acyclic, has at most one parent per bin, and has dimensions equal to `number_bins`.
- Every gene is represented exactly as either a master regulator or target, matching SERGIO's `build_graph()` expectations.

## Parameter Surface

Exposed JSON-serializable upstream parameters:

- Input/mode controls: `input_preset`, `simulation_mode`.
- Constructor controls: `number_genes`, `number_bins`, `number_sc`, `noise_params`, `noise_type`, `decays`, `sampling_state`, `tol`, `window_length`, `dt`, `optimize_sampling`, `noise_params_splice`, `noise_type_splice`, `splice_ratio`, `dt_splice`.
- Graph loading control: `shared_coop_state`.
- Differentiation expression normalization: `differentiation_expression` chooses `total`, `spliced` or `unspliced` for `expression.tsv`.
- Technical noise controls: outlier, library-size, dropout and UMI conversion parameters in the same order shown in upstream notebooks.

JSON representation decisions:

- `noise_params`, `decays`, `noise_params_splice` and `splice_ratio` are scalar-or-array parameters because upstream accepts either a scalar or one value per gene.
- `bifurcation_matrix` is intentionally an input file, not an arbitrary JSON matrix, so large differentiation graphs stay reproducible and reusable.
- ANDREA `seed` is not a simulator parameter. The wrapper calls `numpy.random.seed(seed)` before constructing/running SERGIO.
- Runtime threads are not simulator parameters.

Not exposed:

- `migration_rate`: present in the constructor signature at the pinned commit but not used elsewhere in `sergio.py`.
- Arbitrary post-processing callbacks: SERGIO does not expose function-valued callbacks in the reviewed public API.
- Free-form edits to built-in demo input files. Use `input_preset=custom_files` instead.

## Runtime Resources

- Upstream thread support: none found in README, notebooks or source.
- `runtime_resources.threading`:
  - `supported=false`
  - `default_threads=1`
  - `max_threads=1`
  - assigned ANDREA threads must remain `1`
- The wrapper fails fast if a request somehow provides `runtime_resources.threads != 1`.

## Output Normalization Contract

`expression.tsv`:

- Rows are public gene IDs `gene_<zero_based_sergio_id>`.
- Columns are public cell IDs `cell_bin<bin_id>_<within_bin_index>`.
- Steady-state mode uses `getExpressions()` output.
- Differentiation mode uses `getExpressions_dynamics()` and writes total `U+S` expression by default, unless `differentiation_expression` is `spliced` or `unspliced`.
- Enabled technical-noise modules run in the order shown in upstream notebooks: outlier effect, library-size effect, dropout, UMI conversion.

`truth/networks.csv`:

- Required columns: `source,target,score,sign,evidence,context`.
- `source` and `target` use the same public gene IDs as expression rows.
- `global`: one non-self-loop row per K value in the SERGIO target interaction file.
- `group:<group_id>`: for `scrna_grouped`, duplicate the fixed global network once per group.
- `score=abs(K)`, public `sign=+` for positive K and public `sign=-` for negative K.
- `evidence` identifies `sergio_input_grn`.

`truth/gene_universe.txt`:

- Every public expression gene, one per line. This must match expression rows exactly.

`extras/`:

- `groups.tsv`: one row per cell with group label `bin_<id>`.
- `cell_phenotypes.tsv`: one row per cell, phenotype equals group.
- `cluster_identities.tsv`: one row per group.
- `enrichment_background.txt`: every public expression gene.
- `lineage_tree.tsv`: differentiation-only bin graph with root rows and zero gain/loss rates.
- `prior_grn.tsv`: fixed GRN in oracle-prior form.
- `tf_list.txt`: regulator/master-regulator public gene IDs.
- `prior_grn_by_group.tsv`: fixed GRN duplicated per group.

`provenance/raw/` includes:

- input request JSON
- resolved parameter JSON
- copied upstream input files used for the run
- `public_gene_id_map.tsv`
- `public_cell_id_map.tsv`
- raw clean expression arrays
- raw spliced/unspliced arrays for differentiation
- technical-noise intermediate arrays/factors when enabled
- `truth_source_edges.tsv` parsed from SERGIO interactions
- Python package versions and pinned SERGIO commit
- stdout/stderr logs

Manifest dependencies:

- `dataset-manifest.json` for `infer-network` needs `expression.tsv` plus requested extras under `extras/`.
- `ground-truth-manifest.json` for `evaluate-inference` needs `truth/networks.csv` and `truth/gene_universe.txt`.
- The wrapper writes `simulator-output-manifest.json`; generate-data core owns downstream ZIP bundle families and strict GUI handoff bundles.

## Field-By-Field Contract

| Field | Chosen value | Evidence | Rationale | Uncertainty |
| --- | --- | --- | --- | --- |
| `schema_version` | `1.0` | Current simulator schema | Matches existing catalog. | None. |
| `id` | `sergio` | User request and target paths | Stable lowercase id. | None. |
| `name` | `SERGIO` | README and paper title | Official upstream spelling. | None. |
| `publication` | `https://doi.org/10.1016/j.cels.2020.08.003` | Paper first page | Primary simulator paper DOI as canonical URL. | README also has a Zenodo badge, but the primary publication is sufficient for this integration. |
| `first_author` | `Payam Dibaeinia` | Paper first page | Full first-author name. | None. |
| `year` | `2020` | Paper final citation | Publication year of primary paper. | None. |
| `implementation_url` | `https://github.com/PayamDiba/SERGIO` | README and paper availability statement | Public implementation source. | None. |
| `docker_image` | `adriansegura99/simulator_sergio:1.0.0` | Playbook naming rule and user requirement | Required image naming convention. | Built and smoke-tested locally. |
| `extra_inputs.required` | `[]` | README/Demo public bundled files | Default preset can run from pinned public repo files. | Installed clone path includes Demo files, but bundled 100-gene demo runs are too slow for routine smoke tests. |
| `extra_inputs.conditional_required` | SERGIO target interactions, master regulators and differentiation matrix for custom files | README `build_graph()` and `bifurcation_matrix` docs | Required only when the user selects custom SERGIO input files. | Generic preflight cannot inspect variable-width custom file consistency. |
| `runtime_resources.threading` | unsupported, 1 thread | Source has no worker/thread parameter | Prevents exposing fake parallelism. | None. |
| `profile_capabilities.scrna_global` | supported | Paper and README describe single-cell expression simulation from GRN | Exports expression and global GRN truth. | None. |
| `profile_capabilities.scrna_grouped` | supported | README states outputs are grouped by cell type/bin | Exports group labels and group truth contexts. | Group truth is identical across groups by design. |
| `scrna_cell_specific` | not claimed | No cell-specific GRN API/artifact in source | Avoids misleading duplicated cell contexts. | None. |
| `truth_outputs.global` | `native` | SERGIO input GRN drives the simulator and is stored in `graph_` | Direct normalized simulator GRN. | No separate truth file is emitted. |
| `truth_outputs.group` | `derivable` for `scrna_grouped` | Bin membership is known and GRN is fixed | Duplicate global GRN per bin. | Group networks are not group-varying. |
| `truth_outputs.cell` | `none` | Source has no cell-specific GRN object | Not claimable. | None. |
| `derivable_extras` | Listed above by profile | README output shapes, input files and source technical-noise methods | Every claimed extra has a deterministic wrapper rule. | `lineage_tree` is differentiation-only and guarded by a run-scoped compatibility rule plus wrapper validation. |
| `params` | Serializable constructor, graph and noise parameters | README usage and `sergio.py` signature/methods | Exposes supportable public API surface while keeping seed/runtime resources separate. | None beyond upstream runtime cost for large bundled demos. |
| `compatibility_rules.lineage_tree` | `scope=run`, block when `lineage_tree` is requested and `simulation_mode!=differentiation` | Generate-data scenario preflight has only default params; plan/run validation has resolved run params | Prevents valid lineage scenarios from being blocked before a SERGIO run chooses differentiation mode, while still rejecting invalid selected runs before Docker execution. | Requires the shared SimulatorSpec schema to support run-scoped compatibility rules. |
| `compatibility_rules.demo_differentiation_runtime` | Block `input_preset=demo_differentiation` when `number_sc>100`, `sampling_state>1`, scalar `noise_params>0.3`, scalar `noise_params_splice>0.3` or scalar `splice_ratio>1.5` | Upstream demo notebook uses `number_sc=100`, `noise_params=0.3`, `sampling_state=1`, `noise_params_splice=0.1` and `splice_ratio=1.5`; README recommends small differentiation noise values; GUI runs with `number_sc=300` or `sampling_state=15` stalled or exited 137 | Prevents known unsafe public demo-preset configurations from entering monolithic `simulate_dynamics()` without diagnostics. | Larger SERGIO differentiation runs may be scientifically possible with custom files or manual tuning, but are not promised by this demo preset. |
| `cost profile` | No `cost.json` for SERGIO in Phase 3 | No timing benchmark matrix has been collected beyond smoke tests | Generate-data uses conservative fallback ETA/resource planning with explicit fallback provenance. | A future cost-benchmark phase can add catalog costs for production scheduling. |

## Smoke-Test Matrix

Implemented smoke configs:

1. `sergio_global_custom_basic`
   - profile: `scrna_global`
   - inputs: compact custom SERGIO target interaction and master-regulator files
   - extras: none
   - validates `expression.tsv`, `truth/networks.csv` with `context=global`, `truth/gene_universe.txt`, manifest, progress, copied inputs, public ID maps and session provenance
2. `sergio_global_custom_extras_noise`
   - profile: `scrna_global`
   - extras: `enrichment_background`, `prior_grn`, `tf_list`
   - params: compact custom inputs with outlier, dropout and UMI technical-noise modules enabled
   - validates generated global extras, technical-noise raw outputs and public gene ID consistency
3. `sergio_grouped_custom_steady_full`
   - profile: `scrna_grouped`
   - extras: `groups`, `cell_phenotypes`, `cluster_identities`, `enrichment_background`, `prior_grn`, `tf_list`, `prior_grn_by_group`
   - inputs: compact custom SERGIO target interaction and master-regulator files
   - validates `global` and `group:` truth contexts, group context coverage, all grouped extras except lineage and cross-file public ID consistency
4. `sergio_grouped_custom_differentiation_lineage`
   - profile: `scrna_grouped`
   - extras: all grouped extras including `lineage_tree`
   - inputs: compact custom SERGIO target interaction, master-regulator and bifurcation matrix files
   - params: `input_preset=custom_files`, `simulation_mode=differentiation`
   - validates conditional mounted-input handling, lineage root/child coverage, `global` and `group:` truth contexts, fixed-GRN group truth duplication and cross-file public ID consistency

The smoke matrix intentionally has no `scrna_cell_specific` config because the spec does not claim that profile. Shared output validation enforces the required truth contexts for each claimed profile and verifies that group contexts match `groups.tsv`.

A local probe of `input_preset=demo_steady_state` with `number_genes=100`, `number_bins=9`, `number_sc=1` and `sampling_state=1` was still running after more than 90 seconds and was interrupted. The wrapper and Docker image still support the bundled public SERGIO demo presets, but routine smoke tests use compact custom inputs so CI covers the executable contract without inheriting the upstream demo runtime.

After GUI testing, the public differentiation demo preset was narrowed to the upstream notebook scale. The failed GUI plan at `/tmp/andrea_gui/generate_data/e8b03895b903/simulation-plan.json` used `input_preset=demo_differentiation`, `number_sc=300`, `sampling_state=15` and full technical noise, and Docker exited with code 137. A follow-up GUI plan at `benchmarks/gui_generate_benchmark_20260617T112322Z` used `sampling_state=1` but still kept `number_sc=300`, `noise_params=1.0` and `splice_ratio=4.0`; it remained inside `simulate_dynamics()` for more than 48 minutes while using about 24 GiB. The upstream demo notebook uses `number_sc=100`, `sampling_state=1`, `noise_params=0.3`, `noise_params_splice=0.1` and `splice_ratio=1.5`; a direct Docker probe of that recommended configuration completed in about 51 seconds. Because SERGIO's dynamics call is monolithic and keeps simulated history for sampling, the wrapper and spec now reject `demo_differentiation` configurations outside that public demo scale before execution. The top-level SERGIO defaults were also made conservative (`number_sc=100`, `noise_params=0.3`, `sampling_state=1`, `noise_params_splice=0.1`, `noise_type_splice=dpd`, `splice_ratio=1.5`) so GUI-created runs do not inherit steady-state-only demo values by default.

## Implementation Outcome

- Implemented [run_simulator.py](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/run_simulator.py).
- Implemented [Dockerfile](/home/adrian/Grecia/separation/ANDREA/wrappers/simulation_data_tools/simulators/sergio/Dockerfile), installing from `https://github.com/PayamDiba/SERGIO.git` pinned to `a6190b74425112834c8fa9b4b6157d9cb3d1ab88`.
- The wrapper reads `/work/request/simulator-run-request.json`, writes directly under `/work/out/`, rejects `runtime_resources.threads != 1`, calls the public `SERGIO.sergio.sergio` API, and writes `progress.json`.
- SERGIO differentiation runs inside the monolithic upstream `simulate_dynamics()` / `getExpressions_dynamics()` calls, which expose no progress callback. The wrapper therefore reports generate-data-compatible phases and emits a bounded `run_simulator` heartbeat with elapsed seconds while the upstream call is still running; this is liveness reporting, not native simulator progress.
- Raw provenance includes the request, resolved parameters, copied SERGIO input files, public gene/cell ID maps, parsed truth-source edges, raw NumPy expression arrays, upstream stdout/stderr logs and `session_info.txt`.
- Public truth rows match the spec: `scrna_global` emits only `context=global`; `scrna_grouped` emits `context=global` plus one `context=group:<group_id>` duplicate fixed-GRN network per group; no `cell:` contexts are emitted.
- Deterministic provenance filenames are `input_target.csv`, `input_master_regulators.csv` and `input_bifurcation.tsv` because Docker smoke-test staged inputs are mounted without source extensions.
- Phase 3 added a run-scoped compatibility guard for `lineage_tree`: scenario preflight no longer blocks SERGIO before run params are known, but `generate-data plan` still rejects selected SERGIO runs that request `lineage_tree` without `simulation_mode=differentiation`.

Implementation verification:

- `docker build -f wrappers/simulation_data_tools/simulators/sergio/Dockerfile -t adriansegura99/simulator_sergio:1.0.0 .`: passed
- `PYTHONPATH=. PYENV_VERSION=3.10.7 pyenv exec python wrappers/simulation_data_tools/scripts/run_smoketests.py --simulator sergio --skip-build`: passed
- `PYTHONPATH=. PYENV_VERSION=3.10.7 pyenv exec python wrappers/simulation_data_tools/scripts/validate_input_specs.py`: passed
- `PYTHONPATH=. PYENV_VERSION=3.10.7 pyenv exec python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py --simulator sergio`: passed
- `PYTHONPATH=. PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/wrappers/simulation_data_tools/test_input_specs.py tests/wrappers/simulation_data_tools/test_simulatorspecs.py`: 23 passed

## Phase 3 Final Verification

- `make validate-simulatorspecs ARGS="--simulator sergio"`: passed
- `make validate-simulator-smoketest-configs ARGS="--simulator sergio"`: 4 SERGIO configs valid
- `make run-simulator-smoketests ARGS="--simulator sergio --skip-build"`: passed
- `make validate-simulatorspecs`: 3 simulator specs valid
- `make validate-simulator-smoketest-configs`: 16 smoke configs valid
- `PYTHONPATH=. PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/wrappers/simulation_data_tools/test_input_specs.py tests/wrappers/simulation_data_tools/test_simulatorspecs.py`: 23 passed
- `PYTHONPATH=. PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/core/commands/generate_data/test_generate_data.py`: 46 passed
- `PYTHONPATH=. PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/cli/test_generate_data_cli.py tests/gui/test_generate_data_server.py tests/core/test_generate_data_progress.py`: 6 passed, 3 skipped
- Generated temporary SERGIO datasets through `execute_generate_data()` for `scrna_global` and `scrna_grouped` differentiation. Both generated `dataset-manifest.json` files passed `infer-network preflight`:
  - `sergio_phase3_global__sergio_custom_global__r01`: 3 genes, 4 cells, 7 eligible tools
  - `sergio_phase3_grouped__sergio_custom_grouped__r01`: 3 genes, 4 cells, 10 eligible tools

No wrapper-output mismatch remains after Phase 3. Public truth contexts, extras, manifests, and provenance validate through both the simulator smoke matrix and `generate-data` packaging.

## Post-GUI Failure Follow-Up

- Rebuilt `adriansegura99/simulator_sergio:1.0.0` after adding the `demo_differentiation` guard.
- Direct Docker run of the failed GUI configuration now exits quickly with `ValueError` and writes `progress.json` plus `provenance/raw/wrapper_error.log`; it no longer enters the long `simulate_dynamics()` call.
- `validate_simulation_plan('/tmp/andrea_gui/generate_data/e8b03895b903/simulation-plan.json')` now rejects the unsafe run with the `sampling_state=1` compatibility message.
- `validate_simulation_plan('benchmarks/gui_generate_benchmark_20260617T112322Z/simulation-plan.json')` now rejects the long-running follow-up plan with the `number_sc <= 100`, `noise_params <= 0.3` and `splice_ratio <= 1.5` compatibility messages.
- Direct Docker run of the safe demo differentiation configuration with full technical noise completed in about 49.5 seconds.
- `generate-data` now copies `simulation-plan.json` at execution start and preserves failed simulator staging under `failed_runs/<dataset_id>/` when `_run_simulator` raises.
- Verification after this follow-up:
  - `PYTHONPATH=. .venv/bin/python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py --simulator sergio`: passed
  - `PYTHONPATH=. .venv/bin/python wrappers/simulation_data_tools/scripts/validate_smoketest_configs.py --simulator sergio`: passed
  - `PYTHONPATH=. .venv/bin/python wrappers/simulation_data_tools/scripts/run_smoketests.py --simulator sergio --skip-build`: passed
  - `PYTHONPATH=. .venv/bin/python -m pytest tests/core/commands/generate_data/test_generate_data.py`: 48 passed before the follow-up runtime-scale guard
  - `PYTHONPATH=. .venv/bin/python -m pytest tests/core/commands/generate_data/test_generate_data.py::GenerateDataDyngenTests::test_run_generate_data_preserves_failed_simulator_stage tests/core/commands/generate_data/test_generate_data.py::GenerateDataDyngenTests::test_sergio_demo_differentiation_rejects_unsafe_sampling_state tests/core/commands/generate_data/test_generate_data.py::GenerateDataDyngenTests::test_sergio_demo_differentiation_rejects_unsafe_cell_count`: 3 passed
