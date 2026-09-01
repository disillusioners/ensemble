# Re-Gate Report: slash-commands `/compact` — defect-fix verification @ 9eb1b67e

Date: 2026-08-31 (15:00–17:00 UTC)
Branch: `feature/slash-commands` @ `9eb1b67e` (fix commits BE 85ba2250/04139030/795bac07/f9d377b9, FE 34c08746/42da3b92/9eb1b67e)
Prior report: RESULTS/2026-08-31-slash-commands-compact-e2e-gate.md (CONDITIONAL PASS @ 235650f1)
Posture: repo READ-ONLY, report-only. 9 dispatches this re-gate (recon, stack-restart reuse, 6 BE packs, Jest, PW, ensure, live1, live2, live3), ≤3 concurrent pytest, dual-layer timeouts, stack cleanly restarted at HEAD before all live checks.

## VERDICT: ✅ SHIP — all 7 targeted defects verified FIXED (live evidence); all suites exact-green; 2 NEW follow-up findings (non-blocking, leader triage)

## Per-defect results (all live-verified at 9eb1b67e unless noted)

| # | Defect (prior severity) | Verdict | Key evidence |
|---|---|---|---|
| 1 | 🔴 pause→resume stuck + queued jobs dropped | **PASS** | Auto-resume in **0.42s** (no manual /resume); jobs before/during/after: pending 1/0/0, active 1/1/0, done 50/50/50, **dropped = 0**; all 8 mid-pause messages persisted + replies enumerate all eight; instance completed cleanly. A REAL compaction also ran (timed_out→fallback_applied, 141,593→114,300 tokens, forced=true, truncation-marker system row visible). F2 note adjudicated: lifecycle test #3 indeed MIRRORS claim_pending_task (in-process list); tests #1/#2 pin resume on real graph+SQL; this live run is the authoritative SQL-path check. Artifacts: /tmp/regate/ (TIMELINE.md, jobs4-*.json, ack-a4.json, sse logs) |
| 2 | 🟠 terminal reject delayed ~174s | **PASS** | t_ack **13.6 ms** (was ~174,000 ms); full §7 envelope: `state:"rejected"`, `reason:"terminal_instance"`, guidance verbatim "Send a message to start a new turn, then /compact."; zero command_progress SSE events; status stayed completed. ack-b.json |
| 3 | 🟠 GET /commands/active 500 race | **PASS** | **1,718,038 GETs, 100% HTTP 200, zero 5xx** across 2 hammers (75.3s + 60.3s, 6 GET loops + 3 POST loops, ~9.4k GETs/s, p99 1.06ms) + expired-ring spike; the original trigger condition (≥2 expired ring entries) hit live → 200 `{exists:false}` (pre-fix: 500). /tmp/regate-hammer/ |
| 4 | 🟠 escape-path double-insert | **PASS** | Netlog (final arbiter): 2 trials, each **exactly 1 POST** with RAW wire body `{"content":"//compact is useful"}`; stored row singular, stripped `/compact is useful`; **no same-µs twin**. def4-netlog.json, def4-verdict.json |
| 5+F1 | 🟠 dishonest bubble / retry content | **PASS** | Forced-failure (route-abort) → failed bubble `[data-message-failed=true]` + Retry/Dismiss panel `role=alert`; Retry → **exactly 1 POST** with RAW `//escape retry probe` (retry_content stash verified); assistant reply delivered; UI-vs-DB bubble counts honest (SYSTEM-CONTEXT row excluded by design). def5-*.png/json |
| 7 | 🟢 ack stall ~32s on RUNNING | **PASS** | t_ack **2.5 ms** (≤500ms); SSE `waiting` at **ack+1 ms**, instance→paused at ack+464 ms — waiting strictly precedes pause work. ack-a4.json + timeline_a4 |
| 6, 8 | deferred by leader | N/A (no action) | — |

## Regression sweep at HEAD (exact commands, all from repo root)

