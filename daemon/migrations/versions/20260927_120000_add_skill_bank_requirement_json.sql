-- Migration: add requirement_json column to skill_bank (P3-WP2 wiring fix)
--
-- Created: 2026-09-27
-- Author: dev-coder-p3-injectgate-fix (designer-agent P3 — proof (b) fix 1/3)
-- Description:
--   Adds ``skill_bank.requirement_json`` (TEXT, nullable) carrying the
--   serialized :class:`CapabilityRequirement` parsed from the
--   ``requires:`` block of the originating ``skill-set.yaml`` entry.
--   This makes the requirement durable on the bank template so the
--   injection-time capability gate (P3-WP2 acceptance: "a single
--   function call from the skill-body instruction loader") can look it
--   up via ``Skill.source_skill_bank_id`` without re-parsing the
--   template body or maintaining a separate in-memory map.
--
--   Day-1 contract: ``NULL`` when the entry omits ``requires:`` —
--   back-compat for every existing repo skill (zero behavior change at
--   injection time — the gate is a no-op when the requirement is empty
--   or absent).
--
-- DUAL-DRIVER NOTES:
--   For SQLite (this file): ALTER TABLE ADD COLUMN.
--   For PostgreSQL: the equivalent statement lives in
--     ``daemon/manager.py::_ensure_postgres_columns`` (the migration
--     runner is SQLite-only by design — runner.py:719-727). The mirror
--     statement is added in the same commit so fresh PG databases get
--     the column via ``SQLModel.metadata.create_all()`` from the
--     SkillBankItem model declaration.

-- UP

ALTER TABLE skill_bank ADD COLUMN requirement_json TEXT;

-- DOWN
-- SQLite ALTER TABLE DROP COLUMN support arrived in 3.35; safe no-op
-- for older engines because the column add is also a no-op there.
ALTER TABLE skill_bank DROP COLUMN requirement_json;
