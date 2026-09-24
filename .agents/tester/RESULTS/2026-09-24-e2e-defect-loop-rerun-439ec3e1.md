# E2E Re-Run Report: DEFECT-5 / DEFECT-1 defect-loop fixes — fix/watch-notify-delivery-gaps @ 439ec3e1
Date: 2026-09-24 (session 03:25Z–04:2xZ)
Branch: fix/watch-notify-delivery-gaps (fix `1e12944a` + e2e-align `89e4b397` + A1 pin `439ec3e1`, base `5f4e35b0` = latest)
Method: identical rails to the baseline E2E (RESULTS/2026-09-24-e2e-job-watch-replay-5f4e35b0.md) — real dev.sh daemon on ensemble_dev, real Ari (instance d179794c, fresh), real LLM (agentic @ llm.ensem.dev/v1); Ari = actor AND judge; every verdict reconciled byte-level against DB ground truth + daemon log.

## FINAL VERDICT

| Item | Verdict |
|---|---|
| 1. DEFECT-5 (mid-flight ⟳ delivery) | ✅ **CLOSED live** — ⟳ delivered (+36ms), watcher survives non-terminal, terminal still exactly-once |
| 2. DEFECT-1 (task-kind Result: block) | ❌ **NOT CLOSED live** — DEFECT-1b filed (unit-green, production path diverges) |
| 3. Intent5/F6a live gates | ✅ **PASS** 1/1 — clean-text result_summary on all 4 emission surfaces |
| 4. Replay invariants (light regression) | ✅ **PASS** — delta-arm verbatim, zero replay, flip-probe silent, watchers 0 |
| Mechanical (pins + 3 full dirs) | ✅ **zero branch-caused reds** — every red adjudicated pre-existing/known (incl. A/B confirmation for 5 usvc reds) |

## Item 1 — DEFECT-5 CLOSED (window 03:35:07–03:39:24Z)
- J1 (coder, real 40-line work + genuine mid_flight_report w/ CHECK5A), subscribed `['mission_terminal','midflight_report']`:
  - ⟳ **delivered**: `[JOB_EVENT] Job 8bd11e5d... mid-flight report ⟳ / Agent: coder / Result: CHECK5A: …` (message_queue row aea0956c; emission→delivery **+36ms**; marker byte-verified in body).
  - Terminal **exactly-once** ~15s later (f7baceac); watcher row **survived the ⟳** (read-only matching bucket; CAS claim only at terminal). job_watchers end = 0.
- J2 (in_progress-subscribed): ⟳ correctly NOT delivered (kind separation = **KNOWN DEFECT-6**, adjudicated per rules); its midflight emission exists (event row 2434, CHECK5B); terminal exactly-once.
- Zero duplicates, zero cross-talk. Ari-vs-DB byte-level agreement throughout.

## Item 2 — DEFECT-1 NOT CLOSED live (critical finding, DEFECT-1b) (window 03:45:58–03:55:56Z)
- T1: task-kind (job_type=task), worker reply `RESULTWORD7` present in Task.result AND returned by the API resolver (job_get).
- Terminal [JOB_EVENT] body **byte-exact 57 bytes**: `[JOB_EVENT] Job 4a7236e2... completed ✓\n  Agent: worker` — **NO Result: line** (hex-verified; contains "Result:" = False).
- Reconciliation: fix helper `_parse_task_result_summary` (work_resolver.py:798-811, 'content'-key extraction) IS in the running code; unit pin `test_completed_task_kind_renders_clean_result_text` PASSES from source; API surface works. **Production watcher-notify path diverges.**
- Leading hypothesis (worker diagnosis): **pre-commit visibility gap** — [JOB_EVENT] enqueued at 03:46:43.987, Task.completed_at = 03:46:44.047 (**notify fired 60ms BEFORE the Task row commit was visible** to the resolver read) → task.result empty at read → effective_result=None → no Result:. Fix direction: thread `result_summary=` explicitly at the notify call site (as the observer path does) or read the in-memory complete_task return. Report-only per protocol.
- Continuation observation: `job_continue` mints job_type=message (not task) — structural note, by-design M3 lane applies.

