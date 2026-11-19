"""Behavioral pin: the instances-list UI-prefs batch read runs OFF the event loop.

``GET /api/instances`` merges the per-instance UI preferences (pin / color
tag / icon tag) into every row of the page.  The merge reads the
``instance_ui_prefs`` table in ONE batch (``get_all`` → a single SELECT with
an ``IN (...)`` over the page's ids), but the repository is SYNCHRONOUS:
called straight from the route, its SELECT ran on the event loop and blocked
every other request for the duration of the query.

The fix routes that batch read through the house off-loop DB seam
(``daemon.services.db_offload.offload``) — a SECOND, route-local offload
call (the list body itself is already offloaded at the top of the route).

What is pinned here
-------------------
(a) BEHAVIORAL: the prefs batch read runs on a ``db-offload*`` worker
    thread, never on the event-loop thread.  The probe records
    ``threading.current_thread().name`` inside ``get_all``; a revert to the
    on-loop call records the loop thread's name instead and the assertions
    below FAIL.
(b) The merge itself is UNCHANGED: a prefs row still lands on its own
    instance (``pinned`` / ``color_tag`` / ``icon_tag`` / ``pinned_at``),
    and an instance without a prefs row still gets ``None`` for all four.
(c) The DEGENERATE paths are off-loop too: a page whose instances carry NO
    prefs rows, and an EMPTY page.
(d) The probe has TEETH: a direct (on-loop) call to the SAME instrumented
    ``get_all`` is recorded on the loop thread — so (a) is not vacuous.
(e) Fence (1): the read is a SECOND, route-local offload — it was NOT folded
    into ``manager.list_instances`` (that would widen the change into
    ``daemon/manager.py`` for no gain).

Harness mirrors ``tests/api/test_instances_list_mcp_tool_names.py``: the real
``daemon.api`` FastAPI app, a manager stand-in on ``app.state.manager``, a
real in-memory SQLite engine (the seeded rows are the source of truth), and
``httpx.AsyncClient`` + ``ASGITransport``.

Run in isolation::

    python -m pytest tests/api/test_instances_list_offload_prefs.py -q --tb=short
"""

from __future__ import annotations

import asyncio
import inspect
import re
import threading
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import httpx

from daemon.api import app
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance_ui_prefs.repository import (
    InstanceUiPrefsRepository,
)


# ─────────────────────────────────────────────────────────────────────────────
# Auth posture — the OFF-LOOP behaviour is under test, not the auth layer
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _auth_gate_off():
    """Neutralize the OAuth2 gate for the duration of this pack.

    The app singleton registers ``AuthMiddleware`` at IMPORT time when
    ``ENSEMBLE_AUTH_ENABLED`` is set in the AMBIENT environment; every
    request then 302s to the login page BEFORE the route under test is
    reached — a false red about the code under test.  The auth layer
    itself is NOT under test here.
    """
    from daemon.middleware.auth import AuthMiddleware

    def _cls_of(entry: Any) -> Any:
        # Starlette stores ``Middleware`` wrappers (``.cls``) — older
        # shapes store the class itself.
        return getattr(entry, "cls", entry)

    removed = [e for e in list(app.user_middleware) if _cls_of(e) is AuthMiddleware]
    for entry in removed:
        app.user_middleware.remove(entry)

    # ``scope_required`` is inert iff ``app.state.auth_config`` is absent
    # (the authoritative OFF signal — ``daemon/auth/decorators.py``).
    original_cfg = getattr(app.state, "auth_config", None)
    if original_cfg is not None:
        app.state.auth_config = None

    if removed:
        # Force Starlette to rebuild the middleware stack on the next
        # request (it is built lazily and cached on the app).
        app.middleware_stack = None

    try:
        yield
    finally:
        if removed:
            for entry in removed:
                app.user_middleware.insert(0, entry)
            app.middleware_stack = None
        if original_cfg is not None:
            app.state.auth_config = original_cfg


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

#: Seeded WITH a UI-prefs row (pinned + color tag + icon tag).
PINNED_ID = "iloffload-pinned-001"

#: Seeded WITHOUT any UI-prefs row — the merge must fall back to ``None``.
PLAIN_ID = "iloffload-plain-001"

