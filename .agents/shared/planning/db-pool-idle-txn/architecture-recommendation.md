# Architecture Recommendation — db-pool-idle-txn (causal-completeness adjudication + plan enrichment)

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`) — "DB pool poisoned by leaked idle-in-transaction connections".
- **Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn` — branch `fix/db-pool-idle-txn`.
- **Base for every `file:line`:** `19fc11c8b` (= `origin/latest` = the staged **v0.27.11** tree). Plan commit `23c232b8d` adds `.agents/` docs only (`git diff --stat 19fc11c8b 23c232b8d -- daemon/` is **empty**) ⇒ code tree == base.
- **Author:** Architect (fan-in of 3 workers; every load-bearing claim re-verified by the architect against the code at HEAD — see §7).
- **Worker instance IDs:** `334d2377-7fa3-4846-b8d7-8d225886b14c` (`architect-worker-causal-chain`, resilience-design) · `33594317-25e5-440c-b332-27b77812a3f1` (`architect-worker-idiom-census`, data-flow-design) · `8c78a2b2-84a2-431e-a4bf-d5b796f3a5d6` (`architect-worker-blast-radius`, scalability-design).
- **Mode:** Standard Design (3-worker fan-out). Council was **not** convened here: the house pipeline already charters exactly **one** council (the reviewer council) on this plan downstream, and the ONE-council-per-question slot is consumed by it.
- **Status of the planner's artifacts:** **unmodified**. Every amendment below is expressed here only (per the dispatch: amend via this recommendation, never by editing planner artifacts).
- **Read-only discipline:** this lane changed **no** code. This document is the only artifact.

---

## ① Causal-completeness verdict — **HOLE FOUND**

### 1.0 Ruling

> **The plan's confirmed hazard set {A (repair-tool BEGIN gap), D (readiness-probe timeout retention)} does NOT explain the observed relapse signature.** The chain closes only with two mechanisms the plan does not contain, and the plan's own dominant framing ("leaked idle-in-transaction connections") is **partly wrong**: on the manager engine, `idle in transaction` measures **slot occupancy under dwell-time stretch**, not leaks.
>
> The plan's fix set (F3/F4/F5/F6/F2) repairs **real** defects and should still land. But **no item in it reduces the dominant producer**, so a v0.27.12 staged on this basis would plausibly **re-open the incident under the same load**. Three amendments are therefore required before implementation: **A1** (new fix item F8), **A2** (F7's guard invariant), **A3/A4** (census method + framing corrections).

### 1.1 The chain that actually closes (verified)

```
auto-continue boot pass resumes 4 instances (29 [RESUME] lines / 3 min)
  → per-turn DB borrows (checkpoint/mission/queue) + UI polling
  → manager engine: 15 slots (pool_size=5 + max_overflow=10), NO server-side bound
     (factory.py:323-338 — connect_args carry ONLY "-c timezone=UTC")
  → EVERY session borrow parks its backend idle-in-txn from its first statement
     until close/commit (no autocommit / isolation_level configured)
  → event loop pegged at 104% CPU: sync DB work runs ON the loop
     (routers/instances.py:476 calls sync manager.list_instances with NO await;
      routers/queues.py:654 calls resolver.resolve() on the loop)
  → dwell W stretches 10-50x  ⇒  occupancy λ×W ≈ 6   (Little's law)  ⇒ 6 idle-in-txn
  → checkout waits (pool_timeout 30s, NOT pinned)  ⇒  probes exceed their
     0.5s / 2.0s budgets (readiness.py:113 / :127) — PG itself was sub-ms
  → abandoned probe threads park in the DEFAULT ThreadPoolExecutor checkout queue
     (readiness.py:1279-1281; 1351 to_thread/executor call sites daemon-wide)
  → ~6 of 12 default-executor workers pinned  ⇒  ALL to_thread work delayed
  → dwell stretches further  ⇒  readiness red  ⇒  retry pressure
```

**No leak is required to reproduce the signature.** Every step above is verified in code (§7).

### 1.2 The two missing mechanisms

**M-STRETCH (dominant).** Implicit-transaction dwell-time multiplication on a 15-slot pool. The plan explicitly puts the sync-DB-on-the-event-loop defect **out of scope** (`plan-overview.md:34` — "a latency defect, not a transaction defect") — **that framing is the hole**: on this engine a long dwell *is* the idle-in-transaction population. **No fix item reduces dwell time or occupancy.**

**M-R3 (the only multi-second producer) — absent from the plan set.** The deferred-report reconcile holds a manager-engine session across an `await` and across an 8-second cross-thread bridge:

- `daemon/manager.py:11515-11517` — `with self._write_guard:` / `with self._session_scope() as session:` (the scope is `manager.py:2774-2787` — a `WriteGuardSession` over the manager engine, closed in `finally`).
- `daemon/manager.py:11568-11571` — **inside that scope**: `content = await self._fetch_subshape_a_content_async(child_instance_id=…, session=session)`.
- `daemon/manager.py:11260` — the last statement before the await is `child_row = session.get(Instance, child_instance_id)`. Its render is `SELECT instances.instance_id, instances.project_id, … WHERE instance_id = ?` — **byte-compatible with the incident's observed fingerprint** (RCA `:59`).
- `daemon/manager.py:11208-11224` — the **sync twin** holds the same session scope across `asyncio.run_coroutine_threadsafe(...).result(timeout=8.0)` ⇒ up to **8 s** of manager-engine slot held.
- The awaited work is checkpoint I/O (`daemon/services/child_reports.py:1654` → `get_instance_messages(self._checkpointer, …)`), i.e. the slow surface.

The plan's census names R3 only via `child_reports.py:2594` and marks it "observation only; no defect at HEAD" (`research-audit-census.md:65`). **The manager.py sites are absent from the entire plan set** — they are the missing leak/retention path.

### 1.3 Three plan claims that must be corrected (evidence in §7)

