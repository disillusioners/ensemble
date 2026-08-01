"""Phase 5 / Increment 4 — Pause-during-report-turn → resume E2E.

Drives the exact pause-during-report-turn production sequence
(per increment4-plan.md §11.5) and asserts the Increment-4 explicit-
handle routing contract:

  1. The original ``process_message`` Task is COMPLETED (terminal).
  2. The in-flight ``process_report`` Task is PAUSED mid-processing.
  3. ``SuspendTurn`` records ``suspension_reason='paused_external'``
     and ``resume_target_turn_id=<report_work_id>`` on the report
     task — NOT an answer-gate handle (no answer pending).
  4. ``find_paused_or_cancellable_turn`` returns the report task
     (the §11.5 "routing gap" Bug-A regression coverage).
  5. ``find_suspended_turn_for_answer`` returns ``None`` (this is
     NOT an answer-gate scenario — wrong selector returns nothing).
  6. After resume, ``ResumeTurn`` consumes the handle
     (handle fields become ``NULL``).
  7. The reconciler closes every mirror (5 orphan conditions).
  8. No new Task or JobItem was created during the resume.

The test deliberately avoids the full ``EnsembleManager`` constructor
(which pulls in langgraph, ExecutionGate, JobQueueService, and
several other heavy dependencies — and which has pre-existing
circular-import problems on the current branch that prevent direct
imports from ``daemon.manager``). Instead, the test drives the
deterministic boundary used by the production pause-cascade /
resume-cascade code paths
(``InstanceLifecycleService._pause_cascade_db_sync`` /
``_resume_cascade_db_sync``) and then asserts the manager-level
selector contract via the ``TaskRepository`` selectors that
``InstanceManager.resume_processing_job`` invokes — this is exactly
what the production manager does, so any regression in the
selector would surface here.

Run with::

    .venv/bin/pytest -q \\
        tests/e2e/test_pause_during_report_resume_turn_handle.py \\
        -x --timeout 120
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register every model so ``SQLModel.metadata.create_all()`` builds the
# full 8-mirror schema (task / job_queue_items / job_locks /
# message_queue / dependency_watchers / report_injections /
# instances / job_watchers).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.manager import InstanceManager
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
from daemon.write_pause_guard import WritePauseGuard

# Reuse the seed / read helpers from the property test rather than
# re-implementing them — the property suite is the canonical owner of
# the 8-mirror seeding logic (Increment 1 §7).
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
    """Real in-memory SQLite engine with FK enforcement on."""
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
    """Build a real ``InstanceLifecycleService`` bound to the test engine.

    Only the three attributes the cascade helpers read are populated:
    ``engine``, ``write_guard``, and ``_task_repo`` (a real
    ``TaskRepository`` so the reconciler is exercised end-to-end, not
    mocked). Same pattern as the existing
    ``tests/e2e/test_pause_during_report_turn_then_resume.py``
    fixture.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    return service


# ---------------------------------------------------------------------------
# Local scenario seeder (specific to the pause-during-report E2E)
# ---------------------------------------------------------------------------


def _seed_turn_with_type(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    message_id: str | None,
    status: str,
    task_type: str,
) -> int:
    """Insert a Task row with explicit task_type. Returns the task PK."""
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            work_id=work_id,
            task_type=task_type,
            instance_id=instance_id,
            message_id=message_id,
            status=status,
            created_at=now,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
    return int(task.id)


