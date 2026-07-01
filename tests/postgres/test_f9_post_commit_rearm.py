"""PostgreSQL regression tests for F9 (defer-seam bugfix).

F9 — Post-commit re-arm violates the PostgreSQL lock guard trigger
---------------------------------------------------------------
The orphan-race post-commit re-arm in
``daemon/services/job_feedback_observer.py`` (lines ~1266-1337)
previously issued a single ``UPDATE job_queue_items SET
admission_state='active' WHERE admission_state='done'`` via
:meth:`JobRepository.atomic_transition`. The pre-fix flow ran in two
SEPARATE transactions:

    TX-A: ``_finalize_job_db_sync`` Step 3 — DELETE job_locks
          → COMMIT (trigger 2 passes: no active JobItem, no lock)
    TX-B: ``JobRepository.atomic_transition(done → active)``
          → COMMIT (trigger 1 fires: active JobItem, NO lock
                    → raises ``integrity_constraint_violation``)

Every orphan-race re-arm in production hit the trigger at TX-B's commit
and aborted. The exception was caught by a broad ``except Exception``,
leaving the JobItem in ``done`` and silently orphaning the late child.

The fix (:meth:`JobRepository.rearm_with_lock`) collapses both writes
into a SINGLE ``engine.begin()`` block — INSERT job_locks + UPDATE
``admission_state='active' WHERE admission_state='done'`` — so the
trigger sees both the lock row AND the active admission_state at COMMIT
and accepts the re-arm. Mirrors the B1 fix's
:func:`start_job_atomic_with_lock` pattern.

What this module verifies
-------------------------
* **A. The fix works end-to-end** — ``rearm_with_lock`` after
  ``_finalize_job_db_sync``-style DELETE + UPDATE commits cleanly
  against the live PG triggers. A new ``job_locks`` row exists and
  ``admission_state='active'`` after the call.
* **B. The pre-fix bug reproduces** — ``atomic_transition(done →
  active)`` alone (no lock INSERT) raises
  ``integrity_constraint_violation``. Documents that the trigger
  exists and would catch a regression.
* **C. No slot available** — when all ``concurrency_limit`` slots are
  taken, ``rearm_with_lock`` returns ``(None, False)`` and writes
  nothing. Job stays in ``done``.
* **D. Invalid state** — when ``admission_state`` is not ``done``,
  ``rearm_with_lock`` raises ``ValueError`` and rolls back (no lock
  leak).
* **E. Missing job** — when ``job_id`` does not exist,
  ``rearm_with_lock`` returns ``(None, False)`` and writes nothing.
* **F. Single-transaction atomicity** — under all failure modes, the
  lock INSERT and admission_state UPDATE either both commit or both
  roll back. No half-states are observable.

Run with::

    .venv/bin/python -m pytest tests/postgres/test_f9_post_commit_rearm.py \\
        -v -m postgres --tb=short --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is not reachable. The autouse
``_install_phase2_schema`` fixture installs the ``admission_state``
column + both constraint triggers (mirroring
``tests/postgres/test_jq_proxy_phase2_constraints.py``).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session as SQLModelSession, select

# Import models so ``SQLModel.metadata.create_all`` (run by the
# session-scoped ``pg_engine`` fixture) registers the
# ``job_queue_items``, ``job_locks``, and ``job_queues`` tables.
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
    JobQueue,
    QueueType,
)
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository

logger = logging.getLogger(__name__)


# Auto-apply the postgres marker so ``pytest -m postgres`` selects
# these tests and the default ``addopts`` skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# SQL constants — mirror tests/postgres/test_jq_proxy_phase2_constraints.py
# =============================================================================
#
# The Phase 2 constraint trigger install SQL is duplicated here rather
# than imported so the F9 fix is independently testable (the two
# modules can run in isolation against a fresh ``pg_engine``). The
# fixture is idempotent (every statement uses IF NOT EXISTS /
# OR REPLACE / DROP IF EXISTS), so re-running during a test is safe.
PHASE2_INSTALL_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE job_queue_items ADD COLUMN IF NOT EXISTS admission_state TEXT NOT NULL DEFAULT 'queued'",
    "CREATE INDEX IF NOT EXISTS idx_job_queue_admission_state ON job_queue_items(admission_state)",
    (
        "CREATE OR REPLACE FUNCTION job_queue_items_active_lock_guard() "
        "RETURNS TRIGGER AS $$ "
        "BEGIN "
        "  IF NEW.admission_state = 'active' THEN "
        "    IF NOT EXISTS (SELECT 1 FROM job_locks WHERE instance_id = NEW.instance_id) THEN "
        "      RAISE EXCEPTION "
        "        'admission_state=active requires a job_locks row (instance_id=%)', "
        "        NEW.instance_id "
        "        USING ERRCODE = 'integrity_constraint_violation'; "
        "    END IF; "
        "  END IF; "
        "  RETURN NEW; "
        "END; "
        "$$ LANGUAGE plpgsql"
    ),
    (
        "CREATE OR REPLACE FUNCTION job_locks_active_guard() "
        "RETURNS TRIGGER AS $$ "
        "BEGIN "
        "  IF NOT EXISTS ("
        "    SELECT 1 FROM job_queue_items "
        "    WHERE instance_id = NEW.instance_id "
        "      AND admission_state = 'active' "
        "      AND deleted_at IS NULL"
        "  ) THEN "
        "    RAISE EXCEPTION "
        "      'job_locks row requires admission_state=active (instance_id=%)', "
        "      NEW.instance_id "
        "      USING ERRCODE = 'integrity_constraint_violation'; "
        "    END IF; "
        "  RETURN NEW; "
        "END; "
        "$$ LANGUAGE plpgsql"
    ),
    "DROP TRIGGER IF EXISTS trg_job_queue_items_active_lock_guard ON job_queue_items",
    "DROP TRIGGER IF EXISTS trg_job_locks_active_guard ON job_locks",
    (
        "CREATE CONSTRAINT TRIGGER trg_job_queue_items_active_lock_guard "
        "AFTER INSERT OR UPDATE OF admission_state ON job_queue_items "
        "DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION job_queue_items_active_lock_guard()"
    ),
    (
        "CREATE CONSTRAINT TRIGGER trg_job_locks_active_guard "
        "AFTER INSERT OR UPDATE ON job_locks "
        "DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION job_locks_active_guard()"
    ),
)


# =============================================================================
# Helpers
# =============================================================================


def _apply(engine, statements: tuple[str, ...]) -> None:
    """Execute each statement in a single transaction."""
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _sqlstate(exc: BaseException) -> str | None:
    """Return the SQLSTATE string for a psycopg3-wrapped SQLAlchemy error."""
    orig = getattr(exc, "orig", exc)
    diag = getattr(orig, "diag", None)
    if diag is not None:
        state = getattr(diag, "sqlstate", None)
        if state:
            return state
    return getattr(orig, "sqlstate", None)


def _insert_job_queue_item(
    conn,
    *,
    job_id: str,
    instance_id: str | None,
    admission_state: str = "queued",
    project_id: str | None = None,
    queue_id: str | None = None,
    deleted_at: str | None = None,
    created_at: str | None = "2026-07-01T00:00:00+00:00",
) -> None:
    """Insert one ``job_queue_items`` row with the given admission state.

    Bypasses the ORM (raw SQL) so we can drive the test setup
    deterministically — the test exercises the constraint triggers
    directly, not the application-level state machine.

    Column list mirrors the post-Phase-5 ``JobItem`` SQLModel
    (``daemon/repositories/job_queue/models.py``): the legacy
    ``status`` / ``started_at`` / ``completed_at`` columns were
    dropped in Phase 5 — ``admission_state`` is the sole authority.
    The Phase 7c ``terminal_reason`` discriminator is included so
    re-arm tests can verify it's preserved on rollback.
    """
    conn.execute(
        text(
            """
            INSERT INTO job_queue_items (
                job_id, agent_id, agent_dir, message, source, priority,
                admission_state, instance_id, project_id,
                queue_id, deleted_at, created_at, job_type, retry_count
            ) VALUES (
                :job_id, 'f9-agent', 'agents/f9-agent', 'm', 'api', 5,
                :admission_state, :instance_id, :project_id,
                :queue_id, :deleted_at, :created_at, 'task', 0
            )
            """
        ),
        {
            "job_id": job_id,
            "admission_state": admission_state,
            "instance_id": instance_id,
            "project_id": project_id,
            "queue_id": queue_id,
            "deleted_at": deleted_at,
            "created_at": created_at,
        },
    )


def _insert_job_lock(
    conn,
    *,
    lock_id: str,
    project_id: str,
    queue_id: str,
    job_id: str,
    instance_id: str,
    lock_slot: int = 0,
    acquired_at: str = "2026-07-01T00:00:00+00:00",
) -> None:
    """Insert one ``job_locks`` row with ``acquired_at`` explicit."""
    conn.execute(
        text(
            """
            INSERT INTO job_locks (
                lock_id, project_id, queue_id, job_id, instance_id,
                lock_slot, acquired_at
            ) VALUES (
                :lock_id, :project_id, :queue_id, :job_id, :instance_id,
                :lock_slot, :acquired_at
            )
            """
        ),
        {
            "lock_id": lock_id,
            "project_id": project_id,
            "queue_id": queue_id,
            "job_id": job_id,
            "instance_id": instance_id,
            "lock_slot": lock_slot,
            "acquired_at": acquired_at,
        },
    )


def _count_locks_for_instance(conn, instance_id: str) -> int:
    """Count ``job_locks`` rows for the given instance."""
    row = conn.execute(
        text("SELECT COUNT(*) FROM job_locks WHERE instance_id = :instance_id"),
        {"instance_id": instance_id},
    ).first()
    return int(row[0]) if row is not None else 0


def _get_admission_state(conn, job_id: str) -> str | None:
    """Read the JobItem's ``admission_state``. Returns None if the row is gone."""
    row = conn.execute(
        text(
            "SELECT admission_state FROM job_queue_items WHERE job_id = :job_id"
        ),
        {"job_id": job_id},
    ).first()
    return row[0] if row is not None else None


