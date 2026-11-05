"""KSSH snippet library: ready-made command templates with English / Bulgarian documentation.

The library lives in ``snippets/<category>.yaml`` next to this file. Every snippet has an id, EN/BG
name and description, supported operating systems, required privilege, typed parameters, a risk level
(READ_ONLY / CHANGE / DANGEROUS), the expected result and how errors look.

Safety model
  * Parameters are typed and validated; values are inserted only as whole, shell-quoted tokens
    (POSIX ``shlex.quote`` / PowerShell single-quoted literal). Placeholders inside quotes are refused.
    Integers are inserted bare after validation. No value may start with ``-`` (no option injection).
  * READ_ONLY snippets are checked against a deny-list of changing commands (restart, delete, network,
    firewall, disk, user and password changes, output redirection to files, ...).
  * ``render`` refuses everything that is not READ_ONLY unless the caller explicitly allows that risk;
    nothing in this module runs a command.
  * PowerShell snippets are sent as ``powershell -NoProfile -NonInteractive -EncodedCommand ...`` so the
    default shell of OpenSSH on Windows (cmd or PowerShell) does not matter.

Command line (no network, no SSH)::

    python snippet_library.py validate
    python snippet_library.py search --q "disk" --category linux --os debian --lang bg
    python snippet_library.py show linux.disk_usage --lang bg
    python snippet_library.py render network.ping host=192.0.2.1 count=3
    python snippet_library.py export linux.disk_usage linux.memory

Библиотека с готови команди за KSSH с описание на английски и български. Модулът само проверява,
търси и подготвя команди — нищо не изпълнява.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import re
import shlex
import sys
from pathlib import Path

import yaml

LIB_DIR = Path(__file__).resolve().parent / "snippets"
LANGS = ("en", "bg")
RISKS = ("READ_ONLY", "CHANGE", "DANGEROUS")
SHELLS = ("posix", "freebsd-sh", "powershell")
PRIVILEGES = {
    "user": ("normal user account", "обикновен потребител"),
    "sudo": ("user with sudo rights (read-only commands)", "потребител с sudo права (команди само за четене)"),
    "root": ("root shell on the host", "root достъп на хоста"),
    "admin": ("local administrator (Windows) / admin shell (pfSense, UniFi)",
              "локален администратор (Windows) / админ shell (pfSense, UniFi)"),
    "domain-admin": ("Active Directory read rights; some cmdlets need Domain Admins",
                     "права за четене в Active Directory; някои команди искат Domain Admins"),
    "exchange-admin": ("Exchange View-Only Organization Management or higher",
                       "Exchange View-Only Organization Management или по-високо"),
}
CATEGORIES = {
    "linux": ("Linux — Debian, Ubuntu, RHEL, Rocky Linux", "Linux — Debian, Ubuntu, RHEL, Rocky Linux"),
    "proxmox": ("Proxmox VE — hosts, VM, LXC, storage, backup, networking",
                "Proxmox VE — хостове, VM, LXC, storage, backup, мрежа"),
    "pfsense": ("pfSense — interfaces, routing, DNS, VPN, firewall diagnostics",
                "pfSense — интерфейси, маршрутизация, DNS, VPN, диагностика на firewall"),
    "windows": ("Windows Server 2019/2025 — PowerShell, Active Directory, DNS, DHCP, RDP",
                "Windows Server 2019/2025 — PowerShell, Active Directory, DNS, DHCP, RDP"),
    "exchange": ("Microsoft Exchange — services, databases, certificates, SMTP",
                 "Microsoft Exchange — услуги, бази данни, сертификати, SMTP"),
    "pmg": ("Proxmox Mail Gateway — SMTP, Postfix, queue, spam filtering",
            "Proxmox Mail Gateway — SMTP, Postfix, опашка, спам филтър"),
    "network": ("Networking — VLAN, ARP, routing, ping, TCP, 10GbE, MTU",
                "Мрежа — VLAN, ARP, маршрути, ping, TCP, 10GbE, MTU"),
    "unifi": ("UniFi — read-only diagnostics of devices and network services",
              "UniFi — диагностика само за четене на устройства и мрежови услуги"),
    "vpn": ("Tailscale and WireGuard — connections, status, diagnostics",
            "Tailscale и WireGuard — връзки, състояние, диагностика"),
    "security": ("Cybersecurity — TLS, certificates, SSH, logs, monitoring",
                 "Киберсигурност — TLS, сертификати, SSH, логове, наблюдение"),
    "snmp": ("SNMP — devices, interfaces, statistics (net-snmp tools)",
             "SNMP — устройства, интерфейси, статистики (net-snmp инструменти)"),
    "backup": ("Backup & Recovery — archive checks and data integrity",
               "Backup и възстановяване — проверка на архиви и цялост на данните"),
}
OS_NAMES = {
    "debian": "Debian", "ubuntu": "Ubuntu", "rhel": "Red Hat Enterprise Linux", "rocky": "Rocky Linux",
    "proxmox-ve": "Proxmox VE", "proxmox-backup-server": "Proxmox Backup Server",
    "proxmox-mail-gateway": "Proxmox Mail Gateway", "pfsense": "pfSense (FreeBSD)",
    "windows-server-2019": "Windows Server 2019", "windows-server-2022": "Windows Server 2022",
    "windows-server-2025": "Windows Server 2025", "exchange-2016": "Exchange Server 2016",
    "exchange-2019": "Exchange Server 2019", "exchange-se": "Exchange Server Subscription Edition",
    "unifi-device": "UniFi switch / access point (device shell)", "unifi-os": "UniFi OS console (UDM, UCG, UNVR)",
}
ID_RE = re.compile(r"^[a-z][a-z0-9]*\.[a-z0-9_]+$")
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
CYRILLIC = re.compile(r"[Ѐ-ӿ]")
MAX_COMMAND = 8000                                    # cmd.exe line limit is 8191
PS_PROLOGUE = "$ProgressPreference = 'SilentlyContinue'"

SNIPPET_KEYS = {"id", "name", "description", "os", "shell", "privilege", "permissions_note", "risk",
                "requires_approval", "timeout", "params", "command", "preamble", "expected", "errors",
                "tags", "smoke"}
FILE_KEYS = {"category", "defaults", "snippets"}
DEFAULT_KEYS = {"os", "shell", "privilege", "timeout", "preamble", "tags"}
PARAM_KEYS = {"name", "type", "description", "required", "default", "example", "min", "max", "choices",
              "pattern", "max_len"}
BILINGUAL = ("name", "description", "expected", "errors")


class SnippetError(Exception):
    """Error with a stable code: UNKNOWN_SNIPPET, INVALID_PARAM, NOT_ALLOWED, INVALID_LIBRARY."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def as_dict(self):
        return {"error_code": self.code, "error": str(self)}


