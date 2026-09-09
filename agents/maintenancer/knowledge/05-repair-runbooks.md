# 05 — Repair Runbooks

last-verified-against: v0.12.4

Each runbook: **trigger** → **steps** → **verification**. Runbooks
that mutate prod go through the pause-first quiesce sequence first
(§01 architecture + §04 trap (iv)).

## Runbook R1 — Pause-first quiesce

**Trigger:** any feature or repair needing a quiescent instance (config
flip, activation toggle, in-place migration).

**Steps:**
1. `pause_instance_cascade(<instance_id>)` — cancels in-flight
   `graph_task.cancel()`; LangGraph checkpoints at node boundaries.
2. Wait for bounded quiescence (≤30s; tune per workload).
3. Confirm `Instance.status == PAUSED` via `ens_db_inspect` or the
   instance router.
4. Apply the state mutation (config flip / schema patch / write).
5. `resume_instance_cascade(<instance_id>)` — DB-only `PAUSED → RUNNING`;
   `is_retry=True` resumes from checkpoint.

**Verification:**
- `ens_db_inspect` shows `status=RUNNING` post-resume.
- No `FAILED` rows appeared (pause-cancelled tasks stay in
  `PROCESSING`, not `FAILED`; `CancellationReason` discriminates pause
  from shutdown).

**Anchor:** `daemon/services/instance_lifecycle.py`.

## Runbook R2 — Idempotent DROP NOT NULL

**Trigger:** column needs NOT NULL → NULL on prod PG without breaking
DBs that already migrated.

**Steps:**
1. Run the `DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=<t> AND column_name=<c> AND is_nullable='NO') THEN ALTER TABLE <t> ALTER COLUMN <c> DROP NOT NULL; END IF; END $$` block inside a transaction.
2. Commit; verify by re-running — no-op on the second pass confirms
   idempotence.
3. Audit row in `repair_log` (same transaction, fail-closed).

**Verification:**
- `\d <table>` in psql shows the column as nullable.
- Re-running the block produces no error and zero changes.

**Anchor:** `daemon/manager.py:4996-5012` — the canonical recipe.

## Runbook R3 — CHECKPOINT-aware migration

**Trigger:** adding columns to a table that an in-flight graph task is
writing into.

**Steps:**
1. Pause-first (§R1) on the target instance.
2. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS <col> <type>` — the
   `IF NOT EXISTS` makes it idempotent.
3. Resume; mid-flight task sees the new column on its next checkpoint
   commit.

**Verification:**
- `\d <table>` lists the new column.
- The instance returns to `RUNNING` and serves a fresh request without
  IntegrityError.

## Runbook R4 — MaintenanceJob registry

**Trigger:** periodic sweep / health check (orphan ACTIVE jobs, stale
locks, drift reconcile).

**Steps:**
1. Define `MaintenanceJob(name, min_interval_hours, execute_fn)` —
   `daemon/services/maintenance.py:68`.
2. Register via `MaintenanceService.register(name=..., min_interval_hours=...,
   execute_fn=...)` — daemon/services/maintenance.py:119+ .
3. The `MaintenanceService` checks due-jobs every `check_interval_minutes`
   (default 15, daemon/services/maintenance.py:92-99).

**Verification:**
- `service.register(...)` returns the `MaintenanceJob` row.
- After one check interval, `last_run` is updated.

## Runbook R5 — StaleTaskRecovery

**Trigger:** tasks stuck in PROCESSING past threshold; need a 5-step
recovery protocol.

**Steps:**
1. `StaleTaskRecovery` (daemon/services/stale_task_recovery.py:42)
   finds stale running tasks past threshold and not yet cancelled.
2. Sets `cancel_requested` flag.
3. Waits for graceful shutdown (grace period).
4. Force-cancels tasks still running after grace.
5. Reaps and re-arms watchers.

**Verification:**
- Stale tasks transition to `DEAD` or `QUEUED` per recovery class.
- No tasks remain in PROCESSING past threshold.

## Runbook R6 — Orphan ACTIVE JobItem sweep

**Trigger:** orphan ACTIVE JobItems — active row + no Task rows + alive
instance — accumulating past `min_orphan_age`.

**Steps:**
1. The sweep runs on a periodic cadence (config-driven).
2. Eligible rows: `admission_state = 'active'` AND `created_at < now -
   min_orphan_age` AND no Task rows AND alive instance
   (`daemon/api.py:510-526`).
3. Default `min_orphan_age = 900s` (15 min — see
   `daemon/config.py:1214`).
4. Pattern-f1 subtree-alive guard protects against false-positives.

**Verification:**
- No orphan ACTIVE rows older than `min_orphan_age` survive the sweep.
- Healthy active jobs whose Task rows are still being enqueued are
  NOT touched (the 15-min window vs 5-min P1 grace).

**Anchor:** `daemon/api.py:510-526` (sweep logic) + `daemon/config.py:1214`
(`drift_reconcile_min_orphan_age_seconds=900`).

## Runbook R7 — DLQ replay (DEAD → QUEUED)

**Trigger:** jobs in DEAD that should re-deliver.

**Steps:**
1. Use the existing service method `DeadLetterService.replay_from_dlq`
   (`daemon/services/dead_letter_service.py:370`).
2. The state machine permits `DEAD → QUEUED`
   (`daemon/services/job_state_machine.py:60`).

**Verification:**
- Replayed jobs transition DEAD → QUEUED; instance state un-touched.
- Worker picks up the replayed job via the normal queue.

## Cross-refs

- §01 architecture (pause-first, idempotent DROP NOT NULL)
- §02 admission state (DEAD → QUEUED replay)
- §04 traps (sentinel bridge; pause-first prerequisite)
- §06 restart/upgrade runbook (pause-first reused for live ops)
