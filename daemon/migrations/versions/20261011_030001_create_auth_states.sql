-- Migration: create auth_states (oauth2-auth Phase 2, T2.1 — OIDC
--           authorization-request state rows, exactly-once via single CAS)
-- Created: 2026-10-11
-- Phase: OAuth2 authentication Phase 2 (phase2-plan.md T2.1, D2.1/D14 —
--           one .sql per table; [TA] §Session Model schema; decisions.md
--           D2.3 single CAS ``pending → consumed``; D17 inert tables).
--
-- One row per issued OAuth2 ``state`` parameter. The callback consumes a
-- state EXACTLY ONCE via a single portable conditional UPDATE
-- (``SET consumed_at = :now WHERE id = :id AND consumed_at IS NULL``) —
-- the loser maps to ``400 STATE_REPLAYED`` (D2.3). A dedicated 60 s
-- sweeper loop deletes consumed + expired rows (states are short-lived).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): applied by the .sql migration runner on every
--     startup (auto-apply — no manual-only marker (D17)).
--   - PostgreSQL: created by ``SQLModel.metadata.create_all`` from the
--     ``AuthState`` model (``daemon/repositories/auth_session/models.py``)
--     at manager init — brand-new table, no ``_ensure_postgres_columns``
--     mirror. Runner is a NO-OP on non-SQLite engines (blueprint
--     invariant).
--   - Timestamps are TEXT ISO-8601 UTC strings (INV-7 house convention,
--     see ``20261011_030000_create_auth_sessions.sql``).
--   - No secondary index: the table is PK-addressed (the CAS is a single
--     PK lookup) and the 60 s sweep keeps row counts tiny.

-- UP

CREATE TABLE IF NOT EXISTS auth_states (
    id               TEXT PRIMARY KEY,  -- the 'state' param (URL-safe random 32 bytes)
    code_verifier    TEXT NOT NULL,     -- PKCE verifier (server-held, never leaves)
    code_challenge   TEXT NOT NULL,     -- PKCE S256 challenge sent to the IdP
    challenge_method TEXT NOT NULL,     -- 'S256' (never 'plain')
    redirect_to      TEXT,              -- SPA path to return to post-login
    created_at       TEXT NOT NULL,     -- now_utc_iso()
    expires_at       TEXT NOT NULL,     -- short-lived (minutes)
    consumed_at      TEXT               -- single-CAS marker (NULL = pending)
);

-- DOWN

DROP TABLE IF EXISTS auth_states;
