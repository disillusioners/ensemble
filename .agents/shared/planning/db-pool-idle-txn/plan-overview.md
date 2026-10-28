# db-pool-idle-txn — Plan Overview

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`) — "DB pool poisoned by leaked idle-in-transaction connections".
- **Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn` — branch `fix/db-pool-idle-txn`.
- **Base commit (all `file:line` citations in this plan set):** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (= `origin/latest` = the staged **v0.27.11** tree).
- **HEAD verified** at plan time: `git rev-parse HEAD` → `19fc11c8b52239813fd91327ee0c1961c5b6fc87`. ✔
- **Status:** PLANNING ONLY — zero code changes in this lane so far. This plan set is the deliverable.
- **Sibling artifacts (written in parallel, cited by filename only, NOT read):** `test-strategy.md` (F7 guard design/repro/mutation pins), `wi-78967dba-verdict.md` (verdict write-up), `audit-method.md` (borrow-site audit method + counts).

---

## 1. Objective

Repair the two DB-transaction-hygiene mechanisms that are **still live at HEAD** and that together explain the incident's `idle in transaction` backends: (A) a transaction-abort gap in the agent-facing DB repair tool's dry-run path, and (D) unbounded server-side retention of a connection held by an abandoned readiness-probe worker. Alongside them, close the pool-hardening gap on the shared manager engine (F2) and two correctness defects the incident window exposed (F5 wrong table name, F6 non-idempotent mission mint). The lane must leave the soak-gate lane's fail-closed readiness/leak machinery **untouched in behavior** — this lane removes the condition that makes the leak class fire, it does not teach the detector to be lenient.

## 2. Scope

**In scope**

| Fix | Subject | Landing file(s) |
|-----|---------|-----------------|
| F1 | Transaction-hygiene audit — documentation of a clean census + audited-and-clean records for RCA sites B and C | new doc in this planning dir (§7 F1) |
| F3 | `ens_db_repair_execute` dry-run: explicit `BEGIN` outside the rollback-owning `try` | `daemon/tools/ens_db_tools.py` |
| F4 | Readiness-probe timeout-path connection retention | `daemon/services/readiness.py` (+ `daemon/repositories/factory.py` for the engine-level half — see D-04 (probe-scoped) and D-16 (engine-level)) |
| F6 | `mission_records` boot mint: deterministic `UniqueViolation` → idempotent mint | `daemon/services/mission_record_service.py`, `daemon/repositories/mission_record/repository.py` |
| F2 | Manager-engine pool hardening (knob-by-knob) | `daemon/repositories/factory.py`, `daemon/constants.py` |
| F5 | wedge-capture queries `tasks` → `task` | `daemon/services/wedge_capture/db_capture.py` |
| F7 | Regression guard: "no idle-in-transaction backend survives a read cycle" | new PG test pack(s) (Phase 3) |

**Out of scope** (each with a reason — see §10)

- The readiness **detector** (leak predicate, grace refusal path, never-green arming, detail schema) — coupling C1/C2, forbidden.
- The **P2 psycopg checkpoint-saver pool** knobs (`daemon/persistence.py:316-331`) — different pool, different driver, `autocommit=True`; it is the leak-signal ORACLE and must stay intact (research-pool-couplings.md:132).
- The **event-loop** blocking of `manager.list_instances` (research-leak-sites.md:65) — a latency defect, not a transaction defect.
- The **RUNNING-stalled missed-finalize coverage gap** — no periodic sweeper finalizes a JobItem whose backing Task is non-terminal-but-stalled (research-audit-census.md:114-119). Owned by the strand-healer surface, not this lane.
- `mission_records` production-data remediation / index dedup — a data operation, not a code fix.
- The **proxy-side** `coding-free` deprecated-row drop and every other morning-item in the project KV — unrelated lanes.

---

## 3. Root-cause summary — what the evidence actually supports

The RCA (`/home/nea/ensemble-src/.agents/shared/incident-20261026-db-pool-idle-in-transaction.md`, read-only) was **fingerprint-driven**: it attributed five `idle in transaction` backends to four candidate code sites by matching `pg_stat_activity.last_query`. The research pass re-verified every candidate at HEAD and **three of the four attributions do not survive**. This plan states the corrected picture honestly, because the fix set is only defensible if the premise corrections are part of the record.

### 3.1 RCA premise ledger (verified at `19fc11c8b`)

