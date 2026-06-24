# scMINER Integration Decisions

## Sources Reviewed

- Upstream repo snapshot: `wrappers/inference_tools/tools/scminer/repo/scMINER/` at commit `48d4bc4e7fe7ccc880e5816857f264eb7c2161e9`.
- Primary paper: `wrappers/inference_tools/tools/scminer/papers/scMINER.pdf`.
- Extracted paper text: `wrappers/inference_tools/tools/scminer/papers/scMINER.txt`.
- Key repo evidence: `README.md`, `DESCRIPTION`, `NAMESPACE`, `vignettes/quick_tutorial.Rmd`, `R/network_analysis.R`, `R/clustering_analysis.R`, `man/*.Rd`, `inst/extdata/demo_pbmc14k/SJARACNe/.../consensus_network_ncol_.txt`.
- External availability checks: CRAN `available.packages()` did not contain `scMINER`; `pip index versions` found no `scMINER`, `MICA`, or `SJARACNe` package in the current index. Phase 2 installs scMINER from the official GitHub repo at commit `48d4bc4e7fe7ccc880e5816857f264eb7c2161e9` and SJARACNe from official tag `0.2.1` (the packaged Python distribution reports version `0.2.0`).
- Implementation files: `run_tool.R`, `Dockerfile`, `wrappers/inference_tools/tests/smoketest_configs/scminer.json`, `wrappers/inference_tools/scripts/template_map.json`.
- Phase 3 status: ToolSpec validation, relevant/all input-spec validation and scMINER smoketest passed.

## Paper Preparation

- PDF inputs found: `scMINER.pdf`.
- Extracted text used: `scMINER.txt`.
- Extraction quality: usable for title, DOI, abstract, method sections, benchmarking and references. Some line wrapping and hyphenation are present but do not obscure the main method evidence.

## Method Summary

scMINER is a single-cell mutual-information framework that includes MI-based clustering, cluster/cell-type-specific TF and signaling-gene network inference, and hidden-driver activity estimation. For ANDREA inference, the selected contract mirrors the public scMINER network-inference path: prepare homogeneous-cell expression and candidate-driver files, run SJARACNe, then export SJARACNe `consensus_network_ncol_.txt` edges as raw directed driver-target associations.

## ToolSpec Evidence Ledger

### `schema_version`

- Chosen value: `1.0`.
- Evidence: `andrea/catalog_inference_tools/schemas/toolspec.schema.json`.
- Rationale: fixed project schema.
- Uncertainty: none.

### `id`

- Chosen value: `scminer`.
- Evidence: scaffold path `wrappers/inference_tools/tools/scminer/` and catalog path `andrea/catalog_inference_tools/tools/scminer/`.
- Rationale: stable lowercase tool id.
- Uncertainty: none.

### `name`

- Chosen value: `scMINER`.
- Evidence: repo README title and `DESCRIPTION` package name; paper title `scMINER: a mutual information-based...`.
- Rationale: official method spelling uses lowercase `sc` and uppercase `MINER`.
- Uncertainty: none.

### `publication`, `first_author`, `year`

- Chosen values:
  - publications: `https://doi.org/10.1038/s41467-025-59620-6`, `https://doi.org/10.1093/bioinformatics/bty907`
  - first author: `Qingfei Pan`
  - year: `2025`
- Evidence: `papers/scMINER.txt` lines near the article header show DOI `10.1038/s41467-025-59620-6`, author list beginning with Qingfei Pan, and the paper as a 2025 Nature Communications article; SJARACNe README/reference and search evidence identify DOI `10.1093/bioinformatics/bty907`.
- Rationale: primary scMINER paper first; SJARACNe is included because the selected public network entrypoint delegates actual network reconstruction to SJARACNe.
- Uncertainty: none for the primary DOI/author/year; low for the secondary DOI, confirmed from upstream SJARACNe public records.

### `method_summary` and `method_keywords`

