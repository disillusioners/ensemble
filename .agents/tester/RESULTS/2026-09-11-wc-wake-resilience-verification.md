# WC-Wake-Resilience Verification — feature/fix-wc-wake-resilience @ 55a76bb5

**Gate type:** independent verification (dev/review claims NOT trusted — re-derived)
**Branch head verified:** `55a76bb5df1c518c8ea9b99f7ba758e1fca4e7e2` (17 commits over base `8a30f75b0fe650c5798c375eaf65e5cbad812a7a`, "Merge branch 'fix/jobs-status-combo-filter' into latest")
**Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-wc-wake-resilience` (clean tree at verification start)
**Verification commits added on-branch (test-only, clearly labeled):**
- `f04611aa` — `test: verification gap-fill (wcw): B4 backstop behavioral tests + control-flow ruling` (1 file, +721)
- `a880b033` — `test: verification gap-fill (wc): A5×B3 interaction, B3 fail-closed, W-D resume, episode hygiene` (5 files, +2380)
- `a7d7b760` — `test: fix (wcw verification arc): restore REPO_ROOT constant dropped by branch rewrite of test_instance_tools` (1 file, +4, base-exact restore)
**Method:** every test run via `uv run python -m pytest` (bare pytest = broken foreign Homebrew), dual-layer timeout everywhere (`timeout 300` command-level + script-internal 280s / ini per-test 30s), drift-pinned per pack (test-only sibling-commit drift rule). PG runs on PRIVATE disposable PG14 clusters (ports 15431–15433, `initdb -A trust`, house recipe) with `POSTGRES_*` env scrubbed wholesale and `PG_TEST_*` pinned; `ensemble_prod` and port 5432 never used for test writes; no daemon boot anywhere.

---

## 1. Full-suite base-relative gate (default selection `-m 'not integration and not postgres'`)

### 1a. HEAD partition runs (raw pytest lines authoritative)

| Chunk | Pack (registered) | Raw final pytest line | F/E classification |
|---|---|---|---|
| C1 | regression_unit_tools | run1 @f04611aa: `11 failed, 2558 passed, 5 skipped, 50 warnings in 12.14s` → **after quick-fix a7d7b760**: `2569 passed, 5 skipped, 50 warnings in 12.39s` RESULT: PASS | run1: 11 = **branch-caused** (`NameError: REPO_ROOT` — branch rewrite dropped module constant) → **FIXED at victim**, partition restored PASS. 5 TestAccessMemoryArchive deselected by script (quarantine-expected, deselect proven by arithmetic 2574 = 2579−5) |
| C2 | regression_unit_services | `7 failed, 1547 passed, 101 warnings in 12.94s` | 7/7 = quarantined `job_queue_proxy_phase1` derived-status family (exact node set). 0 suspects. Node count 1,554 = baseline 1,359 + ~9 branch test files, all green |
| C3 | regression_unit_smaller_subdirs_routers | `647 passed` (12.31s pytest) | 0F/0E |
| C4 | regression_unit_loose_a_d | `10 failed, 1362 passed, 2 skipped, 5 warnings, 21 errors in 15.90s` | 31/31 quarantine-expected (api-module-size 1, coder/devops prompt-meta 9, builtin-MCP mock_config 17, context7 blueprint 4); count-parity vs 2026-09-08 baseline (F 10→10, E 21→21) |
| C5 | regression_unit_loose_e_l | `19 failed, 1168 passed, 16 warnings in 43.68s` | 6 quarantine (status-guard ×4, llm_allowed_models ×2) + 13 `TestFindNearInstance` (unpack 3-got-2 @ manager.py:10625) — **PROVEN pre-existing at base** (base run: `13 failed, 13 passed in 0.21s`, identical shape @ :10539) |
| C6 | regression_unit_loose_m_r | `10 failed, 2005 passed, 40 skipped, 90 warnings in 63.40s` | 5 quarantine (paused_auto_resume MagicMock-await ×5) + 5 suspects **ALL pre-existing at base** (release-tag pin v0.12.4≠v0.12.6; phase4 facade `cascade_to_root` kwarg; project-manager prompt ×2 — matches 2026-09-10 gate adjudication; models_split `LivezResponse`) |
| C7 | regression_unit_loose_s_z | `54 failed, 984 passed, 11 skipped, 28 warnings, 2 errors in 16.46s` | 47 quarantine (watchover cascade) + 2 webfetch setup-errors (complete the documented blueprint-fixture 6-error family) + 7 suspects: 5 **pre-existing at base** (terminal_reason mirror literals; wanderer allow 15≠13 ×2; validate_agent_id get_registry; vision MagicMock-await) + **2 BRANCH-CAUSED** (`test_task_reconciliation` ×2 `TypeError: Logger._log() got unexpected kwarg 'work_id'` @ task/repository.py:3128 — base: `13 passed in 0.69s`) |
| C8 | regression_top_level_a_h | `20–21 failed, 1033 passed, 52 skipped, 31 warnings, 2 errors in 66.55s` (xdist variance; 21 unique) | 9 quarantine (fresh-SQLite migration-20260714 trap, tests/manager ×9) + 12 suspects **ALL pre-existing at base**: chokepoint static pair FAILS AT BASE with identical nodes+files (manager `_resume_processing_background`/`_ensure_postgres_columns`, worker_pool `_usage_limit_episode_decide`, job_recovery `_pattern_e_dead_letter_sweep_sync` — branch only shifted line numbers); agents_api registry-leak ×2; test_api MagicMock-await ×2; enqueue_shared dual-dispatch; ui_prefs `search` kwarg ×2; migration jsonb ×2 + perf-matrix (env-class: xdist PG-schema race / missing cells); 1 perf flake first-run-only |
| C9 | regression_top_level_i_q | `58 failed, 2388 passed, 73 skipped, 46 warnings in 20.89s` | 58/58 in 5 documented pre-existing families (awaited-dispatch mock 25, fresh-SQLite trap 18, inner_soul re.search mock-ripple 9, agent-meta drift 3, facade-forwarding 1, test-of-tests 1); baseline-consistent (2,370P/60F/73S @ 2026-09-07 → improved) |
| C10 | regression_top_level_r_z_misc | `14 failed, 2258 passed, 34 skipped, 5 xfailed, 1140 warnings in 19.70s` | 1 context-flake (`TestCompleteAtomic` — base-pass 2/2, timing class) + 13 suspects **ALL pre-existing at base** (spawn_limit SQLite-trap ×9; skill-evolution config ×2; terminal-orphan matrix ×1; leader_team_members `maintenancer` registry drift — introduced by d5659c4a which is IN base) |
| C11 | regression_job_queue | `7 failed, 1723 passed, 38 skipped, 1528 warnings in 22.15s` | 7/7 = exact quarantined settled-rename stale-fixture family (node-for-node, signature-for-signature); +45 passes from branch (a2/a3/a4 = 38) + vgap (7) tests |
| C12a | integration a–m (74 files) | `6 failed, 356 passed, 1 skipped, 13 warnings in 14.99s` | 6/6 pre-existing (NOT branch-caused): ×4 Turn-Reconciler Phase-4b/4c deferred family (`test_complete_cancel_route_through_transitions` admission_state/terminal_reason, file added 18675fc3 2026-08-02, matches documented deferred-fences critical note) + ×2 `maintenance.py:617` orphan-cleanup unpack crash (3-got-2 — same pre-existing tuple-widening ripple class as find_near; file added 714f58ff 2026-09-04). cold_resume_ttl ×2 PASSED here; no httpx/U11 surfacing |
| C12b | integration n–z (35 files; 164 collected / 121 deselected) | `6 failed, 154 passed, 124 warnings, 4 errors in 11.24s` | 8/10 map to documented QUARANTINE.md rows: SSL-env httpx spill class ×6 (ambient `SSL_CERT_FILE/DIR` PyInstaller-certifi pollution verified present in worker shell — mechanism confirmed; incl. `test_wc_wake_pure_hang::test_agent_tool_send_message...` which PASSES clean standalone per repro-integ run — env-class, not branch), M2-gate MagicMock queue_type ×1, vscode C1 proxy ×1. 2 suspects (`test_vscode_routing_e2e::TestSpaCatchAll` ×2, SPA-fallback 404 mode, no exact quarantine row) → base-check dispatched |
| C12c | opencode + e2e (1 mandatory ignore) | `4 failed, 516 passed, 1 skipped in 17.72s` (49 deselected) | 4/4 quarantine-expected (Release-Gate 1e/1f rows: pause-during-report trio + answer_dismiss_flow T0-mirror — all base-evidenced, c171a289 semantic-shift family); 0 suspects; httpx pollution not observed |

**DEFAULT-SELECTION FINAL TOTALS (post-D1-fix, all 18 chunks):** **18,841 passed / 216 failed + 29 errors / ~257 skipped** across 19,353 collected (550 marker-deselected by selection + per-chunk deselects: 5 archive + 49 C12c-marker + 121 C12b-marker). Dev-expected shape ~18.8k passed / ~236 failed / ~23 errors — **converged** (delta = D1's 11 fixed failures + isolation-method differences). **Branch-caused failures: exactly ONE class — D1 (11 nodes), FIXED at victim `a7d7b760`.** D2 (2 red nodes) is pre-existing latent `114d1cc5` code surfaced by harness logging config — see §1c. vscode SPA ×2: base-proven PRE-EXISTING. **Every one of the 216F+29E is now adjudicated.**
| C13 | GAP: unit/checkpoint_adapter + unit/persistence | `72 passed in 3.78s` | 0F |
| C14 | GAP: daemon/tests | `46 passed in 2.80s` | 0F |
| C15 | GAP: test/ probes (2 mandatory ignores: bytecompat sys.exit probe, vscode playwright) | `13 passed, 55 warnings in 10.39s` | 0F |

**Collection landmines (documented, mandatory ignores):** `test/packs/wc_wake_off_bytecompat_probe_test.py` (import-time `sys.exit(1)` w/o `DAEMON_BYTECOMPAT_ROOT`), `tests/packs/g7_unique_index_smoke_test.py` (module-level `sys.exit(0)` — **newly documented this gate**), `test/packs/vscode_e2e_browser_test.py` (playwright absent), `tests/e2e/test_context_injection_hybrid.py` (import-time live HTTP → daemon boot forbidden).

### 1b. Triage verdict (mission categories)

| Category | Count | Evidence |
|---|---|---|
| (a) Quarantined TestAccessMemoryArchive | 5 | Identity confirmed vs QUARANTINE.md rows 41–45 char-for-char; failure class = documented `access_memory` 'Access denied'; deselected in C1 script (arithmetic-proven); direct run `5 failed in 0.17s` all same class |
| (b) Accepted flake TestGenerationCounterBump | 1 node | 3× isolated serial: 2P/1F; failing run signature = exact documented `sqlite3.InterfaceError` StaticPool race; other 7 nodes stable-pass 3/3; in-context full-file run: 8/8 pass |
| (c) PROVEN pre-existing at base 8a30f75b | all remaining F/E | Detached base worktree `/tmp/wcw-base-8a30f75b` (pack scripts byte-identical base↔branch); every non-quarantine suspect re-run at base — see per-chunk table; **zero unresolved** |
| **Branch-caused NEW failures** | **13 test nodes** | **11** = REPO_ROOT NameError (test-code; FIXED at victim `a7d7b760`, C1 restored `2569P/5S/0F`); **2** = task_reconciliation `work_id` logger TypeError (**production-code defect**, UNFIXED — verification-only) |
| Dev-expected shape sanity | — | Dev claimed ~18.8k passed / ~236 failed / ~23 errors. Ex-C12 actual: 17,815 passed / 200 failed (post-fix) / 25 errors; with C12 (~1,020 default-filtered nodes incl. known families) totals converge on dev's shape. **[FINAL TOTALS PENDING C12]** |

### 1c. The two branch-caused defects

**D1 — REPO_ROOT drop (test-code, FIXED).** Branch rewrite of `tests/unit/tools/test_instance_tools.py` (+339) preserved all 13 usages but dropped the base definition (base :95 `REPO_ROOT = Path(__file__).resolve().parents[3]`, with portability comment). All 11 failures single root cause `NameError`. Fix `a7d7b760`: base-exact 4-line restore; whole file `191 passed`; C1 pack `2569 passed, 5 skipped, 0 failed`.

**D2 — `work_id` logger kwarg TypeError (production code, PRE-EXISTING — reclassified by attribution).** `daemon/repositories/task/repository.py:3128-3132` (HEAD; `:3033` at base — **byte-identical**) passes `work_id=`/`count=` as bare logging kwargs on a plain stdlib logger → `TypeError` whenever ANY handler is attached. **Introduced by `114d1cc5` (2026-08-11 "Phase 1 task-reconciliation") — an ANCESTOR of base `8a30f75b`** (merge-base verified); the wc-branch's only touch to the file (`4c3b0973` W-B) inserts a new method with zero logger hunks (`git blame -L 3120,3140` → all `114d1cc5`). The observed base-pass/HEAD-fail split is a **test-harness logging-config delta, not a code delta**: stdlib `logger.info` is a silent no-op with no handler (serial bare run → passes, incl. base-triage run) and raises under xdist/log-capture handlers (C7 pack run at HEAD → 2 nodes red); blame worker reproduced BOTH outcomes at HEAD (`2 passed` bare vs `1 failed` under `--log-cli-level=INFO`). **Runtime severity MEDIUM-HIGH (observability, not correctness):** fires on EVERY orphan reconciliation in prod (`job_feedback_observer.py:4021-4039` Step 4, flag `TASK_RECONCILIATION_BEST_EFFORT` default ON) but is caught by the surrounding try/except — the reconciliation UPDATE itself commits; the intended INFO `task.reconciled_to_cancelled` is lost and replaced by WARNING spam (`Step 4 reconcile_terminal_task failed ... TypeError`). Sole invalid kwarg-form call in the entire `daemon/` tree (exhaustive AST scan; the other 34 structured calls use valid `extra=`); the branch's 49 new logger calls are all valid. **Suggested fix (report-only):** `logger.info("task.reconciled_to_cancelled", extra={"work_id": work_id, "count": count})` — 2 lines, matches the sibling `extra=` convention (e.g. dependency_bus.py:580).

---

## 2. Wedge-release repro pack — per-fix verdicts

| Fix | Incident shape | Verdict | Evidence |
|---|---|---|---|
| A1 revival carve-out (33231) | message reactivating terminal instance born deferred | **VERIFIED** | `test_a1_revival_carve_out.py` 9/9 — REAL `_prepare_enqueued_message`, caller `is_deferred=True` × 4 terminal statuses → born `is_deferred=False`; non-revival statuses preserve flag; e2e claim tail covered by vgap greens (claim_pending_task actually claims) |
| A2 autopromote notify | born-deferred → flip → notify → claimable | **VERIFIED** | `test_a2_autopromote_notify.py` 8/8 — real repos file-backed SQLite, born-deferred PENDING backdated past 60s grace, REAL `reconcile_drift_states` → flip landed in DB + `notify_work` fired once; negative guards (atomic-skip, disabled, seam-busy, pool-none, notify-failure-isolated) |
| A3 eligible-PENDING sweep (ef495b32/33009) | missed creation-notify → sweep → claimed | **VERIFIED** | `test_a3_eligible_pending_sweep.py` 18/8+10 — aged eligible row → real `sweep_once()` → notify; skips deferred/running/young; idempotent no-op; W-D liveness filter ×5 via REAL repo; double-notify idempotency proven by vgap greens (atomic claim → single dispatch) |
| A4 completion guard orphan partition | claimable-orphan → notify+skip-park; live → park | **VERIFIED** | `test_a4_f14_orphan_detection.py` 12/12 — P1 shape → notify+fall-through; live/deferred/mixed → park preserved (guard purpose explicit); REAL partition helper vs seeded rows; notify-failure non-blocking |
| A5 wedge-notice direct notify (33252) | watchdog notice task directly notified | **VERIFIED** | `test_a5_wedge_notify_work.py` 9/9 — real watchdog `run_once()` on real instance repo, wedged shape → direct `notify_work`; suppressed-by-B-guard / paused / cooldown / live-carrier negatives; enqueue AsyncMock'd (claim tail via greens) |
| B1 WC durable send (no 202-park) | WC-targeted send → durable enqueue + wake | **VERIFIED (strongest)** | unit `test_b1_wc_durable_send.py` 12/12 (routing ×2 lanes + 8 static invariants incl. import-level pin that flag machinery stays removed — `ENSEMBLE_WC_WAKE_ENQUEUE` resolver/env/boot-log all deleted, negatively pinned) + **integration `test_wc_wake_pure_hang.py` 3/3**: real InstanceManager + real SQLite + 1-worker WorkerPool + real graph (scripted LLM); HTTP POST / agent-tool / job-inject lanes each: durable `message_id`+`job_id` (never 202-shape), WC→RUNNING flip ≤5s, wake token consumed by real turn, report delivered via `internal_report:`. ⚠️ API-tier pins in `test_injection_api.py` (e.g. `test_waiting_children_routes_to_enqueue`) FAIL with the pre-existing MagicMock-await family — they never execute assertions; B1's proof rests on unit+integration lanes (sufficient) |
| B2 resume wake-or-refuse (no silent no-op) | resume on WC-parked parent | **VERIFIED** | `test_b2_resume_wake_or_refuse.py` 12/12 — REAL `resume_processing_job` bound via MethodType; WC+silent → `wake_enqueued` (never `silent_resume`); enqueue raise → loud `wake_failed` + `refusal_kind="wc_wake_failed"`; non-WC/missing preserve no-op; non-silent preserved; unpark effect covered by S6 (same primitive) |
| B3 escalation/release + liveness | hung child escalate@3/release@5; heartbeat suppresses; DB-error fail-closed | **VERIFIED (with note)** | `test_b3_watchdog_escalation.py` 12/12 (thresholds 3/5 pinned, once-per-episode cooldowns, episode reset, enqueue-failure safety) + `test_w_b_watchdog_liveness_gate.py` 18/18 (beating heartbeat suppresses escalation AND release while counter climbs; heartbeat→silent escalates; per-pair isolation; real `child_has_recent_heartbeat` SQL integration ×7) + vgap `test_vgap_b3_dberror_failclosed.py` 3/3 (raising probe → no release/escalation enqueue). **Note:** fail-closed holds via the BROAD per-parent `except Exception` (watchdog :1267) — by accident, not design; recommend explicit try/except around the probe (vgap test fails loudly if the broad catch is ever tightened) |
| B4 obligation backstop (84563a03) | child completes, no terminal emit → exactly-once force-emit | **🔴 FAILED for target mechanism** | Control-flow ruling (independently re-derived, line-cited): backstop @ child_reports.py:4119-4190 is the final `else`, reachable ONLY for unknown outcome strings — every canonical outcome early-returns BEFORE it (deferred_waiting_children :3640, root_waiting_children :3655, child_still_running_defer :3743, root_completed :3802, **idempotency_skip :3806**, deferred_pause :3822, dead_parent_skip :3841, tool_invocation_completed :3869, regular_child_completed :4112 [self-heals via unconditional corrective emit :3907-3912 then flags out], instance_not_found :4117). Commit fef2b792 message claims backstop handles idempotency_skip + 5 more — **implementation does not**. The exact 84563a03 recurrence (idempotency_skip, COMPLETED child, live parent, no emit) returns at :3806 with NO emit — wedge NOT healed. Parts verified PASSING: concurrent double-fire → single emit (bus guarded UPDATE); all 8 defer outcomes not force-emitted; unknown-outcome shape works. Dev's own behavioral test was a placeholder (`assert True` @ :217). Evidence: `test_vgap_b4_backstop_behavior.py` — 12 passed + **1 xfail(strict)** documenting the defect (commit f04611aa) |
| C1 ERROR-lane corrective pair-emit | ERROR child → (parent,child) emit → watcher not stranded | **VERIFIED** | `test_c1_error_lane_corrective_emit.py` 10/10 — REAL `_send_error_report`, both task-keyed + pair emits fire, independent try/except, correct pair args, defensive fallback, defaults pinned. Moderate: emit asserted at bus boundary (AsyncMock); watcher-fires-from-pair-emit shown indirectly via S6/integration family |
| C2 orphan-watcher sweep + grace | stale PENDING watchers healed; 30s grace; W-D paused excluded | **VERIFIED** | `test_c2_orphan_watcher_sweep.py` 17/17 + **bus-level real** `tests/test_dependency_bus.py::TestWCSweepGraceWindow` 7/7 (real bus + real watcher rows, created_at backdating vs real `DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS=30` @ dependency_bus.py:131, boundary ±precision, active-watchers not protected); W-D paused exclusion + prompt re-notify after resume: vgap `test_vgap_wd_resume_renotify.py` 4/4 (real TaskRepository + real instance repo, real W-D filter) |

**Interaction + edge coverage (mission §4):** A5×B3 interaction — vgap `test_vgap_a5_b3_interaction.py` 3/3 (no double-release/double-notify; release→wedge-cooldown race safe). Episode/counter hygiene — vgap `test_vgap_episode_hygiene.py` 6/6 (episode-end purges wedge cooldown via `_wedge_notified.discard` :1507/:1517; 25-pair × 5-tick bounded-growth invariant on all four tracking dicts). Sweep interplay — vgap W-D file 4/4 (excluded-while-paused → re-notified within one interval after resume; young-after-resume not notified until threshold). Heartbeat invariant — vgap greens: 30s cadence ≪ 3600s threshold (threshold > cadence × 60).

**Mock fidelity (TrueAuto discipline):**
- `child_has_recent_heartbeat(instance_id, threshold_seconds) -> bool` — REAL probe integration-tested on file-backed SQLite (tz-aware UTC binds, NULL-safe, ValueError on negative); watchdog plumb passes real `hang_threshold_seconds`; only data-seam mocked. Faithful.
- `notify_work()` — zero-arg global pulse everywhere; no test assumes task-id targeting; zero-arg contract itself pinned by pre-existing test. Faithful.
- Heartbeat constants — real 30.0 (worker_pool.py:45) / 3600 (watchdog :488) used; relationship invariant now pinned by vgap greens. Minor: 3600 mirrored as literal in 2 fixtures rather than imported constant.
- Divergences found (non-blocking): A1 docstring claims DB-row persistence but asserts captured constructor kwargs (`_read_task_is_deferbed` defined-never-called — doc drift); A2 `notify_watchers` stub returns None vs real `-> int` (ignored by recovery path — benign); A5 production `inspect.iscoroutine` guard is test-shaped but harmless.
- All mock signatures cross-checked vs real: `list_hung_children_for_parent`, `parents_with_non_terminal_children`, `list_terminal_instance_ids`, `list_live_process_report_carriers_for_instance`, `list_paused_or_terminal_instance_ids`, `resume_processing_job` KeyError contract, `emit_terminal_for_child_instance -> list[FollowUp]`. Match.

---

## 3. PG parity spot-pack (disposable private clusters, serial, env scrubbed)

| Chunk | Files | Raw line | Failures → base verdict |
|---|---|---|---|
| A–F (port 15431) | 7 files (concurrent_enqueue/jsonb_updates/lock_claims/status_transitions ×4 busy family, critical_notes ×1, dependency_bus_pg ×1, f9_post_commit_rearm ×1) | `41 passed in 3.40s` | 0 |
| G–R (port 15432) | 19 files (busy: premature_completion ×2, recovery; sweep: deferred_migration/savepoint/recovery; dependency_bus: obligation_triple, pause_report_orphan, deferred_savepoint, lane_phase2; MATRIX_A: recovery double-delivery matrix) | `4 failed, 165 passed, 33 skipped, 2 xfailed in 14.36s` | 2 mixed-context artifacts (session-scoped constraint-trigger bleed from jq_proxy_phase2; pass 5/5 isolated BOTH sides) + 2 **pre-existing at base** (claim_for_injection NULL-keys; Lane-2 alias canary — byte-identical shape at base) |
| S–Z+extras (port 15433) | smoke + wanderer + double_delivery_pg + defer_gate_post_settle_window + settings_api | `1 failed, 52 passed, 20 deselected in 4.78s` | 1 **pre-existing at base** (idle-predicate bool/int fixture `DatatypeMismatch`); settings_api quarantine family: all 12 marked nodes PASS, the 20 deselected are unmarked InvalidSchemaName-class (correctly excluded) |

**PG totals @ HEAD: 258 passed / 5 failed / 33 skipped / 2 xfailed — ZERO branch-caused PG failures** (all 5 adjudicated by base comparison + isolation runs). Dev's "233/4" expectation shape: directionally consistent (both sides small-failure); exact-count difference explained by chunk isolation method (2 of our 5 are mixing artifacts that a different slicing would not produce).
**Mission-family note:** busy/busy_predicate ✓, watchdog/sweep/dependency_bus ✓, MATRIX_A ✓ all ran PG-marked and green; **no PG-marked files match "constitution drift" or "work_resolver budget" by name** — those families run SQLite-side and were covered green in C9/C11 (work_resolver/job_queue partitions). Flagged for the record, not a gap in PG coverage of what exists.

---

## 4. FE compatibility (static, justified)

**COMPATIBLE — static-only.** Branch FE diff empty (daemon/ 17 + docs/ 3 + tests/ 22 only). Daemon shape changes: resume `wake_enqueued`/`wake_failed`+`refusal_kind` land only inside `resume_results` map entries — FE (`chat.component.ts:2017-2031` `next: () => {}`, `ResumeResponse` type reads only unchanged top-level fields) ignores payloads; WC-send 202→200 invisible to Angular HttpClient (both 2xx; explicit FE comment treats both as success); new 200 body is a superset of what FE reads (`queued`, `message_id` defensive). No spec pins any changed field. Product gap (not incompatibility): FE never surfaces `wake_failed`. **Correction for the record:** `ENSEMBLE_WC_WAKE_ENQUEUE` was REMOVED by B1 (not added); import-level negative pins confirm.

---

## 5. ensure.md validation

- Core R1 concurrency pack: **PASS** `98 passed, 74 skipped, 44 warnings in 6.71s` (exact canonical baseline).
- Core R2 dev.sh flag: **PASS** (verbatim `--timeout-graceful-shutdown 10` @ dev.sh:102).
- Core R3 no-sync-DB-on-loop: **PASS** (12 dedicated thread-identity nodes verified in pack scope; 2 in-file skips documented with equivalent-coverage pointer).
- Core R4 no-regressions-in-changed-packs: adjudicated from §1/§2/§3 chunk data (2 branch-caused defects found: D1 fixed, D2 open).
- Release-Gate E2E items: **out of scope — contradiction** (see Improvement Notice).

---

## 6. Improvement Notices & hygiene findings

1. ⚠️ **ensure.md Release-Gate E2E vs no-boot constraint** (ensure.md:37,:46-53 requires `./dev.sh` live daemon; mission forbids daemon boot). Validated: nothing (declared out of scope). Suggested rewrite per ensure.md's own :34 preamble: convert each E2E scenario to a daemon-mocked pack (`test/packs/e2e_<scenario>_mock_test.sh`, `timeout 300 bash …`), keep live-daemon variants in a separate operator-run section; also resolves the raw `.venv/bin/pytest` fallbacks (:47/:49/:51) conflicting with the file's own run-as-packs rule (:6).
2. ⚠️ **PACKS.md stale entry on this branch:** `wc_wake_flag_resolver_tools_unit_test.sh` targets `tests/unit/services/test_wc_wake_flag_resolver.py` DELETED by B1 (commit 88a27f71) — pack is half-broken at HEAD; recommend DEPRECATED marking or retarget when PACKS.md is next updated. (Pre-existing orphan: `test/packs/constitution_drift_test.sh` has zero PACKS.md mentions.)
3. 🟠 **B4 recommendation for dev:** hoist the backstop ABOVE the canonical-outcome early-returns (or emit inside the idempotency_skip branch when no prior emit is recorded), then remove the strict-xfail in `test_vgap_b4_backstop_behavior.py` (it will XPASS-fail until then, by design). The skip-list inside the backstop is dead code at HEAD.
4. 🟠 **B3 recommendation:** wrap the heartbeat probe in an explicit try/except returning fail-closed, instead of relying on the broad per-parent `except Exception` (vgap test documents this fragility).
5. 🟢 `tests/packs/g7_unique_index_smoke_test.py` module-level `sys.exit(0)` breaks bare collection (newly documented this gate; keep in the mandatory-ignore list).

---

## 7. Overall verdict for merge

**🟠 HOLD — one blocker remains (B4). All test gates PASS.**

- **Full-suite base-relative gate: PASS.** Exactly ONE branch-caused failure class — D1 (REPO_ROOT ×11, test-code), fixed at victim `a7d7b760` (C1 partition restored `2569 passed, 5 skipped, 0 failed`). Every other failure among the final **18,841 passed / 216 failed + 29 errors / ~257 skipped** is proven pre-existing / quarantined / context-flake / env-class via detached-base comparison (`/tmp/wcw-base-8a30f75b`, byte-identical pack scripts) + isolation/discrimination runs.
- **D2 reclassified PRE-EXISTING** (`114d1cc5`, ancestor of base; byte-identical code at base :3033; base-pass/HEAD-fail was a harness logging-config delta — stdlib no-op without handler). NOT a merge blocker for this branch. Strongly recommended 2-line `extra=` fix (MEDIUM-HIGH prod observability: WARNING spam on every orphan reconciliation; sole invalid logging call in `daemon/`; branch's 49 new logger calls all valid).
- **PG gate: PASS** — 258P/5F/33S/2xf on disposable private clusters, zero branch-caused (all 5 base-proven or mixing-artifacts).
- **FE: COMPATIBLE** (static-only justified; product gap noted: `wake_failed` never surfaced in UI).
- **ensure.md Core: 3/3 PASS** (concurrency exact-baseline, dev.sh flag, thread-identity); Release-Gate E2E contradiction recorded as Improvement Notice — no daemon boot per mission constraint.
- **Per-fix verdicts: A1–A5, B1 (strongest — real-engine integration 3/3), B2, B3 (with fail-closed-by-accident note), C1, C2 = VERIFIED. B4 = FAILED for its target mechanism** — the 84563a03 recurrence (idempotency_skip, COMPLETED child, live parent, no prior emit) returns at `child_reports.py:3806` BEFORE the backstop `:4119`; the backstop is reachable only for unknown outcome strings; commit `fef2b792`'s message claims coverage it does not implement. Parts 2+3 of the B4 spec (exactly-once under concurrent double-fire; defer-outcomes not force-emitted) verified PASSING. Strict-xfail `test_vgap_b4_backstop_behavior.py::...::test_target_shape_force_emits_idempotency_skip` (commit `f04611aa`) pins the defect and will XPASS-fail if left in place after a fix — by design.

**Path to green (merge-ready once resolved):**
1. **B4 (blocker):** hoist the backstop above the canonical-outcome early-returns, or emit inside the `idempotency_skip` branch when no prior emit is recorded, then remove the strict-xfail. Alternative: explicit product decision to accept the residual wedge window AND correct the commit-message/backstop claims (the dead skip-list inside the backstop should go either way).
2. Recommended non-blocking: D2 `extra=` logger fix; explicit fail-closed try/except around the B3 heartbeat probe (currently relies on broad per-parent `except Exception`); PACKS.md stale `wc_wake_flag_resolver_tools_unit_test.sh` row (target deleted by B1).

**Merge recommendation: HOLD pending B4 resolution; every other gate is green.**
d evidence.

**[FINAL SECTION PENDING: C12a/b/c raw lines + final default-suite totals + D2 attribution — workers in flight]**


---

# R2 RE-VERIFICATION — developer's response to HOLD (new HEAD 01cf157e + verification commit 159ce3a2)

**Scope:** focused re-verification of the 4 R2 commits answering the round-1 HOLD — `f115daf7` (B4 obligation re-mint inline in the idempotency_skip branch), `1e9ceb2f` (D2 `extra=` logger fix), `63cde877` (B3 explicit fail-closed heartbeat probe), `01cf157e` (PACKS stale-row deprecation). Verification arc added `159ce3a2` (re-mint concurrent exactly-once probe). Baseline for comparison: round-1 final state `35f72930`.

## 1. B4 acceptance (the round-1 blocker) — ✅ RESOLVED / VERIFIED

- **Delta audit (scope-clean):** the 4 commits touch exactly 6 files, nothing off-scope. The re-mint replaced the bare `return` IN PLACE (child_reports.py:3843-3882): three-condition gate — parent alive (:3844) ∧ instance COMPLETED via `asyncio.to_thread` fetch with fail-safe skip (:3846-3853) ∧ PENDING watcher for the pair (via `fetch_pending_for_target_and_child`) → `_emit_terminal_for_child_instance_via_bus(..., summary="idempotency_skip obligation re-mint (B4 cycle-2 wedge fix; 84563a03)")`. Exactly-once = `transition_state` guarded UPDATE (`WHERE state='PENDING'`, rowcount-gated, dependency_bus/repository.py:681, :728-757) + per-task `asyncio.Lock` (dependency_bus.py:400/:1602, acquired :897-901) + `mark_enqueued` dedup belt (:932-941).
- **The flip is honest:** `xfail` grep-removed; ZERO assertion lines deleted from the file; the flipped node is a real end-to-end pin (real PENDING `DependencyWatcher` row → real `DependencyBus` on file-backed SQLite → real `_dispatch_post_commit_side_effects(outcome="idempotency_skip")` → asserts PENDING→FIRED).
- **Acceptance runs:** vgap_b4 `16 passed` (15 after flip + probe), dev b4 `9 passed`, `child_reports_unit` pack `48 passed`, `wc_wake_d1_w5_pairing` pack `59 passed` (ledger 57 + 2).
- **Independent probe (mine, commit 159ce3a2):** `TestReMintConcurrentDoubleFireSingleEmit` — two overlapping `asyncio.gather` dispatches on the idempotency_skip shape → **exactly ONE FIRED row** (guarded UPDATE + per-task locks exercised end-to-end). 16/16.
- **Non-force-emit coverage:** 7 legit-defer outcomes parameterized (each seeds a watcher, asserts stays PENDING; `child_still_running_defer` excluded as EMITTING category — deliberate, documented) + the 3 new legit-skip shapes (`TestLegitimateIdempotencySkipDefer` ×3: no-parent / not-COMPLETED / no-PENDING-watcher) + backstop's own skip conditions ×2. COVERED.
- **The 84563a03 recurrence is now healed:** idempotency_skip + COMPLETED child + live parent + unmet obligation → emit fires, exactly once.

## 2. Delta polish — all three confirmed

- **D2 FIXED:** `task/repository.py:3127-3132` now `logger.info("task.reconciled_to_cancelled", extra={"work_id":..., "count":...})` (single hunk, valid stdlib, matches the 34-sibling `extra=` convention). Proof under handler: `test_task_reconciliation.py --log-cli-level=INFO` → `13 passed` (round-1 failure mode was handler-conditional TypeError). Partition proof: C7 = `52 failed, 986 passed` vs baseline `54/984` — the −2F/+2P delta is EXACTLY the two recovered nodes.
- **B3 explicit fail-closed:** `waiting_children_watchdog.py:877-890` — `try: return bool(method(...)) except Exception: logger.warning(...failing-CLOSED...) return True` (assume-alive → release ∧ escalation withheld). Broad per-parent catch retained as second backstop (line drifted :1267→:1297). Narrowed semantics pinned: `test_vgap_b3_dberror_failclosed.py` 3/3 (`release_notices_enqueued == 0 AND escalation_notices_enqueued == 0`). Note: dev retargeted the old `enqueue_message.await_count==0` assertions to the narrowed counters — intentional, documented in commit, not a weakening of the fail-closed contract (the informational base hang-notice may now fire).
- **PACKS:** `wc_wake_flag_resolver_tools_unit_test` row marked DEPRECATED (2026-09-11, cycle-2) with named successor coverage.

## 3. Full-suite gate @ 01cf157e/159ce3a2 — ✅ ZERO new branch-caused failures

All 18 default chunks re-run + PG ×3 on private disposable clusters (POSTGRES_* scrubbed, PG_TEST_* pinned, serial):

| Chunk | R2 raw line | vs round-1 (18,841P/216F+29E) |
|---|---|---|
| C1 unit_tools | `2569 passed, 5 skipped, 50 warnings in 12.92s` | parity (identical counts) |
| C2 unit_services | `7 failed, 1574 passed, 118 warnings in 13.43s` | 7F identical family; +27P = new vgap nodes |
| C3 subdirs_routers | `647 passed, 11 warnings in 12.75s` | parity |
| C4 loose_a_d | `10 failed, 1362 passed, 2 skipped, 5 warnings, 21 errors in 16.85s` | parity (node-set diff EMPTY) |
| C5 loose_e_l | `19 failed, 1168 passed, 16 warnings in 43.57s` | parity (13 find_near + 6, identical) |
| C6 loose_m_r | `10 failed, 2005 passed, 40 skipped, 90 warnings in 64.86s` | parity (identical 10) |
| C7 loose_s_z | `52 failed, 986 passed, 11 skipped, 28 warnings, 2 errors in 17.42s` | −2F/+2P = D2 recovered; rest identical |
| C8 top_a_h | run1 `20 failed, 1033 passed, 52 skipped, 31 warnings, 2 errors in 99.18s`; run2 `20 failed, 1031 passed, 54 skipped, 33 warnings, 2 errors in 79.92s` | inside documented 20↔21 band; set deltas = the two documented flaky families only |
| C9 top_i_q | `60 failed, 2386 passed, 73 skipped, 43 warnings in 22.03s` | 58 baseline-identical + 2 suspects → **both CONTEXT-FLAKE** (see below) |
| C10 top_r_z | `15 failed, 2257 passed, 34 skipped, 5 xfailed, 1139 warnings in 20.96s` | 13 baseline + 2 context-flakes (atomic-family toggle + worker_notification — see below) |
| C11 job_queue | `7 failed, 1723 passed, 38 skipped, 1528 warnings in 29.50s` | parity (identical 7) |
| C12a integ a–m | `6 failed, 356 passed, 1 skipped, 13 warnings in 13.18s` | parity (identical 6) |
| C12b integ n–z | `6 failed, 156 passed, 124 warnings, 2 errors in 10.32s` | same families (SSL-spill variance 6-vs-8 hits, item-set unchanged; `wc_wake_pure_hang` trip moved lanes within its ×3 quarantine row) |
| C12c opencode+e2e | `4 failed, 516 passed, 1 skipped in 28.26s` | parity (identical 4) |
| C13 ckpt+persistence | `72 passed in 4.41s` | parity |
| C14 daemon/tests | `46 passed in 2.93s` | parity |
| C15 test/ probes | `13 passed, 55 warnings in 14.12s` | parity |
| **PG A/B/C** | `41 passed in 3.57s` / `4 failed, 165 passed, 33 skipped, 2 xfailed in 18.28s` / `1 failed, 52 passed, 20 deselected in 5.31s` | **identical tallies** (list_queues pair isolation-PASS 5/5 reconfirmed; 3 base-proven pre-existing) |

**R2 default-suite total: 18,869 passed / 216 failed + 25 errors / ~257 skipped.** Deltas vs round-1 fully accounted: +27P/+2P new verification tests, +2P D2-recovered, −2E/+2P SSL-spill variance, −3P/+3F the three flake suspects below.

**Flake adjudications (retry budgets, all serial):**
1. `test_worker_notification::test_multi_worker_notification` (C10) — 5× isolated + 2× file-context **7/7 PASS** → CONTEXT-FLAKE (load-sensitive claim race; worker-pool claim paths untouched by branch).
2. `test_skill_evolution_service::TestCheckABTestResolution::test_ab_resolution_force_resolve` (C9) — 3× isolated + file-context (62/62) **all PASS** → CONTEXT-FLAKE (`transaction-in-transaction` only under 12-worker partition load; skill-evolution service untouched).
3. `test_memory_integration::TestFullLifecycleIntegration::test_concurrent_writes_no_corruption` (C9) — 3× isolated PASS, suspect passes in file-context → CONTEXT-FLAKE (the 10 file-context failures observed alongside are the documented inner_soul baseline family, part of the 58).

No quarantine rows warranted (context-flakes are not stable runner signals; noted for partition-tuning awareness instead).

## 4. Nits carried forward (non-blocking)

- B4 shape-3 legit-skip (COMPLETED, no PENDING watcher) emits a WARNING at child_reports.py:3864-3870 saying "obligation honored via corrective multi-turn emit" even when `matched_rows=0` → nothing fired. Observability overclaim only; unpinned by tests. Suggest gating the log on `fired > 0`.
- Stale line-cites in docstrings after the inserts (":1267" is now :1297; vgap docstrings cite pre-edit line numbers) — cosmetic.
- C6 cosmetic: pack script header echoes branch from invocation cwd before `cd $PROJECT_DIR` — pytest itself correctly anchors to the worktree (verified via venv-path counts).

## 5. R2 VERDICT

**✅ PASS FOR MERGE.** The sole round-1 blocker (B4) is resolved and independently verified end-to-end (flip + re-mint probe + exactly-once + skip-shape coverage). D2 and B3 polish confirmed fixed with narrowed-semantics pins. Full default suite + PG: **zero new branch-caused failures** — every R2 failure is baseline-identical, quarantine-family, or adjudicated context-flake. FE compatible (unchanged from round 1 — no FE files in R2 delta). ensure.md Core remains 3/3 (concurrency pack re-run not required for this focused scope; nothing in the R2 delta touches those gates' surfaces — logger kwargs, watchdog probe, child_reports re-mint are all outside the concurrency/thread-identity assertions; if desired pre-merge, a 7-second re-run of `concurrency_atomic_unit_test.sh` is the cheap confirmation).

**Merge recommendation: GO.**


---

# R3 DELTA-CONFIRM — final scoped check @ e18f8c46 (dc73fb6f + 79340190 + e18f8c46)

**Scope:** 2 polish commits, 4 files, +354/−31 (claim verified exact). Dev delta: W1 conditional heal-WARNING + N1 debug logs + docs; test class `TestReMintConditionalHealWarning` + N4 PACKS reword; disclosed fixture enhancement to `_seed_parent_watcher`.

**Deviation (benign, flagged):** `daemon/services/waiting_children_watchdog.py` appears beyond the stated allow-list — entire 5-line diff is **docstring-only** (stale `:1267` line-anchor → text-search anchor). Zero executable change.

## 1. Independent fixture check (the one real risk) — ✅ CLEAN
- **Additive-only:** file diff has **zero removed lines**; 4 hunks (logging import, docstring, 3 payload keys added to `_seed_parent_watcher`, EOF append of new class + log filter).
- **KeyError claim substantiated at contract level:** `FollowUp.from_payload` (dependency_bus.py:227-246) hard-subscripts `target_instance_id` and `message`; the old payload had neither. `emit_terminal_for_child_instance` runs `transition_state` (:904) BEFORE `from_payload` (:921) → old fixture reliably raised KeyError AFTER the durable commit; child_reports' defensive `except` (:3940) swallowed it and FollowUp returns were discarded. Claim fully confirmed.
- **Assertion-neutral:** `git diff | grep '^-' | grep -i assert` → empty; all 16 pre-existing test bodies byte-identical.
- **Semantics: strengthens, never flips.** No pre-existing test was vacuous (all assert real DB state), but emit-reaching tests previously passed over the swallowed-exception path — they could not distinguish "healed + clean return" from "healed DB + raised exception". The repair is also a hard prerequisite for the new positive W1 test (old payload → helper raises → WARNING never fires → `len==1` assertion would fail).

## 2. W1/N1 confirmed (child_reports.py)
- **W1:** heal-WARNING now conditional — `fired_followups = (await …)` :3907, gate `if fired_followups:` :3924, message appends `"(N watcher(s) FIRED)"` :3931. Closes the round-2 matched=0 overclaim nit. Emit call args byte-identical (capture + gate only).
- **N1:** two `except Exception as fetch_exc:` + `logger.debug` additions (:3881 re-mint, :4349 backstop) — log-only, `inst = None` unchanged.

## 3. Nodes + families — ✅ all exact-match (266 passed / 0 failures)
- vgap_b4 file `18 passed` (16 + 2 new; collect = 18) — **new class both directions PASS** (`test_heal_warning_fires_on_non_empty_return`, `test_heal_warning_silent_on_empty_return`; filter precisely excludes the failure-path text), **flipped pin PASS**, **my double-fire probe PASS**.
- dev b4 `9` · watchdog family `51` · census/per-fix `81` · `child_reports_unit` pack `48` · `wc_wake_d1_w5_pairing` pack `59`. Dev's claimed 116 is a consistent subset.

## 4. Bounded suite spot — ✅ 0 NEW
- `regression_unit_services`: `7 failed, 1577 passed, 122 warnings in 13.87s` (7 = quarantined proxy_phase1; +3P vs R2 = 2 new class nodes + 1 pass-side noise).
- `regression_job_queue`: `7 failed, 1723 passed, 38 skipped, 1528 warnings in 23.43s` (identical to R2 baseline).
- `tests/test_dependency_bus.py`: `1 failed, 74 passed, 7 warnings in 4.22s` — the exact quarantined `TestGenerationCounterBump` lock-flake node, pre-flagged.
- **PG skipped — justified:** delta touches only child_reports.py (+175), watchdog docstring (5), and the test file; zero PG-marked surfaces (grep clean).

## R3 VERDICT

**✅ GO — delta confirmed.** Fixture enhancement verified genuine/neutral at contract level, W1 both-directions proven, all named nodes green, families exact-match, bounded spot baseline-clean. Cosmetic nits only (docstring line-anchor swaps; one narrow comment cite ~:866-921). **The PASS FOR MERGE verdict stands at e18f8c46.**
