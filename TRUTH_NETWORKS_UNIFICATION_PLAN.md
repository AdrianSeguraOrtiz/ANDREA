# Truth Networks Unification Plan

## Goal

Unify every simulator ground-truth regulatory network into one public file, `truth/networks.csv`, before adding new cell-specific GRN profiles.

This removes the current split between:

- `truth/global_network.csv`
- `truth/group_networks/<group>.csv`

and replaces it with a single context-aware edge table.

## Scope

In scope:

- Change simulator truth manifests, simulator output manifests, schemas, core code, GUI code, tests and playbooks to use `truth/networks.csv`.
- Represent global and group-specific truth with the same normalized edge columns and a `context` value.
- Remove `group_networks` as a selectable simulation extra.
- Keep all `schema_version` values at `"1.0"`.
- Reject old manifest/spec shapes rather than supporting legacy compatibility.

Out of scope:

- Adding the new cell-specific profile. That is covered by `CELL_NATIVE_GRN_IMPLEMENTATION_PLAN.md`.
- Integrating LIONESS, ScReNI, CeSpGRN or scGeneRAI.
- Keeping compatibility with `truth/global_network.csv` or `truth/group_networks/`.

## New Contract

Public truth file:

```text
truth/networks.csv
```

Required columns:

- `source`
- `target`
- `score`
- `sign`
- `evidence`
- `context`

Context values:

- `global`: network for the full dataset.
- `group:<group_id>`: network for one cell/sample group.
- `cell:<cell_id>`: reserved for the later cell-native plan, not introduced by this plan.

Rules:

- `score` is a strictly positive confidence or effect magnitude.
- Signed effects store their direction in `sign`; negative scores are invalid.
- Zero-score edges are not exported.
- Self-loops are excluded unless simulator primary evidence requires them.
- `context` is the only public discriminator between global, group and future cell networks.
- Internal code may classify contexts from their value when needed for rendering or summaries, but no public schema or CSV should split `context` into separate type/id fields.
- Parsers must group by exact `context` value. They may not reject an otherwise valid row only because the context is not `global` or `group:*`.
- Visual code may use known context families to choose chart types, but unknown future contexts must still appear in tables and raw outputs.

Manifest shape:

```json
{
  "outputs": {
    "gene_universe": "truth/gene_universe.txt",
    "networks": "truth/networks.csv"
  }
}
```

Simulator output manifests should similarly expose:

```json
{
  "truth": {
    "gene_universe": "truth/gene_universe.txt",
    "networks": "truth/networks.csv"
  }
}
```

SimulatorSpec profile capabilities should stop using `global_network` and `group_networks` as truth-output keys. Replace them with truth support by context family:

```json
{
  "truth_outputs": {
    "global": "native",
    "group": "derivable"
  }
}
```

Allowed values remain:

- `native`
- `derivable`
- `none`

`cell` is intentionally not added in this plan. It belongs to the later cell-native profile work.

## Phase 1: Lock Contract And Inventory

Record the exact target contract above and audit every reference to:

- `global_network`
- `group_networks`
- `truth/global_network.csv`
- `truth/group_networks`

Relevant areas already identified:

- Simulation schemas under `andrea/catalog_simulation_data_tools/schemas/`.
- Simulator specs under `andrea/catalog_simulation_data_tools/simulators/`.
- Generate-data core, GUI and tests.
- Evaluate-inference core, GUI and tests.
- Simulation wrappers for dyngen and scMultiSim.
- Simulation smoke configs and wrapper playbook.
- Cost-profile scripts and simulator cost profiles.
- Inferred-network graph exports that currently add a derived `context_scope` attribute.
- Compare-networks and evaluate-inference visual assets that currently special-case only `global` and `group:*`.

Known rigid implementations to remove or generalize:

- evaluate-inference truth loading from `outputs.global_network` plus `outputs.group_networks`.
- evaluate-inference GUI truth discovery/freezing based on old truth fields.
- evaluate-inference charts with only two panels: global bars and group heatmap.
- generate-data packaging decisions that copy/delete `truth/group_networks` based on `effective_extras`.
- benchmark artifacts named `global_network` and `group_networks_dir`.
- infer-network GraphML/GEXF exports and GUI text that expose `context_scope` as a public attribute.
- compare-networks labels that only strip the `group:` prefix and otherwise assume all non-global contexts are unclassified labels.
- smoke-test checks that can only require `group:*` contexts.

