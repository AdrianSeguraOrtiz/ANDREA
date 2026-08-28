# SynTReN Integration Decisions

Phase 3 status: executable wrapper, Dockerfile, catalog SimulatorSpec, smoke-test matrix, generate-data execution and infer-network preflight handoff are validated. The active spec is `andrea/catalog_simulation_data_tools/simulators/syntren/simulatorspec.json`.

## Evidence Reviewed

- `papers/1471-2105-7-43.txt`: SynTReN generates synthetic transcriptional regulatory networks and corresponding microarray-like expression datasets from source-network topology, nonlinear regulatory functions, external conditions and noise.
- `repo/syntren1.2release/doc/RELEASE NOTES.txt`: version 1.2 dated 2007-06-08; expression output is genes by conditions; `_unnormalized_dataset.txt` is preferred; CLI supports user external-input files and concentration-series experiments.
- `repo/syntren1.2release/doc/additional documentation.html`: documents `NetworkGeneratorCLI`, `IniSettings`, generated filenames and sample ini files.
- `repo/syntren1.2release/data/samples/*.ini`: documents task switches, source SIF, externals file, noise and sampling controls.
- `repo/syntren1.2release/data/samples/externalsFile.txt`: predefined external input table with `Regulators` plus one column per experiment/condition.
- `repo/syntren1.2release/data/sourceNetworks/*.sif`: native source topology format uses regulator, interaction token and target.
- `repo/syntren1.2release/LICENSE-AGREEMENT.TXT`: academic-use license; non-academic use requires contacting authors.
- Local jar inspection: `SynTReN.jar` contains `NetworkGeneratorCLI` and `IniSettings`.
- Official paper supplement `https://static-content.springer.com/esm/art%3A10.1186%2F1471-2105-7-43/MediaObjects/12859_2005_782_MOESM3_ESM.zip`: public but its `SynTReN.jar` lacks the CLI class.
- Archived official site `https://web.archive.org/web/20191023060930/http://bioinformatics.intec.ugent.be/kmarchal/SynTReN/index.html`: lists version 1.2 and `syntren1.2release.zip`; the ZIP itself was not retrievable from Wayback.
- Softpedia page `https://www.softpedia.com/get/Science-CAD/SynTReN.shtml`: independently lists filename `syntren1.2release.zip` and Java requirement, but is not treated as an official install source.
- Runtime probes: OpenJDK 17 fails with bundled XStream 1.2.1; OpenJDK 8 plus XStream 1.4.7, xmlpull 1.1.3.1 and xpp3_min 1.1.4c executes the SynTReN 1.2 CLI successfully.

## Installation And Version

- Chosen Docker route: copy the verified local SynTReN 1.2 release into the image at build time and verify `SynTReN.jar` checksum.
- Rationale: no live official package, public repository, tag or commit could be found. The official supplement is public but does not contain the headless CLI needed by ANDREA.
- Runtime dependency patch: the container uses OpenJDK 8 and places XStream 1.4.7 plus xmlpull/xpp3 ahead of the bundled XStream 1.2.1 on classpath. This is a compatibility fix for XML deserialization; the simulator execution remains SynTReN's public Java CLI/API.
- Checksums:
  - SynTReN local jar: `1830a5d10c909b135040181b22470aac84ffb6f1ffc085f91089c92f728b8f5a`.
  - XStream 1.4.7: `7f8039c0ee7284f9c2a9554b5e2bc20bf26b74b37f690633a75ff1993136f364`.
  - xmlpull 1.1.3.1: `34e08ee62116071cbb69c0ed70d15a7a5b208d62798c59f2120bb8929324cb63`.
  - xpp3_min 1.1.4c: `bfc90e9e32d0eab1f397fb974b5f150a815188382ac41f372a7149d5bc178008`.
- Docker image: `adriansegura99/simulator_syntren:1.0.0`.

## Public Inputs

- `syntren_source_network_sif`: required when `source_network.preset=custom_sif`. This is SynTReN's three-token SIF format, not the generic ANDREA signed regulatory TSV.
- `syntren_externals_table`: required when `external_inputs.fixed_source=custom_table`. This is the upstream externals file used with `externalInputValues=FROM_EXTERNALS_FILE`.

Not exposed:

- Pre-generated GeneNetwork XML. Although SynTReN can separate network generation and expression generation, XML is an internal native artifact for the current wrapper and is not a stable ANDREA input contract yet.
- GUI execution.

## Claimed Capabilities

### Bulk Observational Samples

- Axes: `measurement=rna_expression`, `resolution=bulk`, `column_kind=samples`, `experimental_design=observational`.
- Truth requirements: `global`.
- Upstream switches: `createGeneNetwork=true`, `generateExpressionData=true`, `externalInputValues=RANDOMIZED`, `fixedExternals=false`.
- Scenario binding: `external_inputs.mode=randomized`.
- Public truth:
  - `global`: native topology exported from generated GeneNetwork XML through `GeneNetwork.toSIF()`.
  - `group`: unavailable.
  - `column`: unavailable.
- Extras: `enrichment_background`, `prior_grn`, `tf_list`.

### Bulk Perturbational External-Input Conditions

- Axes: `measurement=rna_expression`, `resolution=bulk`, `column_kind=perturbations`, `experimental_design=perturbational`.
- Truth requirements: `global`.
- Upstream switches: `createGeneNetwork=true`, `selectSubnetwork=false`, `fixedExternals=true`, `generateExpressionData=true`, `externalInputValues=FROM_EXTERNALS_FILE`.
- Scenario bindings: `external_inputs.mode=from_file`, `subnetwork.enabled=false`.
- Public truth:
  - `global`: native topology exported from generated GeneNetwork XML through `GeneNetwork.toSIF()`.
  - `group`: unavailable.
  - `column`: unavailable.
- Extras: `perturbation_design`, `interventions`, `enrichment_background`, `prior_grn`, `tf_list`.

## Truth Context Audit

- `global`: native topological truth. The wrapper exports the generated GeneNetwork XML to SIF with a small Java helper that calls SynTReN's `GeneNetwork.fromXMLFile()` and `GeneNetwork.toSIF()`, then writes `truth/networks.csv` with `context=global`, `evidence=simulated_truth`, `score=1.0`.
- `group`: unavailable. SynTReN does not emit group-specific GRNs.
- `column`: unavailable. External conditions alter expression values but do not rewire the regulatory network per expression column.

Score/sign semantics:

- SynTReN's generated SIF export writes generated edges with token `un` in the verified route.
- Public truth therefore uses `sign=?` for SynTReN edges. Internal activation/repression choices influence expression generation but are not exposed as a stable signed GRN artifact by the public API route.
- `prior_grn.tsv` is topological and uses positive `score=1.0` for exported non-self edges.

## Parameter Surface

Exposed serializable parameters:

- `source_network.preset`: bundled source SIF or `custom_sif`.
- `subnetwork.enabled`, `subnetwork.method`, `subnetwork.num_nodes`, `subnetwork.num_background_nodes`.
- `interactions.use_edge_types_from_sif`, `interactions.percent_activators`, `interactions.interaction_category`, `interactions.higher_order_probability`.
- `external_inputs.mode`: scenario-controlled.
- `external_inputs.fixed_source`: bundled concentration-series table or custom externals table.
- `external_inputs.num_externals`, `external_inputs.num_correlated_externals`, `external_inputs.correlation_noise`.
- `sampling.burn_in`, `sampling.num_experiments`, `sampling.samples_per_experiment`.
- `noise.biological`, `noise.input`, `noise.experimental`.
- `expression_variant`: `unnormalized` or `max_expr_1`.

Intentionally wrapper-owned:

- `randomSeed`: mapped from ANDREA run seed.
- `outputdir`, `GeneNetworkXMLFile`, `NetworkSIFFile`, `externalsFile`: resolved under `/work`.
- `createGeneNetwork`, `generateExpressionData`, `fixedExternals`: derived from selected capability and wrapper contract.
- Thread controls: upstream exposes none.

## Output Normalization

Wrapper writes directly under `/work/out/`:

- `expression.tsv`: SynTReN genes by conditions matrix normalized to ANDREA TSV.
- `truth/networks.csv`: global topological truth with `sign=?`.
- `truth/gene_universe.txt`: exact expression genes.
- Required extra: `extras/tf_list.txt`.
- Optional extras:
  - `extras/enrichment_background.txt`
  - `extras/prior_grn.tsv`
  - `extras/perturbation_design.tsv` for perturbational runs
  - `extras/interventions.tsv` for perturbational runs
