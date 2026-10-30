# Research Findings — LOCKS / REPAIR / WATCHES / RIDERS (wi-78967dba close-path planning)

Read-only research. Lane worktree `/home/nea/ensemble-src-wt-missed-finalize`, branch `fix/missed-finalize-zombie-active`, base HEAD `22fe83317`. All file:line references observed at this HEAD. Sibling explorer owns finalize/liveness machinery (sweeps, E1 gate, strand-healer internals) — not duplicated here except where the lock/watch surfaces intersect.

---

## 1. job_locks LIFECYCLE

**Schema/model.**
- Model: `JobLock` — `daemon/repositories/job_queue/models.py:478-522`. PK `lock_id` (uuid4), `project_id`, `queue_id`, `job_id`, `instance_id` (nullable), `lock_slot` int (default 0), `acquired_at` ISO str. `__tablename__ = "job_locks"` (:501).
- Capacity invariant: `UniqueConstraint("project_id","queue_id","lock_slot", name="uq_job_locks_slot")` (`models.py:504-507`). Model docstring (:481-499): at most `concurrency_limit` rows per queue; two processes racing for the same slot cannot both succeed; "lock_slot is not user-meaningful".
- Owning migrations:
  - `daemon/migrations/versions/20260420_000001_add_job_system_improvements.sql:8-19` — CREATE TABLE IF NOT EXISTS job_locks + 3 indexes (project_id/queue_id/instance_id).
  - `daemon/migrations/versions/20260619_000001_add_lock_slot_to_job_locks.sql:52-70` — DELETE all rows (stale by definition at migration time, :47), ADD COLUMN `lock_slot`, CREATE UNIQUE INDEX `uq_job_locks_slot`.
  - PG runtime column/index ensure: `daemon/manager.py:7737-7762` (`ALTER TABLE job_locks ADD COLUMN IF NOT EXISTS lock_slot`, `CREATE UNIQUE INDEX IF NOT EXISTS uq_job_locks_slot`).

**Acquire sites.**
- Atomic per-slot claim primitive: `LockRepository.try_acquire_slot` — `daemon/repositories/job_queue/lock_repository.py:182-261`. Dialect-branched `INSERT ... ON CONFLICT (project_id,queue_id,lock_slot) DO NOTHING` (PG, :226-236) / `INSERT OR IGNORE` (SQLite, :238-247); wins iff rowcount==1.
- Service wrapper: `JobLockManager.acquire_queue_lock` — `daemon/services/job_lock_manager.py:65-117`: loops slots `0..concurrency_limit-1`; returns False if ALL slots taken. The DB INSERT is "the only cross-process synchronisation point" (:80).
- **Primary production acquire = job start, not enqueue:** `JobRepository.start_job_atomic_with_lock` — `daemon/repositories/job_queue/repository.py:3333-3444+`. Collapses lock INSERT + `admission_state PENDING→PROCESSING('queued'→'active')` UPDATE into ONE `engine.begin()` (B1 fix; PG deferred trigger `trg_job_locks_active_guard` requires both visible at COMMIT, :3344-3362). Returns `(None, False)` when no slot free — job stays queued (:3369-3370).
- Second inline acquire (enqueue-side, task-type): `daemon/repositories/job_queue/repository.py:3625-3642` (same dialect-branching inline INSERT; per `docs/architecture/job-as-front-primitive-invariants.md:47` "enqueue_task_job inserts a job_locks row in the same transaction").
- Message-type JobItems NEVER insert job_locks (`docs/architecture/job-as-front-primitive-invariants.md:137-139`).

**Release sites.**
- `LockRepository.release / release_by_job / release_by_instance` — `lock_repository.py:52-130` (F09 atomic single-DELETE, rowcount semantics, no RETURNING due to pysqlite commit-cursor issue :65-74).
- `JobLockManager.release_queue_lock` — `job_lock_manager.py:119-138` (delegates `release_by_job`).
- Finalize hot path: `_finalize_job_db_sync` Step 3 DELETEs job_locks (`docs/architecture/job-as-queue-proxy-invariants.md:142` — "Step 2 instance.status at L2518; Step 3 DELETE `job_locks` at L2529").
- F5 atomic same-commit release (orphan reaper): `repository.py:4288-4347` — JobItem UPDATE `active→done` and `DELETE FROM job_locks WHERE job_id = :job_id` in ONE transaction; **job-scoped, NOT instance-wide** (W1: sibling/successor locks survive, :4258-4269); skips DELETE when `instance_id` is NULL (:4270-4273).
- Other DELETE arms (hard-delete cascades): `daemon/repositories/instance/repository.py:3478-3484` (job_id subquery), `daemon/repositories/task/repository.py:2316-2318`, `daemon/repositories/project/repository.py:1190`, terminate cascade `instance_lifecycle.py:2555`.