Expected outcome:

- No behavior changes.
- A complete checklist for later phases.

### Phase 1 Audit Results

Status: completed on 2026-05-11.

Locked contract:

- Public truth network path: `truth/networks.csv`.
- Public truth manifest field: `outputs.networks`.
- Public simulator output manifest field: `truth.networks`.
- Public edge context remains one opaque `context` value.
- No public `context_scope`, `context_type`, `context_id` or equivalent derived context fields.
- Old public truth paths and fields are removed, not supported as legacy:
  - `truth/global_network.csv`
  - `truth/group_networks/`
  - `outputs.global_network`
  - `outputs.group_networks`
  - `truth.global_network`
  - `truth.group_networks`

Audit commands used:

```bash
rg -l "global_network|group_networks|truth/global_network|truth/group_networks|group_networks_dir|include_group_networks" andrea wrappers tests utils Makefile -g '!**/repo/**' -g '!**/papers/**' -g '!**/additional_files/**' -g '!**/__pycache__/**'
rg -l "context_scope|startsWith\\([\\\"']group:|startswith\\([\\\"']group:|context === [\\\"']global|require_group_context" andrea wrappers tests utils -g '!**/repo/**' -g '!**/papers/**' -g '!**/additional_files/**' -g '!**/__pycache__/**'
```

Schema files to migrate in Phase 2:

- `andrea/catalog_simulation_data_tools/schemas/ground-truth-manifest.schema.json`
- `andrea/catalog_simulation_data_tools/schemas/simulator-output-manifest.schema.json`
- `andrea/catalog_simulation_data_tools/schemas/simulatorspec.schema.json`
- `andrea/catalog_simulation_data_tools/schemas/benchmark-manifest.schema.json`
- `andrea/catalog_simulation_data_tools/schemas/preflight-report.schema.json`
- `andrea/catalog_simulation_data_tools/schemas/scenario-request.schema.json`
- `andrea/catalog_simulation_data_tools/schemas/simulation-plan.schema.json`

Catalog and shared-contract files to migrate:

- `andrea/catalog_simulation_data_tools/simulators/dyngen/simulatorspec.json`
- `andrea/catalog_simulation_data_tools/simulators/scmultisim/simulatorspec.json`
- `andrea/core/shared/catalog_contracts.py`
- `wrappers/simulation_data_tools/cost_profiles/dyngen.json`
- `wrappers/simulation_data_tools/cost_profiles/scmultisim.json`

Generate-data files to migrate:

- `andrea/core/commands/generate_data/pipeline.py`
  - remove `include_group_networks`
  - stop copying/deleting `truth/group_networks`
  - emit `outputs.networks`
  - emit benchmark artifact `networks`
- `andrea/core/commands/generate_data/selection.py`
  - change default `truth_outputs` keys to `global` and `group`
- `andrea/gui/generate_data/server.py`
  - remove `group_networks` from the selectable output extras
  - bundle/check `truth/networks.csv`
- `andrea/gui/generate_data/static/app/main.js`
  - relabel truth output from `global_network.csv` / `group_networks/*.csv` to `networks.csv`
  - stop reading `truth_outputs.global_network`

Evaluate-inference files to migrate:

- `andrea/core/commands/evaluate_inference/evaluation.py`
  - load `outputs.networks`
  - group truth rows by exact `context`
  - remove loaders for `outputs.global_network` and `outputs.group_networks`
- `andrea/gui/evaluate_inference/server.py`
  - discover manifests by `outputs.networks`
  - freeze `truth/networks.csv`
  - stop writing old truth fields
- `andrea/core/commands/evaluate_inference/view_assets/view.js`
  - remove two-panel-only assumption
  - keep global bars and group heatmap as known-context renderers
  - add generic fallback for other valid context families

Inference/compare context-rigidity files to migrate:

- `andrea/core/commands/infer_network/commons/network_exports.py`
  - remove public `context_scope` from GraphML/GEXF exports and styling helpers
- `andrea/gui/infer_network/server.py`
  - remove GUI copy that advertises `context_scope`
- `andrea/core/commands/compare_networks/view_assets/view.js`
- `andrea/gui/compare_networks/static/app/main.js`
  - replace `group:`-only label helpers with generic context labeling
- `wrappers/inference_tools/scripts/run_smoketests.py`
- `wrappers/inference_tools/tests/schemas/smoketest.config.schema.json`
  - replace `require_group_context` with a generic context-prefix or context-pattern check

