"""Unit tests for ``MessageQueueRepository.clear_all`` 2-arm disjunction.

Feature: durability-f1-f2 / F-1 / Phase 1 (2026-10-04).

The queue-side ``clear_all`` predicate was extended in lockstep
with the task-side predicate to admit a second arm — a
terminal-stamped task row of a non-terminal instance retains
its backing ``message_queue`` row across the boot-wipe. This
is the queue-side symmetric extension of task-side arm 3
(plan §2, §13b).

The 2-arm disjunction (mirroring the task-side predicate):

  1. ``status IN ('running', 'paused')`` (preserved as before)
  2. ``auto_continued_at IS NOT NULL AND EXISTS (SELECT 1
     FROM instances WHERE instances.instance_id = task.instance_id
     AND instances.status NOT IN TERMINAL_INSTANCE_STATUSES)``
     (NEW — the F-1 wedge fix)

Plus the FP1 JobItem-anchor clause (preserved byte-exact from
the pre-F-1 wipe). The kill-switch
``ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`` (env-direct, default
ON, ``=0`` disables ONLY arm 3) gates the queue-side arm 3
condition; arm 1 is never gated.

The primary test ``test_message_queue_clear_all_2arm_disjunction``
exercises the real ``MessageQueueRepository.clear_all(preserve_in_flight=True)``
SQL on a real DB session (file-backed SQLite, F9 parity).
Coverage matrix:
  (a) arm-3 ``EXISTS instances`` join → backing message of a
      terminal+auto_continued_at row of a NON-terminal
      instance SURVIVES (kill-switch ON).
  (b) terminal-no-marker row's backing message DELETED.
  (c) kill-switch ON preserves, OFF yields the EXACT pre-fix
      predicate (arm-3 absent).
  (d) JobItem-anchor clause keep-green pin (PENDING task
      anchoring a live JobItem + its message survives).
  (e) Arm 1 (running/paused task) is preserved in BOTH
      kill-switch states.

Harness mirrors ``tests/unit/repositories/test_task_auto_continued_lifecycle.py``:
REAL file-backed SQLite (``tmp_path`` + ``NullPool`` —
deliberately NOT ``StaticPool``/``:memory:``, mirrors
production concurrency).
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterator

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401 — register
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.constants import TERMINAL_INSTANCE_STATUSES
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.timestamps import now_utc_naive


# ---------------------------------------------------------------------------
# F9-parity harness — file-backed SQLite (per-connection PRAGMAs, NullPool)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """REAL SQLite FILE database, per-connection PRAGMAs, NullPool.

    F9 parity — mirrors production concurrency for the queue
    wipe seam. NOT ``StaticPool``/``:memory:``.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/f1_message_queue_wipe.db",
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
# Seeding helpers — minimal real-DB row inserts
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
    message_id: str | None = None,
) -> int:
    """Insert a Task row. Returns its primary key."""
    with Session(eng) as s:
        t = Task(
            id=task_id,
            work_id=work_id or f"work-{uuid.uuid4().hex[:12]}",
            task_type=task_type,
            instance_id=instance_id,
            message_id=message_id,
            status=status,
            auto_continued_at=auto_continued_at,
            created_at=now_utc_naive(),
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _seed_message(
    eng: Engine,
    message_id: str,
    instance_id: str,
    *,
    content: str = "hello",
) -> None:
    """Insert a MessageQueue row."""
    with Session(eng) as s:
        s.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content=content,
                type="human",
                source="api",
            )
        )
        s.commit()


def _boot_epoch() -> datetime:
    return now_utc_naive()


def _surviving_message_ids(eng: Engine) -> set[str]:
    """Return the set of message_id values still present after a wipe.

    Asserts against a real session, NOT a Python re-implementation.
    """
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT message_id FROM message_queue")).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Kill-switch env-var context manager
# ---------------------------------------------------------------------------


@contextmanager
def _env(name: str, value: str | None) -> Iterator[None]:
    """Temporarily set/unset an env var."""
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


# ---------------------------------------------------------------------------
# S5 / Plan-overview primary — real SQL 2-arm disjunction on the queue side
# ---------------------------------------------------------------------------


