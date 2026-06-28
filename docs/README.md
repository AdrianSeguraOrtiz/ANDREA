# ANDREA Documentation

This directory contains user-facing documentation that complements the CLI help,
GUI screens and catalogs.

## Start Here

- [Installation](installation.md)
- [Workflow contracts](workflows.md)
- [GUI guide](gui.md)
- [CLI guide](cli.md)
- [Core Python guide](core.md)
- [Catalogs and coverage](catalogs.md)
- [External Docker tools](external-docker-tools.md)
- [Developer notes](development.md)
- [Release checklist](release.md)

## Documentation Model

The top-level `README.md` is the project landing page. The files under `docs/`
provide deeper operational detail:

- how the four commands connect;
- which files are expected at each handoff;
- how GUI bundles map to CLI artifacts;
- how to run the same workflows from the CLI or Python;
- how catalog coverage is represented from specs.

`compare-networks` deserves one explicit distinction: the CLI writes complete
portable artifacts, while the local GUI uses `comparison.sqlite` for scalable
interactive exploration. The generated `comparison_view.html` is intentionally
a lightweight static report, not a full replica of the GUI.

Maintenance procedures such as validation, documentation-asset generation,
runtime profiling and package release checks are collected in
[Developer notes](development.md) and [Release checklist](release.md), keeping
the user-facing pages focused on installation and usage.
