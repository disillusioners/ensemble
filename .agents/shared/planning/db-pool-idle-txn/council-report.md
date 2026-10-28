# Reviewer Council Report — db-pool-idle-txn fix lane (plan + architect amendments)

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`) — "DB pool poisoned by leaked idle-in-transaction connections".
- **Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn`.
- **Base commit for every `file:line`:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (= `origin/latest` = the staged **v0.27.11** soak-gate tree).
- **Artifacts reviewed:** `.agents/shared/planning/db-pool-idle-txn/` — `plan-overview.md`, `decisions.md`, `audit-method.md`, `phase1-plan.md`, `phase2-plan.md`, `phase3-plan.md`, `test-strategy.md`, `research-audit-census.md`, `research-leak-sites.md`, `research-pool-couplings.md`, `wi-78967dba-verdict.md`, `architecture-recommendation.md` (12 artifacts, 3256 lines).
- **Plan commit:** `23c232b8d` (docs only). **Architect commit:** `a1b38260f` (docs only). `git diff --stat 19fc11c8b a1b38260f -- daemon/` is empty ⇒ code tree == base.
- **Date:** 2026-10-28.

---

## 1. VERDICT

# 🔴 REJECT

Three independent lanes — a 2-council governor council, a causal-completeness worker, and a test-gate worker — converged on the same verdict without a refinement round. **The plan is not implementable as written.** It must be folded and re-reviewed before a single line of code is written.

The council's own two councilors returned **REJECT independently** (2/2, no councilor failures, `cleared=true`). The council merged 13 + 22 raw findings into 8 CRITICAL / 11 WARNING / 5 ADVISORY. Two of the eight criticals are **corrections to the architect's amendment set**, not to the plan — a point the architect's own document did not reach.

**Why REJECT and not APPROVE-WITH-ADVISORIES:** four findings are *unachievable gates* or *leader-ruling violations*, i.e. the lane as specified would either (a) execute a gate that cannot pass, (b) execute zero tests and read it as green, (c) build a regression guard that would be **green on the incident's own shape**, or (d) land a knob the leader explicitly ruled DROP. None is closable by the implementer without either an unauthorised change or a weakened safety property.

**What must NOT be lost in the fold** — the plan's research core is genuinely strong and should be preserved verbatim: the RCA premise ledger (3 of 4 original attributions correctly refuted with citations), the 0-unclosed-borrow census, the C1 rationale for rejecting a dedicated probe pool, the D-17 repair-engine isolation, the zero-new-`.sql` claim, and the phase sequencing. See §7.

---

## 2. Independent verification of the architect's causal-completeness claim

Three lanes ran this arithmetic independently. Two (councilor A and councilor B) computed M-R3's occupancy contribution at **1.3 slots** from different derivations; the causal worker reproduced the same order of magnitude and added the bound corrections. **The architect's HOLE finding is CORRECT: the plan's hazard set {A, D} does not explain the incident, and M-R3 is a real multi-second producer.** But the chain **does not close even after F8**.

### 2.1 Pool capacity at base `19fc11c8b`

| Pool | Site | Capacity | Can it be `idle in transaction`? |
|---|---|---|---|
| **P1** manager (SQLAlchemy/psycopg sync) | `daemon/repositories/factory.py:323-338` | **15** (`pool_size=5` + `max_overflow=10`) | **YES** — `Session` autobegins; no autocommit/isolation set; **no server-side reap** |
| **P8** repair | `daemon/tools/ens_db_tools.py:603-633` | **5** (2+3) | YES, but reaped at 60 s (`REPAIR_IDLE_IN_TXN_TIMEOUT_MS`, `:188`); granted to exactly one agent (`agents/maintenancer/meta.json:14`) |
| **P2** saver (psycopg3) — the leak **ORACLE** | `daemon/persistence.py:316-331` | **10** (`CHECKPOINT_POOL_MAX_SIZE_DEFAULT`, `daemon/constants.py:197`, clamp [5,20]) | **NO** — `kwargs={"autocommit": True}` (`:328`) |
| **P2b** asyncpg checkpoint adapter | `daemon/persistence.py:355-359` | **10** | **YES** — `daemon/checkpoint_adapter.py:1229-1239` opens `async with conn.transaction():` with three `await`s between DELETEs ⇒ the backend sits `idle in transaction` **between statements**; **no server-side reap** |
| vacuum NullPool | `daemon/tools/ens_db_tools.py:649-695` | 1 | NO (`autocommit`) |
| boot preflight / wedge-capture one-shot / external DB pools | `daemon/__main__.py`, `daemon/services/wedge_capture/db_capture.py:196-200`, `daemon/services/db_pool_manager.py:57` | 1 each | one-shot / external |

Daemon-internal ceiling ≈ **40**. Observed at relapse: **18** open backends (baseline 8–9), **6** `idle in transaction` (earlier census 5/16). API latency 5–8 s, CPU 104–119 %, readiness degraded, 503s on `/api/prompts/pending-count`.

Manager-engine knobs at base: `pool_pre_ping=True` **already set** (`:328`); `pool_reset_on_return`, `pool_recycle`, `pool_timeout` **absent** (library defaults `'rollback'`, `-1`, `30.0`).

> **Correction to the council's own capability table.** The council recorded the asyncpg adapter pool as "NO (per-statement autocommit)". That is **factually wrong** — `daemon/checkpoint_adapter.py:1231` opens an *explicit* `conn.transaction()`. Verified directly at base by the reviewer. This is finding **C-8** below; the council's two-engine `application_name` model is therefore incomplete in a way that would have left the *exact class that matters* unattributable in the next census.

### 2.2 Step 1 — the 18 exceed P1's 15-slot cap ⇒ attribution is undecidable

At base every daemon connection carries an **empty `application_name`** (`factory.py:252` = `{"options": "-c timezone=UTC"}`; the only setter in the tree is the one-shot wedge-capture connection, `db_capture.py:198`). With 18 observed backends and a 15-slot manager cap, the census **spans pools** ⇒ *"the 6"* **cannot** be assigned to P1. The incident is formally undecidable as it stands. ⇒ the architect's A4/A5 attribution correction is **correct**.

### 2.3 Step 2 — "6 idle-in-txn" cannot produce the measured symptoms

6 of 15 slots = 40 % occupied, **9 free** ⇒ no checkout ever queues ⇒ `pool_timeout=30 s` is **unreachable** ⇒ the 0.5 s DB-probe budget and the 5–8 s `/api/instances` latency **cannot** be caused by pool occupancy at the sampled instant. Saturation requires ≈ 15/15.

Causal decomposition (all three lanes agree):

| Symptom | Explained by pool occupancy? | Actual cause |
|---|---|---|
| `/api/instances` 0.08 s → 5–8 s | **NO** | **sync DB work on the event loop** — `daemon/routers/instances.py:471-483` calls sync `manager.list_instances(...)` with **no `await`**; `daemon/routers/queues.py:653-654` calls sync `resolver.resolve()` on the loop |
| `/readyz` red (`probe_timeout`) | **NO** | the same loop peg (the `asyncio.wait_for(to_thread(probe), 0.5)` budget is consumed before the probe thread is even scheduled) **+** the default-executor ceiling (`asyncio.to_thread` ⇒ `min(32, cpu+4)` = **12** workers on this 8-core host; **1351** `to_thread`/`run_in_executor` sites in `daemon/`) |
| 6 `idle in transaction` backends | — | a **SYMPTOM** of the two above |
| CPU 104 % | **NO** | retry pressure around the starved loop |

⇒ **The architect's §1.1 arrow "6 idle-in-txn ⇒ checkout waits ⇒ probe timeouts" is a non-sequitur.** The supported arrow is the reverse: *loop-blocking ⇒ dwell stretch ⇒ (idle-in-txn ↑) AND (probe timeouts) AND (API latency)*. Occupancy is a **consequence**, and the plan scopes its actual cause **out** (`plan-overview.md:34`).

