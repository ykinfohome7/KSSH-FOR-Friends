"""Offline tests for the connector endpoint / Tailscale report (plugins/ssh-connector/endpoint_info.py).

Mock CLI and HTTP only: no real tailscale, no network, nothing is enabled.
Офлайн тестове: подменен tailscale CLI и HTTP проверка; нищо не се стартира и не се включва.
"""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "ssh-connector"
sys.path.insert(0, str(PLUGIN))

import endpoint_info as ei  # noqa: E402

DNS = "ssh-connector.tail0000.ts.net"
TOKEN = "Zq9mX2vL7pR4tY8wB1nC6kD3fG5hJ0sA"          # fake token, test only
STDIO = {"mode": "stdio"}
HTTP = {"mode": "http", "host": "127.0.0.1", "port": 8765}


def status(backend="Running", dns=DNS + ".", host="ssh-connector"):
    me = {"HostName": host, "TailscaleIPs": ["100.64.0.9"], "Online": True}
    if dns is not None:
        me["DNSName"] = dns
    return json.dumps({"BackendState": backend, "Self": me})


SERVE_ONLY = json.dumps({"TCP": {"443": {"HTTPS": True}},
                         "Web": {f"{DNS}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}}})
FUNNEL = json.dumps({"TCP": {"443": {"HTTPS": True}},
                     "Web": {f"{DNS}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}},
                     "AllowFunnel": {f"{DNS}:443": True}})


class Cli:
    """Fake `tailscale` CLI. Records every call."""

    def __init__(self, status_result=(0, status()), serve_result=(0, "{}")):
        self.status_result, self.serve_result, self.calls = status_result, serve_result, []

    async def __call__(self, *args):
        self.calls.append(args)
        res = self.status_result if args[:1] == ("status",) else self.serve_result
        if isinstance(res, BaseException):
            raise res
        return res


def collect(cli, transport=STDIO, env=None, verify=False, probe=None):
    return asyncio.run(ei.collect(transport, cli, verify=verify, env=env or {}, probe=probe))


class TailscaleScenarios(unittest.TestCase):
    def test_not_installed(self):
        r = collect(Cli(status_result=FileNotFoundError("tailscale")))
        self.assertEqual(r["tailscale"]["state"], "not_installed")
        self.assertEqual(r["exposure"]["mode"], "unknown")
        self.assertEqual(r["exposure"]["public_internet"], "unknown")

    def test_daemon_not_running(self):
        r = collect(Cli(status_result=(1, "failed to connect to local tailscaled; it doesn't appear to be running")))
        self.assertEqual(r["tailscale"]["state"], "daemon_not_running")
        self.assertEqual(r["exposure"]["mode"], "unknown")

    def test_not_authenticated(self):
        for backend in ("NeedsLogin", "NeedsMachineAuth"):
            r = collect(Cli(status_result=(0, status(backend=backend))))
            self.assertEqual(r["tailscale"]["state"], "not_authenticated", backend)
            self.assertEqual(r["exposure"]["mode"], "unknown")

    def test_stopped_backend(self):
        r = collect(Cli(status_result=(0, status(backend="Stopped"))))
        self.assertEqual(r["tailscale"]["state"], "not_running")

    def test_valid_dns_name(self):
        r = collect(Cli())
        self.assertEqual(r["tailscale"]["state"], "running")
        self.assertEqual(r["tailscale"]["dns_name"], DNS)         # trailing dot removed

    def test_missing_dns_name_is_not_guessed_from_hostname(self):
        r = collect(Cli(status_result=(0, status(dns=None, host="my-device"))), transport=HTTP)
        self.assertIsNone(r["tailscale"]["dns_name"])
        self.assertIn("DNSName missing", r["tailscale"]["detail"])
        self.assertNotIn("my-device", json.dumps(r))              # never derived from the device name
        self.assertIn("https://<host>/<TOKEN>/mcp", json.dumps(r))

    def test_invalid_dns_name_rejected(self):
        r = collect(Cli(status_result=(0, status(dns="bad host/x"))))
        self.assertIsNone(r["tailscale"]["dns_name"])

    def test_invalid_json(self):
        r = collect(Cli(status_result=(0, "{not json")))
        self.assertEqual(r["tailscale"]["state"], "unknown")
        self.assertIn("invalid JSON", r["tailscale"]["detail"])
        r = collect(Cli(serve_result=(0, "[1,2]")))
        self.assertEqual(r["exposure"]["mode"], "unknown")

    def test_timeout(self):
        r = collect(Cli(status_result=asyncio.TimeoutError()))
        self.assertEqual(r["tailscale"]["state"], "unknown")
        self.assertIn("timed out", r["tailscale"]["detail"])
        r = collect(Cli(serve_result=asyncio.TimeoutError()))
        self.assertEqual(r["exposure"]["mode"], "unknown")
        self.assertIn("timed out", r["exposure"]["detail"])

    def test_serve_active_funnel_not(self):
        r = collect(Cli(serve_result=(0, SERVE_ONLY)), transport=HTTP)
        self.assertEqual(r["exposure"]["mode"], "serve")
        self.assertEqual(r["exposure"]["public_internet"], "no")
        self.assertIn("only inside the tailnet, not public", r["summary"])
        self.assertIn("not public", r["exposure"]["detail"])

    def test_funnel_active_but_not_verified(self):
        r = collect(Cli(serve_result=(0, FUNNEL)), transport=HTTP)
        self.assertEqual(r["exposure"]["mode"], "funnel")
        self.assertEqual(r["exposure"]["public_internet"], "configured_not_verified")
        self.assertFalse(r["reachability"]["verified"])
        self.assertIn("NOT verified", r["summary"])

    def test_nothing_configured(self):
        r = collect(Cli(serve_result=(0, "{}")))
        self.assertEqual(r["exposure"]["mode"], "none")
        self.assertIsNone(r["configured_address"]["value"])
        self.assertIn("no working link is claimed", r["summary"])

    def test_configured_address_without_verified_reachability(self):
        probe_calls = []
        r = collect(Cli(serve_result=(0, FUNNEL)), transport=HTTP,
                    env={"SSH_CONNECTOR_PUBLIC_URL": f"https://{DNS}"}, probe=lambda *a: probe_calls.append(a))
        self.assertEqual(r["configured_address"]["value"], f"https://{DNS}")
        self.assertTrue(r["configured_address"]["matches_tailscale_dns"])
        self.assertEqual(r["reachability"]["status"], "not_checked")
        self.assertFalse(r["reachability"]["verified"])
        self.assertEqual(probe_calls, [])                         # no network without verify=true

    def test_backend_health_check_fails(self):
        env = {"SSH_CONNECTOR_PUBLIC_URL": f"https://{DNS}"}
        for code in (0, 502, 404):
            r = collect(Cli(serve_result=(0, FUNNEL)), transport=HTTP, env=env, verify=True,
                        probe=lambda url, timeout, c=code: c)
            self.assertEqual(r["reachability"]["status"], "failed", code)
            self.assertFalse(r["reachability"]["verified"])
            self.assertIn("NOT verified", r["summary"])

    def test_backend_health_check_ok_and_unprotected(self):
        env = {"SSH_CONNECTOR_PUBLIC_URL": f"https://{DNS}"}
        seen = []
        r = collect(Cli(serve_result=(0, FUNNEL)), transport=HTTP, env=env, verify=True,
                    probe=lambda url, timeout: seen.append(url) or 401)
        self.assertTrue(r["reachability"]["verified"])
        self.assertFalse(r["reachability"]["proves_internet_access"])
        self.assertEqual(seen, [f"https://{DNS}/mcp"])           # only the configured host, no token
        r = collect(Cli(serve_result=(0, FUNNEL)), transport=HTTP, env=env, verify=True,
                    probe=lambda url, timeout: 200)
        self.assertEqual(r["reachability"]["status"], "unexpected")
        self.assertFalse(r["reachability"]["verified"])

    def test_health_check_timeout(self):
        import time

        async def run():                                          # probe hangs longer than timeout + 2 s
            return await ei.check_reachability(f"https://{DNS}", probe=lambda u, t: time.sleep(2.3) or 401,
                                               timeout=0.05)
        r = asyncio.run(run())
        self.assertEqual(r["status"], "failed")
        self.assertFalse(r["verified"])

    def test_configured_address_from_file(self):
        env = {"SSH_CONNECTOR_PUBLIC_URL_FILE": "/x/public-url"}
        r = asyncio.run(ei.collect(STDIO, Cli(), env=env, read_file=lambda p: f"https://{DNS}/\n"))
        self.assertEqual(r["configured_address"]["source"], "SSH_CONNECTOR_PUBLIC_URL_FILE")
        self.assertEqual(r["configured_address"]["value"], f"https://{DNS}")

        def missing(p):
            raise OSError("nope")
        r = asyncio.run(ei.collect(STDIO, Cli(), env=env, read_file=missing))
        self.assertIsNone(r["configured_address"]["value"])
        self.assertIn("not readable", r["configured_address"]["reason"])

    def test_configured_address_must_be_https(self):
        for bad in ("http://example.org", "ftp://x", "https://", "https://bad_host!/", "not a url"):
            r = collect(Cli(), env={"SSH_CONNECTOR_PUBLIC_URL": bad})
            self.assertIsNone(r["configured_address"]["value"], bad)
            self.assertFalse(r["configured_address"]["valid"], bad)

    def test_mismatch_between_configured_and_tailscale(self):
        r = collect(Cli(), env={"SSH_CONNECTOR_PUBLIC_URL": "https://other.example.org"})
        self.assertFalse(r["configured_address"]["matches_tailscale_dns"])


class Transports(unittest.TestCase):
    def test_stdio_needs_no_address(self):
        r = collect(Cli())
        self.assertEqual(r["transport"]["mode"], "stdio")
        self.assertIsNone(r["mcp_endpoints"]["remote_http"])
        self.assertIn("needs no public address", r["summary"])

    def test_http_templates_never_contain_a_token(self):
        with patch.dict(os.environ, {"SSH_CONNECTOR_TOKEN": TOKEN}):
            r = collect(Cli(serve_result=(0, SERVE_ONLY)), transport=HTTP,
                        env={"SSH_CONNECTOR_PUBLIC_URL": f"https://{DNS}"})
        ep = r["mcp_endpoints"]["remote_http"]
        self.assertEqual(ep["chatgpt_secret_path"], f"https://{DNS}/<TOKEN>/mcp")
        self.assertEqual(ep["claude_bearer"], {"url": f"https://{DNS}/mcp", "header": "Authorization: Bearer <TOKEN>"})
        self.assertNotIn(TOKEN, json.dumps(r))


class Redaction(unittest.TestCase):
    def test_token_bearing_configured_url_is_reduced_to_host(self):
        url = f"https://{DNS}/{TOKEN}/mcp?x=1"
        r = collect(Cli(), transport=HTTP, env={"SSH_CONNECTOR_PUBLIC_URL": url})
        self.assertEqual(r["configured_address"]["value"], f"https://{DNS}")
        self.assertTrue(r["configured_address"]["path_removed"])
        self.assertNotIn(TOKEN, json.dumps(r))

    def test_userinfo_removed(self):
        base, info = ei.safe_base("https://user:pass@example.org:8443/a")
        self.assertEqual(base, "https://example.org:8443")
        self.assertTrue(info["path_removed"])

    def test_redact_function(self):
        with patch.dict(os.environ, {"SSH_CONNECTOR_TOKEN": TOKEN}):
            text = (f"url https://h.ts.net/{TOKEN}/mcp token={TOKEN} key tskey-auth-kAbC123-SECRETxyz "
                    "Authorization: Bearer abc.def-ghi https://h/AbCdEfGhIjKlMnOpQrStUv/sse")
            out = ei.redact(text)
        for secret in (TOKEN, "SECRETxyz", "abc.def-ghi", "AbCdEfGhIjKlMnOpQrStUv"):
            self.assertNotIn(secret, out)
        self.assertIn("https://h.ts.net/<TOKEN>/mcp", out)

    def test_cli_error_text_redacted(self):
        with patch.dict(os.environ, {"SSH_CONNECTOR_TOKEN": TOKEN}):
            r = collect(Cli(status_result=(0, status(backend="NeedsLogin" + TOKEN))))
        self.assertNotIn(TOKEN, json.dumps(r))


class ReadOnly(unittest.TestCase):
    def test_only_read_only_cli_commands(self):
        for serve in ((0, "{}"), (0, SERVE_ONLY), (0, FUNNEL)):
            cli = Cli(serve_result=serve)
            collect(cli, transport=HTTP, env={"SSH_CONNECTOR_PUBLIC_URL": f"https://{DNS}"})
            self.assertEqual(cli.calls, [("status", "--json"), ("serve", "status", "--json")])

    def test_no_serve_call_when_not_running(self):
        cli = Cli(status_result=(0, status(backend="NeedsLogin")))
        collect(cli)
        self.assertEqual(cli.calls, [("status", "--json")])

    def test_source_has_no_mutating_tailscale_command(self):
        src = (PLUGIN / "endpoint_info.py").read_text()
        for word in ('"up"', '"login"', '"logout"', '"--bg"', '"reset"', '"set"', "funnel --", "serve --"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
