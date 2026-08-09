"""Post-hook lifecycle claiming, execution, and plugin installation tests."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from typer.testing import CliRunner

from hterm.cli.app import app
from hterm.config import load_config
from hterm.lifecycle import LifecycleStore, workspace_closed
from hterm.plugin import PLUGIN_ID, install_lifecycle_plugin
from hterm.process import ProcessResult


class FakeRunner:
    def __init__(self, responses: list[ProcessResult | BaseException]) -> None:
        self.responses = responses
        self.calls: list[
            tuple[tuple[str, ...], Path | None, Mapping[str, str] | None, float | None]
        ] = []

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
        del args, cwd, env


def config_with_hook(tmp_path: Path, hook: str | None = "echo closed"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    hook_line = f'post_hook = "{hook}"' if hook is not None else ""
    config_path = tmp_path / "hterm.toml"
    config_path.write_text(
        f'''version = 1
default = "demo"
[settings]
herdr_binary = "/fake/herdr"
hook_shell = "/bin/zsh"
hook_timeout_seconds = 12
[projects.demo]
cwd = "{project_dir}"
{hook_line}
'''
    )
    return load_config(config_path)


def pending_store(tmp_path: Path, hook: str | None = "echo closed"):
    config = config_with_hook(tmp_path, hook)
    store = LifecycleStore(tmp_path / "state")
    store.write(
        "w1",
        config=config,
        project=config.resolve(),
        tab_id="w1:t1",
        pane_id="w1:p1",
    )
    return config, store


def test_post_hook_runs_once_with_complete_environment_and_records_output(
    tmp_path: Path,
) -> None:
    config, store = pending_store(tmp_path)
    runner = FakeRunner([ProcessResult((), 0, "hook output\n", "hook warning\n")])

    first = workspace_closed("w1", store=store, runner=runner)
    duplicate = workspace_closed("w1", store=store, runner=runner)

    assert first.status == "succeeded"
    assert first.claimed is True
    assert duplicate.status == "succeeded"
    assert duplicate.claimed is False
    assert len(runner.calls) == 1
    command, cwd, environment, timeout = runner.calls[0]
    assert command == ("/bin/zsh", "-lc", "echo closed")
    assert cwd == config.resolve().cwd
    assert timeout == 12
    assert environment is not None
    assert {
        key: environment[key]
        for key in (
            "HTERM_PROJECT",
            "HTERM_PROJECT_DIR",
            "HTERM_CONFIG_PATH",
            "HTERM_WORKSPACE_ID",
            "HTERM_TAB_ID",
            "HTERM_PANE_ID",
        )
    } == {
        "HTERM_PROJECT": "demo",
        "HTERM_PROJECT_DIR": str(config.resolve().cwd),
        "HTERM_CONFIG_PATH": str(config.path),
        "HTERM_WORKSPACE_ID": "w1",
        "HTERM_TAB_ID": "w1:t1",
        "HTERM_PANE_ID": "w1:p1",
    }
    record = json.loads((store.root / "w1.json").read_text())
    assert record["status"] == "succeeded"
    assert record["exit_code"] == 0
    assert record["stdout"] == "hook output\n"
    assert record["stderr"] == "hook warning\n"
    assert (store.root / "w1.claim").exists()


def test_failed_and_timed_out_hooks_are_terminal_and_not_retried(
    tmp_path: Path,
) -> None:
    _, failed_store = pending_store(tmp_path / "failed")
    failed_runner = FakeRunner([ProcessResult((), 7, "", "bad hook")])
    failed = workspace_closed("w1", store=failed_store, runner=failed_runner)
    assert failed.status == "failed"
    failed_record = failed_store.read("w1")
    assert failed_record is not None
    assert failed_record["error"]["code"] == "post_hook_failed"
    assert (
        workspace_closed("w1", store=failed_store, runner=failed_runner).claimed
        is False
    )

    _, timeout_store = pending_store(tmp_path / "timeout")
    timeout_runner = FakeRunner([subprocess.TimeoutExpired(("zsh",), 12)])
    timed_out = workspace_closed("w1", store=timeout_store, runner=timeout_runner)
    assert timed_out.status == "timed_out"
    timeout_record = timeout_store.read("w1")
    assert timeout_record is not None
    assert timeout_record["error"]["code"] == "post_hook_timeout"


def test_no_hook_foreign_workspace_and_canceled_record_are_ignored(
    tmp_path: Path,
) -> None:
    _, store = pending_store(tmp_path, None)
    runner = FakeRunner([])
    assert workspace_closed("w1", store=store, runner=runner).status == "skipped"
    assert workspace_closed("foreign", store=store, runner=runner).status == "missing"

    record = store.read("w1")
    assert record is not None
    record["status"] = "canceled"
    (store.root / "w2.json").write_text(
        json.dumps({**record, "workspace_id": "w2"}) + "\n"
    )
    canceled = workspace_closed("w2", store=store, runner=runner)
    assert canceled.status == "canceled"
    assert canceled.claimed is False


def test_invalid_claimed_record_is_recorded_as_failed(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    (root / "w1.json").write_text(
        json.dumps({"version": 99, "workspace_id": "w1", "status": "pending"})
    )
    result = workspace_closed("w1", store=LifecycleStore(root), runner=FakeRunner([]))
    assert result.status == "failed"
    record = json.loads((root / "w1.json").read_text())
    assert record["error"]["code"] == "lifecycle_record_invalid"


def test_workspace_closed_cli_uses_success_envelope_for_untracked_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    result = CliRunner().invoke(
        app, ["lifecycle", "workspace-closed", "other", "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "missing"


def test_install_plugin_links_and_writes_absolute_hterm_path(tmp_path: Path) -> None:
    config = config_with_hook(tmp_path)
    executable = tmp_path / "bin" / "hterm"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    plugin_config = tmp_path / "plugin-config"
    runner = FakeRunner(
        [
            ProcessResult((), 0, "linked\n", ""),
            ProcessResult((), 0, f"{plugin_config}\n", ""),
        ]
    )

    installed = install_lifecycle_plugin(config, hterm_binary=executable, runner=runner)

    assert installed["plugin_id"] == PLUGIN_ID
    assert (plugin_config / "hterm-path").read_text() == f"{executable.resolve()}\n"
    assert runner.calls[0][0][0:3] == (
        "/fake/herdr",
        "plugin",
        "link",
    )
    assert runner.calls[1][0] == (
        "/fake/herdr",
        "plugin",
        "config-dir",
        PLUGIN_ID,
    )


def test_plugin_adapter_delegates_event_workspace_id(tmp_path: Path) -> None:
    config_dir = tmp_path / "plugin-config"
    config_dir.mkdir()
    args_file = tmp_path / "args"
    executable = tmp_path / "hterm"
    executable.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{args_file}"\n')
    executable.chmod(0o755)
    (config_dir / "hterm-path").write_text(f"{executable}\n")
    script = Path(__file__).parents[1] / "src/hterm/herdr_plugin/workspace-closed.sh"

    completed = subprocess.run(
        ("/bin/sh", str(script)),
        env={
            **os.environ,
            "HERDR_WORKSPACE_ID": "w42",
            "HERDR_PLUGIN_CONFIG_DIR": str(config_dir),
        },
        check=False,
    )

    assert completed.returncode == 0
    assert args_file.read_text().splitlines() == [
        "lifecycle",
        "workspace-closed",
        "w42",
        "--json",
    ]
