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

