# MetaSEM Integration Decisions

Phase: 3 finalized. Wrapper, Dockerfile, ToolSpec, input specs, template-map registration and smoketest config are validated.

## Sources Reviewed

- Integration playbook: `wrappers/inference_tools/TOOL_INTEGRATION_PLAYBOOK.md`
- Upstream repo snapshot: `wrappers/inference_tools/tools/metasem/repo/MetaSEM/`
- Upstream commit: `482987b360ca57172f0276fd64e27b2681223b00`
- Upstream remote: `https://github.com/ZhangLab312/MetaSEM.git`
- Local paper PDF: `wrappers/inference_tools/tools/metasem/papers/MetaSEM.pdf`
- Extracted paper text: `wrappers/inference_tools/tools/metasem/papers/MetaSEM.txt`
- Entry/docs reviewed:
  - `MetaSEM_main.py`
  - `Tutorial.ipynb`
  - `SRC/MetaSEM_Train_GRN_inference.py`
  - `SRC/MetaSEM_Train_robust.py`
  - `SRC/MetaSEM_Model.py`
  - `SRC/MetaSEM_Model_robust.py`
  - `SRC/MetaSEM_tool.py`
- Implemented wrapper files:
  - `wrappers/inference_tools/tools/metasem/run_tool.py`
  - `wrappers/inference_tools/tools/metasem/Dockerfile`
  - `wrappers/inference_tools/tools/metasem/patched_src/MetaSEM_Model.py`
  - `wrappers/inference_tools/tools/metasem/patched_src/MetaSEM_tool.py`
  - `wrappers/inference_tools/tools/metasem/patched_src/MetaSEM_Train_GRN_inference.py`
  - `wrappers/inference_tools/tests/smoketest_configs/metasem.json`

## Paper Preparation

- PDF found: `MetaSEM.pdf`
- Extracted text used: `MetaSEM.txt`
- Extraction quality: sufficient for title, DOI, authors, abstract, method, implementation, data availability and keywords. Some table/body formatting is noisy, but the relevant evidence is readable.

## Method Summary

MetaSEM is a scRNA-seq GRN inference method based on meta-learning. The paper describes an encoder, meta-decoder and GRN layer; the GRN layer embeds a structural equation model adjacency matrix as directed regulatory weights and bi-level optimization trains pseudo-labels/feature extraction for sparse high-dimensional single-cell data.

## Upstream Interface Audit

| Upstream public entrypoint / mode | Evidence | Inputs | Output | ANDREA mapping | Exposed? | Rationale |
| --- | --- | --- | --- | --- | --- | --- |
| `python MetaSEM_main.py --task GRN_inference --setting default` | `MetaSEM_main.py`; `Tutorial.ipynb` documents this command for GRN inference | demo expression path and demo network path are hard-coded by the script; implementation class reads an AnnData/CSV expression file and `net_path` CSV | `Output<cell>.tsv` with `TF`, `Target`, `EdgeWeight` | `global`; `group_emulated` by ANDREA partitioning | Yes, via the implementation class rather than the CLI | This is the documented GRN inference path. The wrapper must call the selected implementation directly because the CLI ignores `--data_file` and `--net_file` in the documented `default` branch. |
| `python MetaSEM_main.py --task GRN_inference --setting test` | `MetaSEM_main.py` constructs `Train_test(opt)` with the same demo paths | same hard-coded demo expression/network | benchmark/evaluation TSV | not exposed | No | It is a benchmark/test variant, not a distinct public inference contract. It still uses demo paths and ground-truth metrics. |
| `python MetaSEM_main.py --task Robust --setting default` | `MetaSEM_main.py`; `SRC/MetaSEM_Train_robust.py`; tutorial command for Robust | hard-coded robustness experiment paths under `Data/GRN_robust` | robustness/evaluation TSV and metrics | not exposed | No | This is an experiment for robustness over data scales, not the primary GRN inference interface. The checked source also contains `for epoch in range(self.epoch)` although only `opt.epoch` is parsed, so it is not a stable wrapper contract. |
| `SRC/MetaSEM_tool.py` helpers | helper functions only | tensors/model instances | gradients/parameter updates | implementation detail | No | Not a public data-level inference entrypoint. |

Selected upstream public entrypoint: the documented `GRN_inference/default` path, mirrored through `SRC.MetaSEM_Train_GRN_inference.Train_inference.train_model(input_path, net_path)` rather than through `MetaSEM_main.py`, because the public script hard-codes demo I/O and does not honor the CLI input paths in this branch.

## Execution Capabilities

