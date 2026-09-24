# Test Report: agent-pause-resume-tools — Pre-Merge Acceptance Gate

Date: 2026-09-24T19:32Z → ~20:15Z
Branch: `feature/agent-pause-resume-tools` @ `17cf80f189ffe7b9b3d63f32bf6bd40aa8eed350` (4 commits from `latest` @ `0fd06cf0`)
Instance IDs (10 workers, 0 re-dispatches):
- pausegate-discover `7ed7ce5f` · pausegate-feat `bafc2a5f` · pausegate-mockfid `93390808` · pausegate-boot `58df5494` · pausegate-conc `2449e1ac` · pausegate-regsurf `c8dfb258` · pausegate-t1 `8333feb2` · pausegate-t2 `3df10fce` · pausegate-router `7ce3d90b` · pausegate-jqtools `b42ebe7d`

## VERDICT: ✅ MERGE-READY

0 branch-caused failures. All 6 pytest packs green (one after quarantine-aware adjudication), all 3 non-pytest checks green, mock fidelity SOUND, registration surface fully verified, execution-lane boot gate PASS. Web automation: **N/A — daemon-side tools, zero frontend surface** (nothing to browser-test by design).

### Summary
- Total observations: 6 packs + 3 checks; 3,094 pass-observations / 5 failures (all pre-existing quarantined family) / 79 skips / 0 timeouts
- Feature suite: 32/32 PASS
- Mock fidelity (TrueAuto rule): **SOUND — zero drift, zero call-count-only scenario tests**
- ensure.md (scoped Core): 4/4 PASS · Release Gate: NOT RUN (justified below)
- Quick fixes applied: 0 (acceptance gate = report-only per commission)
- Quarantined: 5 tests skipped-by-adjudication (pre-existing `TestAccessMemoryArchive`, matches existing QUARANTINE.md row)

### Scope Decision
> Change = 2 new agent-facing tools wrapping the EXISTING manager service layer + registry entries + agent docs + pure-mock tests. Prod footprint: `daemon/tools/instance.py`, `daemon/tools/_tool_registry.py`, `agents/{ari,leader}` docs, 1 new test file. No service-layer/manager/router code changed. Full suite NOT warranted; ran: feature suite, full containing dir `tests/unit/tools/` (47 files, 2,884 collected = inventory count exactly), router-level pause/resume endpoint tests, job-queue-tools access-pattern sibling, concurrency atomic pack, scrubbed dev.sh boot lane-gate, registration-surface probe, mock-fidelity audit. Skipped: `tests/job_queue/` (branch touches zero job-queue-lane code; known pre-existing RED lives there — TestSite1InlineMirrorFinalize ×3 + enqueue_shared drift; execution-lane intersection covered instead by the boot gate + concurrency pack per the lane-intersection convention). Skipped: `tests/unit/routers/test_stop_instance_subtree.py` (tests the deprecated `/stop` alias, not pause/resume endpoints). Offer to expand if the leader wants the job_queue A/B leg.

## Per-Suite Results

| # | Pack / Check | Result | Counts | Runtime | Worker |
|---|---|---|---|---|---|
| 1 | `pause_resume_feature_unit_test` | ✅ PASS | 32/32 (`32 passed in 7.95s`) | 7.95s | bafc2a5f |
| 2 | `tools_dir_regression_test` HALF A (a–m) | ✅ PASS (quarantine-aware) | 1052P/4S/0F + 5 quarantine-family F | 143.08s | 8333feb2 |
| 3 | `tools_dir_regression_test` HALF B (n–z) | ✅ PASS | 1822P/1S/0F | 56.58s | 3df10fce |
| 4 | `router_pause_resume_test` (tests/test_api.py -k pause/resume) | ✅ PASS | 9/9, 38 deselected | 2.46s | 7ce3d90b |
| 5 | `job_queue_tools_sibling_test` | ✅ PASS | 81/81 (`81 passed in 5.71s`) | 5.71s | b42ebe7d |
| 6 | `concurrency_atomic_unit_test` (registered pack) | ✅ PASS | 98P/74S/0F — byte-identical to 2026-09-23 baseline | 60.86s | 2449e1ac |
| 7 | Lane-gate boot probe (scrubbed dev.sh) | ✅ PASS | 30s+ must-not-crash, clean teardown | ~2m14s | 58df5494 |
| 8 | Registration-surface probe | ✅ PASS | 12/12 checks | ~1m | c8dfb258 |
| 9 | Mock-fidelity + scenario-mapping audit | ✅ SOUND | 0 drift · 0 call-count-only | ~4m | 93390808 |

