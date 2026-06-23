# GRouNdGAN Integration Decisions

Phase: 3 completed. The executable wrapper is under
`wrappers/simulation_data_tools/simulators/groundgan/`; the catalog spec is
`andrea/catalog_simulation_data_tools/simulators/groundgan/simulatorspec.json`.

## Upstream And Installation

- Upstream repository: `https://github.com/Emad-COMBINE-lab/GRouNdGAN`.
- Pinned commit: `2df087f9144081c46eb6ce0a1daadd273adcc50a`.
- Publication references:
  - `https://doi.org/10.1038/s41467-024-48516-6`
  - `https://doi.org/10.5281/zenodo.11068246`
- First author: Yazdan Zinati.
- License: AGPL-3.0-or-later from upstream `LICENSE`.
- Docker image: `adriansegura99/simulator_groundgan:1.0.0`.
- Installation route: the Dockerfile clones the public upstream GitHub repository
  and checks out the pinned commit. It does not depend on local `repo/` at
  runtime.
- Dependency decision: the reviewed upstream `requirements.txt` is problematic
  in the local clone and includes routes not used by the executable contract.
  The image installs the minimal runtime needed for causal-GAN checkpoint
  inference (`torch`, `anndata`, `numpy`, `pandas`, `scipy`, `scikit-learn`,
  `matplotlib`, `tensorboard`). `scanpy` is not installed; the wrapper provides
  a tiny `scanpy.read_h5ad` stub before importing upstream because `gans.gan`
  imports `sc_dataset` even when no training loaders are used.

Evidence:
- `repo/GRouNdGAN/README.md`
- `repo/GRouNdGAN/docs/installation.rst`
- `repo/GRouNdGAN/docs/tutorial.rst`
- `repo/GRouNdGAN/src/factory.py`
- `repo/GRouNdGAN/src/gans/causal_gan.py`
- `repo/GRouNdGAN/src/perturbation/perturbation.py`
- `papers/GRouNdGAN.txt`

## Claimed Semantic Capabilities

The executable wrapper claims only capabilities that can be produced from a
single pretrained causal-GAN run without fabricating truth contexts.

| Capability | ANDREA axes | Truth | Decision |
| --- | --- | --- | --- |
| Causal GAN generation | `rna_expression/single_cell/cells/observational` | `global` native | Claimed. Uses upstream `CausalGANFactory` and `CausalGAN.generate_cells()`. |
| TF perturbation generation | `rna_expression/single_cell/cells/perturbational` | `global` native | Claimed. Uses upstream causal generator perturbation mode: frozen TF expressions/noise/LSN with configured TF replacement values. |
| Group-specific GRNs | any | `group` | Not claimed. A single run imposes one global causal graph. |
| Cell-specific GRNs | any | `column` | Not claimed. Upstream does not emit cell-specific simulator truth. |
| Training from reference data | single-cell | global possible | Scientifically possible but not exposed in Phase 2 because upstream training is GPU-oriented and the paper/tutorial describe very long runs for realistic models. |
| GRNBoost2 graph creation | single-cell | global possible | Not exposed in Phase 2; it changes graph source, not semantic capability, and requires a separate expensive preprocessing/training route. |
| scGAN/cWGAN/conditional GAN | single-cell | none | Not claimed because those modes do not impose a known GRN suitable for ANDREA truth outputs. |
| Trajectory/pseudotime preservation | single-cell | none | Not claimed; the paper evaluates pseudotime preservation, but upstream does not emit generated pseudotime/timepoint truth. |
| Bulk/pseudobulk | bulk/pseudo_bulk | global possible by aggregation | Not claimed because aggregation would be wrapper post-processing, not native GRouNdGAN output. |

## Truth Context Audit

For both claimed capabilities:

- `global`: native. The imposed `causal_graph.pkl` is the simulator's data-generating regulatory graph. Public rows use `context=global`.
- `group`: unavailable. Duplicating the same imposed global graph for clusters would be misleading.
- `column`: unavailable. Cell-specific networks in the publication are inference-tool outputs, not simulator truth.

Truth row semantics:

- `source` and `target`: mapped from native graph indices through
  `groundgan_reference_h5ad.var_names`.
- `score`: `1.0`, because native graph stores topology only.
- `sign`: `?`, because native graph is unsigned.
- `evidence`: `simulated_truth`, matching ANDREA's public truth contract.