| Plan claim | Verdict | Why |
|---|---|---|
| §3.3: `BEGIN` ×2 ⇒ **Site A**, "CONFIRMED attribution" | **UNVERIFIED / non-unique** | Only 2 literal `text("BEGIN")` sites exist (`ens_db_tools.py:1479/:1491`) — but **254 `engine.begin()` + 257 `.begin()` sites** exist in `daemon/`, and libpq/psycopg3 opens *every* transaction by sending a literal `BEGIN` command. `last_query='BEGIN'` is therefore the generic marker of **"a transaction opened and then went quiet"** — exactly the M-STRETCH/M-R3 shape. A cannot be the fingerprint owner. *(Protocol-level argument; NOT DB-verified in-lane → A3 makes it empirically settleable.)* |
| R2 / §3.3: `_enrich_instances` holds the hot path open across in-Python work | **FALSE PREMISE** | `_enrich_instances` is a **no-op hook** (`repository.py:444-449`; 1 definition, 7 call sites, **no override anywhere in the repo** — verified by grep). The real dwell is the **BFS depth loop** (`repository.py:1434-1645`) plus M-R3. |
| §3.4 M2: D is "unbounded server-side retention" of a connection | **MIS-DESCRIBED** | The probe's query completes in ms when PG is sub-ms (`readiness.py:1477-1478`, `SELECT 1`), so per-abandonment *connection* retention ≈ 0. D's real retention is **executor-side**: the abandoned thread parks in the default `ThreadPoolExecutor` checkout queue for up to `pool_timeout=30 s`. Also: the module's own docstring claims "the driver's own connect/execute timeout releases it — bounded leakage" (`readiness.py:107-113`) — but the manager engine sets **no** connect/execute timeout (`factory.py:323-338`) ⇒ **that bound does not exist**. The plan's direction was right; its mechanism was wrong. |

### 1.4 Hazard A: rate feasibility — **NO**

- The repair engine is a **separate pool** (`ens_db_tools.py:603-633`: `pool_size=2`, `max_overflow=3`, `pool_timeout=10`, plus server-side `statement_timeout/lock_timeout/idle_in_transaction_session_timeout=60000`) ⇒ **A cannot touch the manager pool at all**.
- The `ens-db` tool category is granted to **exactly one agent** (`agents/maintenancer/meta.json:14`) ⇒ no plausible invocation during an agent-resume burst.
- Even if it fired, the damage is capped at 1 backend on a 5-slot pool with a 60 s reap.

