"""Generate docs/COMMANDS.md (English) and docs/COMMANDS.bg.md (Bulgarian) from the code.
Генерира документацията на командите от кода: имена, параметри, описание и готов пример.

Run / Пускане:  python3 scripts/gen_commands_docs.py
Check only / Само проверка (за тестове):  python3 scripts/gen_commands_docs.py --check
"""
import inspect
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "ssh-connector"))
os.environ.setdefault("SSH_CONNECTOR_CONFIG", str(ROOT / "servers.example.yaml"))
import server  # noqa: E402

# Групи команди — същият ред в двата документа
GROUPS = [
    ("Info & help", "Информация и помощ", ["help", "version_info", "health_status"]),
    ("Server profiles", "Сървърни профили",
     ["list_servers", "server_get", "server_add", "server_update", "server_rename", "server_set_primary",
      "server_failover", "server_test", "server_remove"]),
    ("Credentials", "Данни за достъп", ["credentials_set", "credentials_rotate", "sudo_password"]),
    ("Session settings", "Настройки на сесията", ["session_configure"]),
    ("Tailscale", "Tailscale", ["tailscale_status", "tailscale_devices", "tailscale_keys", "tailscale_key_create",
                                "tailscale_key_revoke"]),
    ("GitHub Codespaces", "GitHub Codespaces", ["codespace_status", "codespace_start", "codespace_stop"]),
    ("Commands", "Команди", ["run_command", "run_on_many", "run_on_group", "list_snippets", "run_snippet", "history"]),
    ("Background jobs", "Задачи във фонов режим", ["start_job", "job_status"]),
    ("Interactive terminal", "Интерактивен терминал", ["shell_open", "shell_send", "shell_close", "list_shells"]),
    ("Files (SFTP)", "Файлове (SFTP)",
     ["sftp_list", "sftp_read", "sftp_write", "sftp_upload", "sftp_download", "sftp_manage"]),
    ("Port forwarding", "Тунели към портове", ["port_forward", "list_forwards", "stop_forward"]),
    ("Event watching", "Наблюдение на събития", ["start_watch", "stop_watch", "get_events"]),
    ("SNMP monitoring (read-only)", "SNMP наблюдение (само четене)",
     ["snmp_health", "snmp_device_info", "snmp_get", "snmp_getnext", "snmp_getbulk", "snmp_walk",
      "snmp_interfaces", "snmp_interface_stats"]),
    ("RDP / Windows (read-only)", "RDP / Windows (само четене)",
     ["rdp_check", "rdp_tunnel", "windows_health", "rdp_sessions"]),
]
# Команди, които променят достъп или трият — маркираме ги в документите
CONFIRM = {"server_remove", "credentials_rotate", "tailscale_key_create", "tailscale_key_revoke", "codespace_stop"}


def params(name):
    """Параметри на командата от сигнатурата на функцията: име, тип, стойност по подразбиране."""
    fn = getattr(server, name)
    out = []
    for p in inspect.signature(fn).parameters.values():
        t = p.annotation
        t = getattr(t, "__name__", None) if not hasattr(t, "__args__") else str(t).replace("typing.", "")
        default = None if p.default is inspect.Parameter.empty else p.default
        out.append((p.name, t or "", p.default is inspect.Parameter.empty, default))
    return out


def render(lang):
    bg = lang == "bg"
    total = len(server.HELP)
    lines = []
    if bg:
        lines += ["# Команди на SSH Connector", "",
                  f"Версия: **{server.APP_VERSION}** · Команди: **{total}**", "",
                  "> Този файл се генерира автоматично от кода (`scripts/gen_commands_docs.py`). Не го редактирайте на ръка.", "",
                  "Всяка команда има готов пример: сменяте само своите стойности и пускате.",
                  "Може и свободно — просто опишете на ChatGPT/Claude какво искате.", "",
                  "Пароли, ключове и токени **никога** не се показват и не се записват — само имена на променливи и пътища.", ""]
    else:
        lines += ["# SSH Connector commands", "",
                  f"Version: **{server.APP_VERSION}** · Commands: **{total}**", "",
                  "> Generated from the code by `scripts/gen_commands_docs.py` — do not edit by hand.",
                  "> _Генерира се автоматично от кода — не редактирайте на ръка._", "",
                  "Every command has a ready-to-run example: change only your own values and run it.",
                  "You can also just describe what you want in plain words to ChatGPT/Claude.", "",
                  "Passwords, keys and tokens are **never** shown or stored — only environment-variable names and file paths.",
                  "> _Пароли, ключове и токени никога не се показват — само имена на променливи и пътища._", ""]
    # съдържание
    lines.append("## Съдържание" if bg else "## Contents")
    lines.append("")
    for en_t, bg_t, names in GROUPS:
        title = bg_t if bg else en_t
        anchor = title.lower().replace(" & ", "--").replace(" ", "-").replace("(", "").replace(")", "")
        lines.append(f"- [{title}](#{anchor}) — " + ", ".join(f"`{n}`" for n in names))
    lines.append("")
    for en_t, bg_t, names in GROUPS:
        lines += [f"## {bg_t if bg else en_t}", ""]
        for n in names:
            desc_bg, desc_en, example = server.HELP[n]
            lines += [f"### `{n}`", "", desc_bg if bg else desc_en]
            if n in CONFIRM:
                lines += ["", "⚠️ **Изисква изрично потвърждение (`confirm: true`).**" if bg
                          else "⚠️ **Requires explicit confirmation (`confirm: true`).** _Изисква потвърждение._"]
            ps = params(n)
            lines.append("")
            if ps:
                lines += ["| Параметър | Тип | Задължителен | По подразбиране |" if bg
                          else "| Parameter | Type | Required | Default |", "|---|---|---|---|"]
                for pn, pt, req, d in ps:
                    yes, no = ("да", "не") if bg else ("yes", "no")
                    lines.append(f"| `{pn}` | {pt} | {yes if req else no} | {'' if req else '`' + json.dumps(d) + '`'} |")
            else:
                lines.append("_Без параметри._" if bg else "_No parameters._")
            # примерът е на един ред — лесно се копира и се чете на телефон
            lines += ["", "**Пример:**" if bg else "**Example:**", "", "```json",
                      json.dumps({"tool": n, "arguments": example}, ensure_ascii=False), "```", ""]
    return "\n".join(lines).rstrip() + "\n"


def main():
    listed = [n for _, _, names in GROUPS for n in names]
    missing = set(server.HELP) - set(listed)
    extra = set(listed) - set(server.HELP)
    if missing or extra:
        raise SystemExit(f"GROUPS out of sync with HELP: missing={sorted(missing)} extra={sorted(extra)}")
    files = {ROOT / "docs" / "COMMANDS.md": render("en"), ROOT / "docs" / "COMMANDS.bg.md": render("bg")}
    if "--check" in sys.argv:  # проверка, че документите са актуални
        stale = [str(p.relative_to(ROOT)) for p, text in files.items() if not p.exists() or p.read_text() != text]
        if stale:
            raise SystemExit("Outdated docs, run scripts/gen_commands_docs.py: " + ", ".join(stale))
        print("Command docs are up to date")
        return
    for p, text in files.items():
        p.write_text(text)
        print("wrote", p.relative_to(ROOT))


if __name__ == "__main__":
    main()
