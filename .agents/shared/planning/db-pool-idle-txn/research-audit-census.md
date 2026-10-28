# Research: DB borrow-site census + missed-finalize (wi-78967dba) evidence

**Lane:** fix/db-pool-idle-txn (job 634c38f2) · **Scope:** VERIFIED FACTS ONLY — no fixes decided, no plan written.
**HEAD verification:** branch `fix/db-pool-idle-txn` → `19fc11c8b52239813fd91327ee0c1961c5b6fc87` — VERIFIED at worktree
`/home/nea/ensemble-src-wt-dbpool-idletxn` via git metadata files (`.git` pointer → worktree HEAD → loose ref
`/home/nea/ensemble-src/.git/refs/heads/fix/db-pool-idle-txn`). Explorer has no shell; `git rev-parse` was reproduced
by reading the exact ref files git itself resolves. All code reads below are at this HEAD.

---

## PART 1 — Session/connection borrow-site census (method + results)

### 1a. Method (reproducible enumeration)

Python-only, `daemon/` scope, tests excluded (all searches rooted at `daemon/`, `*.py` only). The regex set,
as ripgrep one-liners:

```bash
rg -n "sessionmaker|async_sessionmaker"            daemon/                 # factory pattern (0 hits)
rg -n "def session_scope|session_scope\(|get_session\(" daemon/            # scope helpers
rg -n "with (SQLModel\.)?Session\(|WriteGuardSession\(Session\(" daemon/  # context-managed session borrows
rg -n "with SQLModelSession\("                      daemon/                # aliased CM borrows
rg -n "=\s*(SQLModel)?Session\("                    daemon/                # NON-context-managed assignments (the risk set)
rg -n "\.begin\(|engine\.connect\(|raw_connection\(|getconn\(|putconn\(|engine\.dispose" daemon/  # connection-level borrows
rg -n "with engine\.begin|with engine\.connect|async with .*pool\.acquire" daemon/          # CM connection borrows
rg -n "Session\(self|with Session\(|= Session\("    daemon/                # cross-check / alias sweep
```

Classification rule: CLEAN = borrow scope guaranteed to close on all paths (`with`-form, or `try/finally`
with `rollback()`+`close()` on every exit, or ownership-transferring CM proxy). RISK = borrow clean at
close-level but with a transaction-state / retention hazard on some path. LEAK = confirmed missing
close/rollback. N/A = comment/docstring/definition.