## Inputs

Default and embedded input routes:

- `input_bundle=toy_4gene` uses a tiny pretrained bundle embedded inside the
  Docker image at `/opt/andrea/bundles/groundgan/toy_4gene`.
- `input_bundle=toy_20gene` and `input_bundle=toy_50gene` use larger demo
  bundles embedded inside the same image.
- This keeps the GUI usable without asking the user for checkpoint files during
  ordinary smoke/demo runs.

Conditional custom-file inputs:

- `groundgan_reference_h5ad`: provides public gene IDs and gene order.
- `groundgan_causal_graph_pickle`: native dict `{target_index: {regulator_index}}`.
- `groundgan_model_checkpoint`: trained GRouNdGAN checkpoint containing
  `generator_state_dict` and `critic_state_dict`.
- `groundgan_causal_controller_checkpoint`: causal-controller checkpoint
  containing `generator_state_dict`.

These four files are required only when `input_bundle=custom_files`.

Not used by the executable spec:

- `regulatory_network`

Reason: public graph conversion/training routes remain future work; exposing them
without a validated runtime would make the GUI/planner promise unsupported work.
No GroundGAN-specific TF table input spec is kept because the executable wrapper
derives TFs from the imposed causal graph.

Gene-count decision:

- The number of output genes is not a public run parameter for the executable
  checkpoint route.
- It is fixed by `groundgan_reference_h5ad.var_names` and must match the supplied
  causal graph and checkpoints.
- The embedded toy bundles produce fixed 4-, 20- and 50-gene toy universes.
  Generating any other gene count requires `input_bundle=custom_files` with a
  compatible reference/causal-graph/checkpoint bundle trained for that gene
  universe.

## Parameter Surface

Public executable params:

- `input_bundle`: `toy_4gene`, `toy_20gene` and `toy_50gene` use embedded demo
  bundles; `custom_files` activates the four external bundle inputs.
- `run_command`: locked by capability to `generate` or `perturb`.
- `generation.num_cells`: number of generated observational cells or matched
  perturbation pairs.
- `preprocessing.library_size`: library size required to instantiate the supplied
  checkpoint.
- `perturbation.*`: TF target selection and replacement values for the
  perturbational capability.

Intentionally not exposed:

- `device`: runtime placement is wrapper/environment controlled.
- `model.*`, `causal_controller.*` and `training.batch_size`: these are
  checkpoint/runtime metadata, not scientific dataset controls. The wrapper
  infers the causal-GAN architecture from `groundgan_model_checkpoint` and
  `groundgan_causal_controller_checkpoint`, stages the causal-controller
  checkpoint as upstream expects, and uses an internal inference batch size.
- training optimizer, learning-rate, preprocessing and GRNBoost2 controls:
  scientifically valid but not implemented in this executable contract.
- `--evaluate` and `--benchmark_grn`: evaluation utilities, not dataset
  generation outputs.

## Runtime Resources

- `runtime_resources.threading.supported=true`.
- Mapping: wrapper sets `torch.set_num_threads(threads)` and `OMP_NUM_THREADS`,
  `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS`.
- Upstream has no explicit `--threads`, `n_jobs`, worker or DataLoader worker
  generation option.
- GPU is not represented in the current SimulatorSpec resource contract; high
  quality training should wait for a future GPU/runtime contract.

## Output Normalization

Common outputs:

- `expression.tsv`: genes as rows, public generated columns as cells.
- `truth/networks.csv`: one global unsigned imposed causal graph.
- `truth/gene_universe.txt`: all expression genes from reference `var_names`.
- `simulator-output-manifest.json`.
- `progress.json`.
- `provenance/raw/`: request snapshot, resolved params, resolved upstream config,
  copied inputs/checkpoints, public ID maps, session info and raw h5ad outputs.

Observational extras/native outputs:

- Derivable extras: `enrichment_background`, `prior_grn`, `tf_list`.
- Native outputs: `simulated_h5ad`, `causal_graph_pickle`.

Perturbational extras/native outputs:

- Required/derivable extras: `perturbation_design`, `interventions`.
- Optional derivable extras: `enrichment_background`, `prior_grn`, `tf_list`.
- Native outputs: `before_perturbation_h5ad`, `after_perturbation_h5ad`,
  `causal_graph_pickle`.

Public ID policy:

