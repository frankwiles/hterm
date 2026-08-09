# hterm implementation plan

## Purpose

`hterm` is a private macOS CLI and Raycast extension for launching repeatable Herdr workspaces from project definitions in `~/.hterm.toml`.

The Python CLI is the source of truth. Zsh completion and the Raycast extension both consume the CLI. A small Herdr plugin handles workspace-close lifecycle hooks.

## Confirmed decisions

- Platform: macOS.
- Shell support: Zsh only.
- Terminal: Ghostty.
- Window manager: AeroSpace.
- Herdr session: default session only.
- An invocation reuses and focuses an existing Herdr workspace with the project label; otherwise it creates a new workspace.
- `hterm` with no project opens the configured default project, initially `home` in `~`.
- Preferred shorthand is `hterm do2`; canonical `hterm open do2` also works.
- Zsh completion supports both forms, including `hterm d<Tab>` completing to `do2`.
- Reserved command names (`open`, `list`, `check`, `completion`, etc.) cannot be project names.
- Projects define zero or more tabs. With no tab definitions, create one shell tab in the project CWD.
- Hooks and commands are Zsh strings, not argv arrays.
- Post-hooks run when a workspace is explicitly closed or when all panes naturally exit and the workspace closes.
- From Raycast, focus the Ghostty window containing Herdr by focusing its exact AeroSpace window ID.
- If no Herdr client window exists, open a new Ghostty window and attach it to the default Herdr session.

## External capabilities verified

- Herdr 0.8.0 returns JSON from workspace/tab operations.
- `herdr workspace create` returns workspace, initial tab, and root pane IDs.
- `herdr tab create --workspace ID` returns the new tab and root pane IDs.
- `herdr pane run PANE COMMAND` submits commands to panes.
- Herdr plugin events include `workspace.closed`.
- Ghostty 1.3.x supports macOS AppleScript window creation and activation when `macos-applescript` is enabled (default).
- AeroSpace 0.20.x supports JSON window listing and exact focus with `aerospace focus --window-id ID`; exact focus reveals the window's AeroSpace workspace.

## CLI contract

### Commands

```zsh
hterm                         # open configured default project
hterm do2                     # shorthand project launch
hterm open do2                # canonical project launch
hterm list [--json]
hterm check [PROJECT]
hterm completion zsh
hterm config path
hterm --version
```

Common launch options:

```zsh
--json
--dry-run
--no-focus
--config PATH
```

### Exit statuses

- `0`: success
- `1`: configuration, hook, Herdr, Ghostty, AeroSpace, or orchestration failure
- `2`: invalid CLI usage

### JSON success

```json
{
  "ok": true,
  "action": "open",
  "project": "do2",
  "workspace_id": "w12",
  "tabs": [
    {"name": "code", "tab_id": "w12:t1", "pane_id": "w12:p1"}
  ],
  "presentation": {
    "ghostty_window_id": 18039,
    "aerospace_workspace": "T",
    "focused": true
  },
  "warnings": []
}
```

### JSON failure

```json
{
  "ok": false,
  "error": {
    "code": "pre_hook_failed",
    "message": "VPN is not connected",
    "project": "do2",
    "step": "pre_hook",
    "exit_code": 1
  }
}
```

In JSON mode stdout contains only the result envelope. Human diagnostics and hook output must not corrupt stdout.

## Configuration schema

Default path: `~/.hterm.toml`.

```toml
version = 1
default = "home"

[settings]
herdr_binary = "/opt/homebrew/bin/herdr"
ghostty_app = "/Applications/Ghostty.app"
aerospace_binary = "/opt/homebrew/bin/aerospace"
hook_shell = "/bin/zsh"
hook_timeout_seconds = 60
focus = true
herdr_title_match = "herdr"

[projects.home]
cwd = "~"
label = "home"

[projects.do2]
description = "DO2 development"
cwd = "~/src/do2"
label = "do2"
aliases = ["d2"]
keywords = ["work", "development"]
pre_hook = """
test -d "$HOME/src/do2"
"""
setup_hook = """
# HTERM_WORKSPACE_ID, HTERM_TAB_ID, and HTERM_PANE_ID are available.
"""
post_hook = """
echo "DO2 closed"
"""

[[projects.do2.tabs]]
name = "code"
command = "pi"

[[projects.do2.tabs]]
name = "server"
command = "uv run server"

[[projects.do2.tabs]]
name = "shell"
focus = true
```

Reusable tab sets may instead be declared and selected by a project:

```toml
[[layouts.coding.tabs]]
name = "code"
command = "pi"
focus = true

[[layouts.coding.tabs]]
name = "shell"

[projects.do2]
cwd = "~/src/do2"
layout = "coding"
```

Rules:

- Expand `~` and environment variables in paths.
- Project names and aliases are unique.
- Reserved CLI command names are invalid project names.
- A project may select a named layout or define its own tabs, but not both.
- Layouts can be shared by any number of projects and unknown layout names are invalid.
- A project or layout tab's `cwd` defaults to the project `cwd`.
- `name`, `command`, and tab `cwd` are optional.
- At most one tab per project or layout may have `focus = true`.
- With no tabs, the workspace's initial tab remains an interactive shell in project `cwd`.
- With tabs, the first definition configures the initial tab and later definitions create new tabs.

