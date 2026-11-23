# REPORT 1(a) — Watch Registration Storage: end-to-end trace

**Base sha:** `a1130e257a3c3bb3929455aec28cacafcbfaa1a7` (`docs/jwr-storage` worktree `/home/nea/ensemble-src-wt-jwr-storage`)
**Date:** 2026-11-23
**Scope:** READ-ONLY investigation. No code/config/migration edits. Every anchor below was re-derived at this base sha (prior-lane anchors drift — see §6 anchor-drift table).

---

## 0. TL;DR facts

1. **All watch registration state is DB-resident.** There is **no in-process watch registry** (no dict/set/manager-attr store). Every watch surface writes to the SQL table **`job_watchers`** via `JobWatcherRepository`. The only in-process artifacts are (a) the repository *handle* (`manager._watcher_repo`), (b) per-notify-call ephemeral buckets, and (c) module constants.
2. **`job_watchers` model** = `daemon/repositories/job_queue/watcher_models.py:65` (`class JobWatcher`), `__tablename__ = "job_watchers"` at `:71`. Its house-rule `.sql` migrations: `daemon/migrations/versions/20260619_000004_add_unique_job_watchers.sql` (unique index + pre-flight dedupe) and `daemon/migrations/versions/20260627_000002_drop_job_watchers_fk.sql` (SQLite table-rebuild dropping the `job_queue_items` FK; PG counterpart `daemon/manager.py:9690`). **No `CREATE TABLE job_watchers` migration exists** — the table is created by `SQLModel.metadata.create_all` (`daemon/api.py:1358`), and the runner is SQLite-only (house rule: every *new* SQLModel table needs its own `*.sql`; `job_watchers` predates that rule).
3. **`stored_watches` model** = `daemon/repositories/job_queue/stored_watch_models.py:68` (`class StoredWatch`), `__tablename__ = "stored_watches"` at `:71`. Its own migration: `daemon/migrations/versions/20261011_000001_create_stored_watches.sql` (house rule satisfied). PG side: `metadata.create_all` (`daemon/api.py:1371`) — brand-new table, no `_ensure_postgres_columns` mirror (migration header `:14-22`).
4. **The watch row is minted BEFORE the job is enqueued** in `job_create` — this is deliberate (TOCTOU close), not incidental.
5. **`stored_watches` covers exactly ONE lane**: the usage-limit deferral lane (`rearm_watchers=True`). Ordinary registration/claiming, the timeout lane, stale-recovery lanes, S15 compensation re-inserts, and zombie-GC retires are **all outside** it and remain unrecorded durably (matches `deferred-debt.md` §(a)/(b)).

---

## 1. `job_create` with `watch=True` — where/when the watch row is minted

**Tool:** `daemon/tools/job_queue.py:1477` (`async def job_create`), parameter `watch: bool = True` at `:1486` (default flipped ON by FP4-PP2).

**Sequence (all inside `create_job_tools`, `daemon/tools/job_queue.py:1410`):**

| Step | Anchor | What happens |
|---|---|---|
| 1 | `job_queue.py:1608` | `pre_generated_job_id = str(uuid.uuid4())` — the job_id is **pre-generated** so the watcher can be registered before dispatch. |
| 2 | `job_queue.py:1612` | Gate: `if watch and watcher_repo is not None and current_instance_id:` — all three must hold, else **no watch row is minted at all**. |
| 3 | `job_queue.py:1613-1617` | Cap check `count_watches_for_instance(current_instance_id) >= MAX_WATCHES_PER_INSTANCE` (50, constant at `job_queue.py:116`) → returns early with `_watch_cap_error(count) + ". Job was not created."` — **the job is NOT created**. |
| 4 | **`job_queue.py:1618`** | **`watcher_repo.add_watch(pre_generated_job_id, current_instance_id)` — THE MINT SITE.** The row is written to `job_watchers` *before* the job exists. |
| 5 | `job_queue.py:1620-1642` | `job_service.enqueue(..., job_id=pre_generated_job_id)` — the enqueue happens *after* the watch row exists. |
| 6 | `job_queue.py:1648-1654` | Idempotency-dedup repair: if `job_item.job_id != pre_generated_job_id` (dedup hit returned an existing job), the watch is **re-keyed** onto the real job id (`:1650` `add_watch(job_item.job_id, ...)`) and the stale pre-generated row is best-effort removed (`:1652` `remove_watch(pre_generated_job_id, ...)` — wrapped in `try/except: pass`, `:1653-1654`). |