- `global`: exposed. One expression matrix and one prior/pseudo GRN produce one directed gene-gene network.
- `group_emulated`: exposed. ANDREA can partition expression by `groups.tsv`, run the same global MetaSEM wrapper once per group, and rewrite final contexts to `group:<group_id>`.
- `group_native`: not exposed. MetaSEM does not consume group metadata or return multiple group networks in one public run.
- `column_native`: not exposed. MetaSEM outputs one adjacency/edge table for the input matrix, not one network per cell.
- `group_aggregated`: not exposed. There is no native column-level output to aggregate.

## Required Inputs

- Normalized expression matrix: scRNA-seq cells/samples by genes. Upstream tutorial says to store data as sample-gene CSV; the code reads with `scanpy.read(input_path)` and uses `data.X`, `data.obs_names`, and `data.var_names`.
- MetaSEM still needs an upstream `net_path` CSV, but that file does not always have to come from user-provided `prior_grn`. Upstream `data_prepare()` always reads `net_path` and uses its `Gene1`/`Gene2` columns to derive TFs, all genes and masks. The tutorial says that if no ground-truth GRN is available, the user should initialize a pseudo GRN using prior knowledge or randomly and set `is_label` to none.
- ToolSpec default is `pseudo_grn_mode=unlabeled_all_genes`: the wrapper should generate a deterministic pseudo-GRN scaffold from expression genes and set `opt.is_label=False`. This gives users the no-ground-truth path in ANDREA without requiring an external `prior_grn`.

## Optional and Conditional Inputs

- `groups`: conditionally required only when `execution.mode == "group_emulated"`. MetaSEM itself does not read group metadata; ANDREA needs it to create per-group physical runs.
- `prior_grn`: conditionally required only when `pseudo_grn_mode == "provided_prior"`. In that mode the wrapper converts normalized `source,target,score` rows to MetaSEM `Gene1,Gene2` rows and sets `opt.is_label=True`.
- `tf_list`: not used as a separate extra input. Candidate TFs are inferred from `prior_grn.source` / upstream `Gene1`.
- No new normalized input spec is required. Existing `prior_grn` matches the optional provided-prior source-target-score semantics closely enough; the wrapper will convert it to upstream `Gene1,Gene2` CSV and ignore `score` except for validating nonzero prior edges.

## Parameters and Defaults

ToolSpec exposes the important hard-coded GRN-inference defaults. The Dockerfile clones the pinned upstream repo and applies narrow replacement files under `patched_src/` to make those settings effective.

Fixed implementation choices from the selected upstream path:

- `task=GRN_inference` and `setting=default` are fixed; other public modes are documented above as not exposed.
- `alpha=0.45`, `gamma=0.95`, `gamma_meta=0.85`, `lr=1e-3`, `lr_meta=5e-4`, `lr_step_size=1`, `lr_step_size_meta=1`, and `batch_size=64` are assigned in `MetaSEM_main.py` for the selected `GRN_inference/default` branch. ToolSpec exposes the scientifically relevant values except the step sizes, which remain fixed at 1 because the selected upstream branch hard-codes them.
- The selected upstream loop was fixed at 20 epochs (`for epoch in range(20)`). The patched training file now uses the configured `epochs` value.
- The selected upstream path used `DataLoader(..., batch_size=64)`. The patched training file now uses the configured `batch_size` value.
- `net_size=1000` was hard-coded in `MetaSEM_main.py`, and `SRC/MetaSEM_Model.py` hard-coded 1000-dimensional meta-decoder layers. The patched model/training files now derive dimensions from the expression gene count.
- `is_label` is parsed by the CLI but not safely typed; passing the string `"None"` would still be truthy in Python. The wrapper must instantiate the option object directly with a real boolean:
  - `pseudo_grn_mode=provided_prior`: `is_label=True`
  - `pseudo_grn_mode=unlabeled_all_genes`: `is_label=False`
- `n_hidden` is parsed by the CLI but not used by the selected implementation path.

Data-dependent implementation rule implemented:

- `opt.net_size` is set to the number of expression genes after ANDREA subsetting and prior/pseudo-GRN alignment.

Runtime-dependent thread assignment is handled by `runtime_resources.threading`, not by a tool parameter.

## Output Mapping to `network.csv`

Evidence:

- `extractEdgesFromMatrix(m, geneNames, TFmask)` copies the learned matrix, applies the TF mask, keeps all `abs(mat) > 0` entries, and emits `TF = geneNames[idx_send]`, `Target = geneNames[idx_rec]`, `EdgeWeight = mat[idx_rec, idx_send]`.
- The paper states that the final GRN layer output is an adjacency matrix representing the GRN and that elements describe directed edge weights.
- `get_AUPR()` takes `abs(output['EdgeWeight'])` only for evaluation; the raw TSV preserves the signed `EdgeWeight`.

Wrapper mapping:

- `source = TF`
- `target = Target`
- `score = abs(EdgeWeight)`
- `sign = 1` if `EdgeWeight > 0`, `-1` if `EdgeWeight < 0`
- Drop rows with `score <= 0`.
- Drop self-loops. Upstream TF/evaluation masks skip diagonal entries; the wrapper should enforce this on export.
- `evidence = association`
- Preserve public gene IDs from expression and prior inputs. No upstream alias map is required by the reviewed code, but Phase 2 should add one if needed after testing numeric, duplicate-looking or file-unsafe gene identifiers.

## Installation Strategy

Preferred public installation source:

- No installable package was found with `python -m pip index versions MetaSEM`.
- The repo contains no `setup.py`, `pyproject.toml`, `requirements*.txt`, or environment file.

Pinned source:

- Install from the official upstream repository at commit `482987b360ca57172f0276fd64e27b2681223b00`.
- Implementation URL in ToolSpec uses the pinned GitHub tree.
- Runtime dependencies must be installed explicitly for the Python interpreter that executes the wrapper. Evidence from `Tutorial.ipynb`: Python >= 3.6, PyTorch >= 1.2.0, `scanpy==1.6.0`, `numpy==1.14.5`, `pandas==1.0.0`, `scikit-learn==0.23.2`. Modern container compatibility may require newer compatible versions, but Phase 2 must record any deviation.

## Runtime Resources

- Upstream exposes no CLI `threads`, `workers`, `n_jobs`, multiprocessing, joblib or sharding control.
- Training is implemented in PyTorch and uses `torch.utils.data.DataLoader` without `num_workers`, so DataLoader workers stay at upstream default 0.
- ToolSpec declares threading supported by backend/runtime control: the wrapper should set PyTorch CPU intra-op threads to `--threads`, keep inter-op threads at 1, and set common BLAS/OpenMP environment variables to the assigned thread count.
- Reviewed source called `.cuda()` unconditionally and set `Tensor = torch.cuda.FloatTensor`. The patched training file is device-aware and runs in the CPU-only container used by ANDREA.

## ToolSpec Evidence Ledger

| Field | Chosen value | Evidence | Rationale / uncertainty |
| --- | --- | --- | --- |
| `schema_version` | `1.0` | Existing ToolSpec schema and catalog conventions | Required schema version. |
| `id` | `metasem` | scaffold path and requested tool id | Stable lowercase catalog id. |
| `name` | `MetaSEM` | paper title and repo name | Preserves upstream capitalization. |
| `publication` | `https://doi.org/10.3390/ijms24032595` | paper title page and citation line | Canonical DOI URL as required. |
| `first_author` | `Yongqing Zhang` | paper author list | Full first author name. |
| `year` | `2023` | paper citation and publication date | Publication year. |
| `method_summary` | meta-learning encoder/meta-decoder/SEM GRN layer summary | abstract, Figure 1 caption, Sections 3.2-3.3 | Wording describes method-level evidence, not wrapper behavior. |
| `method_keywords` | `single_cell`, `meta_learning`, `structural_equation_model`, `bi_level_optimization`, `few_shot_learning`, `directed` | paper keywords and method sections | Lowercase snake_case summary of paper concepts. |
| `implementation_url` | pinned GitHub tree | local git remote and requested commit | Package unavailable; pin source for reproducibility. |
| `docker_image` | `adriansegura99/inference-tools_metasem:1.0.0` | project image naming conventions | Draft image name for Phase 2. |
| `execution_capabilities` | `global`, `group_emulated` | selected entrypoint returns one matrix for one expression input; ANDREA supports group emulation for global tools | No native grouped or cell-specific public output. |
| `runtime_resources` | threading supported via PyTorch/BLAS env | PyTorch source imports and training loops; no worker controls found | Wrapper sets PyTorch intra-op threads and BLAS/OpenMP env vars from `--threads`, with inter-op threads pinned to 1. |
| `taxonomic_scope` | all broad groups, no species IDs | method uses expression and supplied prior/pseudo GRN; paper evaluates mouse/human data but no bundled species resource is required | No species-level restriction found. |
| `compatibility_rules` | empty | no species restriction found | Gene-count limitation should be removed by the Phase 2 dynamic-dimension patch rather than represented as a dataset rule. |
| `accepts` | `cells` | title, abstract and tutorial refer to scRNA-seq and sample-gene matrices | Single-cell expression is the primary method domain. |
| `assumes` | `scrna_specific` | paper title and problem statement | MetaSEM is designed for scRNA-seq GRN inference. |
| `extra_inputs.required` | none | tutorial allows no ground-truth GRN via pseudo GRN and `is_label=None`; wrapper can generate MetaSEM's required `net_path` scaffold | Avoids blocking users who do not have a prior/ground truth. |
| `extra_inputs.conditional_required` | `prior_grn` for `pseudo_grn_mode=provided_prior`; `groups` for `group_emulated` | `data_prepare()` always reads `net_path`; tutorial pseudo-GRN note; ANDREA group emulation contract | `prior_grn` is only needed for provided-prior mode; `groups` is required for orchestration, not consumed by MetaSEM. |
| `outputs` | directed, signed, association | paper GRN layer directed weights; `extractEdgesFromMatrix()` emits signed `EdgeWeight` | Raw signed coefficient is split into positive magnitude and sign. |
| `progress` | `iterations` | upstream prints epoch metrics inside the training loop | Patched training loop calls wrapper progress callback once per configured epoch. |
| `params` | `pseudo_grn_mode`, `epochs`, `batch_size`, `alpha`, `lr`, `lr_meta`, `gamma`, `gamma_meta`, `random_seed` | tutorial no-ground-truth note; `MetaSEM_main.py` default branch; source seed constants | Exposes important hard-coded values made effective by the patched source. Step-size and hidden-layer quirks remain fixed implementation choices. |
| `artifacts_aux` | log, raw edge TSV, config JSON | upstream writes raw TSV and prints metrics; wrapper preserves config | No alias map was needed; IDs round-trip through pandas/CSV without internal renaming. |

