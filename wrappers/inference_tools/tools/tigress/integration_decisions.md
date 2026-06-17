# tigress Integration Decisions

Status: threading-contract migration complete.

## Runtime Resources

- ToolSpec value: `runtime_resources.threading.supported=true`,
  `default_threads=1`, `max_threads=8`.
- Evidence:
  - The pinned upstream source installed by the Dockerfile is
    `https://github.com/jpvert/tigress` at
    `de70cd256840b08a64f0471c0c63dbb01314b35a`.
  - Upstream `R/tigress.R` exposes `tigress(..., usemulticore=FALSE)`.
  - When `usemulticore=TRUE`, upstream first uses a registered `foreach`
    backend when available; otherwise it falls back to
    `parallel::mclapply(..., mc.cores=detectCores()-1)`.
- Wrapper mapping:
  - `usemulticore` was removed from public ToolSpec params because it is a
    resource control, not a method parameter.
  - For `--threads=1`, the wrapper calls `tigress::tigress(usemulticore=FALSE)`.
  - For `--threads>1`, the wrapper registers `doParallel` with exactly the
    assigned thread count and calls `tigress::tigress(usemulticore=TRUE)`, which
    drives the upstream `foreach` target-gene parallel path.
  - BLAS/OpenMP-style environment variables are pinned to 1 to avoid nested
    oversubscription inside each worker.
- Dockerfile change: installs `doParallel` alongside `lars` and `remotes` so the
  wrapper can provide a deterministic worker count instead of allowing the
  upstream `detectCores()-1` fallback.
- Cost behavior: existing `cost.json` retains runtime points for threads 1, 2,
  4 and 8, matching `max_threads=8`; references to the removed `usemulticore`
  parameter were deleted from `resolved_params`, `cost_relevant_params` and
  `cost_relevant_values`.

## Parameter Surface

- Method parameters retained: `alpha`, `nstepsLARS`, `nsplit`, `normalizeexp`,
  `scoring`, `allsteps`, `limit` and `seed`.
- Resource parameters intentionally not exposed: `usemulticore`, cores, workers,
  backend choice and BLAS thread counts.
