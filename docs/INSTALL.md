# Install SSH Connector

**English** · [Български](INSTALL.bg.md)

Works with **Claude Code** and **ChatGPT Codex** on Linux and macOS. Needs `python3` ≥ 3.10 (with `venv`) and the client's CLI (`claude` or `codex`). No `sudo`.

## 1. One line (recommended)

```bash
wget -qO- https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh
```
or
```bash
curl -fsSL https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh
```

The script finds `claude` and/or `codex`, adds the plugin marketplace, installs the plugin and creates an empty `~/.ssh-connector/servers.yaml` (mode 600) if you have none. Then restart the client and ask `version_info`.

## 2. Check the script first (for careful admins)

```bash
wget -O install.sh https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh
less install.sh          # read it
sh install.sh --dry-run  # shows the commands, changes nothing
sh install.sh
```

## Options

| Option | What it does |
|---|---|
| `--claude` / `--codex` | install only for this client (default: every client found) |
| `--dry-run` | print the commands, change nothing |
| `--remote https://<host>/mcp` | do not install locally; connect to an existing always-on backend (e.g. your KSSH server over Tailscale) |
| `--name NAME` | connector name for `--remote` (default `kssh`) |

With a pipe, pass options after `sh -s --`:
```bash
wget -qO- https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh -s -- --codex
```

### Connect to a remote backend
```bash
sh install.sh --remote https://kssh.example.ts.net/mcp
```
The token is asked for without echo (or taken from `KSSH_TOKEN`). It is never printed and never put in the URL.
- Claude Code stores it in its user config as `Authorization: Bearer …`.
- Codex stores only the variable name (`bearer_token_env_var = "KSSH_TOKEN"`); add `export KSSH_TOKEN='…'` to your shell profile.

Give each friend **their own token**. Never send a token in a group chat or a public post.

## After installing

1. Add your servers to `~/.ssh-connector/servers.yaml` — see [`servers.example.yaml`](../servers.example.yaml). Use SSH keys, not passwords.
2. Restart Claude Code / Codex.
3. Ask: `version_info`, then `server_test` for one server.

Update later: run the same line again, or `claude plugin update ssh-connector@kssh-for-friends`.

Remove: `claude plugin uninstall ssh-connector@kssh-for-friends` / remove the plugin in Codex `/plugins`; `claude mcp remove kssh` / `codex mcp remove kssh` for `--remote`.

## Share with a friend

Copy and send:

> Try SSH Connector for Claude Code / Codex — run commands on your servers from the chat.
> Install: `wget -qO- https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh`
> Docs: https://github.com/ykinfohome7/KSSH-FOR-Friends/blob/main/docs/INSTALL.md

## Notes

- The plugin installs from the `main` branch.
- Codex: `codex plugin add` is tried; if your Codex version does not have it, enable `ssh-connector` in `/plugins`. The Codex steps were tested only with a fake `codex` command.
- ChatGPT / Claude apps (web, phone) cannot run the script; they connect to an HTTPS backend — see the README section on connectors.
