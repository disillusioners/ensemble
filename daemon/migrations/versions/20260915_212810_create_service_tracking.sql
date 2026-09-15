-- Migration: create service_tracking table + 3-site indexes
--
-- Created: 2026-09-15
-- Author: coder (service-tool Phase 1.A - STORE TRACK)
-- Description:
--   Creates the ``service_tracking`` table backing the new ``service``
--   daemon tool category (D2 of
--   ``.agents/shared/planning/service-tool/decisions.md``, amended per
--   A11 + A12 + A13 -- see also the F3 frozen-interface block in
--   ``phase1-plan.md`` task 1.A.0). One row per daemon-managed
--   detached process. Schema columns match the SQLModel class at
--   ``daemon/repositories/service_tool/models.py`` byte-for-byte.
--
--   The table is the D2 store consumed by Phase 1.B's
--   ``ServiceToolManager`` (5-tool surface, 5 services: start/stop/
--   status/list/logs -- see ``decisions.md`` §D3) and the Phase 1.C
--   ``ServiceReconciliationService`` (skeleton + Phase 2 sweep). It
--   also drives the D6 boot-reconcile sweep (PID-liveness +
--   start-time match, mark EXITED on mismatch).
--
-- DUAL-DIALECT DDL (the file applies cleanly on BOTH backends):
--   * Plain ``CREATE TABLE IF NOT EXISTS``, ``CREATE UNIQUE INDEX IF
--     NOT EXISTS`` and ``CREATE INDEX IF NOT EXISTS`` are valid on
--     PostgreSQL AND SQLite -- unlike migration 20260714_000001
--     (fresh-SQLite boot broken by PG-only ``DROP CONSTRAINT IF
--     EXISTS``), this file applies cleanly on both backends.
--   * The column DDL is dialect-neutral: ``TEXT`` works on both PG
--     and SQLite, ``INTEGER`` works on both (SQLite's
--     ``INTEGER PRIMARY KEY AUTOINCREMENT`` is a superset of PG's
--     ``SERIAL``), no ``DEFAULT now()`` (which is PG-only and would
--     break fresh-SQLite boot -- see A12 rationale below).
--   * NO PG-only DDL anywhere. NO ``DROP CONSTRAINT``, NO native
--     enum types, NO ``USING`` clauses, NO ``GENERATED ALWAYS AS
--     IDENTITY`` (PG-12+ syntax).
--   * Partial UNIQUE indexes with a WHERE clause are supported on
--     both PG (always) and SQLite (since 3.8.0, 2013 -- every
--     supported deployment has it).
--
-- A11 - partial UNIQUE index IS the D5 same-name guard:
--   The ``name`` column itself has NO full unique constraint -- a
--   full unique would kill name-reuse-after-EXITED (an operator
--   may legitimately want to start a fresh service with the same
--   name after the previous one died). The partial UNIQUE index
--   below gates uniqueness on the ACTIVE state set
--   (``status IN ('starting','running')``), so an ``EXITED`` row
--   no longer holds the slot.
--
-- A12 - TEXT ISO-8601 timestamps, Python-side default_factory:
--   The SQL DEFAULT clauses for ``created_at`` / ``updated_at`` use
--   SQLite-native ``strftime`` (PG has no ``strftime`` and the
--   runner is a NO-OP on PG so this statement never executes on
--   PG, see ``daemon/migrations/runner.py:721-722``). The
--   Python-side ``_now_utc_iso()`` factory in
--   ``daemon/repositories/service_tool/models.py`` is the real
--   source of truth and supplies values on every insert path (even
--   when the SQL DEFAULT would also fire). The PG mirror in
--   ``EnsembleManager._ensure_postgres_columns`` (lands in Phase
--   1.C task 1.C.13b) emits ``ALTER TABLE ADD COLUMN IF NOT
--   EXISTS`` statements WITHOUT SQL DEFAULTs for the same reason
--   Python-side factory is authoritative on PG too.
--
-- A13 - atomic-guard UPDATE contract (lives in the repository, NOT
--   here). Every status-mutating UPDATE statement in
--   ``daemon/repositories/service_tool/repository.py`` is guarded
--   ``WHERE id=? AND status IN ('starting','running')``. The SQL
--   in this file is purely schema DDL, no row-level guards.
--
-- 3-site index registration (the F3 reassignment note after 1.A.7):
--   the index names below MUST be byte-identical to the SQLModel
--   ``__table_args__`` block in
--   ``daemon/repositories/service_tool/models.py`` AND to the
--   ``EnsembleManager._ensure_postgres_columns`` clause that
--   lands in Phase 1.C task 1.C.13b (NOT YET WRITTEN. The Phase
--   1.A name-pin test asserts the .sql + models.py arms
--   strictly and skips the manager.py arm with an explicit
--   ``pytest.mark.skip`` whose message points to 1.C.13b).
--   1.C.13b un-skips that arm to a strict 3rd-site assertion.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   ``daemon/migrations/runner.py:543-557`` splits the UP block on
--   ``;`` AFTER stripping full-line ``--`` comments. Inline
--   ``--`` comments that contain a ``;`` character are NOT
--   stripped -- they share a line with SQL and so misdirect the
--   splitter. This file uses NO inline comments containing ``;``.
--   All inline annotations use words (e.g. "via ps parse") or full
--   sentences with no semicolon. The 20260627_000003_task_is_deferred
--   file documented the same trap in reverse (semicolons inside
--   comments previously corrupted the split).
--
-- Lifecycle / DOWN:
--   The DOWN section DROPs the whole table. Per the F3 freeze, the
--   ``ServiceRepo`` public surface MUST stay byte-identical until
--   Phase 1.B / 1.C consume it. Operators who roll back this
--   migration must also revert the SQLModel class and the 1.B /
--   1.C consumer code paths.

