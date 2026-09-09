# 05 — Repair Runbooks

last-verified-against: v0.12.4

Runbooks go through pause-first (§04 (iv)).

## R1 — Pause-first quiesce
**T:** repair needs quiescent instance.
**S:** `pause_instance_cascade(<iid>)` → wait ≤30s → `status==PAUSED` → mutate → `resume_instance_cascade(<iid>)`.
**V:** `status=RUNNING`; no `FAILED`. **A:** `daemon/services/instance_lifecycle.py`.

## R2 — Idempotent DROP NOT NULL
**T:** NOT NULL → NULL on prod PG.
**S:** `DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns WHERE ... AND is_nullable='NO') THEN ALTER TABLE <t> ALTER COLUMN <c> DROP NOT NULL; END IF; END $$`; commit; re-run.
**V:** `\d <table>` nullable; second pass no-op. **A:** `daemon/manager.py:4996-5015` (§04 (iii)).

## R3 — CHECKPOINT-aware migration
**T:** adding columns to a table a task writes into.
**S:** Pause-first (§R1); `ALTER TABLE ... ADD COLUMN IF NOT EXISTS <col> <type>`; resume.
**V:** `\d <table>` lists new column; instance `RUNNING` without `IntegrityError`.

## R4 — MaintenanceJob registry
**T:** periodic sweep / health check.
**S:** `MaintenanceJob(name, min_interval_hours, execute_fn)` (`daemon/services/maintenance.py:68`); register via `MaintenanceService.register(...)` (`:119+`); 15m cadence.
**V:** `register(...)` returns row; `last_run` updated.

## R5 — StaleTaskRecovery
**T:** tasks stuck in PROCESSING past threshold.
**S:** `StaleTaskRecovery` (`daemon/services/stale_task_recovery.py:42`) finds stale tasks, sets `cancel_requested`, waits grace, force-cancels, reaps.
**V:** stale → `DEAD`/`QUEUED`.

## R6 — Orphan ACTIVE JobItem sweep
**T:** orphan ACTIVE JobItems past `min_orphan_age`.
**S:** `_periodic_drift_reconcile_loop` (`daemon/api.py:1186-1272`) calls `reconcile_drift_states`. Eligible: `admission_state='active'`, `created_at < now - min_orphan_age`, no Task rows, alive. Default `min_orphan_age=900s` (`daemon/config.py:1214`). f1 guard.
**V:** no orphan ACTIVE rows survive.

## R7 — DLQ replay (DEAD → QUEUED)
**T:** jobs in DEAD that should re-deliver.
**S:** `DeadLetterService.replay_from_dlq` (`daemon/services/dead_letter_service.py:370`); state machine permits `DEAD → QUEUED`.
**V:** replayed → DEAD → QUEUED.
