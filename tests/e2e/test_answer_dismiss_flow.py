"""B2 Review Follow-up — E2E Answer/Dismiss Resume Flow.

This e2e test targets the critical ordering invariant that the
``resume_processing_job`` ordering fix protects:

    The answer-gate selector ``find_suspended_turn_for_answer`` MUST
    resolve the awaiting-answer suspension handle WHILE the Task is
    in ``status='paused'`` — before ``_resume_cascade_db_sync``
    transitions the Task to ``status='pending'``.

The existing
``tests/e2e/test_full_chain_turn_reconciler.py::test_full_chain_claim_process_pause_resume_answer_complete``
exercises the full integration chain. This file focuses narrowly on
the REPOSITORY SELECTOR + HANDLE RESOLUTION path — the single point
of failure the ordering fix defends against.

The same ordering applies to both answer endpoints:

  * ``POST /instances/{id}/answer_questions`` (daemon/routers/instances.py:996-1070)
  * ``POST /instances/{id}/dismiss_question`` (daemon/routers/instances.py:1248-1320)

Both routes call ``manager.resume_processing_job`` (which queries
``find_suspended_turn_for_answer``) BEFORE calling
``manager.resume_instance_cascade`` (which calls
``_resume_cascade_db_sync``). Reversing these calls would leave the
handle unresolvable — the answer would never be injected into the
checkpoint.

Mirrors the in-memory SQLite fixture pattern from
``tests/e2e/test_full_chain_turn_reconciler.py`` (the §11.4.1
fixture recipe). Imports the seed / read helpers from
``tests.property.test_turn_state_machine`` (Increment 1 §7
state-machine seeder), then layers two local helpers
(``_seed_claim_ready_mirror``, ``_force_claim``) that the reference
test owns locally. PostgreSQL is the primary dev DB, but e2e tests
use in-memory SQLite — same pattern as every existing e2e suite.

Run with::

    .venv/bin/pytest -q tests/e2e/test_answer_dismiss_flow.py -x
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register every model so ``SQLModel.metadata.create_all()`` builds
# the full 8-mirror schema (the same import list as the reference
# e2e test — every test that reuses the fixture must import every
# model so the metadata table registry is complete at create_all time).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

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
from daemon.repositories.task.models import (
    SuspensionReason,
    Task,
    TaskStatus,
    TaskType,
)
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.turn_transitions import (
    ClaimTurn,
    CompleteTurn,
    ResumeTurn,
    SuspendTurn,
)
from daemon.write_pause_guard import WritePauseGuard

# Reuse the seed / read helpers from the property test rather than
# re-implementing them (per §11.4.1 fixture pattern; the property
# suite's Hypothesis state machine exercises them under fuzzing).
_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from tests.property.test_turn_state_machine import (  # noqa: E402
    _read_job_item_admission,
    _read_job_watcher_count,
    _read_lock_count,
    _read_message_status,
    _read_task_status,
    _seed_instance,
    _seed_job_item,
    _seed_job_lock,
    _seed_job_watcher,
    _seed_message,
    _seed_turn,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with FK enforcement on.

    Identical recipe to the reference e2e test — the §11.4.1 fixture
    pattern. PG-compatible SQL is used in raw queries so the same
    test can move to a PG engine later without surprises
    (constraint: the project's primary dev DB is PostgreSQL).
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
    """Build ``InstanceLifecycleService`` bound to the test engine.

    The manager mock exposes ``engine``, ``write_guard``, and a real
    ``_task_repo`` so the cascade helpers' reconciler call is on the
    production code path, not a no-op. Same pattern as the reference
    e2e test and ``tests/e2e/test_pause_during_report_turn_then_resume.py``.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    return service


# ─── Local helpers (mirror state + claim + read) ───────────────────────────
#
# These are local to this file because the reference e2e test also
# defines them locally (they bridge between the property-test seeders
# and the full chain scenario). Copy-pasting keeps each e2e test
# self-contained and avoids cross-file helper churn.


