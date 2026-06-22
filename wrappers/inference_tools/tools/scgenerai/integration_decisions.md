# scgenerai Integration Decisions

Status: Phase 3 complete. The ToolSpec, wrapper, Dockerfile, build registration,
fixtures, and smoketest config are implemented and verified.

## Sources Reviewed

- Playbook: `wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md`
- Upstream repo evidence: `wrappers/inference_tools/tools/scgenerai/repo/scGeneRAI/`
- Upstream commit reviewed and used for runtime install:
  `77177a2589775ff125622bc4e8bf26a54f95ca62`
- Upstream remote: `https://github.com/PhGK/scGeneRAI.git`
- Upstream public docs/code:
  - `repo/scGeneRAI/README.md`
  - `repo/scGeneRAI/example.ipynb`
  - `repo/scGeneRAI/scGeneRAI.py`
  - `repo/scGeneRAI/dataloading_simple.py`
- Paper evidence:
  - `papers/scGeneRAI.pdf`
  - `papers/scGeneRAI.txt`
- Existing normalized inputs:
  - `andrea/catalog_inference_tools/input_specs/expression_matrix.json`
  - `andrea/catalog_inference_tools/input_specs/column_descriptors.json`
  - `andrea/catalog_inference_tools/input_specs/groups.json`

Paper extraction was usable for title, DOI, authors, abstract, methods, and
availability evidence. The extracted text has normal two-column PDF
interleaving, so the PDF remains the primary paper evidence.

## Selected Contract

- Public upstream entrypoint mirrored: instantiate `scGeneRAI.scGeneRAI`, call
  `fit(...)`, then call `predict_networks(...)`.
- Selected prediction mode: `predict_networks(..., LRPau=True,
  remove_descriptors=True, device_name="cpu")`.
- Execution capabilities: `column_native` and `group_aggregated`.
- Excluded execution capabilities: `global`, `group_native`, and
  `group_emulated`.
- Runtime install source: pinned upstream GitHub source, not the local evidence
  repo. PyPI package discovery with `python -m pip index versions scgenerai`
  found no package on 2026-06-16.

## Runtime Resources

- ToolSpec value: `runtime_resources.threading.supported=true`,
  `default_threads=1`, `max_threads=8`.
- Evidence: the pinned upstream implementation imports PyTorch, trains a
  `torch.nn.Module` in `train(...)`, and performs LRP prediction through
  PyTorch tensor operations in `compute_LRP(...)`; it does not expose
  `n_jobs`, workers, DataLoader workers or a public thread parameter.
