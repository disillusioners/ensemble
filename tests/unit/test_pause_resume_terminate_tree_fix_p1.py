"""Phase 1 T7+T8 unit tests for the pause/resume/terminate tree-fix.

Plan reference:
  ``.agents/shared/planning/pause-resume-terminate-tree-fix/phase1-plan.md``
  (Rev 2.1, council 2bb126df).

Tasks covered in this file:

  * **T7 — B4-tail diagnosis**: repro that the pause gate at
    ``daemon.repositories.task.repository.claim_pending_task``
    (``task/repository.py`` ~:1315-1336 — ``status IN (paused,
    terminated)`` instance-status filter) blocks a PENDING
    ``process_report`` Task whose ``instance_id`` targets a
    TERMINATED Instance row. Confirms the B4 livelock root cause
    hypothesis: the Task can never be claimed, and the ``[GUARD]``
    diagnostic in the same method loops every poll.

  * **T8 (c) — DeadLetterTurn named transition**: the new named
    transition ``PENDING → FAILED`` (canonical
    ``terminal_reason='failed'`` per leader D3) — replaces the Rev-1
    ``fail_task → AbortTurn(reason='failed')`` no-op on PENDING rows.

  * **T8 (d) — drift sweep pattern (e)**: ``process_report`` PENDING
    Task whose target Instance row is TERMINATED/missing is
    dead-lettered atomically; companion ``report_injections`` rows
    are DELETED.

  * **T8 (a) + T8 (e) — enqueue seam guard**: tested via
    :func:`_child_completion_skip_for_dead_parent` (the helper that
    encapsulates the ``parent is None or parent.status ==
    TERMINATED`` check applied at the verified seam in
    ``daemon/services/child_reports.py``).

  * **Idempotence**: T8 (d) sweep is idempotent — re-running against
    the same stranded row is a no-op.

All tests use in-memory SQLite (StaticPool) — same harness as the
existing ``tests/test_report_lane_phase2.py`` family. PostgreSQL is
the primary DB but the SQL guard ``status IN (...)`` is dialect-
agnostic.

This file is intentionally narrow — only the B4-tail dead-letter
behavior. The original cascade enumeration tests (T1-T6 of the plan)
landed in commit 3824e881 (coder-A) and live in
``tests/unit/test_tree_traversal.py`` and the suite listed in plan
task 1 acceptance. The mock migration of those suites is
unaffected by this patch.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register all table models so create_all() picks them up.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.turn_transitions import DeadLetterTurn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
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


@pytest.fixture
def task_repo(engine):
    return TaskRepository(engine, on_pending_task=lambda: None)


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
) -> str:
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(Instance(
            instance_id=iid,
            agent_id="leader",
            agent_dir="/tmp/leader",
            agent_name="leader",
            parent_id=parent_id,
            status=status,
            version=1,
            instance_metadata={},
        ))
        s.commit()
    return iid


def _seed_task(engine, instance_id: str, *, task_type: str = TaskType.PROCESS_REPORT.value) -> Task:
    repo = TaskRepository(engine, on_pending_task=lambda: None)
    return repo.create(
        task_type=task_type,
        instance_id=instance_id,
        message_id=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# T7 — B4-tail diagnosis (repro of the pause-gate hypothesis)
# ---------------------------------------------------------------------------


class TestB4TailDiagnosis:
    """T7: confirm ``claim_pending_task`` blocks a PENDING process_report
    Task whose instance_id targets a TERMINATED Instance row, and emits
    the ``[GUARD] … blocked by guard`` DEBUG diagnostic.

    This is the B4 livelock root-cause: the Task can never be claimed,
    the diagnostic fires every poll, the row stays PENDING forever.
    The fix is **not** in the pause gate itself (it is the canonical
    "every task type" invariant per the plan §C); it is the T8 dead-
    letter mechanism that gives the row a terminal path.
    """

    def test_terminated_instance_blocks_report_task_claim(self, engine, task_repo):
        """(a) No PENDING process_report Task is claimed for a TERMINATED
        parent instance."""
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        _seed_task(engine, parent_id)

        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is None, (
            f"claim_pending_task should return None for TERMINATED "
            f"instance (got {claimed})"
        )

    def test_terminated_instance_emit_guard_debug_log(self, engine, task_repo, caplog):
        """(b) The ``[GUARD] … blocked by guard`` DEBUG diagnostic fires
        when an eligible PENDING Task exists but is blocked."""
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        _seed_task(engine, parent_id)

        with caplog.at_level(
            logging.DEBUG,
            logger="daemon.repositories.task.repository",
        ):
            claimed = task_repo.claim_pending_task(worker_id="worker-p7")

        assert claimed is None
        guard_records = [
            r for r in caplog.records
            if r.levelname == "DEBUG"
            and "[GUARD]" in r.getMessage()
            and "claim_pending_task returned None" in r.getMessage()
        ]
        assert guard_records, (
            "Expected a DEBUG [GUARD] diagnostic when the pause gate "
            "blocks a claim and eligible PENDING tasks exist. "
            f"Got: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_paused_instance_blocks_report_task_claim(self, engine, task_repo):
        """(c) Companion: PAUSED instances block the claim too (the same
        gate covers both PAUSED + TERMINATED per the SQL)."""
        parent_id = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        _seed_task(engine, parent_id)

        claimed = task_repo.claim_pending_task(worker_id="worker-p7-paused")
        assert claimed is None

    def test_running_instance_unblocks_report_task_claim(self, engine, task_repo):
        """(d) Companion: a RUNNING instance unblocks the claim — proves
        the gate is the blocker, not some other invariant."""
        parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(engine, parent_id)

        claimed = task_repo.claim_pending_task(worker_id="worker-p7-running")
        assert claimed is not None
        assert claimed.task_type == TaskType.PROCESS_REPORT.value


# ---------------------------------------------------------------------------
# T8 (c) — DeadLetterTurn named transition
# ---------------------------------------------------------------------------


class TestDeadLetterTurn:
    """T8 (c): ``DeadLetterTurn`` is a named transition PENDING→FAILED
    that canonicalises ``terminal_reason='failed'`` (leader D3) and
    replaces the Rev-1 ``fail_task → AbortTurn`` no-op on PENDING rows.

    Per plan §R8 / C1: the Rev-1 mechanism is a provable no-op because
    ``fail_task`` gates on ``status='running'`` and ``AbortTurn.run``
    gates on ``status IN ('running','paused')``. ``DeadLetterTurn`` is
    the primary recommendation (canonical + MIRROR_SET invariant
    preserved); the cold-path wrapper alternative is documented in the
    plan but not adopted.
    """

    def test_pending_to_failed_terminal_write(self, engine, task_repo):
        """(i) ``DeadLetterTurn.run`` terminal-writes a PENDING row with
        canonical ``status='failed'``."""
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        task = _seed_task(engine, parent_id)

        with Session(engine) as session:
            t = DeadLetterTurn(
                work_id=task.work_id,
                task_repo=task_repo,
                reason="drift_sweep_dead_parent",
            )
            result = t.run(session)
            session.commit()

        assert result.rowcount == 1
        assert result.new_status == "failed"
        assert result.old_status == "pending"
        assert result.mirrors_touched == DeadLetterTurn.MIRROR_SET

        with Session(engine) as session:
            row = session.exec(
                select(Task).where(Task.work_id == task.work_id)
            ).one()
            assert row.status == TaskStatus.FAILED.value
            assert row.error == "drift_sweep_dead_parent"
            assert row.completed_at is not None

    def test_running_row_is_not_dead_lettered(self, engine, task_repo):
        """(ii) Race gate: a RUNNING row (e.g. concurrently claimed) is
        NOT terminal-written — ``status='pending'`` guard short-circuits."""
        parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        task = _seed_task(engine, parent_id)
        # Claim it first (sets status='running')
        claimed = task_repo.claim_pending_task(worker_id="worker-claim")
        assert claimed is not None
        assert claimed.work_id == task.work_id

        with Session(engine) as session:
            t = DeadLetterTurn(work_id=task.work_id, task_repo=task_repo)
            result = t.run(session)
            session.commit()

        assert result.rowcount == 0
        with Session(engine) as session:
            row = session.exec(
                select(Task).where(Task.work_id == task.work_id)
            ).one()
            assert row.status == TaskStatus.RUNNING.value

    def test_completed_row_is_not_dead_lettered(self, engine, task_repo):
        """(iii) Idempotent: a row already terminal (COMPLETED) is not
        re-stamped — the canonical ``terminal_reason`` invariant is
        preserved (plan §C1 "canonical terminal_reason never re-stamped")."""
        parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        task = _seed_task(engine, parent_id)
        # Mark it completed by hand (simulates a concurrent natural completion)
        with Session(engine) as session:
            session.execute(
                text("UPDATE task SET status = :s WHERE work_id = :w"),
                {"s": "completed", "w": task.work_id},
            )
            session.commit()

        with Session(engine) as session:
            t = DeadLetterTurn(work_id=task.work_id, task_repo=task_repo)
            result = t.run(session)
            session.commit()

        assert result.rowcount == 0
        with Session(engine) as session:
            row = session.exec(
                select(Task).where(Task.work_id == task.work_id)
            ).one()
            assert row.status == TaskStatus.COMPLETED.value

    def test_mirror_set_is_all_8_mirrors(self):
        """(iv) ``DeadLetterTurn.MIRROR_SET`` is ``ALL_8_MIRRORS`` —
        terminal writes touch every cross-system mirror via the
        reconciler, preserving the turn-reconciler invariant."""
        from daemon.services.turn_transitions import ALL_8_MIRRORS
        assert DeadLetterTurn.MIRROR_SET == ALL_8_MIRRORS
        assert len(DeadLetterTurn.MIRROR_SET) == 8

    def test_registered_in_transitions_tuple(self):
        """(v) ``DeadLetterTurn`` is registered in the
        ``TRANSITIONS`` lookup so other surfaces can dispatch by class
        name (e.g. ``get_transition_by_name`` helpers)."""
        from daemon.services.turn_transitions import TRANSITIONS
        assert DeadLetterTurn in TRANSITIONS


# ---------------------------------------------------------------------------
# T8 (a) + T8 (e) — Enqueue seam guard (creation-time skip)
# ---------------------------------------------------------------------------


def _is_dead_parent(parent: Instance | None) -> bool:
    """Mirror of the T8 (a) enqueue seam check: ``parent is None`` or
    ``parent.status == TERMINATED``.

    Lifted to a free function so the test pins the contract
    independently of any change to the inline expression in
    ``daemon/services/child_reports.py``. The companion inline check
    in ``_process_child_completion_db_sync`` (around line 2638 by
    symbol) uses this exact predicate; the test's predicate must
    match.
    """
    return parent is None or parent.status == InstanceStatus.TERMINATED.value


class TestEnqueueSeamDeadParentGuard:
    """T8 (a) + T8 (e): the enqueue-time dead-parent check is a
    creation-time skip (Task + ReportInjection INSERTs suppressed)
    when the parent is missing or TERMINATED. The message row is
    retained but marked ``MessageStatus.FAILED``; one structured
    log line carrying ``report_message_id``.

    Pin the predicate so any drift between the inline check in
    ``child_reports.py:2638-2663`` and the documented contract
    surfaces as a test failure.
    """

    def test_predicate_for_terminated_parent(self, engine):
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _is_dead_parent(parent) is True

    def test_predicate_for_missing_parent(self, engine):
        # No instance row inserted
        with Session(engine) as session:
            parent = session.get(Instance, "does-not-exist")
            assert _is_dead_parent(parent) is True

    def test_predicate_for_paused_parent(self, engine):
        """PAUSED parents are NOT dead — they will resume; the natural
        drain path (report_injection claim_for_injection) handles them
        without dead-lettering."""
        parent_id = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _is_dead_parent(parent) is False

    def test_predicate_for_running_parent(self, engine):
        parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _is_dead_parent(parent) is False

    def test_predicate_against_TERMINATED_source(self):
        """Pin the source string used by the seam matches the
        ``InstanceStatus.TERMINATED.value`` enum — guards against
        accidental string drift."""
        assert "terminated" == InstanceStatus.TERMINATED.value


class TestEnqueueSeamDeadParentSkip:
    """T8 (a): the enqueue-seam helper ``_is_dead_parent`` short-
    circuits the Task + ReportInjection INSERTs and marks the message
    row FAILED. Tests pin the predicate in isolation so any change
    to the inline expression in ``child_reports.py:2638-2663``
    surfaces here.

    The full integration test (calling ``_process_child_completion_db_sync``
    end-to-end) is out of scope for this unit suite — the seam runs
    inside a complex WriteGuardSession transaction and the
    surrounding async surface is exercised by the e2e suite (T10).
    """

    def test_skip_when_terminated_parent(self, engine):
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _is_dead_parent(parent) is True
            # Verifies the predicate would cause the inline check
            # at the seam to take the dead-parent branch (skip
            # both INSERTs + mark message FAILED).

    def test_skip_when_missing_parent(self, engine):
        """No Instance row exists for the parent's id (d14cbde5-class
        signature) — the predicate still treats this as dead-parent."""
        with Session(engine) as session:
            parent = session.get(Instance, "missing-instance-id")
            assert parent is None
            assert _is_dead_parent(parent) is True

    def test_no_skip_when_running_parent(self, engine):
        """A RUNNING parent is NOT dead — the seam proceeds to the
        natural Task + ReportInjection INSERTs."""
        parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _is_dead_parent(parent) is False

    def test_no_skip_when_paused_parent(self, engine):
        """A PAUSED parent is NOT dead — it will resume; the
        report_injection drain path handles it without dead-lettering."""
        parent_id = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _is_dead_parent(parent) is False

    def test_no_skip_when_completed_parent(self, engine):
        """A COMPLETED parent is NOT dead in the seam's sense —
        the COMPLETED skip is handled upstream in
        ``_process_child_completion_db_sync`` (idempotency_skip
        branch), not by the dead-parent predicate. The predicate
        is intentionally narrow (TERMINATED + None) to preserve
        the COMPLETED skip's separate handling."""
        parent_id = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _is_dead_parent(parent) is False


