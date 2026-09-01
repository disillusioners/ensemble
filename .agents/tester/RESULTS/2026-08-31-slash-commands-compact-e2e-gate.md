# Test Report: slash-commands `/compact` — END-TO-END Gate

Date: 2026-08-31 (12:22–13:23 UTC)
Branch: `feature/slash-commands` @ `235650f1` (BE 59951b8f→f82a9379, FE 0610773f→235650f1)
Instance IDs (dispatched workers): 39c707b6 (recon), 9595c049 (routers), d2a999c2 (dispatcher), a2a8df65 (executor), 4ce77b42 (revive-brick), f34a67b5 (compaction), 1f9b5c0a (mock audit), 8f9f7d60 (stack-up), 894b1384 (fe-jest), 412c5853 (e2e-pw), f5cb95d6 (ensure-core), 07d174d5 (scope3-live)
Posture: repo READ-ONLY, report-only (per leader: bugs reported, NOT fixed). 12 dispatches, ≤3 concurrent, dual-layer timeouts on every pack.

## Summary

| Area | Result | Counts |
|---|---|---|
| Scope 1 — BE regression (5 packs) | ✅ PASS | **226/226** (25+64+40+5+92) — exact leader baseline |
| Scope 1 — mock fidelity | ✅ PASS | 5/5 suites MATCH real behavior; no green result invalidated |
| Scope 2 — FE Jest | ✅ PASS | 62/62 suites, **2256/2256** tests — exact baseline |
| Scope 2 — Playwright spec | ✅ PASS | **15/15** (110s; O17 keepalive ~38s by design, passed) |
| Scope 3 — live original scenario | ⚠️ PASS with caveats | 4/5 PASS + 1 PARTIAL; **8 defects documented** (1 HIGH) |
| ensure.md Core | ✅ PASS | 6/6 (incl. concurrency pack 98P/0F/74S baseline-exact) |
| **Overall** | **CONDITIONAL PASS** | All suites green; live bugs #1–#8 need leader triage before merge |

## Scope Decision

Full feature-scope gate (new cross-stack subsystem, BE+FE, 8 commits) — full feature scope warranted per blast radius. Within it, scope was tightened to the leader's stated baseline: compaction scope (e) = `tests/unit/test_compaction.py` (92) ONLY; ~100 adjacent compaction-family tests (multimodal ×30, inner_soul ×42, fired_watchers ×13, instance_messaging_guard ×8, injection_compaction ×7) exist on the branch but were NOT touched by feature commits → skipped (noted, not run). Quarantined `test_job_queue_proxy_phase1.py` ×8 excluded (QUARANTINE.md misc cluster) — no overlap with any pack.

## Scope 1 — Backend regression (all commands from repo root)

| Pack (ad-hoc) | Command | Result | Runtime |
|---|---|---|---|
| routers intercept | `timeout 120 .venv/bin/pytest tests/unit/routers/test_slash_commands_router.py --tb=short -q` | PASS 25/25 | 1.33s |
| command dispatcher | `timeout 120 .venv/bin/pytest tests/unit/services/test_command_dispatcher.py --tb=short -q` | PASS 64/64 | 1.91s |
| compact executor | `timeout 120 .venv/bin/pytest tests/unit/services/test_compact_executor.py --tb=short -q` | PASS 40/40 | 6.96s |
| revive-brick e2e | `timeout 240 .venv/bin/pytest tests/unit/services/test_compact_executor_revive_brick_e2e.py --tb=short -q` | PASS 5/5 | 1.19s |
| daemon compaction | `timeout 180 .venv/bin/pytest tests/unit/test_compaction.py --tb=short -q` | PASS 92/92 | 2.91s |

