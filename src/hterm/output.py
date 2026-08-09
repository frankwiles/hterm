"""CLI output envelopes.

JSON mode writes exactly one document to stdout. Human failures are written to
stderr so command output can be safely redirected.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import typer

from hterm.errors import HtermError


def success(action: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": True, "action": action, **(data or {})}


def failure(error: HtermError) -> dict[str, Any]:
    return {"ok": False, "error": error.to_dict()}


def emit_success(
    action: str,
    data: Mapping[str, Any] | None = None,
    *,
    json_output: bool,
    human_message: str | None = None,
) -> None:
    payload = success(action, data)
    if json_output:
        typer.echo(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    elif human_message:
        typer.echo(human_message)


def emit_error(error: HtermError, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(failure(error), separators=(",", ":"), sort_keys=True))
    else:
        typer.echo(f"Error: {error.message}", err=True)
