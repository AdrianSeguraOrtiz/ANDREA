# dignet Integration Decisions

## Status

- Phase executed: Phase 3 complete.
- Wrapper: `wrappers/inference_tools/tools/dignet/run_tool.py`.
- Dockerfile: `wrappers/inference_tools/tools/dignet/Dockerfile`.
- ToolSpec: `andrea/catalog_inference_tools/tools/dignet/toolspec.json`.
- Smoketest config: `wrappers/inference_tools/tests/smoketest_configs/dignet.json`.
- New normalized input specs: none.
- Build registration: `wrappers/inference_tools/scripts/template_map.json` maps `dignet` to Python with `python_runtime_contract`.

## Primary Sources

- Upstream repo snapshot: `wrappers/inference_tools/tools/dignet/repo/DigNet`, official remote `https://github.com/zpliulab/DigNet.git`, pinned commit `5109401ac242d2b671156eab4b4a5fabd808b612`.
- Local paper: `wrappers/inference_tools/tools/dignet/papers/DigNet.pdf`; extracted text in `DigNet.txt`.
- Main upstream evidence: `README.md`, `Tutorial.py`, `DigNet.py`, `config.py`, `pathway/pathway.py`, `make_final_net.py`, `denoising_diffusion_pytorch/denoising_diffusion_pytorch_1d.py`, `discrete/diffusion_utils.py`.

## Method Metadata

- Publication: `https://doi.org/10.1101/gr.279551.124`.
- First author: Chuanyuan Wang.
- Year: 2024.
- Summary basis: the paper describes DigNet as a diffusion-based generative model that reconstructs GRNs from scRNA-seq profiles, uses meta-cell integration, graph-transformer denoising, binary edge states, and repeated diffusion generation (`DigNet.txt`, abstract/method sections).
- Keywords: `single_cell`, `diffusion_model`, `discrete_generation`, `graph_transformer`, `metacell`, `cell_specific_network`, `directed`.

## Upstream Entrypoint Decision

Selected upstream public interface:

`Config() -> DigNet(args) -> load_test_data(test_expression_csv, num=0, diffusion_pre=checkpoint) -> DigNet.test(diffusion_pre, testdata, truelabel)`

Evidence:

- README/Tutorial document testing by loading a pretrained model and PCA file, then calling `load_test_data()` and `test()` (`README.md`, `Tutorial.py`).
- `DigNet.test()` returns one final adjacency/vote matrix via `cal_final_net()` (`DigNet.py`, `make_final_net.py`).
- The CSV/XLSX test path builds one `Data` object from a gene x cell expression table and selected human pathway/gene set (`pathway/pathway.py`).

Excluded public modes:

- Training from simulated `.data` or CSV/XLSX: excluded because it trains a new model/checkpoint instead of performing inference on a single normalized ANDREA dataset.
- Simulated `.data` testing: excluded because upstream requires both `exp` and `net`; ANDREA inference inputs do not include a true/synthetic network.
- Direct `make_final_net.py` helper usage: excluded because `DigNet.test()` already calls it.
- Resource/download/preprocessing scripts: excluded because they do not perform the selected GRN inference.

## Execution Capabilities

- `global`: exposed. One expression matrix produces one directed gene x gene network.
- `group_emulated`: exposed. ANDREA can partition expression by `groups.tsv`, run the global wrapper per group, and rewrite contexts to `group:<group_id>`.
- `group_native`: not exposed; upstream does not consume group metadata.
- `column_native`: not exposed; the public CSV interface does not return one network per cell.
- `group_aggregated`: not exposed; DigNet does not produce column-native output for ANDREA to aggregate.

For wrapper-level `execution.mode=group_emulated`, the wrapper still runs one DigNet invocation over the expression matrix it receives. It does not validate or consume `groups.tsv`; the ANDREA orchestrator validates groups, partitions the dataset, invokes one child run per group, and rewrites contexts to `group:<group_id>`.

## Input Contract

Required normalized input:

- `expression.tsv`: genes x cells, numeric finite non-negative values, unique gene IDs and unique cell IDs.

Conditional normalized input:

- `groups.tsv`: required only for `execution.mode=group_emulated`; consumed by ANDREA/orchestrator, not by DigNet.

No optional normalized inputs are consumed. Upstream uses bundled human KEGG, RegNetwork and TF resources rather than ANDREA `tf_list`.

Implemented wrapper validations:

