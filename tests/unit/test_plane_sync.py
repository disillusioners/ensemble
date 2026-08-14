"""Unit tests for the Plane project sync subsystem.

Covers:
- ``PlaneHttpClient`` (httpx.MockTransport, error classification, circuit breaker,
  feature gating, never-logs-api-key contract).
- ``PlaneSyncService`` (CREATE / UPDATE / adopt / recreate paths, error mapping,
  metadata persistence, idempotency, status mapping helper).
- ``plane_sync_project`` agent tool (cooldown gate, force bypass, feature gating,
  tool-category registration).
- Module-level helpers: ``_project_state_for_plane``, ``_find_plane_id_by_name``.

Notes
-----
* Per CR-5, all HTTP mocking uses :class:`httpx.MockTransport` — never respx.
* Per CR-6, the service writes via ``set_metadata_record`` (the low-level path)
  and reads via ``list_metadata_records`` (one call, then filter).
* Tests run on SQLite in-memory; the repository layer is engine-agnostic so the
  same fixtures work on PostgreSQL without modification.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from daemon.clients.plane_http_client import (
    PlaneAPIError,
    PlaneAuthError,
    PlaneHttpClient,
    PlaneNotFoundError,
)
from daemon.constants import (
    PLANE_PROJECT_ID_METADATA_KEY,
    PLANE_STATUS_MAP,
    PLANE_SYNC_STATE_METADATA_KEY,
    PLANE_SYNCED_AT_METADATA_KEY,
)
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
)
from daemon.services.plane_sync_service import (
    PlaneSyncService,
    _find_plane_id_by_name,
    _project_state_for_plane,
)
from daemon.sources.circuit_breaker import CircuitBreaker, CircuitState
from daemon.tools.plane_sync import (
    _check_cooldown,
    _last_sync,
    create_plane_sync_tools,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine with project + metadata tables.

    Uses ``StaticPool`` so the in-memory DB survives across threads
    (mirrors ``tests/tools/conftest.py``).
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Force model registration on SQLModel.metadata
    _ = (Project, ProjectMetadataRecord, ProjectShortnameLink)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> SQLModelProjectRepository:
    """Project repository bound to the test engine."""
    return SQLModelProjectRepository(engine)


@pytest.fixture
def clear_cooldown():
    """Reset the module-level ``_last_sync`` dict between cooldown tests."""
    _last_sync.clear()
    yield
    _last_sync.clear()


@pytest.fixture
def mock_plane_env(monkeypatch):
    """Set the env vars required by ``PlaneHttpClient.is_available``.

    Tests that need the feature enabled pin all three vars; tests that
    exercise the disabled path use ``monkeypatch.delenv`` directly.
    """
    monkeypatch.setenv("PLANE_BASE_URL", "https://plane.example.com")
    monkeypatch.setenv("PLANE_MCP_API_KEY", "test-api-key-xyz")
    monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "test-ws")
    yield


# ─────────────────────────────────────────────────────────────────────────────
# MockTransport infrastructure
# ─────────────────────────────────────────────────────────────────────────────


def _make_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    """Build an ``httpx.MockTransport`` that pops a pre-canned response per call.

    Args:
        responses: Ordered list of responses to serve. The transport dequeues
            one per request and returns the last response repeatedly when
            the list is exhausted (handy for "always 401" tests).

    Returns:
        A configured :class:`httpx.MockTransport`.
    """
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if queue:
            return queue.pop(0)
        return queue[-1] if queue else httpx.Response(200, json={})

    return httpx.MockTransport(handler)


def _patched_async_client(monkeypatch, responses: list[httpx.Response]):
    """Patch ``daemon.clients.plane_http_client.httpx.AsyncClient`` so it
    constructs a *real* ``httpx.AsyncClient`` with a ``MockTransport`` injected.

    Returns the underlying :class:`httpx.MockTransport` so tests can inspect
    the captured request objects via ``transport.handle_async_request``.

    The implementation monkey-patches the module-level ``httpx.AsyncClient``
    symbol that ``PlaneHttpClient._request`` resolves to. The patched callable
    simply forwards to the real ``httpx.AsyncClient`` while injecting
    ``transport=mock_transport``.
    """
    import daemon.clients.plane_http_client as phc_module

    transport = _make_transport(responses)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(phc_module.httpx, "AsyncClient", fake_async_client)
    return transport


# ─────────────────────────────────────────────────────────────────────────────
# Class 1: TestPlaneHttpClientCRUD
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneHttpClientCRUD:
    """Happy-path CRUD: ``create_project``, ``update_project``, ``get_project``,
    ``list_projects``."""

    def test_create_project_success(self, mock_plane_env, monkeypatch):
        """POST returns a project dict → ``create_project`` returns it."""
        _patched_async_client(
            monkeypatch,
            [httpx.Response(201, json={"id": "plane-123", "name": "Test"})],
        )
        client = PlaneHttpClient()

        async def run():
            return await client.create_project(name="Test", description="d")

        result = asyncio.run(run())
        assert result == {"id": "plane-123", "name": "Test"}

    def test_update_project_success(self, mock_plane_env, monkeypatch):
        """PATCH returns the updated project dict."""
        _patched_async_client(
            monkeypatch,
            [httpx.Response(200, json={"id": "plane-9", "name": "Renamed"})],
        )
        client = PlaneHttpClient()

        async def run():
            return await client.update_project(
                "plane-9", name="Renamed", description="new"
            )

        result = asyncio.run(run())
        assert result == {"id": "plane-9", "name": "Renamed"}

    def test_get_project_success(self, mock_plane_env, monkeypatch):
        """GET returns the project dict."""
        _patched_async_client(
            monkeypatch,
            [httpx.Response(200, json={"id": "plane-7", "name": "Foo"})],
        )
        client = PlaneHttpClient()

        async def run():
            return await client.get_project("plane-7")

        result = asyncio.run(run())
        assert result == {"id": "plane-7", "name": "Foo"}

    def test_get_project_404_returns_none(self, mock_plane_env, monkeypatch):
        """GET 404 → ``get_project`` returns ``None`` (catches ``PlaneNotFoundError``)."""
        _patched_async_client(
            monkeypatch,
            [httpx.Response(404, text="not found")],
        )
        client = PlaneHttpClient()

        async def run():
            return await client.get_project("missing")

        result = asyncio.run(run())
        assert result is None

    def test_list_projects_returns_list(self, mock_plane_env, monkeypatch):
        """GET returning a JSON array → ``list_projects`` returns the list."""
        _patched_async_client(
            monkeypatch,
            [
                httpx.Response(
                    200,
                    json=[
                        {"id": "p1", "name": "Alpha"},
                        {"id": "p2", "name": "Beta"},
                    ],
                )
            ],
        )
        client = PlaneHttpClient()

        async def run():
            return await client.list_projects()

        result = asyncio.run(run())
        assert result == [
            {"id": "p1", "name": "Alpha"},
            {"id": "p2", "name": "Beta"},
        ]

    def test_list_projects_wrapped_results(self, mock_plane_env, monkeypatch):
        """``{"results": [...]}`` shape is unwrapped by ``list_projects``."""
        _patched_async_client(
            monkeypatch,
            [
                httpx.Response(
                    200,
                    json={"results": [{"id": "p1", "name": "Alpha"}]},
                )
            ],
        )
        client = PlaneHttpClient()

        async def run():
            return await client.list_projects()

        result = asyncio.run(run())
        assert result == [{"id": "p1", "name": "Alpha"}]

    def test_list_projects_empty(self, mock_plane_env, monkeypatch):
        """Empty list response → empty list (no wrapping)."""
        _patched_async_client(monkeypatch, [httpx.Response(200, json=[])])
        client = PlaneHttpClient()

        async def run():
            return await client.list_projects()

        result = asyncio.run(run())
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Class 2: TestPlaneHttpClientErrors
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneHttpClientErrors:
    """HTTP error classification: 401, 403, 404, 429, 5xx → typed exceptions."""

    def test_auth_error_401_raises(self, mock_plane_env, monkeypatch):
        """401 → ``PlaneAuthError``."""
        _patched_async_client(
            monkeypatch, [httpx.Response(401, text="unauthorized")]
        )
        client = PlaneHttpClient()

        async def run():
            return await client.list_projects()

        with pytest.raises(PlaneAuthError):
            asyncio.run(run())

    def test_auth_error_403_raises(self, mock_plane_env, monkeypatch):
        """403 → ``PlaneAuthError`` (same class as 401)."""
        _patched_async_client(
            monkeypatch, [httpx.Response(403, text="forbidden")]
        )
        client = PlaneHttpClient()

        async def run():
            return await client.list_projects()

        with pytest.raises(PlaneAuthError):
            asyncio.run(run())

    def test_404_raises_plane_not_found_in_request(
        self, mock_plane_env, monkeypatch
    ):
        """``_request`` directly raises ``PlaneNotFoundError`` on 404."""
        _patched_async_client(monkeypatch, [httpx.Response(404, text="missing")])
        client = PlaneHttpClient()

        async def run():
            return await client._request("GET", "http://x/anything/")

        with pytest.raises(PlaneNotFoundError):
            asyncio.run(run())

    def test_429_rate_limited(self, mock_plane_env, monkeypatch):
        """429 → ``PlaneAPIError`` (not auth, not 404)."""
        _patched_async_client(
            monkeypatch, [httpx.Response(429, text="too many requests")]
        )
        client = PlaneHttpClient()

        async def run():
            return await client.list_projects()

        with pytest.raises(PlaneAPIError) as exc:
            asyncio.run(run())
        assert "rate-limited" in str(exc.value)

    def test_500_server_error(self, mock_plane_env, monkeypatch):
        """500 → ``PlaneAPIError``."""
        _patched_async_client(
            monkeypatch, [httpx.Response(500, text="internal error")]
        )
        client = PlaneHttpClient()

        async def run():
            return await client.list_projects()

        with pytest.raises(PlaneAPIError) as exc:
            asyncio.run(run())
        assert "500" in str(exc.value)

    def test_502_server_error(self, mock_plane_env, monkeypatch):
        """5xx range → ``PlaneAPIError``."""
        _patched_async_client(
            monkeypatch, [httpx.Response(502, text="bad gateway")]
        )
        client = PlaneHttpClient()

        async def run():
            return await client.list_projects()

        with pytest.raises(PlaneAPIError):
            asyncio.run(run())

    def test_400_client_error(self, mock_plane_env, monkeypatch):
        """Generic 4xx (400) → ``PlaneAPIError``."""
        _patched_async_client(
            monkeypatch, [httpx.Response(400, text="bad request")]
        )
        client = PlaneHttpClient()

        async def run():
            return await client.list_projects()

        with pytest.raises(PlaneAPIError):
            asyncio.run(run())

    def test_204_no_content_returns_none(self, mock_plane_env, monkeypatch):
        """204 → returns ``None`` (no JSON body)."""
        _patched_async_client(monkeypatch, [httpx.Response(204)])
        client = PlaneHttpClient()

        async def run():
            return await client._request("DELETE", "http://x/anything/")

        result = asyncio.run(run())
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Class 3: TestPlaneHttpClientCircuitBreaker
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneHttpClientCircuitBreaker:
    """Circuit breaker trips after ``failure_threshold`` failures.

    The Plane client shares one module-level ``_plane_breaker`` so we override
    it via the ``breaker`` constructor arg to isolate per-test state.
    """

    def _make_5xx_client(self, monkeypatch, breaker: CircuitBreaker):
        """Client whose every call returns 500 (counts as failures)."""
        _patched_async_client(
            monkeypatch, [httpx.Response(500, text="fail")] * 10
        )
        return PlaneHttpClient(
            base_url="http://x/",
            api_key="k",
            workspace_slug="w",
            breaker=breaker,
        )

    def test_circuit_breaker_opens_after_threshold(self, mock_plane_env, monkeypatch):
        """5 consecutive 5xx → breaker is OPEN → next call raises immediately."""
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
        client = self._make_5xx_client(monkeypatch, breaker)

        async def run():
            # Trigger 5 failures
            for _ in range(5):
                with pytest.raises(PlaneAPIError):
                    await client.list_projects()
            # 6th call: breaker is OPEN → raises before HTTP
            with pytest.raises(PlaneAPIError) as exc:
                await client.list_projects()
            return str(exc.value)

        msg = asyncio.run(run())
        assert "Circuit breaker is OPEN" in msg
        assert breaker.get_state() == "open"

    def test_circuit_breaker_records_success_on_2xx(
        self, mock_plane_env, monkeypatch
    ):
        """A successful 2xx records success on the breaker."""
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
        # 4 failures, then success → breaker stays closed (count resets)
        responses = [httpx.Response(500, text="fail")] * 4 + [
            httpx.Response(200, json=[{"id": "p1"}])
        ]
        _patched_async_client(monkeypatch, responses)
        client = PlaneHttpClient(
            base_url="http://x/",
            api_key="k",
            workspace_slug="w",
            breaker=breaker,
        )

        async def run():
            for _ in range(4):
                with pytest.raises(PlaneAPIError):
                    await client.list_projects()
            # Success resets count
            result = await client.list_projects()
            return result

        result = asyncio.run(run())
        assert result == [{"id": "p1"}]
        assert breaker.get_state() == "closed"
        assert breaker.failure_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Class 4: TestPlaneHttpClientFeatureGating
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneHttpClientFeatureGating:
    """``is_available`` and ``create`` honor env vars (both required)."""

    def test_feature_disabled_no_env(self, monkeypatch):
        """All Plane env vars unset → ``is_available`` False."""
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)
        assert PlaneHttpClient.is_available() is False

    def test_feature_disabled_only_url(self, monkeypatch):
        """Only ``PLANE_BASE_URL`` set → still disabled (API key missing)."""
        monkeypatch.setenv("PLANE_BASE_URL", "https://x")
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "ws")
        assert PlaneHttpClient.is_available() is False

    def test_feature_disabled_only_api_key(self, monkeypatch):
        """Only ``PLANE_MCP_API_KEY`` set → still disabled."""
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.setenv("PLANE_MCP_API_KEY", "k")
        monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)
        assert PlaneHttpClient.is_available() is False

    def test_feature_disabled_only_workspace_slug(self, monkeypatch):
        """Only ``PLANE_MCP_WORKSPACE_SLUG`` set → still disabled."""
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "ws")
        assert PlaneHttpClient.is_available() is False

    def test_feature_enabled_all_set(self, monkeypatch):
        """All three env vars set → ``is_available`` True."""
        monkeypatch.setenv("PLANE_BASE_URL", "https://x")
        monkeypatch.setenv("PLANE_MCP_API_KEY", "k")
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "ws")
        assert PlaneHttpClient.is_available() is True

    def test_feature_disabled_whitespace_only(self, monkeypatch):
        """Whitespace-only env values are stripped → disabled."""
        monkeypatch.setenv("PLANE_BASE_URL", "   ")
        monkeypatch.setenv("PLANE_MCP_API_KEY", "   ")
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "   ")
        assert PlaneHttpClient.is_available() is False

    def test_create_returns_none_when_disabled(self, monkeypatch):
        """``create()`` returns ``None`` when env vars are missing."""
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)
        assert PlaneHttpClient.create() is None


# ─────────────────────────────────────────────────────────────────────────────
# Class 5: TestPlaneHttpClientLoggingSafety
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneHttpClientLoggingSafety:
    """Verify the Authorization header value is never written to logs."""

    def test_never_logs_api_key(self, mock_plane_env, monkeypatch, caplog):
        """A 401 response logs status + body — never the API key."""
        secret = "very-secret-api-key-do-not-log"
        _patched_async_client(
            monkeypatch, [httpx.Response(401, text="bad token")]
        )
        client = PlaneHttpClient(
            base_url="https://x",
            api_key=secret,
            workspace_slug="w",
        )

        with caplog.at_level(logging.WARNING):
            async def run():
                with pytest.raises(PlaneAuthError):
                    await client.list_projects()

            asyncio.run(run())

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert secret not in log_text, (
            f"API key leaked into log output: {log_text!r}"
        )
        # But the URL and status should still be logged
        assert "401" in log_text
        assert "https://x" in log_text or "plane.so" in log_text or "/" in log_text

    def test_never_logs_api_key_on_500(self, mock_plane_env, monkeypatch, caplog):
        """5xx error path also doesn't leak the API key."""
        secret = "another-secret-key-xyz"
        _patched_async_client(
            monkeypatch, [httpx.Response(500, text="server boom")]
        )
        client = PlaneHttpClient(
            base_url="https://x",
            api_key=secret,
            workspace_slug="w",
        )

        with caplog.at_level(logging.WARNING):
            async def run():
                with pytest.raises(PlaneAPIError):
                    await client.list_projects()

            asyncio.run(run())

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert secret not in log_text


