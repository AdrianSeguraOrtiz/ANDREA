# ANDREA release checklist

This checklist describes the local steps for publishing ANDREA to PyPI. It does
not replace the catalog validation workflows; run it only after the simulator
and inference-tool catalogs are in the intended release state.

## 1. Pre-release checks

1. Confirm that `andrea/config.py` contains the release version.
2. Confirm that generated catalog files, especially `cost.json`, are complete.
3. Confirm that no local benchmark outputs are staged for commit.
4. Run the package and catalog checks:

```sh
make validate-generation-catalog
make validate-inference-catalog
make test-all
```

If a full catalog validation is too expensive for a release candidate, document
which Docker smoketests were skipped and run the schema and cost validators at
minimum.

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
  PACKAGE_VERSION=0.1.0 \
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
make smoke-testpypi PACKAGE_VERSION=0.1.0
```

## 4. PyPI

Publish to PyPI only after the TestPyPI installation works. The full target
rebuilds, checks, installs locally and uploads to PyPI. The post-upload PyPI
smoke test is kept separate because package propagation can lag briefly after
upload.

```sh
make publish-pypi-full \
  PACKAGE_VERSION=0.1.0 \
  TWINE_USERNAME=__token__ \
  TWINE_PASSWORD=pypi-...
make smoke-pypi PACKAGE_VERSION=0.1.0
```

Create the Git tag from the same commit:

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
