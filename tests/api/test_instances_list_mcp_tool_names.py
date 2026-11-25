"""Contract tests for the LIST wire shape of ``mcp_tool_names``.

Option A of the instances-list latency analysis
(``.agents/shared/planning/sonic-team/instances-list-latency/ANALYSIS.md``
§8): ``GET /api/instances`` MUST NOT carry the ``mcp_tool_names`` key AT
ALL on its rows — **key-ABSENT, not ``null``**. On live pages that
field was ~2.26 KB per row (58 % of every row) and the frontend never
read it (0 hits in ``frontend/`` for either spelling). The DETAIL route
(``GET /api/instances/{instance_id}``) keeps serving it, populated.

Why both halves are needed (the mechanism is binding):

1. ``InstanceInfo.mcp_tool_names`` is ``Field(default=None)``, so
   merely deleting the constructor kwarg on the list route would
   serialize as ``"mcp_tool_names": null`` — which VIOLATES the
   key-absent contract. The route decorator therefore carries
   ``response_model_exclude={"instances": {"__all__": {"mcp_tool_names"}}}``
   (the ``__all__`` form is required: in pydantic v2 a plain
   ``{"instances": {"mcp_tool_names"}}`` is read as an INDEX mapping for
   the sequence and silently excludes nothing).
2. the list route no longer SOURCES the field at all.

Pins
----
(a) every LIST row omits the key (for a row seeded WITH
    ``metadata.mcp_tool_names``) — dies if the key reappears, including
    as ``null``;
(b) DETAIL of the same instance still carries the key, populated;
(c) an instance WITHOUT the metadata key: LIST row key-absent, DETAIL
    key present and ``null``;
(d) the response envelope is intact (``total`` / ``limit`` / ``offset``
    / ``has_more`` / ``truncated``) — the exclude must not leak;
(e) the LIST path never READS ``metadata["mcp_tool_names"]`` (the
    source half of the mechanism), while the DETAIL path still does.

Harness mirrors ``tests/api/test_list_instances_source_param.py`` +
``tests/api/test_instance_ui_prefs_api.py``: the real ``daemon.api``
FastAPI app, a manager stand-in on ``app.state.manager``, a real
in-memory SQLite engine (the seeded rows are the source of truth for
the metadata), and ``httpx.AsyncClient`` + ``ASGITransport``.

This pack runs in isolation — do not invoke it alongside the rest of
the suite::

    python -m pytest tests/api/test_instances_list_mcp_tool_names.py -v --tb=short -q
"""

from __future__ import annotations

import asyncio
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
# Auth posture — the RESPONSE SHAPE is under test, not the auth layer
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _auth_gate_off():
    """Neutralize the OAuth2 gate for the duration of this pack.

    The API-shape packs assume the daemon is built with auth OFF (the
    house default; ``config.yaml`` ships ``auth.enabled: false``). Some
    hosts carry ``ENSEMBLE_AUTH_ENABLED=true`` in the AMBIENT
    environment — the app singleton then registers ``AuthMiddleware`` at
    IMPORT time and every request 302s to the login page BEFORE the
    route under test is reached. That would be a false red about the
    response shape, not about the code under test.

    This fixture removes the gate (restoring it on teardown) so the pack
    measures the wire shape. The auth layer itself is NOT under test
    here and is exercised by the auth suites.
    """
    from daemon.middleware.auth import AuthMiddleware

    def _cls_of(entry: Any) -> Any:
        # Starlette stores ``Middleware`` wrappers (``.cls``) — older
        # shapes store the class itself.
        return getattr(entry, "cls", entry)

    removed = [e for e in list(app.user_middleware) if _cls_of(e) is AuthMiddleware]
    for entry in removed:
        app.user_middleware.remove(entry)

    # ``scope_required`` is inert iff ``app.state.auth_config`` is
    # absent (the authoritative OFF signal — see
    # ``daemon/auth/decorators.py::_auth_enabled``).
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

#: Seeded WITH ``metadata.mcp_tool_names`` — the row that proves the list
#: route drops a POPULATED field (not merely an absent one).
WITH_TOOLS_ID = "ilnames-with-tools-001"

#: Seeded WITHOUT the ``mcp_tool_names`` metadata key at all.
WITHOUT_TOOLS_ID = "ilnames-no-tools-001"

