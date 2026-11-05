"""SSH connector - local MCP server (stdio) for Claude Code and ChatGPT Codex.

Connects directly to target servers over SSH. Features (Termius-style):
- multiple servers (Debian / Red Hat), primary + fallback addresses, automatic failover
- connect/login timeouts, keepalive, retries with backoff, transparent reconnect
- jump hosts (bastion / ProxyJump)
- tags / groups, run a command on a whole group in parallel
- snippets (saved commands)
- sudo support
- interactive shell sessions that keep state (cd, env vars, running programs)
- SFTP: list, read, write, upload, download, mkdir, remove, rename
- local port forwarding (tunnels)
- background jobs for long commands
- event watchers: service errors, crashes / OOM, logins
- command history
- HTTP mode (--http) for ChatGPT and remote clients, token-protected
"""
import asyncio
import json
import os
import shlex
import time
import uuid
from collections import deque
from pathlib import Path

import asyncssh
import yaml
from mcp.server.fastmcp import FastMCP

import endpoint_info  # read-only endpoint/Tailscale report (no side effects on import)

CONFIG_PATH = os.environ.get("SSH_CONNECTOR_CONFIG", str(Path(__file__).with_name("servers.yaml")))


def _read_version():
    """Версията от plugin.json (вътре в плъгина, работи и при инсталиран плъгин), иначе от файла VERSION."""
    here = Path(__file__).resolve().parent
    for manifest in (here / ".claude-plugin" / "plugin.json", here / ".codex-plugin" / "plugin.json"):
        try:
            return json.loads(manifest.read_text())["version"]
        except (OSError, ValueError, KeyError):
            continue
    for f in (here / "VERSION", here.parent.parent / "VERSION"):
        if f.is_file():
            return f.read_text().strip()
    return "dev"


APP_VERSION = _read_version()
TRANSPORT = {"mode": "stdio", "note": "local plugin over stdio; no public address needed"}
mcp = FastMCP("ssh-connector")
try:  # allow access through a tunnel / reverse proxy (Host header is the public domain); auth is enforced below
    from mcp.server.transport_security import TransportSecuritySettings
    mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
except Exception:
    pass


def load_config():
    path = os.path.expanduser(CONFIG_PATH)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No server list at {path}. Copy servers.example.yaml there and add your servers "
            f"(or set SSH_CONNECTOR_CONFIG to another path).")
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    defaults = raw.get("defaults", {})
    servers = {}
    for name, cfg in (raw.get("servers") or {}).items():
        merged = {**defaults, **(cfg or {})}
        if not merged.get("hosts"):
            merged["hosts"] = [merged["host"]]
        merged.setdefault("tags", [])
        servers[name] = merged
    return servers, raw.get("snippets") or {}


class Server:
    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.conn = None
        self.active_host = None
        self.lock = asyncio.Lock()
        self.sem = asyncio.Semaphore(int(cfg.get("max_sessions", 10)))
        self.last_error = None

    def _alive(self):
        return self.conn is not None and not self.conn.is_closed()

    async def _connect_host(self, host):
        c = self.cfg
        kh = c.get("known_hosts", "~/.ssh/known_hosts")
        kh = os.path.expanduser(kh) if kh else None
        key = c.get("key")
        tunnel = None
        if c.get("jump"):  # name of another configured server used as bastion
            tunnel = await srv(c["jump"]).get_conn()
        ct = c.get("connect_timeout", 8)
        kwargs = dict(
            port=int(c.get("port", 22)),
            username=c.get("user"),
            password=os.getenv(c["password_env"]) if c.get("password_env") else c.get("password"),
            known_hosts=kh,
            connect_timeout=ct,
            login_timeout=ct * 2,
            keepalive_interval=c.get("keepalive_interval", 30),
            keepalive_count_max=c.get("keepalive_count_max", 3),
        )
        if key:
            kwargs["client_keys"] = [os.path.expanduser(key)]
        if tunnel:
            kwargs["tunnel"] = tunnel
        return await asyncio.wait_for(asyncssh.connect(host, **kwargs), timeout=ct * 3)

    async def get_conn(self, force=False):
        async with self.lock:
            if self._alive() and not force:
                return self.conn
            if self.conn:
                self.conn.close()
            self.conn = None
            errors = []
            for host in self.cfg["hosts"]:  # primary first, then fallbacks
                for attempt in range(int(self.cfg.get("retries", 3))):
                    try:
                        self.conn = await self._connect_host(host)
                        self.active_host = host
                        self.last_error = None
                        return self.conn
                    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
                        errors.append(f"{host} try {attempt + 1}: {type(e).__name__}: {e}")
                        await asyncio.sleep(min(2 ** attempt, 10))
            self.last_error = "; ".join(errors)
            raise ConnectionError(f"{self.name}: all addresses failed -> {self.last_error}")

    async def run(self, command, timeout, input=None):
        async with self.sem:
            for attempt in range(2):  # one transparent reconnect if the connection died
                conn = await self.get_conn(force=attempt > 0)
                try:
                    r = await asyncio.wait_for(conn.run(command, check=False, input=input), timeout=timeout)
                    res = {"server": self.name, "host": self.active_host, "exit_code": r.exit_status,
                           "stdout": r.stdout, "stderr": r.stderr}
                    HISTORY.append({"ts": time.time(), "server": self.name, "command": command,
                                    "exit_code": r.exit_status})
                    return res
                except asyncio.TimeoutError:
                    return {"server": self.name, "host": self.active_host, "error": f"timeout after {timeout}s"}
                except (asyncssh.ConnectionLost, asyncssh.DisconnectError, BrokenPipeError, ConnectionResetError) as e:
                    if attempt:
                        raise
                    self.last_error = f"connection lost: {e}"
                except asyncssh.ChannelOpenError as e:
                    return {"server": self.name, "error": f"cannot open session (MaxSessions?): {e}"}


SERVERS = {}
SNIPPETS = {}
JOBS = {}
WATCHERS = {}
SHELLS = {}
FORWARDS = {}
EVENTS = deque(maxlen=2000)
HISTORY = deque(maxlen=500)


def init():
    if not SERVERS:
        servers, snippets = load_config()
        for n, c in servers.items():
            SERVERS[n] = Server(n, c)
        SNIPPETS.update(snippets)


def srv(name):
    init()
    if name not in SERVERS:
        for key, item in SERVERS.items():
            if name.casefold() == key.casefold() or any(name.casefold() == x.casefold()
                                                          for x in item.cfg.get("aliases", [])):
                return item
        raise ValueError(f"unknown server '{name}'. Known: {', '.join(SERVERS)}")
    return SERVERS[name]


def _err(e, **extra):
    return json.dumps({**extra, "error": f"{type(e).__name__}: {e}"})


# ---------------- servers & commands ----------------
@mcp.tool()
async def version_info() -> str:
    """Return SSH connector version, git commit, status and the list of available commands."""
    return json.dumps({"name": "ssh-connector", "version": APP_VERSION, "commit": _git_sha(),
                       "status": "ok", "commands": sorted(HELP)}, indent=2)


@mcp.tool()
async def list_servers(tag: str = "") -> str:
    """List configured servers (optionally only one tag/group), addresses, tags and connection state."""
    init()
    out = []
    for s in SERVERS.values():
        if tag and tag not in s.cfg["tags"]:
            continue
        out.append({"name": s.name, "os": s.cfg.get("os"), "hosts": s.cfg["hosts"], "tags": s.cfg["tags"],
                    "jump": s.cfg.get("jump"), "connected": s._alive(), "active_host": s.active_host,
                    "last_error": s.last_error})
    return json.dumps(out, indent=2)


# ---------------- managed server profiles ----------------
# Connection profiles (not credentials) persist in SSH_CONNECTOR_CONFIG.
# On GitHub Actions the config is ephemeral: persist permanent servers in the
# repository's SSH_CONNECTOR_SERVERS secret or reviewed config/github-session.yaml.
import ipaddress
import re
import tempfile

_PROFILE_LOCK = asyncio.Lock()
_PROFILE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_LOGIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _checked_profile(name, hosts, user, port, tags, aliases, password_env):
    if not _PROFILE_NAME.fullmatch(name):
        raise ValueError("Invalid server name: use letters, digits, '-' or '_'")
    if not _LOGIN_NAME.fullmatch(user):
        raise ValueError("Invalid SSH username")
    if not 1 <= int(port) <= 65535:
        raise ValueError("SSH port must be in 1..65535")
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= 8:
        raise ValueError("Provide 1 to 8 IP addresses or hostnames")
    checked_hosts = []
    for host in hosts:
        if not isinstance(host, str) or len(host) > 253 or not host.strip():
            raise ValueError("Invalid hostname or IP address")
        h = host.strip()
        try:
            ipaddress.ip_address(h.strip("[]"))
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", h) or ".." in h:
                raise ValueError("Invalid hostname or IP address")
        checked_hosts.append(h.strip("[]"))
    if not all(isinstance(t, str) and _PROFILE_NAME.fullmatch(t) for t in tags):
        raise ValueError("Tags must contain letters, digits, '-' or '_'")
    if not all(isinstance(a, str) and _PROFILE_NAME.fullmatch(a) for a in aliases):
        raise ValueError("Aliases must contain letters, digits, '-' or '_'")
    if password_env and not _ENV_NAME.fullmatch(password_env):
        raise ValueError("password_env must be the name of an environment variable")
    return {
        "hosts": checked_hosts, "user": user, "port": int(port),
        "tags": tags, "aliases": aliases,
        "known_hosts": "~/.ssh/known_hosts",
        **({"password_env": password_env} if password_env else {}),
    }


