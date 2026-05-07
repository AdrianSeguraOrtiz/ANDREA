# Command Review Action Items

Working notes from the review of the four ANDREA commands across CLI, core and GUI:

- `infer-network`
- `generate-data`
- `evaluate-inference`
- `compare-networks`

The goal of this document is to track possible errors, simplifications and refactors before committing the current work.

## 1. Move GUI Runtime Dependencies Out Of Dev

Status: resolved.

`evaluate-inference` and `compare-networks` GUI servers import FastAPI static serving code. In environments where FastAPI is installed but `aiofiles` is not, importing the server can fail with `ModuleNotFoundError: aiofiles`.

Implemented change:

- Move `aiofiles` to the main `[project].dependencies`.
- Keep `httpx` in dev if it is only used by tests.

## 2. Revisit The Candidate Universe Used By `evaluate-inference`

Status: resolved.

`evaluate-inference` needs an explicit candidate gene universe so sparse positive-only ground truth does not shrink the negative edge pool and inflate ranking metrics.

Implemented change:

- Generated `ground-truth-manifest.json` now records `outputs.gene_universe`.
- `generate-data` writes `truth/gene_universe.txt` from the packaged expression matrix.
- `evaluate-inference` builds topology, directed and signed candidate edges from that explicit universe.
- `evaluate-inference` raises a clear error if `outputs.gene_universe` is missing or cannot be resolved.
- The evaluation report now records `ground_truth.gene_universe_size`, and metric rows include `n_candidate_genes`.

## 3. Freeze GUI Inputs Before Running `compare-networks`

Status: resolved.

The `compare-networks` GUI previously extracted uploads to a temporary directory, ran the core command, and then rewrote report paths after the fact.

Implemented change:

- The GUI now creates the comparison output directory before invoking the core.
- Uploaded run/evaluation inputs are frozen into that directory first.
- The frozen `comparison-request.json` is the request passed to `compare_networks`.
- The post-run report path rewrite helper was removed because the report now sees frozen paths from the start.

## 4. Make Core `comparison-request.json` Portable Or Stop Treating It As Reproducible

Status: resolved.

The core now treats portability as first-class behavior.

Implemented change:

- The core freezes each comparison source into `input/sources/<source_id>/`.
- Each frozen source includes a rewritten `run_report.json` plus `merged_network_normalized.csv`.
- Optional `evaluation_report.json` is copied when provided.
- The `comparison-request.json` written to the output package contains only paths relative to the comparison package.
- This keeps GUI reproducibility simple because the core and GUI agree on the same portable request contract.

## 5. Revisit Signed Edge Difference Semantics In `compare-networks`

Status: resolved.

For signed comparisons, strict distance keys still include sign. For ordered edge differences, users expect one directed pair row with score and sign shown per selected network.

Implemented change:

- Core signed network/distance keys remain strict: `source`, `target`, and `sign`.
- The reusable comparison view now uses a separate edge-difference key for signed ordered comparisons: `source|target`.
- Signed values are shown per selected network, so sign changes appear within one directed-pair row.
- If a single network contains both signs for the same directed pair, the ordered visualizer keeps the sign with the largest score magnitude for that displayed row.

## 6. Consolidate GUI Upload And Reproducibility Helpers

Status: resolved.

There was duplicated helper code across GUI servers for uploads, ZIP extraction, path safety, output-dir parsing and reproducibility snippet formatting.

Implemented change:

- `andrea/gui/common/server_files.py` now owns upload saving, safe ZIP extraction, optional upload lookup, output-dir form parsing and report-relative path resolution.
- `evaluate-inference` and `compare-networks` use those shared upload/ZIP/path helpers.
- `infer-network` and `generate-data` use the shared upload saver.
- `infer-network` and `generate-data` use shared reproducibility formatting helpers for shell command wrapping, Python path literals and unavailable reproducibility payloads.
- Command-specific reproducibility snippets remain in each server, but the repeated low-level formatting code has been removed.

## 7. Split `compare_networks/comparison.py`

Status: resolved.

`andrea/core/commands/compare_networks/comparison.py` previously contained request parsing, source loading, table building, distance calculation, MDS coordinates, CSV writing and HTML view rendering.

Implemented change:

- `comparison.py` is now orchestration only.
- Request parsing lives in `request.py`.
- Run/evaluation loading and frozen input copying live in `loading.py`.
- Network index, edge score and evaluation metric table builders live in `tables.py`.
- Weighted Jaccard, rank overlap and distance-map coordinates live in `distances.py`.
- HTML view rendering lives in `view.py`.
- Shared low-level helpers live in `utils.py`.

## 8. Remove Generated Python Cache Files Before Commit

Status: resolved.

Generated Python bytecode should not be committed.

Implemented change:

- Removed generated Python caches from project source/test utility trees.
- Verified no tracked `__pycache__`, `.pyc` or `.pyo` files exist.
- Confirmed `.gitignore` already ignores `__pycache__/` and Python bytecode via `*.py[codz]`.
