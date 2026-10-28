# Test Strategy — DB pool poisoned by leaked idle-in-transaction connections (FOLDED)

- **Lane:** `fix/db-pool-idle-txn` · job `634c38f2` · branch `fix/db-pool-idle-txn`
- **BASE anchor:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (verified via `git rev-parse HEAD` in `/home/nea/ensemble-src-wt-dbpool-idletxn`)
- **HEAD anchor:** the post-fix commit produced by this lane (re-derived at gate time)
- **Companion artifacts (same directory, written in parallel — cross-reference by filename only):**
  `plan-overview.md` · `decisions.md` · `phase1-plan.md` · `phase2-plan.md` · `phase3-plan.md`
  · `research-leak-sites.md` · `research-pool-couplings.md` · `research-audit-census.md` · `audit-method.md` · `wi-78967dba-verdict.md`
  · `final-implementation-spec.md` (BINDING CONTRACT — this strategy implements its §5 pin inventory and §6 gate ladder)
- **Status:** FOLDED — incorporates the 12 CRITICAL corrections from `council-report.md` (C-1..C-12) and the leader rulings. Planning only, zero code changes.
- **Interpreter binding (C-1, W-10):** `/home/nea/ensemble-src/.venv/bin/python` (pytest 9.0.2, py 3.13.15; `import daemon` resolves to the **worktree** when cwd = worktree). **`uv run` is DROPPED** (`uv` is off-PATH at `/home/nea/.local/bin/uv`; the worktree has no `.venv` ⇒ network-dependent sync at gate time). **`/usr/bin/python3` is DROPPED** (no pytest installed ⇒ the C3 fence reads COLLECTED=0 ⇒ FATAL). **Gates MUST NOT run from the shared checkout.**

---

## 0. Scope, fix-ID map, and what this strategy can/cannot prove

### 0.1 Fix-ID map (carried from the lane dispatch; IDs are stable across the lane's artifacts)

