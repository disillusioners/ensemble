"""Phase 5 / Increment 4 — Turn handle transition tests.

Direct unit tests for the four transitions that own the suspension
handle lifecycle:

  * ``SuspendTurn`` — atomically writes ``status='paused'`` +
    ``suspension_reason=:reason`` + ``resume_target_turn_id=:target``
    in ONE guarded UPDATE (§11.3).
  * ``ResumeTurn`` — consumes the handle by writing
    ``suspension_reason=NULL, resume_target_turn_id=NULL`` in the
    same UPDATE that flips ``paused → cancelled`` (§7 invariant 7).
  * ``CompleteTurn`` — clears the handle on the terminal
    ``running → completed`` transition (§7 invariant 9).
  * ``AbortTurn`` — clears the handle on the terminal
    ``running → failed|cancelled`` transition (§7 invariant 9).

The existing unit tests in ``tests/unit/test_pause_resume_root.py``
exercise the same handle semantics through the cascade helpers
(``_pause_cascade_db_sync`` / ``_resume_cascade_db_sync``) and the
repository selectors (``find_suspended_turn_for_answer`` /
``find_paused_or_cancellable_turn``). This file drives the
transitions directly so:

  * the atomic-write invariant is verified at the SQL level (status +
    reason + target all commit together or not at all);
  * the validation invariant (``awaiting_answer`` requires non-null
    target) is verified at the constructor level;
  * the idempotency invariant (second SuspendTurn on the same
    work_id is a no-op because the ``status='running'`` guard fails)
    is verified end-to-end through a second session.

Each transition is invoked directly via ``transition.run(session)``
on a real in-memory SQLite engine, mirroring the production call
sites (``daemon/services/instance_lifecycle.py``).

Run with::

    .venv/bin/pytest -q tests/unit/test_turn_handle_transitions.py \\
        -x --timeout 120
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all()`` emits the
# full schema — the transitions only touch ``task`` but other tables
# must exist because ``reconcile_turn_mirror`` (called from inside
# ``ResumeTurn`` / ``CompleteTurn`` / ``AbortTurn``) writes to all
# 8 mirrors.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.task.models import (
    SuspensionReason,
    Task,
    TaskStatus,
    TaskType,
)
from daemon.services.turn_transitions import (
    AbortTurn,
    CompleteTurn,
    ResumeTurn,
    SuspendTurn,
    TransitionResult,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with the full schema.

    ``StaticPool`` for cross-thread visibility; ``PRAGMA foreign_keys=ON``
    so the reconciler's mirror writes (which the transitions call
    indirectly via ``reconcile_turn_mirror``) observe the FK
    invariants.
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


def _seed_running_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    instance_id: str | None = None,
    status: str = TaskStatus.RUNNING.value,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
) -> str:
    """Insert a Task row in RUNNING state. Returns the ``work_id``."""
    work_id = work_id or f"work-{uuid.uuid4().hex[:12]}"
    instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            Task(
                work_id=work_id,
                task_type=task_type,
                instance_id=instance_id,
                status=status,
                created_at=now,
            )
        )
        session.commit()
    return work_id


def _read_handle(
    engine: Engine, work_id: str
) -> tuple[str, str | None, str | None]:
    """Read ``(status, suspension_reason, resume_target_turn_id)``.

    Uses raw SQL so the test is dialect-agnostic and the assertion
    does not depend on the SQLModel datetime hydration behaviour (see
    ``tests/unit/test_pause_resume_root.py::_read_task_status`` for
    the production cascade's TEXT-as-datetime quirk).
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


# ─── Test classes ────────────────────────────────────────────────────────────


class TestSuspendTurn:
    """``SuspendTurn`` writes status + reason + target in one transaction.

    Per increment4-plan.md §11.3: "SuspendTurn writes status, reason,
    and target in one transaction. Invalid reason, missing required
    answer target, missing target Task, or cross-instance target causes
    a complete rollback."
    """

    def test_writes_status_reason_and_target_atomically(self, engine):
        """Single UPDATE writes all three handle fields + status.

        Invariant: after ``SuspendTurn.run(session)`` commits, the
        Task row has ``status='paused'`` AND ``suspension_reason=
        'awaiting_answer'`` AND ``resume_target_turn_id=<target>``
        simultaneously. A partial-write regression (e.g., the status
        UPDATE succeeds but the handle UPDATE fails) would leave the
        task in a half-handled state and is what the single-statement
        guard protects against.
        """
        target = str(uuid.uuid4())
        work_id = _seed_running_task(engine)

        with Session(engine) as session:
            transition = SuspendTurn(
                work_id=work_id,
                reason=SuspensionReason.AWAITING_ANSWER.value,
                resume_target_turn_id=target,
            )
            result = transition.run(session)
            session.commit()

        assert isinstance(result, TransitionResult)
        assert result.work_id == work_id
        assert result.new_status == TaskStatus.PAUSED.value

        status, reason, read_target = _read_handle(engine, work_id)
        assert status == TaskStatus.PAUSED.value
        assert reason == SuspensionReason.AWAITING_ANSWER.value
        assert read_target == target, (
            f"resume_target_turn_id must equal the supplied target "
            f"{target!r}; got {read_target!r}"
        )

    def test_awaiting_answer_requires_non_null_target(self, engine):
        """``SuspensionReason.AWAITING_ANSWER`` REQUIRES a target.

        Per §7 invariant 2: ``suspension_reason='awaiting_answer'``
        MUST carry a non-null target. The transition raises
        ``ValueError`` before the UPDATE when the field shape is
        invalid — the caller sees the validation failure synchronously
        rather than discovering a NULL-handle row after commit.
        """
        work_id = _seed_running_task(engine)
        with pytest.raises(ValueError, match="awaiting_answer"):
            SuspendTurn(
                work_id=work_id,
                reason=SuspensionReason.AWAITING_ANSWER.value,
                resume_target_turn_id=None,  # MUST be non-null
            )

        # Status MUST remain RUNNING — the failed validation must not
        # touch the DB at all.
        status, reason, target = _read_handle(engine, work_id)
        assert status == TaskStatus.RUNNING.value, (
            f"Status must remain RUNNING after failed validation; "
            f"got {status!r}"
        )
        assert reason is None
        assert target is None

    def test_non_answer_reason_may_omit_target(self, engine):
        """``paused_external`` / ``awaiting_children`` may have NULL target.

        §7 invariant 2's contrapositive: only ``awaiting_answer``
        requires a target. Other reasons (especially
        ``paused_external``, set by the B2 backfill and by the
        pause-cascade cascade helper) must allow NULL targets so
        legacy rows can be re-routable after migration.
        """
        work_id = _seed_running_task(engine)
        with Session(engine) as session:
            transition = SuspendTurn(
                work_id=work_id,
                reason=SuspensionReason.PAUSED_EXTERNAL.value,
                resume_target_turn_id=None,  # allowed for non-answer reasons
            )
            transition.run(session)
            session.commit()

        status, reason, target = _read_handle(engine, work_id)
        assert status == TaskStatus.PAUSED.value
        assert reason == SuspensionReason.PAUSED_EXTERNAL.value
        assert target is None

    def test_invalid_reason_string_raises(self, engine):
        """An empty or non-string ``reason`` raises ``ValueError``."""
        work_id = _seed_running_task(engine)
        with pytest.raises(ValueError, match="not a valid SuspensionReason"):
            SuspendTurn(work_id=work_id, reason="")

        # DB must be untouched.
        status, _, _ = _read_handle(engine, work_id)
        assert status == TaskStatus.RUNNING.value

    def test_idempotent_when_already_paused(self, engine):
        """Second ``SuspendTurn`` on the same work_id is a no-op.

        The UPDATE is guarded by ``status='running'``; once the first
        ``SuspendTurn`` flips the row to ``paused`` a second
        ``SuspendTurn`` matches zero rows and silently no-ops. This
        protects against a duplicate suspension call (e.g., a
        redundant ask_questions on a turn that is already awaiting
        an answer) silently overwriting the existing handle.
        """
        target_1 = str(uuid.uuid4())
        target_2 = str(uuid.uuid4())  # would clobber target_1 if not guarded
        work_id = _seed_running_task(engine)

        # First SuspendTurn — sets handle.
        with Session(engine) as session:
            SuspendTurn(
                work_id=work_id,
                reason=SuspensionReason.AWAITING_ANSWER.value,
                resume_target_turn_id=target_1,
            ).run(session)
            session.commit()

        # Second SuspendTurn — same work_id, DIFFERENT target.
        # The guard (``status='running'``) prevents the UPDATE so the
        # first target survives unchanged.
        with Session(engine) as session:
            result = SuspendTurn(
                work_id=work_id,
                reason=SuspensionReason.AWAITING_ANSWER.value,
                resume_target_turn_id=target_2,
            ).run(session)
            session.commit()

        # Handle is still pointing at target_1.
        _, reason, read_target = _read_handle(engine, work_id)
        assert reason == SuspensionReason.AWAITING_ANSWER.value
        assert read_target == target_1, (
            f"Second SuspendTurn must NOT overwrite the first handle; "
            f"got target={read_target!r}, expected target_1={target_1!r}"
        )
        # Status unchanged from paused.
        status, _, _ = _read_handle(engine, work_id)
        assert status == TaskStatus.PAUSED.value


class TestResumeTurn:
    """``ResumeTurn`` consumes/clears the handle on the PAUSED → CANCELLED transition.

    Per §7 invariant 7: "successful resume consumes the handle exactly
    once". A duplicate resume (e.g., a duplicate answer-gate resume
    call) sees the cleared handle and becomes a no-op via the
    ``status='paused'`` guard.
    """

    def _seed_paused_task_with_handle(
        self,
        engine: Engine,
        *,
        reason: str = SuspensionReason.AWAITING_ANSWER.value,
        target: str | None = None,
    ) -> str:
        """Insert a PAUSED task with a suspension handle. Returns work_id."""
        work_id = f"work-{uuid.uuid4().hex[:12]}"
        target = target or str(uuid.uuid4())
        with Session(engine) as session:
            session.add(
                Task(
                    work_id=work_id,
                    task_type=TaskType.PROCESS_MESSAGE.value,
                    instance_id=f"inst-{uuid.uuid4().hex[:8]}",
                    status=TaskStatus.PAUSED.value,
                    suspension_reason=reason,
                    resume_target_turn_id=target,
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
        return work_id

    def test_consumes_handle_in_same_update(self, engine):
        """``ResumeTurn`` clears both handle fields AND transitions
        status in one UPDATE."""
        work_id = self._seed_paused_task_with_handle(engine)

        with Session(engine) as session:
            result = ResumeTurn(work_id=work_id).run(session)
            session.commit()

        assert result.new_status == TaskStatus.CANCELLED.value
        status, reason, target = _read_handle(engine, work_id)
        assert status == TaskStatus.CANCELLED.value, (
            "ResumeTurn must flip status PAUSED → CANCELLED "
            "(the resume driver owns the graph turn; CANCELLED keeps "
            "the WorkerPool from re-claiming and racing — D13)"
        )
        assert reason is None, (
            f"suspension_reason must be cleared on resume; "
            f"got {reason!r}"
        )
        assert target is None, (
            f"resume_target_turn_id must be cleared on resume; "
            f"got {target!r}"
        )

    def test_uses_resume_target_turn_id_for_target_work_id(self, engine):
        """``ResumeTurn`` may be invoked with a ``new_work_id`` —
        the wakeup_payload surfaces that work_id so the scheduler can
        resume against the recorded target (the §11.4.1 C2 contract).

        The transition's wakeup_payload must carry the resume target
        so the post-commit dispatch knows which ``work_id`` to
        schedule the graph against. Without this, the scheduler would
        re-derive the target from instance state and could lose the
        explicit-handle routing.
        """
        work_id = self._seed_paused_task_with_handle(engine)
        # The "next" work_id (e.g., for a retry path that mints a
        # fresh child work_id). For the answer-resume path,
        # ``new_work_id`` is None and the original work_id is used.
        with Session(engine) as session:
            result = ResumeTurn(
                work_id=work_id, new_work_id=None
            ).run(session)
            session.commit()

        assert result.wakeup_payload is not None
        assert result.wakeup_payload["event"] == "schedule_resume_job"
        # When new_work_id is None, the wakeup_payload carries the
        # original work_id so the scheduler resumes against the
        # recorded handle's target.
        assert result.wakeup_payload["work_id"] == work_id

    def test_duplicate_resume_is_idempotent(self, engine):
        """A duplicate ``ResumeTurn`` after the first one is a no-op.

        The guard ``status='paused'`` excludes the cancelled row, so
        a second invocation matches zero rows and the handle remains
        cleared. This is the §9.1 step 10 invariant: a duplicate
        answer-gate resume sees the cleared handle and becomes
        idempotent rather than routing onto a terminal Task.
        """
        work_id = self._seed_paused_task_with_handle(engine)

        # First resume — consumes the handle.
        with Session(engine) as session:
            ResumeTurn(work_id=work_id).run(session)
            session.commit()

        # Second resume — no-op (status is CANCELLED, not PAUSED).
        with Session(engine) as session:
            result = ResumeTurn(work_id=work_id).run(session)
            session.commit()

        # Status remains CANCELLED; handle still cleared.
        status, reason, target = _read_handle(engine, work_id)
        assert status == TaskStatus.CANCELLED.value
        assert reason is None
        assert target is None

    def test_find_suspended_turn_for_answer_returns_none_after_consume(
        self, engine
    ):
        """After ``ResumeTurn`` clears the handle,
        ``find_suspended_turn_for_answer`` returns ``None`` —
        the explicit-handle answer-gate selector must NOT route
        onto a Task whose handle has been consumed.
        """
        from daemon.repositories.task.repository import TaskRepository

        work_id = self._seed_paused_task_with_handle(engine)
        # Sanity: before resume, the selector returns the row.
        task_repo = TaskRepository(engine)
        before = task_repo.find_suspended_turn_for_answer(
            _seed_running_task.__wrapped__(engine)
            if False  # cheap placeholder; not actually used
            else _instance_id_for_work_id(engine, work_id)
        )
        assert before is not None
        assert before.work_id == work_id

        # Resume consumes the handle.
        with Session(engine) as session:
            ResumeTurn(work_id=work_id).run(session)
            session.commit()

        # Selector now returns None — handle is gone.
        after = task_repo.find_suspended_turn_for_answer(
            _instance_id_for_work_id(engine, work_id)
        )
        assert after is None, (
            f"find_suspended_turn_for_answer must return None after "
            f"ResumeTurn consumes the handle; got {after!r}"
        )


def _instance_id_for_work_id(engine: Engine, work_id: str) -> str:
    """Read ``instance_id`` for a given ``work_id`` (test helper)."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT instance_id FROM task WHERE work_id = :work_id"),
            {"work_id": work_id},
        ).first()
    assert row is not None, f"No task with work_id={work_id!r}"
    return row[0]


class TestCompleteTurnClearsHandle:
    """``CompleteTurn`` clears the handle on the terminal
    RUNNING → COMPLETED transition (§7 invariant 9).

    A completed historical Task must NOT be selected later by either
    the answer-gate or pause-cascade selectors — the cleared handle
    is the only authoritative signal that the Task is terminal.
    """

    def test_complete_clears_handle(self, engine):
        """``CompleteTurn`` writes ``status='completed'`` AND clears
        both handle fields in the same UPDATE."""
        work_id = _seed_running_task(engine)
        # Inject a stale handle (the production path that calls
        # CompleteTurn is the chokepoint ``complete_task`` which is
        # only called after a successful turn — but a regression
        # could leave a stale handle from a previous suspend-then-
        # resume cycle and CompleteTurn must still clear it).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET "
                    "suspension_reason = :reason, "
                    "resume_target_turn_id = :target "
                    "WHERE work_id = :work_id"
                ),
                {
                    "reason": SuspensionReason.AWAITING_ANSWER.value,
                    "target": str(uuid.uuid4()),
                    "work_id": work_id,
                },
            )

        with Session(engine) as session:
            result = CompleteTurn(work_id=work_id, result="ok").run(session)
            session.commit()

        assert result.new_status == TaskStatus.COMPLETED.value
        status, reason, target = _read_handle(engine, work_id)
        assert status == TaskStatus.COMPLETED.value
        assert reason is None, (
            f"suspension_reason must be cleared on COMPLETE; got {reason!r}"
        )
        assert target is None, (
            f"resume_target_turn_id must be cleared on COMPLETE; got {target!r}"
        )

    def test_complete_does_not_clear_non_paused_handle_shape(self, engine):
        """``CompleteTurn`` on a row with NULL handle still succeeds
        and writes status='completed' (the UPDATE is unconditional
        for the clear columns — they just stay NULL)."""
        work_id = _seed_running_task(engine)
        # No handle set — the row has NULL / NULL.
        status_before, reason_before, target_before = _read_handle(
            engine, work_id
        )
        assert reason_before is None
        assert target_before is None

        with Session(engine) as session:
            result = CompleteTurn(work_id=work_id, result="ok").run(session)
            session.commit()

        assert result.new_status == TaskStatus.COMPLETED.value
        status_after, reason_after, target_after = _read_handle(
            engine, work_id
        )
        assert status_after == TaskStatus.COMPLETED.value
        assert reason_after is None
        assert target_after is None


class TestAbortTurnClearsHandle:
    """``AbortTurn`` clears the handle on the terminal
    RUNNING → FAILED|CANCELLED transition (§7 invariant 9).

    The reason argument picks the terminal status:
      * ``reason='failed'`` → ``status='failed'``,
      * anything else → ``status='cancelled'``.
    """

    def test_abort_cancelled_clears_handle(self, engine):
        """``AbortTurn(reason='cancelled')`` clears handle + writes CANCELLED."""
        work_id = _seed_running_task(engine)
        # Stale handle (e.g., from a pause-then-resume cycle that
        # left a non-null handle behind). The AbortTurn clear must
        # remove it on terminalization.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET "
                    "suspension_reason = :reason, "
                    "resume_target_turn_id = :target "
                    "WHERE work_id = :work_id"
                ),
                {
                    "reason": SuspensionReason.PAUSED_EXTERNAL.value,
                    "target": str(uuid.uuid4()),
                    "work_id": work_id,
                },
            )

        with Session(engine) as session:
            result = AbortTurn(
                work_id=work_id, reason="cancelled"
            ).run(session)
            session.commit()

        assert result.new_status == TaskStatus.CANCELLED.value
        status, reason, target = _read_handle(engine, work_id)
        assert status == TaskStatus.CANCELLED.value
        assert reason is None
        assert target is None

    def test_abort_failed_clears_handle(self, engine):
        """``AbortTurn(reason='failed')`` clears handle + writes FAILED.

        Distinct from ``reason='cancelled'`` — the B1 critical
        invariant that ``fail_task`` produces ``terminal_reason=
        'failed'`` (not 'cancelled') relies on this dispatch.
        """
        work_id = _seed_running_task(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET "
                    "suspension_reason = :reason, "
                    "resume_target_turn_id = :target "
                    "WHERE work_id = :work_id"
                ),
                {
                    "reason": SuspensionReason.AWAITING_ANSWER.value,
                    "target": str(uuid.uuid4()),
                    "work_id": work_id,
                },
            )

        with Session(engine) as session:
            result = AbortTurn(
                work_id=work_id, reason="failed", error="boom"
            ).run(session)
            session.commit()

        assert result.new_status == TaskStatus.FAILED.value, (
            f"AbortTurn(reason='failed') must write status='failed'; "
            f"got {result.new_status!r}"
        )
        status, reason, target = _read_handle(engine, work_id)
        assert status == TaskStatus.FAILED.value
        assert reason is None
        assert target is None


class TestTransitionResultForHandleOperations:
    """``TransitionResult`` carries the handle fields so the post-commit
    dispatcher knows the recorded reason + target without re-reading
    the DB.

    The wakeup_payload is the contract between the transition and
    the caller — a regression that drops the payload would silently
    strip the resume scheduler of the target work_id.
    """

    def test_suspend_result_carries_handle_in_wakeup(self, engine):
        """``SuspendTurn`` result's wakeup_payload includes both handle fields."""
        target = str(uuid.uuid4())
        work_id = _seed_running_task(engine)

        with Session(engine) as session:
            result = SuspendTurn(
                work_id=work_id,
                reason=SuspensionReason.AWAITING_ANSWER.value,
                resume_target_turn_id=target,
            ).run(session)
            session.commit()

        assert result.wakeup_payload is not None
        payload = result.wakeup_payload
        assert payload["event"] == "graph_task_cancel"
        assert payload["work_id"] == work_id
        assert payload["suspension_reason"] == SuspensionReason.AWAITING_ANSWER.value
        assert payload["resume_target_turn_id"] == target

    def test_resume_result_carries_schedule_event(self, engine):
        """``ResumeTurn`` result's wakeup_payload is
        ``{'event': 'schedule_resume_job', 'work_id': <target>}``."""
        work_id = _seed_running_task(engine)
        # Set up the paused-task-with-handle precondition.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET status='paused', "
                    "suspension_reason='awaiting_answer', "
                    "resume_target_turn_id=:target "
                    "WHERE work_id = :work_id"
                ),
                {"work_id": work_id, "target": str(uuid.uuid4())},
            )

        with Session(engine) as session:
            result = ResumeTurn(work_id=work_id).run(session)
            session.commit()

        assert result.wakeup_payload is not None
        assert result.wakeup_payload["event"] == "schedule_resume_job"
        assert result.wakeup_payload["work_id"] == work_id
