"""Unit tests for ``clear_task_auto_continued`` (F-1, plan §13b).

Feature: durability-f1-f2 / F-1 / Phase 1 (2026-10-04).

The ``clear_task_auto_continued`` repository method closes the
arm-3 ``auto_continued_at`` marker leak: at the terminalizer
call site (``daemon/manager.py:11660-11721``, gate conjunct
``:11700-11702``), AFTER a successful ``complete_task``, the
marker is unconditionally cleared so the arm-3
``EXISTS instances`` co-condition becomes the lifetime bound
(per §13b's composition of (i) + (ii)).

The repository method is a single-statement atomic UPDATE:

    UPDATE task
    SET auto_continued_at = NULL
    WHERE id = :task_id
      AND status = 'completed'

The ``status='completed'`` guard makes the clear a no-op on
still-running rows (the marker stays so the next wipe preserves
the row when the instance is non-terminal — the intended
behavior). The kill-switch ``ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE``
does NOT gate this clear: the wipe-side arm 3 is the operator's
opt-out; the marker-clearing is lifecycle hygiene.

Harness mirrors ``tests/unit/repositories/test_auto_continue_candidates.py``:
REAL file-backed SQLite (``tmp_path`` + ``NullPool`` — deliberately
NOT ``StaticPool``/``:memory:``, mirrors production concurrency).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401 — register
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.timestamps import now_utc_naive


# ---------------------------------------------------------------------------
# Fixtures — file-backed SQLite (F9 parity pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """REAL SQLite FILE database, per-connection PRAGMAs, NullPool."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/task_auto_continued_lifecycle.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_instance(
    eng: Engine, instance_id: str, status: str = InstanceStatus.RUNNING.value
) -> None:
    """Insert a minimal instance row with the given status."""
    with Session(eng) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                status=status,
            )
        )
        s.commit()