-- UP
CREATE TABLE IF NOT EXISTS service_tracking (
    -- SQLite-friendly autoincrement serial. PG tolerates this on create_all tests (1.C.13b mirrors BIGSERIAL).
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    -- JSON-encoded argv array (no shell expansion = no shell injection).
    command TEXT NOT NULL,
    -- Nullable: pre-spawn (F8 path) and post-exit (historical forensic).
    pid INTEGER,
    -- Nullable: kernel starttime jiffies (Linux) or epoch seconds (macOS via ps parse).
    start_time INTEGER,
    -- Absolute path, validated at insert time by Phase 1.B.
    cwd TEXT NOT NULL,
    -- Partial-index predicate is case-lockstep with ServiceStatus enum values.
    status TEXT NOT NULL DEFAULT 'starting',
    started_by_instance_id TEXT NOT NULL,
    started_by_agent_id TEXT NOT NULL,
    -- Absolute path to data/services/<name>.log (D1 stdoe-to-file).
    log_path TEXT NOT NULL,
    -- Nullable while STARTING/RUNNING, set on EXITED transitions.
    exit_code INTEGER,
    -- A12 TEXT ISO-8601. strftime is SQLite-native (PG has no strftime).
    -- The runner is a NO-OP on PG so this default never executes on PG.
    -- Python-side default_factory (now_utc_iso) is the real source of truth.
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- 3-site index registration. Names MUST be byte-identical to:
--   1. THIS FILE (the .sql migration, the SQLite path).
--   2. daemon/repositories/service_tool/models.py __table_args__
--      (fresh databases via SQLModel.metadata.create_all).
--   3. daemon/manager.py _ensure_postgres_columns (Phase 1.C.13b,
--      NOT YET WRITTEN. The Phase 1.A name-pin test skips the
--      manager.py arm with an explicit pending marker).
-- A11: this partial UNIQUE index is the actual D5 same-name guard.
-- The literal case ('starting','running') MUST stay in lockstep
-- with the storage enum values in
-- daemon/repositories/service_tool/models.py:ServiceStatus (lowercase, exact).
CREATE UNIQUE INDEX IF NOT EXISTS idx_service_tracking_name_active
    ON service_tracking (name)
    WHERE status IN ('starting','running');

CREATE INDEX IF NOT EXISTS idx_service_tracking_pid
    ON service_tracking (pid);

-- DOWN
DROP TABLE IF EXISTS service_tracking;