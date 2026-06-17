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

