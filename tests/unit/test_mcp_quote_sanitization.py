"""Regression tests for quote-leak poisoning of MCP server configs.

Root cause (2026-09-20): ``.env`` values stored with surrounding quotes
reach ``os.environ`` verbatim when loaded by quote-leaking shell loaders
(e.g. ``export $(cat .env | xargs)`` — BSD xargs preserves the quote
characters). The plane builtin persisted a poisoned ``mcp_servers`` row
(url ``"https://..."`` — literal quotes) on 2026-08-13. At session
creation the quoted URL is scheme-less to httpx →
``httpcore.UnsupportedProtocol`` inside the MCP transport's task group →
the task group collapses and closes the read stream →
``ClientSession.initialize()`` receives ``McpError: Connection closed``
(daemon/mcp/connection_manager.py ``_open_and_track_session``).

Fix: sanitize wrapping quotes on ``url`` / ``headers`` at the config
validation seam (``daemon/mcp/config.py``) and on env reads in the plane
builtin (``daemon/mcp/builtin_servers/plane.py``), plus a schema-version
bump so the next env-available bootstrap refreshes the poisoned row.

The end-to-end harness runs in a SUBPROCESS with the real ``mcp`` SDK:
``tests/conftest.py`` replaces the whole ``mcp`` package in
``sys.modules`` with MagicMocks at collection time, so the real
streamable-HTTP transport can only be exercised outside the pytest
process. The loopback server reproduces mcp.ensem.dev's verified wire
shape (probe 2026-09-20): POST initialize → HTTP 200,
``content-type: text/event-stream``, SSE ``event: message`` frame
carrying the result, and NO ``Mcp-Session-Id`` response header
(stateless "header-http" server).

RED/GREEN proof: on the pre-fix code the subprocess harness exits
non-zero with ``McpError: Connection closed`` (the live boot/spawn
error-storm signature); on the fixed code it prints REPRO_GREEN.
"""

import os
import subprocess
import sys

from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
from daemon.mcp.config import McpStreamableHttpConfig, validate_mcp_server_config

# The exact poisoned shape persisted in the live mcp_servers row
# (2026-09-20 API dump, values quoted verbatim):
POISONED_ROW_CONFIG = {
    "transport": "streamable-http",
    "url": '"https://mcp.ensem.dev/plane/http/api-key/mcp"',
    "headers": {
        "Authorization": 'Bearer "plane_api_test_token"',
        "x-workspace-slug": '"nea"',
    },
}


class TestQuotedUrlSanitization:
    """Config-layer healing: the poisoned row shape must validate clean."""

    def test_poisoned_row_config_url_is_unwrapped(self, allow_local):
        validated = validate_mcp_server_config(dict(POISONED_ROW_CONFIG))
        assert isinstance(validated, McpStreamableHttpConfig)
        assert validated.url == "https://mcp.ensem.dev/plane/http/api-key/mcp"

    def test_poisoned_row_headers_are_unwrapped(self, allow_local):
        validated = validate_mcp_server_config(dict(POISONED_ROW_CONFIG))
        assert validated.headers is not None
        assert validated.headers["Authorization"] == "Bearer plane_api_test_token"
        assert validated.headers["x-workspace-slug"] == "nea"

    def test_single_quoted_url_is_unwrapped(self, allow_local):
        validated = validate_mcp_server_config(
            {"transport": "streamable-http", "url": "'http://localhost:8080/mcp'"}
        )
        assert validated.url == "http://localhost:8080/mcp"

    def test_clean_config_passes_through_unchanged(self, allow_local):
        clean = {
            "transport": "streamable-http",
            "url": "http://localhost:8080/mcp",
            "headers": {"Authorization": "Bearer tok", "x-other": 'a"b'},
        }
        validated = validate_mcp_server_config(dict(clean))
        assert validated.url == clean["url"]
        assert validated.headers == clean["headers"]

    def test_interior_quotes_are_preserved(self, allow_local):
        """Only wrapping quotes are stripped; legit interior quotes stay."""
        validated = validate_mcp_server_config(
            {
                "transport": "streamable-http",
                "url": 'http://localhost:8080/mcp?q="x"',
                "headers": {"x-custom": 'say "hi"'},
            }
        )
        assert validated.url == 'http://localhost:8080/mcp?q="x"'
        assert validated.headers is not None
        assert validated.headers["x-custom"] == 'say "hi"'

    def test_quoted_token_in_non_auth_header_left_alone(self, allow_local):
        """Credential-unwrap is gated to Authorization (minimal blast radius)."""
        validated = validate_mcp_server_config(
            {
                "transport": "streamable-http",
                "url": "http://localhost:8080/mcp",
                "headers": {"x-token": 'Bearer "abc"'},
            }
        )
        assert validated.headers is not None
        assert validated.headers["x-token"] == 'Bearer "abc"'

    def test_malformed_transport_still_rejected(self):
        """Sanitization must not weaken transport validation."""
        import pytest

        from daemon.mcp.config import McpConfigValidationError

        with pytest.raises(McpConfigValidationError):
            validate_mcp_server_config({"transport": "carrier-pigeon"})


