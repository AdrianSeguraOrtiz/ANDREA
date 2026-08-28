# Workflow Contracts

ANDREA is organized around four commands that can be used independently or as a
strict end-to-end workflow:

```text
generate-data -> infer-network -> evaluate-inference -> compare-networks
```

Each command writes plain files that can be inspected directly and passed to
later commands without ad hoc conversion.

For runnable examples, see the [CLI guide](cli.md). For local browser workflows,
see the [GUI guide](gui.md). For programmatic use, see the
[Core Python guide](core.md).

## `generate-data`

`generate-data` builds benchmark datasets from simulator catalogs.

Typical outputs:

- `expression.tsv`: standardized expression matrix with genes in rows;
- `truth/networks.csv`: standardized ground-truth regulatory edges;
- dataset and ground-truth manifests;
- standardized extras when available or derivable;
- raw/native simulator outputs for traceability;
- analysis/report/full bundles for handoff and storage.

Every generated dataset includes `tf_list`; the ground-truth manifest records
it as the required candidate regulator universe and the analysis bundle
includes the referenced file.

The generated analysis bundle can feed `evaluate-inference`.

## `infer-network`

`infer-network` runs catalog tools or temporary external Docker tools against a
standardized dataset.

Typical outputs:

- one workspace per selected run;
- normalized `network.csv` outputs;
- merged raw and normalized network CSVs;
- optional GraphML, GEXF and Cytoscape exports;
- `run_report.json`, `plan.json`, logs and runtime state;
- analysis/report/graphs/full bundles.

The run report freezes direction and sign capabilities per logical run,
including temporary external Docker tools, so downstream evaluation does not
depend on the currently installed catalog. External definitions must declare
both capabilities explicitly; missing declarations are rejected.

The analysis bundle can feed `evaluate-inference` and `compare-networks`.

## `evaluate-inference`

`evaluate-inference` compares inferred networks against reference truth.

Candidate sources and targets declared by the truth manifest determine the
evaluated edge universe. Known-gene predictions outside that universe are
excluded and reported per evaluation level. Truth can be broader than the
inference candidate space; unreachable truth edges are likewise excluded and
reported per level rather than counted as false negatives. The declaration is
mandatory; evaluation does not invent an all-gene fallback universe.
Inference and truth must match both the dataset ID and the SHA-256 fingerprint
of the normalized expression and extras, preventing accidental comparisons of
different packages that happen to reuse an ID.

Typical outputs:

- `metrics.csv`;
- `pairings.csv`;
- `evaluation_report.json`;
- static/interactive visual summaries;
- analysis/report/full bundles.

The analysis bundle can enrich `compare-networks` with metric overlays.

## `compare-networks`

`compare-networks` compares inferred networks across sources, tools, parameters
and contexts.

Typical outputs:

- `network_index.csv`;
- `distances.csv`;
- `distance_coordinates.csv`;
- `comparison.sqlite`;
- `edge_scores.csv`;
- `comparison_report.json`;
- a lightweight static `comparison_view.html`.

The GUI uses `comparison.sqlite` for scalable interactive exploration, including
distance maps, metric overlays and edge-level variability views.

## Handoff Bundles

Available bundle families are:

- `analysis`: minimal handoff files for downstream commands;
- `report`: compact status and human-inspection files;
- `graphs`: graph exports when applicable;
- `full`: complete archive for storage/debugging.

Heavy artifacts can finish after results are already explorable. The GUIs report
readiness per bundle, so one bundle can be downloadable while another is still
being finalized.
