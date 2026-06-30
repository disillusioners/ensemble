-- Migration: backfill NULL terminal_reason on 'done' rows (Bug F3 fix)
-- Created: 2026-07-01
-- Description:
--   Phase 2 of the defer-seam bugfix (F3): ``list_work(status=...)``
--   now discriminates ``completed`` / ``failed`` / ``cancelled`` by
--   ``JobItem.terminal_reason`` at SQL level. Rows that pre-date the
--   F3 fix may still have ``terminal_reason IS NULL`` even though
--   they are in a terminal state — those rows would silently vanish
--   from the F3 status filter (only ``completed`` would see them via
--   the ``OR terminal_reason IS NULL`` clause; ``failed`` and
--   ``cancelled`` filters would drop them entirely).
--
--   Backfill rule (per F3 spec):
--     * If ``error_message IS NOT NULL AND error_message != ''``:
--       set ``terminal_reason = 'failed'`` (the row had a non-empty
--       legacy error message, so it almost certainly terminated via
--       the FAILED path).
--     * Otherwise: set ``terminal_reason = 'completed'`` (the safe
--       default for NULL ``terminal_reason`` rows per the legacy
--       ``done → completed`` map in
--       ``daemon/repositories/job_queue/models.py``).
--
--   Both statements are gated on ``admission_state = 'done' AND
--   terminal_reason IS NULL`` so re-runs are idempotent — once a row
--   has its ``terminal_reason`` populated, subsequent runs skip it.
--
--   Phase 5 compatibility: the ``error_message`` column was DROPPED
--   by Phase 5 (``20260628_000002_drop_admission_legacy.sql`` /
--   ``20260628_000002_drop_job_queue_legacy_columns.sql``). The
--   failed-aware UPDATE below references ``error_message`` and will
--   raise ``no such column: error_message`` on Phase 5+ databases.
--   The migration runner (``daemon/migrations/runner.py`` lines
--   374-380) catches that specific error and idempotently skips the
--   statement so the safe-default UPDATE below becomes the operative
--   backfill on Phase 5+ schemas. On pre-Phase-5 databases the
--   failed-aware UPDATE runs first and stamps ``failed`` where the
--   legacy ``error_message`` was non-empty, then the safe-default
--   UPDATE catches any remaining NULLs.
--
--   The PostgreSQL counterpart lives in
--   ``daemon/manager.py::_ensure_postgres_columns`` (the .sql runner
--   is a NO-OP on PG, so the equivalent UPDATE statements are
--   inlined there).

-- UP

-- Failed-aware backfill: rows with a non-empty legacy
-- ``error_message`` are stamped as 'failed' (the F3 spec says these
-- rows almost certainly terminated via the FAILED path). The
-- ``error_message`` column is dropped by Phase 5; the migration
-- runner catches ``no such column: error_message`` and skips this
-- UPDATE on Phase 5+ databases so the second UPDATE (default to
-- 'completed') is the sole backfill.
UPDATE job_queue_items
SET terminal_reason = 'failed'
WHERE admission_state = 'done' AND terminal_reason IS NULL
  AND error_message IS NOT NULL AND error_message != '';

-- Safe-default backfill: any 'done' row with NULL terminal_reason
-- is stamped as 'completed'. This matches the lossy legacy
-- ``done → completed`` mapping the resolver uses for rows that
-- don't have terminal_reason set. Idempotent: the WHERE clause
-- restricts to terminal_reason IS NULL, so re-runs are no-ops once
-- every row has been backfilled.
UPDATE job_queue_items
SET terminal_reason = 'completed'
WHERE admission_state = 'done'
  AND terminal_reason IS NULL;

-- DOWN
-- The DOWN section is a no-op: the migration only writes to an
-- existing column (terminal_reason was added in the 7c migration
-- ``20260629_000003_add_terminal_reason.sql``). Rolling back the
-- backfill would require clearing terminal_reason to NULL on every
-- 'done' row, which would silently drop the F3 status-filter
-- discrimination the migration just established. Skip the DOWN
-- (down-time recovery for this migration is "manually reset
-- terminal_reason to NULL if you need the pre-F3 lossy behaviour").