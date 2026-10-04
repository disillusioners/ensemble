# Test Report: fix/claim-gate-sibling-deadlock — FINAL MERGE GATE (HOLD-promote)

Date: 2026-10-04
Target: branch `fix/claim-gate-sibling-deadlock` @ `aced9ead` (4 commits atop base `2ae91046`, unpushed) — commit chain db717781 (guard relaxation + belt) → e7e42f7b (belt ACTIVE-only + busy-instance mirror) → 30e1c7a4 (chat-pool SUBSTR/LENGTH) → aced9ead (tidy).
Commission: final gate before merge; execution-lane change (claim gate + admission + chat-pool claim filters) → full gate pack per project e2e rule. Gate doc = ensure.md (grep-verified: 53-line pack-mapped version).
Worker instances (24, all dual-layer timeout, zero repo modifications, port 8088 never touched): recon d02802b8 · baseprep 0c0fa980 · pinA 80035b33 · pinB 3cbec45d · concurrency b4c7bd1b · pgsmoke f7b0f7c4 · fix-g1..g8 b999d0e7/368007bd/fb4b42e6/efea2f8d/bc50764f/3fbc8968/3eb85623/4a92be6c · base-g1..g8 835a85be/f16e5d87/81a54713/1437cd54/03ae6d56/30f2b54b/19bff4fe/ba0b56e6 · base-concurrency 90d1d879 · flakecheck 9d51c4fd · e2e 1acd6247 · rca-instancepause c6eba4f8 · rca-guard 9ee740a4 · f1-probe 3722f7c6 · cleanup 3360b8a4.

## VERDICT: 🔴 HOLD — NOT READY TO MERGE

