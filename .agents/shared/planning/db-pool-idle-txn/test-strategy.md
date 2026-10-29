# Test Strategy — DB pool poisoned by leaked idle-in-transaction connections (FOLDED)

- **Lane:** `fix/db-pool-idle-txn` · job `634c38f2` · branch `fix/db-pool-idle-txn`
- **BASE anchor:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (verified via `git rev-parse HEAD` in `/home/nea/ensemble-src-wt-dbpool-idletxn`)
- **HEAD anchor:** the post-fix commit produced by this lane (re-derived at gate time)
- **Companion artifacts (same directory, written in parallel — cross-reference by filename only):**
  `plan-overview.md` · `decisions.md` · `phase1-plan.md` · `phase2-plan.md` · `phase3-plan.md`
  · `research-leak-sites.md` · `research-pool-couplings.md` · `research-audit-census.md` · `audit-method.md` · `wi-78967dba-verdict.md`
  · `final-implementation-spec.md` (BINDING CONTRACT — this strategy implements its §5 pin inventory and §6 gate ladder)
- **Status:** FOLDED (round 1) **and RE-BASED on the Phase-0 measured evidence (round 2, part B)** — incorporates the 12 CRITICAL corrections from `council-report.md` (C-1..C-12), the leader rulings, and the **24 delta conditions** in `delta-recheck.md` (**DR-29** is a premise rewrite). Planning only, zero code changes.
- **The pin inventory in §3 is a MIRROR of `final-implementation-spec.md` §5 — the contract is the authority.** They must match **row for row** (W-6: ONE pack inventory). **46 pins.**
- **Interpreter binding (C-1, W-10):** `/home/nea/ensemble-src/.venv/bin/python` (pytest 9.0.2, py 3.13.15; `import daemon` resolves to the **worktree** when cwd = worktree). **`uv run` is DROPPED** (`uv` is off-PATH at `/home/nea/.local/bin/uv`; the worktree has no `.venv` ⇒ network-dependent sync at gate time). **`/usr/bin/python3` is DROPPED** (no pytest installed ⇒ the C3 fence reads COLLECTED=0 ⇒ FATAL). **Gates MUST NOT run from the shared checkout.**
- **THE FULL CLASS-1 FENCE (DR-16) — every gate form in this file carries ALL FIVE parts:** `cd /home/nea/ensemble-src-wt-dbpool-idletxn && \` + the **six-variable per-variable** scrub + `HOME="$(mktemp -d)"` + the certified interpreter. **The `cd` is LOAD-BEARING** — from `/tmp` the certified interpreter resolves `daemon` to the **SHARED CHECKOUT**, so the same command form runs against **different code** depending on cwd.

---

## 0. Scope, fix-ID map, and what this strategy can/cannot prove

### 0.1 Fix-ID map (carried from the lane dispatch; IDs are stable across the lane's artifacts)

| Fix ID | Subject | Research verdict it discharges | Primary artifact |
|---|---|---|---|
| **F1** | Systematic borrow-site **audit** | CLEAN/RISK classification; 0 unclosed borrows. **The census headline is the AST baseline (A=613 / C=4 / B=175 / D=148 / E=14 / F=0); the scanner-independent total is 779; the ≈689 aggregate is NON-CANONICAL** (DR-6) | `audit-method.md`; this file §3.9 |
| **F2** | **Pool hardening** on the manager engine (site 1) | `reset_on_return` / `pool_recycle` / server-side PG timeouts are ABSENT today. **The engine-level idle-in-txn backstop ships DEFINED BUT NOT APPLIED** (§1.4) | this file §1.2, §3.3 pins F2-a..j |
| **F3** | `ens_db_repair_execute` dry-run explicit `BEGIN` | **CONFIRMED** leak mechanism (site A) | this file §1.1, §3.1 pins F3-a..e |
| **F4** | Readiness probe bounds | **PREMISE REWRITTEN (DR-29):** the probe does **not** leak a transaction; under starvation it holds **NO** connection (it is a **pool waiter**). ⇒ **F4-A** (bound the acquisition wait) + **F4-B** (statement bound, demoted) + **F4-C** (the missing starvation leg). **F4 is DEMOTED from Phase-1 primary.** | this file §1.2, §3.2 pins F4-a..f |
| **F5** | wedge-capture `tasks` → `task` | **CONFIRMED** (site F). **RAISED to Phase 1 (DR-34):** the journal quote is a same-connection cascade whose ROOT is the wrong table name | this file §3.5 pins F5-b..d |
| **F6** | `mint_boot_epoch_if_none` UniqueViolation | **REFUTED as a leak**; the 4×/boot logic defect is real. **DR-33: the trigger is a CONCURRENT double-mint for the same anchor** (8-way, no barrier ⇒ 7 real `UniqueViolation`s); the boot path's re-resolve probe is **SKIPPED** (`founding_message_id=None`), not mis-keyed | this file §3.6 pins F6-a..e + F6-c-static |
| **F7** | **Regression guard** (the RCA's fix-item 7) | "no test anywhere asserts zero idle in transaction" (research-pool-couplings.md:175). **MEASURED invariant (DR-35): three-state occupancy + `xact_age_s` residence** | this file §1.3, §3.7 pins F7-a..e + static twins |
| **F8** | Remove the **borrow-then-await** from the deferred-report reconcile (**the lane's ONLY mechanism item, and the Phase-1 PRIMARY**) | M-R3. **The flip condition is NOT MET ⇒ F8 STANDS** (worst hold 8011.119 ms) | `final-implementation-spec.md` §1 F8.1–F8.9, §3; this file §3.8 pins F8-a..e |

### 0.2 What this strategy CAN prove

1. That the **confirmed** mechanisms (F3 BEGIN-outside-try) produce a measurable transaction-hygiene defect on a real scratch PostgreSQL, and that the fixed code produces none.
2. That F8 removes the borrow-then-await hold (F8-b, F8-c, F8-d, F8-e) and that the guard's occupancy invariant (F7-e) would have caught the incident.
3. That the fix does not regress the soak-gate grace/leak surfaces (the DO-NOT-TOUCH packs stay green).
4. That the guard itself is not vacuous (collected-count discipline + self-pins + occupancy invariant).
5. The **Phase-0 empirical baseline** (§1.4) that settled the flip condition (**NOT MET ⇒ F8 STANDS**), the F7 invariant's measured shape, and the pool attribution census (**11 real pool-creation sites**).

### 0.3 What this strategy CANNOT prove (disclosed, do not overstate in the gate report)

- **CORRECTION — this item was previously folded HERE as a "cannot prove" limitation ("two of the four fingerprints are NOT reproducible at BASE"); the e-base explorer's findings REFUTE that claim, so it is removed from the cannot-prove list and recorded below as the measured fact. ALL FOUR of the RCA's recorded `pg_stat_activity` fingerprints REPRODUCE at BASE** (`BASELINE.md:355-362`; the comparison matrix at raw `t4_fingerprints.out:46-56` shows `true` for each). Measured owners, not inferences: **`BEGIN` ×2 → Site A**, `daemon/tools/ens_db_tools.py:1479` (DDL) / `:1491` (DML) — the **only** `text("BEGIN")` in `daemon/` (raw `t4_fingerprints.out:12-18`); **`SELECT instances.instance_id, instances.project_id …` ×2 → the real `SQLModelInstanceRepository.list`** (`daemon/repositories/instance/repository.py:1259+`), so the RCA's attribution to the `/api/instances` list handler is **CONFIRMED** as the fingerprint source (raw `t4_fingerprints.out:3-11`); **`SELECT count(*) FROM job_queue_items …` ×1 → `daemon/services/defer_block_resolver.py:185-191`** (`_DEFER_PENDING_COUNT_SQL`), executed context-managed at `:274`/`:321` — the plan's correction is **CONFIRMED** (raw `t4_fingerprints.out:19-25`); **`INSERT … mission_records` ×4/boot → the ORM insert** at `daemon/repositories/mission_record/repository.py:498-514` (raw `t4_fingerprints.out:36-43`).
  **⇒ The architect's A4-i reading ("`BEGIN` ×2 is a generic wire-level marker ⇒ the attribution is UNVERIFIED") is REFUTED by measurement.** psycopg3 **pipelines** autobegin, so a transaction opened implicitly leaves the **STATEMENT** as `last_query` — measured at raw `t2_stat_activity.out:48`, where the backend is `idle in transaction` with `xact_start` non-null yet `last_query` = `"SELECT 1"`. The **only** literal `"BEGIN"` `last_query` in the raw is the T2e case in which the harness issued an **explicit standalone BEGIN** (raw `t2_stat_activity.out:133`). ⇒ a literal `BEGIN` as `last_query` is a **standalone-BEGIN marker**, and it is **measured-unique to Site A** (the only `text("BEGIN")` in `daemon/`).
  **The readiness probe reads `FROM task`, NOT `job_queue_items`** — `readiness_probe_actually_reads_from_task: true`, `readiness_probe_matches_incident_fingerprint: false` (raw `t4_fingerprints.out:26-35`, `:46-56`) ⇒ that probe **is not** the source of the incident's `job_queue_items` fingerprint.
  **What the fingerprints do and do not prove:** they identify the **statements**, not the **holders**. A reproduced fingerprint places that statement in the incident window; it does **not** by itself establish a leak at that site. **There is still no never-closed borrow at the instances-list or defer-count sites at BASE** (research-leak-sites.md:82, :108; research-audit-census.md:73-82) ⇒ a churn leg built ONLY from those two paths still shows **0** idle-in-txn at BASE; a BASE-demonstrating leg MUST include the confirmed mechanism (F3) and the F8 shape. §1.3 designs the composite leg accordingly.
  **P-9 — the measured record, cited correctly.** The Phase-0 fingerprint evidence is a **PREFIX / substring** check on a **200-char-truncated** `last_query` (raw `common.py:87`: `left(query, 200)`), **not** byte equality with the incident text. The full ORM render is a **separate** field (`full_rendered_sql`, raw `t4_fingerprints.out:10`) and is the **COMPILED** SQL — **not** the observed `last_query`. ⇒ **Do not claim "byte-exactly"**; claim "prefix-compatible with the incident fingerprint (200-char-truncated comparison)". The canonical copy of the measured record is **`.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`**; the planning-directory copy is `BASELINE.md` (the two are line-for-line identical) and the raw harness output is `.agents/tester/harness/dbpool-phase0/raw/*.out`.
- **Production load shape.** The RCA's "0 → 6 backends in ~3 min" is a production figure under real resume-burst load; the lab rate on a scratch cluster is NOT the same number and must not be asserted as such.
- **Pool starvation without aborted transactions** is a *different* mechanism (the Oct-26 checkpoint-pool saturation, `analysis-20261026-v0278-soak-failure.md`) and is NOT this lane's target — **but note that the probe's starvation behaviour IS in scope now (F4-A / F4-f)**, because the probe's *acquisition* wait is the measured hazard.
- **The recurrence precondition is NOT removed by this lane.** The dominant term is **sync DB work on the event loop** (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) and the **BFS depth-loop** transaction span (`daemon/repositories/instance/repository.py:1434-1645`). The guard's occupancy invariant (F7-e) is what will see the recurrence; the follow-up commission owns the residual (leader ruling 6).
- **F8's flip condition is a PRECONDITION, not a constant (DR-30).** The binding form is **"the M-R3 hold < 2 s IFF the awaited content fetch < 2 s"**, and the **decision instrument is the FETCH-latency distribution**, not the hold. Measured base figures: async worst **1059.877 ms** — **MODEL-INJECTED** (raw `t1_mr3_hold.py:281-282`), not a measured fetch; sync-twin ceiling **8.0 s** (worst hold **8011.119 ms**). **The real fetch latency is UNMEASURED** (it needs a booted daemon + a real checkpoint store) ⇒ the precondition's data-dependence is **disclosed, not resolved**. **At base the precondition is NOT MET ⇒ F8 STANDS.**
- **DR-11 / DR-36 — the derivation inputs are MODELLED, not measured:** the real `_fetch_subshape_a_content_async` latency and the real `_get_last_assistant_message` python-side cost were **NOT measured**; the incident-window mint overlap is **INFERRED**; the `pool_timeout` stall was measured at **3 s** (a harness override), not the 30 s default; **λ is NOT measurable** from the raw ⇒ the `λ × budget` bound is dropped, never asserted.

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

### 1.2 LEG D — Site D / F4 (readiness probe bounds) — **REWRITTEN (DR-29)**

**The old premise is REFUTED.** The pre-fold leg asserted *"under a held lock the probe's connection stays checked out for the full `pool_timeout=30 s`"* and *"the probe leaks a transaction per tick"*. The Phase-0 measurement refutes both:

| case | budget | timeout fired | `checkedout()` right after the timeout | backend `pg_state` | raw cite |
|---|---|---|---|---|---|
| **b1** `SELECT 1` | 0.5 s | **no** — completed in **5.8 ms** | **0** | `idle` | raw `t5_fix_sites.out:80-88`, `:1014-1020` |
| **b2** `SELECT pg_sleep(5)` | 0.5 s | **yes at 0.5017 s** | **1** | **`active`** — NOT `idle in transaction` | raw `t5_fix_sites.out:1021-1027`; release bracketed `(4.4747, 4.5275] s` at `:1540-1556` |
| **b3** **pool starvation** (15 holders) | 0.5 s | **yes at 0.5012 s** | **15** — **the holder count, not 16** | n/a — **never got a connection** | raw `t5_fix_sites.out:1960-1967`; verdict `:2019` |

⇒ The measured hazard is **(i) a STRANDED POOL-WAITER THREAD** (the probe never acquires a connection; `asyncio.wait_for` cancels the await, not the thread — `readiness.py:1281-1283`), and **(ii) an unbounded STATEMENT** on a connection the probe *does* hold (b2 — `active`, invisible to an idle-in-txn census), with **no** server-side bound because the manager engine carries no `statement_timeout`.

**The leg is now three parts:**

- **F4-A (NEW — the incident's shape): the probe's CONNECTION-ACQUISITION wait must be bounded.** The mechanism is a **probe-scoped admission pre-check** at the top of each probe body: before `engine.connect()`, read the pool's occupancy (`engine.pool.checkedout()` / `engine.pool.size()`) against the configured capacity; when the pool is **at capacity**, the probe **does not enter the blocking checkout** and raises `TimeoutError` **synchronously** ⇒ `readiness.py:1284-1285` maps it to `FAILURE_CLASS_PROBE_TIMEOUT` ⇒ grace-eligibility preserved, no verdict-contract change, **no stranded thread**.
  - **The engine-level knob is NOT lowered, and this is stated rather than hidden:** a probe-scoped acquisition timeout **cannot** be expressed through SQLAlchemy's public API — the checkout wait is `QueuePool._do_get()` → `self._pool.get(wait, self._timeout)`, where `self._timeout` is the **pool-level** `pool_timeout`; `Engine.connect()` takes **no** timeout (verified against the installed **2.0.48**). Lowering `pool_timeout` to the probe budget (0.5 s) would convert ordinary manager-pool contention into `TimeoutError` **for every daemon caller** (D-15) ⇒ **the manager engine's `pool_timeout` stays 30 s** and the acquisition bound is delivered by the pre-check. **Residual (disclosed):** a checkout that loses the race between the pre-check and `engine.connect()` can still strand the thread for up to `pool_timeout` = 30 s.
- **F4-B (RETAINED, DEMOTED — defence-in-depth): the probe's STATEMENT duration is server-bounded when the probe DOES hold a connection.** `SET LOCAL statement_timeout = 2 × budget`, PG-only, house idiom `daemon/tools/ens_db_tools.py:787-797`. **The measured semantics, stated plainly:** the backend is **`active`** ⇒ the effect is **statement cancellation (`57014` / `QueryCanceled`)**, **NOT** a census-visible idle-in-txn change, and **the backend row is EXPECTED TO PERSIST** with `state='idle'`. The `"row gone from pg_stat_activity"` signature belongs to the **engine-level `idle_in_transaction_session_timeout`** — measured `after_wait: null`, `state_after: "BACKEND GONE (terminated by server)"` (raw `t2_stat_activity.out:137-140`), `terminated: true` / `elapsed_s: 2.02` (`:159-161`) — a mechanism **F4 does NOT install**.
- **F4-C (NEW — the missing leg):** a **starvation-shaped** PG leg under **15 holders**: the probe holds **NO** connection, `engine.pool.checkedout()` **equals the holder count**, no waiter is left in the pool's checkout queue, and the probe thread **terminates within a bounded window**. BASE (no F4-A): the probe is a **stranded waiter** (timeout at **0.5012 s**, `checkedout_right_after_timeout: 15`). HEAD (with F4-A): it **refuses immediately**.

- **Pin:** F4-a..f (§3.2). **F4-b is SYNTHETIC** — a deliberately held lock is **not** the incident's shape; the incident's shape is covered by **F4-f**.
- **F4 is DEMOTED from "Phase-1 primary"** — the Phase-1 primary is **F8**.

### 1.3 LEG C — Black-box churn + census (the F7 guard's load leg)

(mechanism unchanged; **the invariant is MEASURED (DR-35)** — see §3.7)

- **Composite leg:** drive a resume-burst load that exercises F3 + F8 paths; sample `manager.engine.pool.checkedout()` at ≤ 1 Hz (in-memory) and `pg_stat_activity` by `application_name` with `state` + `wait_event_type` + `xact_start` + `query_start` + `state_change` + `backend_start` + **`xact_age_s`**.
- **BASE expectation (reproduction guard):** ≥ 1 manager-pool backend in one of the **three** states **and** `pool.checkedout()` above baseline. If BASE cannot demonstrate the F8 shape, the guard is a **regression** guard, not a **reproduction** guard — say so (A10) rather than forcing a BASE demo.
- **HEAD expectation (regression guard):** `pool.checkedout()` returns to baseline; no backend's **`xact_age_s`** exceeds **the operation's own hold budget**.
- **Pin:** F7-a..e + the static twins (§3.7).
### 1.4 Phase-0 empirical baseline (leader ruling 5) — **DONE; this strategy is re-based on it**

**Phase 0 is COMPLETE.** The measured record is `BASELINE.md` (this directory) and its **canonical copy** `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`; the harness raw output is `.agents/tester/harness/dbpool-phase0/raw/*.out` (not shipped). **The raw WINS over any quoted summary** — where a copy's number differs from the raw, the raw value is the one used below.

**The fence the run honoured (unchanged, and binding for every leg below):** a **lane-created SCRATCH PostgreSQL database** (dropped at the end — **NEVER** `ensemble_prod`, **NEVER** the live daemon's DB, **NEVER** a restart of anything), with **PER-VARIABLE env scrubbing** (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT`; `env -u POSTGRES_*` is a silent NO-OP); test scripts never resolve live-shaped HOME-derived install paths (mktemp FAKE_HOME + escape guard).

**(a) The M-R3 hold — the flip condition is a PRECONDITION (DR-30), and it is NOT MET.**

- The binding form is **"the M-R3 hold < 2 s IFF the awaited content fetch < 2 s"**, and the **decision instrument is the FETCH-latency distribution**, not the hold. The old constant form ("if hold < 2 s") is **withdrawn** — the hold is a function of the fetch, which is **data-dependent**.
- Measured base figures: async worst **1059.877 ms** (L5, n=15 — **MODEL-INJECTED**: `await asyncio.sleep(1.0)` at raw `t1_mr3_hold.py:281-282`); sync-twin worst **8011.119 ms** (n=1, the 8.0 s bridge). Median hold 1029.887 ms vs await 1026.527 ms ⇒ the hold **equals** the awaited fetch latency (raw `t1_mr3_hold.out:26-339`, `:341-346`).
- **The real fetch latency remains UNMEASURED** (needs a booted daemon + a real checkpoint store) ⇒ the precondition's data-dependence is **disclosed, not resolved**.
- **⇒ NOT MET ⇒ F8 STANDS.** The bound derived from it (`MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`) **ships DEFINED BUT NOT APPLIED** — see §1.6.

**(b) The occupancy/residence census — the MEASURED F7 invariant (DR-35).**

- **Three states:** (i) `idle` with `xact_start IS NULL` (checked out, **no statement yet**) — raw `t2_stat_activity.out:6-15`, `:25-33`, `:170-178`; (ii) `idle in transaction` — `:41-50`; (iii) `active` — the F4-B probe shape, raw `t5_fix_sites.out:1540-1544`. An idle-in-txn-only census is **blind** to (i) and (iii) — confirmed empirically at raw `t2_stat_activity.out:17-18`, `:179-180`.
- **Residence:** the metric is **`xact_age_s` = `now() − xact_start`** (already collected — raw `common.py:88`). The old metric `state_change − xact_start` is **FROZEN** at the last statement's completion ⇒ a leaked backend shows the **same sub-millisecond value** as a healthy one ⇒ the budget **can never fire on the leak** (the harness says so itself: raw `t2_stat_activity.out:99`).
- **Measured healthy residence:** n=40, min **0.0003 s**, median **0.0005 s**, p95 **0.0007 s**, max **0.0008 s**, mean 0.0005 s (raw `t2_stat_activity.out:88-95`). ⇒ **the 2.0 s placeholder is WITHDRAWN** — 2 × the noise floor = 1.6 ms ⇒ the 2.0 s floor dominated the formula, and 2.0 s **would false-red a healthy daemon** (the sync twin's legitimate dwell is **unconditionally ≥ 8 s**; any long-history instance's real fetch exceeds 2 s).
- **λ is NOT MEASURABLE** from the raw (no borrow-arrival series exists) ⇒ the `peak_occupancy ≤ λ × budget` bound is **dropped as an assertion**, never asserted.

**(c) Measured PG runtime semantics.**

- `last_query` fingerprints per pool reproduced — **with the P-9 caveat**: the comparison is a **prefix / substring** check on a **200-char-truncated** value (raw `common.py:87`), **not** byte equality; the full ORM render is the **compiled** SQL in a **separate** field (raw `t4_fingerprints.out:10`).
- `application_name` attribution: `options=-c application_name` lands, but **last `-c` wins** within one options string (raw `t3_pool_attribution.out:121-125`) and the **conninfo parameter BEATS the options form** (`:127-135`) ⇒ the attribution channel is the **conninfo**, and the shared constant carries **no** `application_name` (the ordering hazard is measured: `repair_engine_reports_manager_name: true`, `:111-119`).
- `idle_in_transaction_session_timeout` fires as documented: the backend is **GONE** — `after_wait: null`, `state_after: "BACKEND GONE (terminated by server)"` (raw `t2_stat_activity.out:137-140`), `terminated: true`, `elapsed_s: 2.02` (`:159-161`).
- The manager engine carries **no** `statement_timeout`, **no** `idle_in_transaction_session_timeout`, **no** `pool_recycle`, **no** `pool_timeout` override at base (the library default `pool_timeout` is 30 s).

**(d) The pool attribution census — 11 REAL sites.**

12 enumerated, **1** comment false-positive at `daemon/repositories/factory.py:289` (raw `t3_pool_attribution.out:27-101`) ⇒ **11 real pool-creation sites**. Base attribution: `backends_by_application_name {"": 2}` — the pools are **NOT distinguishable** at base (`:2-25`). ⇒ **The three named engines are a FLOOR, not the census boundary** (DR-32).

### 1.5 Attribution method (A3, A5, C-2, C-8, **DR-31, DR-32**) — F7 guard's census attribution

1. **Scratch-database isolation (D-23/D-24)** — the guard creates its own uniquely-named database, points the daemon's engine factory at it, and asserts on `pg_stat_activity WHERE datname = <that db>`. Every backend in that database belongs to the test. **This is the guard's attribution and it stays.**
2. **`application_name` where present, else scratch-DB isolation** — the census is attributed *"by `application_name` where present, else by scratch-DB isolation"*. **DR-31 — the CHANNEL is the engine's OWN conninfo**, not the `-c options` string (measured: conninfo **beats** options — raw `t3_pool_attribution.out:127-135`; last-`-c` wins within one options string — `:121-125`; the shared constant appended last ⇒ the repair engine reports the manager's name — `:111-119`). The three engines carry a name: `ensemble-manager` (F2.3), `ensemble-repair` (F2.5), `ensemble-checkpoint-adapter` (F2.6). The **shared** `PG_SESSION_CONNECT_ARGS` must **never** carry `application_name` (D-17). **F2-g / F2-h assert the CHANNEL, not merely the presence of a name.**
3. **The census scope is EVERY pool-creation site (DR-32)** — **11 real sites** (raw `t3_pool_attribution.out:27-101`); the three named engines are the **daemon-internal subset**, not the boundary.
4. **Every census captures** `count` + `state` + `wait_event_type` + `xact_start` + `query_start` + `state_change` + `backend_start` + `application_name` + **`xact_age_s`** + `state_age_s`, and reports **residence-time buckets**, not just a count (raw `common.py:80-92` is the reference query shape). The `backend_start > run_start` BASE filter is **prescribed** at §1.3 and must be **implemented** in the helper's SQL.
5. **PG state `idle` (and `active`) are disclosure metrics**, not assertions (C-10).

### 1.6 The engine-level backstop — DEFINED BUT NOT APPLIED (DR-29, DR-7)

- **Shipped state:** the `idle_in_transaction_session_timeout` clause is **omitted** from the manager engine's rendered options string; at boot the daemon logs `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no Phase-0 measurement`; the honesty blocks say the same.
- **Why (P-3):** the formula's own arithmetic yields 4 × 1059.877 ms = **4.24 s**, so the **30 s floor dominates** and **coincides exactly with the legitimate `pool_timeout` wait** — enabling at 30 s risks killing a legitimate long waiter, i.e. the incident class this lane exists to fix. Server-side enforcement **kills the backend** (measured: `BACKEND GONE`), which is a **behavioural** change with no measured safety margin. ⇒ **DR-29(b)'s "add the engine-level backstop" option is REJECTED.**
- **Enablement mechanism (one line):** the clause is applied **iff `BASELINE.md` records a TRUSTWORTHY measurement** of the manager engine's legitimate hold (measured on the **real** fetch path, not modelled, and not a single-sample failure shape); the constant is then set from that measurement.
- **CONDITIONAL carve-out (leader OQ-3):** because **no engine-wide `statement_timeout` is applied**, the `SET LOCAL statement_timeout = 0` carve-out at `daemon/manager.py:8760` is **NOT** required today — it becomes **MANDATORY** if the backstop is ever enabled.
- **The ladder cell (G2.1's family):** one cell asserts the constant **equals the Phase-0 rule** OR **is DISABLED with the reason recorded** in the gate report (DR-20).

## 2. (b) RUNNER FORM + C3 DISCIPLINE (false-green)

### 2.1 The false-green mechanism this section exists to defeat

`pyproject.toml:95` sets `addopts = "-m 'not integration and not postgres and not slow and not conformance'"`. ⇒ A bare `pytest tests/<anything>/` deselects the very tests it names and reports "0 failed" with nothing executed. The soak lane recorded this as the C3 lesson; the binding house rule is `requirements.md:1133` and `:1169-1172`. A second false-green class sits on top of it: `tests/postgres/conftest.py:167-169` **skips the whole module** when PostgreSQL is unreachable ⇒ a "green" PG gate that collected 0 and skipped everything is indistinguishable from a real pass.

### 2.2 The EXACT command form for each pack class

**Interpreter (C-1, W-10):** `/home/nea/ensemble-src/.venv/bin/python` (verified: pytest 9.0.2, py 3.13.15; `import daemon` resolves to the **worktree** when cwd = worktree). **Drop `uv run`** — `uv` is off-PATH at `/home/nea/.local/bin/uv` and the worktree has no `.venv` ⇒ network-dependent sync at gate time. **Drop `/usr/bin/python3`** — no pytest installed ⇒ the C3 fence reads COLLECTED=0 ⇒ FATAL. **Gates MUST NOT run from the shared checkout.**

**THE FULL CLASS-1 FENCE — DR-16: EVERY gate form below carries ALL FIVE parts.** No exceptions, no "bare interpreter" forms.

```
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
/home/nea/ensemble-src/.venv/bin/python -m pytest <paths> --collect-only -q
```

- **`cd /home/nea/ensemble-src-wt-dbpool-idletxn` is LOAD-BEARING** — from `/tmp` the certified interpreter resolves `daemon` to the **SHARED CHECKOUT** (`/home/nea/ensemble-src/daemon/__init__.py`), i.e. the same command form runs against **different code** depending on cwd. Every gate form therefore begins with the `cd`.
- **Per-variable** scrub only, **six** variables: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `DAEMON_PORT`. `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) — an ambient `POSTGRES_DB=ensemble_prod` survives it. (`DAEMON_PORT` is scrubbed so a gate can never bind or probe the live daemon's port.)
- `HOME="$(mktemp -d)"` — **never** a HOME-derived live install path (two live-install deletions, 2026-10-12).
- **Certification requirement (blueprint "[INSTALL+PG]"):** the implementer MUST verify `import daemon` resolves **inside the worktree** (`python -c "import daemon, os; print(os.path.dirname(daemon.__file__))"`) and record the resolved path in the gate report before any pack is trusted.
- **First step of every gate: print the interpreter certification** — `python -c "import daemon,pytest; print(daemon.__file__, pytest.__version__)"` — and attach it to the gate report. `collected == 0` ⇒ **FATAL**, never "0 failed ⇒ pass". **A `TBD` `EXPECTED` cell ⇒ FATAL too — the fence integer-validates before comparing (Δ1).**

**Class 1 — default-run unit packs (no marker; collected by the default `pytest`).**

```bash
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/unit/repositories/test_pg_engine_utc_session.py \
  tests/unit/tools/test_dbpool_pool_attribution.py \
  tests/unit/tools/test_dbpool_repair_engine_isolation.py \
  tests/unit/tools/test_dbpool_p2_immutability.py \
  tests/unit/tools/test_dbpool_honesty_blocks.py \
  tests/unit/tools/test_dbpool_txn_hygiene.py \
  tests/unit/tools/test_dbpool_probe_bound.py \
  tests/unit/tools/test_dbpool_probe_failure_class.py \
  tests/unit/tools/test_dbpool_mission_mint.py \
  tests/unit/tools/test_dbpool_wedge_capture_static.py \
  tests/unit/tools/test_dbpool_probe_retention_static.py \
  tests/unit/tools/test_dbpool_occupancy_static.py \
  tests/unit/tools/test_dbpool_residence_static.py \
  tests/unit/tools/test_dbpool_guard_pack_selfcheck.py \
  tests/unit/tools/test_dbpool_fix_site_static.py \
  tests/unit/tools/test_dbpool_census_families.py \
  tests/unit/tools/test_dbpool_consistency_pin.py \
  tests/unit/tools/test_dbpool_audit_record.py \
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
  tests/postgres/test_dbpool_mission_mint_pg.py \
  tests/postgres/test_dbpool_probe_starvation_pg.py \
  tests/postgres/test_dbpool_wedge_capture_pg.py \
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
  tests/integration/test_dbpool_probe_bound_pg.py \
  tests/integration/test_db_pool_idle_in_transaction_guard.py \
  tests/integration/test_dbpool_churn_census.py \
  tests/integration/test_dbpool_deferred_report_reconcile_pg.py \
  --override-ini="addopts=" -m integration -q --timeout=900
```

`tests/integration/conftest.py` does **NOT** auto-mark (soak `phase1-plan.md:61`) — the pack file itself must carry `pytestmark = pytest.mark.integration` (precedent: `tests/integration/test_checkpoint_pool_burst_shapes.py:49`, `.../test_checkpoint_pool_ghost_waiters.py:58`).

**Class 4 — the DO-NOT-TOUCH regression suite (§3.4). Mixed classes ⇒ **two commands** (one per marker), never one. **DR-16 — both now carry the FULL Class-1 fence** (previously the interpreter and nothing else).**

```bash
# 4a — the 6 default-run unit packs (default addopts are FINE here: no marker ⇒ collected)
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/unit/tools/test_readiness_failure_class.py \
  tests/unit/tools/test_readyz_grace_detail.py \
  tests/unit/tools/test_saver_probe_contention_class.py \
  tests/unit/tools/test_promote_txn_hold.py \
  tests/unit/tools/test_checkpoint_pool_config.py \
  tests/unit/test_readiness_mcp_independence.py -q

# 4b — the 2 integration packs (house form; MUST NOT use the default addopts)
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/integration/test_checkpoint_pool_burst_shapes.py \
  tests/integration/test_checkpoint_pool_ghost_waiters.py \
  --override-ini="addopts=" -m integration -q --timeout=300
```

**Class 5 — the whole-tree base-vs-HEAD legs (G1.5, G3.5).** The same fence, over the whole default-collected corpus, **on both sides** (base worktree and HEAD worktree), with the collected counts captured and reconciled (§2.3).

### 2.3 The expected-collected-count assertion (the non-vacuity fence)

**Rule (binding, `requirements.md:1133` + `:1169-1172`):** *no gate may report "0 failed" without also proving a non-zero collected count equal to the pack's DECLARED count.* "0 failed" alone is vacuous — it is the exact output a fully-deselected run produces.

**DR-23 — the DECLARED count comes from an INDEPENDENT artifact, not from the run.** The artifact contract is:

> **`.agents/shared/planning/db-pool-idle-txn/expected-collected.md`** — a per-pack table of collected counts **measured at base `19fc11c8b`**. **The artifact EXISTS (created in this fold); every count cell is currently `TBD (R-1)` — no number is invented.**

**The counts are TO BE MEASURED at base (task R-1, routed to the tester lane) — THIS FILE DOES NOT INVENT ANY NUMBER.** The fence reads that file. The pack-local `EXPECTED_COLLECTED` constant applies to **NEW packs only**; the pre-existing DO-NOT-TOUCH corpus declares none and **must not be modified**. Without this independent declaration, `EXPECTED` would be filled in from the current run ⇒ the assertion is a **tautology** ⇒ the whole-tree leg would be "recorded", not "asserted" — the exact C-1 failure mode.

**Mechanism.** `pytest <paths> --collect-only -q` prints one line per collected test id plus a summary tail of the form `42 tests collected in 1.23s`.

> **GNU grep trap (blueprint "[TESTING]"):** `grep -E "a|b"` is BROKEN — under ERE `\|` is a *literal pipe*. Use a plain `|` under `-E`. The pattern below uses no alternation and is therefore safe; if alternation is added, use `|` not `\|`.

**Exact assert form** (run BEFORE the real run, and its output pasted into the gate report). **DR-16 — the fence script itself now carries the full Class-1 fence** (it previously ran the interpreter bare, from whatever cwd the operator happened to be in):

```bash
#!/usr/bin/env bash
set -euo pipefail
# $1 = declared expected count (from expected-collected.md), $2 = marker (or "" for default), $3.. = paths
EXPECTED="$1"; MARKER="$2"; shift 2
case "$EXPECTED" in ''|*[!0-9]*)
  echo "FATAL: EXPECTED is not an integer (TBD cell?) for: $*" >&2; exit 1;; esac
cd /home/nea/ensemble-src-wt-dbpool-idletxn
ARGS=()
[ -n "$MARKER" ] && ARGS+=(--override-ini="addopts=" -m "$MARKER")
COLLECTED=$(env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
  HOME="$(mktemp -d)" \
  /home/nea/ensemble-src/.venv/bin/python -m pytest "$@" "${ARGS[@]}" --collect-only -q 2>/dev/null \
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

**Additional non-vacuity rules that apply to every pack:**
- **A `TBD` cell in `expected-collected.md` at gate time = FATAL — the fence integer-validates `EXPECTED` before comparing** (Δ1). Without the guard, bash `test` on a non-integer `EXPECTED` errors inside the `if` condition, the FATAL branch is skipped, and the script prints `COLLECTED-OK …` with **exit 0** — the exact C3 vacuous-pass class.
- **A pack that can be silently skipped is a red gate.** `tests/postgres/conftest.py` **skips the whole module** when PostgreSQL is unreachable (`:167-169`). The gate script MUST therefore assert the run did not produce skips: parse the summary line and fail if `skipped > 0` for the PG classes. **A green PG gate that skipped is a FAILING gate.**
- `--timeout=N` on every long pin (house convention, 115 usages; `pyproject.toml:97-98` default is 30 s and the kill is `os._exit(1)` ⇒ no traceback). Every new long pin carries an explicit `@pytest.mark.timeout(N)` with N > its worst case.
- The churn pack's `@pytest.mark.timeout` must exceed `N*60 + 300`.
- **The whole-tree legs (G1.5, G3.5) must assert `collected > 0` on BOTH sides** and reconcile `base_collected` / `head_collected` against the sum of the declared `EXPECTED_COLLECTED_*`; a **mismatch is FATAL**. "The base run's collected count is *recorded*" is **not** an assertion.
## 3. (c) MUTATION-MEANINGFUL PIN LIST — **MIRRORS `final-implementation-spec.md` §5 (46 pins)**

**Discipline (blueprint "[DISCIPLINE]"):** *regression pins MUST be mutation-verified.* A pin nothing can redden is dead weight. Every pin below states **the exact mutation that turns it RED**. Where a mutation is impossible, that is stated as a residual. **Every PG/integration pin has a default-collected static twin** (W-13) so deleting the PG pin leaves the default run red — **the pin → twin map is §3.11**.

**THIS SECTION IS A MIRROR.** The contract's §5 is the authority; the two must match **row for row** (W-6: ONE pack inventory). **46 pins.** The pack file for every pin is named here — **no `tests/unit/…` wildcards** (DR-28).

### 3.1 F3 — `ens_db` dry-run BEGIN hygiene (Site A)

Pack: `tests/unit/tools/test_dbpool_txn_hygiene.py` (NEW, default-collected).

| Pin | Pack file | What it asserts | Mutation that MUST redden it |
|---|---|---|---|
| **F3-a** | `tests/unit/tools/test_dbpool_txn_hygiene.py` | After a dry-run whose `BEGIN` raises, the connection is **not** in a transaction (`conn.in_transaction() is False`) and a further statement on it does **not** raise *"current transaction is aborted"*. | Re-introduce `session.execute(text("BEGIN"))` at `daemon/tools/ens_db_tools.py:1479` (DDL) / `:1491` (DML) ⇒ the outer except at `:1513` runs with no rollback ⇒ **RED**. |
| **F3-b** | `tests/unit/tools/test_dbpool_txn_hygiene.py` | The dry-run's failure path performs a **rollback** (observable: the session's transaction is None AND a sentinel rollback spy fired). | Delete the rollback from the outer-except path (the outer-except-no-rollback shape at `:1513`) ⇒ **RED**. |
| **F3-c** | `tests/unit/tools/test_dbpool_txn_hygiene.py` | The DDL-branch rollback itself failing also leaves the connection clean. | Remove the outer-path rollback handling for the rollback-failure variant — **the plantable site is `daemon/tools/ens_db_tools.py:1489`** (the `session.rollback()` call inside the DDL branch's `finally:`) ⇒ **RED**. |
| **F3-d** | `tests/unit/tools/test_dbpool_txn_hygiene.py` | The dry-run's `previewed` audit row is written in a **separate session** and is not rolled back by the dry-run transaction. | Re-introduce the audit row **inside** the rolled-back transaction (violates the separate-session contract at `:1530-1538`) ⇒ **RED**. |
| **F3-e** | `tests/unit/tools/test_dbpool_txn_hygiene.py` | Agent-visible preview strings and docstrings reflect the new dry-run wording. | Change the preview string at `daemon/tools/ens_db_tools.py:1482` ⇒ the wording assertion REDs (one concrete edit). |

### 3.2 F4 — readiness probe bounds (Site D) — **REWRITTEN (DR-29)**

Packs: `tests/unit/tools/test_dbpool_probe_bound.py` (NEW), `tests/unit/tools/test_dbpool_probe_failure_class.py` (NEW), `tests/unit/tools/test_readyz_grace_detail.py` (EXISTING), `tests/integration/test_dbpool_probe_bound_pg.py` (NEW), `tests/postgres/test_dbpool_probe_starvation_pg.py` (NEW).

| Pin | Pack file | What it asserts | Mutation that MUST redden it |
|---|---|---|---|
| **F4-a** | `tests/unit/tools/test_dbpool_probe_bound.py` | **Both halves.** (i) **F4-A:** each probe body performs the **acquisition admission pre-check** before `engine.connect()` (raising `TimeoutError` synchronously when the pool is at capacity), and the **saver probe is excluded** from it. (ii) **F4-B:** `make_db_probe` issues `SET LOCAL statement_timeout = 2 × DB_PROBE_TIMEOUT_S` as the **FIRST** statement, dialect-guarded (PG only); `make_queue_probe` issues `2 × QUEUE_PROBE_TIMEOUT_S`; the **saver** probe carries **no** `SET LOCAL`. | Remove the admission pre-check from `make_db_probe` or `make_queue_probe`; or remove the `SET LOCAL statement_timeout` from `make_db_probe`; or change the derived value so it is no longer `2 ×` the constant; or add `SET LOCAL` to the **saver** probe (the exclusion assertion REDs). |
| **F4-b** | `tests/integration/test_dbpool_probe_bound_pg.py` | **RETAINED but RE-SCOPED — the F4-B STATEMENT case, and it is SYNTHETIC.** Under a deliberately held lock, `make_queue_probe`'s statement is **cancelled** (`57014` / `QueryCanceled`), the `to_thread` future **returns**, the connection is **returned to the pool**, and `engine.pool.checkedout()` returns to baseline within the bound. The backend row is **EXPECTED TO PERSIST** with `state='idle'`. | **Remove the `SET LOCAL statement_timeout` from `make_queue_probe`** — the statement stays blocked, the connection stays checked out, `engine.pool.checkedout()` does not return to baseline ⇒ RED. **The `"row gone from pg_stat_activity"` assertion is NOT part of this pin** — that signature belongs to the engine-level `idle_in_transaction_session_timeout` (measured `after_wait: null`, `state_after: "BACKEND GONE (terminated by server)"` — raw `t2_stat_activity.out:137-140`), which **F4 does not install**. **This leg does NOT cover the incident's shape** — that is F4-f. |
| **F4-c** | `tests/unit/tools/test_dbpool_probe_failure_class.py` | The probe's returned `(verdict, failure_class)` pair stays `probe_timeout` (not hard-component-red); **F4-A's synchronous `TimeoutError` lands on the same class** (`FAILURE_CLASS_PROBE_TIMEOUT`) ⇒ grace-eligibility preserved. | Change the probe's returned pair, or reclassify a timeout as hard-component-red. |
| **F4-d** | `tests/unit/tools/test_readyz_grace_detail.py` | Readiness detail fields / serialization unchanged (DO-NOT-TOUCH). | Remove a detail field. |
| **F4-e** | `tests/unit/tools/test_dbpool_probe_bound.py` | The docstring at `daemon/services/readiness.py:107-113` no longer carries the false premise ("the driver's own connect/execute timeout releases it") **and states the MEASURED semantics** (backend `active`, statement cancelled `57014`, row persists). | Restore the false docstring premise. |
| **F4-f** | `tests/postgres/test_dbpool_probe_starvation_pg.py` | **NEW — the missing starvation-shaped leg.** Under **15 holders**: the probe holds **NO** connection; `engine.pool.checkedout()` **equals the holder count** (15, not 16); no waiter is left in the pool's checkout queue; the probe thread **terminates within a bounded window**. **Scoping (Δ5):** the 15 holders are created and held **STABLE** before the probe is invoked (quiescent setup); the "no waiter / bounded termination" assertion applies to that setup — the D-57 boundary race (14→15 transition during the pre-check) can still strand one waiter for up to `pool_timeout` and is a **disclosed residual, NOT a pin failure**. **BASE-vs-HEAD:** at BASE (no F4-A) the probe is a **stranded waiter** (measured: timeout at **0.5012 s**, `checkedout_right_after_timeout: 15` — raw `t5_fix_sites.out:1960-1967`); at HEAD (with F4-A) it **refuses immediately**. | Remove the F4-A admission pre-check ⇒ at HEAD the probe becomes a waiter again ⇒ the "no waiter / bounded termination" assertion REDs. |

### 3.3 F2 — pool hardening on the manager engine (site 1)

Packs: `tests/unit/repositories/test_pg_engine_utc_session.py` (EXISTING, amended per D-37), `tests/unit/tools/test_dbpool_pool_attribution.py` (NEW), `tests/unit/tools/test_dbpool_repair_engine_isolation.py` (NEW), `tests/unit/tools/test_dbpool_p2_immutability.py` (NEW), `tests/unit/tools/test_dbpool_honesty_blocks.py` (NEW).

| Pin | Pack file | What it asserts | Mutation that MUST redden it |
|---|---|---|---|
| **F2-a** | `tests/unit/repositories/test_pg_engine_utc_session.py` | `pool_reset_on_return="rollback"` is explicit on the manager engine. | Remove the explicit `pool_reset_on_return` kwarg. |
| **F2-b** | `tests/unit/repositories/test_pg_engine_utc_session.py` | `pool_recycle=3600` is set. | Drop `pool_recycle`. |
| **F2-c** | `tests/unit/repositories/test_pg_engine_utc_session.py` | The `idle_in_transaction_session_timeout` **clause** is present in the manager options. **DR-20 — the assertion is STRUCTURAL, never the literal `60000`.** | Drop the clause from the manager options. |
| **F2-d** | `tests/unit/repositories/test_pg_engine_utc_session.py` | `pool_pre_ping=True` stays. | Remove `pool_pre_ping`. |
| **F2-e** | `tests/unit/repositories/test_pg_engine_utc_session.py` | The manager options string is order-pinned and its `idle_in_transaction_session_timeout` value **equals `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` as imported from `daemon/constants.py`** — **never the literal `60000`** (D-37 + DR-20). | Replace the manager options string (order-sensitive), or change the clause's value so it no longer equals the constant, or reorder the clauses. |
| **F2-f** | `tests/unit/tools/test_dbpool_repair_engine_isolation.py` | The **shared** `PG_SESSION_CONNECT_ARGS` does **not** carry an `application_name` or a timeout (D-17 guard). | Extend the shared constant with an `application_name` or a timeout ⇒ the repair engine's rendered string changes ⇒ RED. |
| **F2-g** | `tests/unit/tools/test_dbpool_pool_attribution.py` | **DR-31 — the CHANNEL is asserted, not merely the presence.** Each engine's `application_name` lives in **its own conninfo / URL query dict** (measured: conninfo **beats** `-c options` — raw `t3_pool_attribution.out:127-135`; last `-c` wins within one options string — `:121-125`); the three names are **distinct**; each appears **exactly once**; the **shared** constant carries **none**. | Put `application_name` back into the shared `-c options` string (the channel flips ⇒ RED); or make two names equal; or make one appear twice; or add a name to the shared constant. |
| **F2-h** | `tests/unit/tools/test_dbpool_p2_immutability.py` | The shared `conn_string` at `daemon/persistence.py:225` does **not** carry `application_name` (P2-immutability + separate-DSN guard). | Add `application_name` to the shared `conn_string` ⇒ the P2 oracle's rendered conninfo changes ⇒ RED. |
| **F2-i** | `tests/unit/tools/test_dbpool_honesty_blocks.py` | Both honesty blocks (`daemon/constants.py:207-217`, `daemon/persistence.py:306-315`) state "retention/transaction-hygiene REPAIRED; DWELL/OCCUPANCY NOT REPAIRED" **and** the backstop's **DISABLED** status (DR-8). | Restore the false honesty-block wording ("the P1 leak is REPAIRED"), or delete the `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED` status line. |
| **F2-j** | `tests/unit/tools/test_dbpool_p2_immutability.py` | P2 saver pool construction (`daemon/persistence.py:316-331`) knobs are byte-identical. | Change any knob of the P2 saver pool. |

### 3.4 DO-NOT-TOUCH regression pins (C1/C2 non-interference)

Pack: the 8 existing packs named in §2.2 Class 4a/4b.

| Pin | Pack file | What it asserts | Mutation that MUST redden it |
|---|---|---|---|
| (existing 8 packs) | *(the 8 DO-NOT-TOUCH packs named in §2.2 Class 4a/4b)* | soak-gate grace/leak surfaces untouched | (each pack's own mutation) |

### 3.5 F5 — wedge-capture table rename (Site F) — **RAISED to Phase 1 (DR-34)**

Packs: `tests/unit/tools/test_dbpool_wedge_capture_static.py` (NEW), `tests/postgres/test_dbpool_wedge_capture_pg.py` (NEW).

| Pin | Pack file | What it asserts | Mutation that MUST redden it |
|---|---|---|---|
| **F5-b** | `tests/postgres/test_dbpool_wedge_capture_pg.py` | The wedge-capture queries use `FROM task` (not `tasks`), and the **same-connection cascade** no longer reproduces. | Re-introduce `FROM tasks` / `tasks t`. |
| **F5-c** | `tests/unit/tools/test_dbpool_wedge_capture_static.py` | Static twin: no `tasks` / `tasks t` in the module. | Re-introduce `FROM tasks` / `tasks t`. |
| **F5-d** | `tests/unit/tools/test_dbpool_wedge_capture_static.py` | **RESTORED (DR-22):** the **store census** query (`daemon/services/wedge_capture/db_capture.py:351-363`, over `pg_class`/`pg_namespace`) still works — the wrong-table fix must not have touched it. | Break the census SQL (e.g. change the `pg_class`/`pg_namespace` join) ⇒ RED. |

### 3.6 F6 — mission mint idempotency (Site E) — **DR-33: the trigger is a CONCURRENT double-mint**

Packs: `tests/unit/tools/test_dbpool_mission_mint.py` (NEW), `tests/postgres/test_dbpool_mission_mint_pg.py` (NEW).

| Pin | Pack file | What it asserts | Mutation that MUST redden it |
|---|---|---|---|
| **F6-a** | `tests/unit/tools/test_dbpool_mission_mint.py` | The boot mint uses an upsert with `on_conflict_do_nothing` keyed on the anchor; on `rowcount == 0` it re-resolves via the **anchor-scoped** probe (`find_open_epoch_by_anchor`, `daemon/repositories/mission_record/repository.py:69-93`). | Replace the upsert with the bare ORM insert; or change the re-resolve key from the anchor to `founding_message_id`. |
| **F6-b** | `tests/postgres/test_dbpool_mission_mint_pg.py` | Idempotency holds on a real scratch PG **under concurrency**: a **concurrent double-mint for the same anchor** must not raise. (Measured trigger: 8-way concurrent, no barrier ⇒ **7 real `UniqueViolation`s** at BASE — raw `t5_fix_sites.out:2040-2053`; forced overlap ⇒ **5** — `:2054-2067`. The sequential path is already idempotent — c2: `ok: true`, `rung: 1`.) | Remove the idempotency (revert to a bare INSERT) ⇒ the concurrent leg raises `UniqueViolation` ⇒ RED. |
| **F6-c** | `tests/postgres/test_dbpool_mission_mint_pg.py` | The target DB **has** `ux_mission_records_anchor_open` with a byte-identical predicate. | Drop the partial unique index from the target DB ⇒ the upsert raises **42P10** ⇒ the negative leg REDs. |
| **F6-c-static** | `tests/unit/tools/test_dbpool_mission_mint.py` | **Static twin (DR-24):** `OPEN_EPOCH_STATUS_SQL` at `daemon/repositories/mission_record/models.py:209` is **byte-identical** to the predicate at `models.py:305-313` **and** to `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`. | Change `status = 'open'` to `status = "OPEN"` at `models.py:209` ⇒ the conflict-target predicate no longer byte-matches the partial index ⇒ RED. |
| **F6-d** | `(gate — process)` | *(gate — process)* no new `.sql` file **and** no new ORM TABLE/COLUMN/INDEX (A-6). | Introduce a new `.sql` file, or a new ORM TABLE/COLUMN/INDEX. |
| **F6-e** | `tests/unit/tools/test_dbpool_mission_mint.py` | **CONCRETE F6-SUBJECT mutation (DR-18 — the TODO cell is DELETED):** the conflict-target predicate must stay byte-identical to the partial unique index. | Change `OPEN_EPOCH_STATUS_SQL` at `daemon/repositories/mission_record/models.py:209` from `status = 'open'` to `status = "OPEN"` ⇒ the conflict target no longer byte-matches the partial unique index ⇒ the upsert stops being idempotent ⇒ RED. |

### 3.7 F7 — the regression guard — **INVARIANT MEASURED (DR-35)**

Packs: `tests/integration/test_db_pool_idle_in_transaction_guard.py` (NEW), `tests/integration/test_dbpool_churn_census.py` (NEW), `tests/unit/tools/test_dbpool_residence_static.py` (NEW), `tests/unit/tools/test_dbpool_probe_retention_static.py` (NEW), `tests/unit/tools/test_dbpool_occupancy_static.py` (NEW), `tests/unit/tools/test_dbpool_guard_pack_selfcheck.py` (NEW).

**The invariant (both halves, co-equal):**

- **(a) Occupancy — THREE states, never a count of zero.** The census reports occupancy across **(i)** `idle` with `xact_start IS NULL` (checked out, no statement yet), **(ii)** `idle in transaction`, **(iii)** `active`. A census that counts only `idle in transaction` is **vacuous against two of the three shapes** — the incident's shape was 15/15 slots held with only 6 in `idle in transaction` ⇒ a census-only guard would be **GREEN on the incident** (measured: raw `t2_stat_activity.out:17-18`, `:179-180`). The **assertion** is that `manager.engine.pool.checkedout()` returns to its baseline after a churn cycle.
- **(b) Residence — `xact_age_s` against the operation's own hold budget.** The metric is **`xact_age_s` = `now() − xact_start`** (raw `common.py:88`). The old metric `state_change − xact_start` is **FROZEN** at the last statement's completion ⇒ a leaked backend shows the **same sub-millisecond value** as a healthy one ⇒ the budget **can never fire on the leak** (raw `t2_stat_activity.out:99`). The **2.0 s placeholder is WITHDRAWN** — the measured healthy band is median **0.0005 s** / p95 **0.0007 s** / max **0.0008 s** (n=40, raw `t2_stat_activity.out:88-95`), and the budget compared against is **the operation's own hold budget**, not a pool-wide literal.
- **λ is NOT MEASURABLE** ⇒ the `peak_occupancy ≤ λ × budget` bound is **dropped as an assertion**; it is reported and marked **UNMEASURABLE**.

**The per-site budget-derivation step (Δ3 — a CONCRETE Phase-3 task-graph entry, tester-executed at pin-writing):**
1. **Measure-then-set at pin-writing** — each per-site budget is measured **post-F8** at pin-writing time (NOT Phase-0 — contract §3.4); the measured healthy band (median 0.0005 s / p95 0.0007 s / max 0.0008 s, n=40) is the reference point.
2. **Derivation note recorded** — each budget's derivation note goes in the **F7 pack header** (`tests/integration/test_db_pool_idle_in_transaction_guard.py`) or a **BASELINE addendum**.
3. **NOT-DERIVABLE, never guessed** — a site whose budget cannot be measured records `NOT-DERIVABLE` **+ the exclusion in the pack header** (DR-11 made procedural).
4. **Declaration location pinned** — ONE table in the F7 pack header maps **call-site → budget constant**; **F7-a-static asserts the table's reachability** (every row's constant exists in `daemon/constants.py`).
5. `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S` is measured by this same step — its provenance line reads "**measured post-F8 at pin-writing (NOT Phase-0)**" (contract F2.1 / §2 constants table, Δ3 reconciliation).

| Pin | Pack file | What it asserts | Mutation that MUST redden it |
|---|---|---|---|
| **F7-a** | `tests/integration/test_db_pool_idle_in_transaction_guard.py` | The residence assertion uses **`xact_age_s`** and compares against **the operation's own hold budget**. | Hold a manager-pool connection across a dwell that exceeds **that operation's** budget ⇒ the `xact_age_s` assertion REDs. **A pin written against `state_change − xact_start` is VACUOUS against the leak** — the mutation that proves this pin alive is a **deliberately long `xact_age_s`**, not a long `state_age_s`. |
| **F7-a-static** | `tests/unit/tools/test_dbpool_residence_static.py` | **Static twin (DR-24):** the residence budget constant exists in `daemon/constants.py`, the guard's assertion references it, and the guard's metric is `xact_age_s`; **and (Δ3) every call-site → budget-constant row in the F7 pack-header declaration table resolves to an existing constant** (reachability). | Delete the budget constant, revert the guard's metric to `state_change − xact_start`, **or add a declaration-table row whose constant does not exist** ⇒ RED. |
| **F7-b** | `tests/integration/test_db_pool_idle_in_transaction_guard.py` | Re-introduce a retention path (e.g. return a connection with an open transaction) is detectable. | Re-introduce a retention path. |
| **F7-b-static** | `tests/unit/tools/test_dbpool_probe_retention_static.py` | (1) `make_queue_probe` source contains `SET LOCAL statement_timeout`; (2) the derived value equals `2 * QUEUE_PROBE_TIMEOUT_S`; (3) the **saver** probe does **not** contain `SET LOCAL`; (4) the docstring at `daemon/services/readiness.py:107-113` no longer carries the false premise. | Remove `SET LOCAL statement_timeout` from `make_queue_probe` (or add it to the saver probe, or restore the false docstring) ⇒ twin RED. |
| **F7-c** | `tests/unit/tools/test_dbpool_guard_pack_selfcheck.py` | Self-pin: delete the guard's assertion **and** decrement the count constant ⇒ the self-pin re-reads the source ⇒ still **RED** (A-9). | Delete the assertion + decrement the constant. |
| **F7-d** | `tests/unit/tools/test_dbpool_guard_pack_selfcheck.py` | Static twin: delete / un-mark / move the guard file out of the `integration` placement. | Delete / un-mark / move the guard file. |
| **F7-e** | `tests/integration/test_dbpool_churn_census.py` | **The pin that would have caught the incident:** re-introduce the occupancy defect — hold 2 manager slots across the churn cycle ⇒ `pool.checkedout()` does not return to baseline ⇒ RED. | Hold 2 manager slots across the churn cycle. |
| **F7-e-static** | `tests/unit/tools/test_dbpool_occupancy_static.py` | (1) no `ast.Await` / `run_coroutine_threadsafe(...).result(...)` inside the `with`-body of a manager-engine session scope in `daemon/` (F8-a's shape, the three named sites); (2) the terminal guard and dead-parent guard are re-asserted inside the P2 write scope (F8.6); (3) `inj` is re-read in P2 (F8.7); (4) the message/task/injection writes commit in one transaction (F8.8). | Re-introduce an `await` inside the `with`-body (or drop the re-assertion / split the transaction) ⇒ twin RED. |

**Corrected claim (DR-24):** F7-c is **not** a backstop for F7-a — deleting the **residence assertion alone** leaves F7-c **GREEN**. **F7-a-static** is F7-a's backstop.

### 3.8 F8 — remove the borrow-then-await (the Phase-1 PRIMARY)

Packs: `tests/unit/tools/test_dbpool_fix_site_static.py` (NEW), `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` (NEW).

| Pin | Pack file | What it asserts | Mutation that MUST redden it |
|---|---|---|---|
| **F8-a** | `tests/unit/tools/test_dbpool_fix_site_static.py` | **Static (AST, default-collected):** no `ast.Await` node and no `run_coroutine_threadsafe(...).result(...)` inside the **`with`-body** of a manager-engine session scope in `daemon/`. Scope the rule to the `with`-body only (a function-wide rule false-positives on the legitimate bridge calls at `:11216-11224` / `:11568-11571`); **name the three expected sites** in the docstring so a fourth is a deliberate failure. **DR-26 — the sync twin's bridge must be OUTSIDE the scope** (its call shape and 8.0 s timeout are preserved; only its position changes). | Insert an `ast.Await` or a `run_coroutine_threadsafe(...).result(...)` inside the `with`-body of a manager-engine session scope in `daemon/` ⇒ RED. |
| **F8-b** | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` | Drive the reconcile with a slowed content fetch; assert **no** manager-pool idle-in-txn backend attributable to the reconcile and `pool.checkedout()` back to baseline. **This leg is also the post-F8 re-measurement** the bound rule needs (DR-11). | Drive the reconcile with a slowed content fetch; the assertion REDs if the hold remains. |
| **F8-c** | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` | **DR-10 — the injection point is named:** a **test hook** flips the `ReportInjection` row's `inj.state` to a terminal value (`INJECTED` / `TASK_DELIVERED`) **after P0's scope closes and before P2's scope opens**; the observable is **NO artefacts created** (no message INSERT, no task INSERT, no injection UPDATE past the guard). | Inject a concurrent escalation at the P0→P2 seam ⇒ artefacts are created ⇒ RED. |
| **F8-d** | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` | The write scope's single-transaction commit (message + task + injection) is preserved. | Break the write scope's single-transaction commit (e.g. split the message/task/injection writes) ⇒ RED. |
| **F8-e** | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` | The async twin's hold is bounded (no unbounded `wait_for`-less hold). | Re-introduce the unbounded async-twin hold (remove its `wait_for`/bound). |

### 3.9 Audit-clean + consistency pins

Packs: `tests/unit/tools/test_dbpool_census_families.py` (NEW), `tests/unit/tools/test_dbpool_consistency_pin.py` (NEW), `tests/unit/tools/test_dbpool_audit_record.py` (NEW).

| Pin | Pack file | What it asserts | Mutation that MUST redden it |
|---|---|---|---|
| **G-A** | `tests/unit/tools/test_dbpool_census_families.py` | The census families match the §7.2 **AST baseline** (**A=613 / C=4 / B=175 / D=148 / E=14 / F=0**); the scanner-independent total is **779** and the aggregate (≈689) is marked **NON-CANONICAL** (DR-6). | Any family count delta not classified per §7.3. |
| **G-consistency** | `tests/unit/tools/test_dbpool_consistency_pin.py` | **The W-11 cross-artifact consistency pin.** Its scanner is **WRITTEN DOWN in `final-implementation-spec.md` §6.4** (scan set, regexes, sentinel handling, the four checks). It re-reads that section as its source of truth — it must not carry a private copy of the id sets. | Introduce a gate ID / fix ID / AC ID that is not in the canonical set, or remove/duplicate a sentinel pair, or add a pin to the contract's §5 without adding it here ⇒ RED. |
| **F1-a** | `tests/unit/tools/test_dbpool_audit_record.py` | `audit-findings.md` exists and carries the **AST baseline** (not the retired "≈689"). | Delete `audit-findings.md`, or restore the retired "≈689" figure instead of the AST baseline. |

### 3.10 Pin coverage matrix (all fix IDs covered, nothing orphaned) + **THE ONE COUNT**

| Fix ID | Pins | Count |
|---|---|---|
| F1 | F1-a | 1 |
| F2 | F2-a, F2-b, F2-c, F2-d, F2-e, F2-f, F2-g, F2-h, F2-i, F2-j | 10 |
| F3 | F3-a, F3-b, F3-c, F3-d, F3-e | 5 |
| F4 | F4-a, F4-b, F4-c, F4-d, F4-e, **F4-f** | 6 |
| F5 | F5-b, F5-c, **F5-d** | 3 |
| F6 | F6-a, F6-b, F6-c, F6-c-static, F6-d, F6-e | 6 |
| F7 | F7-a, F7-a-static, F7-b, F7-b-static, F7-c, F7-d, F7-e, F7-e-static | 8 |
| F8 | F8-a, F8-b, F8-c, F8-d, F8-e | 5 |
| G-A | G-A | 1 |
| G-consistency | G-consistency | 1 |
| **TOTAL** | | **46** |

**Inventory rules:** every pin has a **concrete, plantable** mutation (no "any change that alters X", **no TODO cells**); every PG/integration pin has a **default-collected static twin** (W-13 — §3.11); every PG/integration pin asserts **zero skips** in addition to its collected count (DR-17); every pin asserts its **collected count**, sourced from the **independent** `expected-collected.md` declaration (§2.3, DR-23) — a `TBD` cell at gate time = **FATAL** (Δ1: the fence integer-validates `EXPECTED` before comparing). **Total: 46 pins.**

### 3.11 PIN → TWIN MAP (DR-15 — checkable at a glance)

| Pin (PG / integration) | Its default-collected static twin | The static facts the twin asserts |
|---|---|---|
| **F4-b** (`tests/integration/test_dbpool_probe_bound_pg.py`) | **F7-b-static** (`test_dbpool_probe_retention_static.py`) | `SET LOCAL statement_timeout` present in `make_queue_probe`; derived value == `2 * QUEUE_PROBE_TIMEOUT_S`; the **saver** probe excluded; the `readiness.py:107-113` docstring premise corrected |
| **F4-f** (`tests/postgres/test_dbpool_probe_starvation_pg.py`) | **F4-a** (`test_dbpool_probe_bound.py`) | the **F4-A admission pre-check** is present in both probe bodies (the mechanism F4-f exercises) |
| **F5-b** (`tests/postgres/test_dbpool_wedge_capture_pg.py`) | **F5-c** (`test_dbpool_wedge_capture_static.py`) | no `tasks` / `tasks t` in the module |
| **F6-b**, **F6-c** (`tests/postgres/test_dbpool_mission_mint_pg.py`) | **F6-a**, **F6-e**, **F6-c-static** (`test_dbpool_mission_mint.py`) | the upsert is anchor-keyed; the anchor-scoped re-resolve is on the `rowcount == 0` path; `OPEN_EPOCH_STATUS_SQL` is byte-identical to the partial index predicate |
| **F7-a**, **F7-b** (`tests/integration/test_db_pool_idle_in_transaction_guard.py`) | **F7-a-static** (`test_dbpool_residence_static.py`), **F7-b-static** (`test_dbpool_probe_retention_static.py`), **F7-c** (self-pin) | the residence constant exists and the guard references it; the guard's metric is `xact_age_s`; the retention path's static facts hold; the self-pin re-reads the source |
| **F7-e** (`tests/integration/test_dbpool_churn_census.py`) | **F7-e-static** (`test_dbpool_occupancy_static.py`) | no `await` / bridge call inside a manager-engine `with`-body; guards re-asserted in P2; `inj` re-read in P2; one-transaction write |
| **F8-b**, **F8-c**, **F8-d**, **F8-e** (`tests/integration/test_dbpool_deferred_report_reconcile_pg.py`) | **F8-a** (`test_dbpool_fix_site_static.py`), **F7-e-static** (`test_dbpool_occupancy_static.py`) | the AST shape (no `await` in the `with`-body; the bridge **outside** the scope); the guards re-asserted; the single-transaction write |

**Pins with NO twin, and why (each is a default-collected unit or process pin already):** F1-a · F2-a..j · F3-a..e · F4-a · F4-c · F4-d · F4-e · F5-d · F6-d (process) · F7-c · F7-d · G-A · G-consistency.

**Two pins whose twins were previously claimed but were NOT (DR-24, corrected):** **F6-c** is itself a `postgres` pin and **F6-d** is a **process** gate ⇒ neither is a twin; **F6-c-static** is the real twin. **F7-c** is a self-pin that deletes the assertion **and** the constant ⇒ deleting the **residence assertion alone** leaves F7-c **GREEN** ⇒ F7-c is **not** a backstop for F7-a; **F7-a-static** is.
## 4. (d) GATE STRUCTURE per phase

### 4.1 Phase order (from `final-implementation-spec.md` §6.1, plus F8)

| Phase | Contents | Gate |
|---|---|---|
| **P1** | **F8** (F8.1–F8.9) — **the Phase-1 PRIMARY** · F3 (F3.1–F3.5) · **F4 (F4.1–F4.9 — DEMOTED to defence-in-depth, DR-29)** · F6 (F6.1–F6.8) · **F5 (F5.1–F5.3 — RAISED from Phase 2, DR-34)** · F1 (F1.1) | Transaction-hygiene + mechanism pins green; **no** change to the readiness detail schema, leak predicate, grace refusal path or never-green semantics |
| **P2** | F2 (F2.1–F2.10) | Knob matrix pinned; **the engine-level idle-in-txn backstop is DISABLED** (§1.6) — if it is ever enabled, it lands only after F8 |
| **P3** | F7 (§3.7) · the pin packs that freeze F3/F4/F6/F2/F5/F8 | The guard runs in a form the runner **executes** with an asserted collected count; net-new test reds = 0 |

**Sequencing constraints (hard):**
1. **F8 before any engine-level `idle_in_transaction_session_timeout`** (C-4, A6-i). **Currently moot** — the backstop ships DISABLED (§1.6). The constraint is reinstated the moment enablement is contemplated.
2. **F2's `application_name` change and the AC amendments land in ONE commit** (C-2) — AC14/AC15/AC16/T2.4/T2.6/D-19/D-24/G2.4/F2-f/F2-g all change together, or the gate is RED from birth.
3. **The `statement_timeout` DROP lands with the F2 change** (C-3) — D-16, T2.2, AC14 and the Phase-2 objective text.
4. **The asyncpg DSN (F2.6) lands with the F2 change** (C-8) — it must not touch the shared `conn_string`.
5. **G0 is a hard predecessor of every Phase-1 change group** (DR-19): T1.1 (F3), T1.4 (F4), T1.8 (F6), T1.11 (F1) and the F8 group all carry `Depends = G0`; T2.1/T2.2/T2.3 carry `Depends = F8 + G0`.

### 4.2 Canonical gate ladder — **THE ROWS (17 entries; one ID per scope)**

**`final-implementation-spec.md` §6.2a is the authority; this table mirrors it row for row.** Every ID denotes exactly **ONE** scope — no ID may denote three scopes (DR-2). `G-consistency` is a **static pin, NOT a ladder entry** — it rides G1.4.

| Gate | Scope | Contents | Command form (**all carry the FULL §2.2 fence**) | Collected-count assertion |
|---|---|---|---|---|
| **G0** | Phase-0 precondition | `BASELINE.md` exists, is reviewed, and **the two provisional literals are replaced by measurement OR recorded DISABLED** | artifact check | non-vacuity: the artifact is present **and** both literals are resolved — not merely "recorded" |
| **G1.1** | F3 mechanism — unit | F3-a, F3-b, F3-c, F3-d, F3-e | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G1.2** | F6 mint — unit | F6-a, F6-e, **F6-c-static** | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G1.3** | F5 wedge-capture — static | F5-c, **F5-d** | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G1.4** | F8 static + F4 unit/static | F8-a, F4-a, F4-c, F4-d, F4-e, **G-consistency** | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G1.5** | whole-tree base-vs-HEAD | the default-collected corpus, base vs HEAD | Class-5 unit over the whole tree | `collected > 0` on **BOTH** sides; `base_collected`/`head_collected` reconciled against the sum of the declared `EXPECTED_COLLECTED_*`; **mismatch = FATAL** |
| **G2.1** | F2 knob-matrix — unit | F2-a, F2-b, F2-c, F2-d, F2-e, F2-i | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G2.2** | repair-engine isolation — unit | F2-f | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G2.3** | P2-oracle immutability — unit | F2-j, F2-h | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G2.4** | the amended utc-session pin | `test_pg_engine_utc_session.py` amended per **D-37** + **DR-20** (structure, never the literal `60000`) | Class-1 unit | **amended, not "unmodified"**; `collected > 0` |
| **G2.5** | Phase-1 PG legs | F4-b, **F4-f**, F6-b, F8-b, F8-c, F8-d, F8-e | Class-2 + Class-3 | `collected > 0` **and zero skips**; count **equals** `expected-collected.md` |
| **G2.6** | Phase-2 PG legs | F5-b | Class-2 | `collected > 0` **and zero skips**; count **equals** `expected-collected.md` |
| **G3.1** | F7 occupancy + census-reporting legs | F7-e | Class-3 | `collected > 0` **and zero skips**; count **equals** `expected-collected.md` |
| **G3.2** | F7 residence (`xact_age_s`) + guard self-pin | F7-a, F7-c | Class-3 + Class-1 | `collected > 0` **and zero skips** on the PG leg; count **equals** `expected-collected.md` |
| **G3.3** | F7 static backstop pins | F7-a, **F7-a-static**, **F7-b-static**, **F7-e-static** | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G3.4** | F7 reachability + census-family pin | **G-A** | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G3.5** | whole-tree base-vs-HEAD | the default-collected corpus, base vs HEAD (Phase-3 run) | Class-5 unit over the whole tree | `collected > 0` on **BOTH** sides; mismatch = FATAL |

**17 ladder entries — the explicit enumeration:** `G0`, `G1.1`, `G1.2`, `G1.3`, `G1.4`, `G1.5`, `G2.1`, `G2.2`, `G2.3`, `G2.4`, `G2.5`, `G2.6`, `G3.1`, `G3.2`, `G3.3`, `G3.4`, `G3.5`. **No range notation is used anywhere in this lane's artifacts** — every id in the set is listed individually.

**PG gate form:** `… /home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m postgres|integration <paths> -q` (pyproject.toml:95 deselects `integration`/`postgres`/`slow`/`conformance` by default). **A PG gate fails on `skipped > 0`, not only on failures.**

**Legacy → canonical mapping:** the single authoritative table is `final-implementation-spec.md` §6.3, where it lives inside the machine-detectable BEGIN/END marker pair defined there. **This artifact carries NO legacy→canonical mapping and therefore NO exempt region and ZERO marker pairs** — per the contract's §6.4.3 rule, a marker pair is required only in an artifact that carries the mapping, and a pair in an artifact that does not is itself a defect. **This strategy carries no second copy** — a duplicated mapping table is exactly the drift the W-11 pin exists to catch (DR-3/DR-21).

**The W-11 consistency pin** (`G-consistency`) — its scanner is **written down in `final-implementation-spec.md` §6.4** (the scan set over all eight artifacts, the four checks, the sentinel handling, and the rule that the pin re-reads that section rather than carrying a private copy of the id sets).
### 4.3 Cross-phase gate rules (binding on all three phases)

1. **Interpreter certification first** — print `python -c "import daemon,pytest; print(daemon.__file__, pytest.__version__)"` and attach to the gate report.
2. **Collected count asserted > 0 before "0 failed" is read as a pass.** `collected == 0` ⇒ FATAL.
3. **Per-variable env scrub — six variables**: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT`. `env -u POSTGRES_*` is a silent NO-OP.
4. **`HOME="$(mktemp -d)"`** — never a HOME-derived live install path.
5. **`cd /home/nea/ensemble-src-wt-dbpool-idletxn` first** — the `cd` is load-bearing (from `/tmp` the interpreter resolves `daemon` to the shared checkout).
6. **PG gates assert zero skips.** A green PG gate that skipped is a FAILING gate.
7. **Every long pin carries `@pytest.mark.timeout(N)`** with N > worst case. The churn pack's timeout must exceed `N*60 + 300`.
8. **The census gate's non-vacuity cell** is restated as *"each family's count MUST equal its §7.2 baseline constant; any delta is a FAILURE until classified per §7.3"*, and the **diff** is the failing step (the scanner always exits 0 — OQ-7(c)).
9. **Net-new test reds = 0** across all phases (G3.5). Any new red must be classified as pre-existing (via the base-vs-HEAD byte-comm) before the gate passes.
10. **DR-20 — the knob-matrix cell asserts the STRUCTURE, never the literal `60000`:** the clause is present, its value **equals `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`** (imported from `daemon/constants.py`), and the option **ORDER** is pinned. **One cell asserts the constant equals the Phase-0 rule OR is DISABLED with the reason recorded** in the gate report.
11. **DR-23 — the expected collected counts come from the INDEPENDENT artifact** `expected-collected.md` (measured at base; task R-1, tester lane). The pack-local constant applies to **NEW packs only**. **No number is invented in this file.** **A `TBD` cell at gate time = FATAL — the fence integer-validates `EXPECTED` before comparing (Δ1).**
12. **Every PG/integration row's last column is `✅ + zero-skips`** — `tests/postgres/conftest.py:167-169` skips the whole module when PG is unreachable ⇒ a skipped-everything PG gate is indistinguishable from a pass (DR-17).

---

## 5. Open questions for the plan owner

1. **Who folds the plan?** The architect wrote *"Status of the planner's artifacts: unmodified"* (`architecture-recommendation.md:9`). The fold must be performed by the planner, then re-reviewed. Until then the lane proceeds against contradictory ACs. (council-report.md §9.1)
2. **May F8 touch `daemon/tools/instance.py:1207`/`:1360`?** If no ⇒ pin (i) must be scoped narrowly and the residual disclosed (it is a write path under `WriteGuardSession`), and `plan-overview.md` §9 needs a `daemon/manager.py` row (W-8). If yes ⇒ the lane file manifest grows and the W-8 diff-review leg must land with it. (council-report.md §9.2, leader ruling 3)
3. **Sanctioned slow-content-fetch technique for the F8 PG pin:** instrumented checkpointer (leader ruling 4 preferred) vs checkpoint-table lock (fallback). Needed before the flip-condition leg can be written. (council-report.md §9.3, C-12)
4. **Does the `statement_timeout` DROP also change the Phase-2 OBJECTIVE text** (`phase2-plan.md:3`, `:16`)? The council's read: **yes** — the objective must change, not just AC14. (council-report.md §9.4, C-3)
5. **F8's instance.py sites:** if read and concluded to be a different fix shape, state the discrepancy explicitly in the final report so the dispatcher can reconcile — do NOT silently diverge. (dispatch instruction)
6. **Phase-0 harness ownership:** the Phase-0 run is **DONE**; the canonical copy of the measured record is `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md` and the planning-directory mirror is `BASELINE.md`. **Task R-1** (the `expected-collected.md` base measurement, DR-23) is routed to the tester lane.
7. **NEW — F4-A's capacity pre-check needs one decision from the plan owner:** the pre-check compares the pool's occupancy against its **configured** capacity. `QueuePool` exposes `size()` / `checkedout()` / `overflow()` publicly, but the **configured `max_overflow` limit is private** (`_max_overflow`) and `overflow()` returns the *current* overflow, not the limit. ⇒ Either (a) the probe reads the lane-pinned `max_overflow` **constant** (F2.4 leaves it at 10) rather than the pool's private attribute, or (b) the probe uses only `size()` (⇒ it refuses whenever `checkedout() >= size()`, i.e. whenever overflow is in use — more conservative, slightly more false-refusals). **This strategy assumes (a)** — the pinned constant — and the choice must be ratified, because it changes the probe's refusal threshold.
8. **NEW — the enablement of the engine-level backstop is now a standing question for the user, not for this lane.** It ships **DISABLED** (§1.6). Enabling it requires a **trustworthy** measurement of the manager engine's legitimate hold on the **real** fetch path (a booted daemon + a real checkpoint store) — **not** modelled. The follow-up that produces that measurement is **not** in this lane's scope.

---

## 6. References

- `council-report.md` — the 12 CRITICAL + 14 WARNING + 9 ADVISORY findings (authoritative).
- `delta-recheck.md` — the **24 delta conditions** (DR-1..DR-36); **DR-29** is the F4 premise rewrite this strategy is re-based on.
- `research-phase0-raw-verification.md` — the independent adversarial re-check of the Phase-0 raw evidence (58 scored rows; M-1..M-16 weaknesses; the two derivation verdicts).
- **`BASELINE.md`** (this directory) and its **canonical copy** `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md` — the measured record; the raw harness output is `.agents/tester/harness/dbpool-phase0/raw/*.out` (not shipped).
- `final-implementation-spec.md` — the BINDING CONTRACT (this strategy implements its §5 pin inventory and §6.2a gate ladder).
- `architecture-recommendation.md` — architect amendments A1–A12 (input, not artifact of record).
- `plan-overview.md` · `decisions.md` · `phase1-plan.md` · `phase2-plan.md` · `phase3-plan.md` — plan-of-record (to be folded; the sibling workers align them to this file's §3 / §4.2).
- `research-leak-sites.md` · `research-pool-couplings.md` · `research-audit-census.md` · `audit-method.md` · `wi-78967dba-verdict.md`.
- `daemon/manager.py:11516, :11568-11571, :11754-11757, :10860-10861, :11216-11224, :11110-11112, :11529-11541, :11555-11561, :11366/:11383/:11400/:11421/:11430, :11281-11300, :8760` (F8; the conditional `SET LOCAL statement_timeout = 0` carve-out).
- `daemon/tools/ens_db_tools.py:1475-1513, :1482, :1489, :1491, :1530-1538` (F3); `:603-633`, `:787-797` (F2 repair engine; the `SET LOCAL` house idiom).
- `daemon/services/readiness.py:107-113, :113, :127, :1281-1283, :1284-1285, :1463-1481, :1716-1790` (F4).
- `daemon/repositories/factory.py:252, :289, :323-338` (F2; `:289` is the pool-site comment false-positive).
- `daemon/persistence.py:225, :306-315, :316-331, :355-359` (F2; F2 honesty block).
- `daemon/constants.py` (knob canonical home, D-18).
- `daemon/services/wedge_capture/db_capture.py:34, :196-200, :289-294, :308, :351-363, :387` (F5).
- `daemon/repositories/mission_record/repository.py:69-93, :498-514` · `models.py:209, :305-313` · `daemon/manager.py:8548-8552` · `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69` (F6).
- `daemon/services/mission_record_service.py:544-567` (the **skipped** boot re-resolve probe, DR-33).
- `daemon/checkpoint_adapter.py:1229-1239` (the asyncpg explicit transaction).
- `daemon/services/child_reports.py:1654` · `daemon/persistence.py:316-331` (F8 churn leg).
- `tests/unit/repositories/test_pg_engine_utc_session.py:65, :86-88` (D-17 guard + C-9 amendment).
- `tests/postgres/conftest.py:44-45, :82-95, :167-169, :187-188, :191-195, :240-258` (PG harness).
- `tests/integration/conftest.py` (no auto-mark).
- `pyproject.toml:95` (default deselects).

---

*FOLDED by the technical-analysis worker per the dispatch. Source of truth for the binding contract: `final-implementation-spec.md` (council-produced, with C-1..C-12 corrections applied). Every `file:line` is at base `19fc11c8b` and must be re-verified at the implementation base; drift is a plan-amendment trigger. Read-only fold: no daemon/, tests/, scripts/ or payload-tree file was modified.*