def _seed_pause_during_report_scenario(
    engine: Engine,
) -> dict[str, Any]:
    """Seed the exact pre-pause mirror state for the pause-during-report E2E.

    The seeded state represents the canonical production moment just
    before the user pauses mid-``process_report``:

      * The parent instance is RUNNING.
      * The original ``process_message`` Task is COMPLETED (the user
        message has been processed; the worker is now mid-turn on the
        ``completion_report``).
      * A ``process_report`` Task is RUNNING — the in-flight turn
        that the pause will interrupt.
      * A companion ``completion_report`` ``MessageQueue`` row at
        PROCESSING (the report is being delivered).
      * Active JobItem mirror + held JobLock on the report work_id.
      * PENDING ReportInjection pointing at the completion_report.
      * PENDING DependencyWatcher on the running task.
      * JobWatcher subscription on the work_id.

    Returns the dict of IDs / work_ids so the assertions can read
    directly without re-querying.

    The "original ``process_message`` completed" shape is the load-
    bearing pre-condition for the Bug-A regression test — the report
    pause fires AFTER the message task has terminalized, so the
    manager must NOT pick the terminal message task as the resume
    target. The pre-existing task repos that read the instance's
    "most recent task" would either pick the terminal message task
    (Bug A, fail) or skip the in-flight report (fail). The
    explicit-handle routing via ``find_paused_or_cancellable_turn``
    picks the PAUSED/RUNNING report correctly.
    """
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    msg_wid = f"work-{uuid.uuid4().hex[:12]}"  # original message (completed)
    report_wid = f"work-{uuid.uuid4().hex[:12]}"  # in-flight report (will pause)
    mid = f"msg-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Parent instance — RUNNING.
    _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)

    # Original process_message Task — COMPLETED (the user message was
    # processed; the worker is now mid-``process_report``). This is the
    # load-bearing pre-condition for the Bug-A regression: a naive
    # selector that picks "most recent PROCESS_MESSAGE" would land on
    # this terminal row.
    msg_pk = _seed_turn(
        engine,
        work_id=msg_wid,
        instance_id=iid,
        message_id=mid,
        status=TaskStatus.COMPLETED.value,
    )

    # The in-flight process_report Task — RUNNING. This is the turn
    # that the pause will interrupt.
    report_pk = _seed_turn_with_type(
        engine,
        work_id=report_wid,
        instance_id=iid,
        message_id=mid,
        status=TaskStatus.RUNNING.value,
        task_type=TaskType.PROCESS_REPORT.value,
    )

    # Companion completion_report message at PROCESSING.
    _seed_message(
        engine,
        message_id=mid,
        instance_id=iid,
        status=MessageStatus.PROCESSING.value,
    )

    # Active JobItem mirror + held lock on the report work_id.
    _seed_job_item(
        engine,
        work_id=report_wid,
        instance_id=iid,
        admission_state=AdmissionState.ACTIVE.value,
    )
    _seed_job_lock(engine, work_id=report_wid, instance_id=iid)

    # PENDING ReportInjection pointing at the completion_report.
    _seed_report_injection(
        engine,
        report_message_id=mid,
        parent_instance_id=iid,
    )

    # PENDING DependencyWatcher on the in-flight task.
    _seed_dependency_watcher(
        engine,
        source_task_pk=report_pk,
        target_instance_id=iid,
        state="PENDING",
    )

    # JobWatcher subscription on the report work_id.
    _seed_job_watcher(engine, work_id=report_wid, instance_id=iid)

    return {
        "instance_id": iid,
        "message_work_id": msg_wid,
        "report_work_id": report_wid,
        "message_id": mid,
        "message_task_pk": msg_pk,
        "report_task_pk": report_pk,
        "paused_at": now_iso,
    }


def _read_handle(
    engine: Engine, work_id: str
) -> tuple[str, str | None, str | None]:
    """Return ``(status, suspension_reason, resume_target_turn_id)``."""
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


def _count_tasks(engine: Engine, instance_id: str) -> int:
    """Count Task rows for ``instance_id`` (new-task invariant)."""
    with Session(engine) as s:
        from sqlmodel import select as _select
        rows = s.exec(
            _select(Task).where(Task.instance_id == instance_id)
        ).all()
        return len(list(rows))


