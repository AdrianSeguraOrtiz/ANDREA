# PIDC Integration Decisions

Phase: 3 finalized. Wrapper, Dockerfile, ToolSpec, input specs, template-map registration, fixtures and smoketest config are aligned and verified.

## Sources Reviewed

- Integration playbook: `wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md`
- Upstream repo snapshot: `wrappers/inference_tools/tools/pidc/repo/NetworkInference.jl/`
- Upstream remote: `https://github.com/Tchanders/NetworkInference.jl.git`
- Local upstream commit: `e5a3de323127f002e57bbd91c834f7739939ba0e`
- Upstream tags present locally/remotely: `v0.0.1`, `v0.0.2`, `v0.1.0`, `v0.1.1`
- Julia General registry metadata:
  - `https://raw.githubusercontent.com/JuliaRegistries/General/master/N/NetworkInference/Package.toml`
  - `https://raw.githubusercontent.com/JuliaRegistries/General/master/N/NetworkInference/Versions.toml`
- Local paper PDF: `wrappers/inference_tools/tools/pidc/papers/PIDC.pdf`
- Extracted paper text: `wrappers/inference_tools/tools/pidc/papers/PIDC.txt`
- Upstream files reviewed:
  - `README.md`
  - `Project.toml`
  - `src/NetworkInference.jl`
  - `src/common.jl`
  - `src/network_inference.jl`
  - `src/infer_network.jl`
  - `src/empirical_bayes_glue.jl`
  - `test/runtests.jl`
  - `test/empirical_bayes_glue_tests.jl`
- Implemented files:
  - `wrappers/inference_tools/tools/pidc/run_tool.jl`
  - `wrappers/inference_tools/tools/pidc/Dockerfile`
  - `wrappers/inference_tools/tests/smoketest_configs/pidc.json`
  - `wrappers/inference_tools/tests/fixtures/pidc/expression.tsv`
  - `wrappers/inference_tools/tests/fixtures/pidc/groups.tsv`
  - `wrappers/inference_tools/scripts/template_map.json`

## Paper Preparation

- PDF found: `PIDC.pdf`
- Extracted text found and used: `PIDC.txt`
- Extraction quality: sufficient for title, DOI, authors, abstract, method details, software availability, discretization/estimator details and output interpretation. Some two-column text is interleaved, but key evidence is readable.

## Method Summary

PIDC is an information-theoretic GRN inference method for single-cell expression data. It uses partial information decomposition over gene triplets to compute proportional unique contribution for each gene pair, then applies a network-context weighting step to produce an undirected weighted association network.

Evidence:
- `PIDC.txt` lines near the title/abstract: "Gene Regulatory Network Inference from Single-Cell Data Using Multivariate Information Measures"; PIDC uses partial information decomposition and multivariate information theory.
- `PIDC.txt` method/results sections around "Incorporating PID into an Inference Algorithm": defines PUC from ratios of unique information to mutual information across triplets.
- `PIDC.txt` Figure 4/6/discussion text: PIDC combines PID/PUC with network context and reports putative functional relationships, not causal direction.
- `README.md`: NetworkInference represents fully connected weighted undirected networks, with edge weight as relative confidence.

## Selected Upstream Entrypoint

The wrapper contract mirrors the public lower-level NetworkInference.jl workflow:

```julia
nodes = get_nodes(path; delim, discretizer, estimator, number_of_bins)
network = InferredNetwork(PIDCNetworkInference(), nodes; estimator, base)
```

Rationale:
- README documents this as the "Multiple steps" public API.
- It is the same PIDC algorithm used by the one-step `infer_network(path, PIDCNetworkInference(); ...)`.
- It exposes the raw `InferredNetwork.edges` list directly, avoiding the convenience `write_network_file` convention that writes each undirected edge in both directions.
- It lets the wrapper export one unordered edge per gene pair as required by ANDREA for undirected methods.

The one-step public API remains semantically equivalent evidence for parameter names and defaults:

```julia
infer_network(path, PIDCNetworkInference(); delim=false,
              discretizer="bayesian_blocks",
              estimator="maximum_likelihood",
              number_of_bins=10,
              base=2,
              out_file_path="")
```

## Upstream Interface Audit

