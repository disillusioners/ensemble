# Independent Verification — `job_answer` tool, `feature/job-answer-tool` @ `fae93a9f`

**Date:** 2026-09-22 · **Gate:** final pre-merge verification · **Mandate:** verify and report only — zero modifications (honored: no edits, no commits, no fixes by any worker)

## VERDICT: **SHIP** ✅

All six mandated verification items PASS. The developer's claims reproduce exactly; the "13 pre-existing failures" claim is rigorously proven by full-directory A/B base-vs-branch diff (13 ≡ 13, node-for-node, zero branch-caused failures).

- **Base:** `0062e6fc` (full `0062e6fc3aa4425cc72364bceb5ba1066e27a29a29b`) · **Branch HEAD:** `fae93a9f` (full `fae93a9feff687e1ce43f92904475cdd09167815`) · 6 commits · diff-stat = exactly the 5 claimed files, nothing under `daemon/routers/`, `daemon/services/`, `daemon/models/`.
- **Worker instances:** 8d6a1e78 (state), 856c84e0 (scenario), 6f9a34cc (count-pin), 03cb0405 (answer-route), bb84d3e3 (smoke), f61379e5 + 7638f99b (base sweep), 6791da0c + c4726530 (branch sweep).

## 1. State check — PASS
- Branch `feature/job-answer-tool` ✓ · HEAD `fae93a9f` ✓ · diff-stat exactly 5 files ✓ (job_queue.py +484, _tool_registry.py +1, test_job_answer_tool.py +899 new, jober_watch_integration.py ±4, test_job_queue_tools.py ±4) · 6 commits present in order ✓.
- ⚠️ Disclosures (non-blocking):
  - ` M .agents/tidier/notes.md` — foreign in-flight edit (shared-worktree hazard (p)); outside the change set; never touched by any worker; main checkout byte-stable across all 9 workers' pre/post checks.
  - 3rd commit SHA is `b8fdec3a`, leader brief said `b8ddec3a` — commit message matches exactly; d/f transposition in the brief. Anchors (base, tip) both verified.

## 2. Mandated scenarios — PASS (17/17 in 1.23s)
Command: `timeout 300 uv run python -m pytest tests/job_queue/test_job_answer_tool.py --override-ini='addopts=' -q --tb=short` → 17 passed / 0 failed / 0 errors / 0 skipped.

| Scenario | Covering tests | Evidence |
|---|---|---|
| (a) happy path + resume info | `TestJobAnswerHappyPath::test_pending_pack_answered_returns_envelope_and_resume_info` (+ `test_second_answer_is_already_delivered`) | asserts `resume_route == "answer_gate_existing_turn"` (:331), `resume_instance_cascade.assert_awaited_once_with(asker)` (:335); CAS-lover sibling pins `resume_route == "already_delivered"` + `await_count == 1` |
| (b) stale/mismatched pack_id | `TestJobAnswerStalePackId::test_stale_pack_id_returns_mismatch_error` | `QUESTION_PACK_MISMATCH` (:409), "400" (:410), pack stays pending (:417), cascade not awaited (:420) |
| (c) wrong state / no pending pack | `TestJobAnswerNoPendingPack::test_no_pending_pack_returns_no_pending_question_error` (410 LOST) + `::test_durable_shadow_non_pending_returns_no_pending_question` (404) | `QUESTION_PACK_LOST` (:481) / `NO_PENDING_QUESTION` (:524) + "job_continue" hint pinned both |
| (d) access-control mismatch | `TestJobAnswerAccessDenied::test_project_mismatch_returns_access_denied` (+ same-project + system-default siblings) | exact-dict `{"error": "Access denied: job does not belong to caller's project"}` (:572-574) matching `_check_job_access` verbatim; pack not stamped, cascade not awaited |

**Three new tests — all REAL (non-tautological, mangle-matrix verified):**
1. `TestJobAnswerLiveHubNone::test_live_hub_none_happy_path_completes` — deleting the `live_hub is not None` guard would AttributeError → outer backstop error → `"error" not in result` assert goes RED. REAL.
2. `TestFormatAnswerHttpError503RaceWindow::test_race_window_503_string_branch` — calls the real unmocked `_format_answer_http_error`; pins `"503"`, `"migration"`, negative-pin `"WRITE_PAUSED" not in err`, echo pins. REAL (scope caveat below).
3. `TestJobAnswerRegistration::test_create_job_tools_returns_job_answer` — order-pin on runtime production return list (`last.name == "job_answer"` + witnesses at indices 16/17/20). REAL; other lookups correctly switched to name-based.

