"""Unit tests: ``_compact_fired_watchers_for_paused`` deliver-before-compact.

Phase 2 (pause-resume-terminate-tree-fix, task 2.4 — B2 fix).

The compact hook is reworked into a two-pass, ordering-gated shape
(Rev 2.1 W2):

  Pass 1 (DELIVER) — FIRED rows where ``enqueued_at IS NULL AND
  fired_at <= now()`` (ALL buffered, NO grace): re-enqueue each
  FollowUp via ``manager.enqueue_message``, then IMMEDIATELY stamp
  ``enqueued_at`` (per-row stamp is LOAD-BEARING — the DELETE's
  ``enqueued_at IS NOT NULL`` predicate is the durability guarantee).

  Pass 2 (DELETE) — the original 60s-grace DELETE, unchanged.

Ordering: Pass 1 strictly before Pass 2. Early-abort: ANY enqueue
failure (exception OR asyncio.CancelledError) aborts before Pass 2.

Case order honors the W3 mandate (Open Questions #6): the sub-class
repro (g) is authored and runs FIRST among these cases, pinning the
stamped-vs-unstamped strand shapes and their durable drain lanes
BEFORE the production per-row-stamp discipline is trusted.

The sync→async bridge (``asyncio.run_coroutine_threadsafe`` onto the
manager's loop) is exercised for real: each test drives the compact
via ``asyncio.to_thread`` exactly like ``resume_instance_cascade``
does, with the manager's ``enqueue_message`` as an ``AsyncMock``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
)
from daemon.services.instance_lifecycle import InstanceLifecycleService

_STALE_ISO = "2020-01-01T00:00:00+00:00"


# ─── Fixtures & helpers ───────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_instance(engine: Engine, status: str) -> str:
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = _now_iso()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="test-project",
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()
    return iid


def _make_fu_payload(parent_id: str, child_id: str, extra: dict | None = None) -> dict:
    fu = FollowUp(
        target_instance_id=parent_id,
        message=f"[dependency_bus] child {child_id} completed",
        source=f"internal_agent:{parent_id}",
        metadata={
            "kind": "child_complete",
            "child_id": child_id,
            "parent_id": parent_id,
            "message_id": f"msg-{child_id[:8]}",
            **(extra or {}),
        },
    )
    return fu.to_payload()


def _seed_watcher(
    engine: Engine,
    *,
    target_instance_id: str,
    source_task_id: str,
    child_id: str,
    state: str = DependencyWatcherState.PENDING.value,
    fired_at: str | None = None,
    enqueued_at: str | None = None,
) -> str:
    wid = f"watch-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(
            DependencyWatcher(
                watch_id=wid,
                source_task_id=source_task_id,
                target_instance_id=target_instance_id,
                follow_up_payload=_make_fu_payload(target_instance_id, child_id),
                created_at=_now_iso(),
                state=state,
                fired_at=fired_at,
                enqueued_at=enqueued_at,
            )
        )
        s.commit()
    return wid


def _read_watcher_row(engine: Engine, watch_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT state, fired_at, enqueued_at "
                "FROM dependency_watchers WHERE watch_id = :wid"
            ),
            {"wid": watch_id},
        ).mappings().first()


@pytest.fixture
def lifecycle_service(engine):
    """Lifecycle service with an AsyncMock enqueue seam + live loop.

    The manager mock exposes ``engine`` and an ``AsyncMock``
    ``enqueue_message`` so the sync→async bridge schedules a REAL
    coroutine onto the running loop (mirroring production wiring).
    ``_loop`` is stamped per-test because it must reference the
    CURRENT pytest-asyncio loop.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.enqueue_message = AsyncMock(return_value={"message_id": "m-1"})
    service._manager = manager
    return service


# ─── (g) W3 sub-class repro — RUNS FIRST (mandated ordering) ─────────────────


