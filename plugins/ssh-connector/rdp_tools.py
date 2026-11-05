"""Read-only RDP / Windows helpers for the KSSH MCP server.

Four tools, nothing that changes a Windows host:

* ``rdp_check``      - is RDP answering? RDP negotiation (X.224), NLA enforced or not, legacy
                       (non-TLS) RDP security allowed or not, TLS certificate (verified only, never
                       an unverified handshake), latency. No login, no credentials sent.
* ``rdp_tunnel``     - local SSH port forward to a Windows host behind a configured SSH server,
                       plus a ready ``.rdp`` file for the user's own RDP client (no password in it).
* ``windows_health`` - fixed read-only PowerShell over the existing SSH connection: OS, uptime,
                       disks, stopped automatic services, pending reboot, RDP/NLA settings.
* ``rdp_sessions``   - who is logged on (``quser``), over SSH.

Targets come only from ``servers.yaml -> rdp.targets``; every host must be inside
``rdp.allowed_networks``. A full interactive RDP desktop session is intentionally not offered:
AI clients (Claude Code / Codex) work through commands, the user through their own RDP client.

Само четене: проверка на RDP, тунел + .rdp файл, здраве на Windows и активни сесии. Без вход и без промени.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import ssl
import struct
import time
from typing import Any, Awaitable, Callable

import yaml

from kssh_snmp.errors import ErrorCode, MonitorError, status_for
from kssh_snmp.snmp_client import Allowlist

# ---------------------------------------------------------------- hooks (tests / server.py)
AUDIT: Any = None                       # server.py sets this to HISTORY
CLOCK: Callable[[], float] = time.monotonic
CONNECT_TIMEOUT = 5.0
MAX_TEXT = 1024
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

# ---------------------------------------------------------------- RDP negotiation constants
PROTOCOL_RDP, PROTOCOL_SSL, PROTOCOL_HYBRID, PROTOCOL_RDSTLS, PROTOCOL_HYBRID_EX = 0, 1, 2, 4, 8
PROTOCOL_NAMES = {PROTOCOL_RDP: "rdp_standard_security", PROTOCOL_SSL: "tls", PROTOCOL_HYBRID: "credssp_nla",
                  PROTOCOL_RDSTLS: "rdstls", PROTOCOL_HYBRID_EX: "credssp_nla_ex"}
FAILURE_NAMES = {1: "SSL_REQUIRED_BY_SERVER", 2: "SSL_NOT_ALLOWED_BY_SERVER", 3: "SSL_CERT_NOT_ON_SERVER",
                 4: "INCONSISTENT_FLAGS", 5: "HYBRID_REQUIRED_BY_SERVER",
                 6: "SSL_WITH_USER_AUTH_REQUIRED_BY_SERVER"}


class RdpConfigError(Exception):
    pass


def _config_path() -> str:
    from pathlib import Path
    default = str(Path.home() / ".ssh-connector" / "servers.yaml")
    return os.path.expanduser(os.environ.get("SSH_CONNECTOR_CONFIG", default))


def load_rdp_config(path: str | None = None) -> dict:
    """Return {"allowlist": Allowlist, "targets": {name: cfg}} or raise RdpConfigError."""
    path = path or _config_path()
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except OSError as exc:
        raise RdpConfigError(f"cannot read config: {type(exc).__name__}") from None
    section = raw.get("rdp")
    if not section:
        raise RdpConfigError("no 'rdp' section in servers.yaml (see servers.example.yaml)")
    if not isinstance(section, dict):
        raise RdpConfigError("'rdp' must be a mapping")
    try:
        allowlist = Allowlist(section.get("allowed_networks") or [])
    except MonitorError as exc:
        raise RdpConfigError(f"rdp.allowed_networks: {exc.message}") from None
    targets = section.get("targets") or {}
    if not isinstance(targets, dict) or not targets:
        raise RdpConfigError("rdp.targets must be a non-empty mapping")
    out = {}
    for name, cfg in targets.items():
        if not _NAME_RE.match(str(name)):
            raise RdpConfigError(f"rdp target name {name!r} is invalid")
        if not isinstance(cfg, dict) or not cfg.get("host"):
            raise RdpConfigError(f"rdp target {name}: host is required")
        host = str(cfg["host"]).strip()
        if not allowlist.allows(host):
            raise RdpConfigError(f"rdp target {name}: host is not in rdp.allowed_networks")
        port = int(cfg.get("port", 3389))
        if not 1 <= port <= 65535:
            raise RdpConfigError(f"rdp target {name}: port must be 1..65535")
        via = cfg.get("via")
        if via is not None and not _NAME_RE.match(str(via)):
            raise RdpConfigError(f"rdp target {name}: via must be the name of an SSH server profile")
        ca_file = cfg.get("ca_file")
        if ca_file and not os.path.isfile(os.path.expanduser(str(ca_file))):
            raise RdpConfigError(f"rdp target {name}: ca_file does not exist")
        user = cfg.get("user")
        if user is not None and not re.match(r"^[A-Za-z0-9_.\\@-]{1,104}$", str(user)):
            raise RdpConfigError(f"rdp target {name}: user is invalid")
        out[str(name)] = {"host": host, "port": port, "via": str(via) if via else None,
                          "server_name": str(cfg.get("server_name") or host),
                          "ca_file": os.path.expanduser(str(ca_file)) if ca_file else None,
                          "timeout": max(1.0, min(float(cfg.get("timeout", CONNECT_TIMEOUT)), 30.0)),
                          "user": str(user) if user else None}
    return {"allowlist": allowlist, "targets": out}


def _target(name: str, config: dict | None = None) -> tuple[dict, dict]:
    config = config or load_rdp_config()
    if not isinstance(name, str) or name not in config["targets"]:
        raise RdpConfigError(f"unknown rdp target {name!r}; configured: {', '.join(sorted(config['targets']))}")
    return config, config["targets"][name]


def _clean(obj: Any) -> Any:
    if isinstance(obj, str):
        return obj if len(obj) <= MAX_TEXT else obj[:MAX_TEXT] + "...[truncated]"
    if isinstance(obj, list):
        return [_clean(x) for x in obj[:200]]
    if isinstance(obj, dict):
        return {str(k)[:64]: _clean(v) for k, v in list(obj.items())[:100]}
    return obj


def _audit(tool: str, target: str, ok: bool) -> None:
    if AUDIT is not None:
        AUDIT.append({"ts": time.time(), "server": target, "command": f"rdp:{tool} {target}",
                      "exit_code": 0 if ok else 1})


def _err(tool: str, target: str, code: ErrorCode, detail: str) -> str:
    _audit(tool, target, False)
    return json.dumps({"target": target, "status": status_for(code).value, "error_code": code.value,
                       "detail": detail[:300], "read_only": True})


# ---------------------------------------------------------------- X.224 / RDP_NEG
def build_connection_request(requested: int) -> bytes:
    """TPKT + X.224 Connection Request + RDP_NEG_REQ (MS-RDPBCGR 2.2.1.1)."""
    neg = struct.pack("<BBHI", 0x01, 0, 8, requested)
    x224 = bytes([6 + len(neg), 0xE0, 0, 0, 0, 0, 0]) + neg
    return struct.pack(">BBH", 3, 0, 4 + len(x224)) + x224


def parse_connection_confirm(data: bytes) -> dict:
    """Parse TPKT + X.224 Connection Confirm. Raises ValueError if this is not RDP."""
    if len(data) < 11 or data[0] != 3 or data[1] != 0:
        raise ValueError("not a TPKT response (service is not RDP)")
    length = struct.unpack(">H", data[2:4])[0]
    if length < 11 or length > len(data):
        raise ValueError("truncated TPKT response")
    li, code = data[4], data[5]
    if code & 0xF0 != 0xD0:
        raise ValueError("not an X.224 Connection Confirm")
    neg = data[11:length]
    if len(neg) < 8:                                  # very old servers: no negotiation, standard security
        return {"type": "none", "selected": PROTOCOL_RDP}
    ntype, flags, nlen, value = struct.unpack("<BBHI", neg[:8])
    if ntype == 0x02:
        return {"type": "response", "selected": value, "flags": flags}
    if ntype == 0x03:
        return {"type": "failure", "failure": value, "failure_name": FAILURE_NAMES.get(value, f"UNKNOWN_{value}")}
    raise ValueError("unknown RDP negotiation type")


async def _read_tpkt(reader: asyncio.StreamReader, timeout: float) -> bytes:
    head = await asyncio.wait_for(reader.readexactly(4), timeout)
    if head[0] != 3:
        raise ValueError("not a TPKT response (service is not RDP)")
    length = struct.unpack(">H", head[2:4])[0]
    if not 7 <= length <= 1024:
        raise ValueError("implausible TPKT length")
    return head + await asyncio.wait_for(reader.readexactly(length - 4), timeout)


async def negotiate(host: str, port: int, requested: int, timeout: float, tls: dict | None = None) -> dict:
    """One negotiation. With ``tls`` = {"server_name", "ca_file"} and a TLS-capable answer, also run a
    VERIFIED TLS handshake and return certificate facts. Nothing else is sent; no credentials."""
    t0 = CLOCK()
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        writer.write(build_connection_request(requested))
        await writer.drain()
        res = parse_connection_confirm(await _read_tpkt(reader, timeout))
        res["latency_ms"] = round((CLOCK() - t0) * 1000, 1)
        if tls is not None and res["type"] == "response" and res["selected"] != PROTOCOL_RDP:
            res["tls"] = await _tls_facts(writer, tls, timeout)
        return res
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except Exception:
            pass


async def _tls_facts(writer: asyncio.StreamWriter, tls: dict, timeout: float) -> dict:
    ctx = ssl.create_default_context(cafile=tls.get("ca_file"))     # verification stays ON
    try:
        await asyncio.wait_for(writer.start_tls(ctx, server_hostname=tls["server_name"]), timeout)
    except ssl.SSLCertVerificationError as exc:
        return {"verified": False, "reason": (exc.verify_message or "certificate not trusted")[:200],
                "hint": "RDP uses a self-signed certificate by default; set rdp.targets.<name>.ca_file to "
                        "the CA or certificate you trust to verify it"}
    except (ssl.SSLError, OSError, asyncio.TimeoutError) as exc:
        return {"verified": False, "reason": f"TLS handshake failed: {type(exc).__name__}"}
    sslobj = writer.get_extra_info("ssl_object")
    cert = sslobj.getpeercert() if sslobj else {}
    not_after = cert.get("notAfter")
    days = None
    if not_after:
        days = int((ssl.cert_time_to_seconds(not_after) - time.time()) // 86400)
    subject = ", ".join("=".join(x) for rdn in cert.get("subject", ()) for x in rdn)
    issuer = ", ".join("=".join(x) for rdn in cert.get("issuer", ()) for x in rdn)
    return {"verified": True, "version": sslobj.version() if sslobj else None, "subject": subject,
            "issuer": issuer, "not_after": not_after, "days_left": days}


Forwarder = Callable[[str, str, int], Awaitable[tuple[str, int, Callable[[], None]]]]


async def rdp_check(name: str, forwarder: Forwarder | None = None, config: dict | None = None) -> str:
    """Read-only RDP check of a configured target (direct, or through its ``via`` SSH server)."""
    try:
        config, t = _target(name, config)
    except RdpConfigError as exc:
        return _err("check", str(name), ErrorCode.CONFIG_ERROR, str(exc))
    close = None
    host, port = t["host"], t["port"]
    try:
        if t["via"]:
            if forwarder is None:
                return _err("check", name, ErrorCode.DEPENDENCY_MISSING, "SSH forwarding not available")
            try:
                host, port, close = await forwarder(t["via"], t["host"], t["port"])
            except ValueError:
                return _err("check", name, ErrorCode.CONFIG_ERROR, f"unknown SSH server profile {t['via']!r}")
            except Exception as exc:  # noqa: BLE001
                return _err("check", name, ErrorCode.UNREACHABLE,
                            f"SSH forward through {t['via']} failed: {type(exc).__name__}")
        tls = {"server_name": t["server_name"], "ca_file": t["ca_file"]}
        main = await negotiate(host, port, PROTOCOL_SSL | PROTOCOL_HYBRID | PROTOCOL_HYBRID_EX, t["timeout"], tls)
        tls_only = await negotiate(host, port, PROTOCOL_SSL, t["timeout"])
        legacy = await negotiate(host, port, PROTOCOL_RDP, t["timeout"])
    except asyncio.TimeoutError:
        return _err("check", name, ErrorCode.TIMEOUT, f"no RDP answer within {t['timeout']} s")
    except (ConnectionError, OSError) as exc:
        return _err("check", name, ErrorCode.UNREACHABLE, f"cannot connect: {type(exc).__name__}")
    except (ValueError, asyncio.IncompleteReadError) as exc:
        return _err("check", name, ErrorCode.PROTOCOL_ERROR, str(exc) or "service is not RDP")
    except Exception as exc:  # noqa: BLE001 - never leak backend text
        return _err("check", name, ErrorCode.INTERNAL, type(exc).__name__)
    finally:
        if close:
            close()

    if main["type"] == "failure":
        selected = None
    else:
        selected = PROTOCOL_NAMES.get(main["selected"], f"unknown_{main['selected']}")
    if tls_only["type"] == "failure" and tls_only.get("failure") in (5, 6):
        nla = "required"
    elif main.get("selected") in (PROTOCOL_HYBRID, PROTOCOL_HYBRID_EX):
        nla = "supported_not_required"
    else:
        nla = "not_supported"
    legacy_allowed = legacy["type"] != "failure"
    warnings = []
    if nla != "required":
        warnings.append("NLA is not enforced (enable 'Require Network Level Authentication')")
    if legacy_allowed:
        warnings.append("legacy RDP standard security (no TLS) is accepted")
    tls = main.get("tls")
    if tls and not tls.get("verified"):
        warnings.append("TLS certificate not verified: " + tls.get("reason", ""))
    if tls and tls.get("days_left") is not None and tls["days_left"] < 14:
        warnings.append(f"TLS certificate expires in {tls['days_left']} days")
    _audit("check", name, True)
    return json.dumps(_clean({
        "target": name, "status": "UP", "read_only": True, "host": t["host"], "port": t["port"],
        "via": t["via"], "latency_ms": main.get("latency_ms"), "selected_protocol": selected,
        "negotiation_failure": main.get("failure_name"), "nla": nla, "legacy_rdp_security_allowed": legacy_allowed,
        "tls": tls, "warnings": warnings, "login_attempted": False}))


def rdp_file(local_host: str, local_port: int, user: str | None) -> str:
    """Content of a .rdp file for the user's own RDP client. Never contains a password."""
    lines = [f"full address:s:{local_host}:{int(local_port)}", "prompt for credentials:i:1",
             "authentication level:i:2", "enablecredsspsupport:i:1", "negotiate security layer:i:1"]
    if user:
        lines.append(f"username:s:{user}")
    return "\r\n".join(lines) + "\r\n"