**Mock quality (leader's explicit concern) — verdict: mocks MATCH real behavior; no invalidation.**
- Real ground truth pinned by the revive-brick suite itself: real LangGraph + file-backed `AsyncSqliteSaver` (`_RealLangGraph` swap, `test_compact_executor_revive_brick_e2e.py:57-88`); real terminal checkpoint = `next==()`; real paused-mid-graph = `next==("agent",)`.
- **No mock fabricates `next=("agent",)` on a success path.** All success-path fixtures use the genuine quiescent `next==()` shape; the single non-empty-`next` fixture (executor suite :1370) is (a) the real shape for a RUNNING instance frozen mid-graph and (b) outcome-irrelevant (executor gates on instance status + quiescence probe, never on `.next`).
- Success paths anchored to real graph: B-suite canary `test_compact_succeeds_on_quiescent_instance` (B:608) proves a real quiescent instance compacts and the next `astream` runs — the anti-brick (Variant A) invariant, plus `test_compact_persists_and_next_turn_runs_agent` proves the persistence recipe doesn't brick the live checkpointer.
- Non-checkpoint mocks verified against real surfaces: manager methods exist (`manager.py:2564` etc.); SSE mock signature-matches `live_event_hub.stream_message` (`live_event_hub.py:150-156`); compaction suite mocks only the LLM — correct seam (engine imports zero checkpoint machinery).
- Earlier interim claim "truncation-marker ABSENT from daemon/" was **wrong** (verbatim plan-string grep false-negative; see LESSONS/2026-08-31-verbatim-plan-string-grep-false-negative.md). Marker EXISTS: `_append_truncation_marker` `daemon/compaction.py:105-133`, id `truncation-marker-{uuid4()}` (`:131`), both paths route through it (`:1481` partial, `:1537` truncate), exactly-once pinned (`test_compaction.py:1461/:1485/:1918-1931`). **No plan-vs-impl deviation.**
- Envelope naming: plan text `detail:{available}` is imprecise; implementation is `detail: {code, message, details: {available: [...]}}` — **BE (messages.py:259-267) ↔ FE (api.service.ts:101-118) consistent**, router test matches real wire. Not a bug.

**Edge-case matrix (12 rows, cross-suite):** terminal×4 reject+pinned guidance ✅ · unknown→400 additive envelope ✅ · rate-limit-before-gate ✅ · concurrent busy ✅ (D:422, R:465) · noop recently_compacted ✅ · noop below_floor ✅ · engine-timeout→fallback mapping ✅ · failure_kind timeout-vs-error ✅ · quiescence_timeout ✅-where (timeout variant E:1350; **minor GAP: generic-exception variant** `compact_executor.py:747-776` untested) · pending_injections ✅ · V-1 pause-cancel gate release ✅-where (structural pin per spec, E:1605-1634) · V-2 tenacity facade ~305s ✅-where (structural 300+5 pin + scaled behavioral, E:1492+).

**Engine feature coverage (7/7 in test_compaction.py):** `_summarization_timeout_s` min(cap, 90+tokens/100k·60) ✅ · `wall_clock_cap_s`=cap+5 threaded ✅ · whole-op budget 300s between LLM calls ✅ · `ChunkedOutcome` 4 stop_reasons + partial-summary preservation ✅ · force bypasses threshold only (dedup/min-messages stay) ✅ · `CompactionResult.forced/failure_kind` anti-drift ✅ · truncation marker exactly-once ✅.

## Scope 2 — Frontend

- **Jest:** `cd frontend && CI=1 timeout 240 npm test` → **62/62 suites, 2256/2256 tests, 0 failed** (7.7s) — exact baseline, zero delta. Slash suites green: command-registry, command-state, sse-command-progress, parse-command-ack, message-input.component.
- **Stack:** BE `./dev.sh` :8079 (livez 200, PG healthy, v0.11.3) + FE `npm start` :4199 (200). Started by dedicated worker; Playwright `reuseExistingServer:true` reused both; 8088 untouched throughout.
- **Playwright:** `CI=1 timeout 280 npx playwright test e2e/slash-command-compact.spec.ts --reporter=line` → **15/15 PASS**, 110s. O17 (SSE open >30s idle, keepalive) passed within window. Per-test list in worker report (SC1, SC2a/2b, SC3, SC4/5, SC6, SC7, SC9, SC13, SC14a/b/c, C1 escape, SC15, O17). Browser-console SSE reconnect noise observed, non-failing.

## Scope 3 — Original scenario (live browser, LLM-LIVE mode)

Mode: **LLM-LIVE** (api.openai.com reachable; probe reply 32s). Instance `e1206f9a…` (agent `ari`) created, verified, terminated by exact id. Artifacts: /tmp/scope3/ (11 screenshots, 3 message-list JSON snapshots, run logs/timelines).

| Check | Verdict | Evidence |
|---|---|---|
| (a) card waiting→in_progress | **PARTIAL** | `waiting` card captured live (`[data-command-phase="waiting"]`, "⏳ /compact Preparing compaction… (waiting for instance to quiesce)"). `in_progress` never rendered live — both live attempts terminated as noop (below_floor); by design in_progress emits only while the engine runs. in_progress→success IS covered by Playwright SC1. |
| (b) compaction + list refetch | **PASS on noop path; real-summary path NOT live-observable** | Terminal card: "✓ /compact Nothing to compact" (noop/below_floor; BE: compacted_type=noop, estimated_tokens=641, resolved_window=600000). List refresh correct-for-noop (no removals, no summary row); fresh reload shows phase=success, card auto-clears ~8s. Real-summary compaction unreachable live: floor = 5%×600k = 30k tokens vs ~641-token estimate (defect/observation #8). Summary path verified at suite level instead (engine 92/92 + marker tests + Playwright SC1/SC2a/SC2b mock-mode). |
| (c) subsequent-turn integrity (Variant A) | **PASS** | After /compact, normal message → assistant reply with **correct memory of the first message** ("reply with the single word 'ready'"); no brick, no collapse. (Reply delayed ~10min by bug #1 stall; 11s once resumed.) |
| (d) `//compact is useful` escape | **PASS on retest** | Literal bubble `/compact is useful` (BE strips one slash per contract), NO card, NO compaction, assistant replied conversationally. First UI attempt hit bugs #4/#5. |
| (e) `/definitelynotacommand` | **PASS** | Inline error <1s, composer preserved, no crash. API cross-check: HTTP 400 `{code:"UNKNOWN_COMMAND", details:{available:["compact"]}}`. |

## Defects Found (REPORT-ONLY — nothing fixed, per leader instruction)

| # | Sev | Component | Summary | Evidence |
|---|---|---|---|---|
| 1 | 🔴 HIGH | BE compact_executor | Mid-turn /compact pause→resume leaves instance **stuck `paused` >130s**, queue fully stalled; `resume_instance_cascade` never lands. Manual resume unblocks, but re-dispatch processes only 1 of 8 queued jobs — **7 silently dropped** (pending_count 0 after). Suspect: resume-in-finally path. | run logs 12:59:37+, updated_at frozen; /tmp/scope3/run*-result.json |
| 2 | 🟠 MED | BE | /compact on terminal (completed) instance acks `accepted`→`waiting` ~174s before `failed(terminal_instance)` — should reject at ack time per executor docstring; user watches misleading "waiting to quiesce" on an already-quiescent instance. | command d34c1026, elapsed_ms 174361 |
| 3 | 🟠 MED | BE | `GET /commands/active` → HTTP 500 `OrderedDict mutated during iteration` racing a message POST. Transient (5 subsequent probes 200). | run5-net.json t=2.1s |
| 4 | 🟠 LOW-MED | BE | Escape-path double-insert: one POST `//compact is useful` persisted 2 identical user rows (same created_at µs). Intermittent (retest = 1 row). | ids fb125533/f5739113, d-escape-api-post.txt |
| 5 | 🟠 MED | FE | Dishonest optimistic bubble: rendered bubble for a message that never reached the API (absent from list); no error shown. Suspect: composer send path swallows failed POST. | run4, verified hits:NONE |
| 6 | 🟢 LOW | FE | One render tick: card text shows new outcome while `data-command-phase` still stale `failed`. | run2 chase +3.7s |
| 7 | 🟢 LOW | BE | /compact ack took ~32s on RUNNING instance (no UI feedback during). | elapsed_ms 32081 back-calc |
| 8 | 🟡 OBS | BE/config | Noop-floor math: resolved_window 600k → 30k-token floor while estimator reads ~641 tokens (checkpoint view excludes ~60KB synthetic system/scope msgs). Real-summary compaction effectively unreachable at normal chat scale — tuning question for acceptance owner, not necessarily a defect. | post-compact-messages.json |

Bug #1 is the only merge-blocking candidate in my judgment (stuck-paused + silent job loss on the pause-first path); #2/#3/#5 are real UX/integrity issues worth fixing pre-merge; #4/#6/#7 low; #8 needs a product decision.

## ensure.md Validation (Core, blast-radius scoped)

- **Critical 4/4 + Important 2/2 = 6/6 PASS.**
  - ✅ No regressions in changed packs — 226 BE + Jest 2256 all PASS (this gate).
  - ✅ Deadlock/concurrency integrity — `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` → 98P/0F/74S in 9.97s, **baseline-exact** (prior gate 98/0/74).
  - ✅ No sync DB on event loop — thread-identity tests within pack (named: test_gate_threading_serialization.py ×5, TestH15ThreadOffload ×2).
  - ✅ dev.sh `--timeout-graceful-shutdown 10` — grep hit dev.sh:102 (active invocation).
  - ✅ Async-await static check — `_get_system_prompt_tokens`/`_compute_context_usage` zero callers; `get_queue_stats` awaited (manager.py:8313).
  - ✅ Deadlock scenario — test_deadlock_fix.py among the 13 canonical pack files, green.
- Release Gate NOT triggered (feature gate, not release; per ensure.md scoping).
- No contradictions between ensure.md methods and pack rules this run.

### Gaps
1. **Real-summary live compaction unobserved** (defect #8 floor math) — summary path verified at suite level (engine + Playwright SC1/SC2 mock-mode) only. If the leader wants a live real-summary observation, the floor knob (`SLASH_COMMANDS_NOOP_FLOOR_RATIO`) or window override must be tuned for a test instance first.
2. Quiescence generic-exception variant (`compact_executor.py:747-776`) has no dedicated test (audit row 9).
3. Bug #5's failed POST was not network-captured on the failing attempt (evidenced by API-list absence only).
4. Factsheets #2–#8 (scope-3 bulk payload) lost to bug #1 aftermath, not re-sent.

## Code Changes Summary
None — repo READ-ONLY gate; zero commits, zero source edits by any worker. (Pre-existing `.agents/*` dirt untouched; frontend/test-results/.last-run.json is a Playwright artifact.)

## Documentation Updated
- [x] RESULTS/2026-08-31-slash-commands-compact-e2e-gate.md — this report
- [x] PACKS.md — gate entry added
- [x] LESSONS/2026-08-31-verbatim-plan-string-grep-false-negative.md — grep false-negative lesson
- [ ] QUARANTINE.md — no new quarantines (no flaky tests observed)
- [ ] MOCK_TESTS.md — no new mock tests registered this gate (stack-driven scenario, ad-hoc scripts in /tmp)

## Overall Status
- Scope 1 (BE regression): ✅ PASS 226/226, mocks faithful
- Scope 2 (FE): ✅ PASS 2256/2256 Jest + 15/15 Playwright
- Scope 3 (original scenario): ⚠️ PASS with caveats (4/5 PASS, 1 PARTIAL; 8 defects, 1 HIGH)
- ensure.md Core: ✅ 6/6
- **Testing verdict: CONDITIONAL PASS — suites fully green and mock-verified; leader must triage defect #1 (merge-blocking candidate) and #2/#3/#5 before merge; #8 needs a product decision on noop-floor tuning.**

Environment notes: stack left running on :8079/:4199 (held by stack-up worker instance 8f9f7d60 — terminate it to stop the stack). Port 8088 untouched at all times. LLM endpoint LIVE (no mock-mode fallback needed).