# ------------------------------------------------------------------ parameter types
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$")
_TOKEN = re.compile(r"^[A-Za-z0-9._@:][A-Za-z0-9._@:-]*$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_PATH = re.compile(r"^/[A-Za-z0-9._/+-]{0,254}$")
_OID = re.compile(r"^(?:\.?[0-9]+(?:\.[0-9]+){1,63}|[A-Za-z][A-Za-z0-9-]{0,63}::[A-Za-z][A-Za-z0-9.]{0,127})$")
_DEVICE = re.compile(r"^/dev/(?:sd[a-z]{1,2}|vd[a-z]{1,2}|hd[a-z]|nvme[0-9]{1,2}n[0-9]{1,2})$")
_IFACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,14}$")
BARE_TYPES = {"int", "port"}                          # inserted without quotes (digits only)


def _as_text(value):
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        raise ValueError("must be text")
    text = str(value)
    if not text or text != text.strip() or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("empty, padded or contains control characters")
    if text.startswith("-"):
        raise ValueError("must not start with '-'")
    return text


def _v_int(value, spec):
    if isinstance(value, bool):
        raise ValueError("must be an integer")
    if isinstance(value, int):
        number = value
    else:
        text = _as_text(value)
        if not re.fullmatch(r"[0-9]{1,10}", text):
            raise ValueError("must be an integer")
        number = int(text)
    lo, hi = spec.get("min", 1 if spec["type"] == "port" else None), spec.get("max", 65535 if spec["type"] == "port" else None)
    if lo is None or hi is None:
        raise ValueError("type int needs min and max")
    if not lo <= number <= hi:
        raise ValueError(f"must be between {lo} and {hi}")
    return str(number)


def _v_host(value, spec):
    text = _as_text(value)
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if not _HOSTNAME.fullmatch(text) or text.replace(".", "").isdigit():
        raise ValueError("must be a host name or IP address")
    return text


def _v_ip(value, spec):
    try:
        return str(ipaddress.ip_address(_as_text(value)))
    except ValueError:
        raise ValueError("must be an IP address") from None


def _v_regex(regex, what, max_len=255):
    def check(value, spec):
        text = _as_text(value)
        if len(text) > max_len or not regex.fullmatch(text):
            raise ValueError(f"must be {what}")
        return text
    return check


def _v_path(value, spec):
    text = _as_text(value)
    if not _PATH.fullmatch(text) or ".." in text.split("/") or "//" in text:
        raise ValueError("must be an absolute path without '..'")
    return text


