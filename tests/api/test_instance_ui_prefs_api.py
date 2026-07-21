"""API integration tests for the instance UI preferences endpoints.

End-to-end coverage (through the real HTTP API) for:

    * ``PUT    /api/instances/{instance_id}/ui-prefs``  — partial upsert
    * ``DELETE /api/instances/{instance_id}/ui-prefs``  — removes the row
    * ``GET    /api/instances``                          — list (merged prefs)
    * ``GET    /api/instances/{instance_id}``            — single (merged prefs)

The repository layer (``InstanceUiPrefsRepository``) has its own dedicated
test suite (19/19 passing) including the **C1 fix** — an explicit
``color_tag=null`` in an upsert must CLEAR the tag rather than preserve it.
This pack verifies that the C1 fix is reachable through the HTTP API by
exercising the router's ``clear_color_tag`` translation logic
(``"color_tag" in body.model_fields_set and body.color_tag is None``) against
the real repo and a real SQLite engine.

Strategy (mirrors ``tests/api/test_projects.py`` + ``tests/test_agents_api.py``):
    * Build the FastAPI ``app`` (already wired in ``daemon.api``).
    * Construct a real in-memory SQLite engine and create all tables.
    * Seed a single ``Instance`` row so the GET list/single endpoints have
      something to return.
    * Build a lightweight *manager stand-in* that exposes the attributes
      the router actually touches — ``is_write_paused`` (False),
      ``_instance_ui_prefs_repo`` (the REAL repo, so the C1 path is
      exercised), ``get_instance`` (async, exists), ``list_instances``,
      ``get_instance_info``, ``get_queue_stats`` — and stash it on
      ``app.state.manager``.
    * Drive everything through ``httpx.AsyncClient`` + ``ASGITransport``.

This pack runs in isolation — do not invoke with ``-x`` or alongside the
rest of the suite:

    python -m pytest tests/api/test_instance_ui_prefs_api.py -v --tb=short -q
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import httpx

from daemon.api import app
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance_ui_prefs.repository import (
    InstanceUiPrefsRepository,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


INSTANCE_ID = "ui-prefs-test-instance-001"


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with every SQLModel table created.

    ``StaticPool`` keeps a single connection alive for the test so reads
    after writes see the latest data even across the asyncio boundary that
    ASGITransport introduces (mirrors
    ``tests/test_instance_hard_delete.py::engine`` and
    ``tests/repositories/conftest.py::engine``).
    """
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
def seeded_instance(engine: Engine) -> str:
    """Seed one instance row and return its ID.

    Both ``list_instances`` and ``get_instance_info`` (as stubbed below)
    read from this row, so the GET list/single endpoints have stable data
    to merge the prefs fields into.
    """
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=INSTANCE_ID,
                agent_id="developer",
                agent_dir="agents/developer",
                agent_name="developer",
                parent_id=None,
                status="running",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()
    return INSTANCE_ID


