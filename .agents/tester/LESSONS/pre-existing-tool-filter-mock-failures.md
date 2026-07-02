# Pre-existing test_tool_filter.py Failures

**Discovered**: 2026-07-02
**Affected tests**: 6 in `tests/test_tool_filter.py`
**Branch tested**: `feature/charter-generate-chart-tool` (commit 9197e726)
**Status**: PRE-EXISTING — confirmed failing on parent commit `a4ac7f0e`

## Failing Tests

1. `TestApplyToolFilter::test_deny_filter_removes_tools` (:362)
2. `TestApplyToolFilter::test_tool_without_name_gets_warning` (:390)
3. `TestApplyToolFilter::test_debug_logging_when_tools_filtered` (:471)
4. `TestMcpToolFiltering::test_apply_tool_filter_with_mcp_deny` (:637)
5. `TestMcpToolFiltering::test_apply_tool_filter_with_mcp_allow` (:670)
6. `TestExpandAllowForInnateSkills::test_explicit_deny_still_wins_over_innate_skill_grant` (:814)

## Root Cause

Tests mock `agent_meta` using `MagicMock()` without explicitly setting `innate_skills`. 
The auto-generated `MagicMock.innate_skills` attribute is **truthy** (it's a MagicMock object).

This causes `expand_allow_for_innate_skills()` (`daemon/tools/instance.py:58`) to behave 
unexpectedly when processing tool filtering — the truthy mock attribute pollutes the allow/deny 
set computation.

## Fix Candidate

Set `mock_agent_meta.innate_skills = None` or `[]` explicitly in the test mock setup.

OR: Make `_apply_tool_filter` defensively coerce `agent_meta.innate_skills` if it's a Mock.

## Verification

Ran `test_tool_filter.py` against parent commit `a4ac7f0e` in a separate worktree — 
identical 6 failures (46 passed there vs 47 now, +1 being the new charter-added test which passes).
