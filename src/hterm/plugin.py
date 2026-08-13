"""Installation support for the bundled Herdr plugins."""

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
FINDER_PLUGIN_ID = "hterm.finder"
FINDER_PLUGIN_ROOT = Path(__file__).parent / "finder_plugin"

FINDER_KEYBINDING = """[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "hterm.finder.open"
description = "find an hterm project"
"""


def _current_executable(
    name: str,
    explicit: Path | None,
    *,
    candidates: tuple[Path, ...] = (),
) -> Path:
    paths = ([explicit.expanduser()] if explicit is not None else []) + list(candidates)
    discovered = shutil.which(name)
    if discovered:
        paths.append(Path(discovered))
    for candidate in paths:
        resolved = candidate.resolve(strict=False)
        if (
            resolved.is_absolute()
            and resolved.is_file()
            and os.access(resolved, os.X_OK)
        ):
            return resolved
    option = f"--{name}-binary"
    raise HtermError(
        f"{name}_executable_not_found",
        f"Unable to find the {name} executable; pass {option} with its absolute path",
    )


def current_hterm_executable(explicit: Path | None = None) -> Path:
    """Resolve the executable path persisted for Herdr's reduced environment."""
    argv_path = Path(sys.argv[0]).expanduser()
    argv_candidates = (argv_path,) if argv_path.name == "hterm" else ()
    return _current_executable("hterm", explicit, candidates=argv_candidates)


def current_fzf_executable(explicit: Path | None = None) -> Path:
    """Resolve fzf now so the plugin does not depend on Herdr's PATH."""
    return _current_executable("fzf", explicit)


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


def _install_plugin(
    config: Config,
    *,
    plugin_id: str,
    plugin_root: Path,
    files: dict[str, str],
    runner: ProcessRunner,
) -> dict[str, Any]:
    manifest = plugin_root / "herdr-plugin.toml"
    if not manifest.is_file():
        raise HtermError(
            "plugin_package_missing", f"Bundled Herdr plugin is missing: {manifest}"
        )

    _run_herdr(
        config.settings.herdr_binary,
        ("plugin", "link", str(plugin_root)),
        runner=runner,
    )
    config_dir_text = _run_herdr(
        config.settings.herdr_binary,
        ("plugin", "config-dir", plugin_id),
        runner=runner,
    )
    if not config_dir_text:
        raise HtermError(
            "herdr_protocol_error", "Herdr did not return the plugin config directory"
        )
    config_dir = Path(config_dir_text).expanduser().resolve(strict=False)
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        for name, contents in files.items():
            destination = config_dir / name
            temporary = config_dir / f".{name}.{os.getpid()}.tmp"
            temporary.write_text(contents)
            temporary.replace(destination)
    except OSError as exc:
        raise HtermError(
            "plugin_configuration_failed",
            f"Unable to configure the Herdr plugin: {exc}",
            {"plugin_id": plugin_id, "config_dir": str(config_dir)},
        ) from exc
    return {
        "plugin_id": plugin_id,
        "plugin_root": str(plugin_root),
        "plugin_config_dir": str(config_dir),
    }


def install_lifecycle_plugin(
    config: Config,
    *,
    hterm_binary: Path | None = None,
    runner: ProcessRunner | None = None,
) -> dict[str, Any]:
    """Link the lifecycle plugin and persist the exact hterm executable path."""
    executable = current_hterm_executable(hterm_binary)
    result = _install_plugin(
        config,
        plugin_id=PLUGIN_ID,
        plugin_root=PLUGIN_ROOT,
        files={"hterm-path": f"{executable}\n"},
        runner=runner or SubprocessRunner(),
    )
    result["hterm_binary"] = str(executable)
    return result


def install_finder_plugin(
    config: Config,
    *,
    hterm_binary: Path | None = None,
    fzf_binary: Path | None = None,
    runner: ProcessRunner | None = None,
) -> dict[str, Any]:
    """Link the fzf popup plugin and persist every external path it needs."""
    executable = current_hterm_executable(hterm_binary)
    fzf = current_fzf_executable(fzf_binary)
    result = _install_plugin(
        config,
        plugin_id=FINDER_PLUGIN_ID,
        plugin_root=FINDER_PLUGIN_ROOT,
        files={
            "hterm-path": f"{executable}\n",
            "fzf-path": f"{fzf}\n",
            "config-path": f"{config.path}\n",
        },
        runner=runner or SubprocessRunner(),
    )
    result["hterm_binary"] = str(executable)
    result["fzf_binary"] = str(fzf)
    result["config_path"] = str(config.path)
    result["keybinding"] = FINDER_KEYBINDING
    return result
