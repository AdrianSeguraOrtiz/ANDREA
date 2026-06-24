# scRegulate Integration Decisions

## Phase 1 Scope

- Phase executed: evidence and contract only.
- ToolSpec draft: `andrea/catalog_inference_tools/tools/scregulate/toolspec.json`.

## Phase 2 Implementation Status

- Phase executed: wrapper, Dockerfile, template registration and smoketest.
- Wrapper: `wrappers/inference_tools/tools/scregulate/run_tool.py`.
- Dockerfile: installs `scRegulate==1.0.1` from PyPI; the local `repo/` directory is not used at runtime.
- Template map: `scregulate` registered as a Python runtime with `python_runtime_contract`.
- Smoketest: `make run-tool-smoketests ARGS="--tool scregulate --timeout 1200 --show-output-lines 30"` passed on 2026-06-24.

## Sources Reviewed

- Playbook: `wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md`.
- Local upstream repo: `wrappers/inference_tools/tools/scregulate/repo/scRegulate/`.
- Local repo commit: `de8efa5e24a2297c22636ba682aad69281bf630c`.
- Local paper PDF/text: `wrappers/inference_tools/tools/scregulate/papers/scRegulate.pdf`, `wrappers/inference_tools/tools/scregulate/papers/scRegulate.txt`.
- PyPI project page: `https://pypi.org/project/scRegulate/` reports `scRegulate 1.0.1`, released 2025-09-17.
- PyPI wheel inspected locally: `scRegulate==1.0.1`; package code diff against local `scregulate/` source was empty.

## Paper Preparation

- PDF inputs found: `scRegulate.pdf`.
- Extracted text used: `scRegulate.txt`.
- Extraction quality: usable for title, DOI, abstract, methods and output semantics. Some ligatures and formula symbols are noisy, but not enough to affect contract decisions.

## Method Summary

scRegulate is a single-cell GRN and TF-activity inference method. It embeds a TF-target prior in a PyTorch variational autoencoder decoder, trains a dynamic weighted GRN from scRNA-seq expression, and optionally fine-tunes the learned GRN for cell types or clusters.

## ToolSpec Evidence Ledger

