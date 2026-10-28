# Research — Phase-0 Baseline Extraction (fold round 2)

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`) · **worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn` @ `43e577a5bc45edb78fa69cba0c384e0545fdc6a7` (Phase-0 evidence merged).
- **Role:** extraction only — facts with cites. No plan text written, no fix chosen.
- **Evidence read:** `.agents/shared/planning/db-pool-idle-txn/BASELINE.md` (599L) · `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md` (576L, canonical per `BASELINE.md:15-18`) · `.agents/tester/harness/dbpool-phase0/{README.md,common.py?,t1..t5*.py,raw/*.out}` · plan-of-record `plan-overview.md`, `decisions.md`, `architecture-recommendation.md`, `phase0-plan.md`, `final-implementation-spec.md`, `test-strategy.md`, `delta-recheck.md` (§13/§14 DR-29..DR-36).
- **Line-number map:** `RESULTS.md` line = `BASELINE.md` line − 23 for every body line (BASELINE carries a 23-line header; body BASELINE:24 ≡ RESULTS:1; 599 − 576 = 23).
- **Fences honoured:** read-only except this file. No daemon, no DB, no git operations, no commits.

---

## 1. THE PREMISE DELTA (P-1 … P-9)

The baseline's consolidated table (`BASELINE.md:542-554` ≡ `RESULTS.md:519-531`) carries **9 rows**: rows 1–8 change a plan premise; row 9 **CONFIRMS** the plan. The baseline's own framing — *"Four of the five targets produced results that change a plan premise"* (`BASELINE.md:12-13` ≡ `RESULTS.md:9`) — maps to **T1, T2, T3, T4**; the fifth target **T5** is the one that closed **OQ-1** (`BASELINE.md:12-13`, `BASELINE.md:490-515`). (T5 additionally refutes two premises — rows 3 and 4 — so the "four/fifth" sentence under-counts T5's premise damage; the table is the authoritative list.)

### P-1 — F8 flip condition (T1) — NOT MET
| | |
|---|---|
| **Plan said** | *"leader ruling 1: if M-R3 holds a manager slot < 2 s under real load, approach C collapses to approach B"* (`final-implementation-spec.md:28`); *"the flip-condition verdict (hold < 2 s ⇒ F8 marginal ⇒ C collapses to B; hold ≥ 2 s ⇒ F8 load-bearing ⇒ C stands)"* (`phase0-plan.md:89`); *"If hold < 2 s, F8 is marginal and C collapses to B… If hold ≥ 2 s, F8 is load-bearing and C stands"* (`plan-overview.md:266` OQ-6) |
| **Measured** | **NOT MET.** Worst hold **8011.119 ms** (sync twin) — `BASELINE.md:133-134`, raw `t1_mr3_hold.out:345` `"worst_max_hold_ms": 8011.119` and `:343` `"flip_condition": "NOT MET"`. Async worst **1059.877 ms** (`BASELINE.md:134`, raw `t1:230`) — under 2 s **only because the awaited fetch was fast** (`BASELINE.md:134-137`) |
| **Plan text to change** | `final-implementation-spec.md:28`; `phase0-plan.md:89`; `plan-overview.md:266` (OQ-6) — restate as a **precondition**, not a constant (`BASELINE.md:139-143`); `decisions.md:172-180` (D-40 measuring pin). Delta-recheck's own condition: **DR-30** (`delta-recheck.md:430-438`) |

### P-2 — the flip is decidable from the HOLD (T1) — REFUTED (hold == fetch latency, 1:1)
| | |
|---|---|
| **Plan said** | The decision rule is stated over the **hold** (`final-implementation-spec.md:28`; `phase0-plan.md:77-91`; `decisions.md:84` — *"No instrument, sample rate, or threshold source exists anywhere"*) |
| **Measured** | *"the hold is **not an independent cost**: it is *exactly* the awaited content-fetch latency (L5: hold median 1029.9 ms vs await median 1026.5 ms; L2: hold 52.4 ms vs await 47.7 ms)"* (`BASELINE.md:134-137` ≡ `RESULTS.md:110-114`); row 2: *"the hold **equals** the awaited fetch latency (1:1) → measure the *fetch* latency, not the hold, to decide"* (`BASELINE.md:547` ≡ `RESULTS.md:524`). Raw: L5 hold median 1029.887 / await median 1026.527 (`raw/t1:228-239`); L2 hold 52.385 / await 47.693 (`raw/t1:107-118`) |
| **Plan text to change** | `decisions.md:172-180` (D-40 — the pin must instrument the **fetch**); `final-implementation-spec.md:28`; `plan-overview.md:266` — **DR-30** |

### P-3 — F4's premise: the readiness probe's retention is the carrier (T5b) — REFUTED for the incident's shape
| | |
|---|---|
| **Plan said** | F4 is the **primary** fix, carried by a probe-scoped `SET LOCAL statement_timeout` (D-04 `decisions.md:20`; `plan-overview.md:104` F4 row *"Probe-scoped server-side statement bound: `SET LOCAL statement_timeout`… sized at 2× the probe's asyncio budget"*; `final-implementation-spec.md:95-96` F4.1/F4.2). SC2: *"Under a forced probe timeout, a real PG shows **zero** backends in `idle in transaction` attributable to the probe"* (`plan-overview.md:234`) |
| **Measured** | **b3 (pool starvation, 15 holders): timeout fired at 0.5015 s; `checkedout()` after the timeout = 15 (= the holder count, NOT 16); the probe never got a connection** — *"the abandoned probe thread was a **pool WAITER**, not a holder"* (`BASELINE.md:468` ≡ `RESULTS.md:445`; raw `t5_fix_sites.out:1960-2019`, verdict at `:2019`). **b2 (`SELECT pg_sleep(5)`): timeout at 0.5011 s, `checkedout()` = 1, PG state = `active`** (not `idle in transaction`) — *"A probe-scoped `SET LOCAL statement_timeout` is therefore IRRELEVANT to this shape — the probe never reached a statement"* (`BASELINE.md:473-476` ≡ `RESULTS.md:449-453`). Measured hazard = **a stranded waiter thread** + **an unbounded statement duration on a connection the probe holds**, with no server-side bound (`BASELINE.md:482-488`). Row 9 **CONFIRMED**: the manager engine carries **neither** `statement_timeout` nor `idle_in_transaction_session_timeout` (`BASELINE.md:554` ≡ `RESULTS.md:531`; inventory `BASELINE.md:248`) |
| **Plan text to change** | `phase1-plan.md` D-04/T1.4 and `plan-overview.md:104` ("F4 primary") — **DR-29** (`delta-recheck.md:412-428`); `decisions.md:122-130` (D-35); `final-implementation-spec.md:95-101` (F4.1/F4.2/F4.7); `plan-overview.md:234` (SC2 is now the wrong observable — a probe holding a connection is `active`) |

### P-4 — the incident's "aborted connection handed back out" (T5a) — REFUTED for the pool path
| | |
|---|---|
| **Plan said** | The RCA's mechanism #3 (aborted connection handed out of the pool) is carried in the plan's premise ledger / F3's rationale (`plan-overview.md:55` premise 1; `plan-overview.md:92` M1; `decisions.md:17-19` D-01/D-02; `test-strategy.md:83-86` LEG A) |
| **Measured** | **REFUTED.** In every failure shape — a1 (statement fails, inner except), a2 (the rollback itself fails), a3 (BEGIN itself fails), a4 (aborted connection handed straight to the pool) — the backend's post-close state is `idle`, `xact_start=NULL`, `last_query='ROLLBACK'`, and **the next borrower succeeds**; `pool_reset_on_return = ResetStyle.reset_rollback` (SQLAlchemy 2.0.48 library default) (`BASELINE.md:406-421` ≡ `RESULTS.md:383-398`; raw `t5:4-56`). *"F3's 'BEGIN gap' leaves the connection in an aborted transaction **only for the remainder of the tool call**; it does **not** poison the pool"* (`BASELINE.md:423-426`). The incident's **journal quote IS reproducible** — but as a **same-connection cascade** on the wedge-capture one-shot (`db_capture.py:195`), where the first query (`tasks` → should be `task`) aborts the transaction and queries 2–3 fail with `InFailedSqlTransaction: current transaction is aborted, commands ignored until end of transaction block` (`BASELINE.md:428-453` ≡ `RESULTS.md:405-430`; raw `t5:57-77`, `"reproduces_incident_journal_quote": true` at `:76`). Row 4 action: *"drop the aborted-hand-off premise; **raise F5's priority** (the `tasks`→`task` rename is the cascade's root)"* (`BASELINE.md:549` ≡ `RESULTS.md:526`) |
| **Plan text to change** | `plan-overview.md:55`/`:92` (M1 wording) and `test-strategy.md:83-86` (LEG A BASE expectation "leaves the connection in `idle in transaction`" — measured: it does **not** survive the close); F5's phase placement (`plan-overview.md:121` puts F5 in **Phase 2**) — **DR-34** (`delta-recheck.md:445`) |

