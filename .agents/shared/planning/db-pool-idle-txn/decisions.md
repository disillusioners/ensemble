# db-pool-idle-txn — Decisions

- **Lane:** `fix/db-pool-idle-txn` (job `634c38f2`) · **Base:** `19fc11c8b52239813fd91327ee0c1961c5b6fc87` (= `origin/latest` = the staged v0.27.11 tree).
- **All `file:line` citations are at this commit.** The RCA's line numbers are stale (coupling **C4**) and are never quoted from the incident snapshot.
- **This file is the single canonical home for the decision records.** The phase files' "Decisions" tables are **pointers** to the IDs below — no ID is minted twice, and no ID here is renumbered.
- **Append-only discipline (council fold):** D-01..D-28 are **frozen** (they are cited elsewhere). D-16 is marked **REVERSED** (not deleted). New records are appended as D-29+.
- **Sibling artifacts referenced by filename only** (authored in parallel, not read): `test-strategy.md`, `wi-78967dba-verdict.md`, `audit-method.md`, `final-implementation-spec.md`.
- **Binding couplings:** C1 (the leak detector and its refusal path are fail-closed by design), C2 (no conditional weakening near rollback; never-green stays unconditional at cap-reach), C3 (false-green discipline: gates run in a form the runner executes + assert the expected collected count), C4 (stale line numbers).
- **Safety-precedence rule applied throughout:** if a requirement conflicts with a safety property, preserve the safety property, narrow the feature, and say so.

---

## 1. Index (D-01..D-28 — frozen)

| ID | Area | Decision (one line) |
|----|------|--------------------|
| **D-01** | F3 | **DROP** the explicit `BEGIN` (rather than move it inside the inner `try`) |
| **D-02** | F3 | The rollback moves into a **`finally`** so every exit path rolls back |
| **D-03** | F3 | The agent-visible preview strings / docstrings are updated **in the same change** (audit-contract wording left alone) |
| **D-04** | F4 | Probe-scoped **`SET LOCAL statement_timeout`** (house idiom) is the primary vehicle; dedicated probe pool **rejected on C1** |
| **D-05** | F4 | The server bound is **2× the client budget**, so the client classifies the failure first |
| **D-06** | F4 | **PG-only** guard; the SQLite path is untouched |
| **D-07** | F4 | An **explicit end-of-transaction** precedes the probe's connection return |
| **D-08** | F6 | **Idempotent mint** via a dialect upsert (`ON CONFLICT … DO NOTHING`) + anchor-scoped re-resolve; **no schema change** |
| **D-09** | F6 | The boot path's re-resolve is keyed on the **index's own predicate** (anchor + `status='open'`), not on `founding_message_id` |
| **D-10** | F6 | F6 is **scoped to the own-session (boot) path**; the caller-session path is untouched |
| **D-11** | F1 | The audit record is a **new artifact in this planning dir**, not an edit to the read-only RCA |
| **D-12** | phasing | **Phase 1 lands no pool knob** — the knob table is one table with one pin |
| **D-13** | F2 | F2 scope = the **manager engine** + the repair engine's explicit reset pin; every other engine is untouched |
| **D-14** | F2 | Exact manager-engine knob values (5/10/pre_ping/reset=rollback/recycle=3600/timeout=30; no LIFO) |
| **D-15** | F2 | `pool_timeout` **review outcome: keep 30s**, pinned explicitly; residual disclosed |
| **D-16** | F2 | Engine-level server-side timeouts: `statement_timeout=30000`, `idle_in_transaction_session_timeout=60000` — **REVERSED by C-3 (leader ruling 2): the engine-wide `statement_timeout` is DROPPED; `idle_in_transaction_session_timeout` is retained; the future global cap must `SET LOCAL statement_timeout = 0` inside `manager.py:8760`** |
| **D-17** | F2 | A **NEW** connect-args constant; `PG_SESSION_CONNECT_ARGS` is **not** extended (it would silently retune the repair engine) |
| **D-18** | F2 | Knob **values** live in `daemon/constants.py`; the options **assembly** stays in `factory.py` |
| **D-19** | F2 | The repair engine changes by **exactly one** knob (`pool_reset_on_return`); its options string stays byte-identical |
| **D-20** | F5 | F5 is a **correctness** fix (no pool involved), scoped to the **table name** only |
| **D-21** | F2/F5 | The **honesty blocks** are refreshed (they currently name this lane as the owner of an unfixed defect) |
| **D-22** | F2 | The latent config-driven PG branch is **not** hardened |
| **D-23** | F7 | The guard rides the **integration** placement (scratch DB, `-m integration`) |
| **D-24** | F7 | Attribution is by **scratch-database isolation**, not `application_name` |
| **D-25** | F7 | A **default-collected static leg** (G-A) guards the guard's own reachability |
| **D-26** | C3 | Every gate asserts its **expected collected count** before "0 failed" is read as a pass |
| **D-27** | C3 | Every PG gate uses the **per-variable** env scrub + fenced HOME; `env -u POSTGRES_*` is forbidden |
| **D-28** | phasing | **Phasing rationale** (why F3/F4/F6/F1 first, then F2/F5, then F7) |

---

## 2. New decision records (D-29+) — council fold

### D-29 — C-1: Certified test interpreter is binding

**Decision:** Every gate command in this lane uses `/home/nea/ensemble-src/.venv/bin/python` (pytest 9.0.2, py 3.13.15; `import daemon` resolves to the worktree when cwd = worktree). `uv run` is dropped (uv is off-PATH; worktree has no .venv). `/usr/bin/python3` is dropped (no pytest). Every gate asserts its EXPECTED COLLECTED COUNT (a zero-collected gate is FATAL, never green).

**Why:** `/usr/bin/python3` has no pytest ⇒ the §2.3 fence reads COLLECTED=0 → FATAL ⇒ Class-4a/4b run ZERO tests and read "0 failed" as GREEN. `uv run` triggers a network-dependent sync at gate time (the same failure mode that killed `bump_version.py`'s uv-sync step in two prior lanes).

**Evidence:** council-report.md C-1; verified: `ModuleNotFoundError: No module named 'pytest'` on `/usr/bin/python3`; verified: `/home/nea/ensemble-src/.venv/bin/python` has pytest 9.0.2 and resolves `daemon` to the worktree.

**Displaces:** the dispatch's own house note (`uv run python -m pytest`); every gate command in phase1/2/3 that used `uv run`.

### D-30 — C-2: ONE statement of truth for application_name

**Decision:** The shared connect-args constant (`PG_SESSION_CONNECT_ARGS`) carries NO `application_name`. Each pool's attribution is set at that pool's own connect site: manager engine → `-c application_name=ensemble-manager`; repair engine → `-c application_name=ensemble-repair` (appended to its own literal); asyncpg checkpoint-adapter pool → `application_name=ensemble-checkpoint-adapter` on a SEPARATE DSN (not the shared `conn_string`). AC14/AC15/AC16/T2.4/T2.6/D-19/D-24/G2.4/F2-f/F2-g all change together in ONE commit. D-23(c)/D-24 is superseded/reconciled: scratch-DB isolation stays as the guard's attribution; `application_name` is added "where present, else by scratch-DB isolation."

**Why:** PG's last `-c` wins. Placing `application_name` in the shared constant would make the repair engine report the manager's name, destroying attribution. F2-g is RED from birth as written (AC14 + AC16 make F2-g unsatisfiable).

**Evidence:** council-report.md C-2; `factory.py:252` (shared constant); `ens_db_tools.py:619-627` (repair engine embeds it); `persistence.py:225` (shared conn_string) vs `:317` (P2 oracle).

**Displaces:** A5's two-engine model (extended to three); D-24's "attribution by scratch-DB isolation, not application_name" (reconciled, not replaced).

### D-31 — C-3: engine-wide statement_timeout is DROPPED (REVERSES D-16's first clause)

**Decision:** The engine-wide `-c statement_timeout=30000` on the manager engine is **DROPPED**. D-16 is marked REVERSED. The manager engine's connect-args become: `-c timezone=UTC -c application_name=ensemble-manager -c idle_in_transaction_session_timeout=60000`. The future global cap must `SET LOCAL statement_timeout = 0` inside `manager.py:8760` (recorded as a follow-up, not a change).

**Why:** `manager.py:8760-8762` runs the whole `_ensure_postgres_columns` list in ONE transaction; the code documents the JSON→JSONB step as "FULL TABLE REWRITE with ACCESS EXCLUSIVE lock… may take seconds" (`:7877-7879`); the method is fail-loud ("startup aborts", `:7554-7560`) ⇒ a 30 s cap firing there = the daemon does not boot, on a path that is a no-op at steady state (invisible in normal measurement). No other statement class has a plausible >30 s duration (the famous ~206 MB walk rode the P2 saver pool and was deleted; RAG is external HTTP; the migrations runner is a PG no-op; vacuum is a separate NullPool).

**Evidence:** council-report.md C-3; `manager.py:8760-8762/:7877-7879/:7554-7560`; OQ-3 ruling in architecture-recommendation.md.

**Displaces:** D-16's `statement_timeout=30000` clause; `phase2-plan.md` objective/T2.2/AC14; `test-strategy.md` mentions.

### D-32 — C-4: measured rule for the idle-in-txn bound (replaces D-16's unmeasured 60 s)

**Decision:** The idle-in-txn bound is not an unmeasured number. Phase 0 produces the measured max legitimate hold + margin. The rule: `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS ≥ 4 × measured max legitimate hold`, floor 30 s. The measurement and its command are recorded in the gate report. If unmeasurable at stage time, the bound ships DISABLED (constant defined, not applied); enablement gated on the measurement. The async twin's hold is currently UNBOUNDED (`manager.py:11265-11271` has no `wait_for`) ⇒ bound it or explicitly exclude it from the budget. The victim class is sized from the measured P100, not by analogy to the repair engine (W-2: PG resets the idle timer per statement ⇒ the bound counts continuous idle; M-R3's legitimate dwell ~13-15 s is NOT killable by a 60 s bound).

**Why:** "8 s" is the literal `.result(timeout=8.0)` at `manager.py:11217-11224` — a configured timeout, which the architect's own A4-iii reclassifies as a checkout wait, not an observed hold; mixed with A6's post-F8 "≤ 2 s" target ⇒ 60 s is 30×, not 7×. No instrument, sample rate, or threshold source exists anywhere.

**Evidence:** council-report.md C-4; `manager.py:11217-11224/:11265-11271`; architecture-recommendation.md A6.

**Displaces:** D-16's unmeasured 60 s bound; the "7× margin" argument (now holds only after F8).

### D-33 — C-5: F8 specified as a three-phase scope split

**Decision:** F8 is NOT "hoist the content fetch before opening the scope." It is a named three-phase shape:

- **P0** read-decide scope (manager engine session OPEN): read `inj`, `parent_row`, child `agent_id`, `live_carrier_exists`.
- **P1** content fetch (NO manager-engine transaction open): short-lived own session for the child row; the checkpoint read (P2 saver pool) happens with no manager slot held.
- **P2** write scope (manager engine session OPEN): RE-READ `inj` (detached/expired after P0); RE-ASSERT the terminal guard and the dead-parent guard; create the artefacts (message INSERT + task INSERT + injection UPDATE) in ONE transaction.

Both fetch helpers' signature changes from `session=` to `child_agent_id: str`.

