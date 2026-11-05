# Инсталиране на SSH Connector

[English](INSTALL.md) · **Български**

Работи с **Claude Code** и **ChatGPT Codex** на Linux и macOS. Нужни са `python3` ≥ 3.10 (с `venv`) и командата на клиента (`claude` или `codex`). Без `sudo`.

## 1. С един ред (препоръчително)

```bash
wget -qO- https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh
```
или
```bash
curl -fsSL https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh
```

Скриптът намира `claude` и/или `codex`, добавя каталога с плъгини, инсталира плъгина и създава празен `~/.ssh-connector/servers.yaml` (права 600), ако нямаш такъв. После рестартирай клиента и попитай `version_info`.

## 2. Първо прегледай скрипта (за внимателните админи)

```bash
wget -O install.sh https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh
less install.sh          # прочети го
sh install.sh --dry-run  # показва командите, без да променя нищо
sh install.sh
```

## Опции

| Опция | Какво прави |
|---|---|
| `--claude` / `--codex` | инсталира само за този клиент (по подразбиране: за всички намерени) |
| `--dry-run` | показва командите, без да променя нищо |
| `--remote https://<хост>/mcp` | без локална инсталация; свързва се с вече работещ сървър (напр. твоя KSSH през Tailscale) |
| `--name ИМЕ` | име на конектора при `--remote` (по подразбиране `kssh`) |

При pipe опциите се пишат след `sh -s --`:
```bash
wget -qO- https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh -s -- --codex
```

### Свързване към отдалечен сървър
```bash
sh install.sh --remote https://kssh.example.ts.net/mcp
```
Токенът се пита скрито (или се взима от `KSSH_TOKEN`). Никога не се показва и не се слага в адреса.
- Claude Code го пази в своите настройки като `Authorization: Bearer …`.
- Codex пази само името на променливата (`bearer_token_env_var = "KSSH_TOKEN"`); добави `export KSSH_TOKEN='…'` в профила на shell-а.

Давай на всеки приятел **отделен токен**. Не пращай токен в групов чат или публичен пост.

## След инсталиране

1. Добави сървърите си в `~/.ssh-connector/servers.yaml` — виж [`servers.example.yaml`](../servers.example.yaml). Използвай SSH ключове, не пароли.
2. Рестартирай Claude Code / Codex.
3. Попитай: `version_info`, после `server_test` за един сървър.

Обновяване: пусни същия ред отново или `claude plugin update ssh-connector@kssh-for-friends`.

Премахване: `claude plugin uninstall ssh-connector@kssh-for-friends` / махни плъгина в Codex от `/plugins`; при `--remote`: `claude mcp remove kssh` / `codex mcp remove kssh`.

## Сподели с приятел

Копирай и изпрати:

> Пробвай SSH Connector за Claude Code / Codex — командваш сървърите си от чата.
> Инсталиране: `wget -qO- https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh`
> Упътване: https://github.com/ykinfohome7/KSSH-FOR-Friends/blob/main/docs/INSTALL.bg.md

## Бележки

- Плъгинът се инсталира от branch `main`.
- Codex: опитва се `codex plugin add`; ако твоята версия на Codex го няма, включи `ssh-connector` от `/plugins`. Стъпките за Codex са тествани само с фалшива команда `codex`.
- Приложенията ChatGPT / Claude (уеб, телефон) не могат да пуснат скрипта; те се свързват към HTTPS сървър — виж раздела за конектори в README.