## Item 3 — Intent5/F6a PASS (1/1, both runs green)
`tests/e2e/test_result_summary_emission.py::test_result_summary_emission_surface_intent5` PASS vs live daemon + real LLM (20.69s / 16.09s re-run). Clean-text result_summary verified on all 4 surfaces: SSE `event: completed` data.result_summary; GET /api/jobs/{id} (resolver, 278/633 chars, no `{`-prefix); DB event row job_completed; notifications collector. No B1-heuristic false-fail (agent text starts "Acknowledged. …"). Queue clean pre/post. NOTE (HEAD drift observed mid-run): developer's A1 pin landed as `439ec3e1` during the live round — same branch, test file unchanged since `89e4b397`; verdict stands.

## Item 4 — Replay invariants PASS
- Delta-arm verbatim: `Mission d0d3eec3... is already terminal (completed). Armed 0 live receipt(s), 2 already-settled receipt(s) skipped — no historical replay.`
- ZERO replay of settled receipts; exactly ONE [JOB_EVENT] in window (T1 terminal); flip-probe (post-03:48:41) → ZERO new events; job_watchers end 0/0.

## Mechanical round (branch @ 439ec3e1, after A1 landed)

| Pack | Result | Adjudication |
|---|---|---|
| Pin suites (defect5 ×6 + defect1 ×7, incl. A1 `test_dual_terminal_kind_settles_mid_mission_delivers_once_with_claim`) | ✅ **13/13 PASS** (7.35s) | All M2-gate + result-arm pins green |
| Full tests/job_queue/ | 🟢 13F/1,872P/39S of 1,924 (54.7s) | **0 branch-caused**: 11 on developer's list + wedge flake (secondary baseline) + 1 phase2 observer-skip TWIN (`test_observer_completion_then_termination_skips_termination`) — **stale-list undercount** (my 2026-09-23 baseline @ ac789d45 had this exact node+signature; file untouched by branch; family is ×2 not ×1). Watcher-taxonomy settled-literal family NOT fixed by branch (counts unchanged) — corrected expectation noted |
| Full tests/unit/services/ | 🟢 modulo known: 13F/1,900P (48.7s) | 8 = exact baseline (proxy_phase1 ×7 + env-defect ×1); **5 A/B-CONFIRMED pre-existing at base 5f4e35b0** (byte-identical signatures; 3 = env-defect hardcoded macOS REPO_ROOT fallback in anti-drift tests — PASS with REPO_ROOT set on BOTH legs; 2 = genuine lineage mismatches). test_work_resolver.py **76/0 green** (branch lane clean) |
| Full tests/unit/tools/ | ✅ 2,841P/0F/6S/5-des (48.3s) | +1 growth = A1/B2 pin; all green |

**Triage-list gap findings for the developer:** (a) observer-skips-terminated family is ×2 (add the phase2 twin); (b) unit_services carries 5 unlisted pre-existing reds (3 of them fixable as test-infra: derive REPO_ROOT from test file parents, not the macOS author fallback).

## Defect ledger (this round)
- **DEFECT-1b (P1→P2, NEW)**: DEFECT-1 fix unit-green but production task-kind terminal watcher bodies still lack Result: — pre-commit visibility gap hypothesis; needs explicit result_summary threading at the notify site. NOT fixed, NOT committed (report-only).
- DEFECT-6 (carried, P3): in_progress ≢ midflight_report semantic separation (observed again on J2; adjudicated known).
- Triage-list gaps (a)/(b) above.

## Safety ledger
- Ambient POSTGRES_* (LIVE ensemble_prod) scrubbed in every worker lane; engine line ensemble_dev verified; log-wide 0 hits for prod leakage.
- 9797 (LIVE) / 7979 (demo) / 8088 untouched throughout; dev daemon on 8079 only.
- .env: backed up (sha256 11db2814…), real-LLM combo for the round, **restored at teardown** (sha256-verified).
- No code fixes, no production edits; `latest` lineage untouched (branch only); artifact commit = tester RESULTS only (below).
- Parked f27e2d15 (commit-hash reference): untouched.

## Teardown
Dev daemon graceful shutdown on single TERM to uvicorn 995759 (all 5 workers drained failed=0; EventBus/manager disposed; "Graceful shutdown complete"); port 8079 freed, full tree exited incl. MCP grandchild. LIVE 9797 (pid 819745) + demo 7979 (pid 800677) alive with identical lstart to baseline — untouched. `.env` restored to mock found-state (sha256 exact match 11db2814…; real-LLM state during round was b7a284ec…f889, recorded). Git: zero tracked modifications; branch fix/watch-notify-delivery-gaps @ 439ec3e1 intact. Evidence preserved: /tmp/e2edr-dev-daemon.log (206KB), env backup, A/B logs (usvc-branch-A / usvc-base-B).
