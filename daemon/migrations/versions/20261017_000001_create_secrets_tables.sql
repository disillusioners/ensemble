-- Migration: create secrets + secrets_audit
--           (agent-secrets commission, Phase 1 — store + tool surface + category)
-- Created: 2026-10-17
-- Phase: agent-secrets P1 (plan f11965039700b5bb53b22c4c803281f56c744cef).
--   Two grouped tables in ONE file (the prompt_versions precedent —
--   ``20261013_214000_create_prompt_versions.sql`` = 2 tables in one
--   file). See ``.agents/shared/planning/agent-secrets/`` for the full
--   plan + decisions.
--
-- Design summary (per plan §4 + §10 + §13):
--
--   * ``secrets`` is the durable Fernet-encrypted secret store backing
--     the new ``secrets`` privileged tool category (#6 in
--     ``PRIVILEGED_TOOL_CATEGORIES`` — sextet → septet per
--     Q1/Q2/Q3 decisions 2026-10-17 (``cu`` joined
--     2026-10-16 making the count 6 before this entry,
--     ``secrets`` is the 7th). The row IS the secret:
--     - ``id`` is the UUID4 immutable handle that the
--       ``__ENS_SECRET__<id>__`` marker text embeds (Q3 — marker
--       format FROZEN, see ``daemon/services/secret_service.py``).
--     - ``name`` is the human-readable label (UNIQUE — agent authors
--       the name when calling ``secrets.store`` / ``generate_*``).
--     - ``kind`` is a forward-compatible enum tag
--       (``'ssh_keypair' | 'api_token' | 'generic'``); the v1 tool
--       surface mints the first two, ``store`` accepts any
--       pre-declared kind. New kinds can be added later by a future
--       migration without touching this table.
--     - ``public_material`` carries the public-side of the secret
--       when applicable (the SSH public key for keypairs); null
--       for token / generic rows. Pubkey-only rule: agents
--       receive public material only via ``read_public``; private
--       material NEVER crosses the tool-result boundary.
--     - ``wrapped_secret`` is the Fernet ciphertext (BYTEA on PG,
--       BLOB on SQLite — see dual-driver notes below). NEVER
--       returned by any read path; the only consumer that
--       decrypts is the in-process ``SecretService`` (P2
--       marker-resolver seam).
--     - ``created_by`` is the agent id (Cardinal #2 source
--       discipline — provenance is the calling agent, not the
--       HTTP subject). ``created_at`` is the row birth.
--     - ``retired_at`` is the soft-retire tombstone. Plan §5.2's
--       ``secrets.retire`` tool sets it; retired rows stay
--       queryable for audit but the partial index
--       ``(kind) WHERE retired_at IS NULL`` lets the active
--       registry queries hit only live rows.
--
--   * ``secrets_audit`` is the append-only event table backing the
--     three P1 actions (``create`` / ``read_public`` / ``retire``).
--     Two more actions live in the ``action`` enum for P2
--     ``use_marker`` / ``read_value`` (P2 only — no v1 emitter).
--     The audit row is stamped INSIDE the same
--     ``engine.begin()`` transaction as the mutation per the
--     prompt_versions Phase-A precedent (audit-event stamping
--     contract, A3).
--     - ``action`` is a forward-compatible enum tag. ``read_value``
--       is RESERVED-FOR-FUTURE per plan §4.2 — no v1 emitter
--       (no value-read tool exists in v1; v1 read = public
--       material only).
--     - ``actor`` is the agent id (Cardinal #2).
--     - ``secret_id`` is nullable so create events (no row yet
--       at audit-stamp time? actually we stamp AFTER insert) can
--       be linked; the FK is informational only — we never
--       CASCADE-delete a secret because retention is via
--       ``retired_at``, not DELETE.
--
-- Encryption coupling (plan §7.1 + §10, user Q1 ruling):
--   The Fernet key is ``SYSTEM_ENCRYPTION_KEY`` — the same env
--   the existing ``daemon/sources/credentials.py`` +
--   ``daemon/services/kms_lite.py`` paths consume. Rotation
--   coupling: a future ``SYSTEM_ENCRYPTION_KEY`` rotation MUST
--   re-encrypt ``secrets.wrapped_secret`` AND
--   ``db_connections`` Fernet credentials, or both registries
--   break. The coupling is documented in code at the cipher
--   seam (``daemon/services/secret_service.py:SecretCipher``).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on
--     every startup (idempotent CREATE TABLE IF NOT EXISTS).
--     ``wrapped_secret`` is BLOB; ``id`` is TEXT (UUID4 string)
--     for SQLite portability. Booleans / timestamps use the
--     cross-driver conventions of the other migrations (no
--     booleans in this table; timestamps are PG ``TIMESTAMPTZ``
--     here, ``TEXT`` ISO on the SQLModel side — see
--     dual-driver notes in the model).
--   - PostgreSQL: brand-new tables — created by
--     ``SQLModel.metadata.create_all`` from the
--     :class:`Secret` + :class:`SecretAuditEvent` models
--     (``daemon/repositories/secrets/models.py``). The
--     create_all on the SQLModel side uses ``UUID`` PK +
--     ``LargeBinary`` for wrapped_secret (BYTEA on PG); the
--     model declarations are the canonical source for the PG
--     schema. Brand-new tables need NO
--     ``_ensure_postgres_columns`` mirror — create_all creates
--     them on fresh AND existing PG databases at next boot.
--   - Index / unique-constraint names MUST stay byte-identical
--     to the SQLModel model's ``__table_args__`` declarations
--     (the canonical source). The
--     ``tests/migration/test_prompt_versions_index_parity.py``
--     test pattern will extend to this table when a parity
--     test is added.
--
-- KNOWN PRE-EXISTING DEFECT (NOT in scope for this migration):
--   migration ``20260714_000001`` breaks fresh-SQLite boots;
--   PG is the target for integration tests. Confirmed by the
--   project KB; not fixed in this commission.

-- UP

CREATE TABLE IF NOT EXISTS secrets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    public_material TEXT,
    wrapped_secret BLOB NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    retired_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_secrets_kind_active
    ON secrets(kind)
    WHERE retired_at IS NULL;

CREATE TABLE IF NOT EXISTS secrets_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    secret_id TEXT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_secrets_audit_secret_id_at
    ON secrets_audit(secret_id, at);

-- DOWN

DROP INDEX IF EXISTS idx_secrets_audit_secret_id_at;
DROP TABLE IF EXISTS secrets_audit;
DROP INDEX IF EXISTS idx_secrets_kind_active;
DROP TABLE IF EXISTS secrets;
