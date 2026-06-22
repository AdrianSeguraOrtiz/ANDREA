# Simulator Integration Playbook

This playbook defines the recommended workflow for integrating a benchmark data simulator into `andrea generate-data`.

Use it when you have local evidence under:

```text
wrappers/simulation_data_tools/simulators/<simulator_id>/
  repo/
  papers/
```

`repo/`, `papers/` and locally downloaded HTML/PDF documentation are evidence sources for analysis only. They must not become runtime dependencies.

Runtime behavior must be reproducible from:
- `andrea/catalog_simulation_data_tools/simulators/<simulator_id>/simulatorspec.json`
- shared simulator input specs under `andrea/catalog_simulation_data_tools/input_specs/`
- the wrapper under `wrappers/simulation_data_tools/simulators/<simulator_id>/`
- the `Dockerfile` under `wrappers/simulation_data_tools/simulators/<simulator_id>/`

The catalog must contain only simulators that are fully integrated, dockerized and smoketested. Do not add placeholder specs for simulators that are still under review.

## Scope

The goal of an integration is to produce:
- a coherent `simulatorspec.json`
- a wrapper that maps upstream simulator behavior to the ANDREA output contract
- a `Dockerfile` that installs the simulator from a stable public source
- a smoke-test matrix covering every declared semantic capability and extra combination
- an `integration_decisions.md` file documenting evidence and tradeoffs
- a `progress.json` implementation for observable execution state

The goal is not to reproduce a local vertical slice, bundled example export or copied repo execution. The wrapper must execute the public simulator installation declared by the integration.

## Source-Of-Truth Rules

1. Prefer the upstream implementation and paper over assumptions.
2. Keep `simulatorspec.json` aligned with the real upstream interface and the implemented wrapper.
3. The catalog is executable truth: if a simulator is listed, ANDREA must be able to run it through Docker.
4. Do not depend on local `wrappers/simulation_data_tools/simulators/<simulator_id>/repo/` at runtime.
5. Installation preference:
   - official package manager / library first
   - upstream public repo pinned to tag/commit if no package exists
   - never depend on unpinned floating source if avoidable
6. Docker image names must follow:

```text
adriansegura99/simulator_<simulator_id>:1.0.0
```

7. `publication` must be a list. Store DOI references as full canonical URLs such as `https://doi.org/...`.
8. `first_author` must be the full first-author name from the primary publication, not only surname.
9. Expose the full public parameter surface that is serializable and supportable through JSON. Function-valued callbacks may be represented by explicit presets, but should not be faked as arbitrary JSON parameters.
   Parameters that select the semantic scenario axes, such as steady-state vs
   differentiation mode, must be declared as capability `parameter_bindings`
   instead of remaining freely editable per run.
10. Runtime resource controls such as `threads`, `num_cores` or equivalent
    upstream parallelism knobs must be declared under
    `runtime_resources.threading`, not as user-facing simulator params.
11. Every supported semantic capability/extra combination must be covered by tests.
12. Simulator-side input files are defined in shared input specs. A
    `simulatorspec.json` may only declare how a simulator uses those files via
    `extra_inputs`; it must not define inline formats or examples.
13. Every claimed semantic capability must be audited independently. Do not
    infer support for one combination of axes from another only because the
    expression matrix shape is compatible.
14. Every claimed capability must document all public truth context families,
    including native upstream artifacts, wrapper-derived artifacts, upstream
    parameter switches, score/sign semantics and limitations.
15. Column-specific truth is cumulative when requested: it requires public
    `global` and any requested `group:<group_id>` rows plus
    `column:<column_id>` rows in `truth/networks.csv`. If group truth is
    requested, `groups` is a standardized extra because the group truth layer
    must be traceable to expression-column-to-group assignments.
16. Preserve public identifiers consistently across all normalized outputs.
    Gene ids in `expression.tsv`, `truth/networks.csv`, `truth/gene_universe.txt`
    and extras must refer to the same public ids. Column and group ids must also
    be consistent across expression columns, `groups.tsv`, other extras and
    truth contexts.
17. Keep packaging separate from wrapper behavior. The simulator wrapper emits
    the normalized dataset tree and manifests; `generate-data` core owns ZIP
    bundle families such as `analysis`, `report` and `full`, including strict
    GUI handoff bundles for downstream commands.

## Required Integration Order

Simulator integrations must proceed in this order:

1. Upstream source audit.
2. Current wrapper behavior audit, if a scaffold or older wrapper exists.
3. Wrapper correction or implementation.
4. SimulatorSpec update from verified wrapper behavior.
5. Smoke-test proof for every claimed semantic capability, truth context and
   extra path.

Do not write aspirational `truth_contexts` or capability claims into
`simulatorspec.json` before the wrapper can actually emit those outputs. The
catalog describes executable behavior, not intended future behavior.

## Core Concepts

### Semantic Dataset Axes

`andrea generate-data` models benchmark requests with independent semantic
axes, not with one combined profile string. Do not encode measurement modality,
experimental design and truth granularity into names such as
`<measurement>_<design>_<truth_granularity>`.

The central axes are:

- `measurement`: what the matrix measures. Initial value: `rna_expression`.
- `resolution`: biological/experimental resolution. Initial values: `bulk`,
  `single_cell`, `spatial`, `pseudo_bulk`, `mixed`, `unknown`.
- `column_kind`: what each expression column represents. Initial values:
  `samples`, `cells`, `timepoints`, `perturbations`, `spots`, `metacells`,
  `conditions`.
- `experimental_design`: scientific design of the benchmark. Initial values:
  `observational`, `steady_state`, `perturbational`, `time_series`,
  `trajectory`, `differentiation`.

