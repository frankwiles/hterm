"""HTTP GET command example."""

import json
from typing import Any

import typer

from hterm.cli.config import Config
from hterm.cli.ui.console import info, ok
from hterm.domain.errors import AppError
from hterm.infrastructure.http_client import HttpClient

app = typer.Typer(help="HTTP GET requests")


@app.command()
def json(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="URL to fetch JSON from"),
) -> None:
    """Fetch and display JSON from a URL.

    Example:
        hterm api get json https://api.github.com
    """
    config: Config = ctx.obj
    info(f"Fetching: {url}")

    with HttpClient(config) as client:
        try:
            data: Any = client.get_json(url)
            ok("Successfully fetched data!")
            print(json.dumps(data, indent=2))
        except AppError:
            raise
