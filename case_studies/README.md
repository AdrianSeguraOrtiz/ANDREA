# ANDREA case studies

This directory contains lightweight, reproducible case-study material used to
support ANDREA manuscripts and documentation.

Each case study is expected to include:

- input JSON files that define the scenario, selected simulators, selected
  inference tools and comparison request;
- a `commands.sh` script with the equivalent CLI workflow;
- compact reports and summaries that document planning, execution, evaluation
  and comparison outcomes.

Large generated outputs are intentionally not versioned. In particular,
`case_studies/*/outputs/` is ignored because it can contain Docker workspaces,
merged networks, graph exports, SQLite stores and other files that are fully
reproducible from the inputs and commands.