Simulator wrapper files to migrate:

- `wrappers/simulation_data_tools/simulators/dyngen/run_simulator.R`
  - write one `truth/networks.csv`
  - write global rows as `context=global`
  - write grouped rows as `context=group:<id>`
  - stop writing public old truth files
- `wrappers/simulation_data_tools/simulators/scmultisim/run_simulator.R`
  - same public output migration as dyngen
- `wrappers/simulation_data_tools/simulators/dyngen/integration_decisions.md`
- `wrappers/simulation_data_tools/simulators/scmultisim/integration_decisions.md`
  - update documented implemented behavior after wrapper migration

Tooling, smoke-test and documentation files to migrate:

- `wrappers/simulation_data_tools/scripts/benchmark_costs.py`
- `wrappers/simulation_data_tools/scripts/scaffold_simulator.py`
- `wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md`
- `wrappers/simulation_data_tools/tests/smoketest_configs/*.json`

Test files to update or remove:

- `tests/core/commands/evaluate_inference/test_evaluation.py`
- `tests/gui/test_evaluate_inference_server.py`
- `tests/core/commands/generate_data/test_generate_data.py`
- `tests/core/test_generate_data_progress.py`
- `tests/gui/test_generate_data_server.py`
- `tests/wrappers/simulation_data_tools/test_simulatorspecs.py`

Tests that only assert old behavior should be removed rather than rewritten as legacy compatibility checks. Tests that cover still-current behavior should be updated to the unified contract.

## Phase 2: Schema Contract Changes

Update schemas without changing `schema_version`:

- `ground-truth-manifest.schema.json`
  - require `outputs.gene_universe`
  - require `outputs.networks`
  - remove `outputs.global_network`
  - remove `outputs.group_networks`
- `simulator-output-manifest.schema.json`
  - require `truth.gene_universe`
  - require `truth.networks`
  - remove `truth.global_network`
  - remove `truth.group_networks`
- `simulatorspec.schema.json`
  - change truth-output keys from `global_network` / `group_networks` to `global` / `group`
  - remove `group_networks` from derivable/native artifact enums
- `benchmark-manifest.schema.json`
  - replace dataset artifact `global_network` and `group_networks_dir` with `networks`
- `preflight-report.schema.json`
  - report `truth_outputs.global` and `truth_outputs.group`
- `scenario-request.schema.json`
  - remove `group_networks` from selectable extras.
- `simulation-plan.schema.json`
  - remove `group_networks` from effective/requested extras.

Validation rules:

- Old fields must fail validation.
- `truth_outputs` keys outside the new enum must fail validation.
- `group_networks` must not be accepted as a requested extra.

### Phase 2 Implementation Results

Status: completed on 2026-05-11.

Schemas migrated:

- `andrea/catalog_simulation_data_tools/schemas/ground-truth-manifest.schema.json`
  - `outputs` now requires `gene_universe` and `networks`.
  - `outputs.global_network` and `outputs.group_networks` were removed.
- `andrea/catalog_simulation_data_tools/schemas/simulator-output-manifest.schema.json`
  - `truth` now requires `gene_universe` and `networks`.
  - `truth.global_network` and `truth.group_networks` were removed.
- `andrea/catalog_simulation_data_tools/schemas/simulatorspec.schema.json`
  - `truth_outputs` now requires `global` and `group`.
  - `global_network` and `group_networks` were removed from truth-output keys.
  - `group_networks` was removed from selectable extra enums.
  - `global_network` and `group_networks` were removed from derivable artifact enums.
- `andrea/catalog_simulation_data_tools/schemas/preflight-report.schema.json`
  - simulator entries now report `truth_outputs.global` and `truth_outputs.group`.
- `andrea/catalog_simulation_data_tools/schemas/scenario-request.schema.json`
  - `group_networks` is no longer accepted as a requested extra.
- `andrea/catalog_simulation_data_tools/schemas/simulation-plan.schema.json`
  - `group_networks` is no longer accepted in `requested_extras` or `effective_extras`.
- `andrea/catalog_simulation_data_tools/schemas/benchmark-manifest.schema.json`
  - artifact entries now require `networks`.
  - `global_network` and `group_networks_dir` were removed.

Validation run:

```bash
rg -n "global_network|group_networks|group_networks_dir" andrea/catalog_simulation_data_tools/schemas
python -m json.tool <each changed schema>
python - <<'PY'
import json
from pathlib import Path
from jsonschema.validators import validator_for
for path in [
    Path("andrea/catalog_simulation_data_tools/schemas/ground-truth-manifest.schema.json"),
    Path("andrea/catalog_simulation_data_tools/schemas/simulator-output-manifest.schema.json"),
    Path("andrea/catalog_simulation_data_tools/schemas/simulatorspec.schema.json"),
    Path("andrea/catalog_simulation_data_tools/schemas/preflight-report.schema.json"),
    Path("andrea/catalog_simulation_data_tools/schemas/simulation-plan.schema.json"),
    Path("andrea/catalog_simulation_data_tools/schemas/scenario-request.schema.json"),
    Path("andrea/catalog_simulation_data_tools/schemas/benchmark-manifest.schema.json"),
]:
    schema = json.loads(path.read_text(encoding="utf-8"))
    validator_for(schema).check_schema(schema)
PY
```

Expected temporary inconsistency:

- Existing simulator specs, wrappers, core code and tests still use the old fields until phases 3-6 migrate them.
- Full catalog validation is expected to fail between phase 2 and phase 3.

## Phase 3: Catalog And Cost Profile Migration

Update simulator specs:

- dyngen:
  - `scrna_global.truth_outputs.global = "native"`
  - `scrna_global.truth_outputs.group = "none"`
  - `scrna_grouped.truth_outputs.global = "native"`
  - `scrna_grouped.truth_outputs.group = "derivable"`
- scMultiSim:
  - `scrna_global.truth_outputs.global = "derivable"`
  - `scrna_global.truth_outputs.group = "none"`
  - `scrna_grouped.truth_outputs.global = "derivable"`
  - `scrna_grouped.truth_outputs.group = "derivable"`

Update cost profiles and validators:

- Remove `group_networks` as an extra feature.
- Add truth support features based on `truth_outputs.global` and `truth_outputs.group`.
- Keep cost-profile warnings at planning time only.

Expected outcome:

- Catalog validation passes with the new strict contract.
- No simulator still declares `global_network` or `group_networks`.

### Phase 3 Implementation Results

Status: completed on 2026-05-11.

Catalog/spec changes:

- `andrea/catalog_simulation_data_tools/simulators/dyngen/simulatorspec.json`
  - `truth_outputs.global = "native"` for both profiles.
  - `truth_outputs.group = "none"` for `scrna_global`.
  - `truth_outputs.group = "derivable"` for `scrna_grouped`.
  - grouped truth derivation artifact renamed from `group_networks` to `group`.
- `andrea/catalog_simulation_data_tools/simulators/scmultisim/simulatorspec.json`
  - `truth_outputs.global = "derivable"` for both profiles.
  - `truth_outputs.group = "none"` for `scrna_global`.
  - `truth_outputs.group = "derivable"` for `scrna_grouped`.
  - global truth derivation artifact renamed from `global_network` to `global`.
  - grouped truth derivation artifact renamed from `group_networks` to `group`.
  - `group_networks` removed from `scrna_grouped.derivable_extras`.
- `andrea/catalog_simulation_data_tools/schemas/simulatorspec.schema.json`
  - derivation artifacts now allow truth scopes `global` and `group`.
  - selectable extras still exclude `group_networks`.
- `andrea/core/shared/catalog_contracts.py`
  - `group_networks` removed from `SIMULATION_EXTRA_IDS`.

Cost-profile changes:

- `wrappers/simulation_data_tools/cost_profiles/dyngen.json`
  - grouped full benchmark profile no longer requests `group_networks`.
- `wrappers/simulation_data_tools/cost_profiles/scmultisim.json`
  - grouped dynamic benchmark profile no longer requests `group_networks`.
- `wrappers/simulation_data_tools/scripts/validate_simulator_costs.py`
  - truth outputs are no longer treated as supported extras.
- `wrappers/simulation_data_tools/scripts/benchmark_costs.py`
  - truth outputs are no longer treated as supported extras.
  - normalized output check now expects `truth/networks.csv`.

Validation run:

```bash
python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py
.venv/bin/python wrappers/simulation_data_tools/scripts/validate_simulator_costs.py
python -m json.tool <changed catalog/spec/profile JSON files>
rg -n "global_network|group_networks" andrea/catalog_simulation_data_tools/schemas andrea/catalog_simulation_data_tools/simulators wrappers/simulation_data_tools/cost_profiles andrea/core/shared/catalog_contracts.py wrappers/simulation_data_tools/scripts/validate_simulator_costs.py wrappers/simulation_data_tools/scripts/benchmark_costs.py
```