- Chosen values: summary emphasizes single-cell MI framework, homogeneous/cell-type network inference, SJARACNe, MI weights and correlation-derived signs; keywords include `single_cell`, `mutual_information`, `sjaracne`, `driver_target_network`, `tf_network`, `signaling_network`, `directed`.
- Evidence: README describes scMINER as a mutual information-based framework and states that it rewires cell-type specific gene networks for TFs and signaling genes; paper abstract/methods state scMINER performs clustering, TF/SIG network inference and hidden driver inference; tutorial network section states scMINER constructs cellular networks using SJARACNe.
- Rationale: describes method behavior rather than wrapper implementation.
- Uncertainty: none.

### `implementation_url` and installation source

- Chosen value: `https://github.com/jyyulab/scMINER/tree/48d4bc4e7fe7ccc880e5816857f264eb7c2161e9`.
- Evidence: local repo remote is `https://github.com/jyyulab/scMINER.git`; local HEAD is the user-specified commit. README documents `devtools::install_github("jyyulab/scMINER")`. CRAN check returned no package named `scMINER`.
- Rationale: no official package-manager release was found for the R package, so runtime should install from the official GitHub repo pinned to the requested commit.
- Implemented: Docker installs scMINER with `remotes::install_github(..., ref=48d4bc4e7fe7ccc880e5816857f264eb7c2161e9, dependencies=FALSE)` after installing required R imports, and installs SJARACNe from official GitHub tag `0.2.1`.
- Uncertainty: low. SJARACNe tag `0.2.1` builds a Python wheel whose package metadata version is `0.2.0`; the wrapper records both the official tag and installed package version.

### `docker_image`

- Chosen value: `adriansegura99/inference-tools_scminer:1.0.0`.
- Evidence: local wrapper image naming convention used by other integrated tools.
- Rationale: project convention.
- Uncertainty: none.

### `execution_capabilities`

- Chosen values: `global`, `group_emulated`.
- Evidence:
  - Paper methods include global network evaluation and cell-type-specific networks.
  - Tutorial says network inference is usually cluster- or cell type-specific and `generateSJARACNeInput()` takes a grouping column.
  - `generateSJARACNeInput()` creates one folder/input set per group but does not itself run and return native group networks.
  - SJARACNe CLI runs one expression matrix plus one driver list into one network.
- Rationale: one whole-matrix network is valid (`global`). Grouped execution should be ANDREA `group_emulated`, because ANDREA can partition expression by `groups.tsv` and run the same one-network public path per group while preserving public group ids. We intentionally do not claim `group_native` because the upstream group helper writes filesystem folders and scripts, with group-name filename restrictions, rather than returning a single native grouped network object.
- Uncertainty: low. Upstream can generate multiple group input folders in one call, but wrapper-level `group_emulated` is safer for public ID preservation.

### `runtime_resources`

- Chosen/implemented value: threading unsupported, `default_threads=1`, `max_threads=1`.
- Evidence:
  - scMINER R network functions expose no worker/thread argument.
  - README says Python components MICA and SJARACNe are used for speed/memory.
  - SJARACNe README says `local` runs in parallel by default using cwltool `--parallel`, and `--serial` can force serial execution, but no bounded worker/thread count is documented.
  - MICA has `-nw` in clustering examples, but MICA clustering is excluded from this network wrapper contract.
- Rationale: no safe, public, reproducible mapping from ANDREA `--threads` to an exact CPU count was found for the selected network path. The wrapper avoids unbounded nested runtime behavior by requiring `--threads=1`, setting BLAS/OpenMP variables to `1`, and invoking `sjaracne local --serial`.
- Uncertainty: low for the implemented serial contract.

### `taxonomic_scope` and compatibility rules