| Field | Chosen value | Evidence | Rationale / uncertainty |
| --- | --- | --- | --- |
| `schema_version` | `1.0` | `andrea/catalog_inference_tools/schemas/toolspec.schema.json` | Project contract. |
| `id` | `scregulate` | Scaffold paths under `wrappers/inference_tools/tools/scregulate/` and catalog path | Project identifier. |
| `name` | `scRegulate` | README title and paper title | Official capitalization. |
| `publication` | `https://doi.org/10.1093/bioinformatics/btaf638` | `papers/scRegulate.txt:1-12`, README citation | Primary final Bioinformatics paper supersedes the older bioRxiv DOI still present in PyPI metadata. |
| `first_author` | `Mehrdad Zandigohar` | `papers/scRegulate.txt:10-16` | Full first author from primary paper. |
| `year` | `2025` | `papers/scRegulate.txt:1-2` | Publication year of primary paper. |
| `method_summary` | VAE, prior-guided TF activity and context-specific GRN summary | Abstract `papers/scRegulate.txt:27-41`; methods `:85-143` | Summary describes method, not wrapper. |
| `method_keywords` | `single_cell`, `variational_autoencoder`, `transcription_factor_activity`, `prior_guided`, `cell_type_specific`, `signed_grn` | Paper abstract and methods, especially `:32-40`, `:85-109`, `:137-143` | Captures modeling family and outputs. |
| `implementation_url` | `https://pypi.org/project/scRegulate/1.0.1/` | User clarification, PyPI page, wheel metadata | Official package source; local repo code matches wheel package. |
| `docker_image` | `adriansegura99/inference-tools_scregulate:1.0.0` | Project image naming convention | Implemented Dockerfile is compatible with this image name. |
| `execution_capabilities` | `global`, `group_native`, `group_emulated` | `train_model()` returns trained model/data/GRN; `fine_tune_clusters()` consumes `cluster_key` and returns per-cluster GRNs | `global` exports one all-cell GRN. `group_native` mirrors upstream cluster fine-tuning in one logical run. `group_emulated` partitions expression by ANDREA group and runs the public global path independently per group. No `column_native`; per-cell TF activities are not per-cell GRNs. |
| `runtime_resources.threading` | supported, default 1, max 8, map to PyTorch/BLAS CPU threads | README says PyTorch/CUDA; source uses PyTorch tensors and DataLoader; `create_dataloader(..., num_workers=0)` | No public `n_jobs`; safe CPU mapping is backend-controlled via `torch.set_num_threads` and BLAS/OpenMP env vars. Scaling benefit is workload-dependent. |
| `taxonomic_scope` | all broad groups, empty `supported_species` | Method can consume user prior GRNs; built-in `collectri_prior()` supports human/mouse only | Species restriction is parameter-dependent, so encoded in `compatibility_rules` for builtin priors instead of globally blocking provided priors. |
| `compatibility_rules` | min columns and builtin-prior organism rules | `train_model()` splits cells into train/validation; `collectri_prior(species)` accepts only `human` or `mouse` | Dataset with one column cannot split safely. Builtin CollecTRI modes are blocked for declared incompatible taxa and warned for unknown taxon. |
| `accepts` | `cells` | README says single cell/nucleus RNA data; paper is scRNA-seq-specific | Expression columns are cells. |
| `assumes` | `scrna_specific` | Paper title/abstract and methods; README introduction | Method is explicitly for single-cell transcriptomics. |
| `extra_inputs` | `prior_grn` conditional on `prior_source=provided_prior`; `groups` conditional on `execution.mode=group_native` or `group_emulated` | `train_model(rna_data, net, ...)` requires a net DataFrame; `fine_tune_clusters(..., cluster_key=...)` consumes clusters; ANDREA group emulation needs `groups.tsv` to partition expression | Builtin CollecTRI modes need no user prior. Group labels drive native grouped fine-tuning or ANDREA's per-group emulated runs. |
| `outputs` | directed, signed, association | Paper defines TF-target matrix `W`, sign as activator/repressor and magnitude as strength; source uses TF columns/genes | Wrapper should export TF -> target edges, `score=abs(raw_weight)`, `sign` from raw weight. |
| `progress` | iterations | Source has explicit epoch loops in `train_model()` and `fine_tune_clusters()` | Implemented wrapper reports coarse lifecycle phases in `progress.json` and preserves upstream epoch diagnostics in `scregulate.log`. |
| `params` | prior selection plus core training/fine-tuning hyperparameters | Function signatures in `train.py` and `fine_tuning.py`; tutorial examples | Exposes runtime/scientific knobs needed for practical runs; keeps architecture arrays fixed to source defaults for Phase 1 simplicity. |
| `artifacts_aux` | log, config, prior network, raw weights, model state | Upstream returns model, processed AnnData and GRN objects; debugging requires resolved prior and raw weights | Implemented wrapper preserves raw method state before ANDREA conversion. |

## Upstream Interface Audit

| Upstream entrypoint | Inputs | Outputs | ANDREA mapping | Exposed | Rationale |
| --- | --- | --- | --- | --- | --- |
| `scregulate.collectri_prior(species="human")` | species `human` or `mouse` | TF-target DataFrame | `prior_source` parameter | yes | Official helper for builtin priors; should be cached/bundled at image build to avoid runtime network dependence. |
| `scregulate.train_model(rna_data, net, ...)` | AnnData expression, TF-target prior DataFrame, hyperparameters | trained model, processed AnnData, GRN posterior object | `global` base training | yes | Core public API that learns TF activities and weighted GRN. |
| `scregulate.fine_tuning.fine_tune_clusters(processed_adata, model, cluster_key=None, ...)` | trained output, model, optional cluster key | fine-tuned model, TF activities, per-cluster raw/scaled GRNs | `global` when `cluster_key=None`; `group_native` when `cluster_key` comes from `groups.tsv` | yes | Public cell-type/cluster-specific fine-tuning path; native grouped output. |
| `scregulate.utils.extract_GRN(adata, matrix_type)` | processed AnnData | prior or posterior GRN DataFrame | auxiliary extraction helper | partially | Useful for debugging, but source-stored posterior is min-max scaled and loses raw sign; wrapper should export raw model weights instead. |
| `scregulate.auto_tune(...)` | train/fine-tune objects, Optuna ranges | best hyperparameters | excluded | no | Hyperparameter search, not a single GRN inference contract; expensive and not needed for initial wrapper. |
| notebook preprocessing workflows | `.h5ad`, Scanpy operations | normalized AnnData | excluded | no | ANDREA supplies normalized expression; wrapper should not own broad preprocessing beyond required matrix conversion. |
| per-cell TF activities | trained/fine-tuned model | cell x TF activity matrix | excluded from `network.csv` | no | TF activities are per-cell activities, not GRN edges; can be auxiliary later if useful. |
| rerun global model per group | partitioned expression and prior | one GRN per partition | `group_emulated` | yes | This is the standard ANDREA-emulated grouped profile for tools with a valid global interface. It is intentionally distinct from native cluster fine-tuning because it trains independent per-group global models instead of fine-tuning one shared model by cluster. |
| per-column GRN | none found | none | `column_native` excluded | no | No public API returns one GRN per expression column/cell. |

