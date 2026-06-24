# scsgl Integration Decisions

## Sources

- Upstream evidence repo: `wrappers/inference_tools/tools/scsgl/repo/scSGL/`
- Paper: `wrappers/inference_tools/tools/scsgl/papers/scSGL.pdf`
- Extracted text: `wrappers/inference_tools/tools/scsgl/papers/scSGL.txt`
- Main implementation evidence:
  - `README.md`
  - `notebooks/demo.ipynb`
  - `pysrc/graphlearning/__init__.py`
  - `pysrc/graphlearning/signed.py`
  - `pysrc/associations/`
- Checked and excluded as non-target inference entrypoints:
  - `rsrc/simulation_scLink.R`
  - `pysrc/evaluation/`

## Method Metadata

- Tool id: `scsgl`
- Display name: `scSGL`
- Publication DOI: `https://doi.org/10.1093/bioinformatics/btac288`
- First author: `Abdullah Karaaslanli`
- Year: `2022`
- Evidence: paper title/metadata and abstract in `scSGL.txt`; upstream README.
- Summary: scSGL learns signed gene-gene graphs from single-cell expression by treating gene-expression signals as smooth over activating edges and non-smooth over inhibitory edges. The selected implementation supports dot product, correlation, proportionality and zero-inflated Kendall association kernels and solves a density-controlled signed graph learning problem with ADMM.

## Selected Upstream Entrypoint

- Mirrored public entrypoint: `pysrc.graphlearning.learn_signed_graph(X, pos_density, neg_density, assoc, gene_names, return_run_time=False, verbose=False)`.
- Evidence: `notebooks/demo.ipynb` calls `learn_signed_graph` on a genes-by-cells expression matrix; `pysrc/graphlearning/__init__.py` exposes that function.
- The function returns a dataframe with `Gene1`, `Gene2` and signed `EdgeWeight`.
- The implementation removes all-zero genes before inference; the wrapper records this in `raw/retained_genes.tsv`.

## Execution Capabilities

- Declared and implemented:
  - `global`: run `learn_signed_graph` once on the full expression matrix.
  - `group_emulated`: require `groups.tsv`, partition expression columns by public group id, run the same entrypoint once per group and emit `group:<id>` contexts.
- Not exposed:
  - `group_native`: upstream has no public grouped API that consumes group labels and emits native group networks.
  - `column_native`: upstream has no per-cell/per-column GRN mode.
  - `group_aggregated`: not applicable because scSGL does not produce per-column networks.

## Inputs

- Reused normalized inputs:
  - `expression_matrix`
  - `groups`, only when `execution.mode == group_emulated`
- No new input spec was needed.
- Public expression gene ids and group ids are preserved exactly in public outputs. No alias map is required because public gene ids are passed to upstream as `gene_names`.

## Parameters

- `pos_density`: required float, `0 < value < 1`; passed to upstream `pos_density`.
- `neg_density`: required float, `0 < value < 1`; passed to upstream `neg_density`.
- `association_kernel`: enum `dotprod`, `correlation`, `proprho`, `zikendall`; default `dotprod`; passed to upstream `assoc`.
- Fixed implementation choices:
  - `return_run_time=False`
  - `verbose=False`
  - no seed parameter, because the selected public API exposes no seed.
- Densities exclude `0` because the current upstream zero-density branch is not safe in the inspected source.
- The upstream notebook states that binary search can fail to find alpha values that approximate some requested densities. The wrapper keeps the public density interface but bounds the upstream cota search with `SCSGL_MAX_UPPER_BOUND_STEPS` (default `12`). If a requested density cannot be bracketed, the wrapper returns the best-effort graph from the bounded upstream search, records requested/best/actual densities, and emits a runtime warning through `progress.json`.

## Runtime Resources

- `runtime_resources.threading.supported=false`
- `default_threads=1`, `max_threads=1`
- Evidence: selected public API has no worker/thread/process argument; source search found no safe public joblib/Dask/pool/sharding control for the selected inference path.
- Wrapper behavior:
  - reject `--threads` values other than `1`
  - pin common OpenMP/BLAS/NUMBA/R thread environment variables to `1`
  - prevent indefinite upstream density-search loops by returning a best-effort graph with warnings when scSGL cannot bracket a requested density

## Installation

- No public package install route was found for `scsgl`/`scSGL`.
- Dockerfile clones `https://github.com/Single-Cell-Graph-Learning/scSGL` at commit `7fb2a011f6e1061daf4c976225027e76f4e0e4ea`.
- Runtime does not depend on the local `repo/` folder.
- Dockerfile uses `python:3.8-slim-bullseye`, installs Python dependencies with `python -m pip`, installs system R and R package `pcaPP`, and sets `/opt/scSGL` on `PYTHONPATH`.
- `R_LIBS_SITE=/usr/local/lib/R/site-library` is set so rpy2 can see the installed `pcaPP` dependency used by `association_kernel=zikendall`.

## Output Mapping

- Upstream `EdgeWeight` is signed and symmetric.
- Wrapper writes one unordered row per non-zero finite gene pair and excludes self-loops.
- `network.csv` mapping:
  - `source`, `target`: original public expression gene ids
  - `score`: `abs(EdgeWeight)`, strictly positive
  - `sign`: `+` for positive `EdgeWeight`, `-` for negative `EdgeWeight`
  - `evidence`: `association`
  - `context`: `global` or `group:<public_group_id>`
- No ANDREA score normalization is applied in the wrapper.

## Auxiliary Artifacts

- `scsgl.log`: wrapper/upstream lifecycle log.
- `raw/scsgl_edges.tsv`: raw upstream signed edge table before de-duplication.
- `raw/scsgl_config.json`: resolved params, execution mode, dimensions, runtime mapping and pinned upstream commit.
- `raw/density_search.tsv`: requested, best upper-bound and actual exported densities for each context/sign.
- `raw/retained_genes.tsv`: retained and dropped public gene ids after the upstream all-zero gene filter.

## Validation Outcome

- ToolSpec validation passed:
  - `make validate-toolspecs ARGS="--tool scsgl"`
- Input spec validation passed:
  - `make validate-input-specs`
- Smoketest config validation passed:
  - `make validate-smoketest-configs ARGS="--tool scsgl"`
- Image build passed:
  - `python wrappers/inference_tools/scripts/build_tool_images.py --tool scsgl --image-tag scsgl=scsgl-smoketest:local`
- Final smoketest passed on 2026-06-24:
  - `python wrappers/inference_tools/scripts/run_smoketests.py --tool scsgl --image-tag scsgl=scsgl-smoketest:local --skip-image-build --timeout 300`
  - `global`: validated `network.csv` with 15 rows and 5 auxiliary artifacts.
  - `group_emulated_contract`: validated `network.csv` with 30 rows and 5 auxiliary artifacts.

## Known Limitations

- The selected scSGL implementation is undirected/symmetric even though the biological GRN terminology can suggest direction.
- The public API has no reproducibility seed.
- Degenerate matrices such as all-zero retained genes or no gene variation are runtime validation failures because they depend on expression values, not only metadata.