Two fix-introduced reds, one of them a production over-relaxation on an ensure.md **Critical** gate (Core #2/#3 deadlock/concurrency integrity) — for a branch whose purpose is fixing a claim-gate deadlock.

| # | Red | Class | Severity |
|---|---|---|---|
| 1 | `tests/test_report_lane_phase2.py::TestReportLaneGuard::test_process_message_blocked_by_cross_system_guard` (concurrency_atomic_unit_test pack) | **FIX-INTRODUCED — production over-relaxation** (commit db717781) | 🔴 critical — merge blocker |
| 2 | `tests/job_queue/test_instance_pause.py::TestJobQueueServiceInstancePause::test_start_job_processes_running_instance` | **FIX-INTRODUCED — test-fixture gap** (missing belt mock) | 🟠 important — 1-line test fix on-branch |

The original deadlock itself IS closed (pins 14/14, PG predicate 49/49, e2e cascade green) — but the fix as shipped trades the sibling-deadlock for a cross-system-guard protection gap.

---

## Gate 1 — New pin suites: ✅ PASS (14/14)

| Pack | Result | Runtime | Evidence |
|---|---|---|---|
| `test_claim_gate_sibling_deadlock_pins.py` (6 pins) | **6/6 PASS** | 3.73s | /tmp/claimgate-ab/pinA.{xml,log} |
| `test_chat_pool_starvation_pins.py` (8 pins) | **8/8 PASS** | 4.26s | /tmp/claimgate-ab/pinB.{xml,log} |

Independent confirmation of dev's two green runs. Includes `test_two_rapid_messages_both_drain_in_order` — the ORIGINAL incident scenario (critical note 6ab92289/8fe271db family) proven drained in order. Tree byte-stable pre/post (porcelain verified).

## Gate 2 — Full-dir tests/job_queue/ A/B vs base 2ae91046: ✅ attribution complete

Method: established drill — fix leg in main checkout (cwd-resolved daemon), base leg in detached worktree `/tmp/ens-claimgate-base-2ae91046` (HEAD-proof + `daemon.__file__` inside-worktree proof per pack, `.env` parity, same interpreter/invocation), 8 balanced partitions (104 common files, 2010 tests; the 2 fix-only pin files are Gate 1 territory), JUnit XML both legs, per-file verbatim comparison. json-report absent → `--junitxml` capture.

**Fix side: 1956P / 15F / 39S (2010). Base side true reds: 14F (identical files/tests) — after resolving 3 location-artifact F.**

### Red-attribution table (every red accounted)

| File | F (fix) | Base outcome | Attribution | Root cause (verbatim evidence) |
|---|---|---|---|---|
| test_round2_council_fixes.py (g1) | 1 | FAIL identical | ✅ PRE-EXISTING | Py3.13 `RuntimeError: no current event loop` — `asyncio.get_event_loop()` at :938 |
| test_job_answer_tool.py (g2) | 1 | FAIL identical | ✅ PRE-EXISTING | `Last tool in create_job_tools() should be 'job_answer', got 'job_resume'` |
| test_instance_pause.py (g3) | 1 | **PASS on base** | 🚨 **FIX-INTRODUCED** | `assert None is not None` :208 — see RCA-1 |
| test_in_progress_guard.py (g5) | 2 | FAIL identical | ✅ PRE-EXISTING | `fake_sync() takes 5 positional arguments but 6 were given` @ job_feedback_observer.py:1828 (mock-signature rot) |
| test_terminal_write_census.py (g6) | 2 | FAIL identical | ✅ PRE-EXISTING | unlisted site manager.py:5781 + 13-entry stale hooked_at list (verbatim match) |
| test_event_driven_completion.py (g8) | 3 | FAIL identical root | ✅ PRE-EXISTING | `MagicMock is not JSON serializable` via complete_task json.dumps (line 2852 base → 2992 fix = diff-induced shift, same family) |
| test_jober_watch_integration.py (g8) | 2 | FAIL identical | ✅ PRE-EXISTING | `assert 24 == 22` tool count + watch_events `'settled' != 'failed'` drift |
| test_n8_hot_path_pin.py (g8) | 1 | FAIL identical | ✅ PRE-EXISTING | outbox zero-calls; root `_fake_sync() takes 5 args but 6 given` |
| test_watcher_repository_concurrent.py (g8) | 2 | FAIL identical | ✅ PRE-EXISTING | watch_events `'settled' != 'failed'` drift ×2 |
| test_f1_killswitch_tz_matrix.py (g2, base-only) | 0 (fix PASS) | 3F | 📍 LOCATION-ATTRIBUTED | worktree lacks `.venv/bin/pytest` (child-process spawn) — probe: 3/3 PASS with symlink bridge (transplant leg; teardown clean) — NOT a code signal |

All green groups identical both legs (g4 232P/0F/19S, g7 252P/0F, g3-rest 250P, etc.). No base-only code divergence anywhere. No TIMEOUTs; all partitions <77s vs 300s cap.

## Concurrency pack — ensure.md Core #2/#3: 🔴 FAIL on fix (fix-introduced)

| Leg | Result | Evidence |
|---|---|---|
| FIX @ aced9ead | **97P / 1F / 74S** (65.5s) | /tmp/claimgate-ab/concurrency.log |
| BASE @ 2ae91046 | **98P / 0F / 74S baseline-exact** (73s; target test PASS verbatim via `-rA` reporting re-run) | /tmp/claimgate-ab/base-concurrency{,-verbose}.log |
| Flake budget | 3/3 deterministic FAIL, identical assertion (0.3s each) | /tmp/claimgate-rca (flakecheck) |

### RCA-1 (merge blocker): cross-system guard over-relaxation — production code

- Causal hunk (git blame @ `daemon/repositories/task/repository.py:2529`): **db717781** added `AND j.job_type != 'message'` to the claim-gate cross-system guard subquery — unconditionally.
- The failing test pins a real invariant: ACTIVE JobItem + genuinely in-flight backing Task (PAUSED ∈ guard's in-flight set pending/running/paused) MUST block a second `process_message` claim. Post-fix the subquery excludes ALL message JobItems → empty set → guard no-ops → claim proceeds → protection removed.
- The fix's own SAFETY ARGUMENT's backstop (per-instance RUNNING-only guard) does **not** cover PAUSED backing Tasks → no remaining layer enforces the invariant.
- Second site carries the same filter: `has_pending_tasks_blocked_by_busy_instance` (e7e42f7b "FOLD-IN 5").
- Verdict: TOO BROAD (not a stale pin). Cross-check: sibling test `test_process_message_unblocked_when_message_id_matches` passes both legs — subquery mechanics fine; the filter is the sole cause.
- Remediation options (dev decision, evidence in RCA): (a) scope the exclusion to the sibling message-mirror case; (b) revert the claim-path filter and rely on the tightened ACTIVE-only belt (structurally correct layer); (c) add a `job_type="task"` variant pin before shipping any weakened guard.

### RCA-2: instance_pause — test-fixture gap

- db717781's belt calls `find_active_message_job_for_instance`; the test fixture wires only `repo.get`, so MagicMock auto-attr returns a truthy mock → belt correctly declines → `start_job` returns None → `assert None is not None` (:208).
- Production semantic judged intended + precisely scoped (message-only, ACTIVE-only post-council-rework, self-excluded; task-jobs mint fresh instance_ids).
- Fix: 1 line on-branch — `mock_repository.find_active_message_job_for_instance = MagicMock(return_value=None)` (+ ideally a belt-positive test).

## Gate 3 — Release-Gate E2E (fix-tree daemon): 🟡 2/4 PASS, both reds base-attributed

Method: own daemon on :8090 (uvicorn direct, `--timeout-graceful-shutdown 10`, own PG `ensemble_gate3`, dev-lane LLM proxy), live :8079 v0.16.12 daemon untouched (uptime continuity proof), E2E_BASE_URL override, ensure.md prerequisites (SSL unset, PYTEST_TIMEOUT=280/--override-ini timeout=280, queue cleanup per item, one-by-one -k).

| Item | Verdict | Attribution |
|---|---|---|
| test_terminate_after_spawn_then_revive | ✅ PASS (44.8s) | — |
| test_three_level_cascade_reports | ✅ PASS (128.6s) | — the commission's cascade gate is green on fix-tree code |
| test_parent_child_workflow_happy_path | ❌ FAIL (51.7s) | **Base-pre-existing desync**: workflow COMPLETED (leader+child `completed`); test's terminal set lacks `"settled"` — `watcher_models.py:13 ALL_TERMINAL_STATES` carries settled on BASE too (proven by base-leg watch_events reds). Test debt, not branch-caused. |
| test_pause_after_spawn_then_resume | ❌ FAIL (150.4s) | Same settled desync + **known quarantine symptom** (pack header 2026-08-21 deselect family: child did not reach paused in 15s). Quarantine-aware → does not red the gate. |

Teardown proof: PID 3527684 killed (own child only), :8090 free, :8088 never touched. Scratch DB + data_gate3 + base worktree removed post-gate; main checkout porcelain = pre-gate baseline exactly.

## Gate 4 — ensure.md gates: 2 Critical FAIL (fix-caused), rest PASS

- Core #1 (no regressions in changed packs): ❌ FAIL — 2 fix-introduced reds (RCA-1, RCA-2)
- Core #2/#3 (concurrency_atomic_unit_test / sync-DB-off-loop): ❌ FAIL on fix — 97P/1F/74S vs baseline-exact 98P/0F/74S on base (thread-identity 97 all pass; the 1F is the guard regression, not a sync-DB lapse)
- Core #4 (dev.sh `--timeout-graceful-shutdown 10`): ✅ PASS (dev.sh:102)
- Important #1 (async-await callers): ✅ PASS — all 8 call sites awaited (messages.py:901, instances.py:352/548, manager.py:9812, tools/instance.py:3402, instance_messaging.py:1059/1089/1300)
- Important #2 (original deadlock scenario): ✅ functionally PROVEN closed (pin `test_two_rapid_messages_both_drain_in_order` + PG smoke) — while the pack gate itself is red via RCA-1
- Nice-to-have (dead code from fix): ✅ PASS — `_read_task_instance` 0 refs repo-wide; other removed imports unused in origin files only (tidier claims hold)
- Release-Gate full non-integration suite: scoped per commission to the execution-lane full-dir (tests/job_queue/ complete) + concurrency pack + e2e items; repo-wide all-packs sweep not run (out of this commission's gate list; note for release ceremony)

## Gate 5 — PG predicate smoke (optional, high-value): ✅ PASS 49/49

Real `_chat_source_exists_sql()` (imported, task/repository.py:60) embedded in production-shape claim UPDATE…RETURNING, executed on live ensemble_dev PG (rollback-wrapped, 7-table zero-leftover proof): positive path claims; all 6 over-match variants (LIKE-underscore, case, interior substring, no-colon, extended, suffix) correctly REJECTED end-to-end (claim → None, task stays pending). 7 groups × 7 patterns. **PG residual-risk note closed.** Evidence: /tmp/claimgate-pgsmoke/run.{py,log}.

---

## Scope Decision

Full gate pack run — warranted: execution-lane change (claim gate + admission + chat-pool claim filters) per project e2e rule; commission prescribed gates 1–5 and all were executed (gate 5 optional→executed). No scope reduction taken.

## Tooling notes (report-only)

1. `test/packs/concurrency_atomic_unit_test.sh`: `set -euo pipefail` + `EXIT_CODE=$?` after the pytest line suppresses `RESULT: FAIL` output on red runs (invisible on green). Parse pytest summary/exit as authoritative.
2. Worktree legs needing `.venv/bin/pytest` (child-process tests): symlink bridge `.venv → /home/nea/ensemble-src/.venv` works, but the editable `.pth` wins over `PYTHONPATH` for **child** daemon imports (direct pytest resolves correctly via cwd/PYTHONPATH — proven per-pack). Stricter isolation needs per-worktree `uv sync` venv.
3. `tee` masks pytest exit under `/bin/sh` (no pipefail/PIPESTATUS) — all workers cross-checked JUnit XML + summary lines (done consistently).

## Action Needed (dev/leader)

- [ ] 🔴 Re-scope or revert the `j.job_type != 'message'` guard exclusion (db717781) — restore cross-system-guard coverage for in-flight backing Tasks; re-run `test_process_message_blocked_by_cross_system_guard` + full concurrency pack (both sites: claim path AND has_pending_tasks_blocked_by_busy_instance)
- [ ] 🟠 Add `find_active_message_job_for_instance` mock to test_instance_pause fixture (1 line) + belt-positive test
- [ ] 🟢 (test-debt, base-attributed — do not block this branch): settled-token terminal-set update in test_e2e_workflows (:1429/:1927); census fixture; watch_events default-list assertions; fake_sync mock arities; job_answer tool-order pin; council event-loop modernization; MagicMock-not-JSON-serializable fixtures
- [ ] Re-gate after fixes: pins + concurrency pack + g3/g5 partitions minimum

## Documentation Updated

- [x] RESULTS/2026-10-04-claim-gate-sibling-deadlock-merge-gate.md — this report
- [x] PACKS.md — commission record + concurrency pack Last Run (FAIL on fix @ aced9ead; base PASS @ 2ae91046)
- [x] QUARANTINE.md — consolidated base-attributed family row (8 files / 14 reds + f1 location note)
- [ ] rules/ensure.md — no changes (user-maintained)
- [ ] MOCK_TESTS.md — no durable mock spec added (PG smoke was a one-shot rollback-wrapped probe; spec recorded here)

## Code Changes Summary

None. Verification-only commission — zero modifications, zero commits by tester or any of 24 workers. Repo byte-stable throughout (porcelain = pre-gate baseline at close; base worktree + scratch DB/data dir removed cleanly).

### Overall Status
- Gate 1 pins: ✅ PASS · Gate 2 full-dir A/B: ✅ complete, 1 🚨 fix-introduced · Concurrency (Core #2/#3): 🔴 FAIL fix-introduced · Gate 3 e2e: 🟡 2/4 (reds base-attributed) · Gate 4: 2 Critical FAIL (fix-caused) · Gate 5 PG: ✅ PASS
- **Testing Complete: ❌ NOT READY — HOLD** (remediation path documented above; re-gate after dev fixes)