**Ruling: F3 stays (it is real hygiene, and the plan's fix shape is right), but it must be re-labelled hygiene — not the incident's mechanism.** A "BEGIN ×2 ⇒ Site A" narrative must be removed from the incident follow-up.

### 1.5 Backstop sufficiency (`idle_in_transaction_session_timeout=60 s`)

| Quantity | Value | Source |
|---|---|---|
| Pool slots | 15 (5 + 10) | `factory.py:323-338` |
| Reap bound | 60 s of **continuous** idle-in-txn | planned D-16 |
| Saturation threshold (Little's law) | λ > 15/60 = **0.25 retention-events/s** | derived |
| Observed borrow rate | λ ≈ 10-20 borrows/s (29 turns × ~50 borrows + UI polling) | order-of-magnitude from the RCA's 29 `[RESUME]` lines |
| M-R3 rate | ≈ 0.16/s (1 reconcile per resume event) × 8 s dwell | `manager.py:12810`/`:13087` lanes |

⇒ **The 60 s reap does NOT prevent saturation** (it binds only at λ ≤ 0.25/s) and it **fires only after 60 continuous idle seconds** ⇒ **the first 60 s of any burst — exactly the incident's 3-minute window — is unprotected.** The backstop is a *containment* device, not a fix. The fix is to reduce the producer (F8) and the dwell (A8 follow-up).

**Collateral-kill risk (disclosure):** the 60 s idle-in-txn bound kills sessions that are open-but-busy-in-Python. That is *precisely* M-R3's shape: checkout wait (≤30 s) + 8 s bridge ⇒ a 60 s kill can fire on **legitimate** in-flight work ("terminating connection due to idle-in-transaction timeout") ⇒ **F8 must land first**, and the bound must be sized against the post-F8 legitimate-hold budget, not by analogy to the repair engine.

### 1.6 Grace / soak interaction — the plan's §3.4 amplifier claim is **inverted**

Verified at HEAD:

- `GRACE_ELIGIBLE_FAILURE_CLASSES` (`readiness.py:83-91`) = {`probe_timeout`, `pool_busy`, `queue_stale`, `read_timeout`}. A probe **timeout is grace-ELIGIBLE** ⇒ the grace **ENGAGES** on the incident's own failure mode.
- The **leak** class (`pool_leak`) arms on the **P2 psycopg saver pool** (`readiness.py:788-806` docstring + `:852-900` code) and the **P1 manager pool is ADVISORY — "MUST NOT arm the leak class"** (`readiness.py:526-538`).
- The never-green soak trigger stays unconditional — C2 unaffected.

⇒ **Correction:** a poisoned/saturated *manager* pool degrades components as `probe_timeout` ⇒ **grace-eligible ⇒ the grace engages (≤120 s)**. The plan's §3.4 line "pool_leak … grace REFUSES when pool_leak fires" describes the **P2 oracle**, not the manager pool. The consequence must be disclosed: **a manager-pool-poisoned daemon can pass the promote-time soak gate inside the 120 s grace window** — the never-green trigger remains the only unconditional backstop, and it is armed on the *soak* path, not on the leak path. (This does not weaken C1 — it narrows the plan's claim about what the leak signal does for the manager pool.)

---

## ② Open-question rulings (OQ-1 … OQ-7)

**Numbering map** (the plan numbers OQ-1…OQ-5 in `decisions.md:310-318` = `plan-overview.md:249-255`; OQ-6/OQ-7 are the plan-owner questions in `audit-method.md:475-488`): OQ-6 = family-D breadth; OQ-7 = scanner materialization.

| ID | Question | **Ruling** | Reason |
|---|---|---|---|
| **OQ-1** | Why is the boot-mint `UniqueViolation` deterministic (4×/boot)? | **Implement F6 as planned; add a code-level trigger pin to F6; do NOT commission a separate prod investigation in this lane.** | Verified: `_mint_if_no_open_epoch` DOES probe first (`mission_record_service.py:656-661` → `find_open_epoch_by_anchor`), and the probe's predicate is **identical** to the partial index (`repository.py:78-93` vs `models.py:209` + `:305-313`, `OPEN_EPOCH_STATUS_SQL = "status = 'open'"`) ⇒ the "probe/idx predicate mismatch" hypothesis is **excluded**. The confirmed defect is elsewhere: **`_mint_epoch_locked`'s IntegrityError handler re-resolves ONLY on `founding_message_id`** (`:544-565` — the code's own comment: "a NULL-founding epoch can NEVER be found by that probe") ⇒ on the boot path (`founding_message_id=NULL` by design) a collision has **no re-resolve path** and can only be swallowed by the fail-open wrapper. Two trigger candidates remain, both code-visible: **(a) TOCTOU** — probe and INSERT run in *separate* transactions (`session=None` ⇒ own-session at both ends) while the two boot mint call sites (`auto_continue_boot_pass.py:721` and `:1156`) run **concurrently** via `asyncio.to_thread` ⇒ a same-anchor double-mint commits inside the probe→insert window; **(b) a pre-existing OPEN row invisible to the probe** (legacy `status` literal ≠ `'open'`, or NULL anchor). F6's upsert (D-08/D-09) is idempotent under **both**. **Required addition (A6):** F6's PG pin must (i) reproduce a deterministic double-mint for the same anchor with `founding_message_id=NULL` ⇒ exactly one row, **no IntegrityError escaping**; (ii) assert the boot path's re-resolve is keyed on the **index's predicate** (anchor + `status='open'`), not on `founding_message_id`; (iii) include a census assertion flagging any anchor with >1 OPEN epoch or any `status` literal ≠ `'open'`. Prod-data remediation stays out of scope (§10 #7). |
| **OQ-2** | Which pool did the incident's 5 backends belong to? | **Does not change the fix set; makes the NEXT incident decidable. Ruling: adopt A5 (per-engine `application_name`) and A3 (census captures residence time + attribution).** | `application_name` is empty for daemon connections (`factory.py:252` — `PG_SESSION_CONNECT_ARGS = {"options": "-c timezone=UTC"}` only; the sole setter is the one-shot wedge-capture connection, `wedge_capture/db_capture.py:198`). ⇒ censuses are unattributable, which is why "BEGIN ×2 ⇒ Site A" was over-read. Cheap fix, C1-clean (it does **not** move probes off the pool, does not touch the leak predicate/grace/schema). |
| **OQ-3** | Engine-wide `statement_timeout=30000` on the manager engine: include or drop? | **DROP the engine-wide `statement_timeout`. KEEP the probe-scoped `SET LOCAL` (F4) and KEEP `idle_in_transaction_session_timeout` (sized per A6).** | The one manager-engine statement class that is unbounded-at-scale runs **at boot**: `_ensure_postgres_columns` executes its whole statement list inside **one** transaction (`manager.py:8760-8761` — `with self._engine.begin() as conn: for stmt in statements: conn.execute(text(stmt))`), and the code itself documents the JSON→JSONB step as a "FULL TABLE REWRITE with ACCESS EXCLUSIVE lock… may take seconds" (`manager.py:7877-7879`) while the method is **fail-loud** ("startup aborts", `manager.py:7554-7560`) ⇒ a 30 s cap firing there = **the daemon does not boot**, on a path that is a **no-op at steady state** (so the hazard is invisible in normal measurement). No other class has a plausible >30 s duration: the famous ~206 MB / 42 s `GET /messages` walk rode the **P2 saver pool** and was **deleted** (`persistence.py:530-536`); checkpoint saves/prune ride P2 + the asyncpg adapter; RAG is external HTTP (`daemon/rag/client.py`); the migrations runner is a PG **no-op** (`daemon/migrations/runner.py:815-840`); vacuum is a separate NullPool. This is exactly R1's escape hatch ("drop it without reopening F4"). **Do NOT substitute a larger global value** — the boot-DDL duration is not calibratable from this lane (no prod row counts). If a global cap is later mandated, it must **exclude** the boot-DDL transaction (`SET LOCAL statement_timeout = 0` inside `manager.py:8760`) — a reviewer-visible pattern, not a default. |
| **OQ-4** | Is `pool_timeout=30` acceptable given the 0.5 s probe budget? | **CONCUR — keep 30 s, pin it explicitly.** Add one disclosure. | The probe budget is an **asyncio** budget; `asyncio.wait_for(asyncio.to_thread(probe), timeout=…)` (`readiness.py:1279-1281`) abandons the **coroutine**, not the thread. `pool_timeout` is the **checkout** bound for every manager borrower: 30 s ≈ 3.75× the incident's observed 8 s worst case, whereas the repair-engine parity value (10 s) sits only +25 % above that worst case ⇒ zero margin for the very spike that produced the incident ⇒ converts slow-but-successful requests into 500 s. **Added disclosure (A7):** the true ceiling on abandoned probes is the **default `ThreadPoolExecutor`** (`asyncio.to_thread` ⇒ `min(32, cpu+4)` workers; 12 on an 8-core host), **not** the PG pool — submissions beyond it queue. The plan does not mention this amplifier. |
| **OQ-5** | Should `_enrich_instances` be moved outside the session scope now? | **NO code change — but for a DIFFERENT reason than the planner's, and the follow-up target must be re-pointed.** | `_enrich_instances` is a **no-op hook** (`repository.py:444-449`; no override in the repo) ⇒ there is nothing to move; the planner's R2 "hot spot" premise is false. The real hot spots are the **BFS depth loop** (`repository.py:1434-1645`, one query per level, depth ≤256, ≤1000 descendants/page) and the **M-R3 borrow-then-await** (`manager.py:11515-11571` / `:11208-11224`). R2's disclosure must be rewritten (A8), and a follow-up commission re-pointed at those two surfaces. |
| **OQ-6** | Should family D's canonical breadth be the AST form (148) or a hand-written narrower form (~60)? | **ADOPT the AST form**; retire "≈689" as a historical figure. | The AST form is machine-reproducible and strictly safer (audits *more*); the narrower form's definition was never recorded, so a future lane trusting the printed number would flag a false drift (`audit-method.md:482-485`). |
| **OQ-7** | Should the borrow-census scanner be committed to the repo (`scripts/audit/borrow_census.py`) or kept as a planning-dir artifact? | **INCLUDE in THIS lane**, with three conditions. | G1.4/G3.4 are **binding gates** that re-run the census, and `audit-method.md:56-57` already specifies the repo path with the exact source ("verified to run against this HEAD"). Without it, those gates degrade to prose ⇒ the C3 false-green class. Maintenance surface ≈ 0 (pure `ast`+`os`, no repo imports, one CLI arg). It adds **no** payload-tree bytes (no `agents/`/`frontend/`/`plugins/` change) ⇒ no manifest impact; house precedent `scripts/upgrade/ledger_check.py`. **Conditions:** (a) list it in the lane file manifest; (b) header names the census method + the baseline it reproduces; (c) **the gate command must make the DIFF the failing step** — the script always exits 0 (`audit-method.md:~66`), so "0 unclosed" must be asserted by the diff, not by the exit code. |

**Rider (audit-method.md §9.3, "should the audit also scan `plugins/`?") — NO, out of scope.** The plugin subsystem runs out-of-process; flag it to a future lane as a named residual, do not widen this lane's census.

---

## ③ C1–C4 compliance assessment

| Coupling | Verdict | Evidence + required amendment |
|---|---|---|
| **C1** — leak detector + grace refusal are fail-closed by design | **PASS with one correction + one amendment** | The two probe bodies are **disjoint** from the leak machinery at HEAD: DB probe `make_db_probe` (`readiness.py:1463`, body `:1476-1481`), queue probe (`:1716`, body `:1746-1789`) vs leak predicate `:784-941`, conjunction `:739-743` (the plan says `:734-743` — **line drift, substance intact** → doc correction A9), detail schema `:381/:425-435`, grace constants `daemon/constants.py:165`, `daemon/api.py:3939-3987`. Both probes bind **`manager.engine`** (`api.py:3940-3946`) ⇒ F4 lands where the leak is observed ⇒ **no relocation**; D-04's dedicated-pool rejection stands. **Will `pool_leak` stop for the right reason?** Yes — but the reason must be stated correctly: the leak ORACLE is the **P2 psycopg pool** (`readiness.py:852-900`); the manager pool is **advisory and MUST NOT arm** (`:526-538`). F4 removes manager-pool retention ⇒ request scope-transactions are no longer stretched ⇒ the P2 pool stops starving ⇒ the oracle stops arming **because the pool is genuinely healthy**, not by leniency. The 2× bound keeps the **client-side** `wait_for` firing first (`readiness.py:1279-1285` → `FAILURE_CLASS_PROBE_TIMEOUT`), so a slow probe still classifies as a timeout and cannot silently go green. **Correction (A4):** the plan's §3.4 claim that the grace "refuses when `pool_leak` fires" describes the P2 oracle — for the **manager** pool the grace **ENGAGES** (`probe_timeout` is grace-eligible, `:83-91`) ⇒ disclose that a manager-pool-poisoned daemon can pass the soak gate inside the 120 s grace window (never-green remains the unconditional backstop). **Amendment (A5):** AC14/AC16 must be amended together with F2-g — as written, F2-g (`test-strategy.md:132-134`, "the F7 guard asserts the `application_name` presence") is **unsatisfiable** against AC14's byte-exact options string (no `application_name`) and AC16's "repair options string byte-identical". |
| **C2** — no conditional weakening near rollback; never-green unconditional | **PASS** | The plan touches no soak surface: `scripts/upgrade/lib.sh` is not in any phase task (phase files touch only `daemon/constants.py`, `factory.py`, `ens_db_tools.py`, `persistence.py` (comment only), `readiness.py` (probe bodies only), `wedge_capture/db_capture.py`, tests). The soak lane's pins exist as claimed (`tests/unit/tools/test_readiness_failure_class.py` — `TestLeakPredicate` with the exhaustion/trend/streak/sub-ceiling legs). **No proposed change introduces conditionality**; `SET LOCAL` is per-transaction and reverts on reset-on-return — an engine-wide `connect_arg` would apply to **every** session, a second independent argument for probe-scoping (and for OQ-3's DROP). **One correction (A9):** the plan's implied "idle-in-txn timeout ⇒ boot-abort on lock wait" is **not** how PG behaves — a session waiting on a lock is `active` (wait_event_type='Lock'), not idle-in-txn, so the idle-in-txn bound does **not** fire on the boot DDL's lock wait; the real exposure of the 60 s bound is the M-R3 "open-but-busy-in-Python" shape ⇒ this is a **disclosure** correction, not a safety hole. |
| **C3** — false-green discipline (runner executes the gate + asserts expected collected count) | **PASS WITH 2 REQUIRED FIXES (A8)** | `pyproject.toml:95` `addopts = "-m 'not integration and not postgres and not slow and not conformance'"` ⇒ a bare `pytest` collects **zero** PG/integration tests. Classes 2/3/4b name executed forms + the §2.3 fence script (0 collected ⇒ FATAL; count ≠ declared ⇒ FATAL; `skipped>0` ⇒ failing PG gate). **GAP 1 (🔴):** **G1.5 and G3.5 — the two whole-tree net-new gates — carry NO collected-count assertion** (G1.5 says the base count is "recorded"; G3.5's non-vacuity cell holds only the diff method). A 0-collected whole-tree run produces an **empty FAILED set ⇒ "net-new = ∅" ⇒ FALSE GREEN** — the exact class C3 exists to defeat. **Fix:** apply the §2.3 fence to both whole-tree legs (`collected > 0`; assert base-vs-head counts differ by exactly the lane's added tests). **GAP 2 (🟡):** the Class-4a command form is **unfenced** — no per-variable `POSTGRES_*` scrub, no `mktemp -d` HOME fence, and `/usr/bin/python3` instead of the certified uv/venv interpreter the same section mandates ⇒ a live-shaped-path hazard on packs that touch the saver pool. **Fix:** apply the Class-1 env fence + certified interpreter to 4a. **GAP 3 (🟢):** G1.4/G3.4 name a *recipe*, not a runner — the diff must be the failing step (see OQ-7). |
| **C4** — no regression of the soak lane's pool sizing (5+10 preserved) | **PASS — zero conflict** | The manager engine stays **5+10** with `pool_pre_ping=True` (D-14); the P2 saver pool (`persistence.py:316-331`) is untouched except the D-21 honesty comment. The soak lane's A4 sizing (5→10) rides **both non-manager pools** — the psycopg saver (`persistence.py:320`, `max_size=checkpoint_pool_max_size`) **and** the asyncpg adapter (`persistence.py:358-359`) — with `limits.checkpoint_pool_max_size` (`ge=5, le=20`, `daemon/config.py:631-634`; default 10, `daemon/constants.py:197`) ⇒ disjoint pools, disjoint knobs, no shared limit. **Doc drift (A9):** `research-pool-couplings.md` §3 describes the saver pool as "max 5" — stale at HEAD (it is `checkpoint_pool_max_size`, default 10). |

---

## ④ Recommended architecture + amendments

### 4.1 Shape of the recommendation

**Keep the plan's spine** (it is well-built and its premise corrections are right): F3 (drop BEGIN + `finally` rollback), F4 (probe-scoped `SET LOCAL` + explicit end-of-transaction), F6 (idempotent boot mint), F5 (`tasks`→`task`), F2 (knob set, **minus** the engine-wide `statement_timeout`), F1 (audit record), F7 (guard, **re-specified**).
**Add one fix item (F8) and one attribution item (F5-b)**, and correct the framing, the guard invariant, and two gate forms.

### 4.2 Amendments (numbered for the reviewer council to fold)

**A1 — NEW FIX ITEM F8: remove the borrow-then-await from the deferred-report reconcile.** *(Phase 1 — it is the only change that removes a multi-second producer, and it is a prerequisite for sizing F2's idle-in-txn bound.)*
- Sites: `daemon/manager.py:11515-11517` (async: `with self._session_scope() as session:` → `:11568-11571` `await self._fetch_subshape_a_content_async(..., session=session)`) and `daemon/manager.py:11208-11224` (sync twin: the same scope held across `asyncio.run_coroutine_threadsafe(...).result(timeout=8.0)`).
- Shape: **fetch the content BEFORE opening the scope** (or use a separate, short-lived session for the fetch) so no manager-engine transaction is open across the await/bridge. Preferred: hoist `session.get(Instance, …)` + the content fetch **out** of the `_session_scope()` and pass the fetched values in. Do **not** fix it by lengthening the session — that increases dwell.
- **Do not** "fix" it by moving the work to a thread pool without a bound: the sync twin's `.result(timeout=8.0)` is the shape to preserve (bounded), the *transaction hold* is what must go.
- Pins: (i) a default-collected static pin asserting the reconcile's `_session_scope()` contains **no `await`** and no `run_coroutine_threadsafe(...).result(...)` between the scope's entry and exit; (ii) a PG pin driving the reconcile with a deliberately slow content fetch (the D2 lock technique `test-strategy.md` already documents) ⇒ **zero** idle-in-txn backend attributable to the reconcile, and the manager pool's `checkedout()` returns to baseline.

**A2 — RE-SPECIFY F7's GUARD INVARIANT (load-shape amendment).** The current invariant ("idle-in-txn == 0 at every sample") is **red for a healthy busy daemon and green for a slow leak** — a false-green/false-red machine. Replace with a **residence-time-based, occupancy-bounded** invariant:
1. **Primary:** after a churn cycle, the manager pool's `checkedout()` returns to baseline (`sample_sqlalchemy_pool_state`, `readiness.py:526-538` — in-memory, no DB round trip).
2. **No backend's idle-in-txn residence exceeds the legitimate-hold budget** (pin the budget as a constant; ≤2 s post-F8) — measured via `state_change`/`xact_start`, **not** by count.
3. **Count is bounded by λ×W**, not asserted to be 0 — a *leak* assertion is a residence-time assertion, not a count assertion.
4. **The churn leg must drive the deferred-report reconcile** (F8's target) with a deliberately slow content fetch — the current library-level leg never touches `manager.py` and therefore **cannot discriminate F8**.
5. Keep the F7 attribution model (scratch-DB isolation, per-`application_name` breakdown) — unchanged, and strengthened by A5.

**A3 — CENSUS METHOD: count + residence time + attribution.** The incident's "5 (then 6) idle-in-txn backends" is **undecidable** without dwell: 6 short-lived borrows and 1 long-lived leak produce the same count. Every census (F7 guard, F1 audit record, RCA follow-up) must capture `count` **+ `state_change` + `xact_start` + `backend_start` + `application_name`**, and report **residence-time buckets**, not just a count. The existing census SQL already selects `application_name, backend_start, state_change` (`test-strategy.md:113-126`) ⇒ the method change is a reporting change, not a new capability.

**A4 — FRAMING CORRECTIONS (the incident's premise ledger).** (i) `BEGIN` ×2 is **not** attributable to Site A (generic wire-level BEGIN; §1.3) ⇒ downgrade the plan's §3.3 "CONFIRMED attribution" to **UNVERIFIED** and correct the RCA follow-up narrative; (ii) "idle in transaction" on the manager engine = **slot occupancy under dwell**, not leak ⇒ the incident's title/framing ("leaked connections") is partly wrong and the follow-up must say so; (iii) D's retention is **executor-side**, not connection-side; (iv) the plan's §3.4 amplifier claim (grace refuses) applies to the **P2 oracle**, not the manager pool ⇒ disclose the grace-window soak exposure; (v) the `readiness.py:107-113` docstring's premise ("the driver's own connect/execute timeout releases it") is **false at HEAD** for the manager engine — correct the docstring in this lane (it is the exact sentence a future reader will trust).

**A5 — PER-ENGINE `application_name` (attribution; amend AC14/AC16 + F2-g together).** Extend the **manager** engine's options string with `-c application_name=ensemble-manager` and the **repair** engine's with `-c application_name=ensemble-repair`. Notes: `application_name` is a per-connection GUC and PG's **last `-c` wins** ⇒ the two engines need **distinct** values and the option **order** becomes part of the pin; the saver pool (psycopg) can carry `application_name` in its `conninfo` for the P2-oracle side. This makes **F2-g satisfiable** (as written, AC14 + AC16 make F2-g RED from birth) and makes every future census attributable. It is **C1-clean**: no probe relocation, no detector change, no schema change.

**A6 — F2'S SERVER-SIDE BOUND, RE-SIZED AND RE-SEQUENCED.** Drop the engine-wide `statement_timeout` (OQ-3). Keep `idle_in_transaction_session_timeout` on the manager engine, but: (i) **land it after F8** (A1) — before F8, the bound can kill legitimate M-R3 work; (ii) **pin the legitimate-hold budget** as a named constant (post-F8 target ≤2 s) and size the bound as an explicit multiple of it (the plan's 60 s ≈ 7× the observed 8 s worst case is acceptable **only after F8**); (iii) record the collateral-kill semantics in the honesty block (a killed session = a loud request failure, fail-loud over fail-quiet). The probe-scoped `SET LOCAL` (F4) is unaffected.

**A7 — EXECUTOR-SATURATION DISCLOSURE + F7 EXECUTION CEILING.** Add to the risk register and to the F7 churn leg: the ceiling on abandoned probes is the **default `ThreadPoolExecutor`** (`asyncio.to_thread` ⇒ `min(32, cpu+4)` workers), not the PG pool; with 1351 `to_thread`/`run_in_executor` call sites in `daemon/`, pinning executor workers is a **daemon-wide** latency amplifier. Do **not** "fix" it with a dedicated probe executor in this lane (a probe-side executor is adjacent to the C1 relocation argument and needs its own adjudication); **disclose + measure** (the F7 churn leg should record executor saturation alongside pool occupancy).

**A8 — C3 GATE FENCES (two required fixes).** (i) Apply the §2.3 collected-count fence to **G1.5 and G3.5** (both whole-tree net-new legs): `collected > 0` and base-vs-head counts reconciled against the lane's added tests — otherwise "net-new = ∅" is a false green by construction; (ii) give the **Class-4a** command form the Class-1 treatment: per-variable `POSTGRES_*` scrub (never `env -u POSTGRES_*` — bash does not glob), `HOME="$(mktemp -d)"` fence, certified interpreter. (iii) The G-ladder's naming in the dispatch is `G1.1…G3.5` (three phase groups), not `G0…G6` — the fold should reference the actual IDs.

**A9 — DOC/DISCLOSURE CORRECTIONS (no code).** (i) `research-pool-couplings.md` §3 saver-pool "max 5" → `checkpoint_pool_max_size` (default 10, clamp [5,20]); (ii) leak conjunction `:734-743` → `:739-743`; (iii) R2's `_enrich_instances` hot-spot premise → replaced by the BFS depth loop + M-R3, and the "8 s observed" figure re-described as **checkout wait**, not statement time; (iv) `readiness.py` docstring correction (A4-v); (v) `constants.py:207-217` + `persistence.py:306-315` honesty blocks refreshed (R12) to name **this lane's** repair and the residual mechanism (dwell/occupancy is **not** fully repaired by this lane).

**A10 — F7's "BASE-DEMONSTRATING" REQUIREMENT.** `test-strategy.md:29/:42` requires the confirmed mechanisms to produce a measurable idle-in-txn population at BASE and none at HEAD. With A1/A2's reframing this must become: **BASE demonstrates the F8 shape (a multi-second hold) ⇒ HEAD demonstrates none**; the probe-abandonment shape (BASE) stays a corroborating leg. If BASE cannot demonstrate the F8 shape, the guard is a **regression** guard, not a **reproduction** guard — say so in the test strategy rather than forcing a BASE demo that the mechanism does not support.

**A11 — OQ-1'S TRIGGER, RESOLVED IN-LANE (code-level, not prod).** Fold OQ-1's evidence into F6's PG pin (double-mint reproduction + index-predicate re-resolve + census assertion) rather than commissioning a separate prod investigation. The prod-data question (a pre-existing OPEN row invisible to the probe) stays with the mission-records data owner (§10 #7).

**A12 — SCOPE DISCIPLINE: WHAT THIS LANE DELIBERATELY DOES NOT FIX.** (i) the sync-DB-on-the-event-loop latency defect (`routers/instances.py:476`, `routers/queues.py:654`) — **disclosed + follow-up**, but note it is now understood as a **dwell multiplier**, not a cosmetic latency defect; (ii) the BFS depth-loop transaction span (`repository.py:1434-1645`) — follow-up; (iii) the default-executor ceiling (A7) — measured, not fixed; (iv) the deferred-report reconcile's **8 s bridge timeout value** — keep, but the transaction hold must go (A1); (v) `pool_recycle=3600` on the manager pool — retained (house precedent, negligible churn), disclosed as checkout-time churn.

### 4.3 Sequencing (the fold that matters)

1. **Phase 1 gains F8 (A1) + the F3 re-labelling (A4-i) + the OQ-1 pin (A11) + the docstring correction (A4-v).**
2. **Phase 2 drops the engine-wide `statement_timeout` (OQ-3), adds `application_name` to both engines (A5), and lands `idle_in_transaction_session_timeout` **after** F8 (A6).**
3. **Phase 3 re-specifies F7's invariant + churn leg (A2, A7, A10), materializes `scripts/audit/borrow_census.py` (OQ-7), and applies the two C3 gate fences (A8).**
4. **Follow-ups (routed, not in-lane):** event-loop sync-DB removal; BFS depth-loop transaction span; the executor-ceiling question; mission-records data remediation.

---

## ⑤ Trade-off matrix

Options compared (house 5 axes; Complexity/Cost scored inverted-low-is-bad as the house form requires; Risk inverted so **higher = safer**):

- **A — Plan as written:** F3+F4+F5+F6+F2 (incl. engine-wide `statement_timeout=30000`) + F7 as specified.
- **B — Plan narrowed (safety-precedence):** A minus the engine-wide `statement_timeout`.
- **C — RECOMMENDED:** B + F8 (A1) + F7 invariant/churn rewrite (A2/A7/A10) + census residence-time + attribution (A3/A5) + C3 gate fences (A8) + framing/disclosure corrections (A4/A9).

| Approach | Complexity (20%) | Scalability (20%) | Maintainability (25%) | Risk (20%) | Cost (15%) | Weighted total |
|---|---|---|---|---|---|---|
| **A: Plan as written** | 4 — 5 known fix items, no new machinery | 2 — reduces **no** dwell/occupancy; the dominant producer survives | 3 — more knobs, but F7 asserts a wrong invariant and §3.3 carries an unsound attribution | 2 — 🔴 engine-wide cap can abort boot (`manager.py:7554-7560`); 🔴 F7 false-green; 🔴 incident plausibly relapses | 4 — no scope beyond the plan | **2.95** |
| **B: Plan narrowed** | 4 — unchanged | 2.5 — one risk class removed, mechanism still untouched | 3.5 — one fewer knob; F7 still wrong | 3 — boot-abort risk gone; F7 false-green + relapse risk remain | 4 — unchanged | **3.38** |
| **C: RECOMMENDED** | 3 — one extra fix item + one census change + two gate fences (net modest) | 4 — attacks the **dominant** producer (M-R3 hold) and the dwell class; F8 is the only item that reduces occupancy | 4 — F7 becomes a meaningful invariant; attribution closes; gates stop false-greening | 4 — 🔴→🟡 on the mechanism; no boot-abort; fences remove the false-green class | 3 — F8 + churn harness + scanner + amendments | **3.65** |

**Recommendation: C** — 3.65 vs 2.95 (plan as written) and 3.38 (narrowed). **Dominant axis: Risk (20%) + Scalability (20%)**, then Maintainability (25%) sustains the margin: C is the only variant that removes a multi-second producer, keeps the detector untouched, and eliminates the false-green gate class.
**Confidence: Medium-High.** The flip condition: *if the M-R3 sites are shown (by a PG repro) to hold a manager-engine slot for <2 s under the incident's actual load* — i.e. if F8's contribution to the 6-backend population is marginal — then C collapses to B on mechanism grounds, and the lane's value rests on F2/F4's containment alone (which would itself be a Medium-confidence outcome). **Second flip condition:** if prod row counts make the boot-DDL rewrite exceed minutes, OQ-3's DROP becomes "drop + explicitly exclude the boot DDL" — either way the global cap does not ship.
**Tie-breaking:** not required (no equal totals).

---

## ⑥ Risk-register deltas

**New risks (to be added):**

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| **R13** | **The plan's fix set does not reduce the dominant producer** (dwell/occupancy) — a v0.27.12 staged on this basis re-opens the incident under the same load | high | F8 (A1) + F7's occupancy/residence invariant (A2) + honest disclosure (A9-iii) that this lane does **not** fully repair the dwell class |
| **R14** | **False-green gate class in G1.5/G3.5** — a 0-collected whole-tree run yields "net-new = ∅" | high | Collected-count fence on both whole-tree legs (A8-i) |
| **R15** | **`idle_in_transaction_session_timeout=60000` kills legitimate M-R3 work** (open-but-busy-in-Python) if it lands before F8 | medium | Sequence F8 first (A6-i); pin the legitimate-hold budget; fail-loud disclosure |
| **R16** | **Default-executor saturation** by abandoned probes (≈6 of 12 workers on an 8-core host) is an unmodelled daemon-wide latency amplifier (1351 `to_thread` call sites) | medium | Disclose + measure in F7's churn leg (A7); do not relocate probes (C1) |
| **R17** | **`application_name` amendment is half-applied** ⇒ F2-g stays RED from birth (AC14/AC16 vs `test-strategy.md:132-134`) | medium | Fold A5 as one amendment across AC14/AC16/F2-g in a single change; PG "last `-c` wins" ⇒ distinct values per engine + option order pinned |
| **R18** | **Unfenced Class-4a test command** (no per-var `POSTGRES_*` scrub, no `mktemp -d` HOME, uncertified interpreter) on saver-pool-touching packs | medium | Apply the Class-1 fence to 4a (A8-ii) |

**Amendments to existing rows:**

| ID | Delta |
|---|---|
| **R1** | **Resolved by narrowing** — the engine-wide `statement_timeout` is **dropped** (OQ-3). F4's guarantee is unchanged (probe-scoped `SET LOCAL`); the boot-DDL abort class is eliminated. Keep the row, reworded: "global statement cap = REJECTED (boot-DDL fail-loud abort); a future global cap must explicitly exclude the boot-DDL transaction." |
| **R2** | **Premise corrected** — `_enrich_instances` is a no-op hook; the 60 s idle-in-txn hot spot is the BFS depth loop + M-R3. The 7× margin argument now holds only **after F8** (A6). Re-point the follow-up (A12-ii). |
| **R7** | Extend — the F7 guard's non-vacuity rule must cover the **whole-tree** legs (A8-i), not only the PG pack. |
| **R10** | Extend — the "relocation by future maintainer" rejection must also cover a **probe-side executor** change (A7) and a **probe-pool** change (D-04) in the same code comment at the probe-wiring site. |
| **R12** | Extend — the honesty blocks must state that the lane repairs the **retention/transaction-hygiene** class and **does not** repair the **dwell/occupancy** class (A9-v). |
| **New R19** | **The `BEGIN` fingerprint attribution is unsound** — a future RCA may repeat the Site-A attribution. The incident follow-up must carry the correction (A4-i) and the census method must capture residence time (A3). |

---

## ⑦ Evidence base, method, and confidence

- **Read-only on code.** No file in `daemon/`, `tests/`, `scripts/`, or any payload tree was modified by this lane. The only artifact is this document.
- **Three-worker fan-out** (`resilience-design` / `data-flow-design` / `scalability-design`), one skill per worker, each on a distinct analysis dimension (§1 chain + backstop math; §1/§3 timeout-abandon census + engine attribution + B/C soundness; §3 OQ-3 blast radius + C1–C4 + OQ-4/OQ-7). All three reports were returned with a skill-load confirmation line; no skill-bank miss.
- **Architect verification pass** (independent re-reads at HEAD, ~40 cited sites): `manager.py:11208-11230/:11250-11270/:11505-11525/:11560-11575/:2774-2787/:8755-8765`; `readiness.py:80-91/:105-130/:520-545/:788-806/:836-900/:1258-1300/:1460-1485`; `factory.py:246-258/:318-340`; `persistence.py:312-335/:526-540`; `instances.py:468-500`; `defer_block_resolver.py:300-345/:462-478`; `repository.py:440-452`; `mission_record_service.py:493-570/:630-700`; `mission_record/repository.py:60-100`; `mission_record/models.py:209/:300-320`; `ens_db_tools.py:176-200/:603-632`; `auto_continue_boot_pass.py:713-727/:1146-1164`; `config.py:2683-2695`; `child_reports.py:1650-1660`; plus greps: `text("BEGIN")` (2 hits), `.begin()` (257), `engine.begin()` (254), `asyncio.to_thread|run_in_executor` (1351), `_enrich_instances` (1 definition / 7 call sites / 0 overrides).
- **Every load-bearing claim in this document is either (i) verified above, (ii) cited to the plan/research artifacts, or (iii) explicitly labelled UNVERIFIED.** Specifically UNVERIFIED (no DB access permitted in this lane): (a) that psycopg3's transaction-opening `BEGIN` is what produced the incident's `BEGIN` ×2 fingerprint (protocol-level argument + the 254+257 competing BEGIN emitters make the attribution non-unique either way); (b) prod row counts ⇒ the boot-DDL rewrite duration (⇒ OQ-3's "include at a higher value" is uncalibratable — hence DROP); (c) the live v0.27.8 binary's repair-engine `idle_in_transaction_session_timeout` dating (the plan's own OQ-2 gap); (d) whether the 6 backends were long-lived or short-lived (no `state_change` was captured in the incident) — **this is the single most decision-relevant unknown, and A3 makes it settleable in the next census**.
- **Not adjudicated here (deliberately):** the `event-loop` latency defect, the BFS depth-loop span, the executor ceiling, and mission-records data remediation — routed as named follow-ups (A12).

---

## ⑧ Fold summary for the reviewer council (one screen)

1. **Verdict: HOLE.** Hazards {A, D} do not close the chain; the dominant producer is **dwell/occupancy** (M-STRETCH) plus one unmodelled multi-second producer (M-R3, `manager.py:11515-11571` / `:11208-11224`) that **reproduces the incident's fingerprint** and is **absent from the plan set**.
2. **12 amendments** (A1–A12): A1 = new fix item **F8**; A2 = **re-specify F7's invariant** (residence-time/occupancy, not count==0) + churn leg that drives the reconcile; A3 = census count **+ residence time**; A4 = framing corrections (BEGIN attribution unsound; "idle in transaction" = occupancy; D is executor-side; grace engages on the manager pool); A5 = **per-engine `application_name`** (amend AC14/AC16/F2-g together — as written F2-g is unsatisfiable); A6 = **drop** the engine-wide `statement_timeout`, keep + re-sequence + re-size the idle-in-txn bound **after F8**; A7 = executor-ceiling disclosure; A8 = **two C3 gate fences** (G1.5/G3.5 collected-count; Class-4a env/interpreter fence); A9–A12 = doc/disclosure corrections, F7's BASE-demonstration reframing, OQ-1's in-lane evidence, explicit non-goals.
3. **OQ rulings:** OQ-1 implement F6 + in-lane trigger pin (no separate prod commission) · OQ-2 attribution only · OQ-3 **DROP** the global cap (keep probe-scoped + idle-in-txn, sized) · OQ-4 keep 30 s (+ executor disclosure) · OQ-5 no change (premise false; re-point the follow-up) · OQ-6 AST form · OQ-7 **include** the scanner.
4. **C1 PASS** (probe bodies disjoint from the leak machinery; F4 lands on the observed pool; oracle is P2 ⇒ stops for the right reason) + one correction + A5 · **C2 PASS** (no conditionality) + one PG-semantics correction · **C3 PASS with 2 required fixes** · **C4 PASS, zero conflict** with the soak lane's A4 sizing (disjoint pools).
5. **Trade-off:** C (recommended) **3.65** vs B (narrowed) 3.38 vs A (as written) **2.95** — Confidence Medium-High; flip condition stated in §5.
