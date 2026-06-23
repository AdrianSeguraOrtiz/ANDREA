# planet Integration Decisions

## Status

- Phase executed: Phase 3 complete.
- Wrapper implementation: `run_tool.py` implemented.
- Dockerfile implementation: `Dockerfile` implemented and built.
- New normalized input specs: none.
- Smoketest: passed on 2026-06-23 for `global` and `group_emulated` variants.

## Sources Reviewed

- Upstream repo snapshot: `wrappers/inference_tools/tools/planet/repo/project-Planet`, official remote `https://github.com/wangchuanyuan1/project-Planet`, pinned commit `3d8be3ca788dc436e8d2c888facdeffe49553c61`.
- Local paper: `wrappers/inference_tools/tools/planet/papers/Planet.pdf`; extracted text in `Planet.txt`.
- Main source files: `README.md`, `Planet.py`, `config.py`, `run_all_sample_on_simulation.py`, `run_all_sample_on_BRCA.py`, `pathway/pathway.py`, `make_final_net.py`, `denoising_diffusion_pytorch/denoising_diffusion_pytorch_1d.py`, `Download_TF_file.py`.

## Method Metadata

- Publication: `https://doi.org/10.3390/genes16111255`.
- First author: Shiyu Xu.
- Year: 2025.
- Summary basis: the paper describes Planet as an attention-guided probabilistic diffusion framework that reconstructs cell-specific GRNs from gene expression profiles, uses a Triple Hybrid-Attention Transformer, directed TF-to-gene binary edge states, accelerated sampling, and an ensemble edge probability.
- Keywords: `single_cell`, `diffusion_model`, `probabilistic_generation`, `hybrid_attention`, `graph_attention`, `metacell`, `cell_specific_network`, `directed`.

## Upstream Entrypoint Decision

Selected public interface:

`Config() -> Planet(args) -> torch.load(pretrained_checkpoint) -> Planet.load_test_data(expression_csv) -> Planet.test(diffusion_pre, testdata, truelabel)`

Evidence:

- The README documents testing by constructing `Config()` and `Planet(args)`, loading pre-trained network weights, calling `load_test_data()`, then `test()`.
- `Planet.test()` runs `ensemble` reverse-diffusion generations, optionally through `joblib.Parallel(n_jobs=n_job)`, and returns `cal_final_net(all_adj_list)`.
- `load_test_data()` accepts CSV/XLSX expression matrices through `create_batch_dataset_from_cancer()` or upstream simulation pickle files through `create_batch_dataset_simu()`.

Excluded public modes:

- Training with `Planet.train()`: excluded because it creates a model checkpoint from labelled simulated or reference networks rather than performing one ANDREA inference run.
- Simulation `.data` testing: excluded because upstream requires a pickle containing both `exp` and `net`; normalized ANDREA inference inputs do not include upstream simulation objects or true labels.
- BRCA batch-training script: excluded because it trains across hard-coded external files and is not a general runtime entrypoint.
- `make_final_net.py` helpers: excluded because `Planet.test()` already calls the final-network helper.
- Resource download scripts: excluded from runtime; the Docker image uses bundled pinned repo resources and fails clearly if required resources are missing or corrupt.

## Execution Capabilities

- `global`: exposed. One expression matrix plus selected gene set produces one directed gene regulatory network.
- `group_emulated`: exposed. ANDREA can partition the expression matrix by `groups.tsv` and run the same global Planet contract independently per group.
- `group_native`: not exposed; Planet does not consume group labels.
- `column_native`: not exposed. Although the paper uses "cell-specific" language, the public CSV interface returns one adjacency matrix for the expression matrix/subnetwork it receives, not one network per expression column.
- `group_aggregated`: not exposed because there is no column-native Planet output for ANDREA to aggregate.

## Input Contract

Required normalized input:

- `expression.tsv`: gene x cell matrix, numeric values, unique public gene IDs and column IDs.

Conditional normalized input:

- `groups.tsv`: required only when `execution.mode=group_emulated`; ANDREA consumes it for orchestration, not Planet.

No new input spec is needed. The upstream TF and pathway resources are bundled in the pinned repo and are method resources, not user-supplied normalized inputs for the selected contract.

