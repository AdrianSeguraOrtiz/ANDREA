# inferelator3 Integration Decisions

Phase: updated after Phase 3 to support the new execution-capabilities contract and Inferelator native grouped execution.

## Sources Reviewed

- Upstream repo: `wrappers/inference_tools/tools/inferelator3/repo/inferelator/`
- Upstream docs:
  - `repo/inferelator/README.md`
  - `repo/inferelator/docs/tutorial.rst`
  - `repo/inferelator/docs/workflow.rst`
  - `repo/inferelator/docs/results.rst`
  - `repo/inferelator/docs/references.rst`
- Upstream implementation:
  - `repo/inferelator/setup.py`
  - `repo/inferelator/inferelator/workflow.py`
  - `repo/inferelator/inferelator/workflows/workflow_base.py`
  - `repo/inferelator/inferelator/workflows/tfa_workflow.py`
  - `repo/inferelator/inferelator/workflows/single_cell_workflow.py`
  - `repo/inferelator/inferelator/workflows/amusr_workflow.py`
  - `repo/inferelator/inferelator/regression/bbsr_python.py`
  - `repo/inferelator/inferelator/regression/amusr_regression.py`
  - `repo/inferelator/inferelator/postprocessing/results_processor.py`
  - `repo/inferelator/inferelator/postprocessing/results_processor_mtl.py`
  - `repo/inferelator/inferelator/postprocessing/inferelator_results.py`
- Local primary paper:
  - `wrappers/inference_tools/tools/inferelator3/papers/btac117.pdf`
  - `wrappers/inference_tools/tools/inferelator3/papers/btac117.txt`

## Implemented Contract

- Wrapper path: `wrappers/inference_tools/tools/inferelator3/run_tool.py`
- Dockerfile path: `wrappers/inference_tools/tools/inferelator3/Dockerfile`
- ToolSpec path: `andrea/catalog_inference_tools/tools/inferelator3/toolspec.json`
- Runtime install: `python -m pip install "numpy<2" inferelator==0.6.3 joblib==1.5.3`
- Runtime source: official PyPI package; no runtime dependency on the local `repo/` folder.
- Build registration: `wrappers/inference_tools/scripts/template_map.json` maps `inferelator3` to the Python runtime bundle.

The ToolSpec now declares:

- `execution_capabilities`: `["global", "group_native", "group_emulated"]`
- `execution.mode=global`: one upstream single-task run on the full expression matrix.
- `execution.mode=group_emulated`: ANDREA partitions expression by `groups.tsv`; each physical child run uses the same global wrapper path, and the orchestrator rewrites child output context to `group:<label>`.
- `execution.mode=group_native`: one upstream multitask Inferelator run. The wrapper passes full expression plus `groups.tsv` as metadata and exports per-task upstream networks as `context=group:<label>`.

Compatibility note: `execution.group_mode` is accepted only as a deprecated input alias by the core resolver. New plans, GUI requests and ToolSpecs use `execution.mode` and `execution_capabilities`.

## Upstream Entrypoints

- Global and emulated grouped execution mirror `inferelator_workflow(regression="bbsr", workflow="tfa")`, configured with public setters and executed with `worker.run()`.
- Native grouped execution mirrors `inferelator_workflow(regression=<amusr|bbsr-by-task|elasticnet-by-task>, workflow="multitask")`.
- Native grouped tasks are created through the public `create_task(...)` API with `tasks_from_metadata=True` and `meta_data_task_column="cluster"`.

Evidence:

- Standard public workflow and `worker.run()`: `repo/inferelator/docs/tutorial.rst`
- Multitask workflow options `amusr`, `bbsr-by-task`, `elasticnet-by-task`: `repo/inferelator/docs/tutorial.rst`
- Workflow factory and allowed workflow/regression strings: `repo/inferelator/inferelator/workflow.py`
- Task creation and task metadata splitting: `repo/inferelator/inferelator/workflows/amusr_workflow.py`
- Per-task and aggregate multitask result files: `repo/inferelator/inferelator/postprocessing/results_processor_mtl.py`

Rationale: workflow choice is an execution capability, while the concrete multitask regression engine is a parameter. This keeps native grouped support available without exploding execution modes into algorithm-specific names.

## Inputs

