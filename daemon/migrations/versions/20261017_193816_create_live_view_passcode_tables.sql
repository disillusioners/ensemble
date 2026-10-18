-- Migration: create live_view_passcodes + live_view_passcode_audit
--           (live-view-passcode commission, Phase 1 — storage + service layer)
-- Created: 2026-10-17
-- Phase: live-view-passcode P1 (plan f11965039700b5bb53b22c4c803281f56c744cef
--   / .agents/shared/planning/live-view-passcode/phase1-plan.md).
--   Two grouped tables in ONE file (the secrets-tables precedent —
--   ``20261017_000001_create_secrets_tables.sql`` = 2 tables in one file).
--   See ``.agents/shared/planning/live-view-passcode/`` for the full
--   plan + ADR of record. P1 ships only the durable substrate;
--   routers/tools/grants/cookies arrive in P2.
--
-- Design summary (per phase1-plan.md §3, architecture-recommendation.md
-- D1/D4/D5/D9, plan-overview.md R1–R5):
--
--   * ``live_view_passcodes`` is the durable Fernet-encrypted passcode
--     store backing the P2 ``/views/public/{root}/{rel}`` family. ONE
--     row per ``(root_name, rel_path)`` (the ADR-D2 row-gating posture;
--     lockout of one file never takes out a root).
--
--     PRIVILEGED-CATEGORY-COUNT LOCKSTEP NOTE
--     ----------------------------------------
--     This migration does NOT add a new privileged tool category;
--     ``view_link`` lives in the existing ``view-views`` category. No
--     sextet/septet lockstep applies (the secrets P1 line at
--     20261017_000001 carries that one). Documented here so a future
--     audit reads the lineage in one place.
--
--     - ``id`` is the UUID4 immutable handle (TEXT — cross-driver
--       portability, the ``Secret`` / ``PromptVersion`` /
--       ``CustomAgentConfig`` precedent).
--     - ``root_name`` + ``rel_path`` form the UNIQUE pair; the
--       ``uq_live_view_passcodes_root_rel`` constraint is the
--       first-store-wins fence the P1 mint path follows (a re-mint
--       on the same key returns the existing row — id / generation /
--       locked state preserved; ADR D5 sharpening: idempotent mint
--       does NOT clear an active lock).
--     - ``wrapped_passcode`` is BLOB/BYTEA Fernet ciphertext (NEVER a
--       plaintext-at-rest column). The plaintext re-display on every
--       ``view_link`` mint is a forced deviation (plan-overview §6
--       D6 / architecture-recommendation.md V1 — same ratified class
--       as agent-secrets plaintext-at-mint).
--     - ``generation`` is an int bumped ONLY by ``refresh()``. The
--       generation bump silently revokes ALL outstanding grant
--       cookies (no session table — ADR D6 stateless cookie + D3
--       revocation-via-generation).
--     - ``failed_attempts`` + ``locked`` are the durable 10-strike
--       lockout state (plan D4 / architecture-recommendation.md D5).
--       Lock persists until ``refresh``; no auto-unlock (the spec
--       default, not a deviation — architecture-recommendation.md
--       D5:66).
--     - ``created_at`` + ``last_attempt_at`` are ISO-8601 TEXT
--       (cross-driver convention used by the other additive tables).
--     - Partial index ``idx_live_view_passcodes_locked`` keyed on
--       ``WHERE locked`` — the durable lookup path the P2 verify
--       short-circuit (locked → 200 notice) consults. Mirrors the
--       secrets ``idx_secrets_kind_active`` precedent at
--       ``20261017_000001_create_secrets_tables.sql:152-154``.
--
--   * ``live_view_passcode_audit`` is the append-only event table
--     backing the closed action set
--     ``{mint, regenerate, grant, deny, lockout}`` (ADR D9:115).
--     ``action`` is TEXT, NOT a DB enum — additive emitters never
--     require a migration.
--
--     - ``actor`` is NULLable for public actions (the verify POST on
--       the unauthenticated lane has no agent provenance). The
--       privileged ``mint`` / ``regenerate`` paths carry the calling
--       agent id (Cardinal #2 source discipline, the
--       ``secrets_audit`` precedent).
--     - ``ip_hmac`` is a 16-hex-char keyed HMAC of the request IP
--       (ADR D9:116 — ``HMAC-SHA256(ip, key)[:16 hex]`` where ``key``
--       is ``SHA256(SYSTEM_ENCRYPTION_KEY || "live-view-audit-ip-v1")``,
--       domain-separated). Rotation unlinks historical rows by
--       design — raw IPs are NEVER stored.
--     - Per-request cookie failures (``grant_expired``,
--       ``cookie_invalid``) are log-only — write-amplification is
--       rejected (ADR D9:115). The closed action set is the
--       minimal durable layer.
--     - Index ``idx_live_view_passcode_audit_root_at`` covers the
--       ``audit rows for a (root, time-ordered)`` query path the
--       future operator UI will hit.
--
-- Encryption coupling (plan §3 D5 + §7.1, user Q1 ruling 2026-10-17,
-- architecture-recommendation.md D4:59 + D9:117):
--   The Fernet key is ``SYSTEM_ENCRYPTION_KEY`` — the same env the
--   existing ``daemon/sources/credentials.py`` +
--   ``daemon/services/kms_lite.py`` paths consume. Rotation coupling:
--   a future ``SYSTEM_ENCRYPTION_KEY`` rotation MUST re-encrypt
--   ``live_view_passcodes.wrapped_passcode`` AND
--   ``secrets.wrapped_secret`` AND the ``db_connections`` Fernet
--   credentials AND invalidate ALL outstanding grant cookies, or
--   every one of those registries breaks. The cipher seam at
--   ``daemon/services/view_passcode_service.py:ViewPasscodeCipher``
--   documents the rotation walk alongside the secrets precedent.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on
--     every startup (idempotent CREATE TABLE IF NOT EXISTS /
--     CREATE INDEX IF NOT EXISTS).
--     ``wrapped_passcode`` is BLOB; ``id`` is TEXT (UUID4 string)
--     for SQLite portability. Booleans / timestamps use the
--     cross-driver conventions of the other additive migrations
--     (timestamps are ISO-8601 TEXT — same convention as
--     ``Secret.created_at``).
--   - PostgreSQL: brand-new tables — created by
--     ``SQLModel.metadata.create_all`` from the
--     :class:`LiveViewPasscode` + :class:`LiveViewPasscodeAudit`
--     models (``daemon/repositories/view_passcodes/models.py``).
--     ``create_all`` uses ``LargeBinary`` for ``wrapped_passcode``
--     (BYTEA on PG); the model declarations are the canonical source
--     for the PG schema. Brand-new tables need NO
--     ``_ensure_postgres_columns`` mirror.
--   - Index / unique-constraint names MUST stay byte-identical to
--     the SQLModel model's ``__table_args__`` declarations (the
--     canonical source). The
--     ``tests/migration/test_view_passcode_index_parity.py`` test
--     pattern (modeled on the secrets parity test at
--     ``tests/migration/test_secrets_index_parity.py``) pins the
--     drift.
--   - ``locked`` is INTEGER 0/1 on both drivers (the
--     ``prompt_versions.is_selected`` precedent at
--     ``20261013_214000_create_prompt_versions.sql:94`` —
--     cross-driver, native-enum-free).
--   - ``ip_hmac`` is TEXT (16 hex chars on both drivers — HMAC-SHA256
--     truncated to 16 hex = 64 bits; ADR D9:116).
--
-- KNOWN PRE-EXISTING DEFECT (NOT in scope for this migration):
--   migration ``20260714_000001`` breaks fresh-SQLite boots;
--   PG is the target for integration tests. Confirmed by the
--   project KB; not fixed in this commission. The unit tests for
--   P1 use F1-premarked throwaway DBs per the project critical
--   note convention (see
--   ``tests/fakes/f1_premark_helper.py:48``).

-- UP

CREATE TABLE IF NOT EXISTS live_view_passcodes (
    id TEXT PRIMARY KEY,
    root_name TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    wrapped_passcode BLOB NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_attempt_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_live_view_passcodes_root_rel
    ON live_view_passcodes(root_name, rel_path);

CREATE INDEX IF NOT EXISTS idx_live_view_passcodes_locked
    ON live_view_passcodes(root_name, rel_path)
    WHERE locked <> 0;

CREATE TABLE IF NOT EXISTS live_view_passcode_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    root_name TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    generation INTEGER NOT NULL,
    ip_hmac TEXT,
    actor TEXT
);

CREATE INDEX IF NOT EXISTS idx_live_view_passcode_audit_root_at
    ON live_view_passcode_audit(root_name, at);

-- DOWN

DROP INDEX IF EXISTS idx_live_view_passcode_audit_root_at;
DROP TABLE IF EXISTS live_view_passcode_audit;
DROP INDEX IF EXISTS idx_live_view_passcodes_locked;
DROP INDEX IF EXISTS uq_live_view_passcodes_root_rel;
DROP TABLE IF EXISTS live_view_passcodes;