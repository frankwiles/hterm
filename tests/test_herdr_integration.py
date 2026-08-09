"""Opt-in checks against the locally installed Herdr CLI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from hterm.orchestration import HerdrClient
from hterm.presentation import AeroSpaceWindowSource, GhosttyAppleScript
from hterm.process import SubprocessRunner

pytestmark = pytest.mark.macos_integration


@pytest.mark.skipif(
    os.environ.get("HTERM_RUN_MACOS_INTEGRATION") != "1",
    reason="set HTERM_RUN_MACOS_INTEGRATION=1 to use installed Herdr",
)
def test_installed_herdr_create_response_shapes(tmp_path: Path) -> None:
    binary = shutil.which("herdr")
    if binary is None:
        pytest.skip("Herdr is not installed")
    client = HerdrClient(Path(binary), SubprocessRunner())
    client.ensure_server()
    workspace_id, tab_id, pane_id = client.create_workspace(
        tmp_path, "hterm integration test"
    )
    try:
        assert workspace_id
        assert tab_id.startswith(f"{workspace_id}:")
        assert pane_id.startswith(f"{workspace_id}:")
    finally:
        client.close_workspace(workspace_id)


@pytest.mark.skipif(
    os.environ.get("HTERM_RUN_MACOS_INTEGRATION") != "1",
    reason="set HTERM_RUN_MACOS_INTEGRATION=1 to inspect installed macOS tools",
)
def test_installed_aerospace_response_shape() -> None:
    binary = shutil.which("aerospace")
    if binary is None:
        pytest.skip("AeroSpace is not installed")

    windows = AeroSpaceWindowSource(Path(binary), SubprocessRunner()).windows()

    assert all(window.window_id > 0 for window in windows)


@pytest.mark.skipif(
    os.environ.get("HTERM_RUN_MACOS_INTEGRATION") != "1",
    reason="set HTERM_RUN_MACOS_INTEGRATION=1 to inspect installed macOS tools",
)
def test_installed_ghostty_application_is_discoverable() -> None:
    app = Path("/Applications/Ghostty.app")
    if not app.is_dir():
        pytest.skip("Ghostty is not installed in /Applications")

    # Deliberately do not create a window in an automated test. Validate the same
    # installation precondition used before requesting Automation permission.
    creator = GhosttyAppleScript(app, SubprocessRunner())
    assert creator.app.is_dir()
    assert creator.osascript_binary == "/usr/bin/osascript"
