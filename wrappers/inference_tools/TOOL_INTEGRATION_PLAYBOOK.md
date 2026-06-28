# Tool Integration Playbook

This playbook defines the recommended hybrid workflow for integrating a new inference tool into `andrea infer-network`.

Use it when you have local evidence under:

```text
wrappers/inference_tools/tools/<tool_id>/
  repo/
  papers/
```

`repo/` and `papers/` are evidence sources for analysis only. They must not become runtime dependencies.
For new integrations, assume `papers/` contains one or more PDF files. Convert them to text before semantic analysis.
Runtime behavior must be reproducible from:
- `andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json`
- the wrapper under `wrappers/inference_tools/tools/<tool_id>/`
- the `Dockerfile` under `wrappers/inference_tools/tools/<tool_id>/`

## Scope

The goal of an integration is to produce:
- a coherent `toolspec.json`
- reuse of existing normalized inputs when semantics match
- a new `input_spec` only when the semantic content does not match current inputs
- a wrapper that maps upstream behavior to the ANDREA runtime contract
- a `Dockerfile` that installs the tool from a stable public source
- smoketests and fixtures
- an `integration_decisions.md` file documenting the decisions taken

## Source-of-truth rules

1. Prefer the upstream implementation and paper over assumptions.
2. Keep `toolspec.json` aligned with the real upstream interface.
3. Reuse an existing normalized input only if the semantic content matches.
4. Create a new normalized input only when the current catalog cannot express the same information content.
5. Do not depend on the local `repo/` folder at runtime.
6. Installation preference:
   - package manager / official library first
   - upstream public repo pinned to tag/commit if no package exists
   - never depend on an unpinned floating source if avoidable
7. Audit all relevant upstream public modes before choosing the wrapper contract.
8. Treat execution capabilities as dataset-routing semantics, not as algorithm-variant names.
9. Preserve public identifiers exactly in public outputs. Expression gene ids,
   expression column ids and group ids must not be renamed, prefixed or normalized in
   `network.csv` unless every public artifact is mapped back consistently and
   the mapping is recorded as an auxiliary artifact.
10. Runtime resource controls and runtime parallelism must be declared under
    `runtime_resources.threading`, not as user-facing tool params. The audit
    must look beyond obvious `threads` parameters: consider `cores`, `workers`,
    `n_jobs`, process pools, schedulers, OpenMP/BLAS/MKL/Torch controls,
    foreach/joblib/Dask-style backends, and upstream-documented sharding over
    independent work units such as targets, genes, bootstraps, folds,
    subsamples or partitions. The wrapper must map ANDREA `--threads` to the
    declared upstream control or documented execution pattern.
11. Keep ANDREA-owned orchestration out of wrappers. The wrapper produces raw
    method outputs; `infer-network` core owns downstream score normalization,
    `group_emulated` orchestration, `group_aggregated` aggregation and ZIP
    bundles such as `analysis`, `report`, `graphs` and `full`. This does not
    forbid wrapper-owned parallel execution that is part of the selected
    upstream runtime contract for one logical run, such as upstream-documented
    sharding over independent work units; in that case the wrapper must preserve
    per-shard raw outputs, logs and configs, then merge deterministically into
    the single public `network.csv` contract.

## Official End-to-End Procedure

Follow these steps in order. A tool is not considered fully integrated until you reach the final image/cost steps.

### Step 0. Choose `<tool_id>` and wrapper language

Manual action:
- choose a stable lowercase id using letters, digits, `_` or `-`
- choose the initial wrapper language label (`python`, `r`, `matlab`, `julia`, `java`, ...)
- if the upstream repo is a multi-tool library, decide which method/package/function/CLI entrypoint is the actual target
- if installation details are unclear in the repo, decide what clarification you will provide later in the prompt

This id will be reused in:
- [tool source dir](tools/<tool_id>/)
- [toolspec.json](../../andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json)
- [smoketest config](tests/smoketest_configs/<tool_id>.json)

### Step 1. Create the scaffold

Command:

```bash
make scaffold-tool TOOL=<tool_id> WRAPPER=python
```

Variants:
- use `WRAPPER=r` if the wrapper should be R-based
- use another label such as `WRAPPER=matlab`, `WRAPPER=julia` or `WRAPPER=java` for a generic scaffold
- if the wrapper file extension differs from the language label, pass it through `ARGS`, for example:

```bash
make scaffold-tool TOOL=<tool_id> WRAPPER=matlab ARGS="--wrapper-ext m"
```

Expected files after this step:
- [tool source dir](tools/<tool_id>/)
- [integration_decisions.md](tools/<tool_id>/integration_decisions.md)
- [Dockerfile](tools/<tool_id>/Dockerfile)
- [wrapper stub](tools/<tool_id>/run_tool.py)
- [toolspec.json](../../andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json)
- [smoketest config](tests/smoketest_configs/<tool_id>.json)

