"""Configuration schema and command tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hterm.cli.app import app
from hterm.config import load_config
from hterm.errors import ConfigurationError

runner = CliRunner()


def write_config(path: Path, project_dir: Path, extra: str = "") -> None:
    path.write_text(
        f'''version = 1
default = "do2"

[settings]
hook_timeout_seconds = 12
focus = false
herdr_title_match = "herdr client"

[projects.do2]
description = "DO2 development"
cwd = "{project_dir}"
label = "DO2"
aliases = ["d2"]
keywords = ["work", "development"]
pre_hook = "test -d ."
setup_hook = "echo setup"
post_hook = "echo closed"

[[projects.do2.tabs]]
name = "code"
command = "pi"

[[projects.do2.tabs]]
name = "shell"
focus = true
{extra}'''
    )


def test_missing_config_uses_home_default(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")
    assert config.default == "home"
    assert config.resolve().cwd == Path.home().resolve()
    assert config.resolve().tabs == ()


def test_loads_full_project_and_inherits_tab_cwd(tmp_path: Path) -> None:
    config_path = tmp_path / "hterm.toml"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    write_config(config_path, project_dir)

    config = load_config(config_path)
    project = config.resolve("d2")

    assert project.name == "do2"
    assert project.aliases == ("d2",)
    assert project.keywords == ("work", "development")
    assert project.tabs[0].cwd == project_dir
    assert project.tabs[1].focus is True
    assert config.settings.hook_timeout_seconds == 12
    assert config.settings.focus is False
    assert config.settings.herdr_title_match == "herdr client"


def test_expands_environment_variables_in_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "path with spaces"
    project_dir.mkdir()
    monkeypatch.setenv("HTERM_TEST_PROJECT", str(project_dir))
    config_path = tmp_path / "hterm.toml"
    config_path.write_text(
        """version = 1
[projects.home]
cwd = "$HTERM_TEST_PROJECT"
"""
    )

    assert load_config(config_path).resolve().cwd == project_dir


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("version = 2", "Unsupported configuration version"),
        ("version = 1\n[projects.open]\ncwd = '~'", "reserved project name"),
        (
            "version = 1\n[projects.home]\ncwd = '/definitely/missing/hterm'",
            "not an existing directory",
        ),
        (
            "version = 1\n[projects.home]\ncwd = '~'\n"
            "[[projects.home.tabs]]\nfocus = true\n"
            "[[projects.home.tabs]]\nfocus = true",
            "more than one focused tab",
        ),
    ],
)
def test_invalid_config_is_actionable(tmp_path: Path, body: str, message: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(body)

    with pytest.raises(ConfigurationError, match=message) as raised:
        load_config(path)

    assert raised.value.code == "configuration_error"
    assert raised.value.details["path"] == str(path)


def test_duplicate_alias_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(
        """version = 1
[projects.one]
cwd = "~"
aliases = ["shared"]
[projects.two]
cwd = "~"
aliases = ["shared"]
"""
    )
    with pytest.raises(ConfigurationError, match="Duplicate or reserved project alias"):
        load_config(path)


def test_list_json_is_machine_oriented(tmp_path: Path) -> None:
    config_path = tmp_path / "hterm.toml"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    write_config(config_path, project_dir)

    result = runner.invoke(app, ["list", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["projects"] == [
        {
            "aliases": ["d2"],
            "cwd": str(project_dir),
            "description": "DO2 development",
            "keywords": ["work", "development"],
            "label": "DO2",
            "name": "do2",
        }
    ]


def test_check_resolves_alias_and_reports_json_errors(tmp_path: Path) -> None:
    config_path = tmp_path / "hterm.toml"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    write_config(config_path, project_dir)

    valid = runner.invoke(app, ["check", "d2", "--config", str(config_path), "--json"])
    missing = runner.invoke(
        app, ["check", "missing", "--config", str(config_path), "--json"]
    )

    assert valid.exit_code == 0
    assert json.loads(valid.stdout)["project"] == "do2"
    assert missing.exit_code == 1
    assert json.loads(missing.stdout)["error"]["code"] == "project_not_found"