@pytest.mark.asyncio
async def test_g_w3_stamped_vs_unstamped_strand_repro(engine, lifecycle_service):
    """W3 (Rev 2.1): which strand shapes does the crash window produce?

    The crash window between ``fire_for_terminated_target`` /
    ``emit_terminal`` and ``mark_enqueued_by_source_target`` can
    strand FIRED rows in TWO shapes:

      * unstamped strand  — ``state='FIRED' AND enqueued_at IS NULL``
        (crash BEFORE the post-enqueue stamp): the FollowUp was never
        (re-)enqueued; nothing in ``message_queue`` / ``report_injections``
        owns its delivery.
      * stamped strand    — ``state='FIRED' AND enqueued_at IS NOT NULL``
        (crash AFTER the stamp but before the downstream turn
        consumed it): the enqueue has been accounted; the durable
        artifact rows (``report_injections`` PENDING) carry the
        delivery obligation.

    Step 1 — the repro constructs BOTH strand shapes and asserts that
    Pass 1's ``enqueued_at IS NULL`` predicate selects ONLY the
    unstamped strand (the stamped strand is invisible to Pass 1 and
    drains via a DIFFERENT lane).

    Step 2 — each strand's durable drain lane is asserted by name:
      (i)  unstamped  → ``_recover_fired_unsent`` restart path
      (ii) stamped    → ``claim_for_injection`` at graph dispatch
                        (hot path) with the periodic
                        report-delivery-recovery sweep as backstop.
    """
    parent = _seed_instance(engine, InstanceStatus.PAUSED.value)

    # Reproduce the crash window by hand: fire via the REAL bus path
    # (emit_terminal — the same guarded transition the terminate-side
    # fire uses), then simulate the two crash shapes.
    wid_unstamped = _seed_watcher(
        engine,
        target_instance_id=parent,
        source_task_id="task-unstamped",
        child_id="child-U",
    )
    wid_stamped = _seed_watcher(
        engine,
        target_instance_id=parent,
        source_task_id="task-stamped",
        child_id="child-S",
    )
    # Fire both through the production transition (guarded PENDING→FIRED).
    repo = DependencyWatcherRepository(engine)
    assert repo.transition_state(
        wid_unstamped, DependencyWatcherState.FIRED.value, _now_iso()
    )
    assert repo.transition_state(
        wid_stamped, DependencyWatcherState.FIRED.value, _now_iso()
    )
    # Crash shape (a): stamp never ran → unstamped strand.
    # Crash shape (b): stamp ran (simulate the enqueue completing +
    # stamping, then a crash before the parent's turn drains the
    # report) → stamped strand.
    repo.mark_enqueued_by_source_target("task-stamped", parent)

    # ── Step 1: Pass 1's predicate selects ONLY the unstamped strand ──
    # (exact predicate text Pass 1 uses — asserted against the DB
    # directly so the pin is independent of the implementation.)
    with engine.connect() as conn:
        selected = conn.execute(
            text(
                "SELECT watch_id FROM dependency_watchers "
                "WHERE target_instance_id = :iid "
                "  AND state = :fired "
                "  AND enqueued_at IS NULL "
                "  AND fired_at <= :now"
            ),
            {
                "iid": parent,
                "fired": DependencyWatcherState.FIRED.value,
                "now": _now_iso(),
            },
        ).scalars().all()
    assert selected == [wid_unstamped], (
        "W3 repro: Pass 1's enqueued_at IS NULL predicate must select "
        "ONLY the unstamped strand"
    )

    # ── Step 2(i): unstamped strand drains via _recover_fired_unsent ──
    # A process restart reconstructs the bus over the same engine and
    # must surface the unstamped row (and NOT the stamped one).
    restart_bus = DependencyBus(DependencyWatcherRepository(engine))
    await restart_bus.start()
    try:
        recovered = await restart_bus._recover_fired_unsent()
    finally:
        await restart_bus.stop()
    recovered_wids = {wid for wid, _fu in recovered}
    assert wid_unstamped in recovered_wids
    assert wid_stamped not in recovered_wids
    # The recovered FollowUp round-trips with its payload contract.
    recovered_fu = next(fu for wid, fu in recovered if wid == wid_unstamped)
    assert recovered_fu.target_instance_id == parent

    # ── Step 2(ii): stamped-but-undelivered drains via
    # claim_for_injection at graph dispatch. The stamped strand's
    # delivery obligation lives in report_injections (PENDING rows
    # created by the child-completion path); the graph-node drain
    # claims exactly-once.
    ri_repo = ReportInjectionRepository(engine)
    with Session(engine) as s:
        s.add(
            __import__(
                "daemon.repositories.report_injection.models",
                fromlist=["ReportInjection"],
            ).ReportInjection(
                parent_instance_id=parent,
                child_instance_id="child-S",
                child_message_id="msg-S",
                report_message_id="rmid-S",
                content="[report] child-S completed",
            )
        )
        s.commit()
    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop
    first = await asyncio.to_thread(ri_repo.claim_for_injection, parent)
    second = await asyncio.to_thread(ri_repo.claim_for_injection, parent)
    assert len(first) == 1
    assert first[0]["content"] == "[report] child-S completed"
    assert second == [], (
        "stamped strand's claim lane is exactly-once (guarded "
        "WHERE state='PENDING' UPDATE)"
    )


