# VERIFICATION GATE — EMPTY-RESPONSE-GUARD (Option 4 Layered Minimal)

**Date:** 2026-09-12 · **Tester gate, final pre-merge**
**Branch:** `feature/fix-empty-response-guard` @ `97b45ba1` (stack: 03ee192c plan-doc → abc226c7 impl → 8f211c3e review-fix-1 → cadbce48 review-fix-2 → 97b45ba1 tidy; merge-base with `latest` = `3a9357e8` = 03ee192c's parent — confirmed strict descendant)
**Base for attribution:** detached `03ee192c` (leader-designated)
**Spec:** `.agents/shared/planning/empty-response-guard/architecture-recommendation.md` (§3 facade table, §8.1 spoke-rule synthesis, §10 test strategy, §11a/b edge answers, §12 Open Questions) · Gap doc: `docs/hallucination-protection.md` §7 (+32 lines on branch)
**Worktrees:** `agents-ensemble-wt-erg` (@97b45ba1, detached, clean) · `agents-ensemble-wt-erg-base` (@03ee192c, detached, clean) — both uv-synced; no daemon booted anywhere; port 8088 untouched throughout
**Method:** every run via `uv run python -m pytest` (bare pytest = broken foreign Homebrew), dual-layer timeout everywhere (`timeout 300` command-level + script-internal 280s / per-test ini), drift-pinned per pack, FE diff confirmed EMPTY (`git diff 03ee192c..97b45ba1 -- frontend/` → no output)
**Diff shape:** 20 files +2285/−46 — 8 daemon/ prod files (graph.py +242, response_validation.py +429, config.py +97, manager.py +85, compaction.py +46, llm_error_classifier.py +9, api.py +14, __main__.py +14), config.yaml +6, 8 test files (3 new), docs +32, .agents +2 (ADR + spec update)

---

## VERDICT: ✅ **PASS FOR MERGE (GO)** — with 1 reported production defect (non-blocking, 1-line, leader-routed) + restart-activation pending per repo convention

Original symptom (provider returns continuous empty AI messages; silent empty successes / ~100-call reasoning-only burn) is **CLOSED and pinned**: raise-inside-retry-scope → bounded transient budget → backup swap → loud exhaustion → instance ERROR; empty AIMessage never checkpointed as final answer; S5 caps bound the degenerate re-invoke loops. Zero durable state added. Kill-switch + COMPACTION_SKIP knobs fail-loud (`ValueError`) and restore legacy routing.

---

## 1. PRIORITY 1 — Original-symptom scenarios (all PASS)

Guard's own suite run as ad-hoc pack `emptyguard_scenarios_unit_test` (7 files, worktree-local script modeled on compaction pack): **343/343 PASS in 4.43s.**

| # | Scenario | Verdict | Evidence |
|---|----------|---------|----------|
| a | Continuous empty, no tool results → S1 raise → bounded retries → swap → loud terminal ERROR; no empty final checkpointed; instance ERROR lineage | **PASS** (2 layers) | Ladder: `TestGuardRidesRetryFailoverLadder` ×6 (classifier re-raise :383, transient-budget consumption :409, swap-at-3 :421, swap-then-succeed-on-backup :449, loud-exhaustion-no-backup :434 w/ exactly 3 calls). End-to-end lineage (NEW, `b3c0120a`): real `create_agent_node` + real `classify_llm_errors`+`Retrying`+HA wiring + real LangGraph `StateGraph`+`MemorySaver` — (a) 3 bounded calls, `EmptyLLMResponseError` raised, **last state message is the HumanMessage (no empty AIMessage)**; (b) backup also empty → 6 calls total, 1 swap, bounded; (c) no backup → verbatim loud exhaustion; (d) normal response → 1 call, clean pass (no false positive) |
| b | No backup → loud exhaustion, no swap | **PASS** | `:434` (call_count==3, no controller) + lineage (c) |
| c | Reasoning-only continuous empties → cap 3, burn ≤ cap+2 (≤ 2×cap with-tool), no GraphRecursionError | **PASS** | `TestDegenerateReinvokeCap` ×9: reasoning-only ≤cap+2 :144, think-tag :150, mixed :155, with-tool ≤2×cap :198, **route-sequence pinned** `["agent","agent","nudge","agent","agent",END]` with llm_calls==2×cap :218, ghost-promise deliberately NOT capped :168, cap==3 == LoopDetector threshold :246 |
| d | Nudge contract + attestation-nudge repro | **PASS** | First-empty-after-tool → nudge; **second empty after nudge raises** :562 (legacy text too :574); attestation `[user, report_AI, attestation_nudge]`+empty → NO raise :587/:604 (legacy + stamped shapes); marker pins :711-766 |
| e | Kill-switch OFF byte-identical routing (S1+S5), telemetry intentionally UNGATED | **PASS** | `TestKillSwitchDisablesRouterHalf` ×4 (incl. exact pre-abc226c7 truth table `[]→False` :293) + `TestEmptyResponseGuardKillSwitch` ×4 (raise disabled :771; checks 1&2 still fire :778; scope isolation :793) + `TestEmptyResponseStreakTelemetry.test_kill_switch_off_still_bumps_streak_and_warns` :562 (spec §11c W1) |
| f | Predicate truth table (incl. multimodal + malformed fail-open) | **PASS** | `TestSharedEmptinessPredicate` ×18: None :393, "" :396, ws :401, think-tag NON-empty :407, all-ws-text-blocks empty :417, non-ws text non-empty :421, image-block non-empty :425, `text:None` fails OPEN :449, `[None]` fails open :456, `{"text":""}` empty :464 |
| g | Nudge-pin flip (`test_nudge_behavior.py`) | **PASS** | `TestIsEmptyContent::test_empty_list_is_vacuously_empty` (now :58 — 5-line drift from spec's :53; pin flipped to `[]→True` same-commit per spec Phase-1 item 2) |
| h | Secondary-surface fallbacks (keyword + title-gen) | **PASS** | Keyword (branch): retry→heuristic `[]` (:383). Title-gen (NEW `5898457c`): `""` AND `None` shapes retry→**skip-store**, ± backup, kill-switch OFF passthrough, think-tag exemption, pre-existing-title short-circuit — 7/7 |
| i | Compaction truncation fallback + COMPACTION_SKIP | **PASS** (NEW `5898457c`, 9/9) | Empty → retry ladder (`max_retries_per_call ≥1`) → `compaction_type="truncation"`, `failure_kind="error"`; **`COMPACTION_SKIP=ON` → 0 retries burned** (leader's direct pin; scope entered on worker thread); truncation fallback mechanism preserved (timeout still routes `_truncate_fallback`); EmptyLLMResponseError **escapes the TimeoutError-narrowed excepts** (:2830/:2908/:2928) into `except Exception` (:2645→`_truncate_fallback` :3686) — behavioral + 2 AST pins |
| j | Single-caller grep-pin `_is_empty_content` | **PASS** | AST-based exactly-one-prod-caller in graph.py :351 + delegate-matches-shared-predicate :366 |
| k | Classifier transient membership + swap-at-3 | **PASS** | Subclass chain into TRANSIENT_EXCEPTIONS :534/:202; swap-at-3 :421 (attempts 1-3 retry, 4th swaps) |

**Scenario-a residual (e2e "no empty final / ERROR lineage") was a discovery-confirmed GAP on the branch** (only structural pins existed) → closed by NEW `tests/integration/test_empty_guard_error_lineage.py` (`b3c0120a`+`2a8dc71e`). Scenarios h-title-gen and i-compaction were config-only on the branch → closed by NEW `5898457c`.

## 2. PRIORITY 2 — Carry-forward outcomes

| Carry-forward item | Outcome |
|---|---|
| **Full-suite independent re-attribution** (dev claim: 108✗/23E all pre-existing) | ✅ DONE, **claim held for 235/236; 1 miss found & fixed.** My sweep: 12 partition packs, 17,547P/213F/33E (~205S), no TIMEOUT (7-148s each ≪300s). 10 base legs @03ee192c (byte-identical scripts): **235/236 nodes signature-verbatim PRE-EXISTING; 1 BRANCH-CAUSED** = `test_attestation_in_graph_nudge_flow.py::test_flagship_deny_nudge_routes_back_and_attests` (solo PASS at base / FAIL at branch): branch stamps attestation nudge with `injected_message:True` (spec-correct, cadbce48) but stale pin asserted old payload → **FIXED test-side `d6300a5f`** (production untouched). Dev's 10,572-test run was a subset; my 17.8k-node sweep is the definitive set |
| **Spec §10 e2e before restart-activation** | ✅ title-gen skip-store ✓ · compaction truncation-fallback escape ✓ (incl. AST pins) · COMPACTION_SKIP truncation-preservation ✓ (0 retries + fallback intact) · continuous-empty → instance ERROR lineage ✓ (graph-level, real machinery) |
| **Streaming: installed langchain-openai aggregation** (reviewer: "taken from doc") | ✅ **SOUND** — independently verified from INSTALLED source + empirical probes: langchain-openai **1.1.10** / langchain-core **1.2.16** / openai 2.24.0. `None→""` coercion at chunk conversion (`base.py:408`) AND non-streaming (`:189`); usage-only chunk = explicit `""` (`:1163`); `invoke()` aggregates via `generate_from_stream` → one final AIMessage; **`reasoning_content` SURVIVES streamed aggregation** (daemon override `graph.py:2209-2231` stamps chunks; `merge_dicts` CONCATENATES strings → truthy → S1 exemption sound — no false-raise on legit reasoning models); zero mock-vs-real divergence (every branch mock shape matches probe output). Doc nit: spec §11a "preserved" is actually "concatenated" — harmless |
| **PG runtime round-trip** (reviewer: unexercised) | ✅ **PASS 2/2** (`9530f4cb`) — real `AsyncPostgresSaver` on disposable PG14 (ports 15441-15460, `initdb -A trust`, POSTGRES_* scrubbed, prod untouched) + SQLite default path (WAL+busy_timeout): nudge markers byte-survive restore; `_scan_turn_window` result restore-invariant; `validate_llm_response(empty, input_messages=restored)` raises identically; non-vacuous (mutation → FAIL → revert); teardown verified (cluster stopped, datadir removed, ports free) |
| **Spec Open Question 4** (designed-empty finals) | ✅ **CLOSED — no exemption-table gap.** 37 registered agents (39 dirs, 2 prompt-fragment), 301 files, 26 meta.json keys inventoried, all designed-empty grep patterns negative. Every agent has prompt-level non-empty turn-ending contract. Near-misses safe: watcher (mandatory first-line verdict; empty = fail-closed deny; service-side invocation); v2 dispatcher family ("END TURN" = stop polling; acks are content). Runtime watch-item for spec: dispatcher model emitting zero text post-dispatch = model non-compliance → retry/telemetry, not exemption |
| **Perf sanity `_scan_turn_window`** (tidier) | ✅ **NOT-PATHOLOGICAL.** O(d) per call, early boundary exit: realistic mix FLAT 0.29µs across 300→5000 msgs; forced boundary-free worst shape (unreachable under router discipline) 1.13ms @5000 (~222ns/msg) → ~112ms per pathological 100-call turn. No memoization warranted |
| **FE-adjacent SSE shape** | ⚠️ **GAP — pre-existing FE, NOT branch** (zero frontend/ files changed). BE correct + loud: raise lands inside retry scope → empty AIMessage NEVER checkpointed (`graph.py:5517` re-raise precedes `outgoing` build `:5656`); emits SSE `error` + `status_change{status:"error"}` (instance_messaging.py:4294-4300 → live_event_hub.py:202-218; error_reporting.py:839-843). BUT FE transcript renders NEITHER: `chat.component.ts:540-549` logs-and-clears `latestError`; `sendError` banner fires only on POST-failure paths. User sees: delivered bubble + empty transcript + no banner; only sidebar dot changes. Applies to ALL LLM-error classes, not just this guard. Fix candidate: `sendError.set(latestError.message)` at :547 (+ equivalent status_change effect). Report-only per gate rules |

## 3. Full-suite partition table (branch @97b45ba1) + base attribution

| Partition | Result (branch) | Runtime | Base @03ee192c verdict |
|---|---|---|---|
| P-1 regression_unit_tools | ✅ PASS 2569P/5S/0F | 14s | (green — no leg needed) |
| P-2 regression_unit_services | ⚠️ 1576P/**8F** | 18s | **8/8 PRE-EXISTING** (7 = QUARANTINE row-24 proxy_phase1 family, node-identical to 2026-09-07 record; 1 = env defect `test_b1_wc_durable_send.py:255` hardcoded deleted sibling-worktree path → new QUARANTINE row) |
| P-3 regression_unit_subdirs_routers | ✅ PASS 647P/0F | 13s | (green) |
| P-4 regression_unit_loose_a_d | ⚠️ 1452P/**11F+21E**/2S | 18s | **31 PRE-EXISTING + 1 flake** (`test_full_workflow_configure_reset` 1F/1P at base → QUARANTINE row); 21E = mock-gap fixtures (`slash_commands` ×17, `blueprint` ×4) byte-identical |
| P-5 regression_unit_loose_e_l (guard primary) | ⚠️ 1211P/**19F** | 42s | **19/19 PRE-EXISTING** (find_near ×13 = documented 02918951 stale-mock family; status-guard ×4; allowed-models ×2). **Guard's own files ALL GREEN** |
| P-6 regression_unit_loose_m_r (guard primary) | ⚠️ 2056P/**10F**/40S | 65s | **10/10 PRE-EXISTING** (release-tag pin v0.12.4↔v0.12.7, models `__all__`, MagicMock-await ×5 @messages.py:249, cascade_to_root kwarg, project-manager cross-refs ×2). **Branch-modified files ZERO reds** |
| P-7 regression_unit_loose_s_z | ⚠️ 1008P/**52F+2E**/11S | 32s | **54/54 PRE-EXISTING** (watchover evaluator/structure/integration ×38 + watcher-context-builder ×9, all `default_streaming`-AttributeError mock-degrade family; standalones ×5; webfetch `blueprint` ×2E — only delta = benign +17 line shift) |
| P-8 regression_top_level_a_h | ⚠️ 1059P/**20F+2E**/53S | 76s | **22/22 PRE-EXISTING** (2 runs). **Chokepoint gate ×2 = verbatim the 6 charter-documented latest-lineage entries** (3 unexpected + 1 over-budget + 2 direct-SQL; line drift +42/+19 only) — NOT branch-introduced |
| P-9 regression_top_level_i_q | ⚠️ 2388P/**58F**/73S | 35s | **58/58 PRE-EXISTING** node-exact (MagicMock-await ×26, SQLite-migration-20260714 trap ×18, inner_soul TypeError ×9, drifts ×5) |
| P-10 regression_top_level_r_z_misc | ⚠️ 2259P/**13F**/34S/5xf | 28s | **13/13 PRE-EXISTING** (migration trap ×9, skill_evolution ×2, orphan-matrix ×1, team_members ×1); +1 base-only flake (`worker_notification` — documented context-flake family, passes solo) |
| P-11 regression_job_queue | ⚠️ 1723P/**7F**/38S | 28s | **7/7 PRE-EXISTING** — set equality, triple-agreement (base/branch/2026-09-10 gate) |
| P-12 regression_integration_opencode_e2e | ⚠️ 1099P/**15F+8E**/2S | 148s | **14/15 PRE-EXISTING + 1 BRANCH-CAUSED (FIXED `d6300a5f`)**. 8E = 1 file × 8 workers (httpx env, daemon not booted by design). Base-only extras = documented env/context classes (dead_letter in-suite httpx row-37, opencode client drift) |

**Aggregate: 17,547 passed / 213 failed / 33 errors. Attribution: 235 pre-existing (byte/verbatim signatures) + 1 branch-caused (attestation payload pin; production spec-correct; fixed test-side) + 4 flake observations (all retry-budgeted → QUARANTINE). ZERO unresolved branch-caused failures.**

## 4. Defects & findings

| # | Sev | Finding | Disposition |
|---|---|---|---|
| D1 | 🟠 important | **PROD (1-line, report-only):** `EmptyLLMResponseError` routes to `execution_error` lane, not `validation_error` — `message_processing_errors.py:131-133` matches class NAME, not `isinstance`; voids the spec §3-2/ADR-0001 "zero seam edits" subclass assumption for the lane seam. Instance still → ERROR (loud); error_type label in event row + parent report wrong; Phase-2 promotion hook blind to the class. Repro: `_classify_error_type(EmptyLLMResponseError("x")) == "execution_error"`. Fix: widen tuple or isinstance | **Reported for leader routing; PINNED** by 2 strict-xfail tests (`2a8dc71e`) that un-xfail when fixed. LESSONS/2026-09-12-empty-guard-lane-name-matching.md |
| D2 | 🟠 important (pre-existing, FE) | FE transcript renders nothing for SSE `error` / `status_change{error}` events — guard failures (and ALL LLM-error classes) end as silent empty transcript + `isSending=false`; only sidebar dot changes | Report-only (no FE files on branch); fix candidate `chat.component.ts:547` `sendError.set(latestError.message)` |
| D3 | 🟢 nice (env defect) | `test_b1_wc_durable_send.py:255` hardcoded deleted sibling-worktree `cwd` → deterministic FileNotFoundError on every checkout | QUARANTINE row added (test-side fix: `Path(__file__)`-anchor) |
| D4 | 🟢 nice (spec doc) | §11a "reasoning_content preserved" → actually CONCATENATED across chunks (truthiness intact — exemption sound); COMPACTION_SKIP=ON + empty routes via summarization path (`summaries=[""]` non-empty), not literal truncation — intent (0 retry burn) pinned either way | Doc-nit notes for spec owner |
| D5 | 🟢 nice (watch) | Chokepoint gate red at base = 6 documented charter entries still unfixed on latest lineage | Standing follow-up (charter-reuse list #1) — unchanged by this branch |

## 5. ensure.md (Core, scoped) — ✅ 8/8

- **Critical-1** (no regressions in changed packs): **PASS by attribution** — every changed-scope pack green or 100% base-attributed; 0 unresolved branch-caused
- **Critical-2/3, Important-2** (concurrency/thread-identity): `concurrency_atomic_unit_test` **98P/74S/0F in 7.31s** — baseline-exact
- **Critical-4**: dev.sh `--timeout-graceful-shutdown 10` exact match (dev.sh:102)
- **Important-1**: all 9 async call-sites awaited (`_get_system_prompt_tokens` ×2, `_compute_context_usage` ×1, `get_queue_stats` ×6)
- **Nice-to-have**: all 4 new symbols have ≥1 real consumer (install_empty_guard_config → config.py:3214; empty_guard_disabled → compaction.py:76; EmptyLLMResponseError → raise site + catch tuples; is_empty_llm_content → graph.py ×3)
- **Contradiction scan**: CLEAN — no Improvement Notices
- **Release Gate**: scoped OUT (daemon-restart-activation pending; leader's verification is pre-activation; scenario-level mock-provider suites supersede daemon E2E per gate convention — same posture as charter-reuse gate)

## 6. On-branch test commits (this gate — test-only, production untouched)

| Commit | Content |
|---|---|
| `b3c0120a` | `tests/integration/test_empty_guard_error_lineage.py` (752 ln) — real-machinery lineage e2e a-d |
| `2a8dc71e` | strict-xfail conversion of the 2 lane pins (suite green; wc-wake f04611aa precedent) |
| `5898457c` | `tests/unit/services/test_title_generation_empty_guard.py` (524 ln) + `tests/unit/test_compaction_empty_guard_fallback.py` (809 ln) — 16/16 |
| `d6300a5f` | attestation-nudge payload pin update (branch-intended shape; base-pass/branch-fail attributed) |
| `9530f4cb` | `tests/integration/test_empty_guard_checkpoint_roundtrip.py` (598 ln) — PG+SQLite 2/2 |

## 7. Scope Decision

Full suite run — **warranted**: leader explicitly requested full-suite independent re-attribution; executed as the 12 established partition packs (each ≤148s ≪ 300s cap, quarantine deselects intact). FE pack skipped: zero frontend/ files in branch diff. PG exercised via targeted disposable-cluster round-trip (house recipe) rather than daemon-dependent Release Gate.

## 8. Gaps / Limitations

- base-p8's first turn was a junk report (zero tool calls) — re-dispatched once per escape valve; attempt 2 delivered full evidence. No other stranded workers (32/33 dispatches first-try).
- Test-level green ≠ live: daemon restart required to activate the guard (restart-pending convention); post-restart validation (boot log `[ResponseValidation] empty_response_guard=...` + `[Config]` cap line) belongs to the activation runbook, not this gate.
- The lane defect (D1) means error_type labels during a real empty-guard incident will read `execution_error` until the 1-line fix lands — flagged to leader for routing before or right after merge.
- P-12 in-suite F↔E mode shifts across xdist runs (httpx env class) are run-to-run variance within a documented family; the 14 pre-existing classifications are robust (each fails at base in ≥1 mode).

## 9. Documentation updated

- [x] RESULTS/2026-09-12-empty-response-guard-verification.md (this file)
- [x] PACKS.md — gate block + ad-hoc pack registrations
- [x] QUARANTINE.md — 3 rows (gate-consolidated superset row; b1_wc env defect; configure_reset flake)
- [x] LESSONS/2026-09-12-empty-guard-lane-name-matching.md (name-string vs subclass contract + exact-dict-pin lesson)
- [ ] rules/ensure.md — no changes (user-maintained, contradiction scan clean)

**Overall: P1 scenarios ✅ (a-k incl. closed gaps) · Full-suite attribution ✅ (235 pre-existing / 1 branch-caused → fixed) · Streaming ✅ · PG round-trip ✅ · Open Q4 ✅ · Perf ✅ · FE-adjacent ⚠️ (pre-existing FE gap, reported) · ensure.md Core 8/8 ✅ — TESTING COMPLETE: READY (merge + restart-activate; route D1 1-line fix).**
