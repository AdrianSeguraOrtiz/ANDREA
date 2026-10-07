# aracne3 Integration Decisions

## Runtime Resources

- Threading support: `supported=true`.
- Default threads: `1`.
- Maximum planned threads: `8`.
- ANDREA mapping: wrapper argument `--threads` is forwarded to upstream
  `ARACNe3_app_release --threads`.
- Evidence:
  - `wrappers/inference_tools/tools/aracne3/repo/README.md` documents OpenMP
    multithreading and says `--threads` sets the number of threads to use, with
    upstream default `--threads 1`.
  - `wrappers/inference_tools/tools/aracne3/run_tool.py` builds the ARACNe3
    command with `--threads <threads>`.
  - `wrappers/inference_tools/tools/aracne3/Dockerfile` installs `libgomp1`,
    the OpenMP runtime library used by the compiled C++ binary.
  - `andrea/catalog_inference_tools/tools/aracne3/cost.json` benchmarks thread
    values `1`, `2`, `4` and `8`.
- Rationale: this is a real upstream CPU parallelism control, not a method
  parameter, so it belongs under `runtime_resources.threading` and should not be
  exposed in `params`.
- Uncertainty: upstream does not publish a fixed hard maximum; `max_threads=8`
  is the current ANDREA planning cap because it is the largest value covered by
  the checked-in cost profile.

## Grouped Execution Boundary

- `global` runs the wrapper on the full expression matrix.
- `group_emulated` requires `groups` with
  `delivery="orchestration_only"`. ANDREA partitions the expression matrix and
  invokes one physical ARACNe3 child per group with `execution.mode=global`;
  the wrapper requires `execution.json`, does not receive `groups.tsv`, and
  emits `context=global` for ANDREA to relabel.

## Release audit (2026-09-29)

- Primary sources: [ARACNe](https://doi.org/10.1186/1471-2105-7-S1-S7), [ARACNe-AP](https://doi.org/10.1093/bioinformatics/btw216), and the [pinned ARACNe3 source](https://github.com/califano-lab/ARACNe3/tree/3d8791a23e3bd8fd0d74f3b8d48f912e81d00f14), specifically `README.md`, `src/app/ARACNe3.cpp`, `subnet_operations.cpp` and `io.cpp`.
- `method_family=information`: adaptive-partitioning MI with significance and maximum-entropy weakest-edge pruning. The earlier summary described only the original ARACNe DPI formulation; the selected ARACNe3 implementation explicitly calls `pruneMaxEnt`. The regulator list constrains candidate sources but MI itself is symmetric, so unsigned association/undirected interpretation is retained.
- Checked all exposed CLI mappings, required regulator file, subnet/consensus output and OpenMP `--threads`. The pinned parser accepts **`--noalpha`**, despite the README spelling `--noAlpha`; the existing wrapper spelling is correct. ANDREA explicitly passes `x=1`, including in adaptive mode; upstream would choose occupancy 30 only if `-x` were absent. This difference is now visible in the parameter description.
- Raw consensus `mi.values` remains the score; Spearman and consensus p-values remain in the raw artifact, not substituted for MI. Empty stderr is a valid successful execution and is no longer required to be nonempty. Global/group-emulated routing remains owned by ANDREA.

Runtime verification for this audit is recorded below separately from the historical smoke results above. Source inspection does not establish coverage of all parameter combinations or published biological results.

- Fresh runtime check (2026-09-29): built the current repository Dockerfile and wrapper, using cached dependency layers, as `adriansegura99/inference-tools_aracne3:1.1.0`; image ID `sha256:67d96118e8f52b97a0f5458da7a7bfea36aaa32cca5b466dd64326aad05754f1`. The repository smoke runner passed `default` (4 rows), including network schema, progress and declared auxiliary-artifact validation. Runs used `--threads 1`, a 1-CPU/8-GiB container limit and a 120-second per-variant timeout. These fixture runs are functional checks, not cost calibration or biological validation.
- The first full build attempt exceeded 180 seconds while fetching recursive source dependencies. A temporary current-wrapper overlay was tested for diagnosis, then superseded by the successful full Dockerfile build and repeat smoke against the release tag recorded above.