**Why the ordering matters (comment at `:1604-1607`):** registering after `enqueue()` opens a TOCTOU window where a fast job could complete between `enqueue()` and `add_watch()`, so the watcher would miss the terminal `[JOB_EVENT]`. Pre-generating + pre-registering closes it.

**Default event set:** `add_watch(pre_generated_job_id, current_instance_id)` is called with **no `watch_events` argument** → the repository defaults to `list(ALL_WATCHABLE_EVENTS)` (`daemon/repositories/job_queue/watcher_repository.py:96-97`), i.e. terminal states + `in_progress` + the four mid-flight QA events (`watcher_models.py:34-36`). **`mission_terminal` is NOT in the default** — it is opt-in only (`watcher_models.py:38-62`).

**Note:** `job_create` is the only watch-family tool that registers a row **without** going through the `WorkResolver`. It keys directly on the pre-generated UUID because the work does not exist yet.

---

## 2. Tool surfaces — the ACTUAL storage each hits

**Storage for ALL five surfaces: the DB table `job_watchers`** (model `daemon/repositories/job_queue/watcher_models.py:65`; repo `daemon/repositories/job_queue/watcher_repository.py:30`).

| Tool | Anchor | Repo call | Storage hit | In-process state touched |
|---|---|---|---|---|
| `watch_job` | `job_queue.py:2702` (def), `:2720` `_strip_id` | **`watcher_repo.add_watch(record.work_id, current_instance_id, events)`** — two sites: `:2781` (already-terminal path, register-then-immediately-notify) and `:2869` (live path) | `job_watchers` INSERT..ON CONFLICT DO UPDATE (`watcher_repository.py:112-117`) | none (repo handle only) |
| `watch_jobs` | `job_queue.py:3025` (def) | **`add_watch(record.work_id, current_instance_id, events)`** — three sites: `:3114` (terminal + mission-held), `:3117` (terminal), `:3145` (live) | same | none |
| `watch_mission` | `job_queue.py:4383` (def) | **`add_watch`** — two sites: `:4472` (pre-dispatch receipt: `pre_dispatch_receipt` = `record.work_id`, set at `:4463`) and `:4541` (fan-out, one row per LIVE receipt, `receipt_work_id` from `Task.work_id` via `task_repo.get_by_instance`, `:4489-4490`) | same | none (a transient `live_receipts` list, `:4520`) |
| `unwatch_job` | `job_queue.py:2893` (def) | **`watcher_repo.remove_watch(job_id, current_instance_id)`** — `:2945` (receipt-keyed path); mission-handle fan-out: `:2927` (per-receipt `task_row.work_id`) and `:2933` (stranded mission-keyed row) | `job_watchers` DELETE WHERE job_id AND instance_id (`watcher_repository.py:137-144`) | none |
| `list_watched_jobs` | `job_queue.py:2956` (def) | **`watcher_repo.get_watches_for_instance(current_instance_id)`** — `:2972` | `job_watchers` SELECT WHERE instance_id (`watcher_repository.py:255-257`) | a transient `labeled` list (`:2984`) |

**Notes / verified facts:**
- **No in-process store.** There is no `dict`/`set`/manager-attribute holding watch registrations. The only manager attribute is the **repository handle**: `manager._watcher_repo = watcher_repo` (`daemon/api.py:1359-1360`; also `manager._stored_watch_repo` at `:1372-1373`). The repository holds an `Engine` (`watcher_repository.py:36-38`) — a DB handle, not a store.
- **Per-notify-call in-process buckets** (ephemeral, all derived from the DB row set, all in `daemon/services/work_notifier.py`): `watchers` (read at `:542-544`), `held_for_mission` counter (`:682`), `matching_readonly` list (`:888`), `matching_claimable` list (`:901`), `claimed` list (`:952`/`:954`), `notify_list` (`:1038`/`:1045`/`:1067`), `failed_claimed` list (`:1098`). None survive the call.
- **Event validation is fail-closed** on all three registering tools: unknown event names are rejected with the accepted set (`watch_job` `:2734-2745`, `watch_jobs` `:3048-3059`, `watch_mission` `:4411-4423`). `mission_terminal` is added to the accepted set explicitly (`watch_job` `:2737`, `watch_jobs` `:3051`, `watch_mission` `:4414`).
- **Cap:** `MAX_WATCHES_PER_INSTANCE = 50` (`job_queue.py:116`), enforced at `watch_job` `:2761-2763`, `watch_jobs` `:3062-3064`, `watch_mission` `:4467-4471` and `:4533-4537`, `job_create` `:1613-1617`.
- **`watch_mission` is NOT a mission-keyed row.** It is a resolver front-end + registration-time fan-out: the docstring at `:4310-4357` states explicitly "NOT a new watcher table and NOT a mission-keyed row"; the engine's notify path queries `WHERE job_id == work_id` only, so a mission-keyed row would never resolve (`:4328-4331`).
- **`watch_mission` default events = `["mission_terminal"]`** (`:4415-4417`), i.e. HOLD semantics — different from `watch_job`/`watch_jobs`, which default to the transport-only set.
- **No HTTP/REST watch-registration surface exists.** `daemon/routers/` contains no `watcher_repo` writer; the only router reference is `daemon/routers/answer_helper.py:447-454`, which *notifies* (`notify_work_watchers`, status `answer_received`) through `manager._watcher_repo` — read-only with respect to registration.
- **Wiring:** `watcher_repo` is an injected parameter of `create_job_tools` (`job_queue.py:1416`, doc `:1428`); `watch_mission` gets it via `create_mission_watch_tools` (`job_queue.py:4300-4306`) ← `create_mission_watch_tools_if_available` (`daemon/tools/instance.py:2772-2811`, `watcher_repo=getattr(manager, '_watcher_repo', None)` at `:2809`) ← appended at `daemon/tools/instance.py:7583-7584`.

