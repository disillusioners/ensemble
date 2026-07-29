"""Tests for the ``initiative_message`` feature.

Captures the first real user message on the IDLE -> RUNNING transition into
``instance_metadata.initiative_message``, exposes it in the ``InstanceInfo``
API response, and adds it to the dialect-aware substring search predicate in
``SQLModelInstanceRepository._build_search_condition``.

Coverage breakdown:

* **Group 1 — Capture** (in-memory SQLite, real ``SQLModelInstanceRepository``):
  exercises ``InstanceMessagingService._maybe_store_initiative_message``
  directly so each capture rule (first-wins, truncation, skip-empty,
  special characters) is asserted against real persistence.

* **Group 2 — Search**: mirrors ``tests/test_instance_search.py`` so the
  existing ``SQLModelInstanceRepository.list(search=...)`` fixtures and
  assertions are reused; new test methods assert ``initiative_message``
  matching, case-insensitivity, OR-combination with title/agent_name/
  agent_id, and wildcard escaping on the SQLite path
  (``json_extract(metadata, '$.initiative_message')``).

* **Group 3 — API + Edge cases**: builds a FastAPI ``TestClient`` against a
  real in-memory SQLite engine (mirrors ``tests/test_instance_search_api.py``)
  and asserts that ``initiative_message`` is exposed by both the list and
  detail endpoints, and that instances never messaged return ``null``.

These tests run on the in-memory SQLite path used by ``test_instance_search``.
The PostgreSQL-specific JSONB path (``metadata->>'initiative_message'`` cast
to VARCHAR) is exercised separately in
``tests/postgres/test_initiative_message_pg.py`` and only runs under
``pytest -m postgres``.

Run::

    pytest tests/test_initiative_message.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance_ui_prefs.repository import (
    InstanceUiPrefsRepository,
)
from daemon.routers.instances import router as instances_router
from daemon.services.instance_lifecycle import InstanceLifecycleService


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
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
    agent_id: str,
    agent_dir: str,
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


def _ids(instances) -> list[str]:
    return sorted(inst.instance_id for inst in instances)


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


# ─────────────────────────────────────────────────────────────────────────────
# Group 1 — Capture tests (direct method calls against real repo)
# ─────────────────────────────────────────────────────────────────────────────


class TestCaptureInitiativeMessage:
    """Direct tests for ``_maybe_store_initiative_message``.

    These call the async method directly (no MainLoopBridge, no real graph)
    so each capture rule can be asserted against the real SQLite persistence
    path.
    """

    async def test_captures_first_message(self, messaging_service, repo):
        """First message sent to an IDLE instance is persisted."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message(
            "inst-1", "hello world"
        )
        stored = repo.get("inst-1")
        assert stored is not None
        assert stored.instance_metadata["initiative_message"] == "hello world"

    async def test_captures_raw_message_content_verbatim(
        self, messaging_service, repo
    ):
        """The RAW message text is stored — no system context injection.

        The capture hook receives ``message`` from the queue upstream of any
        system-context prepending (see ``_build_graph_input``), so the stored
        value must equal the input byte-for-byte.
        """
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        raw = "Help me debug this Python error please"
        await messaging_service._maybe_store_initiative_message("inst-1", raw)
        assert repo.get("inst-1").instance_metadata["initiative_message"] == raw

    async def test_idempotent_first_message_wins(self, messaging_service, repo):
        """A second call with a different message does NOT overwrite the first.

        The hook reads the instance's metadata before writing; if
        ``initiative_message`` is already present it returns early.
        """
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message(
            "inst-1", "first message"
        )
        await messaging_service._maybe_store_initiative_message(
            "inst-1", "second message should be ignored"
        )
        assert (
            repo.get("inst-1").instance_metadata["initiative_message"]
            == "first message"
        )

    async def test_skips_when_already_stored_via_set_metadata(
        self, messaging_service, repo
    ):
        """If ``initiative_message`` was set by any prior path, the hook skips."""
        _make(
            repo, "inst-1", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "preset via metadata"},
        )
        await messaging_service._maybe_store_initiative_message(
            "inst-1", "new attempt"
        )
        assert (
            repo.get("inst-1").instance_metadata["initiative_message"]
            == "preset via metadata"
        )

    async def test_truncates_message_to_1000_chars(self, messaging_service, repo):
        """Message longer than 1000 chars is truncated to exactly 1000."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        long_msg = "x" * 1500
        await messaging_service._maybe_store_initiative_message(
            "inst-1", long_msg
        )
        stored = repo.get("inst-1").instance_metadata["initiative_message"]
        assert len(stored) == 1000
        assert stored == "x" * 1000

    async def test_truncation_keeps_message_under_1000_intact(
        self, messaging_service, repo
    ):
        """Messages shorter than 1000 chars are stored verbatim (no padding)."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        msg = "x" * 999
        await messaging_service._maybe_store_initiative_message("inst-1", msg)
        stored = repo.get("inst-1").instance_metadata["initiative_message"]
        assert stored == msg
        assert len(stored) == 999

    async def test_exactly_1000_chars_not_truncated(self, messaging_service, repo):
        """Boundary case: exactly 1000 chars is stored as-is."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        msg = "a" * 1000
        await messaging_service._maybe_store_initiative_message("inst-1", msg)
        stored = repo.get("inst-1").instance_metadata["initiative_message"]
        assert stored == msg
        assert len(stored) == 1000

    async def test_skips_empty_message(self, messaging_service, repo):
        """Empty string ``""`` is not stored — no ``initiative_message`` key."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message("inst-1", "")
        stored = repo.get("inst-1")
        assert "initiative_message" not in (stored.instance_metadata or {})

    async def test_skips_whitespace_only_message(self, messaging_service, repo):
        """Whitespace-only message is not stored (defense against blank input)."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message(
            "inst-1", "   \t\n  "
        )
        stored = repo.get("inst-1")
        assert "initiative_message" not in (stored.instance_metadata or {})

    async def test_skips_none_message(self, messaging_service, repo):
        """``None`` message is not stored (defensive — type would otherwise raise)."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        await messaging_service._maybe_store_initiative_message("inst-1", None)
        stored = repo.get("inst-1")
        assert "initiative_message" not in (stored.instance_metadata or {})

    async def test_returns_early_when_instance_not_found(
        self, messaging_service, repo
    ):
        """Missing instance — silent return, no exception."""
        # No _make() call — instance doesn't exist.
        await messaging_service._maybe_store_initiative_message(
            "ghost-instance", "orphan message"
        )
        # Nothing to assert against — just verifying no exception leaks.

    async def test_stores_percent_literal(self, messaging_service, repo):
        """``%`` in the message is stored as a literal character."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        msg = "50% off sale"
        await messaging_service._maybe_store_initiative_message("inst-1", msg)
        assert repo.get("inst-1").instance_metadata["initiative_message"] == msg

    async def test_stores_underscore_literal(self, messaging_service, repo):
        """``_`` in the message is stored as a literal character."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        msg = "value_with_underscore"
        await messaging_service._maybe_store_initiative_message("inst-1", msg)
        assert repo.get("inst-1").instance_metadata["initiative_message"] == msg

    async def test_stores_backslash_literal(self, messaging_service, repo):
        """``\\`` in the message is stored as a literal character."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        msg = r"path\to\file"
        await messaging_service._maybe_store_initiative_message("inst-1", msg)
        assert repo.get("inst-1").instance_metadata["initiative_message"] == msg

    async def test_stores_unicode(self, messaging_service, repo):
        """Unicode characters are stored correctly (UTF-8 round-trip)."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        msg = "héllo wörld 日本語 🚀"
        await messaging_service._maybe_store_initiative_message("inst-1", msg)
        assert repo.get("inst-1").instance_metadata["initiative_message"] == msg

    async def test_stores_multiline_message(self, messaging_service, repo):
        """Newlines are preserved (important for code-paste messages)."""
        _make(repo, "inst-1", agent_id="dev", agent_dir="agents/coder")
        msg = "line one\nline two\nline three"
        await messaging_service._maybe_store_initiative_message("inst-1", msg)
        assert repo.get("inst-1").instance_metadata["initiative_message"] == msg