## Taxonomic Scope

- Selected contract: Homo sapiens only when a dataset NCBI taxon is declared.
- Evidence: the stable bundled TF list is `GRN/TF.txt` for human genes; `pathway/kegg.rar` contains human KEGG pathways and `pathway/Regnetwork.rar` contains `2022.human.source`.
- Mouse is not exposed despite paper examples, because `GRN/mouse_TF.txt` in the reviewed repo snapshot is an HTML 405 error page, not a valid TF table. Mouse KEGG and RegNetwork files exist in archives, but the selected runtime contract must not rely on a corrupt TF resource.

## Parameters and Defaults

- `gene_set`: required enum, default `null`. Implemented values are `all_expression_genes` followed by the 342 bundled human KEGG IDs from Planet's `KEGG_all_pathway.pkl`; GUI forms should keep an empty placeholder so the user explicitly chooses a value. Upstream test preprocessing requires a non-null gene set; `None` reaches `Other_Pathway[:3]` and fails. The wrapper validates KEGG IDs against the runtime KEGG resource and always writes the retained genes to an upstream-compatible temporary CSV before calling Planet. This also avoids the reviewed source bug where the CSV test path recognizes `mmu...` IDs directly but treats `hsa...` IDs as CSV paths. For the bundled pretrained checkpoint, retained genes must be 10 to 99; `all_expression_genes` is preflight-blocked when the dataset has more than 99 genes.
- `metacell`: bool, default `true`; passed to `args.metacell`.
- `knn`: int, default `20`; passed to `args.KNN`; wrapper validates `knn <= number_of_columns` when `metacell=true`.
- `metacell_count`: int, default `100`; passed to `args.Cnum`.
- `ensemble`: int, default `30`; passed to `args.ensemble`.
- `diffusion_timesteps`: int, default `1000`; passed to `args.diffusion_timesteps`.
- `sampling_timesteps`: int, default `1000`; passed to `args.sampling_timesteps`; wrapper validates `sampling_timesteps <= diffusion_timesteps`.

Fixed implementation choices:

- Use the repo-provided pre-trained checkpoint `pre-train/Pre-training_weights_on_simulated_datasets.pth`.
- Keep `use_pca="false"`, matching `config.py` default and avoiding an undeclared PCA artifact.
- Keep `Adddatabse=false`, `net_key_par` flags false, and training hyperparameters fixed/unexposed because the selected contract is pretrained inference, not training.
- Keep `max_nodes=None` for upstream preprocessing; in the CSV pathway code this becomes an effective loader `lim=200`, but wrapper validation uses the bundled checkpoint `max_nodes=99`, so selected gene sets must contain 10 to 99 retained expression genes.
- Keep `show=false` because ANDREA does not provide true labels for upstream evaluation progress.

Data-dependent upstream defaults and rules:

- `create_batch_dataset_from_cancer()` rejects gene sets with fewer than 10 or more than `lim` genes; with `max_nodes=None`, `from_cancer_create()` sets `lim=200`. The bundled pretrained checkpoint additionally declares `max_nodes=99`, so the wrapper enforces 10 to 99 selected genes before inference.
- `cal_metacell()` returns original columns unchanged when the number of cells is `<= Cnum`, but it still fits KNN first, so the wrapper requires `knn <= number_of_columns` when `metacell=true`.
- `Planet.__init__()` chooses CUDA if available. The wrapper forces CPU unless ANDREA later defines GPU runtime support.

## Runtime Resource Mapping

- Threading supported: yes.
- Mapping: ANDREA `--threads` sets `args.n_job=min(threads, ensemble)` for the `joblib.Parallel(n_jobs=self.n_job)` ensemble loop in `Planet.test()`.
- The wrapper sets PyTorch and BLAS/OpenMP intra-process thread counts to 1 to avoid nested oversubscription while joblib runs independent ensemble members. PyTorch interop configuration is idempotent and occurs before checkpoint inspection to avoid PyTorch's "parallel work has started" abort.
- No worker/thread control is exposed as a user parameter.

## Output Mapping to `network.csv`

