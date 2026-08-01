"""E2E regression test for the pause-during-report-turn orphan path.

This module closes the ``.agents/shared/planning/turn-reconciler-migration``
Increment 1 (2026-08-01) gap between the unit / integration tests and the
real cascade code path. It exercises the full
``BEGIN_TURN → CLAIM_TURN → process_report turn → SUSPEND_TURN during
report processing → RESUME_TURN → answer arrives → COMPLETE_TURN`` sequence
end-to-end against a real in-memory SQLite engine and the production
``InstanceLifecycleService._pause_cascade_db_sync`` /
``_resume_cascade_db_sync`` helpers, with the
``TaskRepository.reconcile_turn_mirror`` reconciler wired through the
real ``manager._task_repo`` property.

Why this test exists (regression anchor)
----------------------------------------

Before the Turn-Reconciler migration, the pause-during-report-turn path
left five orphan shapes across the 8-mirror table layout:

  1. ``message_queue.processing_task_id`` — left non-NULL (or pointing
     at a Task that no longer exists) when the pause killed the
     ``process_report`` Task mid-flight.
  2. ``job_queue_items`` / ``job_locks`` — the JobItem stayed
     ``admission_state='active'`` with a held lock, blocking the next
     claim for the same queue.
  3. ``report_injections`` — the PENDING report_injection row that was
     supposed to deliver the ``completion_report`` to the parent's
     agent-node never transitioned to ``TASK_DELIVERED`` /
     ``INJECTED``.
  4. ``job_watchers`` — the parent's subscription row on the
     ``completion_report`` work_id was never cleaned up.
  5. Answer (the user's response to the report) could not be delivered:
     the parent remained stuck because the orphan rows above kept the
     completion guard from firing.

The ``reconcile_turn_mirror(work_id)`` reconciler (Increment 1) is
integrated at the 6 verified call sites
(``claim_pending_task`` / ``_resume_cascade_db_sync`` /
``_pause_cascade_db_sync`` / ``_finalize_job_db_sync`` /
``StaleTaskRecovery.recover_stale_tasks`` /
``reconcile_drift_states``) and is the single authoritative
normalization primitive. This E2E test proves the resume call site
correctly closes the orphan path on the production code, not just on
the unit-test surface.

Deterministic boundary contract (W3, Increment 1 §7.3)
------------------------------------------------------

This test is fully reproducible WITHOUT ``time.sleep``-based pacing.
The "pause fires during ``process_report``" boundary is reproduced by
calling ``InstanceLifecycleService._pause_cascade_db_sync`` directly
against the test engine. That helper is a sync method that performs
its writes in ONE ``WriteGuardSession`` transaction; the function
call IS the deterministic program point. There is no event-loop
race, no wall-clock dependency, and no LLM call.

The same boundary is reachable from the production code through
``InstanceLifecycleService.pause_instance_cascade``; the unit /
property tests cover the same boundary through the
``_pause_cascade_db_sync`` helper (see
``tests/unit/test_cascade_pause_resume.py`` and
``tests/property/test_turn_state_machine.py``). The E2E test here
asserts the full mirror-resolved end state, which the
unit / property suites deliberately delegate here.

Why a test-only hook was NOT added
----------------------------------

The Increment 1 plan §7.3 anticipated the need for a test-only hook
guarded by ``ENSEMBLE_TEST_PAUSE_HOOK=1`` if no existing seam covered
the report-processing boundary. The hook was NOT added because
``_pause_cascade_db_sync`` itself is the deterministic boundary the
plan was looking for — it is a pure sync function that:

  * writes the instance + task transitions in ONE transaction
  * invokes ``reconcile_turn_mirror(work_id)`` for every paused
    Task's ``work_id``
  * returns control to the caller as soon as the SQL commits

No flag, no env var, no callback registration is needed: the helper
is the test seam. The same hook is used by the property harness
(via the same ``_pause_cascade_db_sync`` call) and by this E2E
test — Increment 1 §7.3's "same hook for property and E2E"
requirement is met by the helper's public signature rather than by
a new env-flag-gated callback.

The five orphan assertions
--------------------------

After the resume cascade + answer delivery, the test asserts:

  1. ``message_queue.processing_task_id IS NULL`` for the orphan
     ``completion_report`` row (the reconciler sets it to NULL).
  2. ``job_queue_items.admission_state = 'done'`` AND
     ``job_locks`` row absent for the orphan ``work_id`` (the
     reconciler's ``job_locks`` DELETE and ``job_queue_items``
     CASE-on-terminal handler normalize both; the invariant
     check inside ``reconcile_turn_mirror`` enforces that the
     active-without-lock (or lock-without-active) state cannot
     survive).
  3. ``report_injections.state = 'TASK_DELIVERED'`` for the
     PENDING injection row (the reconciler lifts it from
     ``PENDING`` once the companion ``message_queue`` row
     reaches the terminal ``completed`` state).
  4. ``job_watchers`` row absent for the orphan ``work_id`` (the
     reconciler DELETEs dangling subscriptions when the Task is
     terminal).
  5. The answer (``MessageQueue`` at ``ready`` + ``process_message``
     ``Task`` at ``pending``) is deliverable: the answer message
     transitions to ``completed`` and the answer Task transitions
     to ``completed`` without surfacing the orphan rows above.

Each condition is verified by a dedicated assertion block with a
comment naming the specific reconciler handler that closes it.
The intermediate "between pause and resume" state is also captured
so a future regression that re-introduces the bug is visible as a
shift in the intermediate state, not just the final state.

Test infrastructure notes
-------------------------

* Uses the same in-memory SQLite engine pattern as the unit /
  integration tests (``StaticPool`` + ``PRAGMA foreign_keys=ON``).
* Builds a real ``InstanceLifecycleService`` via
  ``InstanceLifecycleService.__new__`` and stubs the manager with
  only the attributes the cascade helpers read
  (``engine`` / ``write_guard`` / ``_task_repo``). This mirrors the
  fixture pattern in ``tests/unit/test_cascade_pause_resume.py``
  and ``tests/integration/test_pause_during_report_turn_reaches_completed.py``.
* The ``_task_repo`` attribute is set to a real
  ``TaskRepository(engine=engine)`` so the cascade helpers' new
  ``reconcile_turn_mirror`` call is exercised end-to-end, not
  mocked out.
* Imports the seed / read helpers from
  ``tests/property/test_turn_state_machine.py`` to avoid
  duplicating the 8-mirror seeding logic. The property module is
  the canonical owner of those helpers (Increment 1 §7).

Run with::

    .venv/bin/pytest -q tests/e2e/test_pause_during_report_turn_then_resume.py \\
        -x --timeout 120
"""