## Required And Conditional Inputs

- Always required: expression matrix only.
- Required by parameter:
  - `prior_grn` when `prior_source=provided_prior`.
- Required by execution mode:
  - `groups` when `execution.mode=group_native`.
  - `groups` when `execution.mode=group_emulated`.
- Optional inputs: none in Phase 1.
- Upstream inputs intentionally not exposed:
  - `.h5ad` direct input, because ANDREA normalized expression is the wrapper contract.
  - custom cluster-key name, because ANDREA `groups.tsv` provides the public grouping semantics.
  - auto-tuning ranges, because Optuna search is excluded from the selected inference contract.

## Normalized Input Mapping

- `prior_grn` is reused. Its `source`, `target`, `score` columns match scRegulate's `source`, `target`, `weight` prior DataFrame after a direct `score -> weight` rename. Signed scores are meaningful because scRegulate uses weighted priors and the paper describes binary or ternary priors. The wrapper filters prior sources with fewer than `min_targets` distinct targets before calling upstream so scRegulate's `adapt_prior_and_data()` and ULM source sets stay aligned for weighted priors.
- `groups` is reused. The first column maps expression column ids to clusters, which matches `fine_tune_clusters(cluster_key=...)` after the wrapper stores those labels in AnnData.obs. For `group_emulated`, the same file supplies ANDREA partition labels; scRegulate itself does not consume those labels in each child run.
- Expression values are converted to AnnData with public expression columns as observations and public genes as variables. The wrapper requires non-negative values and positive per-column totals, then applies `scanpy.pp.normalize_total(target_sum=1e6)` and `scanpy.pp.log1p()` before calling scRegulate, matching the package data-preparation documentation and preventing raw-count simulations from driving the VAE loss to `NaN`.
- No new input spec is required.

## Parameter Mapping And Defaults

Selected exposed parameters:

- `prior_source`: wrapper-level selector for `collectri_human`, `collectri_mouse`, or `provided_prior`.
- `epochs`, `freeze_epochs`, `train_val_split_ratio`, `batch_size`, `learning_rate`, `alpha_max`, `alpha_scale`, `beta_max`, `gamma_max`, `early_stopping_patience`, `min_targets`, `min_TFs`, `random_state`: passed to `train_model()`.
- `fine_tune_epochs`, `fine_tune_batch_size`, `fine_tune_min_epochs`, `fine_tune_beta_max`, `fine_tune_max_weight_norm`, `fine_tune_early_stopping_patience`: passed to `fine_tune_clusters()`.

Defaults and conflicts:

- `train_model()` source default `min_targets=20`; paper preprocessing and tutorial examples mention/use 10. ToolSpec preserves the installable 1.0.1 source default `20`, and users can set `10` explicitly. The wrapper also applies the same threshold as an explicit distinct-target-count prefilter because upstream 1.0.1 uses weighted row sums in `adapt_prior_and_data()` but target counts in ULM, which can otherwise fail for priors with non-unit weights.
- `batch_size=3500` is the source default. If `batch_size=null`, wrapper should pass `None`; upstream then uses `int(train_val_split_ratio * rna_data.n_obs)`. This is the only data-dependent default currently exposed.
- `device=None` is not a user parameter. The wrapper passes `device=None` to scRegulate while the Docker image sets `CUDA_VISIBLE_DEVICES=""`, so upstream device auto-selection resolves to CPU in the standard image.
- `encode_dims=[512]`, `decode_dims=[1024]`, `z_dim=40`, `alpha_start=0`, `beta_start=0`, `gamma_start=0`, fine-tuning learning rates and `log_interval` remain fixed to upstream defaults in Phase 1 to keep the GUI parameter surface manageable. They can be exposed later if needed.
- `freeze_epochs` is constrained to `>=20` because upstream `schedule_mask_factor()` divides by `freeze_epochs // 20`; smaller values can fail.

## Output Mapping To `network.csv`

- Global mode:
  - Run `train_model()` and `fine_tune_clusters(..., cluster_key=None)`.
  - Export one `context=global` network from the final raw `tf_mapping.weight`/single `W_posteriors_per_cluster` matrix.
- Group native mode:
  - Build AnnData.obs from `groups.tsv`.
  - Run `fine_tune_clusters(..., cluster_key="andrea_group")`.
  - Export one context per public group as `group:<group_id>`.
- Group emulated mode:
  - ANDREA partitions expression by public `groups.tsv` labels and invokes the wrapper once per group, using the global scRegulate path in each child run.
  - The wrapper should accept either the full parent `groups.tsv` or the subset matching the current child expression matrix, subset labels to current expression columns, and export the current public group as `group:<group_id>`.
  - scRegulate does not consume group labels in this profile; labels are used only for context assignment and id preservation.
- Direction:
  - `source` is TF name, `target` is target gene name.
- Score/sign:
  - Use raw signed weights, not the min-max scaled DataFrames returned for plotting.
  - Write `score=abs(weight)`.
  - Write `sign="+"` for positive weight, `sign="-"` for negative weight, and omit zero-weight rows.
- Evidence:
  - `association`, because the method estimates regulatory association/strength from expression and priors rather than direct perturbational causality.
- Public ids:
  - AnnData var names and prior identifiers should be matched directly to normalized expression gene ids. If Phase 2 needs upstream-safe aliases, it must write an auxiliary alias map and map all public outputs back.

## Runtime Resource Mapping

- `runtime_resources.threading.supported=true`.
- ANDREA `--threads` should map to:
  - `torch.set_num_threads(threads)`;
  - `torch.set_num_interop_threads(1)` where possible;
  - `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS`, `BLIS_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`.
- Do not expose thread controls as normal params.
- Do not claim DataLoader worker parallelism: `create_dataloader()` exposes `num_workers` internally but `train_model()` and `fine_tune_clusters()` do not forward it and call the helper with its default `0`.

## Installation Strategy

- Preferred runtime source: `pip install scRegulate==1.0.1` from PyPI, respecting the integrator clarification and official README installation instructions.
- Wheel inspection: `scRegulate==1.0.1` includes package `scregulate`, version `1.0.1`, and the same source as the local repo package directory.
- Fallback if PyPI becomes unavailable: GitHub repo `YDaiLab/scRegulate` pinned to local reviewed commit `de8efa5e24a2297c22636ba682aad69281bf630c`.
- Dockerfile installs runtime dependencies with Python `pip`, including explicit `optuna==4.4.0` because `scregulate.__init__` imports `auto_tuning`, which imports Optuna although the PyPI metadata does not declare it.
- Runtime does not depend on local `repo/`.
- Runtime network access for CollecTRI priors is avoided by downloading/caching `collectri_human_net.csv` and `collectri_mouse_net.csv` during image build into `/opt/scregulate_priors`.

## ToolSpec Field Evidence Details