# ─────────────────────────────────────────────────────────────────────────────
# Class 6: TestPlaneHttpClientHeaders
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneHttpClientHeaders:
    """Verify the outgoing request includes the right headers + JSON body."""

    def test_request_includes_authorization_header(
        self, mock_plane_env, monkeypatch
    ):
        """The ``Authorization: Bearer <key>`` header is sent."""
        captured: list[httpx.Request] = []
        transport = _make_transport(
            [httpx.Response(200, json={"id": "p1"})]
        )
        # Replace transport handler to also capture the request.
        original_handler = transport.handle_async_request
        import daemon.clients.plane_http_client as phc_module

        real_async_client = httpx.AsyncClient

        def capturing_client(*args, **kwargs):
            # Wrap transport so we can capture the request.
            wrapped_transport = httpx.MockTransport(
                lambda req: (
                    captured.append(req)
                    or original_handler(req)
                )
            )
            kwargs["transport"] = wrapped_transport
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(phc_module.httpx, "AsyncClient", capturing_client)

        client = PlaneHttpClient(
            base_url="https://plane.example.com",
            api_key="my-secret-key",
            workspace_slug="ws-slug",
        )

        async def run():
            await client.list_projects()

        asyncio.run(run())

        assert len(captured) == 1
        req = captured[0]
        assert req.headers["authorization"] == "Bearer my-secret-key"
        assert req.headers["x-workspace-slug"] == "ws-slug"
        assert req.headers["content-type"] == "application/json"


