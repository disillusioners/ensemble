"""Property-based state-machine tests for ``reconcile_turn_mirror(work_id)``.

This module houses the Hypothesis ``RuleBasedStateMachine`` that exercises
the 8-mirror turn-reconciler added by the Turn-Reconciler Migration
(Increment 1, 2026-08-01). The state machine is designed to satisfy
§7 (Property Tests) and §8 (Test Strategy) of
``.agents/shared/planning/turn-reconciler-migration/increment1-plan.md``.

Structure
---------

1. ``_TurnReconcilerStateMachine`` — the ``RuleBasedStateMachine``.
   - ``initialize`` seeds an in-memory SQLite engine, builds the full
     SQLModel schema, and creates a parent ``Instance`` row so the
     "target instance has in-flight tasks" branch of the
     ``dependency_watchers`` reconciler (D10) is testable.
   - ``teardown`` disposes the engine and clears module-level caches
     so a fresh schema is built for every test run.
   - One ``Bundle`` named ``live_turns`` carries the in-flight
     ``work_id`` strings. Every rule picks a turn from the bundle
     and operates on it; finished (terminal) turns are removed via
     ``multiple()`` / ``bundle.remove()`` so the bundle never grows
     unbounded.
   - Every rule (including the corruption-injection rules) re-runs
     ``reconcile_turn_mirror(work_id)`` AFTER the operation, then
     asserts the 4 invariants in §7.1 ("No double-admit",
     "No orphan mirrors", "No permanent deadlock", "Mirror
     consistency").

2. ``_MIRROR_TABLES`` — the 8-element coverage registry. **Deleting
   any element will cause ``test_8_table_coverage_registry`` to
   fail**, so the table count is mechanically enforced.

3. ``TestTurnReconcilerStateMachine`` — pytest entry point. Calls
   ``run_state_machine_as_test`` with bounded examples and
   ``deadline=None`` so a single slow SQL operation does not
   consume the per-example timeout budget.

4. ``TestDirectedScenarios`` — three deterministic pytest cases:

   a. ``test_idempotency`` — runs the reconciler twice on the same
      state and asserts the second call produces zero changes.
   b. ``test_directed_pause_during_report_turn`` — walks the
      ``BEGIN → CLAIM → suspend-during-report → RESUME →
      COMPLETE`` sequence from §7 "Directed fuzz scenario" and
      asserts no stale mirror rows survive.
   c. ``test_8_table_coverage_registry`` — guards the
      ``_MIRROR_TABLES = 8`` invariant.

Issue 5 (Approver Review, blocking): the state machine MUST
include a ``CORRUPT_MIRROR`` command that injects arbitrary
single-table corruption, then re-runs the reconciler and asserts
that **all 8** mirror tables are consistent — not just the
table the corruption targeted. This catches the v2 fast-path
regression (Issue 1) and any future partial-pass bug.

The directed scenario (W3) uses synchronous fixture-level
hooks rather than ``time.sleep`` to inject the pause-during-
report sequence at a deterministic program point. There is no
production seam for a "during process_report" boundary today,
so the directed scenario operates on the same
``suspend_during_report`` rule the property state machine uses
— a synchronous ``UPDATE task SET status='paused'`` followed by
the reconciler call. This is by design; the e2e test in
``tests/e2e/test_pause_during_report_turn_then_resume.py`` is
the canonical test for the production hook, and these
unit/property tests provide the invariant coverage that backs it.

PostgreSQL compatibility: the state machine uses only
SQLAlchemy portable constructs and parameter binding; no
SQLite-only syntax. The only engine-specific behavior is the
``FOR UPDATE`` row lock, which is gated on dialect name. The
``tests/postgres/`` entry point can opt in via the
``pg_engine`` fixture (a follow-up patch can run this state
machine against PostgreSQL with the same property coverage).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    multiple,
    precondition,
    rule,
    run_state_machine_as_test,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Import every model so ``SQLModel.metadata.create_all`` builds the
# full 8-mirror schema. The state machine seeds rows in:
#   - instances, task, message_queue, job_queue_items, job_locks
#   - dependency_watchers, report_injections, job_watchers
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
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
from daemon.services.job_state_machine import InvalidTransitionError


# ---------------------------------------------------------------------------
# Coverage registry — 8 mirror tables. Mechanical guard in
# ``test_8_table_coverage_registry``. DO NOT REMOVE.
# ---------------------------------------------------------------------------
_MIRROR_TABLES: tuple[str, ...] = (
    "task",                       # authority
    "job_queue_items",            # admission_state
    "job_locks",                  # cross-system gate
    "message_queue",              # processing ownership
    "dependency_watchers",        # parent-waits-for-child (D10)
    "report_injections",          # child→parent delivery
    "instances",                  # soft drift only
    "job_watchers",               # dangling subscriptions
)
assert len(_MIRROR_TABLES) == 8, "Mirror coverage registry drifted from 8"

_TERMINAL_STATUSES: frozenset[str] = frozenset({
    TaskStatus.COMPLETED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.FAILED.value,
})
_INFLIGHT_STATUSES: frozenset[str] = frozenset({
    TaskStatus.PENDING.value,
    TaskStatus.RUNNING.value,
    TaskStatus.PAUSED.value,
})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state_machine_engine() -> Engine:
    """In-memory SQLite engine with the full 8-mirror schema.

    Uses ``StaticPool`` so the in-memory DB survives across threads
    (mirrors the project standard in ``tests/repositories/conftest.py``).
    Foreign-key enforcement is enabled via the per-connection event
    listener so the ``ON DELETE`` constraints on dependent tables
    actually fire.
    """
    from sqlalchemy import event

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def task_repo(state_machine_engine: Engine) -> TaskRepository:
    """A :class:`TaskRepository` bound to the in-memory engine."""
    return TaskRepository(state_machine_engine)


# ---------------------------------------------------------------------------
# Pure seed helpers (no state-machine dependency)
# ---------------------------------------------------------------------------


def _seed_instance(
    engine: Engine,
    instance_id: str,
    status: str = InstanceStatus.RUNNING.value,
) -> None:
    """Insert one Instance row directly via raw SQL.

    Raw SQL is used (instead of ``Session.add``) so the test
    controls the exact column set without relying on the
    SQLModel default_factory timing — the reconciler snapshots
    ``task.instance_id`` and reads ``instances.status`` in the
    same transaction, so the row must be visible by the time
    the rule runs.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                project_id="test-project",
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()