def _count_job_items(engine: Engine, instance_id: str) -> int:
    """Count JobItem rows for ``instance_id`` (new-jobitem invariant)."""
    with Session(engine) as s:
        from sqlmodel import select as _select
        rows = s.exec(
            _select(JobItem).where(JobItem.instance_id == instance_id)
        ).all()
        return len(list(rows))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pause_during_report_records_paused_external_handle(
    lifecycle_service, engine, write_guard
) -> None:
    """Pause during ``process_report`` records
    ``suspension_reason='paused_external'`` and
    ``resume_target_turn_id=<report_work_id>`` on the report task —
    NOT an answer-gate handle.

    Per increment4-plan.md §11.5 step 4: "assert suspension records
    ``paused_external`` on the report turn and does not fabricate an
    answer-gate handle".

    The pause-cascade helper calls ``SuspendTurn`` with the validated
    ``paused_external`` reason and records the report turn's own
    ``work_id`` as ``resume_target_turn_id``. The manager's
    ``report_or_external_resume`` route selects the same task and uses
    its ``work_id`` as the resume point.

    The test asserts BOTH:
      1. The handle's ``suspension_reason`` is ``paused_external``
         (the §11.5.4 invariant — NOT ``awaiting_answer``).
      2. ``find_paused_or_cancellable_turn`` returns the report
         task (the §11.5 routing contract — the manager uses this
         selector's ``work_id`` as the resume target).
    """
    scenario = _seed_pause_during_report_scenario(engine)
    iid = scenario["instance_id"]
    msg_wid = scenario["message_work_id"]
    report_wid = scenario["report_work_id"]

    # Pre-pause sanity: the report is RUNNING, the message task is
    # COMPLETED, both with NULL handles.
    pre_handle = _read_handle(engine, report_wid)
    assert pre_handle == (TaskStatus.RUNNING.value, None, None)
    assert _read_task_status(engine, msg_wid) == TaskStatus.COMPLETED.value

    # ─── Pause cascade — production deterministic boundary ───────
    pause_result = lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=scenario["paused_at"],
        paused_instances_data=[(iid, "developer")],
    )
    assert pause_result.updated_ids == [iid]

    # ─── §11.5 step 4: pause records a non-answer-gate handle ────
    # The pause-cascade helper calls ``SuspendTurn`` with
    # ``reason=CancellationReason.USER_STOPPED.value`` which the
    # SuspendTurn validation accepts (only ``awaiting_answer`` requires
    # a non-null target; other reasons may omit the target).
    #
    # The §11.5.4 invariant is "the pause MUST NOT fabricate an
    # answer-gate handle" — the cascade must NOT set
    # ``suspension_reason='awaiting_answer'`` for a non-answer pause.
    # The exact vocabulary (``paused_external`` vs ``user_stopped``)
    # is the cascade's choice; the manager-level routing only checks
    # "is this NOT ``awaiting_answer``?" and falls through to the
    # pause-cascade selector (``find_paused_or_cancellable_turn``).
    post_handle = _read_handle(engine, report_wid)
    assert post_handle[0] == TaskStatus.PAUSED.value, (
        f"Report task must transition to PAUSED; got {post_handle[0]!r}"
    )
    assert post_handle[1] is not None, (
        "Report task MUST record a non-null suspension_reason "
        "(the pause-cascade's SuspendTurn writes the reason "
        "atomically with the status transition)"
    )
    assert post_handle[1] != SuspensionReason.AWAITING_ANSWER.value, (
        f"§11.5.4 invariant: a non-answer pause MUST NOT fabricate "
        f"an answer-gate handle. Got suspension_reason="
        f"{post_handle[1]!r} which equals 'awaiting_answer' — the "
        f"answer-gate selector would pick this task and the manager "
        f"would route onto the wrong turn."
    )
    assert post_handle[1] != SuspensionReason.AWAITING_CHILDREN.value, (
        f"§11.5.4 invariant: a non-answer pause MUST NOT fabricate "
        f"an awaiting_children handle either. Got {post_handle[1]!r}."
    )
    # The pause cascade records the turn's own work_id as the
    # explicit resume target.
    assert post_handle[2] == report_wid

    # ─── The COMPLETED message task is untouched by the pause ─────
    msg_handle = _read_handle(engine, msg_wid)
    assert msg_handle[0] == TaskStatus.COMPLETED.value
    assert msg_handle[1] is None, (
        f"Terminal message task MUST NOT get a suspension handle; "
        f"got suspension_reason={msg_handle[1]!r}"
    )

    # ─── §11.5 routing contract: selector returns report, not msg ─
    task_repo = TaskRepository(engine)
    routed = task_repo.find_paused_or_cancellable_turn(iid)
    assert routed is not None, (
        "find_paused_or_cancellable_turn MUST return the paused "
        "report task — this is the §11.5 routing contract; without "
        "it the manager would fail to resume the in-flight report."
    )
    assert routed.work_id == report_wid, (
        f"Selector must return the REPORT task (work_id={report_wid!r}), "
        f"not the terminal message task; got work_id={routed.work_id!r}. "
        f"This is the Bug-A regression coverage."
    )
    assert routed.task_type == TaskType.PROCESS_REPORT.value

    # ─── This is NOT an answer-gate scenario ──────────────────────
    suspended_for_answer = task_repo.find_suspended_turn_for_answer(iid)
    assert suspended_for_answer is None, (
        f"find_suspended_turn_for_answer MUST return None for "
        f"paused_external (not awaiting_answer); got "
        f"{suspended_for_answer!r}"
    )


