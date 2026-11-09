-- Migration: create the Memoir Store v2 substrate tables
--           (Constellation: star / star_alias / line, reflection state,
--           proposal ledger, JEV ledger) and evolve memoir_episodes
-- Created: 2026-11-09
-- Phase: Memoir Store v2 Phase 1 (plan:
--   .agents/shared/planning/ari-memoir-store/v2-phase1-plan.md).
--
-- What lives here:
--   * ``memoir_star``            — the canonical entity NODES of the
--     Constellation (one row per person / project / place / concept /
--     domain). Carries the two-axis time stamps (``first_seen_at`` /
--     ``last_touched_at``), the heat accumulator and the 24h
--     summarization cooldown watermark (``description_as_of``).
--   * ``memoir_star_alias``      — alternative surface forms resolved
--     to a canonical star ("the Emperor" -> the Emperor node).
--   * ``memoir_line``            — the weighted co-occurrence EDGES
--     between a star and an episode, carrying the ``last_pass_id``
--     crash-recovery guard (SF2 / V2-29).
--   * ``memoir_reflection_state``— the durable nightly watermark and
--     claimant row.
--   * ``memoir_proposal_ledger`` — the DURABLE keepsake-proposal and
--     notification ledger (C3 / W2 / V2-36).
--   * ``memoir_jev_ledger``      — the DURABLE JEV classification and
--     candidate-retry ledger (B2 / V2-38 / ND-3 / ND-14).
--   * ``memoir_episodes``        — EVOLVED by four plain ``ADD COLUMN``
--     statements (``poignancy_status``, ``poignancy``,
--     ``poignancy_attempts``, ``reflected_at``) plus one
--     cutover-bounded legacy seed UPDATE.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup. One .sql per migration event — this file is the
--     persistent record.
--   - PostgreSQL: the schema comes from
--     ``SQLModel.metadata.create_all`` with the models in
--     ``daemon/repositories/memoir/models.py`` as the canonical source.
--     The models register into the metadata via the late-import block
--     in ``daemon/manager.py`` BEFORE the create_all call (the
--     app_settings / resource_monitor / board / bash_spawn / PSL /
--     memoir-v1 precedent). The PG-side runner is a NO-OP for this
--     file.
--   - EVERY STRING COLUMN IS TEXT (D1.2 / AM-6) — never VARCHAR, on
--     either driver. Note the two artifacts are NOT type-for-type
--     equivalent and must not be read as such: the model's SQLite DDL
--     emits JSON affinity for ``embedding`` / ``source_episode_ids``
--     (benign under SQLite's dynamic typing), while this file declares
--     them TEXT so the file stays SQLite-valid. On PostgreSQL the
--     models render ``embedding JSONB`` and ``source_episode_ids
--     JSONB``.
--   - 3-site index lockstep: the index names AND column lists below are
--     byte-identical to the ``__table_args__`` of the models (and to
--     the schema pack's parity assertion). The two DESC-ordered
--     indexes (``idx_memoir_star_agent_heat``,
--     ``idx_memoir_line_agent``) cannot be expressed with the string
--     column references ``__table_args__`` accepts, so — exactly like
--     ``idx_memoir_ep_agent_created`` in the v1 file — they are declared
--     in the model module AFTER the class, against the live table
--     columns.
--   - The ``reflected_at`` column is ``TIMESTAMPTZ`` on PostgreSQL
--     (the model is ``DateTime(timezone=True)``). It is added to
--     existing PG databases by the ``_ensure_postgres_columns`` ensure
--     in ``daemon/manager.py`` — NOT here (the runner is SQLite-only).
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path ``SQLModel.metadata.create_all(self._engine)`` runs
--   FIRST and the migration runner applies this file IMMEDIATELY AFTER
--   it. By the time this file is applied, create_all has already
--   created the six tables AND the model-declared indexes. A bare
--   CREATE would therefore raise "already exists" and the runner would
--   SWALLOW that error on its idempotent-skip branch — the failure
--   would be silent and this file would degrade into a no-op on every
--   boot. IF NOT EXISTS removes both failure modes and makes the file
--   applicable in the real boot order.
--
-- WHY THE ALTER STATEMENTS ARE PLAIN ``ADD COLUMN`` (MANDATORY):
--   ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` is a HARD SYNTAX ERROR
--   in SQLite (SQLite has no such form) and would raise
--   ``MigrationError`` and break EVERY fresh-SQLite boot. The runner
--   already tolerates the column-exists collision on a fresh SQLite
--   database (create_all created the column first), so the plain form
--   is both correct and idempotent. The ``IF NOT EXISTS`` forms live
--   ONLY in the PostgreSQL ensure in ``daemon/manager.py``.
--
-- THE CUTOVER LITERAL IS SPACE-SEPARATED (SF11 / V2-33):
--   ``'2026-11-09 00:00:00'`` — NOT the ISO-8601 ``'T'`` form. SQLite
--   stores datetimes as TEXT, so the cutover comparison is LEXICOGRAPHIC;
--   with the ``T`` separator the literal sorts AFTER every stored
--   ``' '``-separated timestamp of the same date (``'T'`` = 0x54 >
--   ``' '`` = 0x20) and the seed would misclassify the whole day.
--
-- ROLLBACK POSTURE:
--   Additive only. The DOWN section drops the six tables (children
--   first, so the self-referencing foreign keys stay order-safe). The
--   four ``ADD COLUMN`` statements are ADDITIVE-ONLY and are retained
--   in the standard forward-only migration posture — dropping a column
--   would destroy the ``poignancy`` / ``reflected_at`` history, so the
--   release rollback is FIX-LOSS, never an incompatibility: the
--   pre-lane tree references none of these tables and none of these
--   columns.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner before the
--   statement split, and inline (trailing) comments are left alone —
--   a semicolon inside an inline comment corrupts the statement
--   boundaries. This file uses no semicolon in any comment.

-- UP

-- 1. memoir_star: Canonical entity nodes in the Constellation
CREATE TABLE IF NOT EXISTS memoir_star (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    star_type TEXT NOT NULL CHECK (star_type IN ('person', 'project', 'place', 'concept', 'domain')),
    description TEXT NOT NULL DEFAULT '',
    description_as_of DATETIME,
    first_seen_at DATETIME NOT NULL,
    last_touched_at DATETIME NOT NULL,
    heat REAL NOT NULL DEFAULT 0.0 CHECK (heat >= 0.0),
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_star_agent_name UNIQUE (agent_id, name)
);
CREATE INDEX IF NOT EXISTS idx_memoir_star_agent_heat ON memoir_star (agent_id, heat DESC);

-- 2. memoir_star_alias: Alternative surface forms resolved to canonical stars
CREATE TABLE IF NOT EXISTS memoir_star_alias (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    star_id TEXT NOT NULL REFERENCES memoir_star(id),
    alias TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_alias_agent_alias UNIQUE (agent_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_memoir_star_alias_star ON memoir_star_alias (star_id);

-- 3. memoir_line: Weighted episodic co-occurrence edges (with last_pass_id guard, SF2)
CREATE TABLE IF NOT EXISTS memoir_line (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    star_id TEXT NOT NULL REFERENCES memoir_star(id),
    episode_id TEXT NOT NULL REFERENCES memoir_episodes(id),
    mention_count INTEGER NOT NULL DEFAULT 1 CHECK (mention_count > 0),
    last_pass_id TEXT,
    first_mentioned_at DATETIME NOT NULL,
    last_mentioned_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_line_star_episode UNIQUE (star_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_memoir_line_episode ON memoir_line (episode_id);
CREATE INDEX IF NOT EXISTS idx_memoir_line_agent ON memoir_line (agent_id, last_mentioned_at DESC);

-- 4. memoir_reflection_state: Durable watermark and claimant tracking
CREATE TABLE IF NOT EXISTS memoir_reflection_state (
    id TEXT NOT NULL PRIMARY KEY,
    last_nightly_date TEXT,
    claimed_at DATETIME
);

-- 5. memoir_proposal_ledger: Durable keepsake proposal and notification ledger (C3 / W2 / B1')
CREATE TABLE IF NOT EXISTS memoir_proposal_ledger (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    episode_id TEXT NOT NULL REFERENCES memoir_episodes(id),
    proposal_date TEXT NOT NULL,
    proposed_at DATETIME NOT NULL,
    notification_status TEXT NOT NULL CHECK (notification_status IN ('pending', 'enqueued', 'sent', 'failed')),
    notified_at DATETIME,
    reason TEXT NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_proposal_agent_episode UNIQUE (agent_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_memoir_proposal_ledger_date ON memoir_proposal_ledger (agent_id, proposal_date);

-- 6. memoir_jev_ledger: Durable JEV classification and candidate retry ledger (B2 / V2-38 / ND-3 / ND-14)
CREATE TABLE IF NOT EXISTS memoir_jev_ledger (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    synthesis_id TEXT NOT NULL REFERENCES memoir_syntheses(id),
    classification TEXT CHECK (classification IS NULL OR classification IN ('conflict', 'boundary_change', 'new_rule', 'reinforcement', 'none')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'drafted', 'suppressed', 'terminal')),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_jev_synthesis UNIQUE (agent_id, synthesis_id)
);
CREATE INDEX IF NOT EXISTS idx_memoir_jev_status ON memoir_jev_ledger (agent_id, status);

-- 7. Evolution of memoir_episodes: PLAIN ADD COLUMN statements (SQLite-safe, Register #1)
ALTER TABLE memoir_episodes ADD COLUMN poignancy_status TEXT;
ALTER TABLE memoir_episodes ADD COLUMN poignancy INTEGER;
ALTER TABLE memoir_episodes ADD COLUMN poignancy_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memoir_episodes ADD COLUMN reflected_at DATETIME;

-- 8. Cutover-bounded legacy seed UPDATE (Register #5 / SF11 space-separated literal)
UPDATE memoir_episodes SET poignancy_status = 'legacy' WHERE poignancy_status IS NULL AND created_at < '2026-11-09 00:00:00';

-- DOWN
--
-- Order-safe ADDITIVE rollback: the child tables are dropped BEFORE the
-- parent so the rollback path is order-safe with the foreign keys in
-- place, and the four column additions are additive-only and RETAINED
-- in the standard forward-only migration posture (dropping a column
-- would destroy the poignancy / reflected_at history).

DROP INDEX IF EXISTS idx_memoir_jev_status;
DROP TABLE IF EXISTS memoir_jev_ledger;
DROP INDEX IF EXISTS idx_memoir_proposal_ledger_date;
DROP TABLE IF EXISTS memoir_proposal_ledger;
DROP INDEX IF EXISTS idx_memoir_line_agent;
DROP INDEX IF EXISTS idx_memoir_line_episode;
DROP TABLE IF EXISTS memoir_line;
DROP INDEX IF EXISTS idx_memoir_star_alias_star;
DROP TABLE IF EXISTS memoir_star_alias;
DROP INDEX IF EXISTS idx_memoir_star_agent_heat;
DROP TABLE IF EXISTS memoir_star;
DROP TABLE IF EXISTS memoir_reflection_state;
