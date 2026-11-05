"""Where can this connector be reached? An honest, read-only answer.

Used by the existing ``tailscale_status`` MCP tool. It never starts, changes
or enables anything: no ``tailscale up/login/serve/funnel``, no listeners.

It keeps four things apart:

* the transport this process uses (local stdio for the Claude Code / Codex
  plugin, or streamable HTTP for a remote MCP backend);
* the address the operator configured (``SSH_CONNECTOR_PUBLIC_URL`` or the
  file named by ``SSH_CONNECTOR_PUBLIC_URL_FILE``);
* what Tailscale itself reports (``tailscale status --json`` and
  ``tailscale serve status --json``): Serve = tailnet only, Funnel = public;
* whether reachability was actually checked (only on request).

A configured address is never presented as working, a tailnet name is never
presented as public, and nothing is guessed from the repository or device
name. Token-bearing URLs are never returned: only the safe templates
``https://<host>/<TOKEN>/mcp`` and ``Authorization: Bearer <TOKEN>``.

Само четене: показва конфигурирания адрес, режима (Serve/Funnel/unknown),
дали достъпността е проверена и кой MCP endpoint се отнася — без токени.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

TOKEN_PLACEHOLDER = "<TOKEN>"
HOST_PLACEHOLDER = "<host>"
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}))*\.?$")
_TSKEY_RE = re.compile(r"tskey-[A-Za-z0-9_-]+")
# A long random path segment (as used for the secret-path MCP URL) or a bearer value.
_SECRET_SEGMENT_RE = re.compile(r"(?<=/)[A-Za-z0-9_\-]{20,}(?=/(?:mcp|sse|sudo)\b)")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
BACKEND_STATES_OK = {"Running"}
BACKEND_NOT_AUTH = {"NeedsLogin", "NeedsMachineAuth"}


def redact(text, extra_secrets=()):
    """Remove the connector token, Tailscale keys, bearer values and secret URL paths."""
    if text is None:
        return None
    s = str(text)
    for secret in [os.environ.get("SSH_CONNECTOR_TOKEN", ""), *extra_secrets]:
        if secret and len(secret) >= 8:
            s = s.replace(secret, TOKEN_PLACEHOLDER)
    s = _TSKEY_RE.sub("tskey-<REDACTED>", s)
    s = _BEARER_RE.sub(lambda m: m.group(1) + TOKEN_PLACEHOLDER, s)
    s = _SECRET_SEGMENT_RE.sub(TOKEN_PLACEHOLDER, s)
    return s


def safe_base(url):
    """Return (base, info): base is ``https://host[:port]`` only, or None if invalid.

    Path, query, fragment and user-info are dropped, so a token-bearing URL such as
    ``https://host/<secret>/mcp`` never leaves this function in full.
    """
    info = {"path_removed": False, "valid": False, "reason": None}
    if not url:
        info["reason"] = "not configured"
        return None, info
    try:
        p = urllib.parse.urlsplit(str(url).strip())
    except ValueError:
        info["reason"] = "not a URL"
        return None, info
    if p.scheme != "https":
        info["reason"] = "must start with https://"
        return None, info
    host = p.hostname or ""
    if p.username or p.password:
        info["path_removed"] = True
    if p.path not in ("", "/") or p.query or p.fragment:
        info["path_removed"] = True
    if not host or not _HOST_RE.match(host):
        info["reason"] = "invalid host"
        return None, info
    try:
        port = p.port
    except ValueError:
        info["reason"] = "invalid port"
        return None, info
    info["valid"] = True
    return "https://" + host.rstrip(".").lower() + (f":{port}" if port and port != 443 else ""), info


def configured_address(env=None, read_file=None):
    """The operator-configured address (trusted config), reduced to its safe base."""
    env = os.environ if env is None else env
    source, raw = None, ""
    if env.get("SSH_CONNECTOR_PUBLIC_URL"):
        source, raw = "SSH_CONNECTOR_PUBLIC_URL", env["SSH_CONNECTOR_PUBLIC_URL"]
    elif env.get("SSH_CONNECTOR_PUBLIC_URL_FILE"):
        source = "SSH_CONNECTOR_PUBLIC_URL_FILE"
        try:
            reader = read_file or (lambda p: open(p, encoding="utf-8").read())
            raw = reader(env["SSH_CONNECTOR_PUBLIC_URL_FILE"]).strip()
        except OSError:
            raw = ""
    base, info = safe_base(raw)
    if source and not raw:
        info["reason"] = "file not readable or empty"
    return {"value": base, "source": source, **info}


def classify_status(code, out, exc=None):
    """Interpret ``tailscale status --json``. Returns a dict with state + data."""
    if isinstance(exc, FileNotFoundError):
        return {"state": "not_installed", "detail": "the tailscale CLI is not installed on the connector host"}
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return {"state": "unknown", "detail": "tailscale status timed out"}
    if exc is not None:
        return {"state": "unknown", "detail": f"tailscale status failed: {type(exc).__name__}"}
    if code != 0:
        text = (out or "").lower()
        if "tailscaled" in text or "connect" in text or "not running" in text:
            return {"state": "daemon_not_running", "detail": "tailscaled is not running or not reachable"}
        return {"state": "unknown", "detail": f"tailscale status exited with code {code}"}
    try:
        j = json.loads(out or "")
        if not isinstance(j, dict):
            raise ValueError("not an object")
    except ValueError:
        return {"state": "unknown", "detail": "tailscale status returned invalid JSON"}
    backend = j.get("BackendState")
    me = j.get("Self") if isinstance(j.get("Self"), dict) else {}
    dns = me.get("DNSName") if isinstance(me.get("DNSName"), str) else ""
    dns = dns.rstrip(".").lower()
    if dns and not _HOST_RE.match(dns):
        dns = ""
    res = {"backend_state": backend, "dns_name": dns or None, "json": j}
    if backend in BACKEND_NOT_AUTH:
        return {**res, "state": "not_authenticated", "detail": f"device is not logged in ({backend})"}
    if backend not in BACKEND_STATES_OK:
        return {**res, "state": "not_running", "detail": f"tailscale backend state is {backend or 'unknown'}"}
    if not dns:
        return {**res, "state": "running", "detail": "running, but no MagicDNS name (DNSName missing)"}
    return {**res, "state": "running", "detail": "running"}


def classify_serve(code, out, exc=None, dns_name=None):
    """Interpret ``tailscale serve status --json``: funnel / serve / none / unknown."""
    if exc is not None or code != 0:
        why = "timed out" if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) else "not available"
        return {"mode": "unknown", "detail": f"tailscale serve status {why}", "hosts": []}
    try:
        j = json.loads(out or "")
        if not isinstance(j, dict):
            raise ValueError("not an object")
    except ValueError:
        return {"mode": "unknown", "detail": "tailscale serve status returned invalid JSON", "hosts": []}
    funnel = j.get("AllowFunnel") if isinstance(j.get("AllowFunnel"), dict) else {}
    web = j.get("Web") if isinstance(j.get("Web"), dict) else {}
    tcp = j.get("TCP") if isinstance(j.get("TCP"), dict) else {}
    funnel_hosts = sorted(h for h, on in funnel.items() if on)
    if funnel_hosts:
        return {"mode": "funnel", "detail": "Funnel is configured (public internet)", "hosts": funnel_hosts}
    if web or tcp:
        return {"mode": "serve", "detail": "Serve is configured (tailnet only, not public)",
                "hosts": sorted(web) or sorted(f"{dns_name or HOST_PLACEHOLDER}:{p}" for p in tcp)}
    return {"mode": "none", "detail": "neither Serve nor Funnel is configured", "hosts": []}


def _host_of(base):
    return urllib.parse.urlsplit(base).hostname if base else None


def mcp_endpoints(transport, base):
    """Safe endpoint templates. Never contains a real token."""
    if transport.get("mode") != "http" and not base:
        return {"plugin_stdio": "no URL: Claude Code / Codex start bin/run.sh locally over stdio",
                "remote_http": None}
    b = base or f"https://{HOST_PLACEHOLDER}"
    return {
        "plugin_stdio": "no URL: Claude Code / Codex start bin/run.sh locally over stdio",
        "remote_http": {
            "chatgpt_secret_path": f"{b}/{TOKEN_PLACEHOLDER}/mcp",
            "claude_bearer": {"url": f"{b}/mcp", "header": f"Authorization: Bearer {TOKEN_PLACEHOLDER}"},
            "note": "replace <TOKEN> yourself; the token is never shown by the connector",
        },
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # pragma: no cover - trivial
        return None


def http_probe(url, timeout=10):
    """GET without any credential. Returns the HTTP status code (0 on network error)."""
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                                         _NoRedirect())
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "ssh-connector-selfcheck"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


async def check_reachability(base, probe=None, timeout=10):
    """Ask the backend at ``base`` for /mcp without a token. 401 = backend answers and is protected."""
    if not base:
        return {"checked": False, "verified": False, "status": "not_checked", "detail": "no address to check"}
    probe = probe or http_probe
    try:
        code = await asyncio.wait_for(asyncio.to_thread(probe, base + "/mcp", timeout), timeout + 2)
    except (asyncio.TimeoutError, TimeoutError):
        code = 0
    if code == 401:
        return {"checked": True, "verified": True, "status": "ok", "http_status": 401,
                "detail": "backend answered and refused the request without a token (expected)",
                "checked_from": "connector host", "proves_internet_access": False}
    if code in (200, 202, 204, 400, 405, 406):
        return {"checked": True, "verified": False, "status": "unexpected", "http_status": code,
                "detail": "endpoint answered without credentials; check the token guard",
                "checked_from": "connector host", "proves_internet_access": False}
    return {"checked": True, "verified": False, "status": "failed", "http_status": code,
            "detail": "backend not reachable" if code == 0 else f"unexpected HTTP {code}",
            "checked_from": "connector host", "proves_internet_access": False}


async def collect(transport, cli, verify=False, env=None, probe=None, read_file=None):
    """Build the endpoint report. ``cli(*args)`` returns (code, text) like server._ts_cli."""
    async def run(*args):
        try:
            code, out = await cli(*args)
            return code, out, None
        except Exception as e:  # FileNotFoundError, TimeoutError, ...
            return None, "", e

    code, out, exc = await run("status", "--json")
    st = classify_status(code, out, exc)
    if st["state"] == "running":
        scode, sout, sexc = await run("serve", "status", "--json")
        serve = classify_serve(scode, sout, sexc, st.get("dns_name"))
    else:
        serve = {"mode": "unknown", "detail": "Tailscale is not running here", "hosts": []}

    cfg = configured_address(env, read_file)
    dns = st.get("dns_name")
    cfg_host = _host_of(cfg["value"])
    cfg["matches_tailscale_dns"] = (cfg_host == dns) if (cfg_host and dns) else None

    if serve["mode"] == "funnel":
        public = "configured_not_verified"
    elif serve["mode"] in ("serve", "none"):
        public = "no"
    else:
        public = "unknown"

    base = cfg["value"]
    if verify and base:
        reach = await check_reachability(base, probe)
    else:
        reach = {"checked": False, "verified": False, "status": "not_checked",
                 "detail": "a configured address is not proof of a working backend; "
                           "call tailscale_status(verify_reachability=true) to check"
                 if base else "no configured address"}

    lines = []
    if transport.get("mode") == "stdio":
        lines.append("This process is a local stdio plugin: it needs no public address.")
    else:
        lines.append(f"This process serves MCP over HTTP on {transport.get('host')}:{transport.get('port')}.")
    lines.append({"funnel": "Tailscale Funnel is configured (public), reachability "
                            + ("verified from the connector host." if reach["verified"] else "NOT verified."),
                  "serve": "Tailscale Serve is configured: reachable only inside the tailnet, not public.",
                  "none": "Tailscale Serve/Funnel is not configured.",
                  "unknown": "Serve/Funnel mode is unknown."}[serve["mode"]])
    if not base:
        lines.append("No address is configured (SSH_CONNECTOR_PUBLIC_URL / _FILE); no working link is claimed.")

    report = {
        "transport": transport,
        "tailscale": {"state": st["state"], "detail": st["detail"], "dns_name": dns,
                      "backend_state": st.get("backend_state")},
        "exposure": {"mode": serve["mode"], "detail": serve["detail"], "hosts": serve["hosts"],
                     "public_internet": public},
        "configured_address": cfg,
        "reachability": reach,
        "mcp_endpoints": mcp_endpoints(transport, base),
        "summary": " ".join(lines),
    }
    return json.loads(redact(json.dumps(report)))