Notes:

- `validate_simulator_costs.py` currently reports no catalog `cost.json` files to validate, which is expected because simulator cost files have not been regenerated yet.
- Wrapper implementation still writes old truth paths until Phase 4.

## Phase 4: Simulator Wrapper Output Migration

Update dyngen and scMultiSim wrappers:

- Write one public `truth/networks.csv`.
- Write global rows with `context = "global"`.
- Write group rows with `context = "group:<group_id>"` when the active profile supports group truth.
- Stop writing public `truth/global_network.csv`.
- Stop writing public `truth/group_networks/*.csv`.
- Preserve useful native or intermediate files only under `provenance/raw/`, for example a `group_networks_index.tsv` if it helps debugging.
- Update `simulator-output-manifest.json` to reference `truth.networks`.

Wrapper validation:

- Every group context must map to a known group in exported metadata.
- Every source/target must exist in `truth/gene_universe.txt`.
- `score > 0`.
- `sign` is normalized.

Smoke configs:

- Require `truth/networks.csv`.
- Remove required paths for old public truth files.
- For grouped smoke tests, assert at least one `group:` context exists.

### Phase 4 Implementation Results

Status: completed on 2026-05-11.

Wrapper changes:

- `wrappers/simulation_data_tools/simulators/dyngen/run_simulator.R`
  - writes `truth/gene_universe.txt`.
  - writes unified `truth/networks.csv`.
  - writes global rows with `context=global`.
  - writes grouped rows with `context=group:<group_id>` for `scrna_grouped`.
  - no longer writes public `truth/global_network.csv` or `truth/group_networks/*.csv`.
  - reports `truth.gene_universe` and `truth.networks` in `simulator-output-manifest.json`.
- `wrappers/simulation_data_tools/simulators/scmultisim/run_simulator.R`
  - writes `truth/gene_universe.txt`.
  - writes unified `truth/networks.csv`.
  - writes global rows with `context=global`.
  - writes grouped rows with `context=group:<group_id>` for `scrna_grouped`.
  - no longer writes public `truth/global_network.csv` or `truth/group_networks/*.csv`.
  - reports `truth.gene_universe` and `truth.networks` in `simulator-output-manifest.json`.
  - requires `dynamic_grn.enabled=true` for `scrna_grouped`, because grouped truth depends on native `cell_specific_grn`.

Smoke-test changes:

- All simulator smoke configs now require `truth/networks.csv` and `truth/gene_universe.txt`.
- Grouped smoke configs declare `required_truth_context_prefixes: ["group:"]`.
- Simulator smoke-test validation now checks those declared context prefixes in `truth/networks.csv`.
- `group_networks` was removed from scMultiSim smoke `effective_extras`.

Documentation updated:

- dyngen and scMultiSim `integration_decisions.md` now describe the unified truth output behavior.

Validation run:

```bash
python -m json.tool wrappers/simulation_data_tools/tests/schemas/smoketest.config.schema.json
for f in wrappers/simulation_data_tools/tests/smoketest_configs/*.json; do python -m json.tool "$f" >/dev/null; done
python wrappers/simulation_data_tools/scripts/validate_smoketest_configs.py
Rscript -e "invisible(parse(file='wrappers/simulation_data_tools/simulators/dyngen/run_simulator.R')); invisible(parse(file='wrappers/simulation_data_tools/simulators/scmultisim/run_simulator.R'))"
rg -n "truth/global_network|truth/group_networks|global_network|\\bgroup_networks\\b" wrappers/simulation_data_tools/simulators/dyngen/run_simulator.R wrappers/simulation_data_tools/simulators/scmultisim/run_simulator.R wrappers/simulation_data_tools/tests/smoketest_configs wrappers/simulation_data_tools/tests/schemas/smoketest.config.schema.json wrappers/simulation_data_tools/scripts/run_smoketests.py
```

Notes:

- Full Docker smoke tests are intentionally left for the later end-to-end phase because generate-data and evaluate-inference still need to migrate to the unified truth contract.

## Phase 5: Generate-Data Core Migration

Update generate-data internals:

- Build dataset ground-truth manifests with `outputs.networks`.
- Package `truth/networks.csv` from wrapper stages.
- Remove `include_group_networks`.
- Remove logic that deletes or keeps `truth/group_networks` based on requested extras.
- Treat group truth as part of the profile contract, not as an optional extra.
- Report benchmark artifacts as `networks`.
- Ensure `scrna_grouped` datasets contain group contexts when the simulator capability declares `group != "none"`.

Planning behavior:

- Do not expose `group_networks` in requested extras.
- If a selected simulator cannot produce required profile truth, fail during planning/config validation.
- Planning warnings remain separate from execution warnings.

### Phase 5 Implementation Results

Status: completed on 2026-05-11.

- `generate-data` now builds `ground-truth-manifest.json` with `outputs.gene_universe` and `outputs.networks`.
- Stage packaging copies the unified `truth/` directory from simulator output and no longer rewrites `gene_universe.txt` from expression data.
- `include_group_networks` and all keep/delete logic for `truth/group_networks/` were removed.
- `scrna_grouped` now requires simulator `truth_outputs.group != "none"` during simulator selection.
- Wrapper output validation now checks `truth/networks.csv` for required columns, positive nonzero scores, valid signs, non-empty contexts, no self-loops, genes inside `truth/gene_universe.txt`, and required context families.
- Grouped datasets must contain at least one `group:<id>` context, and exported group context IDs are checked against `extras/groups.tsv` when available.
- Benchmark artifacts now report `gene_universe` and `networks` paths.
- `dyngen` public truth derivation now filters package self-loops before writing `truth/networks.csv`.
- Validation passed:
  - `python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py`
  - `python wrappers/simulation_data_tools/scripts/validate_smoketest_configs.py`
  - `PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/core/commands/generate_data tests/core/test_generate_data_progress.py tests/wrappers/simulation_data_tools/test_simulatorspecs.py`

## Phase 6: Evaluate-Inference Migration

Update evaluate-inference:

- Load `truth/networks.csv` from `ground-truth-manifest.outputs.networks`.
- Group truth rows by exact `context`.
- Match inferred rows to truth by exact `context`.
- Keep existing metrics unchanged.
- Remove all loader support for `outputs.global_network` and `outputs.group_networks`.
- Do not assume the only valid contexts are `global` and `group:*`.

GUI evaluate-inference:

- When unpacking uploaded benchmark zips, freeze `truth/networks.csv`.
- Reject uploaded ground-truth manifests without `outputs.networks`.
- Do not search for old truth paths.

View behavior:

- Keep the current chart behavior for known contexts:
  - `global`: bar chart.
  - `group:<id>`: heatmap when the number of groups is reasonable.
- Add a generic fallback panel or table section for non-global, non-group contexts so future context families are not silently hidden.
- Do not hardcode a two-panel assumption into the report structure.

Tests:

- Update fixtures to use `truth/networks.csv`.
- Remove tests that assert legacy truth loading.

### Phase 6 Implementation Results

Status: completed on 2026-05-11.

- `evaluate-inference` now requires `ground-truth-manifest.outputs.networks`.
- The evaluator loads one public truth table, groups rows by exact `context`, and matches inferred rows to truth with exact context equality.
- Loader support for `outputs.global_network` and `outputs.group_networks` was removed.
- Truth CSV validation now requires the unified columns `source,target,score,sign,evidence,context`, positive scores, non-empty evidence/context, no self-loops, and genes inside `outputs.gene_universe`.
- The reusable evaluation view still renders `global` contexts as bars and `group:<id>` contexts as heatmaps.
- Non-global, non-group contexts are now shown in a generic `Other Contexts` table so future context families are not hidden.
- The evaluate-inference GUI now discovers and freezes `truth/networks.csv` only; old truth paths are not searched or copied.
- Validation passed:
  - `PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/core/commands/evaluate_inference tests/gui/test_evaluate_inference_server.py tests/cli/test_evaluate_inference_cli.py`
  - `python -m compileall -q andrea/core/commands/evaluate_inference andrea/gui/evaluate_inference tests/core/commands/evaluate_inference tests/gui/test_evaluate_inference_server.py`

## Phase 7: Infer-Network And Compare-Networks Review

Infer-network mostly consumes inferred networks, not simulator truth, but review:

- Graph exports must preserve the exact `context` value.
- Remove public `context_scope` from GraphML/GEXF exports and related GUI copy, or keep any classification strictly internal to visualization code.
- No code should assume group truth lives in a directory.

Compare-networks mostly consumes inferred run reports, but review:

