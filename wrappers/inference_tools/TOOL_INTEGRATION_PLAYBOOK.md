# Tool Integration Playbook

Shared behavior changes require prior user consultation: catalog filtering,
normalized input schemas, validation scope and other methods' input semantics
are not implicit parts of a new integration. Follow the scope rule in
[the common procedure](../../docs/INTEGRATION_WORKFLOW.md#scope-of-an-integration).

This playbook defines the inference-specific contract for the [shared autonomous integration workflow](../../docs/INTEGRATION_WORKFLOW.md). Execute that workflow end to end after one request; the checks below are internal gates, not prompts for user approval.

Use it when you have local evidence under:

```text
wrappers/inference_tools/tools/<tool_id>/
  repo/
  papers/
```

`repo/` and `papers/` are evidence sources for analysis only. They must not become runtime dependencies.
Accept articles directly as TXT. PDFs and extraction are optional. Record bibliographic identity, hashes and evidence limitations; a sufficient TXT does not require a PDF.
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
   - require immutable pinned public sources; leave the reproducibility gate pending when they are inaccessible
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

## Wrapper layout and candidate promotion

Use the existing Osaka worktree and current branch; do not create a separate
branch or worktree per method unless explicitly requested by the user.

New wrappers keep `Dockerfile`, `run_tool.*`, `integration_decisions.md` and
the user's local `papers/` and `repo/`. Keep scientific notes in that decision
log, dependency pins/checksums in the Dockerfile, and required compatibility
patches in the wrapper. Do not scaffold a separate lock file, ignore file,
duplicate README, per-method dossier or `wrappers/integration/inference/` folder.

Spec and smoke candidates start in `release-logs/inference-<tool_id>/` or the
explicit `--results-dir`, outside the wrapper and active catalogs. Promotion
with `--activate-catalog` and `--activate-smoketest` validates the selected
candidates before writing active files. It removes only draft copies identical
to their active counterparts. Conflicting or uncopied drafts remain available;
new integrations use only the staged locations above, without wrapper-local
legacy drafts or new compatibility readers. Existing generic helper support does
not authorize those paths. Repeating the scaffold after promotion does not
recreate placeholder drafts.

Use canonical catalog specs, smoke configurations and cost recipes afterward.
Inference uses the central scripts/template_map.json; a matching local candidate map can be removed after promotion.
Commands, logs, hashes and validation reports belong in the existing campaign
results. No additional versioned dossier is required. Preserve historical
receipts and frozen campaign sources when changing layout.

## End-to-end execution

Follow [INTEGRATION_WORKFLOW.md](../../docs/INTEGRATION_WORKFLOW.md) for the
single-request sequence, minimal files, traceability matrix, calibration design,
independent validation, receipts, acceptance states and reusable prompt. Keep
this document for the inference-specific field rules below.

Commands from the repository root (run only the current gate):

```sh
make scaffold-tool TOOL=<tool_id> WRAPPER=python
make prepare-tool-papers TOOL=<tool_id>
make validate-toolspecs ARGS="--tool <tool_id>"
make validate-input-specs
make validate-smoketest-configs ARGS="--tool <tool_id>"
make verify-tool TOOL=<tool_id>
make benchmark-tool-costs ARGS="--tool <tool_id> --plan-only --repeats 3"
python wrappers/inference_tools/scripts/validate_tool_costs.py \
  --tool <tool_id> --require --require-provenance --require-coverage
```

`prepare-tool-papers` accepts existing TXT without an extractor. Scaffolds remain
outside the active catalog until explicitly activated with the scaffold option
or moved after implementation; inspect `--help`. `verify-tool` builds and runs
containers and is a late functional check. It does not establish complete
calibration, independent accuracy or image publication. The inference smoke
runner supports a `variants` array in its per-tool config; use variants for
physical mode/input cases and test logical core orchestration separately.

See [cost_profiles/README.md](cost_profiles/README.md) for measured matrix,
coverage/resume controls and independent prediction validation. Respect
`runtime_resources.threading` when choosing the resource panel. Keep the same
version tag for unpublished fixes and retain measured immutable image IDs.
Publishing is a separate authorized gate, never a phase prerequisite.

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
    - primary paper TXT (or optional PDF)
  - search for:
    - full name of the first author of `publication[0]`
- `year`
  - look in:
    - primary paper TXT (or optional PDF)
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
    - describe execution exclusively with `execution_capabilities`
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
    - `group_emulated` always requires its physical companion capability `global`; declare `groups` only in `extra_inputs.conditional_required` for `execution.mode=group_emulated` with `delivery: "orchestration_only"`
    - if `group_aggregated` is exposed, declare `groups` in `extra_inputs.conditional_required` with `execution: "mode"`, `value: "group_aggregated"`, and `delivery: "orchestration_only"`
    - never mount orchestration-only `groups.tsv` in a wrapper: logical
      `group_emulated` children execute physically as `global` on an already
      partitioned expression matrix, while logical `group_aggregated` children
      execute physically as `column_native` before ANDREA performs aggregation
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
      - `timepoints` (observed sampling coordinates; metadata is supplied separately through the normalized `column_descriptors` extra)
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
    - encode `extra_inputs.required` and `extra_inputs.optional` as objects with
      `input`, `usage`, and `delivery`; strings and implicit delivery are invalid
    - include `usage` and `delivery` on every `conditional_required` rule so the
      GUI can explain why the file is needed and the runner can enforce whether
      it crosses the container boundary
    - use `delivery: "runtime"` only when the selected upstream wrapper reads
      the file; use `delivery: "orchestration_only"` when ANDREA core requires
      the input for planning, partitioning, aggregation, or finalization but the
      physical wrapper must not see it
    - for `groups`, use runtime delivery only in a native grouped mode whose
      upstream interface consumes group metadata; `group_emulated` and
      `group_aggregated` rules must always use orchestration-only delivery
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
    - physical-wrapper smoketests must copy only active runtime-delivered files;
      do not list orchestration-only `groups.tsv` in `extra_files`. Verify raw
      `context=global` for emulated children and raw `context=column:<id>` for
      aggregated runs; cover the final logical group contexts in ANDREA core
      orchestration tests

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
    - `kind: "none"` means no stable incremental counter; explain available
      phase updates in `note`, rather than implying that no status is emitted
    - use `task_partitions` for completed native work units such as windows;
      distinguish counts of completed work from a prediction of remaining time
    - publish the inference phase before a long fit or CV begins, and report
      aggregation/export separately when those phases are observable
    - verify intermediate phase/counter events as well as terminal status; an
      auxiliary event journal can preserve short phases without inventing a new
      public output format or depending on polling frequency

Method-specific coefficients, diagnostic tables, logs, models and other
informative native outputs remain declared `artifacts_aux`, as elsewhere in
the catalog. `network.csv` is the common scientific network contract. Add a new
normalized output contract only when exposing a distinct reusable semantic
capability, not merely because a method writes an additional informative file.

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
    - set `max_threads=null` only when a multithreaded wrapper has no intrinsic
      upper bound; ANDREA still validates allocations against the effective run
      resource budget
    - do not expose thread/resource controls as normal `params`
    - native BLAS/OpenMP limits must be effective before the interpreter or
      library initializes. Setting environment variables after importing NumPy,
      or using `Sys.setenv()` after R has already loaded OpenBLAS, is insufficient.
      In `scripts/template_map.json`, `native_threads="assigned"` makes the
      generated container entrypoint apply the assigned native thread count;
      `native_threads="single"` keeps one native thread per outer worker (or
      for a serial tool). An omitted policy is reserved for existing integrations
      whose image/wrapper already enforces its limits before initialization.
    - verify effective pools, not only environment strings, for every supported
      thread stratum, with unrestricted and pinned CPU affinity. Check child
      workers too. `scripts/validate_thread_controls.py` exercises these
      entrypoint policies in the actual catalog images without scientific fits.
      A Docker CPU quota alone does not bound the number of native threads.
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
    - expose the full compatible serializable upstream public surface; a chosen
      preset does not justify hiding other supported options. Fixed internal
      implementation constants need no user param, but public exclusions require
      individual evidence and rationale in the traceability matrix
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

## Evidence and acceptance records

Use the [shared decisions template](../integration/decisions.template.md) and
[acceptance procedure](../../docs/INTEGRATION_WORKFLOW.md#5-acceptance-image-publication-and-handoff).
Keep the selected interface, excluded modes, all public parameters and their
actual arguments, dynamic default rules, input delivery, output semantics,
resource mapping, patches and evidence limitations in the decision log.
Retain hashed artifacts and actual check reports in the campaign results.
Do not equate a passing smoke test or valid `cost.json` with a fully calibrated
integration. Every declared mode and conditional branch needs evidence.

## Reviewed method family

Set `method_family` from the actual integrated algorithm: `information`, `tree`,
`regression`, `neural`, `graph`, or `correlation`. Cite the implementation and
primary publication in `integration_decisions.md`. Context, priors and downstream
workflow stages belong in the summary and capability/input fields. The catalog
figure consumes this explicit family and rejects missing annotations.
After stable publication, use a new Docker version tag for changed wrappers or
dependencies; never replace a published historical image.
