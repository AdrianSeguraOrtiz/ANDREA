from __future__ import annotations

from andrea.gui.common.reproducibility import shell_join_pretty


def test_shell_join_pretty_groups_options_after_subcommand() -> None:
    command = shell_join_pretty(
        [
            "andrea",
            "generate-data",
            "execute",
            "--scenario",
            "/tmp/scenario-request.json",
            "--max-cores",
            "8",
        ]
    )

    assert command == (
        "andrea generate-data execute \\\n"
        "  --scenario /tmp/scenario-request.json \\\n"
        "  --max-cores 8"
    )


def test_shell_join_pretty_groups_options_without_subcommand() -> None:
    command = shell_join_pretty(
        [
            "andrea",
            "evaluate-inference",
            "--run-report",
            "/tmp/run_report.json",
            "--view",
        ]
    )

    assert command == (
        "andrea evaluate-inference \\\n"
        "  --run-report /tmp/run_report.json \\\n"
        "  --view"
    )
