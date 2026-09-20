# Final Verification Gate — Watch-Notify Engine (Shape b: Event-Driven Completion)

- **Date**: 2026-09-20
- **Branch**: `feature/watch-notify-engine` @ `b0d9ff5e` · **Base**: `b0a4f21d` (ANCESTOR-OK verified)
- **Mode**: VERIFY-ONLY — 0 commits, 0 repo file modifications, 0 daemon boots, 0 prod restarts. Both disposable worktrees porcelain-clean at close (sha256-verified restore on the one sanctioned mutation).
- **Environments**: detached worktrees `/private/tmp/watch-notify-gate/wt-head` (@b0d9ff5e) + `wt-base` (@b0a4f21d); uv sync 1.3–1.5s; pytest 9.0.2 + pytest-timeout (dual-layer `timeout 300` + `--timeout=270` on every pack).
- **Workers**: 9 worker instances (infra 5f2cd457 · jq-head 2e5359a2 · new3-head 24719063 · adj-head 4e23f663 · conc-head 7663127e · base-jq 9078c098 · base-adj e2d2d092 · static eb76fcf8 · census-sim 53f8d9b2).

## VERDICT: ✅ SHIP

Every must-cover item of the dispatch plan is green. Every HEAD failure is differential-proven pre-existing at base (node-for-node). Zero polling-by-mechanism. Census pin proven to bite via live RED simulation. ensure.md Core criticals PASS.

---

## 1. Pack runs (commands + counts)

| Pack | Leg | Command (essence) | Result | Runtime |
|---|---|---|---|---|
| `watch_notify_jq_full` | HEAD | `timeout 300 uv run python -m pytest tests/job_queue/ -q --tb=short --timeout=270` | **PASS** — 1741P / 7F / 38S / 3des | 60.15s |
| `watch_notify_jq_full` | BASE | same @ wt-base | baseline — 1723P / 7F / 38S / 3des | 60.79s (×2, deterministic) |
| `watch_notify_new3` | HEAD | 3 new suites `-v` | **PASS** — 23/23 (13+3+7) | 1.98s |
| `watch_notify_adjacent` | HEAD | family pack (47 files, split A/B at >800) | **PASS\*** — 1220P / 2F / 9S | 54s |
| `watch_notify_adjacent` | BASE | family pack (76 files†, split halves) | baseline — 1522P / 4F / 9S / 26des | 97s |
| `concurrency_atomic_unit_test` (registered) | HEAD | `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` | **PASS** — 98P / 0F / 74S | 8.41s |

\* PASS = zero failures beyond the differential-proven pre-existing set (see §2).
† Base leg ran the assembly greps verbatim; macOS BSD grep emitted `tests//` double-slash paths that defeated `^tests/<dir>/` exclusion anchors → 76 files incl. job_queue/e2e/integration (harmless: marker-deselected or covered by the jq packs). HEAD leg repaired exclusions (paired single+double slash) → 47 files. All failing nodes present in both legs → differential valid.

### HEAD failure differential (node-for-node vs base)

| HEAD failure (7 + 2) | Base | Class |
|---|---|---|
| test_watcher_repository_concurrent.py::TestSequentialAddWatch::test_first_call_inserts (:158) | FAIL (identical) | PRE-EXISTING (`settled`≠`failed` default-events pin) |
| test_watcher_repository_concurrent.py::TestConcurrentAddWatch::test_concurrent_threads_default_events_single_row (:315) | FAIL (identical) | PRE-EXISTING |
| test_job_feedback_observer.py::TestObserverSkipsTerminated::test_observer_skips_terminated_status (:327) | FAIL (identical) | PRE-EXISTING (observer mock) |
| test_in_progress_guard.py ×2 (:428, :453 — notify_watchers arg mock vs literal) | FAIL (identical) | PRE-EXISTING |
| test_jober_watch_integration.py::TestJobWatcherRepository::test_add_watch_creates_record (:935) | FAIL (identical) | PRE-EXISTING |
| test_phase2_feedback_verify.py::test_observer_completion_then_termination_skips_termination (:497) | FAIL (identical) | PRE-EXISTING |
| tests/static/test_chokepoint_callers.py::test_no_new_direct_chokepoint_callers | FAIL (identical violation set: manager.py `_resume_processing_background→cancel_task`, worker_pool.py `_usage_limit_episode_decide→fail_task/schedule_retry`, over-budget `_schedule_explicit_handle_resume→cancel_task` 2>1) | PRE-EXISTING (all 3 files NOT in branch diff) |
| tests/static/test_chokepoint_callers.py::test_no_direct_sql_on_task_status_outside_transitions | FAIL (identical: manager.py:5987 + job_recovery_service.py `_pattern_e_dead_letter_sweep_sync` — base :2037 → HEAD :2056, same function, line shifted by branch insertions) | PRE-EXISTING |