Regulatory truth granularity is a separate request:

- `global`: one dataset-level GRN.
- `group`: one GRN per group of expression columns.
- `column`: one GRN per expression column.

The public `truth/networks.csv` context conventions are `global`,
`group:<group_id>` and `column:<column_id>`. The meaning of `column:<id>` comes
from `column_kind`: if columns are cells, this is cell-level truth; if columns
are timepoints, this is timepoint-level truth.

`groups.tsv` groups expression columns, not only cells. Avoid UI or wrapper
text that describes groups as cell-only unless the selected axes specifically
use `column_kind=cells`.

### Extras

Supported extras currently include:
- `groups`
- `cell_cell_interactions`
- `chromatin_accessibility`
- `chromatin_regions`
- `column_descriptors`
- `column_phenotypes`
- `cluster_identities`
- `enrichment_background`
- `interventions`
- `lineage_tree`
- `perturbation_design`
- `pseudotime`
- `prior_grn`
- `prior_grn_by_group`
- `replicates`
- `spatial_coordinates`
- `tf_list`
- `timepoints`

Extras describe optional generated artifacts that can accompany a scenario.
For example, a grouped single-cell workflow consumes `resolution=single_cell`,
`column_kind=cells`, `truth_requirements.contexts=["global", "group"]` plus
extras, not a special profile name.

Column-specific regulatory truth is not an infer-network extra input. It is a
public simulator truth output represented by `truth/networks.csv` rows with
`context=column:<column_id>`.

If a standardized extra is only valid for a semantic design, model that by
listing it only on the matching capability. For example, a lineage artifact that
exists only for trajectory/differentiation runs belongs in those capabilities,
not behind simulator-specific GUI logic. If the upstream simulator exposes this
through a parameter, bind that parameter with `parameter_bindings` so the
capability and run parameters stay aligned.

Simulator-specific native/provenance outputs may depend on run parameters,
requested extras or selected native outputs. Model that dependency with
`native_outputs[].conditions`; the GUI, planner and wrapper request validation
must evaluate those conditions centrally instead of adding simulator-specific
branches.

### Simulator-Side Inputs

Simulator-side inputs are files the user provides to the simulator before data
generation, such as a custom GRN parameter table or a differentiation tree.
Their reusable semantics live in:

```text
andrea/catalog_simulation_data_tools/input_specs/<input_id>.json
```

Each simulator declares only its specific usage and requirement rules in:

```text
simulatorspec.extra_inputs.required
simulatorspec.extra_inputs.optional
simulatorspec.extra_inputs.conditional_required
```

Use `required` only when the simulator cannot run without the file, `optional`
when providing the file enriches or changes the simulation without being forced
by a parameter, and `conditional_required` when a selected semantic capability,
requested extra, native output or parameter value requires the file. Generated
extras are outputs requested from the simulator; they are not simulator-side
inputs.

### Truth Outputs

Truth output modes are:
- `none`
- `native`
- `derivable`

Use these modes for:
- `global`
- `group`
- `column`

All public truth networks are exported through `truth/networks.csv`.
`context` determines whether an edge belongs to global, group or column truth.
`global` means rows with `context=global`; `group` means rows with
`context=group:<group_id>`; `column` means rows with
`context=column:<column_id>`.
Be explicit about whether each context family comes directly from the simulator or is derived by the wrapper from simulator-native state.
For simulators with native column-specific GRNs, distinguish native column truth
from any group truth that the wrapper derives by aggregating column-specific
simulator state. Do not label group-level summaries as native unless upstream
actually emits group-level truth.
Simulators may preserve native outputs under provenance, but public consumers must not depend on those native files.

### Truth Contexts

Every simulator capability entry must include `truth_contexts`.

For every supported `truth_outputs[]` entry, add one matching
`truth_contexts` entry with:

- `context`: `global`, `group` or `column`
- `status`: the same value as the matching `truth_outputs[]` entry
- `source_artifacts`: native upstream or normalized artifacts used
- `upstream_configuration`: capability-relevant upstream settings or switches
- `generation`: exact wrapper rule for public rows in `truth/networks.csv`
- `score_semantics`: how `score` and `sign` are computed
- `limitations`: what is lost, derived or not native

`status=none` is allowed only for contexts not required by the selected scenario
truth requirements. Contexts with `status=native` or `status=derivable` require
the evidence fields above. If a truth context depends on parameters, inputs or
requested native outputs, encode those constraints in
`truth_parameter_requirements` or `compatibility_rules` so invalid runs fail
before wrapper execution.

Every derived public artifact must also have a `derivations[]` entry in the relevant capability. This applies to both:
- each item in `derivable_extras`
- each `truth_outputs[]` entry whose status is `derivable`

Each derivation entry must state:
- `artifact`: the derived extra id or derived truth context family
- `source_artifacts`: simulator-native or already-normalized artifacts used as evidence
- `method`: the exact wrapper rule, including thresholds and tie-breaks
- `assumptions`: why the rule is acceptable for the benchmark scenario
- `limitations`: what information is lost or where the derivation can be misleading
- `implemented_in`: wrapper path where the rule is implemented

### Public Manifests And GUI Handoff

The normalized simulator output tree is consumed in two ways:

- `dataset-manifest.json` describes the generated expression dataset and is
  the strict input descriptor used by `infer-network`.
- `ground-truth-manifest.json` describes the truth artifacts needed by
  `evaluate-inference`, including `truth/networks.csv` and
  `truth/gene_universe.txt`.