Mock seams: real repos/QuestionManager/WorkResolver/access-check/helper run unmocked end-to-end; AsyncMock only at manager-facade seams; awaited call-shapes match production (`resume_instance_cascade(instance_id)` single positional; mock return dicts shape-compatible). No kwarg rot, no plain-Mock-on-await.

## 3. Count-pin suites — PASS (expected shape)
`timeout 300 uv run python -m pytest tests/test_job_queue_tools.py tests/job_queue/test_jober_watch_integration.py --override-ini='addopts=' -q --tb=short`
→ **121 passed / 1 failed / 1 skipped, 6.27s.** The single failure IS the quarantined `test_add_watch_creates_record` (:935, `'settled' != 'failed'`). `failures ⊆ {quarantine}` = TRUE. No HOLD signal.

## 4. "13 pre-existing failures" — PROVEN (A/B full-directory diff)
Method: identical 6-chunk partition definitions (C1–C6, 92 branch files / 91 base files) run at base (2 disposable detached worktrees `/tmp/ens-basejob-a.wwEES1`, `/tmp/ens-basejob-b.WLSFj2`, `.env` parity copied, ambient `POSTGRES_*`/`ENSEMBLE_JOB_SYSTEM_JOB_RETRY_SCHEDULER_ENABLED=false` disclosed, both worktrees removed + verified) and at HEAD (main worktree, read-only). Every chunk `timeout 300`-wrapped; no chunk timed out (longest 25s). C3 asymmetry pre-declared: `test_job_answer_tool.py` exists only on branch (base C3 ran without it).

**Diff table — branch-vs-base failing-node sets, 13 ≡ 13:**

| # | Failing node (tests/job_queue/) | Base | Branch | Family |
|---|---|---|---|---|
| 1 | test_jober_watch_integration.py::TestJobWatcherRepository::test_add_watch_creates_record | F | F | settled-vocab rot (standing ledger row 2026-09-03) |
| 2 | test_watcher_repository_concurrent.py::TestSequentialAddWatch::test_first_call_inserts | F | F | settled-vocab rot (standing ledger row) |
| 3 | test_watcher_repository_concurrent.py::TestConcurrentAddWatch::test_concurrent_threads_default_events_single_row | F | F | settled-vocab rot (standing ledger row) |
| 4 | test_in_progress_guard.py::TestJobFeedbackObserverWaitingForGuard::test_completed_with_no_waiting_runs_normal_path | F | F | observer-guard stale fixture (standing ledger row) |
| 5 | test_in_progress_guard.py::TestJobFeedbackObserverWaitingForGuard::test_waiting_for_none_treated_as_zero | F | F | observer-guard stale fixture (standing ledger row) |
| 6 | test_job_feedback_observer.py::TestObserverSkipsTerminated::test_observer_skips_terminated_status | F | F | observer-guard stale fixture (standing ledger row) |
| 7 | test_phase2_feedback_verify.py::TestObserverObserverBehavior::test_observer_completion_then_termination_skips_termination | F | F | observer-guard stale fixture (standing ledger row) |
| 8 | test_event_driven_completion.py::TestSite1InlineMirrorFinalize::test_hook_fires_settled_and_dual_fire_is_deduped | F | F | MagicMock-not-JSON-serializable |
| 9 | test_event_driven_completion.py::TestSite1InlineMirrorFinalize::test_pre_terminal_task_completes_on_success_fully | F | F | MagicMock-not-JSON-serializable |
| 10 | test_event_driven_completion.py::TestSite1InlineMirrorFinalize::test_hook_skips_when_finalize_races_to_none | F | F | MagicMock-not-JSON-serializable |
| 11 | test_terminal_write_census.py::TestTerminalWriteCensus::test_every_terminal_write_site_is_classified | F | F | census: unlisted site daemon/manager.py:5047 |
| 12 | test_terminal_write_census.py::TestTerminalWriteCensus::test_hooked_entries_point_at_live_notify_calls | F | F | census: stale hooked_at fixture lines |
| 13 | test_round2_council_fixes.py::TestStaleTaskRecoveryWedgeHook::test_wedge_resolver_end_to_end_for_dead_letter_root | F | F | RuntimeError: no current event loop |

**Branch-only failures: 0. Base-only failures: 0. Sets identical.**