def _profile_store(modify, defaults_update=None):
    path = Path(os.path.expanduser(CONFIG_PATH))
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("Server configuration must be a YAML map")
    if defaults_update:  # общи настройки за всички сървъри
        raw["defaults"] = {**(raw.get("defaults") or {}), **defaults_update}
    raw.setdefault("servers", {})
    if not isinstance(raw["servers"], dict):
        raise ValueError("servers must be a YAML map")
    result = modify(raw["servers"])
    # Reject name/alias collisions before committing.
    all_names = set()
    for name, cfg in raw["servers"].items():
        for value in [name] + (cfg or {}).get("aliases", []):
            norm = value.casefold()
            if norm in all_names:
                raise ValueError("Duplicate server name or alias: " + value)
            all_names.add(norm)
    tmpname = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent),
                                         prefix=".servers-", delete=False) as out:
            tmpname = out.name
            yaml.safe_dump(raw, out, sort_keys=False)
        os.chmod(tmpname, 0o600)
        os.replace(tmpname, path)
    finally:
        if tmpname and os.path.exists(tmpname):
            os.unlink(tmpname)
    # Recreate profiles so address/user changes discard old SSH connections.
    for old in SERVERS.values():
        if old.conn:
            old.conn.close()
    SERVERS.clear()
    for name, cfg in raw["servers"].items():
        SERVERS[name] = Server(name, {**raw.get("defaults", {}), **(cfg or {})})
    return result


def _resolve_profile_name(name):
    init()
    for key, item in SERVERS.items():
        if name.casefold() == key.casefold() or any(
                name.casefold() == a.casefold() for a in item.cfg.get("aliases", [])):
            return key
    raise ValueError("unknown server profile: " + name)


@mcp.tool()
async def server_add(name: str, hosts: list[str], user: str = "admin",
                     port: int = 22, tags: list[str] = [], aliases: list[str] = [],
                     password_env: str = "") -> str:
    """Add an SSH profile, with hostname/IP, user, port, tags and aliases. Use password_env, never a literal password. On Actions changes last only for this runner."""
    try:
        profile = _checked_profile(name, hosts, user, port, tags, aliases, password_env)
        async with _PROFILE_LOCK:
            init()
            def change(servers):
                if any(name.casefold() == key.casefold() for key in servers):
                    raise ValueError("Server name already registered")
                servers[name] = profile
                return {"added": name, "host": hosts[0], "port": port, "user": user,
                        "persistence": "active backend config; GitHub Actions runner is ephemeral"}
            return json.dumps(_profile_store(change))
    except Exception as e:
        return _err(e, name=name)


@mcp.tool()
async def server_update(name: str, hosts: list[str] = [], user: str = "",
                        port: int = 0, tags: list[str] = [], aliases: list[str] = [],
                        password_env: str = "") -> str:
    """Update a registered profile's addresses, user, port, tags or aliases. Empty/zero fields keep existing values; credential env refs only."""
    try:
        async with _PROFILE_LOCK:
            realname = _resolve_profile_name(name)
            old = SERVERS[realname].cfg
            profile = _checked_profile(realname, hosts or old["hosts"], user or old.get("user", "root"),
                                       port or old.get("port", 22), tags or old.get("tags", []),
                                       aliases or old.get("aliases", []),
                                       password_env or old.get("password_env", ""))
            def change(servers):
                servers[realname].update(profile)
                return {"updated": realname, "host": profile["hosts"][0],
                        "port": profile["port"], "user": profile["user"]}
            return json.dumps(_profile_store(change))
    except Exception as e:
        return _err(e, name=name)


@mcp.tool()
async def server_remove(name: str, confirm: bool = False) -> str:
    """Remove a registered profile only with confirm=true and no active shell, watcher, tunnel or background job."""
    if not confirm:
        return json.dumps({"confirmation_required": True, "server": name})
    try:
        async with _PROFILE_LOCK:
            realname = _resolve_profile_name(name)
            busy = any(v["server"] == realname for v in SHELLS.values()) or any(
                v["server"] == realname for v in FORWARDS.values()) or any(
                k.startswith(realname + ":") and not t.done() for k, t in WATCHERS.items()) or any(
                v["server"] == realname for v in JOBS.values())
            if busy:
                raise ValueError("Server still has shells, jobs, watchers or forwarded ports")
            def change(servers):
                del servers[realname]
                return {"removed": realname}
            return json.dumps(_profile_store(change))
    except Exception as e:
        return _err(e, name=name)


@mcp.tool()
async def server_test(name: str) -> str:
    """Test an SSH profile's real network route, host key and authentication without showing secrets."""
    try:
        realname = _resolve_profile_name(name)
        s = SERVERS[realname]
        await s.get_conn()
        return json.dumps({"server": realname, "connected": True, "active_host": s.active_host})
    except Exception as e:
        return _err(e, name=name)


# ---------------- v1.3: профили, преминаване, здраве / profiles, failover, health ----------------
def _busy(realname):
    """Връща True, ако сървърът има отворени shell-ове, задачи, наблюдатели или тунели."""
    return any(v["server"] == realname for v in SHELLS.values()) or any(
        v["server"] == realname for v in FORWARDS.values()) or any(
        k.startswith(realname + ":") and not t.done() for k, t in WATCHERS.items()) or any(
        v["server"] == realname for v in JOBS.values())


def _public_profile(realname):
    """Профил без тайни: паролата се показва само като име на променлива и дали е зададена."""
    s = SERVERS[realname]
    c = s.cfg
    return {"name": realname, "hosts": c["hosts"], "primary": c["hosts"][0], "user": c.get("user"),
            "port": int(c.get("port", 22)), "os": c.get("os"), "tags": c.get("tags", []),
            "aliases": c.get("aliases", []), "jump": c.get("jump"),
            "key": bool(c.get("key")),                                    # само дали има ключ, без пътя
            "password_env": c.get("password_env"),                        # само името на променливата
            "password_env_set": bool(c.get("password_env") and os.getenv(c["password_env"])),
            "host_key_check": bool(c.get("known_hosts", "~/.ssh/known_hosts")),
            "connected": s._alive(), "active_host": s.active_host, "last_error": s.last_error}


@mcp.tool()
async def server_get(name: str) -> str:
    """Show one server profile (addresses, user, port, tags, aliases, state) without any secrets."""
    try:
        return json.dumps(_public_profile(_resolve_profile_name(name)), indent=2)
    except Exception as e:
        return _err(e, name=name)


@mcp.tool()
async def server_rename(name: str, new_name: str) -> str:
    """Rename a server profile. Refused while it has shells, jobs, watchers or tunnels."""
    try:
        if not _PROFILE_NAME.fullmatch(new_name):
            raise ValueError("Invalid server name: use letters, digits, '-' or '_'")
        async with _PROFILE_LOCK:
            realname = _resolve_profile_name(name)
            if _busy(realname):
                raise ValueError("Server still has shells, jobs, watchers or forwarded ports")
            def change(servers):
                if any(new_name.casefold() == k.casefold() for k in servers if k != realname):
                    raise ValueError("Server name already registered")
                # пазим реда на сървърите в YAML файла
                items = [(new_name if k == realname else k, v) for k, v in servers.items()]
                servers.clear()
                servers.update(items)
                return {"renamed": realname, "to": new_name}
            return json.dumps(_profile_store(change))
    except Exception as e:
        return _err(e, name=name)


@mcp.tool()
async def server_set_primary(name: str, host: str) -> str:
    """Make one of the server's existing addresses the primary (tried first). Saved in the profile."""
    try:
        async with _PROFILE_LOCK:
            realname = _resolve_profile_name(name)
            hosts = list(SERVERS[realname].cfg["hosts"])
            if host not in hosts:
                raise ValueError("Address is not in this profile; add it first with server_update")
            hosts.remove(host)
            hosts.insert(0, host)                                          # новият основен адрес е първи
            def change(servers):
                servers[realname]["hosts"] = hosts
                servers[realname].pop("host", None)
                return {"server": realname, "primary": host, "hosts": hosts}
            return json.dumps(_profile_store(change))
    except Exception as e:
        return _err(e, name=name)


@mcp.tool()
async def server_failover(name: str) -> str:
    """Switch the live connection to the next address of the server now (for this session only, profile unchanged)."""
    try:
        realname = _resolve_profile_name(name)
        s = SERVERS[realname]
        hosts = s.cfg["hosts"]
        if len(hosts) < 2:
            raise ValueError("Profile has only one address; add a fallback with server_update")
        current = s.active_host or hosts[0]
        start = (hosts.index(current) + 1) if current in hosts else 0
        order = hosts[start:] + hosts[:start]                              # следващият адрес е пръв
        order = [h for h in order if h != current] or order
        async with s.lock:
            if s.conn:
                s.conn.close()
            s.conn, errors = None, []
            for h in order:
                try:
                    s.conn = await s._connect_host(h)
                    s.active_host, s.last_error = h, None
                    return json.dumps({"server": realname, "from": current, "to": h, "connected": True})
                except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
                    errors.append(f"{h}: {type(e).__name__}: {e}")
            s.last_error = "; ".join(errors)
            raise ConnectionError("failover failed -> " + s.last_error)
    except Exception as e:
        return _err(e, name=name)


# ---------------- v1.3: данни за достъп / credentials ----------------
# Правила: никога не връщаме пароли, ключове или токени; пазим само имена на променливи и пътища.
# Ed25519 по подразбиране, RSA 4096 само за стари системи; смяна на ключа на 40-90 дни.
KEY_DIR = Path(os.path.expanduser(os.environ.get("SSH_CONNECTOR_KEY_DIR", "~/.ssh/ssh-connector")))
ROTATE_MIN_DAYS, ROTATE_MAX_DAYS = 40, 90