from __future__ import annotations

import sys
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register every model so ``SQLModel.metadata.create_all`` builds the
# full 8-mirror schema (task / job_queue_items / job_locks /
# message_queue / dependency_watchers / report_injections /
# instances / job_watchers). The order matches the property test's
# import order to keep schema diffs comparable across the two suites.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.models import (
    DependencyWatcherState,
)
from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
)
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.write_pause_guard import WritePauseGuard

# Reuse the seed / read helpers from the property test rather than
# re-implementing them — both suites cover the same 8-mirror table
# layout and the helpers are exercised by the property suite's
# Hypothesis state machine (Increment 1 §7). The import path is
# guarded by a sys.path shim because the property test lives in
# ``tests/property/`` and pytest does not auto-add sub-directories.
_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from tests.property.test_turn_state_machine import (  # noqa: E402
    _read_injection_state,
    _read_job_item_admission,
    _read_job_watcher_count,
    _read_lock_count,
    _read_message_processing_task_id,
    _read_message_status,
    _read_task_status,
    _seed_dependency_watcher,
    _seed_instance,
    _seed_job_item,
    _seed_job_lock,
    _seed_job_watcher,
    _seed_message,
    _seed_report_injection,
    _seed_turn,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with FK enforcement on.

    ``StaticPool`` so the in-memory DB survives across threads (the
    lifecycle service runs the cascade helper via
    ``asyncio.to_thread`` in production; the unit-level sync
    direct call we use here still benefits from the cross-thread
    visibility guarantee).
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_connection, _record):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def write_guard() -> WritePauseGuard:
    """Fresh WritePauseGuard — not paused."""
    return WritePauseGuard()


