"""Versioned TOML configuration loading and validation."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hterm.errors import ConfigurationError

SUPPORTED_VERSION = 1
RESERVED_NAMES = frozenset(
    {"add", "open", "list", "check", "completion", "config", "finder", "lifecycle"}
)


@dataclass(frozen=True, slots=True)
class Settings:
    herdr_binary: Path = Path("/opt/homebrew/bin/herdr")
    ghostty_app: Path = Path("/Applications/Ghostty.app")
    aerospace_binary: Path = Path("/opt/homebrew/bin/aerospace")
    hook_shell: Path = Path("/bin/zsh")
    hook_timeout_seconds: float = 60
    focus: bool = True
    herdr_title_match: str = "herdr"


@dataclass(frozen=True, slots=True)
class Tab:
    name: str | None
    command: str | None
    cwd: Path
    focus: bool = False


@dataclass(frozen=True, slots=True)
class LayoutTab:
    name: str | None
    command: str | None
    cwd: Path | None
    focus: bool = False


@dataclass(frozen=True, slots=True)
class Layout:
    name: str
    tabs: tuple[LayoutTab, ...]


@dataclass(frozen=True, slots=True)
class Project:
    name: str
    cwd: Path
    label: str
    description: str | None
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]
    pre_hook: str | None
    setup_hook: str | None
    post_hook: str | None
    layout: str | None
    tabs: tuple[Tab, ...]

    def listing(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "aliases": list(self.aliases),
            "keywords": list(self.keywords),
            "cwd": str(self.cwd),
        }


@dataclass(frozen=True, slots=True)
class Config:
    path: Path
    version: int
    default: str
    settings: Settings
    layouts: Mapping[str, Layout]
    projects: Mapping[str, Project]
    aliases: Mapping[str, str]

    def resolve(self, name: str | None = None) -> Project:
        requested = name or self.default
        canonical = self.aliases.get(requested, requested)
        try:
            return self.projects[canonical]
        except KeyError:
            kind = "default project" if name is None else "project"
            raise ConfigurationError(
                f"Unknown {kind}: {requested}",
                code="project_not_found",
                project=requested,
            ) from None


def _error(message: str, *, path: Path, **details: Any) -> ConfigurationError:
    return ConfigurationError(message, path=str(path), **details)


def _table(value: Any, name: str, path: Path) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise _error(f"{name} must be a TOML table", path=path, field=name)
    return value


def _string(
    value: Any, field: str, path: Path, *, optional: bool = False
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{field} must be a non-empty string", path=path, field=field)
    return value


def _strings(value: Any, field: str, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise _error(
            f"{field} must be an array of non-empty strings", path=path, field=field
        )
    return tuple(value)


def _path(value: Any, field: str, config_path: Path) -> Path:
    text = _string(value, field, config_path)
    assert text is not None
    expanded = Path(os.path.expandvars(text)).expanduser()
    if not expanded.is_absolute():
        expanded = config_path.parent / expanded
    return expanded.resolve(strict=False)


def _directory(value: Any, field: str, config_path: Path) -> Path:
    directory = _path(value, field, config_path)
    if not directory.is_dir():
        raise _error(
            f"{field} is not an existing directory: {directory}",
            path=config_path,
            field=field,
        )
    return directory


def _settings(data: Any, path: Path) -> Settings:
    table = _table(data or {}, "settings", path)
    defaults = Settings()
    timeout = table.get("hook_timeout_seconds", defaults.hook_timeout_seconds)
    if (
        not isinstance(timeout, int | float)
        or isinstance(timeout, bool)
        or timeout <= 0
    ):
        raise _error(
            "settings.hook_timeout_seconds must be greater than zero",
            path=path,
            field="settings.hook_timeout_seconds",
        )
    focus = table.get("focus", defaults.focus)
    if not isinstance(focus, bool):
        raise _error(
            "settings.focus must be a boolean", path=path, field="settings.focus"
        )
    title_match = _string(
        table.get("herdr_title_match", defaults.herdr_title_match),
        "settings.herdr_title_match",
        path,
    )
    assert title_match is not None

    def setting_path(key: str, default: Path) -> Path:
        return _path(table.get(key, str(default)), f"settings.{key}", path)

    return Settings(
        herdr_binary=setting_path("herdr_binary", defaults.herdr_binary),
        ghostty_app=setting_path("ghostty_app", defaults.ghostty_app),
        aerospace_binary=setting_path("aerospace_binary", defaults.aerospace_binary),
        hook_shell=setting_path("hook_shell", defaults.hook_shell),
        hook_timeout_seconds=float(timeout),
        focus=focus,
        herdr_title_match=title_match,
    )


def _layout_tabs(data: Any, field: str, path: Path) -> tuple[LayoutTab, ...]:
    if not isinstance(data, list):
        raise _error(
            f"{field} must be an array of tables",
            path=path,
            field=field,
        )

    tabs: list[LayoutTab] = []
    focused = 0
    for index, raw_tab in enumerate(data):
        tab_field = f"{field}[{index}]"
        tab = _table(raw_tab, tab_field, path)
        tab_focus = tab.get("focus", False)
        if not isinstance(tab_focus, bool):
            raise _error(
                f"{tab_field}.focus must be a boolean",
                path=path,
                field=f"{tab_field}.focus",
            )
        focused += tab_focus
        tabs.append(
            LayoutTab(
                name=_string(tab.get("name"), f"{tab_field}.name", path, optional=True),
                command=_string(
                    tab.get("command"),
                    f"{tab_field}.command",
                    path,
                    optional=True,
                ),
                cwd=(
                    _directory(tab["cwd"], f"{tab_field}.cwd", path)
                    if "cwd" in tab
                    else None
                ),
                focus=tab_focus,
            )
        )
    if focused > 1:
        raise _error(
            f"{field} has more than one focused tab",
            path=path,
            field=f"{field}.focus",
        )
    return tuple(tabs)


def _layouts(data: Any, path: Path) -> dict[str, Layout]:
    tables = _table(data if data is not None else {}, "layouts", path)
    layouts: dict[str, Layout] = {}
    for name, raw_layout in tables.items():
        if not name.strip():
            raise _error(
                "Layout names must be non-empty",
                path=path,
                field=f"layouts.{name}",
            )
        table = _table(raw_layout, f"layouts.{name}", path)
        layouts[name] = Layout(
            name=name,
            tabs=_layout_tabs(table.get("tabs", []), f"layouts.{name}.tabs", path),
        )
    return layouts


def _project(
    name: str, data: Any, path: Path, layouts: Mapping[str, Layout]
) -> Project:
    table = _table(data, f"projects.{name}", path)
    cwd = _directory(table.get("cwd"), f"projects.{name}.cwd", path)
    aliases = _strings(table.get("aliases"), f"projects.{name}.aliases", path)
    keywords = _strings(table.get("keywords"), f"projects.{name}.keywords", path)
    layout_name = _string(
        table.get("layout"), f"projects.{name}.layout", path, optional=True
    )
    if layout_name is not None and "tabs" in table:
        raise _error(
            f"Project {name!r} cannot define both layout and tabs",
            path=path,
            field=f"projects.{name}.layout",
        )
    if layout_name is not None:
        try:
            tab_definitions = layouts[layout_name].tabs
        except KeyError:
            raise _error(
                f"Project {name!r} references unknown layout: {layout_name}",
                path=path,
                field=f"projects.{name}.layout",
                layout=layout_name,
            ) from None
    else:
        tab_definitions = _layout_tabs(
            table.get("tabs", []), f"projects.{name}.tabs", path
        )
    tabs = tuple(
        Tab(
            tab.name,
            tab.command,
            tab.cwd if tab.cwd is not None else cwd,
            tab.focus,
        )
        for tab in tab_definitions
    )

    label = _string(table.get("label", name), f"projects.{name}.label", path)
    assert label is not None
    return Project(
        name=name,
        cwd=cwd,
        label=label,
        description=_string(
            table.get("description"),
            f"projects.{name}.description",
            path,
            optional=True,
        ),
        aliases=aliases,
        keywords=keywords,
        pre_hook=_string(
            table.get("pre_hook"), f"projects.{name}.pre_hook", path, optional=True
        ),
        setup_hook=_string(
            table.get("setup_hook"), f"projects.{name}.setup_hook", path, optional=True
        ),
        post_hook=_string(
            table.get("post_hook"), f"projects.{name}.post_hook", path, optional=True
        ),
        layout=layout_name,
        tabs=tabs,
    )


def _built_in(path: Path) -> Config:
    home = Path.home().resolve()
    project = Project("home", home, "home", None, (), (), None, None, None, None, ())
    return Config(
        path, SUPPORTED_VERSION, "home", Settings(), {}, {"home": project}, {}
    )


def load_config(path: Path) -> Config:
    """Load, expand, and validate a config, or use the built-in home config."""
    path = path.expanduser().resolve(strict=False)
    if not path.exists():
        return _built_in(path)
    try:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _error(f"Unable to read configuration: {exc}", path=path) from exc

    version = data.get("version")
    if version != SUPPORTED_VERSION:
        raise _error(
            f"Unsupported configuration version {version!r}; expected {SUPPORTED_VERSION}",
            path=path,
            field="version",
        )
    default = _string(data.get("default", "home"), "default", path)
    assert default is not None
    layouts = _layouts(data.get("layouts"), path)
    projects_data = _table(data.get("projects", {}), "projects", path)
    projects: dict[str, Project] = {}
    aliases: dict[str, str] = {}
    occupied: set[str] = set()
    for name, raw_project in projects_data.items():
        if not name.strip() or name in RESERVED_NAMES:
            raise _error(
                f"Invalid or reserved project name: {name!r}",
                path=path,
                field=f"projects.{name}",
            )
        if name in occupied:
            raise _error(
                f"Duplicate project name or alias: {name}", path=path, name=name
            )
        project = _project(name, raw_project, path, layouts)
        projects[name] = project
        occupied.add(name)
        for alias in project.aliases:
            if alias in RESERVED_NAMES or alias in occupied or alias in projects_data:
                raise _error(
                    f"Duplicate or reserved project alias: {alias}",
                    path=path,
                    alias=alias,
                )
            aliases[alias] = name
            occupied.add(alias)

    config = Config(
        path,
        version,
        default,
        _settings(data.get("settings"), path),
        layouts,
        projects,
        aliases,
    )
    config.resolve()
    return config
