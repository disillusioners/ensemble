"""P5d (P3-D) — ``POST /api/mcp-servers/test-connection`` consults the
resolver and the plane kill-switch (lane ``plane-pm-issue-tools`` Phase 3).

The D7/AD7 defect: the probe path bypassed the kill-switch and reported
``success=true, 177 tools`` while the switch was OFF or the workspace was
refused — the operator's natural "is Plane up?" check therefore LIED.
This pack pins the honest report for plane-shaped configs; non-plane
configs proceed through the endpoint unchanged (the scope boundary).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon.routers.mcp_servers import router as mcp_servers_router
from tests.helpers.plane_workspace_fixtures import (
    make_plane_env_fixture,
    reset_plane_workspace_cache,  # noqa: F401 — the autouse fixture
)

MCP_URL = "https://mcp.example.test/plane/http/api-key/mcp"


@pytest.fixture
def mock_app() -> FastAPI:
    app = FastAPI()
    api_router = APIRouter(prefix="/api")
    api_router.include_router(mcp_servers_router)
    app.include_router(api_router)
    return app


@pytest.fixture
def test_client(mock_app):
    return TestClient(mock_app)


env = make_plane_env_fixture(
    (
        ("self_env", "ENSEMBLE_SELF_ENV", "dev"),
        ("db", "POSTGRES_DB", "ensemble_dev"),
        ("slug", "PLANE_MCP_WORKSPACE_SLUG", "nea"),
        (None, "PLANE_MCP_URL", MCP_URL),
        (None, "PLANE_MCP_API_KEY", "dummy-plane-mcp-key"),
        ("mcp_enabled", "PLANE_MCP_ENABLED", None),
    ),
)


def _plane_config(slug: str = "nea"):
    """A plane-shaped config: the streamable-http shape with the
    x-workspace-slug header (the builtin's base config shape)."""
    return {
        "transport": "streamable-http",
        "url": MCP_URL,
        "headers": {
            "Authorization": "Bearer dummy-plane-mcp-key",
            "x-workspace-slug": slug,
        },
    }


def _case_variant_header_config(slug: str = "nea"):
    """Plane-shaped via the HEADER arm only — a NON-plane URL plus a
    case-variant header NAME (``X-Workspace-Slug``; RFC 9110 §5.1 —
    header names are case-insensitive). If the shape predicate were
    exact-case, this config would escape the guard entirely."""
    return {
        "transport": "streamable-http",
        "url": "https://other.example.test/mcp",
        "headers": {"X-Workspace-Slug": slug},
    }


class TestPlaneShapeRefused:
    def test_workspace_refusal_is_reported_not_dialed(self, env, test_client):
        """The endpoint reports the resolver's TYPED refusal instead of
        dialing (the config would 'succeed' against the wrong workspace)."""
        env(self_env="dev", slug="nea")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock()

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _plane_config()},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert (
            "workspace 'nea' is production-only; this environment resolves to dev"
            in data["message"]
        )
        # NOT dialed — no stale success path can run.
        conn_mgr.create_test_session.assert_not_awaited()

    def test_kill_switch_state_is_reported(self, env, test_client):
        """The D7/AD7 lie itself: with the switch OFF the probe must say
        so, not report a stale success."""
        env(self_env="dev", slug="e2e", mcp_enabled="false")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock()

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _plane_config()},
            )

        data = response.json()
        assert data["success"] is False
        assert "PLANE_MCP_ENABLED" in data["message"]
        assert "deliberately disabled" in data["message"]
        conn_mgr.create_test_session.assert_not_awaited()

    def test_permitted_resolution_dials_normally(self, env, test_client):
        """A permitted plane shape — the SUBMITTED slug matching the
        resolved slug (F2: the submitted header is authority-checked on
        non-live) — proceeds through the endpoint; the guard must not
        break the healthy path."""
        env(self_env="dev", slug="e2e")
        session = MagicMock()
        tools = MagicMock()
        tools.tools = []
        session.list_tools = AsyncMock(return_value=tools)
        session.stop = AsyncMock()
        streams_cm = MagicMock()
        streams_cm.__aexit__ = AsyncMock(return_value=None)
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock(return_value=(session, streams_cm))

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _plane_config(slug="e2e")},
            )

        data = response.json()
        assert data["success"] is True
        conn_mgr.create_test_session.assert_awaited_once()

    def test_live_shape_dials_normally(self, env, test_client):
        env(self_env="live", slug="nea", db="ensemble_prod")
        session = MagicMock()
        tools = MagicMock()
        tools.tools = []
        session.list_tools = AsyncMock(return_value=tools)
        session.stop = AsyncMock()
        streams_cm = MagicMock()
        streams_cm.__aexit__ = AsyncMock(return_value=None)
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock(return_value=(session, streams_cm))

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _plane_config()},
            )

        assert response.json()["success"] is True


