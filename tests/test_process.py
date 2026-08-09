"""Tests for the real subprocess boundary used by tools and hooks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hterm.process import SubprocessRunner


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n")
    path.chmod(0o755)
    return path


def test_subprocess_runner_preserves_arguments_and_cwd_with_spaces(
    tmp_path: Path,
) -> None:
    working = tmp_path / "working directory"
    working.mkdir()
    executable = _script(
        tmp_path / "fake tool",
        'printf "%s\\n%s\\n%s" "$PWD" "$1" "$HTERM_FAKE_VALUE"',
    )

    result = SubprocessRunner().run(
        (str(executable), "argument with spaces"),
        cwd=working,
        env={"HTERM_FAKE_VALUE": "environment with spaces"},
        timeout=2,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        str(working),
        "argument with spaces",
        "environment with spaces",
    ]
    assert result.args == (str(executable), "argument with spaces")


def test_subprocess_runner_surfaces_missing_binary_timeout_and_signal(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunner()
    with pytest.raises(FileNotFoundError):
        runner.run((str(tmp_path / "missing tool"),))

    sleeper = _script(tmp_path / "sleeper", "sleep 10")
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run((str(sleeper),), timeout=0.01)

    signaled = _script(tmp_path / "signaled", "kill -TERM $$")
    result = runner.run((str(signaled),), timeout=2)
    assert result.returncode == -15
