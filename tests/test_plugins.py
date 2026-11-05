"""Plugin checks (no network): manifests agree with VERSION, and an installed plugin (only the
plugins/ssh-connector folder, as Claude Code / Codex copy it) reports the right version and all commands.
Проверка на плъгините: версиите съвпадат, а инсталиран плъгин (само неговата папка) показва текущата версия и всички команди."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "ssh-connector"


def main():
    version = (ROOT / "VERSION").read_text().strip()
    for m in (PLUGIN / ".claude-plugin" / "plugin.json", PLUGIN / ".codex-plugin" / "plugin.json"):
        got = json.loads(m.read_text())["version"]
        assert got == version, f"{m.relative_to(ROOT)} has {got}, VERSION has {version}"
    for m in (ROOT / ".claude-plugin" / "marketplace.json", ROOT / ".agents" / "plugins" / "marketplace.json",
              PLUGIN / "codex-mcp.json"):
        json.loads(m.read_text())                                           # валиден JSON
    # Codex/ChatGPT вижда командите само ако сървърите са под ключа "mcpServers"
    codex_mcp = json.loads((PLUGIN / "codex-mcp.json").read_text())
    assert "ssh" in codex_mcp.get("mcpServers", {}), "codex-mcp.json must wrap servers in \"mcpServers\""
    assert codex_mcp["mcpServers"]["ssh"].get("cwd") == ".", "codex-mcp.json: cwd must be the plugin root"

    with tempfile.TemporaryDirectory() as d:
        # копираме само папката на плъгина — както я инсталира Claude Code / Codex
        installed = pathlib.Path(d) / "cache" / "ssh-connector"
        shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__"))
        code = ("import asyncio, json, sys; sys.path.insert(0, sys.argv[1]); import server; "
                "print(json.dumps({'v': server.APP_VERSION, 'n': len(server.HELP)}))")
        env = {**os.environ, "SSH_CONNECTOR_CONFIG": str(PLUGIN / "servers.example.yaml")}
        out = subprocess.run([sys.executable, "-c", code, str(installed)], capture_output=True, text=True,
                             env=env, cwd=d, timeout=60)
        assert out.returncode == 0, out.stderr
        info = json.loads(out.stdout.strip().splitlines()[-1])
        assert info["v"] == version, f"installed plugin reports {info['v']}, expected {version}"
        assert info["n"] == 60, info  # 48 + 8 read-only SNMP + 4 read-only RDP/Windows tools
    # Изолиран инсталиран плъгин през истинския MCP stdio протокол (виж tests/test_installed_plugin.py)
    import asyncio
    sys.path.insert(0, str(ROOT / "tests"))
    import test_installed_plugin
    report = asyncio.run(test_installed_plugin.main())
    assert report["launcher"]["tools"] == info["n"], report
    print(f"plugin tests PASSED (version {version}, commands {info['n']})")


if __name__ == "__main__":
    main()