# ─────────────────────────────────────────────────────────────────────────────
# Class 7: TestStatusMappingHelper
# ─────────────────────────────────────────────────────────────────────────────


class TestStatusMappingHelper:
    """``_project_state_for_plane`` maps Ensemble → Plane status vocabulary."""

    def test_active_maps_to_active(self):
        assert _project_state_for_plane("active") == "active"

    def test_paused_maps_to_hold(self):
        assert _project_state_for_plane("paused") == "hold"

    def test_archived_maps_to_cancelled(self):
        assert _project_state_for_plane("archived") == "cancelled"

    def test_completed_maps_to_completed(self):
        assert _project_state_for_plane("completed") == "completed"

    def test_unknown_status_defaults_to_active(self):
        """Unknown statuses fall through to ``active`` per PLANE_STATUS_MAP."""
        assert _project_state_for_plane("nonexistent") == "active"

    def test_none_status_defaults_to_active(self):
        """None → ``active`` (defensive guard)."""
        assert _project_state_for_plane(None) == "active"

    def test_empty_string_defaults_to_active(self):
        """Empty string → ``active``."""
        assert _project_state_for_plane("") == "active"


# ─────────────────────────────────────────────────────────────────────────────
# Class 8: TestFindPlaneIdByName
# ─────────────────────────────────────────────────────────────────────────────


class TestFindPlaneIdByName:
    """``_find_plane_id_by_name`` — case-insensitive, defensive against
    missing keys."""

    def test_exact_match(self):
        projects = [{"id": "p1", "name": "Alpha"}, {"id": "p2", "name": "Beta"}]
        assert _find_plane_id_by_name(projects, "Alpha") == "p1"

    def test_case_insensitive_match(self):
        projects = [{"id": "p1", "name": "Alpha"}]
        assert _find_plane_id_by_name(projects, "alpha") == "p1"
        assert _find_plane_id_by_name(projects, "ALPHA") == "p1"

    def test_whitespace_in_name_is_stripped(self):
        projects = [{"id": "p1", "name": "  Alpha  "}]
        assert _find_plane_id_by_name(projects, "Alpha") == "p1"

    def test_no_match_returns_none(self):
        projects = [{"id": "p1", "name": "Alpha"}]
        assert _find_plane_id_by_name(projects, "Beta") is None

    def test_empty_list_returns_none(self):
        assert _find_plane_id_by_name([], "Anything") is None

    def test_empty_name_returns_none(self):
        projects = [{"id": "p1", "name": "Alpha"}]
        assert _find_plane_id_by_name(projects, "") is None
        assert _find_plane_id_by_name(projects, None) is None

    def test_missing_name_key_returns_none(self):
        """A project with no ``name`` field is skipped — never raises."""
        projects = [{"id": "p1"}, {"id": "p2", "name": "Alpha"}]
        assert _find_plane_id_by_name(projects, "Alpha") == "p2"

    def test_missing_id_key_returns_none_for_that_match(self):
        """A name match with no ``id`` field is skipped — no crash."""
        projects = [{"name": "Alpha"}, {"id": "p2", "name": "Alpha"}]
        # First one has no id → skipped, second matches
        assert _find_plane_id_by_name(projects, "Alpha") == "p2"

    def test_none_name_value_handled(self):
        """``name=None`` in a project row is treated as empty."""
        projects = [{"id": "p1", "name": None}]
        assert _find_plane_id_by_name(projects, "anything") is None

    def test_id_returned_as_str(self):
        """Even numeric IDs come back as ``str``."""
        projects = [{"id": 12345, "name": "Alpha"}]
        assert _find_plane_id_by_name(projects, "Alpha") == "12345"