---

## 3. `job_watchers` row lifecycle — add / claim / delete sites

### 3.1 ADD sites (row creation / re-creation)

| Site | Anchor | Note |
|---|---|---|
| Job creation | `daemon/tools/job_queue.py:1618` | pre-dispatch mint (see §1) |
| Job creation dedup repair | `daemon/tools/job_queue.py:1650` | re-key onto the real job id |
| Single watch | `daemon/tools/job_queue.py:2781`, `:2869` | `watch_job` |
| Bulk watch | `daemon/tools/job_queue.py:3114`, `:3117`, `:3145` | `watch_jobs` |
| Mission watch | `daemon/tools/job_queue.py:4472`, `:4541` | `watch_mission` |
| **S15 compensation re-insert** | `daemon/services/work_notifier.py:1215-1226` | per-row `watcher_repo.add_watch(job_id=watcher.job_id, instance_id=watcher.instance_id, watch_events=list(watcher.watch_events or []))` after the notify loop; restores a row whose enqueue threw. **`created_at` is re-stamped** (documented at `:1203-1214`). A failed compensation logs `S15: compensation UPSERT failed` and the row is **DROPPED** (`:1229-1241`). |
| **Stored-watch GC rehydrate** | `daemon/services/job_queue_service.py:1323-1328` | `add_watch(job_id=row.original_work_id, instance_id=row.watcher_instance_id, watch_events=["mission_terminal"])` — a **pure** `mission_terminal` row re-armed on the ORIGINAL work id |
| **In-txn F6 re-arm (events upgrade)** | `daemon/services/turn_transitions.py:912-922` | `UPDATE job_watchers SET watch_events = <json>` — appends `mission_terminal` to the surviving row (survival event set, depth 2) |

All ADD sites funnel through **`JobWatcherRepository.add_watch`** (`daemon/repositories/job_queue/watcher_repository.py:62`), a dialect-aware atomic UPSERT: `INSERT ... ON CONFLICT (job_id, instance_id) DO UPDATE SET watch_events = excluded.watch_events` (`:112-117`). The `watch_events` JSON column is **always full-replace** on both branches — there is no partial-append path in the repository (`:77-85`). `created_at` is preserved on the UPDATE branch.

### 3.2 CLAIM sites (the exactly-once CAS)

**The canonical claim-first CAS is in `daemon/services/work_notifier.py::notify_work_watchers` (def at `:316`).**

Ordering (docstring `:350-429`):
1. **Resolve FIRST** — `work_resolver.resolve_work(work_id)` at `:519-521`. If the work is gone → return 0, **watcher rows are left in place** for reconcile cleanup (`:522-528`). (This ordering matters: the pre-fix flow resolved *after* the delete, so a missing record silently dropped the watch.)
2. **Read-only fetch** — `watcher_repo.get_watchers_for_job(work_id)` at `:542-544` (a plain SELECT, **no DELETE**). If zero watchers and the status is terminal → the pinned `PP1 zero-watcher terminal fire` WARN (`:559-576`, once per work_id per process via `_pp1_zero_watcher_should_warn` at `:240`).
3. **Three-bucket partition** (per-watcher loop `:787-901`):
   - `matching_claimable` — every subscribed event fires NOW (`:901`)
   - `matching_readonly` — some but not all subscribed events fire NOW (`:888`) — delivered, **not claimed**, row survives
   - `held_for_mission` — pure `mission_terminal` row while the mission is still live (`:881`) — skipped, survives
   - The mission-live verdict is hoisted once per call (`:359-374`); a guard raise **fails CLOSED** (HOLD, no claim) — `:775-785`.
