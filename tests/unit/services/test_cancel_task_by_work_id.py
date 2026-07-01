"""Tests for ``JobQueueService.cancel_task_by_work_id`` — the write-side
virtual-job facade (Part B, revive-fix follow-up, 2026-07-01).

The read side (``get_work`` / ``resolve_work``) resolves a ``work_id`` to
either a JobItem or a Task. The cancel side was JobItem-only, so cancelling
a Task-backed work_id (a message turn the user wants to abort) 404'd from
``DELETE /api/jobs/{work_id}``. ``cancel_task_by_work_id`` closes that gap:

* a RUNNING task → cooperative ``request_cancel`` (graceful).
* a PENDING/PAUSED task → direct atomic ``cancel_task`` (no worker holds it).
* missing / already-terminal work_id → ``False``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session as SQLModelSession, SQLModel, create_engine

from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.repositories.job_queue import JobRepository, JobQueueRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
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
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


class _FakeInstanceManager:
    """Minimal shim exposing only ``_task_repo`` (the attribute
    ``cancel_task_by_work_id`` reaches through)."""

    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo


@pytest.fixture
def service(task_repo: TaskRepository, engine: Engine) -> JobQueueService:
    job_repo = JobRepository(engine)
    queue_repo = JobQueueRepository(engine)
    svc = JobQueueService(
        repository=job_repo,
        lock_manager=JobLockManager(LockRepository(engine)),
        queue_repo=queue_repo,
        instance_manager=_FakeInstanceManager(task_repo),
    )
    return svc


def _seed_task(
    task_repo: TaskRepository,
    engine: Engine,
    *,
    status: str = TaskStatus.PENDING.value,
    instance_id: str = "inst-x",
) -> str:
    """Insert a Task via the repository and return its ``work_id``.

    ``create`` always seeds PENDING; non-PENDING seeds flip the status
    in a direct session so the test exercises a specific lifecycle.
    """
    task = task_repo.create(
        task_type="process_message",
        instance_id=instance_id,
        message_id="m-1",
    )
    if status != TaskStatus.PENDING.value:
        with SQLModelSession(engine) as session:
            row = session.get(Task, task.id)
            assert row is not None
            row.status = status
            session.add(row)
            session.commit()
    return task.work_id


def _set_status(engine: Engine, task_id: int, status: str) -> None:
    """Flip a task's status directly (simulate a claimed/terminal task)."""
    with SQLModelSession(engine) as session:
        row = session.get(Task, task_id)
        assert row is not None
        row.status = status
        session.add(row)
        session.commit()


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_task_cancelled_directly(service, task_repo, engine):
    """A PENDING task (never claimed, no worker) is cancelled atomically
    and synchronously — the work_id no longer resolves to pending."""
    work_id = _seed_task(task_repo, engine, status=TaskStatus.PENDING.value)

    cancelled = await service.cancel_task_by_work_id(work_id)

    assert cancelled is True
    task = task_repo.get_by_work_id(work_id)
    assert task is not None
    assert task.status == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_running_task_cooperative_cancel(service, task_repo, engine):
    """A RUNNING task is cancelled cooperatively — ``cancel_requested``
    is set but the row stays RUNNING until the worker yields at its next
    checkpoint (avoids orphaning in-flight graph state)."""
    work_id = _seed_task(task_repo, engine, status=TaskStatus.PENDING.value)
    running_task = task_repo.get_by_work_id(work_id)
    assert running_task is not None
    # Flip to running to simulate a claimed task.
    _set_status(engine, running_task.id, TaskStatus.RUNNING.value)

    cancelled = await service.cancel_task_by_work_id(work_id)

    assert cancelled is True
    task = task_repo.get_by_work_id(work_id)
    assert task is not None
    assert task.cancel_requested is True
    # Cooperative: the row stays RUNNING (not flipped to CANCELLED).
    assert task.status == TaskStatus.RUNNING.value


@pytest.mark.asyncio
async def test_missing_work_id_returns_false(service):
    """A work_id that resolves to no Task returns False (no 404 raised —
    the caller decides the response)."""
    cancelled = await service.cancel_task_by_work_id("does-not-exist-uuid")
    assert cancelled is False


@pytest.mark.asyncio
async def test_already_terminal_task_returns_false(service, task_repo, engine):
    """An already-terminal task can't be cancelled again."""
    work_id = _seed_task(task_repo, engine, status=TaskStatus.PENDING.value)
    task = task_repo.get_by_work_id(work_id)
    assert task is not None
    _set_status(engine, task.id, TaskStatus.COMPLETED.value)

    cancelled = await service.cancel_task_by_work_id(work_id)

    assert cancelled is False