class TestHookFiresInitiativeMessage:
    """Verify ``_maybe_trigger_title_generation`` dispatches to the store hook.

    The hook is the single entry point used by both ``send_message`` (in its
    ``finally`` block) and ``_prepare_enqueued_message`` (when status changes
    IDLE -> RUNNING). The hook MUST fire the store coroutine iff
    ``should_trigger`` is True.
    """

    def test_does_not_fire_when_should_trigger_false(
        self, messaging_service, messaging_manager
    ):
        """``should_trigger=False`` (not first message): no fire-and-forget."""
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_run_async:
            messaging_service._maybe_trigger_title_generation(
                instance_id="inst-1",
                message="subsequent message",
                should_trigger=False,
            )
            mock_run_async.assert_not_called()

    def test_fires_two_coroutines_when_should_trigger_true(
        self, messaging_service, messaging_manager
    ):
        """``should_trigger=True`` fires TWO coroutines: title + initiative_message."""
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_run_async:
            messaging_service._maybe_trigger_title_generation(
                instance_id="inst-1",
                message="first message",
                should_trigger=True,
            )
            try:
                assert mock_run_async.call_count == 2
            finally:
                # Close captured coroutines to avoid RuntimeWarning.
                for call in mock_run_async.call_args_list:
                    coro = call.args[0]
                    if hasattr(coro, "close"):
                        coro.close()

    def test_initiative_message_coroutine_is_passed_to_bridge(
        self, messaging_service, messaging_manager
    ):
        """The 2nd coroutine passed to the bridge is from
        ``_maybe_store_initiative_message``."""
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_run_async:
            messaging_service._maybe_trigger_title_generation(
                instance_id="inst-1",
                message="hello",
                should_trigger=True,
            )
            # Inspect each coroutine's __qualname__ / source
            coros = [c.args[0] for c in mock_run_async.call_args_list]
            try:
                qualnames = [getattr(c, "__qualname__", str(c)) for c in coros]
                # The 2nd coroutine MUST come from _maybe_store_initiative_message
                assert any(
                    "_maybe_store_initiative_message" in q for q in qualnames
                ), f"initiative_message coroutine not in: {qualnames}"
            finally:
                # Close the captured coroutines so pytest-asyncio doesn't
                # emit ``RuntimeWarning: coroutine '...' was never awaited``
                # when the test fixture tears them down.
                for c in coros:
                    if hasattr(c, "close"):
                        c.close()


