# GeneNetWeaver Integration Decisions

Phase 3 status: executable wrapper, Dockerfile, catalog SimulatorSpec, smoke-test matrix, generate-data execution and infer-network preflight handoff are implemented and validated. The active executable spec is `andrea/catalog_simulation_data_tools/simulators/genenetweaver/simulatorspec.json`.

## Evidence Reviewed

- `repo/genenetweaver/README.md`: GNW is an open-source Java tool for in silico GRN benchmark generation and performance profiling.
- `repo/genenetweaver/CITATION.cff`: primary citation DOI, first author and implementation URL.
- `repo/genenetweaver/CHANGELOG`: command-line interface and batch generation are supported in GNW 3.x.
- `repo/genenetweaver/gnw-3.1.2b.jar --help`: public CLI supports `--simulate`, `--extract`, `--transform`, `--evaluate`, `--input-net`, network formats and output path flags.
- `repo/genenetweaver/sandbox/settings.txt`: complete settings surface loaded by `GnwSettings`, including steady-state toggles, time-series toggles, ODE/SDE controls, perturbation settings and noise settings.
- `repo/genenetweaver/src/ch/epfl/lis/gnw/GnwSettings.java`: every settings key is required; `maxtTimeSeries` must be divisible by `dt`; `outputDirectory` is loaded from settings.
- `repo/genenetweaver/src/ch/epfl/lis/gnw/BenchmarkGeneratorDream4.java`: writes gold-standard, signed gold-standard, expression, perturbation and normalization files.
- `repo/genenetweaver/src/ch/epfl/lis/gnw/TimeSeriesExperiment.java`: native time-series tables include explicit measured time rows.
- `papers/bioinformatics_27_16_2263.txt`: paper describes steady-state and time-series expression data for wild-type, knockout, knockdown, dual-knockout and multifactorial perturbation experiments, ODE/SDE simulation and measurement noise.
- Local probe with `gnw-3.1.2b.jar --simulate` on `sandbox/InSilicoSize10-Yeast1.xml`: confirmed output filenames and table shapes.

## Installation And Version

- Chosen route: install from the public upstream GitHub repository pinned to commit `c5310349f5d5723306585c2bb62aedbdeb70db46`, then execute the official `gnw-3.1.2b.jar`.
- Rationale: no Python/R package or Maven-style public package was found in the reviewed repo; the official repo includes the runnable JAR and example sandbox XML.
- Docker image: `adriansegura99/simulator_genenetweaver:1.0.0`.
- Runtime dependency expectation: Java runtime only. No MATLAB/R/Python simulator runtime is required.
- Uncertainty: the JAR version reports `3.1.2 Beta`, while `CITATION.cff` says version `3.1.0`. The wrapper should record both the pinned commit and JAR-reported version under provenance.

## Public Inputs

The default runnable path should use the official pinned sandbox XML `sandbox/InSilicoSize10-Yeast1.xml` as `network_preset=in_silico_size10_yeast1`, so the simulator can run out-of-the-box.

Conditional input:

- `gnw_dynamical_network`: required only when `network_preset=custom_xml`.
- Spec file added: `andrea/catalog_simulation_data_tools/input_specs/gnw_dynamical_network.json`.
- Schema update: simulation input specs now allow `format="xml"`.

Not claimed as Phase 1 inputs:

- TSV/GML/DOT network extraction inputs. GNW can extract subnetworks and transform formats, but claiming this would require a two-step extract/transform/simulate contract, generated model naming, and additional smoke tests. Keep this out until the wrapper supports it directly.
- Preloaded perturbation sidecar files via `loadPerturbations=1`. This expects GNW-specific file names and locations and is not modeled as a stable ANDREA input contract yet.

## Claimed Semantic Capabilities

### Bulk perturbational

- `measurement`: `rna_expression`
- `resolution`: `bulk`
- `column_kind`: `perturbations`
- `experimental_design`: `perturbational`
- Truth requirements: `global`
- Upstream switches:
  - `simulation_design=perturbational_steady_state` locks this capability.
  - Enable exactly one requested steady-state family through `ssKnockouts`, `ssKnockdowns`, `ssMultifactorial`, `ssDualKnockouts`, `ssDREAM4TimeSeries`, or enable all when explicitly requested.
  - Disable all `ts*` settings.
  - Map `solver=ode` to `simulateODE=1, simulateSDE=0`; map `solver=sde` to `simulateODE=0, simulateSDE=1`.
