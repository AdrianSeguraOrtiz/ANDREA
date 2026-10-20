# Changelog

## 0.2.1

### Integration reliability and reproducibility

- Control SynTReN's auxiliary maximum-expression random stream with the requested
  seed, while preserving its upstream distributions and externally supplied XML
  models. Use simulator image `1.1.1`.
- Repair CeSpGRN covariance projections only when the upstream PSD check fails;
  record each numerical repair. Retry numerically singular PPCOR covariance
  inversions with the Moore-Penrose inverse already used by upstream for singular
  matrices, recording the fallback in the output provenance.
- Handle asymmetric sparse ARACNe3 regulons and singleton MINI-EX clustermaps
  without changing pruning thresholds or inference parameters.
- Apply explicit numerical-library thread limits in the affected integration
  images. The complete image identities are recorded in
  [releases/0.2.1-images.json](releases/0.2.1-images.json).

### Calibration and simulation planning

- Expand calibration to 86 inference profiles and 100 simulator profiles across
  all 32 catalog integrations. Vary genes and observations independently where
  supported, preserving fixed dimensions for pretrained models and native input
  requirements. Calibration coverage is distinct from prediction accuracy.
- Resume calibration from successful observations only when their execution
  identity, input hashes and output checks remain compatible. Preserve the
  original measurements, source versions and receipts when reusing points.
- Use `simulator_cost_v2` to match resolved parameters, effective extras, inputs,
  dimensions and resources. Interpolate within measured geometric support and
  identify extrapolations and unmatched configurations explicitly.
- Report typical simulator runtime separately from the conservative planning
  estimate. Neither value guarantees a deadline or predicts peak memory.
- Enforce planned simulator CPU and RAM limits, measure container elapsed time
  independently of progress polling, and clean up owned containers on errors or
  cancellation.
- Record native output-dimension differences, including dyngen gene budgets and
  lineage sampling, instead of treating requested budgets as exact output shapes.

### Interface and documentation

- Keep inference run cards within their available width. Group optional resource
  constraints in an expandable panel and explain automatic CPU/RAM allocation,
  tool limits, affinity and per-task timeouts.
- Update the automatically generated inference and simulator catalog diagrams.
- Isolate standalone JavaScript checks from inherited Node IPC descriptors.

### Migration

- Recreate plans to use the updated catalogs, cost profiles and simulator resource
  validation. Existing result archives and historical calibration receipts retain
  their original identities.
- Results affected by integration changes require revalidation before scientific
  reuse. Fixed seeds and images support repeatability on the validated environment;
  identical results on arbitrary hardware are not guaranteed.
- Python support remains `>=3.11,<3.14`. Package and container versions remain
  independent. The `v0.2.0` source tag is preserved; PyPI publication is a separate
  step using the exact distributions checked for this release.

## 0.2.0

### Catalog and integration contracts

- Audit the 24 inference tools and eight simulators against their integrated
  implementations. Correct TIGRESS's methodological family and scoring, signed
  scMTNI coefficients, and BoolODE reference-network signs. Integration-specific
  decisions and limitations accompany each wrapper.
- Generate inference catalog groupings from the reviewed `method_family` field.
  Simulator figures distinguish native contexts from wrapper-derived truth.
- Preserve execution modes, information strata and output capabilities in
  plans, run reports, evaluation and comparison. Correct analysis identities
  and resource allocation for native, emulated and aggregated contexts.

### Reproducibility

- Add the public integer `seed` parameter to DigNet, PLaNet and scGeneRAI.
  Its default is 0 and its range is 0 through 4294967295; existing parameter
  files that omit it use the same default.
- Seed DigNet/PLaNet ensemble members independently of worker scheduling.
  Seed scGeneRAI before model initialization and NumPy masking; preserve its
  upstream fixed training/test split.
- Use integration images `1.1.1` for these three tools. Other integration
  images remain at `1.1.0`. See [the image lock](releases/0.2.0-images.json)
  for the exact image identities and registry digests.
- Add Docker regression checks for repeated and changed seeds. Repeatability
  is validated for fixed images, inputs and resources on the tested host;
  arbitrary hardware-independent bitwise determinism is not promised.

### Resource planning

- Use `cost_profile_v3` to match parameters, inputs, contexts and resources.
  Separate measured/interpolated forecasts from size/resource extrapolation
  and fallback estimates; a forecast does not certify a timeout bound.
- Retain complete baseline calibration and representative configurations
  across all inference tools. The 32 cost files contain 155 profiles, with
  strict checks of successful repeats, fixture hashes and build provenance.
- Regenerate both baseline and representative profiles for the three new
  seeded images. Preserve original calibration receipts and package versions.

### Migration

- Recreate plans when adopting updated catalog specifications or image tags;
  archived plans and result files remain unchanged.
- Results affected by integration or scoring fixes must be regenerated before
  scientific reuse. A new image is not interchangeable with its predecessor.
- Recreate evaluation/comparison bundles to apply the corrected analysis
  identity and output-capability rules.
- Legacy cost files remain readable, but release checks require complete
  current profiles. Version-only package promotion does not invalidate an
  unchanged measurement.
- Python support remains `>=3.11,<3.14`. Docker integration versions are
  independent of the Python package version.

The promotion from `0.2.0rc3` changes package version metadata and documentation.
Scientific code, catalog specifications, cost measurements and integration
images remain unchanged. Candidate measurement receipts retain their original
version labels. Publishing the source release and publishing to PyPI are
separate operations.
