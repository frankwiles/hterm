"""Durable, exactly-once workspace-close post-hook handling."""

from __future__ import annotations

import json
import os
import re
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hterm.config import Config, Project
from hterm.errors import HtermError
from hterm.process import ProcessRunner, SubprocessRunner

_ID_PATTERN = re.compile(r"^[A-Za-z0-9:_-]+$")
_RECORD_VERSION = 1


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _state_root() -> Path:
    state_home = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return state_home / "hterm" / "workspaces"


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
        temporary.replace(path)
    except OSError as exc:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise HtermError(
            "lifecycle_state_failed",
            f"Unable to persist workspace lifecycle state: {exc}",
            {"path": str(path)},
        ) from exc


def _valid_id(workspace_id: str) -> bool:
    return bool(_ID_PATTERN.fullmatch(workspace_id))


class LifecycleStore:
    """Persist launch records and atomically claim workspace-close events."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _state_root()

    def _path(self, workspace_id: str) -> Path:
        if not _valid_id(workspace_id):
            raise HtermError("invalid_workspace_id", "Invalid workspace lifecycle ID")
        return self.root / f"{workspace_id}.json"

    def write(
        self,
        workspace_id: str,
        *,
        config: Config,
        project: Project,
        tab_id: str,
        pane_id: str,
    ) -> None:
        if not _valid_id(workspace_id):
            raise HtermError(
                "herdr_protocol_error", "Herdr returned an invalid workspace ID"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _RECORD_VERSION,
            "workspace_id": workspace_id,
            "project": project.name,
            "project_dir": str(project.cwd),
            "config_path": str(config.path),
            "post_hook": project.post_hook,
            "hook_shell": str(config.settings.hook_shell),
            "hook_timeout_seconds": config.settings.hook_timeout_seconds,
            "tab_id": tab_id,
            "pane_id": pane_id,
            "status": "pending",
            "created_at": _timestamp(),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_json_write(self._path(workspace_id), payload)

    def discard(self, workspace_id: str) -> None:
        """Remove an uncommitted launch record before rollback closure."""
        # Rollback must preserve the original orchestration failure.
        with suppress(HtermError, OSError):
            self._path(workspace_id).unlink(missing_ok=True)

    def read(self, workspace_id: str) -> dict[str, Any] | None:
        path = self._path(workspace_id)
        try:
            text = path.read_text()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise HtermError(
                "lifecycle_state_failed",
                f"Unable to read workspace lifecycle state: {exc}",
                {"workspace_id": workspace_id, "path": str(path)},
            ) from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HtermError(
                "lifecycle_record_invalid",
                "Workspace lifecycle record contains malformed JSON",
                {"workspace_id": workspace_id, "path": str(path)},
            ) from exc
        if not isinstance(payload, dict):
            raise HtermError(
                "lifecycle_record_invalid",
                "Workspace lifecycle record must be a JSON object",
                {"workspace_id": workspace_id, "path": str(path)},
            )
        return payload

    def claim(self, workspace_id: str) -> dict[str, Any] | None:
        """Claim a pending record once using an O_EXCL marker.

        The marker remains after completion. If the process crashes after claiming,
        a duplicate event therefore cannot rerun a hook whose outcome is unknown.
        """
        record = self.read(workspace_id)
        if record is None or record.get("status") != "pending":
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / f"{workspace_id}.claim"
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return None
        except OSError as exc:
            raise HtermError(
                "lifecycle_state_failed",
                f"Unable to claim workspace lifecycle state: {exc}",
                {"workspace_id": workspace_id, "path": str(marker)},
            ) from exc
        with os.fdopen(descriptor, "w") as claimed:
            claimed.write(f"claimed_at={_timestamp()}\npid={os.getpid()}\n")
        record["status"] = "running"
        record["claimed_at"] = _timestamp()
        _atomic_json_write(self._path(workspace_id), record)
        return record

    def finish(self, workspace_id: str, record: dict[str, Any]) -> None:
        record["completed_at"] = _timestamp()
        _atomic_json_write(self._path(workspace_id), record)


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    workspace_id: str
    status: str
    claimed: bool
    record_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "status": self.status,
            "claimed": self.claimed,
            "record_path": str(self.record_path),
        }


def _require_record(record: dict[str, Any], workspace_id: str) -> None:
    required_strings = (
        "workspace_id",
        "project",
        "project_dir",
        "config_path",
        "hook_shell",
        "tab_id",
        "pane_id",
    )
    valid = (
        record.get("version") == _RECORD_VERSION
        and record.get("workspace_id") == workspace_id
        and all(isinstance(record.get(key), str) for key in required_strings)
        and isinstance(record.get("hook_timeout_seconds"), (int, float))
        and record["hook_timeout_seconds"] > 0
        and (
            record.get("post_hook") is None or isinstance(record.get("post_hook"), str)
        )
    )
    if not valid:
        raise HtermError(
            "lifecycle_record_invalid",
            "Workspace lifecycle record has an invalid schema",
            {"workspace_id": workspace_id},
        )


def _hook_environment(record: dict[str, Any]) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HTERM_PROJECT": record["project"],
            "HTERM_PROJECT_DIR": record["project_dir"],
            "HTERM_CONFIG_PATH": record["config_path"],
            "HTERM_WORKSPACE_ID": record["workspace_id"],
            "HTERM_TAB_ID": record["tab_id"],
            "HTERM_PANE_ID": record["pane_id"],
        }
    )
    return environment


def workspace_closed(
    workspace_id: str,
    *,
    store: LifecycleStore | None = None,
    runner: ProcessRunner | None = None,
) -> LifecycleResult:
    """Claim and execute a post-hook, recording every terminal outcome."""
    store = store or LifecycleStore()
    path = store._path(workspace_id)
    existing = store.read(workspace_id)
    if existing is None:
        return LifecycleResult(workspace_id, "missing", False, path)
    status = existing.get("status")
    if status != "pending":
        return LifecycleResult(workspace_id, str(status or "invalid"), False, path)

    record = store.claim(workspace_id)
    if record is None:
        current = store.read(workspace_id)
        current_status = current.get("status") if current else "claimed"
        return LifecycleResult(workspace_id, str(current_status), False, path)

    try:
        _require_record(record, workspace_id)
    except HtermError as error:
        record.update(
            status="failed",
            error=error.to_dict(),
            stdout="",
            stderr="",
            exit_code=None,
        )
        store.finish(workspace_id, record)
        return LifecycleResult(workspace_id, "failed", True, path)

    command = record.get("post_hook")
    if command is None:
        record.update(status="skipped", stdout="", stderr="", exit_code=0)
        store.finish(workspace_id, record)
        return LifecycleResult(workspace_id, "skipped", True, path)

    active_runner = runner or SubprocessRunner()
    try:
        result = active_runner.run(
            (record["hook_shell"], "-lc", command),
            cwd=Path(record["project_dir"]),
            env=_hook_environment(record),
            timeout=float(record["hook_timeout_seconds"]),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr
        record.update(
            status="timed_out",
            stdout=stdout or "",
            stderr=stderr or "",
            exit_code=None,
            error={"code": "post_hook_timeout", "message": "Post hook timed out"},
        )
    except (FileNotFoundError, OSError) as exc:
        record.update(
            status="failed",
            stdout="",
            stderr="",
            exit_code=None,
            error={"code": "post_hook_failed", "message": str(exc)},
        )
    else:
        status = "succeeded" if result.returncode == 0 else "failed"
        record.update(
            status=status,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
        if result.returncode != 0:
            record["error"] = {
                "code": "post_hook_failed",
                "message": result.stderr.strip()
                or result.stdout.strip()
                or "Post hook failed",
            }
    store.finish(workspace_id, record)
    return LifecycleResult(workspace_id, record["status"], True, path)
