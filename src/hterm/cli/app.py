"""The hterm command-line interface."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import typer
from typer.core import TyperGroup

from hterm import __version__
from hterm.completion import (
    ZSH_COMPLETION,
    project_alias_lines,
    project_completion_lines,
)
from hterm.config import Config, load_config
from hterm.errors import ConfigurationError, HtermError
from hterm.lifecycle import workspace_closed
from hterm.orchestration import dry_run_result, orchestrate
from hterm.output import emit_error, emit_success
from hterm.plugin import install_finder_plugin, install_lifecycle_plugin

DEFAULT_CONFIG_PATH = Path("~/.hterm.toml")
RESERVED_COMMANDS = frozenset(
    {"add", "open", "list", "check", "completion", "config", "finder", "lifecycle"}
)

_INITIAL_CONFIG = """version = 1
default = "home"

[projects.home]
cwd = "~"
label = "home"
"""


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    """Input passed from every launch syntax to the orchestration layer."""

    project: str | None
    config_path: Path
    dry_run: bool
    focus: bool


def _config_path(value: Path) -> Path:
    return value.expanduser().resolve(strict=False)


def launch(request: LaunchRequest) -> dict[str, Any]:
    """Resolve and launch one configured project."""
    config = load_config(request.config_path)
    project = config.resolve(request.project)
    if request.dry_run:
        return dry_run_result(config, project, focus=request.focus)
    return orchestrate(config, project, focus=request.focus).to_dict()


def _run_launch(
    project: str | None,
    *,
    config: Path,
    json_output: bool,
    dry_run: bool,
    no_focus: bool,
) -> None:
    try:
        data = launch(
            LaunchRequest(
                project=project,
                config_path=_config_path(config),
                dry_run=dry_run,
                focus=not no_focus,
            )
        )
    except HtermError as error:
        emit_error(error, json_output=json_output)
        raise typer.Exit(error.exit_code) from error

    label = project or "the default project"
    emit_success(
        "open",
        data,
        json_output=json_output,
        human_message=f"Opened {label}" if not dry_run else f"Would open {label}",
    )


class ProjectGroup(TyperGroup):
    """Treat unknown, non-option command names as project shorthand."""

    def get_command(self, ctx, cmd_name):
        command = super().get_command(ctx, cmd_name)
        if command is not None or cmd_name.startswith("-"):
            return command

        @click.command(name=cmd_name, help=f"Open the {cmd_name!r} project.")
        @click.option(
            "--config", type=click.Path(path_type=Path), default=DEFAULT_CONFIG_PATH
        )
        @click.option("--json", "json_output", is_flag=True)
        @click.option("--dry-run", is_flag=True)
        @click.option("--no-focus", is_flag=True)
        def project_command(
            config: Path, json_output: bool, dry_run: bool, no_focus: bool
        ) -> None:
            _run_launch(
                cmd_name,
                config=config,
                json_output=json_output,
                dry_run=dry_run,
                no_focus=no_focus,
            )

        return project_command


app = typer.Typer(
    cls=ProjectGroup,
    name="hterm",
    help="Launch repeatable Herdr workspaces.",
    no_args_is_help=False,
    invoke_without_command=True,
    pretty_exceptions_enable=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"hterm {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="TOML config path."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan without side effects."),
    no_focus: bool = typer.Option(False, "--no-focus", help="Do not focus a terminal."),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Open the default project when no subcommand is given."""
    del version
    if ctx.invoked_subcommand is None:
        _run_launch(
            None,
            config=config,
            json_output=json_output,
            dry_run=dry_run,
            no_focus=no_focus,
        )


def _prompt_nonempty(prompt: str, *, default: str) -> str:
    """Prompt until a non-empty value is entered."""
    while True:
        value = typer.prompt(prompt, default=default).strip()
        if value:
            return value
        typer.echo(f"{prompt} cannot be empty.", err=True)


def _prompt_project_name(config: Config, *, default: str) -> str:
    occupied = set(config.projects) | set(config.aliases) | set(RESERVED_COMMANDS)
    while True:
        name = _prompt_nonempty("Name", default=default)
        if name not in occupied:
            return name
        typer.echo(f"Project name {name!r} is already used or reserved.", err=True)