Pass-count parity per chunk: C1 240P/0F/2S ≡ · C2 268P/5F/13S ≡ · C3 base 330P/1F/6S → branch **347P/1F/6S** (+17 = exactly the new job_answer tests, all passing) · C4 326P/1F/0S ≡ · C5 340P/2F/0S ≡ · C6 315P/4F/19S ≡. Totals: base 1819P/13F/40S → branch 1836P/13F/40S. Skips identical shape (17 task_lock_manager obsolete-methods + 2 SQLite-concurrency + 21 C1-C3 pre-existing PG-parity/obsolete).

Adjudication note: one sweep worker editorialized the `'settled'` failures as "likely the dominant regression signature" — overruled by the A/B evidence: all 3 reproduce verbatim at base and are the pre-existing quarantined vocab-rot family.

## 5. Answer-route regression — PASS (exact match)
`timeout 300 uv run python -m pytest tests/ -k 'answer' --override-ini='addopts=' -q --tb=line --ignore=tests/packs --ignore=tests/e2e`
→ **150 passed / 1 skipped / 0 failed, 8.06s, 22,627 deselected** — zero delta vs the developer's 150P/1S claim. Exclusions: `tests/packs` (pre-existing collection bug, sys.exit(0)) and `tests/e2e` (needs live daemon) — pre-authorized by the leader. No `tests/postgres/*` node matched `-k answer`, so the PG contingency never fired.

## 6. Tool-surface smoke — PASS
Probe replicating the exact test-factory path (`create_job_tools(job_service, queue_mgmt_service, dead_letter_service)` from tests/test_job_queue_tools.py:38):
- **22 tools** exactly (12 job_* + 3 queue_* + 2 dlq_* + 3 watch* + list_watched_jobs + job_answer); `job_answer` present.
- Args schema (`JobAnswerInput`, daemon/tools/job_queue.py:2808-2843, wired at :2846): `work_id` str, `answers` dict[str,str], `question_pack_id` str — all three `required`.
- Registry: +1 line `job_answer` in `KNOWN_TOOL_NAMES` (alphabetical, between inner_soul and job_cancel); category `job` via `@register_tool_category("job")` (:2845); renders under `## Job Queue` between `dlq_replay` and `job_cancel` via the same `tool_help(category=...)` path the daemon exposes.

## Test-quality concerns (report-only; none block)
- 🟢 **True 503 race-window never exercised end-to-end** — covered as shaper-unit + pre-check-unit separately (flip `is_write_paused` post-pre-check would close it); documented as intentional scope in the test docstring. Low risk: guard duplicated on both surfaces.
- 🟢 `tests/test_job_queue_tools.py` count-pin (22) alone wouldn't catch a stub swap of job_answer — mitigated by the behavioral tests in test_job_answer_tool.py (real args through the real tool) and the order-pin test.
- 🟢 Daemon-vs-test construction seam: daemon calls `create_job_tools` with all 8 kwargs via `create_job_tools_if_available` (instance.py:1747/1770); tests pass 3 positional. Facade-forwarding discipline applies if kwargs are ever added.
- 🟢 Cosmetic: single blank line before `TestJobAnswerLiveHubNone` (:797-798); garbled docstring clause in that class.

## Scope & exclusions
- `tests/packs` (collection bug sys.exit(0)) and `tests/e2e` (live daemon) excluded throughout — leader-authorized.
- PG: job_queue sweep runs on SQLite-backed unit seams (house pattern); ambient POSTGRES_* env disclosed and identical on both A/B legs (`.env` copied to base worktrees). No fresh full-SQLite daemon-boot paths used (PG-only migration trap avoided).
- ensure.md Core: "no regressions in changed packs" = the substance of this gate → PASS (all changed-pack tests green except the 13 A/B-proven pre-existing). Remaining Core items (concurrency pack, dev.sh flag, async-await greps) target modules untouched by this change set (daemon/services, daemon/routers absent from diff-stat) → out of blast radius.

## Quarantine ledger
Consolidated gate row appended to QUARANTINE.md (13 nodes; 7 = standing mission settled-rename family re-proven at this base; 6 newly-documented: 3 event_driven MagicMock-JSON + 2 census + 1 round2 event-loop). Rising pre-existing debt in tests/job_queue/ is a quality signal for the standing migration follow-up — not attributable to this branch.

## Evidence artifacts
- Branch sweep C4-C6 logs: `/tmp/branch_sweep_c4c5c6/chunk_C{4,5,6}.log`
- Base worktrees removed and verified gone; main checkout unchanged (only the foreign tidier line).

**Overall: Unit/scenario ✅ · Count-pin ✅ (quarantine-shaped) · A/B pre-existing proof ✅ (13≡13) · Answer-route ✅ · Tool surface ✅ → SHIP.**
