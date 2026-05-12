# Cell-Native GRN Implementation Plan

## Goal

Add first-class platform support for inference tools that produce one inferred gene regulatory network per individual cell, without integrating those wrappers yet.

This plan prepares ANDREA to support future tools such as LIONESS, ScReNI, CeSpGRN and scGeneRAI by extending catalog contracts, core commands, GUI behavior, evaluation, comparison, simulator outputs and tests.

## Required Prerequisite

Complete `TRUTH_NETWORKS_UNIFICATION_PLAN.md` first.

This plan assumes all simulator truth networks are represented through:

```text
truth/networks.csv
```

with normalized columns:

- `source`
- `target`
- `score`
- `sign`
- `evidence`
- `context`

The cell-native profile extends that unified file with:

```text
context = "cell:<cell_id>"
```

It does not introduce a separate `truth/cell_networks.csv` file.

## Scope

In scope:

- Add a new inference execution capability for native cell-specific inference.
- Add a derived grouped-output capability for aggregating cell-native outputs by `groups.tsv`.
- Add a canonical simulation/evaluation profile for datasets with cell-specific regulatory truth.
- Extend the unified truth-network contract with cell contexts.
- Identify new standardized extra inputs needed by future cell-native tools.
- Update generate-data, infer-network, evaluate-inference and compare-networks to tolerate cell-level contexts.
- Extend dyngen and scMultiSim separately so they can generate cell-specific truth rows when supported by their native simulator output.

Out of scope:

- Implementing wrappers for LIONESS, ScReNI, CeSpGRN or scGeneRAI.
- Adding legacy compatibility for older manifests or specs.
- Adding a cell-emulated execution mode. Running a global method on one cell at a time is not generally meaningful.
- Adding configurable aggregation methods for cell-to-group summaries. The first contract uses one fixed ANDREA rule.
- Adding online organism, TF, peak or motif resolution.

## Working Decisions

- Inference capability name: `cell_native`.
- Derived grouped capability name: `group_aggregated`.
- Simulation/evaluation profile name: `scrna_cell_specific`.
- Cell-specific network context convention: `context = "cell:<cell_id>"`.
- Aggregated group network context convention: `context = "group:<group_id>"`.
- Public truth file remains `truth/networks.csv`.
- `network.csv`, merged inferred networks and `truth/networks.csv` use the same edge columns.
- `score` remains a strictly positive magnitude. Signed effects must store direction in `sign`.
- Zero-score edges must not be exported.
- `context` remains an opaque public value. The platform may classify known prefixes internally for rendering, but it must not add public context-type fields.
- `group_aggregated` has no user-facing aggregation parameter.
- `group_aggregated` requires `groups.tsv` and a successful `cell_native` output.
- `group_aggregated` always aggregates by mean signed effect when signs are meaningful, and by mean unsigned score when signs are not meaningful.
- Existing `schema_version` values remain `"1.0"`.
- No legacy fallbacks are introduced.

## Conceptual Model

`cell_native` is an inference execution capability, not merely a dataset profile.

`group_aggregated` is a derived inference output capability managed by ANDREA. It does not run the upstream method once per group. Instead, ANDREA runs a cell-native method once, then aggregates `cell:<cell_id>` network rows into `group:<group_id>` rows using `groups.tsv`.

This is intentionally different from `group_emulated`:

- `group_emulated`: ANDREA splits the expression matrix and executes the tool independently for each group.
- `group_aggregated`: ANDREA does not split expression. It summarizes native per-cell outputs after one cell-native execution.

`group_emulated` does not require a tool to also expose `global`. Some tools may be designed only for one already selected cell population and therefore expose only `group_emulated`. In contrast, `group_aggregated` should only be exposed together with `cell_native`, because the aggregation source is the tool's native per-cell network output.

`scrna_cell_specific` is a simulation/evaluation profile for generated datasets that include cell-specific regulatory truth.

A dataset with profile `scrna_grouped` can still be used as input to cell-native inference tools if it has cells as expression columns. However, proper cell-level evaluation requires `cell:<id>` truth contexts. Therefore, evaluation support depends on truth availability, not only on expression matrix shape.

