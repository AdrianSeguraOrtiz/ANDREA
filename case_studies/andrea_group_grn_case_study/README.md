# ANDREA group-level GRN case study

This case study exercises ANDREA's full workflow on a synthetic single-cell
differentiation dataset:

1. `generate-data` creates a scMultiSim dataset with global and group-level
   truth.
2. `infer-network` runs a mixed set of group-native, group-emulated and
   group-aggregated inference tools.
3. `evaluate-inference` scores inferred networks against the generated truth.
4. `compare-networks` computes network distances and edge-level comparison
   artifacts.

The repository version of this case study is intentionally lightweight. It
contains the exact inputs, CLI commands, planning reports and compact summaries
used to describe the case study, but not the heavy generated outputs.

## Reproduce

From the repository root:

```bash
python scripts/run_paper_case_study.py \
  --out case_studies/andrea_group_grn_case_study \
  --preset paper \
  --force
```

The script writes the same inputs and runs the workflow through ANDREA's Python
core API. The generated `commands.sh` file records the equivalent CLI workflow.

The default case uses:

- 150 genes and 60 cells;
- one scMultiSim replicate with seed `1729`;
- selected inference tools: scMTNI, SimiC, scRegulate, Inferelator 3, kSCReni,
  GENIE3, GRNBoost2 and CLR;
- `infer-network` planner mode `auto` with a 100 second CP-SAT search limit;
- resource limits of 8 cores and 32 GB RAM.

## Versioned files

- `inputs/`: scenario, simulator run configuration, inference tool parameters
  and comparison request.
- `plans/`: the generated `generate-data` simulation plan.
- `reports/`: compact JSON reports copied from the run, including preflight,
  inference plan, inference run report, evaluation report and comparison report.
- `summaries/`: TSV summaries for catalog eligibility, selected runs, timing,
  resource sampling, evaluation rows and comparison counts.
- `case_study_summary.json`: high-level machine-readable summary used to
  cross-check manuscript values.

## Omitted outputs

The `outputs/` directory is ignored by Git. A full run produces hundreds of MB
of generated artifacts, including Docker workspaces, merged networks, graph
exports, SQLite stores and edge-score tables. These files are reproducible from
the versioned inputs and are therefore not stored in the repository.

For the run captured in the versioned summaries, `infer-network` planned 8
logical runs as 17 physical tasks across 5 waves. All selected inference tasks
completed successfully. The observed end-to-end `infer-network run` time was
138.91 seconds, with a peak observed memory footprint of 2.33 GB.