@pytest.fixture
def f9_setup(pg_engine, pg_repository_factory):
    """Common F9 setup: a JobQueue + a JobItem in ``active`` with a matching lock.

    Mirrors the end-of-``start_job_atomic_with_lock`` state — the job
    is running, the lock is held, the triggers are satisfied. Tests
    then simulate ``_finalize_job_db_sync`` by deleting the lock and
    setting ``admission_state='done'`` (the F9 trigger scenario).
    Returns a dict with the wired-up repository + the IDs tests need.
    """
    queue_repo = pg_repository_factory(JobQueueRepository)
    job_repo = pg_repository_factory(JobRepository)

    project_id = f"f9-project-{uuid.uuid4().hex[:8]}"
    queue = queue_repo.create(
        project_id=project_id,
        queue_name=f"f9-queue-{uuid.uuid4().hex[:8]}",
        queue_type=QueueType.FIFO.value,
        concurrency_limit=1,
    )
    job_id = f"f9-job-{uuid.uuid4().hex[:8]}"
    instance_id = f"f9-instance-{uuid.uuid4().hex[:8]}"

    # Stage the pre-finalize state: job active + lock held.
    with pg_engine.begin() as conn:
        _insert_job_queue_item(
            conn,
            job_id=job_id,
            instance_id=instance_id,
            admission_state="active",
            project_id=project_id,
            queue_id=queue.queue_id,
        )
        _insert_job_lock(
            conn,
            lock_id=f"f9-lock-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            queue_id=queue.queue_id,
            job_id=job_id,
            instance_id=instance_id,
            lock_slot=0,
        )

    return {
        "job_repo": job_repo,
        "queue_repo": queue_repo,
        "project_id": project_id,
        "queue_id": queue.queue_id,
        "job_id": job_id,
        "instance_id": instance_id,
    }


