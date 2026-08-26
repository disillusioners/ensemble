-- Migration: Phase 1 C2 — message_metadata side table (Solution M)
-- Created: 2026-08-25
-- Author: planner[v2] (Rev 4 approved)
-- Description:
--   Side table for message timestamps + future sequence, fired by the
--   MessageTapSlot hook (Solution M; companion to PERF-1). Schema is the
--   minimal viable column set; the `seq` column is reserved nullable for
--   Phase 2 PERF-2 cursor pagination — adding it now avoids a future
--   ALTER on a populated table (option value, see decisions.md D5).
--
-- DUAL-DRIVER NOTES:
--   Applied by MigrationRunner ONLY when the engine dialect is SQLite.
--   Fresh PG databases receive the table from SQLModel.create_all()
--   (MessageMetadata SQLAlchemy model). Existing PG databases receive
--   CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS from
--   daemon/manager.py::_ensure_postgres_columns(). Index name MUST match.
--
-- CONSTRAINT: PRIMARY KEY (thread_id, message_id). Write path uses
-- INSERT ... ON CONFLICT DO NOTHING (PG) / INSERT OR IGNORE (SQLite) for
-- idempotency — see MessageMetadataRepository.upsert_batch.
--
-- REV 2 NOTE: hook reads NODE-RETURN persisted list, not post-LLM state;
-- see hook placement (decisions.md D1). Re-taps under ON CONFLICT
-- DO NOTHING are EXPECTED on revive + compaction, not anomalies.

-- UP
CREATE TABLE IF NOT EXISTS message_metadata (
    thread_id   TEXT    NOT NULL,
    message_id  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    seq         INTEGER,
    PRIMARY KEY (thread_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_message_metadata_thread
    ON message_metadata (thread_id);

-- DOWN
DROP INDEX IF EXISTS ix_message_metadata_thread;
DROP TABLE IF EXISTS message_metadata;