def _key_age_days(cfg):
    """Възраст на ключа в дни (от key_created в профила) или None, ако не е известна."""
    created = cfg.get("key_created")
    return None if not created else int((time.time() - float(created)) // 86400)


@mcp.tool()
async def credentials_set(name: str, password_env: str = "", key_path: str = "", clear_password: bool = False) -> str:
    """Point a server profile to its credentials WITHOUT storing secrets: password_env = name of an environment
    variable that holds the password; key_path = path to a private key file on the connector host."""
    try:
        if password_env and not _ENV_NAME.fullmatch(password_env):
            raise ValueError("password_env must be the NAME of an environment variable (e.g. SRV1_SSH_PASSWORD), not the password")
        if key_path:
            kp = os.path.expanduser(key_path)
            if not os.path.isfile(kp):                                     # ключът трябва вече да е на машината
                raise ValueError("key_path does not exist on the connector host")
            if "PRIVATE KEY" not in Path(kp).read_text(errors="ignore")[:200]:
                raise ValueError("key_path is not a private key file")
        if not (password_env or key_path or clear_password):
            raise ValueError("Give password_env and/or key_path (or clear_password=true)")
        async with _PROFILE_LOCK:
            realname = _resolve_profile_name(name)
            def change(servers):
                p = servers[realname]
                p.pop("password", None)                                    # литерална парола никога не остава
                if clear_password:
                    p.pop("password_env", None)
                if password_env:
                    p["password_env"] = password_env
                if key_path:
                    p["key"] = key_path
                    p.setdefault("key_created", os.path.getmtime(os.path.expanduser(key_path)))
                return {"server": realname, "password_env": p.get("password_env"),
                        "password_env_set": bool(p.get("password_env") and os.getenv(p["password_env"])),
                        "key": bool(p.get("key"))}
            return json.dumps(_profile_store(change))
    except Exception as e:
        return _err(e, name=name)


@mcp.tool()
async def credentials_rotate(name: str, key_type: str = "ed25519", confirm: bool = False,
                             remove_old_key: bool = False) -> str:
    """Rotate the SSH key of a server: create a new key (ed25519, or rsa4096 for old systems), add its public part
    to the server's authorized_keys, test login with it, then switch the profile. Needs confirm=true.
    The old key is removed from the server only with remove_old_key=true and only after the new key works."""
    try:
        if key_type not in ("ed25519", "rsa4096"):
            raise ValueError("key_type must be ed25519 (default) or rsa4096 (only for old systems)")
        realname = _resolve_profile_name(name)
        s = SERVERS[realname]
        plan = {"server": realname, "key_type": key_type, "current_key_age_days": _key_age_days(s.cfg),
                "steps": ["generate new key on the connector host", "append public key to ~/.ssh/authorized_keys",
                          "test login with the new key", "switch profile to the new key"]
                         + (["remove old public key from the server"] if remove_old_key else [])}
        ephemeral = os.environ.get("GITHUB_ACTIONS") == "true"              # машината в GitHub се изтрива след сесията
        if ephemeral:
            plan["warning"] = "GitHub Actions runner is ephemeral: the new private key is lost when the session ends"
        if remove_old_key and ephemeral:                                    # иначе рискуваме да заключим достъпа
            raise ValueError("remove_old_key is not allowed on an ephemeral GitHub Actions runner (would lock you out)")
        if not confirm:                                                     # без потвърждение само показваме плана
            return json.dumps({"confirmation_required": True, **plan})
        if _busy(realname):
            raise ValueError("Server still has shells, jobs, watchers or forwarded ports")

        # 1) нов ключ — частната част остава само на диска (0600), никога не се връща
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(KEY_DIR, 0o700)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        new_path = KEY_DIR / f"{realname}_{key_type}_{stamp}"
        key = (asyncssh.generate_private_key("ssh-ed25519") if key_type == "ed25519"
               else asyncssh.generate_private_key("ssh-rsa", key_size=4096))
        key.write_private_key(str(new_path))
        os.chmod(new_path, 0o600)
        pub = key.export_public_key().decode().strip() + f" ssh-connector-{realname}-{stamp}"

        # 2) добавяме публичния ключ (без дубликати), през текущата работеща връзка
        add = (f"umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
               f"(grep -qxF {shlex.quote(pub)} ~/.ssh/authorized_keys || echo {shlex.quote(pub)} >> ~/.ssh/authorized_keys)")
        r = await s.run(add, 30)
        if r.get("error") or r.get("exit_code") != 0:
            new_path.unlink(missing_ok=True)
            raise RuntimeError("could not add the new public key on the server: " + (r.get("error") or r.get("stderr") or ""))

        # 3) проверка: вход само с новия ключ, със същата проверка на host key
        test_cfg = {**s.cfg, "key": str(new_path), "password_env": None, "password": None}
        tester = Server(realname, test_cfg)
        try:
            conn = await tester._connect_host(s.active_host or s.cfg["hosts"][0])
            conn.close()
        except Exception as e:
            raise RuntimeError(f"new key was added but login with it failed; profile NOT changed: {type(e).__name__}: {e}")

        # 4) превключваме профила към новия ключ
        old_key = s.cfg.get("key")
        async with _PROFILE_LOCK:
            def change(servers):
                p = servers[realname]
                p["key"], p["key_type"], p["key_created"] = str(new_path), key_type, time.time()
                return True
            _profile_store(change)

        # 5) по желание: махаме стария публичен ключ от сървъра (само ако е от нашия ключ)
        removed_old = False
        if remove_old_key and old_key and os.path.isfile(os.path.expanduser(old_key)):
            old_pub = asyncssh.read_private_key(os.path.expanduser(old_key)).export_public_key().decode().split()[:2]
            pattern = " ".join(old_pub)
            rm = (f"umask 077; grep -vF {shlex.quote(pattern)} ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.new; "
                  f"mv ~/.ssh/authorized_keys.new ~/.ssh/authorized_keys")
            rr = await SERVERS[realname].run(rm, 30)
            removed_old = rr.get("exit_code") == 0
        return json.dumps({"server": realname, "rotated": True, "key_type": key_type,
                           "new_key_file": new_path.name, "old_key_removed_from_server": removed_old,
                           "next_rotation_days": [ROTATE_MIN_DAYS, ROTATE_MAX_DAYS],
                           **({"warning": plan["warning"]} if ephemeral else {})})
    except Exception as e:
        return _err(e, name=name)


# ---------------- v1.3.2: sudo парола / sudo password ----------------
# Ти решаваш откъде идва паролата: написваш я сам, взима се от профила или се генерира с избрани от теб
# дължина и символи. Паролата НИКОГА не минава през чата: пази се само в паметта на конектора
# (или в променлива на средата), а се въвежда/показва на еднократна https страница.
import html as _html
import secrets as _secrets
import string as _string
import urllib.parse as _urlparse

_SUDO_PROMPT = re.compile(r"(\[sudo\] password for [^:]*:|^\s*Password:\s*$)", re.I | re.M)  # въпросът за парола
SUDO_SECRETS = {}                     # {сървър: парола} — само в паметта, изчезва при рестарт или mode="clear"
SUDO_LINKS = {}                       # еднократни връзки {nonce: {...}}
SUDO_LINK_TTL = 120                   # връзката важи 2 минути
SUDO_LINK_TRIES = 3                   # до 3 опита за грешна парола на една връзка
SUDO_LEN = (16, 128)                  # граници за генерираната дължина (избираш я ти)
SUDO_CHARSETS = {"lower": _string.ascii_lowercase, "upper": _string.ascii_uppercase,
                 "digits": _string.digits, "symbols": "!#$%&()*+,-./:;<=>?@[]^_{|}~"}


def _sudo_secret(s):
    """Паролата за sudo: първо от паметта (написана/генерирана), после от профила (sudo_password_env)."""
    if s.name in SUDO_SECRETS:
        return SUDO_SECRETS[s.name]
    env = s.cfg.get("sudo_password_env")
    return os.getenv(env) if env and os.getenv(env) else None


def _sudo_needed(name):
    """Отговор, когато сървърът иска парола, а такава не е избрана — даваме избора на потребителя."""
    return {"sudo_password_required": True,
            "choose_one": {"prompt": f"sudo_password(server='{name}', mode='prompt')  -> you type it on a one-time page",
                           "profile": f"sudo_password(server='{name}', mode='profile', password_env='MY_ENV_NAME')",
                           "generate": f"sudo_password(server='{name}', mode='generate', length=<you choose>, "
                                       f"charset='<lower,upper,digits,symbols>')"}}


async def _sudo_run(s, command, timeout):
    """Изпълнява команда със sudo. С парола: sudo -S (паролата по stdin, не в командата). Без: sudo -n."""
    secret = _sudo_secret(s)
    if secret is None:
        r = await s.run(f"sudo -n sh -c {shlex.quote(command)}", timeout)
        if "password is required" in (r.get("stderr") or "") or "terminal is required" in (r.get("stderr") or ""):
            r.update(_sudo_needed(s.name))
        return r
    r = await s.run(f"sudo -S -p '' sh -c {shlex.quote(command)}", timeout, input=secret + "\n")
    if re.search(r"incorrect password|Sorry, try again", r.get("stderr") or ""):
        r["sudo_password_wrong"] = True                                    # грешна парола — избери отново
        r.update(_sudo_needed(s.name))
    return r


def _generate_password(length, charset):
    """Случайна парола с ТВОИТЕ дължина и символи; поне по един знак от всеки избран вид."""
    classes = [c.strip().lower() for c in (charset or "").split(",") if c.strip()]
    if not classes:
        raise ValueError("charset is required: any of lower,upper,digits,symbols (comma separated) — you choose")
    bad = [c for c in classes if c not in SUDO_CHARSETS]
    if bad:
        raise ValueError(f"unknown charset {bad}; allowed: {', '.join(SUDO_CHARSETS)}")
    if not SUDO_LEN[0] <= int(length) <= SUDO_LEN[1]:
        raise ValueError(f"length is required and must be {SUDO_LEN[0]}..{SUDO_LEN[1]} — you choose")
    pool = "".join(SUDO_CHARSETS[c] for c in dict.fromkeys(classes))
    while True:                                                            # секретен генератор (secrets)
        pw = "".join(_secrets.choice(pool) for _ in range(int(length)))
        if all(any(ch in SUDO_CHARSETS[c] for ch in pw) for c in classes):
            return pw


def _public_url():
    """Публичният https адрес: от SSH_CONNECTOR_PUBLIC_URL, иначе от файла SSH_CONNECTOR_PUBLIC_URL_FILE,
    който скриптът на сесията записва СЛЕД като всичко е стартирано (без рестарт на конектора)."""
    base = os.environ.get("SSH_CONNECTOR_PUBLIC_URL", "")
    if not base and os.environ.get("SSH_CONNECTOR_PUBLIC_URL_FILE"):
        try:
            base = Path(os.environ["SSH_CONNECTOR_PUBLIC_URL_FILE"]).read_text().strip()
        except OSError:
            base = ""
    base = base.rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("Secure links need SSH_CONNECTOR_PUBLIC_URL = the connector's https address "
                         "(e.g. the Tailscale Funnel URL) and HTTP mode (--http)")
    return base


def _new_link(kind, server, **data):
    """Еднократна връзка: случаен 256-битов ключ, важи 2 минути, после се изтрива."""
    now = time.time()
    for k in [k for k, v in SUDO_LINKS.items() if v["expires"] < now]:
        SUDO_LINKS.pop(k, None)
    nonce = _secrets.token_urlsafe(32)
    SUDO_LINKS[nonce] = {"kind": kind, "server": server, "expires": now + SUDO_LINK_TTL, "tries": 0, **data}
    return f"{_public_url()}/sudo/{nonce}"


async def _verify_sudo(s, secret):
    """Проверява паролата на сървъра (sudo -k -v), без да изпълнява друго."""
    r = await s.run("sudo -k -S -p '' -v", 30, input=secret + "\n")
    return r.get("exit_code") == 0


async def _apply_password(s, new_pw):
    """Задава новата парола на SSH потребителя на сървъра (chpasswd през sudo)."""
    user = s.cfg.get("user")
    current = _sudo_secret(s)
    if current is not None:
        r = await s.run("sudo -S -p '' chpasswd", 30, input=f"{current}\n{user}:{new_pw}\n")
    else:
        r = await s.run("sudo -n chpasswd", 30, input=f"{user}:{new_pw}\n")
    if r.get("exit_code") != 0:
        raise RuntimeError("could not set the password on the server: " + (r.get("error") or r.get("stderr") or "")[:300])


@mcp.tool()
async def sudo_password(server: str, mode: str, length: int = 0, charset: str = "", apply_on_server: bool = False,
                        password_env: str = "") -> str:
    """Choose the sudo password of a server. YOU decide how; the password never appears in the chat or logs.
    mode='prompt'  : one-time https link (2 min) where you type the password yourself; it is checked on the server.
    mode='profile' : use the preset value from the environment variable named in the profile
                     (password_env='NAME' saves the NAME as sudo_password_env; never the value).
    mode='generate': random password with the length (16-128) and charset (lower,upper,digits,symbols) YOU choose;
                     shown once on a one-time link; with apply_on_server=true it is set on the server only after you
                     press 'I saved it' on that page.
    mode='status'  : which source is active (never the value).  mode='clear': forget the password from memory."""
    try:
        s = srv(server)
        name = s.name
        if mode == "status":
            env = s.cfg.get("sudo_password_env")
            return json.dumps({"server": name, "source": "memory" if name in SUDO_SECRETS else
                               ("profile" if env and os.getenv(env) else None),
                               "sudo_password_env": env, "sudo_password_env_set": bool(env and os.getenv(env)),
                               "open_links": sum(1 for v in SUDO_LINKS.values()
                                                 if v["server"] == name and v["expires"] > time.time())})
        if mode == "clear":
            had = SUDO_SECRETS.pop(name, None) is not None
            for k in [k for k, v in SUDO_LINKS.items() if v["server"] == name]:
                SUDO_LINKS.pop(k, None)
            return json.dumps({"server": name, "cleared": had})
        if mode == "prompt":
            link = _new_link("prompt", name)
            return json.dumps({"server": name, "link": link, "expires_in_s": SUDO_LINK_TTL,
                               "say": "Open the link and type the password there — never in the chat."})
        if mode == "profile":
            if password_env:
                if not _ENV_NAME.fullmatch(password_env):
                    raise ValueError("password_env must be the NAME of an environment variable, not the password")
                async with _PROFILE_LOCK:
                    realname = _resolve_profile_name(name)
                    def change(servers):
                        servers[realname]["sudo_password_env"] = password_env
                        return True
                    _profile_store(change)
                s.cfg["sudo_password_env"] = password_env
            env = s.cfg.get("sudo_password_env")
            if not env:
                raise ValueError("No sudo_password_env in the profile: pass password_env='NAME'")
            if not os.getenv(env):
                raise ValueError(f"Environment variable {env} is empty on the connector host (set it as a secret)")
            SUDO_SECRETS.pop(name, None)                                   # профилът става активен
            ok = await _verify_sudo(s, os.getenv(env))
            return json.dumps({"server": name, "source": "profile", "sudo_password_env": env, "verified": ok})
        if mode == "generate":
            pw = _generate_password(length, charset)
            link = _new_link("reveal", name, value=pw, apply=bool(apply_on_server))
            return json.dumps({"server": name, "link": link, "expires_in_s": SUDO_LINK_TTL,
                               "length": int(length), "charset": charset, "apply_on_server": bool(apply_on_server),
                               "say": "Open the link, save the password, then press the button. "
                                      "Nothing changes on the server before that."})
        raise ValueError("mode must be prompt, profile, generate, status or clear")
    except Exception as e:
        return _err(e, server=server)


def _sudo_html(title, body):
    return ("<!doctype html><html lang=bg><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{_html.escape(title)}</title><style>body{{font:16px system-ui;max-width:28rem;margin:2rem auto;"
            "padding:0 1rem}input,button{font:inherit;padding:.6rem;width:100%;box-sizing:border-box;margin:.4rem 0}"
            "code{display:block;word-break:break-all;background:#eee;padding:.8rem;font-size:1.05rem}</style>"
            f"<h2>{_html.escape(title)}</h2>{body}</html>").encode()


async def sudo_page(scope, receive, send):
    """Еднократната https страница: GET показва формата/паролата, POST записва/прилага. Без токен — ключът е в адреса."""
    nonce = scope.get("path", "")[len("/sudo/"):]
    link = SUDO_LINKS.get(nonce)

    async def reply(status, title, body):
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-store"),
            (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
            (b"content-security-policy", b"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")]})
        await send({"type": "http.response.body", "body": _sudo_html(title, body)})

    if not link or link["expires"] < time.time():
        SUDO_LINKS.pop(nonce, None)
        return await reply(410, "Връзката е изтекла", "<p>Поискай нова връзка в чата.</p>")
    s = srv(link["server"])
    if scope.get("method") == "GET":
        if link["kind"] == "prompt":
            return await reply(200, f"sudo парола: {s.name}",
                               "<form method=post><input type=password name=pw autocomplete=off autofocus required>"
                               "<button>Изпрати</button></form><p>Паролата отива само към конектора.</p>")
        btn = "Записах я — приложи на сървъра" if link["apply"] else "Записах я"
        return await reply(200, f"Нова парола: {s.name}",
                           f"<p>Запиши я сега — показва се само веднъж:</p><code>{_html.escape(link['value'])}</code>"
                           f"<form method=post><button>{btn}</button></form>")
    body = b""
    while True:                                                            # четем формата (POST)
        msg = await receive()
        body += msg.get("body", b"")
        if len(body) > 4096:
            return await reply(413, "Твърде дълго", "")
        if not msg.get("more_body"):
            break
    try:
        if link["kind"] == "prompt":
            pw = (_urlparse.parse_qs(body.decode(errors="replace")).get("pw") or [""])[0]
            if not pw or not await _verify_sudo(s, pw):
                link["tries"] += 1
                if link["tries"] >= SUDO_LINK_TRIES:
                    SUDO_LINKS.pop(nonce, None)
                    return await reply(403, "Грешна парола", "<p>Връзката е затворена. Поискай нова в чата.</p>")
                return await reply(403, "Грешна парола", "<p>Опитай отново: презареди страницата.</p>")
            SUDO_SECRETS[s.name] = pw
            SUDO_LINKS.pop(nonce, None)
            return await reply(200, "Готово ✅", "<p>Паролата е проверена и е в паметта на конектора. "
                                                 "Върни се в чата и напиши „готово“.</p>")
        if link["apply"]:
            await _apply_password(s, link["value"])
        SUDO_SECRETS[s.name] = link["value"]
        SUDO_LINKS.pop(nonce, None)
        return await reply(200, "Готово ✅", "<p>" + ("Паролата е зададена на сървъра. " if link["apply"] else "")
                           + "Върни се в чата и напиши „готово“.</p>")
    except Exception as e:
        SUDO_LINKS.pop(nonce, None)
        return await reply(500, "Грешка", f"<p>{_html.escape(type(e).__name__)}: нищо не е запазено.</p>")


