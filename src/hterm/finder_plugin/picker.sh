#!/bin/sh
# Run inside Herdr's modal terminal popup. Enter opens a project; Escape cancels.
set -eu

config_dir=${HERDR_PLUGIN_CONFIG_DIR:?HERDR_PLUGIN_CONFIG_DIR is not set}
hterm_path_file="$config_dir/hterm-path"
fzf_path_file="$config_dir/fzf-path"
config_path_file="$config_dir/config-path"

if [ ! -r "$hterm_path_file" ] || [ ! -r "$fzf_path_file" ] || [ ! -r "$config_path_file" ]; then
  printf '%s\n' 'hterm finder is not configured; run: hterm finder install-plugin' >&2
  exit 1
fi

hterm_bin=$(head -n 1 "$hterm_path_file")
fzf_bin=$(head -n 1 "$fzf_path_file")
config_path=$(head -n 1 "$config_path_file")

if [ ! -x "$hterm_bin" ]; then
  printf 'hterm finder: executable is unavailable: %s\n' "$hterm_bin" >&2
  exit 1
fi
if [ ! -x "$fzf_bin" ]; then
  printf 'hterm finder: fzf is unavailable: %s\n' "$fzf_bin" >&2
  exit 1
fi

projects=$(
  "$hterm_bin" list --config "$config_path" --finder-data
) || exit $?

set +e
selection=$(
  printf '%s\n' "$projects" | "$fzf_bin" \
    --delimiter='\t' \
    --with-nth=1,2 \
    --layout=reverse \
    --height=100% \
    --border=none \
    --prompt='hterm> ' \
    --header='Enter: open project  •  Esc: cancel' \
    --no-multi
)
status=$?
set -e

# fzf uses 1 for no match and 130 for an interrupted/cancelled picker.
if [ "$status" -eq 1 ] || [ "$status" -eq 130 ]; then
  exit 0
fi
if [ "$status" -ne 0 ]; then
  exit "$status"
fi

project=$(printf '%s\n' "$selection" | cut -f 1)
[ -n "$project" ] || exit 0

exec "$hterm_bin" open "$project" --config "$config_path"
