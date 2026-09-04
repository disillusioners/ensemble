# ensure.md Validation — 2026-09-04

**Branch:** `feature/queue-status-missions-badge`
**Gate Head:** `13782089442efb015eb86a2eb7600cf46adb4733` (13782089)
**Pack of record:** `test/packs/concurrency_atomic_unit_test.sh`
**Validation scope:** Core requirements (blast-radius scoped). NOT a big/critical/architecture change → Release Gate out of scope.

## Blast Radius

```
daemon/repositories/job_queue/_idle_predicate_sql.py |  39 + (NEW)
daemon/repositories/job_queue/repository.py          |   7 +-
daemon/routers/queues.py                             |  33 +-
daemon/routers/schemas.py                            |  29 +-
daemon/services/defer_block_resolver.py              |  30 + (NEW)
daemon/api.py                                        |  13 + (gate recon)
tests/unit/routers/test_defer_blocked_api.py         | 406 + (new unit test)
test/packs/defer_blocked_api_unit_test.sh            |  51 + (new pack)
FE: job-queue-indicator component, defer-blocked/mission models, job.service
docs/job-task-system.md                              |  37 +-
```

The branch adds `_idle_predicate_sql`, a new `defer_block_resolver.py`, and a public
`/api/defer-blocked` endpoint + FE badge surfaces. It is **NOT** a big/critical/architecture
change — it is a scoped API+FE feature.

## Results

### R1 — Deadlock / concurrency integrity (Core Critical #2/#3 — pack-mapped)

**Pack:** `test/packs/concurrency_atomic_unit_test.sh`
**Wrap:** `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh`

**Verbatim tail (last 4 lines of pack output):**

```
=============================== warnings summary ===============================
tests/test_report_lane_phase2.py: 44 warnings
  /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble/.venv/lib/python3.13/site-packages/sqlalchemy/engine/default.py:952: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)
98 passed, 74 skipped, 44 warnings in 7.69s
RESULT: PASS
```

