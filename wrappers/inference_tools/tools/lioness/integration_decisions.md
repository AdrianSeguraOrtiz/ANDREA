# LIONESS Integration Decisions

Phase: 3 complete. Wrapper, Dockerfile, ToolSpec, template-map registration, smoketest config, and final validation are complete.

## Evidence Reviewed

- `wrappers/inference_tools/tools/lioness/papers/LIONESS.pdf`
- `wrappers/inference_tools/tools/lioness/papers/LIONESS.txt`
- `wrappers/inference_tools/tools/lioness/repo/lionessR/README.md`
- `wrappers/inference_tools/tools/lioness/repo/lionessR/DESCRIPTION`
- `wrappers/inference_tools/tools/lioness/repo/lionessR/R/lioness.R`
- `wrappers/inference_tools/tools/lioness/repo/lionessR/R/netFun.R`
- `wrappers/inference_tools/tools/lioness/repo/lionessR/NAMESPACE`
- `wrappers/inference_tools/tools/lioness/additional_files/lionessR.txt`

The paper PDF was converted to `papers/LIONESS.txt` with `make prepare-tool-papers TOOL=lioness`.

## Installation Source

Chosen source: Bioconductor package `lionessR` version `1.26.0`.

Evidence:
- `additional_files/lionessR.txt` lists `Version 1.26.0`, `Repository Bioconductor 3.23`, `Date/Publication 2026-05-17`, `Depends R (>= 3.6.0)`, imports `stats`, `SummarizedExperiment`, and `S4Vectors`, and git source `https://git.bioconductor.org/packages/lionessR`.
- The local upstream GitHub snapshot under `repo/lionessR` is older (`DESCRIPTION` version `1.0`) and documents `devtools::install_github("kuijjerlab/lionessR")`.

Rationale:
- The integrator explicitly preferred the official Bioconductor release. The Bioconductor package is a public package source and gives a versioned release, so it is preferable to the older local GitHub snapshot for runtime installation.

Implementation:
- `wrappers/inference_tools/tools/lioness/Dockerfile` uses `bioconductor/bioconductor_docker:RELEASE_3_23`, installs `lionessR`, and asserts `packageVersion("lionessR") == "1.26.0"`.
- The runtime image does not copy or depend on local `repo/`; only the wrapper script and generated `run_tool.sh` are copied into `/app`.

Uncertainty:
- None after Phase 2 smoke build; the container resolved `lionessR 1.26.0` from Bioconductor 3.23.

## Upstream Public Entrypoints and Modes

### Exposed

Chosen public entrypoint: `lionessR::lioness(x, f = netFun)` from Bioconductor `lionessR 1.26.0`, using the default Pearson aggregate network function `lionessR::netFun`.

Evidence:
- `additional_files/lionessR.txt` documents usage `lioness(x, f = netFun)`.
- `additional_files/lionessR.txt` states that `x` may be a numeric matrix with samples in columns or a `SummarizedExperiment`.
- `additional_files/lionessR.txt` states that `f` defaults to Pearson correlation and that edge weights are accessed through the `lioness` assay of the returned `SummarizedExperiment`.
- `repo/lionessR/R/lioness.R` shows the older implementation calling `f(x)` for the all-sample aggregate network and `f(x[, -i])` for each leave-one-sample-out network.
- `repo/lionessR/R/netFun.R` defines the default function as `stats::cor(t(x), method="pearson")`.

Wrapper contract:
- The wrapper will pass ANDREA `expression.tsv` as a numeric expression matrix with genes as rows and cells as columns.
- The wrapper will always call `lionessR::lioness(..., f = lionessR::netFun)`.
- The wrapper will export one network per cell as `context=cell:<cell_id>`.

### Not Exposed

- Arbitrary upstream `f` callbacks are not exposed. Evidence: both repo and Bioconductor docs define `f` as a function argument that can be substituted with any function returning a complete weighted adjacency matrix. Rationale: arbitrary function-valued callbacks are not serializable in ToolSpec, not portable through the GUI/CLI contract, and would create unbounded dependency and provenance requirements. Since the current integration exposes only the package-native Pearson default, the ToolSpec has no user-facing params.
- The older GitHub `lioness()` data-frame return shape is not the runtime contract. Evidence: `repo/lionessR/R/lioness.R` returns a data frame with `reg`, `tar`, and one column per sample; Bioconductor 1.26.0 documentation states that `lioness()` returns a `SummarizedExperiment`. Rationale: the integration targets the official Bioconductor package requested by the integrator.
- `global` execution is not exposed. Evidence: LIONESS is designed to estimate sample-specific networks from aggregate networks, and the public `lioness()` entrypoint returns sample-specific networks. Rationale: although the default `netFun()` can compute the all-sample Pearson aggregate network, exposing that as `global` would mirror a helper aggregate function rather than the LIONESS method.
- `group_native` is not exposed. Evidence: no upstream argument consumes cell groups or produces one network per group.
- `group_emulated` is not exposed. Rationale: LIONESS relies on the full background sample set to infer each sample-specific network. Splitting expression by group before running LIONESS would change the background set and is not the desired grouped behavior for this integration.
- `group_aggregated` is exposed as an ANDREA-managed execution mode, not as an upstream LIONESS mode. It runs the cell-native LIONESS output and lets ANDREA average signed cell-level edge effects within each group.