class TestSubmittedSlugAuthority:
    """F2 (reviewer fold 2026-11-02) — P2-D's resolved-slug authority
    mirrored on the probe path: on non-live, a SUBMITTED
    ``x-workspace-slug`` that disagrees with the RESOLVED slug is a typed
    refusal BEFORE dialing (previously a permitted e2e resolution still
    dialed whatever slug the operator submitted — e.g. ``nea`` ⇒ the prod
    MCP endpoint, bounded to list_tools)."""

    def test_submitted_nea_under_e2e_resolution_is_refused_not_dialed(
        self, env, test_client
    ):
        """The F2 defect itself: dev resolves e2e (permitted), the
        submitted config names 'nea' ⇒ typed refusal, no dial."""
        env(self_env="dev", slug="e2e")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock()

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _plane_config(slug="nea")},
            )

        data = response.json()
        assert data["success"] is False
        message = data["message"]
        assert "workspace mismatch" in message
        assert "'nea'" in message and "'e2e'" in message
        assert "refused before dialing" in message
        conn_mgr.create_test_session.assert_not_awaited()

    def test_poisoned_quoted_submitted_slug_is_caught(self, env, test_client):
        """The submitted value is NORMALIZED before comparison — a quoted
        ``'nea'`` smuggles nothing past the authority check."""
        env(self_env="dev", slug="e2e")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock()

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _plane_config(slug="'nea'")},
            )

        data = response.json()
        assert data["success"] is False
        assert "workspace mismatch" in data["message"]
        conn_mgr.create_test_session.assert_not_awaited()

    def test_live_is_exempt_from_the_mismatch_check(self, env, test_client):
        """ADJ-1 live-exempt mirrored: on live ANY configured slug may be
        submitted — a mismatching slug still dials (live is operator
        authority)."""
        env(self_env="live", slug="nea", db="ensemble_prod")
        session = MagicMock()
        tools = MagicMock()
        tools.tools = []
        session.list_tools = AsyncMock(return_value=tools)
        session.stop = AsyncMock()
        streams_cm = MagicMock()
        streams_cm.__aexit__ = AsyncMock(return_value=None)
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock(return_value=(session, streams_cm))

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _plane_config(slug="e2e")},
            )

        assert response.json()["success"] is True
        conn_mgr.create_test_session.assert_awaited_once()


