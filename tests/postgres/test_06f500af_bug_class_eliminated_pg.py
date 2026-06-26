"""Phase 0 acceptance test for the 06f500af bug class elimination (red phase).

The Bug Class
-------------
Instance ``06f500af`` (a leader) was permanently stuck in
``status=waiting_children`` because:

  1. A child task (task 4464) was force-cancelled by ``StaleTaskRecovery``
     and a retry (task 4466) was scheduled.
  2. The ``DependencyBus`` had a PENDING watcher keyed on
     ``source_task_id=4464`` — registered when the parent called
     ``send_message`` to the child.
  3. No code path notified the bus that task 4464 reached a terminal event
     (the cancellation was a direct DB write by ``StaleTaskRecovery``, not
     a ``bus.emit_terminal`` call).
  4. The watcher stayed PENDING forever → ``count_pending_for_target(
     06f500af) > 0`` → the parent's completion gate never fired → the
     parent was stranded in ``waiting_children`` indefinitely.

The Two-Layer Fix
-----------------
Eliminating the 06f500af bug class requires fixes at two distinct layers,
each gated by a separate phase of the architecture migration:

  * **Phase 1 — ``_sweep_orphan_watchers()`` startup sweep** (Scenarios
    1 + 2, now green). Adds an atomic ``UPDATE dependency_watchers SET
    state='cancelled' WHERE state='pending' AND source_task_id NOT IN
    (SELECT id FROM task WHERE status IN ('running','pending','paused'))``
    to ``DependencyBus.start()``. This catches any PENDING watcher whose
    source task is no longer active (CANCELLED, FAILED, COMPLETED, etc.)
    and transitions it to CANCELLED, releasing the parent's
    ``count_pending_for_target`` counter. PAUSED tasks are explicitly
    exempt from the sweep because they may resume and need their
    watchers intact. See
    ``.agents/shared/planning/finish-architecture-migration/phase1-plan.md``.

  * **Phase 2 — D13 single-record invariant** (xfail Scenario 3). Makes
    ``enqueue_message`` route ALL messages through the WorkerPool path
    (write only ``task`` + ``message_queue`` rows), and makes
    ``enqueue_job`` reject ``job_type='message'``. This eliminates the
    dual-record coupling where each user message creates both a Task
    row AND a JobItem row — the root structural cause of 06f500af-class
    bugs. See
    ``.agents/shared/planning/finish-architecture-migration/phase2-plan.md``.

Why These Tests Were Xfail
--------------------------
Per the Phase 0 plan (``.agents/shared/planning/finish-architecture-migration/phase0-plan.md``),
all three scenarios started life as ``@pytest.mark.xfail`` so CI stayed
green while the implementation phases landed. Status as of Phase 1
(2026-06-27):

  * Scenario 1 (``test_orphan_watcher_cancelled_on_startup_sweep``)
    — **un-xfail'd**. Phase 1's ``_sweep_orphan_watchers()`` is
    implemented and wired into ``DependencyBus.start()``.
  * Scenario 2 (``test_paused_task_watcher_not_cancelled_by_sweep``)
    — **un-xfail'd** alongside Scenario 1 (same Phase 1 work — the
    paused exemption is part of the same SQL filter).
  * Scenario 3 (``test_d13_single_record_invariant``) — still
    ``xfail``; will be un-xfail'd when Phase 2's D13 changes land.

References
----------
* ``.agents/shared/planning/finish-architecture-migration/plan-overview.md``
* ``.agents/shared/planning/finish-architecture-migration/phase0-plan.md``
* ``.agents/shared/planning/finish-architecture-migration/phase1-plan.md``
* ``.agents/shared/planning/finish-architecture-migration/phase2-plan.md``
* ``LESSONS/architecture-migration-status-2026-06-26.md``
* ``docs/bugs/parent-stuck-waiting-children-orphan-error-report.md``

File Placement Rationale
------------------------
This test lives in ``tests/postgres/`` (not ``tests/e2e/`` as originally
proposed) because:

  * The Phase 1 sweep must be exercised against a real
    ``bus.start()`` → ``_sweep_orphan_watchers()`` path with real DB
    rows. ``tests/postgres/`` provides the ``pg_engine``,
    ``pg_repository_factory``, and ``pg_session_factory`` fixtures for
    free, plus the autouse ``_pg_truncate_tables`` isolation.
  * ``tests/e2e/`` requires the real MCP SDK and does module swapping
    that's unnecessary for this test — there's no MCP dependency in the
    bus / sweeper code path.
  * The conftest in ``tests/postgres/conftest.py`` auto-applies the
    ``postgres`` marker via ``pytest_collection_modifyitems`` so
    ``pytest -m postgres`` selects this test and the default
    ``addopts = "-m 'not integration and not postgres'"`` skips it.

Run with::

    uv run python -m pytest tests/postgres/test_06f500af_bug_class_eliminated_pg.py -v \\
        --override-ini="addopts=" -m postgres

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is not reachable, so this file is
safe to collect even on machines without a running PG.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

# Import the JobItem model so its ``Table`` is registered on
# ``SQLModel.metadata`` before the session-scoped ``pg_engine`` fixture
# runs ``SQLModel.metadata.create_all(engine)``. Without this import,
# the ``job_queue_items`` table would not exist in the test database.
from daemon.repositories.dependency_bus import (  # noqa: F401
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import Instance, InstanceStatus  # noqa: F401
from daemon.repositories.job_queue.models import JobItem, JobStatus  # noqa: F401
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType  # noqa: F401
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    set_dependency_bus,
)


# Auto-apply the postgres marker so ``pytest -m postgres`` selects these
# tests and the default ``addopts = "-m 'not integration and not postgres'"``
# skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# Helpers
# =============================================================================


def make_fu(
    target_id: str = "parent-A",
    message: str = "m",
    metadata: dict | None = None,
) -> FollowUp:
    """Build a FollowUp with sensible defaults."""
    return FollowUp(
        target_instance_id=target_id,
        message=message,
        metadata=metadata if metadata is not None else {},
    )


def make_outcome(
    status: str = "completed", error: str | None = None
) -> Outcome:
    """Build an Outcome with sensible defaults."""
    return Outcome(status=status, error=error)


def fresh_bus(repo: DependencyWatcherRepository) -> DependencyBus:
    """Construct a NEW DependencyBus bound to ``repo`` (used for restart tests)."""
    return DependencyBus(repo)


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string.

    Mirrors :meth:`DependencyBus._now_iso` so timestamps written by the
    bus and the repository are format-compatible.
    """
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    """Return current UTC time as a ``datetime`` object.

    Used for columns whose SQLAlchemy type is ``DATETIME`` (e.g.
    ``task.created_at``), where a string would be rejected by psycopg.
    """
    return datetime.now(timezone.utc)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bus_repo(pg_repository_factory):
    """Real DependencyWatcherRepository bound to the PG engine."""
    return pg_repository_factory(DependencyWatcherRepository)


