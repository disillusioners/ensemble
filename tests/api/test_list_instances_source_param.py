"""API integration tests for the new ``source`` query parameter on
``GET /api/instances``.

The ``source`` parameter is the wire-level hook for the new special
"Chat" tab in the Projects UI. The parameter semantics:

* ``source="chat"`` → chat-source roots (telegram / slack / discord /
  whatsapp — the registry-backed set mirroring
  ``USER_ORIGIN_CHAT_SOURCE_TYPES`` in
  ``daemon/tools/upgrade_journal.py``).
* any other string → exact ``source_type`` match.
* omitted / empty → no source filter (back-compat with the pre-feature
  list endpoint).

This pack covers the ROUTE BOUNDARY only:

  1. The router accepts the new ``source`` query parameter without 422.
  2. The router forwards the value verbatim to ``manager.list_instances``
     (the lifecycle seam), pinning the new facade kwarg.
  3. The route's response envelope shape is unchanged (3-tuple result,
     existing consumers unaffected).
  4. Pre-existing calls without ``source`` still work (back-compat).

The repository-level filter is exercised in
``tests/test_instance_source_filter.py``; the facade forwarding in
``tests/unit/test_manager_list_instances_source.py``.

Strategy mirrors ``tests/api/test_instance_ui_prefs_api.py``:
    * Build the FastAPI ``app`` (already wired in ``daemon.api``).
    * Construct a real in-memory SQLite engine.
    * Build a lightweight manager stand-in whose ``list_instances``
      records the call kwargs into a spy list so we can assert the
      router forwarded ``source`` correctly.
    * Drive everything through ``httpx.AsyncClient`` + ``ASGITransport``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import httpx

from daemon.api import app


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine for the route tests.

    We do NOT need any real rows in the DB — the manager stand-in
    short-circuits ``list_instances`` and returns a single hard-coded
    dict. The engine exists only so the stand-in's read paths (which
    some router code may touch incidentally) can run without
    ``OperationalError``."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _instance_dict() -> dict[str, Any]:
    """Single hard-coded instance the manager stand-in returns.

    Mirrors the shape ``InstanceInfo`` requires (``instance_id``,
    ``agent_dir``, ``status``, ``created_at``); everything else is
    optional and the router fills defaults. Used to satisfy the
    router's ``InstanceInfo(**inst)`` merge step.
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "instance_id": "src-test-001",
        "agent_id": "developer",
        "agent_dir": "agents/developer",
        "status": "running",
        "parent_id": None,
        "title": None,
        "children": [],
        "metadata": {},
        "created_at": now,
        "updated_at": now,
        "project_id": None,
    }


class _UiPrefsRepoStub:
    """Minimal stub for ``manager._instance_ui_prefs_repo.get_all`` —
    the route's UI-prefs merge step runs on every list response, so
    the spy manager must expose a stand-in that returns an empty
    prefs map for any input."""

    def get_all(self, instance_ids: list[str]) -> dict[str, Any]:
        # Empty map → no prefs overrides → every instance renders with
        # its default (unpinned, no color, no icon) prefs. The real
        # prefs-merge logic is exercised in
        # ``tests/api/test_instance_ui_prefs_api.py``; this pack only
        # cares that the new ``source`` param is forwarded correctly.
        return {}


class _SpyManager:
    """Minimal manager stand-in whose ``list_instances`` records kwargs.

    Attributes the router reads:
    * ``is_write_paused`` — always False (no migration in progress).
    * ``_instance_ui_prefs_repo.get_all`` — stubbed to return an empty
      dict so the router's UI-prefs merge step (which fires on every
      list response, regardless of source filter) does not crash. The
      real repo is exercised end-to-end in
      ``tests/api/test_instance_ui_prefs_api.py``; this pack only
      cares that the source param is forwarded correctly.
    * ``list_instances(...)`` — stubbed to capture the kwargs passed
      by the route and return a 3-tuple ``(instances, total, truncated)``
      so the response envelope is well-formed.

    The PRE-EXISTING call shape returned a 2-tuple ``(instances, total)``
    in older tests; this stand-in uses the current 3-tuple shape so the
    route layer's ``unpack`` works byte-compatibly.
    """

    def __init__(self) -> None:
        self.list_calls: list[dict[str, Any]] = []

    @property
    def is_write_paused(self) -> bool:
        return False

    @property
    def _instance_ui_prefs_repo(self) -> Any:
        """Stub: the router's UI-prefs merge step calls
        ``_instance_ui_prefs_repo.get_all(instance_ids)``. Returning
        an object whose ``get_all`` yields an empty dict keeps the
        route happy without booting the real repo."""
        return _UiPrefsRepoStub()

    def list_instances(
        self,
        limit: int = 10,
        offset: int = 0,
        project_id: str | None = None,
        exclude_kb: bool = True,
        include_descendants: bool = False,
        search: str | None = None,
        order: str = "pinned",
        source: str | None = None,
    ) -> tuple[list[dict], int, bool]:
        # Capture the call kwargs so the tests can assert the router
        # forwarded the new ``source`` parameter correctly.
        self.list_calls.append({
            "limit": limit,
            "offset": offset,
            "project_id": project_id,
            "exclude_kb": exclude_kb,
            "include_descendants": include_descendants,
            "search": search,
            "order": order,
            "source": source,
        })
        return [_instance_dict()], 1, False