**Zero UNKNOWN / branch-caused failures.** Passed delta jq (1723→1741 = +18) equals exactly the branch's added/expanded tests (16 new + 2 n1_pin growth). Base-only pack flake: `test_usage_limit_worker_seam.py::TestCarveOut::test_other_exceptions_cascade_unchanged` — fails only in base pack context; 4× deterministic solo PASS (2× HEAD, 2× BASE); branch diff (+27/−1 task_processor.py) confined to success-path hook + race guard, does not touch the exception-cascade path. MONITORED (quarantine if it ever fails inside a registered pack).

## 2. Incident replay (fb2c6b2e) — ACCEPTANCE TEST: PASS

`test_event_driven_completion.py::TestSite1InlineMirrorFinalize::test_hook_fires_settled_and_dual_fire_is_deduped`
- Drives REAL `ProcessMessageProcessor._build_callbacks → on_success` → REAL `finalize_mirror_job_at_completion` (Fix-B inline mirror — the exact structurally-silent site from the incident) → REAL `notify_watchers` chain over real `WorkResolverService` + `JobWatcherRepository` on the conftest in-memory SQLite engine (StaticPool, 27+ tables).
- Asserts: `[JOB_EVENT]` fires with canonical source `internal_agent:job_event:{wid}:settled`; watcher row CAS-claimed exactly once (dual-fire deduped to 1 delivery); notification deliverable to caller (enqueue_message asserted at the message-queue boundary — the only mock, downstream of the watcher CAS).
- Companion: `test_pre_terminal_task_completes_on_success_fully` (pre-terminal race) + `test_hook_skips_when_finalize_races_to_none` (race-loss → no phantom).
- What stranded silently in prod twice now delivers end-to-end at SQLite-fixture e2e depth (leader-sanctioned depth; no fresh-SQLite daemon boot per known migration trap).

## 3. dead_letter replay: PASS

`test_work_notifier_n1_pin.py::TestMissionTerminalDeadLetterFire::test_dead_letter_mission_terminal_fires` (+ `test_non_terminal_mission_still_held`) — file-backed SQLite (tmp_path+QueuePool, required for cross-connection DELETE...RETURNING CAS observability), real notify chain, `dead_letter` WorkRecord → 1 delivery + CAS-claimed + source/body asserted; non-terminal stays held. Predicate verified in source: `_is_terminal` gates on `_TERMINAL_STATUSES = {completed, settled, failed, cancelled, dead_letter}` (work_status.py:145-147) — closes the old `{completed,failed,cancelled}` hold-forever gap. Note: spec said `work_notifier.py:369`; the predicate line is `:374` in current source (`:369` is the `mission_live = getattr(...)` extraction) — cosmetic spec drift, substance verified.

## 4. Per-site battery: ALL GREEN (6 hooked + 1 exempt)

| Site | Location (hook line) | Token asserted | Exactly-once mechanism | Tests |
|---|---|---|---|---|
| 1 Fix-B inline mirror | task_processor.py:1088 (census :1087) | `settled` | dual-fire CAS dedup → 1 delivery | TestSite1InlineMirrorFinalize ×3 |
| 2 F-1 reconcile rows | job_recovery_service.py:894 | `settled` (per-kind mirror) | re-entrant sweep → 0 extra | TestSite2ReconcileTerminalMirrors ×2 |
| 3 EXEMPT (D2 carve-out) | repository.py:2687 (carve-out block :2680-2689, comment-only hunk) | — (exempt) | breadcrumb pinned <1200 chars above fn | TestSite3Exemption ×1 + census exempt_kind |
| 4 batch_cancel race-guarded | job_queue_service.py:1298 (census :1296) | `cancelled` (re-SELECT-verified ids) | empty capture → 0 | TestSite4BatchCancelQueued ×3 |
| 5 force_finalize_orphan | job_queue_service.py:1365 (census :1362) | `cancelled` (from terminal_reason) | re-entrant → 0 | TestSite5ForceFinalizeOrphan ×2 |
| 6 f1-DEAD | job_recovery_service.py:3886 | `dead_letter` | concurrent re-finalize → InvalidTransitionError no-op | TestSite6PatternFDead ×2 |

All 6 hooked sites' `hooked_at` census entries resolve to live `notify_watchers` calls (±5-line anti-rot test). 17 hooked + 7 exempt = 24/24 walker==fixture.

## 5. Site-4 race guard: PASS

