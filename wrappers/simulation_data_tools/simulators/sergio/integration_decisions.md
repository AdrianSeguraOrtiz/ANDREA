# SERGIO Integration Decisions

Status: migrated to the semantic simulator contract.

## Upstream Target

- Simulator id: `sergio`
- Public project: `https://github.com/PayamDiba/SERGIO`
- Installation route: pinned public GitHub commit
- Pinned commit: `a6190b74425112834c8fa9b4b6157d9cb3d1ab88`
- Docker image: `adriansegura99/simulator_sergio:1.0.0`
- Wrapper entrypoint: public Python class `SERGIO.sergio.sergio`, using `build_graph()`, `simulate()` or `simulate_dynamics()`, `getExpressions()` or `getExpressions_dynamics()`, and public technical-noise methods.
- Runtime does not depend on the local `repo/` checkout.

## Evidence Used

- Upstream source: `wrappers/simulation_data_tools/simulators/sergio/repo/SERGIO/`
- Upstream README and notebooks under the pinned repo snapshot.
- Paper text: `wrappers/simulation_data_tools/simulators/sergio/papers/nihms-1625336.txt`
- Wrapper: `wrappers/simulation_data_tools/simulators/sergio/run_simulator.py`
- Catalog spec: `andrea/catalog_simulation_data_tools/simulators/sergio/simulatorspec.json`

## Claimed Semantic Capabilities

All claimed capabilities use:

- `data_axes.measurement=rna_expression`
- `data_axes.resolution=single_cell`
- `data_axes.column_kind=cells`

| Experimental design | Truth requirements | Public contexts emitted | Status |
| --- | --- | --- | --- |
| `steady_state` | `["global"]` | `global` | Supported |
| `differentiation` | `["global"]` | `global` | Supported |
| `steady_state` | `["global", "group"]` | `global`, `group:<id>` | Supported |
| `differentiation` | `["global", "group"]` | `global`, `group:<id>` | Supported |

Unclaimed capabilities:

- `column` truth is not claimed. SERGIO uses a fixed GRN and does not emit per-expression-column GRNs; duplicating the fixed network into `column:<id>` contexts would misrepresent the contract.
- Bulk, pseudo-bulk, spatial, perturbational and time-series axes were re-reviewed during the expanded semantic-contract migration and are not claimed. The reviewed public API has no JSON-safe perturbation/time-series benchmark contract with labeled truth contexts, and differentiation mode does not expose a sampled per-cell temporal index that would satisfy the normalized `timepoints.tsv` contract.
- Pseudotime is not claimed. Differentiation simulations sample random transient cells without returning the sampled time indices needed for a public pseudotime extra.

## Truth Context Decisions

- `global` is native. The wrapper parses the same target-interaction file passed to `build_graph()` and writes one public row per non-self-loop SERGIO K value.
- `group` is derivable. SERGIO simulates bins/cell types; the wrapper derives `groups.tsv` from bin membership and duplicates the fixed global GRN into one `context=group:<id>` network per observed group.
- `column` is unavailable for the public contract.

Required upstream switches:

- `build_graph(input_file_taregts=..., input_file_regs=..., shared_coop_state=...)` for every run.
- `simulate()` for `experimental_design=steady_state`.
- `simulate_dynamics()` plus a bifurcation matrix for `experimental_design=differentiation`.
- `number_bins >= 2` when `group` truth or group-derived extras are requested.
- `simulation_mode` is represented as a semantic `parameter_binding`, locked from `data_axes.experimental_design`; it is not a user-editable run parameter in the GUI contract. Differentiation capabilities also default `input_preset=demo_differentiation` and `number_bins=3` when unset so the bundled dynamics demo is internally consistent.

Score/sign semantics:

- `score=abs(K)` from SERGIO regulatory interaction strengths.
- `sign=+` for positive K and `sign=-` for negative K.
- Hill coefficients, half-response values and master-regulator basal rates are simulator parameters/provenance, not public truth scores.

## Extras

Derivable extras:

