"""Tests for the A5 watchdog-notifies-its-own-nudge fix (Batch A of WC wake/resilience).

A5 (Batch A, 2026-09-11): the wedge-notice path in
``daemon/services/waiting_children_watchdog.py`` must dispatch a
DIRECT ``worker_pool.notify_work()`` after ``enqueue_message``
returns, instead of relying on the notice's own creation-time
notify.

Pre-A5 the wedge notice path was: enqueue_message writes
MessageQueue + Task rows, flips WC→RUNNING, internally calls
worker_pool.notify_work(). Incident 33252 (2026-09-11) proved
that creation-time notify is unreliable on the wedge path —
the notice was stranded PENDING for zero claims ever. The cause
is opaque (could be a lost wake between enqueue commit and pool
notify, a defer-gate race parking the notice behind a busy
witness, etc.).

The A5 fix: the watchdog dispatches a redundant
``worker_pool.notify_work()`` directly, AFTER enqueue_message
returns. Same primitive as task creation + A2 autopromote + A3
sweep + A4 orphan-detection. Idempotent on the pool side
(``notify_work()`` is a condition-variable signal — double-notify
is wasted but benign).

Test surface (this file):

* **wedge_dispatches_direct_notify_work_after_enqueue** — happy
  path: wedged parent → enqueue_message fires → DIRECT
  notify_work fires once.
* **wedge_no_notify_when_backstop_suppressed_by_b_guard** —
  B.S.5 active (parent has an open (b) notice) → wedge skips
  entirely → no enqueue, no notify.
* **wedge_no_notify_when_paused_parent** — PAUSED parents are
  skipped → no enqueue, no notify.
* **wedge_no_notify_when_cooldown_active** — a parent already
  in ``_wedge_notified`` is skipped → no enqueue, no notify.
* **wedge_no_notify_when_live_carrier_present** — a healthy
  parent with a live PROCESS_REPORT carrier → no wedge → no
  notify.
* **wedge_direct_notify_failure_does_not_abort_sweep** — a
  direct ``notify_work()`` that raises is caught; the sweep
  proceeds.
* **wedge_direct_notify_safe_when_worker_pool_none** —
  ``worker_pool=None`` (legacy test fixture) → no raise, no
  spurious notify, DEBUG log only.
* **census_stays_23_1_0** — A5 fix is a method call on an
  injected pool; no new writer / creator / mint site.

Harness: ``WaitingChildrenWatchdog`` against an in-memory
SQLite engine. The ``manager`` is an ``AsyncMock`` shim; the
wedge precondition (zero non-terminal children, zero live
carrier) is forced via mocks so the wedge pass actually fires.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.waiting_children_watchdog import (
    WaitingChildrenWatchdog,
)


# ─── Fixtures / helpers (mirror the watchdog test file) ─────────────────


@pytest.fixture
def engine():
    """In-memory SQLite with the Instance table created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> SQLModelInstanceRepository:
    """Repository bound to the in-memory engine."""
    return SQLModelInstanceRepository(engine=engine)


@pytest.fixture
def make_instance(repo):
    """Factory that inserts an ``Instance`` row directly via SQL."""

    def _make(
        *,
        instance_id: str,
        status: str = InstanceStatus.WAITING_CHILDREN.value,
        parent_id: str | None = None,
        age_seconds: int = 0,
    ) -> str:
        created_at = datetime.now(timezone.utc)
        if age_seconds > 0:
            from datetime import timedelta
            created_at = created_at - timedelta(seconds=age_seconds)
        with repo.engine.begin() as conn:
            conn.execute(
                __import__("sqlalchemy").text(
                    """
                    INSERT INTO instances
                        (instance_id, agent_id, agent_dir, status,
                         project_id, parent_id, created_at, updated_at,
                         version, last_activity_at)
                    VALUES
                        (:instance_id, 'developer', 'agents/developer',
                         :status, 'test-project', :parent_id,
                         :created_at, :created_at, 1, :created_at)
                    """
                ),
                {
                    "instance_id": instance_id,
                    "status": status,
                    "parent_id": parent_id,
                    "created_at": created_at.isoformat(),
                },
            )
        return instance_id

    return _make