# ---------------- v1.3: настройки на сесията / session settings ----------------
# Разрешени настройки и граници (мин, макс) — без тях стойността се отказва
SESSION_LIMITS = {"connect_timeout": (1, 120), "retries": (1, 10), "keepalive_interval": (5, 600),
                  "keepalive_count_max": (1, 20), "command_timeout": (5, 7200), "max_sessions": (1, 50)}


@mcp.tool()
async def session_configure(server: str = "", connect_timeout: int = 0, retries: int = 0,
                            keepalive_interval: int = 0, keepalive_count_max: int = 0,
                            command_timeout: int = 0, max_sessions: int = 0) -> str:
    """Change connection settings (timeouts, retries, keepalive, parallel sessions) for one server,
    or for all servers when server is empty. Zero = keep the current value. Called with no values it shows them."""
    try:
        asked = {k: v for k, v in dict(connect_timeout=connect_timeout, retries=retries,
                                       keepalive_interval=keepalive_interval, keepalive_count_max=keepalive_count_max,
                                       command_timeout=command_timeout, max_sessions=max_sessions).items() if v}
        for k, v in asked.items():                                          # проверка на границите
            lo, hi = SESSION_LIMITS[k]
            if not lo <= int(v) <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
        async with _PROFILE_LOCK:
            init()
            realname = _resolve_profile_name(server) if server else None
            if not asked:                                                   # само показваме текущите стойности
                cfgs = {realname: SERVERS[realname].cfg} if realname else {n: s.cfg for n, s in SERVERS.items()}
                return json.dumps({n: {k: c.get(k) for k in SESSION_LIMITS} for n, c in cfgs.items()}, indent=2)
            def change(servers):
                if realname:
                    servers[realname].update(asked)                         # само за един сървър
                return {"scope": realname or "defaults (all servers)", "changed": asked}
            return json.dumps(_profile_store(change, None if realname else asked))
    except Exception as e:
        return _err(e, server=server)


# ---------------- v1.3: Tailscale ----------------
# Статус и устройства се четат от локалния `tailscale` на машината на конектора.
# Ключовете се управляват през Tailscale API с TAILSCALE_API_KEY (само от средата, никога не се показва).
TS_API = "https://api.tailscale.com/api/v2"