- Reused `expression_matrix`: ANDREA provides genes x observations; the wrapper transposes to upstream samples x genes and sets `expression_matrix_columns_are_genes=True`.
- Reused `tf_list`: upstream regulator list.
- Added/reused `prior_grn`: normalized long global prior with `source,target,score`, converted to upstream `[Genes x Regulators]`.
- Reused `groups`: optional globally, required when `execution.mode` is `group_native` or `group_emulated`.

Conditional input behavior:

- ToolSpec rule: `groups` is conditionally required when `execution.mode == "group_native"` so Inferelator can create native multitask tasks from metadata.
- ToolSpec rule: `groups` is conditionally required when `execution.mode == "group_emulated"` because ANDREA partitions the dataset by group before running the global wrapper path.
- GUI/core/CLI requirement checks consume the ToolSpec `conditional_required` rules; `plan.py` and `run.py` retain defensive checks before materializing group partitions.

Not exposed:

- `gold_standard_file`: scoring input, not needed for inference; wrapper sets `use_no_gold_standard=True`.
- upstream no-prior mode: upstream warns it is inadvisable, and the primary method description is prior-informed.
- timecourse/perturbation metadata modes: no normalized inference input currently models those semantics.

## Parameters

- `regression`: enum default `auto`.
  - `auto` maps to `bbsr` for `global` and `group_emulated`.
  - `auto` maps to `amusr` for `group_native`.
  - `group_native` also accepts `amusr`, `bbsr-by-task`, and `elasticnet-by-task`.
  - `global` and `group_emulated` currently accept `auto` or `bbsr`.
- `num_bootstraps`: upstream run parameter; ToolSpec default `2`.
- `random_seed`: upstream run parameter; ToolSpec default `42`.
- `bsr_feature_num`: BBSR parameter; ToolSpec default `10`.
- `prior_weight`: BBSR/AMuSR prior-edge weight; ToolSpec default `1.0`.
- `no_prior_weight`: BBSR non-prior-edge weight; ToolSpec default `1.0`.
- `clr_only`: BBSR predictor-selection switch; ToolSpec default `false`.

Evidence:

- Run parameter setters and defaults: `repo/inferelator/inferelator/workflows/workflow_base.py`
- BBSR defaults and setter docs: `repo/inferelator/inferelator/regression/bbsr_python.py`
- AMuSR regression parameters: `repo/inferelator/inferelator/regression/amusr_regression.py`
- Multitask regression option list: `repo/inferelator/docs/tutorial.rst`

Data-dependent upstream defaults preserved:

- Bootstrap sample matrices are generated by upstream from the post-filter observation count.
- BBSR may cap or adjust usable predictors according to available regulators and CLR/prior filtering. The wrapper passes `bsr_feature_num` and does not precompute a replacement.
- `regression=auto` is wrapper-level routing, not an upstream replacement for a data-dependent default.

## Output Mapping

Raw upstream outputs are kept under `raw/`. `network.csv` maps:

- `source`: upstream `regulator`
- `target`: upstream `target`
- `score`: upstream `combined_confidences`
- `sign`: `+`/`-` from `model_coefficient` or `beta.sign.sum`, otherwise `?`
- `evidence`: `association`
- `context`: `global` for global wrapper output; `group:<label>` for native grouped output and orchestrator-finalized emulated grouped output

Score decision:

- Preserve raw upstream `combined_confidences` directly.
- Do not apply ANDREA-specific normalization in the wrapper.
- Drop exact zero-score edges before writing `network.csv`.

Evidence:

- Long network output columns and score semantics: `repo/inferelator/docs/results.rst`
- Result file writer defaults: `repo/inferelator/inferelator/postprocessing/inferelator_results.py`
- Confidence/ranking processing: `repo/inferelator/inferelator/postprocessing/results_processor.py`
- Multitask per-task file writer: `repo/inferelator/inferelator/postprocessing/results_processor_mtl.py`

## Field-by-Field ToolSpec Evidence

