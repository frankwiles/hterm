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
from typing import Any, Protocol

from hterm.config import Config, LayoutTab, Project, Tab
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
class ExistingTab:
    name: str | None
    tab_id: str
    number: int
    focused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tab_id": self.tab_id,
            "number": self.number,
            "focused": self.focused,
        }


@dataclass(frozen=True, slots=True)
class FocusedWorkspace:
    workspace_id: str
    label: str | None
    checkout_path: Path | None
    repo_root: Path | None


@dataclass(frozen=True, slots=True)
class FixResult:
    workspace_id: str
    layout: str
    project: str | None
    cwd: Path
    kept: tuple[ExistingTab, ...]
    created: tuple[CreatedTab, ...]
    closed: tuple[ExistingTab, ...]
    retained_extras: tuple[ExistingTab, ...]
    missing: tuple[str | None, ...]
    focused_tab_id: str | None
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "layout": self.layout,
            "project": self.project,
            "cwd": str(self.cwd),
            "kept": [tab.to_dict() for tab in self.kept],
            "created": [tab.to_dict() for tab in self.created],
            "closed": [tab.to_dict() for tab in self.closed],
            "retained_extras": [tab.to_dict() for tab in self.retained_extras],
            "missing": list(self.missing),
            "focused_tab_id": self.focused_tab_id,
            "dry_run": self.dry_run,
        }


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


class WorkspacePresenter(Protocol):
    def present(self) -> PresentationResult: ...


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

    def _workspaces(self) -> list[Mapping[str, Any]]:
        payload = self._json("workspace", "list")
        result = payload.get("result")
        workspaces = result.get("workspaces") if isinstance(result, dict) else None
        if not isinstance(workspaces, list) or any(
            not isinstance(workspace, dict) for workspace in workspaces
        ):
            raise HtermError(
                "herdr_protocol_error", "Invalid Herdr workspace list response"
            )
        return workspaces

    def focused_workspace(self) -> FocusedWorkspace:
        focused = [
            workspace
            for workspace in self._workspaces()
            if workspace.get("focused") is True
        ]
        if not focused:
            raise HtermError(
                "focused_workspace_not_found", "No focused Herdr workspace was found"
            )
        if len(focused) != 1:
            raise HtermError(
                "herdr_protocol_error", "Herdr returned multiple focused workspaces"
            )
        workspace = focused[0]
        workspace_id = workspace.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise HtermError(
                "herdr_protocol_error", "Invalid focused Herdr workspace response"
            )
        label = workspace.get("label")
        worktree = workspace.get("worktree")

        def worktree_path(field: str) -> Path | None:
            value = worktree.get(field) if isinstance(worktree, dict) else None
            return (
                Path(value).resolve(strict=False)
                if isinstance(value, str) and value
                else None
            )

        return FocusedWorkspace(
            workspace_id,
            label if isinstance(label, str) else None,
            worktree_path("checkout_path"),
            worktree_path("repo_root"),
        )

    def workspace_with_label(self, label: str) -> str | None:
        matches: list[tuple[bool, int, str]] = []
        for workspace in self._workspaces():
            if not isinstance(workspace, dict) or workspace.get("label") != label:
                continue
            workspace_id = workspace.get("workspace_id")
            if not isinstance(workspace_id, str) or not workspace_id:
                raise HtermError(
                    "herdr_protocol_error", "Invalid Herdr workspace list response"
                )
            focused = workspace.get("focused") is True
            number = workspace.get("number")
            matches.append(
                (focused, number if isinstance(number, int) else 2**31, workspace_id)
            )

        if not matches:
            return None
        matches.sort(key=lambda item: (not item[0], item[1], item[2]))
        return matches[0][2]

    def tabs(self, workspace_id: str) -> tuple[ExistingTab, ...]:
        payload = self._json("tab", "list", "--workspace", workspace_id)
        result = payload.get("result")
        tabs = result.get("tabs") if isinstance(result, dict) else None
        if not isinstance(tabs, list):
            raise HtermError("herdr_protocol_error", "Invalid Herdr tab list response")
        parsed: list[ExistingTab] = []
        for index, tab in enumerate(tabs, start=1):
            if not isinstance(tab, dict):
                raise HtermError(
                    "herdr_protocol_error", "Invalid Herdr tab list response"
                )
            tab_id = tab.get("tab_id")
            if not isinstance(tab_id, str) or not tab_id:
                raise HtermError(
                    "herdr_protocol_error", "Invalid Herdr tab list response"
                )
            name = tab.get("label")
            number = tab.get("number")
            parsed.append(
                ExistingTab(
                    name if isinstance(name, str) else None,
                    tab_id,
                    number if isinstance(number, int) else index,
                    tab.get("focused") is True,
                )
            )
        return tuple(sorted(parsed, key=lambda tab: (tab.number, tab.tab_id)))

    def focused_pane_cwd(self, workspace_id: str) -> Path:
        payload = self._json("pane", "list", "--workspace", workspace_id)
        result = payload.get("result")
        panes = result.get("panes") if isinstance(result, dict) else None
        if not isinstance(panes, list):
            raise HtermError("herdr_protocol_error", "Invalid Herdr pane list response")
        candidates = sorted(
            (pane for pane in panes if isinstance(pane, dict)),
            key=lambda pane: (
                pane.get("focused") is not True,
                str(pane.get("pane_id", "")),
            ),
        )
        for pane in candidates:
            value = pane.get("foreground_cwd") or pane.get("cwd")
            if isinstance(value, str) and value:
                return Path(value).resolve(strict=False)
        raise HtermError(
            "workspace_cwd_not_found",
            "Unable to determine the focused workspace working directory",
            {"workspace_id": workspace_id},
        )

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

    def close_tab(self, tab_id: str) -> None:
        self._json("tab", "close", tab_id)

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