**Crash behavior / orphan locks.**
- Orphan classes documented at `lock_repository.py:271-282`: job hard-deleted/never existed; job terminal but worker crashed between `atomic_transition` and `release_queue_lock`, or release errored; soft-deleted job.
- Boot recovery: `api.py:933-944` awaits `job_lock_manager.recover_stale_job_locks()` BEFORE first acquire; failure non-fatal (warn-only).
- Steady-state sweep: `JobLockSweepService` (`daemon/services/job_lock_sweep.py:93-199`) → `cleanup_terminal_job_locks` → `clear_terminal_job_locks` (`lock_repository.py:304-314`, same logic as `clear_stale_job_locks` :267-302). Interval knob `job_lock_sweep_interval_seconds` default 90s, `ge=1` FAIL-FAST, env `SERVICES_JOB_LOCK_SWEEP_INTERVAL_SECONDS` (`daemon/config.py:2428-2462`); ALWAYS-ON, **no kill-switch by HARD POLICY** (:2437-2438, :2456-2457).

**"Never delete a lock whose job still claims active" — where enforced.**
- Every bulk DELETE is guarded by `_ACTIVE_JOB_IDS_SUBQUERY` = `job_id NOT IN (SELECT job_id FROM job_queue_items WHERE admission_state IN {queued,active} AND deleted_at IS NULL)` — `lock_repository.py:18-34` (comment explains the C3 race-window fix: BOTH 'queued' AND 'active' must be in the active set), used at :294-302 and :304+.
- PG triggers (both directions) installed by `daemon/manager.py:8201-8206`: `job_queue_items_active_lock_guard` (an 'active' task-type JobItem must have a job_locks row at COMMIT) and `job_locks_active_guard` (fires on INSERT/UPDATE of job_locks only — a lock row must match an active JobItem). NOTE: `trg_job_locks_active_guard` does NOT fire on DELETE, so the delete-side invariant is procedural (the NOT-IN guard), not trigger-enforced (`repository.py:4237-4244`).
- Reaper docstring spells out why the reaper's DELETE is safe: scoped `job_id NOT IN (active ids)` "the same atomic invariant as clear_terminal_job_locks" (`repository.py:4237-4244`).

**Queue-slot pressure mechanics ("slot held for hours").**
- A zombie-ACTIVE JobItem keeps its `job_id` in the active set → `_ACTIVE_JOB_IDS_SUBQUERY` excludes it from BOTH the boot sweep and the 90s F3 sweep → its `job_locks` row is immortal while `admission_state='active'`.
- Each surviving row permanently occupies one of `concurrency_limit` slots per `(project_id, queue_id)` (unique index). `start_job_atomic_with_lock` returns `(None, False)` for every later job when all slots are consumed → new jobs cannot start (`task/repository.py:2875` records the "denied by start_job_atomic_with_lock" path); per the Job Lock & Work blueprint, `acquire` failure is the deferral/stall input. With limit=1 (legacy project queues, `repository.py:3388-3391`) a single zombie lock blocks the entire queue indefinitely — that is the mechanical meaning of "slot held for hours".

---

## 2. OPERATOR REPAIR LANE — `ens_db_repair_execute` (`daemon/tools/ens_db_tools.py`)

**Semantics (verified).**
- Kill-switch: `KILL_SWITCH_ENV = "ENSEMBLE_REPAIR_ENABLED"` (`ens_db_tools.py:134`), code default ON via `_resolve_repair_enabled` (:128-137, :997); flag-OFF returns byte-identical no-op (module docstring :28-31).
- Three gates before any write: confirm nonce + audit + dry-run-first (:19-21, :122-124).
- Nonce: `_RepairNonceStore` (:498-575) — in-memory, single-use, TTL 5 min, bound to `(instance_id, target)`; consume fail-closed vocabulary `nonce-mismatch / nonce-already-used / nonce-instance-mismatch / nonce-expired / nonce-action-mismatch` (:540-546).
- Audit: `RepairLog` append-only model (`daemon/repositories/ens_db/models.py:44-79`) — every dry-run AND committed repair writes a row (sql_text verbatim, sql_hash, dry_run, before/after JSONB snapshots, outcome, confirm nonce); **the audit INSERT rides the SAME transaction/connection as the repair — fail-closed** (models.py:48-52; ens_db_tools.py:35-41, audit write at :1644).
- Idempotency contract: "Idempotent DO$$-only DDL/DML is the rule" so partial application is replayable from `repair_log` (models.py:54-59); atomic single-DELETE rowcount semantics everywhere in the lock repo (F09).
- Dedicated engine: `_build_repair_engine` (:583-610) — separate 2+3 pool, `pool_pre_ping=True`, `pool_recycle=3600`, `pool_timeout=10s`, PG `connect_args` `statement_timeout=60s / lock_timeout=10s / idle_in_transaction_session_timeout=60s` (:22-27, :642, :711); R13: never registered into the user-facing external pool manager (:45-49, :594-599).
- Other standing refusals: self-surgery (SQL targeting the calling instance or audit-sensitive tables `instances/messages/message_queue/tasks`, :50-54) and the migration-runner trap (refuses `*.sql` inputs; "runner.py:486-491 is SQLite-only; PG schema uses create_all + _ensure_postgres_columns", :55-57).
- Error surface: class-name-only error strings, no SQL echo (:59-61).

