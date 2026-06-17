# scMTNI Integration Decisions

Status: threading-contract migration updated to use upstream target-gene sharding.

## Runtime Resources

- ToolSpec value: `runtime_resources.threading.supported=true`,
  `default_threads=1`, `max_threads=8`.
- Evidence:
  - `wrappers/inference_tools/tools/scmtni/repo/README.md` states that scMTNI
    learns regulators per target gene and can be parallelized by replacing the
    `-n` target-gene file with files containing separate genes or gene sets.
  - The same README examples run the public `Code/scMTNI` executable repeatedly
    with different `-n` files.
  - `wrappers/inference_tools/tools/scmtni/repo/Code/Makefile` does not compile
    with OpenMP, and source search under `repo/Code/` found no in-process
    thread, worker, core or jobs control.
- Wrapper behavior:
  - `--threads=1` preserves the prior single public `scMTNI` invocation.
  - `--threads>1` splits the generated target orthogroup list into up to
    `threads` non-overlapping shards and launches one public `scMTNI`
    executable process per shard.
  - Each shard receives its own `-n` file, runtime config, working directory,
    raw output directory and log.
  - Common BLAS/OpenMP environment variables are pinned to 1 inside each
    process so ANDREA's assigned threads map to process-level target-gene
    parallelism rather than nested math-library threads.
- Output/provenance behavior:
  - Per-shard upstream outputs are preserved under `raw/shards/shard_*/`.
  - The wrapper concatenates per-cluster shard edge files into `raw/merged/`
    and writes standardized `network.csv` from that merged raw view.
  - `scmtni.log` is a combined log; individual shard logs remain as
    `scmtni.shard_*.log`.
- Cost behavior:
  - `cost.json` was regenerated after this change for `threads=1,2,4,8`,
    `ram_gb=8,16,32`, sizes `50x20`, `100x40` and `200x80`.
  - Both benchmark profiles passed: the lineage-aware prior profile with two
    groups and the INDEP profile corrected to one group.

## Parameter Boundary

- `threads`, workers, shard count, OpenMP controls and BLAS controls are not
  ToolSpec method parameters.
- `split_genes` remains a method parameter because it maps to upstream `-c yes`
  preprocessing behavior and existed in the scientific wrapper contract before
  runtime resource sharding.

## Remaining Limitations

- This is external process-level parallelism around the public executable, not
  in-process scMTNI threading.
- If the number of target genes is smaller than assigned threads, the wrapper
  runs only one shard per target gene.
- Current progress is shard-level/coarse; native optimization iteration logs are
  preserved but not merged into a stable cross-shard iteration counter.
