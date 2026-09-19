# SPDX-License-Identifier: Apache-2.0
"""Command-line interface for PlanLabel.

Phase 0 provides only ``--version``; the working verbs arrive with the phases that
implement them:

==================  =======
Command             Phase
==================  =======
``attach``          2
``read``            2
``strip``           2
``sidecar``         2
``validate``        3
``samples build``   4
``inspect``         5
``infer``           7
==================  =======

This is the only module permitted to write to standard output directly. Everything
else in the package logs through :mod:`logging`.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from planlabel import __version__
from planlabel.constants import SCHEMA_VERSION, SPEC_URI

console = Console()

app = typer.Typer(
    name="planlabel",
    help=(
        "Machine-readable semantics for 2D construction drawings. "
        "The PDF page stays the leading document; the label is auxiliary."
    ),
    no_args_is_help=True,
    add_completion=True,
)


def _version_callback(*, value: bool) -> None:
    """Print version information and exit.

    Args:
        value: Whether the ``--version`` flag was given.

    Raises:
        typer.Exit: Always, when ``value`` is true. This is how Typer implements
            eager options.
    """
    if not value:
        return
    console.print(f"planlabel {__version__}")
    console.print(f"schema     {SCHEMA_VERSION}")
    console.print(f"spec       {SPEC_URI}")
    raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the package, schema and specification versions, then exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Entry point for the ``planlabel`` command."""


if __name__ == "__main__":  # pragma: no cover
    app()
