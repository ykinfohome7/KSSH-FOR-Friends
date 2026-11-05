"""Tests for the KSSH snippet library (offline: no SSH, no devices; stdlib + PyYAML).

Тестове за библиотеката с команди: схема, EN/BG текстове, само READ_ONLY, проверка на параметрите,
защита от shell injection, подготовка за POSIX / pfSense / PowerShell, търсене, експорт, документация.
"""
import base64
import copy
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "ssh-connector"
sys.path.insert(0, str(PLUGIN))
import snippet_library as L  # noqa: E402

LIB = L.load_library()
INJECTIONS = ["x; rm -rf /", "$(id)", "`id`", "a b", "a'b", 'a"b', "a|b", "a&b", "a>b", "a\nb", "-rf",
              "--help", "", " a", "a\\b", "a$b", "../../etc/passwd", "a\x00b", "ä", "a*b", "a{b}"]
DANGEROUS = [
    "rm -rf /tmp/x", "sudo reboot", "systemctl restart sshd", "systemctl --no-pager stop nginx",
    "apt-get install -y nmap", "dnf -y update", "ip link set eno1 down", "ip route del default",
    "iptables -F", "nft flush ruleset", "pfctl -d", "pfctl -F all", "qm stop 100", "pct exec 101 -- id",
    "pvesh delete /nodes/x/qemu/100", "zpool scrub rpool", "zfs destroy rpool/data", "wg set wg0 peer X remove",
    "wg show wg0 private-key", "tailscale up", "tailscale funnel 443 on", "postsuper -d ALL", "postqueue -f",
    "echo x > /etc/hosts", "cat a | tee /etc/x", "sed -i s/a/b/ f", "useradd bob", "passwd root", "chmod 777 /",
    "find / -name x -delete", "curl -X POST https://x", "bash -c id", "eval id", "nc -l 4444",
    "shutdown -h now", "snmpset -v2c h sysName.0 s x", "snmpwalk -v3 -A secret h", "cat <<EOF",
    "Restart-Service W3SVC", "Stop-Computer", "Set-ADUser bob", "Remove-Item C:\\x", "net user bob x /add",
    "sc stop DNS", "vssadmin delete shadows /all", "repadmin /syncall", "dcdiag /fix", "reg add HKLM\\x",
    "netsh advfirewall set allprofiles state off", "iex 'x'", "Get-Process | Out-File C:\\x.txt",
    "Dismount-Database DB1", "Clear-DnsServerCache", "Set-ServerComponentState x",
]
SAFE = ["systemctl status ssh --no-pager", "cat /var/run/reboot-required", "ls 2>/dev/null",
        "tailscale serve status", "pfctl -sr", "Get-Service -Name x | Format-List", "dnf -q -C check-update",
        "Add-PSSnapin Microsoft.Exchange.Management.PowerShell.SnapIn", "Import-Module ActiveDirectory",
        "nc -z -v -w 3 h 443 2>&1", "journalctl -p err -b -n 5 --no-pager", "Write-Output x"]


def _snippet(**over):
    s = {"id": "linux.test_x", "category": "linux", "risk": "READ_ONLY", "os": ["debian"], "shell": "posix",
         "privilege": "user", "timeout": 30, "tags": [], "command": "echo ok",
         "name": {"en": "Test", "bg": "Тест"}, "description": {"en": "Test", "bg": "Тест"},
         "expected": {"en": "ok", "bg": "добре"}, "errors": {"en": "none", "bg": "няма"}}
    s.update(over)
    return s


def _validate(s):
    errors = []
    L._validate_snippet(copy.deepcopy(s), errors)
    return errors


class LibraryShape(unittest.TestCase):
    def test_library_loads_without_errors(self):
        self.assertEqual(LIB.errors, [])
        self.assertGreaterEqual(len(LIB.snippets), 120)

    def test_all_twelve_categories_present(self):
        cats = {s["category"] for s in LIB.snippets.values()}
        self.assertEqual(cats, set(L.CATEGORIES))
        self.assertEqual(len(L.CATEGORIES), 12)
        for c in cats:
            self.assertGreaterEqual(sum(1 for s in LIB.snippets.values() if s["category"] == c), 5, c)

    def test_every_snippet_has_required_documentation(self):
        for sid, s in LIB.snippets.items():
            self.assertRegex(sid, L.ID_RE)
            self.assertTrue(sid.startswith(s["category"] + "."))
            for field in L.BILINGUAL:
                self.assertTrue(s[field]["en"].strip() and s[field]["bg"].strip(), (sid, field))
                self.assertRegex(s[field]["bg"], L.CYRILLIC, (sid, field))
            self.assertIn(s["privilege"], L.PRIVILEGES)
            self.assertTrue(set(s["os"]) <= set(L.OS_NAMES))
            for p in s["params"]:
                self.assertRegex(p["description"]["bg"], L.CYRILLIC, (sid, p["name"]))
                self.assertTrue("default" in p or "example" in p)

    def test_phase_one_contains_only_read_only(self):
        for sid, s in LIB.snippets.items():
            self.assertEqual(s["risk"], "READ_ONLY", sid)
            self.assertFalse(s["requires_approval"], sid)
            self.assertEqual(L.readonly_violations(L._script(s, {n: "x" for n in L.PLACEHOLDER.findall(s["command"])})),
                             [], sid)

    def test_no_secrets_in_library(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in (PLUGIN / "snippets").glob("*.yaml"))
        for marker in ("BEGIN PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY", "tskey-", "ghp_", "password=",
                       "-A ", "-X ", "defAuthPassphrase "):
            if marker in ("-A ", "-X "):
                self.assertNotRegex(text, r"snmp\w*\s[^\n]*\s" + marker.strip() + r"\s", marker)
            elif marker == "defAuthPassphrase ":
                self.assertNotRegex(text, r"(?m)^\s*defAuthPassphrase \S", marker)
            else:
                self.assertNotIn(marker, text)