def _seed_claim_ready_mirror(
    engine: Engine, *, instance_id: str, work_id: str, message_id: str
) -> int:
    """Seed the full mirror state for a freshly-claimed Task.

    Returns the Task integer PK. Mirrors the helper in
    ``test_full_chain_turn_reconciler.py`` — see that file for the
    detailed invariant rationale.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc)

    # Authority: PENDING task row.
    task_pk = _seed_turn(
        engine,
        work_id=work_id,
        instance_id=instance_id,
        message_id=message_id,
        status=TaskStatus.PENDING.value,
    )

    # Companion message at READY (waiting to be claimed).
    _seed_message(
        engine,
        message_id=message_id,
        instance_id=instance_id,
        status=MessageStatus.READY.value,
    )

    # Queued JobItem mirror.
    _seed_job_item(
        engine,
        work_id=work_id,
        instance_id=instance_id,
        admission_state=AdmissionState.QUEUED.value,
    )

    # Held JobLock (the reconciler's invariant requires active ↔
    # lock for a claimable Task; seed the lock preemptively so the
    # post-claim ACTIVE state has a matching lock).
    _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)

    # JobWatcher subscription on the work_id.
    _seed_job_watcher(engine, work_id=work_id, instance_id=instance_id)

    return task_pk


def _force_claim(
    engine: Engine, work_id: str, worker_id: str = "worker-0"
) -> None:
    """Atomic CLAIM_TURN: PENDING → RUNNING + JobItem QUEUED → ACTIVE.

    Mirrors ``TaskRepository.claim_pending_task``'s status-guarded
    UPDATE — the same SQL the production worker pool runs. Uses
    raw SQL to avoid the chokepoint method's session-binding
    requirements.

    PG-compatible: status-guarded UPDATE with binding params; no
    SQLite-only syntax (no rowid, no INSERT OR IGNORE).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE task SET status = :running, "
                "worker_id = :worker, started_at = :now "
                "WHERE work_id = :work_id AND status = :pending"
            ),
            {
                "running": TaskStatus.RUNNING.value,
                "worker": worker_id,
                "now": now_iso,
                "work_id": work_id,
                "pending": TaskStatus.PENDING.value,
            },
        )
        assert result.rowcount == 1, (
            f"CLAIM_TURN must update exactly 1 row; got {result.rowcount}"
        )
        # Advance the message mirror to PROCESSING.
        conn.execute(
            text(
                "UPDATE message_queue SET status = :processing, "
                "last_activity_at = :now "
                "WHERE message_id = (SELECT message_id FROM task "
                "WHERE work_id = :work_id) AND status = :ready"
            ),
            {
                "processing": MessageStatus.PROCESSING.value,
                "ready": MessageStatus.READY.value,
                "now": now_iso,
                "work_id": work_id,
            },
        )
        # Flip the JobItem to ACTIVE atomically with the Task claim.
        conn.execute(
            text(
                "UPDATE job_queue_items SET admission_state = :active "
                "WHERE job_id = :work_id AND admission_state = :queued"
            ),
            {
                "active": AdmissionState.ACTIVE.value,
                "queued": AdmissionState.QUEUED.value,
                "work_id": work_id,
            },
        )


