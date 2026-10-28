# Final Implementation Spec — db-pool-idle-txn lane (BINDING CONTRACT)

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`).
- **Worktree:** `/home/nea/ensemble-src-wt-dbpool-idletxn`.
- **Base commit for every `file:line`:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (= `origin/latest` = staged **v0.27.11**).
- **Source of authority:** `council-report.md` (same directory) — **VERDICT: 🔴 REJECT**. This spec is the contract the developer builds against **once the plan set is folded** (see §0).
- **Date:** 2026-10-28.

> **How to read this.** §0 is a gate, not a formality. §1–§8 are binding: a change not listed here is out of contract; a listed change not built is a gate failure. Every line cites a `file:line@base` so the implementer can verify drift before writing code (C4: line numbers from the incident snapshot are **stale** for `readiness.py` / `persistence.py` / `constants.py` — re-verify at your own base; drift is a plan-amendment trigger).

---

## §0 PHASE 0 — BINDING PRECONDITION (leader ruling 5)

**This section is a GATE, not a formality. No implementation change lands until Phase 0 COMPLETES and `BASELINE.md` exists.** The council had **no DB access**; several PG-semantics claims in this spec (last-`-c`-wins, idle-in-txn vs lock-blocked exclusivity, `idle_in_transaction_session_timeout` firing thresholds, residence times, `application_name` attribution) are **documentation claims, not measurements**. They MUST be measured before we build on them.

**Phase 0 runs FIRST**, on a **lane-created SCRATCH PostgreSQL database**:
- **NEVER** `ensemble_prod`. **NEVER** the live daemon's DB. **NEVER** a restart of anything.
- Port `5433`, throwaway role, fresh scratch DB per run, dropped at teardown.
- **Per-variable env scrub** — `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT`. `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) — an ambient `POSTGRES_DB=ensemble_prod` survives it.
- Test scripts must NEVER resolve live-shaped HOME-derived install paths (mktemp FAKE_HOME + escape guard).
- Interpreter: `/home/nea/ensemble-src/.venv/bin/python` (C-1).

**Phase 0 deliverable: `BASELINE.md`** (fixed filename) in the planning directory, produced and reviewed before Phase 1 begins. The sibling plan worker owns `phase0-plan.md` and the BASELINE.md contract — align by fixed filename; do not read the sibling's file.

**Four evidence outputs (all required):**

**(a) Base-code M-R3 hold duration under real load** — this **SETTLES the architect's F8 flip condition** (leader ruling 1: if M-R3 holds a manager slot < 2 s under real load, approach C collapses to approach B). Drive `_handle_recover_deferred_report_async` (and the sync twin) against scratch PG with a deliberately slowed content fetch — **leader ruling 4 prefers an instrumented checkpointer** (whose `aget_tuple` sleeps a known duration); the checkpoint-table lock is the **fallback**. Sample `manager.engine.pool.checkedout()` at ≤ 100 ms across the reconcile call. Record: measured max legitimate hold, P100, and the resulting `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS = 4 × measured max hold` (floor 30 s). If unmeasurable, the bound ships **DISABLED** (constant defined, NOT applied).

**(b) The occupancy/residence census shape at base** — this **defines F7's assertion shape** (C-10). Sample `pg_stat_activity` filtered by the scratch DB's `datname`, capturing `count`, `state`, `wait_event_type`, `state_change`, `xact_start`, `backend_start`, `application_name`. Report residence-time buckets (distinguish "6 short borrows" from "1 long leak"). F7's thresholds/formula are fixed from this measurement, not guessed.

**(c) Measured PG runtime semantics** (documentation claims, not measurements, until Phase 0):
- `last_query` fingerprints per pool (manager / repair / asyncpg adapter) — settles the A-1 attribution question.
- `application_name` attribution per pool — confirms the three named engines (F2.3, F2.5, F2.6) are distinguishable.
- Whether `idle_in_transaction_session_timeout` actually fires as documented (threshold + SQLSTATE `25P03`).
- PG last-`-c`-wins behaviour under the daemon's exact connect strings (confirms D-17 isolation).
- `SET LOCAL statement_timeout` semantics inside SQLAlchemy's autobegin (confirms F4's probe-scoped bound lands on the observed pool).

**(d) The 3-pool attribution census** explaining the incident's 18 observed backends vs the 15-slot manager cap: count `pg_stat_activity` rows by `application_name` (and by `datname` for the scratch DB) across manager / repair / asyncpg-adapter / P2-oracle pools. Multi-pool ⇒ unattributable without `application_name` ⇒ confirms F2.3/F2.5/F2.6.

**Gate:** no implementation change in §1 lands until Phase 0 COMPLETES and `BASELINE.md` exists and is reviewed. Phase 0's measurement also fixes the two provisional literals in §2 (`MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`, `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`) — they are placeholders until Phase 0 replaces them.

---

## §0b FOLD PRECONDITIONS — the plan must be folded and re-reviewed FIRST

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
| F8.4 | `daemon/manager.py:10860-10861` + `:11110-11112` + `:11208` + `:11216-11224` (sync twin) | Same three-phase shape. The twin is called from a **non-loop** `threading.Thread` (`manager.py:11180-11184`) ⇒ the `asyncio.run_coroutine_threadsafe(…).result(timeout=8.0)` bridge stays unchanged and **no deadlock** is introduced. The nested `self._has_live_process_report_carrier(...)` (`:11110`) must also move out of the held scope. | F8-a, F8-b |
| F8.5 | `daemon/tools/instance.py:1207` (`with WriteGuardSession(Session(manager.engine), manager.write_guard) as session:`) + `:1360` (`await _bus.watch(...)`) | **Third borrow-then-await site.** Either move the bus-watch registration out of the scope, or obtain a leader ruling to exclude it — and then **disclose the residual** (it is a write path under `WriteGuardSession`). The static pin is **RED from birth** on this site otherwise. | F8-a + leader ruling (council OQ 2) |
| F8.6 | `daemon/manager.py:11529-11541` (terminal guard) and `:11555-11561` (dead-parent guard) | **Re-assert both inside the P2 write scope** (they are currently evaluated on a pre-await snapshot ⇒ a concurrent escalation slips through ⇒ **double delivery**). | F8-c (new pin) |
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