### P-5 — F7 = "no idle-in-transaction backend" / an idle-in-txn count is a valid census (T2) — REFUTED
| | |
|---|---|
| **Plan said** | F7 = *"Regression guard: 'no idle-in-transaction backend survives a read cycle'"* (`plan-overview.md:31`); A2: *"The current invariant ('idle-in-txn == 0 at every sample') is **red for a healthy busy daemon and green for a slow leak**"* (`architecture-recommendation.md:145`); D-38 withdraws the count invariant and asks for occupancy+residence (`decisions.md:152-160`) |
| **Measured** | **Census blindness CONFIRMED**: a backend checked out **before its first statement** reports `state='idle'`, `xact_start=NULL` — it is **NOT** `idle in transaction` (`BASELINE.md:200-205` ≡ `RESULTS.md:177-182`; raw `t2:2-18` a0 → `"visible as 'idle in transaction'? False"`, `t2:20-35` a, `t2:164-179` f, summary `t2:181-191` `"f_checked_out_no_statement_visible_as_idle_in_txn": false`). The **MEASURED F7 invariant** = occupancy across **all three** PG states + **residence** (`state_change − xact_start`) against the hold budget of the operation that opened it; *"A guard that counts only `idle in transaction` is **vacuous against two of the three shapes** and is therefore not a valid F7"* (`BASELINE.md:207-231` ≡ `RESULTS.md:184-208`). Healthy residence measured: **median 0.0005 s, p95 0.0007 s, max 0.0008 s over 40 cycles** (`BASELINE.md:215-218`; raw `t2:86-100`: n=40 min 0.0003 / median 0.0005 / p95 0.0007 / max 0.0008 / mean 0.0005) |
| **Plan text to change** | `decisions.md:152-160` (D-38 — update to the measured three-state + residence form); `plan-overview.md:31` (F7 row); `architecture-recommendation.md:145-151` (A2); `final-implementation-spec.md:184` (the 2.0 s placeholder) — **DR-35** (`delta-recheck.md:446`) |

### P-6 — "3 pools" (T3a) — REFUTED: 11 pool-creation sites
| | |
|---|---|
| **Plan said** | The attribution model is **three** pools: manager / repair / asyncpg adapter — *"the 3-pool attribution census"* (`phase0-plan.md:20`, `phase0-plan.md:133-146`); `final-implementation-spec.md:39`; `test-strategy.md:123`; `decisions.md:132-140` (D-36 three-pool model) |
| **Measured** | *"**12 pool-creation sites (11 real + 1 comment false-positive at `factory.py:289`)**"* (`BASELINE.md:239` ≡ `RESULTS.md:216`); the table enumerates all of them with pool size, `application_name` (**none**), server-side timeouts (**none** for the manager engine**) and DSN source (`BASELINE.md:241-253`); raw static scan: `raw/t3_pool_attribution.out:27-101` (12 entries: `persistence.py:316`, `persistence.py:355`, `__main__.py:237`, `factory.py:184/:215/:289(comment)/:323`, `ens_db_tools.py:633/:691`, `vacuum_engine.py:79`, `db_pool_manager.py:261`, `db_capture.py:195`). *"The task brief assumed **three** pools. There are **eleven**. The incident's 18 observed backends exceeding the manager engine's 15-slot cap is therefore **fully explainable by multi-pool occupancy** — no single-pool overflow is needed"* (`BASELINE.md:255-257` ≡ `RESULTS.md:232-234`). Row 6 action: *"widen the census scope"* (`BASELINE.md:551` ≡ `RESULTS.md:528`) |
| **Plan text to change** | `phase0-plan.md:20`/`:133-146`; `final-implementation-spec.md:39`; `test-strategy.md:123`; `decisions.md:134` (D-36 — state that the three named `application_name`s are the daemon-internal **subset**, not the census boundary) — **DR-32** (`delta-recheck.md:443`) |

### P-7 — per-pool `application_name` via `-c application_name=` in each engine's own options string (T3c) — VIABLE BUT THE WEAKER CHANNEL
| | |
|---|---|
| **Plan said** | D-30: manager engine → `-c application_name=ensemble-manager`; repair engine → `-c application_name=ensemble-repair`; asyncpg → separate DSN (`decisions.md:60-68`); F2.3/F2.5/F2.6 render the names into the engines' own options strings (`final-implementation-spec.md:120`/`:122`/`:123`); F2-g asserts name presence (`test-strategy.md:130`) |
| **Measured** | `-c application_name=ensemble-manager` **lands** (`raw/t3:103-109`); within ONE options string **last `-c` wins** (`raw/t3:121-125`: `A_then_B: beta`, `B_then_A: alpha`); **the conninfo parameter is the STRONGER channel** — `conninfo application_name=from-conninfo` **beats** `options=-c application_name=from-options` (`raw/t3:127-135`); `ALTER ROLE … SET application_name` lands and a **startup packet beats the role default** (`raw/t3:137-141`); asyncpg `server_settings={...}` and `?application_name=` both land (`raw/t3:143-147`); **the ordering hazard is MEASURED**: with the shared constant appended last, the repair engine reports the manager's name — `"repair_engine_reports_manager_name": true` (`raw/t3:111-119`; `BASELINE.md:288-299` ≡ `RESULTS.md:265-276`). Mitigation that works: put `application_name` in the **conninfo parameter**, never in a shared `-c options` string (`BASELINE.md:301-304` ≡ `RESULTS.md:278-281`). Row 7 action: *"viable, but **only** via the **conninfo parameter**… and the asyncpg pool needs its **own DSN**"* (`BASELINE.md:552` ≡ `RESULTS.md:529`) |
| **Plan text to change** | `decisions.md:62` (D-30); `final-implementation-spec.md:120` (F2.3), `:122` (F2.5), `:123` (F2.6); `test-strategy.md:130` — **DR-31** (`delta-recheck.md:442`) |

