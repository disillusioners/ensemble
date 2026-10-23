-- Migration: create the wedge-captures metadata table
--           (wedge-capture evidence instrumentation, incident 2026-10-22)
-- Created: 2026-10-22
-- Phase: wedge-capture (plan:
--   .agents/shared/planning/wedge-capture/).
--
-- What lives here:
--   * ``wedge_captures`` — ONE row per capture: a PATH REFERENCE plus a
--     small metrics snapshot. The capture's PAYLOAD lives on disk, in
--     the capture directory; this table is an INDEX over it.
--
-- WHY THE PAYLOAD IS NOT IN THE DB (D10):
--   The instrument this commission builds exists because the incident
--   of 2026-10-22 left an unreadable store behind: ~96.5k checkpoint
--   rows / ~13 GB, because multi-megabyte blobs were written into the
--   database. A capture payload is 1-5 MB (a full stack dump of a
--   large process). Storing it here would reproduce the exact failure
--   the instrument is measuring. The row is a path; the bytes are on
--   disk.
--
-- UNIQUENESS (A3):
--   ``capture_id`` and ``capture_dir`` are UNIQUE — in the model AND
--   in this file (A4, fix-forward: ``capture_id`` now carries its
--   UNIQUE constraint here too, matching the SQLModel column that PG's
--   create_all has always produced; this .sql is the persistent
--   record). The reconciler re-inserts the pending sidecars, which
--   dedup by an existence check PLUS these constraints — and a
--   duplicate capture directory is a bug worth failing on, not
--   absorbing silently.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     `SQLModel.metadata.create_all` with the :class:`WedgeCapture`
--     model (`daemon/repositories/wedge_captures/models.py`) as the
--     canonical source (the secrets + instance_ops + mcps_gateway
--     precedent). The model registers into the metadata via the
--     late-import block in `daemon/api.py` BEFORE the create_all call.
--     Brand-new tables need NO `_ensure_postgres_columns` mirror. The
--     PG-side runner is a NO-OP for this file.
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
-- ACCEPTANCE IS PG-ONLY (A10):
--   The migration count goes 108 -> 109 with this file. A fresh-SQLite
--   clean boot is NOT an acceptance gate for this commission — it
--   dies at the PRE-EXISTING `20260714_000001` migration
--   (`ALTER TABLE ... DROP CONSTRAINT` is not valid SQLite), which is
--   a different lane's debt and must not be mis-attributed here.

-- UP

CREATE TABLE IF NOT EXISTS wedge_captures (
    id TEXT NOT NULL PRIMARY KEY,
    capture_id TEXT NOT NULL UNIQUE,
    capture_dir TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    trigger_source TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL,
    boot_epoch TEXT,
    host TEXT,
    release_version TEXT,
    summary TEXT,
    auto_continue_mode TEXT,
    file_count INTEGER,
    total_bytes INTEGER,
    metrics_snapshot JSON,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wedge_captures_captured_at
    ON wedge_captures (captured_at);

CREATE INDEX IF NOT EXISTS idx_wedge_captures_trigger_reason
    ON wedge_captures (trigger_reason);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wedge_captures_capture_dir
    ON wedge_captures (capture_dir);

-- DOWN

DROP INDEX IF EXISTS idx_wedge_captures_capture_dir;
DROP INDEX IF EXISTS idx_wedge_captures_trigger_reason;
DROP INDEX IF EXISTS idx_wedge_captures_captured_at;
DROP TABLE IF EXISTS wedge_captures;