- `id = inferelator3`: scaffold/catalog directory names.
- `name = Inferelator 3.0`: upstream README and primary paper title.
- `publication`: primary Inferelator 3.0 DOI plus BBSR/original Inferelator DOIs from paper/docs; stored as canonical `https://doi.org/...` URLs.
- `first_author = Claudia Skok Gibbs`: primary paper author order; full name recorded.
- `year = 2022`: primary `btac117` publication.
- `method_summary`: primary paper and README describe TFA from expression plus prior knowledge followed by regularized regression; includes package support for bulk, single-cell and multitask methods.
- `method_keywords`: derived from paper/repo terms: regularized regression, transcription factor activity, prior knowledge, BBSR, directed GRNs, single-cell.
- `implementation_url = https://pypi.org/project/inferelator/0.6.3/`: integrator clarification and upstream README installation route.
- `docker_image`: project naming convention, not upstream-derived.
- `execution_capabilities`: standard TFA workflow plus upstream multitask workflow and ANDREA emulated group partitioning.
- `accepts = ["samples", "cells"]`: tutorial/paper describe samples and single-cell transcriptomes; timepoints/perturbations are not claimed without a metadata input contract.
- `assumes = generic`: upstream supports bulk and single-cell data.
- `extra_inputs`: `tf_list` and `prior_grn` are required by the selected prior-informed workflow; `groups` is conditional for grouped modes.
- `outputs`: directed regulator-target edges with unsigned confidence and optional coefficient sign.
- `progress`: BBSR progress uses MI/CLR and per-target regression jobs; AMuSR native grouped mode currently reports coarse phase progress.
- `params`: public workflow/regression parameters above.
- `artifacts_aux`: upstream log plus `network.tsv.gz`, `combined_confidences.tsv.gz`, `model_coefficients.tsv.gz`, and `inferelator_model.h5ad`; smoke validation confirms all exist.

Uncertainty:

- Sign is `mixed` because the primary score is unsigned and sign comes from model coefficients.
- `progress.kind` remains `target_genes` because that is the strongest granular evidence for BBSR; native AMuSR exposes less stable job-level progress.
- `elasticnet-by-task` is exposed only for native grouped execution via the upstream multitask public interface; it is not implemented as a standalone global mode in this wrapper.

## Runtime Notes

- Docker pins `numpy<2` because `inferelator==0.6.3` fails the smoke BBSR path with current NumPy 2.x scalar assignment behavior.
- Inferelator 0.6.3 also calls `np.isdtype`; the wrapper installs a small NumPy 1.x compatibility shim before importing/using Inferelator.
- `threads` remains an ANDREA runtime argument and maps to Inferelator `MPControl`; it is not a ToolSpec parameter.

## Verification

Validated successfully:

- `python -m py_compile wrappers/inference_tools/tools/inferelator3/run_tool.py wrappers/inference_tools/scripts/run_smoketests.py wrappers/inference_tools/scripts/validate_smoketest_configs.py wrappers/inference_tools/scripts/validate_toolspecs.py andrea/core/commands/infer_network/commons/tools.py andrea/core/commands/infer_network/plan.py andrea/core/commands/infer_network/run.py andrea/core/commands/infer_network/commons/runtime_helpers.py`
- `python wrappers/inference_tools/scripts/validate_toolspecs.py`
- `python wrappers/inference_tools/scripts/validate_smoketest_configs.py`
- `python wrappers/inference_tools/scripts/run_smoketests.py --tool inferelator3 --threads 2 --timeout 900 --show-output-lines 20`
- `python wrappers/inference_tools/scripts/run_smoketests.py --tool inferelator3 --threads 2 --timeout 900 --skip-image-build`
- `PYENV_VERSION=3.10.7 python -m pytest tests/core/commands/infer_network/test_preflight.py tests/core/commands/infer_network/test_plan.py tests/core/commands/infer_network/test_run.py -q`
- `PYENV_VERSION=3.10.7 python -m pytest tests/gui/test_infer_network_server.py -q`
- Manual preflight/plan check with `execution.mode=group_native`; generated one logical run with one physical task and `execution_mode=group_native`.
- Manual preflight/plan check with `execution.mode=group_emulated`; generated one logical run with two physical tasks for two groups and `execution_mode=group_emulated`.

Smoketest outcome:

- `global` variant passed and wrote 5 non-zero directed edges.
- `group_native` variant passed and wrote 16 non-zero directed edges with `context=group:*`.
- Both variants wrote `progress.json` and the declared auxiliary raw artifacts.

Environment note:

- The default Python 3.13 interpreter lacks `pytest` and `rich`; pytest/core checks were run under the local `PYENV_VERSION=3.10.7` environment where those dependencies are installed.