def _project_for_workspace(
    config: Config, workspace: FocusedWorkspace
) -> Project | None:
    """Match paths before labels so linked worktrees resolve to their project."""
    path_matches: list[Project] = []
    workspace_paths = {workspace.checkout_path, workspace.repo_root} - {None}
    for project in config.projects.values():
        if project.cwd in workspace_paths:
            path_matches.append(project)
    if len(path_matches) == 1:
        return path_matches[0]
    if workspace.label is not None:
        labeled = [
            project
            for project in (path_matches or list(config.projects.values()))
            if project.label == workspace.label
        ]
        if len(labeled) == 1:
            return labeled[0]
    return None


def _match_layout_tabs(
    definitions: tuple[LayoutTab, ...], existing: tuple[ExistingTab, ...]
) -> tuple[list[ExistingTab | None], list[ExistingTab]]:
    available = list(existing)
    matches: list[ExistingTab | None] = []
    for index, definition in enumerate(definitions):
        match: ExistingTab | None = None
        if definition.name is not None:
            match = next(
                (tab for tab in available if tab.name == definition.name), None
            )
        elif index < len(existing) and existing[index] in available:
            match = existing[index]
        matches.append(match)
        if match is not None:
            available.remove(match)
    return matches, available


def fix_focused_workspace(
    config: Config,
    *,
    layout_name: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    runner: ProcessRunner | None = None,
) -> FixResult:
    """Reconcile the focused workspace with a named layout."""
    selected_layout = layout_name or config.settings.fix_layout
    try:
        layout = config.layouts[selected_layout]
    except KeyError:
        raise HtermError(
            "layout_not_found",
            f"Unknown layout: {selected_layout}",
            {"layout": selected_layout, "config_path": str(config.path)},
        ) from None

    active_runner = runner or SubprocessRunner()
    herdr = HerdrClient(config.settings.herdr_binary, active_runner)
    workspace = herdr.focused_workspace()
    project = _project_for_workspace(config, workspace)
    cwd = workspace.checkout_path or (project.cwd if project is not None else None)
    if cwd is None:
        cwd = herdr.focused_pane_cwd(workspace.workspace_id)

    # Empty layouts have the same meaning as project launches with no tabs: keep
    # one interactive shell rooted at the workspace/worktree directory.
    definitions = layout.tabs or (LayoutTab(None, None, None),)
    existing = herdr.tabs(workspace.workspace_id)
    matches, extras = _match_layout_tabs(definitions, existing)
    missing_definitions = tuple(
        definition
        for definition, match in zip(definitions, matches, strict=True)
        if match is None
    )
    kept = tuple(match for match in matches if match is not None)
    to_close = tuple(extras) if force else ()
    retained = () if force else tuple(extras)

    if dry_run:
        focus_id = next(
            (
                match.tab_id
                for definition, match in zip(definitions, matches, strict=True)
                if definition.focus and match is not None
            ),
            None,
        )
        return FixResult(
            workspace.workspace_id,
            selected_layout,
            project.name if project is not None else None,
            cwd,
            kept,
            (),
            to_close,
            retained,
            tuple(definition.name for definition in missing_definitions),
            focus_id,
            dry_run=True,
        )

    created: list[CreatedTab] = []
    created_by_index: dict[int, CreatedTab] = {}
    for index, (definition, match) in enumerate(zip(definitions, matches, strict=True)):
        if match is not None:
            continue
        tab = Tab(
            definition.name,
            definition.command,
            definition.cwd or cwd,
            definition.focus,
        )
        tab_id, pane_id = herdr.create_tab(workspace.workspace_id, tab)
        created_tab = CreatedTab(tab.name, tab_id, pane_id)
        created.append(created_tab)
        created_by_index[index] = created_tab
        if tab.command:
            herdr.run_pane(pane_id, tab.command)

    focus_id: str | None = None
    for index, definition in enumerate(definitions):
        if not definition.focus:
            continue
        matched = matches[index]
        focus_id = (
            matched.tab_id if matched is not None else created_by_index[index].tab_id
        )
        break
    if focus_id is not None:
        herdr.focus(workspace.workspace_id, focus_id)
    for tab in to_close:
        herdr.close_tab(tab.tab_id)

    return FixResult(
        workspace.workspace_id,
        selected_layout,
        project.name if project is not None else None,
        cwd,
        kept,
        tuple(created),
        to_close,
        retained,
        (),
        focus_id,
    )


def orchestrate(
    config: Config,
    project: Project,
    *,
    focus: bool,
    runner: ProcessRunner | None = None,
    lifecycle: LifecycleStore | None = None,
    presenter: WorkspacePresenter | None = None,
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
                GhosttyAppleScript(
                    config.settings.ghostty_app,
                    runner,
                    herdr_binary=config.settings.herdr_binary,
                ),
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
                GhosttyAppleScript(
                    config.settings.ghostty_app,
                    runner,
                    herdr_binary=config.settings.herdr_binary,
                ),
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
