# Plugin update and connector address

[English](#english) · [Български](#български)

## English

### 1. Two different things: plugin update vs. connector Refresh

| | Claude Code / Codex **plugin** | ChatGPT / Claude apps **remote connector** |
|---|---|---|
| Transport | local **stdio**: the app starts `bin/run.sh` itself | **HTTP** (streamable HTTP) to a running backend |
| Needs an address? | **no** | yes: `https://<host>/...` |
| Where the code runs | the plugin copy in the app's plugin cache | the backend host (always-on server or GitHub Actions session) |
| How it gets new code | **plugin update** (below) | **redeploy/restart the backend**, then **Refresh** |
| What *Refresh* does | n/a | only re-reads the tool list from the backend; it does not update code |

### 2. Updating the plugin (version 1.4.1, 60 tools)

Claude Code:

```bash
claude plugin marketplace update kssh-for-friends
claude plugin update ssh-connector@kssh-for-friends
```

Then restart the Claude Code session. Check with the `version_info` tool: `version` must be `1.4.1`, `commands` must list 60 names including `snmp_health` … `snmp_interface_stats` and `rdp_check`, `rdp_tunnel`, `windows_health`, `rdp_sessions`.

Before PR #14 is merged, the marketplace on `main` still serves 1.3.3. To try this branch, use a local checkout:

```bash
git clone https://github.com/ykinfohome7/KSSH-FOR-Friends
claude plugin marketplace add ./ssh-connector
claude plugin install ssh-connector@kssh-for-friends
```

Codex: update the `kssh-for-friends` marketplace (re-run `codex plugin marketplace add ykinfohome7/KSSH-FOR-Friends`), enable/upgrade **ssh-connector** in the plugin list and restart Codex. *The Codex CLI was not available in the development environment, so these Codex steps were not executed.*

Dependencies: `requirements.txt` did not change in 1.4.1, so the launcher reuses the existing private virtualenv. The SNMP backend (`pysnmp`) is **optional and not installed**: without it the `snmp_*` tools answer `UNKNOWN / DEPENDENCY_MISSING` and every other tool keeps working. Installing `pysnmp` into the plugin virtualenv is possible but **untested** (see `docs/SNMP.md`).

### 3. Updating a remote connector (ChatGPT / Claude apps)

1. Update the code on the backend host (e.g. always-on server: pull the new version and restart the `ssh-connector` service; GitHub Actions session: start a new session).
2. In the app, press **Refresh** on the connector.

### 4. Where can the connector be reached? (`tailscale_status`)

`tailscale_status` now returns an `endpoint` section. It is read-only: it runs only `tailscale status --json`, `tailscale serve status --json` and `tailscale version`, never `up`, `login`, `serve`, `funnel` or key creation.

| Field | Meaning |
|---|---|
| `transport.mode` | `stdio` (local plugin, no address needed) or `http` (backend, with host/port) |
| `configured_address` | from `SSH_CONNECTOR_PUBLIC_URL` or the file in `SSH_CONNECTOR_PUBLIC_URL_FILE`; reduced to `https://host[:port]` (any path, e.g. a secret token path, is removed) |
| `exposure.mode` | `funnel` (public internet), `serve` (tailnet only, **not public**), `none`, or `unknown` |
| `exposure.public_internet` | `configured_not_verified`, `no` or `unknown`; never "yes" from configuration alone |
| `reachability` | `not_checked` unless called with `verify_reachability=true`; then one GET without credentials to `<configured>/mcp`, expecting `401`. Checked from the connector host only, so it does **not** prove internet access |
| `mcp_endpoints` | safe templates: `https://<host>/<TOKEN>/mcp` (ChatGPT secret path) and `https://<host>/mcp` + `Authorization: Bearer <TOKEN>` (Claude) |

No address is guessed from the repository name, the device name or `HostName`. If data is missing, the fields say `unknown` / `not configured` with a reason, and no working link is claimed.

Secrets: the token is never shown. Token-bearing URLs (`/<TOKEN>/mcp`), bearer values and `tskey-…` keys are redacted from tool output. The HTTP backend runs with the uvicorn access log disabled because it would print the secret path. Tailscale auth keys are never accepted as MCP arguments.

## Български

### 1. Две различни неща: обновяване на плъгина и Refresh на конектора

| | **Плъгин** за Claude Code / Codex | **Отдалечен конектор** в ChatGPT / Claude |
|---|---|---|
| Връзка | локален **stdio**: приложението само пуска `bin/run.sh` | **HTTP** към работещ сървър |
| Нужен ли е адрес? | **не** | да: `https://<host>/...` |
| Къде работи кодът | копието на плъгина в кеша на приложението | сървъра (постоянен сървър или сесия в GitHub Actions) |
| Как получава нов код | **обновяване на плъгина** (по-долу) | **обновяване/рестарт на сървъра**, после **Refresh** |
| Какво прави *Refresh* | — | само зарежда наново списъка с команди; кода не го обновява |

### 2. Обновяване на плъгина (версия 1.4.1, 60 команди)

Claude Code:

```bash
claude plugin marketplace update kssh-for-friends
claude plugin update ssh-connector@kssh-for-friends
```

После рестартирай сесията на Claude Code. Провери с `version_info`: версия `1.4.1` и 60 команди, включително `snmp_health` … `snmp_interface_stats` и четирите `rdp_*` / `windows_health`.

Докато PR #14 не е слят, `main` дава 1.3.3. За проба на този клон използвай локално копие (командите са в английската част горе).

Codex: обнови marketplace-а `kssh-for-friends` (отново `codex plugin marketplace add ykinfohome7/KSSH-FOR-Friends`), обнови **ssh-connector** от списъка с плъгини и рестартирай Codex. *Codex CLI липсваше в средата за разработка — тези стъпки не са изпълнени.*

Зависимости: `requirements.txt` не е променен, затова launcher-ът използва същата виртуална среда. `pysnmp` е **по избор и не е инсталиран**: без него `snmp_*` връщат `UNKNOWN / DEPENDENCY_MISSING`, а всички други команди работят.

### 3. Обновяване на отдалечен конектор

1. Обнови кода на сървъра (постоянен сървър: новата версия + рестарт на услугата `ssh-connector`; GitHub Actions: нова сесия).
2. В приложението натисни **Refresh** на конектора.

### 4. Къде е достъпен конекторът? (`tailscale_status`)

`tailscale_status` вече връща раздел `endpoint` — само четене (`tailscale status --json`, `tailscale serve status --json`, `tailscale version`; никога `up`, `login`, `serve`, `funnel` или нов ключ).

- `transport.mode`: `stdio` (локален плъгин — не трябва адрес) или `http` (сървър).
- `configured_address`: от `SSH_CONNECTOR_PUBLIC_URL` / `SSH_CONNECTOR_PUBLIC_URL_FILE`, само `https://host[:port]` — пътят (напр. таен токен) се маха.
- `exposure.mode`: `funnel` (публичен), `serve` (само в tailnet, **не е публичен**), `none` или `unknown`.
- `reachability`: `not_checked`, освен с `verify_reachability=true` — една заявка без токен към `<адрес>/mcp`, очаква се `401`. Проверката е от самия сървър и **не доказва** достъп от интернет.
- `mcp_endpoints`: безопасни шаблони `https://<host>/<TOKEN>/mcp` и `https://<host>/mcp` + `Authorization: Bearer <TOKEN>`.

Адрес не се измисля от името на хранилището или устройството. При липсващи данни полетата казват `unknown` с причина, без фиктивен работещ линк. Токенът никога не се показва; URL с токен, bearer стойности и `tskey-…` ключове се скриват.
