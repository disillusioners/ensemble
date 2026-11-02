"""P5d (P3-D) — ``POST /api/mcp-servers/test-connection`` consults the
resolver and the plane kill-switch (lane ``plane-pm-issue-tools`` Phase 3).

The D7/AD7 defect: the probe path bypassed the kill-switch and reported
``success=true, 177 tools`` while the switch was OFF or the workspace was
refused — the operator's natural "is Plane up?" check therefore LIED.
This pack pins the honest report for plane-shaped configs; non-plane
configs proceed through the endpoint unchanged (the scope boundary).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon.plane_workspace import reset_resolution_cache
from daemon.routers.mcp_servers import router as mcp_servers_router

MCP_URL = "https://mcp.example.test/plane/http/api-key/mcp"


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_resolution_cache()
    yield
    reset_resolution_cache()


@pytest.fixture
def mock_app():
    app = FastAPI()
    api_router = APIRouter(prefix="/api")
    api_router.include_router(mcp_servers_router)
    app.include_router(api_router)
    return app


@pytest.fixture
def test_client(mock_app):
    return TestClient(mock_app)


@pytest.fixture
def env(monkeypatch):
    def _set(
        *,
        self_env="dev",
        slug="nea",
        db="ensemble_dev",
        mcp_enabled: str | None = None,
    ):
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", self_env)
        monkeypatch.setenv("POSTGRES_DB", db)
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", slug)
        monkeypatch.setenv("PLANE_MCP_URL", MCP_URL)
        monkeypatch.setenv("PLANE_MCP_API_KEY", "dummy-plane-mcp-key")
        if mcp_enabled is None:
            monkeypatch.delenv("PLANE_MCP_ENABLED", raising=False)
        else:
            monkeypatch.setenv("PLANE_MCP_ENABLED", mcp_enabled)
        reset_resolution_cache()

    return _set


def _plane_config():
    """A plane-shaped config: the streamable-http shape with the
    x-workspace-slug header (the builtin's base config shape)."""
    return {
        "transport": "streamable-http",
        "url": MCP_URL,
        "headers": {
            "Authorization": "Bearer dummy-plane-mcp-key",
            "x-workspace-slug": "nea",
        },
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
        """A permitted plane shape proceeds through the endpoint — the
        guard must not break the healthy path."""
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
                json={"config": _plane_config()},
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
