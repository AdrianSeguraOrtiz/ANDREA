# ANDREA release checklist

This checklist describes the local steps for publishing ANDREA to PyPI. It does
not replace the catalog validation workflows; run it only after the simulator
and inference-tool catalogs are in the intended release state.

## Cost refinement candidate 0.2.0rc2

The completed rc2 receipts report 633 source tests passed (8 skipped), 415
installed-package tests passed, and 42 successful independent physical cost
observations. These are recorded results, not a claim that every tool is
deterministic on every host.

The release target is **0.2.0**. Inspect workflow outcomes and numerical
differences before removing the release-candidate suffix or creating a final
version tag. Keep candidate wheels, image digests and measurement receipts
immutable. Publishing candidate commits does not publish a PyPI release.
Complete the release checks below before publication. A later version-only
promotion must preserve measurement provenance and document source equivalence;
do not relabel old measurements.

The inference planner uses `cost_profile_v3`. It requires matching method
parameters, runtime inputs and group counts before using a calibration.
Random seeds are retained in provenance but do not define separate cost regimes.
Measured points and supported axis interpolation use `cost_profile`;
unsupported size/resource transfers use `cost_profile_extrapolated`, and
unsupported method configurations use a fallback scheduling heuristic. Neither
uncalibrated case certifies timeout feasibility or a runtime upper bound.
A diagonal genes/cells grid cannot identify the two size effects separately.

Supplementary recipes under `wrappers/inference_tools/cost_profiles/representative/`
cover 29 profiles and 32 fixture sizes across the 24 inference tools, including
full DigNet/PLaNet settings and MINI-EX with motif analysis. Each point requires
three successful, individually identified measurements. Calibration evidence
and subsequent validation executions are kept separate.
Baseline profiles are preserved. Representative fixture inputs are byte-pinned
in each recipe and retained in the evidence bundle. To regenerate a tool's
supplementary profiles with those inputs:

```sh
python wrappers/inference_tools/scripts/benchmark_representative_costs.py \
  --tool dignet \
  --fixture-root release-logs/cost-refinement-20261006/fixtures \
  --results-dir ../andrea-cost-evidence/dignet-representative-new
```

Inspect `release-logs/cost-refinement-20261006/refinement-state.json` for the
finite refinement workflow. Completion evidence is `calibration-import.json`,
`strict-cost-validation.log`, the source/installed test receipts, and
`heldout-comparison.json`. The latter compares old and new forecasts frozen
before separate executions; its limits include repeated input datasets and
only three unseen cell-count subsamples. No image rebuild is required for these
planner and catalog measurement changes. Published calibration observations
identify their evidence by report SHA-256 and task ID; machine-local report
locations are retained with the private measurement receipts.

## Validated catalog baseline 0.2.0rc1

The baseline uses Docker integration tags `1.1.0`. As of 2026-10-05,
all 32 recalibrated `cost.json` files pass the strict release check, covering 126
profiles. The local image IDs match those recorded during calibration. Keep
using these measurements while their spec, build-input and profile fingerprints
remain valid. This is a candidate, not a completed PyPI release.

The following blocks reproduce image preparation and calibration when measured
inputs change. For an unchanged, calibrated candidate, continue with the
pre-release checks below. Run commands from the ANDREA repository with the
development/release requirements installed, and retain their logs. Push images
after builds and smoke tests succeed.

```sh
set -euo pipefail
mkdir -p release-logs
make build-tool-images 2>&1 | tee release-logs/build-tools.log
make build-simulator-images 2>&1 | tee release-logs/build-simulators.log
make run-tool-smoketests ARGS="--catalog-images --threads 2 --timeout 600" 2>&1 | tee release-logs/smoke-tools.log
make run-simulator-smoketests ARGS="--skip-build" 2>&1 | tee release-logs/smoke-simulators.log

docker login
make push-tool-images ARGS="--fail-fast"
make push-simulator-images ARGS="--fail-fast"
```

Calibrate these exact local images after publishing, so their repository digests
are available for provenance. Each script is sequential; run the two campaigns
sequentially on an otherwise idle machine. Inspect the resolved matrix first:

```sh
make benchmark-tool-costs ARGS="--catalog-images --plan-only --repeats 3 --threads 1,2,4,8 --ram-gb 8,32 --max-cpu 8 --max-ram-gb 32"
make benchmark-simulator-costs ARGS="--catalog-images --plan-only --repeats 3 --threads 1,2,4,8 --ram-gb 8,32 --max-cpu 8 --max-ram-gb 32"
```

For the current catalog, these commands resolve to 3,108 inference runs and
2,934 simulator runs (6,042 sequential runs in total). Recheck the plan if
profiles change; the runtime depends on the selected method and configuration.

The supplied matrices include small sizes and configuration-specific fixtures.
They are a baseline calibration, not evidence of scalability to arbitrary gene
or cell counts. Tools with explicit fast-training profiles must not be presented
as calibrated at their full-training defaults. Add scientifically appropriate
larger profiles before making larger-scale performance claims.

```sh
set -euo pipefail
mkdir -p release-logs
make benchmark-tool-costs ARGS="--catalog-images --repeats 3 --threads 1,2,4,8 --ram-gb 8,32 --max-cpu 8 --max-ram-gb 32 --timeout 1800 --results-dir ../andrea-cost-evidence/inference" 2>&1 | tee release-logs/cost-inference.log
make benchmark-simulator-costs ARGS="--catalog-images --repeats 3 --threads 1,2,4,8 --ram-gb 8,32 --max-cpu 8 --max-ram-gb 32 --timeout 1800 --results-dir ../andrea-cost-evidence/simulation" 2>&1 | tee release-logs/cost-simulation.log
make validate-release-costs
```

The strict validator requires every currently configured benchmark profile,
checks its resolved parameters and fixture recipe, and rejects missing matrix
points or fewer than three successful repetitions. It also compares the measured
Docker build inputs (including shared templates) with the current sources.
These checks run without Docker; the recorded immutable image ID identifies the
image actually measured. Package-version and documentation-only changes do not
invalidate calibration. Legacy cost files remain readable by the ordinary
validators but cannot satisfy the release check.

A failed campaign returns nonzero. Inspect the retained evidence, fix the cause,
and rerun the affected tool or simulator. For individual profiles use
`--profile ID --merge-existing`; this preserves other completed profiles. Do not
increase a timeout or relabel a failed measurement as success without checking
its log. Export the final figures after any further spec correction:

```sh
make render-doc-assets ARGS="--catalogs-only --output-dir release-logs/catalog-figures"
```

Keep the final image digests, cost evidence and workflow results with the local
release evidence. Promote the package version to `0.2.0` only after the complete
release checks pass; image integration versions are independent of the Python
package version.

The source audit and Docker fixture tests establish the integration contracts,
not biological accuracy. When using outputs from updated integrations:

- Repeat experiments affected by the TIGRESS scoring repair, signed scMTNI
  coefficients or the corrected BoolODE reference-network signs.
- Use the audited `method_family` field for the inference figure. It describes
  the primary integrated algorithm; shared statistical components and workflow
  stages remain documented in each tool's spec and `integration_decisions.md`.
- Do not use GRouNdGAN's embedded, untrained toy checkpoints as evidence of
  realistic biological data generation.
- Distinguish reproducible environments and recorded inputs from deterministic
  outputs. Some upstream implementations do not expose or consistently apply a
  seed; the integration notes document these limits. Timing repetitions use the
  same requested seed and do not replace independent scientific replicates.

## 1. Pre-release checks

1. Confirm that `andrea/config.py` contains the release version.
2. Confirm that generated catalog files, especially `cost.json`, are complete.
3. Confirm that no local benchmark outputs are staged for commit.
4. Run the package and catalog checks:

```sh
make validate-generation-catalog
make validate-inference-catalog
make validate-release-costs
make test-all
```

If a full catalog validation is too expensive for a release candidate, document
which Docker smoketests were skipped and run the schema and cost validators at
minimum.

The integrated validation must also exercise actual workflows beyond unit
tests: generate a small dataset, infer with catalog and external Docker tools,
evaluate against its truth, then compare the resulting networks. Include native
group/column contexts and emulated/aggregated execution. Check analysis IDs,
dataset fingerprints, applicable direction/sign levels and final artifacts.

For GUI validation, open all four applications in a browser and exercise their
forms and execution controls, including a user-selected output directory and an
external tool. API tests alone do not execute JavaScript. Record browser errors,
screenshots and the generated bundles with the release evidence.