def _build_wedge_watchdog(
    repo: SQLModelInstanceRepository,
    manager: AsyncMock,
    *,
    worker_pool: MagicMock | None,
    task_repo: MagicMock | None = None,
) -> WaitingChildrenWatchdog:
    """Build a watchdog with the wedge precondition forced
    (zero live carriers) — every wedged-shape parent fires the
    wedge pass."""
    if task_repo is None:
        task_repo = MagicMock()
        task_repo.list_live_process_report_carriers_for_instance = (
            lambda instance_id: []
        )
    w = WaitingChildrenWatchdog(
        repo,
        manager,
        interval_seconds=1,
        hang_threshold_seconds=1,
        task_repository=task_repo,
    )
    # Wire the worker_pool onto the manager shim — A5 reads from
    # ``self._manager._worker_pool``.
    manager._worker_pool = worker_pool
    return w


# ─── Happy path ─────────────────────────────────────────────────────────


class TestA5WedgeNotifyWork:
    """A5: the wedge-notice path dispatches a DIRECT
    ``worker_pool.notify_work()`` AFTER ``enqueue_message`` returns,
    in addition to the internal creation-time notify.

    Incident 33252 (2026-09-11): the wedge notice was stranded
    PENDING for zero claims ever. The pre-A5 creation-time notify
    was unreliable; A5 dispatches a redundant wake from the
    watchdog directly."""

    @pytest.mark.asyncio
    async def test_wedge_dispatches_direct_notify_work_after_enqueue(
        self, repo, engine, make_instance, caplog
    ):
        """Happy path — wedged parent → enqueue_message fires →
        DIRECT notify_work fires once."""
        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()
        worker_pool = MagicMock()
        parent_id = make_instance(
            instance_id="wc-parent-a5-happy",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        # Wedged shape: one COMPLETED child (zero non-terminal
        # children after B.S.5-aware predicate).
        make_instance(
            instance_id="child-completed-a5",
            status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
        )
        w = _build_wedge_watchdog(
            repo, manager, worker_pool=worker_pool,
        )

        with caplog.at_level(logging.WARNING):
            stats = await w.run_once()

        # Wedge fired (the wedge counter is exposed via the
        # ``wedge_notices_enqueued`` property, NOT the ``stats``
        # dict — the latter is the HANG-pass stats; see
        # ``WaitingChildrenWatchdog.run_once`` and the wedge
        # counter declaration in
        # ``daemon/services/waiting_children_watchdog.py``).
        assert w.wedge_notices_enqueued == 1, (
            f"Wedge MUST fire on the wedged shape; got "
            f"wedge_notices_enqueued={w.wedge_notices_enqueued!r}, "
            f"stats={stats!r}"
        )
        assert manager.enqueue_message.await_count == 1, (
            "enqueue_message must be called exactly once for the "
            "wedge notice"
        )
        # The direct notify_work fires ONCE for the wedge notice.
        assert worker_pool.notify_work.call_count == 1, (
            f"worker_pool.notify_work() must be called exactly once "
            f"per wedge notice (the A5 redundant notify); got "
            f"call_count={worker_pool.notify_work.call_count}"
        )

    @pytest.mark.asyncio
    async def test_wedge_no_notify_when_backstop_suppressed_by_b_guard(
        self, repo, engine, make_instance
    ):
        """B.S.5 active (parent has an open (b) notice) → wedge
        skips entirely → no enqueue, no notify. A spurious wake
        would falsely unstick a (b)-guarded parent — the A5
        notify is only dispatched when the wedge notice actually
        enqueues."""
        from daemon.services import report_integrity_guard as rig

        rig._B_NOTICE_LEDGER.clear()
        try:
            manager = AsyncMock()
            manager.enqueue_message = AsyncMock()
            worker_pool = MagicMock()
            parent_id = make_instance(
                instance_id="wc-parent-a5-bguarded",
                status=InstanceStatus.WAITING_CHILDREN.value,
            )
            make_instance(
                instance_id="child-completed-a5-2",
                status=InstanceStatus.COMPLETED.value,
                parent_id=parent_id,
            )

            # Open a (b) notice episode for the parent — the
            # wedge backstop MUST skip.
            rig._B_NOTICE_LEDGER[parent_id] = (
                "child-completed-a5-2:completed:PENDING"
            )

            w = _build_wedge_watchdog(
                repo, manager, worker_pool=worker_pool,
            )
            stats = await w.run_once()

            assert w.wedge_notices_enqueued == 0, (
                f"B.S.5: wedge_notices_enqueued MUST stay at 0; "
                f"got {w.wedge_notices_enqueued!r}"
            )
            assert w.wedge_notices_enqueued == 0, (
                f"B.S.5: wedge_notices_enqueued counter MUST stay "
                f"at 0; got "
                f"wedge_notices_enqueued={w.wedge_notices_enqueued!r}"
            )
            assert manager.enqueue_message.await_count == 0, (
                "B.S.5: no enqueue_message call when (b) guards"
            )
            assert worker_pool.notify_work.call_count == 0, (
                f"B.S.5: no direct notify_work when wedge is "
                f"suppressed (the A5 wake MUST only fire on a "
                f"real wedge notice); got call_count="
                f"{worker_pool.notify_work.call_count}"
            )
        finally:
            rig._B_NOTICE_LEDGER.clear()

    @pytest.mark.asyncio
    async def test_wedge_no_notify_when_paused_parent(
        self, repo, engine, make_instance
    ):
        """PAUSED parents are skipped → no enqueue, no notify.
        Waking a PAUSED parent is a contract violation — the
        resume path owns the wake, not the watchdog."""
        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()
        worker_pool = MagicMock()
        make_instance(
            instance_id="wc-parent-a5-paused",
            status=InstanceStatus.PAUSED.value,
        )

        w = _build_wedge_watchdog(
            repo, manager, worker_pool=worker_pool,
        )
        stats = await w.run_once()

        assert w.wedge_notices_enqueued == 0, (
            f"PAUSED parent MUST be skipped; got stats={stats!r}, "
            f"wedge_notices_enqueued={w.wedge_notices_enqueued!r}"
        )
        assert worker_pool.notify_work.call_count == 0, (
            f"PAUSED parent MUST NOT trigger notify_work; "
            f"got call_count={worker_pool.notify_work.call_count}"
        )

    @pytest.mark.asyncio
    async def test_wedge_no_notify_when_cooldown_active(
        self, repo, engine, make_instance
    ):
        """A parent already in ``_wedge_notified`` is skipped
        (anti-spam cooldown) → no enqueue, no notify. The A5
        wake MUST respect the cooldown — a duplicate notify
        would be benign but the test pins the contract."""
        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()
        worker_pool = MagicMock()
        parent_id = make_instance(
            instance_id="wc-parent-a5-cooldown",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        make_instance(
            instance_id="child-completed-a5-3",
            status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
        )

        w = _build_wedge_watchdog(
            repo, manager, worker_pool=worker_pool,
        )
        # Pre-seed the cooldown so the wedge skip applies.
        w._wedge_notified.add(parent_id)

        stats = await w.run_once()

        assert w.wedge_notices_enqueued == 0, (
            f"Cooldown MUST suppress the wedge; got stats={stats!r}, "
            f"wedge_notices_enqueued={w.wedge_notices_enqueued!r}"
        )
        assert worker_pool.notify_work.call_count == 0

    @pytest.mark.asyncio
    async def test_wedge_no_notify_when_live_carrier_present(
        self, repo, engine, make_instance
    ):
        """A healthy parent with a live PROCESS_REPORT carrier
        → no wedge (the carrier will deliver naturally) → no
        notify. The A5 wake MUST NOT fire on a healthy parent."""
        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()
        worker_pool = MagicMock()
        make_instance(
            instance_id="wc-parent-a5-healthy",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )

        # Wire a task_repo that reports a live carrier → the
        # wedge predicate skips this parent.
        task_repo = MagicMock()
        task_repo.list_live_process_report_carriers_for_instance = (
            lambda instance_id: [
                MagicMock(id=1, instance_id=instance_id)
            ]
        )
        w = _build_wedge_watchdog(
            repo, manager,
            worker_pool=worker_pool, task_repo=task_repo,
        )
        stats = await w.run_once()

        assert w.wedge_notices_enqueued == 0, (
            f"Live-carrier parent MUST be skipped; got stats="
            f"{stats!r}, wedge_notices_enqueued="
            f"{w.wedge_notices_enqueued!r}"
        )
        assert worker_pool.notify_work.call_count == 0

    @pytest.mark.asyncio
    async def test_wedge_direct_notify_failure_does_not_abort_sweep(
        self, repo, engine, make_instance, caplog
    ):
        """A direct ``notify_work()`` that raises is caught; the
        sweep advances without aborting. The A3 sweep is the
        systemic backstop for any notify that crashes."""
        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()
        worker_pool = MagicMock()
        worker_pool.notify_work.side_effect = RuntimeError(
            "pool blip"
        )
        parent_id = make_instance(
            instance_id="wc-parent-a5-blip",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        make_instance(
            instance_id="child-completed-a5-blip",
            status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
        )

        w = _build_wedge_watchdog(
            repo, manager, worker_pool=worker_pool,
        )

        with caplog.at_level(logging.WARNING):
            stats = await w.run_once()

        # The wedge still fired — the enqueue landed; only the
        # redundant notify was lost.
        assert w.wedge_notices_enqueued == 1, (
            f"notify_work() raising MUST NOT abort the wedge; "
            f"got wedge_notices_enqueued={w.wedge_notices_enqueued!r}, "
            f"stats={stats!r}"
        )

    @pytest.mark.asyncio
    async def test_wedge_direct_notify_safe_when_worker_pool_none(
        self, repo, engine, make_instance
    ):
        """``worker_pool=None`` (legacy test fixture / pre-wiring
        lifespan) → no raise, no spurious notify, DEBUG log only.
        The wedge still enqueues (the enqueue path is
        independent of the watchdog's direct-notify code)."""
        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()
        parent_id = make_instance(
            instance_id="wc-parent-a5-no-pool",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        make_instance(
            instance_id="child-completed-a5-no-pool",
            status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
        )

        # ``worker_pool=None`` — the A5 branch must skip silently.
        w = _build_wedge_watchdog(
            repo, manager, worker_pool=None,
        )
        stats = await w.run_once()

        assert w.wedge_notices_enqueued == 1, (
            f"Wedge MUST still fire when worker_pool is None — "
            f"the enqueue is independent of A5; got "
            f"wedge_notices_enqueued={w.wedge_notices_enqueued!r}, "
            f"stats={stats!r}"
        )


# ─── Census static guard ────────────────────────────────────────────────


class TestA5ConstitutionStatic:
    """A5 fix is a method call on an injected pool; no new
    ``admission_state`` writes, no new JobItem creators, no new
    ``work_id`` mints. Census stays at 23/1/0."""

    def test_census_stays_23_1_0(self):
        from daemon.job_state import constitution

        assert (
            len(constitution.KNOWN_ADMISSION_STATE_WRITERS) == 23
        ), (
            f"A5 introduced new admission_state writers — census "
            f"drifted from 23 to "
            f"{len(constitution.KNOWN_ADMISSION_STATE_WRITERS)}"
        )
        assert len(constitution.KNOWN_JOBITEM_CREATORS) == 1, (
            f"A5 introduced new JobItem creators — census drifted "
            f"from 1 to "
            f"{len(constitution.KNOWN_JOBITEM_CREATORS)}"
        )
        assert len(constitution.KNOWN_MINT_SITES) == 0, (
            f"A5 introduced new work_id mints — census drifted "
            f"from 0 to {len(constitution.KNOWN_MINT_SITES)}"
        )

    def test_a5_block_uses_canonical_seam(self):
        """The A5 redundant-notify uses the canonical
        ``worker_pool.notify_work()`` seam — same primitive as
        A2 / A3 / A4."""
        from pathlib import Path

        prod_path = (
            Path(__file__).parent.parent.parent.parent
            / "daemon"
            / "services"
            / "waiting_children_watchdog.py"
        )
        contents = prod_path.read_text()
        assert (
            "worker_pool.notify_work()" in contents
        ), (
            "A5 redundant-notify MUST use the canonical "
            "notify_work seam — same primitive as A2 / A3 / A4"
        )
        # The A5 block sits AFTER the ``enqueue_message`` await.
        # Anchor on the A5 comment marker to find the structural
        # call site (a generic substring search hits the docstring
        # + comments + the actual call).
        a5_marker_idx = contents.find("Batch A — A5")
        assert a5_marker_idx != -1, (
            "A5 marker comment missing — the structural change "
            "may have regressed"
        )
        # Find the FIRST ``worker_pool.notify_work()`` call AFTER
        # the A5 marker — that is the A5 redundant-notify.
        a5_call_idx = contents.find(
            "_notify_result = worker_pool.notify_work()",
            a5_marker_idx,
        )
        assert a5_call_idx != -1, (
            "A5 redundant-notify call site missing after the "
            "A5 marker comment"
        )
        # The A5 call site must come AFTER the enqueue_message
        # await for the same parent (the structural position).
        # The A5 marker comment lives BETWEEN the enqueue and the
        # call (it's the structural hand-off comment) — search
        # BACKWARDS from the A5 marker for the most recent
        # ``await self._manager.enqueue_message``. The call site
        # is multi-line; match the line-stripped prefix.
        # Find the LAST enqueue BEFORE the A5 call site.
        enqueue_idx_before = contents.rfind(
            "await self._manager.enqueue_message",
            0, a5_call_idx,
        )
        assert enqueue_idx_before != -1 and enqueue_idx_before < a5_call_idx, (
            f"A5 notify_work() MUST follow the enqueue_message "
            f"await (the redundant wake fires AFTER the notice "
            f"row is committed). Found enqueue at "
            f"{enqueue_idx_before!r}, A5 call at {a5_call_idx!r}"
        )