def _instance_dict(instance_id: str = INSTANCE_ID) -> dict[str, Any]:
    """Static dict shape consumed by the router's list/single merge step.

    ``InstanceInfo`` requires ``instance_id``, ``agent_dir``, ``status``,
    and ``created_at``; the router also reads ``parent_id``, ``title``,
    ``children``, ``metadata``, ``updated_at``, ``project_id`` with
    ``.get(...)`` so they may be absent. We provide the minimal valid set.
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "instance_id": instance_id,
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


class _ManagerStandin:
    """Minimal manager the ui-prefs router paths actually touch.

    Attributes the router reads (verified by reading
    ``daemon/routers/instances.py``):

    * ``is_write_paused`` — property returning ``False`` (we are not mid
      migration).
    * ``_instance_ui_prefs_repo`` — the REAL
      :class:`InstanceUiPrefsRepository`, bound to the in-memory engine.
      This is the whole point: the C1 ``color_tag=null`` clearing logic
      lives in the repo, and only routing through the real repo proves the
      fix is reachable from the API.
    * ``get_instance(instance_id)`` — async; called by
      ``_check_instance_exists``. Raises ``KeyError`` for unknown IDs so
      the 404 path still works.
    * ``list_instances(...)`` — returns ``(list[dict], total)``. Stubbed to
      the seeded row so the list endpoint's merge step has a target.
    * ``get_instance_info(instance_id)`` — returns a dict (or raises
      ``KeyError``).
    * ``get_queue_stats(instance_id)`` — async; returns ``{}`` so the
      single-instance GET's ``pending_count`` resolves to ``None``.
    """

    def __init__(self, engine: Engine):
        self._engine = engine
        # The real repo — exercises the C1 fix path end-to-end.
        self._instance_ui_prefs_repo = InstanceUiPrefsRepository(engine=engine)

    @property
    def is_write_paused(self) -> bool:
        return False

    async def get_instance(self, instance_id: str) -> Any:
        # Existence check driven by the DB so an unknown id raises KeyError
        # → router maps to 404 (matches _check_instance_exists contract).
        with Session(self._engine) as s:
            row = s.get(Instance, instance_id)
        if row is None:
            raise KeyError(instance_id)
        return row

    def list_instances(
        self,
        limit: int = 10,
        offset: int = 0,
        project_id: str | None = None,
        exclude_kb: bool = True,
        include_descendants: bool = False,
    ) -> tuple[list[dict], int]:
        # Return the seeded instance as the single page entry. We do not
        # honor limit/offset/project_id because the pack only seeds one
        # instance and asserts on that one instance's merged fields.
        return [_instance_dict()], 1

    def get_instance_info(self, instance_id: str) -> dict[str, Any]:
        with Session(self._engine) as s:
            row = s.get(Instance, instance_id)
        if row is None:
            raise KeyError(instance_id)
        return _instance_dict(instance_id)

    async def get_queue_stats(self, instance_id: str) -> dict[str, Any]:
        return {}


@pytest_asyncio.fixture
async def client(engine: Engine, seeded_instance: str):
    """Async httpx client pointed at the real FastAPI app.

    Yields ``(client, instance_id, engine)`` so individual tests can assert
    on the HTTP response and (if needed) inspect the DB directly.
    """
    manager = _ManagerStandin(engine=engine)
    app.state.manager = manager
    app.state.start_time = 1000.0

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, seeded_instance, engine


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _put(client, instance_id: str, body: dict):
    """Issue a PUT to /api/instances/{id}/ui-prefs and return the response."""
    return client.put(f"/api/instances/{instance_id}/ui-prefs", json=body)


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_pinned_true_returns_pinned_and_stamps_pinned_at(client):
    """PUT {"pinned": true} → 200, pinned true, pinned_at is non-null ISO-8601."""
    client, instance_id, _ = client

    response = await _put(client, instance_id, {"pinned": True})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["instance_id"] == instance_id
    assert data["pinned"] is True
    pinned_at = data["pinned_at"]
    assert pinned_at is not None, "pinned_at must be stamped when pinning"
    # Must round-trip as ISO-8601 (datetime.fromisoformat accepts the
    # Z-suffixed or +00:00 form the repo emits).
    parsed = datetime.fromisoformat(pinned_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, "pinned_at must carry timezone info"


@pytest.mark.asyncio
async def test_put_color_tag_red(client):
    """PUT {"color_tag": "red"} → 200, color_tag == "red"."""
    client, instance_id, _ = client

    response = await _put(client, instance_id, {"color_tag": "red"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["color_tag"] == "red"


@pytest.mark.asyncio
async def test_put_color_tag_null_CLEARS_tag(client):
    """CRITICAL (was C1 bug): explicit null clears the tag, not preserves it.

    Set color_tag=red, then PUT color_tag=null → response color_tag == None.
    The router must translate the explicit null into clear_color_tag=True,
    and the repo must honor it. If the C1 fix regressed, color_tag would
    still be "red" here.
    """
    client, instance_id, _ = client

    set_resp = await _put(client, instance_id, {"color_tag": "red"})
    assert set_resp.status_code == 200
    assert set_resp.json()["color_tag"] == "red"

    clear_resp = await _put(client, instance_id, {"color_tag": None})
    assert clear_resp.status_code == 200, clear_resp.text
    assert clear_resp.json()["color_tag"] is None, (
        "C1 regression: explicit color_tag=null must CLEAR the tag, "
        "not preserve it"
    )


@pytest.mark.asyncio
async def test_put_pinned_false_clears_pinned_at(client):
    """Pin then unpin → pinned false AND pinned_at None."""
    client, instance_id, _ = client

    pin_resp = await _put(client, instance_id, {"pinned": True})
    assert pin_resp.status_code == 200
    assert pin_resp.json()["pinned_at"] is not None

    unpin_resp = await _put(client, instance_id, {"pinned": False})
    assert unpin_resp.status_code == 200, unpin_resp.text
    data = unpin_resp.json()
    assert data["pinned"] is False
    assert data["pinned_at"] is None, (
        "pinned_at must be cleared when pinning is set to false"
    )


@pytest.mark.asyncio
async def test_delete_ui_prefs_removes_row(client):
    """PUT prefs, DELETE → {"deleted": true}; subsequent GET shows nulls."""
    client, instance_id, _ = client

    put_resp = await _put(client, instance_id, {"pinned": True, "color_tag": "blue"})
    assert put_resp.status_code == 200

    del_resp = await client.delete(f"/api/instances/{instance_id}/ui-prefs")
    assert del_resp.status_code == 200, del_resp.text
    assert del_resp.json() == {"deleted": True}

    # After delete, the GET single response must show nulls for all three
    # merged fields (no prefs row exists).
    get_resp = await client.get(f"/api/instances/{instance_id}")
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["pinned"] is None
    assert body["color_tag"] is None
    assert body["pinned_at"] is None


@pytest.mark.asyncio
async def test_get_instances_list_includes_prefs_fields(client):
    """After PUT-ing prefs, GET /api/instances merges them into the item."""
    client, instance_id, _ = client

    put_resp = await _put(client, instance_id, {"pinned": True, "color_tag": "green"})
    assert put_resp.status_code == 200

    list_resp = await client.get("/api/instances")
    assert list_resp.status_code == 200, list_resp.text
    payload = list_resp.json()
    assert "instances" in payload
    # The seeded instance must be present.
    matches = [
        i for i in payload["instances"] if i["instance_id"] == instance_id
    ]
    assert matches, "seeded instance missing from GET /api/instances list"
    item = matches[0]
    assert item["pinned"] is True
    assert item["color_tag"] == "green"
    assert item["pinned_at"] is not None


@pytest.mark.asyncio
async def test_get_single_instance_includes_prefs_fields(client):
    """GET /api/instances/{id} for instance WITH prefs merges the fields."""
    client, instance_id, _ = client

    put_resp = await _put(client, instance_id, {"pinned": True, "color_tag": "red"})
    assert put_resp.status_code == 200

    get_resp = await client.get(f"/api/instances/{instance_id}")
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["instance_id"] == instance_id
    assert body["pinned"] is True
    assert body["color_tag"] == "red"
    assert body["pinned_at"] is not None


@pytest.mark.asyncio
async def test_partial_upsert_preserves_other_field(client):
    """PUT {"pinned": true} then PUT {"color_tag": "green"} → both set.

    Confirms partial-update semantics at the API layer: the second PUT,
    which omits ``pinned``, must NOT touch the previously-pinned state.
    """
    client, instance_id, _ = client

    first = await _put(client, instance_id, {"pinned": True})
    assert first.status_code == 200
    assert first.json()["pinned"] is True

    second = await _put(client, instance_id, {"color_tag": "green"})
    assert second.status_code == 200, second.text
    data = second.json()
    assert data["pinned"] is True, (
        "partial upsert must preserve the previously-set pinned state"
    )
    assert data["color_tag"] == "green"