class DenyList(unittest.TestCase):
    def test_dangerous_commands_are_caught(self):
        missed = [c for c in DANGEROUS if not L.readonly_violations(c)]
        self.assertEqual(missed, [])

    def test_safe_commands_pass(self):
        flagged = {c: L.readonly_violations(c) for c in SAFE if L.readonly_violations(c)}
        self.assertEqual(flagged, {})

    def test_changing_snippet_in_read_only_is_rejected(self):
        errs = _validate(_snippet(command="systemctl restart ssh"))
        self.assertTrue(any("not read-only" in e for e in errs), errs)


class Validation(unittest.TestCase):
    def test_change_without_approval_is_rejected(self):
        errs = _validate(_snippet(risk="CHANGE", requires_approval=False, command="systemctl restart ssh"))
        self.assertTrue(any("requires_approval" in e for e in errs), errs)
        self.assertEqual(_validate(_snippet(risk="CHANGE", requires_approval=True, command="systemctl restart ssh")), [])

    def test_placeholders_must_match_params(self):
        p = {"name": "host", "type": "host", "example": "h", "description": {"en": "h", "bg": "хост"}}
        self.assertTrue(_validate(_snippet(command="ping {{host}}")))                 # undeclared
        self.assertTrue(_validate(_snippet(command="ping x", params=[p])))            # unused
        self.assertEqual(_validate(_snippet(command="ping {{host}}", params=[p])), [])

    def test_placeholder_inside_quotes_is_rejected(self):
        p = {"name": "host", "type": "host", "example": "h", "description": {"en": "h", "bg": "хост"}}
        for cmd in ("ping '{{host}}'", 'ping "{{host}}"', "sh -c 'ping {{host}}'"):
            errs = _validate(_snippet(command=cmd, params=[p]))
            self.assertTrue(any("inside quotes" in e for e in errs), (cmd, errs))

    def test_missing_bulgarian_is_rejected(self):
        errs = _validate(_snippet(name={"en": "Test", "bg": "Test"}))
        self.assertTrue(any("Bulgarian" in e for e in errs), errs)

    def test_unknown_keys_and_bad_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "linux.yaml").write_text(yaml.safe_dump(
                {"category": "linux", "snippets": [dict(_snippet(), surprise=1)]}, allow_unicode=True), encoding="utf-8")
            lib = L.load_library(d)
            self.assertEqual(lib.snippets, {})
            self.assertTrue(any("unknown keys" in e for e in lib.errors))
        for over in ({"risk": "MAYBE"}, {"shell": "fish"}, {"privilege": "god"}, {"os": ["windows-95"]},
                     {"timeout": 0}, {"id": "Linux.X"}, {"id": "proxmox.x"}):
            self.assertTrue(_validate(_snippet(**over)), over)

    def test_bad_enum_and_int_definitions(self):
        bad_enum = {"name": "t", "type": "enum", "choices": ["ok", "a b"], "default": "ok",
                    "description": {"en": "t", "bg": "тип"}}
        bad_int = {"name": "n", "type": "int", "default": 1, "description": {"en": "n", "bg": "брой"}}
        self.assertTrue(_validate(_snippet(command="echo {{t}}", params=[bad_enum])))
        self.assertTrue(_validate(_snippet(command="echo {{n}}", params=[bad_int])))


