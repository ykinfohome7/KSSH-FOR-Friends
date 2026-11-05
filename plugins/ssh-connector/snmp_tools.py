"""Read-only SNMP tools for KSSH (MCP layer over the vendored kssh_snmp library).

Safety model
* Tools accept only a configured **device name**, never a free host/IP.
* Every device host must lie inside ``snmp.allowed_networks`` (required).
* Only GET / GETNEXT / GETBULK / bounded WALK. There is no SET anywhere.
* Secrets are referenced by environment-variable names in servers.yaml;
  values are never returned, logged or echoed in errors.
* Each call is bounded (OID count, walk results/seconds, timeout, retries)
  and recorded in the command history as ``snmp:<tool> ...``.

Config (servers.yaml)::

    snmp:
      allowed_networks: [192.0.2.30/32]
      devices:
        realhd-switch:
          host: 192.0.2.30
          adapter: realhd            # generic | realhd | cisco | unifi
          version: "3"
          user: admin
          security_level: authPriv
          auth_protocol: SHA
          priv_protocol: DES
          allow_legacy_des: true
          auth_env: KSSH_REALHD_AUTH_PASS
          priv_env: KSSH_REALHD_PRIV_PASS
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable

import yaml

from kssh_snmp.errors import ErrorCode, MonitorError, status_for
from kssh_snmp.snmp_adapters import make_adapter
from kssh_snmp.snmp_client import (END_OF_MIB, NO_SUCH, Allowlist, SnmpClient, SnmpCredentials, SnmpLimits,
                                   SnmpTarget, normalize_oid)
from kssh_snmp.snmp_interfaces import collect_sample, compute_rates

MAX_VALUE_CHARS = 1024
MAX_WALK_RESULTS = 2000
MAX_SAMPLE_SECONDS = 30

# Test hook: tests replace this with a factory returning a fake backend.
BACKEND_FACTORY: Callable[[str, dict], Any] | None = None
# Where audit entries go (server.py passes its HISTORY deque).
AUDIT: Any = None
# Clock and sleep used between interface samples (replaceable in tests).
CLOCK: Callable[[], float] = time.time
SLEEP = asyncio.sleep


class SnmpConfigError(ValueError):
    pass


def _config_path() -> str:
    from pathlib import Path
    default = str(Path(__file__).with_name("servers.yaml"))
    return os.path.expanduser(os.environ.get("SSH_CONNECTOR_CONFIG", default))


def load_snmp_config(path: str | None = None) -> dict:
    """Return {"allowlist": Allowlist, "devices": {name: cfg}} or raise SnmpConfigError."""
    path = path or _config_path()
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except OSError as exc:
        raise SnmpConfigError(f"cannot read config: {type(exc).__name__}") from None
    section = raw.get("snmp")
    if not section:
        raise SnmpConfigError("no 'snmp' section in servers.yaml (see servers.example.yaml)")
    if not isinstance(section, dict):
        raise SnmpConfigError("'snmp' must be a mapping")
    try:
        allowlist = Allowlist(section.get("allowed_networks") or [])
    except MonitorError as exc:
        raise SnmpConfigError(f"snmp.allowed_networks: {exc.message}") from None
    devices = section.get("devices") or {}
    if not isinstance(devices, dict) or not devices:
        raise SnmpConfigError("snmp.devices must be a non-empty mapping")
    out = {}
    for name, cfg in devices.items():
        if not isinstance(cfg, dict) or not cfg.get("host"):
            raise SnmpConfigError(f"snmp device {name}: host is required")
        if not allowlist.allows(str(cfg["host"])):
            raise SnmpConfigError(f"snmp device {name}: host is not in snmp.allowed_networks")
        try:
            SnmpCredentials.from_config(cfg)
            SnmpTarget(str(cfg["host"]), int(cfg.get("port", 161)),
                       float(cfg.get("timeout", 2.0)), int(cfg.get("retries", 1)))
            make_adapter(cfg.get("adapter", "generic"), None)  # validates the adapter name only
        except MonitorError as exc:
            raise SnmpConfigError(f"snmp device {name}: {exc.message}") from None
        out[str(name)] = cfg
    return {"allowlist": allowlist, "devices": out}


def _resolve(config: dict, device: str) -> tuple[str, dict]:
    for name, cfg in config["devices"].items():
        if device == name or device.casefold() == name.casefold():
            return name, cfg
    raise SnmpConfigError(f"unknown snmp device '{device}'. Known: {', '.join(config['devices'])}")


def _client(config: dict, name: str, cfg: dict) -> SnmpClient:
    creds = SnmpCredentials.from_config(cfg)
    target = SnmpTarget(str(cfg["host"]), int(cfg.get("port", 161)),
                        float(cfg.get("timeout", 2.0)), int(cfg.get("retries", 1)))
    backend = BACKEND_FACTORY(name, cfg) if BACKEND_FACTORY else None
    limits = SnmpLimits(max_concurrency=2, max_walk_results=MAX_WALK_RESULTS)
    return SnmpClient(target, creds, config["allowlist"], limits=limits, backend=backend)


def _value(v: Any) -> Any:
    if v is NO_SUCH:
        return "NO_SUCH_OBJECT"
    if v is END_OF_MIB:
        return "END_OF_MIB_VIEW"
    if isinstance(v, str) and len(v) > MAX_VALUE_CHARS:
        return v[:MAX_VALUE_CHARS] + "...[truncated]"
    return v


def _clean_output(obj: Any) -> Any:
    """Apply value rules (markers, length cap) to every value in a tool result."""
    if isinstance(obj, dict):
        return {k: _clean_output(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_output(v) for v in obj]
    return _value(obj)


def _audit(tool: str, device: str, detail: str, ok: bool) -> None:
    if AUDIT is not None:
        AUDIT.append({"ts": time.time(), "server": device, "command": f"snmp:{tool} {detail}".strip(),
                      "exit_code": 0 if ok else 1})


async def _call(tool: str, device: str, detail: str, work) -> str:
    """Run one tool call with uniform error handling and audit."""
    try:
        config = load_snmp_config()
        name, cfg = _resolve(config, device)
        client = _client(config, name, cfg)
        timeout = float(cfg.get("timeout", 2.0)) * (int(cfg.get("retries", 1)) + 1) * 4 + MAX_SAMPLE_SECONDS + 30
        result = await asyncio.wait_for(work(client, name, cfg), timeout=timeout)
        _audit(tool, name, detail, True)
        return json.dumps({"device": name, "status": "UP", "read_only": True, **_clean_output(result)},
                          ensure_ascii=False, indent=2, default=str)
    except SnmpConfigError as exc:
        _audit(tool, device, detail, False)
        return json.dumps({"device": device, "status": "UNKNOWN", "error_code": "CONFIG_ERROR", "detail": str(exc)})
    except MonitorError as exc:
        _audit(tool, device, detail, False)
        return json.dumps({"device": device, "status": status_for(exc.code).value,
                           "error_code": exc.code.value, "detail": exc.message})
    except asyncio.TimeoutError:
        _audit(tool, device, detail, False)
        return json.dumps({"device": device, "status": "UNKNOWN", "error_code": ErrorCode.DEADLINE_EXCEEDED.value})
    except Exception as exc:  # never echo backend texts: they may contain secrets
        _audit(tool, device, detail, False)
        return json.dumps({"device": device, "status": "UNKNOWN", "error_code": "INTERNAL",
                           "detail": type(exc).__name__})


def _oid_list(oids: list[str]) -> list[str]:
    if not isinstance(oids, list) or not oids:
        raise MonitorError(ErrorCode.CONFIG_ERROR, "oids must be a non-empty list")
    return [normalize_oid(str(o)) for o in oids]


# --------------------------------------------------------------------------- tools


async def snmp_health(device: str) -> str:
    async def work(client, name, cfg):
        return await make_adapter(cfg.get("adapter", "generic"), client).health()
    return await _call("snmp_health", device, "", work)


async def snmp_device_info(device: str) -> str:
    async def work(client, name, cfg):
        adapter = make_adapter(cfg.get("adapter", "generic"), client)
        info = await adapter.device_info()
        info["environment"] = await adapter.environment()
        info["capabilities"] = adapter.capabilities()
        return info
    return await _call("snmp_device_info", device, "", work)


async def snmp_get(device: str, oids: list[str]) -> str:
    async def work(client, name, cfg):
        res = await client.get(_oid_list(oids))
        return {"values": {o: _value(v) for o, v in res.items()}}
    return await _call("snmp_get", device, " ".join(map(str, oids or []))[:200], work)


async def snmp_getnext(device: str, oids: list[str]) -> str:
    async def work(client, name, cfg):
        res = await client.getnext(_oid_list(oids))
        return {"values": [{"oid": o, "value": _value(v)} for o, v in res]}
    return await _call("snmp_getnext", device, " ".join(map(str, oids or []))[:200], work)


async def snmp_getbulk(device: str, oids: list[str], max_repetitions: int = 10) -> str:
    async def work(client, name, cfg):
        res = await client.getbulk(_oid_list(oids), max_repetitions=max_repetitions)
        return {"values": [{"oid": o, "value": _value(v)} for o, v in res]}
    return await _call("snmp_getbulk", device, " ".join(map(str, oids or []))[:200], work)


async def snmp_walk(device: str, oid: str, max_results: int = 500) -> str:
    async def work(client, name, cfg):
        cap = max(1, min(int(max_results), MAX_WALK_RESULTS))
        res = await client.walk(oid, max_results=cap)
        return {"root": res.root, "count": len(res.items), "truncated": res.truncated, "reason": res.reason,
                "duration_s": res.duration_s, "values": [{"oid": o, "value": _value(v)} for o, v in res.items]}
    return await _call("snmp_walk", device, str(oid)[:200], work)


def _iface_rows(rates: dict, include: str) -> list[dict]:
    import fnmatch
    rows = []
    for idx, e in sorted(rates["interfaces"].items()):
        if include and include != "*" and not fnmatch.fnmatchcase(str(e.get("name") or ""), include):
            continue
        rows.append(e)
    return rows


async def snmp_interfaces(device: str, include: str = "*") -> str:
    async def work(client, name, cfg):
        sample = await collect_sample(client, CLOCK())
        rates = compute_rates(None, sample)
        rows = _iface_rows(rates, include)
        for r in rows:
            r.pop("rates", None)
            r["events"] = [e for e in r["events"] if e != "BASELINE"]
        return {"sys_uptime_ticks": sample.sys_uptime_ticks, "count": len(rows), "interfaces": rows}
    return await _call("snmp_interfaces", device, include, work)


async def snmp_interface_stats(device: str, include: str = "*", sample_seconds: int = 5) -> str:
    async def work(client, name, cfg):
        wait = max(1, min(int(sample_seconds), MAX_SAMPLE_SECONDS))
        first = await collect_sample(client, CLOCK())
        await SLEEP(wait)
        second = await collect_sample(client, CLOCK())
        rates = compute_rates(first, second)
        rows = _iface_rows(rates, include)
        return {"interval_s": rates["interval_s"], "device_events": rates["device_events"],
                "count": len(rows), "interfaces": rows}
    return await _call("snmp_interface_stats", device, f"{include} {sample_seconds}s", work)


TOOLS = ("snmp_health", "snmp_device_info", "snmp_get", "snmp_getnext", "snmp_getbulk", "snmp_walk",
         "snmp_interfaces", "snmp_interface_stats")