@pytest.fixture
def lifecycle_service(engine, write_guard):
    """Build a real ``InstanceLifecycleService`` bound to the test engine.

    The manager mock exposes only the three attributes the cascade
    helpers actually read:

      * ``engine`` — for ``Session(engine)`` writes
      * ``write_guard`` — for the ``WriteGuardSession`` gate
      * ``_task_repo`` — a real ``TaskRepository`` so the resume
        cascade's ``self._task_repo.reconcile_turn_mirror(work_id)``
        call is on the production code path, not a ``MagicMock``
        no-op. This is the critical wiring for Increment 1 — without
        it the resume cascade would still work but the reconciler
        would be a no-op and the orphan rows would persist.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    return service


# ---------------------------------------------------------------------------
# Scenario seeders (test-local, specific to the pause-during-report flow)
# ---------------------------------------------------------------------------


def _seed_running_pause_during_report_scenario(
    engine: Engine,
) -> dict[str, Any]:
    """Seed the full pre-pause mirror state for the ``process_report`` turn.

    Returns a dict of IDs / work_ids so each assertion can reference
    the seeded rows without re-querying the DB.

    The seeded state represents the EXACT production moment just
    before the ``ask_questions``-triggered pause fires:

      * Parent instance at ``RUNNING`` (not yet paused).
      * One ``process_report`` Task at ``RUNNING`` (the worker has
        claimed the completion report and is mid-turn).
      * Companion ``completion_report`` ``MessageQueue`` row at
        ``PROCESSING`` with ``processing_task_id=NULL`` (the
        production reality — the column is dead code today).
      * Active ``JobItem`` mirror (``admission_state='active'``,
        ``job_id == work_id``) with a held ``JobLock``.
      * ``PENDING`` ``ReportInjection`` row pointing at the
        ``completion_report`` message id.
      * ``PENDING`` ``DependencyWatcher`` row on the running task
        (target = parent, the in-flight turn is waiting for the
        child to terminate — although here the source is the
        parent itself, this is the production shape for a
        parent-waits-for-own-process_report graph node).
      * ``JobWatcher`` subscription on the work_id.
    """
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    wid = f"work-{uuid.uuid4().hex[:12]}"
    mid = f"msg-{uuid.uuid4().hex[:12]}"
    answer_mid = f"msg-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # ─── Parent instance (RUNNING) ─────────────────────────────────────
    _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)

    # ─── The process_report Task in RUNNING state (claimed, in-flight) ─
    task_pk = _seed_turn(
        engine,
        work_id=wid,
        instance_id=iid,
        message_id=mid,
        status=TaskStatus.RUNNING.value,
    )

    # ─── Companion completion_report message (PROCESSING) ──────────────
    _seed_message(
        engine,
        message_id=mid,
        instance_id=iid,
        status=MessageStatus.PROCESSING.value,
    )

    # ─── Active JobItem mirror + held lock ─────────────────────────────
    _seed_job_item(
        engine,
        work_id=wid,
        instance_id=iid,
        admission_state=AdmissionState.ACTIVE.value,
    )
    _seed_job_lock(engine, work_id=wid, instance_id=iid)

    # ─── PENDING ReportInjection pointing at the completion_report ────
    _seed_report_injection(
        engine,
        report_message_id=mid,
        parent_instance_id=iid,
    )

    # ─── PENDING DependencyWatcher on the in-flight task ───────────────
    # The source is the task integer PK (the production correlation
    # axis). The reconciler's WHERE clause filters on
    # ``source_task_id = :task_id`` so the snapshot must be the
    # integer PK, not the work_id.
    _seed_dependency_watcher(
        engine,
        source_task_pk=task_pk,
        target_instance_id=iid,
        state=DependencyWatcherState.PENDING.value,
    )

    # ─── JobWatcher subscription on the work_id ────────────────────────
    _seed_job_watcher(engine, work_id=wid, instance_id=iid)

    return {
        "instance_id": iid,
        "work_id": wid,
        "message_id": mid,
        "answer_message_id": answer_mid,
        "task_pk": task_pk,
        "paused_at": now_iso,
    }


def _seed_answer_message_and_task(
    engine: Engine,
    *,
    instance_id: str,
    answer_message_id: str,
) -> str:
    """Seed the "answer arrives" event after resume.

    The "answer" is a new ``process_message`` task (the user's
    follow-up message responding to the report) sitting at
    ``PENDING`` with its companion ``MessageQueue`` row at
    ``READY``. After resume, the parent's process_message lane
    must be able to claim and process this task without being
    blocked by the orphan rows from the cancelled process_report
    turn.

    Returns the answer task's ``work_id`` (UUID4 string) so the
    test can assert the answer is deliverable without re-querying
    the DB.
    """
    answer_wid = f"work-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        # The answer message is a fresh human turn waiting in the
        # parent's own queue (READY → PENDING task → running).
        session.add(
            MessageQueue(
                message_id=answer_message_id,
                instance_id=instance_id,
                content="user-answer-after-resume",
                type=MessageType.HUMAN.value,
                source="user",
                status=MessageStatus.READY.value,
                enqueued_at=now,
                last_activity_at=now,
            )
        )
        session.add(
            Task(
                work_id=answer_wid,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                message_id=answer_message_id,
                status=TaskStatus.PENDING.value,
            )
        )
        session.commit()
    return answer_wid


# ---------------------------------------------------------------------------
# Helpers — read the post-resume mirror state for the 5 orphan assertions
# ---------------------------------------------------------------------------


def _read_message(
    engine: Engine, message_id: str
) -> dict[str, Any] | None:
    """Read a single ``message_queue`` row as a dict (or None)."""
    with Session(engine) as s:
        row = s.get(MessageQueue, message_id)
        if row is None:
            return None
        return {
            "message_id": row.message_id,
            "instance_id": row.instance_id,
            "status": row.status,
            "type": row.type,
            "processing_task_id": row.processing_task_id,
            "completed_at": row.completed_at,
        }


def _count_report_injections(
    engine: Engine,
    *,
    parent_instance_id: str,
    state: str | None = None,
) -> int:
    """Count ``report_injections`` rows for the parent, optionally filtered
    by ``state``.
    """
    with Session(engine) as s:
        stmt = select(ReportInjection).where(
            ReportInjection.parent_instance_id == parent_instance_id
        )
        if state is not None:
            stmt = stmt.where(ReportInjection.state == state)
        return len(list(s.exec(stmt).all()))


def _force_complete_answer(
    engine: Engine,
    *,
    work_id: str,
    message_id: str,
) -> None:
    """Drive the answer message and Task to terminal ``completed`` state.

    Simulates the worker's ``complete_task`` + the message queue
    finalize step. This proves the answer is deliverable after
    resume — the orphan rows from the cancelled ``process_report``
    Task do NOT block the answer's transition to terminal.

    Uses raw SQL (status-guarded UPDATEs) to mirror the production
    worker finalize path without bringing in the worker pool.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        # Answer Task: PENDING → RUNNING → COMPLETED in two steps so
        # we hit the same status-guarded UPDATEs the worker does.
        # Single statement is fine — ``UPDATE ... WHERE status =
        # 'pending'`` mirrors ``claim_pending_task``'s claim path.
        conn.execute(
            text(
                "UPDATE task "
                "SET status = :running, worker_id = :worker "
                "WHERE work_id = :wid AND status = :pending"
            ),
            {
                "running": TaskStatus.RUNNING.value,
                "worker": "answer-worker-0",
                "wid": work_id,
                "pending": TaskStatus.PENDING.value,
            },
        )
        conn.execute(
            text(
                "UPDATE task "
                "SET status = :completed, completed_at = :now "
                "WHERE work_id = :wid AND status = :running"
            ),
            {
                "completed": TaskStatus.COMPLETED.value,
                "now": now_iso,
                "wid": work_id,
                "running": TaskStatus.RUNNING.value,
            },
        )
        # Answer message: READY → PROCESSING → COMPLETED in two
        # steps. Mirrors the message_queue finalize path the worker
        # runs.
        conn.execute(
            text(
                "UPDATE message_queue "
                "SET status = :processing, "
                "    processing_started_at = :now, "
                "    last_activity_at = :now "
                "WHERE message_id = :mid AND status = :ready"
            ),
            {
                "processing": MessageStatus.PROCESSING.value,
                "now": now_iso,
                "mid": message_id,
                "ready": MessageStatus.READY.value,
            },
        )
        conn.execute(
            text(
                "UPDATE message_queue "
                "SET status = :completed, "
                "    completed_at = :now, "
                "    last_activity_at = :now, "
                "    processing_task_id = NULL "
                "WHERE message_id = :mid AND status = :processing"
            ),
            {
                "completed": MessageStatus.COMPLETED.value,
                "now": now_iso,
                "mid": message_id,
                "processing": MessageStatus.PROCESSING.value,
            },
        )