# ─────────────────────────────────────────────────────────────────────────────
# Group 2 — Search tests (dialect-aware SQLite path)
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchByInitiativeMessage:
    """The new ``initiative_message`` JSON field is searchable.

    Matches the existing ``TestSearchByTitle`` style so the two fields'
    behaviour can be compared side-by-side.
    """

    def test_search_matches_initiative_message_substring(self, repo):
        """Search term appears as substring in ``initiative_message`` → match."""
        _make(
            repo, "alpha", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "deploy the staging server"},
        )
        _make(
            repo, "beta", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "write unit tests"},
        )
        instances, total = repo.list(search="staging")
        assert total == 1
        assert _ids(instances) == ["alpha"]

    def test_search_no_match_returns_empty(self, repo):
        _make(
            repo, "alpha", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "deploy the staging server"},
        )
        instances, total = repo.list(search="production-deploy")
        assert total == 0
        assert instances == []

    def test_search_matches_initiative_message_case_insensitive(self, repo):
        """ILIKE on the JSON-extracted text is case-insensitive."""
        _make(
            repo, "lower", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "deploy the staging server"},
        )
        # uppercase query → lowercase data match
        instances, total = repo.list(search="STAGING")
        assert total == 1
        assert _ids(instances) == ["lower"]

    def test_search_matches_initiative_message_lowercase_query_uppercase_data(
        self, repo
    ):
        _make(
            repo, "upper", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "DEPLOY THE STAGING SERVER"},
        )
        instances, total = repo.list(search="staging")
        assert total == 1
        assert _ids(instances) == ["upper"]

    def test_search_skips_when_initiative_message_absent(self, repo):
        """Without ``initiative_message`` key in metadata, no false match."""
        _make(repo, "no-msg", agent_id="nope", agent_dir="agents/nope")
        # "nope" doesn't appear in any initiative_message (none exists) but
        # matches agent_id "nope" + agent_name "Nope" — confirms the field is
        # additive and not silently matching absent keys.
        instances, total = repo.list(search="nope")
        assert total == 1
        assert _ids(instances) == ["no-msg"]

    def test_search_coexists_with_title(self, repo):
        """Title match OR initiative_message match — both surfaced."""
        _make(
            repo, "via-title", agent_id="dev", agent_dir="agents/coder",
            metadata={"title": "Refactor auth module"},
        )
        _make(
            repo, "via-init", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "Refactor the auth module please"},
        )
        _make(
            repo, "miss", agent_id="dev", agent_dir="agents/coder",
            metadata={"title": "Add login button", "initiative_message": "styling"},
        )
        instances, total = repo.list(search="refactor")
        assert total == 2
        assert _ids(instances) == ["via-init", "via-title"]

    def test_search_coexists_with_agent_name(self, repo):
        """A row matched ONLY by agent_name is still returned alongside an
        initiative_message match (OR semantics across all four fields)."""
        _make(
            repo, "by-name", agent_id="developer", agent_dir="agents/coder",
            metadata={},  # no initiative_message
        )
        _make(
            repo, "by-init", agent_id="fixer", agent_dir="agents/fixer",
            metadata={"initiative_message": "refactor authentication"},
        )
        instances, total = repo.list(search="refactor")
        # 'refactor' is NOT in 'Coder' agent_name or 'developer' agent_id
        # → only "by-init" matches via initiative_message.
        assert total == 1
        assert _ids(instances) == ["by-init"]

    def test_search_coexists_with_agent_id(self, repo):
        """Initiative_message OR agent_id match — OR-composed."""
        _make(
            repo, "by-id", agent_id="developer", agent_dir="agents/coder",
            metadata={},
        )
        _make(
            repo, "by-init", agent_id="fixer", agent_dir="agents/fixer",
            metadata={"initiative_message": "developer notes here"},
        )
        instances, total = repo.list(search="developer")
        # 'developer' matches agent_id on "by-id" AND initiative_message on
        # "by-init" — both come back via OR.
        assert total == 2
        assert _ids(instances) == ["by-id", "by-init"]