- Chosen values: broad `allowed_groups` with empty `supported_species`, plus parameter-dependent rules for built-in human/mouse resources.
- Evidence:
  - `getDriverList()` only accepts `species_type` values `hg` and `mm`.
  - scMINER stores built-in `tf_sigs_hg.RData` and `tf_sigs_mm.RData`.
  - `generateSJARACNeInput()` accepts `customDriver_TF` and `customDriver_SIG`, allowing user-supplied driver sets.
- Rationale: the built-in route is human/mouse-specific, but `custom_tf_list` is a valid upstream route that can support other taxa if the provided TF ids match expression genes. Therefore the species restriction is encoded in `compatibility_rules` when built-in drivers are selected rather than globally blocking the whole tool.
- Compatibility rules:
  - block `<2` genes or `<2` columns.
  - warn `<100` columns because upstream warns SJARACNe needs `>=100` single cells for high-quality single-cell networks.
  - warn explicit `downSample_N < 100`.
  - block built-in drivers for declared taxa other than human/mouse.
  - block mismatched `species_type` versus declared human/mouse NCBI taxon id.
- Uncertainty: low. Support for custom signaling-gene lists is not exposed because there is no existing normalized SIG-list input, and built-in TF/SIG covers the primary scMINER route.

### `accepts` and `assumes`

- Chosen values: `accepts=["cells"]`, `assumes="scrna_specific"`.
- Evidence: README and paper repeatedly define scMINER for single-cell/single-nucleus RNA-seq; tutorial expression matrices are genes by cells; `generateSJARACNeInput()` warnings distinguish single cells and metacells.
- Rationale: selected interface consumes a single-cell expression matrix; it is not a bulk-generic ANDREA wrapper even though SJARACNe itself can run bulk matrices.
- Uncertainty: low. Single-nucleus data share the `cells` column semantic.

### `extra_inputs`

- Required: none.
- Conditional required:
  - `groups` when `execution.mode=group_emulated`.
  - `tf_list` when `driver_source=custom_tf_list`.
- Optional: none.
- Evidence:
  - ANDREA group emulation requires `groups.tsv` for partitioning.
  - `generateSJARACNeInput()` has `customDriver_TF`, and the existing `tf_list` input spec is a text file with one TF identifier per line.
  - Built-in scMINER driver lists remove the need for `tf_list` in default modes.
- Rationale: `tf_list` semantically matches custom TF drivers exactly; no new input spec is needed for Phase 1. Custom SIG lists are intentionally excluded rather than creating a broad new input before a concrete need.
- Uncertainty: low.
- Implementation note: for `group_emulated` runs the wrapper accepts a `groups.tsv` that covers either the current expression matrix or the full parent dataset. ANDREA GUI child runs may pass a group-filtered expression matrix with the full `groups.tsv`; the wrapper therefore requires all current expression columns to be present and ignores extra group rows from other child runs.

### `outputs`

- Chosen values: `directed=true`, `sign="signed"`, `evidence="association"`.
- Evidence:
  - `drawNetworkQC()` docs state all SJARACNe networks are directed and weighted by the `MI` column.
  - Example `consensus_network_ncol_.txt` columns: `source`, `target`, `MI`, `pearson`, `spearman`, `slope`, `p-value`.
  - Paper says scMINER can distinguish positive and negative targets for each TF.
  - `get_target_list2matrix()` constructs signed MI as `MI * sign(spearman)` for weighted activity.
- Rationale: export `source -> target`, raw positive `score=MI`, and derive `sign` from `sign(spearman)` (`+`, `-`, or `?` for zero/missing). This preserves raw score magnitude and stores direction separately.
- Uncertainty: low. There is a likely typo in `drawNetworkQC()` source assigning sign from `MI`, but docs, paper and activity code support Spearman-derived signs.

### `progress`

- Chosen value: `kind="none"`.
- Evidence: selected path has scMINER setup plus SJARACNe workflow/CWL phases, but no stable public progress callback. Bootstraps are configured with `-n`, but the public CLI does not provide a documented monotonic per-bootstrap progress stream.
- Rationale: wrapper emits coarse lifecycle states in `progress.json` and retains logs, without claiming precise iteration progress.
- Uncertainty: low for the implemented coarse progress contract.