## Execution Capability Decisions

Chosen capabilities:
- `cell_native`
- `group_aggregated`

Evidence:
- The paper title and summary describe "Estimating Sample-Specific Regulatory Networks".
- The paper states that LIONESS estimates individual sample networks by applying linear interpolation to aggregate network predictions.
- Bioconductor docs describe the package as reconstructing single-sample networks.
- `lionessR::lioness()` returns sample-specific edge weights through its `lioness` assay.

Rationale:
- In ANDREA, cell-specific inferred networks are represented with `execution.mode=cell_native` and `context=cell:<cell_id>`.
- `group_aggregated` is appropriate because ANDREA can aggregate the resulting cell-native networks into group contexts using the standardized `groups.tsv` input.

Uncertainty:
- LIONESS is a generic single-sample network method, not exclusively a single-cell method. The current ANDREA context model for this canonical profile is cell-specific, so the ToolSpec accepts `cells` only. Extending this to sample-native contexts would require a future platform-level context extension.

## Input Semantics

Primary input:
- `expression.tsv`, genes in rows and cells in columns.

Required extra inputs:
- None.

Optional extra inputs:
- None.

Conditional required inputs:
- `groups` when `execution.mode=group_aggregated`.

Evidence:
- `lionessR::lioness()` requires only `x` and optional function `f`.
- `netFun(x)` requires only the expression matrix.
- No upstream group, TF-list, prior network, pseudotime, or phenotype input is consumed by the selected entrypoint.

Rationale:
- `groups.tsv` is not passed to lionessR. It is needed only by ANDREA after the cell-native run to aggregate cell-level networks into group-level networks.

No new input spec is needed.

## Parameter Contract

Chosen value:
- `params: {}`

Evidence:
- `additional_files/lionessR.txt` documents `f` as the network reconstruction function and states it defaults to Pearson correlation.
- `repo/lionessR/R/netFun.R` implements `stats::cor(t(x), method="pearson")`.
- The paper evaluates LIONESS with Pearson, PANDA, mutual information, and CLR, and states the mathematical framework is independent of the aggregate network method.

Rationale:
- The upstream `f` argument is function-valued, so it cannot be represented directly in a stable JSON/GUI contract.
- The only package-native preset exposed in this integration is the documented default Pearson function, so a one-option user parameter would add UI/API noise without changing behavior.
- The wrapper hardcodes `lionessR::netFun`, making this contract explicitly "LIONESS with Pearson aggregate network".

Uncertainty:
- Future integrations could add explicit, separately installed presets for PANDA, MI, or CLR if each preset has a reproducible runtime dependency set and output convention. They should not be represented as arbitrary user callbacks.

## Output Mapping

Upstream output:
- Bioconductor `lionessR::lioness()` returns a `SummarizedExperiment`; `rowData` stores regulators and targets, `colData` stores samples, and the `lioness` assay stores sample-specific edge weights.
- The older repo implementation returns a data frame with `reg`, `tar`, and one column per sample.

ANDREA `network.csv` mapping:
- `source`: regulator/gene from upstream `reg`.
- `target`: target/gene from upstream `tar`.
- `context`: `cell:<cell_id>` for cell-native output.
- `score`: `abs(weight)`.
- `sign`: `+` if weight > 0, `-` if weight < 0, `?` only if a nonzero edge has no reliable sign.
- `evidence`: `association`.

Edge convention:
- The selected Pearson preset is an undirected association network.
- Export one row per unordered gene pair.
- Exclude self-loops.
- Filter zero-magnitude edges before writing `network.csv`.

Evidence:
- `netFun()` computes Pearson correlation on gene expression rows, producing a symmetric gene-by-gene adjacency matrix.
- The paper examples show positive and negative LIONESS edge weights for Pearson and MI, and interpret LIONESS edge weights as sample-specific edge weights.
- The playbook requires positive score magnitudes with direction stored only in `sign`.

