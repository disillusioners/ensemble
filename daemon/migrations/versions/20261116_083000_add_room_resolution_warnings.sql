-- Migration: add the resolution-warnings column to room_messages (group chat P5)
-- Created: 2026-11-16
-- PRECONDITION: sqlite>=3.35.0
-- Phase: group-chat P5 — room tool + guidelines + digest (A-P3-16 disposition)
--
-- Adds ONE nullable column to ``room_messages``:
--   * resolution_warnings — the DURABLE @tag / addressed_to resolution
--     warning set: a JSON array of
--     {"kind": "tag"|"addressed", "name": str, "reason": str} entries.
--
-- WHY A NEW COLUMN (A-P3-16 — the decision rule was applied, not skipped):
-- the existing JSONB columns ``tagged_members`` / ``addressed_members``
-- were EVALUATED as carriers and REFUSED for both. Each is a typed
-- ``list[str] | None`` carrying a distinct routing semantic
-- (escalation vs addressing), and a warning-shaped entry would
-- (a) violate the payload contract every reader assumes
-- (``list(tagged_members or [])`` normalization; the TAGGED filter in
-- ``derive_undelivered_tagged`` would lane-route unresolvable names on
-- every boot), and (b) still need a shaped map (name -> reason) that a
-- ``list[str]`` cannot carry cleanly. A dedicated column keeps the
-- routing columns machine-shaped and the warnings human-shaped.
--
-- The column is written AT APPEND time (the log is append-only — there
-- is no UPDATE path for a room_messages row), so the warnings ride the
-- same INSERT statement as the post body.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on startup.
--     ALTER TABLE ... ADD COLUMN is idempotent-tolerated by the runner
--     ("duplicate column name" -> skip).
--   - PostgreSQL: the column arrives via _ensure_postgres_columns
--     (ALTER ... ADD COLUMN IF NOT EXISTS); fresh PG databases get it
--     from SQLModel.metadata.create_all via the RoomMessage SQLModel
--     declaration (daemon/repositories/room/models.py).
--   - Rollback = drop the column (SQLite 3.35+ supports DROP COLUMN;
--     the runner gates on sqlite>=3.35.0). Additive-only — no data
--     rewrite, no table rebuild; rollback_safe=true.

-- UP

ALTER TABLE room_messages ADD COLUMN resolution_warnings JSON;

-- DOWN

ALTER TABLE room_messages DROP COLUMN resolution_warnings;