### P-8 — OQ-1 (mint trigger "unexplained") — **CLOSED**; and the fingerprint premise (T5c + T4)
| | |
|---|---|
| **Plan said** | *"The 4×/boot `UniqueViolation` is **real**; its trigger is **unexplained** (open question, §11)"* (`plan-overview.md:59`); OQ-1 (`plan-overview.md:261`) — *"a deterministic 4× is not explained by a race alone"*; R6 (`plan-overview.md:214`); D-42 routed OQ-1 out of the lane (`decisions.md:194`) |
| **Measured** | **CLOSED — trigger = a concurrent double-mint for the same anchor.** Sequential path is idempotent (c2 → rung 1, no violation). **c4 (8-way concurrent, no barrier): 7 errors, all 7 `UniqueViolation` on `ux_mission_records_anchor_open` — "the race fires NATURALLY; no barrier needed"** (`BASELINE.md:497` ≡ `RESULTS.md:474`; raw `t5:2046-2058`: `"error_count": 7, "unique_violation_count": 7`). **c4b (probe→insert window forced to overlap): 5 × `IntegrityError`** (`BASELINE.md:498` ≡ `RESULTS.md:475`; raw `t5:2059-2069`). Why the loser is not recovered: *"`_mint_epoch_locked`'s `IntegrityError` handler re-resolves via a probe **keyed on `founding_message_id`**, and the **boot** mint has `founding_message_id=None` — so the re-resolve probe is **skipped** and the error **re-raises**"* (`BASELINE.md:511-515` ≡ `RESULTS.md:488-492`). Three boot-lane call sites, two of which can be in flight for the same `instance_id` (`BASELINE.md:517-533`) ⇒ *"This matches the incident's '4x/boot (once per actor)' exactly"* (`BASELINE.md:532-533`). F6 consequence: *"F6's fix must therefore be an **idempotent upsert**… a re-resolve probe alone will **not** fix it"* (`BASELINE.md:535-538` ≡ `RESULTS.md:512-515`) |
| **Plan text to change** | `plan-overview.md:59` (premise 5), `:214` (R6), `:261` (OQ-1 — the "candidate hypothesis" is now the **measured** trigger); `decisions.md:25` (D-09 — must state the **skip**, not only the key), `decisions.md:194` (D-42 routing) — **DR-33** (`delta-recheck.md:444`) |

### P-8b — (T4) the fingerprint premise: "not reproducible at BASE" / "BEGIN is a generic marker" — REFUTED
| | |
|---|---|
| **Plan said** | *"**Two of the RCA's four `pg_stat_activity` fingerprints are NOT reproducible at BASE**"* (`test-strategy.md:40`); architect: *"`last_query='BEGIN'` is therefore the generic marker of 'a transaction opened and then went quiet'… **A cannot be the fingerprint owner**"* — downgrading `plan-overview.md:78` (*"**CONFIRMED attribution**"*) to UNVERIFIED (`architecture-recommendation.md:63`, A4-i `:154`) |
| **Measured** | **All four reproduce byte-exactly** (`BASELINE.md:355` ≡ `RESULTS.md:332`; raw `t4:46-55` comparison matrix: `BEGIN: true`, `SELECT instances...: true`, `SELECT count(*) FROM job_queue_items...: true`, `INSERT ... mission_records: true`). The `BEGIN` owner is measured to be `daemon/tools/ens_db_tools.py:1479` (DDL) / `:1491` (DML) — *"This is the only `text("BEGIN")` in `daemon/`; the RCA's attribution to Site A is **CONFIRMED**"* (`BASELINE.md:366-367` ≡ `RESULTS.md:343-344`; raw `t4:12-18`). **The architect's "generic wire-level BEGIN" premise is contradicted by the measurement**: after an autobegun statement the backend's `last_query` is the **statement** (`SELECT 1`, raw `t2:37-52`, last_query at `:48`), and every M-R3 iteration's held backend reported `idle in transaction` with `last_query` = the ORM SELECT (`BASELINE.md:391-398` ≡ `RESULTS.md:368-375`) — i.e. a literal `BEGIN` `last_query` is a **standalone-BEGIN marker**, not psycopg3's implicit BEGIN. See §6 for the full P0-9 answer |
| **Plan text to change** | `test-strategy.md:40` (§0.3 "NOT reproducible at BASE"); `architecture-recommendation.md:63`/`:154` (A4-i) — and `plan-overview.md:78` is **restored** as CONFIRMED, not downgraded; `plan-overview.md:227` (R19 "the BEGIN fingerprint attribution is unsound") |

### P-9 — (CONFIRMING row, not a delta) the manager engine carries no server-side timeout — **CONFIRMED**
`BASELINE.md:554` ≡ `RESULTS.md:531`; inventory `BASELINE.md:248` (`pool_size=5, max_overflow=10`, `application_name` none, **no** `statement_timeout` / `idle_in_transaction_session_timeout` / `pool_recycle` / `pool_timeout` override); the library-default `pool_timeout` is **30 s** (`BASELINE.md:112-113`).

### Extra contradiction found (not in the baseline's table) — the asyncpg pool IS idle-in-txn capable
`plan-overview.md:275` lists *"The **asyncpg checkpoint-adapter pool** … not a leak site"* as **out of scope**. Measured: the asyncpg adapter's explicit `conn.transaction()` with three awaits between DELETEs leaves the backend **`idle in transaction`**, `xact_start` present, **still present after 3 s**, `server_side_reap_observed: false` (cluster default `idle_in_transaction_session_timeout = 0` = disabled) (`BASELINE.md:306-323` ≡ `RESULTS.md:283-300`; raw `t3:149-161`). Also the shared `conn_string` lands the **same** `application_name` on the P2 psycopg pool and the asyncpg pool — `p2_psycopg_pid 2325876` / `asyncpg_pid 2325878`, both `ensemble-shared-dsn` (`BASELINE.md:325-337` ≡ `RESULTS.md:302-314`; raw `t3:163-172`) ⇒ a **separate DSN** is required.

---

## 2. THE F8 FLIP CONDITION — VERDICT: **NOT MET**

- **Condition:** *"if a PG repro shows M-R3 holding a manager slot < 2 s under real load, approach C collapses to approach B"* (`final-implementation-spec.md:28`).
- **Verdict:** **NOT MET — F8 stands.** (`BASELINE.md:129-131` ≡ `RESULTS.md:106-108`; raw `t1:341-345`: `"flip_condition": "NOT MET"`, `"flip_condition_text": "hold < 2000ms  -> CONDITION NOT MET (F8 stands)"`, `"worst_max_hold_ms": 8011.119`.)
- **Restatement (measured):** *"the M-R3 hold < 2 s **iff** the awaited content fetch < 2 s"*, a **data-dependent** property of the fetch path; *"The sync twin is unconditionally ≥ 8 s"* (`BASELINE.md:139-143` ≡ `RESULTS.md:116-120`).

### Measured hold distribution (all ms)

| Regime | n | min | **median** | p95 | **max** | median await | pg state during await | cite |
|---|---|---|---|---|---|---|---|---|
| L0 no load | 30 | 5.929 | **7.121** | 8.475 | **21.654** | 2.692 | `idle in transaction` 30/30 | `BASELINE.md:83`; raw `t1:26-63` |
| L1 pool pressure (8 holders) | 30 | 36.123 | **46.471** | 49.977 | **55.307** | 3.686 | 30/30 | `BASELINE.md:84`; raw `t1:64-100` |
| L2 event-loop peg | 30 | 26.225 | **52.385** | 61.927 | **73.601** | 47.693 | 30/30 | `BASELINE.md:85`; raw `t1:101-142` |
| L3 pressure + loop peg | 30 | 55.233 | **78.846** | 100.525 | **114.502** | 40.657 | 30/30 | `BASELINE.md:86`; raw `t1:143-184` |
| L4 pool over-subscribed (`pool_timeout=3s`) | 4 | 10.275 | **11.220** | 12.502 | **12.502** | 3.782 | 4/4 | `BASELINE.md:87`; raw `t1:185-221` |
| L5 loop peg + 1.0 s modelled fetch tail | 15 | 1027.955 | **1029.887** | 1033.342 | **1059.877** | 1026.527 | 15/15 | `BASELINE.md:88`; raw `t1:222-263` |
| L4b genuine over-subscription (16th borrow) | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | borrow **timed out** at `pool_timeout`; `mr3_borrow_wait_ms: 3001.116` | `BASELINE.md:89`/`:101-110`; raw `t1:2-11`, `264-301` |
| **L6 SYNC TWIN** (8 s bridge, inner never completes) | **1** | 8011.119 | **8011.119** | 8011.119 | **8011.119** | 8000.320 | `idle in transaction` 1/1 | `BASELINE.md:90`/`:117-124`; raw `t1:13-23`, `302-338` |

