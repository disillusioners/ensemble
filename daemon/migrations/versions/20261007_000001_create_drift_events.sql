-- Migration: create drift_events (plugin-subsystem slice ⑥ — drift-event
--           publisher store, REC §1.2 component 7)
-- Created: 2026-10-07
-- Phase: Plugin subsystem slice ⑥ (trigger-engine hookup). One row per
-- emitted drift event (CON §5 verbatim payload; probe doc option (a) —
-- DB-backed, durable, replay-capable). "Unresolved" == "row exists":
-- resolution (the re-apply-or-drop disposition landing, CON §2) DELETES
-- the row; the manifest's divergence register stays the canonical
-- audit representation.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every startup.
--   - PostgreSQL: the table is created by ``SQLModel.metadata.create_all``
--     from the DriftEvent model (``daemon/plugin_subsystem/drift_event_publisher.py``)
--     at startup — brand-new tables need no ``_ensure_postgres_columns``
--     mirror (snapshot precedent, manager.py create_all prelude).
--   - Fresh databases of either flavor get the table automatically via
--     ``create_all``.
--   - ``target_class`` carries the payload's ``class`` field (``class`` is
--     an awkward SQL identifier); every other column carries the payload
--     field name verbatim.
--   - ``files`` is JSON (list of strings) via JSONBType on the model —
--     same dual-dialect JSON treatment as ``skill_triggers.condition_json``.
--   - Index names match the model declaration so both dialects converge.

-- UP

CREATE TABLE IF NOT EXISTS drift_events (
    id TEXT PRIMARY KEY,
    plugin TEXT NOT NULL,
    target_class TEXT NOT NULL,
    divergence_id INTEGER NOT NULL DEFAULT 0,
    files JSON NOT NULL DEFAULT '[]',
    delta TEXT NOT NULL DEFAULT '',
    rationale TEXT NOT NULL DEFAULT '',
    pinning_test TEXT NOT NULL DEFAULT '',
    observed_at TEXT NOT NULL,
    observed_tag TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_drift_events_plugin ON drift_events(plugin);
CREATE INDEX IF NOT EXISTS ix_drift_events_plugin_divergence ON drift_events(plugin, divergence_id);
CREATE INDEX IF NOT EXISTS ix_drift_events_observed_at ON drift_events(observed_at);

-- DOWN

DROP INDEX IF EXISTS ix_drift_events_observed_at;
DROP INDEX IF EXISTS ix_drift_events_plugin_divergence;
DROP INDEX IF EXISTS ix_drift_events_plugin;
DROP TABLE IF EXISTS drift_events;
