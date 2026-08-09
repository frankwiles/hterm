"""Installation support for the bundled Herdr lifecycle plugin."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from hterm.config import Config
from hterm.errors import HtermError
from hterm.process import ProcessRunner, SubprocessRunner

PLUGIN_ID = "hterm.lifecycle"
PLUGIN_ROOT = Path(__file__).parent / "herdr_plugin"


def current_hterm_executable(explicit: Path | None = None) -> Path:
    """Resolve the executable path persisted for Herdr's reduced environment."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.expanduser())
    argv_path = Path(sys.argv[0]).expanduser()
    if argv_path.name == "hterm":
        candidates.append(argv_path)
    discovered = shutil.which("hterm")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if (
            resolved.is_absolute()
            and resolved.is_file()
            and os.access(resolved, os.X_OK)
        ):
            return resolved
    raise HtermError(
        "hterm_executable_not_found",
        "Unable to find the hterm executable; pass --hterm-binary with its absolute path",
    )


def _run_herdr(
    binary: Path,
    args: tuple[str, ...],
    *,
    runner: ProcessRunner,
) -> str:
    try:
        result = runner.run((str(binary), *args), timeout=30)
    except FileNotFoundError as exc:
        raise HtermError(
            "herdr_not_found", f"Herdr executable not found: {binary}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HtermError(
            "herdr_timeout", f"Herdr plugin command timed out: {' '.join(args)}"
        ) from exc
    except OSError as exc:
        raise HtermError("herdr_command_failed", f"Unable to run Herdr: {exc}") from exc
    if result.returncode != 0:
        raise HtermError(
            "herdr_command_failed",
            result.stderr.strip() or result.stdout.strip() or "Herdr command failed",
            {"command": list(args), "exit_code": result.returncode},
        )
    return result.stdout.strip()


def install_lifecycle_plugin(
    config: Config,
    *,
    hterm_binary: Path | None = None,
    runner: ProcessRunner | None = None,
) -> dict[str, Any]:
    """Link the bundled plugin and persist the exact hterm executable path."""
    active_runner = runner or SubprocessRunner()
    executable = current_hterm_executable(hterm_binary)
    manifest = PLUGIN_ROOT / "herdr-plugin.toml"
    if not manifest.is_file():
        raise HtermError(
            "plugin_package_missing", f"Bundled Herdr plugin is missing: {manifest}"
        )

    _run_herdr(
        config.settings.herdr_binary,
        ("plugin", "link", str(PLUGIN_ROOT), "--enabled"),
        runner=active_runner,
    )
    config_dir_text = _run_herdr(
        config.settings.herdr_binary,
        ("plugin", "config-dir", PLUGIN_ID),
        runner=active_runner,
    )
    if not config_dir_text:
        raise HtermError(
            "herdr_protocol_error", "Herdr did not return the plugin config directory"
        )
    config_dir = Path(config_dir_text).expanduser().resolve(strict=False)
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        path_file = config_dir / "hterm-path"
        temporary = config_dir / f".hterm-path.{os.getpid()}.tmp"
        temporary.write_text(f"{executable}\n")
        temporary.replace(path_file)
    except OSError as exc:
        raise HtermError(
            "plugin_configuration_failed",
            f"Unable to configure the Herdr lifecycle plugin: {exc}",
            {"config_dir": str(config_dir)},
        ) from exc
    return {
        "plugin_id": PLUGIN_ID,
        "plugin_root": str(PLUGIN_ROOT),
        "plugin_config_dir": str(config_dir),
        "hterm_binary": str(executable),
    }
