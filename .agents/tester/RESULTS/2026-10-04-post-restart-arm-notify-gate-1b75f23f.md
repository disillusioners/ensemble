# Post-Restart Arm-Notify — Independent Merge-Gate Validation @ 1b75f23f

Date: 2026-10-04
Commissioner: leader (independent validation gate before merge)
Branch / tip: `feature/post-restart-arm-notify` @ `1b75f23fb38e0c048d27752cb15d3c1bb627764e` (8 commits atop plan tip `24ad2f28`)
Change set (verified `git diff --name-status 24ad2f28..1b75f23f`, 23 files): `daemon/api.py` (+26 boot wiring), `daemon/services/upgrade_journal_sweep.py` (+1234), `daemon/tools/upgrade_journal.py` (+531), `daemon/tools/upgrade_tools.py` (+142/−8) + 6 test files, 6 pack scripts, drill + driver + runbook + smoke, CHANGELOG, PACKS.md, risk-register.

**Freshness mandate honored: every executed result below was run fresh by gate-dispatched workers at HEAD `1b75f23f` during this gate. No prior phase-1..4 tester record was trusted for any verdict.**

## FINAL VERDICT: 🟢 PASS FOR MERGE

Zero feature-caused test failures. Zero regressions. All in-scope ensure.md requirements pass. Drill stable across repeated runs. Mock fidelity HIGH with zero papering-over. Pre-existing suite rot (22 failures) proven byte-identical on base — none of it in the change-set packs.

---

## 1. Pass/Fail Matrix (all executed at tip 1b75f23f, dual-layer timeout, independently dispatched)

| Suite | Pack / invocation | Result | Runtime |
|---|---|---|---|
| journal | `timeout 300 bash test/packs/post_restart_arm_notify_journal_unit_test.sh` (110s inner) | ✅ PASS **40/40** | 1.14 s |
| sweep | `…/post_restart_arm_notify_sweep_unit_test.sh` (110s inner) | ✅ PASS **33/33** | 1.53 s |
| routing | `…/post_restart_arm_notify_routing_unit_test.sh` (110s inner) | ✅ PASS **7/7** | 0.89 s |
| edge_cases | `…/post_restart_arm_notify_edge_cases_unit_test.sh` (110s inner) | ✅ PASS **8/8** | 0.93 s |
| structural | `…/post_restart_arm_notify_structural_unit_test.sh` (110s inner) | ✅ PASS **8/8** | 0.69 s |
| banner | `…/post_restart_arm_notify_banner_unit_test.sh` (110s inner) | ✅ PASS **5/5** | 0.34 s |
| drill-smoke (ad-hoc, unregistered by design) | `timeout 300 .venv/bin/python -m pytest --override-ini="addopts=" --timeout=240 tests/test_post_restart_arm_notify_drill_smoke.py` | ✅ PASS **1/1** (executes full drill) | 20.53 s |
| regression: upgrade_tool_interlock | `timeout 300 bash test/packs/upgrade_tool_interlock_unit_test.sh` (110s inner) | ✅ PASS **278/278** | 14.10 s |
| regression: upgrade_alerting | `timeout 300 bash test/packs/upgrade_alerting_unit_test.sh` (120s inner) | ✅ PASS **72/72** | 7 s |
| sandbox drill ×2 | `timeout 300 bash test/drills/post_restart_arm_notify_drill.sh` (run twice) | ✅ **9/9 exit-0 both runs** | 21 s / 20 s |

**Totals: 352 pytest tests 0 failures; drill 3 green executions this gate (2 standalone + 1 via smoke).** Dual-layer timeout verified present on every pack (feature packs uniform 110s inner; interlock 110s; alerting 120s self-reinvoking battery pattern); no layer ever engaged.

Pack registration audit: all 6 feature packs registered in committed PACKS.md (`git show 1b75f23f:.agents/tester/PACKS.md`), scripts exist + executable, 1:1 test-file mapping, zero discrepancies for this feature. Drill-smoke intentionally unregistered (keeps drill out of pack dispatch; run ad-hoc here). Working-tree PACKS.md is dirty from an unrelated OD-timeout commission — judged by committed version per recon.

