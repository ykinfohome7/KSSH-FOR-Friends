"""Isolated installed-plugin test (offline, no devices, no Tailscale changes).

Copies ONLY plugins/ssh-connector to a temporary "plugin cache" (as Claude Code / Codex install it),
then talks real MCP over stdio to it:

  Run A - the real launcher ``sh bin/run.sh`` (venv pre-seeded so no pip/network is needed),
          cwd outside the repository, no PYTHONPATH: initialize, list_tools, unique names, measured
          count, old SSH tools, snmp_* without pysnmp (optional backend missing), tailscale_status with a
          fake ``tailscale`` CLI that records every call.
  Run B - a harness file (outside the repo and outside the plugin) that imports the installed copy,
          plugs in a fake SNMP backend and runs the same MCP stdio server: call_tool works end to end,
          and import/initialize/list_tools create no SNMP backend and run no tailscale command.

Requires the plugin's own dependencies (mcp<2, asyncssh, PyYAML) in the running interpreter, as in CI.
Изолиран тест на инсталирания плъгин: само неговата папка, без repository root и без PYTHONPATH.
"""
import asyncio
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "ssh-connector"
EXPECTED_TOOLS = 60
SNMP_TOOLS = {"snmp_health", "snmp_device_info", "snmp_get", "snmp_getnext", "snmp_getbulk", "snmp_walk",
              "snmp_interfaces", "snmp_interface_stats"}
RDP_TOOLS = {"rdp_check", "rdp_tunnel", "windows_health", "rdp_sessions"}
OLD_TOOLS = {"version_info", "list_servers", "server_test", "run_command", "run_on_many", "shell_open", "sftp_list",
             "port_forward", "start_watch", "tailscale_status", "tailscale_devices", "sudo_password",
             "codespace_status", "health_status", "help"}
MUTATING_TS = {"up", "login", "logout", "funnel", "set", "down", "cert"}

FAKE_TAILSCALE = r'''#!{python}
import json, sys
args = sys.argv[1:]
with open({log!r}, "a") as f:
    f.write(json.dumps(args) + "\n")
dns = "ssh-connector.tail0000.ts.net"
if args[:1] == ["version"]:
    print("1.104.2")
elif args[:2] == ["status", "--json"]:
    print(json.dumps({{"BackendState": "Running", "Self": {{"HostName": "ssh-connector", "DNSName": dns + ".",
                     "TailscaleIPs": ["100.64.0.9"], "Online": True, "UserID": 1}}, "User": {{}}}}))
elif args[:3] == ["serve", "status", "--json"]:
    print(json.dumps({{"Web": {{dns + ":443": {{}}}}}}))      # Serve on, Funnel off
else:
    print("refused by fake tailscale", file=sys.stderr)
    sys.exit(3)
'''

HARNESS = r'''
import importlib.util, json, sys
sys.path.insert(0, sys.argv[1])                      # the installed plugin copy only
assert importlib.util.find_spec("monitoring") is None, "repository package reachable"
import server, snmp_tools
from kssh_snmp.snmp_client import END_OF_MIB, NO_SUCH, parse_oid
assert snmp_tools.__file__.startswith(sys.argv[1]), snmp_tools.__file__
MIB = {"1.3.6.1.2.1.1.1.0": "Fake SW 10G", "1.3.6.1.2.1.1.3.0": 4242, "1.3.6.1.2.1.1.5.0": "sw-test"}

class Agent:
    def _next(self, oid):
        for o in sorted(MIB, key=parse_oid):
            if parse_oid(o) > parse_oid(oid):
                return o, MIB[o]
        return oid, END_OF_MIB
    async def get(self, oids):
        return [(o, MIB.get(o, NO_SUCH)) for o in oids]
    async def getnext(self, oids):
        return [self._next(o) for o in oids]
    async def getbulk(self, n, m, oids):
        return [self._next(oids[0])]

def factory(name, cfg):
    with open(sys.argv[2], "a") as f:
        f.write(name + "\n")
    return Agent()

snmp_tools.BACKEND_FACTORY = factory
server.mcp.run()
'''