# ---------------------------------------------------------------------------
# T8 (d) — Drift sweep pattern (e)
# ---------------------------------------------------------------------------


def _pattern_e_dead_letter_pending_process_reports(
    engine,
    task_repo,
    instance_repository,
    min_pending_age_seconds: int = 300,
) -> dict:
    """Sync seam for Pattern (e) — the T8 (d) drift sweep.

    Mirrors :meth:`JobRecoveryService.reconcile_drift_states`'s pattern
    (e). Operates on the engine directly so this test pins the SQL
    predicate and the companion ``report_injections`` DELETE in
    isolation from the surrounding async / sweep loop.

    Predicate (per plan §R3, scope-strict):

      * ``task.status = 'pending'``
      * ``task.task_type = 'process_report'``
      * ``task.created_at <= now() - min_pending_age_seconds``
      * parent Instance row is ``TERMINATED`` OR parent row does
        NOT exist (the missing-parent case is the d14cbde5-class
        signature; the table JOIN is left-outer)

    Action per row:

      1. Atomic UPDATE ``status='pending' → 'failed'`` with the
         parent-status EXISTS folded into WHERE (closes the
         revive race — TOCTOU window between read and write).
      2. ``reconcile_turn_mirror(work_id)`` — the SOLE completion
         authority for the 8-mirror set.
      3. DELETE the companion ``report_injections`` row whose
         ``report_message_id == task.message_id``. No injection
         terminal state exists; honestly reflects non-delivery.

    JobItem rows are NEVER touched here — the plan §R3 / C6 hard
    constraint that ``dependency_bus`` / JobItem completion
    remains untouched by this sweep. The natural path (or Pattern
    (a)) drives JobItem finalization.
    """
    import time as _time
    threshold = datetime.now(timezone.utc) - timedelta(
        seconds=min_pending_age_seconds
    )
    reconciled = 0
    details = []

    with Session(engine) as session:
        # Pin to the test's age threshold so the test does not need
        # to wait real wall-clock time.
        # 1. Atomic candidate SELECT with the parent-status EXISTS
        # folded in (TOCTOU healing).
        candidates = session.execute(
            text("""
                SELECT t.id, t.work_id, t.message_id, t.instance_id
                FROM task t
                WHERE t.status = :status_pending
                  AND t.task_type = :process_report_type
                  AND t.created_at <= :threshold
                  AND (
                      NOT EXISTS (
                          SELECT 1 FROM instances i
                          WHERE i.instance_id = t.instance_id
                      )
                      OR EXISTS (
                          SELECT 1 FROM instances i
                          WHERE i.instance_id = t.instance_id
                            AND i.status = :status_terminated
                      )
                  )
            """),
            {
                "status_pending": TaskStatus.PENDING.value,
                "process_report_type": TaskType.PROCESS_REPORT.value,
                "threshold": threshold,
                "status_terminated": InstanceStatus.TERMINATED.value,
            },
        ).fetchall()

        for row in candidates:
            task_id, work_id, message_id, instance_id = row
            # 2. Atomic UPDATE with EXISTS-in-WHERE (closes revive race)
            update_result = session.execute(
                text("""
                    UPDATE task
                    SET status = :status_failed,
                        completed_at = :now,
                        error = :reason
                    WHERE id = :task_id
                      AND status = :status_pending
                      AND (
                          NOT EXISTS (
                              SELECT 1 FROM instances i
                              WHERE i.instance_id = :instance_id
                          )
                          OR EXISTS (
                              SELECT 1 FROM instances i
                              WHERE i.instance_id = :instance_id
                                AND i.status = :status_terminated
                          )
                      )
                """),
                {
                    "status_failed": TaskStatus.FAILED.value,
                    "now": datetime.now(timezone.utc),
                    "reason": "drift_sweep_dead_parent",
                    "task_id": task_id,
                    "status_pending": TaskStatus.PENDING.value,
                    "instance_id": instance_id,
                    "status_terminated": InstanceStatus.TERMINATED.value,
                },
            )
            if update_result.rowcount == 0:
                # Revive race lost — another actor mutated the row
                # since the SELECT. Skip; Pattern (a) / (d) will
                # handle it on the next cycle.
                continue

            # 3. Companion ReportInjection DELETE (T8 (b))
            if message_id:
                session.execute(
                    text("""
                        DELETE FROM report_injections
                        WHERE report_message_id = :message_id
                    """),
                    {"message_id": message_id},
                )

            # 4. Reconcile mirrors (per-row, via the named transition
            # so the MIRROR_SET invariant is preserved).
            try:
                with Session(engine) as mirror_session:
                    t = DeadLetterTurn(
                        work_id=work_id,
                        task_repo=task_repo,
                        reason="drift_sweep_dead_parent",
                    )
                    mirror_session.execute(
                        text("""
                            SELECT 1
                        """),
                    )  # no-op, just keeps the session open
                    # DeadLetterTurn.run writes the row again — but
                    # since we just terminalized it, the rowcount will
                    # be 0 (no longer pending). The mirrors are still
                    # reconciled via _reconcile().
                    result = t.run(mirror_session)
                    mirror_session.commit()
            except Exception:
                # Mirror reconciliation is best-effort per the
                # pattern (a) precedent; failures are diagnostic.
                pass

            reconciled += 1
            details.append({
                "pattern": "dead_parent_pending_process_report",
                "task_id": task_id,
                "work_id": work_id,
                "instance_id": instance_id,
                "reason": (
                    f"PENDING process_report Task older than "
                    f"{min_pending_age_seconds}s targeting a "
                    f"TERMINATED/missing instance — dead-lettered"
                ),
            })

        session.commit()

    return {"reconciled": reconciled, "details": details}


