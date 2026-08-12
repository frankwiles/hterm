"""Tests for CLI parsing and stable output."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from hterm.cli import app as app_module
from hterm.cli.app import LaunchRequest, app
from hterm.errors import HtermError

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Launch repeatable Herdr workspaces" in result.stdout
    assert "open" in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == "hterm 0.1.0\n"


def test_default_project_routing(monkeypatch) -> None:
    requests: list[LaunchRequest] = []
    monkeypatch.setattr(
        app_module, "launch", lambda request: requests.append(request) or {}
    )

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert requests[0].project is None


def test_shorthand_and_canonical_route_identically(monkeypatch) -> None:
    requests: list[LaunchRequest] = []
    monkeypatch.setattr(
        app_module, "launch", lambda request: requests.append(request) or {}
    )

    shorthand = runner.invoke(app, ["do2", "--config", "other.toml", "--no-focus"])
    canonical = runner.invoke(
        app, ["open", "do2", "--config", "other.toml", "--no-focus"]
    )

    assert shorthand.exit_code == canonical.exit_code == 0
    assert requests[0] == requests[1]
    assert requests[0].project == "do2"
    assert requests[0].focus is False
    assert requests[0].config_path == Path("other.toml").resolve()


def test_json_success_is_single_document() -> None:
    result = runner.invoke(app, ["home", "--dry-run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "open"
    assert payload["project"] == "home"
    assert result.stderr == ""


def test_json_failure_is_single_document(monkeypatch) -> None:
    def fail(_request: LaunchRequest) -> dict:
        raise HtermError("herdr_command_failed", "Herdr is unavailable")

    monkeypatch.setattr(app_module, "launch", fail)
    result = runner.invoke(app, ["home", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload == {
        "ok": False,
        "error": {
            "code": "herdr_command_failed",
            "message": "Herdr is unavailable",
        },
    }
    assert result.stderr == ""


def test_human_failure_uses_stderr(monkeypatch) -> None:
    def fail(_request: LaunchRequest) -> dict:
        raise HtermError("herdr_command_failed", "Herdr is unavailable")

    monkeypatch.setattr(app_module, "launch", fail)
    result = runner.invoke(app, ["home"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Herdr is unavailable" in result.stderr


def test_reserved_command_is_not_project_shorthand(monkeypatch) -> None:
    called = False

    def fake_launch(_request: LaunchRequest) -> dict:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(app_module, "launch", fake_launch)
    result = runner.invoke(app, ["list", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["action"] == "list"
    assert called is False


def test_add_creates_config_and_prompts_for_project_fields(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    config_path = tmp_path / "hterm.toml"

    result = runner.invoke(
        app,
        ["add", "--config", str(config_path)],
        input="\nDemo Workspace\n\n0\n",
    )

    assert result.exit_code == 0, result.output
    contents = config_path.read_text()
    assert '[projects."home"]' not in contents
    assert "[projects.home]" in contents
    assert '[projects."demo"]' in contents
    assert f'cwd = "{project_dir}"' in contents
    assert 'label = "Demo Workspace"' in contents
    assert "layout =" not in contents
    assert "Added project 'demo'" in result.stdout


def test_add_reprompts_for_duplicate_name_and_selects_layout(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    config_path = tmp_path / "hterm.toml"
    config_path.write_text(
        f'''version = 1
default = "home"

[[layouts.coding.tabs]]
name = "shell"

[projects.home]
cwd = "~"

[projects.demo]
cwd = "{project_dir}"
'''
    )

    result = runner.invoke(
        app,
        ["add", "--config", str(config_path)],
        input="\nfresh\n\n\n1\n",
    )

    assert result.exit_code == 0, result.output
    assert "already used or reserved" in result.output
    contents = config_path.read_text()
    assert '[projects."fresh"]' in contents
    assert 'label = "fresh"' in contents
    assert 'layout = "coding"' in contents


def test_invalid_usage_exits_two() -> None:
    result = runner.invoke(app, ["open", "one", "two"])
    assert result.exit_code == 2