### `params`

- `driver_source`: enum `built_in_tf_sig`, `built_in_tf`, `built_in_sig`, `custom_tf_list`; default `built_in_tf_sig`.
  - Evidence: `driver_type` supports `TF`, `SIG`, `TF_SIG`; `customDriver_TF` supports a custom TF route.
  - Rationale: combines upstream `driver_type` and custom-driver behavior into mutually valid GUI choices.
- `species_type`: enum `hg`, `mm`; default `hg`.
  - Evidence: `getDriverList()` and `generateSJARACNeInput()` default `species_type="hg"` and accept only `hg`/`mm`.
  - Rationale: exposes upstream built-in driver resource selection.
- `downSample_N`: integer or null; default `1000`.
  - Evidence: `generateSJARACNeInput()` default is `1000`; docs say `NULL` skips downsampling and integer downsampling is applied only when a group has more cells than the threshold.
  - Rationale: preserves data-dependent upstream rule. The wrapper must omit/disable downsampling for null, not replace null with a number.
- `n_bootstraps`: integer default `100`.
  - Evidence: generated scMINER commands and paper methods use SJARACNe `-n 100`.
  - Rationale: meaningful method/stability parameter.
- `consensus_pvalue`: float default `0.01`.
  - Evidence: scMINER generated non-metacell commands use `-pc 1e-2`; paper methods say SJARACNe default parameters include `-pc 0.01`.
  - Rationale: selected contract excludes SuperCell metacell mode, so the non-metacell rule applies.
- `random_seed`: integer default `123`.
  - Evidence: `generateSJARACNeInput()` default seed is `123`.
  - Rationale: controls reproducible downsampling.
- Fixed, not exposed:
  - SJARACNe local/LSF platform: use local in wrapper; LSF is incompatible with container runtime.
  - SJARACNe bootstrap p-value `-pb`: leave upstream default `1e-7`, documented in SJARACNe README and paper methods.
  - SuperCell/metacell parameters: excluded for Phase 1 contract because they add an optional dependency and a distinct aggregation/preprocessing mode; default upstream is `superCell_N=NULL`.
  - MICA clustering parameters: excluded because clustering does not directly output ANDREA GRNs.
- Uncertainty: medium for whether Phase 2 should expose SuperCell after dependency testing.

### `artifacts_aux`

- Chosen/implemented artifacts: `scminer.log`, resolved config, gene alias map, raw SJARACNe expression/driver inputs, raw `consensus_network_ncol_.txt`, and each SJARACNe run's generated `sjaracne_workflow.yml`.
- Evidence: scMINER docs list generated `.exp.txt`, driver files, `runSJARACNe.sh`, and config; SJARACNe README lists main network output plus parameter and bootstrap metadata.
- Rationale: these artifacts make runtime decisions, ID mapping and raw upstream outputs auditable. `parameter_info_.txt` and `bootstrap_info_.txt` are shown in upstream examples but were not consistently emitted by `sjaracne local` with the current CWL runtime, so they are not declared as required artifacts.
- Uncertainty: low.

## Upstream Interface

### Public execution modes / entrypoints