### Step 2. Place the upstream evidence

Manual action:
- copy or clone the upstream implementation into [repo/](tools/<tool_id>/repo/)
- copy one or more local PDF papers into [papers/](tools/<tool_id>/papers/)

Minimum evidence expected in [repo/](tools/<tool_id>/repo/):
- README or package docs
- examples
- config files or CLI help
- the relevant function/module/script if the repo contains multiple tools
- output examples if available

Minimum evidence expected in [papers/](tools/<tool_id>/papers/):
- primary method paper PDF
- implementation/package paper PDF if different
- any additional paper needed to justify special inputs, assumptions or outputs

### Step 3. Extract local PDFs

Command:

```bash
make prepare-tool-papers TOOL=<tool_id>
```

Rules:
- the generated `.txt` files are analysis helpers only
- the PDFs remain the primary evidence source
- if extraction quality is poor, record that in [integration_decisions.md](tools/<tool_id>/integration_decisions.md)

### Step 4. Sanity-check the local layout

Manual action:
- confirm that these paths exist before starting Phase 1:
  - [repo/](tools/<tool_id>/repo/)
  - [papers/](tools/<tool_id>/papers/)
  - [integration_decisions.md](tools/<tool_id>/integration_decisions.md)
  - [toolspec.json](../../andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json)
- confirm that at least one extracted paper text file exists if PDFs were provided

### Step 5. Execute Phase 1: evidence and contract

Goal:
- produce/update [integration_decisions.md](tools/<tool_id>/integration_decisions.md)
- draft [toolspec.json](../../andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json)
- propose a new normalized input only if the current catalog cannot express the same semantic content

Prompt to paste in chat:

```text
Please integrate the new tool `<tool_id>` following [TOOL_INTEGRATION_PLAYBOOK.md](wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md), but only execute Phase 1 for now.

Context:
- Upstream repo is under [repo/](wrappers/inference_tools/tools/<tool_id>/repo/)
- Local papers are under [papers/](wrappers/inference_tools/tools/<tool_id>/papers/)
- Existing scaffold files already exist
- The working decision log is [integration_decisions.md](wrappers/inference_tools/tools/<tool_id>/integration_decisions.md)
- The ToolSpec draft location is [toolspec.json](andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json)
- Optional manual clarifications from the integrator:
  - target method inside a multi-tool library/repo: `<optional>`
  - relevant module / package / script / function / CLI entrypoint: `<optional>`
  - preferred installation source if upstream docs are unclear: `<optional>`
  - preferred version / tag / commit if needed: `<optional>`

Requirements:
- Review the upstream repo and the local papers
- Audit all relevant upstream public execution modes/entrypoints before choosing the wrapper contract
- Produce or update [integration_decisions.md](wrappers/inference_tools/tools/<tool_id>/integration_decisions.md)
- Draft [toolspec.json](andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json)
- Reuse existing normalized inputs when their semantic content matches
- If current normalized inputs do not fit semantically, propose and implement a new input spec under [andrea/catalog_inference_tools/input_specs/](andrea/catalog_inference_tools/input_specs/)
- Do not implement the wrapper yet
- In [toolspec.json](andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json), store publication DOIs as full canonical URLs (`https://doi.org/...`) and store `first_author` as the full author name
- Populate `year`, `method_summary` and `method_keywords` with explicit evidence from the primary paper/repo, not with wrapper-level wording
- Explicitly decide and document which upstream public entrypoint the integration mirrors
- Explicitly document any upstream public modes/entrypoints that are not exposed by the wrapper and why
- Identify runtime resource support. Do not stop at searching for a literal
  `threads` argument: inspect docs, examples, CLI help and source for worker,
  process, scheduler, OpenMP/BLAS/MKL/Torch, foreach/joblib/Dask, environment
  variable, and documented independent-work-unit sharding controls. Document
  how ANDREA will map assigned `--threads` to the selected mechanism, or why no
  safe mapping exists.
- If any upstream default is data-dependent or runtime-dependent, document the exact rule and how the ToolSpec preserves it
- Determine whether the wrapper should preserve raw method score magnitudes directly or whether the chosen upstream public interface already defines the score scale; if the upstream score is a signed coefficient, document how the wrapper separates `abs(coefficient)` into `score` and coefficient direction into `sign`
- For every non-trivial field in [toolspec.json](andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json), record:
  - chosen value
  - evidence path(s)
  - rationale
  - uncertainty if any
