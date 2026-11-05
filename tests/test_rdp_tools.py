"""Offline tests for the read-only RDP / Windows tools (plugins/ssh-connector/rdp_tools.py).

A local fake RDP listener on 127.0.0.1 answers the X.224 negotiation and (optionally) TLS with a
throw-away certificate made by `openssl`. Windows commands are faked. No real host is contacted.
Офлайн тестове: подменен RDP сървър на 127.0.0.1 и подменени Windows команди.
"""
import asyncio
import base64
import json
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "ssh-connector"
sys.path.insert(0, str(PLUGIN))

import rdp_tools as rt  # noqa: E402


def neg_response(selected=None, failure=None):
    if failure is not None:
        neg = struct.pack("<BBHI", 0x03, 0, 8, failure)
    elif selected is not None:
        neg = struct.pack("<BBHI", 0x02, 0, 8, selected)
    else:
        neg = b""
    x224 = bytes([6 + len(neg), 0xD0, 0, 0, 0x12, 0x34, 0]) + neg
    return struct.pack(">BBH", 3, 0, 4 + len(x224)) + x224


class FakeRdp:
    """Minimal RDP negotiation peer. mode: 'nla_required' | 'nla_optional' | 'legacy_ok' | 'http' | 'silent'."""

    def __init__(self, mode="nla_required", tls_ctx=None):
        self.mode, self.tls_ctx, self.requests = mode, tls_ctx, []

    async def handle(self, reader, writer):
        try:
            if self.mode == "silent":
                await asyncio.sleep(5)
                return
            data = await reader.readexactly(19)
            requested = struct.unpack("<I", data[15:19])[0]
            self.requests.append(requested)
            if self.mode == "http":
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                return
            if requested == 0:
                writer.write(neg_response(selected=0) if self.mode == "legacy_ok" else neg_response(failure=1))
                await writer.drain()
                return
            if self.mode == "nla_required" and not requested & 2:
                writer.write(neg_response(failure=5))
                await writer.drain()
                return
            selected = 2 if requested & 2 else 1
            writer.write(neg_response(selected=selected))
            await writer.drain()
            if self.tls_ctx is not None:
                await writer.start_tls(self.tls_ctx)
                await asyncio.sleep(0.2)
        except Exception:
            pass
        finally:
            writer.close()


def make_cert(directory: Path, days=30):
    if not shutil.which("openssl"):
        return None
    key, crt = directory / "k.pem", directory / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
                    "-days", str(days), "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                   check=True, capture_output=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    return ctx, str(crt)


def config(port, **extra):
    return {"allowlist": rt.Allowlist(["127.0.0.1/32"]),
            "targets": {"win": {"host": "127.0.0.1", "port": port, "via": None, "server_name": "localhost",
                                "ca_file": None, "timeout": 1.0, "user": None, **extra}}}


async def with_server(fake, coro_factory):
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        return await coro_factory(port)
    finally:
        server.close()
        await server.wait_closed()


def check(fake, **extra):
    return json.loads(asyncio.run(with_server(fake, lambda p: rt.rdp_check("win", config=config(p, **extra)))))


class Negotiation(unittest.TestCase):
    def test_request_bytes(self):
        b = rt.build_connection_request(0x0B)
        self.assertEqual(len(b), 19)
        self.assertEqual(b[:4], b"\x03\x00\x00\x13")
        self.assertEqual(b[5], 0xE0)
        self.assertEqual(struct.unpack("<I", b[15:19])[0], 0x0B)

    def test_parse(self):
        self.assertEqual(rt.parse_connection_confirm(neg_response(selected=2))["selected"], 2)
        f = rt.parse_connection_confirm(neg_response(failure=5))
        self.assertEqual(f["failure_name"], "HYBRID_REQUIRED_BY_SERVER")
        self.assertEqual(rt.parse_connection_confirm(neg_response())["selected"], 0)
        for bad in (b"HTTP/1.1 200 OK\r\n", b"\x03\x00\x00\x0b\x06\xe0\x00\x00\x00\x00\x00", b"\x03\x00"):
            with self.assertRaises(ValueError):
                rt.parse_connection_confirm(bad)