| Upstream entrypoint | Required inputs | Output shape | ANDREA mapping | Exposed | Rationale |
| --- | --- | --- | --- | --- | --- |
| `readInput_*`, `createSparseEset`, `filterSparseEset`, `normalizeSparseEset` | raw count files/matrix | SparseEset/preprocessed matrix | fixed wrapper preprocessing only | partially | ANDREA already supplies normalized expression; wrapper may create a minimal SparseEset but should not expose full QC workflow. |
| `generateMICAinput`, `mica mds`, `mica ge`, `addMICAoutput` | expression matrix, MICA CLI | clusters/embeddings | excluded | no | Clustering/annotation output is not a GRN. MICA `-nw` thread control is not relevant to selected network contract. |
| `generateSJARACNeInput()` | SparseEset, grouping column, species/driver settings | per-group `.exp.txt`, driver files and command script | preparation for `global`/`group_emulated` | yes, conceptually | This is the scMINER network input preparation path. ANDREA will avoid group-name filesystem restrictions by using one child run per public group. |
| `sjaracne local` | `.exp.txt`, driver list, output directory, bootstraps, consensus p-value | one directed weighted network file | `global` or per-child `group_emulated` | yes | This is the public upstream network inference CLI that produces raw scores. |
| `sjaracne lsf` | same plus LSF config | one directed weighted network via cluster scheduler | excluded | no | LSF is not portable inside the ANDREA container runtime. |
| `drawNetworkQC()` | network file or SJARACNe directory | QC table/report | auxiliary only | no | Does not infer a network; useful for docs/artifact interpretation only. |
| `getActivity_individual`, `getActivity_inBatch`, `getDA` | expression plus network files | driver activity matrices and differential activity tables | excluded | no | Activity analysis is downstream of GRN inference and does not map to `network.csv`. |

### Input requirement matrix

- Always required: normalized expression matrix from ANDREA.
- Required by execution mode: `groups` for `group_emulated`.
- Required by parameter: `tf_list` for `driver_source=custom_tf_list`.
- Optional: none in Phase 1 contract.
- Not exposed: custom SIG driver lists, MICA output labels, LSF config, SuperCell objects, scMINER Portal export inputs.

## Normalized Input Mapping

- Reused `groups`: exact semantic match for ANDREA group emulation.
- Reused `tf_list`: exact semantic match for `customDriver_TF`, one TF identifier per line, subset-checked against expression genes.
- New input specs required: none for Phase 1.

## Output Mapping to `network.csv`

- `context`: `global` for whole-dataset runs; `group:<public_group_id>` for group-emulated child outputs assigned by ANDREA.
- `source`: upstream `source` mapped back to exact expression gene id.
- `target`: upstream `target` mapped back to exact expression gene id.
- `score`: raw positive `MI`; omit rows with `MI <= 0`, missing source/target, self-loops, or genes not mapped back to expression ids.
- `sign`: `+` when `spearman > 0`, `-` when `spearman < 0`, `?` when `spearman == 0` or unavailable.
- Duplicate TF/SIG edges in `built_in_tf_sig`: wrapper should retain the row with highest raw `MI` for the same context/source/target and preserve both raw source files as auxiliary artifacts.
- Public IDs: if upstream-safe aliases are required, wrapper must write `raw/gene_alias_map.tsv` and convert every public output back to ANDREA ids.

## Runtime Resource Mapping

- `--threads` contract: unsupported; effective threads = 1.
- Wrapper sets common BLAS/OpenMP variables to `1`.
- Wrapper runs `sjaracne local --serial`, avoiding SJARACNe local mode's unbounded cwltool `--parallel` default.
- Worker/threads are not exposed as user params.

## Installation Strategy

- scMINER R package: installed from official GitHub repo pinned to `48d4bc4e7fe7ccc880e5816857f264eb7c2161e9`.
- SJARACNe: installed from official GitHub tag `0.2.1`; package metadata reports `0.2.0`.
- Runtime compatibility patch: after installing SJARACNe, Docker makes packaged `SJARACNe/bin/*.py` executable and patches CWL p-value fields from `float` to `double` plus an explicit `toPrecision(12)` command-line binding for bootstrap p-values. Without this patch, current `cwltool` passed `1e-7` to `sjaracne.exe` as `0`, causing an upstream p-value range failure. The patch preserves the public default value and does not alter the SJARACNe binary or scoring algorithm.
- MICA: not installed because clustering is excluded from the selected GRN contract.
- R dependencies are installed through R/Ubuntu packages in the R runtime image; Python dependencies are installed with `python3 -m pip` for the Python runtime that executes SJARACNe.