**Proven ORDERING — finalize JobItem FIRST, release job_locks LAST.**
- Canonical in-source ordering fix (F12/F4, drift reconciler): `daemon/services/job_recovery_service.py:1400-1416` — "finalize FIRST, then cancel. The previous order (cancel → finalize) was an unrecoverable wedge… Finalize fails → state unchanged → retry next cycle; Finalize succeeds, cancel fails → JobItem=done + task=pending → Pattern (d) catches it."
- The lock-side restatement of the same rule is the F5 atomic pattern: JobItem UPDATE commits BEFORE the lock DELETE inside the same transaction (`repository.py:4241-4244, 4288-4347`) — the lock release is meaningful only once the job left the active set; a lock deleted while the job still claims active both violates the delete-side guard and frees a slot for a second admission of work the zombie still "owns" (the double-admission risk). The blueprinted operator protocol ("finalize JobItem first, release held job_locks last") is the manual-runbook form of this; its in-repo enforcement is the `NOT IN active` guard on every lock DELETE (§1) plus the reaper's same-transaction pairing.

**Guardrails a new AUTOMATED sweep should MIRROR:**
1. Job-scoped lock release only (`WHERE job_id = :job_id`), never instance-wide (W1, `repository.py:4258-4269`).
2. The `job_id NOT IN (active-set)` guard on every lock DELETE (`lock_repository.py:18-34`).
3. Single-transaction finalize + lock release (F5, `repository.py:4294-4347`) — crash between the two cannot orphan either side.
4. Ordering: state transition first, lock release last (`job_recovery_service.py:1400-1416`).
5. Idempotency by construction: guarded UPDATE rowcount==0 ⇒ concurrent writer won ⇒ no-op on the lock side too (`repository.py:4325-4329`); idempotent DELETEs.
6. Append-only audit of every act + refusal (audit-as-signal precedent: notify_audit ledgers refusals, `20261025_000001_create_notify_audit.sql:9-10`).

