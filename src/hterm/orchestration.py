"""Herdr workspace orchestration and local hook execution."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hterm.config import Config, Project, Tab
from hterm.errors import HtermError
from hterm.lifecycle import LifecycleStore
from hterm.presentation import (
    AeroSpaceWindowSource,
    GhosttyAppleScript,
    GhosttyPresenter,
    PresentationResult,
)
from hterm.process import ProcessResult, ProcessRunner, SubprocessRunner


@dataclass(frozen=True, slots=True)
class CreatedTab:
    name: str | None
    tab_id: str
    pane_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tab_id": self.tab_id, "pane_id": self.pane_id}


@dataclass(frozen=True, slots=True)
class WorkspaceResult:
    project: str
    workspace_id: str
    tabs: tuple[CreatedTab, ...]
    presentation: PresentationResult | None = None
    warnings: tuple[Mapping[str, Any], ...] = ()
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "workspace_id": self.workspace_id,
            "tabs": [tab.to_dict() for tab in self.tabs],
            "presentation": (
                self.presentation.to_dict()
                if self.presentation is not None
                else {"focused": False}
            ),
            "warnings": [dict(warning) for warning in self.warnings],
            "reused": self.reused,
        }


class HerdrClient:
    def __init__(
        self,
        binary: Path,
        runner: ProcessRunner,
        *,
        sleep: Callable[[float], None] = time.sleep,
        server_timeout: float = 5,
    ) -> None:
        self.binary = str(binary)
        self.runner = runner
        self.sleep = sleep
        self.server_timeout = server_timeout

    def _execute(self, *args: str, timeout: float | None = 30) -> ProcessResult:
        command = (self.binary, *args)
        try:
            result = self.runner.run(command, timeout=timeout)
        except FileNotFoundError as exc:
            raise HtermError(
                "herdr_not_found", f"Herdr executable not found: {self.binary}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HtermError(
                "herdr_timeout", f"Herdr command timed out: {' '.join(args)}"
            ) from exc
        except OSError as exc:
            raise HtermError(
                "herdr_command_failed", f"Unable to run Herdr: {exc}"
            ) from exc
        if result.returncode != 0:
            message = (
                result.stderr.strip() or result.stdout.strip() or "Herdr command failed"
            )
            external_code: str | None = None
            try:
                failure = json.loads(result.stdout)
            except json.JSONDecodeError:
                failure = None
            if isinstance(failure, dict):
                error = failure.get("error")
                if isinstance(error, dict):
                    code = error.get("code")
                    detail = error.get("message")
                    if isinstance(code, str):
                        external_code = code
                    if isinstance(detail, str) and detail:
                        message = detail
            normalized_code = {
                "protocol_mismatch": "herdr_protocol_mismatch",
                "server_not_running": "herdr_server_not_running",
            }.get(external_code, "herdr_command_failed")
            details: dict[str, Any] = {
                "command": list(args),
                "exit_code": result.returncode,
            }
            if external_code is not None:
                details["herdr_code"] = external_code
            raise HtermError(normalized_code, message, details)
        return result

    def _json(self, *args: str) -> Mapping[str, Any]:
        result = self._execute(*args)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HtermError(
                "herdr_protocol_error",
                "Herdr returned malformed JSON",
                {"command": list(args)},
            ) from exc
        if not isinstance(payload, dict):
            raise HtermError(
                "herdr_protocol_error", "Herdr returned an invalid response"
            )
        return payload

    def ensure_server(self) -> None:
        try:
            payload = self._json("session", "list", "--json")
        except HtermError as error:
            if error.code != "herdr_server_not_running":
                raise
            payload = {}
        sessions = payload.get("sessions")
        if isinstance(sessions, list) and any(
            isinstance(session, dict)
            and session.get("default") is True
            and session.get("running") is True
            for session in sessions
        ):
            return
        try:
            self.runner.start((self.binary, "server"))
        except FileNotFoundError as exc:
            raise HtermError(
                "herdr_not_found", f"Herdr executable not found: {self.binary}"
            ) from exc
        except OSError as exc:
            raise HtermError(
                "herdr_server_failed", f"Unable to start Herdr server: {exc}"
            ) from exc

        deadline = time.monotonic() + self.server_timeout
        while time.monotonic() < deadline:
            self.sleep(0.1)
            try:
                payload = self._json("session", "list", "--json")
            except HtermError as error:
                if error.code == "herdr_server_not_running":
                    continue
                raise
            sessions = payload.get("sessions")
            if isinstance(sessions, list) and any(
                isinstance(session, dict)
                and session.get("default") is True
                and session.get("running") is True
                for session in sessions
            ):
                return
        raise HtermError("herdr_server_timeout", "Timed out starting the Herdr server")

    @staticmethod
    def _created_ids(
        payload: Mapping[str, Any], operation: str
    ) -> tuple[str, str, str]:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise HtermError(
                "herdr_protocol_error", f"Invalid Herdr {operation} response"
            )
        workspace = result.get("workspace")
        tab = result.get("tab")
        pane = result.get("root_pane")
        workspace_id = (
            workspace.get("workspace_id") if isinstance(workspace, dict) else None
        )
        tab_id = tab.get("tab_id") if isinstance(tab, dict) else None
        pane_id = pane.get("pane_id") if isinstance(pane, dict) else None
        if workspace_id is None and isinstance(tab, dict):
            workspace_id = tab.get("workspace_id")
        if workspace_id is None and isinstance(pane, dict):
            workspace_id = pane.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise HtermError("herdr_protocol_error", f"Invalid Herdr {operation} IDs")
        if not isinstance(tab_id, str) or not tab_id:
            raise HtermError("herdr_protocol_error", f"Invalid Herdr {operation} IDs")
        if not isinstance(pane_id, str) or not pane_id:
            raise HtermError("herdr_protocol_error", f"Invalid Herdr {operation} IDs")
        return workspace_id, tab_id, pane_id

    def workspace_with_label(self, label: str) -> str | None:
        payload = self._json("workspace", "list")
        result = payload.get("result")
        workspaces = result.get("workspaces") if isinstance(result, dict) else None
        if not isinstance(workspaces, list):
            raise HtermError(
                "herdr_protocol_error", "Invalid Herdr workspace list response"
            )

        matches: list[tuple[bool, int, str]] = []
        for workspace in workspaces:
            if not isinstance(workspace, dict) or workspace.get("label") != label:
                continue
            workspace_id = workspace.get("workspace_id")
            if not isinstance(workspace_id, str) or not workspace_id:
                raise HtermError(
                    "herdr_protocol_error", "Invalid Herdr workspace list response"
                )
            focused = workspace.get("focused") is True
            number = workspace.get("number")
            matches.append((focused, number if isinstance(number, int) else 2**31, workspace_id))

        if not matches:
            return None
        matches.sort(key=lambda item: (not item[0], item[1], item[2]))
        return matches[0][2]

    def create_workspace(self, cwd: Path, label: str) -> tuple[str, str, str]:
        payload = self._json(
            "workspace", "create", "--cwd", str(cwd), "--label", label, "--no-focus"
        )
        return self._created_ids(payload, "workspace create")

    def create_tab(self, workspace_id: str, tab: Tab) -> tuple[str, str]:
        args = [
            "tab",
            "create",
            "--workspace",
            workspace_id,
            "--cwd",
            str(tab.cwd),
            "--no-focus",
        ]
        if tab.name:
            args.extend(("--label", tab.name))
        payload = self._json(*args)
        response_workspace, tab_id, pane_id = self._created_ids(payload, "tab create")
        if response_workspace != workspace_id:
            raise HtermError(
                "herdr_protocol_error", "Herdr created a tab in the wrong workspace"
            )
        return tab_id, pane_id

    def rename_tab(self, tab_id: str, name: str) -> None:
        self._json("tab", "rename", tab_id, name)

    def run_pane(self, pane_id: str, command: str) -> None:
        # Herdr 0.8 returns an empty body when a command is accepted.
        self._execute("pane", "run", pane_id, command)

    def focus(self, workspace_id: str, tab_id: str | None) -> None:
        if tab_id:
            self._json("tab", "focus", tab_id)
        else:
            self._json("workspace", "focus", workspace_id)

    def close_workspace(self, workspace_id: str) -> None:
        self._json("workspace", "close", workspace_id)


def _hook_environment(
    config: Config,
    project: Project,
    *,
    workspace_id: str | None = None,
    tab_id: str | None = None,
    pane_id: str | None = None,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HTERM_PROJECT": project.name,
            "HTERM_PROJECT_DIR": str(project.cwd),
            "HTERM_CONFIG_PATH": str(config.path),
        }
    )
    if workspace_id and tab_id and pane_id:
        environment.update(
            {
                "HTERM_WORKSPACE_ID": workspace_id,
                "HTERM_TAB_ID": tab_id,
                "HTERM_PANE_ID": pane_id,
            }
        )
    return environment


def run_hook(
    name: str,
    command: str | None,
    *,
    config: Config,
    project: Project,
    runner: ProcessRunner,
    workspace_id: str | None = None,
    tab_id: str | None = None,
    pane_id: str | None = None,
) -> None:
    if command is None:
        return
    try:
        result = runner.run(
            (str(config.settings.hook_shell), "-lc", command),
            cwd=project.cwd,
            env=_hook_environment(
                config,
                project,
                workspace_id=workspace_id,
                tab_id=tab_id,
                pane_id=pane_id,
            ),
            timeout=config.settings.hook_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise HtermError(
            f"{name}_hook_timeout",
            f"{name.replace('_', ' ').title()} hook timed out",
            {"project": project.name, "step": f"{name}_hook"},
        ) from exc
    except (FileNotFoundError, OSError) as exc:
        raise HtermError(
            f"{name}_hook_failed",
            f"Unable to run {name.replace('_', ' ')} hook: {exc}",
            {"project": project.name, "step": f"{name}_hook"},
        ) from exc
    if result.returncode != 0:
        message = (
            result.stderr.strip() or result.stdout.strip() or f"{name} hook failed"
        )
        raise HtermError(
            f"{name}_hook_failed",
            message,
            {
                "project": project.name,
                "step": f"{name}_hook",
                "exit_code": result.returncode,
            },
        )


def dry_run_result(config: Config, project: Project, *, focus: bool) -> dict[str, Any]:
    tabs = project.tabs or (None,)
    return {
        "project": project.name,
        "label": project.label,
        "cwd": str(project.cwd),
        "tabs": [
            {
                "name": tab.name if tab else None,
                "command": tab.command if tab else None,
                "cwd": str(tab.cwd if tab else project.cwd),
                "focus": tab.focus if tab else False,
            }
            for tab in tabs
        ],
        "dry_run": True,
        "config_path": str(config.path),
        "focus": focus and config.settings.focus,
        "warnings": [],
    }


def orchestrate(
    config: Config,
    project: Project,
    *,
    focus: bool,
    runner: ProcessRunner | None = None,
    lifecycle: LifecycleStore | None = None,
    presenter: GhosttyPresenter | None = None,
) -> WorkspaceResult:
    """Reuse a matching Herdr workspace, or create and configure one."""
    runner = runner or SubprocessRunner()
    lifecycle = lifecycle or LifecycleStore()
    herdr = HerdrClient(config.settings.herdr_binary, runner)
    herdr.ensure_server()

    existing_workspace_id = herdr.workspace_with_label(project.label)
    if existing_workspace_id is not None:
        presentation: PresentationResult | None = None
        warnings: tuple[Mapping[str, Any], ...] = ()
        if focus and config.settings.focus:
            herdr.focus(existing_workspace_id, None)
            active_presenter = presenter or GhosttyPresenter(
                AeroSpaceWindowSource(config.settings.aerospace_binary, runner),
                GhosttyAppleScript(config.settings.ghostty_app, runner),
                title_match=config.settings.herdr_title_match,
            )
            try:
                presentation = active_presenter.present()
            except HtermError as error:
                warnings = (error.to_dict(),)
        return WorkspaceResult(
            project.name,
            existing_workspace_id,
            (),
            presentation,
            warnings,
            reused=True,
        )

    run_hook("pre", project.pre_hook, config=config, project=project, runner=runner)
    first_definition = project.tabs[0] if project.tabs else None
    initial_cwd = first_definition.cwd if first_definition else project.cwd
    workspace_id: str | None = None
    try:
        workspace_id, initial_tab_id, initial_pane_id = herdr.create_workspace(
            initial_cwd, project.label
        )
        lifecycle.write(
            workspace_id,
            config=config,
            project=project,
            tab_id=initial_tab_id,
            pane_id=initial_pane_id,
        )
        created = [
            CreatedTab(
                first_definition.name if first_definition else None,
                initial_tab_id,
                initial_pane_id,
            )
        ]
        if first_definition:
            if first_definition.name:
                herdr.rename_tab(initial_tab_id, first_definition.name)
            if first_definition.command:
                herdr.run_pane(initial_pane_id, first_definition.command)

        for definition in project.tabs[1:]:
            tab_id, pane_id = herdr.create_tab(workspace_id, definition)
            created.append(CreatedTab(definition.name, tab_id, pane_id))
            if definition.command:
                herdr.run_pane(pane_id, definition.command)

        run_hook(
            "setup",
            project.setup_hook,
            config=config,
            project=project,
            runner=runner,
            workspace_id=workspace_id,
            tab_id=initial_tab_id,
            pane_id=initial_pane_id,
        )
        presentation: PresentationResult | None = None
        warnings: tuple[Mapping[str, Any], ...] = ()
        if focus and config.settings.focus:
            focused_tab = next(
                (
                    created[index].tab_id
                    for index, definition in enumerate(project.tabs)
                    if definition.focus
                ),
                None,
            )
            herdr.focus(workspace_id, focused_tab)
            active_presenter = presenter or GhosttyPresenter(
                AeroSpaceWindowSource(config.settings.aerospace_binary, runner),
                GhosttyAppleScript(config.settings.ghostty_app, runner),
                title_match=config.settings.herdr_title_match,
            )
            try:
                presentation = active_presenter.present()
            except HtermError as error:
                # The Herdr workspace exists; presentation is a warning rather than
                # falsely reporting that project creation failed.
                warnings = (error.to_dict(),)
        return WorkspaceResult(
            project.name,
            workspace_id,
            tuple(created),
            presentation,
            warnings,
        )
    except HtermError:
        if workspace_id is not None:
            lifecycle.discard(workspace_id)
            with suppress(HtermError):
                herdr.close_workspace(workspace_id)
        raise