- `native/`: requested native-facing files: raw datasets, generated SIF/XML, resolved externals table and resolved ini.
- `provenance/raw/`: request snapshot, resolved params, resolved ini, input copies, raw SynTReN output, exported SIF, command logs, Java/session info and checksums.
- `simulator-output-manifest.json`.

ID rules:

- Gene IDs match across expression, truth, gene universe, prior GRN, TF list and enrichment background.
- Perturbation column IDs match across expression and perturbation design.
- Interventions use regulator IDs only when the regulator is present in the expression gene universe.

## Runtime Resources

- Threading support: `supported=false`, `default_threads=1`, `max_threads=1`.
- Evidence: reviewed ini/CLI docs expose no thread or worker control.
- ANDREA mapping: run with `runtime_resources.threads=1`; wrapper rejects other values.

## Capabilities Not Claimed

- Single-cell, spatial, pseudo-bulk, trajectory and differentiation: not supported by reviewed SynTReN outputs.
- Bulk time-series: not claimed; concentration series are external-input perturbational conditions, not temporal measurements with timepoint metadata.
- Group/column truth: not claimed because SynTReN has one fixed generated TRN per run.
- Signed GRN truth: not claimed because the verified public export path preserves topology but not stable signed edge labels.
- XML input: deferred until it is useful as a stable public input, not just an internal native artifact.

## Smoke-Test Matrix

Implemented smoke configs:

1. `syntren_bulk_observational_global.json`
   - Bundled E. coli source network, randomized externals.
   - Proves observational capability, global truth, `enrichment_background`, `prior_grn`, `tf_list`, and all native output IDs.
2. `syntren_bulk_perturbational_global.json`
   - Bundled E. coli source network and bundled externals table.
   - Proves perturbational capability, `perturbation_design`, `interventions`, global truth and native outputs.
3. `syntren_custom_inputs_perturbational_global.json`
   - Mounted `syntren_source_network_sif` and `syntren_externals_table`.
   - Proves both conditional input specs and public ID consistency.

Negative behavior implemented in wrapper:

- Rejects `runtime_resources.threads != 1`.
- Rejects scenario-controlled `external_inputs.mode` mismatches.
- Rejects perturbational runs with `subnetwork.enabled=true`.
- Rejects missing `syntren_source_network_sif` or `syntren_externals_table` when their params require them.

## Validation

- `python -m py_compile wrappers/simulation_data_tools/simulators/syntren/run_simulator.py`: passed.
- `make validate-simulatorspecs ARGS="--simulator syntren"`: passed.
- `make validate-simulator-smoketest-configs ARGS="--simulator syntren"`: passed.
- `make validate-simulation-input-specs`: passed for all 11 simulation input specs.
- `docker build -f wrappers/simulation_data_tools/simulators/syntren/Dockerfile -t adriansegura99/simulator_syntren:1.0.0 .`: passed.
- `make run-simulator-smoketests ARGS="--simulator syntren --skip-build"`: passed.
- `make verify-simulator SIMULATOR=syntren`: passed; rebuilds the image and runs the full SynTReN smoke matrix.
- `.venv/bin/python -m pytest tests/wrappers/simulation_data_tools/test_generate_data_schemas.py tests/wrappers/simulation_data_tools/test_simulatorspecs.py tests/wrappers/simulation_data_tools/test_input_specs.py tests/core/commands/generate_data/test_semantic_model.py tests/core/commands/generate_data/test_bootstrap.py tests/core/commands/generate_data/test_generate_data.py tests/cli/test_generate_data_cli.py tests/gui/test_generate_data_server.py -q`: 109 passed.
- `andrea generate-data execute` with bulk observational SynTReN generated `/tmp/andrea_syntren_phase3/benchmarks/syntren_phase3_observational_20260623T151258Z`.
- `andrea generate-data execute` with bulk perturbational SynTReN generated `/tmp/andrea_syntren_phase3/benchmarks/syntren_phase3_perturbational_20260623T151302Z`.
- `andrea infer-network preflight --dataset-manifest <generated dataset-manifest.json> --tools-params <genie3 tools params>`: passed for both generated SynTReN dataset manifests with `genie3__01` selected and no run issues.