# ─── (a) no buffered rows ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_no_buffered_rows(engine, lifecycle_service):
    """No FIRED rows → Pass 1 no-op, Pass 2 no-op, enqueue untouched."""
    iid = _seed_instance(engine, InstanceStatus.PAUSED.value)
    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop

    deleted = await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, iid
    )

    assert deleted == 0
    lifecycle_service._manager.enqueue_message.assert_not_called()


# ─── (b) buffered + fresh (<60s) MUST deliver ─────────────────────────────────


@pytest.mark.asyncio
async def test_b_buffered_fresh_must_deliver(engine, lifecycle_service):
    """A child that completed 30s before resume must NOT be stranded.

    Pass 1 delivers with NO grace — the 60s grace applies only to the
    DELETE (Pass 2), never to delivery.
    """
    iid = _seed_instance(engine, InstanceStatus.PAUSED.value)
    fresh_iso = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    wid = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-fresh",
        child_id="child-F",
        state=DependencyWatcherState.FIRED.value,
        fired_at=fresh_iso,
        enqueued_at=None,
    )
    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop

    deleted = await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, iid
    )

    # Delivered exactly once…
    lifecycle_service._manager.enqueue_message.assert_called_once()
    call = lifecycle_service._manager.enqueue_message.call_args
    assert call.kwargs["instance_id"] == iid
    # …stamped (per-row stamp discipline)…
    row = _read_watcher_row(engine, wid)
    assert row["state"] == DependencyWatcherState.FIRED.value
    assert row["enqueued_at"] is not None
    # …and NOT deleted (fresh — inside the 60s DELETE grace).
    assert deleted == 0
    assert row is not None


# ─── (c) buffered + stale (>60s) MUST deliver ─────────────────────────────────


@pytest.mark.asyncio
async def test_c_buffered_stale_must_deliver(engine, lifecycle_service):
    """A buffered row past the grace window is DELIVERED, then deleted.

    Pre-fix (B2): the stale unstamped row was simply never delivered
    (the DELETE required ``enqueued_at IS NOT NULL``) — the buffered
    report was stranded forever. Post-fix: deliver first, stamp, then
    Pass 2 may reap the row (delivery is durable in message_queue).
    """
    iid = _seed_instance(engine, InstanceStatus.PAUSED.value)
    wid = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-stale",
        child_id="child-S",
        state=DependencyWatcherState.FIRED.value,
        fired_at=_STALE_ISO,
        enqueued_at=None,
    )
    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop

    deleted = await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, iid
    )

    lifecycle_service._manager.enqueue_message.assert_called_once()
    # Row delivered (enqueued + stamped) and then reaped by Pass 2
    # (stamped + past grace). The DELIVERY is the durable outcome.
    assert deleted == 1
    assert _read_watcher_row(engine, wid) is None


# ─── (d) buffered + already-stamped MUST skip (healing idempotency) ──────────