def _prompt_directory(*, default: Path) -> Path:
    while True:
        value = _prompt_nonempty("Working directory", default=str(default))
        expanded = Path(os.path.expandvars(value)).expanduser()
        if not expanded.is_absolute():
            expanded = Path.cwd() / expanded
        directory = expanded.resolve(strict=False)
        if directory.is_dir():
            return directory
        typer.echo(f"Directory does not exist: {directory}", err=True)


def _prompt_layout(config: Config) -> str | None:
    layouts = list(config.layouts)
    typer.echo("Layout:")
    typer.echo("  0. None")
    for index, layout in enumerate(layouts, start=1):
        typer.echo(f"  {index}. {layout}")
    selection = typer.prompt(
        "Layout",
        default=0,
        type=click.IntRange(0, len(layouts)),
    )
    return None if selection == 0 else layouts[selection - 1]


def _project_toml(name: str, cwd: Path, label: str, layout: str | None) -> str:
    def quote(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    lines = [
        f"[projects.{quote(name)}]",
        f"cwd = {quote(str(cwd))}",
        f"label = {quote(label)}",
    ]
    if layout is not None:
        lines.append(f"layout = {quote(layout)}")
    return "\n".join(lines) + "\n"


@app.command("add")
def add_project(
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="TOML config path."
    ),
) -> None:
    """Interactively append a project to the configuration."""
    path = _config_path(config)
    loaded = _load_or_exit(path, json_output=False)
    current_directory = Path.cwd().resolve()
    name = _prompt_project_name(loaded, default=current_directory.name or "project")
    label = _prompt_nonempty("Label", default=name)
    cwd = _prompt_directory(default=current_directory)
    layout = _prompt_layout(loaded)

    existing = path.read_text() if path.exists() else _INITIAL_CONFIG
    separator = (
        ""
        if existing.endswith("\n\n")
        else ("\n" if existing.endswith("\n") else "\n\n")
    )
    contents = existing + separator + _project_toml(name, cwd, label, layout)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        load_config(path)
    except (OSError, HtermError) as error:
        if isinstance(error, HtermError):
            failure = error
        else:
            failure = ConfigurationError(
                f"Unable to write configuration: {error}", path=str(path)
            )
        emit_error(failure, json_output=False)
        raise typer.Exit(failure.exit_code) from error

    typer.echo(f"Added project {name!r} to {path}")


@app.command("open")
def open_project(
    project: str | None = typer.Argument(None, help="Project name or alias."),
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="TOML config path."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan without side effects."),
    no_focus: bool = typer.Option(False, "--no-focus", help="Do not focus a terminal."),
) -> None:
    """Open a project, or the configured default project."""
    _run_launch(
        project,
        config=config,
        json_output=json_output,
        dry_run=dry_run,
        no_focus=no_focus,
    )


def _load_or_exit(path: Path, *, json_output: bool) -> Config:
    try:
        return load_config(_config_path(path))
    except HtermError as error:
        emit_error(error, json_output=json_output)
        raise typer.Exit(error.exit_code) from error


def _finder_field(value: str) -> str:
    """Keep one fzf candidate on one tab-delimited line."""
    return " ".join(value.replace("\t", " ").splitlines()).strip()


def _finder_lines(config: Config) -> list[str]:
    lines: list[str] = []
    for project in config.projects.values():
        details = [project.description or str(project.cwd)]
        if project.label != project.name:
            details.append(project.label)
        if project.aliases:
            details.append(f"aliases: {', '.join(project.aliases)}")
        if project.keywords:
            details.append(f"keywords: {', '.join(project.keywords)}")
        lines.append(f"{project.name}\t{_finder_field(' · '.join(details))}")
    return lines


@app.command("list")
def list_projects(
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result."),
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="TOML config path."
    ),
    completion_data: bool = typer.Option(False, "--completion-data", hidden=True),
    completion_aliases: bool = typer.Option(False, "--completion-aliases", hidden=True),
    finder_data: bool = typer.Option(False, "--finder-data", hidden=True),
) -> None:
    """List configured projects."""
    loaded = _load_or_exit(config, json_output=json_output)
    if completion_data:
        typer.echo("\n".join(project_completion_lines(loaded)))
        return
    if completion_aliases:
        typer.echo("\n".join(project_alias_lines(loaded)))
        return
    if finder_data:
        typer.echo("\n".join(_finder_lines(loaded)))
        return
    projects = [project.listing() for project in loaded.projects.values()]
    human = "\n".join(
        f"{item['name']}\t{item['description'] or item['cwd']}" for item in projects
    )
    emit_success(
        "list", {"projects": projects}, json_output=json_output, human_message=human
    )


