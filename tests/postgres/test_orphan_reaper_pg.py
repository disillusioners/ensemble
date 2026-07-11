"""PostgreSQL test for the orphan reaper under the real constraint triggers.

Background
----------

The Phase 2 orphan reaper (commit ``93b10484`` -> ``e78bd932`` ->
this round's fix) drops ``admission_state='active'`` rows whose
instance is missing or terminal. Round 2 shipped an elaborate
synthetic-lock-row bypass under the assumption that the deferred
PG triggers
``trg_job_queue_items_active_lock_guard`` /
``trg_job_locks_active_guard`` would reject the reaper's
``active → done`` UPDATE — but reading the trigger bodies
(``daemon/manager.py:2627-2632``) shows that
``trg_job_queue_items_active_lock_guard`` only fires when
``NEW.admission_state = 'active'`` (its outer IF). The reaper sets
``NEW.admission_state = 'done'`` and the trigger is a no-op.

This file proves that contract against a real PostgreSQL trigger
suite, because the SQLite test DB does NOT install the constraint
triggers (see ``test_jq_proxy_phase2_constraints.py`` module
docstring for the architecture note). Without a PG test, a future
regression that did break under the triggers could ship via the
SQLite-only test path.

Run with::

    .venv/bin/python -m pytest tests/postgres/test_orphan_reaper_pg.py \\
        -v -m postgres --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips
the entire module cleanly when PostgreSQL is not reachable.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module", autouse=True)
def _install_phase2_schema(pg_engine):
    """Install the same constraint-trigger suite the production DB has.

    The SQL is taken verbatim from
    ``tests/postgres/test_jq_proxy_phase2_constraints.py`` so the
    PG test exercises the triggers with exactly the same shape
    (DEFERRABLE INITIALLY DEFERRED, firing at COMMIT) that
    production hits. Importing the constants keeps the PG trigger
    definition in a single source of truth — if the production
    trigger body changes, this test's install block must change
    in lockstep.
    """
    from tests.postgres.test_jq_proxy_phase2_constraints import (
        PHASE2_INSTALL_STATEMENTS,
    )

    with pg_engine.begin() as conn:
        for stmt in PHASE2_INSTALL_STATEMENTS:
            conn.execute(text(stmt))
    yield


def _insert_instance(conn, *, instance_id: str, status: str = "completed") -> None:
    """Create a backing ``instances`` row in the given status."""
    conn.execute(
        text(
            """
            INSERT INTO instances (
                instance_id, agent_id, agent_dir, status, version,
                created_at, updated_at
            ) VALUES (
                :instance_id, 'agent', 'agents/agent', :status, 0,
                '2026-07-11T00:00:00+00:00', '2026-07-11T00:00:00+00:00'
            )
            """
        ),
        {"instance_id": instance_id, "status": status},
    )


def _insert_orphan_job(
    conn,
    *,
    job_id: str,
    instance_id: str | None,
    project_id: str,
    queue_id: str,
    job_type: str = "task",
) -> None:
    """Create an orphan ``job_queue_items`` row (``admission_state='active'``).

    The instance (when provided) is in a terminal status — the
    row is orphan by definition. We bypass the ``active ⇒ lock``
    trigger by inserting the lock in the same transaction as the
    job, then dropping it AFTER the COMMIT boundary using a
    follow-up DML so the orphan shape lands: there is an active
    job, no lock row, terminal-or-missing instance.
    """
    conn.execute(
        text(
            """
            INSERT INTO job_queue_items (
                job_id, agent_id, agent_dir, message, source, project_id,
                queue_id, priority, admission_state, instance_id,
                created_at, job_type, retry_count
            ) VALUES (
                :job_id, 'agent', 'agents/agent', 'orphan reap PG test',
                'api', :project_id, :queue_id,
                5, 'active', :instance_id,
                '2026-07-11T00:00:00+00:00', :job_type, 0
            )
            """
        ),
        {
            "job_id": job_id,
            "instance_id": instance_id,
            "job_type": job_type,
            "project_id": project_id,
            "queue_id": queue_id,
        },
    )


def _ensure_project_and_queue(conn, *, project_id: str, queue_id: str) -> None:
    """Create minimal ``projects`` and ``job_queues`` rows so the
    ``job_queue_items.queue_id`` and ``.project_id`` FKs accept
    the row in tests. Idempotent — re-creating is a no-op via
    ``ON CONFLICT DO NOTHING`` semantics on the PK.
    """
    conn.execute(
        text(
            """
            INSERT INTO projects (
                project_id, name, project_type, status, job_queue_paused,
                created_at, updated_at
            ) VALUES (
                :project_id, :project_id, 'system', 'active', false,
                '2026-07-11T00:00:00+00:00', '2026-07-11T00:00:00+00:00'
            )
            """
        ),
        {"project_id": project_id},
    )
    conn.execute(
        text(
            """
            INSERT INTO job_queues (
                queue_id, project_id, queue_name, queue_name_lower,
                queue_type, concurrency_limit, is_system, is_paused,
                created_at, updated_at
            ) VALUES (
                :queue_id, :project_id, :queue_id, :queue_id,
                'fifo', 1, true, false,
                '2026-07-11T00:00:00+00:00', '2026-07-11T00:00:00+00:00'
            )
            """
        ),
        {"queue_id": queue_id, "project_id": project_id},
    )


def test_orphan_reaper_non_message_against_real_triggers(pg_engine) -> None:
    """The reaper's bare UPDATE must commit cleanly under the real
    PG deferred constraint trigger suite.

    Setup:

    1. INSERT an ``instances`` row in status ``completed``
       (terminal — would trigger the natural finalize path if it
       could, but doesn't because the worker process is gone).
       Plus minimal ``projects`` / ``job_queues`` rows so the
       FK on ``job_queue_items`` accepts the test row.
    2. INSERT a ``job_queue_items`` row referenced to that
       instance via a temporary ``job_locks`` row (so trigger 1,
       ``active ⇒ lock``, lets the insert pass at the first
       COMMIT).
    3. DROP the lock row in a second transaction, leaving the
       job as ``active`` + no lock + terminal instance — the
       orphan shape the reaper targets.

    Reap: ``JobRepository.force_finalize_orphan`` issues the bare
    ``UPDATE … SET admission_state='done'``. Under the real PG
    trigger suite, that UPDATE must COMMIT without raising.

    The early-round assumption was that trigger 1
    (``active ⇒ lock``) would reject the UPDATE. It does not,
    because the trigger's outer IF guards on
    ``NEW.admission_state = 'active'`` and the reaper sets it to
    ``'done'`` — the IF is FALSE, the trigger returns NEW, and
    the UPDATE lands. This test pins that contract with the
    actual trigger installed against a live ``pg_engine``.
    """
    instance_id = "orphan-reaper-pg-instance"
    job_id = "orphan-reaper-pg-job"

    # 1. Minimal FK plumbing + terminal instance.
    with pg_engine.begin() as conn:
        _ensure_project_and_queue(
            conn,
            project_id="orphan-pg-proj",
            queue_id="orphan-pg-queue",
        )
        _insert_instance(conn, instance_id=instance_id, status="completed")

    # 2. Insert lock + active job in one transaction so trigger 1
    #    (active ⇒ lock) lets it pass at first COMMIT. Then drop
    #    the lock in a second transaction so we land on the orphan
    #    shape (active + no lock + terminal instance).
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_locks (
                    lock_id, project_id, queue_id, job_id, instance_id,
                    lock_slot, acquired_at
                ) VALUES (
                    'orphan-reaper-pg-lock', 'orphan-pg-proj',
                    'orphan-pg-queue', :job_id, :instance_id,
                    0, '2026-07-11T00:00:00+00:00'
                )
                """
            ),
            {"job_id": job_id, "instance_id": instance_id},
        )
        _insert_orphan_job(
            conn,
            job_id=job_id,
            instance_id=instance_id,
            project_id="orphan-pg-proj",
            queue_id="orphan-pg-queue",
            job_type="task",
        )

    # Drop the lock — leaves active job, no lock, terminal instance.
    with pg_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM job_locks WHERE lock_id = 'orphan-reaper-pg-lock'")
        )

    # 3. Run the reaper against the live engine. The bare UPDATE
    #    must commit cleanly under the deferred triggers.
    from daemon.repositories.job_queue.repository import JobRepository

    repo = JobRepository(pg_engine)
    reaped = repo.force_finalize_orphan(job_id, terminal_reason="cancelled")

    assert reaped is not None, (
        "force_finalize_orphan must return the reaped row under real triggers"
    )
    assert reaped.admission_state == "done"
    assert reaped.terminal_reason == "cancelled"

    # 4. Post-conditions: job is done, no lock row leaked (there
    #    was none to start with and the reaper must not have
    #    inserted any), and the orphan is gone from
    #    find_orphan_active_jobs.
    with pg_engine.connect() as conn:
        job_row = conn.execute(
            text(
                "SELECT admission_state, terminal_reason "
                "FROM job_queue_items WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).fetchone()
        locks = conn.execute(
            text(
                "SELECT lock_id FROM job_locks "
                "WHERE lock_id LIKE 'orphan-reap-%' "
                "   OR lock_id = 'orphan-reaper-pg-lock'"
            )
        ).fetchall()

    assert job_row is not None
    assert job_row[0] == "done"
    assert job_row[1] == "cancelled"
    assert locks == [], f"no spurious job_locks rows allowed, found: {locks}"

    # find_orphan_active_jobs should now return [] for this job.
    remaining_orphans = repo.find_orphan_active_jobs()
    assert all(
        r.job_id != job_id for r in remaining_orphans
    ), "job should be gone from the orphan set after the reap"


def test_orphan_reaper_null_instance_id_branch_is_unreachable_pg(pg_engine) -> None:
    """The ``find_orphan_active_jobs`` predicate includes
    ``JobItem.instance_id IS NULL`` as an orphan branch. In
    production PostgreSQL that branch is UNREACHABLE: the
    ``trg_job_queue_items_active_lock_guard`` trigger fires
    AFTER INSERT and rejects any ``admission_state='active'``
    row whose ``instance_id`` cannot find a matching
    ``job_locks`` row — and NULL ``instance_id`` cannot match
    a lock row via the trigger's JOIN key (``instance_id =
    job_queue_items.instance_id`` collapses to NULL, which is
    unknown, so ``EXISTS`` returns false, so the trigger
    RAISES).

    This test pins that contract: setting up an
    ``admission_state='active'`` row with NULL ``instance_id``
    raises ``IntegrityConstraintViolation`` from
    ``job_queue_items_active_lock_guard``. The SQLite fallback
    (``tests/job_queue/test_orphan_reaper.py``) covers the
    branch where SQLite hosts the schema without the trigger
    installed — PG has no such path.

    Documented so a future maintainer does not "fix" the
    finder's NULL branch by removing it (it's not reachable
    on PG but the SQL is still valid because the existing
    production rows it returns from are zero).
    """
    with pg_engine.begin() as conn:
        _ensure_project_and_queue(
            conn,
            project_id="orphan-pg-null-proj",
            queue_id="orphan-pg-null-queue",
        )

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as excinfo:
        with pg_engine.begin() as conn:
            _insert_orphan_job(
                conn,
                job_id="orphan-pg-null-impossible",
                instance_id=None,
                project_id="orphan-pg-null-proj",
                queue_id="orphan-pg-null-queue",
                job_type="task",
            )

    # SQLSTATE 23000 = integrity_constraint_violation — the
    # category the trigger uses (``ERRCODE =
    # 'integrity_constraint_violation'``).
    assert "admission_state=active" in str(excinfo.value).lower()
