-- Migration: create app_settings
--           (db-backed-settings commission, Phase 1 — backend storage)
-- Created: 2026-10-18
-- Phase: db-backed-settings P1 (plan set:
--   .agents/shared/planning/db-backed-settings/). Backing store for
--   DB-backed daemon runtime settings; v1 manages exactly ONE key:
--   ``limits.llm_concurrency`` (the LLM worker-limit knob).
--
-- What lives here:
--
--   * One row per MANAGED key. The managed-key registry
--     (``daemon/services/settings_service.py``) is the single source
--     of managed-key truth — unregistered keys are refused on every
--     read/write path, so this table cannot accumulate junk keys.
--   * Resolution ladder (architecture-recommendation.md, BINDING):
--     DB row > os.environ (LIMITS_LLM_CONCURRENCY) > yaml literal >
--     field default. v1 apply semantics are restart-required (the
--     daemon-wide semaphore is constructed once at boot from the
--     resolved value; no live re-arm).
--   * ``value_json`` carries the JSON-encoded scalar;
--     ``value_type`` the registry type tag. Per-key type + range
--     validation lives in the REGISTRY (mirroring the pydantic Field
--     constraint ``ge=1`` exactly) — NOT in DB CHECK constraints
--     (dialect parity for zero benefit; two truths of validation
--     would drift). Invalid stored values are skipped at read time
--     with a class-specific ERROR log (fail-open to the lower tier).
--   * ``updated_by`` is the AuthContext.subject of the last writer;
--     NULL when auth is off (never the empty string — nullable-actor
--     precedent infra/models.py:257).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: brand-new table → created by
--     ``SQLModel.metadata.create_all`` from the :class:`AppSetting`
--     model (daemon/models/app_settings.py), which registers into the
--     metadata via the one-line late-import block in manager.py
--     BEFORE the create_all call (snapshot / maintenance_runs /
--     drift_events precedent). Brand-new tables need NO
--     ``_ensure_postgres_columns`` mirror. The PG-side runner is a
--     NO-OP for this file.
--   - Column names/types stay byte-identical to the AppSetting model
--     (canonical source for PG); parity is checked by the migration
--     test runner.
--   - No indexes beyond the PRIMARY KEY (≤10 rows forever at v1
--     scope — Ruling 1).
--   - The settings BOOT READ (apply_db_settings_overrides) runs
--     BEFORE create_all / this runner — a missing table is the
--     NORMAL first-boot state and reads as "no overrides"
--     (fail-open; never a boot-failure mode).

-- UP

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT NOT NULL PRIMARY KEY,
    value_json TEXT NOT NULL,
    value_type TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);

-- DOWN

DROP TABLE IF EXISTS app_settings;
