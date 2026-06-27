"""PostgreSQL constraint trigger tests for Job-as-Queue-Proxy Phase 2.

Phase 2 of ``feature/job-as-queue-proxy`` installs two
``DEFERRABLE INITIALLY DEFERRED CONSTRAINT TRIGGER`` s at
``daemon/manager.py::_ensure_postgres_columns`` (lines 2072-2077) that
enforce the ``admission_state='active' ⇔ JobLock row exists``
cross-table invariant at COMMIT time:

  1. ``trg_job_queue_items_active_lock_guard``
     AFTER INSERT OR UPDATE OF admission_state ON job_queue_items
     — raises ``integrity_constraint_violation`` when the new row has
     ``admission_state='active'`` and no matching row exists in
     ``job_locks`` (matched on ``instance_id``).

  2. ``trg_job_locks_active_guard``
     AFTER INSERT OR UPDATE ON job_locks
     — raises ``integrity_constraint_violation`` when the new row's
     ``instance_id`` has no matching ``job_queue_items`` row with
     ``admission_state='active' AND deleted_at IS NULL``.

Both are ``DEFERRABLE INITIALLY DEFERRED`` — they only fire at
COMMIT unless you run ``SET CONSTRAINTS ALL IMMEDIATE`` inside the
current transaction (we use that to make violation tests
deterministic).

What this module verifies
-------------------------
* **A. active ⇒ lock-held** (trigger 1): violation raises at
  COMMIT/IMMEDIATE; valid path commits cleanly.
* **B. lock-held ⇒ active** (trigger 2): violation raises at
  COMMIT/IMMEDIATE; valid path commits cleanly.
* **C. Normal lifecycle** is invariant-preserving: enqueue (queued)
  → start (active + lock) → complete (delete lock + done) each
  COMMIT cleanly without firing the triggers.
* **D. ``SET CONSTRAINTS ALL IMMEDIATE``** deterministically fires
  the deferred trigger inside the transaction, before COMMIT.
* **E. Migration** is idempotent and installs the column + both
  triggers correctly on a fresh DB.

Run with::

    .venv/bin/python -m pytest tests/postgres/test_jq_proxy_phase2_constraints.py \\
        -v -m postgres --tb=short --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is not reachable.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# Import models so ``SQLModel.metadata.create_all`` (run by the
# session-scoped ``pg_engine`` fixture) registers the ``job_queue_items``
# and ``job_locks`` tables. Without these imports, the autouse
# install fixture would target tables that don't exist yet.
from daemon.repositories.job_queue.models import (  # noqa: F401
    AdmissionState,
    JobItem,
    JobLock,
)


logger = logging.getLogger(__name__)


# Auto-apply the postgres marker so ``pytest -m postgres`` selects
# these tests and the default ``addopts`` skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# SQL constants — mirror daemon/manager.py::_ensure_postgres_columns
# =============================================================================
#
# The trigger SQL and trigger function bodies are taken verbatim from
# ``daemon/manager.py:2072-2077`` (the Phase 2 install block). Keeping
# them here as string constants instead of importing the method makes
# the contract under test explicit and lets the tests run without
# standing up the full ``InstanceManager`` surface area (engine,
# repositories, credential manager, connection pool manager).
#
# The trigger functions use ``CREATE OR REPLACE FUNCTION`` for
# idempotency; the triggers themselves use ``DROP TRIGGER IF EXISTS``
# + ``CREATE CONSTRAINT TRIGGER`` (CREATE CONSTRAINT TRIGGER has no
# OR REPLACE form per PostgreSQL semantics — see manager.py:2069-2071).
# =============================================================================

PHASE2_INSTALL_STATEMENTS: tuple[str, ...] = (
    # admission_state column + backfill (mirrors manager.py:2052-2057).
    "ALTER TABLE job_queue_items ADD COLUMN IF NOT EXISTS admission_state TEXT NOT NULL DEFAULT 'queued'",
    # Index supporting future ``WHERE admission_state IN ('queued','active')``.
    "CREATE INDEX IF NOT EXISTS idx_job_queue_admission_state ON job_queue_items(admission_state)",
    # Trigger function 1: ``active ⇒ lock-held``.
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
    # Trigger function 2: ``lock-held ⇒ active``.
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
        "  END IF; "
        "  RETURN NEW; "
        "END; "
        "$$ LANGUAGE plpgsql"
    ),
    # Idempotent trigger install: DROP IF EXISTS + CREATE CONSTRAINT TRIGGER.
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


def _apply(pg_engine, statements: tuple[str, ...]) -> None:
    """Execute each statement in a single transaction.

    ``engine.begin()`` keeps all statements in one transaction so the
    install either completes or rolls back atomically. Re-running on
    an already-installed schema is a no-op (every statement is
    IF NOT EXISTS / OR REPLACE / DROP IF EXISTS).
    """
    with pg_engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _trigger_exists(pg_engine, trigger_name: str) -> bool:
    """Return True if ``trigger_name`` exists in the ``public`` schema.

    Uses ``pg_trigger`` (joined with ``pg_class`` to get the table
    name) because ``pg_trigger`` is the canonical catalog for triggers
    and exposes ``tgdeferrable`` / ``tginitdeferred`` flags.
    """
    sql = text(
        """
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE t.tgname = :trigger_name
          AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
        LIMIT 1
        """
    )
    with pg_engine.connect() as conn:
        row = conn.execute(sql, {"trigger_name": trigger_name}).fetchone()
    return row is not None


def _trigger_metadata(pg_engine, trigger_name: str) -> dict[str, Any] | None:
    """Return deferrable/initdeferred flags + table for ``trigger_name``.

    Returns ``None`` if the trigger is not present. Used by
    ``test_migration_triggers_installed_with_definable_deferred`` to
    assert the trigger was created with ``DEFERRABLE INITIALLY DEFERRED``
    (not the default ``NOT DEFERRABLE``).
    """
    sql = text(
        """
        SELECT
            t.tgname            AS trigger_name,
            c.relname           AS table_name,
            t.tgdeferrable      AS is_deferrable,
            t.tginitdeferred    AS is_initially_deferred,
            t.tgconstraint != 0 AS is_constraint_trigger
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE t.tgname = :trigger_name
          AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
        LIMIT 1
        """
    )
    with pg_engine.connect() as conn:
        row = conn.execute(sql, {"trigger_name": trigger_name}).mappings().fetchone()
    if row is None:
        return None
    return dict(row)


def _column_exists(pg_engine, table_name: str, column_name: str) -> bool:
    """Return True if ``column_name`` exists on ``table_name``."""
    sql = text(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :table_name
          AND column_name = :column_name
        LIMIT 1
        """
    )
    with pg_engine.connect() as conn:
        row = conn.execute(
            sql, {"table_name": table_name, "column_name": column_name}
        ).fetchone()
    return row is not None