- Gene IDs always come from `groundgan_reference_h5ad.var_names`.
- Causal graph indices must be within that gene universe.
- Observational columns are `cell_001`, `cell_002`, ...
- Perturbational columns are matched pairs:
  `pair_001_control`, `pair_001_perturbed`, ...
- `perturbation_design.tsv` carries `matched_pair_id` so pairing is not encoded
  only in column names.

## Smoke Tests

Implemented configs:

- `groundgan_single_cell_observational_global.json`
  - covers observational/global capability;
  - uses embedded `input_bundle=toy_4gene`;
  - requests `enrichment_background`, `prior_grn`, `tf_list`;
  - requests `simulated_h5ad` and `causal_graph_pickle`.
- `groundgan_single_cell_observational_toy20_global.json`
  - covers embedded `input_bundle=toy_20gene`.
- `groundgan_single_cell_observational_toy50_global.json`
  - covers embedded `input_bundle=toy_50gene`.
- `groundgan_single_cell_observational_custom_global.json`
  - covers the `input_bundle=custom_files` path and the conditional input
    requirement contract;
  - uses the same tiny fixture bundle as external files.
- `groundgan_single_cell_perturbational_global.json`
  - covers perturbational/global capability;
  - uses embedded `input_bundle=toy_4gene`;
  - requests `perturbation_design`, `interventions`,
    `enrichment_background`, `prior_grn`, `tf_list`;
  - requests `before_perturbation_h5ad`, `after_perturbation_h5ad` and
    `causal_graph_pickle`.

Fixtures:

- `wrappers/simulation_data_tools/tests/fixtures/groundgan/reference.h5ad`
- `wrappers/simulation_data_tools/tests/fixtures/groundgan/causal_graph.pkl`
- `wrappers/simulation_data_tools/tests/fixtures/groundgan/model_checkpoint.pth`
- `wrappers/simulation_data_tools/tests/fixtures/groundgan/causal_controller_checkpoint.pth`

The Dockerfile generates embedded `toy_4gene`, `toy_20gene` and `toy_50gene`
bundles during image build using `build_toy_bundles.py`. The fixture files remain
only to cover the `custom_files` path in smoke tests.

Verification completed:

- `python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py --simulator groundgan`
- `python wrappers/simulation_data_tools/scripts/validate_input_specs.py`
- `python wrappers/simulation_data_tools/scripts/validate_smoketest_configs.py --simulator groundgan`
- `docker build -f wrappers/simulation_data_tools/simulators/groundgan/Dockerfile -t adriansegura99/simulator_groundgan:1.0.0 .`
- `python wrappers/simulation_data_tools/scripts/run_smoketests.py --simulator groundgan --skip-build --show-output`
- `PYTHONPATH=. .venv/bin/pytest -q tests/wrappers/simulation_data_tools/test_generate_data_schemas.py tests/wrappers/simulation_data_tools/test_input_specs.py tests/wrappers/simulation_data_tools/test_simulatorspecs.py tests/core/commands/generate_data/test_semantic_model.py tests/core/commands/generate_data/test_bootstrap.py tests/cli/test_generate_data_cli.py tests/gui/test_generate_data_server.py`
- `PYTHONPATH=. .venv/bin/pytest -q tests/core/commands/generate_data/test_generate_data.py -k 'not run_generate_data_dyngen_grouped and not run_generate_data_freezes and not run_generate_data_preserves and not run_generate_data_pulls_missing_docker_image and not docker_runner'`
- `PYTHONPATH=. .venv/bin/pytest -q tests/core/test_generate_data_progress.py tests/wrappers/simulation_data_tools/test_simulation_smoketests.py`
- `andrea generate-data execute` on observational and perturbational GroundGAN requests using the smoke-test fixtures.
- `andrea generate-data execute` on `input_bundle=toy_50gene` without user-supplied inputs.
- `andrea infer-network preflight` on generated `dataset-manifest.json` files.
- Independent JSON Schema validation of generated `benchmark-manifest.json`,
  `dataset-manifest.json`, `ground-truth-manifest.json` and
  `provenance/simulator-output-manifest.json`.

## Remaining Work

- Future expansion could add a validated training route after ANDREA has a GPU
  resource contract and a clear plan for large model/checkpoint provenance.
- Future group truth would require a defensible multi-model input bundle with one
  checkpoint and imposed graph per group; it should not be simulated by copying
  the global graph into group contexts.