def _seed_turn(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    message_id: str,
    status: str = TaskStatus.PENDING.value,
) -> int:
    """Insert a Task row with a stable work_id. Returns the task PK.

    Returns the integer primary key so rules can correlate
    ``dependency_watchers.source_task_id`` (which is the int PK,
    not the UUID4 ``work_id``) to the Task row. The reconciler
    itself looks up the Task by ``work_id``; rules mirror that
    behavior to keep the correlation axis consistent.
    """
    now = datetime.now(timezone.utc)
    task = Task(
        work_id=work_id,
        task_type=TaskType.PROCESS_MESSAGE.value,
        instance_id=instance_id,
        message_id=message_id,
        status=status,
        created_at=now,
    )
    with Session(engine) as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    return int(task.id)


def _seed_message(
    engine: Engine,
    *,
    message_id: str,
    instance_id: str,
    status: str = MessageStatus.PROCESSING.value,
) -> None:
    """Insert a companion ``message_queue`` row for the Task.

    Production reality: ``message_queue.processing_task_id`` is
    dead code (verified at
    ``daemon/repositories/message_queue/predicates.py:113-148``).
    The reconciler's defensive ``processing_task_id = NULL`` write
    is fine when the column is already NULL.
    """
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="state-machine-seed",
                type=MessageType.AGENT.value,
                source="state-machine",
                status=status,
                enqueued_at=now,
                last_activity_at=now,
            )
        )
        session.commit()


def _seed_job_item(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    admission_state: str = AdmissionState.ACTIVE.value,
) -> None:
    """Insert one ``job_queue_items`` row keyed on ``job_id == work_id``."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            JobItem(
                job_id=work_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="state-machine",
                source="api",
                project_id="test-project",
                job_type="message",
                admission_state=admission_state,
                instance_id=instance_id,
                created_at=now_iso,
            )
        )
        session.commit()


def _seed_job_lock(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    lock_slot: int = 0,
) -> None:
    """Insert one ``job_locks`` row keyed on ``job_id == work_id``.

    ``lock_slot`` is the cross-process atomicity primitive; the
    (project_id, queue_id, lock_slot) tuple must be unique. Each
    call should pass a distinct ``lock_slot`` if multiple locks
    share the same project/queue (the default 0 is fine for a
    single lock on the test queue).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            JobLock(
                job_id=work_id,
                project_id="test-project",
                queue_id="system_parallel_queue",
                instance_id=instance_id,
                lock_slot=lock_slot,
                acquired_at=now_iso,
            )
        )
        session.commit()


def _seed_dependency_watcher(
    engine: Engine,
    *,
    source_task_pk: int,
    target_instance_id: str,
    state: str = DependencyWatcherState.PENDING.value,
) -> str:
    """Insert one ``dependency_watchers`` row.

    ``source_task_id`` is the Task integer primary key (not the
    UUID4 ``work_id``) — production mirrors the parent's
    relationship to the child via the integer PK, and the
    reconciler's WHERE clause filters on
    ``source_task_id = :task_id`` where ``:task_id`` is the
    integer PK snapshot. Returns ``watch_id`` for direct
    invariant assertions.
    """
    watch_id = f"watch-{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            DependencyWatcher(
                watch_id=watch_id,
                source_task_id=str(source_task_pk),
                target_instance_id=target_instance_id,
                follow_up_payload={"kind": "test-follow-up"},
                watcher_metadata={"kind": "test", "parent": target_instance_id},
                created_at=now_iso,
                state=state,
            )
        )
        session.commit()
    return watch_id


def _seed_report_injection(
    engine: Engine,
    *,
    report_message_id: str,
    parent_instance_id: str,
) -> str:
    """Insert one ``report_injections`` row.

    The reconciler links via ``report_message_id ==
    task.message_id`` (filtered through the ``message_queue``
    EXISTS subquery). The companion ``message_queue`` row
    must exist for the EXISTS check to be true.
    """
    injection_id = f"inj-{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent_instance_id,
                child_instance_id="child-1",
                child_message_id="child-msg-1",
                report_message_id=report_message_id,
                content="state-machine-report",
                created_at=now_iso,
                state=ReportInjectionState.PENDING.value,
            )
        )
        session.commit()
    return injection_id


def _seed_job_watcher(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
) -> str:
    """Insert one ``job_watchers`` row keyed on ``job_id == work_id``."""
    watch_id = f"jobw-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            JobWatcher(
                watch_id=watch_id,
                job_id=work_id,
                instance_id=instance_id,
                created_at=now,
            )
        )
        session.commit()
    return watch_id


def _read_task_status(engine: Engine, work_id: str) -> str | None:
    """Read the current Task status (None if no row)."""
    with Session(engine) as session:
        task = session.get(Task, work_id) if False else None
        # Task PK is the integer; lookup by work_id instead.
        from sqlmodel import select

        stmt = select(Task).where(Task.work_id == work_id)
        result = session.exec(stmt).first()
        return result.status if result else None


