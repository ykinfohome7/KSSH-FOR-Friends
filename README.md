<div align="center">

# 🔐 KSSH for Friends

**Your own SSH connector for Claude Code and ChatGPT Codex — on your own server.**
Commands, terminals, SFTP, tunnels, 143 read-only command templates, SNMP and RDP checks. Self-hosted: it talks only to the servers you add.

[![Install for Claude Code](https://img.shields.io/badge/Install-Claude%20Code-D97757?style=for-the-badge)](docs/INSTALL.md)
[![Install for Codex](https://img.shields.io/badge/Install-ChatGPT%20Codex-10A37F?style=for-the-badge)](docs/INSTALL.md)
[![Own server](https://img.shields.io/badge/Self--hosted-Debian%20server-555?style=for-the-badge)](#-your-own-server-self-hosted)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-green)](LICENSE)

**English** · [Български](README.bg.md)

</div>

---

## ⚡ On your computer (one line)
```bash
wget -qO- https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh
```
Finds `claude` and/or `codex`, installs the plugin, creates `~/.ssh-connector/servers.yaml`. Review first, options, update, remove: **[docs/INSTALL.md](docs/INSTALL.md)**.

## 🖥️ Your own server (self-hosted)
On your Debian / Ubuntu server:
```bash
curl -fsSL https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install-server.sh | sudo bash
```
- Creates **your own** token, SSH key and service user; listens only on `127.0.0.1:8765`.
- HTTPS address, recommended: `sudo tailscale funnel --bg 8765` (no open router port). Own domain: `sudo PUBLIC_URL=https://kssh.example.com bash install-server.sh` behind a reverse proxy with a certificate.
- `sudo ssh-connector-info` shows what to paste into Claude / ChatGPT.
- Connect your Claude Code / Codex to it: `sh install.sh --remote https://<your-address>/mcp` (token asked hidden).

It never connects to anyone else's server. Add only your own hosts to `/etc/ssh-connector/servers.yaml`.

## 🚀 First steps
1. Add servers — see [`servers.example.yaml`](servers.example.yaml). Use SSH keys, not passwords.
2. Ask in plain words, e.g. *"show disk usage on web1"*, or call `version_info`, `server_test`, `help`.
3. Ready templates: **[docs/SNIPPETS.md](docs/SNIPPETS.md)**. All commands: **[docs/COMMANDS.md](docs/COMMANDS.md)**.

## 🛡️ Security
- Passwords, keys and tokens are never shown in the chat or written to the repository.
- One token per person. Anyone with your token can run commands on your servers.
- Report a vulnerability privately: **[SECURITY.md](SECURITY.md)**.

## 📚 Documentation
| | English | Български |
|---|---|---|
| Install | [INSTALL.md](docs/INSTALL.md) | [INSTALL.bg.md](docs/INSTALL.bg.md) |
| Commands | [COMMANDS.md](docs/COMMANDS.md) | [COMMANDS.bg.md](docs/COMMANDS.bg.md) |
| Templates | [SNIPPETS.md](docs/SNIPPETS.md) | [SNIPPETS.bg.md](docs/SNIPPETS.bg.md) |
| SNMP / RDP | [SNMP.md](docs/SNMP.md) · [RDP.md](docs/RDP.md) | |
| Security | [SECURITY-STANDARDS.md](docs/SECURITY-STANDARDS.md) | [SECURITY-STANDARDS.bg.md](docs/SECURITY-STANDARDS.bg.md) |

## 📄 License
[GPL-3.0](LICENSE) © ykinfohome7