The simulator wrapper must provide enough normalized outputs and
`simulator-output-manifest.json` metadata for `generate-data` to build both
manifests. The wrapper must not decide which files belong to GUI ZIP bundles.
Bundle definitions and readiness belong to `generate-data` core.

Current GUI handoff convention:

- `analysis` bundles are minimal strict ZIPs designed for downstream GUI
  upload. Per-dataset generate-data analysis bundles must contain
  `ground-truth-manifest.json` at the ZIP root plus the referenced truth files.
- `report` bundles are compact human/machine-readable summaries.
- `full` bundles contain the complete generated benchmark archive for storage
  and debugging.

## Official End-To-End Procedure

Follow these steps in order. A simulator is not considered integrated until the Docker image and smoke-test matrix pass.

### Step 0. Choose `<simulator_id>` And Target Scope

Manual action:
- choose a stable lowercase id using letters, digits and `_`
- confirm the canonical upstream project name and implementation URL
- decide which public package, repo tag or commit will be installed
- decide which upstream public API/CLI entrypoint the wrapper mirrors
- list which semantic axis combinations are realistically supported
- list which extras are native or derivable
- for every derived extra or truth output, write the exact derivation explanation before implementing the wrapper

This id will be reused in:
- simulator spec: `andrea/catalog_simulation_data_tools/simulators/<simulator_id>/simulatorspec.json`
- Phase 1 draft spec, when the wrapper is not implemented yet:
  `wrappers/simulation_data_tools/simulators/<simulator_id>/draft_simulatorspec.json`
- wrapper dir: `wrappers/simulation_data_tools/simulators/<simulator_id>/`
- smoke configs: `wrappers/simulation_data_tools/tests/smoketest_configs/<simulator_id>*.json`
- Docker image: `adriansegura99/simulator_<simulator_id>:1.0.0`

### Step 1. Place And Prepare Upstream Evidence

Manual action:
- clone or copy the upstream implementation into:

```text
wrappers/simulation_data_tools/simulators/<simulator_id>/repo/
```

- copy one or more local papers into:

```text
wrappers/simulation_data_tools/simulators/<simulator_id>/papers/
```

- convert PDFs to text if needed, keeping both PDF and `.txt` files as evidence.
- collect public docs such as CRAN pages, vignettes or HTML manuals when useful.

Minimum evidence expected in `repo/`:
- README or package docs
- examples/vignettes
- config files or CLI help
- relevant exported functions/scripts
- output examples if available

Minimum evidence expected in `papers/`:
- primary simulator paper
- implementation/package paper if different
- any additional paper needed to justify lineage, perturbation, multi-omics or ground-truth semantics

### Step 2. Execute Phase 1: Evidence And Contract

Goal:
- produce/update `integration_decisions.md`
- draft `simulatorspec.json`; keep it as
  `wrappers/simulation_data_tools/simulators/<simulator_id>/draft_simulatorspec.json`
  until the wrapper and Docker image are executable
- decide the public installation route
- decide the wrapper entrypoint and output mapping
- decide the smoke-test matrix

Prompt to paste in chat:

```text
Please integrate the simulator `<simulator_id>` following [SIMULATOR_INTEGRATION_PLAYBOOK.md](wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md), but only execute Phase 1 for now.

Context:
- Upstream repo is under [repo/](wrappers/simulation_data_tools/simulators/<simulator_id>/repo/)
- Local papers are under [papers/](wrappers/simulation_data_tools/simulators/<simulator_id>/papers/)
- Optional docs are under [wrappers/simulation_data_tools/simulators/<simulator_id>/](wrappers/simulation_data_tools/simulators/<simulator_id>/)
- The final executable SimulatorSpec target is [simulatorspec.json](andrea/catalog_simulation_data_tools/simulators/<simulator_id>/simulatorspec.json)
- During Phase 1, draft the spec at [draft_simulatorspec.json](wrappers/simulation_data_tools/simulators/<simulator_id>/draft_simulatorspec.json) unless an executable wrapper already exists
- The wrapper target directory is [wrappers/simulation_data_tools/simulators/<simulator_id>/](wrappers/simulation_data_tools/simulators/<simulator_id>/)
- Optional manual clarifications from the integrator:
  - preferred installation route: `<optional>`
  - preferred version/tag/commit: `<optional>`
  - target function/CLI/API entrypoint: `<optional>`
  - semantic capabilities/extras that must be prioritized: `<optional>`

Requirements:
- Review the upstream repo, local paper text/PDFs and available docs
- Produce or update [integration_decisions.md](wrappers/simulation_data_tools/simulators/<simulator_id>/integration_decisions.md)
- Draft [draft_simulatorspec.json](wrappers/simulation_data_tools/simulators/<simulator_id>/draft_simulatorspec.json), or update the catalog [simulatorspec.json](andrea/catalog_simulation_data_tools/simulators/<simulator_id>/simulatorspec.json) only if the executable wrapper already exists
- Do not implement the wrapper yet
- Store publication references as a list of full canonical URLs
- Store `first_author` as the full first-author name
- Set `docker_image` to `adriansegura99/simulator_<simulator_id>:1.0.0`
- Identify every semantic capability the simulator can support
- Identify every extra that is native or derivable
- Audit every claimed capability independently; for each capability, identify
  public `truth_contexts` for `global`, `group` and `column`, including whether
  each is native, derivable or unavailable
- Document the upstream parameter switches required to produce each claimed
  truth context
- Identify required, optional and conditional simulator input files
- Reuse existing simulation input specs when the file semantics match; otherwise
  draft a new spec under `andrea/catalog_simulation_data_tools/input_specs/`
- Identify the full public serializable parameter surface
- Identify runtime resource support, especially whether upstream exposes
  thread/worker controls and how ANDREA will map assigned threads to them
- Explicitly document parameters that are intentionally represented as presets because upstream expects function-valued callbacks
- Document any capability that exists scientifically but will not be claimed because the wrapper cannot support it yet
- For every non-trivial `simulatorspec.json` field, record:
  - chosen value
  - evidence path(s)
  - rationale
  - uncertainty if any
- Do not add placeholder specs for other simulators

Focus especially on:
1. semantic axis and truth-requirement support
2. optional and conditional input files
3. extras and truth-output derivation
4. parameter mapping and defaults
5. output normalization into `expression.tsv`, `extras/`, `truth/` and provenance
6. public installation source and version pinning
7. smoke-test matrix needed to cover the declared contract
8. capability-specific truth context evidence, including score/sign semantics and
   limitations
9. public id consistency across expression, truth, gene universe and extras
10. which normalized outputs are needed to build `dataset-manifest.json` for
    `infer-network` and `ground-truth-manifest.json` for `evaluate-inference`
```