| Upstream public interface | Inputs | Output | ANDREA mapping | Exposed | Rationale |
| --- | --- | --- | --- | --- | --- |
| `infer_network(path, PIDCNetworkInference(); ...)` | Data file with one node/gene per row and measurements in columns | `InferredNetwork`; optional file writes both directions | `global` | Yes, via lower-level equivalent | Public one-step PIDC workflow and source of documented defaults. |
| `get_nodes(...)` + `InferredNetwork(PIDCNetworkInference(), nodes; ...)` | Same expression data, already loaded as nodes | `InferredNetwork.edges`, one unordered edge per pair | `global` | Yes, selected entrypoint | Best fit for raw score-preserving `network.csv`. |
| Same PIDC workflow on ANDREA group subsets | Expression matrix subset selected by `groups.tsv` | One network per group via ANDREA orchestration | `group_emulated` | Yes | Upstream has no group metadata API, but the method can run independently on any selected measurement subset. |
| `MINetworkInference`, `CLRNetworkInference`, `PUCNetworkInference` | Same node measurements | Alternative algorithms | Separate tool/parameter choices | No | The requested tool is PIDC; exposing other algorithms would broaden the contract beyond the tool identity. |
| `write_network_file` | `InferredNetwork` | Text edge list with each undirected edge written in both directions | Output helper | No | Conflicts with ANDREA's one-row-per-unordered-pair convention for undirected tools. |
| `get_adjacency_matrix` | `InferredNetwork`, threshold | Thresholded adjacency matrix | Downstream filtering | No | ANDREA stores raw weighted edges; thresholding is downstream analysis. |
| `read_network_file` | Upstream edge-list file | `InferredNetwork` | Import helper | No | Not part of inference. |
| `empirical_bayes` glue | `InferredNetwork`, priors, EmpiricalBayes package | Posterior-weighted network | Optional post-processing | No | Optional dependency, extra prior semantics, and posterior rescaling are outside the core PIDC method requested here. |
| Native group/task/per-column API | None found | None | `group_native`, `column_native`, `group_aggregated` | No | README/source/tests expose no public API returning native group-specific or column-specific networks from one run. |

## Input Requirement Matrix

- Always required: normalized expression matrix. It maps to the upstream node-measurement file where rows are genes/nodes and columns are measurements.
- Required only when `execution.mode=group_emulated`: `groups`.
- Required only for parameter values: none.
- Optional inputs: none.
- Upstream inputs intentionally not exposed:
  - empirical-Bayes prior files, because they belong to optional posterior post-processing and require an optional external package.
  - threshold/proportion values for `get_adjacency_matrix`, because ANDREA stores raw weighted edges and performs downstream filtering elsewhere.

Normalized input reuse:
- `groups` matches the needed semantics for `group_emulated`: it maps expression column IDs to group labels so ANDREA can partition columns before independent PIDC runs.
- No new input spec is required.

## ToolSpec Evidence Ledger

### Fixed/project fields

- `schema_version`: `1.0`; evidence: ToolSpec schema.
- `id`: `pidc`; evidence: tool folder and catalog path.
- `docker_image`: `adriansegura99/inference-tools_pidc:1.0.0`; evidence: existing inference-tool image naming convention. This is a project packaging decision, not upstream evidence.

### Identity and provenance

- `name`: `PIDC`.
  - Evidence: paper title/abstract and README reference to PIDC; source exports `PIDCNetworkInference`.
  - Rationale: official method acronym.
  - Uncertainty: none.
- `publication`: `https://doi.org/10.1016/j.cels.2017.08.014`.
  - Evidence: `PIDC.txt` title page and DOI lines.
  - Rationale: primary PIDC Cell Systems paper.
  - Uncertainty: none.
- `first_author`: `Thalia E. Chan`.
  - Evidence: `PIDC.txt` author list.
  - Rationale: first author of the primary publication.
  - Uncertainty: none.
- `year`: `2017`.
  - Evidence: `PIDC.txt` title page, journal citation and DOI page text.
  - Rationale: primary publication year.
  - Uncertainty: none.
- `method_summary` and `method_keywords`.
  - Evidence: paper abstract, method sections around PID/PUC, Figure 4/6 text, README description.
  - Rationale: summarize PIDC itself: partial information decomposition, mutual/multivariate information, network context, single-cell, undirected weighted network.
  - Uncertainty: none for method concepts; "single_cell" is included as a keyword because the paper is explicitly single-cell, even though the package API is generic.
- `implementation_url`: `https://github.com/Tchanders/NetworkInference.jl`.
  - Evidence: README, paper Data and Software Availability, Julia registry `Package.toml`.
  - Rationale: canonical public implementation repo.
  - Uncertainty: none.

### Execution capabilities

- Chosen value: `["global", "group_emulated"]`.
- Evidence:
  - README one-step and multiple-step examples infer one network from one data file.
  - Source exposes no group metadata or per-column network API.
  - Paper discusses inferring networks from carefully chosen subsets of single-cell data and overlapping subsets.