- Direction: directed TF/source gene -> target gene. The paper defines edges as directed from transcription factors to target genes, and source code writes adjacency as `adj_matrix[i, j] = 1` for `TF, Gene`.
- Score: raw positive vote count from `cal_final_net()`, not ANDREA-normalized. The paper describes ensemble edge probability, but the reviewed public code returns summed final binary adjacency matrices; probability can be derived as `score / ensemble`.
- Sign: `?`; Planet does not output signed coefficients.
- Evidence: `association`.
- Filtering: omit self-loops and rows with `score <= 0`.
- Public IDs: use the gene IDs carried by `testdata.y` into the final adjacency DataFrame. The implemented wrapper does not introduce aliases, so no alias map is needed.
- Contexts: `global` for direct global runs. In `group_emulated`, ANDREA orchestration rewrites child outputs to `group:<group_id>`.

## Normalized Input Mapping

- Reused specs: `groups` only for `group_emulated` orchestration.
- Existing `tf_list` is not used in the selected contract because the stable human TF resource is bundled with the pinned repo.
- New specs required: none.

## Installation Strategy

- No installable package was found in the reviewed repo snapshot (`setup.py`, `pyproject.toml`, requirements/environment files absent). Public search also found no Planet package corresponding to this method; the PyPI name `planet` is unrelated.
- Preferred runtime installation: clone official GitHub repo and checkout pinned commit `3d8be3ca788dc436e8d2c888facdeffe49553c61`.
- Docker installs Python runtime dependencies with the Python package manager used by the wrapper and extracts `pathway/kegg.rar` and `pathway/Regnetwork.rar` during image build.
- Runtime does not depend on `wrappers/inference_tools/tools/planet/repo/`.
- Preflight compatibility includes a specific block for `param.gene_set=all_expression_genes` and `dataset.expression.genes > 99`, because that parameter selects every expression gene and would exceed the bundled checkpoint.

## Auxiliary Artifacts

- `planet.log`: combined wrapper and upstream diagnostics.
- `raw/adj_final.csv`: raw Planet final vote matrix before conversion to `network.csv`.
- `raw/planet_config.json`: resolved parameter values, selected gene set, resource files, checkpoint, thread mapping and retained dimensions.
- `raw/selected_genes.tsv`: gene IDs retained after gene-set/resource intersection.

## Wrapper Implementation Notes

- Runtime image clones `https://github.com/wangchuanyuan1/project-Planet`, checks out `3d8be3ca788dc436e8d2c888facdeffe49553c61`, and does not depend on the local `repo/` folder at runtime.
- The Dockerfile installs Python dependencies with `pip` for the same Python runtime that executes `run_tool.py`.
- Debian `unar` failed to extract the pinned `Regnetwork.rar` archive with `Attempted to read more data than was available`; the implemented Dockerfile enables Debian `contrib non-free non-free-firmware` and uses `unrar`, which extracts both `kegg.rar` and `Regnetwork.rar` successfully.
- The wrapper forces CPU execution and validates that checkpoint, KEGG, RegNetwork and human TF resources are present.
- The wrapper inspects checkpoint metadata at runtime and validates selected gene count, selected checkpoint feature count, `sampling_timesteps <= diffusion_timesteps`, metacell KNN feasibility, and nonzero bundled human RegNetwork overlap before calling the upstream public flow.
- The upstream `make_final_net.cal_final_net()` calls `cal_identify_TF_gene()` without passing `args.TF_file`, so it reads the default relative path `GRN/mouse_TF.txt`. In the pinned repo this file is corrupt HTML. The wrapper does not patch upstream source; it creates a per-run upstream work directory and places a valid human `TF.txt` copy at `GRN/mouse_TF.txt` so the selected human-only contract uses the stable human TF table while preserving public outputs.
- Expression gene IDs and column IDs are preserved. The wrapper writes an upstream CSV with an extra dummy expression column because the public CSV loader sets the first column as row names and then drops the first remaining expression column.

## Field Evidence Summary

