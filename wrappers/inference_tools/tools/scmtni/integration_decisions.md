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
  - The catalog retains only the valid lineage-aware native-group measurements.
    The previous INDEP measurements were produced through the now-invalid
    `group_native + indep=true` contract and were removed rather than relabelled.
    `cost_profiles/scmtni.json` defines the correct physical `global` INDEP
    profile for the next explicit cost regeneration; planning uses the normal
    fallback estimate until those measurements exist.

## Group execution contract

- `indep=false` is the native multi-task method: ANDREA delivers `groups.tsv`
  and `lineage_tree.tsv` once, and the wrapper emits one `group:<id>` network
  for every native scMTNI cluster.
- `indep=true` is the independent method. A logical `group_emulated` request is
  partitioned by ANDREA into one physical `global` invocation per group. The
  child sees only its expression slice, does not receive `groups.tsv`, creates
  a single internal cluster, and emits only `context=global`; ANDREA restores
  the corresponding public `group:<id>` context during the merge.
- Direct `global` execution is the same physical INDEP route over the complete
  expression matrix. The wrapper rejects inconsistent physical combinations,
  while ToolSpec compatibility rules reject inconsistent logical combinations
  before planning.
- `q>0` is blocked for `group_emulated`: the standardized prior is keyed by
  public group, while an isolated physical child intentionally does not receive
  that orchestration identity. Independent no-prior runs therefore use `q=0`.

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
