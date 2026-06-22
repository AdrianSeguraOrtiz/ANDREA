# GeneSPIDER2 Integration Decisions

Phase: 3 complete. GeneSPIDER2 is active in `andrea/catalog_simulation_data_tools/simulators/genespider2/simulatorspec.json`.

## Upstream And Packaging

- Simulator id: `genespider2`
- Public project: `GeneSPIDER2`
- Implementation URL: `https://bitbucket.org/sonnhammergrni/genespider`
- Pinned commit: `0ac785abf89dbf65cb01132da703d0e75196abc2`
- Docker image: `adriansegura99/simulator_genespider2:1.0.0`
- Publications:
  - `https://doi.org/10.1093/nargab/lqae121`
  - `https://doi.org/10.1039/c7mb00058h`
- First author: `Mateusz Garbulowski`

Evidence paths:

- `wrappers/simulation_data_tools/simulators/genespider2/repo/genespider/README.md`
- `wrappers/simulation_data_tools/simulators/genespider2/additional_files/GeneSPIDER.html`
- `wrappers/simulation_data_tools/simulators/genespider2/papers/lqae121.txt`
- `wrappers/simulation_data_tools/simulators/genespider2/repo/genespider/+datastruct/scalefree2.m`
- `wrappers/simulation_data_tools/simulators/genespider2/repo/genespider/+datastruct/noise.m`
- `wrappers/simulation_data_tools/simulators/genespider2/repo/genespider/+datastruct/scdata.m`
- `wrappers/simulation_data_tools/simulators/genespider2/repo/genespider/+datastruct/simts.m`
- `wrappers/simulation_data_tools/simulators/genespider2/repo/genespider/+datastruct/@Network/Network.m`

Packaging decision:

- GeneSPIDER2 is MATLAB-only. Octave is not compatible with the pinned source because the public API uses MATLAB `arguments` blocks, tables/groupcounts and Statistics Toolbox distributions.
- The wrapper compiles `run_genespider2.m` with MATLAB Compiler. The final container installs MATLAB Runtime R2026a Update 3 from MathWorks and runs the compiled binary; no MATLAB license is needed at runtime.
- The container also clones the pinned upstream repository for audit/provenance, but runtime execution does not depend on the local evidence `repo/` directory.
- The wrapper executes public upstream functions from the compiled package: `datastruct.scalefree2`, `datastruct.noise`, `datastruct.scdata`, `datastruct.simts` and `datastruct.Network`.

## Claimed Capabilities

| Capability | `data_axes` | Truth | Required/fixed extras |
| --- | --- | --- | --- |
| Bulk perturbational | `rna_expression`, `bulk`, `perturbations`, `perturbational` | `global` native | `perturbation_design`, `interventions` |
| Bulk time-series | `rna_expression`, `bulk`, `timepoints`, `time_series` | `global` native | `timepoints`, `perturbation_design`, `interventions` |
| Single-cell perturbational | `rna_expression`, `single_cell`, `cells`, `perturbational` | `global` native | `perturbation_design`, `interventions` |
| Single-cell perturbational grouped | `rna_expression`, `single_cell`, `cells`, `perturbational` | `global` native, `group` derivable | `groups`, `perturbation_design`, `interventions` |

Truth semantics:

- `A(target, regulator)` non-zero, non-diagonal values become public edges with `source=regulator`, `target=target`.
- `score=abs(A[target, regulator])`.
- `sign=+` for positive activation and `sign=-` for negative repression.
- `context=global` is native because every selected GeneSPIDER2 mode consumes one fixed upstream GRN matrix.
- `context=group:<id>` is derivable for the grouped single-cell capability: the wrapper derives public groups by deterministic expression clustering and duplicates the fixed global GRN for every observed group. This is fixed-GRN group truth, not group-specific rewiring.
- `column` truth is not claimed. GeneSPIDER2 does not emit perturbation-, timepoint- or cell-specific GRNs.

## Parameters And Bindings

Public parameter surface:

- `network_source`: `scalefree2` or `input_tsv`.
- `num_genes`, `average_degree`.
- `network.scalefree2_alpha`, `network.activation_probability`.
- `perturbation.replicates_per_gene`, `perturbation.strength`.
- `bulk.snr`, `bulk.snr_model`.
- `single_cell.snr`, `single_cell.control_snr`, `single_cell.snr_model`, `single_cell.raw_counts`, `single_cell.right_tail`, `single_cell.negbin_prob`, `single_cell.dispersion`, `single_cell.n_clusts`, `single_cell.logbase`, `single_cell.ds_min`, `single_cell.ds_max`.
- `time_series.time_points`, `time_series.perturbed_gene_index`, `time_series.perturbation_strength`, `time_series.input_noise_std`.
- `grouping.method=kmeans_expression`.

Design mapping:

- Bulk perturbational and single-cell perturbational build `P = -strength * repmat(eye(num_genes), 1, replicates_per_gene)` and pass it to GeneSPIDER2.
- Bulk perturbational computes `X = Net.G * P` and calls `datastruct.noise`.
- Bulk time-series builds one perturbation vector `p` and calls `datastruct.simts(A, p, time_points, input_noise_std, false)`.
- Single-cell perturbational calls `datastruct.scdata(A, P, ...)`.

Not exposed:

- `threads`, workers or cores as simulator params. `runtime_resources.threads` maps to MATLAB `maxNumCompThreads`.
- `datastruct.large_scalefree` and `datastruct.stabilize`. These depend on vendored CVX/stabilization code that cannot be packaged cleanly by MATLAB Compiler in this environment. The capability exists upstream, but ANDREA does not claim it until a portable CVX/runtime packaging route is proven.
- Legacy `datastruct.scalefree`, `randomNet`, `smallworld`, `optimalP*`, `dataRunParameters` and GeneSPIDER inference methods. They are outside the selected simulator contract.
- Arbitrary user-provided perturbation matrices. Upstream can consume arbitrary `P`, but ANDREA needs a stricter simulator input spec for gene order, condition labels and semantics before exposing it.

Conditional inputs:

- `regulatory_network` is required when `network_source=input_tsv`.
- Reused input spec: `andrea/catalog_simulation_data_tools/input_specs/regulatory_network.json`.
- Wrapper mapping: `effect` becomes `A[target, regulator]`; `num_genes` must match the unique gene count.

## Extras

Native:

- `perturbation_design` for perturbational modes and the time-series perturbation vector metadata.

Derivable:

- `interventions`: derived from generated perturbation metadata.
- `replicates`: repeated perturbation columns/cells.
- `timepoints`: one row per time-series expression column, using the same `tau*0.1` timestep formula as `datastruct.simts`.
- `groups`: deterministic expression clustering for grouped single-cell runs.
- `column_phenotypes`, `cluster_identities`: derived from `groups`.
- `enrichment_background`: expression gene universe.
- `prior_grn`: truth-derived oracle prior.
- `prior_grn_by_group`: duplicated fixed-GRN prior per derived group.
- `tf_list`: genes with at least one outgoing public truth edge.

Not claimed:

- `pseudotime`, `lineage_tree`, `spatial_coordinates`, `chromatin_accessibility`, `chromatin_regions`, `cell_cell_interactions`.

## Output Normalization

- `expression.tsv`: rows are public gene IDs; columns are perturbation IDs, timepoint IDs or cell IDs depending on `data_axes.column_kind`.
- `truth/networks.csv`: `source,target,score,sign,evidence,context`.
- `truth/gene_universe.txt`: exact expression row universe.
- `extras/`: only requested/effective standardized extras are written.
- `native/`: selected native GeneSPIDER2/MATLAB outputs copied from raw upstream artifacts and listed in `simulator-output-manifest.json`.
- `provenance/raw/`: original request, resolved params, MATLAB request, compiled MATLAB logs, raw upstream matrices, `.mat` snapshots and session info.
- `simulator-output-manifest.json`: points to all normalized outputs and native outputs.

These outputs are sufficient for `generate-data` to build `dataset-manifest.json` for `infer-network` and `ground-truth-manifest.json` for `evaluate-inference`.

## Runtime Resources

- `runtime_resources.threading.supported=true`.
- `default_threads=1`, `max_threads=64`.
- The MATLAB entrypoint calls `maxNumCompThreads(runtime_resources.threads)` before invoking GeneSPIDER2 functions.
- No simulator-level worker count or MATLAB `parpool` is exposed.

## Validation Matrix

Smoke configs:

- `genespider2_bulk_perturbational_global.json`
- `genespider2_bulk_time_series_global.json`
- `genespider2_single_cell_perturbational_global.json`
- `genespider2_single_cell_perturbational_grouped.json`
- `genespider2_single_cell_custom_network.json`

The matrix covers every declared semantic capability, every declared standardized extra, required truth context families (`global`, `group:`) and the conditional `regulatory_network` input path.

Phase 3 checks performed:

- `make validate-simulatorspecs ARGS="--simulator genespider2"`
- `make validate-simulator-smoketest-configs ARGS="--simulator genespider2"`
- `docker build -f wrappers/simulation_data_tools/simulators/genespider2/Dockerfile -t adriansegura99/simulator_genespider2:1.0.0 .`
- `docker run --rm --entrypoint /bin/sh adriansegura99/simulator_genespider2:1.0.0 -lc 'command -v matlab && exit 1 || true; test -x /opt/genespider2/compiled/run_genespider2; test -d /opt/matlab_runtime/R2026a'`: passed; confirms runtime uses MATLAB Runtime and the compiled binary, not licensed MATLAB.
- `make run-simulator-smoketests ARGS="--simulator genespider2 --skip-build"`
- `make benchmark-simulator-costs ARGS="--simulator genespider2 --skip-build --threads 1,2,4,8 --ram-gb 8 --repeats 1 --timeout 900"`: 16/16 runtime points succeeded and wrote `andrea/catalog_simulation_data_tools/simulators/genespider2/cost.json`.
- `make validate-simulator-costs ARGS="--simulator genespider2"`
- `andrea generate-data execute` with a single-cell perturbational grouped GeneSPIDER2 run produced a benchmark package and dataset manifest.
- `andrea infer-network preflight --dataset-manifest <generated dataset-manifest.json>` passed for the generated dataset.
- `python -m pytest tests/wrappers/simulation_data_tools/test_generate_data_schemas.py tests/wrappers/simulation_data_tools/test_simulatorspecs.py tests/wrappers/simulation_data_tools/test_benchmark_costs.py tests/core/commands/generate_data/test_semantic_model.py tests/core/commands/generate_data/test_bootstrap.py tests/core/commands/generate_data/test_generate_data.py tests/cli/test_generate_data_cli.py tests/gui/test_generate_data_server.py -q`: 115 passed.
- `make validate-generation-catalog`
