"""Ghostty client discovery and creation behind injectable process boundaries."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from hterm.errors import HtermError
from hterm.process import ProcessRunner

_GHOSTTY_BUNDLE_ID = "com.mitchellh.ghostty"
_WINDOW_FORMAT = (
    "%{window-id} %{app-bundle-id} %{window-title} %{workspace} "
    "%{workspace-is-visible} %{workspace-is-focused}"
)
_ATTACH_COMMAND = "herdr session attach default"


@dataclass(frozen=True, slots=True)
class GhosttyWindow:
    """The window metadata needed by Ghostty presentation."""

    window_id: int
    title: str
    workspace: str | None = None
    visible: bool = False
    focused: bool = False
    app_bundle_id: str = _GHOSTTY_BUNDLE_ID


@dataclass(frozen=True, slots=True)
class PresentationResult:
    ghostty_window_id: int
    aerospace_workspace: str | None
    focused: bool = False
    created: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ghostty_window_id": self.ghostty_window_id,
            "focused": self.focused,
            "created": self.created,
        }
        if self.aerospace_workspace is not None:
            result["aerospace_workspace"] = self.aerospace_workspace
        return result


class WindowManager(Protocol):
    def windows(self) -> Sequence[GhosttyWindow]: ...

    def focus(self, window_id: int) -> None: ...


class GhosttyCreator(Protocol):
    def create_attached_window(self) -> None: ...


class AeroSpaceWindowSource:
    """Discover Ghostty windows and focus an exact window through AeroSpace."""

    def __init__(self, binary: Path, runner: ProcessRunner) -> None:
        self.binary = str(binary)
        self.runner = runner

    def windows(self) -> tuple[GhosttyWindow, ...]:
        command = (
            self.binary,
            "list-windows",
            "--all",
            "--json",
            "--format",
            _WINDOW_FORMAT,
        )
        try:
            result = self.runner.run(command, timeout=10)
        except FileNotFoundError as exc:
            raise HtermError(
                "aerospace_not_found",
                f"AeroSpace executable not found: {self.binary}",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HtermError(
                "aerospace_timeout", "Timed out while listing Ghostty windows"
            ) from exc
        except OSError as exc:
            raise HtermError(
                "aerospace_command_failed", f"Unable to query AeroSpace: {exc}"
            ) from exc
        if result.returncode != 0:
            raise HtermError(
                "aerospace_command_failed",
                result.stderr.strip()
                or result.stdout.strip()
                or "AeroSpace window query failed",
                {"exit_code": result.returncode},
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HtermError(
                "aerospace_protocol_error", "AeroSpace returned malformed JSON"
            ) from exc
        if not isinstance(payload, list):
            raise HtermError(
                "aerospace_protocol_error", "AeroSpace returned an invalid response"
            )

        windows: list[GhosttyWindow] = []
        for item in payload:
            if not isinstance(item, dict):
                raise HtermError(
                    "aerospace_protocol_error",
                    "AeroSpace returned invalid window metadata",
                )
            if item.get("app-bundle-id") != _GHOSTTY_BUNDLE_ID:
                continue
            window_id = item.get("window-id")
            title = item.get("window-title", "")
            workspace = item.get("workspace")
            visible = item.get("workspace-is-visible")
            focused = item.get("workspace-is-focused")
            if (
                not isinstance(window_id, int)
                or isinstance(window_id, bool)
                or not isinstance(title, str)
                or (workspace is not None and not isinstance(workspace, str))
                or not isinstance(visible, bool)
                or not isinstance(focused, bool)
            ):
                raise HtermError(
                    "aerospace_protocol_error",
                    "AeroSpace returned invalid Ghostty window metadata",
                )
            windows.append(
                GhosttyWindow(
                    window_id,
                    title,
                    workspace,
                    visible,
                    focused,
                    _GHOSTTY_BUNDLE_ID,
                )
            )
        return tuple(windows)

    def focus(self, window_id: int) -> None:
        try:
            result = self.runner.run(
                (self.binary, "focus", "--window-id", str(window_id)), timeout=10
            )
        except FileNotFoundError as exc:
            raise HtermError(
                "aerospace_not_found",
                f"AeroSpace executable not found: {self.binary}",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HtermError(
                "aerospace_timeout", "Timed out while focusing the Ghostty window"
            ) from exc
        except OSError as exc:
            raise HtermError(
                "aerospace_command_failed", f"Unable to focus with AeroSpace: {exc}"
            ) from exc
        if result.returncode != 0:
            raise HtermError(
                "aerospace_focus_failed",
                result.stderr.strip()
                or result.stdout.strip()
                or f"AeroSpace could not focus window {window_id}",
                {"exit_code": result.returncode, "window_id": window_id},
            )


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class GhosttyAppleScript:
    """Create a Ghostty 1.3 window configured to attach to Herdr."""

    def __init__(
        self,
        app: Path,
        runner: ProcessRunner,
        *,
        osascript_binary: str = "/usr/bin/osascript",
        timeout: float = 10,
    ) -> None:
        self.app = app
        self.runner = runner
        self.osascript_binary = osascript_binary
        self.timeout = timeout

    def create_attached_window(self) -> None:
        if not self.app.is_dir():
            raise HtermError(
                "ghostty_not_found", f"Ghostty application not found: {self.app}"
            )
        app = _applescript_string(str(self.app))
        command = _applescript_string(_ATTACH_COMMAND)
        script = f"""using terms from application "Ghostty"
    tell application {app}
        set cfg to new surface configuration
        set command of cfg to {command}
        new window with configuration cfg
    end tell
