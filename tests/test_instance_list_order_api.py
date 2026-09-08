"""HTTP-level integration tests for the ``order`` query parameter on
``GET /api/instances?order=...``.

The repository-layer behaviour (``SQLModelInstanceRepository.list`` with
``order``) is locked in by ``tests/unit/test_instance_list_order_repository.py``.
This file goes ONE layer up and exercises the full
router → manager → lifecycle-service → repository path via FastAPI's
``TestClient`` against a real in-memory SQLite engine — the real-dispatch
wiring (NOT AsyncMock): if any seam drops the new ``order`` kwarg, the
activity tests fail with the pinned-order result.

Coverage:

    * No ``order`` param at all → BYTE-COMPAT default (pinned-first, exact
      result order of a mixed pinned/unpinned/old/new fixture set)
    * ``order=pinned`` → identical sequence to the default
    * ``order=activity`` → unpinned fresh live root above older pinned
      terminal root; live tier above ALL terminal roots; terminal tier
      ``updated_at`` DESC
    * ``total`` unaffected by ordering
    * Invalid ``order`` value → 422 (strict pattern validation)

The test wiring follows ``tests/test_instance_search_api.py``: a real
``SQLModelInstanceRepository`` + ``InstanceLifecycleService`` is stood up
behind a ``MagicMock`` manager so the router's ``_get_manager(request)``
returns a manager whose ``list_instances`` actually hits the engine.

Run standalone:

    timeout 300 .venv/bin/pytest tests/test_instance_list_order_api.py --tb=short -q
"""

from __future__ import annotations

from typing import Any

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
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

T0 = "2026-09-01T10:00:00+00:00"  # oldest
T1 = "2026-09-02T10:00:00+00:00"
T2 = "2026-09-03T10:00:00+00:00"
T3 = "2026-09-04T10:00:00+00:00"
T4 = "2026-09-05T10:00:00+00:00"
T5 = "2026-09-06T10:00:00+00:00"  # newest


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (mirrors
    ``tests/test_instance_search_api.py::engine``). ``StaticPool`` keeps a
    single connection alive so reads after writes always see the latest
    data, even when the writer ran on a different thread.
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


def _make_root(
    instance_id: str,
    *,
    agent_id: str = "developer",
    status: str = "idle",
    created_at: str = T0,
    updated_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Instance:
    """Build a ROOT ``Instance`` row (NOT committed) with deterministic
    timestamps."""
    return Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir="agents/coder",
        agent_name="Coder",
        parent_id=None,
        status=status,
        version=1,
        instance_metadata=metadata or {},
        created_at=created_at,
        updated_at=updated_at if updated_at is not None else created_at,
    )


@pytest.fixture
def seed_instances(engine):
    """Seed a mixed pinned/unpinned/old/new root set (all roots — the
    endpoint paginates by root).

    Layout:
        id          pinned  status      created_at  updated_at
        ----------  ------  ----------  ----------  ----------
        pin-old     True    completed   T0          T1
        pin-new     True    completed   T1          T1
        free-new    False   idle        T5          T5
        free-mid    False   idle        T3          T3
        free-old    False   idle        T2          T2

    Upsert order sets ``pinned_at`` (later upsert ⇒ later pinned_at).
    ``pin-new`` carries the newer created_at of the two pinned roots so the
    expected order is identical even under a ``pinned_at`` microsecond tie
    (the ``created_at`` DESC tiebreak agrees with ``pinned_at`` DESC).
    """
    with Session(engine) as s:
        s.add(_make_root("pin-old", status="completed", created_at=T0, updated_at=T1))
        s.add(_make_root("pin-new", status="completed", created_at=T1, updated_at=T1))
        s.add(_make_root("free-new", status="idle", created_at=T5))
        s.add(_make_root("free-mid", status="idle", created_at=T3))
        s.add(_make_root("free-old", status="idle", created_at=T2))
        s.commit()

    ui_prefs = InstanceUiPrefsRepository(engine)
    ui_prefs.upsert("pin-old", pinned=True)
    ui_prefs.upsert("pin-new", pinned=True)
    return ["pin-old", "pin-new", "free-new", "free-mid", "free-old"]


@pytest.fixture
def app_and_client(engine):
    """FastAPI ``TestClient`` wired to a real repo + lifecycle service
    (mirrors ``tests/test_instance_search_api.py::app_and_client``)."""
    from unittest.mock import MagicMock

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

    app = FastAPI()
    app.include_router(instances_router)
    app.state.manager = manager

    with TestClient(app) as client:
        yield client


def _root_ids(resp_json: dict) -> list[str]:
    """Root instance_ids in WIRE order — first occurrence of each id in the
    flat response (roots are emitted before their descendants; this fixture
    has no descendants, so the flat order IS the root order)."""
    seen: list[str] = []
    for inst in resp_json["instances"]:
        if inst["instance_id"] not in seen:
            seen.append(inst["instance_id"])
    return seen


# ─────────────────────────────────────────────────────────────────────────────
# BYTE-COMPAT: default (no param) == order=pinned == historical behavior
# ─────────────────────────────────────────────────────────────────────────────


