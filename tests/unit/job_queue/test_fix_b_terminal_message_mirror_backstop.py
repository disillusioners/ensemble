"""Round-2 regression tests for the F-1 terminal-message-mirror backstop.

The inline Fix B writer is the event-time owner.  This suite pins the
permanent no-age loss-recovery seam that repairs mirrors after a crash
between Task completion and the inline write, or during a straggler
window.  The mirror follows its Task: COMPLETED, FAILED and CANCELLED are
terminal on the Task side, while the JobItem receipt is always DONE with
``terminal_reason='completed'``.

The private-service tests exercise the actual
``JobRecoveryService._reconcile_terminal_message_mirrors`` seam and its
best-effort logging contract.  Repository tests stay on real file-backed
SQLite rows so state and ordering are genuine database behavior.

Round-3 widens the backstop scan + guarded IN-list to cover all
pre-terminal admission states (``{queued, active, paused}``) — a
mirror with a TERMINAL linked Task must follow it regardless of where
in the dequeue lifecycle it is stuck.  ``(queued, done)`` and
``(active, done)`` are both enumerated in ``VALID_TRANSITIONS``;
``paused`` is normalized to the active branch for
``validate_transition`` exactly as the inline writer does.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_recovery_service import JobRecoveryService


_STALE_CREATED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    """Real in-memory SQLite engine (StaticPool) for sequential tests."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def job_repo(engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def task_repo(engine) -> TaskRepository:
    return TaskRepository(engine)


