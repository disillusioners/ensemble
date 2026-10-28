# Test Strategy — DB pool poisoned by leaked idle-in-transaction connections

- **Lane:** `fix/db-pool-idle-txn` · job `634c38f2` · branch `fix/db-pool-idle-txn`
- **BASE anchor:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (verified via `git rev-parse HEAD` in `/home/nea/ensemble-src-wt-dbpool-idletxn`)
- **HEAD anchor:** the post-fix commit produced by this lane (re-derived at gate time; the L1 tip-identity rule does not apply — this is a test-strategy artifact, not a release)
- **Companion artifacts (same directory, written in parallel — cross-reference by filename only):**
  `plan-overview.md` · `decisions.md` · `phase1-plan.md` · `phase2-plan.md` · `phase3-plan.md`
  · `research-leak-sites.md` · `research-pool-couplings.md` · `research-audit-census.md` · `audit-method.md` · `wi-78967dba-verdict.md`
- **Status:** Draft for review — planning only, zero code changes, no commits made by this artifact's author.

---

## 0. Scope, fix-ID map, and what this strategy can/cannot prove

### 0.1 Fix-ID map (carried from the lane dispatch; IDs are stable across the lane's artifacts)

| Fix ID | Subject | Research verdict it discharges | Primary artifact |
|---|---|---|---|
| **F1** | Systematic borrow-site **audit** (≈689 borrows) | CLEAN/RISK classification; 0 unclosed borrows | `audit-method.md` |
| **F2** | **Pool hardening** on the manager engine (site 1) | `reset_on_return` / `pool_recycle` / server-side PG timeouts are ABSENT today | this file §1.2, §3 pins F2-a..d |
| **F3** | `ens_db_repair_execute` dry-run explicit `BEGIN` | **CONFIRMED** leak mechanism (site A) | this file §1.1, §3 pins F3-a..b |
| **F4** | Readiness probe **timeout-path connection retention** | **CONFIRMED** (site D); per-tick leak hypothesis REFUTED | this file §1.2, §3 pins F4-a..b |
| **F5** | wedge-capture `tasks` → `task` | **CONFIRMED** (site F) | this file §3 pins F5-a..b |
| **F6** | `mint_boot_epoch_if_none` UniqueViolation | **REFUTED as a leak**, but the 4×/boot logic defect is real | this file §3 pins F6-a..b |
| **F7** | **Regression guard** (the RCA's fix-item 7) | "no test anywhere asserts zero idle in transaction" (research-pool-couplings.md:175) | this file §1.3, §3 pins F7-a..c |

### 0.2 What this strategy CAN prove

1. That the **confirmed** mechanisms (F3 BEGIN-outside-try; F4 probe-timeout retention) produce a measurable `idle in transaction` population on a real scratch PostgreSQL, and that the fixed code produces none.
2. That the fix does not regress the soak-gate grace/leak surfaces (the 8 DO-NOT-TOUCH packs stay green).
3. That the guard itself is not vacuous (collected-count discipline + self-pins).

### 0.3 What this strategy CANNOT prove (disclosed, do not overstate in the gate report)

- **Two of the RCA's four `pg_stat_activity` fingerprints are NOT reproducible at BASE.** The RCA table
  (`incident-20261026-db-pool-idle-in-transaction.md:56-61`) lists `SELECT instances.instance_id, ...` (×2) and
  `SELECT count(*) FROM job_queue_items ...` (×1). Research CORRECTED both: the `/api/instances` ORM read path is
  context-managed (`daemon/repositories/instance/repository.py:1383` / `:1434`, inside `with SQLModelSession(...)`),
  and the only render-match for the `job_queue_items` count is `defer_block_resolver._DEFER_PENDING_COUNT_SQL`
  (`:185-191`) executed at `:274` and `:321` — both inside `with ... engine.connect()`. **There is no
  never-closed borrow at either site at BASE** (research-leak-sites.md:82, :108; research-audit-census.md:73-82).
  ⇒ A churn leg built ONLY from those two paths is expected to show **0** idle-in-txn at BASE. A BASE-demonstrating
  leg MUST include the confirmed mechanisms (F3, F4). §1.3 designs the composite leg accordingly.
- **Production load shape.** The RCA's "0 → 6 backends in ~3 min" is a production figure under real resume-burst
  load; the lab rate on a scratch cluster is NOT the same number and must not be asserted as such.
- **Pool starvation without aborted transactions** is a *different* mechanism (the Oct-26 checkpoint-pool
  saturation, `analysis-20261026-v0278-soak-failure.md`) and is NOT this lane's target.

---

## 1. (a) RELAPSE REPRO STRATEGY on scratch PostgreSQL

### 1.0 The scratch-PG harness (binding for every leg below)

**No Docker.** Docker is BANNED in this repo (`docker-compose.test.yml` is unusable — blueprint
"[INSTALL+PG]"). Use a **native disposable PostgreSQL cluster** on the host:

```bash
# One-time, host-local, throwaway cluster (initdb -A trust; NOT a docker volume)
export PGBIN="$(dirname "$(readlink -f "$(command -v initdb)")")"
export PGDATA="$(mktemp -d /tmp/dbpool-pg.XXXXXX)"
"$PGBIN/initdb" -A trust -U postgres -D "$PGDATA" >>"$PGDATA/initdb.log" 2>&1
"$PGBIN/pg_ctl" -D "$PGDATA" -o "-p 5433 -k $PGDATA" -l "$PGDATA/pg.log" -w start
```

> **Named-cluster note:** the blueprint records a pre-existing `localhost:5433` listener and a
> `pg18_rancher_local` role; **use a distinct port** (`5433`) and a distinct role name so a stray ambient
> `POSTGRES_*` can never point the repro at a non-scratch database.

Per run, create a **fresh scratch database + throwaway LOGIN role**, and drop both at teardown:

```bash
export PGPORT=5433
export PGROLE="dbpool_repro"
export PGPASSWORD="dbpool_repro_pw"
"$PGBIN/createdb" -h /tmp -p "$PGPORT" -U postgres -O postgres "dbpool_${RUN_ID}" 2>/dev/null || \
  "$PGBIN/createdb" -h 127.0.0.1 -p "$PGPORT" -U postgres -O postgres "dbpool_${RUN_ID}"
"$PGBIN/psql" -h 127.0.0.1 -p "$PGPORT" -U postgres -d "dbpool_${RUN_ID}" \
  -c "CREATE ROLE $PGROLE LOGIN PASSWORD '$PGPASSWORD'; GRANT ALL ON DATABASE \"dbpool_${RUN_ID}\" TO $PGROLE;" \
  -c "GRANT pg_read_all_stats TO $PGROLE;"   # ← so pg_stat_activity rows of other backends are fully visible
```

> **`createdb` over TCP, NOT a `/tmp` socket path.** The blueprint records that psycopg defaults to
> `:5432` and mis-parses a socket path as `path:port` → `dbname`. Always pass `-h 127.0.0.1 -p $PGPORT`.

**Schema.** The scratch DB gets the real SQLModel schema via the repo's own `SQLModel.metadata.create_all`
(the same mechanism `tests/postgres/conftest.py:187-188` uses). The daemon's own migrations are irrelevant
to the leak mechanics; `create_all` is sufficient and avoids the SQLite-only runner entirely.

**Per-variable env scrub — MANDATORY on every command that touches PG.**

```bash
# ✅ CORRECT — scrub per variable (this is the house form, phase1-plan.md:112 of the soak lane)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB="dbpool_${RUN_ID}" \
    PG_TEST_USER="$PGROLE" PG_TEST_PASSWORD="$PGPASSWORD" \
    <command>

# ❌ FORBIDDEN — silent NO-OP (bash does not glob env names)
env -u POSTGRES_* <command>
```

**Also scrubbed on every command:** `HOME="$(mktemp -d)"` — test scripts must NEVER resolve live-shaped
HOME-derived install paths (the 2026-10-12 live-install-deletion incident). The scratch HOME is deleted at
teardown.

**Census helper (the poller used by every leg).** Written as a repo test helper, not an ad-hoc shell loop,
so the same code path is used by the leg scripts and by the F7 guard pack:

```python
# tests/helpers/dbpool_idle_txn_census.py  (shape, to be authored by the implementer)
def idle_in_txn_backends(engine, *, application_name: str | None = None) -> list[dict]:
    """Rows in pg_stat_activity whose state is 'idle in transaction' (or the
    '(aborted)' variant). Optionally filtered to one application_name."""
    sql = text("""
        SELECT pid, state, wait_event_type, wait_event,
               application_name, backend_start, state_change,
               (query_start IS NOT NULL) AS had_query
          FROM pg_stat_activity
         WHERE state IN ('idle in transaction', 'idle in transaction (aborted)')
           AND datname = current_database()
           AND (:app IS NULL OR application_name = :app)
    """)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(sql, {"app": application_name}).mappings()]
```

**Attribution (a required prerequisite, not a nice-to-have).** The daemon's connections carry an **EMPTY
`application_name`** (research-leak-sites.md:173), so a census cannot tell the manager engine from the repair
engine post-hoc — and the RCA's own forensic difficulty. The fix lane must therefore **set a distinct
`application_name`** on the daemon engines (`ensemble-manager`, `ensemble-repair`) as part of F2. Until that
lands, the BASE census filters by `backend_start > run_start` (backends created during this run), which is a
sound proxy. The F7 guard asserts the `application_name` presence so the attribution gap cannot reopen.

---

### 1.1 LEG A — Site A / F3 (`ens_db_repair_execute` dry-run explicit `BEGIN`)

**Mechanism under test (PROVEN, research-leak-sites.md §2 "Site A").**
`daemon/tools/ens_db_tools.py` `_dry_run_sqlite_safe`: the outer `try:` is at `:1475`; the DDL branch
`session.execute(text("BEGIN"))` is at `:1479` and the DML branch's at `:1491` — **outside the inner
`try:`** that owns the rollback (inner try `:1480`/`:1492`, rollbacks at `:1488`/`:1501`/`:1503`). The outer
`except` at `:1513` appends a preview line and **does not roll back**.

**Failure inventory (PROVEN):**
| Path | Rollback runs? |
|---|---|
| DDL success | yes (`:1488`) |
| DDL SQL fails (inner except `:1483`) | yes (`:1488` still runs) |
| **BEGIN itself fails** | **NO** → outer except `:1513` |
| **`session.rollback()` itself fails** (`:1488`) | **NO** → outer except `:1513` |
| DML success / DML SQL fails | yes (`:1501` / `:1503`) |
| **BEGIN itself fails (DML)** | **NO** → outer except `:1513` |

**Deterministic forcing recipe.** The failure must be injected at the exact seam (between the explicit BEGIN
and the inner try), not waited for. Two sanctioned forcing methods, in preference order:

- **A1 (preferred — hermetic fault injection).** Wrap the `Session` handed to `_dry_run_sqlite_safe` with a
  stub whose `execute` raises on the clause whose text is exactly `"BEGIN"` (the only two BEGIN strings in
  `daemon/` are `:1479` and `:1491` — grep-verified). Force it by injecting the exception at the
  `session.execute` boundary so the raise lands on the BEGIN call itself.
- **A2 (real-PG, no monkeypatching).** Put the connection into a **rollback-required** state *before* the
  dry-run so the literal `BEGIN` raises: execute a deliberately invalid statement on the same session
  (e.g. `SELECT * FROM relation_that_does_not_exist`) and do **not** roll it back. The next statement on that
  connection fails — this is exactly the incident's aborted-transaction fingerprint
  (`incident-20261026-db-pool-idle-in-transaction.md:37-43`: *"current transaction is aborted, commands ignored
  until rollback"*). The `BEGIN` at `:1479`/`:1491` is then the first statement to hit the aborted state.

**The 60 s server-side timeout — and why the repro must NOT wait it out.** The **repair engine** carries
`-c idle_in_transaction_session_timeout=60000` in its PG connect options (`ens_db_tools.py:619-627`,
`REPAIR_IDLE_IN_TXN_TIMEOUT_MS = 60_000` at `:188`). A leaked idle-in-txn backend on that engine is reaped by
PostgreSQL within 60 s. **Therefore the repro MUST NOT use "wait 60 s and observe the backend vanish" as its
signal.** The asserted signals are:

1. **Client-side transaction state (deterministic, sub-second, primary).** Immediately after the failing
   dry-run returns, inspect the connection: the session/connection must be **NOT** in a transaction.
   Observable: `conn.in_transaction() is False` and `session.get_transaction() is None`.
2. **Aborted-state statement failure (deterministic, sub-second, corroborating).** Execute one further
   statement on the same connection. If the txn was left open-and-aborted, it must raise
   *"current transaction is aborted"*. At BASE this raise is the expected signal; at HEAD it must NOT occur.
3. **Server-side census (bounded window only, corroborating).** A census taken within the ≤60 s window
   (poll at ≈ +0.5 s and again at ≈ +5 s) shows the backend in `idle in transaction` /
   `idle in transaction (aborted)`. This is *corroboration of a state that already exists*, never a wait-for
   mechanism. The ≤60 s server-side reaping is itself a BASE-vs-HEAD asymmetry that must be disclosed in the
   gate report: at BASE the backend disappears by server timeout; at HEAD it never parks.

**Expected signals.**

| | BASE `19fc11c8b` | HEAD (post-fix) |
|---|---|---|
| Failing BEGIN → `conn.in_transaction()` | **True** (txn left open) | **False** |
| Further statement on that connection | raises *"current transaction is aborted"* | **no raise** |
| `pg_stat_activity` within ≤60 s | 1 backend `idle in transaction (aborted)` on the repair engine | **0** |
| Preview output | `"DRY-RUN WRAPPER ERROR (...)"` with no rollback performed | rollback performed; the failure is reported (label may change — see research-leak-sites.md:61 on the *"DRY-RUN DDL/DML FAILED"* mislabel if the fix moves BEGIN inside the inner try) |

**Additional BASE→HEAD invariants to assert in the same leg (from the research's fix-shape facts).**
- After the fix, the literal `text("BEGIN")` string must be **absent** from `_dry_run_sqlite_safe` (the fix
  shape (a) — drop the explicit BEGIN entirely; SQLAlchemy autobegins and the ROLLBACKs at the `:1488`/`:1501`/
  `:1503` sites provide the "does not commit" property). Research fact: **no test in `tests/` asserts
  `text("BEGIN")`** (grep-verified, research-leak-sites.md:59) and no savepoint/isolation/multi-statement
  semantics depend on the literal BEGIN. If the lane instead chooses fix shape (b) (move the BEGIN inside the
  inner try), the *behavioural* assertions above still hold; only the static string assertion changes. **The
  test strategy must be written against the behavioural assertions, with the static assertion parameterised
  by the chosen shape.**
- The DDL branch's `session.rollback()` at `:1488` failing must also be covered (the "after BEGIN" variant the
  dispatch names): inject a rollback failure → the outer except must still leave the connection NOT in a
  transaction at HEAD.

---

### 1.2 LEG D — Site D / F4 (readiness probe timeout-path connection retention)

**Mechanism under test (PROVEN, research-leak-sites.md §2 "Site D").**
`daemon/services/readiness.py` `_guarded` (`:1262-1290`) runs
`asyncio.wait_for(asyncio.to_thread(probe), timeout=timeout_s)`. On timeout the asyncio side gives up but the
**worker thread keeps running**, holding the connection checked out inside `with engine.connect()`:
`:1477` (DB probe, `make_db_probe`), `:1752` (queue probe, `make_queue_probe`). Budgets:
`DB_PROBE_TIMEOUT_S = 0.5` (`:113`), `CHECKPOINT_SAVER_PROBE_TIMEOUT_S = 1.0` (`:122`),
`QUEUE_PROBE_TIMEOUT_S = 2.0` (`:127`). The module's own docstring states the retention
(`readiness.py:107-112`). The manager engine has **no** server-side `statement_timeout` and **no**
`idle_in_transaction_session_timeout` (`daemon/repositories/factory.py:252`, `:323-338`), and the DB/queue
probes have **no** cancellation — unlike the saver probe, which got ghost-waiter withdrawal
(`readiness.py:1669-1684`).

**Deterministic forcing recipes.** Two legs, because the two probes block on different things:

- **D1 (SQL-level, deterministic, both probes).** Drive the **real `_guarded` seam** with a probe whose shape
  is byte-identical to the production probe but whose SQL is slow:
  `with engine.connect() as conn: conn.execute(sa_text("SELECT pg_sleep(30)"))`.
  `asyncio.wait_for` fires at the production budget (0.5 s DB / 2.0 s queue); the `to_thread` worker is still
  inside `with engine.connect()` when the await returns.
  **Assertion:** `engine.pool.checkedout()` is `>= 1` **after** the timeout returns, and the abandoned thread is
  still alive (`threading.enumerate()` shows it, or a `threading.Event` set at the end of the probe body is
  NOT set at assert time).
  At BASE: retained. At HEAD: not retained beyond the fix's bound.
- **D2 (production probe, lock-blocked — no SQL substitution).** Use the **real** `make_queue_probe` against the
  scratch PG, with a second session holding the lock:
  ```sql
  BEGIN; LOCK TABLE task IN ACCESS EXCLUSIVE MODE;   -- held for the duration of the leg
  ```
  The queue probe's SQL (`_QUEUE_MAX_AGE_SQL_POSTGRES` `readiness.py:201-205`, `_INFLIGHT_COUNT_SQL_POSTGRES`
  `:228-237`) reads `FROM task` → it blocks on the lock → the 2.0 s `wait_for` budget expires → the abandoned
  worker holds a checked-out connection whose backend is `idle in transaction` with
  `wait_event_type='Lock'`.
  (The DB probe's `SELECT 1` does not touch a table, so it cannot be lock-blocked — D1 is its deterministic
  analogue; do NOT claim a lock-blocked `SELECT 1`.)

**Expected signals.**

| | BASE `19fc11c8b` | HEAD (post-fix) |
|---|---|---|
| After `wait_for` returns, `engine.pool.checkedout()` | **≥ 1** for an unbounded period (no server/client timeout on the manager engine) | returns to baseline **within the fix's bound** (state the bound; it must be ≤ the probe budget + the fix's own timeout) |
| Abandoned `to_thread` worker | **still running** | terminated / connection released |
| `pg_stat_activity` (D2, lock held) | 1 backend `idle in transaction`, `wait_event_type='Lock'` | **0** (or bounded ≤ the fix's bound) |
| Readiness classification | `probe_timeout` (fail-class row 1) | unchanged — the fix must NOT change the classification semantics; it only removes the retention. **Non-interference with the soak grace is asserted by the DO-NOT-TOUCH packs (§3.4).** |

**Disclosed asymmetry.** The DB/queue probes cannot cancel a synchronous `conn.execute` — only the saver probe
has a precedent (`future.cancel()`, `readiness.py:1669-1684`). Therefore the fix for F4 is bounded by one of:
(a) a dedicated short-lived probe engine with server-side `statement_timeout` (+ `idle_in_transaction_session_timeout`),
(b) a per-probe cancellation/withdrawal seam, (c) a dedicated probe pool whose exhaustion cannot starve the
manager pool. **The test strategy is written to be agnostic** — it asserts the *observable* (no retained
connection beyond the bound; 0 idle-in-txn backends), not the mechanism, so it survives whichever shape the plan
selects. The plan MUST state the bound; the pin asserts against the stated constant.

---

### 1.3 LEG C — Black-box churn + census (the F7 guard's load leg)

**This leg must be COMPOSITE.** Per §0.3, a churn leg built only from the RCA's corrected-clean paths shows 0 at
BASE. To be a *discriminating* leg (BASE > 0, HEAD = 0) it must include the confirmed retention trigger (D)
inside the churn. Structure:

1. **Churn generator** (auto-resume-shaped traffic, library level — the daemon is NOT run in this lane):
   - instance reads: `SQLModelInstanceRepository.list(...)` — the exact Site-B call chain
     (`daemon/routers/instances.py:371` → `instance_lifecycle.list_instances` `:5021` → repository `:1259`);
   - queue counts: `DeferBlockResolver.defer_pending_count()` (`defer_block_resolver.py:274`) and
     `defer_pending_count` inside `resolve()` (`:321`) — the Site-C paths;
   - mint attempts: `MissionRecordService.mint_boot_epoch_if_none()` (`mission_record_service.py:679`) — the
     Site-E path, called ≥4× per cycle (the RCA's 4×/boot shape);
   - readiness probes: the real `make_db_probe` / `make_queue_probe` at the production **10 s** cadence
     (`readiness_refresh_interval_seconds`), each cycle running **one deliberately blocked** probe (D2's lock
     held briefly) so the confirmed retention path is exercised;
   - wedge-capture: the real `capture_db_snapshot` (`daemon/services/wedge_capture/db_capture.py:279`) — the
     Site-F path (at BASE it errors on the wrong table; the census must not be polluted by it — see §1.3.2).
2. **Poller.** A concurrent thread samples `pg_stat_activity` every **1 s** (the census helper of §1.0) and
   records, per sample: total `idle in transaction` count, and the per-`application_name` breakdown.
3. **Duration.** `N` minutes. **N is a pack-defined constant** — recommended **N = 10** (≥3× the RCA's ~3 min
   relapse figure, and ≥2× the 300 s strand-heal cadence so a background sweep cannot hide a regression).
   The pack MUST carry `@pytest.mark.timeout(N * 60 + 300)`.

**1.3.1 Expected signals.**

| | BASE `19fc11c8b` | HEAD (post-fix) |
|---|---|---|
| Peak `idle in transaction` count over the window | **≥ 1** (attributable to this run's backends) | **0 at every sample** |
| Samples with count > 0 | **≥ 1** | **0** |
| Count at the final sample | **≥ 1** (the RCA's relapse shape: the count does not self-clear) | **0** |
| `checkedout()` at the end of the churn | may be > baseline (retained) | back to baseline |

**1.3.2 Disclosed interference sources (the census must exclude them, by rule).**
- The **leg-A** repair-engine backend is `idle in transaction (aborted)` and lives on the *repair* engine, not
  the manager engine → filter by `application_name` (post-F2) or by `backend_start > run_start` **and**
  `state != 'idle in transaction (aborted)'` when isolating the F4 signal. The F7 guard, by contrast, asserts
  the **TOTAL** count is 0 — aborted included — because a healthy pool must not produce either.
- The **wedge-capture** connection is a one-shot `psycopg.connect` with `close()` in `finally`
  (`db_capture.py:185-200`, `:289-294`) → it never parks; at BASE it *errors* on the wrong table
  (`:308`, `:387`) and the errors are same-connection cascades. It contributes 0 idle-in-txn backends and must
  not be counted as a failure of this leg.
- The **poller's own** census connection is a healthy, short-lived read; it must be excluded explicitly
  (the census query runs inside `with engine.connect()`; it is never `idle in transaction` at sample time
  because SQLAlchemy's autobegin + the read completes and the block exits — but the census MUST run on a
  **separate engine** from the one under test so the census's own transaction can never be the one it counts).

**1.3.3 The F7 guard's *read-only cycle* leg (the RCA's fix-item 7, verbatim).**
A short, separate, non-load assertion: after a read-only request cycle (instance list + queue count + one
readiness probe), `pg_stat_activity` has **zero** `idle in transaction` backends attributable to the run.
This is the assertion the RCA explicitly asked for and which **no test in the repo currently makes**
(research-pool-couplings.md:175: *"No test anywhere asserts 'zero idle in transaction'"*).

---

## 2. (b) RUNNER FORM + C3 DISCIPLINE (false-green)

### 2.1 The false-green mechanism this section exists to defeat

`pyproject.toml:95` sets
`addopts = "-m 'not integration and not postgres and not slow and not conformance'"`.
⇒ **A bare `pytest tests/<anything>/` deselects the very tests it names and reports "0 failed" with nothing
executed.** The soak lane recorded this as the C3 lesson (`plan-overview.md:362-366`); the binding house rule is
`requirements.md:1133` and `:1169-1172`.

### 2.2 The EXACT command form for each pack class

**Interpreter.** `python` does **not** exist on this host (only `/usr/bin/python3`); `uv` is **off-PATH** at
`/home/nea/.local/bin/uv`; the worktree has **no `.venv`**. Every gate command names its interpreter
explicitly. Sanctioned forms:

```bash
# Form I — uv (preferred; resolves the worktree's own dependency set)
PATH="/home/nea/.local/bin:$PATH" uv run python -m pytest <args>

# Form II — system interpreter against the certified venv
/usr/bin/python3 -m pytest <args>
```

> **Certification requirement (blueprint "[INSTALL+PG]"):** worktrees lack `.venv`; the certified pattern is
> *cd-worktree + main-venv interpreter* (`cwd sys.path[0]=''` shadows `.pth`). The implementer MUST verify
> `import daemon` resolves **inside the worktree** (`python -c "import daemon, os; print(os.path.dirname(daemon.__file__))"`)
> and record the resolved path in the gate report before any pack is trusted. **NEVER reuse the main `.venv`
> for runs in the shared checkout.**

**Class 1 — default-run unit packs (no marker; collected by the default `pytest`).**

```bash
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
PATH="/home/nea/.local/bin:$PATH" uv run python -m pytest \
  tests/unit/tools/test_dbpool_txn_hygiene.py \
  tests/unit/tools/test_dbpool_pool_knobs.py \
  tests/unit/tools/test_dbpool_probe_retention.py \
  tests/unit/tools/test_dbpool_guard_pack_selfcheck.py \
  -q
```
> Pack filenames above are **proposed**; the implementer may rename. The **runner form** is fixed.

**Class 2 — PG packs under `tests/postgres/` (auto-marked `postgres`; SERIAL ONLY).**

```bash
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB="dbpool_${RUN_ID}" \
PG_TEST_USER="$PGROLE" PG_TEST_PASSWORD="$PGPASSWORD" \
PATH="/home/nea/.local/bin:$PATH" uv run python -m pytest \
  tests/postgres/test_dbpool_site_a_repro_pg.py \
  tests/postgres/test_dbpool_site_d_retention_pg.py \
  --override-ini="addopts=" -m postgres -q --timeout=300
```
Mechanics (research-pool-couplings.md:148-152):
- `tests/postgres/conftest.py:82-95` auto-applies the `postgres` marker to every test in that directory;
- `:44-45`/`:94-95` **skip every postgres test under xdist** ⇒ **serial only**; never run this class with `-n`;
- the session `pg_engine` fixture does `drop_all` → `create_all` (`:187-188`) and tears down with `drop_all` +
  `dispose` (`:191-195`); an autouse per-test `TRUNCATE … RESTART IDENTITY CASCADE` runs (`:240-258`);
- DSN vars: `PG_TEST_HOST` / `PG_TEST_PORT` / `PG_TEST_DB` / `PG_TEST_USER` / `PG_TEST_PASSWORD`.

**Class 3 — integration packs under `tests/integration/` (opt-in `pytestmark = pytest.mark.integration`).**

```bash
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB="dbpool_${RUN_ID}" \
PG_TEST_USER="$PGROLE" PG_TEST_PASSWORD="$PGPASSWORD" \
PATH="/home/nea/.local/bin:$PATH" uv run python -m pytest \
  tests/integration/test_dbpool_churn_census.py \
  tests/integration/test_dbpool_readcycle_guard.py \
  --override-ini="addopts=" -m integration -q --timeout=900
```
`tests/integration/conftest.py` does **NOT** auto-mark (soak `phase1-plan.md:61`) — the pack file itself must
carry `pytestmark = pytest.mark.integration` (precedent: `tests/integration/test_checkpoint_pool_burst_shapes.py:49`,
`.../test_checkpoint_pool_ghost_waiters.py:58`).

**Class 4 — the DO-NOT-TOUCH regression suite (§3.4).** Mixed classes ⇒ **two commands** (one per marker),
never one.

```bash
# 4a — the 6 default-run unit packs (default addopts are FINE here: no marker ⇒ collected)
/usr/bin/python3 -m pytest \
  tests/unit/tools/test_readiness_failure_class.py \
  tests/unit/tools/test_readyz_grace_detail.py \
  tests/unit/tools/test_saver_probe_contention_class.py \
  tests/unit/tools/test_promote_txn_hold.py \
  tests/unit/tools/test_checkpoint_pool_config.py \
  tests/unit/test_readiness_mcp_independence.py -q

# 4b — the 2 integration packs (house form; MUST NOT use the default addopts)
/usr/bin/python3 -m pytest \
  tests/integration/test_checkpoint_pool_burst_shapes.py \
  tests/integration/test_checkpoint_pool_ghost_waiters.py \
  --override-ini="addopts=" -m integration -q --timeout=300
```

### 2.3 The expected-collected-count assertion (the non-vacuity fence)

**Rule (binding, `requirements.md:1133` + `:1169-1172`):** *no gate may report "0 failed" without also proving
a non-zero collected count equal to the pack's DECLARED count.* "0 failed" alone is vacuous — it is the exact
output a fully-deselected run produces.

**Mechanism.** `pytest <paths> --collect-only -q` prints one line per collected test id plus a summary tail of
the form `42 tests collected in 1.23s`.

> **GNU grep trap (blueprint "[TESTING]"):** `grep -E "a\|b"` is BROKEN — under ERE `\|` is a *literal pipe*.
> Use a plain `|` under `-E`. The pattern below uses no alternation and is therefore safe; if alternation is
> added, use `|` not `\|`.

**Exact assert form** (run BEFORE the real run, and its output pasted into the gate report):

```bash
#!/usr/bin/env bash
set -euo pipefail
# $1 = declared expected count, $2 = marker (or "" for default), $3.. = paths
EXPECTED="$1"; MARKER="$2"; shift 2
ARGS=()
[ -n "$MARKER" ] && ARGS+=(--override-ini="addopts=" -m "$MARKER")
COLLECTED=$(/usr/bin/python3 -m pytest "$@" "${ARGS[@]}" --collect-only -q 2>/dev/null \
  | grep -E 'tests? collected' | grep -oE '^[0-9]+' || true)
COLLECTED="${COLLECTED:-0}"
if [ "$COLLECTED" -ne "$EXPECTED" ]; then
  echo "FATAL: collected=${COLLECTED} expected=${EXPECTED} for: $*" >&2
  exit 1
fi
if [ "$COLLECTED" -le 0 ]; then
  echo "FATAL: vacuous run — 0 collected for: $*" >&2
  exit 1
fi
echo "COLLECTED-OK count=${COLLECTED} expected=${EXPECTED}"
```

**Pack-defined constants.** Each pack declares its count as a module-level constant **inside the pack** so the
number cannot drift from the code:

```python
# tests/unit/tools/test_dbpool_txn_hygiene.py
EXPECTED_COLLECTED = 9   # pack-defined; fixed once authored
```
and the pack carries a **self-check test** that asserts the pack's own collected count equals the constant
(§3, pin F7-c). The gate script then uses the pack's constant as `EXPECTED`.

| Pack (proposed) | Class | Declared count | Constant lives in |
|---|---|---|---|
| `tests/unit/tools/test_dbpool_txn_hygiene.py` | unit (default) | `EXPECTED_COLLECTED_HYGIENE` (placeholder — fix at authoring) | the pack |
| `tests/unit/tools/test_dbpool_pool_knobs.py` | unit (default) | `EXPECTED_COLLECTED_KNOBS` | the pack |
| `tests/unit/tools/test_dbpool_probe_retention.py` | unit (default) | `EXPECTED_COLLECTED_RETENTION` | the pack |
| `tests/unit/tools/test_dbpool_guard_pack_selfcheck.py` | unit (default) | `EXPECTED_COLLECTED_SELFCHECK` | the pack |
| `tests/postgres/test_dbpool_site_a_repro_pg.py` | postgres | `EXPECTED_COLLECTED_SITE_A` | the pack |
| `tests/postgres/test_dbpool_site_d_retention_pg.py` | postgres | `EXPECTED_COLLECTED_SITE_D` | the pack |
| `tests/integration/test_dbpool_churn_census.py` | integration | `EXPECTED_COLLECTED_CHURN` | the pack |
| `tests/integration/test_dbpool_readcycle_guard.py` | integration | `EXPECTED_COLLECTED_GUARD` | the pack |

> The numbers are **placeholders by design** (dispatch: "counts are fixed constants once the pack is authored").
> The *mechanism* (the assert form above + the in-pack constant + the self-check) is fixed now.

**Additional non-vacuity rules that apply to every pack:**
- **A pack that can be silently skipped is a red gate.** `tests/postgres/conftest.py` **skips the whole module**
  when PostgreSQL is unreachable (`:167-169`). The gate script MUST therefore assert the run did not produce
  skips: parse the summary line and fail if `skipped > 0` for the PG classes. **A green PG gate that skipped is
  a FAILING gate.**
- `--timeout=N` on every long pin (house convention, 115 usages; `pyproject.toml:97-98` default is 30 s and the
  kill is `os._exit(1)` ⇒ no traceback). Every new long pin carries an explicit `@pytest.mark.timeout(N)` with
  N > its worst case.
- The churn pack's `@pytest.mark.timeout` must exceed `N*60 + 300`.

---

## 3. (c) MUTATION-MEANINGFUL PIN LIST

**Discipline (blueprint "[DISCIPLINE]"):** *regression pins MUST be mutation-verified.* A pin nothing can redden
is dead weight. Every pin below states **the exact mutation that turns it RED**. Where a mutation is impossible,
that is stated as a residual.

### 3.1 F3 — `ens_db` dry-run BEGIN hygiene (Site A)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F3-a** | After a dry-run whose `BEGIN` raises, the connection is **not** in a transaction (`conn.in_transaction() is False`) and a further statement on it does **not** raise *"current transaction is aborted"*. | Revert the fix so the BEGIN sits **outside** the inner try (restore the `:1479`/`:1491` shape) → the outer except at `:1513` runs with no rollback → the connection stays in a txn → **RED**. |
| **F3-b** | The dry-run's failure path performs a **rollback** (assert the rollback is observable: the session's transaction is None AND a sentinel `rollback` spy fired). | Delete the rollback from the outer-except path (re-introduce the *outer-except-no-rollback* shape) → **RED**. |
| **F3-c** | The DDL-branch `session.rollback()` failing (`:1488`) also leaves the connection clean. | Remove the outer-path rollback handling for the rollback-failure variant → **RED**. |
| **F3-d** *(static, shape-parameterised)* | The literal `text("BEGIN")` no longer appears in `_dry_run_sqlite_safe` — **only if the plan selects fix shape (a)**. If the plan selects shape (b) (move the BEGIN inside the inner try), this pin is REPLACED by: the BEGIN string appears **inside** the inner try block (assert by AST, not by grep). | Re-introduce the BEGIN at the outer scope → **RED**. |
| **F3-e** *(keep-green)* | The **non-dry-run** committed path (`Session(repair_engine)` at `:1067`, the DDL/DML commit branch) is unchanged by the fix. | Any change that alters the commit path's outcome → **RED**. |

### 3.2 F4 — readiness probe retention (Site D)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F4-a** | After a probe timeout driven through the **real `_guarded` seam**, `engine.pool.checkedout()` returns to baseline **within the fix's bound** (the bound is a named constant the plan must define). | Remove the probe connection timeout **or** the cancellation **or** the server-side `statement_timeout` connect-args ⇒ the abandoned worker keeps the connection ⇒ **RED**. |
| **F4-b** | During a **lock-blocked** queue probe (D2), `pg_stat_activity` shows **0** `idle in transaction` backends attributable to the probe engine for longer than the bound. | Remove the server-side `idle_in_transaction_session_timeout` **and** the client-side bound ⇒ the backend parks ⇒ **RED**. |
| **F4-c** *(keep-green, non-interference)* | The probe **classification** semantics are unchanged: a timeout still classifies as `probe_timeout` (`FAILURE_CLASS_PROBE_TIMEOUT`), an exception still as `hard_component_red`, and the saver probe's ghost-waiter withdrawal (`readiness.py:1669-1684`) still fires. | Re-classify a timeout as grace-eligible, or drop the saver withdrawal ⇒ **RED**. |
| **F4-d** *(keep-green)* | The readiness **detail schema** (`readiness.py:312-325`, `:408-446`) is byte-compatible: `pool_state_sqlalchemy`, `pool_state_psycopg`, `leak_signal`, `grace_active` still present. | Rename/remove any detail field ⇒ **RED** (this is also what `tests/unit/tools/test_readyz_grace_detail.py` pins). |

### 3.3 F2 — pool hardening on the manager engine (site 1)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F2-a** | `create_postgres_engine` passes **`reset_on_return="rollback"` EXPLICITLY** (not the library default — the audit found `reset_on_return` set **nowhere** in `daemon/`, so the default is not a pin). | Remove the explicit kwarg ⇒ **RED** (the pin asserts *explicit presence*; a library-default implementation would silently pass a default-based assertion — hence "explicit" is the assertion). |
| **F2-b** | `create_postgres_engine` passes a **positive `pool_recycle`** (site 1 has NONE at BASE; site 8 has 3600). | Drop `pool_recycle` ⇒ **RED**. |
| **F2-c** | The manager engine's PG `connect_args` carry a **server-side `statement_timeout`** *and* an **`idle_in_transaction_session_timeout`** (site 1 has neither at BASE; only the repair engine does, `ens_db_tools.py:619-627`). | Drop either option ⇒ **RED**. |
| **F2-d** *(keep-green)* | `pool_pre_ping is True` still holds on site 1 — the existing pin `tests/unit/repositories/test_pg_engine_utc_session.py:114` must stay GREEN and unmodified. | Remove `pool_pre_ping` ⇒ **RED** (existing pin catches it). |
| **F2-e** *(keep-green)* | The UTC connect-args byte-shape (`PG_SESSION_CONNECT_ARGS`, `factory.py:252`) is preserved — adding the timeout options must not drop `-c timezone=UTC`. | Replace the options string ⇒ **RED** (existing pin at `:292` catches the shape). |
| **F2-f** | The repair engine (site 8) keeps its own `pool_recycle=3600` / `pool_timeout` / `idle_in_transaction_session_timeout=60s` — the F2 change must not weaken site 8. | Remove site 8's timeouts ⇒ **RED**. |
| **F2-g** | The manager engine sets a **distinct `application_name`** (attribution prerequisite, §1.0). | Remove it ⇒ **RED** (and the F7 census's attribution filter breaks). |

### 3.4 DO-NOT-TOUCH regression pins (C1/C2 non-interference)

These are **not new pins** — they are the soak-gate lane's delivered, pin-protected surface. The fix lane must
not weaken, re-key or re-shape any of them; the gate is that they stay **GREEN**.

| # | Pack | Marker | Command class |
|---|---|---|---|
| 1 | `tests/unit/tools/test_readiness_failure_class.py` | none (default) | 4a |
| 2 | `tests/unit/tools/test_readyz_grace_detail.py` | none | 4a |
| 3 | `tests/unit/tools/test_saver_probe_contention_class.py` | none | 4a |
| 4 | `tests/unit/tools/test_promote_txn_hold.py` | none | 4a |
| 5 | `tests/unit/tools/test_checkpoint_pool_config.py` | none | 4a |
| 6 | `tests/unit/test_readiness_mcp_independence.py` | none | 4a |
| 7 | `tests/integration/test_checkpoint_pool_burst_shapes.py` | `integration` | 4b |
| 8 | `tests/integration/test_checkpoint_pool_ghost_waiters.py` | `integration` | 4b |

**Specific surfaces these pins protect** (from research-pool-couplings.md §Q4): the readiness detail fields
(`:312-325`, `:408-446`), `resolve_psycopg_pool` duck-typing on `raw_saver.conn` (`:500-522`), the P1
advisory-never-arms rule (`:525-549`), the P2 oracle (`:552-567`), the conjunction `eligible = degraded AND all
classes grace-eligible AND NOT leak.detected` (`:734-743`), the horizon-never-extended rule (`:745-763`), and the
`pool_leak` grace-ineligibility (`requirements.md:1035`, `:1113-1118`).

**The two F4-adjacent couplings the fix MUST respect:**
1. The P2 oracle is the **psycopg saver pool**; the P1 signal (SQLAlchemy manager engine) is **advisory and MUST
   NOT arm** the leak class (`requirements.md:1113-1118`). A fix that *adds* `application_name`/timeouts to the
   manager engine must not change the P1 sampling surface's *shape* (`sample_sqlalchemy_pool_state` publishes
   `{checkedout, size, overflow}` — `readiness.py:525-549`); renaming or re-shaping it breaks the pins.
2. The saver pool's construction must keep `check=`, `autocommit=True`, and the stats-bearing pool object
   resolvable via `raw_saver.conn` (research-pool-couplings.md:132) — else `resolve_psycopg_pool` returns None
   and the system silently degrades to the S9 streak arm.

### 3.5 F5 — wedge-capture table rename (Site F)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F5-a** | `capture_db_snapshot` reads the executing-turns and resume-history rows against a real scratch PG whose table is `task` — **no** `relation "tasks" does not exist`. | Restore `FROM tasks t` (`:308`) / `FROM tasks` (`:387`) ⇒ **RED**. |
| **F5-b** *(static)* | The strings `FROM tasks` / `tasks t` do not appear in `daemon/services/wedge_capture/db_capture.py` (the module docstring at `:34` also said "tasks" — it is prose and must be corrected too). | Re-introduce either form ⇒ **RED**. |
| **F5-c** *(keep-green)* | The **store census** query (`:351-363`, over `pg_class`/`pg_namespace`) still works — the wrong-table fix must not have touched it. | Break the census SQL ⇒ **RED**. |

### 3.6 F6 — mission mint idempotency (Site E)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F6-a** | A mint against an anchor that already has an **OPEN** epoch **does not raise** `IntegrityError` — the write is idempotent (the plan's chosen shape: `ON CONFLICT (anchor_instance_id) WHERE status = 'open' DO NOTHING` + the existing re-resolve probe, or `DO UPDATE … RETURNING`). | Restore the bare INSERT (`own.add(record); own.commit()` — `mission_record/repository.py:510-513`) ⇒ **RED**. |
| **F6-b** | Repeating the boot mint **4×** (the RCA's per-boot shape) yields **0** `IntegrityError`s and exactly one OPEN epoch. | Remove the idempotency ⇒ **RED**. |
| **F6-c** *(keep-green)* | The **no-session** path (`session=None` → `with Session(self._engine) as own:`) still commits on success and still refreshes the record — the fix must not change the commit semantics. | Change the commit/refresh behaviour ⇒ **RED**. |
| **F6-d** *(schema note)* | **No new `.sql` file is added.** The partial unique index `ux_mission_records_anchor_open` already exists (3 sites: `models.py:307-313`, `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`, and the `manager.py::_ensure_postgres_columns` mirror per the migration's 3-SITE PIN) ⇒ `ON CONFLICT` needs **no** schema change. The house rule "every new SQLModel table needs its own `*.sql`" is **not** triggered. | Adding a migration ⇒ the gate must flag it as an unnecessary schema delta. |

### 3.7 F7 — the regression guard (self-pins)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F7-a** | After a **read-only request cycle** (instance list + queue count + one readiness probe) against scratch PG, the `pg_stat_activity` `idle in transaction` count attributable to the run is **0**. | Delete the `pg_stat_activity` assertion from the guard pack ⇒ the pack's **collected-count** check (§2.3) turns RED (the count drops below the declared constant) — *and* the F7-c self-pin turns RED. |
| **F7-b** | Under the churn load (§1.3, N = 10 min), the sampled `idle in transaction` count is **0 at every sample** and the peak is **0**. | Re-introduce a retention path (e.g. remove the F4 fix's bound) ⇒ **RED**. |
| **F7-c** *(self-pin)* | The guard pack **contains** its `pg_stat_activity` assertion and its count constant — asserted by inspecting the pack's own source/AST, not by trusting the run. | Delete the assertion **and** decrement the declared constant to match ⇒ the F7-c self-pin still turns RED (it re-reads the pack source and requires the assertion to be present). |

> **F7-c is the answer to "a pin nothing can redden is dead".** The self-pin is what makes the guard itself
> non-vacuous: the *only* way to make the guard green-by-absence is to remove the assertion, and that removal
> is detected by a pin that does not depend on the removed assertion's own count.

### 3.8 Audit-clean pins (the census families stay close-guaranteed)

The audit (see `audit-method.md`) found **0 unclosed borrows** across ≈689 sites. These pins make that
**close-guaranteed going forward** — i.e. they detect the *introduction* of a non-close-guaranteed borrow.

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **AC-a** | Every `Session(...)` / `SQLModelSession(...)` **assignment** in `daemon/` (non-`with` form) is either (i) inside a `try:` whose `finally:` closes it, or (ii) inside a `with` block. A static scan over `daemon/**/*.py` fails the build when a bare `session = Session(engine)` appears with no `finally: session.close()`. | Introduce `session = Session(engine)` with no `finally` ⇒ **RED**. |
| **AC-b** | The **4 known non-`with` manual borrows** keep their try/finally-close shape: `daemon/manager.py:2782` (`_session_scope`), `daemon/repositories/auth_bootstrap/repository.py:93` (`consume`), `daemon/repositories/auth_bootstrap/repository.py:161` (`mint_with_sentinel`), `daemon/services/child_reports.py:2594` (the `owns_session` pattern, close at `:2757-2759`). | Remove any of the four `finally: … close()` arms ⇒ **RED**. |
| **AC-c** | `WriteGuardSession.__exit__` still closes the inner session (`daemon/write_pause_guard.py:361-395`) — all 10 `with WriteGuardSession(Session(...))` sites depend on it. | Make `__exit__` skip the close ⇒ **RED**. |
| **AC-d** | Every `engine.connect()` / `engine.begin()` / `pool.acquire()` in `daemon/` is in `with` / `async with` form (the audit found **0** non-context-managed connection borrows and **15/15** asyncpg acquires in `async with`). | Introduce `conn = engine.connect()` without a `with` ⇒ **RED**. |

### 3.9 Pin coverage matrix (all fix IDs covered, nothing orphaned)

| Fix ID | Pins | Mutation-verified? | Notes |
|---|---|---|---|
| F1 (audit) | AC-a … AC-d + `audit-method.md` re-run gate (§4.4) | yes | the audit's own re-run is the F1 gate |
| F2 (pool knobs) | F2-a … F2-g | yes | F2-d/F2-e are existing pins kept green |
| F3 (BEGIN hygiene) | F3-a … F3-e | yes | F3-d is shape-parameterised |
| F4 (probe retention) | F4-a … F4-d | yes | F4-c/F4-d are non-interference |
| F5 (table rename) | F5-a … F5-c | yes | F5-b is static |
| F6 (mint idempotency) | F6-a … F6-d | yes | F6-d is a "no migration" gate |
| F7 (guard) | F7-a … F7-c | yes | F7-c is the self-pin |

---

## 4. (d) GATE STRUCTURE per phase

Phase names follow the dispatch: **phase1 hygiene / phase2 hardening / phase3 guard**.
Fix-ID assignment (stated here because the plan owns the final mapping; the gates below hold under either
mapping for F4):
- **Phase 1 — transaction hygiene (site-level):** **F3**, **F5**, **F6** (the three CONFIRMED/logic sites).
- **Phase 2 — pool & probe hardening:** **F2**, **F4** (the manager-engine knobs + the probe retention bound).
- **Phase 3 — the guard:** **F7** (+ the **F1** audit re-run as the phase-3 audit receipt).

> If the plan places F4 in phase 1 (as "hygiene"), the phase-1 gate list simply gains G2.1/G2.2's F4 rows and
> phase 2 loses them; the gate *definitions* are per-fix-ID and unaffected.

### 4.1 Phase 1 gates — hygiene (F3, F5, F6)

| Gate | Command form | Assertion | Non-vacuity |
|---|---|---|---|
| **G1.1** unit pins | Class 1 | F3-a…F3-e, F5-b, F6-c, AC-a…AC-d → all GREEN | collected == `EXPECTED_COLLECTED_HYGIENE` and > 0 |
| **G1.2** PG repro (real PG) | Class 2 | F3-a at BASE-shape reproduced / HEAD closed; F5-a (real `task` table read); F6-a/F6-b (idempotent mint, 4×) → all GREEN | collected == `EXPECTED_COLLECTED_SITE_A` and > 0; **zero skips allowed** |
| **G1.3** DO-NOT-TOUCH 4a | Class 4a | the 6 default-run unit packs GREEN | collected == their declared counts (> 0) |
| **G1.4** audit re-run | `audit-method.md` recipe | census counts unchanged from the `19fc11c8b` baseline (§`audit-method.md` §6) and **0 unclosed** borrows | the re-run script prints its own per-pattern counts — a count of `0` for any family is a FAILURE |
| **G1.5** whole-tree net-new | default `pytest` | net-new FAILED set vs the TRUE base is **EMPTY** (full FAILED-list diff, same command both sides; order/load-sensitive reds re-run 3× isolated per side before classifying) | the base run's collected count is recorded |

### 4.2 Phase 2 gates — hardening (F2, F4)

| Gate | Command form | Assertion | Non-vacuity |
|---|---|---|---|
| **G2.1** unit knobs + retention | Class 1 | F2-a…F2-g, F4-a, F4-c, F4-d → GREEN | collected == `EXPECTED_COLLECTED_KNOBS` (+ `..._RETENTION`) and > 0 |
| **G2.2** PG retention (real PG) | Class 2 | F4-b (lock-blocked probe ⇒ 0 idle-in-txn attributable backends) | collected == `EXPECTED_COLLECTED_SITE_D` and > 0; **zero skips** |
| **G2.3** DO-NOT-TOUCH 4a + 4b | Class 4a & 4b | all 8 packs GREEN — **this is the C1/C2 non-interference gate** | collected == declared counts; the integration class must use `--override-ini="addopts=" -m integration` |
| **G2.4** existing site-1 pin | Class 1 | `tests/unit/repositories/test_pg_engine_utc_session.py` GREEN and **unmodified** | collected > 0 |
| **G2.5** DO-NOT-TOUCH 4b (PG-shaped) | Class 4b | the 2 integration pool packs GREEN under the house form | collected == declared counts (> 0) |

### 4.3 Phase 3 gates — the guard (F7) + audit receipt (F1)

| Gate | Command form | Assertion | Non-vacuity |
|---|---|---|---|
| **G3.1** guard pack (read-cycle) | Class 3 | F7-a: after a read-only cycle, `idle in transaction` == 0 | collected == `EXPECTED_COLLECTED_GUARD` and > 0; **zero skips** |
| **G3.2** churn/load leg | Class 3 | F7-b: 0 at every sample over N = 10 min; peak == 0 | collected == `EXPECTED_COLLECTED_CHURN` and > 0 |
| **G3.3** guard self-pin | Class 1 | F7-c: the guard pack still contains its assertion + count constant | collected == `EXPECTED_COLLECTED_SELFCHECK` and > 0 |
| **G3.4** audit receipt | `audit-method.md` recipe | the census re-run reproduces the `19fc11c8b` counts (§`audit-method.md` §6) and 0 unclosed | per-family counts printed and diffed against the baseline constants |
| **G3.5** whole-tree + DO-NOT-TOUCH | default `pytest` + 4a + 4b | net-new FAILED == ∅; all 8 DO-NOT-TOUCH packs GREEN | base and head FAILED lists diffed in full |

### 4.4 Cross-phase gate rules (binding on all three phases)

1. **BASE reproduction is a gate, not a hope.** Each repro leg MUST be run against the **true BASE**
   (`19fc11c8b`, checked out into a scratch worktree — never the shared checkout) and MUST show the
   documented BASE signal (idle-in-txn > 0 / retained `checkedout()` / aborted-transaction raise). **A leg that
   does not go RED at BASE is not a valid regression test** — it must be repaired or removed before it can gate
   anything. (Blueprint "[TESTING]": net-new-failure discipline — a fix is only proven against a reproduced base.)
2. **The same command, both sides.** BASE and HEAD runs use the identical command lines, the identical scratch
   PG cluster construction, and the identical seed data.
3. **No gate reports "0 failed" without the collected-count assertion** (§2.3).
4. **No gate reports GREEN with skips** in the PG classes.
5. **The guard's own `pg_stat_activity` assertion must not be the *only* proof** — F7-a/F7-b are backed by the
   behavioural pins (F3-a, F4-a) so the guard cannot be the sole line of defence.
6. **Whole-tree net-new** is computed as a **full FAILED-list diff** against the TRUE base — not a count diff.

---

## 5. Open questions for the plan owner

1. **The F4 bound.** §1.2 asserts "returns to baseline within the fix's bound" but the bound is a **plan-owned
   constant**. The pin cannot be authored until the plan names it (candidates: the probe budget + 1 s; or a
   dedicated server-side `statement_timeout` value). **Blocking for pin authoring, not for the strategy.**
2. **The F3 fix shape** (drop the BEGIN vs move it inside the try) determines whether F3-d is the "string
   absent" assertion or the "BEGIN inside the inner try" AST assertion. Both are authored; the plan selects.
3. **Scratch-PG port.** §1.0 proposes `5433`; the host already has a `localhost:5433` listener recorded in the
   blueprint's credential inventory (`pg18_rancher_local`). If 5433 is occupied, the plan must choose another
   free port **and** record it — a collision would silently point the repro at a non-scratch cluster.
4. **Churn duration N.** Recommended 10 min; the plan may raise it. The pack's `@pytest.mark.timeout` must be
   re-derived whenever N changes.
5. **F6's idempotency shape.** `ON CONFLICT … DO NOTHING` returns no row, so the mint still needs the existing
   re-resolve probe to hand back the winning epoch (research-leak-sites.md:140). If the plan instead chooses
   `DO UPDATE … RETURNING`, the F6-a pin must be re-checked against that shape (the behavioural assertion —
   "no IntegrityError, exactly one OPEN epoch" — is shape-agnostic and survives either).

---

## 6. References

- `research-leak-sites.md` — the six-site verification (A CONFIRMED, B CORRECTED, C CORRECTED, D CONFIRMED (timeout path), E REFUTED-as-leak, F CONFIRMED)
- `research-pool-couplings.md` — engine inventory, knob matrix, DO-NOT-TOUCH list, C3 runner mechanics
- `research-audit-census.md` — the ≈689-borrow census, the RISK table (R1/R2/R3), the sweep-coverage map
- `audit-method.md` — the re-runnable audit procedure and the census baseline counts
- `wi-78967dba-verdict.md` — the missed-finalize verdict (read before signing off §4.4 gate G1.4)
- `/home/nea/ensemble-src/.agents/shared/incident-20261026-db-pool-idle-in-transaction.md` — the RCA (read-only; the main checkout)
- `.agents/shared/planning/soak-gate-mcp-warmup/requirements.md` §4.4 (P4), §4.6 (P6) — the normative leak class + the house runner/count rules
- `.agents/shared/planning/soak-gate-mcp-warmup/risk-safety-register.md` R-MASK-4 — the leak class the grace refuses
- `.agents/shared/planning/soak-gate-mcp-warmup/plan-overview.md:362-366` — the C3 false-green lesson