MCP_TOOL_NAMES = ["mcp_a", "mcp_b"]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with every SQLModel table created.

    ``StaticPool`` keeps a single connection alive across the asyncio
    boundary ASGITransport introduces (the list/detail routes read the
    seeded rows through the manager stand-in's own sessions).
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


@pytest.fixture
def seeded(engine: Engine) -> None:
    """Seed two instance rows.

    * ``WITH_TOOLS_ID`` carries ``metadata.mcp_tool_names`` (plus a
      ``model_override`` so the list route's other metadata reads are
      exercised too);
    * ``WITHOUT_TOOLS_ID`` carries metadata WITHOUT the
      ``mcp_tool_names`` key.

    The ``metadata`` column is the genuine source of the values the
    routes read — the manager stand-in below serializes these rows.
    """
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=WITH_TOOLS_ID,
                agent_id="developer",
                agent_dir="agents/developer",
                agent_name="developer",
                parent_id=None,
                status="running",
                version=1,
                instance_metadata={
                    "title": "row WITH mcp tool names",
                    "mcp_tool_names": list(MCP_TOOL_NAMES),
                    "model_override": "mcp_a",
                },
                created_at=now,
                updated_at=now,
            )
        )
        s.add(
            Instance(
                instance_id=WITHOUT_TOOLS_ID,
                agent_id="developer",
                agent_dir="agents/developer",
                agent_name="developer",
                parent_id=None,
                status="running",
                version=1,
                instance_metadata={"title": "row WITHOUT mcp tool names"},
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()


class _MetadataProbe(dict):
    """A ``metadata`` dict that records every KEY LOOKUP.

    The list route reads ``metadata`` through ``.get(...)`` calls; this
    probe records the key so a test can assert the list path never
    sources ``mcp_tool_names`` (pin ``e``) while the detail path still
    does. It is a plain ``dict`` subclass, so the router cannot tell it
    apart from a normal mapping.
    """

    def __init__(self, source: dict[str, Any], log: list[str]):
        super().__init__(source)
        self._log = log

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        self._log.append(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:
        self._log.append(key)
        return super().__getitem__(key)


class _ManagerStandin:
    """Minimal manager exposing exactly what the instances router touches.

    Attributes the router reads:

    * ``is_write_paused`` — always ``False``.
    * ``_instance_ui_prefs_repo`` — the REAL
      :class:`InstanceUiPrefsRepository` bound to the in-memory engine
      (the list route's prefs merge and the detail route's prefs read
      both run against it).
    * ``list_instances(...)`` — returns ``(rows, total, truncated)``; the
      rows are built from the SEEDED DB rows so ``metadata`` is the real
      column value.
    * ``get_instance_info(instance_id)`` — the detail route's row
      source; raises ``KeyError`` for unknown ids (→ 404).
    * ``get_queue_stats(instance_id)`` — async; ``{}`` so the detail
      route's ``pending_count`` resolves to ``None``.
    """

    def __init__(self, engine: Engine):
        self._engine = engine
        self._instance_ui_prefs_repo = InstanceUiPrefsRepository(engine=engine)
        #: Every ``metadata`` key read through the probe, in order.
        self.metadata_access_log: list[str] = []

    @property
    def is_write_paused(self) -> bool:
        return False

    def _row_dict(self, row: Instance) -> dict[str, Any]:
        return {
            "instance_id": row.instance_id,
            "agent_id": row.agent_id,
            "agent_dir": row.agent_dir,
            "status": row.status,
            "parent_id": row.parent_id,
            "title": row.title,
            "initiative_message": row.initiative_message,
            "children": [],
            "metadata": _MetadataProbe(
                row.instance_metadata or {}, self.metadata_access_log
            ),
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
async def client(engine: Engine, seeded: None):
    """Async httpx client on the real app with the stand-in manager.

    Yields ``(client, standin)``.
    """
    manager = _ManagerStandin(engine=engine)
    app.state.manager = manager
    app.state.start_time = 1000.0

    # Bind the db-offload seam to THIS test's event loop (house pattern —
    # see ``tests/unit/decision/conftest.py`` and
    # ``tests/unit/metrics/test_metrics_read_tools.py``). The list route
    # reads through ``_offload``; the seam captures the running loop ONCE
    # (lazily, on first use), so a sibling pack that already used it in
    # an earlier event loop leaves the gate bound to a DEAD loop and the
    # route raises "Future attached to a different loop". Re-binding per
    # test keeps the pack runnable in a shared-directory sweep as well
    # as in isolation.
    from daemon.services import db_offload as _db_offload

    _db_offload.configure(loop=asyncio.get_running_loop())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, manager


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _list_response(client: httpx.AsyncClient) -> httpx.Response:
    response = await client.get("/api/instances")
    assert response.status_code == 200, response.text
    return response


async def _list(client: httpx.AsyncClient) -> dict[str, Any]:
    return (await _list_response(client)).json()


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_rows_omit_the_mcp_tool_names_key(client):
    """(a) LIST rows carry NO ``mcp_tool_names`` key — not even ``null``.

    The seeded row WITH ``metadata.mcp_tool_names=["mcp_a","mcp_b"]`` is
    the load-bearing case: a regression that merely nulls the field
    (e.g. dropping the constructor kwarg without the route-level
    exclude) still fails here.
    """
    ac, _ = client

    response = await _list_response(ac)
    data = response.json()

    rows = data["instances"]
    assert rows, "expected the seeded rows to be returned"
    by_id = {row["instance_id"]: row for row in rows}
    assert WITH_TOOLS_ID in by_id, f"seeded row missing from list: {sorted(by_id)}"

    for row in rows:
        assert "mcp_tool_names" not in row, (
            f"LIST row {row['instance_id']} must not carry the "
            f"mcp_tool_names key at all (got {row.get('mcp_tool_names')!r})"
        )

    # The whole body must be free of the key — nothing may smuggle it
    # back under a different nesting.
    assert "mcp_tool_names" not in response.text


@pytest.mark.asyncio
async def test_detail_route_still_serves_mcp_tool_names(client):
    """(b) DETAIL of the seeded row keeps the key, populated."""
    ac, _ = client

    response = await ac.get(f"/api/instances/{WITH_TOOLS_ID}")

    assert response.status_code == 200, response.text
    data = response.json()
    assert "mcp_tool_names" in data, (
        "the DETAIL route must keep the mcp_tool_names key"
    )
    assert data["mcp_tool_names"] == MCP_TOOL_NAMES


@pytest.mark.asyncio
async def test_instance_without_metadata_key_is_key_absent_on_list(client):
    """(c) A row WITHOUT the metadata key: LIST key-absent, DETAIL null."""
    ac, _ = client

    data = await _list(ac)
    by_id = {row["instance_id"]: row for row in data["instances"]}
    assert WITHOUT_TOOLS_ID in by_id
    assert "mcp_tool_names" not in by_id[WITHOUT_TOOLS_ID]

    response = await ac.get(f"/api/instances/{WITHOUT_TOOLS_ID}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert "mcp_tool_names" in detail, (
        "the DETAIL route keeps the key (null when the metadata has none)"
    )
    assert detail["mcp_tool_names"] is None


@pytest.mark.asyncio
async def test_list_envelope_is_intact(client):
    """(d) The exclude must not leak into the response envelope."""
    ac, _ = client

    data = await _list(ac)

    for key in ("total", "limit", "offset", "has_more", "truncated"):
        assert key in data, f"envelope key {key!r} missing from the list response"
    assert isinstance(data["total"], int)
    assert data["total"] == 2
    assert isinstance(data["limit"], int)
    assert isinstance(data["offset"], int)
    assert isinstance(data["has_more"], bool)
    assert isinstance(data["truncated"], bool)


@pytest.mark.asyncio
async def test_list_never_sources_mcp_tool_names_from_metadata(client):
    """(e) The LIST path must not READ ``metadata["mcp_tool_names"]``.

    This is the source half of the mechanism: the route must not source
    the field at all (the detail route still does).
    """
    ac, manager = client

    manager.metadata_access_log.clear()
    await _list(ac)
    assert "mcp_tool_names" not in manager.metadata_access_log, (
        "the LIST route must not source metadata['mcp_tool_names']; "
        f"observed reads: {manager.metadata_access_log}"
    )

    # The detail route DOES source it — the field stays live there.
    manager.metadata_access_log.clear()
    response = await ac.get(f"/api/instances/{WITH_TOOLS_ID}")
    assert response.status_code == 200, response.text
    assert "mcp_tool_names" in manager.metadata_access_log, (
        "the DETAIL route must still source metadata['mcp_tool_names']"
    )
