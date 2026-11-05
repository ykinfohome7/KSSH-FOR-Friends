# GENERATED COPY of monitoring/snmp_client.py - edit the original, then run scripts/sync_plugin_snmp.py
"""Bounded, read-only SNMP client.

Design:
* ``SnmpClient`` holds all safety rules (allowlist, OID validation, request
  limits, concurrency, walk bounds). It never offers a SET operation.
* A *backend* does the wire work. ``PysnmpBackend`` talks to real devices via
  PySNMP 7.x; tests inject a fake backend, so the safety rules are tested
  without any network access.
* Credentials are only referenced by environment-variable *names* in config.
  Values are read at request time, registered for log scrubbing and never put
  into results, reprs or error messages.
* Versions are never downgraded: a v3 failure is reported as a v3 failure.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from .errors import ErrorCode, MonitorError
from .logutil import register_secret

# --------------------------------------------------------------------------- values


class _Marker:
    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name


NO_SUCH = _Marker("NO_SUCH")          # noSuchObject / noSuchInstance / v1 noSuchName
END_OF_MIB = _Marker("END_OF_MIB")    # endOfMibView

VarBind = tuple[str, Any]

# --------------------------------------------------------------------------- OIDs

_OID_RE = re.compile(r"^\.?\d+(\.\d+)+$")
_MAX_OID_LEN = 128


def parse_oid(oid: str) -> tuple[int, ...]:
    if not isinstance(oid, str) or not _OID_RE.match(oid):
        raise MonitorError(ErrorCode.CONFIG_ERROR, "invalid OID (numeric dotted form required)")
    parts = tuple(int(p) for p in oid.lstrip(".").split("."))
    if len(parts) > _MAX_OID_LEN or any(p >= 2**32 for p in parts) or parts[0] > 2:
        raise MonitorError(ErrorCode.CONFIG_ERROR, "invalid OID")
    return parts


def normalize_oid(oid: str) -> str:
    return ".".join(str(p) for p in parse_oid(oid))


def oid_in_subtree(oid: str, root: str) -> bool:
    o, r = parse_oid(oid), parse_oid(root)
    return len(o) > len(r) and o[: len(r)] == r


# --------------------------------------------------------------------------- allowlist


class Allowlist:
    """Explicit list of hosts/networks the monitor may contact.

    IP entries may be addresses or CIDR networks. Hostname entries match by
    exact (case-insensitive) name only; prefer IP entries, since DNS answers
    are outside our control.
    """

    def __init__(self, entries: Iterable[str]):
        self._networks: list[ipaddress._BaseNetwork] = []
        self._names: set[str] = set()
        for raw in entries:
            entry = str(raw).strip()
            if not entry:
                continue
            try:
                self._networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                if not re.match(r"^[A-Za-z0-9.-]{1,253}$", entry):
                    raise MonitorError(ErrorCode.CONFIG_ERROR, "invalid allowlist entry")
                self._names.add(entry.lower().rstrip("."))
        if not self._networks and not self._names:
            raise MonitorError(ErrorCode.CONFIG_ERROR, "allowlist is empty")

    def allows(self, host: str) -> bool:
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            return str(host).lower().rstrip(".") in self._names
        return any(addr in net for net in self._networks)

    def check(self, host: str) -> None:
        if not self.allows(host):
            raise MonitorError(ErrorCode.NOT_ALLOWED, "target is not in allowed_networks")


# --------------------------------------------------------------------------- credentials

AUTH_PROTOCOLS = {"SHA", "SHA224", "SHA256", "SHA384", "SHA512"}   # MD5 deliberately unsupported
PRIV_PROTOCOLS = {"AES", "AES192", "AES256", "DES"}
SECURITY_LEVELS = {"noAuthNoPriv", "authNoPriv", "authPriv"}
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


@dataclass(frozen=True)
class SnmpCredentials:
    version: str = "3"
    user: str = ""
    security_level: str = "authPriv"
    auth_protocol: str = "SHA"
    priv_protocol: str = "AES"
    auth_env: str = ""
    priv_env: str = ""
    community_env: str = ""
    allow_legacy_snmp: bool = False   # required for v1/v2c
    allow_legacy_des: bool = False    # required for DES privacy

    @classmethod
    def from_config(cls, cfg: dict) -> "SnmpCredentials":
        if not isinstance(cfg, dict):
            raise MonitorError(ErrorCode.CONFIG_ERROR, "snmp section must be an object")
        creds = cls(
            version=str(cfg.get("version", "3")),
            user=str(cfg.get("user", "")),
            security_level=str(cfg.get("security_level", "authPriv")),
            auth_protocol=str(cfg.get("auth_protocol", "SHA")).upper(),
            priv_protocol=str(cfg.get("priv_protocol", "AES")).upper(),
            auth_env=str(cfg.get("auth_env", "")),
            priv_env=str(cfg.get("priv_env", "")),
            community_env=str(cfg.get("community_env", "")),
            allow_legacy_snmp=cfg.get("allow_legacy_snmp") is True,
            allow_legacy_des=cfg.get("allow_legacy_des") is True,
        )
        creds.validate()
        return creds

    def validate(self) -> None:
        bad = lambda msg: MonitorError(ErrorCode.CONFIG_ERROR, msg)  # noqa: E731
        if self.version not in {"1", "2c", "3"}:
            raise bad("unsupported SNMP version")
        if self.version in {"1", "2c"}:
            if not self.allow_legacy_snmp:
                raise bad("SNMPv1/v2c requires allow_legacy_snmp: true for this device")
            if not _ENV_NAME.match(self.community_env):
                raise bad("legacy SNMP requires community_env (an environment variable name)")
            return
        if not self.user or len(self.user) > 32:
            raise bad("SNMPv3 requires user (1..32 chars)")
        if self.security_level not in SECURITY_LEVELS:
            raise bad("security_level must be noAuthNoPriv, authNoPriv or authPriv")
        if self.security_level in {"authNoPriv", "authPriv"}:
            if self.auth_protocol not in AUTH_PROTOCOLS:
                raise bad("unsupported auth_protocol (SHA, SHA224, SHA256, SHA384, SHA512)")
            if not _ENV_NAME.match(self.auth_env):
                raise bad("auth_env must name an environment variable")
        if self.security_level == "authPriv":
            if self.priv_protocol not in PRIV_PROTOCOLS:
                raise bad("unsupported priv_protocol (AES, AES192, AES256, DES)")
            if self.priv_protocol == "DES" and not self.allow_legacy_des:
                raise bad("DES requires allow_legacy_des: true for this device")
            if not _ENV_NAME.match(self.priv_env):
                raise bad("priv_env must name an environment variable")

    def resolve_secrets(self, environ: dict | None = None) -> dict[str, str]:
        """Read secret values from the environment at request time."""
        env = os.environ if environ is None else environ
        wanted: dict[str, str] = {}
        if self.version in {"1", "2c"}:
            wanted["community"] = self.community_env
        else:
            if self.security_level in {"authNoPriv", "authPriv"}:
                wanted["auth_key"] = self.auth_env
            if self.security_level == "authPriv":
                wanted["priv_key"] = self.priv_env
        out = {}
        for key, env_name in wanted.items():
            value = env.get(env_name)
            if not value:
                raise MonitorError(ErrorCode.SECRET_MISSING, f"environment variable {env_name} is not set")
            if key in {"auth_key", "priv_key"} and len(value) < 8:
                raise MonitorError(ErrorCode.CONFIG_ERROR, f"{env_name}: SNMPv3 keys need at least 8 characters")
            register_secret(value)
            out[key] = value
        return out

    def describe(self) -> dict:
        """Secret-free description for logs and reports."""
        d = {"version": self.version}
        if self.version == "3":
            d.update(user=self.user, security_level=self.security_level)
            if self.security_level != "noAuthNoPriv":
                d["auth_protocol"] = self.auth_protocol
            if self.security_level == "authPriv":
                d["priv_protocol"] = self.priv_protocol
        return d


# --------------------------------------------------------------------------- target / limits


@dataclass(frozen=True)
class SnmpTarget:
    host: str
    port: int = 161
    timeout: float = 2.0
    retries: int = 1

    def __post_init__(self):
        if not self.host or len(self.host) > 253:
            raise MonitorError(ErrorCode.CONFIG_ERROR, "invalid SNMP host")
        if not 1 <= int(self.port) <= 65535:
            raise MonitorError(ErrorCode.CONFIG_ERROR, "invalid SNMP port")
        if not 0.2 <= float(self.timeout) <= 10:
            raise MonitorError(ErrorCode.CONFIG_ERROR, "SNMP timeout must be 0.2..10 s")
        if not 0 <= int(self.retries) <= 5:
            raise MonitorError(ErrorCode.CONFIG_ERROR, "SNMP retries must be 0..5")


@dataclass(frozen=True)
class SnmpLimits:
    max_oids_per_request: int = 32
    max_repetitions: int = 25
    max_walk_results: int = 2000
    max_walk_seconds: float = 20.0
    max_concurrency: int = 4
    # Hard ceilings: callers can lower the limits above, never exceed these.
    HARD_WALK_RESULTS: int = field(default=10000, init=False, repr=False)
    HARD_WALK_SECONDS: float = field(default=120.0, init=False, repr=False)


@dataclass
class WalkResult:
    root: str
    items: list[VarBind]
    truncated: bool = False
    reason: str | None = None   # LIMIT_EXCEEDED detail when truncated
    duration_s: float = 0.0


# --------------------------------------------------------------------------- backends


class SnmpBackend(Protocol):
    async def get(self, oids: list[str]) -> list[VarBind]: ...
    async def getnext(self, oids: list[str]) -> list[VarBind]: ...
    async def getbulk(self, non_repeaters: int, max_repetitions: int, oids: list[str]) -> list[VarBind]: ...


_AUTH_FAIL_HINTS = (
    "unknownusername", "unknown usm user", "wrongdigest", "wrong snmp pdu digest",
    "decryptionerror", "unsupportedseclevel", "unsupported security level",
    "authenticationfailure", "authentication failure",
)
_TIMEOUT_HINTS = ("timeout", "timed out", "no snmp response")


def classify_error_indication(text: str) -> ErrorCode:
    t = text.lower().replace(" ", "")
    raw = text.lower()
    if any(h.replace(" ", "") in t for h in _AUTH_FAIL_HINTS):
        return ErrorCode.AUTH_FAILED
    if any(h in raw for h in _TIMEOUT_HINTS):
        return ErrorCode.TIMEOUT
    if "refused" in raw or "unreachable" in raw or "no route" in raw:
        return ErrorCode.UNREACHABLE
    return ErrorCode.PROTOCOL_ERROR


class PysnmpBackend:
    """PySNMP 7.x backend.

    STATUS: written against the documented pysnmp 7 ``hlapi.v3arch.asyncio``
    API but NOT executed in CI or locally (PyPI blocked in the authoring
    environment). Treat as untested until validated against a lab agent.
    """

    _AUTH_MAP = {
        "SHA": "USM_AUTH_HMAC96_SHA", "SHA224": "USM_AUTH_HMAC128_SHA224",
        "SHA256": "USM_AUTH_HMAC192_SHA256", "SHA384": "USM_AUTH_HMAC256_SHA384",
        "SHA512": "USM_AUTH_HMAC384_SHA512",
    }
    _PRIV_MAP = {
        "AES": "USM_PRIV_CFB128_AES", "AES192": "USM_PRIV_CFB192_AES",
        "AES256": "USM_PRIV_CFB256_AES", "DES": "USM_PRIV_CBC56_DES",
    }

    def __init__(self, target: SnmpTarget, creds: SnmpCredentials):
        self.target = target
        self.creds = creds

    def _api(self):
        try:
            from pysnmp.hlapi.v3arch import asyncio as api  # type: ignore
        except ImportError as exc:
            raise MonitorError(ErrorCode.DEPENDENCY_MISSING, "pysnmp>=7.1,<8 is not installed") from exc
        return api

    def _auth_data(self, api):
        secrets = self.creds.resolve_secrets()
        if self.creds.version in {"1", "2c"}:
            return api.CommunityData(secrets["community"], mpModel=0 if self.creds.version == "1" else 1)
        kwargs = {}
        level = self.creds.security_level
        if level in {"authNoPriv", "authPriv"}:
            proto = getattr(api, self._AUTH_MAP[self.creds.auth_protocol], None)
            if proto is None:
                raise MonitorError(ErrorCode.DEPENDENCY_MISSING, "auth protocol not available in pysnmp")
            kwargs.update(authKey=secrets["auth_key"], authProtocol=proto)
        if level == "authPriv":
            proto = getattr(api, self._PRIV_MAP[self.creds.priv_protocol], None)
            if proto is None:
                raise MonitorError(ErrorCode.DEPENDENCY_MISSING, "privacy protocol not available in pysnmp")
            kwargs.update(privKey=secrets["priv_key"], privProtocol=proto)
        return api.UsmUserData(self.creds.user, **kwargs)

    @staticmethod
    def _convert(value) -> Any:
        cls = type(value).__name__
        if cls in {"NoSuchObject", "NoSuchInstance"}:
            return NO_SUCH
        if cls == "EndOfMibView":
            return END_OF_MIB
        if cls in {"Integer", "Integer32", "Counter32", "Counter64", "Gauge32", "Unsigned32", "TimeTicks"}:
            return int(value)
        if cls == "OctetString":
            raw = value.asOctets()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.hex(":")
        return value.prettyPrint()

    async def _run(self, kind: str, oids: list[str], non_rep: int = 0, max_rep: int = 0) -> list[VarBind]:
        api = self._api()
        auth = self._auth_data(api)
        engine = api.SnmpEngine()
        try:
            transport = await api.UdpTransportTarget.create(
                (self.target.host, self.target.port),
                timeout=self.target.timeout, retries=self.target.retries,
            )
            objs = [api.ObjectType(api.ObjectIdentity(o)) for o in oids]
            ctx = api.ContextData()
            if kind == "get":
                res = await api.get_cmd(engine, auth, transport, ctx, *objs, lookupMib=False)
            elif kind == "next":
                res = await api.next_cmd(engine, auth, transport, ctx, *objs, lookupMib=False)
            else:
                res = await api.bulk_cmd(engine, auth, transport, ctx, non_rep, max_rep, *objs, lookupMib=False)
            err_ind, err_status, _err_index, var_binds = res
            if err_ind:
                code = classify_error_indication(str(err_ind))
                raise MonitorError(code, f"snmp {code.value.lower()}")
            if err_status:
                name = str(err_status.prettyPrint() if hasattr(err_status, "prettyPrint") else err_status)
                if name == "noSuchName":
                    return [(o, NO_SUCH) for o in oids]
                raise MonitorError(ErrorCode.PROTOCOL_ERROR, f"snmp error-status {name}")
            out: list[VarBind] = []
            for vb in var_binds:
                oid, val = vb[0], vb[1]
                out.append((str(oid), self._convert(val)))
            return out
        except MonitorError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # backend text may echo secrets: keep type only
            raise MonitorError(ErrorCode.INTERNAL, f"snmp backend {type(exc).__name__}") from None
        finally:
            try:
                engine.close_dispatcher()
            except Exception:
                pass

    async def get(self, oids):
        return await self._run("get", oids)

    async def getnext(self, oids):
        return await self._run("next", oids)

    async def getbulk(self, non_repeaters, max_repetitions, oids):
        return await self._run("bulk", oids, non_repeaters, max_repetitions)


# --------------------------------------------------------------------------- client


class SnmpClient:
    """Read-only SNMP operations with enforced limits. There is no ``set``."""

    def __init__(
        self,
        target: SnmpTarget,
        creds: SnmpCredentials,
        allowlist: Allowlist,
        *,
        limits: SnmpLimits | None = None,
        backend: SnmpBackend | None = None,
    ):
        allowlist.check(target.host)
        creds.validate()
        self.target = target
        self.creds = creds
        self.limits = limits or SnmpLimits()
        self._backend = backend or PysnmpBackend(target, creds)
        self._sem = asyncio.Semaphore(max(1, min(self.limits.max_concurrency, 16)))

    def _check_oids(self, oids: list[str]) -> list[str]:
        if not oids:
            raise MonitorError(ErrorCode.CONFIG_ERROR, "no OIDs given")
        if len(oids) > self.limits.max_oids_per_request:
            raise MonitorError(ErrorCode.LIMIT_EXCEEDED, "too many OIDs in one request")
        return [normalize_oid(o) for o in oids]

    async def _call(self, coro_factory):
        # Fail fast (SECRET_MISSING, an UNKNOWN status) before touching the
        # network, whatever backend is in use.
        self.creds.resolve_secrets()
        async with self._sem:
            return await coro_factory()

    async def get(self, oids: list[str]) -> dict[str, Any]:
        norm = self._check_oids(oids)
        result = await self._call(lambda: self._backend.get(norm))
        return {normalize_oid(o): v for o, v in result}

    async def getnext(self, oids: list[str]) -> list[VarBind]:
        norm = self._check_oids(oids)
        result = await self._call(lambda: self._backend.getnext(norm))
        return [(normalize_oid(o), v) for o, v in result]

    async def getbulk(self, oids: list[str], max_repetitions: int = 10, non_repeaters: int = 0) -> list[VarBind]:
        if self.creds.version == "1":
            raise MonitorError(ErrorCode.CONFIG_ERROR, "GETBULK is not available in SNMPv1")
        norm = self._check_oids(oids)
        reps = max(1, min(int(max_repetitions), self.limits.max_repetitions))
        nrep = max(0, min(int(non_repeaters), len(norm)))
        result = await self._call(lambda: self._backend.getbulk(nrep, reps, norm))
        return [(normalize_oid(o), v) for o, v in result]

    async def walk(
        self,
        root: str,
        *,
        max_results: int | None = None,
        max_seconds: float | None = None,
        use_bulk: bool = True,
    ) -> WalkResult:
        """Bounded walk of one subtree. Never unbounded: limits are capped."""
        root = normalize_oid(root)
        lim = self.limits
        cap_results = min(max_results or lim.max_walk_results, lim.max_walk_results, lim.HARD_WALK_RESULTS)
        cap_seconds = min(max_seconds or lim.max_walk_seconds, lim.max_walk_seconds, lim.HARD_WALK_SECONDS)
        bulk = use_bulk and self.creds.version != "1"
        started = time.monotonic()
        items: list[VarBind] = []
        cursor = root
        last = parse_oid(root)

        def finish(truncated=False, reason=None):
            return WalkResult(root, items, truncated, reason, round(time.monotonic() - started, 3))

        while True:
            remaining = cap_seconds - (time.monotonic() - started)
            if remaining <= 0:
                return finish(True, "max_walk_seconds reached")
            want = min(lim.max_repetitions, cap_results - len(items) + 1)
            step = self.getbulk([cursor], want) if bulk else self.getnext([cursor])
            try:
                batch = await asyncio.wait_for(step, timeout=remaining)
            except asyncio.TimeoutError:
                return finish(True, "max_walk_seconds reached")
            if not batch:
                return finish()
            for oid, value in batch:
                if value is END_OF_MIB or not oid_in_subtree(oid, root):
                    return finish()
                parsed = parse_oid(oid)
                if parsed <= last:
                    # Agent returned a non-increasing OID: stop instead of looping forever.
                    raise MonitorError(ErrorCode.PROTOCOL_ERROR, "agent returned non-increasing OID")
                last = parsed
                if len(items) >= cap_results:
                    return finish(True, "max_walk_results reached")
                items.append((oid, value))
                cursor = oid
