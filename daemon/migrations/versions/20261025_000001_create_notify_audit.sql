-- Migration: create the notify_audit ledger
--           (the `notify-user` privileged tool category — the Ari voice)
-- Created: 2026-10-25
-- Phase: Phase 3 of the ari-notify-user commission (base 7af1d2632d).
--
-- What lives here:
--   * ``notify_audit`` — one row per notify_user attempt: who / which
--     resolved destination (hashed) / outcome / body hash + length.
--     Refusals ARE ledgered (the refusal is the security signal);
--     ONLY rows standing as `accepted` consume the rate budget.
--
-- WHY A DEDICATED TABLE (the frozen ruling D8):
--   No table to reuse. ``instance_ops_audit`` is the ledger of LOCAL
--   destructive acts; ``secrets_audit`` is a domain-specific resolution
--   log; ``mcp_gateway_audit`` is the REMOTE-command ledger. The notify
--   audit is a fourth thing: it is BOTH the audit trail AND the
--   rate-limit budget source (the rolling-hour M cap is COUNTED FROM
--   THIS TABLE), which is why the write is FAIL-CLOSED on the send
--   path (N2 — no ledger row, no send) and not best-effort like
--   mcp_gateway_audit.
--
-- WHY THE BODY IS NOT STORED (D8):
--   The message body is stored as ``body_sha256`` + ``body_length``,
--   never cleartext — rate counting + tamper evidence without a second
--   copy of message content. There is deliberately NO body column to
--   fill in by accident (the mcp_gateway_audit args-hash reasoning,
--   applied to a message body).
--
-- WHY source_id AND target_hash ARE NULLABLE (W3, pinned choice):
--   PRE-resolution refusals (``notify_size_exceeded``,
--   ``notify_no_bound_chat_source``) have no resolved source to name.
--   Fold-2 N3 (RULED) requires every refusal class to be ledgered, so
--   the nullable variant of W3 is pinned. The budget COUNT excludes
--   NULL target_hash rows explicitly, so nullable columns can never
--   pool into a cap.
--
-- TIMESTAMPS:
--   ``created_at`` is an ISO-8601 TEXT stamp — the lineage of
--   ``secrets_audit`` / ``instance_ops_audit`` / ``mcp_gateway_audit``.
--   String comparison is the window predicate (ISO-8601 UTC sorts
--   lexicographically); the PG session timezone cannot shift a digit.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     ``SQLModel.metadata.create_all`` with the :class:`NotifyAudit`
--     model (``daemon/repositories/notify_audit/models.py``) as the
--     canonical source. The model registers into the metadata via the
--     factory import (``daemon/repositories/factory.py``) and the
--     late-import block in ``daemon/api.py`` BEFORE the create_all
--     call (the N1 required seam — a missed import means no table on
--     PG, which means the budget INSERT fails, which is fail-closed
--     but dead). Brand-new table — NO ``_ensure_postgres_columns``
--     mirror. The PG-side runner is a NO-OP for this file.
--   - Column names and types stay byte-identical to the model
--     (canonical source for PG). Every string column is TEXT on both
--     drivers — never VARCHAR. ``body_length`` is INTEGER on both.
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path ``SQLModel.metadata.create_all(self._engine)``
--   runs FIRST and the migration runner applies this file IMMEDIATELY
--   AFTER it. By the time this file is applied, create_all has already
--   created the table AND its indexes. A bare CREATE would therefore
--   raise "already exists" and the runner would SWALLOW that error on
--   its idempotent-skip branch — the failure would be silent and this
--   file would degrade into a no-op on every boot.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner before the
--   statement split, and inline (trailing) comments are left alone —
--   a semicolon inside an inline comment corrupts the statement
--   boundaries. THE INVARIANT THIS FILE UPHOLDS: no INLINE (trailing)
--   comment carries a semicolon. Semicolons appear ONLY inside full-line
--   ``--`` comments, which the runner strips before splitting the
--   remainder on ``;`` (``daemon/migrations/runner.py``:670-673).

-- UP

CREATE TABLE IF NOT EXISTS notify_audit (
    id TEXT NOT NULL PRIMARY KEY,
    created_at TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'notify_user',
    source_id TEXT,
    target_hash TEXT,
    outcome TEXT NOT NULL DEFAULT 'accepted',
    refusal_reason TEXT,
    error_code TEXT,
    body_length INTEGER NOT NULL DEFAULT 0,
    body_sha256 TEXT NOT NULL,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_notify_audit_agent_created
    ON notify_audit (agent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_notify_audit_instance_created
    ON notify_audit (instance_id, created_at);

CREATE INDEX IF NOT EXISTS idx_notify_audit_target_created
    ON notify_audit (target_hash, created_at);

-- DOWN

DROP INDEX IF EXISTS idx_notify_audit_target_created;
DROP INDEX IF EXISTS idx_notify_audit_instance_created;
DROP INDEX IF EXISTS idx_notify_audit_agent_created;
DROP TABLE IF EXISTS notify_audit;
