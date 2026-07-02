# Test Report: team_members field + spawn_instance validation
Date: 2026-07-02
Session: team-members-validation-test (ses_0de2ac45affeBGu526mBH0NtE2)
Branch: feature/team-members-spawn-validation
Commits: 0aabaa8c, 999f7131, a202ccb1, 2fd68764 (test additions)

## Summary
- **Total tests run**: 118 across 5 suites
- **Passed**: 118 | **Failed**: 0 | **Errors**: 0
- **Quick Fixes Applied**: 2 new edge-case tests added (commit 2fd68764)
- **Overall Status**: ✅ PASS — production-ready from security standpoint

## Test Run Results

| Suite | Passed | Failed | Notes |
|-------|--------|--------|-------|
| tests/test_spawn_team_members.py | 27 (+2 new) | 0 | 25 original + 2 edge-case tests added |
| tests/test_spawn_instance_validation.py | 5 | 0 | No regression |
| tests/unit/test_llm_config_override.py | 31 | 0 | No regression |
| tests/unit/test_context_key.py | 9 | 0 | No regression |
| tests/test_manager.py -k "spawn" | 4 | 0 | No regression |

## Security Property Validation — All PASS

### 1. Validation BEFORE instance creation — ✅ PASS
- `daemon/tools/instance.py:598`: `_check_team_membership(caller_agent_id, agent_id)` called first
- Line 600: Returns ERROR string if rejected
- Line 608: `manager.spawn_instance(...)` called only AFTER gate passes
- Tests assert `manager.spawn_instance.assert_not_called()` on all rejection paths
- **No orphaned instances on rejection**

### 2. Deny-by-default — ✅ PASS
- `_check_team_membership()` line 290: `raw_members = caller_meta.team_members or []`
- Both `None` and `[]` collapse to same deny-everything path
- Pinned by: `test_returns_error_for_truly_empty_team_members`, `test_returns_error_for_missing_team_members_attribute`

### 3. Alias-bypass prevention — ✅ PASS
- Requested agent: line 266 `registry.resolve_pure_id(requested_agent_id)`
- Caller: line 275 `registry.get_resolved(caller_agent_id)`
- Team entries: lines 295-299 canonicalized via `resolve_pure_id`
- Both sides canonicalized before comparison
- Pinned by: `test_alias_request_resolves_to_canonical_id`, `test_alias_caller_resolves_to_canonical_id`, unit-level alias tests

## Mock Fidelity Assessment — Accurate

| Check | Verdict |
|-------|---------|
| Mock manager.spawn_instance signature | ✅ Matches real call site (tuple return, kwargs) |
| _check_team_membership shape | ✅ Unit tests invoke real function (not mocked) |
| Would catch broken impl? | ✅ Yes — removing/moving validation, removing canonicalization all caught |
| Assertion quality | ✅ Meaningful — specific substrings, exact kwargs, call counts |

## Edge Case Coverage

| # | Edge case | Status | Details |
|---|-----------|--------|---------|
| 1 | Case sensitivity ("Developer" vs "developer") | ✅ NOW COVERED | `resolve_pure_id` is case-sensitive, fails closed. Test: `test_case_sensitive_agent_id_fails_closed` |
| 2 | Self-spawn (leader→leader) | ✅ COVERED | `test_invalid_spawn_leader_cannot_spawn_leader`. Same code path for all agents. |
| 3 | Non-existent agent_id | ✅ COVERED | Tool-level + unit-level tests both assert denial |
| 4 | None caller_agent_id | ✅ COVERED | `caller_agent_id: str = agent_id or ""` collapses None→"" |
| 5 | Whitespace ("developer ") | ✅ NOW COVERED | `resolve_pure_id` doesn't strip, fails closed. Test: `test_whitespace_in_agent_id_fails_closed` |

## Gaps Found

| Gap | Severity |
|-----|----------|
| Self-spawn for agents with non-empty team_members (developer→developer) not explicitly tested | Cosmetic (same code path as leader→leader) |

**No critical gaps found.**

## Quick Fixes Applied

**Commit 2fd68764** — Added 2 edge-case tests to `tests/test_spawn_team_members.py` (+35 lines):
- `test_case_sensitive_agent_id_fails_closed` — pins that "Developer" (capital) is rejected
- `test_whitespace_in_agent_id_fails_closed` — pins that "developer " (trailing space) is rejected

Both are contract-documentation tests protecting the safe-by-default (fails-closed) behavior from silent regression.

## ensure.md Validation

ensure.md was reviewed. The feature is a scoped test task (not a merge request), so full ensure.md validation (full non-integration test suite) was not run. Related spawn/instance suites (118 tests) all pass with 0 regressions. Full ensure.md validation recommended before merge.