## 2. Drill Stability (task item 2)

- Run 1: exit 0, 9/9 checks (2 preflight + D1..D6 + live-isolation), run-dir `/tmp/ens-wake-drills/20261004T001118`.
- Run 2: exit 0, 9/9 checks, run-dir `/tmp/ens-wake-drills/20261004T001224`.
- **STABLE**: normalized diff (run-ids/timestamps excluded) → 63/63 check lines identical verbatim (9 verdicts + 54 per-scenario sub-checks: D1=16, D2=10, D3=6, D4=7, D5=11, D6=4). Zero flakiness/retries/reordering.
- Scenario content verified green: D1 restart-lane wake one-shot + api sentinel; D2 upgrade-lane terminal_outcome=commit; D3 `discord:user123` source preserved (ADR-041); D4 double-arm coalesce ONE enqueue coalesced_count=2; D5 kill-switch OFF→no record / persisted→abandoned / re-enable→no stale flood; D6 FAKE-live outright refusal (A2 token intact, state.json byte-identical).
- Safety: live pid set unchanged across all drills (checkpoint `3321986` baseline==end); fake HOME + env scrub + preflight collision refusal ran as designed; no daemon boot / DB / network; port 8088 never touched; `git status` byte-identical pre/post. Fakehomes retained by design (drill EXIT trap is a documented no-op; evidence artifacts inside run dirs).
- Live daemon restart cycle NOT executed (user-gated; sandbox drill is the sanctioned proxy) — honored.

## 3. Mock-Quality Review — TrueAuto mandate (task item 3)

**Overall: HIGH fidelity. Mocks that paper over real behavior: NONE.** Four documented assertion-surface gaps (non-blocking, listed in §8).

- **Q1 journal fixtures: FAITHFUL.** Real flat `{ts, event, detail}` shape everywhere (`upgrade_journal.py:326` schema); the single fictional `{ts,name,run_id}` entry is the INTENTIONAL negative test (`test_walker_uses_real_journal_shape`, journal file :626-643) proving the reader does NOT match on name/run_id. ts values are Z-ISO matching `now_iso()`/`parse_iso_utc`. PendingWake fixtures build all real fields; round-trip byte-exact test exists. **The prior C1 fictional-schema bug class: NOT present.**
- **Q2 manager-mock fidelity: PARTIAL FAITHFUL (assertions real, not vacuous).** Delivery tests assert `instance_id`/`source`/`priority` kwargs + `metadata.system_context.kind` + `run_id` (routing :164-166). GAP: single-wake `message` body prose unasserted; 6/8 system_context keys (incl. `terminal_outcome`) unasserted — see §8.
- **Q3 lock discipline: FAITHFUL.** T6.4 structural pin source-scans BOTH arm sites and asserts `journal_lock_acquire → try: → write_pending_op → _arm_pending_wake_for_op` ordering (upgrade_tools.py:2287-2337 / :2848-2987) — the caller-lock atomicity story (ADR-039 delta-3) is pinned. Noted weakness: regex/indentation-based, no same-try containment (still catches the documented regressions).
- **Q4 sweep mocks: FAITHFUL.** ts-scope (armed_at window, before/after cases), terminal-class membership (`restart ∈ WAKE_TERMINAL_EVENTS`, `restart ∉ _TERMINAL_EVENTS`, base 6-member pinned), promote-lane NO-run_id fire on the real `promote.sh:366` shape, restart-lane run_id-prose mismatch tolerated.
- **Q5 papering-over: NONE.** Units under test never mocked; structural pins scan REAL daemon source (banner pack reads the real `upgrade_tools.py` via repo-root path, not a copy); assertions are production-contract-level, not mock-internal.

## 4. Base-Failure Attribution (task item 4) — dev claim VERIFIED in substance, file list 2/3 wrong

A/B legs: TIP = main checkout @1b75f23f; BASE = temp detached worktree @24ad2f28, fresh `uv sync` venv (Python 3.13.15 both legs, import provenance verified in-worktree), identical POSTGRES-scrubbed env wrapper on every invocation (zero live-DB contact). Worktree removed after; main checkout byte-identical pre/post.