**Why:** the main scope has already autobegun at `:11517` and `:11556`, so its backend is idle-in-txn across the await wherever the fetch's own session lives. Only splitting works. Splitting introduces three invariants (re-read, re-assert, write atomicity) that need pins.

**Evidence:** council-report.md C-5; `manager.py:11515-11571/:10860-10930/:11281-11440`; architecture-recommendation.md A1.

**Displaces:** A1's literal shape ("hoist the content fetch before opening the scope").

### D-34 — C-6: F8 extended to all three borrow-then-await sites

**Decision:** F8 covers three sites, not one:

1. `manager.py:11568-11571` (async content fetch) — moved out (F8.2).
2. `manager.py:11754-11757` (`await asyncio.to_thread(self._has_live_process_report_carrier)`, whose callee opens its own manager-engine session at `:10773`) — moved out (F8.3).
3. `daemon/tools/instance.py:1207` (`with WriteGuardSession(...)`) + `:1360` (`await _bus.watch(...)`) — moved out, OR excluded by leader ruling + disclosed (F8.5).

The static pin (F8-a) is scoped to the `with`-body only (a function-wide AST rule false-positives on the legitimate bridge calls at `:11217-11224`/`:11568-11571`) and names the three expected sites in its docstring so a fourth is a deliberate failure.

**Why:** implementing A1 as written leaves the `:11754` thread-hop inside the scope ⇒ pin (i) stays RED. The `instance.py:1360` site has no task attached and is RED from birth otherwise.

**Evidence:** council-report.md C-6; `manager.py:11516/:11568-11571/:11754-11757/:10773`; `instance.py:1207/:1360`.

**Displaces:** A1's single-site scope.

### D-35 — C-7: F4-b re-specified to the true observable (AMENDED 2026-10-28 round-2 — P-1/DR-29 + DR-1)

**Decision (amended):** D2's expected signal → `state='active'` + `wait_event_type='Lock'`. F4-b asserts (i) the probe's statement is **cancelled** (`57014` / `QueryCanceled`), (ii) the abandoned `to_thread` future **returns**, and (iii) `engine.pool.checkedout()` **returns to baseline** within the bound — and states explicitly that the backend **row is EXPECTED to persist** in `pg_stat_activity` (state `idle`, returned to the pool); its persistence is the *correct* observable, not a failure. The census count is asserted only after the lock is released and the client-side bound elapsed. The engine-level `idle_in_transaction_session_timeout` signature (BACKEND GONE, FATAL `IdleInTransactionSessionTimeout`, raw t2:137-140) is **NOT** F4's — F4 installs neither `statement_timeout` engine-wide nor `idle_in_transaction_session_timeout`; the row-gone observable belongs to the engine-level backstop, which ships DISABLED (D-47). F4-b's mutation → remove the `SET LOCAL statement_timeout` from `make_queue_probe` (that does redden: the statement stays blocked, the connection stays checked out).

**Why (amended):** a lock-blocked backend is `state='active'` with `wait_event_type='Lock'`; an `idle in transaction` backend waits on the CLIENT (`wait_event_type='Client'`). They are mutually exclusive ⇒ F4-b's "0 idle in transaction backends" is TRUE AT BASE ⇒ false green. The old mutation (removing an engine-level knob F4 does not install) is a no-op for F4. **DR-1 fix:** the binding decision record now matches the pin rows (test-strategy.md:281) instead of contradicting them. **DR-29 (P-1):** under pool starvation the probe holds NO connection (a pool WAITER), so the probe-scoped bound is irrelevant to the starvation shape; F4 is demoted to defence-in-depth and its outcome is restated as "the probe's statement duration is server-bounded **when the probe holds a connection**" (b2: `active`, not `idle in transaction`).

**Evidence:** council-report.md C-7; `readiness.py:1476-1481/:1746-1789`; A9 in architecture-recommendation.md; BASELINE.md T5b (b1/b2/b3); raw t5 (b1=5.8 ms, b2 timeout=0.5017 s, b3 timeout=0.5012 s).

**Displaces:** F4-b's old assertion and mutation; the superseded "row gone from `pg_stat_activity`" clause (originated in my own C-7 required-change wording — owned and repaired here).

### D-36 — C-8: three-pool attribution model (corrects the council's capability table)

**Decision:** A5 names a THIRD `application_name` (`ensemble-checkpoint-adapter`) carried on a SEPARATE DSN for the asyncpg pool only. DO NOT touch the shared `conn_string` (`persistence.py:225`) — it also feeds the P2 psycopg oracle (`:317`), and touching it would break the P2-immutability pin (T2.7/AC18) and put the C1-protected oracle into the lane's diff. Amend AC18 and T2.7 in the same change; add the asyncpg pool to D-13's engine list. Drop A5's saver-pool clause so no clause is left without a landing task.

**Why:** the asyncpg checkpoint-adapter pool opens an explicit transaction (`checkpoint_adapter.py:1231`: `async with conn.transaction():` with three awaits between DELETEs) ⇒ its backend sits `idle in transaction` between statements, with no server-side reap. A5's two-engine model leaves the third candidate unattributable in the next census. The council recorded the asyncpg pool as "NO (per-statement autocommit)" — that is factually wrong; `checkpoint_adapter.py:1229-1239` opens an explicit `conn.transaction()`.

**Evidence:** council-report.md C-8; `checkpoint_adapter.py:1229-1239`; `persistence.py:225/:317/:355-359`.

**Displaces:** A5's two-engine model; the council's capability table entry for the asyncpg pool.

### D-37 — C-9: explicit pin amendment for G2.4

**Decision:** D-37 records an explicit pin amendment: amend `tests/unit/repositories/test_pg_engine_utc_session.py:86-88` to the new manager options string (value-by-value, order-pinned). Keep `:65` untouched (it is the D-17 guard). The file is NOT in `SEALED_SHA_BASELINES` ⇒ no seal re-pin ceremony needed. Correct F2-e's stale `:292` anchor → `:86-88`. G2.4 becomes "amended per D-37, diff attached to the gate report."

**Why:** the file asserts `captured["kwargs"]["connect_args"] == {"options": "-c timezone=UTC"}` — an exact equality on the manager engine's connect_args. F2 must change it. ⇒ G2.4 is unachievable, T2.5's "the file's existing assertions stay green" is false, and the implementer's only exits are: modify a protected pin, fail the gate, or weaken it — all three are C3 anti-patterns.

**Evidence:** council-report.md C-9; `test_pg_engine_utc_session.py:86-88/:65`.

**Displaces:** G2.4's "unmodified" claim; F2-e's stale anchor.

### D-38 — C-10: F7 measures occupancy AND residence with attribution

**Decision:** F7 must assert both, co-equal: (a) occupancy returns to baseline (`manager.engine.pool.checkedout()` returns to baseline after a churn cycle — in-memory, no DB round trip, sampler shape at `readiness.py:525-549`); (b) no backend's idle-in-txn residence exceeds the budget (`now() - state_change <= DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S`). The old "count == 0 at every sample" invariant is withdrawn. A count may be reported and bounded (`peak_occupancy ≤ λ_measured × budget`) but never asserted to be zero. PG state `idle` is a disclosure metric, not an assertion.

**Why:** a connection checked out before its first statement is PG state `idle`, not `idle in transaction`. The incident's shape was 15/15 slots held with only 6 in `idle in transaction` and 9 idle-but-checked-out ⇒ a census-only guard would be GREEN on the incident.

**Evidence:** council-report.md C-10; `readiness.py:525-549`.

**Displaces:** F7's old "count == 0" invariant; A2's "0 idle in transaction at every sample."

### D-39 — C-11: release posture (stage-only)