- Use the field-by-field evidence policy from [TOOL_INTEGRATION_PLAYBOOK.md](wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md)
- If optional manual clarifications are provided, treat them as authoritative context for locating the target implementation and installation route unless stronger primary evidence clearly disproves them

Focus especially on:
1. upstream execution modes/entrypoints and whether each maps to `global`, `group_native`, `group_emulated`, `column_native`, `group_aggregated`, a parameter choice, or is intentionally excluded
2. input semantics and conditional required inputs by execution mode and by parameter value
3. parameter mapping and defaults
4. runtime parallelism support and whether `runtime_resources.threading` should
   be `supported=true` or `supported=false`, including whether parallelism is
   native in-process, backend/environment-controlled, or documented
   process-level sharding over independent work units
5. output semantics and how they should map to raw `network.csv` with positive `score` magnitudes and direction stored only in `sign`
6. installation source preference: package first, pinned upstream source second; inspect the installable package/version when it differs from the local repo snapshot
7. preserving public expression/cell/group ids in wrapper outputs, including any
   upstream alias map needed to round-trip internal ids back to ANDREA ids
8. whether any apparent parameter is actually a fixed implementation choice
   that should be documented instead of exposed as a single-value user param
9. explicit evidence for `accepts`, `assumes`, `extra_inputs`, `outputs`,
   `progress`, `runtime_resources`, `params` and `artifacts_aux`
```

### Step 6. Review the Phase 1 outputs

Manual action:
- inspect [integration_decisions.md](tools/<tool_id>/integration_decisions.md)
- inspect [toolspec.json](../../andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json)
- check that all relevant upstream public modes/entrypoints were audited, including excluded ones
- check that proposed normalized inputs are semantically correct, not just file-shape compatible
- check that execution capabilities and conditional input rules are represented in the ToolSpec rather than only in GUI/core behavior
- decide whether the proposed contract is acceptable before any wrapper code is written

Do not continue to Phase 2 until the contract looks correct.

### Step 7. Execute Phase 2: implementation

Goal:
- implement the wrapper and Dockerfile
- keep [toolspec.json](../../andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json) aligned with the real implementation
- add or update smoketest fixtures/config

Prompt to paste in chat:

```text
Please continue the integration of `<tool_id>` following [TOOL_INTEGRATION_PLAYBOOK.md](wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md), executing Phase 2.

Requirements:
- Use [integration_decisions.md](wrappers/inference_tools/tools/<tool_id>/integration_decisions.md) as the working contract
- Implement the wrapper under [tools/<tool_id>/](wrappers/inference_tools/tools/<tool_id>/) and the corresponding [Dockerfile](wrappers/inference_tools/tools/<tool_id>/Dockerfile)
- Do not depend on the local [repo/](wrappers/inference_tools/tools/<tool_id>/repo/) folder at runtime
- Install the method from an official package when possible, otherwise from a pinned upstream repo/tag/commit
- Install runtime dependencies with the package manager of the same interpreter/runtime that will execute the wrapper
- If the runtime build pipeline requires [template_map.json](wrappers/inference_tools/scripts/template_map.json), register `<tool_id>` there with the correct runtime and template bundles
- Preserve data-dependent or runtime-dependent upstream defaults; if the ToolSpec uses a sentinel such as `null` to mean "defer to upstream default", implement that by omitting the argument rather than hard-coding a replacement value
- Map the wrapper `--threads` runtime argument to the upstream thread/worker/process/runtime control or documented execution pattern declared in `toolspec.runtime_resources.threading.upstream_mapping`; if the mapping is process-level sharding over independent work units, split work into at most `--threads` public upstream invocations, preserve per-shard raw outputs/configs/logs, and merge deterministically; do not reintroduce thread controls as user-facing tool params
- Make the wrapper produce raw positive `network.csv` score magnitudes for the chosen upstream interface and `progress.json`
- If the wrapper completes with a valid `network.csv` but has method-level caveats, degraded fallbacks, or best-effort behavior, write them as a `warnings` list in the final `progress.json`; ANDREA will surface the run as `completed_with_warnings`.
- Do not apply ANDREA-specific score normalization in the wrapper; downstream normalization is handled later by [merge.py](andrea/core/commands/infer_network/commons/merge.py)
- Do not write rows with `score <= 0` to `network.csv`; if the upstream method produces a dense matrix, filter zero-magnitude edges in the wrapper before export
- Preserve expression gene ids, expression column ids and group ids exactly in `network.csv`.
  If the upstream runtime requires internal aliases, write an auxiliary alias
  map and convert all public wrapper outputs back to the original ANDREA ids.