@app.command("check")
def check_project(
    project: str | None = typer.Argument(None, help="Optional project name or alias."),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result."),
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="TOML config path."
    ),
) -> None:
    """Validate configuration and an optional project."""
    loaded = _load_or_exit(config, json_output=json_output)
    try:
        checked = loaded.resolve(project) if project is not None else None
    except HtermError as error:
        emit_error(error, json_output=json_output)
        raise typer.Exit(error.exit_code) from error
    data = {"path": str(loaded.path), "project": checked.name if checked else None}
    message = f"Configuration is valid: {loaded.path}"
    if checked:
        message = f"Project is valid: {checked.name}"
    emit_success("check", data, json_output=json_output, human_message=message)


@app.command("completion")
def completion(
    shell: str = typer.Argument(..., help="Shell name (currently zsh)."),
) -> None:
    """Generate shell completion code."""
    if shell != "zsh":
        raise typer.BadParameter("only zsh completion is supported", param_hint="shell")
    typer.echo(ZSH_COMPLETION, nl=False)


config_app = typer.Typer(help="Inspect configuration.")
app.add_typer(config_app, name="config")


@config_app.command("path")
def show_config_path(
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="TOML config path."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result."),
) -> None:
    """Print the effective configuration path."""
    path = _config_path(config)
    emit_success(
        "config-path",
        {"path": str(path)},
        json_output=json_output,
        human_message=str(path),
    )


finder_app = typer.Typer(help="Install the Herdr fzf project finder.")
app.add_typer(finder_app, name="finder")


@finder_app.command("install-plugin")
def finder_install_plugin(
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="TOML config path."
    ),
    hterm_binary: Path | None = typer.Option(
        None,
        "--hterm-binary",
        help="Absolute hterm executable path if it cannot be auto-detected.",
    ),
    fzf_binary: Path | None = typer.Option(
        None,
        "--fzf-binary",
        help="Absolute fzf executable path if it cannot be auto-detected.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result."),
) -> None:
    """Link and configure the bundled Herdr fzf picker plugin."""
    loaded = _load_or_exit(config, json_output=json_output)
    try:
        data = install_finder_plugin(
            loaded, hterm_binary=hterm_binary, fzf_binary=fzf_binary
        )
    except HtermError as error:
        emit_error(error, json_output=json_output)
        raise typer.Exit(error.exit_code) from error
    message = (
        f"Installed Herdr plugin {data['plugin_id']}. Add this to your Herdr config "
        f"and reload it:\n\n{data['keybinding']}"
    )
    emit_success(
        "install-finder-plugin",
        data,
        json_output=json_output,
        human_message=message,
    )


lifecycle_app = typer.Typer(help="Handle Herdr workspace lifecycle events.")
app.add_typer(lifecycle_app, name="lifecycle")


@lifecycle_app.command("workspace-closed", hidden=True)
def lifecycle_workspace_closed(
    workspace_id: str = typer.Argument(..., help="Closed Herdr workspace ID."),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result."),
) -> None:
    """Run a claimed post-hook for a closed workspace."""
    try:
        result = workspace_closed(workspace_id)
    except HtermError as error:
        emit_error(error, json_output=json_output)
        raise typer.Exit(error.exit_code) from error
    data = result.to_dict()
    emit_success(
        "workspace-closed",
        data,
        json_output=json_output,
        human_message=f"Workspace {workspace_id}: {result.status}",
    )


@lifecycle_app.command("install-plugin")
def lifecycle_install_plugin(
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="TOML config path."
    ),
    hterm_binary: Path | None = typer.Option(
        None,
        "--hterm-binary",
        help="Absolute hterm executable path if it cannot be auto-detected.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON result."),
) -> None:
    """Link and configure the bundled Herdr workspace-close plugin."""
    loaded = _load_or_exit(config, json_output=json_output)
    try:
        data = install_lifecycle_plugin(loaded, hterm_binary=hterm_binary)
    except HtermError as error:
        emit_error(error, json_output=json_output)
        raise typer.Exit(error.exit_code) from error
    emit_success(
        "install-lifecycle-plugin",
        data,
        json_output=json_output,
        human_message=f"Installed Herdr plugin {data['plugin_id']}",
    )