class TestPlaneBuiltinEnvSanitization:
    """Write-side healing: poisoned env must produce a clean base config."""

    def test_get_base_config_sanitizes_poisoned_env(self, monkeypatch):
        monkeypatch.setenv("PLANE_MCP_URL", '"https://mcp.ensem.dev/plane/http/api-key/mcp"')
        monkeypatch.setenv("PLANE_MCP_API_KEY", '"plane_api_test_token"')
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", '"nea"')
        definition = PlaneServerDefinition()
        assert definition.is_available() is True
        config = definition.get_base_config()
        assert config["url"] == "https://mcp.ensem.dev/plane/http/api-key/mcp"
        assert config["headers"]["Authorization"] == "Bearer plane_api_test_token"
        assert config["headers"]["x-workspace-slug"] == "nea"

    def test_clean_env_unchanged(self, monkeypatch):
        monkeypatch.setenv("PLANE_MCP_URL", "https://mcp.ensem.dev/plane/http/api-key/mcp")
        monkeypatch.setenv("PLANE_MCP_API_KEY", "plane_api_test_token")
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "nea")
        config = PlaneServerDefinition().get_base_config()
        assert config["url"] == "https://mcp.ensem.dev/plane/http/api-key/mcp"
        assert config["headers"]["Authorization"] == "Bearer plane_api_test_token"
        assert config["headers"]["x-workspace-slug"] == "nea"

    def test_schema_version_bumped_for_row_refresh(self):
        """Schema drift (1→2) is the sanctioned poisoned-row refresh path."""
        assert PlaneServerDefinition().schema_version == "2"


