"""Unit tests for the message-queue boot-recovery pass (Phase 2 / G2).

Feature: hard-reboot recovery. Phase 2.

Covers the boot pass ``daemon/services/message_queue_boot_recovery.py``
and its two repository primitives on
``daemon/repositories/message_queue/repository.py``:

  M1 — boot-epoch-relative selection: a row claimed *seconds* before the
       power loss (the COMMON hard-kill shape, which the 3600s age
       predicate misses) IS selected; a row touched AFTER the boot is
       not.
  M2 — the NULL-``last_activity_at`` branch is selected too.
  M3 — the skip-guards: a row on a TERMINAL instance and a row backed
       by a RUNNING/PAUSED task are left untouched (revive hazard /
       AutoContinue owns them).
  M4 — the reset is a guarded CAS: the row flips to 'ready', a second
       pass is a no-op (reboot-loop idempotency).
  M5 — post-pass ``get_stats()["processing_count"] == 0`` and the
       busy-guard's 'pending/processing counts no longer wedge' shape.
  M6 — the kill-switch (``ENSEMBLE_HARD_REBOOT_BOOT_PASS=0``) skips
       the pass entirely.
  M7 — never-wedge: a raising repository does not propagate out of the
       pass.

Harness: REAL file-backed SQLite (mirrors
``tests/unit/repositories/test_auto_continue_candidates.py`` — a file
DB with NullPool, NOT ``:memory:``, because the pass opens more than
one connection and ``:memory:`` is per-connection).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401 — register
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.services.message_queue_boot_recovery import (
    HARD_REBOOT_BOOT_PASS_ENV,
    recover_stuck_messages_on_startup,
)
from daemon.services.timestamps import now_utc_naive

# The boot epoch — a fixed instant, so "before the boot" is exact.
BOOT_EPOCH = datetime(2026, 10, 21, 6, 0, 0)


@pytest.fixture
def engine(tmp_path):
    """REAL SQLite FILE database, per-connection PRAGMAs, NullPool."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/mq_boot_recovery.db",
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

    # Only the three tables this pass touches — a full create_all() of
    # every registered model costs ~0.5s per test.
    SQLModel.metadata.create_all(
        eng,
        tables=[MessageQueue.__table__, Instance.__table__, Task.__table__],
    )
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    return SQLModelMessageQueueRepository(engine)


def _seed_instance(eng: Engine, instance_id: str, status: str) -> None:
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


def _seed_message(
    eng: Engine,
    message_id: str,
    instance_id: str,
    *,
    last_activity_at: datetime | None,
    status: str = MessageStatus.PROCESSING.value,
) -> None:
    with Session(eng) as s:
        s.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="fixture content",
                status=status,
                last_activity_at=last_activity_at,
                processing_started_at=last_activity_at,
            )
        )
        s.commit()


def _seed_task(
    eng: Engine,
    work_id: str,
    instance_id: str,
    message_id: str,
    status: str,
) -> None:
    with Session(eng) as s:
        s.add(
            Task(
                work_id=work_id,
                instance_id=instance_id,
                message_id=message_id,
                status=status,
            )
        )
        s.commit()


def _status_of(eng: Engine, message_id: str) -> str:
    with Session(eng) as s:
        row = s.get(MessageQueue, message_id)
        return row.status


# ---------------------------------------------------------------------------
# M1 / M2 — boot-epoch-relative selection
# ---------------------------------------------------------------------------


def test_fresh_dead_row_is_selected(repo, engine):
    """M1 — a row claimed seconds before the power loss IS selected.

    This is the shape the 3600s AGE predicate misses: the row is only
    minutes old, but it predates the boot.
    """
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    selected = [r.message_id for r in repo.find_stuck_messages_before(BOOT_EPOCH)]

    assert selected == ["m_fresh"]


def test_row_touched_after_the_boot_is_not_selected(repo, engine):
    """M1 — a row touched AFTER the boot is NOT selected."""
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_new",
        "i1",
        last_activity_at=BOOT_EPOCH + timedelta(minutes=1),
    )

    assert repo.find_stuck_messages_before(BOOT_EPOCH) == []