| # | RCA premise | Verdict | Evidence (file:line@`19fc11c8b`) |
|---|-------------|---------|----------------------------------|
| 1 | `ens_db_tools.py:1479/:1491` explicit `BEGIN` outside the `try` ⇒ a failure there skips `session.rollback()` | **CONFIRMED** — precision correction: the BEGIN sits *inside* the outer `try:` (:1475) but *outside* the inner `try/except` that owns the rollback; the outer `except:` (:1513) has **no** rollback | `daemon/tools/ens_db_tools.py:1475/:1479/:1491/:1488/:1501/:1503/:1513` |
| 2 | `/api/instances` list handler leaks ("ORM read path, autobegin without commit/rollback") | **CORRECTED — no structural leak.** The session is context-managed (`with SQLModelSession(...)` at :1383 flat / :1434 root+BFS); "no explicit commit/rollback in the code" is literally true but none is structurally needed. The adjacent **real** observation is different: the session is held open *across in-Python enrichment* (`return self._enrich_instances(db_session, instances), ...` at :1429) | `daemon/repositories/instance/repository.py:1259/:1383/:1429/:1434` |
| 3 | The readiness `queue_freshness` probe "leaks a transaction per tick" | **REFUTED.** The probe SQL is `FROM task` (max-age :201-210, inflight count :228-237) and runs inside `with engine.connect()` (:1752) | `daemon/services/readiness.py:201-210/:228-237/:1752` |
| 4 | The `SELECT count(*) FROM job_queue_items` fingerprint belongs to the readiness probe | **CORRECTED.** The only render-match is `defer_block_resolver._DEFER_PENDING_COUNT_SQL` (:185-191), executed at :274 and :321 inside `with ... engine.connect()` — context-managed, no leak. A second, differently-prefixed count over `job_queue_items` lives in `daemon/tools/instance_read.py:1408-1416` (husk evidence) — also context-managed | `daemon/services/defer_block_resolver.py:185-191/:274/:306/:321`, `daemon/tools/instance_read.py:1408-1416` |
| 5 | `mint_boot_epoch_if_none`: "an exception inside a transaction without a rollback leaks that connection" | **REFUTED as a leak.** Boot-lane callers pass **no** session (`auto_continue_boot_pass.py:1154-1159`, and the pass loop at :721); `insert_mission` opens `with Session(self._engine) as own:` (:510) and commits at :512 — an `IntegrityError` exits the block ⇒ `close()` ⇒ pool reset-on-return rollback. The 4×/boot `UniqueViolation` is **real**; its trigger is **unexplained** (open question, §11) | `daemon/services/mission_record_service.py:499-567/:641-692`, `daemon/repositories/mission_record/repository.py:498-514`, `daemon/services/auto_continue_boot_pass.py:721/:1154-1159` |
| 6 | wedge-capture queries use `tasks`; the table is `task` | **CONFIRMED** — 2 SQL sites + 1 docstring | `daemon/services/wedge_capture/db_capture.py:308/:387`, docstring :34; correct table `daemon/repositories/task/models.py:171` |
| 7 | Pool hardening needs `pool_pre_ping=True`, `reset_on_return='rollback'`, `pool_recycle` | **CORRECTED.** `pool_pre_ping=True` is **already set** (:328). `reset_on_return` is **not overridden** — SQLAlchemy 2.0.48's *library default* `'rollback'` applies, but it is not pinned in code and `reset_on_return` is set nowhere in `daemon/` (research-pool-couplings.md:56). `pool_recycle` is **genuinely absent** on the manager engine (the repair engine is the only site that has one, :609). **The real gap is the absence of server-side `statement_timeout` / `idle_in_transaction_session_timeout`** on the manager engine — only the repair engine carries them (:619-627) | `daemon/repositories/factory.py:252/:323-338`, `daemon/tools/ens_db_tools.py:609/:619-627` |
| 8 | (soak-lane premise 10/11) The readiness probe's *per-tick* leak is not supported; the **timeout-path retention** is real | **CONFIRMED and sharpened.** A timed-out `asyncio.to_thread` worker keeps running; the module's own docstring calls it "bounded leakage" — but on the manager engine that bound **does not exist** (no server-side timeout, no client-side execute timeout): the retention is unbounded *in theory* | `daemon/services/readiness.py:107-113/:1463-1481/:1746-1789`; soak `decisions.md` §2 premises 10-11 |

**Net:** of the RCA's "4 candidate leak sites", **one is a genuine structural defect** (A), **one is a genuine but differently-shaped defect** (D — retention, not a per-tick leak), **two are not leaks at all** (B, C — audited clean; E — a *logic* defect, not a leak), plus **one non-pool correctness defect** (F).

### 3.2 The borrow-site census — the honest headline (F1)

A full enumeration of session/connection borrow sites in `daemon/` at HEAD audited **≈689 sites across 6 families and found ZERO unclosed borrows** — every borrow is `with`-form, `try/finally`-form with rollback+close, or an ownership-transferring proxy (method + counts: `audit-method.md`). The one non-`with` session family (4 sites) was verified individually and is clean. **There is no "leaked session" class in this codebase at HEAD.** The hazard set is narrower and different: transaction-state/retention hazards on close-clean borrows (the RISK table in `research-audit-census.md` §1c — R1 = the BEGIN gap, R2 = probe retention, R3 = borrow-then-await amplification).

This is the single most important correction to the RCA's framing, and it is why F1's fix content collapses into F3/F4/F6 plus an audited-and-clean record.

### 3.3 Reconciling the five observed backends — **INFERENCE, explicitly labeled**

The incident observed **5 `idle in transaction` backends** with these `last_query` fingerprints: `BEGIN` ×2, `SELECT instances.instance_id, instances.project_id, ...` ×2, `SELECT count(*) FROM job_queue_items ...` ×1 (RCA :54-61).

| Fingerprint | Count | Most-consistent origin | Confidence |
|---|---|---|---|
| `BEGIN` | 2 | **Site A** — the only `text("BEGIN")` in `daemon/` is `ens_db_tools.py:1479/:1491` (grep: zero other hits). A BEGIN left open on failure is exactly the "idle in transaction, last query = BEGIN" state | **CONFIRMED attribution** (the site exists and the fingerprint is unique to it) |
| `SELECT instances.instance_id, ...` | 2 | **Not a leak.** This is the ORM render of `SQLModelInstanceRepository.list`. A request in flight holds one scope-transaction open while the daemon does the BFS + `_enrich_instances` work in Python (repository :1429). Under pool pressure these are ordinary in-flight transactions whose *last* statement was the SELECT — i.e. the fingerprint identifies "a request is mid-flight", not "a connection was abandoned" | **INFERENCE** — consistent with the event-loop-starved in-flight-request shape |
| `SELECT count(*) FROM job_queue_items ...` | 1 | **Site C's real owner** — `defer_block_resolver._DEFER_PENDING_COUNT_SQL` (:185-191), executed context-managed at :274/:321. Like the row above, the fingerprint identifies a query in flight on a connection that is *about to* be returned | **INFERENCE** — the site is confirmed; the "leaked" reading is not |

