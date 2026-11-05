#!/bin/sh
# Launcher used by the Claude Code and Codex plugins.
# Creates a private virtualenv on first run (and when requirements change), then starts the MCP server.
# Everything except the server's own MCP traffic goes to stderr, so stdio stays clean.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${CLAUDE_PLUGIN_DATA:-${PLUGIN_DATA:-$HOME/.ssh-connector}}"
mkdir -p "$DATA"
VENV="$DATA/venv"
if [ ! -x "$VENV/bin/python" ] || ! cmp -s "$ROOT/requirements.txt" "$DATA/requirements.installed" 2>/dev/null; then
  echo "ssh-connector: installing dependencies into $VENV ..." >&2
  python3 -m venv "$VENV" >&2
  "$VENV/bin/pip" install -q --upgrade pip >&2
  "$VENV/bin/pip" install -q -r "$ROOT/requirements.txt" >&2
  cp "$ROOT/requirements.txt" "$DATA/requirements.installed"
fi
export SSH_CONNECTOR_CONFIG="${SSH_CONNECTOR_CONFIG:-$HOME/.ssh-connector/servers.yaml}"
exec "$VENV/bin/python" "$ROOT/server.py" "$@"