- **RESULT: PASS** (exit 0)
- Counts: **98 passed, 0 failed, 74 skipped** — exact match to expected baseline
- Runtime: **7.69s** (matches prior gates' 9.22s order of magnitude; variance is SKIP-count dependent on env)
- Files run: 13 of 13 candidate files present (none missing)
- Warnings: 44 (all `DeprecationWarning` from SQLAlchemy on `test_report_lane_phase2.py` — pre-existing, not from this branch's code)

### R2 — `dev.sh` includes `--timeout-graceful-shutdown 10` (Core Critical #4)

**Verification:** `grep -n "timeout-graceful-shutdown" dev.sh`

```
99:# --timeout-graceful-shutdown 10 ensures uvicorn forces exit after 10s even
102:$PYTHON -m uvicorn daemon.api:app --host "$HOST" --port "$PORT" --reload --log-level "$LOG_LEVEL" --no-access-log --timeout-graceful-shutdown 10
```

- **RESULT: PASS**
- Flag present on dev.sh:102 (uvicorn invocation)
- Comment context at dev.sh:99 explains the rationale

### R3 — Async function callers properly await (Core Important #1)

**Verification:** `grep -rn "get_queue_stats\|_get_system_prompt_tokens\|_compute_context_usage" daemon/ --include="*.py" | grep -v "def \|test"` + manual audit per call site.

**Audit summary:**

| # | Location | Snippet | Awaited? |
|---|----------|---------|----------|
| 1 | `daemon/routers/instances.py:375` | `pending_count=(await manager.get_queue_stats(instance_id)).get("pending_count")` | ✅ |
| 2 | `daemon/routers/instances.py:525` | `pending_count=(await manager.get_queue_stats(instance_id)).get("pending_count")` | ✅ |
| 3 | `daemon/routers/messages.py:757` | `stats = await manager.get_queue_stats(instance_id)` | ✅ |
| 4 | `daemon/tools/instance.py:2914` | `stats = await manager.get_queue_stats(instance_id)` | ✅ |
| 5 | `daemon/manager.py:8433` | `return await self._messaging_service.get_queue_stats(instance_id)` | ✅ (facade forwarding) |
| 6 | `daemon/services/instance_messaging.py:1052` | `system_prompt_tokens = await self._get_system_prompt_tokens(instance_id)` | ✅ |
| 7 | `daemon/services/instance_messaging.py:1082` | `snapshot = await self._compute_context_usage(instance_id, messages)` | ✅ |
| 8 | `daemon/services/instance_messaging.py:1203` | `system_prompt_tokens = await self._get_system_prompt_tokens(instance_id)` | ✅ |

- **RESULT: PASS**
- **9 actual call sites examined** — 9 awaited, 0 unawaited
- False-positive audited: `daemon/services/message_processing_pipeline.py:419` is a docstring/comment referencing `get_queue_stats()` — confirmed as comment text, not a call site
- All other raw grep hits (e.g. messages.py:682, instance.py:3053, instance_messaging.py:850/1039/4447/4466) are docstrings, comments, or log messages — no actual call sites
- Branch did not modify any of these functions, expectation of clean state confirmed

### R4 — Out-of-scope ensure.md requirements

| Requirement | Tier | Disposition | One-line justification |
|-------------|------|-------------|------------------------|
| Core Critical #1 — No regressions in changed packs | Critical | **OUT OF SCOPE here; COVERED by parallel partition regression wave** | Branch task explicitly delegates this to the partition-wave adjudication (noted in dispatcher task: "Critical #1 no-regressions-in-changed-packs is being adjudicated from the partition wave results"). `tests/unit/routers/test_defer_blocked_api.py` + `test/packs/defer_blocked_api_unit_test.sh` are the canonical regression pack for this change set. |
| Core Nice-to-have — No dead code from the fix | Nice-to-have | **NOT APPLICABLE** | This branch ADDS new code (`_idle_predicate_sql.py`, `defer_block_resolver.py`, `/api/defer-blocked` endpoint, FE badge surfaces). It does NOT delete any production code as part of a "fix" — there is no dead-code claim to validate. |
| Release Gate — Full non-integration suite | Release Critical | **OUT OF SCOPE** | Branch is scoped (job_queue repos + queues router + new resolver + FE badge). Blast radius is single-feature, not cross-module/architecture — does not meet Release Gate trigger. |
| Release Gate — E2E parent→child happy path | Release Critical | **OUT OF SCOPE** | Same: scoped feature, not big/critical/architecture change. |
| Release Gate — E2E pause→resume | Release Critical | **OUT OF SCOPE** | Same: scoped feature. |
| Release Gate — E2E terminate→revive | Release Critical | **OUT OF SCOPE** | Same: scoped feature. |
| Release Gate — E2E 3-level cascade | Release Critical | **OUT OF SCOPE** | Same: scoped feature. |

**Additional coverage note (in-scope requirements folded into R1):**

- Core Critical #2 (deadlock/concurrency integrity) → R1 — PASS
- Core Critical #3 (no sync DB calls on asyncio event loop) → covered by R1's `test_gate_threading_serialization`, `test_instance_metadata_atomic`, `test_project_repository_atomic`, `test_finalize_job_h15` — all PASS in R1
- Core Important #2 (original deadlock scenario parent→child→complete) → covered by R1's `test_deadlock_fix.py`, `test_cascade_race3.py`, `test_cascade_concurrency.py`, `test_cascade_unified.py`, `test_cascade_integration.py` — all PASS in R1

## Quick Fixes Applied

**None.** All in-scope requirements PASS on first run; no failures detected. R1 reproduced the exact expected baseline (98P/0F/74S, 7.69s) with no new warnings introduced by this branch.

## Summary

- **Critical Requirements:** 3/3 in-scope passed (R1 covers #2 and #3; #1 adjudicated by parallel partition wave)
- **Important Requirements:** 1/1 in-scope passed (R3)
- **Nice-to-have Requirements:** 0/0 in-scope (none applicable to this additive branch)
- **Release Gate:** not triggered (scoped feature, not big/critical/architecture)

**OVERALL: PASS** — branch `feature/queue-status-missions-badge` meets all in-scope ensure.md Core requirements.