class TestOrderDefaultByteCompat:
    """Omitting ``order`` must be byte-compatible with the pre-change
    endpoint: pinned-first, then created_at DESC."""

    def test_no_param_exact_pinned_order(self, app_and_client, seed_instances):
        """BYTE-COMPAT PIN (HTTP layer): exact root sequence with NO
        ``order`` param — pinned tier (pinned_at DESC) then unpinned tier
        (created_at DESC)."""
        resp = app_and_client.get("/instances")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 5
        assert _root_ids(body) == [
            "pin-new", "pin-old",                # pinned tier: pinned_at DESC
            "free-new", "free-mid", "free-old",  # unpinned tier: created_at DESC
        ]

    def test_order_pinned_identical_to_default(self, app_and_client, seed_instances):
        """``?order=pinned`` MUST return the exact same sequence as no param."""
        default = app_and_client.get("/instances").json()
        pinned = app_and_client.get("/instances", params={"order": "pinned"}).json()
        assert pinned["total"] == default["total"]
        assert _root_ids(pinned) == _root_ids(default)
        assert pinned["instances"] == default["instances"]

    def test_pinned_still_beats_newest_unpinned(self, app_and_client, seed_instances):
        """Historical contract intact under default ordering: 'free-new'
        (newest created_at) stays BELOW the pinned tier."""
        body = app_and_client.get("/instances").json()
        ids = _root_ids(body)
        assert ids.index("pin-old") < ids.index("free-new")


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVITY ordering
# ─────────────────────────────────────────────────────────────────────────────


class TestOrderActivity:
    """``?order=activity`` — live roots first, then updated_at DESC."""

    def test_activity_fresh_live_above_pinned_terminal(
        self, app_and_client, seed_instances
    ):
        """The motivating production failure: pinned terminal roots owned the
        page while a fresh unpinned live root never appeared. Under
        activity ordering the live root leads."""
        resp = app_and_client.get("/instances", params={"order": "activity"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 5  # total is order-invariant
        assert _root_ids(body) == [
            # Live tier: updated_at DESC
            "free-new",   # idle, updated_at T5
            "free-mid",   # idle, updated_at T3
            "free-old",   # idle, updated_at T2
            # Terminal tier: updated_at DESC (both updated_at=T1 → id ASC)
            "pin-new",
            "pin-old",
        ]

    def test_activity_live_root_above_all_terminal(
        self, app_and_client, engine
    ):
        """A live root with the OLDEST timestamps still outranks EVERY
        terminal root (even pinned, even recently-updated)."""
        with Session(engine) as s:
            s.add(_make_root("live-ancient", status="running", created_at=T0, updated_at=T0))
            s.add(_make_root("term-recent", status="completed", created_at=T5, updated_at=T5))
            s.add(_make_root("term-pinned-recent", status="failed", created_at=T4, updated_at=T4))
            s.commit()
        InstanceUiPrefsRepository(engine).upsert("term-pinned-recent", pinned=True)

        resp = app_and_client.get("/instances", params={"order": "activity"})
        assert resp.status_code == 200, resp.text
        assert _root_ids(resp.json()) == ["live-ancient", "term-recent", "term-pinned-recent"]

    def test_activity_terminal_roots_updated_at_desc(
        self, app_and_client, engine
    ):
        """Within the terminal tier, roots order by updated_at DESC
        (created_at does NOT dominate)."""
        with Session(engine) as s:
            s.add(_make_root("term-a", status="completed", created_at=T0, updated_at=T2))
            s.add(_make_root("term-b", status="error", created_at=T4, updated_at=T1))
            s.add(_make_root("term-c", status="terminated", created_at=T1, updated_at=T3))
            s.commit()

        resp = app_and_client.get("/instances", params={"order": "activity"})
        assert resp.status_code == 200, resp.text
        # updated_at DESC: T3, T2, T1 — created_at order (T4, T1, T0) ignored.
        assert _root_ids(resp.json()) == ["term-c", "term-a", "term-b"]

    def test_activity_descendants_unchanged(self, app_and_client, engine):
        """Descendant embedding is order-invariant: a root's children remain
        attached under activity ordering (BFS loading untouched)."""
        with Session(engine) as s:
            s.add(_make_root("parent-live", status="running", created_at=T5))
            s.add(_make_root("stranger", status="idle", created_at=T1))
            s.add(Instance(
                instance_id="child-1",
                agent_id="developer",
                agent_dir="agents/coder",
                parent_id="parent-live",
                status="running",
                version=1,
                instance_metadata={},
                created_at=T5,
                updated_at=T5,
            ))
            s.commit()

        resp = app_and_client.get("/instances", params={"order": "activity"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        by_id = {i["instance_id"]: i for i in body["instances"]}
        assert by_id["parent-live"]["children"] == ["child-1"]
        assert by_id["child-1"]["parent_id"] == "parent-live"


# ─────────────────────────────────────────────────────────────────────────────
# STRICT VALIDATION: invalid order → 422
# ─────────────────────────────────────────────────────────────────────────────


class TestOrderValidation:
    """Invalid ``order`` values are rejected with 422 (FastAPI pattern)."""

    def test_invalid_order_returns_422(self, app_and_client, seed_instances):
        resp = app_and_client.get("/instances", params={"order": "bogus"})
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, list) and detail, resp.text
        entry = detail[0]
        assert entry["loc"] == ["query", "order"]
        assert entry["type"] == "string_pattern_mismatch"
        assert "pinned|activity" in entry["msg"]

    def test_case_sensitivity_is_strict(self, app_and_client, seed_instances):
        """The pattern is case-sensitive — 'Activity' is NOT accepted."""
        resp = app_and_client.get("/instances", params={"order": "Activity"})
        assert resp.status_code == 422, resp.text

    def test_empty_order_rejected(self, app_and_client, seed_instances):
        """Empty string does not match the pattern → 422 (omitting the param
        entirely is the only way to get the default)."""
        resp = app_and_client.get("/instances", params={"order": ""})
        assert resp.status_code == 422, resp.text
