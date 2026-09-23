# Test Report: job-event watch replay fix — branch fix/job-event-watch-replay @ ac789d45

Date: 2026-09-23T18:30Z
Branch: `fix/job-event-watch-replay` (fix `4b8e5e1c` + hardening `ac789d45`, base `6ed47fca` = latest @ v0.14.0)
Worker instances: 0219d026 (regress), 442a28ec (audit), c22292f3 (scenario), 9286d0b1 (jqfull), 5dfe43b0 (utfull), 80364121 (bootab), 7f1e1ec5 (concur), be5529eb (adjacent) — 8/8 reported, 0 gaps, 0 re-dispatches.

## FINAL VERDICT: **ORIGINAL SYMPTOM CLOSED — YES**

Every leg of the original incident mechanics (mission f27e2d15 shape) is proven dead on the fixed code by an end-to-end synthetic replay on a real stack, corroborated by 100%-green fix-owned tests, an honest mock audit, and full-dir lane gates with zero new reds.

---

## Summary

| Leg | Result |
|---|---|
| 1. Regression pack (new/changed tests) | ✅ Fix-owned tests 100% green (3 reds in scope = pre-existing, inside documented known set) |
| 2. Mock fidelity audit | ✅ HONEST — real repos/CAS/is_terminal; 13/14 new tests narrow; 0 blockers |
| 3. Incident-mechanics scenario (a)–(e) | ✅ 5/5 PASS on real-stack synthetic replay |
| 4. Lane gates (boot + full dirs) | ✅ Boot PASS; jq dir green-modulo-known (0 NEW); unit/tools dir all green; concurrency PASS; adjacent PASS |
| Known-red confirmation vs base 6ed47fca | ✅ 4/4 spot-checked A/B identical (pre-existing confirmed) |
| **Overall** | ✅ **READY — merge cleared on testing grounds** |

## Scope Decision

Full-dir sweeps warranted (not reduced): change intersects the job/task/queue execution lane per the ensure.md e2e rule (job_feedback_observer = completion sink; job_queue tools) → full-dir-per-merge lesson applied. Scoped additions: concurrency pack (ensure.md Core), orphan/observer-adjacency pack, boot gate + base A/B.

## ENV safety (ambient LIVE hazard)

Ambient `POSTGRES_DB=ensemble_prod` @ 10.44.0.2:5432 confirmed present in worker shells. All 8 dispatches carried the mandatory `unset POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_USER POSTGRES_PORT POSTGRES_DB POSTGRES_URL DATABASE_URL` block; each worker verified `env | grep -i postgres` empty pre-run. Boot gate engine log confirmed `localhost:5432/ensemble_dev` (dev.sh .env guard overrode ambient LIVE vars — designed protection fired). Live 9797 / demo 7979 / self 8088 untouched throughout. Parked mission f27e2d15 untouched (in-process synthetic fixtures only, ids `scn-*`).

---

## 1. Regression pack (scoped, ~88 collected, 29s)

- `TestRegistrationTerminalFilter` **5/5 PASS** (tests/unit/tools/test_mission_watch_tools.py:445)
- `TestNotifySlotWiring` **5/5 PASS** (tests/job_queue/test_job_feedback_observer.py:2723)
- `TestAlreadyTerminal` **4/4 PASS** (task expected 3 — actual class = 3 rewritten pins + 1 unchanged M2 dead_letter-revived pin; count discrepancy benign)
- `test_terminal_write_census.py` **2/4** — 2 failures pre-existing (see known-reds)
- Whole-file: mission_watch_tools 36/36; observer 47/48; census 2/4
- Command: `timeout 300 .venv/bin pytest <3 files> -v --tb=short -q -rf` (POSTGRES-scrubbed)

## 2. Mock fidelity audit — HONEST (read-only, git-show evidence)

Strengths (file:line-verified):
- Real `JobWatcherRepository` on file-backed SQLite — genuine UPSERT (`ON CONFLICT DO UPDATE`, watcher_repository.py:99-104) and CAS (`DELETE ... RETURNING`, :277-287), not faked.
- Real `work_status.is_terminal` with canonical `_TERMINAL_STATUSES = {completed, settled, failed, cancelled, dead_letter}` (work_status.py:145-147) — no hand-rolled terminal set.
- `notify_watchers` AsyncMock matches real signature (job_queue_service.py:322-329); F2 tests assert `c.kwargs` — positional args don't surface in `.kwargs`, so the pre-fix positional bug is structurally un-passable.
- 13/14 new tests individually fail on pre-fix (base) logic; the 1 non-narrow test (`test_terminated_refire_cancel_status_uses_result_slot`, `result_summary=None` twin) is gap-filled by its non-None-payload sibling (hardened in ac789d45) — sound pair design.

Findings: 6 notes, 0 blockers/concerns (mock boundaries appropriate; census pointer `:2071` verified accurate; M2 pin intentionally unchanged).

## 3. Incident-mechanics scenario — 5/5 PASS (real stack, synthetic fixtures)

Script: `test/scenarios/job_watch_replay_incident_scenario.py` (1,524 lines, commit `347896a2`, parent ac789d45). Real JobWatcherRepository UPSERT+CAS, real MissionResolver/TaskRepository, real `JobFeedbackObserver._process_event`/`_finalize_job` fan-out; only `notify_watchers`/delivery sink spied (CAS-gated). Pre-import guard aborts if any `POSTGRES_*` set. Runtime 4.5s (inner alarm 120s / outer 300s).

