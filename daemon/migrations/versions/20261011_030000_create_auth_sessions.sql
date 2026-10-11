-- Migration: create auth_sessions (oauth2-auth Phase 2, T2.1 — session
--           store row per logged-in browser session)
-- Created: 2026-10-11
-- Phase: OAuth2 authentication Phase 2 (phase2-plan.md T2.1, D2.1/D14 —
--           one .sql per table; [TA] §Session Model schema; requirements
--           §4 NFR-8 / decisions.md D17 inert-tables posture).
--
-- One row per authenticated browser session (cookie ``ensemble_sid``).
-- Created by phase-3 login/callback; swept by the phase-2 background
-- sweeper (5-min loop, batches of 1000, ``expires_at < now()``).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): applied by the .sql migration runner on every
--     startup (auto-apply — no manual-only marker (D17): auth
--     tables are created on BOTH backends, inert when auth is unused).
--   - PostgreSQL: the table is created by ``SQLModel.metadata.create_all``
--     from the ``AuthSession`` model
--     (``daemon/repositories/auth_session/models.py``) at manager init —
--     brand-new tables need no ``_ensure_postgres_columns`` mirror
--     (snapshot/maintenance_runs precedent, manager.py registration
--     block). The runner is deliberately a NO-OP on non-SQLite engines
--     (daemon/migrations/runner.py:721-726 — blueprint invariant).
--   - Timestamps are TEXT ISO-8601 UTC strings (``now_utc_iso()`` format,
--     microsecond precision + ``+00:00`` offset). INV-7 house convention:
--     zero ``TIMESTAMP`` columns — lexicographic ordering is
--     chronological for the fixed format, so the ``expires_at`` sweep
--     comparison and the index below behave identically on both drivers.
--   - Index name MUST stay byte-identical to the model's
--     ``__table_args__`` declaration (canonical source) — the
--     ``tests/migration/test_auth_tables_g2_2.py`` parity suite fails on
--     drift.
--   - Runner-parser constraint: INLINE (trailing) comments must not
--     contain ``;`` — the runner splits statements on semicolons after
--     stripping FULL-LINE comments only (runner.py apply path).

-- UP

CREATE TABLE IF NOT EXISTS auth_sessions (
    id           TEXT PRIMARY KEY,   -- URL-safe random 32 bytes (ensemble_sid value)
    subject      TEXT NOT NULL,      -- OIDC "sub" claim
    email        TEXT,               -- OIDC "email" claim (optional)
    scopes       TEXT NOT NULL,      -- CSV of granted scopes
    auth_method  TEXT NOT NULL,      -- 'session' | 'bearer'
    user_agent   TEXT,
    ip_subnet    TEXT,               -- /24 IPv4 or /64 IPv6 (when fingerprint_by_ip_subnet)
    created_at   TEXT NOT NULL,      -- now_utc_iso()
    last_used_at TEXT,               -- now_utc_iso() + 1m-batched touch
    expires_at   TEXT NOT NULL       -- now_utc_iso() + session_ttl_seconds
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at
    ON auth_sessions (expires_at);

-- DOWN

DROP INDEX IF EXISTS idx_auth_sessions_expires_at;
DROP TABLE IF EXISTS auth_sessions;