Coverage completeness (HALF A + HALF B): both globs cover all 47 `test_*.py` files in `tests/unit/tools/`; collected 1,061 + 1,823 = 2,884 = inventory `--collect-only` count exactly. HALF B re-ran the feature suite inside the pack — no divergence vs the solo 32/32 (no ordering/shared-state interference).

## 7-Scenario → Test Mapping (all OUTCOME-asserting; verified by audit worker)

| # | Commissioned scenario | Test node | Key assertion |
|---|---|---|---|
| a | Pause running instance, reason omitted → `suspension_reason=None` | `TestPauseInstance::test_pause_running_instance` | exact result dict `{"paused": True, "paused_ids": [TARGET], "skipped_ids": []}` (test:184) |
| b | Pause waiting parent → whole lineage (ancestors in paused_ids) | `TestPauseInstance::test_pause_waiting_parent_pauses_whole_lineage` | `paused_ids == lineage` 3 IDs order-preserved (test:203) |
| c | Cascade covers descendants | `TestPauseInstance::test_pause_cascade_to_descendants` | `paused_ids == [TARGET, CHILD, grandchild]` + `resume_instance_cascade.assert_not_awaited()` (test:223-224) |
| d | Idempotent re-pause → skipped_ids | `TestPauseInstance::test_repause_already_paused_is_skipped` | `{"paused": True, "paused_ids": [], "skipped_ids": [TARGET]}` (test:236) |
| e | Message-job spun on resume of running turn | `TestResumeInstance::test_resume_running_turn_spins_job_resuming` | `status == "resuming"`, `job_id == "work-1"`, endpoint-exact kwargs `message="resume", silent=False` (test:288-294) |
| f | Resume silent lane (parent+child), status passthrough | `TestResumeInstance::test_resume_silent_lane_status_passthrough[silent_resume/wake_enqueued]` | target+child statuses verbatim, child `silent=True` (test:336-341) |
| g | Project-scope access denial | `TestPauseResumeAccessControl::test_pause_project_mismatch_denied` / `::test_resume_project_mismatch_denied` | exact denial dict + services never awaited (test:610-625) |

**CALL-COUNT-ONLY scenario tests: zero.** Beyond the 7: question-paused resume refusal (commit `099d168f`) pinned by `test_resume_refused_when_question_pack_pending` (exact refusal dict + both services `assert_not_awaited`); W1 fail-closed repo-error deny pinned with `side_effect=RuntimeError`; None-handle → `no_active_job`; 4-status verbatim passthrough; allow-lanes (unscoped/same-project/system-default/anonymous) — all OUTCOME-asserting.

## Mock-Fidelity Verdict: SOUND (TrueAuto rule satisfied)

Per-API, stubs vs REAL def sites (cites from audit worker):
1. `pause_instance_cascade` — REAL-MATCHES. Real: `manager.py:9562` → `instance_lifecycle.py:3041`, returns exactly `{"paused_ids": [...], "skipped_ids": [...]}` (`:3396`, `:3127`). Stub key set identical; tool reads only guaranteed keys.
2. `resume_instance_cascade` — REAL-MATCHES. Real: `manager.py:9602` → `instance_lifecycle.py:3614`, returns `{"resumed_ids", "skipped_ids", "target_id"}` (`:3859`, `:3661`). Stub 3-key identical.
3. `resume_processing_job` — REAL-MATCHES, all 6 statuses + **None** + raise. Real: `manager.py:9619`; branches resuming/already_resuming/silent_resume/wake_enqueued/wake_failed(+error,refusal_kind)/deferred_report_recovery(+recovery_count); `return None` at `:10038` (no running turn). None branch exercised by test (→ `no_active_job`, matching tool `instance.py:4727-4732` and HTTP endpoint `instances.py:835-839`).
4. Access-check repo lookup — REAL-MATCHES + fail-closed VERIFIED. Tool calls repo DIRECTLY (`instance.py:486-487`) bypassing the swallow-all helper (`:412-428`); real `SQLModelInstanceRepository.get` is **sync** (`repository.py:387-390`, returns `Instance|None`); stub is sync MagicMock (an AsyncMock would have failed loudly). `except Exception` at `instance.py:493` → deny (`:496-505`) — strictly STRONGER than the `_check_job_access` sibling (bare `.get`, no try/except).
5. Question-manager guard — REAL-MATCHES (sync `get_question_pack`, `.status` contract).

