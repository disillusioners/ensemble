-- Migration: create mcp_kb_keys (oauth2-auth Phase 3, T3.11 —
--           module-owned MCP-KB key store)
-- Created: 2026-10-11
-- Phase: OAuth2 authentication Phase 3 (phase3-plan.md T3.11, AMEND
--           v1.4 / module-lanes; decisions.md D20a/D20b — module-OWNED
--           storage, own per-table migration, D14 one .sql per table).
--
-- One row per MCP-KB module key (kbk_… — hash-only storage). Minted by
-- the OAuth2-gated /api/mcp/kb/keys CRUD (T3.12); verified by the
-- module-lane mount guard (daemon/mcp/kb_keys.py KbKeyGuardApp).
-- INDEPENDENT of OAuth2: exists and is used in BOTH auth states
-- (FR-42); lane isolation D20a — no auth table reads this and no auth
-- lane reads this.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): applied by the .sql migration runner on every
--     startup (auto-apply — no manual-only marker; house D17 posture).
--   - PostgreSQL: created by SQLModel.metadata.create_all from the
--     McpKbKey model (daemon/repositories/mcp_kb_keys/models.py) at
--     manager init (registered beside the auth models); brand-new
--     tables need no _ensure_postgres_columns mirror. The runner is a
--     NO-OP on non-SQLite engines (blueprint invariant).
--   - Timestamps are TEXT ISO-8601 UTC strings (now_utc_iso() format,
--     INV-7 house convention — zero TIMESTAMP columns).
--   - UNIQUE index on id_hash (the lookup column); model-side declared
--     unique+indexed — keep the two in sync if either changes.
--   - Runner-parser constraint: INLINE (trailing) comments must not
--     contain ";" — statements split on semicolons after full-line
--     comment stripping (runner.py apply path).

-- UP

CREATE TABLE IF NOT EXISTS mcp_kb_keys (
    id           TEXT PRIMARY KEY,   -- URL-safe random id (CRUD handle)
    id_hash      TEXT NOT NULL UNIQUE, -- SHA-256 of the kbk_ token hex — plaintext NEVER stored
    label        TEXT,               -- operator-friendly name
    created_at   TEXT NOT NULL,      -- now_utc_iso()
    last_used_at TEXT,               -- now_utc_iso() at last mount verification
    revoked_at   TEXT                -- soft-revocation marker (NULL = active)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_kb_keys_id_hash
    ON mcp_kb_keys (id_hash);

-- DOWN

DROP INDEX IF EXISTS idx_mcp_kb_keys_id_hash;
DROP TABLE IF EXISTS mcp_kb_keys;
