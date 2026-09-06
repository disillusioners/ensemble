"""Regression pin: ``TaskRepository.batch_reconcile_bad_state_tasks`` must
return the actual reconciled count and NOT raise on its post-commit log.

Background
----------
The post-commit ``logger.info(...)`` call inside
:meth:`TaskRepository.batch_reconcile_bad_state_tasks` originally passed
structlog-style kwargs (``count=``, ``queue_id=``, ``project_id=``) to a
stdlib logger. stdlib ``Logger._log`` only accepts
``exc_info``/``stack_info``/``stacklevel``/``extra`` as kwargs — anything
else raises ``TypeError: Logger._log() got an unexpected keyword argument
'count'``.

The DB UPDATE inside the function commits BEFORE the logger runs, so the
reconcile effect lands; the TypeError then escapes and the caller
(``JobQueueService.cleanup_non_terminal_jobs`` bucket 4) catches it via its
``except Exception as exc:`` handler and reports
``reconciled_bad_state = 0`` — masking the actual transition count during
the 2026-09-06 ops unstick.

Quarantine note
---------------
The sibling 6-node family in ``tests/unit/test_task_reconciliation.py``
(``test_reconcile_paused_task_with_done_jobitem``,
``test_reconcile_pending_task_with_dead_jobitem``, and the four
``test_batch_reconcile_bad_state_tasks_*`` cases) is QUARANTINE-listed for
this exact defect — they pass under pytest's default WARNING-level log
capture because the buggy code path is short-circuited at module level
before the kwargs reach ``Logger._log``. They stay green even on the
unfixed code; this pin forces the code path to execute by setting the
logger to INFO, so it fails on the unfixed code and passes on the fix.

Recipe
------
File-backed SQLite (per the project's Testing & QC Conventions — StaticPool
+ WriteGuardSession is FORBIDDEN). NullPool + ``PRAGMA journal_mode=WAL``
+ ``PRAGMA busy_timeout=10000`` applied via a connect-event listener.
"""

import logging
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository

#: The module logger that holds the buggy post-commit call. Pin test forces
#: this to INFO so the buggy kwargs actually reach ``Logger._log`` (under
#: default pytest WARNING-level capture the bug is short-circuited).
TASK_REPO_LOGGER_NAME = "daemon.repositories.task.repository"


@pytest.fixture
def file_sqlite_engine(tmp_path):
    """File-backed SQLite: NullPool + WAL + busy_timeout=10000."""
    db_path = tmp_path / "task_repo_logging_pin.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def forced_info_logger():
    """Force ``daemon.repositories.task.repository`` to INFO so the buggy
    post-commit ``logger.info(..., count=...)`` call site actually executes.

    Restores the prior level on teardown so this fixture is hermetic.
    """
    log = logging.getLogger(TASK_REPO_LOGGER_NAME)
    original_level = log.level
    log.setLevel(logging.INFO)
    try:
        yield log
    finally:
        log.setLevel(original_level)


def _seed_instance(session: Session, iid: str) -> None:
    session.add(
        Instance(
            instance_id=iid,
            agent_id="developer",
            agent_dir="/tmp",
            project_id="p",
            status=InstanceStatus.IDLE.value,
            version=1,
            instance_metadata={},
        )
    )


def _seed_jobitem(
    session: Session,
    work_id: str,
    iid: str,
    admission_state: str,
    queue_id: str = "system_parallel_queue",
) -> None:
    session.add(
        JobItem(
            job_id=work_id,
            agent_id="developer",
            agent_dir="/tmp",
            message="msg",
            source="api",
            project_id="p",
            priority=5,
            job_metadata={},
            queue_id=queue_id,
            job_type="task",
            instance_id=iid,
            admission_state=admission_state,
        )
    )


def _seed_task(session: Session, work_id: str, iid: str, status: str) -> None:
    session.add(
        Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=iid,
            message_id=str(uuid.uuid4()),
            status=status,
            work_id=work_id,
            created_at=datetime.now(timezone.utc),
        )
    )


def _get_task_status(engine, work_id: str) -> str:
    with Session(engine) as session:
        task = session.exec(
            select(Task).where(Task.work_id == work_id)
        ).scalar_one()
        return task.status


def test_batch_reconcile_bad_state_tasks_returns_count_without_logging_crash(
    file_sqlite_engine,
    forced_info_logger,
):
    """Pin: the post-commit log must not raise; the returned count must
    reflect the ACTUAL number of Tasks transitioned to CANCELLED.

    On the unfixed code, this test fails with
    ``TypeError: Logger._log() got an unexpected keyword argument 'count'``
    propagating out of ``batch_reconcile_bad_state_tasks`` BEFORE the
    caller's ``reconciled_bad_state`` variable can be assigned — exactly the
    2026-09-06 ops-untstick symptom.
    """
    engine = file_sqlite_engine
    iid = f"inst-{uuid.uuid4()}"
    expected_count = 3
    work_ids: list[str] = []

    # Seed N bad-state Tasks (paused + linked done JobItem). The
    # ``AND EXISTS`` subquery in the reconcile UPDATE matches all of them.
    with Session(engine) as session:
        _seed_instance(session, iid)
        for _ in range(expected_count):
            wid = f"work-{uuid.uuid4()}"
            work_ids.append(wid)
            _seed_jobitem(session, wid, iid, AdmissionState.DONE.value)
            _seed_task(session, wid, iid, TaskStatus.PAUSED.value)
        session.commit()

    repo = TaskRepository(engine)

    # The pin. Buggy version: raises TypeError inside logger.info(..., count=).
    # Fixed version: returns the integer rowcount cleanly.
    count = repo.batch_reconcile_bad_state_tasks()

    assert count == expected_count, (
        f"reconcile reported {count}, expected {expected_count} — the post-commit "
        f"logger crashed before the count reached the caller (see "
        f".agents/tester/LESSONS/2026-09-05-stdlib-logger-kwargs-latent-crash.md)"
    )
    # All bad-state rows must have actually transitioned (DB effect).
    for wid in work_ids:
        assert _get_task_status(engine, wid) == TaskStatus.CANCELLED.value


def test_batch_reconcile_bad_state_tasks_idempotent_under_info_logging(
    file_sqlite_engine,
    forced_info_logger,
):
    """Second call under INFO-level logging returns 0 without raising.

    If the post-commit logger crashed the FIRST time, the second call would
    hit the same crash with ``count == 0`` — but only if ``count > 0`` enters
    the ``if`` branch does the logger run, so this test guards the happy
    idempotent path AND the buggy edge case where the second call somehow
    re-enters the logger branch.
    """
    engine = file_sqlite_engine
    iid = f"inst-{uuid.uuid4()}"
    work_id = f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        _seed_jobitem(session, work_id, iid, AdmissionState.DONE.value)
        _seed_task(session, work_id, iid, TaskStatus.PAUSED.value)
        session.commit()

    repo = TaskRepository(engine)
    first = repo.batch_reconcile_bad_state_tasks()
    second = repo.batch_reconcile_bad_state_tasks()

    assert first == 1
    assert second == 0
    assert _get_task_status(engine, work_id) == TaskStatus.CANCELLED.value
