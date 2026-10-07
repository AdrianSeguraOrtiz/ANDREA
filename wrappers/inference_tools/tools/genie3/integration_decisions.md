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

## Grouped Execution Boundary

- `global` runs the wrapper on the full expression matrix.
- `group_emulated` requires `groups` with
  `delivery="orchestration_only"`. ANDREA partitions the expression matrix and
  invokes one physical GENIE3 child per group with `execution.mode=global`;
  the wrapper requires `execution.json`, does not receive `groups.tsv`, and
  emits `context=global` for ANDREA to relabel.

## Release audit (2026-09-29)

- Primary sources: [GENIE3 paper](https://doi.org/10.1371/journal.pone.0012776) and the official [Arboreto 0.1.6 package](https://pypi.org/project/arboreto/0.1.6/). The exact PyPI wheel was downloaded and `arboreto/algo.py` and `arboreto/core.py` inspected, including RF/ET defaults, `create_graph`, target selection and feature-importance extraction.
- `method_family=tree`: independent target regressions with Random Forests or Extra Trees. The wrapper uses Arboreto's lower-level graph path to expose both RF and ET, while `algo.genie3()` itself fixes RF. The configured 1000 trees and sqrt feature sampling match the package defaults. `seed=666` is an explicit ANDREA reproducibility choice, whereas upstream permits no seed.
- Checked optional TF restriction, all-gene target universe, global physical execution, unsigned directed importance and no downstream normalization. `--threads` remains the Dask worker count with one sklearn job per worker.
- Fixed the shared Arboreto input reader: preserve literal numeric/NA-like gene IDs, reject duplicate gene or observation IDs instead of silently dropping/mangling them, and reject nonfinite expression before fitting. This shared correction also applies to GRNBoost2; it changes malformed-input handling, not the regression objective.

Runtime verification for this audit is recorded below separately from the historical smoke results above. Source inspection does not establish coverage of all parameter combinations or published biological results.

- Fresh runtime check (2026-09-29): built the current repository Dockerfile and wrapper, using cached dependency layers, as `andrea-audit/genie3:audit-local`; image ID `sha256:655fd7469edcdaa3b10f697fe41923abcfb1dd525881d1fdacdb584f4840fd5a`. The repository smoke runner passed `default` (21 rows), including network schema, progress and declared auxiliary-artifact validation. Runs used `--threads 1`, a 1-CPU/8-GiB container limit and a 120-second per-variant timeout. These fixture runs are functional checks, not cost calibration or biological validation.