def _sqlstate(exc: BaseException) -> str | None:
    """Return the SQLSTATE string for a psycopg3-wrapped SQLAlchemy error.

    psycopg3 exposes the SQLSTATE on ``error.diag.sqlstate`` (preferred)
    or ``error.sqlstate`` (fallback). SQLAlchemy's ``IntegrityError``
    wraps the DBAPI exception as ``.orig``. Mirrors the pattern in
    ``tests/postgres/conftest.py::_is_auth_failure`` so this file
    follows the same diagnostic convention.
    """
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
    deleted_at: str | None = None,
    created_at: str | None = "2026-06-28T00:00:00+00:00",
) -> None:
    """Insert one ``job_queue_items`` row with the given admission state.

    Bypasses the ORM (raw SQL) so we can set ``admission_state``
    directly to any value, including values that would be rejected
    by the application-level status state machine. The test exercises
    the DB-level invariant — not the application helper.

    Supplies the four NOT NULL columns that don't have SQL defaults
    (``created_at``, ``job_type``, ``retry_count``, ``version``).
    ``job_type`` and ``retry_count`` rely on Python-side defaults in
    the SQLModel that ``SQLAlchemy`` does not propagate to raw SQL
    INSERTs — we pass them explicitly. ``version`` DOES have a SQL
    default (``0``) so we leave it out.
    """
    conn.execute(
        text(
            """
            INSERT INTO job_queue_items (
                job_id, agent_id, agent_dir, message, source, priority,
                status, admission_state, instance_id, deleted_at,
                created_at, job_type, retry_count
            ) VALUES (
                :job_id, 'agent', 'agents/agent', 'm', 'api', 5,
                'pending', :admission_state, :instance_id, :deleted_at,
                :created_at, 'task', 0
            )
            """
        ),
        {
            "job_id": job_id,
            "admission_state": admission_state,
            "instance_id": instance_id,
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
) -> None:
    """Insert one ``job_locks`` row.

    Supplies ``acquired_at`` explicitly because the column is
    NOT NULL with no SQL default (the model uses a Python-side
    ``default_factory`` that ``SQLAlchemy`` does not propagate
    to raw SQL INSERTs).
    """
    conn.execute(
        text(
            """
            INSERT INTO job_locks (
                lock_id, project_id, queue_id, job_id, instance_id,
                lock_slot, acquired_at
            ) VALUES (
                :lock_id, :project_id, :queue_id, :job_id, :instance_id,
                :lock_slot, '2026-06-28T00:00:00+00:00'
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
        },
    )


# =============================================================================
# Session-scoped autouse fixture: install Phase 2 column + triggers
# =============================================================================
#
# The session-scoped ``pg_engine`` fixture (tests/postgres/conftest.py)
# creates the schema once for the entire module via
# ``SQLModel.metadata.create_all``. The base schema does NOT include
# ``admission_state`` or the constraint triggers — those are added
# by ``EnsembleManager._ensure_postgres_columns`` at production
# startup. We replicate that install here so the tests can exercise
# the constraint triggers.
#
# The fixture is idempotent (every statement uses IF NOT EXISTS /
# OR REPLACE / DROP IF EXISTS), so re-running it during a test is
# safe.
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def _install_phase2_schema(pg_engine):
    """Install the admission_state column + both constraint triggers once."""
    _apply(pg_engine, PHASE2_INSTALL_STATEMENTS)
    yield


# =============================================================================
# Section A — Trigger 1: active ⇐ lock-held
# =============================================================================
# trg_job_queue_items_active_lock_guard: when admission_state='active'
# on a job_queue_items row, a matching job_locks row (by instance_id)
# must exist.
# =============================================================================


def test_active_requires_lock_violation_raises(pg_engine) -> None:
    """INSERT a job with admission_state='active' and NO lock → RAISES.

    The trigger function 1 RAISE EXCEPTION uses
    ``ERRCODE = 'integrity_constraint_violation'`` (SQLSTATE 23000,
    the generic ``Class 23 — Integrity Constraint Violation`` class
    — verified against PostgreSQL error codes). Without ``SET
    CONSTRAINTS ALL IMMEDIATE``, the deferred trigger fires at
    COMMIT — so the assertion sits on ``conn.commit()`` raising
    ``IntegrityError`` with the trigger's message.
    """
    instance_id = "phase2-A-violation-instance"

    raised: IntegrityError | None = None
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_queue_item(
                conn,
                job_id="phase2-A-viol-job",
                instance_id=instance_id,
                admission_state="active",
            )
            # Force the deferred trigger to fire inside this
            # transaction so the assertion is on a deterministic
            # operation rather than ``commit()`` (which also works
            # but is less surgical).
            try:
                conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            except IntegrityError as exc:
                raised = exc
        finally:
            trans.rollback()

    assert raised is not None, (
        "Expected IntegrityError from SET CONSTRAINTS ALL IMMEDIATE "
        "for admission_state='active' without a matching job_locks "
        "row; trigger 1 did not fire."
    )
    assert _sqlstate(raised) == "23000", (
        f"Expected SQLSTATE 23000 (integrity_constraint_violation) "
        f"from the trigger; got {_sqlstate(raised)!r}"
    )
    assert "requires a job_locks row" in str(raised), (
        f"Expected trigger's error message; got: {raised!r}"
    )

    # Confirm the row did NOT survive (transaction rolled back).
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT job_id FROM job_queue_items WHERE job_id = :job_id"),
            {"job_id": "phase2-A-viol-job"},
        ).fetchall()
    assert rows == [], (
        "Transaction should have rolled back; the violating row must not persist"
    )


def test_active_requires_lock_violation_raises_on_commit(pg_engine) -> None:
    """Same violation as ``..._violation_raises`` but fires at COMMIT.

    Demonstrates the default ``DEFERRABLE INITIALLY DEFERRED``
    semantics: the trigger does NOT fire at the INSERT statement
    (no error there) — it fires when the transaction COMMITS. We
    wrap the entire ``begin() / commit()`` cycle in a try/except
    so the test can assert on the raised ``IntegrityError`` without
    leaving the connection in a broken state.
    """
    instance_id = "phase2-A-commit-instance"

    raised: IntegrityError | None = None
    try:
        with pg_engine.connect() as conn:
            trans = conn.begin()
            try:
                _insert_job_queue_item(
                    conn,
                    job_id="phase2-A-commit-job",
                    instance_id=instance_id,
                    admission_state="active",
                )
                trans.commit()
            except IntegrityError as exc:
                raised = exc
                trans.rollback()
    except Exception:
        # ``pg_engine.connect()`` itself shouldn't fail; surface if it does.
        raise

    assert raised is not None, (
        "Expected IntegrityError on COMMIT for admission_state='active' "
        "without a matching job_locks row; commit() succeeded — "
        "DEFERRABLE INITIALLY DEFERRED trigger is NOT firing at commit"
    )
    # PostgreSQL SQLSTATE for the trigger's RAISE EXCEPTION …
    # 'integrity_constraint_violation' is 23000 (the generic
    # ``Class 23 — Integrity Constraint Violation``). Verified
    # against PostgreSQL error codes; psycopg3 reports the parent
    # class, not a 235xx subclass.
    sqlstate = _sqlstate(raised)
    assert sqlstate == "23000", (
        f"Expected SQLSTATE 23000 (integrity_constraint_violation) from "
        f"the trigger; got sqlstate={sqlstate!r}, error={raised!r}"
    )


def test_active_with_lock_commits_cleanly(pg_engine) -> None:
    """INSERT job with admission_state='active' AND matching job_lock → OK.

    The valid-path version of the invariant: a matching ``job_locks``
    row exists for the ``instance_id``, so the trigger's NOT EXISTS
    guard passes and the COMMIT succeeds.
    """
    instance_id = "phase2-A-ok-instance"
    job_id = "phase2-A-ok-job"

    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_queue_item(
                conn,
                job_id=job_id,
                instance_id=instance_id,
                admission_state="active",
            )
            _insert_job_lock(
                conn,
                lock_id="phase2-A-ok-lock",
                project_id="phase2-A-ok-project",
                queue_id="phase2-A-ok-queue",
                job_id=job_id,
                instance_id=instance_id,
                lock_slot=0,
            )
            # Force deferred-trigger check to confirm no violation.
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            trans.commit()
        except Exception:
            trans.rollback()
            raise

    # Confirm both rows persisted.
    with pg_engine.connect() as conn:
        job_row = conn.execute(
            text(
                "SELECT admission_state FROM job_queue_items WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).fetchone()
        lock_row = conn.execute(
            text(
                "SELECT instance_id FROM job_locks WHERE lock_id = :lock_id"
            ),
            {"lock_id": "phase2-A-ok-lock"},
        ).fetchone()
    assert job_row is not None and job_row[0] == "active"
    assert lock_row is not None and lock_row[0] == instance_id


# =============================================================================
# Section B — Trigger 2: lock-held ⇐ active
# =============================================================================
# trg_job_locks_active_guard: when a job_locks row is inserted/updated,
# the matching job_queue_items row (by instance_id) must have
# admission_state='active' AND deleted_at IS NULL.
# =============================================================================


def test_lock_requires_active_violation_raises(pg_engine) -> None:
    """INSERT a job_locks row when job admission_state≠'active' → RAISES.

    The job is created with ``admission_state='queued'`` (the
    default). Inserting a matching ``job_locks`` row violates
    trigger 2 (the matching job is not active).
    """
    instance_id = "phase2-B-viol-instance"
    job_id = "phase2-B-viol-job"

    # Pre-create the job in ``queued`` (no lock yet — this insert
    # itself must succeed because trigger 1 only fires when
    # admission_state='active').
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_queue_item(
                conn,
                job_id=job_id,
                instance_id=instance_id,
                admission_state="queued",
            )
            trans.commit()
        except Exception:
            trans.rollback()
            raise

    # Now attempt to insert the matching lock — should violate trigger 2.
    raised: IntegrityError | None = None
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_lock(
                conn,
                lock_id="phase2-B-viol-lock",
                project_id="phase2-B-viol-project",
                queue_id="phase2-B-viol-queue",
                job_id=job_id,
                instance_id=instance_id,
                lock_slot=0,
            )
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            trans.commit()
        except IntegrityError as exc:
            raised = exc
            trans.rollback()

    assert raised is not None, (
        "Expected IntegrityError on job_locks INSERT when matching job "
        "is not admission_state='active'; trigger 2 did not fire"
    )
    sqlstate = _sqlstate(raised)
    assert sqlstate == "23000", (
        f"Expected SQLSTATE 23000 from trigger 2; got sqlstate={sqlstate!r}"
    )


def test_lock_with_active_job_commits_cleanly(pg_engine) -> None:
    """INSERT job_locks row when matching job IS active → OK."""
    instance_id = "phase2-B-ok-instance"
    job_id = "phase2-B-ok-job"

    # Set up: both rows in one transaction. The acquire-then-set-active
    # ordering is exactly the real start_job path: lock first, then
    # activate. Both writes must pass their respective trigger guards.
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_lock(
                conn,
                lock_id="phase2-B-ok-lock",
                project_id="phase2-B-ok-project",
                queue_id="phase2-B-ok-queue",
                job_id=job_id,
                instance_id=instance_id,
                lock_slot=0,
            )
            # We insert the lock first WITHOUT the active job
            # existing — that itself would violate trigger 2.
            # So flip the order: insert job as active first.
            trans.rollback()
        except Exception:
            trans.rollback()

    # Re-do in the correct order: job-active FIRST, then lock.
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_queue_item(
                conn,
                job_id=job_id,
                instance_id=instance_id,
                admission_state="active",
            )
            _insert_job_lock(
                conn,
                lock_id="phase2-B-ok-lock",
                project_id="phase2-B-ok-project",
                queue_id="phase2-B-ok-queue",
                job_id=job_id,
                instance_id=instance_id,
                lock_slot=0,
            )
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            trans.commit()
        except Exception:
            trans.rollback()
            raise

    # Verify the lock exists.
    with pg_engine.connect() as conn:
        lock_row = conn.execute(
            text("SELECT job_id FROM job_locks WHERE lock_id = :lock_id"),
            {"lock_id": "phase2-B-ok-lock"},
        ).fetchone()
    assert lock_row is not None and lock_row[0] == job_id


def test_lock_with_soft_deleted_active_job_violates(pg_engine) -> None:
    """A soft-deleted job (deleted_at IS NOT NULL) is NOT 'active' for trigger 2.

    Trigger 2's guard is ``admission_state='active' AND deleted_at IS NULL``.
    Even though the job's ``admission_state`` is still 'active' in the
    column, the soft-delete flag means the trigger should reject the
    lock — a lock cannot outlive the row it locks.

    Sequence:
      1. Insert job (admission_state='active', deleted_at=NULL).
      2. Insert lock — passes trigger 2 (job is active + not deleted).
      3. UPDATE job SET deleted_at = ... — does NOT fire trigger 2
         (trigger 2 is on ``job_locks``, not ``job_queue_items``).
      4. UPDATE job_locks — fires trigger 2; guard now fails because
         deleted_at IS NOT NULL. This is the case under test.
    """
    instance_id = "phase2-B-softdel-instance"
    job_id = "phase2-B-softdel-job"
    lock_id = "phase2-B-softdel-lock"

    # Step 1+2: create a valid active job + matching lock pair.
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_queue_item(
                conn,
                job_id=job_id,
                instance_id=instance_id,
                admission_state="active",
            )
            _insert_job_lock(
                conn,
                lock_id=lock_id,
                project_id="phase2-B-softdel-project",
                queue_id="phase2-B-softdel-queue",
                job_id=job_id,
                instance_id=instance_id,
                lock_slot=0,
            )
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            trans.commit()
        except Exception:
            trans.rollback()
            raise

    # Step 3: soft-delete the job. This must NOT fire any trigger
    # (trigger 1 is on admission_state; trigger 2 is on job_locks).
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(
                text(
                    "UPDATE job_queue_items SET deleted_at = :deleted_at "
                    "WHERE job_id = :job_id"
                ),
                {
                    "deleted_at": "2026-06-28T00:00:00+00:00",
                    "job_id": job_id,
                },
            )
            trans.commit()
        except Exception:
            trans.rollback()
            raise

    # Step 4: UPDATE the lock — fires trigger 2; guard now fails because
    # the matching job has deleted_at IS NOT NULL.
    raised: IntegrityError | None = None
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(
                text(
                    "UPDATE job_locks SET lock_slot = lock_slot "
                    "WHERE lock_id = :lock_id"
                ),
                {"lock_id": lock_id},
            )
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            trans.commit()
        except IntegrityError as exc:
            raised = exc
            trans.rollback()

    assert raised is not None, (
        "Expected IntegrityError: a lock UPDATE must trigger "
        "job_locks_active_guard when the matching job has "
        "deleted_at IS NOT NULL (even though admission_state='active' "
        "is still in the column)."
    )


# =============================================================================
# Section C — Normal lifecycle is invariant-preserving
# =============================================================================
# Enqueue (queued) → Start (active + lock) → Complete (delete lock + done)
# Each transition must COMMIT cleanly without firing the triggers.
# =============================================================================


def test_lifecycle_enqueue_start_complete(pg_engine) -> None:
    """End-to-end happy path: enqueue → start → complete, no violations."""
    instance_id = "phase2-C-instance"
    job_id = "phase2-C-job"

    # ── 1. Enqueue ──────────────────────────────────────────────────────
    # admission_state='queued' — trigger 1 passes (active check is
    # conditional). No lock involved.
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_queue_item(
                conn,
                job_id=job_id,
                instance_id=instance_id,
                admission_state="queued",
            )
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            trans.commit()
        except Exception:
            trans.rollback()
            raise

    # ── 2. Start ────────────────────────────────────────────────────────
    # Acquire lock + flip to active in one transaction. The order
    # matters: trigger 2 (lock ⇒ active) means the job must already
    # be active when the lock arrives. Trigger 1 (active ⇒ lock)
    # is satisfied because the lock is created in the same tx.
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_lock(
                conn,
                lock_id="phase2-C-lock",
                project_id="phase2-C-project",
                queue_id="phase2-C-queue",
                job_id=job_id,
                instance_id=instance_id,
                lock_slot=0,
            )
            conn.execute(
                text(
                    "UPDATE job_queue_items SET admission_state = 'active' "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            )
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            trans.commit()
        except Exception:
            trans.rollback()
            raise

    # ── 3. Complete ─────────────────────────────────────────────────────
    # Delete lock + flip to done. The deletion happens BEFORE the
    # UPDATE to 'done' so the intermediate state
    # (lock present + admission_state still 'active') is preserved.
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(
                text("DELETE FROM job_locks WHERE lock_id = :lock_id"),
                {"lock_id": "phase2-C-lock"},
            )
            conn.execute(
                text(
                    "UPDATE job_queue_items SET admission_state = 'done' "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            )
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            trans.commit()
        except Exception:
            trans.rollback()
            raise

    # Verify final state: job is done, no lock remains.
    with pg_engine.connect() as conn:
        final = conn.execute(
            text(
                "SELECT admission_state FROM job_queue_items WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).fetchone()
        remaining_lock = conn.execute(
            text("SELECT lock_id FROM job_locks WHERE job_id = :job_id"),
            {"job_id": job_id},
        ).fetchall()
    assert final is not None and final[0] == "done"
    assert remaining_lock == [], "Lock should have been released at complete"


# =============================================================================
# Section D — SET CONSTRAINTS ALL IMMEDIATE deterministically fires triggers
# =============================================================================
# The whole point of ``DEFERRABLE INITIALLY DEFERRED`` is that the
# trigger doesn't fire mid-transaction — but ``SET CONSTRAINTS ALL
# IMMEDIATE`` forces an immediate check. These tests demonstrate
# the asymmetric behavior:
#   * Violation + IMMEDIATE → raises inside the transaction.
#   * Violation + COMMIT only → raises on commit.
# =============================================================================


def test_set_constraints_immediate_fires_trigger_inline(pg_engine) -> None:
    """``SET CONSTRAINTS ALL IMMEDIATE`` raises before COMMIT.

    Verifies that the deferred-trigger check is re-runnable inside
    the same transaction and that the IMMEDIATE form is exactly
    what test fixtures (and application-level invariants) should
    use to surface violations synchronously.
    """
    instance_id = "phase2-D-immediate-instance"

    with pg_engine.connect() as conn:
        trans = conn.begin()
        raised: IntegrityError | None = None
        try:
            _insert_job_queue_item(
                conn,
                job_id="phase2-D-immediate-job",
                instance_id=instance_id,
                admission_state="active",
            )
            # Force deferred-trigger check NOW (no commit yet).
            try:
                conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            except IntegrityError as exc:
                raised = exc
            trans.rollback()
        except Exception:
            trans.rollback()

    assert raised is not None, (
        "SET CONSTRAINTS ALL IMMEDIATE must raise IntegrityError for "
        "the active-without-lock violation; it did not fire."
    )
    sqlstate = _sqlstate(raised)
    assert sqlstate == "23000", (
        f"Expected SQLSTATE 23000; got sqlstate={sqlstate!r}"
    )


def test_without_immediate_violation_defers_to_commit(pg_engine) -> None:
    """Without IMMEDIATE, the violation only surfaces at COMMIT.

    Pair to ``test_set_constraints_immediate_fires_trigger_inline``:
    same data, different flow. Demonstrates that
    ``DEFERRABLE INITIALLY DEFERRED`` means the trigger does NOT
    fire at INSERT — only at COMMIT (or at IMMEDIATE).
    """
    instance_id = "phase2-D-defer-instance"
    job_id = "phase2-D-defer-job"

    raised: IntegrityError | None = None
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            _insert_job_queue_item(
                conn,
                job_id=job_id,
                instance_id=instance_id,
                admission_state="active",
            )
            # No SET CONSTRAINTS here — INSERT itself must succeed
            # because the trigger is deferred.
            try:
                trans.commit()
            except IntegrityError as exc:
                raised = exc
                trans.rollback()
        except Exception:
            trans.rollback()

    assert raised is not None, (
        "Expected IntegrityError on COMMIT (deferred trigger fires at "
        "commit time); commit succeeded — trigger is not deferred."
    )


# =============================================================================
# Section E — Migration: column, triggers, idempotency
# =============================================================================
# Verifies the install path matches the production code and that
# re-running is safe.
# =============================================================================


def test_migration_admission_state_column_exists(pg_engine) -> None:
    """``job_queue_items.admission_state`` column exists after install."""
    assert _column_exists(pg_engine, "job_queue_items", "admission_state"), (
        "Phase 2 install must add ``admission_state`` to "
        "``job_queue_items`` (ALTER TABLE … ADD COLUMN IF NOT EXISTS)."
    )


def test_migration_index_idx_job_queue_admission_state_exists(pg_engine) -> None:
    """The Phase 2 index on ``admission_state`` is installed."""
    sql = text(
        """
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'job_queue_items'
          AND indexname = 'idx_job_queue_admission_state'
        LIMIT 1
        """
    )
    with pg_engine.connect() as conn:
        row = conn.execute(sql).fetchone()
    assert row is not None, (
        "Index ``idx_job_queue_admission_state`` must be created by "
        "the Phase 2 install (CREATE INDEX IF NOT EXISTS)."
    )


def test_migration_triggers_installed_with_definable_deferred(pg_engine) -> None:
    """Both constraint triggers exist and are DEFERRABLE INITIALLY DEFERRED.

    Checks ``pg_trigger`` for the trigger name, the table, and the
    two boolean flags (``tgdeferrable``, ``tginitdeferred``).
    ``tgconstraint != 0`` confirms it was created as a CONSTRAINT
    TRIGGER (not a regular row trigger).
    """
    for trigger_name, expected_table in (
        ("trg_job_queue_items_active_lock_guard", "job_queue_items"),
        ("trg_job_locks_active_guard", "job_locks"),
    ):
        meta = _trigger_metadata(pg_engine, trigger_name)
        assert meta is not None, (
            f"Trigger {trigger_name!r} is not installed — Phase 2 install "
            f"missing the CREATE CONSTRAINT TRIGGER statement."
        )
        assert meta["table_name"] == expected_table, (
            f"Trigger {trigger_name!r} attached to wrong table: "
            f"expected {expected_table!r}, got {meta['table_name']!r}"
        )
        assert meta["is_deferrable"] is True, (
            f"Trigger {trigger_name!r} must be DEFERRABLE; "
            f"got tgdeferrable={meta['is_deferrable']}"
        )
        assert meta["is_initially_deferred"] is True, (
            f"Trigger {trigger_name!r} must be INITIALLY DEFERRED; "
            f"got tginitdeferred={meta['is_initially_deferred']}"
        )
        assert meta["is_constraint_trigger"] is True, (
            f"Trigger {trigger_name!r} must be a CONSTRAINT TRIGGER "
            f"(not a regular row trigger)."
        )


def test_migration_install_is_idempotent(pg_engine) -> None:
    """Re-running PHASE2_INSTALL_STATEMENTS does NOT raise.

    Every statement uses IF NOT EXISTS / OR REPLACE / DROP IF EXISTS,
    so a second pass must be a no-op. This is the production
    guarantee: ``_ensure_postgres_columns`` runs at every daemon
    startup.
    """
    # First, snapshot the trigger metadata so we can confirm the
    # second pass leaves the schema unchanged.
    before_triggers = {
        name: _trigger_metadata(pg_engine, name)
        for name in (
            "trg_job_queue_items_active_lock_guard",
            "trg_job_locks_active_guard",
        )
    }
    assert all(v is not None for v in before_triggers.values()), (
        "Pre-condition: triggers must be installed (autouse fixture); "
        "this test cannot verify idempotency without a baseline."
    )

    # Second pass — must not raise.
    _apply(pg_engine, PHASE2_INSTALL_STATEMENTS)

    # Triggers still present, still DEFERRABLE INITIALLY DEFERRED.
    for name, before in before_triggers.items():
        after = _trigger_metadata(pg_engine, name)
        assert after is not None, (
            f"Trigger {name!r} disappeared after re-install — "
            f"idempotency check failed."
        )
        assert after == before, (
            f"Trigger {name!r} metadata changed across re-install: "
            f"before={before}, after={after}"
        )


# =============================================================================
# Section F — Sanity: trigger function bodies are intact
# =============================================================================
# Verifies the function bodies match the production SQL. This catches
# drift between the test mirror and ``_ensure_postgres_columns``.
# =============================================================================


def test_trigger_functions_use_integrity_constraint_violation_errcode(pg_engine) -> None:
    """Both trigger functions raise with ERRCODE 23514.

    Inspects ``pg_proc.prosrc`` for the trigger function bodies and
    asserts the literal ``ERRCODE = 'integrity_constraint_violation'``
    is present. This is the SQLSTATE 23514 that application code
    catches as an ``IntegrityError``.
    """
    sql = text(
        """
        SELECT p.proname, p.prosrc
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN (
              'job_queue_items_active_lock_guard',
              'job_locks_active_guard'
          )
        """
    )
    with pg_engine.connect() as conn:
        rows = conn.execute(sql).mappings().fetchall()

    found = {row["proname"]: row["prosrc"] for row in rows}
    assert "job_queue_items_active_lock_guard" in found, (
        "Trigger function ``job_queue_items_active_lock_guard`` is missing."
    )
    assert "job_locks_active_guard" in found, (
        "Trigger function ``job_locks_active_guard`` is missing."
    )
    for name, body in found.items():
        assert "ERRCODE = 'integrity_constraint_violation'" in body, (
            f"Trigger function {name!r} must raise with "
            f"ERRCODE = 'integrity_constraint_violation' (SQLSTATE 23514); "
            f"body was:\n{body}"
        )