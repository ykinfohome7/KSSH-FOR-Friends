<div align="center">

# 🔐 KSSH for Friends

**Собствен SSH конектор за Claude Code и ChatGPT Codex — на твой собствен сървър.**
Команди, терминали, SFTP, тунели, 143 готови шаблона само за четене, SNMP и RDP проверки. Самостоятелен: говори само със сървърите, които ти добавиш.

[![Инсталирай за Claude Code](https://img.shields.io/badge/Инсталирай-Claude%20Code-D97757?style=for-the-badge)](docs/INSTALL.bg.md)
[![Инсталирай за Codex](https://img.shields.io/badge/Инсталирай-ChatGPT%20Codex-10A37F?style=for-the-badge)](docs/INSTALL.bg.md)
[![Собствен сървър](https://img.shields.io/badge/Собствен-Debian%20сървър-555?style=for-the-badge)](#-собствен-сървър-self-hosted)
[![Лиценз: GPL-3.0](https://img.shields.io/badge/лиценз-GPL--3.0-green)](LICENSE)

[English](README.md) · **Български**

</div>

---

## ⚡ На твоя компютър (с един ред)
```bash
wget -qO- https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install.sh | sh
```
Намира `claude` и/или `codex`, инсталира плъгина и създава `~/.ssh-connector/servers.yaml`. Преглед, опции, обновяване, премахване: **[docs/INSTALL.bg.md](docs/INSTALL.bg.md)**.

## 🖥️ Собствен сървър (self-hosted)
На твоя Debian / Ubuntu сървър:
```bash
curl -fsSL https://raw.githubusercontent.com/ykinfohome7/KSSH-FOR-Friends/main/install-server.sh | sudo bash
```
- Създава **твой собствен** токен, SSH ключ и служебен потребител; слуша само на `127.0.0.1:8765`.
- HTTPS адрес, препоръчително: `sudo tailscale funnel --bg 8765` (без отворен порт в рутера). Собствен домейн: `sudo PUBLIC_URL=https://kssh.example.com bash install-server.sh` зад reverse proxy със сертификат.
- `sudo ssh-connector-info` показва какво да поставиш в Claude / ChatGPT.
- Свържи своя Claude Code / Codex към него: `sh install.sh --remote https://<твоят-адрес>/mcp` (токенът се пита скрито).

Никога не се свързва с чужд сървър. Добавяй само свои хостове в `/etc/ssh-connector/servers.yaml`.

## 🚀 Първи стъпки
1. Добави сървъри — виж [`servers.example.yaml`](servers.example.yaml). Използвай SSH ключове, не пароли.
2. Питай с обикновени думи, например *„покажи заетото място на web1“*, или извикай `version_info`, `server_test`, `help`.
3. Готови шаблони: **[docs/SNIPPETS.bg.md](docs/SNIPPETS.bg.md)**. Всички команди: **[docs/COMMANDS.bg.md](docs/COMMANDS.bg.md)**.

## 🛡️ Сигурност
- Пароли, ключове и токени никога не се показват в чата и не се записват в хранилището.
- Отделен токен за всеки човек. Който има твоя токен, може да пуска команди на твоите сървъри.
- Уязвимост съобщавай частно: **[SECURITY.md](SECURITY.md)**.

## 📚 Документация
| | English | Български |
|---|---|---|
| Инсталиране | [INSTALL.md](docs/INSTALL.md) | [INSTALL.bg.md](docs/INSTALL.bg.md) |
| Команди | [COMMANDS.md](docs/COMMANDS.md) | [COMMANDS.bg.md](docs/COMMANDS.bg.md) |
| Шаблони | [SNIPPETS.md](docs/SNIPPETS.md) | [SNIPPETS.bg.md](docs/SNIPPETS.bg.md) |
| SNMP / RDP | [SNMP.md](docs/SNMP.md) · [RDP.md](docs/RDP.md) | |
| Сигурност | [SECURITY-STANDARDS.md](docs/SECURITY-STANDARDS.md) | [SECURITY-STANDARDS.bg.md](docs/SECURITY-STANDARDS.bg.md) |

## 📄 Лиценз
[GPL-3.0](LICENSE) © ykinfohome7