`group_aggregated` can be evaluated against ordinary grouped truth when `truth/networks.csv` contains matching `group:<group_id>` contexts. It is useful for comparing cell-native tools against `group_native` and `group_emulated` tools at group resolution.

## Fixed Cell-To-Group Aggregation Rule

For each group in `groups.tsv`, ANDREA aggregates every edge over the selected cells in that group.

Signed tools:

- Convert each cell edge to a signed effect:
  - `+` => `+score`
  - `-` => `-score`
- Missing edge in a cell contributes `0`.
- Compute `group_effect = mean(signed_effect)` across cells in the group.
- Export:
  - `score = abs(group_effect)`
  - `sign = "+"` when `group_effect > 0`
  - `sign = "-"` when `group_effect < 0`
- Omit the edge when `group_effect == 0`.

Unsigned tools:

- Missing edge in a cell contributes `0`.
- Compute `score = mean(score)` across cells in the group.
- Export `sign = "?"`.
- Omit the edge when `score == 0`.

The sign-aware branch should be selected from the tool output contract, not from a user parameter. The aggregation method is fixed to keep `group_aggregated` reproducible and avoid adding parameterized execution capabilities.

## Phase 1: Prerequisite Gate And Naming Lock

Before changing behavior, verify that the truth-unification plan is complete:

- `ground-truth-manifest.json` references `outputs.networks`.
- `simulator-output-manifest.json` references `truth.networks`.
- dyngen and scMultiSim write `truth/networks.csv`.
- evaluate-inference loads truth by context from `truth/networks.csv`.
- no public code path still expects `truth/global_network.csv` or `truth/group_networks/`.
- no public graph export exposes `context_scope` or an equivalent derived context-type field.
- evaluate-inference and compare-networks do not assume that the only possible contexts are `global` and `group:*`.

Then lock names:

- `cell_native`
- `group_aggregated`
- `scrna_cell_specific`
- `cell:<cell_id>`
- `group:<group_id>`

Expected outcome:

- A short implementation note confirming the prerequisite and exact strings.
- No behavior changes in this phase.

## Phase 2: Standardized Inputs Inventory

Separate simulator truth outputs from infer-network extra inputs.

Existing inputs that already fit future cell-native tools:

- `expression_matrix`
  - Core input for LIONESS, CeSpGRN and scGeneRAI.
- `tf_list`
  - Can restrict regulator candidates for methods that support regulator masks or TF lists.
- `groups`
  - Can be used only when a method explicitly accepts cell group labels as metadata or descriptors.
  - It must not be treated as required for `cell_native`.
  - It is conditionally required for `group_aggregated`, where ANDREA uses it to map `cell:<cell_id>` outputs to `group:<group_id>` summaries.

Candidate new input specs to audit before adding:

- `chromatin_accessibility_matrix`
  - Needed for ScReNI-style scRNA-seq plus scATAC-seq methods.
  - Candidate format: region-by-cell or peak-by-cell TSV.
  - Must define whether rows are peaks, regions or accessibility features.
  - Must specify how cell IDs match expression columns.
- `cell_descriptors`
  - Potential optional input for scGeneRAI-like categorical descriptors.
  - More general than `groups.tsv`, because it may contain multiple categorical columns such as batch, donor or cell type.
  - Add only if wrapper Phase 1 evidence confirms that `groups` is too narrow.

Expected outcome:

- Add only input specs needed by the platform contract before wrapper implementation.
- Do not add placeholder specs for speculative tool features.

## Phase 3: Inference Catalog Schema

Update the inference ToolSpec contract:

- Extend `execution_capabilities` enum with `cell_native`.
- Extend `execution_capabilities` enum with `group_aggregated`.
- Extend validation so `conditional_required.execution.mode` may reference `cell_native`.
- Extend validation so `conditional_required.execution.mode` may reference `group_aggregated`.
- Keep `group_emulated` rules unchanged.
- Do not imply that `groups` is required for `cell_native`.
- Do not introduce a general schema-level dependency mechanism between execution capabilities.
- Integration review should only declare `group_aggregated` for tools that also declare `cell_native`, because `group_aggregated` consumes native per-cell output.
- Add a simple defensive planner/run check that fails clearly if a malformed ToolSpec exposes `group_aggregated` without `cell_native`.
- Require ToolSpecs that declare `group_aggregated` to include a `conditional_required` rule for `groups` when `execution.mode == "group_aggregated"`.
- Do not add aggregation parameters to ToolSpecs; `group_aggregated` uses the fixed ANDREA aggregation rule.
- Add documentation text explaining:
  - `global`: one network for the whole expression matrix.
  - `group_native`: tool natively produces one or more group-level networks.
  - `group_emulated`: ANDREA splits the dataset by `groups.tsv`.
  - `group_aggregated`: ANDREA aggregates native per-cell networks by `groups.tsv`.
  - `cell_native`: tool natively produces one network per cell.

Expected outcome:

- Current tools remain valid.
- Future cell-native tools can be represented without schema workarounds.
- Future cell-native tools can also expose group-level outputs through a standard `group_aggregated` capability without wrapper-specific parameters.

## Phase 4: Normalized Network Context Handling

Extend normalized network handling to recognize cell contexts.

Context rules:

- `context == "global"` means global context.
- `context.startswith("group:")` means group context.
- `context.startswith("cell:")` means cell context.

Core changes:

- Keep `context` as the only public context field.
- Do not add public `context_type`, `context_scope`, `context_id` or equivalent columns.
- Use small internal helper functions to classify context values only where rendering, grouping or summaries require it.
- Unknown context families must remain valid and visible in tables/raw outputs.
- Keep validation strict:
  - empty context is invalid
  - non-positive score is invalid
  - sign must remain normalized

Expected outcome:

- Existing global and group outputs remain unchanged.
- Future wrappers can emit `cell:<id>` contexts in normal `network.csv` files.

## Phase 5: Generate-Data Profile Contract

Add simulation profile `scrna_cell_specific`.

Schema updates:

- `scenario-request.schema.json`
- `simulation-plan.schema.json`
- `simulatorspec.schema.json`
- `preflight-report.schema.json`
- `benchmark-manifest.schema.json`

SimulatorSpec profile capability changes:

```json
{
  "profile": "scrna_cell_specific",
  "truth_outputs": {
    "global": "native",
    "group": "none",
    "cell": "native"
  }
}
```

Allowed values remain:

- `native`
- `derivable`
- `none`

Rules:

- `scrna_cell_specific` requires expression columns to represent cells.
- The selected simulator must declare `truth_outputs.cell != "none"`.
- Generated `truth/networks.csv` must include at least one `cell:<id>` context.
- Global truth may still be included when the simulator can produce it, but cell truth is required.
- Group truth is optional and controlled by the simulator capability, not by `scrna_cell_specific` itself.

Manifest updates:

```json
{
  "outputs": {
    "gene_universe": "truth/gene_universe.txt",
    "networks": "truth/networks.csv"
  }
}
```

No `cell_networks` manifest field is added.

## Phase 6: Infer-Network Planning And Execution

Update infer-network to accept future `cell_native` tools.

Planner behavior:

- `cell_native` creates one logical run and one physical task unless the tool spec later declares internal chunking.
- `group_aggregated` creates one cell-native upstream physical task plus an ANDREA aggregation step.
- `cell_native` must not require `groups.tsv`.
- `group_aggregated` must require `groups.tsv` through ToolSpec `conditional_required`.
- `cell_native` should use cells, genes and expected output density in ETA features.
- `group_aggregated` should use the same upstream ETA as `cell_native` plus a small postprocessing cost based on cells, groups and emitted edge count.
- Both modes should warn if the selected dataset has many cells and genes and the tool is likely to emit dense per-cell networks.

Execution behavior:

- Wrappers still write one `network.csv`.
- For `cell_native`, wrappers write contexts as `cell:<cell_id>`.
- For `group_aggregated`, wrappers still write `cell:<cell_id>` contexts; ANDREA writes derived `group:<group_id>` contexts after wrapper completion.
- The aggregation step uses the fixed mean signed-effect rule and does not call the upstream tool again.
- The merge step must not collapse cell contexts.
- Merged raw and normalized outputs must preserve both cell and aggregated group contexts.