- **Structural facts confirmed on every iteration (139/139):** `same_conn_across_suspension_all = True` (the pooled connection identity is unchanged across the suspension — the ORM does **not** release it before the await); `in_transaction_after_await_all = True`; `pg_state_during_await_counts = {'idle in transaction': N}` for **every** iteration; `peak_engine_pool_checkedout` = 1 (no pressure) / 9 (8 pressure holders) (`BASELINE.md:92-97` ≡ `RESULTS.md:69-74`; raw `t1:60-62`, `97-99`).
- **Sync twin ceiling:** bounded **only** by the 8 s bridge (`asyncio.run_coroutine_threadsafe(...).result(timeout=8.0)`, `manager.py:11208-11224`) (`BASELINE.md:126-127` ≡ `RESULTS.md:103-104`).
- **Instrument:** harness `t1_mr3_hold.py` on a lane-owned scratch DB; REAL = every PG statement / `pg_stat_activity` row / pool config / client behaviour; **MODELLED (disclosed)** = the python-side half of the content fetch, the "1.0 s fetch tail" regime, and the event-loop peg (`README.md:65-83`); engine configs replicated byte-for-byte from base (`README.md:79-83`).
- **Plain statement:** the **< 2 s condition does NOT hold**. Worst measured hold **8011.119 ms** (sync twin); async worst **1059.877 ms** — and that async figure is an artefact of a **modelled fast fetch**, not a property of the code.

---

## 3. MEASURED INPUTS FOR THE MANAGER IDLE-IN-TXN BOUND (D-32 rule)

**The rule:** `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS ≥ 4 × measured max legitimate hold`, floor 30 s; if unmeasurable at stage time the bound ships **DISABLED** (constant defined, not applied) (`decisions.md:80-88` D-32; `final-implementation-spec.md:28`, `:180`; `phase0-plan.md:165`).

| Input | Measured value | cite |
|---|---|---|
| **Measured max legitimate hold** (async) | **1059.877 ms** (L5, n=15, modelled fetch tail) | `BASELINE.md:88`; raw `t1:230` |
| **Measured max legitimate hold** (sync twin) | **8011.119 ms** (n=1) — bounded by the 8 s bridge, a *configured* timeout | `BASELINE.md:90`/`:126-127`; raw `t1:307-311` |
| **P100** of the async hold distribution | = max = **1059.877 ms** (L5); no-load P100 = **21.654 ms** | raw `t1:230`, `t1:35` |
| **Continuous idle-in-txn gap distribution (healthy, closed borrows)** | n=40 — min **0.0003 s**, median **0.0005 s**, p95 **0.0007 s**, max **0.0008 s**, mean **0.0005 s**; `observed_states_during_borrow = {'idle in transaction': 40}` | `BASELINE.md:170-176`; raw `t2:86-100` |
| **Held-borrow observability** (deliberate holds 0.5/1.0/2.0/3.0 s) | observed min **0.5017 s**, median **1.5024 s**, p95/max **3.002 s** (n=4) | raw `t2:102-119`; narrative `BASELINE.md:177-179` |
| **Victim-class analysis — is M-R3's dwell killable?** | **NOT ESTABLISHED as measured.** The plan's figure *"M-R3's legitimate dwell ~13-15 s is NOT killable by a 60 s bound"* (`decisions.md:82`; `final-implementation-spec.md:184`) is a **plan-side estimate**; the baseline measured M-R3 dwell at **7.1 ms (no load) … 1059.9 ms (modelled 1 s fetch tail)** — never 13–15 s. The baseline's own T2e measurement shows the mechanism **does** fire and **terminates the backend** (row gone from `pg_stat_activity` at 2.03 s; `terminated: true`, `elapsed_s: 2.02`) — i.e. the *kill* is real; what is **unmeasured** is the real M-R3 dwell distribution that would size it. | `BASELINE.md:181-198`; raw `t2:121-162` |
| **Async twin bounded or unbounded?** | At base the hold is **coupled to the fetch, not to the DB** and therefore **not structurally bounded** (no server-side timeout on the manager engine — row 9 CONFIRMED; and the fetch duration is data-dependent). The plan's own statement that the async twin's hold is *currently UNBOUNDED* (`decisions.md:82`) is consistent with the measurement. | `BASELINE.md:145-151`; `:554` |
| **Is the measurement MEASURABLE at all?** | **PARTIAL / NO for the bound's derivation input.** *"The **real** `_fetch_subshape_a_content_async` latency (the true content-fetch distribution) … needs a booted daemon + a real checkpoint store; the harness models the python-side half (disclosed)… The T1 flip decision therefore rests on a *model* of the fetch, and the plan should measure the real fetch before finalising"* (`BASELINE.md:558-561` ≡ `RESULTS.md:535-538`). The real `ChildReportsService._get_last_assistant_message` python-side cost is likewise **not measured** (`BASELINE.md:561`). ⇒ per D-32 / `final-implementation-spec.md:180`, the bound's enablement gate ("enablement gated on the measurement") is **NOT satisfied** by this baseline; the bound would ship **DISABLED** unless the real fetch latency is measured. |

**Arithmetic if the measured figures were taken at face value (not a recommendation):** async-only max 1059.877 ms × 4 = **4239.5 ms** → the **30 s floor** applies → 30 s. Using the sync twin's 8011.119 ms × 4 = **32 044 ms** → **32.1 s**. Both inputs are model-derived; the binding input is the *real* fetch latency, which is **NOT ESTABLISHED**.

---

## 4. MEASURED INPUTS FOR THE F7 RESIDENCE BUDGET (D-38 rule)

**The rule:** `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S = measured P100 legitimate hold + stated margin`, margin ≥ 2× the measurement noise floor, floor 2.0 s (`final-implementation-spec.md:184`); the invariant form (`decisions.md:152-160` D-38) = occupancy returns to baseline **AND** no backend's idle-in-txn residence exceeds the budget; count may be reported and bounded (`peak_occupancy ≤ λ_measured × budget`) but **never asserted to be zero**.

| Input | Measured value | cite |
|---|---|---|
| **Measured P100 (healthy residence)** | **0.0008 s** (max over n=40; p95 0.0007 s; median 0.0005 s) | `BASELINE.md:215-218`; raw `t2:86-100` |
| **Measurement noise floor** | **NOT ESTABLISHED as a named number.** The observable-lag distribution itself is the floor: min **0.0003 s**, median **0.0005 s** over 40 cycles (raw `t2:89-95`); the baseline never states a "noise floor" value or its derivation ⇒ the "margin ≥ 2× the noise floor" clause has **no measured input** in this baseline | — |
| **Measured P100 of the M-R3 hold (the operation whose budget matters)** | **1059.877 ms** (async, L5 n=15); **8011.119 ms** (sync twin, n=1) | raw `t1:230`, `t1:307-311` |
| **Resulting budget** | From the measured inputs: P100(healthy 0.0008 s) + margin ⇒ the **2.0 s floor** dominates ⇒ **2.0 s**; from the M-R3 P100 (1.0599 s) + margin ⇒ also **≥ 2.0 s** floor ⇒ **2.0 s**. But the baseline's measured **sync-twin hold (8.011 s) and async worst (1.06 s)** both sit at/above that budget ⇒ a blanket 2.0 s invariant **reddens legitimate M-R3 work** unless F8 removes it — the plan already flags this (*"a 2.0 s budget can redden a HEALTHY daemon (false red) unless F8 removes every hold above it, which nothing guarantees"*, `final-implementation-spec.md:184`). The baseline's own measured invariant is **per-operation**: *"A backend whose residence exceeds the **measured** hold budget **of the operation that opened it** is a retention, not an in-flight request"* (`BASELINE.md:217-218` ≡ `RESULTS.md:194-195`) | `BASELINE.md:207-231`; `final-implementation-spec.md:184` |
| **Does the budget red a healthy daemon at the measured distribution?** | **NO for the measured healthy population** (all healthy borrows ≤ **0.0008 s** residence, raw `t2:89-95`) ⇒ a ≥ 2.0 s budget is ~2 500× above it. **YES (false red) for the measured M-R3 population**: the async hold reaches **1059.877 ms** (still < 2.0 s) but the **sync twin reaches 8011.119 ms** ⇒ a blanket 2.0 s budget **reddens the sync twin**; the plan's own 13–15 s dwell warning (`final-implementation-spec.md:184`) is *unmeasured* and therefore an additional, unquantified false-red risk | as above |

