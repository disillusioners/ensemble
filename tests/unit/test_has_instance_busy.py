"""Tests for ``TaskRepository.has_instance_busy`` — the canonical
"is this instance busy?" predicate.

Bug-1 fix (2026-08-12, concurrency-gate review). The per-instance
guard inside ``claim_pending_task`` was the narrowest of seven
busy-check predicates (``status='running'`` only) and caused a
concurrency leak: a PENDING or PAUSED task did not block a second
task from being claimed for the same instance, allowing two
``graph.astream`` turns to race on the same ``langgraph``
``thread_id`` and shadow channel writes in the Postgres
checkpointer.

``has_instance_busy`` is the single canonical replacement. It
checks for ANY task in PENDING, RUNNING, or PAUSED state for the
given instance. This file pins the contract:

  * PENDING  → True  (a not-yet-claimed task is live work)
  * RUNNING  → True  (actively driving ``graph.astream``)
  * PAUSED   → True  (the key new behavior — PAUSED is now busy)
  * COMPLETED → False
  * CANCELLED → False
  * FAILED   → False
  * no tasks → False

The same status set (PENDING + RUNNING + PAUSED) is the live-Task
predicate the defer gate, background gate, ``claim_pending_task``
per-instance guard, ``job_continue`` concurrency gate, bus crash
recovery, and the zombie reaper's ``_has_live_work`` all use.
This test pins the contract so any drift from the canonical
predicate is caught immediately.

Dual-driver: the method uses parameterized IN-lists so the same
SQL works on both SQLite and PostgreSQL. Tests run against a
real in-memory SQLite engine (StaticPool, PRAGMA foreign_keys=ON)
— the same pattern used by ``test_cascade_pause_resume.py`` and
``test_pause_flow_redesign.py``.

Run with::

    pytest tests/unit/test_has_instance_busy.py -xvs
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
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


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    """A real ``TaskRepository`` bound to the in-memory engine.

    Tests run end-to-end against the production method (not a mock)
    so the SQL is verified, not just the Python contract.
    """
    return TaskRepository(engine=engine)


# ─── Seed helpers ────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert an Instance row. Returns the instance_id.

    Mirrors the helper in ``test_cascade_pause_resume.py`` — kept
    local so this test file has no cross-test-file fixture
    coupling.
    """
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            agent_name="developer",
            project_id="test-project",
            status=status,
            created_at=now_iso,
            updated_at=now_iso,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_task(
    engine: Engine,
    *,
    instance_id: str,
    status: str,
) -> int:
    """Insert a Task row. Returns the task id.

    Mirrors the helper in ``test_cascade_pause_resume.py``. PAUSED
    and RUNNING tasks get a ``worker_id`` (so the row looks like
    a real in-flight task); PENDING and terminal tasks do not.
    """
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            task_type="process_message",
            instance_id=instance_id,
            status=status,
            worker_id=(
                "worker-0"
                if status
                in (
                    TaskStatus.RUNNING.value,
                    TaskStatus.PAUSED.value,
                )
                else None
            ),
            started_at=now if status == TaskStatus.RUNNING.value else None,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)


# ─── Tests: has_instance_busy ───────────────────────────────────────────────