- Rationale:
  - `global`: PIDC can infer one network from the full matrix.
  - `group_emulated`: ANDREA can partition expression columns by `groups.tsv` and run the same public PIDC workflow independently per group.
  - Excluded `group_native`: no upstream API consumes group labels and returns multiple networks.
  - Excluded `column_native`: no upstream API returns one network per expression column.
  - Excluded `group_aggregated`: this requires `column_native`, which PIDC does not provide.
- Uncertainty: group-emulated runs may be statistically weak for groups with too few columns because PIDC estimates distributions from measurement variability; the current schema has no minimum-column rule.

### Runtime resources

- Chosen value: threading supported, default `1`, max `8`.
- Evidence:
  - README Performance section documents using Julia with multiple processes (`julia -p`) and says NetworkInference distributes costly calculations when multiple processes are available.
  - Source imports `Distributed` and `SharedArrays`.
  - `get_mi_scores`, `get_puc_scores`, and `get_weights` use `@sync @distributed` loops.
- Rationale:
  - ANDREA `--threads=N` should map to one Julia process budget of N total processes: no extra workers for N=1, and `N-1` worker processes for N>1.
  - `max_threads=8` is a conservative wrapper cap because upstream documents that too many processes can degrade performance and gives no hard maximum.
- Uncertainty: performance scaling is workload-dependent; small matrices can run slower with extra processes.

### Dataset compatibility

- `accepts`: `samples`, `cells`, `timepoints`, `perturbations`.
  - Evidence: README says each node has a set of measurements and methods can apply beyond biological networks; paper uses single cells and also discusses time-series/perturbation-style variation as measurement sources.
  - Rationale: the selected API only needs repeated numeric measurements per gene/node.
  - Uncertainty: PIDC was developed/evaluated for single-cell data and benefits from many observations, so non-cell datasets are methodologically generic but may need care.
- `assumes`: `generic`.
  - Evidence: README explicitly says the methods could be applied to other data types even though the package originated for gene expression.
  - Rationale: no single-cell-only file format, resource, or model component is required by the implementation.
  - Uncertainty: the primary paper is single-cell-specific; the generic designation follows implementation semantics.
- `taxonomic_scope`: all catalog groups, no species IDs.
  - Evidence: no species-specific databases, motifs or aliases are used by README/source/paper workflow.
  - Rationale: PIDC operates on numeric expression values and gene labels only.
  - Uncertainty: none.
- `compatibility_rules`: none.
  - Evidence: no taxon/resource constraints found.
  - Rationale: no catalog-expressible hard block is needed.
  - Uncertainty: no rule for minimum number of cells/samples is currently expressible.

### Extra inputs

- `required`: none.
- `optional`: none.
- `conditional_required`: `groups` when `execution.mode=group_emulated`.
- Evidence:
  - Upstream PIDC requires only the expression file.
  - Playbook requires `groups` to be declared for group-emulated orchestration when grouped execution is exposed.
- Rationale: `groups.tsv` is not consumed by PIDC itself; ANDREA uses it to split the matrix into independent PIDC child runs.
- Uncertainty: none.

### Parameters and defaults

- `discretizer`: enum `bayesian_blocks`, `uniform_width`, `uniform_count`; default `bayesian_blocks`.
  - Evidence: README Options and `get_nodes`/`infer_network` signatures.
  - Rationale: public upstream option; Bayesian blocks is the documented default and paper recommendation.
  - Dynamic/default rule: for Bayesian blocks, `Node` overwrites `number_of_bins` with a data-dependent number of bins selected by `get_bin_ids!`.
- `estimator`: enum `maximum_likelihood`, `dirichlet`, `shrinkage`; default `maximum_likelihood`.
  - Evidence: README Options and source signatures; README recommends maximum likelihood for PUC/PIDC.
  - Rationale: public upstream option.
  - Uncertainty: paper discusses Miller-Madow for MI comparisons, but the selected NetworkInference PIDC API documents only these three estimator strings.
- `number_of_bins`: integer default `10`, min `1`.
  - Evidence: README Options and source signatures.
  - Rationale: public upstream option for uniform discretizers.
  - Dynamic/default rule: ignored when `discretizer=bayesian_blocks`; preserved in ToolSpec description.
  - Uncertainty: paper methods mention using a square-root heuristic for equal-width analyses, but the selected implementation default is `10`; the ToolSpec follows implementation defaults.
- `base`: float default `2`, positive.
  - Evidence: README Options and source signatures.
  - Rationale: public upstream information-measure unit parameter.
  - Uncertainty: ToolSpec can express positive lower bound but not "not equal to 1"; the wrapper rejects `base=1`.
