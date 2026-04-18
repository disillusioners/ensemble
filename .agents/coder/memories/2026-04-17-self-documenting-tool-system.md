# Self-Documenting Tool System Refactor

## What Changed
Replaced the fragile tools_common.md section-parsing system with a self-documenting tool architecture where each tool module carries its own documentation.

## Architecture
- CATEGORY_NAME + CATEGORY_DOC: Constants in each tool module (daemon/tools/*.py)
- Tool Registry API: get_tool_categories(), get_category_doc() in _tool_registry.py
- Dynamic prompt loading: load_tools_doc_for_agent(agent_id) in loader.py
- Filtered tool_help: Per-agent filtering based on allowed tools
- No more static markdown: tools_common.md deleted, TOOL_CATEGORIES dict removed

## Key Files
- daemon/tools/_tool_registry.py — CATEGORY_MODULES mapping, dynamic category discovery
- daemon/loader.py — load_tools_doc_for_agent() replaces load_common_tools_filtered()
- daemon/tools/help.py — create_help_tool(all_tools, agent_id) with per-agent filtering
- daemon/tools/instance.py — resolve_tool_filter() with optional tool_categories param

## Lessons
- create_help_tool() uses closure pattern for agent_id injection (not context variable)
- Tests for resolve_tool_filter() need tool_categories=EXPECTED_TOOL_CATEGORIES param now
- tools_note.md is the new name for agent-specific tool notes (was tools.md), with backward compat fallback
- 32 test failures in full suite run are pre-existing test isolation issues (resource contention), not regressions
