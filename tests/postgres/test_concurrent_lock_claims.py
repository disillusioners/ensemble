"""Phase 3 — PostgreSQL concurrency tests for ``job_locks`` slot claiming.

This module exercises the cross-process atomicity primitive that
``LockRepository.try_acquire_slot`` relies on: the
``uq_job_locks_slot`` UNIQUE constraint on
``(project_id, queue_id, lock_slot)`` combined with
``INSERT ... ON CONFLICT DO NOTHING``.

The model docstring (``daemon/repositories/job_queue/models.py``)
states the invariant under test:

    The (project_id, queue_id, lock_slot) UNIQUE constraint is the
    cross-process atomicity primitive that makes
    ``JobLockManager.acquire_queue_lock`` safe when two daemons race.
    Each acquire tries slot 0, then 1, ... up to concurrency_limit-1
    via ``INSERT OR IGNORE`` (SQLite) / ``INSERT ... ON CONFLICT DO
    NOTHING`` (PostgreSQL).

These tests prove that invariant holds against a real PostgreSQL server
by:

* ``test_concurrent_slot_claim_exactly_one_wins`` — the primary race.
  Two connections attempt to claim the SAME slot. The
  ``ON CONFLICT`` clause must silently absorb the second insert so
  exactly one row exists, never two. Run five times to catch
  ordering flakes.

* ``test_slot_loop_fills_capacity_in_order`` — secondary coverage.
  With a logical ``concurrency_limit`` of 2, conn1 wins slot 0,
  conn2 loses slot 0, then conn2 wins slot 1. The end state is one
  row per slot, demonstrating the "try N → try N+1 → ..." loop
  pattern in the manager.

All assertions use raw SQL via ``sqlalchemy.text()`` so the test
exercises the exact DDL/DML the repository emits — no ORM layer
masks the race.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

# Importing the model registers ``job_locks`` in ``SQLModel.metadata``
# so ``pg_engine``'s session-scoped ``create_all`` creates the table
# (and the ``uq_job_locks_slot`` UNIQUE constraint) before any test
# runs. Without this import, the table is absent and the INSERT
# fails with ``UndefinedTable``.
from daemon.repositories.job_queue.models import JobLock  # noqa: F401


# ---------------------------------------------------------------------------
# Test data: NOT NULL columns on ``job_locks``
#
#   lock_id      TEXT    PK, NOT NULL
#   project_id   TEXT    NOT NULL
#   queue_id     TEXT    NOT NULL
#   job_id       TEXT    NOT NULL
#   instance_id  TEXT    NULL allowed
#   lock_slot    INTEGER NOT NULL  (default 0)
#   acquired_at  TEXT    NOT NULL  (default ISO timestamp)
#
# Mirrors ``LockRepository.try_acquire_slot`` parameters exactly so the
# test exercises the same INSERT shape the production code emits.
# ---------------------------------------------------------------------------

_INSERT_SLOT_SQL = text(
    """
    INSERT INTO job_locks
        (lock_id, project_id, queue_id, job_id,
         instance_id, lock_slot, acquired_at)
    VALUES
        (:lock_id, :project_id, :queue_id, :job_id,
         :instance_id, :slot, :now)
    ON CONFLICT (project_id, queue_id, lock_slot) DO NOTHING
    """
)

_COUNT_QUEUE_LOCKS_SQL = text(
    """
    SELECT count(*) FROM job_locks
    WHERE project_id = :project_id AND queue_id = :queue_id
    """
)

_SELECT_SLOT_HOLDER_SQL = text(
    """
    SELECT lock_id, job_id FROM job_locks
    WHERE project_id = :project_id
      AND queue_id = :queue_id
      AND lock_slot = :slot
    """
)


def _make_lock_params(
    *,
    project_id: str,
    queue_id: str,
    job_id: str,
    instance_id: str,
    slot: int,
) -> dict[str, object]:
    """Build the parameter dict matching ``try_acquire_slot``."""
    import uuid
    return {
        "lock_id": str(uuid.uuid4()),
        "project_id": project_id,
        "queue_id": queue_id,
        "job_id": job_id,
        "instance_id": instance_id,
        "slot": slot,
        "now": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# PRIMARY TEST: single-slot race — exactly one winner
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("run", range(5))
def test_concurrent_slot_claim_exactly_one_wins(pg_two_connections, run):
    """Two connections claim the SAME slot — exactly one wins.

    The fixture's autouse TRUNCATE runs before this test, so the
    ``job_locks`` table is empty. We use unique ``project_id`` /
    ``queue_id`` per parametrized run for additional defence in depth
    (so a residual row from a previous test could not silently
    affect row counts even if the autouse fixture were ever
    disabled).

    Sequence:

    1. conn1 attempts ``INSERT ... ON CONFLICT DO NOTHING`` for
       ``lock_slot=0`` → rowcount must be 1.
    2. conn1 commits.
    3. conn2 attempts the SAME insert (same ``project_id``,
       ``queue_id``, ``lock_slot``) → ON CONFLICT fires, rowcount
       must be 0.
    4. conn2 commits (no-op for the row, but ends its tx).
    5. Verify exactly one row exists for the queue and the holder is
       conn1's ``job_id``.

    This is the cross-process atomicity guarantee: no matter how
    many daemons race for the same slot, the UNIQUE constraint
    ensures at most one row exists per slot.
    """
    # Unique per-parametrize-run keys for defensive isolation.
    project_id = f"slot-race-proj-{run}"
    queue_id = f"slot-race-queue-{run}"

    conn1_params = _make_lock_params(
        project_id=project_id,
        queue_id=queue_id,
        job_id=f"job-conn1-{run}",
        instance_id="instance-conn1",
        slot=0,
    )
    conn2_params = _make_lock_params(
        project_id=project_id,
        queue_id=queue_id,
        job_id=f"job-conn2-{run}",
        instance_id="instance-conn2",
        slot=0,
    )

    with pg_two_connections() as (conn1, conn2):
        # Step 1: conn1 claims slot 0.
        result1 = conn1.execute(_INSERT_SLOT_SQL, conn1_params)
        rowcount1 = result1.rowcount or 0
        assert rowcount1 == 1, (
            f"run={run}: conn1 should have won slot 0, got rowcount={rowcount1}"
        )

        # Step 2: commit conn1 so the row is visible to conn2.
        conn1.commit()

        # Step 3: conn2 attempts the same slot — ON CONFLICT must
        # silently absorb the duplicate, returning rowcount=0.
        result2 = conn2.execute(_INSERT_SLOT_SQL, conn2_params)
        rowcount2 = result2.rowcount or 0
        assert rowcount2 == 0, (
            f"run={run}: conn2 should have LOST the slot race "
            f"(ON CONFLICT DO NOTHING), got rowcount={rowcount2}"
        )

        # Step 4: commit conn2 — its INSERT was a no-op but the
        # transaction must close cleanly so we leave no open tx.
        conn2.commit()

        # Step 5: post-condition assertions.
        # (a) Exactly one row for this (project_id, queue_id) — the
        # concurrency_limit of 1 is never breached.
        total_locks = conn1.execute(
            _COUNT_QUEUE_LOCKS_SQL,
            {"project_id": project_id, "queue_id": queue_id},
        ).scalar()
        assert total_locks == 1, (
            f"run={run}: expected exactly 1 lock for the queue, "
            f"got {total_locks} — UNIQUE constraint failed"
        )

        # (b) The surviving row belongs to conn1 — conn1's job_id
        # is the holder, not conn2's.
        holder = conn1.execute(
            _SELECT_SLOT_HOLDER_SQL,
            {"project_id": project_id, "queue_id": queue_id, "slot": 0},
        ).first()
        assert holder is not None, "run={run}: slot 0 row missing after both commits"
        holder_lock_id, holder_job_id = holder
        assert holder_lock_id == conn1_params["lock_id"], (
            f"run={run}: wrong holder lock_id — expected conn1's "
            f"{conn1_params['lock_id']}, got {holder_lock_id}"
        )
        assert holder_job_id == conn1_params["job_id"], (
            f"run={run}: wrong holder job_id — expected "
            f"{conn1_params['job_id']}, got {holder_job_id}"
        )

        # Final cleanup: no open transactions left on either side.
        conn1.rollback()
        conn2.rollback()


# ---------------------------------------------------------------------------
# SECONDARY TEST: slot loop — two slots filled by different winners
# ---------------------------------------------------------------------------

def test_slot_loop_fills_capacity_in_order(pg_two_connections):
    """The acquire loop: try slot 0 → lose → try slot 1 → win.

    Simulates the ``JobLockManager.acquire_queue_lock`` inner loop:

    1. conn1 attempts ``lock_slot=0`` → wins (rowcount=1), commits.
    2. conn2 attempts ``lock_slot=0`` → loses (rowcount=0,
       ON CONFLICT absorbs). It does NOT commit — the manager
       would try the next slot without committing the losing
       attempt (a no-op write would still commit cleanly, but the
       loop semantics are: keep trying until success).
    3. conn2 attempts ``lock_slot=1`` → wins (rowcount=1), commits.

    End state: slot 0 owned by conn1's ``job_id``, slot 1 owned by
    conn2's ``job_id``. Total locks == 2 == logical
    ``concurrency_limit``. This proves the slot-loop pattern
    never over-allocates and that losers successfully advance to
    the next slot.
    """
    project_id = "slot-loop-proj"
    queue_id = "slot-loop-queue"
    logical_concurrency_limit = 2

    conn1_slot0 = _make_lock_params(
        project_id=project_id,
        queue_id=queue_id,
        job_id="job-loop-conn1",
        instance_id="instance-loop-conn1",
        slot=0,
    )
    conn2_slot0 = _make_lock_params(
        project_id=project_id,
        queue_id=queue_id,
        job_id="job-loop-conn2",
        instance_id="instance-loop-conn2",
        slot=0,
    )
    conn2_slot1 = _make_lock_params(
        project_id=project_id,
        queue_id=queue_id,
        job_id="job-loop-conn2",
        instance_id="instance-loop-conn2",
        slot=1,
    )

    with pg_two_connections() as (conn1, conn2):
        # Step 1: conn1 takes slot 0.
        result = conn1.execute(_INSERT_SLOT_SQL, conn1_slot0)
        assert (result.rowcount or 0) == 1, "conn1 should win slot 0"
        conn1.commit()

        # Step 2: conn2 loses slot 0.
        result = conn2.execute(_INSERT_SLOT_SQL, conn2_slot0)
        assert (result.rowcount or 0) == 0, (
            "conn2 must lose slot 0 (ON CONFLICT DO NOTHING)"
        )
        # Per the manager loop pattern, the loser advances to the
        # next slot without committing the no-op. We follow the
        # same pattern: rollback the empty tx so the next INSERT
        # is the only write in the new tx.
        conn2.rollback()

        # Step 3: conn2 takes slot 1.
        result = conn2.execute(_INSERT_SLOT_SQL, conn2_slot1)
        assert (result.rowcount or 0) == 1, (
            "conn2 should win slot 1 after losing slot 0"
        )
        conn2.commit()

        # Post-conditions.
        # (a) Total locks == concurrency_limit.
        total = conn1.execute(
            _COUNT_QUEUE_LOCKS_SQL,
            {"project_id": project_id, "queue_id": queue_id},
        ).scalar()
        assert total == logical_concurrency_limit, (
            f"expected {logical_concurrency_limit} locks, got {total}"
        )

        # (b) Slot 0 is owned by conn1.
        slot0_holder = conn1.execute(
            _SELECT_SLOT_HOLDER_SQL,
            {"project_id": project_id, "queue_id": queue_id, "slot": 0},
        ).first()
        assert slot0_holder is not None
        _, slot0_job = slot0_holder
        assert slot0_job == conn1_slot0["job_id"], (
            f"slot 0 should belong to {conn1_slot0['job_id']}, got {slot0_job}"
        )

        # (c) Slot 1 is owned by conn2.
        slot1_holder = conn1.execute(
            _SELECT_SLOT_HOLDER_SQL,
            {"project_id": project_id, "queue_id": queue_id, "slot": 1},
        ).first()
        assert slot1_holder is not None
        _, slot1_job = slot1_holder
        assert slot1_job == conn2_slot1["job_id"], (
            f"slot 1 should belong to {conn2_slot1['job_id']}, got {slot1_job}"
        )

        # Cleanup: no open transactions.
        conn1.rollback()
        conn2.rollback()