**Guardrails an automated sweep must NOT mirror (operator-only constraints):**
1. Human confirm-nonce ceremony (mint/consume TTL store) — an autonomous sweep's authorization is its config default + category allow-list, not a per-action human nonce.
2. Dry-run-first interactive gate — the sweep analogue is a detect/counters-only mode knob if desired, not a mandatory preview step.
3. Dedicated 2+3 repair engine — the sweep should reuse the shared manager engine/repositories (the DB-pool lane's F2 posture: fewer engines, attribution via `application_name`), never mint its own engine per sweep.
4. Free-form SQL execution surface — the sweep must call typed repository/service methods (the `ens_db_repair_execute` free-SQL shape is precisely what makes it operator-gated).
5. Self-surgery/target-set refusals are tool-layer concerns; the sweep's safety comes from a fail-closed predicate (strand-healer precedent: "every leg must hold; any unprovable leg SKIPS", `config.py:2640-2644`).

---

## 3. STORED WATCHES + WATCHER BUCKETS

**StoredWatch (Option A quarantine, fix e8a61da3).**
- Model `daemon/repositories/job_queue/stored_watch_models.py:1-128`: one row per watcher per re-arm executed by the in-transaction F6 hook (`RetryTurn.run` step 4, `turn_transitions.py`) when a usage-limit deferral re-keys `job_watchers` from the cancelled parent's work_id onto the retry child's fresh work_id (:7-11).
- Columns: `original_work_id`/`retry_work_id` re-key pair (:105-107), `watcher_instance_id` (FK instances, :110), `original_watch_events` (audit fidelity of what the watcher asked for vs the armed `mission_terminal`, :112-115), BOTH `mission_instance_id` (permanent-tree ROOT via parent_id walk — the GC's `evaluate_mission_live` anchor) and `work_instance_id` (:117-119).
- GC lifecycle `status`: `armed` → `released` (carrier alive / coverage completed) | `rehydrated` (carrier lost; GC re-inserted a pure mission_terminal row on original_work_id) (:32-37); status transition is a CAS on `status='armed'` — the exactly-once fence (:36-37). Constants `STORED_WATCH_STATUS_{ARMED,RELEASED,REHYDRATED}` (:63-65).
- First-store-wins fence: `UniqueConstraint(original_work_id, watcher_instance_id)` with hook INSERT ON CONFLICT DO NOTHING (:73-82).
- Explicitly NOT columns on job_watchers (three-writer contention) and NOT S15-piggyback; standalone table + own `.sql` per the zero-exceptions rule; PG gets it via create_all, no `_ensure_postgres_columns` mirror (brand-new table) (:39-45). Wired in `api.py:961-967` (explicit `StoredWatch.metadata.create_all` + repo on manager).
- Companion migration: `daemon/migrations/versions/20261011_000001_create_stored_watches.sql`.

**work_notifier CAS buckets.**
- `notify_work_watchers` three-bucket classifier — `daemon/services/work_notifier.py:346-366`:
  - **matching_claimable** — all subscribed events fire NOW; CAS-claimed before notify (N1 exactly-once) (:349-353).
  - **matching_readonly** — SOME subscribed events fire now (multi-kind retire rule; the LAST firing event is the one that claims); delivered WITHOUT claiming, row survives (:354-362).
  - **held_for_mission** — mission_terminal-only AND mission still live; skipped entirely; survives for the future mission-terminal re-fire (:363-366).
- Claim-first CAS: atomic `DELETE ... RETURNING` via `watcher_repo.claim_watchers_for_job_for_instances` BEFORE any enqueue_message (:368-380); exactly-once invariant — the repo-level CAS is the ONLY enforcement; "Do not add new notify-then-claim call sites" (:401-409). Non-terminal statuses never claim (:393-399).
- Mission-live guard invoked exactly once per call (C1, :330-344).

**What finalize does to watcher rows today.**
- Legacy notify path: on terminal status, after notifying, `remove_all_watches_for_job` (`job_queue_service.py:506-509`; repo `watcher_repository.py:274+`, idempotent DELETE; double-notify hazard documented :311-313).
- Reconcile paths: `reconcile_held_watches_for_instance` re-fires held mission_terminal rows through the SAME partition/CAS path (`job_queue_service.py:753-784`); zombie-GC leg retires rows whose `job_id` is unresolvable AND liveness terminal (`watch_reconcile_sweep.py`, `remove_all_watches_for_job` at :946 call site).
- `job_watchers` has NO state column/history — a consumed row leaves only the zero-watcher WARN line (blueprint forensics rule; matches `watcher_repository` shape).

**Implications for a new terminalization sweep (watchers).**
- A sweep that finalizes a zombie JobItem should drive the EXISTING notify path (`notify_work_watchers` partition + CAS), never notify-then-claim directly (work_notifier.py:401-409 ban).
- Zero-watcher terminal-fire: terminalizing a job with NO watcher rows must still log the zero-watcher WARN (the only trace `job_watchers` leaves) — mirror the three existing zero-watcher log sites.
- Held `mission_terminal` rows on the zombie's work_id: the C1/E1 gate still governs the terminal fire; the sweep must not force-claim rows whose mission is live — route through `reconcile_held_watches_for_instance` semantics (held_for_mission rows survive until mission-terminal).
- stored_watches: rows reference work_ids, not admission_state — a JobItem finalize does not touch them; no interaction beyond the GC's own liveness predicates.

---

## 4. AUDIT SURFACES + TABLE CONVENTION

**Prior art.**
- Strand-healer: NO DB audit table — structured greppable log lines `mission_strand_healer|running-zombie: ...` (`daemon/services/mission_strand_healer.py:825, 832, 846, 858, 877, 1324, 1358`) + counters; the durable record is the production completion chain the heal drives through (config.py:2687-2689 "TERMINALIZING WRITES through the production completion chain").
- maintenance_runs: per-section DB rows with partial unique `uq_maintenance_runs_running_section WHERE status='running'` = cross-daemon single-flight claim — `daemon/migrations/versions/20260927_000001_create_maintenance_runs_table.sql:45`.
- notify_audit ledger: `daemon/migrations/versions/20261025_000001_create_notify_audit.sql` — one row per attempt INCLUDING refusals ("the refusal is the security signal", :9-10); body stored as sha256+length only (:30-35); ISO-8601 TEXT created_at, lexicographic window predicates (:45-49); fail-closed on the send path (N2, :26-27).
- instance_ops_audit: two-tier audit boundary DECLARED in the migration — Tier-1 same-transaction acts, Tier-2 write-ahead pending→committed/error (`20261021_120000_create_instance_ops_audit.sql:28-48`); "the two tiers are DECLARED, not silently de-coupled".
- repair_log: same-transaction append-only, reconstructible partial application (§2).

**REQUIRED *.sql-per-new-SQLModel-table convention (with citations).**
- "One .sql per migration event — this file is the persistent record" + runner applies on SQLite every startup; "PostgreSQL: the schema comes from SQLModel.metadata.create_all with the model as canonical source… The PG-side runner is a NO-OP for this file" (`20261025_000001_create_notify_audit.sql:51-64`; identical text in `20261021_120000_create_instance_ops_audit.sql:50-61`).
- Runner SQLite-only fact also asserted by the repair tool's migration-runner trap: "runner.py:486-491 is SQLite-only; PG schema uses create_all + _ensure_postgres_columns" (`ens_db_tools.py:55-57`).
- PG column evolution path: `_ensure_postgres_columns` defined at `daemon/manager.py:7391` (called at :931); constants.py:344 makes the mirror MANDATORY for column additions to EXISTING tables; brand-new tables need NO mirror (`manager.py:797, 815-817, 829`; stored_watch_models.py:43-45; notify_audit :63-64).
- Every CREATE carries IF NOT EXISTS (MANDATORY) because create_all runs FIRST at boot and the runner applies immediately after — a bare CREATE would be silently swallowed (`20261025_000001_create_notify_audit.sql:69-76`).
- Semicolons only in full-line `--` comments (runner strips before statement split, runner.py:670-673 cited at :78-85).
- Recent migration examples to copy: `20261025_000001_create_notify_audit.sql`, `20261021_120000_create_instance_ops_audit.sql`, `20261011_000001_create_stored_watches.sql`.

**If our plan adds an audit table, exactly this must ship:**
1. SQLModel model with `__tablename__`, named indexes/unique constraints matching the .sql byte-for-byte (model = canonical PG source; stored_watch_models.py:83-86 precedent).
2. Own `daemon/migrations/versions/<YYYYMMDD_NNNNNN>_create_<table>.sql`: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS; full-line comments only where semicolons appear; -- DOWN section per house shape (see notify_audit / stored_watches).
3. Model import registered into metadata BEFORE create_all: late-import block in `daemon/api.py` (N1 required seam — "a missed import means no table on PG", notify_audit :58-62) + the explicit per-model `metadata.create_all` precedent at `api.py:961-967` (StoredWatch) when the manager-level create_all may predate the module import.
4. NO `_ensure_postgres_columns` entry (brand-new table — manager.py:815-817 precedent), UNLESS the plan instead ALTERs an existing table — then the column-ensure mirror in `manager.py::_ensure_postgres_columns` becomes MANDATORY (constants.py:344) and the paired .sql is documentation-only (20261011_102502 precedent).
5. Writer discipline: pick the declared tier — same-transaction (Tier-1) if a single transaction exists, else write-ahead pending→committed (Tier-2), and DECLARE it (instance_ops_audit :28-48).

---

## 5. CONFIG KNOB TEMPLATE

**Strand-heal running-zombie (ServicesConfig Fields, SERVICES_ env auto-prefix):** `daemon/config.py:2645-2695`
- `mission_strand_heal_running_zombie_enabled: bool = Field(default=True, ...)` env `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_ENABLED` (:2645-2656); False ⇒ COMPLETE no-op (no candidate selection, no probe, no drive) (:2651-2653).
- `mission_strand_heal_running_zombie_age_bound_seconds: int = Field(default=7200, ge=1, ...)` (:2657-2679) — age keys on `task.completed_at`, NOT instances.last_activity_at (frozen-by-design for a running root, :2670-2673); floor 1s, FAIL FAST AT BOOT.
- `mission_strand_heal_running_zombie_batch_cap: int = Field(default=25, ge=1, ...)` (:2680-2695) — deliberately SMALL because the strand performs TERMINALIZING WRITES; tighter cap = smaller blast radius of a mis-predicate (:2684-2689).
- Default-True justification pattern: fail-CLOSED predicate + age bound + dev-staged promote-gated rollout ("the kill-switch exists for the operator, not as the default posture", :2640-2644).
- Wiring: direct read from `config.services.*` into the constructor at `daemon/api.py:1730-1744` (+ healer-side defensive coercion of numeric knobs, api.py:1726-1729; disable check logs at `mission_strand_healer.py:827`).

**Lane-6 stuck-wake (per-lane kill-switch, ENSEMBLE_ env):** `report_delivery_recovery_lane_stuck_wake: bool = Field(default=True, ...)` — `daemon/config.py:2098-2120`; env `ENSEMBLE_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE=0` restores pre-fix behavior; "Production should leave this True (the lane is the heal frontier…)". Consumed at `daemon/services/pool_orchestrator.py:392`.

**Orphan-F1 (env-only resolver, module-global cached — the LEGACY shape):** `ENSEMBLE_ORPHAN_F1_ENABLED` default ON, restart-to-flip — `daemon/services/job_recovery_service.py:115-158` (`_resolve_orphan_f1_enabled`, falsy-parse, cached `_ORPHAN_F1_ENABLED`), noted env-only at `config.py:2258` and `api.py:1467`.

**Template for a new sweep (synthesis from the three):** prefer the strand-heal shape — `ServicesConfig` bool Field(default=True) + age-bound int (ge=1) keyed on the discriminating timestamp + SMALL batch cap, all FAIL-FAST via pydantic, wired by direct config read at construction in api.py; kill-switch default-ON is acceptable when the predicate is fail-closed; the env-only module-global resolver (F1) is the older pattern, not the one to copy. Note the contrasting HARD POLICY: the F3 job-lock sweep is ALWAYS-ON with interval-only tuning, NO kill-switch (config.py:2437-2438) — pick the posture explicitly and record why.

---

## 6. RIDER R-10 — wedge-capture store-census defect (pg_class n_live_tup)

**Verified at base 22fe83317 (pre-existing, NOT fixed at this HEAD):**
- Query: `daemon/services/wedge_capture/db_capture.py:374-386` —
  `SELECT c.relname, c.reltuples::bigint, c.n_live_tup::bigint, pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = current_schema() AND c.relname = ANY(%s)` with `CHECKPOINT_TABLES = ("checkpoints","checkpoint_blobs","checkpoint_writes","checkpoint_migrations")` (:107).
- `n_live_tup` exists on `pg_stat_user_tables`, NOT on `pg_class` ⇒ on real PG the census ALWAYS fails `UndefinedColumn`; caught by the broad except at :388-390 ⇒ census = `{"status": "failed:UndefinedColumn"}` and (in the full snapshot path) the aborted transaction cascades `InFailedSqlTransaction` into the THIRD read.
- Independently disclosed in the db-pool lane's PG pack: `tests/postgres/test_dbpool_wedge_capture_pg.py:26-46` — "BORN-BROKEN on real PostgreSQL… measured on the scratch PG16: `column c.n_live_tup does not exist`… does NOT touch the census SQL — the born-broken census is reported… owner = the wedge-capture lane."

**What v0.27.13 F5 covered vs remains:** F5 fixed ONLY the `tasks`→`task` relation-name error in `_read_executing_turns` (test pack items (i)/(ii)/(iii), test file :14-24, plantable mutation :56-59). The store census defect REMAINS — confirmed present in source at this HEAD (:374-390).

**Minimal isolated fix (query shape):** keep `FROM pg_class c JOIN pg_namespace n …` and `pg_total_relation_size(c.oid)`; replace `c.n_live_tup::bigint` with `s.n_live_tup::bigint` from `LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid` (NULL-safe: existing consumer already handles None, :392-397). Correct-query precedent in-repo: `ens_db_tools.py:1223` (`s.n_live_tup AS approx_rows` from pg_stat_user_tables).

**Blast radius:** one function (`_read_store_census`); consumers = census dict keys `checkpoints_rows / checkpoint_blobs_rows / checkpoint_blobs_bytes` (:398-400) and the wedge-capture snapshot JSON. Behavior change: census goes from always-failed to populated on real PG (SQLite path unaffected — wedge capture is PG-only). The cascade-abort of read 3 disappears (strictly a fix).

**Natural test surface today:**
- `tests/postgres/test_dbpool_wedge_capture_pg.py` — real-PG pack; currently ASSERTS the failure mode is not the F5 cascade (:42-44); a fix must update its (ii) expectations and can now assert populated census values.
- `tests/unit/tools/test_dbpool_wedge_capture_static.py:100-137` — exact-string pins: `FROM pg_class c` must REMAIN (:102), pg_namespace join (:105), schema filter (:108), `pg_total_relation_size(c.oid)` (:111), `_read_store_census` own-function AST pin (:115-130), and exactly ONE `FROM pg_class c` + one `FROM task t` (:132-137). A LEFT-JOIN-adding fix satisfies ALL of these pins unchanged; only a rewrite that drops pg_class would redden them.
- `tests/integration/test_wedge_capture_boot_smoke.py` (boot wiring), `tests/integration/test_dbpool_churn_census.py:39/:123` (one-shot psycopg.connect census of wedge capture itself).

---

## 7. RIDER R-9 — asyncpg adapter-pool idle-in-txn reap

**D-47 engine backstop (defined-but-disabled):**
- Flag: `MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED = False` — `daemon/repositories/factory.py:278`. Template `_manager_pg_session_options()` (:281-289) appends `-c idle_in_transaction_session_timeout=<MS>` (UTC clause FIRST, idle-in-txn SECOND) IFF the flag is True; while disabled the rendered options are EXACTLY the UTC clause (:263-266, :299-301).
- Constant: `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS = 60_000` — `daemon/constants.py:314`, with the full DISABLED rationale (:315-344): the bound's own rule (≥4× measured max legitimate hold, floor 30s) cannot be sized safely — measured max legit hold is model-injected 1059.877ms (4×=4.24s, floor dominates, coincides EXACTLY with the legitimate pool_timeout wait) and the SERVER TERMINATES the backend on this timeout (behavioural change with no measured safety margin).
- Boot log when disabled: `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no trustworthy Phase-0 measurement` (constants.py:318-320).
- **The one-parameter enablement = flip the flag in factory.py:278 to True** (constants.py:334-342: "the operative one-edit site… Editing the template alone is NOT enablement"). If EVER enabled, a `SET LOCAL statement_timeout = 0` carve-out inside `manager.py::_ensure_postgres_columns` becomes MANDATORY (constants.py:342-344; full-table rewrites under ACCESS EXCLUSIVE must not hit a 30s cap — also D-31/C-3 at factory.py:273-277).
- Enablement precondition: a TRUSTWORTHY measurement of the manager engine's legitimate hold on the REAL fetch path (booted daemon + real checkpoint store); the post-F8 re-measurement is the candidate figure (DR-11) (constants.py:329-335, factory.py:267-270).

**D-49 attribution (application_name per pool):**
- Manager engine: `MANAGER_ENGINE_APPLICATION_NAME = "ensemble-manager"` (constants.py:295), applied via the URL query dict (CONNINFO channel) at `factory.py:390`; deliberately NOT in the options string (would be overridden / would retune the repair engine, factory.py:292-298).
- Repair engine: `ensemble-repair` (dedicated engine, §2; attribution documented constants.py:300-303).
- Checkpoint adapter (asyncpg): `CHECKPOINT_ADAPTER_APPLICATION_NAME = "ensemble-checkpoint-adapter"` — `daemon/persistence.py:65`, delivered via asyncpg's OWN `server_settings` on a DSN built separately from the shared conn_string (:59-64, :441-462). Comment: "Attribution only — the adapter's idle-in-transaction HOLD is routed to the follow-up DB-pool commission (D-58)" (:63-64).

**Default-safe posture assessment:**
- The D-47 backstop, if enabled, covers ONLY the manager sync engine's connections (options string channel) — it does NOT reach the asyncpg checkpoint-adapter pool (server_settings channel, separate DSN) nor the repair engine (own connect_args already carry `idle_in_transaction_session_timeout=60s`, ens_db_tools.py:26).
- Reaping legitimate long transactions is a REAL hazard: the server KILLS the backend on this timeout (constants.py:325-327); the floor value (30s) coincides with legitimate pool_timeout waits; checkpoint saves / analytics / VACUUM jobs are the plausible victims (vacuum engine sets its OWN per-job `idle_in_transaction_session_timeout`, vacuum_engine.py:64-75, so it is fenced independently).
- For R-9 specifically (the ADAPTER pool reap): any parameter would ride the asyncpg server_settings DSN (persistence.py:441-462), sized ≥4× the measured legitimate adapter hold (checkpoint `aget`s measured 73-99s in the 2026-10-28 incident family — db_capture.py:407-409 — so a safe bound is minutes-scale, NOT 60s); same mandatory-measurement precondition applies. Default-safe posture: keep attribution-only (current state) until the follow-up DB-pool commission supplies the measurement; enabling at 60s on the adapter pool would reap legitimate checkpoint saves.

---

## 8. TEST SURFACE + HOUSE DISCIPLINE

**Where sweep/recovery pins live today:**
- `tests/job_queue/` — the dense home: `test_running_zombie_sweep.py` (strand-healer running-zombie pack, 2500+ lines; imports `mission_strand_healer` at :517, asserts greppable log lines `mission_strand_healer|running-zombie:` at :1158/:1201, config-knob wiring at :499+), `test_orphan_active_job_recovery.py`, `test_orphan_reaper.py`, `test_pattern_f_mission_live_gate.py`, `test_pattern_f_bus_pending_param_type.py`, `test_job_recovery_service.py`, `test_lock_repository.py`, `test_job_feedback_observer*.py`, `test_watcher_loss_terminal_strand*.py`, `test_retry_supersede_watch_recovery.py`, `test_f1_killswitch_tz_matrix.py`, `test_subtree_error_recovery_finalize_lane_pins.py`, `test_finalize_payload_unification_pins.py`, plus `test_claim_gate_sibling_deadlock_pins.py` (blueprint-cited).
- Real-PG packs: `tests/postgres/` (e.g. `test_dbpool_wedge_capture_pg.py`; conftest auto-applies the `postgres` marker; SERIAL-only under xdist).
- Static exact-string/AST pins: `tests/unit/tools/test_dbpool_wedge_capture_static.py` (§6).
- Wider: `tests/integration/test_wedge_capture_boot_smoke.py`, `tests/integration/test_boot_report_recovery.py`.

**PACKS.md convention:** `.agents/tester/PACKS.md` — newest-first sections per lane (line 3 = the 2026-10-30 preflight lane), pack tables with invocation/scope/run-anchor/status; conflicts keep-both newest-first (Repo & Dev Conventions; PACKS.md:1-30 shows the shape; the keep-both rule is exercised by every multi-lane merge in file history, e.g. v0.27.13 M2 "PACKS.md keep-both").

**net-new=∅ discipline precedents (full FAILED-list diff vs TRUE pre-fix base, same commands both sides):**
- `.agents/tester/RESULTS/2026-10-06-designer-od-lane-fix-final-gates.md` — "142-file/3,958-test symmetric A/B, 11 chunks ×2 states: net-new at HEAD = ∅ after test-only seam commit" (:18); raw delta root-causing workflow at :51-54; verdict summary :138 ("pre-existing red families byte-identical; 12 previously-red green").
- `.agents/tester/RESULTS/2026-10-29-dbpool-functional-gate.md` — the db-pool lane's functional gate (G3.5 net-new discipline execution: 11 net-new reds caught → fixed → re-verify net-new=0 with +11/-11 fingerprint, per the lane's staged record; the file is the worked example on this exact code area).
- Discipline codified in Repo & Dev Conventions [TESTING]: "Net-new-failure=full FAILED-list diff vs TRUE pre-fix base, same commands both sides; fixed=BASE_FAILED−FIX_FAILED empty→gate=net_new∅ AND previously-red green at HEAD; order/load-sensitive reds 3× isolated per side before classifying."

**Pinned-test conventions used by recent lanes:**
- Exact-string source pins (substring asserts over module text — static pin pack §6; PACKS.md rows cite "anchor-exact").
- Mutation-verified regression pins: plant→RED→GREEN per leg with digest/sha proof (PACKS.md:22 `preflight_mutation_matrix` "plant→RED→GREEN per leg; planted-sha discipline"; test_dbpool_wedge_capture_pg.py:56-59 "Plantable mutation" section).
- Count/collected-count fences on packs (PACKS.md:21 "469/0/1-skip (superset…)"; v0.27.14 record "4/4 collected-count fences").
- Greppable structured log-line assertions (test_running_zombie_sweep.py:1158).

---

## DISCREPANCIES vs commission brief

1. **"literal timestamps for idempotency" (Q2)** — no in-repo source text carries that phrase (`grep 'literal timestamp'` daemon-wide: no matches). It is the blueprint's phrasing of the operator practice; the in-repo equivalents are the idempotent `DO$$`-only rule + reconstructible `repair_log` (models.py:54-59) and F09 atomic single-DELETE rowcount semantics. Treated as blueprint-carried, not source-carried.
2. **`_ensure_postgres_columns` cited as `daemon/manager.py::_ensure_postgres_columns`** — correct; it is a method on the manager (def at manager.py:7391), not a module function. No discrepancy, clarified for the planner.
3. **Brief said the census reads "c.n_live_tup from pg_class"** — verified verbatim (db_capture.py:376-380). The R-10 record's "column exists only on pg_stat_user_tables" is corroborated in-repo by the db-pool PG pack (test_dbpool_wedge_capture_pg.py:30-34, measured UndefinedColumn on PG16). No discrepancy.
4. **Brief's Q3 said "quarantine/re-arm semantics from the usage-limit watch-loss fix"** — verified: the quarantine is the StoredWatch table + in-txn F6 re-key hook (stored_watch_models.py:7-11); GC statuses armed/released/rehydrated (:32-37). No discrepancy.
5. **`ENSEMBLE_ORPHAN_F1_ENABLED`** — present (job_recovery_service.py:115-158); it is an env-only module resolver, NOT a ServicesConfig Field, contrary to the impression the brief's knob-list grouping might give. Flagged in Q5.
6. **Strand-healer "journaling" (Q4 brief wording)** — the healer keeps NO journal table; its observability = structured log lines + counters + the completion chain's own durable writes (Q4). If the planner expected a DB journal precedent there, notify_audit / instance_ops_audit / repair_log / maintenance_runs are the real table precedents.
