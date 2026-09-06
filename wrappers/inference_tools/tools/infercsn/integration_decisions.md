# inferCSN CRAN Core Integration Decisions

## Sources Reviewed

- Primary method publication: <https://doi.org/10.1038/s41540-025-00564-4>
- Official CRAN package page: <https://CRAN.R-project.org/package=inferCSN>
- CRAN 1.2.0 source tarball: <https://cran.r-project.org/src/contrib/inferCSN_1.2.0.tar.gz>
- CRAN 1.2.0 public documentation for `inferCSN()` and `network_sift()`.

The runtime contract below is based on the public CRAN 1.2.0 package, which is
also the version installed in the image. It does not depend on an untracked
local source checkout or on private glue from the publication workflow.

## Integration Boundary

The publication describes a broader dynamic, cell-type/state-specific
workflow. ANDREA does not claim to reproduce that complete workflow. The
catalog entry integrates the public CRAN sparse-regression core:

- `inferCSN::inferCSN()` is called once for each physical task and returns one
  regulator-target-weight table.
- `sift_method="none"` preserves that table and is the default.
- `sift_method="max"` optionally calls the public
  `inferCSN::network_sift(method="max")` function once on the inferred table.

There is no wrapper-defined inference algorithm, hidden state windowing, or
additional biological hierarchy. The integrated method therefore appears as
`inferCSN CRAN core` in the catalog so it cannot be mistaken for the complete
workflow described in the paper.

## Logical and Physical Execution

### `global`

One logical `global` run creates one physical task. The physical task receives
the complete supplied expression matrix, executes with
`execution.mode="global"`, calls `inferCSN()` once, and emits one network whose
context is `global`.

### `group_emulated`

`group_emulated` is ANDREA orchestration around the same global core:

1. ANDREA reads `groups.tsv`.
2. ANDREA creates one expression matrix for each distinct group label.
3. ANDREA creates one physical task per group and gives each task
   `execution.mode="global"`.
4. Each task calls `inferCSN()` once and emits a network with physical context
   `global`.
5. ANDREA assigns that task's logical `group:<label>` context and merges the
   group networks.

Consequently, a logical grouped run produces at most one inferred network per
input group. inferCSN does not create extra networks or temporal windows inside
a physical task. `group_emulated` is not advertised as upstream-native grouped
inference.

## ToolSpec Evidence Ledger

### Identity and provenance

- `schema_version`: `1.0`, the current ANDREA ToolSpec contract.
- `id`: `infercsn`, the stable machine identifier.
- `name`: `inferCSN CRAN core`, explicitly identifying the executable method
  boundary.
- `publication`: <https://doi.org/10.1038/s41540-025-00564-4>, the primary
  method publication.
- `first_author`: `Xiong Li`.
- `year`: `2025`.
- `implementation_url`: <https://CRAN.R-project.org/package=inferCSN>.
- `docker_image`: `adriansegura99/inference-tools_infercsn:1.0.0`, following
  the inference-wrapper image convention.

### Method classification

- `accepts`: `cells`; the integrated package is intended for single-cell
  expression matrices.
- `assumes`: `scrna_specific`; this is not presented as a generic bulk
  regression method.
- `method_keywords`: `single_cell`, `sparse_regression`,
  `l0_regularization`, `cell_state`, and `directed`.
- `execution_capabilities`: `global` and `group_emulated`.

The public `inferCSN()` matrix method consumes one matrix and returns one
network table. That directly supports `global`; ANDREA can safely obtain
group-specific networks by invoking the same global operation independently
on each group partition.

### Extra inputs

- `tf_list` is optional with `delivery="runtime"`. When supplied, the wrapper
  validates its identifiers against the expression genes and passes it as
  `regulators`. When absent, the `regulators` argument is omitted so the public
  all-gene default is preserved.
- `groups` is conditionally required only when the logical execution mode is
  `group_emulated`, with `delivery="orchestration_only"`. ANDREA consumes it to
  partition expression columns; it is not mounted in a physical child.

No target list is part of this integration. The wrapper omits `targets`, so all
genes retained in the supplied expression matrix are candidate targets, as in
the public default.