async def _ts_cli(*args):
    """Пуска `tailscale ...` локално (само четене) и връща (код, изход). При timeout процесът се спира."""
    proc = await asyncio.create_subprocess_exec("tailscale", *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, (out or err).decode(errors="replace")


async def _ts_api(method, path, body=None):
    """Заявка към Tailscale API. Ключът идва само от TAILSCALE_API_KEY и не се връща никъде."""
    import urllib.request
    key = os.environ.get("TAILSCALE_API_KEY", "")
    if not key:
        raise RuntimeError("TAILSCALE_API_KEY is not set on the connector host (add it as a secret)")
    tailnet = os.environ.get("TAILSCALE_TAILNET", "-")                    # '-' = тейлнетът на ключа
    req = urllib.request.Request(f"{TS_API}/tailnet/{tailnet}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    def call():
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
            return json.loads(data) if data else {}
    return await asyncio.to_thread(call)


@mcp.tool()
async def tailscale_status(verify_reachability: bool = False) -> str:
    """Tailscale on the connector host (version, device, account) plus an honest endpoint report:
    transport (stdio plugin / HTTP backend), configured address, Serve (tailnet) vs Funnel (public) vs unknown,
    whether reachability was checked, and safe MCP endpoint templates (never a token).
    verify_reachability=true sends one GET without credentials to the configured address (expects 401).
    Read-only: never runs tailscale up/login/serve/funnel."""
    endpoint = await endpoint_info.collect(TRANSPORT, lambda *a: _ts_cli(*a), verify=bool(verify_reachability))
    try:
        code, ver = await _ts_cli("version")
        code2, st = await _ts_cli("status", "--json")
        if code2 != 0:
            raise RuntimeError("tailscale is not running here: " + endpoint_info.redact(st.strip()[:200]))
        j = json.loads(st)
        me = j.get("Self") or {}
        user = (j.get("User") or {}).get(str(me.get("UserID")), {})
        return json.dumps({"version": ver.splitlines()[0] if ver else None, "backend_state": j.get("BackendState"),
                           "device": me.get("HostName"), "dns_name": (me.get("DNSName") or "").rstrip("."),
                           "ips": me.get("TailscaleIPs"), "online": me.get("Online"),
                           "account": user.get("LoginName"), "tailnet": (j.get("CurrentTailnet") or {}).get("Name"),
                           "funnel_on": endpoint["exposure"]["mode"] == "funnel",
                           "endpoint": endpoint}, indent=2)
    except FileNotFoundError:
        return json.dumps({"error": "tailscale is not installed on the connector host", "endpoint": endpoint},
                          indent=2)
    except Exception as e:
        return json.dumps({"error": endpoint_info.redact(f"{type(e).__name__}: {e}"), "endpoint": endpoint},
                          indent=2)


@mcp.tool()
async def tailscale_devices(online_only: bool = False) -> str:
    """List devices in the tailnet seen by this host: name, IPs, OS, online, last seen."""
    try:
        code, st = await _ts_cli("status", "--json")
        if code != 0:
            raise RuntimeError("tailscale is not running here: " + st.strip()[:200])
        j = json.loads(st)
        out = []
        for p in list((j.get("Peer") or {}).values()) + [j.get("Self") or {}]:
            if online_only and not p.get("Online"):
                continue
            out.append({"name": p.get("HostName"), "dns_name": (p.get("DNSName") or "").rstrip("."),
                        "ips": p.get("TailscaleIPs"), "os": p.get("OS"), "online": p.get("Online"),
                        "last_seen": p.get("LastSeen"), "this_device": p is j.get("Self")})
        return json.dumps(sorted(out, key=lambda d: (not d["online"], d["name"] or "")), indent=2)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def tailscale_keys() -> str:
    """List Tailscale auth keys (id, created, expires, reusable/ephemeral, tags) — never the key values. Needs TAILSCALE_API_KEY."""
    try:
        data = await _ts_api("GET", "/keys?all=true")
        out = []
        for k in data.get("keys", []):
            d = k if "created" in k else await _ts_api("GET", f"/keys/{k['id']}")
            create = ((d.get("capabilities") or {}).get("devices") or {}).get("create") or {}
            out.append({"id": d.get("id"), "description": d.get("description"), "created": d.get("created"),
                        "expires": d.get("expires"), "revoked": d.get("revoked"),
                        "reusable": create.get("reusable"), "ephemeral": create.get("ephemeral"), "tags": create.get("tags")})
        return json.dumps(out, indent=2)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def tailscale_key_create(description: str = "ssh-connector", reusable: bool = True, ephemeral: bool = True,
                               tags: list[str] = [], expiry_days: int = 90, confirm: bool = False) -> str:
    """Create a new Tailscale auth key (needs confirm=true and TAILSCALE_API_KEY). The key value is NOT shown:
    it is saved to a private file (0600) on the connector host; put it into the TAILSCALE_AUTHKEY secret yourself."""
    try:
        if not 1 <= int(expiry_days) <= 90:
            raise ValueError("expiry_days must be 1..90")
        body = {"description": description[:50], "expirySeconds": int(expiry_days) * 86400,
                "capabilities": {"devices": {"create": {"reusable": reusable, "ephemeral": ephemeral,
                                                        "preauthorized": True, "tags": tags}}}}
        if not confirm:
            return json.dumps({"confirmation_required": True, "will_create": body})
        d = await _ts_api("POST", "/keys", body)
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(KEY_DIR, 0o700)
        f = KEY_DIR / f"tailscale_authkey_{d.get('id')}"
        f.write_text((d.get("key") or "") + "\n")                          # стойността отива само във файла
        os.chmod(f, 0o600)
        return json.dumps({"created": True, "id": d.get("id"), "expires": d.get("expires"),
                           "saved_to_file": str(f), "key_shown": False,
                           "next": "copy the file content into the TAILSCALE_AUTHKEY secret, then delete the file"})
    except Exception as e:
        return _err(e)


@mcp.tool()
async def tailscale_key_revoke(key_id: str, confirm: bool = False) -> str:
    """Revoke (delete) a compromised Tailscale auth key by id. Needs confirm=true and TAILSCALE_API_KEY."""
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", key_id):
            raise ValueError("invalid key id")
        if not confirm:
            return json.dumps({"confirmation_required": True, "will_revoke": key_id})
        await _ts_api("DELETE", f"/keys/{key_id}")
        return json.dumps({"revoked": key_id})
    except Exception as e:
        return _err(e, key_id=key_id)


def _git_sha():
    """Git commit на кода: от GitHub Actions или от локалното хранилище."""
    sha = os.environ.get("GITHUB_SHA", "")
    if not sha:
        try:
            import subprocess
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(Path(__file__).resolve().parent),
                                 capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:
            sha = ""
    return sha or "unknown"


@mcp.tool()
async def health_status() -> str:
    """Connector health: version, commit, config, servers and their connection state, open shells/jobs/tunnels/watchers."""
    try:
        init()
        cfg_ok, cfg_error = True, None
    except Exception as e:
        cfg_ok, cfg_error = False, f"{type(e).__name__}: {e}"
    return json.dumps({
        "version": APP_VERSION, "commit": _git_sha(), "transport": TRANSPORT,
        "config_ok": cfg_ok, "config_error": cfg_error,
        "servers": {n: {"connected": s._alive(), "active_host": s.active_host, "last_error": s.last_error,
                        "key_age_days": _key_age_days(s.cfg),
                        "key_rotation_due": (_key_age_days(s.cfg) or 0) >= ROTATE_MAX_DAYS}
                    for n, s in SERVERS.items()},
        "shells": len(SHELLS), "jobs": len(JOBS), "forwards": len(FORWARDS),
        "watchers": sum(1 for t in WATCHERS.values() if not t.done()), "events": len(EVENTS),
    }, indent=2)


@mcp.tool()
async def run_command(server: str, command: str, timeout: int = 0, sudo: bool = False) -> str:
    """Run a shell command on a server. Reconnects / fails over automatically. sudo=True uses the sudo password
    chosen with sudo_password (never shown), or sudo -n; if a password is needed it says so instead of hanging."""
    try:
        s = srv(server)
        t = timeout or s.cfg.get("command_timeout", 120)
        if not sudo:
            return json.dumps(await s.run(command, t), indent=2)
        res = await _sudo_run(s, command, t)
        return json.dumps(res, indent=2)
    except Exception as e:
        return _err(e, server=server)


@mcp.tool()
async def run_on_many(servers: list[str], command: str, timeout: int = 0, sudo: bool = False) -> str:
    """Run the same command on several servers in parallel."""
    res = await asyncio.gather(*(run_command(n, command, timeout, sudo) for n in servers))
    return "[" + ",".join(res) + "]"


@mcp.tool()
async def run_on_group(tag: str, command: str, timeout: int = 0, sudo: bool = False) -> str:
    """Run a command on every server that has the given tag (group)."""
    init()
    names = [n for n, s in SERVERS.items() if tag in s.cfg["tags"]]
    if not names:
        return json.dumps({"error": f"no servers with tag '{tag}'"})
    return await run_on_many(names, command, timeout, sudo)


@mcp.tool()
async def list_snippets() -> str:
    """List saved command snippets from servers.yaml."""
    init()
    return json.dumps(SNIPPETS, indent=2)


@mcp.tool()
async def run_snippet(server: str, name: str, sudo: bool = False) -> str:
    """Run a saved snippet by name on a server."""
    init()
    if name not in SNIPPETS:
        return json.dumps({"error": f"unknown snippet '{name}'", "known": list(SNIPPETS)})
    return await run_command(server, SNIPPETS[name], 0, sudo)


@mcp.tool()
async def history(limit: int = 50, server: str = "") -> str:
    """Show recently executed commands."""
    h = [x for x in HISTORY if not server or x["server"] == server]
    return json.dumps(h[-limit:], indent=2)


# ---------------- background jobs ----------------
@mcp.tool()
async def start_job(server: str, command: str) -> str:
    """Start a long-running command in the background on the server (survives disconnects)."""
    try:
        jid = uuid.uuid4().hex[:8]
        log = f"/tmp/sshconn_{jid}.log"
        cmd = f"nohup sh -c {shlex.quote(command)} > {log} 2>&1 & echo $!"
        r = await srv(server).run(cmd, 30)
        JOBS[jid] = {"server": server, "pid": (r.get("stdout") or "").strip(), "log": log, "command": command}
        return json.dumps({"job_id": jid, **JOBS[jid]})
    except Exception as e:
        return _err(e, server=server)


@mcp.tool()
async def job_status(job_id: str, tail: int = 50) -> str:
    """Check a background job: running or finished, plus the last lines of output."""
    j = JOBS.get(job_id)
    if not j:
        return json.dumps({"error": "unknown job"})
    try:
        r = await srv(j["server"]).run(
            f"if kill -0 {j['pid']} 2>/dev/null; then echo RUNNING; else echo DONE; fi; tail -n {int(tail)} {j['log']}", 30)
        return json.dumps({"job_id": job_id, **r}, indent=2)
    except Exception as e:
        return _err(e, job_id=job_id)


# ---------------- interactive shell sessions ----------------
async def _read_until_idle(proc, wait, idle=0.6):
    buf = []
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=idle)
        except asyncio.TimeoutError:
            if buf:
                break
            continue
        if not chunk:
            break
        buf.append(chunk)
    return "".join(buf)


@mcp.tool()
async def shell_open(server: str, cols: int = 200, rows: int = 50) -> str:
    """Open an interactive shell (with PTY) that keeps state between calls (cd, env, running programs)."""
    try:
        conn = await srv(server).get_conn()
        proc = await conn.create_process(term_type="xterm", term_size=(cols, rows), encoding="utf-8")
        sid = uuid.uuid4().hex[:8]
        SHELLS[sid] = {"server": server, "proc": proc, "opened": time.time()}
        banner = await _read_until_idle(proc, 3)
        return json.dumps({"shell_id": sid, "output": banner})
    except Exception as e:
        return _err(e, server=server)


@mcp.tool()
async def shell_send(shell_id: str, input: str = "", wait: float = 5, enter: bool = True,
                     use_sudo_password: bool = False) -> str:
    """Send input to an open shell and return the output. Use input='\\x03' with enter=False for Ctrl+C.
    When the output shows a password prompt ("sudo_prompt": true), call again with use_sudo_password=true:
    the connector types the password chosen with sudo_password itself; it never appears in the chat."""
    sh = SHELLS.get(shell_id)
    if not sh:
        return json.dumps({"error": "unknown shell_id"})
    proc = sh["proc"]
    try:
        if use_sudo_password:
            secret = _sudo_secret(srv(sh["server"]))
            if secret is None:                                             # няма избрана парола — питаме потребителя
                return json.dumps({"shell_id": shell_id, **_sudo_needed(srv(sh["server"]).name)})
            proc.stdin.write(secret + "\n")                               # паролата отива само към сървъра
            logged = "[shell] <sudo password>"
        else:
            proc.stdin.write(input + ("\n" if enter else ""))
            logged = f"[shell] {input}"
        out = await _read_until_idle(proc, wait)
        HISTORY.append({"ts": time.time(), "server": sh["server"], "command": logged, "exit_code": None})
        res = {"shell_id": shell_id, "output": out, "closed": proc.exit_status is not None}
        if _SUDO_PROMPT.search(out[-300:]):                                # сървърът чака парола
            res["sudo_prompt"] = True
            res["next"] = ("shell_send(use_sudo_password=true)" if _sudo_secret(srv(sh["server"])) is not None
                           else "choose a password first: sudo_password(server, mode='prompt'|'profile'|'generate')")
        return json.dumps(res)
    except Exception as e:
        SHELLS.pop(shell_id, None)
        return _err(e, shell_id=shell_id, note="shell closed; open a new one")


@mcp.tool()
async def shell_close(shell_id: str) -> str:
    """Close an interactive shell."""
    sh = SHELLS.pop(shell_id, None)
    if sh:
        sh["proc"].close()
    return json.dumps({"closed": bool(sh)})


@mcp.tool()
async def list_shells() -> str:
    """List open interactive shells."""
    return json.dumps([{"shell_id": k, "server": v["server"], "opened": v["opened"]} for k, v in SHELLS.items()])


# ---------------- SFTP ----------------
async def _sftp(server):
    conn = await srv(server).get_conn()
    return conn.start_sftp_client()


@mcp.tool()
async def sftp_list(server: str, path: str = ".") -> str:
    """List a remote directory (name, size, type, permissions, modified time)."""
    try:
        async with await _sftp(server) as sftp:
            items = []
            for e in await sftp.readdir(path):
                if e.filename in (".", ".."):
                    continue
                a = e.attrs
                items.append({"name": e.filename, "size": a.size,
                              "dir": bool(a.permissions and (a.permissions & 0o170000) == 0o040000),
                              "perm": oct(a.permissions & 0o777) if a.permissions else None, "mtime": a.mtime})
            return json.dumps(sorted(items, key=lambda x: (not x["dir"], x["name"])), indent=2)
    except Exception as e:
        return _err(e, server=server, path=path)


@mcp.tool()
async def sftp_read(server: str, path: str, max_bytes: int = 200000) -> str:
    """Read a remote text file (up to max_bytes)."""
    try:
        async with await _sftp(server) as sftp:
            async with sftp.open(path, "rb") as f:
                data = await f.read(max_bytes)
            return json.dumps({"path": path, "content": data.decode("utf-8", "replace"), "truncated": len(data) >= max_bytes})
    except Exception as e:
        return _err(e, server=server, path=path)


@mcp.tool()
async def sftp_write(server: str, path: str, content: str, append: bool = False) -> str:
    """Write (or append) text to a remote file."""
    try:
        async with await _sftp(server) as sftp:
            async with sftp.open(path, "ab" if append else "wb") as f:
                await f.write(content.encode("utf-8"))
            return json.dumps({"path": path, "bytes": len(content.encode("utf-8")), "append": append})
    except Exception as e:
        return _err(e, server=server, path=path)


@mcp.tool()
async def sftp_upload(server: str, local_path: str, remote_path: str) -> str:
    """Upload a local file or directory (recursive) to the server."""
    try:
        async with await _sftp(server) as sftp:
            await sftp.put(os.path.expanduser(local_path), remote_path, recurse=True, preserve=True)
            return json.dumps({"uploaded": local_path, "to": remote_path})
    except Exception as e:
        return _err(e, server=server)


@mcp.tool()
async def sftp_download(server: str, remote_path: str, local_path: str) -> str:
    """Download a remote file or directory (recursive) to this computer."""
    try:
        async with await _sftp(server) as sftp:
            await sftp.get(remote_path, os.path.expanduser(local_path), recurse=True, preserve=True)
            return json.dumps({"downloaded": remote_path, "to": local_path})
    except Exception as e:
        return _err(e, server=server)


@mcp.tool()
async def sftp_manage(server: str, action: str, path: str, new_path: str = "") -> str:
    """File management: action = mkdir | remove | rmdir | rename | stat."""
    try:
        async with await _sftp(server) as sftp:
            if action == "mkdir":
                await sftp.makedirs(path, exist_ok=True)
            elif action == "remove":
                await sftp.remove(path)
            elif action == "rmdir":
                await sftp.rmdir(path)
            elif action == "rename":
                await sftp.rename(path, new_path)
            elif action == "stat":
                a = await sftp.stat(path)
                return json.dumps({"path": path, "size": a.size, "perm": oct(a.permissions & 0o777) if a.permissions else None,
                                   "uid": a.uid, "gid": a.gid, "mtime": a.mtime})
            else:
                return json.dumps({"error": "action must be mkdir|remove|rmdir|rename|stat"})
            return json.dumps({"ok": True, "action": action, "path": path, "new_path": new_path or None})
    except Exception as e:
        return _err(e, server=server, path=path)


# ---------------- port forwarding ----------------
@mcp.tool()
async def port_forward(server: str, local_port: int, remote_host: str = "127.0.0.1", remote_port: int = 0) -> str:
    """Open a local tunnel: localhost:local_port -> remote_host:remote_port through the server."""
    try:
        conn = await srv(server).get_conn()
        listener = await conn.forward_local_port("127.0.0.1", local_port, remote_host, remote_port or local_port)
        fid = uuid.uuid4().hex[:8]
        FORWARDS[fid] = {"server": server, "listener": listener, "local": f"127.0.0.1:{listener.get_port()}",
                         "remote": f"{remote_host}:{remote_port or local_port}"}
        return json.dumps({"forward_id": fid, "local": FORWARDS[fid]["local"], "remote": FORWARDS[fid]["remote"]})
    except Exception as e:
        return _err(e, server=server)


@mcp.tool()
async def list_forwards() -> str:
    """List open tunnels."""
    return json.dumps([{"forward_id": k, "server": v["server"], "local": v["local"], "remote": v["remote"]}
                       for k, v in FORWARDS.items()])


@mcp.tool()
async def stop_forward(forward_id: str) -> str:
    """Close a tunnel."""
    f = FORWARDS.pop(forward_id, None)
    if f:
        f["listener"].close()
    return json.dumps({"closed": bool(f)})


# ---------------- events ----------------
WATCH_KINDS = {
    "service_errors": "journalctl -f -n0 -o json -p err",
    "crashes": "journalctl -f -n0 -o json --grep='Out of memory|Killed process|segfault|core dumped|Failed with result|entered failed state'",
    "logins": "journalctl -f -n0 -o json --grep='Accepted (password|publickey)|Failed password|session opened for user'",
}


async def _watch(server, kind):
    s = srv(server)
    cmd = WATCH_KINDS[kind]
    backoff = 1
    while True:
        try:
            conn = await s.get_conn()
            async with conn.create_process(cmd) as proc:
                backoff = 1
                async for line in proc.stdout:
                    try:
                        j = json.loads(line)
                        msg = j.get("MESSAGE")
                        unit = j.get("_SYSTEMD_UNIT") or j.get("SYSLOG_IDENTIFIER")
                    except ValueError:
                        msg, unit = line.strip(), None
                    EVENTS.append({"ts": time.time(), "server": server, "kind": kind, "unit": unit, "message": msg})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            EVENTS.append({"ts": time.time(), "server": server, "kind": "watcher",
                           "message": f"{kind} watcher lost: {e}; reconnecting"})
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


@mcp.tool()
async def start_watch(server: str, kinds: list[str] = ["service_errors", "crashes", "logins"]) -> str:
    """Start listening for events on a server. Kinds: service_errors, crashes, logins."""
    started = []
    for k in kinds:
        if k not in WATCH_KINDS:
            continue
        key = f"{server}:{k}"
        if key not in WATCHERS or WATCHERS[key].done():
            WATCHERS[key] = asyncio.create_task(_watch(server, k))
        started.append(key)
    return json.dumps({"watching": started})


@mcp.tool()
async def stop_watch(server: str) -> str:
    """Stop all event watchers for a server."""
    stopped = [k for k in list(WATCHERS) if k.startswith(server + ":")]
    for k in stopped:
        WATCHERS.pop(k).cancel()
    return json.dumps({"stopped": stopped})


@mcp.tool()
async def get_events(since: float = 0, server: str = "", limit: int = 100) -> str:
    """Read captured events (newest last). Pass the last 'ts' as 'since' to get only new ones."""
    ev = [e for e in EVENTS if e["ts"] > since and (not server or e["server"] == server)]
    return json.dumps(ev[-limit:], indent=2)


# ---------------- GitHub Codespaces lifecycle (v1.3.3) ----------------
# Uses the GitHub CLI identity on the connector host, not SSH/Tailscale or
# tokens supplied as tool arguments. A stopped Codespace cannot wake itself
# through an MCP backend running inside that same Codespace.
import codespaces as _codespaces


@mcp.tool()
async def codespace_status(name: str = "") -> str:
    """List all GitHub Codespaces available to this GitHub account, or one by exact name."""
    return await _codespaces.status(name)


@mcp.tool()
async def codespace_start(name: str) -> str:
    """Start/wake an existing Codespace via GitHub REST API (requires Codespaces permission)."""
    return await _codespaces.start(name)


@mcp.tool()
async def codespace_stop(name: str, confirm: bool = False) -> str:
    """Stop a Codespace only after explicit confirm=true; this may disconnect the MCP host."""
    return await _codespaces.stop(name, confirm)


# ---------------- SNMP (read-only monitoring) ----------------
# Only configured device names from servers.yaml -> snmp.devices, all hosts inside
# snmp.allowed_networks. GET / GETNEXT / GETBULK / bounded WALK only; no SNMP SET exists.
import snmp_tools as _snmp

_snmp.AUDIT = HISTORY


@mcp.tool()
async def snmp_health(device: str) -> str:
    """Read-only SNMP liveness of a configured device: authenticated GET of sysUpTime and sysName."""
    return await _snmp.snmp_health(device)


@mcp.tool()
async def snmp_device_info(device: str) -> str:
    """Read-only device facts: sysDescr/sysName/uptime, model and firmware (ENTITY-MIB), temperature/fans/power
    (ENTITY-SENSOR-MIB). Anything the device does not report is UNAVAILABLE, never estimated."""
    return await _snmp.snmp_device_info(device)


@mcp.tool()
async def snmp_get(device: str, oids: list[str]) -> str:
    """SNMP GET of numeric OIDs (max 32) on a configured device. Read-only."""
    return await _snmp.snmp_get(device, oids)


@mcp.tool()
async def snmp_getnext(device: str, oids: list[str]) -> str:
    """SNMP GETNEXT of numeric OIDs (max 32) on a configured device. Read-only."""
    return await _snmp.snmp_getnext(device, oids)


@mcp.tool()
async def snmp_getbulk(device: str, oids: list[str], max_repetitions: int = 10) -> str:
    """SNMP GETBULK (v2c/v3) with max_repetitions capped at 25. Read-only."""
    return await _snmp.snmp_getbulk(device, oids, max_repetitions)


@mcp.tool()
async def snmp_walk(device: str, oid: str, max_results: int = 500) -> str:
    """Bounded SNMP WALK of one subtree (max 2000 results, 20 s); reports truncated=true when a limit hit. Read-only."""
    return await _snmp.snmp_walk(device, oid, max_results)


@mcp.tool()
async def snmp_interfaces(device: str, include: str = "*") -> str:
    """Interface inventory and status (IF-MIB/IF-X-MIB): name, alias, admin/oper status, speed, error counters.
    include is a glob on the interface name, e.g. "Te*"."""
    return await _snmp.snmp_interfaces(device, include)


@mcp.tool()
async def snmp_interface_stats(device: str, include: str = "*", sample_seconds: int = 5) -> str:
    """Traffic, utilisation and error rates from two samples sample_seconds apart (1-30). Uses 64-bit counters;
    handles rollover, reboot and ifIndex changes by reporting no rate instead of a wrong one."""
    return await _snmp.snmp_interface_stats(device, include, sample_seconds)


# ---------------- RDP / Windows (read-only) ----------------
# Targets only from servers.yaml -> rdp.targets inside rdp.allowed_networks. No login, no changes:
# RDP negotiation + verified TLS, an SSH tunnel with a .rdp file, and fixed read-only commands over SSH.
import rdp_tools as _rdp

_rdp.AUDIT = HISTORY


async def _rdp_forward(via, host, port):
    """Temporary local forward 127.0.0.1:<free> -> host:port through the SSH profile `via`."""
    conn = await srv(via).get_conn()
    listener = await conn.forward_local_port("127.0.0.1", 0, host, port)
    return "127.0.0.1", listener.get_port(), listener.close


async def _rdp_open_forward(server, local_port, host, port):
    return json.loads(await port_forward(server, local_port, host, port))


async def _rdp_ssh_run(server, command, timeout):
    try:
        s = srv(server)
    except ValueError:
        return {"error": "unknown server", "unknown_server": True}
    try:
        return await s.run(command, timeout)
    except Exception as e:
        return {"error": type(e).__name__}


@mcp.tool()
async def rdp_check(target: str) -> str:
    """Read-only RDP check of a configured target: port answers RDP, NLA enforced or not, legacy RDP security
    allowed or not, TLS certificate (verified only) and latency. No login, no credentials sent."""
    return await _rdp.rdp_check(target, forwarder=_rdp_forward)


@mcp.tool()
async def rdp_tunnel(target: str, local_port: int = 0) -> str:
    """Open a local SSH tunnel (127.0.0.1 only) to a configured RDP target through its 'via' SSH server and
    return a ready .rdp file for your own RDP client (no password in it). Close it with stop_forward."""
    return await _rdp.rdp_tunnel(target, _rdp_open_forward, TRANSPORT, local_port)


@mcp.tool()
async def windows_health(server: str) -> str:
    """Read-only Windows health over SSH (OpenSSH on Windows): OS, last boot, disks, stopped automatic services,
    pending reboot, RDP enabled / NLA / port / firewall rules. Runs one fixed PowerShell script."""
    return await _rdp.windows_health(server, _rdp_ssh_run)


@mcp.tool()
async def rdp_sessions(server: str) -> str:
    """Who is logged on to a Windows host (quser) over SSH: user, session, state, idle, logon time. Read-only."""
    return await _rdp.rdp_sessions(server, _rdp_ssh_run)


# ---------------- help: всички команди, описание (BG/EN) и шаблон ----------------
# Всеки шаблон е готов за пускане: сменяте само своите стойности.
HELP = {
    "help": ("Списък с всички команди и примери", "List all commands with examples", {}),
    "codespace_status": ("Показва всички GitHub Codespaces или един по точно име", "List GitHub Codespaces or inspect one by exact name", {"name": ""}),
    "codespace_start": ("Стартира или събужда Codespace през GitHub API", "Start/wake an existing GitHub Codespace via GitHub API", {"name": "your-codespace-name"}),
    "codespace_stop": ("Спира Codespace само след confirm=true", "Stop Codespace only after confirm=true", {"name": "your-codespace-name", "confirm": False}),
    "version_info": ("Версия, commit и команди", "Version, commit and commands", {}),
    "health_status": ("Здраве на конектора и връзките", "Connector and connection health", {}),
    "list_servers": ("Списък със сървърите", "List servers", {"tag": ""}),
    "server_get": ("Един профил, без тайни", "One profile, no secrets", {"name": "SRV1"}),
    "server_add": ("Добавя сървър", "Add a server", {"name": "web1", "hosts": ["10.0.0.5"], "user": "admin",
                   "port": 22, "tags": ["web"], "aliases": [], "password_env": ""}),
    "server_update": ("Променя сървър", "Update a server", {"name": "web1", "hosts": ["10.0.0.5", "10.0.0.6"]}),
    "server_rename": ("Преименува сървър", "Rename a server", {"name": "web1", "new_name": "web-main"}),
    "server_set_primary": ("Сменя основния адрес", "Set the primary address", {"name": "web1", "host": "10.0.0.6"}),
    "server_failover": ("Превключва към следващия адрес сега", "Switch to the next address now", {"name": "web1"}),
    "server_test": ("Проверява реална SSH връзка", "Test a real SSH connection", {"name": "web1"}),
    "credentials_set": ("Задава откъде да се вземе паролата/ключа (без тайни)", "Point to password env / key file (no secrets)",
                        {"name": "SRV1", "password_env": "SRV1_SSH_PASSWORD", "key_path": ""}),
    "credentials_rotate": ("Сменя SSH ключа (само с потвърждение)", "Rotate the SSH key (confirm required)",
                           {"name": "SRV1", "key_type": "ed25519", "confirm": False, "remove_old_key": False}),
    "sudo_password": ("sudo парола: напиши / от профила / генерирай (ти избираш дължина и символи)",
                      "sudo password: type it / from profile / generate (you choose length and charset)",
                      {"server": "SRV1", "mode": "prompt", "length": 0, "charset": "", "apply_on_server": False,
                       "password_env": ""}),
    "session_configure": ("Таймаути, опити, keepalive, паралелни сесии", "Timeouts, retries, keepalive, parallel sessions",
                          {"server": "SRV1", "connect_timeout": 10, "retries": 3, "command_timeout": 300}),
    "tailscale_status": ("Tailscale и адрес на конектора: stdio/HTTP, Serve (tailnet) или Funnel (публичен), проверен ли е, MCP шаблон без токен",
                         "Tailscale and connector address: stdio/HTTP, Serve (tailnet) or Funnel (public), verified or not, MCP template without token",
                         {"verify_reachability": False}),
    "tailscale_devices": ("Устройства в Tailscale мрежата", "Devices in the tailnet", {"online_only": False}),
    "tailscale_keys": ("Списък с auth ключове (без стойностите)", "List auth keys (no values)", {}),
    "tailscale_key_create": ("Нов auth ключ (само с потвърждение)", "New auth key (confirm required)",
                             {"description": "github-runner", "reusable": True, "ephemeral": True, "expiry_days": 90,
                              "confirm": False}),
    "tailscale_key_revoke": ("Отнема компрометиран ключ (само с потвърждение)", "Revoke a compromised key (confirm required)",
                             {"key_id": "kXXXXXXXXXX", "confirm": False}),
    "server_remove": ("Маха профил (само с потвърждение)", "Remove a profile (confirm required)",
                      {"name": "web1", "confirm": False}),
    "run_command": ("Изпълнява команда", "Run a command", {"server": "SRV1", "command": "uptime", "sudo": False}),
    "run_on_many": ("Команда на няколко сървъра", "Command on several servers",
                    {"servers": ["SRV1", "web1"], "command": "df -h"}),
    "run_on_group": ("Команда на група (таг)", "Command on a tag group", {"tag": "web", "command": "uptime"}),
    "list_snippets": ("Запазени команди", "Saved commands", {}),
    "run_snippet": ("Пуска запазена команда", "Run a saved command", {"server": "SRV1", "name": "disk"}),
    "history": ("История на командите", "Command history", {"limit": 20, "server": ""}),
    "start_job": ("Дълга задача във фонов режим", "Long background job", {"server": "SRV1", "command": "apt-get update"}),
    "job_status": ("Състояние на задача", "Job status", {"job_id": "abcd1234", "tail": 50}),
    "shell_open": ("Отваря терминал", "Open a terminal", {"server": "SRV1"}),
    "shell_send": ("Пише в терминала (use_sudo_password=true пише sudo паролата)",
                   "Type into the terminal (use_sudo_password=true types the sudo password)",
                   {"shell_id": "abcd1234", "input": "ls -la", "use_sudo_password": False}),
    "shell_close": ("Затваря терминал", "Close a terminal", {"shell_id": "abcd1234"}),
    "list_shells": ("Отворени терминали", "Open terminals", {}),
    "sftp_list": ("Файлове в папка", "List a folder", {"server": "SRV1", "path": "/var/log"}),
    "sftp_read": ("Чете файл", "Read a file", {"server": "SRV1", "path": "/etc/hostname"}),
    "sftp_write": ("Записва файл", "Write a file", {"server": "SRV1", "path": "/tmp/a.txt", "content": "hello"}),
    "sftp_upload": ("Качва файл", "Upload a file", {"server": "SRV1", "local_path": "a.txt", "remote_path": "/tmp/a.txt"}),
    "sftp_download": ("Сваля файл", "Download a file", {"server": "SRV1", "remote_path": "/tmp/a.txt", "local_path": "a.txt"}),
    "sftp_manage": ("mkdir / remove / rename", "mkdir / remove / rename",
                    {"server": "SRV1", "action": "mkdir", "path": "/tmp/new", "new_path": ""}),
    "port_forward": ("Тунел към порт", "Forward a port", {"server": "SRV1", "local_port": 15432, "remote_port": 5432}),
    "list_forwards": ("Активни тунели", "Active tunnels", {}),
    "stop_forward": ("Спира тунел", "Stop a tunnel", {"forward_id": "abcd1234"}),
    "start_watch": ("Следи грешки, сривове, влизания", "Watch errors, crashes, logins", {"server": "SRV1"}),
    "stop_watch": ("Спира следенето", "Stop watching", {"server": "SRV1"}),
    "get_events": ("Събрани събития", "Collected events", {"server": "", "limit": 50}),
    "snmp_health": ("SNMP проверка на устройство (само четене)", "SNMP liveness of a device (read-only)",
                    {"device": "realhd-switch"}),
    "snmp_device_info": ("Модел, firmware, uptime, температура ако е налична (само четене)",
                         "Model, firmware, uptime, temperature if available (read-only)", {"device": "realhd-switch"}),
    "snmp_get": ("SNMP GET по OID (само четене)", "SNMP GET by OID (read-only)",
                 {"device": "realhd-switch", "oids": ["1.3.6.1.2.1.1.5.0"]}),
    "snmp_getnext": ("SNMP GETNEXT по OID (само четене)", "SNMP GETNEXT by OID (read-only)",
                     {"device": "realhd-switch", "oids": ["1.3.6.1.2.1.1"]}),
    "snmp_getbulk": ("SNMP GETBULK (само четене)", "SNMP GETBULK (read-only)",
                     {"device": "realhd-switch", "oids": ["1.3.6.1.2.1.2.2.1.2"], "max_repetitions": 10}),
    "snmp_walk": ("Ограничен SNMP WALK на клон (само четене)", "Bounded SNMP WALK of a subtree (read-only)",
                  {"device": "realhd-switch", "oid": "1.3.6.1.2.1.2.2.1.2", "max_results": 500}),
    "snmp_interfaces": ("Портове: състояние, скорост, грешки", "Ports: status, speed, errors",
                        {"device": "realhd-switch", "include": "*"}),
    "snmp_interface_stats": ("Трафик, натоварване и грешки в секунда за портовете",
                             "Port traffic, utilisation and error rates",
                             {"device": "realhd-switch", "include": "*", "sample_seconds": 5}),
    "rdp_check": ("RDP проверка без вход: порт, NLA, стара защита, TLS сертификат",
                  "RDP check without login: port, NLA, legacy security, TLS certificate", {"target": "win-server"}),
    "rdp_tunnel": ("SSH тунел до RDP и готов .rdp файл (без парола)", "SSH tunnel to RDP and a ready .rdp file (no password)",
                   {"target": "win-server", "local_port": 0}),
    "windows_health": ("Здраве на Windows през SSH: дискове, услуги, рестарт, RDP/NLA (само четене)",
                       "Windows health over SSH: disks, services, reboot, RDP/NLA (read-only)", {"server": "WIN1"}),
    "rdp_sessions": ("Кой е влязъл в Windows (quser)", "Who is logged on to Windows (quser)", {"server": "WIN1"}),
}


@mcp.tool()
async def help(command: str = "") -> str:
    """List every command with a short Bulgarian and English description and a ready-to-run example."""
    if command:
        if command not in HELP:
            return json.dumps({"error": f"unknown command '{command}'", "known": sorted(HELP)})
        bg, en, ex = HELP[command]
        return json.dumps({"command": command, "bg": bg, "en": en, "example": ex}, ensure_ascii=False, indent=2)
    return json.dumps([{"command": k, "bg": v[0], "en": v[1], "example": v[2]} for k, v in HELP.items()],
                      ensure_ascii=False, indent=2)


# ---------------- HTTP mode (ChatGPT, remote Codex) ----------------
def _credential(headers):
    """Accept 'Authorization: Bearer <t>', 'Authorization: <t>', 'x-api-key: <t>' or 'api-key: <t>'."""
    for name in (b"authorization", b"x-api-key", b"api-key"):
        v = headers.get(name)
        if v:
            v = v.decode(errors="replace").strip()
            if v.lower().startswith("bearer "):
                v = v[7:].strip()
            return name.decode(), v
    return None, ""


def http_app(token):
    """Streamable HTTP app guarded by a token: a header (see _credential)
    or a secret URL prefix https://host/<token>/mcp (for clients without header auth, e.g. ChatGPT).
    GET /health answers 'ok' without a token. If SSH_CONNECTOR_AUTH_LOG is set, every request is logged there
    as one JSON line (header names, lengths and the result only, never header values or the token)."""
    import hmac

    app = mcp.streamable_http_app()
    prefix = f"/{token}"
    log_path = os.environ.get("SSH_CONNECTOR_AUTH_LOG")

    def log(entry):
        if log_path:
            try:
                with open(log_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass

    async def deny(send, status=401, body=b"unauthorized"):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain"),
                                (b"www-authenticate", b'Bearer realm="ssh-connector"')]})
        await send({"type": "http.response.body", "body": body})

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            method = scope.get("method", "")
            headers = dict(scope.get("headers") or [])
            if path.startswith("/sudo/") and len(path) > 40:            # еднократна страница за sudo парола
                await sudo_page(scope, receive, send)
                return
            if path == "/health":
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"ok"})
                return
            name, value = _credential(headers)
            entry = {"ts": time.strftime("%H:%M:%S"), "method": method, "path": path.replace(token, "<token>"),
                     "headers": sorted(h.decode(errors="replace") for h in headers
                                       if h not in (b"cookie",)),
                     "cred_header": name, "cred_len": len(value), "expected_len": len(token)}
            if path.startswith(prefix + "/"):
                new = path[len(prefix):]
                scope = dict(scope, path=new, raw_path=new.encode())
                entry["result"] = "ok (secret path)"
            elif value and hmac.compare_digest(value.encode(), token.encode()):
                entry["result"] = "ok (header)"
            else:
                entry["result"] = "DENIED: " + ("no credential header" if not name else "wrong token")
                log(entry)
                await deny(send)
                return
            log(entry)
        await app(scope, receive, send)

    return guarded


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("-v", "--version", action="version", version=f"ssh-connector {APP_VERSION} (commit {_git_sha()[:12]})")
    p.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    if a.http:
        token = os.environ.get("SSH_CONNECTOR_TOKEN", "")
        if len(token) < 24:
            raise SystemExit("Set SSH_CONNECTOR_TOKEN to a random secret of at least 24 characters "
                             "(e.g. python3 -c 'import secrets;print(secrets.token_urlsafe(32))')")
        import uvicorn
        TRANSPORT.clear()
        TRANSPORT.update({"mode": "http", "host": a.host, "port": a.port,
                          "note": "MCP over streamable HTTP; reachable from outside only through "
                                  "Tailscale Serve/Funnel or another proxy"})
        # access_log=False: uvicorn's access log would print the secret-path URL (/<token>/mcp) in full.
        uvicorn.run(http_app(token), host=a.host, port=a.port, access_log=False)
    else:
        mcp.run()
