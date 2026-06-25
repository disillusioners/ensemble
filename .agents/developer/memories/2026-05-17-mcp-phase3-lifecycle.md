# Phase 3 MCP Lifecycle Integration — Implementation Notes

## What was delivered
- `daemon/tools/instance.py`: `_load_mcp_tools()` helper + MCP injection in `create_instance_tools()` BEFORE scan/filter
- `daemon/routers/instances.py`: UUID generated upfront, MCP preload before spawn
- `daemon/utils.py`: MCP preload before spawn in `invoke_agent_and_wait`
- `daemon/services/job_processor.py`: MCP preload at all 3 spawn sites
- `daemon/services/job_feedback_observer.py`: MCP preload before spawn
- `daemon/services/instance_messaging.py`: Conditional preload at 2 restore sites only (NOT existence checks)
- `daemon/services/instance_lifecycle.py`: MCP cleanup in `terminate_instance()` after live_hub cleanup
- `daemon/manager.py`: Shutdown step verified in correct order

## Key findings during implementation
1. **Critical ordering**: MCP tools MUST be injected before `scan_tools_for_full_docs()` and `_apply_tool_filter()`
2. **Async-async bridge**: Preload is async, spawn is sync. All callers are async, so preload runs naturally before sync spawn
3. **Conditional restore preload**: Only 2 sites in `instance_messaging.py` actually trigger restore — checked `instance_id not in self._manager.instances`
4. **Review caught indentation bug**: `job_processor.py` had broken if/try nesting at one spawn site — fixed before commit
5. **Guard pattern**: All MCP access uses `hasattr(manager, '_mcp_service') and manager._mcp_service` for backward compatibility

## Commit
- Hash: `b2f589b`
- Branch: `feature/mcp-runtime-integration`
- 8 files changed, 103 insertions, 6 deletions
