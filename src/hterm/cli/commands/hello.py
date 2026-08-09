"""Hello command - a simple greeting example."""

import typer

from hterm.cli.ui.console import ok

app = typer.Typer(help="Greeting commands")


@app.command()
def world() -> None:
    """Say hello to the world."""
    ok("Hello, World!")


@app.command()
def name(name: str = typer.Argument(..., help="The name to greet")) -> None:
    """Say hello to someone by name."""
    ok(f"Hello, {name}!")