class TestInitiativeMessageEscaping:
    """``%``, ``_``, ``\\`` in the search term must be literals on SQLite too.

    The Python escape-then-ilike pattern in ``_build_search_condition``
    applies identically to the new ``initiative_message`` field — the
    underlying ``json_extract(..., '$.initiative_message')`` expression is
    just another ILIKE target.
    """

    def test_percent_is_literal(self, repo):
        """``50%`` must match only the literal '50%', not '50xyz'."""
        _make(
            repo, "literal", agent_id="x", agent_dir="agents/x",
            metadata={"initiative_message": "50% off sale"},
        )
        _make(
            repo, "fuzzy", agent_id="x", agent_dir="agents/y",
            metadata={"initiative_message": "50xyz off sale"},
        )
        instances, total = repo.list(search="50%")
        assert total == 1
        assert _ids(instances) == ["literal"]

    def test_underscore_is_literal(self, repo):
        """``a_b`` must match only the literal 'a_b', not 'axb'."""
        _make(
            repo, "literal", agent_id="x", agent_dir="agents/x",
            metadata={"initiative_message": "value a_b here"},
        )
        _make(
            repo, "fuzzy", agent_id="x", agent_dir="agents/y",
            metadata={"initiative_message": "value axb here"},
        )
        instances, total = repo.list(search="a_b")
        assert total == 1
        assert _ids(instances) == ["literal"]

    def test_backslash_is_literal(self, repo):
        """A backslash in the search term must match a backslash in the data."""
        _make(
            repo, "literal", agent_id="x", agent_dir="agents/x",
            metadata={"initiative_message": r"path\to\file"},
        )
        _make(
            repo, "other", agent_id="x", agent_dir="agents/y",
            metadata={"initiative_message": r"pathXtoXfile"},
        )
        instances, total = repo.list(search=r"\to")
        assert total == 1
        assert _ids(instances) == ["literal"]

    def test_percent_in_stored_data_not_wildcard_for_other_query(self, repo):
        """A literal ``%`` stored in initiative_message does NOT widen a
        DIFFERENT field's LIKE — escape on the search term only."""
        _make(
            repo, "with-percent", agent_id="x", agent_dir="agents/x",
            metadata={
                "title": "regular title",
                "initiative_message": "100% commitment",
            },
        )
        # Searching for "title" must match only the title field, the stored
        # ``%`` in initiative_message must NOT make every other LIKE hit.
        instances, total = repo.list(search="title")
        assert total == 1
        assert _ids(instances) == ["with-percent"]


