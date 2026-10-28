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
