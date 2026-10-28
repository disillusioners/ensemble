# Final Implementation Spec — db-pool-idle-txn lane (BINDING CONTRACT)

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`).
- **Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn`.
- **Base commit for every `file:line`:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (= `origin/latest` = staged **v0.27.11**).
- **Source of authority:** `council-report.md` (same directory) — **VERDICT: 🔴 REJECT**; plus `delta-recheck.md` — **24 delta conditions**, of which **DR-29** is a **premise rewrite** (F4). This spec is the contract the developer builds against **once the plan set is folded** (see §0).
- **Date:** 2026-10-28. **Re-based on the Phase-0 measured evidence** (fold round 2, part B).

> **RE-BASE NOTE (fold round 2, part B).** This spec is re-based on the **Phase-0 baseline** (`BASELINE.md`; canonical copy `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`; raw `.agents/tester/harness/dbpool-phase0/raw/*.out`). **Where a measurement CONTRADICTS a plan premise, the plan text CHANGED — nothing was annotated away.** The material changes: **F4's premise is refuted and F4 is rewritten** (F4-A / F4-B / F4-C) and **demoted from Phase-1 primary** (DR-29); **F8 becomes the Phase-1 primary**; the **engine-level idle-in-txn backstop ships DISABLED** and the **2.0 s residence placeholder is WITHDRAWN** (DR-35, §2); the **flip condition becomes a PRECONDITION** on the fetch latency (DR-30); **`application_name` moves to the conninfo channel** (DR-31); the **census scope widens to all 11 real pool-creation sites** (DR-32); **F6's trigger is the concurrent double-mint** and the re-resolve probe is **skipped**, not mis-keyed (DR-33); **F5 is raised to Phase 1** (DR-34); the ladder gains its **ROWS** (DR-2) and the **W-11 scanner is written down** (DR-3/DR-21); **every gate form carries the FULL Class-1 fence** (DR-16); **§5 is the ONE authoritative 46-pin inventory** with concrete pack files (DR-17, DR-28).

> **How to read this.** §0 is a gate, not a formality. §1–§9 are binding: a change not listed here is out of contract; a listed change not built is a gate failure. Every line cites a `file:line@base` so the implementer can verify drift before writing code (C4: line numbers from the incident snapshot are **stale** for `readiness.py` / `persistence.py` / `constants.py` — re-verify at your own base; drift is a plan-amendment trigger). **Where a value is cited as MEASURED, the raw artifact path + line is given; where it is MODELLED, it is labelled MODELLED.**

---

## §0 PHASE 0 — BINDING PRECONDITION (leader ruling 5) — **COMPLETE; this spec is re-based on it**

**This section was a GATE. Phase 0 is DONE** — the measured record is `BASELINE.md` (this directory) and its canonical copy `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`; the harness raw output is `.agents/tester/harness/dbpool-phase0/raw/*.out` (not shipped). **This spec is re-based on the measurements.** Where a measurement CONTRADICTS a plan premise, **the plan text changed** — the contradictions are not annotated away.