| Dev claim | Verdict | Detail |
|---|---|---|
| (a) `test_f1a_tools_allow_wire.py` fails on base | **NOT-REPRODUCED — green on BOTH legs (0 F)** | Dev misattribution |
| (b) `test_job_queue_proxy_phase1.py` fails on base | **VERIFIED-PRE-EXISTING** | 7 F / 45 P at BOTH legs; identical test ids, identical file:line (:312/:440/:487/:533/:1007/:1044/:1085), byte-identical assertions |
| (c) `test_jq_proxy_phase2_dualwrite.py` fails on base | **NOT-REPRODUCED — green on BOTH legs (0 F)** | Dev misattribution |
| (d) ~14 `tests/unit/services/` failures | **VERIFIED-PRE-EXISTING (actual count: 15)** | 15 F / 0 E at BOTH legs; TIP-ONLY = NONE (zero feature-caused); BASE-ONLY = NONE (zero regressions) |

- Totals: services TIP 15F/2312P (4 shards) vs BASE 15F/2279P; delta 33 = the feature-new sweep test file (green at tip, absent at base — only scope asymmetry).
- Byte-similarity: sorted diff of all 46 E-lines → 44 identical, 2 differ only by generator-object heap addresses.
- Failure families (all committed-tree rot, NOT location/environment-attributed): phase1 JobItem-mirror status canonicalization (7); anti-drift/static-site pins (3); context-hint 0%-match render (1); legacy-status helper export (1); `service_tool_manager` KeyError 'reason' (1); `b1_wc_durable_send` VisionModelNotAllowedError stale-MagicMock fixture + hardcoded `/Users/nguyenminhkha/...` author path (2).
- **Action for dev/leader: correct the claim text — (a) and (c) never fail; the failing file is `test_job_queue_proxy_phase1.py` + the 15 services failures.**

## 5. Execution-Lane Intersection (task item 5) — EMPTY → e2e N/A

Per the repo rule (job/task/queue changes judged by EXECUTION-LANE intersection, not file diff):
- Lane files in the 23-file diff? **NO** — `task_processor.py` and the message-handler equivalent both absent.
- `git diff 24ad2f28..1b75f23f -- daemon/ | grep -nE 'claim_pending|dispatch|task_processor|message_job'` → **zero output**.
- Import audit: neither lane file imports any of the 4 changed daemon modules; conversely the sweep is a boot-wired background service delivering via `manager.enqueue_message` (wiring pre-dates this window; `manager.py` NOT in diff).
- **Verdict: lane intersection EMPTY → job-lane e2e pack N/A.** Citation drift noted for the rule text: `claim_pending_task` now at `task_processor.py:1797` (was :464); `message_job_handler.py` was DELETED in `8d20ffb6` — surviving message-lane equivalent is `daemon/services/message_processing_pipeline.py`.

## 6. AC Evidence Map (task item 6) — one line per AC, verified against actual test code

