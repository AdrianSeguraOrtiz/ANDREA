# Developer Notes

This page collects repository-maintenance commands that are useful for ANDREA
developers but intentionally kept out of the top-level README.

## Development Setup

```sh
python -m pip install -e ".[dev]"
make install-dev-deps
```

Docker is required for wrapper builds, wrapper smoketests and full workflow
executions that launch simulator or inference containers.

## Catalog Validation

Run schema and catalog checks before publishing catalog changes:

```sh
make validate-generation-catalog
make validate-inference-catalog
```

Run wrapper smoketests when Docker images are available and the check is not
too expensive for the current change:

```sh
make run-simulator-smoketests
make run-tool-smoketests
```

Wrapper-specific helpers are also available:

```sh
make verify-simulator SIMULATOR=<simulator_id>
make verify-tool TOOL=<tool_id>
```

## Documentation Assets

Regenerate README and documentation assets with:

```sh
make render-doc-assets
```

This target renders:

- `docs/assets/andrea_overview.png` from tracked documentation source;
- simulator and inference-tool coverage figures from catalog specs;
- `docs/catalogs.md` from catalog metadata, including generated tables.

The generated coverage figures read tracked catalog and input specs. They do
not read `cost.json`, so they can be regenerated while long-running cost
profiling jobs are still in progress.

GUI screenshots under `docs/assets/gui_*.png` are static documentation assets.
Refresh them manually when the local GUIs change substantially.

## Runtime Profiling

Reports can include additive `runtime_profile` entries. Existing outputs can be
summarized without rerunning full benchmarks:

```sh
python scripts/profile_andrea_runtime.py inferred_networks evaluations comparisons
```

Use this to compare before/after runtime behavior when optimizing commands.

## Package Checks

Local package checks are:

```sh
make build-package
make check-package
make smoke-wheel
```

`smoke-wheel` installs the built wheel into a temporary virtual environment and
checks that the `andrea` command starts. Full release steps are documented in
[release.md](release.md).
