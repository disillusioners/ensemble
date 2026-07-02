# Quick Fix: team_members validation edge-case tests

**Date**: 2026-07-02
**Session**: team-members-validation-test
**Commit**: 2fd68764
**File**: tests/test_spawn_team_members.py (+35 lines)

## What was fixed
Added 2 edge-case documentation tests to pin the "fails-closed" behavior of `registry.resolve_pure_id()` for inputs that don't exactly match a registered agent_id:

1. **`test_case_sensitive_agent_id_fails_closed`** — `resolve_pure_id("Developer")` returns None (case-sensitive dict lookup), which triggers deny-by-default. The gate correctly rejects with "not allowed to spawn".

2. **`test_whitespace_in_agent_id_fails_closed`** — `resolve_pure_id("developer ")` returns None (no whitespace stripping), which triggers deny-by-default.

## Why
These edge cases were not explicitly tested. While they fail **closed** (deny-by-default protects against security issues), the behavior was undocumented. If someone later adds case-insensitive matching or whitespace stripping to `resolve_pure_id`, these tests make that an explicit, reviewed decision.

## Root cause
Not a bug — missing test coverage for documented behavior. The `resolve_pure_id` function in `daemon/registry.py` uses case-sensitive dict lookups and does not strip whitespace.

## Verification
All 27 tests pass (25 original + 2 new). 0 regressions across 118 tests in related spawn/instance suites.
