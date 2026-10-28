# db-pool-idle-txn — Plan Overview

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`) — "DB pool poisoned by leaked idle-in-transaction connections".
- **Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn` — branch `fix/db-pool-idle-txn`.
- **Base commit (all `file:line` citations in this plan set):** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (= `origin/latest` = the staged **v0.27.11** tree).
- **HEAD verified** at plan time: `git rev-parse HEAD` → `19fc11c8b52239813fd91327ee0c1961c5b6fc87`. ✔
- **Status:** Phase 0 COMPLETE — BASELINE.md + canonical RESULTS file + harness + raw/ on branch; **no implementation change has landed before Phase 0** (git log 19fc11c8b..HEAD is docs/test only: `23c232b8d … 17dbfe33d`, verified). This plan set is the deliverable.
- **Sibling artifacts referenced by filename only** (authored in parallel, not read at fold time): `test-strategy.md` (F7 guard design/repro/mutation pins), `wi-78967dba-verdict.md` (verdict write-up), `audit-method.md` (borrow-site audit method + counts), `final-implementation-spec.md` (binding contract), `BASELINE.md` (Phase 0 evidence, produced by the lane).

---

## 1. Objective

Repair the two DB-transaction-hygiene mechanisms that are **still live at HEAD** and that together explain the incident's `idle in transaction` backends: (A) a transaction-abort gap in the agent-facing DB repair tool's dry-run path, and (D) unbounded server-side retention of a connection held by an abandoned readiness-probe worker. Alongside them, close the pool-hardening gap on the shared manager engine (F2), two correctness defects the incident window exposed (F5 wrong table name, F6 non-idempotent mission mint), and — per the council fold — the dominant multi-second producer M-R3 (F8) that the original plan omitted. The lane must leave the soak-gate lane's fail-closed readiness/leak machinery **untouched in behavior** — this lane removes the condition that makes the leak class fire, it does not teach the detector to be lenient.

**This lane does NOT close the incident's symptom class.** The recurrence precondition — **sync DB work on the event loop** (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) — is a **dwell multiplier** that this lane does not remove. The BFS depth-loop transaction span (`daemon/repositories/instance/repository.py:1434-1645`) is also untouched. After this lane the daemon can still hold 15/15 manager slots; the guard's occupancy invariant (F7-e) is the thing that will see it, and the follow-up commission is the owner of the residual. See §10 (Out of scope) and §12 (Routed items).

QUALIFICATION (2026-10-28, plan-owner-ratified): this attribution is PARTIAL — the `tasks`→`task` relation error was one of TWO roots. `_read_store_census`'s `n_live_tup`-from-`pg_class` defect is a second, pre-existing root (R-10; owner = the wedge-capture lane). F5 does NOT close the aborted-cascade.

## 2. Scope

**In scope**

| Fix | Subject | Landing file(s) |
|-----|---------|-----------------|
| F0 (Phase 0) | Empirical baseline — scratch-DB measurements that convert documentation-only PG-semantics claims into evidence | new `BASELINE.md` in this planning dir (no code) |
| F1 | Transaction-hygiene audit — documentation of a clean census + audited-and-clean records for RCA sites B and C | new doc in this planning dir (§7 F1) |
| F3 | `ens_db_repair_execute` dry-run: explicit `BEGIN` outside the rollback-owning `try` | `daemon/tools/ens_db_tools.py` |
| F4 | Readiness-probe timeout-path connection retention | `daemon/services/readiness.py` (+ `daemon/repositories/factory.py` for the engine-level half — see D-04 (probe-scoped) and D-16 (engine-level)) |
| F6 | `mission_records` boot mint: deterministic `UniqueViolation` → idempotent mint | `daemon/services/mission_record_service.py`, `daemon/repositories/mission_record/repository.py` |
| F2 | Manager-engine pool hardening (knob-by-knob) | `daemon/repositories/factory.py`, `daemon/constants.py` |
| F5 | wedge-capture queries `tasks` → `task` | `daemon/services/wedge_capture/db_capture.py` |
| F7 | Regression guard: "no idle-in-transaction backend survives a read cycle" | new PG test pack(s) (Phase 3) |
| **F8 (NEW — council fold)** | Remove the borrow-then-await from the deferred-report reconcile (M-R3, the dominant multi-second producer the original plan omitted) | `daemon/manager.py`, `daemon/tools/instance.py` |

**Out of scope** (each with a reason — see §10)

- The readiness **detector** (leak predicate, grace refusal path, never-green arming, detail schema) — coupling C1/C2, forbidden.
- The **P2 psycopg checkpoint-saver pool** knobs (`daemon/persistence.py:316-331`) — different pool, different driver, `autocommit=True`; it is the leak-signal ORACLE and must stay intact (research-pool-couplings.md:132).
- The **event-loop** blocking of `manager.list_instances` (research-leak-sites.md:65) — a latency defect AND a dwell multiplier, but a dwell defect, not a transaction defect; **routed** (see §12).
- The **RUNNING-stalled missed-finalize coverage gap** — no periodic sweeper finalizes a JobItem whose backing Task is non-terminal-but-stalled (research-audit-census.md:114-119). Owned by the strand-healer surface, not this lane; **routed**.
- `mission_records` production-data remediation / index dedup — a data operation, not a code fix; **routed**.
- The **proxy-side** `coding-free` deprecated-row drop and every other morning-item in the project KV — unrelated lanes.
- The **BFS depth-loop** transaction span (`daemon/repositories/instance/repository.py:1434-1645`) — **routed**.
- The **default-executor ceiling** (12 workers on an 8-core host, 1351 `to_thread` call sites) — assessed + disclosed, not fixed (A7).

---

## 3. Root-cause summary — what the evidence actually supports

The RCA (`/home/nea/ensemble-src/.agents/shared/incident-20261026-db-pool-idle-in-transaction.md`, read-only) was **fingerprint-driven**: it attributed five `idle in transaction` backends to four candidate code sites by matching `pg_stat_activity.last_query`. The research pass re-verified every candidate at HEAD and **three of the four attributions do not survive**. This plan states the corrected picture honestly, because the fix set is only defensible if the premise corrections are part of the record.

### 3.1 RCA premise ledger (verified at `19fc11c8b`) — PRESERVED VERBATIM

| # | RCA premise | Verdict | Evidence (file:line@`19fc11c8b`) |
|---|-------------|---------|----------------------------------|
| 1 | `ens_db_tools.py:1479/:1491` explicit `BEGIN` outside the `try` ⇒ a failure there skips `session.rollback()` | **CONFIRMED** — precision correction: the BEGIN sits *inside* the outer `try:` (:1475) but *outside* the inner `try/except` that owns the rollback; the outer `except:` (:1513) has **no** rollback | `daemon/tools/ens_db_tools.py:1475/:1479/:1491/:1488/:1501/:1503/:1513` |
| 2 | `/api/instances` list handler leaks ("ORM read path, autobegin without commit/rollback") | **CORRECTED — no structural leak.** The session is context-managed (`with SQLModelSession(...)` at :1383 flat / :1434 root+BFS); "no explicit commit/rollback in the code" is literally true but none is structurally needed. The adjacent **real** observation is different: the session is held open *across in-Python enrichment* (`return self._enrich_instances(db_session, instances), ...` at :1429) | `daemon/repositories/instance/repository.py:1259/:1383/:1429/:1434` |
| 3 | The readiness `queue_freshness` probe "leaks a transaction per tick" | **REFUTED.** The probe SQL is `FROM task` (max-age :201-210, inflight count :228-237) and runs inside `with engine.connect()` (:1752) | `daemon/services/readiness.py:201-210/:228-237/:1752` |
| 4 | The `SELECT count(*) FROM job_queue_items` fingerprint belongs to the readiness probe | **CORRECTED.** The only render-match is `defer_block_resolver._DEFER_PENDING_COUNT_SQL` (:185-191), executed at :274 and :321 inside `with ... engine.connect()` — context-managed, no leak. A second, differently-prefixed count over `job_queue_items` lives in `daemon/tools/instance_read.py:1408-1416` (husk evidence) — also context-managed | `daemon/services/defer_block_resolver.py:185-191/:274/:306/:321`, `daemon/tools/instance_read.py:1408-1416` |
| 5 | `mint_boot_epoch_if_none`: "an exception inside a transaction without a rollback leaks that connection" | **REFUTED as a leak.** Boot-lane callers pass **no** session (`auto_continue_boot_pass.py:1154-1159`, and the pass loop at :721); `insert_mission` opens `with Session(self._engine) as own:` (:510) and commits at :512 — an `IntegrityError` exits the block ⇒ `close()` ⇒ pool reset-on-return rollback. The 4×/boot `UniqueViolation` is **real**; its trigger is **unexplained** (open question, §11) | `daemon/services/mission_record_service.py:499-567/:641-692`, `daemon/repositories/mission_record/repository.py:498-514`, `daemon/services/auto_continue_boot_pass.py:721/:1154-1159` |
| 6 | wedge-capture queries use `tasks`; the table is `task` | **CONFIRMED** — 2 SQL sites + 1 docstring | `daemon/services/wedge_capture/db_capture.py:308/:387`, docstring :34; correct table `daemon/repositories/task/models.py:171` |
| 7 | Pool hardening needs `pool_pre_ping=True`, `reset_on_return='rollback'`, `pool_recycle` | **CORRECTED.** `pool_pre_ping=True` is **already set** (:328). `reset_on_return` is **not overridden** — SQLAlchemy 2.0.48's *library default* `'rollback'` applies, but it is not pinned in code and `reset_on_return` is set nowhere in `daemon/` (research-pool-couplings.md:56). `pool_recycle` is **genuinely absent** on the manager engine (the repair engine is the only site that has one, :609). **The real gap is the absence of server-side `statement_timeout` / `idle_in_transaction_session_timeout`** on the manager engine — only the repair engine carries them (:619-627) | `daemon/repositories/factory.py:252/:323-338`, `daemon/tools/ens_db_tools.py:609/:619-627` |
| 8 | (soak-lane premise 10/11) The readiness probe's *per-tick* leak is not supported; the **timeout-path retention** is real | **CONFIRMED and sharpened.** A timed-out `asyncio.to_thread` worker keeps its connection checked out; the module's own docstring calls it "bounded leakage" — but on the manager engine that bound **does not exist** (no server-side timeout, no client-side execute timeout): the retention is unbounded *in theory* | `daemon/services/readiness.py:107-113/:1463-1481/:1746-1789`; soak `decisions.md` §2 premises 10-11 |

**Net:** of the RCA's "4 candidate leak sites", **one is a genuine structural defect** (A), **one is a genuine but differently-shaped defect** (D — retention, not a per-tick leak), **two are not leaks at all** (B, C — audited clean; E — a *logic* defect, not a leak), plus **one non-pool correctness defect** (F).

### 3.2 The borrow-site census — the honest headline (F1) — PRESERVED VERBATIM

A full enumeration of session/connection borrow sites in `daemon/` at HEAD audited **779 sites across 6 families (AST baseline: A=613 / B=175 / C=4 / D=148 / E=14 / F=0) and found ZERO unclosed borrows** — every borrow is `with`-form, `try/finally`-form with rollback+close, or an ownership-transferring proxy (method + counts: `audit-method.md`). The retired aggregate **≈689** is non-canonical (scanner-dependent; `audit-method.md:283-296`); the AST baseline 779 is the cited figure. The one non-`with` session family (4 sites) was verified individually and is clean. **There is no "leaked session" class in this codebase at HEAD.** The hazard set is narrower and different: transaction-state/retention hazards on close-clean borrows (the RISK table in `research-audit-census.md` §1c — R1 = the BEGIN gap, R2 = probe retention, R3 = borrow-then-await amplification).

This is the single most important correction to the RCA's framing, and it is why F1's fix content collapses into F3/F4/F6 plus an audited-and-clean record.