**The reconciliation, stated as one inference:** the 2 `BEGIN` backends are the *only* fingerprints that evidence a genuine defect (Site A). The other three are consistent with **event-loop-starved in-flight requests** — the D-driven pressure loop (probe timeout → readiness red → retry → more DB demand) starves the event loop, so request threads that have *already* opened their scope-transaction sit idle-in-transaction while waiting for the loop to schedule their next step, and their `last_query` is whatever SELECT they last issued. Under that reading, B and C are **amplifiers/observables**, not leaks, and the causal chain is **D (retention) + A (abort gap) → pool pressure → event-loop starvation → more idle-in-txn backends**. This is consistent with the RCA's own feedback-loop sketch (RCA :94-102) and with the census's R2/R3 entries.

**Two honesty caveats that bound this inference** (both from `research-leak-sites.md` §5):
1. `application_name` is **empty** for daemon connections (research-leak-sites.md:173), so `pg_stat_activity` **cannot** distinguish manager-engine from repair-engine backends post-hoc. The `BEGIN` attribution is unique by *SQL text*, not by pool identity.
2. The repair engine carries `idle_in_transaction_session_timeout=60s` (:619-627) and **should** have reaped a leaked BEGIN backend within a minute. Either the live v0.27.8 binary predates that `connect_args` option (**dating not verified in this pass**) or the backends were on a different engine. **Unresolved** — see §11 OQ-2.

### 3.4 The mechanism that actually persists at HEAD

Two defects survive the census and the premise corrections:

- **M1 — transaction-abort gap (Site A).** `session.execute(text("BEGIN"))` executes outside the rollback-owning inner `try`. A failure at the BEGIN (or at the DDL-branch `rollback()` itself) reaches the outer `except:` (:1513) with **no rollback**, leaving the repair engine's session in a begun/aborted transaction for the rest of the tool call. The engine's 60s `idle_in_transaction_session_timeout` is the only backstop. Repair: **F3**.
- **M2 — unbounded server-side retention (Site D).** On probe timeout the abandoned `to_thread` worker keeps its connection checked out; the manager engine has **no** server-side `statement_timeout` and **no** `idle_in_transaction_session_timeout`, so the retention is unbounded server-side. Repair: **F4** (probe-scoped server bound) + **F2** (engine-level backstop).

**Amplifier (not a leak):** the manager engine's small pool (`pool_size=5`, `max_overflow=10` — 15 slots) with a 30s default `pool_timeout` and no server-side timeouts turns any retention into pool starvation → probe timeouts → readiness red → retry → more demand (research-audit-census.md:96-97). Repair: **F2**.

---

## 4. Per-fix mapping table