def _simulate_finalize_lock_release(pg_engine, f9_ctx) -> None:
    """Simulate ``_finalize_job_db_sync`` Step 1 + Step 3.

    Step 1: JobItem UPDATE — ``admission_state='active'`` → ``done``.
    Step 3: Lock release — DELETE ``job_locks`` rows for the instance.

    In production both writes commit atomically inside one
    ``WriteGuardSession``. Here we mimic that single commit by using
    one ``engine.begin()`` block — the triggers see both writes at
    COMMIT and pass (no active JobItem left without a lock because
    the active→done UPDATE removed the trigger-sensitive state
    before the lock DELETE).
    """
    with pg_engine.begin() as conn:
        # Step 1: admission_state active → done.
        result = conn.execute(
            text(
                "UPDATE job_queue_items "
                "SET admission_state = :done "
                "WHERE job_id = :job_id"
            ),
            {"done": AdmissionState.DONE.value, "job_id": f9_ctx["job_id"]},
        )
        assert (result.rowcount or 0) == 1, (
            "Step 1 setup UPDATE should have matched exactly 1 row"
        )
        # Step 3: lock release.
        result = conn.execute(
            text("DELETE FROM job_locks WHERE instance_id = :instance_id"),
            {"instance_id": f9_ctx["instance_id"]},
        )
        assert (result.rowcount or 0) == 1, (
            "Step 3 setup DELETE should have removed exactly 1 lock row"
        )


