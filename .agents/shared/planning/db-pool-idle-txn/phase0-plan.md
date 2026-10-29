# Phase 0 — Empirical baseline (NEW — leader ruling 5)

> ## ✅ STATUS: **COMPLETE** (2026-10-28 — `decisions.md` D-54)
>
> Phase 0 measured all five targets and produced its artifacts on branch. **No implementation change landed before it** (verified: `git log 19fc11c8b..HEAD` = 10 commits, docs/test only; `git diff --name-only 19fc11c8b..HEAD | grep -c '^daemon/'` = **0**). The full completion record with measured values and artifact:line cites is **§0a** below. The canonical measured record is `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md` (one-canonical-copy rule — `plan-overview.md` §3.5 P-9 / D-53); `BASELINE.md` in this dir is the plan's phase-0 input and agrees line-for-line today.
>
> **What the measurements changed downstream (the premise delta, `plan-overview.md` §3.5 P-1..P-10):** F4's premise is REFUTED and F4 is rewritten (phase1-plan.md, D-45); the flip condition becomes a PRECONDITION on the fetch latency (D-46); the idle-in-txn bound ships **DISABLED** (D-47); F7 becomes the three-state + `xact_age_s` form and the 2.0 s residence placeholder is WITHDRAWN (D-48); attribution moves to the per-pool **conninfo** channel (D-49); the census scope widens to **11** pool-creation sites (D-50); F6's trigger is the concurrent double-mint (D-51); F5 is raised into Phase 1 (D-52). Where a measurement contradicted a plan premise, the plan text changed — visible in the diffs of the phase files, not annotated away.