| Fix ID | Subject | Research verdict it discharges | Primary artifact |
|---|---|---|---|
| **F1** | Systematic borrow-site **audit** (≈689 borrows) | CLEAN/RISK classification; 0 unclosed borrows | `audit-method.md` |
| **F2** | **Pool hardening** on the manager engine (site 1) | `reset_on_return` / `pool_recycle` / server-side PG timeouts are ABSENT today | this file §1.2, §3 pins F2-a..j |
| **F3** | `ens_db_repair_execute` dry-run explicit `BEGIN` | **CONFIRMED** leak mechanism (site A) | this file §1.1, §3 pins F3-a..e |
| **F4** | Readiness probe **timeout-path connection retention** | **CONFIRMED** (site D); per-tick leak hypothesis REFUTED | this file §1.2, §3 pins F4-a..e |
| **F5** | wedge-capture `tasks` → `task` | **CONFIRMED** (site F) | this file §3 pins F5-b..c |
| **F6** | `mint_boot_epoch_if_none` UniqueViolation | **REFUTED as a leak**, but the 4×/boot logic defect is real | this file §3 pins F6-a..e |
| **F7** | **Regression guard** (the RCA's fix-item 7) | "no test anywhere asserts zero idle in transaction" (research-pool-couplings.md:175) | this file §1.3, §3 pins F7-a..e |
| **F8** | Remove the **borrow-then-await** from the deferred-report reconcile (the lane's only mechanism item; A1 + C-5 + C-6) | M-R3 (λ≈0.161/s, ≈1.3 slots, ≈22% of the observed 6) | `final-implementation-spec.md` §1 F8.1–F8.9, §3; this file §3 pins F8-a..e |

### 0.2 What this strategy CAN prove

1. That the **confirmed** mechanisms (F3 BEGIN-outside-try; F4 probe-timeout retention) produce a measurable `idle in transaction` population on a real scratch PostgreSQL, and that the fixed code produces none.
2. That F8 removes the borrow-then-await hold (F8-b, F8-c, F8-d, F8-e) and that the guard's occupancy invariant (F7-e) would have caught the incident.
3. That the fix does not regress the soak-gate grace/leak surfaces (the DO-NOT-TOUCH packs stay green).
4. That the guard itself is not vacuous (collected-count discipline + self-pins + occupancy invariant).
5. The **Phase-0 empirical baseline** (§1.4) that settles the flip condition, the F7 invariant definition, and the 3-pool attribution census.

### 0.3 What this strategy CANNOT prove (disclosed, do not overstate in the gate report)

- **Two of the RCA's four `pg_stat_activity` fingerprints are NOT reproducible at BASE.** The RCA table (`incident-20261026-db-pool-idle-in-transaction.md:56-61`) lists `SELECT instances.instance_id, ...` (×2) and `SELECT count(*) FROM job_queue_items ...` (×1). Research CORRECTED both: the `/api/instances` ORM read path is context-managed (`daemon/repositories/instance/repository.py:1383` / `:1434`, inside `with SQLModelSession(...)`), and the only render-match for the `job_queue_items` count is `defer_block_resolver._DEFER_PENDING_COUNT_SQL` (`:185-191`) executed at `:274` and `:321` — both inside `with ... engine.connect()`. **There is no never-closed borrow at either site at BASE** (research-leak-sites.md:82, :108; research-audit-census.md:73-82). ⇒ A churn leg built ONLY from those two paths is expected to show **0** idle-in-txn at BASE. A BASE-demonstrating leg MUST include the confirmed mechanisms (F3, F4). §1.3 designs the composite leg accordingly.
- **Production load shape.** The RCA's "0 → 6 backends in ~3 min" is a production figure under real resume-burst load; the lab rate on a scratch cluster is NOT the same number and must not be asserted as such.
- **Pool starvation without aborted transactions** is a *different* mechanism (the Oct-26 checkpoint-pool saturation, `analysis-20261026-v0278-soak-failure.md`) and is NOT this lane's target.
- **The recurrence precondition is NOT removed by this lane.** The dominant term is **sync DB work on the event loop** (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) and the **BFS depth-loop** transaction span (`daemon/repositories/instance/repository.py:1434-1645`). The guard's occupancy invariant (F7-e) is what will see the recurrence; the follow-up commission owns the residual (leader ruling 6).
- **F8's flip condition is a TEST-TIME determination** (leader ruling 1). The architect's "Confidence: Medium-High" is unearned until the measurement exists. This strategy instruments for it (§1.4, §4.4) and does not resolve it on paper.

---

## 1. (a) RELAPSE REPRO STRATEGY on scratch PostgreSQL

### 1.0 The scratch-PG harness (binding for every leg below)

**No Docker.** Docker is BANNED in this repo (`docker-compose.test.yml` is unusable — blueprint "[INSTALL+PG]"). Use a **native disposable PostgreSQL cluster** on the host. **Per-variable env scrub only** — `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names). Test scripts must NEVER resolve live-shaped HOME-derived install paths (mktemp FAKE_HOME + escape guard).

```bash
# One-time, host-local, throwaway cluster (initdb -A trust; NOT a docker volume)
export PGBIN="$(dirname "$(readlink -f "$(command -v initdb)")")"
export PGDATA="$(mktemp -d /tmp/dbpool-pg.XXXXXX)"
"$PGBIN/initdb" -A trust -U postgres -D "$PGDATA" >>"$PGDATA/initdb.log" 2>&1
"$PGBIN/pg_ctl" -D "$PGDATA" -o "-p 5433 -k $PGDATA" -l "$PGDATA/pg.log" -w start
```

> **Named-cluster note:** the blueprint records a pre-existing `localhost:5433` listener and a `pg18_rancher_local` role; **use a distinct port** (`5433`) and a distinct role name so a stray ambient `POSTGRES_*` can never point the repro at a non-scratch database.

Per run, create a **fresh scratch database + throwaway LOGIN role**, and drop both at teardown:

```bash
export PGPORT=5433
export PGROLE="dbpool_repro"
export PGPASSWORD="dbpool_repro_pw"
"$PGBIN/createdb" -h /tmp -p "$PGPORT" -U postgres -O postgres "dbpool_${RUN_ID}" 2>/dev/null || \
  "$PGBIN/createdb" -h 127.0.0.1 -p "$PGPORT" -U postgres -O postgres "dbpool_${RUN_ID}"
"$PGBIN/psql" -h 127.0.0.1 -p "$PGPORT" -U postgres -d "dbpool_${RUN_ID}" \
  -c "CREATE ROLE $PGROLE LOGIN PASSWORD '$PGPASSWORD'; GRANT ALL ON DATABASE \"dbpool_${RUN_ID}\" TO $PGROLE;" \
  -c "GRANT USAGE, CREATE ON SCHEMA public TO $PGROLE;"
```

Teardown (mandatory, even on failure): drop the scratch DB + role, stop the cluster, remove `$PGDATA`. Never leave a scratch cluster running.

### 1.1 LEG A — Site A / F3 (`ens_db_repair_execute` dry-run explicit `BEGIN`)

(unchanged from the pre-fold strategy — re-verified against `final-implementation-spec.md` §1 F3.1–F3.5)

- **Mechanism:** `daemon/tools/ens_db_tools.py:1479` / `:1491` — `session.execute(text("BEGIN"))` inside the **outer** try at `:1475`, but the **rollback** lives only in the inner except at `:1489`/`:1499`; the outer except at `:1513` has **no** rollback. A failed DDL rollback escapes to the outer handler with the connection still in a transaction.
- **BASE expectation:** a dry-run whose `BEGIN` raises leaves the connection in `idle in transaction` (F3-a, F3-b, F3-c).
- **HEAD expectation:** after the fix (rollback in `finally:`), the connection is clean (`conn.in_transaction() is False`).
- **Pin:** F3-a..e (§3.1).

### 1.2 LEG D — Site D / F4 (readiness probe timeout-path connection retention)

(unchanged from the pre-fold strategy — re-verified against `final-implementation-spec.md` §1 F4.1–F4.7)

- **Mechanism:** `daemon/services/readiness.py:1476-1481` (`make_db_probe`) and `:1746-1789` (`make_queue_probe`) issue no server-side bound; a probe that blocks on a held lock keeps the connection checked out for the full `pool_timeout=30 s`.
- **BASE expectation:** under a held lock, `engine.pool.checkedout()` does not return to baseline within the probe budget; the backend appears as `state='active'` + `wait_event_type='Lock'` (NOT `idle in transaction` — see C-7).
- **HEAD expectation:** with `SET LOCAL statement_timeout = 2 × budget` (1000 ms / 4000 ms) issued as the **FIRST** statement, the statement is cancelled (QueryCanceled), the `to_thread` future returns, the connection is returned to the pool, and `engine.pool.checkedout()` returns to baseline.
- **Pin:** F4-a..e (§3.2). **F4-b's mutation is corrected (C-7):** remove the `SET LOCAL statement_timeout` from `make_queue_probe` (the statement stays blocked, the connection stays checked out ⇒ RED). The old mutation (removing an engine-level knob F4 does not install) was a no-op. The old assertion "0 idle-in-txn backends" was TRUE AT BASE because `idle in transaction` and `wait_event_type='Lock'` are mutually exclusive.

### 1.3 LEG C — Black-box churn + census (the F7 guard's load leg)

(unchanged mechanism; the **invariant is corrected (C-10)** — see §4.1)

- **Composite leg:** drive a resume-burst load that exercises F3 + F4 + F8 paths; sample `manager.engine.pool.checkedout()` at ≤ 1 Hz (in-memory) and `pg_stat_activity` by `application_name` with `state_change` + `xact_start` + `backend_start`.
- **BASE expectation (reproduction guard):** ≥ 1 manager-pool idle-in-txn backend AND `pool.checkedout()` above baseline. If BASE cannot demonstrate the F8 shape, the guard is a **regression** guard, not a **reproduction** guard — say so (A10) rather than forcing a BASE demo.
- **HEAD expectation (regression guard):** `pool.checkedout()` returns to baseline; no backend's idle-in-txn residence exceeds `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`.
- **Pin:** F7-a..e (§3.7).

### 1.4 Phase-0 empirical baseline (leader ruling 5) — MEASURE BEFORE BUILDING

**This is a BINDING PRECONDITION.** The council had **no DB access**; every PG-semantics claim in the plan (last-`-c`-wins, idle-in-txn vs lock-blocked exclusivity, `idle_in_transaction_session_timeout` firing thresholds, residence times, `application_name` attribution) is a **documentation claim**, not a measurement. Phase 0 runs on a **SCRATCH PostgreSQL database** (lane-created, dropped at the end — **NEVER** `ensemble_prod`, **NEVER** the live daemon's DB, **NEVER** a restart of anything), with **PER-VARIABLE env scrubbing** (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT`; `env -u POSTGRES_*` is a silent NO-OP). Test scripts must NEVER resolve live-shaped HOME-derived install paths (mktemp FAKE_HOME + escape guard).

**Phase-0 deliverable: `BASELINE.md`** in the planning directory, produced BEFORE any implementation begins, containing measurements (a)–(d):

**(a) Base-code M-R3 hold duration under real load** — settles the flip condition (leader ruling 1). Drive `_handle_recover_deferred_report_async` (and the sync twin) against scratch PG with a deliberately slowed content fetch (instrumented checkpointer whose `aget_tuple` sleeps a known duration, or a lock on the checkpoint table in the saver's DB — **leader ruling 4 prefers the instrumented checkpointer; the table-lock is the fallback**). Sample `manager.engine.pool.checkedout()` at ≤ 100 ms across the reconcile call. Record: measured max legitimate hold, P100, and the resulting `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS = 4 × measured max hold` (floor 30 s). If unmeasurable, the bound ships **DISABLED** (constant defined, not applied).

**(b) The occupancy/residence census shape at base** — what F7 must assert. Sample `pg_stat_activity` filtered by the scratch DB's `datname`, capturing `count`, `state_change`, `xact_start`, `backend_start`, `application_name`, and `state`/`wait_event_type`. Report residence-time buckets (distinguish "6 short borrows" from "1 long leak"). This measurement **defines** the F7 invariant's thresholds/formula (C-10) — they are fixed from measurement, not guessed.

**(c) Measured PG runtime semantics:**
- `last_query` fingerprints per pool (manager / repair / asyncpg adapter) — settles the A-1 attribution question.
- `application_name` attribution per pool — confirms the three named engines (F2.3, F2.5, F2.6) are distinguishable in the census.
- Whether `idle_in_transaction_session_timeout` actually fires as documented (threshold + SQLSTATE `25P03`).
- Last-`-c`-wins behaviour under the daemon's exact connect strings (confirms D-17's isolation).
- `SET LOCAL statement_timeout` semantics inside SQLAlchemy's autobegin (confirms F4's probe-scoped bound lands on the observed pool).