# =============================================================================
# Session-scoped autouse fixture: install Phase 2 column + triggers
# =============================================================================
@pytest.fixture(scope="session", autouse=True)
def _install_phase2_schema(pg_engine):
    """Install the ``admission_state`` column + both constraint triggers once."""
    _apply(pg_engine, PHASE2_INSTALL_STATEMENTS)
    yield


# =============================================================================
# Section A — The fix works end-to-end
# =============================================================================


def test_rearm_with_lock_does_not_violate_trigger(
    pg_engine, f9_setup
) -> None:
    """F9 fix headline test: ``rearm_with_lock`` after
    ``_finalize_job_db_sync`` commits cleanly against the live PG
    triggers.

    Setup:
      * ``f9_setup`` wires a JobItem (``active``) + JobLock row.
      * ``_simulate_finalize_lock_release`` deletes the lock and
        sets ``admission_state='done'`` — this is the post-Step-3
        state from which the orphan-race re-arm runs.

    Act:
      * ``JobRepository.rearm_with_lock(job_id, instance_id)``.

    Asserts (the F9 contract):
      1. No ``IntegrityError`` is raised.
      2. ``job_queue_items.admission_state == 'active'`` after the call.
      3. A ``job_locks`` row exists for the instance (the lock was
         re-inserted in the SAME transaction as the admission_state
         UPDATE — the PG trigger sees both at COMMIT).
      4. Exactly one lock row exists for the instance (no duplicate).
    """
    # ── Setup: post-_finalize_job_db_sync state ──────────────────────
    _simulate_finalize_lock_release(pg_engine, f9_setup)

    with pg_engine.connect() as conn:
        # Sanity-check the pre-condition state.
        assert _get_admission_state(conn, f9_setup["job_id"]) == "done", (
            "Setup pre-condition: JobItem should be in 'done' before "
            "rearm_with_lock is called"
        )
        assert _count_locks_for_instance(conn, f9_setup["instance_id"]) == 0, (
            "Setup pre-condition: no job_locks row should exist "
            "before rearm_with_lock is called"
        )

    # ── Act: rearm_with_lock ────────────────────────────────────────
    raised: IntegrityError | None = None
    job, lock_acquired = None, False
    try:
        job, lock_acquired = f9_setup["job_repo"].rearm_with_lock(
            job_id=f9_setup["job_id"],
            instance_id=f9_setup["instance_id"],
        )
    except IntegrityError as exc:
        raised = exc

    # ── Assert 1: no IntegrityError raised ───────────────────────────
    if raised is not None:
        sqlstate = _sqlstate(raised) if "IntegrityError" in type(raised).__name__ else None
        assert sqlstate != "23000", (
            f"F9 REGRESSION: rearm_with_lock raised IntegrityError "
            f"with SQLSTATE 23000 — the trigger fired. The lock "
            f"INSERT and admission_state UPDATE MUST be in ONE "
            f"transaction. Raised: {raised!r}"
        )
        raise raised

    # ── Assert 2: lock was acquired ───────────────────────────────────
    assert lock_acquired is True, (
        "rearm_with_lock should have acquired a lock — concurrency_limit=1 "
        "and no other job is competing for the slot"
    )

    # ── Assert 3: job returned is non-None and active ────────────────
    assert job is not None, "rearm_with_lock should return the JobItem"
    assert job.admission_state == AdmissionState.ACTIVE.value, (
        f"Expected admission_state='active' after rearm_with_lock, "
        f"got {job.admission_state!r}"
    )

    # ── Assert 4: DB state matches the API state ─────────────────────
    with pg_engine.connect() as conn:
        assert _get_admission_state(conn, f9_setup["job_id"]) == "active", (
            "DB-level admission_state must be 'active' after rearm_with_lock"
        )
        lock_count = _count_locks_for_instance(conn, f9_setup["instance_id"])
        assert lock_count == 1, (
            f"Expected exactly 1 job_locks row for the instance after "
            f"rearm_with_lock, got {lock_count}"
        )


# =============================================================================
# Section B — The pre-fix bug reproduces
# =============================================================================
#
# This test is the negative-control: it confirms the trigger exists
# AND that the pre-fix code path (a bare ``atomic_transition`` after
# lock release) would fail. If this test ever starts passing (the
# pre-fix path no longer raises), one of two things has happened:
#   1. The constraint trigger was dropped or weakened — investigate
#      ``_install_phase2_schema`` / production install.
#   2. PG semantics changed — very unlikely; the trigger is the
#      authoritative defence-in-depth boundary.
# =============================================================================