class RdpCheck(unittest.TestCase):
    def test_nla_required_no_legacy(self):
        r = check(FakeRdp("nla_required"))
        self.assertEqual(r["status"], "UP")
        self.assertEqual(r["nla"], "required")
        self.assertFalse(r["legacy_rdp_security_allowed"])
        self.assertEqual(r["selected_protocol"], "credssp_nla")
        self.assertFalse(r["login_attempted"])
        # the fake peer has no TLS here, so only a TLS-handshake warning may appear
        self.assertEqual([w for w in r["warnings"] if not w.startswith("TLS")], [])
        self.assertIn("TLS handshake failed", r["tls"]["reason"])

    def test_nla_optional_and_legacy_warn(self):
        r = check(FakeRdp("legacy_ok"))
        self.assertEqual(r["nla"], "supported_not_required")
        self.assertTrue(r["legacy_rdp_security_allowed"])
        self.assertEqual(len([w for w in r["warnings"] if not w.startswith("TLS")]), 2)

    def test_not_rdp(self):
        r = check(FakeRdp("http"))
        self.assertEqual((r["status"], r["error_code"]), ("DOWN", "PROTOCOL_ERROR"))

    def test_timeout(self):
        r = check(FakeRdp("silent"))
        self.assertEqual((r["status"], r["error_code"]), ("DOWN", "TIMEOUT"))

    def test_closed_port(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        r = json.loads(asyncio.run(rt.rdp_check("win", config=config(port))))
        self.assertEqual((r["status"], r["error_code"]), ("DOWN", "UNREACHABLE"))

    def test_unknown_target_is_config_error(self):
        r = json.loads(asyncio.run(rt.rdp_check("192.0.2.1", config=config(3389))))
        self.assertEqual((r["status"], r["error_code"]), ("UNKNOWN", "CONFIG_ERROR"))

    def test_tls_untrusted_is_reported_never_bypassed(self):
        with tempfile.TemporaryDirectory() as d:
            made = make_cert(Path(d))
            if not made:
                self.skipTest("openssl not available")
            r = check(FakeRdp("nla_required", tls_ctx=made[0]))
        self.assertEqual(r["status"], "UP")
        self.assertFalse(r["tls"]["verified"])
        self.assertNotIn("days_left", r["tls"])                    # no data from an unverified handshake
        self.assertTrue(any("not verified" in w for w in r["warnings"]))

    def test_tls_verified_with_pinned_ca(self):
        with tempfile.TemporaryDirectory() as d:
            made = make_cert(Path(d), days=10)
            if not made:
                self.skipTest("openssl not available")
            r = check(FakeRdp("nla_required", tls_ctx=made[0]), ca_file=made[1])
        self.assertTrue(r["tls"]["verified"], r)
        self.assertIn("commonName=localhost", r["tls"]["subject"])
        self.assertLessEqual(r["tls"]["days_left"], 10)
        self.assertTrue(any("expires in" in w for w in r["warnings"]))

    def test_via_ssh_forwarder_used_and_closed(self):
        closed = []

        async def run(port):
            async def forwarder(via, host, rport):
                self.assertEqual((via, host, rport), ("jump", "10.0.0.5", 3389))
                return "127.0.0.1", port, lambda: closed.append(True)
            cfg = config(3389, via="jump")
            cfg["targets"]["win"]["host"] = "10.0.0.5"
            return await rt.rdp_check("win", forwarder=forwarder, config=cfg)
        r = json.loads(asyncio.run(with_server(FakeRdp("nla_required"), run)))
        self.assertEqual(r["status"], "UP")
        self.assertEqual(closed, [True])

    def test_audit(self):
        rt.AUDIT = []
        try:
            check(FakeRdp("nla_required"))
            self.assertEqual(rt.AUDIT[-1]["command"], "rdp:check win")
        finally:
            rt.AUDIT = None


class Config(unittest.TestCase):
    def load(self, section):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "servers.yaml"
            p.write_text(yaml.safe_dump({"servers": {}, "rdp": section}))
            return rt.load_rdp_config(str(p))

    def test_valid(self):
        c = self.load({"allowed_networks": ["192.0.2.0/24"], "targets": {"win1": {"host": "192.0.2.20", "via": "SRV1",
                                                                                  "user": "DOMAIN\\admin"}}})
        self.assertEqual(c["targets"]["win1"]["port"], 3389)
        self.assertEqual(c["targets"]["win1"]["via"], "SRV1")

    def test_rejected(self):
        bad = [None, {"targets": {"w": {"host": "192.0.2.20"}}},                       # no allowlist
               {"allowed_networks": ["192.0.2.0/24"], "targets": {}},
               {"allowed_networks": ["192.0.2.0/24"], "targets": {"w": {"host": "198.51.100.1"}}},   # outside
               {"allowed_networks": ["192.0.2.0/24"], "targets": {"w": {"host": "192.0.2.1", "port": 0}}},
               {"allowed_networks": ["192.0.2.0/24"], "targets": {"w": {"host": "192.0.2.1", "via": "a;b"}}},
               {"allowed_networks": ["192.0.2.0/24"], "targets": {"w": {"host": "192.0.2.1", "ca_file": "/nope"}}},
               {"allowed_networks": ["192.0.2.0/24"], "targets": {"w/x": {"host": "192.0.2.1"}}},
               {"allowed_networks": ["192.0.2.0/24"], "targets": {"w": {"host": "192.0.2.1", "user": "a b"}}}]
        for section in bad:
            with self.assertRaises(rt.RdpConfigError, msg=str(section)):
                self.load(section)

    def test_example_configs_valid(self):
        for p in (PLUGIN / "servers.example.yaml", PLUGIN.parents[1] / "servers.example.yaml"):
            raw = yaml.safe_load(p.read_text())
            raw["rdp"]["targets"] = {k: {**v, "ca_file": None} for k, v in raw["rdp"]["targets"].items()}
            with tempfile.TemporaryDirectory() as d:
                q = Path(d) / "s.yaml"
                q.write_text(yaml.safe_dump(raw))
                self.assertTrue(rt.load_rdp_config(str(q))["targets"], p)


class Tunnel(unittest.TestCase):
    def test_tunnel_and_rdp_file(self):
        calls = []

        async def open_forward(server, lport, host, rport):
            calls.append((server, lport, host, rport))
            return {"forward_id": "abcd1234", "local": "127.0.0.1:53389", "remote": f"{host}:{rport}"}
        cfg = config(3389, via="jump", user="DOMAIN\\admin")
        r = json.loads(asyncio.run(rt.rdp_tunnel("win", open_forward, {"mode": "stdio"}, 0, config=cfg)))
        self.assertEqual(calls, [("jump", 0, "127.0.0.1", 3389)])
        self.assertIn("full address:s:127.0.0.1:53389", r["rdp_file"])
        self.assertIn("username:s:DOMAIN\\admin", r["rdp_file"])
        self.assertNotIn("password", r["rdp_file"].lower())
        self.assertFalse(r["password_in_file"])
        self.assertIsNone(r["note"])
        self.assertEqual(r["stop_with"]["tool"], "stop_forward")

    def test_tunnel_remote_backend_note_and_errors(self):
        async def ok(*a):
            return {"forward_id": "x", "local": "127.0.0.1:5"}
        cfg = config(3389, via="jump")
        r = json.loads(asyncio.run(rt.rdp_tunnel("win", ok, {"mode": "http"}, config=cfg)))
        self.assertIn("not on your computer", r["note"])
        r = json.loads(asyncio.run(rt.rdp_tunnel("win", ok, None, config=config(3389))))       # no via
        self.assertEqual(r["error_code"], "CONFIG_ERROR")
        r = json.loads(asyncio.run(rt.rdp_tunnel("win", ok, None, local_port=80, config=cfg)))
        self.assertEqual(r["error_code"], "CONFIG_ERROR")

        async def fail(*a):
            return {"error": "ConnectionError: secret details"}
        r = json.loads(asyncio.run(rt.rdp_tunnel("win", fail, None, config=cfg)))
        self.assertEqual((r["status"], r["error_code"]), ("DOWN", "UNREACHABLE"))
        self.assertNotIn("secret", json.dumps(r))


HEALTH_JSON = {"os": "Microsoft Windows Server 2022 Standard", "version": "10.0.20348", "hostname": "WIN1",
               "last_boot": "2026-11-01T03:00:00.0000000Z", "rdp_enabled": True, "nla_required": False,
               "rdp_port": 3389, "security_layer": 2, "termservice": "Running", "firewall_rdp_rules_enabled": 3,
               "pending_reboot": True, "stopped_auto_services": "gupdate",
               "disks": [{"drive": "C:", "size_gb": 100.0, "free_gb": 5.0}, {"drive": "D:", "size_gb": 0, "free_gb": 0}]}


def fake_run(result):
    calls = []

    async def run(server, command, timeout):
        calls.append((server, command, timeout))
        return result
    return run, calls


class Windows(unittest.TestCase):
    def test_health_command_is_fixed_and_read_only(self):
        cmd = rt.health_command()
        self.assertTrue(cmd.startswith("powershell -NoProfile -NonInteractive -EncodedCommand "))
        script = base64.b64decode(cmd.split()[-1]).decode("utf-16-le")
        self.assertEqual(script, rt.HEALTH_PS)
        for verb in ("Set-", "New-", "Remove-", "Stop-", "Start-", "Restart-", "Enable-", "Disable-", "Invoke-"):
            self.assertNotIn(verb, script)

    def test_health_parsed_with_warnings(self):
        run, calls = fake_run({"exit_code": 0, "stdout": "noise\r\n" + json.dumps(HEALTH_JSON) + "\r\n"})
        r = json.loads(asyncio.run(rt.windows_health("WIN1", run)))
        self.assertEqual(calls[0][0], "WIN1")
        self.assertEqual(r["status"], "UP")
        self.assertEqual(r["stopped_auto_services"], ["gupdate"])
        w = " | ".join(r["warnings"])
        for part in ("disk C: below 10 %", "reboot pending", "1 automatic service", "RDP without NLA"):
            self.assertIn(part, w)

    def test_health_not_windows_and_errors(self):
        run, _ = fake_run({"exit_code": 127, "stdout": "", "stderr": "powershell: not found"})
        self.assertEqual(json.loads(asyncio.run(rt.windows_health("lin", run)))["error_code"], "UNAVAILABLE")
        run, _ = fake_run({"error": "timeout after 60s"})
        self.assertEqual(json.loads(asyncio.run(rt.windows_health("w", run)))["error_code"], "UNREACHABLE")
        run, _ = fake_run({"error": "unknown server", "unknown_server": True})
        self.assertEqual(json.loads(asyncio.run(rt.windows_health("nope", run)))["error_code"], "CONFIG_ERROR")
        self.assertEqual(json.loads(asyncio.run(rt.rdp_sessions("nope", run)))["error_code"], "CONFIG_ERROR")
        run, _ = fake_run({"exit_code": 0, "stdout": "garbage"})
        self.assertEqual(json.loads(asyncio.run(rt.windows_health("w", run)))["error_code"], "PROTOCOL_ERROR")

    def test_quser(self):
        out = (" USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\r\n"
               ">administrator         rdp-tcp#3           2  Active          .  11/4/2026 9:12 PM\r\n"
               " operator                                  3  Disc        1+02:3  11/3/2026 8:00 AM\r\n")
        rows = rt.parse_quser(out)
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["user"], rows[0]["session"], rows[0]["id"], rows[0]["current"]),
                         ("administrator", "rdp-tcp#3", 2, True))
        self.assertEqual((rows[1]["user"], rows[1]["session"], rows[1]["state"]), ("operator", None, "Disc"))
        run, calls = fake_run({"exit_code": 0, "stdout": out})
        r = json.loads(asyncio.run(rt.rdp_sessions("WIN1", run)))
        self.assertEqual((r["count"], calls[0][1]), (2, "quser"))
        run, _ = fake_run({"exit_code": 1, "stdout": "", "stderr": "No User exists for *"})
        self.assertEqual(json.loads(asyncio.run(rt.rdp_sessions("WIN1", run)))["count"], 0)
        run, _ = fake_run({"exit_code": 127, "stdout": "", "stderr": "quser: not found"})
        self.assertEqual(json.loads(asyncio.run(rt.rdp_sessions("lin", run)))["error_code"], "UNAVAILABLE")


class ReadOnlySource(unittest.TestCase):
    def test_no_login_or_write_paths(self):
        src = (PLUGIN / "rdp_tools.py").read_text()
        for word in ("CERT_NONE", "check_hostname = False", "check_hostname=False", "password=", "sendall(cred"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
