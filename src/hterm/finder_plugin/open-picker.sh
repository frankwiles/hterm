#!/bin/sh
# Actions run detached, so ask Herdr to open the interactive fzf pane as a popup.
set -eu

herdr_bin=${HERDR_BIN_PATH:-herdr}
plugin_id=${HERDR_PLUGIN_ID:-hterm.finder}

exec "$herdr_bin" plugin pane open \
  --plugin "$plugin_id" \
  --entrypoint picker \
  --placement popup \
  --width '80%' \
  --height '70%'
