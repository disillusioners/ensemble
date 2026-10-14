-- Migration: create custom_agent_configs
--           (agent-prompt-immutability commission, Phase C — User Directive #5A)
-- Created: 2026-10-14
-- Phase: agent-prompt-immutability Phase C (overnight, USER DIRECTIVE #5A).
--   Backing store for user-defined "custom agents" that have NO
--   ``.md`` template file in the install dir (R-A2). Custom agents
--   are DATABASE-BORN: the row IS the config (a meta.json-equivalent
--   for the no-template lifecycle); prompt content lives in
--   ``prompt_versions`` per the existing Phase A resolution layer.
--   See ``.agents/shared/planning/agent-prompt-immutability/``
--   decisions.md §8.4 (Block #5) for the binding rulings.
--
-- What lives here:
--
--   * Config row (the meta.json equivalent): one row per custom
--     agent. Carries everything ``agents/<name>/meta.json`` carries
--     today — display name, tools allow/deny/deny_spawn, llm_model,
--     team_members, innate_skills, etc. — EXCEPT we drop everything
--     that is implicitly the template file (``system: false`` is
--     FORCED on this table; customs are non-system by construction).
--   * ``agent_id`` is the wire identity (e.g. ``"myhelper"``) and
--     PRIMARY KEY. The actual db key in ``prompt_versions`` is the
--     SAME string + ``version_tag=''`` (F9 normalization — base tag).
--     The DB-key namespace overlap with predefined ``agents/<id>/``
--     dirs is allowed ONLY when no predefined agent shares the id
--     (a file-defined agent always shadows a same-named custom row;
--     see ``AgentRegistry`` collision rule documentation).
--   * Lifecycle columns: ``created_by`` (actor subject — from the
--     session lane per R-A1; the creating user IS the human approver
--     of the v1 versions they create), ``created_at`` /
--     ``updated_at`` ISO timestamps, ``deleted_at`` for soft-delete.
--   * ``tools_allow``, ``tools_deny``, ``tools_deny_spawn``, ``team_members``,
--     ``innate_skills``, ``capabilities``, ``llm_models`` are JSON
--     lists (``'[]'`` default) — JSONBType on the SQLModel side, same
--     dual-dialect treatment as ``drift_events.files``.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS).
--   - PostgreSQL: brand-new table → created by
--     ``SQLModel.metadata.create_all`` from the
--     :class:`CustomAgentConfig` model. Snapshot-tables /
--     stored_watches precedent: brand-new tables need NO
--     ``_ensure_postgres_columns`` mirror. Explicit
--     ``CustomAgentConfig.metadata.create_all(manager.engine)`` call
--     in the api lifespan (next to the StoredWatch / PromptVersion
--     precedent at api.py) covers engines whose manager-level
--     create_all ran before this model was imported.
--   - Index / unique-constraint names MUST stay byte-identical to
--     the CustomAgentConfig model's ``__table_args__`` declaration
--     (canonical source). The model declares the same names; parity
--     is checked by the migration test runner.
--   - Booleans stored as INTEGER 0/1 (cross-driver; skill/models.py
--     precedent). Timestamps as ISO TEXT (research-findings-db-api §1
--     preamble).
--   - ``agent_id`` is the PRIMARY KEY (TEXT — dual-driver portability
--     precedent: ``StoredWatch``, ``PromptVersion``). The unique
--     constraint + the row's lifecycle columns are the soft-delete
--     fence (no FK to ``prompt_versions`` because a custom agent's
--     history must survive the agent being soft-deleted; a future
--     restore operation re-points ``deleted_at`` and the version
--     rows are already in place).

-- UP

CREATE TABLE IF NOT EXISTS custom_agent_configs (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '🤖',
    color TEXT NOT NULL DEFAULT 'accent-blue',
    system INTEGER NOT NULL DEFAULT 0,
    tools_allow JSON NOT NULL DEFAULT '[]',
    tools_deny JSON NOT NULL DEFAULT '[]',
    tools_deny_spawn JSON NOT NULL DEFAULT '[]',
    llm_model TEXT,
    llm_models JSON NOT NULL DEFAULT '[]',
    team_members JSON NOT NULL DEFAULT '[]',
    innate_skills JSON NOT NULL DEFAULT '[]',
    capabilities JSON NOT NULL DEFAULT '[]',
    skill_injection INTEGER NOT NULL DEFAULT 0,
    snapshot_enabled INTEGER NOT NULL DEFAULT 0,
    skill_search_interval INTEGER NOT NULL DEFAULT 1,
    blueprint_inactive INTEGER NOT NULL DEFAULT 0,
    inject_allowed_models INTEGER NOT NULL DEFAULT 0,
    recursion_limit_multiplier REAL NOT NULL DEFAULT 1.0,
    recursion_limit INTEGER,
    context_injection_heuristic_match_shared_md_files INTEGER NOT NULL DEFAULT 0,
    version_tag TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_auth_method TEXT NOT NULL DEFAULT 'session',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_custom_agent_configs_deleted_at
    ON custom_agent_configs(deleted_at);
CREATE INDEX IF NOT EXISTS idx_custom_agent_configs_created_by
    ON custom_agent_configs(created_by);
CREATE INDEX IF NOT EXISTS idx_custom_agent_configs_created_at
    ON custom_agent_configs(created_at);

-- DOWN

DROP INDEX IF EXISTS idx_custom_agent_configs_created_at;
DROP INDEX IF EXISTS idx_custom_agent_configs_created_by;
DROP INDEX IF EXISTS idx_custom_agent_configs_deleted_at;
DROP TABLE IF EXISTS custom_agent_configs;
