# Changelog

## 0.2.0 — unpublished candidate

This candidate consolidates the changes since 0.1.0, including work previously
prepared with source version 0.2.1. Existing Git tags `v0.2.0` and `v0.2.1` remain
historical preparation snapshots; neither identifies this consolidated candidate.
Use the exact clean source commit recorded when building the current artifacts.
Package-index publication remains a separate step.

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

### Integration reliability and reproducibility

- Add the public integer `seed` parameter to DigNet, PLaNet and scGeneRAI.
  Its default is 0 and its range is 0 through 4294967295; existing parameter
  files that omit it use the same default.
- Seed DigNet/PLaNet ensemble members independently of worker scheduling.
  Seed scGeneRAI before model initialization and NumPy masking; preserve its
  upstream fixed training/test split.
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
  images. The complete current image identities are recorded in
  [releases/0.2.0-images.json](releases/0.2.0-images.json).
- Add Docker regression checks for repeated and changed seeds. Repeatability
  is validated for fixed images, inputs and resources on the tested host;
  arbitrary hardware-independent bitwise determinism is not promised.

### Evaluation continuity

- Match inference results to benchmark datasets by their recorded content
  fingerprint without requiring equal dataset identifiers. Preserve the original
  identifiers in reports as provenance, so manual GUI uploads can use their own
  labels. Fingerprints still cover the expression file and supplied auxiliary
  files; mismatched content is rejected.

### Calibration and resource planning

- Use `cost_profile_v3` to match inference parameters, inputs, contexts and
  resources. Separate measured/interpolated forecasts from size/resource
  extrapolation and fallback estimates; a forecast does not certify a timeout
  bound.
- Expand calibration to 86 inference profiles and 100 simulator profiles across
  all 32 catalog integrations and cost files. Vary genes and observations
  independently where supported, preserving fixed dimensions for pretrained
  models and native input requirements. Calibration coverage is distinct from
  prediction accuracy.
- Resume calibration from successful observations only when their execution
  identity, input hashes and output checks remain compatible. Preserve the
  original measurements, source versions and receipts when reusing points.
- Regenerate affected profiles after integration-image or seed-control changes.
  Retain strict checks of successful repeats, fixture hashes and build provenance.
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

- Recreate plans to use the updated catalog specifications, image tags, cost
  profiles and simulator resource validation. Existing result archives and
  historical calibration receipts retain their original identities.
- Results affected by integration or scoring changes require revalidation before
  scientific reuse. A new image is not interchangeable with its predecessor.
- Recreate evaluation/comparison bundles to apply the corrected analysis
  identity and output-capability rules. Dataset identifiers may differ when
  the recorded input-content fingerprints match.
- Legacy cost files remain readable, but release checks require complete current
  profiles. Consolidating the package version does not invalidate an unchanged
  measurement or relabel its historical execution version.
- Python support remains `>=3.11,<3.14`. Package and container versions remain
  independent. The existing images and calibration receipts are reused unchanged
  by this version consolidation; the historical
  [0.2.1 image lock](releases/0.2.1-images.json) remains available for provenance.
