"""Smoke tests for hterm CLI."""

from typer.testing import CliRunner

from hterm.cli.app import app

runner = CliRunner()


def test_help() -> None:
    """Test that --help works and shows usage."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "hterm" in result.stdout.lower() or "usage" in result.stdout.lower()


def test_version() -> None:
    """Test that --version works."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "hterm" in result.stdout



def test_hello_world() -> None:
    """Test the hello world command."""
    result = runner.invoke(app, ["hello", "world"])
    assert result.exit_code == 0
    assert "Hello" in result.stdout or "Success" in result.stdout


def test_hello_name() -> None:
    """Test the hello name command."""
    result = runner.invoke(app, ["hello", "name", "TestUser"])
    assert result.exit_code == 0
    assert "TestUser" in result.stdout