GUI behavior:

- Add `cell_native` as a selectable mode for tools that declare it.
- Add `group_aggregated` as a selectable mode for tools that declare it.
- Display an output-size warning when the plan includes cell-native runs.
- Do not show `groups.tsv` as required merely because `cell_native` is selected.
- Show `groups.tsv` as required when `group_aggregated` is selected.

## Phase 7: Evaluate-Inference With Cell Truth

Extend evaluation to support `cell:<id>` rows already present in `truth/networks.csv`.

Loading behavior:

- Load all truth rows from `outputs.networks`.
- Group rows by exact `context`.
- Match inferred rows to truth by exact `context`.
- No separate cell truth file is loaded.

Metric behavior:

- Compute existing levels independently per cell context:
  - topology
  - directed
  - signed
- Aggregated group outputs from `group_aggregated` are evaluated exactly like any other `group:<id>` inferred network.
- Continue using normalized positive magnitudes for ranking metrics.
- Keep `f1_at_truth_count` and `EPR@truth_count`.
- Report skipped contexts when inference has a cell context without matching truth or vice versa.

Visualization behavior:

- Choose the primary chart by context family and cardinality:
  - `global`: bar chart by tool/run, matching the current global view.
  - `group:<id>`: heatmap by tool/run and group, matching the current grouped view when the number of groups is reasonable.
  - `cell:<id>`: violin plot or boxplot by tool/run, showing the distribution of metric values across cells.
- Keep a fallback table or compact list for valid contexts whose family is not yet associated with a dedicated chart type.
- Do not render thousands of cell contexts as one giant heatmap by default.
- For cell contexts, add compact summaries next to the distribution plot:
  - number of evaluated cells
  - number of skipped or unmatched cells
  - median
  - quartiles
  - min and max
  - optional top/bottom cell tables
- Allow drilling into exact cell-level values through a searchable or paginated metrics table.
- Keep raw `metrics.csv` as the authoritative detail table.

## Phase 8: Compare-Networks With Cell Contexts

Update compare-networks so cell-specific networks can be compared without special-case failures.

Distance maps:

- Allow context selector to include `cell:<cell_id>`.
- Avoid loading every cell context into the first viewport if there are many contexts.
- Provide filtering/search by cell ID.

Ordered edge differences:

- Permit selecting networks from any context, including different cells.
- Permit selecting `group_aggregated` outputs alongside `group_native` and `group_emulated` outputs when contexts are comparable.
- Continue comparing only common genes.
- Show a warning if there are no common genes or no comparable edges.

Performance:

- Distance calculations over many cell contexts can be expensive.
- Add clear progress reporting and consider lazy context-level calculations if needed.

## Phase 9: Costs And ETA Extensions

Extend cost features for inference tools:

- `execution_mode = cell_native`
- `execution_mode = group_aggregated`
- `n_cells`
- `n_genes`
- `n_groups`
- `expected_contexts`
- `expected_dense_edges = n_cells * n_genes * (n_genes - 1)`
- `has_tf_list`
- `has_chromatin_accessibility_matrix`
- `output_density_class`
- `aggregation_step = none|cell_to_group`

Extend cost features for simulators:

- `profile = scrna_cell_specific`
- `n_cells`
- `n_genes`
- `n_tfs`
- `native_cell_truth_enabled`
- simulator-specific dynamic GRN flags

Rules:

- Missing cost profiles should warn during planning.
- Execution should not repeat planning-only cost warnings.
- Tool and simulator costs should not expose thread count as a user-facing biological parameter.
- `group_aggregated` ETA should be based on the selected tool's `cell_native` cost plus deterministic ANDREA aggregation overhead, not on multiplying by group count as `group_emulated` does.

## Phase 10: GUI Generate-Data Updates

Add profile support:

- Add `scrna_cell_specific` as a selectable canonical profile.
- Show only simulators that can produce cell truth.
- Show conditional parameter errors when cell-specific truth depends on simulator parameters.

Truth and output UI:

- Clearly separate:
  - public truth outputs
  - standardized extras for inference
  - native/provenance outputs
- Do not present cell truth as a casual optional extra when `scrna_cell_specific` requires it.
- Describe cell truth as `cell:<id>` contexts inside `truth/networks.csv`.

Result explorer:

- Show `truth/networks.csv`.
- Add file-size warnings for large cell-specific truth outputs.

## Phase 11: GUI Infer-Network Updates

Add cell-native execution support:

- Show `cell_native` as a mode when declared by a ToolSpec.
- Show `group_aggregated` as a mode when declared by a ToolSpec.
- Include concise explanatory text in tool info:
  - "Produces one network per cell."
  - "Aggregates native per-cell networks into one network per group using `groups.tsv`."
- Add a plan-level warning when output size may be large.
- Show the normal conditional-required input error when `group_aggregated` is selected without `groups.tsv`.

Additional input modal:

- Include new standardized inputs, if added:
  - `chromatin_accessibility_matrix`
  - `cell_descriptors`
- Tags should show which future tools require or optionally use them once those ToolSpecs exist.

No wrapper-specific UI should be hardcoded.

## Phase 12: Simulator Extension - dyngen

dyngen already exposes native cell-specific regulatory activity as `regulatory_network_sc`.

Required changes:

- Add `scrna_cell_specific` to `dyngen` `profile_capabilities`.
- Add `truth_outputs.cell = "native"` or `"derivable"` depending on final evidence wording.
- Generate `cell:<cell_id>` rows in public `truth/networks.csv` from `dataset$regulatory_network_sc`.
- Preserve native `regulatory_network_sc.tsv` behavior under native outputs.
- Do not apply the group-level active-edge threshold used for group truth.
- Export one row per non-zero cell-specific regulatory activity:
  - `source = regulator`
  - `target = target`
  - `score = abs(strength)`
  - `sign = sign(strength)`
  - `evidence = simulated_truth`
  - `context = cell:<cell_id>`

Validation:

- Ensure every `cell:<cell_id>` exists in `expression.tsv` columns.
- Ensure genes in truth are inside `gene_universe.txt`.
- Ensure no self-loops unless dyngen primary evidence requires them.

Smoke tests:

- Add one `scrna_cell_specific` smoke config.
- Validate `truth/networks.csv` contains `cell:` contexts.
- Confirm generated dataset passes infer-network preflight.
- Confirm evaluate-inference can load cell truth, even before cell-native tools exist, using a small synthetic inferred fixture if needed.

## Phase 13: Simulator Extension - scMultiSim

scMultiSim exposes `cell_specific_grn` when `dynamic_grn.enabled=true`.

Required changes:

- Add `scrna_cell_specific` to `scmultisim` `profile_capabilities`.
- For `scrna_cell_specific`, require or force-compatible `dynamic_grn.enabled=true`.
- Add `truth_outputs.cell = "native"` or `"derivable"` depending on final evidence wording.
- Generate `cell:<cell_id>` rows in public `truth/networks.csv` from `results$cell_specific_grn`.
- Preserve native `cell_specific_grn.rds` behavior under native outputs.
- Export one row per non-zero cell-specific GRN effect:
  - `source = regulator / TF`
  - `target = target gene`
  - `score = abs(effect)`
  - `sign = sign(effect)`
  - `evidence = simulated_truth`
  - `context = cell:<cell_id>`

Validation:

- Ensure `length(results$cell_specific_grn)` matches expression cell count.
- Ensure regulator and target names map back to public expression gene IDs.
- Ensure zero effects are filtered.

Optional standardized extra candidate:

- If future ScReNI integration is prioritized, expose scMultiSim `atac_counts` as a standardized `chromatin_accessibility_matrix` extra, not only as a native output.

Smoke tests:

- Add one `scrna_cell_specific` smoke config with `dynamic_grn.enabled=true`.
- Validate `truth/networks.csv` contains `cell:` contexts.
- Validate behavior when `dynamic_grn.enabled=false` is incompatible with `scrna_cell_specific`.
- Confirm generated dataset passes infer-network preflight.

## Phase 14: Evaluation And Comparison Regression Tests

