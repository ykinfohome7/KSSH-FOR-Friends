"""Offline, non-destructive tests of SSH Connector v1.3.3 Codespaces lifecycle tools."""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plugins" / "ssh-connector"))
import codespaces


async def main():
    calls = []

    async def fake_gh(*args):
        calls.append(args)
        if args[0] != "api":
            raise AssertionError("unexpected gh command")
        route = args[-1]
        if route == "/user/codespaces?per_page=100&page=1":
            return {"codespaces": [{
                "name": "demo-123", "state": "Shutdown",
                "repository": {"full_name": "owner/repo"},
                "web_url": "https://demo-123.github.dev",
            }]}
        if route == "/user/codespaces/demo-123":
            return {"name": "demo-123", "state": "Shutdown",
                    "repository": {"full_name": "owner/repo"}}
        if route == "/user/codespaces/demo-123/start":
            return {"name": "demo-123", "state": "Starting",
                    "repository": {"full_name": "owner/repo"}}
        if route == "/user/codespaces/demo-123/stop":
            return {"name": "demo-123", "state": "Stopping",
                    "repository": {"full_name": "owner/repo"}}
        raise AssertionError("unexpected API path: " + route)

    codespaces._gh_json = fake_gh

    many = json.loads(await codespaces.status())
    assert many["count"] == 1
    assert many["codespaces"][0]["repository"] == "owner/repo"

    one = json.loads(await codespaces.status("demo-123"))
    assert one["codespace"]["name"] == "demo-123"

    start = json.loads(await codespaces.start("demo-123"))
    assert start["action"] == "start_requested"
    assert ("api", "-X", "POST", "/user/codespaces/demo-123/start") in calls

    before_stop = len(calls)
    refused = json.loads(await codespaces.stop("demo-123"))
    assert refused["confirmation_required"] is True
    assert len(calls) == before_stop, "stop without confirm must not call the API"

    stopped = json.loads(await codespaces.stop("demo-123", True))
    assert stopped["action"] == "stop_requested"
    assert ("api", "-X", "POST", "/user/codespaces/demo-123/stop") in calls

    before_bad = len(calls)
    for bad in ("../secrets", "owner/repo", "; rm -rf /", "", " spaces "):
        res = json.loads(await codespaces.start(bad))
        assert "error" in res, res
    assert len(calls) == before_bad, "invalid names must never reach GitHub CLI"

    assert "error" in json.loads(await codespaces.status("../bad"))
    print("Codespaces v1.3.3 offline lifecycle tests PASS (no GitHub API calls)")


if __name__ == "__main__":
    asyncio.run(main())