Session-factory finding: **no `sessionmaker` exists in daemon/** — every borrow is a direct
`Session(engine)` / `SQLModelSession(engine)` (sqlmodel) or engine-level `connect()/begin()`.
`manager._session_scope()` (manager.py:2774-2787) is the only scope helper.

### 1b. Counts (audited at HEAD)

| Family | Pattern | Sites | Classification |
|---|---|---:|---|
| A | `with Session(...)` / `WriteGuardSession(Session(...))` | 439 hits (≈435 code, 4 docstring N/A) | ALL CLEAN |
| B | `with SQLModelSession(...)` | 175 | ALL CLEAN |
| C | non-`with` assignments `= Session(...)` | **4** | ALL CLEAN (verified individually, below) |
| D | `with engine.begin()` / `with engine.connect()` | 60 hits | ALL CLEAN (0 non-`with` connection borrows) |
| E | asyncpg `pool.acquire()` (checkpointer pool) | 15, all `async with` | ALL CLEAN |
| F | raw psycopg / `raw_connection` / `getconn` / `putconn` | 0 | — |
| | **TOTAL audited** | **≈689** | **689 CLEAN / 0 LEAK at borrow-hygiene level** |

The 4 non-`with` session assignments, each verified:
1. `daemon/manager.py:2782` — `session = Session(self._engine)` + `try:` … `finally: session.close()` (manager `_session_scope`). CLEAN.
2. `daemon/repositories/auth_bootstrap/repository.py:93` — `consume()`: `try: …commit() except IntegrityError: rollback() finally: close()`. CLEAN.
3. `daemon/repositories/auth_bootstrap/repository.py:161` — `mint_with_sentinel()`: same try/rollback/finally-close shape (:188-210). CLEAN.
4. `daemon/services/child_reports.py:2594` — `owns_session` pattern: `if owns_session: session = Session(...)` … `finally: if owns_session: session.close()` (:2757-2759). CLEAN.

`WriteGuardSession` ownership verified: `__exit__` → `close()` → `self._session.close()` in try/finally
(daemon/write_pause_guard.py:361-395) — all 10 `with WriteGuardSession(Session(...))` sites close the inner session.

### 1c. RISK table (close-clean borrows with transaction-state / retention hazards)

| # | Site (file:line @HEAD) | Mechanism | In-scope (daemon request path)? |
|---|---|---|---|
| R1 | `daemon/tools/ens_db_tools.py:1479` and `:1491` | `_dry_run_sqlite_safe`: `session.execute(text("BEGIN"))` executes OUTSIDE the inner `try:` (`try:` at :1476/:1492). If the BEGIN itself raises, control jumps to the OUTER except (:1513) with NO rollback → the connection is left in a begun/aborted txn for the REST of the tool call. Matches the incident fingerprint "last query = BEGIN" ×2. Self-heals only when the caller's `with Session(repair_engine)` at :1067 exits. | YES (ens-db repair tool lane) |
| R2 | `daemon/services/readiness.py:1477-1478` (SELECT 1 probe), `:1752-1770` (queue-freshness MAX()+count probe), and the checkpoint-sentinel probe (same `_guarded` seam :1262-1290) | Documented abandoned-thread retention: probes run via `asyncio.wait_for(asyncio.to_thread(probe), timeout=…)`. On timeout the `to_thread` worker is NOT cancellable — the probe's `with engine.connect()` stays open and the PG backend sits `idle in transaction` until the query finishes. In-repo comment: "A timed-out to_thread leaves the underlying thread running until the driver's own connect/execute timeout releases it" (readiness.py:107-113). The MAIN engine sets NO `statement_timeout` / `idle_in_transaction_session_timeout` (factory.py:323-338), so the hold is unbounded driver-side. 10s refresh cadence ⇒ can accumulate. | YES (readiness runs continuously in the request-serving daemon) |
| R3 | `daemon/services/child_reports.py:2594` gate (`_gate_…` ephemeral session) and similar borrow-then-await shapes | Borrow is CLEAN (finally-close), but if any await inside the scope stalls (pool starvation), the held txn is idle-in-transaction for the stall duration — amplification surface, not a leak. | YES (observation only; no defect at HEAD) |

**LEAK count: 0.** No borrow site in `daemon/` misses close/rollback at HEAD.

### 1d. Named suspect dossiers (required regardless of classification)

- **`ens_db_tools.py` BEGIN-site** → R1 above. RISK. Note the REPAIR engine carries
  `idle_in_transaction_session_timeout` in connect_args (ens_db_tools.py:619-627) — the MAIN engine has none.
- **`/api/instances` list path** — route `routers/instances.py:371` → `manager.list_instances` (manager.py:14427) →
  `instance_lifecycle.list_instances` (:5021) → `SQLModelInstanceRepository.list` → `with SQLModelSession(self.engine)`
  (instance/repository.py, ~48 sites, all with-form). **CLOSE-CLEAN at HEAD.** The incident's "2 backends, last query =
  instances SELECT, idle in transaction" is most consistent with slow in-flight requests holding their single
  scope-txn during the BFS (one open txn per request) plus the R2 abandoned-probe connections starving the pool —
  the RCA's per-site attribution was fingerprint-based, and the soak-gate plan itself corrects the
  "leaks a transaction per tick" phrasing as NOT the confirmed mechanism (risk-safety-register.md:78).
- **`job_queue_items` count path** — `defer_block_resolver.py:274/:306` `with self._job_repo.engine.connect()`; readiness
  queue probe :1752 `with engine.connect()`. **CLOSE-CLEAN**; residual exposure is R2 (abandoned thread holds the
  connection only when the probe times out mid-query).
- **Mission mint path** — `mission_record_service` mint sites (`_mint_epoch_locked` :499 with own-session at :491;
  `mint_boot_epoch_if_none` :679 → `_mint_if_no_open_epoch` :641 → :664; all `with Session(self._engine) as own:`).
  **CLOSE-CLEAN at HEAD** — an IntegrityError (UniqueViolation on `ux_mission_records_anchor_open`, 4×/boot per the
  incident boot log) unwinds through the with-block → close → rollback. The UniqueViolation itself is a LOGIC defect
  (non-idempotent mint), explicitly non-goal #4 of the soak-gate lane (plan-overview.md:134).

### 1e. Pool facts (load-bearing for the fix)

- Canonical PG engine: `create_postgres_engine` (daemon/repositories/factory.py:255-339): `pool_size=5`,
  `max_overflow=10`, `pool_pre_ping=True`, `connect_args={"options": "-c timezone=UTC"}` ONLY — **no
  `statement_timeout`, no `idle_in_transaction_session_timeout`, no `pool_recycle`, no `reset_on_return`
  override** (SQLAlchemy default reset-on-return is 'rollback', which fires on `close()`, not on an
  abandoned thread's unchecked-out connection).
- Small pool + R2 retention ⇒ a handful of stuck probe threads can starve all 15 slots → 30s `pool_timeout`
  failures → the retry storm observed in incident-20261026.
- The checkpointer has a SEPARATE asyncpg pool (checkpoint_adapter.py) — its 15 acquire sites are all `async with`.

---

## PART 2 — Missed-finalize / zombie-ACTIVE coverage map + wi-78967dba evidence

### 2a. Sweep coverage map

| Sweeper | Cadence | What it covers | Covers missed-finalize zombie-ACTIVE JobItem? |
|---|---|---|---|
| `watch_reconcile_sweep` (daemon/services/watch_reconcile_sweep.py, 300s) | periodic | Re-evaluates HELD `mission_terminal` **watcher rows** (`job_watchers`), zombie-GC of rows whose job no longer resolves. Explicit: "The sweep does NOT touch the `admission_state` of any JobItem" (:109-110). | **NO** — watcher rows only |
| `orphan_watcher_sweep` (daemon/services/orphan_watcher_sweep.py, 90s) | periodic | Cancels PENDING **dependency-bus watchers** whose `source_task_id` is no longer an active task (:5-11). | **NO** — bus rows only |
| Drift-reconcile loop (daemon/api.py:~3660-3760, `SERVICES_DRIFT_RECONCILE_INTERVAL_SECONDS`) → `JobRecoveryService.reconcile_drift_states` (job_recovery_service.py:925) | periodic | Pattern f1: ACTIVE + **no Task** → DEAD (900s orphan-age grace + tree-activity guard, api.py:3707-3719). Pattern f2: ACTIVE + **terminal Task** → DONE (:1010). f1 leg E1-gated (`orphan_active_skipped_mission_live_f1`, :2666-2674); f2 leg E1-gated (`orphan_active_skipped_mission_live`, :2970-2999): `evaluate_mission_live` ⇒ if mission tree live → "finalize + notify skipped, watcher rows untouched". Backstop: fires iff a non-terminal tree member exists AND freshest activity older than `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS` (:2959-2969). | **PARTIAL** — only when the backing Task is terminal (f2) or absent (f1). A RUNNING-but-stalled Task matches NEITHER leg, and E1 holds any leg while the mission evaluates live. |
| Boot recovery (`recover_on_startup`, job_recovery_service.py:522; called at api.py:1395) | boot-only | Orphan finalize at startup (same f1/f2 shape). | Only across a restart. |
| P1 pending-watcher warning | periodic | "flags only PENDING>300s → ZERO warnings" for other shapes (blueprint + code predicate). | **NO** for RUNNING-stalled. |

**Precise gap statement:** a JobItem with `admission_state='active'` whose backing Task is non-terminal but
stalled (no progress) — the "RUNNING-zombie turn" shape — has **no finalize leg in any periodic sweeper**:
f1 requires no Task, f2 requires a terminal Task, P1 only counts PENDING watchers, and both watcher sweeps
are scoped to watcher/bus rows, never `job_queue_items.admission_state`. Even when f2's predicate WOULD match,
E1 holds it while `evaluate_mission_live` says live (a stalled non-terminal descendant makes the mission live —
self-holding). This is the standing gap recorded in the blueprint since 2026-10-14.

### 2b. What a "finalize write" is (exact sites)

1. **JobItem terminal transition (the canonical finalize write):** `JobQueueService._finalize_terminal`
   (daemon/services/job_queue_service.py:3016) — sole `AdmissionState` terminal writer boundary
   (active→done/dead + `terminal_reason` + scoped lock release in finally).
2. **Observer 5-step terminal cascade:** `JobFeedbackObserver._finalize_job_db_sync`
   (daemon/services/job_feedback_observer.py:3664) — ONE `WriteGuardSession` transaction: (1) JobItem
   PROCESSING→COMPLETED/FAILED in-session UPDATE (WHERE `admission_state == 'active'`), (2) instance status
   UPDATE, (3) `job_locks` release DELETE; "If any step raises, none of them commit" (:3675-3702). Async wrapper
   `_finalize_job` :1620.
3. **Turn/instance completion:** `_finalize_instance_db_sync` (job_feedback_observer.py:3559) — instance.status
   → terminal + `updated_at`/`last_activity_at`/version bump + `session.commit()` at :3630-3634, inside
   `with WriteGuardSession(Session(...))` opened :3592. Child-completion path: `_process_child_completion_db_sync`
   (daemon/services/child_reports.py:3063).
4. **Mission terminal:** `MissionRecordService.resolve_or_mint` / epoch retirement writes
   (daemon/services/mission_record_service.py:571, :999-1020) → `mission_record/repository.py` stamp UPDATEs
   (all with-Session, 22 sites).
5. **Recovery-path finalize:** `_fail_orphaned_job` (job_recovery_service.py:697) routes through
   `_finalize_terminal` with `Decision.NO_RETRY`, `target_status="failed"`.

### 2c. Oct-27 explorer-turn wedge (wi-78967dba) — on-disk evidence

NOT independently recoverable from on-disk docs:
- `.agents/tester/RESULTS/` has **no 2026-10-27/28 files** (newest = 2026-10-26-v0278-staged-verification.md).
- `wi-78967dba`, "5h15", "five hours" — **zero hits** across `/home/nea/ensemble-src/.agents/**` and the
  worktree `.agents/**` (board items are ari-only and not on disk in readable form).
- The board item itself was NOT read (fence: board tools are ari-only).

What IS on disk (adjacent evidence):
- `incident-20261026-db-pool-idle-in-transaction.md` (main checkout `.agents/shared/`): 5 backends
  `idle in transaction`; journal verbatim: `Wedge-capture: store census failed: current transaction is aborted,
  commands ignored until rollback` + `resume-history read failed: current transaction is aborted` (:40-43);
  readiness degraded 45×/12min; relapse 0→6 idle-in-txn backends in ~3min after restart (:77-87); fix list
  (:109-128) names exactly the four leak suspects censused above.
- `analysis-20261026-v0278-soak-failure.md` (same dir): the Oct-26 promote soak rollback — checkpoint
  AsyncConnectionPool saturation under the boot/resume burst; degradation began 21s BEFORE the resumes;
  same binary served green ~90s later when the burst eased.
- `wedge-capture-tracking.md` (main checkout `.agents/approver/`, :32-50): operational note 2026-10-26 — during
  the pool incident the wedge-capture tool returned `current transaction is aborted` errors; ruling: aborted-txn
  or missing-relation errors are **inconclusive evidence, not evidence of absence** of a wedge.
- soak-gate planning dir (worktree): plan-overview.md:84-103 + requirements.md:1426 + risk-safety-register.md:78 —
  the pool leak is a CONSTRAINT ("safe in the presence of", non-goals #1-#6 at plan-overview.md:129-136); the
  grace window **refuses** the leak signature by design, and "the healthy-release rollback CAN RECUR for that
  signature until the leak is repaired".

How the Oct-27 wedge STARTED and ENDED: **not established from on-disk docs** (see open questions). What the
docs establish: the pool poisoning was OPEN and relapsing on Oct 26 (~3min re-formation under load), the
mechanism produces exactly the aborted-transaction errors that make fired finalize writes roll back whole
(single-txn cascade), and wedge-capture evidence from that window is ruled inconclusive.

### 2d. Verdict inputs (facts only)

**(a) Were the finalize-write failures pool-caused?**
- PROVEN: aborted-transaction errors occurred in the incident window and invalidated DB reads/writes
  (journal lines quoted in the incident doc; wedge-capture-tracking ruling).
- PROVEN: the finalize cascade is all-or-nothing — an aborted connection inside `_finalize_job_db_sync`'s
  single transaction rolls back the JobItem transition, the instance terminal write, AND the lock release
  together (job_feedback_observer.py:3700-3702), leaving `admission_state='active'` (missed finalize).
- PROVEN (mechanism, code-level): the leak class persists at HEAD via R2 (abandoned-thread retention, no
  driver/server timeout on the main engine) and R1 (BEGIN-outside-try).
- INFERRED: that the SPECIFIC Oct-27 5h15m explorer-turn wedge was triggered by a pool-caused finalize-write
  failure. Adjacent timing + mechanism match, but no on-disk artifact records the failing write, its SQLSTATE,
  or the recovery path for that specific turn. Per the wedge-capture ruling, absence of captured evidence in
  that window is inconclusive, not exculpatory.

**(b) With a healthy pool, would the sweeps have caught/recovered the wedge?**
- PROVEN (coverage analysis, code-cited): **No, for the RUNNING-stalled shape.** No periodic sweeper finalizes
  a JobItem whose backing Task is non-terminal-but-stalled: f1 needs no Task (job_recovery_service.py f1 leg),
  f2 needs a terminal Task (:1010), P1 counts only PENDING watchers, watch_reconcile/orphan_watcher touch only
  watcher/bus rows (watch_reconcile_sweep.py:109-110; orphan_watcher_sweep.py:5-11). The drift-reconcile
  backstop itself requires freshest tree activity older than `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS` AND
  `evaluate_mission_live` — a stalled RUNNING descendant keeps the mission "live" (self-holding E1).
- PROVEN: a healthy pool WOULD retire the aborted-transaction subclass of missed-finalize (a finalize that
  fires and errors mid-txn). The single-txn cascade design means pool health converts "rolled-back finalize"
  into "committed finalize" whenever the write actually fires.
- INFERRED: a healthy pool reduces but does not zero the trigger side either — R2 retention can still starve
  the pool under burst (pool saturation without aborted txns caused the Oct-26 soak rollback per the analysis doc),
  and any timeout window still delays finalize delivery.

**(c) Missed-finalize failure modes that REMAIN with a healthy pool:**
1. **RUNNING-stalled turn** (task alive, no progress) — no finalize leg at all (PROVEN, §2a). This is the
   wi-78967dba shape per the lane brief; the shipped `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_*`
   config surface (v0.26.4, AGE_BOUND_SECONDS=7200, guard 21600s horizon) addresses the strand/root shape —
   whether it covers every in-lane worker turn shape is NOT verifiable from the docs read here (OPEN).
2. **E1 mission-live hold** while any descendant is non-terminal — finalize deferred by design until the tree
   settles or the timeout backstop fires (PROVEN, :2959-2999).
3. **Daemon restart / kill mid-turn** — finalize never fires in-process; recovery is deferred to boot-time
   `recover_on_startup` (boot-only, api.py:1395) and checkpoint-resume semantics (PROVEN boot-only placement).
4. **Process kill between logical steps** — structurally mitigated: the 5-step cascade is ONE transaction
   (PROVEN, :3675-3702); remaining exposure is the gap between an upstream commit and the observer's async
   finalize invocation (logic-order gap, INFERRED).
5. **Logic gaps** — e.g., the mission-mint UniqueViolation 4×/boot (PROVEN it occurs; it is a logic defect,
   non-goal #4, not pool-caused) and the wedge-capture `tasks`→`task` table-name bug
   (wedge_capture/db_capture.py:308/:387, PROVEN in plan docs) which blinds diagnostics during incidents.

### Open questions for the plan owner

1. The Oct-27 wedge's primary evidence lives only in the board item (ari-only) + daemon journal — the plan
   needs either a journal pull or the board text to convert verdict-input (a) from INFERRED to PROVEN.
2. Whether `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_*` (enabled=True per v0.26.4 config surface) actually
   covers an in-lane explorer worker turn — reads of `mission_strand_healer.py` predicates were NOT done in
   this pass (out of census scope) and decide whether mode (c)-1 is already partially closed.
3. Whether the fix lane wants R2's driver-side bound (`connect_args` timeout family) as an in-lane item or a
   separate commission — the soak-gate lane explicitly reserved the leak sites for THIS lane (non-goals #1-#5
   all say "owner: follow-up, DB-transaction-discipline").