CONFIG = """servers: {{}}
snmp:
  allowed_networks: [192.0.2.10/32]
  devices:
    sw-test:
      host: 192.0.2.10
      adapter: generic
      version: "3"
      user: monitor
      security_level: authPriv
      auth_env: KSSH_TEST_AUTH
      priv_env: KSSH_TEST_PRIV
rdp:
  allowed_networks: [127.0.0.1/32]
  targets:
    closed:
      host: 127.0.0.1
      port: {closed_port}
      timeout: 1
"""


def _text(res):
    return res.content[0].text if res.content else ""


async def _session(params, body):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await asyncio.wait_for(s.initialize(), 120)
            return await body(s, init)


def _lines(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines()] if Path(path).exists() else []


async def main():
    from mcp import StdioServerParameters
    with tempfile.TemporaryDirectory(prefix="kssh-installed-") as d:
        d = Path(d)
        installed = d / "plugins" / "cache" / "kssh-for-friends" / "ssh-connector"
        shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "servers.yaml"))
        assert not (installed / ".." / ".." / ".." / "monitoring").exists()
        data = d / "plugin-data"
        (data / "venv" / "bin").mkdir(parents=True)
        # Pre-seeded "venv" (no pip). A wrapper, not a symlink: a symlink to a venv interpreter does not
        # activate that venv (Python looks for pyvenv.cfg next to the path it was started from), so the
        # child would fall back to the system Python without the venv's packages.
        seeded = data / "venv" / "bin" / "python"
        seeded.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n")
        seeded.chmod(0o755)
        shutil.copy(installed / "requirements.txt", data / "requirements.installed")
        home = d / "home"
        (home / ".ssh-connector").mkdir(parents=True)
        cfg = home / ".ssh-connector" / "servers.yaml"
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
        sock.close()                                                            # nothing listens there
        cfg.write_text(CONFIG.format(closed_port=closed_port))
        bindir = d / "bin"
        bindir.mkdir()
        ts_log = d / "tailscale-calls.log"
        fake = bindir / "tailscale"
        fake.write_text(FAKE_TAILSCALE.format(python=sys.executable, log=str(ts_log)))
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env = {"PATH": f"{bindir}:/usr/local/bin:/usr/bin:/bin", "HOME": str(home), "CLAUDE_PLUGIN_DATA": str(data),
               "KSSH_TEST_AUTH": "auth-secret-123", "KSSH_TEST_PRIV": "priv-secret-456", "LANG": "C.UTF-8"}
        if os.environ.get("KSSH_TEST_STUBS"):                                  # local runs without asyncssh only
            env["PYTHONPATH"] = os.environ["KSSH_TEST_STUBS"]
        report = {}

        # ---------------- Run A: real launcher ----------------
        async def run_a(s, init):
            info = getattr(init, "serverInfo", None) or getattr(init, "server_info", None)   # mcp 1.x / 2.x
            assert info.name == "ssh-connector", info
            assert not _lines(ts_log), "initialize ran tailscale"
            tools = (await s.list_tools()).tools
            names = [t.name for t in tools]
            assert not _lines(ts_log), "list_tools ran tailscale"
            assert len(names) == len(set(names)), "duplicate tool names"
            assert len(names) == EXPECTED_TOOLS, (len(names), sorted(names))
            assert SNMP_TOOLS | RDP_TOOLS | OLD_TOOLS <= set(names), sorted((SNMP_TOOLS | RDP_TOOLS | OLD_TOOLS) - set(names))
            ver = json.loads(_text(await s.call_tool("version_info", {})))
            assert len(ver["commands"]) == EXPECTED_TOOLS and set(ver["commands"]) == set(names), ver
            assert json.loads(_text(await s.call_tool("list_servers", {}))) is not None
            helped = json.loads(_text(await s.call_tool("help", {"command": "snmp_walk"})))
            assert helped["bg"] and helped["en"], helped
            health = json.loads(_text(await s.call_tool("health_status", {})))
            assert health["transport"]["mode"] == "stdio", health
            snmp = json.loads(_text(await s.call_tool("snmp_health", {"device": "sw-test"})))
            rdp = json.loads(_text(await s.call_tool("rdp_check", {"target": "closed"})))
            assert (rdp["status"], rdp["error_code"]) == ("DOWN", "UNREACHABLE"), rdp
            rdp = json.loads(_text(await s.call_tool("rdp_check", {"target": "10.0.0.1"})))
            assert (rdp["status"], rdp["error_code"]) == ("UNKNOWN", "CONFIG_ERROR"), rdp
            win = json.loads(_text(await s.call_tool("windows_health", {"server": "nope"})))
            assert win["error_code"] == "CONFIG_ERROR", win
            ts_before = list(_lines(ts_log))
            ts = json.loads(_text(await s.call_tool("tailscale_status", {})))
            calls = _lines(ts_log)
            assert not ts_before, "tailscale ran before tailscale_status"
            assert calls and not any(c and c[0] in MUTATING_TS for c in calls), calls
            assert all(c in (["version"], ["status", "--json"], ["serve", "status", "--json"]) for c in calls), calls
            ep = ts["endpoint"]
            assert ep["transport"]["mode"] == "stdio" and ep["exposure"]["mode"] == "serve", ep
            assert ep["exposure"]["public_internet"] == "no" and not ep["reachability"]["verified"], ep
            assert "secret" not in json.dumps(snmp) and "secret" not in json.dumps(ts)
            return {"tools": len(names), "snmp_health_without_backend": [snmp.get("status"), snmp.get("error_code")],
                    "tailscale_calls": calls, "exposure": ep["exposure"]["mode"]}

        params_a = StdioServerParameters(command="sh", args=[str(installed / "bin" / "run.sh")], env=env, cwd=str(d))
        report["launcher"] = await _session(params_a, run_a)
        pysnmp_present = report["launcher"]["snmp_health_without_backend"][1] != "DEPENDENCY_MISSING"
        if not pysnmp_present:
            assert report["launcher"]["snmp_health_without_backend"] == ["UNKNOWN", "DEPENDENCY_MISSING"], report

        # ---------------- Run B: fake SNMP backend through the installed copy ----------------
        harness = d / "harness" / "run_with_fake_snmp.py"
        harness.parent.mkdir()
        harness.write_text(HARNESS)
        made = d / "backend-created.log"
        ts_log.unlink(missing_ok=True)
        env_b = dict(env, SSH_CONNECTOR_CONFIG=str(cfg))

        async def run_b(s, init):
            names = [t.name for t in (await s.list_tools()).tools]
            assert not made.exists(), "SNMP backend created during import/initialize/list_tools"
            assert not _lines(ts_log), "tailscale ran during import/initialize/list_tools"
            h = json.loads(_text(await s.call_tool("snmp_health", {"device": "sw-test"})))
            g = json.loads(_text(await s.call_tool("snmp_get", {"device": "sw-test", "oids": ["1.3.6.1.2.1.1.5.0"]})))
            w = json.loads(_text(await s.call_tool("snmp_walk", {"device": "sw-test", "oid": "1.3.6.1.2.1.1"})))
            ip = json.loads(_text(await s.call_tool("snmp_get", {"device": "192.0.2.10", "oids": ["1.3.6.1.2.1.1.5.0"]})))
            assert h["status"] == "UP", h
            assert json.dumps(g).count("sw-test") >= 1 and g.get("status") == "UP", g
            assert w.get("status") == "UP", w
            assert ip.get("status") == "UNKNOWN", ip                           # raw IP refused
            assert "auth-secret-123" not in json.dumps([h, g, w, ip])
            return {"tools": len(names), "snmp_health": h["status"], "snmp_get": g["status"],
                    "snmp_walk": w["status"], "raw_ip_refused": ip.get("error_code"),
                    "backend_created_after_call": made.exists()}

        params_b = StdioServerParameters(command=str(data / "venv" / "bin" / "python"),
                                         args=["-I", str(harness), str(installed), str(made)] if not env.get("PYTHONPATH")
                                         else [str(harness), str(installed), str(made)],
                                         env=env_b, cwd=str(d))
        report["fake_backend"] = await _session(params_b, run_b)
        assert report["fake_backend"]["backend_created_after_call"], report
    print("installed plugin test PASSED " + json.dumps(report, sort_keys=True))
    return report


if __name__ == "__main__":
    asyncio.run(main())