| Site | Fix | file:line@`19fc11c8b` | Fix pattern (design-level) | Verification (Phase) |
|---|---|---|---|---|
| **A** — repair-tool dry-run BEGIN | **F3** | `daemon/tools/ens_db_tools.py:1479` (DDL), `:1491` (DML); outer `try:` :1475, outer `except:` :1513 | **Drop the explicit `BEGIN`** (SQLAlchemy autobegins on the next `execute`; the ROLLBACKs carry the "does not commit" property) **and** move the rollback into a `finally` so every exit path rolls back — including a failure *of* the rollback | P1: unit pin (no `text("BEGIN")` remains; rollback on every exit path, incl. SQL-error and rollback-error paths); no test asserted `text("BEGIN")` before this change (grep: zero hits) |
| **D** — readiness-probe retention | **F4** | `daemon/services/readiness.py:107-113` (docstring), `:1477` (DB probe connect), `:1752` (queue probe connect), `_guarded` seam :1262-1295 | **Probe-scoped server-side statement bound**: `SET LOCAL statement_timeout` inside each probe's own transaction (house idiom, `ens_db_tools.py:787-797`) sized at 2× the probe's asyncio budget, PG-only (SQLite has no `SET LOCAL`), + an explicit end-of-transaction before the connection is returned. Engine-level backstop lands in **F2** | P1: PG-backed probe-timeout pin (a probe forced to exceed its budget leaves **no** idle-in-transaction backend behind); PG-only guard pinned |
| **E** — mission boot mint | **F6** | `daemon/services/mission_record_service.py:499-567` (IntegrityError handler), `:641-692` (rung-1 probe + boot entry point), `daemon/repositories/mission_record/repository.py:498-514` (own-session insert), `:69-93` (rung-1 probe) | **Idempotent mint**: replace the own-session ORM insert with a dialect upsert `ON CONFLICT (anchor_instance_id) WHERE <open-predicate> DO NOTHING` (house precedent `daemon/repositories/job_queue/repository.py:485-496`) + re-resolve via the **anchor-scoped** rung-1 probe (which uses the *same* predicate as the index) instead of the `founding_message_id`-keyed probe that can never match a NULL-founding boot epoch. **No schema change** — the partial unique index already exists in 3 places | P1: unit pin (upsert shape, dialect selection, re-resolve key) + PG pin (second mint returns the winning epoch, no `IntegrityError`, **and** the caller's transaction is not poisoned) |
| **F** — wedge-capture table | **F5** | `daemon/services/wedge_capture/db_capture.py:308`, `:387`, docstring :34 | Rename `tasks` → `task` (2 SQL sites + 1 docstring). Correctness fix — this path rides a **one-shot `psycopg.connect`** with no pool, `close()` in `finally` (:289-294) | P2: PG pin (wedge-capture read returns rows; no `relation "tasks" does not exist`); assert the *second* query on the same connection no longer fails with `current transaction is aborted` |
| **B** — `/api/instances` list | **F1 doc** | `daemon/repositories/instance/repository.py:1259/:1383/:1429/:1434`; `daemon/routers/instances.py:370-476`; `daemon/services/instance_lifecycle.py:5021` | **No code change.** Audited-and-clean record + the RCA premise-2 correction | P1: doc artifact only (audit-findings.md) |
| **C** — `job_queue_items` count | **F1 doc** | `daemon/services/defer_block_resolver.py:185-191/:274/:306/:321`; `daemon/tools/instance_read.py:1408-1416` | **No code change.** Audited-and-clean record + the RCA premise-4 correction | P1: doc artifact only |
| **P1 pool** — manager engine | **F2** | `daemon/repositories/factory.py:323-338` (+ `:252` connect-args home), `daemon/constants.py` (canonical knob home; `factory.py:14` already imports `BOOT_DB_TIMEOUT_S` from it) | **Knob-by-knob hardening** — exact values + rationale per knob in `phase2-plan.md` §2 and `decisions.md` D-14, D-16, D-17, D-18* | P2: the existing knob-pin file `tests/unit/repositories/test_pg_engine_utc_session.py` (which already captures `create_engine` kwargs at :104-117) is **extended** to assert the complete final knob set |
| **Guard** | **F7** | greenfield — no idle-in-transaction assertion exists anywhere in `tests/` today (grep: only the vacuum-engine timeout *strings* at `tests/unit/test_run_vacuum_full_after_cleanup.py:192/:212`) | PG-backed regression guard; design/repro/mutation pins specified in `test-strategy.md` | P3: gate structure in `phase3-plan.md`; **house runner form + expected-collected-count discipline** (coupling C3) |

---

## 5. Phases

| # | Name | File | Objective | Depends on |
|---|------|------|-----------|------------|
| 1 | Transaction hygiene | `phase1-plan.md` | F3 (BEGIN gap) + F4 (probe retention) + F6 (idempotent mint) + F1 (audit documentation for B/C) | — |
| 2 | Pool hardening | `phase2-plan.md` | F2 (manager-engine knobs, exact values) + F5 (wedge-capture table) | Phase 1 (the connect-args half of F2 is sized in Phase 1's context; the final knob table is pinned in Phase 2) |
| 3 | Regression guard | `phase3-plan.md` | F7 (idle-in-transaction guard) + the pin packs that freeze F3/F4/F6/F2/F5 | Phases 1-2 |

### 5.1 Phase gates (summary; per-phase detail in the phase files)

| Phase | Gate | Non-vacuity requirement |
|---|---|---|
| 1 | Transaction-hygiene pins green; **no** change to the readiness detail schema, leak predicate, grace refusal path, or never-green semantics | Default `pytest` run must **assert the expected collected count** of the new packs (> 0) before "0 failed" is read as a pass (coupling C3) |
| 2 | Pool-knob matrix pinned (exact values per knob); wedge-capture read proven against a real PG | The knob pin is a **default-collected** unit pack (no marker) so a bare `pytest` run collects it; the PG pin runs in the house fenced form with an asserted collected count |
| 3 | The guard pack runs in a form the runner **executes**, with an asserted expected collected count; net-new test reds = 0 | `--override-ini="addopts=" -m postgres` (or `-m integration`) **plus** `--collect-only` count assertion before interpreting "0 failed" |

---

## 6. Coupling Map — binding constraints (violating any = plan rejection)

### C1 — The leak detector and its refusal path are fail-closed BY DESIGN

`pool_leak` is **never** grace-eligible, and the refusal is unconditional (soak `requirements.md` §4.4 P4; `risk-safety-register.md` R-MASK-4). This lane's purpose is to make the signal **stop firing** because the condition is gone — not to make it fire less often.

**Therefore this plan proposes NO change to:** the leak predicate (`daemon/services/readiness.py:784-941`), the exhaustion/trend arms, the conjunction at :734-743, the streak arm, the grace arming window, the detail schema (:408-446), the P1/P2 sampling surfaces (:525-582), the `daemon/api.py:3939-3987` wiring, or the `daemon/constants.py` grace/leak constants.

**What WOULD break C1 and is therefore REJECTED** (see `decisions.md` D-04):
- A **dedicated readiness-probe engine/pool**. Moving the DB/queue probes off `manager.engine` would let the readiness `database` component report green while the manager pool is poisoned — the probe would stop observing the condition it exists to observe. That is detector leniency by relocation. **Rejected on C1 grounds**, not merely as "more moving parts".
- Any threshold change in the leak predicate or grace horizon.

### C2 — No conditional weakening near rollback; never-green stays UNCONDITIONAL at cap-reach

The promote-soak semantics (3 consecutive greens, never-green **unconditional** at cap-reach) live in `scripts/upgrade/lib.sh` and are pinned by the soak lane's own mutation legs + `tests/unit/tools/test_readiness_failure_class.py`. This lane touches **none** of that surface. The coupling that matters here is the *direction*: this lane's changes make the P2 oracle's `requests_waiting`/`pool_available` signals healthier, which lets a healthy release reach 3 greens and survive. That is the intended outcome — the leak class stops arming because the pool stops being poisoned, and the never-green rule remains the unconditional backstop for every other cause.

### C3 — FALSE-GREEN DISCIPLINE (every gate must run in a form the runner EXECUTES)

`pyproject.toml:95` sets `addopts = "-m 'not integration and not postgres and not slow and not conformance'"` — **default `pytest` collects ZERO PG tests** and ZERO integration-marked files. A bare `pytest tests/integration/…` reports "0 failed" with **nothing executed** (soak `plan-overview.md:362-366`; the house rule is restated in the soak lane's `requirements.md:1133/:1169-1172`).

Every gate in this plan set therefore:
1. runs in the **house form** — `uv run python -m pytest --override-ini="addopts=" -m postgres <paths> -q` (or `-m integration` for scratch-DB packs);
2. **asserts the EXPECTED COLLECTED COUNT** — `pytest <paths> --collect-only -q` must print the expected N (> 0) — **before** "0 failed" is read as a pass;
3. names the interpreter explicitly (`python` does not exist on this host; `uv` lives at `/home/nea/.local/bin/uv`);
4. scrubs ambient `POSTGRES_*` **per variable** (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT`) — **`env -u POSTGRES_*` is a silent NO-OP** (bash does not glob env names; project convention note, 2026-10-21).

### C4 — Line numbers from the incident snapshot are STALE

Every `file:line` in the RCA was captured against the Oct-26 live binary and **drifted** since. All citations in this plan set are re-verified at `19fc11c8b` (I re-read each load-bearing site during planning: `ens_db_tools.py:1475-1513`, `factory.py:245-338`, `readiness.py:100-135/:1470-1485/:1745-1760`, `db_capture.py:30-40/:300-315/:383-393`, `mission_record_service.py:495-570/:641-695`, `mission_record/repository.py:69-93/:495-520`, `persistence.py:300-335`, `constants.py:195-232`, `pyproject.toml:85-99`). The implementation phase must re-verify at its own base and treat drift as a plan-amendment trigger, not as an implementation detail.

---

## 7. Risks

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| **R1** | **Global `statement_timeout` on the manager engine** could kill a legitimately long repository query (> the proposed bound). The bound must be generous enough for the slowest known manager-engine statement | medium | The per-probe `SET LOCAL` bound (F4 primary) is **independent** of the engine-wide cap: if the architect judges the global cap too broad, **drop it without reopening F4** (safety-precedence narrowing, D-14, D-16, D-17, D-18c). Bind the value to the in-repo precedent `SELECT_TX_STATEMENT_TIMEOUT_MS = 30_000` (`ens_db_tools.py:191`) and disclose |
| **R2** | **`idle_in_transaction_session_timeout` on the manager engine** could kill a request that legitimately holds a transaction across > the bound of in-Python work. Known hot spot: `SQLModelInstanceRepository.list` holds the session open across `_enrich_instances` (repository :1429); the incident's endpoint already took 8s under load | medium | Choose 60s (the repair-engine precedent `REPAIR_IDLE_IN_TXN_TIMEOUT_MS = 60_000`, `ens_db_tools.py:188`) — ~7× the observed worst case. The kill is **fail-loud** (the statement errors, the request fails) and `pool_pre_ping=True` + `reset_on_return='rollback'` retire the dead backend. Disclose the hot spot; a follow-up may move the enrichment outside the session |
| **R3** | **`pool_recycle`** forces periodic reconnects; too short ⇒ churn under load, too long ⇒ stale exposure | low | Value chosen to match the single in-repo recycle precedent (`REPAIR_POOL_RECYCLE_S = 3600`, `ens_db_tools.py:182`). At 15 slots, one recycle per hour is ~15 reconnects/hour — negligible |
| **R4** | **F3 changes the observable preview text** ("EXECUTED INSIDE BEGIN/ROLLBACK") and the `pg_stat_activity.last_query` fingerprint | low | The wording change is cosmetic and must be asserted by the pin; the fingerprint removal is an *intended* consequence — a future occurrence can no longer be mis-attributed to a dry-run preview (D-01) |
| **R5** | **F6's upsert changes the mint's error contract** — a caller that today *expects* the `IntegrityError` to propagate may stop seeing it | medium | Scope F6 to the **own-session (boot) path** only (`session is None`); the caller-session path keeps its documented "poison the caller's txn and re-raise" semantics untouched. The boot path's current behavior (raise → fail-open wrapper catches at `mission_record_service.py:1379-1384`) is preserved as "return the winning epoch" |
| **R6** | **The unexplained 4×/boot `UniqueViolation` trigger** may be a *data* condition (pre-index duplicate OPEN rows) rather than a code race. An idempotent upsert masks the *symptom*; a data condition would remain | medium | The upsert is robust to **any** trigger (it is idempotent by construction). The trigger investigation is an explicit **open question** (§11 OQ-1) with a named evidence pull (boot-log lines + a `mission_records` census) — routed to the architect, not silently dropped |
| **R7** | **The guard (F7) is PG-dependent and therefore invisible to a default `pytest` run** — a green default run proves nothing about it | medium | The guard pack's expected-collected-count assertion is a **first-class gate artifact** (C3): the gate FAILS if the collected count is 0, not if a test fails. A second, default-collected **static** pin asserts the guard file exists, is marked, and is reachable under the house runner form |
| **R8** | **`SET LOCAL statement_timeout` is PG-only** — the SQLite install path would raise `OperationalError` | medium | PG-only guard, exactly as `ens_db_tools.py:793` does it; the SQLite branch must be proven unchanged by a pin |
| **R9** | **The probe's server-side bound changes the probe's *classification*** (a statement killed server-side may surface as a probe exception rather than a client timeout) | low | The bound is set to **2× the asyncio budget** so the client-side `wait_for` fires **first** and the client-side classification (`probe_timeout`) is unchanged; the server-side kill then releases the connection shortly after. Pinned |
| **R10** | **C1 violation by relocation** — a future maintainer may "fix" the probe starvation by moving the probes to their own pool, re-introducing detector leniency | medium | The rejection is written into `decisions.md` (D-04) and mirrored in a code comment at the probe-wiring site, so the reasoning survives the lane |
| **R11** | **Scope creep into the readiness detector** — a "helpful" implementer may weaken a threshold to make a gate pass | high | The DO-NOT-TOUCH list (research-pool-couplings.md §Q4) is reproduced verbatim in `phase1-plan.md` §5 and re-checked at the phase gate by a diff review of `daemon/services/readiness.py` (the ONLY permitted change is the two probe bodies' timeout wiring) |
| **R12** | **The `constants.py:207-217` honesty block becomes stale** once the leak is fixed — it names this lane as the owner of an unfixed defect | low | Phase 2 updates the block (and `persistence.py:306-315`) to state that the P1 leak is repaired by this lane, with the repair's date/branch. Not optional — a stale honesty block is a documentation defect the house explicitly tracks |

---

## 8. Success Criteria

- **SC1 — No BEGIN fingerprint survives.** `grep -n 'text("BEGIN")' daemon/tools/ens_db_tools.py` returns **zero** hits; the dry-run path rolls back on **every** exit path (SQL-success, SQL-error, rollback-error), pinned by a unit pack that is **default-collected** (no marker) and asserts a non-zero collected count.
- **SC2 — The probe's retention is server-bounded.** Under a forced probe timeout, a real PG shows **zero** backends in `idle in transaction` attributable to the probe within the probe's bound (2× budget); the pack asserts the collected count before reading "0 failed".
- **SC3 — The boot mint is idempotent.** Two consecutive mints for the same anchor return the **same** `mission_id`, raise **no** `IntegrityError`, and leave the caller's transaction un-poisoned. Pinned on real PG.
- **SC4 — The knob set is exact and pinned.** The final `create_engine` kwargs for the manager engine are asserted value-by-value in `tests/unit/repositories/test_pg_engine_utc_session.py` (the file that already captures those kwargs), including `pool_size=5`, `max_overflow=10`, `pool_pre_ping=True`, `pool_reset_on_return='rollback'`, `pool_recycle=3600`, `pool_timeout=30`, and the connect-args timeout options.
- **SC5 — Pool sizing is NOT regressed.** The saver (P2) pool's knobs are byte-identical before/after (`daemon/persistence.py:316-331` untouched); the manager engine stays at 5+10; the soak lane's `tests/unit/tools/test_checkpoint_pool_config.py` and the readiness/grace unit packs stay green.
- **SC6 — The detector is untouched.** `daemon/services/readiness.py` changes are limited to the two probe bodies' timeout wiring; the leak predicate, conjunction, streak arm, detail schema, grace constants, and `daemon/api.py` wiring are byte-identical (verified by diff review at the gate).
- **SC7 — wedge-capture reads work.** The capture returns executing-turn rows and resume-history rows against a real PG, and neither read fails with `relation "tasks" does not exist` nor `current transaction is aborted`.
- **SC8 — The guard is real.** The F7 pack runs in the house runner form, asserts its expected collected count (> 0), and fails when a deliberately-planted idle-in-transaction backend is present (mutation-proven — the pin is RED with the defect present and GREEN without it, per `test-strategy.md`).
- **SC9 — Zero net-new test reds.** Whole-tree sweep shows no new failures versus the base; any pre-existing red is classified as pre-existing with a citation.
- **SC10 — The audit record is honest.** The delivered audit-findings document states **0 unclosed borrows across ~689 audited sites** with the method cited (`audit-method.md`), and records B and C as audited-and-clean with the RCA premise corrections — it does **not** claim a leak was found where the census found none.

---

## 9. Coupling matrix (phase × shared surface)

| Shared surface | P1 (F3/F4/F6/F1) | P2 (F2/F5) | P3 (F7) | Coupling note |
|---|---|---|---|---|
| `daemon/services/readiness.py` | **F4 only** — the two probe bodies' timeout wiring | — | reads (no write) | C1: nothing else in this file may change |
| `daemon/repositories/factory.py` | context only (F4 sizes the engine-level half) | **F2 — the knob set** | reads | P2 owns the final knob values; P1 must not land a competing knob |
| `daemon/constants.py` | — | **F2 — new knob constants + honesty-block refresh** | reads | Canonical knob home (`factory.py:14` precedent) |
| `daemon/tools/ens_db_tools.py` | **F3** | — | reads | Repair engine knobs are **out of scope** (already hardened) |
| `daemon/services/mission_record_service.py` + `daemon/repositories/mission_record/repository.py` | **F6** | — | reads | — |
| `daemon/services/wedge_capture/db_capture.py` | — | **F5** | reads | — |
| `daemon/persistence.py` (P2 saver pool) | **DO NOT TOUCH** | **DO NOT TOUCH** | reads | C2 + research-pool-couplings.md:132 — the oracle must stay intact |
| `scripts/upgrade/lib.sh` (soak semantics) | **DO NOT TOUCH** | **DO NOT TOUCH** | — | C2 — never-green stays unconditional |
| `daemon/services/readiness.py` leak machinery (:312-983), `daemon/api.py:3939-3987` | **DO NOT TOUCH** | **DO NOT TOUCH** | reads | C1 |

---

## 10. Out of scope — with reasons

1. **The readiness leak detector / grace refusal / never-green semantics** — coupling C1/C2. The fix must make the signal stop firing, never make the detector lenient.
2. **The P2 psycopg saver pool knobs** (`daemon/persistence.py:316-331`) — a different pool with `autocommit=True` (so its connections cannot sit idle-in-txn by construction) and a different reset semantic; it is the leak-signal ORACLE. research-pool-couplings.md:132.
3. **The asyncpg checkpoint-adapter pool** (`daemon/persistence.py:355-359`) — max-size-only knob, separate driver/failure domain, and not a leak site.
4. **`manager.list_instances` running synchronously on the event loop** (`daemon/routers/instances.py:476`) — a real latency defect, but a latency defect. It is *mentioned* in this plan as a contributing factor to the §3.3 inference, and routed to a separate commission.
5. **Moving `_enrich_instances` outside the session scope** (`daemon/repositories/instance/repository.py:1429`) — tempting (it would shrink the idle-in-txn window on the hot path) but it is a repository-semantics change to a shared read path, unrelated to the leak class. Recorded as a **follow-up** under R2.
6. **The RUNNING-stalled missed-finalize gap** — no sweeper finalizes a JobItem whose backing Task is non-terminal-but-stalled (research-audit-census.md:114-119). The `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_*` surface (v0.26.4) is the adjacent owner; this lane does not touch it.
7. **`mission_records` production-data remediation** (deduping pre-index OPEN rows) — a data operation on production, requiring its own authorization; the code fix is idempotent either way.
8. **A dedicated readiness-probe engine/pool** — rejected on C1 grounds (D-04).
9. **Per-probe asyncio cancellation of a synchronous DBAPI call** — impossible: `asyncio` cannot cancel a running thread's synchronous DBAPI call. The saver probe's `future.cancel()` precedent works only because that probe is an async coroutine on the loop (research-leak-sites.md:120).
10. **Schema migration for F6** — none needed: the partial unique index already exists in 3 places (`models.py:307-313`, `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`, `daemon/manager.py:8549`), and no new table/column/index is introduced — so the house rule ("every new SQLModel table needs its own `.sql` file") is **not triggered**. **Expected: ZERO new `.sql` files in this lane** (assert at the gate).

---

## 11. wi-78967dba verdict section

The verdict write-up lives in **`wi-78967dba-verdict.md`** (sibling artifact, cited by filename). This section states what this lane contributes to it and — equally important — what it does **not**.

**Verdict inputs (from `research-audit-census.md` §2d, cited here so the plan stands alone):**

- **(a) Were the finalize-write failures pool-caused?** — **PROVEN** that aborted-transaction errors occurred in the incident window and invalidated DB reads/writes; **PROVEN** that the finalize cascade is all-or-nothing (one `WriteGuardSession` transaction rolls back the JobItem transition + instance terminal write + lock release together — `job_feedback_observer.py:3675-3702`), leaving `admission_state='active'`; **PROVEN** that the leak class persists at HEAD via R1 (BEGIN-outside-try) and R2 (abandoned-thread retention). **INFERRED** — that the *specific* Oct-27 5h15m explorer-turn wedge was triggered by a pool-caused finalize-write failure: no on-disk artifact records the failing write, its SQLSTATE, or the recovery path for that turn, and per the wedge-capture ruling, absence of captured evidence in that window is **inconclusive, not exculpatory**.
- **(b) With a healthy pool, would the sweeps have caught/recovered the wedge?** — **PROVEN: NO, for the RUNNING-stalled shape.** No periodic sweeper finalizes a JobItem whose backing Task is non-terminal-but-stalled (f1 needs no Task, f2 needs a terminal Task, P1 counts only PENDING watchers, and both watcher sweeps are scoped to watcher/bus rows, never `job_queue_items.admission_state`).
- **(c) What remains with a healthy pool?** — five modes: the RUNNING-stalled shape (no finalize leg at all), the E1 mission-live hold, daemon restart/kill mid-turn, process kill between logical steps (structurally mitigated by the single-transaction cascade), and logic gaps (the mission-mint `UniqueViolation` and the wedge-capture table-name bug).

**This lane's contribution:** F3 and F4 (+F2's engine-level backstop) remove the **pool-side trigger** of the aborted-transaction subclass — the mechanism that converts a fired finalize write into a rolled-back one. F5 and F6 remove two of the logic gaps that blinded diagnostics and produced the boot-time noise.

**This lane's explicit NON-contribution:** the lane does **not** close missed-finalize coverage for the RUNNING-stalled shape (out of scope #6). With a healthy pool, a finalize that *fires* commits instead of rolling back — but a finalize that *never fires* is untouched by this lane. `wi-78967dba-verdict.md` must state this without ambiguity, so the verdict is not read as "the pool fix closes the wedge class".

---

## 12. Assumptions & Open Questions

### Assumptions (stated, not buried)

- **A1** — The base for the whole plan is `19fc11c8b`; the implementation phase re-verifies its own base and treats drift as a plan-amendment trigger (C4).
- **A2** — The manager engine's `connect_args` (`factory.py:337`) may safely carry PG session options: the repair engine already does exactly this on the same URL and driver (`ens_db_tools.py:619-627`).
- **A3** — The repository layer's `with Session(...)` / `with SQLModelSession(...)` discipline is **uniform** (supported by the census: ~689 audited sites, 0 unclosed). If the implementation phase finds a counter-example, F1's census is re-run before anything else.
- **A4** — The house rule "every new SQLModel table needs its own `.sql` migration" is **not** triggered by this lane (no new table/column/index). If the implementation phase cannot express F6's upsert without a schema change, that is an **amendment trigger**, not a licence to add a migration silently.
- **A5** — `test-strategy.md`, `wi-78967dba-verdict.md` and `audit-method.md` are produced by sibling workers in parallel; this plan references them **by filename only** and does not depend on their contents beyond the stated scope.

### Open questions (for the architect)

- **OQ-1 — Why is the boot-mint `UniqueViolation` deterministic (4×/boot, once per resumed actor)?** The rung-1 probe (`find_open_epoch_by_anchor`, repository :69-93) filters on the *same* predicate as the partial unique index, so the INSERT can only collide if an OPEN epoch exists that the probe did not see. Probe and INSERT run in **separate transactions** (session=None ⇒ two sessions), so a race window exists — but a deterministic 4× is not explained by a race alone. **Candidate hypothesis (named, NOT asserted):** the boot pass has **two** mint call sites for the same instance in the same boot (`auto_continue_boot_pass.py:721` and `:1156`) — 4 instances × 1 duplicate = 4 collisions, which matches the observed count. *Needed:* the actual boot-log lines + a `mission_records` census. **Does the answer change the fix?** No — the upsert is idempotent under every trigger. It changes the **disclosure**: if the trigger is a data condition (pre-index duplicate OPEN rows), a data remediation is additionally needed (out of scope #7). **Decision needed:** accept the upsert as the whole fix with the trigger routed to a follow-up, or commission the trigger investigation in parallel.
- **OQ-2 — Which pool did the incident's 5 backends belong to?** `application_name` is empty for daemon connections, so `pg_stat_activity` cannot distinguish manager-engine from repair-engine backends post-hoc. The repair engine carries `idle_in_transaction_session_timeout=60s` and should have reaped a leaked BEGIN backend within a minute — either the live v0.27.8 binary predates that `connect_args` option (**dating not verified**) or the backends were on another engine. **Needed:** a fresh census with `application_name`/`backend_start`/`state_change` during a repro. **Does the answer change the fix?** No — F3 removes the BEGIN entirely and F2/F4 bound both engines' retention. It changes the **narrative** in the RCA follow-up and the "which engine to instrument first" priority.
- **OQ-3 — Engine-wide `statement_timeout` on the manager engine: include or narrow?** The per-probe `SET LOCAL` bound (F4 primary) is sufficient for F4 and is the house idiom. The engine-wide cap is a **defense-in-depth** layer with the largest blast radius of anything in this lane. **Recommendation: include it at 30s** (matching `SELECT_TX_STATEMENT_TIMEOUT_MS`, `ens_db_tools.py:191`) — but the architect may drop it at zero cost to F4 (safety-precedence narrowing, D-14, D-16, D-17, D-18c). **Decision needed:** include / drop.
- **OQ-4 — Is the `pool_timeout` review's "keep 30s" acceptable given the probe budget is 0.5s?** The retained *worker thread* can wait up to 30s for a checkout (it holds no pool slot while waiting). The alternative (10s, matching `REPAIR_POOL_TIMEOUT_S`) converts load into 500s. **Recommendation: keep 30s explicitly + disclose the residual** (D-14, D-16, D-17, D-18b).
- **OQ-5 — Does the incident's `SELECT instances...` ×2 fingerprint warrant moving `_enrich_instances` outside the session scope now?** It is the one known hot spot that holds a transaction open across in-Python work, and it interacts with R2 (the new idle-in-txn timeout). **Recommendation: out of scope (§10 #5), disclosed under R2.** The architect may fold it in.

---

## 13. Safety-precedence note

**Rule applied throughout this plan:** *if any requirement conflicts with a safety property, preserve the safety property, narrow the feature, and say so.*

Three places where this rule actually bound a decision:

1. **C1 over convenience (D-04).** A dedicated readiness-probe pool would have been the tidiest fix for probe starvation, but it would have decoupled the probe from the condition it observes — i.e. it would have bought a green readiness board with a poisoned manager pool. The safety property (the detector observes the real pool) wins; the feature is narrowed to a server-side bound on the **existing** engine.
2. **Fail-loud over fail-quiet (R2).** The engine-level `idle_in_transaction_session_timeout` can kill a legitimate long transaction. We accept the failure (the request errors, loudly) rather than the alternative (a backend held open indefinitely, silently) — and we accept it at 60s so the margin over the observed 8s worst case is ~7×.
3. **The global statement cap is optional, the probe-local bound is not (OQ-3).** F4's guarantee is carried entirely by the probe-scoped `SET LOCAL` bound. The engine-wide cap is a second layer that the architect may drop **without** reopening F4 — that is the narrowing the rule produces.

No requirement in the dispatch was dropped to satisfy a safety property; the three RCA attributions that did not survive verification were dropped because the **evidence** dropped them, and each re-targeting is stated with its citation (F1's audit record; the F4 premise correction; the F6 rollback-guarantee collapse).