`test_race_guard_stale_capture_never_notified` covers both branches in one test: raced id (transitioned to `active` between pre-SELECT and UPDATE) → NOT notified AND its watcher row SURVIVES (`len(get_watchers_for_job(raced)) == 1`); winner (naturally transitioned) → fires `cancelled` with CAS dedup.

## 6. Census pin + RED simulation: PASS

- Green: `test_terminal_write_census.py` 3/3 — walker discovers 24, fixture 24, classified 24/24; anti-rot green; synthetic unlisted-site RED-path green (5 message needles).
- **Live mutation sim**: deleted the site-1 hook (19 lines, task_processor.py:1078-1096; sole mutation, diff quoted in worker report) → census FAILED naming the site verbatim: `hooked_at entries no longer point at a notify_watchers call (±5 lines) … ['daemon/services/task_processor.py:1087', …]`; site-1 battery co-failed ALONE (`TestSite1InlineMirrorFinalize::test_hook_fires_settled_and_dual_fire_is_deduped` — "got []"), other 12 pins stayed green → bite is site-specific. Restored byte-identical (sha256 match), porcelain empty, re-green 3/3, no commits.

## 7. Zero-polling audit: PASS

`git diff b0a4f21d..b0d9ff5e -- daemon/` (+175/−4, 5 files): 0 hits for time.sleep / asyncio.sleep / Timer / sched / apscheduler / add_job / interval / while True / create_task / ensure_future / call_later / call_at / poll / sweep / cron; 0 new background-service registrations. Only `asyncio.to_thread(...)` ×15 — one-shot sync-SQL dispatch (post-UPDATE hook helpers), not a loop/timer. User architectural principle (event-driven only; boot reconcile = core-system last-effort) HOLDS in the added lines.

## 8. Mock honesty: CLEAN

- census suite: 0 mocks (pure AST walker). event_driven: 7 AsyncMock/MagicMock; n1_pin: 5 — all positioned at the message-queue boundary (`enqueue_message` AsyncMock vs real `async def`) or as duck-typed pass-through args; sync `resolver.resolve_work` mocked sync (matches real, invoked via `asyncio.to_thread` in prod). No AsyncMock-on-sync, no signature divergence, no vacuous-depth seams. One dead `held_watcher=MagicMock()` at n1_pin:688 (unreferenced; harmless).
- Depth proof: real repos + real notify chain on SQLite fixtures; conftest session engine + create_all (27+ tables) + autouse truncation.

## 9. ensure.md (Core, blast-radius scoped)

- ✅ Critical: scoped packs PASS (modulo differential-proven pre-existing) — jq_full*, new3, adjacent*, concurrency
- ✅ Critical: `concurrency_atomic_unit_test` PASS (98P/0F/74S — exact prior-gate parity)
- ✅ Critical: no sync DB calls on loop — same pack green (hooks route SQL via `asyncio.to_thread`)
- ✅ Critical: dev.sh:102 `--timeout-graceful-shutdown 10` PRESENT
- Release Gate: NOT run — not a big/critical/architecture change by blast radius (5-file surgical hook addition); deferred-with-scope-notice per ensure.md scoping rule.
- Improvement notice: none this gate (no contradicting methods encountered).

## 10. Gaps / deviations (disclosed, none blocking)

1. **Parity bonus-flip NOT observed**: leader expected 3 `TestRecoveryServiceBothStateParity` tests FAIL@base→PASS@HEAD. Observed PASS@base AND PASS@HEAD (base full-pack + node-scoped probe, deterministic). The "PASS at HEAD" direction holds; the flip rationale didn't materialize at this base — expectation likely authored against a different base/env. Zero impact on verdict.
2. Adjacent-leg file-set asymmetry (BSD grep `tests//` quirk) — §1 note; all failing nodes compared validly.
3. `test_usage_limit_worker_seam` base-pack flake — MONITORED (§1).
4. Known 7 pre-existing reds remain (families match the leader's stash-diff list exactly); recommend a separate test-debt pass to re-contract them (`settled` vocabulary rename fallout + observer mock seams) — outside this gate.
5. e2e/integration/postgres families excluded by project addopts (configured deselect) per scope.
6. Adjacent HEAD collect-count artifact: 1218 collected vs 1231 outcomes (static-test per-scope enumeration double-count; non-material).
7. Worktrees removed post-gate (cleanup worker); recreation is cheap (uv sync ~1.3s) if re-runs are needed.

## 11. Verify-only attestation

No worker modified the shared main checkout (foreign `.agents/` drift observed pre-existing and untouched). All pack runs pinned `git rev-parse --short HEAD` + empty porcelain pre/post. The single sanctioned mutation (census sim) was restored sha256-verified. Zero commits by the gate. RESULTS/PACKS updates are working-tree files only, uncommitted, per dispatch constraints.