- **Phase objective:** produce the empirical evidence that the council had NO access to (it had no DB, no prod, no scratch cluster). Several claims in the plan set are documentation-only PG-semantics assertions; Phase 0 converts them into MEASUREMENTS before any implementation begins. Phase 0 is a **hard predecessor** of Phase 1 — no Phase 1 code is written until Phase 0 passes. **(Delivered — see §0a.)**
- **Base:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87`. All `file:line` citations are at this commit.
- **Depends on:** nothing (first phase). **Everything depends on this.**
- **Carried from the overview:** couplings C1-C4, risks R1-R19, safety-precedence rule.
- **Sibling artifacts referenced by filename only:** `test-strategy.md` (guard design), `audit-method.md` (census method + counts), `wi-78967dba-verdict.md` (verdict), `final-implementation-spec.md` (binding contract).

---

## 0a. PHASE-0 COMPLETION RECORD — the measured record (2026-10-28)

**Every value below cites an artifact path + line.** Where the RAW harness output and the printed report disagree, the **RAW value is canonical** (`plan-overview.md` §3.5 P-9 / `decisions.md` D-53: the reports were written against an earlier harness run and carry 8 stale/over-stated rows; the plan cites RAW and drops the byte-exact claim).

### 0a.1 Artifacts produced

| Artifact | Path | What it is |
|---|---|---|
| Canonical measured record | `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md` | **THE canonical copy** (one-canonical-copy rule, D-53) |
| Plan-side input | `.agents/shared/planning/db-pool-idle-txn/BASELINE.md` (599 lines) | The plan's phase-0 input; agrees line-for-line with the canonical copy today (divergence NONE — D-53) |
| Harness (**not shipped**) | `.agents/tester/harness/dbpool-phase0/` (`run_all.sh` + 5 target scripts) | The measurement harness; dual-layer timeout, exit 0 on all 5 |
| Raw verbatim output | `.agents/tester/harness/dbpool-phase0/raw/{t1_mr3_hold,t2_stat_activity,t3_pool_attribution,t4_fingerprints,t5_fix_sites}.out` | The verbatim measurements every value below is quoted from |
| Independent raw verification | `.agents/shared/planning/db-pool-idle-txn/research-phase0-raw-verification.md` | A 58-row raw-vs-report score table (the source of P-9's stale-row list) |
| Expected collected counts | `.agents/shared/planning/db-pool-idle-txn/expected-collected.md` | The **independent** count baseline for the ladder — 35 rows, every count cell `TBD (R-1)` (measured by the tester lane, **not** by this phase) |

### 0a.2 No implementation change landed before Phase 0

`git log --oneline 19fc11c8b..HEAD` = **10 commits**, all `docs(...)` / `test(dbpool)` (evidence only). Verified read-only in this worktree:

```
$ git diff --name-only 19fc11c8b..HEAD | grep -c '^daemon/'
0
```

⇒ the phase gate's precondition ("no implementation change lands until Phase 0 completes", `final-implementation-spec.md` §0) **HELD** (D-54).

### 0a.3 Measured outputs

**(a) M-R3 hold distribution** — `BASELINE.md` T1 (L0…L6 table, `BASELINE.md:79-91`); raw `t1_mr3_hold.out`. Medians/maxima in ms, n = 30 per regime unless noted:

| Regime | median | p95 | max | note |
|---|---:|---:|---:|---|
| L0 no load | 7.121 | 8.475 | 21.654 | pg state during await `idle in transaction` 30/30 |
| L1 pool pressure (8 holders) | 46.471 | 49.977 | 55.307 | |
| L2 event-loop peg | 52.385 | 61.927 | 73.601 | |
| L3 pressure + loop peg | 78.846 | 100.525 | 114.502 | the **114.502 ms** figure is the max **un-injected** hold measured in T1 |
| L4 pool over-subscribed (`pool_timeout=3s`) | 11.220 | 12.502 | 12.502 | harness override — the base value is the **30 s** library default |
| L5 loop peg + 1.0 s **modelled** fetch tail | 1029.887 | 1033.342 | **1059.877** | the 1.0 s tail is **MODEL-INJECTED** — `raw/t1_mr3_hold.py:281-282` (`await asyncio.sleep(extra_delay_s)`) |
| L6 sync twin (8 s bridge) | **8011.119** | — | **8011.119** | n=1; `bridge_ms` 8000.320; `bridge_exception: TimeoutError` (`raw/t1_mr3_hold.out`) |

Structural facts on **139/139** iterations (`BASELINE.md:92-98`): the pooled connection identity is unchanged across the suspension (`same_conn_across_suspension_all = True`), the transaction is still open after the await resumes, and the backend is `idle in transaction` for the **whole** hold. **The hold is not an independent cost — it equals the awaited content-fetch latency** (L5: hold median 1029.887 ms vs await median 1026.527 ms; L2: 52.385 vs 47.693).

**(b) The T5b probe-retention table** — `BASELINE.md` T5(b); raw `raw/t5_fix_sites.out:1955-1967`. **RAW values** (D-53):

| Case | budget | timeout | `checkedout` right after the timeout | backend pg state | note |
|---|---|---|---:|---|---|
| **b1** `SELECT 1` | 0.5 s | no (completed) | 0 | `idle` | completed in **5.8 ms** (RAW; the printed report's 5.1 ms is a superseded run) |
| **b2** `SELECT pg_sleep(5)` | 0.5 s | yes at **0.5017 s** | 1 | **`active`** | released at t ≈ 4.52 s — the backend is **not** `idle in transaction` |
| **b3** pool starvation (15 holders) | 0.5 s | yes at **0.5012 s** | **15** (= the holder count, **not** 16) | never got a connection | the probe is a pool **WAITER**; the timeout fires while it waits |

**(c) Residence band** — `BASELINE.md` T2(d) (`BASELINE.md:168-180`): 40 borrow/return cycles on the manager engine ⇒ `n=40 min=0.0003 s median=0.0005 s p95=0.0007 s max=0.0008 s mean=0.0005 s`, `observed_states_during_borrow: {'idle in transaction': 40}`. A borrow closed immediately leaves a **sub-millisecond** residence.

**(d) PG state matrix** — `BASELINE.md` T2 state matrix (`BASELINE.md:157-166`): checked-out-before-first-statement ⇒ `state='idle'`, **`xact_start` NULL**; after the first statement (autobegin) ⇒ `idle in transaction` with `xact_start` set. ⇒ **the census-blindness claim is CONFIRMED** (`BASELINE.md:200-206`): a census over `idle in transaction` alone is blind to the checked-out-`idle` and `active` shapes.

**(e) Attribution channels** — `BASELINE.md` T3(c) (`BASELINE.md:274-305`); raw `raw/t3_pool_attribution.out`: within one options string the **last** `-c` wins; the **conninfo parameter BEATS** `options=-c`; the shared-constant-last hazard is **MEASURED** — `repair_engine_reports_manager_name: true` (`raw/t3_pool_attribution.out:117`); the repair engine's rendered string was `-c statement_timeout=60000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=60000 -c timezone=UTC -c application_name=ensemble-manager`. At base **every** daemon pool reports an empty `application_name` (`BASELINE.md:259-273`).

**(f) The census scope — 11 real pool-creation sites** (+1 comment false-positive at `factory.py:289`), tabulated at `BASELINE.md:237-257`: P2 saver (`persistence.py:316`), asyncpg checkpoint adapter (`persistence.py:355`), boot preflight (`__main__.py:237`), SQLite (`factory.py:184`), latent config-driven PG (`factory.py:215`), **manager engine** (`factory.py:323`, `pool_size=5, max_overflow=10` ⇒ 15 slots, **no** server-side timeouts), **repair engine** (`ens_db_tools.py:633`, `pool_size=2, max_overflow=3`), vacuum NullPool (`ens_db_tools.py:691`), vacuum NullPool (`vacuum_engine.py:79`), external-DB pool (`db_pool_manager.py:261`), one-shot psycopg wedge-capture (`db_capture.py:195`, `application_name='wedge-capture'`, **no pool**). ⇒ the incident's 18 observed backends are **fully explainable by multi-pool occupancy** — no single-pool overflow is needed.

**(g) The F3 abort story — REFUTED for the pool path** — `BASELINE.md` T5(a) (`BASELINE.md:404-427`): in every failure shape (a1 statement fails, a2 the rollback itself fails, a3 the BEGIN fails, a4 the aborted connection is handed straight to the pool) the backend's post-close state is `idle` with `xact_start=NULL` and `last_query='ROLLBACK'`, and the next borrower succeeds — `pool_reset_on_return = ResetStyle.reset_rollback` (SQLAlchemy 2.0.48's library default).

**(h) The incident's journal quote — a SAME-CONNECTION CASCADE** — `BASELINE.md` T5(a) (`BASELINE.md:428-457`): on the wedge-capture path (its **own one-shot** `psycopg.connect`, three reads on one connection, no rollback between them) the first read fails with `UndefinedTable: relation "tasks" does not exist` and the second and third then fail with `current transaction is aborted` ⇒ `reproduces_incident_journal_quote = True`. **The `tasks`→`task` rename is the cascade's ROOT** ⇒ F5 is raised (D-52).

**(i) The F6 trigger — 7 `UniqueViolation`s** — `BASELINE.md` T5(c) (`BASELINE.md:490-540`); raw `raw/t5_fix_sites.out:2040-2053`: **c4** (8-way concurrent, same anchor, **no barrier**) ⇒ `error_count: 7`, `unique_violation_count: 7`, all on `ux_mission_records_anchor_open`; **c4b** (forced overlap) ⇒ `error_count: 5` (`raw/t5_fix_sites.out:2054-2067`); **c2** (sequential) ⇒ `rung: 1`, **no** violation ⇒ the sequential path is already idempotent and the trigger is a **concurrent double-mint**.

### 0a.4 The two derivation verdicts

**Verdict 1 — the engine-level idle-in-txn bound: NOT DERIVABLE ⇒ ships DEFINED BUT NOT APPLIED (D-47).** The bound was specified as "4 × measured max legitimate hold, floor 30 s" (`decisions.md` D-32). The measured max legitimate hold is either **model-injected** (1059.877 ms — L5, `raw/t1_mr3_hold.py:281-282`) or **114.502 ms un-injected** (L3, n=30); 4 × 1059.877 ms = **4.24 s**, so the **30 s floor dominates** — and it **coincides with the legitimate `pool_timeout` wait** (the library default 30 s; the 3 s figure in L4 is a harness override to fit the pack budget, `BASELINE.md` "What was NOT measured"). A 30 s server-side reap therefore risks killing **legitimate waiters**, and the server **TERMINATES** the backend rather than cancelling a statement — measured: `after_wait: null`, `backend_row_present_after_wait: false`, `state_after: "BACKEND GONE (terminated by server)"` (`raw/t2_stat_activity.out:137-140`), client exception `psycopg.errors.IdleInTransactionSessionTimeout`. ⇒ the constant is **defined but not applied**; the rendered connect-args template **omits the clause** while disabled; the daemon logs `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no trustworthy Phase-0 measurement` at boot; enablement = applied **iff** a trustworthy measurement exists (the post-F8 re-measurement is the figure it should use — DR-11).

**Verdict 2 — the F7 residence budget: NOT DERIVABLE as specified ⇒ the METRIC changed and the 2.0 s placeholder is WITHDRAWN (D-48).** The specified metric was `state_change − xact_start` with a budget of "measured P100 legitimate hold + margin (≥2× noise floor, floor 2.0 s)". That metric is **frozen** at the last statement's completion ⇒ a leaked backend shows the **same** sub-ms value as a healthy one (the healthy band measured in 0a.3(c) is median 0.0005 s / p95 0.0007 s / max 0.0008 s over 40 cycles) ⇒ **the budget cannot fire on the leak**. The working metric is **`xact_age_s` = `now() − xact_start`** (already collected — `raw/common.py:88`), and the budget's real job is to catch a hold exceeding **the operation's own** budget. The **2.0 s placeholder is withdrawn** (it is orders of magnitude too loose AND would falsely red the sync twin, which holds ≥ 8 s by construction). ⇒ F7's invariant is rewritten to the **three-state occupancy + `xact_age_s` residence** form (phase3-plan.md T3.1). **λ was NOT measured** — the planned "borrow rate λ" cell of the (b) artifact was never derived (the measured record carries the occupancy series and the residence distribution, not a borrow rate) ⇒ the `peak_occupancy ≤ λ_measured × budget` form is **dropped as an assertion** and is never asserted.

### 0a.5 What was NOT measured (disclosed — carried forward, not resolved)

The **real** `_fetch_subshape_a_content_async` latency (needs a booted daemon + a real checkpoint store — the harness models the python-side half); the **real** `ChildReportsService._get_last_assistant_message` python-side cost; whether the boot's two mint sites **actually** overlapped in the incident window (the trigger *class* is measured, the exact scheduling is **inferred**); the daemon's real `pool_timeout` stall (measured with a `pool_timeout=3s` harness override, **not** the 30 s default); any **production** database state (deliberately out of scope).

---

## 1. Objective

At the end of Phase 0:

1. A **scratch PostgreSQL database** has been created by the lane, used for measurement, and **dropped** at the end — never `ensemble_prod`, never the live daemon's DB, never a restart of anything.
2. Four **evidence outputs** have been produced as artifacts in this planning dir, each with a named instrument and a stated artifact:
   - **(a)** the base-code M-R3 hold duration under real load (settles the flip condition — leader ruling 1);
   - **(b)** the occupancy/residence census shape at base (what F7 must assert — occupancy + residence, NOT an idle-in-txn count — C-10);
   - **(c)** measured PG runtime semantics: last_query fingerprints, application_name attribution per pool (manager / repair / asyncpg adapter), and whether `idle_in_transaction_session_timeout` actually fires as documented (C-7, A-1, A-2);
   - **(d)** the 3-pool attribution census that explains the incident's 18 observed backends vs the 15-slot manager cap (multi-pool ⇒ unattributable without application_name — C-8).
3. A **BASELINE.md** artifact has been written, recording every measurement, its command, its sample, and its artifact path.
4. The **F7 invariant definition** has been derived from measurement (b), not from analogy.
5. The **application_name/attribution model** has been derived from measurements (c)+(d), not from documentation.
6. The **flip condition** (leader ruling 1) has been settled by measurement (a), not by paper.

## 2. Scratch-DB lifecycle (binding)

**NEVER** `ensemble_prod`. **NEVER** the live daemon's DB. **NEVER** a restart of anything. **NEVER** a daemon process. The measurement harness runs against a lane-created scratch database only.

### 2.1 Creation (adopted from the contract's §0 — stricter wording)

Per the binding contract (final-implementation-spec.md §0), Phase 0 runs on a **lane-created SCRATCH PostgreSQL database**:
- **NEVER** `ensemble_prod`. **NEVER** the live daemon's DB. **NEVER** a restart of anything.
- **Port `5433`**, throwaway role, fresh scratch DB per run, dropped at teardown.
- **Per-variable env scrub** — `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT`. `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) — an ambient `POSTGRES_DB=ensemble_prod` survives it.
- Test scripts must NEVER resolve live-shaped HOME-derived install paths (mktemp FAKE_HOME + escape guard).
- Interpreter: `/home/nea/ensemble-src/.venv/bin/python` (C-1).