### Step 3. Review Phase 1

Manual action:
- inspect `integration_decisions.md`
- inspect `simulatorspec.json`
- confirm that the catalog only claims what the wrapper will actually implement
- confirm that the selected installation route is public and reproducible
- confirm that no local repo path is part of runtime behavior

Do not continue to Phase 2 until the contract looks correct.

### Step 4. Execute Phase 2: Wrapper And Docker Implementation

Goal:
- implement the wrapper
- implement the Dockerfile
- keep `simulatorspec.json` aligned with actual behavior
- add smoke-test configs covering all declared semantic capabilities/extras
- make the wrapper produce `progress.json`

Prompt to paste in chat:

```text
Please continue the integration of `<simulator_id>` following [SIMULATOR_INTEGRATION_PLAYBOOK.md](wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md), executing Phase 2.

Requirements:
- Use [integration_decisions.md](wrappers/simulation_data_tools/simulators/<simulator_id>/integration_decisions.md) as the working contract
- Implement the wrapper under [wrappers/simulation_data_tools/simulators/<simulator_id>/](wrappers/simulation_data_tools/simulators/<simulator_id>/)
- Implement [Dockerfile](wrappers/simulation_data_tools/simulators/<simulator_id>/Dockerfile)
- Do not depend on local [repo/](wrappers/simulation_data_tools/simulators/<simulator_id>/repo/) at runtime
- Install the simulator from an official package when possible, otherwise from a pinned upstream tag/commit
- The container must read `/work/request/simulator-run-request.json`
- The container must write the normalized output tree directly under `/work/out/`
- The wrapper must execute the public simulator package/API/CLI, not a ANDREA-side reimplementation
- The wrapper must write `progress.json`
- Preserve raw upstream outputs, config snapshots, logs and session info under `provenance/raw/`
- Map `runtime_resources.threads` to the upstream thread/worker option declared
  in `simulatorspec.json`; do not reintroduce thread controls as simulator params
- Implement public truth rows exactly as documented in the selected simulator
  capability `truth_contexts`; if wrapper behavior differs, fix the wrapper
  first and then update the spec from verified behavior
- Produce:
  - `expression.tsv`
  - `truth/networks.csv`
  - `truth/gene_universe.txt`
  - optional `extras/`
  - `simulator-output-manifest.json`
- Add smoke-test configs under [wrappers/simulation_data_tools/tests/smoketest_configs/](wrappers/simulation_data_tools/tests/smoketest_configs/)
- The smoke-test matrix must cover every declared semantic capability and every
  declared extra path
- The smoke-test matrix must prove every required truth context family:
  - `global`: `context=global`
  - `group`: at least one `context=group:<group_id>`
  - `column`: at least one `context=column:<column_id>`
- The smoke-test matrix must prove public id consistency across expression,
  truth, gene universe and generated extras
- Build the image and run the smoke tests during this phase
- If implementation constraints require changing the spec, update `simulatorspec.json` and document why in `integration_decisions.md`
```

### Step 5. Execute Phase 3: Verification And Final Alignment

Goal:
- validate schemas
- run smoke tests again
- run `generate-data` compatibility tests
- leave the decision log complete and concise

Prompt to paste in chat:

```text
Please finish the integration of `<simulator_id>` following [SIMULATOR_INTEGRATION_PLAYBOOK.md](wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md), executing Phase 3.

Requirements:
- Validate [simulatorspec.json](andrea/catalog_simulation_data_tools/simulators/<simulator_id>/simulatorspec.json)
- Run the simulator smoke-test matrix
- Run the relevant `generate-data` tests
- Confirm generated `dataset-manifest.json` files pass `infer-network preflight`
- Fix any mismatch between wrapper behavior, `simulatorspec.json`, smoke configs and output schemas
- Leave [integration_decisions.md](wrappers/simulation_data_tools/simulators/<simulator_id>/integration_decisions.md) complete and concise
```

### Step 6. Build, Verify And Optionally Publish The Image

Validate the catalog and smoke-test config first:

```bash
make validate-simulatorspecs ARGS="--simulator <simulator_id>"
make validate-simulator-smoketest-configs ARGS="--simulator <simulator_id>"
```

Build or rebuild the simulator image:

```bash
make build-simulator-images ARGS="--simulator <simulator_id>"
```

Run the smoke-test matrix:

```bash
make run-simulator-smoketests ARGS="--simulator <simulator_id> --skip-build"
```

Or run the complete per-simulator verification:

```bash
make verify-simulator SIMULATOR=<simulator_id>
```

If image publication is part of the task, push:

```bash
make push-simulator-images ARGS="--simulator <simulator_id>"
```

If another machine or CI needs the image explicitly:

```bash
make pull-simulator-images ARGS="--simulator <simulator_id>"
```

## Runtime Contract

### Container Input

The core mounts:

```text
/work/request/simulator-run-request.json
/work/inputs/
/work/out/
```

The input request has this shape:

```json
{
  "schema_version": "1.0",
  "simulator_id": "<simulator_id>",
  "data_axes": {
    "measurement": "rna_expression",
    "resolution": "single_cell",
    "column_kind": "cells",
    "experimental_design": "differentiation"
  },
  "truth_requirements": {
    "contexts": ["global", "group"]
  },
  "seed": 1,
  "effective_extras": ["groups", "tf_list"],
  "mounted_inputs": {},
  "params": {},
  "runtime_resources": {"threads": 1},
  "output_dir_in_container": "/work/out"
}
```

Rules:
- `effective_extras` includes truth-required extras plus user-requested extras.
- `mounted_inputs` maps simulator input ids to read-only paths under `/work/inputs/`.
- `params` contains values already schema-validated by `generate-data`.
- `runtime_resources.threads` contains the planner-assigned thread count. The
  wrapper must map this to the public upstream parallelism control declared in
  `runtime_resources.threading.upstream_mapping`.
- The wrapper should still validate assumptions that depend on simulator internals.
- Exit nonzero on failure.

### Container Output

The wrapper must write under `/work/out/`:

```text
expression.tsv
extras/
truth/
  networks.csv
  gene_universe.txt
provenance/
  raw/
simulator-output-manifest.json
progress.json
```

Optional files:

```text
extras/groups.tsv
extras/cell_cell_interactions.tsv
extras/chromatin_accessibility.tsv
extras/chromatin_regions.tsv
extras/column_descriptors.tsv
extras/column_phenotypes.tsv
extras/cluster_identities.tsv
extras/enrichment_background.txt
extras/interventions.tsv
extras/lineage_tree.tsv
extras/perturbation_design.tsv
extras/pseudotime.tsv
extras/prior_grn.tsv
extras/prior_grn_by_group.tsv
extras/replicates.tsv
extras/spatial_coordinates.tsv
extras/tf_list.txt
extras/timepoints.tsv
```

The core validates only the normalized contract. It must not parse simulator-native files directly.
Simulators may preserve native outputs under provenance, but public consumers must not depend on those native files.

### Progress

Every wrapper should emit `progress.json` with at least:

```json
{
  "schema_version": "1.0",
  "status": "running",
  "phase": "run_simulator",
  "updated_at": "2026-04-21 12:00:00 UTC",
  "message": "Running upstream simulator."
}
```

Recommended steps:
- `validate_request`
- `prepare_run`
- `run_simulator`
- `derive_extras`
- `derive_truth`
- `write_manifest`
- `done`
- `failed`

On failure, write `status = "failed"` when possible before exiting nonzero.

## SimulatorSpec Field Evidence Guide

When drafting `simulatorspec.json`, use this field-by-field evidence policy.

### Fixed By Project Contract

- `schema_version`
  - value: `1.0`
  - evidence: `andrea/catalog_simulation_data_tools/schemas/simulatorspec.schema.json`
- `id`
  - value: `<simulator_id>`
  - evidence: folder name under `andrea/catalog_simulation_data_tools/simulators/`
- `docker_image`
  - value: `adriansegura99/simulator_<simulator_id>:1.0.0`
  - evidence: ANDREA naming convention
  - record the decision in `integration_decisions.md`

### Identity And Provenance

- `name`
  - look in:
    - paper title
    - package docs
    - repo README
  - use official spelling and capitalization
- `publication`
  - look in:
    - primary paper
    - package citation
    - repo citation section
  - store as list
  - primary simulator paper first
  - DOI references must be full URLs
- `first_author`
  - use full name from the primary paper
- `year`
  - publication year of `publication[0]`
- `simulation_summary`
  - summarize the simulator model, not the wrapper
- `simulation_keywords`
  - use reusable lower_snake_case terms, for example:
    - `single_cell`
    - `trajectory`
    - `bulk`
    - `perturbation`
    - `dynamic_grn`
- `implementation_url`
  - canonical public source matching what the Dockerfile installs

### Simulator Inputs

- `extra_inputs.required`
- `extra_inputs.optional`
- `extra_inputs.conditional_required`

Look in:
- function signatures
- CLI docs
- config examples
- README examples
- paper methods
- code paths that fail when files are missing

Rules:
- if the simulator can run from built-in templates, do not declare unnecessary required inputs
- if a file is needed only for specific params, semantic axes, requested extras or native outputs, model it in `conditional_required`
- create or reuse `andrea/catalog_simulation_data_tools/input_specs/<input_id>.json` for file semantics, format, columns, accepted extensions, validation notes and examples
- keep `extra_inputs` entries focused on simulator-specific `usage`, `conditions` and `message`
- do not define inline `formats`, `example` or column contracts inside `simulatorspec.json`
- do not confuse generated extras with simulator-side inputs
- when a parameter combination is accepted by ANDREA's schema but upstream ignores it, changes its meaning, or fails for data-dependent reasons, record that as `compatibility_rules` with `action: "block"` or `action: "warn"` instead of relying on wrapper failures
- if compatibility depends on an input file's gene universe, use `value_from: "input.<input_id>.unique_gene_count"` and ensure the relevant input spec defines target/regulator semantics clearly

