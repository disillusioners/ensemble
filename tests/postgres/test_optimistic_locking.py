"""PostgreSQL concurrency tests — Scenario 5: Optimistic Locking.

This module validates the **``version_id_col``** optimistic-locking pattern
implemented by ``daemon/repositories/job_queue/models.py:JobItem`` (and
mirrored by ``daemon/repositories/task/models.py:Task``). The pattern is:

    UPDATE <table> SET ..., version = version + 1
    WHERE <pk> = :id AND version = :expected_version

When two transactions read the same row at ``version = N`` and both
attempt to update, only the first commit wins. The second one's
``WHERE version = N`` predicate matches zero rows because version has
already been bumped to ``N + 1``. This is the application-level version
guard that supplements SQLAlchemy's ORM-level
``__mapper_args__ = {"version_id_col": <col>}`` — the mapper auto-emits
the same ``AND version = :expected_version`` predicate on ORM-flushed
UPDATEs and raises ``StaleDataError`` on a concurrent modification.

What this test asserts:

* conn1's UPDATE under ``WHERE version = 0`` affects exactly **1** row.
* conn2's UPDATE under the **stale** ``WHERE version = 0`` (issued after
  conn1's commit bumped version to 1) affects exactly **0** rows.
* After both commits, the row's ``version`` column is **1**, not 2.

The test runs 5 times via ``@pytest.mark.parametrize("run", range(5))``
to catch flakes from connection-pooling timing or commit ordering.

Only runs via ``pytest -m postgres`` (opt-in, see ``pyproject.toml``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

# Import the JobItem model so ``SQLModel.metadata.create_all`` (run by
# the ``pg_engine`` fixture) registers the ``job_queue_items`` table in
# the PG schema. The test only uses raw SQL, but the table must exist
# in the database for the INSERT/UPDATE statements to succeed.
from daemon.repositories.job_queue.models import JobItem  # noqa: F401


# The table that ``JobItem.__mapper_args__ = {"version_id_col": ...}`` is
# configured for (see ``daemon/repositories/job_queue/models.py:220``).
TABLE = "job_queue_items"
PK_COL = "job_id"
VERSION_COL = "version"
STATUS_COL = "status"
PENDING_STATUS = "pending"
PROCESSING_STATUS = "processing"

# Minimal INSERT — only NOT NULL columns plus a hard-coded empty JSONB
# for the ``metadata`` column. The model defines several NOT NULL columns
# whose ``default`` / ``default_factory`` is applied at the Python level
# only (e.g. ``created_at``, ``job_type``, ``retry_count``, ``version``),
# so we must supply them explicitly in raw SQL.
_INSERT_SQL = text(
    f"""
    INSERT INTO {TABLE} (
        {PK_COL}, agent_id, agent_dir, message, source,
        project_id, queue_id, priority, {STATUS_COL},
        created_at, job_type, retry_count, {VERSION_COL}, metadata
    ) VALUES (
        :{PK_COL}, :agent_id, :agent_dir, :message, :source,
        :project_id, :queue_id, :priority, :{STATUS_COL},
        :created_at, :job_type, :retry_count, :{VERSION_COL},
        '{{}}'::jsonb
    )
    """
)

# The optimistic-lock UPDATE: guard the write with the expected version
# and bump version on success. Mirrors the predicate the daemon's
# repository uses (e.g. ``JobRepository.atomic_transition``) plus the
# ``version = version + 1`` auto-bump that ``version_id_col`` would emit
# on an ORM-flushed UPDATE.
_BUMP_VERSION_SQL = text(
    f"""
    UPDATE {TABLE}
    SET {STATUS_COL} = :{STATUS_COL}, {VERSION_COL} = {VERSION_COL} + 1
    WHERE {PK_COL} = :{PK_COL} AND {VERSION_COL} = :expected_version
    """
)

_READ_VERSION_SQL = text(
    f"SELECT {VERSION_COL} FROM {TABLE} WHERE {PK_COL} = :{PK_COL}"
)


def _insert_job_item(conn, *, job_id: str, version: int = 0) -> None:
    """Insert a minimal ``job_queue_items`` row at the given starting version.

    Only NOT NULL columns are populated; everything else falls through
    to the column defaults (e.g. ``status='pending'``, ``priority=5``).
    """
    conn.execute(
        _INSERT_SQL,
        {
            PK_COL: job_id,
            "agent_id": "test-agent",
            "agent_dir": "/tmp/test-agent",
            "message": "optimistic-locking-probe",
            "source": "api",
            "project_id": "test-project",
            "queue_id": None,
            "priority": 5,
            STATUS_COL: PENDING_STATUS,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "job_type": "task",
            "retry_count": 0,
            VERSION_COL: version,
        },
    )


@pytest.mark.parametrize("run", range(5))
def test_version_guard_blocks_stale_concurrent_update(
    pg_two_connections,
    run: int,
) -> None:
    """Two concurrent UPDATEs at the same ``version`` — only the first wins.

    Sequence:

    1. Insert a row with ``version = 0``.
    2. conn1 ``UPDATE … WHERE job_id = :id AND version = 0`` → rowcount=1.
       Commit.
    3. conn2 ``UPDATE … WHERE job_id = :id AND version = 0`` → rowcount=0
       (stale version — version is now 1 after conn1's commit).
       Commit.
    4. Final ``version`` is 1.

    The ``WHERE version = 0`` guard is the optimistic-lock check that
    ``__mapper_args__ = {"version_id_col": ...}`` auto-emits on
    ORM-flushed UPDATEs (raising ``StaleDataError`` instead of silently
    matching zero rows, but the effect is the same: the second writer
    loses).
    """
    job_id = str(uuid.uuid4())

    with pg_two_connections() as (conn1, conn2):
        try:
            # --- Setup: insert a row with version=0 ------------------------
            _insert_job_item(conn1, job_id=job_id, version=0)
            conn1.commit()

            # --- Step 1: conn1 wins the race at version=0 ------------------
            conn1_rowcount = conn1.execute(
                _BUMP_VERSION_SQL,
                {
                    STATUS_COL: PROCESSING_STATUS,
                    PK_COL: job_id,
                    "expected_version": 0,
                },
            ).rowcount
            conn1.commit()

            assert conn1_rowcount == 1, (
                f"conn1 expected to bump 1 row at version=0, "
                f"got rowcount={conn1_rowcount} (run={run})"
            )

            # --- Step 2: conn2 tries the same bump with the stale version -
            # After conn1's commit, the row is now at version=1, so
            # conn2's ``WHERE version = 0`` predicate matches nothing.
            conn2_rowcount = conn2.execute(
                _BUMP_VERSION_SQL,
                {
                    STATUS_COL: PROCESSING_STATUS,
                    PK_COL: job_id,
                    "expected_version": 0,
                },
            ).rowcount
            conn2.commit()

            assert conn2_rowcount == 0, (
                f"conn2 expected stale version=0 update to affect 0 rows, "
                f"got rowcount={conn2_rowcount} — optimistic lock NOT enforced "
                f"(run={run})"
            )

            # --- Step 3: verify final state is version=1 -------------------
            final_version = conn2.execute(
                _READ_VERSION_SQL, {PK_COL: job_id}
            ).scalar()
        finally:
            # Defensive: roll back any leftover transaction so the
            # autouse TRUNCATE fixture between tests does not deadlock
            # on a still-open transaction.
            for c in (conn1, conn2):
                try:
                    c.rollback()
                except Exception:
                    pass

    assert final_version == 1, (
        f"expected final version=1 after conn1's commit, got {final_version} "
        f"(run={run})"
    )