def _seed_instance(engine, *, status: str = InstanceStatus.RUNNING.value) -> str:
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agent",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_job(
    engine,
    *,
    instance_id: str | None,
    job_type: str = "message",
    admission_state: str = AdmissionState.ACTIVE.value,
    created_at: datetime = _STALE_CREATED_AT,
) -> JobItem:
    with Session(engine) as session:
        job = JobItem(
            job_id=f"job-{uuid.uuid4().hex[:8]}",
            agent_id="developer",
            agent_dir="/tmp/agent",
            message="round-2-backstop",
            source="api",
            job_type=job_type,
            admission_state=admission_state,
            instance_id=instance_id,
            project_id="test-project",
            created_at=created_at.isoformat(),
            job_metadata={},
        )
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def _seed_task(
    engine,
    *,
    work_id: str,
    instance_id: str | None = None,
    status: str = TaskStatus.RUNNING.value,
) -> Task:
    with Session(engine) as session:
        task = Task(
            work_id=work_id,
            instance_id=instance_id,
            task_type="process_message",
            status=status,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


def _read_job(engine, job_id: str) -> JobItem | None:
    with Session(engine) as session:
        return session.get(JobItem, job_id)


def test_task_repository_is_required(job_repo):
    """A missing Task dependency fails fast instead of every cycle."""
    with pytest.raises(
        ValueError, match="task_repository is required"
    ):
        job_repo.reconcile_terminal_message_mirrors(task_repository=None)


def test_stale_terminal_message_mirror_follows_task_to_done(
    engine, job_repo, task_repo
):
    """A terminal Task, even for a 2020 mirror, is followed to DONE."""
    instance_id = _seed_instance(engine)
    job = _seed_job(engine, instance_id=instance_id)
    _seed_task(
        engine,
        work_id=job.job_id,
        instance_id=instance_id,
        status=TaskStatus.COMPLETED.value,
    )

    reaped = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    assert len(reaped) == 1
    assert reaped[0].job_id == job.job_id
    assert reaped[0].admission_state == AdmissionState.DONE.value
    assert reaped[0].terminal_reason == "completed"
    refreshed = _read_job(engine, job.job_id)
    assert refreshed is not None
    assert refreshed.admission_state == AdmissionState.DONE.value
    assert refreshed.terminal_reason == "completed"


def test_terminal_task_statuses_all_follow(engine, job_repo, task_repo):
    """FAILED and CANCELLED are terminal too; the mirror remains a receipt."""
    for status in (
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    ):
        instance_id = _seed_instance(engine)
        job = _seed_job(engine, instance_id=instance_id)
        _seed_task(
            engine,
            work_id=job.job_id,
            instance_id=instance_id,
            status=status,
        )

    reaped = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    assert len(reaped) == 3
    assert {item.job_id for item in reaped} == {
        row.job_id for row in _all_jobs(engine)
    }


def _all_jobs(engine) -> list[JobItem]:
    with Session(engine) as session:
        return list(session.exec(select(JobItem)))


# ---------------------------------------------------------------------------
# Round-3 — pre-terminal admission-state coverage for the widened F-1 scan.
#
# The F-1 backstop now sweeps every pre-terminal message mirror
# (``{queued, active, paused}``) and the guarded IN-list must admit all
# three.  ``active`` was the only covered state at round-2; the new cases
# pin ``queued`` and ``paused`` here.  Live-task and task-type exclusions
# MUST keep holding for every widened state — a queued / paused mirror
# whose Task is still alive stays in place.
# ---------------------------------------------------------------------------


_PRE_TERMINAL_MIRROR_STATES = (
    AdmissionState.QUEUED.value,
    AdmissionState.ACTIVE.value,
    "paused",
)


@pytest.mark.parametrize("admission_state", _PRE_TERMINAL_MIRROR_STATES)
def test_widened_scan_reconciles_terminal_task_in_every_pre_terminal_state(
    engine, job_repo, task_repo, admission_state
):
    """A mirror seeded in any pre-terminal state with a TERMINAL Task
    follows the Task to ``done`` with ``terminal_reason='completed'``.

    Pins the scan widening (every pre-terminal state is now a
    candidate) AND the SQL guard widening (every pre-terminal state
    is admitted by ``WHERE admission_state IN (...)`` so the UPDATE
    actually lands — ``rowcount == 1``, not ``rowcount == 0``).
    ``paused`` is the legacy/drift spelling.
    """
    instance_id = _seed_instance(engine)
    job = _seed_job(
        engine, instance_id=instance_id, admission_state=admission_state
    )
    _seed_task(
        engine,
        work_id=job.job_id,
        instance_id=instance_id,
        status=TaskStatus.COMPLETED.value,
    )

    reaped = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    # Assert the reconciliation actually transitioned the row.
    # ``rowcount == 1`` (not 0) is the real proof — a queued row with
    # the pre-widening IN-list would have hit ``rowcount == 0`` even
    # when the scan picked it up.
    assert len(reaped) == 1
    assert reaped[0].job_id == job.job_id
    assert reaped[0].admission_state == AdmissionState.DONE.value
    assert reaped[0].terminal_reason == "completed"
    refreshed = _read_job(engine, job.job_id)
    assert refreshed is not None
    assert refreshed.admission_state == AdmissionState.DONE.value
    assert refreshed.terminal_reason == "completed"


@pytest.mark.parametrize("admission_state", _PRE_TERMINAL_MIRROR_STATES)
def test_widened_scan_keeps_live_task_in_every_pre_terminal_state(
    engine, job_repo, task_repo, admission_state
):
    """A mirror seeded in any pre-terminal state with a LIVE Task stays
    in that state.

    Live-task exclusion must hold for the widened states — a
    ``queued`` or ``paused`` mirror whose Task is still running is
    NOT a candidate, even though the scan widening now looks at
    every pre-terminal row.  ``PENDING`` is the simplest non-terminal
    Task that would otherwise qualify.
    """
    instance_id = _seed_instance(engine)
    job = _seed_job(
        engine, instance_id=instance_id, admission_state=admission_state
    )
    _seed_task(
        engine,
        work_id=job.job_id,
        instance_id=instance_id,
        status=TaskStatus.PENDING.value,
    )

    reaped = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    assert reaped == []
    refreshed = _read_job(engine, job.job_id)
    assert refreshed is not None
    assert refreshed.admission_state == admission_state
    # The Task-side link must also be untouched — backstop never
    # transitions Tasks; it only follows them.
    assert refreshed.terminal_reason is None


def test_live_task_statuses_remain_active(engine, job_repo, task_repo):
    """PENDING, RUNNING and PAUSED Tasks are not backstop candidates."""
    for status in (
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.PAUSED.value,
    ):
        instance_id = _seed_instance(engine)
        job = _seed_job(engine, instance_id=instance_id)
        _seed_task(
            engine,
            work_id=job.job_id,
            instance_id=instance_id,
            status=status,
        )

    reaped = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    assert reaped == []
    with Session(engine) as session:
        rows = list(session.exec(select(JobItem)))
    assert all(
        row.admission_state == AdmissionState.ACTIVE.value
        for row in rows
    )


def test_task_type_mirror_remains_active(engine, job_repo, task_repo):
    """Scope discipline excludes TASK/mission JobItems from the message leg."""
    instance_id = _seed_instance(engine)
    job = _seed_job(engine, instance_id=instance_id, job_type="task")
    _seed_task(
        engine,
        work_id=job.job_id,
        instance_id=instance_id,
        status=TaskStatus.COMPLETED.value,
    )

    reaped = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    assert reaped == []
    refreshed = _read_job(engine, job.job_id)
    assert refreshed is not None
    assert refreshed.admission_state == AdmissionState.ACTIVE.value


def test_idempotent_rerun(engine, job_repo, task_repo):
    """The second bounded sweep repairs nothing new and changes nothing."""
    instance_id = _seed_instance(engine)
    job = _seed_job(engine, instance_id=instance_id)
    _seed_task(
        engine,
        work_id=job.job_id,
        instance_id=instance_id,
        status=TaskStatus.COMPLETED.value,
    )

    first = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )
    second = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    assert len(first) == 1
    assert second == []
    refreshed = _read_job(engine, job.job_id)
    assert refreshed is not None
    assert refreshed.admission_state == AdmissionState.DONE.value
    assert refreshed.terminal_reason == "completed"


