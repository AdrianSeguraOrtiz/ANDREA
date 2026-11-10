# Complete bulk time-series example

This small example can be uploaded to **ANDREA GUI infer-network** or executed
with the ordinary CLI. It exercises dynGENIE3, SWING, BiXGBoost and PFBNet
in its expression-only scope using the existing normalized input catalog. It is
an entirely
**synthetic functional example**, not measured biological data, a validated
regulatory network, an accuracy benchmark or a cost-calibration dataset.

## Experimental layout

The eight illustrative genes are `TF_A`, `TF_B`, `TF_C` and `RESP_D` through
`RESP_H`. Two idealized stimulus conditions, `low_stimulus` and `high_stimulus`,
each have **three independent biological longitudinal replicates**. Every course
has **21 observations from 0 to 10 hours, spaced by 0.5 hours**. There is one
technical measurement per observation; technical replicates are not averaged.

The expression matrix also contains three independent stationary controls per
condition. These are independent experiments, not additional visits in the
longitudinal courses. In total, the same matrix contains **8 genes × 132 columns**:

| Observation role | Columns |
| --- | ---: |
| Six independent courses × 21 timepoints | 126 |
| Two conditions × three independent stationary controls | 6 |

The stored values are finite, nonnegative, continuous **linear abundances in
arbitrary units**, not raw sequencing counts or logarithmic expression. The
temporal illustration uses a shared noisy linear response system with delayed
pulses and different stimulus amplitudes; biological replicates have different
initial states and noise. Stationary controls vary around gene-specific
baselines. Values were generated with
NumPy `default_rng(20261108)` and stored to six decimals. These idealizations do
not establish real biology or a reference network.

## Files and their meanings

| File | Existing catalog input or purpose |
| --- | --- |
| `expression.tsv` | Required `expression_matrix`: genes in rows, all observations in columns. |
| `column_descriptors.tsv` | Required `column_descriptors`: one exact row per expression column, including its role, condition, biological unit, observation, technical measurement and temporal geometry. Stationary rows leave series/time fields empty. |
| `tf_list.txt` | Optional `tf_list`: the three illustrative candidate regulators `TF_A`, `TF_B` and `TF_C`. |
| `dataset-manifest.json` | Ordinary dataset manifest declaring `timepoints`, `bulk`, synthetic taxonomy and both extras. |
| `dataset-manifest-no-tf.json` | Same matrix and metadata, omitting only the optional TF restriction. |
| `tools-params.json` | Four small global runs covering the four tools. |
| `tools-params-backends.json` | Three additional small runs: dynGENIE3 ExtraTrees, SWING Dionesus/PLSR and SWING Lasso. Use the manifest **without** TF restriction. |

There is no `groups.tsv`: these tools infer a **global** network by pooling
within-course information. Condition and biological replicate identities
already belong in `column_descriptors.tsv`; they do not request separate
condition networks. No timepoint, replicate or stationary-expression file is
needed beside the unified inputs above.

The same files intentionally exercise each tool's native input interpretation:

| Tool | Temporal and stationary observations | Optional regulator list |
| --- | --- | --- |
| dynGENIE3 | Uses all six temporal courses and all six stationary controls through native `SS_data`. | Restricts candidate sources to the three listed genes. |
| SWING | Uses only the six aligned regular temporal courses; records all six stationary columns as unused. | Restricts candidates for RandomForest and Dionesus. Restricted TF subsets are unsupported by native Lasso; use the no-TF manifest for that run. |
| BiXGBoost | Uses only the six temporal courses; records stationary columns as unused. | Has no native regulator-list parameter; fits all expression genes. |
| PFBNet | Uses only the six temporal courses; all six stationary columns are unused. | Restricts candidate sources to the three listed genes. |

These different scientific uses of the shared matrix mean that the runs are
functional demonstrations, not matched-input evidence for comparing inference
accuracy. All runs return global, directed, unsigned association scores in
canonical `network.csv`; raw scores from different methods are not on a common
scale. Per-method transformation and fitting details remain in `provenance`.

## Quick run settings

The supplied JSON makes every reduced setting visible. It does not change
catalog defaults:

