-- Migration: create auth_bootstrap_consumption (oauth2-auth Phase 2,
--           AUTHORIZED re-scope from phase4 T4.1 — one-shot bootstrap
--           mint sentinel rows)
-- Created: 2026-10-11
-- Phase: OAuth2 authentication Phase 2 (phase2-plan.md; schema from
--           phase4-plan.md T4.1 AMEND v1.3 / F10 — SENTINEL ROW design).
--           Migration + SQLModel model land in P2 (per-table migration
--           convention D14); the repository + CLI consumption land in
--           P4 (T4.1 proper). The table is INERT in P2 (D17 posture —
--           created on both backends, never read or written until the
--           P4 mint CLI arrives).
--
-- One row per CONSUMED bootstrap env value, keyed by ``sha256(env_value)``
-- (hash-only discipline — the sensitive value is never stored plaintext,
-- same as the bearer table, NFR-13). The P4 CLI refuses a second
-- ``issue-token --bootstrap`` with the same env value when a sentinel row
-- exists (one-shot semantics, C2/F10).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): applied by the .sql migration runner on every
--     startup (auto-apply — no manual-only marker (D17)).
--   - PostgreSQL: created by ``SQLModel.metadata.create_all`` from the
--     ``AuthBootstrapConsumption`` model
--     (``daemon/repositories/auth_session/models.py``) at manager init —
--     brand-new table, no ``_ensure_postgres_columns`` mirror. Runner is
--     a NO-OP on non-SQLite engines (blueprint invariant).
--   - Timestamps are TEXT ISO-8601 UTC strings (INV-7 house convention,
--     see ``20261011_030000_create_auth_sessions.sql``).

-- UP

CREATE TABLE IF NOT EXISTS auth_bootstrap_consumption (
    id_hash         TEXT PRIMARY KEY,  -- SHA-256(env_value) hex — one row per consumed value
    consumed_at     TEXT NOT NULL,     -- now_utc_iso() at first mint
    actor_label     TEXT NOT NULL,     -- the label of the first minted token
    consumed_by_pid INTEGER            -- process that performed the consumption (operator debugging)
);

-- DOWN

DROP TABLE IF EXISTS auth_bootstrap_consumption;