- For undirected methods, export one row per unordered pair and exclude self-loops unless stronger primary evidence clearly requires another edge convention
- Add or update [smoketest config](wrappers/inference_tools/tests/smoketest_configs/<tool_id>.json) and any needed fixtures under [tests/fixtures/](wrappers/inference_tools/tests/fixtures/)
- Cover every declared execution capability in smoketests when feasible,
  including small representative checks for `column_native` and
  `group_aggregated` contracts.
- Build the image and run the smoketest during this phase; if it fails, fix the implementation and repeat until it passes
- Keep [toolspec.json](andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json) aligned with the implemented behavior
- Update [integration_decisions.md](wrappers/inference_tools/tools/<tool_id>/integration_decisions.md) so it reflects implemented behavior and records the smoketest outcome
- If the integrator provided optional clarifications about the target method or installation source, keep respecting them during implementation unless stronger primary evidence clearly disproves them
```

### Step 8. Execute Phase 3: verification and final alignment

Goal:
- validate schemas
- run the smoketest again as final confirmation
- leave [integration_decisions.md](tools/<tool_id>/integration_decisions.md) concise and complete

Prompt to paste in chat:

```text
Please finish the integration of `<tool_id>` following [TOOL_INTEGRATION_PLAYBOOK.md](wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md), executing Phase 3.

Requirements:
- Validate [toolspec.json](andrea/catalog_inference_tools/tools/<tool_id>/toolspec.json) and all relevant input specs under [andrea/catalog_inference_tools/input_specs/](andrea/catalog_inference_tools/input_specs/)
- Run the smoketest for `<tool_id>`
- Fix any remaining inconsistency between the wrapper, the ToolSpec, the normalized inputs and the smoketest
- Leave [integration_decisions.md](wrappers/inference_tools/tools/<tool_id>/integration_decisions.md) complete and concise
```

### Step 9. Generate the planning cost profile

Command:

```bash
make benchmark-tool-costs ARGS="--tool <tool_id>"
```

If you need a smaller or custom benchmark matrix, customize `ARGS`, for example:

```bash
make benchmark-tool-costs ARGS="--tool <tool_id> --size 50x20 --size 100x40 --threads 1,2 --ram-gb 8,16 --repeats 1"
```

Before launching a long run, inspect the resolved matrix:

```bash
make benchmark-tool-costs ARGS="--tool <tool_id> --plan-only"
```

The script combines the CLI matrix with
[cost_profiles](cost_profiles/README.md). Profile configs must cover every
declared `execution_capabilities` mode and every required/optional/conditional
input path that materially changes runtime. Use profile-local `sizes`,
`column_kind`, `expression_profile` or `gene_id_source` when the generic
synthetic matrix is invalid for the tool, for example tools whose bundled public
resources require human gene symbols.

Each benchmarked Docker run has a timeout guard. The default is 1800 seconds
per run; override it with `--timeout <seconds>` or use `--timeout 0` only when
you intentionally want no timeout.

```bash
make benchmark-tool-costs ARGS="--tool <tool_id> --timeout 3600"
```

Timeouts are stored in `cost.json` as `status=timeout` when no repeat succeeds,
or inside `failure_breakdown.timeout` when a benchmark point is only partially
successful. The planner does not use fully timed-out points as runtime evidence.
Partially successful points remain usable, but their ETA receives a conservative
risk penalty and carries a provenance warning. The benchmarker also names each
container and performs a best-effort `docker rm -f` when Docker itself times out,
so interrupted points should not leave orphaned containers.

The requested `--threads` matrix must be compatible with
`toolspec.runtime_resources.threading`. Tools with `supported=false` may only
benchmark `threads=1`; tools with `supported=true` may benchmark values up to
`max_threads`. If `supported=true` is implemented by wrapper-owned upstream
sharding, include benchmark points for the declared shard counts so the planner
does not rely on single-thread measurements for multi-thread runs.

Then validate the resulting [cost.json](../../andrea/catalog_inference_tools/tools/<tool_id>/cost.json):

```bash
make validate-tool-costs ARGS="--tool <tool_id>"
```

Note:
- [benchmark_costs.py](scripts/benchmark_costs.py) uses its own local image tag `inference-tools-<tool_id>:benchmark-local`
- do not pass `--skip-build` unless that benchmark-local image already exists

### Step 10. Validate the full catalog state

Command:

```bash
make validate-inference-catalog
```

Optional focused verification:

```bash
make verify-tool TOOL=<tool_id>
```

Optional focused smoketest-config validation:

```bash
make validate-smoketest-configs ARGS="--tool <tool_id>"
```

### Step 11. Build and optionally publish the final image

Build the final packaged image:

```bash
make build-tool-images ARGS="--tool <tool_id>"
```

Optionally push it:

```bash
make push-tool-images ARGS="--tool <tool_id>"
```

If another machine or CI needs to pull it explicitly:

```bash
make pull-tool-images ARGS="--tool <tool_id>"
```

## ToolSpec Field Evidence Guide

When drafting `toolspec.json`, use the following field-by-field evidence policy.

### Fixed by project contract

- `schema_version`
  - value: `1.0`
  - evidence: `andrea/catalog_inference_tools/schemas/toolspec.schema.json`
- `id`
  - value: `<tool_id>`
  - evidence: folder name under `wrappers/inference_tools/tools/` and `andrea/catalog_inference_tools/tools/`
- `docker_image`
  - value source: project naming convention
  - evidence: local project convention, not upstream evidence
  - note: still record the naming decision in `integration_decisions.md`

### Identity and provenance

- `name`
  - look in:
    - repo README title
    - package/project name in upstream docs
    - paper title and abstract
  - search for:
    - official spelling and capitalization of the method name
- `publication`
  - look in:
    - paper PDFs
    - repo README citation section
    - package metadata / documentation
  - search for:
    - DOI(s), primary method paper, follow-up implementation paper if relevant
  - note:
    - store DOI references as full canonical URLs: `https://doi.org/...`
    - primary method paper should appear first