### Base occupancy / residence census shape (measured)

| PG state | What the baseline measured | cite |
|---|---|---|
| **`idle`** (checked out, **no statement** ⇒ `xact_start IS NULL`) | state `idle`, `wait_event_type=Client`, `xact_start=NULL`, `last_query='ROLLBACK'` (a0/a) or `""` (f) ⇒ **invisible to an idle-in-txn census** | raw `t2:2-18`, `t2:20-35`, `t2:164-179`; `BASELINE.md:200-205` |
| **`idle in transaction`** | set after the first statement (autobegin): `xact_start` set, `last_query='SELECT 1'`; visible for the whole hold | raw `t2:37-52`; `BASELINE.md:164` |
| **`active`** | the abandoned readiness probe's backend is **`active`** (running the statement), not idle-in-txn ⇒ invisible to an idle-in-txn census | `BASELINE.md:477-480`; raw `t5:1085-1093` |
| **Residence-time buckets** | The baseline reports a **distribution** (n=40: min 0.0003 / median 0.0005 / p95 0.0007 / max 0.0008 s) and the **held-borrow** series (0.5017 / 1.5024 / 3.002 s for requested 0.5/1.0/2.0/3.0 s) — **not** the bucketed census the Phase-0 contract asked for (*"Report residence-time buckets (distinguish '6 short borrows' from '1 long leak')"*, `phase0-plan.md:100`, `final-implementation-spec.md:30`) | raw `t2:86-119`; `phase0-plan.md:100` |
| **λ (arrival / borrow rate)** | **NOT ESTABLISHED — NOT MEASURED.** The contract requires *"the churn leg's own borrow rate (λ) — measured, not assumed"* (`phase0-plan.md:101`) and the invariant form depends on it (`peak_occupancy ≤ λ_measured × budget`, `phase0-plan.md:109`; `decisions.md:154`). The baseline measured **no churn-leg census and no λ** ⇒ the F7 invariant's occupancy bound currently has **no measured λ input**. | `phase0-plan.md:101`/`:109`; `decisions.md:154` |

---

## 5. PER-POOL ATTRIBUTION (P0-5)

