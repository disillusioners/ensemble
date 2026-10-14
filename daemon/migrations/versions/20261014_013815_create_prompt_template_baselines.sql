-- Migration: create prompt_template_baselines
--           (agent-prompt-immutability commission, RESOLUTION-CORE — Ud#5B)
-- Created: 2026-10-14
-- Phase: agent-prompt-immutability Phase A extension (Ud#5B binding blocks 7-9)
--   New single-table migration. ``prompt_template_baselines`` records the
--   shipping-state sha256 per ``(agent_id, version_tag, CORE artifact)`` so
--   the predefined-agent template-drift detection can compare live file
--   hashes against the committed baseline and run the R-B2 CORE-only
--   auto-reset protocol when a shipped template changes.
--
--   Schema fields (mirrors the SQLModel declaration in
--   ``daemon/repositories/prompt_version/models.py``):
--
--     * agent_id TEXT NOT NULL  — the resolved agent identity (per F9 /
--       D15/D16: base ``"developer"`` vs tagged ``"developer[v2]"`` both
--       resolve to ``agent_id="developer"``; the ``version_tag`` column
--       disambiguates the copy).
--     * version_tag TEXT NOT NULL DEFAULT ''  — same normalization as
--       ``prompt_versions.version_tag`` (NULL → '' at write time so the
--       UNIQUE constraint is unambiguous across drivers; NULL-distinct in
--       UNIQUE pitfall from ``research-findings-db-api.md`` §1).
--     * artifact TEXT NOT NULL  — the artifact key from
--       ``PROMPT_ARTIFACTS`` (``soul | rule | workflow | tools_note |
--       tools | skill``; the CORE set per Ud#3). MEMORY-class artifacts
--       never land here (the auto-reset is CORE-only per R-B2).
--     * template_sha256 TEXT NOT NULL  — lowercase sha256 hex of the
--       shipped file body at ``recorded_at`` time. ``None`` is NOT valid;
--       absent entries signal "never recorded" (the baseline service
--       lazy-seeds on first ``record_initial_baselines`` call).
--     * recorded_at TEXT NOT NULL  — ISO-8601 timestamp of the seed /
--       re-point event.
--     * UNIQUE(agent_id, version_tag, artifact)  — one baseline per key
--       (the auto-reset re-points in place; recorded_at updates).
--
--   One-file-per-table convention per corpus. The runner is SQLite-only by
--   design (runner.py:715-720: post-2026-10 SQLite is one path, PG goes
--   through SQLModel.metadata.create_all + _ensure_postgres_columns).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup. CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS are
--     safe against re-apply (idempotent at the SQL level).
--   - PostgreSQL: brand-new table — created by
--     ``SQLModel.metadata.create_all`` from the :class:`PromptTemplateBaseline`
--     model (``daemon/repositories/prompt_version/models.py``). The
--     explicit ``PromptTemplateBaseline.metadata.create_all(manager.engine)``
--     call in api.py lifespan (next to the StoredWatch / PromptVersion
--     precedent at api.py:714, :727, :743-744) covers engines whose
--     manager-level create_all ran before the model module was imported.
--     No existing DB column additions are needed (one new table = no
--     _ensure_postgres_columns mirror; snapshot / stored_watches
--     precedent).
--   - Index / unique-constraint names MUST stay byte-identical to the
--     model's ``__table_args__`` declarations (the canonical source).
--     The ``tests/migration/test_prompt_versions_index_parity.py``
--     pattern extends to ``prompt_template_baselines`` (parity test
--     is added in the same Ud#5B commit).
--   - JSON-shaped payloads: this table has NO JSON column — the only
--     audit-payload JSON lands in ``prompt_audit_events.payload``
--     (JSONBType). No JSON-shaped decisions here.
--   - Booleans stored as INTEGER 0/1 (cross-driver; skill/models.py
--     precedent). This table has no boolean columns; ``is_selected``
--     lives on ``prompt_versions`` (Ud#3 unchanged).
--   - Reversibility: -- DOWN reverse-drop mirrors -- UP forward-build.
--     The runner's -- DOWN splitter strips full-line ``--`` comments
--     before splitting on ``;`` (see ``apply_migration`` in
--     ``daemon/migrations/runner.py:543-557``); trailing in-line
--     semicolon-comments are left alone to keep the splitter honest.
--
--   This table does NOT carry a state column — baselines are
--   monotonically-replaced (the re-point updates the same row's hash +
--   timestamp). No selection pointer (baselines are inherently
--   per-key one-slot). Memory-class IDs (``memory`` / ``user`` / ``growth``
--   / ``memories/<slug>``) are NEVER inserted here; the service guards
--   the artifact against the ``PROMPT_ARTIFACTS`` set and the class.

-- UP

CREATE TABLE IF NOT EXISTS prompt_template_baselines (
    baseline_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    version_tag TEXT NOT NULL DEFAULT '',
    artifact TEXT NOT NULL,
    template_sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_prompt_template_baselines_agent_tag_artifact
    ON prompt_template_baselines(agent_id, version_tag, artifact);
CREATE INDEX IF NOT EXISTS idx_prompt_template_baselines_artifact
    ON prompt_template_baselines(artifact);
CREATE INDEX IF NOT EXISTS idx_prompt_template_baselines_recorded_at
    ON prompt_template_baselines(recorded_at);

-- DOWN

DROP INDEX IF EXISTS idx_prompt_template_baselines_recorded_at;
DROP INDEX IF EXISTS idx_prompt_template_baselines_artifact;
DROP INDEX IF EXISTS uq_prompt_template_baselines_agent_tag_artifact;
DROP TABLE IF EXISTS prompt_template_baselines;