**The Phase-0 fence (unchanged, and honoured by the run that produced the baseline):**
- **NEVER** `ensemble_prod`. **NEVER** the live daemon's DB. **NEVER** a restart of anything.
- Port `5433`, throwaway role, fresh scratch DB per run, dropped at teardown.
- **Per-variable env scrub** — `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT`. `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) — an ambient `POSTGRES_DB=ensemble_prod` survives it.
- Test scripts must NEVER resolve live-shaped HOME-derived install paths (mktemp FAKE_HOME + escape guard).
- Interpreter: `/home/nea/ensemble-src/.venv/bin/python` (C-1).

**§0.1 — what the four Phase-0 outputs actually decided (raw cites; the raw WINS over any quoted summary):**

| Phase-0 output | Measured verdict | Raw cite |
|---|---|---|
| (a) M-R3 hold under load | **Flip condition NOT MET ⇒ F8 STANDS.** Worst hold over all regimes = **8011.119 ms** (the sync twin's 8.0 s bridge, n=1); worst **async** hold = **1059.877 ms** (L5, n=15) — **MODEL-INJECTED** (`await asyncio.sleep(1.0)` at raw `t1_mr3_hold.py:281-282`), **not** a measured fetch. Median hold 1029.887 ms vs await 1026.527 ms ⇒ the hold **equals** the awaited fetch latency. | raw `t1_mr3_hold.out:26-339`, `:341-346`; `t1_mr3_hold.py:281-282` |
| (b) Occupancy/residence census shape | **Three-state occupancy CONFIRMED.** A backend checked out **before its first statement** is `state='idle'` with `xact_start IS NULL` ⇒ an idle-in-txn-only census is **blind** to it (the incident's shape: 15/15 slots held, only 6 in `idle in transaction`). Healthy residence (n=40): median **0.0005 s**, p95 **0.0007 s**, max **0.0008 s**. **The old metric `state_change − xact_start` is FROZEN at the last statement's completion ⇒ a leaked backend shows the SAME sub-millisecond value as a healthy one ⇒ the budget can never fire on the leak.** The metric that works is **`xact_age_s` = `now() − xact_start`**, already collected (raw `common.py:88`). | raw `t2_stat_activity.out:6-15` (idle, xact NULL), `:41-50` (idle in transaction), `:88-98` (residence), `:17-18`, `:179-180` (blindness); `common.py:87-89` |
| (c) PG runtime semantics | `last_query` fingerprints per pool reproduced; **`options=-c` last-wins CONFIRMED**, **conninfo BEATS options**; `idle_in_transaction_session_timeout` fires as documented — the backend is **GONE** (`after_wait: null`, `state_after: "BACKEND GONE (terminated by server)"`), `terminated: true`, `elapsed_s: 2.02`. | raw `t2_stat_activity.out:137-140`, `:159-161`; `t3_pool_attribution.out:111-135`; `t4_fingerprints.out:2-56` |
| (d) Pool attribution census | **11 REAL pool-creation sites** (12 enumerated, **1** comment false-positive at `daemon/repositories/factory.py:289`) ⇒ the three named engines are a **FLOOR, not the census boundary**. Base attribution: `backends_by_application_name {"": 2}` — the pools are **NOT distinguishable** at base. | raw `t3_pool_attribution.out:27-101` (site inventory), `:2-25` (base attribution) |

**§0.2 — the two derivations, adjudicated (they REPLACE the old placeholders):**

- **(a) The manager idle-in-txn bound ⇒ ships DISABLED.** The formula's own arithmetic yields **4 × 1059.877 ms = 4.24 s**, so the **30 s floor dominates** and coincides **exactly with the legitimate `pool_timeout` wait** — enabling at 30 s risks killing a legitimate long waiter (the incident class this lane exists to fix). Server-side enforcement **kills the backend**, not just flags it (raw `t2_stat_activity.out:137-140`). ⇒ **The constant is DEFINED but NOT APPLIED.** See §2 for the enablement mechanism and the `SET LOCAL statement_timeout = 0` conditional carve-out (leader OQ-3).
- **(b) The F7 residence budget ⇒ the 2.0 s placeholder is WITHDRAWN.** 2 × the noise floor (max 0.0008 s) = 1.6 ms ⇒ the 2.0 s floor dominated the formula, and it **would false-red a healthy daemon**: the sync twin's legitimate dwell is **unconditionally ≥ 8 s** (measured 8011.119 ms) and any long-history instance's real fetch exceeds 2 s. ⇒ **Replaced by** the measured healthy band + **the operation's own hold budget** (§4.1). See also DR-11: the two derivation inputs (the real `_fetch_subshape_a_content_async` latency and the real `_get_last_assistant_message` python-side cost) are **MODELLED, not measured** — disclosed as such, never as measured.

**§0.3 — the flip condition is now a PRECONDITION, not a constant (DR-30).** The old form — *"if the M-R3 hold < 2 s"* — is a constant, but the measurement shows the hold is a **function of the awaited fetch**, i.e. **data-dependent** (a short-history instance is fast, a long-history one is slow). ⇒ The binding form is: **"the M-R3 hold < 2 s IFF the awaited content fetch < 2 s."** The **decision instrument is the FETCH-latency distribution**, not the hold. Measured base figures: async worst **1059.877 ms** (model-injected), sync-twin ceiling **8.0 s**. **The real fetch latency remains UNMEASURED** (it needs a booted daemon + a real checkpoint store) ⇒ the precondition's data-dependence is **disclosed, not resolved**. **At base the precondition is NOT MET ⇒ F8 STANDS.**

**§0.4 — the baseline's own disclosed limits (DR-36) — these carry into every honesty block in this lane:**
1. The **real** `_fetch_subshape_a_content_async` latency was **NOT measured** — the harness models the python-side half and uses a real PG checkpoint-shaped read for the PG half.
2. The **real** `ChildReportsService._get_last_assistant_message` python-side cost was **NOT measured** — a disclosed stand-in.
3. Whether the boot's **two mint sites actually overlapped** in the incident window is **INFERRED** (the trigger class is measured; the exact scheduling is not).
4. The `pool_timeout` stall was measured at **3 s** (a harness override to fit the pack budget), **not** the 30 s default.
5. **λ (the borrow arrival rate) is NOT MEASURABLE from the raw** — there is no timestamped borrow-arrival series anywhere ⇒ the `peak_occupancy ≤ λ_measured × budget` bound is **dropped or marked unmeasurable, never asserted**.

**Gate:** `BASELINE.md` exists, is reviewed, and **the two provisional literals have been replaced by measurement OR recorded DISABLED** — this is the canonical **`G0`** ladder row (§6.2a). **No implementation change lands before `G0` is green.**

---

## §0b FOLD PRECONDITIONS — the plan must be folded and re-reviewed FIRST

**Status at this revision: the plan-set fold is IN PROGRESS** (fold round 2, part B = this spec + `test-strategy.md`). The plan-of-record files (`plan-overview.md`, `decisions.md`, `phase1/2/3-plan.md`) are owned by sibling workers and must be aligned to **this spec's §5 (the 46-pin inventory) and §6.2a (the 17 ladder rows)** — those two tables are the authority they must match.

The plan set is **not implementable as written** (council verdict REJECT; 12 CRITICAL). The architect's document states *"Status of the planner's artifacts: unmodified"* (`architecture-recommendation.md:9`) — so the plan-of-record still carries the pre-amendment ACs, the ruled-DROP knob, and a test strategy that executes zero tests. **Do not start implementation against the current artifacts.**

**The fold must:**
1. Fold architect amendments **A1–A12** into the plan-of-record (the architect's file becomes an **input**, not the artifact of record).
2. Fold the **12 CRITICAL + 14 WARNING** findings from `council-report.md`.
3. Reconcile **AC14, AC15, AC16, T2.4, T2.6, D-19, D-24, G2.4, F2-f, F2-g in ONE change** (C-2) — and re-derive the affected IDs after the fold.
4. Produce **ONE** canonical gate ladder and **ONE** pack inventory (W-6), plus a machine-checkable cross-artifact consistency pin (W-11).
5. Replace the interpreter in **every** gate form (C-1, W-10) and drop `uv run`.
6. State the **Release posture** (§8) inside the plan set (C-11).
7. Be **re-reviewed** (fresh council or at minimum a delta review) before implementation begins.

**Nothing in this spec authorises implementation before that fold.** The spec tells the developer what the folded plan must say.

---


## §1 NUMBERED CHANGES

Each row: **`file:line@base` → exact change → verification.** "AC" = the acceptance criterion the folded plan must carry.

### F8 — remove the borrow-then-await from the deferred-report reconcile *(the lane's only mechanism item; A1 + C-5 + C-6)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F8.1 | `daemon/manager.py:11516` (async scope) — the `_session_scope()` opened at `:11516`, autobegun at `:11517` (`inj = session.get(ReportInjection, injection_id)`) and `:11556` (`parent_row = session.get(Instance, …)`) | **Split into P0 / P1 / P2** (see §3). No manager-engine transaction may be open across an `await`. | F8-a static pin (§5) |
| F8.2 | `daemon/manager.py:11568-11571` — `await self._fetch_subshape_a_content_async(child_instance_id=…, session=session)` | Change the helper's signature from `session=` to **`child_agent_id: str`** (it does `session.get(Instance, …)` at `:11208`/`:11260`); perform the fetch with **no** manager-engine session open (its own short-lived session, closed before P2). | F8-a, F8-b |
| F8.3 | `daemon/manager.py:11754-11757` — `await asyncio.to_thread(self._has_live_process_report_carrier, message_id=report_message_id)` | **Move the call out of the scope.** The callee opens **its own** manager-engine session (`manager.py:10773`) ⇒ while it runs, the outer scope's backend is idle-in-txn for a checkout wait bounded by `pool_timeout=30 s`. Compute `live_carrier_exists` **before** opening the scope. | F8-a (extended) |
| F8.4 | `daemon/manager.py:10860-10861` + `:11110-11112` + `:11208` + `:11216-11224` (sync twin) | Same three-phase shape. The twin is called from a **non-loop** `threading.Thread` (`manager.py:11180-11184`) ⇒ the `asyncio.run_coroutine_threadsafe(…).result(timeout=8.0)` bridge stays unchanged and **no deadlock** is introduced. The nested `self._has_live_process_report_carrier(...)` (`:11110`) must also move out of the held scope. **DR-26 — the bridge sentence (unambiguous):** the bridge's **call shape and its 8.0 s timeout are PRESERVED**; **only its POSITION changes** — it moves **OUT** of the held `_session_scope()` body. ⇒ **F8-a's `with`-body scope therefore REQUIRES the bridge to be OUTSIDE the scope**; the bridge's *presence* inside the scope is the thing F8-a REDs on. | F8-a, F8-b |
| F8.5 | `daemon/tools/instance.py:1207` (`with WriteGuardSession(Session(manager.engine), manager.write_guard) as session:`) + `:1360` (`await _bus.watch(...)`) | **Third borrow-then-await site.** Either move the bus-watch registration out of the scope, or obtain a leader ruling to exclude it — and then **disclose the residual** (it is a write path under `WriteGuardSession`). The static pin is **RED from birth** on this site otherwise. | F8-a + leader ruling (council OQ 2) |
| F8.6 | `daemon/manager.py:11529-11541` (terminal guard) and `:11555-11561` (dead-parent guard) | **Re-assert both inside the P2 write scope** (they are currently evaluated on a pre-await snapshot ⇒ a concurrent escalation slips through ⇒ **double delivery**). | F8-c (new pin) |
| F8.6a | **F8-c's injection point (DR-10)** — the seam between P0's close and P2's open | The F8-c pin's mutation must be plantable **deterministically**. The specified injection point: a **test hook** in the reconcile's P0→P2 seam that flips the `ReportInjection` row's `inj.state` from its pre-await value to a terminal value (`INJECTED` / `TASK_DELIVERED`) **after P0's scope has closed and before P2's scope opens**. **The observable: NO artefacts are created** — no message INSERT, no task INSERT, and no injection UPDATE past the guard. The hook is a **test-only** seam (the same shape as the existing instrumented-checkpointer seam used by the churn leg); it must not be reachable from production code paths. | F8-c |
| F8.7 | `daemon/manager.py:11366`/`:11383`/`:11400`/`:11421`/`:11430` (`_create_subshape_a_artifacts` reads `inj.parent_instance_id`) | The `inj` row is **detached/expired** after P0 closes ⇒ **re-read it in P2**. | F8-c |
| F8.8 | `daemon/manager.py:11281-11300` (the write path) | The message INSERT + task INSERT + injection UPDATE must still commit in **one** transaction. The change is **decision↔write**, not write↔write. | F8-d (new pin) |
| F8.9 | `daemon/write_pause_guard.py` — the `_write_guard` held across the reconcile | **No change.** It is a **counter**, not a lock (`:63-98`) ⇒ not an amplifier; it only delays a `pause_writes()` drain by up to the hold. One line in the F8 rationale so a future reader does not over-claim. | — (disclosure only) |

### F3 — repair-tool dry-run transaction hygiene *(plan D-01/D-02/D-03)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F3.1 | `daemon/tools/ens_db_tools.py:1479` | **Delete** `session.execute(text("BEGIN"))` (DDL branch). | F3-a |
| F3.2 | `daemon/tools/ens_db_tools.py:1491` | **Delete** `session.execute(text("BEGIN"))` (DML branch). | F3-a |
| F3.3 | `daemon/tools/ens_db_tools.py:1475-1513` | Move **each** branch's rollback into a `finally:` so the rollback runs on SQL-success, SQL-error **and rollback-error** alike. The uncovered path today is a failure *of* the rollback itself (`:1489`), which reaches the outer `except:` at `:1513` with **no** rollback. Keep the DML row-count projection (`_rows_to_dicts(result)`) inside the try, before the rollback. | F3-a, F3-b, F3-c, F3-d |
| F3.4 | `daemon/tools/ens_db_tools.py:1482`, `:1496`, `:963`, `:1407`, `:1076-1077` | Update the agent-visible preview strings and docstrings in the **same change** (the dry-run wording is part of the tool's contract). **Leave** the module docstring's audit-contract wording at `:37` and `:942` unchanged — it describes the *committed* path's `BEGIN/COMMIT`, not the dry-run. | F3-a (asserts the new wording) |
| F3.5 | `daemon/tools/ens_db_tools.py:1530-1538` | **No change** — the dry-run's `previewed` audit row is written in a separate session and must not be rolled back by the dry-run transaction. | F3-d |

### F4 — readiness-probe bounds *(plan D-04/D-05/D-06/D-07; A4-v; C-7; A-7; **REWRITTEN by DR-29**)*

> **DR-29 — F4's PREMISE WAS REFUTED BY MEASUREMENT. The premise below is the OLD one; it must not be built.** The Phase-0 baseline measured the readiness probe's timeout path and found that under **pool starvation** — the incident's own shape — the abandoned probe **never acquires a connection**. The old rationale ("the abandoned probe worker cannot hold a connection open indefinitely") is therefore **false**, and a probe-scoped `SET LOCAL statement_timeout` is **irrelevant** to the starvation shape. **F4 is DEMOTED from "Phase-1 primary" to "defence-in-depth".** The **Phase-1 primary becomes F8**; the starvation shape is addressed by **F8 + F2** (pool occupancy / waiter arguments), not by a statement bound.
>
> **Measured, with raw cites** (raw = `.agents/tester/harness/dbpool-phase0/raw/t5_fix_sites.out`; the canonical copy of the measured record is `.agents/tester/RESULTS/2026-10-28-dbpool-phase0-baseline.md`, mirrored into this directory as `BASELINE.md`):
>
> | case | budget | timeout fired | `checkedout()` right after the timeout | backend `pg_state` | raw cite |
> |---|---|---|---|---|---|
> | **b1** `SELECT 1` | 0.5 s | **no** — completed in **5.8 ms** (`timeout_at_s: 0.0058`) | **0** | `idle` | `t5_fix_sites.out:80-88` (b1 block), `:1014-1020` |
> | **b2** `SELECT pg_sleep(5)` | 0.5 s | **yes at 0.5017 s** | **1** | **`active`** (NOT `idle in transaction`) | `t5_fix_sites.out:1021-1027`; `active` through `t_s 4.4747`, first `checkedout 0`/`idle` at `t_s 4.5275` ⇒ release bracketed `(4.4747, 4.5275] s` — `t5_fix_sites.out:1540-1556` |
> | **b3** **pool starvation** (15 holders) | 0.5 s | **yes at 0.5012 s** | **15** — **exactly the holder count, not 16** | n/a — **never got a connection** | `t5_fix_sites.out:1960-1967`; verdict line `:2019` |
>
> **⇒ the incident shape is (i) a STRANDED POOL-WAITER THREAD, not a retained connection, and (ii) an unbounded STATEMENT on a connection the probe *does* hold (b2), whose backend is `active` and therefore invisible to an idle-in-txn census.**

**F4-A — NEW: bound the probe's CONNECTION-ACQUISITION wait (the incident's shape).**

- **Where:** the probe bodies — `daemon/services/readiness.py:1476-1481` (`make_db_probe`) and `:1746-1789` (`make_queue_probe`) — reached through `daemon/services/readiness.py:1281-1283` (`await asyncio.wait_for(asyncio.to_thread(probe), timeout=timeout_s)`).
- **The defect (from the code):** `asyncio.wait_for` cancels the **await**, not the **thread** — `asyncio.to_thread` submits to the default `ThreadPoolExecutor` and a running thread cannot be cancelled. The probe thread therefore **outlives the budget by design** and stays blocked inside `engine.connect()`'s pool checkout, whose only bound is the engine's `pool_timeout` (**30 s**, the SQLAlchemy library default at base — `factory.py:323-338` sets no `pool_timeout`). Measured b3 is exactly this: the probe was a **waiter**, and `checkedout()` stayed at the holder count.
- **Mechanism specified (engine-neutral, primary):** a **probe-scoped acquisition ADMISSION PRE-CHECK** at the top of each probe body, immediately **before** `engine.connect()`: read the pool's public occupancy accessors (`engine.pool.checkedout()`, `engine.pool.size()`) against the pool's configured capacity (`size` + the lane-pinned `max_overflow`), and when the pool is **at capacity** the probe **does not enter the blocking checkout at all** — it raises `TimeoutError` **synchronously**, which `readiness.py:1284-1285` (`except TimeoutError`) maps to `FAILURE_CLASS_PROBE_TIMEOUT` ⇒ **grace-eligibility is preserved** (the same class the client's own `wait_for` timeout produces) and **no verdict-contract change** results (F4.6 holds). Because the refusal happens **before** the checkout, **no thread is stranded** and the default-executor ceiling (A7) is not consumed.
- **Residual (disclosed, not closed):** the pre-check is a **pre-check** — a checkout that loses the race between the pre-check and `engine.connect()` can still strand the thread for up to `pool_timeout`.
- **The engine-level knob is NOT lowered — and why.** A **probe-scoped** acquisition timeout **cannot** be expressed through SQLAlchemy's public API: the checkout wait is `QueuePool._do_get()` → `self._pool.get(wait, self._timeout)`, where `self._timeout` is the **pool-level** `pool_timeout`; `Engine.connect()` accepts **no** timeout parameter (verified against the installed SQLAlchemy **2.0.48**). The **only** knob that bounds a checkout is therefore **engine-level** `pool_timeout`. Lowering it to the probe budget (0.5 s) would convert ordinary manager-pool contention into `TimeoutError` **for every daemon caller** — the D-15 hazard ("lowering it converts load into 500 s"). ⇒ **The manager engine's `pool_timeout` stays 30 s** (F2.4, unchanged) and **F4-A ships as the admission pre-check**, with the race-window residual bounded by 30 s and recorded in the honesty block (F4.9).

**F4-B — RETAINED, DEMOTED to defence-in-depth: the probe's STATEMENT duration is server-bounded *when the probe holds a connection*.**

- Probe-scoped `SET LOCAL statement_timeout`, derived **2 ×** the client budget, **PG-only**, house idiom `daemon/tools/ens_db_tools.py:787-797`.
- **State plainly what this does and does not do (the measured semantics):** the backend is **`active`**, **not** `idle in transaction` ⇒ the effect is **statement cancellation (`57014` / `QueryCanceled`)**, **NOT** a census-visible idle-in-txn change. **The backend row is EXPECTED TO PERSIST** in `pg_stat_activity` with `state = 'idle'` after the statement is cancelled. The `"row gone from pg_stat_activity"` signature belongs to the **engine-level `idle_in_transaction_session_timeout`** — measured: `after_wait: null`, `state_after: "BACKEND GONE (terminated by server)"` (raw `t2_stat_activity.out:137-140`) and `terminated: true`, `elapsed_s: 2.02` (`:159-161`) — a mechanism **F4 does NOT install** and this lane does not enable (see §2 and DR-29's engine-level backstop disposition).

**F4-C — NEW: the missing starvation-shaped PG leg.** Under 15 holders, assert the probe holds **NO** connection, `engine.pool.checkedout()` **equals the holder count**, and the stranded thread terminates **within a bounded window**. See the F4-f pin.

**Disposition of the F4-keyed pins (each one):** **F4-a** (static) re-points to the new mechanism (it must assert **both** the F4-A admission pre-check and the F4-B statement bound). **F4-b** (the synthetic held-lock PG leg) is **RETAINED** but re-scoped as the **F4-B statement-case** leg — and it is **SYNTHETIC**: a deliberately held lock is **not** the incident's shape and does not cover it. **F4-c**, **F4-d**, **F4-e** re-point; **F4-e** still covers the `readiness.py:107-113` docstring correction (per DR-25). **F4-f is NEW** — the F4-C starvation leg. **The DR-29(b) option "add the engine-level backstop" is REJECTED** — see §2's backstop disposition.

| # | Where | Exact change | Verify |
|---|---|---|---|
| F4.1 | `daemon/services/readiness.py:1476-1481` (`make_db_probe` body) | **F4-A — the admission pre-check.** Before `with engine.connect() as conn:`, compute the pool's free capacity from `engine.pool.checkedout()` / `engine.pool.size()` (+ the lane-pinned `max_overflow`); if the pool is **at capacity**, raise `TimeoutError` **without entering the checkout**. PG/engine-agnostic (no statement is issued ⇒ no dialect guard needed). | F4-a (static), F4-f (PG) |
| F4.2 | `daemon/services/readiness.py:1746-1789` (`make_queue_probe` body) | Same admission pre-check as F4.1. | F4-a (static), F4-f (PG) |
| F4.3 | `daemon/services/readiness.py:1476-1481`, `:1746-1789` | **F4-B — the probe-scoped statement bound.** Inside the probe's own connection scope, issue `SET LOCAL statement_timeout = <ms>` as the **FIRST** statement (it opens the autobegun transaction that scopes the setting). **PG-only** — dialect-guarded exactly as `daemon/tools/ens_db_tools.py:793`. The value is **derived** (never a literal): `2 × DB_PROBE_TIMEOUT_S` (`readiness.py:113`, 0.5 s ⇒ 1000 ms) and `2 × QUEUE_PROBE_TIMEOUT_S` (`readiness.py:127`, 2.0 s ⇒ 4000 ms). Derive the millisecond value in **ONE** place so the two probes cannot drift. | F4-a, F4-c |
| F4.4 | `daemon/services/readiness.py:1476-1481`, `:1746-1789` | Issue an explicit `Connection.rollback()` after the probe's reads, before the connection is returned (SQLAlchemy 2.0.48: a documented no-op when no transaction is active). | F4-a |
| F4.5 | `daemon/services/readiness.py:107-113` | **Correct the docstring.** Its premise — *"the driver's own connect/execute timeout releases it — bounded leakage"* — is **false at base**: the manager engine sets **no** execute timeout (`factory.py:323-338`; the URL's `connect_timeout` bounds the *connect* phase only). **Phase 1's DO-NOT-TOUCH list must be amended to permit this** (it currently says "the two probe bodies only"). **The corrected docstring must state the MEASURED semantics** (F4-B): the backend is `active`, the statement is cancelled with `57014`, the row persists. | **F4-e, F7-b-static (fact 4)** + diff review (DR-25) |
| F4.6 | `daemon/services/readiness.py` — the **saver probe** | **STAY EXCLUDED.** The P2 psycopg pool is `autocommit=True` (`daemon/persistence.py:328`) ⇒ every statement is its own transaction ⇒ `SET LOCAL` would be a no-op with a PG warning. **The saver probe is also OUT of F4-A's pre-check** (it does not use `engine.connect()`; it reads through the saver path). | F4-a (asserts exclusion) |
| F4.7 | `daemon/services/readiness.py:1263-1296` (`_guarded`), `:1279-1281` | **NO CHANGE.** The 2× bound keeps the **client-side** `asyncio.wait_for` firing first ⇒ the cancelled `to_thread` future's later `QueryCanceled` is never observed ⇒ the class stays `probe_timeout` ⇒ **grace-eligibility preserved**. At ≤ 1× the server error would arrive as an exception ⇒ `FAILURE_CLASS_HARD_COMPONENT_RED` ⇒ **not** grace-eligible ⇒ a behaviour change. **F4-A's synchronous `TimeoutError` also lands on `except TimeoutError` (`:1284-1285`) ⇒ the same `probe_timeout` class ⇒ no verdict-contract change.** The **relationship** (value = 2 × the constant) is what must be pinned, not the number. | F4-a, F4-c |
| F4.8 | `phase1-plan.md` D-04 outcome + T1.7 | **Restate the guarantee (DR-29):** F4-B bounds the **statement** duration **when the probe holds a connection**; F4-A bounds the **acquisition** wait by refusing to enter a saturated pool. The **checkout** wait, where the pre-check loses its race, remains bounded by `pool_timeout=30 s` (D-15) and the default-executor ceiling (A7). T1.7 must assert **connection return** (`engine.pool.checkedout()` back to baseline; the abandoned thread terminated) — **not** "no idle-in-txn backend" and **not** "the row is gone from `pg_stat_activity`" (the row-gone signature belongs to the engine-level `idle_in_transaction_session_timeout`, which this lane does not enable). | **F4-b (corrected)** |
| F4.9 | `daemon/constants.py:207-217` + `daemon/persistence.py:306-315` (honesty blocks, with F2.7) | The honesty blocks must additionally record: (i) the **engine-level backstop ships DEFINED BUT NOT APPLIED** (see §2) and the log line that says so; (ii) **F4-A's residual** — a checkout that loses the pre-check race may still strand the probe thread for up to `pool_timeout=30 s`. | F2-i, F7-b-static |

### F6 — idempotent mission-record boot mint *(plan D-08/D-09/D-10; A11; W-4; A-6)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F6.1 | `daemon/repositories/mission_record/repository.py:498-514` (`insert_mission`, own-session path) | Replace the ORM insert with a dialect-appropriate insert (`pg_insert` / `sqlite_insert` — house helper shape at `daemon/repositories/source/repository.py:33-46`) carrying `.on_conflict_do_nothing(index_elements=["anchor_instance_id"], index_where=text(OPEN_EPOCH_STATUS_SQL))`. On `rowcount == 0`, re-resolve the winning row via the **anchor-scoped** probe (`find_open_epoch_by_anchor`, `:69-93`) and return it. | F6-a, F6-b |
| F6.2 | `daemon/repositories/mission_record/models.py:209` (`OPEN_EPOCH_STATUS_SQL`) | **Reuse, do not redefine.** The conflict target must be **byte-identical** to the existing partial unique index `ux_mission_records_anchor_open` (`:307-313`, `postgresql_where=text("status = 'open'")`, mirrored at `daemon/manager.py:8548-8552` and `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`). | F6-a |
| F6.3 | `daemon/services/mission_record_service.py:544-567` (`_mint_epoch_locked`'s IntegrityError branch) | The boot path's re-resolve becomes the **anchor-scoped** probe. The code's own comment (`:558-565`) records that the current `founding_message_id`-keyed probe "can NEVER be found by that probe" for a NULL-founding boot epoch ⇒ the boot path has **no** re-resolve path today. **DR-33 — the measured mechanism is a SKIP, not a mis-key:** the boot path's re-resolve probe is **skipped** because `founding_message_id` is `None` (guard at `daemon/services/mission_record_service.py:561`, raise at `:567`) and the error **re-raises**. The measured trigger is a **CONCURRENT DOUBLE-MINT for the same anchor** — the harness's 8-way concurrent mint, **no barrier**, produced **7 real `UniqueViolation`s** on `ux_mission_records_anchor_open` (raw `t5_fix_sites.out:2040-2053`); the forced-overlap leg produced **5** (`:2054-2067`). ⇒ **D-09's "mis-keyed probe" framing is RETRACTED**; the correct framing is "the probe does not run on the boot path, and the error re-raises". | F6-a, F6-b |
| F6.4 | `daemon/services/mission_record_service.py` — the `session is not None` branch | **UNCHANGED** (D-10): the caller-session path keeps its documented "poison the caller's txn and re-raise" semantics. Measured: `caller_session_usable_after: false`, `caller_session_error: PendingRollbackError` (raw `t5_fix_sites.out:2068-2074`). | F6-a |
| F6.5 | `daemon/repositories/mission_record/repository.py:498-514` — the **target DB's** index | **Add a read-only assertion** that the target DB **has** `ux_mission_records_anchor_open` and that its predicate is **byte-identical** to `OPEN_EPOCH_STATUS_SQL`. If the index is absent (its absence is exactly OQ-1's duplicate-OPEN data shape) the boot mint would raise **42P10** instead of the IntegrityError being repaired. Add a "no index" negative leg asserting the failure is **loud**. Record a read-only prod index-presence SELECT in the gate report (no data remediation — leader-ruled out of scope). | F6-c (PG), **F6-c-static** (DR-24) |
| F6.6 | The lane's schema surface | **ZERO new `.sql` files.** Add a companion assertion that the **ORM diff introduces no new TABLE/COLUMN/INDEX** — a `.sql` file-count check alone would not catch an ORM-only table (A-6). | F6-d (process) |
| F6.7 | `daemon/repositories/mission_record/repository.py:498-514` (the upsert) | **CONCURRENCY IDEMPOTENCY (DR-33).** The upsert must be idempotent **under concurrent mint for the same anchor**, not merely under sequential re-mint. Requirement: on `rowcount == 0` the winner row is re-resolved **by the anchor-scoped probe** (`find_open_epoch_by_anchor`, `:69-93`) — **the anchor-scoped re-resolve is reached on the `rowcount == 0` path**, and it is the path the concurrent race takes. The sequential path is already idempotent (measured c2: `ok: true`, `rung: 1`, **no** violation) ⇒ the leg that must go GREEN is the **concurrent** one. | F6-b (PG, concurrent leg), F6-a |
| F6.8 | `daemon/repositories/mission_record/models.py:209` (`OPEN_EPOCH_STATUS_SQL`) | **F6-e's mutation subject (DR-18).** The predicate must remain **byte-identical** to the partial unique index's predicate (`models.py:305-313`, `postgresql_where=text("status = 'open'")`; mirrored at `daemon/manager.py:8548-8552` and `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`). The concrete F6-subject mutation is: **change `status = 'open'` to `status = "OPEN"` at `models.py:209`** ⇒ the conflict-target predicate no longer byte-matches the partial index ⇒ the upsert stops being idempotent ⇒ RED. | F6-e |

### F2 — manager-engine pool hardening + per-engine attribution *(D-13…D-22; A5; A6; C-2; C-3; C-8; C-9)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F2.1 | `daemon/constants.py` | Add the canonical knob constants (**values** live here — D-18): `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`, `MANAGER_ENGINE_POOL_RECYCLE_S = 3_600`, `MANAGER_ENGINE_POOL_TIMEOUT_S = 30`, and `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S` (the **measured** budget, C-4). Each carries its precedent citation. | F2-a (knob matrix) |
| F2.2 | `daemon/repositories/factory.py:252` | **DO NOT EXTEND `PG_SESSION_CONNECT_ARGS`.** It is embedded **last** into the repair engine's options string (`daemon/tools/ens_db_tools.py:619-627`) ⇒ PG's **last `-c` wins** ⇒ extending it would silently retune the repair engine's `statement_timeout` 60 s → 30 s (D-17). Already fenced by `tests/unit/repositories/test_pg_engine_utc_session.py:65` — keep that assertion green. | F2-f (repair-engine pin) |
| F2.3 | `daemon/repositories/factory.py:252` (new constant) | Add `MANAGER_PG_SESSION_CONNECT_ARGS` assembled in `factory.py` from the `constants.py` values. **The `application_name` goes in the engine's OWN conninfo/URL query dict — NOT in the `-c options` string (DR-31).** *Measured channel comparison:* `options=-c application_name` lands, but within one options string **last `-c` wins** (raw `t3_pool_attribution.out:121-125`), and the **conninfo parameter BEATS the options form** (`:127-135` — `both → "from-conninfo"`). The ordering hazard is real and measured: with the shared constant appended last, the **repair engine reports the manager's name** — `repair_engine_reports_manager_name: true` (raw `t3_pool_attribution.out:111-119`). ⇒ Putting `application_name` in each engine's own conninfo makes the attribution **override-proof** rather than merely last-wins-correct. **The shared `PG_SESSION_CONNECT_ARGS` carries NO `application_name` and NO `idle_in_transaction_session_timeout`.** The **rendered** manager options string is a TEMPLATE: `-c timezone=UTC` (+ `-c idle_in_transaction_session_timeout=<MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS>` **only when the backstop is enabled** — see §2: it ships **DISABLED**). **NO `statement_timeout`** — DROPPED per the leader ruling (C-3): `manager.py:8760-8762` runs the whole `_ensure_postgres_columns` list in ONE transaction, documented as *"FULL TABLE REWRITE with ACCESS EXCLUSIVE lock… may take seconds"* (`:7877-7879`), fail-loud (`:7554-7560`) ⇒ a 30 s cap there means **the daemon does not boot** on a steady-state no-op path. **CONDITIONAL carve-out (leader OQ-3):** if the engine-level backstop is EVER enabled, a `SET LOCAL statement_timeout = 0` inside `manager.py:8760` becomes **MANDATORY** — with no engine-wide `statement_timeout` applied today, that carve-out is **NOT** required. | F2-a, F2-e, **F2-g** (channel) |
| F2.4 | `daemon/repositories/factory.py:323-338` (`create_engine`) | `connect_args=dict(MANAGER_PG_SESSION_CONNECT_ARGS)`; **add** `pool_reset_on_return="rollback"`, `pool_recycle=3600`, `pool_timeout=30`. **Leave** `pool_size=5`, `max_overflow=10`, `pool_pre_ping=True` untouched (C-4 — the soak lane's sizing is on the P2 pools, not this one). `pool_use_lifo` deliberately **not** set. **`pool_timeout=30` is deliberately NOT lowered** (F4-A's safety argument: lowering it converts ordinary manager-pool contention into `TimeoutError` for every daemon caller — D-15). | F2-a, F2-b, F2-c, F2-d |
| F2.5 | `daemon/tools/ens_db_tools.py:603-633` (repair engine) | The **only** knob change is `pool_reset_on_return="rollback"`. Its **`application_name=ensemble-repair` goes in the REPAIR ENGINE'S OWN conninfo** (DR-31), **not** appended to its options string — the shared constant is interpolated **last** into that options string, so an `application_name` placed there would be overridden (measured: `repair_engine_reports_manager_name: true`, raw `t3_pool_attribution.out:117`). Every other knob (`pool_size=2`, `max_overflow=3`, `pool_pre_ping=True`, `pool_recycle=3600`, `pool_timeout=10`) and the rest of the options string (`statement_timeout=60000`, `lock_timeout=10000`, `idle_in_transaction_session_timeout=60000`, `timezone=UTC`) stay **byte-identical**. | F2-f, **F2-g** (channel) |
| F2.6 | `daemon/persistence.py:355-359` (asyncpg checkpoint-adapter pool) | **Third engine — the one A5 missed.** It opens an **explicit** transaction (`daemon/checkpoint_adapter.py:1229-1239`: `async with conn.transaction():` with three `await`s between DELETEs) ⇒ its backend sits `idle in transaction` **between statements**, with **no** server-side reap (measured: the asyncpg explicit transaction lands `idle in transaction` with the cluster default `0` = disabled — raw `t3_pool_attribution.out:149-161`). **Mechanism (DR-9, one implementation line):** build the asyncpg DSN from the same config **without reusing the shared `conn_string` variable**, and pass `server_settings={"application_name": "ensemble-checkpoint-adapter"}` to `asyncpg.create_pool` (measured: asyncpg `server_settings` lands — raw `t3_pool_attribution.out:143-147`). **DO NOT touch the shared `conn_string`** (`persistence.py:225`) — it also feeds the P2 psycopg **oracle** (`:317`), and touching it would break the P2-immutability pin (T2.7/AC18) and put the C1-protected oracle into the lane's diff. | F2-g (new), F2-h (new) |
| F2.7 | `daemon/constants.py:207-217` and `daemon/persistence.py:306-315` (honesty blocks) | Refresh both to state: **retention / transaction-hygiene class REPAIRED** by this lane (branch + date); **DWELL / OCCUPANCY class NOT REPAIRED** — residual owner = the event-loop sync-DB follow-ups (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) and the BFS depth loop (`daemon/repositories/instance/repository.py:1434-1645`). **Do not** say "the P1 idle-in-transaction leak is REPAIRED" — P1 has occupancy under dwell, not a leak. **The backstop's APPLIED / NOT-APPLIED status must also be stated (DR-8)** — the engine-level idle-in-txn backstop **ships DEFINED BUT NOT APPLIED** (see §2) unless `BASELINE.md` records a trustworthy measurement; while disabled the daemon logs `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no Phase-0 measurement` at boot. **Knob values in these blocks must not move.** | F2-i (new) |
| F2.8 | `tests/unit/repositories/test_pg_engine_utc_session.py:86-88` | **Explicit pin amendment** (C-9, **D-37**): the exact-equality assertion on the manager engine's `connect_args` must be amended to the new string, value-by-value, **order-pinned**. **Keep `:65` untouched** — it is the D-17 guard. The file is **not** in `SEALED_SHA_BASELINES` ⇒ **no seal re-pin ceremony**. Correct F2-e's stale `:292` anchor → `:86-88`. G2.4 becomes "amended per **D-37**, diff attached to the gate report". **DR-20 — the amended assertion asserts the STRUCTURE, never the literal `60000`:** the `idle_in_transaction_session_timeout` clause is **present**, its value **equals `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` as imported from `daemon/constants.py`**, and the option **ORDER** is pinned. | F2-a, F2-e, F2-f |
| F2.9 | `daemon/repositories/factory.py:215-222` (latent config-driven PG branch) | **NOT hardened** (D-22) — zero daemon-runtime PG callers, deliberately config-driven. **DR-28 — this row now has a pin:** the knob-matrix pin asserts that this branch carries **none** of the lane's knobs (no `pool_reset_on_return`, no `pool_recycle`, no `pool_timeout`, no `application_name`) ⇒ a future edit that silently hardens it REDs. | **F2-a** (extended) |
| F2.10 | `daemon/repositories/factory.py:184-199` (SQLite), `daemon/__main__.py` (boot preflight), `daemon/persistence.py:316-331` (P2 oracle), `daemon/tools/ens_db_tools.py:649-695` (vacuum NullPool), `daemon/auth/cli.py:152-160` (one-shot CLI) | **NOT touched** — except the honesty comment in `persistence.py:306-315` (F2.7). | F2-j (P2-immutability pin) |

### F5 — wedge-capture table name *(D-20; **RAISED TO PHASE 1** by DR-34)*

> **DR-34 — F5 is RAISED to Phase 1.** The incident's *"aborted connection handed back out"* premise is **REFUTED for the pool path** — `pool_reset_on_return='rollback'` cleans it (measured: `pool_reset_on_return: "ResetStyle.reset_rollback"`, next borrower OK — raw `t5_fix_sites.out:127-128`). The journal quote the incident captured is a **SAME-CONNECTION CASCADE** in wedge-capture, whose **ROOT is the wrong table name**: step 1 fails `UndefinedTable: relation "tasks" does not exist`, steps 2-3 fail `InFailedSqlTransaction`, and the cascade reproduces the incident's journal quote exactly (raw `t5_fix_sites.out:128-130`). ⇒ The cascade's root is `FROM tasks` ⇒ **F5 is a Phase-1 item**, not Phase-2.

| # | Where | Exact change | Verify |
|---|---|---|---|
| F5.1 | `daemon/services/wedge_capture/db_capture.py:308` | `FROM tasks t` → `FROM task t`. | F5-b, F5-c |
| F5.2 | `daemon/services/wedge_capture/db_capture.py:387` | `FROM tasks` → `FROM task`. | F5-b, F5-c |
| F5.3 | `daemon/services/wedge_capture/db_capture.py:34` (docstring) | "``tasks`` rows" → "``task`` rows". **No other change** to the module. The path rides a one-shot `psycopg.connect` (`:196-200`) with `close()` in `finally` (`:289-294`) ⇒ **no pool**, so it cannot leak. **F5-d (the store-census keep-green pin) is added to this cell's Verify set (DR-22)** — the `pg_class`/`pg_namespace` census query (`:351-363`) is a **separate** query from the two fixed ones and must keep working. | F5-b, **F5-d** |

### F1 — the audit record *(D-11)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F1.1 | `.agents/shared/planning/db-pool-idle-txn/audit-findings.md` (new) | Publish the census headline as the **AST baseline** — **not** the retired "≈689" aggregate: family counts **A=613 / C=4 / B=175 / D=148 / E=14 / F=0**, method cited to `audit-method.md` by filename. **Cite the scanner-independent total (`779`) as the canonical figure and mark the aggregate (≈689) NON-CANONICAL** — `audit-method.md` states the aggregate is not scanner-independent (DR-6). Record RCA sites **B** and **C** as audited-and-clean with their premise corrections. Label the five-backend reconciliation as **inference**. State that **no leak class was found where the census found none**. | F1-a (doc existence) |

### F7 — the regression guard *(A2, A3, A7, A10; C-10, C-12; W-1, W-13)*

See **§4** for the invariant, the attribution method and the collected-count assertions; **§5** for the pin inventory.

### Fold-only items (no code)

| # | Where | Exact change |
|---|---|---|
| A4-v / W-12 | `daemon/services/readiness.py:107-113` | Docstring correction (F4.4) — **and** the Phase-1 permitted set must be amended to allow it. |
| A7 | `plan-overview.md` risk register | Add the **default-executor ceiling** disclosure: the true ceiling on abandoned probes is the default `ThreadPoolExecutor` (`asyncio.to_thread` ⇒ `min(32, cpu+4)` = 12 workers on this 8-core host), **not** the PG pool; with 1351 `to_thread`/`run_in_executor` sites, pinning executor workers is a daemon-wide latency amplifier. **Do not** "fix" it with a dedicated probe executor in this lane (adjacent to the C1 relocation argument). |
| A9 | `research-pool-couplings.md` §3 | Saver-pool "max 5" → `checkpoint_pool_max_size` (default 10, clamp [5,20]). |
| A9 | architect §1.3 / plan §3.1 | Leak conjunction `:734-743` → `:739-743`; record the repair-engine dating (A-2) in the premise ledger. |

---

## §2 EXACT KNOB VALUES AND WHERE THEY LIVE

**Canonical home = `daemon/constants.py`** (D-18; precedent: `factory.py:14` already imports `BOOT_DB_TIMEOUT_S` from it). **Assembly stays in `factory.py`** (a rendering detail).

### Manager engine — `daemon/repositories/factory.py:323-338`

| Knob | At base `19fc11c8b` | After | Change |
|---|---|---|---|
| `pool_size` | `5` | `5` | — (C-4: do not regress) |
| `max_overflow` | `10` | `10` | — |
| `pool_pre_ping` | `True` | `True` | — |
| `pool_reset_on_return` | *absent* (library default `'rollback'`) | `"rollback"` | **ADDED, explicit** |
| `pool_recycle` | *absent* | `3600` | **ADDED** (house precedent `REPAIR_POOL_RECYCLE_S`, `ens_db_tools.py:182`) |
| `pool_timeout` | *absent* (library default 30) | `30` | **ADDED, explicit** — deliberately pinned at the default; lowering it converts load into 500 s (D-15) |
| `pool_use_lifo` | *absent* | *absent* | — (deliberate) |
| `connect_args` | `{"options": "-c timezone=UTC"}` | `{"options": "-c timezone=UTC"}` **+ the manager engine's OWN conninfo `application_name=ensemble-manager`** (DR-31: the conninfo channel BEATS the `-c options` form — measured raw `t3_pool_attribution.out:127-135`) **+ the `idle_in_transaction_session_timeout` clause ONLY when the backstop is ENABLED** (it ships DISABLED — see below) | **The shared `PG_SESSION_CONNECT_ARGS` stays byte-identical.** The `application_name` lives in the engine's own URL query dict, **not** in the shared options string. |
| URL `connect_timeout` | `BOOT_DB_TIMEOUT_S` (10) | unchanged | — (bounds the **connect** phase only, not the pool checkout — F4.4's docstring correction) |

### Constants to add in `daemon/constants.py`

| Constant | Value | Note |
|---|---|---|
| `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` | **DEFINED, NOT APPLIED** — the engine-level backstop **ships DISABLED**. | **DR-29 / DR-7.** The Phase-0 measurement does **not** support enabling it: the formula's own arithmetic yields 4 × 1059.877 ms = **4.24 s** (model-injected input), so the **30 s floor dominates** and **coincides exactly with the legitimate `pool_timeout` wait** — enabling at 30 s risks killing a legitimate long waiter. Server-side enforcement **kills the backend**, not just flags it (raw `t2_stat_activity.out:137-140`: `after_wait: null`, `state_after: "BACKEND GONE (terminated by server)"`). **The DR-29(b) option "add the engine-level backstop" is REJECTED for this reason.** |
| `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S` | **NOT DERIVABLE from the Phase-0 evidence** — the quantity it consumes (the real fetch latency) is **model-derived for the dominant regimes** and **unidentified for the real path**. | See §3.4. It is the *per-operation hold budget* F7's residence invariant is compared against — **not** a pool-wide reap threshold. |
| `MANAGER_ENGINE_POOL_RECYCLE_S` | `3_600` | — |
| `MANAGER_ENGINE_POOL_TIMEOUT_S` | `30` | — **deliberately NOT lowered** (D-15 / F4-A's safety argument). |
| `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S` | **The CONSTANT REMAINS** (it is the guard's default per-call-site hold budget); **its 2.0 s VALUE is WITHDRAWN.** The comparison is against **the operation's own hold budget** — this constant as the default for sites that declare no tighter budget — with the **measured healthy band** (`median 0.0005 s` / `p95 0.0007 s` / `max 0.0008 s`, n=40 — raw `t2_stat_activity.out:88-95`) as the reference point. | **DR-35.** The old **2.0 s placeholder is WITHDRAWN**: 2 × the noise floor = 1.6 ms ⇒ the 2.0 s floor dominated the formula, and 2.0 s **would false-red a healthy daemon** — the sync twin's legitimate dwell is **unconditionally ≥ 8 s** (measured 8011.119 ms) and any long-history instance's real fetch exceeds 2 s. The metric is also **changed** from `state_change − xact_start` (frozen at the last statement ⇒ a leak shows the same sub-millisecond value as a healthy one ⇒ the budget can never fire on the leak) to **`xact_age_s` = `now() − xact_start`** (already collected — raw `common.py:88`). See §4.1. |

### The engine-level backstop — **DEFINED BUT NOT APPLIED**, and how it would be enabled (DR-7, DR-8, DR-29)

- **Shipped state:** the clause is **omitted** from the manager engine's rendered options string. At boot the daemon logs `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no Phase-0 measurement`. The honesty blocks (F2.7) state the same.
- **Enablement mechanism (one line):** the clause is applied **iff `BASELINE.md` records a TRUSTWORTHY measurement** of the manager engine's legitimate hold. "Trustworthy" = measured on the **real** fetch path (a booted daemon + a real checkpoint store), **not** modelled, and **not** a single-sample failure shape. When enabled, the constant is set from that measurement and the rendered options string gains `-c idle_in_transaction_session_timeout=<the constant>`.
- **CONDITIONAL carve-out (leader OQ-3):** because **no engine-wide `statement_timeout` is applied**, the `SET LOCAL statement_timeout = 0` carve-out at `daemon/manager.py:8760` is **NOT required** today. **If the backstop is ever enabled, that carve-out becomes MANDATORY** — the boot DDL path (`manager.py:8760-8762`, `:7877-7879`) is a documented FULL TABLE REWRITE under an ACCESS EXCLUSIVE lock and must not be reaped.
- **The rejected alternative (recorded):** DR-29(b)'s "add the engine-level backstop" option — rejected on the P-3 grounds above (the 30 s floor dominates and coincides with the legitimate `pool_timeout` wait; the server TERMINATES the backend).

### Repair engine — `daemon/tools/ens_db_tools.py:603-633`

`pool_size=2`, `max_overflow=3`, `pool_pre_ping=True`, `pool_recycle=3600`, `pool_timeout=10` — **all unchanged**. The **only** knob added is `pool_reset_on_return="rollback"`. **`application_name=ensemble-repair` goes in the repair engine's OWN conninfo** (DR-31), **not** appended to its options string — the shared constant is interpolated **last** into that options string, so an `application_name` placed there would be overridden (measured: `repair_engine_reports_manager_name: true`, raw `t3_pool_attribution.out:111-119`). The rest of the options string (`statement_timeout=60000`, `lock_timeout=10000`, `idle_in_transaction_session_timeout=60000`, `timezone=UTC`) stays **byte-identical**.

### Engines NOT touched

`factory.py:184-199` (SQLite) · `factory.py:215-222` (latent config-driven PG) · `daemon/__main__.py` (boot preflight) · `daemon/persistence.py:316-331` (**P2 ORACLE**) except the honesty comment · `daemon/tools/ens_db_tools.py:649-695` (vacuum NullPool) · `daemon/auth/cli.py:152-160` (one-shot CLI) · `daemon/services/db_pool_manager.py:261-271` (external DBs) — **except** the asyncpg adapter's **separate** DSN (F2.6).

---

## §3 THE F8 CHANGE (the lane's only mechanism item)

### 3.1 Shape — three phases, no transaction open across an `await`

```
P0  read-decide scope        (manager engine session OPEN)
      read inj, parent_row, child agent_id, live_carrier_exists
    close