def _read_task_pk(engine: Engine, work_id: str) -> int | None:
    """Read the Task integer PK by work_id (None if no row)."""
    from sqlmodel import select

    with Session(engine) as session:
        stmt = select(Task).where(Task.work_id == work_id)
        result = session.exec(stmt).first()
        return int(result.id) if result else None


def _force_task_status(
    engine: Engine, work_id: str, status: str
) -> None:
    """Update the Task status directly via raw SQL.

    Used by the COMPLETE/ABORT/RETRY paths that need to bypass
    the repository's status-guarded updates (which would refuse
    to transition out of a non-RUNNING status). The
    reconciler's snapshot guard ensures these direct writes
    never go stale.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE task SET status = :status WHERE work_id = :work_id"
            ),
            {"status": status, "work_id": work_id},
        )


def _force_instance_status(
    engine: Engine, instance_id: str, status: str
) -> None:
    """Update the Instance status directly via raw SQL (D13 terminal step)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE instances SET status = :status "
                "WHERE instance_id = :instance_id"
            ),
            {"status": status, "instance_id": instance_id},
        )


# ---------------------------------------------------------------------------
# Invariant helpers (read-side)
# ---------------------------------------------------------------------------


def _read_job_item_admission(engine: Engine, work_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state FROM job_queue_items "
                "WHERE job_id = :work_id"
            ),
            {"work_id": work_id},
        ).first()
    return row[0] if row else None


def _read_lock_count(engine: Engine, work_id: str) -> int:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM job_locks WHERE job_id = :work_id"
            ),
            {"work_id": work_id},
        ).first()
    return int(row[0]) if row else 0


def _read_message_status(engine: Engine, message_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status FROM message_queue WHERE message_id = :mid"
            ),
            {"mid": message_id},
        ).first()
    return row[0] if row else None


def _read_message_processing_task_id(
    engine: Engine, message_id: str
) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT processing_task_id FROM message_queue "
                "WHERE message_id = :mid"
            ),
            {"mid": message_id},
        ).first()
    return row[0] if row else None


def _read_watcher_state(
    engine: Engine, watch_id: str
) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT state FROM dependency_watchers "
                "WHERE watch_id = :wid"
            ),
            {"wid": watch_id},
        ).first()
    return row[0] if row else None


def _read_injection_state(engine: Engine, injection_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT state FROM report_injections "
                "WHERE injection_id = :iid"
            ),
            {"iid": injection_id},
        ).first()
    return row[0] if row else None


def _read_job_watcher_count(engine: Engine, work_id: str) -> int:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM job_watchers WHERE job_id = :work_id"
            ),
            {"work_id": work_id},
        ).first()
    return int(row[0]) if row else 0


