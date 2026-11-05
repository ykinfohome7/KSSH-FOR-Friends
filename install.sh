#!/bin/sh
# SSH Connector - one-line installer for Claude Code and ChatGPT Codex.
# Инсталатор с един ред за Claude Code и ChatGPT Codex.
#
#   wget -qO- https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh
#
# Options / Опции (after "sh -s --" when piped):
#   --claude | --codex      only this client (default: every client found)
#   --remote URL            connect to an existing HTTPS backend instead of the local plugin
#   --name NAME             connector name for --remote (default: kssh)
#   --dry-run               print the commands, change nothing
#   -h | --help
#
# No sudo, no system packages, no SSH keys or passwords are touched.
# Only the client's own configuration is changed (plugin list / MCP list),
# plus ~/.ssh-connector/servers.yaml is created (mode 600) when it does not exist.
set -eu

REPO="ykinfohome7/KSSH-FOR-Friends"
MARKET="kssh-for-friends"
PLUGIN="ssh-connector"
EXAMPLE_URL="https://github.com/$REPO/blob/main/servers.example.yaml"

want_claude=0
want_codex=0
remote=""
name="kssh"
dry=0

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR / ГРЕШКА: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,17p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//' || true
  say "See / Виж: https://github.com/$REPO/blob/main/docs/INSTALL.md"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --claude) want_claude=1 ;;
    --codex) want_codex=1 ;;
    --remote) [ $# -ge 2 ] || die "--remote needs a URL"; remote="$2"; shift ;;
    --name) [ $# -ge 2 ] || die "--name needs a value"; name="$2"; shift ;;
    --dry-run) dry=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option / непозната опция: $1" ;;
  esac
  shift
done

case "$name" in
  ''|*[!A-Za-z0-9_-]*) die "--name: only letters, digits, - and _ / само букви, цифри, - и _" ;;
esac

if [ -n "$remote" ]; then
  case "$remote" in
    https://*) ;;
    *) die "--remote must start with https:// / адресът трябва да започва с https://" ;;
  esac
  case "$remote" in
    *[!A-Za-z0-9.:/_-]*) die "--remote: invalid characters (no user:password@, no query) / невалидни символи" ;;
  esac
  # Only https://<host>[:port]/mcp - a token must never be part of the URL.
  remote_rest="${remote#https://}"
  case "$remote_rest" in
    */*) remote_path="${remote_rest#*/}" ;;
    *) remote_path="" ;;
  esac
  case "$remote_path" in
    mcp|mcp/) ;;
    *) die "--remote must be https://<host>/mcp (token goes in the header, never in the URL) / адресът трябва да е https://<хост>/mcp, токенът не се слага в адреса" ;;
  esac
fi

has() { command -v "$1" >/dev/null 2>&1; }

if [ "$want_claude" -eq 0 ] && [ "$want_codex" -eq 0 ]; then
  has claude && want_claude=1
  has codex && want_codex=1
  [ "$want_claude" -eq 1 ] || [ "$want_codex" -eq 1 ] || die "neither 'claude' nor 'codex' found in PATH / не са намерени 'claude' или 'codex'"
fi
[ "$want_claude" -eq 0 ] || has claude || die "'claude' (Claude Code) not found / не е намерен"
[ "$want_codex" -eq 0 ] || has codex || die "'codex' (ChatGPT Codex) not found / не е намерен"

# run CMD... : print the command, run it unless --dry-run. Arguments are passed as-is (no eval).
run() {
  printf '+ %s\n' "$*"
  [ "$dry" -eq 1 ] || "$@"
}