Rationale:
- Pearson-based LIONESS scores are signed association weights, so the sign must not remain embedded in `score`.
- LIONESS interpolation can produce edge weights outside the original Pearson correlation range; raw magnitude should be preserved and downstream normalization left to ANDREA merge logic.

Implementation:
- `run_tool.R` extracts the Bioconductor `SummarizedExperiment` assay named `lioness` when available, with a first-assay fallback.
- It detects source/target columns from `rowData` using `reg`/`tar` and common source/target aliases, with a two-column fallback for older data-frame-shaped returns.
- The wrapper keeps a data-frame fallback for older return shapes only as an internal defensive extractor; the runtime installation target remains Bioconductor `lionessR 1.26.0`.

Uncertainty:
- None for the selected Bioconductor runtime after smoketest execution.

## Progress Contract

Chosen value:
- `progress.kind = "none"`

Evidence:
- `lionessR::lioness()` exposes no documented progress callback.
- The older implementation loops over samples internally but does not emit a stable machine-readable counter.

Rationale:
- The wrapper should write coarse `progress.json` lifecycle states such as loading, running, exporting, and done.

Implementation:
- `progress.json` is written at coarse phases: init, load input, inference, export, and done/error.
- No sample-level progress is claimed because `lionessR::lioness()` does not expose a progress callback.

Uncertainty:
- None.

## Auxiliary Artifacts

Chosen artifacts:
- `lioness.log`
- `raw/lioness_result.rds`
- `raw/lioness_weights.tsv.gz`

Evidence:
- Bioconductor docs state that the raw result is a `SummarizedExperiment`.
- The ANDREA wrapper contract requires preserving raw method outputs and logs where possible.

Rationale:
- The RDS preserves the exact upstream object.
- The gzipped long-form table makes the raw upstream edge weights inspectable without R.
- The log captures package version, session info, warnings, and stdout/stderr.

Implementation:
- The wrapper writes exactly the declared artifacts:
  - `lioness.log`
  - `raw/lioness_result.rds`
  - `raw/lioness_weights.tsv.gz`

Uncertainty:
- None.

## ToolSpec Field Evidence Summary

| Field | Chosen value | Evidence paths | Rationale | Uncertainty |
|---|---|---|---|---|
| `schema_version` | `1.0` | Catalog policy | Current platform schema version. | None. |
| `id` | `lioness` | User request; scaffold path | Stable lowercase tool id. | None. |
| `name` | `LIONESS` | Paper title and package docs | Public method name. | None. |
| `publication` | `https://doi.org/10.1016/j.isci.2019.03.021` | `papers/LIONESS.pdf`, `papers/LIONESS.txt` | Primary method paper DOI. | None. |
| `first_author` | `Marieke Lydia Kuijjer` | `papers/LIONESS.pdf`, `papers/LIONESS.txt`, `additional_files/lionessR.txt` | Full first-author name. | None. |
| `year` | `2019` | `papers/LIONESS.pdf`, `papers/LIONESS.txt` | Paper publication year. | None. |
| `method_summary` | Single-sample network estimation by linear interpolation from aggregate and leave-one-out networks; default Pearson in lionessR | `papers/LIONESS.txt`; `additional_files/lionessR.txt`; `repo/lionessR/R/netFun.R` | Describes method core, not wrapper mechanics. | None. |
| `method_keywords` | `single_sample`, `single_cell`, `linear_interpolation`, `aggregate_network`, `pearson_correlation`, `coexpression`, `undirected` | Paper and package docs | Captures selected public preset and output convention. | LIONESS itself is generic single-sample, not only single-cell. |
| `implementation_url` | Bioconductor lionessR page | `additional_files/lionessR.txt`; integrator clarification | Official package source for runtime install. | None. |
| `docker_image` | `adriansegura99/inference-tools_lioness:1.0.0` | Project naming convention | Expected image tag for Phase 2. | None. |
| `execution_capabilities` | `cell_native`, `group_aggregated` | Paper; Bioconductor docs; ANDREA execution model | Native one network per cell; group output is ANDREA aggregation from cell outputs. | No sample-native context exists yet. |
| `accepts` | `cells` | Selected ANDREA profile semantics | Avoids mislabeling generic samples as `cell:<id>` contexts. | Future sample-native support could expand this. |
| `assumes` | `generic` | Method works on numeric expression matrices and is not scRNA-specific in the paper | Algorithm has no scRNA-specific required input. | The integration targets cell columns for ANDREA context semantics. |
| `extra_inputs.required` | `[]` | `lioness(x, f=netFun)` docs | Selected upstream entrypoint needs only expression matrix. | None. |
| `extra_inputs.optional` | `[]` | Upstream docs and code | No optional standardized input modifies selected Pearson LIONESS run. | None. |
| `extra_inputs.conditional_required` | `groups` when `execution.mode=group_aggregated` | ANDREA group aggregation contract | Required by ANDREA postprocess, not by lionessR. | None. |
| `outputs.directed` | `false` | `netFun()` Pearson correlation creates symmetric adjacency | One unordered row per pair. | If future non-Pearson presets are added this may change. |
| `outputs.sign` | `signed` | Paper examples and Pearson weights can be positive or negative | Export magnitude in `score`, direction in `sign`. | None. |
| `outputs.evidence` | `association` | Pearson/coexpression preset | Pearson LIONESS is association/coexpression evidence, not causal direction. | None. |
| `progress` | `kind=none` | Public function has no callback | Coarse wrapper lifecycle only. | None. |
| `params` | `{}` | `lionessR::netFun`; Bioconductor docs | The wrapper fixes the package-native Pearson aggregate network function; a one-option parameter would not change behavior. | Future presets can be added after evidence review. |
| `artifacts_aux` | log, raw RDS, raw long TSV | Bioconductor return type; wrapper provenance rules; Phase 2 wrapper output | Preserve raw upstream object and inspectable extracted weights. | None. |
| `taxonomic_scope` | all groups, empty supported species | No species-specific resource in selected entrypoint | No taxonomic restriction. | None. |
| `compatibility_rules` | `[]` | No taxon/parameter compatibility rule from upstream docs | No extra block/warn conditions expressible with current rule fields. | Minimum cell count cannot currently be expressed in ToolSpec rules. |