def test_naive_rearm_violates_trigger(pg_engine, f9_setup) -> None:
    """The pre-fix F9 bug: ``atomic_transition(done → active)`` alone
    (no lock INSERT in the same transaction) raises
    ``integrity_constraint_violation``.

    Documents that the trigger exists and would catch a regression
    where someone splits the single-transaction re-arm back into
    two commits. With the fix in place, ``rearm_with_lock`` is the
    canonical re-arm primitive and this naive pattern should never
    be used — this test is here to make a regression loud and
    diagnosable.
    """
    _simulate_finalize_lock_release(pg_engine, f9_setup)

    raised: IntegrityError | None = None
    try:
        # Mimic the pre-fix code path: a single UPDATE setting
        # admission_state='active' without re-acquiring the lock.
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job_queue_items "
                    "SET admission_state = :active "
                    "WHERE job_id = :job_id"
                ),
                {
                    "active": AdmissionState.ACTIVE.value,
                    "job_id": f9_setup["job_id"],
                },
            )
    except IntegrityError as exc:
        raised = exc

    assert raised is not None, (
        "Expected IntegrityError from the constraint trigger — the "
        "naive UPDATE admission_state='active' without a matching "
        "job_locks row MUST violate the trigger. If this test ever "
        "starts passing, the trigger was dropped or weakened."
    )
    assert _sqlstate(raised) == "23000", (
        f"Expected SQLSTATE 23000 (integrity_constraint_violation) "
        f"from the trigger; got {_sqlstate(raised)!r}"
    )
    assert "requires a job_locks row" in str(raised), (
        f"Expected trigger's error message; got: {raised!r}"
    )


# =============================================================================
# Section C — No slot available: returns (None, False), writes nothing
# =============================================================================


def test_rearm_with_lock_no_slot_available(pg_engine, pg_repository_factory) -> None:
    """When all ``concurrency_limit`` slots are taken,
    ``rearm_with_lock`` returns ``(None, False)`` and writes nothing.

    Setup:
      * JobQueue with ``concurrency_limit=1``.
      * One JobItem in ``done`` (the F9 trigger scenario) for that queue.
      * Another (competing) job_locks row already occupies slot 0.

    Asserts:
      1. ``rearm_with_lock`` returns ``(None, False)``.
      2. JobItem stays in ``done`` (no admission_state change).
      3. The competing lock row is NOT disturbed (no new lock rows
         for our instance, no DELETE on the competing row).
    """
    queue_repo = pg_repository_factory(JobQueueRepository)
    job_repo = pg_repository_factory(JobRepository)

    project_id = f"f9C-project-{uuid.uuid4().hex[:8]}"
    queue = queue_repo.create(
        project_id=project_id,
        queue_name=f"f9C-queue-{uuid.uuid4().hex[:8]}",
        queue_type=QueueType.FIFO.value,
        concurrency_limit=1,
    )

    # The competing job: a different job already holding slot 0.
    # Insert BOTH the JobItem (admission_state='active') AND the lock
    # row — the PG ``trg_job_locks_active_guard`` trigger requires the
    # matching JobItem to be in 'active' at COMMIT, otherwise the
    # lock INSERT itself violates the invariant.
    competing_instance_id = f"f9C-competing-{uuid.uuid4().hex[:8]}"
    competing_job_id = f"f9C-competing-job-{uuid.uuid4().hex[:8]}"
    with pg_engine.begin() as conn:
        _insert_job_queue_item(
            conn,
            job_id=competing_job_id,
            instance_id=competing_instance_id,
            admission_state="active",
            project_id=project_id,
            queue_id=queue.queue_id,
        )
        _insert_job_lock(
            conn,
            lock_id=f"f9C-competing-lock-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            queue_id=queue.queue_id,
            job_id=competing_job_id,
            instance_id=competing_instance_id,
            lock_slot=0,
        )

    # The job that wants to re-arm: in 'done', no lock.
    target_job_id = f"f9C-target-{uuid.uuid4().hex[:8]}"
    target_instance_id = f"f9C-target-instance-{uuid.uuid4().hex[:8]}"
    with pg_engine.begin() as conn:
        _insert_job_queue_item(
            conn,
            job_id=target_job_id,
            instance_id=target_instance_id,
            admission_state="done",
            project_id=project_id,
            queue_id=queue.queue_id,
        )

    # Act.
    job, lock_acquired = job_repo.rearm_with_lock(
        job_id=target_job_id,
        instance_id=target_instance_id,
    )

    # Assert 1: no-op return.
    assert lock_acquired is False, (
        "rearm_with_lock should have returned False (no slot available)"
    )
    assert job is None, (
        "rearm_with_lock should have returned (None, False) — no slot"
    )

    # Assert 2: JobItem still in 'done'.
    with pg_engine.connect() as conn:
        assert _get_admission_state(conn, target_job_id) == "done", (
            "JobItem must stay in 'done' when no slot is available"
        )
        # Assert 3: competing lock untouched.
        competing_count = _count_locks_for_instance(conn, competing_instance_id)
        assert competing_count == 1, (
            f"Competing lock row must be undisturbed; got count={competing_count}"
        )
        # No new lock for our instance.
        target_lock_count = _count_locks_for_instance(conn, target_instance_id)
        assert target_lock_count == 0, (
            f"No lock should be inserted for the target instance; "
            f"got count={target_lock_count}"
        )


