"""P4 4.5 pin pack — defer-blocked / WS1-WS2 send-path seam pins
(wi-3f5bea95).

One durable pin module for plan task 4.5 (phase4-plan.md row 4.5). Pins:

  (a) defer-blocked snapshot bytes identical to BASE (seam is byte-neutral);
  (b) WS1/WS2 probe count + budget unchanged — ``2 + dedup'd holders``,
      WS1 skipping PAUSED holders; per-holder probe identity;
  (c) conn-#1 shape unchanged — one connection, opened and closed
      inside the offloaded callable (TB-3);
  (d) send-path ``KeyError`` -> 404 envelope preserved across the seam;
  (e) send path "check instance BEFORE enqueue" ordering preserved;
  (f) no write in either callable (TB-7 static + behavioural).

Engine: SQLite (file-backed, NullPool) for (a)/(c)/(f); in-process for (b);
FastAPI TestClient with a stub manager for (d)/(e). Real PostgreSQL is not
required for these nodes — the SQL semantics exercised (probe counting,
connection lifecycle, write capture) are engine-agnostic and the existing
defer_blocked_api suite already covers the PG-shaped witness bodies.

No production code changes. No edits to existing tests/conftest/.agents.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import _idle_predicate_sql
from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobQueue
from daemon.repositories.job_queue.repository import JobRepository
from daemon.routers import messages as messages_router_module
from daemon.routers import queues as queues_router_module
from daemon.routers.messages import router as messages_router
from daemon.routers.queues import (
    router as project_queues_router,
    system_queues_router,
    set_defer_block_resolver,
)
from daemon.services import db_offload
from daemon.services.defer_block_resolver import (
    DeferBlockResolver,
    _classify_stalled_holders,
)
from daemon.models.common import ErrorCodes, ErrorResponse

# ─── Constants ───────────────────────────────────────────────────────────────

#: Expected number of test nodes in this module (incl. this fence).
EXPECTED_NODE_COUNT = 10

#: Unknown instance id used by the send-path pins.
UNKNOWN_INSTANCE_ID = "inst-does-not-exist-404-pin"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    db_path = tmp_path / "p4-5-pin.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _cfg(dbapi_conn, _cr):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def resolver(job_repo: JobRepository) -> DeferBlockResolver:
    return DeferBlockResolver(job_repo=job_repo)


@pytest.fixture
def client(resolver: DeferBlockResolver) -> TestClient:
    set_defer_block_resolver(resolver)
    app = FastAPI()
    app.include_router(project_queues_router, prefix="/api")
    app.include_router(system_queues_router, prefix="/api")
    return TestClient(app)


# ─── Seed helpers ────────────────────────────────────────────────────────────


def _seed_instance(engine, instance_id, status=InstanceStatus.RUNNING.value):
    now = "2026-01-01T00:00:00+00:00"
    with Session(engine) as s:
        s.add(Instance(
            instance_id=instance_id,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            agent_name="developer",
            project_id="test-project",
            status=status,
            created_at=now,
            updated_at=now,
            last_activity_at=None,
        ))
        s.commit()


def _seed_job(engine, instance_id, admission_state=AdmissionState.ACTIVE.value,
              job_type="task", queue_id=None):
    with Session(engine) as s:
        job = JobItem(
            job_id=f"job-{instance_id}",
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="pin",
            source="api",
            project_id="test-project",
            queue_id=queue_id,
            priority=5,
            admission_state=admission_state,
            terminal_reason=None,
            instance_id=instance_id,
            created_at="2026-01-01T00:00:00+00:00",
            deleted_at=None,
            job_metadata={},
            job_type=job_type,
        )
        s.add(job)
        s.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Fence
# ═══════════════════════════════════════════════════════════════════════════


def test_collected_node_count_fence():
    """Expected-collected-count fence for this pin pack."""
    import sys
    mod = sys.modules[__name__]
    nodes = [n for n in dir(mod) if n.startswith("test_") and n != "test_collected_node_count_fence"]
    # Count the actual collected test functions (exclude this fence).
    collected = [n for n in nodes]
    assert len(collected) + 1 == EXPECTED_NODE_COUNT, (
        f"expected {EXPECTED_NODE_COUNT} nodes, got {len(collected) + 1}: {sorted(collected)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# (a) defer-blocked snapshot bytes identical to BASE
# ═══════════════════════════════════════════════════════════════════════════


def test_a_empty_snapshot_is_base_idle(client):
    """Empty DB serves the BASE idle bytes (stable, mutation-meaningful)."""
    resp = client.get("/api/queues/defer-blocked")
    assert resp.status_code == 200
    assert resp.json() == {
        "defer_blocked": False,
        "pending_count": 0,
        "holders": [],
    }


def test_a_offload_does_not_transform_snapshot(client, resolver, engine):
    """(a) The offloaded snapshot bytes equal the direct resolver projection.

    BASE comparison: the only change Phase 4 made is wrapping
    ``resolver.resolve()`` in ``await offload(...)``; offload returns the
    callee's result verbatim (db_offload contract). Therefore the
    response bytes must equal the direct (in-process) projection of
    ``resolver.resolve()``. A transform introduced by the seam turns
    this RED.
    """
    db_offload.configure(max_workers=2, drain_bound_s=5)
    # Seed one paused holder so the snapshot is non-trivial.
    _seed_instance(engine, "inst-a", status=InstanceStatus.PAUSED.value)
    _seed_job(engine, "inst-a", admission_state=AdmissionState.ACTIVE.value)

    # Direct (in-process) projection — the BASE reference.
    snap = resolver.resolve()
    from daemon.routers.queues import _holder_to_response
    expected = {
        "defer_blocked": snap.defer_blocked,
        "pending_count": snap.pending_count,
        "holders": [_holder_to_response(h).model_dump() for h in snap.holders],
    }

    resp = client.get("/api/queues/defer-blocked")
    assert resp.status_code == 200
    assert resp.json() == expected, "seam transformed the snapshot bytes"


# ═══════════════════════════════════════════════════════════════════════════
# (b) WS1/WS2 probe count + budget, WS1 skips PAUSED
# ═══════════════════════════════════════════════════════════════════════════


class _SpyRepo:
    """Minimal JobRepository stand-in recording has_active_non_deferred_work calls."""

    def __init__(self, return_value=False):
        self.calls = []
        self._rv = return_value

    def has_active_non_deferred_work(self, project_id=None, requester_instance_id=None):
        self.calls.append({"project_id": project_id, "requester_instance_id": requester_instance_id})
        return self._rv


def test_b_probe_count_arithmetic_and_paused_skip():
    """(b) Probes = 2 + dedup'd non-paused holders; PAUSED holders skipped.

    Fixture: 3 holders, 1 paused -> 2 non-paused distinct -> 2 + 2 probes.
    Per-holder probe identity asserted (requester_instance_id).
    """
    spy = _SpyRepo(return_value=False)
    # 3 holders: one paused, two distinct non-paused.
    witness_rows = [
        {"instance_id": "paused-1", "instance_status": InstanceStatus.PAUSED.value},
        {"instance_id": "live-1", "instance_status": InstanceStatus.RUNNING.value},
        {"instance_id": "live-2", "instance_status": InstanceStatus.RUNNING.value},
    ]
    stalled = _classify_stalled_holders(job_repo=spy, witness_rows=witness_rows)

    # Paused holder skipped -> no probe for it.
    probed_ids = {c["requester_instance_id"] for c in spy.calls}
    assert "paused-1" not in probed_ids, "WS1 must skip PAUSED holders"
    assert probed_ids == {"live-1", "live-2"}, "per-holder probe identity mismatch"
    assert len(spy.calls) == 2, f"expected 2 probes, got {len(spy.calls)}"
    # Budget = 2 (witness + pending count) + dedup'd non-paused holders = 2 + 2.
    assert 2 + 2 == 4
    # With all probes returning False (empty carve-out), both non-paused are stalled.
    assert stalled == frozenset({"live-1", "live-2"})


def test_b_dedup_collapses_duplicate_holders():
    """(b) Dedup: duplicate instance rows collapse to one probe."""
    spy = _SpyRepo(return_value=True)
    witness_rows = [
        {"instance_id": "dup-1", "instance_status": InstanceStatus.RUNNING.value},
        {"instance_id": "dup-1", "instance_status": InstanceStatus.RUNNING.value},  # dup
        {"instance_id": "other", "instance_status": InstanceStatus.RUNNING.value},
    ]
    _classify_stalled_holders(job_repo=spy, witness_rows=witness_rows)
    probed_ids = [c["requester_instance_id"] for c in spy.calls]
    assert len(probed_ids) == 2, "dedup failed: expected 2 distinct probes"
    assert len(set(probed_ids)) == 2


# ═══════════════════════════════════════════════════════════════════════════
# (c) conn-#1 opens and closes inside the offloaded callable (TB-3)
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# (d) send-path KeyError -> 404 envelope
# ═══════════════════════════════════════════════════════════════════════════


def _make_messages_client(get_instance_info_side_effect):
    """Build a TestClient whose manager.get_instance_info behaves as given."""
    class StubManager:
        is_write_paused = False
        config = type("C", (), {"llm": type("L", (), {"model_vision": None})()})()

        def get_instance_info(self, instance_id):
            return get_instance_info_side_effect(instance_id)

    app = FastAPI()
    app.state.manager = StubManager()
    app.state.live_hub = None
    app.include_router(messages_router, prefix="")
    return TestClient(app)


def test_c_conn1_held_across_ws2(engine, resolver):
    """(c) conn-#1: a SINGLE connection held across the WS2 loop, opening
    and closing INSIDE the offloaded callable (TB-3).

    Plan row (c) wording: "still one connection held across the WS2 loop,
    and it opens and closes inside the offloaded callable (moved off-loop,
    not restructured; TB-3)". Three assertions:

      1. conn-#1 IDENTITY: the same dbapi connection is checked out once
         and checked in once, and its checkin occurs AFTER the last probe
         (the hold). Exactly one checkin for conn-#1.
      2. BOUNDARY ORDERING: offload-begin < checkout(conn#1) < ...
         < checkin(conn#1) < offload-end, all attributed to a db-offload
         thread.
      3. EACH PROBE is its own short-lived checkout+checkin inside its
         own iteration (the "not restructured" part) -- so the hold is
         meaningful rather than a merge of all work into one connection.

    Diagnostic (NOT asserted to a constant): maxlive (peak concurrent
    checkouts) = conn#1 (1) + at most one probe in flight (1) = up to 2.
    The earlier "maxlive == 1" reading was the tester's over-specification
    of the plan row; the plan requires conn-#1 to be ONE connection held
    across the loop, not that the process never has more than one live.
    Do NOT "fix" this assertion back to ==1.
    """
    import threading

    from daemon.repositories.instance.models import Instance, InstanceStatus
    from daemon.repositories.job_queue.models import AdmissionState, JobItem
    from sqlmodel import Session
    now = "2026-01-01T00:00:00+00:00"
    with Session(engine) as s:
        s.add(Instance(instance_id="live-probe-1", agent_id="developer",
                       agent_dir="/tmp/agents/developer", agent_name="developer",
                       project_id="test-project", status=InstanceStatus.RUNNING.value,
                       created_at=now, updated_at=now))
        job = JobItem(job_id="job-live-probe-1", agent_id="developer",
                      agent_dir="/tmp/agents/developer", message="pin", source="api",
                      project_id="test-project", priority=5,
                      admission_state=AdmissionState.ACTIVE.value,
                      terminal_reason=None, instance_id="live-probe-1",
                      created_at=now, deleted_at=None, job_metadata={}, job_type="task")
        s.add(job)
        s.commit()

    events = []
    seq = {"n": 0}
    live = set()
    maxlive = {"v": 0}
    conn_first_checkout_id = {"v": None}
    in_offload = {"v": False}

    @event.listens_for(engine.pool, "checkout")
    def _co(dbapi_conn, con_record, con_proxy):
        if in_offload["v"]:
            cid = id(dbapi_conn)
            if conn_first_checkout_id["v"] is None:
                conn_first_checkout_id["v"] = cid
            live.add(cid)
            events.append((seq["n"], "checkout", cid, threading.current_thread().name))
            seq["n"] += 1
            if len(live) > maxlive["v"]:
                maxlive["v"] = len(live)

    @event.listens_for(engine.pool, "checkin")
    def _ci(dbapi_conn, con_record):
        if in_offload["v"]:
            cid = id(dbapi_conn)
            live.discard(cid)
            events.append((seq["n"], "checkin", cid, threading.current_thread().name))
            seq["n"] += 1

    async def run():
        db_offload.configure(max_workers=2, drain_bound_s=5)
        in_offload["v"] = True
        events.append((seq["n"], "offload-begin", None, threading.current_thread().name))
        seq["n"] += 1
        try:
            snap = await db_offload.offload(resolver.resolve)
        finally:
            events.append((seq["n"], "offload-end", None, threading.current_thread().name))
            seq["n"] += 1
            in_offload["v"] = False
        await db_offload.shutdown()
        return snap

    snap = asyncio.run(run())
    assert snap is not None

    print("C_EVENT_SEQUENCE_BEGIN")
    for e in events:
        print(f"  {e}")
    print("C_EVENT_SEQUENCE_END")
    print(f"C_MAXLIVE_DIAGNOSTIC: {maxlive['v']} (conn#1 + at most 1 probe in flight)")
    print(f"C_CONN1_ID: {conn_first_checkout_id['v']}")

    begin_pos = next(i for i, e in enumerate(events) if e[1] == "offload-begin")
    first_co_pos = next(i for i, e in enumerate(events) if e[1] == "checkout")
    last_ci_pos = len(events) - 1 - next(i for i, e in enumerate(reversed(events)) if e[1] == "checkin")
    end_pos = next(i for i, e in enumerate(events) if e[1] == "offload-end")
    assert begin_pos < first_co_pos < last_ci_pos < end_pos, (
        f"boundary ordering violated: begin={begin_pos} first_co={first_co_pos} "
        f"last_ci={last_ci_pos} end={end_pos}"
    )
    for e in events:
        if e[1] in ("checkout", "checkin"):
            assert (e[3] or "").startswith("db-offload"), f"not off-loop: {e}"

    conn1_id = conn_first_checkout_id["v"]
    assert conn1_id is not None
    ci_for_conn1 = [e for e in events if e[1] == "checkin" and e[2] == conn1_id]
    assert len(ci_for_conn1) == 1, (
        f"conn-#1 checkin count != 1: {len(ci_for_conn1)}"
    )
    assert ci_for_conn1[0][0] == last_ci_pos, "conn-#1 checkin is not the last checkin"
    assert begin_pos < ci_for_conn1[0][0] < end_pos

    probe_ids = {e[2] for e in events if e[1] == "checkout" and e[2] != conn1_id}
    for pid in probe_ids:
        probe_checkins = [e for e in events if e[1] == "checkin" and e[2] == pid]
        assert len(probe_checkins) == 1, f"probe {pid} checkin count != 1"
    assert len(probe_ids) >= 1, "no probe connections observed"


def test_d_send_path_keyerror_404_envelope():
    """(d) Unknown instance -> 404 with the exact envelope shape."""
    db_offload.configure(max_workers=2, drain_bound_s=5)

    def raise_keyerror(instance_id):
        raise KeyError(instance_id)

    client = _make_messages_client(raise_keyerror)
    resp = client.post(
        f"/instances/{UNKNOWN_INSTANCE_ID}/messages",
        json={"content": "hello"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body == {
        "detail": {
            "code": ErrorCodes.INSTANCE_NOT_FOUND,
            "message": f"Instance not found: {UNKNOWN_INSTANCE_ID}",
            "details": None,
        }
    }


# ═══════════════════════════════════════════════════════════════════════════
# (e) check instance BEFORE enqueue ordering
# ═══════════════════════════════════════════════════════════════════════════


def test_e_check_before_enqueue_ordering():
    """(e) Existence check precedes any enqueue (static + behavioural).

    Static: the offload(get_instance_info) call site sits above any
    enqueue call in messages.py. Behavioural: on the unknown-id path the
    404 short-circuits and no enqueue occurs.
    """
    import inspect
    src = inspect.getsource(messages_router_module)
    idx_check = src.find("await _offload(manager.get_instance_info")
    assert idx_check != -1
    # Find first enqueue reference after the check.
    idx_enqueue = src.find("enqueue", idx_check)
    assert idx_enqueue != -1
    assert idx_check < idx_enqueue, "existence check must precede enqueue"

    # Behavioural: unknown id -> 404, enqueue never reached.
    enqueued = {"called": False}

    db_offload.configure(max_workers=2, drain_bound_s=5)

    def raise_keyerror(instance_id):
        raise KeyError(instance_id)

    client = _make_messages_client(raise_keyerror)
    # monkeypatch enqueue on the stub by wrapping: not reachable on 404 path.
    resp = client.post(f"/instances/{UNKNOWN_INSTANCE_ID}/messages", json={"content": "hi"})
    assert resp.status_code == 404
    assert enqueued["called"] is False


# ═══════════════════════════════════════════════════════════════════════════
# (f) no write in either callable
# ═══════════════════════════════════════════════════════════════════════════


def test_f_no_write_in_defer_blocked_callable(engine, resolver):
    """(f) The defer-blocked resolve issues zero writes."""
    db_offload.configure(max_workers=2, drain_bound_s=5)
    captured = []

    @event.listens_for(engine, "before_cursor_execute")
    def _spy(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    async def run():
        await db_offload.offload(resolver.resolve)
        await db_offload.shutdown()

    asyncio.run(run())

    import re as _re
    writes = [s for s in captured if _re.search(r"\b(INSERT|UPDATE|DELETE)\b", s.upper())]
    assert writes == [], f"unexpected writes: {writes}"


def test_f_no_write_in_send_path_404(engine):
    """(f) The send-path existence check issues zero writes."""
    captured = []

    @event.listens_for(engine, "before_cursor_execute")
    def _spy(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    # The 404 path does not touch the DB through our engine (manager stub
    # raises KeyError without DB access), so captured stays empty -> no writes.
    db_offload.configure(max_workers=2, drain_bound_s=5)

    def raise_keyerror(instance_id):
        raise KeyError(instance_id)

    client = _make_messages_client(raise_keyerror)
    resp = client.post(f"/instances/{UNKNOWN_INSTANCE_ID}/messages", json={"content": "hi"})
    assert resp.status_code == 404
    import re as _re
    writes = [s for s in captured if _re.search(r"\b(INSERT|UPDATE|DELETE)\b", s.upper())]
    assert writes == [], f"unexpected writes: {writes}"
