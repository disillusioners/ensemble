# Incident 2026-10-26: `/api/instances` at 5-8s — DB pool poisoned by leaked "idle in transaction" connections

- **Date:** 2026-10-26, ~22:10-22:50 UTC (diagnosed + stabilized by operator DevOps lane)
- **Severity:** S2 (API latency 5-8s + readiness red; no data loss, no external outage)
- **Status:** **MITIGATED, RELAPSED, OPEN.** Restart clears the pool but the leak re-forms in
  **~3 minutes under load**. Root cause (transaction hygiene) is the team's.
- **Audience:** ensemble dev team — the leak sites and the amplifier are yours
- **Version under test:** v0.27.8 (`ensemble/current/ensemble-prod`; daemon pid 4191398 pre-restart, 9752 post)

## What the user saw

`GET /api/instances?limit=10&offset=0&exclude_kb=true&include_descendants=false&order=activity`
returned in **~8s** from the browser (through `www.ensem.dev`). The UI felt "slow", and the
daemon reported **119% CPU** with `/readyz` showing **database / queue_freshness /
checkpoint_saver = false** ("degraded").

## The decomposition (what it is NOT)

| Path | Measured | Verdict |
|---|---|---|
| Through gateway + oauth2-proxy | **0.10s** floor | proxy ~85ms — NOT the cause |
| Direct to daemon `127.0.0.1:9797` | **4.99s -> 1.59s -> 0.64s** | the whole problem is here |
| Postgres itself | `SELECT 1` median **0.66-0.75ms**, p95 1.47ms; `count(*) instances` 1-3ms; 23 backends / **1 active** | DB innocent |
| pg_stat_activity census | **5 backends `idle in transaction`**, open backends **16** | <- smoking gun |

Postgres was idle and sub-millisecond the whole time. The latency was **entirely daemon-side**.

## The mechanism (confirmed by evidence)

**Leaked transactions poison the connection pool; the pool then starves the daemon's own probes.**

1. Several code paths open a transaction and return the connection to the pool **without
   `commit()`/`rollback()`** -> the backend sits in `idle in transaction`, holding a snapshot
   (blocks vacuum) and, if any statement errored on it, in the **aborted** state.
2. SQLAlchemy **autobegin**: a bare `session.execute(select(...))` issues `BEGIN` implicitly,
   so *any* read path that never commits leaks a transaction.
3. An **aborted** connection handed back out makes every subsequent statement fail with
   `current transaction is aborted, commands ignored until rollback` — visible verbatim in
   the journal:
   ```
   Wedge-capture: store census failed: current transaction is aborted, commands ignored until ...
   Wedge-capture: resume-history read failed: current transaction is aborted, commands ignored ...
   ```
4. Pool pressure -> the daemon's own readiness probes starve -> `/readyz` goes red
   (`[Readiness] degraded: queue_freshness: queue probe timed out; checkpoint_saver: sentinel c...`
   **45x in 12 min**) -> the event loop burns CPU retrying (**119%**) -> API latency 5-8s ->
   the FE starts seeing `503` on `/api/prompts/pending-count` (10x in 12 min).
5. **The amplifier:** the `queue_freshness` readiness probe itself leaks a transaction *per tick*
   (`SELECT count(*) FROM job_queue_items ...`). So the failure is self-sustaining —
   probe leaks -> pool pressure -> probe times out -> retry -> more leaks.

## Where the leaks are (4 candidate sites)

`pg_stat_activity` last-query fingerprints (5 stuck backends, open backends 16):

| Last query | Count | Site |
|---|---|---|
| `BEGIN` | 2 | `daemon/tools/ens_db_tools.py:1479` and `:1491` — the explicit `BEGIN` executes **outside** the `try`, so a failure there skips the `session.rollback()` -> db-tool / wedge-capture path |
| `SELECT instances.instance_id, instances.project_id ...` | 2 | the **`/api/instances` list handler** — ORM read path, autobegin without commit/rollback |
| `SELECT count(*) FROM job_queue_items ...` | 1 | the **readiness `queue_freshness` probe** |
| `INSERT ... mission_records` (UniqueViolation) | 4/boot | **candidate** — `mission_record_service.mint_boot_epoch_if_none` raises `IntegrityError: duplicate key value violates unique constraint "ux_mission_records_anchor_open"` **4x per boot** (once per actor). An exception inside a transaction without a rollback leaks that connection. Verify rollback hygiene in this path. |