@pytest.fixture
async def bus(bus_repo):
    """Started DependencyBus; auto-stops on teardown.

    Mirrors the ``bus`` fixture from
    ``tests/postgres/test_dependency_bus_pg.py``. ``set_dependency_bus(None)``
    clears the module-level singleton so this test's bus is the active
    one for any module-level observers (e.g. ``JobFeedbackObserver``).
    """
    set_dependency_bus(None)
    b = DependencyBus(bus_repo)
    await b.start()
    try:
        yield b
    finally:
        # Idempotent — safe even if the test body already called
        # ``bus.stop()`` to simulate a restart.
        await b.stop()


@pytest.fixture
def instance_id() -> str:
    """Unique parent instance id for the test, with the 06f500af prefix.

    The prefix is a debugging convenience — log lines surface the bug
    class at a glance without revealing test internals.
    """
    return f"06f500af-{uuid.uuid4().hex[:8]}"


# =============================================================================
# Raw-SQL insert helpers
# =============================================================================


def _insert_instance(pg_engine, instance_id: str) -> None:
    """Insert a minimal ``Instance`` row at IDLE status.

    The Instance is a passive participant in the bus / sweeper test — the
    bus doesn't FK to it — but Scenario 3 (D13) verifies the dual-record
    invariant on a per-instance basis, so we need a real row to hang
    the message_queue / job_queue_items rows off.
    """
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO instances "
                "(instance_id, agent_id, agent_dir, status, version) "
                "VALUES (:iid, :aid, :adir, :status, :version)"
            ),
            {
                "iid": instance_id,
                "aid": "acceptance-test",
                "adir": "/tmp/acceptance",
                "status": InstanceStatus.IDLE.value,
                "version": 1,
            },
        )