- **AC1** (arm-time record atomic w/ arm): `TestArmPendingWakeAtomicity::test_arm_pending_wake_writes_to_pending_wakes_key` + `test_arm_pending_wake_rides_journal_write_envelope` (journal pack) + structural `TestArmPendingWakeLockPositionPin` (arm inside caller-held lock at BOTH sites, after `write_pending_op`) — ADR-039 delta-3 caller-lock story pinned.
- **AC2** (boot delivery): `TestBootPassEnqueuesWake::test_boot_pass_enqueues_wake_on_matching_terminal_event` (+ clean no-op empty case) — sweep pack; 90s-tick rides `sweep_once → sweep_wake_records` (bound-method pin T6.6).
- **AC3** (routing to originating chat): `TestWakeSourceRouting::test_wake_source_equals_recorded_source` / `test_full_path_routing_uses_recorded_source` / `test_user_origin_window_set_after_wake_delivery` / `test_empty_source_uses_api_sentinel` + `TestSite1ProgressiveDispatch` ×3 — routing pack (source verbatim incl. `discord:user123`; `system_context.kind/run_id`; Site 1 dispatch_source verbatim).
- **AC4** (terminal gating): `TestLatestMatchingEvent` walker family (ts-scope, non-terminal skip, garbage-tolerant) + `TestWakeTerminalEventAfter::test_wake_terminal_events_mutation_guard` + `test_restart_event_accepted_by_wake_reader` + `TestTerminalStateGating::test_pending_wake_held_when_no_terminal_event` / `test_mixed_batch_delivers_only_terminal_ones` + `test_promote_lane_fire_no_run_id_on_history` (T13.1) + `test_restart_lane_run_id_mismatch_still_fires` (T13.2) — incl. no-fire-while-rollback-possible (hold with zero enqueues) and non-interference (clear/reconcile/restart-sh never touch pending_wakes).
- **AC5** (fallback/coalesce/idempotency/no-wedge/live-refusal-no-record): `TestMissingInstanceAriFallback` + `TestAriFallbackDeterminismAndStatusFilter` (max(instance_id) tie-break, non-terminal filter, zero→abandon) + `TestCoalesce` + `TestIdempotency` + `TestBootNeverWedges` (incl. F1 rollback-to-pending recovery) + `TestKillSwitch` (OFF-abandon/re-enable-no-flood) + `TestGraceAbandonment` ×6 + `TestArmSideLiveRefusal::test_system_restart_on_live_writes_no_wake` + edge_cases T5.13/T5.14/T5.15×4.
- **AC6** (structural pins): structural pack 8/8 — T6.1 no new journal file, T6.2 no new HTTP endpoint, T6.3 no new SQLModel table, T6.4 lock-position, T6.5 wired helper, T6.6 bound sweep method, T6.7 real-shape fixture pin.
- **AC7** (coverage map): 6 registered packs + drill + smoke all exist, registered, and pass fresh; matrix audit = **46/48 T-cases map to dedicated assert-bearing tests**; T5.6 (run-list payload format) and T5.8 (two-tick CAS race) covered only incidentally — see §8.

## 7. Boot-Wedge Safety (task item 7)

- **manager=None no-op: PRESENT (structural) + behavioral via install_dir seam.** `test_manager_none_default_kwarg_is_dev_mode_noop` (structural, asserts the dev-mode default) and `test_install_dir_none_is_clean_noop` (behavioral all-zeros). GAP (minor): the exact combination manager=None × real install_dir × deliverable wake (logged skip+`continue` at `upgrade_journal_sweep.py:1288-1295`) is not behaviorally exercised.
- **Sweep-exception-during-boot: PRESENT at the sweep seam.** `test_journal_torn_returns_errors_continues` + `test_enqueue_failure_continues_to_next_wake` + F1 recovery prove `sweep_wake_records` never raises for both realistic failure classes; the sweep has an internal catch-all (`:1491-1504`) and `api.py:1526-1531` wraps the boot call in try/except. GAP (minor): no test forces an exception through the catch-all or the api.py wrapper (double-safety unpinned). **Not flagged MISSING — the load-bearing never-raises contract IS tested.**

## 8. Gaps & Action Needed (none merge-blocking)

🔴 none.

🟠 should fix (follow-up commissions, not this merge):
1. **Pre-existing suite rot is un-quarantined**: 22 failures (7 × `test_job_queue_proxy_phase1.py`, 15 × `tests/unit/services/` families) red the default suite runs at BOTH base and tip. Per quarantine policy they should be quarantined or fixed in a separate test-debt commission (identity table in §4 is the intake list). Left untouched here (commit-only-RESULTS constraint).
2. **Dev claim text wrong 2/3** — correct the base-failure list before citing it downstream.

