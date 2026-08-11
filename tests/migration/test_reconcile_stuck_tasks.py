"""Idempotency and behavior tests for the Task↔JobItem reconciliation migration.

The migration
``daemon/migrations/versions/20260811_000001_reconcile_stuck_tasks_with_terminal_jobitems.sql``
cancels Task rows that are stuck in ``paused``/``pending`` while their
linked JobItem (matched via ``task.work_id = job_queue_items.job_id``)
has already transitioned to a terminal admission_state (``done``/``dead``).

These tests verify:

1. **Cancellation behavior** — the stuck task is cancelled, healthy
   tasks (paused/pending with ACTIVE JobItems, or terminal-JobItem
   tasks already in ``cancelled``/``completed``) are left untouched.
2. **Idempotency** — running the migration a second time on the same
   data updates 0 rows (the ``WHERE status IN ('paused','pending')``
   guard ensures the migration is safe to re-run).
3. **Soft-delete guard** — JobItems with ``deleted_at IS NOT NULL`` are
   ignored, even if their ``admission_state`` is terminal. The
   reconciliation is intentionally conservative: a soft-deleted JobItem
   should not cause a still-valid task to be cancelled.

Project rule: PostgreSQL is the primary database. The test runs the
same SQL the migration runner applies (extracted from the ``.sql``
file) against an in-memory SQLite engine. The dual-driver parity
is covered by ``tests/migration/test_idle_gate_migration_parity.py``;
this file focuses on behavioral correctness of the migration itself.

Run with::

    pytest tests/migration/test_reconcile_stuck_tasks.py -v
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_FILE = (
    REPO_ROOT
    / "daemon"
    / "migrations"
    / "versions"
    / "20260811_000001_reconcile_stuck_tasks_with_terminal_jobitems.sql"
)


# ─────────────────────────────────────────────────────────────────────────────
# Migration SQL extraction
# ─────────────────────────────────────────────────────────────────────────────


def _read_migration_up_sql() -> str:
    """Read the .sql file and return the UP section, comments stripped."""
    content = MIGRATION_FILE.read_text(encoding="utf-8")
    lines = content.split("\n")
    in_up = False
    up_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "-- UP":
            in_up = True
            continue
        if stripped == "-- DOWN":
            break
        if in_up:
            if stripped.startswith("--"):
                continue
            if stripped:
                up_lines.append(line)
    return "\n".join(up_lines).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Schema setup — minimal task + job_queue_items table pair
# ─────────────────────────────────────────────────────────────────────────────
#
# Mirrors the ``task`` columns the migration's WHERE/UPDATE clause
# references (``id``, ``work_id``, ``status``,
# ``cancel_requested``, ``cancel_requested_at``, ``completed_at``) and the
# ``job_queue_items`` columns referenced by the EXISTS subquery
# (``job_id``, ``admission_state``, ``deleted_at``). We do NOT bring
# in the full SQLModel ``Task`` and ``JobQueueItem`` models because
# that would pull in 60+ unrelated tables; the migration is a pure
# data UPDATE and the column surface it touches is small.

TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    status TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    completed_at TEXT
)
"""

JOB_QUEUE_ITEMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_queue_items (
    job_id TEXT PRIMARY KEY,
    admission_state TEXT NOT NULL,
    deleted_at TEXT
)
"""


def _make_engine() -> Engine:
    """Fresh in-memory SQLite engine with the two tables we need."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text(TASK_SCHEMA))
        conn.execute(text(JOB_QUEUE_ITEMS_SCHEMA))
    return engine


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Iterator[Engine]:
    """Per-test in-memory SQLite engine with task + job_queue_items."""
    eng = _make_engine()
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def migration_sql() -> str:
    """The migration's UP SQL (single statement, with trailing ``;``)."""
    return _read_migration_up_sql()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _insert_task(
    engine: Engine,
    *,
    task_id: str,
    work_id: str,
    status: str,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO task (id, work_id, status) "
                "VALUES (:id, :work_id, :status)"
            ),
            {"id": task_id, "work_id": work_id, "status": status},
        )


def _insert_job_item(
    engine: Engine,
    *,
    job_id: str,
    admission_state: str,
    deleted_at: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO job_queue_items (job_id, admission_state, deleted_at) "
                "VALUES (:job_id, :admission_state, :deleted_at)"
            ),
            {"job_id": job_id, "admission_state": admission_state, "deleted_at": deleted_at},
        )


def _task_status(engine: Engine, work_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM task WHERE work_id = :work_id"),
            {"work_id": work_id},
        ).fetchone()
    return row[0] if row else None


def _run_migration(engine: Engine, migration_sql: str) -> int:
    """Apply the migration SQL and return the rowcount of the UPDATE."""
    with engine.begin() as conn:
        result = conn.execute(text(migration_sql))
    # SQLAlchemy's ``Result.rowcount`` is reliable for UPDATE on SQLite.
    return result.rowcount or 0


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_cancels_stuck_task_with_terminal_done_jobitem(
    engine: Engine, migration_sql: str
) -> None:
    """A task with status='paused' and a linked JobItem in admission_state='done'
    (not soft-deleted) must be cancelled by the migration."""
    work_id = str(uuid.uuid4())
    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=work_id, status="paused")
    _insert_job_item(engine, job_id=work_id, admission_state="done")

    assert _task_status(engine, work_id) == "paused"

    rowcount = _run_migration(engine, migration_sql)
    assert rowcount == 1, f"Expected 1 row updated, got {rowcount}"
    assert _task_status(engine, work_id) == "cancelled"


