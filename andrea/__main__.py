"""Entry point for the ANDREA CLI."""

from rich import print

from andrea.cli.app import app
from andrea.config import HEADER


def main() -> None:
    print(HEADER)
    app()


if __name__ == "__main__":
    main()