Examples:
- custom regulatory network input
- differentiation tree
- perturbation design
- kinetic parameter file
- real expression reference file

### Semantic Capabilities

For each supported combination of semantic axes and truth requirements,
determine:
- whether the simulator can generate the requested measurement/resolution,
  column kind and experimental design
- which extras are native
- which extras are derivable
- which truth outputs are native or derivable

Evidence sources:
- paper simulation scenarios
- repo examples/vignettes
- package function docs
- upstream output structures

Rules:
- decide from simulator semantics, not only matrix shape
- grouped truth requires expression plus a defensible group assignment over
  expression columns
- column truth requires a `truth_outputs[]` entry with `context="column"` and
  `status` equal to `native` or `derivable`; it must be exported through
  `truth/networks.csv` with `context=column:<column_id>`, not through a separate
  public column-network file
- `experimental_design=time_series` requires temporal semantics, not just
  ordered samples
- `experimental_design=perturbational` requires perturbation/intervention
  semantics
- do not claim `lineage_tree` unless the simulator has trajectory/tree/state-transition information or the wrapper can derive it defensibly
- do not claim `prior_grn_by_group` unless there is a defensible modality or simulator state from which to derive it

### Truth Outputs

For each semantic capability, set:
- `global`
- `group`
- `column`

Rules:
- `native`: directly available from simulator internals or declared inputs
- `derivable`: computed by wrapper from simulator-native state
- `none`: unavailable or not defensible

Document:
- edge direction convention
- edge sign convention
- score meaning
- thresholding rule, if any
- context convention (`global` and, when supported, `group:<group_id>`)
- context convention for column-specific truth (`column:<column_id>`) and how
  those column ids map to expression columns
- whether grouped truth is capability-native, derived from simulator-native
  state, or unavailable
- whether column truth is simulator-native, wrapper-derived from simulator state,
  or unavailable
- whether group truth is a direct simulator output or a wrapper-derived
  aggregation of native column truth

### Parameters

Expose the full public parameter surface that is serializable and testable.

Look in:
- package function signatures
- man pages / CRAN docs
- README examples
- vignettes
- code defaults

Rules:
- prefer upstream parameter names unless ANDREA has a strong reason to normalize
- preserve upstream defaults when they are stable
- if a default is data-dependent, document the exact rule
- use nested `object` params to mirror upstream grouped params
- expose function-valued hooks only as explicit presets when practical
- do not include arbitrary code strings as parameters
- every declared parameter must be accepted by the wrapper
- if a parameter controls a semantic axis already chosen in the scenario, for
  example `experimental_design=steady_state` vs `differentiation`, add a
  capability-level `parameter_bindings[]` entry so planning and the GUI enforce
  the scenario contract
- use `policy="locked"` when the value must not be changed by the user for that
  capability
- use `policy="default_if_unset"` when the capability needs a safer default but
  the user may still choose another compatible value
- do not rely on the global param default when it contradicts one supported
  capability; bind that capability explicitly

### Auxiliary Artifacts

Use `artifacts_aux` for non-public but useful native outputs:
- raw simulator model objects
- milestone/column-state metadata
- reaction logs
- velocity matrices
- column-specific GRNs
- upstream config snapshots
- session info

These artifacts usually belong in `provenance/raw/`, not as first-class benchmark outputs.

## Output Mapping Rules

### `expression.tsv`

Use tab-separated format:

```text
gene	cell_1	cell_2	...
G1	0	3	...
```

Rules:
- first column is gene id
- remaining columns are samples/cells/timepoints/perturbations
- column semantics must match `data_axes.column_kind`
- preserve simulator count/abundance scale unless a documented upstream export requires transformation
- do not rename genes opportunistically. If the upstream simulator emits
  numeric ids or prefixed ids, choose one public convention at the earliest
  normalized export point and apply it consistently to every normalized file
  and truth context

### `groups.tsv`

Use:

```text
column	cluster
cell_1	group_a
cell_2	group_b
```

Rules:
- exactly one group assignment per expression column
- first-column values must match expression column names
- group labels should be stable and human-readable

### `column_descriptors.tsv`

Use:

```text
column	batch	condition
cell_1	batch_a	control
cell_2	batch_a	stimulated
```

Rules:
- exactly one descriptor row per expression column
- first-column values must match expression column names
- descriptor columns should be serializable categorical or scalar metadata

### `column_phenotypes.tsv`

Use:

```text
column	phenotype	order
cell_1	state_a	0
cell_2	state_b	1
```

Rules:
- exactly one phenotype assignment per expression column
- first-column values must match expression column names
- `order` must be an integer and must follow a documented simulator-derived state ordering

### `cluster_identities.tsv`

Use:

```text
cluster	annotation	order
group_a	progenitor	0
group_b	branch_1	1
```

Rules:
- `cluster` values must exist in `groups.tsv`
- `annotation` should be stable and human-readable
- `order` is optional by input spec but should be emitted when the simulator has an ordered state or trajectory model

### `pseudotime.tsv`

Use:

```text
column	pseudotime
cell_1	0.0
cell_2	0.7
```

Rules:
- exactly one pseudotime value per expression column
- first-column values must match expression column names
- document whether pseudotime is native or derived, including branch handling and scaling

### `timepoints.tsv`

Use:

```text
column	timepoint
sample_1	0
sample_2	24
```

Rules:
- exactly one row per expression column
- `timepoint` is a numeric observed sampling coordinate, not a derived pseudotime

### `perturbation_design.tsv`

Use:

```text
column	condition	perturbation	target	dose	timepoint	replicate	control
cell_1	control	none		0	0	r1	true
cell_2	knockdown_G1	knockdown	G1	1	24	r1	false
```

Rules:
- exactly one row per expression column
- `condition` is required; additional columns are optional when the simulator exposes them
- target genes should use the same public IDs as `expression.tsv` and `truth/gene_universe.txt`

### `interventions.tsv`

Use:

```text
intervention	target	effect	sign	dose
knockdown_G1	G1	knockdown	-1	1.0
overexpress_G2	G2	overexpression	1	1.0
```

Rules:
- one row per intervention definition
- `target` must use public expression gene IDs
- `effect` should preserve the simulator-native intervention type when available

### `replicates.tsv`

Use:

```text
column	replicate	batch
sample_1	r1	batch_a
sample_2	r2	batch_a
```

Rules:
- exactly one row per expression column
- use for biological or technical replicate labels that are not already captured by `groups.tsv`

### `lineage_tree.tsv`

Use:

```text
child	parent	gain_rate	loss_rate
group_b	group_a	0.1	0.2
```

Rules:
- `child` and `parent` must exist in `groups.tsv`
- root groups have no row
- `gain_rate` and `loss_rate` must be in `[0, 1]`
- document how rates are derived

### `tf_list.txt`

Use one TF id per line.

Rules:
- ids must be a subset of expression genes
- if the simulator does not distinguish TFs natively, document the derivation rule
- ids must use the same public gene-id convention as `expression.tsv`

### `enrichment_background.txt`

Use one gene id per line.

Rules:
- ids should be a subset of expression genes
- document whether the background is all simulated genes, assay-specific detected genes, or an external universe
- ids must use the same public gene-id convention as `expression.tsv`

### `prior_grn.tsv`

Use:

```text
source	target	score
TF1	G2	0.7
TF2	G3	-0.4
```

Rules:
- `source` and `target` must be expression genes
- document whether `score` is unsigned confidence, signed effect, or another method-specific value
- document whether the prior is modality-grounded, truth-derived, or heuristic
- avoid deriving a prior directly from clean truth unless the benchmark explicitly intends that
- `source` and `target` must use the same public gene-id convention as
  `expression.tsv`

### `prior_grn_by_group.tsv`

Use:

```text
group	source	target	score
group_a	TF1	G2	0.7
```

Rules:
- `group` must exist in `groups.tsv`
- `source` and `target` must be expression genes
- document whether `score` is unsigned confidence, signed effect, or another method-specific value
- document whether the prior is modality-grounded, truth-derived, or heuristic
- avoid deriving a prior directly from clean truth unless the benchmark explicitly intends that
- `source` and `target` must use the same public gene-id convention as
  `expression.tsv`

### `truth/networks.csv`

Use:

```text
source,target,score,sign,evidence,context
TF1,G2,1,+,simulated_truth,global
TF1,G3,0.7,-,simulated_truth,group:group_a
TF2,G4,0.4,+,simulated_truth,column:cell_001
```

Rules:
- do not include self-loops unless upstream semantics require them
- omit exact zero-score edges
- preserve direction if the simulator has directed regulatory semantics
- `score` must be a positive truth label, confidence or effect magnitude; sign belongs in `sign`
- `context=global` for dataset-level truth
- `context=group:<group_id>` for group-specific truth
- `context=column:<column_id>` for expression-column-specific truth. The
  biological meaning of the column id comes from `data_axes.column_kind`.
- every group context must correspond to a group in `groups.tsv`
- every column context must correspond to an expression column identifier
- `source` and `target` must use the same public gene-id convention as
  `expression.tsv`
- when deriving group truth from column-specific simulator state, document the
  fixed aggregation rule and whether missing column-edge values contribute zero
- when exporting native column truth, do not apply group-level thresholds unless
  they are also native to the column-level simulator output
- dense column-specific truth can be very large; keep smoke-test matrices small
  but representative, and preserve full native objects under provenance/raw when
  useful for audit
- include this file in `ground-truth-manifest.json` as `outputs.networks`
- include this file in `simulator-output-manifest.json` as `truth.networks`

## Smoke-Test Requirements

Every completed simulator must have one or more configs under:

```text
wrappers/simulation_data_tools/tests/smoketest_configs/<simulator_id>*.json
```

The matrix must cover:
- every declared semantic capability
- every declared extra at least once
- representative parameter groups
- conditional-input behavior when supported
- the presence of `progress.json`
- required public output files
- public id consistency across expression, truth, gene universe and extras
- important provenance files

For `dyngen`, this currently means representative single-cell trajectory or
differentiation capabilities covering:
- `global` truth
- `global + group` truth plus `groups`
- `global + group + column` truth
- optional `tf_list`, `lineage_tree`, RNA velocity and other declared extras

Run:

```bash
make verify-simulator SIMULATOR=<simulator_id>
```

## Cost Benchmark Requirements

Planner-facing simulator cost profiles are generated by:

```bash
make benchmark-simulator-costs ARGS="--simulator <simulator_id>"
make validate-simulator-costs ARGS="--simulator <simulator_id> --require"
```

Each benchmarked Docker run has a timeout guard. The default is 1800 seconds
per run; override it with `--timeout <seconds>` or use `--timeout 0` only when
you intentionally want no timeout.

```bash
make benchmark-simulator-costs ARGS="--simulator <simulator_id> --timeout 3600"
```

Timeouts are stored in `cost.json` as `status=timeout` when no repeat succeeds,
or inside `failure_breakdown.timeout` when a benchmark point is only partially
successful. The generate-data planner does not use fully timed-out points as
runtime evidence. Partially successful points remain usable, but their ETA
receives a conservative risk penalty and carries a provenance warning.