### 3.3 Reconciling the five observed backends — **INFERENCE, explicitly labeled** — PRESERVED VERBATIM

The incident observed **5 `idle in transaction` backends** with these `last_query` fingerprints: `BEGIN` ×2, `SELECT instances.instance_id, instances.project_id, ...` ×2, `SELECT count(*) FROM job_queue_items ...` ×1 (RCA :54-61).

| Fingerprint | Count | Most-consistent origin | Confidence |
|---|---|---|---|
| `BEGIN` | 2 | **Site A** — the only `text("BEGIN")` in `daemon/` is `ens_db_tools.py:1479/:1491` (grep: zero other hits). A BEGIN left open on failure is exactly the "idle in transaction, last query = BEGIN" state. **CONFIRMED attribution, cite-backed (P-11/D-56):** measured-unique to Site A — raw `t2_stat_activity.out:48` (psycopg3 autobegin pipelines, leaving the STATEMENT as `last_query`, so a literal `BEGIN` is a standalone-BEGIN marker) + raw `t4_fingerprints.out:12-18` (BEGIN owner = ens_db_tools.py:1479/:1491; byte-exact repro at BASELINE.md:355-362) | **CONFIRMED attribution** (cite-backed; see P-11/D-56) |
| `SELECT instances.instance_id, ...` | 2 | **Not a leak.** This is the ORM render of `SQLModelInstanceRepository.list`. A request in flight holds one scope-transaction open while the daemon does the BFS + `_enrich_instances` work in Python (repository :1429). Under pool pressure these are ordinary in-flight transactions whose *last* statement was the SELECT — i.e. the fingerprint identifies "a request is mid-flight", not "a connection was abandoned" | **INFERENCE** — consistent with the event-loop-starved in-flight-request shape |
| `SELECT count(*) FROM job_queue_items ...` | 1 | **Site C's real owner** — `defer_block_resolver._DEFER_PENDING_COUNT_SQL` (:185-191), executed context-managed at :274/:321. Like the row above, the fingerprint identifies a query in flight on a connection that is *about to* be returned | **INFERENCE** — the site is confirmed; the "leaked" reading is not |

**The reconciliation, stated as one inference:** the 2 `BEGIN` backends are the *only* fingerprints that evidence a genuine defect (Site A). The other three are consistent with **event-loop-starved in-flight requests** — the D-driven pressure loop (probe timeout → readiness red → retry → more DB demand) starves the event loop, so request threads that have *already* opened their scope-transaction sit idle-in-transaction while waiting for the loop to schedule their next step, and their `last_query` is whatever SELECT they last issued. Under that reading, B and C are **amplifiers/observables**, not leaks, and the causal chain is **D (retention) + A (abort gap) → pool pressure → event-loop starvation → more idle-in-txn backends**. This is consistent with the RCA's own feedback-loop sketch (RCA :94-102) and with the census's R2/R3 entries.

**Two honesty caveats that bound this inference** (both from `research-leak-sites.md` §5):
1. `application_name` is **empty** for daemon connections (research-leak-sites.md:173), so `pg_stat_activity` **cannot** distinguish manager-engine from repair-engine backends post-hoc. The `BEGIN` attribution is unique by *SQL text*, not by pool identity.
2. The repair engine carries `idle_in_transaction_session_timeout=60s` (:619-627) and **should** have reaped a leaked BEGIN backend within a minute. Either the live v0.27.8 binary predates that `connect_args` option (**dating not verified in this pass**) or the backends were on a different engine. **Unresolved** — see §11 OQ-2.

### 3.4 The mechanism that actually persists at HEAD — PRESERVED VERBATIM

Two defects survive the census and the premise corrections:

- **M1 — transaction-abort gap (Site A).** `session.execute(text("BEGIN"))` executes outside the rollback-owning inner `try`. A failure at the BEGIN (or at the DDL-branch `rollback()` itself) reaches the outer `except:` (:1513) with **no rollback**, leaving the repair engine's session in a begun/aborted transaction for the rest of the tool call. The engine's 60s `idle_in_transaction_session_timeout` is the only backstop. Repair: **F3**.
- **M2 — unbounded server-side retention (Site D).** On probe timeout the abandoned `to_thread` worker keeps its connection checked out; the manager engine has **no** server-side `statement_timeout` and **no** `idle_in_transaction_session_timeout`, so the retention is unbounded server-side. Repair: **F4** (probe-scoped server bound) + **F2** (engine-level backstop).

**Amplifier (not a leak):** the manager engine's small pool (`pool_size=5`, `max_overflow=10` — 15 slots) with a 30s default `pool_timeout` and no server-side timeouts turns any retention into pool starvation → probe timeouts → readiness red → retry → more demand (research-audit-census.md:96-97). Repair: **F2**.

QUALIFICATION (2026-10-28, plan-owner-ratified): this attribution is PARTIAL — the `tasks`→`task` relation error was one of TWO roots. `_read_store_census`'s `n_live_tup`-from-`pg_class` defect is a second, pre-existing root (R-10; owner = the wedge-capture lane). F5 does NOT close the aborted-cascade.

---

## 3.5 §PREMISE DELTA (Phase-0 measured) — P-1..P-10

Phase 0 measured evidence (BASELINE.md + canonical RESULTS + harness/raw/) refutes or restates ten plan premises. Every row cites artifact:line. Where a measurement contradicts a plan premise, the plan text changes (visible in the diff), not an annotation.