Separate (not pool-related) bug spotted in the same log window:
`Wedge-capture: executing-turns read failed: relation "tasks" does not exist` — the table is
`task`, not `tasks`; the wedge-capture queries need the correct name.

## The mitigation (operator lane) and the measured relapse

```
systemctl restart ensemble-main.service      # clears the poisoned pool
```

Immediately after the restart: same endpoint **0.067-0.23s** (0.079s final check), `/readyz`
**all green** (database / queue_freshness / services / checkpoint_saver), CPU **119% -> 49-55%**,
open backends **16 -> 8-9**, `idle in transaction` **0**.

**Then it relapsed — measured, ~3 minutes later (after a second restart at 22:37:57 by another lane):**

| Metric | Right after restart | ~3 min later (under load) |
|---|---|---|
| `idle in transaction` backends | **0** | **6** |
| open backends | 8-9 | **18** |
| `/api/instances` direct | **0.079s** | **0.45 / 0.89 / 0.51s** |
| daemon CPU | ~50% | **104%** (pid 9752) |
| readiness | green | flapping (`database: SELECT 1 probe failed or timed out`, `checkpoint_saver: sentinel checkpoint read timed out after 1.0s`) |

So a restart buys **minutes, not hours**. The leak rate scales with DB traffic.

## The feedback loop (why it keeps coming back)

The auto-continue boot pass resumed **4 instances** — `f17ccff0`, `a8577346`, `644df4de`,
`4649fe43` — with **29 `[RESUME]` lines in 3 minutes** (5 per instance). That burst:

```
auto-resume burst (4 instances, 29 resumes/3min)
   -> heavy DB traffic (checkpoints, mission_records, job queue)
   -> each leaky read path leaks a transaction
   -> idle-in-txn 0 -> 6, pool pressure
   -> readiness probes starve, event loop retries at 100% CPU
   -> more DB traffic + retries  ==  more leaks
   -> endpoint 0.08s -> 0.89s, readiness red
```

This is the **same surface symptom** as the 2026-10-22 event-loop CPU-peg incident, but a
different mechanism — here the loop is busy **retrying around a starved pool**, and CPU drops
to ~50% the moment the pool is clean. Reducing load (kill-switch / pausing the resume set)
lowers the leak rate; it does not fix it.

## Fix list for the team

1. **Transaction hygiene (root cause).** Every session path must `rollback()`/`close()` in a
   `finally`. Audit at minimum: the instances-list handler, the `job_queue_items` count path,
   the `mission_record_service.mint_boot_epoch_if_none` error path, and any repo method that
   reads without committing.
2. **Pool hardening.** `pool_pre_ping=True`, `reset_on_return='rollback'`, and a sane
   `pool_recycle` — an aborted connection must never be handed out.
3. **DB tool.** Move `session.execute(text("BEGIN"))` **inside** the `try` (or drop it —
   SQLAlchemy already autobegins), so the `rollback()` is guaranteed on every path.
4. **The readiness probe must not leak.** It currently amplifies any leak into a full
   readiness outage — it should run on a dedicated short-lived connection or explicitly
   commit/rollback.
5. **wedge-capture queries:** `tasks` -> `task`.
6. **`mission_records` UniqueViolation.** Every boot raises it 4x; either make the mint
   idempotent (upsert / `ON CONFLICT DO NOTHING`) or guarantee the transaction is rolled back.
7. **Regression guard.** Add a test asserting no connection is returned to the pool with an
   open transaction (e.g. assert `pg_stat_activity` has zero `idle in transaction` after a
   read-only request cycle), plus a load test that asserts the count stays 0 after N minutes
   of instance churn.

## Operator notes (this session)

