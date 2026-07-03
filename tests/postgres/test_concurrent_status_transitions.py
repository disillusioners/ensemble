"""PostgreSQL concurrency tests for status-transition race conditions.

These tests validate that the WHERE-guard pattern in
``JobRepository.atomic_transition`` (and the underlying
``UPDATE job_queue_items SET admission_state = :to WHERE job_id = :id AND admission_state = :from``
SQL) is genuinely safe under PostgreSQL READ COMMITTED + EvalPlanQual (EPQ).
They run only via ``pytest -m postgres`` and exercise real cross-connection
concurrency with raw ``sqlalchemy.text()`` statements.

Scenarios:
1. **Atomic Status Transitions** (test_atomic_status_transition_where_guard)
   — Two connections each attempt the same guarded UPDATE. PostgreSQL's
   row-level lock + WHERE guard serialises them so exactly one wins and
   the other observes ``rowcount == 0``.

2. **EvalPlanQual Re-evaluation** (test_evalplanqual_re_evaluation) — A
   connection that pre-snapshotted a row in its own autobegun transaction
   is forced to re-evaluate the WHERE guard after a peer commits a
   conflicting change. This is the critical safety net that prevents the
   original TOCTOU clobber identified during the concurrency audit: even
   if a caller was already inside a transaction with a stale snapshot,
   EPQ ensures the row-level predicate is re-checked against the
   post-commit row version.

Both scenarios are parametrised over ``range(5)`` to flush out flakiness
from a single test execution.

Status string values are pinned to the literals actually used by
``AdmissionState`` (``queued`` → ``active``); the table name matches
``JobItem.__tablename__`` (``job_queue_items``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

# Import the JobItem model so its ``Table`` is registered on
# ``SQLModel.metadata`` before the session-scoped ``pg_engine`` fixture
# runs ``SQLModel.metadata.create_all(engine)``. Without this import, the
# ``job_queue_items`` table would not exist in the test database.
from daemon.repositories.job_queue.models import JobItem  # noqa: F401


# ---------------------------------------------------------------------------
# Cross-test trigger isolation
# ---------------------------------------------------------------------------
# ``test_jq_proxy_phase2_constraints._install_phase2_schema`` is a
# session-scoped autouse fixture that installs
# ``trg_job_queue_items_active_lock_guard`` (after-update-of-
# admission_state constraint trigger) on the ``job_queue_items`` table.
# That trigger fires whenever ``admission_state`` is flipped to
# ``'active'`` and demands a matching ``job_locks`` row — fine for the
# Phase 2 tests, but these tests exercise ONLY the WHERE-guarded
# UPDATE-atomicity and EvalPlanQual re-evaluation primitives without
# any ``job_locks`` setup, so the trigger would incorrectly fire on
# commit and break the suite.
#
# Drop the trigger in a function-scoped autouse fixture BEFORE each
# test runs (the Phase 2 fixture installs it again on its next
# collection, so the rest of the suite is unaffected).
@pytest.fixture(autouse=True)
def _drop_job_queue_items_active_lock_guard_trigger(pg_engine):
    """Drop the cross-test trigger from ``job_queue_items`` before each test.

    Idempotent: ``DROP TRIGGER IF EXISTS`` succeeds whether or not the
    trigger was previously installed by the session-scoped Phase 2
    fixture.
    """
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "DROP TRIGGER IF EXISTS trg_job_queue_items_active_lock_guard "
                "ON job_queue_items"
            )
        )
    yield

# Status literals — verbatim from ``AdmissionState`` in
# ``daemon/repositories/job_queue/models.py``. Hard-coded here so this
# test file is decoupled from the model module (raw SQL only).
# Phase 5 cleanup: ``status`` was dropped in favor of ``admission_state``
# (``queued`` / ``active`` / ``done`` / ``dead``).
STATUS_QUEUED = "queued"
STATUS_ACTIVE = "active"

# Table name — verbatim from ``JobItem.__tablename__``.
TABLE = "job_queue_items"


# --------------------------------------------------------
# Helpers
# --------------------------------------------------------

def _insert_pending_job(conn, *, status: str = STATUS_QUEUED) -> str:
    """Insert a single job row and return its freshly minted ``job_id``.

    Provides every NOT NULL column. The model defines Python-side
    defaults for several columns (``source``, ``priority``,
    ``admission_state``, ``created_at``, ``job_type``, ``retry_count``)
    but **no ``server_default``**, so the corresponding PostgreSQL columns
    are declared NOT NULL with no DB-side fallback. A raw ``INSERT`` must
    therefore supply every NOT NULL value explicitly — SQLAlchemy would
    normally apply the Python defaults, but raw ``text()`` does not.

    The INSERT runs inside the connection's current transaction; the
    caller is responsible for committing (or rolling back).
    """
    job_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        text(
            f"INSERT INTO {TABLE} "
            "(job_id, agent_id, agent_dir, message, source, priority, "
            "admission_state, created_at, job_type, retry_count, version) "
            "VALUES (:job_id, :agent_id, :agent_dir, :message, :source, "
            ":priority, :admission_state, :created_at, :job_type, "
            ":retry_count, :version)"
        ),
        {
            "job_id": job_id,
            "agent_id": "concurrent-test-agent",
            "agent_dir": "/tmp/concurrent-test",
            "message": "concurrent status transition test",
            "source": "api",
            "priority": 5,
            "admission_state": status,
            "created_at": created_at,
            "job_type": "task",
            "retry_count": 0,
            "version": 0,
        },
    )
    return job_id


def _current_status(conn, job_id: str) -> str | None:
    """Read the current ``admission_state`` of a job via the supplied connection.

    Honours the caller's transaction state: if the connection has an
    open autobegun transaction, the SELECT will see that transaction's
    snapshot. Use ``conn.commit()`` (or ``conn.rollback()``) first if a
    fresh post-commit read is required.
    """
    return conn.execute(
        text(f"SELECT admission_state FROM {TABLE} WHERE job_id = :job_id"),
        {"job_id": job_id},
    ).scalar()


# --------------------------------------------------------
# Scenario 1: WHERE-guard atomicity under concurrent UPDATEs
# --------------------------------------------------------

@pytest.mark.parametrize("run", range(5))
def test_atomic_status_transition_where_guard(pg_two_connections, run):
    """Two connections race the same guarded UPDATE; exactly one wins.

    Sequence:
        1. Insert a fresh pending job on conn1 and commit so both
           connections can see it.
        2. conn1 issues
           ``UPDATE job_queue_items SET admission_state='active'
            WHERE job_id = :id AND admission_state = 'queued'``,
           commits.
        3. conn2 issues the *same* UPDATE. Because the row is no
           longer pending, the WHERE guard filters it out:
           ``rowcount == 0``.

    This is the steady-state case the WHERE-guard pattern protects
    against: two workers that arrive at the gate at the same time and
    both believe the job is pending. The row lock + WHERE predicate
    means exactly one mutates the row, the other no-ops cleanly.
    """
    with pg_two_connections() as (conn1, conn2):
        job_id = _insert_pending_job(conn1)
        conn1.commit()

        # Step 2: conn1 wins the race.
        result1 = conn1.execute(
            text(
                f"UPDATE {TABLE} "
                "SET admission_state = :to_status "
                "WHERE job_id = :job_id AND admission_state = :from_status"
            ),
            {
                "to_status": STATUS_ACTIVE,
                "job_id": job_id,
                "from_status": STATUS_QUEUED,
            },
        )
        conn1.commit()
        assert result1.rowcount == 1, (
            f"run={run}: conn1 should have transitioned the queued job, "
            f"got rowcount={result1.rowcount}"
        )

        # Step 3: conn2 races the same guard. The row is already
        # 'active' so the WHERE no longer matches.
        result2 = conn2.execute(
            text(
                f"UPDATE {TABLE} "
                "SET admission_state = :to_status "
                "WHERE job_id = :job_id AND admission_state = :from_status"
            ),
            {
                "to_status": STATUS_ACTIVE,
                "job_id": job_id,
                "from_status": STATUS_QUEUED,
            },
        )
        # No commit needed — we're inspecting rowcount only. Roll back
        # so conn2 is left in a clean state for the verification read
        # below.
        conn2.rollback()

        assert result2.rowcount == 0, (
            f"run={run}: conn2's WHERE guard should have filtered the "
            f"now-claimed row, got rowcount={result2.rowcount}"
        )

        # Final state check: row is 'active', not corrupted.
        final = _current_status(conn2, job_id)
        assert final == STATUS_ACTIVE, (
            f"run={run}: final admission_state should be '{STATUS_ACTIVE}', "
            f"got '{final}'"
        )


# --------------------------------------------------------
# Scenario 2: TRUE EvalPlanQual re-evaluation
# --------------------------------------------------------

@pytest.mark.parametrize("run", range(5))
def test_evalplanqual_re_evaluation(pg_two_connections, run):
    """EPQ invalidates a stale snapshot when a peer commits first.

    Sequence:
        1. conn1's autobegun transaction SELECTs the row and snapshots
           ``admission_state='queued'`` (the row's pre-update version, taken
           under READ COMMITTED).
        2. conn2 UPDATEs the row to ``active`` and commits.
        3. conn1 attempts the same guarded UPDATE. PostgreSQL's
           EvalPlanQual re-checks the WHERE clause against conn2's
           now-committed row version; ``admission_state='queued'`` no
           longer matches → ``rowcount == 0``.
        4. conn1 rolls back its (now empty) transaction.

    This is the **critical** safety net. It proves that even if a
    caller was already mid-transaction with a stale snapshot of the
    job's admission_state, PostgreSQL's row-level recheck prevents the
    guarded UPDATE from clobbering a peer's commit. Without EPQ, the
    original TOCTOU bug (SELECT → Python check → UPDATE) could re-emerge
    any time a caller does a non-trivial amount of work between the
    state read and the state write.
    """
    with pg_two_connections() as (conn1, conn2):
        job_id = _insert_pending_job(conn1)
        conn1.commit()

        # Step 1: conn1's autobegun transaction snapshots 'queued'.
        # A plain SELECT does NOT take a row lock, so conn2 is free
        # to proceed immediately.
        snapshot_status = _current_status(conn1, job_id)
        assert snapshot_status == STATUS_QUEUED, (
            f"run={run}: conn1's initial snapshot should be "
            f"'{STATUS_QUEUED}', got '{snapshot_status}'"
        )

        # Step 2: conn2 wins the race and commits.
        result2 = conn2.execute(
            text(
                f"UPDATE {TABLE} "
                "SET admission_state = :to_status "
                "WHERE job_id = :job_id AND admission_state = :from_status"
            ),
            {
                "to_status": STATUS_ACTIVE,
                "job_id": job_id,
                "from_status": STATUS_QUEUED,
            },
        )
        conn2.commit()
        assert result2.rowcount == 1, (
            f"run={run}: conn2 should have claimed the pending job, "
            f"got rowcount={result2.rowcount}"
        )

        # Step 3: conn1 attempts the same guarded UPDATE. PostgreSQL
        # notices the row was modified since conn1's snapshot was
        # taken and runs EvalPlanQual to re-evaluate the WHERE clause
        # against the post-commit version. ``admission_state`` is no
        # longer 'queued', so the UPDATE affects 0 rows.
        result1 = conn1.execute(
            text(
                f"UPDATE {TABLE} "
                "SET admission_state = :to_status "
                "WHERE job_id = :job_id AND admission_state = :from_status"
            ),
            {
                "to_status": STATUS_ACTIVE,
                "job_id": job_id,
                "from_status": STATUS_QUEUED,
            },
        )
        # Roll back conn1 — it has nothing to commit, but we must
        # leave the connection in a clean state.
        conn1.rollback()

        assert result1.rowcount == 0, (
            f"run={run}: EPQ should have invalidated conn1's stale "
            f"snapshot, got rowcount={result1.rowcount}. "
            f"EvalPlanQual is not re-evaluating the WHERE guard as "
            f"expected — this would re-open the TOCTOU window."
        )

        # Final state check: conn2's commit stands. Read via conn2
        # (now in a clean autobegun state after its prior commit).
        final = _current_status(conn2, job_id)
        assert final == STATUS_ACTIVE, (
            f"run={run}: final admission_state should be '{STATUS_ACTIVE}', "
            f"got '{final}'"
        )
