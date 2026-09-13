# Verification Gate — Hallucination-Recovery-Ladder PHASE 1 (durable loop-breaker rung)

**Date:** 2026-09-13
**Branch:** `feature/hallucination-recovery-ladder` — verified range `57b1e0c2` (spec) → `22a75147` (P1 impl) → `62650f82` (W1-W4 hardening) → `669ef6f0` (tidy) → gate-added test-only commits through `d713dda6`
**Worktrees:** `agents-ensemble-wt-recovery-ladder` (branch) + `agents-ensemble-wt-recovery-ladder-base` (throwaway detached @ `57b1e0c2`, removed post-gate)
**Verdict:** ✅ **PASS FOR MERGE (GO)** — 0 branch-caused failures across the full 12-partition suite; Priority-1 symptom closure verified on SQLite AND PostgreSQL; restart-activation pending (kill-switches default ON).

---

## 1. Scope Decision

Full verification requested and warranted: 4 production files changed (`daemon/config.py` +132, `daemon/graph.py` +715, `daemon/services/context_messages.py` +8, `daemon/services/symptom_repair_engine.py` NEW +779), cross-cutting `agent_node` return assembly shared with L2 compaction + new GraphState field + kill-switches. Diff = 16 files +4758/−68, **zero frontend/ changes** (verified twice: recon + M4a pack pre-flight, empty diff).

## 2. Priority 1 — Symptom Closure (all PASS)

### a. Continuous identical toolcalls → durable repair → different approach — ✅ PASS 87/87 (0.44–2.64s) — pack `ladder_symptom_unit_test`
- Detection @ threshold 3: real `LoopDetector.scan` + real-repairer e2e (`test_loop_detector_finds_kb_writer_time_loop`, `test_full_in_memory_pipeline_with_real_repairer`).
- Durable repair shape: sentinel `RemoveMessage(REMOVE_ALL_MESSAGES)` element-0; retained tail keeps ORIGINAL ids/objects (upsert-in-place); repair-doc id namespace `repair-{iid}-{seq}` distinct from `compaction-global-`; per-instance seq monotonic (`TestSurgeryShape` ×5).
- Continuation: post-repair provider emits different approach → turn completes, no re-trip next turn (`test_llm_changes_approach_no_re_repair_on_next_turn`).
- Mock fidelity CLEAN: real `langchain_core.messages` objects at every load-bearing seam; mocks only at LLM-invoke seam.
- P-8 tool-pairing + orphan sweep; budget consult/increment/abort-no-increment pinned (`TestBudgetGate` ×3).

### b. THE RESTART SCENARIO — ✅ PASS on BOTH engines
- **SQLite** 3/3 (+ new T-2b): TRUE restart = fresh aiosqlite connection + fresh `AsyncSqliteSaver` on same file → stripped block NOT replayed (`ai-1/tm-1/ai-2/tm-2` absent), original-id evidence retained, `repair_budget_used == 1` from restored state, explicit `LoopDetector.scan(...) is None` no-re-trip. Commit `703e46f6`.
- **PostgreSQL** 2/2 runs (disposable PG14, port 15432, idempotent re-run identical): real `AsyncPostgresSaver.from_conn_string`, restart via NEW compiled graph over same saver, 7 in-body durability asserts (lines 181/182/183/189/190/211/212), UUID-isolated per run. Non-vacuous (zero skips; `LADDER_PG_CONNINFO` gate satisfied; not SQLite fallback).

### c. Budget exhaustion → LOUD terminal — ✅ PASS (`TestExhaustionEscalation`)
3/3 budget → next detection returns **normal AIMessage with visible content** ("[LOOP TERMINATION]…", `_LOOP_TERMINAL_CONTENT` graph.py:1851-1858), no tool_calls → END, `len(llm.calls)==0` (no continuation), budget not incremented. NOT the legacy WARN+continue.

### d. OQ5 reset discrimination — ✅ PASS (`TestBudgetResetOQ5`, 7 tests incl. 4 gate-added)
Real HumanMessage → reset to 0. Negative arms pinned against EXACT production marker shapes (gate commit `94f0409b`): empty-nudge (`NUDGE_MARKER_KWARG`), language-reminder (`LANGUAGE_REMINDER_TEMPLATE` + `language_check_reminder`), attestation-nudge (`ATTESTATION_NUDGE_TEXT` + counter), `[SYSTEM CONTEXT]` (prod `_make_context_message` shape). All → budget unchanged.

### e. Kill-switch OFF byte-identity — ✅ PASS (`TestP11KillSwitchByteIdentity` + OFF-mode arm of exhaustion)
Both flags =0 → outcome `None` (byte-identical fall-through); graceful even with broken engine; legacy WARN+continue with ORIGINAL messages preserved (`test_ram_per_turn_cap_shipped_warn_continue_under_off`: `repairer.repair.assert_not_awaited()`, LLM invoked once, content preserved); RAM budget; repair NOT durable across restart.