**(d) The 3-pool attribution census** explaining the incident's 18 observed backends vs the 15-slot manager cap: count `pg_stat_activity` rows by `application_name` (and by `datname` for the scratch DB) across manager / repair / asyncpg-adapter / P2-oracle pools. Multi-pool ⇒ unattributable without `application_name` ⇒ confirms F2.3/F2.5/F2.6.

**Phase-0 gate:** Phase 0 must COMPLETE (BASELINE.md written + reviewed) before Phase 1 implementation begins. The sibling plan worker owns `phase0-plan.md` and the BASELINE.md contract — align by fixed filename `BASELINE.md`.

### 1.5 Attribution method (A3, A5, C-2, C-8) — F7 guard's census attribution

1. **Scratch-database isolation (D-23/D-24)** — the guard creates its own uniquely-named database, points the daemon's engine factory at it, and asserts on `pg_stat_activity WHERE datname = <that db>`. Every backend in that database belongs to the test. **This is the guard's attribution and it stays.**
2. **`application_name` where present, else scratch-DB isolation** — the census is attributed *"by `application_name` where present, else by scratch-DB isolation"*. (The old wording "by `application_name` (post-F2)" is **wrong** if D-24 stands — restated here.) The three engines carry a name: `ensemble-manager` (F2.3), `ensemble-repair` (F2.5), `ensemble-checkpoint-adapter` (F2.6). The **shared** `PG_SESSION_CONNECT_ARGS` must **never** carry `application_name` (D-17).
3. **Every census captures** `count` + `state_change` + `xact_start` + `backend_start` + `application_name`, and reports **residence-time buckets**, not just a count. The `backend_start > run_start` BASE filter is **prescribed** at `test-strategy.md` §1.3 and must be **implemented** in the helper's SQL.
4. **PG state `idle` is a disclosure metric**, not an assertion (C-10).

---

## 2. (b) RUNNER FORM + C3 DISCIPLINE (false-green)

### 2.1 The false-green mechanism this section exists to defeat

`pyproject.toml:95` sets `addopts = "-m 'not integration and not postgres and not slow and not conformance'"`. ⇒ A bare `pytest tests/<anything>/` deselects the very tests it names and reports "0 failed" with nothing executed. The soak lane recorded this as the C3 lesson; the binding house rule is `requirements.md:1133` and `:1169-1172`.

### 2.2 The EXACT command form for each pack class

**Interpreter (C-1, W-10):** `/home/nea/ensemble-src/.venv/bin/python` (verified: pytest 9.0.2, py 3.13.15; `import daemon` resolves to the **worktree** when cwd = worktree). **Drop `uv run`** — `uv` is off-PATH at `/home/nea/.local/bin/uv` and the worktree has no `.venv` ⇒ network-dependent sync at gate time. **Drop `/usr/bin/python3`** — no pytest installed ⇒ the C3 fence reads COLLECTED=0 ⇒ FATAL. **Gates MUST NOT run from the shared checkout.**