- Any optional evaluation overlay must continue working after evaluation reports point to unified truth.
- No visual or parser should assume group truth files exist.
- Context labels should be produced by a generic helper that knows common prefixes (`group:`, later `cell:`) but gracefully displays unknown context families.
- Distance and edge-difference logic must continue comparing by exact context unless the user explicitly selects networks from different contexts.

### Phase 7 Implementation Results

Status: completed on 2026-05-12.

- Infer-network GraphML and GEXF exports now preserve only the exact public `context` edge attribute; `context_scope` is no longer exported.
- The Cytoscape helper script now maps line styles from exact `context` values instead of a public `context_scope` attribute.
- Infer-network GUI copy for external graph exports now describes styling/filtering by exact `context`.
- Compare-networks view helpers now label known context prefixes generically:
  - `global`
  - `group:<id>` as `group <id>`
  - `cell:<id>` as `cell <id>`
  - unknown context families as their exact raw value
- Compare-network distance generation continues to group automatic distance maps by exact `(source_id, context, level)`.
- Ordered edge differences remain able to compare selected networks from any context because the selection is keyed by exact source/tool/context/level and uses common genes only.
- Validation passed:
  - `PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/core/commands/compare_networks tests/gui/test_compare_networks_server.py tests/core/commands/infer_network tests/gui/test_infer_network_server.py`
  - `python -m compileall -q andrea/core/commands/infer_network/commons/network_exports.py andrea/gui/infer_network/server.py andrea/core/commands/compare_networks andrea/gui/compare_networks`
  - A temporary export smoke check confirmed GraphML/GEXF preserve `cell:cell_a`, do not emit `context_scope`, and the generated Cytoscape script compiles.

## Phase 8: GUI Generate-Data Migration

Update generate-data GUI:

- Remove `group_networks` from selectable extra outputs.
- Show public truth outputs as `networks.csv`.
- For grouped profiles, explain that group-specific truth is represented as `group:<id>` contexts inside `truth/networks.csv`.
- Update result explorer labels and zip-bundling checks.

No simulator-specific hardcoding should be introduced.

### Phase 8 Implementation Results

Status: completed on 2026-05-12.

- Generate-data GUI bootstrap no longer adds truth output capabilities to selectable extras.
- `group_networks` is no longer exposed as an optional generated extra.
- Result bundle discovery now includes `truth/networks.csv` and `truth/gene_universe.txt` in compact mode.
- Result bundle discovery no longer searches `truth/global_network.csv` or `truth/group_networks/`.
- Simulator info now displays truth capabilities as `truth/networks.csv` context families:
  - `global`
  - `group:<id>`
  - reserved/future `cell:<id>`
- Simulator list summaries now describe available truth outputs from the current `truth_outputs` keys (`global`, `group`, etc.) rather than old file names.
- Validation performed:
  - `python -m compileall -q andrea/gui/generate_data tests/gui/test_generate_data_server.py`
  - `node --input-type=module --check < andrea/gui/generate_data/static/app/main.js`
  - `PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/gui/test_generate_data_server.py` (`2 skipped` in the current environment because optional GUI test dependencies are incomplete)

## Phase 9: Documentation, Playbooks And Scaffolds

Update:

- `wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md`
- simulator scaffold templates
- catalog docs and examples
- any README snippets that mention old truth files

Required wording:

- All public truth networks are exported through `truth/networks.csv`.
- `context` determines whether an edge belongs to global, group or future cell truth.
- Simulators may preserve native outputs under provenance, but public consumers must not depend on those native files.

### Phase 9 Implementation Results

Status: completed on 2026-05-12.

- `wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md` now documents the unified public truth contract:
  - all public truth networks use `truth/networks.csv`
  - `context` determines global, group and future cell truth
  - simulator-native outputs may be preserved under provenance but are not public consumer inputs
- Simulator truth-output capability wording now uses context families:
  - `global`
  - `group`
- Phase 2 wrapper requirements and the normalized output tree now require:
  - `truth/networks.csv`
  - `truth/gene_universe.txt`
- The truth-output section now documents the unified CSV columns, positive-score/sign convention, `global` and `group:<group_id>` contexts, and manifest keys `outputs.networks` / `truth.networks`.
- `wrappers/simulation_data_tools/scripts/scaffold_simulator.py` now generates draft integrations with:
  - `truth_outputs.global` and `truth_outputs.group`
  - `truth/networks.csv` and `truth/gene_universe.txt` as required smoke-test outputs
  - `required_truth_context_prefixes`
  - README wording for the unified truth contract