🟢 nice-to-have (assertion-surface hardening):
3. Single-wake body prose + 6/8 `system_context` keys (`arm_kind`, `terminal_outcome`, `target_version`, `armed_at`, `wake_at`, `coalesced_count`) unasserted — a `_format_wake_body` regression would be partially silent (coalesce run_ids + ordering ARE asserted).
4. T5.6 dedicated run-list payload-format test; T5.8 sweep-level two-tick CAS race test (helper-level CAS-loser + F1 recovery exist).
5. Uncaught-sweep-exception through the catch-all / api.py boot wrapper unpinned.
6. T6.4 pin: add same-try containment (or AST) if cheap.
7. PACKS.md journal row says 37 — actual 40 (additive review-round growth; stale row, all green).
8. test-strategy.md mapping drift: T4.8 lives in sweep file (map says journal); row 65 misroutes T5.18/T5.19/T6.5; §4.4 names `tests/postgres/test_post_restart_arm_notify_pg.py` which does not exist.
9. Drill smoke remains unregistered (by design) — considered adding a registration row; deferred to avoid touching PACKS.md under this gate's constraints.

## 9. ensure.md Validation (scoped by blast radius)

| Requirement | Verdict | Evidence |
|---|---|---|
| Core#1 no regressions in changed packs | ✅ PASS | All 8 registered change-set packs PASS — executed fresh by THIS gate's workers at 1b75f23f (§1), not implementer claims |
| Core#2/#3 deadlock/concurrency + sync-DB-on-loop | OUT-OF-SCOPE | 3-piece empty-intersection proof: concurrency pack's 13 files ∩ 23-file diff = ∅; import-intersection grep empty; diff adds no DB-session code, sole event-loop addition is properly-awaited `await sweep_wake_records()` double-wrapped in try/except |
| Core#4 dev.sh `--timeout-graceful-shutdown 10` | ✅ PASS | `dev.sh:102` grep hit (static) |
| Important#1 async-await callers | OUT-OF-SCOPE | Zero occurrences of the 3 symbols in full diff text |
| Nice#3 no dead code | INFO PASS | Obsolete pull-model banner replaced in both branches; T4.7 pins its absence |
| Release Gate | **NOT TRIGGERED** | Single-lane feature (upgrade-tool surface), empty task-lane intersection, no architecture refactor, not a release |
| Quarantine overlap | ZERO | 3 active QUARANTINE.md entries touch none of the 8 packs; no quarantine credits applied |
| Contradictions | NONE | All methods pack-mapped/static/timeout-wrapped; no `-x`; no bare pytest |

## 10. Constraints Compliance

- No live daemon restart executed; sandbox drill (×2) + drill-smoke (×1) = sanctioned proxy only. No live install contact, no DB contact (POSTGRES-scrubbed attribution legs), no repo mutation outside test runs (`git status` byte-identical pre/post every leg; worktree removed).
- Working-tree uncommitted artifacts (.agents/tester/ OD-timeout era, .agents/approver/) untouched and NOT staged.
- Port 8088 never touched; no process kills by port/name anywhere.
- No bare `npx tsc` (convention respected by all workers).

## 11. Worker Instances (gate-dispatched, fresh execution)

recon-lane 7ed35330 · mock-review cdae6934 · ac-map 713066e1 · base-attribution 95699014 · pk-journal 6a23ca32 · pk-sweep 7ab5d0cc · pk-routing f08e499e · pk-edge 573dbb13 · pk-structural be144f02 · pk-banner f5bd6822 · pk-interlock df7c47ae · pk-alerting 1f4ea506 · pk-drillsmoke e038e5f6 · drill-x2 cabd039b · ensure-gate 621d6012

## 12. Code Changes Summary

None to production or test code — report-only gate. Only artifact: this RESULTS file (committed alone). PACKS.md last-run rows intentionally NOT updated (file carries unrelated uncommitted edits; gate constrained to commit ONLY the RESULTS file).

### Overall Status
- Feature packs: ✅ PASS (101/101) · Regression: ✅ PASS (350/350) · Drill: ✅ STABLE (9/9 ×2) · Mock review: ✅ HIGH, zero papering-over · Base attribution: ✅ zero feature-caused · Lane: ✅ EMPTY → e2e N/A · ensure.md: ✅ all in-scope PASS · Boot-wedge: ✅ covered (2 minor gaps documented)
- **TESTING COMPLETE: ✅ READY FOR MERGE**
