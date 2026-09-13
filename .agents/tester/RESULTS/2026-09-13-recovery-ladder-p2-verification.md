# Verification Gate — Hallucination-Recovery-Ladder PHASE 2 (ghost + truncated + empty_post_ladder; master-flag only per ADR-0009)

**Date:** 2026-09-13
**Branch:** `feature/hallucination-recovery-ladder-p2` — verified range `d658bf06` (base, latest merge) → `f1d976e3` (P2 impl) → `21e54bd1` (C1 fix) → `aaa1da21` (tidy); gate-added test-only commits through `0a9c9a79` + quick-fix commits (§7)
**Worktrees:** `agents-ensemble-wt-recovery-ladder` (branch) + throwaway detached `agents-ensemble-wt-rlp2-head` @ `aaa1da21` + `agents-ensemble-wt-rlp2-base` @ `d658bf06` (attribution legs)
**Verdict:** ❌ **NOT READY — 1 blocking product defect (D-1: ghost repair-budget never accumulates in natural runs → exhaustion backstop unreachable).** Everything else PASSES: master-OFF byte-identity ×4 classes, all three repair rungs (mechanism), restart durability on BOTH engines (incl. byte-exact `excerpt=` through REAL restart), reviewer-routed gaps closed with committed tests, full-suite attribution 0 OTHER branch-caused reds, ensure.md Core 4/4, PG parity idempotent, FE untouched. Fix D-1 (production, small — leader routes), re-run ghost exhaustion arm, then merge.

---

## 1. Scope Decision

Full verification requested and warranted: 8 files +3370/−55 over base — `daemon/graph.py` +1264 (new detectors/helpers, pre-terminal intercept, ghost node/carrier), `daemon/services/symptom_repair_engine.py` +337 (3 new preset rows + selectors + doc builders), new branch test file `tests/unit/test_symptom_repair_engine_phase2.py` (+1622) plus edits to 4 sibling test files. Cross-cutting: shared carrier/budget/OQ5 machinery. **Zero `frontend/` changes** (verbatim-empty diff `d658bf06..aaa1da21 -- frontend/`, verified twice).

ADR-0009 (USER AMENDMENT): NO per-class sub-flags. `ENSEMBLE_REPAIR_GHOST_PROMISE` / `_TRUNCATED` / `_EMPTY_POST_LADDER` grep across `daemon/` = **zero hits** (verified). Master `ENSEMBLE_SYMPTOM_REPAIR_LADDER` (default ON, resolver `config.py:2436`, ValueError-on-invalid) governs all 4 classes.

## 2. Priority 1 — Symptom Closure

