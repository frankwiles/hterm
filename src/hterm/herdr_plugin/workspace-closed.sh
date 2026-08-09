#!/bin/sh
# Herdr runs event hooks asynchronously. Keep this adapter tiny and delegate
# lifecycle claiming, validation, hook execution, and durable output to hterm.

workspace_id=${HERDR_WORKSPACE_ID:-}
[ -n "$workspace_id" ] || exit 0

hterm_bin=""
if [ -n "${HERDR_PLUGIN_CONFIG_DIR:-}" ] && [ -r "$HERDR_PLUGIN_CONFIG_DIR/hterm-path" ]; then
  IFS= read -r hterm_bin < "$HERDR_PLUGIN_CONFIG_DIR/hterm-path"
fi
if [ ! -x "$hterm_bin" ]; then
  hterm_bin=$(command -v hterm 2>/dev/null || true)
fi
if [ -z "$hterm_bin" ]; then
  printf '%s\n' "hterm lifecycle plugin: hterm executable not found" >&2
  exit 127
fi

exec "$hterm_bin" lifecycle workspace-closed "$workspace_id" --json
