"""Herdr orchestration tests using an injectable fake process boundary."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from hterm.config import load_config
from hterm.errors import HtermError
from hterm.orchestration import (
    HerdrClient,
    LifecycleStore,
    fix_focused_workspace,
    orchestrate,
)
from hterm.presentation import GhosttyPresenter, PresentationResult
from hterm.process import ProcessResult


class FakeRunner:
    def __init__(self, responses: list[ProcessResult | BaseException]) -> None:
        self.responses = responses
        self.calls: list[
            tuple[tuple[str, ...], Path | None, Mapping[str, str] | None, float | None]
        ] = []
        self.started: list[tuple[str, ...]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        self.calls.append((tuple(args), cwd, env, timeout))
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
        del cwd, env
        self.started.append(tuple(args))


def response(payload: Any, returncode: int = 0, stderr: str = "") -> ProcessResult:
    return ProcessResult((), returncode, json.dumps(payload), stderr)


def server_running() -> ProcessResult:
    return response(
        {"sessions": [{"name": "default", "default": True, "running": True}]}
    )


def no_workspaces() -> ProcessResult:
    return response({"result": {"type": "workspace_list", "workspaces": []}})


def workspaces(*items: dict[str, Any]) -> ProcessResult:
    return response({"result": {"type": "workspace_list", "workspaces": list(items)}})


def focused_workspace(
    *,
    workspace: str = "w1",
    label: str = "Demo",
    checkout_path: Path | None = None,
    repo_root: Path | None = None,
) -> ProcessResult:
    item: dict[str, Any] = {
        "workspace_id": workspace,
        "label": label,
        "number": 1,
        "focused": True,
    }
    if checkout_path is not None:
        item["worktree"] = {
            "checkout_path": str(checkout_path),
            "repo_root": str(repo_root or checkout_path),
        }
    return workspaces(item)


def tabs(*items: dict[str, Any]) -> ProcessResult:
    return response({"result": {"type": "tab_list", "tabs": list(items)}})


def workspace_created(workspace: str = "w1") -> ProcessResult:
    return response(
        {
            "result": {
                "workspace": {"workspace_id": workspace},
                "tab": {"workspace_id": workspace, "tab_id": f"{workspace}:t1"},
                "root_pane": {
                    "workspace_id": workspace,
                    "pane_id": f"{workspace}:p1",
                },
            }
        }
    )


def tab_created(workspace: str = "w1") -> ProcessResult:
    return response(
        {
            "result": {
                "tab": {"workspace_id": workspace, "tab_id": f"{workspace}:t2"},
                "root_pane": {
                    "workspace_id": workspace,
                    "pane_id": f"{workspace}:p2",
                },
            }
        }
    )


def ok() -> ProcessResult:
    return response({"result": {"type": "ok"}})


def make_config(tmp_path: Path, body: str):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    path = tmp_path / "hterm.toml"
    path.write_text(
        f'''version = 1
default = "demo"
[settings]
herdr_binary = "/fake/herdr"
hook_shell = "/bin/zsh"
focus = true
[projects.demo]
cwd = "{project}"
label = "Demo"
{body}
'''
    )
    return load_config(path)


def make_fix_config(tmp_path: Path) -> tuple[Any, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    checkout = tmp_path / "worktree"
    checkout.mkdir()
    path = tmp_path / "hterm.toml"
    path.write_text(
        f'''version = 1
default = "demo"
[settings]
herdr_binary = "/fake/herdr"
fix_layout = "coding"

[[layouts.coding.tabs]]
name = "code"
command = "pi"
focus = true

[[layouts.coding.tabs]]
name = "shell"

[projects.demo]
cwd = "{project}"
label = "Demo"
'''
    )
    return load_config(path), project, checkout


def test_fix_preserves_matching_tabs_and_uses_linked_worktree_cwd(
    tmp_path: Path,
) -> None:
    config, project, checkout = make_fix_config(tmp_path)
    runner = FakeRunner(
        [
            focused_workspace(checkout_path=checkout, repo_root=project),
            tabs(
                {
                    "tab_id": "w1:t1",
                    "label": "code",
                    "number": 1,
                    "focused": True,
                },
                {"tab_id": "w1:t9", "label": "logs", "number": 2},
            ),
            tab_created(),
            ok(),
        ]
    )

    result = fix_focused_workspace(config, runner=runner)

    assert result.project == "demo"
    assert result.cwd == checkout
    assert [tab.tab_id for tab in result.kept] == ["w1:t1"]
    assert [tab.name for tab in result.created] == ["shell"]
    assert [tab.tab_id for tab in result.retained_extras] == ["w1:t9"]
    assert result.closed == ()
    assert [call[0][1:] for call in runner.calls] == [
        ("workspace", "list"),
        ("tab", "list", "--workspace", "w1"),
        (
            "tab",
            "create",
            "--workspace",
            "w1",
            "--cwd",
            str(checkout),
            "--no-focus",
            "--label",
            "shell",
        ),
        ("tab", "focus", "w1:t1"),
    ]


def test_fix_force_closes_extras_and_focuses_configured_tab(tmp_path: Path) -> None:
    config, project, checkout = make_fix_config(tmp_path)
    runner = FakeRunner(
        [
            focused_workspace(checkout_path=checkout, repo_root=project),
            tabs(
                {"tab_id": "w1:t1", "label": "code", "number": 1},
                {"tab_id": "w1:t2", "label": "shell", "number": 2},
                {"tab_id": "w1:t3", "label": "extra", "number": 3},
            ),
            ok(),
            ok(),
        ]
    )

    result = fix_focused_workspace(config, force=True, runner=runner)

    assert result.created == ()
    assert [tab.tab_id for tab in result.closed] == ["w1:t3"]
    assert result.focused_tab_id == "w1:t1"
    assert [call[0][1:] for call in runner.calls[-2:]] == [
        ("tab", "focus", "w1:t1"),
        ("tab", "close", "w1:t3"),
    ]


def test_fix_dry_run_reports_plan_without_mutating(tmp_path: Path) -> None:
    config, project, checkout = make_fix_config(tmp_path)
    runner = FakeRunner(
        [
            focused_workspace(checkout_path=checkout, repo_root=project),
            tabs({"tab_id": "w1:t9", "label": "extra", "number": 1}),
        ]
    )

    result = fix_focused_workspace(config, force=True, dry_run=True, runner=runner)

    assert result.dry_run is True
    assert result.missing == ("code", "shell")
    assert [tab.tab_id for tab in result.closed] == ["w1:t9"]
    assert len(runner.calls) == 2


def test_fix_rejects_unknown_layout_before_contacting_herdr(tmp_path: Path) -> None:
    config, _, _ = make_fix_config(tmp_path)
    runner = FakeRunner([])

    with pytest.raises(HtermError) as raised:
        fix_focused_workspace(config, layout_name="ops", runner=runner)

    assert raised.value.code == "layout_not_found"
    assert runner.calls == []


def test_success_configures_tabs_hooks_focus_and_lifecycle(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        """pre_hook = "echo pre"
setup_hook = "echo setup"
post_hook = "echo post"
[[projects.demo.tabs]]
name = "code"
command = "pi"
[[projects.demo.tabs]]
name = "server"
command = "uv run server"
focus = true
""",
    )
    runner = FakeRunner(
        [
            server_running(),
            no_workspaces(),
            ProcessResult((), 0, "pre output", ""),
            workspace_created(),
            ok(),
            ok(),
            tab_created(),
            ok(),
            ProcessResult((), 0, "setup output", ""),
            ok(),
            response(
                [
                    {
                        "window-id": 42,
                        "app-bundle-id": "com.mitchellh.ghostty",
                        "window-title": "herdr — Demo",
                        "workspace": "T",
                        "workspace-is-visible": True,
                        "workspace-is-focused": False,
                    }
                ]
            ),
            ok(),
        ]
    )
    store = LifecycleStore(tmp_path / "state")

    result = orchestrate(
        config, config.resolve(), focus=True, runner=runner, lifecycle=store
    )

    assert result.workspace_id == "w1"
    assert [tab.tab_id for tab in result.tabs] == ["w1:t1", "w1:t2"]
    commands = [call[0][1:] for call in runner.calls]
    assert (
        "workspace",
        "create",
        "--cwd",
        str(tmp_path / "project"),
        "--label",
        "Demo",
        "--no-focus",
    ) in commands
    assert ("tab", "rename", "w1:t1", "code") in commands
    assert ("pane", "run", "w1:p1", "pi") in commands
    assert ("pane", "run", "w1:p2", "uv run server") in commands
    assert ("tab", "focus", "w1:t2") in commands
    assert result.presentation is not None
    assert result.presentation.ghostty_window_id == 42
    assert result.presentation.created is False

    pre_call = runner.calls[2]
    setup_call = runner.calls[-4]
    assert pre_call[1] == tmp_path / "project"
    assert pre_call[2] is not None
    assert setup_call[2] is not None
    assert pre_call[2]["HTERM_PROJECT"] == "demo"
    assert "HTERM_WORKSPACE_ID" not in pre_call[2]
    assert setup_call[2]["HTERM_WORKSPACE_ID"] == "w1"

    lifecycle = json.loads((tmp_path / "state" / "w1.json").read_text())
    assert lifecycle["post_hook"] == "echo post"
    assert lifecycle["status"] == "pending"


@pytest.mark.parametrize(
    "stopped",
    [
        response(
            {"sessions": [{"name": "default", "default": True, "running": False}]}
        ),
        response(
            {
                "ok": False,
                "error": {
                    "code": "server_not_running",
                    "message": "the default server is not running",
                },
            },
            returncode=1,
        ),
    ],
)
def test_absent_server_is_started_without_creating_workspace(
    stopped: ProcessResult,
) -> None:
    runner = FakeRunner([stopped, server_running()])
    client = HerdrClient(
        Path("/fake/herdr"), runner, sleep=lambda _seconds: None, server_timeout=1
    )

    client.ensure_server()

    assert runner.started == [("/fake/herdr", "server")]
    assert all("workspace" not in call[0] for call in runner.calls)


def test_existing_workspace_with_project_label_is_focused_and_reused(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, 'pre_hook = "must not run"')
    runner = FakeRunner(
        [
            server_running(),
            workspaces(
                {
                    "workspace_id": "w7",
                    "label": "Demo",
                    "number": 2,
                    "focused": False,
                }
            ),
            ok(),
        ]
    )

    class Presenter:
        def __init__(self) -> None:
            self.calls = 0

        def present(self) -> PresentationResult:
            self.calls += 1
            return PresentationResult(42, "T", focused=True)

    presenter = Presenter()
    result = orchestrate(
        config,
        config.resolve(),
        focus=True,
        runner=runner,
        lifecycle=LifecycleStore(tmp_path / "state"),
        presenter=presenter,  # type: ignore[arg-type]
    )

    assert result.workspace_id == "w7"
    assert result.tabs == ()
    assert result.reused is True
    assert result.to_dict()["reused"] is True
    assert presenter.calls == 1
    assert [call[0][1:] for call in runner.calls] == [
        ("session", "list", "--json"),
        ("workspace", "list"),
        ("workspace", "focus", "w7"),
    ]
    assert not (tmp_path / "state" / "w7.json").exists()


def test_existing_workspace_is_not_focused_with_no_focus(tmp_path: Path) -> None:
    config = make_config(tmp_path, "")
    runner = FakeRunner(
        [
            server_running(),
            workspaces({"workspace_id": "w7", "label": "Demo", "number": 1}),
        ]
    )

    result = orchestrate(config, config.resolve(), focus=False, runner=runner)

    assert result.reused is True
    assert len(runner.calls) == 2


def test_orchestration_creates_when_no_matching_workspace(tmp_path: Path) -> None:
    config = make_config(tmp_path, "")
    runner = FakeRunner(
        [
            server_running(),
            no_workspaces(),
            workspace_created("w1"),
            server_running(),
            no_workspaces(),
            workspace_created("w2"),
        ]
    )
    store = LifecycleStore(tmp_path / "state")

    first = orchestrate(
        config, config.resolve(), focus=False, runner=runner, lifecycle=store
    )
    second = orchestrate(
        config, config.resolve(), focus=False, runner=runner, lifecycle=store
    )

    assert first.workspace_id == "w1"
    assert first.reused is False
    assert second.workspace_id == "w2"
    assert sum(call[0][1:3] == ("workspace", "create") for call in runner.calls) == 2


def test_no_tabs_keeps_one_shell_tab_without_focus(tmp_path: Path) -> None:
    config = make_config(tmp_path, "")
    runner = FakeRunner([server_running(), no_workspaces(), workspace_created("w2")])

    result = orchestrate(
        config,
        config.resolve(),
        focus=False,
        runner=runner,
        lifecycle=LifecycleStore(tmp_path / "state"),
    )

    assert len(result.tabs) == 1
    assert result.tabs[0].name is None
    assert len(runner.calls) == 3


def test_failed_pre_hook_creates_no_workspace(tmp_path: Path) -> None:
    config = make_config(tmp_path, 'pre_hook = "false"')
    runner = FakeRunner(
        [
            server_running(),
            no_workspaces(),
            ProcessResult((), 7, "", "VPN is not connected"),
        ]
    )

    with pytest.raises(HtermError) as raised:
        orchestrate(config, config.resolve(), focus=True, runner=runner)

    assert raised.value.code == "pre_hook_failed"
    assert raised.value.details["exit_code"] == 7
    assert len(runner.calls) == 3
    assert all(call[0][1:3] != ("workspace", "create") for call in runner.calls)


def test_tab_failure_rolls_back_and_discards_lifecycle(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        """[[projects.demo.tabs]]
name = "one"
[[projects.demo.tabs]]
name = "two"
""",
    )
    runner = FakeRunner(
        [
            server_running(),
            no_workspaces(),
            workspace_created(),
            ok(),
            ProcessResult((), 1, "", "cannot create tab"),
            ok(),
        ]
    )
    store = LifecycleStore(tmp_path / "state")

    with pytest.raises(HtermError, match="cannot create tab"):
        orchestrate(
            config, config.resolve(), focus=True, runner=runner, lifecycle=store
        )

    assert runner.calls[-1][0][1:] == ("workspace", "close", "w1")
    assert not (tmp_path / "state" / "w1.json").exists()


def test_setup_failure_rolls_back_without_post_hook(tmp_path: Path) -> None:
    config = make_config(
        tmp_path, 'setup_hook = "false"\npost_hook = "echo must-not-run"'
    )
    runner = FakeRunner(
        [
            server_running(),
            no_workspaces(),
            workspace_created(),
            ProcessResult((), 1, "", "setup failed"),
            ok(),
        ]
    )

    with pytest.raises(HtermError) as raised:
        orchestrate(
            config,
            config.resolve(),
            focus=True,
            runner=runner,
            lifecycle=LifecycleStore(tmp_path / "state"),
        )

    assert raised.value.code == "setup_hook_failed"
    assert all("must-not-run" not in call[0] for call in runner.calls)
    assert runner.calls[-1][0][1:] == ("workspace", "close", "w1")


def test_presentation_failure_is_a_structured_warning(tmp_path: Path) -> None:
    config = make_config(tmp_path, "")
    runner = FakeRunner([server_running(), no_workspaces(), workspace_created(), ok()])

    class NoWindows:
        def windows(self):
            return ()

        def focus(self, window_id: int):
            raise AssertionError(window_id)

    class DeniedCreator:
        def create_attached_window(self):
            raise HtermError(
                "ghostty_automation_denied", "Automation permission was denied"
            )

    result = orchestrate(
        config,
        config.resolve(),
        focus=True,
        runner=runner,
        lifecycle=LifecycleStore(tmp_path / "state"),
        presenter=GhosttyPresenter(NoWindows(), DeniedCreator()),
    )

    assert result.workspace_id == "w1"
    assert result.presentation is None
    assert result.to_dict()["warnings"] == [
        {
            "code": "ghostty_automation_denied",
            "message": "Automation permission was denied",
        }
    ]


@pytest.mark.parametrize(
    "bad_response",
    [response({"result": {}}), ProcessResult((), 0, "not json", "")],
)
def test_malformed_create_response_is_protocol_error(
    tmp_path: Path, bad_response: ProcessResult
) -> None:
    config = make_config(tmp_path, "")
    runner = FakeRunner([server_running(), no_workspaces(), bad_response])

    with pytest.raises(HtermError) as raised:
        orchestrate(config, config.resolve(), focus=False, runner=runner)

    assert raised.value.code == "herdr_protocol_error"


def test_pane_run_accepts_herdr_empty_success_response() -> None:
    runner = FakeRunner([ProcessResult((), 0, "", "")])

    HerdrClient(Path("/fake/herdr"), runner).run_pane("w1:p1", "nvim")

    assert runner.calls[0][0] == ("/fake/herdr", "pane", "run", "w1:p1", "nvim")


def test_missing_herdr_binary_is_normalized(tmp_path: Path) -> None:
    config = make_config(tmp_path, "")
    runner = FakeRunner([FileNotFoundError("missing")])

    with pytest.raises(HtermError) as raised:
        orchestrate(config, config.resolve(), focus=False, runner=runner)

    assert raised.value.code == "herdr_not_found"


def test_protocol_mismatch_preserves_external_diagnostic() -> None:
    runner = FakeRunner(
        [
            response(
                {
                    "ok": False,
                    "error": {
                        "code": "protocol_mismatch",
                        "message": "client and server protocols differ",
                    },
                },
                returncode=1,
            )
        ]
    )

    with pytest.raises(HtermError) as raised:
        HerdrClient(Path("/fake/herdr"), runner).ensure_server()

    assert raised.value.code == "herdr_protocol_mismatch"
    assert raised.value.details["herdr_code"] == "protocol_mismatch"
    assert str(raised.value) == "client and server protocols differ"
    assert runner.started == []


def test_server_start_timeout_is_normalized() -> None:
    runner = FakeRunner([response({"sessions": [{"default": True, "running": False}]})])
    client = HerdrClient(Path("/fake/herdr"), runner, server_timeout=0)

    with pytest.raises(HtermError) as raised:
        client.ensure_server()

    assert raised.value.code == "herdr_server_timeout"
    assert runner.started == [("/fake/herdr", "server")]


def test_signaled_herdr_command_is_failure() -> None:
    runner = FakeRunner([ProcessResult((), -15, "", "")])

    with pytest.raises(HtermError) as raised:
        HerdrClient(Path("/fake/herdr"), runner).ensure_server()

    assert raised.value.code == "herdr_command_failed"
    assert raised.value.details["exit_code"] == -15


def test_hook_timeout_is_normalized(tmp_path: Path) -> None:
    config = make_config(tmp_path, 'pre_hook = "sleep 100"')
    runner = FakeRunner(
        [
            server_running(),
            no_workspaces(),
            subprocess.TimeoutExpired(("/bin/zsh", "-lc", "sleep 100"), 60),
        ]
    )

    with pytest.raises(HtermError) as raised:
        orchestrate(config, config.resolve(), focus=False, runner=runner)

    assert raised.value.code == "pre_hook_timeout"