async def rdp_tunnel(name: str, open_forward: Callable[[str, int, str, int], Awaitable[dict]],
                     transport: dict | None = None, local_port: int = 0, config: dict | None = None) -> str:
    """Open a local SSH port forward to the target through its ``via`` server and return a .rdp file."""
    try:
        config, t = _target(name, config)
        if not t["via"]:
            raise RdpConfigError(f"rdp target {name} has no 'via' SSH server; connect directly to "
                                 f"{t['host']}:{t['port']}")
        if not 0 <= int(local_port) <= 65535 or 0 < int(local_port) < 1024:
            raise RdpConfigError("local_port must be 0 (any free port) or 1024..65535")
    except RdpConfigError as exc:
        return _err("tunnel", str(name), ErrorCode.CONFIG_ERROR, str(exc))
    res = await open_forward(t["via"], int(local_port), t["host"], t["port"])
    if not isinstance(res, dict) or "local" not in res:
        detail = res.get("error", "forward failed") if isinstance(res, dict) else "forward failed"
        return _err("tunnel", name, ErrorCode.UNREACHABLE, str(detail).split(":")[0])
    lhost, lport = res["local"].rsplit(":", 1)
    remote_note = None
    if (transport or {}).get("mode") == "http":
        remote_note = ("this connector runs as a remote HTTP backend: the tunnel listens on the backend "
                       "host's 127.0.0.1, not on your computer")
    _audit("tunnel", name, True)
    return json.dumps(_clean({
        "target": name, "status": "UP", "forward_id": res.get("forward_id"), "local": res["local"],
        "via": t["via"], "remote": f"{t['host']}:{t['port']}", "listens_on": "127.0.0.1 only",
        "rdp_file": rdp_file(lhost, int(lport), t["user"]), "password_in_file": False,
        "stop_with": {"tool": "stop_forward", "arguments": {"forward_id": res.get("forward_id")}},
        "note": remote_note}))