### F4 — readiness-probe server-side bound *(plan D-04/D-05/D-06/D-07; A4-v; C-7; A-7)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F4.1 | `daemon/services/readiness.py:1476-1481` (`make_db_probe` body) | Inside the probe's own connection scope, issue `SET LOCAL statement_timeout = <ms>` as the **FIRST** statement (it opens the autobegun transaction that scopes the setting). **PG-only** — dialect-guarded exactly as `daemon/tools/ens_db_tools.py:793`. The value is **derived** (never a literal): `2 × DB_PROBE_TIMEOUT_S` (`readiness.py:113`, 0.5 s ⇒ 1000 ms). Derive the millisecond value in **ONE** place so the two probes cannot drift. | F4-a, F4-c |
| F4.2 | `daemon/services/readiness.py:1746-1789` (`make_queue_probe` body) | Same, with `2 × QUEUE_PROBE_TIMEOUT_S` (`readiness.py:127`, 2.0 s ⇒ 4000 ms). | F4-a, F4-c |
| F4.3 | `daemon/services/readiness.py:1476-1481`, `:1746-1789` | Issue an explicit `Connection.rollback()` after the probe's reads, before the connection is returned (SQLAlchemy 2.0.48: a documented no-op when no transaction is active). | F4-a |
| F4.4 | `daemon/services/readiness.py:107-113` | **Correct the docstring.** Its premise — *"the driver's own connect/execute timeout releases it — bounded leakage"* — is **false at base**: the manager engine sets **no** execute timeout (`factory.py:323-338`; the URL's `connect_timeout` bounds the *connect* phase only). **Phase 1's DO-NOT-TOUCH list must be amended to permit this** (it currently says "the two probe bodies only"). | F4-a (source-level) + diff review |
| F4.5 | `daemon/services/readiness.py` — the **saver probe** | **STAY EXCLUDED.** The P2 psycopg pool is `autocommit=True` (`daemon/persistence.py:328`) ⇒ every statement is its own transaction ⇒ `SET LOCAL` would be a no-op with a PG warning. | F4-a (asserts exclusion) |
| F4.6 | `daemon/services/readiness.py:1263-1296` (`_guarded`), `:1279-1281` | **NO CHANGE.** The 2× bound keeps the **client-side** `asyncio.wait_for` firing first ⇒ the cancelled `to_thread` future's later `QueryCanceled` is never observed ⇒ the class stays `probe_timeout` ⇒ **grace-eligibility preserved**. At ≤ 1× the server error would arrive as an exception ⇒ `FAILURE_CLASS_HARD_COMPONENT_RED` ⇒ **not** grace-eligible ⇒ a behaviour change. The **relationship** (value = 2 × the constant) is what must be pinned, not the number. | F4-a, F4-c |
| F4.7 | `phase1-plan.md` D-04 outcome + T1.7 | **Restate the guarantee**: `SET LOCAL` bounds the **statement** duration; the **checkout** wait remains bounded by `pool_timeout` (D-15) and the default-executor ceiling (A7). T1.7 must assert **connection return** (`engine.pool.checkedout()` back to baseline; the abandoned thread terminated) — **not** "no idle-in-txn backend" (see C-7). | F4-b (corrected) |

### F6 — idempotent mission-record boot mint *(plan D-08/D-09/D-10; A11; W-4; A-6)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F6.1 | `daemon/repositories/mission_record/repository.py:498-514` (`insert_mission`, own-session path) | Replace the ORM insert with a dialect-appropriate insert (`pg_insert` / `sqlite_insert` — house helper shape at `daemon/repositories/source/repository.py:33-46`) carrying `.on_conflict_do_nothing(index_elements=["anchor_instance_id"], index_where=text(OPEN_EPOCH_STATUS_SQL))`. On `rowcount == 0`, re-resolve the winning row via the **anchor-scoped** probe (`find_open_epoch_by_anchor`, `:69-93`) and return it. | F6-a, F6-b |
| F6.2 | `daemon/repositories/mission_record/models.py:209` (`OPEN_EPOCH_STATUS_SQL`) | **Reuse, do not redefine.** The conflict target must be **byte-identical** to the existing partial unique index `ux_mission_records_anchor_open` (`:307-313`, `postgresql_where=text("status = 'open'")`, mirrored at `daemon/manager.py:8548-8552` and `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`). | F6-a |
| F6.3 | `daemon/services/mission_record_service.py:544-567` (`_mint_epoch_locked`'s IntegrityError branch) | The boot path's re-resolve becomes the **anchor-scoped** probe. The code's own comment (`:558-565`) records that the current `founding_message_id`-keyed probe "can NEVER be found by that probe" for a NULL-founding boot epoch ⇒ the boot path has **no** re-resolve path today. | F6-a, F6-b |
| F6.4 | `daemon/services/mission_record_service.py` — the `session is not None` branch | **UNCHANGED** (D-10): the caller-session path keeps its documented "poison the caller's txn and re-raise" semantics. | F6-a |
| F6.5 | `daemon/repositories/mission_record/repository.py:498-514` — the **target DB's** index | **Add a read-only assertion** that the target DB **has** `ux_mission_records_anchor_open` and that its predicate is **byte-identical** to `OPEN_EPOCH_STATUS_SQL`. If the index is absent (its absence is exactly OQ-1's duplicate-OPEN data shape) the boot mint would raise **42P10** instead of the IntegrityError being repaired. Add a "no index" negative leg asserting the failure is **loud**. Record a read-only prod index-presence SELECT in the gate report (no data remediation — leader-ruled out of scope). | F6-c (new) |
| F6.6 | The lane's schema surface | **ZERO new `.sql` files.** Add a companion assertion that the **ORM diff introduces no new TABLE/COLUMN/INDEX** — a `.sql` file-count check alone would not catch an ORM-only table (A-6). | F6-d (new) |

### F2 — manager-engine pool hardening + per-engine attribution *(D-13…D-22; A5; A6; C-2; C-3; C-8; C-9)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F2.1 | `daemon/constants.py` | Add the canonical knob constants (**values** live here — D-18): `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS`, `MANAGER_ENGINE_POOL_RECYCLE_S = 3_600`, `MANAGER_ENGINE_POOL_TIMEOUT_S = 30`, and `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S` (the **measured** budget, C-4). Each carries its precedent citation. | F2-a (knob matrix) |
| F2.2 | `daemon/repositories/factory.py:252` | **DO NOT EXTEND `PG_SESSION_CONNECT_ARGS`.** It is embedded **last** into the repair engine's options string (`daemon/tools/ens_db_tools.py:619-627`) ⇒ PG's **last `-c` wins** ⇒ extending it would silently retune the repair engine's `statement_timeout` 60 s → 30 s (D-17). Already fenced by `tests/unit/repositories/test_pg_engine_utc_session.py:65` — keep that assertion green. | F2-f (repair-engine pin) |
| F2.3 | `daemon/repositories/factory.py:252` (new constant) | Add `MANAGER_PG_SESSION_CONNECT_ARGS` assembled in `factory.py` from the `constants.py` values. **Rendered string (order is load-bearing) is a TEMPLATE, not a literal:** `-c timezone=UTC -c application_name=ensemble-manager -c idle_in_transaction_session_timeout=<MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS>` where `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` is the value fixed by Phase 0's measurement per the §3.4 rule (`≥ 4 × measured max legitimate hold`, floor 30 s), recorded in the gate report. The literal `60000` appearing elsewhere in this spec is a **PRE-MEASUREMENT PLACEHOLDER** that Phase 0 replaces — it is NOT an unconditional value. **Unmeasurable disposition:** if Phase 0 cannot measure the hold, the knob is **defined but NOT applied** (the `idle_in_transaction_session_timeout` clause is omitted from the rendered string; enablement gated on the measurement). **NO `statement_timeout`** — it is DROPPED per the leader ruling (C-3): `manager.py:8760-8762` runs the whole `_ensure_postgres_columns` list in ONE transaction, documented as *"FULL TABLE REWRITE with ACCESS EXCLUSIVE lock… may take seconds"* (`:7877-7879`), fail-loud (`:7554-7560`) ⇒ a 30 s cap there means **the daemon does not boot** on a steady-state no-op path. Any future global cap must `SET LOCAL statement_timeout = 0` inside `manager.py:8760`. | F2-a, F2-e |
| F2.4 | `daemon/repositories/factory.py:323-338` (`create_engine`) | `connect_args=dict(MANAGER_PG_SESSION_CONNECT_ARGS)`; **add** `pool_reset_on_return="rollback"`, `pool_recycle=3600`, `pool_timeout=30`. **Leave** `pool_size=5`, `max_overflow=10`, `pool_pre_ping=True` untouched (C-4 — the soak lane's sizing is on the P2 pools, not this one). `pool_use_lifo` deliberately **not** set. | F2-a, F2-b, F2-c, F2-d |
| F2.5 | `daemon/tools/ens_db_tools.py:603-633` (repair engine) | The **only** knob change is `pool_reset_on_return="rollback"`. Its options string gains `-c application_name=ensemble-repair` appended to **its own literal** (a **distinct** value ⇒ no last-`-c` collision). Every other knob (`pool_size=2`, `max_overflow=3`, `pool_pre_ping=True`, `pool_recycle=3600`, `pool_timeout=10`) and the rest of the options string (`statement_timeout=60000`, `lock_timeout=10000`, `idle_in_transaction_session_timeout=60000`, `timezone=UTC`) stay **byte-identical**. | F2-f |
| F2.6 | `daemon/persistence.py:355-359` (asyncpg checkpoint-adapter pool) | **Third engine — the one A5 missed.** It opens an **explicit** transaction (`daemon/checkpoint_adapter.py:1231`: `async with conn.transaction():` with three `await`s between DELETEs) ⇒ its backend sits `idle in transaction` **between statements**, with **no** server-side reap. Give it a **separate DSN** carrying `-c application_name=ensemble-checkpoint-adapter`. **DO NOT touch the shared `conn_string`** (`persistence.py:225`) — it also feeds the P2 psycopg **oracle** (`:317`), and touching it would break the P2-immutability pin (T2.7/AC18) and put the C1-protected oracle into the lane's diff. | F2-g (new), F2-h (new) |
| F2.7 | `daemon/constants.py:207-217` and `daemon/persistence.py:306-315` (honesty blocks) | Refresh both to state: **retention / transaction-hygiene class REPAIRED** by this lane (branch + date); **DWELL / OCCUPANCY class NOT REPAIRED** — residual owner = the event-loop sync-DB follow-ups (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) and the BFS depth loop (`daemon/repositories/instance/repository.py:1434-1645`). **Do not** say "the P1 idle-in-transaction leak is REPAIRED" — P1 has occupancy under dwell, not a leak. **Knob values in these blocks must not move.** | F2-i (new) |
| F2.8 | `tests/unit/repositories/test_pg_engine_utc_session.py:86-88` | **Explicit pin amendment** (C-9, **D-37**): the exact-equality assertion on the manager engine's `connect_args` must be amended to the new string, value-by-value, **order-pinned**. **Keep `:65` untouched** — it is the D-17 guard. The file is **not** in `SEALED_SHA_BASELINES` ⇒ **no seal re-pin ceremony**. Correct F2-e's stale `:292` anchor → `:86-88`. G2.4 becomes "amended per **D-37**, diff attached to the gate report". | F2-a, F2-e, F2-f |
| F2.9 | `daemon/repositories/factory.py:215-222` (latent config-driven PG branch) | **NOT hardened** (D-22) — zero daemon-runtime PG callers, deliberately config-driven. | diff review |
| F2.10 | `daemon/repositories/factory.py:184-199` (SQLite), `daemon/__main__.py` (boot preflight), `daemon/persistence.py:316-331` (P2 oracle), `daemon/tools/ens_db_tools.py:649-695` (vacuum NullPool), `daemon/auth/cli.py:152-160` (one-shot CLI) | **NOT touched** — except the honesty comment in `persistence.py:306-315` (F2.7). | F2-j (P2-immutability pin) |

### F5 — wedge-capture table name *(D-20)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F5.1 | `daemon/services/wedge_capture/db_capture.py:308` | `FROM tasks t` → `FROM task t`. | F5-b, F5-c |
| F5.2 | `daemon/services/wedge_capture/db_capture.py:387` | `FROM tasks` → `FROM task`. | F5-b, F5-c |
| F5.3 | `daemon/services/wedge_capture/db_capture.py:34` (docstring) | "``tasks`` rows" → "``task`` rows". **No other change** to the module. The path rides a one-shot `psycopg.connect` (`:196-200`) with `close()` in `finally` (`:289-294`) ⇒ **no pool**, so it cannot leak. | F5-b |

### F1 — the audit record *(D-11)*

| # | Where | Exact change | Verify |
|---|---|---|---|
| F1.1 | `.agents/shared/planning/db-pool-idle-txn/audit-findings.md` (new) | Publish the census headline (**AST baseline**, not the retired "~689"): family counts A=613 / C=4 / B=175 / D=148 / E=14 / F=0, method cited to `audit-method.md` by filename. Record RCA sites **B** and **C** as audited-and-clean with their premise corrections. Label the five-backend reconciliation as **inference**. State that **no leak class was found where the census found none**. | F1-a (doc existence) |

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
| `connect_args` | `{"options": "-c timezone=UTC"}` | `{"options": "-c timezone=UTC -c application_name=ensemble-manager -c idle_in_transaction_session_timeout=<MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS>"}` (value = Phase-0 measurement per §3.4; the literal `60000` is a PRE-MEASUREMENT PLACEHOLDER) | **EXTENDED via a NEW constant** — `PG_SESSION_CONNECT_ARGS` stays byte-identical |
| URL `connect_timeout` | `BOOT_DB_TIMEOUT_S` (10) | unchanged | — |

### Constants to add in `daemon/constants.py`

| Constant | Value | Note |
|---|---|---|
| `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` | **PROVISIONAL `60_000` (pre-measurement placeholder)** — Phase 0 fixes the value per the §3.4 rule (`≥ 4 × measured max legitimate hold`, floor 30 s). Ships **DISABLED** (constant defined, NOT applied) if unmeasurable. | **Conditional on the measured rule (C-4).** Ships **DISABLED** if unmeasurable at stage time; enablement is gated on the measurement. |
| `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S` | **measured** (P100 of the churn-leg census) | Not a guess. See §3.4. |
| `MANAGER_ENGINE_POOL_RECYCLE_S` | `3_600` | — |
| `MANAGER_ENGINE_POOL_TIMEOUT_S` | `30` | — |
| `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S` | **PROVISIONAL default `2.0` (post-F8 target)** — **NOT an unconditional value.** Phase 0 fixes it per the derivation rule: `budget = measured P100 legitimate hold + stated margin` (margin ≥ 2× the measurement noise floor, floor 2.0 s). The literal `2.0` is a pre-measurement placeholder. **Danger:** M-R3's legitimate dwell is ≈ 13–15 s and PG resets the idle timer per statement — a 2.0 s budget can redden a HEALTHY daemon (false red) unless F8 removes every hold above it, which nothing guarantees. The margin/floor rule is stated here so the developer cannot pick a number by analogy. | The F7 residence budget (W-1). |

### Repair engine — `daemon/tools/ens_db_tools.py:603-633`

`pool_size=2`, `max_overflow=3`, `pool_pre_ping=True`, `pool_recycle=3600`, `pool_timeout=10` — **all unchanged**. The **only** knob added is `pool_reset_on_return="rollback"`. Options string: the existing literals **plus** `-c application_name=ensemble-repair` appended to its **own** literal, **after** the shared constant is interpolated — a distinct value, so PG's last-`-c`-wins rule produces no contest.

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
| sync twin | `manager.py:10860-10861` | `:11216-11224` (the `.result(timeout=8.0)` bridge) + the nested session at `:11110-11112` | both moved out (F8.4) |
| instance tool | `daemon/tools/instance.py:1207` | `:1360` (`await _bus.watch(...)`) | moved out, or excluded by leader ruling + disclosed (F8.5) |

**Deadlock check (done):** the sync twin runs on a **non-loop** `threading.Thread` (`manager.py:11180-11184`); the `run_coroutine_threadsafe` bridge still needs the manager's event loop, and the calling thread is not the loop thread ⇒ **no self-block, no deadlock**. The async twin runs on the loop; the fetch is moved to a short-lived session, so no loop-blocking DB call is introduced.

### 3.3 What the F8 change must NOT do

- **Must not** "fix" the hold by lengthening the session (that increases dwell).
- **Must not** move the work to an unbounded thread pool — the `.result(timeout=8.0)` bounded shape is the thing to preserve; the *transaction hold* is what goes.
- **Must not** touch `daemon/write_pause_guard.py` (F8.9).
- **Must not** weaken the `_write_guard` or the terminal/dead-parent guards to make the split easy.

### 3.4 The R15 bound rule (C-4) — ordering + validation

- **Ordering (hard task-level predecessor):** F8 lands **before** the engine-level `idle_in_transaction_session_timeout`.
- **Instrument:** one reconcile with a slowed content fetch, sampling `manager.engine.pool.checkedout()` at **≤ 100 ms** and `pg_stat_activity` `state_change` / `xact_start` residence of the reconcile's backend.
- **Rule:** `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS ≥ 4 × measured max legitimate hold`, floor **30 s**. The measurement and its command are recorded in the gate report.
- **If unmeasurable at stage time:** the bound ships **DISABLED** (constant defined, not applied); enablement is gated on the measurement.
- **The async twin's hold is currently UNBOUNDED** (`manager.py:11265-11271` has no `wait_for`) ⇒ **bound it or explicitly exclude it** from the budget.
- **Victim class (W-2):** the bound counts **continuous** idle (PG resets the timer per statement). M-R3's legitimate dwell (≈ 13–15 s) is **not** killable by a 60 s bound; the real collateral victim class is **unmeasured** ⇒ size the bound from the **measured P100**, not by analogy to the repair engine.

---

## §4 F7 — INVARIANT, ATTRIBUTION METHOD, COLLECTED-COUNT ASSERTIONS

### 4.1 The invariant (A2, corrected by C-10)

F7 must assert **both**, co-equal:

- **(a) Occupancy returns to baseline** — after a churn cycle, `manager.engine.pool.checkedout()` returns to its baseline. In-memory, no DB round trip (the sampler shape already exists at `daemon/services/readiness.py:525-549`). **This is the invariant that would have caught the incident** — the census alone would not (C-10).
- **(b) No backend's idle-in-txn residence exceeds the budget** — `now() - state_change <= DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`. **Not** a count assertion. The budget value is **PROVISIONAL** (default 2.0 s) — Phase 0 fixes it per the derivation rule `budget = measured P100 legitimate hold + stated margin` (margin ≥ 2× the measurement noise floor, floor 2.0 s). A 2.0 s budget can redden a HEALTHY daemon (false red) unless F8 removes every hold above it — the margin rule prevents picking a number by analogy.

**The old "count == 0 at every sample" invariant is withdrawn.** It is red for a healthy busy daemon and green for a slow leak. A count may be **reported** and **bounded** (`peak_occupancy ≤ λ_measured × budget`, with λ from the leg's own sampler) but never asserted to be zero.

**Why (a) is load-bearing:** a connection checked out **before its first statement** is PG state `idle`, **not** `idle in transaction`. The incident's shape was 15/15 slots held with only 6 in `idle in transaction` and 9 idle-but-checked-out ⇒ a census-only guard would be **GREEN on the incident**.

### 4.2 Attribution method (A3, A5, C-2, C-8)

1. **Scratch-database isolation** (D-23/D-24) — the guard creates its own uniquely-named database, points the daemon's engine factory at it, and asserts on `pg_stat_activity WHERE datname = <that db>`. Every backend in that database belongs to the test. **This is the guard's attribution and it stays.**
2. **`application_name` where present** — the census is attributed *"by `application_name` where present, else by scratch-DB isolation"*. The old wording ("by `application_name` (post-F2)") is **wrong** if D-24 stands ⇒ restated in `test-strategy.md` §1.5 (added in the fold amendment). |
3. **Three engines carry a name** — `ensemble-manager` (F2.3), `ensemble-repair` (F2.5), `ensemble-checkpoint-adapter` (F2.6). The **shared** `PG_SESSION_CONNECT_ARGS` must **never** carry `application_name` (D-17).
4. **Every census captures** `count` **+** `state_change` **+** `xact_start` **+** `backend_start` **+** `application_name`, and reports **residence-time buckets**, not just a count. The `backend_start > run_start` BASE filter is **prescribed** in `test-strategy.md` §1.5 item 3 and must be **implemented** in the helper's SQL.
5. **PG state `idle` is a disclosure metric**, not an assertion (C-10).

### 4.3 Collected-count assertions (C3 — every gate)

- Every gate command is written down with its **expected collected count**, and the count is **asserted > 0** **before** "0 failed" is read as a pass.
- The **whole-tree** legs (G1.5, G3.5) must assert `collected > 0` on **both** sides and reconcile `base_collected` / `head_collected` against the sum of the declared `EXPECTED_COLLECTED_*`; a **mismatch is FATAL**. "The base run's collected count is *recorded*" is **not** an assertion.
- A PG gate **fails** on `skipped > 0`, not only on failures.
- The census gate's non-vacuity cell is restated as *"each family's count MUST **equal** its §7.2 baseline constant; any delta is a FAILURE until classified per §7.3"*, and the **diff** is the failing step (the scanner always exits 0 — OQ-7(c)).
- **ONE** canonical gate ladder and **ONE** pack inventory, with a static consistency pin asserting every gate ID referenced in `decisions.md` / `architecture-recommendation.md` exists in the canonical ladder (W-6, W-11).

### 4.4 The churn leg (A2-iv, A10, C-12, W-8)

The churn leg **must drive the deferred-report reconcile** with a deliberately slow content fetch. The current library-level leg never touches `manager.py` and therefore **cannot discriminate F8**.

**Technique (sanctioned by this spec):** a second session holds `LOCK TABLE checkpoints IN ACCESS EXCLUSIVE MODE` on the **P2 saver's** tables, and the reconcile's content fetch is instrumented to block **≥ 3 s**; `pg_stat_activity` is sampled every **250 ms**. D2's `LOCK TABLE task` technique is reused **only** for the F4 leg — it targets the **queue probe's** `FROM task` read, whereas the reconcile awaits a **checkpoint read on the P2 saver pool** (`child_reports.py:1654` → `get_instance_messages(self._checkpointer, …)` → `persistence.py:316-331`, `autocommit=True`) — **different table, different pool**.

**Expectations:** BASE = ≥ 1 manager-pool idle-in-txn backend **and** `pool.checkedout()` above baseline; HEAD = none. If BASE cannot demonstrate the F8 shape, the guard is a **regression** guard, not a **reproduction** guard — **say so** in the test strategy (A10) rather than forcing a BASE demo the mechanism does not support.

---

## §5 PIN INVENTORY (each pin: the mutation that makes it RED)

Markers: `none` = default-collected · `postgres` = `tests/postgres/…_pg.py` (auto-marked) · `integration` = `pytestmark = pytest.mark.integration`.

| Pin | Where | Marker | Mutation that makes it RED | Default-collected? | Count asserted? |
|---|---|---|---|---|---|
| **F3-a** | `tests/unit/tools/test_dbpool_txn_hygiene.py` | none | Re-introduce `session.execute(text("BEGIN"))` at the DDL or DML site | ✅ | ✅ |
| **F3-b** | same | none | Delete the rollback from the outer-except path | ✅ | ✅ |
| **F3-c** | same | none | Make the rollback itself raise on the DDL path (the rollback-error leg) | ✅ | ✅ |
| **F3-d** | same | none | Re-introduce the audit row **inside** the rolled-back transaction (violates the separate-session contract at `:1530-1538`) | ✅ | ✅ |
| **F3-e** | same | none | **Rewrite as one concrete edit** (A-8): change the preview string at `:1482` → the wording assertion REDs | ✅ | ✅ |
| **F4-a** | `tests/unit/tools/test_dbpool_probe_bound.py` | none | Remove the `SET LOCAL statement_timeout` from `make_db_probe`; or change the derived value so it is no longer `2 × DB_PROBE_TIMEOUT_S`; or add `SET LOCAL` to the **saver** probe (the exclusion assertion REDs) | ✅ | ✅ |
| **F4-b** | `tests/integration/…` | integration | **Remove the `SET LOCAL statement_timeout` from `make_queue_probe`** — the statement stays blocked, the connection stays checked out, `engine.pool.checkedout()` does not return to baseline ⇒ RED. *(The old mutation — removing an engine-level knob F4 does not install — was a no-op; and the old assertion "0 idle-in-txn backends" was TRUE AT BASE because `idle in transaction` and `wait_event_type='Lock'` are mutually exclusive. See C-7.)* | ❌ PG | ✅ + zero-skips |
| **F4-c** | readiness unit pack | none | Change the probe's returned `(verdict, failure_class)` pair, or reclassify a timeout as hard-component-red | ✅ | ✅ |
| **F4-d** | `test_readyz_grace_detail.py` (existing) | none | Remove a detail field | ✅ | ✅ |
| **F4-e** | `tests/unit/tools/test_dbpool_probe_bound.py` | none | Restore the false docstring premise at `readiness.py:107-113` (source-level) | ✅ | ✅ |
| **F5-b** | `tests/postgres/` or `tests/integration/` | pg/int | Re-introduce `FROM tasks` | ❌ PG | ✅ + zero-skips |
| **F5-c** | `tests/unit/…` (static twin) | none | Re-introduce `FROM tasks` / `tasks t` in the module | ✅ | ✅ |
| **F6-a** | `tests/unit/…` | none | Replace the upsert with the bare ORM insert; or change the re-resolve key from the anchor to `founding_message_id` | ✅ | ✅ |
| **F6-b** | `tests/postgres/` | postgres | Remove the idempotency (revert to a bare INSERT) | ❌ PG | ✅ + zero-skips |
| **F6-c** | `tests/postgres/` (new, W-4) | postgres | Drop the partial unique index from the target DB ⇒ the upsert raises **42P10** ⇒ the negative leg REDs | ❌ PG | ✅ |
| **F6-d** | gate (process) | n/a | Introduce a new `.sql` file **or** a new ORM TABLE/COLUMN/INDEX (A-6) | n/a | n/a |
| **F6-e** | `tests/unit/…` | none | **Rewrite F6-c's old broad mutation as one concrete edit** (A-8) | ✅ | ✅ |
| **F2-a** | `tests/unit/repositories/test_pg_engine_utc_session.py` | none | Remove the explicit `pool_reset_on_return` kwarg | ✅ | ✅ |
| **F2-b** | same | none | Drop `pool_recycle` | ✅ | ✅ |
| **F2-c** | same | none | Drop `idle_in_transaction_session_timeout` from the manager options | ✅ | ✅ |
| **F2-d** | same (existing) | none | Remove `pool_pre_ping` | ✅ | ✅ |
| **F2-e** | same (existing `:86-88`, **amended** per C-9) | none | Replace the manager options string (order-sensitive) | ✅ | ✅ |
| **F2-f** | `tests/unit/tools/` (new) | none | Extend the **shared** `PG_SESSION_CONNECT_ARGS` with an `application_name` or a timeout ⇒ the repair engine's rendered string changes ⇒ RED (this is the D-17 guard) | ✅ | ✅ |
| **F2-g** | same | none | Change either engine's `application_name` value; or make the two **equal**; or make either appear **more than once** | ✅ | ✅ |
| **F2-h** | same | none | Add `application_name` to the **shared** `conn_string` at `persistence.py:225` ⇒ the P2 oracle's rendered conninfo changes ⇒ RED (P2-immutability + separate-DSN guard) | ✅ | ✅ |
| **F2-i** | `tests/unit/…` (new) | none | Restore the false honesty-block wording ("the P1 idle-in-transaction leak is REPAIRED") | ✅ | ✅ |
| **F2-j** | `tests/unit/…` (new) | none | Change **any** knob of the P2 saver pool construction (`persistence.py:316-331`) | ✅ | ✅ |
| **F7-a** | `tests/integration/test_db_pool_idle_in_transaction_guard.py` | integration | Hold a manager-pool connection across a >budget dwell ⇒ the residence assertion REDs (re-specify as a **deliberate >budget hold** — W-1) | ❌ PG | ✅ |
| **F7-b** | same | integration | Re-introduce a retention path (e.g. return a connection with an open transaction) | ❌ PG | ✅ |
| **F7-c** | `tests/unit/…` (self-pin) | none | Delete the guard's assertion **and** decrement the count constant ⇒ the self-pin re-reads the source ⇒ still **RED** (A-9) | ✅ | ✅ |
| **F7-d** | `tests/unit/…` (static twin) | none | Delete / un-mark / move the guard file out of the `integration` placement | ✅ | ✅ |
| **F7-e** | `tests/integration/…` (new) | integration | Re-introduce the occupancy defect: hold 2 manager slots across the churn cycle ⇒ `pool.checkedout()` does not return to baseline ⇒ RED (**this is the pin that would have caught the incident** — C-10) | ❌ PG | ✅ |
| **F8-a** | `tests/unit/…` (static, AST) | none | Insert an `ast.Await` **or** a `run_coroutine_threadsafe(...).result(...)` **inside the `with`-body** of a manager-engine session scope in `daemon/` ⇒ RED. **Scope the rule to the `with`-body only** (a function-wide rule false-positives on the legitimate bridge calls at `:11217-11224` / `:11568-11571`) and **name the three expected sites** in the docstring so a fourth is a deliberate failure. | ✅ | ✅ |
| **F8-b** | `tests/integration/…` | integration | Drive the reconcile with a slowed content fetch; assert **no** manager-pool idle-in-txn backend attributable to the reconcile and `pool.checkedout()` back to baseline | ❌ PG | ✅ |
| **F8-c** | `tests/integration/…` (new, C-5) | integration | Inject a concurrent escalation at the **P0→P2** seam ⇒ the re-asserted terminal guard must produce **no** artefacts | ❌ PG | ✅ |
| **F8-d** | `tests/integration/…` (new, C-5) | integration | Break the write scope's single-transaction commit (e.g. split the message/task/injection writes) ⇒ the atomicity assertion REDs | ❌ PG | ✅ |
| **F8-e** | `tests/integration/…` | integration | Re-introduce the unbounded async-twin hold (remove its `wait_for`/bound) | ❌ PG | ✅ |
| **F1-a** | `tests/unit/…` (static) | none | Delete `audit-findings.md` or restore the retired "~689" figure instead of the AST baseline | ✅ | ✅ |

**Inventory rules:** every pin has a **concrete, plantable** mutation (no "any change that alters X"); every PG/integration pin has a **default-collected static twin** (W-13); every PG pin asserts **zero skips**; every pin asserts its **collected count**.

---

## §6 PHASE / GATE ORDER (ONE canonical ladder)

The plan currently carries **two** ladders (`phase3-plan.md` G0–G6 and `test-strategy.md` G1.1–G3.5) and **two** guard filenames. **Reconcile to one** — use the IDs the architect's amendments reference (**G1.1…G3.5**) — and ship an explicit mapping table for the phase-3 IDs (W-6).

### 6.1 Phase order (unchanged by the fold, plus F8)

| Phase | Contents | Gate |
|---|---|---|
| **P1** | **F8** (F8.1–F8.9) · F3 (F3.1–F3.5) · F4 (F4.1–F4.7) · F6 (F6.1–F6.6) · F1 (F1.1) | Transaction-hygiene + mechanism pins green; **no** change to the readiness detail schema, leak predicate, grace refusal path or never-green semantics |
| **P2** | F2 (F2.1–F2.10) · F5 (F5.1–F5.3) | Knob matrix pinned; **the engine-level idle-in-txn bound lands only after F8** |
| **P3** | F7 (§4) · the pin packs that freeze F3/F4/F6/F2/F5/F8 | The guard runs in a form the runner **executes** with an asserted collected count; net-new test reds = 0 |

**Sequencing constraints (hard):**
1. **F8 before the engine-level `idle_in_transaction_session_timeout`** (C-4, A6-i).
2. **F2's `application_name` change and the AC amendments land in ONE commit** (C-2) — AC14/AC15/AC16/T2.4/T2.6/D-19/D-24/G2.4/F2-f/F2-g all change together, or the gate is RED from birth.
3. **The `statement_timeout` DROP lands with the F2 change** (C-3) — D-16, T2.2, AC14 **and** the Phase-2 objective text.
4. **The asyncpg DSN (F2.6) lands with the F2 change** (C-8) — it must not touch the shared `conn_string`.

### 6.2 Gate form (binding — C-1, W-10)

**Interpreter: `/home/nea/ensemble-src/.venv/bin/python`** (verified: pytest 9.0.2, py 3.13.15; `import daemon` resolves to the **worktree** when cwd = worktree). **Drop `uv run`** — `uv` is **off-PATH** and the worktree has **no `.venv`** ⇒ `uv run` triggers a network-dependent sync at gate time. **Gates MUST NOT run from the shared checkout.**

**Every gate command:**

```
cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
HOME="$(mktemp -d)" /home/nea/ensemble-src/.venv/bin/python -m pytest <paths> --collect-only -q
```

- **Per-variable** scrub only. `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) — an ambient `POSTGRES_DB=ensemble_prod` survives it.
- `HOME="$(mktemp -d)"` — **never** a HOME-derived live install path (two live-install deletions, 2026-10-12).
- **First step of every gate: print the interpreter certification** — `python -c "import daemon,pytest; print(daemon.__file__, pytest.__version__)"` — and attach it to the gate report.
- The `--collect-only` count is captured **before** the run; `collected == 0` ⇒ **FATAL**, never "0 failed ⇒ pass".

**PG gate form** (adds the marker override):

```
… /home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m postgres|integration <paths> -q
```

(`pyproject.toml:95` deselects `integration`/`postgres`/`slow`/`conformance` by default.)

### 6.3 Legacy → canonical gate-ID mapping (authority for FOLD-MAP.md cross-check)

**Canonical source of truth (this spec, §6.1/§6.2):** the gate ladder is **G1.1 … G3.5** (W-6). The legacy `phase3-plan.md` IDs **G0 … G6** are the duplicate ladder being retired. The legacy IDs' **meanings were recovered empirically** from the pre-fold plan at commit `23c232b8d` (`git show 23c232b8d:.agents/shared/planning/db-pool-idle-txn/phase3-plan.md` §2.1) — G0 = the F7 guard's own reachability (unit static pin, G-A); G1 = Phase-1 packs (F3/F4/F6 unit legs); G2 = Phase-2 packs (knob matrix, repair-engine, P2 immutability); G3 = Phase-1 PG legs (F4 retention PG, F6 idempotency PG); G4 = Phase-2 PG legs (wedge-capture correctness); G5 = the F7 guard; G6 = whole-tree sweep. A single mapping table now governs BOTH artifacts (this table + the one in FOLD-MAP.md CF-1, which must agree). The sibling plan worker owns `phase1/2/3-plan.md` and may publish its own mapping table in FOLD-MAP.md; **this spec publishes the canonical-ID side as the authority** so the plan set can be cross-checked against it (do not edit the sibling's files).

| Legacy ID (phase3-plan.md) | Canonical ID(s) (this spec) | Contents |
|---|---|---|
| **G0** | **G3.4** (the guard's static reachability leg) | guard's own reachability (unit static pin, G-A) — G0 was a **runnable unit gate**, not a pre-gate; the earlier "(pre-gate)" reading was a content error |
| **G1** | **G1.1 – G1.4** | Phase-1 unit packs (F3, F5, F6 transaction-hygiene + mechanism pins) |
| **G2** | **G2.1 – G2.3** | Phase-2 unit packs (F2 knob matrix, repair-engine, P2 immutability) |
| **G3** | **G2.1 – G2.3** (F4 retention PG leg) | Phase-1 PG legs (F4 retention PG, F6 idempotency PG) — F4 retention is Phase-2 content |
| **G4** | **G2.1 – G2.3** (F5 wedge-capture PG subset) | Phase-2 PG legs (wedge-capture correctness) |
| **G5** | **G3.1 – G3.4** | F7 guard (occupancy + residence) |
| **G6** | **G1.5 / G3.5** | two whole-tree sweep legs |

**Rules:**
- Any gate ID referenced in `decisions.md` or `architecture-recommendation.md` must exist in the canonical set {G1.1…G3.5} (W-11 static consistency pin).
- The `F7` self-pin (F7-c) and the F8 static pin (F8-a) are default-collected unit packs and are NOT separate ladder entries — they ride G1.x / G3.x respectively.
- If the sibling's FOLD-MAP.md uses different legacy→canonical IDs, the canonical side (this table) wins; the sibling reconciles against it, not the reverse.
- The legacy mapping table is an explicitly-marked exempt block (legacy→canonical audit table, not a gate reference) so the W-11 pin does not self-RED on the plan set (CF-2).

## §7 DO-NOT-TOUCH

**Absolutely untouched (any diff is a gate failure):**

- `daemon/services/readiness.py` — `:312-325` (detail fields), `:381`/`:408-446` (detail serialization), `:500-522` (`resolve_psycopg_pool`), `:525-549` (`sample_sqlalchemy_pool_state` — P1 **ADVISORY, MUST NOT arm**), `:552-567` (`sample_psycopg_pool_state` — P2 **ORACLE**), `:656-671` (tracker init), `:690-781` (`observe()` — conjunction `:739-743`, arm-on-first-eligible, re-arm-after-full-lapse), `:784-941` (`_evaluate_leak` — waiting-streak arm, exhaustion arm, subsumed trend arm, S9 streak arm), `:961-983` (`_checkedout_rising` — published, never arming), `:1263-1296` (`_guarded`), `:1279-1281`.
  **Permitted in this lane:** the two probe **bodies** (`make_db_probe` `:1463-1481`, `make_queue_probe` `:1716-1790`), the **derived** timeout constant (the existing `DB_PROBE_TIMEOUT_S` / `QUEUE_PROBE_TIMEOUT_S` **values do not change**), and the **docstring at `:107-113`** (A4-v — this must be added to the permitted set).
- `daemon/constants.py` — `READINESS_GRACE_HORIZON_S=120.0` (`:165`), `READINESS_LEAK_TICK_THRESHOLD=3` (`:175`), `CHECKPOINT_POOL_MAX_SIZE_DEFAULT=10` (`:197`), `CHECKPOINT_POOL_TIMEOUT_S_DEFAULT=5.0` (`:205`).
- `daemon/api.py` — `:3939-3987` (probe wiring, `manager.engine.pool` access shape, `resolve_psycopg_pool`), `:5049-5087` (the no-composite `boot_incomplete` branch).
- `daemon/persistence.py:316-331` (P2 saver pool construction) — **knobs byte-identical**; **`:306-315` honesty comment changes per D-21/R12 only** (state both granularities in the gate, W-7).
- `daemon/persistence.py:225` — the **shared** `conn_string` (the asyncpg pool gets a **separate** DSN, F2.6).
- `scripts/upgrade/lib.sh` — the soak semantics (3 consecutive greens; **never-green unconditional at cap-reach**).
- `daemon/repositories/factory.py:184-199` (SQLite), `:210-214` (generic engine path), `:215-222` (latent config-driven PG).
- `daemon/tools/ens_db_tools.py:603-633` — every knob **except** the added `pool_reset_on_return="rollback"` and the appended `application_name`; the options string's other literals stay byte-identical.
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
- The stage log records the **measured** idle-in-txn bound and its command (C-4), or records that the bound shipped **DISABLED** pending the measurement.

### 8.5 Honest statement of what this lane does and does not close

**Closes:** the transaction-abort gap (F3) · the probe's server-side statement bound (F4) · the non-idempotent boot mint (F6) · the wedge-capture table bug (F5) · the manager-engine knob gap (F2) · **the dominant multi-second producer M-R3** (F8) · the attribution blind spot (three named engines).

**Does NOT close — and must be stated in the shipped honesty blocks (F2.7):** the **dwell / occupancy** class. The recurrence precondition — **sync DB work on the event loop** (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) — is **not removed** by this lane, and the **BFS depth-loop** transaction span (`daemon/repositories/instance/repository.py:1434-1645`) is **not** touched. After this lane the daemon can still hold 15/15 manager slots; the guard's occupancy invariant (F7-e) is the thing that will see it, and the follow-up is the **owner** of the residual.

---

*BINDING. Produced by the reviewer (dispatcher/controller) from the governor council (`24a5d779-09dd-465b-b9d2-899b88f3f56b`, 2 councilors) plus two code-review workers (`856436de-2f09-4b21-ab9a-5dc6e361ecae`, `183cd3fe-977f-41f3-b87b-170138f9dc2e`). Every `file:line` is at base `19fc11c8b` and was re-verified by the reviewer. Read-only review: no daemon/, tests/, scripts/ or payload-tree file was modified.*