## Hook semantics

All local hooks run as Zsh strings through `/bin/zsh -lc`, with the project CWD as the working directory and a configurable timeout.

Before creation, `pre_hook` receives:

- `HTERM_PROJECT`
- `HTERM_PROJECT_DIR`
- `HTERM_CONFIG_PATH`

After creation, `setup_hook` and eventually `post_hook` also receive:

- `HTERM_WORKSPACE_ID`
- `HTERM_TAB_ID`
- `HTERM_PANE_ID`

Behavior:

1. A failed pre-hook creates no workspace.
2. A failed Herdr create operation returns a normalized Herdr error.
3. A tab/setup failure closes the partial workspace by default.
4. Rollback closure does not run the normal post-hook.
5. Initial pane commands are considered launched when Herdr accepts submission; their later process exit is not an `hterm` launch failure.
6. Readiness/output waiting is a later enhancement.

## Workspace orchestration

1. Load and validate config.
2. Resolve project/alias and expanded paths.
3. Run pre-hook.
4. Ensure the default Herdr server is available.
5. List Herdr workspaces and reuse an exact project-label match when present.
6. Otherwise run `herdr workspace create --cwd ... --label ... --focus`, parse IDs, and persist a lifecycle record.
7. Configure/rename/run the initial tab from the first tab definition, if any.
8. Create and configure remaining tabs.
9. Run optional setup-hook with `HTERM_*` IDs.
10. Focus the configured tab/workspace.
11. Present/focus the corresponding Ghostty/AeroSpace window.
12. Return human or JSON success.

Use Herdr CLI wrappers rather than the raw socket API unless a required operation has no CLI wrapper.

## Ghostty and AeroSpace presentation

### Existing Herdr client

1. Query AeroSpace for Ghostty windows with window ID, title, workspace, visibility, and focus state.
2. Identify Herdr candidates by configurable title matching (default recognizes `herdr`).
3. If one matches, focus it using `aerospace focus --window-id ID`.
4. If several match, prefer focused, then visible, then deterministic window-ID order.
5. Include the chosen window and AeroSpace workspace in JSON diagnostics.

### No Herdr client

1. Snapshot existing Ghostty AeroSpace window IDs.
2. Use Ghostty AppleScript to create a new window that runs `herdr session attach default`.
3. Poll AeroSpace for a new Ghostty window and identify it by set difference.
4. Focus the exact new window with AeroSpace.
5. Return clear permission/timeout errors when Ghostty automation fails.

If the Herdr server is absent, start/ensure it before workspace creation so a failed pre-hook never opens a terminal window. Verify server-start behavior against Herdr during implementation to avoid an unwanted automatic workspace.

Presentation failure after successful Herdr creation should be returned as a warning by default rather than falsely reporting workspace creation failure.

## Post-hook lifecycle

Ship a small Herdr plugin with a `workspace.closed` event command. Persist records under:

```text
~/.local/state/hterm/workspaces/<workspace-id>.json
```

On `workspace.closed`, invoke an internal command such as:

```zsh
hterm lifecycle workspace-closed <workspace-id>
```

The handler atomically claims the record, executes the post-hook exactly once, records status/output, and never blocks Herdr closure. Explicit close and natural last-pane/last-tab exit both converge on `workspace.closed`. Crash, forced kill, or machine shutdown remains best effort.

## Zsh completion

`hterm completion zsh` emits a completion function that reads current project names and aliases from the TOML through a machine-oriented CLI query. It supports:

- `hterm <Tab>`
- `hterm d<Tab>` -> `hterm do2`
- `hterm open <Tab>`
- `hterm check <Tab>`
- options and canonical subcommands

Persistent setup:

```zsh
mkdir -p ~/.zfunc
hterm completion zsh > ~/.zfunc/_hterm
```

## Raycast extension

Build a private TypeScript Raycast extension, not a static Script Command.

- Call `hterm list --json` for project metadata.
- Use Raycast List fuzzy filtering over names, aliases, descriptions, keywords, and paths.
- Enter invokes `hterm open PROJECT --json`.
- Show progress, success, and structured failure toasts.
- Configure the absolute `hterm` executable path because Raycast does not inherit the interactive shell PATH reliably.
- Offer secondary actions for no-focus launch, config validation, opening config, copying/revealing project paths.
- Let the CLI own Ghostty and AeroSpace integration so terminal and Raycast behavior stay consistent.

## Testing strategy

- Unit-test config, alias resolution, errors, hook environment, completion data, lifecycle claiming, and selection policies.
- Use fake executable scripts for Herdr, AeroSpace, Ghostty/osascript, and Zsh subprocess boundaries.
- Integration-test against installed Herdr/Ghostty/AeroSpace only in explicitly marked macOS tests.
- Cover paths with spaces, malformed JSON, timeouts, missing binaries, protocol mismatch, absent server, automation permission denial, multiple Herdr clients, partial tab failure, and post-hook idempotency.

## Deferred enhancements

- Pane splits/layout trees.
- Named Herdr sessions.
- Bash/fish completion.
- Startup readiness checks with `pane wait-output`.
- Closing/status/recent CLI commands.
- Cross-platform support.