- `first_author`
  - look in:
    - primary paper PDF
  - search for:
    - full name of the first author of `publication[0]`
- `year`
  - look in:
    - primary paper PDF
    - repo citation section
    - package metadata when it cites the primary paper
  - search for:
    - publication year of `publication[0]`
- `method_summary`
  - look in:
    - paper abstract
    - paper methods/introduction
    - repo README description
  - search for:
    - the core modeling idea in one or two sentences
  - rule:
    - summarize the method itself, not the wrapper implementation details
- `method_keywords`
  - look in:
    - paper title/abstract
    - repo README
    - implementation docs
  - search for:
    - 3-6 short lower_snake_case keywords capturing the method family or central ideas
  - rule:
    - prefer reusable conceptual terms such as `mutual_information`, `tree_ensemble`, `stability_selection`, `single_cell`
- `implementation_url`
  - look in:
    - official package page
    - CRAN / Bioconductor / PyPI page
    - upstream GitHub repository
  - search for:
    - canonical public source matching the implementation you will install

### Interface boundary

Before finalizing params, inputs and outputs, explicitly decide which upstream public entrypoint the integration mirrors.

- look in:
  - package docs / man pages
  - exported functions
  - CLI usage/help
  - examples in README and papers
- search for:
  - whether the method exists as:
    - a low-level algorithm primitive
    - a higher-level convenience pipeline that includes preprocessing/postprocessing
    - multiple public workflows or functions for global, grouped, multitask, pseudotime, time-lagged, or condition-specific inference
- rule:
  - audit all relevant public upstream entrypoints before choosing the wrapper contract; do not stop at the first entrypoint that can produce a network
  - choose the narrowest public upstream interface that cleanly matches ANDREA inputs
  - record the decision explicitly in `integration_decisions.md`
  - include a short mode/entrypoint matrix in `integration_decisions.md` with:
    - upstream function/CLI/workflow name
    - required inputs
    - output shape and whether it produces one network, multiple native group/task networks, or a post-filtered network
    - mapped ANDREA execution capability or parameter
    - whether it is exposed by the wrapper
    - rationale for exposing or excluding it
  - if the wrapper intentionally targets a convenience wrapper instead of the bare algorithm, document the consequence for params and output semantics
  - if the upstream package offers both a score-preserving low-level interface and a convenience wrapper that only rescales the same scores, prefer the score-preserving interface so raw `network.csv` score magnitudes remain comparable with other tools
  - if the official installable package/version differs from the local repo snapshot, inspect the installable package/version and base the runtime contract on that version

