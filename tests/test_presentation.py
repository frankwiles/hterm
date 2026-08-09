"""Tests for Ghostty discovery, AppleScript creation, and polling."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from hterm.errors import HtermError
from hterm.presentation import (
    AeroSpaceWindowSource,
    GhosttyAppleScript,
    GhosttyPresenter,
    GhosttyWindow,
)
from hterm.process import ProcessResult


class Runner:
    def __init__(self, responses: list[ProcessResult | BaseException]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        del cwd, env, timeout
        self.calls.append(tuple(args))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def start(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        raise AssertionError((args, cwd, env))


class Windows:
    def __init__(self, snapshots: list[tuple[GhosttyWindow, ...]]) -> None:
        self.snapshots = snapshots
        self.calls = 0
        self.focused_ids: list[int] = []

    def windows(self) -> tuple[GhosttyWindow, ...]:
        self.calls += 1
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    def focus(self, window_id: int) -> None:
        self.focused_ids.append(window_id)


class Creator:
    def __init__(self, error: HtermError | None = None) -> None:
        self.calls = 0
        self.error = error

    def create_attached_window(self) -> None:
        self.calls += 1
        if self.error:
            raise self.error


def result(stdout: str = "", stderr: str = "", returncode: int = 0) -> ProcessResult:
    return ProcessResult((), returncode, stdout, stderr)


def test_aerospace_source_filters_non_ghostty_windows() -> None:
    runner = Runner(
        [
            result(
                json.dumps(
                    [
                        {
                            "window-id": 12,
                            "app-bundle-id": "other.app",
                            "window-title": "herdr",
                            "workspace": "1",
                            "workspace-is-visible": True,
                            "workspace-is-focused": False,
                        },
                        {
                            "window-id": 18,
                            "app-bundle-id": "com.mitchellh.ghostty",
                            "window-title": "herdr — default",
                            "workspace": "T",
                            "workspace-is-visible": True,
                            "workspace-is-focused": True,
                        },
                    ]
                )
            )
        ]
    )

    windows = AeroSpaceWindowSource(Path("/fake/aerospace"), runner).windows()

    assert windows == (
        GhosttyWindow(18, "herdr — default", "T", visible=True, focused=True),
    )
    assert runner.calls[0][:4] == (
        "/fake/aerospace",
        "list-windows",
        "--all",
        "--json",
    )


def test_existing_herdr_window_is_reused_without_creation() -> None:
    windows = Windows(
        [
            (
                GhosttyWindow(9, "shell", "1"),
                GhosttyWindow(7, "Herdr default", "T"),
            )
        ]
    )
    creator = Creator()

    presentation = GhosttyPresenter(windows, creator).present()

    assert presentation.ghostty_window_id == 7
    assert presentation.created is False
    assert presentation.focused is True
    assert windows.focused_ids == [7]
    assert creator.calls == 0


def test_new_window_is_identified_by_snapshot_difference() -> None:
    old = GhosttyWindow(9, "shell", "1")
    new = GhosttyWindow(41, "herdr", "T")
    windows = Windows([(old,), (old,), (old, new)])
    creator = Creator()
    clock_values = iter((0.0, 0.0, 0.1))

    presentation = GhosttyPresenter(
        windows,
        creator,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock_values),
    ).present()

    assert presentation.ghostty_window_id == 41
    assert presentation.created is True
    assert presentation.focused is True
    assert windows.focused_ids == [41]
    assert creator.calls == 1


def test_new_window_timeout_is_structured() -> None:
    windows = Windows([()])
    clock_values = iter((0.0, 0.0, 6.0))

    with pytest.raises(HtermError) as raised:
        GhosttyPresenter(
            windows,
            Creator(),
            sleep=lambda _seconds: None,
            monotonic=lambda: next(clock_values),
        ).present()

    assert raised.value.code == "ghostty_window_timeout"
    assert raised.value.details["timeout_seconds"] == 5


def test_multiple_candidates_prefer_focused_then_visible_then_id() -> None:
    windows = Windows(
        [
            (
                GhosttyWindow(1, "herdr hidden", "A"),
                GhosttyWindow(9, "herdr visible", "B", visible=True),
                GhosttyWindow(20, "herdr focused", "C", visible=True, focused=True),
                GhosttyWindow(
                    2, "herdr focused lower", "C", visible=True, focused=True
                ),
            )
        ]
    )

    presentation = GhosttyPresenter(windows, Creator()).present()

    assert presentation.ghostty_window_id == 2
    assert presentation.aerospace_workspace == "C"
    assert windows.focused_ids == [2]


def test_visible_candidate_wins_over_lower_hidden_id() -> None:
    windows = Windows(
        [
            (
                GhosttyWindow(1, "herdr hidden", "A"),
                GhosttyWindow(9, "herdr visible", "B", visible=True),
            )
        ]
    )

    assert GhosttyPresenter(windows, Creator()).present().ghostty_window_id == 9


def test_aerospace_focus_uses_exact_window_id() -> None:
    runner = Runner([result()])
    source = AeroSpaceWindowSource(Path("/fake/aerospace"), runner)

    source.focus(18039)

    assert runner.calls == [("/fake/aerospace", "focus", "--window-id", "18039")]


def test_aerospace_focus_failure_is_structured() -> None:
    source = AeroSpaceWindowSource(
        Path("/fake/aerospace"),
        Runner([result(stderr="unknown window", returncode=1)]),
    )

    with pytest.raises(HtermError) as raised:
        source.focus(42)

    assert raised.value.code == "aerospace_focus_failed"
    assert raised.value.details == {"exit_code": 1, "window_id": 42}


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (result(stdout="not json"), "aerospace_protocol_error"),
        (FileNotFoundError(), "aerospace_not_found"),
    ],
)
def test_malformed_or_missing_aerospace_is_structured(
    response: ProcessResult | BaseException, code: str
) -> None:
    source = AeroSpaceWindowSource(Path("/fake/aerospace"), Runner([response]))

    with pytest.raises(HtermError) as raised:
        source.windows()

    assert raised.value.code == code


def test_applescript_creates_configured_attach_window(tmp_path: Path) -> None:
    app = tmp_path / "Ghostty.app"
    app.mkdir()
    runner = Runner([result()])

    GhosttyAppleScript(app, runner).create_attached_window()

    script = runner.calls[0][2]
    assert runner.calls[0][:2] == ("/usr/bin/osascript", "-e")
    assert "new surface configuration" in script
    assert 'set command of cfg to "herdr session attach default"' in script
    assert "new window with configuration cfg" in script


@pytest.mark.parametrize(
    ("stderr", "code"),
    [
        (
            "AppleScript is disabled by the macos-applescript configuration.",
            "ghostty_applescript_disabled",
        ),
        ("Not authorized to send Apple events. (-1743)", "ghostty_automation_denied"),
        ("Ghostty rejected the command", "ghostty_automation_failed"),
    ],
)
def test_applescript_failures_are_normalized(
    tmp_path: Path, stderr: str, code: str
) -> None:
    app = tmp_path / "Ghostty.app"
    app.mkdir()
    creator = GhosttyAppleScript(app, Runner([result(stderr=stderr, returncode=1)]))

    with pytest.raises(HtermError) as raised:
        creator.create_attached_window()

    assert raised.value.code == code
    assert raised.value.details["exit_code"] == 1


def test_missing_app_and_timeout_are_normalized(tmp_path: Path) -> None:
    with pytest.raises(HtermError) as missing:
        GhosttyAppleScript(
            tmp_path / "missing.app", Runner([])
        ).create_attached_window()
    assert missing.value.code == "ghostty_not_found"

    app = tmp_path / "Ghostty.app"
    app.mkdir()
    timeout = subprocess.TimeoutExpired(("osascript",), 10)
    with pytest.raises(HtermError) as timed_out:
        GhosttyAppleScript(app, Runner([timeout])).create_attached_window()
    assert timed_out.value.code == "ghostty_automation_timeout"