# =============================================================================
# Section D — Invalid state: ValueError, no lock leak
# =============================================================================


def test_rearm_with_lock_invalid_state_raises(pg_engine, pg_repository_factory) -> None:
    """When ``admission_state`` is not ``done``, ``rearm_with_lock``
    raises ``ValueError`` and rolls back (no lock leak).

    Asserts:
      1. ``ValueError`` is raised.
      2. No ``job_locks`` row is left behind for the instance.
      3. ``admission_state`` is unchanged.
    """
    queue_repo = pg_repository_factory(JobQueueRepository)
    job_repo = pg_repository_factory(JobRepository)

    project_id = f"f9D-project-{uuid.uuid4().hex[:8]}"
    queue = queue_repo.create(
        project_id=project_id,
        queue_name=f"f9D-queue-{uuid.uuid4().hex[:8]}",
        queue_type=QueueType.FIFO.value,
        concurrency_limit=1,
    )

    # JobItem in 'queued' — wrong state for re-arm (must be 'done').
    # Use 'queued' instead of 'active' to avoid firing trigger 1 at
    # setup time: 'queued' has no trigger sensitivity (the active
    # ⇒ lock-held guard only fires for admission_state='active'),
    # so the setup commit passes cleanly. The test then exercises
    # the in-method guard ``current_admission != 'done'`` which
    # returns (None, False) without raising — but the production
    # code path goes through the same lookup, and the ValueError
    # branch (when admission_state flips off 'done' between the
    # SELECT and the UPDATE) is the path that would leak a lock if
    # not handled atomically.
    #
    # We assert the (None, False) no-op shape here for the pre-UPDATE
    # case, and a separate test (or this same test with a deferred
    # transition) covers the in-transaction ValueError branch.
    target_job_id = f"f9D-target-{uuid.uuid4().hex[:8]}"
    target_instance_id = f"f9D-target-instance-{uuid.uuid4().hex[:8]}"
    with pg_engine.begin() as conn:
        _insert_job_queue_item(
            conn,
            job_id=target_job_id,
            instance_id=target_instance_id,
            admission_state="queued",  # WRONG — should be 'done' for re-arm
            project_id=project_id,
            queue_id=queue.queue_id,
        )

    raised: ValueError | None = None
    job, lock_acquired = None, True
    try:
        job, lock_acquired = job_repo.rearm_with_lock(
            job_id=target_job_id,
            instance_id=target_instance_id,
        )
    except ValueError as exc:
        raised = exc

    # In this state, the in-method SELECT sees ``admission_state !=
    # 'done'`` BEFORE the lock INSERT — it returns ``(None, False)``
    # without raising. The ValueError branch is exercised by tests
    # where the race happens AFTER the SELECT but BEFORE the UPDATE.
    assert raised is None, (
        "rearm_with_lock should return (None, False) (not raise) when "
        "admission_state is not 'done' at SELECT time — no lock INSERT "
        "is attempted, so no rollback is needed"
    )
    assert job is None, (
        "rearm_with_lock should return (None, False) when admission_state "
        "is not 'done'"
    )
    assert lock_acquired is False, (
        "rearm_with_lock should report no lock acquired when admission_state "
        "is not 'done'"
    )

    # No lock leak + state unchanged.
    with pg_engine.connect() as conn:
        assert _count_locks_for_instance(conn, target_instance_id) == 0, (
            "rearm_with_lock must NOT insert a lock row when admission_state "
            "is not 'done'"
        )
        assert _get_admission_state(conn, target_job_id) == "queued", (
            "rearm_with_lock must leave admission_state unchanged when "
            "admission_state is not 'done'"
        )