| Run | Explicit quick settings |
| --- | --- |
| dynGENIE3 RandomForest / ExtraTrees | 100 trees per target instead of the default 1,000. |
| SWING RandomForest | Width 7, lags 1–2, 25 trees per target/window instead of 500, no permutations. There is no Lasso bootstrap stage in this backend. |
| BiXGBoost | Native regularization and lag 2; its upstream 1,000 boosting rounds per target/direction remain fixed. |
| PFBNet | Accumulation order 2, decay 0.45, 200 rounds per target instead of the published-example default 1,000. |
| SWING Dionesus/PLSR | Width 7, lags 1–2, one component, no permutations. |
| SWING Lasso | Width 7, lags 1–2, fixed alpha 0.05 and null alpha 2.0, two bootstraps and three alpha levels, no permutations. |

All runs use seed 0, one assigned CPU, 2 GiB RAM and a **1,800-second maximum per
physical task**. They are intended for short local functional checks. Actual
time depends on the machine and image availability; these examples provide no
calibrated duration guarantee. Reduced fits are unsuitable for drawing
biological conclusions.

## CLI

Run the following from the ANDREA repository root with ANDREA installed and
Docker available. The four catalog images must be available at their declared
tags; this example does not build or publish images or start calibration.

Inspect the four primary requests without executing them:

```sh
andrea infer-network preflight \
  --dataset-manifest wrappers/inference_tools/tests/fixtures/bulk_time_series_complete/dataset-manifest.json \
  --tools-params wrappers/inference_tools/tests/fixtures/bulk_time_series_complete/tools-params.json \
  --output-json inferred_networks/bulk-time-series-example-preflight.json
```

Execute the primary example with a sequential, bounded resource budget:

```sh
andrea infer-network execute \
  --dataset-manifest wrappers/inference_tools/tests/fixtures/bulk_time_series_complete/dataset-manifest.json \
  --tools-params wrappers/inference_tools/tests/fixtures/bulk_time_series_complete/tools-params.json \
  --output-dir inferred_networks/bulk-time-series-example \
  --max-cores 1 --max-ram-gb 2 \
  --planner heuristic --output-profile canonical
```

The optional backend examples reuse all expression values and descriptors, but
remove the TF restriction so that native SWING Lasso can use all genes:

```sh
andrea infer-network execute \
  --dataset-manifest wrappers/inference_tools/tests/fixtures/bulk_time_series_complete/dataset-manifest-no-tf.json \
  --tools-params wrappers/inference_tools/tests/fixtures/bulk_time_series_complete/tools-params-backends.json \
  --output-dir inferred_networks/bulk-time-series-backends-example \
  --max-cores 1 --max-ram-gb 2 \
  --planner heuristic --output-profile canonical
```

To test just one method, copy a request JSON outside this fixture and keep only
the desired object in its `runs` list. Changing the request does not require
copying or splitting the expression matrix. Outputs are written under the
chosen `inferred_networks` directory, including the plan, run report, canonical
networks and per-tool provenance.

## GUI

Launch `andrea gui infer-network`. In the dataset stage:

1. Upload `expression.tsv` and select **column kind `timepoints`**, expression
   profile **`bulk`**, taxonomic group **`synthetic`** and no organism taxon ID.
2. Add the existing input fields for `column_descriptors.tsv` and, for the
   primary example, `tf_list.txt`.
3. Analyze the dataset and select global runs for the four methods. Copy the
   quick parameters from `tools-params.json`; leaving GUI defaults unchanged
   selects larger fits.
4. Set one CPU and 2 GiB RAM per run with a 1,800-second task timeout; review the
   ordinary plan before starting.

For SWING Lasso, omit `tf_list.txt` from the uploaded extras and use the values
in `tools-params-backends.json`. Warnings about unused stationary observations,
an omitted optional TF list or an unavailable empirical cost model are expected
where applicable; the supplied valid requests have no input-blocking issues.

See the [shared temporal contract](../../../../../docs/bulk-time-series-contract.md)
for normalized field semantics and the [CLI guide](../../../../../docs/cli.md)
for the complete workflow.
