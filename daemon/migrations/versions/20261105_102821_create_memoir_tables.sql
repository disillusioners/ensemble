-- Migration: create the four memoir tables
--           (Memoir Store v1 — layer 1 episodes, layer 3 persona,
--           layer 4 syntheses)
-- Created: 2026-11-05
-- Phase: Memoir Store v1 Phase 1 (plan:
--   .agents/shared/planning/ari-memoir-store/phase1-plan.md).
--
-- What lives here:
--   * ``memoir_episodes``       — layer 1 (the append-only, immutable
--     first-person narrative rows) AND layer 2 (keepsakes are a STATUS
--     on the episode row: ``is_keepsake`` + the pin columns).
--   * ``memoir_persona``        — layer 3, the pointer row: which
--     persona version is currently in force.
--   * ``memoir_persona_versions`` — layer 3, the versioned persona
--     document itself. The transition vocabulary is mirrored from the
--     prompt-version lane, so the human lane is enforced by the data.
--   * ``memoir_syntheses``      — layer 4. SCHEMA ONLY in v1 and EMPTY
--     (DD-2): the table exists so v2 adds a BEHAVIOR, not migration
--     churn. Nothing writes to it in v1.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     ``SQLModel.metadata.create_all`` with the models in
--     ``daemon/repositories/memoir/models.py`` as the canonical source.
--     The models register into the metadata via the late-import block
--     in ``daemon/manager.py`` BEFORE the create_all call (the
--     app_settings / resource_monitor / board / bash_spawn precedent).
--     The PG-side runner is a NO-OP for this file.
--   - EVERY STRING COLUMN IS TEXT (D1.2 / AM-6) — never VARCHAR, on
--     either driver. Note the two artifacts are NOT type-for-type
--     equivalent and must not be read as such: the model's SQLite DDL
--     emits JSON affinity for ``stars`` / ``embedding`` /
--     ``source_episode_ids`` (benign under SQLite's dynamic typing),
--     while this file declares them TEXT so the file stays
--     SQLite-valid. On PostgreSQL the models render ``stars TEXT[]``,
--     ``embedding JSONB`` and ``source_episode_ids JSONB``.
--   - 3-site index lockstep: the index names AND column lists below are
--     byte-identical to the ``__table_args__`` of the models (and to
--     the schema pack's parity assertion).
--   - THE ``tsv`` COLUMN IS NOT HERE. It is a PostgreSQL-only GENERATED
--     column (``to_tsvector`` does not exist in SQLite), so it is added
--     by the ``_ensure_postgres_columns`` ensure in ``daemon/manager.py``
--     as an idempotent ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...``
--     plus a GIN index. The expression uses the TWO-argument
--     ``to_tsvector('english', ...)`` form — the one-argument form is
--     STABLE and PostgreSQL rejects it ("generation expression is not
--     immutable"), proven on PG 16.15 during phase 1.
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path ``SQLModel.metadata.create_all(self._engine)`` runs
--   FIRST and the migration runner applies this file IMMEDIATELY AFTER
--   it. By the time this file is applied, create_all has already
--   created the four tables AND the model-declared indexes. A bare
--   CREATE would therefore raise "already exists" and the runner would
--   SWALLOW that error on its idempotent-skip branch — the failure
--   would be silent and this file would degrade into a no-op on every
--   boot. IF NOT EXISTS removes both failure modes and makes the file
--   applicable in the real boot order.
--
-- ROLLBACK POSTURE:
--   Additive only. The DOWN section drops the four tables (children
--   first). Rollback of the RELEASE is therefore FIX-LOSS, never an
--   incompatibility: the pre-lane tree references none of these tables,
--   so the old binary simply never touches them.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner before the
--   statement split, and inline (trailing) comments are left alone —
--   a semicolon inside an inline comment corrupts the statement
--   boundaries. This file uses no semicolon in any comment.

-- UP

CREATE TABLE IF NOT EXISTS memoir_episodes (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    title TEXT,
    body TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    event_at DATETIME NOT NULL,
    importance INTEGER NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    stars TEXT,
    is_keepsake BOOLEAN NOT NULL DEFAULT false,
    pinned_by TEXT CHECK (pinned_by IS NULL OR pinned_by IN ('empress', 'emperor')),
    pinned_reason TEXT,
    pinned_at DATETIME,
    last_accessed_at DATETIME,
    access_count INTEGER NOT NULL DEFAULT 0,
    embedding TEXT,
    embedding_model TEXT,
    embedding_dims INTEGER,
    embedded_at TEXT,
    embedding_attempts INTEGER NOT NULL DEFAULT 0,
    embedding_error TEXT,
    superseded_by TEXT REFERENCES memoir_episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_memoir_ep_agent_created
    ON memoir_episodes (agent_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memoir_ep_agent_event
    ON memoir_episodes (agent_id, event_at);

CREATE INDEX IF NOT EXISTS idx_memoir_ep_keepsakes
    ON memoir_episodes (agent_id, created_at) WHERE is_keepsake;

CREATE INDEX IF NOT EXISTS idx_memoir_ep_pending_emb
    ON memoir_episodes (agent_id) WHERE embedding IS NULL;

CREATE TABLE IF NOT EXISTS memoir_persona (
    agent_id TEXT NOT NULL PRIMARY KEY,
    current_version_id TEXT,
    updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS memoir_persona_versions (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected', 'archived')),
    source TEXT NOT NULL CHECK (source IN ('seed', 'ari', 'operator')),
    submitted_at TEXT,
    approved_at TEXT,
    CONSTRAINT uq_memoir_persona_versions_agent_version UNIQUE (agent_id, version)
);

CREATE INDEX IF NOT EXISTS idx_memoir_persona_versions_agent
    ON memoir_persona_versions (agent_id, version);

CREATE TABLE IF NOT EXISTS memoir_syntheses (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    source_episode_ids TEXT,
    valid_from TEXT,
    valid_to TEXT,
    verb TEXT NOT NULL CHECK (verb IN ('CREATE', 'CORROBORATE', 'REFINE', 'CORRECT')),
    supersedes TEXT,
    superseded_by TEXT,
    confidence REAL,
    created_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memoir_syntheses_agent
    ON memoir_syntheses (agent_id, created_at);

-- DOWN
--
-- The child tables are dropped BEFORE the parent so the rollback path
-- is order-safe with the self-referencing foreign key in place.

DROP INDEX IF EXISTS idx_memoir_syntheses_agent;
DROP TABLE IF EXISTS memoir_syntheses;
DROP INDEX IF EXISTS idx_memoir_persona_versions_agent;
DROP TABLE IF EXISTS memoir_persona_versions;
DROP TABLE IF EXISTS memoir_persona;
DROP INDEX IF EXISTS idx_memoir_ep_pending_emb;
DROP INDEX IF EXISTS idx_memoir_ep_keepsakes;
DROP INDEX IF EXISTS idx_memoir_ep_agent_event;
DROP INDEX IF EXISTS idx_memoir_ep_agent_created;
DROP TABLE IF EXISTS memoir_episodes;