- **How many pools:** **12 pool-creation sites (11 real + 1 comment false-positive at `factory.py:289`)** — enumerated with kind/file:line/size/`application_name`/server-side timeouts/DSN source in `BASELINE.md:241-253` ≡ `RESULTS.md:218-230`; raw static scan `raw/t3:27-101`. The manager engine is `factory.py:323` (`pool_size=5, max_overflow=10` = **15 slots**); the repair engine is `ens_db_tools.py:633` (`pool_size=2, max_overflow=3` = 5 slots, `statement_timeout=60s`, `lock_timeout=10s`, `idle_in_transaction_session_timeout=60s`, `pool_recycle=3600`, `pool_timeout=10`).
- **Does the shared `conn_string` carry an `application_name`?** **NO.** Every daemon pool's `application_name` column reads **none** at base (`BASELINE.md:241-253`); measured census with the manager **and** repair engines connected together: `backends_by_application_name: { "": 2 }`, `distinct_states: ['idle']`, `last_query: 'ROLLBACK'` — *"verdict: distinguishable ONLY by last_query text, NOT by pool"* (`BASELINE.md:259-272` ≡ `RESULTS.md:236-249`; raw `t3:2-25`). *"**`application_name` is empty for every daemon pool at base.** Post-hoc attribution to a specific pool is impossible"* (`BASELINE.md:270-271`).
- **Is `application_name` attribution viable?** **Yes, with two constraints** (`BASELINE.md:339-349` ≡ `RESULTS.md:316-326`): (1) the asyncpg pool must get its **own DSN** (it shares the P2 psycopg pool's `conn_string`, `persistence.py:225` → `:317`/`:356`) — measured: `p2_psycopg_pid 2325876` and `asyncpg_pid 2325878` both report `ensemble-shared-dsn` (`raw/t3:163-172`); (2) it must be set via the **conninfo parameter**, not a `-c` inside a shared `options` string.
- **Was last-`-c`-wins confirmed?** **YES**, twice: (a) within one options string (`A_then_B: beta`, `B_then_A: alpha`, `raw/t3:121-125`); (b) the shared-constant hazard — the repair engine with the shared constant appended last reports **`ensemble-manager`** (`repair_engine_reports_manager_name: true`, `raw/t3:111-119`; `BASELINE.md:288-299`). Stronger channel confirmed: **conninfo beats `-c options`** (`raw/t3:127-135`).
- **The 3-pool census vs the 15-slot cap:** the incident's **18** observed backends are *"**fully explainable by multi-pool occupancy** — no single-pool overflow is needed"* (`BASELINE.md:255-257` ≡ `RESULTS.md:232-234`); row 6: *"3 pools → **11** pool-creation sites; the incident's 18 backends are explainable by multi-pool occupancy → widen the census scope"* (`BASELINE.md:551` ≡ `RESULTS.md:528`).
- **Third leak-capable pool measured:** the asyncpg checkpoint-adapter pool holds `idle in transaction` with `server_side_reap_observed: false` and the cluster default `idle_in_transaction_session_timeout = 0` (disabled) (`BASELINE.md:306-323`; raw `t3:149-161`).

---

## 6. T4 FINGERPRINT RECONCILIATION

**All four recorded fingerprints reproduced byte-exactly at base** (`BASELINE.md:355` ≡ `RESULTS.md:332`; raw `t4:46-55` matrix — all four `true`).

| Incident fingerprint | Verdict | Exact `last_query` observed at base | Owner (measured) | cite |
|---|---|---|---|---|
| `BEGIN` ×2 | **REPRODUCED (byte-exact)** | `BEGIN` — `pg_state: idle in transaction` | `daemon/tools/ens_db_tools.py:1479` (DDL) / `:1491` (DML) — *"the only `text("BEGIN")` in `daemon/`"* ⇒ the RCA's Site-A attribution is **CONFIRMED** | `BASELINE.md:359`/`:366-367`; raw `t4:12-18` |
| `SELECT instances.instance_id, instances.project_id …` ×2 | **REPRODUCED (byte-compatible)** | `SELECT instances.instance_id, instances.project_id, instances.agent_id, instances.agent_dir, instances.agent_name, instances.agent_tag, instances.parent_id, instances.status, instances.metadata, instances.version, instances.last_activity_at, instances.created_at, instances.updated_at, instances.paused_at, instances.attestation_denied_count, instances.completion_gate_escalated \nFROM instances` | `SQLModelInstanceRepository.list` (`daemon/repositories/instance/repository.py:1259+`) | `BASELINE.md:360`/`:368-371`; raw `t4:4-11` |
| `SELECT count(*) FROM job_queue_items …` ×1 | **REPRODUCED (byte-compatible)** | `SELECT count(*) FROM job_queue_items j LEFT JOIN job_queues q ON j.queue_id = q.queue_id WHERE j.deleted_at IS NULL AND j.admission_state = 'queued' AND q.queue_type = 'defer'` | `daemon/services/defer_block_resolver.py:185-191` (`_DEFER_PENDING_COUNT_SQL`), executed context-managed at `:274`/`:321` | `BASELINE.md:361`/`:372-374`; raw `t4:19-25` |
| `INSERT … mission_records` ×4/boot | **REPRODUCED (byte-compatible)** | `INSERT INTO mission_records (mission_id, anchor_instance_id, epoch_seq, founding_message_id, founding_source, commissioner_instance_id, work_id, parent_mission_id, status, attested_at, attest_method, …)` | ORM insert `daemon/repositories/mission_record/repository.py:498-514` (`insert_mission`) | `BASELINE.md:362`/`:375-376`; raw `t4:36-43` |

**Adjacent negative (also measured):** the readiness queue probe does **NOT** read `job_queue_items` — its two statements read `FROM task` (`SELECT EXTRACT(EPOCH FROM (now() - MAX(CASE WHEN last_heartbeat_at >= $1 THEN last_heartbeat_at END))) FROM task WHERE status = $2` and `SELECT COUNT(CASE WHEN last_heartbeat_at >= now() - ($1 * interval '1 second') AND last_heartbeat_at >= $2 THEN 1 END) FROM task WHERE status = $3`); `reads_from_task = True`, `byte_compatible_with_incident = False` (`BASELINE.md:378-389` ≡ `RESULTS.md:355-366`; raw `t4:26-35`).

**What the fingerprints do and do not prove (measured):** *"A fingerprint identifies **the statement that opened the transaction**, not the code path that **abandoned** it… the fingerprint alone cannot separate 'in flight' from 'abandoned'. **T2(d) residence is the discriminator the fingerprints lack**"* (`BASELINE.md:391-398` ≡ `RESULTS.md:368-375`).

### P0-9 — does psycopg3 pipeline the implicit BEGIN?

**ANSWER (measured): NO — a literal `BEGIN` `last_query` is a STANDALONE-BEGIN marker, not psycopg3's implicit BEGIN.**

- After an **autobegun** statement the backend's `pg_stat_activity.last_query` is the **statement**: `state: idle in transaction`, `xact_start` set, `last_query: "SELECT 1"` (raw `t2:37-52`, `last_query` at `:48`; summary `t2:185` `"b_after_statement_state": "idle in transaction"`).
- Every M-R3 iteration's held backend reported `idle in transaction` with `last_query` = the **ORM SELECT** (`BASELINE.md:394-396` ≡ `RESULTS.md:371-373`).
- `last_query = 'BEGIN'` appears only where a **literal `BEGIN` statement** was the last command sent: the explicit `session.execute(text("BEGIN"))` at `ens_db_tools.py:1479/:1491` (raw `t4:13`, note at `:17`) and the harness's own explicit `probe.execute(text("BEGIN"))` (raw `t2:133`; harness source `t2_stat_activity.py:165`).
- **Consequence — this CONTRADICTS the architect's A4-i premise** (`architecture-recommendation.md:63`: *"libpq/psycopg3 opens every transaction by sending a literal `BEGIN` command. `last_query='BEGIN'` is therefore the generic marker of 'a transaction opened and then went quiet'… A cannot be the fingerprint owner"*) and **RE-CONFIRMS** `plan-overview.md:78` (*"**CONFIRMED attribution**"*). The measured statement is the baseline's own: *"the RCA's `BEGIN` attribution was unique only because the SQL text `BEGIN` occurs in exactly one place in `daemon/` (measured — see T4/F-B)"* (`BASELINE.md:270-272` ≡ `RESULTS.md:247-249`).
- **Caveat:** the baseline never states the pipeline question in these words; the conclusion is **inferred** from the three measurements above (T2b, T1, T4/F-B). The SQLSTATE of the *implicit* BEGIN was never printed.

---

## 7. T5 FIX-SITE BEHAVIOURS

### (i) The aborted-connection handoff — **REFUTED for the pool path**; the journal quote is a **same-connection cascade**
- a1 (statement fails, inner except): `explicit_rollback_ran: true`, `in_transaction_at_close: false`, post-close `idle`, `last_query=';'` — **next borrower OK** (raw `t5:4-18`).
- a2 (the **rollback itself** fails, outer `except` at `:1513`, no rollback): `explicit_rollback_ran: false`, `in_transaction_at_close: true` **at close**, post-close `idle`, `last_query='ROLLBACK'` — **next borrower OK** (raw `t5:19-33`).
- a3 (the **BEGIN itself** fails): post-close `idle`, `last_query='ROLLBACK'` — **next borrower OK** (raw `t5:34-47`).
- a4 (aborted connection handed **directly** to the pool): post-close `idle` — **next borrower OK**; `"pool_reset_on_return": "ResetStyle.reset_rollback"` (raw `t5:48-56`).
- The incident's journal quote — *"Wedge-capture: store census failed: current transaction is aborted, commands ignored until …"* — **IS** reproduced, but on the **wedge-capture one-shot** (`db_capture.py:195`, three reads on one connection, `close()` in `finally`, **no rollback between them**): step 1 fails `UndefinedTable: relation "tasks" does not exist`, steps 2 and 3 fail `InFailedSqlTransaction: current transaction is aborted, commands ignored until end of transaction block`; `"reproduces_incident_journal_quote": true` (raw `t5:57-77`; narrative `BASELINE.md:428-453` ≡ `RESULTS.md:405-430`).
- **The exact journal string asked for — "current transaction is aborted, commands ignored until rollback" — APPEARS**, verbatim, in the measured wedge-capture errors at raw `t5:66-74` (and in the report at `BASELINE.md:444-445` ≡ `RESULTS.md:421-422`).

### (ii) The readiness-probe timeout path — MEASURED retention window
- Shape: `with engine.connect() as conn: conn.execute(sa_text("SELECT 1"))` at `daemon/services/readiness.py:1462-1482`, run via `asyncio.to_thread` + `asyncio.wait_for(budget)` (`BASELINE.md:461-462` ≡ `RESULTS.md:438-439`; harness source `t5_fix_sites.py:124-181`).
- **b1** `SELECT 1`, budget 0.5 s: no timeout, `checkedout_at_timeout = 0`, `idle` (raw `t5:82-88`, summary `:1015-1019`).
- **b2** `SELECT pg_sleep(5)`, budget 0.5 s: timeout fired at **0.5017 s** (raw `t5:1027`); `checkedout_at_timeout = 1`; PG state **`active`** for the whole retention (raw `t5:1029-1093` and onward); the connection is released **between t_s 4.4747 s (last `checkedout: 1`) and t_s 4.5275 s (first `checkedout: 0`)** (raw `t5:1546-1555`) ⇒ **retention after the budget expires ≈ 4.5 s = the statement's remaining duration**, i.e. **the retention equals the statement duration** and is **unbounded** without a server-side `statement_timeout` (row 9: the manager engine has none). `idle_in_txn_observed: false`, `max_t_idle_in_txn_s: 0.0` (raw `t5:1954-1958`). Report form: *"t ≈ 4.52 s"* (`BASELINE.md:467` ≡ `RESULTS.md:444`).
- **b3** pool starvation (15 holders), budget 0.5 s: timeout at **0.5012 s** (raw `t5:1966`); `checkedout_right_after_timeout = 15` = the holder count (**not 16**) — the probe **never got a connection** (raw `t5:1960-2019`, verdict `:2019`).
- **⇒ What the F4 knob should therefore be:** the measured hazard is **not** "the probe leaks a transaction" and **not** "the probe retains a connection under starvation". It is (1) a **stranded waiter thread** under starvation (bounded by `pool_timeout=30 s`, *not* by `SET LOCAL statement_timeout` — the probe never reached a statement), and (2) an **unbounded statement duration** on a connection the probe holds (state `active`, invisible to an idle-in-txn census), fixable only by a **statement-scoped server bound** (`SET LOCAL statement_timeout`) — which the plan already sizes as `2 ×` the probe budget (`final-implementation-spec.md:95-96`) — or by an **engine-level** `statement_timeout` (which C-3/D-31 dropped, with the `SET LOCAL statement_timeout = 0` carve-out at `manager.py:8760` then becoming mandatory). Verbatim: *"**A probe-scoped `SET LOCAL statement_timeout` is therefore IRRELEVANT to this shape** — the probe never reached a statement"* (`BASELINE.md:475-476` ≡ `RESULTS.md:452-453`).

### (iii) The mission mint 4×/boot `IntegrityError` — **TRIGGER IDENTIFIED** (OQ-1 CLOSED)
Measured cases: c1 first mint → OK rung 3; c2 second mint, **same anchor, sequential** → OK **rung 1** (found the open epoch) — **no violation**; c3 different anchor → OK rung 3; **c4 8-way concurrent, same anchor, NO barrier → 7 errors, all 7 `UniqueViolation` on `ux_mission_records_anchor_open` — "the race fires NATURALLY; no barrier needed"**; **c4b 6-way concurrent with the probe→insert window forced to overlap → 5 × `IntegrityError: duplicate key value violates unique constraint "ux_mission_records_anchor_open"`**; c5 the losing mint inside a **caller-supplied** session → the caller's session is **poisoned** (`PendingRollbackError: This Session's transaction has been rolled back due to a previous exception during flush`) (`BASELINE.md:490-499` ≡ `RESULTS.md:467-476`; raw `t5:2023-2074`).
Mechanism: the sequential path is idempotent (no predicate mismatch — the probe `find_open_epoch_by_anchor` and the partial index `ux_mission_records_anchor_open` (`WHERE status='open'`, `models.py:307-313`) use the same anchor key and the same status predicate); the **only** miss is a **race**; and the loser is **not recovered** because the `IntegrityError` handler's re-resolve probe is keyed on `founding_message_id` while the **boot** mint has `founding_message_id=None` ⇒ **the re-resolve is SKIPPED and the error RE-RAISES** (`BASELINE.md:501-515` ≡ `RESULTS.md:478-492`). Three boot-lane call sites, two of which can be in flight for the same `instance_id` (`auto_continue_boot_pass.py:721`, `:1156`, `daemon/api.py:2721`) ⇒ *"This matches the incident's '4x/boot (once per actor)' exactly"* (`BASELINE.md:517-533`). **Consequence:** F6 must be an **idempotent upsert**; a re-resolve probe alone will not fix it (`BASELINE.md:535-538`).

### (iv) PG runtime semantics — what was and was NOT measured
| Question | Measured? | Evidence |
|---|---|---|
| Does `statement_timeout` leave the backend **ALIVE** (row persists in `pg_stat_activity`, state `idle`) after cancellation? | **NOT ESTABLISHED — NOT MEASURED.** The Phase-0 contract *required* it (*"confirm that a `statement_timeout` cancels the statement (QueryCanceled) but does **NOT** terminate the backend"*, `phase0-plan.md:121`/`:129`; `final-implementation-spec.md:37`; `test-strategy.md:121`) but no such test exists in the harness: the committed raw contains no `statement_timeout` experiment (raw `t1..t5` cover hold, pg_stat_activity, attribution, fingerprints, fix-sites only). The harness's `pg_sleep(5)` case cancelled **only the client-side asyncio budget**, not the server statement | absence across `raw/t1_mr3_hold.out`, `raw/t2_stat_activity.out`, `raw/t3_pool_attribution.out`, `raw/t4_fingerprints.out`, `raw/t5_fix_sites.out`; contract `phase0-plan.md:121`/`:129` |
| Does `idle_in_transaction_session_timeout` actually fire? | **YES — it fires, and it TERMINATES the backend (row GONE).** With the timeout set to `2s`: `before_wait` = `state: idle in transaction`, `last_query: 'BEGIN'`, `xact_start` set → after 2.03 s the row is **GONE** from `pg_stat_activity` (`backend_row_present_after_wait: false`, `state_after: "BACKEND GONE (terminated by server)"`) (raw `t2:121-141`); the BEGIN+SELECT variant: `terminated: true`, `elapsed_s: 2.02` (raw `t2:143-162`); summary flags `e_idle_in_txn_timeout_fired: true`, `e2_idle_in_txn_timeout_fired: true` (raw `t2:188-189`). Report form: *"**It fires as documented.** The manager engine carries **no** server-side timeout, so on the manager engine the retention is **unbounded server-side**"* (`BASELINE.md:181-198` ≡ `RESULTS.md:158-175`) | as cited |
| **SQLSTATE `25P03`** | **NOT ESTABLISHED.** The contract asked for *"threshold + SQLSTATE `25P03`"* (`final-implementation-spec.md:35`; `test-strategy.md:119`; `phase0-plan.md:120`). The measured artifacts carry the **exception class** `psycopg.errors.IdleInTransactionSessionTimeout` and the message *"terminating connection due to idle-in-transaction timeout"* (`BASELINE.md:190-192` ≡ `RESULTS.md:167-171`) — but the **SQLSTATE code itself is not printed anywhere** in the committed raw (raw `t2:121-162`), and the measurement was taken at **2 s**, not at the 60 s the plan wanted probed ("does it fire at 60 s? at 13 s?", `phase0-plan.md:128`) | as cited |
| Does `SET LOCAL statement_timeout` work inside SQLAlchemy's **autobegin**? | **NOT ESTABLISHED — NOT MEASURED.** Required by `final-implementation-spec.md:37` (*"`SET LOCAL statement_timeout` semantics inside SQLAlchemy's autobegin (confirms F4's probe-scoped bound lands on the observed pool)"*) and `test-strategy.md:121`; no artifact in the baseline measures it | as cited |

---

## 8. COPY AGREEMENT (BASELINE.md vs the canonical RESULTS file)

- **Authority:** `BASELINE.md:15-18` — *"The canonical copy (and the one the tester signed off on) is `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`"*. The RESULTS file reciprocates: it lists BASELINE.md as *"the plan's phase-0 input (same content)"* (`RESULTS.md:572-573` ≡ `BASELINE.md:595-596`).
- **Structure verified:** BASELINE.md = a **23-line header** (`BASELINE.md:1-23`) + the RESULTS body verbatim (`BASELINE.md:24` ≡ `RESULTS.md:1`; 599 − 576 = **23**). Every section was compared line-for-line in tandem: T1 table (`:79-90` ≡ `:56-67`), L4b/L6 blocks (`:99-127` ≡ `:76-104`), flip verdict (`:129-143` ≡ `:106-120`), T2 matrix/residence/timeout (`:157-198` ≡ `:134-175`), F7 invariant (`:207-231` ≡ `:184-208`), T3 inventory/attribution/asyncpg/shared-DSN (`:237-349` ≡ `:214-326`), T4 (`:353-398` ≡ `:330-375`), T5 a/b/c (`:402-538` ≡ `:379-515`), consolidated table (`:542-554` ≡ `:519-531`), not-measured table (`:556-564` ≡ `:533-541`), reproduction + artifacts (`:566-599` ≡ `:543-576`).
- **VERDICT: the two copies AGREE — zero divergences found between BASELINE.md and RESULTS.md on every number, threshold, distribution and verdict checked.** (Neither copy diverges from the other.)
- **Report-vs-RAW divergences (both copies share these; the RAW is the verbatim record — `README.md:85-89`: *"raw/*.out holds the verbatim stdout of each script at the recorded run… the committed copies are the ones the baseline report cites"*).** These are transcription drifts, not verdict changes:

| # | Report value (both copies) | Raw value | cite (report / raw) |
|---|---|---|---|
| 1 | b1 probe completed in **5.1 ms** | `timeout_at_s: 0.0058` (**5.8 ms**) | `BASELINE.md:466` ≡ `RESULTS.md:443` / raw `t5:88` |
| 2 | b2 timeout **0.5011 s** | `timeout_at_s: 0.5017` | `BASELINE.md:467` ≡ `RESULTS.md:444` / raw `t5:1027` |
| 3 | b3 timeout **0.5015 s** | `timeout_at_s: 0.5012` | `BASELINE.md:468` ≡ `RESULTS.md:445` / raw `t5:1966` |
| 4 | held-borrow **3.003 s** for requested holds "0.5 / 1.0 / 3.0 s" | `max_s: 3.002` (n=**4**, holds 0.5/1.0/**2.0**/3.0) | `BASELINE.md:178-179` ≡ `RESULTS.md:155-156` / raw `t2:104-117` |
| 5 | T2e excerpt includes `client_exception: psycopg.errors.IdleInTransactionSessionTimeout: terminating connection due to idle-in-transaction timeout` | the committed raw T2e block contains **no** `client_exception` key (the harness only sets it if the client raises inside the wait loop — `t2_stat_activity.py:184-187`) | `BASELINE.md:190-192` ≡ `RESULTS.md:167-171` / raw `t2:121-141` |

  Magnitude: ≤ 0.7 ms (cases 1–4) and one narrative line (case 5). **No verdict, threshold, distribution shape or premise delta is affected.** The measured facts behind case 5 (row gone at 2.03 s / terminated at 2.02 s) ARE in the raw.
- **Tester's second commit `2fd4480bc` ("align the c4 concurrent-mint row with the committed raw output"):** the commit is referenced by the lane's delta re-check as one of the two commits that carried the baseline in (`delta-recheck.md:390` — *"ADDENDUM 2 — the Phase-0 baseline landed mid-review (`43e577a5b`, `2fd4480bc`)"*). **What the commit changed is NOT ESTABLISHED** — this extraction lane has no git execution capability, so the pre-commit text could not be diffed. **Consistency check performed instead:** the CURRENT c4 row in both copies — *"**7 errors — all 7 are `UniqueViolation` on `ux_mission_records_anchor_open`** (the race fires NATURALLY; no barrier needed)"* (`BASELINE.md:497` ≡ `RESULTS.md:474`) — **matches the committed raw exactly** (`raw/t5:2046-2058`: `"error_count": 7`, `"unique_violation_count": 7`; the seven error strings at `:2048-2054` are all `psycopg.errors.UniqueViolation … ux_mission_records_anchor_open`). **⇒ BASELINE.md IS consistent with the committed raw output for the c4 row.**

---

## 9. NOT ESTABLISHED (with the reason)

| Item | Why |
|---|---|
| The **real** `_fetch_subshape_a_content_async` latency / content-fetch distribution | needs a booted daemon + a real checkpoint store; the harness **models** the python half — `BASELINE.md:558-560` |
| The **real** `ChildReportsService._get_last_assistant_message` python-side cost | same — `BASELINE.md:561` |
| Whether the boot's two mint sites **actually overlapped** in the incident window | trigger class measured; the scheduling in that boot is **inferred** — `BASELINE.md:562` |
| The daemon's **real 30 s `pool_timeout` stall** under real load | measured at `pool_timeout=3s` to fit the pack budget; 30 s follows from the config — `BASELINE.md:563` (raw `t1:10`) |
| **λ** (borrow/arrival rate) for `peak_occupancy ≤ λ_measured × budget` | no churn-leg census was run — required at `phase0-plan.md:101`/`:109` |
| **Noise floor** for the F7 budget's *"margin ≥ 2× the noise floor"* clause | never named or derived in the baseline — `final-implementation-spec.md:184` |
| M-R3's **13–15 s legitimate dwell** (victim-class claim) | plan-side estimate (`decisions.md:82`; `final-implementation-spec.md:184`); the baseline measured 7.1 ms … 1059.9 ms under modelled load |
| `statement_timeout` **leaves the backend alive** after cancellation | required by `phase0-plan.md:121`/`:129`, `final-implementation-spec.md:37`; **never measured** |
| `SET LOCAL statement_timeout` **inside SQLAlchemy's autobegin** | required by `final-implementation-spec.md:37`, `test-strategy.md:121`; **never measured** |
| **SQLSTATE 25P03** for `idle_in_transaction_session_timeout` | exception class + message only; the code is not printed in the raw; also measured at **2 s**, not the 60 s the contract probed (`phase0-plan.md:128`) |
| The **residence-time buckets** the contract asked for | the baseline reports distributions (n=40; held 0.5/1/2/3 s), not buckets — `phase0-plan.md:100`, `final-implementation-spec.md:30` |
| **Which four targets** the baseline's "four of the five" sentence counts | the report's framing (T5 is both the OQ-1 closer **and** the carrier of rows 3 and 4) is internally loose; §1 maps the table's 8 premise rows to targets instead |
| What commit `2fd4480bc` **changed** | no git execution in this extraction lane; current text verified consistent with the raw instead |
| `common.py` (harness helper) line-level content | not read (not required for any extracted number) |

---

## 10. DELTA-RECHECK CROSS-MAP (DR-29 … DR-36) — where each DR's baseline cite lives

| DR | Baseline content it names | cite |
|---|---|---|
| **DR-29** (F4 premise refuted) | T5b three-case table: b1 `SELECT 1` / no timeout / `checkedout()`=0 / `idle`; b2 `pg_sleep(5)` / timeout at 0.5011 s / `checkedout()`=**1** / state **active** / released t ≈ 4.52 s; b3 starvation (15 holders) / timeout at 0.5015 s / `checkedout()`=**15** (= holder count, not 16) / never got a connection | `BASELINE.md:464-468` ≡ `RESULTS.md:441-445`; *"A probe-scoped `SET LOCAL statement_timeout` is therefore IRRELEVANT to this shape"* `BASELINE.md:475-476`; census-blindness point 5 `BASELINE.md:477-480`; row 9 `BASELINE.md:554` |
| **DR-30** (flip restatement) | precondition wording *"the M-R3 hold < 2 s **iff** the awaited content fetch < 2 s"*; L5 1029.9 vs 1026.5 ms; L2 52.4 vs 47.7 ms; async worst 1059.9 ms; sync ceiling 8.0 s; real fetch unmeasured | `BASELINE.md:139-143` ≡ `RESULTS.md:116-120`; `:134-137`; raw `t1:230`, `t1:307-311`; disclosure `BASELINE.md:558-561` |
| **DR-31** (attribution channel) | conninfo beats `-c`; last `-c` wins; role default lands, startup packet beats it; asyncpg `server_settings` + `?application_name=` land; `repair_engine_reports_manager_name: true` | `BASELINE.md:276-286` ≡ `RESULTS.md:253-263`; hazard block `BASELINE.md:288-304`; raw `t3:103-147` |
| **DR-32** (11 pool sites) | *"3 pools → **11** pool-creation sites… widen the census scope"* + the T3(a) static enumeration | `BASELINE.md:239-257` ≡ `RESULTS.md:216-234`; row 6 `BASELINE.md:551`; raw `t3:27-101` |
| **DR-33** (OQ-1 closed) | re-resolve **skipped** (`founding_message_id=None`) ⇒ error **re-raises**; trigger = concurrent double-mint; c4 8-way no-barrier → **7** real `UniqueViolation`s, *"the race fires NATURALLY"*; c4b forced overlap → **5** `IntegrityError`s | `BASELINE.md:490-515` ≡ `RESULTS.md:467-492`; row 8 `BASELINE.md:553`; raw `t5:2046-2069` |
| **DR-34** (aborted hand-off refuted; raise F5) | pool path cleaned by `reset_on_return='rollback'`; journal quote = same-connection cascade; *"**raise F5's priority** (the `tasks`→`task` rename is the cascade's root)"* | `BASELINE.md:415-457` ≡ `RESULTS.md:392-434`; row 4 `BASELINE.md:549`; raw `t5:4-77` |
| **DR-35** (measured F7 invariant) | occupancy across all three states + residence vs the opening operation's hold budget; *"vacuous against two of the three shapes"*; healthy residence median 0.0005 / p95 0.0007 / max 0.0008 s over 40 cycles | `BASELINE.md:207-231` ≡ `RESULTS.md:184-208`; `:215-218`; raw `t2:86-100` |
| **DR-36** (disclosed limits) | real fetch latency + real python-side cost **not measured** (modelled); incident-window mint overlap **inferred**; `pool_timeout` stall measured at 3 s, not the 30 s default | `BASELINE.md:556-564` ≡ `RESULTS.md:533-541`; raw `t1:10`; `README.md:72-78` |

---

**Extraction complete.** No production code, plan text, or fix decisions were produced here — facts and cites only.
