"""Dynamic Zsh completion generation."""

from __future__ import annotations

from hterm.config import Config


def _describe_escape(value: str) -> str:
    """Escape a value for Zsh's `candidate:description` convention."""
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("\n", " ")


def project_completion_lines(config: Config) -> list[str]:
    """Return dynamic project and alias candidates for `_describe`."""
    lines: list[str] = []
    for project in config.projects.values():
        description = project.description or str(project.cwd)
        if project.aliases:
            description = f"{description} (aliases: {', '.join(project.aliases)})"
        lines.append(
            f"{_describe_escape(project.name)}:{_describe_escape(description)}"
        )
    return lines


def project_alias_lines(config: Config) -> list[str]:
    """Return tab-delimited alias-to-canonical mappings for completion."""
    return [
        f"{alias}\t{project.name}"
        for project in config.projects.values()
        for alias in project.aliases
    ]


ZSH_COMPLETION = r"""#compdef hterm

_hterm_projects() {
  local executable="$words[1]"
  local -a config_args projects alias_display
  local i output alias_output alias canonical

  # Preserve an explicit config path so completion reflects the active config.
  for (( i = 2; i < CURRENT; i++ )); do
    if [[ "$words[i]" == "--config" && $(( i + 1 )) -lt CURRENT ]]; then
      config_args=(--config "$words[i + 1]")
    elif [[ "$words[i]" == --config=* ]]; then
      config_args=("$words[i]")
    fi
  done

  output="$("$executable" list --completion-data "${config_args[@]}" 2>/dev/null)"
  projects=("${(@f)output}")
  (( ${#projects} )) && _describe -t projects 'project' projects

  # Matching an alias inserts its canonical project name. This avoids aliases
  # competing with canonical names for a shared prefix (d -> do2, not d2).
  alias_output="$("$executable" list --completion-aliases "${config_args[@]}" 2>/dev/null)"
  while IFS=$'\t' read -r alias canonical; do
    if [[ -n "$PREFIX" && "$alias" == "$PREFIX"* ]]; then
      alias_display=("$alias (alias for $canonical)")
      compadd -Q -U -d alias_display -- "$canonical"
    fi
  done <<< "$alias_output"
}

_hterm_commands() {
  local -a commands=(
    'add:add a project to the configuration'
    'open:open a project'
    'list:list configured projects'
    'check:validate configuration or a project'
    'completion:generate shell completion code'
    'config:inspect configuration'
    'finder:install the Herdr fzf project finder'
    'lifecycle:install the Herdr lifecycle plugin'
  )
  _describe -t commands 'command' commands
}

_hterm() {
  local context state state_descr line
  typeset -A opt_args

  _arguments -C \
    '(-h --help)'{-h,--help}'[show help]' \
    '--version[show version]' \
    '--config=[use a TOML configuration file]:configuration file:_files' \
    '--json[emit one JSON result]' \
    '--dry-run[plan without side effects]' \
    '--no-focus[do not focus a terminal]' \
    '1:command or project:->first' \
    '*::argument:->args'

  case "$state" in
    first)
      _hterm_commands
      _hterm_projects
      ;;
    args)
      case "$words[2]" in
        add)
          _arguments '--config=[configuration file]:configuration file:_files'
          ;;
        open)
          if (( CURRENT == 3 )); then
            _hterm_projects
          else
            _arguments '--config=[configuration file]:configuration file:_files' \
              '--json[emit one JSON result]' '--dry-run[plan without side effects]' \
              '--no-focus[do not focus a terminal]'
          fi
          ;;
        check)
          if (( CURRENT == 3 )); then
            _hterm_projects
          else
            _arguments '--config=[configuration file]:configuration file:_files' \
              '--json[emit one JSON result]'
          fi
          ;;
        list)
          _arguments '--config=[configuration file]:configuration file:_files' \
            '--json[emit one JSON result]'
          ;;
        completion)
          _values 'shell' zsh
          ;;
        config)
          if (( CURRENT == 3 )); then
            _values 'config command' path
          else
            _arguments '--config=[configuration file]:configuration file:_files' \
              '--json[emit one JSON result]'
          fi
          ;;
        finder)
          if (( CURRENT == 3 )); then
            _values 'finder command' install-plugin
          else
            _arguments '--config=[configuration file]:configuration file:_files' \
              '--hterm-binary=[absolute hterm executable]:executable:_files' \
              '--fzf-binary=[absolute fzf executable]:executable:_files' \
              '--json[emit one JSON result]'
          fi
          ;;
        lifecycle)
          if (( CURRENT == 3 )); then
            _values 'lifecycle command' install-plugin
          else
            _arguments '--config=[configuration file]:configuration file:_files' \
              '--hterm-binary=[absolute hterm executable]:executable:_files' \
              '--json[emit one JSON result]'
          fi
          ;;
        *)
          _arguments '--config=[configuration file]:configuration file:_files' \
            '--json[emit one JSON result]' '--dry-run[plan without side effects]' \
            '--no-focus[do not focus a terminal]'
          ;;
      esac
      ;;
  esac
}

compdef _hterm hterm
"""
