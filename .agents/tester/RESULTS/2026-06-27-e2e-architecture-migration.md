# E2E Test Report — Architecture Migration Validation
**Date**: 2026-06-27 01:52 UTC
**Session**: `e2e-architecture-validation` (ses_0f94f4a84ffe300CajaTZcQYvA)
**Branch**: `feature/migration-followups`

## Context
Post-architecture-migration E2E validation. The migration eliminated MESSAGE-vs-Job coupling (D11+D13 collapse), making DependencyBus the sole completion authority. These E2E tests validate that the full system works end-to-end with real HTTP API calls and real LLM calls against a live PostgreSQL-backed daemon.

---

## Summary

| # | Test | Status | Duration | Notes |
|---|------|--------|----------|-------|
| 1 | `test_parent_child_workflow_happy_path` | ✅ **PASS** | 45s | After quick fix `0917449b` |
| 2 | `test_pause_after_spawn_then_resume` | ❌ **FAIL** | 161s | Architectural regression — leader stuck at `waiting_children` |
| 3 | `test_terminate_after_spawn_then_revive` | ✅ **PASS** | 41s | |
| 4 | `test_wave_spawn_with_defer_queue` | ✅ **PASS** | 67s | |

**Result: 3/4 passed (75%)**

---

## Daemon Configuration
- **Port**: 8079
- **Database**: PostgreSQL (`ensemble_dev`)
- **Startup**: `./dev.sh` (without `--reload` to prevent mid-test uvicorn restarts)
- **OpenAI**: Real LLM calls (costs incurred)

---

## ensure.md Validation Results

### Critical Requirements (E2E section)
| Requirement | Status | Details |
|------------|--------|---------|
| E2E: Normal parent→child workflow completes (happy path) | ✅ PASS | After PostgreSQL `.scalars()` fix |
| E2E: Pause after spawn, then resume works correctly | ❌ FAIL | Leader stuck at `waiting_children` after resume |
| E2E: Terminate after spawn, then revive documented | ✅ PASS | |
| E2E: Wave spawn (2 children) + defer queue + cross-system | ✅ PASS | |

### Critical (5/5 of non-E2E requirements assumed previously validated — not re-run in this session)

---

## Quick Fixes Applied

### Fix 1: PostgreSQL Row/tuple adapt error — `0917449b`
- **File**: `daemon/services/instance_lifecycle.py:757`
- **Root cause**: `select(InstanceHierarchy.child_id).where(...).all()` returns Row/tuple objects on PostgreSQL (e.g., `('uuid',)`), which were passed as parameters to subsequent queries. Triggered `psycopg.ProgrammingError: cannot adapt type 'Row'`.
- **Fix**: Use `.scalars().all()` to unwrap Row → plain string values.
- **Commit**: `0917449b` — `fix: use .scalars() for single-column child_id select on PostgreSQL`
- **Note**: Same pattern exists at `daemon/repositories/instance/repository.py:265` (not blocking, but should fix for consistency).

### Fix 2: PostgreSQL AmbiguousParameter — `036d09b7`
- **File**: `daemon/services/instance_lifecycle.py:2457`
- **Root cause**: Single `now_iso` parameter bound to both `cancel_requested_at` (VARCHAR) and `completed_at` (TIMESTAMP) in same query. PostgreSQL raised `psycopg.errors.AmbiguousParameter: inconsistent types deduced for parameter $3`. SQLite was lenient about the type mismatch.
- **Fix**: Two separate parameters — `now_iso` (string) for VARCHAR column and `now_dt` (datetime) for TIMESTAMP column.
- **Commit**: `036d09b7` — `fix: separate datetime params for cancel_requested_at and completed_at`

---

## Test 2 Failure Analysis (Architectural Regression)

### Error
```
AssertionError: Leader ee3f4ddf... did not reach a terminal status after resume (last status: waiting_children)
```

### Root Cause (Architectural — NOT quick-fixable)
1. Leader resumed successfully after pause (POST /resume returned 200)
2. LLM invoked — leader made tool calls, spawned developer child, sent message
3. Developer child completed and reported back to parent
4. Child completion processed at 08:45:33 — parent status set to `waiting_children`, "1 pending messages"
5. The pending message completed **WITHOUT an LLM call** (phantom completion)
6. Leader stuck at `waiting_children` with 0 pending messages and 0 pending children
7. No further LLM calls ever fired — test timed out at 120s

### Hypothesis
The DependencyBus terminal-completion hook (`emit_terminal` → `PROCESS_REPORT` → `JobFeedbackObserver`) marks the parent's pending message as completed WITHOUT re-queuing the parent for a final LLM turn. Under the pre-migration `CorrelationManager`, completion re-queued the parent for a final response; under the new `DependencyBus`, that re-queue path appears missing.

This is exactly the kind of bug E2E tests catch that unit tests miss (due to DB mocking).

### Impact
- Pause→Resume→Final-Response flow is broken
- Happy path, terminate, and wave-spawn all work correctly
- The regression is specifically in the resume-after-child-completion path

### Recommended Action
Non-trivial architectural fix needed. Investigate the DependencyBus terminal hook and compare with old CorrelationManager re-queue behavior. The fix likely involves ensuring that when a child completes and the parent has a pending message, the parent is re-queued for a final LLM turn.

---

## Overall Assessment

The architecture migration is **largely successful** — 3 of 4 critical workflows pass end-to-end. The DependencyBus single-record invariant is sound for happy path, terminate, and wave-spawn scenarios.

**One identified gap**: The pause→resume→child-completion→parent-final-response chain breaks because the parent's final LLM turn never fires. This needs follow-up work before the migration can be considered fully complete.

**Two PostgreSQL compatibility bugs** were found and fixed during this session — both were latent bugs that SQLite's leniency had masked. These are important fixes regardless of the architecture migration.

---

## Environment Notes
- Stale `SSL_CERT_FILE` env vars from prior session pointed to non-existent path — resolved by setting `SSL_CERT_FILE` to certifi path
- `uvicorn --reload` caused 13 mid-test daemon restarts — disabled reload for E2E runs
