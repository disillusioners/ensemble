"""Real-SQL tests for the orphan reaper (Phase 2 of System Cleanup).

Background
----------

Phase 2 of the System Cleanup button added two new repository methods
to drain *ghost* active jobs — rows whose instance has already
terminated but whose ``admission_state='active'`` was leaked by an
observer-feedback drop (process killed mid-ack, DB write race).

Three review rounds shaped this test file:

Round 1 (commit ``93b10484``):
  The original implementation had three breakages:
  1. ``frontend/src/app/pages/jobs/jobs.component.ts`` shipped a
     verbatim leftover copy of the old ``onSystemCleanup`` body at
     class-body level — the file did not compile under
     ``tsc -p tsconfig.app.json --noEmit``.
  2. ``JobQueueService.cleanup_non_terminal_jobs`` computed
     ``orphaned_reaped`` and logged it but dropped it from the
     returned dict.
  3. ``JobRepository.find_orphan_active_jobs`` used
     ``~text(...).bindparams(p=tuple)`` without
     ``expanding=True``; SQLAlchemy raised ``AssertionError`` on
     every real call.

Round 2 (commit ``e78bd932``):
  Fixed all three breakages. ``force_finalize_orphan`` shipped an
  elaborate synthetic-lock-row bypass under the assumption that
  the deferred PG constraint triggers
  ``trg_job_queue_items_active_lock_guard`` /
  ``trg_job_locks_active_guard`` would reject the UPDATE — but
  ``trg_job_queue_items_active_lock_guard`` only fires when
  ``NEW.admission_state = 'active'``, so the reaper's
  ``active → done`` UPDATE is not in scope.

Round 3 (the current shape):
  The synthetic-lock-row mechanism is removed entirely. The
  reaper is a bare ``UPDATE … SET admission_state='done'`` which
  is portable to both SQLite and PostgreSQL with no bypass — the
  triggers do not fire. This file pins that contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.instance.models import Instance


def _make_job(
    session: Session,
    job_id: str,
    *,
    admission_state: str = AdmissionState.ACTIVE.value,
    instance_id: str | None = "inst-1",
    job_type: str = "message",
) -> JobItem:
    """Build and INSERT a ``JobItem`` against the open session."""
    item = JobItem(
        job_id=job_id,
        agent_id="agent",
        agent_dir="/tmp",
        message="m",
        source="api",
        project_id="p1",
        queue_id="q1",
        priority=1,
        admission_state=admission_state,
        created_at=datetime.now(timezone.utc).isoformat(),
        instance_id=instance_id,
        job_type=job_type,
        retry_count=0,
        version=0,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _make_instance(
    session: Session,
    instance_id: str,
    status: str = "processing",
) -> Instance:
    ins = Instance(
        instance_id=instance_id,
        agent_id="agent",
        agent_dir="/tmp",
        status=status,
        version=0,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(ins)
    session.commit()
    session.refresh(ins)
    return ins


# ── Tests ────────────────────────────────────────────────────────────


class TestFindOrphanActiveJobs:
    """Real-SQL exercise of :meth:`JobRepository.find_orphan_active_jobs`.

    The first-review-round failure (#3) was an ``~text(...)`` form
    that raised ``AssertionError`` inside SQLAlchemy — these tests
    exercise the finder against a real SQLite DB so the assertion
    above would surface as an immediate test failure, not a
    simulation-pass.
    """

    def test_returns_orphans_with_missing_instance(self, repository: JobRepository, engine):
        """A row whose ``instance_id`` references no ``instances`` row
        is orphan, regardless of ``job_type``."""
        with Session(engine) as session:
            _make_job(session, "ghost-missing", instance_id="inst-missing")

        with Session(engine) as session:
            result = repository.find_orphan_active_jobs()

        job_ids = sorted(j.job_id for j in result)
        assert job_ids == ["ghost-missing"]

    def test_returns_orphans_with_terminal_instance(self, repository: JobRepository, engine):
        """A row whose instance row exists but ``status`` is in the
        terminal set (completed/failed/cancelled/dead) is orphan."""
        with Session(engine) as session:
            _make_instance(session, "inst-completed", status="completed")
            _make_job(session, "ghost-completed", instance_id="inst-completed")

        with Session(engine) as session:
            result = repository.find_orphan_active_jobs()

        assert [j.job_id for j in result] == ["ghost-completed"]

    def test_returns_orphans_with_null_instance_id(self, repository: JobRepository, engine):
        """A row with ``instance_id IS NULL`` is orphan — there is no
        instance to ever fire observer feedback."""
        with Session(engine) as session:
            _make_job(session, "ghost-null", instance_id=None)

        with Session(engine) as session:
            result = repository.find_orphan_active_jobs()

        assert [j.job_id for j in result] == ["ghost-null"]

    def test_excludes_live_active_jobs(self, repository: JobRepository, engine):
        """Live active jobs (instance still processing/active) are
        NOT returned. Pin the negative case so a future refactor
        that loses the ``not_(exists(...))`` predicate cannot
        silently start reaping live jobs."""
        with Session(engine) as session:
            _make_instance(session, "inst-alive", status="processing")
            _make_job(session, "live-msg", instance_id="inst-alive")
            _make_instance(session, "inst-dead", status="completed")
            _make_job(session, "ghost-dead", instance_id="inst-dead")

        with Session(engine) as session:
            result = repository.find_orphan_active_jobs()

        assert sorted(j.job_id for j in result) == ["ghost-dead"]

    def test_terminates_for_each_terminal_status(self, repository: JobRepository, engine):
        """All four terminal instance statuses must match the NOT-IN
        predicate — pin the expanding-IN-list behaviour (#3) by
        exercising one row per terminal status and confirming each
        one is matched."""
        with Session(engine) as session:
            for status in ("completed", "failed", "cancelled", "dead"):
                _make_instance(session, f"inst-{status}", status=status)
                _make_job(
                    session,
                    f"ghost-{status}",
                    instance_id=f"inst-{status}",
                )

        with Session(engine) as session:
            result = repository.find_orphan_active_jobs()

        assert sorted(j.job_id for j in result) == [
            "ghost-cancelled",
            "ghost-completed",
            "ghost-dead",
            "ghost-failed",
        ]

    def test_excludes_soft_deleted_orphans(self, repository: JobRepository, engine):
        """``deleted_at IS NOT NULL`` rows never enter the queue
        counters and must not be returned here either."""
        with Session(engine) as session:
            _make_instance(session, "inst-completed", status="completed")
            item = _make_job(session, "deleted-orphan", instance_id="inst-completed")
            item.deleted_at = datetime.now(timezone.utc).isoformat()
            session.add(item)
            session.commit()

        with Session(engine) as session:
            result = repository.find_orphan_active_jobs()

        assert [j.job_id for j in result] == []

    def test_excludes_non_ghost_already_terminal_jobs(self, repository: JobRepository, engine):
        """Rows whose ``admission_state`` is already ``done`` / ``dead``
        are not orphans even if their instance is terminal — they
        were finalised cleanly and are not stuck."""
        with Session(engine) as session:
            _make_instance(session, "inst-completed", status="completed")
            _make_job(
                session,
                "done-not-orphan",
                instance_id="inst-completed",
                admission_state=AdmissionState.DONE.value,
            )

        with Session(engine) as session:
            result = repository.find_orphan_active_jobs()

        assert [j.job_id for j in result] == []


class TestForceFinalizeOrphan:
    """Real-SQL exercise of :meth:`JobRepository.force_finalize_orphan`.

    Round-trip: insert orphan row → reap → confirm ``done`` /
    ``terminal_reason`` written, and that any synthetic
    ``job_locks`` row written by the trigger bypass was cleaned up.
    """

    def test_message_orphan_is_finalized(self, repository: JobRepository, engine):
        """The dominant failure mode — ``job_type='message'`` ghost —
        is reaped without the trigger bypass (the trigger skips
        message jobs). Round-trip the row and assert the
        post-condition."""
        with Session(engine) as session:
            _make_instance(session, "inst-completed", status="completed")
            _make_job(session, "ghost-msg", instance_id="inst-completed", job_type="message")

        with Session(engine) as session:
            reaped = repository.force_finalize_orphan("ghost-msg", "cancelled")

        assert reaped is not None
        assert reaped.admission_state == AdmissionState.DONE.value
        assert reaped.terminal_reason == "cancelled"

    def test_non_message_orphan_is_finalized(
        self, repository: JobRepository, engine
    ):
        """A ``job_type='task'`` (non-message) orphan is reaped
        with a bare UPDATE — no synthetic lock row, no trigger
        bypass, ``done/cancelled`` is the post-condition, and no
        spurious ``job_locks`` row is created.

        Pin this contract so a future fix that re-introduces a
        bypass for non-message orphans cannot silently write
        sentinel rows.
        """
        with Session(engine) as session:
            _make_instance(session, "inst-completed", status="completed")
            _make_job(
                session,
                "ghost-task",
                instance_id="inst-completed",
                job_type="task",
            )

        with Session(engine) as session:
            reaped = repository.force_finalize_orphan("ghost-task", "cancelled")

        assert reaped is not None
        assert reaped.admission_state == AdmissionState.DONE.value
        assert reaped.terminal_reason == "cancelled"

        with engine.begin() as conn:
            all_locks = conn.exec_driver_sql(
                "SELECT lock_id FROM job_locks"
            ).fetchall()
        assert all_locks == [], (
            f"reaper must not insert any job_locks rows, found: {all_locks}"
        )

    def test_null_instance_id_orphan_is_finalized(self, repository: JobRepository, engine):
        """A ghost with ``instance_id IS NULL`` reaps with the same
        bare UPDATE — there is no FK key for the lock trigger's
        JOIN anyway. The reap must still finalize the row."""
        with Session(engine) as session:
            _make_job(session, "ghost-null", instance_id=None, job_type="task")

        with Session(engine) as session:
            reaped = repository.force_finalize_orphan("ghost-null", "cancelled")

        assert reaped is not None
        assert reaped.admission_state == AdmissionState.DONE.value
        assert reaped.terminal_reason == "cancelled"

    def test_returns_none_when_row_already_done(self, repository: JobRepository, engine):
        """A race where a concurrent legitimate finalize flipped
        ``active → done`` between ``find_orphan_active_jobs`` and
        ``force_finalize_orphan`` returns ``None`` (no-op) so the
        service loop counts it as 0 reaped."""
        with Session(engine) as session:
            _make_instance(session, "inst-completed", status="completed")
            _make_job(session, "already-done", instance_id="inst-completed")
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE job_queue_items "
                "SET admission_state = 'done' "
                "WHERE job_id = 'already-done'"
            )

        result = repository.force_finalize_orphan("already-done", "cancelled")

        assert result is None

    def test_does_not_match_soft_deleted_row(self, repository: JobRepository, engine):
        """``deleted_at IS NOT NULL`` rows are not touched — the
        WHERE clause already excludes them. Pin the contract so the
        soft-delete cleanup can't be undone by a System Cleanup
        press."""
        with Session(engine) as session:
            _make_instance(session, "inst-completed", status="completed")
            item = _make_job(session, "deleted-ghost", instance_id="inst-completed")
            item.deleted_at = datetime.now(timezone.utc).isoformat()
            session.add(item)
            session.commit()

        result = repository.force_finalize_orphan("deleted-ghost", "cancelled")

        assert result is None
