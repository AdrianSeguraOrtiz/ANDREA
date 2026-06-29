#!/usr/bin/env bash
set -euo pipefail

CASE_ROOT="${CASE_ROOT:-case_studies/andrea_group_grn_case_study}"

andrea generate-data preflight \
  --scenario "$CASE_ROOT/inputs/scenario.json" \
  --output-json "$CASE_ROOT/reports/generate_data_preflight.json"

andrea generate-data plan \
  --scenario "$CASE_ROOT/inputs/scenario.json" \
  --simulator-runs "$CASE_ROOT/inputs/simulator_runs.json" \
  --out "$CASE_ROOT/plans/simulation-plan.json" \
  --max-parallel-tasks 1 \
  --max-cores 8 \
  --max-ram-gb 32.0

andrea generate-data run \
  --plan "$CASE_ROOT/plans/simulation-plan.json" \
  --output-dir "$CASE_ROOT/outputs/01_generate_data" \
  --max-parallel-tasks 1

BENCHMARK_DIR="$(find "$CASE_ROOT/outputs/01_generate_data" -mindepth 1 -maxdepth 1 -type d | sort | tail -n1)"
DATASET_MANIFEST="$(find "$BENCHMARK_DIR/datasets" -name dataset-manifest.json | sort | head -n1)"
GROUND_TRUTH_MANIFEST="$(find "$BENCHMARK_DIR/datasets" -name ground-truth-manifest.json | sort | head -n1)"

andrea infer-network preflight \
  --dataset-manifest "$DATASET_MANIFEST" \
  --tools-params "$CASE_ROOT/inputs/tools_params.json" \
  --output-json "$CASE_ROOT/reports/infer_network_preflight.json"

andrea infer-network plan \
  --dataset-manifest "$DATASET_MANIFEST" \
  --tools-params "$CASE_ROOT/inputs/tools_params.json" \
  --output-dir "$CASE_ROOT/outputs/02_infer_network" \
  --max-cores 8 \
  --max-ram-gb 32.0 \
  --planner auto \
  --planner-time-limit-seconds 100.0

INFER_RUN_DIR="$(find "$CASE_ROOT/outputs/02_infer_network" -mindepth 1 -maxdepth 1 -type d | sort | tail -n1)"

andrea infer-network run --run-dir "$INFER_RUN_DIR"

andrea evaluate-inference \
  --run-report "$INFER_RUN_DIR/run_report.json" \
  --ground-truth-manifest "$GROUND_TRUTH_MANIFEST" \
  --output-dir "$CASE_ROOT/outputs/03_evaluate_inference" \
  --view

EVALUATION_REPORT="$(find "$CASE_ROOT/outputs/03_evaluate_inference" -name evaluation_report.json | sort | tail -n1)"
cat > "$CASE_ROOT/inputs/comparison_request.json" <<JSON
{
  "schema_version": "1.0",
  "id": "andrea_group_grn_case_study",
  "sources": [
    {
      "source_id": "case_study_inference",
      "label": "Case-study inferred networks",
      "run_report": "$INFER_RUN_DIR/run_report.json",
      "evaluation_report": "$EVALUATION_REPORT"
    }
  ]
}
JSON

andrea compare-networks \
  --request "$CASE_ROOT/inputs/comparison_request.json" \
  --output-dir "$CASE_ROOT/outputs/04_compare_networks"