- `execution_capabilities`
  - look in:
    - upstream examples and public workflow constructors
    - whether the method natively supports one whole-dataset run, grouped/multitask runs, or neither
  - supported values:
    - `global`: the wrapper can run one network on the whole expression matrix
    - `group_native`: the upstream public interface natively consumes group/task metadata and returns group/task networks from one run
    - `group_emulated`: ANDREA can emulate grouped execution by partitioning the expression matrix and running the tool once per group/subset
    - `column_native`: the upstream public interface natively returns one network per expression column from one logical run
    - `group_aggregated`: ANDREA aggregates native per-column outputs into group-level outputs using `groups.tsv`
  - rule:
    - use `execution_capabilities` instead of the removed `execution_scope`
    - use `global` only when it is methodologically valid to infer one network from the whole expression matrix, not merely because the upstream code can technically accept any matrix
    - if the method is designed for one condition, cell type, cell state, trajectory segment, or subgroup at a time, expose `group_emulated` when ANDREA should partition the full dataset and run the method independently per group
    - use `group_native` only when the upstream public interface itself consumes group/task metadata and returns group/task networks from one run
    - use `column_native` when the upstream public interface itself estimates column-specific networks and the wrapper preserves those outputs as `column:<column_id>` contexts
    - do not emulate `column_native` by running a global/group method once per expression column unless the upstream public interface explicitly defines that as a valid column-specific mode for the dataset column semantics
    - use `group_aggregated` only for tools that also expose `column_native`; do not declare a schema-level dependency, but do define both capabilities together in the ToolSpec
    - `group_aggregated` is ANDREA-managed post-processing: the upstream method runs once in `column_native` mode, ANDREA aggregates `column:<column_id>` rows into `group:<group_id>` rows with `groups.tsv`, and the wrapper must not rerun the upstream method per group
    - `group_aggregated` uses ANDREA's fixed signed-effect mean aggregation rule and must not introduce wrapper-specific aggregation params
    - keep algorithm choices as normal params when they are variants inside one execution capability
    - examples of parameter choices, not execution capabilities, include regression model families, penalties, score filters, feature-selection strategy, and post-processing mode when they do not change how ANDREA partitions or routes the dataset
    - record whether grouped output context is produced by the wrapper (`group_native`), by orchestrated subruns (`group_emulated`), or by ANDREA aggregation of per-column output (`group_aggregated`)
    - if `group_emulated` is exposed alongside other execution modes and `groups` is only used for that mode, declare `groups` only in `extra_inputs.conditional_required`; do not also mark it optional unless providing it outside the required condition changes the upstream inference
    - if `group_emulated` is the only exposed execution mode, declaring `groups` in `extra_inputs.required` is acceptable and usually clearer
    - if `group_aggregated` is exposed, declare `groups` in `extra_inputs.conditional_required` with `execution: "mode"` and `value: "group_aggregated"`
    - do not require `groups` just because `column_native` is exposed
    - warn in `integration_decisions.md` when `column_native` output can be dense or very large, and make the smoketest verify a small representative subset rather than assuming all possible column-edge rows are manageable
    - document excluded upstream modes/entrypoints explicitly; the reason may be unsupported normalized inputs, incompatible output semantics, deprecated API, unavailable runtime dependency, or a deliberate scope decision

### Dynamic defaults

If an upstream default depends on the dataset or runtime state, do not silently replace it with an arbitrary constant.

- look in:
  - function signatures
  - source code defaults
  - docs/examples
- search for:
  - expressions such as `sqrt(NROW(dataset))`, `ncol(X)`, `auto`, `None`, inferred thread counts, or data-dependent heuristics
- rule:
  - record the exact upstream default rule in `integration_decisions.md`
  - decide how the ToolSpec will preserve that behavior
  - if a sentinel such as `null` is used in the ToolSpec to mean "defer to upstream default", document that explicitly

### Dataset compatibility

- `accepts`
  - look in:
    - repo input examples
    - README usage examples
    - CLI/config format
    - paper methods and datasets
  - search for:
    - what each expression-matrix column semantically represents:
      - `samples`
      - `cells`
      - `timepoints`
      - `perturbations`
  - rule:
    - decide from method semantics, not only file shape
- `assumes`
- `taxonomic_scope`
- `compatibility_rules`
  - look in:
    - paper abstract, introduction and methods
    - repo README
    - preprocessing assumptions in code/examples
  - search for:
    - whether the method is specifically for scRNA, bulk, or genuinely generic
  - rule:
    - use `scrna_specific` only when the method materially depends on single-cell structure
    - use `bulk_specific` only when it is explicitly designed for bulk/cohort data
    - otherwise use `generic`
    - declare `taxonomic_scope.allowed_groups` for every tool; use all catalog groups only when there is no primary evidence for a taxonomic restriction
    - declare `taxonomic_scope.supported_species` as NCBI taxonomy IDs when species-level resources, motif databases, or aliases are explicitly limited
    - use `compatibility_rules` for compatibility that depends on dataset organism, resolved params, and/or `execution.mode`
    - `compatibility_rules.conditions` are AND-combined; use `action: "block"` for impossible/invalid combinations and `action: "warn"` for supported but degraded combinations
    - if a parameter disables the restricted feature, encode the allowed degraded path as a warning rule instead of globally blocking the whole tool

### Extra inputs

