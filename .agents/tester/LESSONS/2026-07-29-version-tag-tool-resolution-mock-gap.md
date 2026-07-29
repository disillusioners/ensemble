# Quick Fix: version-tag tool resolution mock gap in test_tool_filter.py

**Date:** 2026-07-29
**Branch:** bugfix/version-tag-tool-resolution
**Commit (fix):** cf30fcd7
**Pack:** image_regression_test

## Problem

After the version-tag tool resolution fix (commit 09d146c9), `_apply_tool_filter()`
now calls `registry.get_version(agent_id, version_tag)` **first**, falling back to
`registry.get_resolved()` only if `get_version()` returns `None`.

The 12 test mock setups in `tests/test_tool_filter.py` only mocked `get_resolved()`.
Since `get_version()` returned an auto-created MagicMock (which is truthy), the
fallback to the properly-mocked `get_resolved()` never triggered. This caused the
configured mock tool filters (deny lists, allow lists) to be silently ignored.

6 tests failed:
- `test_deny_filter_removes_tools` (write_file not removed by deny)
- `test_tool_without_name_gets_warning` (logger.warning never called)
- `test_debug_logging_when_tools_filtered` (filter returned 3 instead of 1)
- `test_apply_tool_filter_with_mcp_deny` (mcp_filesystem_read not removed)
- `test_apply_tool_filter_with_mcp_allow` (bash not filtered by allow)
- `test_explicit_deny_still_wins_over_innate_skill_grant` (external_opencode_init_session not denied)

## Root Cause

Mock setup did not account for the new `get_version()` resolution path added by the
version-tag fix. When a function gains a new lookup method in its resolution chain,
ALL resolution methods must be mocked in tests.

## Fix

Added `mock_registry.return_value.get_version.return_value = None` to all 12 test
mock setups in `tests/test_tool_filter.py`, ensuring the code falls through to the
properly-mocked `get_resolved()` path.

**Changes:** 1 file, 12 insertions (test code only — no production/source changes).

## Pattern / Lesson

**When a function gains a new lookup path, tests must mock ALL resolution methods.**
The `get_version() or get_resolved()` fallback pattern means that under a MagicMock,
`get_version()` returns a truthy Mock object — the `or` short-circuits and
`get_resolved()` is never called. Tests that only mock `get_resolved()` will silently
have their filters bypassed.

**Detection:** The branch already patched 5 existing test files (`test_help_tool.py`,
`test_loader.py`, `test_coder_agent.py`, `test_devops_agent.py`, `test_wanderer_agent.py`)
with 1-line `get_version` stubs — but `test_tool_filter.py` was missed because it was
not in the branch's modified test list.