def _v_token(value, spec):
    text = _as_text(value)
    if len(text) > int(spec.get("max_len", 64)) or not _TOKEN.fullmatch(text):
        raise ValueError("may contain only letters, digits and . _ @ : -")
    if spec.get("pattern") and not re.fullmatch(spec["pattern"], text):
        raise ValueError("does not match the allowed format")
    return text


def _v_enum(value, spec):
    text = _as_text(value)
    if text not in spec["choices"]:
        raise ValueError("must be one of " + ", ".join(spec["choices"]))
    return text


TYPES = {
    "int": _v_int, "port": _v_int, "host": _v_host, "ip": _v_ip, "token": _v_token, "enum": _v_enum,
    "email": _v_regex(_EMAIL, "an e-mail address", 254), "path": _v_path,
    "oid": _v_regex(_OID, "a numeric OID or MIB::name", 192), "device": _v_regex(_DEVICE, "a disk device such as /dev/sda"),
    "iface": _v_regex(_IFACE, "a network interface name", 15),
}


def check_value(spec, value):
    """Validate one parameter value; returns the normalized text or raises ValueError."""
    return TYPES[spec["type"]](value, spec)


def quote(value, ptype, shell):
    if ptype in BARE_TYPES:
        return value
    if shell == "powershell":
        return "'" + value.replace("'", "''") + "'"
    return shlex.quote(value)


