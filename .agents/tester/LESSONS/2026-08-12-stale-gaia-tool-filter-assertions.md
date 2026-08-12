# Quick Fix: Stale Gaia tool_filter test assertions

**Date**: 2026-08-12
**Commit**: `2b2e42a9`
**Branch**: `feature/ari-prompt-delegation-fix`
**Worker**: 376c6f72 (ari-keyword-regression)

## Problem
3 test assertions in `tests/unit/test_gaia_agent.py` (lines 192, 365, 514) expected Gaia's `tools.allow` list WITHOUT the `"proc"` tool category. The `proc` category was added to `agents/gaia/meta.json` in commit `2e5861fd` (on main), but the test assertions were never updated.

## Root Cause
Pre-existing test/production drift — NOT caused by the Ari branch. Discovered during the Ari keyword regression sweep (`pytest tests/unit/ -k "ari or agent_config or tool_filter or loader"`).

## Fix
Added `"proc"` to the expected `tools.allow` list in all 3 assertions.
- 3 insertions, 3 deletions — single file
- Eligible for quick fix: < 20 lines, single file, obvious root cause, no architecture change

## Verification
Re-ran keyword sweep → 161/161 PASS.