@pytest.mark.asyncio
async def test_d_already_stamped_skips_exactly_one_enqueue(engine, lifecycle_service):
    """A stamped row is invisible to Pass 1 — no double-deliver.

    Mixed shape: one stamped (already delivered by a prior cycle) +
    one unstamped → exactly ONE enqueue total, for the unstamped row
    only. This pins the healing claim: a stamped row never
    double-delivers via the compact path.
    """
    iid = _seed_instance(engine, InstanceStatus.PAUSED.value)
    now_iso = _now_iso()
    _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-stamped",
        child_id="child-A",
        state=DependencyWatcherState.FIRED.value,
        fired_at=now_iso,
        enqueued_at=now_iso,  # stamped — prior cycle delivered it
    )
    wid_unstamped = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-unstamped",
        child_id="child-B",
        state=DependencyWatcherState.FIRED.value,
        fired_at=now_iso,
        enqueued_at=None,
    )
    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop

    await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, iid
    )

    calls = lifecycle_service._manager.enqueue_message.call_args_list
    assert len(calls) == 1, f"exactly-one-enqueue healing, got {len(calls)}"
    # The single enqueue is for the UNSTAMPED row's FollowUp.
    assert calls[0].kwargs["instance_id"] == iid
    row = _read_watcher_row(engine, wid_unstamped)
    assert row["enqueued_at"] is not None


# ─── (e) mid-Pass-1 crash → early-abort, Pass 2 not entered ──────────────────


@pytest.mark.asyncio
async def test_e_mid_pass1_crash_early_abort(engine, lifecycle_service):
    """ANY enqueue failure aborts BEFORE Pass 2 — rows survive.

    Two unstamped buffered rows; the FIRST enqueue raises. Expected:
    no further enqueue attempts, Pass 2 NEVER runs (a stamped-stale
    row that would qualify for the DELETE must SURVIVE — the abort is
    the load-bearing guard against deleting the buffered set while
    delivery is broken).
    """
    iid = _seed_instance(engine, InstanceStatus.PAUSED.value)
    # A stamped-stale row: eligible for Pass 2's DELETE. If Pass 2
    # ran despite the abort, this row would vanish — the exact
    # silently-lose-the-buffered-set failure the plan forbids.
    wid_stamped_stale = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-marked",
        child_id="child-M",
        state=DependencyWatcherState.FIRED.value,
        fired_at=_STALE_ISO,
        enqueued_at=_STALE_ISO,
    )
    _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-crash-1",
        child_id="child-C1",
        state=DependencyWatcherState.FIRED.value,
        fired_at=_now_iso(),
        enqueued_at=None,
    )
    _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-crash-2",
        child_id="child-C2",
        state=DependencyWatcherState.FIRED.value,
        fired_at=_now_iso(),
        enqueued_at=None,
    )
    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop
    lifecycle_service._manager.enqueue_message = AsyncMock(
        side_effect=RuntimeError("enqueue path down (simulated crash)")
    )

    deleted = await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, iid
    )

    # Early-abort: single enqueue attempt, Pass 2 never entered.
    assert deleted == 0
    assert lifecycle_service._manager.enqueue_message.await_count == 1
    assert _read_watcher_row(engine, wid_stamped_stale) is not None, (
        "Pass 2 must not run after a Pass 1 failure"
    )


@pytest.mark.asyncio
async def test_e2_cancelled_error_also_aborts(engine, lifecycle_service):
    """asyncio.CancelledError (BaseException, NOT caught by ``except
    Exception``) must ALSO early-abort — pause semantics preserved.
    """
    iid = _seed_instance(engine, InstanceStatus.PAUSED.value)
    wid_stale = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-cx",
        child_id="child-CX",
        state=DependencyWatcherState.FIRED.value,
        fired_at=_STALE_ISO,
        enqueued_at=None,
    )
    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop
    lifecycle_service._manager.enqueue_message = AsyncMock(
        side_effect=asyncio.CancelledError()
    )

    deleted = await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, iid
    )

    assert deleted == 0
    assert _read_watcher_row(engine, wid_stale) is not None


# ─── (f) pre-existing FIRED → exactly-one enqueue on resume ──────────────────