- Wrapper mapping: ANDREA `--threads` is applied before `fit(...)` and
  `predict_networks(...)` through `torch.set_num_threads(threads)` and through
  `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
  `NUMEXPR_NUM_THREADS`, `BLIS_NUM_THREADS` and `VECLIB_MAXIMUM_THREADS`.
- PyTorch inter-op threads are fixed to 1 because scGeneRAI has no outer worker
  pool; this avoids nested oversubscription while still allowing the assigned
  threads to affect CPU tensor kernels.
- No normal ToolSpec parameter represents threads, workers, cores, DataLoader
  worker count or device selection. `batch_size` remains a method/training
  hyperparameter because it changes optimization behavior, not only resource
  allocation.
- No `cost.json` exists yet for scGeneRAI, so planner fallback currently uses
  `default_threads=1` until cost benchmarking adds compatible runtime points.

## Upstream Entrypoint Audit

| Upstream surface | Output | ANDREA mapping | Exposed | Decision |
| --- | --- | --- | --- | --- |
| `scGeneRAI.scGeneRAI().fit(data, nepochs, model_depth, ...)` | Trained in-memory model | Required setup for selected run | Yes, internally | README documents fit before prediction; code implements it in `scGeneRAI.py:155-195`. |
| `model.predict_networks(data, descriptors=None, LRPau=True, remove_descriptors=True, ...)` | One CSV per cell/sample under `PATH/results/` | `column_native` | Yes | README documents this prediction API; code loops over samples and writes per-sample files in `scGeneRAI.py:198-222` and `scGeneRAI.py:358-390`. |
| Same `predict_networks`, with ANDREA `groups.tsv` supplied | Native per-cell CSVs; groups used after wrapper output | `group_aggregated` | Yes | Upstream does not consume group labels, but ANDREA can aggregate native `column:<id>` rows. |
| `predict_networks(..., LRPau=False)` | Directed raw LRPr rows with signed `LRP` and diagnostics | Parameter choice with different output semantics | No | ToolSpec fixes undirected unsigned LRPau. Exposing LRPr would require a different directed/signed contract. |
| `predict_networks(..., remove_descriptors=False)` | Descriptor features can appear as network nodes | Incompatible with gene-gene public output | No | Paper methods ignore descriptor scores when reducing to gene scores; wrapper keeps gene-gene rows only. |
| Notebook `average_network = ... groupby(...).mean()` | One mean table over per-cell outputs | Downstream postprocessing | No | This is not a native global inference mode; the paper uses averaged scGeneRAI output only for comparison with average-network methods. |

## Input Contract

- Required primary input: ANDREA `expression_matrix`. The wrapper reads genes as
  rows and cells as columns, then transposes to upstream's row=cells,
  column=genes DataFrame.
- The wrapper applies per-gene z-score standardization before `fit(...)`, using
  the same preprocessing pattern shown in upstream `example.ipynb`
  (`means = ex_data.mean(axis=0)`, `sds = ex_data.std(axis=0)`,
  `(ex_data-means)/sds`). This is required for raw count-like inputs: a dyngen
  count matrix produced `testloss: nan` and all-zero LRPau without
  standardization, while the standardized run produced positive raw LRPau
  scores.
- Genes with zero variance across cells are dropped before scGeneRAI because
  z-score standardization would otherwise produce NaN values and such genes do
  not provide inferable cell-varying signal for this upstream interface.
- Optional extra input: `column_descriptors`. This semantically matches upstream
  optional categorical descriptors such as batch or cell type. The wrapper
  requires exact alignment to expression columns when provided.
- Conditional extra input: `groups`, required only for
  `execution.mode=group_aggregated`. The wrapper validates coverage, but
  scGeneRAI itself does not consume this file.
- No new normalized input spec was needed.

## Parameters

| ToolSpec param | Default | Implementation | Evidence / uncertainty |
| --- | ---: | --- | --- |
| `nepochs` | `1500` | Passed to `fit(...)`; smoketest overrides to `1`. | Paper used maximum 1500 epochs; upstream code requires the argument. |
| `model_depth` | `2` | Passed to `fit(...)`. | README and notebook use 2; code requires the argument. |
| `lr` | `0.02` | Passed to `fit(...)`. | README, code, and paper agree. |
| `batch_size` | `5` | Passed to `fit(...)`. | README, code, and paper agree. |
| `lr_decay` | `0.995` | Passed to `fit(...)`. | Code and paper use 0.995; README says 0.99, so code plus paper were preferred. |
| `early_stopping` | `true` | Passed to `fit(...)`. | README, code, and paper support early stopping. |

Fixed choices not exposed as params:
- `LRPau=True`, because the selected public score is the paper's absolute
  undirected reciprocal LRP score.
- `remove_descriptors=True`, because ANDREA public output is gene-gene.
- `device_name="cpu"`, because device selection is container infrastructure.
- Prediction mask count and LRP batch behavior remain upstream implementation
  choices. In the pinned code, `predict_networks` calls `calc_all_paths` with
  `batch_size=100`, and `Dataset_LRP` defaults to `maskspersample=10000` while
  only one batch is consumed.

Dynamic or implementation defaults preserved by calling upstream code:
- Internal split: `torch.manual_seed(0)`, then `nsamples//10*9` train rows and
  the remainder for test loss.
- Hidden width: pinned code sets `hidden = 2 * nfeatures`; the paper mentions
  `10 * N`, but the wrapper mirrors the pinned implementation.
- Target range: fixed `remove_descriptors=True` preserves `self.simple_features`
  gene-only target range.

## Output Contract

- Raw upstream LRPau values are written directly as positive `score`
  magnitudes after the fixed upstream-style z-score preprocessing. No ANDREA
  score normalization is done in the wrapper.
- The selected public interface already returns non-negative absolute
  undirected scores, so there is no signed coefficient to split into
  `abs(coefficient)` and sign.
- `network.csv` rows use `sign="?"`, `evidence="association"`, and
  `context="column:<original_expression_column_id>"`.
- `score <= 0` rows and self-loops are omitted.
- Undirected output is one row per unordered source-target pair per cell.
- Public gene and cell IDs are preserved exactly. The wrapper uses internal
  numeric sample names for upstream filenames and writes
  `raw/cell_alias_map.tsv` to map back to original ANDREA cell IDs.
- For `group_aggregated`, the physical wrapper run still emits native column
  rows; ANDREA core retains those rows as `network.column_native.csv` and writes
  only aggregated `group:<id>` rows to the logical `group_aggregated`
  `network.csv`.

## ToolSpec Evidence Ledger