- `extra_inputs.required`
- `extra_inputs.optional`
- `extra_inputs.conditional_required`
  - look in:
    - CLI flags / argument parser
    - example config files
    - README parameter docs
    - code paths that fail when files are missing
    - paper methods if a prior/annotation is part of the method
  - search for:
    - files beyond expression matrix
    - whether they are always required, mode-dependent, or optional
  - rule:
    - encode `extra_inputs.required` and `extra_inputs.optional` as objects with `input` and `usage`; strings are invalid
    - include `usage` on every `conditional_required` rule so the GUI can explain why the file is needed for that tool and condition
    - create an input requirement matrix in `integration_decisions.md` before finalizing the ToolSpec:
      - always required inputs
      - inputs required only for an `execution.mode`
      - inputs required only for a parameter value
      - genuinely optional inputs
      - upstream inputs intentionally not exposed
    - if a file is needed only when certain parameter values are used, model it in `conditional_required`
    - if a file is needed only for a selected execution mode, model it in `conditional_required` with `execution: "mode"` and a value from `execution_capabilities`
    - reserve `extra_inputs.optional` for inputs that are not required by the selected configuration but still enrich or modify inference when provided
    - an input may appear in both `optional` and `conditional_required` only when it is genuinely optional in some valid configurations and required in others; use different `usage` text if the behavior differs
    - if an execution capability is the only supported mode and the input is therefore always required for that tool, declaring it in `extra_inputs.required` is acceptable and often clearer than a conditional rule
    - do not rely on GUI-only or orchestrator-only hardcoding as the source of a required input rule; express the rule in the ToolSpec whenever the catalog can represent it
    - if the semantic content does not match an existing normalized input, propose a new `input_spec`
    - document why each reused normalized input matches semantically, not just structurally

### Output semantics

- `outputs.directed`
- `outputs.sign`
- `outputs.evidence`
  - look in:
    - paper method definition
    - output files in repo/examples
    - package docs describing edge meaning
  - search for:
    - whether edges are directed
    - whether sign is available
    - whether evidence is association, causal, or pseudotime-directed
  - also determine:
    - whether the chosen upstream interface returns raw method scores or already-normalized scores
    - whether raw scores are signed coefficients or non-negative confidence/importance values
    - when scores are signed coefficients, whether the upstream source defines edge confidence/ranking by absolute coefficient magnitude
  - rule:
    - `network.csv` `score` is a positive raw magnitude/strength value; it may be greater than `1`
    - when the upstream score is a signed coefficient, write `score = abs(coefficient)` and encode the coefficient direction only in `sign` as `+` or `-`
    - when no sign is available, write `sign = "?"`
    - never encode direction by making `score` negative
    - preserve source and target ids exactly as they appear in the normalized
      expression input; do not add prefixes such as `gene` to numeric ids, and
      do not mix upstream aliases with public ANDREA ids
    - every row must have a non-empty `context`; use `global`, `group:<id>`, `column:<id>`, or a documented tool-specific non-empty context family
    - for `column_native`, `column:<id>` values must correspond to expression column identifiers unless stronger upstream evidence documents a different column identifier mapping and the wrapper records that mapping as an auxiliary artifact
    - for `group_aggregated`, the wrapper's physical `column_native` `network.csv` should preserve `column:<id>` rows; ANDREA keeps those rows as an auxiliary `network.column_native.csv` artifact and writes only derived `group:<id>` rows to the logical `group_aggregated` `network.csv`
    - downstream normalized networks and `evaluate-inference` rank by the positive `score`, with sign handled separately by the `sign` column
    - do not add an extra ANDREA-specific score normalization layer in the wrapper; downstream normalization is handled later by `infer_network`
    - exact zero-magnitude edges should be omitted from `network.csv`; zero means "no retained interaction", not a useful stored edge

### Progress

- `progress.kind`
- `progress.note`
  - look in:
    - upstream logs
    - iteration counters
    - target-gene loops
    - partitioned tasks or phases
  - search for:
    - a stable observable unit that the wrapper can convert into `progress.json`
  - rule:
    - this field is defined by wrapper instrumentation, but it must still be justified from real upstream execution behavior

### Runtime Resources

