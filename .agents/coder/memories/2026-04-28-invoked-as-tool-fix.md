# Fix: invoked_as_tool flag for tool-invoked child instances

## Problem
When explore()/experience() tools spawn Explorer/Experiencer agents, child_reports.py ran the full completion flow:
- Sent COMPLETION_REPORT to parent (double-reporting)
- Decremented waiting_for (corrupting parent state, since waiting_for was never incremented for tool spawns)
- CompletionRegistry also signaled (correct for explore, unnecessary for experience)

## Root Cause
- `waiting_for` is only incremented in `send_message()` tool (normal parent→child message)
- `spawn_instance()` does NOT increment it
- But `_update_parent_on_child_complete()` always decrements it
- No way to distinguish tool-invoked children from normal children

## Fix
- Added `invoked_as_tool: bool = False` param to `spawn_instance()` → stored in instance_metadata JSON
- `invoke_agent_and_wait()` (used by explore()) passes `invoked_as_tool=True`
- `experience()` in knowledge_tools.py passes `invoked_as_tool=True`
- `child_reports.py` checks flag in `_process_child_completion_and_notify_parent()`:
  - Skips: completion report, waiting_for decrement, children list update
  - Still does: status update to COMPLETED, CompletionRegistry signal, instance_hierarchy cleanup

## Key Files
- `daemon/services/instance_lifecycle.py` — spawn_instance()
- `daemon/utils.py` — invoke_agent_and_wait()
- `daemon/tools/knowledge_tools.py` — experience()
- `daemon/services/child_reports.py` — completion flow
- `tests/unit/services/test_invoked_as_tool.py` — 14 tests

## Commit
5686a0a on feature/rag-knowledge-toolset
