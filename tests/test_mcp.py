import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import TESTDATA
from unripe import mcp

MODERN = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}


@pytest.fixture
def server(fake, tmp_path, monkeypatch):
    """A server running in an empty project, with a fake registry on the real clock."""
    monkeypatch.chdir(tmp_path)
    fake.base = datetime.now(timezone.utc)
    return mcp.Server()


def ask(server, method, params=None, ident=1):
    message = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def call(server, tool, arguments, modern=False):
    params = {"name": tool, "arguments": arguments}
    if modern:
        params["_meta"] = MODERN
    return ask(server, "tools/call", params)["result"]


def test_the_older_handshake(server):
    answer = ask(server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert answer["result"]["protocolVersion"] == "2025-06-18"
    assert answer["result"]["capabilities"] == {"tools": {}}
    assert answer["result"]["serverInfo"]["name"] == "unripe"
    # a version this server does not know gets the newest older one it does
    assert ask(server, "initialize", {"protocolVersion": "2024-01-01"})["result"]["protocolVersion"] == "2025-11-25"
    assert "resultType" not in ask(server, "tools/list")["result"]


def test_the_stateless_protocol(server):
    found = ask(server, "server/discover", {"_meta": MODERN})["result"]
    assert found["resultType"] == "complete" and found["supportedVersions"] == ["2026-07-28"]
    assert found["_meta"]["io.modelcontextprotocol/serverInfo"]["version"]
    tools = ask(server, "tools/list", {"_meta": MODERN})["result"]
    assert tools["resultType"] == "complete"
    assert [tool["name"] for tool in tools["tools"]] == ["check_package", "check_lockfiles", "cooldown_settings"]
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools["tools"])
    refused = ask(server, "tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "1900-01-01"}})
    assert refused["error"] == {"code": -32022, "message": "Unsupported protocol version", "data": {"supported": ["2026-07-28"], "requested": "1900-01-01"}}


def test_protocol_errors(server):
    assert ask(server, "nope")["error"]["code"] == -32601
    assert ask(server, "tools/call", {"name": "rm_rf", "arguments": {}})["error"]["code"] == -32602
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600
    assert ask(server, "ping")["result"] == {}


def test_check_package_picks_the_newest_old_enough_version(server, fake):
    fake.add("npm", "lib", "2.0.0", timedelta(hours=5))
    fake.add("npm", "lib", "1.9.0", timedelta(days=20))
    fake.add("npm", "lib", "1.8.0", timedelta(days=60))
    result = call(server, "check_package", {"ecosystem": "npm", "name": "lib"}, modern=True)
    assert result["resultType"] == "complete" and result["isError"] is False
    assert result["structuredContent"]["newest_old_enough"]["version"] == "1.9.0"
    assert result["content"][0]["text"].startswith("Use npm lib 1.9.0")
    assert [r["old_enough"] for r in result["structuredContent"]["recent"]] == [False, True, True]


def test_check_package_one_version(server, fake):
    fake.add("npm", "lib", "1.9.1", timedelta(hours=5))
    fake.add("npm", "lib", "1.9.0", timedelta(days=20))
    result = call(server, "check_package", {"ecosystem": "npm", "name": "lib", "version": "1.9.1"})
    data = result["structuredContent"]
    assert data["state"] == "unripe" and data["old_enough"] is False
    assert data["use_instead"]["version"] == "1.9.0"
    assert "Use 1.9.0 instead" in result["content"][0]["text"]
    assert call(server, "check_package", {"ecosystem": "npm", "name": "lib", "version": "1.9.0"})["structuredContent"]["old_enough"] is True


def test_check_package_unknowns_and_mistakes_are_tool_errors(server, fake):
    missing = call(server, "check_package", {"ecosystem": "npm", "name": "ghost", "version": "1.0.0"})
    assert missing["structuredContent"]["state"] == "unknown"
    assert "Do not treat it as old enough" in missing["content"][0]["text"]
    assert call(server, "check_package", {"ecosystem": "npm", "name": "ghost"})["isError"] is True
    assert call(server, "check_package", {"ecosystem": "cobol", "name": "x"})["isError"] is True
    assert call(server, "check_package", {"ecosystem": "go", "name": "golang.org/x/net"})["isError"] is True
    assert call(server, "check_package", {"ecosystem": "npm", "name": "lib", "min_age": "soon"})["isError"] is True


def test_check_package_follows_the_project_policy(server, fake, tmp_path):
    (tmp_path / ".unripe.toml").write_text('min-age = "30d"\n')
    fake.add("npm", "lib", "1.9.0", timedelta(days=20))
    fake.add("npm", "lib", "1.8.0", timedelta(days=60))
    assert call(server, "check_package", {"ecosystem": "npm", "name": "lib"})["structuredContent"]["newest_old_enough"]["version"] == "1.8.0"
    assert call(server, "check_package", {"ecosystem": "npm", "name": "lib", "min_age": "1d"})["structuredContent"]["newest_old_enough"]["version"] == "1.9.0"


def test_check_lockfiles(server, fake, tmp_path):
    (tmp_path / "Cargo.lock").write_text((TESTDATA / "cargo" / "Cargo.lock").read_text())
    from unripe import lockfiles

    for package in lockfiles.read((tmp_path / "Cargo.lock").read_text(), "Cargo.lock"):
        fake.add(package.ecosystem, package.name, package.version, timedelta(days=100))
    first = lockfiles.read((tmp_path / "Cargo.lock").read_text(), "Cargo.lock")[0]
    fake.add("cargo", first.name, first.version, timedelta(hours=1))
    result = call(server, "check_lockfiles", {"path": str(tmp_path)})
    data = result["structuredContent"]
    assert data["result"] == "fail" and data["summary"]["unripe"] == 1
    assert [f["name"] for f in data["findings"]] == [first.name]
    assert f"unripe: cargo {first.name} {first.version}" in result["content"][0]["text"]
    assert call(server, "check_lockfiles", {"path": str(tmp_path / "nowhere")})["isError"] is True


def test_cooldown_settings(server, tmp_path):
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "pnpm-workspace.yaml").write_text("minimumReleaseAge: 4320\n")
    data = call(server, "cooldown_settings", {"path": str(tmp_path)})["structuredContent"]
    assert data["native"] == [{"tool": "pnpm", "file": "pnpm-workspace.yaml", "setting": "minimumReleaseAge", "value": "4320", "age": "3d"}]
    assert data["could_add"][0]["tool"] == "npm"


def test_stdio_framing(server, fake):
    fake.add("npm", "lib", "1.0.0", timedelta(days=30))
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "check_package", "arguments": {"ecosystem": "npm", "name": "lib"}}},
    ]
    stdin = io.BytesIO(b"".join(json.dumps(line).encode() + b"\n" for line in lines) + b"not json\n\n")
    stdout = io.BytesIO()
    assert mcp.serve(stdin, stdout) == 0
    answers = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [a.get("id") for a in answers] == [1, 2, None]
    assert answers[1]["result"]["structuredContent"]["newest_old_enough"]["version"] == "1.0.0"
    assert answers[2]["error"]["code"] == -32700
    # every message is one line of JSON and nothing else reaches stdout
    assert all(line.startswith(b"{") for line in stdout.getvalue().splitlines())
