# Per-Agent Tool Control Feature — Test Report
Date: 2026-04-19
Branch: feature/per-agent-tools
Commits: 5de34b0 (initial), 10fd317 (review fixes)

## Summary
- **New Tests**: 35/35 PASSED
- **Full Suite**: 2410 passed, 0 failed, 22 skipped
- **Integration Validation**: All imports, counts, smoke tests PASS
- **Edge Cases**: All 5 edge cases PASS
- **ensure.md (dev.sh)**: PASS — server runs cleanly for 30 seconds
- **Quick Fixes Applied**: None needed

## Test 1: New Test Suite (tests/test_tool_filter.py)

| Test Class | Count | Status |
|-----------|-------|--------|
| TestToolFilterModel | 7 | ✅ ALL PASS |
| TestResolveToolFilter | 11 | ✅ ALL PASS |
| TestCategoryExpansion | 9 | ✅ ALL PASS |
| TestApplyToolFilter | 8 | ✅ ALL PASS |
| **Total** | **35** | **✅ ALL PASS** |

Duration: 0.30s

## Test 2: Full Test Suite (excluding integration)

| Metric | Count |
|--------|-------|
| Passed | 2410 |
| Failed | 0 |
| Skipped | 22 (pre-existing) |
| Duration | 75.31s |

No regressions detected.

## Test 3: Integration Validation

### Imports
- `from daemon.tools.instance import TOOL_CATEGORIES, resolve_tool_filter, _apply_tool_filter` — ✅ PASS
- `from daemon.registry import ToolFilter` — ✅ PASS

### Tool Category Counts

| Category | Tool Count | Tools |
|----------|-----------|-------|
| bash | 1 | `bash` |
| filesystem | 6 | `list_directory`, `read_file`, `write_file`, `glob_files`, `grep_files`, `edit_file` |
| time | 1 | `time` |
| instance | 5 | `spawn_instance`, `send_message`, `terminate_instance`, `list_instances`, `get_instance_info` |
| self | 2 | `inner_soul`, `access_memory` |
| project | 21 | `project_*` (21 tools) |
| help | 1 | `tool_help` |
| mother | 5 | `agent_list`, `agent_create`, `agent_read`, `agent_modify`, `agent_delete` |
| **Total** | **42** | |

### Smoke Test
- `allow=["filesystem"], deny=["read_file"]` → 5 tools (all fs tools except read_file) — ✅ PASS

## Test 4: Edge Cases

| # | Edge Case | Result | Evidence |
|---|-----------|--------|----------|
| 1 | No `tools` key → ALL tools | ✅ PASS | Returns None (all allowed) |
| 2 | `"tools": {}` → ALL tools | ✅ PASS | Empty allow/deny returns None (backward compatible) |
| 3 | Deny wins over allow | ✅ PASS | Same tool in allow+deny → denied |
| 4 | Category expansion | ✅ PASS | `"filesystem"` expands to 6 individual tools |
| 5 | _mother agent | ✅ PASS | Mother tools included via allow=['instance','self','help','mother'] |

## Test 5: ensure.md — dev.sh Validation

| Check | Result |
|-------|--------|
| Server starts? | ✅ YES |
| Runs 30 seconds? | ✅ YES |
| Errors in output? | ✅ NONE |
| Graceful shutdown? | ✅ YES |

All services initialized successfully: WorkerPool (4 workers), SessionManager, RetryScheduler, JobProcessor, JobFeedbackObserver, message sources system, StaleTaskRecovery.

## Overall Status: ✅ READY FOR MERGE