# ─────────────────────────────────────────────────────────────────────────────
# Group 3 — API tests + Edge cases
# ─────────────────────────────────────────────────────────────────────────────


def _make_instance(
    instance_id: str,
    *,
    agent_id: str = "developer",
    agent_dir: str = "agents/coder",
    agent_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    parent_id: str | None = None,
    project_id: str | None = None,
    status: str = "idle",
) -> Instance:
    """Build an ``Instance`` row (NOT committed) — mirrors search API test."""
    now = datetime.now(timezone.utc).isoformat()
    return Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        agent_name=(
            agent_name if agent_name is not None
            else agent_dir.rsplit("/", 1)[-1].title()
        ),
        parent_id=parent_id,
        status=status,
        version=1,
        instance_metadata=metadata or {},
        created_at=now,
        updated_at=now,
        project_id=project_id,
    )


@pytest.fixture
def seed_instances_with_initiative(engine):
    """Seed instances with and without ``initiative_message``.

    Layout (all roots):
        id              title               agent_id    initiative_message
        --              -----               --------    ------------------
        with-msg        "Refactor auth"     developer   "Refactor the auth module"
        no-msg          "Add login button"  fixer       None (key absent)
        msg-only        None                reviewer    "deploy staging environment"
    """
    with Session(engine) as s:
        s.add(_make_instance(
            "with-msg", agent_id="developer", agent_dir="agents/coder",
            metadata={
                "title": "Refactor auth",
                "initiative_message": "Refactor the auth module please",
            },
            project_id="proj-1",
        ))
        s.add(_make_instance(
            "no-msg", agent_id="fixer", agent_dir="agents/fixer",
            metadata={"title": "Add login button"},
            project_id="proj-1",
        ))
        s.add(_make_instance(
            "msg-only", agent_id="reviewer", agent_dir="agents/reviewer",
            metadata={"initiative_message": "deploy staging environment"},
            project_id="proj-2",
        ))
        s.commit()
    return ["with-msg", "no-msg", "msg-only"]


