# Quick Fix: Pre-existing test_gaia_agent.py Infrastructure Issue

**Date**: 2026-05-19
**Commit**: `36e42b1`
**Session**: mcp-regression
**Context**: MCP Config-Based Fix verification

## Issue
2 tests in `tests/unit/test_gaia_agent.py` failed:
- `TestGaiaToolFiltering::test_gaia_has_filesystem_tools`
- `TestGaiaToolFiltering::test_gaia_has_help_tool`

## Root Cause
Tests called `resolve_tool_filter()` without the `tool_categories` parameter. This caused the function to return category names literally (e.g., `'bash'`, `'filesystem'`, `'help'`) instead of expanding them to individual tool names. At test time, `list_tools_by_category()` returns empty.

## Fix
Added `TOOL_CATEGORIES` dictionary and passed `tool_categories=TOOL_CATEGORIES` to all 6 affected test methods.

## Classification
- **NOT related to MCP changes** — pre-existing test infrastructure issue
- **Quick fix eligible**: ~6 lines changed, single file, no architecture change

## Verification
Re-ran tests after fix: all passed.