P1  content fetch             (NO manager-engine transaction open)
      short-lived own session for the child row; the checkpoint read
      (P2 saver pool) happens with no manager slot held
P2  write scope               (manager engine session OPEN)
      RE-READ inj  (detached/expired after P0)
      RE-ASSERT the terminal guard and the dead-parent guard
      create the artefacts  (message INSERT + task INSERT + injection UPDATE
      commit in ONE transaction)
```

**The three invariants the split creates — all three are new and all three need pins:**

1. **Re-read** — `inj` is re-read in P2 (F8.7).
2. **Re-assert** — the terminal guard and the dead-parent guard are re-evaluated **inside** P2, against the re-read row (F8.6). Without this, a concurrent escalation between P0 and P2 is not caught ⇒ **double delivery**.
3. **Write atomicity** — the message INSERT + task INSERT + injection UPDATE still commit in **one** transaction (F8.8). The change is decision↔write, not write↔write.

### 3.2 The three sites

| Site | Scope | Awaits inside | Required shape |
|---|---|---|---|
| async | `manager.py:11516` | `:11568-11571` (content fetch) **and** `:11754-11757` (`asyncio.to_thread(self._has_live_process_report_carrier)`) | both moved out (F8.2, F8.3) |
| sync twin | `manager.py:10860-10861` | `:11216-11224` (the `.result(timeout=8.0)` bridge) + the nested session at `:11110-11112` | both moved out (F8.4). **DR-26 — the bridge's call shape and its 8.0 s timeout are PRESERVED; only its POSITION changes (it moves OUT of the held scope) ⇒ F8-a's `with`-body scope requires the bridge to be OUTSIDE the scope.** |
| instance tool | `daemon/tools/instance.py:1207` | `:1360` (`await _bus.watch(...)`) | moved out, or excluded by leader ruling + disclosed (F8.5) |

**Deadlock check (done):** the sync twin runs on a **non-loop** `threading.Thread` (`manager.py:11180-11184`); the `run_coroutine_threadsafe` bridge still needs the manager's event loop, and the calling thread is not the loop thread ⇒ **no self-block, no deadlock**. The async twin runs on the loop; the fetch is moved to a short-lived session, so no loop-blocking DB call is introduced.

### 3.3 What the F8 change must NOT do

- **Must not** "fix" the hold by lengthening the session (that increases dwell).
- **Must not** move the work to an unbounded thread pool — the `.result(timeout=8.0)` bounded shape is the thing to preserve; the *transaction hold* is what goes.
- **Must not** touch `daemon/write_pause_guard.py` (F8.9).
- **Must not** weaken the `_write_guard` or the terminal/dead-parent guards to make the split easy.

### 3.4 The R15 bound rule (C-4) — **REPLACED by the Phase-0 verdict: the bound ships DISABLED**

- **Ordering (hard task-level predecessor):** F8 lands **before** the engine-level `idle_in_transaction_session_timeout` — which is now **moot**, because the backstop ships **DISABLED** (§2). If it is ever enabled, the ordering constraint is reinstated.
- **The Phase-0 verdict on the formula:** the rule *"≥ 4 × measured max legitimate hold, floor 30 s"* **cannot be satisfied by a trustworthy measurement** — the quantity it multiplies is **model-derived** for the dominant regimes (the 1 s tail is an injected `asyncio.sleep(1.0)`, raw `t1_mr3_hold.py:281-282`) and **unidentified** for the real path (the real fetch latency was **not measured**). Its own arithmetic yields **4.24 s**, so the **30 s floor dominates** and **coincides with the legitimate `pool_timeout` wait**.
- **⇒ The engine-level backstop ships DEFINED BUT NOT APPLIED** (§2). The enablement mechanism, the boot log line and the CONDITIONAL `SET LOCAL statement_timeout = 0` carve-out (leader OQ-3) are specified in §2.
- **DR-11 — the derivation inputs are MODELLED, not measured, and must be disclosed as such:** the real `_fetch_subshape_a_content_async` latency and the real `_get_last_assistant_message` python-side cost were **NOT measured**. The Phase-0 (base-tree) measurement is an **upper bound** for any applied value (it includes M-R3's 8–38 s dwell, a hold class F8 removes) ⇒ it is safe but **never shrinks after F8 lands**. The **post-F8 re-measurement** (F8-b's BASE-vs-HEAD comparison) is the figure any applied value should use once available; recording that re-measurement as the follow-up that tightens the bound is **required**, not optional.
- **The async twin's hold is currently UNBOUNDED** (`manager.py:11265-11271` has no `wait_for`) ⇒ **bound it or explicitly exclude it** from the budget.
- **Victim class (W-2) — the real collateral risk is UNMEASURED:** the bound counts **continuous** idle (PG resets the timer per statement). A legitimate borrower that waited ~29 s for a slot and *then* opened its transaction would be inside a 30 s bound's reach ⇒ the collateral class is **not** the "13–15 s M-R3 dwell" but the **checkout wait**, whose upper edge is ≈ `pool_timeout` = 30 s. Any future enablement must size the bound from a **measured** P100 **above** `pool_timeout` + 4 × (measured async max) — never by analogy to the repair engine.

---

## §4 F7 — INVARIANT, ATTRIBUTION METHOD, COLLECTED-COUNT ASSERTIONS

### 4.1 The invariant — **MEASURED FORM (DR-35; supersedes D-38's provisional form)**

F7 must assert **both**, co-equal. The **occupancy** half is measured and is a **three-state** invariant; the **residence** half uses the **measured** metric and the **operation's own** hold budget.

**(a) Occupancy — THREE states, never a count of zero.**

The census must report occupancy across **all three** PG states (measured — a backend checked out **before its first statement** is `state='idle'` with `xact_start IS NULL`; raw `t2_stat_activity.out:6-15`, `:25-33`, `:170-178`):

| # | state | meaning | raw cite |
|---|---|---|---|
| (i) | `idle` with `xact_start IS NULL` | checked out, **no statement issued yet** — the incident's dominant shape | raw `t2_stat_activity.out:6-15` (T2a0), `:25-33` (T2a), `:170-178` (T2f) |
| (ii) | `idle in transaction` | a transaction is open, the client is idle | raw `t2_stat_activity.out:41-50` (T2b) |
| (iii) | `active` | a statement is running — **the F4-B probe shape** | raw `t5_fix_sites.out:1540-1544` |

**Why (a) is load-bearing:** a census that counts only `idle in transaction` is **vacuous against two of the three shapes** — the incident's shape was 15/15 slots held with only 6 in `idle in transaction` and 9 idle-but-checked-out ⇒ a census-only guard would be **GREEN on the incident** (confirmed empirically: raw `t2_stat_activity.out:17-18`, `:179-180`).

**The occupancy assertion itself:** after a churn cycle, `manager.engine.pool.checkedout()` returns to its baseline. In-memory, no DB round trip (the sampler shape already exists at `daemon/services/readiness.py:525-549`). **This is the invariant that would have caught the incident.**

**(b) Residence — `xact_age_s`, against the operation's own hold budget.**

- **The metric is `xact_age_s` = `now() − xact_start`** (already collected — raw `common.py:88`, visible in every raw T2 row, e.g. `t2_stat_activity.out:49`). The **old metric `state_change − xact_start` is FROZEN at the last statement's completion** ⇒ a leaked backend shows the **same sub-millisecond value** as a healthy one ⇒ the budget **can never fire on the leak it exists to catch** (raw `t2_stat_activity.out:99` says so in the harness's own words: *"residence here is the OBSERVABLE lag, not the hold length"*). **This closes the residual half of DR-4.**
- **The budget is the operation's own hold budget, not a pool-wide literal.** The **2.0 s placeholder is WITHDRAWN** — see §2. The comparison is: *a backend whose `xact_age_s` exceeds **the hold budget of the operation that opened it** is a finding.* The **measured healthy band** is the reference point: **median 0.0005 s, p95 0.0007 s, max 0.0008 s** over 40 cycles (raw `t2_stat_activity.out:88-95`) — i.e. a healthy backend's transaction age is sub-millisecond.
- **The per-site hold budgets are NOT derivable from the Phase-0 evidence** (the real fetch latency is unmeasured — DR-11 / §0.4). ⇒ The budgets are declared **per call site** and pinned by the guard's own self-pin; where a budget cannot be justified it is **recorded as NOT-DERIVABLE and the assertion is scoped to the sites whose budget IS derivable** — never a guessed number.
- **The 2.0 s value would false-red a healthy daemon** — deterministically, in two ways: (i) the **sync twin's** legitimate dwell is **unconditionally ≥ 8 s** when the inner call stalls (measured 8011.119 ms, raw `t1_mr3_hold.out:307-320`); (ii) any instance whose real fetch exceeds 2 s. Neither is a leak.

**The old "count == 0 at every sample" invariant is withdrawn.** It is red for a healthy busy daemon and green for a slow leak. Counts are **never asserted to be zero.**

**λ (arrival rate) is NOT MEASURABLE from the raw** — there is no timestamped borrow-arrival series anywhere in the evidence (the only multi-backend census is a single instant — raw `t3_pool_attribution.out:2-25`). ⇒ The old `peak_occupancy ≤ λ_measured × budget` bound is **DROPPED as an assertion**; if a churn leg wants to report it, it is marked **UNMEASURABLE**, never asserted.

### 4.2 Attribution method (A3, A5, C-2, C-8, **DR-31, DR-32**)

1. **Scratch-database isolation** (D-23/D-24) — the guard creates its own uniquely-named database, points the daemon's engine factory at it, and asserts on `pg_stat_activity WHERE datname = <that db>`. Every backend in that database belongs to the test. **This is the guard's attribution and it stays.**
2. **`application_name` where present, else scratch-DB isolation** — the census is attributed *"by `application_name` where present, else by scratch-DB isolation"*. **DR-31 — the channel is the engine's OWN conninfo**, not the `-c options` string: the conninfo parameter **beats** the options form (measured — raw `t3_pool_attribution.out:127-135`: `both → "from-conninfo"`), and within one options string **last `-c` wins** (`:121-125`). The ordering hazard is measured: with the shared constant appended last, the repair engine reports the manager's name (`:111-119`, `repair_engine_reports_manager_name: true`). ⇒ **F2-g and F2-h assert the CHANNEL (conninfo), not merely the presence of a name.**
3. **Three engines carry a name — and they are a FLOOR, not the census boundary (DR-32).** `ensemble-manager` (F2.3), `ensemble-repair` (F2.5), `ensemble-checkpoint-adapter` (F2.6). The **shared** `PG_SESSION_CONNECT_ARGS` must **never** carry `application_name` (D-17). **But there are 11 REAL pool-creation sites in `daemon/`** (12 enumerated, **1** comment false-positive at `daemon/repositories/factory.py:289` — raw `t3_pool_attribution.out:27-101`) ⇒ **the F7 census covers EVERY pool-creation site**; the three named engines are the **daemon-internal subset** that this lane names, not the boundary.
4. **Every census captures** `count` **+** `state` **+** `wait_event_type` **+** `xact_start` **+** `query_start` **+** `state_change` **+** `backend_start` **+** `application_name` **+** `xact_age_s` (`now() − xact_start`) **+** `state_age_s`, and reports **residence-time buckets**, not just a count (raw `common.py:80-92` is the reference query shape). The `backend_start > run_start` BASE filter is **prescribed** in `test-strategy.md` §1.5 item 3 and must be **implemented** in the helper's SQL.
5. **PG state `idle` is a disclosure metric**, not an assertion (C-10); the same holds for `active` (F4-B's shape).

### 4.3 Collected-count assertions (C3 — every gate)

- Every gate command is written down with its **expected collected count**, and the count is **asserted > 0** **before** "0 failed" is read as a pass.
- The **expected count comes from an INDEPENDENT declaration (DR-23):** the artifact contract **`.agents/shared/planning/db-pool-idle-txn/expected-collected.md`** — **created in this fold** — holds a **per-pack table of counts measured at base `19fc11c8b`** (task **R-1**, routed to the tester lane). Every count cell in it is currently **`TBD (R-1)`**; **no number is invented here or anywhere in this lane**. The fence **reads that file**; the pack-local `EXPECTED_COLLECTED` constant applies to **NEW packs only** (the pre-existing DO-NOT-TOUCH corpus declares none and must not be modified). Without this independent declaration, `EXPECTED` would be filled in from the current run ⇒ the assertion is a **tautology** ⇒ the whole-tree leg would be "recorded", not "asserted" — the exact C-1 failure mode.
- The **whole-tree** legs (G1.5, G3.5) must assert `collected > 0` on **both** sides and reconcile `base_collected` / `head_collected` against the sum of the declared `EXPECTED_COLLECTED_*`; a **mismatch is FATAL**. "The base run's collected count is *recorded*" is **not** an assertion.
- A PG gate **fails** on `skipped > 0`, not only on failures — `tests/postgres/conftest.py:167-169` **skips the whole module** when PostgreSQL is unreachable ⇒ a skipped-everything PG gate is indistinguishable from a pass.
- The census gate's non-vacuity cell is restated as *"each family's count MUST **equal** its §7.2 baseline constant; any delta is a FAILURE until classified per §7.3"*, and the **diff** is the failing step (the scanner always exits 0 — OQ-7(c)).
- **ONE** canonical gate ladder (§6.2a) and **ONE** pack inventory (§5), with a static consistency pin asserting every gate ID / fix ID / AC ID referenced anywhere in the artifact set exists in the canonical sets (§6.4, W-11).
### 4.4 The churn leg (A2-iv, A10, C-12, W-8)

The churn leg **must drive the deferred-report reconcile** with a deliberately slow content fetch. The current library-level leg never touches `manager.py` and therefore **cannot discriminate F8**.

**Technique (sanctioned by this spec):** a second session holds `LOCK TABLE checkpoints IN ACCESS EXCLUSIVE MODE` on the **P2 saver's** tables, and the reconcile's content fetch is instrumented to block **≥ 3 s**; `pg_stat_activity` is sampled every **250 ms**. D2's `LOCK TABLE task` technique is reused **only** for the F4 leg — it targets the **queue probe's** `FROM task` read, whereas the reconcile awaits a **checkpoint read on the P2 saver pool** (`child_reports.py:1654` → `get_instance_messages(self._checkpointer, …)` → `persistence.py:316-331`, `autocommit=True`) — **different table, different pool**.

**Expectations:** BASE = ≥ 1 manager-pool backend in one of the **three** states (measured invariant, §4.1) **and** `pool.checkedout()` above baseline; HEAD = none. If BASE cannot demonstrate the F8 shape, the guard is a **regression** guard, not a **reproduction** guard — **say so** in the test strategy (A10) rather than forcing a BASE demo the mechanism does not support.

**The λ bound is DROPPED as an assertion (DR-35 / §0.4):** λ is **not measurable** from the Phase-0 evidence (no timestamped borrow-arrival series exists) ⇒ the churn leg **reports** `peak_occupancy` as a **disclosure metric** and marks the `λ × budget` comparison **UNMEASURABLE**. It is **never asserted**.

---

## §5 PIN INVENTORY — **THE ONE AUTHORITATIVE LIST (46 pins)**

**This table is the authority.** `test-strategy.md` §3 is a **mirror** of this table and MUST match it row-for-row (W-6: ONE pack inventory). The plan's §3 must match it 1:1. **Any pin added here must be added there in the same change; any pin removed here must be removed there.**

Markers: `none` = default-collected · `postgres` = `tests/postgres/…_pg.py` (auto-marked) · `integration` = `pytestmark = pytest.mark.integration`.
**Count-asserted column:** every PG/integration row reads **`✅ + zero-skips`** — `tests/postgres/conftest.py:167-169` **skips the whole module** when PostgreSQL is unreachable ⇒ a skipped-everything PG gate is indistinguishable from a pass (DR-17).

### 5.1 The pack inventory (concrete files — DR-28)

| # | Pack file | Class | Pins |
|---|---|---|---|
| 1 | `tests/unit/repositories/test_pg_engine_utc_session.py` **(EXISTING — amended per D-37)** | none | F2-a, F2-b, F2-c, F2-d, F2-e |
| 2 | `tests/unit/tools/test_dbpool_pool_attribution.py` **(NEW)** | none | F2-g |
| 3 | `tests/unit/tools/test_dbpool_repair_engine_isolation.py` **(NEW)** | none | F2-f |
| 4 | `tests/unit/tools/test_dbpool_p2_immutability.py` **(NEW)** | none | F2-h, F2-j |
| 5 | `tests/unit/tools/test_dbpool_honesty_blocks.py` **(NEW)** | none | F2-i |
| 6 | `tests/unit/tools/test_dbpool_txn_hygiene.py` **(NEW)** | none | F3-a, F3-b, F3-c, F3-d, F3-e |
| 7 | `tests/unit/tools/test_dbpool_probe_bound.py` **(NEW)** | none | F4-a, F4-e |
| 8 | `tests/unit/tools/test_dbpool_probe_failure_class.py` **(NEW)** | none | F4-c |
| 9 | `tests/unit/tools/test_readyz_grace_detail.py` **(EXISTING)** | none | F4-d |
| 10 | `tests/unit/tools/test_dbpool_mission_mint.py` **(NEW)** | none | F6-a, F6-e, F6-c-static |
| 11 | `tests/unit/tools/test_dbpool_wedge_capture_static.py` **(NEW)** | none | F5-c, F5-d |
| 12 | `tests/unit/tools/test_dbpool_probe_retention_static.py` **(NEW)** | none | F7-b-static |
| 13 | `tests/unit/tools/test_dbpool_occupancy_static.py` **(NEW)** | none | F7-e-static |
| 14 | `tests/unit/tools/test_dbpool_residence_static.py` **(NEW)** | none | F7-a-static |
| 15 | `tests/unit/tools/test_dbpool_guard_pack_selfcheck.py` **(NEW)** | none | F7-c, F7-d |
| 16 | `tests/unit/tools/test_dbpool_fix_site_static.py` **(NEW)** | none | F8-a |
| 17 | `tests/unit/tools/test_dbpool_census_families.py` **(NEW)** | none | G-A |
| 18 | `tests/unit/tools/test_dbpool_consistency_pin.py` **(NEW)** | none | G-consistency |
| 19 | `tests/unit/tools/test_dbpool_audit_record.py` **(NEW)** | none | F1-a |
| 20 | `tests/postgres/test_dbpool_mission_mint_pg.py` **(NEW)** | postgres | F6-b, F6-c |
| 21 | `tests/postgres/test_dbpool_probe_starvation_pg.py` **(NEW)** | postgres | **F4-f** |
| 22 | `tests/postgres/test_dbpool_wedge_capture_pg.py` **(NEW)** | postgres | F5-b |
| 23 | `tests/integration/test_dbpool_probe_bound_pg.py` **(NEW)** | integration | F4-b |
| 24 | `tests/integration/test_db_pool_idle_in_transaction_guard.py` **(NEW)** | integration | F7-a, F7-b |
| 25 | `tests/integration/test_dbpool_churn_census.py` **(NEW)** | integration | F7-e |
| 26 | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` **(NEW)** | integration | F8-b, F8-c, F8-d, F8-e |
| 27 | *(gate — process)* | n/a | F6-d |