@pytest.mark.asyncio
async def test_service_backstop_emits_detail_and_soft_failure(
    engine, job_repo, task_repo, caplog
):
    """The real service seam records success and contains/logs a soft-fail."""
    instance_id = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
    job = _seed_job(
        engine,
        instance_id=instance_id,
        created_at=datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc),
    )
    _seed_task(
        engine,
        work_id=job.job_id,
        instance_id=instance_id,
        status=TaskStatus.COMPLETED.value,
    )

    service = JobRecoveryService(
        job_repository=job_repo,
        lock_repository=MagicMock(),
        instance_repository=MagicMock(),
        task_repository=task_repo,
    )
    details = await service._reconcile_terminal_message_mirrors()

    assert details == [{
        "pattern": "terminal_message_mirror_done",
        "job_id": job.job_id,
        "task_id": None,
        "instance_id": instance_id,
        "reason": (
            f"F-1 terminal message-mirror backstop: job "
            f"{job.job_id[:8]}... followed its terminal Task "
            f"to admission_state='done' with "
            f"terminal_reason='completed'"
        ),
    }]

    failing_job_repo = MagicMock()
    failing_job_repo.reconcile_terminal_message_mirrors.side_effect = (
        RuntimeError("backstop boom")
    )
    failing_service = JobRecoveryService(
        job_repository=failing_job_repo,
        lock_repository=MagicMock(),
        instance_repository=MagicMock(),
        task_repository=MagicMock(),
    )
    with caplog.at_level(
        "ERROR", logger="daemon.services.job_recovery_service"
    ):
        failed_details = await failing_service._reconcile_terminal_message_mirrors()

    assert failed_details == []
    assert "F-1 terminal message-mirror backstop soft-failed" in caplog.text
    assert any(record.exc_info for record in caplog.records)