- Auto-continue was **ON** (`auto_continue_raw_env='1'`, key
  `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` in the install `.env`), so each boot resumes
  the 4-instance set above and re-feeds the loop.
- The daemon was restarted at 22:37:57 by **another lane**, not by the operator lane — the
  two lanes can collide on restarts; coordinate before restarting.
- The Oct 22 cycle-breaking procedure (kill-switch + pause the resume set + clean restart)
  lowers the leak rate but is mitigation only.

## Evidence pointers

- `journalctl -u ensemble-main.service --since "-12 min"` -> 45x `[Readiness] degraded ...`
  and the `Wedge-capture: ... current transaction is aborted` lines
- `pg_stat_activity` census on `ensemble_prod` -> 5 x `idle in transaction` (fingerprint table
  above); post-restart 0, then 6 again within ~3 min
- `curl -w "%{time_total}"` direct vs gateway timing (tables above)
- `ps -eo pcpu` -> 119% (pre-restart) / 104% (relapse), single-core peg
- Boot log: `AutoContinue boot pass: ContinueResult(candidates=4, scheduled=4, ...)` and
  `mission_records: mint_boot_epoch_if_none failed ... UniqueViolation` x4

## Related / open items

- `.agents/approver/wedge-capture-tracking.md` — the wedge-capture failures logged during
  this window were **consequences** of the poisoned pool (aborted connections) plus the
  wrong table name; cross-linked there.
- The 2026-10-22 event-loop CPU-peg incident shares the *symptom* (CPU peg + readiness red)
  but not the mechanism; do not conflate.
- Python `asyncio` + `psycopg` note: a leaked transaction also holds a snapshot, which
  contributes to the observed **42% rollback ratio** / WAL churn on this cluster.

---

## FIXES LANDED — db-pool idle-txn lane (`fix/db-pool-idle-txn`, 2026-10-29)

**Status: the transaction-hygiene fixes for this incident are landed on the lane branch and
independently gated — they are NOT promoted.** The live daemon still runs the pre-fix tree
(`current -> releases/v0.27.12`, `livez` 0.27.12, read-only GET 2026-10-29). The incident above
therefore stays **OPEN** as a symptom class: §5 lists the original RCA claims the measurement
**refuted**, and §6 states exactly what this lane does **not** close.

Landmark: branch `fix/db-pool-idle-txn`, base `19fc11c8b` (the staged v0.27.11 tree), **59
commits**, **11 daemon files** (+1096/−207), **0 new `.sql`** (`git diff --stat
19fc11c8b..441a1657d -- daemon/`). Plan of record: `.agents/shared/planning/db-pool-idle-txn/`.

### 1. WHAT LANDED, per file

