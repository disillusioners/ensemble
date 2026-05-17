# MCP Runtime Integration — Lessons Learned

**Date**: 2026-05-17
**Branch**: `feature/mcp-runtime-integration`

## spawn_instance → spawn_instance_with_mcp Migration

When a new method wraps an existing one (like `spawn_instance_with_mcp` wrapping `spawn_instance`), ALL tests that mock the old method must be updated. The pattern is:

1. Find all tests mocking `manager.spawn_instance` → add `manager.spawn_instance_with_mcp = AsyncMock(return_value=...)`
2. The wrapper is async, so the mock MUST be `AsyncMock`, not `MagicMock`

**Quick check**: `grep -rn "spawn_instance" tests/ --include="*.py" | grep -v "spawn_instance_with_mcp"` to find stale mocks.

## TYPE_CHECKING vs Runtime Imports

When using `from __future__ import annotations` with `TYPE_CHECKING`, imports inside the `TYPE_CHECKING` block are NOT available at runtime. If code actually instantiates the class at runtime (e.g., `self._mcp_service = McpService(...)`), you need BOTH:
- `TYPE_CHECKING` import for type hints
- Runtime import in the method that uses it

## Mock Instance Metadata Patterns

When mocking SQLModel instances that have both dict fields and direct attributes:
- `instance_meta.instance_metadata = {"project_id": "X"}` — sets the dict
- `instance_meta.project_id = "X"` — sets the attribute
- Code may access EITHER. Always set BOTH on mocks.

## Test Suite Size

The agents-ensemble project has 3,689 tests. Running the full suite takes >5 minutes. For targeted testing:
- MCP tests: `pytest tests/unit/test_mcp_*.py tests/unit/test_mcp_runtime_integration.py tests/integration/test_mcp_lifecycle.py` (~1s)
- Core tests: `pytest tests/unit/ tests/test_api.py tests/test_manager.py` (~50s)
- Full suite: allocate 5+ minutes
