"""Integration: exactly-once under the wake-vs-claim race — Debug Phase 4
fix #1 (terminal-report wake).

With the PROCESS_REPORT wake lane (``claim_pending_task`` claims the
report task ahead of older FIFO work), a report can now be claimed by
the wake path very shortly after the child's terminal emission. The
invariant that must hold: **exactly-once delivery** — the wake path
and the natural claim/drain paths may race, but the report is
delivered ONCE, never zero, never twice. Composition (never bypass):

* ``TaskRepository.claim_pending_task`` — atomic
  ``UPDATE ... RETURNING``: each task goes to exactly one worker.
* ``ReportInjectionRepository.claim_for_task_delivery`` — guarded
  ``WHERE state = 'PENDING'`` flip to ``TASK_DELIVERED``.
* ``ReportInjectionRepository.claim_for_injection`` (the parent's live
  agent-node drain) — guarded ``WHERE state = 'PENDING'`` flip to
  ``INJECTED`` with ``UPDATE ... RETURNING``.
* ``uq_report_injections_oblig_triple`` — partial unique index:
  write-once delivery obligation per (parent, child, child_message_id).

Pinned scenarios (real repositories, real threads, file-backed SQLite
at ``tmp_path`` + NullPool + WAL + busy_timeout=10000 per project
Testing & QC conventions):

1. Wake-lane claim vs natural FIFO claim of the SAME report task →
   exactly one worker wins the task; the loser claims nothing.
2. Wake path (task delivery claim) vs the parent's live drain →
   exactly ONE terminal transition; the row ends INJECTED or
   TASK_DELIVERED, never PENDING, never both; the loser observes
   ``already_delivered`` / empty drain.
3. Double ``claim_for_task_delivery`` → one ``claimed`` +
   one ``already_delivered``; single ``delivered_at``.
4. The delivery obligation is write-once: a second
   (parent, child, child_message_id) triple for the same report is
   rejected by the partial unique index.

Run with::

    pytest tests/integration/test_wake_vs_claim_exactly_once.py -v
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select as sm_select

import daemon.repositories.dependency_bus.models  # noqa: F401 — table registration
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository

PARENT_ID = "inst-parent"
CHILD_ID = "inst-child"
CHILD_MESSAGE_ID = "child-msg-1"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite: NullPool + WAL + busy_timeout=10000.

    NullPool gives every thread a fresh connection (no cross-thread
    session sharing); WAL + busy_timeout serialize the concurrent
    writers the scenarios below spawn.
    """
    db_path = tmp_path / "wake_vs_claim_exactly_once.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine=engine)


@pytest.fixture
def injection_repo(engine: Engine) -> ReportInjectionRepository:
    return ReportInjectionRepository(engine=engine)


# ─── Seed helpers (the exact triple child_reports commits) ──────────────────


def _seed_instances(engine: Engine) -> None:
    for iid, status in (
        (PARENT_ID, InstanceStatus.RUNNING.value),
        (CHILD_ID, InstanceStatus.COMPLETED.value),
    ):
        with Session(engine) as s:
            s.add(
                Instance(
                    instance_id=iid,
                    agent_id="developer",
                    agent_dir="/tmp/agents/developer",
                    agent_name="developer",
                    status=status,
                )
            )
            s.commit()


def _seed_report_triple(
    engine: Engine,
    *,
    report_message_id: str = "report-msg-1",
    child_message_id: str = CHILD_MESSAGE_ID,
) -> int:
    """Commit the crash-consistent triple exactly as
    ``child_reports._process_child_completion_db_sync`` does:
    completion_report MessageQueue row + PROCESS_REPORT Task +
    report_injections PENDING row. Returns the task id."""
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(
            MessageQueue(
                message_id=report_message_id,
                instance_id=PARENT_ID,
                content="child report content",
                source=f"internal_report:{CHILD_ID}:{child_message_id}",
                type=MessageType.COMPLETION_REPORT.value,
                status=MessageStatus.READY.value,
                priority=0,
                enqueued_at=now,
            )
        )
        task = Task(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=PARENT_ID,
            message_id=report_message_id,
            status=TaskStatus.PENDING.value,
            created_at=now,
        )
        s.add(task)
        s.add(
            ReportInjection(
                parent_instance_id=PARENT_ID,
                child_instance_id=CHILD_ID,
                child_message_id=child_message_id,
                report_message_id=report_message_id,
                content="child report content",
                state=ReportInjectionState.PENDING.value,
                created_at=now.isoformat(),
            )
        )
        s.commit()
        s.refresh(task)
        return int(task.id)