| File | What landed |
|---|---|
| `daemon/manager.py` | **F8** — the deferred-report reconcile split into **P0/P1/P2** in both twins (`_reconcile_deferred_report` :10809, `_reconcile_deferred_report_async` :11609). P0 = the read-decide scope (`_session_scope()` at :10874 / :11682) which **closes before any await**; P1 = the content fetch with **no manager-engine transaction open** (sync :10966, async :11780 — the async fetch is now **bounded** by `asyncio.wait_for` at :11780, previously unbounded); P2 = a **fresh** write scope (:10971 / :11795) that **re-reads** the injection row and **re-asserts** the terminal + dead-parent guards, so a concurrent escalation between P0 and P2 can no longer double-deliver. The carrier flag is consumed **strictly** at the (c) branch — `live_carrier_exists is False` (**:11226** sync / **:12049** async at HEAD `441a1657d`; the code-review's recorded :11212/:11999 were the line numbers at its review HEAD `a393ed87d`, `code-review.md:88`) ⇒ an UNKNOWN flag can never trigger a second carrier. |
| `daemon/tools/instance.py` | **F8.5** — the `await _bus.watch(...)` registration (:1371) moved **out** of the `WriteGuardSession` scope (:1207); the manager-engine transaction is no longer held across that await. |
| `daemon/tools/ens_db_tools.py` | **F3** — the explicit `session.execute(text("BEGIN"))` **deleted in both branches** (the dry-run DDL and the DML wrapper); `grep 'text("BEGIN")' daemon/` → **0** at HEAD. Rollback is **unconditional in a `finally`** (:1089, :1520, :1544) ⇒ no path returns a connection with an open transaction. The repair engine also gained its **own** `connect_args` (the shared UTC options string stays byte-identical) and an explicit `pool_reset_on_return="rollback"` (:625). |
| `daemon/services/readiness.py` | **F4-A** — the admission pre-check `_refuse_pool_admission_at_capacity(engine)` (:1488) fires **immediately before** `engine.connect()` in both probes (:1559, :1861) ⇒ a starved probe is refused synchronously instead of queueing as a waiter. Its **shape-guards** (tolerance for non-QueuePool engines) landed last, after the tester's gate caught the unguarded `pool.checkedout()` crash on `StaticPool`/`FakeEngine` (see §7). **F4-B** — the probe's statement is bounded by `SET LOCAL statement_timeout` (:1576, :1878) + `conn.rollback()`. The probe docstring was corrected (it had claimed a driver-level timeout the engine never set). |
| `daemon/repositories/factory.py` | **F2** — the manager engine's knobs are now **explicit** (`pool_pre_ping=True`, `pool_reset_on_return="rollback"` :407, `pool_recycle=3600` :408, `pool_timeout=30` :409; **sizing 5+10 unchanged**, :394-395), attribution rides the **conninfo** channel (`application_name` in the URL query, :371), the engine binds its **own** `connect_args` template (the shared `PG_SESSION_CONNECT_ARGS` stays byte-identical for the repair engine), and the engine-level idle-in-txn **backstop is DEFINED BUT DISABLED** (:277) with a boot log line that says so (:383-389). |
| `daemon/constants.py` | The **Δ3 constants** (`MANAGER_ENGINE_APPLICATION_NAME` :246, `MANAGER_ENGINE_POOL_RECYCLE_S` :255, `MANAGER_ENGINE_POOL_TIMEOUT_S` :259, `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` :265) plus the **honesty blocks** (:280-300 — the enablement condition and the "edit the template AND the flag, or the boot log lies" rule; :345-353 — the residence-budget constant and its derivation note). |
| `daemon/persistence.py` | The asyncpg checkpoint-adapter pool now carries its **own** DSN + `server_settings` attribution (:390-396) — 100% of its `application_name` comes from asyncpg's own channel; the shared `conn_string` is byte-unchanged. |
| `daemon/repositories/mission_record/repository.py` + `daemon/services/mission_record_service.py` | **F6** — the boot mint is **idempotent**: `INSERT ... ON CONFLICT DO NOTHING` with the partial-index predicate `status='open'` + a `.returning(mission_id)` loser signal (repository :580-584). A conflict raises a **dedicated** `MissionEpochConflictError` (:45, :617); a **non-conflict** `IntegrityError` **propagates** (the service catches only the conflict type, :557) instead of being swallowed into a re-resolve. |
| `daemon/services/wedge_capture/db_capture.py` | **F5** — `FROM tasks` → `FROM task` (the relation error that aborted the connection and produced the journal's "current transaction is aborted" cascade). |
| `daemon/routers/queues.py` | **F9.1** — a **comment-only** correction of the stale minimising comment at the routed event-loop site (:653-654). Zero non-comment delta lines; AST-identical (`code-review.md:92`). |

### 2. THE TRANSACTION-HYGIENE STORY

Four paths could hold a transaction open. Each is now guaranteed closed:

1. **F8 — borrow-then-await, three sites** (`final-implementation-spec.md` §3.2): the async
   reconcile's session scope held the content fetch and the carrier check; the sync twin's scope
   held the `run_coroutine_threadsafe(...).result(timeout=8.0)` bridge plus a nested session; the
   instance tool's `WriteGuardSession` held `await _bus.watch(...)`.
   **NOW:** no manager-engine transaction is open across an await. P0 reads and closes, P1 fetches
   with no session open, P2 opens a fresh scope that re-reads the row and re-asserts both guards.
   The async twin's hold is **bounded** at 8.0 s. **Write atomicity is preserved** — the message
   INSERT + task INSERT + injection UPDATE still commit in **one** transaction.
2. **F3 — the explicit `BEGIN` outside the `try`** (`daemon/tools/ens_db_tools.py:1479`/`:1491` at
   base): the `BEGIN` executed *before* the try, so a failure there skipped the rollback and left
   the transaction open for the rest of the tool call.
   **NOW:** the explicit `BEGIN` is **gone** (SQLAlchemy autobegins) and the rollback is
   **unconditional in a `finally`** ⇒ no path returns a connection with an open transaction.
3. **F6 — the mint exception path** (`daemon/services/mission_record_service.py:544-567` at base):
   the `UniqueViolation` handler's re-resolve probe is **skipped** on the boot path
   (`founding_message_id=None`), so the error re-raised and the caller's session was left poisoned.
   **NOW:** the mint is an idempotent upsert (the tester's 8-way no-barrier race: 7 `UniqueViolation`
   at base → **0 exceptions, 8/8 same mission_id, exactly 1 winner row**) and a non-conflict
   `IntegrityError` propagates instead of being absorbed. (The **caller-supplied-session** path is
   unchanged by design — the lane's upsert does not rewrite a caller's session semantics.)
4. **The probe timeout path** (`daemon/services/readiness.py`, `asyncio.wait_for(budget)` around a
   synchronous DBAPI call in a worker thread): on timeout the thread is abandoned.
   **NOW:** **F4-A** refuses admission at capacity *before* `engine.connect()` (no stranded waiter
   under saturation), **F4-B** bounds the statement with `SET LOCAL statement_timeout` and rolls
   back, and the probe's connection is returned by the `with engine.connect()` scope — **never
   stranded**.

**Measured, post-fix** (the tester's functional gate — §7):

| Measure | Base | Head | Artifact |
|---|---|---|---|
| Manager-engine hold, invariant to fetch duration (0.05→2.0 s fetch, n=20/leg × 7 legs) | hold ≡ fetch latency (Phase-0: 1.03 s median, ≥8 s sync twin) | P0 median **4.8–5.7 ms**, P2 median **14.0–17.0 ms**, worst **0.021607 s** (p95 0.019867 s) | tester J2 |
| Idle-in-txn samples during the borrow-then-await window | **39/39** | **0/39** (max xact_age 2.0078 s → null) | tester J5(a) |
| Reproduction guard — both incident shapes, 53 rounds | **53/53** rounds show the shapes (iit 162 + iit-aborted 106 samples) | **in-txn 0/53**, aborted 0, max xact_age **0.0092 s** (= 18.4% of the guard-cycle budget) | tester J5(e) |

### 3. THE POOL-HARDENING KNOBS + values

| Knob | Value | Where | Note |
|---|---|---|---|
| `pool_pre_ping` | `True` | manager engine + repair engine | **already on at base** — pinned explicitly, not newly added |
| `pool_reset_on_return` | `"rollback"` | manager engine `factory.py:407`; repair engine `ens_db_tools.py:625` | pins "an aborted connection is never handed out"; the library default already applied it (see §5) — an implicit default is not a pinned invariant |
| `pool_recycle` | `3600` | `MANAGER_ENGINE_POOL_RECYCLE_S` (`constants.py:255`) | |
| `pool_timeout` | `30` | `MANAGER_ENGINE_POOL_TIMEOUT_S` (`constants.py:259`) | the SQLAlchemy library default, pinned explicitly so it cannot drift (D-15); **deliberately not lowered** — lowering it "converts ordinary manager-pool contention into TimeoutError for every daemon caller (500s under exactly the incident's conditions)" (`constants.py:260-264`). **Residual, stated in the shipped comment:** F4-A's pre-check leaves a window — "a checkout that loses the pre-check race may still strand a probe thread for up to `MANAGER_ENGINE_POOL_TIMEOUT_S` (30 s)" (`constants.py:324-326`) |
| `application_name` (manager) | `ensemble-manager` | conninfo query dict, `factory.py:371` | the **conninfo** channel, not a `-c` in a shared options string — measured to win over `options=-c` |
| `application_name` (asyncpg adapter) | its own `server_settings` | `persistence.py:395-396` | 100% of the attribution; the shared `conn_string` is byte-unchanged |
| engine-level idle-in-txn backstop | **DEFINED BUT DISABLED** | `factory.py:277` (`MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED = False`) | one flag gates both the rendered clause and the boot log line (:383-389) — editing one without the other makes the boot log lie. Enablement is a **one-edit** ceremony conditioned on a **trustworthy post-F8 measurement** (D-47; `constants.py:280-300`). Not enabled by this lane. |
| pool sizing | `pool_size=5`, `max_overflow=10` (**unchanged**) | `factory.py:394-395` | the soak lane's A4 sizing lives on the P2 pools, not this engine |

**One honest wrinkle on Δ3.** The residence-budget constant
`MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S` ships in the lane tree as the **sentinel `-1.0`**
(`constants.py:353`) — "no trustworthy measurement yet", which scopes the F7 residence assertion
out. The **measured** value `0.25 s` (worst 0.021607 s × 10, rounded up) landed on the **tester's**
branch (commit `8cc538a71`), **not** in the lane tree. The same is true of the tester's
`expected-collected.md` count fill (35/35 cells, commit `47ff2f5ff`): it lives on the tester branch
and has not been merged into the lane tree, where the cells still read `TBD (R-1)`.

### 4. WHAT THE PINS PROVE

- **Inventory:** **46 pins** across **26 pack files** (+1 process-gate row), and a **17-member**
  gate ladder — the contract's §5 is the ONE authority (`final-implementation-spec.md:376`, `:471`;
  `FOLD-MAP.md:140`; set-equal across four artifacts).
- **The ladder fails closed in both directions, proven live:**
  - a `TBD (R-1)` count cell ⇒ **FATAL, exit 1, no pytest executed** (`code-review.md:103-104`);
  - the **T-H1 zero-match FATAL** — a typo'd/renamed row filter prints `LADDER: FATAL — no gate row
    matched '<filter>' …; ZERO gates were executed` and exits 1 (`delta-tidy-verify.md` §1.1; the
    tester's verbatim capture, J3);
  - the **W-11 canary** — the AC-id cross-artifact pin runs **7 collected / 6 passed / exactly 1
    xfailed**, the single strict xfail being the expected artifact-identity canary
    (`code-review.md:100`; tester J3 row-18).
- **Ladder full run (tester):** `LADDER: OK (mode=--run, rows matched=17)`, exit 0 — **155 tests,
  0 failed, 0 skipped, exactly 1 xfailed** (tester J3).
- **Mutation legs: 9/9 re-proven independently by the tester** — 7 required + 2 bonus (tester J4).
  Every plant REDdened its pin on the first try; every restore was sha-verified (pre == post).
  Part 1: A1 carrier-dimension, T-M7 broad catch, W-4 flag flip. Part 2: T-H1 counter, F8-a, F4-a,
  F7-a-static, F7-e (bonus, PG), F7-b-static (bonus).
- **Base-vs-head evidence pointer:** the tester's functional-gate RESULTS doc — branch
  `tester/dbpool-functional-gate`, commit `148a7b30d`, file
  `.agents/tester/RESULTS/2026-10-29-dbpool-functional-gate.md` (read-only; never modified by this
  lane). Count identity: base **30,975** collected → head **31,084** = **+109 exactly**
  (103 new default-pack tests + 6 row-1 amendment ids; the arithmetic closes).

### 5. WHAT THE MEASUREMENT CHANGED vs the original RCA

This is the part a reader of the RCA above most needs. The Phase-0 empirical baseline
(`BASELINE.md`; canonical copy `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`)
**refuted or re-scoped** the RCA's central claims — six rows below. The plan text was changed where
a measurement contradicted a premise — nothing was annotated away.

| The RCA above said | What the measurement shows | Artifact |
|---|---|---|
| The pool was starved, so probes timed out and the API stalled | **The pool was never exhausted.** 6 of 15 slots occupied ⇒ **9 free** ⇒ no checkout ever queues ⇒ `pool_timeout=30 s` is unreachable ⇒ the 0.5 s probe budget and the 5–8 s `/api/instances` latency **cannot** be caused by pool occupancy at the sampled instant. Occupancy is a **symptom**; the cause is **sync DB work on the pegged event loop** | `council-report.md:53`, `:59-64` |
| "6 `idle in transaction` backends" is the smoking gun | The `idle in transaction` census is **blind to the dominant occupancy shape**: a connection checked out **before its first statement** reports `state='idle'` with `xact_start=NULL` — it is *not* `idle in transaction` | `BASELINE.md:161-166`, `:200-205` |
| The `queue_freshness` probe leaks a transaction per tick (it reads `job_queue_items`) | The probe's SQL reads **`FROM task`**, not `job_queue_items` — the RCA's fingerprint does not belong to the probe. And under pool starvation the probe's timeout path holds **no connection**: `checkedout()` after the timeout equals the holder count — the probe thread is a **pool waiter**, not a holder | `BASELINE.md:380-391`, `:466-478` |
| "An **aborted** connection handed back out makes every subsequent statement fail" | **REFUTED for the pool path.** SQLAlchemy 2.0.48's library default already rolls back on return — in every failure shape the backend's post-close state is `idle`, `xact_start=NULL`, `last_query='ROLLBACK'`, and the next borrower succeeds. The journal quote reproduces, but it is a **same-connection cascade** in wedge-capture (the first query aborts the transaction; the 2nd and 3rd on the *same* connection then report "current transaction is aborted") | `BASELINE.md:415-428`, `:430-459` |
| The M-R3 hold is short, so the plan can narrow F8 | **The flip condition is NOT MET ⇒ F8 stands.** The worst measured hold is **8011 ms** (the sync twin); the hold is not an independent cost, it is *exactly* the awaited fetch latency. The condition is restated as a **precondition** ("the hold < 2 s **iff** the awaited fetch < 2 s"), and the fetch is data-dependent | `BASELINE.md:129-143`, `:548` |
| The `mission_records` `UniqueViolation` trigger is unexplained (OQ-1) | **OQ-1 CLOSED:** the trigger is a **concurrent double-mint for the same anchor** — the sequential path is idempotent, and the re-resolve probe is *skipped* on the boot path (`founding_message_id=None`), so the error re-raised. Measured 7/8 violations in a plain 8-way concurrent run, 5/6 with the overlap forced | `BASELINE.md:492-540`, `:555` |

Two further corrections carried into the shipped honesty blocks: the F5 rename is **not** cosmetic
— it is the **root of the aborted-transaction cascade** the RCA attributed to the pool
(`BASELINE.md:457-459`); and the **abort cascade has a second root** this lane does **not** close
(§6, R-10).

### 6. SCOPE, ROUTED, AND THE HONEST BOUNDARY

**This lane does NOT close the incident's symptom class.** The recurrence precondition is **sync DB
work on the pegged event loop** (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`)
— that is a separate commission. After this lane the daemon can still hold 15/15 manager slots; the
guard's occupancy invariant (F7-e) is the thing that will see it (`plan-overview.md:465`).

| # | Routed item | What this lane did / did not do |
|---|---|---|
| **R-1** | **Event-loop sync-DB** (the dominant dwell multiplier) | **ROUTED** — disclosed, not fixed. The stale minimising comment at the site was corrected in-lane (F9.1, comment-only); the defect itself remains open |
| **R-2** | **BFS depth-loop transaction span** (`daemon/repositories/instance/repository.py:1434-1645`) | **ROUTED** — disclosed, not fixed |
| **R-3** | **Default-executor ceiling** (12 workers on an 8-core host; 1351 `to_thread` sites) | **ROUTED** — assessed + disclosed |
| **R-4 / R-7** | **Missed-finalize family** | With a healthy pool, **a finalize that *fires* commits instead of rolling back — but a finalize that *never fires* is untouched by this lane.** The **aborted-transaction subclass is retired; the RUNNING-stalled class is NOT** (`plan-overview.md:361`, `:374`, `:377`) |
| **R-9** | **asyncpg adapter idle-in-txn hold** (`daemon/checkpoint_adapter.py:1229-1240` — an explicit `conn.transaction()` with three awaits between DELETEs, no server-side reap) | **Attribution shipped** (its own DSN + `server_settings`). The **hold itself** belongs to the follow-up DB-pool commission, under the same D-47 enablement gate |
| **R-10** | **wedge-capture store-census defect** | `db_capture.py:347` reads `n_live_tup` from **`pg_class`** — that column exists only on `pg_stat_user_tables` ⇒ the census fails on every real server. **Born-broken at base, not introduced here**, and it is a **SECOND root of the aborted cascade** (the census failure aborts the connection; the next reads inherit `InFailedSqlTransaction`). **F5 fixed ONLY the relation error — this lane does NOT close that root** (`plan-overview.md:380`; tester J5(b) "R-10 DISCLOSED") |
| **R-11** | **`daemon/tools/instance.py:1346` unreachable defensive check** | **DISCLOSED**, no patch in this lane |

Also outside this lane: the **pre-existing seal-tripwire reds** (the chart-image seal test
`tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts::test_phase_d_does_not_modify_sealed_artifacts`
— **red at base**, hence not net-new by construction; and the `daemon/sources/registry.py` /
`daemon/constants.py` `SEALED_SHA_BASELINES` rows, already red at the tester's pre-Δ3 parent) and
the **`g7_unique_index_smoke_test.py` module-level `sys.exit(0)`** collection error (an
INTERNALERROR that quarantines that file from naive collection). Both are a **separate re-pin
ceremony**, not this lane's work (tester J3 + the gate's open-items list).

### 7. GATE RECORD POINTERS

- **Plan of record:** `.agents/shared/planning/db-pool-idle-txn/` — `BASELINE.md` (the Phase-0
  measurements that re-scoped the RCA), `final-implementation-spec.md` (§5 = the ONE 46-pin
  inventory; §6.2a = the 17 ladder rows), `plan-overview.md` (§12 routed items, §15 release
  posture), `phase1/2/3-plan.md`, `test-strategy.md`, `decisions.md`, `audit-findings.md`,
  `FOLD-MAP.md`, `expected-collected.md`, `council-report.md`.
- **Code gate:** `code-review.md` — APPROVE-WITH-ADVISORIES, 0 CRITICAL, 6 warnings (W-1/W-2 since
  folded).
- **Quality pass:** `tidier-report.md` (37 findings: 6 High / 17 Medium / 14 Low) and
  `delta-tidy-verify.md` (VERDICT: TESTER-GATE-READY, 0 conditions; 10 deferred adjudications:
  0 FIX-NOW · 1 DEFER-POST-TESTER · 9 NO-ACTION-WITH-REASON).
- **Independent plan approval:** `.agents/approver/db-pool-idle-txn-tracking.md` — VERDICT:
  **APPROVED**, 0 blocking issues, 2026-10-28.
- **Functional gate (off-tree, read-only):** the tester's RESULTS doc —
  `git show tester/dbpool-functional-gate:.agents/tester/RESULTS/2026-10-29-dbpool-functional-gate.md`
  (branch `tester/dbpool-functional-gate`, commit `148a7b30d`). **Its verdict is FAIL — one gate:
  G3.5 (11 net-new reds), one root cause: the F4-A pre-check crashed on non-QueuePool engines**
  (`readiness.py:1512` at the tester's tree). The lane's own last commit `441a1657d` fixes exactly
  that, and the F4-a mutation leg was re-proven RED→GREEN on the merged tester tree `3d835d8dd`;
  **the re-verification is not yet written up as a RESULTS doc**, so the tester's FINAL verdict
  stays FAIL pending that write-up. Never modified by this lane.
- **Stage / promote boundary: STAGE ONLY.** The promote flip is the **user's 3-factor nonce
  ceremony** — never performed by this lane, never by an agent (`plan-overview.md:436`). The lane
  branch is **not pushed** to origin, and the live daemon, the live symlink and the promote journal
  are untouched.
