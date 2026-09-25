# TESTER GATE — A/B Verification: fix/mission-terminal-watch-report-publish (C1+C2+C3)

Date: 2026-09-25 (gate window 19:58–21:00 UTC)
Branch: `fix/mission-terminal-watch-report-publish` @ `fe9692ad` (local-only; not pushed/merged by tester)
Base: `db71500a` (v0.14.2 tip)
Commission commits: `6e4e6171` (C1+C2+C3) → `fe71e450` (fixback 1) → `fe9692ad` (fixback 2)
Workdir: /home/nea/ensemble-src
Worker instances: base-leg `79294c14`, feature-leg `e4520bcd`, spotchecks `82cb659f`, flake/parity `27c506eb`, boot `0c7a98a0`, concurrency pack `1d6fa980`
Evidence logs: `/tmp/abgate-base-db71500a-run.log`, `/tmp/abgate-feature-fe9692ad-run.log`, `/tmp/abgate-wave2-logs/` (25 matrix logs + parity + collect), `/tmp/abgate-boot.log`, `/tmp/abgate-concurrency-pack.log`

## VERDICT: ✅ PASS — CLEARED FOR MERGE (giter --no-ff merge + push)

Zero feature-only (NEW-at-feature) failures across the full `tests/job_queue/` A/B. Zero flakes in 25 serial pin runs. Boot gate PASS with live-DB scrub proven necessary and effective. Zero NEW collection errors. All 8 semantic spot-checks PASS. ensure.md Core items ALL PASS (incl. concurrency pack, baseline-exact).

---

## 1. A/B vs base — FULL tests/job_queue/ (leader-mandated; full-dir-per-merge lesson)

Invocation (identical shape both legs, POSTGRES_*/SSL_CERT_* scrubbed and verified empty, `-q --tb=short -p no:cacheprovider`, `timeout 1500` command-level cap — documented deviation from the 5-min pack cap, mandated by the full-dir gate and applied identically to both legs; pytest-timeout absent from venv, noted):

- **Base leg:** detached worktree `/tmp/abgate-base-db71500a` @ `db71500a`, `PYTHONPATH`-pinned, `daemon.__file__` verified resolving INSIDE the worktree (editable-install trap avoided). 1934 collected → **19F / 1874P / 0E / 38S / 3deselect**, 251.59s.
- **Feature leg:** main checkout @ `fe9692ad` on the feature branch (HEAD verified, untouched). 1941 collected → **16F / 1884P / 0E / 38S / 3deselect**, 271.95s. (+7 collected = new commission pin file.)

### Failure adjudication table

