# Test Verification Report — mission-live guard fix (premature 'completed ✓' job events)

- **Date:** 2026-09-22
- **Branch:** `fix/job-event-premature-completed` @ `9e596604` (base `0062e6fc3aa4425cc72364bceb5ba1066e27a29b`)
- **Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-jobevent-fix` (verified clean at HEAD before and after every run; read-only throughout — zero commits, zero source modifications, main checkout untouched)
- **Verifier:** Tester agent (dispatch-only); 10 worker instances executed all runs
- **Verdict:** **FAIL** — solely on the scenario-audit bar (caller's criterion: "weak/missing scenario → FAIL"). Execution evidence is clean: **zero new failures vs base anywhere**; claims (a) and (c) CONFIRMED, claim (b) confirmed in the no-new-failures sense (literal "green" is false — 23 pre-existing, base-identical failures in the wider lineage/services surface).

## Scope Decision
Verification protocol as dispatched by the caller (claims re-run + broader regression + audit). Broader regression scoped to the blast radius: whole `tests/job_queue/`, `tests/unit/services/` (101 files), curated lineage tiers (76 files outside `tests/job_queue/`), and the PG-lane bug-class pair on disposable PG. Integration-marked lane (needs live server) and the remaining 246 attestation-family "adjacent" files excluded — no overlap with the 7-file change set. Full suite not warranted.

## Change set (verified via git diff 0062e6fc..9e596604)
7 files (+1420/−3): `daemon/services/mission_live_guard.py` (new, 318 L), `daemon/services/job_recovery_service.py` (+65), `daemon/services/job_queue_service.py` (+97), `tests/job_queue/test_mission_live_guard.py` (new, 848 L), `tests/job_queue/test_orphan_active_job_recovery.py` (+27/−3), `tests/job_queue/test_f1_killswitch_tz_matrix.py` (+8), plus **`​.agents/shared/planning/job-event-premature-completed/README.md`** (60 L, docs-only — 7th file beyond the stated 6; no code impact).

## Per-suite results (all `uv run python -m pytest` from worktree root; `-q --tb=short -rf`; dual-layer timeout, no run exceeded 68s)

| # | Suite | Command scope | Result | Counts | Duration |
|---|---|---|---|---|---|
| 1 | Guard solo | `tests/job_queue/test_mission_live_guard.py` | **PASS** | 17 passed / 0 failed (17 collected) | 0.70s |
| 2 | Whole job_queue (branch) | `tests/job_queue/` | FAIL = expected pre-existing | 1835 passed / 13 failed / 38 skipped / 3 desel. (1886 collected) | 67.47s |
| 3 | Whole job_queue (base) | `tests/job_queue/` @ 0062e6fc throwaway WT | FAIL = 13 real + 3 env | 1815 passed / 16 failed / 38 skipped / 3 desel. (1869 collected) | 56.95s |
| 4 | Lineage tier a (unit) | 39 files outside `tests/job_queue/` | FAIL = all pre-existing | 1123 passed / 11 failed | 23.03s |
| 5 | Lineage tier b (top/services) | 37 files | FAIL = all pre-existing | 622 passed / 4 failed / 99 skipped | 33.07s |
| 6 | Services unit | `tests/unit/services/` | FAIL = all pre-existing | 1905 passed / 8 failed (1913 collected) | 54.12s |
| 7 | PG bug-class pair | `tests/postgres/test_premature_completion_{edge_cases,regression}.py` on disposable PG14 @15432 | PASS-by-skip (no signal) | 28 collected / 28 skipped / 0 executed | ~2s wall |
| 8–10 | Base spot-checks | failing files only @ base | all sets byte-identical | see below | 0.92s / 5.99s / 2.37s |

## Claim adjudication (dev claims independently re-run)

**(a) "17/17 pass in test_mission_live_guard.py" — CONFIRMED.** 17 collected, 17 passed, exit 0.

**(c) "whole tests/job_queue/ failure-set identical to base (13 failed both), +17 delta" — CONFIRMED, with proof at both ends:**
- Base raw run: 16 failed. 3 of them (`test_f1_killswitch_tz_matrix.py::TestRecoveryServiceBothStateParity::*`, `FileNotFoundError: '.venv/bin/pytest'`) proven **environmental** by A/B: with a `.venv` symlink into the throwaway worktree the file goes **29/29 green (7.82s)**; the invocation block is byte-identical base↔branch (CWD-relative `.venv/bin/pytest`, only a +4-line offset). True base failure set = **13**.
- Branch 13 vs base 13: **node-ID-identical** (all 13 match one-for-one; symmetric set-diff empty on both sides).
- Delta: 1886 − 1869 collected = **+17** = exactly the new file; `git cat-file -e 0062e6fc:tests/job_queue/test_mission_live_guard.py` → path absent at base. Pass-count arithmetic reconciles (1815 + 17 new + 3 env-artifact-recoveries = 1835).
- The 13 pre-existing: 3× `test_event_driven_completion` (MagicMock-JSON), 2× `test_in_progress_guard`, 1× `test_job_feedback_observer` (observer_skips_terminated), 1× `test_jober_watch_integration`, 1× `test_phase2_feedback_verify`, 1× `test_round2_council_fixes` (event-loop), 2× `test_terminal_write_census`, 2× `test_watcher_repository_concurrent` — matches the known pre-existing reds ledger.

**(b) "lineage suites green" — CONFIRMED in the no-new-failures sense; literally false.** My wider lineage/services sweep found **23 failures, every single one proven pre-existing at base** (byte-identical node-IDs, empty set-diffs):
- `tests/unit/services/test_job_queue_proxy_phase1.py` ×7 — Phase-1 canonical-status map gap ('pending' overrides Instance status; missing `error→failed`, `waiting_children→processing`) — PRE-EXISTING.
- `tests/unit/services/test_b1_wc_durable_send.py` ×1 — ENVIRONMENTAL: test hard-codes `cwd="…/-wt-wc-wake-resilience"` (absent sibling worktree; fails identically anywhere).
- `tests/unit/test_coder_developer_migration.py` ×5 (restore/enqueue `get_resolved`/`agent_dir` mock leaks), `tests/unit/test_job_processor_status_guard.py` ×4 (`complete_job` 0 calls), `tests/unit/test_api_router_extraction.py` ×1 (api.py 2827 vs <1600 — known re-baseline debt, base-identical 2827), `tests/unit/test_phase4_manager_decomposition.py` ×1 (`cascade_to_root=True` kwarg, pre-exists at base) — ALL PRE-EXISTING.
- `tests/services/test_instance_messaging_queue_routing.py` ×1 (plain-Mock-awaited rot), `tests/static/test_chokepoint_callers.py` ×2 (5 AST chokepoint callers + 2 direct-SQL sites — incl. `job_recovery_service.py` `UPDATE task SET status` at base:2096 → branch:2097, a **line-shift of the same pre-existing site**, proven by grep both sides), `tests/test_enqueue_shared.py` ×1 (title-bridge double-fire) — ALL PRE-EXISTING.

**Zero new regressions vs base across every suite run.**

## PG-lane bug-class finding (material)
`tests/postgres/test_premature_completion_edge_cases.py` + `test_premature_completion_regression.py` — the historical regression net for this exact bug class (`TestOriginalBugReproduction`, `TestStuckJobRecovery`, …) — are **module-level skip-dead**: `pytestmark = [skip("Phase 5: CorrelationManager removed"), postgres]`; 28 collected / 28 skipped / **0 executed**, identically at base (change set doesn't touch `tests/postgres/`). The only live net for the original bug is the new guard file + existing f2 tests. Disposable PG fully cleaned (15432 free; 5432/8088 untouched).

## Scenario audit (duty 3) — the FAIL driver
| Scenario | Test(s) | Verdict |
|---|---|---|
| (i) mid-mission leader → no 'completed ✓', watcher row NOT claimed | `test_mid_mission_leader_not_finalized` (+ boot-sweep twin `test_boot_sweep_holds_live_mission`) | **WEAK** — real JobItem-ACTIVE + skip-detail state assertions are strong, but `notify_watchers` is an AsyncMock → production `notify_work_watchers → claim_watchers_for_job_for_instances` atomic CAS-delete is bypassed; "row not claimed" is witnessed only via `get_watchers_for_job` truthy + mock not-called (caller's exact weak-assertion example) |
| (ii) true terminal → exactly-one terminal event | `test_true_terminal_finalizes_and_notifies_once` (+ `test_boot_sweep_fires_for_dead_mission`) | **WEAK** — `await_count == 1` on a single sweep proves "at least one / one call because one call"; no double-sweep, no two-watcher-row, no CAS-row-count check |
| (iii) crash-after-mission-end → backstop finalizes+notifies (highest-value) | named `test_crash_after_mission_end_still_delivers_backstop` — **actually delivers (iv)** per its own docstring (:504-506) | **MISSING as a direct test** — the crash→restart→boot-sweep narrative is never constructed; nearest live coverage is state-level (existing f2 dead-mission tests, green, + `test_boot_sweep_fires_for_dead_mission` seeding the stranded durable state directly), and the old PG `TestOriginalBugReproduction` is skip-dead |
| (iv) revive mid-mission → guard holds, terminal still emits | `test_crash_after_mission_end_still_delivers_backstop` (two-phase, real repos) | **STRONG** (mislabeled name) |
| (v) 6h orphan timeout → no starvation | `test_timeout_overrides_live_legs`, `test_missing_anchor_does_not_open_the_door`, `test_orphan_timeout_fires_while_guard_sees_live` (constant 21600s pinned by use) | **STRONG** |
| (vi) guard error → fail-open | `test_repo_error_fails_open`, `test_unwired_repo_fails_open`, `test_guard_error_fails_open_to_finalize` | **STRONG** |

## Mock fidelity (duty 4)
- **M1 (HIGH):** `notify_watchers` mocked in all 8 f2/boot-sweep tests → claim-first CAS-delete + `[JOB_EVENT]` byte-stream never exercised anywhere in the new file.
- Clean: AsyncMock used correctly everywhere awaited (M2); instance statuses real (`waiting_children`/`processing`/`idle` partition per `TERMINAL_INSTANCE_STATUSES`, M3); watcher rows real (`JobWatcherRepository.add_watch`/UPSERT/`watch_events` incl. `mission_terminal` opt-in, M4); WorkRecord 14-field shape faithful (SimpleNamespace, no isinstance in path — LOW fragility, M5/M6); parent_id tree walk matches `get_tree_ids_permanent` (M7); 'completed' token matches production literals (M8); missing-anchor invariant pinned (M9). No impossible seeded states (M10; SQLite-FK note only).
- Touched existing tests: all 8 edits are seed-only `"running"`→`"completed"` flips with in-file doctrine comments; assertions byte-identical pre/post; coverage re-anchored (live-mission shape now explicitly tested in the new file). **No weakening.**
- No self-reading pin tautologies; positional-arg pins only (minor kwarg-rot risk).

## Verdict & required follow-ups
**FAIL per the caller's bar** (weak/missing scenario), with this decomposition:
1. Execution: **clean** — all green where green was claimed; failure sets base-identical everywhere; no new regressions.
2. Test matrix: **does not meet the bar** — (iii) not delivered as a named/direct test; (i)/(ii) weak on the durable-witness criterion; production delivery path (CAS-delete + byte-stream) never exercised; PG bug-class net skip-dead.

Required before this fix's non-regression guarantee can be trusted at the caller's bar:
- 🔴 Add explicit crash-after-mission-end test: seed mission-dead + ACTIVE JobItem + unclaimed watcher, run `reconcile_terminal_watches` with the REAL notify path (enqueue seam mocked at most), assert watcher row CAS-claimed + exactly-one terminal event.
- 🟠 Strengthen (i): real watcher-repo witness — after the guarded sweep, row still present AND a flattened-mission sibling sweep proves the row WOULD have been claimed (guard is the only difference).
- 🟠 Strengthen (ii): double-sweep idempotency (two `reconcile_drift_states` calls on a DEAD mission → still exactly one notify) or two-watcher-row CAS-count assertion.
- 🟢 Replace/retire the Phase-5-skipped PG premature-completion files (their `TestOriginalBugReproduction` intent is now the single most valuable missing test); 🟢 fix `test_b1_wc_durable_send` machine-specific `cwd`; 🟢 re-baseline or refactor `api.py` size pin (known debt).

## Cleanup / integrity
Both throwaway base worktrees removed (`git worktree remove`, no force); `git worktree list` clean of `/tmp/ens-base*`; source worktree clean @ `9e596604` at every gate; no commits anywhere; ports 8088/5432 untouched; disposable PG (15432) initialized/stopped/removed with verification.

## Worker instances
976a05f5 (discovery) · e7e543be (guard solo) · 118b24f1 (job_queue branch) · 7e788a65 (job_queue base + f1 env-proof + services spot-check) · 4aa30e0f (scenario/mock audit) · dc867e55 (lineage a) · 4564d263 (lineage b) · 14fa9630 (services unit) · aa93ce93 (PG lane) · 38baabe8 (lineage spot-checks + final cleanup)


---

# Iteration 2 — Re-verification @ f37a42d4 (tests-only close-out)

- **Date:** 2026-09-22 · **Commit:** `f37a42d4` (lineage 0062e6fc → 9e596604 → f37a42d4)
- **Scope:** focused re-verification per leader request — no full re-sweep (base-identical already proven at 9e596604; daemon/ verified untouched)
- **Verdict: PASS** — all iteration-1 FAIL drivers closed; execution green; census identical; non-vacuity independently reproduced.

## 1. Tests-only gate — PASS
`git diff 9e596604..f37a42d4` = exactly ONE file: `tests/job_queue/test_mission_live_guard.py` (+611/−194, matching claim). `daemon/` diff EMPTY; exclude-one-file diff EMPTY (nothing else anywhere); no renames/mode changes. Collection 1886→1889 (+3); 20 tests enumerated. (Untracked file in worktree = this RESULTS report, mine.)

## 2. Execution
| Run | Result |
|---|---|
| `test_mission_live_guard.py` solo | **20/20 PASS**, 0.74s, exit 0 (claim: 20 — confirmed) |
| whole `tests/job_queue/` | 1838 passed / **13 failed** / 38 skipped / 3 desel., 1889 collected, 62.3s — FAILED node-ID set **byte-identical to the 13-item base census** (empty symmetric diff); arithmetic reconciles exactly (+3 passing) |

## 3. Per-gap closure adjudication (shape-audited with line evidence @ f37a42d4)
- **(iii) 🔴 → CLOSED.** `test_crash_before_finalize_restart_sweep_delivers_exactly_once` (L742-843): mission truly terminal (leader+worker completed), JobItem stranded ACTIVE (sanity-asserted), Task completed 300s, watcher unclaimed (`_assert_rows_survive` pre-restart), restart-framed fresh `JobRecoveryService.reconcile_drift_states` — the correct OWNER for a stuck-ACTIVE shape. `test_crash_after_finalize_restart_boot_sweep_delivers_exactly_once` (L1122-1181): JobItem ALREADY DONE + lost notify — `JobQueueService.reconcile_terminal_watches` owner. Genuinely different owners/paths (dev comment L1044-1050 documents the ownership split; matches production `job_queue_service.py:534-548` / `job_recovery_service.py:2868`). Exactly-once triple-witnessed: `await_args_list` count + adversarial second sweep no-growth + `_assert_rows_claimed` CAS.
- **M1 HIGH → CLOSED.** Zero `notify_watchers` mocks in the file (grep clean). Real chain wired: real `WorkResolverService` (L245-249, even sanity-probed at L1095-1097) → real `JobQueueService.notify_watchers` → real `notify_work_watchers` → real `JobWatcherRepository.claim_watchers_for_job_for_instances` CAS; the ONLY transport mock is `enqueue_message` (L252). Byte-path pinned at L297-306 (13 call sites): `[JOB_EVENT] Job {job_id[:8]}... completed ✓` + `source=internal_agent:job_event:{job_id}:completed` — kwarg-pinned, matches `work_notifier.py:470/:492/:102` byte-for-byte.
- **(ii) WEAK → CLOSED.** Double-sweep no-ops at L660-672, L731-739, L834-843, L1117-1119, L1178-1181, L1227-1229 (incl. "settled job must not re-appear as f2 candidate"). Two-watchers-each-once at L676-739 + L1184-1229: count==2 AND `delivered_to == {watcher-a, watcher-b}` set-equality AND per-call byte pin AND CAS-claim of both rows.
- **(i) WEAK → CLOSED.** `_assert_rows_survive` (real repo reads) at L554/L794/L889/L1104/L1164 + same-state true-terminal follow-through at L559-600 / L1099-1119 / L891-908 — flipping ONLY the mission liveness at identical state delivers exactly once: the guard proven to be the only difference.
- **Mutation-probe claim → SUBSTANTIATED (independently re-run read-only).** No probe mechanism exists in committed code (audit finding), so I reproduced it via a `/tmp` pytest plugin (`-p` + PYTHONPATH; repo untouched, verified before/after): **guard disabled (`live=False` forced)** → all 3 hold tests FAIL (`test_mid_mission_leader_not_finalized`, `test_revive_mid_mission_holds_then_terminal_delivers`, `test_boot_sweep_holds_live_mission_then_delivers_once`), receipt pin GREEN. **Guard stuck (`live=True` forced)** → all 4 delivery tests FAIL (both crash-restart + both true-terminal), receipt pin GREEN. **NON-VACUOUS in both directions.** Patched-module set: mission_live_guard + job_recovery_service + job_queue_service (complete per sys.modules scan).
- **Coverage lost: ZERO.** Every iter-1 test survived, was strengthened onto the real chain, or was split into its genuine halves (crash-before vs crash-after finalize); the mislabeled revive test renamed to `test_revive_mid_mission_holds_then_terminal_delivers`.

## 4. Residuals (non-blocking, nice-to-have)
- 🟢 Dev's "mutation probes run" claim referenced nothing committed — recommend committing a probe test (monkeypatch `evaluate_mission_live` counterfactuals) or re-wording; my `/tmp/jobevent_probe/probe_plugin.py` reproduces it and can be adapted.
- 🟢 `lock_manager`/`queue_repo` are under-specified MagicMocks (L257-258) — safe today; refresh the seam contract if `notify_work_watchers` ever consults locks/queues.
- 🟢 Pre-existing project debt unchanged from iteration 1 (13 job_queue reds, Phase-1 canonical map ×7, PG premature-completion files module-skip-dead, api.py size pin, b1 cwd hard-code) — all base-identical, none attributable to this branch.

## 5. Final verdict
**PASS.** Iteration-1 FAIL drivers (iii missing; i/ii weak; M1) all closed with real-path, durable-witness, byte-pinned tests; tests-only diff verified; 20/20 green; `tests/job_queue/` FAILED census identical to base; guard non-vacuity independently mutation-probed. The fix's non-regression guarantee for the original missing-report bug is now directly tested on both repair owners.

*Workers (iteration 2): d4d7c83a (diff gate) · 8451b4dd (guard re-run) · c07b8592 (job_queue census) · 2d9ff73c (gap-closure audit) · 4d7a49e4 (mutation probe).*
