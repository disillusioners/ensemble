# Test Report: message-display-latency — FINAL MERGE GATE

Date: 2026-08-31
Branch: `feature/message-display-latency` @ `b8c7a611` (full: b8c7a6114d04b2f609fbffbfec79b9b5a1ad03a3; merge-base vs `latest`=97b0f0b3, 8 commits ahead)
Instance IDs (workers): aa677a40 (injection pack), 4cc69cff (injection compaction), 7ef18ccc (sweep A), 171d7cac (sweep B), ff957948 (base-attribution), 29ca62ea (ensure concurrency), c6fc53f4 (FE jest), c0d92c31 (FE build), 476a687c (mock fidelity), 1661f07d (ensure static), 16ef0fd2 (e2e browser)
Repo posture: READ-ONLY honored — zero source/test modifications, zero commits, worktree base-evidence removed. 10 dispatches, ≤3 concurrent.

## VERDICT: ✅ PASS — CLEARED FOR MERGE

**Original symptom (variable/long delay before typed message displays) — GONE.**
Measured time-to-display: **fast path (200): 161 ms; injection path (202): 56 ms** (target < 1000–2000 ms; pre-fix injection path was seconds→minutes = remainder of the in-flight turn).

---

## Scope Decision

Full verification explicitly requested as the final merge gate; blast radius honored by running the feature's own packs (injection ×8 files) + messaging-adjacent sweep (bounded, 2 packs) + FE full suite/build + mandatory web automation. Full-suite Release Gate NOT triggered: additive contract change, 3 approved review cycles, task bounds runtime; ensure.md Core validated in full. All backend packs ran as ad-hoc packs (repo read-only → no new pack scripts committable) with dual-layer timeouts (`timeout 300` + pyproject per-test timeout=30).

## §1 Backend regression — PASS (0 new regressions)

| Pack | Result | Counts | Notes |
|---|---|---|---|
| injection_merge_gate (7 files: test_injection_{api,sse,graph,slot,cleanup}.py, unit/graph/test_injection_tool_pairing.py, integration/test_injection_echo_id_continuity.py) | ✅ PASS | 132P/0F/0S, 1.77s | branch gate verified; 3 pre-existing SAWarnings (fixture engine, informational) |
| injection_compaction_unit (8th injection file discovered by sweep A: tests/test_injection_compaction.py) | ✅ PASS | 7P/0F, 0.51s | closes the leader-list gap (7 listed, 8 exist) |
| messaging_adjacent_sweep_a (15 files: instance-messaging ×5, routers/message-status, context-messages, live_event_hub, llm-streaming ×2, notification-sse, todo-sse, integration api_messages/message_queue_e2e/workspace_sse) | ✅ PASS (effective) | 279P/3F, 3.57s | 3F = SQLite-migration cascade quarantine family (MigrationError 20260714_000001, deterministic, predates branch) — tests/integration/test_message_queue_e2e.py ×3 |
| messaging_adjacent_sweep_b (tests/job_queue/ + unit/repositories/test_job_queue_atomic_transition.py + unit/services/test_job_queue_proxy_phase1.py) | ✅ PASS (effective) | 1589P/8F/38S, 36.7s | tests/job_queue/ = 1569P/0F/38S EXACT baseline; 8F = job_queue_proxy_phase1 family, **base-attributed PRE-EXISTING** (see below) |

**Base-attribution (worktree @ latest=97b0f0b3, removed after):** job_queue_proxy_phase1.py → 10P/8F at base, failing-test set and assertion signatures verbatim-identical to HEAD (all 8: `'pending' == expected` WorkResolverService Phase-1 derived-status/timing fallback). Branch exonerated; family already registered in QUARANTINE.md "Misc pre-existing drift cluster" (row re-verified stamp added). Graph-adjacent subset was structurally empty (tests/unit/graph/ contains only test_injection_tool_pairing.py, covered).

## §2 Frontend full suite — PASS

- **Jest (CI one-shot)**: 58/58 suites, **2150/2150 tests**, 0 failed/skipped, 7.1s (`CI=true npm test`; log /tmp/fe_jest_full.log). Key specs all PASS: message-merge.util.spec.ts, sse.service.spec.ts (incl. N2 real-service block), chat.component.spec.ts.
- **Production build**: exit 0, 12s (`ng build`). Warnings 9, ALL pre-existing baseline (NG8113 ×1, Sass ×2, bundle initial ×1, any-component-style ×6); **0 new**. Log /tmp/fe_build.log.

## §3 Mock fidelity — verdicts

- **(a) N2 spec vs production connect(): DRIFT (partial, non-blocking).** N2 exercises the REAL SseService (import :3, instantiated :827) and correctly pins connect()/connectInternal early-return + error-latch semantics (spec:838-860 ≙ sse.service.ts:237-241, :592-599). But event-name/payload/URL coverage lives on the `TestSseService` surrogate, which has stale pins: a `'checkpoint'` listener production no longer wires (spec:177-197 asserted at :332-386) and minor copy drift (role default, images filter, isStreaming placement). Production `injection_pending`/`injection_consumed` handlers have ZERO FE spec coverage.
- **(b) echo_id_continuity integration: REAL-GRAPH CONFIRMED.** Real langgraph + StateGraph + MemorySaver + compiled with checkpointer (test:135-169); real `create_agent_node` drain from daemon.graph (:2716 → :2994 id=echo_id, :3122-3158 mint-once, :3162+ same-id+stamp re-emit); only LLM/slot/hub stubbed; checkpoint read via production `get_instance_messages`. NOT vacuous. (POST hop lives in test_injection_api.py with mocked manager — chain stitched across 2 files.)
- **(c) FE send-stubs vs ApiService.sendMessage: MATCH.** 202 stub `make202Response` ≙ messages.py:482-500 key set exactly (message_id always present — minted :415); PAUSED-200 message_id=None path (:352) tolerated by FE types (`message_id?: string | null`, models/index.ts:128); absent-message_id degradation SPEC-COVERED (spec:1227-1240 → no optimistic append). Minor: TestableChatComponent surrogate sendMessage is 3-arg vs production 4-arg (queue_id) — a queue_id-forwarding regression would pass specs; `message_id: null` literal branch untested; component-level reconnect merge-refetch wiring (chat.component.ts:439-443) unpinned by specs.

