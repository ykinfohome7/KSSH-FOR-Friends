"""Offline checks of install-server.sh: no personal hosts, PUBLIC_URL validation, address shown by ssh-connector-info."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install-server.sh"


def info_script():
    text = SCRIPT.read_text()
    m = re.search(r"cat > /usr/local/bin/ssh-connector-info <<'EOF'\n(.*?)\nEOF\n", text, re.S)
    assert m, "ssh-connector-info heredoc not found"
    return m.group(1)


class InstallServerScript(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in ("sh", "python3", "cat", "timeout"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        (self.tmp / "id_ed25519.pub").write_text("ssh-ed25519 AAAAtest ssh-connector@test\n")

    def run_info(self, env_lines, tailscale_json=None):
        env = self.tmp / "env"
        env.write_text("SSH_CONNECTOR_TOKEN=tok-123456789012345678901234\n" + env_lines)
        body = info_script().replace("/etc/ssh-connector/env", str(env)).replace(
            "/var/lib/ssh-connector/.ssh/id_ed25519.pub", str(self.tmp / "id_ed25519.pub"))
        script = self.tmp / "info.sh"
        script.write_text(body)
        ts = self.bin / "tailscale"
        if tailscale_json is None:
            ts.unlink(missing_ok=True)
        else:
            ts.write_text("#!/bin/sh\ncat <<'J'\n" + tailscale_json + "\nJ\n")
            ts.chmod(0o755)
        r = subprocess.run(["sh", str(script)], env={"PATH": str(self.bin)}, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_syntax(self):
        r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_personal_hosts(self):
        text = SCRIPT.read_text().lower()
        for bad in ("abrdns", "bgssh", "bgadmin", "192.168.", "ts.net"):
            self.assertNotIn(bad, text)

    def test_public_url_wins(self):
        out = self.run_info("SSH_CONNECTOR_PUBLIC_URL=https://kssh.example.com\n",
                            '{"Self": {"DNSName": "box.tail0000.ts.net."}}')
        self.assertIn("URL:            https://kssh.example.com/mcp", out)

    def test_tailscale_name(self):
        out = self.run_info("", '{"Self": {"DNSName": "box.tail0000.ts.net."}}')
        self.assertIn("https://box.tail0000.ts.net/mcp", out)

    def test_placeholder(self):
        out = self.run_info("")
        self.assertIn("https://<your-address>/mcp", out)
        out = self.run_info("", "not json")
        self.assertIn("https://<your-address>/mcp", out)

    def test_public_url_validation(self):
        block = re.search(r'PUBLIC_URL="\$\{PUBLIC_URL:-\}"\n(case .*?\nesac\n)', SCRIPT.read_text(), re.S).group(1)
        for url, ok, shown in (("https://kssh.example.com/mcp", True, "https://kssh.example.com"),
                               ("https://kssh.example.com/", True, "https://kssh.example.com"),
                               ("http://kssh.example.com", False, None),
                               ("https://x/$(id)", False, None),
                               ("https://a b", False, None)):
            r = subprocess.run(["bash", "-c", 'PUBLIC_URL="$1"\n' + block + 'echo "$PUBLIC_URL"', "_", url],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode == 0, ok, url)
            if ok:
                self.assertEqual(r.stdout.strip(), shown)


if __name__ == "__main__":
    unittest.main()