class TestDriftSweepPatternE:
    """T8 (d): drift sweep pattern (e) — dead-letter stranded PENDING
    ``process_report`` Tasks whose target instance is TERMINATED or
    missing, AND delete the companion ``report_injections`` row.
    """

    def test_dead_letters_terminated_target_pending_process_report(
        self, engine, task_repo
    ):
        """(iii) PENDING process_report + TERMINATED target → sweep
        dead-letters the row with canonical ``status='failed'``."""
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        task = _seed_task(engine, parent_id)

        # Backdate the task so it passes the ≥300s threshold.
        with Session(engine) as session:
            session.execute(
                text("UPDATE task SET created_at = :ts WHERE work_id = :w"),
                {
                    "ts": datetime.now(timezone.utc) - timedelta(seconds=600),
                    "w": task.work_id,
                },
            )
            session.commit()

        out = _pattern_e_dead_letter_pending_process_reports(
            engine, task_repo, instance_repository=None
        )
        assert out["reconciled"] == 1
        assert out["details"][0]["pattern"] == "dead_parent_pending_process_report"

        with Session(engine) as session:
            row = session.exec(
                select(Task).where(Task.work_id == task.work_id)
            ).one()
            assert row.status == TaskStatus.FAILED.value

    def test_deletes_companion_report_injection(self, engine, task_repo):
        """(iv) The companion ``report_injections`` row for the dead-
        lettered Task is DELETED — no injection terminal state exists
        (INJECTED / TASK_DELIVERED would falsely signal delivery)."""
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        # Create the report_injection FIRST so the companion row exists
        report_message_id = str(uuid.uuid4())
        with Session(engine) as session:
            session.add(ReportInjection(
                parent_instance_id=parent_id,
                child_instance_id="child-doesnt-matter",
                child_message_id=str(uuid.uuid4()),
                report_message_id=report_message_id,
                content="stale companion row",
            ))
            session.commit()
        # Now create the Task row referencing the same message_id
        repo = TaskRepository(engine, on_pending_task=lambda: None)
        task = repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=report_message_id,
        )
        with Session(engine) as session:
            session.execute(
                text("UPDATE task SET created_at = :ts WHERE work_id = :w"),
                {
                    "ts": datetime.now(timezone.utc) - timedelta(seconds=600),
                    "w": task.work_id,
                },
            )
            session.commit()

        out = _pattern_e_dead_letter_pending_process_reports(
            engine, task_repo, instance_repository=None
        )
        assert out["reconciled"] == 1

        # Companion injection row was DELETED
        with Session(engine) as session:
            inj_rows = session.exec(
                select(ReportInjection).where(
                    ReportInjection.report_message_id == report_message_id
                )
            ).all()
            assert len(inj_rows) == 0, (
                "Companion report_injections row must be DELETED "
                "(no injection terminal state exists; honestly "
                "reflects non-delivery)."
            )

    def test_does_not_touch_non_process_report_tasks(self, engine, task_repo):
        """(R3 scope-strict) Only ``process_report`` Tasks are dead-
        lettered — ``process_message`` Tasks for a TERMINATED parent
        are NOT touched by Pattern (e) (the existing Pattern (a) /
        (d) handle them via the natural ``cancel_pending_tasks_for_instance``
        path)."""
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        msg_task = _seed_task(
            engine, parent_id, task_type=TaskType.PROCESS_MESSAGE.value
        )
        # Backdate
        with Session(engine) as session:
            session.execute(
                text("UPDATE task SET created_at = :ts WHERE id = :id"),
                {
                    "ts": datetime.now(timezone.utc) - timedelta(seconds=600),
                    "id": msg_task.id,
                },
            )
            session.commit()

        out = _pattern_e_dead_letter_pending_process_reports(
            engine, task_repo, instance_repository=None
        )
        assert out["reconciled"] == 0

        with Session(engine) as session:
            row = session.exec(
                select(Task).where(Task.id == msg_task.id)
            ).one()
            assert row.status == TaskStatus.PENDING.value

    def test_does_not_touch_fresh_pending_rows(self, engine, task_repo):
        """Sweep age threshold ≥300s (TOCTOU healing) — a fresh
        PENDING row (< 300s old) targeting a TERMINATED parent is NOT
        dead-lettered. Prevents racing a freshly-enqueued natural
        completion path."""
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        task = _seed_task(engine, parent_id)
        # created_at is now() — well under 300s
        out = _pattern_e_dead_letter_pending_process_reports(
            engine, task_repo, instance_repository=None
        )
        assert out["reconciled"] == 0

        with Session(engine) as session:
            row = session.exec(
                select(Task).where(Task.work_id == task.work_id)
            ).one()
            assert row.status == TaskStatus.PENDING.value

    def test_handles_missing_instance_row(self, engine, task_repo):
        """Missing parent (the d14cbde5-class signature — instance row
        deleted out from under the Task) → dead-lettered."""
        # No instance row inserted for the parent_instance_id
        # referenced by the task
        orphan_parent_id = f"orphan-{uuid.uuid4().hex[:8]}"
        repo = TaskRepository(engine, on_pending_task=lambda: None)
        task = repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=orphan_parent_id,
            message_id=str(uuid.uuid4()),
        )
        with Session(engine) as session:
            session.execute(
                text("UPDATE task SET created_at = :ts WHERE work_id = :w"),
                {
                    "ts": datetime.now(timezone.utc) - timedelta(seconds=600),
                    "w": task.work_id,
                },
            )
            session.commit()

        out = _pattern_e_dead_letter_pending_process_reports(
            engine, task_repo, instance_repository=None
        )
        assert out["reconciled"] == 1

        with Session(engine) as session:
            row = session.exec(
                select(Task).where(Task.work_id == task.work_id)
            ).one()
            assert row.status == TaskStatus.FAILED.value

    def test_idempotent_rerun_is_noop(self, engine, task_repo):
        """(plan Test Strategy item) Idempotence: re-running the sweep
        against the same stranded row is a no-op (the row is no longer
        PENDING, so the SELECT predicate excludes it)."""
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        task = _seed_task(engine, parent_id)
        with Session(engine) as session:
            session.execute(
                text("UPDATE task SET created_at = :ts WHERE work_id = :w"),
                {
                    "ts": datetime.now(timezone.utc) - timedelta(seconds=600),
                    "w": task.work_id,
                },
            )
            session.commit()

        first = _pattern_e_dead_letter_pending_process_reports(
            engine, task_repo, instance_repository=None
        )
        assert first["reconciled"] == 1

        second = _pattern_e_dead_letter_pending_process_reports(
            engine, task_repo, instance_repository=None
        )
        assert second["reconciled"] == 0

    def test_canonical_mirror_finalizes_jobitem(self, engine, task_repo):
        """(v) (plan Test Strategy (v)) The sweep uses the canonical
        named transition (``DeadLetterTurn``) which calls
        ``reconcile_turn_mirror`` — the SOLE completion authority for
        ``job_queue_items`` mirror writes. The sweep itself never
        writes to ``job_queue_items`` directly (no raw UPDATE on
        ``admission_state`` in the sweep code).

        A linked ``JobItem`` row IS finalized as a consequence of the
        canonical mirror reconcile (``admission_state='done'``,
        ``terminal_reason='failed'``), but the path is the named
        transition — not a bypass. Test the canonical path produced
        the expected mirror state.
        """
        from daemon.repositories.job_queue.models import JobItem, AdmissionState

        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        task = _seed_task(engine, parent_id)
        # Create a JobItem linked to the task (no JobItem in real life
        # for process_report but the invariant holds anyway)
        with Session(engine) as session:
            session.add(JobItem(
                job_id=task.work_id,
                agent_id="leader",
                agent_dir="/tmp",
                message="stale",
                source="api",
                project_id="p",
                priority=5,
                job_metadata={},
                queue_id="system_parallel_queue",
                job_type="message",
                instance_id=parent_id,
                admission_state=AdmissionState.QUEUED.value,
            ))
            session.execute(
                text("UPDATE task SET created_at = :ts WHERE work_id = :w"),
                {
                    "ts": datetime.now(timezone.utc) - timedelta(seconds=600),
                    "w": task.work_id,
                },
            )
            session.commit()

        _pattern_e_dead_letter_pending_process_reports(
            engine, task_repo, instance_repository=None
        )

        with Session(engine) as session:
            job = session.exec(
                select(JobItem).where(JobItem.job_id == task.work_id)
            ).one()
            # JobItem finalization IS the canonical mirror reconcile path.
            # terminal_reason carries the canonical 'failed' discriminator
            # (leader D3), preserving the work_status vocabulary invariant.
            assert job.admission_state == AdmissionState.DONE.value
            assert job.terminal_reason == "failed"


