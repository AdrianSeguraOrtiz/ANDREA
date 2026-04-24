"""Top-level CLI for ANDREA."""

from __future__ import annotations

from typing import Optional

import typer
from rich import print

from andrea.config import __version__


def _version_callback(value: bool) -> None:
    if value:
        print(f"ANDREA {__version__}")
        raise typer.Exit()


app = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="Platform workflows for network inference, simulation, and benchmarking.",
)

infer_network_app = typer.Typer(
    no_args_is_help=True,
    help="Inference workflows. This namespace will receive the first migrated slice from GENECI.",
)
generate_data_app = typer.Typer(
    no_args_is_help=True,
    help="Simulation and benchmark generation workflows. This namespace will receive generate-v2 after inference.",
)
gui_app = typer.Typer(
    no_args_is_help=True,
    help="Graphical interfaces for ANDREA workflows.",
)

app.add_typer(infer_network_app, name="infer-network", rich_help_panel="Workflows")
app.add_typer(generate_data_app, name="generate-data", rich_help_panel="Workflows")
app.add_typer(gui_app, name="gui", rich_help_panel="Interfaces")


@app.callback()
def root(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        help="Show ANDREA version and exit.",
        callback=_version_callback,
        is_eager=True,
    )
) -> None:
    """ANDREA command line interface."""


@infer_network_app.callback()
def infer_network_root() -> None:
    """Inference namespace bootstrap."""


@generate_data_app.callback()
def generate_data_root() -> None:
    """Generate-data namespace bootstrap."""


@gui_app.callback()
def gui_root() -> None:
    """GUI namespace bootstrap."""