end using terms from
"""
        try:
            result = self.runner.run(
                (self.osascript_binary, "-e", script), timeout=self.timeout
            )
        except FileNotFoundError as exc:
            raise HtermError(
                "osascript_not_found", "AppleScript executable was not found"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HtermError(
                "ghostty_automation_timeout", "Ghostty automation timed out"
            ) from exc
        except OSError as exc:
            raise HtermError(
                "ghostty_automation_failed", f"Unable to automate Ghostty: {exc}"
            ) from exc
        if result.returncode == 0:
            return

        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Ghostty automation failed"
        )
        lowered = message.casefold()
        if "macos-applescript" in lowered or "applescript is disabled" in lowered:
            code = "ghostty_applescript_disabled"
        elif any(
            marker in lowered
            for marker in ("-1743", "not authorized", "not permitted", "permission")
        ):
            code = "ghostty_automation_denied"
        else:
            code = "ghostty_automation_failed"
        raise HtermError(code, message, {"exit_code": result.returncode})


class GhosttyPresenter:
    """Reuse an attached client or identify exactly the window just created."""

    def __init__(
        self,
        windows: WindowManager,
        creator: GhosttyCreator,
        *,
        title_match: str = "herdr",
        poll_timeout: float = 5,
        poll_interval: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.windows = windows
        self.creator = creator
        self.title_match = title_match.casefold()
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self.sleep = sleep
        self.monotonic = monotonic

    def present(self) -> PresentationResult:
        before = tuple(self.windows.windows())
        candidates = [
            window for window in before if self.title_match in window.title.casefold()
        ]
        if candidates:
            chosen = self._choose(candidates)
            return self._focus(chosen, created=False)

        previous_ids = {window.window_id for window in before}
        self.creator.create_attached_window()
        deadline = self.monotonic() + self.poll_timeout
        while self.monotonic() < deadline:
            current = tuple(self.windows.windows())
            created = [
                window for window in current if window.window_id not in previous_ids
            ]
            if created:
                chosen = self._choose(created)
                return self._focus(chosen, created=True)
            self.sleep(self.poll_interval)
        raise HtermError(
            "ghostty_window_timeout",
            "Timed out waiting for the new Ghostty window",
            {"timeout_seconds": self.poll_timeout},
        )

    @staticmethod
    def _choose(windows: Sequence[GhosttyWindow]) -> GhosttyWindow:
        return min(
            windows,
            key=lambda window: (
                not window.focused,
                not window.visible,
                window.window_id,
            ),
        )

    def _focus(self, window: GhosttyWindow, *, created: bool) -> PresentationResult:
        self.windows.focus(window.window_id)
        return PresentationResult(
            window.window_id,
            window.workspace,
            focused=True,
            created=created,
        )