def test_message_queue_clear_all_2arm_disjunction(engine: Engine) -> None:
    """Plan-overview S5 primary test (per ITERATION-002 Issue-1).

    Exercises the real ``MessageQueueRepository.clear_all(preserve_in_flight=True)``
    SQL on a real DB session (file-backed SQLite) — NOT a
    Python re-implementation of the predicate. The test pins
    the 2-arm disjunction end-to-end:

      (a) arm-3 ``EXISTS instances`` join — backing message
          of a terminal+auto_continued_at row of a NON-terminal
          instance SURVIVES (kill-switch ON).
      (b) terminal-no-marker row's backing message DELETED.
      (c) kill-switch ON preserves, OFF yields the EXACT
          pre-fix predicate (arm-3 absent — same row DELETED).
      (e) Arm 1 (running/paused task) is preserved in BOTH
          kill-switch states.

    Note (asymmetry per ``research-findings.md §1 Q3``):
    the queue-side ``clear_all`` predicate does NOT include
    the FP1 JobItem-anchor clause (the task side does). The
    queue side resolves the keep-set via ``task.message_id``
    linkage to ``task.status`` and the arm-3 ``EXISTS
    instances`` co-condition. The FP1 keep-green pin lives on
    the task side (see ``test_fp1_jobitem_anchor_clause_keeps_pending_task``
    in ``test_discard_on_startup_dependency_bus_race.py``).
    """
    repo = SQLModelMessageQueueRepository(engine)

    # --- Seed all four message-bearing task rows ---
    # (a) terminal+stamped of a non-terminal instance → arm 3 keeps.
    _seed_instance(engine, "inst-S5a", status=InstanceStatus.WAITING_CHILDREN.value)
    _seed_message(engine, "msg-S5a", "inst-S5a")
    _seed_task(
        engine,
        "inst-S5a",
        status=TaskStatus.FAILED.value,
        auto_continued_at=_boot_epoch() - timedelta(seconds=10),
        message_id="msg-S5a",
    )
    # (b) terminal-no-marker of a non-terminal instance → arm 3
    # declines (no marker); arm 1 also declines (status not
    # running/paused); DELETE.
    _seed_instance(engine, "inst-S5b", status=InstanceStatus.RUNNING.value)
    _seed_message(engine, "msg-S5b", "inst-S5b")
    _seed_task(
        engine,
        "inst-S5b",
        status=TaskStatus.FAILED.value,
        auto_continued_at=None,
        message_id="msg-S5b",
    )
    # (e-1) running task → arm 1 PRESERVES.
    _seed_instance(engine, "inst-S5e1", status=InstanceStatus.RUNNING.value)
    _seed_message(engine, "msg-S5e1", "inst-S5e1")
    _seed_task(
        engine,
        "inst-S5e1",
        status=TaskStatus.RUNNING.value,
        message_id="msg-S5e1",
    )
    # (e-2) paused task → arm 1 PRESERVES.
    _seed_instance(engine, "inst-S5e2", status=InstanceStatus.PAUSED.value)
    _seed_message(engine, "msg-S5e2", "inst-S5e2")
    _seed_task(
        engine,
        "inst-S5e2",
        status=TaskStatus.PAUSED.value,
        message_id="msg-S5e2",
    )

    # --- (a, b, e) kill-switch ON (default) ---
    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        deleted_on = repo.clear_all(preserve_in_flight=True)
    surviving_on = _surviving_message_ids(engine)
    # (a) preserved; (b) deleted; (e-1, e-2) preserved.
    assert "msg-S5a" in surviving_on, (
        f"(a) arm-3 stamped row of non-terminal instance should survive; "
        f"surviving={sorted(surviving_on)}"
    )
    assert "msg-S5b" not in surviving_on, (
        f"(b) terminal-no-marker row should be deleted; "
        f"surviving={sorted(surviving_on)}"
    )
    assert "msg-S5e1" in surviving_on, "(e-1) running task message preserved"
    assert "msg-S5e2" in surviving_on, "(e-2) paused task message preserved"
    # 1 row deleted (b) → 3 rows survive.
    assert deleted_on == 1, (
        f"kill-switch ON: 1 row deleted (b); deleted={deleted_on} "
        f"surviving={sorted(surviving_on)}"
    )
    assert len(surviving_on) == 3

    # --- (c) kill-switch OFF: re-seed and verify pre-fix predicate ---
    # Clean and re-seed (independent of the ON state).
    with Session(engine) as s:
        s.execute(text("DELETE FROM task"))
        s.execute(text("DELETE FROM message_queue"))
        s.execute(text("DELETE FROM instances"))
        s.execute(text("DELETE FROM job_queue_items"))
        s.commit()
    _seed_instance(engine, "inst-S5a", status=InstanceStatus.WAITING_CHILDREN.value)
    _seed_message(engine, "msg-S5a", "inst-S5a")
    _seed_task(
        engine,
        "inst-S5a",
        status=TaskStatus.FAILED.value,
        auto_continued_at=_boot_epoch() - timedelta(seconds=10),
        message_id="msg-S5a",
    )
    _seed_instance(engine, "inst-S5b", status=InstanceStatus.RUNNING.value)
    _seed_message(engine, "msg-S5b", "inst-S5b")
    _seed_task(
        engine,
        "inst-S5b",
        status=TaskStatus.FAILED.value,
        auto_continued_at=None,
        message_id="msg-S5b",
    )
    _seed_instance(engine, "inst-S5e1", status=InstanceStatus.RUNNING.value)
    _seed_message(engine, "msg-S5e1", "inst-S5e1")
    _seed_task(
        engine,
        "inst-S5e1",
        status=TaskStatus.RUNNING.value,
        message_id="msg-S5e1",
    )

    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "0"):
        deleted_off = repo.clear_all(preserve_in_flight=True)
    surviving_off = _surviving_message_ids(engine)
    # (c) arm 3 disarmed — stamped row DELETED; arm 1 still active
    # — running row PRESERVED.
    assert "msg-S5a" not in surviving_off, (
        f"(c) kill-switch OFF: arm 3 disarmed, stamped row deleted; "
        f"surviving={sorted(surviving_off)}"
    )
    assert "msg-S5b" not in surviving_off
    assert "msg-S5e1" in surviving_off, (
        f"(c) arm 1 (running) still preserved when kill-switch is OFF; "
        f"surviving={sorted(surviving_off)}"
    )
    assert deleted_off == 2
    assert len(surviving_off) == 1
    # TERMINAL_INSTANCE_STATUSES parity — the queue-side
    # predicate hardcodes the same set; pin the equality.
    assert "completed" in TERMINAL_INSTANCE_STATUSES
    assert "terminated" in TERMINAL_INSTANCE_STATUSES
    assert "error" in TERMINAL_INSTANCE_STATUSES
    assert "failed" in TERMINAL_INSTANCE_STATUSES
