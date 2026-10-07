# grnboost2 Integration Decisions

## Runtime Resources

- Threading support: `supported=true`.
- Default threads: `1`.
- Maximum planned threads: `8`.
- ANDREA mapping: wrapper argument `--threads` is mapped to
  `distributed.LocalCluster(n_workers=threads, threads_per_worker=1,
  processes=True)`.
- Upstream parallelism: arboreto builds one Dask task graph over target-gene
  regressions, and the wrapper computes those partitions through the local Dask
  cluster. Each assigned thread corresponds to one Dask worker process.
- Parameter boundary: `regressor_kwargs.n_jobs` is not a public method
  parameter for GRNBoost2. The wrapper rejects it if supplied so runtime
  resources stay controlled only by ANDREA `--threads`.
- Evidence:
  - `wrappers/inference_tools/scripts/templates/python/_arboreto_common.py`
    creates the Dask `LocalCluster` from ANDREA `--threads`.
  - `wrappers/inference_tools/tools/grnboost2/run_tool.py` passes `--threads`
    to `infer_arboreto_local()` and rejects public `regressor_kwargs.n_jobs`.
  - `wrappers/inference_tools/tools/grnboost2/repo/README.rst` and arboreto
    source document distributed/Dask execution for GRNBoost2.
  - `andrea/catalog_inference_tools/tools/grnboost2/cost.json` benchmarks
    thread values `1`, `2`, `4` and `8`.
- Rationale: GRNBoost2 has real CPU parallelism through arboreto/Dask task
  partitioning. Keeping thread count exclusively in `runtime_resources` avoids
  hidden resource controls in method parameters and lets the planner choose
  assigned CPUs.
- Uncertainty: upstream does not publish a fixed hard maximum worker count;
  `max_threads=8` is the current ANDREA planning cap because it is the largest
  value covered by the checked-in cost profile.

## Grouped Execution Boundary

- `global` runs the wrapper on the full expression matrix.
- `group_emulated` requires `groups` with
  `delivery="orchestration_only"`. ANDREA partitions the expression matrix and
  invokes one physical GRNBoost2 child per group with `execution.mode=global`;
  the wrapper requires `execution.json`, does not receive `groups.tsv`, and
  emits `context=global` for ANDREA to relabel.

## Release audit (2026-09-29)

- Primary sources: [GRNBoost2/Arboreto paper](https://doi.org/10.1093/bioinformatics/bty916) and the exact [Arboreto 0.1.6 wheel](https://pypi.org/project/arboreto/0.1.6/), especially `algo.grnboost2`, `core.SGBM_KWARGS`, `fit_model`, `EarlyStopMonitor`, `to_feature_importances` and `create_graph`.
- `method_family=tree`: stochastic gradient-boosted regression trees per target. Checked defaults learning rate 0.01, maximum 5000 estimators, feature fraction 0.1 and subsample 0.9. The upstream early-stop window remains 25; supplying different regressor kwargs can change whether the out-of-bag early-stop heuristic applies.
- The raw edge score is the importance produced by Arboreto, including its upstream estimator-count scaling for GBM; it is not a signed coefficient or a probability. TF restriction is optional, all genes are targets, and physical output is global. Group emulation remains outside the container.
- Dask worker resources stay separate from scientific kwargs. Shared input-reader fixes preserve literal gene IDs and reject duplicate/nonfinite inputs. No algorithm or score normalization was added.

Runtime verification for this audit is recorded below separately from the historical smoke results above. Source inspection does not establish coverage of all parameter combinations or published biological results.

- Fresh runtime check (2026-09-29): built the current repository Dockerfile and wrapper, using cached dependency layers, as `andrea-audit/grnboost2:audit-local`; image ID `sha256:bfd18fe96018379c452ada4b8ccf7ccb3c849860c1e3b8fd525a1a3f256fc8ac`. The repository smoke runner passed `default` (21 rows), including network schema, progress and declared auxiliary-artifact validation. Runs used `--threads 1`, a 1-CPU/8-GiB container limit and a 120-second per-variant timeout. These fixture runs are functional checks, not cost calibration or biological validation.
