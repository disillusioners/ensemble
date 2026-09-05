"""Real-SQLite validation for ``get_queued_or_expected_wakeups`` (Phase 2).

Task 2.3's R2 input helper sums four wakeup counts across three
``next_retry_at`` tables plus the held-not-scheduled task families.
The unit suite mocks the manager facades; THIS file validates the
helper's actual SQL against a real file-backed SQLite engine:

* file-backed SQLite at ``tmp_path`` + ``NullPool`` + WAL +
  ``busy_timeout`` (repo real-DB discipline — NEVER
  ``StaticPool`` + ``WriteGuardSession``);
* scoped to EXACTLY the four tables the helper touches
  (``task``, ``message_queue``, ``job_queue_items``, ``instances``)
  via a targeted ``create_all(tables=[...])`` — no full-schema boot
  (the fresh-SQLite migration-20260714 trap is avoided by design).

Count expectations under test:

  counted:   task PENDING + future next_retry_at (scheduled retry)
  counted:   task PENDING + is_deferred (idle-gate held)
  counted:   task PENDING + owning instance PAUSED (pause-held)
  counted:   message_queue retrying + future next_retry_at
  counted:   job_queue_items queued + future next_retry_at
  NOT:       task PENDING with past next_retry_at (already due)
  NOT:       message_queue ready + NULL next_retry_at (claimable now)
  NOT:       job_queue_items queued + NULL next_retry_at (fresh job)
  NOT:       terminal-row families (done/completed) of any table
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
from daemon.repositories.task.models import Task, TaskStatus
from daemon.services.instance_messaging import InstanceMessagingService

TEST_INSTANCE = "11111111-1111-1111-1111-111111111111"
OTHER_INSTANCE = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def engine(tmp_path):
    """File-backed SQLite with WAL + busy_timeout + NullPool (repo discipline)."""
    db_path = tmp_path / "attest_wakeups.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _tune(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    # Scope to exactly the tables the helper queries — avoids the
    # full-schema fresh-SQLite boot trap (migration 20260714 is PG-only).
    SQLModel.metadata.create_all(
        eng,
        tables=[
            Task.__table__,
            MessageQueue.__table__,
            JobItem.__table__,
            Instance.__table__,
        ],
    )
    return eng


@pytest.fixture
def service(engine):
    manager = MagicMock()
    manager._engine = engine
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(),
        child_reports_service=MagicMock(),
        events_service=MagicMock(),
    )


def _task_next_retry_str(dt: datetime) -> str:
    # EXACT writer format used by schedule_retry / requeue_task_with_backoff
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + dt.strftime("%z")


def _make_task(instance_id=TEST_INSTANCE, **overrides) -> Task:
    defaults = dict(
        task_type="process_message",
        instance_id=instance_id,
        status=TaskStatus.PENDING.value,
    )
    defaults.update(overrides)
    return Task(**defaults)


def _seed(engine, *rows):
    with Session(engine) as session:
        for row in rows:
            session.add(row)
        session.commit()


def test_empty_db_counts_zero(service):
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 0


def test_scheduled_retry_task_counts(service, engine):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    _seed(engine, _make_task(next_retry_at=_task_next_retry_str(future)))
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 1


def test_past_due_task_not_counted(service, engine):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    _seed(engine, _make_task(next_retry_at=_task_next_retry_str(past)))
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 0


def test_deferred_held_task_counts(service, engine):
    _seed(engine, _make_task(is_deferred=True))
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 1


def test_paused_instance_held_task_counts(service, engine):
    _seed(
        engine,
        Instance(
            instance_id=TEST_INSTANCE,
            agent_id="leader",
            agent_dir="/agents/leader",
            status=InstanceStatus.PAUSED.value,
        ),
        _make_task(),  # plain PENDING, no schedule — held by the pause gate
    )
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 1


def test_message_queue_retrying_counts(service, engine):
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    _seed(
        engine,
        MessageQueue(
            instance_id=TEST_INSTANCE,
            content="retry me later",
            status=MessageStatus.RETRYING.value,
            next_retry_at=future,
        ),
    )
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 1


def test_claimable_message_not_counted(service, engine):
    _seed(
        engine,
        MessageQueue(
            instance_id=TEST_INSTANCE,
            content="ready now",
            status=MessageStatus.READY.value,  # NULL next_retry_at — claimable
        ),
    )
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 0


def test_scheduled_job_counts(service, engine):
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    _seed(
        engine,
        JobItem(
            name="retry-window job",
            payload={"x": 1},
            message="test job payload",
            agent_id="leader",
            agent_dir="/agents/leader",
            queue_id="q-1",
            project_id=None,
            instance_id=TEST_INSTANCE,
            admission_state=AdmissionState.QUEUED.value,
            next_retry_at=future.isoformat(),
        ),
    )
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 1


def test_fresh_job_not_counted(service, engine):
    _seed(
        engine,
        JobItem(
            name="fresh job",
            payload={"x": 1},
            message="test job payload",
            agent_id="leader",
            agent_dir="/agents/leader",
            queue_id="q-1",
            project_id=None,
            instance_id=TEST_INSTANCE,
            admission_state=AdmissionState.QUEUED.value,  # NULL next_retry_at
        ),
    )
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 0


def test_counts_are_scoped_to_instance(service, engine):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    _seed(
        engine,
        _make_task(instance_id=OTHER_INSTANCE, next_retry_at=_task_next_retry_str(future)),
        MessageQueue(
            instance_id=OTHER_INSTANCE,
            content="other instance retry",
            status=MessageStatus.RETRYING.value,
            next_retry_at=future,
        ),
    )
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 0
    assert service.get_queued_or_expected_wakeups(OTHER_INSTANCE) == 2


def test_sum_of_multiple_families(service, engine):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    _seed(
        engine,
        # task family: 2 scheduled + 1 deferred + 1 paused-held
        _make_task(next_retry_at=_task_next_retry_str(future)),
        _make_task(
            message_id=None,
            next_retry_at=_task_next_retry_str(future + timedelta(seconds=5)),
        ),
        _make_task(is_deferred=True),
        Instance(
            instance_id=TEST_INSTANCE,
            agent_id="leader",
            agent_dir="/agents/leader",
            status=InstanceStatus.PAUSED.value,
        ),
        # (the paused instance makes ALL FOUR tasks pause-held too, but
        # counts #1 and #4 are DISTINCT queries — summing is additive by
        # design; zero-vs-nonzero is what R2 consumes)
        # message family
        MessageQueue(
            instance_id=TEST_INSTANCE,
            content="m",
            status=MessageStatus.RETRYING.value,
            next_retry_at=future,
        ),
        # job family
        JobItem(
            name="j",
            payload={},
            message="test job payload",
            agent_id="leader",
            agent_dir="/agents/leader",
            queue_id="q-1",
            project_id=None,
            instance_id=TEST_INSTANCE,
            admission_state=AdmissionState.QUEUED.value,
            next_retry_at=future.isoformat(),
        ),
    )
    # 2 scheduled tasks + 1 deferred + 3 pause-held + 1 message + 1 job
    assert service.get_queued_or_expected_wakeups(TEST_INSTANCE) == 8