if [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; then
  say "Note / Бележка: running as root is not needed; the connector is installed for this user only."
fi

# ---------------- local plugin ----------------
install_plugin() {
  has python3 || die "python3 is required / нужен е python3"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "python3 >= 3.10 is required / нужен е python3 >= 3.10"
  python3 -c 'import venv' 2>/dev/null \
    || die "python3 venv module missing (Debian/Ubuntu: apt install python3-venv) / липсва модулът venv"

  if [ "$want_claude" -eq 1 ]; then
    say "== Claude Code =="
    run claude plugin marketplace add "$REPO" || say "(marketplace may already be added / може вече да е добавен)"
    run claude plugin marketplace update "$MARKET" || true
    run claude plugin install "$PLUGIN@$MARKET"
  fi
  if [ "$want_codex" -eq 1 ]; then
    say "== ChatGPT Codex =="
    run codex plugin marketplace add "$REPO" || say "(marketplace may already be added / може вече да е добавен)"
    if ! run codex plugin add "$PLUGIN"; then
      say "Open Codex and enable '$PLUGIN' in the plugin list (/plugins)."
      say "Отвори Codex и включи '$PLUGIN' от списъка с плъгини (/plugins)."
    fi
  fi

  cfg_dir="$HOME/.ssh-connector"
  cfg="$cfg_dir/servers.yaml"
  if [ ! -e "$cfg" ]; then
    say "== $cfg =="
    if [ "$dry" -eq 1 ]; then
      say "+ create $cfg (mode 600)"
    else
      mkdir -p "$cfg_dir"
      chmod 700 "$cfg_dir"
      umask 077
      cat > "$cfg" <<'YAML'
# SSH Connector - your servers. Full example / Пълен пример:
# https://github.com/ykinfohome7/KSSH-FOR-Friends/blob/main/servers.example.yaml
# Do not write passwords here; use SSH keys. / Не пиши пароли тук; използвай SSH ключове.
servers: {}
# servers:
#   web1:
#     hosts: [192.0.2.10]
#     user: admin
#     key: ~/.ssh/id_ed25519
YAML
      say "created / създаден: $cfg"
    fi
  fi
}

# ---------------- remote HTTPS backend ----------------
read_token() {
  if [ -n "${KSSH_TOKEN:-}" ]; then
    return 0
  fi
  [ "$dry" -eq 0 ] || { KSSH_TOKEN="<TOKEN>"; return 0; }
  [ -r /dev/tty ] || die "set KSSH_TOKEN in the environment / задай KSSH_TOKEN"
  printf 'Token (hidden) / Токен (скрит): ' > /dev/tty
  old=$(stty -g < /dev/tty)
  stty -echo < /dev/tty
  IFS= read -r KSSH_TOKEN < /dev/tty || true
  stty "$old" < /dev/tty
  printf '\n' > /dev/tty
  [ -n "$KSSH_TOKEN" ] || die "empty token / празен токен"
}

install_remote() {
  if [ "$want_claude" -eq 1 ]; then
    say "== Claude Code -> $remote =="
    read_token
    # The token is stored in the user's Claude Code config (~/.claude.json); it is not printed here.
    printf '+ claude mcp add --scope user --transport http %s %s --header "Authorization: Bearer ***"\n' "$name" "$remote"
    if [ "$dry" -eq 0 ]; then
      claude mcp add --scope user --transport http "$name" "$remote" --header "Authorization: Bearer $KSSH_TOKEN"
    fi
  fi
  if [ "$want_codex" -eq 1 ]; then
    say "== ChatGPT Codex -> $remote =="
    # Codex keeps only the variable name; the token stays in your environment.
    run codex mcp add "$name" --url "$remote" --bearer-token-env-var KSSH_TOKEN
    say "Add to your shell profile / Добави в профила на shell-а:  export KSSH_TOKEN='<TOKEN>'"
  fi
}

if [ -n "$remote" ]; then
  install_remote
else
  install_plugin
fi

say ""
say "Done / Готово."
say "Restart Claude Code / Codex, then ask: version_info"
say "Рестартирай Claude Code / Codex и попитай: version_info"
[ -n "$remote" ] || say "Servers / Сървъри: $HOME/.ssh-connector/servers.yaml  (example / пример: $EXAMPLE_URL)"
