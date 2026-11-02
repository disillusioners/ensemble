"""D1/D2 (reviewer-adjudicated tidier must-fix, 2026-11-02) — the
auth-echo scrub at the response seam.

``McpConfigValidationError`` messages interpolate the SUBMITTED config
(pydantic renders the offending ``input_value`` repr into the message),
so a headers-as-string config carries its
``Authorization: Bearer …`` value VERBATIM into the API response. The
validator (``daemon/mcp/config.py``) is outside the lane's surface —
the scrub lives at the RESPONSE SEAM, on every ``str(e)`` render site
in ``daemon/routers/mcp_servers.py``:

* ``POST /test-connection`` (the ``Invalid configuration: …`` message),
* ``POST /mcp-servers`` (create — the same class, one more render site
  of the identical exception),
* ``POST /configure-builtin`` (build_config re-validation),
* ``PUT /mcp-servers/{id}`` (update re-validation).

The pin contract (D2): a headers-as-string config carrying a sentinel
bearer ⇒ the response contains NEITHER the sentinel NOR ``"Bearer "``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.mcp.config import McpConfigValidationError
from daemon.routers.mcp_servers import (
    _scrub_auth_echo,
    router as mcp_servers_router,
)
from mcp.shared.exceptions import McpError
from tests.helpers.plane_workspace_fixtures import (
    make_plane_env_fixture,
    reset_plane_workspace_cache,  # noqa: F401 — the autouse fixture
)

MCP_URL = "https://mcp.example.test/plane/http/api-key/mcp"

#: A SHORT sentinel — pydantic truncates long ``input_value`` reprs in
#: the middle, and the pin must see the WHOLE sentinel to be honest.
SENTINEL = "plane_api_TESTBEARER123"

_NOW = "2026-11-02T00:00:00+00:00"


@dataclass
class _FakeRow:
    id: str = "row-custom"
    name: str = "custom-server"
    description: str | None = "custom (test double)"
    is_builtin: bool = False
    is_active: bool = True
    config: dict = field(default_factory=dict)
    config_schema: list | None = None
    config_schema_version: str = "0"
    created_at: str = _NOW
    updated_at: str | None = _NOW
    instance_metadata: dict = field(default_factory=dict)


class _FakeRepo:
    def __init__(self, rows: list[_FakeRow] | None = None):
        self.rows: dict[str, _FakeRow] = {r.name: r for r in (rows or [])}
        self.updates: list[dict] = []
        self.creates: list[dict] = []

    def get_mcp_server(self, row_id: str) -> _FakeRow | None:
        return next((r for r in self.rows.values() if r.id == row_id), None)

    def get_mcp_server_by_name(self, name: str) -> _FakeRow | None:
        return self.rows.get(name)

    def list_mcp_servers(self, **kwargs) -> list[_FakeRow]:
        return list(self.rows.values())

    def create_mcp_server(self, **kwargs) -> _FakeRow:
        self.creates.append(kwargs)
        return _FakeRow(name=kwargs["name"])

    def update_mcp_server(self, row_id, **kwargs) -> _FakeRow:
        self.updates.append({"id": row_id, **kwargs})
        return self.get_mcp_server(row_id)


class _SimpleManager:
    def __init__(self, repo: _FakeRepo):
        self._mcp_server_repository = repo
        self.is_write_paused = False
        self._mcp_service = None


@pytest.fixture
def repo() -> _FakeRepo:
    return _FakeRepo([_FakeRow()])


@pytest.fixture
def mock_app(repo, monkeypatch) -> FastAPI:
    app = FastAPI()
    api_router = APIRouter(prefix="/api")
    api_router.include_router(mcp_servers_router)
    app.include_router(api_router)
    app.state.manager = _SimpleManager(repo)
    # Never append to the repo's tracked install-audit file from a test.
    monkeypatch.setattr(
        "daemon.routers.mcp_servers._write_install_audit",
        lambda *args, **kwargs: None,
    )
    return app


@pytest.fixture
def test_client(mock_app):
    return TestClient(mock_app)


env = make_plane_env_fixture(
    (
        ("self_env", "ENSEMBLE_SELF_ENV", "dev"),
        ("db", "POSTGRES_DB", "ensemble_dev"),
        ("slug", "PLANE_MCP_WORKSPACE_SLUG", "e2e"),
        (None, "PLANE_MCP_URL", MCP_URL),
        (None, "PLANE_MCP_API_KEY", "dummy-plane-mcp-key"),
        ("mcp_enabled", "PLANE_MCP_ENABLED", None),
    ),
)


def _headers_as_string_config() -> dict:
    """The D2 shape: headers as a STRING carrying the sentinel bearer —
    the REAL validator rejects it (headers must be a dict) with the
    submitted value rendered into the message."""
    return {
        "transport": "streamable-http",
        "url": MCP_URL,
        "headers": f"Authorization: Bearer {SENTINEL}",
    }


def _assert_scrubbed(payload_text: str) -> None:
    """The D2 contract: neither the sentinel NOR the literal
    ``"Bearer "``."""
    assert SENTINEL not in payload_text, (
        "the sentinel bearer leaked into the response"
    )
    assert "Bearer " not in payload_text, (
        'the literal "Bearer " survived in the response'
    )
    assert "[REDACTED]" in payload_text  # the scrub, not an omission


# ── S1: quoted/delimiter-bearing credential runs are consumed whole ──────────


class TestScrubWidening:
    """S1 (delta hardening round 2, 2026-11-02) — the scrub is pinned
    against the ADVERSARIAL shapes it exists for, not the common case:
    quoted and delimiter-bearing (comma/colon) credentials must be
    consumed WHOLE, the ``[REDACTED]]`` double-bracket artifact must
    never appear, and the scrub must be idempotent on its own output
    (the bearer arm runs before the assign arm inside one
    ``_scrub_auth_echo`` call)."""

    def test_bearer_double_quoted_credential_is_consumed_whole(self):
        out = _scrub_auth_echo(f'Authorization: Bearer "{SENTINEL}"')
        assert SENTINEL not in out
        assert "Bearer " not in out
        assert "[REDACTED]" in out
        # the quotes of the credential do not survive either
        assert out == "Authorization: [REDACTED]"

    def test_bearer_single_quoted_credential_is_consumed_whole(self):
        out = _scrub_auth_echo(f"got 'bearer {SENTINEL}'")
        assert SENTINEL not in out
        assert "[REDACTED]" in out

    def test_assign_quoted_credential_is_consumed_whole(self):
        out = _scrub_auth_echo(f'Authorization: "{SENTINEL}"')
        assert SENTINEL not in out
        assert "Authorization: [REDACTED]" == out

    def test_quote_leaking_env_shape_is_consumed_whole(self):
        """The known-realistic quote-leak shape (a quote-leaking .env
        loader) — doubled quotes around the credential. The OLD value
        class stopped at the quote that OPENED the credential, so this
        shape matched nothing and survived verbatim."""
        out = _scrub_auth_echo(f'Authorization: ""{SENTINEL}""')
        assert SENTINEL not in out
        assert "Authorization: " in out
        assert "[REDACTED]" in out

    def test_comma_bearing_credential_is_consumed(self):
        # quoted comma run — the comma is INSIDE the credential
        out = _scrub_auth_echo(f'Bearer "{SENTINEL},x"')
        assert SENTINEL not in out
        assert "Bearer " not in out
        assert "[REDACTED]" in out
        # bare run stops AT the delimiter — the credential itself is gone
        out = _scrub_auth_echo(f"Authorization: {SENTINEL},extra")
        assert SENTINEL not in out
        assert "Authorization: [REDACTED]" in out

    def test_colon_bearing_credential_is_consumed_whole(self):
        out = _scrub_auth_echo(f"Authorization: {SENTINEL}:tail")
        assert SENTINEL not in out
        assert ":tail" not in out or out == "Authorization: [REDACTED]"
        assert "Authorization: [REDACTED]" == out

    def test_authorization_without_colon_stays_intact(self):
        """The CONTROL leg — the bare word 'authorization' in prose (no
        ``:``/``=`` assignment) is not a credential and must survive."""
        text = "the authorization header is required for this server"
        assert _scrub_auth_echo(text) == text

    def test_no_double_bracket_artifact_on_bearer_then_assign(self):
        """The delta-observed artifact: the bearer arm emits
        ``Authorization: [REDACTED]`` and the assign arm re-consumed
        ``[REDACTED`` leaving a dangling ``]`` ⇒ ``[REDACTED]]``. The
        assign bare run now excludes ``[`` so its own output is never
        re-consumed."""
        for text in (
            f"Authorization: Bearer {SENTINEL}",
            f'input_value="Authorization: Bearer {SENTINEL}"',
            f"input_value='Authorization: Basic {SENTINEL}'",
        ):
            out = _scrub_auth_echo(text)
            assert "]]" not in out, f"double-bracket artifact in {out!r}"
            assert SENTINEL not in out

    def test_scrub_is_idempotent_on_its_own_output(self):
        """Applying the scrub to already-scrubbed text is a no-op —
        required because the S3 ``McpError`` arm scrubs values that may
        already have passed a scrubed render."""
        for text in (
            f"Authorization: Bearer {SENTINEL}",
            f'Authorization: Bearer "{SENTINEL}"',
            f'Authorization: "{SENTINEL}"',
            f'Authorization: ""{SENTINEL}""',
            f"Authorization: Basic dXNlcjpwYXNz",
            f'input_value="Authorization: Bearer {SENTINEL}"',
        ):
            once = _scrub_auth_echo(text)
            assert once == _scrub_auth_echo(once), (
                f"scrub not idempotent: {once!r} -> {_scrub_auth_echo(once)!r}"
            )


# ── S3: the McpError render arm is scrubbed ──────────────────────────────────


class _FakeErrorData:
    """A minimal ``ErrorData`` double — ``McpError.__init__`` requires
    ``error.message``."""

    def __init__(self, message: str) -> None:
        self.message = message


class TestMcpErrorScrub:
    """S3 (delta hardening round 2, 2026-11-02) — the ``McpError`` arm
    of ``POST /test-connection`` was the file's last raw
    ``str(e)``-into-response seam: a remote that ECHOES submitted
    headers back in its protocol error would re-leak the credential
    through the "Server error: …" message."""

    def test_remote_echoed_bearer_in_mcp_error_is_scrubbed(
        self, env, test_client
    ):
        env(self_env="dev", slug="e2e")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock(
            side_effect=McpError(
                _FakeErrorData(
                    f"session terminated: Authorization: Bearer {SENTINEL}"
                )
            )
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": {"transport": "stdio", "command": "echo"}},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["message"].startswith("Server error:")
        _assert_scrubbed(data["message"])

    def test_remote_echoed_basic_in_mcp_error_is_scrubbed(
        self, env, test_client
    ):
        """A different echo shape — the bare scheme word without the
        ``Authorization:`` keyword (still a recognizable credential
        run)."""
        env(self_env="dev", slug="e2e")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock(
            side_effect=McpError(
                _FakeErrorData(f"invalid request: Bearer {SENTINEL}")
            )
        )

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": {"transport": "stdio", "command": "echo"}},
            )

        data = response.json()
        assert data["success"] is False
        assert SENTINEL not in data["message"]
        assert "Server error:" in data["message"]


# ── the helper itself ────────────────────────────────────────────────────────


class TestScrubHelper:
    def test_bearer_token_is_scrubbed(self):
        scrubbed = _scrub_auth_echo(
            f"Invalid MCP server config: input_value='Authorization: Bearer {SENTINEL}'"
        )
        _assert_scrubbed(scrubbed)

    def test_authorization_basic_assignment_is_scrubbed(self):
        scrubbed = _scrub_auth_echo(
            "Input should be a valid dictionary, input_value='Authorization: Basic dXNlcjpwYXNz'"
        )
        assert "dXNlcjpwYXNz" not in scrubbed
        assert "Basic" not in scrubbed
        assert "[REDACTED]" in scrubbed

    def test_clean_message_is_unchanged(self):
        clean = (
            "Invalid MCP server config: url — URL scheme 'ftp' is not "
            "allowed (see server definition docs)"
        )
        assert _scrub_auth_echo(clean) == clean

    def test_case_insensitive_scheme(self):
        scrubbed = _scrub_auth_echo(f"got 'bearer {SENTINEL}'")
        _assert_scrubbed(scrubbed)


# ── the four response-seam sites ─────────────────────────────────────────────


class TestTestConnectionScrub:
    def test_validation_error_message_is_scrubbed(self, env, test_client):
        """POST /test-connection — the ``Invalid configuration: …``
        response message."""
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = _raising_session()

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _headers_as_string_config()},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Invalid configuration" in data["message"]
        _assert_scrubbed(data["message"])


def _raising_session():
    """An AsyncMock whose call raises the REAL validator error for the
    headers-as-string shape (mirrors the transport's internal
    validation raising through the route's except arm)."""

    async def _raise(config, timeout=None):
        validate = __import__(
            "daemon.mcp.config", fromlist=["validate_mcp_server_config"]
        ).validate_mcp_server_config
        validate(config)

    return _raise


class TestCreateScrub:
    def test_create_validation_error_detail_is_scrubbed(self, env, test_client):
        """POST /mcp-servers (create) — the same exception class, one
        more ``str(e)`` render site (the fourth site)."""
        response = test_client.post(
            "/api/mcp-servers",
            json={
                "name": "leaky",
                "description": "test",
                "config": _headers_as_string_config(),
                "is_active": True,
            },
        )

        assert response.status_code == 422
        _assert_scrubbed(response.json()["detail"]["message"])


class TestUpdateScrub:
    def test_update_validation_error_detail_is_scrubbed(self, env, test_client):
        """PUT /mcp-servers/{id} — the REAL end-to-end validator path
        (the route validates the submitted config before writing)."""
        response = test_client.put(
            "/api/mcp-servers/row-custom",
            json={"config": _headers_as_string_config()},
        )

        assert response.status_code == 422
        _assert_scrubbed(response.json()["detail"]["message"])


class TestConfigureBuiltinScrub:
    def test_build_config_error_detail_is_scrubbed(self, env, test_client):
        """POST /configure-builtin — a builtin whose ``build_config``
        raises ``McpConfigValidationError`` with the sentinel in the
        message."""

        class _RaisingDefinition:
            name = "raising-builtin"
            display_name = "Raising"
            description = "raises from build_config"
            schema_version = "1"

            def get_config_schema(self):
                return []

            def build_config(self, values):
                raise McpConfigValidationError(
                    f"remote URL rejected: headers carried "
                    f"'Authorization: Bearer {SENTINEL}'"
                )

        class _SingleRegistry:
            def __init__(self, definition):
                self._definition = definition

            def get_all(self):
                return [self._definition]

            def get_by_name(self, name):
                return (
                    self._definition
                    if name == self._definition.name
                    else None
                )

        with patch(
            "daemon.routers.mcp_servers.get_registry",
            return_value=_SingleRegistry(_RaisingDefinition()),
        ):
            response = test_client.post(
                "/api/mcp-servers/configure-builtin",
                json={
                    "template_name": "raising-builtin",
                    "values": {},
                },
            )

        assert response.status_code == 422
        _assert_scrubbed(response.json()["detail"]["message"])