def _injection_row(engine: Engine, report_message_id: str) -> ReportInjection:
    with Session(engine) as s:
        row = s.exec(
            sm_select(ReportInjection).where(
                ReportInjection.report_message_id == report_message_id
            )
        ).first()
        assert row is not None
        return row


def _run_pair(fn_a, fn_b) -> tuple:
    """Run two callables concurrently behind a shared barrier and return
    their results. The guarded SQL makes the outcome deterministic
    regardless of interleaving — that IS the property under test."""
    barrier = threading.Barrier(2, timeout=10)
    results: list = [None, None]
    errors: list = [None, None]

    def _worker(idx: int, fn):
        try:
            barrier.wait()
            results[idx] = fn()
        except Exception as exc:  # pragma: no cover — surfaced below
            errors[idx] = exc

    t_a = threading.Thread(target=_worker, args=(0, fn_a))
    t_b = threading.Thread(target=_worker, args=(1, fn_b))
    t_a.start()
    t_b.start()
    t_a.join(timeout=15)
    t_b.join(timeout=15)
    assert errors == [None, None], f"race workers raised: {errors}"
    return results[0], results[1]


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestWakeVsClaimExactlyOnce:
    def test_wake_lane_claim_vs_natural_claim_single_winner(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """Race 1 — the wake path and a natural FIFO competitor both
        attempt to claim the SAME report task: exactly one worker wins;
        the other gets nothing. Never zero (one always wins), never two
        workers holding one task."""
        _seed_instances(engine)
        report_task_id = _seed_report_triple(engine)

        r_wake, r_natural = _run_pair(
            lambda: task_repo.claim_pending_task(worker_id="worker-wake"),
            lambda: task_repo.claim_pending_task(worker_id="worker-natural"),
        )

        winners = [r for r in (r_wake, r_natural) if r is not None]
        assert len(winners) == 1, (
            "exactly one claim attempt may win the report task — "
            f"got {[w.id for w in winners]}"
        )
        assert winners[0].id == report_task_id
        assert winners[0].task_type == TaskType.PROCESS_REPORT.value
        assert winners[0].status == TaskStatus.RUNNING.value
        loser_worker = (
            "worker-natural" if winners[0].worker_id == "worker-wake"
            else "worker-wake"
        )
        assert winners[0].worker_id != loser_worker

    def test_task_delivery_vs_live_drain_exactly_one_terminal_transition(
        self, engine: Engine, task_repo: TaskRepository, injection_repo
    ):
        """Race 2 — THE wake-vs-claim race: the wake path claims the task
        and flips the report_injections row PENDING→TASK_DELIVERED while
        the parent's live graph turn concurrently drains the same row
        PENDING→INJECTED. Exactly ONE transition wins; the loser
        observes the dedup signal (``already_delivered`` / empty drain).
        The report is delivered exactly once."""
        _seed_instances(engine)
        _seed_report_triple(engine)

        def _wake_delivery():
            """The wake path: task claimed (assertion-only), then the
            task-delivery claim the ProcessMessageProcessor performs."""
            claimed_task = task_repo.claim_pending_task(worker_id="worker-wake")
            claim = injection_repo.claim_for_task_delivery("report-msg-1")
            return claimed_task, claim

        def _live_drain():
            """The parent's live agent-node drain."""
            return injection_repo.claim_for_injection(PARENT_ID)

        (claimed_task, delivery_claim), drained = _run_pair(
            _wake_delivery, _live_drain
        )

        row = _injection_row(engine, "report-msg-1")

        # Exactly one terminal transition — never PENDING, never both.
        assert row.state in (
            ReportInjectionState.TASK_DELIVERED.value,
            ReportInjectionState.INJECTED.value,
        ), f"row must be terminal exactly once, got {row.state}"
        assert row.delivered_at is not None

        # The winner's contract holds; the loser saw the dedup signal.
        if row.state == ReportInjectionState.TASK_DELIVERED.value:
            assert delivery_claim.status == "claimed"
            assert drained == [], (
                "the live drain must come back EMPTY when the wake path "
                "won the row (exactly-once, never double-delivered)"
            )
        else:
            assert len(drained) == 1
            assert drained[0]["report_message_id"] == "report-msg-1"
            assert delivery_claim.status == "already_delivered", (
                "the task-delivery claim must observe already_delivered "
                "when the live drain won (the ProcessMessageProcessor "
                "skip gate)"
            )
            # The skip gate path: the task is marked completed-skipped,
            # NOT re-delivered — asserted implicitly by the row being
            # INJECTED with a single terminal state.

        # Companion message row: COMPLETED exactly when the drain won
        # (the drain's guarded status='ready' hygiene UPDATE).
        with Session(engine) as s:
            msg = s.get(MessageQueue, "report-msg-1")
            if row.state == ReportInjectionState.INJECTED.value:
                assert msg.status == MessageStatus.COMPLETED.value
            else:
                assert msg.status == MessageStatus.READY.value, (
                    "the task path does not touch the companion row's "
                    "status at claim time — the pipeline owns it"
                )

    def test_double_task_delivery_claim_exactly_once(
        self, engine: Engine, injection_repo
    ):
        """Race 3 — two concurrent task-delivery claims (e.g. the task
        re-claimed after a worker crash while recovery races it): one
        ``claimed``, one ``already_delivered``; one row; one
        ``delivered_at``."""
        _seed_instances(engine)
        _seed_report_triple(engine)

        claim_a, claim_b = _run_pair(
            lambda: injection_repo.claim_for_task_delivery("report-msg-1"),
            lambda: injection_repo.claim_for_task_delivery("report-msg-1"),
        )

        statuses = sorted([claim_a.status, claim_b.status])
        assert statuses == ["already_delivered", "claimed"], (
            f"exactly one claim may win: got {statuses}"
        )
        winner = claim_a if claim_a.status == "claimed" else claim_b
        assert winner.row is not None
        assert winner.row.state == ReportInjectionState.TASK_DELIVERED.value

        row = _injection_row(engine, "report-msg-1")
        assert row.state == ReportInjectionState.TASK_DELIVERED.value
        assert row.delivered_at is not None

    def test_delivery_obligation_is_write_once(
        self, engine: Engine
    ):
        """Race 4 — the write-once obligation: a second
        report_injections row for the same (parent, child,
        child_message_id) triple is rejected by the partial unique index
        ``uq_report_injections_oblig_triple`` while the row is still an
        outstanding obligation (PENDING)."""
        _seed_instances(engine)
        _seed_report_triple(engine)

        dup = ReportInjection(
            parent_instance_id=PARENT_ID,
            child_instance_id=CHILD_ID,
            child_message_id=CHILD_MESSAGE_ID,
            report_message_id="report-msg-2",
            content="duplicate obligation",
            state=ReportInjectionState.PENDING.value,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises(Exception) as excinfo:
            with Session(engine) as s:
                s.add(dup)
                s.commit()
        # Driver-tolerant: SQLite reports "UNIQUE constraint failed:
        # report_injections.<col>, ..."; PostgreSQL reports the index
        # name. Both prove the write-once obligation held.
        err = str(excinfo.value)
        assert (
            "uq_report_injections_oblig_triple" in err
            or (
                "UNIQUE constraint failed" in err
                and "parent_instance_id" in err
                and "child_instance_id" in err
                and "child_message_id" in err
            )
        ), (
            "the partial unique index must reject a second outstanding "
            f"obligation for the same (parent, child, child_message_id): {err}"
        )