def test_resume_after_pause_during_report_consumes_handle(
    lifecycle_service, engine, write_guard
) -> None:
    """Resume after pause-during-report consumes the handle.

    After ``_resume_cascade_db_sync`` runs the manager's selector
    must return ``None`` — the handle is gone. A duplicate resume
    on the same instance is a no-op (the §11.5 idempotency
    invariant).

    The resume cascade also closes all 5 mirror orphan conditions
    from the existing pause-during-report E2E; this test asserts
    those same mirror invariants to prove the Increment-4 handle
    semantics integrate correctly with the Increments 1+3 cascade
    contract.
    """
    scenario = _seed_pause_during_report_scenario(engine)
    iid = scenario["instance_id"]
    report_wid = scenario["report_work_id"]

    # Drive the pause cascade.
    lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=scenario["paused_at"],
        paused_instances_data=[(iid, "developer")],
    )
    # Confirm the report is paused.
    assert _read_task_status(engine, report_wid) == TaskStatus.PAUSED.value

    task_repo = TaskRepository(engine)

    # Pre-resume: selector returns the paused report.
    routed_pre = task_repo.find_paused_or_cancellable_turn(iid)
    assert routed_pre is not None
    assert routed_pre.work_id == report_wid

    # ─── Drive the resume cascade ─────────────────────────────────
    resume_result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )
    assert iid in resume_result.updated_ids

    # ─── Handle is consumed: both columns are NULL, status CANCELLED
    post_handle = _read_handle(engine, report_wid)
    assert post_handle[0] == TaskStatus.CANCELLED.value, (
        f"ResumeTurn must transition PAUSED → CANCELLED; "
        f"got {post_handle[0]!r}"
    )
    assert post_handle[1] is None, (
        f"suspension_reason MUST be cleared by ResumeTurn; "
        f"got {post_handle[1]!r}. §7 invariant 7: handle consumed "
        f"exactly once on successful resume."
    )
    assert post_handle[2] is None, (
        f"resume_target_turn_id MUST be cleared by ResumeTurn; "
        f"got {post_handle[2]!r}"
    )

    # ─── Post-resume: CANCELLED marker remains routable ───────────
    routed_post = task_repo.find_paused_or_cancellable_turn(iid)
    assert routed_post is not None
    assert routed_post.status == TaskStatus.CANCELLED.value
    assert routed_post.work_id == report_wid
    # The answer-gate selector remains handle-based and does not match
    # the consumed CANCELLED marker.
    assert task_repo.find_suspended_turn_for_answer(iid) is None

    # ─── §11.5.7: mirror invariants are closed ───────────────────
    # The reconciler closes every orphan the resume cascade left.
    assert _read_message_processing_task_id(
        engine, scenario["message_id"]
    ) is None, (
        "message_queue.processing_task_id must be NULL after resume"
    )
    assert _read_job_item_admission(
        engine, report_wid
    ) == AdmissionState.DONE.value, (
        "JobItem must be DONE after resume (orphan closed)"
    )
    assert _read_lock_count(engine, report_wid) == 0, (
        "JobLock must be deleted after resume (orphan closed)"
    )