Setup: mission `scn-mission-001`, 13 receipts = 9 already-terminal + 4 live.

- **(a)** watch_mission → `"armed 4 live receipt(s)... 9 already-settled receipt(s) skipped"`; watched set = 4 live only; 0 emissions.
- **(b)** flip mission completed → candidate enumeration 13, **CAS-gated deliveries = 4** (live only); all 9 settled CAS-returned 0 rows — ZERO notifications for pre-terminal receipts.
- **(c)** re-call watch_mission → `"already terminal (completed). Armed 0 live... no historical replay"`; flip again → deliveries delta = **0** — duplicate burst dead (RC1).
- **(d)** slot wiring: completed → `{status: completed, error: None, result_summary: MISSION_ASSISTANT_TEXT}`; failed → `{status: failed, error: "real failure text — child crashed", result_summary: None}`; assistant text NEVER in `error=` (all three leak-check lists empty) — RC2 dead.
- **(e)** unwatch_job after consumption → `"no matching receipt watches"`; zero NEW emissions after unwatch.

## 4. Lane gates

| Gate | Result | Evidence |
|---|---|---|
| dev.sh static (`--timeout-graceful-shutdown 10`) | ✅ PASS | dev.sh:102 |
| dev.sh boot | ✅ PASS | engine `localhost:5432/ensemble_dev`; livez 200 (14s), readyz 200 (db/queue/services true); clean 1s shutdown; live daemons untouched |
| Full `tests/job_queue/` | 🟢 GREEN MODULO KNOWN | 13F/1,860P/38S of 1,911 in ~64s; **0 NEW reds**; all 13 adjudicated KNOWN (verbatim signatures) |
| Full `tests/unit/tools/` | ✅ PASS | 2,840P/0F/6S/5-deselected in 41.6s; +103 net-new vs baseline (branch growth) |
| concurrency_atomic (ensure.md Core) | ✅ PASS | 98P/0F/74S in 68s; observer race + no-sync-DB-on-loop thread-identity tests green |
| orphan_active_job_recovery_suites | ✅ PASS | 53P/0F/0S in 17.8s; real EventBus↔observer pairing 9/9, bus-terminal/unwatch/defer 8/8 |

### Known-red reconciliation (documented 14 → observed 13 + 1 out-of-dir)

Documented families: 3× Site1 MagicMock-JSON (@repository.py:2602) + 4× instance-derived-status (incl. observer-skip + phase2) + 1× dev_sh hardcoded-path + 3× settled-rename + 2× census drift + 1× wedge_resolver event-loop = 14. This run: 13 in-dir (wedge_resolver PASSED — timing-flake, treat flake-susceptible). enqueue_shared idle→running drift lives at `tests/test_enqueue_shared.py` (outside this dir) — covered by A/B below.

**A/B spot-check vs clean base 6ed47fca (detached worktree /tmp/jw-ab-base, editable-install trap mitigated):** 4/4 FAIL→FAIL with byte-identical signatures — 3× TestSite1InlineMirrorFinalize (`TypeError MagicMock not JSON serializable` @ repository.py:2602) + enqueue_shared `assert 2 == 1` idle→running. **Pre-existing confirmed; developer's stash-verified claim independently corroborated.** Worktree removed post-run.

### ensure.md Validation Results (Core, blast-radius scoped)

- **Critical**: No regressions in changed packs — ✅ (scoped pack green modulo pre-existing; scenario + full dirs 0 NEW)
- **Critical**: Deadlock/concurrency integrity — ✅ concurrency_atomic 98P/0F
- **Critical**: No sync DB on asyncio loop — ✅ (thread-identity tests in pack)
- **Critical**: dev.sh `--timeout-graceful-shutdown 10` — ✅ static PASS
- **Important**: await-correctness / original deadlock scenario — ✅ (covered by concurrency pack)
- Release Gate NOT run — not warranted (bug-fix branch, not architecture/release; boot gate run instead per lane rule)

### ensure.md Improvement Notices

None — no contradictions encountered.

## Pre-existing reds carried (NOT branch-caused; need separate commissions)

1. Census fixture drift (2 reds): unlisted `manager.py:5047 _on_stale_task_permanent_failure` + 14 stale `hooked_at` entries across 5 untouched files.
2. Site1 MagicMock-JSON family (3) + instance-derived-status family (4) + settled-rename (3) + dev_sh hardcoded-path (1) — all documented since v0.13.10.
3. `wedge_resolver` (1) — flake-susceptible (passed this run).
4. enqueue_shared idle→running drift (1, out-of-dir).

## Quick Fixes Applied

None required — zero branch-caused failures. Scenario script committed (`347896a2`).

## Documentation Updated

- [x] RESULTS/2026-09-23-job-watch-replay-verification-ac789d45.md (this file)
- [x] MOCK_TESTS.md — scenario spec + Last Run (PASS, worker c22292f3)
- [x] PACKS.md — gate-run rows updated (regression_job_queue, regression_unit_tools, concurrency_atomic, orphan_active 51P→53P)
- [ ] QUARANTINE.md — no changes (no new quarantines; wedge_resolver already in documented known-red set)

## Code Changes Summary

- `test/scenarios/job_watch_replay_incident_scenario.py` (new, 1,524 lines) — commit `347896a2`. No production changes by testers.