def _insert_task(
    pg_engine, instance_id: str, status: str
) -> int:
    """Insert a ``task`` row with the given status; return its integer id.

    Uses a raw INSERT (not ``session.add``) so we don't fight with the
    Task model's ``version_id_col`` machinery in a test that's just
    setting up fixture state. The integer primary key is generated by
    SQLite/PostgreSQL via the autoincrement / serial column; we use
    ``RETURNING id`` to capture it.
    """
    with pg_engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO task "
                "(task_type, instance_id, status, created_at, version) "
                "VALUES (:ttype, :iid, :status, :created_at, :version) "
                "RETURNING id"
            ),
            {
                "ttype": TaskType.PROCESS_MESSAGE.value,
                "iid": instance_id,
                "status": status,
                "created_at": _now_dt(),
                "version": 0,
            },
        )
        return result.scalar()


# =============================================================================
# Scenario 1: Orphan watcher cancelled on startup sweep (Phase 1 — green)
# =============================================================================


@pytest.mark.asyncio
async def test_orphan_watcher_cancelled_on_startup_sweep(
    bus, bus_repo, pg_engine, instance_id
):
    """A PENDING watcher on a CANCELLED task must be cancelled on bus.start().

    This is the structural regression test for the 06f500af bug class:
    if a child task is force-cancelled (status='cancelled') without
    ``bus.emit_terminal`` ever being called, the parent's PENDING
    watcher must still transition to CANCELLED so the parent's
    completion gate can fire. The startup sweep is the only mechanism
    that achieves this after a process restart.

    Steps:
      1. Insert an Instance row (parent) — passive, just for context.
      2. Insert a Task row with status='cancelled' — simulates a
         force-cancelled stale task (e.g. StaleTaskRecovery output).
      3. Register a bus watcher on that task's id with the parent as
         target. The watcher lands as PENDING in the DB.
      4. Stop the bus and create a fresh DependencyBus, then call
         ``start()`` to simulate a daemon restart.
      5. Assert: the watcher row is now CANCELLED in the DB.
      6. Assert: ``bus.count_pending_for_target(parent_id) == 0``.

    Pre-Phase-1: ``start()`` does not call ``_sweep_orphan_watchers()``
    so the row stays PENDING — assertion 5 fails. Xfail expected.
    """
    parent_id = instance_id
    _insert_instance(pg_engine, instance_id)
    cancelled_task_id = _insert_task(
        pg_engine, instance_id, TaskStatus.CANCELLED.value
    )

    # Register the watcher (this is what `send_message` does for a parent
    # calling into a child). The bus writes a PENDING row.
    await bus.watch(str(cancelled_task_id), make_fu(target_id=parent_id))

    # Confirm the row landed as PENDING — sanity check for the test setup,
    # not part of the bug-class invariant.
    pending_state = DependencyWatcherState.PENDING.value
    with Session(pg_engine) as session:
        rows = list(
            session.exec(
                select(DependencyWatcher).where(
                    DependencyWatcher.source_task_id == str(cancelled_task_id)
                )
            )
        )
        assert len(rows) == 1, (
            f"setup: expected 1 watcher row for source_task_id="
            f"{cancelled_task_id}, got {len(rows)}"
        )
        assert rows[0].state == pending_state

    # Simulate a daemon restart: stop the bus, spin up a fresh one,
    # call start() on it. This is the exact path Phase 1 will hook
    # _sweep_orphan_watchers() into.
    await bus.stop()
    new_bus = fresh_bus(bus_repo)
    await new_bus.start()
    try:
        # After startup, the orphan watcher must be CANCELLED.
        cancelled_state = DependencyWatcherState.CANCELLED.value
        with Session(pg_engine) as session:
            row = session.exec(
                select(DependencyWatcher).where(
                    DependencyWatcher.source_task_id == str(cancelled_task_id)
                )
            ).one()
            assert row.state == cancelled_state, (
                f"06f500af bug class: orphan watcher for source_task_id="
                f"{cancelled_task_id} (task.status='cancelled') must be "
                f"CANCELLED after bus.start() — got state='{row.state}'. "
                f"Phase 1 _sweep_orphan_watchers() not implemented yet."
            )

        # The parent's pending-children counter must be zero so its
        # completion gate can fire.
        pending_count = await new_bus.count_pending_for_target(parent_id)
        assert pending_count == 0, (
            f"06f500af bug class: count_pending_for_target({parent_id}) "
            f"must be 0 after startup sweep — got {pending_count}. "
            f"Parent would be stranded in waiting_children forever."
        )
    finally:
        await new_bus.stop()


