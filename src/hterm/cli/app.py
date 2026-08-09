"""Main CLI application with command autodiscovery."""

from importlib import import_module
from importlib.metadata import version
from pathlib import Path

import typer

from hterm.cli.config import Config
from hterm.domain.errors import AppError

app = typer.Typer(
    name="hterm",
    help="Raycast extension for launching common herdr workspaces",
    no_args_is_help=True,
)


def _get_version(value: bool) -> None:
    """Callback for --version option."""
    if value:
        try:
            pkg_version = version("hterm")
            print(f"hterm {pkg_version}")
        except Exception:
            print("hterm (unknown version)")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_get_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug/--no-debug",
        envvar="HTERM_DEBUG",
        help="Enable debug mode.",
    ),
    http_timeout: float = typer.Option(
        30.0,
        "--http-timeout",
        envvar="HTERM_HTTP_TIMEOUT",
        help="HTTP request timeout in seconds.",
    ),
    http_user_agent: str = typer.Option(
        "hterm/0.1.0",
        "--http-user-agent",
        envvar="HTERM_HTTP_USER_AGENT",
        help="User-Agent header for HTTP requests.",
    ),
) -> None:
    """Raycast extension for launching common herdr workspaces"""
    ctx.obj = Config(
        debug=debug,
        http_timeout=http_timeout,
        http_user_agent=http_user_agent,
    )


def _discover_commands() -> None:
    """Discover and register commands from the commands directory."""
    commands_dir = Path(__file__).parent / "commands"

    if not commands_dir.exists():
        return

    # Track registered names to detect collisions
    registered: dict[str, str] = {}

    # Process command groups (subdirectories with __init__.py)
    for item in sorted(commands_dir.iterdir()):
        if item.name.startswith("_"):
            continue

        if item.is_dir() and (item / "__init__.py").exists():
            _register_command_group(item, registered)
        elif item.is_file() and item.suffix == ".py":
            _register_command(item, registered)


def _register_command_group(group_dir: Path, registered: dict[str, str]) -> None:
    """Register a command group (subdirectory as a Typer group)."""
    group_name = group_dir.name.replace("_", "-")

    # Check for collisions
    if group_name in registered:
        raise AppError(
            message=f"Command name collision: '{group_name}' is already registered by {registered[group_name]}",
            hint="Rename one of the conflicting files or directories.",
        )

    # Import the group module
    module_path = f"hterm.cli.commands.{group_dir.name}"
    try:
        module = import_module(module_path)
    except ImportError as e:
        raise AppError(
            message=f"Failed to import command group '{group_name}': {e}",
            hint=f"Check that {module_path} is a valid Python module.",
        ) from e

    # Get or create the Typer app for this group
    group_app: typer.Typer = getattr(module, "app", None) or typer.Typer(
        name=group_name,
        help=f"Commands for {group_name}",
    )

    # Register commands inside the group
    for item in sorted(group_dir.iterdir()):
        if item.name.startswith("_"):
            continue

        if item.is_file() and item.suffix == ".py":
            _register_nested_command(group_app, group_dir.name, item)

    # Register the group with the main app
    app.add_typer(group_app, name=group_name)
    registered[group_name] = str(group_dir)


def _register_command(file_path: Path, registered: dict[str, str]) -> None:
    """Register a top-level command from a file."""
    command_name = file_path.stem.replace("_", "-")

    # Check for collisions
    if command_name in registered:
        raise AppError(
            message=f"Command name collision: '{command_name}' is already registered by {registered[command_name]}",
            hint="Rename one of the conflicting files.",
        )

    # Import the command module
    module_path = f"hterm.cli.commands.{file_path.stem}"
    try:
        module = import_module(module_path)
    except ImportError as e:
        raise AppError(
            message=f"Failed to import command '{command_name}': {e}",
            hint=f"Check that {module_path} is a valid Python module.",
        ) from e

    # Get the Typer app from the module
    command_app: typer.Typer | None = getattr(module, "app", None)
    if command_app is None:
        raise AppError(
            message=f"Command module '{command_name}' does not define an 'app' variable.",
            hint="Add `app = typer.Typer()` to your command module.",
        )

    # Allow custom command name via __command_name__
    final_name = getattr(module, "__command_name__", command_name)
    hidden = getattr(module, "__hidden__", False)

    # Register with the main app
    app.add_typer(command_app, name=final_name, hidden=hidden)
    registered[final_name] = str(file_path)


def _register_nested_command(
    group_app: typer.Typer,
    group_name: str,
    file_path: Path,
) -> None:
    """Register a command inside a command group."""
    command_name = file_path.stem.replace("_", "-")

    # Import the command module
    module_path = f"hterm.cli.commands.{group_name}.{file_path.stem}"
    try:
        module = import_module(module_path)
    except ImportError as e:
        raise AppError(
            message=f"Failed to import nested command '{command_name}': {e}",
            hint=f"Check that {module_path} is a valid Python module.",
        ) from e

    # Get the Typer app from the module
    command_app: typer.Typer | None = getattr(module, "app", None)
    if command_app is None:
        raise AppError(
            message=f"Nested command module '{command_name}' does not define an 'app' variable.",
            hint="Add `app = typer.Typer()` to your command module.",
        )

    # Allow custom command name via __command_name__
    final_name = getattr(module, "__command_name__", command_name)
    hidden = getattr(module, "__hidden__", False)

    # Register with the group app
    group_app.add_typer(command_app, name=final_name, hidden=hidden)


# Discover commands on import
_discover_commands()
