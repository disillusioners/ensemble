-- Migration: add auto_continued_at column to task table
-- Created: 2026-10-04
-- Author: coder-lane (feature/auto-continue-running-after-restart)
-- Description: Boot auto-continue CAS marker. Stamped by
--              ``daemon/services/auto_continue_boot_pass.py`` AFTER
--              ``_schedule_explicit_handle_resume`` returns ``{"status":
--              "resuming"}`` so "marked" ≡ "continued" (a′ semantics —
--              D2 / architecture-recommendation.md Focus 1).
--
--              The selection predicate
--              ``(auto_continued_at IS NULL OR auto_continued_at < :boot_epoch)``
--              re-arms per boot epoch so a still-RUNNING orphan after the
--              first stamp is re-considered on the next boot (the <
--              boot_epoch arm — see D17 / architecture-recommendation.md
--              Focus 1 "What < :boot_epoch buys over IS NULL").
--
--              Advisory-only column — never a status. The shared
--              ``complete_task`` SQL stays byte-identical to pre-feature
--              (D18 r3 / D29 — the r2 fold's "AND auto_continued_at IS NOT
--              NULL" guard was REJECTED because it would silently make
--              worker-pool / task-processor completion a no-op). The
--              terminalizer fires ONLY at the call site gated on
--              ``task.auto_continued_at is not None``.
--
--              Dual-driver (SQLite-only runner; PG lives in
--              ``_ensure_postgres_columns`` at
--              ``daemon/manager.py:5834``, idempotent via ``IF NOT EXISTS``).
--              No new index — selection is gated on ``status='running'``,
--              served by ``idx_task_status_type_created``; stamp CAS is
--              PK-scoped (D17).
--
-- Template: ``daemon/migrations/versions/20260606_000001_add_task_last_heartbeat_at.sql``
--           (same dual-driver + DROP-style reversibility).

-- UP

ALTER TABLE task ADD COLUMN auto_continued_at TIMESTAMP;

-- DOWN

-- ALTER TABLE task DROP COLUMN auto_continued_at;
