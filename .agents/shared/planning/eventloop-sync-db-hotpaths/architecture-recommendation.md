# Architecture Recommendation — wi-3f5bea95 (event-loop sync-DB hot paths)

**Lane:** `fix/eventloop-sync-db-hotpaths` @ `42764588c` (base `b1183a637` = `origin/latest` = v0.27.13 bump)
**Posture:** STAGE-ONLY — no live promote, no live daemon restart, no `.env` edits. Not committed (house precedent: the planning set rides the developer's commit).
**Author:** architect. **Ruling basis:** six dispatched analysts (3 competitive on D-2 + 3 independent dimensions); every load-bearing claim re-verified in-tree by the architect against the worktree at `42764588c`.
**Deliverable audience:** the developer implementing Phases 1–5, and the tester running the gate.

---

## 0. The three decisions, stated unambiguously

| # | Decision | Verdict |
|---|---|---|
| **D-2** | Off-load strategy | **ADOPT the off-loop bridge (mechanism C) + the leader-ruled A-batches (mechanism A).** Async-native (Option 2) is **REJECTED for this lane** and routed to the D-47/D-58 measurement campaign. The hybrid's deferral claim is **accepted only if funded** with three named artefacts (see §2.7). Bridge sizing **6 workers** (not 4; **not 3**). |
| **D-4 / D-B** | Orphan-miss closure | **ADOPT — and it is REQUIRED, not beneficial.** It covers **three** lookup sites, not the one the plan cites. The plan's D-C α (per-row `resolve_work` patch-up) is **REJECTED as harmful** and must be batched; D-8's slice must move **pre-conversion**. |
| **D-16** | BFS-depth txn span | **ROUTE OUT** to R-2. This lane owns the off-loop relocation only; the span is neither restructured nor re-sequenced. |

**Two gate defects are blocking and must be fixed before the gate can pass at HEAD** — the committed harness requires the pins to be **positive** (§6.1). This is a tester-lane work item, not a developer task, and the plan does not currently schedule it.

---

## 1. Why the D-2 decision is a *policy*, not an architecture choice

Two of the three competitive analysts converged independently on the same structural finding:

> **Option 1 and Option 3 are mechanically identical for this lane's scope.** The A-batches are already leader-ruled under Q1/Q3 (`decisions.md` D-1, D-4). Option 3's entire distinguishing content is the **deferral claim** — "the bridge seams are exactly where an async repo call would slot in later, making Option 2's eventual code motion smaller, not wasted" (`decisions.md` D-2; `technical-analysis.md:153`).

That claim is **half-true**, and the half that is false is decisive:

- **True at the router boundary.** `await run_db(manager.list_instances, …)` → `await manager.list_instances_async(…)` is a mechanical one-token edit at the six router sites. Return shapes survive: `tuple[list[dict], int, bool]` (`instance_lifecycle.py:5109-5117`), `dict` (`:5143`), `list[WorkRecord]` via the pure `to_dict()` (`work_resolver.py:329`), `DeferBlockSnapshot` (`defer_block_resolver.py:344`). Exception contracts survive (`KeyError`→404 at `instances.py:550-560`, `messages.py:204-208`).
- **Unsupported at the repository boundary** — where most of Option 2's motion actually lives. Services reach **past** repositories into `.engine`: `work_resolver.py:2226` (`self._task_repo.engine`), `:2279` (`self._job_repo.engine`), `:2361`, `:2413`; `defer_block_resolver.py:274`, `:306`; `mission_resolver.py:691`. An async twin therefore needs a **per-repo async engine handle** — a per-class attribute addition, not a method rename.
- **And the sync tool layer shares the same methods.** `manager.get_instance_info` has ~15 call sites; the sync ones include `daemon/tools/instance.py:1547/1632/4408/4626/5191/5636/6188` (inside factories producing **sync** callables, e.g. `def list_instances() -> list[dict]` at `:6165`), `daemon/tools/instance_read.py:1673`, `daemon/tools/tunables.py:178`. Converting the shared method to `async def` breaks the sync tool layer; keeping a sync twin **creates the two-repo-truth drift class by construction** — at exactly the three methods the six sites need most.

⇒ The correct framing of D-2 is: **"which future are we buying?"**, not "which architecture?" Option 2 buys a future; this lane buys a loop.

---

## 2. RULING D-2 — off-load strategy

### 2.1 Trade-off matrix (5 fixed axes; Risk and Cost scored inverted)

| Approach | Complexity (20%) | Scalability (20%) | Maintainability (25%) | Risk (20%) | Cost (15%) | Weighted total |
|---|---|---|---|---|---|---|
| **Adopted scope** — A-batches (leader-ruled) + C-bridge on a dedicated executor, async-native deferred behind D-47/D-58 | 4 (two mechanisms, but the plan's own R1–R4 rule makes the site choice learnable; ~14 production units + 1 module) | 4 (loop-blocking removed; DB/GIL bottleneck *moved*, not removed — relief comes from D-B; bounded 6-worker ceiling) | 4 (unwrap-able; batches carry over; deferral funded) | 4 (deadlock/admission/shutdown hazards — all three mitigable and pinned; byte-identity hard gate holds) | 4 (~1 module + 14 units, days not weeks) | **4.00** |
| **Option 2** — async-native engine/session rewrite | 1 (14 async twin units across 6 modules; 430 `session.exec` → `await session.execute` sites; a second engine + `server_settings` implementation on the file the sibling just stabilized) | 5 (no thread-pool ceiling; native non-blocking) | 2 (two-repo-truth is the *permanent* shape of an incremental async migration, not transitional; F8 discipline unenforced at 14 sites) | 2 (new pool invisible to the leak oracle; F8 becomes the default idiom; not unwrap-able) | 1 (weeks; new engine; re-anchored D-47/D-58/D-36/D-49 gates) | **2.25** |

*(Option 1 alone — the bridge without the A-batches — is a **sub-case** of the adopted scope, not a live alternative: Q1/Q3 already mandate the A-batches. It scores 4.15 because it is simpler, but it is **not available** — it would fail G9 by leaving `/api/work` at ~1,873 round-trips and the jobs page at ~38.)*

**Dominant axis: Maintainability (25%, the heaviest).** The adopted scope wins it 4-vs-2 because Option 2's two-repo-truth is permanent and its F8 exposure is structural, while Option 2 wins Scalability 5-vs-4 — and the Scalability credit is the *only* place Option 2 leads, which is why its deferral must carry a re-open trigger rather than a hope.

**Confidence: Medium-High.** The flip condition is in §2.6 (the bridge-queue alarm).

### 2.2 Why Option 2 loses here — the measured defect is round-trip COUNT, not round-trip DURATION

`/api/work` is 1,873 round-trips at 8,112 ms p50 (`BASELINE.md` §2), and the badge's 92.3 % of statement **TIME** is 1,434–1,869 doomed single-row `SELECT instances` (`BASELINE.md` §6). Option 2 without A-batches leaves 1,873 round-trips; it makes each wait yield the loop, but the request still takes ~8 s of wall clock and the **GIL-held** Python-side cost (SQLAlchemy construction + ORM materialization) is unchanged.

**The bridge removes the event-loop bottleneck; it MOVES the DB/GIL bottleneck.** The GIL is process-wide, so a bridge thread performing 1,434 round-trips starves the loop exactly as a default-executor thread did. ⇒ **The relief comes from D-B (A-class), not from C-class (the bridge).**

**Consequence — this reframes the whole lane:** **G2 is D-B-dependent, not bridge-dependent.** With D-B rejected, a *perfect* bridge still fails G2. With D-B adopted, the jobs path's 1,439-statement storm collapses to ~10–25 and the default executor frees.

### 2.3 The plan's Option-2 characterization is miscalibrated in BOTH directions — corrected

| Plan claim (`technical-analysis.md:145-152`) | Verified reality | Correction |
|---|---|---|
| "zero `create_async_engine`/`async_sessionmaker` usage exists in `daemon/`" | **TRUE for SQLAlchemy-async.** 0 `create_async_engine`, 0 `async_sessionmaker`, 0 `AsyncSession`, 0 `run_sync` in `daemon/`. `daemon/repositories/**` has **0** `async def`. | Keep, but scope it: the *ORM/repository* layer is 100 % sync-SQLAlchemy. |
| "the only async DB code is the persistence layer" | **UNDERSTATED.** `daemon/checkpoint_adapter.py` carries **50 `async def`** — a complete async DB layer (psycopg-cursor variant `:812-1001`, asyncpg variant `:1098-1400+`). The checkpoint saver's pool is **psycopg** (`persistence.py:334`, opened `:350` → `AsyncPostgresSaver` `:356`); the **asyncpg** pool (`:391`) is separate, used only by `PostgresCheckpointerAdapter` for direct GROUP BY/DELETE/COUNT maintenance. | Rewrite: the tree has real async DB competence, but **zero SQLAlchemy-async**. The risk is *in-tree usage + discipline*, not missing library capability. |
| "~10–50× the code motion" | **14 async twin units / 6 modules** for this cut (43 repo modules / 635 public methods at full-repo scale). Option 2 ≈ **4–5× Option 1** here. | Restate. |
| "asyncpg has different parameter binding / requires `text()` care" | **WRONG for the repositories.** The repo's raw SQL is already dialect-portable: `text(body).bindparam("terminal_statuses", expanding=True)` (`daemon/repositories/job_queue/_idle_predicate_sql.py:361-390`), named `:project_id` binds — SQLAlchemy compiles `:name`→`$N` per dialect. `RETURNING` (56), `ON CONFLICT` (40), `FOR UPDATE` (16), `SKIP LOCKED` (3) are all supported; `SKIP LOCKED` lives only in `auth_session/repository.py:370` and `task/repository.py:2659/2912` — **not** in any of the six sites' path. The `$1`/`text()` class is real only for the **raw-driver** style (`checkpoint_adapter.py`). | Correct the claim. The **real** delta is (i) the ORM rewrite (`session.exec` → `await session.execute` + `.scalars()`/`.all()`, 430 sites) and (ii) **`connect_args`**: asyncpg has **no libpq `options` channel**, so `PG_SESSION_CONNECT_ARGS = {"options": "-c timezone=UTC"}` (`factory.py:259`), the D-47 `idle_in_transaction_session_timeout` clause (`:279-292`) and the attribution name each need a **separate async-engine `server_settings` implementation** — landing on the one file the sibling just stabilized. The sibling's shape already exists: `server_settings={"application_name": …}` (`persistence.py:391-397`). |
| "a parallel repo layer creates a two-repo-truth drift class" | **TRUE and worse than stated**: the sync agent-tool layer shares the same three methods (§1), so the drift class is created **by construction**, not by accident. | Keep and sharpen. |
| "a second engine splits pool occupancy; the leak oracle would need re-anchoring" | **CONFIRMED and precisely sourced**: the readiness leak predicate samples exactly **two** pools — `sqlalchemy_pool=manager.engine.pool` (`daemon/api.py:4154`) and `psycopg_pool=resolve_psycopg_pool(...)` (`:4155`). A third pool is **invisible** to the oracle that arms the leak class. The F7 occupancy assertion (`manager.engine.pool.checkedout()`) is manager-engine-only. | Keep. Note the **census** side is *better* than claimed: `daemon/services/metrics_census.py:85-127` classifies leak sites by **statement-text fragments**, not `application_name`, so an async pool's statements would still classify and its idle-in-txn backends **would** be counted. The census sees the new pool; the readiness leak predicate does not. |
| "the conversion seam makes Option 2's eventual code motion smaller" | **Half-true** (§1): true at the router boundary, unsupported at the repository boundary. | Restate — see §1. |

### 2.4 MANDATORY bridge amendments — without these the seam is a defect, not a fix

These are **not optional**. Each is a probe-confirmed or in-tree-verified hazard, and each becomes a pin.

| # | Amendment | Evidence | Consequence if omitted |
|---|---|---|---|
| **B1** | **Re-entrancy guard.** A bridged callee that itself calls `run_db` deadlocks iff **O ≥ W** (progress needs `W − O ≥ 1` free worker). Probe-confirmed at W=4: O=2 ✓, O=3 ✓, **O=4 → permanent deadlock**. D-7's own sizing rationale is "the four bridged endpoints firing concurrently" ⇒ **the deadlock sits exactly at the designed concurrency.** Enforce with a runtime guard that raises a typed error (never hangs) + a pin that drives the nested case. | probe-confirmed | A nested seam is a permanent daemon wedge with no error. |
| **B2** | **BaseException containment.** A `KeyboardInterrupt`/`SystemExit` raised inside a bridged callable **escapes the event loop entirely** (CPython `Task.__step` re-raises them) ⇒ process death + systemd `Restart=on-failure` crash-loop (`scripts/systemd/ensemble-daemon.service:108`), not a 500. Contain and pin. (A `CancelledError` raised in a sync callee instead cancels the awaiting task — it looks like a client disconnect.) | probe-confirmed | One stray `KeyboardInterrupt` in a DB call crash-loops the daemon. |
| **B3** | **Drain seam.** `daemon/manager.py:15223` `("dispose_db_pools", …)` runs during shutdown, reached via `daemon/api.py:3390` (`yield`) → `:3809` `await manager.shutdown()`. An in-flight bridged read would hit a disposed pool **inside a dying request**. Add `await db_bridge.shutdown()` (bounded drain) to the lifespan **before** `await manager.shutdown()`. | in-tree | A shutdown-race `Engine is disposed` inside a dying request. |
| **B4** | **Admission gate — defer, never reject, never time out.** `ThreadPoolExecutor.submit` never blocks and never rejects ⇒ the queue is unbounded. Use an `asyncio.Semaphore` acquired with `await` (a blocking acquire would re-block the loop). A **per-call timeout is the trap**: `run_in_executor` future cancellation does **not** stop the running thread, so on timeout the DB work continues to completion, still holding its connection and still consuming the GIL while the endpoint must return something — a byte-different error (violates byte-identity) or a late reply to a gone client. **A timeout amplifies the load it is meant to shed.** | executor semantics | Unbounded queueing; or a load amplifier. |
| **B5** | **kwargs in the seam spec.** `daemon/routers/work.py:181-187` calls `resolver.list_work(project_id=…, instance_id=…, status=…, kind=…, root_only=…)` — **keyword-only**. `technical-analysis.md:81` specifies `run_db(fn, *args)` / `partial(fn, *args)` (no kwargs) ⇒ **implementing from §2.0 breaks `/api/work`.** The seam must be `run_db(fn, *args, **kwargs)`. | verified | Phase 3 fails on a TypeError. |
| **B6** | **No executor parameter.** Keep the executor module-private and expose only `run_db(fn, *args, **kwargs)`. "A caller picked the wrong executor" then becomes **unrepresentable**, not merely pin-detected. | design | The "never the default executor" discipline decays to a convention. |
| **B7** | **`thread_name_prefix="db-offload"`.** Makes bridge threads distinguishable in the phase-0 `sys._current_frames` census (`BASELINE.md` §5) and gives the executor-identity pin real discriminating power — the proposed `is not loop._default_executor` assertion is **near-tautological** (it can only fail on a literal `run_in_executor(None, …)`). Three house pools already set a prefix (`stdio_wrapper.py:130` `"mcp-procscan"`, `warmup_pool.py:116` `"mcp-secret"`, `question_pause_set.py:137` `"qpause-merge"`). | in-tree | The census cannot tell bridge threads from default-executor threads; the pin is theatre. |
| **B8** | **Rename `daemon/services/db_bridge.py` → `daemon/services/db_offload.py`**; `run_db` → `offload` (or `run_offloop`). "Bridge" is **already taken twice** in `daemon/services/` with different meanings — `main_loop_bridge.py:1-2` ("bridge **toward** the event loop"; this bridges **away** from it) and `tmp_image_bridge.py:1-3` (a path→data-URI transform). The `db` prefix also already denotes a **different subsystem** (`daemon/services/db_pool_manager.py` = asyncpg pools for *external* `db_connections`). **Rename BEFORE the Phase-1 API freeze** — after it, the cost is the frozen-signature evidence record plus P2/P3/P4 edits. | in-tree | A future maintainer hunts for a DB↔DB bridge that does not exist. |
| **B9** | **TypeVar, not `-> Any`.** Keeps `manager.list_instances`'s `tuple[list[dict], int, bool]` and `get_instance_info`'s `dict` typed at the router; `-> Any` drops the checking on `InstanceInfo(...)` construction (`instances.py:348-360`) for free. | design | Silent type erosion at six call sites. |
| **B10** | **Constant placement.** Pool-sizing knobs live in `daemon/config.py`'s `LimitsConfig` (`env_prefix="LIMITS_"`; the `checkpoint_pool_max_size` precedent), with the default tier in `daemon/constants.py`. `daemon/constants.py:1-11` states the module imports **nothing**; its env helpers do a lazy `import os` inside the function (`:423-436`). Recommend `DB_BRIDGE_MAX_WORKERS_DEFAULT` in constants + `limits.db_bridge_max_workers` in `LimitsConfig` (env `LIMITS_DB_BRIDGE_MAX_WORKERS`), not a bespoke env read in constants. | in-tree | A second, competing config idiom. |

### 2.5 Executor sizing — **6 workers** (the plan's 4 and the "drop to 3" are both wrong)

| Derivation | Result | Verdict |
|---|---|---|
| **Way A — the plan's "four bridged endpoints firing concurrently"** | The coincidence set is **already 4** at HEAD: badge tick bridged legs {instances_flat, defer_blocked} = 2, home tick {instances BFS} = 1 (they coincide at lcm(8,10) = 40 s), plus `/api/work` = 1 ⇒ **4 simultaneous**, with the FE's own agent traffic (T-1/T-2, unmeasured) on top. | **4 = zero margin.** The plan's Way A counts *endpoints*, not the *coincidence set*, and omits agent traffic. |
| **Way B — Little's Law (λ × W)** | λ_badge = 2/8 s = 0.25 req/s × W = 0.756 s ⇒ L = **0.189**; λ_home = 0.1 × 0.028 ⇒ 0.003. Measured total L ≈ **0.19**. | **The wrong statistic** for a forkJoin **burst** — 0.19 is 21× below the peak. Little's Law returns a *time-average*; it imposes no upward constraint. |
| **Per-shape HEAD bridge occupancy** | flat ≤6 rts ≈ **≤27 ms**; BFS ~8 ≈ **~28 ms**; `/api/work` ~6–10 ≈ **~26–43 ms**; defer-blocked 89 rts **unchanged** ≈ **~729 ms**. | **defer-blocked is 25–30× every other shape** — the pool is sized almost entirely by it. |
| **RECOMMENDED** | **`DB_BRIDGE_MAX_WORKERS = 6`** = measured burst 4 + 2 agent fan-out. | **The plan's "may drop to 3 for margin" is REJECTED: 3 is below the already-reachable burst of 4 ⇒ negative margin.** Margin must be added, not subtracted. |

**The pool-envelope argument — the plan is wrong twice, and both errors are corrections in the lane's favour:**

1. **Wrong construction site.** The plan cites `factory.py:143-144`/`:155-156`/`:435` for "capacity 15 = pool_size 5 + max_overflow 10". Those lines are the **`DatabaseConfig` dataclass defaults**, consumed only by `create_engine_from_config` (`factory.py:225-226`) — the SQLite/generic path. The **manager engine** is built at `factory.py:434-435` with the config-driven `manager_pool_size` (default **15**, `daemon/constants.py:264`) + `max_overflow=10` ⇒ **capacity 25**, matching the house's own `MANAGER_POOL_CONFIGURED_CAPACITY = 25` (`daemon/services/readiness.py:165`) and the D-57 equality pin (`tests/postgres/test_dbpool_probe_starvation_pg.py:96`). The plan copied the sibling's **stale** text.
2. **Category error in the phrasing.** `max_overflow` is a slot count *above* `pool_size`, not a budget that bridge workers "fit into". A bridge worker holds **at most one** manager-engine connection (one `Session` per sync call) ⇒ B workers consume **≤ B of 25**. Worst case at B=6: bridge 6 + default-executor jobs enrichment ≤5 + readiness probe 1 + residual on-loop ~2 = **~14 of 25 (56 %)**. The pool is **not** the binding constraint at any defensible bridge size; the 25-cap is a hard ceiling whose failure mode is a bounded `pool_timeout=30` wait, never a ceiling breach.

**F4-A does not protect the bridge — the plan's D-17 reliance is unfounded.** `_refuse_pool_admission_at_capacity` (`readiness.py:1541`) is called from exactly two sites, **both inside `_probe()` closures** — `make_db_probe` (`:1627`) and `make_queue_probe` (`:1929`) — and its own docstring calls it probe-scoped. It never sees a bridged request. The bridge's pool protection is the pool's own 25-cap plus `pool_timeout=30` (D-15), not F4-A. **(The lane still must not touch `readiness.py` — see §5.)**

**The new saturation class — is the bottleneck removed or moved?**

| Endpoint | BASE on-loop | HEAD on-loop | HEAD bridge occupancy | Residual bottleneck at HEAD |
|---|---|---|---|---|
| `/api/instances` flat(10) | 13 rts / 59.2 ms (loaded p95 914 ms) | 0 rts (≤6) | ≤27 ms | PG round-trips; GIL if D-B rejected |
| `/api/instances` BFS(100) | 248 rts / 858.7 ms | 0 rts (~8) | ~28 ms | same |
| `/api/work` | 1,873 rts / 8,112 ms | 0 rts (~6–10) | ~26–43 ms | **response serialization O(n) stays on the loop** (`work.py:189`) — see §4 |
| `/api/queues/defer-blocked` | 89 rts / 729 ms (conn #1 idle-in-txn) | 0 rts (89) | **~729 ms** | **the bridge pool**; O(holders) growth |
| `/api/jobs` recent(10) | 0 on-loop (default exec), 1,439 rts / 7,391 ms | 0 on-loop, ~10–25 rts (D-B) | n/a — default executor | default executor (freed by D-B) |
| `/api/missions` (20) | 3 rts / 18.4 ms | 3 rts (routed out) | — | missions stays on-loop (~18 ms) |

**Saturates first:** (1) the **GIL**, while the jobs path still issues ~1,434 singles — *not* the bridge, *not* PG connections; (2) the **bridge pool**, only under a burst > B; (3) PG server CPU / round-trips (~897 trivial statements/s from the badge's jobs leg); (4) PG connections (14 of 25 at B=6, capped by the pool); (5) the default executor (untouched by this lane's sites).

**Scaling cliffs:** 🔴 `/api/work` response serialization is O(population) and stays on the loop. 🔴 defer-blocked is O(holders): at ~200 holders ≈ 1.65 s/resolve, so 3 concurrent resolves occupy 3 of 6 workers for 1.65 s each ⇒ **the failure mode migrates from "loop stall" to "bridge exhaustion + queueing of everything else."** 🟡 the 25-connection ceiling (a 30 s wait at the margin, not a breach).

### 2.6 The deferral contract — what Option 3 must actually leave behind

**Prose alone fails.** The future lane's failure mode is exactly "a new sync repo call appears on the loop", which no document prevents. **Required (Phase-1 / Phase-5 deliverables):**

1. **A production registry** of bridged callables — `db_bridge.BRIDGED_CALLABLES`, a frozenset of `(file:line, callable, args, error contract)`. Today this exists only as prose + a *test* enumeration (phase1 task 1.6); promote it to production so a new seam is a **deliberate** failure.
2. **The seam inventory + the A-batches that carry over untouched**: `list_child_ids_for_parents` (new), `_batch_instances` (`work_resolver.py:2413`), `get_dlq_by_job_ids` (new) — all engine-agnostic, 1:1 carry-over.
3. **The invariants the async lane must preserve**: D-6 children `ORDER BY`; the D-B observation-window narrowing; the D-C α post-sort slice + batched patch-up; the degradation contract at `work_resolver.py:2003-2015`; the `KeyError`→404 envelopes (`instances.py:551-558`, `messages.py:205-210`); TB-1..TB-7 (§4.3); D-57 capacity 25; the D-47 enablement gate.
4. **A parity harness**: for each registered seam, a stub async implementation that must produce byte-identical output, so the future diff is a mechanical substitution.
5. **A static pin that every `run_db(` callee is in the registry** (the inverse of the plan's task 1.6 check).

**The EXPLICIT re-open trigger for Option 2** (this is the honest form of "we deferred it"): the **`DB_BRIDGE_QUEUE_WARN_DEPTH` alarm firing in production** — i.e. the bridge's own bounded pool becoming the bottleneck — plus a measurement campaign that records the manager engine's legitimate hold on the real fetch path, which is exactly what D-47/D-58/R-9 are gated on (`daemon/constants.py:305-330`).

**Cost of the deferral (what keeps being deferred, and how long it stays safe):** the D-47 engine backstop enablement; the D-58/R-9 asyncpg DSN with `idle_in_transaction_session_timeout`; and the **R-1/R-2 DWELL class** — which *neither* option repairs, and which Option 2 would structurally harden (a transaction held across awaits at the single most expensive site). Deferral stays safe while the bridge+batch shape keeps the loop free and no new sync-DB-on-loop site is added; it is **not indefinitely safe**, because F4-A exists precisely because the daemon can still reach pool capacity under load.

### 2.7 The one thing Option 3 must do that Option 1 alone would not

**Fund the deferral.** If §2.6's five artefacts are not produced, the honest ruling is **"Option 1 + the leader-ruled A-batches"** — the label, not the work, is what is at stake, and saying so plainly changes how D-2 should be framed (it is a *policy about the future*, not a different architecture).

---

## 3. RULING D-4 / D-B — orphan-miss closure: **ADOPT (required)**

### 3.1 The plan cites the wrong site — there are THREE

| Site | Guard | Fires for a fixture orphan? |
|---|---|---|
| `work_resolver.py:1778-1779` | `admission_state == "active"` | **No** — all 1,250 orphans are `admission_state='done'` (`seed.py:306`) |
| `work_resolver.py:1846-1847` | `job_type == "message"` | ~50 % |
| **`work_resolver.py:1878`** | **unguarded** (mission-fields fallback) | **100 % — the dominant site** |

⇒ The closure must cover **all three**, and the plan's `:1846-1849` anchor is not the one carrying the cost.

### 3.2 Byte-identity — proven field by field

`WorkRecord` fields that depend on `instance` (`work_resolver.py:1903-1984`): `status` (via `:1780-1782`), `mission_liveness` (`:1849-1850`), `mission_id`/`mission_epoch`/`mission_terminal_reason` (`:1876-1881`), `mission_ref` (`:1967-1973`), `completion_gate_escalated` (`:1979-1983`). All others are JobItem-native (`project_id` `:1908`, `agent_id` `:1909`, plus `work_id/kind/instance_id/result_summary/error/created_at/message_id/started_at/completed_at/job_type/outcome`).

For a **batch-proven-absent** id the render is **byte-identical**: `mission_liveness` stays `None` both ways; the mission tuple is `(None,None,None)` both ways (`:2097-2098`); `status` falls back to the admission map both ways; `completion_gate_escalated` is `False` both ways.

**THE ONE DIVERGENCE — the absence set MUST be empty when the batch degraded.** `_batch_instances` returns `{}` on `SQLAlchemyError` (`:2416-2431`), and its own comment says "the caller treats every requested instance as 'unknown'". If the absence set is computed as `requested − found`, a degraded batch marks **every** instance absent and rows whose instance **does** exist render instance-less — a real byte change. The existing pin at `tests/unit/services/test_fix_c_read_model_split.py:815-820` (which patches `SQLModelInstanceRepository.get` because "the per-row fallback inside `_job_to_record` would silently resolve `mission_liveness`") is exactly this case and **must stay green**.

### 3.3 The G9 arithmetic — D-B is not optional

Fixture replay (deterministic, seed 20261029; reproduces the measured BASE numbers to ±5):

| Shape | enrichment population | orphans | singles today | after a top-N slice |
|---|---|---|---|---|
| `/api/jobs?limit=100` | 1,808 | 1,250 | **1,874** (measured ~1,869 ✓) | N=100 → 19 orphans / 30 singles |
| `/api/jobs?…&limit=10` | 1,298 | 932 | **1,394** (measured 1,434 ✓) | N=10 → 7 orphans / 12 singles |
| `/api/work` (no limit) | 1,808 | 1,250 | **1,874** (100 % on-loop) | **no limit ⇒ α cannot apply** |

| Configuration | badge N=10 | page N=100 |
|---|---|---|
| **D-B only (no limit)** | **~6–10 ✓** | **~6–10 ✓** |
| limit + **batched** patch-up, **no** D-B | ~19 ✓ | ~38 ✗ |
| limit + **per-row** `resolve_work` patch-up, with D-B | ~49 ✗ | ~179 ✗ |

⇒ **`/api/work` has no limit param** (`daemon/routers/work.py:109-136`), so the limit lever cannot touch it; **D-B is the only lever there.** And the plan's D-C α **per-row** patch-up is **harmful** — it misses G9 on both jobs shapes.

### 3.4 Implementable statement

1. `_batch_instances` (**sole production caller** `work_resolver.py:1424`; zero test callers) returns `(rows_by_id, degraded: bool)`; `degraded=True` on the `SQLAlchemyError` path (`:2416-2431`).
2. `list_work` computes `absent_ids = (instance_ids - rows_by_id.keys())` **only when `not degraded`**, else `set()`.
3. `_job_to_record` gains keyword-only `instance_absent: bool = False`; `:1779`, `:1847`, `:1878` become `if instance is None and not instance_absent:`.
4. `list_work` passes `instance_absent=(j.instance_id in absent_ids)` on the batch path only; `resolve_work` (`:1102/:1110/:1119`) keeps the default `False`.
5. **Rejected mechanisms:** a separate renderer (drift); a sentinel object (every `is not None` branch flips → `instance.status` raises); a thread-local absence memo (hidden state on a shared singleton).
6. **Pins:** (i) an orphan row costs **0** `SELECT instances` on the list path; (ii) the orphan record is field-for-field identical to BASE; (iii) the **degraded** batch still performs the per-row fallback — the existing W-1 pin (`test_fix_c_read_model_split.py:758-820`) stays green **and** a new node asserts the fallback *did* run.

### 3.5 Two adjacent plan decisions must be REWORKED (they are D-B's execution surface)

- **D-8's slice must land PRE-CONVERSION**, on the merged+sorted **source rows** — not post-conversion on records. A post-conversion slice happens *after* `_job_to_record`, so the singles are already paid. The plan's anchor `work_resolver.py:1272-1276` is the **docstring**; the sort is at **`:1520`** (`records.sort(key=lambda r: _normalize_sort_key(r.created_at), reverse=True)`).
- **D-C α's patch-up must be BATCHED** (one job_ids IN-select + one instance batch + one mission batch), not per-row `resolve_work`. Measured patch-up counts: **8/10** (badge N=10), **76/100** (badge N=100), **34/100** (page N=100) — bounded by **page size**, not by "ordering-tie boundary misses" as D-5 claims. Drivers: the **`root_only` divergence** (the page does not apply it; the enrichment does) + the **status-predicate divergence** (`_canonical_to_job_filters` at `:646-689` requires `terminal_reason='completed'` and never matches `'settled'`) + different ordering (`created_at DESC, priority DESC` at `job_queue/repository.py:1487-1490` vs `created_at DESC` at `work_resolver.py:2320`).

### 3.6 Consequence of rejecting D-B (the rejected option)

`/api/work` stays at **~1,300+ round-trips** (no limit param exists ⇒ the limit lever cannot reach it); the `/api/jobs` page shape misses G9 (~38 rts vs the ~15–30 target); the **92.3 % of statement TIME** item stays open and needs its own commission; **G2 is at risk even with a perfect bridge** (the GIL pressure from ~1,434 singles/request persists); and the default executor stays loaded. The measured targets re-scope to **loop-offloading only**, and the lane's headline benefit halves.

---

## 4. RULING D-16 — BFS-depth txn span: **ROUTE OUT**

### 4.1 Evidence

- **The span is real.** `daemon/repositories/instance/repository.py:1434` opens `with SQLModelSession(self.engine) as db_session:` and holds it across the root COUNT (`:1447-1460`), the root page query (`:1472`), the BFS level loop (`:1548-1620` — one `SELECT … WHERE parent_id IN (...)` per level at `:1567`, up to `_MAX_TRAVERSAL_DEPTH = 256` at `:50`), the KB post-filter (`:1640`), and `_enrich_instances` (`:1645`).
- **The warning semantics live inside it:** `truncated` (set `:1617` when `MAX_DESCENDANTS_PER_PAGE = 1000` fires at `:1596`, returned at `:1645`), the rate-limited WARN/DEBUG (`:1600-1616`), `hit_depth_limit` (`:1625`), the depth WARN (`:1628-1633`).
- **The plan's reachability claim is CONFIRMED.** The children loop is **outside** the span: `instance_lifecycle.py:5090` returns from `repo.list()` (span closed) and the loop at `:5113-5116` calls `list_child_ids_permanent`, which opens **its own** session at `repository.py:432` and closes it at `:438`. ⇒ the new children batch runs in its own session outside the span, and the span's **duration and DB-side fingerprint are unchanged** — only its thread changes.
- **The span has NO declared residence budget.** The Δ3 declaration table (`tests/integration/test_db_pool_idle_in_transaction_guard.py:20-24`) budgets only the guard's own cycle (`DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S = 0.05`, `constants.py:383`) and the deferred-report reconcile P2 (`MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S = 0.25`, `constants.py:394`). The BFS span is judged against the 0.05 s default and the guard's workload does not exercise it ⇒ **currently uncovered**. A 256-level tree would blow the budget; the fixture's 3-level tree (~5 statements) does not.
- **The DWELL class remains unrepaired and is already disclosed open** at `daemon/constants.py:344-368` and `daemon/persistence.py:320-333`.

### 4.2 Weighing

| Factor | Assessment |
|---|---|
| Dwell risk | Real, but **pool-independent of this lane** — the bridge relocates it; the `xact_age_s` fingerprint is a PG-side backend property, unchanged by the client thread. |
| Blast radius | **The highest available** — a response-assembling repository method whose `truncated`/depth-warning semantics are assembled inside it and returned by it. A per-level-session or recursive-CTE rewrite changes the statement sequence, the isolation windows, and the warning assembly in one edit. |
| Duration under the new shape | **Shorter** — the children N+1 leaves the span; post-C-1 the span holds ~5 statements instead of 248. The span's *relative* cost falls without touching it. |
| Precedent | The sibling explicitly scoped it out (`db-pool-idle-txn` A12(ii)); it is entry 16 of the 18-entry routed-out register. |

### 4.3 Routing memo — exact text the plan must carry

> **R-2 — BFS-depth transaction span (`daemon/repositories/instance/repository.py:1434-1645`) — ROUTED OUT of `wi-3f5bea95`.** This lane relocates the span off the event loop (bridge, C-1) and removes the children N+1 that surrounds it; it does **not** restructure the span, does not change its statement sequence, its `_MAX_TRAVERSAL_DEPTH` / `MAX_DESCENDANTS_PER_PAGE` warning assembly (`:1596-1633`), or its `xact_age_s` fingerprint. The DWELL class remains unrepaired and is already disclosed as open in `daemon/constants.py:344-368` and `daemon/persistence.py:320-333`. **Owner:** the DB-pool / R-2 lane (sibling `fix/db-pool-idle-txn` family; entry A12(ii) of the 18-entry routed-out register). **Seam the future commission picks up at:** `SQLModelInstanceRepository.list` — the `include_descendants=True` branch at `repository.py:1434` — whose replacement must (a) open/close a session **per level** (or use a recursive CTE) so no transaction spans the traversal, (b) preserve the `truncated` boolean and the depth-cap WARN semantics (`:1596-1633`) **byte-for-byte**, and (c) declare its per-site residence budget in the Δ3 table (`tests/integration/test_db_pool_idle_in_transaction_guard.py:20-24`), since the BFS span currently has **no** declared budget and is therefore judged against the 0.05 s default.

---

## 5. Secondary rulings

### 5.1 `include_descendants` router/manager default mismatch → **(c)+ KEEP + NAME + MAKE INTERNAL CALL SITES EXPLICIT**

**Verified:** router default `True` (`daemon/routers/instances.py:387`, docstring `:390` "back-compat for every existing consumer"); manager facade default `False` (`daemon/manager.py:14735`); lifecycle default `False` (`daemon/services/instance_lifecycle.py:5027`). The agent tool at `daemon/tools/instance.py:6165-6171` calls `manager.list_instances(limit=DEFAULT_PAGE_LIMIT)` **implicitly** (its own comment at `:6167-6169` says "this tool uses the default (false)"); `daemon/tools/instance_read.py:1673` likewise. The router call at `instances.py:476-485` is **explicit**.

| Candidate | Cost | Byte/risk impact | Semantics-neutral? |
|---|---|---|---|
| One shared constant | — | — | **Impossible as stated** — one constant cannot carry two values. The honest form is a **named pair**. |
| **Internal call sites explicit** | 2 production lines (`tools/instance.py:6170`, `tools/instance_read.py:1673`) | **Zero** byte impact; both sites keep `False` | **Yes.** Makes the manager default non-load-bearing. |
| Remove the manager default (make it required) | 2 production + ~5 test sites; `plugins/` 0 hits | Zero byte impact, but a **public-surface signature change** (an unknown external consumer would `TypeError`) | Out of scope for a stage-only latency lane. |

**Ruling: (c)+.** Keep both defaults; express them as a **named pair** (`INSTANCE_LIST_HTTP_INCLUDE_DESCENDANTS_DEFAULT = True` / `INSTANCE_LIST_INTERNAL_INCLUDE_DESCENDANTS_DEFAULT = False`); make the two internal production call sites pass `include_descendants` **explicitly**; write the docstring. **Zero byte impact, zero signature change, no default flipped.** Rejected: (a) router→False (response-semantics change for any consumer that omits the param → routed to the FE-shapes follow-up contingent on an external-consumer audit) and (b) manager→True (widens the expensive path for every internal caller).

### 5.2 CHILDREN-ORDER byte-diff gate → **two-part gate ACCEPTED with five corrections**

**Verified:** `daemon/repositories/instance/repository.py:432-437` is `select(Instance.instance_id).where(Instance.parent_id == instance_id)` with **no ORDER BY**; `parent_id` is indexed (`models.py:61`) ⇒ today's order is heap order, unspecified by contract.

1. The set-equality pin must be **MULTISET** equality (`sorted(BASE_list) == sorted(HEAD_list)`) — a duplicated child id passes a set pin.
2. It must be **per-parent AND global**: per-parent alone is blind to a child reassigned to an **off-page** parent; global alone is blind to a re-grouping that preserves the global id set.
3. A **static SQL `ORDER BY` pin** is required — a byte gate cannot see an ORDER BY implemented in Python instead of SQL.
4. The batch must cover **ALL returned rows, not just the roots**: the BFS shape returns **241** rows (100 roots + 141 descendants), each with its own `children` array; batching only the root ids would empty 141 children arrays.
5. The **DETAIL path must share the ordered source** (`instance_lifecycle.py:5143` uses the same unordered query) — otherwise the list and detail surfaces disagree about the same parent's children.

Plus: `/api/instances/{id}`'s `children` must be added to the compared set. **What the gate still masks (state it in the plan):** it cannot prove the new order is the *product* order (a pinned constant does that), cannot see a Python-side sort (static pin), and covers only the shapes the harness requests.

### 5.3 TRANSACTION-BOUNDARY RULE — TB-1 … TB-7 (the architect's rule)

A callable is **bridge-eligible iff all hold**. A developer applies this to a candidate without asking anyone.

- **TB-1 — atomic open/close.** A repository method that opens `with Session(engine)` must open **and close** it entirely inside the bridged callable.
- **TB-2 — no session across an `await`** (sibling F8, D-33/D-34).
- **TB-3 — no session across the bridge boundary.** The session is created **and** closed inside the bridged callable; the bridge takes a **callable**, never a session.
- **TB-4 — the callee must not await**, and must not return an awaitable.
- **TB-5 — no callback into the loop** (`asyncio.run_coroutine_threadsafe`, `loop.call_soon_threadsafe` to a coroutine) — it would re-block the loop the work was moved off.
- **TB-6 — no `WriteGuardSession` participant** (`daemon/write_pause_guard.py:277`; the manager's write scope at `daemon/manager.py:2785-2799`). The guard's enter/exit accounting is process-global and the session's lifetime would be split across threads.
- **TB-7 — read-only.** A bridged callable must not perform a **write**; the whole lane is read-only.

**Verified end-to-end on three bridged callees** (session open → close): C-1 `manager.list_instances` → `instance_lifecycle.py:5021` → `repo.list()` opens `repository.py:1434`, closes at the end of that block (after `:1645`); then `list_child_ids_permanent` opens `:432`, closes `:438`. ✔ T-1/T-2/T-1/T-2 `manager.get_instance_info` → `instance_lifecycle.py:5119` → `repo.get()` opens `:621`, closes `:623`; then `:432`→`:438`. ✔ C-3 `resolver.resolve()` (`defer_block_resolver.py:278`): `engine.connect()` opens `:306`, closes `:340` — one connection pinned across the WS2 classification loop (`:336`). Under the bridge the connection still opens and closes **inside** the callable ✔ TB-3, but the **dwell duration is unchanged** — the in-source comment at `daemon/routers/queues.py:651-662` must be updated to "moved off-loop by wi-3f5bea95, dwell still routed" (Phase-4 task).

### 5.4 SCOPE — the cut is **under-scoped on T-1/T-2 and over-scoped on T-3**

**Criterion → site mapping (exhaustive).** G1 is served by **C-1 + T-2** only; G2 by **C-1 + C-3 + C-4/D-B**; G8 by the C-sites; G9 by **C-1, C-2, C-3, C-4**; G10 by C-4(ii)+α; G11/G12 lane-wide. **T-1, T-2 and T-3 satisfy no positive criterion** (T-2 contributes a *risk reduction* to G1 — an unbridged detail path is a genuine on-loop instance-repo read).

| Group | Measured (BASE) | Criterion value | Ruling |
|---|---|---|---|
| **C-1** | flat 13 rts/59 ms; BFS 248 rts/859 ms; 100 % on-loop | G1+G2+G9 — the only group carrying all three | **KEEP** |
| **C-2** | `/api/work` 1,873 rts/8,112 ms, 100 % on-loop | G9 (no other owner) | **KEEP** |
| **C-3** | defer-blocked 89 rts/729 ms, 100 % on-loop | G2 + G9 | **KEEP** |
| **C-4 + D-B** | jobs 1,439/1,874 rts; 92.3 % of statement TIME | G9 + G2 (via GIL) | **KEEP — inseparable** |
| **T-1** | 2 rts/send (`messages.py:204` → `instance_lifecycle.py:5143`); **no harness leg** | none directly; **shares T-2's bridged callable** | **KEEP** — one line on a seam that already exists after T-2. Its "highest-frequency internal path" claim is an **estimate** (`technical-analysis.md:42/46`) — Unverified — but every message send performs a DB read on the loop, so leaving it unbridged contradicts the lane's own objective sentence (`plan-overview.md:21`). |
| **T-2** | ~2–4 ms per panel refetch (estimate); no harness leg | G1 risk-reduction | **KEEP** — same file, same phase, same bridge seam as C-1; near-zero marginal cost. |
| **T-3** | ≤2 rts per DEAD row; **fixture has 0 `dead` rows** (`seed.py:279/:306/:335` seed only `done`/`active`/`queued`) ⇒ **0 measured** | none | **DEFER — with disclosure.** T-3 as planned is **A applied to an ON-LOOP site** (`jobs_crud.py:943` is a plain sync call inside `async def list_jobs` at `:730`), which contradicts the lane's objective, and it is invisible to every gate. If re-entered later it must be **A + C** and the fixture must gain DEAD JobItems to make it measurable. The deferral must be recorded as a known remaining on-loop site. |

**Cut-variant matrix (5 axes):**

| Variant | Complexity (20%) | Scalability (20%) | Maintainability (25%) | Risk (20%) | Cost (15%) | Total |
|---|---|---|---|---|---|---|
| (i) 7 groups as-is | 3 | 5 | 3 | 3 | 3 | **3.40** |
| (ii) 6 groups (−T-3) | 4 | 5 | 4 | 4 | 4 | **4.20** |
| (iii) 4 core, D-B inside C-4 | 5 | 4 | 4 | 4 | 5 | **4.35** |
| **(iv) C-1..C-4 + D-B standalone, T-3 deferred, T-1/T-2 carried** | 4 | 4 | 4 | **5** | 4 | **4.20** |

**Ruling: variant (iv).** Dominant axis **Risk (inverted, 20 %)** — it re-threads the fewest production paths and keeps D-4's documented fallback executable (`decisions.md:64`), because D-B is separable. (iii) ties on Maintainability/Cost but makes a D-B rejection kill the whole C-4 group. The (iv)-vs-(ii) gap is within one Cost point; the deciding evidence is that T-1/T-2 ride a seam that already exists and cost ~2 one-line wraps, while T-3 costs a method + a pin + a *mechanism change* (A→A+C) and buys **no** measurable gate coverage.

**The architect's inclusion test** (one sentence, applicable without asking):

> **INCLUDE a candidate DB-read site in this lane iff it is a synchronous DB read executed on the event loop inside an `async def` FastAPI request handler AND (it is MATERIAL — measured ≥50 ms per call, or ≥50 round-trips per request, or an N+1 that scales with the page/row count — OR it calls a callable the lane already bridges, OR it lives in the same router module on the same resource as an already-included site) AND its fix is expressible as mechanism A (N→1 batch) or mechanism C (bridge the unchanged sync callable) with byte-identical responses (except the single disclosed children-order normalization) AND it is not one of the enumerated carve-outs (the derive-don't-reimplement WS1/WS2 probe gate at `defer_block_resolver.py:466-478`; the BFS traversal span's `truncated`/`_MAX_TRAVERSAL_DEPTH` semantics at `instance/repository.py:1434-1645`; a client-side request cadence).**

### 5.5 SAFETY INVARIANTS that must survive

| Invariant | How the lane preserves it |
|---|---|
| **pool_leak fail-closed (F4-A)** | `daemon/services/readiness.py` is **DO-NOT-TOUCH** — the lane adds no admission logic and changes no pool semantics. **Amendment:** the plan must stop claiming F4-A protects the bridge (it is probe-scoped — §2.5); the bridge's posture is argued from the 25-cap + `pool_timeout=30`. |
| **never-green rollback** | Untouched — the bridge is unwrap-able and the cut is schema-EMPTY (D-13). The bridge's own drain hook (B3) is a **lifespan** change in `daemon/api.py`, not a readiness change. |
| **env-poison guards** | Per-variable scrub only; the `env -u POSTGRES_*` glob idiom is **never** used (it is a silent no-op). The lane adds no `.env` reads. |
| **no schema change** | EMPTY delta confirmed — every mechanism is Python; every touched table is read-only. |
| **no live promote / no live daemon restart / no `.env` edits** | Lane posture; the drain hook (B3) is a **code** change, not a restart. |
| **byte-identity of every response** | Hard gate. The admission gate **defers** (never rejects) and there is **no per-call timeout** (B4) precisely so a saturated bridge can never change a response body. |

---

## 6. BLOCKING GATE DEFECTS — the lane cannot pass its own gate as committed

These are **tester-lane** work items (the harness path `.agents/tester/harness/eventloop-phase0/` is tester-owned per D-12; the developer does not edit it). The plan currently schedules **only** the N4 expectation update (D-12). **All three must be scheduled before the developer's Phase 5.**

### 6.1 G1 — the harness predicate is thread-agnostic AND asserts the pins POSITIVE

- **Thread-agnostic:** `main.py:410-415` computes `fam_any` over **all** thread sections, then stores it under `frame_family_present_in_main_thread` (`:416`). The main-thread identification **is** computed (`main_native` `:385-393`; `main_section` `:396-401`) and then **never used** — `main_section` is dead code. At HEAD the **bridge thread's** stack contains `daemon/repositories/instance/repository.py` + sqlalchemy (the new batch method lives in that file) ⇒ the family pin reads POSITIVE ⇒ **G1 as instrumented is a near-certain FALSE-RED at HEAD, not a flaky one.** The plan (`plan-overview.md:248`) asserts a MAIN-thread scoping the code does not implement.
- **The capture-count fences are BASE-only:** `main.py:337` `fences.check("captures_collected", len(dirs) >= 1)`; `:423-427` `fam >= 1`; `:434-437` (full profile) `narrow >= 1`. **At HEAD — where the pins must go NEGATIVE — the harness exits 1 on the success condition.** It has no HEAD mode.

**Corrected fence (paste into the harness expectation):**

> A capture FAILS G1 **iff the LOOP-thread section** of `stacks.txt` contains the substring `daemon/repositories/instance/repository.py` **AND** the substring `sqlalchemy` (case-insensitive). The loop thread is the section whose stack contains `asyncio/base_events.py` (do **not** match the faulthandler header against `threads.csv` `native_id` — that is the kernel TID, a different id space from faulthandler's pthread id). The router file `daemon/routers/instances.py` **alone is NOT a violation** — dropping it is exactly what excludes the disclosed UI-prefs residual (`instances.py:496` → `instance_ui_prefs/repository.py`), which never contains `daemon/repositories/instance/repository.py`. **Non-vacuity:** G1 is INCONCLUSIVE (not PASS) if the capture phase collected 0 captures; the synthetic handler-load leg must supply ≥1 capture at BASE and 0 instances-path frames at HEAD.

### 6.2 G2 — the band is two-sided and will false-red on a healthy lane

The 0.067 s anchor = the BASE **unloaded** flat p95 (67.8 ms, `BASELINE.md:38`); the 0.23 s upper anchor and the 0.51 s degraded marker are commission-body values, not re-derivable from `BASELINE.md` (D-18).

Post-lane flat shape = 1 count+page + 1 children-batch (off-loop) + **2 prefs (on-loop)** = **4–5 rts** (BASE 13). **Defensible expected HEAD loaded flat p95: 0.02–0.10 s (point estimate ≈0.04 s)** — own DB work 13→3 statements (59 ms → ~10–15 ms), bridge hop ~0.5 ms, prefs ~2 ms, bridge queueing ~0, the loop's remaining blocker is the missions' 3 on-loop rts (~18 ms/tick), and D-B collapses the default executor's 1,439-statement storms. **That expected value is BELOW the band's lower anchor ⇒ the band as written fails a lane that works.**

**Corrected fence (one-sided, floor re-purposed):**

> LOADED flat(10) p95 (badge composite, paired BASE↔HEAD run, same session/fixture): **p95 ≤ 0.230 s → PASS; 0.230 s < p95 ≤ 0.510 s → FAIL (degraded band); p95 > 0.510 s → FAIL (BASE-equivalent). A measured p95 BELOW 0.067 s PASSES** — record it as an improvement. Non-vacuity: `badge_ticks ≥ 5`, every flat-leg response HTTP 200, and the flat shape's round-trip count ≤ 6 (G9). Expected healthy value **0.02–0.10 s**. If the developer's HEAD p95 exceeds **0.12 s**, raise the bridge pool to 6 workers **before** investigating anything else — C-3 parks a 729 ms job on the pool that G2's own leg competes for.

### 6.3 G9 — "instances-path on-loop 0 in every shape" false-reds on the routed-out missions shape

The harness classifies statements by **table set** (`child.py:69-77` `statement_shape()` = "Verb + sorted table set"), so the missions page leg's `SELECT instances` statements classify as `SELECT instances` **while on-loop** (`BASELINE.md:62`). The plan intends this; G9's wording does not admit it.

**Corrected fence (per shape, the on-loop shape-set must EQUAL the disclosed residual):**

> instances flat(10) ⇒ exactly `{"SELECT instance_ui_prefs": 2}` (the D-9 residual; **no** `SELECT instances` on-loop); instances BFS(100) ⇒ same; missions(20) ⇒ exactly `{"SELECT instances": 2, "SELECT job_queue_items": 1}` (routed-out census #4 — **explicitly exempt**, not a failure); every other shape ⇒ on-loop = 0. Round-trip totals per the plan's §(d) table.

### 6.4 G4 — the bare p95 comparison is not falsifiable at the tick floor (non-blocking, but must be amended)

The probe measures `moment - last_exec_at` (`lag_probe.py:254`) — the interval between successive loop-callback executions ⇒ **the healthy value IS the watchdog tick period (~0.5 s)**. BASE p95 = 0.529 / 0.508 / 0.510 s are all one tick ±30 ms of jitter ⇒ **the minimum detectable effect of the p95 comparison is ≈30 ms, below the noise floor.** A strictly-better HEAD can measure *equal or greater* ⇒ **false-red**; and p95 is nearest-rank over 169–288 samples (`lag_probe.py:107`), so a regression affecting <5 % of ticks is invisible in p95 and visible only in **max** (BASE 0.903 / 1.408 / 2.215 s). The paired BASE↔HEAD run does **not** resolve this — the noise is tick jitter, not system state.

**Corrected fence:**

> For each profile (badge / home / jobs), using the same window-local lag series the BASE run uses: TICK = 0.5 s, STALL = samples > TICK + 0.25 s (0.75 s). G4 PASS iff, per profile: (a) HEAD's STALL count ≤ BASE's STALL count; (b) HEAD's **max** ≤ BASE's max (no tolerance — a clean loop's max ≈0.55 s vs BASE 0.90–2.22 s); (c) HEAD's p95 ≤ BASE's p95 **+ one TICK** — the bare p95 comparison is forbidden; (d) non-vacuity: each profile's sample count ≥ 0.5 × BASE's, and the synthetic handler-load leg's series is non-empty. **The synthetic handler-load leg is what carries the discriminating signal** (STALL count > 0 at BASE, 0 at HEAD).

**The synthetic leg does not exist today** (`grep -rn synthetic .agents/tester/harness/eventloop-phase0/` → 0 hits). It is a **tester-lane** deliverable the plan references but never schedules.

---

## 7. A site the plan does not cover: `/api/work` response serialization stays on the loop

`daemon/routers/work.py:189` — `return [r.to_dict() for r in records]` — runs **after** the bridged call at `:181`, on the loop, and is **O(population)** (1,808 records in the fixture). The plan's phase-3 task 3.6 wraps only `:181`, so this residual loop-blocking work is neither moved nor disclosed. At the measured scale it is the difference between "the `/api/work` shape no longer blocks the loop" and "it blocks the loop less".

**Ruling: the serialization must either move with the fetch, or be explicitly disclosed as a measured residual.** Preferred: move it — `WorkRecord.to_dict()` is pure (`work_resolver.py:329`) and the bridge carries only plain data, so moving the serialization inside the bridged callable keeps the bytes identical **provided** the developer first confirms `to_dict()` has no request-context dependency. If it does, disclose it as a residual with its measured cost rather than moving it. Either way the lane evidence must carry it.

---

## 8. Plan amendments the developer must fold back (A1–A16)

| # | Amendment | Replaces |
|---|---|---|
| **A1** | D-2 is decided: **bridge + A-batches; async-native routed to D-47/D-58** with the bridge-queue alarm as the explicit re-open trigger; the deferral must be **funded** (§2.6) or D-2 is recorded as "Option 1 + A-batches". | D-2 "ARCHITECT-DECIDABLE — Option 3 recommended" |
| **A2** | `DB_BRIDGE_MAX_WORKERS = 6`. **Reject** the "may drop to 3 for margin" — 3 is below the already-reachable burst of 4. | D-7 (4) |
| **A3** | Capacity is **25** (pool 15 + overflow 10), cited at `daemon/constants.py:264` + `daemon/repositories/factory.py:434-435`, matching `daemon/services/readiness.py:165 MANAGER_POOL_CONFIGURED_CAPACITY = 25` and the D-57 pin at `tests/postgres/test_dbpool_probe_starvation_pg.py:96`. Delete "4 ≤ overflow headroom" (category error: a bridge worker holds ≤1 connection ⇒ B workers ≤ B of 25). | D-7, D-17, `phase1-plan.md:1.3` |
| **A4** | **Stop claiming F4-A protects the bridge** — it is probe-scoped (`readiness.py:1541`, called only at `:1627` and `:1929`, both inside `_probe()` closures). | D-17 |
| **A5** | The plan's shutdown premise is **false**: `daemon/services/upgrade_journal_sweep.py:333` *does* shut a dedicated executor down. Accurate restatement: no **module-global** dedicated executor is shut down. Add `await db_bridge.shutdown()` to the lifespan **before** `await manager.shutdown()` (`daemon/api.py:3809`), ahead of `("dispose_db_pools", …)` (`daemon/manager.py:15223`). | `phase1-plan.md:1.3(b)` |
| **A6** | The seam spec must accept **kwargs** (`run_db(fn, *args, **kwargs)`). `daemon/routers/work.py:181-187` is keyword-only; `technical-analysis.md:81`'s `partial(fn, *args)` breaks it. | `technical-analysis.md:81` |
| **A7** | Add B1 (re-entrancy guard), B2 (BaseException containment), B4 (asyncio.Semaphore admission gate — defer, never reject, never timeout), B6 (no executor parameter), B7 (`thread_name_prefix="db-offload"`), B9 (TypeVar, not `-> Any`) to Phase 1, each with a pin. | Phase-1 task list |
| **A8** | Rename `daemon/services/db_bridge.py` → `daemon/services/db_offload.py`, `run_db` → `offload` — **before** the Phase-1 API freeze. `daemon/services/main_loop_bridge.py` is a bridge in the *opposite* direction; `db_pool_manager.py` owns a different subsystem. | Phase 1 |
| **A9** | The executor-identity pin must assert the **thread-name prefix**, not `is not loop._default_executor` (near-tautological). | `phase1-plan.md:1.4` |
| **A10** | Constants: default tier in `daemon/constants.py`, env-overridable field in `daemon/config.py`'s `LimitsConfig` (`LIMITS_` prefix), per the `checkpoint_pool_max_size` precedent. | `phase1-plan.md:1.2` |
| **A11** | **D-B covers three sites** (`:1779`, `:1847`, `:1878`) and must be **degradation-aware** (empty absence set when `_batch_instances` degraded). Mechanism per §3.4. | D-4, `phase3-plan.md:3.2` |
| **A12** | **D-C α's patch-up must be batched** (one job_ids IN-select + one instance batch + one mission batch), **not** per-row `resolve_work` — measured 8/10, 76/100, 34/100 patch-ups, and the per-row variant misses G9. | D-5, `phase3-plan.md:3.5` |
| **A13** | **D-8's slice must land PRE-CONVERSION** (on the merged+sorted source rows). The sort is at `work_resolver.py:1520`; the plan's cited `:1272-1276` is the **docstring**. | D-8, `phase3-plan.md:3.3` |
| **A14** | **T-3 DEFERRED** with a disclosure that `daemon/routers/jobs_crud.py:943` remains a known on-loop site (A→A+C required if re-entered; fixture must gain DEAD rows). **T-1 and T-2 CARRIED.** | D-11, `phase3-plan.md:3.7` |
| **A15** | Fix the citation errors: the prefs call is at `daemon/routers/instances.py:496` (`:495` is the comprehension); `_MAX_TRAVERSAL_DEPTH` is at `daemon/repositories/instance/repository.py:50` (the in-repo docstring at `:828` still says `:33`); the default-executor site count is **1,400** at `b1183a637` (the sibling's 1,351 was accurate at `6bebb9ba0` — drift, not error — and must be re-derived with the commit pinned); the default executor's `max_workers=12` is `min(32, cpu+4)` on an 8-CPU host, **not** a configured value (no `set_default_executor` in `daemon/`). | multiple |
| **A16** | Schedule the **tester-lane** gate repairs: G1 thread-scoping + a HEAD mode (the harness currently asserts `captures_collected >= 1`, `fam >= 1`, `narrow >= 1` — it requires the pins POSITIVE), G2's one-sided band, G9's per-shape residual set, G4's STALL/max fence, and the **synthetic handler-load leg** (does not exist). | D-12, TEST/PIN PLAN (b)(c)(d) |

**Additionally: the `include_descendants` docstring text (§5.1) and the children-order assertion set (§5.2) must be folded into Phase-2 task 2.6 / 2.7.**

---

## 9. Decisions pending

- **D-18 anchors** — the 0.23 s upper anchor and the 0.51 s degraded marker remain commission-body values, not re-derivable from the committed `BASELINE.md`. The tester must confirm them against the sibling lane's composite record during the pre-gate; any deviation is an **explicit test-plan amendment**, never a silent re-baseline. *(This is the dispatcher's non-blocking item; it is unchanged by this recommendation.)*
- **The `/api/work` serialization** — move or disclose (§7) depends on whether `WorkRecord.to_dict()` is request-context-free. The developer confirms this in Phase 3; the architect's preference is "move", with "disclose" as the fallback.

## 10. Open questions for the leader

1. **Who owns the harness HEAD-mode repair?** The committed harness requires the G1 pins to be **positive** (`main.py:337/423/437`), so the gate cannot pass at HEAD without a tester-lane change to a tester-owned path. D-12 currently covers only the N4 expectation update. **If the answer is "the tester lane, commissioned separately", I would dispatch that commission now and gate the developer's Phase 5 on it; if it is "the developer edits the harness", the lane posture's D-12 rule must be formally relaxed for that path.** I recommend the former.
2. **Is the deferral contract funded?** D-2 is decided as "bridge + A-batches"; the hybrid label survives only if the five artefacts in §2.6 ship. **If the lane's phase budget cannot absorb them, record D-2 as "Option 1 + A-batches"** — the work is identical and the label is the only thing lost. I recommend funding at least the production registry and the registry-coverage pin (items 1 and 5), which together cost one module and one test file.
3. **T-1/T-2 carry vs defer** — I rule **carry** on the "same seam, two one-line wraps" argument, which diverges from the criterion→site mapping (T-1/T-2 satisfy no criterion). **If the leader's definition of done is strictly G1..G12 and the objective sentence (`plan-overview.md:21`) is subordinate, defer both** and record the send path and detail path as routed-out with the in-source comment. This is the one place where my ruling could reasonably go the other way, and the deciding evidence is the objective sentence's status.

---

## 11. Evidence index (analyst attribution)

| ID | Skill | Assignment | Load-bearing outputs folded into this ruling |
|---|---|---|---|
| W1 `fd1e6917` | structural-design | D-2 Option 1 (bridge) | B1–B10; shutdown seam + `upgrade_journal_sweep.py:333`; the "capacity 25" re-cite; the near-tautological executor pin; `copy_context()` needed for **measurement validity** (the harness's `REQ_TAG` attribution), not just logging; the bridge's 10-clause eligibility rule |
| W2 `b2420093` | structural-design | D-2 Option 2 (async-native) | the corrected Option-2 cost model (14 units/6 modules); the `text()` correction; the `connect_args`/`server_settings` delta; the two-pool leak oracle (`api.py:4154-4155`); F8-as-default-idiom; the half-true conversion seam |
| W3 `8670c0a9` | structural-design | D-2 Option 3 (hybrid) | "A alone cannot satisfy G1" (three grounds); the R1–R4 nesting rule; the deferral-artefact list; the T-3-is-A-on-an-on-loop-site finding; the kwargs spec mismatch; the double-abstraction trap |
| W4 `89942d27` | data-flow-design | D-4/D-B, D-16, TB-1..7, children-order, `include_descendants` | the three orphan lookup sites; the byte-identity field analysis; the degraded-batch divergence; the G9 arithmetic; the D-8/D-5 rework; the D-16 routing memo and the Δ3 budget gap; the children-order corrections |
| W5 `622cc199` | scalability-design | executor sizing, pool envelope, saturation, admission control | the 6-worker sizing and the rejection of 3; Little's Law vs burst; the F4-A probe-scoping; the GIL "moved not removed" insight; the admission-gate recommendation and constant set; the `work.py:189` serialization finding |
| W6 `62a9ed50` | trade-off-analysis | scope, criterion mapping, gate-fence soundness | the criterion→site table; the three blocking gate defects and their literal corrected wording; the cut-variant matrix; the inclusion test |

**Verification performed by the architect** (in-tree, at `42764588c`): `main.py:337/396-416/423-437`; `child.py:69-77`; `seed.py:279/306/335`; `driver.py:42-59/62`; `readiness.py:165/1501/1541/1627/1929`; `constants.py:264`; `factory.py:143-144/155-156/434-435`; `upgrade_journal_sweep.py:333`; `api.py:3390/3809/4154-4155`; `manager.py:15123/15223`; `work_resolver.py:1396-1397/1421-1424/1778-1878/1520/2226-2413/2431`; `instances.py:387/496/560`; `instance_lifecycle.py:5021/5027/5090/5113-5116/5143`; `repository.py:50/432-437/1434-1645`; `work.py:181-189`; `queues.py:651-662`; `tools/instance.py:6165`; `tools/instance_read.py:1673`; `manager.py:14735`.