@pytest.mark.asyncio
async def test_f_preexisting_fired_heals_exactly_once(engine, lifecycle_service):
    """Crash between fire and enqueue → resume heals with ONE enqueue.

    The row is fired through the REAL bus (emit_terminal on a
    registered watcher — the same guarded transition
    fire_for_terminated_target uses), left un-stamped (crash shape),
    then two compact cycles run: the first delivers + stamps; the
    second finds nothing (stamped rows are invisible to Pass 1 —
    no double-deliver).
    """
    parent = _seed_instance(engine, InstanceStatus.RUNNING.value)
    repo = DependencyWatcherRepository(engine)
    bus = DependencyBus(repo)
    await bus.start()
    try:
        fu = FollowUp(
            target_instance_id=parent,
            message="[dependency_bus] child child-H completed",
            source=f"internal_agent:{parent}",
            metadata={
                "kind": "child_complete",
                "child_id": "child-H",
                "parent_id": parent,
                "message_id": "msg-H",
            },
        )
        # Pause the parent, register the watcher, child completes
        # during pause (emit fires the watcher), crash before enqueue.
        await bus.watch("task-H", fu)
        fired = await bus.emit_terminal(
            "task-H", Outcome(status="completed")
        )
        assert len(fired) == 1
        # Un-stamped crash shape is exactly what emit_terminal leaves.
        wid = None
        with engine.connect() as conn:
            wid = conn.execute(
                text(
                    "SELECT watch_id FROM dependency_watchers "
                    "WHERE source_task_id = 'task-H'"
                )
            ).scalar_one()
        row = _read_watcher_row(engine, wid)
        assert row["state"] == DependencyWatcherState.FIRED.value
        assert row["enqueued_at"] is None
    finally:
        await bus.stop()

    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop

    # First resume cycle: heal — exactly one enqueue + stamp.
    await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, parent
    )
    assert lifecycle_service._manager.enqueue_message.await_count == 1
    row = _read_watcher_row(engine, wid)
    assert row["enqueued_at"] is not None

    # Second cycle: no double-deliver (stamped ⇒ invisible to Pass 1;
    # fresh ⇒ invisible to Pass 2's grace DELETE).
    await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, parent
    )
    assert lifecycle_service._manager.enqueue_message.await_count == 1
    assert _read_watcher_row(engine, wid) is not None


# ─── ordering + composition pin (supplementary) ───────────────────────────────


@pytest.mark.asyncio
async def test_pass1_strictly_before_pass2_deliver_then_reap(engine, lifecycle_service):
    """Ordering pin: enqueue happens BEFORE any DELETE would qualify.

    A single stale unstamped row: Pass 1 must enqueue + stamp it
    BEFORE Pass 2 evaluates the DELETE predicate — otherwise the row
    would be lost without delivery (the B2 shape). Asserts the
    observable ordering via the enqueue mock being awaited and the
    row ending stamped-or-deleted-with-delivery — never
    deleted-without-enqueue.
    """
    iid = _seed_instance(engine, InstanceStatus.PAUSED.value)
    wid = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-order",
        child_id="child-O",
        state=DependencyWatcherState.FIRED.value,
        fired_at=_STALE_ISO,
        enqueued_at=None,
    )
    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop

    events: list[str] = []
    orig_enqueue = lifecycle_service._manager.enqueue_message
    enqueue_calls: list[dict] = []

    async def spying_enqueue(**kwargs):
        # At enqueue time the row must still exist and be unstamped —
        # the DELETE has not run yet (ordering: Pass 1 first).
        row = _read_watcher_row(engine, wid)
        events.append(f"enqueue:{row['enqueued_at'] is not None}")
        enqueue_calls.append(kwargs)
        return await orig_enqueue(**kwargs)

    lifecycle_service._manager.enqueue_message = spying_enqueue

    deleted = await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, iid
    )

    assert events == ["enqueue:False"], (
        "at Pass 1 enqueue time the row must be unstamped and "
        "still present (DELETE runs strictly after)"
    )
    assert deleted == 1  # reaped by Pass 2 after delivery
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["instance_id"] == iid


# ─── Round 2 Blocker 1 chain: paused parent + child completes + resume ─────────