**Decision:** STAGE ONLY. NO promote (user's 3-factor nonce ceremony). NO daemon restart. NO `.env` touch. NO live/production DB contact. No prod-data remediation. Version slot `v0.27.12` is PROVISIONAL (re-derived from live census at stage time). merge-latest-if-moved (5 steps, L1 exit-78 refusal honored). `rollback_safe` adjudicated and recorded. Expected migration delta: EMPTY. Staged artifact carries both provenance sidecars; staging worktree kept.

**Why:** no artifact forbids promote/daemon restart/.env touch; no merge-latest-if-moved step; `rollback_safe` never adjudicated. The soak branch had an unpushed tip and a `stage.sh` live run in flight.

**Evidence:** council-report.md C-11.

**Displaces:** nothing (new section).

### D-40 — C-12: F8 carries a measuring pin

**Decision:** F8 must carry a pin that MEASURES the metric it claims to move (M-R3's manager-slot occupancy/residence under load, before/after). The instrument is the leader-ruling-4 preferred INSTRUMENTED CHECKPOINTER (no cross-pool checkpoint-table locks — other lanes share the same scratch PG and a table lock could perturb them); the checkpoint-table lock technique is the FALLBACK. Mutation-meaningful either way. F8-a (static, default-collected) + F8-b (PG) added to the pin inventory. D2's `LOCK TABLE task` technique is reused ONLY for the F4 leg.

**Why:** no F8 pin exists in the inventory and D2 locked the wrong table on the wrong pool (the reconcile awaits a checkpoint read on the P2 saver pool, not a `task` read on the manager pool).

**Evidence:** council-report.md C-12; `child_reports.py:1654`; `persistence.py:316-331`.

**Displaces:** nothing (new pins).

### D-41 — Phase 0: empirical baseline (leader ruling 5)

**Decision:** A new Phase 0 precedes Phase 1. It runs on a SCRATCH PostgreSQL database (lane-created, dropped at the end — NEVER ensemble_prod, NEVER the live daemon's DB, NEVER a restart of anything), with PER-VARIABLE env scrubbing (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT` — `env -u POSTGRES_*` is a silent NO-OP). Phase 0 produces, as EVIDENCE, before implementation: (a) the base-code M-R3 hold duration under real load (settles the flip condition); (b) the occupancy/residence census shape at base (what F7 must assert); (c) measured PG runtime semantics (last_query fingerprints, application_name attribution per pool, whether idle_in_transaction_session_timeout actually fires); (d) the 3-pool attribution census. Phase 0 output = a BASELINE.md artifact + the F7 invariant definition derived from measurement. Phase 0 must PASS before Phase 1 implementation begins.

**Why:** the council had NO DB access; several PG-semantics claims are documentation-only and MUST be measured before building on them.

**Evidence:** council-report.md leader ruling 5; architecture-recommendation.md §7 (UNVERIFIED items).

**Displaces:** nothing (new phase).

### D-42 — Routed items (leader ruling 6): scope discipline

**Decision:** The event-loop sync-DB defect (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`) stays OUT of this lane (it is the dominant untreated symptom driver; async conversion is its own commission). Recorded as a ROUTED item with exact evidence. Same for: OQ-1 prod-data mint trigger; the BFS-depth-loop dwell defect (`repository.py:1434-1645`); missed-finalize own commission; default-executor ceiling; proxy-side coding-free drop.

**Why:** the plan does NOT claim to close the incident's symptom class. The sync-DB defect is a dwell multiplier, not a transaction defect. Async conversion is its own commission.

**Evidence:** council-report.md leader ruling 6; plan-overview §10/§12.

**Displaces:** nothing (new section).

### D-43 — VERIFIED-CLEAN (leader ruling 7): do not re-litigate

**Decision:** The following are VERIFIED-CLEAN and must not be re-litigated: D-17 repair-engine isolation is correct and already fenced by `test_pg_engine_utc_session.py:65`; zero new `.sql` files (partial unique index exists in 3 places, byte-identical predicate); C1 intact (`_evaluate_leak` arms only on the P2 oracle; P1 advisory); F4's probe-scoped SET LOCAL is C1-clean; the RCA's 3 refutations are accurate; the 2× client-fires-first rule is correct.

**Why:** the council verified these independently; re-litigating them wastes effort and risks weakening safety properties.

**Evidence:** council-report.md leader ruling 7; §7 (what the council preserved).

**Displaces:** nothing (affirmation of existing decisions).

### D-44 — Corrections to the council's spec (every correction recorded)

**Decision:** The following corrections to the council's spec are recorded:

1. **C-8 correction:** the council's capability table recorded the asyncpg adapter pool as "NO (per-statement autocommit)." This is WRONG — `checkpoint_adapter.py:1231` opens an explicit `conn.transaction()`. Corrected in D-36 (three-pool model).
2. **W-12 correction:** the Phase-1 DO-NOT-TOUCH list must be amended to permit the `readiness.py:107-113` docstring correction (A4-v). The docstring's premise ("the driver's own connect/execute timeout releases it — bounded leakage") is false at base: the manager engine sets no execute timeout.
3. **W-7 correction:** both granularities (file-level and knob-level) must be stated in the coupling matrix AND in the T3.6(d) gate: "persistence.py:316-331 knobs byte-identical; :306-315 honesty comment changed per D-21/R12 only."
4. **A-1 correction:** the architect's §1.3(i) over-corrects on the BEGIN fingerprint. SQLAlchemy's `DefaultDialect.do_begin` is a no-op ⇒ the BEGIN is emitted by psycopg3's implicit transaction start, pipelined with the first statement. A backend whose `last_query` is literally 'BEGIN' is most plausibly a session that sent a standalone BEGIN command — i.e. exactly Site A. Attribution rests on psycopg3's implicit-BEGIN pipelining, NOT wire-verified in this lane. A3's census must capture `query_start`/`backend_start` to settle it.
5. **A-2 correction:** the repair engine's `idle_in_transaction_session_timeout` landed `df4bdf994` (2026-09-09), hardened `0b660308e` (2026-10-11) — both ancestors of v0.27.4/v0.27.8 ⇒ during the 2026-10-26 incident the repair engine's 60 s reap WAS live ⇒ the 2 BEGIN backends were NOT long-lived repair-engine leases. Narrow the BEGIN fingerprint's owner set to the manager engine and the asyncpg adapter pool.

**Why:** these are corrections to the council's spec that the fold must record. They are not new decisions; they are refinements of existing ones.

**Evidence:** council-report.md (the corrections are documented in the report's "Council disagreements" and "Advisory" sections); architecture-recommendation.md A-1/A-2.

**Displaces:** nothing (refinements).

---

## 3. F3 — the repair-tool transaction-abort gap (Site A) — PRESERVED

### D-01 — DROP the explicit `BEGIN` (not "move it inside the inner try")

**The defect (verified at HEAD).** `daemon/tools/ens_db_tools.py`:

```
1475:  try:                                   # OUTER try
1479:      session.execute(text("BEGIN"))     # DDL branch — OUTSIDE the inner try
1480:      try:
1481:          session.execute(text(sql))
1483:      except Exception as exc:           # no re-raise
1488:      session.rollback()                 # ← the rollback that owns the branch
1491:      session.execute(text("BEGIN"))     # DML branch — OUTSIDE the inner try
1492:      try:
1501:          session.rollback()
1503:          session.rollback()             # except branch
1513:  except Exception as exc:               # OUTER except — NO rollback
```

So the BEGIN sits **inside the outer try but outside the inner try** that owns the rollback. A failure at the BEGIN (or at the DDL branch's own `rollback()`) reaches the outer `except:` at :1513 with **no rollback**.

**Option (a) — DROP the explicit BEGIN.** SQLAlchemy autobegins on the next `session.execute(...)`; the ROLLBACKs at :1488/:1501/:1503 — not the BEGIN — carry the "does not commit" property. No savepoint, isolation-level, or multi-statement-txn semantics anywhere in the function depend on the literal BEGIN. The only observable differences today are a PG `WARNING: there is already a transaction in progress` (the session has already autobegun when the literal BEGIN executes) and the `BEGIN` string appearing in `pg_stat_activity.last_query` (research-leak-sites.md:59). **No test in `tests/` asserts `text("BEGIN")`** (grep: zero hits).

**Option (b) — MOVE the BEGIN inside the inner try.** A BEGIN failure would then hit the inner `except` and the existing rollback would run. Side effect: the failure is labelled `"DRY-RUN DDL FAILED (…)"` although the SQL never ran (slightly misleading label).

**Option (c) — leave it, rely on the backstop.** The repair engine already carries `idle_in_transaction_session_timeout=60000` (`ens_db_tools.py:619-627`, `REPAIR_IDLE_IN_TXN_TIMEOUT_MS` at :188), so a leaked BEGIN backend is reaped within a minute. That is a **backstop, not a fix** — the defect remains, and the RCA's `BEGIN` fingerprint (the only fingerprint uniquely attributable to a code site) stays possible.

**Decision: (a) — DROP.** Rationale beyond the mechanics: dropping the literal BEGIN **removes the fingerprint class entirely**. `text("BEGIN")` exists nowhere else in `daemon/` (grep), so a future `idle in transaction` backend whose last query is `BEGIN` would be attributable to *this* site — and after this change, no such site exists. Option (b) would leave the fingerprint live and therefore leave the incident's triage ambiguity in place. Option (b) also keeps the double-BEGIN warning noise.

### D-02 — The rollback moves into a `finally`

After D-01, the uncovered path is a failure **of the rollback itself** (:1488): today that also lands in the outer `except:` (:1513) with no rollback. Only a `finally` covers SQL-success, SQL-error, and rollback-error uniformly. The DML branch's row-count projection (`_rows_to_dicts(result)`) must stay **inside** the `try`, before the `finally`'s rollback — the result is consumed before the transaction ends.

**Not chosen:** keeping the two existing `session.rollback()` statements in place — leaves the rollback-error path uncovered.

### D-03 — Agent-visible surface updated in the same change

The dry-run wording is part of the tool's contract with agents. To update: the preview strings `"DRY-RUN DDL EXECUTED INSIDE BEGIN/ROLLBACK — no commit"` (:1482) and its DML twin (:1496); the tool docstring's "DDL preview is a `BEGIN; <DDL>; ROLLBACK;` wrapper" (:963); the `_dry_run_sqlite_safe` docstring (:1407); the inline comments (:1076-1077).

**Deliberately NOT changed:** the module docstring (:37) and the tool docstring (:942) mention `BEGIN/COMMIT` in the **audit contract** ("every repair writes a row to `repair_log` in the SAME `BEGIN/COMMIT` as the repair on the SAME connection"). That describes the **committed** path, not the dry-run. Rewriting it would misdescribe a contract that is unchanged.

**Contract that must survive the change (AC3):** the dry-run's `previewed` audit row is written in a **separate** session via `_write_audit_outcome` (:1530-1538, "Persist the previewed audit row in a SEPARATE session so the ROLLBACK above does not eat the audit row"). The `finally`-rollback does not touch it.

---

## 3. F4 — readiness-probe timeout-path retention (Site D) — PRESERVED

### D-04 — Probe-scoped `SET LOCAL statement_timeout` (house idiom); a dedicated probe pool is rejected

**The defect (verified at HEAD).** The DB and queue probes run under `asyncio.wait_for(asyncio.to_thread(probe), timeout=…)` (`readiness.py:1279-1281`). On timeout the asyncio side gives up but **the worker thread keeps running with its connection checked out** (`:1477` / `:1752`). The module's own docstring calls this "bounded leakage" (:107-113) — but on the **manager engine** that bound does not exist: the engine carries **no** server-side `statement_timeout` and **no** `idle_in_transaction_session_timeout` (the manager engine's only session option is `-c timezone=UTC`, `factory.py:252/:337`), and there is no client-side execute timeout, so the retention is unbounded in theory (research-leak-sites.md:118, §4 premise 8).

**The house idiom for exactly this problem already exists.** `ens_db_tools.py:787-797` bounds a statement on the **shared, pooled** engine with:

```python
with manager.engine.begin() as conn:
    # Per-tx statement_timeout inside the tool's own tx (NOT engine-wide connect_args).
    if manager.engine.url.get_backend_name().startswith("postgres"):
        conn.execute(text(f"SET LOCAL statement_timeout = {int(SELECT_TX_STATEMENT_TIMEOUT_MS)}"))
    result = conn.execute(text(query))
```

with the explicit rationale that it is per-tx and not engine-wide. `SET LOCAL` is scoped to the transaction and is reverted by the pool's `reset_on_return='rollback'`, so it does not leak to the next borrower. This is the same shape the probes need: each probe owns its transaction.

**Options considered:**

- **(a) A dedicated readiness-probe engine/pool** — the RCA's own suggestion ("it should run on a dedicated short-lived connection", RCA fix-list item 4). **REJECTED on coupling C1.** The readiness `database`/`queue_freshness` components are the daemon's *observers* of the manager pool: if the probes ran on their own pool, the readiness board would report green while the manager pool was poisoned — i.e. **detector leniency by relocation**, which is exactly what C1 forbids. A separate pool would also need the same server-side timeouts, and it would add a pool (more connections, more knobs, more incident surface). It is rejected on **safety**, not on complexity.
- **(b) Engine-wide `connect_args` timeouts only** — the whole daemon's blast radius for a probe-scoped need. Retained as the **second layer** in Phase 2 (D-16) and as F2's engine-level backstop; it is *not* the primary carrier because it cannot be scoped per probe.
- **(c) Per-probe asyncio cancellation** — impossible: `asyncio` cannot cancel a running thread's synchronous DBAPI call. The saver probe's `future.cancel()` precedent works only because that probe is an **async** coroutine scheduled on the loop (`readiness.py:1669-1684`), not a sync DBAPI call in a worker thread (research-leak-sites.md:120).

**Decision:** (b)-as-second-layer + the house's (a)-equivalent idiom (`SET LOCAL`) as the **primary**, scoped per probe. The probe-scoped bound alone is sufficient to satisfy F4; the engine-level layer is defense in depth and can be dropped (see OQ-3, now REVERSED by C-3) without reopening F4.

### D-05 — The bound is 2× the client budget

The client-side budget is what **classifies** the failure (`asyncio.TimeoutError` → `FAILURE_CLASS_PROBE_TIMEOUT`, `readiness.py:1284-1285`). A server bound at or below the client budget would race the client and could reclassify a timeout as a hard component red — a behavior change in the readiness contract. A server bound of **2×** the client budget means the client always gives up first (deterministic classification preserved) and the server releases the connection shortly afterwards. Concretely: DB probe `2 × DB_PROBE_TIMEOUT_S` (0.5 s → 1000 ms); queue probe `2 × QUEUE_PROBE_TIMEOUT_S` (2.0 s → 4000 ms). The millisecond value is **derived from the module's own constant** in one place, so the two cannot drift.

**Residual to disclose:** the queue probe issues **two** statements inside one transaction, so its worst-case server-side hold is ~2× the per-statement bound. Bounded, but not a single number.

### D-06 — PG-only; SQLite untouched

SQLite has no `SET LOCAL` and would raise `OperationalError` — the same dual-engine concern the house already documented at `ens_db_tools.py:790-792`. The guard is the dialect check the repo already uses (`readiness.py:1747` — `engine.dialect.name == "postgresql"`). The SQLite install path must be provably unchanged.

### D-07 — An explicit end-of-transaction precedes the probe's connection return

RCA fix-list item 4 asks the probe to "explicitly commit/rollback". `Connection.rollback()` is a documented **no-op when no transaction is active** (SQLAlchemy 2.0.48, `engine/base.py:998-1023`), so calling it unconditionally is safe. It ends the probe's transaction at the probe rather than at pool reset, and it makes the intent visible to a reader (and assertable by a pin).

### D-12 — Phase 1 lands no pool knob

The engine-level `connect_args` layer (D-16, now REVERSED for statement_timeout) belongs to the **F2 knob table** — one table, one pin, one owner. Splitting it across phases risks a half-configured intermediate state and two competing pins. F4's *guarantee* in Phase 1 is carried entirely by the probe-scoped bound (D-04), which is independent. Phase 2 completes the table and the pin.

---

## 4. F6 — the mission boot mint (Site E) — PRESERVED

### D-08 — Idempotent mint via a dialect upsert; no schema change

**What the census established.** The rollback-guarantee half of the RCA's fix-item 6 **already holds**: the boot lane passes **no** session (`auto_continue_boot_pass.py:1154-1159`, and the pass loop at :721); `insert_mission` opens `with Session(self._engine) as own:` (`mission_record/repository.py:510`) and commits at :512 — an `IntegrityError` exits the block ⇒ `close()` ⇒ the pool's reset-on-return rollback. **779 audited borrow sites (AST baseline: A=613 / B=175 / C=4 / D=148 / E=14 / F=0), 0 unclosed** (research-audit-census.md §1b; the retired aggregate is NON-CANONICAL — not scanner-independent, audit-method.md:283-296). So the "guarantee the transaction is rolled back" option is **belt-and-braces on an already-guaranteed path** — it would not remove the error.

**What is actually broken.** The mint is **not idempotent** for the boot shape: the deterministic 4×/boot `IntegrityError` on `ux_mission_records_anchor_open` (RCA :61, :149) is a real observable whose trigger is unexplained (OQ-1).

**The fix.** Replace the own-session ORM insert with a dialect-appropriate insert carrying `.on_conflict_do_nothing(index_elements=["anchor_instance_id"], index_where=text(OPEN_EPOCH_STATUS_SQL))` — the **exact house shape** already in `daemon/repositories/job_queue/repository.py:485-496` (same `index_elements` + `index_where` idiom on a partial index), with the dialect helper of `daemon/repositories/source/repository.py:33-46` (`pg_insert` for PostgreSQL, `sqlite_insert` otherwise). On `rowcount == 0` the repository re-resolves the winning row (see D-09) and returns it.

**Migration implications — VERIFIED, expected NO schema change.** The constraint is a **partial** unique index defined **identically in three places**:
- `daemon/repositories/mission_record/models.py:307-313` (`Index("ux_mission_records_anchor_open", "anchor_instance_id", unique=True, sqlite_where=…, postgresql_where=…)` over `OPEN_EPOCH_STATUS_SQL`);
- `daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql:67-69`;
- `daemon/manager.py:8549` (the PG runtime `_ensure_postgres_columns` mirror).

No new table, column, or index is introduced ⇒ the house rule ("every new SQLModel table needs its own `.sql` file") is **not triggered** ⇒ **zero new `.sql` migration files** in this lane (asserted at the gate, AC14/AC23/AC29). PG's `ON CONFLICT` against a partial index requires the index predicate to be repeated — satisfied by reusing the byte-identical `OPEN_EPOCH_STATUS_SQL` constant (the migration header pins the 3-site byte-identity, `:30-35`).

**A second, independent benefit.** `DO NOTHING` raises **no** error, so the *caller's* transaction is no longer poisoned by a statement-level `IntegrityError` — the exact failure `_mint_epoch_locked` documents today ("The caller's transaction is poisoned on PG after a statement-level IntegrityError — fail the mint", `mission_record_service.py:548-554`). Poisoned caller transactions are themselves an aborted-transaction source, so this removes a secondary contributor to the incident's aborted-transaction errors.

### D-09 — The boot path's re-resolve is keyed on the index's own predicate (AMENDED 2026-10-28 round-2 — P-7/DR-33)

`ON CONFLICT … DO NOTHING` returns **no row**, so the caller must re-resolve the winning epoch. The existing re-resolve is keyed on `founding_message_id` — and the code itself records that this probe "can NEVER be found by that probe" for a NULL-founding epoch (the boot epoch's `founding_message_id` is NULL by construction, `mission_record_service.py:558-565`, :686-688). The **anchor-scoped** rung-1 probe (`find_open_epoch_by_anchor`, `mission_record/repository.py:69-93`) filters on exactly the index's predicate (`anchor_instance_id` + `status = 'open'`) and is therefore the correct key for the boot path.

**Amendment (P-7/DR-33):** the boot path's re-resolve probe is actually **SKIPPED** (`founding_message_id=None`, `mission_record_service.py:561/:567`), not mis-keyed — D-09's mis-keyed-probe claim is RETRACTED. The measured trigger is a **concurrent double-mint** (c4: 8-way, no barrier, 7 UniqueViolations; c4b forced overlap: 5). The upsert (D-08) is idempotent under any trigger; the disclosure and re-resolve wiring must match the measured mechanism (skip + concurrent double-mint). F6's idempotency is specified against CONCURRENT double-mint.

**Why this matters for OQ-1:** the upsert is idempotent under **any** trigger (race, duplicate call site, pre-existing duplicate rows), so the fix does not depend on resolving the trigger. The trigger is now **measured** (concurrent double-mint) and the disclosure is corrected. See OQ-1 → CLOSED-by-measurement (plan-overview R-8).

### D-10 — Scope: the own-session (boot) path only

The caller-session path (`session is not None`) keeps its documented semantics — "the caller's transaction is poisoned … fail the mint and let the caller's fail-open wrapper skip it" (:548-556). That re-raise is load-bearing for the commissioning/revive lanes, whose re-resolve (keyed on `founding_message_id`) **does** work there, and the observed 4×/boot defect does **not** occur on that path. Changing it would alter commissioning-lane semantics for a defect that does not occur there (R5).

---

## 5. F1 — the transaction-hygiene audit record — PRESERVED

### D-11 — A new artifact in this planning dir; the RCA is not edited

The census's headline is the honest result of F1: **779 borrow sites (AST baseline: A=613 / B=175 / C=4 / D=148 / E=14 / F=0) across 6 families, 0 unclosed borrows, 0 leaks** (research-audit-census.md §1b; the retired aggregate is NON-CANONICAL — not scanner-independent, audit-method.md:283-296). The method and the full counts are authored by a sibling worker in **`audit-method.md`** — this plan cites it **by filename** and does not restate its contents.

F1's deliverable is therefore **not** a code change. It is `.agents/shared/planning/db-pool-idle-txn/audit-findings.md`, which records:
1. the census headline + the method citation (`audit-method.md`);
2. RCA sites **B** and **C** as **audited-and-clean**, with their premise corrections (`repository.py:1383/:1434/:1429`; `defer_block_resolver.py:185-191/:274/:306/:321`; `instance_read.py:1408-1416`);
3. the five-backend reconciliation with its **inference** labels;
4. the explicit statement that no leak class was found where the census found none.

**Why not edit the RCA:** it lives in the main checkout and is under this lane's **read-only fence**. **Why not a code comment:** a one-time audit is not a code invariant.

---

## 6. F2 — pool hardening — PRESERVED (D-16 REVERSED)

### D-13 — Scope

Harden the **manager engine** (`daemon/repositories/factory.py:323-338`) and add the repair engine's **explicit reset pin** (`daemon/tools/ens_db_tools.py:633`). Untouched: the SQLite engine (:184-199), the latent config-driven PG branch (:215-222 — D-22), the boot preflight (`daemon/__main__.py:237-240`), the **P2 saver pool** (`daemon/persistence.py:316-331` — coupling **C2**, and the leak-signal ORACLE), the asyncpg adapter pool (`persistence.py:355-359`), `ConnectionPoolManager` (`db_pool_manager.py:261-271` — the daemon's own DB is excluded by the standing R13 guard at `ens_db_tools.py:584-589`), the vacuum NullPool (`ens_db_tools.py:649-695`), and the auth CLI engines (`auth/cli.py:152-160`).

### D-14 — Exact manager-engine knob values

| Knob | Before | After | Rationale |
|---|---|---|---|
| `pool_size` | `5` | `5` | The soak lane's A4 sizing applies to the **P2** pool; the manager engine's 5+10 stays. Changing it changes the concurrency envelope under the incident's load |
| `max_overflow` | `10` | `10` | same |
| `pool_pre_ping` | `True` | `True` | Already set (:328) — RCA fix-item 2's first clause was already satisfied |
| `pool_reset_on_return` | *absent* (library default `'rollback'`) | `"rollback"` | The property "an aborted connection is never handed out" becomes an **asserted invariant** instead of an implicit library default. `reset_on_return` is set **nowhere** in `daemon/` (research-pool-couplings.md:56). Note (from the same source): reset-on-return does **not** save a connection that is checked out and never returned — the R2 shape — which is why the server-side timeouts (D-16, now REVERSED for statement_timeout) are the actual cure |
| `pool_recycle` | *absent* | `3600` | Matches the only in-repo recycle precedent (`REPAIR_POOL_RECYCLE_S`, `ens_db_tools.py:182`). Bounds exposure to server-side/credential churn; at 15 slots that is ~15 reconnects/hour — negligible |
| `pool_timeout` | *absent* (library default `30`) | `30` | See D-15 |
| `pool_use_lifo` | *absent* | *absent* (deliberate) | LIFO reduces idle-time churn, but `pool_pre_ping` already covers dead connections and `pool_recycle` now governs age. Adding it changes checkout semantics under load with no measured benefit |
| `connect_args` | `{"options": "-c timezone=UTC"}` | `{"options": "-c timezone=UTC"}` + **per-engine CONNINFO attribution (D-49)**; the `idle_in_transaction_session_timeout` clause is **OMITTED while D-47-DISABLED** (defined but not applied); never the literal `60000` (D-16-AMENDED); `application_name` is set via the conninfo parameter, NOT a shared `-c` (D-49). **Superseded inline:** the old string `{"options": "-c timezone=UTC -c application_name=ensemble-manager … idle_in_transaction_session_timeout=60000"}` is RETIRED — attribution moved to conninfo (D-49), the idle-in-txn clause is omitted while disabled (D-47), and the literal 60000 is forbidden (D-16-AMENDED). | D-16 (REVERSED for statement_timeout) + D-30 (application_name, now via conninfo) + D-47 (disabled) + D-49 (conninfo channel). The **UTC option is preserved** (the B2/B3 UTC-frame guarantee) and stays **first**, so the existing byte-shape pin's prefix is untouched; the option **order** becomes part of the pinned shape |
| URL `connect_timeout` | `BOOT_DB_TIMEOUT_S` (10) | unchanged | The connect-phase bound is already explicit (:316) |

**Knob values live in `daemon/constants.py`** (D-18).

### D-15 — `pool_timeout` review outcome: keep 30s, pinned explicitly, residual disclosed

The probe budgets (0.5 s DB / 2.0 s queue) are **asyncio** budgets: `asyncio.wait_for` returns "timed out" at the budget while the worker thread continues waiting for a checkout (`readiness.py:1279-1281`). Lowering `pool_timeout` would convert pool pressure into **hard errors** (500s) on legitimate requests during exactly the incident's conditions (the endpoint was already at 5-8 s). The repair engine's 10s value would be parity-motivated but behaviorally riskier on the hot path.

**Residual, stated rather than hidden:** an abandoned probe **worker thread** may linger in the pool's checkout queue for up to 30 s. It holds **no pool slot** while waiting (it has no connection yet), and it is bounded — unlike today's unbounded server-side retention.

**Explicitly pinning the default** (rather than leaving it unset) means a future edit cannot silently change it, and the pin documents that 30s was a **decision**, not an oversight.

### D-16 — Engine-level server-side timeouts (REVERSED for statement_timeout) (AMENDED 2026-10-28 round-2 — P-3/DR-29)

`-c statement_timeout=30000` — **DROPPED** (C-3, leader ruling 2). The future global cap must `SET LOCAL statement_timeout = 0` inside `manager.py:8760`.

`-c idle_in_transaction_session_timeout=60000` — **RETAINED** (the repair engine's proven value, `REPAIR_IDLE_IN_TXN_TIMEOUT_MS = 60_000`, `ens_db_tools.py:188`). Together with F4's probe-scoped bound, this bounds **every** idle-in-transaction backend on the manager engine — the exact symptom the incident recorded — regardless of what the client does.

**`lock_timeout` is deliberately NOT added**: the manager engine's failure mode is idle-in-txn, not lock contention; a global lock timeout could abort legitimate contended writes.

**This layer is optional (OQ-3, now REVERSED):** F4's guarantee is carried by the probe-scoped bound (D-04). The idle_in_transaction_session_timeout is retained; the statement_timeout is dropped. Dropping the statement_timeout costs F4 nothing.

**Amendment (P-3/DR-29):** the manager idle-in-txn bound ships **DISABLED** (defined, not applied) — the max legitimate hold is model-derived (1059.877 ms) or unidentified (real fetch unmeasured); the arithmetic value is 4.24 s so the 30 s floor dominates and coincides with the legitimate `pool_timeout` wait; a 30 s server-side reap risks killing legitimate waiters and the server TERMINATES backends (raw t2:137-140). Enablement mechanism: applied iff BASELINE.md records a trustworthy measurement (the constant is then set from it); while disabled the rendered string omits the clause and the daemon logs `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no trustworthy Phase-0 measurement` at boot. The base measurement is an **upper bound**; the post-F8 re-measurement (F8-b's BASE-vs-HEAD comparison) is the figure the applied value should use once available (DR-11). The 2.0 s placeholder for F7 residence is **withdrawn** in favour of the measured healthy-residence band (median 0.0005 s / p95 0.0007 s / max 0.0008 s over 40 cycles) plus the hold budget, using `xact_age_s` (`now() − xact_start`), not `state_change − xact_start` (P-4/DR-35).

### D-17 — A NEW connect-args constant; `PG_SESSION_CONNECT_ARGS` is not extended

`PG_SESSION_CONNECT_ARGS` (`factory.py:252`) is **embedded** into the repair engine's options string (`ens_db_tools.py:619-627`), which already sets `statement_timeout=60000`. Extending the shared constant with `-c statement_timeout=30000` would make the repair engine's effective `statement_timeout` **30 s instead of 60 s** (PG's last `-c` wins) — a silent, unintended retune of a pool this lane is otherwise leaving alone.

So: a **new** manager-engine constant is assembled in `factory.py` from values that live in `daemon/constants.py`; `PG_SESSION_CONNECT_ARGS` stays **byte-identical** and keeps serving the repair engine. A dedicated pin (AC15/AC16) asserts the repair engine's rendered options string is byte-identical.

### D-18 — One canonical home per value

Knob **values** → `daemon/constants.py` (precedent: `factory.py:14` already imports `BOOT_DB_TIMEOUT_S` from it; `CHECKPOINT_POOL_MAX_SIZE_DEFAULT` + its honesty block at `constants.py:197-217` are the same pattern). The options **string assembly** stays in `factory.py` (a rendering detail, and the repair engine's assembly is there too).

### D-19 — The repair engine changes by exactly one knob

`pool_reset_on_return="rollback"` is added; `pool_size=2`, `max_overflow=3`, `pool_pre_ping=True`, `pool_recycle=3600`, `pool_timeout=10` and the whole `connect_args` options string stay **byte-identical**. Rationale: the repair engine is the best-configured pool in the repo; the only gap is the same implicit-reset gap, and it is the pool the incident's fingerprint came from — so the reset semantics there deserve to be pinned, not assumed.

### D-21 — The honesty blocks are refreshed

`daemon/constants.py:207-217` and `daemon/persistence.py:306-315` both state that the P1 idle-in-txn leak "is a SEPARATE defect owned by a follow-up commission". That becomes false the moment this phase lands. Both blocks are updated to record the repair (branch + date) while keeping the still-true content (the P2 pool's sizing does not cure a leak on another pool; the P2 oracle remains the arming source). **No knob value in either file moves** — and in `daemon/persistence.py` **nothing but the comment changes**. Additionally (A9-v): the honesty blocks must state that the lane repairs the **retention/transaction-hygiene** class and **does not** repair the **dwell/occupancy** class.

### D-22 — The latent config-driven PG branch is not hardened

`create_engine_from_config`'s PostgreSQL branch (`factory.py:215-222`) has **zero** daemon-runtime PG callers (its own comment at :201-203 says so) and is deliberately **config-driven** (`config.pool_size` / `config.max_overflow` — a different contract from the canonical engine). Changing it widens the blast radius for no benefit. It is recorded in the out-of-scope list with a note that a future caller must migrate it deliberately.

---

## 7. F5 — the wedge-capture table name — PRESERVED

### D-20 — A correctness fix, not a pool fix; the table name only

`daemon/services/wedge_capture/db_capture.py:308` (`FROM tasks t`) and `:387` (`FROM tasks`) are wrong — the table is `task` (`daemon/repositories/task/models.py:171`) — and the module docstring (:34) repeats the error.

**It is not a pool fix:** the capture rides a **one-shot `psycopg.connect`** (`_connect` :185-200, `connect_timeout` + per-statement `statement_timeout`, `application_name='wedge-capture'`) with `close()` in a `finally` (:289-294). **No pool** ⇒ it cannot leak an idle-in-txn backend. The incident's `current transaction is aborted` lines are **same-connection cascades**: the first (wrong-table) query aborts the transaction, so the following census and resume-history reads fail even though their own SQL is valid (research-leak-sites.md:157). Renaming fixes both the error and the cascade.

**Scope: the table name only.** Every selected column was verified present on `task`: `work_id`, `instance_id`, `task_type`, `status`, `created_at`, `started_at`, `last_heartbeat_at`, `suspension_reason`, `resume_target_turn_id` (`task/models.py:196-284`) and `auto_continued_at` (`task/models.py:294`; added on PG by `manager.py:7559`). The `LEFT JOIN instances` is correct. **Verification note:** the PG pin must run against a database where `auto_continued_at` exists (the manager's boot-time `_ensure_postgres_columns` adds it).

---

## 8. F7 — the regression guard, and the gate discipline — PRESERVED + AMENDED

### D-23 — Integration placement, with a scratch database

The guard must build the **daemon's own manager engine** (so the engine's connect args and pool knobs are what is under test) and observe `pg_stat_activity`. The `tests/postgres/` shared `pg_engine` fixture owns its own schema lifecycle (`drop_all`/`create_all`, then a per-test `TRUNCATE … RESTART IDENTITY CASCADE`, `tests/postgres/conftest.py:187-195/:240-258`) and gives the test no database it can observe in `pg_stat_activity`; it is also auto-skipped under xdist (:44-45/:94-95). The soak lane's real-pool PG pins took the **integration** shape (`tests/integration/test_checkpoint_pool_burst_shapes.py:49`, `test_checkpoint_pool_ghost_waiters.py:58`), each with `pytestmark = pytest.mark.integration`, and that is the shape this guard takes.

### D-24 — Attribution by scratch-database isolation, not `application_name`

The daemon's connections carry an **empty** `application_name` (research-leak-sites.md:173), so `pg_stat_activity` cannot attribute a backend to a pool post-hoc — the same gap that makes the incident's five-backend attribution partly inference. For the guard, the test creates its **own** database and filters `pg_stat_activity WHERE datname = <that database>`; every backend there is the test's. This avoids changing the daemon's connection metadata (out of scope) and gives the guard *stronger* attribution than the incident had.

**Amended by D-30 (C-2):** attribution is "by `application_name` where present, else by scratch-DB isolation." D-24 is reconciled, not replaced.

### D-25 — A default-collected static leg

A default `pytest` run collects **zero** PG tests (C3). Without a default-collected leg, deleting or un-marking the guard would leave the whole tree green — the exact false-green shape the house rule exists to prevent (R7). G-A asserts: the guard file exists, carries the `integration` marker, uses the scratch-DB isolation, and that the manager engine's connect args carry the server-side timeouts.

### D-26 / D-27 — Gate discipline

Every gate: (i) runs in the house form — `--override-ini="addopts=" -m postgres|integration`, or is default-collected; (ii) **asserts its expected collected count > 0** before "0 failed" is read as a pass; (iii) names the interpreter explicitly (`python` does not exist on this host; `uv` is at `/home/nea/.local/bin/uv`); (iv) scrubs ambient `POSTGRES_*` **one variable at a time** and fences `HOME` with `mktemp -d` — **`env -u POSTGRES_*` is a silent NO-OP** (bash does not glob env names) and is forbidden.

**Amended by D-29 (C-1):** the interpreter is `/home/nea/ensemble-src/.venv/bin/python`, NOT `uv run`. `uv` is off-PATH.

---

## 9. Phasing rationale (D-28) — PRESERVED + AMENDED

**Phase 0 — empirical baseline (NEW — leader ruling 5).** Convert documentation-only PG-semantics claims into measurements on a scratch DB. Produces BASELINE.md. Hard predecessor of Phase 1.

**Phase 1 — transaction hygiene (F3, F4, F6) + the F1 audit record + F8 (NEW).** These are the behavior-changing fixes with the highest incident leverage, each independently verifiable without touching the pool configuration. F3 and F4 address the two mechanisms that survive the census; F6 removes the boot-time error class; F1 publishes the honest audit record; F8 removes the dominant multi-second producer M-R3.

**Phase 2 — pool hardening (F2) + the wedge-capture fix (F5).** The knob table is one table with one pin and lands after Phase 1 so the pin asserts the **final** configuration (including the timeout options, whose vehicle is shared with F4). F5 is independent of the pool work and rides here because it is small, self-contained, and its PG pin shares the phase's PG gate.

**Phase 3 — the regression guard (F7) + the gate structure.** Last, so the guard pins the **final** behavior of the fixed code, and so the lane's evidence set (mutation-proven-ness, net-new-reds, DO-NOT-TOUCH diff review) is assembled against a settled tree.

**Why the F4/F2 vehicle split is explicit (D-12).** F4's *guarantee* lands in Phase 1 via the probe-scoped bound; the engine-level backstop lands in Phase 2 with the rest of the knob table. A reader of the phase gates must be able to tell which artifact carries which guarantee — hence the explicit statement in `phase1-plan.md` §1 and `phase2-plan.md` §3.1.

**Why F8 lands in Phase 1 (NEW).** F8 is the only change that removes a multi-second producer, and it is a prerequisite for sizing F2's idle-in-txn bound (A6-i). The engine-level `idle_in_transaction_session_timeout` must land AFTER F8, or it can kill legitimate M-R3 work.

---

## 10. Re-targeting ledger — every WHAT from the dispatch, and what it became — PRESERVED

The dispatch permits refining the HOW but forbids silently dropping a WHAT. Every refinement is recorded here with its citation.

| Dispatch item | WHAT preserved | How refined / re-targeted | Why |
|---|---|---|---|
| **F0 (NEW)** | Empirical baseline | NEW — leader ruling 5 | The council had no DB access; PG-semantics claims must be measured |
| **F1** — transaction hygiene + site audit | "Document the census honestly; the method + counts live in `audit-method.md`" | The **code** content of F1 collapses to zero: the census found **0 unclosed borrows** across 779 sites (AST baseline: A=613 / B=175 / C=4 / D=148 / E=14 / F=0; the retired aggregate (scanner-dependent, non-canonical), audit-method.md:283-296). F1 = the audit document (B/C audited-and-clean + RCA premise corrections), delivered as `audit-findings.md` in this planning dir | research-audit-census.md §1b (779 CLEAN / 0 LEAK); the RCA's premise-2/4 corrections |
| **F2** — pool hardening | All five knob families | `pool_pre_ping` was **already** `True`; `reset_on_return` was **already** effectively `'rollback'` (library default). The genuine delta is: the reset pin made **explicit**, `pool_recycle`, the two server-side timeout options (now REVERSED for statement_timeout), and a `pool_timeout` **review outcome** (keep 30s, pinned, residual disclosed). **NEW:** `application_name` added per engine (D-30) | research-pool-couplings.md §Q3 knob matrix; `factory.py:323-338` |
| **F3** — the BEGIN gap | The site + the guarantee of rollback on every path | HOW chosen: **drop the BEGIN + `finally`-rollback** rather than move-inside-try | research-leak-sites.md:59 + the fingerprint argument in D-01 |
| **F4** — probe retention | "The readiness probe must not leak / amplify" | The **per-tick** premise is **REFUTED**, so the fix is re-targeted from "stop the per-tick leak" to "**bound the timeout-path retention server-side**" (the WHAT — the probe must not leak or amplify — is preserved). HOW: probe-scoped `SET LOCAL` + explicit end-of-transaction; a dedicated probe pool is **rejected on C1** | research-leak-sites.md §1 Site D + §4 premise 8; soak `decisions.md` §2 premise 10 |
| **F5** — wedge-capture | The rename `tasks` → `task` | Unchanged; **added** the observation that this path rides no pool and that the incident's aborted-transaction lines are same-connection cascades — so it is a correctness fix, not a pool fix | research-leak-sites.md:155-157 |
| **F6** — mint `UniqueViolation` | Idempotency vs disclosure; record the unexplained trigger | The **rollback-guarantee branch collapses** (already guaranteed by the census); **upsert** chosen. HOW narrowed to the boot/own-session path. Migration expectation **verified: NO schema change** (the partial unique index exists in 3 places) | research-leak-sites.md:140-144; `models.py:307-313` + `20261025_140000_…sql:67-69` + `manager.py:8549` |
| **F7** — regression guard | The guard | Placement, isolation model, attribution technique, and the mutation-proven-ness requirement are **decided here**; the detailed design stays in `test-strategy.md`. **AMENDED:** the invariant now measures occupancy + residence (C-10), not count==0 | research-pool-couplings.md §Q5 (runner forms) + §Q6 (no existing idle-in-txn assertion) |
| **F8 (NEW)** — borrow-then-await removal | Remove the dominant multi-second producer M-R3 | NEW — leader ruling 1 (F8 IS IN SCOPE). Three-phase split + 3 sites + measuring pin | council-report.md C-5/C-6/C-12; architecture-recommendation.md A1 |

---

## 11. Open questions carried forward — PRESERVED + UPDATED

(See plan-overview.md §9 for the full list, including OQ-6 and OQ-7.)

---

## 13. Safety-precedence note — PRESERVED

(See plan-overview.md §13.)


---

## 12. Round-2 additions (D-45+) — Phase-0 measured (2026-10-28)

### D-45 — F4 premise rewrite + demotion (P-1/DR-29)

**Decision:** F4 is **demoted** from "primary" to a **bounded defence-in-depth** item. Its outcome is restated as "the probe's statement duration is server-bounded **when the probe holds a connection**" (b2: `active`, not `idle in transaction`); the rationale "the abandoned probe worker cannot hold a connection open indefinitely" is **dropped** because the measurement refutes it for the starvation shape (b3: the probe is a pool WAITER, bounded by `pool_timeout`). The starvation shape is addressed by F8 + F2's pool occupancy/waiter arguments, not by a probe-scoped statement bound. F4's PG-only guard and 2×-budget sizing remain, but F4 no longer carries the lane's primary causal claim.

**Why:** the baseline's T5b table shows that under pool starvation (the realistic incident shape) the abandoned probe thread holds **NO** connection (`checkedout_right_after_timeout` = 15 = holder count). A probe-scoped `SET LOCAL statement_timeout` is therefore IRRELEVANT to that shape. When the probe *does* hold a connection (b2) the backend is `active`, not `idle in transaction` — invisible to an idle-in-txn census.

**Evidence:** BASELINE.md T5b; raw t5 (b1=5.8 ms, b2 timeout=0.5017 s, b3 timeout=0.5012 s); plan-overview §3.5 P-1; delta-recheck.md DR-29.

**Displaces:** F4 as the Phase-1 primary; the original causal-completeness claim for F4.

### D-46 — Flip-condition precondition restatement (P-2/DR-30); F8 stands

**Decision:** The M-R3 flip condition is restated as a **PRECONDITION**, not a constant: "the M-R3 hold < 2 s **iff** the awaited content fetch < 2 s." The decision instrument is the **fetch-latency** distribution (the async twin's content fetch, and the sync twin's 8 s bridge), **not** the hold. Measured base figures: async worst 1059.9 ms (L5, n=15, model-tainted — injected 1.0 s tail); sync twin ceiling 8.0 s (8011.119 ms, n=1). The real fetch latency (`_fetch_subshape_a_content_async`) remains unmeasured (baseline: "needs a booted daemon + a real checkpoint store") — disclosed, not resolved. **F8 stands** (worst hold 8011.119 ms ≥ 2000 ms ⇒ NOT MET).

**Why:** the measurement shows the hold is **not an independent cost** — it is exactly the awaited content-fetch latency (L5: hold median 1029.9 ms vs await median 1026.5 ms; L2: hold 52.4 ms vs await 47.7 ms). A threshold written as a constant decides the lane's mechanism on a number that is a function of the fetch's data.

**Evidence:** BASELINE.md consolidated rows 1-2; raw t1; plan-overview §3.5 P-2, OQ-6.

**Displaces:** OQ-6's constant form; D-40's "if hold < 2 s" phrasing (superseded by the precondition form).

### D-47 — The idle-in-txn bound ships DISABLED with the enablement mechanism (P-3)

**Decision:** `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS` is **defined but NOT applied** (disabled) at ship. Enablement mechanism: applied **iff** BASELINE.md (or a successor measurement) records a trustworthy measurement (the constant is then set from it); while disabled the rendered string omits the clause and the daemon logs `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS DISABLED — no trustworthy Phase-0 measurement` at boot. The base measurement is an **upper bound**; the post-F8 re-measurement (F8-b's BASE-vs-HEAD comparison) is the figure the applied value should use once available (DR-11). The pins assert the **structure** (clause present, value == constant, option order pinned) and the constant against `daemon/constants.py` — never the literal `60000` (DR-5/DR-20).

**Why:** the max legitimate hold is model-derived (1059.877 ms) or unidentified (real fetch unmeasured); 4× = 4.24 s so the 30 s floor dominates and coincides with the legitimate `pool_timeout` wait; a 30 s server-side reap risks killing legitimate waiters; the server TERMINATES backends (raw t2:137-140) — a behavioural change with no measured safety margin.

**Evidence:** raw verifier derivation (a); BASELINE.md; plan-overview §3.5 P-3; delta-recheck.md DR-7, DR-8, DR-11.

**Displaces:** D-32's "4 × measured, floor 30 s, applied" disposition; the 60 000 literal in AC14/T2.5.

### D-48 — F7 measured invariant: three-state occupancy + xact_age residence (P-4/P-10/DR-35)

**Decision:** F7's invariant is rewritten to the **measured** three-state + residence form: occupancy across all three PG states (`idle` with `xact_start IS NULL` ⇒ checked-out-but-no-statement; `idle in transaction`; `active`) **plus** residence (`state_change − xact_start` is DEFECTIVE for leak detection — use `xact_age_s` = `now() − xact_start`, already collected at common.py:88) against the hold budget of the operation that opened it. The 2.0 s placeholder is **withdrawn**: the healthy residence is measured (median 0.0005 s / p95 0.0007 s / max 0.0008 s over 40 cycles) and the budget's real job is to catch a hold exceeding the operation's own budget. A guard that counts only `idle in transaction` is **vacuous against two of the three shapes** and is not a valid F7. F7-a asserts the three states, not just `idle in transaction`.

**Why:** the specified metric (`state_change − xact_start`) is FROZEN at the last statement's completion ⇒ a leaked backend shows the same sub-ms value as a healthy one ⇒ the budget cannot fire on the leak; the 2.0 s floor would red a healthy daemon (sync twin holds ≥8 s; the real async fetch is unmeasured and data-dependent).

**Evidence:** raw M-7; BASELINE.md T2; plan-overview §3.5 P-4, P-10; delta-recheck.md DR-4, DR-35.

**Displaces:** D-38's provisional occupancy+residence form and the 2.0 s placeholder.

### D-49 — Attribution via per-pool conninfo (P-5/DR-31)

**Decision:** `application_name` is set in the **conninfo parameter** of each engine's own URL/query dict (the manager engine's `URL.create(...)` query dict and the repair engine's own conninfo), and the shared connect-args constant carries **no** `application_name`. The asyncpg pool uses `server_settings={"application_name": "ensemble-checkpoint-adapter"}` (or `?application_name=`) on a DSN built from the same config **without** reusing the shared `conn_string` variable, so the P2 oracle's `conninfo` stays byte-identical. Attribution is override-proof (conninfo beats `options=-c`), not merely last-wins-correct. F2-g/F2-h assert the channel.

**Why:** measurement shows the conninfo parameter BEATS `options=-c`; within one options string last `-c` wins; the shared-constant-last hazard is MEASURED (`repair_engine_reports_manager_name: true`, raw t3:117). A future edit appending the shared constant could still override the `-c` form.

**Evidence:** BASELINE.md consolidated; raw t3:103-141; plan-overview §3.5 P-5; delta-recheck.md DR-31.

**Displaces:** D-30's mechanism (a `-c` in the manager's own options string) as the authoritative channel (retained as a viable but weaker fallback).

### D-50 — Census scope = 11 sites (P-6/DR-32)

**Decision:** The three named engines (D-36) are the **daemon-internal** subset that can hold idle-in-txn; the **census** covers every pool-creation site (11 real sites + 1 comment false-positive at factory.py:289). D-36's boundary statement is amended: the three `application_name`s are the daemon-internal subset, not the census boundary. The F7 census scope is widened to every site.

**Why:** baseline consolidated row 6: "3 pools → 11 pool-creation sites; the incident's 18 backends are explainable by multi-pool occupancy → widen the census scope."

**Evidence:** BASELINE.md; raw t3:27-101; plan-overview §3.5 P-6; delta-recheck.md DR-32.

**Displaces:** D-36's "three named engines" as the census boundary.

### D-51 — F6 against concurrent double-mint (P-7/DR-33)

**Decision:** F6's idempotency is specified against **CONCURRENT double-mint** (c4: 8-way, no barrier, 7 UniqueViolations; c4b forced overlap: 5). D-09 is amended: the boot path's re-resolve probe is **SKIPPED** (`founding_message_id=None`), not mis-keyed; the measured trigger is the concurrent double-mint. The upsert (D-08) is idempotent under any trigger; the disclosure and re-resolve wiring match the measured mechanism. OQ-1 is CLOSED-by-measurement.

**Why:** the re-resolve probe is skipped on the boot path (code `mission_record_service.py:561/:567`), so D-09's mis-keyed-probe claim addressed a probe that does not run. The trigger is a concurrent double-mint, which the upsert handles by construction.

**Evidence:** BASELINE.md T5c; raw t5 c4/c4b; code `mission_record_service.py:544-567`; plan-overview R-8; delta-recheck.md DR-33.

**Displaces:** D-09's mis-keyed-probe framing (replaced by skip + concurrent-double-mint).

### D-52 — F5 raised to Phase 1 (P-8/DR-34)

**Decision:** F5 (wedge-capture `tasks` → `task` rename) is **raised from Phase 2 to Phase 1**. The incident's "aborted connection handed back out" premise is REFUTED for the pool path (`reset_on_return='rollback'` cleans it); the journal quote is a SAME-CONNECTION CASCADE in wedge-capture ⇒ the `tasks`→`task` rename is the cascade's root. Phase 5 table updated; §4 F5 row updated.

**Why:** the baseline refutes the aborted-connection premise for the pool path and identifies the wrong table name as the cascade's root.

**Evidence:** BASELINE.md row 4; raw t5 a5; plan-overview §3.5 P-8; delta-recheck.md DR-34.

**Displaces:** F5 as a Phase-2 item.

QUALIFICATION (2026-10-28, plan-owner-ratified): this attribution is PARTIAL — the `tasks`→`task` relation error was one of TWO roots. `_read_store_census`'s `n_live_tup`-from-`pg_class` defect is a second, pre-existing root (R-10; owner = the wedge-capture lane). F5 does NOT close the aborted-cascade.

### D-53 — The measured record's stale rows + one-canonical-copy rule (P-9)

**Decision:** The plan cites **RAW values** (b1=5.8 ms, b2 timeout=0.5017 s, b3 timeout=0.5012 s; PIDs 2325xxx) and drops the byte-exact claim (T4's "byte-exactly" is a prefix check on a 200-char-truncated value; F-A's quoted SQL is the COMPILED SQL, not the observed `last_query`). BASELINE.md ≡ RESULTS line-for-line today (divergence NONE) but the plan names ONE canonical copy (the RESULTS file) and requires the other to carry a pointer, not a fork. The 8 stale rows are recorded as a provenance finding (the reports were written against an earlier harness run).

**Why:** both copies carry the same 8 raw-divergent values; mutual agreement is a consistency property, not a correctness property.

**Evidence:** raw verifier score table (rows 18, 33, 34, 36, 37, 41, 42, 45); raw verifier §3; plan-overview §3.5 P-9; delta-recheck.md (the reviewer's T5b figures stale: raw b1=5.8 ms, b2 timeout=0.5017 s, b3 timeout=0.5012 s).

**Displaces:** the byte-exact claim; the retired aggregate (non-canonical).

### D-54 — Phase 0 COMPLETE + precondition held (P-10)

**Decision:** Phase 0 is COMPLETE — BASELINE.md + canonical RESULTS + harness + raw/ on branch; no implementation change landed before Phase 0 (git log 19fc11c8b..HEAD is docs/test only). The precondition "no implementation change lands until Phase 0 completes" held. The census-blindness claim is confirmed (a probe/connection holding work is `active` or idle-with-no-xact — an idle-in-txn-only census is vacuous against two of the three shapes). The three-state invariant folds into D-48's F7 form.

**Why:** the baseline's evidence is on the branch and its disclosed limits (real fetch latency unmeasured; mint overlap inferred; pool_timeout stall measured at 3 s) are carried into the honesty/scope statements.

**Evidence:** git log 19fc11c8b..HEAD; BASELINE.md; plan-overview §1 (status), §16; delta-recheck.md §13 (P0-6/P0-10).

**Displaces:** the "Phase 0 in flight" framing.

### D-55 — DR closure record (one per DR landing in these two files)

**Decision:** The DR closure for DR-1..DR-36 is recorded in plan-overview.md §16 (one line per DR). DRs landing in these two files: DR-1 (D-35 amended), DR-2 (C3c enumeration), DR-3 (C3b/C3c widened-scope note), DR-5 (D-47), DR-6 (SC10/census rewritten), DR-7 (D-47), DR-8 (D-47/D-48), DR-9 (D-49), DR-11 (D-47), DR-12 (§6 C1 corrected to :739-743), DR-13 (C3c re-key), DR-29 (D-45), DR-30 (D-46), DR-31 (D-49), DR-32 (D-50), DR-33 (D-51), DR-34 (D-52), DR-35 (D-48), DR-36 (D-47/D-48 + honesty statements). DRs owned by sibling artifacts (DR-4, DR-10, DR-14, DR-15, DR-16, DR-17, DR-18, DR-19, DR-20, DR-21, DR-22, DR-23, DR-24, DR-25, DR-26, DR-27, DR-28) are recorded as owned by those artifacts, not silently adopted.

**Why:** the delta-recheck's 24 conditions must be traceable to a landing site; these two files own the plan-core + decision-record subset.

**Evidence:** delta-recheck.md §12-§14; plan-overview.md §16.

**Displaces:** none (additive).

### D-56 — The BEGIN fingerprint attribution is measured-unique to Site A (P-11)

**Decision:** The `BEGIN` fingerprint attribution is **MEASURED-CONFIRMED, not unsound**. The only `text("BEGIN")` in `daemon/` is `ens_db_tools.py:1479` (DDL) / `:1491` (DML) — Site A. psycopg3 does NOT leave `BEGIN` as `last_query`: autobegin pipelines, leaving the STATEMENT (raw `t2_stat_activity.out:48` — `last_query` = the SELECT after an autobegun statement); every M-R3 iteration's held backend reported `idle in transaction` with `last_query` = the ORM SELECT. Therefore a literal `BEGIN` `last_query` is a **standalone-BEGIN marker**, measured-unique to Site A. The architect's A4-i generic-marker claim is REFUTED (architecture-recommendation.md:63/:154). All four incident fingerprints reproduce byte-exactly (BASELINE.md:355-362; raw t4:46-55). The correction belongs to A4-i, not to the Site-A attribution; R19 is reworded accordingly.

**Why:** the measurement settles the attribution question that the plan's premise ledger left as "CONFIRMED (the site exists and the fingerprint is unique to it)" with an honesty caveat about `application_name` being empty. The baseline proves the site is unique by SQL text AND that psycopg3's autobegin does not produce a literal `BEGIN` `last_query`, so the attribution is measured-sound.

**Evidence:** BASELINE.md:355-398; raw t4:12-18, t2:37-52; plan-overview §3.3 premise row 1; research-phase0-findings.md P-8b.

**Displaces:** the architect's A4-i generic-marker reading (architecture-recommendation.md:63/:154) and R19's "attribution unsound" framing (reworded to "attribution measured-sound — correction belongs to A4-i").

### D-57 — the F4-A capacity-threshold ruling (F4 admission pre-check)

**Decision:** The F4-A admission pre-check compares `checkedout()` against the **LANE-PINNED configured capacity constant** (`pool_size` 5 + `max_overflow` 10 = 15), with a **capacity-equality pin** asserting the constant equals the engine's configured capacity at construction, so config drift REDs. Rejected alternatives: `QueuePool._max_overflow` (private API), `overflow()` (returns CURRENT overflow — a load probe, not an admission check), a hard-coded 15 without the equality pin, and lowering the engine `pool_timeout` (D-15). **Why:** SQLAlchemy 2.0.48 exposes no per-connect acquisition timeout — `Engine.connect()` takes no timeout and the wait is `QueuePool._do_get()` → `self._pool.get(wait, self._timeout)` at pool level. **Evidence:** BASELINE.md:244 + factory.py:323-338. **Canonical home:** phase1-plan.md's D-57 row (the sibling is re-pointing its phase1-plan "D-56" mentions to D-57 in parallel). **Displaces:** nothing — this record RECORDS the dispatcher ruling (no prior decision occupied this slot on the decisions.md side).


### D-58 — asyncpg checkpoint-adapter idle-in-txn hold: ROUTED, attribution in-lane (Δ2)

**Decision:** The asyncpg checkpoint-adapter idle-in-txn hold (`checkpoint_adapter.py:1229-1240`) is **ROUTED to the follow-up DB-pool commission**; this lane owns **attribution only** (D-36/D-49). The follow-up commission adds `server_settings={'idle_in_transaction_session_timeout': <measured>}` to the asyncpg pool's **NEW dedicated DSN** (the same DSN D-49 already mandates — a one-parameter addition there), under the **SAME D-47 enablement gate** (enable iff a trustworthy post-F8 measurement of the adapter's legitimate prune-transaction dwell exists). Do NOT pull the fix into this lane. The routed row is recorded as **R-9** in plan-overview.md §12; §10 #3's disclosure now points at R-9 (disclosure-without-owner is the one shape the plan's routing discipline does not tolerate).

**Why:** the asyncpg pool opens an explicit `async with conn.transaction():` with three awaits between DELETEs and holds `idle in transaction` with no server-side reap (measured T3d: leak-capable, `server_side_reap_observed: false`, cluster default 0); a manager-engine backstop would not cover it anyway (different pool/DSN). Attribution (D-36/D-49) is in-lane; the hold needs its own owner and enablement gate, mirroring D-47's disposition for the manager-engine bound.

**Evidence:** architecture-delta.md §③ Δ2 + Q3's compensating-control table; BASELINE.md:306-323 (P-8b-extra); plan-overview.md §10 #3 + §12 R-9.

**Displaces:** the "attribution only, no owner" gap in §10 #3 (disclosure-without-owner).

### D-59 — F4-A capacity constant home pinned to `daemon/services/readiness.py` (Δ6)

**Decision:** The F4-A capacity constant's home is **pinned to `daemon/services/readiness.py` (module-level)** — D-57 permitted either home, the architect rules readiness.py. `constants.py` is NOT the home for this constant. **Conditional note:** if a future implementer prefers `constants.py`, plan-overview.md §6 C5's coupling matrix needs a **one-row Phase-1 amendment for `constants.py` FIRST** — as written the matrix gives `constants.py` to Phase-2 F2 only, and an unamended Phase-1 addition there violates the lane's coupling discipline (recorded as A6 in plan-overview.md §9).

**Why:** coupling discipline — the matrix is the authority on which phase owns which shared surface; an unamended addition would silently break it. The conditional keeps the door open without breaking the rule.

**Evidence:** architecture-delta.md §③ Δ6; plan-overview.md §9 A6; phase1-plan.md D-57 row + T1.4 + AC4.

**Displaces:** the "readiness.py (or constants.py)" permissiveness in phase1-plan.md D-57/T1.4/AC4.

---

## 14. Round-3 additions (D-60+) — §7.3 re-baseline records (2026-10-29)

### D-60 — the checkpoint lane's borrow delta: census re-baseline D 148→150 / E 14→15 (total 781→784) + the F2-j oracle's prepare_threshold re-pin

**Decision:** The §7.3 re-run on the merged tree (`4c8d2f105` — `latest`, which carries the checkpoint-read-path lane's landing) produced **D=150 / E=15 / total 784** against this lane's baseline **D=148 / E=14 / total 781**. The delta is **CODE DRIFT, not scanner drift** — the materialised instrument `scripts/audit/borrow_census.py` is UNCHANGED (byte-for-byte) by this re-baseline, so §5.3's contract ("the scanner definition is the contract, not the aggregate number") still holds and the BASELINE, not the scanner, is what moved. Classification table of the three delta sites (every site classified individually, per §7.3 step 1):

| Site (file:line @ `4c8d2f105`) | Family | Verdict | Reason (one line) |
|---|---|---|---|
| `daemon/services/checkpoint_prune.py:493` (`_analyze_one_table`) | **D** | **CLEAN** | `with engine.connect()` on the DEDICATED per-cycle autocommit engine from `create_vacuum_engine` (`daemon/services/vacuum_engine.py:54-88`) — a **NullPool** engine carrying the bounded `statement_timeout` / `lock_timeout` / `idle_in_transaction_session_timeout` triple (`checkpoint_prune.py:574-580`); `with` form ⇒ close-guaranteed by construction; engine `dispose()`-d in the caller's `finally` (:608-617); deliberately NOT the hot psycopg saver pool and NOT the adapter's shared asyncpg pool (:434-439) |
| `daemon/services/checkpoint_prune.py:693` (`_collect_blob_bytes_sync`) | **D** | **CLEAN** | Same dedicated NullPool per-cycle engine and the same bounded timeout triple (:749-755); `with` form ⇒ close-guaranteed by construction; engine `dispose()`-d in the caller's `finally` (:784-791); connection function-local, only fetched rows leave it |
| `daemon/checkpoint_adapter.py:1458` (`PostgresCheckpointerAdapter.has_checkpoint`) | **E** | **CLEAN** | `async with self._pool.acquire()` — the `EXISTS` probe that moved the retry-path existence check OFF the saver's single `asyncio.Lock` (checkpoint-read-path plan Track-1 item 2, commit `d9ece2fee`); an `async with` form ⇒ returns to the pool by construction, exactly like the 12 sibling `self._pool.acquire()` sites already in this file; it opens **NO explicit transaction** (unlike the sibling `_delete_blobs_anti_join_chunk`, which wraps its work in `conn.transaction()`) ⇒ it contributes **no** idle-in-transaction hold — the adapter pool's transaction-shape idle-in-txn item is the routed R-9 (D-58), not this site |

Families **A**, **B**, **C** and **F** are UNCHANGED (438 / 175 / 4 / 2) — the checkpoint lane added no session borrow, no manual borrow and no raw-psycopg borrow ⇒ **0 LEAK, 0 unclosed** on the merged tree; the audit conclusion is unchanged.

**Re-baseline (with the explicit note §7.3 step 4 requires):** `tests/unit/tools/test_dbpool_census_families.py` — `AST_BASELINE` **D 148→150, E 14→15**; `AST_BASELINE_TOTAL` **781→784**; and a NEW named-site leg (`REBASELINED_DELTA_SITES` + `_per_site_counts`, which re-derives the D/E per-site map THROUGH the instrument's own `_is_connect_call` / `_is_acquire_call` predicates — never a second, competing definition of the family) pins the three sites by (path, ENCLOSING QUALIFIED FUNCTION), path-and-function keyed rather than line-keyed so upstream line drift cannot hide a change. The **drift rule STANDS**: any FOURTH borrow site in `daemon/` still REDs the family counts until individually audited. Pack collected count UNCHANGED (5) — the re-baseline EXTENDS the existing oracle in place.

**The F2-j oracle (a knob baseline, not a census baseline — recorded here because it is the same re-baseline discipline):** the P2-saver-pool oracle `tests/unit/tools/test_dbpool_p2_immutability.py::test_saver_pool_knobs_are_unchanged` was RED because the checkpoint lane changed ONE saver-pool knob — `daemon/persistence.py:412`, `"prepare_threshold": None` (was `0`). Classified **DELIBERATE / DOCUMENTED** (code drift; no scanner involved): psycopg3 `None` **DISABLES prepared statements entirely** — every execution is planned FRESH by the server (a custom plan), so the server can never serve a cached GENERIC plan (the mechanism the read-path audit attributed the 8.56 ms-vs-0.064 ms regression class to; `0` means "prepare IMMEDIATELY on the first execution", which leaves the statement in the per-connection statement cache where PG may serve a generic plan after the 5-execution threshold). Provenance: `dcfcbba82` (checkpoint-read-path plan Track-1 item 3) + the merge `e356d3657` (EXPLAIN parity: 0 non-timing EXPLAIN leaf differences across 10 EXPLAINs per leg, both arms the SAME plan; 25-round latency matrix). Measured cost: **+0.229…+0.254 ms per read, 95 % CI [+0.220, +0.261]**, 4 repetitions with varied leg order — load-dependent (scales with how much read time is planning), and the pool keeps its exclusive per-connection checkout either way. The pin is **NOT weakened into a no-op**: every other knob keeps its lane-pinned value; the knob **SET** is now asserted EXACTLY (`SAVER_POOL_KNOB_SET` — a leg the lane's oracle never had, so a silently ADDED or REMOVED pool knob is now a loud failure); the connection-`kwargs` dict is still exact dict equality; and the ONE deliberate divergence is asserted **by name** (`SAVER_PREPARE_THRESHOLD`), not deleted and not folded into a looser assertion. Pack collected count UNCHANGED (6).

**Why:** §7.3 forbids a silent re-baseline; both deltas are classified, named, and re-baselined with the change called out. The three borrow sites are all `with` / `async with` forms ⇒ they are close-guaranteed by construction, so the census's audit conclusion (0 LEAK, 0 unclosed) is unaffected by the re-baseline — the numbers moved because code moved, not because the audit got looser.

**Evidence:** scanner output at `4c8d2f105` (certified interpreter, read-only): `A  with Session(...) / WriteGuardSession(Session(...)): 438` · `B  with SQLModelSession(...): 175` · `C  non-with session assignment (= Session(...)): 4` · `D  with engine.connect() / engine.begin(): 150` · `E  async with pool.acquire(): 15` · `F  raw psycopg / raw_connection / getconn / putconn: 2` · `TOTAL borrow sites: 784` · `docstring N/A notes: 13` · family-C detail unchanged at the same four named sites (`daemon/manager.py:2794`, `daemon/repositories/auth_bootstrap/repository.py:161`, `…:93`, `daemon/services/child_reports.py:2594` — the C-family pin is (PATH, STATEMENT)-keyed per NOTE-6, so the +12-line shift in `daemon/manager.py` (2782→2794) does not redden it). Same instrument at the pre-lane base `19fc11c8b`: A=438 / B=175 / C=4 / D=148 / E=14 / F=1, total 780 ⇒ the delta is exactly this table's three sites (F was moved to 2 by NOTE-7's WS-B record, which this record does not touch). Mutation-proven: (1) a planted `daemon/zzz_borrow_plant_check.py` borrow site ⇒ `family D drifted — baseline 150, observed 151` (removed after the proof); (2) a distribution shift (a second `engine.connect()` planted inside `_analyze_one_table`, the `_collect_blob_bytes_sync` borrow removed — totals UNCHANGED at 150) ⇒ the named-site leg REDs `_analyze_one_table drifted — expected 1 audited site(s), observed 2`; the tree was restored byte-identical (empty `git diff`) after both proofs. Post-edit: census pack 5/5 GREEN, P2-immutability pack 6/6 GREEN.

**Displaces:** the §7.2 diff-gate assertion "the SAME fixed instrument run at base `19fc11c8b` yields the IDENTICAL numbers ⇒ the lane added ZERO borrow sites" — that sentence held only while HEAD stayed inside this lane's own delta. The gate itself is unchanged; the baseline it diffs against is now the MERGED reality named above. No D-record in §1-§12 is renumbered or amended; NOTE-7's family-F record stands untouched.

### D-60 addendum — the 2 pre-existing `test_instance_tools.py` failures classified (unrelated to this lane)

**Decision:** One line each, both **PRE-EXISTING / ENVIRONMENTAL (not this lane, not any of the four surrounding commits)**: `tests/unit/tools/test_instance_tools.py::TestRegistration::test_planner_and_tester_resolve_subtree_messages_via_registry` and `::TestSubtreeStatusRegistration::test_leader_planner_tester_opt_in_via_production_seam` — both pin `agents/planner/meta.json`, whose blob `3120709f36b2` is **byte-identical across all four commits** (`19fc11c8b` pre-lane base, `9bbef71c1`, `396350bdf` this lane's merge, `f05d8e599` latest), so neither the knob nor any sibling lane moved what they assert; the failures are deterministic-on-HEAD drift between those pins and the live tool-registry seam, present at every commit equally.

**Why:** the rider's classification duty — a red that is provably identical across the lane's base AND its merge AND two neighboring tips cannot be attributed to the lane; recording it here keeps the lane's pack verdict honest without touching code.

**Evidence:** `git rev-parse <commit>:agents/planner/meta.json` = `3120709f36b2` for all four commits (read-only, this worktree @ `f05d8e599`); scrubbed pytest run `tests/unit/tools/test_instance_tools.py` → `2 failed, 207 passed` with exactly the two node IDs above.

**Amended (read-cache PR-1 fix pass):** the addendum's verification scope was incomplete — its scrubbed runs covered `test_instance_tools.py` only, while the read-cache PR's own pack had **omitted** `tests/unit/tools/test_saver_probe_contention_class.py` entirely. The honest red census at the PR's HEAD (`ada12ffb9`) across the known-red read-path files was **12 = 7 pre-existing at base** (`f05d8e599`: 6 × `tests/test_readiness_checkpoint_saver.py` probe-shape drift + 1 × `tests/integration/test_armed_absence_alist.py` alist allowlist) **+ 5 NEW from the PR** (the contention-class `_FakeSaver.aget_tuple(config)` double raising `TypeError` on the sentinel's new `cache_bypass` kwarg — A/B-proven by the tester gate: base `f05d8e599` 8 passed / HEAD 5 failed + 3 passed). All 5 were fixed in the fix pass (C1, commit `5d1bc2c96` — file back to 8/8 green). The two `test_instance_tools.py` classifications above are UNCHANGED by this amendment.

**Displaces:** the addendum's original implication that the PR's pack exhausted the read-path red surface (it did not — 5 of the 12 HEAD reds were invisible to the PR's own runs); nothing else in this record moves.