# ------------------------------------------------------------------ read-only deny-list
_CMD = r"(?:^|[;&|(`\n{]|\$\(|\b(?:then|do|else|sudo|env|xargs|nice|nohup|exec|eval|command)\s)\s*(?:[\w./-]*/)?"
_POSIX_DENY = [
    (_CMD + r"(?:rm|rmdir|mv|cp|dd|ln|touch|mkdir|tee|truncate|shred|wipefs|mkfs(?:\.\w+)?|fdisk|sfdisk|parted|"
     r"mkswap|swapoff|chmod|chown|chgrp|chattr|passwd|chpasswd|useradd|userdel|usermod|groupadd|groupdel|"
     r"groupmod|visudo|reboot|shutdown|poweroff|halt|kill|killall|pkill|wget|scp|rsync|crontab|mount|umount|"
     r"wg-quick|vzdump|snmpset|postsuper|ifup|ifdown|ifreload|dhclient|reset|xargs|eval|exec|bash|sh|zsh|"
     r"python3?|perl|socat|ncat|telnet)(?=\s|$|[;&|)])", "changing or arbitrary command"),
    (_CMD + r"nc\b(?![^;|&\n]*\s-[A-Za-y]*z)", "netcat without -z"),
    (r"\bfind\b[^;|&\n]*\s-(?:delete|exec|execdir|ok|okdir|fprint|fprintf|fls)\b", "find with action"),
    (r"\bsed\s+(?:-\w*\s+)*-i", "sed -i"),
    (r"\binit\s+[0-6]\b", "init runlevel"),
    (r"\bsystemctl\s+(?:--?\S+\s+)*(?:start|stop|restart|reload|try-restart|reload-or-restart|enable|disable|"
     r"mask|unmask|kill|isolate|daemon-reload|edit|set-property|reset-failed|reboot|poweroff|halt|default|"
     r"rescue|emergency|link|revert|preset)\b", "systemctl change"),
    (r"\bservice\s+\S+\s+(?:start|stop|restart|reload|force-reload)\b", "service change"),
    (r"\b(?:apt|apt-get|aptitude)\s+(?:-\S+\s+)*(?:install|remove|purge|upgrade|dist-upgrade|full-upgrade|"
     r"autoremove|update|reinstall|build-dep)\b", "package change"),
    (r"\b(?:dnf|yum)\s+(?:-\S+\s+)*(?:install|remove|erase|upgrade|update|downgrade|autoremove|reinstall|"
     r"distro-sync|makecache)\b", "package change"),
    (r"\bdpkg\s+(?:-\S+\s+)*(?:-i|--install|-r|--remove|-P|--purge|--configure)\b", "package change"),
    (r"\bip\s+(?:-\S+\s+)*(?:link|l|addr|address|a|route|r|neigh|n|rule|tunnel|maddr|xfrm)\s+"
     r"(?:add|del|delete|set|change|replace|flush|append)\b", "network change"),
    (r"\bbridge\s+(?:vlan|fdb|link|mdb)\s+(?:add|del|delete|set|replace)\b", "bridge change"),
    (r"\bethtool\s+(?:-s|-K|-G|-A|-C|-L|-E|-f|--change|--features|--set-ring|--flash|--reset)\b", "NIC change"),
    (r"\b(?:iptables|ip6tables|ebtables|arptables)(?:-restore)?\s+(?:\S+\s+)*?(?:-[ADIRFXPNZ]\b|--append|"
     r"--delete|--insert|--replace|--flush|--policy|--new-chain|--zero)", "firewall change"),
    (r"\b(?:iptables|ip6tables)-restore\b", "firewall change"),
    (r"\bnft\s+(?:-\S+\s+)*(?:add|delete|flush|insert|replace|create|destroy|reset|-f)\b", "firewall change"),
    (r"\bufw\s+(?!status\b)\w", "firewall change"),
    (r"\bfirewall-cmd\b(?![^;|&\n]*--(?:state|list|get|query))", "firewall change"),
    (r"\bpfctl\b[^;|&\n]*\s-[A-Za-z]*[FfedkKT]", "pf change"),
    (r"\b(?:easyrule|pfSsh\.php\s+(?!playback\s+gatewaystatus\b))", "pfSense change"),
    (r"\bqm\s+(?:start|stop|shutdown|reboot|reset|destroy|set|create|migrate|rollback|snapshot|delsnapshot|"
     r"resize|disk|move-disk|move_disk|unlink|clone|restore|suspend|resume|template|importdisk|import|"
     r"guest\s+exec|monitor|terminal|unlock|cleanup|remote-migrate|sendkey|wait)\b", "VM change"),
    (r"\bpct\s+(?:start|stop|shutdown|reboot|destroy|set|create|migrate|rollback|snapshot|delsnapshot|resize|"
     r"move-volume|move_volume|clone|restore|suspend|resume|template|enter|exec|console|push|pull|mount|"
     r"unmount|unlock|fsck|remote-migrate)\b", "LXC change"),
    (r"\bpvesm\s+(?:add|remove|set|alloc|free|import|export|prune-backups|scan\s+\S+\s+--delete)\b", "storage change"),
    (r"\b(?:pvesh|pmgsh)\s+(?:create|set|delete|post|put)\b", "API change"),
    (r"\bpvecm\s+(?:add|delnode|expected|create|updatecerts|qdevice)\b", "cluster change"),
    (r"\bpmgcm\s+(?:join|create|delete|sync|promote|update-fingerprints)\b", "cluster change"),
    (r"\bzpool\s+(?:create|destroy|add|remove|offline|online|clear|scrub|import|export|labelclear|attach|"
     r"detach|replace|set|split|upgrade|reguid|resilver|trim|initialize|checkpoint)\b", "ZFS change"),
    (r"\bzfs\s+(?:create|destroy|set|inherit|rollback|snapshot|rename|send|receive|recv|promote|mount|"
     r"unmount|share|unshare|clone|load-key|unload-key|change-key)\b", "ZFS change"),
    (r"\bceph\s+(?:osd\s+(?:out|in|down|rm|reweight|set|unset|purge|destroy|crush)|config\s+set|"
     r"mon\s+remove|pg\s+repair|tell|orch)\b", "Ceph change"),
    (r"\bwg\s+(?:set|setconf|addconf|syncconf)\b", "WireGuard change"),
    (r"\bwg\s+(?:show\s+\S+\s+)?(?:private-key|preshared-keys)\b", "WireGuard secret"),
    (r"\bwg\s+(?:genkey|genpsk)\b", "WireGuard secret"),
    (r"\btailscale\s+(?:up|down|login|logout|set|cert|switch|file|lock|update|configure|drive|web|bugreport)\b",
     "Tailscale change"),
    (r"\btailscale\s+(?:serve|funnel)\s+(?!status\b)", "Tailscale change"),
    (r"\bpostqueue\s+(?:-\S+\s+)*-[fis]\b", "mail queue change"),
    (r"\bpostfix\s+(?:start|stop|reload|flush|abort|upgrade-configuration|set-permissions)\b", "Postfix change"),
    (r"\bpostconf\s+(?:-\S+\s+)*-e\b|\bpostconf\s+\S+=", "Postfix change"),
    (r"\bsmartctl\b[^;|&\n]*(?:\s-t\b|--test|\s-s\s|--smart=|--offlineauto|--saveauto)", "SMART change"),
    (r"\bcurl\b[^;|&\n]*(?:-X\s*(?:POST|PUT|DELETE|PATCH)|--data|\s-d\s|--upload-file|\s-T\s|-o\s|--output)",
     "HTTP change"),
    (r"\bopenssl\s+(?:genrsa|genpkey|req|ca|pkcs12)\b", "key or certificate creation"),
    (r"\bsnmp(?:get|walk|bulkwalk|getnext|bulkget|table)\b[^;|&\n]*\s-(?:A|X|c)\s", "SNMP secret on command line"),
]
_PS_DENY = [
    (r"\b(?:Set|New|Remove|Restart|Stop|Start|Enable|Disable|Clear|Reset|Move|Rename|Install|Uninstall|Update|"
     r"Suspend|Resume|Mount|Dismount|Register|Unregister|Grant|Revoke|Unlock|Lock|Export|Invoke|Copy|Send|"
     r"Publish|Join|Repair|Optimize|Initialize|Format|Add|Import|Expand|Merge|Protect|Unprotect|Close|Block|"
     r"Unblock|Approve|Deny|Complete|Retry|Redirect|Request|Use|Write|Out)-(?!Module\b|PSSnapin\b|Output\b|"
     r"Table\b|List\b|Wide\b|Custom\b|Host\b)[A-Za-z]", "changing cmdlet"),
    (r"\b(?:iex|Out-File|Set-Content|Add-Content|Tee-Object|Stop-Computer|Restart-Computer)\b", "changing cmdlet"),
    (r"\bnet\s+(?:user|localgroup|group|stop|start|share|use|accounts|computer|pause|continue|session)\b", "net change"),
    (r"\bsc(?:\.exe)?\s+(?:\\\\\S+\s+)?(?:stop|start|config|delete|create|pause|continue|failure|sdset|control)\b",
     "service change"),
    (r"\bshutdown(?:\.exe)?\b|\blogoff\b|\btscon\b|\brwinsta\b|\breset\s+session\b|\bmsg\s", "session or power change"),
    (r"\bvssadmin\s+(?:delete|resize|create|add|revert|break)\b", "VSS change"),
    (r"\bwbadmin\s+(?:start|delete|enable|disable|stop|restore)\b", "Windows Backup change"),
    (r"\bdcdiag\b[^;|&\n]*/fix\b", "dcdiag /fix"),
    (r"\brepadmin\s+/(?:sync|syncall|replicate|add|del|kcc|options|removelingeringobjects|rehost|unhost|setattr)\b",
     "AD replication change"),
    (r"\bnetsh\b[^;|&\n]*\b(?:set|add|delete|reset|install|uninstall|import|dump\s+>|flush)\b", "netsh change"),
    (r"\breg(?:\.exe)?\s+(?:add|delete|import|load|unload|restore|copy|save|export)\b", "registry change"),
    (r"\b(?:bcdedit|diskpart|format\s+[A-Za-z]:|cipher\s+/w|takeown|icacls|cacls|schtasks\s+/(?:create|delete|change|run|end))",
     "system change"),
    (r"\bwmic\b[^;|&\n]*\b(?:call|delete|set|create)\b", "WMI change"),
    (r"\bdnscmd\b[^;|&\n]*/(?:record(?:add|delete)|zone(?:add|delete|reload|refresh|resetsecondaries)|config|"
     r"clearcache|ageallrecords|startscavenging|restart)\b", "DNS change"),
    (r"\bnetdom\s+(?:join|remove|move|reset|resetpwd|add|renamecomputer|trust)\b", "domain change"),
    (r"\bcertutil\b[^;|&\n]*-(?:delstore|addstore|importpfx|repairstore|setreg|shutdown|deny|revoke|resubmit)\b",
     "certificate change"),
]
_POSIX_DENY = [(re.compile(p), why) for p, why in _POSIX_DENY]
_PS_DENY = [(re.compile(p, re.IGNORECASE), why) for p, why in _PS_DENY]
_ALLOWED_REDIRECTS = re.compile(r"(?:[0-9&]?>\s*/dev/null|[0-9]?>\s*\$null|[0-9]>&[0-9]|<\s*/dev/null)")