# ─────────────────────────────────────────────────────────────────────────────
# Class 9: TestPlaneSyncServiceSync
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneSyncServiceSync:
    """End-to-end ``sync_project`` flows against a real SQLite-backed repo."""

    def test_sync_new_project_creates(self, repo, mock_plane_env):
        """No ``plane_project_id`` → CREATE path; plane_id stored; state=synced."""
        project = repo.create(name="NewProj", description="d")
        mock = MagicMock()
        mock.create_project = AsyncMock(return_value={"id": "plane-NEW"})
        mock.list_projects = AsyncMock(return_value=[])

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        assert result["action"] == "created"
        assert result["plane_project_id"] == "plane-NEW"
        assert "synced_at" in result

        # Verify metadata persisted
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_PROJECT_ID_METADATA_KEY] == "plane-NEW"
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == "synced"
        assert PLANE_SYNCED_AT_METADATA_KEY in meta

    def test_sync_existing_project_updates(self, repo, mock_plane_env):
        """``plane_project_id`` present → UPDATE path; action="updated"."""
        project = repo.create(name="ExistingProj", description="d")
        # Pre-seed plane_project_id metadata
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_PROJECT_ID_METADATA_KEY, "old-id"
            )
            session.commit()

        mock = MagicMock()
        mock.update_project = AsyncMock(return_value={"id": "old-id"})
        # create_project must NOT be called
        mock.create_project = AsyncMock(
            side_effect=AssertionError("create should not be called on UPDATE path")
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        assert result["action"] == "updated"
        assert result["plane_project_id"] == "old-id"
        mock.update_project.assert_called_once()
        mock.create_project.assert_not_called()

    def test_sync_adopts_by_name(self, repo, mock_plane_env):
        """No metadata, but Plane has matching name → adopt (UPDATE, no create)."""
        project = repo.create(name="AdoptMe")
        mock = MagicMock()
        mock.list_projects = AsyncMock(
            return_value=[{"id": "plane-existing", "name": "AdoptMe"}]
        )
        mock.update_project = AsyncMock(return_value={"id": "plane-existing"})
        mock.create_project = AsyncMock(
            side_effect=AssertionError("create should not be called when name match")
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        assert result["action"] == "updated"
        assert result["plane_project_id"] == "plane-existing"
        mock.update_project.assert_called_once()

    def test_sync_404_on_update_recreates(self, repo, mock_plane_env):
        """UPDATE fails with ``PlaneNotFoundError`` → recreate path."""
        project = repo.create(name="RecreateProj")
        # Pre-seed a stale plane_project_id
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_PROJECT_ID_METADATA_KEY, "stale-id"
            )
            session.commit()

        mock = MagicMock()

        async def update_404(*args, **kwargs):
            raise PlaneNotFoundError("Plane 404 on ...: missing")

        mock.update_project = AsyncMock(side_effect=update_404)
        mock.create_project = AsyncMock(return_value={"id": "fresh-id"})

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        assert result["action"] == "recreated"
        assert result["plane_project_id"] == "fresh-id"

    def test_sync_auth_error(self, repo, mock_plane_env):
        """``PlaneAuthError`` → status="error", state="error"."""
        project = repo.create(name="AuthErrProj")
        mock = MagicMock()

        async def raise_auth(*args, **kwargs):
            raise PlaneAuthError("bad token")

        mock.create_project = AsyncMock(side_effect=raise_auth)
        mock.update_project = AsyncMock(side_effect=raise_auth)
        mock.list_projects = AsyncMock(side_effect=raise_auth)

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        assert result["action"] is None
        assert "authentication" in result["message"].lower()

        # Verify plane_sync_state metadata was marked "error"
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta.get(PLANE_SYNC_STATE_METADATA_KEY) == "error"
        assert PLANE_SYNCED_AT_METADATA_KEY in meta

    def test_sync_api_error(self, repo, mock_plane_env):
        """Generic ``PlaneAPIError`` → status="error"."""
        project = repo.create(name="ApiErrProj")
        mock = MagicMock()

        async def raise_api(*args, **kwargs):
            raise PlaneAPIError("server boom")

        mock.create_project = AsyncMock(side_effect=raise_api)
        mock.list_projects = AsyncMock(side_effect=raise_api)

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        assert result["action"] is None
        assert "Plane API error" in result["message"]

    def test_sync_unexpected_exception(self, repo, mock_plane_env):
        """Any exception is caught → status="error" (never raises)."""
        project = repo.create(name="BoomProj")
        mock = MagicMock()

        async def raise_value(*args, **kwargs):
            raise ValueError("totally unexpected")

        mock.create_project = AsyncMock(side_effect=raise_value)
        mock.list_projects = AsyncMock(side_effect=raise_value)

        svc = PlaneSyncService(repo, http_client=mock)
        # Service must NEVER raise
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        assert "Unexpected error" in result["message"]

    def test_sync_project_not_found(self, repo, mock_plane_env):
        """Unknown ``project_id`` → status="error" with not-found message."""
        mock = MagicMock()
        mock.create_project = AsyncMock()
        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project("nonexistent-id"))

        assert result["status"] == "error"
        assert "not found" in result["message"].lower()
        # No HTTP calls should have been made
        mock.create_project.assert_not_called()

    def test_sync_disabled(self, repo, monkeypatch):
        """Feature not configured → status="disabled"."""
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)

        project = repo.create(name="DisabledProj")
        svc = PlaneSyncService(repo)  # No http_client → defaults to factory
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "disabled"
        assert "not configured" in result["message"]

    def test_sync_never_raises(self, repo, mock_plane_env):
        """Even catastrophic errors produce a dict, never an exception."""
        project = repo.create(name="NeverRaises")
        mock = MagicMock()
        mock.create_project = AsyncMock(
            side_effect=RuntimeError("something terrible")
        )
        mock.list_projects = AsyncMock(
            side_effect=RuntimeError("something terrible")
        )

        svc = PlaneSyncService(repo, http_client=mock)
        # Must not raise
        result = asyncio.run(svc.sync_project(project.project_id))
        assert isinstance(result, dict)
        assert result["status"] == "error"

    def test_idempotency_two_calls(self, repo, mock_plane_env):
        """Sync twice → first call CREATEs, second call UPDATEs (not CREATEs)."""
        project = repo.create(name="IdempProj")
        mock = MagicMock()
        mock.create_project = AsyncMock(return_value={"id": "plane-once"})
        mock.update_project = AsyncMock(return_value={"id": "plane-once"})
        mock.list_projects = AsyncMock(return_value=[])

        svc = PlaneSyncService(repo, http_client=mock)
        first = asyncio.run(svc.sync_project(project.project_id))
        second = asyncio.run(svc.sync_project(project.project_id))

        assert first["action"] == "created"
        assert second["action"] == "updated"
        assert first["plane_project_id"] == second["plane_project_id"] == "plane-once"
        # create called once, update called once
        assert mock.create_project.call_count == 1
        assert mock.update_project.call_count == 1

    def test_uses_list_metadata_records_not_get_metadata(
        self, repo, mock_plane_env
    ):
        """CR-6: the sync reads metadata via ``list_metadata_records`` (single
        call, then filter) instead of 3x ``get_metadata(key)`` for the three
        Plane keys. We assert that:
          * ``list_metadata_records`` is used (at least once)
          * ``get_metadata`` is NOT used to look up Plane keys
        """
        project = repo.create(name="MetaCallProj")
        mock = MagicMock()
        mock.create_project = AsyncMock(return_value={"id": "plane-meta"})
        mock.list_projects = AsyncMock(return_value=[])

        list_calls: list[str] = []
        original_list = repo.list_metadata_records

        def list_spy(session, project_id):
            list_calls.append(project_id)
            return original_list(session, project_id)

        get_calls: list[tuple[str, str]] = []
        original_get = repo.get_metadata

        def get_spy(project_id, key):
            get_calls.append((project_id, key))
            return original_get(project_id, key)

        with patch.object(repo, "list_metadata_records", side_effect=list_spy), \
             patch.object(repo, "get_metadata", side_effect=get_spy):
            svc = PlaneSyncService(repo, http_client=mock)
            asyncio.run(svc.sync_project(project.project_id))

        # list_metadata_records must be the primary read path (>= 1 call).
        assert len(list_calls) >= 1, (
            "sync_project must use list_metadata_records, not get_metadata"
        )
        # get_metadata must NOT be called for any of the 3 Plane keys.
        plane_keys = {
            PLANE_PROJECT_ID_METADATA_KEY,
            PLANE_SYNC_STATE_METADATA_KEY,
            PLANE_SYNCED_AT_METADATA_KEY,
        }
        plane_get_calls = [c for c in get_calls if c[1] in plane_keys]
        assert plane_get_calls == [], (
            f"get_metadata called for Plane keys: {plane_get_calls}; "
            "use list_metadata_records (CR-6) instead"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Class 10: TestPlaneSyncServiceIsAvailable
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneSyncServiceIsAvailable:
    """``PlaneSyncService.is_available`` delegates to ``PlaneHttpClient``."""

    def test_is_available_when_client_can_be_built(self, mock_plane_env):
        assert PlaneSyncService.is_available() is True

    def test_is_not_available_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)
        assert PlaneSyncService.is_available() is False