```bash
# Per-variable env scrub (house rule: `env -u POSTGRES_*` is a silent NO-OP — bash does not glob env names).
# Scrub one variable at a time. Port 5433, throwaway role, fresh scratch DB per run.
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
    createdb -h 127.0.0.1 -p 5433 -U ens_scratch_throwaway ensemble_scratch_dbpool_phase0
```

The scratch database name is `ensemble_scratch_dbpool_phase0`. It is created on a throwaway role on port 5433, NOT on the live daemon's DB, NOT a Docker container (Docker is BANNED in this repo — blueprint "[INSTALL+PG]"). The contract's §0 wording is stricter than this plan's original wording; the contract's wording is adopted.

### 2.2 Lifecycle rules

1. **Created at the start of Phase 0.** Dropped at the end. No intermediate state persists.
2. **No daemon process is started.** The measurements run against the scratch DB using direct `psycopg`/`asyncpg` connections and, where needed, a minimal FastAPI-free harness that imports `daemon.repositories.factory.create_postgres_engine` with the scratch DB's URL.
3. **No `.env` touch.** The scratch DB URL is passed via an explicit env var or CLI arg, never written to any file.
4. **No live DB contact.** The live daemon, the live release symlink, and the promote journal are untouched. The live daemon's DB is never opened, even read-only (W-4's read-only prod SELECT is a Phase 1/2 gate artifact, not a Phase 0 activity).
5. **No prod-data remediation.** Phase 0 measures; it does not fix data.
6. **Teardown is mandatory and verified.** The phase gate asserts the scratch DB is dropped (`psql -c "\l"` does not list `ensemble_scratch_dbpool_phase0`).