class TestHasInstanceBusy:
    """The canonical "is this instance busy?" predicate.

    Pins the status-set contract: PENDING + RUNNING + PAUSED are
    busy; COMPLETED + CANCELLED + FAILED are not; no tasks is not.
    Sister to ``TestHasInflightTask`` below — that one pins the
    PENDING + RUNNING subset of the same predicate so the
    two methods stay distinct.
    """

    def test_returns_true_when_task_is_pending(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """A PENDING task counts as busy.

        The not-yet-claimed task is live work the worker pool will
        pick up; another dispatch against the same instance must
        wait for the claim/finish cycle to complete.
        """
        iid = _seed_instance(engine)
        _seed_task(engine, instance_id=iid, status=TaskStatus.PENDING.value)

        assert task_repo.has_instance_busy(iid) is True

    def test_returns_true_when_task_is_running(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """A RUNNING task counts as busy.

        The classic "actively driving ``graph.astream``" case.
        """
        iid = _seed_instance(engine)
        _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)

        assert task_repo.has_instance_busy(iid) is True

    def test_returns_true_when_task_is_paused(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """A PAUSED task counts as busy — the key new behavior.

        Bug-1 fix (2026-08-12): the prior ``status='running'``
        per-instance guard in ``claim_pending_task`` (and the
        sister ``has_inflight_task`` PENDING+RUNNING predicate)
        excluded PAUSED — a paused instance was treated as
        "not busy" and a second task could be claimed for the
        same instance, racing the resume on the langgraph
        ``thread_id``. ``has_instance_busy`` widens the status
        set to include PAUSED so a paused instance is correctly
        recognised as busy.
        """
        iid = _seed_instance(engine)
        _seed_task(engine, instance_id=iid, status=TaskStatus.PAUSED.value)

        assert task_repo.has_instance_busy(iid) is True, (
            "PAUSED tasks must count as busy — they hold the "
            "per-instance serialization slot and will resume"
        )

    def test_returns_false_when_only_terminal_tasks(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """All terminal states (COMPLETED, CANCELLED, FAILED) → False.

        Terminal tasks have completed their lifecycle and released
        the per-instance slot. A new dispatch is allowed.
        """
        for terminal_status in (
            TaskStatus.COMPLETED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.FAILED.value,
        ):
            iid = _seed_instance(engine)
            _seed_task(
                engine, instance_id=iid, status=terminal_status
            )

            assert task_repo.has_instance_busy(iid) is False, (
                f"{terminal_status!r} is a terminal state and must "
                f"NOT count as busy"
            )

    def test_returns_false_when_no_tasks(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """An instance with zero tasks → False (no live work).

        The instance is fresh — no serialization slot is held.
        """
        iid = _seed_instance(engine)
        # No tasks seeded.
        assert task_repo.has_instance_busy(iid) is False

    def test_returns_true_when_mixed_live_and_terminal(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """Mixed live + terminal tasks → True (live wins).

        An instance with a COMPLETED task AND a PENDING task is
        busy — the PENDING task is live work even though a prior
        turn is done. The PENDING + PAUSED + RUNNING scan short-
        circuits on the first match (``LIMIT 1``) so we get
        ``True`` without scanning the rest.
        """
        iid = _seed_instance(engine)
        _seed_task(engine, instance_id=iid, status=TaskStatus.COMPLETED.value)
        _seed_task(engine, instance_id=iid, status=TaskStatus.PENDING.value)
        _seed_task(engine, instance_id=iid, status=TaskStatus.FAILED.value)

        assert task_repo.has_instance_busy(iid) is True

    def test_scopes_to_instance_id(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """Tasks for OTHER instances must not affect this check.

        Guards against a future regression where the SQL loses
        the ``WHERE instance_id = :instance_id`` predicate and
        returns True for any task in the table.
        """
        busy_iid = _seed_instance(engine)
        quiet_iid = _seed_instance(engine)
        _seed_task(
            engine, instance_id=busy_iid, status=TaskStatus.RUNNING.value
        )

        assert task_repo.has_instance_busy(busy_iid) is True
        assert task_repo.has_instance_busy(quiet_iid) is False, (
            "the busy predicate must scope to instance_id — "
            "tasks for other instances must not leak into the result"
        )

    def test_returns_true_for_unknown_instance_with_paused_task(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """The check is purely task-driven — instance status irrelevant.

        The canonical predicate is "any PENDING/RUNNING/PAUSED task
        for this instance_id". The instance's own status (RUNNING,
        PAUSED, TERMINATED) is checked by a different gate (the
        pause gate in ``claim_pending_task``) — the two are
        distinct scopes and both must hold for a claim to succeed.

        This test pins the task-side contract by seeding a
        TERMINATED instance with a PAUSED task. ``has_instance_busy``
        must still return True — the pause gate is responsible for
        the instance-status filter, not this check.
        """
        iid = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value
        )
        _seed_task(engine, instance_id=iid, status=TaskStatus.PAUSED.value)

        assert task_repo.has_instance_busy(iid) is True


# ─── Tests: claim_pending_task per-instance guard (Bug-1 regression) ────────


class TestClaimPendingTaskPerInstanceGuard:
    """Pin the per-instance guard's status set to ``RUNNING`` only.

    The per-instance guard in ``claim_pending_task`` is
    ``status='running'`` only. This pins three invariants:

    1. **A single PENDING candidate claims cleanly** — no
       self-deadlock (the candidate is PENDING, not RUNNING, so
       it doesn't match the guard).
    2. **PAUSED does NOT block a PENDING claim** — the S3
       invariant from
       ``test_s3_paused_task_does_not_block_sibling_pending_claim``
       in ``test_report_lane_phase2.py``. A RUNNING instance with
       a stale PAUSED task (from a prior pause/resume cycle)
       must still accept a fresh PENDING candidate. Including
       PAUSED in the per-instance guard would re-introduce the
       operational deadlock the S3 regression test pins.
    3. **COMPLETED does NOT block a PENDING claim** — terminal
       tasks release the per-instance slot.

    Note: Bug-1 (concurrency-gate review, 2026-08-12) is closed
    at the call-site surface via :meth:`has_instance_busy`
    (PENDING+RUNNING+PAUSED) used by ``daemon/api.py``,
    ``daemon/tools/job_queue.py:job_continue``, and
    ``_has_live_work`` — the per-instance guard itself keeps
    ``status='running'`` to preserve the S3 invariant and FIFO
    ordering. See the guard's full comment in
    ``daemon/repositories/task/repository.py`` for the rationale.
    """

    def test_claim_succeeds_when_only_task_is_candidate_pending(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """A single PENDING candidate claims cleanly — no self-deadlock.

        The candidate is PENDING, not RUNNING, so the per-instance
        guard (``status='running'``) does not match. The UPDATE
        proceeds and the candidate transitions to RUNNING.
        """
        from daemon.repositories.task.models import TaskType
        now = datetime.now(timezone.utc)
        with Session(engine) as s:
            task = Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="inst-self",
                status=TaskStatus.PENDING.value,
            )
            s.add(task)
            s.commit()
            s.refresh(task)
            task_id = int(task.id)

        claimed = task_repo.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert int(claimed.id) == task_id
        assert claimed.status == TaskStatus.RUNNING.value
        assert claimed.worker_id == "worker-1"

    def test_paused_task_does_not_block_pending_claim(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """S3 invariant: a PAUSED task does NOT block a PENDING claim.

        Sister test to
        ``test_report_lane_phase2.py::
        test_s3_paused_task_does_not_block_sibling_pending_claim``
        — the S3 regression test. A RUNNING instance with a
        stale PAUSED task (from a prior pause/resume cycle) must
        still accept a fresh PENDING candidate. Including PAUSED
        in the per-instance guard would re-introduce the
        operational deadlock the S3 invariant pins.
        """
        from daemon.repositories.task.models import TaskType
        iid = "inst-s3"
        # Seed RUNNING instance (the S3 invariant requires the
        # instance to be RUNNING, not PAUSED — the PAUSED state
        # is at the task level).
        _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)
        # PAUSED task: stale state from a prior pause/resume.
        with Session(engine) as s:
            paused = Task(
                task_type=TaskType.PROCESS_REPORT.value,
                instance_id=iid,
                status=TaskStatus.PAUSED.value,
                worker_id="worker-stale",
            )
            s.add(paused)
            s.commit()
            s.refresh(paused)
            paused_id = int(paused.id)
        # PENDING task: fresh candidate.
        with Session(engine) as s:
            pending = Task(
                task_type=TaskType.PROCESS_REPORT.value,
                instance_id=iid,
                status=TaskStatus.PENDING.value,
            )
            s.add(pending)
            s.commit()
            s.refresh(pending)
            pending_id = int(pending.id)

        claimed = task_repo.claim_pending_task(worker_id="worker-1")

        # The PENDING candidate claims; the PAUSED sibling does
        # NOT block. S3 invariant.
        assert claimed is not None
        assert int(claimed.id) == pending_id, (
            "the PENDING candidate must claim — the per-instance "
            "guard excludes PAUSED to preserve the S3 invariant "
            "(PAUSED task on a RUNNING instance must not block "
            "a fresh PENDING claim)"
        )
        # The PAUSED task is unchanged.
        with Session(engine) as s:
            from sqlmodel import select
            row = s.exec(
                select(Task).where(Task.id == paused_id)
            ).first()
            assert row is not None
            assert row.status == TaskStatus.PAUSED.value

    def test_two_pending_tasks_first_claims_other_waits(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """FIFO ordering: oldest PENDING claims, others wait their turn.

        Pre-fix this was the design intent. Two PENDING tasks for
        the same instance: the first (oldest) claims, the
        second is blocked by the now-RUNNING first task. The
        per-instance guard stays at ``status='running'`` to
        preserve this — the FIFO ordering is the contract.
        """
        from daemon.repositories.task.models import TaskType
        iid = "inst-fifo"
        _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)
        # Two PENDING tasks, oldest first.
        with Session(engine) as s:
            first = Task(
                task_type=TaskType.PROCESS_REPORT.value,
                instance_id=iid,
                status=TaskStatus.PENDING.value,
            )
            s.add(first)
            s.commit()
            s.refresh(first)
            first_id = int(first.id)
            second = Task(
                task_type=TaskType.PROCESS_REPORT.value,
                instance_id=iid,
                status=TaskStatus.PENDING.value,
            )
            s.add(second)
            s.commit()
            s.refresh(second)
            second_id = int(second.id)

        claimed = task_repo.claim_pending_task(worker_id="worker-1")

        # Oldest PENDING claims. Second waits.
        assert claimed is not None
        assert int(claimed.id) == first_id, (
            "the oldest PENDING must claim (FIFO ordering) — the "
            "per-instance guard's RUNNING-only status set lets "
            "the oldest PENDING through"
        )
        # The second PENDING is still PENDING — will be claimed
        # after the first finishes.
        with Session(engine) as s:
            from sqlmodel import select
            row = s.exec(
                select(Task).where(Task.id == second_id)
            ).first()
            assert row is not None
            assert row.status == TaskStatus.PENDING.value, (
                "the younger PENDING must remain PENDING — the "
                "first claim does NOT block it (per-instance "
                "guard is RUNNING only)"
            )

    def test_running_task_blocks_new_claim(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """A RUNNING task blocks a new PENDING claim — the original guard.

        The per-instance guard's primary purpose: while one task
        is actively driving ``graph.astream`` (RUNNING), no
        second claim for the same instance is allowed. The
        second PENDING waits for the first to finish.
        """
        from daemon.repositories.task.models import TaskType
        iid = "inst-running-block"
        _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)
        # RUNNING task — actively driving.
        with Session(engine) as s:
            running = Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=iid,
                status=TaskStatus.RUNNING.value,
                worker_id="worker-active",
            )
            s.add(running)
            s.commit()
            s.refresh(running)
        # New PENDING task on the same instance.
        with Session(engine) as s:
            new_pending = Task(
                task_type=TaskType.PROCESS_REPORT.value,
                instance_id=iid,
                status=TaskStatus.PENDING.value,
            )
            s.add(new_pending)
            s.commit()
            s.refresh(new_pending)
            new_id = int(new_pending.id)

        # The new PENDING is NOT claimable — the RUNNING task
        # holds the per-instance slot.
        claimed = task_repo.claim_pending_task(worker_id="worker-2")

        # The claim either returned None OR claimed a different
        # instance's task (this test only seeds one instance, so
        # the claim must return None).
        if claimed is not None:
            # Defensive: if a different test leaked a task into
            # this engine, ensure it's NOT the one we created.
            assert int(claimed.id) != new_id, (
                "the PENDING task must NOT claim while a "
                "RUNNING sibling exists"
            )

    def test_claim_succeeds_when_other_task_for_same_instance_is_completed(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """A COMPLETED task does NOT hold the per-instance slot.

        Terminal tasks release the slot — a fresh PENDING candidate
        for the same instance is claimable.
        """
        from daemon.repositories.task.models import TaskType
        now = datetime.now(timezone.utc)
        with Session(engine) as s:
            old = Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="inst-completed",
                status=TaskStatus.COMPLETED.value,
                worker_id="worker-old",
            )
            s.add(old)
            s.commit()
            new_task = Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="inst-completed",
                status=TaskStatus.PENDING.value,
            )
            s.add(new_task)
            s.commit()
            s.refresh(new_task)
            new_id = int(new_task.id)

        claimed = task_repo.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert int(claimed.id) == new_id, (
            "the COMPLETED task does NOT hold the per-instance slot; "
            "the new PENDING candidate is claimable"
        )


# ─── Tests: has_inflight_task (kept for cross-reference) ────────────────────


class TestHasInflightTaskSisterContract:
    """``has_inflight_task`` is a SISTER query to ``has_instance_busy``.

    It checks PENDING + RUNNING only — the "is a Task actively
    driving ``graph.astream`` right now?" subset of the live
    predicate. The two methods have different semantics and are
    not interchangeable. This class pins the existing
    ``has_inflight_task`` contract (PAUSED excluded) so the
    sister query's narrower semantic stays distinct.
    """

    def test_returns_false_when_only_paused(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """PAUSED tasks do NOT count as in-flight.

        Sister invariant to ``TestHasInstanceBusy.
        test_returns_true_when_task_is_paused`` — the two methods
        diverge exactly on PAUSED. ``has_inflight_task`` is the
        PENDING + RUNNING subset used by code paths that
        specifically need the narrower "actively driving" semantic
        (e.g. error-path comments).
        """
        iid = _seed_instance(engine)
        _seed_task(engine, instance_id=iid, status=TaskStatus.PAUSED.value)

        assert task_repo.has_inflight_task(iid) is False, (
            "has_inflight_task must remain the PENDING+RUNNING "
            "subset; PAUSED is busy (has_instance_busy) but not "
            "in-flight (has_inflight_task)"
        )

    def test_returns_true_when_running(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """RUNNING counts in both methods (the common case)."""
        iid = _seed_instance(engine)
        _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)

        assert task_repo.has_inflight_task(iid) is True
        # Sister: also returns True on has_instance_busy.
        assert task_repo.has_instance_busy(iid) is True

    def test_returns_true_when_pending(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """PENDING counts in both methods (the common case)."""
        iid = _seed_instance(engine)
        _seed_task(engine, instance_id=iid, status=TaskStatus.PENDING.value)

        assert task_repo.has_inflight_task(iid) is True
        assert task_repo.has_instance_busy(iid) is True