class Parameters(unittest.TestCase):
    def test_every_parameter_rejects_injection(self):
        for sid, s in LIB.snippets.items():
            for p in s["params"]:
                for bad in INJECTIONS:
                    if p["type"] in ("int", "port") and bad.isdigit():
                        continue
                    with self.assertRaises(ValueError, msg=(sid, p["name"], bad)):
                        L.check_value(p, bad)

    def test_examples_render_and_contain_no_unquoted_metacharacters(self):
        for sid, s in LIB.snippets.items():
            r = L.render(LIB, sid, L.example_params(s))
            self.assertNotIn("{{", r["script"], sid)
            self.assertLessEqual(len(r["command"]), L.MAX_COMMAND, sid)

    def test_types(self):
        ok = {"int": (5, {"min": 1, "max": 10}), "port": ("443", {}), "host": ("mail.example.com", {}),
              "ip": ("2001:db8::1", {}), "token": ("MSExchange_Transport-1", {}), "email": ("a.b+c@example.com", {}),
              "path": ("/var/lib/vz/dump", {}), "oid": ("1.3.6.1.2.1.1", {}), "device": ("/dev/nvme0n1", {}),
              "iface": ("vmbr0.30", {})}
        for ptype, (value, extra) in ok.items():
            self.assertTrue(L.check_value({"type": ptype, **extra}, value), ptype)
        bad = {"int": ("11", {"min": 1, "max": 10}), "port": ("70000", {}), "host": ("-h", {}), "ip": ("999.1.1.1", {}),
               "path": ("/etc/../shadow", {}), "oid": ("1.3.6.1;id", {}), "device": ("/dev/sda1;", {}),
               "iface": ("eth0 up", {}), "email": ("a@b;c", {})}
        for ptype, (value, extra) in bad.items():
            with self.assertRaises(ValueError, msg=ptype):
                L.check_value({"type": ptype, **extra}, value)
        with self.assertRaises(ValueError):
            L.check_value({"type": "int", "min": 1, "max": 9}, True)

    def test_unknown_and_missing_parameters(self):
        with self.assertRaises(L.SnippetError) as e:
            L.render(LIB, "network.ping", {"host": "192.0.2.1", "evil": "1"})
        self.assertEqual(e.exception.code, "INVALID_PARAM")
        with self.assertRaises(L.SnippetError) as e:
            L.render(LIB, "network.ping", {})
        self.assertEqual(e.exception.code, "INVALID_PARAM")
        with self.assertRaises(L.SnippetError) as e:
            L.render(LIB, "nope.nothing")
        self.assertEqual(e.exception.code, "UNKNOWN_SNIPPET")


class Rendering(unittest.TestCase):
    def test_posix_quoting(self):
        r = L.render(LIB, "linux.service_status", {"unit": "postfix@-"})
        self.assertEqual(r["command"], "systemctl status postfix@- --no-pager -l")
        self.assertEqual(shlex.split(r["command"])[2], "postfix@-")
        r = L.render(LIB, "network.ping", {"host": "192.0.2.1", "count": "3"})
        self.assertEqual(r["command"], "ping -c 3 -W 2 192.0.2.1")

    def test_powershell_is_encoded_and_quoted(self):
        r = L.render(LIB, "windows.dns_resolve", {"name": "example.com", "type": "MX"})
        self.assertTrue(r["command"].startswith("powershell -NoProfile -NonInteractive -EncodedCommand "))
        script = base64.b64decode(r["command"].split()[-1]).decode("utf-16-le")
        self.assertEqual(script, r["script"])
        self.assertIn("-Name 'example.com' -Type 'MX'", script)
        self.assertEqual(L.quote("o'brien", "token", "powershell"), "'o''brien'")

    def test_defence_in_depth_quoting_and_option_guard(self):
        # validated values never need it, but quoting and the leading '-' guard must stay in place
        self.assertEqual(L.quote("a b;c", "token", "posix"), "'a b;c'")
        self.assertEqual(L.quote("a b;c", "token", "freebsd-sh"), "'a b;c'")
        self.assertEqual(L.quote("42", "int", "posix"), "42")
        for value in ("-x", "--all", "-"):
            with self.assertRaises(ValueError):
                L._as_text(value)

    def test_exchange_preamble(self):
        r = L.render(LIB, "exchange.queues")
        self.assertTrue(r["script"].splitlines()[1].startswith("Add-PSSnapin Microsoft.Exchange"))

    def test_pfsense_wraps_in_sh(self):
        r = L.render(LIB, "pfsense.pf_states", {"ip": "192.0.2.10"})
        self.assertEqual(r["command"], "/bin/sh -c 'pfctl -ss | grep -F -- 192.0.2.10 | head -n 200'")
        self.assertEqual(shlex.split(r["command"])[:2], ["/bin/sh", "-c"])

    def test_non_read_only_is_not_rendered(self):
        lib = L.Library({"linux.x": dict(_snippet(id="linux.x", risk="CHANGE", requires_approval=True), params=[])}, [])
        with self.assertRaises(L.SnippetError) as e:
            L.render(lib, "linux.x")
        self.assertEqual(e.exception.code, "NOT_ALLOWED")

    def test_sudo_flag_follows_privilege(self):
        self.assertTrue(L.render(LIB, "linux.listening_ports")["sudo"])
        self.assertFalse(L.render(LIB, "linux.disk_usage")["sudo"])