| Field | Chosen value | Evidence / rationale |
|---|---|---|
| `id`, `name` | `planet`, `Planet` | Scaffold id and upstream README/paper title. |
| `publication`, `first_author`, `year` | DOI URL, Shiyu Xu, 2025 | Paper title page and citation text. |
| `method_summary`, `method_keywords` | diffusion, hybrid attention, single-cell, directed | Paper abstract and Methods sections describe these terms explicitly. |
| `implementation_url` | GitHub commit URL | Official repo README; no package source found. |
| `execution_capabilities` | `global`, `group_emulated` | Public CSV test path returns one matrix; groups can only be orchestrated externally. |
| `runtime_resources` | threading supported through joblib ensemble | `config.py` defines `n_job`; `Planet.test()` uses `Parallel(n_jobs=self.n_job)`. |
| `taxonomic_scope` | animal, species `9606` | Stable human resources exist; mouse TF file is corrupt in reviewed repo. |
| `accepts`, `assumes` | `cells`, `scrna_specific` | README and paper target scRNA-seq/cell-specific GRN generation. |
| `extra_inputs` | conditional `groups` for `group_emulated` | ANDREA orchestration requirement; upstream does not consume groups. |
| `params` | gene set, metacell/KNN/Cnum, ensemble, diffusion/sampling timesteps | Public `Config` and paper runtime/sampling discussion. `gene_set` is exposed as an enum generated from Planet's bundled human KEGG ids plus the wrapper sentinel `all_expression_genes` for GUI usability. |
| `outputs` | directed, unsigned association | Paper directed binary TF-target edges; code returns no sign. |
| `progress` | coarse lifecycle only | Public API exposes no stable callback; tqdm depends on `show` and truth labels. |
| `artifacts_aux` | log, raw adjacency, config, selected genes | Needed to audit upstream preprocessing and raw score conversion. |

## Smoketest Outcome

- Config: `wrappers/inference_tools/tests/smoketest_configs/planet.json`.
- Fixtures: `wrappers/inference_tools/tests/fixtures/planet/expression.tsv` and `groups.tsv`.
- Build command: `make build-tool-images ARGS="--tool planet"` passed.
- Static validation: `python -m py_compile wrappers/inference_tools/tools/planet/run_tool.py`, `make validate-toolspecs ARGS="--tool planet"`, `make validate-input-specs ARGS="--spec expression_matrix --spec groups"`, and `make validate-smoketest-configs ARGS="--tool planet"` passed.
- Smoketest command: `make run-tool-smoketests ARGS="--tool planet --threads 2 --timeout 2400 --show-output-lines 80"` passed after the `gene_set=all_expression_genes` preflight rule update.
- Variants covered: `global` produced 74 positive non-self-loop rows; `group_emulated_contract` produced 80 positive non-self-loop rows; both validated the four declared auxiliary artifacts.
- GUI regression check: rerunning preflight on `inferred_networks/gui_dataset_20260623T180159Z` now skips `planet__01` and `planet__02` before container launch when `gene_set=all_expression_genes` and the dataset has 120 genes.

## Known Limitations / Open Questions

- The selected contract is human-only until a valid pinned mouse TF resource is available.
- The pre-trained checkpoint is inspected at runtime inside the wrapper because the host environment may not have `torch`.
- The public source appears to contain a human KEGG handling bug in the CSV test path; the wrapper avoids patching algorithm internals by converting all accepted `gene_set` choices to the documented user gene-list CSV path.
- `gene_set=all_expression_genes` will fail for synthetic or non-human IDs, for more than 99 retained genes with the bundled checkpoint, or when there is no overlap with bundled human RegNetwork/TF resources.

## Post-GUI Run Follow-up

Observed on `inferred_networks/gui_dataset_20260623T214216Z`:

- The global Planet container exited with status 137 while planned through the generic 4 GB fallback, consistent with a container memory kill during default diffusion generation.
- The same run showed default Planet child tasks still in the first diffusion ensemble member after more than 18 minutes with `ensemble=30`, `diffusion_timesteps=1000`, and `sampling_timesteps=1000`.
- Added `andrea/catalog_inference_tools/tools/planet/cost.json` as a conservative planner profile so future plans reserve 8 GB per default Planet task and display multi-hour ETA instead of the previous fallback estimate.
- The ToolSpec descriptions for `ensemble`, `diffusion_timesteps`, and `sampling_timesteps` now document runtime scaling and bounded-runtime overrides. The defaults themselves remain aligned with the selected upstream public configuration.