Each integrated simulator should have a bounded cost benchmark config under:

```text
wrappers/simulation_data_tools/cost_profiles/<simulator_id>.json
```

The cost profile matrix should vary the dimensions and settings that materially
change runtime:

- semantic capability
- genes and cells
- group/population count where applicable
- requested extras
- conditional simulator input source modes
- runtime-affecting parameter presets
- assigned `runtime_resources.threads`

Do not benchmark impossible combinations. If a semantic capability activates an
`extra_inputs.conditional_required` rule, the cost profile config must provide
that simulator-side input file.

## Generate-Data Resource And ETA Contract

`generate-data` plans are strict executable contracts. A valid
`simulation-plan.json` must include:

- per-run `runtime_resources`, `ram_gb`, `eta_seconds`, `eta_source`,
  `eta_start_seconds`, `eta_end_seconds` and `eta_provenance`
- per-task `runtime_resources`, `ram_gb`, `eta_seconds`, `eta_source`,
  `eta_start_seconds`, `eta_end_seconds`, `eta_wave` and `eta_provenance`
- `execution.max_parallel_tasks`, `execution.max_cores`,
  `execution.max_ram_gb`, `execution.eta_total_seconds`,
  `execution.waves` and `execution.warnings`

`runtime_resources.threads` is the only wrapper-facing thread assignment. The
planner may set it from cost profiles or from the simulator's declared default.
Wrappers must not read user-facing simulator params for thread counts.

When a simulator has no catalog `cost.json`, the planner must still emit a valid
plan using conservative fallback ETA values and explicit warnings. When a
`cost.json` exists, the selected runtime point and any approximation details
must be preserved in `eta_provenance`.

Typical planning commands:

```bash
andrea generate-data plan \
  --scenario scenario-request.json \
  --simulator-runs simulator-runs.json \
  --out simulation-plan.json \
  --max-parallel-tasks 2 \
  --max-cores 8 \
  --max-ram-gb 32

andrea generate-data execute \
  --scenario scenario-request.json \
  --simulator-runs simulator-runs.json \
  --output-dir benchmarks \
  --max-parallel-tasks 2 \
  --max-cores 8 \
  --max-ram-gb 32
```

## Required Structure Of `integration_decisions.md`

The decision log must include:
- upstream evidence reviewed
- selected installation route and version/tag/commit
- selected public API/CLI entrypoint
- supported semantic capabilities and why
- unsupported semantic capabilities and why
- native and derivable extras
- per-capability `truth_contexts`, including native upstream outputs,
  wrapper-derived outputs, upstream parameter switches, score/sign semantics and
  limitations
- simulator-side input specs and `extra_inputs` usage rules
- parameter mapping and unsupported function-valued hooks
- output mapping
- progress strategy
- smoke-test matrix and outcome
- cost profile status and whether ETA uses catalog costs or fallback estimates
- resource mapping from request `runtime_resources.threads` through the spec
  `runtime_resources.threading` contract to the upstream API

For every non-trivial decision, record:
- chosen value
- evidence path(s)
- rationale
- uncertainty if any

If a value is unclear:
- say that it is unclear
- list conflicting evidence
- explain the chosen temporary resolution

## Quick Command Summary

From repository root:

```bash
make validate-generation-catalog
make validate-simulatorspecs ARGS="--simulator <simulator_id>"
make validate-simulator-smoketest-configs ARGS="--simulator <simulator_id>"
make build-simulator-images ARGS="--simulator <simulator_id>"
make run-simulator-smoketests ARGS="--simulator <simulator_id> --skip-build"
make verify-simulator SIMULATOR=<simulator_id>
make benchmark-simulator-costs ARGS="--simulator <simulator_id>"
make validate-simulator-costs ARGS="--simulator <simulator_id> --require"
make list-simulator-images
make scaffold-simulator SIMULATOR=<simulator_id> WRAPPER=python
make prepare-simulator-papers SIMULATOR=<simulator_id>
python -m pytest tests/wrappers/simulation_data_tools/test_simulatorspecs.py -q
python -m pytest tests/core/commands/generate_data/test_generate_data.py -q
python -m pytest tests/gui/test_generate_data_server.py tests/cli/test_generate_data_cli.py -q
```

For R wrappers:

```bash
Rscript -e "invisible(parse(file='wrappers/simulation_data_tools/simulators/<simulator_id>/run_simulator.R'))"
```

## Review Checklist

Before considering a simulator integrated, confirm:
- local paper text/PDFs and repo/docs were reviewed
- `simulatorspec.json` validates
- `publication` is a list of full URLs
- `first_author` is a full name
- `docker_image` follows `adriansegura99/simulator_<simulator_id>:1.0.0`
- the Dockerfile installs from a stable public source
- runtime does not depend on the local evidence repo
- every declared semantic capability is covered by smoke tests
- every declared extra is covered by smoke tests
- every declared capability has audited `truth_contexts`
- column-specific truth, when claimed, emits `global`, requested
  `group:<group_id>` and `column:<column_id>` truth rows
- every declared parameter is accepted by the wrapper
- `progress.json` is written
- `simulator-output-manifest.json` validates
- generated `dataset-manifest.json` passes `infer-network preflight`
- generated `ground-truth-manifest.json` is sufficient for an
  `evaluate-inference` analysis handoff bundle
- truth outputs are consistent
- provenance contains enough raw simulator state to debug or audit the run
- no incomplete simulator appears in `andrea/catalog_simulation_data_tools/simulators/`