- Not exposed:
  - `delim`: wrapper-controlled input serialization.
  - `out_file_path`: wrapper-controlled output location.
  - algorithm choice (`MI`, `CLR`, `PUC`): outside the PIDC tool identity.
  - threshold/proportion for `get_adjacency_matrix`: downstream analysis, not raw inference.
  - Julia process count: runtime resource, not a user-facing tool param.

### Outputs

- `directed`: `false`.
  - Evidence: README says networks are assumed undirected; `Edge` node order is arbitrary; paper describes static undirected GRNs and notes PIDC cannot distinguish causality/directionality without further assumptions.
  - Rationale: export one unordered pair and exclude self-loops.
  - Uncertainty: none.
- `sign`: `none`.
  - Evidence: `Edge.weight` is a confidence weight; no sign or activation/repression coefficient is computed.
  - Rationale: wrapper will write `sign="?"`.
  - Uncertainty: none.
- `evidence`: `association`.
  - Evidence: paper describes statistical dependencies, coordinated expression and putative functional relationships; discussion says causal direction is not distinguished.
  - Rationale: scores are association/confidence weights.
  - Uncertainty: none.
- Score scale:
  - Evidence: README says edge weight indicates relative confidence, and `InferredNetwork.edges` are sorted descending by weight; `common.jl` says absolute weights are less meaningful across algorithms than relative weights within a network.
  - Rationale: preserve raw PIDC `edge.weight` as positive `score`; do not apply ANDREA-specific normalization.
  - Implemented rule: filter `score <= 0`; write one row per unordered pair; no self-loops.

### Progress

- Chosen value: `kind="none"`.
- Evidence: `infer_network` prints coarse messages ("Getting nodes...", "Inferring network...", "Writing network to file...") but source exposes no callback or stable per-edge/per-triplet progress counter.
- Rationale: wrapper can update `progress.json` at coarse lifecycle milestones, but not a meaningful percent unit.
- Uncertainty: none.

### Auxiliary artifacts

Planned artifacts:
- `pidc.log`: combined wrapper/upstream lifecycle log.
- `raw/pidc_edges.tsv`: raw PIDC edge table before ANDREA conversion.
- `raw/pidc_config.json`: resolved params, source ref and runtime process mapping.
- `raw/gene_alias_map.tsv`: internal alias to exact ANDREA gene ID map.

Evidence/rationale:
- README/source output can be represented as an edge list and logs are useful for Julia distributed execution.
- `get_nodes` uses `readdlm` and `string(line[1])`; numeric-looking gene IDs could otherwise be parsed and stringified differently. The wrapper should therefore use upstream-safe aliases and map all public output rows back to exact expression gene IDs.

Uncertainty: none after Phase 3 validation; artifact names are implemented and validated by smoketest.

## Output Mapping to `network.csv`

- Source/target: exact ANDREA expression gene IDs, restored from `raw/gene_alias_map.tsv`.
- Context:
  - physical wrapper run: `global`.
  - `group_emulated`: ANDREA orchestration owns child-run context labeling as `group:<group_id>`.
- Score: raw PIDC `edge.weight` when `edge.weight > 0`.
- Sign: `?`.
- Evidence: `association`.
- Directed: false; one row per unordered gene pair; no self-loops.
- Dense outputs: PIDC creates a fully connected weighted network with one edge per gene pair, so smoketests use small matrices and validate zero filtering.

## Installation Strategy

Preferred public installation source after inspection:

```julia
Pkg.add(PackageSpec(url="https://github.com/Tchanders/NetworkInference.jl.git",
                    rev="e5a3de323127f002e57bbd91c834f7739939ba0e"))
```

Rationale:
- README says `Pkg.add("NetworkInference")`, confirming Julia Pkg is the official installation path.
- Julia General registry `Package.toml` points to the same GitHub repo.
- Julia General registry `Versions.toml` currently lists only registered version `0.1.0`.
- Local repo `Project.toml` declares `0.1.1`; remote tag `v0.1.1` exists at `2b647b8...`.
- The requested commit `e5a3de3...` is current remote `master`/`HEAD` and contains a functional fix after `v0.1.1`: negative PUC scores are clamped to zero.
- Therefore, plain `Pkg.add("NetworkInference")` would not reproduce the requested commit, while `Pkg.add(url=..., rev=...)` uses the same Julia package manager and pins the official upstream source.

Fallback: none needed after Docker build verification. The local `repo/` folder remains evidence-only and is not used at runtime.