def test_null_last_activity_row_is_selected(repo, engine):
    """M2 — the NULL-last_activity_at branch is selected."""
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(engine, "m_null", "i1", last_activity_at=None)

    selected = [r.message_id for r in repo.find_stuck_messages_before(BOOT_EPOCH)]

    assert selected == ["m_null"]


# ---------------------------------------------------------------------------
# M3 — skip-guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instance_status",
    [
        InstanceStatus.TERMINATED.value,
        InstanceStatus.COMPLETED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.FAILED.value,
    ],
)
def test_terminal_instance_row_is_untouched(repo, engine, instance_status):
    """M3 — revive hazard: a TERMINAL instance's row is never reset."""
    _seed_instance(engine, "i_term", instance_status)
    _seed_message(
        engine,
        "m_term",
        "i_term",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    assert reset == 0
    assert _status_of(engine, "m_term") == MessageStatus.PROCESSING.value


@pytest.mark.parametrize("task_status", [TaskStatus.RUNNING.value, TaskStatus.PAUSED.value])
def test_live_task_row_is_untouched(repo, engine, task_status):
    """M3 — a RUNNING/PAUSED task owns its row (AutoContinue completes it)."""
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_task",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    _seed_task(engine, "w1", "i1", "m_task", task_status)

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    assert reset == 0
    assert _status_of(engine, "m_task") == MessageStatus.PROCESSING.value


# ---------------------------------------------------------------------------
# M4 / M5 — the guarded reset, idempotency, and the accounting
# ---------------------------------------------------------------------------


def test_reset_flips_the_row_to_ready(repo, engine):
    """M4 — the reset flips a stranded row to 'ready'."""
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    assert reset == 1
    assert _status_of(engine, "m_fresh") == MessageStatus.READY.value


def test_second_pass_is_a_noop(repo, engine):
    """M4 — reboot-loop idempotency: a committed reset leaves the predicate."""
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 1
    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 0


def test_post_pass_processing_count_is_zero_and_guard_does_not_wedge(repo, engine):
    """M5 — the stranded row leaves the 'processing' bucket.

    The busy-guard (daemon/tools/instance.py) trips on
    ``pending_count > 0 or processing_count > 0``; a stranded
    'processing' row is what wedges it. After the pass the row rests at
    'ready' (a normal, deliverable resting state), so the processing
    count is zero.
    """
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    recover_stuck_messages_on_startup(repo, BOOT_EPOCH)
    stats = repo.get_stats("i1")

    assert stats["processing_count"] == 0
    # The guard's 'in progress' term is what the stranded row fed.
    assert not (stats["processing_count"] > 0)


# ---------------------------------------------------------------------------
# M6 / M7 — kill-switch and never-wedge
# ---------------------------------------------------------------------------


def test_kill_switch_zero_skips_the_pass(repo, engine, monkeypatch):
    """M6 — ENSEMBLE_HARD_REBOOT_BOOT_PASS=0 disables the pass."""
    monkeypatch.setenv(HARD_REBOOT_BOOT_PASS_ENV, "0")
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    assert reset == 0
    assert _status_of(engine, "m_fresh") == MessageStatus.PROCESSING.value


def test_kill_switch_defaults_on(repo, engine, monkeypatch):
    """M6 — the default is ON (the deliverable is zero-user-action)."""
    monkeypatch.delenv(HARD_REBOOT_BOOT_PASS_ENV, raising=False)
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 1


def test_raising_repository_does_not_propagate(repo, engine, monkeypatch):
    """M7 — never-wedge: a broken repository cannot break the boot."""
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    def boom(_epoch):
        raise RuntimeError("simulated repository failure")

    monkeypatch.setattr(repo, "find_stuck_messages_before", boom)

    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 0


def test_no_boot_epoch_skips_the_pass(repo, engine):
    """M7 — a failed epoch capture leaves the rows untouched."""
    _seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    _seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    assert recover_stuck_messages_on_startup(repo, None) == 0
    assert _status_of(engine, "m_fresh") == MessageStatus.PROCESSING.value
