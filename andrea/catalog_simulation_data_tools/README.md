# Simulation Data Tool Catalog

Runtime catalog consumed by `andrea generate-data`.

## Layout

```text
schemas/
input_specs/
  <input_id>.json
simulators/
  <simulator_id>/
    simulatorspec.json
    cost.json
```

## Simulator Input Specs

`input_specs/<input_id>.json` defines a reusable simulator-side input file:

- semantic meaning
- format and accepted extensions
- minimal example
- basic validation notes
- TSV columns when applicable

Simulator specs do not define these details inline. They only reference input
ids from `simulatorspec.extra_inputs` and explain how that simulator uses the
file.

## Simulator-Specific Usage

Each `simulatorspec.json` declares:

- `extra_inputs.required`: files always required by the simulator.
- `extra_inputs.optional`: files consumed when present but not required by a
  particular configuration.
- `extra_inputs.conditional_required`: files required only when a selected
  profile, requested extra, native output or parameter value activates the rule.

Generated benchmark extras such as `groups`, `tf_list` or `pseudotime` are
outputs requested from simulators. They are not simulator-side input specs.

## Truth Contexts

All public simulator truth networks are exported through one normalized file:

```text
truth/networks.csv
```

The `context` column distinguishes the public truth level:

- `global`: dataset-level regulatory truth.
- `group:<group_id>`: group-specific regulatory truth.
- `cell:<cell_id>`: cell-specific regulatory truth.

Canonical profile requirements are cumulative:

- `scrna_global` requires `global`.
- `scrna_grouped` requires `global` and at least one `group:*` context.
- `scrna_cell_specific` requires `global`, at least one `group:*` context and
  at least one `cell:*` context.

Each `profile_capabilities.<profile>` entry in a simulator spec must explain
these semantics through `truth_contexts`. For each `global`, `group` and `cell`
entry, the spec records whether the context is `native`, `derivable` or `none`,
which upstream artifacts are used, which upstream settings are activated, how
public rows are generated, how `score` and `sign` are computed, and any
limitations.

`truth_outputs` remains the compact machine-readable summary. `truth_contexts`
is the audit trail that makes those machine claims understandable.

## Validation

```bash
make validate-simulation-input-specs
make validate-simulatorspecs
make validate-generation-catalog
```
