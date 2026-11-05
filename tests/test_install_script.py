"""Offline tests for install.sh with fake `claude` / `codex` commands (no network, no real client)."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install.sh"
SECRET = "s3cr3t-token-value-123"

FAKE = """#!/bin/sh
printf '%s' "$(basename "$0")" >> "$FAKE_LOG"
for a in "$@"; do printf ' [%s]' "$a" >> "$FAKE_LOG"; done
printf '\\n' >> "$FAKE_LOG"
exit "${FAKE_EXIT:-0}"
"""


class InstallScript(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.log = self.tmp / "log"
        self.log.write_text("")
        for tool in ("sh", "python3", "sed", "mkdir", "chmod", "cat", "id", "basename", "stty"):
            path = shutil.which(tool)
            if path:
                (self.bin / tool).symlink_to(path)

    def fake(self, *names):
        for n in names:
            p = self.bin / n
            p.write_text(FAKE)
            p.chmod(0o755)

    def run_script(self, *args, env=None):
        e = {"PATH": str(self.bin), "HOME": str(self.home), "FAKE_LOG": str(self.log)}
        e.update(env or {})
        r = subprocess.run(["sh", str(SCRIPT), *args], env=e, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        return r, self.log.read_text()

    def test_syntax(self):
        r = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plugin_both_clients(self):
        self.fake("claude", "codex")
        r, log = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("claude [plugin] [marketplace] [add] [ykinfohome7/KSSH-FOR-Friends]", log)
        self.assertIn("claude [plugin] [install] [ssh-connector@kssh-for-friends]", log)
        self.assertIn("codex [plugin] [marketplace] [add] [ykinfohome7/KSSH-FOR-Friends]", log)
        self.assertIn("codex [plugin] [add] [ssh-connector]", log)
        cfg = self.home / ".ssh-connector" / "servers.yaml"
        self.assertTrue(cfg.exists())
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.home / ".ssh-connector").stat().st_mode & 0o777, 0o700)

    def test_existing_config_untouched(self):
        self.fake("claude")
        d = self.home / ".ssh-connector"
        d.mkdir()
        (d / "servers.yaml").write_text("mine\n")
        r, _ = self.run_script("--claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((d / "servers.yaml").read_text(), "mine\n")

    def test_only_selected_client(self):
        self.fake("claude", "codex")
        r, log = self.run_script("--codex")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("claude", log)

    def test_dry_run_changes_nothing(self):
        self.fake("claude", "codex")
        r, log = self.run_script("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(log, "")
        self.assertIn("+ claude plugin install ssh-connector@kssh-for-friends", r.stdout)
        self.assertFalse((self.home / ".ssh-connector").exists())

    def test_no_client(self):
        r, _ = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("claude", r.stderr)

    def test_codex_plugin_add_failure_gives_hint(self):
        self.fake("codex")
        r, _ = self.run_script("--codex", env={"FAKE_EXIT": "0"})
        self.assertEqual(r.returncode, 0)
        # make only `codex plugin add` fail
        (self.bin / "codex").write_text(FAKE.replace('exit "${FAKE_EXIT:-0}"',
                                                     '[ "$2" = add ] && exit 1; exit 0'))
        r, _ = self.run_script("--codex")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("/plugins", r.stdout)

    def test_remote_token_never_printed(self):
        self.fake("claude", "codex")
        r, log = self.run_script("--remote", "https://kssh.example.ts.net/mcp", env={"KSSH_TOKEN": SECRET})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertIn(f"[Authorization: Bearer {SECRET}]", log)
        self.assertIn("claude [mcp] [add] [--scope] [user] [--transport] [http] [kssh] [https://kssh.example.ts.net/mcp]", log)
        codex_line = [l for l in log.splitlines() if l.startswith("codex")][0]
        self.assertIn("[--bearer-token-env-var] [KSSH_TOKEN]", codex_line)
        self.assertNotIn(SECRET, codex_line)

    def test_remote_rejects_unsafe_urls(self):
        self.fake("claude")
        for url in ("http://kssh.example/mcp", "https://kssh.example/TOKEN/mcp", "https://u:p@kssh.example/mcp",
                    "https://kssh.example/mcp?x=1", "https://kssh.example", "https://kssh.example/mcp;id",
                    "https://kssh.example/$(id)/mcp"):
            r, log = self.run_script("--claude", "--remote", url, env={"KSSH_TOKEN": SECRET})
            self.assertNotEqual(r.returncode, 0, url)
            self.assertEqual(log, "", url)

    def test_remote_needs_token_without_tty(self):
        self.fake("claude")
        r, log = self.run_script("--claude", "--remote", "https://kssh.example.ts.net/mcp")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(log, "")

    def test_bad_name_and_option(self):
        self.fake("claude")
        for args in (["--name", "a b"], ["--name", "x;y"], ["--bogus"]):
            r, log = self.run_script("--claude", *args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertEqual(log, "")

    def test_no_sudo_or_eval(self):
        text = SCRIPT.read_text()
        for bad in ("sudo ", "eval ", "rm -", "apt-get", "apt install -y"):
            self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