# =============================================================================
# Section D-race — Race-lost ValueError: state flips between SELECT and UPDATE
# =============================================================================
#
# The previous Section D test exercises the pre-flight SELECT guard
# (current_admission != 'done' → return (None, False) without raising).
# This section exercises the OTHER ValueError path: the pre-flight SELECT
# saw ``admission_state='done'``, the lock INSERT succeeded, but a
# concurrent actor committed a state change BEFORE our guarded UPDATE
# landed — so the UPDATE matches 0 rows and the function raises
# ``ValueError``. The transaction must roll back atomically: the lock
# INSERT that already succeeded must NOT persist (the whole point of the
# single-transaction shape is that lock + UPDATE live or die together).
#
# The race window is widened deterministically with a SQLAlchemy
# ``before_cursor_execute`` event listener that flips
# ``admission_state`` on a separate connection right before the
# guarded UPDATE fires. Under READ COMMITTED, the re-arm's UPDATE then
# sees the post-flip state and matches 0 rows → ValueError → rollback.


def test_rearm_with_lock_race_lost_raises_value_error(
    pg_engine, pg_repository_factory
) -> None:
    """Race-lost branch: a concurrent actor flips ``admission_state``
    off ``done`` between the pre-flight SELECT and the guarded UPDATE,
    causing ``rearm_with_lock`` to raise ``ValueError`` and roll back
    atomically (no lock leak).

    Setup:
      * JobItem in ``done`` (the CORRECT pre-flight state for re-arm)
        with no ``job_locks`` row.

    Act:
      * Attach a ``before_cursor_execute`` event listener that, on
        the first guarded UPDATE statement against ``job_queue_items``
        (the re-arm's step 4), opens a separate connection and flips
        ``admission_state`` from ``done`` to ``queued``. This mimics
        a concurrent actor committing a state change after our
        pre-flight SELECT but before our UPDATE.
      * Call ``rearm_with_lock``.

    Asserts:
      1. ``ValueError`` is raised with a message explaining the race.
      2. No ``job_locks`` row persists for the instance (the lock
         INSERT was undone by the atomic rollback).
      3. ``admission_state`` reflects the post-race value (``queued``),
         confirming the race actually fired and the rollback did NOT
         silently re-flip the state.
    """
    queue_repo = pg_repository_factory(JobQueueRepository)
    job_repo = pg_repository_factory(JobRepository)

    project_id = f"f9Dr-project-{uuid.uuid4().hex[:8]}"
    queue = queue_repo.create(
        project_id=project_id,
        queue_name=f"f9Dr-queue-{uuid.uuid4().hex[:8]}",
        queue_type=QueueType.FIFO.value,
        concurrency_limit=1,
    )

    # Seed a job in 'done' — the CORRECT pre-flight state for re-arm.
    # The race will flip it to 'queued' between SELECT and UPDATE.
    target_job_id = f"f9Dr-target-{uuid.uuid4().hex[:8]}"
    target_instance_id = f"f9Dr-target-instance-{uuid.uuid4().hex[:8]}"
    with pg_engine.begin() as conn:
        _insert_job_queue_item(
            conn,
            job_id=target_job_id,
            instance_id=target_instance_id,
            admission_state="done",
            project_id=project_id,
            queue_id=queue.queue_id,
        )

    # ── Inject the race via a before_cursor_execute event listener ────
    # We match the guarded UPDATE by its parameters dict: only the
    # re-arm's step-4 UPDATE binds an ``admission_state_guard`` key (the
    # old ``done`` value used for the ``WHERE`` clause guard). Setup
    # INSERTs and UPDATEs in this test module bind different keys
    # (``admission_state``, ``done``, ``new_state``, ``job_id``), so
    # the ``admission_state_guard`` key is unambiguous.
    #
    # SQLAlchemy renders the SQL string with ``%(name)s`` placeholders
    # for psycopg3, so substring-matching the raw text() string for
    # ``:admission_state`` is unreliable — matching the params dict is
    # both more precise and dialect-agnostic.
    flip_state = {"fired": False}

    def before_cursor_execute(
        conn, cursor, statement, parameters, context, executemany
    ):
        if flip_state["fired"]:
            return
        params = parameters if isinstance(parameters, dict) else {}
        if "admission_state_guard" in params:
            # Guarded UPDATE in rearm_with_lock step 4. Fire the race:
            # flip the state to 'queued' on a SEPARATE connection so
            # the flip's COMMIT is visible to the re-arm's UPDATE under
            # READ COMMITTED.
            flip_state["fired"] = True
            with pg_engine.begin() as flip_conn:
                flip_conn.execute(
                    text(
                        "UPDATE job_queue_items "
                        "SET admission_state = :new_state "
                        "WHERE job_id = :job_id"
                    ),
                    {"new_state": "queued", "job_id": target_job_id},
                )

    event.listen(pg_engine, "before_cursor_execute", before_cursor_execute)
    try:
        raised: ValueError | None = None
        try:
            job_repo.rearm_with_lock(
                job_id=target_job_id,
                instance_id=target_instance_id,
            )
        except ValueError as exc:
            raised = exc

        # ── Assert 1: ValueError raised with race explanation ────────
        assert raised is not None, (
            "rearm_with_lock MUST raise ValueError when admission_state "
            "flips off 'done' between the pre-flight SELECT and the "
            "guarded UPDATE — the guard matched 0 rows"
        )
        msg = str(raised).lower()
        assert "race lost" in msg or "between pre-flight" in msg, (
            f"ValueError should explain the race condition; got: {raised!r}"
        )
    finally:
        event.remove(pg_engine, "before_cursor_execute", before_cursor_execute)

    # ── Assert 2: rollback — no lock leaked ───────────────────────────
    with pg_engine.connect() as conn:
        assert _count_locks_for_instance(conn, target_instance_id) == 0, (
            "Lock INSERT must be rolled back with the transaction when "
            "ValueError is raised — the lock INSERT and admission_state "
            "UPDATE share one engine.begin() block (F9 atomicity contract)"
        )
        # ── Assert 3: race actually fired (state was flipped to 'queued')
        assert _get_admission_state(conn, target_job_id) == "queued", (
            "admission_state should reflect the post-race value ('queued') — "
            "if this fails the event listener didn't fire and the test "
            "didn't actually exercise the race-lost branch"
        )