| ID | Plan said (quote + artifact:line) | Measured (value + cite) | Plan text changed (file + section) |
|----|-----------------------------------|-------------------------|-----------------------------------|
| **P-1** | F4 premise: "the abandoned probe worker holds a connection open indefinitely and a probe-scoped `SET LOCAL statement_timeout` bounds it" (phase1-plan D-04/T1.4; plan-overview §4 F4; decisions D-35) | Under pool starvation the probe holds **NO** connection (b3: holders 15, `checkedout_right_after_timeout` 15 — a pool WAITER, bounded by `pool_timeout`); when it does hold one the backend is `active`, NOT `idle in transaction` (b2); probe-scoped `SET LOCAL` is **IRRELEVANT** to the starvation shape (BASELINE T5b; raw t5 b1/b2/b3: b1=5.8 ms, b2 timeout=0.5017 s, b3 timeout=0.5012 s) | **F4 demoted** from "primary" to bounded defence-in-depth (§4 row D; Phase 5 table); outcome restated as "the probe's statement duration is server-bounded **when the probe holds a connection**" |
| **P-2** | Flip condition as a constant: "if hold < 2 s …" (OQ-6, plan-overview:266; final-implementation-spec §0(a); decisions D-40) | Worst hold 8011.119 ms (sync twin's 8.0 s bridge, n=1); async worst 1059.877 ms but that figure is **MODEL-INJECTED** (t1 py :281-282 injected a 1 s tail), not measured; hold ≈ await at medians (L5 1029.9 vs 1026.5 ms; L2 52.4 vs 47.7 ms; L1 diverges by ~42.8 ms under pressure) ⇒ **F8 STANDS**; condition becomes "hold < 2 s **iff** the awaited content fetch < 2 s" (a property of the FETCH, data-dependent; the real fetch latency is **UNMEASURED** — disclosed) (raw t1) | OQ-6 restated as a precondition (plan-overview:266); decision instrument = fetch-latency distribution |
| **P-3** | Manager idle-in-txn bound = "4 × measured max legitimate hold, floor 30 s" (decisions D-32; final-implementation-spec §2) | The max legit hold is model-injected (1059.877 ms) or 114.5 ms un-injected (n=30); 4× = 4.24 s ⇒ the 30 s floor dominates AND coincides with the legitimate `pool_timeout` wait (~30 s) ⇒ a 30 s server-side reap risks killing legitimate waiters, and the server **TERMINATES** the backend (raw t2:137-140) ⇒ the bound ships **DISABLED** (defined, not applied) with the enablement mechanism (applied iff BASELINE.md records a trustworthy measurement; while disabled the rendered string omits the clause and the daemon logs `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no trustworthy Phase-0 measurement`) (raw verifier derivation (a)) | D-32 amended (decisions); §15.4 stage log records applied/disabled |
| **P-4** | F7 residence budget + metric: "measured P100 legitimate hold + margin (≥2× noise floor, floor 2.0 s)" using `state_change − xact_start` (decisions D-38; final-implementation-spec §4) | The specified metric is **FROZEN** at the last statement's completion ⇒ a leaked backend shows the SAME sub-ms value as a healthy one ⇒ the budget **CANNOT** fire on the leak. Working metric is `now() − xact_start` (`xact_age_s` — already collected, raw common.py:88 + t2). Healthy residence measured: median 0.0005 s / p95 0.0007 s / max 0.0008 s over 40 cycles ⇒ the 2.0 s placeholder is orders of magnitude too loose AND would falsely red the sync twin (≥8 s by construction) and any long-fetch async instance. F7 becomes the three-state occupancy (idle-with-xact_start-NULL / idle-in-txn / active) + xact_age-residence form (raw M-7; DR-35) | D-38 amended (decisions); F7 invariant rewritten |
| **P-5** | Attribution via shared connect-args constant with `application_name` appended last (D-30; phase2) | The conninfo parameter **BEATS** `options=-c` (measured); within one options string last `-c` wins; the shared-constant-last hazard is **MEASURED** (`repair_engine_reports_manager_name: true`, raw t3:117) ⇒ per-pool `application_name` moves to each engine's **OWN** conninfo/URL query dict (override-proof); the shared constant carries none (P-5/DR-31) | D-30 amended (decisions); attribution mechanism specified |
| **P-6** | "Three pools" as the census scope (D-36) | **11 real pool-creation sites** (+1 comment false-positive at factory.py:289) ⇒ the three named engines are a **FLOOR**; the F7 census covers every site (P-6/DR-32; raw T3a) | D-36 boundary statement amended (decisions) |
| **P-7** | OQ-1: "the boot path's re-resolve is keyed on the index's own predicate" (D-09) — the probe is mis-keyed | The boot path's re-resolve probe is **SKIPPED** (`founding_message_id=None`, `mission_record_service.py:561/:567`) and the error re-raises — D-09's mis-keyed-probe claim **RETRACTED**; the trigger is a **concurrent double-mint** (c4: 8-way, no barrier, 7 UniqueViolations — raw; c4b forced overlap: 5) ⇒ F6's idempotency is specified against **CONCURRENT double-mint** (P-7/DR-33; code-verified) | D-09 amended (decisions); OQ-1 → CLOSED-by-measurement (plan-overview R-8) |
| **P-8** | F5 priority: Phase-2 item | The incident's "aborted connection handed back out" premise is **REFUTED** for the pool path (`reset_on_return='rollback'` cleans it); the journal quote is a **SAME-CONNECTION CASCADE** in wedge-capture ⇒ F5's priority is **RAISED** (the `tasks`→`task` rename is the cascade's root) and moves to **Phase 1** (P-8/DR-34; BASELINE row 4 + raw t5 a5) | Phase 5 table updated (F5 in Phase 1); §4 F5 row updated |
| **P-9** | The measured record is authoritative (BASELINE.md ≡ RESULTS) | **8 stale/over-stated rows** in BOTH copies (raw verifier score table): 7 numeric values from a superseded harness run (3.003 vs 3.002; PIDs 2317xxx vs 2325xxx; 5.1 vs 5.8 ms; 0.5011 vs 0.5017; 0.5015 vs 0.5012) and 2 citations over-stated (T4's "byte-exactly" is a prefix check on a 200-char-truncated value; F-A's quoted SQL is the COMPILED SQL, not the observed `last_query`) ⇒ the plan cites **RAW values** and drops the byte-exact claim; BASELINE.md ≡ RESULTS line-for-line today (divergence NONE) but the plan names ONE canonical copy (the RESULTS file) and requires the other to carry a pointer, not a fork (P-9/DR-27) | SC10 + census cites raw values; one-canonical-copy rule recorded |
| **P-10** | Census blindness (BASELINE point 5 + DR-35) | A probe/connection holding work is `active` or idle-with-no-xact — an idle-in-txn-only census is **vacuous against TWO of the three shapes** ⇒ the three-state invariant (folds into P-4's F7 form) (P-10/DR-35) | F7 invariant rewritten (three-state) |

| **P-11** | "Two of the four incident fingerprints are NOT reproducible at BASE" (test-strategy §0.3) + architect A4-i "a literal `BEGIN` `last_query` is a generic marker of 'a transaction opened and then went quiet' ⇒ A cannot be the fingerprint owner" (architecture-recommendation.md:63/:154) | **All four fingerprints REPRODUCE byte-exactly** (BASELINE.md:355-362; raw t4:46-55 comparison matrix: BEGIN=true, SELECT instances...=true, SELECT count(*) FROM job_queue_items...=true, INSERT ... mission_records=true). The `BEGIN` owner is **measured-unique to Site A**: the only `text("BEGIN")` in `daemon/` is `ens_db_tools.py:1479` (DDL) / `:1491` (DML) (BASELINE.md:366-367; raw t4:12-18). **psycopg3 does NOT leave `BEGIN` as `last_query`** — autobegin pipelines, leaving the STATEMENT (raw t2_stat_activity.out:48 — `last_query` = the SELECT after an autobegun statement); a literal `BEGIN` `last_query` is therefore a **standalone-BEGIN marker**, measured-unique to Site A (BASELINE.md:385-398). The architect's A4-i generic-marker claim is **REFUTED**. Plan text changed: the fingerprint table's BEGIN row upgraded from "most-consistent origin" to MEASURED-CONFIRMED with the raw cite; R19 reworded (the attribution is now measured-sound — the correction belongs to A4-i, not to the Site-A attribution) (P-11) | Fingerprint table BEGIN row upgraded; R19 reworded; §3.3 premise-1 row already CONFIRMED |

**Additions:** if BASELINE.md yields a premise change NOT in this list, it is recorded as P-11+ and reported. None identified beyond P-1..P-11 (the raw verifier's 58-row score table is fully covered).

QUALIFICATION (2026-10-28, plan-owner-ratified): this attribution is PARTIAL — the `tasks`→`task` relation error was one of TWO roots. `_read_store_census`'s `n_live_tup`-from-`pg_class` defect is a second, pre-existing root (R-10; owner = the wedge-capture lane). F5 does NOT close the aborted-cascade.

## 4. Per-fix mapping table — PRESERVED (F8 row added)

## 4. Per-fix mapping table — PRESERVED (F8 row added)

| Site | Fix | file:line@`19fc11c8b` | Fix pattern (design-level) | Verification (Phase) |
|---|---|---|---|---|
| **A** — repair-tool dry-run BEGIN | **F3** | `daemon/tools/ens_db_tools.py:1479` (DDL), `:1491` (DML); outer `try:` :1475, outer `except:` :1513 | **Drop the explicit `BEGIN`** (SQLAlchemy autobegins on the next `execute`; the ROLLBACKs carry the "does not commit" property) **and** move the rollback into a `finally` so every exit path rolls back | P1: unit pin (no `text("BEGIN")` remains; rollback on every exit path, incl. SQL-error and rollback-error paths); no test asserted `text("BEGIN")` before this change (grep: zero hits) |
| **D** — readiness-probe retention | **F4 (demoted 2026-10-28 round-2 — P-1/DR-29: bounded defence-in-depth, NOT primary)** | `daemon/services/readiness.py:107-113` (docstring), `:1477` (DB probe connect), `:1752` (queue probe connect), `_guarded` seam :1262-1295 | **Probe-scoped server-side statement bound (defence-in-depth)**: `SET LOCAL statement_timeout` inside each probe's own transaction, sized at 2× the probe's asyncio budget, PG-only, + an explicit end-of-transaction before the connection is returned. **Premise rewritten (P-1):** the incident's starvation shape (b3: probe holds NO connection — a pool WAITER, bounded by `pool_timeout`) is addressed by F8 + F2's pool occupancy/waiter arguments, NOT by a probe-scoped statement bound; F4's outcome is restated as "the probe's statement duration is server-bounded **when the probe holds a connection**" (b2: `active`, not `idle in transaction`). Engine-level backstop lands in **F2** | P1: PG-backed probe-timeout pin (forced probe timeout leaves **no** idle-in-transaction backend behind); PG-only guard pinned |
| **E** — mission boot mint | **F6** | `daemon/services/mission_record_service.py:499-567` (IntegrityError handler), `:641-692` (rung-1 probe + boot entry point), `daemon/repositories/mission_record/repository.py:498-514` (own-session insert), `:69-93` (rung-1 probe) | **Idempotent mint**: replace the own-session ORM insert with a dialect upsert `ON CONFLICT (anchor_instance_id) WHERE <open-predicate> DO NOTHING` (house precedent `daemon/repositories/job_queue/repository.py:485-496`) + re-resolve via the **anchor-scoped** rung-1 probe (which uses the *same* predicate as the index) instead of the `founding_message_id`-keyed probe that can never match a NULL-founding boot epoch. **No schema change** — the partial unique index already exists in 3 places | P1: unit pin (upsert shape, dialect selection, re-resolve key) + PG pin (second mint returns the winning epoch, no `IntegrityError`, **and** the caller's transaction is not poisoned) |
| **F** — wedge-capture table | **F5 (raised to Phase 1 — P-8/DR-34)** | `daemon/services/wedge_capture/db_capture.py:308`, `:387`, docstring :34 | Rename `tasks` → `task` (2 SQL sites + 1 docstring). Correctness fix — this path rides a **one-shot `psycopg.connect`** with no pool, `close()` in `finally` (:289-294) | P1 (raised 2026-10-28 round-2 — P-8/DR-34): PG pin (wedge-capture read returns rows; no `relation "tasks" does not exist`); assert the *second* query on the same connection no longer fails with `current transaction is aborted` |
| **F (NOTE — R-10, plan-owner-ratified 2026-10-28)** — attached qualification to the row above (the original claim is KEPT VISIBLE and is not rewritten) | **F5 (partial — see this row's qualification)** | `daemon/services/wedge_capture/db_capture.py:284` — the store census — executes **BETWEEN** the two F5-owned reads (`:282` executing-turns, `:285` resume-history) | **QUALIFICATION (plan-owner-ratified 2026-10-28): the Verification cell of the row above is met ONLY for the two F5-owned reads IN ISOLATION — which is exactly what the F5-b leg asserts (it exercises those two reads DIRECTLY on one connection, bypassing the census). In the FULL capture path the census at `:284` sits between the two reads and fails on real PG (`n_live_tup` is not a `pg_class` column — it exists only on `pg_stat_user_tables`; R-10) ⇒ it aborts the connection ⇒ the subsequent read inherits `InFailedSqlTransaction`. ⇒ **This lane addresses the aborted-cascade only PARTIALLY; it does NOT close that root.** The original claim stays visible and remains TRUE IN ISOLATION.** | **Owner = the wedge-capture lane** (the census defect is PRE-EXISTING AT BASE — not introduced by this lane) |
| **B** — `/api/instances` list | **F1 doc** | `daemon/repositories/instance/repository.py:1259/:1383/:1429/:1434`; `daemon/routers/instances.py:370-476`; `daemon/services/instance_lifecycle.py:5021` | **No code change.** Audited-and-clean record + the RCA premise-2 correction | P1: doc artifact only (audit-findings.md) |
| **C** — `job_queue_items` count | **F1 doc** | `daemon/services/defer_block_resolver.py:185-191/:274/:306/:321`; `daemon/tools/instance_read.py:1408-1416` | **No code change.** Audited-and-clean record + the RCA premise-4 correction | P1: doc artifact only |
| **P1 pool** — manager engine | **F2** | `daemon/repositories/factory.py:323-338` (+ `:252` connect-args home), `daemon/constants.py` (canonical knob home; `factory.py:14` already imports `BOOT_DB_TIMEOUT_S` from it) | **Knob-by-knob hardening** — exact values + rationale per knob in `phase2-plan.md` §2 and `decisions.md` D-14, D-16, D-17, D-18* | P2: the existing knob-pin file `tests/unit/repositories/test_pg_engine_utc_session.py` (which already captures `create_engine` kwargs at :104-117) is **extended** to assert the complete final knob set |
| **Guard** | **F7** | greenfield — no idle-in-transaction assertion exists anywhere in `tests/` today (grep: only the vacuum-engine timeout *strings* at `tests/unit/test_run_vacuum_full_after_cleanup.py:192/:212`) | PG-backed regression guard; design/repro/mutation pins specified in `test-strategy.md` | P3: gate structure in `phase3-plan.md`; **house runner form + expected-collected-count discipline** (coupling C3) |
| **M-R3** — deferred-report reconcile (NEW, council fold) | **F8** | `daemon/manager.py:11516` (async scope), `:11568-11571` (content fetch await), `:11754-11757` (nested session), `:10860-10861` (sync twin), `:11208/:11216-11224` (sync bridge), `daemon/tools/instance.py:1207/:1360` (third site) | **Three-phase split** (P0 read-decide → P1 content fetch with NO manager-engine txn open → P2 write scope: re-read inj, re-assert terminal + dead-parent guards, then create artifacts in ONE transaction). Hoist the fetch out of the held scope. Do NOT lengthen the session. | P1: F8-a (static AST pin), F8-b (PG pin), F8-c (concurrent-escalation pin), F8-d (write-atomicity pin), F8-e (async-twin bounded hold pin) |
| **R-1 comment (in-lane, Δ7)** | **Comment-only correction** | `daemon/routers/queues.py:652-654` | **Replace the stale minimising comment** ("two fast SELECTs, no event-loop-relevant latency") with an accurate description of the resolve path (2 + N(holder) SELECTs, connection #1 pinned idle-in-txn across the WS2 loop, `defer_block_resolver.py:306-345/:470-472`). **Zero behavior change.** Disclosed in the release composition (honesty block). **Constraint (Δ7):** if the surrounding code differs at implementation time, the developer **reports back rather than improvising**. | P1 (comment-only; no behavior change; the underlying sync-DB defect remains routed to R-1) |

---

## 5. Phases

| # | Name | File | Objective | Depends on |
|---|------|------|-----------|------------|
| **0** | **Empirical baseline** (NEW — leader ruling 5) | `phase0-plan.md` | Convert documentation-only PG-semantics claims into measurements on a scratch DB; produce BASELINE.md | — |
| **1** | Transaction hygiene + F8 (primary) + F5 | `phase1-plan.md` | F3 (BEGIN gap) + F4 (probe retention, defence-in-depth) + F6 (idempotent mint) + F1 (audit documentation for B/C) + **F8 (borrow-then-await removal — primary, council fold)** + **F5 (wedge-capture table, raised to Phase 1 — P-8/DR-34)** | Phase 0 |
| **2** | Pool hardening | `phase2-plan.md` | F2 (manager-engine knobs, exact values) | Phase 1 |
| **3** | Regression guard | `phase3-plan.md` | F7 (idle-in-transaction guard) + the pin packs that freeze F3/F4/F6/F2/F5/F8 | Phases 1-2 |

### 5.1 Phase gates (summary; per-phase detail in the phase files)

| Phase | Gate | Non-vacuity requirement |
|---|---|---|
| 0 | Scratch DB created/dropped; BASELINE.md written with 4 sections; flip condition settled by measurement; F7 invariant derived from measurement; zero live contact | Every gate asserts its collected count > 0; the scratch DB teardown is verified |
| 1 | Transaction-hygiene pins green; F8 pins green; **no** change to the readiness detail schema, leak predicate, grace refusal path, or never-green semantics | Default `pytest` run must **assert the expected collected count** of the new packs (> 0) before "0 failed" is read as a pass (coupling C3) |
| 2 | Pool-knob matrix pinned (exact values per knob); wedge-capture read proven against a real PG | The knob pin is a **default-collected** unit pack (no marker) so a bare `pytest` run collects it; the PG pin runs in the house fenced form with an asserted collected count |
| 3 | The guard pack runs in a form the runner **executes**, with an asserted expected collected count; net-new test reds = 0 | `--override-ini="addopts=" -m postgres` (or `-m integration`) **plus** `--collect-only` count assertion before interpreting "0 failed" |

---

## 6. Coupling Map — binding constraints (violating any = plan rejection)

### C1 — The leak detector and its refusal path are fail-closed BY DESIGN — PRESERVED

`pool_leak` is **never** grace-eligible, and the refusal is unconditional (soak `requirements.md` §4.4 P4; `risk-safety-register.md` R-MASK-4). This lane's purpose is to make the signal **stop firing** because the condition is gone — not to make it fire less often.

**Therefore this plan proposes NO change to:** the leak predicate (`daemon/services/readiness.py:784-941`), the exhaustion/trend arms, the conjunction at :739-743, the streak arm, the grace arming window, the detail schema (:408-446), the P1/P2 sampling surfaces (:525-582), the `daemon/api.py:3939-3987` wiring, or the `daemon/constants.py` grace/leak constants.

**What WOULD break C1 and is therefore REJECTED** (see `decisions.md` D-04):
- A **dedicated readiness-probe engine/pool**. Moving the DB/queue probes off `manager.engine` would let the readiness `database` component report green while the manager pool was poisoned — the probe would stop observing the condition it exists to observe. That is detector leniency by relocation. **Rejected on C1 grounds**, not merely as "more moving parts".
- Any threshold change in the leak predicate or grace horizon.

### C2 — No conditional weakening near rollback; never-green stays UNCONDITIONAL at cap-reach — PRESERVED

The promote-soak semantics (3 consecutive greens, never-green **unconditional** at cap-reach) live in `scripts/upgrade/lib.sh` and are pinned by the soak lane's own mutation legs + `tests/unit/tools/test_readiness_failure_class.py`. This lane touches **none** of that surface. The coupling that matters here is the *direction*: this lane's changes make the P2 oracle's `requests_waiting`/`pool_available` signals healthier, which lets a healthy release reach 3 greens and survive. That is the intended outcome — the leak class stops arming because the pool stops being poisoned, and the never-green rule remains the unconditional backstop for every other cause.

### C3 — FALSE-GREEN DISCIPLINE (every gate must run in a form the runner EXECUTES) — PRESERVED + EXTENDED

`pyproject.toml:95` sets `addopts = "-m 'not integration and not postgres and not slow and not conformance'"` — **default `pytest` collects ZERO PG tests** and ZERO integration-marked files. A bare `pytest tests/integration/…` reports "0 failed" with **nothing executed** (soak `plan-overview.md:362-366`; the house rule is restated in the soak lane's `requirements.md:1133/:1169-1172`).

Every gate in this plan set therefore:
1. runs in the **house form** — `/home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m postgres <paths> -q` (or `-m integration` for scratch-DB packs);
2. **asserts the EXPECTED COLLECTED COUNT** — `pytest <paths> --collect-only -q` must print the expected N (> 0) — **before** "0 failed" is read as a pass;
3. names the interpreter explicitly (`python` does not exist on this host; `uv` is at `/home/nea/.local/bin/uv`);
4. scrubs ambient `POSTGRES_*` **per variable** (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT`) — **`env -u POSTGRES_*` is a silent NO-OP** (bash does not glob env names) and is forbidden.

**Lane-wide false-green invariant (NEW — council fold):** every gate in this lane runs in a form the runner EXECUTES and asserts its EXPECTED COLLECTED COUNT. A zero-collected gate is **FATAL**, never green. This applies to **all** gates — unit, PG, integration, and whole-tree. The whole-tree legs (G1.5/G3.5 equivalent) must assert `collected > 0` on **both** sides and reconcile base-vs-head counts; a mismatch is FATAL. R14 (G1.5/G3.5 whole-tree gates have NO collected-count fence ⇒ false green by construction) is closed by this invariant.

### C3b — Legacy → canonical gate-ID mapping table (NEW — reconciliation)

The plan set previously used legacy gate IDs (G0–G6) from the phase-3 ladder. The binding contract declares **ONE canonical ladder** using **the 17-entry canonical ladder** (contract §6, W-6; enumerated in C3c below). The static consistency pin (W-11) asserts every gate ID referenced in decisions.md / architecture-recommendation.md exists in the canonical ladder — the legacy IDs would RED that pin. This mapping table preserves auditability of the old IDs.

<!-- EXEMPT:BEGIN legacy-gate-map -->
| Legacy ID | Canonical ID(s) | Notes |
|-----------|-----------------|-------|
| G0 (guard's own reachability, unit) | G3.4 | guard reachability is an F7-guard unit pin (G-A) |
| G1 (Phase-1 unit packs) | G1.1–G1.4 | F3, F5, F6 unit packs (F3 dry-run, F6 idempotency unit, F5 wedge-capture static) |
| G2 (Phase-2 unit packs) | G2.1–G2.3 | F2 knob matrix, repair-engine pin, P2-immutability pin, F4 probe-bound unit |
| G3 (Phase-1 PG legs) | G2.1–G2.3 (F4 PG) | Phase-1 PG legs are F4 retention (Phase-2 content) |
| G4 (Phase-2 PG legs) | G2.1–G2.3 (PG subset) | F5 wedge-capture correctness (PG, integration-marked) |
| G5 (F7 guard) | G3.1–G3.4 | F7 guard legs G-A through G-F |
| G6 (whole-tree sweep) | G1.5 / G3.5 | Whole-tree base vs HEAD (G1.5 for Phase 1, G3.5 for Phase 3) |
<!-- EXEMPT:END -->

**Lane-wide rule (binding):** the canonical ladder (the 17 entries enumerated in C3c) is the **only** gate vocabulary. A gate must (i) exist in the canonical ladder, (ii) run in a form the runner EXECUTES (certified interpreter `/home/nea/ensemble-src/.venv/bin/python`), and (iii) assert its EXPECTED COLLECTED COUNT. Legacy IDs (G0–G6) are retained in this table for auditability only; new references must use canonical IDs.

**Static consistency pin (NEW — W-11, phase-3 pin inventory):** a default-collected static pin asserts that every gate ID referenced across the **8-artifact set** — plan-overview.md, phase1-plan.md, phase2-plan.md, phase3-plan.md, test-strategy.md, decisions.md, architecture-recommendation.md, final-implementation-spec.md — exists in the 17-entry canonical ladder (enumerated in C3c), with the **4 checks**: gate IDs, fix IDs, AC IDs, and the pack inventory. The legacy→canonical mapping table above is delimited by the machine-detectable sentinel pair (the legacy-gate-map BEGIN and END markers, exactly one pair); the scanner skips **only** the region between those two markers, and a sub-assertion requires the pair to be present exactly once. Plantable mutation: a **NON-EXEMPT** reference to a non-canonical gate ID ⇒ RED. The pin is added to the phase-3 pin-pack inventory as **G-consistency** (default-collected, static).

### C3c — Canonical ladder (binding enumeration) — NEW (round-2)

The canonical ladder is **enumerated** (not ranges) in `final-implementation-spec.md` §6.1 and is BINDING on all artifacts. This is the authoritative list; the legacy→canonical mapping above (C3b) re-keys to it.

Canonical ladder entries (binding):

- **G0** — Phase-0 precondition
- **G1.1** — F3 mechanism unit
- **G1.2** — F6 mint unit
- **G1.3** — F5 wedge-capture static
- **G1.4** — F8 static + F4 unit/static pins
- **G1.5** — whole-tree
- **G2.1** — F2 knob-matrix unit
- **G2.2** — repair-engine isolation unit
- **G2.3** — P2-oracle immutability unit
- **G2.4** — the amended utc-session pin
- **G2.5** — Phase-1 PG legs (F4 retention + F4 starvation + F6-b + F8-b/c/d/e)
- **G2.6** — Phase-2 PG legs (F5-b)
- **G3.1** — F7 occupancy+census
- **G3.2** — F7 residence+self-pin
- **G3.3** — F7 static backstop pins
- **G3.4** — F7 reachability (G-A)
- **G3.5** — whole-tree

Legacy IDs (G0–G6) are retained in the C3b table for auditability only; new references must use canonical IDs from this enumeration. If the sibling contract worker's final §6 enumeration differs from this ruling, the difference is reported — not silently adopted.

### C4 — Line numbers from the incident snapshot are STALE — PRESERVED

Every `file:line` in the RCA was captured against the Oct-26 live binary and **drifted** since. All citations in this plan set are re-verified at `19fc11c8b` (I re-read each load-bearing site during planning). The implementation phase must re-verify at its own base and treat drift as a plan-amendment trigger, not as an implementation detail.

### C5 — `daemon/manager.py` coupling row (NEW — W-8)

`daemon/manager.py` is a touched file under F8 (a write path under the write-guard that creates MessageQueue/Task/ReportInjection rows). The coupling matrix must include a row for it.

| Shared surface | P0 | P1 (F3/F4/F6/F1/F8) | P2 (F2/F5) | P3 (F7) | Coupling note |
|---|---|---|---|---|---|
| `daemon/services/readiness.py` | — | **F4 only** — the two probe bodies' timeout wiring | — | reads (no write) | C1: nothing else in this file may change |
| `daemon/repositories/factory.py` | — | context only (F4 sizes the engine-level half) | **F2 — the knob set** | reads | P2 owns the final knob values; P1 must not land a competing knob |
| `daemon/constants.py` | — | — | **F2 — new knob constants + honesty-block refresh** | reads | Canonical knob home (`factory.py:14` precedent) |
| `daemon/tools/ens_db_tools.py` | — | **F3** | — | reads | Repair engine knobs are **out of scope** (already hardened) |
| `daemon/services/mission_record_service.py` + `daemon/repositories/mission_record/repository.py` | — | **F6** | — | reads | — |
| `daemon/services/wedge_capture/db_capture.py` | — | — | **F5** | reads | — |
| `daemon/persistence.py` (P2 saver pool) | — | **DO NOT TOUCH** | **DO NOT TOUCH** | reads | C2 + research-pool-couplings.md:132 — the oracle must stay intact |
| `scripts/upgrade/lib.sh` (soak semantics) | — | **DO NOT TOUCH** | **DO NOT TOUCH** | — | C2 — never-green stays unconditional |
| `daemon/services/readiness.py` leak machinery (:312-983), `daemon/api.py:3939-3987` | — | **DO NOT TOUCH** | **DO NOT TOUCH** | reads | C1 |
| **`daemon/manager.py` (NEW — W-8)** | — | **F8 — the reconcile split** (allowed region: `_reconcile_deferred_report` :10809, `_reconcile_deferred_report_async` :11466, `_fetch_subshape_a_content_sync` :11172, `_fetch_subshape_a_content_async` :11234, nested session :10773, carrier check :11110/:11754) | — | reads | F8 changes a write path under the write-guard; add a diff-review leg restricted to those functions |
| `daemon/tools/instance.py` (NEW — C-6) | — | **F8 third site** (:1207/:1360) — include ONLY if identical borrow-then-await shape; else scope + disclose | — | reads | Write path under `WriteGuardSession`; residual disclosed if excluded |

---

## 7. Risks — PRESERVED + NEW (R13-R19)

| ID | Risk | Impact | Mitigation | Disposition |
|----|------|--------|------------|-------------|
| **R1** | **Global `statement_timeout` on the manager engine** could kill a legitimately long repository query (> the proposed bound). The bound must be generous enough for the slowest known manager-engine statement | medium | The per-probe `SET LOCAL` bound (F4 primary) is **independent** of the engine-wide cap: if the architect judges the global cap too broad, **drop it without reopening F4** (safety-precedence narrowing, D-14, D-16, D-17, D-18c). Bind the value to the in-repo precedent `SELECT_TX_STATEMENT_TIMEOUT_MS = 30_000` (`ens_db_tools.py:191`) and disclose | **R1 REVERSED by C-3** — the engine-wide `statement_timeout` is DROPPED (leader ruling 2). The future global cap must `SET LOCAL statement_timeout = 0` inside `manager.py:8760` (recorded as a follow-up). F4's probe-scoped bound is unaffected. |
| **R2** | **`idle_in_transaction_session_timeout` on the manager engine** could kill a request that legitimately holds a transaction across > the bound of in-Python work. Known hot spot: `SQLModelInstanceRepository.list` holds the session open across `_enrich_instances` (repository :1429); the incident's endpoint already took 8s under load | medium | Choose 60s (the repair-engine precedent `REPAIR_IDLE_IN_TXN_TIMEOUT_MS = 60_000`, `ens_db_tools.py:188`) — ~7× the observed worst case. The kill is **fail-loud** (the statement errors, the request fails) and `pool_pre_ping=True` + `reset_on_return='rollback'` retire the dead backend. Disclose the hot spot; a follow-up may move the enrichment outside the session | **R2 AMENDED** — the hot spot is the BFS depth loop + M-R3, not `_enrich_instances` (which is a no-op hook). The 7× margin holds only **after F8** (A6). |
| **R3** | **`pool_recycle`** forces periodic reconnects; too short ⇒ churn under load, too long ⇒ stale exposure | low | Value chosen to match the single in-repo recycle precedent (`REPAIR_POOL_RECYCLE_S = 3600`, `ens_db_tools.py:182`). At 15 slots, one recycle per hour is ~15 reconnects/hour — negligible | unchanged |
| **R4** | **F3 changes the observable preview text** ("EXECUTED INSIDE BEGIN/ROLLBACK") and the `pg_stat_activity.last_query` fingerprint | low | The wording change is cosmetic and must be asserted by the pin; the fingerprint removal is an *intended* consequence — a future occurrence can no longer be mis-attributed to a dry-run preview (D-01) | unchanged |
| **R5** | **F6's upsert changes the mint's error contract** — a caller that today *expects* the `IntegrityError` to propagate may stop seeing it | medium | Scope F6 to the **own-session (boot) path** only (`session is None`); the caller-session path keeps its documented "poison the caller's txn and re-raise" semantics untouched. The boot path's current behavior (raise → fail-open wrapper catches at `mission_record_service.py:1379-1384`) is preserved as "return the winning epoch" | unchanged |
| **R6** | **The unexplained 4×/boot `UniqueViolation` trigger** may be a *data* condition (pre-index duplicate OPEN rows) rather than a code race. An idempotent upsert masks the *symptom*; a data condition would remain | medium | The upsert is robust to **any** trigger (it is idempotent by construction). The trigger investigation is an explicit **open question** (§11 OQ-1) with a named evidence pull (boot-log lines + a `mission_records` census) — routed to the architect, not silently dropped | unchanged |
| **R7** | **The guard (F7) is PG-dependent and therefore invisible to a default `pytest` run** — a green default run proves nothing about it | medium | The guard pack's expected-collected-count assertion is a **first-class gate artifact** (C3): the gate FAILS if the collected count is 0, not if a test fails. A second, default-collected **static** pin asserts the guard file exists, is marked, and is reachable under the house runner form | **R7 EXTENDED** — the non-vacuity rule must cover the **whole-tree** legs (A8-i), not only the PG pack. The whole-tree legs assert `collected > 0` on both sides. |
| **R8** | **`SET LOCAL statement_timeout` is PG-only** — the SQLite install path would raise `OperationalError` | medium | PG-only guard, exactly as `ens_db_tools.py:793` does it; the SQLite branch must be proven unchanged by a pin | unchanged |
| **R9** | **The probe's server-side bound changes the probe's *classification*** (a statement killed server-side may surface as a probe exception rather than a client timeout) | low | The bound is set to **2× the asyncio budget** so the client-side `wait_for` fires **first** and the client-side classification (`probe_timeout`) is unchanged; the server-side kill then releases the connection shortly after. Pinned | unchanged |
| **R10** | **C1 violation by relocation** — a future maintainer may "fix" the probe starvation by moving the probes to their own pool, re-introducing detector leniency | medium | The rejection is written into `decisions.md` (D-04) and mirrored in a code comment at the probe-wiring site, so the reasoning survives the lane | **R10 EXTENDED** — the relocation rejection must also cover a **probe-side executor** change (A7) and a **probe-pool** change (D-04) in the same code comment at the probe-wiring site |
| **R11** | **Scope creep into the readiness detector** — a "helpful" implementer may weaken a threshold to make a gate pass | high | The DO-NOT-TOUCH list (research-pool-couplings.md §Q4) is reproduced verbatim in `phase1-plan.md` §5 and re-checked at the phase gate by a diff review of `daemon/services/readiness.py` (the ONLY permitted change is the two probe bodies' timeout wiring) | unchanged |
| **R12** | **The `constants.py:207-217` honesty block becomes stale** once the leak is fixed — it names this lane as the owner of an unfixed defect | low | Phase 2 updates the block (and `persistence.py:306-315`) to state that the P1 leak is repaired by this lane, with the repair's date/branch. Not optional — a stale honesty block is a documentation defect the house explicitly tracks | **R12 EXTENDED** — the honesty blocks must state that the lane repairs the **retention/transaction-hygiene** class and **does not** repair the **dwell/occupancy** class (A9-v) |
| **R13 (NEW)** | **The plan's fix set does not reduce the dominant producer** (dwell/occupancy) — a v0.27.12 staged on this basis re-opens the incident under the same load | high | F8 (A1) + F7's occupancy/residence invariant (A2) + honest disclosure (A9-iii) that this lane does **not** fully repair the dwell class | **closed-by-F8-and-disclosure** |
| **R14 (NEW)** | **False-green gate class in G1.5/G3.5** — a 0-collected whole-tree run yields "net-new = ∅" | high | Collected-count fence on both whole-tree legs (A8-i); lane-wide false-green invariant (plan-overview §6 C3) | **closed-by-C3-invariant** |
| **R15 (NEW)** | **`idle_in_transaction_session_timeout=60000` kills legitimate M-R3 work** (open-but-busy-in-Python) if it lands before F8 | medium | Sequence F8 first (A6-i); pin the legitimate-hold budget; fail-loud disclosure | **closed-by-sequencing** |
| **R16 (NEW)** | **Default-executor saturation** by abandoned probes (≈6 of 12 workers on an 8-core host) is an unmodelled daemon-wide latency amplifier (1351 `to_thread` call sites) | medium | Disclose + measure in F7's churn leg (A7); do not relocate probes (C1) | **accepted-with-disclosure** |
| **R17 (NEW)** | **`application_name` amendment is half-applied** ⇒ F2-g stays RED from birth (AC14/AC16 vs `test-strategy.md:132-134`) | medium | Fold A5 as one amendment across AC14/AC16/F2-g in a single change; PG "last `-c` wins" ⇒ distinct values per engine + option order pinned | **closed-by-C2-fold** |
| **R18 (NEW)** | **Unfenced Class-4a test command** (no per-var `POSTGRES_*` scrub, no `mktemp -d` HOME, uncertified interpreter) on saver-pool-touching packs | medium | Apply the Class-1 fence to 4a (A8-ii) | **closed-by-C1-fold** |
| **R19 (NEW)** | **The `BEGIN` fingerprint attribution is sound** — Site A is measured-unique (the only `text("BEGIN")` in `daemon/`); the correction belongs to A4-i (the architect's generic-marker claim is refuted), not to the Site-A attribution | The incident follow-up carries the A4-i correction; the census method captures residence time (A3) | **reworded-by-P-11** |

---

## 8. Success Criteria — PRESERVED + NEW

- **SC1 — No BEGIN fingerprint survives.** `grep -n 'text("BEGIN")' daemon/tools/ens_db_tools.py` returns **zero** hits; the dry-run path rolls back on **all three** legs (SQL-success, SQL-error, rollback-error), pinned by a unit pack that is **default-collected** (no marker) and asserts a non-zero collected count.
- **SC2 — The probe's retention is server-bounded.** Under a forced probe timeout, a real PG shows **zero** backends in `idle in transaction` attributable to the probe within the probe's bound (2× budget); the pack asserts the collected count before reading "0 failed".
- **SC3 — The boot mint is idempotent.** Two consecutive mints for the same anchor return the **same** `mission_id`, raise **no** `IntegrityError`, and leave the caller's transaction un-poisoned. Pinned on real PG.
- **SC4 — The knob set is exact and pinned.** The final `create_engine` kwargs for the manager engine are asserted value-by-value in `tests/unit/repositories/test_pg_engine_utc_session.py` (the file that already captures those kwargs), including `pool_size=5`, `max_overflow=10`, `pool_pre_ping=True`, `pool_reset_on_return='rollback'`, `pool_recycle=3600`, `pool_timeout=30`, and the connect-args timeout options.
- **SC5 — Pool sizing is NOT regressed.** The saver (P2) pool's knobs are byte-identical before/after (`daemon/persistence.py:316-331` untouched); the manager engine stays at 5+10; the soak lane's `tests/unit/tools/test_checkpoint_pool_config.py` and the readiness/grace unit packs stay green.
- **SC6 — The detector is untouched.** `daemon/services/readiness.py` changes are limited to the two probe bodies' timeout wiring; the leak predicate, conjunction, streak arm, detail schema, grace constants, and `daemon/api.py` wiring are byte-identical (verified by diff review at the gate).
- **SC7 — wedge-capture reads work.** The capture returns executing-turn rows and resume-history rows against a real PG, and neither read fails with `relation "tasks" does not exist` nor `current transaction is aborted`.
- **SC7 (QUALIFICATION — plan-owner-ratified 2026-10-28): SC7 is PARTIALLY met by this lane.** The `relation "tasks" does not exist` half is **CLOSED** (the `tasks`→`task` rename, F5.1) and the two F5-owned reads no longer abort each other **in isolation**; the **FULL-path abort shape remains live** via R-10's census defect (`_read_store_census` at `daemon/services/wedge_capture/db_capture.py:284` selects `c.n_live_tup` from `pg_class` — a column that exists only on `pg_stat_user_tables` — and it executes between the two reads, so it aborts the connection on real PG; PRE-EXISTING AT BASE, owner = the wedge-capture lane). **This lane does NOT close it.** The SC7 claim above is kept visible and is not rewritten.
- **SC8 — The guard is real.** The F7 pack runs in the house runner form, asserts its expected collected count (> 0), and fails when a deliberately-planted idle-in-transaction backend is present (mutation-proven — the pin is RED with the defect present and GREEN without it, per `test-strategy.md`).
- **SC9 — Zero net-new test reds.** Whole-tree sweep shows no new failures versus the base; any pre-existing red is classified as pre-existing with a citation.
- **SC10 — The audit record is honest.** The delivered audit-findings document states **0 unclosed borrows across 779 audited sites (AST baseline: A=613 / B=175 / C=4 / D=148 / E=14 / F=0)** with the method cited (`audit-method.md`), and records B and C as audited-and-clean with the RCA premise corrections — it does **not** claim a leak was found where the census found none. The retired aggregate **≈689** is non-canonical (scanner-dependent); the AST baseline 779 is the cited figure (DR-6).
- **SC11 (NEW) — F8 removes the borrow-then-await.** The deferred-report reconcile holds no manager-engine transaction across an await; the static AST pin (F8-a) is GREEN; the PG pin (F8-b) shows zero manager-pool idle-in-txn backends attributable to the reconcile; the concurrent-escalation pin (F8-c) shows no double delivery; the write-atomicity pin (F8-d) shows the message/task/injection writes still commit in one transaction.
- **SC12 (NEW) — Phase 0 evidence exists.** BASELINE.md is written with all four sections (a)-(d), each with a named instrument and a measurement command; the flip condition is settled by measurement, not by paper; the F7 invariant is derived from measurement.
- **SC13 (NEW) — The lane-wide false-green invariant holds.** Every gate in the lane runs in a form the runner executes and asserts its expected collected count; a zero-collected gate is FATAL.

---

## 9. Assumptions & Open Questions — PRESERVED

### Assumptions (stated, not buried)

- **A1** — The base for the whole plan is `19fc11c8b`; the implementation phase re-verifies its own base and treats drift as a plan-amendment trigger (C4).
- **A2** — The manager engine's `connect_args` (`factory.py:337`) may safely carry PG session options: the repair engine already does exactly this on the same URL and driver (`ens_db_tools.py:619-627`).
- **A3** — The repository layer's `with Session(...)` / `with SQLModelSession(...)` discipline is **uniform** (supported by the census: 779 audited sites (AST baseline A=613 / B=175 / C=4 / D=148 / E=14 / F=0), 0 unclosed). If the implementation phase finds a counter-example, F1's census is re-run before anything else.
- **A4** — The house rule "every new SQLModel table needs its own `.sql` migration" is **not** triggered by this lane (no new table/column/index). If the implementation phase cannot express F6's upsert without a schema change, that is an **amendment trigger**, not a licence to add a migration silently.
- **A5** — `test-strategy.md`, `wi-78967dba-verdict.md` and `audit-method.md` are produced by sibling workers in parallel; this plan references them **by filename only** and does not depend on their contents beyond the stated scope.
- **A6 (NEW — Δ6)** — The F4-A capacity constant's home is **pinned to `daemon/services/readiness.py` (module-level)** per D-59. **Conditional note:** if a future implementer prefers `constants.py`, plan-overview §6 C5's coupling matrix needs a **one-row Phase-1 amendment for `constants.py` FIRST** — as written the matrix gives `constants.py` to Phase-2 F2 only, and an unamended Phase-1 addition there violates the lane's coupling discipline.

### Open questions (for the architect) — PRESERVED + NEW

- **OQ-1 — Why is the boot-mint `UniqueViolation` deterministic (4×/boot, once per resumed actor)?** The rung-1 probe (`find_open_epoch_by_anchor`, repository :69-93) filters on the *same* predicate as the partial unique index, so the INSERT can only collide if an OPEN epoch exists that the probe did not see. Probe and INSERT run in **separate transactions** (session=None ⇒ two sessions), so a race window exists — but a deterministic 4× is not explained by a race alone. **Candidate hypothesis (named, NOT asserted):** the boot pass has **two** mint call sites for the same instance (`auto_continue_boot_pass.py:721` and `:1156`) — 4 instances × 1 duplicate = 4 collisions, which matches the observed count. *Needed:* the actual boot-log lines + a `mission_records` census. **Does the answer change the fix?** No — the upsert is idempotent under every trigger. It changes the **disclosure**: if the trigger is a data condition (pre-index duplicate OPEN rows), a data remediation is additionally needed (out of scope #7). **Decision needed:** accept the upsert as the whole fix with the trigger routed to a follow-up, or commission the trigger investigation in parallel.
- **OQ-2 — Which pool did the incident's five backends belong to?** `application_name` is empty, so `pg_stat_activity` cannot distinguish manager-engine from repair-engine backends post-hoc; and the repair engine's 60 s `idle_in_transaction_session_timeout` should have reaped a leaked BEGIN backend within a minute — either the live v0.27.8 binary predates that `connect_args` option (**dating not verified**) or the backends were on another engine. **Needed:** a fresh census with `application_name`/`backend_start`/`state_change` during a repro. **Does the answer change the fix?** No — F3 removes the BEGIN entirely and F2/F4 bound both engines' retention. It changes the RCA follow-up narrative and the instrumentation priority.
- **OQ-3 — Engine-wide `statement_timeout` on the manager engine: include or drop?** **REVERSED by C-3 (leader ruling 2).** The engine-wide `statement_timeout` is **DROPPED**. F4's guarantee is carried by the probe-scoped bound (D-04). The future global cap must `SET LOCAL statement_timeout = 0` inside `manager.py:8760`. **Decision settled: DROP.**
- **OQ-4 — Is the `pool_timeout` review's "keep 30s" acceptable given the probe budget is 0.5s?** **Recommendation: yes** — the probe budget is an asyncio budget, not a checkout budget; lowering it converts load into 500s. The residual (a probe worker thread lingering up to 30 s in the checkout queue, holding no pool slot) is disclosed in D-15.
- **OQ-5 — Does the incident's `SELECT instances...` ×2 fingerprint warrant moving `_enrich_instances` outside the session scope now?** **Recommendation: out of scope**, disclosed under R2. The architect may fold it in; it is a repository-semantics change to a shared read path.
- **OQ-6 (NEW, restated 2026-10-28 round-2 as a PRECONDITION, not a constant — P-2/DR-30)** — Does the M-R3 hold duration under real load make F8 load-bearing? **Answer: NOT MET at base ⇒ F8 stands.** The M-R3 hold < 2 s **iff** the awaited content fetch < 2 s (a measurable property of the fetch path; data-dependent — an instance with a long history makes the fetch slower). Decision instrument = the **fetch-latency** distribution (the async twin's content fetch, and the sync twin's 8 s bridge), **not** the hold. Measured base figures: async worst 1059.9 ms (L5, n=15, model-tainted — injected 1.0 s tail); sync twin ceiling 8.0 s (8011.119 ms, n=1). The **real** fetch latency (`_fetch_subshape_a_content_async`) remains **unmeasured** (baseline: "needs a booted daemon + a real checkpoint store") — disclosed, not resolved. Phase 0 (a) settles this. If hold < 2 s, F8 is marginal and C collapses to B on mechanism grounds. If hold ≥ 2 s, F8 is load-bearing and C stands. **Decision: settled by measurement, not by paper.**
- **OQ-7 (NEW) — Which pool did the incident's 18 backends belong to?** Phase 0 (d) settles this. Without `application_name`, attribution is impossible; Phase 2 (F2) adds `application_name` to make future censuses attributable.

---

## 10. Out of scope — with reasons — PRESERVED + ROUTED ITEMS

1. **The readiness leak detector / grace refusal / never-green semantics** — coupling C1/C2. The fix must make the signal stop firing, never make the detector lenient.
2. **The P2 psycopg saver pool knobs** (`daemon/persistence.py:316-331`) — a different pool with `autocommit=True` (so its connections cannot sit idle-in-txn by construction) and a different reset semantic; it is the leak-signal ORACLE. research-pool-couplings.md:132.
3. **The asyncpg checkpoint-adapter pool** (`daemon/persistence.py:355-359`) — max-size-only knob, separate driver/failure domain. **REFUTED by measurement (P-8b-extra / BASELINE.md:306-323): it IS a leak-capable site** — it opens an explicit `async with conn.transaction():` (checkpoint_adapter.py:1229-1239) with three awaits between DELETEs, and the baseline MEASURED it holding `idle in transaction` with **NO server-side reap** (server_side_reap_observed: false; cluster default `idle_in_transaction_session_timeout = 0` = disabled); the shared `conn_string` lands the **same** `application_name` on the P2 psycopg pool and the asyncpg pool (p2_psycopg_pid 2325876 / asyncpg_pid 2325878, both `ensemble-shared-dsn`) ⇒ a **separate DSN** is required. This is the reason it gets its own DSN + attribution per D-36/D-49. Out of scope for knob hardening, but NOT "not a leak site". **ROUTED (see §12 R-9)** — this lane owns attribution (D-36/D-49) only; the hold itself is owned by the follow-up DB-pool commission under the D-47 enablement gate (D-58).
4. **`manager.list_instances` running synchronously on the event loop** (`daemon/routers/instances.py:476`) — a real latency defect AND a dwell multiplier. **ROUTED** (see §12) to the event-loop sync-DB commission.
5. **Moving `_enrich_instances` outside the session scope** (`daemon/repositories/instance/repository.py:1429`) — tempting (it would shrink the idle-in-txn window on the hot path) but it is a repository-semantics change to a shared read path, unrelated to the leak class. Recorded as a **follow-up** under R2.
6. **The RUNNING-stalled missed-finalize gap** — no sweeper finalizes a JobItem whose backing Task is non-terminal-but-stalled (research-audit-census.md:114-119). The `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_*` surface (v0.26.4) is the adjacent owner; this lane does not touch it. **ROUTED**.
7. **`mission_records` production-data remediation** (deduping pre-index OPEN rows) — a data operation on production, requiring its own authorization; the code fix is idempotent either way. **ROUTED**.
8. **A dedicated readiness-probe engine/pool** — rejected on C1 grounds (D-04).
9. **Per-probe asyncio cancellation of a synchronous DBAPI call** — impossible: `asyncio` cannot cancel a running thread's synchronous DBAPI call. The saver probe's `future.cancel()` precedent works only because that probe is an async coroutine on the loop (research-leak-sites.md:120).
10. **Schema migration for F6** — none needed: the partial unique index already exists in 3 places (`models.py:307-313`, `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`, `daemon/manager.py:8549`), and no new table/column/index is introduced — so the house rule ("every new SQLModel table needs its own `.sql` file") is **not triggered**. **Expected: ZERO new `.sql` files in this lane** (assert at the gate).
11. **The BFS depth-loop transaction span** (`daemon/repositories/instance/repository.py:1434-1645`) — **ROUTED**.
12. **The default-executor ceiling** (12 workers on an 8-core host, 1351 `to_thread` call sites) — assessed + disclosed, not fixed (A7). **ROUTED**.

---

## 11. wi-78967dba verdict section — PRESERVED

The verdict write-up lives in **`wi-78967dba-verdict.md`** (sibling artifact, cited by filename). This section states what this lane contributes to it and — equally important — what it does **not**.

**Verdict inputs (from `research-audit-census.md` §2d, cited here so the plan stands alone):**

- **(a) Were the finalize-write failures pool-caused?** — **PROVEN** that aborted-transaction errors occurred in the incident window and invalidated DB reads/writes; **PROVEN** that the finalize cascade is all-or-nothing (one `WriteGuardSession` transaction rolls back the JobItem transition + instance terminal write + lock release together — `job_feedback_observer.py:3675-3702`), leaving `admission_state='active'`; **PROVEN** that the leak class persists at HEAD via R1 (BEGIN-outside-try) and R2 (abandoned-thread retention). **INFERRED** — that the *specific* Oct-27 5h15m explorer-turn wedge was triggered by a pool-caused finalize-write failure: no on-disk artifact records the failing write, its SQLSTATE, or the recovery path for that turn, and per the wedge-capture ruling, absence of captured evidence in that window is **inconclusive, not exculpatory**.
- **(b) With a healthy pool, would the sweeps have caught/recovered the wedge?** — **PROVEN: NO, for the RUNNING-stalled shape.** No periodic sweeper finalizes a JobItem whose backing Task is non-terminal-but-stalled (f1 needs no Task, f2 needs a terminal Task, P1 counts only PENDING watchers, and both watcher sweeps are scoped to watcher/bus rows, never `job_queue_items.admission_state`).
- **(c) What remains with a healthy pool?** — five modes: the RUNNING-stalled shape (no finalize leg at all), the E1 mission-live hold, daemon restart/kill mid-turn, process kill between logical steps (structurally mitigated by the single-transaction cascade), and logic gaps (the mission-mint `UniqueViolation` and the wedge-capture table-name bug).

**This lane's contribution:** F3 and F4 (+F2's engine-level backstop) remove the **pool-side trigger** of the aborted-transaction subclass — the mechanism that converts a fired finalize write into a rolled-back one. F5 and F6 remove two of the logic gaps that blinded diagnostics and produced the boot-time noise. F8 removes the dominant multi-second producer M-R3.

**This lane's explicit NON-contribution:** the lane does **not** close missed-finalize coverage for the RUNNING-stalled shape (out of scope #6). With a healthy pool, a finalize that *fires* commits instead of rolling back — but a finalize that *never fires* is untouched by this lane. `wi-78967dba-verdict.md` must state this without ambiguity, so the verdict is not read as "the pool fix closes the wedge class".

---

## 12. Routed items (NEW — leader ruling 6)

Each routed item is **out of this lane's scope** and is recorded here with its exact evidence and the owning commission. The plan does NOT claim to close the incident's symptom class (the sync-DB event-loop defect is routed).

| # | Item | Exact evidence | Owning commission | Status |
|---|------|----------------|-------------------|--------|
| **R-1** | **Event-loop sync-DB defect** (the dominant dwell multiplier) | `daemon/routers/instances.py:471-483` calls sync `manager.list_instances(...)` with **no `await`**; `daemon/routers/queues.py:653-654` calls sync `resolver.resolve()` on the loop | Event-loop sync-DB removal commission | **ROUTED** — disclosed, not fixed. **(Δ7: the stale minimising comment at `queues.py:652-654` is corrected IN-LANE as a comment-only edit — see §4 fix list; the underlying sync-DB defect remains routed and is NOT closed by that edit.)** |
| **R-2** | **BFS depth-loop transaction span** | `daemon/repositories/instance/repository.py:1434-1645` — one query per level, depth ≤256, ≤1000 descendants/page | BFS depth-loop commission | **ROUTED** — disclosed, not fixed |
| **R-3** | **Default-executor ceiling** | `asyncio.to_thread` ⇒ `min(32, cpu+4)` = 12 workers on an 8-core host; 1351 `to_thread`/`run_in_executor` sites in `daemon/` | Executor-ceiling commission (assess + disclose) | **ROUTED** — disclosed, not fixed (A7) |
| **R-4** | **RUNNING-stalled missed-finalize gap** | No periodic sweeper finalizes a JobItem whose backing Task is non-terminal-but-stalled (research-audit-census.md:114-119) | Strand-healer commission (v0.26.4 surface) | **ROUTED** — not this lane |
| **R-5** | **`mission_records` production-data remediation** | Deduping pre-index OPEN rows on production | Mission-records data owner | **ROUTED** — data operation, not code |
| **R-6** | **Proxy-side `coding-free` deprecated-row drop** | User morning item (D7) — 210 errors/day; killed a coder instance mid-lane | User-owned proxy host surgery | **ROUTED** — not repo work |
| **R-7** | **Missed-finalize / RUNNING-stalled coverage** | No finalize leg exists for the RUNNING-stalled shape (f1 needs no Task, f2 needs a terminal Task) | Strand-healer commission | **ROUTED** — not this lane |
| **R-8** | **OQ-1 trigger investigation** (why the boot-mint UniqueViolation is deterministic) | Candidate: two mint call sites for the same instance (`auto_continue_boot_pass.py:721` and `:1156`) | Architect / data owner | **CLOSED-by-measurement (P-7/DR-33)** — the boot path's re-resolve probe is **skipped** (`founding_message_id=None`, `mission_record_service.py:561/:567`), not mis-keyed; the measured trigger is a **concurrent double-mint** (c4: 8-way, no barrier, 7 UniqueViolations; c4b forced overlap: 5). The upsert (D-08) is idempotent under any trigger; the disclosure is corrected. Originally ROUTED. |
| **R-9** | **asyncpg checkpoint-adapter idle-in-txn hold** | `daemon/checkpoint_adapter.py:1229-1240` opens an explicit `async with conn.transaction():` with three awaits between DELETEs (measured T3d: leak-capable, no server-side reap, cluster default 0); the shared `conn_string` lands the **same** `application_name` on the P2 psycopg pool and the asyncpg pool (p2_psycopg_pid 2325876 / asyncpg_pid 2325878, both `ensemble-shared-dsn`) ⇒ a **separate DSN** is required (D-36/D-49) | Follow-up DB-pool commission (named owner) | **ROUTED** — this lane owns attribution (D-36/D-49) only; the hold itself is owned by the follow-up under the D-47 enablement gate (D-58). The follow-up adds `server_settings={'idle_in_transaction_session_timeout': <measured>}` to the asyncpg pool's **NEW dedicated DSN** (the same DSN D-49 already mandates — a one-parameter addition there), under the **SAME D-47 enablement gate** (enable iff a trustworthy post-F8 measurement of the adapter's legitimate prune-transaction dwell exists). |
| **R-10** | **wedge-capture `_read_store_census` is BORN-BROKEN on real PG** | `daemon/services/wedge_capture/db_capture.py:347` selects `c.n_live_tup` from `pg_class`; that column exists ONLY on `pg_stat_user_tables` ⇒ the store census fails on every real server. PRE-EXISTING AT BASE (not introduced by this lane) and a SECOND ROOT of the aborted-connection class: the census failure aborts the connection, so the subsequent reads inherit `InFailedSqlTransaction`. | The wedge-capture lane owner | **QUALIFICATION (mandatory):** F5 fixed ONLY the `tasks`→`task` relation error — the wedge-capture aborted-cascade root is only PARTIALLY addressed by this lane; this lane does NOT close it. |
| **R-11** | **`instance.py:1346` unreachable defensive check** | `if _bus is None: return None` sits outside the contract's change list and is unreachable (the same condition returns inside the scope at `:1229-1233`). | The follow-up DB-pool commission (instance.py hygiene) | **DISCLOSED**, no patch in this lane. |

---

## 13. Safety-precedence note — PRESERVED

**Rule applied throughout this plan:** *if any requirement conflicts with a safety property, preserve the safety property, narrow the feature, and say so.*

Three places where this rule actually bound a decision:

1. **C1 over convenience (D-04).** A dedicated readiness-probe pool would have been the tidiest fix for probe starvation, but it would have decoupled the probe from the condition it observes — i.e. it would have bought a green readiness board with a poisoned manager pool. The safety property (the detector observes the real pool) wins; the feature is narrowed to a server-side bound on the **existing** engine.
2. **Fail-loud over fail-quiet (R2).** The engine-level `idle_in_transaction_session_timeout` can kill a legitimate long transaction. We accept the failure (the request errors, loudly) rather than the alternative (a backend held open indefinitely, silently) — and we accept it at 60s so the margin over the observed 8s worst case is ~7×.
3. **The global statement cap is optional, the probe-local bound is not (OQ-3).** F4's guarantee is carried entirely by the probe-scoped `SET LOCAL` bound. The engine-wide cap is a second layer that the architect may drop **without** reopening F4 — that is the narrowing the rule produces.

No requirement in the dispatch was dropped to satisfy a safety property; the three RCA attributions that did not survive verification were dropped because the **evidence** dropped them, and each re-targeting is stated with its citation (F1's audit record; the F4 premise correction; the F6 rollback-guarantee collapse).

---

## 14. Council fold section (NEW — the 12 CRITICAL closures)

The plan council REJECTED the plan-of-record (unanimous, 12 CRITICAL). The fold records each closure with its named resolution and where it lands. The research core, premise ledger, census, D-17 isolation, C1 rationale, zero-new-.sql claim and phase sequencing are VERIFIED-CLEAN and are preserved verbatim above.

| # | Critical | Named resolution | Where it lands |
|---|----------|-------------------|----------------|
| **C-1** | C3 gate fence is INERT (`/usr/bin/python3` has no pytest → 0 collected → false green) | Every gate uses the CERTIFIED INTERPRETER (`/home/nea/ensemble-src/.venv/bin/python`, pytest 9.0.2); `uv run` is dropped; every gate asserts its EXPECTED COLLECTED COUNT (a zero-collected gate is FATAL) | plan-overview §6 C3; phase1/2/3 gate sections; decisions D-26/D-27 (extended) |
| **C-2** | A5/AC14/AC15/AC16/T2.6/F2-g mutually unsatisfiable (PG last `-c` wins) | ONE statement of truth in ONE artifact (final-implementation-spec.md): the shared connect-args constant carries NO `application_name`; each pool's attribution is set at that pool's own connect site; D-23(c)/D-24 superseded/reconciled explicitly | decisions (new D-29+); phase2 (AC14/AC15/AC16/T2.2/T2.4/T2.5/T2.6); plan-overview §6 C5 |
| **C-3** | Leader ruling violated: engine-wide `statement_timeout=30000` still live in D-16/T2.2/AC14/Phase-2 objective | DROP is FINAL — scrub EVERY reference; D-16 marked REVERSED; future global cap needs `SET LOCAL statement_timeout = 0` inside `manager.py:8760` (follow-up) | decisions (D-16 REVERSED); phase2 (objective, T2.2, AC14); plan-overview §7 R1 |
| **C-4** | R15 violated: no ordering/validation rule, no named instrument; "8 s" is a configured timeout not an observed hold | Replace the unmeasured bound with a MEASURED rule (Phase 0 produces the measured max legit hold + margin); the plan states the RULE, never an unmeasured number | phase0 (output (a)); phase2 (D-16, the bound); decisions (new); plan-overview §12 |
| **C-5** | F8 as written does NOT remove the hold (scope already autobegun → only a P0/P1/P2 split works; stale injection → double delivery) | Spec the three-phase split (P0 read-decide → P1 content fetch with NO txn open → P2 write scope: re-read inj, re-assert guards, create artifacts); guard re-assertion; freshness handling | phase1 (F8 tasks); decisions (new); final-implementation-spec §3 |
| **C-6** | F8 is 3 SITES, not 1 (manager.py:11568-11571, :11754-11757, instance.py:1207/:1360) | Extend F8 to all three sites; include instance.py ONLY if identical borrow-then-await shape; else scope + disclose | phase1 (F8 tasks); decisions (new); plan-overview §6 C5 |
| **C-7** | F4-b asserts an IMPOSSIBLE PG state (idle-in-txn ⇒ wait_event_type='Client'; lock-blocked ⇒ 'active'+'Lock' ⇒ mutually exclusive) | Re-specify the assertion to the TRUE OBSERVABLE (statement cancelled → to_thread returns → connection returned → checkedout() back to baseline); mutation = remove `SET LOCAL statement_timeout` from the probe | phase1 (T1.7, AC7); decisions (new); final-implementation-spec F4 |
| **C-8** | A5 misses a THIRD pool (asyncpg checkpoint-adapter opens explicit `conn.transaction()` with awaits between DELETEs) | Attribute all THREE pools distinctly (P1 manager, P8 repair, P2b asyncpg adapter + P2 oracle); shared constant carries NO `application_name`; asyncpg pool gets its OWN DSN without mutating the shared `conn_string`; T2.7/AC18 stays satisfied; record the council's capability-table error as a corrected fact | phase2 (F2 tasks, AC18); decisions (new); plan-overview §6 C5 |
| **C-9** | G2.4 "unmodified" is unachievable (exact-equality pin on connect_args that F2 must change) | Specify the test update EXPLICITLY (amend expected shape; keep the assertion's INTENT — do NOT delete it); include amended expected-shape sketch in the plan task text | phase2 (T2.5, AC15); decisions (new); final-implementation-spec F2.8 |
| **C-10** | F7 would be GREEN on the incident's shape (a connection checked out before its first statement is 'idle', not 'idle in transaction') | F7 must measure OCCUPANCY AND RESIDENCE with attribution; never a bare count==0 assertion | phase3 (F7 invariant, AC24); decisions (new); final-implementation-spec §4 |
| **C-11** | No release posture in any artifact | Add RELEASE POSTURE section (stage-only, no promote/restart/.env, merge-latest-if-moved, rollback_safe adjudication) | plan-overview §15 (Release posture) |
| **C-12** | Flip condition undecidable (no F8 pin in inventory; D2 locked the wrong table on the wrong pool) | F8 must carry a pin that MEASURES the metric it claims to move (M-R3's manager-slot occupancy/residence under load, before/after); instrument = INSTRUMENTED CHECKPOINTER (leader ruling 4); checkpoint-table lock = FALLBACK | phase1 (F8 tasks, new pins); decisions (new); final-implementation-spec §5 |

### Folded warnings' dispositions (R13-R19 + R14/R17/R18)

| Warning | Disposition |
|---------|-------------|
| **R14** (G1.5/G3.5 whole-tree gates have NO collected-count fence) | **Closed by the lane-wide false-green invariant** (plan-overview §6 C3) — every gate asserts its collected count; a zero-collected gate is FATAL |
| **R17** (F2-g unsatisfiable) | **Closed by C-2 fold** — AC14/AC15/AC16/T2.4/T2.6/D-19/D-24/G2.4/F2-f/F2-g all change together in one commit |
| **R18** (Class-4a test form unfenced) | **Closed by C-1 fold** — the Class-1 env/interpreter fence applies to 4a (per-variable scrub + mktemp HOME + certified interpreter) |
| **R16** (default-executor saturation is an unmodelled daemon-wide amplifier) | **Accepted with disclosure** — assessed + disclosed (A7), not fixed; routed to the executor-ceiling commission |
| **R13** (the plan's fix set does not reduce the dominant producer) | **Closed by F8 + disclosure** — F8 removes the dominant multi-second producer M-R3; the dwell/occupancy class is disclosed as not fully repaired |
| **R15** (idle_in_transaction_session_timeout kills legitimate M-R3 work) | **Closed by sequencing** — F8 lands before the engine-level bound (A6-i) |
| **R19** (BEGIN fingerprint attribution is measured-sound) | **Reworded by P-11** — Site A is measured-unique (the only `text("BEGIN")` in `daemon/`); the correction belongs to A4-i (the architect's generic-marker claim is refuted), not to the Site-A attribution | **reworded-by-P-11** |

---

## 15. Release posture (NEW — C-11)

### 15.1 Stage-only

- **STAGE ONLY.** **NO promote.** The promote flip is the **user's 3-factor nonce ceremony** — never performed by this lane, never by an agent.
- **NO daemon restart.** **NO `.env` touch.** **NO live/production DB contact.** **No prod-data remediation** (the W-4 index-presence SELECT is **read-only** and is recorded in the gate report, not acted on).
- The live daemon, the live release symlink and the promote journal are **untouched** throughout.

### 15.2 Version slot

**`v0.27.12` is PROVISIONAL.** At stage time the slot is **re-derived from the live census** (W9: no `releases/v0.27.12` directory, no local tag, no remote tag, and no other lane holding the slot). If another lane took `v0.27.12` in the meantime, this lane takes the next free slot. **Never hard-code the slot in an artifact.**

### 15.3 merge-latest-if-moved (mandatory, immediately pre-stage)

`origin/latest` **may move** before staging. The L1 tip-identity guard **refuses a stale stage (exit 78)**; that refusal is a feature and **must not be overridden**.

1. `git fetch origin` and read the current `origin/latest`.
2. If `origin/latest` **equals** the lane's base — proceed.
3. If `origin/latest` **moved** — merge it into the lane branch (`--no-ff`, per house convention), then **re-verify the base**, **re-verify every `file:line`** in this spec (drift ⇒ re-derive), and **re-run the entire gate ladder** on the merged tree. A merged tip is a new base, not a footnote.
4. Re-derive the version slot (§15.2) **after** the merge.
5. **Only then** stage. If the L1 guard exits 78, **stop** — do not look for an override. Promote the staged artifact **as-is** if the tip has moved only in docs (`.agents/**`); a code-bearing tip requires a re-stage, not an override.

### 15.4 Artifact and rollback posture

- `rollback_safe` is adjudicated and recorded in the stage journal with its reasoning, per the standing rule (non-empty migration delta ⇒ explicit semantic reasoning, never a reflex).
- Expected migration delta vs the live release: **EMPTY** (no `.sql` file ships). The payload change is the binary plus the honesty comments; `agents/`, `frontend/`, `plugins/` payload trees are **byte-identical**.
- The staged artifact carries **both** provenance sidecars (binary + FE) and the staging worktree is **kept** for the ceremony.
- The stage log records the **measured** idle-in-txn bound and its command (C-4), or records that the bound shipped **DISABLED** pending the measurement.

### 15.5 Honest statement of what this lane does and does not close

**Closes:** the transaction-abort gap (F3) · the probe's server-side statement bound (F4) · the non-idempotent boot mint (F6) · the wedge-capture table bug (F5) · the manager-engine knob gap (F2) · **the dominant multi-second producer M-R3** (F8) · the attribution blind spot (three named engines) · the empirical baseline (Phase 0).

**Does NOT close — and must be stated in the shipped honesty blocks (F2.7):** the **dwell / occupancy** class. The recurrence precondition — **sync DB work on the event loop** (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) — is **not removed** by this lane, and the **BFS depth-loop** transaction span (`daemon/repositories/instance/repository.py:1434-1645`) is **not** touched. After this lane the daemon can still hold 15/15 manager slots; the guard's occupancy invariant (F7-e) is the thing that will see it, and the follow-up is the **owner** of the residual.

---

## 16. DR closure (round-2) — one line per DR landing in plan-overview.md / decisions.md

Closure lines for DR-1..DR-36 that land in THESE two files (full detail in delta-recheck.md §12-§14):

- **DR-1** (D-35 F4-b observable) → decisions.md D-35 amended in place (AMENDED 2026-10-28 round-2): statement cancelled, `to_thread` returns, `checkedout()` → baseline; backend row EXPECTED to persist (idle). plan-overview §14 C-7 row already carries the corrected clause.
- **DR-2** (one canonical ladder) → plan-overview C3c enumerates the canonical ladder; C3b legacy mapping re-keyed to it; reference to final-implementation-spec.md §6.1.
- **DR-3** (W-11 pin scope narrowed) → plan-overview C3b/C3c note the widened scan + sentinel-delimited exemption (prose; full scanner lives in the sibling's contract).
- **DR-4** (count==0 surviving in phase3) → recorded in decisions.md D-48 (F7 three-state) + plan-overview P-4; phase3 file owned by sibling.
- **DR-5** (AC14 hardcodes 60000) → decisions.md D-47 (bound ships DISABLED) + P-3; AC14 amendment is phase2-owned, recorded here as the rule.
- **DR-6** (retired ≈689 figure) → **CLOSED here**: SC10, §3.2, A3 rewritten to AST baseline 779 (A=613/B=175/C=4/D=148/E=14/F=0); aggregate marked non-canonical.
- **DR-7** (enablement mechanism) → decisions.md D-47 (defined-but-not-applied + enablement gate + log line).
- **DR-8** (honesty block backstop status) → recorded in decisions.md D-47/D-48; honesty-block text is final-implementation-spec-owned.
- **DR-9** (asyncpg DSN mechanism) → decisions.md D-49.
- **DR-10** (F8-c injection seam) → recorded; phase1-owned.
- **DR-11** (pre-F8 vs post-F8 sizing) → decisions.md D-47 (base measurement is an upper bound; post-F8 re-measurement tightens) + P-3.
- **DR-12** (stale `:734-743` ref) → plan-overview §6 C1 corrected to `:739-743` (P-10 row + §6 C1).
- **DR-13** (G3 rationale self-contradictory) → C3c re-key removes the contradictory mapping.
- **DR-14** (D-29 citation for C-1) → decisions.md index/council fold already cite D-29; no change needed.
- **DR-15** (pin→twin map) → recorded; contract-owned.
- **DR-16** (Class-4a/4b unfenced) → recorded; test-strategy-owned.
- **DR-17** (contract pin inventory 38 of 41) → recorded; contract-owned.
- **DR-18** (F6-e orphan TODO) → recorded; contract-owned.
- **DR-19** (task graph doesn't gate preconditions) → recorded; phase files owned by sibling.
- **DR-20** (pins hard-code the placeholder) → decisions.md D-47 (structure, not literal 60000).
- **DR-21** (W-11 pin false-REDs on architect artifact) → recorded; contract-owned.
- **DR-22** (F5 census keep-green pin dropped) → recorded; test-strategy-owned.
- **DR-23** (EXPECTED has no independent declared source) → recorded; test-strategy-owned.
- **DR-24** (F6-c/F7-a no static twin) → recorded; contract-owned.
- **DR-25** (F4.4 Verify cell) → recorded; contract-owned.
- **DR-26** (F8-a vs F8.4 bridge sentence) → recorded; contract-owned.
- **DR-27** (FOLD-MAP false verification claims) → recorded; FOLD-MAP-owned.
- **DR-28** (12 pins use wildcard) → recorded; contract-owned.
- **DR-29** (F4's premise refuted) → **CLOSED here**: P-1 row + F4 demoted to defence-in-depth + Phase 5 table; decisions.md D-45.
- **DR-30** (flip condition restated as precondition) → **CLOSED here**: P-2 row + OQ-6 restated; decisions.md D-46.
- **DR-31** (application_name via conninfo) → decisions.md D-49.
- **DR-32** (census scope = 11 sites) → decisions.md D-50.
- **DR-33** (OQ-1 closed: probe skipped, concurrent double-mint) → decisions.md D-51 + OQ-1 → CLOSED-by-measurement (R-8 row).
- **DR-34** (F5 raised to Phase 1) → **CLOSED here**: P-8 row + Phase 5 table + §4 F5 row; decisions.md D-52.
- **DR-35** (measured F7 invariant: three-state + xact_age) → decisions.md D-48.
- **DR-36** (baseline's disclosed limits) → recorded in the honesty/scope statements (plan-overview §1 + §15.5) + decisions.md D-47/D-48.

- **DR-36** (baseline's disclosed limits) → recorded in the honesty/scope statements (plan-overview §1 + §15.5) + decisions.md D-47/D-48.

### Δ1..Δ8 closure block (MINI-FOLD, 2026-10-28)

Architect amendments Δ1..Δ8 applied per `architecture-delta.md` §③. Landing sites by filename (this file + decisions.md + phase1-plan.md; sibling artifacts named by filename only — not edited here):

- **Δ1** (C3 fence fail-open) → sibling: `test-strategy.md` §2.3 integer validation + one sentence. (Not in this file set.)
- **Δ2** (asyncpg hold unowned) → **this file** §12 R-9 (new routed row, follow-up DB-pool commission owner, D-47 enablement gate) + §10 #3 pointer; **decisions.md** D-58; sibling: `test-strategy.md`/`final-implementation-spec.md` (not edited here).
- **Δ3** (budget derivation procedure) → sibling: `final-implementation-spec.md`/contract (not edited here).
- **Δ4** (BASELINE C-10 erratum) → sibling: `BASELINE.md` (not edited here).
- **Δ5** (F4-f race tolerance scoping) → **phase1-plan.md** T1.7a scoping (quiescent 15-holder setup; D-57 boundary race disclosed residual); sibling: `test-strategy.md` F4-f pin spec.
- **Δ6** (capacity-constant placement pinned to readiness.py) → **phase1-plan.md** D-57 row + T1.4 + AC4 (pinned home); **this file** §9 A6 conditional note + §6 C5 (matrix note); **decisions.md** D-59.
- **Δ7** (stale minimising comment in-lane comment-only edit) → **this file** §4 fix-list row (R-1 comment) + §12 R-1 note; sibling: `final-implementation-spec.md` numbered change.
- **Δ8** (inventory count reconciliation) → sibling: `expected-collected.md` (not edited here).
**Discrepancy with sibling artifacts:** none observed at write time for the in-scope items; the canonical ladder enumeration (C3c) is binding and any divergence with final-implementation-spec.md §6.1 is to be reported, not silently adopted.