def readonly_violations(script):
    """Reasons why ``script`` is not read-only (empty list = no known changing command found)."""
    found = []
    for regex, why in _POSIX_DENY + _PS_DENY:
        match = regex.search(script)
        if match:
            found.append(f"{why}: {match.group(0).strip()!r}")
    if ">" in _ALLOWED_REDIRECTS.sub(" ", script):
        found.append("output redirection to a file")
    if "<<" in script:
        found.append("here-document")
    return found


def _quoted_placeholders(command):
    """Placeholders that sit inside quotes (they must be whole tokens)."""
    bad, quote_char, i = [], None, 0
    while i < len(command):
        c = command[i]
        if quote_char is None and command.startswith("{{", i):
            m = PLACEHOLDER.match(command, i)
            if m:
                i = m.end()
                continue
        if quote_char:
            m = PLACEHOLDER.match(command, i)
            if m:
                bad.append(m.group(1))
                i = m.end()
                continue
            if c == quote_char:
                quote_char = None
            elif c == "\\" and quote_char == '"':
                i += 1
        elif c in "'\"":
            quote_char = c
        i += 1
    return bad


# ------------------------------------------------------------------ loading and validation
def _bilingual(value, field, errors, sid):
    if not isinstance(value, dict) or set(value) != set(LANGS):
        errors.append(f"{sid}: {field} needs exactly en and bg")
        return
    for lang in LANGS:
        text = value.get(lang)
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{sid}: {field}.{lang} is empty")
        elif lang == "bg" and not CYRILLIC.search(text):
            errors.append(f"{sid}: {field}.bg has no Bulgarian text")


