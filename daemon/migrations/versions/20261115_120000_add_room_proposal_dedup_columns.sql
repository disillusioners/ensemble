-- Migration: add proposal-dedup columns to room_messages (group chat P3)
-- Created: 2026-11-15
-- PRECONDITION: sqlite>=3.35.0
-- Phase: group-chat P3 — delivery & injection (R1 / Ruling A)
--
-- Adds three nullable columns to ``room_messages``:
--   * source_message_id  — id of the triggering post for a task_proposal row
--   * trigger            — 'tag_terminal' | 'addressed_terminal' (proposal rows only)
--   * member             — the member name the proposal is about (proposal rows only)
-- plus a UNIQUE index on (source_message_id, trigger, member) so the
-- dedup is mechanical: at most one proposal per (triggering post, trigger,
-- member). NULLs are distinct on both drivers, so ordinary message rows
-- (all three NULL) never collide.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on startup.
--     ALTER TABLE ... ADD COLUMN is idempotent-tolerated by the runner
--     ("duplicate column name" -> skip). CREATE UNIQUE INDEX IF NOT EXISTS
--     is natively idempotent.
--   - PostgreSQL: columns + index arrive via _ensure_postgres_columns
--     (ALTER ... ADD COLUMN IF NOT EXISTS + CREATE UNIQUE INDEX IF NOT
--     EXISTS); fresh PG databases get them from SQLModel.metadata.create_all
--     via the RoomMessage SQLModel declaration.
--   - Rollback = drop the index + drop the three columns (SQLite 3.35+
--     supports DROP COLUMN; the runner gates on sqlite>=3.35.0).

-- UP

ALTER TABLE room_messages ADD COLUMN source_message_id BIGINT;
ALTER TABLE room_messages ADD COLUMN trigger TEXT;
ALTER TABLE room_messages ADD COLUMN member TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_room_messages_proposal_dedup
    ON room_messages (source_message_id, trigger, member);

-- DOWN

DROP INDEX IF EXISTS uq_room_messages_proposal_dedup;
ALTER TABLE room_messages DROP COLUMN member;
ALTER TABLE room_messages DROP COLUMN trigger;
ALTER TABLE room_messages DROP COLUMN source_message_id;
