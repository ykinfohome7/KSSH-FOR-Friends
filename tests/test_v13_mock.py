"""Mock tests for v1.3 commands (no network): server_get, server_rename, server_set_primary,
server_failover, health_status, version_info, help.
Макетни тестове за командите на v1.3 — без реална мрежа, SSH връзката е подменена."""
import asyncio
import json
import os
import pathlib
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "ssh-connector"))


async def main():
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "servers.yaml"
        path.write_text(yaml.safe_dump({
            "defaults": {"connect_timeout": 1, "retries": 1},
            "servers": {
                "SRV1": {"hosts": ["100.64.0.82", "srv1.example.com"], "user": "admin",
                         "password_env": "SRV1_SSH_PASSWORD", "aliases": ["bg"]},
                "web1": {"hosts": ["10.0.0.5"], "user": "root"},
            },
        }))
        os.environ["SSH_CONNECTOR_CONFIG"] = str(path)
        import server

        async def no_network(*args, **kwargs):  # подменяме реалната SSH връзка (mock)
            raise OSError("mock: no network")
        server.asyncssh.connect = no_network

        g = json.loads(await server.server_get("bg"))
        assert g["name"] == "SRV1" and g["primary"] == "100.64.0.82", g
        assert "password" not in g and g["password_env"] == "SRV1_SSH_PASSWORD", g  # без тайни

        r = json.loads(await server.server_set_primary("SRV1", "srv1.example.com"))
        assert r["primary"] == "srv1.example.com", r
        assert yaml.safe_load(path.read_text())["servers"]["SRV1"]["hosts"][0] == "srv1.example.com"
        assert "error" in json.loads(await server.server_set_primary("SRV1", "192.0.2.9"))

        r = json.loads(await server.server_rename("web1", "web-main"))
        assert r == {"renamed": "web1", "to": "web-main"}, r
        assert list(yaml.safe_load(path.read_text())["servers"]) == ["SRV1", "web-main"]
        assert "error" in json.loads(await server.server_rename("web-main", "srv1"))   # дубликат
        assert "error" in json.loads(await server.server_rename("web-main", "1bad"))   # невалидно име

        f = json.loads(await server.server_failover("web-main"))
        assert "only one address" in f["error"], f
        f = json.loads(await server.server_failover("SRV1"))
        assert "failover failed" in f["error"], f

        h = json.loads(await server.health_status())
        assert h["config_ok"] and set(h["servers"]) == {"SRV1", "web-main"}, h

        v = json.loads(await server.version_info())
        assert v["version"] and v["commit"] and "help" in v["commands"], v

        assert len(json.loads(await server.help())) == len(server.HELP)
        assert json.loads(await server.help("server_get"))["example"] == {"name": "SRV1"}
        assert "error" in json.loads(await server.help("nope"))
        # ---- credentials_set: само имена на променливи и пътища, без тайни ----
        assert "error" in json.loads(await server.credentials_set("SRV1", password_env="s3cret pass"))
        assert "error" in json.loads(await server.credentials_set("SRV1", key_path="/nope/key"))
        keyfile = pathlib.Path(directory) / "id_test"
        keyfile.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nmock\n-----END OPENSSH PRIVATE KEY-----\n")
        c = json.loads(await server.credentials_set("SRV1", password_env="NEW_PASS_ENV", key_path=str(keyfile)))
        assert c["password_env"] == "NEW_PASS_ENV" and c["key"] is True, c
        assert "mock" not in json.dumps(c)                                   # ключът не се връща
        saved = yaml.safe_load(path.read_text())["servers"]["SRV1"]
        assert saved["password_env"] == "NEW_PASS_ENV" and "password" not in saved

        # ---- credentials_rotate: без потвърждение само план ----
        plan = json.loads(await server.credentials_rotate("SRV1"))
        assert plan["confirmation_required"] and plan["key_type"] == "ed25519", plan
        assert "error" in json.loads(await server.credentials_rotate("SRV1", key_type="dsa", confirm=True))

        os.environ["GITHUB_ACTIONS"] = "true"                              # в GitHub: махането на стар ключ е забранено
        assert "ephemeral" in json.loads(await server.credentials_rotate("SRV1", confirm=True, remove_old_key=True))["error"]
        os.environ.pop("GITHUB_ACTIONS")

        # ---- credentials_rotate с подменени (mock) ключ, сървър и вход ----
        os.environ["SSH_CONNECTOR_KEY_DIR"] = directory
        server.KEY_DIR = pathlib.Path(directory) / "keys"

        class FakeKey:
            def write_private_key(self, p):
                pathlib.Path(p).write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nnew\n")
            def export_public_key(self):
                return b"ssh-ed25519 AAAAmockNEW\n"
        server.asyncssh.generate_private_key = lambda *a, **k: FakeKey()
        commands = []

        async def fake_run(self, command, timeout):
            commands.append(command)
            return {"exit_code": 0, "stdout": "", "stderr": ""}

        class FakeConn:
            def close(self):
                pass

        async def fake_connect(self, host):
            assert self.cfg["key"].startswith(str(server.KEY_DIR)), self.cfg   # входът е с новия ключ
            return FakeConn()
        server.Server.run = fake_run
        server.Server._connect_host = fake_connect

        rot = json.loads(await server.credentials_rotate("SRV1", confirm=True))
        assert rot.get("rotated") and rot["key_type"] == "ed25519", rot
        assert "new" not in json.dumps(rot).replace("new_key_file", "")    # частният ключ не се връща
        assert any("authorized_keys" in c and "AAAAmockNEW" in c for c in commands), commands
        saved = yaml.safe_load(path.read_text())["servers"]["SRV1"]
        assert saved["key"].startswith(str(server.KEY_DIR)) and saved["key_type"] == "ed25519", saved
        assert oct(os.stat(saved["key"]).st_mode & 0o777) == "0o600"

        async def fail_connect(self, host):
            raise OSError("mock: login refused")
        server.Server._connect_host = fail_connect
        before = yaml.safe_load(path.read_text())["servers"]["SRV1"]["key"]
        bad = json.loads(await server.credentials_rotate("SRV1", confirm=True))
        assert "profile NOT changed" in bad["error"], bad
        assert yaml.safe_load(path.read_text())["servers"]["SRV1"]["key"] == before   # профилът е непроменен

        h = json.loads(await server.health_status())
        assert h["servers"]["SRV1"]["key_age_days"] == 0 and not h["servers"]["SRV1"]["key_rotation_due"], h
    print("v1.3 mock tests PASSED")


if __name__ == "__main__":
    asyncio.run(main())