| # | Test (file:line) | Base | Feature | Verdict |
|---|---|---|---|---|
| 1 | TestSite1InlineMirrorFinalize::test_hook_fires_settled_and_dual_fire_is_deduped (test_event_driven_completion.py:287) | RED | RED | **Pre-existing** — TypeError MagicMock JSON @ task/repository.py:2602 (documented RED-since-v0.13.10 family) |
| 2 | …::test_pre_terminal_task_completes_on_success_fully (:346) | RED | RED | Pre-existing (same family) |
| 3 | …::test_hook_skips_when_finalize_races_to_none (:395) | RED | RED | Pre-existing (same family) |
| 4 | TestJobFeedbackObserverWaitingForGuard::test_completed_with_no_waiting_runs_normal_path (test_in_progress_guard.py:428) | RED | RED | Pre-existing — unconfigured MagicMock `per_kind_status_for` assert |
| 5 | …::test_waiting_for_none_treated_as_zero (:453) | RED | RED | Pre-existing (same family) |
| 6 | TestJobAnswerRegistration::test_create_job_tools_returns_job_answer (test_job_answer_tool.py:676) | RED | RED | Pre-existing — 'job_resume' vs 'job_answer' registration-order drift |
| 7 | TestObserverSkipsTerminated::test_observer_skips_terminated_status (test_job_feedback_observer.py:328) | RED | RED | Pre-existing — observer consults JQS on TERMINATED |
| 8 | TestJoberWatchIntegration::test_tool_registration (test_jober_watch_integration.py:686) | RED | RED | Pre-existing — tool surface 24≠22 AT BASE (predates commission; consistent with job_pause/job_resume landing on latest) |
| 9 | TestJoberWatchIntegration::test_ensure_dev_sh_still_works (:878) | RED | RED | Pre-existing env-defect — hardcoded macOS path `/Users/nguyenminhkha/…` (b1_wc hardcoded-cwd family) |
| 10 | TestJobWatcherRepository::test_add_watch_creates_record (:944) | RED | RED | Pre-existing — default watch_events drift; `'settled'` present AT BASE (10-item actual vs pinned canonical 5-token) → vocabulary change predates db71500a |
| 11 | TestObserverObserverBehavior::test_observer_completion_then_termination_skips_termination (test_phase2_feedback_verify.py:497) | RED | RED | Pre-existing — same terminated-skip family as #7 |
| 12 | TestStaleTaskRecoveryWedgeHook::test_wedge_resolver_end_to_end_for_dead_letter_root (test_round2_council_fixes.py:935) | RED | RED | Pre-existing — deprecated `asyncio.get_event_loop()` RuntimeError |
| 13 | TestTerminalWriteCensus::test_every_terminal_write_site_is_classified (test_terminal_write_census.py:611) | RED | RED | Pre-existing — unlisted site `daemon/manager.py:5083` at BOTH legs |
| 14 | …::test_hooked_entries_point_at_live_notify_calls (:672) | RED | RED | Pre-existing — same 14 drifted hooked_at anchors at BOTH legs (task_processor.py:1087 ×2, child_reports.py:3941 ×4, job_queue_service ×4 sites, job_recovery_service ×3) — census fixture drift predates commission |
| 15 | TestSequentialAddWatch::test_first_call_inserts (test_watcher_repository_concurrent.py:158) | RED | RED | Pre-existing — watch_events drift (same family as #10) |
| 16 | TestConcurrentAddWatch::test_concurrent_threads_default_events_single_row (:315) | RED | RED | Pre-existing — watch_events drift (same family as #10) |
| 17–19 | TestRecoveryServiceBothStateParity ×3 (test_f1_killswitch_tz_matrix.py:709/724/740) | RED | **GREEN** | **Base-leg environment artifact — DISPOSED**: `FileNotFoundError: '.venv/bin/pytest'` (worktree has no local venv; tests shell out to a relative path). Parity re-run at base with `.venv` symlink → **29 passed incl. all 3**. Not code-red; not a feature fix. Symlink removed post-run; worktree pristine. (See LESSONS/2026-09-25-worktree-ab-venv-parity.md) |

**Feature-only (NEW-at-feature) failures: 0 → no blockers.**
**Base-only failures: 3 → all disposed as gate-protocol environment artifacts (§ row 17–19).**
**Both-red: 16 → pre-existing at base, independently confirmed (not trusted from dev claim).**

### Claimed base-red families — independent confirmation

| Claim | Result |
|---|---|
| 3× TestSite1InlineMirrorFinalize | ✅ CONFIRMED — exactly 3, same error both legs |
| 5× TestAccessMemoryArchive (quarantined) | ✅ Out-of-dir-scope as expected (lives tests/unit/tools/; absent both legs — dir run cannot see it) |
| TestJoberWatchIntegration ×3 (ALL_WATCHABLE_EVENTS list-order drift) | ✅ CONFIRMED red at base, with refinement: only 1 of the 3 is the events-drift proper (#10); #8 is tool-count drift, #9 is the macOS-path env defect. The events-drift family spans 3 tests across 2 files (#10, #15, #16) — `'settled'` already in the default list AT BASE |
| "+ up to 8 more claimed base-red families" | ✅ CONFIRMED — 6 additional both-red families beyond the named ones (#4/5, #6, #7+#11, #12, #13/14) + the killswitch env-artifact trio (#17–19). Every feature-leg failure maps 1:1 onto a base-leg failure with matching error family |

## 2. dev.sh boot gate (execution-lane rule) — **PASS**

- ensure.md in-scope items executed (Core static + boot probe); release-gate E2E (real LLM) intentionally out of scope for a fix-branch gate.
- Static: `dev.sh:102` carries `--timeout-graceful-shutdown 10` ✅ (Core-Critical requirement).
- Boot: `./dev.sh` → `http://localhost:8079/api/health` **HTTP 200 at t=6s** — `{"status":"healthy","uptime_seconds":1.329,"version":"0.14.2","current_database":"postgres",…}`; log shows `localhost:5432/ensemble_dev` (LOCAL DEV).
- **Scrub proven necessary:** pre-scrub env held `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `POSTGRES_USER=ensemble`, `POSTGRES_PORT=5432`, `POSTGRES_PASSWORD=***` — exactly the LIVE-DB incident signature from 2026-09-21. Post-scrub `printenv | grep POSTGRES_` empty; daemon resolved to `ensemble_dev`. Backlog-clear ran against the dev DB and found 0/0 (safe).
- Port safety: 8088 never bound/touched; 8079 owned exclusively by the gate's own dev.sh tree (PID descent verified); all 7 tree PIDs exited at t=2s on TERM (no KILL escalation); 8079 free after. Live (9797) / demo (7979) daemons untouched.
- Graceful-shutdown sequence verified end-to-end (`WorkerPool stopped` → … → `Graceful shutdown complete`).
- Non-blocking: 2× `plane` MCP session errors at warmup — expected dev-env behavior (PLANE_* unset), non-fatal.
- Base worktree removed cleanly post-gate; `git worktree list` verified; main checkout unchanged (only pre-existing ` M .agents/tidier/notes.md`).

## 3. Flakiness / race check — **25/25 GREEN, zero flakes**

5 pin files × 5 consecutive serial runs at `fe9692ad` (each `timeout 300`, scrubbed, no concurrent load):

| Pin file | run1–run5 | Verdict |
|---|---|---|
| test_mission_terminal_commission_pins.py (6 tests) | 0/0/0/0/0 exits, 6P each | 5× GREEN |
| test_work_notifier_defect1_pins.py (8) | 0×5, 8P each | 5× GREEN |
| test_work_notifier_defect1_round3_pin.py (4) | 0×5, 4P each | 5× GREEN |
| test_work_notifier_defect5_pins.py (6) | 0×5, 6P each | 5× GREEN |
| test_work_notifier_n1_pin.py (7) | 0×5, 7P each | 5× GREEN |

The ~60ms Task.result commit-visibility race class did not manifest in any of 25 serial runs (per-run 0.49–4.34s). M3 pins = defect1_pins + defect1_round3_pin (worker-C diff classification: 1 NEW file + 4 MODIFIED; the two defect1 files are the modified-in-place M3 pins).

## 4. ensure.md validation (scoped to this change set)

| Requirement | Status | Evidence |
|---|---|---|
| Core: no regressions in changed scope | ✅ PASS | Full-dir A/B §1 — 0 feature-only failures (method note: full-dir-per-merge lesson governs the job_queue lane; leader-mandated) |
| Core: deadlock/concurrency integrity (concurrency_atomic_unit_test; observer race coverage) | ✅ PASS | 98P/0F/74S in 58.59s @ fe9692ad — exact baseline match (2026-09-24: 98P/0F/74S/60.86s); all 13 pack files incl. observer_race1/correlation/late_msg ran; dual-layer timeout (300 outer / 280 inner, neither hit); POSTGRES-scrubbed (`env -u`, live-DB ambient signature stripped again); log /tmp/abgate-concurrency-pack.log |
| Core: sync-DB-off-event-loop | ✅ PASS | Covered by the same pack run (thread-identity tests green) |
| Core: dev.sh `--timeout-graceful-shutdown 10` | ✅ PASS | dev.sh:102 (§2) |
| Release Gate (full non-integration suite; E2E real-LLM) | — NOT RUN | Out of scope: fix-branch verification, not a release; excluded by commissioned plan |

ensure.md Improvement Notices: none — no requirement method contradicted pack/timeout rules this gate (the full-dir A/B is the leader-mandated project lesson, not an ensure.md-prescribed method).

## 5. Semantic spot-checks — **8/8 PASS** (read-only, worker `82cb659f`)

1. Change-set inventory: 4 production files (work_notifier.py +544, task_processor.py +98, child_reports.py +84, job_queue.py +172) + 5 test files — all within claimed scope; no out-of-scope file. ✅
2. M3 pins: defect1_pins + defect1_round3_pin modified-in-place, both pin settled-token Result threading. ✅
3. Timeline pin: all 4 stages genuinely pinned in test_mission_terminal_commission_pins.py — arm (:320–324) → receipt-settles-early-while-children-running (assert 0 deliveries, :346–352) → true terminal (:408–412) → exactly-one-fire (:428–433) + row CAS-claimed (:436–440). ✅
4. Docstring anchor: child_reports.py:4400–4401 cites work_notifier.py:429 → **:429 IS the actual `effective_result` assignment (work_notifier.py:429–432)**. Dev/tidier disagreement resolved in favor of :429. ✅ (Flaggable: two OTHER in-tree citations say `:306` — task_processor.py:1102–1103 and test_work_notifier_defect1b_pin.py:476–478 — stale, point into a docstring; pre-existing, predate this commission.)
5. "C1 HOLD" audit: exactly 2 hits (job_queue.py:2433, :2740), both source comments; user-facing replies use plain operator language ("immediate fire was held (0 watchers notified now…)"). ✅
6. Repo collection: **zero NEW errors** — but note completion required digging past TWO pre-existing import-time `sys.exit` pack files (details §6). ✅
7. C1 canonical guard: boot sweep (job_queue_service.py:603) and observer notify (work_notifier.py:496) both route through canonical `evaluate_mission_live`; missing `_instance_repository` seam is fail-CLOSED — HOLD + error log at work_notifier.py:515–547 (`mission_live = True` on guard exception). ✅
8. C2/C3 anchors: both `_skip_task_as_completed` notify sites incl. carve-out thread `result_summary=message content` (task_processor.py:274–281, 337–344); `_enrich_terminal_record` gate + fallback on `{completed, settled}` (job_queue.py:2224–2227, 2250–2282). ✅ **Clarification (non-blocking):** the `{completed, settled}` narrowing is enforced at the PRODUCERS (child_reports.py:4405 + task_processor.py:1110/1224 — else-branch sends no kwarg for cancelled/dead_letter/failed), NOT in work_notifier.py; the notifier itself is permissive (`effective_result` ternary). Commission docs should describe C3's scoping as producer-side.

## 6. Collection (full repo, feature HEAD) — **zero NEW collection errors**

- Naive `pytest --collect-only` aborts: `test/packs/wc_wake_off_bytecompat_probe_test.py:79` `sys.exit(1)` at import (pre-existing; file from `fe64ea06`, ancestor of base; unchanged in commission range). Behind it, a SECOND pre-existing abort: `tests/packs/g7_unique_index_smoke_test.py:121` `sys.exit(0)` at import (tracked, byte-identical at base).
- Completed walk (both above ignored): **23,174 collected (715 deselected), 2 collection errors — both pre-existing environment artifacts, identical at base**: (1) `test/packs/vscode_e2e_browser_test.py` ModuleNotFoundError playwright; (2) `tests/e2e/test_context_injection_hybrid.py` import-time live HTTP probe to localhost:8079 (no daemon; correctly not started per safety rules).
- Known pre-existing sqlite-boot migration finding remains out of scope per commission.

## 7. Non-blocking findings (for the record / follow-up routing)

1. 🟢 Stale `:306` citations for `effective_result` (task_processor.py:1102–1103; test_work_notifier_defect1b_pin.py:476–478) — pre-existing doc rot; correct anchor is work_notifier.py:429.
2. 🟢 C3 description premise: `{completed, settled}` set lives producer-side, not in work_notifier.py (see §5.8).
3. 🟢 Collection hygiene debt: any bare repo-wide collection dies at g7 (`tests/packs/`, plural) even after ignoring the wc probe (`test/packs/`, singular); needs a paired `--ignore` or conftest guard. Behind it: 2 env errors (playwright; live-HTTP probe).
4. 🟢 16 both-red families = pre-existing test debt at base (rows 1–16). Consolidated QUARANTINE row added (see QUARANTINE.md) so future gates diff against this adjudicated set instead of re-litigating.
5. 🟢 `test_f1_killswitch_tz_matrix.py` shells out to relative `.venv/bin/pytest` → env-sensitive in worktrees (same class as the b1_wc hardcoded-cwd defect). LESSONS entry written.
6. 🟢 dev.sh `HOST` default `0.0.0.0` (dev.sh:89) while uvicorn binds 127.0.0.1 — informational only.
7. 🟢 Boot worker's own process-tree printer had a BFS-awk unbounded-loop bug → one 1800s bash timeout (the long-tool advisory); self-recovered, boot not redone, all later commands bounded. No gate impact.

## 8. Deviations & scope notes

- Full-dir `tests/job_queue/` runs used `timeout 1500` (25 min), a documented deviation from the 5-min pack cap — leader-mandated full-dir-per-merge gate; identical cap/invocation both legs; actual runtimes ~4.2–4.5 min.
- Base leg executed in a detached worktree with PYTHONPATH + `daemon.__file__` import verification (main `.venv` editable-install trap neutralized).
- Concurrency pack (§4) added beyond the leader's 5-item plan: ensure.md Core-Critical closure because the commission touches the observer/notify path ("observer race" is that pack's declared coverage).
- No quick fixes authorized anywhere in this gate (verification-only mandate); zero repo mutations by any worker (git status unchanged: only pre-existing ` M .agents/tidier/notes.md`).

## 9. Documentation updated

- [x] RESULTS/2026-09-25-mission-terminal-watch-report-publish-ab-gate.md (this file)
- [x] QUARANTINE.md — consolidated row for the 16 both-red base-pre-existing nodes (db71500a ≡ fe9692ad)
- [x] LESSONS/2026-09-25-worktree-ab-venv-parity.md — worktree legs + relative-.venv tests
- [x] PACKS.md — concurrency pack last-run update (after worker F reports)
- [ ] rules/ensure.md — untouched (user-owned)

## 10. Overall status

- A/B full-dir: ✅ 0 new-at-feature failures
- Boot gate: ✅ PASS (scrub proven necessary — ambient env pointed at live ensemble_prod)
- Flake matrix: ✅ 25/25 green
- Spot-checks: ✅ 8/8 (+2 non-blocking flaggables)
- Collection: ✅ zero NEW errors
- ensure.md Core: ✅ ALL PASS (incl. concurrency pack, baseline-exact)
- **Testing Complete: ✅ READY — cleared for giter --no-ff merge + push**
