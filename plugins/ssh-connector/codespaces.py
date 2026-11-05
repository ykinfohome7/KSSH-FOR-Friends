"""GitHub Codespaces lifecycle commands for SSH Connector (v1.3.3).

Uses the authenticated GitHub CLI on the MCP host, NEVER any GitHub token
supplied in tool parameters or written to source control.
The host account needs the Codespaces permission for its own Codespaces.
"""
import asyncio
import json
import re
from typing import Any

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_TIMEOUT_SECONDS = 40


def _checked_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("Invalid Codespace name (use the exact name from codespace_status)")
    return name


async def _gh_json(*args: str) -> dict[str, Any]:
    """Run a fixed GitHub CLI command safely without shell evaluation.

    Commands must be constructed by this module only; no free-form command
    parameters. Never echo stdout/stderr containing a login token.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "gh", *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("GitHub CLI (gh) is not installed on the connector host") from exc
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise RuntimeError("GitHub Codespaces request timed out") from exc
    if process.returncode != 0:
        # Keep the failure safe for chat/log display: no raw CLI output or tokens.
        raise RuntimeError(
            f"GitHub CLI request failed (exit {process.returncode}). "
            "Check gh auth status and authorization with the 'codespace' scope "
            "or Codespaces lifecycle admin permission on the target repository."
        )
    try:
        payload = json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("GitHub CLI returned a non-JSON response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub CLI returned an unexpected JSON structure")
    return payload


def _safe_summary(c: dict[str, Any]) -> dict[str, Any]:
    repository = c.get("repository") or {}
    if not isinstance(repository, dict):
        repository = {}
    machine = c.get("machine") or {}
    if not isinstance(machine, dict):
        machine = {}
    return {
        "name": c.get("name"),
        "state": c.get("state"),
        "repository": repository.get("full_name"),
        "web_url": c.get("web_url"),
        "last_used_at": c.get("last_used_at"),
        "machine": machine.get("display_name"),
    }


async def status(name: str = "") -> str:
    """List your Codespaces or inspect one; no mutation."""
    try:
        if name:
            checked = _checked_name(name)
            data = await _gh_json("api", f"/user/codespaces/{checked}")
            return json.dumps({"codespace": _safe_summary(data)}, ensure_ascii=False)
        all_codespaces = []
        for page in range(1, 11):
            data = await _gh_json("api", f"/user/codespaces?per_page=100&page={page}")
            batch = data.get("codespaces")
            if not isinstance(batch, list):
                raise RuntimeError("GitHub returned an unexpected Codespaces listing")
            all_codespaces.extend(_safe_summary(c) for c in batch if isinstance(c, dict))
            if len(batch) < 100:
                break
        return json.dumps({
            "count": len(all_codespaces), "codespaces": all_codespaces,
            "note": "Only Codespaces accessible to the authenticated GitHub CLI account.",
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


async def start(name: str) -> str:
    """Start/wake an existing Codespace using the GitHub REST API."""
    try:
        checked = _checked_name(name)
        data = await _gh_json("api", "-X", "POST", f"/user/codespaces/{checked}/start")
        return json.dumps({
            "action": "start_requested",
            "codespace": _safe_summary(data),
            "note": "Check codespace_status for the current state; startup may take time.",
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"name": name, "error": str(exc)}, ensure_ascii=False)


async def stop(name: str, confirm: bool = False) -> str:
    """Stop only the exact named Codespace after explicit confirmation."""
    try:
        checked = _checked_name(name)
        if not confirm:
            return json.dumps({
                "confirmation_required": True, "name": checked,
                "warning": "Stopping a Codespace disconnects its SSH/MCP services, including this backend if hosted there.",
            }, ensure_ascii=False)
        data = await _gh_json("api", "-X", "POST", f"/user/codespaces/{checked}/stop")
        return json.dumps({
            "action": "stop_requested", "codespace": _safe_summary(data),
            "note": "The MCP session may disconnect when stopping its own host.",
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"name": name, "error": str(exc)}, ensure_ascii=False)