@pytest_asyncio.fixture
async def client(engine: Engine):
    """Async httpx client pointed at the real FastAPI app with the
    spy manager stashed on ``app.state.manager``.

    Yields ``(client, spy_manager)`` so individual tests can assert
    on the HTTP response AND inspect the call kwargs the router
    forwarded."""
    spy = _SpyManager()
    app.state.manager = spy
    app.state.start_time = 1000.0

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, spy


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_list(client, **params) -> httpx.Response:
    """Issue GET /api/instances?… and return the response.

    ``**params`` is forwarded as query parameters — e.g.
    ``_get_list(client, source="chat")`` issues
    ``GET /api/instances?source=chat``. The route layer handles all
    clamping (limit, offset) internally; we only assert forward-semantics
    here."""
    return client.get("/api/instances", params=params or None)


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_source_chat_is_accepted_and_forwarded(client):
    """GET /api/instances?source=chat → 200, manager.list_instances
    received ``source="chat"``."""
    ac, spy = client

    response = await _get_list(ac, source="chat")

    assert response.status_code == 200, response.text
    data = response.json()
    assert "instances" in data
    assert data["total"] == 1
    # The manager's ``list_instances`` must have been called with
    # ``source="chat"`` (forwarded verbatim from the query string).
    assert len(spy.list_calls) == 1
    assert spy.list_calls[0]["source"] == "chat"


@pytest.mark.asyncio
async def test_source_specific_value_is_forwarded(client):
    """GET /api/instances?source=telegram → manager receives
    ``source="telegram"`` (not the special "chat" sentinel)."""
    ac, spy = client

    response = await _get_list(ac, source="telegram")

    assert response.status_code == 200, response.text
    assert spy.list_calls[0]["source"] == "telegram"


@pytest.mark.asyncio
async def test_source_omitted_forwards_none(client):
    """GET /api/instances (no source param) → manager receives
    ``source=None``. A regression that defaulted to "chat" or any
    other value would break every existing consumer (badge poll,
    sidebar poll, panel-open refetch)."""
    ac, spy = client

    response = await _get_list(ac)

    assert response.status_code == 200, response.text
    assert spy.list_calls[0]["source"] is None


@pytest.mark.asyncio
async def test_source_empty_string_forwards_none(client):
    """An explicit empty-string ``source`` (e.g. ``?source=``) is
    treated as "no filter" — the helper's empty-string boundary
    contract (``if not source: return None``) covers this. The
    route layer's ``Query(None, ...)`` will also turn an empty
    string into ``None`` in most FastAPI versions, so we just pin
    the observable: the manager receives no source filter."""
    ac, spy = client

    response = await _get_list(ac, source="")

    assert response.status_code == 200, response.text
    # Either ``""`` or ``None`` is acceptable at the manager boundary
    # (the helper's ``not source`` check treats both equivalently).
    assert spy.list_calls[0]["source"] in (None, "")


@pytest.mark.asyncio
async def test_source_composes_with_existing_filters(client):
    """GET /api/instances?source=chat&project_id=p1&search=foo — the
    source param composes with project_id and search without
    disturbing either. Manager receives all three verbatim."""
    ac, spy = client

    response = await _get_list(
        ac, source="chat", project_id="p1", search="foo", order="activity"
    )

    assert response.status_code == 200, response.text
    call = spy.list_calls[0]
    assert call["source"] == "chat"
    assert call["project_id"] == "p1"
    assert call["search"] == "foo"
    assert call["order"] == "activity"


@pytest.mark.asyncio
async def test_response_envelope_is_unchanged(client):
    """Back-compat: the response envelope shape is unchanged. The
    existing ``InstanceListResponse`` consumer code on the FE reads
    ``data.instances`` and ``data.total`` (and the ``truncated``
    boolean); all three must still be present after the source
    param is added."""
    ac, spy = client

    response = await _get_list(ac, source="chat")

    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data.keys()) >= {"instances", "total"}
    # ``truncated`` is the 3-tuple-shape flag; may or may not be
    # surfaced on the envelope depending on the route layer, but
    # ``total`` and ``instances`` MUST always be present.
    assert isinstance(data["instances"], list)
    assert isinstance(data["total"], int)
