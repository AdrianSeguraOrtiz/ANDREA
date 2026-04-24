# ANDREA

Aggregated Network Discovery through Regulatory Ensemble Analysis.

ANDREA is being built as the platform home for:

- network inference workflows
- synthetic data generation workflows
- catalog-driven tool and simulator integration
- reproducible benchmarking assets and GUIs

The intent is to separate this platform scope from `GENECI`, which will be
reduced back to the published consensus algorithm it was originally built
around.

## Current Status

This repository is currently in migration bootstrap phase.

The installable package and top-level CLI already exist, but the mature
workflows are still being ported in phases from `GENECI`.

Planned public namespaces:

- `andrea infer-network ...`
- `andrea generate-data ...`
- `andrea gui ...`

## Installation

```sh
pip install -e .
```

## CLI

```sh
andrea --help
andrea infer-network --help
andrea generate-data --help
andrea gui --help
```

## Migration Order

1. Bootstrap ANDREA as an installable project.
2. Port the `infer-network-v2` slice first.
3. Port the `generate-v2` slice second.
4. Reduce `GENECI` to the consensus scope.

The neutral planning documents for the split live outside both repositories
under `../decisions/`.