# The end-to-end harness: loopback MCP server speaking mcp.ensem.dev's
# verified wire shape + McpConnectionManager.create_test_session with the
# poisoned row config (URL pointed at the loopback port). Written to
# tmp_path and run in a subprocess so the real SDK is exercised (the
# pytest-process conftest mocks the whole mcp package).
HARNESS = r'''
import asyncio
import json
import sys

from daemon.mcp.connection_manager import McpConnectionManager

INITIALIZE_RESULT = {
    "protocolVersion": "2025-11-25",
    "capabilities": {"tools": {"listChanged": True}},
    "serverInfo": {"name": "Plane MCP Server (header-http)", "version": "3.2.0"},
}


class LoopbackStreamableHttpServer:
    """mcp.ensem.dev wire shape: POST -> 200 text/event-stream SSE frame,
    no Mcp-Session-Id (stateless "header-http" server); GET -> 405."""

    def __init__(self):
        self._server = None
        self.port = None
        self.requests = []

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            raw_head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, ConnectionResetError):
            writer.close()
            return
        lines = raw_head.split(b"\r\n")
        method, path, _version = lines[0].decode("latin-1").split(" ", 2)
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                key, value = line.split(b":", 1)
                headers[key.decode("latin-1").lower()] = value.decode("latin-1").strip()
        length = int(headers.get("content-length", "0"))
        raw_body = await reader.readexactly(length) if length else b""
        self.requests.append(
            {"method": method, "path": path, "headers": headers, "body": raw_body}
        )

        rpc_method, rpc_id = self._parse_rpc(raw_body)
        if method == "POST" and path == "/mcp":
            if rpc_id is None:
                writer.write(
                    b"HTTP/1.1 202 Accepted\r\nconnection: close\r\ncontent-length: 0\r\n\r\n"
                )
            else:
                if rpc_method == "initialize":
                    result = INITIALIZE_RESULT
                elif rpc_method == "tools/list":
                    result = {"tools": []}
                else:
                    result = {}
                sse_body = (
                    "event: message\r\n"
                    "data: " + json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": result})
                    + "\r\n\r\n"
                ).encode()
                head = (
                    "HTTP/1.1 200 OK\r\n"
                    "content-type: text/event-stream\r\n"
                    "cache-control: no-cache, no-transform\r\n"
                    "connection: close\r\n"
                    "content-length: %d\r\n\r\n" % len(sse_body)
                ).encode()
                writer.write(head + sse_body)
        else:
            writer.write(
                b"HTTP/1.1 405 Method Not Allowed\r\nconnection: close\r\ncontent-length: 0\r\n\r\n"
            )
        await writer.drain()
        writer.close()

    @staticmethod
    def _parse_rpc(raw_body):
        try:
            payload = json.loads(raw_body)
            return payload.get("method"), payload.get("id")
        except (ValueError, TypeError):
            return None, None


async def main() -> int:
    server = LoopbackStreamableHttpServer()
    async with server:
        # The EXACT poisoned shape persisted in the live mcp_servers row
        # (2026-09-20 API dump): every string value carries literal quotes.
        poisoned = {
            "transport": "streamable-http",
            "url": '"http://127.0.0.1:%d/mcp"' % server.port,
            "headers": {
                "Authorization": 'Bearer "plane_api_test_token"',
                "x-workspace-slug": '"nea"',
            },
        }
        manager = McpConnectionManager()
        session, streams_cm = await manager.create_test_session(poisoned, timeout=10.0)
        try:
            assert server.requests, "loopback server saw no initialize POST"
            init_req = server.requests[0]
            assert init_req["method"] == "POST" and init_req["path"] == "/mcp"
            # The poisoned Authorization header must reach the wire CLEAN.
            auth = init_req["headers"].get("authorization")
            assert auth == "Bearer plane_api_test_token", "wire auth = %r" % (auth,)
            # A second round-trip proves the session is live.
            result = await session.list_tools()
            assert result is not None
        finally:
            await session.stop()
            await streams_cm.__aexit__(None, None, None)
    print("REPRO_GREEN: poisoned row config established a live MCP session")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
'''


class TestQuotedConfigEndToEndSubprocess:
    """End-to-end red/green: real SDK + loopback server + poisoned config.

    RED (pre-fix): harness exits non-zero, stderr carries
    ``McpError: Connection closed`` — the live error-storm signature.
    GREEN (post-fix): harness exits 0 and prints REPRO_GREEN.
    """

    def _run_harness(self, tmp_path) -> subprocess.CompletedProcess:
        harness = tmp_path / "plane_loopback_repro.py"
        harness.write_text(HARNESS)
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, str(harness)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=repo_root,
            env=env,
        )

    def test_poisoned_row_config_establishes_session(self, tmp_path):
        proc = self._run_harness(tmp_path)
        assert proc.returncode == 0, (
            "harness failed (pre-fix code reproduces McpError: Connection closed here):\n"
            "STDOUT:\n" + proc.stdout + "\nSTDERR:\n" + proc.stderr
        )
        assert "REPRO_GREEN" in proc.stdout