Implemented runtime detail:
- Docker uses Julia 1.10 and installs both `NetworkInference.jl` and `JSON.jl` with Julia `Pkg`.
- The Dockerfile installs `NetworkInference.jl` from the official GitHub repo pinned to `e5a3de323127f002e57bbd91c834f7739939ba0e`.
- The Dockerfile applies one narrow compatibility patch after package installation: `src/empirical_bayes_glue.jl` sets `EB_EXISTS = false` instead of calling removed/deprecated `Pkg.installed()`. The empirical-Bayes API is intentionally not exposed by this wrapper, so this patch only prevents an optional feature probe from breaking package import on Julia 1.10.
- Runtime does not depend on `wrappers/inference_tools/tools/pidc/repo/`; build contexts also ignore `repo/` and `papers/`.

## Implemented Wrapper Notes

- Runtime language: Julia wrapper (`run_tool.jl`).
- `wrappers/inference_tools/scripts/build_tool_images.py` now recognizes `runtime="julia"` and injects the standard `run_tool.sh` entrypoint for `run_tool.jl`.
- `wrappers/inference_tools/scripts/template_map.json` registers `pidc` as Julia with no shared bundles.
- Wrapper serializes normalized expression to an upstream-compatible gene-row file.
- Wrapper uses internal gene aliases before calling `get_nodes` to avoid `readdlm` type coercion of public IDs, writes `raw/gene_alias_map.tsv`, and maps source/target back before writing public outputs.
- Wrapper preserves upstream defaults:
  - `discretizer="bayesian_blocks"`
  - `estimator="maximum_likelihood"`
  - `number_of_bins=10`, ignored by upstream for Bayesian blocks
  - `base=2`
- Wrapper rejects `base=1` because logarithm base 1 is invalid.
- Wrapper rejects `--threads > 8` to match ToolSpec `runtime_resources.threading.max_threads`.
- Wrapper maps `--threads=N` to `N` total Julia processes by adding `N-1` workers before PIDC inference.
- Wrapper exports raw positive PIDC weights only, one row per unordered pair, no self-loops, `sign="?"`, `evidence="association"`, `context="global"`.
- For `group_emulated`, wrapper verifies `groups.tsv` is mounted, but ANDREA core owns partitioning/context rewriting.

## Smoketest Outcome

Final command:

```bash
make run-tool-smoketests ARGS="--tool pidc --threads 2 --timeout 2400 --show-output-lines 80"
```

Outcome: passed. The command rebuilt `pidc-smoketest:local` and validated three variants:
- `global_uniform_width`: passed; wrote 10 positive unordered non-self rows and 4 auxiliary artifacts.
- `global_uniform_count`: passed; wrote 10 positive unordered non-self rows and 4 auxiliary artifacts.
- `group_emulated_contract`: passed; supplied `groups.tsv`, ran with `execution.mode=group_emulated`, wrote 10 positive unordered non-self rows and 4 auxiliary artifacts.

Smoketest note:
- The tiny smoke fixture uses `uniform_width`/`uniform_count` overrides. Direct trials with `bayesian_blocks` on small synthetic fixtures produced all-zero PIDC weights, which is valid to filter but unsuitable for a non-empty wrapper smoketest. The wrapper and ToolSpec still preserve upstream `bayesian_blocks` as the default.

Final Phase 3 validation passed:
- `python -m json.tool andrea/catalog_inference_tools/tools/pidc/toolspec.json`
- `make validate-toolspecs ARGS="--tool pidc"`
- `make validate-input-specs` (22 input specs valid; no new input spec added)
- `make validate-smoketest-configs ARGS="--tool pidc"`
- `make run-tool-smoketests ARGS="--tool pidc --threads 2 --timeout 2400 --show-output-lines 80"`

Additional Phase 2 static validation passed and remains applicable:
- `python -m py_compile wrappers/inference_tools/scripts/build_tool_images.py wrappers/inference_tools/scripts/run_smoketests.py wrappers/inference_tools/scripts/validate_smoketest_configs.py wrappers/inference_tools/scripts/validate_toolspecs.py`

## Known Limitations / Open Questions

- PIDC is computationally expensive: paper/source describe O(n^3) behavior in the number of genes and a fully connected output. Small fixtures are necessary for smoketests.
- The method was designed and evaluated for single-cell expression and benefits from many cells/measurements, but the package API is generic. ToolSpec therefore uses `assumes="generic"` with `cells` among accepted column kinds.
- There is no schema-level minimum for number of columns per group; weak group-emulated runs with too few measurements may fail or produce poor estimates at runtime.
- `bayesian_blocks` remains the upstream default but can produce all-zero edges on very small/simple fixtures; the wrapper filters zero scores and raises an error if no positive edge remains.
