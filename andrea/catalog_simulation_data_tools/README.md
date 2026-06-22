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
  semantic capability, requested extra, native output or parameter value
  activates the rule.

Generated benchmark extras such as `groups`, `tf_list` or `pseudotime` are
outputs requested from simulators. They are not simulator-side input specs.

## Semantic Dataset Model

`generate-data` models benchmark datasets with explicit axes. A scenario is not
defined by one combined profile string; it is defined by:

- `measurement`: what the matrix measures. Initial value: `rna_expression`.
- `resolution`: biological/experimental resolution, such as `bulk`,
  `single_cell`, `spatial`, `pseudo_bulk`, `mixed` or `unknown`.
- `column_kind`: what each expression column represents, such as `samples`,
  `cells`, `timepoints`, `perturbations`, `spots`, `metacells` or
  `conditions`.
- `experimental_design`: the benchmark design, such as `observational`,
  `steady_state`, `perturbational`, `time_series`, `trajectory` or
  `differentiation`.

Regulatory truth requirements are a separate axis. Public truth context
families are:

- `global`: one dataset-level GRN.
- `group`: one GRN per group of expression columns, exported as
  `context=group:<group_id>`.
- `column`: one GRN per expression column, exported as
  `context=column:<column_id>`.

The meaning of `column:<column_id>` comes from `column_kind`. For example, when
`column_kind=cells`, column truth is cell-level truth; when
`column_kind=timepoints`, it is timepoint-level truth. `groups.tsv` groups
expression columns, not only cells.

## Truth Contexts

All public simulator truth networks are exported through one normalized file:

```text
truth/networks.csv
```

The `context` column distinguishes the public truth level:

- `global`: dataset-level regulatory truth.
- `group:<group_id>`: group-specific regulatory truth.
- `column:<column_id>`: expression-column-specific regulatory truth.

Scenario truth requirements are cumulative. If `truth_requirements.contexts`
contains `column`, `global` and any requested `group` truth must still be
present unless the scenario explicitly omits them. Each simulator capability
records whether every declared context family is `native`, `derivable` or
`none`, which upstream artifacts are used, which upstream settings are
activated, how public rows are generated, how `score` and `sign` are computed,
and any limitations.

The compact machine-readable summary and the audit trail must agree: every
claimed context family needs evidence and a wrapper rule.

## Validation

```bash
make validate-simulation-input-specs
make validate-simulatorspecs
make validate-generation-catalog
```