def _validate_param(p, sid, errors):
    where = f"{sid}: param {p.get('name')!r}"
    unknown = set(p) - PARAM_KEYS
    if unknown:
        errors.append(f"{where}: unknown keys {sorted(unknown)}")
    if not isinstance(p.get("name"), str) or not PARAM_NAME_RE.fullmatch(p["name"]):
        errors.append(f"{where}: invalid name")
        return
    ptype = p.get("type")
    if ptype not in TYPES:
        errors.append(f"{where}: unknown type {ptype!r}")
        return
    _bilingual(p.get("description"), f"param {p['name']} description", errors, sid)
    if ptype == "int" and not (isinstance(p.get("min"), int) and isinstance(p.get("max"), int) and p["min"] <= p["max"]):
        errors.append(f"{where}: int needs min <= max")
    if ptype == "enum":
        choices = p.get("choices")
        if not isinstance(choices, list) or not choices or not all(isinstance(c, str) and _TOKEN.fullmatch(c) for c in choices):
            errors.append(f"{where}: enum needs safe choices")
    if p.get("pattern") is not None:
        try:
            re.compile(p["pattern"])
        except (re.error, TypeError):
            errors.append(f"{where}: invalid pattern")
    p.setdefault("required", True)
    for key in ("default", "example"):
        if key in p:
            try:
                check_value(p, p[key])
            except (ValueError, KeyError) as e:
                errors.append(f"{where}: {key} invalid ({e})")
    if "default" not in p and "example" not in p:
        errors.append(f"{where}: needs a default or an example")


def _validate_snippet(s, errors):
    sid = s.get("id", "<no id>")
    if not isinstance(s.get("id"), str) or not ID_RE.fullmatch(s["id"]):
        errors.append(f"{sid}: invalid id")
    elif s["id"].split(".")[0] != s["category"]:
        errors.append(f"{sid}: id must start with '{s['category']}.'")
    for field in BILINGUAL:
        _bilingual(s.get(field), field, errors, sid)
    if "permissions_note" in s:
        _bilingual(s["permissions_note"], "permissions_note", errors, sid)
    if not isinstance(s.get("os"), list) or not s["os"] or not set(s["os"]) <= set(OS_NAMES):
        errors.append(f"{sid}: os must be a non-empty list of {sorted(OS_NAMES)}")
    if s.get("shell") not in SHELLS:
        errors.append(f"{sid}: shell must be one of {SHELLS}")
    if s.get("privilege") not in PRIVILEGES:
        errors.append(f"{sid}: privilege must be one of {sorted(PRIVILEGES)}")
    if s.get("risk") not in RISKS:
        errors.append(f"{sid}: risk must be one of {RISKS}")
    s.setdefault("requires_approval", s.get("risk") != "READ_ONLY")
    if not isinstance(s["requires_approval"], bool):
        errors.append(f"{sid}: requires_approval must be true/false")
    elif s.get("risk") in ("CHANGE", "DANGEROUS") and not s["requires_approval"]:
        errors.append(f"{sid}: {s['risk']} requires requires_approval: true")
    if not isinstance(s.get("timeout"), int) or not 1 <= s["timeout"] <= 600:
        errors.append(f"{sid}: timeout must be 1..600 seconds")
    if not isinstance(s.get("tags", []), list) or not all(isinstance(t, str) for t in s.get("tags", [])):
        errors.append(f"{sid}: tags must be a list of strings")
    if not isinstance(s.get("smoke", False), bool):
        errors.append(f"{sid}: smoke must be true/false")
    command = s.get("command")
    if not isinstance(command, str) or not command.strip():
        errors.append(f"{sid}: command is empty")
        return
    if s.get("preamble") is not None and (s.get("shell") != "powershell" or not isinstance(s["preamble"], str)):
        errors.append(f"{sid}: preamble is only for powershell snippets")
    params = s.get("params") or []
    if not isinstance(params, list):
        errors.append(f"{sid}: params must be a list")
        params = []
    s["params"] = params
    names = []
    for p in params:
        if not isinstance(p, dict):
            errors.append(f"{sid}: param must be a mapping")
            continue
        _validate_param(p, sid, errors)
        names.append(p.get("name"))
    if len(names) != len(set(names)):
        errors.append(f"{sid}: duplicate parameter names")
    used = PLACEHOLDER.findall(command)
    if set(used) != set(names):
        errors.append(f"{sid}: placeholders {sorted(set(used))} do not match params {sorted(names)}")
        return
    if (s.get("preamble") and PLACEHOLDER.search(s["preamble"])) or re.search(r"\{\{(?![a-z][a-z0-9_]*\}\})", command):
        errors.append(f"{sid}: malformed placeholder")
    inside = _quoted_placeholders(command)
    if inside:
        errors.append(f"{sid}: placeholders inside quotes {sorted(set(inside))}")
    if s.get("risk") == "READ_ONLY":
        why = readonly_violations(_script(s, {name: "x" for name in used}))
        if why:
            errors.append(f"{sid}: READ_ONLY snippet is not read-only: {'; '.join(why)}")