| Suite | Command | Result |
|---|---|---|
| routers | `timeout 120 .venv/bin/pytest tests/unit/routers/test_slash_commands_router.py --tb=short -q` | **40/40** PASS (1.48s) — +15 new pins: 6 single-write-per-state-branch (#4 BE), terminal-reject-at-ack ×4 statuses + no-task/no-rate-burn/gate-order (#2), waiting-hoist ack+waiting-while-pause-blocked (#7) |
| dispatcher | `timeout 120 .venv/bin/pytest tests/unit/services/test_command_dispatcher.py --tb=short -q` | **76/76** PASS (1.90s) — +12: OrderedDict-snapshot ≥2-expired (#3), terminal reject at dispatch ×4 + RUNNING/IDLE/PAUSED accept control |
| executor | `timeout 120 .venv/bin/pytest tests/unit/services/test_compact_executor.py --tb=short -q` | **41/41** PASS (7.11s) — +1: RUNNING-noop waiting→success (#7) |
| revive-brick | `timeout 240 .venv/bin/pytest tests/unit/services/test_compact_executor_revive_brick_e2e.py --tb=short -q` | **5/5** PASS (1.13s) — diff analysis: fix commits additive-defensive; guard/brick/Variant-A pins intact |
| lifecycle (NEW) | `timeout 120 .venv/bin/pytest tests/unit/services/test_compact_executor_defect1_pause_resume_lifecycle.py --tb=short -q` | **3/3** PASS (1.22s) — resume-in-finally pins; F2 caveat documented |
| compaction | `timeout 180 .venv/bin/pytest tests/unit/test_compaction.py --tb=short -q` | **92/92** PASS (2.90s) — `git diff 235650f1..9eb1b67e --stat -- daemon/compaction.py` = EMPTY (engine untouched) |
| **BE total** | | **257/257** — exact expected |
| FE Jest | `cd frontend && CI=1 timeout 240 npm test` | **62 suites / 2273 tests** PASS (7.0s) — exact; +17 = #5 retry UI ×13 + F1 raw retry_content ×4 (chat.component.spec.ts) |
| Playwright | `CI=1 timeout 280 npx playwright test e2e/slash-command-compact.spec.ts --reporter=line` | **15/15** PASS (114s) — O17 keepalive ~38s by design; servers reused/untouched |
| ensure.md Core | `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` + grep dev.sh | **98P/0F/74S** baseline-exact (10.6s); dev.sh:102 flag intact; C1 satisfied by sweeps. Core = green at HEAD |

Quarantine: `test_job_queue_proxy_phase1.py` ×8 excluded (no overlap). Stack cleanly restarted at 9eb1b67e before live work (fresh PIDs, uptime 4.5s); 8088 untouched throughout; LLM-LIVE all runs.

## Original scenario re-check at HEAD: (a) PASS (card lifecycle → success/noop) · (b) PASS (list refetch consistent; real-compaction truncation-marker row verified in live1's authoritative run) · (c) PASS (post-compact reply with correct memory — no brick) · (d) PASS (RAW // single-POST, literal render) · (e) PASS (inline error, input preserved, available list).

## NEW findings (report-only — NOT in leader's defect list, surfaced for triage)

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| N1 | 🟠 | **Compaction outcome mapping miss**: one accepted compact terminalized `phase=failed, reason=unknown_compaction_type` while `engine_compacted_type=summarization` — executor mapping table lacks a "summarization" value (unit tests pin summary/partial_summary/truncation/emergency only). Same event showed **negative tokens_saved** (41,865→48,629 — compaction GREW context). 1 occurrence / 3 accepted compacts under hammer load. Candidate BE defect (mapping vocabulary vs engine emission, or engine emitted an anomalous result under concurrency). | /tmp/regate-hammer/ (seed-compacts.jsonl, command 7c78a141 vicinity) |
| N2 | 🟠 | **202-accepted-but-never-persisted message**: reproducible — after an instance completes its reply, re-sending the same content returns HTTP 202 `{status:"injected", message_id}` but the row never appears in GET messages (25s+ poll). Consistent with parked-injection deferral + no wake-up (ENSEMBLE_WC_WAKE_ENQUEUE default OFF, known kill-switch state) on a COMPLETED instance that never dispatches again. User-visible silent loss in that state. | /tmp/regate-live3/def4-netlog.json trial 2; corroborated by live1 anomaly #1 |
| N3 | 🟢 | Command ring not evicted on instance terminate — GET /commands/active keeps serving 200 `{exists:true}` for terminated instances until daemon restart. | /tmp/regate-hammer/ |
| N4 | 🟢 | /api/jobs does not mirror injection-lane queueing (pending count blind to parked messages); noop-floor estimator reads resolved window (30k floor) while excluding synthetic scope content — tuning-adjacent (#8 family). | /tmp/regate/ |
| N5 | 🟢 | Playwright noise under concurrent browser workers: SSE reconnect churn + NG0100 dev-mode-only Angular warning — non-failing, known. | pw run log |

## Gaps
- None blocking. N1/N2 are the only items I'd want a leader decision on (pre-merge fix vs post-merge follow-up ticket). F2's mirrored-SQL caveat is closed by the live #1 run (authoritative).

## Documentation Updated
- [x] RESULTS/2026-08-31-slash-commands-regate-9eb1b67e.md (this file)
- [x] PACKS.md — re-gate gate entry
- [ ] QUARANTINE.md — no new quarantines

## Code Changes Summary
None — repo READ-ONLY; zero commits by any worker this re-gate.

## Overall Status
- Defects #1 #2 #3 #4 #5+F1 #7: **all PASS (live evidence)**; #6/#8 deferred per leader
- Regression: BE 257/257 · Jest 62/2273 · Playwright 15/15 · concurrency 98/0/74 · engine untouched
- ensure.md Core: green at HEAD
- **Final verdict: SHIP** — with N1 (mapping miss / negative tokens_saved) and N2 (202 silent loss on completed instance) flagged as recommended follow-ups (leader triage).
