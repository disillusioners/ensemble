"""PostgreSQL-backed tests for the /readyz probe SQL (Auto-Restart Phase 1).

The readiness composite's two engine-bound probes — ``SELECT 1`` and
the SQL-side queue-heartbeat max-age aggregate — are pure SQL against
the real schema. These tests verify both run correctly against the
actual PostgreSQL ``task`` table shape (column names, status literal,
naive-TIMESTAMP session semantics): see
``daemon/services/readiness.py`` for why the age is computed inside
SQL rather than in Python.

Marker: ``postgres`` (auto-applied by ``tests/postgres/conftest.py``);
run with ``pytest tests/postgres/ --override-ini="addopts=" -m postgres``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import daemon.repositories.task.models  # noqa: F401 — register Task in metadata
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.services.readiness import (
    evaluate_queue_freshness,
    make_db_probe,
    make_queue_probe,
)

_TEST_INSTANCE = "readiness-probe-test-instance"


def _insert_task(
    conn,
    *,
    status: str,
    heartbeat: datetime,
    work_id: str,
) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            """
            INSERT INTO task (task_type, instance_id, message_id, status,
                              retry_count, created_at, cancel_requested,
                              retry_scheduled, work_id, is_deferred,
                              is_background, worker_id, started_at,
                              last_heartbeat_at)
            VALUES (:task_type, :instance_id, :message_id, :status,
                    :retry_count, :created_at, :cancel_requested,
                    :retry_scheduled, :work_id, :is_deferred,
                    :is_background, :worker_id, :started_at,
                    :last_heartbeat_at)
            """
        ),
        {
            "task_type": TaskType.PROCESS_MESSAGE.value,
            "instance_id": _TEST_INSTANCE,
            "message_id": None,
            "status": status,
            "retry_count": 0,
            "created_at": now,
            "cancel_requested": False,
            "retry_scheduled": False,
            "work_id": work_id,
            "is_deferred": False,
            "is_background": False,
            "worker_id": None,
            "started_at": now,
            "last_heartbeat_at": heartbeat,
        },
    )


def _clear_tasks(conn) -> None:
    conn.execute(text("DELETE FROM task WHERE instance_id = :iid"), {"iid": _TEST_INSTANCE})


def test_db_probe_select_one(pg_engine):
    """SELECT 1 probe succeeds against the live PG engine."""
    probe = make_db_probe(pg_engine)
    assert probe() is True


def test_queue_probe_empty_running_set_is_none(pg_engine):
    """No RUNNING tasks → MAX() is NULL → age None (= fresh)."""
    with pg_engine.begin() as conn:
        _clear_tasks(conn)

    probe = make_queue_probe(pg_engine)
    assert probe() is None

    fresh, age = evaluate_queue_freshness(None, threshold_seconds=120)
    assert fresh is True
    assert age is None


def test_queue_probe_returns_newest_running_age(pg_engine):
    """Age of the newest RUNNING heartbeat — non-RUNNING rows ignored.

    Also the timezone regression guard: the probe binds an aware-UTC
    timestamp (as the worker heartbeat thread does) and must compute
    an age near 30s, NOT skewed by the PG session timezone (e.g. 7h
    off on a +07 session — the Python-side subtraction bug this
    SQL-side aggregate replaced).
    """
    now = datetime.now(timezone.utc)
    fresh_beat = now - timedelta(seconds=30)
    stale_completed_beat = now - timedelta(seconds=9999)

    with pg_engine.begin() as conn:
        _clear_tasks(conn)
        # A RUNNING task with a 30s-old heartbeat — must set the age.
        _insert_task(
            conn,
            status=TaskStatus.RUNNING.value,
            heartbeat=fresh_beat,
            work_id="wq-running-fresh",
        )
        # A COMPLETED task with a very old heartbeat — must be ignored.
        _insert_task(
            conn,
            status=TaskStatus.COMPLETED.value,
            heartbeat=stale_completed_beat,
            work_id="wq-completed-old",
        )

    age = make_queue_probe(pg_engine)()
    assert age is not None
    assert 25 <= age <= 35, f"30s-old RUNNING heartbeat, got age={age}"

    fresh, evaluated_age = evaluate_queue_freshness(age, threshold_seconds=120)
    assert fresh is True
    assert evaluated_age == age


def test_queue_probe_stale_running_heartbeat_degrades(pg_engine):
    """A RUNNING task whose heartbeat exceeds the threshold degrades."""
    now = datetime.now(timezone.utc)
    stale_beat = now - timedelta(seconds=180)  # > 120s threshold

    with pg_engine.begin() as conn:
        _clear_tasks(conn)
        _insert_task(
            conn,
            status=TaskStatus.RUNNING.value,
            heartbeat=stale_beat,
            work_id="wq-running-stale",
        )

    age = make_queue_probe(pg_engine)()
    assert age is not None
    assert 175 <= age <= 185, f"180s-old RUNNING heartbeat, got age={age}"

    fresh, _ = evaluate_queue_freshness(age, threshold_seconds=120)
    assert fresh is False
