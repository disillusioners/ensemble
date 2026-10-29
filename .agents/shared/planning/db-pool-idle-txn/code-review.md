# Code Review — db-pool idle-in-txn implementation (the code gate)

**VERDICT: ✅ APPROVE-WITH-ADVISORIES** — with a required small fix-cycle (6 warnings) before the tester gate. 0 CRITICAL. No C1–C4 violation, no mutation-dead pin, no false-green gate, no untraced hunk.

- **Object:** the implementation, `cc5bcd6fc..a393ed87d` on `fix/db-pool-idle-txn` (HEAD `a393ed87d`, base `19fc11c8b`): 48 files +8033/−238 — **11 daemon files (exactly the permitted surface), 30 test/script files, 0 new `.sql`** — plus 2 docs-only amendment commits.
- **Review lanes:** a 2-councilor code-review council (governor `0d57ceff`, perspectives split P1+P2/P3+P4 — the deployment's canonical-model dedup rule caps the council at 2) · a run-verification worker (`715ef002`) · a scope-trace worker (`db092e5e`) · the dispatcher's own verification. Every blocking-class item was verified **personally by the dispatcher**, not taken from the dev report.
- **Date:** 2026-10-29.

---

## 1. Findings

### 🔴 CRITICAL — none

No contract deviation uncovered by a ruling that touches a safety property; no C1–C4 violation; no pin in the sample that cannot fail its specified mutation; no false-green gate.

### 🟡 WARNING — 6 (each with the exact required change; land before the tester gate)

**W-1 — Degenerate-path content delta in the F8 fetch helpers** *(three-way converged: council C-1/F-2 · scope-trace F-4 · dispatcher's own read)*
**WHERE** `daemon/manager.py:10953` (sync caller) / `:11743` (async caller) — `child_agent_id=child_agent_id or "agent"` — with the helper guards at `:11314`/`:11371` (`if child_agent_id:`).
**WHAT** At base the helpers did `child_row = session.get(Instance, …); if child_row is not None:` — a **missing child Instance row skipped the content fetch** (content stayed `""`). At HEAD the Instance read moved to P0 and the caller passes `child_agent_id or "agent"`; the helper's truthy guard means a **deleted/missing child row now performs a checkpoint fetch base skipped**, fetching under the placeholder agent id — a mislabeled `agent, below is…` content prefix and a possibly-wasted downstream LLM repair call, in the exact path this lane makes cheap. The other two cases (row present + real agent_id; row present + NULL agent_id) are byte-equivalent to base. **Unpinned** (the F8 pack stubs the helper wholesale).
**WHY IT MATTERS** This is **lane-introduced** (created by F8.2's restructuring), disclosed by the developer as a residual — but the dispatch framed the disclosed residuals as *pre-existing/base*, and this one is neither. It is therefore a behavior delta not covered by a ruling.
**REQUIRED CHANGE** `row-missing ⇒ pass None` (helper signature `str | None`, keep the `if child_agent_id:` guard); the `"agent"` fallback applies **only** to row-present/NULL-agent-id. Add a pin: seed a reconcile with a **missing** child Instance row ⇒ the fetch is skipped (content `""`), matching base.

**W-2 — Split `_write_guard` re-acquisition can raise a new RuntimeError** *(council C-1/F-1)*
**WHERE** `daemon/manager.py:10870`+`:10956` (sync), `:11650`+`:11755` (async).
**WHAT** Base held `_write_guard` **once** around the whole reconcile, so a `pause_writes()` arriving mid-reconcile could never raise. HEAD's P0/P1/P2 split re-acquires the guard at P2; a `pause_writes()` landing in the P1 window raises a **new** `RuntimeError` base could not produce. **Latent today** — `pause_writes` has no in-repo production caller — but the operator-drain path is outside the repo.
**REQUIRED CHANGE** Restore base parity: **one** `_write_guard` around the whole body (P0+P1+P2) with the **sessions** still split — the transaction split is what F8 requires, not the guard split. (Pin-safe: the F8-a static pin scopes to session-scope bodies, not guard extents.)

**W-3 — Ladder row-id drift: two non-canonical gate IDs** *(council C-2/W-1 · run-lane finding 1)*
**WHERE** `tests/packs/dbpool_gate_ladder.sh:202` (`G2.5b`), `:211` (`G3.2b`) vs the contract §6.2a frozen 17.
**WHAT** The two b-rows are marker-split executions of canonical rows (not a false green), but their IDs appear **nowhere** in the planning set, and the W-11 scanner reads only the 8 `.md` artifacts — not the ladder script — so **no pin can see this drift**. Meanwhile the canonical `G1.5`/`G3.5` are documented-only/tester-owned.
**REQUIRED CHANGE** Either fold both invocations under their canonical IDs (marker recorded in the row's label column), or amend §6.2a to name the marker-split rows explicitly; **and** add a pin asserting the ladder's executable row-id set ⊆ the contract's canonical set.

**W-4 — The `60_000` literal freeze will spuriously RED at the contracted enablement ceremony** *(council C-2/W-2 · run-lane finding 3)*
**WHERE** `tests/unit/tools/test_dbpool_honesty_blocks.py:133` — `assert MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS == 60_000`.
**WHAT** DR-20's letter is scoped to F2-c/F2-e, and this pin lives outside it — but it commits DR-20's spirit on a constant that ships **DEFINED BUT NOT APPLIED** (D-47). The contracted enablement is a **one-edit** ceremony; this pin turns that edit into a test-failure event.
**REQUIRED CHANGE** Restate structurally: assert the constant exists and is typed, **and** that the rendered manager options **omit** the `idle_in_transaction_session_timeout` clause while `MANAGER_ENGINE_IDLE_IN_TXN_BACKSTOP_ENABLED` is False — so the enablement edit cannot land without deliberately updating this pin. Land **before** any future enablement ceremony.

**W-5 — The ladder's `--run` mode discards pytest's exit status** *(council C-2/W-3)*
**WHERE** `tests/packs/dbpool_gate_ladder.sh:128-137`.
**WHAT** `--run` pipes pytest through a tail/grep; an **errors-only** outcome (no `FAILED` line in the last 3) prints RUN-PASS. The collect-mode fence is authoritative, so this is not a false green in the gate sense — but the convenience mode can mislead.
**REQUIRED CHANGE** Capture the pipeline's exit status (`PIPESTATUS`) or add `[0-9]+ error` to the failure grep.

**W-6 — R-1 is the critical path: no ladder row can pass until the tester lands the base measurements** *(run-lane finding 2 — scheduling, not defect)*
**WHERE** `.agents/shared/planning/db-pool-idle-txn/expected-collected.md` — **all 35 cells are `TBD (R-1)`**.
**WHAT** Verified **live** (single row, no pytest executed): the ladder FATALs at the count lookup with "TBD (R-1) … the gate cannot assert its count and therefore cannot pass (Δ1/DR-23)". This is the designed Δ1 behavior — but it makes the tester's R-1 measurement the **blocking dependency** for every promote-time gate.
**REQUIRED CHANGE** None to the artifact. **Escalate R-1** as the critical path; the run-lane's HEAD-collected counts must **not** be substituted (R-1 must measure at base `19fc11c8b`).

### 🟢 ADVISORY — 12

- **A-1** `manager.py` — the F8-a static matcher misses `Session(self._engine)`-shaped scopes; **0 live violations** (AST-scan clean across `daemon/`); widen the matcher when convenient.
- **A-2** `manager.py` — the split adds ~3 extra SELECTs per reconcile on the sweep hot path (the P0 read + the P2 re-read + the guard re-asserts). Accepted cost of the double-delivery closure; note it in the release composition.
- **A-3** `readiness.py` — `_refuse_pool_admission_at_capacity` assumes a QueuePool shape (`pool.checkedout()` unguarded). Unreachable today (the manager engine is always QueuePool); harden with `getattr` if reused.
- **A-4** The F8-b census leg's sample ordering can skew (rare false-RED, <1 %/run) — fix by same-instant reads when convenient.
- **A-5** The F8-d PG pin proves co-existence, not atomicity; atomicity rests on the static twin (hand-evaluated — it holds). Note in the pin's docstring.
- **A-6** `factory.py:387` — the boot log says "no **trustworthy** Phase-0 measurement" vs the contract's literal; the pin agrees with the code, so align **one** of the two docs.
- **A-7** The ladder's END-marker regex uses `[^n]*` where `[^\n]*` is meant — fail-closed direction; fix trivially.
- **A-8** The F7-b `== 0` post-condition at `tests/integration/test_db_pool_idle_in_transaction_guard.py:577` was adjudicated **NOT** the C-10 class (a scratch-DB post-condition the test fully owns; its specified mutation makes it RED). Recorded so the class is not re-litigated.
- **A-9** Three **pre-existing** packs were amended outside §5's inventory (`test_chart_image_delivery_e2e.py` seal re-pin — which **reduced** seal mismatches 2→1; `test_connect_timeout.py`; `test_checkpoint_pool_config.py`) — each a forced, in-line-labeled consequence of a permitted change; recorded in-commit and in the release composition.
- **A-10** F6's loser-path fallback may return a **settled** epoch (the disclosed belt) — flag to the mission-records owner.
- **A-11** `db_capture.py:300` — the `_read_executing_turns` docstring still says ``tasks`` rows while the SQL now reads `FROM task t`. F5.3 forbids touching it in-lane; route to the wedge-capture lane (R-10's owner).
- **A-12** The ladder executes **15/17** rows (`G1.5`/`G3.5` are tester-owned whole-tree legs) — state the executability plainly in the release composition.

### 🚫 CLAIMS CHECKED AND REFUTED (the addendum nudge) — recorded so the record shows they were checked, not ignored

- **R-N1 — "the checkpoint-pool knobs are still inert (not in MANAGED_KEYS)" → REFUTED / OUT-OF-SCOPE.** `MANAGED_KEYS` (`daemon/services/settings_service.py:180`) **already contains `limits.checkpoint_pool_max_size`** at base and at HEAD (identical key lists, with `limits.llm_concurrency`/`worker_pool_size`/`chat_worker_pool_size`). The lane's own constants are deliberately module-level **code constants** per D-47/D-59/Δ3 — the house's own convention note is `boot_subprocess_sweep.py:63` ("deliberately NOT app_settings"). The asyncpg `server_settings` is **attribution**, not a tunable knob, and the asyncpg hold is routed as **R-9** (D-58/Δ2 leader ruling). **No wiring fix is owed**; any such change goes through the normal gates regardless.
- **R-N2 — "F4-A pins a hard-coded 15 that could drift at the merge" → REFUTED, reconciliation-safe by pin.** `MANAGER_POOL_CONFIGURED_CAPACITY: int = 15` is hard-coded module-level at `readiness.py:152` **per D-59's ruling** (deliberately not the private `_max_overflow`, not `constants.py`), and D-57 required a capacity-equality pin — which **exists** at `tests/unit/tools/test_dbpool_probe_bound.py:195-222`: it AST-parses `create_postgres_engine`, extracts the **actual** `pool_size`/`max_overflow` kwargs, and asserts `MANAGER_POOL_CONFIGURED_CAPACITY == pool_size + max_overflow` with the drift message. If another lane changes sizing at the merge, **the pin REDs**. Exactly D-57's design.

---

## 2. The dispatcher's personal verification log

### DO-NOT-TOUCH surfaces (all byte-identical, anchored hashes at base vs HEAD)

| Surface | Method | Result |
|---|---|---|
| `_evaluate_leak` (159L) · `_checkedout_rising` (25L) · `observe` (94L) · `_guarded` · `resolve_psycopg_pool` · `sample_sqlalchemy_pool_state` · `sample_psycopg_pool_state` | anchored extraction + sha256 | **IDENTICAL** (an initial sed-range check "differed" purely from **line drift**; the 6-hunk diff + anchored hashes prove it) |
| `GRACE_ELIGIBLE_FAILURE_CLASSES` · the 4 grace/leak constants in `constants.py` | block extraction + grep | **IDENTICAL** |
| `daemon/api.py` · `scripts/upgrade/` | `git diff --stat` | **EMPTY** (untouched) |
| `PG_SESSION_CONNECT_ARGS` | line sha at `factory.py:258` | **IDENTICAL**; a new separate `MANAGER_PG_SESSION_CONNECT_ARGS` at `:298` |
| The repair engine's `-c options` literal | extracted + diffed | **BYTE-IDENTICAL**, with `application_name` as its **own connect_args key** (D-49 conninfo channel) |
| The shared `conn_string` in `persistence.py` | scope-trace recomputation | **BYTE-IDENTICAL**; the asyncpg pool now takes `adapter_pool_dsn` |
| Pool sizing | grep | `pool_size=5` / `max_overflow=10` at `factory.py:394-395` |

### Safety-critical paths (read by me)

- **F8 P2** (`manager.py:11756+`): the write scope **re-reads `inj`** (F8.7, gone-at-P2 skip), **re-asserts the terminal guard** ("escalated to terminal between P0 and P2; skipping") and the **dead-parent guard** against the re-read row, and re-derives sub-shape (a). The P1 fetch is wrapped in `asyncio.wait_for(…, timeout=8.0)` — **the previously-unbounded async hold is now bounded**. Fetch helpers take `child_agent_id`, not `session=`. The strict-False carrier consumer is at **`:11212` (sync) / `:11999` (async)**, computed outside the scope.
- **F4-A** (`readiness.py`): `_refuse_pool_admission_at_capacity(engine)` fires at `:1547` and `:1839` — **immediately before** `engine.connect()` at `:1548`/`:1840`, in both probes. The raise lands on `_guarded`'s **unchanged** `except TimeoutError → FAILURE_CLASS_PROBE_TIMEOUT` (I read the seam; Python 3.11+ `asyncio.TimeoutError` *is* builtin `TimeoutError`, so the propagation is sound). **No data path from the pre-check into `_evaluate_leak`** (which reads P2 oracle state, not probe outcomes). No conditionality near any rollback path.
- **F4-B**: `SET LOCAL statement_timeout` inside the connection scope (`:1556`/`:1848`, dialect-guarded) + `conn.rollback()` (`:1564`/`:1881`); value from the one `_probe_statement_timeout_ms` helper = 2× the client budget.
- **F3**: `grep 'text("BEGIN")' daemon/` → **0**; `finally:` rollbacks at `:1089/:1520/:1544`.
- **F9.1**: my own count of non-comment/non-blank delta lines in `queues.py` → **0** (the scope-trace additionally proved `ast.dump` identical across revisions).
- **F6**: the upsert `on_conflict_do_nothing(index_where=text(OPEN_EPOCH_STATUS_SQL))` + `.returning(mission_id)` loser signal (rowcount unusable — psycopg3 reports −1 — documented) + the anchor-scoped re-resolve; the caller-session path unchanged (D-10).
- **Honesty amendments**: verified at **8 sites** — "PARTIAL — one of TWO roots", "SC7 is PARTIALLY met", and R-10 discloses the census as **BORN-BROKEN, PRE-EXISTING AT BASE**. The scope-trace independently confirmed the census executes **between** the two F5-owned reads, so the qualification matches the tree. **No overclaiming.**

### Runs (certified interpreter, CWD = the worktree, full 6-var scrub + `mktemp` HOME)

| Run | Result |
|---|---|
| W-11 consistency pin | **7 collected / 6 passed / exactly 1 xfailed** — the xfail is the **AC-id cross-artifact canary** (`strict=True`), not the ladder enumeration (which passes); the pack, the plan, and `expected-collected.md` agree with each other — only the dispatch brief's parenthetical was imprecise |
| txn-hygiene (F3) | **9/9** |
| **All 17 dbpool unit packs + `test_pg_engine_utc_session.py`** | **110 tests: 109 passed, 1 xfailed, 0 failed, 0 errors** — including the amended utc-session pin **10/10** |
| Δ1 fence, three ways | `TBD` → **FATAL exit 1** · correct count → `COLLECTED-OK exit 0` · wrong count → **FATAL exit 1** — integer-validation **before** the comparison, confirmed in the shipped script (`:21-22` guard, `:31-34` compare) |
| Ladder TBD design-FATAL | proven **live** on a single row: exit 1 at the count lookup, **no pytest executed** |
| `count==0` idle-in-txn sweep | **none** (all 4 grep hits are sentinel-count/comment/docstring) |
| `60000` sweep | 1 live assertion (W-4) + 1 prose reference |

*(Env-fence disclosure: the run-lane found ambient `POSTGRES_DB=ensemble_prod` in its shell and re-executed everything under the full scrub — byte-identical counts. Good discipline; it also proves why DR-16's fence matters.)*

### Scope trace (worker, corroborated by my file-level check)

**11/11 daemon files map to their permitting clauses; 55/55 hunks traced, 0 untraced; `queues.py` comment-only AST-proven; `db_capture.py` exactly 3 changes; SQL surface empty (0 `.sql`, 0 ORM table/column/index); both base-debt items reproduce byte-identically at `19fc11c8b`; the seal re-pin REDUCED mismatches 2→1.**

---

## 3. Verdict rationale

**APPROVE-WITH-ADVISORIES.** The implementation matches the binding contract on every clause: the F8 split shipped exactly as ratified (including the dispatcher-ratified strict-False carrier consumer and the previously-unbounded async hold now bounded at 8.0 s), F4-A/B are C1/C2-clean with the classification seam byte-identical, F3/F5/F6/F2/F9.1 are contract-faithful, the Δ1 fence is live and proven fail-closed in both directions, the scope is exactly the permitted surface with zero untraced hunks, and no pin in a 3-lane sample is mutation-dead.

**Conditions of the approval:**
1. **A small fix-cycle for W-1…W-5 before the tester gate.** W-1 is the most behavior-real (a mislabeled report prefix + a wasted LLM call in the exact path this lane makes cheap) and the only **lane-introduced** uncovered delta; W-2 is a one-guard restack; W-3/W-4/W-5 are script/pin edits (W-4 must land before any future enablement ceremony).
2. **W-6 is the scheduling critical path** — the tester's R-1 base measurements gate every ladder row; nothing else does.
3. **The PG-leg gap is the standing caveat**: this review is **static + unit-execution grade**. All 6 PG/integration packs were read end-to-end and adjudicated statically (all mutation-meaningful), but **no RED/GREEN was observed on real PG**. That dimension transfers to the tester instrument unverified — by design, not by omission.

**Merge readiness:** scope ✅ · hunk trace ✅ · comment-only proof ✅ · byte-identity ✅ · DO-NOT-TOUCH ✅ · SQL ✅ · unit execution ✅ · fence ✅ · honesty ✅ · base debt ✅ · the two addendum claims checked and refuted ✅ — **W-1 and W-2 adjudicated/fixed** ⬜ (the two items above), runtime PG ⬜ (tester).

---

*Produced by the reviewer (dispatcher/controller). Council: governor `0d57ceff-7fe0-4b95-a0dd-110a3634335f`, councilors `e62d77c4` (P1+P2) and `01d415ca` (P3+P4) — unanimous APPROVE-WITH-ADVISORIES, 0/5/11. Workers: `715ef002` (runs), `db092e5e` (scope). The deployment's canonical-model dedup rule fielded 2 councilors (one per model) covering all 4 perspectives; the halves corroborate on every shared surface. Read-only review: no source file was modified, no daemon run, no production database contacted.*