## Normalized Input Mapping

Reused input specs:

- `prior_grn`: source/target/score TSV, conditionally required for `pseudo_grn_mode=provided_prior`. The wrapper should filter or validate nonzero scores, require source and target genes to be present in expression, and write `Gene1,Gene2` columns for MetaSEM.
- `groups`: used only by ANDREA for `group_emulated`.

New input specs required:

- None.

## Implemented Wrapper Notes

- Runtime does not depend on the local `repo/` folder. The Dockerfile clones `https://github.com/ZhangLab312/MetaSEM.git`, checks out `482987b360ca57172f0276fd64e27b2681223b00`, and removes `.git`.
- The Dockerfile applies narrow, recorded replacement files after cloning the pinned repo:
  - make CUDA usage device-aware for CPU containers;
  - make model dimensions depend on `opt.net_size` / expression gene count instead of literal 1000;
  - make the training loop use configured `epochs`;
  - make DataLoader use configured `batch_size`;
  - update the Adam helper to use PyTorch 2-compatible scalar tensor step state.
- Wrapper converts ANDREA expression into upstream sample-gene CSV while preserving gene and cell IDs.
- For `pseudo_grn_mode=provided_prior`, wrapper converts `prior_grn.tsv` to upstream `Gene1,Gene2` CSV and runs with `is_label=True`.
- For `pseudo_grn_mode=unlabeled_all_genes`, wrapper writes one cyclic non-self pseudo edge per expression gene and runs with `is_label=False`.
- Wrapper preserves raw `EdgeWeight` in `raw/metasem_edges.tsv` and exports `network.csv` with `score=abs(EdgeWeight)`, `sign` as `+`/`-`, `evidence=association`, and `context=global`.
- Wrapper records upstream ref, source patch list, parameter values, gene/cell counts, prior/pseudo edge count, label mode and runtime thread assignment in `raw/metasem_config.json`.

## Smoketest Outcome

Command:

```bash
make run-tool-smoketests ARGS="--tool metasem --threads 2 --timeout 1800 --show-output-lines 80"
```

Outcome: passed.

- `global_unlabeled_pseudo`: passed; wrote 56 positive directed non-self rows and 3 auxiliary artifacts.
- `global_provided_prior`: passed; wrote 21 positive directed non-self rows and 3 auxiliary artifacts.
- `group_emulated_contract`: passed; supplied `groups.tsv` for ANDREA orchestration contract and wrote 56 positive directed non-self rows and 3 auxiliary artifacts. The physical wrapper output context remains `global`; ANDREA finalization owns `group:<id>` rewriting for actual group-emulated runs.

Phase 3 validation:

- `python -m py_compile wrappers/inference_tools/tools/metasem/run_tool.py wrappers/inference_tools/tools/metasem/patched_src/MetaSEM_Model.py wrappers/inference_tools/tools/metasem/patched_src/MetaSEM_tool.py wrappers/inference_tools/tools/metasem/patched_src/MetaSEM_Train_GRN_inference.py`
- `make validate-toolspecs ARGS="--tool metasem"`
- `make validate-input-specs`
- `make validate-smoketest-configs ARGS="--tool metasem"`
- `make run-tool-smoketests ARGS="--tool metasem --threads 2 --timeout 1800 --show-output-lines 80"`

## Known Limitations / Open Questions

- The selected public script ignores user-provided input paths in `GRN_inference/default`; wrapper invokes the implementation class directly.
- The wrapper intentionally patches the pinned upstream source at build time. These patches are constrained to runtime portability and making documented/default parameters effective.
- The `is_label` CLI flag is not safely typed upstream. The wrapper does not pass it through the CLI; it builds the option object directly from `pseudo_grn_mode`.
- `group_emulated` is an ANDREA orchestration mode, not a MetaSEM-native mode. The physical wrapper run emits `context=global`.
