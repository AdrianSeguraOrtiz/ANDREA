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

## Grouped Execution Boundary

- `global` runs the wrapper on the full expression matrix.
- `group_emulated` requires `groups` with
  `delivery="orchestration_only"`. ANDREA partitions the expression matrix and
  invokes one physical TIGRESS child per group with `execution.mode=global`;
  the wrapper requires `execution.json`, does not receive `groups.tsv`, and
  emits `context=global` for ANDREA to relabel.


## Scientific and runtime contract audit (2026-09-29)

The primary paper (https://doi.org/10.1186/1752-0509-6-145) and pinned `de70cd256840b08a64f0471c0c63dbb01314b35a` `R/tigress.R` and `R/stabilityselection.R` were rechecked. `method_family=regression`: LARS feature selection with stability selection is not a tree ensemble. Found and narrowly repaired two upstream defects at image build, with patch anchors checked: tigress() did not forward scoring to stabilityselection(), and `sd(y[i1]>0)`/`sd(y[i2]>0)` measured Boolean indicators instead of response variance (positive unnormalized inputs could never advance). The patch forwards scoring in sequential/foreach routes and fixes only those variance conditions. doRNG supplies reproducible foreach streams when an explicit seed is requested. Input identifiers and R integer bounds are preserved; at least four observations/two variable TFs are required. Effective LARS-step reductions are now recorded in raw/tigress_config.json and progress warnings. Both area and max Docker runs completed on positive, unnormalized six-gene input, returned different scores and preserved `001`/`NA`. Full scaling and equivalence across worker counts were not claimed.

Scope: this is a fresh source/contract review; older smoke/cost results elsewhere in this log are historical and are not re-certified by this audit. Upstream sources were fetched at the pinned revisions or package versions; local repo/papers caches were absent. No cost campaign or publication was performed.

Fresh bounded verification: the complete current Dockerfile was built with the standard `build_tool_images.py` helper as `andrea-audit/tigress:local` (image ID `sha256:8e7590c5cc3eb7ddd1e973ff2181f40b70d70786689d00eaeb03b7324d4f982c`; no wrapper overlay). The official smoketest runner passed `default` (20 rows), with `--threads 1`, a 180-second per-tool timeout, and Docker limits of 2 CPUs / 8 GiB. These runs resolve ToolSpec defaults plus the committed dev/smoke overrides; they verify execution, network/progress contracts and declared auxiliary artifacts, not accuracy or default-parameter cost calibration.

Additional final-image checks: area/max on strictly positive, unnormalized six-gene input both produced 29 edges with different scores; a three-TF request for five LARS steps reported the effective value two in raw provenance and progress warnings; two seeded runs with two workers produced identical network.csv files (29 edges). The Dockerfile uses stable `rocker/r-ver:4.4.1` and checks every required R namespace after installation: the previous `r-base:4.3.3` mixed old Debian sid libraries with current packages, and R package installation could warn rather than fail when doRNG dependencies were missing. These checks establish successful sequential/parallel operation for the tested inputs, not equality of draws between worker modes.
