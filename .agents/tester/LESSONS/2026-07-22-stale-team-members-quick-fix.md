# Quick Fix: Stale team_members expectations for kb-writer and worker additions

**Date:** 2026-07-22
**Commit:** `ddbc5d3cf99de881df6396760b3e2e2f298120fe`
**File:** `tests/test_spawn_team_members.py`

## Root Cause
Two test assertions in `test_spawn_team_members.py` hardcoded old configuration values that had since been legitimately updated:
1. `test_leader_team_members_parsed` expected 10 leader team_members but `kb-writer` was added (11 total)
2. `test_restricted_team_members_rejects_non_team_spawns` expected tester's team_members as `['explorer']` but `worker` was added (`['explorer', 'worker']`)

## Fix Applied
- Added `"kb-writer"` to expected leader team_members (line ~414)
- Updated expected tester team_members from `['explorer']` → `['explorer', 'worker']` (lines 255, 270)
- Only the tester assertion needed updating — `developer` and `planner` team_members (still `['explorer']` only) were correctly left unchanged

## Key Learning
When updating stale team_members assertions, be careful about identical assertion strings appearing across multiple agent tests. Only 1 of 3 similar assertions needed changing — a global replace would have broken 2 passing tests. Always read each assertion's context to identify which agent it targets.

## Verification
- Before fix: 25 passed, 2 failed
- After fix: 27/27 passed