## Migration notes for the candidate

- Catalog contributors must supply the reviewed `method_family` metadata used
  by the inference figure and GUI. External tools continue to use their separate
  `custom-tools.json` contract.
- Docker integration versions (`1.1.0`) and the Python distribution version
  (`0.2.0rc1`) are independent. Existing images are identified by their immutable
  IDs in calibration records.
- Historical cost files remain readable, but release validation requires the
  current complete profiles and provenance. Consumer-side planning or GUI fixes
  do not themselves change the measured wrappers or calibration recipes.
- Recreate plans and derived comparison bundles to apply corrections to analysis
  identity and applicable comparison levels. Existing result files are not
  rewritten automatically.

## 2. Build and inspect the package

```sh
make build-package
make check-package
make smoke-wheel
```

`build-package` creates a fresh `dist/` directory. `check-package` runs Twine's
metadata validation. `smoke-wheel` installs the wheel into a temporary virtual
environment and verifies that `andrea --help` starts.

Inspect the wheel when package-data changes:

```sh
python - <<'PY'
from pathlib import Path
import zipfile

wheel = next(Path("dist").glob("*.whl"))
with zipfile.ZipFile(wheel) as zf:
    names = set(zf.namelist())

required = [
    "andrea/gui/infer_network/static/index.html",
    "andrea/gui/generate_data/static/index.html",
    "andrea/gui/evaluate_inference/static/index.html",
    "andrea/gui/compare_networks/static/index.html",
    "andrea/catalog_inference_tools/schemas/toolspec.schema.json",
    "andrea/catalog_inference_tools/schemas/tools-params.schema.json",
    "andrea/catalog_inference_tools/schemas/custom-tools.schema.json",
    "andrea/catalog_simulation_data_tools/schemas/simulatorspec.schema.json",
    "andrea/catalog_simulation_data_tools/input_specs/regulatory_network.json",
]
missing = [name for name in required if name not in names]
if missing:
    raise SystemExit("Missing wheel files:\n" + "\n".join(missing))
print(f"{wheel} contains the required GUI assets, schemas and catalogs.")
PY
```

## 3. TestPyPI

Upload to TestPyPI first. The full target rebuilds the package, runs Twine's
metadata check, installs the wheel locally, uploads to TestPyPI, then installs
the published package from TestPyPI in a clean virtual environment:

```sh
make publish-testpypi-full \
  PACKAGE_VERSION=0.2.0rc1 \
  TWINE_USERNAME=__token__ \
  TWINE_PASSWORD=pypi-...
```

`PACKAGE_VERSION` must match `andrea.config.__version__`. `TWINE_USERNAME` and
`TWINE_PASSWORD` are passed directly to Twine and no `~/.pypirc` entry is
required. The TestPyPI install uses PyPI as an extra index so runtime
dependencies are resolved from the main package index.

If the upload already happened and only the published artifact needs checking,
run:

```sh
make smoke-testpypi PACKAGE_VERSION=0.2.0rc1
```

## 4. PyPI

Publish to PyPI only after the TestPyPI installation works. The full target
rebuilds, checks, installs locally and uploads to PyPI. The post-upload PyPI
smoke test is kept separate because package propagation can lag briefly after
upload.

```sh
make publish-pypi-full \
  PACKAGE_VERSION=0.2.0rc1 \
  TWINE_USERNAME=__token__ \
  TWINE_PASSWORD=pypi-...
make smoke-pypi PACKAGE_VERSION=0.2.0rc1
```

Commit the finalized code, specs and regenerated costs before building the
release artifacts. Create the Git tag from that same commit:

```sh
git tag -a v$(python - <<'PY'
from andrea.config import __version__
print(__version__)
PY
) -m "ANDREA release"
git push origin --tags
```

## Notes

- The current package metadata targets Python `>=3.11,<3.14`. Keep this range
  aligned with the Python versions covered by the release test matrix.
- The Docker images used by wrappers are not bundled in the wheel. The package
  ships catalogs, schemas, GUIs and orchestration code; Docker pulls/builds are
  handled by the normal wrapper workflows.
- Do not publish while long-running cost-generation jobs are still writing
  catalog files.