Wrong-seam check: NO — mocks sit on exactly the objects the tool invokes (manager facade methods, `_instance_repository.get`, `_question_manager.get_question_pack`, async `get_instance`).
Documented non-material divergences (intentional, pinned, not mock-vs-reality mismatch): tool passes `suspension_reason` superset (None-default byte-identical); tool REFUSES resume on pending question pack where the HTTP endpoint supersedes the gate (commit `099d168f`, pinned as the tool's own contract at test:394-432, documented in tools_note.md).

## ensure.md Validation (scoped Core — execution-lane intersection applied)

| Requirement | Status | Evidence |
|---|---|---|
| Core #1 no regressions in changed packs | ✅ PASS | all scoped packs green (T1 after quarantine exclusion) |
| Core #2 deadlock/concurrency integrity | ✅ PASS | `concurrency_atomic_unit_test` 98P/74S/0F, baseline-identical |
| Core #3 no sync DB on event loop | ✅ PASS | same pack (thread-identity tests) |
| Core #4 dev.sh `--timeout-graceful-shutdown 10` | ✅ PASS | `dev.sh:102` exact flag on launch line |
| Release Gate | NOT RUN — justified | feature-scoped change (additive tools over existing service layer; no architecture refactor, not a release); lane intersection covered by boot gate |
| Lane gate (convention: judge by execution-lane intersection) | ✅ PASS | scrubbed `./dev.sh` boot: engine line `Creating PostgreSQL engine: localhost:5432/ensemble_dev` (DEV DB — NOT ensemble_prod), `Uvicorn running on http://127.0.0.1:8079`, `Application startup complete`, T+30s+ alive, zero Traceback/ImportError, clean uvicorn shutdown; teardown all 5 spawned PIDs gone, 8079 freed, **port 9797 (ensemble-prod, live) untouched throughout** |

ensure.md Improvement Notices: none — no contradictions this run (boot-probe safety fence applied: `env -u POSTGRES_* DATABASE_URL` on every invocation, per the 2026-08 live-DB incident note).

## Registration-Surface Check: PASS 12/12
- `KNOWN_TOOL_NAMES` + `_tool_metadata`: both tools, category `instance` (short_doc + 1,922/2,869-char full docs)
- ari factory output: 35 tools, both present; schema keys `['instance_id', 'reason']`
- leader: reachable via `instance` category expansion (22 tools resolved)
- `tool_help`: listed under `## Instance Management` on all 3 paths (no-arg / per-name / per-category)
- Docs: `agents/ari/tools_note.md:450-525` + `agents/leader/tools_note.md:37-38`
- Boot probe corroboration: no import/registration crash at daemon startup

## Pre-existing Baselines (NOT attributed to this branch)
1. **5× `TestAccessMemoryArchive`** — appeared in HALF A exactly as quarantined (existing QUARANTINE.md row, triple-attributed pre-branch). Surfaced despite `--ignore` because bash glob expansion made the file an explicit cmdline path; pytest `--ignore` does not apply to explicit paths. Adjudicated: quarantined → do not count toward pack PASS/FAIL. Pack-composition lesson recorded (see LESSONS). The file's other 26 tests passed inside HALF A.
2. `TestSite1InlineMirrorFinalize` ×3 + `enqueue_shared` idle→running drift — did NOT appear (tests/job_queue/ not in scope; zero lane-code changes on branch).
3. Ambient dev-boot noise: plane MCP session failures (no plane server in dev env) — pre-existing, daemon survives.
4. `maintenancer` deny-entry `git_commit` config warning (`daemon/registry.py:1177-1179`) — pre-existing config drift, unrelated.

## Findings (non-blocking, report-only per commission)
- 🟢 None branch-caused. Zero defects to route back.
- 🟢 Intentional tool-vs-endpoint divergence (question-pack refusal vs supersession) — already documented in branch docs + pinned by tests; surfaced here for the record.

### Gaps
None blocking. Deliberately not run (see Scope Decision): `tests/job_queue/` A/B leg, `/stop` subtree router file, Release Gate E2E. Expandable on request.

## Documentation Updated
- [x] PACKS.md — gate section registered pre-run + OUTCOME line post-run
- [x] RESULTS/2026-09-24-agent-pause-resume-tools-acceptance.md — this report
- [x] LESSONS/2026-09-24-pytest-ignore-explicit-path-glob.md — pack-composition gotcha
- [x] QUARANTINE.md — no change needed (failures match existing row exactly)
- [x] rules/ensure.md — untouched (user-owned)

## Code Changes Summary
- Feature/test/production code: **ZERO modifications** (report-only gate honored by all 10 workers; verified via git status each time)
- Commit: N/A (tester docs only; no commit required — no code changed)

### Overall Status
- Unit/feature suites: ✅ PASS · Sibling/regression: ✅ PASS · Router HTTP: ✅ PASS
- Mock fidelity: ✅ SOUND · Registration surface: ✅ PASS · ensure.md scoped Core: ✅ 4/4 · Lane gate: ✅ PASS
- Web automation: N/A (no frontend surface)
- **Testing Complete: ✅ MERGE-READY**