- `wrappers/simulation_data_tools/README.md` now includes the normalized output contract and public truth file semantics.
- Validation performed:
  - `python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py`
  - `python wrappers/simulation_data_tools/scripts/validate_smoketest_configs.py`
  - `python -m compileall -q wrappers/simulation_data_tools/scripts/scaffold_simulator.py`
  - scaffold generation into `/tmp/andrea_phase9_*` with active catalog and smoke-test files
  - search confirmed the updated playbook, README and scaffold no longer mention old public truth paths or old truth-output keys.

## Phase 10: Regression And Acceptance

Run at least:

- simulator spec validation
- simulator cost validation
- simulator smoke tests
- generate-data core tests
- generate-data GUI tests
- evaluate-inference core tests
- evaluate-inference GUI tests
- compare-networks tests affected by evaluation overlays

Acceptance criteria:

- No tracked code, schema, spec, test or playbook references `truth/global_network.csv` as public output.
- No tracked code, schema, spec, test or playbook references `truth/group_networks/` as public output.
- `group_networks` is not accepted as a requested simulation extra.
- Public graph exports do not add `context_scope`, `context_type`, `context_id` or equivalent derived context fields.
- dyngen and scMultiSim grouped datasets generate `truth/networks.csv` with both `global` and `group:<id>` contexts.
- evaluate-inference can evaluate global and grouped datasets using only `truth/networks.csv`.
- evaluate-inference and compare-networks do not hide valid unknown context families from tables/raw outputs.
- Old manifest shapes fail clearly.

### Phase 10 Implementation Results

Status: completed on 2026-05-12.

Fixes made during acceptance:

- Grouped simulator smoketest configs now require both `global` and `group:` contexts in `truth/networks.csv`.
- `validate_simulator_costs.py` and `benchmark_costs.py` now add the repository root to `sys.path` before importing ANDREA modules, matching the other simulator maintenance scripts.
- dyngen and scMultiSim decision logs no longer spell out legacy public truth paths; they describe them generically as legacy split public truth files.

Validation and regression commands:

```bash
python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py
python wrappers/simulation_data_tools/scripts/validate_smoketest_configs.py
PYENV_VERSION=3.10.7 pyenv exec python wrappers/simulation_data_tools/scripts/validate_simulator_costs.py
PYENV_VERSION=3.10.7 pyenv exec python wrappers/simulation_data_tools/scripts/run_smoketests.py
PYENV_VERSION=3.10.7 pyenv exec python wrappers/simulation_data_tools/scripts/run_smoketests.py --skip-build
PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/core/commands/generate_data tests/core/test_generate_data_progress.py tests/gui/test_generate_data_server.py tests/wrappers/simulation_data_tools/test_simulatorspecs.py
PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/core/commands/evaluate_inference tests/gui/test_evaluate_inference_server.py tests/cli/test_evaluate_inference_cli.py
PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/core/commands/compare_networks tests/gui/test_compare_networks_server.py
PYENV_VERSION=3.10.7 pyenv exec python -m pytest tests/core/commands/infer_network tests/gui/test_infer_network_server.py
git diff --check
```

Results:

- Simulator spec validation: 2 valid, 0 invalid.
- Simulator smoketest config validation: 10 valid, 0 invalid.
- Simulator cost validation: no catalog simulator `cost.json` files found; nothing to validate.
- Simulator smoke tests: dyngen passed, scMultiSim passed.
- Simulator smoke tests after stricter grouped context checks: dyngen passed, scMultiSim passed.
- Generate-data regression: 37 passed, 2 skipped.
- Evaluate-inference regression: 7 passed, 3 skipped.
- Compare-networks regression: 8 passed, 4 skipped.
- Infer-network/export regression: 33 passed, 2 skipped.
- `git diff --check`: passed.

Acceptance checks:

- Static search found no unexpected old public truth paths outside this plan.
- Static search found no unexpected `group_networks` requested-extra references outside this plan.
- Static search found no public derived context fields (`context_scope`, `context_type`, `context_id`) outside this plan.
- Schema validation rejects old ground-truth manifests with `outputs.global_network` / `outputs.group_networks` and requires `outputs.networks`.
- Schema validation rejects old simulator-output manifests with `truth.global_network` / `truth.group_networks` and requires `truth.networks`.
- Schema validation rejects `group_networks` as a requested simulation extra.
