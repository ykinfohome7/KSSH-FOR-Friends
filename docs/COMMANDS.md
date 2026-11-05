# SSH Connector commands

Version: **1.3.3** · Commands: **48**

> Generated from the code by `scripts/gen_commands_docs.py` — do not edit by hand.
> _Генерира се автоматично от кода — не редактирайте на ръка._

Every command has a ready-to-run example: change only your own values and run it.
You can also just describe what you want in plain words to ChatGPT/Claude.

Passwords, keys and tokens are **never** shown or stored — only environment-variable names and file paths.
> _Пароли, ключове и токени никога не се показват — само имена на променливи и пътища._

## Contents

- [Info & help](#info--help) — `help`, `version_info`, `health_status`
- [Server profiles](#server-profiles) — `list_servers`, `server_get`, `server_add`, `server_update`, `server_rename`, `server_set_primary`, `server_failover`, `server_test`, `server_remove`
- [Credentials](#credentials) — `credentials_set`, `credentials_rotate`
- [Session settings](#session-settings) — `session_configure`
- [Tailscale](#tailscale) — `tailscale_status`, `tailscale_devices`, `tailscale_keys`, `tailscale_key_create`, `tailscale_key_revoke`
- [GitHub Codespaces](#github-codespaces) — `codespace_status`, `codespace_start`, `codespace_stop`
- [Commands](#commands) — `run_command`, `run_on_many`, `run_on_group`, `list_snippets`, `run_snippet`, `history`
- [Background jobs](#background-jobs) — `start_job`, `job_status`
- [Interactive terminal](#interactive-terminal) — `shell_open`, `shell_send`, `shell_close`, `list_shells`
- [Files (SFTP)](#files-sftp) — `sftp_list`, `sftp_read`, `sftp_write`, `sftp_upload`, `sftp_download`, `sftp_manage`
- [Port forwarding](#port-forwarding) — `port_forward`, `list_forwards`, `stop_forward`
- [Event watching](#event-watching) — `start_watch`, `stop_watch`, `get_events`

## Info & help

### `help`

List all commands with examples

| Parameter | Type | Required | Default |
|---|---|---|---|
| `command` | str | no | `""` |

**Example:**

```json
{"tool": "help", "arguments": {}}
```

### `version_info`

Version, commit and commands

_No parameters._

**Example:**

```json
{"tool": "version_info", "arguments": {}}
```

### `health_status`

Connector and connection health

_No parameters._

**Example:**

```json
{"tool": "health_status", "arguments": {}}
```

## Server profiles

### `list_servers`

List servers

| Parameter | Type | Required | Default |
|---|---|---|---|
| `tag` | str | no | `""` |

**Example:**

```json
{"tool": "list_servers", "arguments": {"tag": ""}}
```

### `server_get`

One profile, no secrets

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |

**Example:**

```json
{"tool": "server_get", "arguments": {"name": "SRV1"}}
```

### `server_add`

Add a server

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |
| `hosts` | list[str] | yes |  |
| `user` | str | no | `"admin"` |
| `port` | int | no | `22` |
| `tags` | list[str] | no | `[]` |
| `aliases` | list[str] | no | `[]` |
| `password_env` | str | no | `""` |

**Example:**

```json
{"tool": "server_add", "arguments": {"name": "web1", "hosts": ["10.0.0.5"], "user": "admin", "port": 22, "tags": ["web"], "aliases": [], "password_env": ""}}
```

### `server_update`

Update a server

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |
| `hosts` | list[str] | no | `[]` |
| `user` | str | no | `""` |
| `port` | int | no | `0` |
| `tags` | list[str] | no | `[]` |
| `aliases` | list[str] | no | `[]` |
| `password_env` | str | no | `""` |

**Example:**

```json
{"tool": "server_update", "arguments": {"name": "web1", "hosts": ["10.0.0.5", "10.0.0.6"]}}
```

### `server_rename`

Rename a server

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |
| `new_name` | str | yes |  |

**Example:**

```json
{"tool": "server_rename", "arguments": {"name": "web1", "new_name": "web-main"}}
```

### `server_set_primary`

Set the primary address

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |
| `host` | str | yes |  |

**Example:**

```json
{"tool": "server_set_primary", "arguments": {"name": "web1", "host": "10.0.0.6"}}
```

### `server_failover`

Switch to the next address now

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |

**Example:**

```json
{"tool": "server_failover", "arguments": {"name": "web1"}}
```

### `server_test`

Test a real SSH connection

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |

**Example:**

```json
{"tool": "server_test", "arguments": {"name": "web1"}}
```

### `server_remove`

Remove a profile (confirm required)

⚠️ **Requires explicit confirmation (`confirm: true`).** _Изисква потвърждение._

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |
| `confirm` | bool | no | `false` |

**Example:**

```json
{"tool": "server_remove", "arguments": {"name": "web1", "confirm": false}}
```

## Credentials

### `credentials_set`

Point to password env / key file (no secrets)

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |
| `password_env` | str | no | `""` |
| `key_path` | str | no | `""` |
| `clear_password` | bool | no | `false` |

**Example:**

```json
{"tool": "credentials_set", "arguments": {"name": "SRV1", "password_env": "SRV1_SSH_PASSWORD", "key_path": ""}}
```

### `credentials_rotate`

Rotate the SSH key (confirm required)

⚠️ **Requires explicit confirmation (`confirm: true`).** _Изисква потвърждение._

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes |  |
| `key_type` | str | no | `"ed25519"` |
| `confirm` | bool | no | `false` |
| `remove_old_key` | bool | no | `false` |

**Example:**

```json
{"tool": "credentials_rotate", "arguments": {"name": "SRV1", "key_type": "ed25519", "confirm": false, "remove_old_key": false}}
```

## Session settings

### `session_configure`

Timeouts, retries, keepalive, parallel sessions

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | no | `""` |
| `connect_timeout` | int | no | `0` |
| `retries` | int | no | `0` |
| `keepalive_interval` | int | no | `0` |
| `keepalive_count_max` | int | no | `0` |
| `command_timeout` | int | no | `0` |
| `max_sessions` | int | no | `0` |

**Example:**

```json
{"tool": "session_configure", "arguments": {"server": "SRV1", "connect_timeout": 10, "retries": 3, "command_timeout": 300}}
```

## Tailscale

### `tailscale_status`

Tailscale: version, device, account, Funnel

_No parameters._

**Example:**

```json
{"tool": "tailscale_status", "arguments": {}}
```

### `tailscale_devices`

Devices in the tailnet

| Parameter | Type | Required | Default |
|---|---|---|---|
| `online_only` | bool | no | `false` |

**Example:**

```json
{"tool": "tailscale_devices", "arguments": {"online_only": false}}
```

### `tailscale_keys`

List auth keys (no values)

_No parameters._

**Example:**

```json
{"tool": "tailscale_keys", "arguments": {}}
```

### `tailscale_key_create`

New auth key (confirm required)

⚠️ **Requires explicit confirmation (`confirm: true`).** _Изисква потвърждение._

| Parameter | Type | Required | Default |
|---|---|---|---|
| `description` | str | no | `"ssh-connector"` |
| `reusable` | bool | no | `true` |
| `ephemeral` | bool | no | `true` |
| `tags` | list[str] | no | `[]` |
| `expiry_days` | int | no | `90` |
| `confirm` | bool | no | `false` |

**Example:**

```json
{"tool": "tailscale_key_create", "arguments": {"description": "github-runner", "reusable": true, "ephemeral": true, "expiry_days": 90, "confirm": false}}
```

### `tailscale_key_revoke`

Revoke a compromised key (confirm required)

⚠️ **Requires explicit confirmation (`confirm: true`).** _Изисква потвърждение._

| Parameter | Type | Required | Default |
|---|---|---|---|
| `key_id` | str | yes |  |
| `confirm` | bool | no | `false` |

**Example:**

```json
{"tool": "tailscale_key_revoke", "arguments": {"key_id": "kXXXXXXXXXX", "confirm": false}}
```

## GitHub Codespaces

### `codespace_status`

List GitHub Codespaces or inspect one by exact name.

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | no | `""` |

**Example:**

```json
{"tool": "codespace_status", "arguments": {"name": ""}}
```

### `codespace_start`

Start/wake an existing GitHub Codespace via GitHub API.

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes | |

**Example:**

```json
{"tool": "codespace_start", "arguments": {"name": "your-codespace-name"}}
```

### `codespace_stop`

Stop Codespace only after `confirm=true`.

⚠️ **Requires explicit confirmation (`confirm: true`).** _Изисква потвърждение._

| Parameter | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes | |
| `confirm` | bool | no | `false` |

**Example:**

```json
{"tool": "codespace_stop", "arguments": {"name": "your-codespace-name", "confirm": false}}
```

**Permissions:** The connector host must have an authenticated `gh` CLI with Codespaces rights. A stopped Codespace cannot wake an MCP host running exclusively inside itself.

## Commands

### `run_command`

Run a command

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `command` | str | yes |  |
| `timeout` | int | no | `0` |
| `sudo` | bool | no | `false` |

**Example:**

```json
{"tool": "run_command", "arguments": {"server": "SRV1", "command": "uptime", "sudo": false}}
```

### `run_on_many`

Command on several servers

| Parameter | Type | Required | Default |
|---|---|---|---|
| `servers` | list[str] | yes |  |
| `command` | str | yes |  |
| `timeout` | int | no | `0` |
| `sudo` | bool | no | `false` |

**Example:**

```json
{"tool": "run_on_many", "arguments": {"servers": ["SRV1", "web1"], "command": "df -h"}}
```

### `run_on_group`

Command on a tag group

| Parameter | Type | Required | Default |
|---|---|---|---|
| `tag` | str | yes |  |
| `command` | str | yes |  |
| `timeout` | int | no | `0` |
| `sudo` | bool | no | `false` |

**Example:**

```json
{"tool": "run_on_group", "arguments": {"tag": "web", "command": "uptime"}}
```

### `list_snippets`

Saved commands

_No parameters._

**Example:**

```json
{"tool": "list_snippets", "arguments": {}}
```

### `run_snippet`

Run a saved command

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `name` | str | yes |  |
| `sudo` | bool | no | `false` |

**Example:**

```json
{"tool": "run_snippet", "arguments": {"server": "SRV1", "name": "disk"}}
```

### `history`

Command history

| Parameter | Type | Required | Default |
|---|---|---|---|
| `limit` | int | no | `50` |
| `server` | str | no | `""` |

**Example:**

```json
{"tool": "history", "arguments": {"limit": 20, "server": ""}}
```

## Background jobs

### `start_job`

Long background job

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `command` | str | yes |  |

**Example:**

```json
{"tool": "start_job", "arguments": {"server": "SRV1", "command": "apt-get update"}}
```

### `job_status`

Job status

| Parameter | Type | Required | Default |
|---|---|---|---|
| `job_id` | str | yes |  |
| `tail` | int | no | `50` |

**Example:**

```json
{"tool": "job_status", "arguments": {"job_id": "abcd1234", "tail": 50}}
```

## Interactive terminal

### `shell_open`

Open a terminal

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `cols` | int | no | `200` |
| `rows` | int | no | `50` |

**Example:**

```json
{"tool": "shell_open", "arguments": {"server": "SRV1"}}
```

### `shell_send`

Type into the terminal

| Parameter | Type | Required | Default |
|---|---|---|---|
| `shell_id` | str | yes |  |
| `input` | str | yes |  |
| `wait` | float | no | `5` |
| `enter` | bool | no | `true` |

**Example:**

```json
{"tool": "shell_send", "arguments": {"shell_id": "abcd1234", "input": "ls -la"}}
```

### `shell_close`

Close a terminal

| Parameter | Type | Required | Default |
|---|---|---|---|
| `shell_id` | str | yes |  |

**Example:**

```json
{"tool": "shell_close", "arguments": {"shell_id": "abcd1234"}}
```

### `list_shells`

Open terminals

_No parameters._

**Example:**

```json
{"tool": "list_shells", "arguments": {}}
```

## Files (SFTP)

### `sftp_list`

List a folder

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `path` | str | no | `"."` |

**Example:**

```json
{"tool": "sftp_list", "arguments": {"server": "SRV1", "path": "/var/log"}}
```

### `sftp_read`

Read a file

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `path` | str | yes |  |
| `max_bytes` | int | no | `200000` |

**Example:**

```json
{"tool": "sftp_read", "arguments": {"server": "SRV1", "path": "/etc/hostname"}}
```

### `sftp_write`

Write a file

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `path` | str | yes |  |
| `content` | str | yes |  |
| `append` | bool | no | `false` |

**Example:**

```json
{"tool": "sftp_write", "arguments": {"server": "SRV1", "path": "/tmp/a.txt", "content": "hello"}}
```

### `sftp_upload`

Upload a file

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `local_path` | str | yes |  |
| `remote_path` | str | yes |  |

**Example:**

```json
{"tool": "sftp_upload", "arguments": {"server": "SRV1", "local_path": "a.txt", "remote_path": "/tmp/a.txt"}}
```

### `sftp_download`

Download a file

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `remote_path` | str | yes |  |
| `local_path` | str | yes |  |

**Example:**

```json
{"tool": "sftp_download", "arguments": {"server": "SRV1", "remote_path": "/tmp/a.txt", "local_path": "a.txt"}}
```

### `sftp_manage`

mkdir / remove / rename

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `action` | str | yes |  |
| `path` | str | yes |  |
| `new_path` | str | no | `""` |

**Example:**

```json
{"tool": "sftp_manage", "arguments": {"server": "SRV1", "action": "mkdir", "path": "/tmp/new", "new_path": ""}}
```

## Port forwarding

### `port_forward`

Forward a port

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `local_port` | int | yes |  |
| `remote_host` | str | no | `"127.0.0.1"` |
| `remote_port` | int | no | `0` |

**Example:**

```json
{"tool": "port_forward", "arguments": {"server": "SRV1", "local_port": 15432, "remote_port": 5432}}
```

### `list_forwards`

Active tunnels

_No parameters._

**Example:**

```json
{"tool": "list_forwards", "arguments": {}}
```

### `stop_forward`

Stop a tunnel

| Parameter | Type | Required | Default |
|---|---|---|---|
| `forward_id` | str | yes |  |

**Example:**

```json
{"tool": "stop_forward", "arguments": {"forward_id": "abcd1234"}}
```

## Event watching

### `start_watch`

Watch errors, crashes, logins

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |
| `kinds` | list[str] | no | `["service_errors", "crashes", "logins"]` |

**Example:**

```json
{"tool": "start_watch", "arguments": {"server": "SRV1"}}
```

### `stop_watch`

Stop watching

| Parameter | Type | Required | Default |
|---|---|---|---|
| `server` | str | yes |  |

**Example:**

```json
{"tool": "stop_watch", "arguments": {"server": "SRV1"}}
```

### `get_events`

Collected events

| Parameter | Type | Required | Default |
|---|---|---|---|
| `since` | float | no | `0` |
| `server` | str | no | `""` |
| `limit` | int | no | `100` |

**Example:**

```json
{"tool": "get_events", "arguments": {"server": "", "limit": 50}}
```
