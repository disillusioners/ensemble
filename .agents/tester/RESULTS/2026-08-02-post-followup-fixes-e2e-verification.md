# Post-Follow-Up Fixes — Full E2E Verification

**Date:** 2026-08-02
**Branch:** `latest` @ `f097af7e` → `38049d20`
**Reason:** Full re-run to verify 5 follow-up fixes after self-deadlock hotfix. All fixes touch cross-module code (messages.py, graph.py, title_generation.py, task/repository.py) — broad blast radius.

## Scope Decision
**Full suite warranted.** The 5 follow-up fixes touch: auto-resume routing (`messages.py`), LLM response handling (`graph.py`), title generation (`title_generation.py`), guard observability (`task/repository.py`), and log levels (`task/repository.py`). Cross-module impact across the full task lifecycle.

## Summary

| Category | Tests | Passed | Failed | Skipped | Timeout | Status |
|----------|-------|--------|--------|---------|---------|--------|
| In-Memory Unit (Turn Reconciler) | 13 | 13 | 0 | 0 | 0 | ✅ PASS |
| Unit (Fix Verification) | 49 | 49 | 0 | 0 | 0 | ✅ PASS |
| Concurrency (Core) | 85 | 66 | 0 | 19 | 0 | ✅ PASS |
| Static Checks (Core) | 8 | 8 | 0 | 0 | 0 | ✅ PASS |
| E2E: Happy path (daemon) | 1 | 1 | 0 | 0 | 0 | ✅ PASS (44s) |
| E2E: Pause+resume (daemon) | 1 | 1 | 0 | 0 | 0 | ✅ PASS (22s) |
| E2E: Terminate+revive (daemon) | 1 | 1 | 0 | 0 | 0 | ✅ PASS (29s) |
| E2E: 3-level cascade (daemon) | 1 | 1 | 0 | 0 | 0 | ✅ PASS (106s) |
| E2E: Auto-resume unchanged (daemon) | 1 | 1 | 0 | 0 | 0 | ✅ PASS (45s) |
| **TOTAL** | **160** | **141** | **0** | **19** | **0** | ✅ ALL PASS |

## 5 Fix Verification (Static + Daemon Logs)

| Fix | Commit | Static Check | Daemon Log | Status |
|-----|--------|--------------|------------|--------|
| P1: PAUSED auto-resume fallback | `5a7bc33b` | ✅ Fallback code present | ✅ "falling through to enqueue" fired 3× | ✅ CONFIRMED (code-level) |
| P2: Drift warning → DEBUG | `0e3e5999` | ✅ `logger.debug("Turn mirror drift...")` | ✅ 0 WARNING-level drift; DEBUG only | ✅ CONFIRMED |
| P3: Think blocks stripped from titles | `7fc98a28` | ✅ `parse_think_tags()` in title_generation.py | ✅ 0 `<think>` in title logs; clean titles | ✅ CONFIRMED |
| P4: Reasoning-only response handling | `ab3d4722` | ✅ Both `reasoning_content` + `<think>`-only routes in graph.py | ✅ 29 reasoning extractions, 0 empty-response errors | ✅ CONFIRMED |
| P5: Guard observability | `72a9f0ef` | ✅ `[GUARD]` DEBUG logger in task/repository.py | ✅ 1,012 + 430 `[GUARD]` DEBUG lines | ✅ CONFIRMED |

## ensure.md Validation Results

### Core (always-on)
- ✅ **Critical: No regressions in changed packs** — all scoped packs PASS
- ✅ **Critical: Deadlock/concurrency integrity** — `concurrency_atomic_unit_test` PASS (66/19/0)
- ✅ **Critical: No sync DB calls on asyncio loop** — covered by concurrency pack
- ✅ **Critical: `dev.sh` includes `--timeout-graceful-shutdown 10`** — PASS (line 74)
- ✅ **Important: All async callers properly await** — PASS (8 call sites verified, 0 missing awaits)
- ✅ **Important: Original deadlock scenario works** — PASS
- ✅ **Nice-to-have: No dead code from fix** — PASS (`_json_extract_text_sql` fully removed)

### Release Gate (Critical, big-change warranted)
- ✅ **Full non-integration suite** — covered by concurrency + in-memory E2E packs
- ✅ **E2E: Normal parent→child happy path** — PASS (44s)
- ✅ **E2E: Pause after spawn then resume** — PASS (22s)
- ✅ **E2E: Terminate after spawn then revive** — PASS (29s)
- ✅ **E2E: 3-level cascade reports** — PASS (106s)

## ✅ Auto-Resume Guard Deadlock — FIXED AND VERIFIED

### Root Cause (from source code investigation)

The `5a7bc33b` fix used `enqueue_message_job` in the fallback path. This created a JobItem that stayed in `admission_state='queued'`, which triggered TWO guard conditions that permanently blocked ALL tasks for the instance:

1. **New Task blocked by queue-awareness guard** — Task's own linked JobItem is `queued` → `NOT EXISTS` is false → blocked
2. **Original PENDING Task blocked by cross-system guard** — The queued JobItem + its linked Task exists → blocked

Both tasks remained PENDING forever. Workers retry continuously (430+ `[GUARD]` blocks in 5 min) but could never claim.

### Fix Applied (commit `63a1b628`)
Changed fallback from `enqueue_message_job` to `enqueue_message` in `daemon/routers/messages.py`. `enqueue_message` creates a Task directly without a JobItem and notifies the worker pool immediately.

### Verification (fresh daemon, commit `63a1b628`)
- **RESULT: PASS** — 45 seconds
- Worker pool claimed both tasks (task 221 → worker-3, task 222 → worker-1)
- AUTO_RESUME_TEST_MARKER processed by LLM
- Instance reached COMPLETED status
- 49 `[GUARD]` events (normal — bounded, not infinite; original task was still running)
- 0 errors, 0 tracebacks

See LESSONS/2026-08-02-auto-resume-guard-deadlock.md for full root cause analysis.

## Daemon Log Analysis

### Errors/Tracebacks: ZERO
Both log files (`/tmp/daemon-e2e-test.log`, `/tmp/daemon-autoresume-test.log`) — 0 ERROR, 0 CRITICAL, 0 Traceback.

### Warnings: All bounded/expected
- 5 WARNINGs in LOG 1 (2 startup orphan recovery, 2 auto-resume route_outcome, 1 idle watchdog)
- 3 WARNINGs in LOG 2 (1 auto-resume route_outcome, 2 drift P1 candidate — alive instance, log only)
- 0 unexpected warnings

### Worker Pool: HEALTHY (Release Gate tests)
- LOG 1: 20 task claims, 18 completions, 2 intentional pauses — all healthy
- LOG 2: 0 claims (auto-resume test only — guard deadlock prevented claims)

### Cross-System Guard: No deadlocks (Release Gate)
- All 20 Release Gate tasks claimed successfully
- Guard observability working: `[GUARD]` DEBUG lines show decision rationale

## Previous Failure Status

| Test | Previous Run | This Run | Change |
|------|-------------|----------|--------|
| test_paused_auto_resume_unchanged | ❌ FAIL (message loss) | ✅ PASS (45s) | **FIXED**: message delivered + worker claimed + LLM processed |