Add focused tests with small fixtures:

- `ground-truth-manifest.json` with `outputs.networks`.
- Truth rows with `cell:<id>` contexts.
- Inferred `merged_network_raw.csv` with `cell:<id>` contexts.
- Inferred `merged_network_raw.csv` with `group:<id>` contexts derived by `group_aggregated`.
- Evaluation computes metrics for matching cell contexts.
- Evaluation computes metrics for `group_aggregated` outputs against matching group truth contexts.
- Evaluation reports skipped contexts cleanly.
- Compare-networks loads `cell:<id>` contexts.
- Compare-networks loads `group_aggregated` group contexts and can compare them with other group-level outputs.
- Network exports and visual summaries internally recognize `cell:<id>` contexts without adding a public context-type field.
- A non-`global`, non-`group:*`, non-`cell:*` fixture remains visible in tables/raw outputs rather than being hidden or rejected.

Do not add tests for old unsupported manifest forms.

## Phase 15: Documentation And Playbooks

Update:

- `wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md`
- `wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md`
- catalog documentation under `docs/`, if present
- GUI help text for canonical profiles and execution modes

Required documentation points:

- Cell-native tools must preserve raw method score magnitudes.
- `group_aggregated` is an ANDREA-managed derived mode that aggregates `cell:<id>` outputs into `group:<id>` outputs using a fixed mean signed-effect rule.
- `group_aggregated` must not expose aggregation parameters in the first contract.
- `group_aggregated` requires `groups.tsv` and must not rerun the upstream method per group.
- `group_aggregated` should only be declared by ToolSpecs that also declare `cell_native`, but this is a catalog integration rule and defensive execution check, not a general schema dependency system.
- Dense per-cell outputs can be very large.
- Wrappers must not emulate cell-native mode unless upstream explicitly defines such a public mode.
- Simulators must distinguish native cell truth from group-aggregated truth.
- Cell-specific truth is represented through `cell:<id>` contexts in `truth/networks.csv`, not as an infer-network extra input.

## Phase 16: Future Wrapper Integration Readiness Gate

Before integrating LIONESS, ScReNI, CeSpGRN or scGeneRAI, verify:

- ToolSpec schema accepts `cell_native`.
- ToolSpec schema accepts `group_aggregated` as a normal execution capability.
- Phase 15 has updated the integration playbook to document that `group_aggregated` should only be declared together with `cell_native`.
- Planner/run checks fail clearly if `group_aggregated` is selected for a malformed ToolSpec that lacks `cell_native`.
- `infer-network` can execute a dummy `cell_native` wrapper and merge `cell:<id>` contexts.
- `infer-network` can derive `group:<id>` contexts from dummy `cell:<id>` outputs in `group_aggregated` mode.
- `evaluate-inference` can evaluate `cell:<id>` contexts against unified `truth/networks.csv`.
- `evaluate-inference` can evaluate `group_aggregated` outputs against `group:<id>` truth contexts.
- `compare-networks` can compare cell contexts.
- `compare-networks` can compare `group_aggregated` group contexts against other group-level outputs.
- `generate-data` can produce at least one dyngen and one scMultiSim `scrna_cell_specific` dataset.
- Costs and ETAs are available or planning warnings are clear.

Only after this gate should individual tool Phase 1 integrations begin.

## Suggested Implementation Order

1. Complete `TRUTH_NETWORKS_UNIFICATION_PLAN.md`.
2. Add `cell_native`, `group_aggregated` and `scrna_cell_specific` schema/catalog support.
3. Extend core context handling for `cell:<id>`.
4. Extend evaluate-inference for cell contexts in `truth/networks.csv`.
5. Add fixed cell-to-group aggregation in infer-network for `group_aggregated`.
6. Add generate-data profile support.
7. Add dyngen `scrna_cell_specific`.
8. Add scMultiSim `scrna_cell_specific`.
9. Update GUIs.
10. Extend costs and ETA profiles.
11. Refine compare-networks for many cell contexts.
12. Update documentation and playbooks.

This order keeps each step testable and avoids integrating wrappers before the platform can correctly represent their outputs.