### f. 2×2 loop×empty-guard matrix — ✅ PASS (4/4 arms; 4th arm gate-authored, commit `c6310826`)
Loop ON×Empty ON (bounded 7-call burn, `class=loop` attribution, budget 1/3) · ON×OFF (W1: repair still bounded, S5 cap warning absent) · OFF×OFF (T-8 shipped routing: originals remain, no repair doc, budget 0) · **OFF×ON (NEW)**: 6 assertions — budget 0, no engine calls, no repair doc, S5 cap fires, `provider.calls==3` bounded, no `[SYMPTOM] class=loop`. Partition-by-shape double-pinned (`TestP1ShapePartition`); P-9 no counter theft (source + behavioral).

### g. Guard regression S1/S5/L1-L13 — ✅ PASS 343/343 (4s) — `emptyguard_scenarios_unit_test` verbatim
Byte-exact the 2026-09-12 baseline; zero new skips. Shipped Option-4 contracts intact.

## 3. Priority 2 — Carry-Forwards (all PASS)

| Item | Result | Evidence |
|---|---|---|
| PG suite execution (tidier-refactored fixtures, never run in wt) | ✅ 2/2 identical | 0.34s/0.39s; teardown proven (port freed, pgdata removed); no shared-DB touch |
| Full-suite attribution A/B | ✅ **0 branch-caused** | 12/12 partitions; see §4 |
| Summarizer failover e2e | ✅ 10/10 (commit `d713dda6`) | REAL `wrap_langchain_failover` + tenacity binding + `FailoverController` base_url swap (both root clients); only inner chat client mocked; abort = no surgery + budget untouched + turn not wedged; **no bare-invoke/static-fallback path remains in the engine** (ADR-0006 closed); 120s timeout inherited (3 pins) |
| L2-skip behavioral (W2) | ✅ | `TestL2PrecallSkipBehavioral` (supersteps 1-3 consulted, 4 SKIPPED on repair, 5 consulted again) + source pin |
| Telemetry axis suffix | ✅ as-run | **Corrected count: 4 `axis="ram-per-turn"` + 4 `axis="durable-task"` = 8 non-terminal sites** (graph.py 2278/2297/2360/2406 ram; 1981/2087/2105/2131 durable) — recon's 7+5 was over-counted; invariant (non-terminal-only, terminal suppression at graph.py:1812) holds and is behaviorally pinned |
| Perf sanity (300+ msgs) | ✅ NOT pathological | `LoopDetector.scan` on 328 messages: **0.104 ms** (bound 2000 ms, ~19,000× headroom) |
| FE-adjacent static | ✅ | `frontend/` diff empty; loud terminal = normal AIMessage → `response = _durable_loop.terminal_message` (graph.py:5782-5784); no `create_error_event`/`handle_message_processing_error` reference in the ladder path (regex-pinned) — does NOT ride the FE-unrenderable SSE error lane |

## 4. Full-Suite Attribution (12 partitions, HEAD `d713dda6`-lineage vs base `57b1e0c2`)

