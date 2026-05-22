# MCP Cold-Load Race Condition Fix — Testing Insights

## Date: 2026-05-22
## Branch: feature/fix-mcp-cold-load

## The Bug
MCP tools not available when instance cold-loaded from disk after service restart. Root cause: `get_instance()` was sync while `ensure_mcp_preloaded()` was async — LLM invoked before MCP tools finished loading.

## Fix Pattern
Make `get_instance()` async and await MCP preload BEFORE calling `_restore_instance()` in the cold-load path. In-memory fast path stays sync.

## Testing Approach
1. **Full unit test suite** — 4433 tests to catch regressions from async change
2. **Targeted race condition tests** — 6 focused tests validating ordering and behavior
3. **E2E MCP tests** — Real daemon tests confirming MCP tools available and survive restart
4. **ensure.md** — dev.sh stability check

## Quick Fixes Applied
1. Import path fix: `daemon/persistence.py` had `from langgraph.checkpoint.base import CheckpointTuple` → needed conftest mock for `langgraph.checkpoint.memory`
2. conftest.py needed mock for `langgraph.checkpoint.memory` module

## Key Takeaway
When converting sync→async in core paths like `get_instance()`, all callers need updating. Running the full test suite catches missing `await` calls.