4. **CLAIM-FIRST (N1)** — for terminal statuses only:
   ```
   work_notifier.py:947   if _is_terminal(status):
   work_notifier.py:948-952       if not matching_claimable: claimed = []
   work_notifier.py:954-958       claimed = await asyncio.to_thread(
                                      watcher_repo.claim_watchers_for_job_for_instances,
                                      work_id, [w.instance_id for w in matching_claimable])
   ```
   → **`daemon/repositories/job_queue/watcher_repository.py:389`** (`claim_watchers_for_job_for_instances`), whose SQL is the `DELETE ... RETURNING` CAS:
   ```
   watcher_repository.py:441-452
       stmt = (sql_delete(JobWatcher)
                .where(JobWatcher.job_id == job_id)
                .where(JobWatcher.instance_id.in_(instance_ids))
                .returning(*JobWatcher.__table__.c))
       rows = db_session.exec(stmt).all(); db_session.commit()
       return [JobWatcher(**dict(row._mapping)) for row in rows]
   ```
   The per-instance WHERE clause is what preserves `held_for_mission` / `matching_readonly` rows (repo docstring `:405-410`). `.returning(*JobWatcher.__table__.c)` projects the table's columns — the earlier `.returning(JobWatcher)` form returned empty instances (docstring `:351-365`).
   Non-terminal statuses **never** claim (`:423-429`).
5. **Notify** only `claimed ∪ matching_readonly` (`:1038`/`:1045`/`:1067`).
6. **S15 compensation** after the loop (`:1215-1254`).

