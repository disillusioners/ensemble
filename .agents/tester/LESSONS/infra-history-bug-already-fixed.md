# Lesson: infra_history_get "AttributeError" Was Already Fixed / Misdiagnosed

**Date**: 2026-06-16
**Status**: Investigation Complete — No Fix Needed

## Context
KB note claimed `infra_history_get` "consistently fails with AttributeError across all tested assets." Investigation was requested to reproduce and find root cause.

## What Happened
- Spawned opencode session to read source files and write reproduction script
- Reproduction script on SQLite: **ALL scenarios pass** (live asset, create+update, deleted asset, no-project-filter)
- 26/26 existing history tests pass
- **Bug does NOT reproduce in current codebase** (commit `9af792e8`)

## Actual Bug That Existed (Already Fixed)
- **Type**: `TypeError` (not `AttributeError`)
- **Cause**: `type` builtin shadowing in exception handlers of `infra_asset_create`, `infra_asset_list`, `infra_asset_search`
- **Fix**: commit `9af792e8` changed `type(exc).__name__` → `exc.__class__.__name__`
- **NOT affected**: `infra_history_get` (its params don't include `type`)

## Key Takeaway
The KB can carry stale bug reports. Always verify with reproduction before trusting a KB-claimed bug. The RAG system stored a note about the bug being "confirmed" but it was from an earlier broken state that was already fixed.

## What to Check When KB Says "Bug Confirmed"
1. Run `git log --oneline -20` — was the bug recently fixed?
2. Write a reproduction script — does it still fail?
3. Run existing tests — do they pass?
4. Check for stale `.pyc` cache in the environment
5. Consider PostgreSQL-only edge cases (if all tests are SQLite-only)