### 2.4 Step 3 — Little's law per producer (occupancy = λ × W)

- **M-R3** (the architect's named dominant producer): λ = 29 `[RESUME]` lines / 180 s ≈ **0.161/s**; W ≤ 8.0 s (sync twin, `manager.py:11217-11224`) ⇒ **occupancy ≈ 1.3 slots ≈ 22 % of the observed 6.** **It does not close the chain.** The causal worker adds two corrections the architect missed: (i) the sync twin's true worst case is **≈ 38 s**, once the nested session (`manager.py:10773`) and the ≤ 30 s `pool_timeout` are counted; (ii) the **async** twin's hold is **unbounded** — `manager.py:11265-11271` has no `wait_for` at all.
- **M-STRETCH** (dwell): λ ≈ **8 borrows/s** (29 turns × ~50 borrows + UI polling, per 180 s) — an **order-of-magnitude assumption, not a measurement** (no borrow counter was captured). Solving W two ways: from the count, W ≈ 6/8 ≈ **0.75 s**; from the latency (5–8 s request latency with an open scope-transaction), W ≈ **5–8 s**. These differ **~10×**. Under W = 0.75 s the pool sits at ~6/15 ⇒ contradicts the incident. Under W = 5–8 s: occupancy = 8 × 5…8 = **40–64 ≫ 15** ⇒ P1 saturates ⇒ 30 s `pool_timeout` waits ⇒ 5–8 s API latency ⇒ CPU ⇒ readiness red ⇒ retry pressure ⇒ **fully self-consistent**.
- ⇒ **The dominant term is request-scope RESIDENCE (dwell), and neither lever that reduces it ships in this lane** (event-loop sync-DB and the BFS depth loop, `daemon/repositories/instance/repository.py:1434-1645`, are both explicitly out of scope).

### 2.5 Step 4 — the 60 s backstop does not bind

Saturation is prevented only if λ × 60 s < 15 ⇒ **λ < 0.25 borrows/s**; the observed λ ≈ 8/s is **32× above** that threshold. It fires only after **60 continuous** idle-in-txn seconds (PG resets the timer on every statement) ⇒ the **first 60 s of the 180 s window is unprotected**, and at the observed 0.75–8 s residence it never fires at all. A session parked in a **checkout** wait holds no transaction, so it cannot fire there either.

What it *does* buy — correctly stated as **containment, not mitigation**: a hard ceiling on any single backend's continuous idle residence, converting an unbounded stall into a **loud** failure (PG `FATAL … idle-in-transaction timeout`, SQLSTATE `25P03`); the only server-side backstop on the manager engine; and a bound on maximum occupancy to λ × 60 s (useless at the incident's λ, decisive at λ < 0.25/s). Its collateral risk is **real and currently unmeasured** — see C-4.

### 2.6 Step 5 — verdict, and the residual hole

> **The architect's HOLE finding is CORRECT and M-R3 is REAL — all three lanes verified both twins at base, and the causal worker proved empirically (`in_transaction=True`, `pool.checkedout()==1`, *the same DBAPI connection object* held across the suspension point) that the ORM does not release the connection before the `await`.**
>
> **But the chain does not close even after F8.** M-R3 accounts for ≈ 1.3 of 6 idle-in-txn slots; the dominant W ≈ 5–8 s dwell term is **unreduced**; and the lane's own guard (F7) would be **GREEN** on the incident's shape (see C-10). A v0.27.12 staged on this basis re-opens the incident under the same load — which is exactly the architect's R13, and it is **worse** than R13 states.

### 2.7 The missing measurement (the concrete ask)

On the **same** repro, capture: **(i)** `manager.engine.pool.checkedout()` sampled ≤ 1 Hz (in-memory; the sampler shape already exists at `readiness.py:525-549`) — the only metric that sees occupancy, and it yields λ and W directly (occupancy = ∫checkedout dt / window); **(ii)** `pg_stat_activity` **by `application_name`** (A5), counting `idle` **and** `idle in transaction`, with `state_change` **and** `xact_start` for residence; **(iii)** the reconcile's own hold duration (F8's flip condition) via sub-second `checkedout()` deltas across the reconcile call; **(iv)** a bucketed **residence-time distribution** to distinguish "6 short borrows" (benign, λ-driven) from "1 long leak" (the actual bug). Without (i) the lane cannot say whether F8 moved the metric; without (ii) the census cannot attribute; without (iii) the flip condition is undecidable; without (iv) F7 cannot be falsifiable.

---

## 3. Findings

### 🔴 CRITICAL (12)

#### C-1 — C3 is both **inert** and **unfenced**; the plan's entire false-green defence executes zero tests

**WHERE** `test-strategy.md:447` (§2.3 fence script), `:405-423` (Class 4a/4b), `:640` (G1.5 — "the base run's collected count is **recorded**"), `:660` (G3.5 — diff method only); `phase3-plan.md:48`.

**WHAT / WHY** The gate interpreter `/usr/bin/python3` has **no pytest** — verified: `ModuleNotFoundError: No module named 'pytest'`. Therefore the §2.3 fence *always* reads `COLLECTED=0 → FATAL`; the unfenced Class-4a/4b execute **zero tests** and read "0 failed" as GREEN **on the C1/C2 non-interference gate**; and the two whole-tree legs assert nothing, so a 0-collected run yields an empty FAILED set on *both* sides ⇒ "net-new = ∅" ⇒ GREEN having executed nothing. This is precisely the class C3 exists to defeat, reproduced inside the lane that is fixing it.