- `accepts`, `assumes`: paper title and abstract explicitly say single-cell/scRNA-seq; README says single cell/nucleus RNA data.
- `extra_inputs`: `train_model(rna_data, net, ...)` requires a prior net; `fine_tune_clusters()` requires cluster annotations for native cluster-specific networks; ANDREA's `group_emulated` profile requires group labels to partition expression and assign public group contexts.
- `outputs`: paper states inferred GRN weight matrix has positive, negative or zero values, sign indicates activator/repressor and magnitude reflects strength. Source stores normalized `GRN_posterior`, but raw weights are available in model/fine-tuning internals.
- `progress`: source epoch loops provide the natural method unit; implemented wrapper writes coarse lifecycle progress and preserves epoch diagnostics in `scregulate.log`.
- `artifacts_aux`: selected artifacts are designed to preserve the raw prior, raw signed weights and runtime configuration required to audit conversion to `network.csv`.

## Phase 2 Wrapper Implementation

- Convert ANDREA expression TSV to AnnData with cells as observations and genes as variables.
- Preprocess expression inside AnnData with Scanpy total-count normalization to `1e6` and `log1p`; ids are preserved in `obs.index` and `var.index`.
- Load prior:
  - `collectri_human`: local bundled/cached CollecTRI human CSV.
  - `collectri_mouse`: local bundled/cached CollecTRI mouse CSV.
  - `provided_prior`: normalized `prior_grn.tsv`, renamed to `source,target,weight`.
- Validate prior shape, finite nonzero weights, provided-prior gene ids, and source target counts before calling upstream; upstream `adapt_prior_and_data()` still enforces overlap and `min_targets`/`min_TFs`.
- Preserve group ids exactly in `group:<id>` contexts.
- `group_native`: pass groups as `AnnData.obs["andrea_group"]` and call `fine_tune_clusters(cluster_key="andrea_group")`.
- `group_emulated`: wrapper accepts full parent group maps, subsets them to current expression columns, runs the global path, and writes `group:<id>` only when the current physical child contains one group. ANDREA's logical grouped finalizer overwrites child contexts with the planned public group label.
- Preserve raw positive score magnitudes only; do not apply ANDREA normalization.
- Export auxiliary artifacts declared in ToolSpec: `scregulate.log`, `raw/scregulate_config.json`, `raw/prior_network.tsv`, `raw/grn_raw_weights.tsv`, `raw/model_state.pt`.

## Smoketest Outcome

- Config: `wrappers/inference_tools/tests/smoketest_configs/scregulate.json`.
- Fixture inputs: tool-specific `scregulate/expression.tsv`, tool-specific `scregulate/prior_grn.tsv`, shared `groups.tsv`.
- Variants covered:
  - `global_provided_prior`;
  - `group_native_provided_prior`;
  - `group_emulated_contract`;
  - `provided_prior_weighted_min_targets_filter`.
- Verified by harness:
  - positive `score`;
  - signed `sign`;
  - public gene ids;
  - `group:<id>` contexts for `group_native`;
  - `scregulate.log`, `raw/scregulate_config.json`, `raw/prior_network.tsv`, `raw/grn_raw_weights.tsv`, `raw/model_state.pt`.
- Result: passed; `network.csv` rows were 39 for global, 39 for group native, 39 for group emulated contract, and 20 for the weighted-prior `min_targets` regression.
- GUI regression checked on `inferred_networks/gui_dataset_20260624T020103Z` input with reduced epoch budgets: the wrapper no longer fails with the upstream ULM `KeyError` or `NaN` posterior, filters TFs `2`, `80` and `91` below `min_targets=20`, and writes 249 network rows.

## Known Limitations / Open Questions

- PyPI README metadata still references the bioRxiv/preprint status, while the local repo README and local paper contain the final Bioinformatics citation. ToolSpec uses the final DOI.
- Default training epochs are large; smoketests must override them downward.
- Builtin CollecTRI priors are human/mouse only and require expression gene symbols with sufficient overlap. Provided priors are the portable path for synthetic or non-human/non-mouse datasets.
- Group native fine-tuning scales per-cluster epochs by cluster size; very small groups may receive few epochs unless `fine_tune_min_epochs` is set.
- `group_native` and `group_emulated` are both exposed but have different method semantics: native mode fine-tunes one shared model by cluster, while emulated mode trains independent global models per ANDREA group.
- No `column_native` support found; per-cell TF activity is a matrix of TF activities, not a GRN.