class TestProbeGuardHardening:
    """A1-A3 (reviewer-adjudicated tidier must-fix, 2026-11-02) — the
    probe guard's shape predicate is case-insensitive on the header
    NAME, normalizes BOTH sides of the URL comparison, and FAILS
    CLOSED on internal errors."""

    def test_case_variant_header_name_still_refuses_on_mismatch(
        self, env, test_client
    ):
        """A1(i) — a case-variant header NAME (``X-Workspace-Slug``;
        RFC 9110 §5.1 makes header names case-insensitive) claims the
        same workspace authority: the guard applies and the refusal is
        typed, not a silent dial."""
        env(self_env="dev", slug="nea")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock()

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _case_variant_header_config()},
            )

        data = response.json()
        assert data["success"] is False
        assert "production-only" in data["message"]
        conn_mgr.create_test_session.assert_not_awaited()

    def test_case_variant_header_name_honors_the_kill_switch(
        self, env, test_client
    ):
        """A1(i) — the kill-switch arm is reached through a
        case-variant header name too (the operator's explicit OFF is
        honored on every plane-shaped config)."""
        env(self_env="dev", slug="e2e", mcp_enabled="false")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock()

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _case_variant_header_config(slug="e2e")},
            )

        data = response.json()
        assert data["success"] is False
        assert "PLANE_MCP_ENABLED" in data["message"]
        assert "deliberately disabled" in data["message"]
        conn_mgr.create_test_session.assert_not_awaited()

    def test_case_variant_header_name_live_is_still_exempt(
        self, env, test_client
    ):
        """A1(i) — the live exemption is unchanged: a case-variant
        header name on live still dials (ADJ-1)."""
        env(self_env="live", slug="nea", db="ensemble_prod")
        session = MagicMock()
        tools = MagicMock()
        tools.tools = []
        session.list_tools = AsyncMock(return_value=tools)
        session.stop = AsyncMock()
        streams_cm = MagicMock()
        streams_cm.__aexit__ = AsyncMock(return_value=None)
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock(return_value=(session, streams_cm))

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _case_variant_header_config()},
            )

        assert response.json()["success"] is True
        conn_mgr.create_test_session.assert_awaited_once()

    def test_internal_error_refuses_instead_of_passing_through(
        self, env, test_client
    ):
        """A1(ii)/A3 — a raising ``normalize`` inside the guard is a
        typed REFUSAL, never a pass-through dial."""
        env(self_env="dev", slug="e2e")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock()

        with (
            patch(
                "daemon.routers.mcp_servers.get_mcp_connection_manager",
                return_value=conn_mgr,
            ),
            patch(
                "daemon.routers.mcp_servers.normalize_workspace_slug",
                side_effect=RuntimeError("planted internal error"),
            ),
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={"config": _plane_config(slug="nea")},
            )

        data = response.json()
        assert data["success"] is False
        assert "could not evaluate the workspace policy" in data["message"]
        assert "refused before dialing" in data["message"]
        conn_mgr.create_test_session.assert_not_awaited()

    def test_trailing_slash_url_env_side_still_matches_the_shape(
        self, env, test_client
    ):
        """A2(iii) — a trailing slash on the CONFIGURED
        ``PLANE_MCP_URL`` previously UN-matched the shape (the probe
        dialed past a kill-switch OFF); both sides are normalized
        now."""
        env(self_env="dev", slug="e2e", mcp_enabled="false")
        # The fixture pins PLANE_MCP_URL as a FIXED variable; re-pin it
        # with a trailing slash for this leg only.
        with patch.dict(os.environ, {"PLANE_MCP_URL": MCP_URL + "/"}):
            conn_mgr = MagicMock()
            conn_mgr.create_test_session = AsyncMock()

            with patch(
                "daemon.routers.mcp_servers.get_mcp_connection_manager",
                return_value=conn_mgr,
            ):
                response = test_client.post(
                    "/api/mcp-servers/test-connection",
                    json={
                        "config": {
                            "transport": "streamable-http",
                            "url": MCP_URL,
                        }
                    },
                )

        data = response.json()
        assert data["success"] is False
        assert "PLANE_MCP_ENABLED" in data["message"]
        conn_mgr.create_test_session.assert_not_awaited()

    def test_trailing_slash_url_config_side_still_matches_the_shape(
        self, env, test_client
    ):
        """A2(iii) — the mirror leg: the trailing slash on the
        SUBMITTED url."""
        env(self_env="dev", slug="e2e", mcp_enabled="false")
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock()

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={
                    "config": {
                        "transport": "streamable-http",
                        "url": MCP_URL + "/",
                    }
                },
            )

        data = response.json()
        assert data["success"] is False
        assert "PLANE_MCP_ENABLED" in data["message"]
        conn_mgr.create_test_session.assert_not_awaited()


class TestScopeBoundary:
    def test_non_plane_config_is_untouched_by_the_guard(
        self, env, test_client
    ):
        """The scope boundary: a NON-plane config proceeds even under a
        workspace refusal — the guard is plane-relevant reporting only."""
        env(self_env="dev", slug="nea")
        session = MagicMock()
        tools = MagicMock()
        tools.tools = []
        session.list_tools = AsyncMock(return_value=tools)
        session.stop = AsyncMock()
        streams_cm = MagicMock()
        streams_cm.__aexit__ = AsyncMock(return_value=None)
        conn_mgr = MagicMock()
        conn_mgr.create_test_session = AsyncMock(return_value=(session, streams_cm))

        with patch(
            "daemon.routers.mcp_servers.get_mcp_connection_manager",
            return_value=conn_mgr,
        ):
            response = test_client.post(
                "/api/mcp-servers/test-connection",
                json={
                    "config": {
                        "transport": "streamable-http",
                        "url": "https://other.example.test/mcp",
                    }
                },
            )

        assert response.json()["success"] is True
        conn_mgr.create_test_session.assert_awaited_once()