## §4 Web automation (MANDATORY) — 4/4 PASS

Setup: fresh BE via ./dev.sh (8079, livez+readyz 200) + fresh FE via npm start (4199); Playwright 1.60 + Chromium; agent `worker`; instances mdl-e2e-a/mdl-e2e-b (both terminated after); 21 screenshots + RESULTS.json + console logs in /tmp/mdl-e2e/.

| Scenario | Result | Key measurements |
|---|---|---|
| A — fast path (200, IDLE) | ✅ PASS | send→bubble **161 ms**; 1 occurrence; user above assistant (no reorder); assistant reply 39.7s (not gated) |
| B — injection path (202, RUNNING mid-turn) — the original SLOW case | ✅ PASS | injection→bubble **56 ms**; mid-turn confirmed (running in 823ms + tool activity); drain 164s → completed; exactly 1 bubble; assistant replied `ACK-B-zjb8vl` literal |
| C — reconnect during pending | ✅ PASS | bubble **90 ms**; CDP offline 5s → online; bubble survived (count 1) through reconnect AND drain; `GET /messages` cross-check = exactly 1 user msg with content |
| D — stale-tick / cross-instance bleed (chat.component.ts:411-419) | ✅ PASS (caveat) | bubble **102 ms**; offline → switch to B via routerLink → reconnect: **0** textD occurrences in B's chat (no bleed), A intact on return (dInA=1 verified with 8s hydration wait); no cross-state console errors |

**Scenario D caveats (honest):** (1) first-run dInA=0 was harness polling (3s < 8s needed to hydrate 56 messages) — post-hoc verify confirmed dInA=1; the dInB=0 key assertion was correct throughout. (2) Network log shows **0 refetches** fired during D — the reconnect refetch-effect was a no-op in this run (no SSE error→connected transition armed at the layer that drives refetchRequest), so the no-wrong-instance-refetch property was satisfied without the armed path actually firing. Combined with §3(c)'s unpinned wiring, recommend a follow-up spec pinning refetchRequest→merge-refetch (🟠 note, non-blocking).

## §5 ensure.md (Core, blast-radius scoped) — PASS 6/6 (+1 n/a)

- Critical 1 — no regressions in changed packs: ✅ (all scoped packs PASS; failures only in quarantine families, base-attributed)
- Critical 2 — concurrency_atomic: ✅ 98P/0F/74S, 9.45s — EXACT baseline
- Critical 3 — no sync DB on event loop: ✅ (same pack, thread-identity tests)
- Critical 4 — dev.sh `--timeout-graceful-shutdown 10`: ✅ executable at dev.sh:102 (grep also matches comment :99 — presence requirement satisfied; no contradiction)
- Important 1 — async callers awaited: ✅ 8/8 call sites (`_get_system_prompt_tokens` ×2, `_compute_context_usage` ×1, `get_queue_stats` ×5)
- Important 2 — original deadlock scenario: ✅ via concurrency pack
- Nice-to-have (dead-code import check): not run — informational only
- Release Gate: NOT triggered (scope decision above). No ensure.md contradictions found → no Improvement Notices.

## Quarantine status

No new quarantines. Existing families re-verified, not aggravated: SQLite-migration cascade (3 hits in sweep A, known), Misc drift cluster job_queue_proxy_phase1 ×8 (base-attributed at 97b0f0b3 today; QUARANTINE.md row stamped). Archive-lifecycle ×5 skipped as instructed. Quarantined-skip impact on this gate: 16 tests excluded from verdicts, all pre-existing.

## Follow-ups (non-blocking, for leader)

- 🟠 Pin reconnect refetch wiring at component level (refetchRequest → loadInstanceMessages(merge:true)) — unpinned by specs, and e2e D never observed it fire.
- 🟠 FE spec debt from §3(a): remove/replace surrogate 'checkpoint' stale pins; add injection_pending/injection_consumed coverage on the real service.
- 🟢 Single-flow continuity test (POST echo id == 202 body id == drain id == GET id in one live path) — currently stitched across 2 files.
- 🟢 FE testability: add data-testid hooks; document SEND_COOLDOWN_MS=3000 (silent drop of rapid second send) and 8s chat-hydration wait for e2e authors (LESSONS/2026-08-31-mdl-e2e-workarounds.md).
- 🟢 job_queue_proxy_phase1 ×8 (Phase-1 WorkResolver acceptance suite red at latest) — owning area should fix or formally retire.

## Overall Status

- §1 Backend regression: ✅ PASS (0 new regressions, 2 base-attributed quarantine families)
- §2 Frontend full suite + build: ✅ PASS (2150/2150; build clean, 0 new warnings)
- §3 Mock fidelity: ✅ (b) CONFIRMED real-graph; (c) MATCH; (a) DRIFT-partial — coverage debt only, no contract mismatch
- §4 Web automation: ✅ 4/4 scenarios PASS
- §5 ensure.md Core: ✅ 6/6
- **Original symptom: GONE** — 161 ms (200) / 56 ms (202)
- **FINAL: ✅ PASS — CLEARED FOR MERGE**