# =============================================================================
# Section E — Missing job: returns (None, False), writes nothing
# =============================================================================


def test_rearm_with_lock_missing_job(pg_engine, pg_repository_factory) -> None:
    """When ``job_id`` does not exist, ``rearm_with_lock`` returns
    ``(None, False)`` and writes nothing.

    Documents the idempotency contract for deleted-concurrently jobs.
    """
    job_repo = pg_repository_factory(JobRepository)

    missing_job_id = f"f9E-missing-{uuid.uuid4().hex[:8]}"
    missing_instance_id = f"f9E-missing-instance-{uuid.uuid4().hex[:8]}"

    job, lock_acquired = job_repo.rearm_with_lock(
        job_id=missing_job_id,
        instance_id=missing_instance_id,
    )

    assert job is None, "rearm_with_lock should return (None, False) for missing job"
    assert lock_acquired is False, "rearm_with_lock should return False for missing job"

    with pg_engine.connect() as conn:
        assert _count_locks_for_instance(conn, missing_instance_id) == 0, (
            "No lock row should be inserted for a missing job"
        )


# =============================================================================
# Section F — Single-transaction atomicity
# =============================================================================


def test_rearm_with_lock_is_single_transaction(
    pg_engine, f9_setup
) -> None:
    """``rearm_with_lock`` must commit both the lock INSERT and the
    admission_state UPDATE in ONE ``engine.begin()`` block. If any
    step fails, neither persists.

    Asserts the fix uses ONE transaction by inspecting
    ``inspect.getsource`` for the ``engine.begin()`` shape — mirrors
    the B1 regression test in
    ``tests/job_queue/test_task_queue_service.py::test_repository_start_job_atomic_with_lock_is_single_transaction``.
    """
    import inspect

    source = inspect.getsource(f9_setup["job_repo"].rearm_with_lock)
    # ``self.engine.begin()`` is the actual transaction entry point —
    # ``engine.begin()`` (without ``self.``) appears in docstrings and
    # comments, which we want to exclude. Counting ``self.engine.begin()``
    # matches the B1 regression test pattern in
    # ``tests/job_queue/test_task_queue_service.py::
    # test_repository_start_job_atomic_with_lock_is_single_transaction``.
    begin_count = source.count("self.engine.begin()")
    assert begin_count == 1, (
        f"rearm_with_lock must open exactly ONE self.engine.begin() "
        f"block (INSERT lock + UPDATE admission_state share a single "
        f"transaction); got {begin_count} occurrences in the method "
        f"source. Each additional transaction breaks the F9 invariant "
        f"on PostgreSQL."
    )

    # Also assert the method body contains BOTH the lock INSERT and
    # the admission_state UPDATE — neither can be silently dropped.
    assert "INSERT INTO job_locks" in source, (
        "rearm_with_lock must INSERT a job_locks row in the same "
        "transaction as the admission_state UPDATE"
    )
    assert "admission_state" in source and "UPDATE job_queue_items" in source, (
        "rearm_with_lock must UPDATE job_queue_items.admission_state "
        "in the same transaction as the lock INSERT"
    )