class Library:
    def __init__(self, snippets, errors):
        self.snippets = snippets
        self.errors = errors

    def get(self, sid):
        if sid not in self.snippets:
            raise SnippetError("UNKNOWN_SNIPPET", f"unknown snippet {sid!r}")
        return self.snippets[sid]


def load_library(directory=LIB_DIR):
    """Load and validate every ``<category>.yaml``. Invalid snippets are reported in ``errors``."""
    directory = Path(directory)
    snippets, errors = {}, []
    files = sorted(directory.glob("*.yaml"))
    if not files:
        errors.append(f"no snippet files in {directory}")
    for path in files:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            errors.append(f"{path.name}: cannot read ({type(e).__name__})")
            continue
        if not isinstance(data, dict) or set(data) - FILE_KEYS:
            errors.append(f"{path.name}: top level must have only {sorted(FILE_KEYS)}")
            continue
        category = data.get("category")
        if category not in CATEGORIES or path.stem != category:
            errors.append(f"{path.name}: category must be one of {sorted(CATEGORIES)} and match the file name")
            continue
        defaults = data.get("defaults") or {}
        if not isinstance(defaults, dict) or set(defaults) - DEFAULT_KEYS:
            errors.append(f"{path.name}: defaults may contain only {sorted(DEFAULT_KEYS)}")
            continue
        for raw in data.get("snippets") or []:
            if not isinstance(raw, dict):
                errors.append(f"{path.name}: snippet must be a mapping")
                continue
            unknown = set(raw) - SNIPPET_KEYS
            if unknown:
                errors.append(f"{raw.get('id', path.name)}: unknown keys {sorted(unknown)}")
                continue
            s = {"timeout": 30, **{k: v for k, v in defaults.items() if k != "tags"}, **raw, "category": category}
            s["tags"] = list(defaults.get("tags", [])) + list(raw.get("tags", []))
            before = len(errors)
            _validate_snippet(s, errors)
            if s.get("id") in snippets:
                errors.append(f"{s['id']}: duplicate id")
            elif len(errors) == before:
                snippets[s["id"]] = s
    return Library(snippets, errors)


# ------------------------------------------------------------------ search, describe, render
def _haystack(s):
    parts = [s["id"], s["category"], " ".join(s["os"]), " ".join(s.get("tags", []))]
    for field in ("name", "description"):
        parts += [s[field]["en"], s[field]["bg"]]
    parts += [CATEGORIES[s["category"]][0], CATEGORIES[s["category"]][1]]
    return " ".join(parts).casefold()


def search(lib, query="", category="", os="", risk="", tag="", lang="en"):
    """Snippets matching all words of ``query`` (EN or BG) and the given filters."""
    words = query.casefold().split()
    out = []
    for sid in sorted(lib.snippets):
        s = lib.snippets[sid]
        if category and s["category"] != category:
            continue
        if os and os not in s["os"]:
            continue
        if risk and s["risk"] != risk:
            continue
        if tag and tag not in s.get("tags", []):
            continue
        hay = _haystack(s)
        if all(w in hay for w in words):
            out.append({"id": sid, "name": s["name"][lang], "category": s["category"], "os": s["os"],
                        "risk": s["risk"], "params": [p["name"] for p in s["params"]]})
    return out


def example_params(s):
    return {p["name"]: p.get("example", p.get("default")) for p in s["params"]}


def _script(s, values):
    body = PLACEHOLDER.sub(lambda m: values[m.group(1)], s["command"])
    if s["shell"] == "powershell":
        lines = [PS_PROLOGUE] + ([s["preamble"]] if s.get("preamble") else []) + [body]
        return "\n".join(lines)
    return body


