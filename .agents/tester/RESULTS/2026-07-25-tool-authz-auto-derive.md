# Test Report: Tool Authorization Auto-Derive
Date: 2026-07-25
Branch: `feature/tool-authz-auto-derive` @ `3b94ba85`
Worker instances:
- `e80664ca-2710-45dd-9065-b0b6dc13e617` (authz-auto-derive-unit, skill: test-pack-execution)
- `46d8e004-f1e2-4eee-ae95-f95fffc91a45` (job-queue-regression, skill: test-pack-execution)

## Feature Under Test
New module `daemon/tools/_auth.py` centralizes spawn authorization.
`_check_team_membership()` auto-derives implied `team_members` from the caller's
`tools.allow` categories via the `TOOL_REQUIRED_AGENTS` map (knowledge→explorer/kb-writer,
chart→charter, image→image-reader, council→governor). Removes the "double authorization"
requirement. `daemon/tools/instance.py` imports from `_auth`.

## Summary
- Total: 1514 tests | Passed: 1514 | Failed: 0 | Errors: 0
- Authz pack: 72/72 PASS | Job queue regression: 1442 passed, 38 skipped, 0 fail
- Quick Fixes Applied: 3 test changes across 2 commits
- Quarantined: 0 tests skipped

## Scope Decision
> **Full test suite NOT run — not warranted.** Change touches 2 source files
> (`daemon/tools/_auth.py` new + `daemon/tools/instance.py` import refactor) — a focused
> authorization logic change, single subsystem, no architecture impact. Ran 2 packs:
> `authz_auto_derive_unit_test` (direct feature coverage + ari no-spawn contract) and
> `job_queue_unit_test` (regression smoke sweep, 1442 tests). Skipped: concurrency pack,
> dev.sh check, E2E Release Gate — all unrelated to authz logic. Scope driven by actual
> change set, not pack-count ratio.

## ensure.md Validation Results (scoped to blast radius)

### Core — Critical
- ✅ **No regressions in changed packs** — PASS. Both packs in the change set return PASS.
- ⏭️ Deadlock/concurrency integrity — **N/A** (not in scope: no concurrency/deadlock code touched).
- ⏭️ No sync DB calls on asyncio loop — **N/A** (not in scope: authz logic has no DB calls).
- ⏭️ dev.sh graceful shutdown flag — **N/A** (not in scope: no dev.sh change).

### Release Gate
- ⏭️ **Not run** — change is small/focused (2 files, single subsystem), not cross-module
  architecture refactor or release. E2E gate not warranted.

### Contradiction Notices
- None. ensure.md methods aligned with pack-based execution.

## Pack Results

### authz_auto_derive_unit_test — ✅ PASS (72/72, 0 failures)
- Files: `tests/test_spawn_team_members.py` (36, +1 new), `tests/unit/test_ari_agent.py` (27),
  `tests/unit/test_ari_worker_integration.py` (13)
- Runtime: ~2s (well under 2-min unit limit)
- Pack script **created**: `test/packs/authz_auto_derive_unit_test.sh` (dual-layer timeout)
- **Coverage gap found + filled**: scenario "non-matching category grants zero implied members"
  (`tools.allow=["bash"]` + empty `team_members` → spawn explorer FAILS with `Allowed team members: []`)
  was NOT covered. Added `test_non_agent_backed_category_implies_nothing`.
- All 3 core claim scenarios now covered:
  1. ✅ `["knowledge"]` + empty team_members → explorer allowed (pre-existing coverage)
  2. ✅ `["bash"]` + empty team_members → explorer denied (NEW test)
  3. ✅ empty tools.allow + empty team_members → deny all (pre-existing coverage)
- **ari contract intact**: empty team_members + no instance tool still holds (40 ari tests green).

### job_queue_unit_test — ✅ PASS (1442 passed, 38 skipped, 0 failed)
- Files: `tests/job_queue/` (~70 test files)
- Runtime: ~32s
- **2 pre-existing failures fixed** (NOT authz regressions — verified via worktree test against
  parent commit `393cfef5`):
  - `test_defer_blocked_by_non_defer_work_on_fifo_queue` — stale assertion; contract was narrowed
    by `45c068f9` (admission_state `IN ('queued','active')` → `= 'active'`).
  - `test_queued_admission_state_blocks_defer_predicate` → renamed
    `..._does_not_block_defer_predicate`, assertion flipped to match `45c068f9`.
- **No authz regression**: the authz commit `3b94ba85` touched only `_auth.py` + `instance.py`
  + `test_spawn_team_members.py`; the entire 1442-test job queue suite passes against it.

## Quick Fixes Applied

| Commit | File | Change | Root Cause |
|--------|------|--------|------------|
| `b81e455d` | `tests/test_spawn_team_members.py` | +29 lines: `test_non_agent_backed_category_implies_nothing` | Coverage gap — non-agent-backed category branch untested |
| `b81e455d` | `test/packs/authz_auto_derive_unit_test.sh` | New pack script | Pack did not exist for authz area |
| `05a00fb3` | `tests/job_queue/test_seam_invariants.py` | 2 tests aligned with `45c068f9` contract | Stale assertions predating authz work (contract drift) |

## Failures
None (all resolved; both packs green).

## Action Needed
None. Feature verified; auto-derive logic works as described. No regressions.

## Documentation Updated
- [x] RESULTS/2026-07-25-tool-authz-auto-derive.md — this file
- [x] PACKS.md — added `authz_auto_derive_unit_test` pack entry
- [x] LESSONS/2026-07-25-job-queue-defer-contract-drift.md — documented pre-existing contract drift
- [ ] rules/ensure.md — no changes (user-maintained, read-only)
- [ ] MOCK_TESTS.md — no changes
- [ ] QUARANTINE.md — no changes (0 quarantined)

---

## Overall Status
- Authz Unit Tests: ✅ PASS
- Job Queue Regression: ✅ PASS
- ensure.md (scoped): ✅ PASS (1/1 in-scope Critical; 3 N/A)
- **Testing Complete**: ✅ READY