### 2.3 Env scrub (binding — per-variable, never glob)

Every command that touches a database uses:

```bash
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest ...
```

`env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) and is **forbidden**. `HOME="$(mktemp -d)"` fences against the two live-install deletions (2026-10-12).

### 2.4 Certified interpreter (binding — C-1)

All commands use `/home/nea/ensemble-src/.venv/bin/python` (pytest 9.0.2, py 3.13.15; `import daemon` resolves to the **worktree** when cwd = worktree). `uv run` is **forbidden** (`uv` is off-PATH; worktree has no `.venv`). `/usr/bin/python3` is **forbidden** (no pytest). Every gate asserts its expected collected count > 0 before "0 failed" is read as a pass.

## 3. Evidence outputs (a)-(d)

### 3.1 (a) — M-R3 hold duration under real load (settles the flip condition)

**Question settled:** does F8's contribution to the 6-backend population matter, or is it marginal? (Leader ruling 1: this is a TEST-TIME determination — the plan must INSTRUMENT for it, not resolve it on paper.)

**What is measured:** the manager-engine slot hold duration of `_reconcile_deferred_report` / `_reconcile_deferred_report_async` (`manager.py:10809` / `:11466`) under a deliberately slowed content fetch, sampling `manager.engine.pool.checkedout()` at ≤ 100 ms + `pg_stat_activity` `state_change`/`xact_start` residence of the reconcile's backend.

**Instrument (named):** an instrumented checkpointer (leader ruling 4 preferred) — a stub whose `aget_tuple` sleeps a known duration, OR a lock on the checkpoint table in the saver's DB. The checkpoint-table lock technique is the **FALLBACK** (leader ruling 4: no cross-pool checkpoint-table locks — other lanes share the same scratch PG and a table lock could perturb them).

**Artifact:** `.agents/shared/planning/db-pool-idle-txn/BASELINE.md` section (a), containing:
- the measurement command;
- the sample rate (≤ 100 ms);
- the M-R3 hold duration (P50, P95, P99, max);
- the manager-engine `checkedout()` delta across the reconcile call;
- the `pg_stat_activity` residence (`state_change`, `xact_start`, `backend_start`);
- the flip-condition verdict.

> **⚠ MEASURED — the verdict form CHANGED (D-46 / DR-30).** The planned verdict was a **constant**: *"hold < 2 s ⇒ F8 marginal ⇒ C collapses to B; hold ≥ 2 s ⇒ F8 load-bearing ⇒ C stands."* The measurement **refutes the constant form**: the hold is **not an independent cost** — it equals the awaited content-fetch latency (L5: hold median 1029.887 ms vs await median 1026.527 ms) ⇒ a threshold written as a constant decides the lane's mechanism on a number that is a **function of the fetch's data**. The verdict is therefore recorded as the **PRECONDITION** *"the M-R3 hold < 2 s **iff** the awaited content fetch < 2 s"*, with the **fetch-latency distribution** as the decision instrument. **Measured verdict: NOT MET ⇒ F8 STANDS** (worst hold 8011.119 ms — the sync twin's 8.0 s bridge, n=1; the async worst 1059.877 ms is **MODEL-INJECTED**, `raw/t1_mr3_hold.py:281-282`). **The real fetch latency is UNMEASURED** — disclosed, not resolved (phase0-plan §0a.5).

**Feeds:** Phase 1 F8 tasks (the flip condition is settled here, not on paper); Phase 2's idle-in-txn bound sizing (C-4); the plan's "Confidence: Medium-High" statement.

### 3.2 (b) — Occupancy/residence census shape at base

**Question settled:** what does F7 actually need to assert? (C-10: the F7 invariant must measure OCCUPANCY AND RESIDENCE with attribution, never a bare count==0.)

**What is measured:** under a churn load on the scratch DB, capture:
- `manager.engine.pool.checkedout()` sampled ≤ 1 Hz (in-memory; the sampler shape already exists at `readiness.py:525-549`);
- `pg_stat_activity` by `application_name`, counting `idle` AND `idle in transaction`, with `state_change` AND `xact_start` for residence;
- residence-time buckets (to distinguish "6 short borrows" from "1 long leak");
- the churn leg's own borrow rate (λ) — measured, not assumed.

**Instrument (named):** the in-memory sampler at `readiness.py:525-549` for occupancy; direct `pg_stat_activity` queries for residence; a churn harness that drives the deferred-report reconcile with a deliberately slow content fetch (§4.4 of final-implementation-spec.md).

**Artifact:** `BASELINE.md` section (b), containing:
- the occupancy time series (checkedout vs baseline); — **delivered**
- the residence-time distribution (buckets); — **delivered**
- ~~the borrow rate (λ, measured)~~ — **NOT DELIVERED (D-48): λ was not derivable from the harness runs ⇒ the `peak_occupancy ≤ λ_measured × budget` bound is DROPPED as an assertion and is never asserted**;
- ~~the derived F7 invariant definition (peak_occupancy ≤ λ_measured × budget; no backend's idle-in-txn residence exceeds the budget)~~ — **DELIVERED IN A DIFFERENT FORM than planned (D-48): the measured three-state occupancy + `xact_age_s` residence invariant (see the measured block below).**

> **⚠ MEASURED — the F7 invariant was REWRITTEN, and two planned cells were NOT delivered (D-48 / DR-35).**
>
> 1. **λ was NOT measured.** The planned "borrow rate (λ, measured)" cell has **no** measured counterpart in the record (the measured (b) carries the occupancy series and the residence distribution, not a borrow rate) ⇒ the `peak_occupancy ≤ λ_measured × budget` form is **DROPPED as an assertion** and is **never asserted** (phase3-plan.md T3.1 asserts a census count that is **REPORTED and BOUNDED**, never `== 0`).
> 2. **The residence metric changed.** The specified `state_change − xact_start` is **frozen** at the last statement's completion ⇒ a leaked backend shows the same sub-ms value as a healthy one (healthy band measured: median 0.0005 s / p95 0.0007 s / max 0.0008 s over 40 cycles) ⇒ the budget **cannot fire on the leak**. The working metric is **`xact_age_s` = `now() − xact_start`**, against **the operation's own** hold budget.
> 3. **The 2.0 s placeholder is WITHDRAWN** — it is orders of magnitude too loose and would falsely red the sync twin (≥ 8 s by construction).
> 4. **The census scope widened** from the idle-in-txn-only shape to the **three-state** form (`idle` with `xact_start IS NULL` / `idle in transaction` / `active`) — a census over `idle in transaction` alone is **vacuous against two of the three measured leak shapes** (D-50 / P-10).

**Feeds:** Phase 3 F7 (the invariant definition is derived here, not asserted by analogy); the F7 pin (F7-e — the pin that would have caught the incident).

### 3.3 (c) — Measured PG runtime semantics

**Question settled:** are the plan's PG-semantics claims (documentation-only) actually true? (C-7: a PG statement_timeout CANCELS THE STATEMENT — it does not terminate the backend. A-1: the BEGIN fingerprint attribution. A-2: the repair-engine dating.)

**What is measured:**
1. **last_query fingerprints:** under a churn load, capture `pg_stat_activity.last_query` for the manager engine's backends. Confirm or refute that `BEGIN` ×2 is attributable to Site A (ens_db_tools.py:1479/:1491) vs generic psycopg3 transaction-opening.
2. **application_name attribution per pool:** confirm that daemon connections carry an empty `application_name` (research-leak-sites.md:173) and that the three pools (manager / repair / asyncpg adapter) are indistinguishable without it.
3. **idle_in_transaction_session_timeout firing:** confirm whether the 60 s bound actually fires as documented (PG resets the timer per statement; M-R3's legitimate dwell ~13-15 s is NOT killable by a 60 s bound — W-2).
4. **statement_timeout semantics:** confirm that a `statement_timeout` cancels the statement (QueryCanceled) but does NOT terminate the backend, and that the abandoned `to_thread` returns and the connection is returned to the pool.

**Instrument (named):** direct `psycopg` connections to the scratch DB + `pg_stat_activity` queries + controlled statement delays (`pg_sleep`).

**Artifact:** `BASELINE.md` section (c), containing:
- the fingerprint table (last_query → count → inferred owner);
- the application_name census (empty vs named, per pool);
- the idle_in_transaction_session_timeout firing test (does it fire at 60 s? at 13 s?);
- the statement_timeout semantics confirmation (QueryCanceled ⇒ connection returned, backend NOT terminated).

**Feeds:** Phase 2 F2 (the application_name model — C-2, C-8); Phase 1 F4 (the statement_timeout semantics — C-7); the RCA follow-up narrative (A-1, A-2).

### 3.4 (d) — 3-pool attribution census

**Question settled:** why 18 observed backends vs the 15-slot manager cap? (C-8: multi-pool ⇒ unattributable without application_name.)

**What is measured:** under a churn load on the scratch DB, capture `pg_stat_activity` grouped by `application_name` (empty for daemon connections) and by `datname`, counting backends per pool. Confirm that the incident's 18 backends span multiple pools (manager 15 + repair 5 + asyncpg adapter 10 + vacuum NullPool 1 + ...) and that, without `application_name`, attribution is impossible.

**Instrument (named):** direct `pg_stat_activity` queries grouped by `application_name` and `datname`, with `backend_start` and `state_change` for residence.

**Artifact:** `BASELINE.md` section (d), containing:
- the per-pool backend count (manager / repair / asyncpg adapter / vacuum / one-shot);
- the total vs the manager cap (18 vs 15);
- the attribution conclusion (multi-pool ⇒ the census cannot attribute without `application_name`).

**Feeds:** Phase 2 F2 (the application_name model — C-8); the plan's "Confidence: Medium-High" statement; the RCA follow-up narrative.

## 4. BASELINE.md output contract

The `BASELINE.md` artifact is written to `.agents/shared/planning/db-pool-idle-txn/BASELINE.md` and contains:

1. **Header:** lane, base commit, scratch DB name, date, certified interpreter path.
2. **Section (a):** M-R3 hold duration (command, sample rate, P50/P95/P99/max, checkedout delta, residence, flip-condition verdict).
3. **Section (b):** occupancy/residence census (occupancy time series, residence-time distribution, borrow rate λ, derived F7 invariant definition). **⚠ MEASURED DEVIATION (D-48):** λ was **not** derived and the invariant was rewritten to the three-state + `xact_age_s` form — see §3.2's measured block.
4. **Section (c):** measured PG runtime semantics (fingerprint table, application_name census, idle_in_transaction_session_timeout firing test, statement_timeout semantics confirmation).
5. **Section (d):** 3-pool attribution census (per-pool backend count, total vs manager cap, attribution conclusion). **⚠ MEASURED DEVIATION (D-50):** the census found **11 real pool-creation sites** (+1 comment false-positive at `factory.py:289`) — the planned "three pools" was a **floor**, not the boundary.
6. **Summary:** the flip-condition verdict, the F7 invariant definition, the application_name model, and the PG-semantics confirmations/refutations.
7. **Commands:** every command used, verbatim, so the measurements are reproducible.

**Canonical copy (D-53 / P-9):** the canonical measured record is `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`; `BASELINE.md` is the plan-side input and must carry a **pointer**, not a fork. Where the two disagree with the RAW harness output, the **RAW value is canonical** (the reports carry 8 stale/over-stated rows from a superseded harness run).

## 5. How Phase-0 outputs feed the later phases

| Phase-0 output | Feeds | How |
|---|---|---|
| (a) M-R3 hold duration | Phase 1 F8 | **⚠ CHANGED (D-46/DR-30):** the flip condition is a **PRECONDITION** on the fetch latency ("hold < 2 s **iff** the awaited content fetch < 2 s"), not a constant; **verdict NOT MET ⇒ F8 STANDS** (worst hold 8011.119 ms). F8's pin (F8-b) measures the same metric before/after. |
| (a) M-R3 hold duration | Phase 2 F2 | **⚠ CHANGED (D-47):** the idle-in-txn bound (C-4) is **NOT DERIVABLE** from this record (max legit hold model-injected or 114.5 ms un-injected; 4× < the 30 s floor, which coincides with the legitimate `pool_timeout` wait) ⇒ the constant ships **DEFINED BUT NOT APPLIED** with the enablement mechanism. |
| (b) occupancy/residence census | Phase 3 F7 | **⚠ CHANGED (D-48):** the F7 invariant is the **measured three-state + `xact_age_s`** form; the 2.0 s placeholder is **WITHDRAWN**; λ was **not** measured ⇒ the `peak_occupancy ≤ λ × budget` bound is **dropped as an assertion**. |
| (b) occupancy/residence census | Phase 3 F7 | The F7 churn leg (F7-e) measures the same metric before/after. |
| (c) PG runtime semantics | Phase 1 F4 | **⚠ CHANGED (D-45):** the statement_timeout semantics (cancels statement, not backend) is confirmed here (C-7) **and** F4's premise is REFUTED (under starvation the probe holds NO connection — it is a pool waiter; when it holds one the backend is `active`, not idle-in-txn) ⇒ F4 is rewritten (F4-A/F4-B/F4-C) and DEMOTED. |
| (c) PG runtime semantics | Phase 2 F2 | **⚠ CHANGED (D-49):** `application_name` attribution works **only via the conninfo parameter** (it BEATS `options=-c`; the shared-constant-last hazard is MEASURED — `repair_engine_reports_manager_name: true`, `raw/t3_pool_attribution.out:117`). |
| (c) PG runtime semantics | RCA follow-up | The BEGIN fingerprint attribution is confirmed/refuted here (A-1). |
| (d) 3-pool attribution census | Phase 2 F2 | **⚠ CHANGED (D-50):** the census scope is **11 real pool-creation sites** (+1 comment false-positive at `factory.py:289`) — the three named engines are a floor, not the boundary. |
| (d) 3-pool attribution census | Plan confidence | The "Confidence: Medium-High" statement is updated based on the measurement. |

## 6. Phase gate (Phase 0 must PASS before Phase 1 implementation begins)

**BINDING (contract §0):** No implementation change in §1 of final-implementation-spec.md lands until Phase 0 COMPLETES and `BASELINE.md` exists and is reviewed. Phase 0's measurement also fixes the two provisional literals in §2 (`MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`, `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`) — they are placeholders until Phase 0 replaces them.

| # | Gate | Non-vacuity requirement |
|---|---|---|
| **G0** (canonical ladder — `final-implementation-spec.md` §6.2a; **this row is the gate row**, not prose) | Phase-0 precondition for every Phase-1 change group: `BASELINE.md` **exists and is reviewed**, and **the two provisional literals are replaced by measurement OR recorded DISABLED** | non-vacuity: the artifact is present **and** both literals are **resolved** — `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` recorded **DISABLED** with the enablement mechanism (D-47) and the residence budget **WITHDRAWN** with the metric replaced by `xact_age_s` (D-48) — not merely "recorded". **STATUS: SATISFIED** (§0a.4) |
| **P0-1** | Scratch DB created and dropped; teardown verified (`psql -c "\l"` does not list `ensemble_scratch_dbpool_phase0`) | The scratch DB existed and was dropped |
| **P0-2** | BASELINE.md written with all four sections (a)-(d), each with a named instrument, a measurement command, and a sample | The artifact exists and is complete |
| **P0-3** | The flip condition is settled by measurement (a), not by paper | Section (a) contains a numeric hold duration and a verdict |
| **P0-4** | The F7 invariant definition is derived from measurement (b), not from analogy | Section (b) contains the derived invariant |
| **P0-5** | The application_name model is derived from measurements (c)+(d), not from documentation | Sections (c)+(d) contain the census |
| **P0-6** | No live DB contact occurred (verified by absence of any write to the live daemon's DB, the live release symlink, or the promote journal) | The phase report states "zero live contact" |
| **P0-7** | No daemon process was started | The phase report states "no daemon started" |
| **P0-8** | No `.env` file was touched | The phase report states "no .env touched" |
| **P0-9** | Whole-tree sweep: net-new reds = 0 versus `19fc11c8b` | The sweep was run and shows 0 net-new reds |

**STATUS: ALL GATES PASS** (P0-1..P0-9 and the G0 precondition — `decisions.md` D-54). The gate table above is **kept unchanged** as the completed phase's record; the canonical ladder carries exactly **one** `G0` id (`final-implementation-spec.md` §6.2a) and this plan's P0-1..P0-9 rows are the phase-local evidence obligations behind it.

**Phase 0 is a MEASUREMENT phase, not an implementation phase.** It produces evidence. It does not change code. It does not change the plan's fix set. It converts documentation-only assertions into measurements so that Phases 1-3 build on evidence, not analogy.

## 7. Tasks

**STATUS: T0.1..T0.8 DELIVERED** (evidence in §0a.1, §0a.3; verdicts in §0a.4). The task table below is kept as the phase's record; **no task is removed and no ID is renumbered** — the measured outcomes are cited in §0a.

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|----------------|----|
| **T0.1** | **Create the scratch DB** (`ensemble_scratch_dbpool_phase0`) on the host's existing PG instance, using the per-variable env scrub | The scratch DB exists and is listed by `psql -c "\l"` | — (DB operation) | — | — | P0-1 |
| **T0.2** | **Measure (a): M-R3 hold duration.** Drive the deferred-report reconcile with a deliberately slowed content fetch; sample `manager.engine.pool.checkedout()` at ≤ 100 ms + `pg_stat_activity` residence. Use the instrumented checkpointer (leader ruling 4 preferred); checkpoint-table lock is the fallback. | A numeric hold duration (P50/P95/P99/max) and a flip-condition verdict | measurement harness (no daemon code) | T0.1 | — | P0-3 |
| **T0.3** | **Measure (b): occupancy/residence census.** Under a churn load, capture occupancy time series + residence-time distribution + borrow rate λ. | The derived F7 invariant definition | measurement harness | T0.1 | — | P0-4 |
| **T0.4** | **Measure (c): PG runtime semantics.** Capture last_query fingerprints, application_name census, idle_in_transaction_session_timeout firing test, statement_timeout semantics confirmation. | Confirmed/refuted PG-semantics claims | measurement harness | T0.1 | — | P0-5 |
| **T0.5** | **Measure (d): 3-pool attribution census.** Capture per-pool backend count + total vs manager cap. | The attribution conclusion | measurement harness | T0.1 | — | P0-5 |
| **T0.6** | **Write BASELINE.md** with all four sections (a)-(d), each with a named instrument, a measurement command, and a sample | The artifact exists and is complete | `.agents/shared/planning/db-pool-idle-txn/BASELINE.md` | T0.2-T0.5 | — | P0-2 |
| **T0.7** | **Drop the scratch DB** and verify teardown (`psql -c "\l"` does not list `ensemble_scratch_dbpool_phase0`) | The scratch DB is gone | — (DB operation) | T0.6 | — | P0-1 |
| **T0.8** | **Phase gate.** Verify P0-1 through P0-9. | All gates pass | — | T0.7 | — | P0-1..P0-9 |

## 8. DO-NOT-TOUCH list for this phase

Phase 0 is a **measurement phase**. It touches **no code**. The DO-NOT-TOUCH list from Phase 1 applies by inheritance, plus:

- **No code changes of any kind.** Phase 0 produces evidence, not code.
- **No daemon process.** The measurements use direct DB connections and minimal harnesses.
- **No `.env` touch.** The scratch DB URL is passed explicitly.
- **No live DB contact.** The live daemon's DB is never opened.
- **No prod-data remediation.** Phase 0 measures; it does not fix data.

## 9. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **P0-1** | The scratch DB `ensemble_scratch_dbpool_phase0` was created and dropped; teardown verified |
| **P0-2** | `BASELINE.md` exists with sections (a)-(d), each with a named instrument, a measurement command, and a sample |
| **P0-3** | The flip condition is settled by measurement (a), not by paper (a numeric hold duration + verdict) |
| **P0-4** | The F7 invariant definition is derived from measurement (b), not from analogy |
| **P0-5** | The application_name model is derived from measurements (c)+(d), not from documentation |
| **P0-6** | Zero live DB contact occurred |
| **P0-7** | No daemon process was started |
| **P0-8** | No `.env` file was touched |
| **P0-9** | Whole-tree sweep: net-new reds = 0 versus `19fc11c8b` |

## 10. Safety-precedence note

Phase 0 is a measurement phase. The safety-precedence rule (plan-overview.md §13) applies:

1. **Measurement over analogy.** Every PG-semantics claim that was documentation-only becomes a measurement obligation. If a measurement contradicts the plan's assumption, the plan is amended, not the measurement.
2. **Scratch over prod.** The scratch DB is the only DB touched. If a measurement requires prod data, the measurement is deferred, not the prod data accessed.
3. **No daemon, no restart.** The measurements use direct DB connections. If a measurement requires a daemon, the measurement is deferred, not the daemon started.