def test_cancels_stuck_task_with_terminal_dead_jobitem(
    engine: Engine, migration_sql: str
) -> None:
    """A task with status='pending' and a linked JobItem in admission_state='dead'
    (not soft-deleted) must be cancelled by the migration."""
    work_id = str(uuid.uuid4())
    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=work_id, status="pending")
    _insert_job_item(engine, job_id=work_id, admission_state="dead")

    rowcount = _run_migration(engine, migration_sql)
    assert rowcount == 1
    assert _task_status(engine, work_id) == "cancelled"


def test_leaves_healthy_paused_task_with_active_jobitem(
    engine: Engine, migration_sql: str
) -> None:
    """A healthy paused task (linked JobItem is still ACTIVE) must be left
    untouched. The reconciliation is conservative: it only fires when the
    JobItem is *already* terminal."""
    work_id = str(uuid.uuid4())
    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=work_id, status="paused")
    _insert_job_item(engine, job_id=work_id, admission_state="active")

    rowcount = _run_migration(engine, migration_sql)
    assert rowcount == 0
    assert _task_status(engine, work_id) == "paused"


def test_leaves_healthy_pending_task_with_active_jobitem(
    engine: Engine, migration_sql: str
) -> None:
    """A healthy pending task (linked JobItem is still ACTIVE) must be left
    untouched. Legitimate pending work should not be cancelled by the
    migration — only the orphaned tasks get cancelled."""
    work_id = str(uuid.uuid4())
    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=work_id, status="pending")
    _insert_job_item(engine, job_id=work_id, admission_state="active")

    rowcount = _run_migration(engine, migration_sql)
    assert rowcount == 0
    assert _task_status(engine, work_id) == "pending"


def test_ignores_soft_deleted_terminal_jobitem(
    engine: Engine, migration_sql: str
) -> None:
    """A task with a soft-deleted terminal JobItem (deleted_at IS NOT NULL)
    must NOT be cancelled. Soft-deleted JobItems are excluded by the
    ``ji.deleted_at IS NULL`` guard inside the EXISTS subquery."""
    work_id = str(uuid.uuid4())
    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=work_id, status="paused")
    _insert_job_item(
        engine,
        job_id=work_id,
        admission_state="done",
        deleted_at="2026-08-01 12:00:00",
    )

    rowcount = _run_migration(engine, migration_sql)
    assert rowcount == 0
    assert _task_status(engine, work_id) == "paused"


def test_leaves_already_cancelled_task_with_terminal_jobitem(
    engine: Engine, migration_sql: str
) -> None:
    """A task already in 'cancelled' (or 'completed' or any non-{paused,
    pending}) state must be left alone, even if the JobItem is terminal.
    The ``status IN ('paused', 'pending')`` guard makes the migration
    idempotent at the row level."""
    work_id = str(uuid.uuid4())
    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=work_id, status="cancelled")
    _insert_job_item(engine, job_id=work_id, admission_state="done")

    rowcount = _run_migration(engine, migration_sql)
    assert rowcount == 0
    assert _task_status(engine, work_id) == "cancelled"


def test_mixed_scenario_only_cancels_stuck_rows(
    engine: Engine, migration_sql: str
) -> None:
    """Combined fixture: one stuck paused task, one healthy paused task,
    one healthy pending task, and one already-cancelled task — all
    coexisting. Only the stuck paused task must be cancelled; rowcount
    must equal 1."""
    stuck_paused = str(uuid.uuid4())
    healthy_paused = str(uuid.uuid4())
    healthy_pending = str(uuid.uuid4())
    already_cancelled = str(uuid.uuid4())

    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=stuck_paused, status="paused")
    _insert_job_item(engine, job_id=stuck_paused, admission_state="done")

    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=healthy_paused, status="paused")
    _insert_job_item(engine, job_id=healthy_paused, admission_state="active")

    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=healthy_pending, status="pending")
    _insert_job_item(engine, job_id=healthy_pending, admission_state="active")

    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=already_cancelled, status="cancelled")
    _insert_job_item(engine, job_id=already_cancelled, admission_state="done")

    rowcount = _run_migration(engine, migration_sql)
    assert rowcount == 1, f"Expected exactly 1 row updated, got {rowcount}"
    assert _task_status(engine, stuck_paused) == "cancelled"
    assert _task_status(engine, healthy_paused) == "paused"
    assert _task_status(engine, healthy_pending) == "pending"
    assert _task_status(engine, already_cancelled) == "cancelled"


def test_migration_is_idempotent(
    engine: Engine, migration_sql: str
) -> None:
    """Running the migration a second time on the same data must update
    0 rows. The ``status IN ('paused', 'pending')`` guard is the
    idempotency mechanism: after the first run, no row remains in
    ``paused``/``pending`` with a terminal JobItem, so the second
    run's WHERE clause matches nothing."""
    work_id = str(uuid.uuid4())
    _insert_task(engine, task_id=str(uuid.uuid4()), work_id=work_id, status="paused")
    _insert_job_item(engine, job_id=work_id, admission_state="done")

    # First run: 1 row updated, task is now 'cancelled'.
    first_rowcount = _run_migration(engine, migration_sql)
    assert first_rowcount == 1
    assert _task_status(engine, work_id) == "cancelled"

    # Second run: 0 rows updated (idempotent).
    second_rowcount = _run_migration(engine, migration_sql)
    assert second_rowcount == 0, (
        f"Migration is not idempotent: second run updated {second_rowcount} "
        f"rows. The ``status IN ('paused', 'pending')`` guard must prevent "
        f"re-matching the now-cancelled row."
    )

    # Third run for good measure.
    third_rowcount = _run_migration(engine, migration_sql)
    assert third_rowcount == 0
