"""Tests for dynamic Zsh completion generation and project data."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hterm.cli.app import app
from hterm.completion import ZSH_COMPLETION

runner = CliRunner()
zsh = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed")


def config_text(project_dir: Path, name: str = "do2") -> str:
    return f'''version = 1
default = "{name}"
[projects.{name}]
cwd = "{project_dir}"
description = "Development: workspace"
aliases = ["d2"]
'''


@zsh
def test_completion_zsh_emits_valid_dynamic_function(tmp_path: Path) -> None:
    result = runner.invoke(app, ["completion", "zsh"])

    assert result.exit_code == 0
    assert result.stdout == ZSH_COMPLETION
    assert "list --completion-data" in result.stdout
    assert "_hterm_projects" in result.stdout
    script = tmp_path / "_hterm"
    script.write_text(result.stdout)
    syntax = subprocess.run(
        ("zsh", "-n", str(script)), check=False, capture_output=True, text=True
    )
    assert syntax.returncode == 0, syntax.stderr


def test_unsupported_completion_shell_is_usage_error() -> None:
    result = runner.invoke(app, ["completion", "bash"])
    assert result.exit_code == 2
    assert "only zsh completion is supported" in result.stderr


def test_completion_data_contains_names_aliases_and_escaped_descriptions(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = tmp_path / "hterm.toml"
    config.write_text(config_text(project_dir))

    result = runner.invoke(app, ["list", "--completion-data", "--config", str(config)])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        r"do2:Development\: workspace (aliases\: d2)",
    ]
    aliases = runner.invoke(
        app, ["list", "--completion-aliases", "--config", str(config)]
    )
    assert aliases.stdout == "d2\tdo2\n"


def test_completion_data_reflects_config_changes_without_regeneration(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = tmp_path / "hterm.toml"
    config.write_text(config_text(project_dir))
    completion_script = runner.invoke(app, ["completion", "zsh"]).stdout

    first = runner.invoke(app, ["list", "--completion-data", "--config", str(config)])
    config.write_text(config_text(project_dir, name="docs"))
    second = runner.invoke(app, ["list", "--completion-data", "--config", str(config)])

    assert first.stdout.startswith("do2:")
    assert second.stdout.startswith("docs:")
    assert runner.invoke(app, ["completion", "zsh"]).stdout == completion_script


@zsh
def test_alias_matching_inserts_the_canonical_project_name(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = tmp_path / "hterm.toml"
    config.write_text(config_text(project_dir))
    completion = tmp_path / "_hterm"
    completion.write_text(ZSH_COMPLETION)
    executable = tmp_path / "hterm"
    executable.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m hterm "$@"\n'
    )
    executable.chmod(0o755)
    harness = f"""compdef() {{ : }}
source {shlex.quote(str(completion))}
_describe() {{ : }}
compadd() {{ print -- "${{@[-1]}}" }}
words=({shlex.quote(str(executable))} --config {shlex.quote(str(config))} d)
CURRENT=4
PREFIX=d
_hterm_projects
"""

    completed = subprocess.run(
        ("zsh", "-f"),
        input=harness,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "do2\n"


def test_completion_script_requests_projects_for_both_launch_forms() -> None:
    assert "first)\n      _hterm_commands\n      _hterm_projects" in ZSH_COMPLETION
    assert (
        "open)\n          if (( CURRENT == 3 )); then\n            _hterm_projects"
        in ZSH_COMPLETION
    )
    assert (
        "check)\n          if (( CURRENT == 3 )); then\n            _hterm_projects"
        in ZSH_COMPLETION
    )