COLOR_TAG = "red"
ICON_TAG = "star"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with every SQLModel table created.

    ``StaticPool`` keeps a single connection alive across the asyncio
    boundary ASGITransport introduces; ``check_same_thread=False`` lets the
    offload worker threads share it (the list body is already read off-loop
    by the route, so cross-thread access is pre-existing).
    """
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


def _seed_instances(engine: Engine, instance_ids: list[str]) -> None:
    """Seed one ``Instance`` row per id (the source of truth for the page)."""
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        for iid in instance_ids:
            s.add(
                Instance(
                    instance_id=iid,
                    agent_id="developer",
                    agent_dir="agents/developer",
                    agent_name="developer",
                    parent_id=None,
                    status="running",
                    version=1,
                    instance_metadata={"title": f"row {iid}"},
                    created_at=now,
                    updated_at=now,
                )
            )
        s.commit()


class _RecordingPrefsRepo:
    """The REAL prefs repository, instrumented to record WHERE it ran.

    The router reaches the repository through
    ``manager._instance_ui_prefs_repo.get_all(...)``; this wrapper keeps
    that exact attribute name (and proxies every other method to the real
    repository) while recording ``threading.current_thread().name`` at the
    moment the batch read actually executes.

    That recorded name is the load-bearing signal of this pack: a
    ``db-offload*`` name means the read left the event loop; the loop
    thread's name (``MainThread``) means it did NOT.
    """

    def __init__(self, engine: Engine):
        self._repo = InstanceUiPrefsRepository(engine=engine)
        #: One entry per ``get_all`` call: ``thread`` + the requested ids.
        self.calls: list[dict[str, Any]] = []

    def get_all(self, instance_ids: list[str]) -> dict[str, Any]:
        self.calls.append(
            {
                "thread": threading.current_thread().name,
                "instance_ids": list(instance_ids),
            }
        )
        return self._repo.get_all(instance_ids)

    def __getattr__(self, name: str) -> Any:
        # Everything else (upsert / get / delete) goes to the REAL repo.
        return getattr(self._repo, name)


class _ManagerStandin:
    """Minimal manager exposing exactly what the instances router touches.

    * ``is_write_paused`` — always ``False``.
    * ``_instance_ui_prefs_repo`` — the instrumented
      :class:`InstanceUiPrefsRepository` (the list route's batch prefs read
      runs against it).
    * ``list_instances(...)`` — returns ``(rows, total, truncated)``; the
      rows are built from the SEEDED DB rows.
    * ``get_instance_info(instance_id)`` — the detail route's row source;
      raises ``KeyError`` for unknown ids (→ 404).
    * ``get_queue_stats(instance_id)`` — async; ``{}`` so the detail
      route's ``pending_count`` resolves to ``None``.
    """

    def __init__(self, engine: Engine):
        self._engine = engine
        self._instance_ui_prefs_repo = _RecordingPrefsRepo(engine=engine)

    @property
    def is_write_paused(self) -> bool:
        return False

    @staticmethod
    def _row_dict(row: Instance) -> dict[str, Any]:
        return {
            "instance_id": row.instance_id,
            "agent_id": row.agent_id,
            "agent_dir": row.agent_dir,
            "status": row.status,
            "parent_id": row.parent_id,
            "title": row.title,
            "initiative_message": row.initiative_message,
            "children": [],
            "metadata": dict(row.instance_metadata or {}),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "project_id": row.project_id,
        }

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
    ) -> tuple[list[dict[str, Any]], int, bool]:
        with Session(self._engine) as s:
            rows = s.exec(select(Instance).order_by(Instance.created_at)).all()
        return [self._row_dict(r) for r in rows], len(rows), False

    def get_instance_info(self, instance_id: str) -> dict[str, Any]:
        with Session(self._engine) as s:
            row = s.get(Instance, instance_id)
        if row is None:
            raise KeyError(instance_id)
        return self._row_dict(row)

    async def get_queue_stats(self, instance_id: str) -> dict[str, Any]:
        return {}


@pytest_asyncio.fixture
async def client(engine: Engine):
    """Async httpx client on the real app with the stand-in manager.

    Yields ``(client, standin)``.
    """
    manager = _ManagerStandin(engine=engine)
    app.state.manager = manager
    app.state.start_time = 1000.0

    # Bind the db-offload seam to THIS test's event loop (house pattern —
    # see ``tests/api/test_instances_list_mcp_tool_names.py``). The seam
    # captures the running loop ONCE (lazily, on first use), so a sibling
    # pack that already used it in an earlier event loop leaves the gate
    # bound to a DEAD loop and the route raises "Future attached to a
    # different loop". Re-binding per test keeps the pack runnable in a
    # shared-directory sweep as well as in isolation.
    from daemon.services import db_offload as _db_offload

    _db_offload.configure(loop=asyncio.get_running_loop())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, manager


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _assert_read_off_loop(calls: list[dict[str, Any]]) -> None:
    """Every recorded ``get_all`` must have run on a ``db-offload*`` thread."""
    assert calls, "the prefs batch read never ran — the route skipped it"
    for call in calls:
        assert call["thread"].startswith("db-offload"), (
            "the UI-prefs batch read must run OFF the event loop — it ran on "
            f"{call['thread']!r} (a revert to the on-loop call breaks this)"
        )


async def _list(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("/api/instances")
    assert response.status_code == 200, response.text
    return response.json()


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prefs_batch_read_runs_off_the_event_loop(engine, client):
    """(a)+(b) Happy path: the batch read is off-loop AND the merge is intact."""
    ac, manager = client
    _seed_instances(engine, [PINNED_ID, PLAIN_ID])
    manager._instance_ui_prefs_repo.upsert(
        PINNED_ID, pinned=True, color_tag=COLOR_TAG, icon_tag=ICON_TAG
    )

    data = await _list(ac)

    # (a) the load-bearing assertion — the read left the event loop.
    calls = manager._instance_ui_prefs_repo.calls
    _assert_read_off_loop(calls)
    assert calls[0]["instance_ids"] == [PINNED_ID, PLAIN_ID], (
        "the batch read must cover the whole page's ids in ONE call"
    )

    # (b) the merge is UNCHANGED: prefs land on their own instance only.
    rows = {row["instance_id"]: row for row in data["instances"]}
    assert set(rows) == {PINNED_ID, PLAIN_ID}, sorted(rows)

    pinned_row = rows[PINNED_ID]
    assert pinned_row["pinned"] is True
    assert pinned_row["color_tag"] == COLOR_TAG
    assert pinned_row["icon_tag"] == ICON_TAG
    assert pinned_row["pinned_at"] is not None

    plain_row = rows[PLAIN_ID]
    assert plain_row["pinned"] is None
    assert plain_row["color_tag"] is None
    assert plain_row["icon_tag"] is None
    assert plain_row["pinned_at"] is None


@pytest.mark.asyncio
async def test_page_without_prefs_rows_still_reads_off_the_event_loop(engine, client):
    """(c) No prefs rows at all: still off-loop, still ``None`` everywhere."""
    ac, manager = client
    _seed_instances(engine, [PINNED_ID, PLAIN_ID])

    data = await _list(ac)

    _assert_read_off_loop(manager._instance_ui_prefs_repo.calls)

    rows = {row["instance_id"]: row for row in data["instances"]}
    assert set(rows) == {PINNED_ID, PLAIN_ID}, sorted(rows)
    for row in rows.values():
        assert row["pinned"] is None
        assert row["color_tag"] is None
        assert row["icon_tag"] is None
        assert row["pinned_at"] is None


@pytest.mark.asyncio
async def test_empty_page_still_reads_prefs_off_the_event_loop(client):
    """(c) An EMPTY page: the degenerate read is off-loop too."""
    ac, manager = client

    data = await _list(ac)

    assert data["instances"] == []
    assert data["total"] == 0

    calls = manager._instance_ui_prefs_repo.calls
    _assert_read_off_loop(calls)
    assert calls[0]["instance_ids"] == [], (
        "an empty page must not ask the repo for anything"
    )


@pytest.mark.asyncio
async def test_the_probe_sees_the_loop_thread_when_called_directly(engine, client):
    """(d) TEETH: an on-loop call is recorded on the loop thread.

    Without this, (a) could be vacuous (e.g. if the probe always saw an
    off-loop thread for any reason).  Here the SAME instrumented
    ``get_all`` is invoked straight from the event-loop thread — the
    recorded name must NOT be a ``db-offload*`` one.
    """
    ac, manager = client
    _seed_instances(engine, [PINNED_ID])
    loop_thread = threading.current_thread().name

    manager._instance_ui_prefs_repo.get_all([PINNED_ID])

    calls = manager._instance_ui_prefs_repo.calls
    assert len(calls) == 1
    assert calls[0]["thread"] == loop_thread, (
        "a direct call must be recorded on the calling (loop) thread"
    )
    assert not calls[0]["thread"].startswith("db-offload")


@pytest.mark.asyncio
async def test_the_route_source_offloads_the_prefs_read(client):
    """(e) Fence (1): a SECOND, route-local offload — not folded into the manager."""
    from daemon.routers import instances as instances_router

    source = inspect.getsource(instances_router.list_instances)
    flat = " ".join(source.split())

    assert re.search(
        r"prefs_map\s*=\s*await\s+_offload\s*\(\s*"
        r"manager\._instance_ui_prefs_repo\.get_all\s*,\s*instance_ids\s*\)",
        source,
    ), (
        "the prefs batch read must be an ``await _offload(...)`` call in the "
        f"route — got: {flat[flat.find('prefs_map') - 80:][:200]!r}"
    )

    # The read must NOT have been folded into ``manager.list_instances``
    # (that would have widened the change into ``daemon/manager.py``).
    from daemon.manager import InstanceManager

    manager_source = inspect.getsource(InstanceManager.list_instances)
    assert "_instance_ui_prefs_repo" not in manager_source, (
        "the prefs read must stay a route-local offload — it must NOT be "
        "folded into manager.list_instances"
    )