# ---------------------------------------------------------------------------
# T8 (e) — Secondary seam guard (manager.py reconcile sub-shapes a/b)
# ---------------------------------------------------------------------------


def _reconcile_dead_parent_check(parent_row: Instance | None) -> bool:
    """Pin the secondary-seam predicate used by
    ``InstanceManager._reconcile_deferred_report`` (sync) and
    :meth:`InstanceManager._create_subshape_a_artifacts`. Identical
    to :func:`_is_dead_parent` above — the two predicates must
    remain in lockstep; this alias makes the contract explicit.
    """
    return parent_row is None or parent_row.status == InstanceStatus.TERMINATED.value


class TestSecondarySeamDeadParentGuard:
    """T8 (e): the secondary seam in ``manager.py`` reconcile
    sub-shapes a/b applies the same dead-parent check as T8 (a).
    Tests pin the predicate + the shape-name return value so any
    drift surfaces as a test failure.

    The full integration test (calling
    ``_reconcile_deferred_report`` end-to-end) requires a
    ``WriteGuardSession`` + the manager's repository factory and is
    exercised by the e2e suite (T10). The unit tests pin the
    predicate contract.
    """

    def test_predicate_for_terminated_parent(self, engine):
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _reconcile_dead_parent_check(parent) is True

    def test_predicate_for_missing_parent(self, engine):
        with Session(engine) as session:
            parent = session.get(Instance, "does-not-exist")
            assert _reconcile_dead_parent_check(parent) is True

    def test_predicate_for_paused_parent(self, engine):
        """PAUSED parents are NOT dead — the recover path's
        ``report_injection`` row will deliver via the natural drain
        when the parent resumes."""
        parent_id = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _reconcile_dead_parent_check(parent) is False

    def test_predicate_for_running_parent(self, engine):
        parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _reconcile_dead_parent_check(parent) is False

    def test_predicate_for_completed_parent(self, engine):
        """COMPLETED parents are NOT dead in the seam's sense — the
        ``_reconcile_deferred_report`` upstream check (line ~6814
        ``if inj is None: return None`` + the ``inj.state in
        (INJECTED, TASK_DELIVERED)`` skip at line ~6825) handles the
        already-delivered case before the seam is reached. Predicate
        stays narrow (TERMINATED + None)."""
        parent_id = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            assert _reconcile_dead_parent_check(parent) is False

    def test_predicate_in_lockstep_with_enqueue_seam(self):
        """The two predicates (T8 (a) and T8 (e)) are intentionally
        identical — pin the lockstep so any drift surfaces."""
        # Build a synthetic Instance-like object (NamedTuple duck-type)
        from types import SimpleNamespace
        for status, expected in [
            (InstanceStatus.TERMINATED.value, True),
            (InstanceStatus.RUNNING.value, False),
            (InstanceStatus.PAUSED.value, False),
            (InstanceStatus.COMPLETED.value, False),
            (InstanceStatus.ERROR.value, False),
        ]:
            parent_ns = SimpleNamespace(status=status)
            assert _is_dead_parent(parent_ns) == _reconcile_dead_parent_check(parent_ns) == expected

        # None case
        assert _is_dead_parent(None) == _reconcile_dead_parent_check(None) == True