def _seed_task(
    eng: Engine,
    instance_id: str,
    *,
    task_id: int | None = None,
    status: str = TaskStatus.RUNNING.value,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    auto_continued_at: datetime | None = None,
    work_id: str | None = None,
) -> int:
    """Insert a Task row. Returns its primary key."""
    import uuid
    with Session(eng) as s:
        t = Task(
            id=task_id,
            work_id=work_id or f"work-{uuid.uuid4().hex[:12]}",
            task_type=task_type,
            instance_id=instance_id,
            status=status,
            auto_continued_at=auto_continued_at,
            created_at=now_utc_naive(),
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _boot_epoch() -> datetime:
    return now_utc_naive()


# ---------------------------------------------------------------------------
# TestClearTaskAutoContinued — single-statement clear, ``status='completed'`` guard
# ---------------------------------------------------------------------------


class TestClearTaskAutoContinued:
    """F-1 §13b marker-clearing — single-statement atomic UPDATE,
    ``rowcount == 1`` contract, ``status='completed'`` guard."""

    def test_clears_marker_on_completed_task_returns_true(
        self, engine: Engine
    ) -> None:
        """Clearing the marker on a COMPLETED row returns True and
        the marker is NULL."""
        _seed_instance(engine, "inst-CLR-OK")
        stamped = _boot_epoch() - timedelta(seconds=10)
        tid = _seed_task(
            engine,
            "inst-CLR-OK",
            status=TaskStatus.COMPLETED.value,
            auto_continued_at=stamped,
        )
        repo = TaskRepository(engine)

        ok = repo.clear_task_auto_continued(tid)

        assert ok is True
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at is None
            # Status is unchanged (the clear is a single-column write).
            assert row.status == TaskStatus.COMPLETED.value

    def test_does_not_clear_when_status_still_running_returns_false(
        self, engine: Engine
    ) -> None:
        """The ``status='completed'`` guard declines a still-RUNNING row.

        The marker STAYS so the next wipe preserves the row when
        the instance is non-terminal — the intended behavior.
        Returns False (rowcount=0).
        """
        _seed_instance(engine, "inst-CLR-RUN")
        stamped = _boot_epoch() - timedelta(seconds=10)
        tid = _seed_task(
            engine,
            "inst-CLR-RUN",
            status=TaskStatus.RUNNING.value,
            auto_continued_at=stamped,
        )
        repo = TaskRepository(engine)

        ok = repo.clear_task_auto_continued(tid)

        assert ok is False
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at == stamped  # unchanged

    def test_does_not_clear_when_status_pending_returns_false(
        self, engine: Engine
    ) -> None:
        """The ``status='completed'`` guard declines a PENDING row."""
        _seed_instance(engine, "inst-CLR-PEND")
        stamped = _boot_epoch() - timedelta(seconds=10)
        tid = _seed_task(
            engine,
            "inst-CLR-PEND",
            status=TaskStatus.PENDING.value,
            auto_continued_at=stamped,
        )
        repo = TaskRepository(engine)

        ok = repo.clear_task_auto_continued(tid)

        assert ok is False
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at == stamped  # unchanged

    def test_does_not_clear_when_status_failed_returns_false(
        self, engine: Engine
    ) -> None:
        """The ``status='completed'`` guard declines a FAILED row.

        Only COMPLETED is the terminalizer's reach: a FAILED row
        did not pass through the resume-success terminalizer path.
        """
        _seed_instance(engine, "inst-CLR-FAIL")
        stamped = _boot_epoch() - timedelta(seconds=10)
        tid = _seed_task(
            engine,
            "inst-CLR-FAIL",
            status=TaskStatus.FAILED.value,
            auto_continued_at=stamped,
        )
        repo = TaskRepository(engine)

        ok = repo.clear_task_auto_continued(tid)

        assert ok is False
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at == stamped  # unchanged

    def test_does_not_clear_when_task_id_missing(self, engine: Engine) -> None:
        """Clearing a non-existent task_id returns False (rowcount=0)."""
        repo = TaskRepository(engine)

        ok = repo.clear_task_auto_continued(999_999)

        assert ok is False

    def test_clear_already_null_marker_on_completed_returns_true(
        self, engine: Engine
    ) -> None:
        """Clearing a NULL marker on a COMPLETED row returns True.

        The ``status='completed'`` guard still matches → rowcount=1.
        Idempotent: a second clear is also True.
        """
        _seed_instance(engine, "inst-CLR-NULL")
        tid = _seed_task(
            engine,
            "inst-CLR-NULL",
            status=TaskStatus.COMPLETED.value,
            auto_continued_at=None,
        )
        repo = TaskRepository(engine)

        ok = repo.clear_task_auto_continued(tid)

        assert ok is True
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at is None

        # Idempotent — second clear also True.
        again = repo.clear_task_auto_continued(tid)
        assert again is True


class TestClearLifecyleComposition:
    """F-1 §13b composition: clear + (i) ``EXISTS instances`` co-condition.

    The two options are designed to be complementary — option (i)
    is the wipe-side bound; option (ii) is the terminalizer-side
    bound. The test pins the composition behavior:

      * A COMPLETED task with a stamped marker is cleared (option
        (ii) does its job) regardless of instance status.
      * A RUNNING task with a stamped marker is NOT cleared
        (option (ii) defers) — option (i) is the bound (instance
        status; tested on the predicate side in
        ``test_discard_on_startup_dependency_bus_race.py``).
    """

    def test_clear_overrides_even_when_instance_terminal(
        self, engine: Engine
    ) -> None:
        """Clearing fires regardless of instance status (the clear
        is lifecycle hygiene, not instance-status-bounded)."""
        # Terminal instance + stamped + COMPLETED task → clear succeeds.
        _seed_instance(engine, "inst-TERM", status=InstanceStatus.COMPLETED.value)
        stamped = _boot_epoch() - timedelta(seconds=10)
        tid = _seed_task(
            engine,
            "inst-TERM",
            status=TaskStatus.COMPLETED.value,
            auto_continued_at=stamped,
        )
        repo = TaskRepository(engine)

        ok = repo.clear_task_auto_continued(tid)

        assert ok is True
        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at is None

    def test_clear_marker_composition_with_stamp(
        self, engine: Engine
    ) -> None:
        """Composition: ``mark_task_auto_continued`` stamps, then
        ``clear_task_auto_continued`` clears (mirror of the
        terminalizer call site's two-step)."""
        _seed_instance(engine, "inst-CMP")
        tid = _seed_task(
            engine,
            "inst-CMP",
            status=TaskStatus.RUNNING.value,
        )
        repo = TaskRepository(engine)
        boot = _boot_epoch()

        # 1. Boot pass CAS-stamps the row (transition to running →
        # stamp; status guard in the stamp method accepts running).
        assert repo.mark_task_auto_continued(tid, boot) is True

        # 2. Resume succeeds → task terminalizes to COMPLETED.
        with Session(engine) as s:
            row = s.get(Task, tid)
            row.status = TaskStatus.COMPLETED.value
            s.add(row)
            s.commit()

        # 3. Terminalizer call site clears the marker.
        assert repo.clear_task_auto_continued(tid) is True

        with Session(engine) as s:
            row = s.get(Task, tid)
            assert row.auto_continued_at is None
            assert row.status == TaskStatus.COMPLETED.value