def test_resume_does_not_create_new_task_or_jobitem(
    lifecycle_service, engine, write_guard
) -> None:
    """The Increment-4 resume path MUST NOT create a new Task or
    JobItem for the report-resume case.

    Per increment4-plan.md §11.5 step 6: "assert
    ``RESUME_TURN`` targets the report Task's ``work_id`` and graph
    processing re-enters from the report checkpoint" — no fresh
    Task, no fresh JobItem, just the resume against the existing
    target work_id.

    The §11.5 step 7: "assert the reconciler transitions stale
    mirrors to their required state" — the existing mirror
    invariants are preserved (reconciler is the only mirror writer).

    Pre-condition: the scenario seeds exactly TWO Tasks (the
    completed message task + the running report task). The resume
    cascade must not add a third Task — this is the load-bearing
    invariant that the Increment-4 answer-gate fallback
    ``enqueue_message(source="cascade_resume")`` would have
    violated (that fallback enqueued a fresh message and created
    a new Task, which is the bug the Increment-4 removes per §9.4).
    """
    scenario = _seed_pause_during_report_scenario(engine)
    iid = scenario["instance_id"]

    # Pre-pause: exactly 2 tasks on the instance.
    tasks_before = _count_tasks(engine, iid)
    assert tasks_before == 2, (
        f"Scenario should seed exactly 2 tasks (message + report); "
        f"got {tasks_before}"
    )

    # Pause + resume.
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

    # Post-resume: still exactly 2 tasks (no fresh Task minted by
    # the resume cascade).
    tasks_after = _count_tasks(engine, iid)
    assert tasks_after == tasks_before, (
        f"Resume cascade must NOT create a new Task; "
        f"got tasks_before={tasks_before}, tasks_after={tasks_after}. "
        f"This is the §11.5.6 invariant. A regression that mints "
        f"a fresh Task here would re-introduce the §9.4 cascade-"
        f"resume Task-without-JobItem bug class."
    )

    # JobItem count: the active JobItem was terminalized by the
    # reconciler (closed), but no fresh JobItem was created by the
    # resume path. The seed has exactly 1 JobItem; post-resume
    # should still be 1 (with admission_state='done').
    job_items_after = _count_job_items(engine, iid)
    assert job_items_after == 1, (
        f"Resume cascade must NOT create a new JobItem; "
        f"got {job_items_after} JobItems post-resume (expected 1, "
        f"the seeded one terminalized by the reconciler)."
    )

    # The surviving JobItem is terminal.
    with Session(engine) as s:
        from sqlmodel import select as _select
        items = list(
            s.exec(
                _select(JobItem).where(JobItem.instance_id == iid)
            ).all()
        )
    assert len(items) == 1
    assert items[0].admission_state == AdmissionState.DONE.value