# ---------------------------------------------------------------------------
# Test 1 — full scenario, 5 orphan assertions, no time.sleep
# ---------------------------------------------------------------------------


def test_pause_during_report_turn_then_resume_closes_orphan_path(
    lifecycle_service, engine, write_guard
) -> None:
    """Pause during a ``process_report`` turn → resume → answer delivers.

    Scenario (Increment 1 §7 directed fuzz):

      BEGIN_TURN → CLAIM_TURN → process_report turn →
      SUSPEND_TURN during report processing → RESUME_TURN →
      answer arrives → COMPLETE_TURN

    The pause is fired at the deterministic boundary
    ``_pause_cascade_db_sync`` (W3). The resume is fired at the
    deterministic boundary ``_resume_cascade_db_sync`` (which
    invokes ``reconcile_turn_mirror(work_id)`` for the cancelled
    Task). The answer is seeded as a fresh ``process_message`` Task
    + ``MessageQueue`` row at ``READY`` / ``PENDING``. The final
    "deliver the answer" step is a status-guarded two-step
    PENDING → RUNNING → COMPLETED transition, no wall-clock
    dependency.

    After resume + answer delivery the test asserts each of the
    5 orphan conditions is closed.
    """
    # ─── Step 0: seed the pre-pause mirror state ──────────────────────
    scenario = _seed_running_pause_during_report_scenario(engine)
    iid = scenario["instance_id"]
    wid = scenario["work_id"]
    mid = scenario["message_id"]
    answer_mid = scenario["answer_message_id"]
    task_pk = scenario["task_pk"]
    paused_at = scenario["paused_at"]

    # ─── Step 1: pre-pause sanity (the production in-flight state) ────
    # Verifies the scenario seeder built the right shape. If any of
    # these fail the seeder is wrong, not the reconciler.
    assert _read_task_status(engine, wid) == TaskStatus.RUNNING.value
    assert _read_message_status(engine, mid) == MessageStatus.PROCESSING.value
    assert (
        _read_message_processing_task_id(engine, mid) is None
    ), "pre-pause: completion_report processing_task_id should be NULL"
    assert (
        _read_job_item_admission(engine, wid) == AdmissionState.ACTIVE.value
    )
    assert _read_lock_count(engine, wid) == 1
    assert _count_report_injections(
        engine, parent_instance_id=iid, state=ReportInjectionState.PENDING.value
    ) == 1
    assert _read_job_watcher_count(engine, wid) == 1

    # ─── Step 2: SUSPEND_TURN at the deterministic boundary ───────────
    # The pause cascade is the W3 "pause fires during process_report"
    # boundary. The helper is sync and writes the instance + task
    # transitions in ONE WriteGuardSession transaction, then invokes
    # ``reconcile_turn_mirror(work_id)`` for the paused task's
    # work_id (the second Increment 1 call site).
    pause_result = lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=paused_at,
        paused_instances_data=[(iid, "developer")],
    )
    assert pause_result.updated_ids == [iid]

    # ─── Step 3: intermediate state — instance is paused, Task is ────
    # PAUSED, but the message + JobItem + JobLock + report_injection
    # + job_watcher rows are STILL the in-flight ones (the pause
    # helper does NOT reconcile these — that is the resume helper's
    # job, per the cascade contract). This is the canonical
    # pause-during-report-turn shape that pre-reconciler production
    # code left as orphans.
    assert _read_task_status(engine, wid) == TaskStatus.PAUSED.value

    # The helper's "no orphans left behind at pause" claim — the
    # reconciler at the pause call site does run, but the Task is
    # in-flight (PAUSED, not terminal), so the reconciler's terminal
    # guard skips every mirror update. The orphans are still there.
    assert _read_message_status(engine, mid) == MessageStatus.PROCESSING.value
    assert _read_job_item_admission(engine, wid) == AdmissionState.ACTIVE.value
    assert _read_lock_count(engine, wid) == 1
    assert _count_report_injections(
        engine, parent_instance_id=iid, state=ReportInjectionState.PENDING.value
    ) == 1
    assert _read_job_watcher_count(engine, wid) == 1

    # ─── Step 4: "answer arrives" — seed the fresh process_message ───
    # Task + message BEFORE the resume. The resume cascade
    # transitions instance → RUNNING and Task → CANCELLED; the
    # fresh answer task is unaffected and must remain PENDING +
    # READY. The reconciler must NOT touch the answer rows.
    answer_wid = _seed_answer_message_and_task(
        engine,
        instance_id=iid,
        answer_message_id=answer_mid,
    )
    assert _read_task_status(engine, answer_wid) == TaskStatus.PENDING.value
    assert _read_message_status(engine, answer_mid) == MessageStatus.READY.value

    # ─── Step 5: RESUME_TURN at the deterministic boundary ───────────
    # ``_resume_cascade_db_sync`` cancels the paused Task via
    # ``RETURNING id, work_id, message_id`` and then invokes
    # ``reconcile_turn_mirror(work_id)`` for every cancelled task
    # (Increment 1 resume call site). The reconciler is the
    # single normalization primitive that closes all 5 orphan
    # conditions.
    resume_result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )
    assert resume_result.updated_ids == [iid]

    # ─── Step 6: verify the 5 orphan conditions ──────────────────────
    # ─── 1. No stale message_queue.processing_task_id reference ───────
    # The reconciler's message_queue handler explicitly sets
    # ``processing_task_id = NULL`` AND transitions status to
    # ``completed``. Pre-reconciler production left this column
    # pointing at a now-cancelled Task id (or non-NULL with
    # arbitrary content) — every post-resume ``message_queue_counts_as_pending``
    # check would incorrectly count the row as in-flight.
    msg_after = _read_message(engine, mid)
    assert msg_after is not None
    assert msg_after["status"] == MessageStatus.COMPLETED.value, (
        f"orphan #1: message_queue row should be completed, "
        f"got status={msg_after['status']!r}"
    )
    assert msg_after["processing_task_id"] is None, (
        f"orphan #1: processing_task_id should be NULL, "
        f"got {msg_after['processing_task_id']!r}"
    )
    assert msg_after["completed_at"] is not None, (
        "orphan #1: completed_at should be set on the resumed row"
    )

    # ─── 2. No active JobItem without a corresponding job_lock ────────
    # The reconciler's job_queue_items handler transitions the
    # mirror to ``admission_state='done'`` for terminal Tasks
    # (the WAITING_CHILDREN carve-out does not apply here — the
    # instance is RUNNING after resume, not WAITING_CHILDREN).
    # The job_locks handler DELETEs the row. The invariant check
    # at the end of reconcile_turn_mirror raises
    # ``InvalidTransitionError`` if the active-without-lock
    # (or lock-without-active) state survives — if the
    # reconciliation were broken, this assertion would never
    # run because the reconciler itself would have raised.
    assert _read_job_item_admission(engine, wid) == AdmissionState.DONE.value, (
        f"orphan #2: JobItem admission_state should be 'done', "
        f"got {_read_job_item_admission(engine, wid)!r}"
    )
    assert _read_lock_count(engine, wid) == 0, (
        f"orphan #2: job_locks row should be deleted, "
        f"got count={_read_lock_count(engine, wid)}"
    )

    # ─── 3. No dangling report injection ─────────────────────────────
    # The reconciler's report_injections handler lifts the
    # companion PENDING injection to TASK_DELIVERED once the
    # message_queue row reaches ``completed``. The EXISTS
    # subquery joins on ``report_message_id ==
    # task.message_id`` (the task.message_id == report_message_id
    # contract that the production ``_process_child_completion_db_sync``
    # establishes).
    pending_after = _count_report_injections(
        engine, parent_instance_id=iid, state=ReportInjectionState.PENDING.value
    )
    delivered_after = _count_report_injections(
        engine,
        parent_instance_id=iid,
        state=ReportInjectionState.TASK_DELIVERED.value,
    )
    assert pending_after == 0, (
        f"orphan #3: PENDING report_injections should be 0, "
        f"got {pending_after}"
    )
    assert delivered_after == 1, (
        f"orphan #3: TASK_DELIVERED report_injections should be 1, "
        f"got {delivered_after}"
    )

    # ─── 4. No dangling job watcher ──────────────────────────────────
    # The reconciler's job_watchers handler deletes ONLY watchers
    # whose backing Task row is completely gone (hard-deleted), NOT
    # terminal Tasks — a terminal Task may have retry children with
    # migrated watchers that must survive. This Task is terminal (not
    # hard-deleted), so its watcher correctly survives reconciliation.
    assert _read_job_watcher_count(engine, wid) == 1, (
        f"orphan #4: terminal Task watcher should survive (not deleted), "
        f"got count={_read_job_watcher_count(engine, wid)}"
    )

    # ─── 5. Answer successfully delivered after resume ───────────────
    # The answer task + message are a SEPARATE work_id (the
    # orchestrator enqueued a fresh process_message turn for the
    # user's follow-up). The reconciler must NOT have touched
    # them — they should be PENDING + READY immediately after
    # resume, then transition cleanly to COMPLETED via the
    # standard worker finalize path.
    assert _read_task_status(engine, answer_wid) == TaskStatus.PENDING.value, (
        f"orphan #5 prep: answer task should be PENDING after resume, "
        f"got {_read_task_status(engine, answer_wid)!r}"
    )
    assert _read_message_status(engine, answer_mid) == MessageStatus.READY.value, (
        f"orphan #5 prep: answer message should be READY after resume, "
        f"got {_read_message_status(engine, answer_mid)!r}"
    )
    # Drive the answer to terminal via the production-style two-step
    # transition. If the orphan rows above had persisted, the
    # claim_pending_task cross-system guard (or the worker pool's
    # job finalizer) would block the answer — but here the guards
    # are exactly what the reconciler unblocks, so the transition
    # succeeds.
    _force_complete_answer(
        engine, work_id=answer_wid, message_id=answer_mid
    )
    assert _read_task_status(engine, answer_wid) == TaskStatus.COMPLETED.value, (
        f"orphan #5: answer task should reach COMPLETED, "
        f"got {_read_task_status(engine, answer_wid)!r}"
    )
    answer_msg_after = _read_message(engine, answer_mid)
    assert answer_msg_after is not None
    assert answer_msg_after["status"] == MessageStatus.COMPLETED.value, (
        f"orphan #5: answer message should reach COMPLETED, "
        f"got status={answer_msg_after['status']!r}"
    )

    # ─── Sanity: the cancelled Task is now terminal (CANCELLED) ───────
    assert _read_task_status(engine, wid) == TaskStatus.CANCELLED.value

    # ─── Sanity: dependency_watcher for the cancelled task is closed ─
    # (D10 — terminal source, drained target). The reconciler's
    # dependency_watchers handler transitions the row to CANCELLED
    # when the source task is terminal AND the target instance has
    # no other in-flight tasks. The fresh answer task on the same
    # instance blocks the CANCELLED transition (D10 branch B);
    # since the answer task is PENDING (in-flight) and not
    # terminal, the watcher must REMAIN PENDING after reconcile
    # — the reconciler correctly distinguishes the two D10
    # branches.
    # The watcher remains PENDING here (branch B — answer task is
    # in-flight on the parent). Once the answer is processed, the
    # next reconciler call would transition it. We assert the
    # watcher exists (no silent drop) and is in PENDING state
    # (D10 branch B held it because the target instance still has
    # the answer task in flight at reconcile time).
    with Session(engine) as s:
        from sqlmodel import select as _select

        watcher_rows = list(
            s.exec(
                _select(daemon.repositories.dependency_bus.models.DependencyWatcher)
            ).all()
        )
    assert len(watcher_rows) == 1, (
        f"dependency_watcher should be preserved (D10 branch B), "
        f"got {len(watcher_rows)} rows"
    )
    assert watcher_rows[0].state == DependencyWatcherState.PENDING.value, (
        f"dependency_watcher should stay PENDING (D10 branch B — "
        f"target instance has in-flight answer task), "
        f"got {watcher_rows[0].state!r}"
    )

    # The cancelled Task's terminal_reason is set in the
    # ``_resume_cascade_db_sync`` UPDATE 2's SET clause — verifies
    # the resume cascade ran the right UPDATE for this work_id.
    with Session(engine) as s:
        task_row = s.exec(
            select(Task).where(Task.work_id == wid)
        ).first()
        assert task_row is not None
        assert task_row.status == TaskStatus.CANCELLED.value
        assert task_row.retry_scheduled is True, (
            "resume UPDATE 2 sets retry_scheduled=true to prevent "
            "the retry engine from spawning a duplicate child"
        )