### 5.2 The pins (46)

| Pin | Where (pack file + subject) | Marker | Mutation that makes it RED | Default-collected? | Count asserted? |
|---|---|---|---|---|---|
| **F1-a** | `tests/unit/tools/test_dbpool_audit_record.py` — `audit-findings.md` exists and carries the **AST baseline** | none | Delete `audit-findings.md`, or restore the retired **"≈689"** figure instead of the AST baseline (A=613 / C=4 / B=175 / D=148 / E=14 / F=0) — the **779** scanner-independent total is the cited figure, the aggregate is marked non-canonical | ✅ | ✅ |
| **F2-a** | `tests/unit/repositories/test_pg_engine_utc_session.py` (`:86-88`, amended) — `pool_reset_on_return="rollback"` is explicit on the manager engine | none | Remove the explicit `pool_reset_on_return` kwarg | ✅ | ✅ |
| **F2-b** | `tests/unit/repositories/test_pg_engine_utc_session.py` | none | Drop `pool_recycle` | ✅ | ✅ |
| **F2-c** | `tests/unit/repositories/test_pg_engine_utc_session.py` | none | Drop the `idle_in_transaction_session_timeout` **clause** from the manager options — **DR-20: the assertion is STRUCTURAL (clause present), never the literal `60000`** | ✅ | ✅ |
| **F2-d** | `tests/unit/repositories/test_pg_engine_utc_session.py` (existing) | none | Remove `pool_pre_ping` | ✅ | ✅ |
| **F2-e** | `tests/unit/repositories/test_pg_engine_utc_session.py` (existing `:86-88`, **amended** per C-9 + **D-37 + DR-20**) | none | Replace the manager options string (order-sensitive), or change the `idle_in_transaction_session_timeout` **value** so it no longer **equals `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` as imported from `daemon/constants.py`**, or reorder the clauses | ✅ | ✅ |
| **F2-f** | `tests/unit/tools/test_dbpool_repair_engine_isolation.py` — the **shared** `PG_SESSION_CONNECT_ARGS` carries **no** `application_name` and no timeout (D-17 guard) | none | Extend the shared constant with an `application_name` or a timeout ⇒ the repair engine's rendered string changes ⇒ RED | ✅ | ✅ |
| **F2-g** | `tests/unit/tools/test_dbpool_pool_attribution.py` — **DR-31: the CHANNEL is asserted, not merely the presence.** Each engine's `application_name` lives in **its own conninfo/URL query dict** (NOT in the `-c options` string); the three names are **distinct**; each appears **exactly once**; the **shared** constant carries **none** | none | Put `application_name` back into the shared `-c options` string (the channel flips ⇒ RED); or make two names equal; or make one appear twice; or add a name to the shared constant | ✅ | ✅ |
| **F2-h** | `tests/unit/tools/test_dbpool_p2_immutability.py` — the shared `conn_string` at `persistence.py:225` does **not** carry `application_name` (P2-immutability + separate-DSN guard) | none | Add `application_name` to the shared `conn_string` ⇒ the P2 oracle's rendered conninfo changes ⇒ RED | ✅ | ✅ |
| **F2-i** | `tests/unit/tools/test_dbpool_honesty_blocks.py` — both honesty blocks state "retention/transaction-hygiene REPAIRED; DWELL/OCCUPANCY NOT REPAIRED" **and** the backstop's **DISABLED** status (DR-8) | none | Restore the false honesty-block wording ("the P1 leak is REPAIRED"), or delete the `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED` status line | ✅ | ✅ |
| **F2-j** | `tests/unit/tools/test_dbpool_p2_immutability.py` — P2 saver pool construction (`persistence.py:316-331`) knobs are byte-identical | none | Change any knob of the P2 saver pool | ✅ | ✅ |
| **F3-a** | `tests/unit/tools/test_dbpool_txn_hygiene.py` — after a dry-run whose `BEGIN` raises, the connection is **not** in a transaction and a further statement does not raise *"current transaction is aborted"* | none | Re-introduce `session.execute(text("BEGIN"))` at the DDL or DML site (`daemon/tools/ens_db_tools.py:1479` / `:1491`) | ✅ | ✅ |
| **F3-b** | `tests/unit/tools/test_dbpool_txn_hygiene.py` — — the dry-run's failure path performs a **rollback** (observable: the session's transaction is None AND a rollback spy fired) | none | Delete the rollback from the outer-except path (the outer-except-no-rollback shape at `:1513`) | ✅ | ✅ |
| **F3-c** | `tests/unit/tools/test_dbpool_txn_hygiene.py` — — the DDL-branch rollback itself failing (`daemon/tools/ens_db_tools.py:1489`) also leaves the connection clean | none | Remove the outer-path rollback handling for the rollback-failure variant — **the plantable site is `daemon/tools/ens_db_tools.py:1489` (the `session.rollback()` call inside the DDL branch's `finally:`)** | ✅ | ✅ |
| **F3-d** | `tests/unit/tools/test_dbpool_txn_hygiene.py` — — the dry-run's `previewed` audit row is written in a **separate session** and is not rolled back by the dry-run transaction | none | Re-introduce the audit row **inside** the rolled-back transaction (violates the separate-session contract at `:1530-1538`) | ✅ | ✅ |
| **F3-e** | `tests/unit/tools/test_dbpool_txn_hygiene.py` — — agent-visible preview strings and docstrings reflect the new dry-run wording | none | Change the preview string at `daemon/tools/ens_db_tools.py:1482` ⇒ the wording assertion REDs (one concrete edit) | ✅ | ✅ |
| **F4-a** | `tests/unit/tools/test_dbpool_probe_bound.py` — **re-pointed (DR-29).** Asserts **BOTH**: (i) **F4-A** — each probe body performs the **acquisition admission pre-check** before `engine.connect()` (the `TimeoutError`-on-saturation path), and the **saver probe is excluded** from both; (ii) **F4-B** — `make_db_probe` issues `SET LOCAL statement_timeout = 2 × DB_PROBE_TIMEOUT_S` as the **FIRST** statement, dialect-guarded (PG only); `make_queue_probe` issues `2 × QUEUE_PROBE_TIMEOUT_S` | none | Remove the admission pre-check from `make_db_probe` or `make_queue_probe`; or remove the `SET LOCAL statement_timeout` from `make_db_probe`; or change the derived value so it is no longer `2 ×` the constant; or add `SET LOCAL` to the **saver** probe (the exclusion assertion REDs) | ✅ | ✅ |
| **F4-b** | `tests/integration/test_dbpool_probe_bound_pg.py` — **RETAINED but RE-SCOPED (DR-29): the F4-B STATEMENT case, and it is SYNTHETIC.** Under a deliberately held lock, `make_queue_probe`'s statement is **cancelled** (`57014` / `QueryCanceled`), the `to_thread` future **returns**, the connection is **returned to the pool**, `engine.pool.checkedout()` returns to baseline within the bound — and the backend row is **EXPECTED TO PERSIST** with `state='idle'` | integration | **Remove the `SET LOCAL statement_timeout` from `make_queue_probe`** — the statement stays blocked, the connection stays checked out, `engine.pool.checkedout()` does not return to baseline ⇒ RED. **The `"row gone from pg_stat_activity"` assertion is NOT part of this pin** — that signature belongs to the engine-level `idle_in_transaction_session_timeout` (measured: `after_wait: null`, `state_after: "BACKEND GONE (terminated by server)"` — raw `t2_stat_activity.out:137-140`), which **F4 does not install**. **This leg does NOT cover the incident's shape** — that is F4-f | ❌ PG | ✅ + zero-skips |
| **F4-c** | `tests/unit/tools/test_dbpool_probe_failure_class.py` — the probe's returned `(verdict, failure_class)` pair stays `probe_timeout` (not hard-component-red); **F4-A's synchronous `TimeoutError` lands on the same class** | none | Change the probe's returned pair, or reclassify a timeout as hard-component-red — **and the F4-A refusal path must land on `FAILURE_CLASS_PROBE_TIMEOUT`** | ✅ | ✅ |
| **F4-d** | `tests/unit/tools/test_readyz_grace_detail.py` **(EXISTING)** — readiness detail fields / serialization unchanged (DO-NOT-TOUCH) | none | Remove a detail field | ✅ | ✅ |
| **F4-e** | `tests/unit/tools/test_dbpool_probe_bound.py` — the docstring at `daemon/services/readiness.py:107-113` no longer carries the false premise ("the driver's own connect/execute timeout releases it") **and states the MEASURED semantics** (backend `active`, statement cancelled `57014`, row persists) | none | Restore the false docstring premise | ✅ | ✅ |
| **F4-f** | `tests/postgres/test_dbpool_probe_starvation_pg.py` — **NEW (DR-29, the missing leg).** Under **15 holders**: the probe holds **NO** connection; `engine.pool.checkedout()` **equals the holder count** (15, not 16); **no** waiter is left in the pool's checkout queue; the probe thread **terminates within a bounded window**. **BASE-vs-HEAD:** at BASE (no F4-A) the probe is a **stranded waiter** (measured: `checkedout_right_after_timeout: 15`, timeout at **0.5012 s** — raw `t5_fix_sites.out:1960-1967`); at HEAD (with F4-A) it **refuses immediately** and no waiter exists | postgres | Remove the F4-A admission pre-check ⇒ at HEAD the probe becomes a waiter again ⇒ the "no waiter / bounded termination" assertion REDs | ❌ PG | ✅ + zero-skips |
| **F5-b** | `tests/postgres/test_dbpool_wedge_capture_pg.py` — the wedge-capture queries use `FROM task` (not `tasks`); the **cascade** no longer reproduces | postgres | Re-introduce `FROM tasks` / `tasks t` | ❌ PG | ✅ + zero-skips |
| **F5-c** | `tests/unit/tools/test_dbpool_wedge_capture_static.py` — static twin: no `tasks` / `tasks t` in the module | none | Re-introduce `FROM tasks` / `tasks t` | ✅ | ✅ |
| **F5-d** | `tests/unit/tools/test_dbpool_wedge_capture_static.py` — **RESTORED (DR-22):** the **store census** query (`daemon/services/wedge_capture/db_capture.py:351-363`, over `pg_class`/`pg_namespace`) still works — the wrong-table fix must not have touched it | none | Break the census SQL (e.g. change the `pg_class`/`pg_namespace` join) ⇒ RED | ✅ | ✅ |
| **F6-a** | `tests/unit/tools/test_dbpool_mission_mint.py` — the boot mint uses an upsert with `on_conflict_do_nothing` keyed on the anchor; on `rowcount == 0` it re-resolves via the **anchor-scoped** probe (`find_open_epoch_by_anchor`, `daemon/repositories/mission_record/repository.py:69-93`) | none | Replace the upsert with the bare ORM insert; or change the re-resolve key from the anchor to `founding_message_id` | ✅ | ✅ |
| **F6-b** | `tests/postgres/test_dbpool_mission_mint_pg.py` — idempotency holds on a real scratch PG **under concurrency** (DR-33): a **concurrent double-mint for the same anchor** must not raise (the measured trigger: 8-way concurrent, no barrier ⇒ **7 real `UniqueViolation`s** at BASE — raw `t5_fix_sites.out:2040-2053`; forced overlap ⇒ **5** — `:2054-2067`). The sequential leg is already idempotent (c2: `ok: true`, `rung: 1`) | postgres | Remove the idempotency (revert to a bare INSERT) ⇒ the concurrent leg raises `UniqueViolation` ⇒ RED | ❌ PG | ✅ + zero-skips |
| **F6-c** | `tests/postgres/test_dbpool_mission_mint_pg.py` — the target DB **has** `ux_mission_records_anchor_open` with a byte-identical predicate | postgres | Drop the partial unique index from the target DB ⇒ the upsert raises **42P10** ⇒ the negative leg REDs | ❌ PG | ✅ + zero-skips |
| **F6-c-static** | `tests/unit/tools/test_dbpool_mission_mint.py` — **static twin (DR-24):** `OPEN_EPOCH_STATUS_SQL` at `daemon/repositories/mission_record/models.py:209` is **byte-identical** to the predicate at `models.py:305-313` **and** to `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69` | none | Change `status = 'open'` to `status = "OPEN"` at `models.py:209` ⇒ the conflict-target predicate no longer byte-matches the partial index ⇒ RED | ✅ | ✅ |
| **F6-d** | *(gate — process)* — no new `.sql` file **and** no new ORM TABLE/COLUMN/INDEX (A-6) | n/a | Introduce a new `.sql` file, or a new ORM TABLE/COLUMN/INDEX | n/a | n/a |
| **F6-e** | `tests/unit/tools/test_dbpool_mission_mint.py` — **CONCRETE F6-SUBJECT mutation (DR-18; the TODO cell is DELETED):** the conflict-target predicate must stay byte-identical to the partial index | none | Change `OPEN_EPOCH_STATUS_SQL` at `daemon/repositories/mission_record/models.py:209` from `status = 'open'` to `status = "OPEN"` ⇒ the conflict target no longer byte-matches the partial unique index ⇒ the upsert stops being idempotent ⇒ RED | ✅ | ✅ |
| **F7-a** | `tests/integration/test_db_pool_idle_in_transaction_guard.py` — the residence assertion uses **`xact_age_s` = `now() − xact_start`** (NOT `state_change − xact_start`) and compares against **the operation's own hold budget** (the **2.0 s placeholder is WITHDRAWN**) | integration | Hold a manager-pool connection across a dwell that exceeds **that operation's** budget ⇒ the `xact_age_s` assertion REDs. **A pin written against `state_change − xact_start` is VACUOUS against the leak** (the metric is frozen at the last statement) — the mutation that proves this pin alive is a **deliberate long `xact_age_s`**, not a long `state_age_s` | ❌ PG | ✅ + zero-skips |
| **F7-a-static** | `tests/unit/tools/test_dbpool_residence_static.py` — **static twin (DR-24):** the residence budget constant exists in `daemon/constants.py` **and** the guard's assertion references it **and** the guard's metric is `xact_age_s` | none | Delete the budget constant, or revert the guard's metric to `state_change − xact_start` ⇒ RED | ✅ | ✅ |
| **F7-b** | `tests/integration/test_db_pool_idle_in_transaction_guard.py` — re-introduce a retention path (e.g. return a connection with an open transaction) | integration | Re-introduce a retention path | ❌ PG | ✅ + zero-skips |
| **F7-b-static** | `tests/unit/tools/test_dbpool_probe_retention_static.py` — (1) `make_queue_probe` source contains `SET LOCAL statement_timeout`; (2) the derived value equals `2 * QUEUE_PROBE_TIMEOUT_S`; (3) the **saver** probe does **not** contain `SET LOCAL` (exclusion); (4) the docstring at `daemon/services/readiness.py:107-113` no longer carries the false premise | none | Remove `SET LOCAL statement_timeout` from `make_queue_probe` (or add it to the saver probe, or restore the false docstring) ⇒ twin RED | ✅ | ✅ |
| **F7-c** | `tests/unit/tools/test_dbpool_guard_pack_selfcheck.py` — self-pin: delete the guard's assertion **and** decrement the count constant ⇒ the self-pin re-reads the source ⇒ still **RED** | none | Delete the assertion + decrement the constant | ✅ | ✅ |
| **F7-d** | `tests/unit/tools/test_dbpool_guard_pack_selfcheck.py` — static twin: delete / un-mark / move the guard file out of the `integration` placement | none | Delete / un-mark / move the guard file | ✅ | ✅ |
| **F7-e** | `tests/integration/test_dbpool_churn_census.py` — **the pin that would have caught the incident:** re-introduce the occupancy defect — hold 2 manager slots across the churn cycle ⇒ `pool.checkedout()` does not return to baseline ⇒ RED | integration | Hold 2 manager slots across the churn cycle | ❌ PG | ✅ + zero-skips |
| **F7-e-static** | `tests/unit/tools/test_dbpool_occupancy_static.py` — (1) no `ast.Await` / `run_coroutine_threadsafe(...).result(...)` inside the `with`-body of a manager-engine session scope in `daemon/` (F8-a's shape, the three named sites); (2) the terminal guard and dead-parent guard are re-asserted inside the P2 write scope (F8.6); (3) `inj` is re-read in P2 (F8.7); (4) the message/task/injection writes commit in one transaction (F8.8) | none | Re-introduce an `await` inside the `with`-body (or drop the re-assertion / split the transaction) ⇒ twin RED | ✅ | ✅ |
| **F8-a** | `tests/unit/tools/test_dbpool_fix_site_static.py` — **static (AST, default-collected):** no `ast.Await` node and no `run_coroutine_threadsafe(...).result(...)` inside the **`with`-body** of a manager-engine session scope in `daemon/`. Scope the rule to the `with`-body only (a function-wide rule false-positives on the legitimate bridge calls at `:11216-11224` / `:11568-11571`); **name the three expected sites** in the docstring so a fourth is a deliberate failure. **DR-26 — the sync twin's bridge must be OUTSIDE the scope** (its call shape and 8.0 s timeout are preserved; only its position changes) | none | Insert an `ast.Await` or a `run_coroutine_threadsafe(...).result(...)` inside the `with`-body of a manager-engine session scope in `daemon/` ⇒ RED | ✅ | ✅ |
| **F8-b** | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` — drive the reconcile with a slowed content fetch; assert **no** manager-pool idle-in-txn backend attributable to the reconcile and `pool.checkedout()` back to baseline. **This leg is also the post-F8 re-measurement** the bound rule needs (DR-11) | integration | Drive the reconcile with a slowed content fetch; the assertion REDs if the hold remains | ❌ PG | ✅ + zero-skips |
| **F8-c** | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` — **DR-10 — the injection point is named:** a **test hook** flips the `ReportInjection` row's `inj.state` to a terminal value (`INJECTED` / `TASK_DELIVERED`) **after P0's scope closes and before P2's scope opens**; the observable is **NO artefacts created** (no message INSERT, no task INSERT, no injection UPDATE past the guard) | integration | Inject a concurrent escalation at the P0→P2 seam ⇒ artefacts are created ⇒ RED | ❌ PG | ✅ + zero-skips |
| **F8-d** | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` — the write scope's single-transaction commit (message + task + injection) is preserved | integration | Break the write scope's single-transaction commit (e.g. split the message/task/injection writes) ⇒ RED | ❌ PG | ✅ + zero-skips |
| **F8-e** | `tests/integration/test_dbpool_deferred_report_reconcile_pg.py` — the async twin's hold is bounded (no unbounded `wait_for`-less hold) | integration | Re-introduce the unbounded async-twin hold (remove its `wait_for`/bound) | ❌ PG | ✅ + zero-skips |
| **G-A** | `tests/unit/tools/test_dbpool_census_families.py` — the census families match the §7.2 **AST baseline** (A=613 / C=4 / B=175 / D=148 / E=14 / F=0); the scanner-independent total is **779** and the aggregate is marked **non-canonical** | none | Any family count delta not classified per §7.3 | ✅ | ✅ |
| **G-consistency** | `tests/unit/tools/test_dbpool_consistency_pin.py` — **the W-11 cross-artifact consistency pin; the scanner is WRITTEN DOWN in §6.4** (file set, regex, sentinel handling) | none | Introduce a gate ID / fix ID / AC ID that is not in the canonical set, or remove/duplicate a sentinel pair, or add a pin to §5 without adding it to `test-strategy.md` §3 ⇒ RED | ✅ | ✅ |

**Inventory rules (binding):**
- Every pin has a **concrete, plantable** mutation — no "any change that alters X", no TODO cells (DR-18 deleted the F6-e TODO).
- Every PG/integration pin has a **default-collected static twin** (W-13) — **the pin → twin map is §9.**
- Every PG/integration pin asserts **zero skips** in addition to its collected count (DR-17).
- Every pin asserts its **collected count**, sourced from the **independent** `expected-collected.md` declaration (§4.3, DR-23).
- **ONE authoritative count: 46 pins.** F1-a (1) · F2 (10) · F3 (5) · F4 (6) · F5 (3) · F6 (6) · F7 (8) · F8 (5) · G-A (1) · G-consistency (1) = **46**.
## §6 PHASE / GATE ORDER (ONE canonical ladder)

The plan set previously carried **two** ladders (a legacy seven-step ladder in `phase3-plan.md` and a three-phase-group enumeration in `test-strategy.md`) and **two** guard filenames. **This section is the authority** — it now carries the **ladder ROWS themselves** (DR-2), not just a declaration.

### 6.1 Phase order

| Phase | Contents | Gate |
|---|---|---|
| **P1** | **F8** (F8.1–F8.9) — **the Phase-1 PRIMARY** · F3 (F3.1–F3.5) · **F4 (F4.1–F4.9 — DEMOTED to defence-in-depth, DR-29)** · F6 (F6.1–F6.8) · **F5 (F5.1–F5.3 — RAISED from Phase 2, DR-34)** · F1 (F1.1) | Transaction-hygiene + mechanism pins green; **no** change to the readiness detail schema, leak predicate, grace refusal path or never-green semantics |
| **P2** | F2 (F2.1–F2.10) · F5's remaining PG leg | Knob matrix pinned; **the engine-level idle-in-txn backstop is DISABLED** (§2) — if it is ever enabled, it lands only after F8 |
| **P3** | F7 (§4) · the pin packs that freeze F3/F4/F6/F2/F5/F8 | The guard runs in a form the runner **executes** with an asserted collected count; net-new test reds = 0 |

**Sequencing constraints (hard):**
1. **F8 before any engine-level `idle_in_transaction_session_timeout`** (C-4, A6-i). **Currently moot** — the backstop ships DISABLED (§2). The constraint is reinstated the moment enablement is contemplated.
2. **F2's `application_name` change and the AC amendments land in ONE commit** (C-2) — AC14/AC15/AC16/T2.4/T2.6/D-19/D-24/G2.4/F2-f/F2-g all change together, or the gate is RED from birth.
3. **The `statement_timeout` DROP lands with the F2 change** (C-3) — D-16, T2.2, AC14 **and** the Phase-2 objective text.
4. **The asyncpg DSN (F2.6) lands with the F2 change** (C-8) — it must not touch the shared `conn_string`.
5. **G0 is a hard predecessor of every Phase-1 change group** (DR-19): T1.1 (F3), T1.4 (F4), T1.8 (F6), T1.11 (F1) and the F8 group all carry `Depends = G0`; T2.1/T2.2/T2.3 carry `Depends = F8 + G0`.
6. **A Phase-2 gate cell asserts F8's commits are ancestors of the F2 commit**, and another asserts the F2 commit contains the `application_name` change, the AC amendments, the `statement_timeout` DROP and the asyncpg DSN **in one commit** (DR-19(c)/(d)).

### 6.2 Gate form (binding — C-1, W-10, **DR-16**)

**Interpreter: `/home/nea/ensemble-src/.venv/bin/python`** (verified: pytest 9.0.2, py 3.13.15; `import daemon` resolves to the **worktree** when cwd = worktree). **Drop `uv run`** — `uv` is **off-PATH** and the worktree has **no `.venv`** ⇒ `uv run` triggers a network-dependent sync at gate time. **Gates MUST NOT run from the shared checkout.**

**The FULL Class-1 fence — every gate form in every artifact carries ALL FIVE parts. No exceptions, no "bare interpreter" forms:**

```
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" \
/home/nea/ensemble-src/.venv/bin/python -m pytest <paths> --collect-only -q
```

- **`cd /home/nea/ensemble-src-wt-dbpool-idletxn` is LOAD-BEARING** — from `/tmp` (or any other cwd) the certified interpreter resolves `daemon` to the **SHARED CHECKOUT** (`/home/nea/ensemble-src/daemon/__init__.py`), i.e. the same command form runs against **different code** depending on cwd. Every gate form therefore begins with the `cd`.
- **Per-variable** scrub only, **six** variables: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `DAEMON_PORT`. `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) — an ambient `POSTGRES_DB=ensemble_prod` survives it. (`DAEMON_PORT` is scrubbed so a gate can never bind or probe the live daemon's port.)
- `HOME="$(mktemp -d)"` — **never** a HOME-derived live install path (two live-install deletions, 2026-10-12).
- **First step of every gate: print the interpreter certification** — `python -c "import daemon,pytest; print(daemon.__file__, pytest.__version__)"` — and attach it to the gate report.
- The `--collect-only` count is captured **before** the run; `collected == 0` ⇒ **FATAL**, never "0 failed ⇒ pass".

**This fence applies to, without exception:** the Class-1 unit command · the Class-2 PG command · the Class-3 integration command · **Class 4a and Class 4b** (the DO-NOT-TOUCH regression packs — DR-16: these previously carried the interpreter and **nothing else**) · **the §2.3 collected-count fence script** · **every unit row of the canonical ladder (§6.2a)** · **every PG/integration row of the canonical ladder** · **the whole-tree rows (G1.5, G3.5)**.

**PG / integration gate form** (adds the marker override):

```
… /home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m postgres|integration <paths> -q
```

(`pyproject.toml:95` deselects `integration`/`postgres`/`slow`/`conformance` by default.) **A PG gate fails on `skipped > 0`, not only on failures.**

### 6.2a The canonical gate ladder — **THE ROWS (17 entries; one ID per scope)**

**Every ID below denotes exactly ONE scope.** No ID may denote three scopes (DR-2's defect). `G-consistency` is a **static pin, NOT a ladder entry** — it rides G1.4.

| Gate ID | Scope | Contents | Command form (all carry the FULL §6.2 fence) | Collected-count assertion |
|---|---|---|---|---|
| **G0** | Phase-0 precondition | `BASELINE.md` exists, is reviewed, and **the two provisional literals are replaced by measurement OR recorded DISABLED** | artifact check (file exists + the two literals resolved) | non-vacuity: the artifact is present **and** both literals are resolved — not merely "recorded" |
| **G1.1** | F3 mechanism — unit | F3-a, F3-b, F3-c, F3-d, F3-e | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G1.2** | F6 mint — unit | F6-a, F6-e, **F6-c-static** | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G1.3** | F5 wedge-capture — static | F5-c, **F5-d** | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G1.4** | F8 static + F4 unit/static | F8-a, F4-a, F4-c, F4-d, F4-e, **G-consistency** | Class-1 unit | `collected > 0`; count **equals** `expected-collected.md` |
| **G1.5** | whole-tree base-vs-HEAD | the default-collected corpus, base vs HEAD | Class-1 unit over the whole tree | `collected > 0` on **BOTH** sides; `base_collected`/`head_collected` reconciled against the sum of the declared `EXPECTED_COLLECTED_*`; **mismatch = FATAL** |
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
| **G3.5** | whole-tree base-vs-HEAD | the default-collected corpus, base vs HEAD (Phase-3 run) | Class-1 unit over the whole tree | `collected > 0` on **BOTH** sides; mismatch = FATAL |

**17 ladder entries — the explicit enumeration:** `G0`, `G1.1`, `G1.2`, `G1.3`, `G1.4`, `G1.5`, `G2.1`, `G2.2`, `G2.3`, `G2.4`, `G2.5`, `G2.6`, `G3.1`, `G3.2`, `G3.3`, `G3.4`, `G3.5`. **No range notation is used anywhere in this lane's artifacts** — the old `{G1.1…G3.5}` form literally admitted non-existent ids; every id in the set is listed individually above and in `test-strategy.md` §4.2.

### 6.3 Legacy → canonical gate-ID mapping (authority for FOLD-MAP.md cross-check)

**Canonical source of truth: this spec's §6.2a.** The legacy seven-step ladder in `phase3-plan.md` is the duplicate ladder being retired. Its ids' **meanings were recovered empirically** from the pre-fold plan at commit `23c232b8d` (`git show 23c232b8d:.agents/shared/planning/db-pool-idle-txn/phase3-plan.md` §2.1). **The whole legacy-mapping region below — the recovered-meanings paragraph AND the mapping table — is ONE sentinel-delimited exempt region**; the scanner skips it and skips nothing else.

<!-- EXEMPT:BEGIN legacy-gate-map -->
**Recovered legacy meanings:** the first legacy id = the F7 guard's own reachability (unit static pin, G-A); the second = Phase-1 packs (F3/F4/F6 unit legs); the third = Phase-2 packs (knob matrix, repair-engine, P2 immutability); the fourth = Phase-1 PG legs (F4 retention PG, F6 idempotency PG); the fifth = Phase-2 PG legs (wedge-capture correctness); the sixth = the F7 guard; the seventh = whole-tree sweep.

| Legacy ID (phase3-plan.md) | Canonical ID(s) (this spec) | Contents |
|---|---|---|
| **G0** | **G3.4** (the guard's static reachability leg) | guard's own reachability (unit static pin, G-A) — G0 was a **runnable unit gate**, not a pre-gate; the earlier "(pre-gate)" reading was a content error |
| **G1** | **G1.1 · G1.2 · G1.3 · G1.4** | Phase-1 unit packs (F3 mechanism, F6 mint, F5 static, F8 static + F4 unit/static) |
| **G2** | **G2.1 · G2.2 · G2.3** | Phase-2 unit packs (F2 knob matrix, repair-engine isolation, P2-oracle immutability) |
| **G3** | **G2.5** (the Phase-1 PG legs) | Phase-1 PG legs — F4 retention PG, F4-f starvation PG, F6 idempotency PG, F8 PG legs |
| **G4** | **G2.6** (the Phase-2 PG legs) | Phase-2 PG legs (wedge-capture correctness) |
| **G5** | **G3.1 · G3.2 · G3.3 · G3.4** | F7 guard (occupancy, residence + self-pin, static backstops, reachability + census family) |
| **G6** | **G1.5 / G3.5** | two whole-tree sweep legs |
<!-- EXEMPT:END -->

**Rules:**
- Any gate ID referenced in any artifact must exist in the canonical set **enumerated in §6.2a** — the enumeration, never a range (W-11 static consistency pin, §6.4).
- The `F7` self-pin (F7-c) and the F8 static pin (F8-a) are default-collected unit packs and are NOT separate ladder entries — F7-c rides **G3.2** and F8-a rides **G1.4**.
- **The legacy mapping table above is the ONLY exempt region in this artifact.** It is delimited by the machine-detectable **BEGIN/END marker pair** (the exact marker text is given above, in §6.3), present **exactly once**; the pin asserts the pair is present exactly once in an artifact that carries the mapping, so deleting or duplicating the markers REDs (DR-3/DR-21). Every gate reference inside it is **historical, not live**. **This artifact carries exactly ONE marker pair** — the one above — and no other mention of the marker text anywhere in the file.

### 6.4 The W-11 cross-artifact consistency pin — **THE SCANNER, WRITTEN DOWN (DR-3, DR-21)**

The pin (`G-consistency`, `tests/unit/tools/test_dbpool_consistency_pin.py`) is specified here in full so it is implementable without re-deriving the rules.

**6.4.1 — The scan set (EVERY artifact carrying gate / AC / fix ids):**

```
.agents/shared/planning/db-pool-idle-txn/plan-overview.md
.agents/shared/planning/db-pool-idle-txn/phase1-plan.md
.agents/shared/planning/db-pool-idle-txn/phase2-plan.md
.agents/shared/planning/db-pool-idle-txn/phase3-plan.md
.agents/shared/planning/db-pool-idle-txn/test-strategy.md
.agents/shared/planning/db-pool-idle-txn/decisions.md
.agents/shared/planning/db-pool-idle-txn/architecture-recommendation.md
.agents/shared/planning/db-pool-idle-txn/final-implementation-spec.md
```

(The two-artifact scope the fold had narrowed to is **withdrawn** — it is the documented root cause of the drift the fold claims to have closed.)

**6.4.2 — The checks (four):**

| # | Check | Rule |
|---|---|---|
| 1 | **Gate-ID check** | every **gate-id token** in the scan set — a capital `G`, a digit `0`–`3`, a dot, and a digit — must be a member of the **explicit enumeration** in §6.2a (17 ids). The legacy seven-step ids are permitted **only** inside a sentinel-delimited exempt region. The scanner matches tokens **outside** inline-code spans and outside fenced blocks, so the regex written in this section is documentation, not a match site. |
| 2 | **Fix-ID check** | every **fix-id token** — a capital `F`, a digit `1`–`8`, then either a dot and a number, or a dash and a lowercase suffix — must be either (a) a **fix id** in §1's numbered-change blocks, (b) a **pin** in §5, or (c) a **known exempt** token (e.g. `F7` the fix group). |
| 3 | **AC-ID check** | every **AC-id token** — the letters `AC` followed by one or two digits — must appear in **at least one** other artifact in the scan set (the AC set is cross-artifact by construction — an AC that exists in exactly one file is drift). |
| 4 | **Pack-inventory check** | the **set of pin ids** in §5 (this artifact) must equal the **set of pin ids** in `test-strategy.md` §3, **exactly** — no pin on one side only, no id on one side with a different file on the other. |

**6.4.3 — Sentinel handling (the exemption is machine-detectable, not prose):**

- The scanner **skips** only the text **between** the BEGIN marker and the matching END marker (the exact marker text is given once, in §6.3).
- The scanner **asserts, per artifact**, that the number of marker pairs is **exactly one if that artifact carries the legacy→canonical mapping, and exactly zero if it does not** — a missing pair on a mapping-carrying artifact, a duplicated pair anywhere, or a pair in an artifact that carries no mapping all **RED** the pin. (Otherwise the exemption could be widened silently.) **Today exactly TWO artifacts carry the mapping — this one (§6.3) and `plan-overview.md`'s C3b — and the other six carry zero pairs.**
- The exempt region is the **legacy→canonical audit table only**. Any gate reference inside it is **not a live reference**.
- The marker pair must appear **at most once per artifact** — labels are per-artifact.

**6.4.4 — What the pin does NOT do:** it does not check prose semantics, does not verify that a gate's *contents* match its description, and does not check file paths beyond the pack-inventory check. Those are the reviewer's job, not the scanner's.

**6.4.5 — The scanner's own artifact:** the pin **re-reads this section** (the enumerated ladder, the sentinel rules and the §5 pin list) as its source of truth — it must **not** carry a private copy of the id sets. If the pin and this artifact disagree, the pin is wrong.

---

## §7 DO-NOT-TOUCH

**Absolutely untouched (any diff is a gate failure):**

- `daemon/services/readiness.py` — `:312-325` (detail fields), `:381`/`:408-446` (detail serialization), `:500-522` (`resolve_psycopg_pool`), `:525-549` (`sample_sqlalchemy_pool_state` — P1 **ADVISORY, MUST NOT arm**), `:552-567` (`sample_psycopg_pool_state` — P2 **ORACLE**), `:656-671` (tracker init), `:690-781` (`observe()` — conjunction `:739-743`, arm-on-first-eligible, re-arm-after-full-lapse), `:784-941` (`_evaluate_leak` — waiting-streak arm, exhaustion arm, subsumed trend arm, S9 streak arm), `:961-983` (`_checkedout_rising` — published, never arming), `:1263-1296` (`_guarded`), `:1279-1281`.
  **Permitted in this lane:** the two probe **bodies** (`make_db_probe` `:1463-1481`, `make_queue_probe` `:1716-1790`) — this covers **both** F4-A (the acquisition admission pre-check) and **F4-B** (the probe-scoped `SET LOCAL statement_timeout`) — the **derived** timeout constant (the existing `DB_PROBE_TIMEOUT_S` / `QUEUE_PROBE_TIMEOUT_S` **values do not change**), and the **docstring at `:107-113`** (A4-v — this must be added to the permitted set). **`_guarded` (`:1263-1296`) itself is NOT touched** — F4-A's synchronous `TimeoutError` is classified by the **existing** `except TimeoutError` branch (`:1284-1285`) ⇒ `probe_timeout` ⇒ grace-eligibility preserved.
- `daemon/constants.py` — `READINESS_GRACE_HORIZON_S=120.0` (`:165`), `READINESS_LEAK_TICK_THRESHOLD=3` (`:175`), `CHECKPOINT_POOL_MAX_SIZE_DEFAULT=10` (`:197`), `CHECKPOINT_POOL_TIMEOUT_S_DEFAULT=5.0` (`:205`).
- `daemon/api.py` — `:3939-3987` (probe wiring, `manager.engine.pool` access shape, `resolve_psycopg_pool`), `:5049-5087` (the no-composite `boot_incomplete` branch).
- `daemon/persistence.py:316-331` (P2 saver pool construction) — **knobs byte-identical**; **`:306-315` honesty comment changes per D-21/R12 only** (state both granularities in the gate, W-7).
- `daemon/persistence.py:225` — the **shared** `conn_string` (the asyncpg pool gets a **separate** DSN, F2.6).
- `scripts/upgrade/lib.sh` — the soak semantics (3 consecutive greens; **never-green unconditional at cap-reach**).
- `daemon/repositories/factory.py:184-199` (SQLite), `:210-214` (generic engine path), `:215-222` (latent config-driven PG).
- `daemon/tools/ens_db_tools.py:603-633` — every knob **except** the added `pool_reset_on_return="rollback"` and the `application_name=ensemble-repair` placed in the **repair engine's OWN conninfo** (**DR-31** — NOT appended to the options string, where the shared constant is interpolated last); the options string's other literals stay byte-identical.
- `daemon/tools/ens_db_tools.py:649-695` (vacuum NullPool) · `daemon/auth/cli.py:152-160` · `daemon/services/db_pool_manager.py:261-271` (external DBs).
- `daemon/write_pause_guard.py`.
- `daemon/migrations/versions/` — **zero** new `.sql` files this lane.

**Explicitly OUT of this lane (routed — stay routed):** the event-loop sync-DB defect (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) · the BFS depth-loop transaction span (`daemon/repositories/instance/repository.py:1434-1645`) · the default-executor ceiling (measured, not fixed) · the missed-finalize / RUNNING-stalled gap (own commission) · `mission_records` production-data remediation (mission-records data owner) · the proxy-side `coding-free` deprecated-row drop (user morning item).

**`daemon/manager.py` — new coupling row (W-8).** Allowed region: `_reconcile_deferred_report` (`:10809`), `_reconcile_deferred_report_async` (`:11466`), `_fetch_subshape_a_content_sync` (`:11172`), `_fetch_subshape_a_content_async` (`:11234`), plus the nested-session helper at `:10773` and the carrier check at `:11110`/`:11754`. Add a **diff-review leg restricted to those functions** to the phase gate. (Exact function boundaries must be re-verified at the implementation base.)

---

## §8 RELEASE POSTURE

### 8.1 Stage-only

- **STAGE ONLY.** **NO promote.** The promote flip is the **user's 3-factor nonce ceremony** — never performed by this lane, never by an agent.
- **NO daemon restart.** **NO `.env` touch.** **NO live/production DB contact.** **No prod-data remediation** (the W-4 index-presence SELECT is **read-only** and is recorded in the gate report, not acted on).
- The live daemon, the live release symlink and the promote journal are **untouched** throughout.

### 8.2 Version slot

**`v0.27.12` is PROVISIONAL.** At stage time the slot is **re-derived from the live census** (W9: no `releases/v0.27.12` directory, no local tag, no remote tag, and no other lane holding the slot). If another lane took `v0.27.12` in the meantime, this lane takes the next free slot. **Never hard-code the slot in an artifact.**

### 8.3 merge-latest-if-moved (mandatory, immediately pre-stage)

`origin/latest` **may move** before staging — the soak branch had an unpushed tip at `bd81b40d2` and a `stage.sh` live run was in flight. The L1 tip-identity guard **refuses a stale stage (exit 78)**; that refusal is a feature and **must not be overridden**.

1. `git fetch origin` and read the current `origin/latest`.
2. If `origin/latest` **equals** the lane's base — proceed.
3. If `origin/latest` **moved** — merge it into the lane branch (`--no-ff`, per house convention), then **re-verify the base**, **re-verify every `file:line`** in this spec (drift ⇒ re-derive), and **re-run the entire gate ladder** on the merged tree. A merged tip is a new base, not a footnote.
4. Re-derive the version slot (§8.2) **after** the merge.
5. **Only then** stage. If the L1 guard exits 78, **stop** — do not look for an override. Promote the staged artifact **as-is** if the tip has moved only in docs (`.agents/**`); a code-bearing tip requires a re-stage, not an override.

### 8.4 Artifact and rollback posture

- `rollback_safe` is adjudicated and recorded in the stage journal with its reasoning, per the standing rule (non-empty migration delta ⇒ explicit semantic reasoning, never a reflex).
- Expected migration delta vs the live release: **EMPTY** (no `.sql` file ships). The payload change is the binary plus the honesty comments; `agents/`, `frontend/`, `plugins/` payload trees are **byte-identical**.
- The staged artifact carries **both** provenance sidecars (binary + FE) and the staging worktree is **kept** for the ceremony.
- The stage log records the **measured** idle-in-txn bound and its command (C-4), or — **as is the case for this lane** — records that the bound shipped **DISABLED** with the Phase-0 reason (§2) and the enablement mechanism.

### 8.5 Honest statement of what this lane does and does not close

**Closes:** the transaction-abort gap (F3) · the **probe's acquisition wait** (F4-A) and its **statement bound** (F4-B, defence-in-depth) · the non-idempotent boot mint (F6) · the wedge-capture table bug (F5) · the manager-engine knob gap (F2) · **the dominant multi-second producer M-R3** (F8) · the attribution blind spot (the three named engines; the census scope widened to **all 11 real pool-creation sites**).

**Does NOT close — and must be stated in the shipped honesty blocks (F2.7):** the **dwell / occupancy** class. The recurrence precondition — **sync DB work on the event loop** (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) — is **not removed** by this lane, and the **BFS depth-loop** transaction span (`daemon/repositories/instance/repository.py:1434-1645`) is **not** touched. After this lane the daemon can still hold 15/15 manager slots; the guard's occupancy invariant (F7-e) is the thing that will see it, and the follow-up is the **owner** of the residual.

**This lane does NOT close the incident's symptom class.** Stated plainly so no future reader infers otherwise.

### 8.6 The honesty blocks must carry the baseline's disclosed limits (DR-36)

In addition to F2.7's text, the shipped honesty blocks must record — **as disclosed limits, never as measured facts**:

1. The **real** `_fetch_subshape_a_content_async` latency was **NOT measured** (the harness modelled the python-side half).
2. The **real** `ChildReportsService._get_last_assistant_message` python-side cost was **NOT measured** (a disclosed stand-in).
3. The incident-window **mint overlap is INFERRED** (the trigger class is measured; the exact scheduling in that boot is not).
4. The `pool_timeout` stall was measured at **3 s** (a harness override to fit the pack budget) — **not** the 30 s default.
5. The **engine-level idle-in-txn backstop ships DISABLED** (Phase-0 reason + enablement mechanism) — the daemon therefore has **no** server-side idle-in-txn reap on the manager engine.
6. **F4-A's residual:** a checkout that loses the pre-check race may still strand the probe thread for up to `pool_timeout` = 30 s.

---

## §9 PIN → TWIN MAP (DR-15 — checkable at a glance)

**Every PG/integration pin has a default-collected static twin (W-13).** One row per pin ⇒ its twin. Pins with no twin are listed explicitly, with the reason.

| Pin (PG / integration) | Its default-collected static twin | The static facts the twin asserts |
|---|---|---|
| **F4-b** (`tests/integration/test_dbpool_probe_bound_pg.py`) | **F7-b-static** (`test_dbpool_probe_retention_static.py`) | `SET LOCAL statement_timeout` present in `make_queue_probe`; derived value == `2 * QUEUE_PROBE_TIMEOUT_S`; the **saver** probe excluded; the `readiness.py:107-113` docstring premise corrected |
| **F4-f** (`tests/postgres/test_dbpool_probe_starvation_pg.py`) | **F4-a** (`test_dbpool_probe_bound.py`) | the **F4-A admission pre-check** is present in both probe bodies (the mechanism F4-f exercises) |
| **F5-b** (`tests/postgres/test_dbpool_wedge_capture_pg.py`) | **F5-c** (`test_dbpool_wedge_capture_static.py`) | no `tasks` / `tasks t` in the module |
| **F6-b**, **F6-c** (`tests/postgres/test_dbpool_mission_mint_pg.py`) | **F6-a**, **F6-e**, **F6-c-static** (`test_dbpool_mission_mint.py`) | the upsert is anchor-keyed; the anchor-scoped re-resolve is on the `rowcount == 0` path; `OPEN_EPOCH_STATUS_SQL` is byte-identical to the partial index predicate |
| **F7-a**, **F7-b** (`tests/integration/test_db_pool_idle_in_transaction_guard.py`) | **F7-a-static** (`test_dbpool_residence_static.py`), **F7-b-static** (`test_dbpool_probe_retention_static.py`), **F7-c** (self-pin, `test_dbpool_guard_pack_selfcheck.py`) | the residence constant exists and the guard references it; the guard's metric is `xact_age_s`; the retention path's static facts hold; the self-pin re-reads the source |
| **F7-e** (`tests/integration/test_dbpool_churn_census.py`) | **F7-e-static** (`test_dbpool_occupancy_static.py`) | no `await` / bridge call inside a manager-engine `with`-body; guards re-asserted in P2; `inj` re-read in P2; one-transaction write |
| **F8-b**, **F8-c**, **F8-d**, **F8-e** (`tests/integration/test_dbpool_deferred_report_reconcile_pg.py`) | **F8-a** (`test_dbpool_fix_site_static.py`), **F7-e-static** (`test_dbpool_occupancy_static.py`) | the AST shape (no `await` in the `with`-body; the bridge **outside** the scope); the guards re-asserted; the single-transaction write |

**Pins with NO twin, and why (each is a default-collected unit or process pin already):** F1-a · F2-a..j · F3-a..e · F4-a · F4-c · F4-d · F4-e · F5-d · F6-d (process) · F7-c · F7-d · G-A · G-consistency.

**Two pins whose twins were previously claimed but were NOT (DR-24, corrected here):** **F6-c** is itself a `postgres` pin and **F6-d** is a **process** gate ⇒ neither is a twin; **F6-c-static** is the real twin. **F7-c** is a self-pin that deletes the assertion **and** the constant ⇒ deleting the **residence assertion alone** leaves F7-c **GREEN** ⇒ F7-c is **not** a backstop for F7-a; **F7-a-static** is.

---

*BINDING. Produced by the reviewer (dispatcher/controller) from the governor council (`24a5d779-09dd-465b-b9d2-899b88f3f56b`, 2 councilors) plus two code-review workers (`856436de-2f09-4b21-ab9a-5dc6e361ecae`, `183cd3fe-977f-41f3-b87b-170138f9dc2e`). Every `file:line` is at base `19fc11c8b` and was re-verified by the reviewer. Read-only review: no daemon/, tests/, scripts/ or payload-tree file was modified.*