### a. Ghost-promise — mechanism ✅ PASS / reachability ❌ DEFECT D-1
- **Storm→repair→continue ✅** (probe `/tmp/rlp2-ghost/probe_ghost.py`, real graph, counting provider): 3 identical trailing-colon ghosts → repair at cap `GHOST_PROMISE_REINVOKE_CAP=3` (sentinel-first, original-id retention) → provider changes approach → turn completes, **burn 2 ≤ 5**, no re-trip next turn. Branch ghost tests 22/22.
- **FP arm ✅**: legitimate colon-ending content (code-block/list intros) below cap → NO repair, budget unchanged.
- **Exhaustion terminal — mechanism ✅ (pre-seeded budget)**: `messages[-1]` = normal AIMessage `repair-terminal-ghost-<uuid>` ("[GHOST TERMINATION]…"), **zero post-exhaustion LLM calls** (counter frozen), carrier `pending_repair_ghost_terminal` cleared, telemetry `phase=terminal`.
- **🐞 D-1 (BLOCKING, 2 independent repros)**: `_build_surgery` order `[sentinel, *hoisted, doc, *retained]` leaves the ORIGINAL real HumanMessage at `messages[-1]` after ghost surgery (ghost class has no tool-pairing evidence tail); the `agent_repair_ghost → agent` re-entry's OQ5 `real_human_boundary` predicate misreads it as a NEW turn → **budget reset 1→0 mid-turn** (astream trace: ghost node returns `budget=1` → channel `1` → next agent entry `0`). Consequences: `SYMPTOM_REPAIR_BUDGET=3` exhaustion unreachable naturally → C1 carrier + `[GHOST TERMINATION]` cycle-kill = **dead code in natural runs**; persistent ghost storms burn unbounded (12-ghost run: 13 provider calls, 4 repairs, no terminal; sole bound = `recursion_limit=300`, uncaught `GraphRecursionError` class). Loop class immune (ToolMessage tail); truncated/empty immune (+1 budget explicitly carried at graph.py:7477; verified live). Sibling: same root misfires watchover `is_turn_boundary` reset mid-turn. Branch tests missed it because exhaustion tests pre-seed `repair_budget_used: 3` (test_symptom_repair_engine_phase2.py:1325). Repro: `/tmp/rlp2-masteroff/{probe_off.py,debug_trace.py}` + `/tmp/rlp2-crossclass/{probe_p9.py#3c,debug_3c_b.py}`. Fix directions (fixer's call): (a) doc-after-tail surgery order for the ghost shape; (b) OQ5 reset requires boundary-HumanMessage id NEW vs pre-entry history; (c) suppress reset on agent entry immediately after a repair-node superstep.