**Sibling claim primitive (not used by the notifier's terminal path):** `JobWatcherRepository.claim_watchers_for_job` (`watcher_repository.py:297`, DELETE..RETURNING at `:377-387`) — the all-watchers variant. Documented as non-atomic-w.r.t.-`get_watchers_for_job` for its sibling `remove_all_watches_for_job` (`:274-295`).

**Non-CAS delete helpers (fire-and-forget cleanup):** `remove_watch` (`:127`), `remove_all_watches_for_instance` (`:259`), `remove_all_watches_for_job` (`:274`).

### 3.3 DELETE sites

| Site | Anchor | Trigger |
|---|---|---|
| CAS claim (terminal, matching rows) | `watcher_repository.py:441-452` (via `:954-958`) | terminal delivery — **the only exactly-once delete** |
| Tool unwatch | `watcher_repository.py:137-144` (via `job_queue.py:2927`, `:2933`, `:2945`) | explicit agent unwatch |
| **F6 in-txn re-key** (UPDATE, not DELETE) | `daemon/services/turn_transitions.py:732-741` | `UPDATE job_watchers SET job_id = :child_work_id WHERE job_id = :parent_work_id` — **the row is RE-KEYED, not deleted**; inside the same transaction as the parent cancel + child INSERT |
| **Turn-mirror DELETE arm** | `daemon/repositories/task/repository.py:2607-2630+` | `DELETE FROM job_watchers WHERE job_id = :work_id AND NOT EXISTS (task row) AND NOT EXISTS (live-instance guard) AND <admission-state guard>` — FP2b (2026-10-03) guarded this arm with the alive-lineage / ACTIVE-job condition; the unguarded form was the amplifier for the dev-boot-on-prod wipe incident (`fb0cf25c`) |
| **Zombie-GC retire** | `daemon/services/job_queue_service.py:943-962` | `remove_all_watches_for_job(work_id)` when the work no longer resolves AND the mission is terminal/unresolvable; **fail-closed retire** if the guard itself raises (`:921-942`) |
| Legacy boot reconcile | `daemon/services/job_queue_service.py:698-749` (`_reconcile_terminal_watches_legacy`) | JobItem-only path, used only when no resolver is wired (`:551`) |
| Instance cascade delete | `daemon/repositories/instance/repository.py:3543-3548` | `DELETE FROM JobWatcher WHERE instance_id IN (...)` — real FK to `instances.instance_id`, must precede the instance row delete |
| Project delete | `daemon/repositories/project/repository.py:1497-1511` | two deletes: by job id in project, and by instance id in project |
| PG dedupe (migration path) | `daemon/manager.py:9394-9398` | `DELETE FROM job_watchers WHERE ctid NOT IN (SELECT max(ctid) ... GROUP BY job_id, instance_id)` — one-time dedupe before the PG unique index at `:9416-9419` |

---

## 4. `stored_watches` coverage map

**Model:** `daemon/repositories/job_queue/stored_watch_models.py:68` — table `stored_watches` (`:71`).
**Migration (house rule satisfied):** `daemon/migrations/versions/20261011_000001_create_stored_watches.sql` — `CREATE TABLE IF NOT EXISTS stored_watches` at `:34-45`, unique index `uq_stored_watches_original_work_watcher` at `:47-48` (first-store-wins fence), 4 named indexes `:49-56`, DOWN `:60-65`. PG path = `SQLModel.metadata.create_all` (`daemon/api.py:1371`), no `_ensure_postgres_columns` mirror (migration header `:14-22`; model docstring `:43-45`).
**Repository:** `daemon/repositories/job_queue/stored_watch_repository.py:41` — **no INSERT method exists by design** (`:16-20`: the write must share the retry transaction). Read/CAS only: `list_aged_armed` (`:48`), `transition_status` (`:81`, the `armed → to_status` CAS, `:94-108`), `get_by_original_work` (`:110`).

### 4.1 WHAT IT COVERS

| Covered | Anchor | Detail |
|---|---|---|
| **Usage-limit-lane rekeys only** | `daemon/services/worker_pool.py:1001-1013` | `task_repo.schedule_retry(..., bypass_retry_budget=True, **rearm_watchers=True**)` — the ONE caller that passes the flag (grep for `rearm_watchers` across `daemon/`: `worker_pool.py:1012` is the only `True`; everything else is the `False` default at `daemon/repositories/task/repository.py:5162`) |
| **In-txn quarantine + survival arming** | `daemon/services/turn_transitions.py:730-731` → `_rearm_watchers_in_txn` at `:799-926` | For each `job_watchers` row still keyed on the parent: (a) INSERT into `stored_watches` with `ON CONFLICT (original_work_id, watcher_instance_id) DO NOTHING` (`:880-906`); (b) `UPDATE job_watchers SET watch_events` to append `mission_terminal` (`:912-922`). Runs **inside the same transaction** as the parent cancel + child INSERT + the F6 re-key — "cancel → persist → re-arm is atomic by construction (daemon death = all-or-nothing)" (`:726-728`). |
| **Mission-root linkage** | `turn_transitions.py:769-797` (`_resolve_mission_instance_id`) | walks `instances.parent_id` up to the permanent-tree ROOT (hop-bounded at `_MISSION_WALK_MAX_HOPS = 64`, `:767`) — the GC's `evaluate_mission_live` anchor |
| **GC: `armed → released`** | `daemon/services/job_queue_service.py:1239-1255` (carrier fence) and `:1313-1319` (legitimate-consume) | leg 1: a `job_watchers` row still exists on `retry_work_id` for that watcher → the natural path owns coverage → CAS `armed → released`. leg 2: carrier gone BUT the retry resolves TERMINAL and the mission (evaluated from the stored `mission_instance_id` root) is ALSO terminal → the carrier was consumed at the true mission-terminal moment → CAS `armed → released`. |
| **GC: `armed → rehydrated`** | `daemon/services/job_queue_service.py:1321-1344` | loss case (retry never arrived / purged / carrier lost while mission or retry still live) → `add_watch(original_work_id, watcher_instance_id, ["mission_terminal"])` (pure mission-terminal carrier on the ORIGINAL work id) then CAS `armed → rehydrated`. UPSERT runs **before** the CAS (deliberate — `:1176-1180`). |
| **GC sweep cadence** | `daemon/services/watch_reconcile_sweep.py:292-306` | the stored-watch GC leg runs in the periodic sweep, in its **own** log-and-continue fence, AFTER the held-watch reconcile; aged scan uses `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS` (6 h) — `job_queue_service.py:1221` |

**Stored row shape** (`stored_watch_models.py:100-126`): `stored_watch_id` (PK), `original_work_id` / `retry_work_id` (the re-key pair), `watcher_instance_id` (FK `instances.instance_id`), `original_watch_events` (JSON — what the watcher ACTUALLY opted into, pre-arming), `mission_instance_id` + `work_instance_id` (both linkages), `status` (`armed` → `released` | `rehydrated`, constants `:63-65`), `created_at` / `updated_at` (naive-UTC convention, `:124-126`).

### 4.2 WHAT STAYS OUTSIDE (unrecorded durably)

| Outside | Anchor | Why it is outside |
|---|---|---|
| **Timeout lane** | `daemon/services/worker_pool.py:840-845` | `schedule_retry(...)` with **no** `rearm_watchers` → the flag defaults to `False` (`daemon/repositories/task/repository.py:5162`) → **no `stored_watches` row, no `mission_terminal` arming**; the F6 re-key still runs (bare re-key, `turn_transitions.py:732-741`) so the row *moves* to the child — but with the ORIGINAL transport-only event set, so the retry's own settle can CONSUME the row without the mission-terminal survival event. Matches `deferred-debt.md` §(b) (parked family). |
| **Stale-recovery lanes** | `daemon/services/stale_task_recovery.py:475`, `:583`, `:781`, `:865` | `force_cancel_and_schedule_retry` (`:5649`) and `schedule_retry` — neither passes `rearm_watchers`; `force_cancel_and_schedule_retry` builds `RetryTurn` WITHOUT the flag (`daemon/repositories/task/repository.py:5819-5826`) |
| **Ordinary registration** | `job_queue.py:1618`, `:2781`, `:2869`, `:3114`, `:3117`, `:3145`, `:4472`, `:4541` | no `stored_watches` write — `stored_watches` is a *recovery* table, not a registration log |
| **Ordinary claiming** | `work_notifier.py:954-958` → `watcher_repository.py:441-452` | the row is DELETE..RETURNING'd away — **no durable trace of what was consumed** |
| **S15 compensation re-inserts** | `work_notifier.py:1215-1226` | re-INSERT via `add_watch` only; `created_at` re-stamped; no `stored_watches` linkage; a failed compensation DROPS the row (`:1229-1241`) |
| **Zombie-GC retires** | `job_queue_service.py:943-962` | `remove_all_watches_for_job` — no `stored_watches` linkage |
| **Turn-mirror DELETE arm** | `daemon/repositories/task/repository.py:2607-2630+` | plain DELETE — no linkage |
| **Instance / project cascade** | `instance/repository.py:3546-3548`, `project/repository.py:1498-1511` | plain DELETEs — no linkage |

**Consistency check vs `deferred-debt.md` §(a)/(b):** the deferred-debt text says the quarantine table is "a **partial, lane-scoped** audit record ONLY for recovery-armed re-keys on the usage-limit deferral lane" and lists exactly the exclusions reproduced above (§(a) bullets at `.agents/shared/planning/watch-loss-fix-options/deferred-debt.md:11-27`; timeout-lane family §(b) at `:48-58`). **The source at this base sha agrees with the deferred-debt document — no drift found.** Note the deferred-debt cites `worker_pool.py:834-862` for the timeout lane; at this base the timeout `schedule_retry` call is at `worker_pool.py:840-845` (the cited range still contains it).

**Additional nuance found (not in the deferred-debt text):** the `stored_watches` row records `original_watch_events` — the events the watcher opted into — but the carrier row's *post-arming* event set (`mission_terminal` appended) is written only to `job_watchers.watch_events` (`turn_transitions.py:912-922`). There is no durable record of the ARMED event set, only of the ORIGINAL set. A reader of `stored_watches` alone cannot tell that the carrier was armed with `mission_terminal`.

---

## 5. POK `job-lookup-01` confirmation — the 8 `job_queue.py` sites keying `record.work_id` post-resolve

**POK row** (`job-lookup-01-canonical-id-seam`, audience `implementation`, `source_tier: agent`, `verify_count: 0`): *"After resolve_work normalizes a work id, follow-ups must key on record.work_id, never caller input. 8 job_queue.py sites: job_continue W3 guard, cancel/retry/delete/restore kind-checks, watch_job/watch_mission arming."*

**VERIFIED — with one correction of granularity.** The POK's "8" is a count of **TOOL FUNCTIONS**, not of code sites. At the code-site level there are **14** `record.work_id`-keyed follow-up sites in `daemon/tools/job_queue.py`, spread over exactly **8 functions**.

| # | Tool function | Resolve site (`get_work`) | `record.work_id` follow-up site(s) | Count |
|---|---|---|---|---|
| 1 | `job_cancel` (`:1904`) | `:1921` | `:1952` (`manager._task_repo.get_by_work_id, record.work_id` — task-kind re-lookup) and `:1977` (`job_service.cancel_job(record.work_id)` — job-kind) | 2 |
| 2 | `job_retry` (`:1988`) | `:1999` | `:2012` (`canonical_id = record.work_id if record is not None else job_id`) | 1 |
| 3 | `job_delete` (`:2024`) | `:2032` | `:2045` (`canonical_id = record.work_id …`) | 1 |
| 4 | `job_restore` (`:2057`) | `:2065` | `:2078` (`canonical_id = record.work_id …`) | 1 |
| 5 | `job_continue` (`:2089`) — W3 guard | `:2117` | `:2154` (`old_job = await job_service.get_job(record.work_id)`) | 1 |
| 6 | `watch_job` (`:2702`) | `:2752` | `:2781` (`add_watch(record.work_id, …)`, already-terminal arm) and `:2869` (`add_watch(record.work_id, …)`, live arm); plus `:2828` (`job_service.notify_watchers(record.work_id, …)`) | 3 |
| 7 | `watch_jobs` (`:3025`) | `:3088` (per jid, in the loop) | `:3114`, `:3117` (arming) and `:3145` (arming), plus `:3127` (`notify_watchers(record.work_id, …)`) | 4 |
| 8 | `watch_mission` (`:4383`) | `:4438` | `:4463` (`pre_dispatch_receipt = record.work_id` → armed at `:4472`) | 1 |
| | | | **TOTAL** | **14** |

**Corrections / gaps vs the POK body:**
- The POK's named enumeration lists 7 functions — **`watch_jobs` (bulk) is ABSENT** from the enumeration, yet it carries the **largest** count of `record.work_id` follow-ups (4). The "8" total is right; the enumeration is incomplete.
- The POK calls the cancel/retry/delete/restore sites "**kind-checks**". Precisely: the *kind-check* itself reads `record.kind` (`:1935`, `:2000`, `:2033`, `:2066`), and it is the **subsequent follow-up** that keys on `record.work_id`. `job_cancel` carries **two** such follow-ups (one per kind branch), which the POK's phrasing collapses to one.
- `watch_job`/`watch_jobs` have a **second** class of `record.work_id`-keyed follow-up the POK does not mention: the immediate-terminal **notify** calls (`:2828`, `:3127`), which are keyed on `record.work_id` exactly like the arming calls.
- `watch_mission`'s bulk fan-out (`:4541`) is keyed on `Task.work_id` (from `task_repo.get_by_instance`, `:4489-4490`) — **NOT** on `record.work_id`; only the pre-dispatch receipt path (`:4463`) is `record.work_id`-keyed. Consistent with the POK, which names `watch_mission` arming generally.
- **No violations found**: every follow-up in the table keys on `record.work_id`; the raw caller input is used only as a *fallback* when `record is None` (`:2012`, `:2045`, `:2078`) or as a display string in the reply text.
- `job_create` is correctly **excluded** — it registers on a **pre-generated** UUID (`:1608`, `:1618`), with no resolver involved.

---

## 6. Anchor drift vs the commission brief (re-anchored at base `a1130e257a`)

| Brief anchor | Actual at this base | Status |
|---|---|---|
| `daemon/tools/job_queue.py:2702` `watch_job` | `:2702` | ✅ exact |
| `daemon/tools/job_queue.py:3025` `watch_jobs` | `:3025` | ✅ exact |
| `daemon/tools/job_queue.py:4383` `watch_mission` | `:4383` | ✅ exact |
| `daemon/tools/job_queue.py:2893` `unwatch_job` | `:2893` | ✅ exact |
| `daemon/tools/job_queue.py:2956` `list_watched_jobs` | `:2956` | ✅ exact |
| `daemon/services/work_notifier.py:836-860` — "claim-first CAS DELETE..RETURNING" | `:836-860` is the **C1 partition comment block** (the four multi-kind retire shapes). The actual **CAS call** is `work_notifier.py:947-958`; the `DELETE ... RETURNING` SQL is `daemon/repositories/job_queue/watcher_repository.py:441-452` | ⚠️ **DRIFT** — brief points at the partition, not the claim |
| `daemon/services/turn_transitions.py:691-700` — "F6 in-txn re-key" | `:691-700` is the child-INSERT `insert_params` block. The **F6 re-key UPDATE** is `turn_transitions.py:732-741`; the in-txn hook call is `:730-731`; the hook body is `_rearm_watchers_in_txn` at `:799-926` | ⚠️ **DRIFT** — brief points at the child INSERT |
| `daemon/services/turn_transitions.py:691-700` (second reading, as the re-arm hook) | hook is at `:799-926` | ⚠️ **DRIFT** |
| `daemon/migrations/versions/20261011_000001_create_stored_watches.sql` | exact | ✅ |
| `daemon/repositories/job_queue/watcher_models.py:68` (blueprint) `stored_watch_models.py:68` | `:68` (`class StoredWatch`) | ✅ |
| `daemon/services/worker_pool.py:996` (blueprint: usage-limit retry) | `:1001-1013` (the `schedule_retry` call with `rearm_watchers=True` at `:1012`) | ⚠️ minor drift |
| `daemon/services/worker_pool.py:835` (blueprint: timeout lane) | `:840-845` | ⚠️ minor drift |
| `daemon/services/turn_transitions.py:799` (blueprint: `_rearm_watchers_in_txn`) | `:799` | ✅ exact |
| `daemon/services/work_notifier.py:531` (blueprint: PP1 zero-watcher WARN) | `:559-576` (the `_is_terminal` guard + WARN); `:531` is the "read-only fetch" comment | ⚠️ minor drift |
| `daemon/repositories/job_queue/watcher_models.py:34` (`ALL_WATCHABLE_EVENTS`) | `:34-36` | ✅ |
| `daemon/services/work_notifier.py:60` (blueprint: re-arm adds `mission_terminal`) | the append is at `daemon/services/turn_transitions.py:872-875` | ⚠️ minor drift |
| `daemon/tools/job_queue.py:4225` `watch_mission` (blueprint) | `:4383` | ⚠️ drift (blueprint-era anchor) |

---

## 7. Open questions / risks (NOT resolved by this trace)

1. **The timeout lane is the live silent-loss candidate.** `worker_pool.py:840-845` re-keys the watch to the retry child but does NOT arm `mission_terminal` and does NOT write a `stored_watches` row. The retry's own settle then consumes the row under the C1 rules with a transport-only event set — the shape `deferred-debt.md` §(b) describes. **NOT confirmed live** from this read-only trace (no runtime evidence gathered — fenced).
2. **No durable record of a consumed watch.** The claim CAS deletes the row; `stored_watches` records only *recovery-armed re-keys*. There is no table answering "who was watching what, and did they ever get the event". The only durable trace of a zero-watcher terminal fire is the `PP1 zero-watcher terminal fire` WARN line (`work_notifier.py:559-576`), which is LOG-only, not a row.
3. **No `stored_watches` row records the ARMED event set** — only `original_watch_events` (`stored_watch_models.py:115`). The armed set (`mission_terminal` appended) exists only on the carrier row.
4. **`stored_watch` GC is fail-open on the guard** → RELEASE (at-most-once) rather than rehydrate (`:1182-1188`, `:1300-1319`). A guard error on a terminal retry therefore releases the row without rehydrating — documented as deliberate (zombie-GC precedent), but it is a residual silent-loss direction.
5. **`stored_watches` has no FK to `job_watchers` / `task`** — only `watcher_instance_id → instances.instance_id`. Provenance to the carrier row is by `work_id` string only.
6. **The GC's "post-verdict carrier-existence re-check" hardening (deferred-debt §(e)) is NOT present** in the code at this base: the carrier-fence read happens once at `:1240-1247` and the rehydrate INSERT follows later; no second re-read before the INSERT. (Deferred-debt §(e) describes it as an optional future follow-up — consistent.)
7. **The GC's only trigger is the periodic sweep** — `reconcile_stored_watches` has exactly ONE caller in `daemon/`: `daemon/services/watch_reconcile_sweep.py:294`. The sweep is started in the API lifespan (`daemon/api.py:2183-2188`), interval default **300 s** (`daemon/config.py:2915-2916`, knob `SERVICES_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS`). **There is no boot-time stored-watch GC hook** — a daemon down for >6 h accumulates aged `armed` rows that are only drained on the first tick after boot. (Verified by grep: no other call site.)
8. **`job_watchers` has NO `CREATE TABLE` migration** — only the unique-index and FK-drop migrations. The table is created by `SQLModel.metadata.create_all` (`daemon/api.py:1358`) and, on PostgreSQL, by the model's `__table_args__`. If the house rule ("every new SQLModel table needs its own `*.sql`") is applied retroactively, `job_watchers` is a pre-existing exception. **Reported as fact; no code change proposed.**
9. **UNKNOWN / not verified:** whether the `job_watchers` row is ever migrated to a different key outside the F6 re-key (e.g. an instance-rename or a job_id migration lane). This trace grepped `UPDATE job_watchers` across `daemon/` — only one site (`turn_transitions.py:732-741`) plus the events-only UPDATE (`:912-922`). No other re-key found.
10. **Not verified (read-only scope):** live DB state, row counts, or whether any of these rows exist in production. No daemon boot, no pytest, no DB connection was performed.
