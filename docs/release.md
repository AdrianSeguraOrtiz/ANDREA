# ANDREA release procedure

## 0.2.0 source release

The release contains the audited catalogs, corrected resource planning and
seed-controlled DigNet, PLaNet and scGeneRAI integrations. Read
[changelog.md](changelog.md) for changes and migration notes. Registry image
identities are frozen in [releases/0.2.0-images.json](releases/0.2.0-images.json).
The Python package version and Docker integration versions are independent.

The validated rc3 calibration is complete: all 32 `cost.json` files pass strict
provenance checks and contain 155 profiles. The three new `1.1.1` images have
fresh baseline and representative measurements. Other images remain `1.1.0`.
Do not rebuild an image merely to publish an already validated local image.
Keep the original measurement versions, image IDs and receipt hashes intact.

Source commits/tags, registry images and PyPI publication are separate steps.
Retain the exact checked wheel and source distribution until package-index
publication; avoid rebuilding them between verification and upload.

## Verify a release

Run from the source checkout with development and release dependencies installed:

```sh
make validate-generation-catalog
make validate-inference-catalog
make validate-release-costs
make test-all
```

The strict cost validator checks the configured profile matrix, three successful
repetitions per point, resolved parameters, fixture recipes, and measured Docker
build inputs. Documentation and version-only changes do not invalidate a cost
profile. Changing wrappers, method parameters or scientific inputs requires
revalidation and, where affected, new calibration.

Integration checks must also cover actual generation, inference, evaluation
and comparison, including native group/column, emulated and aggregated modes,
and external tools. API tests do not replace browser checks of the four GUIs.
When an unchanged component's earlier evidence is retained, record its source
identity and the scope of that evidence.

For the three seeded integrations, the reusable regression command is:

```sh
python wrappers/inference_tools/scripts/validate_seed_reproducibility.py \
  --results-dir release-logs/seed-regression-new
```

This exercises repeated and changed seeds in fresh containers. Representative
full-parameter runs and cost calibration are separate checks. Identical results
on arbitrary hardware are not guaranteed.

## Images and calibration

Build and smoke-test affected images only when integration sources change.
Publish an already verified image by its intended tag and confirm that a pull by
registry digest returns the same image ID. Preserve its calibration receipts.

Inspect calibration workloads before running them:

```sh
make benchmark-tool-costs ARGS="--catalog-images --plan-only --repeats 3 --threads 1,2,4,8 --ram-gb 8,32 --max-cpu 8 --max-ram-gb 32"
make benchmark-simulator-costs ARGS="--catalog-images --plan-only --repeats 3 --threads 1,2,4,8 --ram-gb 8,32 --max-cpu 8 --max-ram-gb 32"
```

Baseline recipes include small fixtures and fast-training configurations. They
are not evidence for arbitrary sizes or full-training defaults. Supplementary
recipes under `wrappers/inference_tools/cost_profiles/representative/` cover
29 profiles and 32 fixture sizes across all 24 tools. Supply their byte-pinned
inputs to `benchmark_representative_costs.py` in a new results directory.

Keep calibration observations separate from independent timing validation.
Unmeasured transfers use `cost_profile_extrapolated` or a fallback, and neither
establishes a runtime upper bound or validated memory prediction.

Regenerate catalog figures after a spec correction:

```sh
make render-doc-assets ARGS="--catalogs-only --output-dir release-logs/catalog-figures"
```

## Build from the final commit

Commit the intended source, tests, specifications, calibration files and generic
documentation. Keep local logs and private materials outside version control.
Build from a clean snapshot of that commit:

```sh
python -m build --outdir dist
python -m twine check dist/*
```

Install both the wheel and source distribution into fresh environments for
Python 3.11, 3.12 and 3.13, outside the checkout. Check dependency consistency,
CLI entry points, all four GUI resources, catalogs and package data. Run the
installed core/CLI/GUI tests and compare packaged files with the frozen source.

The final tag must identify the commit used for the checked artifacts. Preserve
the artifact SHA-256 values, image digests and verification receipts locally.
A version-only promotion must document equivalence to its tested candidate;
it must not relabel historical executions as new-version measurements.

## Deferred package-index publication

Only publish when the intended release artifacts and accompanying materials
are finalized. The following commands upload existing checked files; they do
not rebuild. Supply credentials through the normal Twine authentication setup.

```sh
python -m twine check dist/*
python -m twine upload --non-interactive --repository-url https://test.pypi.org/legacy/ dist/*
```

Check installation from TestPyPI in a clean environment before the final upload:

```sh
make smoke-testpypi PACKAGE_VERSION=0.2.0
python -m twine upload --non-interactive --repository-url https://upload.pypi.org/legacy/ dist/*
make smoke-pypi PACKAGE_VERSION=0.2.0
```

Do not regenerate profiles or overwrite checked artifacts during publication.
The wheel contains the catalog, schemas, GUIs and orchestration code; Docker
images are distributed separately through their recorded registry digests.