### b. Restart durability ×3 NEW classes — ✅ PASS both engines (house bar = T-2b)
- **SQLite TRUE-restart** (`/tmp/rlp2-restart/probe_restart.py`; fresh aiosqlite connection + fresh `AsyncSqliteSaver` + fresh compiled graph on SAME file, RAM gone): 3/3 classes × 8 assertions = **24/24** — degenerate evidence not replayed, original ids retained, `repair_budget_used==1` persisted, **truncated `excerpt=` byte-exact through the REAL restart** (upgrades the pre-existing dict-roundtrip test), no re-trip per class, P1 LoopDetector cross-clean, repair-doc ids `repair-{iid}-{seq}`.
- **PostgreSQL** (`/tmp/rlp2-pg/pg_canaries.py`, real `AsyncPostgresSaver.from_conn_string`, restart = new compiled graph over same saver per P1 PG-canary shape): 3/3 classes **PASS ×2 idempotent runs** (UUID-isolated); excerpt byte-exact on PG too. Existing `ladder_pg_test` pack (loop class) 1/1 ×2 idempotent. Cluster torn down (server stopped, DBs dropped, port 15432 freed, pgdata removed).
- Coverage note: `tests/postgres/` covers loop only — new-class PG coverage is gate-canary-only (follow-up commit candidate, mirrors P1's T-2b gate commit).

### c. Truncated — ✅ PASS 3/3 arms
- **Placement**: retries exhaust (`PRIMARY_TRANSIENT_MAX=3`, `llm_error_classifier.py:505`) BEFORE `_maybe_pre_terminal_repair` (graph.py:2533) fires; repair = later-superstep state transformation. **C-3/D-3 source pin**: `git diff d658bf06..aaa1da21 -- daemon/llm_error_classifier.py daemon/response_validation.py` = **EMPTY** (raise-in-retry-scope contract unchanged; intercept lives in graph.py only).
- **Second-exception path**: symptom-persists-after-repair → shipped loud ERROR **byte-identical** OFF-vs-ON (`[LLM] All retries exhausted (transient, transient_attempts=3, timeout_attempts=1): LLMResponseValidationError: Response was truncated (finish_reason=length)`).
- **Excerpt invariant**: `excerpt=<original verbatim>` byte-exact incl. `[PARTIAL-MARKER-X7Q9Z]`; no summarizer hallucination. By-design note: `surgery_prefix` dropped on second-exception return (graph.py:2778).

### d. Empty-post-ladder — ✅ PASS 6/6 arms
- **Burn**: observed 2 ≤ cap 5 (`PRIMARY_TRANSIENT_MAX(3)+repair(1)+1`; config-dependent ceiling, formula pinned).
- **L6 retention**: `context_kind` + unanswered bare-flag nudge hoisted above doc in `[sentinel, *hoisted, doc, *retained]` shape; hoisted messages boundary-INVISIBLE to `_scan_turn_window` (fed post-sentinel slice back through the real scanner).
- **Tool results**: ToolMessages + pairing AIMessages retained with ORIGINAL ids; **0 orphans** (P-7/P-8).
- **Symptom-persists**: `EmptyLLMResponseError` propagates byte-identically to master-OFF baseline; shipped loud-ERROR lane unchanged.
- Constants pinned: `SYMPTOM_REPAIR_BUDGET=3`, `EMPTY_DEGENERATE_REINVOKE_CAP=3`, `NUDGE_MARKER_KWARG="empty_response_nudge"`.

### e. Master-OFF byte-identity ×4 classes + carrier pin — ✅ PASS
- 63/63 probe checks + 7/7 pytest OFF-arms: per class (loop/ghost/truncated/empty_post_ladder) OFF = shipped routing byte-identical (no repair doc, no surgery, budget 0, originals preserved, shipped terminals identical); ON contrast arms prove non-vacuous. OFF-mode telemetry shape pinned (loop rung1 `action=skipped` still emitted — W1 KEEP; ghost sites fully behind master gate, shipped ungated surface = `[Graph] Ghost promise` WARNs).
- **Carrier pin (reviewer-routed, real run)**: flag=0 → `pending_repair_ghost_terminal` stays **None at EVERY superstep** (13/13 incl. all 4 cap-hits; `agent_repair_ghost` invoked 0×); flag=1 mechanism via checkpoint-resume recipe (budget=3 + ghost tail, `as_node="agent"`): carrier populated exactly at exhaustion superstep → cleared by consuming superstep → terminal final → END, zero LLM invokes.

### f. Cross-class isolation P-9 + 2×2 — ✅ PASS (1 plan gap)
- Loop+ghost same task: independent detection, shared-counter accounting exact (loop 1/3 → ghost 2/3, no theft/double-charge), correct class attribution, partition-by-shape negatives live (tool-call AI never ghost; plain AI never loop; S1/S5 clean).
- 2×2 ladder×empty-guard: all 4 arms PASS; S1 raise-in-retry-scope verified live through real `classify_llm_errors` seam; ladder-OFF×guard-ON = shipped loud ERROR, zero intercept.
- **Actual budget design**: SHARED per-task scalar counter (consistent with ADR-0001 "one durable budget"); P-9 = per-class exact single-charge — pinned by `TestF4CrossClassIsolation`.
- 🟡 **B-4 repair-once-per-turn latch NOT implemented** (plan-vs-code gap; two repairs fired same turn; bounded only by shared budget — which D-1 defeats for ghost). 🟡 F-4 planned `tests/integration/test_ladder_class_isolation.py` absent (coverage lives unit-level).

## 3. Priority 2 — Reviewer-Routed Gaps (test-only commits on branch)

| Commit | Test | Evidence |
|---|---|---|
| `232584a6` | `tests/unit/test_ladder_p2_routed_gap1_master_off_carrier_pin.py` — real-graph OFF arm (carrier None through ghost storm) + non-vacuousness contrast arm (resume recipe) | 1/1 PASS |
| `a522e8e0` | `tests/unit/test_ladder_p2_routed_gap2_second_exception.py` — truncated + empty_post_ladder parametrized; loud-ERROR byte-identity vs OFF baseline | 2/2 PASS |
| `0a9c9a79` | `tests/unit/test_ladder_p2_routed_gap3_caller_seam_gate.py` — intercept fires ON / inert when resolver forced OFF | 2/2 PASS |

**Inverted-gate catch PROVEN** (scratch `/tmp/rlp2-routed/inverted_gate_catch{,_off}.py`): under BOTH inversion directions the suite FAILS (arm (a) provider called 1× instead of 2×, no repair telemetry, exit 1). The suite can now catch an inverted caller-seam gate. Combined sibling run: 114/114.

## 4. Priority 3 — Carry-Forwards

| Item | Result | Evidence |
|---|---|---|
| PG parity | ✅ | §2b — pack ×2 idempotent + 3-class canaries ×2 idempotent on disposable PG14 (port 15432, `LADDER_PG_CONNINFO`, `postgresql://` scheme, privilege trap clean, teardown proven) |
| Full-suite attribution A/B | ✅ 0 unexplained | 12 partitions × both legs (detached worktrees, identical partition defs, all runs ≤300s cap; max P12 262s/278s). HEAD 290 distinct reds (F208/E82) vs BASE 288 (F206/E82): **288 common / 2 HEAD-only / 0 base-only**. Adjudication: (1) `test_proactive_compaction_fix_p1b.py` hook-order pin rot — branch-caused deterministic (3/3) via substring suffix-collision with P2's `new_response = await loop.run_in_executor(`; runtime ordering INTACT (product not regressed); test-side quick-fix applied (§7). (2) `test_symptom_repair_engine_phase2.py::TestMasterKillSwitchByteIdentical::test_master_off_ghost_returns_none_when_called` — branch-added ORDER-DEPENDENT flake (sync `get_event_loop` after `test_streaming_none_node_update`'s `asyncio.run()` on Py 3.13.3; solo 3/3 PASS); test-side quick-fix applied (§7). |
| Watchover disjointness | ✅ CONFIRMED | 49-red cluster set-equal at both legs, censuses byte-identical (`default_streaming` ×136 etc.); files: test_watchover_decision ×28, test_watcher_context_builder ×9, test_watchover_integration ×4, test_watchover_edge_cases ×3, test_watchover_phase5 ×3, job_queue/test_watcher_repository_concurrent ×2 — **zero ladder/symptom files**. Pre-existing (origin dd43a7f1), SEPARATE-DISPATCH item, not fixed. |
| Known families common | ✅ | archive_lifecycle ×5 (P1) · proxy_phase1 ×7 + b1 hardcoded-path (P2) · builtin_mcp 21E (P4) · find_near ×13 + status_guard ×4 + allowed-models pair ×2 (P5) · maintenancer-class ×10 (P6) · injection_api MagicMock-await ×25 (P9) · fresh-SQLite migration 20260714 (P9/P10) · job_queue known-7 (P11) · httpx row-60 ×79 (P12) — identical both legs |
| Perf sanity | ✅ NOT pathological | ghost count helper 0.94 µs/call, collect 0.98, combined site 2.0 µs on 332 msgs (bound 2000 ms; 50–110× faster than P1 LoopDetector baseline 0.104 ms) — early-exit backwards walk per B-2 design |
| FE-adjacent static | ✅ | `frontend/` diff EMPTY (verbatim); ghost terminal = normal AIMessage (`content=`+`id=` only, graph.py:2371-2386) via state-channel emission → plain-AIMessage END branch (graph.py:3750); zero `create_error_event`/`handle_message_processing_error`/SSE-error-lane references in terminal paths |
| Regression baselines | ✅ | ladder_symptom 87/87 (0 delta) · ladder_durable_sqlite 3/3 · **emptyguard 343/343 byte-exact** (shipped Option-4 contracts intact — exit criteria #7/#8) |
| ensure.md Core | ✅ 4/4 Critical | concurrency_atomic 98P/74S/0F canonical-exact (Core #2/#3) · dev.sh `--timeout-graceful-shutdown 10` on live uvicorn line (Core #4) |

## 5. Findings (non-blocking unless marked)

1. 🔴 **D-1** (§2a) — blocking product defect; repros + 3 fix directions recorded; KB entry filed by worker.
2. 🟠 **B-6 telemetry class-label drift** (2 confirmations): `_emit_symptom_telemetry` hardcodes `class=loop` (graph.py:1917, all 21 call sites); ghost/truncated/empty emit `class=loop` with class only in `detail=`. `grep '[SYMPTOM] class=ghost'` = 0 lines → **phase-3 per-class soak gating (exit criterion: per-class `[SYMPTOM]` observation) is unmeasurable by class token**. Engine-level `[SymptomRepair] class=%s` lines DO carry the class (partial mitigation). Fix: thread `symptom_class` into the emitter, or amend spec/phase-3 gate to key on `detail=`.
3. 🟡 **B-4 repair-once latch absent** (§2f) — plan-vs-code gap; acceptable while shared budget binds, but D-1 removes that bind for ghost. Re-assess after D-1 fix.
4. 🟡 `surgery_prefix` dropped on second-exception return (graph.py:2778) — by-design; excerpt canonical surface = direct `engine.repair()` + committed gap-2 test + restart probes.
5. 🟢 Restart-durability for the 3 new classes exists as GATE PROBES only (/tmp, ephemeral) — recommend committing a P2 T-2b canary file (mirrors P1 commit `703e46f6`) as follow-up.
6. 🟢 Pack-script `set -e` quirk: on failure the 3 re-run pack scripts exit before printing `RESULT: FAIL` (exit code preserved). Polish item.
7. 🟢 `ladder_pg_test.sh` auto-overrides caller `LADDER_PG_CONNINFO` to its own DB name (line 50) — document or parameterize.
8. 🟢 Attribution-leg label drift: head-roster text artifact internally inconsistent (use raw partition logs); minor family-label inaccuracies in base report (P5 "coding2 pair" = `test_llm_allowed_models_precedence`; P6 "maintenancer ×10" = 1 maintenancer-file test) — compositions byte-identical regardless.

## 6. Quarantine / Flaky Actions

- No new quarantines (the order-dependent flake was root-caused and fixed test-side, not quarantined — solo 3/3 PASS + both-order pair green post-fix).
- Watch items: none new. Pre-existing watchover 49-red family re-confirmed at both legs (separate-dispatch).

## 7. Gate-Added Test-Only Commits (branch, `test:` prefix, zero production touches)

| Commit | Content |
|---|---|
| `232584a6` | Routed gap #1 — master-OFF carrier pin (real graph, OFF + contrast arms) |
| `a522e8e0` | Routed gap #2 — second-exception-path (truncated + empty) |
| `0a9c9a79` | Routed gap #3 — caller-seam gate wiring (inverted-gate catcher) |
| (quick-fix `187ea05b`) | `test_proactive_compaction_fix_p1b.py` hook-anchor pin repair (suffix-collision rot; anchor `find()` after hook site) — 3× solo + file 28P |
| (quick-fix `8809b3a2`) | `test_symptom_repair_engine_phase2.py` sync→async conversion of the master-off ghost node (order-dependence) — 3× solo + both-order pairs + file 51P |
| (this gate) | RESULTS file + PACKS.md rows |

## 8. Restart-Activation Note

Master `ENSEMBLE_SYMPTOM_REPAIR_LADDER` (default ON) remains restart-pending. Post-restart soak greps: per-class telemetry currently surfaces as `class=loop` for ALL classes (finding #2) — use `detail=` or the engine-level `[SymptomRepair] class=` lines for per-class counts until B-6 is fixed. Phase-3 entry criteria (per-class detect+repair soak events; per-class OFF pins) BLOCKED on B-6 for class-token keying.

## 9. Gaps

**None in verification coverage.** All 13 planned nodes completed; 14 worker dispatches + 1 A/B diff + 1 quick-fix (16 total), every dispatched pack reported, 0 re-dispatches, 0 stranded workers. Blocking item is a PRODUCT defect (D-1) routed to the leader, not a coverage gap.