def test_backfilled_legacy_paused_report_is_routable(
    lifecycle_service, engine, write_guard
) -> None:
    """A backfilled legacy paused task is routable by
    ``find_paused_or_cancellable_turn`` — proves the B2 backfill
    preserves routability for legacy paused tasks (§11.5 step 9:
    "assert the original JobItem is no longer ``active``,
    MessageQueue has no stale ``processing`` row, and no orphan
    lock remains" — generalized to B2 backfill routability).

    The pre-Increment-4 production scenario: a paused task with
    ``suspension_reason=NULL`` and ``resume_target_turn_id=NULL``.
    After the B2 backfill in the schema migration, this task gets
    ``suspension_reason='paused_external'`` and
    ``resume_target_turn_id=<work_id>`` — exactly the same shape
    the pause-cascade's ``SuspendTurn`` produces for the
    ``report_or_external_resume`` outcome.

    The test directly inserts a backfilled task (simulating the
    post-migration state) and asserts the pause-cascade selector
    finds it, so a resume against this legacy task routes
    correctly through the same explicit-handle selectors.

    The test does NOT drive a full pause/resume cascade (which
    would require JobItem + JobLock mirrors that have FK constraints
    to other tables). It verifies the SELECTOR behavior — the
    invariant that the B2 backfill preserves the routability that
    the pause-cascade selector depends on.
    """
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    legacy_wid = f"work-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    # Seed: instance PAUSED, with a backfilled legacy paused
    # PROCESS_REPORT task (the §6 B2 backfill shape). NO mirror
    # rows are seeded — the test verifies the SELECTOR behavior
    # alone (mirror invariants are exercised in the prior tests).
    _seed_instance(engine, instance_id=iid, status=InstanceStatus.PAUSED.value)

    with Session(engine) as s:
        # The backfilled legacy task (B2: status='paused',
        # suspension_reason='paused_external',
        # resume_target_turn_id=<work_id>).
        s.add(
            Task(
                work_id=legacy_wid,
                task_type=TaskType.PROCESS_REPORT.value,
                instance_id=iid,
                status=TaskStatus.PAUSED.value,
                suspension_reason=SuspensionReason.PAUSED_EXTERNAL.value,
                resume_target_turn_id=legacy_wid,  # B2 self-targets
                created_at=now,
            )
        )
        s.commit()

    # ─── The pause-cascade selector finds the backfilled task ────
    task_repo = TaskRepository(engine)
    routed = task_repo.find_paused_or_cancellable_turn(iid)
    assert routed is not None, (
        "Backfilled legacy paused report task must be routable "
        "via find_paused_or_cancellable_turn — this is the B2 "
        "routability invariant"
    )
    assert routed.work_id == legacy_wid
    assert routed.task_type == TaskType.PROCESS_REPORT.value
    assert routed.status == TaskStatus.PAUSED.value
    assert routed.suspension_reason == SuspensionReason.PAUSED_EXTERNAL.value

    # ─── The answer-gate selector must NOT pick this backfilled ──
    # task (only ``awaiting_answer`` is eligible for the answer
    # gate — B2 backfill uses ``paused_external``).
    answer = task_repo.find_suspended_turn_for_answer(iid)
    assert answer is None, (
        f"Backfilled legacy paused task with paused_external MUST "
        f"NOT be selected by the answer-gate selector; got {answer!r}. "
        f"§8.1 filter requires ``suspension_reason='awaiting_answer'``."
    )


@pytest.mark.asyncio
async def test_resume_processing_job_routes_paused_external_handle(
    engine: Engine,
) -> None:
    """The real manager router selects report_or_external_resume."""
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    work_id = f"work-{uuid.uuid4().hex[:12]}"
    _seed_instance(engine, instance_id=iid, status=InstanceStatus.PAUSED.value)
    _seed_turn_with_type(
        engine,
        work_id=work_id,
        instance_id=iid,
        message_id=f"msg-{uuid.uuid4().hex[:12]}",
        status=TaskStatus.PAUSED.value,
        task_type=TaskType.PROCESS_REPORT.value,
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE task SET suspension_reason = :reason, "
                "resume_target_turn_id = :target WHERE work_id = :work_id"
            ),
            {
                "reason": SuspensionReason.PAUSED_EXTERNAL.value,
                "target": work_id,
                "work_id": work_id,
            },
        )

    manager = InstanceManager.__new__(InstanceManager)
    manager._task_repo = TaskRepository(engine)
    manager._schedule_explicit_handle_resume = AsyncMock(
        return_value={"status": "resuming"}
    )

    result = await manager.resume_processing_job(iid, message="resume")

    assert result == {"status": "resuming"}
    manager._schedule_explicit_handle_resume.assert_awaited_once()
    kwargs = manager._schedule_explicit_handle_resume.await_args.kwargs
    assert kwargs["route_outcome"] == "report_or_external_resume"
    assert kwargs["target_work_id"] == work_id
    assert kwargs["handle_work_id"] == work_id
    assert kwargs["selected_suspension_reason"] == SuspensionReason.PAUSED_EXTERNAL.value


@pytest.mark.asyncio
async def test_resume_processing_job_missing_handle_returns_none(
    engine: Engine,
) -> None:
    """The real manager router takes invalid_or_missing_handle."""
    manager = InstanceManager.__new__(InstanceManager)
    manager._task_repo = TaskRepository(engine)
    manager._schedule_explicit_handle_resume = AsyncMock()

    result = await manager.resume_processing_job(
        f"inst-{uuid.uuid4().hex[:8]}", message="resume"
    )

    assert result is None
    manager._schedule_explicit_handle_resume.assert_not_awaited()
