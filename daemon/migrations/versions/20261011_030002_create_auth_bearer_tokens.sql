-- Migration: create auth_bearer_tokens (oauth2-auth Phase 2, T2.1 —
--           static bearer-token store, HASH-ONLY storage)
-- Created: 2026-10-11
-- Phase: OAuth2 authentication Phase 2 (phase2-plan.md T2.1, D2.1/D14 —
--           one .sql per table; [TA] §Session Model schema; NFR-13
--           hash-only discipline — the plaintext token is NEVER
--           persisted, only ``SHA-256(token)``).
--
-- One row per minted static bearer token (CLI mint lane, phase 4).
-- Lookups hash the presented token and address the row by PK. Soft
-- revocation via ``revoked_at``; the 5-min sweeper deletes expired rows
-- in batches of 1000 (``expires_at < now()``).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): applied by the .sql migration runner on every
--     startup (auto-apply — no manual-only marker (D17)).
--   - PostgreSQL: created by ``SQLModel.metadata.create_all`` from the
--     ``BearerToken`` model (``daemon/repositories/auth_session/models.py``)
--     at manager init — brand-new table, no ``_ensure_postgres_columns``
--     mirror. Runner is a NO-OP on non-SQLite engines (blueprint
--     invariant).
--   - Timestamps are TEXT ISO-8601 UTC strings (INV-7 house convention,
--     see ``20261011_030000_create_auth_sessions.sql``).
--   - Index name MUST stay byte-identical to the model's
--     ``__table_args__`` declaration — the
--     ``tests/migration/test_auth_tables_g2_2.py`` parity suite fails on
--     drift.
--   - Runner-parser constraint: INLINE (trailing) comments must not
--     contain ``;`` — the runner splits statements on semicolons after
--     stripping FULL-LINE comments only (runner.py apply path).

-- UP

CREATE TABLE IF NOT EXISTS auth_bearer_tokens (
    id_hash      TEXT PRIMARY KEY,   -- SHA-256(token) hex — plaintext NEVER stored (NFR-13)
    subject      TEXT NOT NULL,      -- owner identity (e.g. 'auth:admin' at first mint)
    scopes       TEXT NOT NULL,      -- CSV of granted scopes
    label        TEXT,               -- operator-friendly name
    created_at   TEXT NOT NULL,      -- now_utc_iso()
    expires_at   TEXT NOT NULL,      -- now_utc_iso() + TTL (default 30d)
    last_used_at TEXT,               -- now_utc_iso() + 1m-batched touch
    revoked_at   TEXT                -- soft revocation marker (NULL = active)
);

CREATE INDEX IF NOT EXISTS idx_auth_bearer_tokens_expires_at
    ON auth_bearer_tokens (expires_at);

-- DOWN

DROP INDEX IF EXISTS idx_auth_bearer_tokens_expires_at;
DROP TABLE IF EXISTS auth_bearer_tokens;