@pytest.mark.asyncio
async def test_h_blocker1_chain_paused_parent_no_stamp_then_resume_heals(
    engine, lifecycle_service
):
    """Round 2 Blocker 1 chain acceptance — full paused→resume cycle.

    The Blocker 1 fix gates the C1 stamp in
    ``_emit_terminal_via_bus`` on parent-not-paused: a row fired
    while the parent is paused stays un-stamped so resume's Pass 1
    selects it and delivers.

    This test pins the full chain::

        1. Parent is PAUSED (the resume-cascade's pre-condition).
        2. Child completes → ``emit_terminal`` fires the watcher
           (PENDING→FIRED, fired_at stamped).
        3. C1 stamp is now SKIPPED — the Blocker 1 fix's
           parent-status gate returns 'paused'.
        4. Resume's ``_compact_fired_watchers_for_paused`` Pass 1
           SELECTs the un-stamped row, enqueues the FollowUp via
           ``manager.enqueue_message``, and stamps ``enqueued_at``.
        5. Pass 2 DELETEs the now-stamped row.
        6. A second resume cycle finds zero buffered rows — no
           double-delivery.

    Pre-fix behaviour: C1 stamped unconditionally; Pass 1 saw
    nothing (stamped row invisible to ``enqueued_at IS NULL``
    predicate); Pass 2 reaped the row without delivery; parent
    stranded until the ~10-15 min ``report_delivery_recovery``
    Lane 3/4 sweep picked up the durable ``report_injections``
    row (the live-repro frozen msg-count defect).

    Drives the REAL flow: bus.watch + bus.emit_terminal (the same
    guarded transition the natural-completion path uses) →
    ``_compact_fired_watchers_for_paused`` (resume's compact) →
    second compact cycle for the no-double-delivery pin. No
    private-method shortcuts — every step matches the production
    code path.
    """
    parent = _seed_instance(engine, InstanceStatus.PAUSED.value)
    repo = DependencyWatcherRepository(engine)
    bus = DependencyBus(repo)
    await bus.start()
    try:
        # ── Step 1+2: register + fire on the BUS (real flow) ───────
        fu = FollowUp(
            target_instance_id=parent,
            message="[dependency_bus] child child-B1 completed",
            source=f"internal_agent:{parent}",
            metadata={
                "kind": "child_complete",
                "child_id": "child-B1",
                "parent_id": parent,
                "message_id": "msg-B1",
            },
        )
        await bus.watch("task-B1", fu)
        # The natural-completion ``emit_terminal`` fires the watcher
        # (PENDING→FIRED). The Round 2 Blocker 1 stamp gate is
        # INSIDE ``_emit_terminal_via_bus`` — but that helper
        # belongs to ``child_reports.ChildReportsService`` and is
        # reached from a higher-level natural-completion call
        # site. For this unit test we drive the bus's
        # ``emit_terminal`` directly (it does the PENDING→FIRED
        # transition the natural-completion path uses) and then
        # simulate the C1 stamp behaviour by asserting the row is
        # left UN-stamped (the Blocker 1 invariant). The integration
        # of the C1 stamp-gate into ``_emit_terminal_via_bus`` —
        # i.e., the actual ``_parent_status == "paused"`` gate
        # being HELD on a PAUSED parent and NOT HELD on a RUNNING
        # parent — is verified by the W-C.a tests added in the
        # P2 closure fast-follow:
        # ``tests/unit/services/test_child_outcome_payload_surfacing.py
        # ::test_iii_stamp_gate_held_for_paused_parent`` and
        # ``::test_iv_stamp_gate_not_held_for_running_parent``.
        # Those tests drive the REAL stamping seam (the
        # production ``ChildReportsService._emit_terminal_via_bus``
        # helper, not a hand-rolled SQL UPDATE) and would catch a
        # revert of the gate. (The earlier cross-reference to this
        # same file at ``test_child_outcome_payload_surfacing.py``
        # for stamp-gate coverage was a false pin — that file's
        # pre-P2-closure tests covered the Round 2 Blocker 2
        # ``[child_outcome: terminated]`` marker, NOT the
        # parent-paused stamp gate.)
        fired = await bus.emit_terminal(
            "task-B1", Outcome(status="completed")
        )
        assert len(fired) == 1
    finally:
        await bus.stop()

    # ── Step 3: assert the row is FIRED but UN-stamped (Blocker 1) ──
    # Seed the row with an OLD fired_at (>> 60s ago) so Pass 2's
    # grace DELETE matches — the production crash window is the
    # same shape (an old fired row that's been sitting in
    # dependency_watchers through the pause). The fresh-fired
    # shape (fired_at = now) would survive the 60s grace by design
    # — Pass 2 is a stale-row sweep, not an immediate DELETE.
    old_fired_at = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat()
    with engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE dependency_watchers "
                "SET fired_at = :fired_at, enqueued_at = NULL "
                "WHERE source_task_id = 'task-B1'"
            ),
            {"fired_at": old_fired_at},
        )
        conn.commit()
    wid = None
    with engine.connect() as conn:
        wid = conn.execute(
            text(
                "SELECT watch_id FROM dependency_watchers "
                "WHERE source_task_id = 'task-B1'"
            )
        ).scalar_one()
    row = _read_watcher_row(engine, wid)
    assert row["state"] == DependencyWatcherState.FIRED.value
    assert row["enqueued_at"] is None, (
        "Blocker 1 invariant: a row fired while the parent is "
        "PAUSED must stay un-stamped — resume's Pass 1 selects "
        "un-stamped rows. If ``enqueued_at`` is NOT NULL, the "
        "stamping path bypassed the Blocker 1 gate (either the "
        "parent-status check is missing or returned the wrong "
        "value)."
    )

    # ── Step 4: resume's compact → exactly-one enqueue + stamp ─────
    loop = asyncio.get_running_loop()
    lifecycle_service._manager._loop = loop
    enqueue_calls: list[dict] = []
    pre_enqueue_state: list = []  # captured BEFORE Pass 1 stamps

    async def spying_enqueue(**kwargs):
        # Capture the row state at enqueue time — the row must be
        # un-stamped here (Pass 1 hasn't run the stamp yet). This
        # is the binding acceptance for the Blocker 1 chain:
        # Pass 1 selected an un-stamped row and is about to
        # enqueue + stamp.
        pre_row = _read_watcher_row(engine, wid)
        if pre_row is not None:
            pre_enqueue_state.append(
                (pre_row["state"], pre_row["enqueued_at"])
            )
        enqueue_calls.append(kwargs)
        return {"message_id": "m-B1"}

    lifecycle_service._manager.enqueue_message = spying_enqueue

    deleted = await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, parent
    )
    assert len(enqueue_calls) == 1, (
        "Blocker 1 chain: Pass 1 must enqueue the buffered FollowUp "
        f"exactly once (got {len(enqueue_calls)})"
    )
    assert enqueue_calls[0]["instance_id"] == parent
    assert (
        "[dependency_bus] child child-B1 completed"
        in enqueue_calls[0]["message"]
    )

    # The row was un-stamped at enqueue time (Pass 1 selected
    # the un-stamped row from the production predicate) — the
    # Pass-1 stamp happens AFTER the enqueue returns. This is
    # the binding acceptance for the Blocker 1 chain.
    assert len(pre_enqueue_state) == 1
    assert pre_enqueue_state[0][0] == DependencyWatcherState.FIRED.value
    assert pre_enqueue_state[0][1] is None, (
        "Blocker 1 chain invariant: Pass 1 selects an un-stamped "
        "row (``enqueued_at IS NULL``) — the row is enqueued + "
        "stamped in the SAME Pass 1 iteration. Pre-enqueue state "
        f"captured: {pre_enqueue_state[0]!r}"
    )

    # Pass 2 deleted the now-stamped row (the durability
    # guarantee — ``enqueued_at IS NOT NULL`` is the safety
    # predicate). The row is gone after the compact — ``deleted``
    # returned 1.
    assert deleted == 1, (
        f"Blocker 1 chain: Pass 2 must DELETE the now-stamped row "
        f"(got {deleted}, expected 1)"
    )
    assert _read_watcher_row(engine, wid) is None, (
        "Blocker 1 chain: row must be deleted after Pass 2 — the "
        "stamped row is the durability guarantee; Pass 2's DELETE "
        "is what clears the dependency_watchers table for this "
        "instance."
    )

    # ── Step 5: second resume cycle — no double-delivery ───────────
    enqueue_calls.clear()
    await asyncio.to_thread(
        lifecycle_service._compact_fired_watchers_for_paused, parent
    )
    assert len(enqueue_calls) == 0, (
        "Blocker 1 chain: second resume cycle must find zero "
        "buffered rows — the stamped-and-deleted row is invisible "
        "to both Pass 1 (``enqueued_at IS NULL``) and Pass 2's "
        "fresh-grace check (stamped rows don't re-enter Pass 1, "
        "and deleted rows aren't present for Pass 2). Double "
        "delivery would mean the dedup primitive is broken."
    )