- `gene_set` must be a human KEGG id present in the bundled KEGG resource, or `all_expression_genes`.
- `all_expression_genes` writes a temporary upstream gene-list CSV from expression gene IDs, but it does not relax DigNet's human-resource requirement. The selected IDs must still match DigNet's bundled human gene symbols and contain RegNetwork source-target overlap.
- Selected genes must be 10 to 200 genes, matching upstream `from_cancer_create()` limits.
- Preflight compatibility blocks `gene_set=all_expression_genes` when `dataset.expression.genes > 200`, because that parameter selects every expression gene and would exceed the public CSV/pathway limit.
- Selected genes must contain at least one bundled RegNetwork edge.
- `knn <= number_of_cells` when `metacell=true`, because upstream calls nearest-neighbor search before returning original columns for small datasets.
- The bundled `S33_Cancer_cell_pca_model.pkl` requires exactly 100 features after upstream metacell preprocessing. The wrapper validates this before calling DigNet.

The wrapper writes upstream CSV with a dummy first data column because upstream sets the first column as gene index and then drops the first remaining expression column.

## Parameters

- `gene_set`: required enum, default `null`. Passed as `args.test_pathway`; `all_expression_genes` is implemented by writing an upstream-compatible temporary user gene-list CSV, but expression IDs still must be human symbols represented in DigNet's bundled RegNetwork/KEGG resources. The enum contains `all_expression_genes` as the first real selectable value, followed by the 342 `hsa...` KEGG pathway IDs present in DigNet's bundled `KEGG_all_pathway.pkl`; GUI forms should start with an empty placeholder so the user explicitly chooses a value.
- `metacell`: bool, default `true`. Passed as `args.metacell`.
- `knn`: int, default `20`. Passed as `args.KNN`.
- `metacell_count`: int, default `100`. Passed as `args.Cnum`; also the required effective feature count for the selected PCA/model pair.
- `ensemble`: int, default `30`. Passed as `args.ensemble`; raw score scale is `1..ensemble`.
- `diffusion_timesteps`: int, default `1000`. Passed as `args.diffusion_timesteps`; smoketest verifies a lower override (`25`) is runtime-valid.

Fixed implementation choices:

- Pretrained model: `pre_train/S33_Cancer_cell_checkpoint_pre_train_20240326.pth`.
- PCA file: `result/S33_Cancer_cell_pca_model.pkl`.
- `use_pca="30"`, matching the bundled PCA.
- `show=false`, because no truth labels are available for progress/evaluation display.
- `n_job` is not a user parameter; it is mapped from runtime `--threads`.
- CPU-only execution is forced with `CUDA_VISIBLE_DEVICES=""`.

## Runtime Resources

- ToolSpec threading: supported, default `1`, max `8`.
- Mapping: `--threads` -> `args.n_job = min(threads, ensemble)`.
- Evidence: `config.py` defines `n_job`; `DigNet.__init__()` stores it; `DigNet.test()` uses `joblib.Parallel(n_jobs=self.n_job)` over independent ensemble members.
- The wrapper sets PyTorch and BLAS/OpenMP thread counts to 1 to avoid nested oversubscription.

## Installation

- No PyPI/package-manager release was found; upstream has no `setup.py`, `setup.cfg`, or `pyproject.toml`, and `pip index versions dignet`/PyPI JSON checks did not find a package.
- Docker installs from official upstream GitHub pinned to `5109401ac242d2b671156eab4b4a5fabd808b612`.
- Runtime does not depend on the local `repo/` directory.
- Runtime stack verified by smoketest: Python 3.10, CPU `torch==2.0.0+cpu`, `torch-geometric==2.3.1`, `torchmetrics==0.11.4`, `setuptools==67.6.0`.
- `setuptools` is pinned because `torchmetrics==0.11.4` imports `pkg_resources`.

## Output Mapping

- `network.csv` columns: `source,target,score,sign,evidence,context`.
- Direction: row gene -> column gene. Upstream constructs adjacency as `adj_matrix[TF, Gene] = 1` and removes invalid `Gene -> TF` entries.
- Score: raw positive DigNet vote count from `cal_final_net()`, not ANDREA-normalized.
- Sign: `?`; DigNet output is not a signed coefficient.
- Evidence: `association`.
- Filtering: omit self-loops and rows with `score <= 0`.
- Gene IDs are preserved from expression IDs for retained genes via upstream `Data.y` and the final adjacency DataFrame.
- Cell IDs do not appear in output contexts. Group IDs are preserved by ANDREA group-emulated orchestration.

Auxiliary artifacts:

- `dignet.log`: upstream stdout/stderr and wrapper failure diagnostics.
- `raw/adj_final.csv`: raw final adjacency/vote matrix.
- `raw/model_config.json`: resolved wrapper/upstream runtime choices.
- `raw/selected_genes.tsv`: genes retained by the selected gene set.

## Field Evidence Summary

| Field | Chosen value | Evidence and rationale |
|---|---|---|
| `id`, `name` | `dignet`, `DigNet` | Catalog/tool path and upstream README/paper title. |
| `publication`, `first_author`, `year` | DOI URL, Chuanyuan Wang, 2024 | README citation and paper text. |
| `execution_capabilities` | `global`, `group_emulated` | Public test path returns one adjacency matrix per expression profile. |
| `accepts`, `assumes` | `cells`, `scrna_specific` | README and paper target scRNA-seq. |
| `extra_inputs` | conditional `groups` for `group_emulated` | ANDREA orchestration requirement; DigNet itself does not read groups. |
| `runtime_resources` | threading supported via `args.n_job` | Upstream joblib ensemble parallelism in `DigNet.test()`. |
| `taxonomic_scope` | animal, species `9606` | Selected CSV path uses bundled human KEGG, RegNetwork and TF resources. |
| `outputs` | directed, unsigned association | Upstream adjacency is directed and no signed coefficient is produced. |
| `progress` | coarse lifecycle `progress.json` | Upstream exposes no stable callback; wrapper writes lifecycle states. |
| `artifacts_aux` | log, raw adjacency, config, selected genes | Needed to audit preprocessing/model choice and raw output conversion. |
| `params` | `gene_set`, `metacell`, `knn`, `metacell_count`, `ensemble`, `diffusion_timesteps` | Public inference-affecting settings from README/config/code; training params excluded. `gene_set` is exposed as an enum generated from bundled human KEGG ids plus the wrapper sentinel `all_expression_genes` for GUI usability. |

## Phase 3 Validation

Run on 2026-06-17:

- `make validate-toolspecs ARGS="--tool dignet"`: passed.
- `make validate-input-specs`: passed, 16 valid input specs.
- `make validate-smoketest-configs ARGS="--tool dignet"`: passed.
- `make run-tool-smoketests ARGS="--tool dignet --threads 2 --timeout 2400 --show-output-lines 60"`: passed.

Smoketest variants:

- `global`: passed; produced a non-empty positive network and validated 4 auxiliary artifacts.
- `group_emulated_contract`: passed; supplied `groups.tsv` for the ANDREA execution contract, produced a non-empty positive network and validated 4 auxiliary artifacts.

DigNet diffusion generation is stochastic, so exact edge counts can vary between smoketest runs. The contract requires non-empty `network.csv`, positive scores, valid signs, no self-loops, and declared auxiliary artifacts.

## Known Limitations

- The selected public CSV/pathway path is human-specific.
- `all_expression_genes` is only suitable when the expression gene IDs are human symbols with bundled RegNetwork overlap and the dataset has 200 or fewer genes. Synthetic IDs such as dyngen-style custom gene names can fail even when the dataset metadata says human.
- Every invocation must produce exactly 100 expression features after metacell preprocessing to match the bundled PCA model. This applies to each group-emulated child run.
- The wrapper exposes the public pretrained inference path only; training and simulated `.data` testing remain excluded.

## Post-GUI Run Follow-up

Observed on `inferred_networks/gui_dataset_20260617T143834Z`:

- Global `all_expression_genes` selected 137 dyngen-style expression IDs, but none formed a bundled human RegNetwork source-target edge. The wrapper error now states that `all_expression_genes` does not bypass DigNet's human gene-symbol/resource requirement.
- Group-emulated child runs previously failed before DigNet preparation because the wrapper compared each already-partitioned child expression matrix against the full `groups.tsv`. The wrapper no longer validates `groups.tsv`; group validation and partitioning are orchestrator responsibilities.

Observed on `inferred_networks/gui_dataset_20260623T214216Z`:

- A default DigNet run with `ensemble=30` and `diffusion_timesteps=1000` remained in the first diffusion ensemble member after more than 18 minutes while planned through the generic 4 GB fallback.
- Added `andrea/catalog_inference_tools/tools/dignet/cost.json` as a conservative planner profile so future plans reserve 8 GB per default DigNet task and display multi-hour ETA instead of the previous fallback estimate.
- The ToolSpec descriptions for `ensemble` and `diffusion_timesteps` now document runtime scaling and bounded-runtime overrides. The defaults themselves remain aligned with the selected upstream public configuration.
