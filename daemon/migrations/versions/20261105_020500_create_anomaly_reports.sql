-- Migration: create the anomaly_reports table
--           (the `nest-report` tool category — the nest-level anomaly
--            store, phase 1 of the nest-anomaly-report lane)
-- Created: 2026-11-05
-- Phase: Phase 1 (C1) of the nest-anomaly-report commission
--        (board wi-2d691ed2, base 1f6898ae2).
--
-- What lives here:
--   * ``anomaly_reports`` — one row per agent-filed anomaly: the
--     reporter identity (agent TYPE + instance), the CLOSED canonical
--     category (plus the raw input it folded from), the severity tier,
--     the scrubbed authored text, and the bounded evidence harvest
--     (ancestor chain, job/mission linkage, the bounded transcript
--     excerpt + its hash/length, the registry snapshot REFERENCE, the
--     wedge capture REFERENCES).
--
-- WHY A DEDICATED TABLE:
--   No table to reuse. ``notify_audit`` is the ari-voice ledger,
--   ``instance_ops_audit`` the local destructive-act ledger,
--   ``mcp_gateway_audit`` the remote-command ledger and
--   ``wedge_captures`` the capture-metadata table. The anomaly report
--   is a fifth thing: the agent-authored INCIDENT record, keyed by a
--   closed category vocabulary so the metric family can chart it
--   without open-cardinality labels (decisions.md R1).
--
-- WHY category_raw EXISTS ALONGSIDE category (R1):
--   ``category`` holds the CANONICAL value (the closed vocabulary —
--   the metric label source). ``category_raw`` holds the reporter's
--   verbatim input, capped at 64 chars, so a vocabulary gap stays
--   VISIBLE instead of being silently folded away. An unknown
--   category folds to the reserved ``other``; the raw column is what
--   tells the morning review which new synonym to add.
--
-- WHY excerpt IS HASHED AND LENGTHED (R6):
--   The excerpt is raw tool stdout (the bash tool returns STDOUT
--   verbatim, 150k cap) — the highest-risk field. It is stored
--   scrubbed and bounded, with ``excerpt_sha256`` + ``excerpt_length``
--   computed over the STORED text, so a reviewer can verify integrity
--   without a second copy of the content. It is NULL for ``minor``
--   severity (no transcript read at all) and NULL when the value
--   scrubber is absent (the fail-CLOSED leg — the pattern masker is
--   the always-on floor, but the excerpt is raw stdout).
--
-- TIMESTAMPS (AM-4):
--   ``created_at`` is a FIXED-WIDTH ISO-8601 TEXT stamp — always 32
--   characters, always ``+00:00``, written by the local
--   ``_utc_now_iso`` in
--   daemon/repositories/anomaly/models.py. This is a DOCUMENTED
--   divergence from the notify_audit sibling, whose
--   ``isoformat()``-without-timespec shape IS the hazard. String
--   comparison is the window predicate (fixed-width ISO-8601 UTC
--   sorts lexicographically); the PG session timezone cannot shift a
--   digit. ALL cutoffs are computed in PYTHON and bound as
--   parameters — never SQL ``now() - interval`` (that shape ERRORS
--   on PostgreSQL against a TEXT column).
--
-- DUAL-DRIVER NOTES
--   - SQLite (this file): runs via the .sql migration runner on every
--     startup (idempotent CREATE TABLE IF NOT EXISTS). One .sql per
--     migration event — this file is the persistent record.
--   - PostgreSQL: the schema comes from
--     ``SQLModel.metadata.create_all`` with the :class:`AnomalyReport`
--     model (daemon/repositories/anomaly/models.py) as the canonical
--     source. The model registers into the metadata via the
--     late-import block in ``daemon/api.py`` BEFORE the create_all
--     call. Brand-new table — NO ``_ensure_postgres_columns`` mirror.
--     The PG-side runner is a NO-OP for this file.
--   - Column names and types stay byte-identical to the model
--     (canonical source for PG). Every string column is TEXT on both
--     drivers — never VARCHAR. ``excerpt_length`` / ``report_count``
--     style counters are INTEGER on both.
--   - The JSON columns use the house dual-face type: SQLite JSON /
--     PostgreSQL JSONB, via ``JSONBType``.
--
-- INDEX NAMES are byte-identical to ``__table_args__`` in
-- daemon/repositories/anomaly/models.py (the model / migration /
-- storage-test-pack lockstep):
--   idx_anomaly_reports_agent_created       (agent_id, created_at)
--       — also serves the phase-2 rate-limit COUNT.
--   idx_anomaly_reports_cluster_id          (cluster_id)
--   idx_anomaly_reports_tree_root_created   (tree_root_instance_id, created_at)
--       — serves the per-tree capacity count + the AM-6 retention
--         decrements.
--   idx_anomaly_reports_category_created    (category, created_at)
--   idx_anomaly_reports_outcome_created     (outcome, created_at)
--       — serves retention and the refusal census.
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
--   full-line ``--`` comments, which the runner strips before
--   splitting the remainder on ``;``
--   (daemon/migrations/runner.py:670-673).

-- UP

CREATE TABLE IF NOT EXISTS anomaly_reports (
    id TEXT NOT NULL PRIMARY KEY,
    created_at TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    tree_root_instance_id TEXT,
    parent_chain JSON NOT NULL DEFAULT '[]',
    parent_chain_agents JSON NOT NULL DEFAULT '[]',
    category TEXT NOT NULL,
    category_raw TEXT NOT NULL,
    severity TEXT NOT NULL,
    description TEXT NOT NULL,
    suspected_cause TEXT,
    excerpt TEXT,
    excerpt_sha256 TEXT,
    excerpt_length INTEGER,
    job_id TEXT,
    mission_id TEXT,
    dispatch_chain JSON,
    boot_epoch TEXT,
    registry_summary_sha256 TEXT,
    registry_captured_at TEXT,
    wedge_capture_ids JSON,
    cluster_id TEXT,
    outcome TEXT NOT NULL DEFAULT 'accepted',
    refusal_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_anomaly_reports_agent_created
    ON anomaly_reports (agent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_anomaly_reports_cluster_id
    ON anomaly_reports (cluster_id);

CREATE INDEX IF NOT EXISTS idx_anomaly_reports_tree_root_created
    ON anomaly_reports (tree_root_instance_id, created_at);

CREATE INDEX IF NOT EXISTS idx_anomaly_reports_category_created
    ON anomaly_reports (category, created_at);

CREATE INDEX IF NOT EXISTS idx_anomaly_reports_outcome_created
    ON anomaly_reports (outcome, created_at);

-- DOWN

DROP INDEX IF EXISTS idx_anomaly_reports_outcome_created;
DROP INDEX IF EXISTS idx_anomaly_reports_category_created;
DROP INDEX IF EXISTS idx_anomaly_reports_tree_root_created;
DROP INDEX IF EXISTS idx_anomaly_reports_cluster_id;
DROP INDEX IF EXISTS idx_anomaly_reports_agent_created;
DROP TABLE IF EXISTS anomaly_reports;