## Wrapper Implementation

- Implemented wrapper does not depend on local `repo/`.
- Implemented wrapper path:
  1. reads ANDREA expression, preserving public gene and column ids;
  2. writes an upstream-safe `raw/gene_alias_map.tsv`;
  3. creates SJARACNe `.exp.txt` files with `isoformId=<alias>` and `geneSymbol=<public_gene_id>`;
  4. resolves candidate hubs from scMINER built-in driver lists or normalized `tf_list`;
  5. runs `sjaracne local --serial` for each requested driver class/context;
  6. parses `consensus_network_ncol_.txt`;
  7. exports raw positive `MI` scores and Spearman signs to `network.csv`;
  8. writes `progress.json`, logs, resolved config and raw upstream inputs/outputs.
- Group emulation uses public `group:<id>` contexts in `network.csv`; filesystem directory names are internal slugs only.

## Validation And Smoketest

- Implemented smoketest config: `wrappers/inference_tools/tests/smoketest_configs/scminer.json`.
- Covered variants:
  - `global_custom_tf`: `driver_source=custom_tf_list`, `tf_list.txt`, `n_bootstraps=1`, `consensus_pvalue=1`, `downSample_N=null`.
  - `group_emulated_custom_tf`: same params plus `groups.tsv` and `execution.mode=group_emulated`.
- Verified: positive raw `score` values, signed `sign`, no self-loops, public gene ids in `network.csv`, public `group:<id>` contexts for group emulation, and all declared auxiliary artifacts.
- GUI regression fixed after testing `inferred_networks/gui_dataset_20260624T010121Z`: `group_emulated` child runs failed because the wrapper required `groups.tsv` to match the already group-filtered expression columns exactly. The wrapper now accepts full parent `groups.tsv` files and subsets them to the current expression columns, while still failing if a current expression column has no group assignment.
- Commands run:
  - `make validate-toolspecs ARGS="--tool scminer"`: passed.
  - `make validate-input-specs ARGS="--spec groups --spec tf_list"`: passed.
  - `make validate-input-specs`: passed.
  - `python wrappers/inference_tools/scripts/validate_smoketest_configs.py --tool scminer`: passed.
  - `python wrappers/inference_tools/scripts/build_tool_images.py --tool scminer --image-tag scminer=scminer-smoketest:local`: passed.
  - `python wrappers/inference_tools/scripts/run_smoketests.py --tool scminer --image-tag scminer=scminer-smoketest:local --timeout 1200`: passed.
  - `Rscript -e "parse(file='wrappers/inference_tools/tools/scminer/run_tool.R')"`: passed.
  - Manual replay of both failed GUI child-run inputs from `inferred_networks/gui_dataset_20260624T010121Z/tools/scminer__02/subruns/{01_a,02_b}/io` against `scminer-smoketest:local`: passed, producing `group:A` and `group:B` outputs.
  - `python wrappers/inference_tools/scripts/run_smoketests.py --tool scminer --skip-image-build --image-tag scminer=scminer-smoketest:local --timeout 1200`: passed after the GUI regression fix.

## Known Limitations / Open Questions

- Runtime may be heavy because SJARACNe performs bootstrap consensus. Smoketest parameters lower `n_bootstraps` and relax `consensus_pvalue`, while ToolSpec defaults preserve upstream public settings.
- The selected wrapper contract excludes SuperCell/metacell mode for now. This is an upstream public option but adds a separate dependency and preprocessing mode; revisit only if full scMINER parity is needed.
- Custom SIG driver lists are not exposed because the current catalog has `tf_list` but no semantically precise signaling-driver list input.
- Built-in driver resources are useful only when expression gene ids overlap scMINER's human/mouse symbols. For synthetic/non-human data, use `driver_source=custom_tf_list` with a matching `tf_list`.