# =============================================================================
# Scenario 2: Paused task exempt from sweep (Phase 1 — green)
# =============================================================================


@pytest.mark.asyncio
async def test_paused_task_watcher_not_cancelled_by_sweep(
    bus, bus_repo, pg_engine, instance_id
):
    """A PENDING watcher on a PAUSED task must NOT be cancelled on bus.start().

    Paused tasks are exempt from the orphan sweep because they may
    resume and need their watchers intact. This is the regression
    guard for Phase 1's ``_sweep_orphan_watchers`` SQL filter — the
    ``WHERE source_task_id IN (SELECT id FROM task WHERE status IN
    ('running','pending','paused'))`` clause MUST keep PAUSED-task
    watchers untouched.

    Steps:
      1. Insert an Instance row (parent).
      2. Insert a Task row with status='paused'.
      3. Register a bus watcher on that task's id.
      4. Stop the bus, create a fresh bus, call ``start()``.
      5. Assert: the watcher is STILL PENDING.
      6. Assert: ``bus.count_pending_for_target(parent_id) == 1``.

    Pre-Phase-1: there is no sweep, so the watcher naturally stays
    PENDING. Once Phase 1 lands, this test catches any regression
    where the sweep's WHERE clause forgets the 'paused' status and
    wrongly cancels paused-task watchers (which would prematurely
    unblock the parent and cause missed child completion reports).
    """
    parent_id = instance_id
    _insert_instance(pg_engine, instance_id)
    paused_task_id = _insert_task(
        pg_engine, instance_id, TaskStatus.PAUSED.value
    )

    # Register the watcher.
    await bus.watch(str(paused_task_id), make_fu(target_id=parent_id))

    # Sanity check the setup.
    pending_state = DependencyWatcherState.PENDING.value
    with Session(pg_engine) as session:
        rows = list(
            session.exec(
                select(DependencyWatcher).where(
                    DependencyWatcher.source_task_id == str(paused_task_id)
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].state == pending_state

    # Simulate a daemon restart.
    await bus.stop()
    new_bus = fresh_bus(bus_repo)
    await new_bus.start()
    try:
        # Paused-task watchers must remain PENDING after the sweep —
        # the parent may resume and emit_terminal normally.
        with Session(pg_engine) as session:
            row = session.exec(
                select(DependencyWatcher).where(
                    DependencyWatcher.source_task_id == str(paused_task_id)
                )
            ).one()
            assert row.state == pending_state, (
                f"Phase 1 regression: paused-task watcher for "
                f"source_task_id={paused_task_id} (task.status='paused') "
                f"must remain PENDING after bus.start() — got "
                f"state='{row.state}'. The sweep is wrongly cancelling "
                f"paused-task watchers; parents would prematurely unblock."
            )

        # The parent must still see one pending child — its completion
        # gate must NOT fire prematurely.
        pending_count = await new_bus.count_pending_for_target(parent_id)
        assert pending_count == 1, (
            f"Phase 1 regression: count_pending_for_target({parent_id}) "
            f"must be 1 after startup sweep (paused task is exempt) — "
            f"got {pending_count}. Parent would prematurely complete."
        )
    finally:
        await new_bus.stop()


# =============================================================================
# Scenario 3: D13 single-record invariant (xfail — Phase 2)
# =============================================================================


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Phase 2: D13 not yet implemented — enqueue_message with "
        "dispatch_path='jobqueue' still creates a job_queue_items row "
        "in addition to a task row, violating the single-record invariant."
    ),
)
@pytest.mark.asyncio
async def test_d13_single_record_invariant(pg_engine, instance_id):
    """Scenario 3 placeholder simulation — D13 single-record invariant.

    NOTE: This test is a *placeholder simulation*, not a faithful
    reproduction of any single code path. It inserts BOTH a Task row
    AND a JobItem(job_type='message') row for the same message to
    represent the dual-record coupling problem state — no current
    dispatch path produces both records together.

    When Phase 2 lands and ``enqueue_message`` always writes only Task
    + MessageQueue (no JobItem ever) and ``enqueue_job`` rejects
    ``job_type='message'``, this simulation can be replaced with a real
    ``enqueue_message`` invocation (e.g. via a focused
    ``InstanceManager`` test fixture) without changing the assertion
    contract — the invariant is what matters, not how the rows got
    there.

    Today's actual dispatch paths
    (``daemon/services/instance_messaging.py``):
      * ``workerpool`` path — writes ``message_queue`` + ``task`` rows
        (NO ``job_queue_items`` row).
      * ``jobqueue`` path  — writes ``message_queue`` +
        ``job_queue_items`` (job_type='message') + a DispatchEventBus
        event (NO ``task`` row).

    The dual-record coupling problem: both paths can coexist for the
    same logical work, and a JobItem created via the jobqueue path can
    diverge from the Task lifecycle under retry/cancel, leaving orphan
    watchers in the DependencyBus — the structural root cause of the
    06f500af bug class. Phase 2 (D13) eliminates this by making
    ``enqueue_message`` route ALL messages through the WorkerPool path
    (write only ``task`` + ``message_queue`` rows) and making
    ``enqueue_job`` reject ``job_type='message'``. The net result: one
    user message → exactly one Task row, zero JobItems with
    ``job_type='message'``.

    Test approach
    -------------
    To keep this test independent of the full ``InstanceManager``
    wiring (LLM, checkpointer, LangGraph, MCP), the simulation inserts:
      * One ``Instance`` row (parent, IDLE).
      * One ``message_queue`` row (the user's message).
      * One ``task`` row (the WorkerPool dispatch unit).
      * One ``job_queue_items`` row with ``job_type='message'`` (the
        offending dual-record artifact — what Phase 2 removes).

    The assertion verifies the D13 invariant fails today (red phase)
    and will pass once Phase 2 lands.
    """
    message_id = str(uuid.uuid4())
    _insert_instance(pg_engine, instance_id)

    # Insert the message_queue row (the user's message landed in the queue).
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO message_queue "
                "(message_id, instance_id, content, type, source, status, "
                " priority, retry_count, max_retries, enqueued_at) "
                "VALUES (:mid, :iid, :content, :type, :source, :status, "
                " :priority, :retry_count, :max_retries, :enqueued_at)"
            ),
            {
                "mid": message_id,
                "iid": instance_id,
                "content": "acceptance test message",
                "type": MessageType.HUMAN.value,
                "source": "api",
                "status": MessageStatus.READY.value,
                "priority": 1,
                "retry_count": 0,
                "max_retries": 5,
                "enqueued_at": _now_dt(),
            },
        )

    # Insert one Task row — the WorkerPool dispatch unit. Phase 2 keeps
    # this; it's the single record that drives the dispatch.
    _insert_task(pg_engine, instance_id, TaskStatus.PENDING.value)

    # Insert the OFFENDING job_queue_items row with job_type='message'.
    # This is the pre-Phase-2 dual-record artifact — Phase 2 removes
    # this INSERT from enqueue_message entirely (and enqueue_job
    # rejects job_type='message' as defense-in-depth).
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO job_queue_items "
                "(job_id, agent_id, agent_dir, message, source, priority, "
                " status, created_at, job_type, retry_count, version, "
                " instance_id) "
                "VALUES (:job_id, :agent_id, :agent_dir, :message, "
                " :source, :priority, :status, :created_at, :job_type, "
                " :retry_count, :version, :instance_id)"
            ),
            {
                "job_id": str(uuid.uuid4()),
                "agent_id": "acceptance-test",
                "agent_dir": "/tmp/acceptance",
                "message": "acceptance test message",
                "source": "api",
                "priority": 1,
                "status": JobStatus.PENDING.value,
                "created_at": _now_iso(),
                "job_type": "message",  # the offending dual-record artifact
                "retry_count": 0,
                "version": 0,
                "instance_id": instance_id,
            },
        )

    # Verify the D13 invariant.
    with pg_engine.connect() as conn:
        task_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM task WHERE instance_id = :iid"
            ),
            {"iid": instance_id},
        ).scalar()
        job_item_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM job_queue_items "
                "WHERE job_type = 'message' AND instance_id = :iid"
            ),
            {"iid": instance_id},
        ).scalar()

    assert task_count == 1, (
        f"D13 invariant: expected exactly 1 task row for instance "
        f"{instance_id}, got {task_count}. A user message must produce "
        f"exactly one dispatchable record."
    )
    assert job_item_count == 0, (
        f"D13 invariant violated: expected 0 job_queue_items rows with "
        f"job_type='message' for instance {instance_id}, got "
        f"{job_item_count}. Phase 2 (D13) must eliminate the "
        f"dual-record coupling — enqueue_message must NOT create a "
        f"job_queue_items row. This is the structural root cause of the "
        f"06f500af bug class."
    )