**Certification requirement (blueprint "[INSTALL+PG]"):** the implementer MUST verify `import daemon` resolves **inside the worktree** (`python -c "import daemon, os; print(os.path.dirname(daemon.__file__))"`) and record the resolved path in the gate report before any pack is trusted. **NEVER reuse the main `.venv` for runs in the shared checkout.**

**Class 1 — default-run unit packs (no marker; collected by the default `pytest`).**

```bash
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/unit/tools/test_dbpool_txn_hygiene.py \
  tests/unit/tools/test_dbpool_pool_knobs.py \
  tests/unit/tools/test_dbpool_probe_retention.py \
  tests/unit/tools/test_dbpool_guard_pack_selfcheck.py \
  -q
```

**Class 2 — PG packs under `tests/postgres/` (auto-marked `postgres`; SERIAL ONLY).**

```bash
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB="dbpool_${RUN_ID}" \
PG_TEST_USER="$PGROLE" PG_TEST_PASSWORD="$PGPASSWORD" \
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/postgres/test_dbpool_site_a_repro_pg.py \
  tests/postgres/test_dbpool_site_d_retention_pg.py \
  --override-ini="addopts=" -m postgres -q --timeout=300
```

Mechanics (research-pool-couplings.md:148-152): `tests/postgres/conftest.py:82-95` auto-applies the `postgres` marker to every test in that directory; `:44-45`/`:94-95` **skip every postgres test under xdist** ⇒ **serial only**; never run this class with `-n`; the session `pg_engine` fixture does `drop_all` → `create_all` (`:187-188`) and tears down with `drop_all` + `dispose` (`:191-195`); an autouse per-test `TRUNCATE … RESTART IDENTITY CASCADE` runs (`:240-258`); DSN vars: `PG_TEST_HOST` / `PG_TEST_PORT` / `PG_TEST_DB` / `PG_TEST_USER` / `PG_TEST_PASSWORD`.

**Class 3 — integration packs under `tests/integration/` (opt-in `pytestmark = pytest.mark.integration`).**

```bash
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB="dbpool_${RUN_ID}" \
PG_TEST_USER="$PGROLE" PG_TEST_PASSWORD="$PGPASSWORD" \
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/integration/test_dbpool_churn_census.py \
  tests/integration/test_dbpool_readcycle_guard.py \
  --override-ini="addopts=" -m integration -q --timeout=900
```

`tests/integration/conftest.py` does **NOT** auto-mark (soak `phase1-plan.md:61`) — the pack file itself must carry `pytestmark = pytest.mark.integration` (precedent: `tests/integration/test_checkpoint_pool_burst_shapes.py:49`, `.../test_checkpoint_pool_ghost_waiters.py:58`).

**Class 4 — the DO-NOT-TOUCH regression suite (§3.4).** Mixed classes ⇒ **two commands** (one per marker), never one.

```bash
# 4a — the 6 default-run unit packs (default addopts are FINE here: no marker ⇒ collected)
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/unit/tools/test_readiness_failure_class.py \
  tests/unit/tools/test_readyz_grace_detail.py \
  tests/unit/tools/test_saver_probe_contention_class.py \
  tests/unit/tools/test_promote_txn_hold.py \
  tests/unit/tools/test_checkpoint_pool_config.py \
  tests/unit/test_readiness_mcp_independence.py -q

# 4b — the 2 integration packs (house form; MUST NOT use the default addopts)
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/integration/test_checkpoint_pool_burst_shapes.py \
  tests/integration/test_checkpoint_pool_ghost_waiters.py \
  --override-ini="addopts=" -m integration -q --timeout=300
```

### 2.3 The expected-collected-count assertion (the non-vacuity fence)

**Rule (binding, `requirements.md:1133` + `:1169-1172`):** *no gate may report "0 failed" without also proving a non-zero collected count equal to the pack's DECLARED count.* "0 failed" alone is vacuous — it is the exact output a fully-deselected run produces.

**Mechanism.** `pytest <paths> --collect-only -q` prints one line per collected test id plus a summary tail of the form `42 tests collected in 1.23s`.

> **GNU grep trap (blueprint "[TESTING]"):** `grep -E "a\|b"` is BROKEN — under ERE `\|` is a *literal pipe*. Use a plain `|` under `-E`. The pattern below uses no alternation and is therefore safe; if alternation is added, use `|` not `\|`.

**Exact assert form** (run BEFORE the real run, and its output pasted into the gate report):

