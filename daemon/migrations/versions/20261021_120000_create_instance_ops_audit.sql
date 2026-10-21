-- Migration: create the instance_ops audit ledger
--           (nest-ops — the `instance_ops` privileged tool category)
-- Created: 2026-10-21
-- Phase: nest-ops (plan:
--   .agents/shared/planning/nest-ops/).
--
-- What lives here:
--   * ``instance_ops_audit`` — the append-only ledger behind the
--     `instance_ops` privileged tool category. One row per ACT
--     attempt: the dry-run preview, the refusal, the Tier-2
--     write-ahead intent, and the committed outcome.
--
-- WHY A DEDICATED TABLE (D-2):
--   The commission's "reuse existing tables" line has no table to
--   reuse. `event` is an SSE fan-out store (NOT an audit ledger);
--   the ari-tools board/self-alarm surfaces have NO audit table at
--   all; `secrets_audit` is a domain-specific resolution log; the
--   upgrade lane's `restart_audits` is file-backed JSON inside
--   `releases/state.json`. `repair_log` is the right SHAPE but lives
--   in another privileged category's schema home and carries six
--   SQL-repair-specific columns whose `sql_class` enum a new value
--   would pollute. Copying the vocabulary gives house consistency
--   WITHOUT cross-category coupling.
--
-- THE AUDIT BOUNDARY IS A TWO-TIER PROPERTY (D-15) — declared here,
-- in the model docstring, and in the writer's docstring:
--
--   TIER 1 (true same-transaction): `instance_terminate` (via the
--   additive `audit_sink` hook inside the lifecycle service's own
--   WriteGuardSession) and `instance_mark(protect|suspect)` (via the
--   optional `session=` on `repo.set_metadata`). The act and the
--   audit row commit TOGETHER or NEITHER commits — a destructive act
--   that cannot be audited NEVER happens.
--
--   TIER 2 (write-ahead): `instance_nudge` / `instance_answer` /
--   `instance_dismiss` / `instance_reclaim` / `instance_mark(park)`
--   have no single transaction to join — they are daemon service calls
--   that own their own transactions, and the question pack lives in
--   memory. They INSERT the row FIRST with outcome='pending' and the
--   full intent, run the act, then UPDATE the row to 'committed' /
--   'error'. A crash mid-act leaves a durable 'pending' row.
--
--   The two tiers are DECLARED, not silently de-coupled (R11's rule,
--   generalized and recorded rather than used to cut three real
--   tools).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     `SQLModel.metadata.create_all` with the :class:`InstanceOpsAudit`
--     model (`daemon/repositories/instance_ops/models.py`) as the
--     canonical source (the secrets + secrets_audit precedent). The
--     models register into the metadata via the late-import block in
--     `daemon/api.py` BEFORE the create_all call. Brand-new tables
--     need NO `_ensure_postgres_columns` mirror. The PG-side runner
--     is a NO-OP for this file.
--   - Column names and types stay byte-identical to the model
--     (canonical source for PG). Every string column is TEXT on both
--     drivers — never VARCHAR.
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path `SQLModel.metadata.create_all(self._engine)` runs
--   FIRST and the migration runner applies this file IMMEDIATELY
--   AFTER it. By the time this file is applied, create_all has
--   already created the table AND its indexes. A bare CREATE would
--   therefore raise "already exists" and the runner would SWALLOW
--   that error on its idempotent-skip branch — the failure would be
--   silent and this file would degrade into a no-op on every boot.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line `--` comments are stripped by the runner before the
--   statement split, and inline (trailing) comments are left alone —
--   a semicolon inside an inline comment corrupts the statement
--   boundaries. This file uses no semicolon in any comment.
--
-- PRE-EXISTING BLOCKERS (NOT caused by this migration):
--   * the duplicate-prefix `20261018_000001` was fixed at
--     `811d3e21b` (both files renamed) and the runner now has a loud
--     `DuplicateMigrationVersionError` guard;
--   * a fresh-SQLite boot is still blocked by a DIFFERENT pre-existing
--     prefix (`20260714_000001`, `20240101_000001`) — those runners
--     die before any new migration. This migration is unaffected; a
--     fresh-SQLite boot test of this commission WILL fail for that
--     reason — the test plan runs against scratch PG, not fresh
--     SQLite.

-- UP

CREATE TABLE IF NOT EXISTS instance_ops_audit (
    id TEXT NOT NULL PRIMARY KEY,
    created_at TEXT NOT NULL,
    created_by_instance_id TEXT,
    created_by_agent_id TEXT,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    dry_run INTEGER NOT NULL DEFAULT 0,
    before_snapshot JSON,
    after_snapshot JSON,
    outcome TEXT NOT NULL,
    confirmed_target_id TEXT,
    provenance_class TEXT,
    provenance_depth INTEGER,
    root_source TEXT,
    root_message_id TEXT,
    error_message TEXT,
    preview_run_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_instance_ops_audit_action_created
    ON instance_ops_audit (action, created_at);

CREATE INDEX IF NOT EXISTS idx_instance_ops_audit_target
    ON instance_ops_audit (target);

CREATE INDEX IF NOT EXISTS idx_instance_ops_audit_outcome_created
    ON instance_ops_audit (outcome, created_at);

-- DOWN

DROP INDEX IF EXISTS idx_instance_ops_audit_outcome_created;
DROP INDEX IF EXISTS idx_instance_ops_audit_target;
DROP INDEX IF EXISTS idx_instance_ops_audit_action_created;
DROP TABLE IF EXISTS instance_ops_audit;
