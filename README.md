# ANDREA

Aggregated Network Discovery through Regulatory Ensemble Analysis.

ANDREA is a catalog-driven platform for synthetic benchmark generation,
network inference, inference evaluation and network comparison. It exposes the
same workflows through a CLI and local browser GUIs, while keeping public
artifacts as plain JSON, CSV, SQLite and ZIP files.

## Workflows

- `generate-data`: build benchmark datasets from simulator catalogs.
- `infer-network`: run inference tools against standardized dataset inputs.
- `evaluate-inference`: compare inferred networks with generated ground truth.
- `compare-networks`: compare inferred networks across runs, contexts and
  tools.

Each workflow has a local GUI under `andrea gui ...`. ZIP bundles are mainly
for GUI handoff; CLI and Python users can pass report JSON paths directly.

## Installation

```sh
pip install -e .
```

For development:

```sh
make install-dev-deps
```

## CLI

```sh
andrea --help
andrea generate-data --help
andrea infer-network --help
andrea evaluate-inference --help
andrea compare-networks --help
```

The CLI writes complete public artifacts by default. For example,
`compare-networks` writes `comparison_report.json`, `comparison.sqlite`,
`network_index.csv`, `distances.csv`, `distance_coordinates.csv`,
`edge_scores.csv` and a lightweight `comparison_view.html`.

## GUIs

```sh
andrea gui generate-data
andrea gui infer-network
andrea gui evaluate-inference
andrea gui compare-networks
```

The GUIs use strict analysis bundles for handoff between commands:

- generate-data analysis bundles feed `evaluate-inference`;
- infer-network analysis bundles feed `evaluate-inference` and
  `compare-networks`;
- evaluate-inference analysis bundles can optionally enrich
  `compare-networks` with metric overlays.

Available bundle families are `analysis`, `report`, `graphs` where applicable,
and `full`. Heavy artifacts may be finalized after results are already
explorable; each bundle reports its own readiness.

## Catalogs And Wrappers

Inference tools and simulators are described by JSON specs under
`andrea/catalog_inference_tools` and `andrea/catalog_simulation_data_tools`.
Wrappers live under `wrappers/` and are validated by catalog, smoketest and cost
profile scripts.

Useful validation targets:

```sh
make validate-inference-catalog
make validate-generation-catalog
make run-tool-smoketests
make run-simulator-smoketests
```

## Runtime Profiling

Reports can include additive `runtime_profile` entries. Existing outputs can be
summarized with:

```sh
python scripts/profile_andrea_runtime.py inferred_networks evaluations comparisons
```

This is useful for comparing before/after performance without rerunning full
benchmarks.