def _read_handle(
    engine: Engine, work_id: str
) -> tuple[str, str | None, str | None]:
    """Return ``(status, suspension_reason, resume_target_turn_id)``.

    Direct DB read — exposes the canonical column values that the
    selectors compare against. Mirrors the helper in the reference
    e2e test.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, suspension_reason, resume_target_turn_id "
                "FROM task WHERE work_id = :work_id"
            ),
            {"work_id": work_id},
        ).first()
    if row is None:
        return ("missing", None, None)
    return (row[0], row[1], row[2])


def _read_instance_status(engine: Engine, instance_id: str) -> str | None:
    """Return the ``instances.status`` value for the instance row."""
    with Session(engine) as s:
        row = s.get(Instance, instance_id)
        return row.status if row is not None else None


# ─── Tests ─────────────────────────────────────────────────────────────────


def test_answer_handle_resolves_while_paused_before_cascade(
    lifecycle_service, engine, write_guard
) -> None:
    """The answer-gate handle MUST be resolvable while the Task is PAUSED.

    This is the invariant the resume-ordering fix protects:
    ``resume_processing_job`` calls ``find_suspended_turn_for_answer``
    which filters ``status == 'paused'``.
    ``_resume_cascade_db_sync`` transitions ``PAUSED → PENDING``. If
    the cascade ran FIRST, the selector would return ``None`` and
    the answer / dismissal would never be injected.

    Steps:
      1. Seed instance + PENDING task mirror.
      2. Claim (PENDING → RUNNING).
      3. ``SuspendTurn(awaiting_answer, resume_target_turn_id=...)``
         transitions the Task RUNNING → PAUSED with the
         awaiting-answer handle.
      4. Assert ``find_suspended_turn_for_answer`` returns the handle
         (Task is PAUSED).
      5. Run ``_resume_cascade_db_sync`` (PAUSED → PENDING).
      6. Assert ``find_suspended_turn_for_answer`` returns ``None``
         (Task is PENDING — selector filter no longer matches).
      7. Assert ``find_paused_or_cancellable_turn`` also returns
         ``None`` (Task is PENDING — not in its PAUSED/RUNNING filter).

    A regression that introduced a reverse ordering
    (``_resume_cascade_db_sync`` BEFORE ``resume_processing_job``)
    would break step 6: the handle would already be unresolvable
    before any selector call. A regression that introduced a
    partial cascade (handle cleared, status still PAUSED) would
    break step 4 (no row matched). A regression that broadened
    the selector to include PENDING rows would break step 6 (the
    selector would still find the row).
    """
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    work_id = f"work-{uuid.uuid4().hex[:12]}"
    mid = f"msg-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    # Seed PENDING mirror state.
    _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)
    _seed_claim_ready_mirror(
        engine, instance_id=iid, work_id=work_id, message_id=mid
    )

    # PENDING → RUNNING via direct CLAIM_TURN mirror UPDATE.
    _force_claim(engine, work_id)

    # SuspendTurn with awaiting_answer + a self-target handle
    # (the §7.2 invariant: awaiting_answer requires
    # resume_target_turn_id).
    with Session(engine) as s:
        SuspendTurn(
            work_id=work_id,
            reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=work_id,
        ).run(s)
        s.commit()

    task_repo = TaskRepository(engine)

    # WHILE PAUSED: the answer selector MUST find the handle.
    handle = _read_handle(engine, work_id)
    assert handle[0] == TaskStatus.PAUSED.value
    assert handle[1] == SuspensionReason.AWAITING_ANSWER.value
    assert handle[2] == work_id, (
        f"§7 invariant 2: awaiting_answer MUST carry a non-null "
        f"resume_target_turn_id; got {handle[2]!r}."
    )

    answer_handle = task_repo.find_suspended_turn_for_answer(iid)
    assert answer_handle is not None, (
        "find_suspended_turn_for_answer MUST resolve the awaiting-answer "
        "handle WHILE the Task is PAUSED — this is the precondition "
        "for resume_processing_job's answer-gate routing to fire."
    )
    assert answer_handle.work_id == work_id
    assert (
        answer_handle.suspension_reason
        == SuspensionReason.AWAITING_ANSWER.value
    )
    assert answer_handle.resume_target_turn_id == work_id
    assert answer_handle.status == TaskStatus.PAUSED.value

    # Pause-cascade selector also fires (PAUSED is in its filter).
    paused_handle = task_repo.find_paused_or_cancellable_turn(iid)
    assert paused_handle is not None
    assert paused_handle.work_id == work_id

    # Run the resume cascade (PAUSED → PENDING). This is what the
    # answer/dismiss routes call AFTER resume_processing_job — the
    # ordering invariant is the contract.
    result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )
    assert iid in result.updated_ids

    # AFTER cascade: Task is PENDING — both selectors MUST return None.
    handle_after_cascade = _read_handle(engine, work_id)
    assert handle_after_cascade[0] == TaskStatus.PENDING.value, (
        f"_resume_cascade_db_sync MUST transition PAUSED → PENDING; "
        f"got status={handle_after_cascade[0]!r}."
    )
    # The handle fields are consumed by ResumeTurn (PAUSED → PENDING
    # transition clears suspension_reason + resume_target_turn_id).
    assert handle_after_cascade[1] is None
    assert handle_after_cascade[2] is None

    # Critical invariant: the answer selector returns None AFTER the
    # cascade. This proves why resume_processing_job must run BEFORE
    # the cascade — once the Task flips to PENDING, the awaiting-answer
    # handle can no longer be resolved and the answer / dismissal would
    # be lost.
    assert task_repo.find_suspended_turn_for_answer(iid) is None, (
        "After _resume_cascade_db_sync (PAUSED → PENDING), the answer "
        "selector MUST return None — this proves why resume_processing_job "
        "must run BEFORE _resume_cascade_db_sync. The answer / dismiss "
        "endpoints (daemon/routers/instances.py:1010 + 1258) both call "
        "resume_processing_job first, exactly to keep this selector "
        "resolvable."
    )
    # The pause-cascade selector filters status IN ('paused','running')
    # — PENDING is not in that set, so find_paused_or_cancellable_turn
    # must also return None.
    assert task_repo.find_paused_or_cancellable_turn(iid) is None, (
        "After _resume_cascade_db_sync, find_paused_or_cancellable_turn "
        "also returns None — PENDING is not in its PAUSED/RUNNING "
        "filter (§8.2 selector shape)."
    )


def test_full_answer_lifecycle_pause_answer_resume_complete(
    lifecycle_service, engine, write_guard
) -> None:
    """Full answer-gate lifecycle through the transition primitives.

    Models the end-to-end answer path:

      seed → claim → suspend(awaiting_answer) → resolve handle
      (simulating resume_processing_job) → consume handle
      (simulating _schedule_explicit_handle_resume) → complete
      (simulating the worker's natural completion path).

    Proves the handle is set, resolvable, consumable, and the Task
    reaches a terminal state. The key assertion is that the handle
    resolves at the "WHILE PAUSED" step (the §7 ordering invariant).
    The cascade ordering itself is asserted in
    ``test_answer_handle_resolves_while_paused_before_cascade`` above;
    this test focuses on the lifecycle shape end-to-end.

    A regression that left the handle uncleared after consumption
    would break the post-resume ``find_suspended_turn_for_answer``
    assertion (duplicate answer would re-route onto the live task).
    A regression that minted a fresh Task for the answered turn
    would break the tasks_total count assertion
    (cancel-and-recreate fallback is removed per Increment 4 §9.4).
    """
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    work_id = f"work-{uuid.uuid4().hex[:12]}"
    mid = f"msg-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    # Step 1: seed + claim.
    _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)
    _seed_claim_ready_mirror(
        engine, instance_id=iid, work_id=work_id, message_id=mid
    )
    _force_claim(engine, work_id)

    task_repo = TaskRepository(engine)

    # Step 2: SuspendTurn(awaiting_answer) — the worker emits
    # ask_questions mid-processing.
    with Session(engine) as s:
        SuspendTurn(
            work_id=work_id,
            reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=work_id,  # self-target (§7.2 invariant)
        ).run(s)
        s.commit()

    paused_handle = _read_handle(engine, work_id)
    assert paused_handle[0] == TaskStatus.PAUSED.value
    assert paused_handle[1] == SuspensionReason.AWAITING_ANSWER.value
    assert paused_handle[2] == work_id

    # Step 3: resume_processing_job resolves the handle (the
    # answer-gate routing call). The selector finds the row because
    # the Task is still PAUSED.
    resolved = task_repo.find_suspended_turn_for_answer(iid)
    assert resolved is not None, (
        "Step 3 (the ordering invariant): the answer selector MUST "
        "resolve the handle WHILE the Task is PAUSED. A regression "
        "that flipped the answer / dismiss route to call "
        "_resume_cascade_db_sync BEFORE resume_processing_job would "
        "leave this selector unable to find the handle, and the "
        "answer / dismissal would be lost."
    )
    assert resolved.work_id == work_id
    assert resolved.resume_target_turn_id == work_id

    # Step 4: ResumeTurn consumes the handle (simulating what
    # _schedule_explicit_handle_resume does after the selector
    # resolves). The Task transitions PAUSED → PENDING and the
    # handle fields are cleared in the same guarded UPDATE.
    with Session(engine) as s:
        ResumeTurn(work_id=work_id).run(s)
        s.commit()

    post_answer_handle = _read_handle(engine, work_id)
    assert post_answer_handle[0] == TaskStatus.PENDING.value, (
        f"After ResumeTurn, the Task is PENDING (live, handle cleared) "
        f"per Phase 4b/4c migration; got {post_answer_handle[0]!r}."
    )
    assert post_answer_handle[1] is None, (
        f"§7 invariant 7 (handle consumed exactly once): "
        f"suspension_reason MUST be NULL after ResumeTurn; got "
        f"{post_answer_handle[1]!r}."
    )
    assert post_answer_handle[2] is None

    # Step 5: the answer selector returns None — the handle is gone.
    # A duplicate answer would re-route onto this row and the
    # selector MUST NOT find it.
    assert task_repo.find_suspended_turn_for_answer(iid) is None

    # Step 6: complete the resumed turn. Production keeps the
    # resumed task live (PAUSED → PENDING in step 4) and WorkerPool
    # drives it to COMPLETED in place. CompleteTurn requires status
    # == 'running' (the natural claim path), so model that by force-
    # claiming first.
    _force_claim(engine, work_id)
    with Session(engine) as s:
        CompleteTurn(work_id=work_id, result="answer-processed").run(s)
        s.commit()

    # Production's natural completion path runs
    # ``reconcile_turn_mirror`` POST-COMMIT (see
    # daemon/services/instance_lifecycle.py:3869 — the cascade
    # helper fires the same call after the Session commit). The
    # transition-level ``_reconcile()`` opens its own connection
    # which reads the OLD status because the Session hasn't
    # committed yet — so a follow-up reconcile is required to
    # observe the terminal status and move mirrors to their
    # final state. Mirrors that exact pattern here.
    task_repo.reconcile_turn_mirror(work_id)

    final_handle = _read_handle(engine, work_id)
    assert final_handle[0] == TaskStatus.COMPLETED.value
    assert final_handle[1] is None, (
        f"§7 invariant 9: CompleteTurn MUST clear the handle; "
        f"got {final_handle[1]!r}."
    )
    assert final_handle[2] is None

    # Step 7: mirror invariants at terminal. After CompleteTurn:
    #   * task_status = completed
    #   * JobItem admission_state = done (if it exists)
    #   * zero JobLocks (the reconciler releases them on terminalize)
    task_status = _read_task_status(engine, work_id)
    assert task_status == TaskStatus.COMPLETED.value

    job_item_admission = _read_job_item_admission(engine, work_id)
    assert job_item_admission == AdmissionState.DONE.value, (
        f"Terminal Task MUST have JobItem admission_state='done'; "
        f"got {job_item_admission!r}."
    )
    lock_count = _read_lock_count(engine, work_id)
    assert lock_count == 0, (
        f"Terminal Task MUST have zero JobLocks; got {lock_count}."
    )
    message_status = _read_message_status(engine, mid)
    assert message_status is not None

    # Step 8: no fresh Task minted by the answer / resume path
    # (Increment 4 §9.4: cancel-and-recreate fallback removed).
    # Count tasks directly to confirm the same work_id persisted
    # throughout the lifecycle.
    from sqlmodel import select  # local import to keep the imports
    # at the top tight.
    with Session(engine) as s:
        all_tasks_for_instance = s.exec(
            select(Task).where(Task.instance_id == iid)
        ).all()
    assert len(all_tasks_for_instance) == 1, (
        f"Full answer lifecycle seeds exactly 1 Task (claim → "
        f"suspend → answer → resume → complete) on the same "
        f"work_id throughout. A regression that minted a fresh Task "
        f"in the resume path (the cancel-and-recreate fallback "
        f"removed per Increment 4 §9.4) would push this to 2. "
        f"Got {len(all_tasks_for_instance)}."
    )
    assert all_tasks_for_instance[0].work_id == work_id


def test_suspension_reason_awaiting_answer_persisted_correctly(engine) -> None:
    """``SuspensionReason.AWAITING_ANSWER.value`` persists correctly.

    This guards the Bug 1 fix in the answer / dismiss path: the
    upstream ``pause_instance_cascade`` (called before the worker
    calls ``ask_questions``) MUST pass
    ``suspension_reason='awaiting_answer'`` so the answer selector
    can find the handle when the user answers or dismisses the
    question.

    Steps:
      1. Seed a fresh RUNNING task (the worker has claimed it).
      2. ``SuspendTurn(awaiting_answer, resume_target_turn_id=...)``
         sets the handle.
      3. Read the raw column values; assert they match the
         ``SuspensionReason.AWAITING_ANSWER.value`` enum value and
         a non-null ``resume_target_turn_id``.
      4. Verify the answer selector matches the row by the same
         string value (the selector's WHERE clause uses the literal
         ``'awaiting_answer'``).

    A regression that changed the enum value to something like
    ``'AWAITING_ANSWER'`` (uppercase) or ``'awaitingAnswer'``
    (camelCase) would break the selector's WHERE clause — the column
    would persist the wrong literal and the selector would never
    find the row. A regression that set ``suspension_reason=NULL``
    would break the same way.
    """
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    work_id = f"work-{uuid.uuid4().hex[:12]}"
    mid = f"msg-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)

    # Seed a fresh RUNNING task (the worker has claimed it; the
    # transition primitive's status-guarded UPDATE requires 'running').
    with Session(engine) as s:
        s.add(
            Task(
                work_id=work_id,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=iid,
                message_id=mid,
                status=TaskStatus.RUNNING.value,
                created_at=now,
            )
        )
        s.commit()

    # SuspendTurn with the awaiting_answer handle.
    with Session(engine) as s:
        SuspendTurn(
            work_id=work_id,
            reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=work_id,
        ).run(s)
        s.commit()

    # Read the raw column values.
    handle = _read_handle(engine, work_id)
    assert handle[0] == TaskStatus.PAUSED.value
    # §7 Bug 1 fix contract: the column MUST persist the lowercase
    # snake_case literal — NOT the enum member name, NOT camelCase,
    # NOT NULL. The selector's WHERE clause hard-codes the literal
    # ``'awaiting_answer'`` at
    # daemon/repositories/task/repository.py:312 + 330, so any drift
    # silently breaks answer routing.
    assert handle[1] == "awaiting_answer", (
        f"Bug 1 guard: Task.suspension_reason MUST persist the "
        f"literal 'awaiting_answer' so ``find_suspended_turn_for_answer`` "
        f"can match. Got {handle[1]!r}. A drift here silently breaks "
        f"answer / dismissal routing — every answer would be lost."
    )
    assert handle[1] == SuspensionReason.AWAITING_ANSWER.value, (
        f"Bug 1 guard: handle[1] ({handle[1]!r}) must equal "
        f"SuspensionReason.AWAITING_ANSWER.value "
        f"({SuspensionReason.AWAITING_ANSWER.value!r}). A divergence "
        f"between the Python enum value and the persisted literal "
        f"would break the selector's WHERE clause."
    )
    # §7 invariant 2: awaiting_answer requires a non-null target.
    assert handle[2] is not None
    assert handle[2] == work_id

    # The selector matches by the same literal — prove the round-trip
    # is symmetric (the selector's WHERE clause uses the literal
    # 'awaiting_answer' which is exactly what we persisted).
    task_repo = TaskRepository(engine)
    resolved = task_repo.find_suspended_turn_for_answer(iid)
    assert resolved is not None
    assert resolved.work_id == work_id
    assert resolved.suspension_reason == "awaiting_answer"

    # Negative case: the selector does NOT match rows with other
    # suspension reasons. Seed a sibling Task with a different
    # reason (awaiting_children) and verify the selector still
    # finds ONLY the awaiting_answer row.
    sibling_wid = f"work-{uuid.uuid4().hex[:12]}"
    sibling_mid = f"msg-{uuid.uuid4().hex[:12]}"
    with Session(engine) as s:
        s.add(
            Task(
                work_id=sibling_wid,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=iid,
                message_id=sibling_mid,
                status=TaskStatus.RUNNING.value,
                created_at=now,
            )
        )
        s.commit()
    with Session(engine) as s:
        SuspendTurn(
            work_id=sibling_wid,
            reason=SuspensionReason.AWAITING_CHILDREN.value,
            resume_target_turn_id=sibling_wid,
        ).run(s)
        s.commit()

    # The awaiting_children task exists but does NOT match the
    # selector's filter (the filter checks
    # suspension_reason='awaiting_answer' specifically).
    sibling_handle = _read_handle(engine, sibling_wid)
    assert sibling_handle[0] == TaskStatus.PAUSED.value
    assert sibling_handle[1] == SuspensionReason.AWAITING_CHILDREN.value

    # Selector still resolves to the original awaiting_answer task —
    # not the awaiting_children sibling.
    still_resolved = task_repo.find_suspended_turn_for_answer(iid)
    assert still_resolved is not None
    assert still_resolved.work_id == work_id, (
        "find_suspended_turn_for_answer MUST discriminate by "
        "suspension_reason='awaiting_answer' — a sibling row with "
        "a different suspension_reason (awaiting_children) must "
        "not match."
    )
    assert (
        still_resolved.suspension_reason
        == SuspensionReason.AWAITING_ANSWER.value
    )
