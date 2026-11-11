-- Migration: create the prompt_assent_tokens ledger (the conversational
--           approval lane — "The Emperor's Word / Assent").
-- Created: 2026-11-10
-- Commission: prompt-assent, Phase 1 (token, lane and ledger primitives)
--             @ base 0efdf106f; branch feature/prompt-assent.
--
-- What lives here:
--   * ``prompt_assent_tokens`` — one row per minted assent token for a
--     CORE pending prompt-version row. The durable leg of the lane's
--     anti-confusion factor: the token is shown to the human WITH the
--     canonical diff whose sha256 this row carries; the approve path
--     re-checks that hash INSIDE the guarded transition's transaction
--     (the D-4 hook, O-L — the only render-hash site) and consumes the
--     token co-transactionally with the state change.
--
-- WHY HASH-ONLY (D-1 amendment 1 / S-3):
--   The raw token exists EXACTLY ONCE — in the mint response shown to
--   the Emperor. Durable state carries token_hash = sha256(token) and
--   render_sha256 = sha256(canonical render). There is deliberately NO
--   raw-token column and NO token_display column (the grouped render
--   minus dashes IS the raw secret; it is rendered at mint time only,
--   in the response). render_sha256 is NEVER a copy of
--   prompt_versions.content_hash — that naming is retired for this
--   table: the column-copy would silently no-op J-3 (an effective-side
--   mutation or a rendered-view divergence would be invisible).
--
-- WHY NO HARD FK (O-B):
--   version_id is an INDEXED SOFT REFERENCE to prompt_versions. This
--   mirrors the audit-ledger precedent (prompt_audit_events.version_id:
--   "no FK — audit must survive even when the version row is archived"):
--   token forensics must outlive version archival the same way.
--
-- MINT-CONTEXT COLUMNS (AR-1 — channel AND user binding):
--   mint_source_id / mint_channel_id / mint_is_dm / mint_user_id /
--   minted_external_user_id are derived from the factor-2 window's
--   message row (daemon-owned evidence), NEVER from tool arguments.
--   mint_user_id is the approve-time equality key (K-8: the approving
--   human IS the mint-witnessed human). minted_external_user_id is the
--   destination identity and is NEVER the approving-user key (the guild
--   form is a CHANNEL).
--
-- LIFECYCLE:
--   live → consumed (D-4 co-transactional consume-CAS) /
--          superseded (a re-mint flips the prior live row; a replay of
--          it reads token-superseded — deliberately distinct from
--          token-already-used) /
--          revoked (the attempt-counter lockout at the configured
--          threshold). attempts increments on the REFUSAL path only and
--          commits in its OWN transaction (D-4 blocking rule) so a
--          rollback inside the guarded transition can never reset it.
--
-- THE PARTIAL UNIQUE INDEX (D-1 amendment 2):
--   uq_prompt_assent_tokens_live_version — UNIQUE (version_id) WHERE
--   status = 'live'. Single-active-token is STRUCTURAL against
--   double-mint races: a second live row for one item cannot exist on
--   either driver (the re-mint path flips the prior row to superseded
--   in the SAME transaction as the new INSERT, so the ordering holds).
--   SQLite and PostgreSQL both support partial indexes natively.
--
-- TIMESTAMPS:
--   ISO-8601 UTC TEXT stamps (%Y-%m-%dT%H:%M:%SZ — the
--   upgrade_journal.now_iso lineage). String comparison is the window
--   predicate; the PG session timezone cannot shift a digit.
--   ttl_expires_at = issued_at + NONCE_TTL_S (the SSOT constant
--   imported from daemon/tools/upgrade_journal.py — 60 min).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE ... IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record. The 128th
--     migration file in this directory.
--   - PostgreSQL: the schema comes from
--     ``SQLModel.metadata.create_all`` with the :class:`PromptAssentToken`
--     model (``daemon/repositories/prompt_assent/models.py``) as the
--     canonical source, via the create_all seam in ``daemon/api.py``
--     (the N1 REQUIRED seam — a missed import means no table on PG,
--     which means every token INSERT fails). The model ALSO registers
--     via the factory import (``daemon/repositories/factory.py``).
--     Brand-new table — NO ``_ensure_postgres_columns`` mirror (the
--     ensure list is ONE transaction; any entry's failure aborts boot).
--     The PG-side runner is a NO-OP for this file.
--   - Column names and types stay byte-identical to the model
--     (canonical source for PG). Every string column is TEXT on both
--     drivers — never VARCHAR. booleans are INTEGER 0/1 flags
--     (``mint_is_dm``, the ``is_selected`` convention).
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
--   comment carries a semicolon. Semicolons appear ONLY inside
--   full-line ``--`` comments.
--
-- INDEX NAMES: byte-identical to the model's __table_args__ declarations
--   (the parity contract — tests/migration/test_prompt_assent_tokens_
--   index_parity.py pins both directions).

-- UP

CREATE TABLE IF NOT EXISTS prompt_assent_tokens (
    token_id TEXT NOT NULL PRIMARY KEY,
    version_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    version_tag TEXT NOT NULL DEFAULT '',
    artifact TEXT NOT NULL,
    version INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    render_sha256 TEXT NOT NULL,
    mint_source_id TEXT,
    mint_channel_id TEXT,
    mint_is_dm INTEGER NOT NULL DEFAULT 0,
    mint_user_id TEXT,
    minted_external_user_id TEXT,
    issued_at TEXT NOT NULL,
    ttl_expires_at TEXT NOT NULL,
    superseded_at TEXT,
    consumed_at TEXT,
    consumed_by_message_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'live'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_prompt_assent_tokens_token_hash
    ON prompt_assent_tokens (token_hash);

CREATE UNIQUE INDEX IF NOT EXISTS uq_prompt_assent_tokens_live_version
    ON prompt_assent_tokens (version_id) WHERE status = 'live';

CREATE INDEX IF NOT EXISTS idx_prompt_assent_tokens_version_status
    ON prompt_assent_tokens (version_id, status);

-- DOWN

DROP INDEX IF EXISTS idx_prompt_assent_tokens_version_status;
DROP INDEX IF EXISTS uq_prompt_assent_tokens_live_version;
DROP INDEX IF EXISTS uq_prompt_assent_tokens_token_hash;
DROP TABLE IF EXISTS prompt_assent_tokens;
