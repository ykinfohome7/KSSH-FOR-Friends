# Команди на SSH Connector

Версия: **1.3.3** · Команди: **48**

> Този файл се генерира автоматично от кода (`scripts/gen_commands_docs.py`). Не го редактирайте на ръка.

Всяка команда има готов пример: сменяте само своите стойности и пускате.
Може и свободно — просто опишете на ChatGPT/Claude какво искате.

Пароли, ключове и токени **никога** не се показват и не се записват — само имена на променливи и пътища.

## Съдържание

- [Информация и помощ](#информация-и-помощ) — `help`, `version_info`, `health_status`
- [Сървърни профили](#сървърни-профили) — `list_servers`, `server_get`, `server_add`, `server_update`, `server_rename`, `server_set_primary`, `server_failover`, `server_test`, `server_remove`
- [Данни за достъп](#данни-за-достъп) — `credentials_set`, `credentials_rotate`
- [Настройки на сесията](#настройки-на-сесията) — `session_configure`
- [Tailscale](#tailscale) — `tailscale_status`, `tailscale_devices`, `tailscale_keys`, `tailscale_key_create`, `tailscale_key_revoke`
- [GitHub Codespaces](#github-codespaces) — `codespace_status`, `codespace_start`, `codespace_stop`
- [Команди](#команди) — `run_command`, `run_on_many`, `run_on_group`, `list_snippets`, `run_snippet`, `history`
- [Задачи във фонов режим](#задачи-във-фонов-режим) — `start_job`, `job_status`
- [Интерактивен терминал](#интерактивен-терминал) — `shell_open`, `shell_send`, `shell_close`, `list_shells`
- [Файлове (SFTP)](#файлове-sftp) — `sftp_list`, `sftp_read`, `sftp_write`, `sftp_upload`, `sftp_download`, `sftp_manage`
- [Тунели към портове](#тунели-към-портове) — `port_forward`, `list_forwards`, `stop_forward`
- [Наблюдение на събития](#наблюдение-на-събития) — `start_watch`, `stop_watch`, `get_events`

## Информация и помощ

### `help`

Списък с всички команди и примери

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `command` | str | не | `""` |

**Пример:**

```json
{"tool": "help", "arguments": {}}
```

### `version_info`

Версия, commit и команди

_Без параметри._

**Пример:**

```json
{"tool": "version_info", "arguments": {}}
```

### `health_status`

Здраве на конектора и връзките

_Без параметри._

**Пример:**

```json
{"tool": "health_status", "arguments": {}}
```

## Сървърни профили

### `list_servers`

Списък със сървърите

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `tag` | str | не | `""` |

**Пример:**

```json
{"tool": "list_servers", "arguments": {"tag": ""}}
```

### `server_get`

Един профил, без тайни

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |

**Пример:**

```json
{"tool": "server_get", "arguments": {"name": "SRV1"}}
```

### `server_add`

Добавя сървър

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |
| `hosts` | list[str] | да |  |
| `user` | str | не | `"admin"` |
| `port` | int | не | `22` |
| `tags` | list[str] | не | `[]` |
| `aliases` | list[str] | не | `[]` |
| `password_env` | str | не | `""` |

**Пример:**

```json
{"tool": "server_add", "arguments": {"name": "web1", "hosts": ["10.0.0.5"], "user": "admin", "port": 22, "tags": ["web"], "aliases": [], "password_env": ""}}
```

### `server_update`

Променя сървър

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |
| `hosts` | list[str] | не | `[]` |
| `user` | str | не | `""` |
| `port` | int | не | `0` |
| `tags` | list[str] | не | `[]` |
| `aliases` | list[str] | не | `[]` |
| `password_env` | str | не | `""` |

**Пример:**

```json
{"tool": "server_update", "arguments": {"name": "web1", "hosts": ["10.0.0.5", "10.0.0.6"]}}
```

### `server_rename`

Преименува сървър

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |
| `new_name` | str | да |  |

**Пример:**

```json
{"tool": "server_rename", "arguments": {"name": "web1", "new_name": "web-main"}}
```

### `server_set_primary`

Сменя основния адрес

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |
| `host` | str | да |  |

**Пример:**

```json
{"tool": "server_set_primary", "arguments": {"name": "web1", "host": "10.0.0.6"}}
```

### `server_failover`

Превключва към следващия адрес сега

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |

**Пример:**

```json
{"tool": "server_failover", "arguments": {"name": "web1"}}
```

### `server_test`

Проверява реална SSH връзка

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |

**Пример:**

```json
{"tool": "server_test", "arguments": {"name": "web1"}}
```

### `server_remove`

Маха профил (само с потвърждение)

⚠️ **Изисква изрично потвърждение (`confirm: true`).**

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |
| `confirm` | bool | не | `false` |

**Пример:**

```json
{"tool": "server_remove", "arguments": {"name": "web1", "confirm": false}}
```

## Данни за достъп

### `credentials_set`

Задава откъде да се вземе паролата/ключа (без тайни)

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |
| `password_env` | str | не | `""` |
| `key_path` | str | не | `""` |
| `clear_password` | bool | не | `false` |

**Пример:**

```json
{"tool": "credentials_set", "arguments": {"name": "SRV1", "password_env": "SRV1_SSH_PASSWORD", "key_path": ""}}
```

### `credentials_rotate`

Сменя SSH ключа (само с потвърждение)

⚠️ **Изисква изрично потвърждение (`confirm: true`).**

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да |  |
| `key_type` | str | не | `"ed25519"` |
| `confirm` | bool | не | `false` |
| `remove_old_key` | bool | не | `false` |

**Пример:**

```json
{"tool": "credentials_rotate", "arguments": {"name": "SRV1", "key_type": "ed25519", "confirm": false, "remove_old_key": false}}
```

## Настройки на сесията

### `session_configure`

Таймаути, опити, keepalive, паралелни сесии

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | не | `""` |
| `connect_timeout` | int | не | `0` |
| `retries` | int | не | `0` |
| `keepalive_interval` | int | не | `0` |
| `keepalive_count_max` | int | не | `0` |
| `command_timeout` | int | не | `0` |
| `max_sessions` | int | не | `0` |

**Пример:**

```json
{"tool": "session_configure", "arguments": {"server": "SRV1", "connect_timeout": 10, "retries": 3, "command_timeout": 300}}
```

## Tailscale

### `tailscale_status`

Tailscale: версия, устройство, акаунт, Funnel

_Без параметри._

**Пример:**

```json
{"tool": "tailscale_status", "arguments": {}}
```

### `tailscale_devices`

Устройства в Tailscale мрежата

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `online_only` | bool | не | `false` |

**Пример:**

```json
{"tool": "tailscale_devices", "arguments": {"online_only": false}}
```

### `tailscale_keys`

Списък с auth ключове (без стойностите)

_Без параметри._

**Пример:**

```json
{"tool": "tailscale_keys", "arguments": {}}
```

### `tailscale_key_create`

Нов auth ключ (само с потвърждение)

⚠️ **Изисква изрично потвърждение (`confirm: true`).**

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `description` | str | не | `"ssh-connector"` |
| `reusable` | bool | не | `true` |
| `ephemeral` | bool | не | `true` |
| `tags` | list[str] | не | `[]` |
| `expiry_days` | int | не | `90` |
| `confirm` | bool | не | `false` |

**Пример:**

```json
{"tool": "tailscale_key_create", "arguments": {"description": "github-runner", "reusable": true, "ephemeral": true, "expiry_days": 90, "confirm": false}}
```

### `tailscale_key_revoke`

Отнема компрометиран ключ (само с потвърждение)

⚠️ **Изисква изрично потвърждение (`confirm: true`).**

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `key_id` | str | да |  |
| `confirm` | bool | не | `false` |

**Пример:**

```json
{"tool": "tailscale_key_revoke", "arguments": {"key_id": "kXXXXXXXXXX", "confirm": false}}
```

## GitHub Codespaces

### `codespace_status`

Показва всички GitHub Codespaces или един по точно име.

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | не | `""` |

**Пример:**

```json
{"tool": "codespace_status", "arguments": {"name": ""}}
```

### `codespace_start`

Стартира или събужда Codespace през GitHub API.

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да | |

**Пример:**

```json
{"tool": "codespace_start", "arguments": {"name": "your-codespace-name"}}
```

### `codespace_stop`

Спира Codespace само след `confirm=true`.

⚠️ **Изисква изрично потвърждение (`confirm: true`).**

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `name` | str | да | |
| `confirm` | bool | не | `false` |

**Пример:**

```json
{"tool": "codespace_stop", "arguments": {"name": "your-codespace-name", "confirm": false}}
```

**Права:** CLI `gh` трябва да е удостоверен с Codespaces разрешения. Спрян Codespace не може да събуди MCP конектор, работещ единствено вътре в него.

## Команди

### `run_command`

Изпълнява команда

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `command` | str | да |  |
| `timeout` | int | не | `0` |
| `sudo` | bool | не | `false` |

**Пример:**

```json
{"tool": "run_command", "arguments": {"server": "SRV1", "command": "uptime", "sudo": false}}
```

### `run_on_many`

Команда на няколко сървъра

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `servers` | list[str] | да |  |
| `command` | str | да |  |
| `timeout` | int | не | `0` |
| `sudo` | bool | не | `false` |

**Пример:**

```json
{"tool": "run_on_many", "arguments": {"servers": ["SRV1", "web1"], "command": "df -h"}}
```

### `run_on_group`

Команда на група (таг)

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `tag` | str | да |  |
| `command` | str | да |  |
| `timeout` | int | не | `0` |
| `sudo` | bool | не | `false` |

**Пример:**

```json
{"tool": "run_on_group", "arguments": {"tag": "web", "command": "uptime"}}
```

### `list_snippets`

Запазени команди

_Без параметри._

**Пример:**

```json
{"tool": "list_snippets", "arguments": {}}
```

### `run_snippet`

Пуска запазена команда

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `name` | str | да |  |
| `sudo` | bool | не | `false` |

**Пример:**

```json
{"tool": "run_snippet", "arguments": {"server": "SRV1", "name": "disk"}}
```

### `history`

История на командите

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `limit` | int | не | `50` |
| `server` | str | не | `""` |

**Пример:**

```json
{"tool": "history", "arguments": {"limit": 20, "server": ""}}
```

## Задачи във фонов режим

### `start_job`

Дълга задача във фонов режим

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `command` | str | да |  |

**Пример:**

```json
{"tool": "start_job", "arguments": {"server": "SRV1", "command": "apt-get update"}}
```

### `job_status`

Състояние на задача

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `job_id` | str | да |  |
| `tail` | int | не | `50` |

**Пример:**

```json
{"tool": "job_status", "arguments": {"job_id": "abcd1234", "tail": 50}}
```

## Интерактивен терминал

### `shell_open`

Отваря терминал

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `cols` | int | не | `200` |
| `rows` | int | не | `50` |

**Пример:**

```json
{"tool": "shell_open", "arguments": {"server": "SRV1"}}
```

### `shell_send`

Пише в терминала

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `shell_id` | str | да |  |
| `input` | str | да |  |
| `wait` | float | не | `5` |
| `enter` | bool | не | `true` |

**Пример:**

```json
{"tool": "shell_send", "arguments": {"shell_id": "abcd1234", "input": "ls -la"}}
```

### `shell_close`

Затваря терминал

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `shell_id` | str | да |  |

**Пример:**

```json
{"tool": "shell_close", "arguments": {"shell_id": "abcd1234"}}
```

### `list_shells`

Отворени терминали

_Без параметри._

**Пример:**

```json
{"tool": "list_shells", "arguments": {}}
```

## Файлове (SFTP)

### `sftp_list`

Файлове в папка

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `path` | str | не | `"."` |

**Пример:**

```json
{"tool": "sftp_list", "arguments": {"server": "SRV1", "path": "/var/log"}}
```

### `sftp_read`

Чете файл

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `path` | str | да |  |
| `max_bytes` | int | не | `200000` |

**Пример:**

```json
{"tool": "sftp_read", "arguments": {"server": "SRV1", "path": "/etc/hostname"}}
```

### `sftp_write`

Записва файл

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `path` | str | да |  |
| `content` | str | да |  |
| `append` | bool | не | `false` |

**Пример:**

```json
{"tool": "sftp_write", "arguments": {"server": "SRV1", "path": "/tmp/a.txt", "content": "hello"}}
```

### `sftp_upload`

Качва файл

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `local_path` | str | да |  |
| `remote_path` | str | да |  |

**Пример:**

```json
{"tool": "sftp_upload", "arguments": {"server": "SRV1", "local_path": "a.txt", "remote_path": "/tmp/a.txt"}}
```

### `sftp_download`

Сваля файл

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `remote_path` | str | да |  |
| `local_path` | str | да |  |

**Пример:**

```json
{"tool": "sftp_download", "arguments": {"server": "SRV1", "remote_path": "/tmp/a.txt", "local_path": "a.txt"}}
```

### `sftp_manage`

mkdir / remove / rename

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `action` | str | да |  |
| `path` | str | да |  |
| `new_path` | str | не | `""` |

**Пример:**

```json
{"tool": "sftp_manage", "arguments": {"server": "SRV1", "action": "mkdir", "path": "/tmp/new", "new_path": ""}}
```

## Тунели към портове

### `port_forward`

Тунел към порт

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `local_port` | int | да |  |
| `remote_host` | str | не | `"127.0.0.1"` |
| `remote_port` | int | не | `0` |

**Пример:**

```json
{"tool": "port_forward", "arguments": {"server": "SRV1", "local_port": 15432, "remote_port": 5432}}
```

### `list_forwards`

Активни тунели

_Без параметри._

**Пример:**

```json
{"tool": "list_forwards", "arguments": {}}
```

### `stop_forward`

Спира тунел

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `forward_id` | str | да |  |

**Пример:**

```json
{"tool": "stop_forward", "arguments": {"forward_id": "abcd1234"}}
```

## Наблюдение на събития

### `start_watch`

Следи грешки, сривове, влизания

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |
| `kinds` | list[str] | не | `["service_errors", "crashes", "logins"]` |

**Пример:**

```json
{"tool": "start_watch", "arguments": {"server": "SRV1"}}
```

### `stop_watch`

Спира следенето

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `server` | str | да |  |

**Пример:**

```json
{"tool": "stop_watch", "arguments": {"server": "SRV1"}}
```

### `get_events`

Събрани събития

| Параметър | Тип | Задължителен | По подразбиране |
|---|---|---|---|
| `since` | float | не | `0` |
| `server` | str | не | `""` |
| `limit` | int | не | `100` |

**Пример:**

```json
{"tool": "get_events", "arguments": {"server": "", "limit": 50}}
```
