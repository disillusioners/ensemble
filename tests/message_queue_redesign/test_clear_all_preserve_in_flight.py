"""Tests for the safe ``clear_all(preserve_in_flight=...)`` backlog clear.

``discard_on_startup`` was redefined (2026-07-07) from a nuclear wipe to
a "safe backlog clear": only UNSTARTED / terminal work is discarded;
RUNNING (in-flight) and PAUSED (resumable) tasks — and the messages
backing them — survive so a paused instance still blocks
``system_defer_queue`` and can still be resumed across a restart.

These tests pin both the preserve mode and the default (wipe-all) mode
for ``TaskRepository.clear_all`` and ``MessageQueueRepository.clear_all``.

Run with::

    pytest tests/message_queue_redesign/test_clear_all_preserve_in_flight.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from daemon.repositories.message_queue.repository import SQLModelMessageQueueRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository

# Statuses that MUST survive a preserve-mode clear.
_KEEP_TASK_STATUSES = (TaskStatus.RUNNING.value, TaskStatus.PAUSED.value)
# Statuses that are backlog / dead and MUST be discarded.
_DISCARD_TASK_STATUSES = (
    TaskStatus.PENDING.value,
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
)


def _insert_task(engine, *, message_id: str, status: str, instance_id: str = "inst-1") -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO task (task_type, instance_id, message_id, status,
                                  retry_count, created_at, cancel_requested,
                                  retry_scheduled, work_id, is_deferred)
                VALUES (:task_type, :instance_id, :message_id, :status,
                        :retry_count, :created_at, :cancel_requested,
                        :retry_scheduled, :work_id, :is_deferred)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "retry_count": 0,
                "created_at": datetime.now(timezone.utc),
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": str(uuid.uuid4()),
                "is_deferred": False,
            },
        )


def _insert_message(engine, *, message_id: str, instance_id: str = "inst-1") -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO message_queue (message_id, instance_id, content, type,
                                           status, priority, retry_count, max_retries,
                                           enqueued_at)
                VALUES (:message_id, :instance_id, :content, :type,
                        :status, :priority, :retry_count, :max_retries, :enqueued_at)
                """
            ),
            {
                "message_id": message_id,
                "instance_id": instance_id,
                "content": "hello",
                "type": "human",
                "status": "ready",
                "priority": 1,
                "retry_count": 0,
                "max_retries": 5,
                "enqueued_at": datetime.now(timezone.utc),
            },
        )


def _task_statuses(engine) -> dict[str, str]:
    """Map message_id -> task.status for every task row."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT message_id, status FROM task")).fetchall()
    return {r[0]: r[1] for r in rows}


def _message_ids(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT message_id FROM message_queue")).fetchall()
    return {r[0] for r in rows}


# ── TaskRepository.clear_all ─────────────────────────────────────────────────


class TestTaskClearAllPreserveInFlight:
    def test_default_wipes_everything(self, engine):
        repo = TaskRepository(engine)
        _insert_task(engine, message_id="m-pending", status=TaskStatus.PENDING.value)
        _insert_task(engine, message_id="m-running", status=TaskStatus.RUNNING.value)
        _insert_task(engine, message_id="m-paused", status=TaskStatus.PAUSED.value)

        deleted = repo.clear_all()  # default: nuclear wipe

        assert deleted == 3
        assert _task_statuses(engine) == {}

    def test_preserve_keeps_running_and_paused(self, engine):
        repo = TaskRepository(engine)
        for status in _DISCARD_TASK_STATUSES:
            _insert_task(engine, message_id=f"m-{status}", status=status)
        for status in _KEEP_TASK_STATUSES:
            _insert_task(engine, message_id=f"m-{status}", status=status)

        deleted = repo.clear_all(preserve_in_flight=True)

        # 4 backlog/dead rows discarded (pending, completed, failed, cancelled).
        assert deleted == len(_DISCARD_TASK_STATUSES)
        survivors = _task_statuses(engine)
        assert set(survivors.keys()) == {f"m-{s}" for s in _KEEP_TASK_STATUSES}
        assert set(survivors.values()) == set(_KEEP_TASK_STATUSES)

    def test_preserve_on_empty_is_zero(self, engine):
        repo = TaskRepository(engine)
        assert repo.clear_all(preserve_in_flight=True) == 0


# ── MessageQueueRepository.clear_all ─────────────────────────────────────────


class TestMessageQueueClearAllPreserveInFlight:
    def test_default_wipes_everything(self, engine):
        repo = SQLModelMessageQueueRepository(engine)
        _insert_message(engine, message_id="m-a")
        _insert_message(engine, message_id="m-b")

        deleted = repo.clear_all()

        assert deleted == 2
        assert _message_ids(engine) == set()

    def test_preserve_keeps_messages_backing_in_flight_tasks(self, engine):
        """Only messages referenced by a RUNNING/PAUSED task survive."""
        repo = SQLModelMessageQueueRepository(engine)
        # Backlog message (task is pending) -> discarded.
        _insert_message(engine, message_id="m-pending")
        _insert_task(engine, message_id="m-pending", status=TaskStatus.PENDING.value)
        # Dead message (task completed) -> discarded.
        _insert_message(engine, message_id="m-done")
        _insert_task(engine, message_id="m-done", status=TaskStatus.COMPLETED.value)
        # Resumable message (task paused) -> KEPT (resume needs it).
        _insert_message(engine, message_id="m-paused")
        _insert_task(engine, message_id="m-paused", status=TaskStatus.PAUSED.value)
        # In-flight message (task running) -> KEPT (recovery needs it).
        _insert_message(engine, message_id="m-running")
        _insert_task(engine, message_id="m-running", status=TaskStatus.RUNNING.value)

        deleted = repo.clear_all(preserve_in_flight=True)

        assert deleted == 2
        assert _message_ids(engine) == {"m-paused", "m-running"}

    def test_preserve_discards_orphan_messages_with_no_task(self, engine):
        """A message with no backing task is backlog -> discarded."""
        repo = SQLModelMessageQueueRepository(engine)
        _insert_message(engine, message_id="m-orphan")

        deleted = repo.clear_all(preserve_in_flight=True)

        assert deleted == 1
        assert _message_ids(engine) == set()