# ---------------------------------------------------------------- Windows over SSH (read only)
HEALTH_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$os=Get-CimInstance Win32_OperatingSystem
$ts='HKLM:\System\CurrentControlSet\Control\Terminal Server'
$rdp=Get-ItemProperty $ts
$tcp=Get-ItemProperty "$ts\WinStations\RDP-Tcp"
$pend=(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
$svc=@(Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State<>'Running'" | Select-Object -First 50 -ExpandProperty Name)
$disks=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {[pscustomobject]@{drive=$_.DeviceID;size_gb=[math]::Round($_.Size/1GB,1);free_gb=[math]::Round($_.FreeSpace/1GB,1)}})
$fw=@(Get-NetFirewallRule -Name 'RemoteDesktop-*' | Where-Object {$_.Enabled -eq 'True'}).Count
$term=(Get-Service TermService).Status.ToString()
[pscustomobject]@{os=$os.Caption;version=$os.Version;hostname=$env:COMPUTERNAME;last_boot=$os.LastBootUpTime.ToUniversalTime().ToString('o');rdp_enabled=($rdp.fDenyTSConnections -eq 0);nla_required=($tcp.UserAuthentication -eq 1);rdp_port=$tcp.PortNumber;security_layer=$tcp.SecurityLayer;termservice=$term;firewall_rdp_rules_enabled=$fw;pending_reboot=$pend;stopped_auto_services=$svc;disks=$disks} | ConvertTo-Json -Depth 3 -Compress
""".strip()


def health_command() -> str:
    """Fixed, read-only PowerShell command (EncodedCommand: no quoting or injection surface)."""
    return ("powershell -NoProfile -NonInteractive -EncodedCommand "
            + base64.b64encode(HEALTH_PS.encode("utf-16-le")).decode())


SshRun = Callable[[str, str, int], Awaitable[dict]]


def _last_json_line(text: str) -> Any:
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("no JSON in output")


async def windows_health(server: str, ssh_run: SshRun) -> str:
    """Read-only Windows health over the SSH profile ``server``."""
    r = await ssh_run(server, health_command(), 60)
    if r.get("unknown_server"):
        return _err("windows_health", server, ErrorCode.CONFIG_ERROR, "unknown SSH server profile")
    if r.get("error"):
        return _err("windows_health", server, ErrorCode.UNREACHABLE, str(r["error"]).split(":")[0])
    if r.get("exit_code") != 0:
        return _err("windows_health", server, ErrorCode.UNAVAILABLE,
                    "not a Windows host, or PowerShell is not available over SSH")
    try:
        data = _last_json_line(r.get("stdout", ""))
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return _err("windows_health", server, ErrorCode.PROTOCOL_ERROR, "unexpected PowerShell output")
    disks = data.get("disks") or []
    if isinstance(disks, dict):
        disks = [disks]
    services = data.get("stopped_auto_services") or []
    if isinstance(services, str):
        services = [services]
    warnings = []
    for d in disks:
        try:
            if d.get("size_gb") and float(d["free_gb"]) / float(d["size_gb"]) < 0.10:
                warnings.append(f"disk {d.get('drive')} below 10 % free")
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if data.get("pending_reboot"):
        warnings.append("reboot pending")
    if services:
        warnings.append(f"{len(services)} automatic service(s) not running")
    if data.get("rdp_enabled") is False:
        warnings.append("RDP is disabled")
    if data.get("rdp_enabled") and data.get("nla_required") is False:
        warnings.append("RDP without NLA")
    if data.get("termservice") not in (None, "Running"):
        warnings.append("TermService is not running")
    _audit("windows_health", server, True)
    out = {k: data.get(k) for k in ("os", "version", "hostname", "last_boot", "rdp_enabled", "nla_required",
                                     "rdp_port", "security_layer", "termservice", "firewall_rdp_rules_enabled",
                                     "pending_reboot")}
    return json.dumps(_clean({"server": server, "status": "UP", "read_only": True, **out, "disks": disks,
                              "stopped_auto_services": services, "warnings": warnings}))


_QUSER_RE = re.compile(r"^(?P<cur>[ >])(?P<user>\S+)\s+(?:(?P<sess>[A-Za-z][\w#-]*)\s+)?(?P<id>\d+)\s+"
                       r"(?P<state>\S+)\s+(?P<idle>\S+)\s+(?P<logon>.+?)\s*$")


def parse_quser(text: str) -> list[dict]:
    rows = []
    for line in (text or "").splitlines()[1:]:
        m = _QUSER_RE.match(line if line[:1] in (" ", ">") else " " + line)
        if m:
            rows.append({"user": m["user"], "session": m["sess"], "id": int(m["id"]), "state": m["state"],
                         "idle": m["idle"], "logon": m["logon"], "current": m["cur"] == ">"})
    return rows


async def rdp_sessions(server: str, ssh_run: SshRun) -> str:
    """Who is logged on to a Windows host (``quser``), over the SSH profile ``server``. Read-only."""
    r = await ssh_run(server, "quser", 30)
    if r.get("unknown_server"):
        return _err("rdp_sessions", server, ErrorCode.CONFIG_ERROR, "unknown SSH server profile")
    if r.get("error"):
        return _err("rdp_sessions", server, ErrorCode.UNREACHABLE, str(r["error"]).split(":")[0])
    out, err = r.get("stdout") or "", r.get("stderr") or ""
    if r.get("exit_code") not in (0, 1):
        return _err("rdp_sessions", server, ErrorCode.UNAVAILABLE, "quser is not available (not Windows?)")
    sessions = parse_quser(out)
    if r.get("exit_code") == 1 and not sessions and "no user" not in (out + err).lower():
        return _err("rdp_sessions", server, ErrorCode.UNAVAILABLE, "quser is not available (not Windows?)")
    _audit("rdp_sessions", server, True)
    return json.dumps(_clean({"server": server, "status": "UP", "read_only": True, "count": len(sessions),
                              "sessions": sessions}))