```bash
#!/usr/bin/env bash
set -euo pipefail
# $1 = declared expected count, $2 = marker (or "" for default), $3.. = paths
EXPECTED="$1"; MARKER="$2"; shift 2
ARGS=()
[ -n "$MARKER" ] && ARGS+=(--override-ini="addopts=" -m "$MARKER")
COLLECTED=$(/home/nea/ensemble-src/.venv/bin/python -m pytest "$@" "${ARGS[@]}" --collect-only -q 2>/dev/null \
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

**Pack-defined constants.** Each pack declares its count as a module-level constant **inside the pack** so the number cannot drift from the code:

```python
# tests/unit/tools/test_dbpool_txn_hygiene.py
EXPECTED_COLLECTED = 9   # pack-defined; fixed once authored
```

and the pack carries a **self-check test** that asserts the pack's own collected count equals the constant (§3, pin F7-c). The gate script then uses the pack's constant as `EXPECTED`.

**Additional non-vacuity rules that apply to every pack:**
- **A pack that can be silently skipped is a red gate.** `tests/postgres/conftest.py` **skips the whole module** when PostgreSQL is unreachable (`:167-169`). The gate script MUST therefore assert the run did not produce skips: parse the summary line and fail if `skipped > 0` for the PG classes. **A green PG gate that skipped is a FAILING gate.**
- `--timeout=N` on every long pin (house convention, 115 usages; `pyproject.toml:97-98` default is 30 s and the kill is `os._exit(1)` ⇒ no traceback). Every new long pin carries an explicit `@pytest.mark.timeout(N)` with N > its worst case.
- The churn pack's `@pytest.mark.timeout` must exceed `N*60 + 300`.
- **The first step of every gate: print the interpreter certification** — `python -c "import daemon,pytest; print(daemon.__file__, pytest.__version__)"` — and attach it to the gate report. `collected == 0` ⇒ **FATAL**, never "0 failed ⇒ pass".
- **The whole-tree legs (G1.5, G3.5) must assert `collected > 0` on BOTH sides** and reconcile `base_collected` / `head_collected` against the sum of the declared `EXPECTED_COLLECTED_*`; a **mismatch is FATAL**. "The base run's collected count is *recorded*" is **not** an assertion.

---

## 3. (c) MUTATION-MEANINGFUL PIN LIST

**Discipline (blueprint "[DISCIPLINE]"):** *regression pins MUST be mutation-verified.* A pin nothing can redden is dead weight. Every pin below states **the exact mutation that turns it RED**. Where a mutation is impossible, that is stated as a residual. **Every PG/integration pin has a default-collected static twin** (W-13) so deleting the PG pin leaves the default run red.

### 3.1 F3 — `ens_db` dry-run BEGIN hygiene (Site A)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F3-a** | After a dry-run whose `BEGIN` raises, the connection is **not** in a transaction (`conn.in_transaction() is False`) and a further statement on it does **not** raise *"current transaction is aborted"*. | Revert the fix so the BEGIN sits **outside** the inner try (restore the `:1479`/`:1491` shape) → the outer except at `:1513` runs with no rollback → the connection stays in a txn → **RED**. |
| **F3-b** | The dry-run's failure path performs a **rollback** (assert the rollback is observable: the session's transaction is None AND a sentinel `rollback` spy fired). | Delete the rollback from the outer-except path (re-introduce the *outer-except-no-rollback* shape) → **RED**. |
| **F3-c** | The DDL-branch `session.rollback()` failing (`:1488`) also leaves the connection clean. | Remove the outer-path rollback handling for the rollback-failure variant → **RED**. |
| **F3-d** | The dry-run's `previewed` audit row is written in a **separate session** and is not rolled back by the dry-run transaction. | Re-introduce the audit row **inside** the rolled-back transaction (violates the separate-session contract at `:1530-1538`) → **RED**. |
| **F3-e** | Agent-visible preview strings and docstrings reflect the new dry-run wording. | Change the preview string at `:1482` → the wording assertion REDs (one concrete edit, A-8). |

### 3.2 F4 — readiness probe retention (Site D)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F4-a** | `make_db_probe` issues `SET LOCAL statement_timeout = 2 × DB_PROBE_TIMEOUT_S` (1000 ms) as the **FIRST** statement, dialect-guarded (PG only); `make_queue_probe` issues 4000 ms; the saver probe is **excluded**. | Remove the `SET LOCAL statement_timeout` from `make_db_probe`; or change the derived value so it is no longer `2 × DB_PROBE_TIMEOUT_S`; or add `SET LOCAL` to the **saver** probe (the exclusion assertion REDs). |
| **F4-b** | Under a held lock, `make_queue_probe`'s statement is cancelled (QueryCanceled), the `to_thread` future returns, the connection is returned to the pool, and `engine.pool.checkedout()` returns to baseline within the bound. | **Remove the `SET LOCAL statement_timeout` from `make_queue_probe`** — the statement stays blocked, the connection stays checked out, `engine.pool.checkedout()` does not return to baseline ⇒ RED. *(C-7 correction: the old mutation — removing an engine-level knob F4 does not install — was a no-op; and the old assertion "0 idle-in-txn backends" was TRUE AT BASE because `idle in transaction` and `wait_event_type='Lock'` are mutually exclusive.)* |
| **F4-c** | The probe's returned `(verdict, failure_class)` pair stays `probe_timeout` (not hard-component-red) — the 2× bound keeps the client-side `asyncio.wait_for` firing first. | Change the probe's returned pair, or reclassify a timeout as hard-component-red. |
| **F4-d** | Readiness detail fields / serialization unchanged (DO-NOT-TOUCH). | Remove a detail field. |
| **F4-e** | The docstring at `readiness.py:107-113` no longer carries the false premise ("the driver's own connect/execute timeout releases it"). | Restore the false docstring premise. |

### 3.3 F2 — pool hardening on the manager engine (site 1)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F2-a** | `pool_reset_on_return="rollback"` is explicit on the manager engine. | Remove the explicit `pool_reset_on_return` kwarg. |
| **F2-b** | `pool_recycle=3600` is set. | Drop `pool_recycle`. |
| **F2-c** | `idle_in_transaction_session_timeout=60000` is in the manager options string. | Drop `idle_in_transaction_session_timeout` from the manager options. |
| **F2-d** | `pool_pre_ping=True` stays. | Remove `pool_pre_ping`. |
| **F2-e** | Manager options string equals `-c timezone=UTC -c application_name=ensemble-manager -c idle_in_transaction_session_timeout=60000` (order-pinned; **amended** per C-9 — the exact-equality assertion at `tests/unit/repositories/test_pg_engine_utc_session.py:86-88` is amended, `:65` stays untouched). | Replace the manager options string (order-sensitive). |
| **F2-f** | The **shared** `PG_SESSION_CONNECT_ARGS` does NOT carry `application_name` or a timeout (D-17 guard). | Extend the shared constant with an `application_name` or a timeout ⇒ the repair engine's rendered string changes ⇒ RED. |
| **F2-g** | Three engines carry **distinct** `application_name` values (`ensemble-manager`, `ensemble-repair`, `ensemble-checkpoint-adapter`); each appears exactly once; the two differ. | Change either engine's value; or make the two equal; or make either appear more than once. |
| **F2-h** | The shared `conn_string` at `persistence.py:225` does NOT carry `application_name` (P2-immutability + separate-DSN guard). | Add `application_name` to the shared `conn_string` ⇒ the P2 oracle's rendered conninfo changes ⇒ RED. |
| **F2-i** | Both honesty blocks (`constants.py:207-217`, `persistence.py:306-315`) state "retention/transaction-hygiene REPAIRED; DWELL/OCCUPANCY NOT REPAIRED" — not "the P1 leak is REPAIRED". | Restore the false honesty-block wording. |
| **F2-j** | P2 saver pool construction (`persistence.py:316-331`) knobs are byte-identical. | Change any knob of the P2 saver pool. |

### 3.4 DO-NOT-TOUCH regression pins (C1/C2 non-interference)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| (existing 8 packs) | soak-gate grace/leak surfaces untouched | (each pack's own mutation) |

### 3.5 F5 — wedge-capture table rename (Site F)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F5-b** | The wedge-capture queries use `FROM task` (not `tasks`). | Re-introduce `FROM tasks`. |
| **F5-c** | Static twin: no `tasks`/`tasks t` in the module. | Re-introduce `FROM tasks` / `tasks t`. |

### 3.6 F6 — mission mint idempotency (Site E)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F6-a** | The boot mint uses an upsert with `on_conflict_do_nothing` keyed on the anchor; on `rowcount == 0` it re-resolves via the anchor-scoped probe. | Replace the upsert with the bare ORM insert; or change the re-resolve key from the anchor to `founding_message_id`. |
| **F6-b** | Idempotency holds on a real scratch PG (re-running the boot mint does not raise). | Remove the idempotency (revert to a bare INSERT). |
| **F6-c** | The target DB **has** `ux_mission_records_anchor_open` with a byte-identical predicate (W-4). | Drop the partial unique index from the target DB ⇒ the upsert raises 42P10 ⇒ the negative leg REDs. |
| **F6-d** | Gate (process): no new `.sql` file AND no new ORM TABLE/COLUMN/INDEX (A-6). | Introduce a new `.sql` file or a new ORM TABLE/COLUMN/INDEX. |
| **F6-e** | Agent-visible preview strings reflect the new dry-run wording (one concrete edit, A-8). | Change the preview string at `:1482`. |

### 3.7 F7 — the regression guard (self-pins) — INVARIANT CORRECTED (C-10)

**The old "count == 0 at every sample" invariant is WITHDRAWN.** It is red for a healthy busy daemon and green for a slow leak. F7 must assert **both**, co-equal:

- **(a) Occupancy returns to baseline** — after a churn cycle, `manager.engine.pool.checkedout()` returns to its baseline. In-memory, no DB round trip (the sampler shape already exists at `daemon/services/readiness.py:525-549`). **This is the invariant that would have caught the incident** — the census alone would not (C-10): a connection checked out **before its first statement** is PG state `idle`, **not** `idle in transaction`; the incident's shape was 15/15 slots held with only 6 in `idle in transaction` and 9 idle-but-checked-out ⇒ a census-only guard would be **GREEN on the incident**.
- **(b) No backend's idle-in-txn residence exceeds the budget** — `now() - state_change <= DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`. **Not** a count assertion. PG state `idle` is a **disclosure metric**, not an assertion.

A count may be **reported** and **bounded** (`peak_occupancy ≤ λ_measured × budget`, with λ from the leg's own sampler) but never asserted to be zero.

**Residence budget constant:** `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S = 2.0` (post-F8 target) in `daemon/constants.py` (W-1). Source pinned to `state_change` (entry into the idle-in-txn state); `xact_start` is total transaction age and is a *different* quantity.

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F7-a** | Hold a manager-pool connection across a >budget dwell ⇒ the residence assertion REDs (re-specified as a **deliberate >budget hold** — W-1). | Hold a manager-pool connection across a >budget dwell. |
| **F7-b** | Re-introduce a retention path (e.g. return a connection with an open transaction). | Re-introduce a retention path. |
| **F7-c** | Self-pin: delete the guard's assertion **and** decrement the count constant ⇒ the self-pin re-reads the source ⇒ still **RED** (A-9). | Delete the assertion + decrement the constant. |
| **F7-d** | Static twin: delete / un-mark / move the guard file out of the `integration` placement. | Delete / un-mark / move the guard file. |
| **F7-e** | **The pin that would have caught the incident (C-10):** re-introduce the occupancy defect — hold 2 manager slots across the churn cycle ⇒ `pool.checkedout()` does not return to baseline ⇒ RED. | Hold 2 manager slots across the churn cycle. |

### 3.7.1 Static twins for F7-b and F7-e (DF-7 — W-13)

Every PG/integration pin must have a default-collected static twin (W-13). F7-d and F8-a already have one; F7-b and F7-e did not. Added:

| Static twin | File (proposed) | Marker | Static facts asserted (the facts that make the PG leg meaningful) | Plantable mutation that reddens it |
|---|---|---|---|---|
| **F7-b-static** | `tests/unit/tools/test_dbpool_probe_retention_static.py` | none (default-collected) | (1) `make_queue_probe` source contains `SET LOCAL statement_timeout`; (2) the derived value equals `2 * QUEUE_PROBE_TIMEOUT_S` (4000 ms); (3) the saver probe (`make_db_probe` for the saver pool) does NOT contain `SET LOCAL` (exclusion); (4) the docstring at `readiness.py:107-113` no longer carries the false premise. | Remove `SET LOCAL statement_timeout` from `make_queue_probe` (or add it to the saver probe, or restore the false docstring) → twin RED. |
| **F7-e-static** | `tests/unit/tools/test_dbpool_occupancy_static.py` | none (default-collected) | (1) no `ast.Await` / `run_coroutine_threadsafe(...).result(...)` inside the `with`-body of a manager-engine session scope in `daemon/` (F8-a shape, the three named sites); (2) the terminal guard and dead-parent guard are re-asserted inside the P2 write scope (F8.6); (3) `inj` is re-read in P2 (F8.7); (4) the message/task/injection writes commit in one transaction (F8.8). | Re-introduce an `await` inside the `with`-body (or drop the re-assertion / split the transaction) → twin RED. |

**Residual (none declared):** both twins are real and plantable; no pin is left without a twin. F7-a (residence assertion) already has its PG leg + the F7-c self-pin as its static backstop; F7-b and F7-e now have explicit static twins above. W-13 is satisfied for all F7 pins.

---

## 3.8 F8 — remove the borrow-then-await (NEW — was missing from the inventory, C-12)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **F8-a** | **Static (AST, default-collected):** no `ast.Await` node and no `run_coroutine_threadsafe(...).result(...)` inside the **`with`-body** of a manager-engine session scope in `daemon/`. Scope the rule to the `with`-body only (a function-wide rule false-positives on the legitimate bridge calls at `:11217-11224` / `:11568-11571`); **name the three expected sites** in the docstring so a fourth is a deliberate failure. | Insert an `ast.Await` or a `run_coroutine_threadsafe(...).result(...)` inside the `with`-body of a manager-engine session scope in `daemon/` ⇒ RED. |
| **F8-b** | **PG (integration):** drive the reconcile with a slowed content fetch; assert **no** manager-pool idle-in-txn backend attributable to the reconcile and `pool.checkedout()` back to baseline. | Drive the reconcile with a slowed content fetch; the assertion REDs if the hold remains. |
| **F8-c** | **PG (integration, new, C-5):** inject a concurrent escalation at the P0→P2 seam ⇒ the re-asserted terminal guard must produce **no** artefacts. | Inject a concurrent escalation at the P0→P2 seam. |
| **F8-d** | **PG (integration, new, C-5):** break the write scope's single-transaction commit (e.g. split the message/task/injection writes) ⇒ the atomicity assertion REDs. | Split the write scope's transaction. |
| **F8-e** | **PG (integration):** re-introduce the unbounded async-twin hold (remove its `wait_for`/bound). | Remove the async twin's bound. |

### 3.9 Audit-clean pins (the census families stay close-guaranteed)

| Pin | What it asserts | Mutation that MUST redden it |
|---|---|---|
| **G-A** | Census families match the §7.2 baseline (A=613 / C=4 / B=175 / D=148 / E=14 / F=0). | Any family count delta not classified per §7.3. |

### 3.10 Pin coverage matrix (all fix IDs covered, nothing orphaned)

| Fix ID | Pins |
|---|---|
| F1 | F1-a (static: `audit-findings.md` exists with AST baseline) |
| F2 | F2-a..j |
| F3 | F3-a..e |
| F4 | F4-a..e |
| F5 | F5-b..c |
| F6 | F6-a..e |
| F7 | F7-a..e |
| F8 | F8-a..e |

**Inventory rules:** every pin has a **concrete, plantable** mutation (no "any change that alters X"); every PG/integration pin has a **default-collected static twin** (W-13); every PG pin asserts **zero skips**; every pin asserts its **collected count**. **Total: 5 F3 + 5 F4 + 10 F2 + 2 F5 + 5 F6 + 5 F7 + 5 F8 + 1 F1 + audit pins = 38+ pins.**

---

## 4. (d) GATE STRUCTURE per phase

### 4.1 Phase order (from `final-implementation-spec.md` §6.1, plus F8)

| Phase | Contents | Gate |
|---|---|---|
| **P1** | **F8** (F8.1–F8.9) · F3 (F3.1–F3.5) · F4 (F4.1–F4.7) · F6 (F6.1–F6.6) · F1 (F1.1) | Transaction-hygiene + mechanism pins green; **no** change to the readiness detail schema, leak predicate, grace refusal path or never-green semantics |
| **P2** | F2 (F2.1–F2.10) · F5 (F5.1–F5.3) | Knob matrix pinned; **the engine-level idle-in-txn bound lands only after F8** |
| **P3** | F7 (§4) · the pin packs that freeze F3/F4/F6/F2/F5/F8 | The guard runs in a form the runner **executes** with an asserted collected count; net-new test reds = 0 |

**Sequencing constraints (hard):**
1. **F8 before the engine-level `idle_in_transaction_session_timeout`** (C-4, A6-i).
2. **F2's `application_name` change and the AC amendments land in ONE commit** (C-2) — AC14/AC15/AC16/T2.4/T2.6/D-19/D-24/G2.4/F2-f/F2-g all change together, or the gate is RED from birth.
3. **The `statement_timeout` DROP lands with the F2 change** (C-3) — D-16, T2.2, AC14 and the Phase-2 objective text.
4. **The asyncpg DSN (F2.6) lands with the F2 change** (C-8) — it must not touch the shared `conn_string`.

### 4.2 Canonical gate ladder (ONE ladder — W-6)

Use the IDs the architect's amendments reference (**G1.1…G3.5**), with an explicit mapping table from the phase-3 G0…G6 IDs. **ONE pack inventory**, with a static consistency pin asserting every gate ID referenced in `decisions.md` / `architecture-recommendation.md` exists in the canonical ladder (W-11).

| Gate | Contents | Collected-count assertion |
|---|---|---|
| **G1.1–G1.4** | F3, F5, F6 unit packs | `collected > 0` on both sides; `base_collected == head_collected` for the DO-NOT-TOUCH packs |
| **G1.5** | Whole-tree base vs HEAD | `collected > 0` on **both** sides (not "recorded"); `base_collected`/`head_collected` reconciled against the sum of declared `EXPECTED_COLLECTED_*`; mismatch = FATAL |
| **G2.1–G2.3** | F2, F4 packs | `collected > 0`; zero skips on PG packs |
| **G2.4** | `test_pg_engine_utc_session.py` amended per C-9 (diff attached) | amended, not "unmodified" |
| **G3.1–G3.4** | F7 guard packs | `collected > 0`; zero skips |
| **G3.5** | Whole-tree base vs HEAD | `collected > 0` on **both** sides; mismatch = FATAL |

**PG gate form:** `… /home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m postgres|integration <paths> -q` (pyproject.toml:95 deselects `integration`/`postgres`/`slow`/`conformance` by default). A PG gate **fails** on `skipped > 0`, not only on failures.

### 4.3 Cross-phase gate rules (binding on all three phases)

1. **Interpreter certification first** — print `python -c "import daemon,pytest; print(daemon.__file__, pytest.__version__)"` and attach to the gate report.
2. **Collected count asserted > 0 before "0 failed" is read as a pass.** `collected == 0` ⇒ FATAL.
3. **Per-variable env scrub** — `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT`. `env -u POSTGRES_*` is a silent NO-OP.
4. **`HOME="$(mktemp -d)"`** — never a HOME-derived live install path.
5. **PG gates assert zero skips.** A green PG gate that skipped is a FAILING gate.
6. **Every long pin carries `@pytest.mark.timeout(N)`** with N > worst case. The churn pack's timeout must exceed `N*60 + 300`.
7. **The census gate's non-vacuity cell** is restated as *"each family's count MUST equal its §7.2 baseline constant; any delta is a FAILURE until classified per §7.3"*, and the **diff** is the failing step (the scanner always exits 0 — OQ-7(c)).
8. **Net-new test reds = 0** across all phases (G3.4). Any new red must be classified as pre-existing (via the base-vs-HEAD byte-comm) before the gate passes.

---

## 5. Open questions for the plan owner

1. **Who folds the plan?** The architect wrote *"Status of the planner's artifacts: unmodified"* (`architecture-recommendation.md:9`). The fold must be performed by the planner, then re-reviewed. Until then the lane proceeds against contradictory ACs. (council-report.md §9.1)
2. **May F8 touch `daemon/tools/instance.py:1207`/`:1360`?** If no ⇒ pin (i) must be scoped narrowly and the residual disclosed (it is a write path under `WriteGuardSession`), and `plan-overview.md` §9 needs a `daemon/manager.py` row (W-8). If yes ⇒ the lane file manifest grows and the W-8 diff-review leg must land with it. (council-report.md §9.2, leader ruling 3)
3. **Sanctioned slow-content-fetch technique for the F8 PG pin:** instrumented checkpointer (leader ruling 4 preferred) vs checkpoint-table lock (fallback). Needed before the flip-condition leg can be written. (council-report.md §9.3, C-12)
4. **Does the `statement_timeout` DROP also change the Phase-2 OBJECTIVE text** (`phase2-plan.md:3`, `:16`)? The council's read: **yes** — the objective must change, not just AC14. (council-report.md §9.4, C-3)
5. **F8's instance.py sites:** if read and concluded to be a different fix shape, state the discrepancy explicitly in the final report so the dispatcher can reconcile — do NOT silently diverge. (dispatch instruction)
6. **Phase-0 harness ownership:** the sibling plan worker owns `phase0-plan.md` and the BASELINE.md contract; this strategy specifies the harness/legs (§1.4) — align by fixed filename `BASELINE.md`.

---

## 6. References

- `council-report.md` — the 12 CRITICAL + 14 WARNING + 9 ADVISORY findings (authoritative).
- `final-implementation-spec.md` — the BINDING CONTRACT (this strategy implements its §5 pin inventory and §6 gate ladder).
- `architecture-recommendation.md` — architect amendments A1–A12 (input, not artifact of record).
- `plan-overview.md` · `decisions.md` · `phase1-plan.md` · `phase2-plan.md` · `phase3-plan.md` — plan-of-record (to be folded).
- `research-leak-sites.md` · `research-pool-couplings.md` · `research-audit-census.md` · `audit-method.md` · `wi-78967dba-verdict.md`.
- `daemon/manager.py:11516, :11568-11571, :11754-11757, :10860-10861, :11216-11224, :11110-11112, :11529-11541, :11555-11561, :11366/:11383/:11400/:11421/:11430, :11281-11300` (F8).
- `daemon/tools/ens_db_tools.py:1475-1513` (F3).
- `daemon/services/readiness.py:1463-1481, :1716-1790, :107-113` (F4).
- `daemon/repositories/factory.py:252, :323-338` (F2).
- `daemon/tools/ens_db_tools.py:603-633` (F2 repair engine).
- `daemon/persistence.py:225, :316-331, :355-359` (F2, F8).
- `daemon/constants.py` (knob canonical home, D-18).
- `daemon/services/wedge_capture/db_capture.py:308, :387` (F5).
- `daemon/repositories/mission_record/repository.py:498-514` · `models.py:209, :307-313` · `daemon/manager.py:8548-8552` (F6).
- `daemon/services/child_reports.py:1654` · `daemon/persistence.py:316-331` (F8 churn leg).
- `tests/unit/repositories/test_pg_engine_utc_session.py:65, :86-88` (D-17 guard + C-9 amendment).
- `tests/postgres/conftest.py:44-45, :82-95, :167-169, :187-188, :191-195, :240-258` (PG harness).
- `tests/integration/conftest.py` (no auto-mark).
- `pyproject.toml:95` (default deselects).

---

*FOLDED by the technical-analysis worker per the dispatch. Source of truth for the binding contract: `final-implementation-spec.md` (council-produced, with C-1..C-12 corrections applied). Every `file:line` is at base `19fc11c8b` and must be re-verified at the implementation base; drift is a plan-amendment trigger. Read-only fold: no daemon/, tests/, scripts/ or payload-tree file was modified.*