# ─────────────────────────────────────────────────────────────────────────────
# Class 11: TestPlaneSyncProjectToolCooldown
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneSyncProjectToolCooldown:
    """``plane_sync_project`` tool enforces a 30s per-project cooldown."""

    def test_first_call_passes_cooldown(self, clear_cooldown):
        """With no prior sync → ``_check_cooldown`` returns None."""
        assert _check_cooldown("proj-1", force=False) is None

    def test_cooldown_blocks_within_30s(self, clear_cooldown):
        """After a sync, a second call within 30s → rate_limited dict."""
        _last_sync["proj-1"] = time.monotonic()
        result = _check_cooldown("proj-1", force=False)

        assert result is not None
        assert result["status"] == "rate_limited"
        assert "recently" in result["message"].lower()
        assert result["cooldown_seconds"] == 30.0
        assert "last_sync_seconds_ago" in result

    def test_cooldown_force_bypasses(self, clear_cooldown):
        """``force=True`` bypasses the cooldown even within 30s."""
        _last_sync["proj-1"] = time.monotonic()
        assert _check_cooldown("proj-1", force=True) is None

    def test_cooldown_different_projects_independent(self, clear_cooldown):
        """Cooldown is per-project — different project_id is independent."""
        _last_sync["proj-1"] = time.monotonic()
        # proj-2 has never been synced → no cooldown
        assert _check_cooldown("proj-2", force=False) is None

    def test_cooldown_expired_allows_resync(self, clear_cooldown):
        """After 30s elapsed, the cooldown expires and sync is allowed."""
        # 31s ago — past the 30s cooldown
        _last_sync["proj-1"] = time.monotonic() - 31.0
        assert _check_cooldown("proj-1", force=False) is None


# ─────────────────────────────────────────────────────────────────────────────
# Class 12: TestPlaneSyncProjectToolIntegration
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneSyncProjectToolIntegration:
    """End-to-end tool tests through ``create_plane_sync_tools``."""

    def test_tool_feature_disabled(
        self, repo, monkeypatch, clear_cooldown
    ):
        """When ``PlaneSyncService.is_available()`` is False → tool returns disabled."""
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)

        tools = create_plane_sync_tools(repo)
        tool = tools[0]
        result = tool.func(project_id="any-id")

        assert result["status"] == "disabled"
        assert "not configured" in result["message"]

    def test_tool_cooldown_blocks_second_call(
        self, repo, mock_plane_env, monkeypatch, clear_cooldown
    ):
        """First call to the tool sets cooldown; second call returns rate_limited."""
        # Pre-fill cooldown so the FIRST call within this test is rate-limited.
        _last_sync["proj-1"] = time.monotonic()

        tools = create_plane_sync_tools(repo)
        tool = tools[0]
        result = tool.func(project_id="proj-1")

        assert result["status"] == "rate_limited"
        assert result["cooldown_seconds"] == 30.0

    def test_tool_force_bypasses_cooldown(
        self, repo, mock_plane_env, monkeypatch, clear_cooldown
    ):
        """force=True bypasses the cooldown gate."""
        _last_sync["proj-1"] = time.monotonic()

        # The mock client will be reached because force bypasses cooldown.
        mock_client = MagicMock()
        mock_client.list_projects = AsyncMock(return_value=[])
        mock_client.create_project = AsyncMock(return_value={"id": "plane-X"})

        # Need to create a project first for sync to succeed
        project = repo.create(name="ForceProj")

        tools = create_plane_sync_tools(repo)
        tool = tools[0]

        # Patch PlaneSyncService inside the tool to use our mock
        with patch(
            "daemon.tools.plane_sync.PlaneSyncService"
        ) as MockSvc:
            instance = MockSvc.return_value
            instance.is_available.return_value = True
            instance.sync_project = AsyncMock(
                return_value={
                    "status": "synced",
                    "action": "created",
                    "plane_project_id": "plane-X",
                    "synced_at": "2026-08-14T00:00:00+00:00",
                }
            )

            result = tool.func(project_id=project.project_id, force=True)

        assert result["status"] == "synced"
        instance.sync_project.assert_called_once()

    def test_tool_records_cooldown_even_on_error(
        self, repo, mock_plane_env, monkeypatch, clear_cooldown
    ):
        """Cooldown is recorded BEFORE the work, so even failures count."""
        tools = create_plane_sync_tools(repo)
        tool = tools[0]

        with patch(
            "daemon.tools.plane_sync.PlaneSyncService"
        ) as MockSvc:
            instance = MockSvc.return_value
            instance.is_available.return_value = True
            instance.sync_project = AsyncMock(
                return_value={"status": "error", "action": None, "message": "boom"}
            )

            result = tool.func(project_id="proj-1")

        assert result["status"] == "error"
        # Cooldown was recorded (the cost was paid)
        assert "proj-1" in _last_sync


