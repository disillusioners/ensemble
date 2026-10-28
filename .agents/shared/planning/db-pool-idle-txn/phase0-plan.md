# Phase 0 — Empirical baseline (NEW — leader ruling 5)

- **Phase objective:** produce the empirical evidence that the council had NO access to (it had no DB, no prod, no scratch cluster). Several claims in the plan set are documentation-only PG-semantics assertions; Phase 0 converts them into MEASUREMENTS before any implementation begins. Phase 0 is a **hard predecessor** of Phase 1 — no Phase 1 code is written until Phase 0 passes.
- **Base:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87`. All `file:line` citations are at this commit.
- **Depends on:** nothing (first phase). **Everything depends on this.**
- **Carried from the overview:** couplings C1-C4, risks R1-R19, safety-precedence rule.
- **Sibling artifacts referenced by filename only:** `test-strategy.md` (guard design), `audit-method.md` (census method + counts), `wi-78967dba-verdict.md` (verdict), `final-implementation-spec.md` (binding contract).

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
- the flip-condition verdict (hold < 2 s ⇒ F8 marginal ⇒ C collapses to B; hold ≥ 2 s ⇒ F8 load-bearing ⇒ C stands).

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
- the occupancy time series (checkedout vs baseline);
- the residence-time distribution (buckets);
- the borrow rate (λ, measured);
- the derived F7 invariant definition (peak_occupancy ≤ λ_measured × budget; no backend's idle-in-txn residence exceeds the budget).

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
3. **Section (b):** occupancy/residence census (occupancy time series, residence-time distribution, borrow rate λ, derived F7 invariant definition).
4. **Section (c):** measured PG runtime semantics (fingerprint table, application_name census, idle_in_transaction_session_timeout firing test, statement_timeout semantics confirmation).
5. **Section (d):** 3-pool attribution census (per-pool backend count, total vs manager cap, attribution conclusion).
6. **Summary:** the flip-condition verdict, the F7 invariant definition, the application_name model, and the PG-semantics confirmations/refutations.
7. **Commands:** every command used, verbatim, so the measurements are reproducible.

## 5. How Phase-0 outputs feed the later phases

| Phase-0 output | Feeds | How |
|---|---|---|
| (a) M-R3 hold duration | Phase 1 F8 | The flip condition is settled here (hold < 2 s ⇒ F8 marginal; hold ≥ 2 s ⇒ F8 load-bearing). F8's pin (F8-b) measures the same metric before/after. |
| (a) M-R3 hold duration | Phase 2 F2 | The idle-in-txn bound (C-4) is sized as ≥ 4 × measured max legitimate hold, floor 30 s. |
| (b) occupancy/residence census | Phase 3 F7 | The F7 invariant definition is derived here (peak_occupancy ≤ λ_measured × budget; no backend's idle-in-txn residence exceeds the budget). |
| (b) occupancy/residence census | Phase 3 F7 | The F7 churn leg (F7-e) measures the same metric before/after. |
| (c) PG runtime semantics | Phase 1 F4 | The statement_timeout semantics (cancels statement, not backend) is confirmed here (C-7). |
| (c) PG runtime semantics | Phase 2 F2 | The application_name model is confirmed here (C-8). |
| (c) PG runtime semantics | RCA follow-up | The BEGIN fingerprint attribution is confirmed/refuted here (A-1). |
| (d) 3-pool attribution census | Phase 2 F2 | The application_name model is confirmed here (C-8). |
| (d) 3-pool attribution census | Plan confidence | The "Confidence: Medium-High" statement is updated based on the measurement. |

## 6. Phase gate (Phase 0 must PASS before Phase 1 implementation begins)

**BINDING (contract §0):** No implementation change in §1 of final-implementation-spec.md lands until Phase 0 COMPLETES and `BASELINE.md` exists and is reviewed. Phase 0's measurement also fixes the two provisional literals in §2 (`MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`, `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`) — they are placeholders until Phase 0 replaces them.

| # | Gate | Non-vacuity requirement |
|---|---|---|
| **P0-1** | Scratch DB created and dropped; teardown verified (`psql -c "\l"` does not list `ensemble_scratch_dbpool_phase0`) | The scratch DB existed and was dropped |
| **P0-2** | BASELINE.md written with all four sections (a)-(d), each with a named instrument, a measurement command, and a sample | The artifact exists and is complete |
| **P0-3** | The flip condition is settled by measurement (a), not by paper | Section (a) contains a numeric hold duration and a verdict |
| **P0-4** | The F7 invariant definition is derived from measurement (b), not from analogy | Section (b) contains the derived invariant |
| **P0-5** | The application_name model is derived from measurements (c)+(d), not from documentation | Sections (c)+(d) contain the census |
| **P0-6** | No live DB contact occurred (verified by absence of any write to the live daemon's DB, the live release symlink, or the promote journal) | The phase report states "zero live contact" |
| **P0-7** | No daemon process was started | The phase report states "no daemon started" |
| **P0-8** | No `.env` file was touched | The phase report states "no .env touched" |
| **P0-9** | Whole-tree sweep: net-new reds = 0 versus `19fc11c8b` | The sweep was run and shows 0 net-new reds |

**Phase 0 is a MEASUREMENT phase, not an implementation phase.** It produces evidence. It does not change code. It does not change the plan's fix set. It converts documentation-only assertions into measurements so that Phases 1-3 build on evidence, not analogy.

## 7. Tasks

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