- Public expression:
  - Normalize one selected mRNA table family into `expression.tsv`.
  - Transpose if necessary so rows are genes and columns are public perturbation columns.
- Public truth:
  - `global`: native, from `*_goldstandard_signed.tsv`.
  - `group`: unavailable.
  - `column`: unavailable.
- Extras:
  - Derivable: `perturbation_design`, `interventions`, `enrichment_background`, `prior_grn`, `tf_list`.
  - Native-but-not-standard public outputs: gold-standard files, expression table directory, perturbation sidecars, XML/SBML model, normalization constant.

### Bulk time-series

- `measurement`: `rna_expression`
- `resolution`: `bulk`
- `column_kind`: `timepoints`
- `experimental_design`: `time_series`
- Truth requirements: `global`
- Upstream switches:
  - `simulation_design=time_series` locks this capability.
  - Enable exactly one requested time-series family through `tsDREAM4TimeSeries`, `tsKnockouts`, `tsKnockdowns`, `tsMultifactorial`, `tsDualKnockouts`, or enable all when explicitly requested.
  - Disable all `ss*` settings unless a future wrapper deliberately normalizes a combined output family.
  - `numTimeSeries=time_series.num_time_series`, `maxtTimeSeries=time_series.maxt`, `dt=time_series.dt`.
  - `maxtTimeSeries` must be divisible by `dt`; Phase 2 wrapper must reject incompatible values before invoking GNW.
- Public expression:
  - Normalize measured time rows from the selected GNW time-series output into columns such as `series_<n>_t_<time>`.
  - Preserve `series_id` and `timepoint` in `extras/timepoints.tsv`.
- Public truth:
  - `global`: native, from `*_goldstandard_signed.tsv`.
  - `group`: unavailable.
  - `column`: unavailable.
- Extras:
  - Derivable: `timepoints`, `perturbation_design`, `interventions`, `enrichment_background`, `prior_grn`, `tf_list`.

## Truth Context Audit

For both claimed capabilities:

- `global`: native. GNW writes `*_goldstandard_signed.tsv` with source, target and sign. The wrapper should emit one `truth/networks.csv` row per signed non-self edge with `context=global`, `evidence=simulated_truth`, `score=1.0`, and sign copied from GNW.
- `group`: unavailable. GNW does not emit group-specific regulatory networks. Deriving groups from perturbation family or time-series block and duplicating the same GRN would not be a distinct truth context.
- `column`: unavailable. GNW uses one fixed dynamical network for all perturbation columns and timepoints; no perturbation-specific or timepoint-specific rewiring is produced.

Score/sign limitations:

- The signed gold-standard file carries topology and sign, not kinetic edge magnitudes. Kinetic XML parameters affect expression dynamics but are not exposed as public truth scores.
- The wrapper should use `score=1.0` for signed gold-standard edges. If a future implementation decides to use kinetic magnitudes, that must be separately justified and smoke-tested.

## Parameter Surface

Exposed draft parameters:

- `network_preset`: official sandbox XML or custom XML input.
- `network_name`: normalized run/root naming.
- `simulation_design`: scenario-controlled and locked by `parameter_bindings`.
- `solver`: selects ODE or SDE normalized expression source.
- `expression_variant`: normalized, nonoise or noexpnoise mRNA table.
- `steady_state_experiment`: selected steady-state perturbation family.
- `time_series_experiment`: selected time-series family.
- `steady_state.*`: `maxtSteadyStateODE`, `maxtSteadyStateSDE`, `mintSDE`.
- `time_series.*`: `numTimeSeries`, `maxtTimeSeries`, `dt`.
- `perturbations.*`: multifactorial, DREAM4 perturbation probability, deletion/overexpression and direct-target fraction settings.
- `ode.*`: ODE precision settings.
- `sde.*`: SDE step and molecular-noise coefficient.
- `experimental_noise.*`: normal/lognormal/microarray measurement-noise settings.
- `goldstandard.*`: autoregulatory and zero-interaction gold-standard export settings.

Intentionally not exposed:

- `outputDirectory`: wrapper-owned. The local probe showed GNW requires an absolute `outputDirectory` in `settings.txt`; `--output-path` alone is not reliable.
- `randomSeed`: should map from ANDREA run seed for reproducibility rather than being an independent simulator parameter.
- `outputGenesInRows`: wrapper-owned because ANDREA normalizes `expression.tsv` shape explicitly.
- `loadPerturbations`: fixed false until GNW perturbation sidecar inputs are represented as stable ANDREA input specs.
- `modelTranslation`: fixed true by default; protein outputs are native outputs, not normalized RNA expression.
- CLI extraction/transform/evaluation switches: not part of generation wrapper behavior.

Compatibility rules in draft:

- `min_gene_deletion_effect <= max_gene_deletion_effect`.
- `min_gene_overexpression_effect <= max_gene_overexpression_effect`.
- `min_fraction_direct_targets <= max_fraction_direct_targets`.
- Additional Phase 2 wrapper validation required: `time_series.maxt % time_series.dt == 0`.

## Output Normalization Contract

Wrapper must write directly under `/work/out/`:

- `expression.tsv`: genes as rows, public perturbation/timepoint columns.
- `truth/networks.csv`: columns compatible with ANDREA truth schema; use `evidence=simulated_truth`.
- `truth/gene_universe.txt`: exact expression gene IDs, one per line.
- `extras/perturbation_design.tsv`: one row per expression column for perturbational/time-series capabilities.
- `extras/interventions.tsv`: distinct interventions derived from perturbation design.
- `extras/timepoints.tsv`: time-series capability only; one row per expression column.
- Required extra: `tf_list.txt`.
- Optional extras: `enrichment_background.txt`, `prior_grn.tsv`.
- `native/`: requested native outputs, including GNW gold-standard files, raw expression tables, perturbation sidecars, XML/SBML model and normalization constant.
- `provenance/raw/`: raw GNW output tree, resolved `settings.txt`, config snapshot, CLI log, Java/GNW session info and input copies.
- `simulator-output-manifest.json`.

Public ID rules:

- Gene IDs must be identical across `expression.tsv`, `truth/networks.csv`, `truth/gene_universe.txt`, `prior_grn.tsv`, `tf_list.txt` and `enrichment_background.txt`.
- Perturbation/timepoint column IDs must be identical across `expression.tsv`, `perturbation_design.tsv`, `timepoints.tsv` and manifest entries.

## Runtime Resources

- Threading support: `supported=false`, `default_threads=1`, `max_threads=1`.
- Evidence: CLI help and `settings.txt` expose no threads/workers/processes parameter; reviewed Java settings load no parallelism control.
- ANDREA mapping: reject or cap requested threads to 1 for this simulator; do not expose thread controls as simulator params.

## Capabilities Not Claimed

- Group truth: not claimed because GNW does not emit group-specific GRNs.
- Column truth: not claimed because GNW has one fixed network per run.
- Single-cell, spatial, pseudo-bulk and trajectory/differentiation: not supported by GNW outputs reviewed.
- Bulk observational/steady-state wildtype-only: not claimed because the useful GNW benchmark expression columns are perturbation experiments; a single wildtype steady state is not a robust benchmark profile.
- Network extraction from TSV/GML/DOT: scientifically available, but not claimed until the wrapper can execute and test the extract-to-dynamical-model path.
- Evaluation mode: GNW can evaluate predictions, but this belongs to `evaluate-inference`, not simulator generation.

## Phase 2 Smoke-Test Matrix

Implemented smoke configs:

1. `genenetweaver_bulk_perturbational_global.json`, default sandbox XML, `steady_state_experiment=knockouts`.
   - Proves `expression.tsv`, global truth, `perturbation_design`, `interventions`, `prior_grn`, `tf_list`, `enrichment_background`.
2. `genenetweaver_bulk_time_series_global.json`, default sandbox XML, `time_series_experiment=dream4_timeseries`, small `num_time_series`, small `maxt/dt`.
   - Proves `timepoints`, time-series perturbation design, global truth and ID consistency.
3. `genenetweaver_custom_xml_global.json`, mounted XML input using `gnw_dynamical_network`.
   - Proves conditional simulator input resolution and provenance preservation.

Native output coverage is included in the first three smoke configs:

- `gnw_goldstandard`
- `gnw_goldstandard_signed`
- `gnw_expression_tables`
- `gnw_perturbation_tables`
- `gnw_sbml_model`
- `gnw_normalization_constant`