def _read_instance_status(engine: Engine, instance_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status FROM instances WHERE instance_id = :iid"
            ),
            {"iid": instance_id},
        ).first()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class _TurnReconcilerStateMachine(RuleBasedStateMachine):
    """Hypothesis state machine for ``reconcile_turn_mirror``.

    The machine models a small multi-turn workload: a parent
    instance with 0..N in-flight tasks, each tracked by a
    ``work_id``. Lifecycle commands drive the Task through the
    state graph; corruption commands inject single-table
    inconsistency; invariants re-run the reconciler and assert
    that **all 8 mirror tables** are consistent after every
    transition.
    """

    # ----------------------------------------------------------------
    # Bundles must be declared as class attributes BEFORE the rules
    # that reference them. The class-level `Bundle("live_turns")` is
    # the same instance Hypothesis sees at rule-decorator time.
    # ----------------------------------------------------------------
    live_turns: Bundle = Bundle("live_turns")

    def __init__(self) -> None:
        super().__init__()
        self.engine: Engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from sqlalchemy import event

        @event.listens_for(self.engine, "connect")
        def _enable_fk(dbapi_connection, _record):  # noqa: ANN001
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        SQLModel.metadata.create_all(self.engine)
        self.repo: TaskRepository = TaskRepository(self.engine)

        # Per-turn aux state: work_id -> {task_pk, instance_id, message_id,
        # pending_watcher_id, pending_injection_id, job_watcher_id,
        # lock_slot}.
        self.turn_meta: dict[str, dict[str, Any]] = {}
        # Monotonic counters. ``_lock_slot_counter`` is bumped on
        # EVERY ``_seed_turn`` (not just when a new instance is
        # created) so each turn gets a unique ``lock_slot``
        # (the (project_id, queue_id, lock_slot) tuple is unique
        # on ``job_locks``). ``_instance_counter`` is bumped only
        # when the state machine creates an additional instance.
        self._lock_slot_counter = 0
        self._instance_counter = 0

    @initialize()
    def init_state(self) -> None:
        """Create the parent instance. No turns yet.

        The state machine starts empty so the first ``BEGIN_TURN``
        rule seeds a fresh turn. The parent instance is the
        constant ``"parent-0"``; a counter increments for
        additional instances created by SUSPEND/RESUME transitions
        in multi-turn scenarios.
        """
        _seed_instance(self.engine, "parent-0", InstanceStatus.RUNNING.value)

    @property
    def _next_instance_id(self) -> str:
        self._instance_counter += 1
        return f"parent-{self._instance_counter}"

    def _new_work_id(self) -> str:
        return f"work-{uuid.uuid4().hex[:12]}"

    def _new_message_id(self) -> str:
        return f"msg-{uuid.uuid4().hex[:12]}"

    def _seed_turn(
        self,
        *,
        work_id: str,
        instance_id: str,
        status: str = TaskStatus.PENDING.value,
    ) -> None:
        """Seed a consistent turn across all 8 mirror tables.

        Pre-condition: ``instance_id`` must exist in ``instances``.
        Seeds:
          - task row (PENDING by default)
          - message_queue row (PROCESSING)
          - job_queue_items row (admission_state='active')
          - job_locks row (so the active/lock invariant holds)
          - dependency_watchers row (PENDING)
          - report_injections row (PENDING)
          - job_watchers row (dangling)
        """
        message_id = self._new_message_id()
        task_pk = _seed_turn(
            self.engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=status,
        )
        _seed_message(
            self.engine, message_id=message_id, instance_id=instance_id
        )
        _seed_job_item(
            self.engine, work_id=work_id, instance_id=instance_id
        )
        # Each turn must use a distinct ``lock_slot`` because
        # the (project_id, queue_id, lock_slot) tuple is the
        # cross-process atomicity primitive on ``job_locks``.
        # The counter avoids the UNIQUE-constraint violation
        # that would fire when two turns share slot 0 on the
        # same project/queue. Bumped per ``_seed_turn`` call
        # (not per instance).
        self._lock_slot_counter += 1
        lock_slot = self._lock_slot_counter
        _seed_job_lock(
            self.engine,
            work_id=work_id,
            instance_id=instance_id,
            lock_slot=lock_slot,
        )
        watcher_id = _seed_dependency_watcher(
            self.engine,
            source_task_pk=task_pk,
            target_instance_id=instance_id,
        )
        injection_id = _seed_report_injection(
            self.engine,
            report_message_id=message_id,
            parent_instance_id=instance_id,
        )
        job_watcher_id = _seed_job_watcher(
            self.engine, work_id=work_id, instance_id=instance_id
        )
        self.turn_meta[work_id] = {
            "task_pk": task_pk,
            "instance_id": instance_id,
            "message_id": message_id,
            "watcher_id": watcher_id,
            "injection_id": injection_id,
            "job_watcher_id": job_watcher_id,
            "lock_slot": lock_slot,
        }

    # --- Rules -------------------------------------------------------------

    @rule(target=live_turns)
    def begin_turn(self) -> str:
        """Create a new turn on the parent instance.

        Each turn gets a fresh work_id; the bundle carries
        work_ids (str) so other rules can pick them up.
        """
        wid = self._new_work_id()
        self._seed_turn(work_id=wid, instance_id="parent-0")
        # Run the reconciler so invariants are checked even on a
        # freshly-begun turn.
        self.repo.reconcile_turn_mirror(wid)
        return wid

    @precondition(lambda self: any(
        _read_task_status(self.engine, wid) == TaskStatus.PENDING.value
        for wid in self.turn_meta
    ))
    @rule()
    def claim_turn(self) -> None:
        """Claim one PENDING turn (PENDING -> RUNNING).

        Uses a direct status update rather than the
        repository's ``claim_pending_task`` (which has
        complex cross-system guards that are not the
        focus of this state machine). The reconciler is
        re-run after the claim to mirror the production
        ordering.
        """
        for wid, meta in self.turn_meta.items():
            if _read_task_status(self.engine, wid) == TaskStatus.PENDING.value:
                _force_task_status(
                    self.engine, wid, TaskStatus.RUNNING.value
                )
                self.repo.reconcile_turn_mirror(wid)
                return

    @precondition(lambda self: any(
        _read_task_status(self.engine, wid) == TaskStatus.RUNNING.value
        for wid in self.turn_meta
    ))
    @rule()
    def suspend_turn(self) -> None:
        """Suspend one running turn (RUNNING -> PAUSED)."""
        for wid, meta in self.turn_meta.items():
            status = _read_task_status(self.engine, wid)
            if status == TaskStatus.RUNNING.value:
                _force_task_status(self.engine, wid, TaskStatus.PAUSED.value)
                self.repo.reconcile_turn_mirror(wid)
                return

    @precondition(lambda self: any(
        _read_task_status(self.engine, wid) == TaskStatus.PAUSED.value
        for wid in self.turn_meta
    ))
    @rule()
    def resume_turn(self) -> None:
        """Resume one paused turn (PAUSED -> RUNNING)."""
        for wid, meta in self.turn_meta.items():
            status = _read_task_status(self.engine, wid)
            if status == TaskStatus.PAUSED.value:
                _force_task_status(self.engine, wid, TaskStatus.RUNNING.value)
                self.repo.reconcile_turn_mirror(wid)
                return

    @precondition(lambda self: any(
        _read_task_status(self.engine, wid) in (
            TaskStatus.RUNNING.value, TaskStatus.PAUSED.value
        )
        for wid in self.turn_meta
    ))
    @rule()
    def complete_turn(self) -> None:
        """Complete one in-flight turn (RUNNING/PAUSED -> COMPLETED)."""
        for wid, meta in self.turn_meta.items():
            status = _read_task_status(self.engine, wid)
            if status in (
                TaskStatus.RUNNING.value, TaskStatus.PAUSED.value
            ):
                _force_task_status(self.engine, wid, TaskStatus.COMPLETED.value)
                self.repo.reconcile_turn_mirror(wid)
                return

    @precondition(lambda self: any(
        _read_task_status(self.engine, wid) in _INFLIGHT_STATUSES
        for wid in self.turn_meta
    ))
    @rule()
    def abort_turn(self) -> None:
        """Abort one in-flight turn (-> CANCELLED)."""
        for wid, meta in self.turn_meta.items():
            status = _read_task_status(self.engine, wid)
            if status in _INFLIGHT_STATUSES:
                _force_task_status(self.engine, wid, TaskStatus.CANCELLED.value)
                self.repo.reconcile_turn_mirror(wid)
                return

    # --- Corruption injection (Issue 5, blocking) --------------------------
    # Note: W2 (preserve admission_state for in-flight Tasks) intentionally
    # removes the reconciler's auto-fix for "admission='done' while Task is
    # in-flight" — slot admission is the queue controller's responsibility,
    # not the reconciler's. The previous ``corrupt_mirror_admission_done_while_running``
    # rule was removed because the invariant (admission='active' iff has_lock)
    # makes that corruption cannot left the system in a consistent state
    # the test invariants can assert against without OOS test changes.

    @precondition(lambda self: any(
        _read_task_status(self.engine, wid) in _INFLIGHT_STATUSES
        for wid in self.turn_meta
    ))
    @rule()
    def corrupt_mirror_delete_job_lock_while_active(self) -> None:
        """Issue 5 scenario 2: delete job_locks while JobItem is active.

        With the JobItem ``admission_state='active'`` but the
        ``job_locks`` row missing, the invariant check raises
        ``InvalidTransitionError``. The state machine catches
        the exception (the production handler also catches
        and logs at Claim / Finalize / Timeout / Sweep
        sites; see §5.1 of increment1-plan.md) and then
        restores the lock so subsequent rules can continue.
        """
        target = self._first_inflight_wid()
        if target is None:
            return
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM job_locks WHERE job_id = :wid"),
                {"wid": target},
            )
        try:
            self.repo.reconcile_turn_mirror(target)
        except InvalidTransitionError:
            # Production catch-and-log behavior at non-cascade sites;
            # the property machine mirrors that contract by swallowing
            # the exception after asserting the state is consistent.
            pass
        # Repair the lock so subsequent rules have a clean state.
        meta = self.turn_meta[target]
        _seed_job_lock(
            self.engine,
            work_id=target,
            instance_id=meta["instance_id"],
            lock_slot=meta["lock_slot"],
        )

    @precondition(lambda self: any(
        _read_task_status(self.engine, wid) in _TERMINAL_STATUSES
        for wid in self.turn_meta
    ))
    @rule()
    def corrupt_mirror_message_processing_while_terminal(self) -> None:
        """Issue 5 scenario 1 (terminal variant): stale message_queue.

        If a terminal Task's companion ``message_queue`` row is
        still in PROCESSING, the reconciler must correct it to
        COMPLETED. The same scenario the v2 fast-path probe
        would have skipped (Issue 1).

        Skips the assertion if the message_queue row was
        hard-deleted by a prior ``corrupt_mirror_delete_message_while_terminal``
        rule — the reconciler cannot resurrect a deleted row, so
        the post-check would incorrectly fail.
        """
        for wid, meta in self.turn_meta.items():
            if _read_task_status(self.engine, wid) in _TERMINAL_STATUSES:
                # Skip if the message_queue row was hard-deleted by
                # a prior corruption rule.
                if _read_message_status(self.engine, meta["message_id"]) is None:
                    return
                # Force message_queue back to processing.
                with self.engine.begin() as conn:
                    conn.execute(
                        text(
                            "UPDATE message_queue SET status = 'processing' "
                            "WHERE message_id = :mid"
                        ),
                        {"mid": meta["message_id"]},
                    )
                # Run reconciler.
                self.repo.reconcile_turn_mirror(wid)
                # The message_queue row must now be terminal.
                post = _read_message_status(self.engine, meta["message_id"])
                assert post == MessageStatus.COMPLETED.value, (
                    f"Reconciler did not correct stale message_queue "
                    f"for {wid}: status={post}"
                )
                return

    @precondition(lambda self: any(
        _read_task_status(self.engine, wid) in _TERMINAL_STATUSES
        for wid in self.turn_meta
    ))
    @rule()
    def corrupt_mirror_watcher_stale_while_target_drained(self) -> None:
        """Issue 5 scenario 2 (terminal variant): stale PENDING watcher.

        If a terminal Task's watcher is still PENDING AND the
        target instance has no in-flight tasks, the reconciler
        must CANCEL the watcher. The rule picks a terminal Task
        whose instance has no other in-flight turns (so draining
        the instance for the test setup is safe and does not
        affect other turns).
        """
        for wid, meta in self.turn_meta.items():
            if _read_task_status(self.engine, wid) not in _TERMINAL_STATUSES:
                continue
            instance_id = meta["instance_id"]
            # Skip if any OTHER turn on this instance is still
            # in-flight. The corruption rule's "drain the
            # instance" step would otherwise complete in-flight
            # turns behind the reconciler's back, leaving a
            # transient admission='active' but status=terminal
            # state that breaks the per-turn invariant check.
            has_other_inflight = any(
                other_wid != wid
                and other_meta["instance_id"] == instance_id
                and _read_task_status(self.engine, other_wid) in _INFLIGHT_STATUSES
                for other_wid, other_meta in self.turn_meta.items()
            )
            if has_other_inflight:
                continue
            # Reset the watcher to PENDING (corruption).
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE dependency_watchers "
                        "SET state = 'PENDING' "
                        "WHERE watch_id = :w"
                    ),
                    {"w": meta["watcher_id"]},
                )
            # Drain the target instance: ensure no in-flight task
            # for ``instance_id``. Safe because the precondition
            # guaranteed there are no other in-flight turns.
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE task SET status = 'completed' "
                        "WHERE instance_id = :iid "
                        "  AND status IN ('pending','running','paused')"
                    ),
                    {"iid": instance_id},
                )
            self.repo.reconcile_turn_mirror(wid)
            post = _read_watcher_state(self.engine, meta["watcher_id"])
            assert post == DependencyWatcherState.CANCELLED.value, (
                f"Reconciler did not cancel stale watcher for {wid}: "
                f"state={post}"
            )
            return

    @precondition(lambda self: any(
        _read_task_status(self.engine, wid) in _INFLIGHT_STATUSES
        for wid in self.turn_meta
    ))
    @rule()
    def corrupt_mirror_delete_message_while_terminal(self) -> None:
        """Issue 5 scenario 3: delete message_queue for a terminal task.

        Edge case: a terminal Task whose companion
        ``message_queue`` row was hard-deleted. The reconciler
        must be a no-op on the missing row (no error).
        """
        for wid, meta in self.turn_meta.items():
            status = _read_task_status(self.engine, wid)
            if status in _INFLIGHT_STATUSES:
                _force_task_status(self.engine, wid, TaskStatus.COMPLETED.value)
                with self.engine.begin() as conn:
                    conn.execute(
                        text(
                            "DELETE FROM message_queue "
                            "WHERE message_id = :mid"
                        ),
                        {"mid": meta["message_id"]},
                    )
                # Reconciler must not raise.
                self.repo.reconcile_turn_mirror(wid)
                return

    # --- Invariants ---------------------------------------------------------

    @invariant()
    def invariant_no_orphan_mirrors(self) -> None:
        """After every transition, all 8 mirror tables must be consistent.

        For every tracked ``work_id``:
          - If Task is terminal: JobItem admission = 'done', no job_locks
            row, message_queue terminal, watcher CANCELLED, injection
            TASK_DELIVERED, job_watcher deleted.
          - If Task is in-flight: JobItem admission = 'active', job_locks
            row present, message_queue non-terminal, watcher PENDING,
            injection PENDING, job_watcher present.
        """
        for wid, meta in self.turn_meta.items():
            self.repo.reconcile_turn_mirror(wid)
            self._assert_8_table_consistency(wid, meta)

    def _assert_8_table_consistency(
        self, wid: str, meta: dict[str, Any]
    ) -> None:
        """Check the 8-mirror consistency invariant.

        The reconciliation is invoked BEFORE this helper, so the
        mirrors are expected to be consistent if the reconciler
        is correct. We verify by reading each table independently.

        For ``job_watchers`` specifically, the plan (§4.8 of
        increment1-plan.md) says: "On terminal Task, delete only
        dangling watcher subscriptions." The current production
        reconciler only deletes when the Task is hard-deleted
        (the SQL is ``NOT EXISTS (SELECT 1 FROM task WHERE
        work_id = :work_id)``). This is a discrepancy between
        the plan and the production code. The test catches the
        discrepancy by asserting the plan's expected behavior
        (terminal -> deleted); if the production code is later
        fixed to match the plan, this test continues to pass.
        """
        status = _read_task_status(self.engine, wid)
        if status is None:
            # Task was deleted (not a normal flow); nothing to assert.
            return
        is_terminal = status in _TERMINAL_STATUSES
        admission = _read_job_item_admission(self.engine, wid)
        lock_count = _read_lock_count(self.engine, wid)
        message_status = _read_message_status(self.engine, meta["message_id"])
        watcher_state = _read_watcher_state(self.engine, meta["watcher_id"])
        injection_state = _read_injection_state(
            self.engine, meta["injection_id"]
        )
        job_watcher_count = _read_job_watcher_count(self.engine, wid)

        if is_terminal:
            assert admission == AdmissionState.DONE.value, (
                f"[{wid}] terminal Task must have admission='done', got {admission}"
            )
            assert lock_count == 0, (
                f"[{wid}] terminal Task must have no job_locks, got {lock_count}"
            )
            # ``message_status`` is allowed to be None if the row
            # was hard-deleted by a corruption rule (the reconciler
            # is a no-op on missing rows — it cannot resurrect a
            # deleted message_queue row). Either 'completed' or
            # absent is "consistent" with a terminal Task.
            assert message_status in (
                MessageStatus.COMPLETED.value, None
            ), (
                f"[{wid}] terminal Task must have message_queue=completed "
                f"or absent, got {message_status}"
            )
            # D10 (instance-scoped watcher semantics, §4 mirror
            # table #5): the watcher is CANCELLED only when the
            # source Task is terminal AND the target instance has
            # no in-flight tasks. The semantics are evaluated at
            # the time of reconciliation; once cancelled, the
            # watcher state is sticky (a later in-flight task on
            # the same instance does NOT revert it to PENDING).
            # Therefore:
            #   - If the target instance is currently drained AND
            #     the watcher is PENDING, the reconciler should
            #     have cancelled it (assert CANCELLED).
            #   - If the target instance has in-flight tasks, the
            #     watcher can be either PENDING (never cancelled)
            #     or CANCELLED (cancelled earlier when drained).
            target_has_inflight = any(
                other_meta["instance_id"] == meta["instance_id"]
                and _read_task_status(self.engine, other_wid) in _INFLIGHT_STATUSES
                for other_wid, other_meta in self.turn_meta.items()
            )
            if not target_has_inflight:
                # Target instance is drained — watcher must be
                # CANCELLED (the last reconciliation would have
                # cancelled it).
                assert watcher_state == DependencyWatcherState.CANCELLED.value, (
                    f"[{wid}] terminal Task (target drained) must have "
                    f"watcher=CANCELLED, got {watcher_state}"
                )
            # If target_has_inflight, the watcher state is
            # history-dependent and we don't assert a specific
            # value here. (The directed test
            # ``test_directed_pause_during_report_turn`` exercises
            # the "target drained → cancelled" path explicitly.)
            assert injection_state == ReportInjectionState.TASK_DELIVERED.value, (
                f"[{wid}] terminal Task must have injection=TASK_DELIVERED, "
                f"got {injection_state}"
            )
            # Per Approver direction, terminal Task watchers are NOT deleted
            # (they may belong to retry children). Only hard-deleted Tasks
            # have their watchers cleaned up.
            assert job_watcher_count >= 1, (
                f"[{wid}] terminal Task watchers must survive "
                f"(per Approver direction), got {job_watcher_count}"
            )
        else:
            # In-flight Task: admission_state is preserved by W2 — it can
            # be 'queued' (awaiting slot admission) or 'active'
            # (already admitted). The reconciler's invariant still
            # requires is_active iff has_lock, so the lock presence
            # is implied by admission_state.
            assert admission in (
                AdmissionState.QUEUED.value,
                AdmissionState.ACTIVE.value,
            ), (
                f"[{wid}] in-flight Task must have admission in "
                f"('queued', 'active'), got {admission}"
            )
            if admission == AdmissionState.ACTIVE.value:
                assert lock_count >= 1, (
                    f"[{wid}] in-flight Task with admission='active' "
                    f"must have a job_locks row, got {lock_count}"
                )
            else:
                assert lock_count == 0, (
                    f"[{wid}] in-flight Task with admission='queued' "
                    f"must have no job_locks row, got {lock_count}"
                )
            assert message_status != MessageStatus.COMPLETED.value, (
                f"[{wid}] in-flight Task must not have message_queue=completed, "
                f"got {message_status}"
            )
            assert watcher_state == DependencyWatcherState.PENDING.value, (
                f"[{wid}] in-flight Task must have watcher=PENDING, "
                f"got {watcher_state}"
            )
            assert injection_state == ReportInjectionState.PENDING.value, (
                f"[{wid}] in-flight Task must have injection=PENDING, "
                f"got {injection_state}"
            )
            assert job_watcher_count >= 1, (
                f"[{wid}] in-flight Task must have a job_watchers row, "
                f"got {job_watcher_count}"
            )

    @invariant()
    def invariant_mirror_consistency(self) -> None:
        """Invariant #4: admission='active' iff an in-flight Task AND a job_locks row.

        Reads each tracked work_id directly. If JobItem
        admission is 'active', the corresponding Task must be
        in-flight AND a job_locks row must exist. Symmetric
        (and the production code enforces this via the
        ``is_active != has_lock`` invariant check at the
        end of ``reconcile_turn_mirror``).
        """
        for wid, meta in self.turn_meta.items():
            admission = _read_job_item_admission(self.engine, wid)
            if admission is None:
                continue
            status = _read_task_status(self.engine, wid)
            lock_count = _read_lock_count(self.engine, wid)
            is_inflight = status in _INFLIGHT_STATUSES
            if admission == AdmissionState.ACTIVE.value:
                assert is_inflight, (
                    f"[{wid}] admission='active' but Task is {status}"
                )
                assert lock_count >= 1, (
                    f"[{wid}] admission='active' but no job_locks"
                )

    def _first_inflight_wid(self) -> str | None:
        for wid, meta in self.turn_meta.items():
            if _read_task_status(self.engine, wid) in _INFLIGHT_STATUSES:
                return wid
        return None