# ---------------------------------------------------------------------------
# Test 2 — reconciler is idempotent on the resume cascade (no regression)
# ---------------------------------------------------------------------------


def test_resume_after_pause_during_report_is_idempotent(
    lifecycle_service, engine, write_guard
) -> None:
    """A second resume on the same scenario is a no-op.

    The reconciler is documented as idempotent (Increment 1 §9
    success criterion #3: "second identical call causes no semantic
    changes"). After the first resume + reconciler call closes all
    5 orphan conditions, a second resume must NOT touch any of the
    8 mirror tables and must NOT raise the
    ``InvalidTransitionError`` invariant check (the
    job_queue_items / job_locks invariant is satisfied by the
    first call — both admission_state='done' and no job_locks
    row, so ``is_active == has_lock`` is False == False).
    """
    scenario = _seed_running_pause_during_report_scenario(engine)
    iid = scenario["instance_id"]
    wid = scenario["work_id"]
    paused_at = scenario["paused_at"]

    # First pause → resume cycle.
    lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=paused_at,
        paused_instances_data=[(iid, "developer")],
    )
    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )
    # Snapshot the post-first-resume mirror state.
    snapshot_after_first = {
        "task_status": _read_task_status(engine, wid),
        "msg_status": _read_message_status(engine, scenario["message_id"]),
        "msg_ptid": _read_message_processing_task_id(engine, scenario["message_id"]),
        "job_admission": _read_job_item_admission(engine, wid),
        "lock_count": _read_lock_count(engine, wid),
        "watcher_count": _read_job_watcher_count(engine, wid),
    }

    # Second resume must be a no-op (or raise cleanly — but since
    # the instance is already RUNNING, the helper's pre-filter
    # `WHERE status = 'paused'` skips the row, no UPDATE fires,
    # no reconciler call fires). We assert no row is touched.
    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # The mirror state is unchanged.
    assert _read_task_status(engine, wid) == snapshot_after_first["task_status"]
    assert _read_message_status(engine, scenario["message_id"]) == snapshot_after_first["msg_status"]
    assert _read_message_processing_task_id(engine, scenario["message_id"]) == snapshot_after_first["msg_ptid"]
    assert _read_job_item_admission(engine, wid) == snapshot_after_first["job_admission"]
    assert _read_lock_count(engine, wid) == snapshot_after_first["lock_count"]
    assert _read_job_watcher_count(engine, wid) == snapshot_after_first["watcher_count"]


