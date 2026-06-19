"""PostgreSQL concurrency: idempotent enqueue race protection.

Verifies that two concurrent connections racing to enqueue a job with the
same ``idempotency_key`` produce exactly ONE persisted row, not two.

Background
----------
``JobItem`` (table ``job_queue_items``) carries a partial UNIQUE index
``idx_job_idempotency`` on ``(idempotency_key)`` with predicate
``idempotency_key IS NOT NULL AND deleted_at IS NULL``. The
``JobRepository.create_or_get_by_idempotency_key`` method exploits this
index by issuing::

    INSERT INTO job_queue_items (...)
    VALUES (...)
    ON CONFLICT (idempotency_key)
        WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL
    DO NOTHING

so that two callers racing on the same key resolve to exactly one
persisted row (the loser sees ``rowcount == 0`` and returns the winner).
This test exercises that exact INSERT shape under real concurrent
PostgreSQL connections via ``pg_two_connections``.

The partial-index ``WHERE`` clause on ``ON CONFLICT`` is REQUIRED for
PostgreSQL to infer the right index for conflict arbitration. Without
it, PostgreSQL raises ``42P10: there is no unique or exclusion
constraint matching the ON CONFLICT specification``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

# Import the model so SQLModel.metadata is populated and
# ``SQLModel.metadata.create_all`` (run by the ``pg_engine`` session
# fixture) actually emits the ``job_queue_items`` table for this test.
from daemon.repositories.job_queue.models import JobItem  # noqa: F401

TABLE = "job_queue_items"
# Partial unique index predicate — MUST match the index definition in
# ``daemon/repositories/job_queue/models.py`` (``idx_job_idempotency``).
IDX_PREDICATE = "idempotency_key IS NOT NULL AND deleted_at IS NULL"


def _build_insert_sql() -> Any:
    """Return a parameterised INSERT ... ON CONFLICT DO NOTHING statement.

    Includes every NOT NULL column that lacks a DB-level
    ``server_default``. SQLModel ``Field(default=...)`` only sets a
    Python-side default — it does NOT emit ``DEFAULT`` in the DDL, so
    raw SQL inserts must supply a value explicitly. The columns covered
    here match the NOT NULL constraints emitted by ``create_all`` for
    ``JobItem`` (verified against the failure row from a prior run:
    ``source``, ``priority``, ``status``, ``created_at``, ``retry_count``,
    ``job_type`` were all NOT NULL with no DEFAULT).

    Only ``version`` gets a server_default (``server_default="0"``,
    declared on ``_job_item_version_col``); we still pass it for
    explicitness so the test reads as a self-contained INSERT.

    The ``metadata`` column is typed as JSONB (``JSONBType`` decorator);
    we cast the literal ``'{}'::jsonb`` so PG accepts it.

    The ``ON CONFLICT`` clause targets the partial unique index by
    providing the index column + the index predicate. PostgreSQL requires
    the predicate for partial-index inference.
    """
    return text(
        f"""
        INSERT INTO {TABLE} (
            job_id, agent_id, agent_dir, message,
            source, priority, status, created_at,
            retry_count, version, job_type,
            idempotency_key, project_id, metadata
        ) VALUES (
            :job_id, :agent_id, :agent_dir, :message,
            :source, :priority, :status, :created_at,
            :retry_count, :version, :job_type,
            :idempotency_key, :project_id, '{{}}'::jsonb
        )
        ON CONFLICT (idempotency_key) WHERE {IDX_PREDICATE}
        DO NOTHING
        """
    )


def _row_count_sql(idempotency_key: str) -> Any:
    """Return a parameterised COUNT(*) for a given idempotency_key."""
    return text(
        f"SELECT count(*) FROM {TABLE} WHERE idempotency_key = :idempotency_key"
    )


def _select_sql(idempotency_key: str) -> Any:
    """Return a parameterised SELECT to fetch the persisted job_id(s)."""
    return text(
        f"SELECT job_id FROM {TABLE} WHERE idempotency_key = :idempotency_key"
    )


@pytest.mark.postgres
@pytest.mark.parametrize("run", range(5))
def test_concurrent_enqueue_unique_constraint_race(
    pg_two_connections, run
) -> None:
    """Two concurrent INSERTs with the same idempotency_key resolve to one row.

    Scenario
    --------
    * Generate a fresh ``idempotency_key`` and two distinct ``job_id``s.
    * Open two independent connections from ``pg_two_connections``.
    * Both connections attempt the same ``INSERT ... ON CONFLICT DO NOTHING``
      with the shared idempotency_key.
    * Commit connection 1, then commit connection 2.
    * Assert exactly one connection saw ``rowcount == 1`` (the winner) and
      the other saw ``rowcount == 0`` (the loser).
    * Assert the final table contains exactly ONE row with that key,
      and that the persisted ``job_id`` belongs to one of the two
      contenders (proves no third row snuck in).

    The ``run`` parametrize runs this scenario 5× to surface flakiness —
    the partial-index conflict arbitration is deterministic so all 5
    iterations must pass; any flakiness indicates a regression in the
    index predicate or ON CONFLICT clause.

    The shared key is regenerated per-run so each parametrize iteration
    is independent of any prior run.
    """
    idempotency_key = f"race-test-{uuid.uuid4()}"
    job_id_a = str(uuid.uuid4())
    job_id_b = str(uuid.uuid4())
    project_id = f"project-{run}"
    now = datetime.now(timezone.utc).isoformat()

    with pg_two_connections() as (conn1, conn2):
        insert_sql = _build_insert_sql()

        # Both connections attempt to enqueue the same idempotency_key.
        # Each picks its own job_id (the unique key is the idempotency_key,
        # not job_id) so any difference in outcome reflects conflict
        # arbitration on the index, not a PK collision.
        result1 = conn1.execute(
            insert_sql,
            {
                "job_id": job_id_a,
                "agent_id": "race-agent",
                "agent_dir": "/tmp/race-agent",
                "message": "race message",
                "source": "api",
                "priority": 5,
                "status": "pending",
                "created_at": now,
                "retry_count": 0,
                "version": 0,
                "job_type": "task",
                "idempotency_key": idempotency_key,
                "project_id": project_id,
            },
        )
        # Commit conn1 first so the partial-index entry is visible before
        # conn2 attempts its INSERT. This is the realistic interleaving:
        # one writer commits, the second writer arrives and finds the
        # partial-index slot already claimed.
        conn1.commit()
        rowcount1 = result1.rowcount

        result2 = conn2.execute(
            insert_sql,
            {
                "job_id": job_id_b,
                "agent_id": "race-agent",
                "agent_dir": "/tmp/race-agent",
                "message": "race message",
                "source": "api",
                "priority": 5,
                "status": "pending",
                "created_at": now,
                "retry_count": 0,
                "version": 0,
                "job_type": "task",
                "idempotency_key": idempotency_key,
                "project_id": project_id,
            },
        )
        conn2.commit()
        rowcount2 = result2.rowcount

        # Exactly one INSERT must report rowcount==1 (the winner).
        # The other must report rowcount==0 (loser — conflict suppressed).
        rowcounts = sorted([rowcount1, rowcount2])
        assert rowcounts == [0, 1], (
            f"Expected exactly one INSERT to insert (rowcount=1) and the "
            f"other to no-op (rowcount=0); got rowcounts=[{rowcount1}, "
            f"{rowcount2}] for idempotency_key={idempotency_key}"
        )

        # Final state: exactly ONE row with that idempotency_key.
        count = conn1.execute(
            _row_count_sql(idempotency_key), {"idempotency_key": idempotency_key}
        ).scalar()
        assert count == 1, (
            f"Expected exactly one row with idempotency_key="
            f"{idempotency_key}; got {count}"
        )

        # The persisted job_id must be one of the two contenders — no
        # phantom rows, no missing rows.
        persisted_job_ids = {
            row[0]
            for row in conn1.execute(
                _select_sql(idempotency_key),
                {"idempotency_key": idempotency_key},
            ).fetchall()
        }
        assert persisted_job_ids.issubset({job_id_a, job_id_b}), (
            f"Persisted job_id set {persisted_job_ids} does not match "
            f"either contender ({job_id_a}, {job_id_b})"
        )
        assert len(persisted_job_ids) == 1
        assert persisted_job_ids.pop() in {job_id_a, job_id_b}