- `runtime_resources.threading.supported`
- `runtime_resources.threading.default_threads`
- `runtime_resources.threading.max_threads`
- `runtime_resources.threading.upstream_mapping`
  - look in:
    - CLI help and argument parsers
    - function signatures and exported workflow constructors
    - README examples, benchmark scripts and scheduler examples
    - source code around loops over genes, targets, cells, samples, folds,
      bootstraps, subsamples or parameter partitions
    - imports/usages of `parallel`, `multiprocessing`, `concurrent.futures`,
      `joblib`, `foreach`, `doParallel`, `Dask`, `OpenMP`, `torch`,
      BLAS/MKL/OpenBLAS controls or equivalent runtime backends
  - search for:
    - explicit resource controls such as `threads`, `cores`, `workers`,
      `n_jobs`, `njobs`, `processes`, `cpus`, `pool`, `scheduler`, `backend`
    - environment-variable controls such as `OMP_NUM_THREADS`,
      `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, Torch thread setters or
      runtime-specific equivalents
    - documented recommendations to split independent work units and run
      multiple public upstream invocations
  - rule:
    - set `supported=true` when ANDREA `--threads` can be mapped to a real
      upstream CPU control or documented execution pattern that can affect
      runtime
    - set `supported=false` when there is no safe, public, reproducible mapping;
      in that case `default_threads=1`, `max_threads=1`, and the wrapper must
      reject or ignore only according to the project runtime contract
    - do not expose thread/resource controls as normal `params`
    - if using process-level sharding, each shard must call the public upstream
      entrypoint, preserve shard raw outputs/configs/logs, and merge only after
      upstream completes
    - do not claim speculative parallelism that would require reimplementing
      the algorithm or changing scientific semantics
    - document uncertainty when the mapping exists but scaling benefit is
      workload-dependent

### Parameters

- `params`
  - look in:
    - CLI flags
    - function signatures
    - README parameter tables
    - config examples
    - defaults in code
  - search for:
    - parameter names
    - data types
    - defaults
    - enum values
    - numeric bounds
  - rule:
    - prefer upstream parameter names unless there is a strong normalization reason not to
    - if defaults conflict across sources, document the conflict and justify the chosen value
    - do not expose a parameter just to encode a fixed wrapper decision or a
      single allowed implementation preset; use `params: {}` when the wrapper
      intentionally mirrors one upstream public preset, and document the fixed
      choice in `method_summary`, `method_keywords` and
      `integration_decisions.md`
    - do not add params for ANDREA-owned behavior such as
      `group_aggregated` aggregation, merge normalization or bundle creation

### Auxiliary artifacts

- `artifacts_aux`
  - look in:
    - example output directories
    - repo docs on generated files
    - logs and temporary outputs that are useful for debugging
  - search for:
    - non-trivial files/dirs worth validating in smoketests

## Required Structure of `integration_decisions.md`

The decision log must include:
- what value was chosen
- where the evidence came from
- why that evidence supports the chosen value
- whether the value is certain or uncertain

If a value is unclear:
- say that it is unclear
- list the conflicting evidence
- explain the chosen temporary resolution

## Quick Command Summary

From repository root:

```bash
make scaffold-tool TOOL=<tool_id> WRAPPER=python
make prepare-tool-papers TOOL=<tool_id>
make verify-tool TOOL=<tool_id>
make validate-smoketest-configs ARGS="--tool <tool_id>"
make benchmark-tool-costs ARGS="--tool <tool_id>"
make validate-tool-costs ARGS="--tool <tool_id>"
make build-tool-images ARGS="--tool <tool_id>"
make push-tool-images ARGS="--tool <tool_id>"
```

Notes:
- `WRAPPER` can be `python`, `r`, or another implementation language label such as `matlab`, `julia`, `java`
- only `python` and `r` currently get language-specific scaffold templates
- use `ARGS="--wrapper-ext <ext>"` when the desired file extension is not the obvious default
- `verify-tool` is intended for late-phase verification, not for an empty scaffold
- `run_smoketests.py` already builds the tool image unless explicitly skipped

## Review Checklist

Before considering a tool integrated, confirm:
- local paper PDFs were extracted to text and reviewed
- all relevant upstream public modes/entrypoints were audited before the wrapper contract was chosen
- exposed and excluded upstream modes/entrypoints are documented with rationale
- `toolspec.json` matches the real upstream interface
- every non-trivial `toolspec` field has explicit evidence in `integration_decisions.md`
- conditional inputs are modeled in the catalog when needed
- required inputs are modeled by always-required, execution-mode-required, or parameter-required rules as appropriate
- no normalized input is being reused with the wrong semantics
- no fixed single-value implementation choice is exposed as a user parameter
- runtime resource controls are declared in `runtime_resources.threading`, not
  as user-facing params, and wrapper `--threads` maps to the documented upstream
  control or is constrained to one thread
- the wrapper does not rely on the local `repo/`
- the `Dockerfile` uses a stable public source
- the output mapping to raw `network.csv` is documented in `integration_decisions.md`
- public ids in `network.csv` match normalized input ids, or an explicit
  auxiliary alias map proves how upstream aliases were mapped back
- any per-tool normalization is left to downstream runtime merge, not silently added by the wrapper unless the chosen upstream public interface itself defines that scale
- `group_emulated`, `group_aggregated`, merge normalization and ZIP bundle
  availability are treated as ANDREA core behavior, not wrapper-specific logic
- smoketest passes
- `cost.json` exists and validates if planner support is expected for the tool,
  and its thread matrix respects `runtime_resources.threading`
- the packaged image can be built, and pushed if publication is part of the integration task