### Parameters

The wrapper exposes the documented public inference controls and one public
post-filter choice:

- `penalty`: `L0`, `L0L1`, or `L0L2`; default `L0`.
- `cross_validation`: boolean; default `false`.
- `seed`: R integer; default `1`.
- `n_folds`: integer greater than or equal to `2`; default `5`.
- `subsampling_method`: `sample`, `meta_cells`, or `pseudobulk`; default
  `sample`.
- `subsampling_ratio`: value in `(0, 1]`; default `1`, which retains the full
  matrix.
- `r_squared_threshold`: value in `[0, 1]`; default `0`.
- `sift_method`: `none` or `max`; default `none`.

`cores` is deliberately not a scientific parameter. ANDREA supplies the
planner-selected thread count and the wrapper maps it to the public `cores`
argument. Common BLAS/OpenMP worker variables are fixed to one to prevent
unplanned nested parallelism.

### Outputs

The public inference table contains `regulator`, `target`, and signed `weight`.
The wrapper converts it to the ANDREA network contract as follows:

- `source` = `regulator`;
- `target` = `target`;
- `score` = `abs(weight)`;
- `sign` = `+` or `-`, derived from `weight`;
- `evidence` = `association`;
- physical `context` = `global`.

Exact zero weights and self-loops are omitted. No extra normalization is
applied: the score remains the magnitude of the coefficient produced by the
public core. ANDREA, rather than the wrapper, replaces the physical context
when finalizing a `group_emulated` logical run.

A valid inference may contain no retained edges. In that case the wrapper
writes a header-only `network.csv`, preserves header-only upstream tables, and
completes successfully. An empty network is data, not an execution failure.

### Progress and auxiliary artifacts

The public core does not expose a stable fine-grained callback. The ToolSpec
therefore declares `progress.kind="none"`, while the wrapper still writes
coarse lifecycle states to `progress.json`.

The wrapper preserves:

- `infercsn.log`;
- `raw/infercsn_inferred_network.tsv`, directly after `inferCSN()`;
- `raw/infercsn_network.tsv`, after the optional public `max` filter;
- standardized `network.csv`.

## Runtime Implementation

- Base image: `rocker/r-ver:4.4.1`.
- Package installation: `inferCSN` is pinned to `1.2.0` with
  `remotes::install_version()` and checked during the image build.
- The wrapper requires the canonical `expression.tsv`, `params.json`,
  `execution.json`, output directory, and thread count supplied by ANDREA.
- `execution.json` must identify a physical `global` task. Logical emulation is
  resolved by ANDREA before container launch.
- ANDREA expression is genes by cells; the wrapper transposes it to the
  cells-by-genes matrix expected by the public function.
- Zero-variance genes are removed before inference. If fewer than two usable
  genes or regulators remain, the wrapper returns a valid empty network.
- The wrapper invokes `inferCSN()` exactly once in the inference phase and, if
  selected, invokes the public `max` filter once afterward.

## Verification Expectations

The integration tests should establish all of the following:

- both logical `global` and `group_emulated` are accepted by the catalog;
- a physical wrapper invocation accepts only `execution.mode="global"`;
- `groups.tsv` is required for logical emulation but never mounted in the
  child;
- the optional TF list is delivered to the child;
- each emulated child receives only the columns assigned to its group;
- each child emits only physical `global` context;
- ANDREA produces one logical `group:<label>` network per successful child;
- `none` preserves the direct CRAN result and `max` calls the public filter;
- a zero-edge result remains a successful, header-only network.

## Known Limitations

- This catalog entry is the public CRAN 1.2.0 sparse-regression core, not the
  complete dynamic workflow described by the publication.
- The flat `groups.tsv` contract represents one context axis. A study with a
  higher-level biological partition and within-partition states must create
  the higher-level datasets first, then use the within-partition state as the
  ANDREA group label.
- The wrapper does not expose `targets`; every retained expression gene is a
  candidate target.
- A `group_emulated` run performs independent fits. It does not share
  information or impose smoothness across groups.