| # | Partition | HEAD | Base/roster | Attribution |
|---|---|---|---|---|
| 1 | unit_tools | **2569P/5S/0F** | — (0F) | clean |
| 2 | unit_services | 8F/1583P | 8F set-equal | 8/8 base-reproduced (proxy_phase1 ×7 + b1 hardcoded-path FileNotFoundError) |
| 3 | smaller_subdirs_routers | 647P/0F | exact | clean; `tests/unit/graph/` 0 failures |
| 4 | loose_a_d | 10F/21E/1462P | 10F/21E identical totals | 31/31 base-reproduced (mock rot, migration trap adjacency, fixture drift) |
| 5 | loose_e_l | 19F/1220P | 19F node-equal | 19/19 base-reproduced — **incl. the 🚩 allowed-models `coding2` pair: base's config.py already yields it → PRE-EXISTING latest-lineage drift, NOT branch** |
| 6 | loose_m_r | 10F/2056P | 10F identical | 10/10 base-reproduced |
| 7 | loose_s_z | 52F/2E/1119P | 54F+2E within tolerance | all base-reproduced; **zero ladder-file failures in full-partition context** (no interference either direction); −100 collection delta at base = missing ladder files (expected) |
| 8 | top_a_h | 19F/2E/1059P | 18F/2E | 18F+2E base-reproduced; 1F delta = jsonb F↔E setup-race noise (same node failed at ancestor 97b45ba1) → **0 branch-caused** |
| 9 | top_i_q | 60F/2390P | 58F | 58 base-reproduced (documented classes a/a2/a3/b-row-27); +2 HEAD-only: `test_ab_resolution_threshold_met` (QUARANTINE row-15 flake family — NOTE: base leg's guess `force_resolve` was wrong; node-diff verified) + `test_concurrent_writes_no_corruption` (latent race witnessed once under load; **3/3 PASS at HEAD** on retry budget; no branch mechanism) → **0 deterministic branch-caused** |
| 10 | top_r_z_misc | 13F/2259P | 13F/2259P/34S/5xf IDENTICAL (2026-09-12 roster) | roster-attributed pre-existing |
| 11 | job_queue | 7F/1723P | KNOWN-7 node-for-node (2026-09-10 baseline) | attributed |
| 12 | integration_opencode_e2e | 20F/58E/1052P | 14F/8E | base leg claimed 🚨 ~57 branch-caused (httpx family + dead_letter ×2) — **OVERTURNED by discrimination probe**: venvs identical (httpx 0.28.1 both, construct-OK), zero branch commits on httpx surfaces, poison+victim files byte-identical base↔HEAD, pair-scope (`vscode_proxy`+`client`) reproduces **byte-identical counts at BOTH commits** (plain 5F/70P/43E; xdist 1F/75P/42E). = documented QUARANTINE row-60 in-suite httpx pollution class; base leg's "absent" was a suite-composition artifact (repeat of the documented Aug-28 extrapolation error) → **0 branch-caused** |

**Net: every red across all 12 partitions is pre-existing, flake-family, environmental, or composition artifact. ZERO branch-caused failures. ZERO branch-caused errors.**

## 5. Flaky / Quarantine Actions

- **No new quarantines** (nothing met the ≥1-pass+≥1-fail-in-budget criterion).
- Watch items (note-only): (1) `ladder_failover_e2e` cold-start xdist transient (3 observed once on partition cold run; 3/3 warm PASS; mechanism = import-chain vs per-test monkeypatch; structural fix if it recurs: session-scope autouse patch of tenacity nap/time.sleep); (2) `test_memory_integration::test_concurrent_writes_no_corruption` latent race (lost-update shape, needs machine load; pre-existing test-side race; candidate for a future soak under `-n 4 --maxschedchunk=1`); (3) pre-existing flake families re-confirm: perf-matrix variance node (top_a_h), bucket5 ±1F/±5E (integration).

## 6. Findings (non-blocking, report-only)

1. 🟢 **Axis site count correction** — spec/recon said 7+5; shipped = 4+4 (all non-terminal, terminal axis-free). Invariant intact; MIN-band test stays green under future hardening.
2. 🟢 **PG env contract** — the PG ladder test reads `LADDER_PG_CONNINFO` (NOT `ENSEMBLE_TEST_PG_URL`); conftest `pg_engine` needs `PG_TEST_*` redirected to the disposable cluster or create_all hits the shared ensemble_test DB (known privilege trap); URL scheme must be `postgresql://` (psycopg conninfo parser rejects `+asyncpg://`).
3. 🟢 **httpx row-60 polluter narrowed** — `test_vscode_proxy.py` ALONE reproduces the full 43E+5F signature vs `test_client.py` in one process (recorded to KB; closes half the pending row-60 bisection).
4. 🟢 **Base-leg extrapolation-error pattern, 2nd occurrence** — a single-leg "family absent at base" observation was wrongly escalated as branch-caused; QUARANTINE row 60 already documented the identical mistake on 2026-08-28. Process rule (LESSONS/2026-09-13-base-leg-extrapolation-httpx.md): composition-dependent families need scope-matched reproduction before branch-caused is claimable.
5. 🟢 `test_b1_wc_durable_send` hardcodes sibling-worktree path `agents-ensemble-wt-wc-wake-resilience` (FileNotFoundError wherever that worktree is absent) — pre-existing env-dependent red.
6. 🟢 Author metadata on the 3 feature commits reads 'Councilor C2' — giter fixes at merge (out of gate scope, per leader).

## 7. Gate-Added Test-Only Commits (branch, all `test:` prefix, zero production touches)

| Commit | Content |
|---|---|
| `c6310826` | 4th 2×2 arm (Loop OFF × Empty ON, 6 assertions) + `ladder_killswitch_matrix_test.sh` |
| `94f0409b` | 4 E5 production-marker-shape negative arms + `ladder_exhaust_oq5_test.sh` |
| `703e46f6` | T-2b TRUE-restart hardening (fresh saver) + `ladder_durable_sqlite_test.sh` |
| `2668e563` | PACKS.md registration (durable pack) |
| `a5c04f3a` | `ladder_misc_test.sh` + 9 tests (axis sites, perf, terminal routing) |
| `d713dda6` | `ladder_failover_e2e_test.sh` + `tests/unit/test_symptom_repair_engine_failover_e2e.py` (10 tests, REAL facade path) |
| (this gate) | `ladder_symptom_unit_test.sh` + `ladder_pg_test.sh` committed + PACKS.md rows + RESULTS file |

## 8. Restart-Activation Note

Kill-switches `ENSEMBLE_SYMPTOM_REPAIR_LADDER` (master, default ON) + `ENSEMBLE_REPAIR_LOOP_DURABLE` (sub, default ON) are restart-pending per house convention — activate via the pause-first restart runbook at merge. Post-restart validation grep: `[SYMPTOM] class=loop` lines (telemetry NOT gated — emits in OFF mode too, per W1-KEEP precedent).

## 9. Gaps

**None.** All 11 planned verification nodes completed; every dispatched pack reported (0 re-dispatches needed, 0 stranded workers). 27 worker dispatches total.
