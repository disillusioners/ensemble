-- Migration: create the mcp_gateway audit ledger
--           (the remote-hands center gateway — the `mcp_rpc` privileged
--            tool category)
-- Created: 2026-10-22
-- Phase: Phase B / remote-hands center gateway (SPEC.md v0.1).
--
-- What lives here:
--   * ``mcp_gateway_audit`` — the append-only ledger of every remote
--     command the center forwarded. One row per command: who / machine
--     / tool / args-hash / outcome (§8.4).
--
-- WHY A DEDICATED TABLE (the leader's ruling):
--   The repo's audit convention is DB TABLES (`secrets_audit` behind the
--   `SecretService`, `instance_ops_audit` behind the `instance_ops`
--   category), not JSONL. `secrets_audit` is a domain-specific
--   resolution log; `instance_ops_audit` is the ledger of a privileged
--   category whose acts are LOCAL. The remote-command audit is a third
--   thing: it describes a REMOTE side effect on ANOTHER host.
--
-- WHY THE ARGUMENTS ARE NOT STORED (§8.4):
--   "Args are hashed, not stored verbatim, by default: a typed password
--   should not end up in a log file because it was the argument to a
--   tool." There is deliberately NO arguments column — so there is
--   nothing to fill in by accident. `args_hash` carries the SHA-256 of
--   the canonicalised arguments, in lower-case hex.
--
-- WHY THE AUDIT DOES NOT SHARE A TRANSACTION WITH THE COMMAND:
--   The secrets lineage writes its audit row INSIDE the same transaction
--   as the mutation. That is right for a LOCAL mutation. This audit
--   describes a REMOTE side effect: the command is already done (or
--   already failed) by the time a row could be written, and refusing to
--   report the command's result because the AUDIT write failed would
--   turn an observability problem into an availability problem on a
--   path that drives a user's desktop. The write is therefore
--   BEST-EFFORT: it is logged loudly and counted, and the command
--   proceeds. The divergence from the secrets pattern is deliberate and
--   is documented in `daemon/services/mcps_gateway/audit.py`.
--
-- TIMESTAMPS:
--   `created_at` is an ISO-8601 TEXT stamp — the lineage of
--   `secrets_audit` and `instance_ops_audit`, which deliberately carry
--   ISO-8601 TEXT rather than a naive timestamp column. Nothing here
--   binds a naive timestamp, so the PostgreSQL session timezone cannot
--   shift a stored digit.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     `SQLModel.metadata.create_all` with the :class:`McpGatewayAudit`
--     model (`daemon/repositories/mcps_gateway/models.py`) as the
--     canonical source. The models register into the metadata via the
--     late-import block in `daemon/api.py` BEFORE the create_all call.
--     Brand-new tables need NO `_ensure_postgres_columns` mirror. The
--     PG-side runner is a NO-OP for this file.
--   - Column names and types stay byte-identical to the model
--     (canonical source for PG). Every string column is TEXT on both
--     drivers — never VARCHAR.
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path `SQLModel.metadata.create_all(self._engine)` runs
--   FIRST and the migration runner applies this file IMMEDIATELY AFTER
--   it. By the time this file is applied, create_all has already
--   created the table AND its indexes. A bare CREATE would therefore
--   raise "already exists" and the runner would SWALLOW that error on
--   its idempotent-skip branch — the failure would be silent and this
--   file would degrade into a no-op on every boot.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line `--` comments are stripped by the runner before the
--   statement split, and inline (trailing) comments are left alone —
--   a semicolon inside an inline comment corrupts the statement
--   boundaries. This file uses no semicolon in any comment.

-- UP

CREATE TABLE IF NOT EXISTS mcp_gateway_audit (
    id TEXT NOT NULL PRIMARY KEY,
    created_at TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    instance_id TEXT,
    machine_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'ok',
    session_id TEXT,
    error_code TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_mcp_gateway_audit_agent_created
    ON mcp_gateway_audit (agent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_mcp_gateway_audit_machine_created
    ON mcp_gateway_audit (machine_id, created_at);

CREATE INDEX IF NOT EXISTS idx_mcp_gateway_audit_tool_created
    ON mcp_gateway_audit (tool, created_at);

-- DOWN

DROP INDEX IF EXISTS idx_mcp_gateway_audit_tool_created;
DROP INDEX IF EXISTS idx_mcp_gateway_audit_machine_created;
DROP INDEX IF EXISTS idx_mcp_gateway_audit_agent_created;
DROP TABLE IF EXISTS mcp_gateway_audit;