- `groups`: one row per expression column with group label `bin_<id>`.
- `column_phenotypes`: group label plus bin order.
- `cluster_identities`: one row per public group.
- `lineage_tree`: differentiation-only, derived from the SERGIO bifurcation matrix; root rows use `parent=__root__` and gain/loss rates are zero because the GRN is fixed.
- `prior_grn`: oracle prior from the fixed SERGIO GRN.
- `prior_grn_by_group`: fixed GRN duplicated per group.
- `tf_list`: regulator and master-regulator public gene IDs.
- `enrichment_background`: all exported expression genes.

Not claimed as extras:

- `pseudotime`, RNA velocity and column-level truth.

## Inputs, Params And Resources

- Required simulator inputs: none for built-in demo presets.
- Conditional inputs:
  - `sergio_target_interactions` when `input_preset=custom_files`.
  - `sergio_master_regulators` when `input_preset=custom_files`.
  - `sergio_bifurcation_matrix` when `input_preset=custom_files` and `experimental_design=differentiation`.
- Runtime threading is unsupported. The wrapper rejects `runtime_resources.threads != 1`.
- Thread controls are not exposed as simulator parameters.
- No function-valued callbacks are exposed by the reviewed public SERGIO API.

## Normalized Output Contract

- `expression.tsv`: public gene IDs in rows and public expression-column IDs in columns.
- `truth/networks.csv`: one table with `source,target,score,sign,evidence,context`.
- `truth/gene_universe.txt`: exact public expression gene universe.
- `extras/`: requested standardized extras only.
- `native/`: selected native SERGIO arrays, copied from raw upstream artifacts and listed in `simulator-output-manifest.json`.
- `provenance/raw/`: request snapshot, resolved params, copied upstream input files, public ID maps, raw expression arrays, technical-noise intermediates, parsed truth source edges and runtime package information.

The smoke matrix covers steady-state and differentiation, global and group truth, custom input files, technical-noise paths and public ID consistency.

## Scientific audit, 2026-09-29

Sources: [primary publication](https://pubmed.ncbi.nlm.nih.gov/32871105/),
[pinned simulator source](https://github.com/PayamDiba/SERGIO/blob/a6190b74425112834c8fa9b4b6157d9cb3d1ab88/SERGIO/sergio.py), and
[pinned examples](https://github.com/PayamDiba/SERGIO/tree/a6190b74425112834c8fa9b4b6157d9cb3d1ab88).
Installed source was inspected for regulator/target indexing, signed K values,
steady-state/differentiation sampling and noise RNG, against the wrapper's
custom-input validation and `truth_edges`/`write_truth_networks` mappings.

Truth uses nonzero non-self K entries: score=abs(K) and sign=sign(K). The same
coupling graph is used in every simulated bin, even when master-regulator rates
or differentiation states differ. Group truth and prior-by-group repeat that
graph. They support contextual pairing but do not establish rewiring. Lineage
is derived only from the differentiation bifurcation input, not inferred from
expression. Exported gene/cell IDs follow explicit native-index maps.

All reviewed stochastic simulation and technical-noise draws use NumPy, seeded
by the wrapper. No bounded public worker-count API is exposed, so one assigned
thread remains the contract. Clean output can be continuous concentrations;
Poisson count conversion and dropout depend on selected technical-noise
settings. A fixed seed does not make datasets realistic by itself. The default
presets fix gene/bin counts to their bundled inputs; arbitrary networks require
valid custom target/master-regulator files and the documented acyclic/dynamics
constraints. Column-specific regulatory truth is not claimed.

### Executed validation for the audited wrapper

On 2026-09-29 the repository Dockerfile built
`adriansegura99/simulator_sergio:1.1.0` successfully (local image
`sha256:8a89df636711d9882e8a59d6e3a9b7dda9c181e58da62656d7216ea69f2a2ac6`). The wrapper SHA-256 inside
that image matched the current repository file. All **5/5** simulator smoke
configurations passed on this final image, using the repository smoke runner
with at most 2 CPU threads, 8 GiB RAM and a 300-second timeout per fixture.
These checks cover executable contracts and fixture outputs; they do not
calibrate costs or independently validate biological realism.