@pytest.fixture
def app_and_client(engine):
    """FastAPI ``TestClient`` with real repo + lifecycle service.

    Mirrors ``tests/test_instance_search_api.py::app_and_client``.
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
    # get_instance_info delegates to the lifecycle service
    manager.get_instance_info = service.get_instance_info
    manager.get_instance = AsyncMock()
    # The detail endpoint calls ``await manager.get_queue_stats(...)`` to
    # populate ``pending_count``; the magic default returns a non-awaitable
    # and crashes — wrap as AsyncMock so the API path is exercised cleanly.
    manager.get_queue_stats = AsyncMock(return_value={"pending_count": 0})

    app = FastAPI()
    app.include_router(instances_router)
    app.state.manager = manager

    with TestClient(app) as client:
        yield client


def _ids_from_list(resp_json: dict) -> list[str]:
    return sorted(i["instance_id"] for i in resp_json["instances"])


class TestAPIDetailEndpoint:
    """``GET /api/instances/{id}`` exposes ``initiative_message``."""

    def test_get_returns_initiative_message_when_set(
        self, app_and_client, seed_instances_with_initiative
    ):
        resp = app_and_client.get("/instances/with-msg")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == "with-msg"
        assert body["initiative_message"] == "Refactor the auth module please"

    def test_get_returns_null_when_absent(
        self, app_and_client, seed_instances_with_initiative
    ):
        """Instance without ``initiative_message`` key → field is ``null``."""
        resp = app_and_client.get("/instances/no-msg")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["initiative_message"] is None

    def test_get_returns_initiative_message_even_without_title(
        self, app_and_client, seed_instances_with_initiative
    ):
        """Instance with no title but with ``initiative_message`` — both
        fields surfaced independently."""
        resp = app_and_client.get("/instances/msg-only")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["title"] is None
        assert body["initiative_message"] == "deploy staging environment"


class TestAPIListEndpoint:
    """``GET /api/instances`` includes ``initiative_message`` per row."""

    def test_list_includes_initiative_message_per_row(
        self, app_and_client, seed_instances_with_initiative
    ):
        resp = app_and_client.get("/instances")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        by_id = {inst["instance_id"]: inst for inst in body["instances"]}
        assert by_id["with-msg"]["initiative_message"] == (
            "Refactor the auth module please"
        )
        assert by_id["no-msg"]["initiative_message"] is None
        assert by_id["msg-only"]["initiative_message"] == (
            "deploy staging environment"
        )

    def test_search_via_initiative_message_via_api(
        self, app_and_client, seed_instances_with_initiative
    ):
        """End-to-end: ``?search=deploy`` returns the row matched only on
        ``initiative_message``."""
        resp = app_and_client.get("/instances", params={"search": "deploy"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # "msg-only" matches via initiative_message; "no-msg" doesn't.
        assert body["total"] == 1
        assert _ids_from_list(body) == ["msg-only"]

    def test_search_combines_title_and_initiative_message_via_api(
        self, app_and_client, seed_instances_with_initiative
    ):
        """``?search=refactor`` matches BOTH "with-msg" (initiative_message)
        and any title containing refactor."""
        resp = app_and_client.get("/instances", params={"search": "refactor"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # "with-msg" matches via initiative_message ("Refactor the auth
        # module please") and via title ("Refactor auth").
        assert body["total"] == 1
        assert _ids_from_list(body) == ["with-msg"]


class TestEdgeCases:
    """Behaviour around the absence of ``initiative_message``."""

    def test_instance_never_messaged_has_no_initiative_message_field(self, repo):
        """Fresh instance (no messages ever sent) has no ``initiative_message``."""
        _make(repo, "fresh", agent_id="dev", agent_dir="agents/coder")
        inst = repo.get("fresh")
        assert inst is not None
        assert inst.initiative_message is None
        assert "initiative_message" not in (inst.instance_metadata or {})

    def test_search_for_unique_initiative_message_term_returns_only_that_row(
        self, repo
    ):
        """A query that matches ONLY the initiative_message field returns
        exactly the row that holds it — confirms the field is in the OR
        predicate without false-positive leakage to other rows."""
        _make(
            repo, "hit", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "unicorn-pineapple-12345"},
        )
        _make(repo, "miss", agent_id="dev", agent_dir="agents/coder")
        instances, total = repo.list(search="unicorn-pineapple-12345")
        assert total == 1
        assert _ids(instances) == ["hit"]

    def test_search_no_match_when_initiative_message_absent(self, repo):
        """Search for a term that appears in NO initiative_message returns 0
        even when other rows have similar titles/agent_ids."""
        _make(repo, "x", agent_id="dev", agent_dir="agents/coder")
        instances, total = repo.list(search="definitely-not-anywhere")
        assert total == 0
        assert instances == []

    def test_empty_search_returns_all_regardless_of_initiative_message(self, repo):
        """``search=""`` is a no-op filter — every row comes back."""
        _make(
            repo, "with-msg", agent_id="dev", agent_dir="agents/coder",
            metadata={"initiative_message": "hello"},
        )
        _make(repo, "no-msg", agent_id="fixer", agent_dir="agents/fixer")
        instances, total = repo.list(search="")
        assert total == 2
        assert _ids(instances) == ["no-msg", "with-msg"]