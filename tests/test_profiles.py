"""Local tests for SSH backend host CRUD, aliases and validation (no network)."""
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
            "defaults": {"connect_timeout": 2, "retries": 1},
            "servers": {"rebex": {"hosts": ["test.rebex.net"], "user": "demo", "tags": ["demo"]}},
        }))
        os.environ["SSH_CONNECTOR_CONFIG"] = str(path)
        import server

        out = json.loads(await server.server_add(
            "SRV1-Codespaces", ["100.64.0.82"], "admin", 2222,
            ["github", "tailscale"], ["github_srv1"], "SRV1_SSH_PASSWORD",
        ))
        assert out.get("added") == "SRV1-Codespaces", out
        assert server.srv("github_srv1").cfg["port"] == 2222
        assert server.srv("github_srv1").cfg["hosts"] == ["100.64.0.82"]
        assert server.srv("rebex").cfg["user"] == "demo"
        assert yaml.safe_load(path.read_text())["servers"]["SRV1-Codespaces"]["password_env"] == "SRV1_SSH_PASSWORD"

        duplicate = json.loads(await server.server_add("github_srv1", ["127.0.0.1"]))
        assert "error" in duplicate, duplicate

        changed = json.loads(await server.server_update(
            "github_srv1", hosts=["srv1.example.com", "100.64.0.82"], port=2202,
        ))
        assert changed.get("updated") == "SRV1-Codespaces", changed
        assert server.srv("SRV1-Codespaces").cfg["port"] == 2202
        assert server.srv("SRV1-Codespaces").cfg["hosts"][0] == "srv1.example.com"

        refused = json.loads(await server.server_remove("github_srv1"))
        assert refused.get("confirmation_required"), refused
        removed = json.loads(await server.server_remove("github_srv1", True))
        assert removed.get("removed") == "SRV1-Codespaces", removed
        assert "SRV1-Codespaces" not in yaml.safe_load(path.read_text())["servers"]
        assert "rebex" in server.SERVERS
    print("SSH profile CRUD / alias / validation tests PASSED")


if __name__ == "__main__":
    asyncio.run(main())