## Phase 2 Implementation

Implemented files:
- `wrappers/inference_tools/tools/lioness/run_tool.R`
- `wrappers/inference_tools/tools/lioness/Dockerfile`
- `wrappers/inference_tools/tests/smoketest_configs/lioness.json`
- `wrappers/inference_tools/scripts/template_map.json`

Runtime behavior:
- Loads `/io/expression.tsv` as genes x cells/samples.
- Requires at least 3 cells/samples and at least 2 variable genes after zero-variance filtering.
- Accepts no runtime parameters and calls `lionessR::lioness(expression_data, lionessR::netFun)`.
- Preserves the raw signed upstream object and extracted long-form signed weights under `raw/`.
- Captures the informational stdout emitted by `lionessR::lioness()` into `lioness.log`.
- Exports `network.csv` as undirected unordered gene pairs, excludes self-loops, filters `score <= 0`, writes `score=abs(weight)`, writes `sign` from the raw signed weight, and emits `context=cell:<cell_id>`.
- Does not pass `groups.tsv` to lionessR. `group_aggregated` remains ANDREA-managed aggregation from the emitted cell-native rows.

## Phase 2 Validation

Commands run:
- `python wrappers/inference_tools/scripts/validate_toolspecs.py --tool lioness`
- `python wrappers/inference_tools/scripts/validate_smoketest_configs.py --tool lioness`
- `Rscript -e 'parse(file="wrappers/inference_tools/tools/lioness/run_tool.R")'`
- `python wrappers/inference_tools/scripts/build_tool_images.py --tool lioness --list`
- `python wrappers/inference_tools/scripts/run_smoketests.py --tool lioness --timeout 1800`

Outcome:
- ToolSpec validation passed.
- Smoketest config validation passed.
- R wrapper syntax parse passed.
- Build pipeline selected `runtime=r` and no template bundles for `lioness`.
- Docker build completed using Bioconductor 3.23 and `lionessR 1.26.0`.
- Smoketest passed for both variants:
  - `cell_native`
  - `group_aggregated` with `groups.tsv`
- Each smoketest variant wrote `network.csv` with 840 positive-score cell-context rows and validated all 3 declared auxiliary artifacts.

## Phase 3 Validation

Commands run:
- `python wrappers/inference_tools/scripts/validate_toolspecs.py --tool lioness`
- `python wrappers/inference_tools/scripts/validate_input_specs.py`
- `python wrappers/inference_tools/scripts/validate_smoketest_configs.py --tool lioness`
- `python wrappers/inference_tools/scripts/run_smoketests.py --tool lioness --timeout 1800`

Outcome:
- `toolspec.json` is valid.
- All inference input specs are valid.
- `lioness` smoketest config is valid.
- Final smoketest rebuilt `lioness-smoketest:local` and passed both `cell_native` and `group_aggregated` variants.
- No remaining wrapper, ToolSpec, normalized-input, or smoketest inconsistencies were found.