def render(lib, sid, params=None, allow_risks=("READ_ONLY",)):
    """Validate parameters and build the final command text. Runs nothing."""
    s = lib.get(sid)
    if s["risk"] not in allow_risks:
        raise SnippetError("NOT_ALLOWED", f"{sid} is {s['risk']}; only {', '.join(allow_risks)} may be rendered here")
    params = dict(params or {})
    known = {p["name"]: p for p in s["params"]}
    extra = sorted(set(params) - set(known))
    if extra:
        raise SnippetError("INVALID_PARAM", f"unknown parameters: {', '.join(extra)}")
    values = {}
    for name, spec in known.items():
        if name in params:
            raw = params[name]
        elif "default" in spec:
            raw = spec["default"]
        else:
            raise SnippetError("INVALID_PARAM", f"missing parameter: {name}")
        try:
            values[name] = quote(check_value(spec, raw), spec["type"], s["shell"])
        except ValueError as e:
            raise SnippetError("INVALID_PARAM", f"{name}: {e}") from None
    script = _script(s, values)
    if s["shell"] == "powershell":
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        command = f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"
    elif s["shell"] == "freebsd-sh":
        command = "/bin/sh -c " + shlex.quote(script)
    else:
        command = script
    if len(command) > MAX_COMMAND:
        raise SnippetError("INVALID_PARAM", "rendered command is too long")
    return {"id": sid, "risk": s["risk"], "requires_approval": s["requires_approval"], "shell": s["shell"],
            "privilege": s["privilege"], "timeout": s["timeout"], "script": script, "command": command,
            "sudo": s["privilege"] == "sudo"}


def describe(lib, sid, lang="en"):
    s = lib.get(sid)
    other = "bg" if lang == "en" else "en"
    ex = example_params(s)
    try:
        rendered = render(lib, sid, ex, allow_risks=RISKS)["script"]
    except SnippetError as e:
        rendered = f"<{e}>"
    return {
        "id": sid, "category": s["category"], "name": s["name"][lang], "name_" + other: s["name"][other],
        "description": s["description"][lang], "os": s["os"], "shell": s["shell"],
        "privilege": s["privilege"], "privilege_text": PRIVILEGES[s["privilege"]][LANGS.index(lang)],
        "permissions_note": s.get("permissions_note", {}).get(lang), "risk": s["risk"],
        "requires_approval": s["requires_approval"], "timeout": s["timeout"],
        "params": [{"name": p["name"], "type": p["type"], "required": p["required"] and "default" not in p,
                    "default": p.get("default"), "example": p.get("example"), "description": p["description"][lang],
                    **{k: p[k] for k in ("min", "max", "choices") if k in p}} for p in s["params"]],
        "template": s["command"], "example_params": ex, "example_script": rendered,
        "expected": s["expected"][lang], "errors": s["errors"][lang], "tags": s.get("tags", []),
    }


def export_snippets(lib, ids):
    """``servers.yaml`` ``snippets:`` entries for parameter-less READ_ONLY snippets (works with run_snippet)."""
    out, refused = {}, []
    for sid in ids:
        s = lib.get(sid)
        if s["risk"] != "READ_ONLY" or s["params"]:
            refused.append(sid)
            continue
        out[sid] = render(lib, sid)["command"]
    if refused:
        raise SnippetError("NOT_ALLOWED", "only parameter-less READ_ONLY snippets can be exported: " + ", ".join(refused))
    return out


# ------------------------------------------------------------------ CLI
def _parse_kv(items):
    out = {}
    for item in items:
        if "=" not in item:
            raise SnippetError("INVALID_PARAM", f"expected key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="KSSH snippet library (validate, search, show, render, export)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    sp = sub.add_parser("search")
    for opt in ("q", "category", "os", "risk", "tag"):
        sp.add_argument("--" + opt, default="")
    sp.add_argument("--lang", choices=LANGS, default="en")
    sh = sub.add_parser("show")
    sh.add_argument("id")
    sh.add_argument("--lang", choices=LANGS, default="en")
    rp = sub.add_parser("render")
    rp.add_argument("id")
    rp.add_argument("params", nargs="*")
    ep = sub.add_parser("export")
    ep.add_argument("ids", nargs="+")
    args = ap.parse_args(argv)
    lib = load_library()
    try:
        if args.cmd == "validate":
            print(json.dumps({"snippets": len(lib.snippets), "errors": lib.errors}, ensure_ascii=False, indent=2))
            return 1 if lib.errors else 0
        if lib.errors:
            print(json.dumps({"error_code": "INVALID_LIBRARY", "errors": lib.errors}, ensure_ascii=False, indent=2))
            return 1
        if args.cmd == "search":
            res = search(lib, args.q, args.category, args.os, args.risk, args.tag, args.lang)
        elif args.cmd == "show":
            res = describe(lib, args.id, args.lang)
        elif args.cmd == "render":
            res = render(lib, args.id, _parse_kv(args.params))
        else:
            print(yaml.safe_dump({"snippets": export_snippets(lib, args.ids)}, allow_unicode=True,
                                 sort_keys=False, width=100000), end="")
            return 0
    except SnippetError as e:
        print(json.dumps(e.as_dict(), ensure_ascii=False))
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
