-- Migration: create the anomaly_clusters table
--           (the `nest-report` tool category — the incident identity
--            anchor, phase 1 of the nest-anomaly-report lane)
-- Created: 2026-11-05
-- Phase: Phase 1 (C1) of the nest-anomaly-report commission
--        (board wi-2d691ed2, base 1f6898ae2).
--
-- What lives here:
--   * ``anomaly_clusters`` — one row per INCIDENT: the key components
--     (tree root + canonical category), the sliding-window anchor,
--     the incremental member count, and the max-rank severity rollup.
--
-- WHY A SECOND TABLE (decisions.md R5):
--   The metric family's pull adapter needs ONE cheap aggregate (a
--   GROUP BY over the cluster columns) and the board item points at
--   a cluster, not at a report. A query-time fold would
--   re-implement the sliding window in SQL AND make cluster identity
--   retroactively unstable — a later report could re-fold earlier rows
--   into a different "incident". The materialized cluster row IS the
--   identity anchor.
--
-- THE KEY AND ITS INDEX (the ONE pick, justified in decisions.md R5):
--   The write-time lookup is exactly ``WHERE tree_root=? AND
--   category=? AND last_report_at >= cutoff ORDER BY last_report_at
--   DESC LIMIT 1`` — a single composite-index seek serves it, which
--   is what ``idx_anomaly_clusters_key_window`` provides.
--   ``UNIQUE(cluster_key_hash)`` was REJECTED: the sliding window
--   legitimately INSERTs a second cluster for the same key after a
--   quiet period (a re-incident — same root, same category, hours
--   later), and a full unique constraint would turn that legitimate
--   INSERT into an IntegrityError. A partial unique ("open only") is
--   impossible because resolution is DERIVED — there is no open flag
--   to index on.
--
-- TIMESTAMPS (AM-4):
--   FIXED-WIDTH ISO-8601 TEXT — always 32 characters, always
--   ``+00:00``, written by the local ``_utc_now_iso`` in
--   daemon/repositories/anomaly/models.py. String comparison is the
--   window predicate; ALL cutoffs are computed in PYTHON and bound as
--   parameters — never SQL ``now() - interval``.
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     ``SQLModel.metadata.create_all`` with the :class:`AnomalyCluster`
--     model (daemon/repositories/anomaly/models.py) as the canonical
--     source. The model registers into the metadata via the
--     late-import block in ``daemon/api.py`` BEFORE the create_all
--     call. Brand-new table — NO ``_ensure_postgres_columns`` mirror.
--     The PG-side runner is a NO-OP for this file.
--   - Column names and types stay byte-identical to the model
--     (canonical source for PG). Every string column is TEXT on both
--     drivers — never VARCHAR. ``report_count`` is INTEGER on both.
--
-- INDEX NAMES are byte-identical to ``__table_args__`` in
-- daemon/repositories/anomaly/models.py:
--   idx_anomaly_clusters_key_window (tree_root_instance_id, category, last_report_at)
--
-- WHY EVERY CREATE CARRIES IF NOT EXISTS (MANDATORY):
--   On the boot path ``SQLModel.metadata.create_all(self._engine)``
--   runs FIRST and the migration runner applies this file IMMEDIATELY
--   AFTER it. A bare CREATE would raise "already exists" and the
--   runner would SWALLOW that error on its idempotent-skip branch —
--   the failure would be silent and this file would degrade into a
--   no-op on every boot.
--
-- RUNTIME-SPLITTER NOTE (defensive):
--   Full-line ``--`` comments are stripped by the runner before the
--   statement split, and inline (trailing) comments are left alone —
--   a semicolon inside an inline comment corrupts the statement
--   boundaries. THE INVARIANT THIS FILE UPHOLDS: no INLINE (trailing)
--   comment carries a semicolon.

-- UP

CREATE TABLE IF NOT EXISTS anomaly_clusters (
    id TEXT NOT NULL PRIMARY KEY,
    cluster_key_hash TEXT NOT NULL,
    tree_root_instance_id TEXT NOT NULL,
    category TEXT NOT NULL,
    first_report_at TEXT NOT NULL,
    last_report_at TEXT NOT NULL,
    report_count INTEGER NOT NULL DEFAULT 1,
    max_severity TEXT NOT NULL,
    board_item_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_anomaly_clusters_key_window
    ON anomaly_clusters (tree_root_instance_id, category, last_report_at);

-- DOWN

DROP INDEX IF EXISTS idx_anomaly_clusters_key_window;
DROP TABLE IF EXISTS anomaly_clusters;