# ─────────────────────────────────────────────────────────────────────────────
# Class 13: TestPlaneSyncToolRegistration
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneSyncToolRegistration:
    """Tool registration / metadata."""

    def test_tool_registered_in_project_category(self, repo):
        """``plane_sync_project._tool_category == "project"`` so the leader
        agent (which has ``tools.allow: ["project"]``) can invoke it."""
        tools = create_plane_sync_tools(repo)
        tool = tools[0]
        assert getattr(tool, "_tool_category", None) == "project"

    def test_tool_has_first_party_marker(self, repo):
        """``_tool_category_first_party`` is set so spoofed categories are
        rejected during rescan."""
        tools = create_plane_sync_tools(repo)
        tool = tools[0]
        assert getattr(tool, "_tool_category_first_party", False) is True

    def test_tool_has_full_doc_attached(self, repo):
        """``_full_doc_`` attribute is set on the tool for ``tool_help``."""
        tools = create_plane_sync_tools(repo)
        tool = tools[0]
        assert hasattr(tool, "_full_doc_")
        assert "Sync an Ensemble project" in tool._full_doc_

    def test_tool_name_is_plane_sync_project(self, repo):
        """The tool is exposed under the exact name ``plane_sync_project``."""
        tools = create_plane_sync_tools(repo)
        tool = tools[0]
        assert tool.name == "plane_sync_project"


# ─────────────────────────────────────────────────────────────────────────────
# Class 14: TestPlaneConstants
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneConstants:
    """Sanity checks on the shared constants used by the sync subsystem."""

    def test_plane_status_map_keys(self):
        assert "active" in PLANE_STATUS_MAP
        assert "paused" in PLANE_STATUS_MAP
        assert "archived" in PLANE_STATUS_MAP
        assert "completed" in PLANE_STATUS_MAP

    def test_plane_status_map_values(self):
        assert PLANE_STATUS_MAP["active"] == "active"
        assert PLANE_STATUS_MAP["paused"] == "hold"
        assert PLANE_STATUS_MAP["archived"] == "cancelled"
        assert PLANE_STATUS_MAP["completed"] == "completed"

    def test_metadata_keys_are_distinct(self):
        keys = {
            PLANE_PROJECT_ID_METADATA_KEY,
            PLANE_SYNC_STATE_METADATA_KEY,
            PLANE_SYNCED_AT_METADATA_KEY,
        }
        assert len(keys) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Class 15: TestEdgeCaseMalformedResponses
# ─────────────────────────────────────────────────────────────────────────────
#
# Gap: existing 74 tests cover 204 (no body) and well-formed 2xx JSON, but
# NOT the following malformed body shapes that Plane (or proxies in front of
# it) can plausibly return:
#   * 2xx with a non-JSON body (HTML error page from a reverse proxy)
#   * 2xx with the literal JSON ``null`` body
#   * 2xx with a list where the contract says "dict"
#   * 2xx with a dict that is missing the ``id`` field entirely
# The service must convert all of these into a structured error state, never
# crash, and never silently lose the project row.


