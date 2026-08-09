# hterm Projects for Raycast

Private Raycast extension for searching and opening projects from `~/.hterm.toml`.
The extension delegates listing, validation, launching, and presentation entirely to the hterm CLI.

## Install

1. Install hterm and verify `hterm list --json` works.
2. In this directory, run `npm install` and `npm run dev`.
3. Set **hterm Executable** to the executable's absolute path (for example, `/opt/homebrew/bin/hterm` or the output of `command -v hterm`).

The absolute path is required because Raycast has a reduced `PATH` environment.

## Actions

- **Open Project** launches and focuses its Ghostty/Herdr window.
- **Open Without Focus** passes `--no-focus` to hterm and keeps Raycast open.
- **Validate Configuration** runs `hterm check --json`.
- **Open Configuration** obtains the effective path from `hterm config path --json` and opens it.
- Project paths can be copied or revealed in Finder.

All messages, project metadata, errors, and warnings are read from hterm's JSON contracts; this extension does not parse TOML or duplicate workspace orchestration.