class SearchAndExport(unittest.TestCase):
    def test_search_filters_and_languages(self):
        ids = [r["id"] for r in L.search(LIB, "disk", category="linux")]
        self.assertIn("linux.disk_usage", ids)
        self.assertTrue(all(i.startswith("linux.") for i in ids))
        self.assertIn("linux.disk_usage", [r["id"] for r in L.search(LIB, "ДИСКОВО")])        # BG, case-insensitive
        self.assertIn("security.tls_certificate", [r["id"] for r in L.search(LIB, "сертификат")])
        self.assertTrue(all("pfsense" in r["os"] for r in L.search(LIB, os="pfsense")))
        self.assertEqual(L.search(LIB, "no-such-word-anywhere"), [])
        self.assertEqual(L.search(LIB, "ping", lang="bg")[0]["name"], LIB.snippets[L.search(LIB, "ping")[0]["id"]]["name"]["bg"])

    def test_describe_both_languages(self):
        en, bg = L.describe(LIB, "network.mtu_test", "en"), L.describe(LIB, "network.mtu_test", "bg")
        self.assertIn("MTU", en["name"])
        self.assertRegex(bg["description"], L.CYRILLIC)
        self.assertIn("-M do -s 1472", en["example_script"])

    def test_export_only_parameterless_read_only(self):
        out = L.export_snippets(LIB, ["linux.disk_usage", "windows.system_info"])
        self.assertEqual(out["linux.disk_usage"], "df -hT -x tmpfs -x devtmpfs")
        self.assertTrue(out["windows.system_info"].startswith("powershell -NoProfile"))
        loaded = yaml.safe_load(yaml.safe_dump({"snippets": out}))
        self.assertEqual(loaded["snippets"], out)
        with self.assertRaises(L.SnippetError):
            L.export_snippets(LIB, ["network.ping"])

    def test_cli(self):
        def run(*args):
            return subprocess.run([sys.executable, str(PLUGIN / "snippet_library.py"), *args],
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(run("validate").returncode, 0)
        self.assertIn("linux.disk_usage", run("search", "--q", "диск").stdout)
        bad = run("render", "linux.service_status", "unit=ssh;id")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("INVALID_PARAM", bad.stdout)
        self.assertIn("snippets:", run("export", "linux.memory").stdout)


class DocsAndCompatibility(unittest.TestCase):
    def test_docs_are_generated_from_the_library(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_snippet_docs.py"), "--check"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bg = (ROOT / "docs" / "SNIPPETS.bg.md").read_text(encoding="utf-8")
        en = (ROOT / "docs" / "SNIPPETS.md").read_text(encoding="utf-8")
        for sid, s in LIB.snippets.items():
            self.assertIn(f"| `{sid}` | {s['name']['en']} |", en)
            self.assertIn(f"| `{sid}` | {s['name']['bg']} |", bg)

    def test_library_is_inside_the_installed_plugin_and_loads_without_the_repo(self):
        with tempfile.TemporaryDirectory() as d:
            copy_dir = Path(d) / "ssh-connector"
            shutil.copytree(PLUGIN, copy_dir, ignore=shutil.ignore_patterns("__pycache__"))
            code = ("import sys; sys.path.insert(0, sys.argv[1]); import snippet_library as L; "
                    "lib = L.load_library(); print(len(lib.snippets), len(lib.errors))")
            r = subprocess.run([sys.executable, "-c", code, str(copy_dir)], capture_output=True, text=True,
                               timeout=60, cwd=d)
            self.assertEqual(r.stdout.split(), [str(len(LIB.snippets)), "0"], r.stderr)

    def test_server_tool_count_is_unchanged(self):
        source = (PLUGIN / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("snippet_library", source)                    # phase 1: no new MCP tool


class Smoke(unittest.TestCase):
    """Run parameter-less / defaulted Linux snippets marked smoke: true on this machine (read-only)."""

    def test_smoke_snippets_run_locally(self):
        ran = 0
        for sid, s in sorted(LIB.snippets.items()):
            if not s.get("smoke"):
                continue
            r = L.render(LIB, sid, L.example_params(s))
            first = r["script"].split()[0]
            if not shutil.which(first):
                continue
            out = subprocess.run(["sh", "-c", r["script"]], capture_output=True, text=True, timeout=30)
            self.assertEqual(out.returncode, 0, (sid, out.stderr[-500:]))
            self.assertTrue(out.stdout.strip(), sid)
            ran += 1
        self.assertGreaterEqual(ran, 3)


if __name__ == "__main__":
    unittest.main()
