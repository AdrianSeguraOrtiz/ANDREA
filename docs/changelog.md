# Changelog

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
