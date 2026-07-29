"""Integration tests for the ``initiative_message`` feature through the API.

These tests exercise the FULL end-to-end flow:

1. The REAL ``InstanceMessagingService._maybe_store_initiative_message`` hook
   writes to a shared in-memory SQLite engine.
2. The FastAPI ``GET /api/instances/{id}`` and ``GET /api/instances?search=``
   endpoints read from the same engine and reflect the captured message.

Unlike ``tests/test_initiative_message.py`` (which pre-seeds metadata directly
in the DB), these tests capture the message via the REAL capture hook and
verify the API reflects it — closing the coverage gap between the capture
hook, repository persistence, and API exposure.

Run::

    pytest tests/integration/test_initiative_message_integration.py -v --override-ini="addopts="
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance_ui_prefs.repository import (
    InstanceUiPrefsRepository,
)
from daemon.routers.instances import router as instances_router
from daemon.services.instance_lifecycle import InstanceLifecycleService


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures (mirrors tests/test_initiative_message.py)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine with FK enforcement (matches search API test)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


def _make(
    repo: SQLModelInstanceRepository,
    instance_id: str,
    agent_id: str = "developer",
    agent_dir: str = "agents/coder",
    *,
    metadata: dict | None = None,
    parent_id: str | None = None,
    project_id: str | None = None,
    status: str = "idle",
) -> Instance:
    """Insert an instance via the repository."""
    return repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        metadata=metadata or {},
        parent_id=parent_id,
        project_id=project_id,
        status=status,
    )


@pytest.fixture
def messaging_manager(repo) -> MagicMock:
    """Build a mock manager with a REAL instance repository.

    The ``_maybe_store_initiative_message`` method calls
    ``self._manager._instance_repository.{get,set_metadata}`` via
    ``asyncio.to_thread``; everything else on the manager is unused by the
    capture hook and is mocked.
    """
    manager = MagicMock()
    manager._instance_repository = repo
    manager._queue_repository = MagicMock()
    manager._engine = MagicMock()
    manager._generate_and_broadcast_title = AsyncMock()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_lifecycle = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._checkpointer = MagicMock()
    manager._graph_tasks = {}
    manager.config = MagicMock()
    manager.config.limits.graph_recursion_limit = 50
    return manager


@pytest.fixture
def mock_cancellation_service() -> MagicMock:
    svc = MagicMock()
    svc.is_shutting_down = False
    return svc


@pytest.fixture
def messaging_service(messaging_manager, mock_cancellation_service):
    """Create an ``InstanceMessagingService`` with a real instance repo.

    ``daemon.manager`` is mocked out so the import path doesn't drag in MCP
    / langgraph_check setup that the capture hook doesn't need.
    """
    mock_manager_module = MagicMock()
    with patch.dict("sys.modules", {"daemon.manager": mock_manager_module}):
        from daemon.services.instance_messaging import InstanceMessagingService
        return InstanceMessagingService(
            manager=messaging_manager,
            cancellation_service=mock_cancellation_service,
        )


@pytest.fixture
def app_and_client(engine):
    """FastAPI ``TestClient`` with real repo + lifecycle service.

    Mirrors ``tests/test_instance_search_api.py::app_and_client``.

    Shares the same ``engine`` as ``messaging_service`` so writes via the
    capture hook are visible through the API.
    """
    repo = SQLModelInstanceRepository(engine)
    ui_prefs_repo = InstanceUiPrefsRepository(engine)

    manager = MagicMock()
    manager.is_write_paused = False
    manager.engine = engine
    manager._instance_repository = repo
    manager._instance_ui_prefs_repo = ui_prefs_repo

    service = InstanceLifecycleService(
        manager=manager,
        cancellation_service=MagicMock(),
    )
    manager.list_instances = service.list_instances
    manager.get_instance_info = service.get_instance_info
    manager.get_instance = AsyncMock()
    manager.get_queue_stats = AsyncMock(return_value={"pending_count": 0})

    app = FastAPI()
    app.include_router(instances_router)
    app.state.manager = manager

    with TestClient(app) as client:
        yield client


def _ids_from_list(resp_json: dict) -> list[str]:
    return sorted(i["instance_id"] for i in resp_json["instances"])


# ─────────────────────────────────────────────────────────────────────────────
# Class 1 — End-to-end capture via the messaging hook + API reflection
# ─────────────────────────────────────────────────────────────────────────────


class TestEndToEndCaptureViaAPI:
    """Full flow: capture hook → repo persistence → API response.

    The message is captured via the REAL
    ``_maybe_store_initiative_message`` hook (NOT pre-seeded via metadata),
    then verified through ``GET /api/instances/{id}``.
    """

    async def test_create_instance_then_capture_then_api_reflects(
        self, messaging_service, app_and_client, repo
    ):
        """Create instance via repo, call real capture hook, GET reflects it."""
        _make(repo, "inst-e2e-1", agent_id="developer", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message(
            "inst-e2e-1", "deploy the staging environment"
        )
        resp = app_and_client.get("/instances/inst-e2e-1")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == "inst-e2e-1"
        assert body["initiative_message"] == "deploy the staging environment"

    async def test_idempotent_across_two_capture_calls(
        self, messaging_service, app_and_client, repo
    ):
        """Two capture calls with different messages — first wins, verified via API."""
        _make(repo, "inst-idem", agent_id="developer", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message(
            "inst-idem", "first message wins"
        )
        await messaging_service._maybe_store_initiative_message(
            "inst-idem", "second message should be ignored"
        )
        resp = app_and_client.get("/instances/inst-idem")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["initiative_message"] == "first message wins"

    async def test_two_instances_get_independent_initiative_messages(
        self, messaging_service, app_and_client, repo
    ):
        """Each instance gets its own captured message — no cross-contamination."""
        _make(repo, "inst-a", agent_id="developer", agent_dir="agents/coder")
        _make(repo, "inst-b", agent_id="reviewer", agent_dir="agents/reviewer")
        await messaging_service._maybe_store_initiative_message(
            "inst-a", "message for instance A"
        )
        await messaging_service._maybe_store_initiative_message(
            "inst-b", "message for instance B"
        )
        resp_a = app_and_client.get("/instances/inst-a")
        resp_b = app_and_client.get("/instances/inst-b")
        assert resp_a.status_code == 200, resp_a.text
        assert resp_b.status_code == 200, resp_b.text
        assert resp_a.json()["initiative_message"] == "message for instance A"
        assert resp_b.json()["initiative_message"] == "message for instance B"


# ─────────────────────────────────────────────────────────────────────────────
# Class 2 — API search after real capture
# ─────────────────────────────────────────────────────────────────────────────


class TestAPISearchAfterCapture:
    """``GET /api/instances?search=`` finds instances via captured messages.

    The initiative_message is captured via the REAL hook, then the search
    API is queried to confirm the message participates in the OR predicate.
    """

    async def test_search_finds_instance_via_captured_initiative_message(
        self, messaging_service, app_and_client, repo
    ):
        """Capture a distinctive phrase, then search returns this instance."""
        _make(repo, "inst-search-1", agent_id="developer", agent_dir="agents/coder")
        _make(repo, "inst-search-2", agent_id="reviewer", agent_dir="agents/reviewer")
        await messaging_service._maybe_store_initiative_message(
            "inst-search-1", "unicorn-pineapple-zebra"
        )
        resp = app_and_client.get(
            "/instances", params={"search": "unicorn-pineapple-zebra"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids_from_list(body) == ["inst-search-1"]

    async def test_search_case_insensitive_via_captured_message(
        self, messaging_service, app_and_client, repo
    ):
        """Capture "Deploy Production", search "deploy" (lowercase) → finds it."""
        _make(repo, "inst-ci", agent_id="developer", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message(
            "inst-ci", "Deploy Production"
        )
        resp = app_and_client.get("/instances", params={"search": "deploy"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids_from_list(body) == ["inst-ci"]

    async def test_search_returns_capture_match_and_other_matches(
        self, messaging_service, app_and_client, repo
    ):
        """One matched via initiative_message, another via title — both returned."""
        _make(
            repo, "inst-via-init", agent_id="developer", agent_dir="agents/coder",
            metadata={"title": "unrelated title"},
        )
        _make(
            repo, "inst-via-title", agent_id="reviewer", agent_dir="agents/reviewer",
            metadata={"title": "deploy monitoring dashboard"},
        )
        await messaging_service._maybe_store_initiative_message(
            "inst-via-init", "deploy the auth service"
        )
        resp = app_and_client.get("/instances", params={"search": "deploy"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert _ids_from_list(body) == ["inst-via-init", "inst-via-title"]


# ─────────────────────────────────────────────────────────────────────────────
# Class 3 — API edge cases (truncation, special chars, empty/None)
# ─────────────────────────────────────────────────────────────────────────────


class TestAPIEdgeCases:
    """Edge cases exercised through the capture hook + API verification."""

    async def test_capture_then_get_with_very_long_message(
        self, messaging_service, app_and_client, repo
    ):
        """1500-char message is truncated to exactly 1000 chars."""
        _make(repo, "inst-long", agent_id="developer", agent_dir="agents/coder")
        long_msg = "A" * 1500
        await messaging_service._maybe_store_initiative_message(
            "inst-long", long_msg
        )
        resp = app_and_client.get("/instances/inst-long")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["initiative_message"] is not None
        assert len(body["initiative_message"]) == 1000
        assert body["initiative_message"] == "A" * 1000

    async def test_capture_then_get_with_special_chars_percent_underscore_backslash(
        self, messaging_service, app_and_client, repo
    ):
        """Special chars (%, _, \\) are stored verbatim and searched literally."""
        _make(repo, "inst-special", agent_id="developer", agent_dir="agents/coder")
        special_msg = r"100%_test\path"
        await messaging_service._maybe_store_initiative_message(
            "inst-special", special_msg
        )
        # Verify exact value via GET
        resp = app_and_client.get("/instances/inst-special")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["initiative_message"] == special_msg
        # Search "100%" → literal match, returns only this instance
        resp_search = app_and_client.get(
            "/instances", params={"search": "100%"}
        )
        assert resp_search.status_code == 200, resp_search.text
        search_body = resp_search.json()
        assert search_body["total"] == 1
        assert _ids_from_list(search_body) == ["inst-special"]

    async def test_capture_then_get_with_unicode_message(
        self, messaging_service, app_and_client, repo
    ):
        """Unicode (é, emoji, CJK) is stored verbatim and searchable."""
        _make(repo, "inst-unicode", agent_id="developer", agent_dir="agents/coder")
        unicode_msg = "héllo 🚀 日本語"
        await messaging_service._maybe_store_initiative_message(
            "inst-unicode", unicode_msg
        )
        resp = app_and_client.get("/instances/inst-unicode")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["initiative_message"] == unicode_msg
        # Search by emoji
        resp_search = app_and_client.get(
            "/instances", params={"search": "🚀"}
        )
        assert resp_search.status_code == 200, resp_search.text
        search_body = resp_search.json()
        assert search_body["total"] == 1
        assert _ids_from_list(search_body) == ["inst-unicode"]

    async def test_capture_then_get_with_multiline_message(
        self, messaging_service, app_and_client, repo
    ):
        """Newlines in the message are preserved verbatim."""
        _make(repo, "inst-multi", agent_id="developer", agent_dir="agents/coder")
        multiline_msg = "line1\nline2\nline3"
        await messaging_service._maybe_store_initiative_message(
            "inst-multi", multiline_msg
        )
        resp = app_and_client.get("/instances/inst-multi")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["initiative_message"] == multiline_msg
        assert "\n" in body["initiative_message"]

    async def test_empty_message_not_captured_no_field_in_api(
        self, messaging_service, app_and_client, repo
    ):
        """Empty string message → not captured, field is null in API."""
        _make(repo, "inst-empty", agent_id="developer", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message(
            "inst-empty", ""
        )
        resp = app_and_client.get("/instances/inst-empty")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["initiative_message"] is None

    async def test_whitespace_only_message_not_captured(
        self, messaging_service, app_and_client, repo
    ):
        """Whitespace-only message → not captured, field is null."""
        _make(repo, "inst-ws", agent_id="developer", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message(
            "inst-ws", "   \t\n  "
        )
        resp = app_and_client.get("/instances/inst-ws")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["initiative_message"] is None

    async def test_none_message_not_captured(
        self, messaging_service, app_and_client, repo
    ):
        """``None`` message → no exception, field is null."""
        _make(repo, "inst-none", agent_id="developer", agent_dir="agents/coder")
        # Must not raise
        await messaging_service._maybe_store_initiative_message(
            "inst-none", None  # type: ignore[arg-type]
        )
        resp = app_and_client.get("/instances/inst-none")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["initiative_message"] is None
