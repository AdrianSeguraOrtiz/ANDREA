# ANDREA Documentation

This directory hosts project-level notes that complement the CLI help,
catalogs and wrapper playbooks.

Current documentation priorities:

- workflow contracts for `generate-data`, `infer-network`,
  `evaluate-inference` and `compare-networks`;
- GUI handoff conventions and strict analysis bundle contents;
- bundle families: `analysis`, `report`, `graphs` and `full`;
- catalog integration playbooks for inference tools and simulators;
- performance profiling and runtime profile interpretation.

`compare-networks` deserves one explicit distinction: the CLI writes complete
portable artifacts, while the local GUI uses `comparison.sqlite` for scalable
interactive exploration. The generated `comparison_view.html` is intentionally
a lightweight static report, not a full replica of the GUI.
