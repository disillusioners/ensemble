-- Migration: add instance_metadata JSONB column to mcp_servers (P3-WP6)
--
-- Created: 2026-09-26
-- Author: dev-coder-p3-kms (designer-agent P3 — bootstrap+kms-lite)
-- Description:
--   Adds ``mcp_servers.instance_metadata`` (JSONB / TEXT depending on
--   driver) for the KMS-Lite handle→server binding substrate. Per
--   P3-WP6 ratify-on-implement decision: prefer a JSONB column on
--   ``mcp_servers`` over a new ``kms_handle_bindings`` table (avoids
--   a new table for day-1; architecturally equivalent for handle-bind
--   semantics).
--
--   Day-1 use: ``instance_metadata.bound_handles`` is a list of
--   ``{"handle": "KMS_HANDLE_<uuid>", "env_key": "OPENDESIGN_API_KEY",
--   "fingerprint": "<sha256[:16]>"}`` entries. Reads (config-load /
--   GET /mcp_servers) consult this for presentation-side fingerprint
--   display; writes (kms_attach) append entries. Plaintext NEVER rides
--   this column — only handle + env-key + fingerprint.
--
-- DUAL-DRIVER NOTES:
--   For SQLite (this file): ALTER TABLE ADD COLUMN.
--   For PostgreSQL: the equivalent statement lives in
--     ``daemon/manager.py::_ensure_postgres_columns`` (the migration
--     runner is SQLite-only by design — runner.py:719-727).

-- UP

ALTER TABLE mcp_servers ADD COLUMN instance_metadata TEXT DEFAULT '{}';

-- DOWN

-- SQLite ALTER TABLE DROP COLUMN support arrived in 3.35; safe no-op
-- for older engines because the column add is also a no-op there.
ALTER TABLE mcp_servers DROP COLUMN instance_metadata;