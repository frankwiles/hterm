# hterm

`hterm` is a macOS CLI for launching repeatable [Herdr](https://github.com/herdrdev/herdr) workspaces from project definitions. The Python CLI is the source of truth for the bundled private Raycast extension and Zsh completion.

## Requirements

- macOS
- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Herdr 0.8+
- Ghostty 1.3+ with `macos-applescript` enabled
- AeroSpace 0.20+
- [fzf](https://github.com/junegunn/fzf) (for the optional Herdr project finder)

## Installation

From a checkout, install the CLI as an isolated uv tool:

```sh
cd /path/to/hterm
uv tool install .
hterm --version
```

After pulling updates, run `uv tool upgrade hterm` (or `uv tool install --force .` for an uncommitted local checkout). Ensure uv's tool bin directory is on `PATH`; `uv tool update-shell` configures this automatically.

Then create `~/.hterm.toml` (see below), validate it, and install the lifecycle plugin:

```sh
hterm check
hterm lifecycle install-plugin
hterm home --dry-run
```

The plugin is included in the Python wheel, so installation does not depend on the source checkout remaining in place.

## Development

```sh
uv sync
uv run hterm --help
just check
cd raycast && npm ci && npm run lint && npm run build
```

Other useful recipes in `Justfile` include `just test`, `just lint`, and `just build`.

## Configuration

The default path is `~/.hterm.toml`. If it does not exist, hterm supplies a built-in `home` project rooted at `~`. See [`docs/example.hterm.toml`](docs/example.hterm.toml) for every setting and [`docs/PLAN.md`](docs/PLAN.md) for the full behavior.

Minimal configuration:

```toml
version = 1
default = "home"

[[layouts.coding.tabs]]
name = "code"
command = "pi"
focus = true

[[layouts.coding.tabs]]
name = "server"
command = "uv run server"

[[layouts.coding.tabs]]
name = "git"
command = "lazygit"

[[layouts.coding.tabs]]
name = "shell"

[projects.home]
cwd = "~"
label = "home"

[projects.example]
cwd = "~/src/example"
aliases = ["ex"]
keywords = ["development"]
layout = "coding"
```

Named layouts are reusable sets of tabs. Set `layout = "coding"` on any number of projects, or continue defining project-specific `[[projects.NAME.tabs]]` entries. A project cannot specify both. Tabs without a `cwd` inherit each project's directory, so one layout can be shared across projects; an explicit layout-tab `cwd` is resolved like any other path.

Paths expand `~` and environment variables. Project names and aliases must be unique and cannot use reserved CLI command names. Project and tab working directories must exist. At most one tab per project or layout may set `focus = true`.

## CLI

```sh
hterm --version
hterm config path
hterm add                         # interactively add the current project
hterm fix                        # reconcile the focused workspace with coding
hterm fix --layout=ops --force   # use ops and close extra tabs
hterm list --json
hterm check [PROJECT]
hterm lifecycle install-plugin     # link the Herdr workspace-close plugin
hterm finder install-plugin        # link the Herdr fzf project finder
hterm                              # configured default project
hterm example                      # shorthand
hterm open example                 # canonical form
hterm example --dry-run            # inspect the launch without side effects
```

`hterm add` prompts for the project name, label, working directory, and an optional existing layout, then appends the project to the configuration. It creates a missing configuration with the built-in `home` project. Pass `--config PATH` to edit another configuration.

`hterm fix` finds the focused Herdr workspace and reconciles its tabs with `settings.fix_layout` (default: `coding`), or the layout passed with `--layout`. Matching tabs are preserved and missing tabs are created; extra tabs are retained unless `--force` is supplied. For linked worktrees, inherited tab directories use the active checkout rather than the project's repository root. Fixing does not run project hooks. Use `--dry-run` to inspect the plan.

`--config PATH`, `--json`, and `--no-focus` are supported for launches. If a Herdr workspace already has the project's label, hterm reuses it instead of running hooks or creating and configuring another workspace; with focus enabled, it focuses that workspace and returns `"reused": true`. If duplicate labels exist, hterm deterministically prefers the focused workspace, then the lowest workspace number.

With focus enabled, hterm also reuses a Ghostty window whose title contains `herdr` (customizable with `settings.herdr_title_match`); if none exists, it creates a Ghostty window running `herdr session attach default` and identifies the new AeroSpace window by ID snapshot difference. Among multiple matches it prefers the focused workspace, then a visible workspace, then the lowest window ID. It focuses the exact result with `aerospace focus --window-id ID`. Presentation failures are returned as structured warnings because the Herdr workspace was still created or found successfully.

JSON mode writes exactly one result envelope to stdout; expected failures exit with status 1, while invalid CLI usage exits with status 2.

## Herdr fzf project finder

Install the bundled finder plugin after installing `hterm` and `fzf`:

```sh
hterm finder install-plugin
```

The command links the `hterm.finder` plugin and records absolute paths for `hterm`, `fzf`, and the active hterm config so it remains reliable in Herdr's reduced plugin environment. Add the keybinding it prints to `~/.config/herdr/config.toml` (or use this default):

```toml
[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "hterm.finder.open"
description = "find an hterm project"
```

Then apply it with `herdr server reload-config`. Press `prefix+f` to open an 80% × 70% modal popup, type to filter project names, descriptions, labels, aliases, keywords, and paths, and press Enter to launch the selected project. Escape closes the popup without changing the current workspace.

If either executable cannot be discovered during setup, provide it explicitly:

```sh
hterm finder install-plugin \
  --hterm-binary "$(command -v hterm)" \
  --fzf-binary "$(command -v fzf)"
```

Inspect registration and action logs with:

```sh
herdr plugin list --plugin hterm.finder --json
herdr plugin action list --plugin hterm.finder
herdr plugin log list --plugin hterm.finder
```

## Workspace post-hooks

Install the bundled Herdr plugin after installing or moving the `hterm` executable:

```sh
hterm lifecycle install-plugin
# If hterm cannot discover its own installed entry point:
hterm lifecycle install-plugin --hterm-binary "$(command -v hterm)"
```

This links the `hterm.lifecycle` plugin, subscribes it to `workspace.closed`, and records the absolute hterm executable path in the plugin config directory. Herdr event hooks are asynchronous, so workspace closure is never held open while a post-hook runs. The same event is emitted for explicit workspace closure and natural closure after the last panes exit.

Each newly created workspace writes `~/.local/state/hterm/workspaces/<workspace-id>.json` (or the equivalent under `XDG_STATE_HOME`); reusing an existing workspace does not replace its lifecycle record. On closure, the handler atomically claims the record, runs the configured post-hook once through the configured hook shell (`/bin/zsh -lc` by default), and records status, exit code, stdout, stderr, and timestamps. It supplies `HTERM_PROJECT`, `HTERM_PROJECT_DIR`, `HTERM_CONFIG_PATH`, `HTERM_WORKSPACE_ID`, `HTERM_TAB_ID`, and `HTERM_PANE_ID`.

Duplicate events and canceled rollback records do not rerun hooks. A permanent claim marker deliberately favors at-most-once behavior: if hterm crashes or the machine shuts down after claiming a record, it will not automatically retry a hook whose side effects are unknown. Events lost during a Herdr/server crash or shutdown are also best effort.

Inspect plugin registration and event-command logs with:

```sh
herdr plugin list --plugin hterm.lifecycle --json
herdr plugin log list --plugin hterm.lifecycle
```

## Zsh completion

Try completion in the current shell:

```zsh
source <(hterm completion zsh)
```

For persistent installation:

```zsh
mkdir -p ~/.zfunc
hterm completion zsh > ~/.zfunc/_hterm
```

Ensure `~/.zfunc` is on `fpath` before initializing completion in `.zshrc`:

```zsh
fpath=(~/.zfunc $fpath)
autoload -Uz compinit && compinit
```

Project names and aliases are queried from the active TOML file whenever completion runs, so configuration changes do not require regenerating `_hterm`. Both `hterm PROJECT` and `hterm open PROJECT` are supported.

## Raycast extension

The private TypeScript extension lives in [`raycast/`](raycast/). Install the CLI first, then load the extension for development with:

```sh
cd raycast
npm install
npm run dev
```

In its preferences, set **hterm Executable** to the absolute path printed by `command -v hterm`. The extension intentionally delegates project listing, validation, launch orchestration, and config path resolution to the CLI. See [`raycast/README.md`](raycast/README.md) for its actions and setup details.

## Acceptance check

After installation and configuration:

```sh
hterm --version
hterm check
hterm list --json
hterm home --dry-run --json
hterm lifecycle install-plugin --json
hterm finder install-plugin --json
```

Then launch one project from the shell and from Raycast. Confirm the first launch creates and configures a Herdr workspace, while the second reuses and focuses it. Close the workspace and inspect its terminal lifecycle record under `~/.local/state/hterm/workspaces/`; a configured post-hook should have a terminal status and should not run again for a duplicate close event.

Presentation checks require macOS Automation permission for the process running hterm to control Ghostty. A denied permission or unavailable AeroSpace is reported as a warning after successful workspace creation rather than losing the workspace result.

Maintainers can opt into non-destructive installed-tool integration checks with `HTERM_RUN_MACOS_INTEGRATION=1 uv run pytest -m macos_integration`. The Herdr test creates and closes a temporary workspace; the AeroSpace test only lists windows.

## License

MIT