**REQUIRED CHANGE** Interpreter → **`/home/nea/ensemble-src/.venv/bin/python`** (verified: pytest 9.0.2, py 3.13.15; `import daemon` resolves to the **worktree** when cwd = worktree — verified `daemon → /home/nea/ensemble-src-wt-dbpool-idletxn/daemon/__init__.py`, version 0.27.11). **Drop `uv run`** — `uv` is **off-PATH** (`/home/nea/.local/bin/uv`) and the worktree has **no `.venv`**, so every Form-I command triggers a network-dependent dependency resolution *at gate time* (the same failure mode that killed `bump_version.py`'s uv-sync step in two prior lanes). Exact fence:

```
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" /home/nea/ensemble-src/.venv/bin/python -m pytest <paths> --collect-only -q
```

`env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) — scrub **per variable**. Assert `collected > 0` on **both** whole-tree legs and reconcile `base_collected`/`head_collected` against the sum of the declared `EXPECTED_COLLECTED_*`; mismatch = **FATAL**. Add the `import daemon` certification print as a **mandatory first step + gate artifact**. Apply to Classes 1/2/3/4a/4b **and** the §2.3 script. Gates MUST NOT run from the shared checkout.

#### C-2 — A5 / AC14 / AC15 / AC16 / T2.4 / T2.6 / D-19 / G2.4 / F2-f / F2-g are **mutually unsatisfiable**

**WHERE** `phase2-plan.md:74`, `:78`, `:90-92`; `test-strategy.md:129-134`, `:530` (F2-g), `:650` (G2.4); `decisions.md:39` (D-24); `architecture-recommendation.md:156` (A5), `:211` (R17); `daemon/repositories/factory.py:252`; `daemon/tools/ens_db_tools.py:619-627`; `phase3-plan.md` D-23(c)/D-24.

**WHAT / WHY** PG's **last `-c` wins**. `application_name` placed in the shared `PG_SESSION_CONNECT_ARGS` ⇒ the **repair** engine reports the **manager**'s name, destroying the very attribution A5 introduces; placed in the repair engine's own literal ⇒ **AC15's byte-identity breaks**. F2-g is **RED from birth** (the architect concedes this) and G2.4 ("GREEN and unmodified") is **unachievable**. A5's amendment list names only "AC14/AC16 + F2-g" and **omits AC15, T2.4, T2.6, D-19, G2.4, F2-f**. A **fourth** conflicting artifact: `phase3-plan.md` D-23(c) and D-24 rule `application_name` **out of scope** (attribution by scratch-DB isolation) — so A5 and the plan's own Phase 3 directly contradict. The council also noted the shared constant has **three** consumers (`factory.py:210-214` generic engine path, `:337`, `ens_db_tools.py:625`); the generic path is named by neither artifact.

**REQUIRED CHANGE** Amend **AC14 + AC15 (second half) + AC16 + T2.4 + T2.6 + D-19 + D-24 + G2.4 + F2-f + F2-g in ONE change**. The shared constant stays **byte-identical** (D-17 intact, already fenced by `tests/unit/repositories/test_pg_engine_utc_session.py:65`). New `MANAGER_PG_SESSION_CONNECT_ARGS` = `-c timezone=UTC -c application_name=ensemble-manager -c idle_in_transaction_session_timeout=60000` (the `statement_timeout` is dropped — see C-3). The repair engine's **own literal** appends `-c application_name=ensemble-repair`; because the value **differs**, there is no last-`-c` collision — but the pins must assert each value appears **exactly once**, that the two **differ**, and that the shared constant carries **no** `application_name`. **Option order is load-bearing** — assert it in both pins. Per the council's adjudication ①, keep D-24 (scratch-DB isolation) for the guard's attribution, re-scope F2-g to *"by `application_name` where present, else by scratch-DB isolation"*, and restate `test-strategy.md:303` to match.

#### C-3 — Leader ruling violated: the engine-wide `statement_timeout=30000` is still live in Phase 2

**WHERE** `decisions.md:31` (D-16); `phase2-plan.md:3` (objective), `:16`, `:28`, `:49`, `:74`, `:90`; `architecture-recommendation.md:109` (OQ-3).

**WHAT / WHY** The leader ruled (and the architect's OQ-3 accepted) that the engine-wide `statement_timeout` is **DROPPED** — but D-16, T2.2, AC14 **and the Phase-2 objective text** still carry it. `daemon/manager.py:8760-8762` runs the **whole** `_ensure_postgres_columns` statement list inside **ONE** transaction; the code itself documents the JSON→JSONB step as *"FULL TABLE REWRITE with ACCESS EXCLUSIVE lock… may take seconds"* (`manager.py:7877-7879`); the method is **fail-loud** ("startup aborts", `manager.py:7554-7560`) ⇒ a 30 s cap firing there means **the daemon does not boot** — on a path that is a **no-op at steady state**, so the hazard is invisible in normal measurement.

**REQUIRED CHANGE** Drop `-c statement_timeout=30000` from the manager engine in **D-16, T2.2, AC14 and the Phase-2 objective text**; record the DROP as a leader ruling (not a preference). Resulting manager options string: `-c timezone=UTC -c application_name=ensemble-manager -c idle_in_transaction_session_timeout=60000`. Document that any future global cap must `SET LOCAL statement_timeout = 0` inside `manager.py:8760`.

#### C-4 — Leader ruling R15 violated: the idle-in-txn bound has **no ordering rule, no named instrument**, and a derivation that mixes a pre-F8 *code constant* with a post-F8 *target*

**WHERE** `plan-overview.md:163` (R2), §13 item 2; `decisions.md` D-16; `phase2-plan.md:28`; `architecture-recommendation.md:87`, `:158`, `:209` (A6/R15).

**WHAT / WHY** The "8 s" in the margin argument is the **literal** `.result(timeout=8.0)` at `manager.py:11217-11224` — a *configured timeout*, which the architect's own A4-iii reclassifies as a **checkout wait**, not an observed hold; mixed with A6's post-F8 "≤ 2 s" *target* ⇒ 60 s is **30×**, not 7×. No instrument, sample rate, or threshold source exists anywhere. The causal worker adds that **PG resets the idle timer on every statement**, so the bound counts **continuous** idle: M-R3's legitimate dwell is ≈ 8 s (bridge) + ≤ 5 s (P2 acquisition) + `aget` ≈ **13–15 s ≪ 60 s** ⇒ **M-R3 is *not* the collateral victim** — the bound's real victim class is **unmeasured**, and R15 as written names the wrong one. The council adds that the **async twin's hold is unbounded** (`manager.py:11265-11271`, no `wait_for`) ⇒ a "legitimate hold budget" cannot even be sized around it.

**REQUIRED CHANGE** (a) F8 becomes a hard **task-level predecessor** of the bound, not prose. (b) **Instrument**: one reconcile with a slowed content fetch, sampling `manager.engine.pool.checkedout()` at ≤ 100 ms + `pg_stat_activity` `state_change`/`xact_start` residence of the reconcile's backend. (c) **Rule**: `bound ≥ 4 × measured max legitimate hold`, floor 30 s; the measurement and its command recorded in the gate report. (d) If unmeasurable at stage time, the bound ships **DISABLED** with the constant defined and enablement gated on the measurement. (e) Bound or explicitly exclude the unbounded async twin.

#### C-5 — F8 as specified does **not** remove the hold; it requires a scope **SPLIT** with three new invariants and a double-delivery risk

**WHERE** `daemon/manager.py:11515-11571`, `:10860-10930`, `:11281-11440`; `architecture-recommendation.md` A1.

**WHAT / WHY** A1's literal shape ("hoist the content fetch before opening the scope", "use a separate short-lived session") **does not remove the hold**: the main scope has already autobegun at `:11517` and `:11556`, so its backend is idle-in-txn across the `await` **wherever the fetch's own session lives**. The only workable shape is **splitting** the scope — and splitting introduces two correctness hazards the plan and A1 never name: (i) `inj` is detached/expired after the first scope closes, but `_create_subshape_a_artifacts` reads `inj.parent_instance_id` at `:11366`/`:11383`/`:11400`/`:11421`/`:11430`; (ii) the **terminal guard** (`:11529-11541`) and the **dead-parent guard** (`:11555-11561`) are evaluated on a **pre-await snapshot**, so a concurrent escalation between the scopes is **not caught** ⇒ **double delivery**; (iii) write atomicity (message INSERT + task INSERT + injection UPDATE in one transaction) must be preserved.

**REQUIRED CHANGE** Specify F8 as a named three-phase shape: **P0** read-decide scope (read `inj`, `parent_row`, child `agent_id`) → close; **P1** content fetch with **no** manager-engine transaction open; **P2** write scope: **re-read `inj`**, **re-assert** the terminal + dead-parent guards, then `_create_subshape_a_artifacts`. Change both fetch helpers' signature from `session=` to `child_agent_id: str` (they do `session.get(Instance, …)` at `:11208`/`:11260`). **New pins**: (a) a concurrent escalation injected at the P0→P2 seam ⇒ **no** artefacts created; (b) message INSERT + task INSERT + injection UPDATE still commit in **one** transaction; (c) A1 pin (i), extended to all sites (see C-6).

#### C-6 — F8 is under-specified: **three** borrow-then-await sites, not one — so A1's own pin (i) stays RED

**WHERE** `daemon/manager.py:11516` (scope) with awaits at `:11568-11571` (content fetch) **and** `:11754-11757` (`await asyncio.to_thread(self._has_live_process_report_carrier, …)`, whose callee opens **its own** manager-engine session at `:10773`); `daemon/tools/instance.py:1207` (`with WriteGuardSession(Session(manager.engine), manager.write_guard)`) with `await _bus.watch(...)` at `:1360`.

**WHAT / WHY** Implementing A1 as written leaves the `:11754` thread-hop **inside** the scope ⇒ pin (i) stays RED and the implementer must invent an **unauthorised second fix**. Repo-wide, the pin is **RED from birth** on `instance.py:1360`, which has no task attached. F8 is the lane's **only** mechanism item. Both the council and the causal worker found the `manager.py:11754` site independently; the council found `instance.py:1207`/`:1360` as well.

**REQUIRED CHANGE** Extend F8 to **all three** sites — compute `live_carrier_exists` and register the bus watch **before** opening the scope. **Scope pin (i) to the `with`-body only** (both councils: a function-wide AST rule false-positives on the legitimate bridge calls at `:11217-11224`/`:11568-11571`): assert no `ast.Await` node and no `run_coroutine_threadsafe(...).result(...)` inside any manager-engine session scope in `daemon/`; **name the three expected sites** in the pin docstring so a fourth is a deliberate failure. Leader decision required: may F8 touch `daemon/tools/instance.py`? If not, the pin must be scoped narrowly and the residual **disclosed** (it is a write path under `WriteGuardSession`) — and `plan-overview.md` §9 needs a `daemon/manager.py` coupling row (see W-8).

#### C-7 — F4-b / D2 asserts a PG state combination that **cannot occur** ⇒ the pin is green-from-birth

**WHERE** `test-strategy.md:237-247` (D2), `:255`, `:518` (F4-b); `architecture-recommendation.md:124` (A9).

**WHAT / WHY** A lock-blocked backend is `state='active'` with `wait_event_type='Lock'`; an `idle in transaction` backend waits on the **CLIENT** (`wait_event_type='Client'`). They are **mutually exclusive**. Therefore F4-b's "0 `idle in transaction` backends" assertion is **TRUE AT BASE** ⇒ false green, and **AC7 is vacuous**. Separately, F4-b's named mutation removes an **engine-level** knob that F4 does not install (F4 installs a probe-scoped `SET LOCAL statement_timeout`) ⇒ the mutation is a **no-op for F4** ⇒ the pin is not mutation-meaningful. The architect states the PG semantics correctly (A9) but applies them only to the boot DDL.

**REQUIRED CHANGE** D2's expected signal → `state='active'` + `wait_event_type='Lock'`. F4-b asserts (i) the lock-blocked backend is **terminated** (row gone from `pg_stat_activity`) within the bound **and** (ii) `engine.pool.checkedout()` returns to baseline within the bound; the census count is asserted only **after** the lock is released and the client-side bound elapsed. F4-b's mutation → **remove the `SET LOCAL statement_timeout` from `make_queue_probe`** (that does redden: the statement stays blocked, the connection stays checked out).

#### C-8 — A5's two-engine `application_name` model misses a **third** pool that can hold `idle in transaction` backends with **no** server-side reap — and the naive fix breaks the P2 oracle pin

**WHERE** `daemon/checkpoint_adapter.py:1229-1239`; `daemon/persistence.py:355-359` (asyncpg pool), `:225` (shared `conn_string`) vs `:317` (P2 psycopg saver `conninfo=conn_string`); `phase2-plan.md` T2.7 / AC18; `architecture-recommendation.md` A5.

**WHAT / WHY** The asyncpg checkpoint-adapter pool opens an **explicit** transaction — `async with conn.transaction():` with three `await`s between DELETEs (`checkpoint_adapter.py:1231-1241`) ⇒ its backend sits `idle in transaction` **between statements**, with no `idle_in_transaction_session_timeout` to reap it. A5's two-engine model (`ensemble-manager` / `ensemble-repair`) therefore leaves the third candidate **unattributable in the next census** — i.e. exactly the class that matters. Worse: the asyncpg pool is built from the **shared** `conn_string` (`persistence.py:225` → `:356`) that also feeds the P2 psycopg **oracle** (`:317`), so naively adding `application_name` to it **breaks the P2-immutability pin (T2.7) and AC18** — i.e. it would put the C1-protected oracle surface into the lane's diff.

> **The council got this one wrong** (its capability table recorded the asyncpg pool as "NO — per-statement autocommit"). The reviewer verified `checkpoint_adapter.py:1229-1239` directly: the explicit `conn.transaction()` is there. Worker A was right; the council is corrected here.

**REQUIRED CHANGE** A5 must name a **third** `application_name` (`ensemble-checkpoint-adapter`) carried on a **separate DSN** for the asyncpg pool only — **do not** touch the shared `conn_string` (`persistence.py:225`). Amend **AC18** and **T2.7** in the same change; add the asyncpg pool to D-13's engine list (it moves from "NOT touched" to "touched"); drop A5's saver-pool clause so no clause is left without a landing task.

#### C-9 — G2.4 "unmodified" is **unachievable** — the plan requires a change that breaks a pin the plan itself declares protected

**WHERE** `phase3-plan.md:49` (G2.4); `phase2-plan.md:77` (T2.5); `test-strategy.md:530` (F2-e); `tests/unit/repositories/test_pg_engine_utc_session.py:86-88`.

**WHAT / WHY** The file asserts `captured["kwargs"]["connect_args"] == {"options": "-c timezone=UTC"}` — an **exact equality** on the manager engine's connect_args. F2 **must** change it (AC14). ⇒ G2.4 is unachievable, T2.5's "the file's existing assertions stay green" is false, and the implementer's only exits are: **modify a protected pin**, **fail the gate**, or **weaken it** — all three are C3 anti-patterns. D-17's own guard lives in the same file at `:65`.

**REQUIRED CHANGE** New **D-xx** recording an explicit **pin amendment**: amend `:86-88` to the new manager options string (value-by-value, **order-pinned**); **keep `:65` untouched** (`PG_SESSION_CONNECT_ARGS == {"options": "-c timezone=UTC"}` — the D-17 guard); correct F2-e's stale `:292` anchor → `:86-88`; G2.4 becomes "amended per D-xx, diff attached to the gate report". The file is 147 lines and is **not** in `SEALED_SHA_BASELINES` ⇒ **no seal re-pin ceremony is needed** (verified by the council).

#### C-10 — F7's census metric is **BLIND to the dominant occupancy** ⇒ F7 would be **GREEN on the incident's own shape**

**WHERE** `test-strategy.md:113-126` (census SQL), `:296-299`, `:586-587`; `architecture-recommendation.md` A2.

**WHAT / WHY** `idle in transaction` requires the connection to have **issued a statement**; a connection checked out **before its first statement** is PG state `idle`, **not** `idle in transaction`. After this lane lands, the daemon can still hold **15/15** manager slots with only **6** in `idle in transaction` and **9 idle-but-checked-out** — the incident's actual shape — and **F7-a/b would be GREEN**. The guard would not have caught the incident and will not catch its recurrence. This is the most consequential test-design finding in the review: it makes the lane's regression guard non-load-bearing.

**REQUIRED CHANGE** F7 must assert **both**: (a) `manager.engine.pool.checkedout()` returns to baseline — promote A2-1's primary invariant from secondary to **co-equal**; and (b) the census count/residence. PG state `idle` attribution becomes a **disclosure metric, not an assertion**.

#### C-11 — No release posture exists in **any** plan artifact

**WHERE** all 12 artifacts; the only `v0.27.12` mention is architect commentary (`architecture-recommendation.md:20`, `:207`); the lane's KV record ("informational; W9 re-derivation at stage time") sits **outside** the plan set.

**WHAT / WHY** (a) The base tree moved **33 commits** during the soak lane (merge `434fd96b4`) ⇒ the L1 tip-identity guard refuses a stale stage (exit 78) and the plan has **no merge-latest-if-moved step**. (b) **No artifact forbids promote / daemon restart / `.env` touch / prod-DB contact at the release boundary** — this project's promote lane is a **user-nonce ceremony**, and an implementer reading only these artifacts could **stage *and* promote**. (c) `rollback_safe` is never adjudicated.

**REQUIRED CHANGE** Add a **Release posture** section — see `final-implementation-spec.md` §8 for the binding text.

#### C-12 — The flip condition is **undecidable** — no F8 pin exists in the pin inventory, and D2 cannot deliver the slow fetch

**WHERE** `test-strategy.md:606-616` (coverage matrix ends at F7), `:268-320` (`:274` "the daemon is NOT run in this lane"), `:237-247` (D2); `architecture-recommendation.md:143` (A1 pin ii), `:196`; `daemon/services/child_reports.py:1654`.

**WHAT / WHY** The leader ruled the flip condition is settled **by the tester** and that the plan must **instrument** for it. It cannot: no F8 pin exists in the inventory; the churn generator never touches `manager.py`; and D2's `BEGIN; LOCK TABLE task IN ACCESS EXCLUSIVE MODE` targets the **queue probe's** `FROM task` read, whereas the reconcile awaits a **checkpoint read on the P2 saver pool** (`child_reports.py:1654` → `get_instance_messages(self._checkpointer, …)` → `persistence.py:316-331`, `autocommit=True`) — **different table, different pool**. ⇒ **F8 ships without ever measuring whether it moved the metric**, and the architect's "Confidence: Medium-High" is unearned.

**REQUIRED CHANGE** Add **F8-a** (static, default-collected) and **F8-b** (PG) to `test-strategy.md` §3 with the reddening mutation for each; add F8 to the §3.9 coverage matrix; add a **reconcile leg** to §1.3 driving `_handle_recover_deferred_report_async` against scratch PG with an instrumented slow checkpointer (a stub whose `aget_tuple` sleeps a known duration) or a lock on the checkpoint table in the saver's DB; state that D2 is reused **only** for the F4 leg; make the flip measurement (sub-second `checkedout()` + `pg_stat_activity` residence, BASE vs HEAD) a **gate artifact**.

### 🟡 WARNING (14)

- **W-1 — F7's re-specified invariant is not measurable; "λ×W" is unfalsifiable.** `test-strategy.md:113-126`, `:296-299`, `:587`; `architecture-recommendation.md:145-152`. The census helper selects `state_change` but **not** `xact_start`, which A2/A3 both require; no named threshold constant, home, or value; §1.3.1 still asserts "0 at every sample"; F7-b's stated mutation produces **exactly** the long residence a count-based census misses ⇒ the pin is not provably RED-able. The `backend_start > run_start` BASE filter is **prescribed** at `test-strategy.md:132-134` but **not implemented** in the helper's SQL. **Fix:** add `xact_start` + `backend_start` to the SELECT; pin the residence source to **one** (`state_change` — it is the entry into the idle-in-txn state; `xact_start` is total transaction age and is a *different* quantity); name the constant (`DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`, home `daemon/constants.py`); replace "λ×W" with `peak_occupancy ≤ λ_measured × budget` where λ comes from the leg's own sampler; re-specify F7-b's mutation as a **deliberate >budget hold**.
- **W-2 — R15 names the wrong victim class.** `architecture-recommendation.md` §1.5/A6/R15; `daemon/manager.py:11224`. PG resets the idle timer on every statement ⇒ the bound counts **continuous** idle; M-R3's legitimate dwell (≈ 13–15 s) is **not** killable by a 60 s bound; the real collateral victim class is **unmeasured**. **Fix:** as C-4(c)–(e).
- **W-3 — The causal chain's final arrows are a non-sequitur, and the recurrence precondition is left out of scope.** `plan-overview.md:34`, `:213-214`; `architecture-recommendation.md` §1.1; `daemon/routers/instances.py:471-483`; `daemon/routers/queues.py:653-654`. See §2.3. **Fix:** restate §1.1's arrows as *"event-loop sync-DB ⇒ dwell stretch ⇒ (idle-in-txn ↑) AND (probe timeouts) AND (API latency); occupancy is a symptom"*; add to `plan-overview.md` §10 that the recurrence **precondition** is **not** removed by this lane, and name the follow-up; make T2.11's **shipped** honesty block say it.
- **W-4 — F6's `ON CONFLICT` depends on an index whose **absence** is exactly OQ-1's data shape, and the PG pack cannot see it.** `phase1-plan.md:47`, `:49`, `:64-65`; `daemon/manager.py:8548-8552`; `daemon/repositories/mission_record/models.py:307-313`. `ux_mission_records_anchor_open` is created at boot by `CREATE UNIQUE INDEX IF NOT EXISTS … WHERE status='open'`, which **fails** if duplicate OPEN rows exist (OQ-1's live candidate). The F6 pack runs on a **fresh scratch DB** (`create_all` ⇒ the index is always present). If prod lacks the index, the boot mint raises **42P10** instead of the IntegrityError it repairs — trading one boot-path exception for another on the exact shape being repaired. Mitigating: the live binary boots today, so the index most likely exists — but the plan **never asserts it**. **Fix:** add a read-only assertion that the target DB **has** the index and that its predicate is **byte-identical** to `OPEN_EPOCH_STATUS_SQL`; add a "no index" negative leg asserting the failure is loud; record a read-only prod index-presence SELECT in the gate report (no data remediation — leader-ruled out of scope).
- **W-5 — The canonical honesty block asserts the premise this lane's analysis **overturns**, and the refresh would assert a different false statement.** `daemon/constants.py:207-217`; `daemon/persistence.py:306-315`; `phase2-plan.md:83` (T2.11). The current block says *"the confirmed leak is on the OTHER pool (P1 — the SQLAlchemy manager engine…)"*; the architect's finding is that P1 has **occupancy under dwell**, not a leak. T2.11 would have the block say *"the P1 idle-in-transaction leak is REPAIRED by this lane"* — also false. **Fix:** widen A9-v so both blocks say: **retention/transaction-hygiene REPAIRED** by this lane (branch + date); **DWELL/OCCUPANCY NOT REPAIRED** — residual owner = the event-loop sync-DB follow-ups and the BFS depth loop.
- **W-6 — Two mutually inconsistent gate ladders and two different guard file inventories.** `test-strategy.md:632-660` (G1.1–G3.5) vs `phase3-plan.md:38-48` (G0–G6); `test-strategy.md:397-398` (`test_dbpool_churn_census.py`, `test_dbpool_readcycle_guard.py`) vs `phase3-plan.md:56` (`test_db_pool_idle_in_transaction_guard.py`). A8-iii noticed the ID mismatch but did not close it. **Fix:** ONE canonical ladder using the IDs the architect's amendments reference (**G1.1…G3.5**), ONE pack inventory, an explicit mapping table (`phase3` G0…G6 → canonical IDs), and a static consistency pin asserting every gate ID referenced in `decisions.md` / `architecture-recommendation.md` exists in the canonical ladder.
- **W-7 — DO-NOT-TOUCH is stated at file granularity in one artifact and knob granularity in another.** `plan-overview.md:202` vs `phase2-plan.md:83` (T2.11 edits `:306-315`) and `plan-overview.md:183` (SC5: ":316-331 untouched"). A file-level diff review (T3.6-d) flags the required honesty-comment edit as a violation. **Fix:** state **both** granularities in the coupling matrix **and** in the T3.6(d) gate: *"persistence.py:316-331 knobs byte-identical; :306-315 honesty comment changed per D-21/R12 only"*.
- **W-8 — The coupling matrix has **no row** for `daemon/manager.py` — which becomes a touched file under F8.** `plan-overview.md:192-204` (no manager.py row); `phase2-plan.md:67` (manager.py listed under "explicitly NOT touched"). F8 changes a **write path** under the write-guard that creates MessageQueue/Task/ReportInjection rows (`manager.py:11281-11300`). The lane's most delicate file would be edited with **no C1-style boundary**. **Fix:** add a manager.py row to §9 — allowed region = `_reconcile_deferred_report` (`:10809`), `_reconcile_deferred_report_async` (`:11466`), `_fetch_subshape_a_content_sync` (`:11172`), `_fetch_subshape_a_content_async` (`:11234`) — plus a T3.6 diff-review leg over that file restricted to those functions.
- **W-9 — The census gate's non-vacuity rule **contradicts** its own baseline table.** `test-strategy.md:639` (G1.4), `:659` (G3.4): *"a count of `0` for any family is a **FAILURE**"* vs `audit-method.md:406-418` (family **F MUST remain 0**, `sessionmaker` MUST remain 0). A correct re-run reports 0 ⇒ the gate **fails a compliant tree** ⇒ the pressure to "fix" it points at weakening the rule. OQ-7(c) additionally requires the **diff** be the failing step (the scanner always exits 0) — never stated. **Fix:** restate the cell as *"each family's count MUST **equal** its §7.2 baseline constant; any delta is a FAILURE until classified per §7.3"*, and make the **diff** the failing step.
- **W-10 — The gate forms depend on `uv run` syncing a venv into a worktree that has none.** `test-strategy.md:341` (Form I), `phase1-plan.md:68` (AC12), `phase3-plan.md:47`. **Verified by the reviewer**: `uv` is **not on PATH** (`/home/nea/.local/bin/uv`); the worktree has **no `.venv`**; `/home/nea/ensemble-src/.venv/bin/python` **does** have pytest 9.0.2 and resolves `daemon` to the worktree when cwd = worktree. **This corrects the dispatch's own house note** — `/home/nea/.local/bin/uv run python -m pytest` is itself the fragile form. **Fix:** pin the gate interpreter to `/home/nea/ensemble-src/.venv/bin/python` and **drop `uv run`**; if `uv` is retained, use `uv run --no-sync` against a pre-created worktree venv. Gates MUST NOT run from the shared checkout.
- **W-11 — The parallel-authorship assumption is the **documented root cause** of the artifact drift.** `plan-overview.md:247` (A5: sibling artifacts "are produced by sibling workers in parallel; this plan references them by **filename only**"). The SC/AC set, gate ladder and pack inventory were authored **without** the artifacts they govern ⇒ two ladders, two guard filenames, a stale `~689` count (OQ-6 retired it for the AST baseline A=613 / C=4 / B=175 / D=148 / E=14 / F=0, `audit-method.md:406-418`), and F2-g's contradiction with D-24. **Fix:** a mandatory **cross-artifact reconciliation pass** before implementation — fix-ID set (F1–F8), pack inventory, gate IDs, AC IDs and the SC10 numbers consistent across all 12 artifacts, with a **machine-checkable consistency pin**.
- **W-12 — The `readiness.py:107-113` docstring correction (A4-v) falls outside Phase 1's permitted set.** `phase1-plan.md` §5 ("the bodies of `make_db_probe` and `make_queue_probe` only"). The docstring's premise — *"the driver's own connect/execute timeout releases it — bounded leakage"* — is **false at base**: the manager engine sets **no** execute timeout (`factory.py:323-338`; the URL carries `connect_timeout` for the connect phase only). **Fix:** amend the permitted set to include the docstring correction, and keep the `SET LOCAL`-must-be-first statement visible in the same diff-review allowlist.
- **W-13 — PG/integration pins are invisible to a default run without a static twin.** `test-strategy.md` §3 (F4-b, F5-a, F6-b, F7-a, F7-b) — plus the new F8 PG pin. Deleting any of them leaves the default `pytest` run green. **Fix:** for each, add a default-collected static twin (file exists, marker present, key assertion string present in source), or document the residual and rely on an F7-c-style self-pin.
- **W-14 — No merge-latest-if-moved step before staging.** `plan-overview.md` §1/§5 (base `19fc11c8b`). The soak branch had an unpushed tip (`bd81b40d2`) and a `stage.sh` live run in flight; without an explicit step, a late `origin/latest` move silently invalidates the base and the whole gate run. **Fix:** the five-step sequence in `final-implementation-spec.md` §8.3.

### 🟢 ADVISORY (9)

- **A-1 — The architect's §1.3(i) over-corrects on the `BEGIN` fingerprint.** `architecture-recommendation.md` §1.3(i); `daemon/tools/ens_db_tools.py:1479`/`:1491`. SQLAlchemy's `DefaultDialect.do_begin` is a **no-op** (`pass`) and the psycopg dialect does not override it ⇒ the BEGIN is emitted by **psycopg3's** implicit transaction start, **pipelined** with the first statement ⇒ `pg_stat_activity.query` ends up as the **statement**, not `'BEGIN'`. A backend whose `last_query` is literally `'BEGIN'` is therefore most plausibly a session that sent a **standalone** BEGIN command — i.e. **exactly Site A**. The plan's §3.3 "CONFIRMED attribution" is **more** defensible than the architect allows. **Fix:** soften §1.3(i) to "attribution rests on psycopg3's implicit-BEGIN pipelining, **not wire-verified in this lane**; A3's census must capture `query_start`/`backend_start` to settle it"; keep §1.4's independent reason (separate 5-slot pool, one agent) as the load-bearing one.
- **A-2 — The plan's "dating not verified" caveat is now decidable **without any DB**.** `plan-overview.md` §3.3 caveat 2. The repair engine's `idle_in_transaction_session_timeout` landed `df4bdf994` (2026-09-09) and was hardened `0b660308e` (2026-10-11) — both ancestors of v0.27.4 and v0.27.8 ⇒ during the 2026-10-26 incident the repair engine's **60 s reap was live** ⇒ any idle-in-txn backend on it was **< 60 s old**; combined with the maintenancer-only grant and the 5-slot pool, the 2 `BEGIN` backends were **not** long-lived repair-engine leases. **Fix:** record the dating in the premise ledger and narrow the `BEGIN` fingerprint's owner set to the manager engine and the asyncpg adapter pool.
- **A-3 — SC2/AC7's shape coverage is synthetic.** `plan-overview.md:180`; `test-strategy.md:228-236` (D1 `SELECT pg_sleep(30)`). In the **production** shape (pool-exhausted **checkout** wait) no backend is ever acquired ⇒ "zero idle-in-txn" is vacuously true. **Fix:** state in AC7/SC2 which shape the pin covers; add a second leg for the checkout-wait shape (no backend ⇒ the executor-occupancy symptom).
- **A-4 — F4's `SET LOCAL` must be the FIRST statement.** `phase1-plan.md:43` (T1.4) says so — keep it visible in the readiness.py diff-review allowlist together with the A4-v docstring correction (W-12).
- **A-5 — The `_write_guard` held across the reconcile is a COUNTER, not a lock.** `daemon/write_pause_guard.py:63-98` (`_active_writes` under a `threading.Lock`; `write_enter`/`write_exit`). It does **not** serialize writers ⇒ not an amplifier; but it delays a `pause_writes()` drain by up to the hold. **Fix:** one line in the F8 rationale so a future reader does not over-claim a write-lock amplifier.
- **A-6 — The zero-new-`.sql` claim is VERIFIED — and needs a companion assertion.** `daemon/repositories/mission_record/models.py:307-313` declares `ux_mission_records_anchor_open` as a partial unique index with `postgresql_where=text("status = 'open'")` (predicate constant `OPEN_EPOCH_STATUS_SQL`, `:209`), mirrored at `daemon/manager.py:8548-8552` and `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69` ⇒ F6's `on_conflict_do_nothing(index_elements=["anchor_instance_id"], index_where=text(OPEN_EPOCH_STATUS_SQL))` is expressible with **no** schema change; **the house rule is not triggered**. **Fix:** add a companion assertion that the ORM diff introduces no new TABLE/COLUMN/INDEX — a `.sql` file-count check alone would not catch an ORM-only table.
- **A-7 — F4's guarantee is mis-stated: it bounds the STATEMENT, not the CHECKOUT.** `phase1-plan.md:26` (D-04), `:43` (T1.4), `:46` (T1.7). Under pool pressure the dominant retention is the checkout wait, bounded by `pool_timeout=30 s` — a bound F4 does not touch. **Fix:** restate D-04's outcome as *"bounds the statement duration; the checkout wait remains bounded by `pool_timeout` (D-15) and by the default-executor ceiling (A7)"*; T1.7 must assert **connection return** (`engine.pool.checkedout()` back to baseline, abandoned thread terminated), not "no idle-in-txn backend".
- **A-8 — Three mutations are too broad to plant as one concrete change.** `test-strategy.md` §3 (F3-e "any change that alters the commit path's outcome", F6-c "change the commit/refresh behaviour", F7-a "indirect via collected count"). **Fix:** rewrite each as one specific code edit with a `file:line` and the expected RED output.
- **A-9 — The static twin (G-A) can pass while the guard's assertion is deleted.** `phase3-plan.md` D-25 (G-A); F7-c. **Fix:** require T3.3's mutation evidence to include the F7-c self-pin leg (planted assertion deletion **+** constant decrement ⇒ still RED) and prove the self-pin cannot be bypassed by removing both the assertion and the constant.

---

## 4. Coupling compliance (C1–C4) — independent verdicts

| Coupling | Verdict | Evidence |
|---|---|---|
| **C1** — leak detector + grace refusal are fail-closed; the fix must make the signal stop firing for a REAL health reason | ✅ **PASS** (with a disclosure owed) | `_evaluate_leak` arms **only** on the P2 psycopg oracle (`pool_state_psycopg`); the P1 manager pool is **ADVISORY and MUST NOT arm** (`readiness.py:526-538`). `GRACE_ELIGIBLE_FAILURE_CLASSES` (`:83-91`) includes `probe_timeout`/`pool_busy` ⇒ a manager-pool-poisoned daemon degrades as `probe_timeout` ⇒ the grace **ENGAGES** (the architect's §1.6 correction is correct — the plan's §3.4 "grace REFUSES when pool_leak fires" describes the P2 oracle, not the manager pool). F4's probe-scoped `SET LOCAL` **is** C1-clean: it lands on the observed pool, does not relocate the probe, and does not alter the predicate's inputs. **Owed:** disclose in the **plan's** C1 section that a manager-pool-poisoned daemon can **pass the promote-time soak gate inside the 120 s grace window** (the never-green trigger remains the unconditional backstop). |
| **C2** — no conditional weakening near rollback; never-green stays UNCONDITIONAL at cap-reach | ✅ **PASS** | No phase task touches `scripts/upgrade/lib.sh`; the soak lane's pins (`tests/unit/tools/test_readiness_failure_class.py`) exist as claimed. No proposed change introduces conditionality — `SET LOCAL` is per-transaction and reverts on reset-on-return, an engine-wide `connect_arg` would apply to every session (a second, independent argument for probe-scoping and for OQ-3's DROP). |
| **C3** — false-green discipline: every gate runs in a form the runner EXECUTES and asserts the EXPECTED COLLECTED COUNT | 🔴 **FAIL — C-1** | `/usr/bin/python3` has no pytest ⇒ the fence is inert; Class-4a/4b unfenced and executing zero tests; G1.5/G3.5 assert no count. **R14/R17/R18 are not reconciled in the plan.** |
| **C4** — no regression of the soak lane's pool sizing (5+10) | ✅ **PASS, zero conflict** | The manager engine stays 5+10 with `pool_pre_ping=True`; the P2 saver pool is untouched except the D-21 honesty comment. The soak lane's A4 sizing rides **both non-manager pools** (psycopg saver `max_size=checkpoint_pool_max_size` and the asyncpg adapter) ⇒ disjoint pools, disjoint knobs, no shared limit. **Doc drift:** `research-pool-couplings.md` §3 describes the saver pool as "max 5" — stale at HEAD (it is `checkpoint_pool_max_size`, default 10, clamp [5,20]). |

**Leader-ruling compliance:** F8 in-scope ✅ (fold-in required) · engine-wide `statement_timeout` DROPped — 🔴 **violated in Phase 2** (C-3) · out-of-scope items stay routed ✅ (OQ-1 prod-data, event-loop/BFS dwell, missed-finalize) · no promote/restart/.env — 🔴 **never stated in any artifact** (C-11) · R15's explicit ordering/validation rule — 🔴 **absent** (C-4).

---

## 5. Scope / quality verification (per the dispatch)

| Check | Result |
|---|---|
| Every proposed change grounded in a `file:line` at base `19fc11c8b` | ✅ the plan's citations re-verified at base: `ens_db_tools.py:1475/:1479/:1491/:1489/:1501/:1513`; `readiness.py:113/:127/:1463-1481/:1716-1790`; `factory.py:252/:323-338`; `mission_record_service.py:499-567`; `mission_record/repository.py:69-93/:498-514`; `db_capture.py:308/:387`; `persistence.py:316-331`; `pyproject.toml:95`. **But** the plan's cited line numbers for `readiness.py`/`persistence.py`/`constants.py` are **stale** relative to the incident snapshot (the plan's own C-4 says so) — the *plan's* citations are correct at base; the architect's A9 still carries two stale refs (leak conjunction `:734-743` → `:739-743`). |
| No schema change expected (house rule: every new SQLModel table needs its own `.sql`) | ✅ **VERIFIED** — the partial unique index `ux_mission_records_anchor_open` exists in three places with a byte-identical `status='open'` predicate, so F6's upsert is expressible with **zero** new `.sql` files. **Owed:** a companion ORM-diff assertion (A-6) and the index-presence assertion (W-4). |
| D-17 trap preserved (repair-engine knob isolation) | ✅ **the plan is correct here** — D-17 forbids extending `PG_SESSION_CONNECT_ARGS` (T2.2/T2.4/T2.6, AC15/AC16), and the shared constant is already fenced by `tests/unit/repositories/test_pg_engine_utc_session.py:65`. 🔴 **But architect A5 would break it** unless A5 is amended as C-2 specifies (shared constant must stay application_name-free). |
| R15 — explicit ordering/validation rule for the 60 s backstop | 🔴 **absent** (C-4). |
| No out-of-lane work smuggled in; routed items stay routed | ✅ the plan's out-of-scope list (§10) keeps the event-loop defect, the BFS loop, missed-finalize, mission-data remediation and the proxy-side item routed. ⚠️ **The plan does not disclose that the recurrence precondition (event-loop sync-DB) is the dominant term** (W-3). |
| Nothing weakens soak-gate safety properties | ✅ C1/C2 surfaces untouched by every proposed change. ⚠️ the disclosure owed under C1 (grace engages for a manager-pool-poisoned daemon) is missing from the plan. |
| Release posture | 🔴 **absent from every artifact** (C-11). |

---

## 6. Council disagreements — surfaced and adjudicated

1. **D-24 vs A5 disposition (the one genuine design fork).** Councilor A: amend D-24 to admit an in-lane `application_name`. Councilor B: keep D-24, re-scope F2-g, amend A5's blast radius. **Adjudication: prefer B, with A's addition.** D-24's scratch-DB isolation *is* sufficient for the F7 guard's attribution and keeps the guard hermetic; B's re-scope still delivers A5's prod-forensics value without putting the **saver pool** (the C1-protected oracle) into the lane's diff. A's concern is real and must be fixed either way: `test-strategy.md:303` states the census is attributed "by `application_name` (post-F2)", which cannot be true if D-24 stands ⇒ restate as *"by `application_name` where present, else by scratch-DB isolation"*. Under B's option, also drop A5's saver-pool clause so no clause is left without a landing task.
2. **Severity of the G2.4/"unmodified" pin conflict.** A: ADVISORY. B: CRITICAL. **Adjudication: CRITICAL** — same class as the C3 findings: an unachievable gate on a file the plan itself declares protected leaves the implementer exactly three exits, all C3 anti-patterns. B's supporting facts are correct and materially reduce remediation cost (the file is 147 lines, so F2-e's `:292` is stale; the file is **not** in `SEALED_SHA_BASELINES`, so **no seal re-pin ceremony is needed**).
3. **H4/H7 verdict labels.** A: CONFIRMED / PARTIAL. B: REFUTED / REFUTED. **No substantive disagreement** — B's "REFUTED" is scoped "as to the plan" (the plan's *coverage claim* is refuted); on the questions actually asked, both agree the instrument is absent and the finding stands.
4. **The council's capability table on the asyncpg pool (C-8).** Both councilors recorded the asyncpg adapter pool as unable to hold `idle in transaction`. **The reviewer verified this is wrong** — `daemon/checkpoint_adapter.py:1231` opens an explicit `conn.transaction()`. Corrected in C-8; this is the one place where the council's output was overruled by direct code inspection.
5. **The dispatch's own house interpreter note.** The dispatch instructed `uv run python -m pytest`; the council verified `uv` is **off-PATH** and the worktree has **no `.venv`** ⇒ a gate-time dependency resolution with a two-lane precedent of dying. Both councils converged on `/home/nea/ensemble-src/.venv/bin/python` instead. **The house note needs correcting** — carried into the plan set, not just this report.

---

## 7. What the council preserved — do not lose this in the fold

- **The RCA premise ledger (`plan-overview.md` §3.1) is ACCURATE** — independently re-verified: premise 1 (the BEGIN gap — `ens_db_tools.py:1479`/`:1491` sit inside the outer `try` at `:1475` but **outside** the rollback-owning inner `try`; the outer `except` at `:1513` has **no** rollback), premise 3 (the probe SQL is `FROM task`), premise 4 (the only `job_queue_items` count render-match is `defer_block_resolver._DEFER_PENDING_COUNT_SQL`, context-managed), premise 6 (`tasks`→`task`), premise 7 (`pool_pre_ping=True` already set at `factory.py:328`; `reset_on_return` set **nowhere**; `pool_recycle` genuinely absent on the manager engine). **Refuting 3 of 4 RCA attributions was the right call and is done with citations.**
- **"There is no leaked-session class at HEAD" (§3.2)** — an AST scan of `daemon/` found no unclosed borrows; the claim holds.
- **C1 discipline is real and non-obvious** — F4's probe-scoped `SET LOCAL` was chosen over a dedicated probe pool **on C1 grounds** (detector leniency by relocation), and it **is** C1-clean.
- **The zero-new-`.sql` claim is sound** (A-6).
- **The architect's OQ-3 DROP reasoning is correct**, and its A9 lock-wait correction is technically right — it just needs applying to the plan's own D2 leg (C-7).
- **The C3 diagnosis is real** — `pyproject.toml:95` (`addopts = "-m 'not integration and not postgres and not slow and not conformance'"`) does deselect the very tests a bare `pytest` names.
- **M-R3 is REAL and correctly identified** — both twins verified at base, independently, by all three lanes, with the causal worker proving the connection is *not* released across the `await`.
- **The D-05 "2× so the client fires first" rule is correct and non-obvious** — independently re-derived: `server_kill_time ≥ statement_issue_time + 2·budget > budget = client_fire_time` ⇒ the client always fires first; the cancelled `to_thread` future's later `QueryCanceled` is never observed ⇒ the class stays `probe_timeout` ⇒ grace-eligibility preserved. At ≤ 1× the server error would arrive as an exception ⇒ `FAILURE_CLASS_HARD_COMPONENT_RED` ⇒ **not** grace-eligible ⇒ a behaviour change.

---

## 8. What could not be verified (by any lane)

- **No database access** by any councilor or worker — no PG, no prod, no scratch cluster. All pool/arithmetic work is static analysis at `19fc11c8b` plus the incident file's recorded observations. Two `pytest --collect-only` runs only.
- **No prod data.** Whether `ensemble_prod` has ≥ 2 OPEN epochs per anchor (OQ-1), whether `ux_mission_records_anchor_open` exists on prod, and the live binary's dating relative to the repair engine's `connect_args` option are **unverified**.
- **No reproduction.** Neither the churn leg, the F4 probe leg, nor any reconcile was run ⇒ **BASE-vs-HEAD discrimination for F3/F4/F6/F7 is unproven** (the plan's own §4.4 rule 1 requires it at BASE in a scratch worktree — that evidence does not exist yet).
- **PG runtime semantics were not executed**: idle-in-txn counts, `application_name` emptiness at runtime, `last_query` fingerprints, the `BEGIN`×2 attribution, residence times, PG's last-`-c`-wins behaviour under the daemon's exact connect strings, `SET LOCAL` semantics inside SQLAlchemy's autobegin, and `idle_in_transaction_session_timeout` firing thresholds. (The council's C-7 conclusion is a **semantics** claim from PG documentation, not a measurement.)
- **The λ estimates are the weakest input to the causal chain** — 0.161/s (M-R3) and ≈ 8 borrows/s (total) are the architect's order-of-magnitude derivation from the RCA's 29 `[RESUME]` lines, **not measured**.
- The **2-minute (dispatch) vs 3-minute (incident doc)** relapse window could not be reconciled; the doc records both a "5 backends / 16 open" census and a "0→6 / 8-9→18" relapse table; the latter was used.
- The architect's **"Confidence: Medium-High"** and the flip-condition outcome are undecidable from the artifacts as they stand.
- **A's specific items:** `write_pause_guard.WriteGuardSession.__exit__`'s exception-path close was taken from the plan's pin inventory, not re-verified; `manager.py:12810` as an "M-R3 lane" citation is **imprecise** — `:12810` is `resume_processing_job`; the reconcile call site the architect means is `:13087`.
- **B's specific items:** whether `uv run` can sync a worktree venv offline was not tested (hence the remedy avoids `uv run` rather than certifying it); the soak lane's normative docs (`requirements.md`, `risk-safety-register.md`) were not re-read in full.
- **Neither councilor could verify the other's perspective** — cross-perspective coverage is by construction, not by verification.

---

## 9. Open questions for the orchestrator

1. **Who folds the plan?** The architect wrote *"Status of the planner's artifacts: unmodified"* (`architecture-recommendation.md:9`) and expressed all 12 amendments in its own file. C-2, C-3, C-4, C-5, C-8, C-9, C-11 and C-12 all require edits to `phase1/2/3-plan.md`, `decisions.md`, `test-strategy.md`, `plan-overview.md`. **Recommendation: the PLANNER folds A1–A12 + these 12 criticals + 14 warnings into the plan set; the architect's document becomes an input, not the artifact of record; then a fresh review runs.** Until then the lane proceeds against contradictory ACs.
2. **May F8 touch `daemon/tools/instance.py:1207`/`:1360`?** If **no** ⇒ pin (i) must be scoped narrowly and the residual **disclosed** (it is a write path under `WriteGuardSession`), and `plan-overview.md` §9 needs a `daemon/manager.py` row (W-8). If **yes** ⇒ the lane file manifest grows and the W-8 diff-review leg must land with it.
3. **What is the sanctioned slow-content-fetch technique for the F8 PG pin?** (checkpoint-table lock in the saver's DB vs an instrumented/stubbed checkpointer.) Needed before the flip-condition leg can be written at all (C-12).
4. **Does the `statement_timeout` DROP also change the Phase-2 OBJECTIVE text** (`phase2-plan.md:3`, `:16`)? The council's read: **yes** — the objective must change, not just AC14 (C-3).

---

*Produced by the reviewer (dispatcher/controller). Council: governor `24a5d779-09dd-465b-b9d2-899b88f3f56b`, 2 councilors (`agentic` @ `b0712b2b`, `coding` @ `048c810c`) — the maximum permitted under the canonical-model dedup rule; the dispatch's "4 councilors" assumed 4 distinct models, of which only 2 exist. Supporting workers: `856436de-2f09-4b21-ab9a-5dc6e361ecae` (causal completeness, `code-review`), `183cd3fe-977f-41f3-b87b-170138f9dc2e` (test-gate integrity, `code-review`). Reviewer-verified code anchors and the council's factual correction are marked inline. Read-only review: no daemon/, tests/, scripts/ or payload-tree file was modified.*
