"""Mock tests (no network) for session_configure and the Tailscale commands.
Макетни тестове: session_configure и командите за Tailscale — без реален Tailscale и без мрежа."""
import asyncio
import json
import os
import pathlib
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "ssh-connector"))

STATUS = {"BackendState": "Running", "CurrentTailnet": {"Name": "example.github"},
          "Self": {"HostName": "ssh-connector", "DNSName": "ssh-connector.tail0000.ts.net.", "UserID": 7,
                   "TailscaleIPs": ["100.64.0.9"], "Online": True, "OS": "linux"},
          "User": {"7": {"LoginName": "owner@example.com"}},
          "Peer": {"a": {"HostName": "srv1", "DNSName": "srv1.tail0000.ts.net.", "TailscaleIPs": ["100.64.0.82"],
                         "Online": True, "OS": "linux", "LastSeen": "2026-10-27T00:00:00Z"},
                   "b": {"HostName": "old-phone", "TailscaleIPs": ["100.64.0.20"], "Online": False, "OS": "iOS"}}}


async def main():
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "servers.yaml"
        path.write_text(yaml.safe_dump({"defaults": {"connect_timeout": 8},
                                        "servers": {"SRV1": {"hosts": ["100.64.0.82"], "user": "admin"}}}))
        os.environ["SSH_CONNECTOR_CONFIG"] = str(path)
        import server

        # ---- session_configure ----
        shown = json.loads(await server.session_configure())
        assert shown["SRV1"]["connect_timeout"] == 8, shown
        assert "error" in json.loads(await server.session_configure(retries=99))           # извън границите
        r = json.loads(await server.session_configure("SRV1", command_timeout=300))
        assert r["scope"] == "SRV1" and yaml.safe_load(path.read_text())["servers"]["SRV1"]["command_timeout"] == 300
        r = json.loads(await server.session_configure(connect_timeout=15))
        assert yaml.safe_load(path.read_text())["defaults"]["connect_timeout"] == 15, r
        assert server.srv("SRV1").cfg["connect_timeout"] == 15                              # приложено веднага

        # ---- tailscale_status / devices с подменен CLI ----
        async def fake_cli(*args):
            if args[0] == "version":
                return 0, "1.104.2\n  tailscale commit: x\n"
            if args[0] == "status":
                return 0, json.dumps(STATUS)
            if args[:2] == ("serve", "status"):
                return 0, json.dumps({"Web": {"ssh-connector.tail0000.ts.net:443": {}},
                                      "AllowFunnel": {"ssh-connector.tail0000.ts.net:443": True}})
            raise AssertionError(f"unexpected tailscale call {args}")          # само четене
        server._ts_cli = fake_cli
        st = json.loads(await server.tailscale_status())
        assert st["version"] == "1.104.2" and st["account"] == "owner@example.com" and st["funnel_on"], st
        ep = st["endpoint"]
        assert ep["exposure"]["mode"] == "funnel" and ep["exposure"]["public_internet"] == "configured_not_verified", ep
        assert ep["transport"]["mode"] == "stdio" and not ep["reachability"]["verified"], ep
        devs = json.loads(await server.tailscale_devices())
        assert [d["name"] for d in devs][:1] == ["srv1"] and len(devs) == 3, devs
        assert len(json.loads(await server.tailscale_devices(online_only=True))) == 2

        # ---- ключове с подменен API ----
        os.environ.pop("TAILSCALE_API_KEY", None)
        assert "TAILSCALE_API_KEY" in json.loads(await server.tailscale_keys())["error"]
        calls = []

        async def fake_api(method, p, body=None):
            calls.append((method, p, body))
            if method == "POST":
                return {"id": "kTEST123", "key": "tskey-auth-SECRETVALUE", "expires": "2027-01-01T00:00:00Z"}
            if method == "GET":
                return {"keys": [{"id": "kTEST123", "created": "x", "expires": "y",
                                  "capabilities": {"devices": {"create": {"reusable": True, "ephemeral": True}}}}]}
            return {}
        server._ts_api = fake_api
        server.KEY_DIR = pathlib.Path(directory) / "keys"

        keys = json.loads(await server.tailscale_keys())
        assert keys[0]["id"] == "kTEST123" and keys[0]["ephemeral"] and "key" not in keys[0], keys

        plan = json.loads(await server.tailscale_key_create())
        assert plan["confirmation_required"] and not calls[-1][0] == "POST"                 # без потвърждение — нищо
        made = json.loads(await server.tailscale_key_create(confirm=True))
        assert made["created"] and not made["key_shown"], made
        assert "SECRETVALUE" not in json.dumps(made)                                        # стойността не се показва
        saved = pathlib.Path(made["saved_to_file"])
        assert saved.read_text().strip() == "tskey-auth-SECRETVALUE"
        assert oct(saved.stat().st_mode & 0o777) == "0o600"
        assert "error" in json.loads(await server.tailscale_key_create(expiry_days=365, confirm=True))

        assert json.loads(await server.tailscale_key_revoke("kTEST123"))["confirmation_required"]
        assert calls[-1][0] != "DELETE"
        assert json.loads(await server.tailscale_key_revoke("kTEST123", confirm=True)) == {"revoked": "kTEST123"}
        assert calls[-1] == ("DELETE", "/keys/kTEST123", None)
        assert "error" in json.loads(await server.tailscale_key_revoke("../etc", confirm=True))  # невалидно id
    print("v1.3 session + tailscale mock tests PASSED")


if __name__ == "__main__":
    asyncio.run(main())