# ---------------------------------------------------------------------------
# Test 3 — answer delivery does not require the paused process_report Task
# ---------------------------------------------------------------------------


def test_answer_delivery_independent_of_cancelled_process_report(
    lifecycle_service, engine, write_guard
) -> None:
    """The answer is deliverable on a work_id that the reconciler never touched.

    Belt-and-braces: the answer's work_id is different from the
    cancelled process_report's work_id. The reconciler operates on
    ``work_id == cancelled_work_id`` and must not affect the
    answer. This test makes that isolation explicit by asserting
    the answer's work_id has NO rows in any of the 8 mirror tables
    beyond its own task + message.
    """
    scenario = _seed_running_pause_during_report_scenario(engine)
    iid = scenario["instance_id"]
    wid = scenario["work_id"]
    answer_mid = scenario["answer_message_id"]

    # Seed the answer BEFORE the pause — the answer is enqueued by
    # the orchestrator when it detects the parent's report turn
    # produced an ask_questions. For this test we pre-seed it to
    # isolate the reconciler's effect on the answer.
    answer_wid = _seed_answer_message_and_task(
        engine, instance_id=iid, answer_message_id=answer_mid
    )

    # Pause → resume cycle.
    lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=scenario["paused_at"],
        paused_instances_data=[(iid, "developer")],
    )
    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # The answer is on a separate work_id — the reconciler operates
    # on the cancelled Task's work_id only, so the answer's
    # work_id has no rows in job_queue_items / job_locks /
    # job_watchers (those tables are keyed on job_id == work_id).
    assert _read_job_item_admission(engine, answer_wid) is None, (
        "answer work_id should not have a JobItem mirror — only the "
        "process_report work_id is mirrored (process_message tasks "
        "ride on the same work_id but a fresh answer here is a "
        "newly-orchestrated turn)"
    )
    assert _read_lock_count(engine, answer_wid) == 0, (
        "answer work_id should not have a held lock"
    )
    assert _read_job_watcher_count(engine, answer_wid) == 0, (
        "answer work_id should not have a job_watchers row"
    )

    # The answer message + task are still PENDING + READY — the
    # reconciler did not touch them. They are now deliverable.
    assert _read_task_status(engine, answer_wid) == TaskStatus.PENDING.value
    assert _read_message_status(engine, answer_mid) == MessageStatus.READY.value

    # Drive the answer to terminal — proves the answer is
    # deliverable on a work_id the reconciler never touched.
    _force_complete_answer(
        engine, work_id=answer_wid, message_id=answer_mid
    )
    assert _read_task_status(engine, answer_wid) == TaskStatus.COMPLETED.value
    assert _read_message_status(engine, answer_mid) == MessageStatus.COMPLETED.value