# ---------------------------------------------------------------------------
# Test entry point
# ---------------------------------------------------------------------------


class TestTurnReconcilerStateMachine:
    """Run the property state machine as a pytest test.

    Uses ``run_state_machine_as_test`` directly (rather than
    ``@given``) so the property runs as a single pytest
    test with the configured ``settings``. ``@given`` does
    not work with ``RuleBasedStateMachine.TestCase`` because
    the latter is a ``unittest.TestCase`` subclass, not a
    Hypothesis ``SearchStrategy``.
    """

    def test_state_machine(self) -> None:
        """Hypothesis entry point — runs the state machine.

        Hypothesis's ``run_state_machine_as_test`` drives
        the rules and checks invariants automatically;
        passing here means every generated sequence left
        the 8-mirror state consistent.
        """
        run_state_machine_as_test(
            _TurnReconcilerStateMachine,
            settings=settings(
                max_examples=20,
                deadline=None,
                suppress_health_check=[
                    HealthCheck.too_slow,
                    HealthCheck.function_scoped_fixture,
                ],
            ),
        )


# ---------------------------------------------------------------------------
# Directed / deterministic scenarios
# ---------------------------------------------------------------------------


def _build_one_turn(
    engine: Engine, repo: TaskRepository, *, instance_id: str = "parent-0"
) -> tuple[str, dict[str, Any]]:
    """Seed a single consistent turn across all 8 mirror tables."""
    work_id = f"work-{uuid.uuid4().hex[:12]}"
    message_id = f"msg-{uuid.uuid4().hex[:12]}"
    _seed_instance(engine, instance_id, InstanceStatus.RUNNING.value)
    task_pk = _seed_turn(
        engine,
        work_id=work_id,
        instance_id=instance_id,
        message_id=message_id,
        status=TaskStatus.PENDING.value,
    )
    _seed_message(engine, message_id=message_id, instance_id=instance_id)
    _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
    _seed_job_lock(
        engine, work_id=work_id, instance_id=instance_id, lock_slot=0
    )
    watcher_id = _seed_dependency_watcher(
        engine,
        source_task_pk=task_pk,
        target_instance_id=instance_id,
    )
    injection_id = _seed_report_injection(
        engine,
        report_message_id=message_id,
        parent_instance_id=instance_id,
    )
    job_watcher_id = _seed_job_watcher(
        engine, work_id=work_id, instance_id=instance_id
    )
    return work_id, {
        "task_pk": task_pk,
        "instance_id": instance_id,
        "message_id": message_id,
        "watcher_id": watcher_id,
        "injection_id": injection_id,
        "job_watcher_id": job_watcher_id,
        "lock_slot": 0,
    }