Negative tests:

- User-supplied `simulation_design` contradicts the selected scenario and is rejected by `parameter_bindings`.
- Missing `gnw_dynamical_network` with `network_preset=custom_xml` is rejected before Docker execution.
- `time_series.maxt` not divisible by `time_series.dt` is rejected before GNW execution.
- Requested `group` or `column` truth blocks because no capability claims those truth contexts.

## Non-Trivial Spec Field Decisions

- `publication`: `["https://doi.org/10.1093/bioinformatics/btr373"]`; evidence `CITATION.cff`; canonical DOI URL.
- `first_author`: `Thomas Schaffter`; evidence `CITATION.cff`; full first-author name.
- `year`: `2011`; evidence primary Bioinformatics paper; publication year.
- `implementation_url`: `https://github.com/tschaffter/genenetweaver`; evidence `CITATION.cff`; public upstream repo.
- `docker_image`: `adriansegura99/simulator_genenetweaver:1.0.0`; required by playbook.
- `extra_inputs.conditional_required`: `gnw_dynamical_network` only for `network_preset=custom_xml`; evidence CLI `--simulate --input-net`; rationale keeps default preset runnable while supporting custom models.
- `runtime_resources.threading`: unsupported; evidence CLI/settings review; no thread control exposed.
- `capabilities[bulk perturbational]`: evidence paper lines describing knockout/knockdown/multifactorial perturbation expression and settings `ss*`; rationale columns are perturbations, not samples.
- `capabilities[bulk time_series]`: evidence paper and native `*_dream4_timeseries.tsv` time column; rationale columns are measured timepoint observations.
- `truth_contexts.global`: native; evidence `*_goldstandard_signed.tsv`; uncertainty only around whether kinetic magnitudes should ever become scores, deferred because signed file has no magnitudes.
- `truth_contexts.group/column`: unavailable; evidence fixed GNW dynamical network and no per-group/per-timepoint gold standards.
- `derivable_extras`: all standardized extras are wrapper-derived from GNW native tables; no standardized extra is marked native because GNW filenames and schemas do not match ANDREA normalized extras directly.

## Cost Profile Status

- No measured `cost.json` is included for GeneNetWeaver yet.
- Rationale: Phase 3 validated functionality, schema compatibility and handoff, but did not run a calibrated cost benchmark. The generate-data planner therefore uses conservative fallback ETA values until `make benchmark-simulator-costs ARGS="--simulator genenetweaver"` is run and validated.

## Validation

- `python -m py_compile wrappers/simulation_data_tools/simulators/genenetweaver/run_simulator.py`: passed.
- `make validate-simulatorspecs ARGS="--simulator genenetweaver"`: passed.
- `make validate-simulator-smoketest-configs ARGS="--simulator genenetweaver"`: passed.
- `make validate-simulation-input-specs`: passed for all 9 simulation input specs.
- `make run-simulator-smoketests ARGS="--simulator genenetweaver --skip-build"`: passed all 3 GeneNetWeaver smoke configs.
- `./.venv/bin/python -m pytest tests/wrappers/simulation_data_tools/test_simulatorspecs.py tests/wrappers/simulation_data_tools/test_generate_data_schemas.py tests/core/commands/generate_data/test_generate_data.py tests/cli/test_generate_data_cli.py tests/gui/test_generate_data_server.py -q`: 91 passed.
- `andrea generate-data execute` for a bulk perturbational GeneNetWeaver run produced a benchmark package and dataset/ground-truth manifests.
- `andrea generate-data execute` for a bulk time-series GeneNetWeaver run produced a benchmark package and dataset/ground-truth manifests.
- `andrea infer-network preflight --dataset-manifest <generated dataset-manifest.json>` passed for both generated GeneNetWeaver dataset manifests.

Observed implementation details from smoke tests:

- The wrapper copies both bundled and custom XML inputs to `provenance/raw/input_network.xml` before executing GNW. Therefore raw GNW output prefixes are stable as `input_network_*`, independent of the upstream XML basename.
- GNW may write empty stdout/stderr logs for successful runs. The wrapper still preserves `provenance/raw/upstream_stdout.log` and `upstream_stderr.log`, but smoke tests do not require those log files to be non-empty.
- `native/` stores only requested native outputs, while the full raw GNW output tree is always preserved under `provenance/raw/gnw_output/`.