| Field | Chosen value | Evidence and rationale | Uncertainty |
| --- | --- | --- | --- |
| `schema_version` | `1.0` | Fixed by `toolspec.schema.json`. | None |
| `id` | `scgenerai` | Scaffold/catalog directory names. | None |
| `name` | `scGeneRAI` | README title and paper method name. | None |
| `publication` | `https://doi.org/10.1093/nar/gkac1212` | DOI on paper first page. | None |
| `first_author` | `Philipp Keyl` | Paper author list. | None |
| `year` | `2023` | Paper first page says published in Nucleic Acids Research, 2023. | None |
| `method_summary` | Neural-network imputation plus LRP-based cell-specific GRN inference with LRPau. | Paper abstract and methods describe static scRNA-seq, neural-network prediction, LRP, LRPr, and LRPau. | None |
| `method_keywords` | `single_cell`, `explainable_ai`, `layer_wise_relevance_propagation`, `deep_learning`, `cell_specific_network`, `undirected` | Paper title, abstract, and LRPau method section. | None |
| `implementation_url` | `https://github.com/PhGK/scGeneRAI` | Paper availability and local git remote. | None |
| `docker_image` | `adriansegura99/inference-tools_scgenerai:1.0.0` | Local image naming convention used by other inference ToolSpecs. | Final publication is outside Phase 3. |
| `runtime_resources.threading` | Supported; default 1, max 8 | Wrapper maps `--threads` to PyTorch CPU intra-op threads and thread environment variables; upstream uses PyTorch training/LRP and exposes no separate worker parameter. | No cost profile exists yet, so default remains conservative. |
| `execution_capabilities` | `column_native`, `group_aggregated` | Upstream writes per-cell files; ANDREA can aggregate column rows by groups. | None |
| `accepts` | `cells` | README and paper describe RNA samples of cells and individual-cell GRNs. | None |
| `assumes` | `scrna_specific` | Paper is explicitly for static scRNA-seq and single-cell GRNs. | None |
| `taxonomic_scope` | All broad groups, no species IDs | Method uses expression data and no species-specific resource; paper validates human and synthetic data. | Empirical evidence is strongest for human/synthetic datasets. |
| `compatibility_rules` | `[]` | No organism or parameter-specific incompatibility is documented. | Minimum cell count is not expressible in current ToolSpec schema. |
| `extra_inputs` | Optional `column_descriptors`; conditional `groups` for `group_aggregated` | Upstream descriptors are optional categorical metadata; groups are ANDREA aggregation metadata. | None |
| `outputs` | Undirected, unsigned association | LRPau is absolute undirected reciprocal LRP; code keeps one unordered pair. | LRPr mode intentionally excluded. |
| `progress` | `kind="none"` | Upstream has loops and tqdm output but no stable callback; wrapper writes coarse lifecycle progress. | None |
| `params` | Training params listed above | README, code signatures, notebook, and paper. | `lr_decay` conflict documented and resolved to code plus paper. |
| `artifacts_aux` | `scgenerai.log`, `raw/results/LRP_*.csv`, `raw/cell_alias_map.tsv` | Upstream writes result CSVs; wrapper logs stdout/stderr and maps internal aliases back to public cell IDs. | None |

## Implementation Files

- Wrapper: `wrappers/inference_tools/tools/scgenerai/run_tool.py`
- Dockerfile: `wrappers/inference_tools/tools/scgenerai/Dockerfile`
- Build map: `wrappers/inference_tools/scripts/template_map.json`
- ToolSpec: `andrea/catalog_inference_tools/tools/scgenerai/toolspec.json`
- Smoketest config:
  `wrappers/inference_tools/tests/smoketest_configs/scgenerai.json`
- Smoketest fixtures:
  `wrappers/inference_tools/tests/fixtures/scgenerai/expression.tsv`,
  `column_descriptors.tsv`, and `groups.tsv`

The Dockerfile installs from pinned GitHub source and explicitly installs
`numpy==1.26.4`, `pandas==2.2.2`, `tqdm==4.66.4`, and CPU `torch==2.3.1`.
The local `repo/` evidence copy is not a runtime dependency.

## Phase 3 Verification

Commands run on 2026-06-16:

- `python wrappers/inference_tools/scripts/validate_toolspecs.py --tool scgenerai`
  - Result: valid, checked=1, invalid=0.
- `python wrappers/inference_tools/scripts/validate_input_specs.py`
  - Result: valid, checked=15, invalid=0.
- `python wrappers/inference_tools/scripts/validate_smoketest_configs.py --tool scgenerai`
  - Result: valid, checked=1, invalid=0.
- `python wrappers/inference_tools/scripts/run_smoketests.py --tool scgenerai --threads 1 --timeout 900 --show-output-lines 20`
  - Result: passed.
  - Image built: `scgenerai-smoketest:local`.
  - Variants covered: `column_native` and `group_aggregated`.
  - Each variant produced `network.csv` with 36 positive rows and validated 14
    auxiliary artifacts.
- Regression check for raw dyngen counts:
  - Dataset:
    `benchmarks/gui_generate_benchmark_20260612T165118Z/datasets/gui_generate_benchmark__dyngen__01__r01`
  - Parameters: `nepochs=1500`, `model_depth=2`, `lr=0.02`,
    `batch_size=5`, `lr_decay=0.995`, `early_stopping=true`,
    `execution.mode=column_native`.
  - Result after z-score preprocessing: passed, `network.csv` contained
    465,800 positive rows and final progress was `status="completed"`.

## Known Limitations

- No package-manager installation exists; runtime uses pinned GitHub source.
- Very small datasets can fail upstream's internal 90/10 split. Current
  ToolSpec schema has no minimum-cell rule.
- README and code/paper disagree on `lr_decay`; ToolSpec and wrapper use 0.995.
- Paper and pinned code differ on hidden width; wrapper mirrors pinned code.
- Exact scores may vary with PyTorch/NumPy stochastic behavior even though the
  upstream split seed is fixed.