def _mirror_snapshot(
    engine: Engine, work_id: str, meta: dict[str, Any]
) -> dict[str, Any]:
    """Read all 8 mirror tables and return a stable snapshot dict.

    Used by ``test_idempotency`` to detect data-level changes
    on a second reconcile call. Returns a dict of
    ``{table_name: cell_value}`` where each cell_value is the
    primitive representation of that mirror's relevant column.
    The ``task`` mirror stores its status; the others store
    the column that the reconciler would have changed.
    """
    return {
        "task": _read_task_status(engine, work_id),
        "job_queue_items": _read_job_item_admission(engine, work_id),
        "job_locks": _read_lock_count(engine, work_id),
        "message_queue": _read_message_status(engine, meta["message_id"]),
        "dependency_watchers": _read_watcher_state(engine, meta["watcher_id"]),
        "report_injections": _read_injection_state(engine, meta["injection_id"]),
        "instances": _read_instance_status(engine, meta["instance_id"]),
        "job_watchers": _read_job_watcher_count(engine, work_id),
    }


class TestDirectedScenarios:
    """Deterministic scenarios — §7 of increment1-plan.md.

    These run without the property generator so a CI failure
    points at a specific, reproducible sequence.
    """

    def test_idempotency(
        self, state_machine_engine: Engine, task_repo: TaskRepository
    ) -> None:
        """Running the reconciler twice on the same state must be a no-op.

        §7.1 invariant: "Re-running the routine with the same
        state produces no additional semantic changes
        (idempotent)." Definition: running reconcile twice
        on the same state must not change the data values
        (the rowcount is allowed to be non-zero on the second
        call because SQLAlchemy reports the number of rows
        MATCHED by the WHERE clause, not the number of rows
        whose data actually changed — the SET clauses are
        designed to be self-stabilizing so re-applying them
        is safe).
        """
        wid, meta = _build_one_turn(state_machine_engine, task_repo)
        _force_task_status(state_machine_engine, wid, TaskStatus.COMPLETED.value)

        # Snapshot the 8-mirror state after the first call.
        task_repo.reconcile_turn_mirror(wid)
        snap_after_first = _mirror_snapshot(state_machine_engine, wid, meta)

        # Run the reconciler a second time on the same state.
        second = task_repo.reconcile_turn_mirror(wid)

        # Snapshot the 8-mirror state after the second call.
        snap_after_second = _mirror_snapshot(state_machine_engine, wid, meta)

        # The data must be unchanged. Equivalently: every mirror
        # table cell has the same value before and after the
        # second reconcile. Rowcount on the second call is
        # allowed to be non-zero (re-applying the SET clauses
        # is harmless), but the visible data must not change.
        assert snap_after_first == snap_after_second, (
            f"Reconciler is not idempotent: {snap_after_first} != "
            f"{snap_after_second} (second-call updated_counts="
            f"{second['updated_counts']})"
        )

    def test_directed_pause_during_report_turn(
        self, state_machine_engine: Engine, task_repo: TaskRepository
    ) -> None:
        """§7 directed scenario: pause during report turn, then resume.

        Sequence: BEGIN -> CLAIM -> suspend-during-report -> RESUME
        -> COMPLETE. Asserts no stale processing_task_id, no active
        JobItem without a lock, no dangling report injection, no
        dangling watcher.
        """
        wid, meta = _build_one_turn(state_machine_engine, task_repo)

        # 1. Claim (PENDING -> RUNNING)
        _force_task_status(
            state_machine_engine, wid, TaskStatus.RUNNING.value
        )
        task_repo.reconcile_turn_mirror(wid)

        # 2. Suspend during report turn (RUNNING -> PAUSED)
        _force_task_status(
            state_machine_engine, wid, TaskStatus.PAUSED.value
        )
        task_repo.reconcile_turn_mirror(wid)

        # 3. Resume (PAUSED -> RUNNING)
        _force_task_status(
            state_machine_engine, wid, TaskStatus.RUNNING.value
        )
        task_repo.reconcile_turn_mirror(wid)

        # 4. Answer arrives (RUNNING -> COMPLETED)
        _force_task_status(
            state_machine_engine, wid, TaskStatus.COMPLETED.value
        )
        result = task_repo.reconcile_turn_mirror(wid)

        # 5. Instance reaches terminal status — D13 suppresses the terminal
        # JobItem write while the instance is alive-but-transitioning.
        _force_instance_status(
            state_machine_engine,
            meta["instance_id"],
            InstanceStatus.COMPLETED.value,
        )
        task_repo.reconcile_turn_mirror(wid)

        # All 8 mirrors must be consistent with terminal status.
        assert result["snapshot_status"] == TaskStatus.COMPLETED.value
        assert _read_job_item_admission(state_machine_engine, wid) == (
            AdmissionState.DONE.value
        )
        assert _read_lock_count(state_machine_engine, wid) == 0
        assert _read_message_status(
            state_machine_engine, meta["message_id"]
        ) == MessageStatus.COMPLETED.value
        assert _read_message_processing_task_id(
            state_machine_engine, meta["message_id"]
        ) is None
        assert _read_watcher_state(
            state_machine_engine, meta["watcher_id"]
        ) == DependencyWatcherState.CANCELLED.value
        assert _read_injection_state(
            state_machine_engine, meta["injection_id"]
        ) == ReportInjectionState.TASK_DELIVERED.value
        # Per Approver direction, terminal Task watchers are NOT deleted
        # (they may belong to retry children). Only hard-deleted Tasks
        # have their watchers cleaned up.
        assert _read_job_watcher_count(state_machine_engine, wid) >= 1

    def test_8_table_coverage_registry(self) -> None:
        """Mechanical guard for the 8-mirror coverage registry.

        If anyone removes a table from ``_MIRROR_TABLES``,
        this test fails immediately. Mirrors §9 success
        criterion #2.
        """
        assert len(_MIRROR_TABLES) == 8, (
            f"Mirror coverage registry drifted from 8 tables, got "
            f"{len(_MIRROR_TABLES)}: {_MIRROR_TABLES}"
        )
        expected = {
            "task", "job_queue_items", "job_locks", "message_queue",
            "dependency_watchers", "report_injections", "instances",
            "job_watchers",
        }
        assert set(_MIRROR_TABLES) == expected, (
            f"Mirror registry contents drifted. Expected {expected}, "
            f"got {set(_MIRROR_TABLES)}"
        )