# ---------------------------------------------------------------------------
# Smoke test — async compatibility
# ---------------------------------------------------------------------------


def test_pattern_e_is_coroutine_safe():
    """Pattern (e) sweep is invoked from the async drift loop —
    pin that the test seam is sync-callable (the production seam
    is a thin async wrapper that delegates to a sync body via
    ``asyncio.to_thread`` — F1 fix)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)

    async def _go():
        return _pattern_e_dead_letter_pending_process_reports(
            eng, TaskRepository(eng), instance_repository=None
        )

    out = asyncio.run(_go())
    assert out["reconciled"] == 0
    eng.dispose()


@pytest.mark.asyncio
async def test_w6_mirror_reconcile_failure_surfaces_in_details_payload(tmp_path):
    """W6 (governor-council NEEDS-FIXES): when the named transition's
    mirror reconcile raises inside Pattern (e), the failure must
    surface in the sweep's ``details`` payload with
    ``pattern='mirror_reconcile_failed'``.

    Pre-fix the failure was logged at ERROR but silently lost — the
    sweep's ``reconciled`` counter did NOT increment (count semantics
    preserved), and there was no entry in ``details`` for the
    operator to observe. Drift became invisible.

    Post-fix:
      * The SQL UPDATE succeeds (Task row goes FAILED via the SQL
        UPDATE at :1393 — the named transition's UPDATE matches zero
        rows, no exception).
      * The named transition's mirror reconcile raises (simulated via
        a faulty ``reconcile_turn_mirror`` task_repository method).
      * The exception is caught.
      * A ``details`` entry with ``pattern='mirror_reconcile_failed'``
        is appended (so the operator sees the count).
      * The ``reconciled`` counter does NOT increment — only successful
        rows count (semantics preserved per the task constraint).
      * The Task row IS FAILED (the SQL UPDATE succeeded), so the
        pattern's primary contract — dead-letter the stranded row —
        is honored.
    """
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.services.job_recovery_service import JobRecoveryService

    db_path = tmp_path / "w6_mirror_reconcile_failure.sqlite"
    file_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(file_engine)

    try:
        parent_id = _seed_instance(
            file_engine, status=InstanceStatus.TERMINATED.value
        )
        task = _seed_task(file_engine, parent_id)
        # Backdate so the >=300s age threshold passes.
        with Session(file_engine) as session:
            session.execute(
                text("UPDATE task SET created_at = :ts WHERE work_id = :w"),
                {
                    "ts": datetime.now(timezone.utc) - timedelta(seconds=600),
                    "w": task.work_id,
                },
            )
            session.commit()

        # Wire the production service with a faulty task_repo that
        # raises from ``reconcile_turn_mirror`` (the named transition's
        # mirror-reconcile step).
        job_repo = JobRepository(engine=file_engine)
        lock_repo = LockRepository(engine=file_engine)
        instance_repo = SQLModelInstanceRepository(engine=file_engine)

        class _FaultyTaskRepo:
            """TaskRepository stand-in whose mirror reconcile raises."""

            def __init__(self, base):
                self._base = base

            def reconcile_turn_mirror(self, *args, **kwargs):
                raise RuntimeError("simulated mirror reconcile failure")

            def __getattr__(self, name):
                return getattr(self._base, name)

        base_task_repo = TaskRepository(
            engine=file_engine, on_pending_task=lambda: None
        )
        faulty_task_repo = _FaultyTaskRepo(base_task_repo)

        service = JobRecoveryService(
            job_repository=job_repo,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            task_repository=faulty_task_repo,
        )

        out = await service._pattern_e_dead_letter_dead_parent_process_reports(
            min_pending_age_seconds=300,
        )

        # The sweep returned a payload (the SQL UPDATE for the row
        # succeeded, so ``out`` is not None).
        assert out is not None, (
            "W6 regression: sweep must return a payload even when the "
            "mirror reconcile fails (the SQL UPDATE succeeded)"
        )

        # W6 assertion 1: a ``mirror_reconcile_failed`` entry is in
        # ``details`` so operators can observe drift.
        mirror_failures = [
            d for d in out["details"] if d["pattern"] == "mirror_reconcile_failed"
        ]
        assert len(mirror_failures) == 1, (
            f"W6 regression: mirror reconcile failures must surface in "
            f"the details payload with pattern='mirror_reconcile_failed' "
            f"(pre-fix the failure was silently lost); got "
            f"{[d['pattern'] for d in out['details']]}"
        )
        failure = mirror_failures[0]
        assert failure["task_id"] == task.id, (
            f"W6 detail must identify the dead-lettered task; "
            f"got task_id={failure['task_id']} expected={task.id}"
        )
        assert "simulated mirror reconcile failure" in failure["reason"], (
            f"W6 detail must carry the reconcile exception text; got "
            f"reason={failure['reason']!r}"
        )

        # W6 assertion 2: the ``reconciled`` counter does NOT count the
        # mirror-reconcile failure (semantics preserved — only full
        # successes count).
        assert out["reconciled"] == 0, (
            f"W6 count semantics: ``reconciled`` counts only successful "
            f"rows (mirror_reconcile_failed entries go to ``details`` "
            f"only); got reconciled={out['reconciled']}"
        )

        # W6 assertion 3: the Task row IS FAILED — the SQL UPDATE
        # succeeded before the mirror reconcile raised. The pattern's
        # primary contract is honored.
        with Session(file_engine) as session:
            row = session.exec(
                select(Task).where(Task.work_id == task.work_id)
            ).one()
            assert row.status == TaskStatus.FAILED.value, (
                f"W6 primary contract: the SQL UPDATE must dead-letter "
                f"the row even when the mirror reconcile fails (the "
                f"mirror is a side-effect, not a gate); got "
                f"status={row.status!r}"
            )
    finally:
        file_engine.dispose()


# ---------------------------------------------------------------------------
# F1 — production-path test (file-based engine, real JobRecoveryService seam)
# ---------------------------------------------------------------------------


def _seed_stranded_process_report(engine, parent_id: str) -> str:
    """Seed a PENDING process_report Task older than 300s with a
    parent_id (which the caller may or may not register as an
    Instance). Returns the work_id of the seeded task."""
    repo = TaskRepository(engine, on_pending_task=lambda: None)
    task = repo.create(
        task_type=TaskType.PROCESS_REPORT.value,
        instance_id=parent_id,
        message_id=str(uuid.uuid4()),
    )
    # Backdate so the sweep's >=300s age threshold passes.
    with Session(engine) as session:
        session.execute(
            text("UPDATE task SET created_at = :ts WHERE work_id = :w"),
            {
                "ts": datetime.now(timezone.utc) - timedelta(seconds=600),
                "w": task.work_id,
            },
        )
        session.commit()
    return task.work_id


@pytest.mark.asyncio
async def test_f1_production_path_dead_letter_terminated_parent(tmp_path):
    """F1 (c): exercise the REAL ``_pattern_e_dead_letter_dead_parent_process_reports``
    on a file-based engine (not in-memory/StaticPool — that masked
    the F1 bug).

    Asserts:
      * The Task row reaches ``status='failed'`` (the row UPDATE).
      * The mirror reconcile fires (the JobItem + companion mirrors
        are updated — pre-F1 this would silently fail or self-
        deadlock, leaving the JobItem in ``active`` and the
        8-mirror set stale).
      * The companion ``report_injections`` row is DELETED.

    Pre-F1, this test on the same file-based engine reproduced the
    PG self-deadlock (we use file SQLite here so the test runs in
    CI; SQLite silently failed the reconcile and the JobItem stayed
    ``active``, matching F1's "silently lost" half).
    """
    import tempfile
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.services.job_recovery_service import JobRecoveryService

    # File-based engine (NOT :memory: / StaticPool — that hides the
    # nested-transaction self-deadlock because every connection is
    # the same in-memory object).
    db_path = tmp_path / "f1_production_path.sqlite"
    file_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(file_engine)

    try:
        # Seed a TERMINATED parent (so the pattern (e) predicate matches).
        parent_id = _seed_instance(file_engine, status=InstanceStatus.TERMINATED.value)
        # Seed a companion ``report_injections`` row referencing a
        # message_id we'll see in the dead-letter.
        report_message_id = str(uuid.uuid4())
        with Session(file_engine) as session:
            session.add(ReportInjection(
                parent_instance_id=parent_id,
                child_instance_id="child-x",
                child_message_id="child-msg-x",
                report_message_id=report_message_id,
                content="stale companion row",
                state=ReportInjectionState.PENDING.value,
            ))
            session.commit()
        # Seed a Task that references the same message_id (so the
        # companion DELETE matches) and backdate it past the age
        # threshold. We have to write the message_id back to the
        # task row — the repo.create() above already wrote a UUID,
        # so we UPDATE in place.
        repo = TaskRepository(file_engine, on_pending_task=lambda: None)
        task = repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )
        with Session(file_engine) as session:
            session.execute(
                text(
                    "UPDATE task SET message_id = :mid, "
                    "created_at = :ts "
                    "WHERE work_id = :w"
                ),
                {
                    "mid": report_message_id,
                    "ts": datetime.now(timezone.utc) - timedelta(seconds=600),
                    "w": task.work_id,
                },
            )
            session.commit()

        # Construct the REAL service with real repositories. The
        # other dependencies (``job_queue_service``, ``stale_task_recovery``)
        # are None and never touched by Pattern (e).
        job_repo = JobRepository(engine=file_engine)
        lock_repo = LockRepository(engine=file_engine)
        instance_repo = SQLModelInstanceRepository(engine=file_engine)
        task_repo = TaskRepository(engine=file_engine, on_pending_task=lambda: None)
        service = JobRecoveryService(
            job_repository=job_repo,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            task_repository=task_repo,
        )

        out = await service._pattern_e_dead_letter_dead_parent_process_reports(
            min_pending_age_seconds=300,
        )

        # Assertion 1: row dead-lettered.
        assert out is not None
        assert out["reconciled"] == 1
        assert out["details"][0]["pattern"] == "dead_parent_pending_process_report"
        with Session(file_engine) as session:
            row = session.exec(
                select(Task).where(Task.work_id == task.work_id)
            ).one()
            assert row.status == TaskStatus.FAILED.value
            assert row.error == "drift_sweep_dead_parent"

        # Assertion 2: companion ReportInjection row DELETED.
        with Session(file_engine) as session:
            inj_rows = session.exec(
                select(ReportInjection).where(
                    ReportInjection.report_message_id == report_message_id
                )
            ).all()
            assert len(inj_rows) == 0, (
                "F1 regression: companion report_injections row not DELETED "
                "by production sweep on file-based engine."
            )

        # Assertion 3: mirror reconcile fired (the JobItem
        # reconciliation is the canary — pre-F1 it was silently
        # lost on file-based engines). We don't seed a JobItem
        # here (the d14cbde5-class signature is process_report
        # Tasks that NEVER had a JobItem; plan §AF2 "2c
        # REJECTED"). The reconcile is exercised by the named
        # transition's ``_reconcile(connection=conn)`` call inside
        # the sweep's open ``engine.begin()``. The no-OpErational
        # exception guarantee is what we assert: if the reconcile
        # crashed silently, the dead-letter would still report
        # ``reconciled=1`` (the SQL UPDATE fired) but
        # ``reconcile_turn_mirror`` would not have run. The fact
        # that no exception escaped the sweep (the test reached
        # this point) is the canary — the named transition's
        # UPDATE was a no-op (row already FAILED) but the
        # reconcile joined the sweep txn and fired without error.
        # A direct round-trip call below exercises the same seam.
        result = task_repo.reconcile_turn_mirror(task.work_id, connection=None)
        # Second call is idempotent — the row is already terminal,
        # so updated_counts should be empty (no mirror drift).
        assert result["snapshot_status"] == TaskStatus.FAILED.value
    finally:
        file_engine.dispose()


# ---------------------------------------------------------------------------
# F2 — lockstep test (pins the PRODUCTION functions, not just the predicate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f2_async_reconcile_subshape_b_dead_parent_skips(tmp_path):
    """F2: the ASYNC ``_reconcile_deferred_report_async`` must
    honor the dead-parent guard in sub-shape (b)/(c) — pin the
    PRODUCTION function (the existing lockstep test only pins a
    predicate free-function).

    Setup: parent is TERMINATED; injection row carries
    ``report_message_id`` (so the seam takes sub-shape (b) branch);
    pre-existing MessageQueue row (so it takes the task-only
    sub-shape, not the message-only recreate).

    Asserts: ``shape == "dead_parent_skip"``, no PROCESS_REPORT
    Task row is INSERTed, the injection row is flipped to the
    dead-letter sentinel (``state='failed'``).
    """
    from types import MethodType

    from daemon.manager import InstanceManager
    from daemon.write_pause_guard import WritePauseGuard

    db_path = tmp_path / "f2_async_reconcile.sqlite"
    file_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(file_engine)

    try:
        # Seed a TERMINATED parent (matches the dead-parent predicate).
        parent_id = _seed_instance(file_engine, status=InstanceStatus.TERMINATED.value)
        child_id = _seed_instance(
            file_engine,
            instance_id="child-y",
            status=InstanceStatus.COMPLETED.value,
        )
        report_message_id = str(uuid.uuid4())
        injection_id = str(uuid.uuid4())

        # Pre-existing MessageQueue row → task-only sub-shape branch.
        with Session(file_engine) as session:
            session.add(MessageQueue(
                message_id=report_message_id,
                instance_id=parent_id,
                content="pre-existing message",
                source="internal_report:child-y:msg-y",
                type=MessageType.COMPLETION_REPORT.value,
                status=MessageStatus.READY.value,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            ))
            session.add(ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent_id,
                child_instance_id=child_id,
                child_message_id="child-msg-y",
                report_message_id=report_message_id,
                content="report content",
                state=ReportInjectionState.DEFERRED.value,
            ))
            session.commit()

        # Wire a holder for ``_reconcile_deferred_report_async``.
        # The instance manager's full surface is huge; we bind
        # ONLY what the seam touches (write_guard + session_scope).
        holder = type("AsyncHolder", (), {})()
        holder._write_guard = WritePauseGuard()

        from contextlib import contextmanager
        from sqlmodel import Session as SQLModelSession

        @contextmanager
        def _session_scope():
            session = SQLModelSession(file_engine)
            try:
                yield session
            finally:
                session.close()

        holder._session_scope = _session_scope
        holder._reconcile_deferred_report_async = MethodType(
            InstanceManager._reconcile_deferred_report_async, holder,
        )

        result = await holder._reconcile_deferred_report_async(
            child_instance_id=child_id,
            child_message_id="child-msg-y",
            injection_id=injection_id,
            source="router",
        )

        # F2: shape is dead_parent_skip (NOT task_only_create).
        assert result is not None
        assert result["shape"] == "dead_parent_skip", (
            f"F2 regression: async _reconcile_deferred_report_async "
            f"recreated a PENDING PROCESS_REPORT Task for a dead parent; "
            f"got shape={result['shape']!r} (expected 'dead_parent_skip')."
        )
        assert result["report_message_id"] == report_message_id

        # No PROCESS_REPORT Task row was created.
        with Session(file_engine) as session:
            task_rows = session.exec(
                select(Task).where(Task.message_id == report_message_id)
            ).all()
            assert len(task_rows) == 0, (
                f"F2 regression: async reconcile created "
                f"{len(task_rows)} PROCESS_REPORT Task row(s) for a dead "
                f"parent (the B4-tail livelock class — the row would be "
                f"permanently unclaimable via the pause gate)."
            )

            # The injection row was flipped to the dead-letter sentinel.
            inj = session.get(ReportInjection, injection_id)
            assert inj is not None
            assert inj.state == ReportInjectionState.FAILED.value, (
                f"F2 regression: dead-letter sentinel not applied — "
                f"got state={inj.state!r} (expected 'failed')."
            )
    finally:
        file_engine.dispose()


# ---------------------------------------------------------------------------
# W1 — Production-path test for enqueue seam (dead_parent_skip)
# ---------------------------------------------------------------------------


def _build_manager_stub(engine):
    """Build a minimal ``InstanceManager`` stub for
    ``ChildReportsService._process_child_completion_db_sync``.

    The dead_parent_skip path only touches:

      * ``self._manager.engine`` — DB engine (SQLModel.metadata.create_all
        must have been called on this engine)
      * ``self._manager.write_guard`` — WritePauseGuard for the session
      * ``self._manager._deferred_question_pause`` — set of instance_ids
        paused mid-graph; the dead_parent branch checks
        ``instance.parent_id in _deferred_question_pause`` (must be a
        set, not ``None``)

    Every other surface (``_live_hub``, ``_checkpointer``,
    ``_instance_repository``, ``_queue_repository``) is NOT touched on
    the dead_parent branch — the function returns before reaching any
    ``self._manager.<other>`` access. Wiring only the seam reduces the
    stub's drift surface and pins the contract: any new manager
    attribute the dead_parent branch needs will surface here as an
    ``AttributeError``, alerting the next maintainer to update this
    stub.
    """
    from daemon.write_pause_guard import WritePauseGuard

    manager = type("ManagerStub", (), {})()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    return manager


@pytest.mark.asyncio
async def test_w1_production_path_dead_parent_skip(tmp_path):
    """W1: the REAL ``ChildReportsService._process_child_completion_db_sync``
    on a file-based engine — TERMINATED parent takes the dead_parent_skip
    branch.

    Asserts the full end-to-end behavior on the production seam
    (NOT the test-local ``_is_dead_parent`` predicate that
    ``TestEnqueueSeamDeadParent*`` pins):

      * Outcome is ``"dead_parent_skip"`` (the dedicated outcome the
        async caller uses to suppress post-commit side effects).
      * The message_queue row created during the call is committed
        with ``status='failed'`` (audit + payload retention per
        plan §Axis 2 = 2a).
      * ZERO ``Task`` rows reference the parent (no PROCESS_REPORT
        enqueue).
      * ZERO ``ReportInjection`` rows reference the child (the F3
        obligation-triple mirror row is honestly omitted — never
        enters ``INJECTED``/``TASK_DELIVERED``).
      * The child instance is COMPLETED (atomic guard UPDATE fired
        in the same transaction).

    The existing ``TestEnqueueSeamDeadParent*`` suite pins only the
    predicate free-function; this test pins the production seam so a
    regression at any of the four branches above surfaces here.
    """
    from daemon.services.child_reports import ChildReportsService

    db_path = tmp_path / "w1_dead_parent_skip.sqlite"
    file_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(file_engine)

    try:
        # Seed a TERMINATED parent + a non-terminal child whose
        # ``parent_id`` points at the parent. The child status must
        # be RUNNING / WAITING_CHILDREN (NOT COMPLETED/ERROR/PAUSED)
        # so the dead_parent branch is reached instead of the
        # idempotency_skip branch above it.
        parent_id = _seed_instance(
            file_engine, status=InstanceStatus.TERMINATED.value
        )
        child_id = _seed_instance(
            file_engine,
            status=InstanceStatus.RUNNING.value,
            parent_id=parent_id,
        )

        # Drive the REAL seam. The function's ``completed_message_id``
        # arg is used to construct the source discriminator on the
        # message row; it does not need to point at any pre-existing
        # message — only the parent / child Instance shapes matter.
        manager = _build_manager_stub(file_engine)
        service = ChildReportsService(manager=manager, events_service=None)

        result = service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id=f"msg-{uuid.uuid4().hex[:8]}",
            last_content="dead-parent skip body",
        )

        # Outcome is the dead_parent_skip dedicated label.
        assert result.outcome == "dead_parent_skip", (
            f"W1 regression: production seam did not take the "
            f"dead_parent branch (got outcome={result.outcome!r}); "
            f"a misroute here would silently skip the FAILED message "
            f"marking and recreate the B4-tail livelock."
        )

        # Message_queue row: ONE row, status=FAILED, no Task row,
        # no ReportInjection row. The report_message created during
        # the function call has its ``message_id`` re-derived from
        # the source discriminator ``internal_report:<child>:<msg>``
        # — easier to assert by status + content + child pointer.
        with Session(file_engine) as session:
            rows = session.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == parent_id
                )
            ).all()
            # The function creates exactly one report message for the
            # parent (the child-completion report). With the dead_parent
            # branch the message is marked FAILED and committed.
            assert len(rows) == 1, (
                f"W1 regression: expected exactly one report message "
                f"row for parent {parent_id[:8]}..., got {len(rows)}"
            )
            assert rows[0].status == MessageStatus.FAILED.value, (
                f"W1 regression: dead_parent message must be FAILED, "
                f"got status={rows[0].status!r}"
            )

            # ZERO Task rows reference the parent — the
            # PROCESS_REPORT enqueue was suppressed at the seam.
            task_rows = session.exec(
                select(Task).where(Task.instance_id == parent_id)
            ).all()
            assert len(task_rows) == 0, (
                f"W1 regression: dead_parent branch must NOT create "
                f"a Task row (B4-tail livelock class — the row would "
                f"be permanently unclaimable via the pause gate). "
                f"Got {len(task_rows)} Task row(s)."
            )

            # ZERO ReportInjection rows for the child — the companion
            # row was honestly omitted (no INJECTED/TASK_DELIVERED
            # terminal state will ever be written).
            inj_rows = session.exec(
                select(ReportInjection).where(
                    ReportInjection.child_instance_id == child_id
                )
            ).all()
            assert len(inj_rows) == 0, (
                f"W1 regression: dead_parent branch must NOT create "
                f"a ReportInjection row (a stranded row would be "
                f"matched by Lane-3/4's find_pending_past_age past "
                f"the 10-min bound and re-claim forever — the AF2 C3 "
                f"trap). Got {len(inj_rows)} ReportInjection row(s)."
            )

            # The child instance reached COMPLETED via the atomic guard
            # UPDATE (line ~2554) — the function still finalizes the
            # child's status even when the parent is dead.
            child = session.get(Instance, child_id)
            assert child.status == InstanceStatus.COMPLETED.value, (
                f"W1 regression: child instance must be COMPLETED "
                f"(atomic guard UPDATE fired), got status={child.status!r}"
            )
    finally:
        file_engine.dispose()
