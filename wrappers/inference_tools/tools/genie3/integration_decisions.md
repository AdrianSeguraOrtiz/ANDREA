# genie3 Integration Decisions

## Runtime Resources

- Threading support: `supported=true`.
- Default threads: `1`.
- Maximum planned threads: `8`.
- ANDREA mapping: wrapper argument `--threads` is mapped to
  `distributed.LocalCluster(n_workers=threads, threads_per_worker=1,
  processes=True)`.
- Inner sklearn mapping: `RandomForestRegressor` / `ExtraTreesRegressor`
  `n_jobs` is not exposed as a ToolSpec parameter. The wrapper sets
  `regressor_kwargs["n_jobs"] = 1` internally so each Dask worker consumes one
  assigned CPU and nested sklearn parallelism cannot oversubscribe the planner
  allocation.
- Evidence:
  - `wrappers/inference_tools/scripts/templates/python/_arboreto_common.py`
    creates the Dask `LocalCluster` from ANDREA `--threads`.
  - `wrappers/inference_tools/tools/genie3/run_tool.py` passes `--threads` to
    `infer_arboreto_local()` and rejects public `regressor_kwargs.n_jobs`.
  - `wrappers/inference_tools/tools/genie3/repo/arboreto/algo.py` documents
    Dask client execution for arboreto.
  - `wrappers/inference_tools/tools/genie3/repo/arboreto/core.py` exposes
    sklearn RF/ET `n_jobs` defaults, confirming it is a runtime control rather
    than a GENIE3 method hyperparameter.
  - `andrea/catalog_inference_tools/tools/genie3/cost.json` benchmarks thread
    values `1`, `2`, `4` and `8`.
- Rationale: GENIE3 has real CPU parallelism through arboreto/Dask, but
  exposing sklearn `n_jobs` independently from ANDREA `--threads` would create
  two competing resource controls. The public ToolSpec now keeps only method
  hyperparameters (`n_estimators`, `max_features`) and lets the planner choose
  assigned threads.
- Cost profile impact: `resolved_params.regressor_kwargs.n_jobs` was removed
  from `andrea/catalog_inference_tools/tools/genie3/cost.json`; the runtime
  points themselves remain valid because their `threads` values represent the
  Dask worker count.
- Uncertainty: upstream does not publish a hard maximum worker count;
  `max_threads=8` is the current ANDREA planning cap because it is the largest
  value covered by the checked-in cost profile.

