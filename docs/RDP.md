# RDP / Windows commands (read-only)

[English](#english) · [Български](#български)

## English

Four MCP commands. None of them logs in over RDP, sends a password or changes a Windows host.

| Command | For | What it does |
|---|---|---|
| `rdp_check(target)` | user + Claude Code / Codex | RDP negotiation on the target port: is it RDP, is **NLA enforced**, is legacy RDP security (no TLS) accepted, TLS certificate (**verified only**), latency |
| `rdp_tunnel(target, local_port=0)` | user | local SSH tunnel `127.0.0.1:<port>` → target through its `via` SSH server, plus a ready `.rdp` file (no password). Close it with `stop_forward` |
| `windows_health(server)` | Claude Code / Codex | over the SSH profile `server` (OpenSSH on Windows): OS, last boot, disks, stopped automatic services, pending reboot, RDP enabled / NLA / port / firewall rules. One fixed PowerShell script (`-EncodedCommand`) |
| `rdp_sessions(server)` | user + agents | `quser` over SSH: user, session, state, idle, logon time |

Why no full RDP desktop session: Claude Code and Codex work with commands and text, not screens. For them, `windows_health`, `rdp_sessions` and the existing `run_command` / `shell_open` over OpenSSH on Windows are the useful interface. People use their own RDP client through `rdp_tunnel`.

### Configuration (`servers.yaml`)

```yaml
rdp:
  allowed_networks:        # required; every target host must be inside
    - 192.0.2.20/32
  targets:
    win-server:
      host: 192.0.2.20
      port: 3389
      via: SRV1            # optional SSH server profile (needed for rdp_tunnel)
      server_name: win-server.example.lan   # name expected in the TLS certificate
      # ca_file: ~/.ssh-connector/win-server-rdp.pem
      user: EXAMPLE\administrator           # only written into the .rdp file
      timeout: 5
```

`windows_health` and `rdp_sessions` use the normal SSH profiles (`servers:`), so the Windows host needs OpenSSH Server.

### Results

* `status`: `UP`, `DOWN` (unreachable, timeout, not RDP) or `UNKNOWN` (our configuration, e.g. unknown target).
* `nla`: `required`, `supported_not_required` or `not_supported`.
* `tls.verified: false` is normal for the default self-signed RDP certificate. The connector never accepts an unverified certificate. To verify it, export the certificate and set `ca_file`.
* `warnings`: NLA not enforced, legacy security accepted, untrusted or soon-expiring certificate, low disk, pending reboot, stopped services.

### Security

* Mandatory `rdp.allowed_networks`; only configured target names are accepted.
* `rdp_check` sends only the X.224 negotiation request (and a TLS ClientHello). It sends no credentials.
* `rdp_tunnel` listens on `127.0.0.1` only. In remote HTTP mode, the tunnel is on the backend host, and the result says so.
* Every call is written to `history` as `rdp:<command> <target>`.

## Български

Четири MCP команди. Нито една не влиза през RDP, не изпраща парола и не променя Windows.

| Команда | За кого | Какво прави |
|---|---|---|
| `rdp_check(target)` | потребител + Claude Code / Codex | RDP договаряне на порта: RDP ли е, **задължителен ли е NLA**, приема ли се старата защита без TLS, TLS сертификат (**само проверен**), време за отговор |
| `rdp_tunnel(target, local_port=0)` | потребител | локален SSH тунел `127.0.0.1:<порт>` → целта през нейния `via` SSH сървър и готов `.rdp` файл (без парола). Затваря се със `stop_forward` |
| `windows_health(server)` | Claude Code / Codex | през SSH профила `server` (OpenSSH на Windows): ОС, последно пускане, дискове, спрени автоматични услуги, чакащ рестарт, RDP / NLA / порт / правила в защитната стена. Един фиксиран PowerShell скрипт |
| `rdp_sessions(server)` | потребител + агенти | `quser` през SSH: потребител, сесия, състояние, бездействие, време на вход |

Защо няма пълна RDP сесия с екран: Claude Code и Codex работят с команди и текст, не с екрани. За тях полезни са `windows_health`, `rdp_sessions` и съществуващите `run_command` / `shell_open` през OpenSSH. Хората използват своя RDP клиент през `rdp_tunnel`.

`tls.verified: false` е нормално за самоподписания сертификат на RDP по подразбиране. Конекторът никога не приема непроверен сертификат. За проверка задай `ca_file`. Настройката е в раздел `rdp` в `servers.yaml` (пример горе).