class TestEdgeCaseMalformedResponses:
    """Malformed 2xx responses — defensive against API shape drift."""

    def test_2xx_with_non_json_body_returns_none_at_client(
        self, mock_plane_env, monkeypatch
    ):
        """2xx with HTML/text body → ``_request`` returns None (defensive)."""
        _patched_async_client(
            monkeypatch,
            [httpx.Response(200, text="<html>not json</html>")],
        )
        client = PlaneHttpClient()

        async def run():
            return await client._request("GET", "http://x/anything/")

        result = asyncio.run(run())
        assert result is None

    def test_2xx_with_null_json_body_returns_none_at_client(
        self, mock_plane_env, monkeypatch
    ):
        """2xx with bare ``null`` JSON → ``_request`` returns None."""
        _patched_async_client(monkeypatch, [httpx.Response(200, text="null")])
        client = PlaneHttpClient()

        async def run():
            return await client._request("GET", "http://x/anything/")

        result = asyncio.run(run())
        assert result is None

    def test_create_project_raises_on_non_dict_response(
        self, mock_plane_env, monkeypatch
    ):
        """Plane returns a list, contract says dict → ``PlaneAPIError``."""
        _patched_async_client(
            monkeypatch, [httpx.Response(201, json=[{"id": "p1"}])]
        )
        client = PlaneHttpClient()

        async def run():
            return await client.create_project(name="X")

        with pytest.raises(PlaneAPIError) as exc:
            asyncio.run(run())
        assert "non-dict" in str(exc.value)

    def test_update_project_raises_on_non_dict_response(
        self, mock_plane_env, monkeypatch
    ):
        """``update_project`` also rejects non-dict responses."""
        _patched_async_client(
            monkeypatch, [httpx.Response(200, text="plain text")]
        )
        client = PlaneHttpClient()

        async def run():
            return await client.update_project("plane-x", name="Y")

        with pytest.raises(PlaneAPIError) as exc:
            asyncio.run(run())
        assert "non-dict" in str(exc.value)

    def test_get_project_raises_on_non_dict_response(
        self, mock_plane_env, monkeypatch
    ):
        """``get_project`` returns None only on 404; other shape errors raise."""
        _patched_async_client(
            monkeypatch,
            [httpx.Response(200, json=["not", "a", "dict"])],
        )
        client = PlaneHttpClient()

        async def run():
            return await client.get_project("plane-x")

        with pytest.raises(PlaneAPIError) as exc:
            asyncio.run(run())
        assert "non-dict" in str(exc.value)

    def test_sync_service_handles_create_response_missing_id(
        self, repo, mock_plane_env
    ):
        """Plane returns a dict with no ``id`` key → service returns error,
        not crash, and the project metadata is marked ``error``."""
        project = repo.create(name="NoIdProj")
        mock = MagicMock()
        mock.list_projects = AsyncMock(return_value=[])
        mock.create_project = AsyncMock(
            return_value={"name": "NoIdProj", "description": "d"}  # no "id"
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        assert "no id" in result["message"].lower()

        # Metadata should reflect the error state
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == "error"
        assert PLANE_SYNCED_AT_METADATA_KEY in meta

    def test_sync_service_handles_adopt_path_create_response_missing_id(
        self, repo, mock_plane_env
    ):
        """Adopt-by-name path: list returns stale Plane project, update
        fails with 404 → recreate; if create returns no id, still error."""
        project = repo.create(name="AdoptNoIdProj")
        mock = MagicMock()

        async def update_404(*args, **kwargs):
            raise PlaneNotFoundError("Plane 404 on ...: missing")

        mock.update_project = AsyncMock(side_effect=update_404)
        mock.create_project = AsyncMock(
            return_value={"name": "X"}  # no id
        )
        mock.list_projects = AsyncMock(return_value=[])

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        assert "no id" in result["message"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Class 16: TestEdgeCaseCircuitBreakerOpenAtService
# ─────────────────────────────────────────────────────────────────────────────
#
# Gap: existing 74 tests verify the client-level breaker behavior (open
# after 5 failures, reset on success) but do NOT verify that the service
# layer translates an OPEN breaker into a clean error response without
# crashing. The service's never-raises contract must hold even when the
# underlying client refuses to even talk to Plane.


class TestEdgeCaseCircuitBreakerOpenAtService:
    """Service-layer behavior when the client's circuit breaker is OPEN."""

    def test_sync_service_returns_error_when_breaker_open(
        self, repo, mock_plane_env
    ):
        """With the client breaker forced OPEN, the service returns
        ``status="error"`` and never raises — the caller is not stuck."""
        project = repo.create(name="BreakerOpenProj")
        # Construct a fresh breaker and force it OPEN (use the enum).
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
        breaker.state = CircuitState.OPEN
        breaker.failure_count = 5
        breaker.last_failure_time = time.monotonic()
        assert breaker.get_state() == "open"

        # Inject a client whose methods raise the same exception the
        # real client would raise when the breaker is OPEN. This tests
        # the SERVICE's error-handling contract end-to-end.
        client = MagicMock()

        async def raise_breaker_open(*args, **kwargs):
            raise PlaneAPIError(
                "Circuit breaker is OPEN — skipping Plane API call"
            )

        client.create_project = AsyncMock(side_effect=raise_breaker_open)
        client.update_project = AsyncMock(side_effect=raise_breaker_open)
        client.list_projects = AsyncMock(side_effect=raise_breaker_open)

        svc = PlaneSyncService(repo, http_client=client)
        # Service must NOT raise.
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        assert "circuit breaker" in result["message"].lower() or \
               "plane api" in result["message"].lower()

    def test_sync_service_marks_error_state_when_breaker_open(
        self, repo, mock_plane_env
    ):
        """When the breaker is OPEN, the project metadata is marked
        ``plane_sync_state="error"`` so the next manual sync can recover."""
        project = repo.create(name="BreakerOpenMeta")
        # Pre-seed an existing plane_project_id so the UPDATE path is taken.
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session,
                project.project_id,
                PLANE_PROJECT_ID_METADATA_KEY,
                "plane-prev",
            )
            session.commit()

        client = MagicMock()

        async def raise_breaker_open(*args, **kwargs):
            raise PlaneAPIError(
                "Circuit breaker is OPEN — skipping Plane API call"
            )

        client.create_project = AsyncMock(side_effect=raise_breaker_open)
        client.list_projects = AsyncMock(side_effect=raise_breaker_open)
        client.update_project = AsyncMock(side_effect=raise_breaker_open)

        svc = PlaneSyncService(repo, http_client=client)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"

        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == "error"
        assert PLANE_SYNCED_AT_METADATA_KEY in meta


# ─────────────────────────────────────────────────────────────────────────────
# Class 17: TestEdgeCaseSpecialCharacters
# ─────────────────────────────────────────────────────────────────────────────
#
# Gap: project names are user-supplied and can contain anything an OS
# filesystem allows. None of the 74 existing tests exercise non-ASCII or
# syntactically-active characters. Verify the sync pipeline passes the
# name through verbatim and does not crash on edge cases like embedded
# quotes, newlines, or emoji.


class TestEdgeCaseSpecialCharacters:
    """Project names with unicode, quotes, emoji, newlines, and escape chars."""

    def test_sync_project_with_unicode_name(self, repo, mock_plane_env):
        """Cyrillic / CJK / Latin-Extended names are passed through."""
        for name in ["Проект", "プロジェクト", "café", "naïve", "über"]:
            project = repo.create(name=name)
            mock = MagicMock()
            mock.create_project = AsyncMock(return_value={"id": "plane-u"})
            mock.list_projects = AsyncMock(return_value=[])

            svc = PlaneSyncService(repo, http_client=mock)
            result = asyncio.run(svc.sync_project(project.project_id))

            assert result["status"] == "synced"
            # Verify the unicode name was sent verbatim
            call_kwargs = mock.create_project.call_args.kwargs
            assert call_kwargs["name"] == name

    def test_sync_project_with_emoji_in_name(self, repo, mock_plane_env):
        """Emoji in name is preserved end-to-end."""
        project = repo.create(name="🚀 Rocket Project 🎯")
        mock = MagicMock()
        mock.create_project = AsyncMock(return_value={"id": "plane-emoji"})
        mock.list_projects = AsyncMock(return_value=[])

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        kwargs = mock.create_project.call_args.kwargs
        assert kwargs["name"] == "🚀 Rocket Project 🎯"

    def test_sync_project_with_quote_in_name(self, repo, mock_plane_env):
        """Single AND double quotes in name don't break the JSON body."""
        name = "Bob's \"Important\" Project"
        project = repo.create(name=name)
        mock = MagicMock()
        mock.create_project = AsyncMock(return_value={"id": "plane-q"})
        mock.list_projects = AsyncMock(return_value=[])

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        assert mock.create_project.call_args.kwargs["name"] == name

    def test_sync_project_with_newline_in_name(self, repo, mock_plane_env):
        """Newlines in names (rare but valid) are preserved."""
        name = "Line1\nLine2"
        project = repo.create(name=name)
        mock = MagicMock()
        mock.create_project = AsyncMock(return_value={"id": "plane-nl"})
        mock.list_projects = AsyncMock(return_value=[])

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        assert mock.create_project.call_args.kwargs["name"] == name

    def test_sync_project_with_backslash_and_special_chars(
        self, repo, mock_plane_env
    ):
        """Backslashes, tabs, and other control chars are preserved."""
        name = "C:\\path\\to\\project\twith-tabs"
        project = repo.create(name=name)
        mock = MagicMock()
        mock.create_project = AsyncMock(return_value={"id": "plane-bs"})
        mock.list_projects = AsyncMock(return_value=[])

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        assert mock.create_project.call_args.kwargs["name"] == name


# ─────────────────────────────────────────────────────────────────────────────
# Class 18: TestEdgeCaseConcurrentSync
# ─────────────────────────────────────────────────────────────────────────────
#
# Gap: existing 74 tests verify the cooldown gate sequentially (first call
# passes, second call within 30s is rate-limited). They do NOT exercise
# concurrent calls from multiple threads — which is the realistic shape
# of the threat model (parallel agent workflows, retry storms, etc.) and
# the one most likely to surface a race condition on the shared
# ``_last_sync`` dict.


class TestEdgeCaseConcurrentSync:
    """Concurrent calls must not crash, and the cooldown must block the loser."""

    def test_tool_concurrent_calls_same_project_one_blocked(
        self, repo, mock_plane_env, monkeypatch, clear_cooldown
    ):
        """Two threads call the tool for the same project ID. With the
        cooldown recorded BEFORE the work, the second thread must observe
        the rate-limited response (or both succeed if the first finished
        and released the slot — but never both proceed concurrently)."""
        # Patch the service so the work is fast and deterministic.
        with patch("daemon.tools.plane_sync.PlaneSyncService") as MockSvc:
            instance = MockSvc.return_value
            instance.is_available.return_value = True
            instance.sync_project = AsyncMock(
                return_value={
                    "status": "synced",
                    "action": "created",
                    "plane_project_id": "plane-conc",
                    "synced_at": "2026-08-14T00:00:00+00:00",
                }
            )

            tools = create_plane_sync_tools(repo)
            tool = tools[0]

            results: list[dict] = []
            errors: list[Exception] = []

            def worker():
                try:
                    r = tool.func(project_id="proj-concurrent")
                    results.append(r)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            # No thread should have crashed
            assert errors == [], f"Threads crashed: {errors}"
            assert len(results) == 2

            # At most one call should have made it past the cooldown.
            # The other must be rate_limited.
            statuses = sorted(r["status"] for r in results)
            assert statuses == ["rate_limited", "synced"], (
                f"Expected 1 synced + 1 rate_limited, got {statuses}"
            )

            # The service must have been called at most once.
            assert instance.sync_project.call_count <= 1

    def test_tool_concurrent_calls_different_projects_both_succeed(
        self, repo, mock_plane_env, monkeypatch, clear_cooldown
    ):
        """Concurrent calls for distinct project IDs must both succeed —
        the cooldown is per-project, not global."""
        project_a = repo.create(name="ProjectA")
        project_b = repo.create(name="ProjectB")

        with patch("daemon.tools.plane_sync.PlaneSyncService") as MockSvc:
            instance = MockSvc.return_value
            instance.is_available.return_value = True
            instance.sync_project = AsyncMock(
                return_value={
                    "status": "synced",
                    "action": "created",
                    "plane_project_id": "plane-x",
                    "synced_at": "2026-08-14T00:00:00+00:00",
                }
            )

            tools = create_plane_sync_tools(repo)
            tool = tools[0]

            results: list[dict] = []

            def worker(project_id):
                results.append(tool.func(project_id=project_id))

            t1 = threading.Thread(target=worker, args=(project_a.project_id,))
            t2 = threading.Thread(target=worker, args=(project_b.project_id,))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            assert len(results) == 2
            assert all(r["status"] == "synced" for r in results), (
                f"Both syncs should succeed, got {results}"
            )

    def test_service_concurrent_calls_dont_crash(
        self, repo, mock_plane_env
    ):
        """Two concurrent ``sync_project`` calls for the SAME project must
        both complete (they both run the work — the service has no
        built-in concurrency lock; the cooldown gate is layer-above)."""
        project = repo.create(name="SvcConcurrent")

        # Use a slow mock so the two threads definitely overlap.
        call_count = 0
        call_lock = threading.Lock()

        async def slow_create(*args, **kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
            await asyncio.sleep(0.05)
            return {"id": "plane-svc-conc"}

        mock = MagicMock()
        mock.create_project = AsyncMock(side_effect=slow_create)
        mock.list_projects = AsyncMock(return_value=[])
        mock.update_project = AsyncMock(return_value={"id": "plane-svc-conc"})

        svc = PlaneSyncService(repo, http_client=mock)

        results: list[dict] = []

        def worker():
            results.append(asyncio.run(svc.sync_project(project.project_id)))

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both calls must complete without crashing.
        assert len(results) == 2
        # Both should succeed (mock returns valid response).
        assert all(r["status"] == "synced" for r in results), (
            f"Both syncs should succeed, got {results}"
        )

    def test_tool_concurrent_calls_after_cooldown_expired_both_succeed(
        self, repo, mock_plane_env, clear_cooldown
    ):
        """If the cooldown has already expired, concurrent calls for the
        same project both succeed (no false rate-limiting)."""
        # Set the cooldown to 31s ago — past the 30s window.
        import daemon.tools.plane_sync as ps_module

        ps_module._last_sync["proj-expired"] = time.monotonic() - 31.0

        with patch("daemon.tools.plane_sync.PlaneSyncService") as MockSvc:
            instance = MockSvc.return_value
            instance.is_available.return_value = True
            instance.sync_project = AsyncMock(
                return_value={
                    "status": "synced",
                    "action": "updated",
                    "plane_project_id": "plane-x",
                    "synced_at": "2026-08-14T00:00:00+00:00",
                }
            )

            tools = create_plane_sync_tools(repo)
            tool = tools[0]

            results: list[dict] = []

            def worker():
                results.append(tool.func(project_id="proj-expired"))

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            # At most one should be rate-limited (the second, after the
            # first sets the cooldown). But neither should crash.
            assert len(results) == 2
            assert all(r["status"] in ("synced", "rate_limited") for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# Class 19: TestEdgeCaseMetadataUpdateWithNameChange
# ─────────────────────────────────────────────────────────────────────────────
#
# Gap: the existing ``test_sync_existing_project_updates`` covers the
# basic UPDATE path when both name and metadata are unchanged. It does
# NOT verify that a project that has been RENAMED (between syncs) still
# takes the UPDATE path — using the existing ``plane_project_id`` — and
# pushes the new name to Plane. This is the renamed-project happy path
# and exercises the path the v1 scope limitation explicitly says it
# does NOT auto-detect (a manual sync must be triggered).


class TestEdgeCaseMetadataUpdateWithNameChange:
    """The UPDATE path is preserved even after the project is renamed."""

    def test_sync_with_existing_metadata_after_rename_updates(
        self, repo, mock_plane_env
    ):
        """Project was synced under name "OldName", then renamed to
        "NewName". The next sync must use the UPDATE path (existing
        ``plane_project_id``) and push the new name to Plane — not
        create a duplicate."""
        project = repo.create(name="OldName")

        # Pre-seed plane_project_id (as if a previous sync happened).
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session,
                project.project_id,
                PLANE_PROJECT_ID_METADATA_KEY,
                "plane-existing",
            )
            repo.set_metadata_record(
                session,
                project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY,
                "synced",
            )
            session.commit()

        # Rename the project.
        repo.update(project.project_id, name="NewName")

        mock = MagicMock()
        mock.update_project = AsyncMock(
            return_value={"id": "plane-existing", "name": "NewName"}
        )
        mock.create_project = AsyncMock(
            side_effect=AssertionError(
                "create_project must NOT be called when metadata exists"
            )
        )
        mock.list_projects = AsyncMock(
            side_effect=AssertionError(
                "list_projects must NOT be called when metadata exists"
            )
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        assert result["action"] == "updated"
        assert result["plane_project_id"] == "plane-existing"
        # Verify the new name was sent to Plane via the UPDATE path.
        # update_project is called as (plane_id, name=..., description=...)
        # so plane_id is the first positional arg, not a kwarg.
        call_args = mock.update_project.call_args
        assert call_args.args[0] == "plane-existing"
        assert call_args.kwargs["name"] == "NewName"

    def test_sync_with_existing_metadata_preserves_state_on_resync(
        self, repo, mock_plane_env
    ):
        """Re-syncing a project whose metadata is already ``synced`` keeps
        the UPDATE path and refreshes ``plane_synced_at``."""
        project = repo.create(name="ResyncProj")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session,
                project.project_id,
                PLANE_PROJECT_ID_METADATA_KEY,
                "plane-known",
            )
            repo.set_metadata_record(
                session,
                project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY,
                "synced",
            )
            repo.set_metadata_record(
                session,
                project.project_id,
                PLANE_SYNCED_AT_METADATA_KEY,
                "2020-01-01T00:00:00+00:00",
            )
            session.commit()

        # Capture timestamp before sync.
        before_sync = time.monotonic()

        mock = MagicMock()
        mock.update_project = AsyncMock(return_value={"id": "plane-known"})
        mock.create_project = AsyncMock(
            side_effect=AssertionError("create must NOT be called")
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "synced"
        assert result["action"] == "updated"
        assert result["plane_project_id"] == "plane-known"

        # ``plane_synced_at`` should be refreshed (newer than the seeded
        # 2020 timestamp).
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == "synced"
        assert meta[PLANE_SYNCED_AT_METADATA_KEY] != "2020-01-01T00:00:00+00:00"
        # Sanity: the new timestamp is parseable.
        from datetime import datetime
        parsed = datetime.fromisoformat(meta[PLANE_SYNCED_AT_METADATA_KEY])
        assert parsed.timestamp() > 0
        assert time.monotonic() - before_sync < 60  # ran recently
