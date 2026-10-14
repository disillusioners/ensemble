-- Migration: create prompt_versions + prompt_audit_events
--           (agent-prompt-immutability commission, Phase A — DB foundation)
-- Created: 2026-10-13
-- Phase: agent-prompt-immutability Phase A (T1/T2/T3 deliverables).
--   Two grouped tables in ONE file (the skill-tables precedent;
--   ``20260710_000001_create_skill_tables.sql`` = 6 tables in one file).
--   Followed by Phase B REST endpoints (``prompts:approve`` scope),
--   then Phase C frontend review/approve UI, then Phase D watcher
--   rework. See ``.agents/shared/planning/agent-prompt-immutability/``
--   for the full commission context.
--
-- Design summary (per phase-contract.md A1–A8, decisions.md F1–F9):
--
--   * prompt_versions is the versioned override table — every row is
--     a snapshot of an agent prompt artifact (soul/rule/workflow/
--     tools_note/tools/skill per the F2 conservative-default set)
--     at a particular monotonic integer ``version`` for the key
--     ``(agent_id, version_tag, artifact)``. F1 = overrides-only;
--     the first DB row per key is v1 in state ``draft``.
--
--   * state = formal approval-state machine per A1
--     (draft → pending_approval → approved | rejected; pending_approval
--     → draft recall; draft → archived). ``approved`` and ``rejected``
--     are TERMINAL — re-submission after reject = a NEW row at
--     max(version)+1 for the key. The state transition is enforced
--     by the repository's ``VALID_TRANSITIONS`` set; rejected/draft
--     rows NEVER affect the effective prompt (F6 precedence:
--     selected_version > latest_approved > template_file).
--
--   * F9 — the key is the resolved agent identity
--     ``(agent_id, version_tag)`` so a tagged sibling copy's overrides
--     never leak into the base agent and vice versa. The PromptCache
--     invalidation key composes with this.
--
--   * is_selected is the per-key one-selection-pointer (F5 — Skill
--     ``is_active`` precedent). NULLs are distinct in UNIQUE on both
--     drivers, so we CANNOT enforce one-pointer via a partial unique
--     index; the repo enforces it transactionally via clear-all-then-
--     set-one inside one ``engine.begin()`` (R3).
--
--   * Split identity columns (decisions.md D-IDENTITY-STAMPING):
--     submitter_* is the in-process submitter identity
--     (instance_id + agent_id + version_tag); approver_* is the
--     HTTP approver identity (``AuthContext.subject`` +
--     ``AuthContext.auth_method``). NEVER a single ``submitted_by``
--     blob — both classes need separate provenance columns.
--
--   * prompt_audit_events is the audit/event-row table for the
--     feature (drift_events precedent, ``20261007_000001``). One row
--     per state transition / selection change / template-copy event.
--     event_type = frozenset-validated UPPERCASE enum; payload =
--     JSONBType for free-form context (old/new state, prior
--     selected_version_id, etc.).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS).
--   - PostgreSQL: brand-new tables — created by
--     ``SQLModel.metadata.create_all`` from the PromptVersion +
--     PromptAuditEvent models (daemon/repositories/prompt_version/
--     models.py); the snapshot-tables precedent. Brand-new tables
--     need NO ``_ensure_postgres_columns`` mirror — ``create_all``
--     creates them on fresh AND existing PG databases at next boot.
--     The explicit ``PromptVersion.metadata.create_all`` and
--     ``PromptAuditEvent.metadata.create_all(manager.engine)`` calls
--     in api lifespan (next to the StoredWatch precedent at api.py)
--     cover databases whose manager-level create_all ran before the
--     model module was imported.
--   - Index / unique-constraint names MUST stay byte-identical to
--     the model's ``__table_args__`` declarations (the canonical
--     source). The
--     ``tests/migration/test_prompt_versions_index_parity.py``
--     test suite fails if any of the three names drift.
--   - payload + original_watch_events style JSON via JSONBType on
--     the model — same dual-dialect JSON treatment as
--     ``drift_events.files`` and ``stored_watches.original_watch_events``.
--   - Booleans stored as INTEGER 0/1 (cross-driver, skill/models.py
--     precedent). NULLs distinct in UNIQUE on both drivers — this is
--     exactly why the one-selection-pointer invariant MUST be
--     enforced transactionally in the repo, NOT via a NULL-aware
--     partial unique index (research-findings-db-api.md §1 PITFALLS).

-- UP

CREATE TABLE IF NOT EXISTS prompt_versions (
    version_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    version_tag TEXT NOT NULL DEFAULT '',
    artifact TEXT NOT NULL,
    version INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft',
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_selected INTEGER NOT NULL DEFAULT 0,
    submitter_instance_id TEXT,
    submitter_agent_id TEXT,
    submitter_version_tag TEXT,
    approver_subject TEXT,
    approver_auth_method TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_prompt_versions_agent_tag_artifact_version
    ON prompt_versions(agent_id, version_tag, artifact, version);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_key
    ON prompt_versions(agent_id, version_tag, artifact);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_key_state
    ON prompt_versions(agent_id, version_tag, artifact, state);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_state_updated
    ON prompt_versions(state, updated_at);

CREATE TABLE IF NOT EXISTS prompt_audit_events (
    event_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    version_tag TEXT NOT NULL DEFAULT '',
    artifact TEXT NOT NULL,
    version_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_subject TEXT NOT NULL DEFAULT '',
    actor_auth_method TEXT NOT NULL DEFAULT '',
    payload JSON NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prompt_audit_events_key_created
    ON prompt_audit_events(agent_id, version_tag, artifact, created_at);
CREATE INDEX IF NOT EXISTS idx_prompt_audit_events_version
    ON prompt_audit_events(version_id);
CREATE INDEX IF NOT EXISTS idx_prompt_audit_events_type
    ON prompt_audit_events(event_type, created_at);

-- DOWN

DROP INDEX IF EXISTS idx_prompt_audit_events_type;
DROP INDEX IF EXISTS idx_prompt_audit_events_version;
DROP INDEX IF EXISTS idx_prompt_audit_events_key_created;
DROP TABLE IF EXISTS prompt_audit_events;
DROP INDEX IF EXISTS idx_prompt_versions_state_updated;
DROP INDEX IF EXISTS idx_prompt_versions_key_state;
DROP INDEX IF EXISTS idx_prompt_versions_key;
DROP INDEX IF EXISTS uq_prompt_versions_agent_tag_artifact_version;
DROP TABLE IF EXISTS prompt_versions;